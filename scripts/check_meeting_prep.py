"""
One-shot end-to-end check for Meeting Prep — all three phases in one run.

Runs IN-PROCESS (no server needed):
  1) Live calendar sync from Microsoft Graph (Phase 1 data)
  2) Optional email sync when requested (Phase 2 data)
  3) Builds the prep snapshot for the soonest upcoming meeting and prints:
       - Phase 1: title, time, organizer, join link, agenda, attendees
       - Phase 2: related emails (attendee overlap + recency, with reason tags)
       - Phase 3: documents (confidence tier + reasons)

Usage:
    cd caretaker
    conda activate caretaker
    # VAULT_MASTER_KEY must be set for ingest (in .env or the environment):
    #   export VAULT_MASTER_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
    python scripts/check_meeting_prep.py            # soonest upcoming meeting (force)
    python scripts/check_meeting_prep.py --window   # only meetings within 15 min
    python scripts/check_meeting_prep.py --no-sync   # skip Graph sync, use existing DB
    python scripts/check_meeting_prep.py --sync-email  # also refresh the inbox archive
"""

import argparse
import sys
from pathlib import Path

# Allow running from the caretaker/ root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import SessionLocal           # noqa: E402
from services.graph.connectors.email_connector import EmailConnector  # noqa: E402
from services.graph.normalizer import GraphNormalizer  # noqa: E402
from services.meeting_prep_service import MeetingPrepService  # noqa: E402
from services.vault_service import VaultService         # noqa: E402
from utils.config import settings                       # noqa: E402


def _sync_calendar(db):
    from api.graph_sync import _run_source
    from database.models import GraphSyncState
    if not VaultService.is_configured():
        print("  ! VAULT_MASTER_KEY not set — skipping sync (rendering existing DB data).")
        print("    Set it to ingest live: export VAULT_MASTER_KEY=$(python -c "
              "\"import secrets;print(secrets.token_hex(32))\")")
        return False
    # Force a full calendar sync (delta can miss freshly-created events).
    st = db.query(GraphSyncState).filter_by(
        source_type="calendar", user_upn=settings.GRAPH_SERVICE_UPN
    ).first()
    if st and st.delta_token:
        st.delta_token = None
        db.commit()
    r = _run_source("calendar", settings.GRAPH_SERVICE_UPN, db, dry_run=False)
    print(f"  {'calendar':9}: fetched={r.fetched} ingested={r.ingested} "
          f"dup={r.skipped_dup} quarantined={r.quarantined} errors={r.errors}")
    return True


def _sync_recent_email(db):
    from api.graph_sync import _ingest_one
    if not VaultService.is_configured():
        print("  ! VAULT_MASTER_KEY not set — skipping email sync.")
        return

    connector = EmailConnector(settings.GRAPH_SERVICE_UPN)
    raw_items = connector.fetch_recent(days_back=30, max_items=100)
    print("  email     : fetching recent inbox items (30d window, max 100)")

    fetched = ingested = skipped_dup = quarantined = errors = 0
    fetched = len(raw_items)
    for raw in raw_items:
        try:
            normalized = GraphNormalizer.normalize("outlook_email", raw)
        except Exception as exc:
            print(f"  ! email normalization failed: {exc}")
            errors += 1
            continue

        outcome, exc = _ingest_one(normalized, db, dry_run=False)
        if outcome == "ingested":
            ingested += 1
        elif outcome == "quarantined":
            quarantined += 1
            ingested += 1
        elif outcome == "skipped_dup":
            skipped_dup += 1
        elif outcome == "error":
            errors += 1

    print(f"  {'email':9}: fetched={fetched} ingested={ingested} "
          f"dup={skipped_dup} quarantined={quarantined} errors={errors}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", action="store_true",
                    help="Only surface meetings within the next 15 min (default: soonest).")
    ap.add_argument("--no-sync", action="store_true", help="Skip Graph sync.")
    ap.add_argument("--sync-email", action="store_true",
                    help="Also refresh a bounded recent inbox slice before building the snapshot.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if not args.no_sync:
            print(f"=== LIVE SYNC (upn={settings.GRAPH_SERVICE_UPN}) ===")
            synced = _sync_calendar(db)
            if synced and args.sync_email:
                _sync_recent_email(db)
            elif synced:
                print("  ! email sync skipped (use --sync-email to refresh recent inbox items).")

        print("\n=== MEETING PREP SNAPSHOT (Phases 1-3) ===")
        snap = MeetingPrepService.next_snapshot(
            db, within_minutes=15, force=not args.window
        )
        if not snap:
            print("  No meeting to prep "
                  "(none upcoming/non-cancelled, or none within 15 min in --window mode).")
            return

        print("  [P1] title      :", snap["title"])
        print("  [P1] when       :", snap["start_display"], "| in", snap["minutes_until"], "min")
        print("  [P1] organizer  :", snap["organizer"]["name"])
        print("  [P1] join_url    :", snap["join_url"] or "(not an online meeting)")
        print("  [P1] attendees  :", [a["name"] for a in snap["attendees"]])
        print("  [P1] agenda     :", (snap["agenda"] or "")[:120])

        emails = snap["related_emails"]
        print(f"\n  [P2] related emails: {len(emails)}")
        for e in emails:
            print(f"        - {e['subject'][:50]}  ({e['reason']})")

        docs = snap["documents"]
        print(f"\n  [P3] documents: {docs['confidence']}")
        for d in docs["items"]:
            print(f"        - {d['label']}  ({d['reason']})")

        masked = "<" in (snap["title"] or "") or "<API_KEY" in (snap["agenda"] or "")
        print(f"\n  MASK CHECK: tokens leaked into display? {masked}  (expected: False)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
