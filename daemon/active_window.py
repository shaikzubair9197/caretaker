import time

import requests
import pywinctl as pwc


last_window = None


while True:

    try:

        window = pwc.getActiveWindow()

        if window:

            current_window = window.title

            if current_window != last_window:

                print(
                    f"New Window Detected: {current_window}"
                )

                response = requests.post(
                    "http://127.0.0.1:8000/telemetry/",
                    json={
                        "window_title": current_window
                    }
                )

                print(
                    f"Saved: {response.status_code}"
                )

                last_window = current_window

    except Exception as e:

        print(
            f"Error: {e}"
        )

    time.sleep(5)