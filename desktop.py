import threading
import time
import webbrowser

from waitress import serve
from app import app  # Change this import to your Flask app


def open_browser():
    time.sleep(2)
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()

    serve(
        app,
        host="127.0.0.1",
        port=5000,
        threads=4,
    )