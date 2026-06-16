"""
Idle tracker daemon.

Listens for mouse/keyboard activity via pynput. After IDLE_THRESHOLD_SECONDS
of silence it fires a POST /agent/idle to the Caretaker API.
Activity resets the timer — a new idle event fires only after another full
period of inactivity.

Usage:
    python -m daemon.idle_tracker
    # or
    python daemon/idle_tracker.py
"""

import os
import time
import threading

import requests
from pynput import mouse, keyboard

from utils.logger import get_logger

logger = get_logger("daemon.idle_tracker")

API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
IDLE_THRESHOLD = int(os.getenv("IDLE_THRESHOLD_SECONDS", "300"))  # 5 min default
POLL_INTERVAL = 10  # check every 10 s (cheap, avoids spinning)
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}

_last_activity = time.monotonic()
_idle_fired = False
_lock = threading.Lock()


def _touch():
    global _last_activity, _idle_fired
    with _lock:
        _last_activity = time.monotonic()
        _idle_fired = False  # reset so next idle period fires again


def _on_move(x, y):
    _touch()


def _on_click(x, y, button, pressed):
    _touch()


def _on_scroll(x, y, dx, dy):
    _touch()


def _on_key(key):
    _touch()


def _fire_idle():
    try:
        resp = requests.post(f"{API_BASE}/agent/idle", headers=_HEADERS, timeout=5)
        logger.info(f"Idle event fired → {resp.status_code}")
    except requests.exceptions.ConnectionError:
        logger.warning("API unreachable — idle event dropped")
    except Exception as e:
        logger.error(f"Idle POST failed: {e}")


def main():
    logger.info(
        f"Idle tracker started — threshold={IDLE_THRESHOLD}s, "
        f"api={API_BASE}"
    )

    mouse_listener = mouse.Listener(
        on_move=_on_move,
        on_click=_on_click,
        on_scroll=_on_scroll,
    )
    keyboard_listener = keyboard.Listener(on_press=_on_key)

    mouse_listener.start()
    keyboard_listener.start()

    global _idle_fired

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            with _lock:
                idle_seconds = time.monotonic() - _last_activity
                should_fire = idle_seconds >= IDLE_THRESHOLD and not _idle_fired

            if should_fire:
                logger.info(f"User idle for {idle_seconds:.0f}s — firing idle event")
                with _lock:
                    # Set flag inside lock BEFORE the HTTP call so a racing poll
                    # loop iteration can't also see should_fire=True and double-fire.
                    _idle_fired = True
                _fire_idle()

    except KeyboardInterrupt:
        logger.info("Idle tracker stopped")
    finally:
        mouse_listener.stop()
        keyboard_listener.stop()


if __name__ == "__main__":
    main()
