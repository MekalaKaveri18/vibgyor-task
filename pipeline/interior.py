"""Bonus - Type A interior + 360° panorama of the drawing room.

The shell (walls, door gaps, windows, slab) is already in the model; this stage furnishes the Type A flat of one floor
from the room labels printed on the plan ("BED ROOM 4.56 x 3.20" sits at the room's centre): each label gives a room rectangle,
and furniture is placed against the walls the plan actually has (headboards on solid walls, sofa facing the TV wall, ...).
Then Cycles renders an equirectangular 4096 x 2048 panorama from the middle of the drawing room.

    python -m pipeline.interior [--quick]        ->  outputs/renders/05_typeA_drawing_room_360.png
"""
import argparse
import json
import math
import time

import bpy  # noqa
import bmesh
from shapely.geometry import Point, box
from shapely.ops import unary_union

from . import assets
from .common import BUILD, OUT, load_config
from .framing import Frame
from .model import Materials, Mesh, from_json, polys
from .render import set_sky, setup_cycles


# ----------------------------------------------------------------------------- room analysis
def room_rect(room, free):
    """The labelled w x d rectangle, rotated so it best fits the free floor space (rooms are axis-aligned here)."""
    best = None
    for a, b in ((room["w"], room["d"]), (room["d"], room["w"])):
        r = box(room["x"] - a / 2, room["y"] - b / 2, room["x"] + a / 2, room["y"] + b / 2)
        ov = r.intersection(free).area
        if best is None or ov > best[0]:
            best = (ov, r)
    return best[1]


def side_info(rect, walls, windows):
    """For each side of the rectangle: how much of it is backed by wall, and whether a window / door gap is there."""
    x0, y0, x1, y1 = rect.bounds
    sides = {"S": ((x0, y0), (x1, y0), (0, 1)), "N": ((x0, y1), (x1, y1), (0, -1)),
             "W": ((x0, y0), (x0, y1), (1, 0)), "E": ((x1, y0), (x1, y1), (-1, 0))}
    out = {}
    from shapely.geometry import LineString
    for k, (a, b, inward) in sides.items():
        ln = LineString([a, b]).buffer(0.3, cap_style=2)
        cover = walls.intersection(ln).area / ln.area
        out[k] = {"line": (a, b), "inward": inward, "cover": cover, "len": LineString([a, b]).length,
                  "window": windows.intersects(ln)}
    return out


