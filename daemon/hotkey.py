"""
Global hotkey daemon.

Listens for Ctrl+Shift+P (configurable via CARETAKER_HOTKEY env var).
On trigger: launches the panic dump UI as a subprocess so it floats above
whatever the user is working in.

Usage:
    python -m daemon.hotkey
    # or
    python daemon/hotkey.py
"""

import os
import subprocess
import sys
from pathlib import Path

from pynput import keyboard

from utils.logger import get_logger

logger = get_logger("daemon.hotkey")

# Hotkey binding — pynput GlobalHotKeys format
# e.g. "<ctrl>+<shift>+p"
HOTKEY_BINDING = os.getenv("CARETAKER_HOTKEY", "<ctrl>+<shift>+p")

# Resolve the UI script path relative to this file so it works regardless
# of the working directory the daemon is launched from.
_UI_SCRIPT = Path(__file__).parent.parent / "ui" / "panic_dump.py"

_ui_process = None


def _launch_ui():
    global _ui_process
    if _ui_process is not None and _ui_process.poll() is None:
        logger.info("Panic dump UI already open — ignoring hotkey")
        return

    logger.info("Hotkey triggered — launching panic dump UI")
    try:
        _ui_process = subprocess.Popen(
            [sys.executable, str(_UI_SCRIPT)],
            # inherit env so the UI picks up CARETAKER_API_URL if set
        )
    except Exception as e:
        logger.error(f"Failed to launch UI: {e}")


def main():
    logger.info(f"Hotkey daemon started — binding={HOTKEY_BINDING}")
    with keyboard.GlobalHotKeys({HOTKEY_BINDING: _launch_ui}) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            logger.info("Hotkey daemon stopped")


if __name__ == "__main__":
    main()
