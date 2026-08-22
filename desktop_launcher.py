"""
Desktop launcher
=================
The entry point for the packaged Windows app. Not used when running from
source -- `server.py` covers that. This exists because a person who just
double-clicked an .exe should not have to know a port number or type a URL:
it starts the server, finds a free port itself, and opens the browser to it.

    pyinstaller utrextractor.spec        build the packaged app
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

import uvicorn

import core
from server import app

HOST = "127.0.0.1"
PORT_RANGE = range(8000, 8010)


def _free_port() -> int:
    """First open port in PORT_RANGE, so a second launch (or a busy 8000) still works."""
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex((HOST, port)) != 0:      # nothing answering -> free
                return port
    return PORT_RANGE[0]


def _open_browser_when_ready(url: str) -> None:
    """Poll the port rather than sleep-and-hope, so the tab opens the instant it can."""
    for _ in range(100):                              # ~10s ceiling
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex((HOST, int(url.rsplit(":", 1)[1]))) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.1)


def main() -> int:
    port = _free_port()
    url = f"http://{HOST}:{port}"

    print("=" * 56)
    print("  UTR / Payment & Blood Test Extractor")
    print("=" * 56)
    engines = core.available_engines()
    print(f"  OCR engine   : {', '.join(engines) if engines else 'NONE FOUND'}")
    print(f"  Address      : {url}")
    print("  Opening your browser now...")
    print()
    print("  Everything runs on this machine. Nothing you upload is sent")
    print("  anywhere else, unless you explicitly turn on the optional AI")
    print("  reading feature and add your own API key.")
    print()
    print("  Closing this window stops the app. Keep it open while you work.")
    print("=" * 56)

    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    try:
        uvicorn.run(app, host=HOST, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
