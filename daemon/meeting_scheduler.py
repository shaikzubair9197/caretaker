"""
Meeting scheduler daemon.

Polls the Meeting Prep API and, when a meeting enters the 15-minute window,
launches the meeting prep UI as a subprocess (so it floats above the user's
work — same pattern as daemon/hotkey.py). Also kicks a periodic calendar sync
so the prep DB stays fresh.

Usage:
    python -m daemon.meeting_scheduler
    # or
    python daemon/meeting_scheduler.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from utils.logger import get_logger

# Load caretaker/.env so CARETAKER_API_KEY (and CARETAKER_API_URL) are available —
# the daemon runs as its own process and, unlike the API app, does not import
# utils.config; without this it sends an empty X-API-Key and every call gets 401.
load_dotenv()

logger = get_logger("daemon.meeting_scheduler")

API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}

POLL_SECONDS = int(os.getenv("MEETING_POLL_SECONDS", "60"))
SYNC_SECONDS = int(os.getenv("MEETING_SYNC_SECONDS", "5"))
WITHIN_MINUTES = int(os.getenv("MEETING_WITHIN_MINUTES", "15"))
TRANSCRIPT_WITHIN_MINUTES = int(os.getenv("MEETING_TRANSCRIPT_WITHIN_MINUTES", "30"))
# Continuous Teams/Email credential sync into the single SecureStore (Phase 2).
CREDENTIAL_SYNC_SECONDS = int(os.getenv("CREDENTIAL_SYNC_SECONDS", "900"))  # 15 min

_UI_SCRIPT = Path(__file__).parent.parent / "ui" / "meeting_prep_popup.py"
_FOLLOWUP_UI_SCRIPT = Path(__file__).parent.parent / "ui" / "followup_center_popup.py"

# external_ids we have already surfaced this process lifetime — fire each once.
_fired: set[str] = set()

# Separate from _fired (pre-meeting) — tracks completed meetings we've already
# triggered a transcript sync for, so we don't re-trigger every poll cycle.
_transcript_fired: set[str] = set()

# Last pending-draft count seen — the Follow-up Center is surfaced when this rises
# (new drafts ready). Dropping then rising re-triggers (reopenable).
_last_followup_count: int = 0


def _launch_ui(external_id: str) -> None:
    logger.info(f"Meeting entering window — launching prep UI for {external_id}")
    try:
        subprocess.Popen([sys.executable, str(_UI_SCRIPT), "--event", external_id])
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to launch meeting prep UI: {e}")


def _poll_upcoming() -> None:
    try:
        resp = requests.get(
            f"{API_BASE}/meeting/prep/upcoming",
            params={"within_minutes": WITHIN_MINUTES},
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        for item in resp.json():
            ext = item.get("external_id")
            if ext and ext not in _fired:
                _fired.add(ext)
                _launch_ui(ext)
    except requests.exceptions.ConnectionError:
        logger.warning("Caretaker API unreachable — will retry next poll")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Upcoming poll failed: {e}")


def _poll_recently_completed() -> None:
    """
    Check for online meetings that ended recently and have no transcript yet.
    If any are pending, trigger a transcript sync — the connector fetches by
    a time cursor (mirroring chat/email), so one sync call picks up whatever
    transcript(s) are now available, not just a single targeted meeting.
    """
    try:
        resp = requests.get(
            f"{API_BASE}/meeting/prep/recently-completed",
            params={"within_minutes": TRANSCRIPT_WITHIN_MINUTES},
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        pending = [item.get("external_id") for item in resp.json() if item.get("external_id")]
        new_pending = [ext for ext in pending if ext not in _transcript_fired]
        if not new_pending:
            return

        logger.info(f"{len(new_pending)} completed meeting(s) awaiting transcript — triggering sync")
        sync_resp = requests.post(
            f"{API_BASE}/graph/sync",
            params={"source": "transcript"},
            headers=_HEADERS,
            timeout=120,
        )
        sync_resp.raise_for_status()
        for ext in new_pending:
            _transcript_fired.add(ext)
        logger.info(f"Transcript sync result: {sync_resp.json()}")
    except requests.exceptions.ConnectionError:
        logger.warning("Caretaker API unreachable — will retry next poll")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Recently-completed poll failed: {e}")


def _launch_followup_center() -> None:
    logger.info("New follow-up drafts ready — launching Follow-up Center")
    try:
        subprocess.Popen([sys.executable, str(_FOLLOWUP_UI_SCRIPT)])
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to launch Follow-up Center: {e}")


def _poll_followups() -> None:
    """
    Proactively surface the Follow-up Center desktop popup when new pending
    drafts appear — exactly the way _poll_upcoming surfaces the Meeting Prep
    popup before meetings. Fires when the pending count RISES since last check;
    dropping (after approvals/dismissals) then rising again re-triggers, so it is
    reopenable. The user can also launch ui/followup_center_popup.py manually.
    """
    global _last_followup_count
    try:
        resp = requests.get(f"{API_BASE}/drafts/counts", headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        pending = int(resp.json().get("pending", 0))
        if pending > _last_followup_count and pending > 0:
            _launch_followup_center()
        _last_followup_count = pending
    except requests.exceptions.ConnectionError:
        logger.warning("Caretaker API unreachable — follow-up poll will retry")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Follow-up poll failed: {e}")


def _fire_reminder_notification(reminder: dict) -> None:
    """Show one desktop notification for a due reminder (Phase 6). Same fire-and-log
    discipline as the UI launchers: subprocess + best-effort. The reminder body is a
    local execution-layer Commitment value; it is shown to the user but never logged
    (logs carry the commitment id only)."""
    cid = reminder.get("commitment_id")
    body = reminder.get("action") or "You have a reminder due."
    try:
        subprocess.Popen(["notify-send", "Caretaker reminder", body])
        logger.info(f"Reminder fired for commitment {cid}")
    except FileNotFoundError:
        # notify-send not installed on this host — still mark fired so we don't loop.
        logger.info(f"Reminder due for commitment {cid} (notify-send unavailable)")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to fire reminder {cid}: {e}")


def _poll_reminders() -> None:
    """
    Fire due, un-fired reminders as desktop notifications (Phase 6, Refinement #4).
    Works EXCLUSIVELY from the commitments table via /reminders/due — it never reads
    KnowledgeItem. Fire-once: after notifying, it POSTs /reminders/{id}/fired to stamp
    reminded_at so a reminder never repeats. No approval popup.
    """
    try:
        resp = requests.get(f"{API_BASE}/reminders/due", headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        due = resp.json().get("due", []) or []
    except requests.exceptions.ConnectionError:
        logger.warning("Caretaker API unreachable — reminder poll will retry")
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Reminder poll failed: {e}")
        return

    for reminder in due:
        cid = reminder.get("commitment_id")
        if cid is None:
            continue
        _fire_reminder_notification(reminder)
        try:
            mark = requests.post(
                f"{API_BASE}/reminders/{cid}/fired", headers=_HEADERS, timeout=15
            )
            mark.raise_for_status()
        except Exception as e:  # noqa: BLE001
            # If we can't stamp it, leave it un-fired so it retries next cycle rather
            # than being silently dropped.
            logger.warning(f"Failed to mark reminder {cid} fired: {e}")


def _sync_calendar() -> None:
    try:
        resp = requests.post(
            f"{API_BASE}/meeting/prep/sync", headers=_HEADERS, timeout=120
        )
        resp.raise_for_status()
        logger.info(f"Calendar sync: {resp.json()}")
    except requests.exceptions.ConnectionError:
        logger.warning("Caretaker API unreachable for calendar sync")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Calendar sync failed: {e}")


def _poll_credential_sync() -> None:
    """
    Continuously discover confidential values from Teams + Email and upsert them
    into the single SecureStore (Phase 2). Ingestion-only: /graph/sync detects →
    masks → normalizes → upserts; the messages are transient and are never read
    again for credential resolution. Same fire-and-log pattern as _sync_calendar.
    """
    for source in ("chat", "email"):
        try:
            resp = requests.post(
                f"{API_BASE}/graph/sync", params={"source": source},
                headers=_HEADERS, timeout=120,
            )
            resp.raise_for_status()
            logger.info(f"Credential sync ({source}): {resp.json()}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"Caretaker API unreachable for credential sync ({source})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Credential sync ({source}) failed: {e}")


def main():
    logger.info(
        f"Meeting scheduler started — poll={POLL_SECONDS}s sync={SYNC_SECONDS}s "
        f"window={WITHIN_MINUTES}min"
    )
    last_sync = 0.0
    last_cred_sync = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_sync >= SYNC_SECONDS:
                _sync_calendar()
                last_sync = now
            if now - last_cred_sync >= CREDENTIAL_SYNC_SECONDS:
                _poll_credential_sync()
                last_cred_sync = now
            _poll_upcoming()
            _poll_recently_completed()
            _poll_followups()
            _poll_reminders()
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Meeting scheduler stopped")


if __name__ == "__main__":
    main()