class Furnisher:
    def __init__(self, cfg, paths, plan, floor_h):
        self.M = Materials(cfg, paths)
        self.plan, self.h = plan, floor_h
        self.mesh = Mesh("Interior")
        flat = self.M.flat
        self.mat = {
            "wood": self.M.pbr("wood"),
            "oak": flat("oak", (0.62, 0.45, 0.28), rough=0.55),
            "fabric": flat("fabric", (0.55, 0.5, 0.44), rough=0.95),
            "fabric2": flat("fabric2", (0.2, 0.28, 0.33), rough=0.95),
            "white": flat("bedding", (0.88, 0.87, 0.84), rough=0.95),
            "dark": flat("darkwood", (0.1, 0.08, 0.07), rough=0.4),
            "screen": flat("screen", (0.01, 0.01, 0.012), rough=0.12),
            "marble": flat("marble", (0.9, 0.9, 0.88), rough=0.2),
            "rug": flat("rug", (0.42, 0.33, 0.25), rough=1.0),
            "steel": flat("steel", (0.7, 0.72, 0.74), rough=0.25, metal=1.0),
            "leaf": flat("hedge", (0.09, 0.22, 0.06), rough=0.8),
            "pot": flat("pot", (0.5, 0.25, 0.15), rough=0.7),
            "floor": self.M.pbr("floor_wood"),
            "teal": flat("cushion_teal", (0.08, 0.38, 0.4), rough=0.95),
            "ochre": flat("cushion_ochre", (0.72, 0.5, 0.12), rough=0.95),
            "rug2": flat("rug_border", (0.75, 0.68, 0.56), rough=1.0),
            "curtain": flat("curtain", (0.86, 0.82, 0.74), rough=1.0),
            "art1": flat("art1", (0.1, 0.3, 0.45), rough=0.5),
            "art2": flat("art2", (0.78, 0.4, 0.18), rough=0.5),
            "art3": flat("art3", (0.85, 0.82, 0.7), rough=0.5),
            "brass": flat("brass", (0.8, 0.6, 0.25), rough=0.3, metal=1.0),
            "lamp": flat("lampshade", (1, 0.9, 0.7), rough=0.6, emission=(1.0, 0.8, 0.5), strength=5.0),
            "ceilinglight": flat("downlight", (1, 0.93, 0.8), rough=0.5, emission=(1.0, 0.85, 0.62), strength=8.0),
        }

    def block(self, poly, z0, z1, m):
        for p in polys(poly):
            self.mesh.prism(p, z0, z1, self.mat[m], top=self.mat[m], bottom=self.mat[m])

    def at(self, rect, side, along, length, depth, gap=0.02):
        """A box of length x depth standing against `side` of rect, `along` metres from the side's centre."""
        x0, y0, x1, y1 = rect.bounds
        if side == "S":
            cx, cy, a, b = (x0 + x1) / 2 + along, y0 + gap + depth / 2, length, depth
        elif side == "N":
            cx, cy, a, b = (x0 + x1) / 2 + along, y1 - gap - depth / 2, length, depth
        elif side == "W":
            cx, cy, a, b = x0 + gap + depth / 2, (y0 + y1) / 2 + along, depth, length
        else:
            cx, cy, a, b = x1 - gap - depth / 2, (y0 + y1) / 2 + along, depth, length
        return box(cx - a / 2, cy - b / 2, cx + a / 2, cy + b / 2)

    # ---------------------------------------------------------------- pieces
    def bed(self, rect, side, free):
        w, d = 1.8, 2.05
        along = 0.0
        bed = self.at(rect, side, along, w, d)
        self.block(bed, 0.0, 0.32, "dark")
        self.block(bed.buffer(-0.03, join_style=2), 0.32, 0.55, "white")
        head = self.at(rect, side, along, w + 0.1, 0.08, gap=0.0)
        self.block(head, 0.0, 1.15, "oak")
        for dx in (-0.45, 0.45):                                       # pillows
            self.block(self.at(rect, side, along + dx, 0.62, 0.38, gap=0.12), 0.55, 0.68, "white")
        for sgn in (-1, 1):                                            # bedside tables
            self.block(self.at(rect, side, along + sgn * (w / 2 + 0.3), 0.45, 0.4), 0.0, 0.5, "oak")

    def wardrobe(self, rect, side, length=2.2):
        self.block(self.at(rect, side, 0, min(length, (rect.bounds[2] - rect.bounds[0]) - 0.8 if side in "SN" else (rect.bounds[3] - rect.bounds[1]) - 0.8), 0.6), 0.0, 2.2, "oak")

    def chair(self, x, y, facing, colour="teal"):
        """Armchair at (x, y) facing the unit vector `facing` (axis-aligned)."""
        fx, fy = facing
        self.block(box(x - 0.42, y - 0.42, x + 0.42, y + 0.42), 0.12, 0.42, "fabric")
        self.block(box(x - 0.36, y - 0.36, x + 0.36, y + 0.36), 0.42, 0.55, colour)
        bx, by = x - fx * 0.36, y - fy * 0.36
        w = (0.84, 0.16) if fy == 0 else (0.16, 0.84)
        self.block(box(bx - w[1] / 2 if fy == 0 else bx - 0.42, by - 0.42 if fy == 0 else by - w[1] / 2,
                       bx + w[1] / 2 if fy == 0 else bx + 0.42, by + 0.42 if fy == 0 else by + w[1] / 2), 0.12, 0.95, "fabric")

    def sofa_set(self, rect, side):
        sofa = self.at(rect, side, 0, 2.7, 0.98)
        c = sofa.centroid
        inward = {"S": (0, 1), "N": (0, -1), "W": (1, 0), "E": (-1, 0)}[side]
        self.block(sofa, 0.1, 0.4, "fabric")                                   # base
        self.block(self.at(rect, side, 0, 2.7, 0.2), 0.4, 0.88, "fabric")        # backrest
        for sgn in (-1, 1):                                                      # arms
            self.block(self.at(rect, side, sgn * 1.26, 0.18, 0.98), 0.1, 0.62, "fabric")
        for i, dx in enumerate((-0.78, 0.0, 0.78)):                              # seat and back cushions
            self.block(self.at(rect, side, dx, 0.74, 0.72, gap=0.22), 0.4, 0.52, "fabric")
            self.block(self.at(rect, side, dx, 0.7, 0.16, gap=0.2), 0.52, 0.86, "fabric")
        self.block(self.at(rect, side, -1.0, 0.42, 0.14, gap=0.3), 0.52, 0.9, "teal")         # accent cushions
        self.block(self.at(rect, side, 1.0, 0.42, 0.14, gap=0.3), 0.52, 0.9, "ochre")
        # coffee table in front of the sofa, armchairs either side of it
        tx, ty = c.x + inward[0] * 2.0, c.y + inward[1] * 2.0
        horiz = inward[0] == 0
        tw, td = (1.2, 0.65) if horiz else (0.65, 1.2)
        self.block(box(tx - tw / 2, ty - td / 2, tx + tw / 2, ty + td / 2), 0.36, 0.41, "oak")
        for lx in (-1, 1):
            for ly in (-1, 1):
                self.block(box(tx + lx * (tw / 2 - 0.05) - 0.025, ty + ly * (td / 2 - 0.05) - 0.025,
                               tx + lx * (tw / 2 - 0.05) + 0.025, ty + ly * (td / 2 - 0.05) + 0.025), 0.0, 0.36, "dark")
        self.block(box(tx - 0.12, ty - 0.12, tx + 0.12, ty + 0.12), 0.41, 0.5, "brass")      # bowl
        for sgn in (-1, 1):
            ax, ay = (tx + (sgn * 1.55 if horiz else 0.9 * inward[0] * 0), ty + (0 if horiz else sgn * 1.55))
            self.chair(ax, ay, (0, -sgn) if horiz else (-sgn, 0))
        rw, rd = (3.2, 2.4) if horiz else (2.4, 3.2)
        self.block(box(tx - rw / 2, ty - rd / 2, tx + rw / 2, ty + rd / 2), 0.014, 0.022, "rug2")
        self.block(box(tx - rw / 2 + 0.18, ty - rd / 2 + 0.18, tx + rw / 2 - 0.18, ty + rd / 2 - 0.18), 0.022, 0.028, "rug")
        # side table + lamp
        self.block(self.at(rect, side, 1.9, 0.5, 0.5), 0.0, 0.5, "oak")
        lp = self.at(rect, side, 1.9, 0.3, 0.3)
        self.block(lp.buffer(-0.03), 0.5, 0.55, "brass")
        self.block(Point(lp.centroid.x, lp.centroid.y).buffer(0.14, 8), 0.55, 0.95, "lamp")
        # art above the sofa
        for along, w_, col in ((-0.75, 0.7, "art1"), (0.1, 0.5, "art2"), (0.85, 0.6, "art3")):
            self.block(self.at(rect, side, along, w_, 0.03, gap=0.0), 1.2, 1.2 + w_ * 1.2, col)
        return (tx, ty)

    def tv_wall(self, rect, side):
        self.block(self.at(rect, side, 0, 2.8, 0.04, gap=0.0), 0.0, 2.4, "wood")          # timber panel wall
        self.block(self.at(rect, side, 0, 2.2, 0.38, gap=0.04), 0.1, 0.5, "dark")
        self.block(self.at(rect, side, 0, 1.45, 0.05, gap=0.08), 0.8, 1.5, "screen")
        for along in (-0.95, 0.95):
            self.block(self.at(rect, side, along, 0.25, 0.25, gap=0.08), 0.5, 1.0, "oak")
        self.block(self.at(rect, side, 1.2, 0.3, 0.3, gap=0.08), 0.5, 0.62, "brass")

    def curtains(self, rect, side, length):
        for sgn in (-1, 1):
            self.block(self.at(rect, side, sgn * (length / 2 - 0.3), 0.6, 0.1, gap=0.0), 0.0, 2.55, "curtain")
        self.block(self.at(rect, side, 0, length, 0.04, gap=0.0), 2.5, 2.58, "steel")

    def pendant(self, x, y):
        self.block(Point(x, y).buffer(0.012, 4), self.h - 1.1, self.h - 0.15, "dark")
        self.block(Point(x, y).buffer(0.27, 12), self.h - 1.2, self.h - 1.1, "lamp")
        self.block(Point(x, y).buffer(0.31, 12), self.h - 1.1, self.h - 1.02, "brass")

    def dining(self, rect, center):
        cx, cy = center
        horizontal = (rect.bounds[2] - rect.bounds[0]) >= (rect.bounds[3] - rect.bounds[1])
        tw, td = (1.9, 0.95) if horizontal else (0.95, 1.9)
        self.block(box(cx - tw / 2, cy - td / 2, cx + tw / 2, cy + td / 2), 0.72, 0.76, "oak")
        for sx in (-0.7, 0.0, 0.7):
            for sy in (-1, 1):
                p = (cx + sx, cy + sy * 0.78) if horizontal else (cx + sy * 0.78, cy + sx)
                self.block(box(p[0] - 0.21, p[1] - 0.21, p[0] + 0.21, p[1] + 0.21), 0.0, 0.46, "fabric2")
        self.pendant(cx, cy)
        self.block(Point(cx, cy).buffer(0.1, 8), 0.76, 0.9, "pot")
        for (lx, ly) in ((-tw / 2 + 0.1, -td / 2 + 0.1), (tw / 2 - 0.1, -td / 2 + 0.1), (-tw / 2 + 0.1, td / 2 - 0.1), (tw / 2 - 0.1, td / 2 - 0.1)):
            self.block(box(cx + lx - 0.04, cy + ly - 0.04, cx + lx + 0.04, cy + ly + 0.04), 0.0, 0.72, "dark")

    def kitchen(self, rect, side):
        L = (rect.bounds[2] - rect.bounds[0]) if side in "SN" else (rect.bounds[3] - rect.bounds[1])
        L = min(L - 0.3, 4.2)
        self.block(self.at(rect, side, 0, L, 0.62), 0.0, 0.9, "oak")
        self.block(self.at(rect, side, 0, L, 0.64), 0.9, 0.94, "marble")
        self.block(self.at(rect, side, 0, L, 0.34), 1.55, 2.2, "oak")
        self.block(self.at(rect, side, L / 4, 0.7, 0.1, gap=0.05), 0.94, 1.5, "steel")        # hob splash back / hood stand-in

    def toilet(self, rect, side):
        self.block(self.at(rect, side, -0.35, 0.4, 0.62), 0.0, 0.42, "marble")
        self.block(self.at(rect, side, -0.35, 0.38, 0.2, gap=0.02), 0.42, 0.95, "marble")
        self.block(self.at(rect, side, 0.4, 0.55, 0.42), 0.82, 0.9, "marble")
        self.block(self.at(rect, side, 0.4, 0.3, 0.3), 0.0, 0.82, "marble")

    def plant(self, x, y, s=1.0):
        self.block(Point(x, y).buffer(0.22 * s, 8), 0.0, 0.42 * s, "pot")
        self.block(Point(x, y).buffer(0.02, 4), 0.42 * s, 0.9 * s, "dark")
        for k, (dx, dy, z, r) in enumerate(((0, 0, 1.0, 0.34), (0.2, 0.1, 0.8, 0.26), (-0.18, -0.12, 0.72, 0.24), (0.05, -0.2, 1.25, 0.2))):
            self.block(Point(x + dx * s, y + dy * s).buffer(r * s, 7), z * s * 0.8 + 0.2, (z + r * 1.4) * s * 0.8 + 0.2, "leaf")

    def downlight(self, x, y):
        self.block(Point(x, y).buffer(0.09, 8), self.h - 0.17, self.h - 0.15, "ceilinglight")


