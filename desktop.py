import os
import threading
import time
import webbrowser

from waitress import serve
from app import app


def open_browser():
    """Open the Flask UI when running interactively."""
    time.sleep(3)
    webbrowser.open("http://127.0.0.1:5000/")


if __name__ == "__main__":
    # Do not open a browser during automated GitHub Actions testing.
    if os.environ.get("DOCLING_SKIP_BROWSER") != "1":
        threading.Thread(
            target=open_browser,
            daemon=True,
        ).start()

    serve(
        app,
        host="127.0.0.1",
        port=5000,
        threads=4,
    )