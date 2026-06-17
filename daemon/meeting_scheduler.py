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

from utils.logger import get_logger

logger = get_logger("daemon.meeting_scheduler")

API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}

POLL_SECONDS = int(os.getenv("MEETING_POLL_SECONDS", "60"))
SYNC_SECONDS = int(os.getenv("MEETING_SYNC_SECONDS", "5"))
WITHIN_MINUTES = int(os.getenv("MEETING_WITHIN_MINUTES", "15"))

_UI_SCRIPT = Path(__file__).parent.parent / "ui" / "meeting_prep_popup.py"

# external_ids we have already surfaced this process lifetime — fire each once.
_fired: set[str] = set()


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


def main():
    logger.info(
        f"Meeting scheduler started — poll={POLL_SECONDS}s sync={SYNC_SECONDS}s "
        f"window={WITHIN_MINUTES}min"
    )
    last_sync = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_sync >= SYNC_SECONDS:
                _sync_calendar()
                last_sync = now
            _poll_upcoming()
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Meeting scheduler stopped")


if __name__ == "__main__":
    main()
