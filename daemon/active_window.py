import os
import time
import requests
import pywinctl as pwc

from utils.logger import get_logger

logger = get_logger("daemon.active_window")

API_BASE = os.getenv("CARETAKER_API_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = int(os.getenv("CARETAKER_POLL_INTERVAL", "5"))
_API_KEY = os.getenv("CARETAKER_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}


def main():
    last_window = None

    logger.info("Active window daemon started")

    while True:
        try:
            window = pwc.getActiveWindow()

            if window:
                current_window = window.title

                if current_window != last_window:
                    logger.info(f"Window switch detected: {current_window!r}")

                    requests.post(
                        f"{API_BASE}/telemetry/",
                        json={"window_title": current_window},
                        headers=_HEADERS,
                        timeout=3,
                    )

                    agent_response = requests.get(
                        f"{API_BASE}/agent/tick",
                        headers=_HEADERS,
                        timeout=5,
                    )

                    logger.info(f"Agent tick: {agent_response.json()}")

                    last_window = current_window

        except requests.exceptions.ConnectionError:
            logger.warning("API unreachable — will retry")
        except Exception as e:
            logger.error(f"Unhandled error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