# ----------------------------------------------------------------------------- stage
def furnish(plan, cfg, paths, floor):
    h = plan["levels"]["floor_h"]
    t = plan["levels"]["slab_t"]
    fp = from_json(plan["footprint"])
    walls = unary_union([from_json(w) for w in plan["walls"]])
    from shapely.affinity import rotate, translate
    wins = unary_union([translate(rotate(box(-o["length"] / 2, -0.2, o["length"] / 2, 0.2), o["angle"], origin=(0, 0)), o["cx"], o["cy"])
                        for o in plan["openings"]])
    # door gaps are free floor but must not be furnished: treat door lines as 'windows' for the side analysis
    from shapely.geometry import LineString
    doors = unary_union([LineString([d["a"], d["b"]]).buffer(0.25) for d in plan["doors"]])
    barrier = unary_union([wins, doors])
    free = fp.difference(walls)

    # Type A is the front flat: rooms south of the lobby front
    lx = sorted([from_json(w) for w in plan["terrace"]["cabin_walls"]], key=lambda p: p.bounds[0])
    y_front = min(lx[0].bounds[1], lx[1].bounds[1])
    rooms = [r for r in plan["rooms"] if r["y"] < y_front - 0.3]
    F = Furnisher(cfg, paths, plan, h)
    info = {}

    # floor finish: timber in the flat, tile in the wet rooms
    flat_area = fp.intersection(box(-1, -1, 20, y_front)).difference(walls)
    F.block(flat_area.buffer(-0.01), 0.004, 0.012, "floor")      # not coplanar with the slab top (that renders black)
    living_center = None
    for r in rooms:
        rect = room_rect(r, free)
        sides = side_info(rect, walls, barrier)
        # candidate wall sides: solid, no window/door, longest first
        solid = sorted([k for k, v in sides.items() if v["cover"] > 0.55 and not v["window"]], key=lambda k: (-sides[k]["cover"], -sides[k]["len"]))
        if not solid:
            solid = sorted(sides, key=lambda k: -sides[k]["cover"])
        n = r["name"]
        if n == "BED ROOM":
            F.bed(rect, solid[0], free)
            if len(solid) > 1:
                F.wardrobe(rect, solid[1])
            elif solid[0]:
                pass
        elif n == "DRAWING ROOM":
            living_center = rect.centroid
            sofa_side = solid[0]
            F.sofa_set(rect, sofa_side)
            opposite = {"S": "N", "N": "S", "W": "E", "E": "W"}[sofa_side]
            if sides[opposite]["cover"] > 0.5 and not sides[opposite]["window"]:
                F.tv_wall(rect, opposite)
            info["living"] = (sofa_side, rect.bounds)
            F.plant(rect.bounds[0] + 0.45, rect.bounds[1] + 0.45)
            F.plant(rect.bounds[2] - 0.45, rect.bounds[3] - 0.45, 1.2)
            for k, v in sides.items():
                if v["window"] and k != sofa_side:
                    F.curtains(rect, k, min(v["len"], 3.6))
        elif n == "KITCHEN/DINING":
            F.kitchen(rect, solid[0])
            c = rect.centroid
            shift = {"S": (0, 0.9), "N": (0, -0.9), "W": (0.9, 0), "E": (-0.9, 0)}[solid[0]]
            F.dining(rect, (c.x + shift[0], c.y + shift[1]))
        elif n == "TOILET":
            F.toilet(rect, solid[0])
        elif n == "PUJA":
            F.block(F.at(rect, solid[0], 0, 0.8, 0.4), 0.0, 0.9, "marble")
        if n in ("DRAWING ROOM", "KITCHEN/DINING", "BED ROOM"):
            c = rect.centroid
            F.downlight(c.x - 0.8, c.y)
            F.downlight(c.x + 0.8, c.y)
    ob = F.mesh.to_object(bpy.context.scene.collection, f"Interior_TypeA_floor{floor}")
    ob.location = (0, 0, h * floor)
    return ob, living_center, info


