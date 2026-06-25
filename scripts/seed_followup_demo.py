"""
Live demo seeder + launcher for the desktop Follow-up Center.

Ingests the API_INTEGRATION mock transcript (threat scan → MASK + VAULT = ENCRYPT),
generates masked drafts, prints exactly what's in the DB, then LAUNCHES the
right-docked Follow-up Center popup (same idea as meeting prep — a desktop GUI).

Security: plaintext is NEVER printed. You see masked tokens only; the recipient
is decrypted in memory at approve time (UNMASK audit), never to stdout.

Usage:
    cd caretaker
    # the API server must be running (serves the popup's data + does decrypt):
    #   uvicorn app:app --reload
    python scripts/seed_followup_demo.py            # ingest + generate + POP THE GUI
    python scripts/seed_followup_demo.py --force     # re-extract + re-generate first
    python scripts/seed_followup_demo.py --no-popup   # just seed, don't open the GUI
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Allow running from the caretaker/ root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database.connection import SessionLocal
from database.models import AgentAction, AuditEvent, Commitment, KnowledgeItem, LLMCallLog, MeetingTranscript, Memory, SourceItem, Task, VaultToken
from services.graph.transcript_payload_parser import parse_transcript_payload
from services.graph.connectors import mock_transcript_fixtures as mock_fixtures
from services import transcript_ingestion_service
from services.draft_generation_service import DraftGenerationService

_DRAFT_TYPES = (
    "teams_message_draft", "email_draft", "reminder_draft",
    "calendar_reminder_draft", "followup_suggestion_draft", "clarification_needed",
)


def _fixture_candidates() -> dict[str, dict]:
    return {
        name: value
        for name, value in vars(mock_fixtures).items()
        if name.isupper()
        and isinstance(value, dict)
        and {"transcript_metadata", "meeting_metadata", "vtt_content"}.issubset(value.keys())
    }


def _preview(p: dict) -> str:
    for k in ("body", "subject", "title", "suggestion_text"):
        if p.get(k):
            return str(p[k])[:90]
    return p.get("display_title", "")


def _purge_scenario(db, external_id: str) -> None:
    """Remove only this synthetic transcript's rows so we can re-ingest from scratch."""
    mt = db.query(MeetingTranscript).filter(MeetingTranscript.external_id == external_id).first()
    if mt is None:
        print(f"    --fresh: no prior rows for {external_id} (clean ingest)")
        return

    src_id = mt.source_id
    ki_ids = {k.id for k in db.query(KnowledgeItem).filter(KnowledgeItem.source_id == src_id).all()}
    drafts = [
        a for a in db.query(AgentAction).all()
        if (a.payload or {}).get("knowledge_item_id") in ki_ids
    ]
    for a in drafts:
        db.delete(a)
    db.flush()

    by_src = lambda model: db.query(model).filter(model.source_id == src_id).delete(synchronize_session=False)
    ki_n = by_src(KnowledgeItem)
    log_n = by_src(LLMCallLog)
    aud_n = by_src(AuditEvent)
    by_src(Task)
    by_src(Memory)
    by_src(Commitment)
    db.query(SourceItem).filter(SourceItem.id == src_id).delete(synchronize_session=False)
    db.commit()

    print(
        f"    --fresh: purged scenario {external_id} (source_id={src_id}: "
        f"{len(drafts)} drafts, {ki_n} knowledge items, {log_n} llm logs, {aud_n} audit events, transcript)"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="bypass idempotency + re-extract knowledge")
    ap.add_argument("--reset", action="store_true",
                    help="dismiss this meeting's existing drafts and regenerate FRESH pending ones")
    ap.add_argument("--fresh", action="store_true",
                    help="purge this meeting's prior rows first, then ingest + extract clean")
    ap.add_argument("--fixture", default=None,
                    help="Named mock transcript fixture to ingest (default: API_INTEGRATION)")
    ap.add_argument("--list-fixtures", action="store_true",
                    help="Print available named mock fixtures and exit")
    ap.add_argument("--all", action="store_true",
                    help="Ingest every available mock transcript fixture")
    ap.add_argument("--no-popup", action="store_true", help="seed only; do not launch the GUI")
    args = ap.parse_args()

    fixtures = _fixture_candidates()
    if args.list_fixtures:
        print("Available mock fixtures:")
        for name in sorted(fixtures):
            print(f"  - {name}")
        return

    if args.fixture is not None and args.all:
        raise SystemExit("Cannot use --fixture and --all together")

    if args.all:
        selected = [(name, fixtures[name]) for name in sorted(fixtures)]
    else:
        fixture_name = (args.fixture or "API_INTEGRATION").upper()
        if fixture_name not in fixtures:
            print(f"Unknown fixture: {args.fixture}")
            print("Use --list-fixtures to see available options.")
            return
        selected = [(fixture_name, fixtures[fixture_name])]

    db = SessionLocal()
    try:
        meetings = []
        for fixture_name, fixture in selected:
            transcript = parse_transcript_payload(
                fixture["transcript_metadata"],
                fixture["vtt_content"],
                fixture["meeting_metadata"],
            )
            ext = transcript.metadata.external_id

            if args.fresh:
                print(f"\n[0] FRESH — purging prior rows for {ext}")
                _purge_scenario(db, ext)

            print(f"\n[1] INGEST + ENCRYPT  → '{transcript.metadata.subject}'  (fixture={fixture_name} force={args.force})")
            outcome = transcript_ingestion_service.ingest(transcript, db, force_reextract=args.force)
            print(f"    outcome={outcome.get('outcome')}  knowledge_items={outcome.get('knowledge_item_count')}")

            mt = db.query(MeetingTranscript).filter(MeetingTranscript.external_id == ext).first()
            if not mt:
                print("    ! transcript row not found — skipping draft generation")
                continue
            print(f"    meeting_transcript_id = {mt.id}")

            kis = (
                db.query(KnowledgeItem)
                .filter(KnowledgeItem.source_id == mt.source_id, KnowledgeItem.is_active.is_(True))
                .all()
            )
            from collections import Counter
            kt = Counter(k.knowledge_type for k in kis)
            print(f"    KnowledgeItems for this meeting: {len(kis)}  {dict(kt)}")
            if kis:
                print("    owners:", sorted({k.owner_token for k in kis if k.owner_token}))

            if args.reset:
                ki_ids = {k.id for k in kis}
                blockers = [
                    a for a in db.query(AgentAction).filter(AgentAction.status != "dismissed").all()
                    if (a.payload or {}).get("knowledge_item_id") in ki_ids
                ]
                for a in blockers:
                    a.status = "dismissed"
                db.commit()
                print(f"    reset: dismissed {len(blockers)} existing draft(s) → regenerating fresh pending drafts")

            print(f"\n[2] GENERATE DRAFTS (masked)")
            new_actions = DraftGenerationService.generate_for_meeting(mt.id, db)
            print(f"    newly generated this run: {len(new_actions)} (existing drafts are skipped — dedup)")

            ki_ids = {k.id for k in kis}
            all_drafts = [
                a for a in db.query(AgentAction).filter(AgentAction.action_type.in_(_DRAFT_TYPES)).all()
                if (a.payload or {}).get("knowledge_item_id") in ki_ids
            ]
            print(f"    drafts in DB for this meeting: {len(all_drafts)}")
            for a in all_drafts:
                p = a.payload or {}
                print(f"      #{a.id} {a.action_type} [{a.status}] → {p.get('recipient_token')}  {_preview(p)!r}")

            if not all_drafts:
                print("\n    No drafts exist. Most likely the LLM extraction produced no draftable")
                print("    owner-attributed items, OR every item fell below the confidence floor.")
                print("    Try:  python scripts/seed_followup_demo.py --force   (re-extract)")

            meetings.append(mt.id)

        if not args.no_popup and len(meetings) == 1:
            popup = ROOT / "ui" / "followup_center_popup.py"
            print(f"\n[3] LAUNCHING Follow-up Center GUI  ({popup.name})")
            print("    (needs the API server running:  uvicorn app:app --reload)")
            try:
                subprocess.Popen([sys.executable, str(popup)])
            except Exception as e:  # noqa: BLE001
                print(f"    ! could not launch popup: {e}")
        elif not args.no_popup and len(meetings) > 1:
            print("\n[3] --all mode ingested multiple meetings; launch the GUI manually if desired.")
        else:
            print("\n[3] --no-popup set. Launch manually:  python ui/followup_center_popup.py")
    finally:
        db.close()


if __name__ == "__main__":
    main()
