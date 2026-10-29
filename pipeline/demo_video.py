"""Records a short screen capture of the viewer running on http://localhost:8000 (needs `python build.py serve` running).
usage: python -m pipeline.demo_video   ->  outputs/demo_viewer.webm"""
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .browser import launch
from .common import OUT

URL = "http://localhost:8000/index.html"


def drag(pg, x0, y0, x1, y1, steps=40, delay=18, button="left"):
    pg.mouse.move(x0, y0)
    pg.mouse.down(button=button)
    for i in range(1, steps + 1):
        pg.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        pg.wait_for_timeout(delay)
    pg.mouse.up(button=button)


def main():
    tmp = OUT / "_video"
    shutil.rmtree(tmp, ignore_errors=True)
    with sync_playwright() as p:
        b = launch(p)
        ctx = b.new_context(viewport={"width": 1280, "height": 720}, record_video_dir=str(tmp), record_video_size={"width": 1280, "height": 720})
        pg = ctx.new_page()
        pg.goto(URL)
        pg.wait_for_selector("#loading.done", timeout=120000)
        try:
            pg.wait_for_function("parseInt(document.getElementById('fps').textContent) > 20", timeout=60000)
        except Exception:
            print("[video] warning: viewer stayed under 20 fps, recording anyway")
        pg.wait_for_timeout(1500)
        # orbit around the building
        drag(pg, 420, 400, 760, 380, steps=70)
        pg.wait_for_timeout(600)
        drag(pg, 760, 380, 300, 330, steps=80)
        pg.wait_for_timeout(600)
        # zoom in and out
        pg.mouse.move(560, 380)
        for _ in range(6):
            pg.mouse.wheel(0, -280)
            pg.wait_for_timeout(120)
        pg.wait_for_timeout(900)
        for _ in range(6):
            pg.mouse.wheel(0, 280)
            pg.wait_for_timeout(120)
        pg.wait_for_timeout(600)
        # click floors: by number button, then directly on the building
        for k in ("7", "4", "2"):
            pg.click(f"#floor-list button[data-k='{k}']")
            pg.wait_for_timeout(1600)
        pg.click("#btn-isolate")
        pg.wait_for_timeout(1800)
        pg.click("#floor-list button[data-k='5']")
        pg.wait_for_timeout(1800)
        pg.click("#btn-isolate")
        pg.mouse.click(560, 330)                     # click straight on the facade
        pg.wait_for_timeout(1800)
        # evening look
        pg.click("#btn-time")
        pg.wait_for_timeout(2500)
        drag(pg, 700, 420, 520, 400, steps=60)
        pg.wait_for_timeout(1500)
        pg.click("#btn-time")
        pg.wait_for_timeout(1500)
        pg.click("#btn-reset")
        pg.wait_for_timeout(2500)
        vid = pg.video
        ctx.close()
        src = Path(vid.path())
        b.close()
    dest = OUT / "demo_viewer.webm"
    shutil.move(str(src), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[video] {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
