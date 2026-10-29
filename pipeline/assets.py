"""Downloads the CC0 Poly Haven assets named in config.toml into build/assets (cached)."""
import json
import urllib.request

from .common import BUILD, load_config

API = "https://api.polyhaven.com/files/"
UA = {"User-Agent": "vibgyor-pipeline/1.0"}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def _fetch(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(_get(url))
    tmp.replace(dest)
    return dest


def texture(asset_id, res="1k"):
    """-> {'diff': Path, 'nor': Path|None, 'rough': Path|None}"""
    d = BUILD / "assets" / "textures" / asset_id
    meta = d / "files.json"
    if not meta.exists():
        d.mkdir(parents=True, exist_ok=True)
        meta.write_bytes(_get(API + asset_id))
    files = json.loads(meta.read_text())
    out = {}
    for key, maps in (("diff", ("Diffuse",)), ("nor", ("nor_gl",)), ("rough", ("Rough",))):
        out[key] = None
        for m in maps:
            try:
                u = files[m][res]["jpg"]["url"]
            except KeyError:
                continue
            out[key] = _fetch(u, d / f"{key}.jpg")
            break
    if out["diff"] is None:
        raise RuntimeError(f"texture {asset_id} has no diffuse map at {res}")
    return out


def hdri(asset_id, res="2k"):
    d = BUILD / "assets" / "hdri"
    meta = d / f"{asset_id}.json"
    if not meta.exists():
        d.mkdir(parents=True, exist_ok=True)
        meta.write_bytes(_get(API + asset_id))
    files = json.loads(meta.read_text())
    return _fetch(files["hdri"][res]["hdr"]["url"], d / f"{asset_id}_{res}.hdr")


def model(asset_id, res="1k"):
    """glTF model + its .bin / textures -> path of the .gltf (cached)."""
    d = BUILD / "assets" / "models" / asset_id
    meta = d / "files.json"
    if not meta.exists():
        d.mkdir(parents=True, exist_ok=True)
        meta.write_bytes(_get(API + asset_id))
    g = json.loads(meta.read_text())["gltf"][res]["gltf"]
    main = _fetch(g["url"], d / f"{asset_id}_{res}.gltf")
    for rel, f in g.get("include", {}).items():
        _fetch(f["url"], d / rel)
    return main


def foliage_texture(size=1024, seed=3):
    """Tileable leaf-cluster colour map drawn by code (no download): hundreds of small leaves in layered greens."""
    import math
    import random

    from PIL import Image, ImageDraw

    out = BUILD / "assets" / "procedural" / "foliage.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    img = Image.new("RGB", (size, size), (14, 40, 12))
    d = ImageDraw.Draw(img)
    greens = [(26, 70, 18), (38, 95, 24), (52, 120, 30), (70, 140, 38), (92, 158, 50), (30, 80, 20)]
    for layer, n in enumerate((2600, 2200, 1500)):
        for _ in range(n):
            x, y = rng.uniform(0, size), rng.uniform(0, size)
            L = rng.uniform(16, 30) * (1 - 0.15 * layer)
            W = L * rng.uniform(0.35, 0.5)
            a = rng.uniform(0, math.tau)
            base = greens[rng.randrange(len(greens))]
            k = 0.55 + 0.25 * layer + rng.uniform(-0.12, 0.12)
            col = tuple(min(255, int(c * k * 1.15)) for c in base)
            pts = []
            for t in range(0, 360, 30):
                u = math.radians(t)
                px, py = math.cos(u) * L / 2, math.sin(u) * W / 2
                pts.append((px * math.cos(a) - py * math.sin(a), px * math.sin(a) + py * math.cos(a)))
            for ox in (-size, 0, size):          # wrap around so the map tiles seamlessly
                for oy in (-size, 0, size):
                    d.polygon([(x + ox + px, y + oy + py) for px, py in pts], fill=col)
    img.save(out, quality=90)
    return out


def fetch_all(cfg=None):
    cfg = cfg or load_config()
    a = cfg["assets"]
    paths = {"hdri_day": hdri(a["hdri_day"], a.get("hdri_res", "4k")), "hdri_dusk": hdri(a["hdri_dusk"], a.get("hdri_res", "4k"))}
    paths["textures"] = {k: texture(v["id"], a["res"]) for k, v in a["textures"].items()}
    return paths


if __name__ == "__main__":
    p = fetch_all()
    for k, v in p["textures"].items():
        print(k, {m: (x.name if x else None) for m, x in v.items()})
    print(p["hdri_day"], p["hdri_dusk"])
