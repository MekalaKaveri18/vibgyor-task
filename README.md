# Vibgyor: drawing to 3D model, renders and web viewer

A pipeline that reads the architect's DXF and produces, with no manual modelling:

- a 3D model of the tower and its plot (`.blend` and `.glb`),
- four photoreal 2560×1440 stills, plus a 360° panorama of the Type A drawing room (bonus),
- an interactive three.js viewer.

Geometry comes from the drawing. Code decides only appearance: materials, cameras, lights and sky.
See [NOTES.md](NOTES.md) for how the extraction works, what breaks, and the hours spent.

```
DXF ──extract──▶ build/plan.json ──build──▶ outputs/vibgyor.blend ──render──▶ outputs/renders/*.png
                                                    └──export──▶ web/model/vibgyor.glb ──▶ web/ (three.js)
```

## Quick start

Requires **Python 3.11** (the `bpy` 4.5 wheel only exists for 3.11) and internet on the first run,
to download CC0 textures and HDRIs from Poly Haven into `build/assets/`.
Tested on Windows only. The `bpy` wheel also exists for macOS and Linux, but I have not run it there.

```bash
# 1. Put the task pack in input/  (input/SUBMISSION - 04.dxf). It is git-ignored: the drawings are confidential.
# 2. Environment
uv venv --python 3.11 .venv && uv pip install -r requirements.txt
#    or: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Run
python build.py                    # every stage, in order
python build.py serve              # viewer at http://localhost:8000
```

A full run on CPU takes about 1.5 h for the four stills and about 2 h more for the panorama.
`--quick` renders low-resolution previews in a few minutes.

## Stages

Each stage can be run on its own.

| Command | Does | Writes |
|---|---|---|
| `python build.py extract` | Reads the DXF and writes clean geometry as JSON | `build/plan.json`, `outputs/extraction_preview.png` |
| `python build.py build` | Extrudes the geometry in headless Blender | `outputs/vibgyor.blend` |
| `python build.py render [--quick] [--shots 1,3]` | Four Cycles stills | `outputs/renders/01…04_*.png` |
| `python build.py export` | glTF for the browser (15 MB) | `web/model/vibgyor.glb`, copy in `outputs/` |
| `python build.py interior [--quick]` | Bonus: furnishes the Type A flat, renders a 4096×2048 panorama | `outputs/renders/05_typeA_drawing_room_360.png` |
| `python build.py serve [--port N]` | Static server for the viewer | http://localhost:8000 |
| `python build.py test` | pytest on the extraction | |

`make all`, `make quick` and `make serve` do the same.

## What you get

**Model.** Open stilt level with columns and core, seven floors, terrace with parapet and stair/lift
cabins, entrance canopy and lobby. Each floor has walls and 32 window / sliding-door openings sized from
the drawing's schedule. Balconies sit on the open slab edges with planters, glass infill, steel railings
and timber louvres. The plot has a compound wall with gate and sign, the 18 m road and 9 m side road,
and 25 trees at the positions drawn on the landscape plan.

**Renders** in `outputs/renders/`:

| File | View |
|---|---|
| `01_eye_level_day.png` | Eye level from the main road, day |
| `02_front_corner_dusk.png` | Front corner at dusk, interior and facade lights on |
| `03_aerial_day.png` | Aerial, about 45°, day |
| `04_entrance_closeup_day.png` | Entrance and canopy close-up, day |
| `05_typeA_drawing_room_360.png` | Bonus: equirectangular 360° panorama |

Cameras, lights and the sun direction are computed from the extracted geometry in `pipeline/render.py`.
The HDRI sky is aligned in code so the sun lights the facade. Cycles uses AgX tone mapping and denoising.

**Viewer** in `web/`. three.js with orbit, zoom and pan, HDRI lighting, a shadow-casting sun, AgX tone
mapping and PBR materials. A day/evening toggle switches the lighting. Click a floor, or use its number button,
to highlight it and see its flats: Type A (front, 160.05 m²) and Type B (rear, 143.18 m²), read from the plan text.
It measured 40–50 fps on an integrated Intel Iris Xe GPU when the machine was idle.

`outputs/demo_viewer.webm` is a recorded tour of the viewer. It was captured while the machine was busy,
so it runs below the viewer's real frame rate.

## Layout

```
build.py            one-command entry point
config.toml         the small documented config: drawing regions, layer names, heights, materials, render settings
pipeline/
  extract.py        DXF → plan.json (walls, openings, slabs, site, levels, unit types)
  model.py          plan.json → Blender scene (bmesh extrusion, materials, trees, site)
  render.py         cameras, sky and lights → Cycles stills
  export.py         .blend → glb + viewer metadata
  interior.py       bonus: Type A interior and 360° panorama
  assets.py         Poly Haven downloader and code-generated foliage texture
  framing.py        site-derived reference directions (front normal, etc.)
  preview.py        debug plot of what the extractor understood
  webshot.py        headless-browser check of the viewer (optional)
  demo_video.py     records the scripted viewer tour (optional)
  browser.py        Chromium launcher shared by the two helpers above
web/                three.js viewer (three.js is vendored)
tests/              pytest for the extraction
outputs/            renders, model files, viewer screenshots, demo video
```

## Tests

`python build.py test` runs 12 tests: 5 on synthetic geometry (wall fusion, isolated lines, open rooms,
door gaps, JSON output) and 7 that check the real drawing against numbers printed on it (titles, floor height,
footprint, openings schedule, wall thickness, unit areas, site registration). The 7 real-drawing tests skip
themselves when `input/` is absent.

## Limits

The layer names and the one-sheet layout are specific to this drawing. Several details are rule-based or
my own assumptions, not drawn: louvres, planters, hedges, parked cars, the 14 scenery trees and the entrance
canopy. [NOTES.md](NOTES.md) lists them, along with what is not modelled.

## Confidentiality

The drawings are confidential and for this assessment only. `input/`, `*.dxf` and `*.dwg` are git-ignored.
Keep the repo private and delete the drawings and `build/` after submission.

Third-party assets: three.js (MIT, vendored in `web/vendor`), and Poly Haven textures and HDRIs (CC0, downloaded at build time).
