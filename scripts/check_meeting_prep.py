"""
One-shot end-to-end check for Meeting Prep — all three phases in one run.

Runs IN-PROCESS (no server needed):
  1) Live calendar + email sync from Microsoft Graph (Phase 1/2 data)
  2) Builds the prep snapshot for the soonest upcoming meeting and prints:
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
"""

import argparse
import sys
from pathlib import Path

# Allow running from the caretaker/ root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import SessionLocal           # noqa: E402
from services.meeting_prep_service import MeetingPrepService  # noqa: E402
from services.vault_service import VaultService         # noqa: E402
from utils.config import settings                       # noqa: E402


def _sync(db):
    from api.graph_sync import _run_source
    from database.models import GraphSyncState
    if not VaultService.is_configured():
        print("  ! VAULT_MASTER_KEY not set — skipping sync (rendering existing DB data).")
        print("    Set it to ingest live: export VAULT_MASTER_KEY=$(python -c "
              "\"import secrets;print(secrets.token_hex(32))\")")
        return
    # Force a full calendar sync (delta can miss freshly-created events).
    st = db.query(GraphSyncState).filter_by(
        source_type="calendar", user_upn=settings.GRAPH_SERVICE_UPN
    ).first()
    if st and st.delta_token:
        st.delta_token = None
        db.commit()
    for src in ("calendar", "email"):
        r = _run_source(src, settings.GRAPH_SERVICE_UPN, db, dry_run=False)
        print(f"  {src:9}: fetched={r.fetched} ingested={r.ingested} "
              f"dup={r.skipped_dup} quarantined={r.quarantined} errors={r.errors}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", action="store_true",
                    help="Only surface meetings within the next 15 min (default: soonest).")
    ap.add_argument("--no-sync", action="store_true", help="Skip Graph sync.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if not args.no_sync:
            print(f"=== LIVE SYNC (upn={settings.GRAPH_SERVICE_UPN}) ===")
            _sync(db)

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
