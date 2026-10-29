"""Stage 4 - export: outputs/vibgyor.blend -> web/model/vibgyor.glb  (+ outputs/vibgyor.glb copy).

The glb is the same scene, with two web-specific tweaks: the Cycles-only Fresnel glass becomes an
alpha-blended PBR material, and the interior glow cards keep their emissive colour so the viewer can
switch them on for the evening look.
"""
import json
import shutil

import bpy  # noqa

from . import assets
from .common import BUILD, OUT, ROOT, load_config
from .framing import Frame


def webify_materials():
    old = bpy.data.materials.get("glass")
    if old is None:
        return
    m = bpy.data.materials.new("glass_web")
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.55, 0.7, 0.8, 1)
    b.inputs["Alpha"].default_value = 0.32
    b.inputs["Roughness"].default_value = 0.04
    b.inputs["Metallic"].default_value = 0.0
    try:
        m.surface_render_method = "BLENDED"
    except Exception:
        pass
    try:
        m.blend_method = "BLEND"
    except Exception:
        pass
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        for slot in ob.material_slots:
            if slot.material is old:
                slot.material = m
    # the glow material: constant emissive colour, strength 1 (the viewer scales it)
    g = bpy.data.materials.get("interior_glow")
    if g:
        g.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 1.0


def write_web_assets(web):
    """meta.json (orientation, floors, flats) + 2k HDRIs the viewer lights itself with."""
    cfg = load_config()
    plan = json.loads((BUILD / "plan.json").read_text())
    F = Frame(plan)
    lv = plan["levels"]
    meta = {
        "front_normal": [float(F.n[0]), float(F.n[1])],          # plan-space unit vector pointing from the plot to the 18 m road
        "centre": [float(F.centre[0]), float(F.centre[1])],
        "bounds": list(F.fp.bounds),
        "floor_h": lv["floor_h"], "floors": cfg["floors"]["count"], "terrace_z": lv["floor_h"] * 8,
        "total_height": lv["lift_room_top"],
        "units": plan["units"],
    }
    (web / "model" / "meta.json").write_text(json.dumps(meta, indent=1))
    a = cfg["assets"]
    (web / "assets").mkdir(exist_ok=True)
    for key, name in (("hdri_day", "sky_day.hdr"), ("hdri_dusk", "sky_dusk.hdr")):
        shutil.copy2(assets.hdri(a[key], "2k"), web / "assets" / name)


def export(glb_path=None):
    bpy.ops.wm.open_mainfile(filepath=str(OUT / "vibgyor.blend"))
    webify_materials()
    dest = glb_path or ROOT / "web" / "model" / "vibgyor.glb"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for ob in list(bpy.data.objects):          # hidden template meshes / scene helpers do not belong in the web model
        if ob.hide_render and ob.type == "MESH" and ob.name.startswith("tree_var"):
            bpy.data.objects.remove(ob)
    bpy.ops.export_scene.gltf(
        filepath=str(dest), export_format="GLB", export_extras=True, export_apply=True, export_yup=True,
        export_image_format="JPEG", export_jpeg_quality=85, export_cameras=False, export_lights=False,
        export_materials="EXPORT", use_selection=False, export_texcoords=True, export_normals=True,
    )
    write_web_assets(dest.parent.parent)
    mb = dest.stat().st_size / 1e6
    shutil.copy2(dest, OUT / "vibgyor.glb")
    print(f"[export] {dest} ({mb:.1f} MB)")
    if mb > 40:
        raise SystemExit("glb exceeds the 40 MB limit")
    return dest


if __name__ == "__main__":
    export()
