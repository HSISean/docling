import os
import threading
import time
import urllib.request
import webbrowser

from waitress import serve
from app import app


APP_URL = "http://127.0.0.1:5000/"


def open_browser_when_ready():
    deadline = time.time() + 120

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(APP_URL, timeout=2) as response:
                if 200 <= response.status < 400:
                    webbrowser.open(APP_URL)
                    return
        except Exception:
            time.sleep(0.25)


if __name__ == "__main__":
    if os.environ.get("DOCLING_SKIP_BROWSER") != "1":
        threading.Thread(
            target=open_browser_when_ready,
            daemon=True,
        ).start()

    serve(
        app,
        host="127.0.0.1",
        port=5000,
        threads=4,
    )