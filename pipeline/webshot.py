"""Headless-browser check of the viewer: loads it, reports console errors and fps, saves screenshots.
usage: python -m pipeline.webshot [out_dir]"""
import http.server
import socketserver
import sys
import threading
from functools import partial
from pathlib import Path

from playwright.sync_api import sync_playwright

from .browser import launch
from .common import OUT, ROOT


def serve(port=0):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    h = partial(Quiet, directory=str(ROOT / "web"))
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), h)
    srv.allow_reuse_address = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "viewer"
    out.mkdir(parents=True, exist_ok=True)
    srv, port = serve()
    logs = []
    with sync_playwright() as p:
        b = launch(p)
        pg = b.new_page(viewport={"width": 1280, "height": 720})
        pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
        pg.goto(f"http://127.0.0.1:{port}/index.html?shot=fast")
        pg.wait_for_selector("#loading.done", timeout=180000)
        pg.wait_for_timeout(8000)
        pg.screenshot(path=str(out / "viewer_day.png"), timeout=180000)
        pg.evaluate("window.__viewer.select('4')")
        pg.wait_for_timeout(8000)
        pg.screenshot(path=str(out / "viewer_floor4.png"), timeout=180000)
        pg.evaluate("window.__viewer.applySky('dusk')")
        pg.wait_for_timeout(8000)
        pg.screenshot(path=str(out / "viewer_dusk.png"), timeout=180000)
        print("fps label:", pg.inner_text("#fps"))
        b.close()
    srv.shutdown()
    print("\n".join(logs[:30]) or "no console output")


if __name__ == "__main__":
    main()
