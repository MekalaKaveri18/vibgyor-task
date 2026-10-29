"""Stage 2 - build: plan.json -> Blender scene (outputs/vibgyor.blend).

Every wall, slab, opening, balcony parapet and plot element is extruded from the geometry that
pipeline/extract.py read out of the architect's drawing. Only appearance (materials, facade
detailing rules, scenery) is decided here.  Runs headless:  python -m pipeline.model
"""
import json
import math
import random

import bpy  # noqa  (must precede bmesh / mathutils)
import bmesh
from mathutils import Euler, Vector, geometry
from PIL import Image
from shapely.affinity import rotate as sh_rotate
from shapely.affinity import translate as sh_translate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from . import assets
from .common import BUILD, OUT, load_config


# =============================================================================== mesh helper
class Mesh:
    """bmesh wrapper: extruded prisms with per-face materials and real-world-metre UVs."""

    def __init__(self, name):
        self.name = name
        self.bm = bmesh.new()
        self.uv = self.bm.loops.layers.uv.new("UVMap")
        self.mats = []

    def slot(self, mat):
        if mat not in self.mats:
            self.mats.append(mat)
        return self.mats.index(mat)

    def face(self, pts, uvs, mat, smooth=False):
        vs = [self.bm.verts.new(p) for p in pts]
        try:
            f = self.bm.faces.new(vs)
        except ValueError:
            return None
        f.material_index = self.slot(mat)
        f.smooth = smooth
        for l, uv in zip(f.loops, uvs):
            l[self.uv].uv = uv
        return f

    def prism(self, poly, z0, z1, side, top=None, bottom=None):
        """Extrude a shapely polygon (holes allowed) between z0 and z1."""
        if poly.is_empty or poly.area < 1e-5 or z1 - z0 < 1e-4:
            return
        poly = orient(poly, 1.0)
        rings = [list(poly.exterior.coords)[:-1]] + [list(h.coords)[:-1] for h in poly.interiors]
        for ring in rings:
            s = 0.0
            n = len(ring)
            for i in range(n):
                a, b = ring[i], ring[(i + 1) % n]
                d = math.dist(a, b)
                if d < 1e-6:
                    continue
                self.face([(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)],
                          [(s, z0), (s + d, z0), (s + d, z1), (s, z1)], side)
                s += d
        if top is None and bottom is None:
            return
        flat = [[Vector((x, y, 0.0)) for x, y in r] for r in rings]
        tris = geometry.tessellate_polygon(flat)
        allv = [v for r in flat for v in r]
        for t in tris:
            p = [allv[i] for i in t]
            cz = (p[1].x - p[0].x) * (p[2].y - p[0].y) - (p[1].y - p[0].y) * (p[2].x - p[0].x)
            if cz < 0:                       # make every cap triangle counter-clockwise seen from +Z -> consistent outward normals
                p = [p[0], p[2], p[1]]
            if top is not None:
                self.face([(q.x, q.y, z1) for q in p], [(q.x, q.y) for q in p], top)
            if bottom is not None:
                self.face([(q.x, q.y, z0) for q in reversed(p)], [(q.x, q.y) for q in reversed(p)], bottom)

    def to_object(self, coll, name=None):
        me = bpy.data.meshes.new(name or self.name)
        self.bm.to_mesh(me)
        self.bm.free()
        for m in self.mats:
            me.materials.append(m)
        ob = bpy.data.objects.new(name or self.name, me)
        coll.objects.link(ob)
        return ob


def polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [q for p in getattr(g, "geoms", []) for q in polys(p)]


def from_json(p):
    return Polygon(p["outer"], p["holes"]).buffer(0)


def oriented_rect(cx, cy, length, thick, angle_deg):
    r = box(-length / 2, -thick / 2, length / 2, thick / 2)
    return sh_translate(sh_rotate(r, angle_deg, origin=(0, 0)), cx, cy)


def seg_rect(a, b, thick, extend=0.0):
    ln = LineString([a, b])
    return ln.buffer(thick / 2, cap_style=2, join_style=2)


