#!/usr/bin/env python
"""One command for the whole pipeline:  drawing (DXF) -> 3D model -> renders -> web model.

    python build.py            # = all: extract, build, render, export
    python build.py extract    # DXF -> build/plan.json (+ outputs/extraction_preview.png)
    python build.py build      # plan.json -> outputs/vibgyor.blend
    python build.py render     # stills -> outputs/renders/*.png   (add --quick for low-res previews)
    python build.py export     # .blend -> web/model/vibgyor.glb
    python build.py interior   # bonus: Type A drawing-room 360 panorama -> outputs/renders/05_*.png
    python build.py serve      # open the interactive viewer on http://localhost:8000
    python build.py test       # pytest (extraction tests)
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGES = {
    "extract": ["-m", "pipeline.extract"],
    "preview": ["-m", "pipeline.preview"],
    "build": ["-m", "pipeline.model"],
    "render": ["-m", "pipeline.render"],
    "export": ["-m", "pipeline.export"],
    "interior": ["-m", "pipeline.interior"],      # bonus: furnished Type A flat + 360 panorama (slow: ~2 h on CPU)
}


def run(name, extra=()):
    t0 = time.time()
    print(f"\n=== {name} " + "=" * (60 - len(name)), flush=True)
    r = subprocess.run([sys.executable, *STAGES[name], *extra], cwd=ROOT)
    if r.returncode:
        sys.exit(f"stage '{name}' failed (exit {r.returncode})")
    print(f"--- {name} done in {time.time() - t0:.0f}s", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", nargs="?", default="all", choices=["all", *STAGES, "serve", "test"])
    ap.add_argument("--quick", action="store_true", help="render / interior: low-resolution, low-sample previews")
    ap.add_argument("--shots", help="render: comma-separated shot numbers, e.g. 1,3")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()

    if sys.version_info[:2] != (3, 11):
        print(f"warning: Python {sys.version_info[0]}.{sys.version_info[1]} - the bpy 4.5 wheel needs Python 3.11 (see README)")
    if a.stage == "test":
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT))
    if a.stage == "serve":
        import http.server
        import socketserver
        from functools import partial
        h = partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "web"))
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        socketserver.ThreadingTCPServer.daemon_threads = True
        with socketserver.ThreadingTCPServer(("", a.port), h) as srv:
            print(f"viewer: http://localhost:{a.port}   (Ctrl+C to stop)")
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                pass
        return
    if not (ROOT / "input" / "SUBMISSION - 04.dxf").exists():
        sys.exit("input/SUBMISSION - 04.dxf not found - copy the task pack into input/ (see README)")

    render_args = (["--quick"] if a.quick else []) + (["--shots", a.shots] if a.shots and a.stage != "interior" else [])
    todo = list(STAGES) if a.stage == "all" else [a.stage]
    for s in todo:
        run(s, render_args if s in ("render", "interior") else ())
    if a.stage == "all":
        print("\nDone. Renders: outputs/renders/  ·  model: outputs/vibgyor.blend, web/model/vibgyor.glb  ·  viewer: python build.py serve")


if __name__ == "__main__":
    main()
