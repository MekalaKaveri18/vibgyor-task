"""Stage 3 - render: outputs/vibgyor.blend -> four photoreal stills (Cycles, HDRI sky, AgX).

Cameras, sun alignment and lighting are all computed from the model geometry here; nothing is set by hand
in the Blender GUI.   python -m pipeline.render [--shots 1,2,3,4] [--quick]
"""
import argparse
import json
import math
import sys
import time

import bpy  # noqa
import numpy as np
from mathutils import Euler, Vector
from shapely.geometry import Point, Polygon

from . import assets
from .framing import Frame
from .common import BUILD, OUT, load_config


# ----------------------------------------------------------------------------- world / sky
def sun_direction_in_hdri(path):
    """Azimuth/elevation (radians, Blender equirect convention) of the brightest pixel of an HDRI."""
    im = bpy.data.images.load(str(path), check_existing=False)
    w, h = im.size
    a = np.empty(w * h * 4, dtype=np.float32)
    im.pixels.foreach_get(a)
    a = a.reshape(h, w, 4)
    lum = a[..., :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    r, c = np.unravel_index(np.argmax(lum), lum.shape)
    u, v = (c + 0.5) / w, (r + 0.5) / h
    bpy.data.images.remove(im)
    return (0.5 - u) * 2 * math.pi, (v - 0.5) * math.pi


def set_sky(hdri_path, sun_az, strength, sun_elev_out=None):
    """HDRI world lighting, rotated so the sun lands at world azimuth `sun_az` (direction towards the sun)."""
    sc = bpy.context.scene
    w = bpy.data.worlds.new("sky")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    phi_s, el = sun_direction_in_hdri(hdri_path)       # (loads and frees its own copy of the image)
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(str(hdri_path), check_existing=True)
    tc, mp = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeMapping")
    bg, out = nt.nodes.new("ShaderNodeBackground"), nt.nodes.new("ShaderNodeOutputWorld")
    mp.inputs["Rotation"].default_value = (0, 0, phi_s - sun_az)
    bg.inputs["Strength"].default_value = strength
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return el


# ----------------------------------------------------------------------------- geometry-derived framing
def make_camera(name, loc, target, lens, shift_y=0.0, shift_x=0.0, level=True):
    sc = bpy.context.scene
    cam = bpy.data.cameras.new(name)
    cam.lens, cam.sensor_width, cam.sensor_fit = lens, 36, "HORIZONTAL"
    cam.shift_y, cam.shift_x = shift_y, shift_x
    cam.clip_start, cam.clip_end = 0.1, 3000
    ob = bpy.data.objects.new(name, cam)
    sc.collection.objects.link(ob)
    ob.location = loc
    d = Vector(target) - Vector(loc)
    if level:                                    # architectural photo: horizontal view direction, vertical lines stay vertical
        d = Vector((d.x, d.y, 0))
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


# ----------------------------------------------------------------------------- shots
def shots(F, plan):
    t = Vector((F.centre[0], F.centre[1], 0))
    n = F.n
    tower = F.fp
    h = F.h
    # 1. eye level from the main road, in front of the gate, looking at the front facade
    c1 = F.pt(along=0.0, out=30.0, z=1.65)
    tgt1 = F.pt(along=0.0, out=0.0, z=0)
    # 2. dusk, front corner: east end of the road frontage, on a spot clear of the plot trees
    trees = [Point(*t) for t in plan["site"]["trees"]]
    best = None
    for along in (26, 30, 34, 22, 38):
        for out in (13, 16, 19):
            c = F.pt(along=along, out=out, z=1.7)
            d = min(Point(c[0], c[1]).distance(t) for t in trees)
            if d > 6 and (best is None or d > best[0] + 2):
                best = (d, c)
    c2 = best[1] if best else F.pt(along=30.0, out=22.0, z=1.7)
    tgt2 = Vector((tower.bounds[2] - 5, tower.bounds[1] + 12, 0))
    # 3. aerial 45 degrees from the road side
    az = math.atan2(n[1], n[0]) + math.radians(28)
    dist = 64
    c3 = Vector((F.centre[0] + math.cos(az) * dist, F.centre[1] + math.sin(az) * dist, dist * math.sin(math.radians(45))))
    tgt3 = Vector((F.centre[0], F.centre[1], 8))
    # 4. close-up of the entrance and canopy
    cab = sorted(plan["terrace"]["cabin_walls"], key=lambda w: min(p[0] for p in w["outer"]))
    l = Polygon(cab[0]["outer"]).bounds
    r = Polygon(cab[1]["outer"]).bounds
    ex, ey = (l[2] + r[0]) / 2, min(l[1], r[1])
    c4 = Vector((ex + 1.6, ey - 6.5, 1.5))
    tgt4 = Vector((ex, ey - 1.0, 2.0))
    return {
        1: dict(name="01_eye_level_day", cam=(c1, tgt1, 22, 0.20), time="day", sun_off=-38),
        2: dict(name="02_front_corner_dusk", cam=(c2, tgt2, 26, 0.24), time="dusk", sun_off=18, backlit=True),
        3: dict(name="03_aerial_day", cam=(c3, tgt3, 30, 0.0), time="day", sun_off=-38, level=False),
        4: dict(name="04_entrance_closeup_day", cam=(c4, tgt4, 20, -0.02), time="day", sun_off=-30),
    }


def setup_cycles(cfg, samples, quick, res_scale):
    sc = bpy.context.scene
    r = cfg["render"]
    sc.render.engine = "CYCLES"
    cy = sc.cycles
    cy.device = "CPU"
    cy.samples = samples
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = 0.02
    cy.use_denoising = True
    cy.denoiser = "OPENIMAGEDENOISE"
    cy.max_bounces, cy.transmission_bounces, cy.glossy_bounces, cy.diffuse_bounces = 8, 10, 6, 4
    cy.sample_clamp_indirect = 8
    cy.caustics_reflective = cy.caustics_refractive = False
    sc.render.resolution_x = int(r["width"] * res_scale)
    sc.render.resolution_y = int(r["height"] * res_scale)
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_depth = "8"
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    sc.display_settings.display_device = "sRGB"
    sc.render.threads_mode = "AUTO"
    sc.render.film_transparent = False


def set_glow(strength, warm):
    """Interior light cards: neutral and faint by day, warm and strong at dusk; the name sign lights up at dusk."""
    for m in bpy.data.materials:
        if not m.use_nodes:
            continue
        b = m.node_tree.nodes.get("Principled BSDF")
        if m.name.startswith("interior_glow"):
            b.inputs["Emission Color"].default_value = (1.0, 0.45, 0.14, 1) if warm else (0.9, 0.9, 0.86, 1)
            b.inputs["Emission Strength"].default_value = strength
        elif m.name.startswith("sign"):
            b.inputs["Emission Strength"].default_value = 6.0 if warm else 0.0
        elif m.name == "led":
            b.inputs["Emission Strength"].default_value = 14.0 if warm else 0.0


def add_lights(F, dusk, fill=False):
    """Stilt lighting. Dusk: warm recessed downlights (spots). Day entrance shot: one soft fill standing in for bounce."""
    sc = bpy.context.scene
    if dusk:
        for i, (x, y) in enumerate([(3.5, 4.5), (9.5, 4.5), (3.5, 11), (9.5, 11), (3.5, 16.5), (9.5, 16.5), (6.5, 21.0), (6.5, 27.0)]):
            L = bpy.data.lights.new(f"stilt{i}", "SPOT")
            L.energy, L.color, L.spot_size, L.spot_blend, L.shadow_soft_size = 1800, (1.0, 0.74, 0.45), math.radians(100), 0.9, 0.12
            ob = bpy.data.objects.new(f"stilt{i}", L)
            sc.collection.objects.link(ob)
            ob.location = (x, y, F.h - 0.12)
            ob.rotation_euler = Euler((0, 0, 0))             # spots shine along -Z: straight down
    if fill:
        L = bpy.data.lights.new("fill", "AREA")
        L.energy, L.color, L.size, L.size_y, L.shape = 2200, (1.0, 0.96, 0.9), 5.0, 2.0, "RECTANGLE"
        ob = bpy.data.objects.new("fill", L)
        sc.collection.objects.link(ob)
        ob.location = (F.centre[0] + 1.0, 8.0, 2.55)
        ob.rotation_euler = Euler((math.radians(80), 0, 0))     # facing +Y, slightly down, towards the lobby


def render(only=None, quick=False):
    cfg = load_config()
    plan = json.loads((BUILD / "plan.json").read_text())
    paths = assets.fetch_all(cfg)
    OUT.mkdir(exist_ok=True)
    F_cache = None
    todo = shots(Frame(plan), plan)
    for idx, s in todo.items():
        if only and idx not in only:
            continue
        bpy.ops.wm.open_mainfile(filepath=str(OUT / "vibgyor.blend"))
        F = Frame(plan)
        s = shots(F, plan)[idx]
        dusk = s["time"] == "dusk"
        setup_cycles(cfg, (cfg["render"]["samples_dusk"] if dusk else cfg["render"]["samples_day"]) // (4 if quick else 1),
                     quick, 0.62 if quick else 1.0)
        # direction towards the sun in world azimuth: relative to the direction of the road-facing normal
        face_az = math.atan2(F.n[1], F.n[0])
        sun_az = face_az + math.radians(s["sun_off"])
        if s.get("backlit"):                         # evening: low sun behind the tower as seen from the camera -> glow + lit windows
            c, t = s["cam"][0], s["cam"][1]
            sun_az = math.atan2(t[1] - c[1], t[0] - c[0]) + math.radians(s["sun_off"])
        set_sky(paths["hdri_dusk" if dusk else "hdri_day"], sun_az, 0.55 if dusk else 1.0)
        set_glow(7.0 if dusk else 0.45, dusk)
        add_lights(F, dusk, fill=(idx == 4))
        loc, tgt, lens, shift = s["cam"]
        cam = make_camera(s["name"], loc, tgt, lens, shift, level=s.get("level", True))
        bpy.context.scene.camera = cam
        if idx == 2:                                   # dusk: look slightly up the facade
            cam.data.shift_y = 0.30
        folder = (BUILD / "preview") if quick else (OUT / "renders")
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / (s["name"] + ".png")
        bpy.context.scene.render.filepath = str(out)
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        print(f"[render] {out.name} {bpy.context.scene.render.resolution_x}x{bpy.context.scene.render.resolution_y} in {time.time() - t0:.0f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="1,2,3,4")
    ap.add_argument("--quick", action="store_true", help="low-res low-sample preview")
    a = ap.parse_args(argv)
    render({int(x) for x in a.shots.split(",")}, a.quick)


if __name__ == "__main__":
    main()
