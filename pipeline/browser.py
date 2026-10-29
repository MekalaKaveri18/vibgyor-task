"""Optional helper for webshot.py / demo_video.py: launch a Chromium with GPU rasterisation.
Defaults to Playwright's bundled chromium (`playwright install chromium`); set VIBGYOR_BROWSER=msedge|chrome to use an installed browser.
These two helpers are QA conveniences (not part of `build.py all`) and were only tested on Windows."""
import os
import sys


def launch(p):
    args = ["--ignore-gpu-blocklist", "--enable-gpu-rasterization"]
    if sys.platform == "win32":
        args.append("--use-angle=d3d11")
    channel = os.environ.get("VIBGYOR_BROWSER") or ("msedge" if sys.platform == "win32" else None)
    return p.chromium.launch(channel=channel, args=args) if channel else p.chromium.launch(args=args)