def render(floor=3, quick=False):
    cfg = load_config()
    plan = json.loads((BUILD / "plan.json").read_text())
    paths = assets.fetch_all(cfg)
    bpy.ops.wm.open_mainfile(filepath=str(OUT / "vibgyor.blend"))
    h = plan["levels"]["floor_h"]
    ob, center, info = furnish(plan, cfg, paths, floor)
    if center is None:
        raise SystemExit("no DRAWING ROOM label in the front block")

    # clean, bright paint for the interior (the shell's textured beige plaster is meant for the exterior look)
    m = bpy.data.materials.get("plaster")
    if m:
        b = m.node_tree.nodes["Principled BSDF"]
        for l in list(m.node_tree.links):
            if l.to_socket == b.inputs["Base Color"]:
                m.node_tree.links.remove(l)
        b.inputs["Base Color"].default_value = (0.86, 0.84, 0.8, 1)
    for o in bpy.data.objects:                                     # the exterior glow cards would float in the rooms
        if o.name.endswith("_interior"):
            o.hide_render = True

    F = Frame(plan)
    setup_cycles(cfg, 40 if quick else 90, quick, 1.0)
    sc0 = bpy.context.scene
    sc0.view_settings.exposure = -0.35
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = (1024, 512) if quick else (4096, 2048)
    sc.cycles.use_adaptive_sampling = True
    sc.cycles.sample_clamp_indirect = 6
    import math as _m
    set_sky(paths["hdri_day"], _m.atan2(F.n[1], F.n[0]) + _m.radians(-38), 1.0)
    # soft invisible fill lights in the room (stand-in for the bounce a furnished room has)
    for i, (dx, dy) in enumerate(((-1.6, 0.3), (1.6, -0.3), (0, 2.0), (0, -2.0))):
        L = bpy.data.lights.new(f"room{i}", "AREA")
        L.energy, L.size, L.color = 30, 2.0, (1.0, 0.93, 0.82)
        L.spread = _m.radians(100)
        lo = bpy.data.objects.new(f"room{i}", L)
        sc.collection.objects.link(lo)
        lo.location = (center.x + dx, center.y + dy, h * (floor + 1) - 0.9)
        lo.visible_camera = False
        lo.rotation_euler = (_m.pi, 0, 0)
    cam = bpy.data.cameras.new("pano")
    cam.type = "PANO"
    cam.panorama_type = "EQUIRECTANGULAR"
    cam.clip_start = 0.05
    co = bpy.data.objects.new("pano", cam)
    sc.collection.objects.link(co)
    co.location = (center.x, center.y, h * floor + 1.45)
    # image centre looks towards the sofa wall
    side, b = info["living"]
    yaw = {"S": _m.pi, "N": 0, "W": _m.pi / 2, "E": -_m.pi / 2}[side]
    co.rotation_euler = (_m.pi / 2, 0, yaw)
    sc.camera = co
    out = (BUILD / "preview" if quick else OUT / "renders")
    out.mkdir(parents=True, exist_ok=True)
    sc.render.filepath = str(out / "05_typeA_drawing_room_360.png")
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"[interior] {sc.render.filepath} {sc.render.resolution_x}x{sc.render.resolution_y} in {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=int, default=3)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    render(a.floor, a.quick)


if __name__ == "__main__":
    main()
