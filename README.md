# Vibgyor: drawing → 3D model → renders → web viewer

One command turns the architect's DXF into a 3D model of the tower, four photoreal stills and an
interactive web view. Nothing is modelled by hand: every wall, slab, window, balcony edge, tree and
road is read from the drawing; only appearance is decided in code.

```
SUBMISSION - 04.dxf ──extract──▶ build/plan.json ──build──▶ outputs/vibgyor.blend ──render──▶ outputs/renders/*.png
                                                                       └────export───▶ web/model/vibgyor.glb ──▶ web/ (three.js)
```

## Run it

Needs **Python 3.11** (Blender's `bpy` 4.5 wheel only exists for 3.11) and an internet connection the
first time (CC0 textures / HDRIs from Poly Haven are downloaded into `build/assets`). The `bpy` wheel exists for macOS, Linux and Windows; I only tested on Windows.

```bash
# 1. put the task pack in input/   (input/SUBMISSION - 04.dxf, input/brochure/...)  - it is git-ignored, it is confidential
# 2. environment
uv venv --python 3.11 .venv && uv pip install -r requirements.txt        # or: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
source .venv/bin/activate                                                 # Windows: .venv\Scripts\activate
# 3. everything
python build.py            # extract → preview → build → render → export → interior
                           # (CPU Cycles: ~1.5 h for the 4 stills, ~2 h more for the panorama; add --quick for minutes)
python build.py serve      # viewer on http://localhost:8000
```

| Command | What it does | Output |
|---|---|---|
| `python build.py extract` | reads the DXF, writes clean geometry as JSON | `build/plan.json`, `outputs/extraction_preview.png` |
| `python build.py build` | extrudes the geometry in Blender (headless) | `outputs/vibgyor.blend` |
| `python build.py render [--quick] [--shots 1,3]` | four Cycles stills, ≥ 2560 px | `outputs/renders/*.png` |
| `python build.py export` | glTF for the browser (≤ 40 MB) | `web/model/vibgyor.glb` (+ copy in `outputs/`) |
| `python build.py interior [--quick]` | bonus: furnishes the Type A flat from its room labels, renders a 4096×2048 360° panorama of the drawing room | `outputs/renders/05_typeA_drawing_room_360.png` |
| `python build.py serve` | static server for `web/` | http://localhost:8000 |
| `python build.py test` | pytest on the extraction step | |

`--quick` renders low-resolution previews (≈ 1 min each) into `build/preview/`. `make all`, `make quick`, `make serve` do the same.

## What it produces

* **3D model** (`outputs/vibgyor.blend`, `web/model/vibgyor.glb`): stilt level with columns and core, seven identical
  floors (walls, 32 window / sliding-door openings sized from the drawing's schedule, balcony planters + timber louvres on the
  open slab edges, 0.6 m slab cantilevers), terrace with parapet and stair/lift cabins, entrance canopy and lobby,
  compound wall with gate and name sign, 18 m road and 9 m side road, 25 trees at the positions drawn on the landscape plan.
* **Renders** (`outputs/renders/`): `01_eye_level_day`, `02_front_corner_dusk` (interior + facade lights on),
  `03_aerial_day`, `04_entrance_closeup_day`. Poly Haven HDRI sky aligned in code so the sun lights the facade, PBR textures at real-world
  scale, soft shadows, AgX tone mapping. Cameras, lights and sun are computed from the extracted geometry (`pipeline/render.py`).
* **Viewer** (`web/`): three.js, orbit/zoom/pan, HDRI image-based lighting + shadow-casting sun, AgX tone mapping, PBR materials, day/evening
  toggle. Click a floor (or its number button) to highlight it and see its flats (Type A front / Type B rear, carpet areas read from the plan text).
  Measured at 40-50 fps on an integrated Intel Iris Xe GPU (Windows, headless Edge via `pipeline/webshot.py`).

## Layout

```
build.py            one-command entry point
config.toml         the small, documented config (regions, layers, heights, materials, render settings)
pipeline/
  extract.py        DXF → plan.json (walls, openings, slab, site, levels, units)
  model.py          plan.json → Blender scene (bmesh extrusion, materials, trees, site)
  render.py         cameras / sky / lights → Cycles stills
  export.py         .blend → glb + viewer metadata
  assets.py         Poly Haven downloader + code-generated foliage texture
  framing.py        site-derived reference directions (front normal etc.)
  preview.py        debug plot of what the extractor understood
  interior.py       bonus: Type A interior + 360° panorama
  webshot.py        headless-browser check of the viewer (optional, tested on Windows only)
  demo_video.py     records a scripted tour of the viewer to outputs/demo_viewer.webm (optional)
  browser.py        Chromium launcher shared by the two helpers above
web/                three.js viewer (three is vendored, MIT)
tests/              pytest for extraction
NOTES.md            how it works, what breaks, hours, next steps
```

## Confidentiality

The drawings are confidential and for this assessment only. `input/`, `*.dxf` and `*.dwg` are git-ignored; keep the repo private,
and delete the drawings (and `build/`) once submitted.

Third-party assets: three.js (MIT, vendored in `web/vendor`), Poly Haven textures and HDRIs (CC0, downloaded at build time).