# =============================================================================== materials
class Materials:
    def __init__(self, cfg, paths):
        self.cfg, self.paths, self.cache = cfg, paths, {}
        (BUILD / "assets" / "tinted").mkdir(parents=True, exist_ok=True)

    def _img(self, path, srgb):
        im = bpy.data.images.load(str(path), check_existing=True)
        im.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
        return im

    def pbr(self, key):
        if key in self.cache:
            return self.cache[key]
        spec = self.cfg["assets"]["textures"][key]
        tex = self.paths["textures"][key]
        diff = tex["diff"]
        if "tint" in spec:                       # bake the tint into the colour map so glTF carries it
            out = BUILD / "assets" / "tinted" / f"{key}.jpg"
            if not out.exists():
                im = Image.open(diff).convert("RGB")
                r, g, b = im.split()
                t = spec["tint"]
                im = Image.merge("RGB", (r.point(lambda v: min(255, int(v * t[0]))), g.point(lambda v: min(255, int(v * t[1]))),
                                         b.point(lambda v: min(255, int(v * t[2])))))
                im.save(out, quality=92)
            diff = out
        m = bpy.data.materials.new(key)
        m.use_nodes = True
        nt = m.node_tree
        bsdf = nt.nodes["Principled BSDF"]
        tc, mp = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeMapping")
        s = 1.0 / spec["scale"]
        mp.inputs["Scale"].default_value = (s, s, 1.0)
        nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])

        def tex_node(path, srgb):
            n = nt.nodes.new("ShaderNodeTexImage")
            n.image = self._img(path, srgb)
            nt.links.new(mp.outputs["Vector"], n.inputs["Vector"])
            return n

        d = tex_node(diff, True)
        nt.links.new(d.outputs["Color"], bsdf.inputs["Base Color"])
        if tex["rough"]:
            nt.links.new(tex_node(tex["rough"], False).outputs["Color"], bsdf.inputs["Roughness"])
        if tex["nor"]:
            nm = nt.nodes.new("ShaderNodeNormalMap")
            nt.links.new(tex_node(tex["nor"], False).outputs["Color"], nm.inputs["Color"])
            nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
        self.cache[key] = m
        return m

    def flat(self, key, color, rough=0.5, metal=0.0, emission=None, strength=0.0):
        if key in self.cache:
            return self.cache[key]
        m = bpy.data.materials.new(key)
        m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*color, 1)
        b.inputs["Roughness"].default_value = rough
        b.inputs["Metallic"].default_value = metal
        if emission:
            b.inputs["Emission Color"].default_value = (*emission, 1)
            b.inputs["Emission Strength"].default_value = strength
        self.cache[key] = m
        return m

    def foliage(self):
        if "foliage" in self.cache:
            return self.cache["foliage"]
        m = bpy.data.materials.new("foliage")
        m.use_nodes = True
        nt = m.node_tree
        b = nt.nodes["Principled BSDF"]
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = self._img(assets.foliage_texture(), True)
        nt.links.new(t.outputs["Color"], b.inputs["Base Color"])
        b.inputs["Roughness"].default_value = 0.65
        b.inputs["Subsurface Weight"].default_value = 0.0
        self.cache["foliage"] = m
        return m

    def glass(self):
        """Window glass for Cycles: Fresnel mix of a tinted transparent shader and a mirror-like glossy one
        (no refraction - cheap, noise-free, and what archviz uses for thin glazing).
        webify() swaps it for an alpha-blended PBR material for the glb."""
        if "glass" in self.cache:
            return self.cache["glass"]
        m = bpy.data.materials.new("glass")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        tr.inputs["Color"].default_value = (0.62, 0.74, 0.8, 1)
        gl = nt.nodes.new("ShaderNodeBsdfGlossy")
        gl.inputs["Roughness"].default_value = 0.03
        gl.inputs["Color"].default_value = (0.95, 0.97, 1.0, 1)
        fr = nt.nodes.new("ShaderNodeFresnel")
        fr.inputs["IOR"].default_value = 1.55
        mix = nt.nodes.new("ShaderNodeMixShader")
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(fr.outputs["Fac"], mix.inputs["Fac"])
        nt.links.new(tr.outputs["BSDF"], mix.inputs[1])
        nt.links.new(gl.outputs["BSDF"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        self.cache["glass"] = m
        return m


# =============================================================================== scene
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    return sc


def new_empty(name, parent=None, loc=(0, 0, 0), coll=None):
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = "PLAIN_AXES"
    e.location = loc
    (coll or bpy.context.scene.collection).objects.link(e)
    if parent:
        e.parent = parent
    return e


def link_copy(src, name, parent, loc, coll):
    ob = bpy.data.objects.new(name, src.data)  # linked duplicate: same mesh datablock (small glb, one draw per material)
    coll.objects.link(ob)
    ob.parent = parent
    ob.location = loc
    return ob


class Builder:
    def __init__(self, plan, cfg, paths):
        self.plan, self.cfg = plan, cfg
        self.M = Materials(cfg, paths)
        self.coll = bpy.context.scene.collection
        lv = plan["levels"]
        self.h = lv["floor_h"]
        self.t = lv["slab_t"]
        self.wh = self.h - self.t                       # clear wall height under the next slab
        self.G = cfg["geometry"]
        self.fp = from_json(plan["footprint"])
        self.slab = from_json(plan["slab"])
        self.walls = unary_union([from_json(w) for w in plan["walls"]])
        self.rng = random.Random(7)
        m = self.M
        self.stone, self.dark, self.edge = m.pbr("facade_stone"), m.pbr("facade_dark"), m.pbr("edge")
        self.plaster, self.conc, self.wood = m.pbr("plaster"), m.pbr("concrete"), m.pbr("wood")
        self.planter, self.tile = m.pbr("planter"), m.pbr("tile")
        self.frame = m.flat("frame", (0.05, 0.055, 0.06), rough=0.35, metal=0.8)
        self.glass = m.glass()
        self.leaf = m.flat("hedge", (0.09, 0.22, 0.06), rough=0.8)
        self.glow = m.flat("interior_glow", (1.0, 0.72, 0.42), rough=1.0, emission=(1.0, 0.68, 0.36), strength=0.0)
        self.card = m.flat("interior_card", (0.82, 0.78, 0.7), rough=1.0)

    # ------------------------------------------------------------------ typical floor
    def typical_floor(self):
        P, h, t, wh, G = self.plan, self.h, self.t, self.wh, self.G
        mesh = Mesh("Floor_typical")
        # slab with the 0.6 m cantilever: tile on top, plaster soffit, pale rendered edge
        for p in polys(self.slab):
            mesh.prism(p, -t, 0.0, self.edge, top=self.tile, bottom=self.plaster)

        ring = self.fp.boundary.buffer(0.32)
        ext = self.walls.intersection(ring)
        inn = self.walls.difference(ring)
        for p in polys(ext):
            mesh.prism(p, 0.0, wh, self.stone, top=self.plaster, bottom=self.plaster)
        for p in polys(inn):
            mesh.prism(p, 0.0, wh, self.plaster, top=self.plaster, bottom=self.plaster)

        glow_cards = []
        for o in P["openings"]:
            self._opening(mesh, o, wh, glow_cards)
        for d in P["doors"]:
            r = seg_rect(d["a"], d["b"], G["wall_thickness"])
            for p in polys(r):
                mesh.prism(p, d["head"], wh, self.plaster, top=self.plaster, bottom=self.plaster)

        self._balconies(mesh, wh)
        ob = mesh.to_object(self.coll)
        # emissive cards live in their own object so the renderer / viewer can switch the glow independently
        cm = Mesh("Floor_typical_interior")
        for pts, uvs in glow_cards:
            cm.face(pts, uvs, self.glow)
        cob = cm.to_object(self.coll)
        return ob, cob

    def _opening(self, mesh, o, wh, glow_cards):
        L, ang = o["length"], o["angle"]
        cx, cy, wt = o["cx"], o["cy"], self.G["wall_thickness"]
        sill, head = o["sill"], min(o["head"], wh)
        base = oriented_rect(cx, cy, L, wt, ang)
        if sill > 0.01:
            for p in polys(base):
                mesh.prism(p, 0.0, sill, self.stone, top=self.plaster, bottom=self.plaster)
        for p in polys(base):
            mesh.prism(p, head, wh, self.stone, top=self.plaster, bottom=self.plaster)
        # glazing + aluminium frame with mullions about every 1.1 m
        pane = oriented_rect(cx, cy, L - 0.08, 0.02, ang)
        for p in polys(pane):
            mesh.prism(p, sill + 0.04, head - 0.04, self.glass, top=self.glass, bottom=self.glass)
        bars = [(0, L, 0.05, sill, sill + 0.05), (0, L, 0.05, head - 0.05, head)]
        n = max(1, round(L / 1.1))
        for i in range(n + 1):
            off = -L / 2 + 0.025 + (L - 0.05) * i / n
            bars.append((off, 0.05, 0.05, sill, head))
        ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        for off, ln, th, z0, z1 in bars:
            c = (cx + ca * off, cy + sa * off) if ln != L else (cx, cy)
            for p in polys(oriented_rect(c[0], c[1], ln, 0.09, ang)):
                mesh.prism(p, z0, z1, self.frame, top=self.frame, bottom=self.frame)
        # warm card behind the glass on the room side (the side with more clear floor in front of it)
        nx, ny = -sa, ca
        def clear(sgn):
            ray = LineString([(cx + sgn * nx * 0.3, cy + sgn * ny * 0.3), (cx + sgn * nx * 8, cy + sgn * ny * 8)])
            inside = ray.intersection(self.fp)
            return inside.length if not inside.is_empty else 0.0
        sgn = 1 if clear(1) >= clear(-1) else -1
        if clear(sgn) > 1.5 and o["kind"] != "V":
            d = 0.7
            ccx, ccy = cx + sgn * nx * d, cy + sgn * ny * d
            hl = (L - 0.2) / 2
            z0, z1 = sill + 0.15, head - 0.15
            a = (ccx - ca * hl, ccy - sa * hl)
            b = (ccx + ca * hl, ccy + sa * hl)
            # winding so the emitting face points towards the window (outwards)
            pts = [(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)]
            if sgn > 0:
                pts = pts[::-1]
            glow_cards.append((pts, [(0, 0), (1, 0), (1, 1), (0, 1)]))

    def _balconies(self, mesh, wh):
        """Open slab edges get a planter box with hedge, a 1.15 m steel-and-glass railing (the drawing's
        '1.15 MT. HT. RAILING') and vertical timber louvres (brochure facade)."""
        rh = self.G["parapet_height"]              # guard height from the drawing
        ph = self.G["planter_height"]
        cen = self.fp.centroid
        for e in self.plan["open_edges"]:
            line = LineString(e)
            if line.length < 0.4:
                continue
            for p in polys(line.buffer(0.1, cap_style=2, join_style=2)):
                mesh.prism(p, 0.0, ph, self.planter, top=self.edge, bottom=self.planter)   # planter box
            # outward normal of the edge
            (x0, y0), (x1, y1) = e[0], e[-1]
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy) or 1
            ux, uy = dx / ln, dy / ln
            nx, ny = uy, -ux
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            if (cen.x - mx) * nx + (cen.y - my) * ny > 0:
                nx, ny = -nx, -ny
            ang = math.degrees(math.atan2(uy, ux))
            # railing: glass infill between the planter and a steel handrail, steel posts at most 1.4 m apart
            for p in polys(line.buffer(0.012, cap_style=2, join_style=2)):
                mesh.prism(p, ph + 0.05, rh - 0.05, self.glass, top=self.glass, bottom=self.glass)
            for z0, z1 in ((rh - 0.05, rh), (ph + 0.02, ph + 0.05)):
                for p in polys(line.buffer(0.025, cap_style=2, join_style=2)):
                    mesh.prism(p, z0, z1, self.frame, top=self.frame, bottom=self.frame)
            n_posts = max(2, int(math.ceil(line.length / 1.4)) + 1)
            for i in range(n_posts):
                s_ = i / (n_posts - 1) * line.length
                px, py = x0 + ux * s_, y0 + uy * s_
                for p in polys(box(px - 0.02, py - 0.02, px + 0.02, py + 0.02)):
                    mesh.prism(p, ph, rh, self.frame, top=self.frame, bottom=self.frame)
            # hedge in the planter
            k = int(line.length / 0.45)
            for i in range(k):
                s = (i + 0.5) / k * line.length
                px, py = x0 + ux * s, y0 + uy * s
                r = self.rng.uniform(0.14, 0.22)
                ico = bmesh.ops.create_icosphere(mesh.bm, subdivisions=1, radius=r)
                for v in ico["verts"]:
                    v.co += Vector((px, py, ph + 0.1))
                for f in {f for v in ico["verts"] for f in v.link_faces}:
                    f.material_index = mesh.slot(self.leaf)
                    f.smooth = False
                    for l in f.loops:
                        l[mesh.uv].uv = (l.vert.co.x, l.vert.co.y)
            # vertical louvres in front of the planter, 5 cm blades every 22 cm
            if line.length > 1.2:
                m = int(line.length / 0.22)
                for i in range(m):
                    s = (i + 0.5) / m * line.length
                    cx, cy = x0 + ux * s + nx * 0.16, y0 + uy * s + ny * 0.16
                    for p in polys(oriented_rect(cx, cy, 0.05, 0.22, ang + 90 - 90)):
                        mesh.prism(sh_rotate(p, 0, origin=(cx, cy)), 0.0, wh, self.wood, top=self.wood, bottom=self.wood)

    # ------------------------------------------------------------------ stilt level
    def stilt(self):
        P, wh = self.plan, self.wh
        mesh = Mesh("Stilt")
        # ground slab under the stilts
        for p in polys(self.slab):
            mesh.prism(p, -0.05, 0.03, self.conc, top=self.conc, bottom=self.conc)
        cols = [from_json(c) for c in P["ground"]["columns"]]
        for c in cols:
            for p in polys(c.buffer(0.04, join_style=2)):
                mesh.prism(p, 0.02, wh, self.edge, top=self.edge, bottom=self.edge)
        for w in P["ground"]["walls"]:
            for p in polys(from_json(w)):
                mesh.prism(p, 0.02, wh, self.dark, top=self.conc, bottom=self.conc)
        return mesh.to_object(self.coll)

    # ------------------------------------------------------------------ terrace + stair cabin
    def terrace(self):
        P, G, t = self.plan, self.G, self.t
        lv = P["levels"]
        z8 = self.h * 8
        mesh = Mesh("Terrace")
        for p in polys(self.slab):
            mesh.prism(p, -t, 0.0, self.edge, top=self.conc, bottom=self.plaster)
        for pj in P["terrace"]["parapet"]:
            for p in polys(from_json(pj)):
                mesh.prism(p, 0.0, G["parapet_height"], self.stone, top=self.edge, bottom=self.plaster)
        walls = sorted([from_json(w) for w in P["terrace"]["cabin_walls"]], key=lambda p: p.bounds[0])
        for i, w in enumerate(walls):
            top = (lv["lift_room_top"] if i == 0 else lv["stair_cabin_top"]) - z8
            top = max(top, 2.4)
            for p in polys(w):
                mesh.prism(p, 0.0, top, self.stone, top=None, bottom=None)
            roof = Polygon(w.exterior).buffer(0.12, join_style=2)
            for p in polys(roof):
                mesh.prism(p, top, top + 0.15, self.edge, top=self.conc, bottom=self.plaster)
        return mesh.to_object(self.coll), walls

    def lobby(self):
        """Gap between the lift block and the stair block of the core = lobby: (x0, x1, y_front)."""
        cab = sorted([from_json(w) for w in self.plan["terrace"]["cabin_walls"]], key=lambda p: p.bounds[0])
        l, r = cab[0].bounds, cab[1].bounds
        return l[2], r[0], min(l[1], r[1])

    def entrance(self, cab_walls):
        """Canopy + lobby front where the stair/lift core meets the stilt, placed from the core geometry."""
        x0, x1, y = self.lobby()
        mesh = Mesh("Entrance")
        led = self.M.flat("led", (1.0, 0.85, 0.6), rough=0.4, emission=(1.0, 0.75, 0.45), strength=0.0)
        mid = (x0 + x1) / 2
        E = self.cfg["entrance"]
        w = (x1 - x0) + E["canopy_overhang"]
        zc = E["canopy_height"]
        # canopy slab: pale rendered edge, timber soffit, warm LED strip along the front lip
        for p in polys(box(mid - w / 2, y - E["canopy_depth"], mid + w / 2, y + 0.25)):
            mesh.prism(p, zc, zc + 0.3, self.edge, top=self.edge, bottom=self.wood)
        for p in polys(box(mid - w / 2 + 0.1, y - E["canopy_depth"] + 0.04, mid + w / 2 - 0.1, y - E["canopy_depth"] + 0.1)):
            mesh.prism(p, zc - 0.02, zc, led, top=led, bottom=led)
        # lobby front: timber slat panels either side of a glazed door
        door_w = E["door_width"]
        for a, b in ((x0 + 0.2, mid - door_w / 2), (mid + door_w / 2, x1 - 0.2)):
            for p in polys(box(a, y - 0.03, b, y + 0.12)):
                mesh.prism(p, 0.03, zc, self.wood, top=self.wood, bottom=self.wood)
            n = int((b - a) / 0.14)
            for i in range(n):
                for p in polys(box(a + i * (b - a) / n, y - 0.1, a + i * (b - a) / n + 0.05, y)):
                    mesh.prism(p, 0.03, zc, self.wood, top=self.wood, bottom=self.wood)
        for p in polys(box(mid - door_w / 2, y - 0.03, mid + door_w / 2, y - 0.01)):
            mesh.prism(p, 0.03, 2.3, self.glass, top=self.glass, bottom=self.glass)
        for xx in (mid - door_w / 2, mid, mid + door_w / 2 - 0.05):
            for p in polys(box(xx, y - 0.06, xx + 0.05, y)):
                mesh.prism(p, 0.03, 2.3, self.frame, top=self.frame, bottom=self.frame)
        for p in polys(box(mid - door_w / 2, y - 0.06, mid + door_w / 2, y)):
            mesh.prism(p, 2.25, 2.3, self.frame, top=self.frame, bottom=self.frame)
        return mesh.to_object(self.coll)

    # ------------------------------------------------------------------ site
    def site(self):
        S = self.plan["site"]
        cfg = self.cfg
        plot = from_json(S["plot"])
        m = Mesh("Site")
        ground = box(-160, -160, 160, 160)
        m.prism(ground, -0.3, -0.02, self.M.pbr("grass"), top=self.M.pbr("grass"))
        # roads: rectangle fitted to each road polygon and extended along its length
        road_polys = []
        for r in S["roads"]:
            rp = from_json(r)
            rect = rp.minimum_rotated_rectangle
            c = list(rect.exterior.coords)[:4]
            e0, e1 = (c[0], c[1]), (c[1], c[2])
            long_e = e0 if math.dist(*e0) >= math.dist(*e1) else e1
            ux, uy = (long_e[1][0] - long_e[0][0]), (long_e[1][1] - long_e[0][1])
            n = math.hypot(ux, uy)
            ux, uy = ux / n, uy / n
            ext = 55
            pts = [(x + sx * ux * ext, y + sx * uy * ext) for (x, y), sx in zip(c, [-1, 1, 1, -1])] if long_e is e0 else \
                  [(x + sx * ux * ext, y + sx * uy * ext) for (x, y), sx in zip(c, [-1, -1, 1, 1])]
            road_polys.append(Polygon(pts).convex_hull)
        roads = unary_union(road_polys)
        # the long main road is drawn first so a side road does not cut through it
        walk = roads.buffer(1.6, join_style=2).difference(roads)
        for p in polys(roads):
            m.prism(p, -0.02, 0.015, self.M.pbr("asphalt"), top=self.M.pbr("asphalt"))
        for p in polys(walk.difference(plot)):
            m.prism(p, -0.02, 0.07, self.conc, top=self.M.pbr("paving"))
        # plot: paved forecourt with a lawn belt inside the compound wall
        lawn = plot.difference(plot.buffer(-2.2, join_style=2)).difference(self.slab.buffer(0.5))
        paved = plot.difference(roads).difference(lawn).difference(self.slab.buffer(0.05))   # no coplanar overlap with the stilt slab (it renders black)
        for p in polys(paved):
            m.prism(p, -0.02, 0.02, self.M.pbr("paving"), top=self.M.pbr("paving"))
        for p in polys(lawn):
            m.prism(p, -0.02, 0.03, self.M.pbr("grass"), top=self.M.pbr("grass"))
        # compound wall around the plot, with a gate in the longest road-facing edge
        front = sorted(S["front_edges"], key=lambda e: e[0][0])      # gate goes in the westernmost road-facing edge (brochure: entry at the left)
        gate = None
        if front:
            a, b = front[0]
            gx = a[0] + (b[0] - a[0]) * cfg["site"]["gate_t"]
            gy = a[1] + (b[1] - a[1]) * cfg["site"]["gate_t"]
            ln = math.dist(a, b)
            ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
            gate = ((gx, gy), (ux, uy), cfg["site"]["gate_width"], math.degrees(math.atan2(uy, ux)))
        wall_h = cfg["site"]["wall_height"]
        front_keys = {tuple(sorted(map(tuple, e))) for e in S["front_edges"]}
        others = [e for e in front[1:]] if front else []
        sign_edge = max(others or front, key=lambda e: math.dist(*e)) if front else None
        ring = list(plot.exterior.coords)
        for a, b in zip(ring, ring[1:]):
            seg = LineString([a, b])
            if seg.length < 0.3:
                continue
            piece = seg
            if gate and seg.distance(Point(gate[0])) < 0.05:
                g = Point(gate[0]).buffer(gate[2] / 2)
                piece = seg.difference(g)
            for ls in (piece.geoms if hasattr(piece, "geoms") else [piece]):
                if ls.length < 0.2:
                    continue
                facing_road = tuple(sorted([tuple(map(lambda v: round(v, 3), a)), tuple(map(lambda v: round(v, 3), b))])) in front_keys
                for p in polys(ls.buffer(0.115, cap_style=2, join_style=2)):
                    m.prism(p, 0.0, wall_h, self.dark if facing_road else self.stone, top=self.edge, bottom=self.edge)
        if gate:
            (gx, gy), (ux, uy), gw, ang = gate
            # timber slatted gate leaves (the brochure shows a dark timber gate) and two stone piers
            for sgn in (-1, 1):
                pier = oriented_rect(gx + sgn * ux * (gw / 2 + 0.2), gy + sgn * uy * (gw / 2 + 0.2), 0.45, 0.45, ang)
                for p in polys(pier):
                    m.prism(p, 0.0, wall_h + 0.4, self.stone, top=self.edge, bottom=self.edge)
            leaf = oriented_rect(gx, gy, gw - 0.1, 0.06, ang)
            for p in polys(leaf):
                m.prism(p, 0.1, 2.0, self.wood, top=self.wood, bottom=self.wood)
        site_ob = m.to_object(self.coll)
        return site_ob, gate, sign_edge

    def sign(self, edge):
        """'VIBGYOR' lettering centred on the longest road-facing compound wall, as in the brochure render."""
        if not edge:
            return None
        (ax, ay), (bx, by) = edge
        ln = math.dist(edge[0], edge[1])
        ux, uy = (bx - ax) / ln, (by - ay) / ln
        curve = bpy.data.curves.new("sign", "FONT")
        curve.body = "VIBGYOR"
        curve.size = 1.0
        curve.extrude = 0.03
        curve.align_x = "CENTER"
        curve.align_y = "CENTER"
        curve.space_character = 1.5
        ob = bpy.data.objects.new("Sign_VIBGYOR", curve)
        self.coll.objects.link(ob)
        nx, ny = uy, -ux                         # wall normal; flip to face the road
        road = from_json(self.plan["site"]["roads"][0]).centroid
        if (road.x - ax) * nx + (road.y - ay) * ny < 0:
            nx, ny = -nx, -ny
        ob.location = ((ax + bx) / 2 + nx * 0.14, (ay + by) / 2 + ny * 0.14, 0.95)
        ob.rotation_euler = Euler((math.radians(90), 0, math.atan2(ny, nx) + math.radians(90)), "XYZ")
        ob.data.materials.append(self.M.flat("sign", (0.92, 0.88, 0.8), rough=0.4, emission=(1.0, 0.85, 0.6), strength=0.0))
        return ob

    # ------------------------------------------------------------------ trees and cars
    def trees(self):
        S, cfg = self.plan["site"], self.cfg["site"]
        coll = self.coll
        leaf = self.M.foliage()
        trunk_m = self.M.flat("trunk", (0.2, 0.14, 0.1), rough=0.9)
        variants = []

        def planar_uv(mesh, f, c):
            n = f.normal
            ax = max(range(3), key=lambda i: abs(n[i]))
            for l in f.loops:
                co = l.vert.co
                l[mesh.uv].uv = ((co.y if ax == 0 else co.x) * 0.45, (co.y if ax == 2 else co.z) * 0.45)

        for v in range(3):
            tm = Mesh(f"tree_var{v}")
            bm = tm.bm
            H, R = cfg["tree_height"] * self.rng.uniform(0.85, 1.2), cfg["tree_crown"] * self.rng.uniform(0.85, 1.2)
            cyl = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.2, radius2=0.09, depth=H * 0.6)
            for vtx in cyl["verts"]:
                vtx.co.z += H * 0.3
                vtx.co.x += 0.05 * math.sin(vtx.co.z * 1.3)
            mt = tm.slot(trunk_m)
            for f in {f for vtx in cyl["verts"] for f in vtx.link_faces}:
                f.material_index = mt
                for l in f.loops:
                    l[tm.uv].uv = (0, 0)
            # a few branches as thin cones reaching into the crown
            for k in range(4):
                a = self.rng.uniform(0, math.tau)
                br = bmesh.ops.create_cone(bm, cap_ends=False, segments=5, radius1=0.07, radius2=0.03, depth=R * 1.0)
                for vtx in br["verts"]:
                    vtx.co.z += R * 0.5
                bmesh.ops.rotate(bm, verts=br["verts"], cent=(0, 0, 0), matrix=Euler((math.radians(55), 0, a)).to_matrix())
                bmesh.ops.translate(bm, verts=br["verts"], vec=(0, 0, H * 0.45))
                for f in {f for vtx in br["verts"] for f in vtx.link_faces}:
                    f.material_index = mt
                    for l in f.loops:
                        l[tm.uv].uv = (0, 0)
            ml = tm.slot(leaf)
            for k in range(11):                      # many smaller lobes -> a ragged, natural crown outline
                a = self.rng.uniform(0, math.tau)
                rr = R * self.rng.uniform(0.15, 0.85)
                c = Vector((math.cos(a) * rr, math.sin(a) * rr, H * (0.62 + 0.32 * (1 - rr / R) * self.rng.uniform(0.6, 1.0))))
                ico = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=R * self.rng.uniform(0.32, 0.5))
                for vtx in ico["verts"]:
                    n = vtx.co.normalized()
                    vtx.co = vtx.co * Vector((1, 1, 0.78)) + c + n * self.rng.uniform(-0.12, 0.1)
                for f in {f for vtx in ico["verts"] for f in vtx.link_faces}:
                    f.material_index = ml
                    f.smooth = True
                    planar_uv(tm, f, c)
            variants.append(tm.to_object(coll, f"tree_var{v}"))
        out = []
        for i, (x, y) in enumerate(S["trees"]):
            v = variants[i % 3]
            ob = link_copy(v, f"Tree_{i:02d}", None, (x, y, 0), coll)
            ob.rotation_euler.z = self.rng.uniform(0, math.tau)
            s = self.rng.uniform(0.85, 1.2)
            ob.scale = (s, s, s)
            out.append(ob)
        for v in variants:                      # templates stay hidden away from the site
            v.location = (0, 0, -50)
        plot = from_json(S["plot"])
        n = 0
        while n < cfg["background_trees"]:
            x, y = self.rng.uniform(-45, 55), self.rng.uniform(-50, 70)
            if plot.buffer(4).contains(Point(x, y)) or math.hypot(x - 7, y - 10) < 10:
                continue
            ob = link_copy(variants[n % 3], f"BgTree_{n:02d}", None, (x, y, 0), coll)
            ob.rotation_euler.z = self.rng.uniform(0, math.tau)
            s = self.rng.uniform(1.0, 1.6)
            ob.scale = (s, s, s)
            out.append(ob)
            n += 1
        return out, variants

    def cars(self):
        """A few parked cars on the stilt level, on a grid placed in the free stilt area (no column clashes)."""
        cols = unary_union([from_json(c) for c in self.plan["ground"]["columns"]] +
                           [from_json(w) for w in self.plan["ground"]["walls"]]).buffer(0.4)
        area = self.fp.buffer(-0.5)
        lx0, lx1, ly = self.lobby()
        keep_clear = box(lx0 - 1.5, ly - 12.0, lx1 + 1.5, ly)          # keep the lobby approach free for the entrance view
        palette = [(0.85, 0.85, 0.87), (0.1, 0.1, 0.12), (0.45, 0.47, 0.5), (0.55, 0.08, 0.08), (0.9, 0.9, 0.9)]
        spots = []
        y = 1.5
        for iy in range(7):
            for ix in range(5):
                x = 2.6 + ix * 2.55
                car = box(x - 0.95, y + iy * 5.3 - 2.4, x + 0.95, y + iy * 5.3 + 2.4)
                if area.contains(car) and not car.intersects(cols) and not car.intersects(keep_clear) and self.rng.random() < 0.55:
                    spots.append((x, y + iy * 5.3))
        mesh = Mesh("Cars")
        for i, (x, yy) in enumerate(spots[:9]):
            body = self.M.flat(f"car{i % 5}", palette[i % 5], rough=0.25, metal=0.6)
            L, W = 4.2, 1.75
            mesh.prism(box(x - W / 2, yy - L / 2, x + W / 2, yy + L / 2), 0.25, 0.85, body, top=body, bottom=body)
            mesh.prism(box(x - W / 2 + 0.1, yy - 1.0, x + W / 2 - 0.1, yy + 1.1), 0.85, 1.35, self.glass, top=body, bottom=body)
            for sx in (-1, 1):
                for sy in (-1.3, 1.3):
                    mesh.prism(box(x + sx * (W / 2 - 0.1) - 0.1, yy + sy - 0.32, x + sx * (W / 2 - 0.1) + 0.1, yy + sy + 0.32),
                               0.0, 0.64, self.frame, top=self.frame, bottom=self.frame)
        return mesh.to_object(self.coll) if spots else None


