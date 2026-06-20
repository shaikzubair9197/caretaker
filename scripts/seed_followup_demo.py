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
from database.models import AgentAction, KnowledgeItem, MeetingTranscript, VaultToken
from services.graph.transcript_payload_parser import parse_transcript_payload
from services.graph.connectors.mock_transcript_fixtures import API_INTEGRATION
from services import transcript_ingestion_service
from services.draft_generation_service import DraftGenerationService

_DRAFT_TYPES = (
    "teams_message_draft", "email_draft", "reminder_draft",
    "calendar_reminder_draft", "followup_suggestion_draft", "clarification_needed",
)


def _preview(p: dict) -> str:
    for k in ("body", "subject", "title", "suggestion_text"):
        if p.get(k):
            return str(p[k])[:90]
    return p.get("display_title", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="bypass idempotency + re-extract knowledge")
    ap.add_argument("--reset", action="store_true",
                    help="dismiss this meeting's existing drafts and regenerate FRESH pending ones")
    ap.add_argument("--no-popup", action="store_true", help="seed only; do not launch the GUI")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        transcript = parse_transcript_payload(
            API_INTEGRATION["transcript_metadata"],
            API_INTEGRATION["vtt_content"],
            API_INTEGRATION["meeting_metadata"],
        )
        ext = transcript.metadata.external_id

        print(f"\n[1] INGEST + ENCRYPT  → '{transcript.metadata.subject}'  (force={args.force})")
        outcome = transcript_ingestion_service.ingest(transcript, db, force_reextract=args.force)
        print(f"    outcome={outcome.get('outcome')}  knowledge_items={outcome.get('knowledge_item_count')}")

        mt = db.query(MeetingTranscript).filter(MeetingTranscript.external_id == ext).first()
        if not mt:
            print("    ! transcript row not found — aborting")
            return
        print(f"    meeting_transcript_id = {mt.id}")

        # What knowledge was extracted (this is what drafts are built from)?
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

        # Everything that now exists for this meeting (incl. prior runs)
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

        if not args.no_popup:
            popup = ROOT / "ui" / "followup_center_popup.py"
            print(f"\n[3] LAUNCHING Follow-up Center GUI  ({popup.name})")
            print("    (needs the API server running:  uvicorn app:app --reload)")
            try:
                subprocess.Popen([sys.executable, str(popup)])
            except Exception as e:  # noqa: BLE001
                print(f"    ! could not launch popup: {e}")
        else:
            print("\n[3] --no-popup set. Launch manually:  python ui/followup_center_popup.py")
    finally:
        db.close()


if __name__ == "__main__":
    main()