# =============================================================================== assembly
def build(plan=None, cfg=None):
    cfg = cfg or load_config()
    plan = plan or json.loads((BUILD / "plan.json").read_text())
    paths = assets.fetch_all(cfg)
    sc = reset_scene()
    B = Builder(plan, cfg, paths)
    root = new_empty("Vibgyor_Tower")
    h = B.h
    flats = {u["type"]: u for u in plan["units"]}

    st = B.stilt()
    st_g = new_empty("Stilt", root, (0, 0, 0))
    st.parent = st_g
    st["label"] = "Stilt parking"
    cars = B.cars()
    if cars:
        cars.parent = st_g

    typ, glow = B.typical_floor()
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}
    floors = []
    for k in range(1, 8):
        g = new_empty(f"Floor_{k}", root, (0, 0, h * k))
        g["floor"] = k
        g["label"] = f"{ordinal.get(k, str(k) + 'th')} floor"
        g["flats"] = json.dumps([{"type": u["type"], "flat": f"{k}{u['first'][1:]}", "carpet_sqm": u["carpet_sqm"], "position": u["position"]}
                                 for u in sorted(plan["units"], key=lambda u: u["type"])])
        a = link_copy(typ, f"Floor_{k}_shell", g, (0, 0, 0), bpy.context.scene.collection)
        b = link_copy(glow, f"Floor_{k}_interior", g, (0, 0, 0), bpy.context.scene.collection)
        floors.append(g)
    for tmpl in (typ, glow):                      # the template objects are replaced by their 7 linked copies
        bpy.data.objects.remove(tmpl)

    ter, cab = B.terrace()
    ter_g = new_empty("Terrace", root, (0, 0, h * 8))
    ter_g["label"] = "Terrace & stair cabin"
    ter.parent = ter_g
    ent = B.entrance(cab)
    if ent:
        ent.parent = root

    site, gate, sign_edge = B.site()
    site.parent = root
    B.sign(sign_edge)
    trees, templates = B.trees()
    for o in bpy.data.objects:
        if o.type == "MESH" and o.parent is None and o.name.startswith("tree_var"):
            o.hide_render = True
            o.hide_viewport = True
    OUT.mkdir(exist_ok=True)
    return sc, root, gate


def main():
    cfg = load_config()
    sc, root, gate = build(cfg=cfg)
    bpy.ops.file.pack_all()
    path = OUT / "vibgyor.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True)
    n_tris = sum(len(o.data.polygons) for o in bpy.data.objects if o.type == "MESH")
    print(f"[build] saved {path} ({path.stat().st_size / 1e6:.1f} MB), {len(bpy.data.objects)} objects, ~{n_tris} faces")


if __name__ == "__main__":
    main()
