"""Stage 1 - extract: architect's DXF -> clean 2D geometry (build/plan.json).

Everything is in metres in a 'world' frame whose origin is the south-west corner of the tower
footprint (x east, y north, plan orientation as drawn). No geometry is typed in by hand:
walls, openings, columns, parapets, roads, trees and levels are all read from the drawing.
"""
import json
import math
import re

import ezdxf
from ezdxf.path import make_path
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from .common import BUILD, ROOT, load_config


# ----------------------------------------------------------------------------- reading entities
def plain(e):
    t = e.dxf.text if e.dxftype() == "TEXT" else e.plain_text()
    return re.sub(r"%%[uUoO]", "", t).strip()


class Drawing:
    def __init__(self, path):
        self.doc = ezdxf.readfile(path)
        self.msp = self.doc.modelspace()
        self.texts = []
        for e in self.msp.query("TEXT MTEXT"):
            p = e.dxf.insert
            self.texts.append((plain(e), p.x, p.y, e.dxf.layer))

    def geoms(self, layers, bbox, kinds="LINE LWPOLYLINE ARC CIRCLE POLYLINE"):
        """Yield (shapely geometry, closed?) for entities of `layers` lying fully inside bbox."""
        x0, y0, x1, y1 = bbox
        for e in self.msp.query(kinds):
            if e.dxf.layer not in layers:
                continue
            try:
                pts = [(v.x, v.y) for v in make_path(e).flattening(0.02)]
            except Exception:
                continue
            if len(pts) < 2:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if min(xs) < x0 or max(xs) > x1 or min(ys) < y0 or max(ys) > y1:
                continue
            closed = e.dxftype() == "CIRCLE" or bool(getattr(e, "closed", False)) or (
                len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-3)
            yield (Polygon(pts) if closed and len(pts) > 2 else LineString(pts)), closed

    def lines(self, layers, bbox):
        out = []
        for g, closed in self.geoms(layers, bbox):
            out.append(g.exterior if closed and g.geom_type == "Polygon" else g)
        return out

    def segments(self, layers, bbox):
        """Straight segments ((x0,y0),(x1,y1)) of LINE/LWPOLYLINE entities, polylines taken apart."""
        x0, y0, x1, y1 = bbox
        for g, _ in self.geoms(layers, bbox, "LINE LWPOLYLINE"):
            c = list(g.exterior.coords) if g.geom_type == "Polygon" else list(g.coords)
            yield from zip(c, c[1:])

    def texts_in(self, bbox, layers=None):
        x0, y0, x1, y1 = bbox
        return [t for t in self.texts if x0 <= t[1] <= x1 and y0 <= t[2] <= y1 and (layers is None or t[3] in layers)]


# ----------------------------------------------------------------------------- geometry helpers
def polys_of(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [q for p in getattr(g, "geoms", []) for q in polys_of(p)]


def solid_walls(lines, radius, min_area, min_thickness=0.06):
    """Merge the two faces of each wall into one solid polygon (morphological closing).
    Walls are drawn as parallel lines <= 2*radius apart; isolated single lines (no second face) are
    dropped by the thickness filter - a lone line would otherwise become a 2 cm sliver."""
    if not lines:
        return []
    u = unary_union([l.buffer(0.01) for l in lines])
    closed = u.buffer(radius, join_style=2).buffer(-radius, join_style=2)
    return [p for p in polys_of(closed) if p.area >= min_area and 2 * p.area / p.length >= min_thickness]


def poly_json(p):
    r = lambda c: [[round(x, 4), round(y, 4)] for x, y in c]
    return {"outer": r(p.exterior.coords[:-1]), "holes": [r(h.coords[:-1]) for h in p.interiors]}


def region_check(dr, name, r):
    hits = [t for t in dr.texts_in(r["bbox"]) if r["title"].lower() in t[0].lower()]
    if not hits:
        raise SystemExit(f"region '{name}': title '{r['title']}' not found in bbox {r['bbox']} - fix [regions] in config.toml")
    return hits[0]


def find_footprint(dr, bbox, layers, want=(13.12, 38.73), tol=0.6):
    """The tower footprint is the closed 'built up' polyline with the expected overall size."""
    best = None
    for g, closed in dr.geoms(layers, bbox):
        if not closed or g.geom_type != "Polygon":
            continue
        x0, y0, x1, y1 = g.bounds
        d = abs((x1 - x0) - want[0]) + abs((y1 - y0) - want[1])
        if d < tol and (best is None or d < best[0]):
            best = (d, g)
    return best[1] if best else None


def parse_schedule(dr):
    """'W1 = 2.15 x 1.50' rows of the schedule of openings -> {label: (w, h)}"""
    sched = {}
    for t, *_ in dr.texts:
        m = re.match(r"^([A-Z]+\d?)\s*=\s*(\d+\.\d+)\s*x\s*(\d+\.\d+)$", t)
        if m:
            sched[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return sched


def derive_levels(dr, cfg, R):
    """Floor-to-floor height from the section's slab annotations, ground level from the elevation."""
    g = cfg["geometry"]
    slabs = sorted(y for t, x, y, l in dr.texts_in(R["section"]["bbox"]) if "R.C.C. SLAB" in t.upper())
    gl_lines = [ln.bounds[1] for ln in dr.lines(cfg["layers"]["ground_line"], R["elevation"]["bbox"]) if ln.bounds[2] - ln.bounds[0] > 5]
    gl = min(gl_lines) if gl_lines else None
    out = {"slab_t": g["slab_thickness"], "source": "section annotations"}
    if gl is not None and len(slabs) >= 8:
        lv = []
        for y in slabs:                      # collapse repeated labels of the same slab
            if not lv or y - lv[-1] > 1.0:
                lv.append(y)
        diffs = sorted(b - a for a, b in zip(lv, lv[1:]))
        out.update(floor_h=round(diffs[len(diffs) // 2], 3), gl_y=gl, slab_labels=[round(y - gl, 2) for y in lv])
    else:
        out.update(floor_h=g["fallback_floor_height"], gl_y=gl or 0.0, source="config fallback")
    # rooftop: horizontal RCC roof slabs in the section above the terrace -> stair cabin and lift machine room tops
    terrace = out["floor_h"] * 8
    tops = sorted({round(a[1] - out["gl_y"], 2) for a, b in dr.segments(["a-RCC-HM"], R["section"]["bbox"])
                   if abs(a[1] - b[1]) < 1e-3 and abs(a[0] - b[0]) > 2.0 and a[1] - out["gl_y"] > terrace + 1.5})
    clusters = []
    for t in tops:
        if clusters and t - clusters[-1][-1] < 0.3:
            clusters[-1].append(t)
        else:
            clusters.append([t])
    out["rooftop_levels"] = [max(c) for c in clusters]
    out["stair_cabin_top"] = out["rooftop_levels"][0] if out["rooftop_levels"] else terrace + 2.4
    out["lift_room_top"] = out["rooftop_levels"][-1] if out["rooftop_levels"] else terrace + 2.4
    return out


def extract(cfg=None):
    cfg = cfg or load_config()
    R, L, G = cfg["regions"], cfg["layers"], cfg["geometry"]
    dr = Drawing(ROOT / cfg["input"]["dxf"])
    for n, r in R.items():
        region_check(dr, n, r)

    levels = derive_levels(dr, cfg, R)
    sched = parse_schedule(dr)

    # ---- registration: world origin = SW corner of the tower footprint on the typical plan
    fp = {n: find_footprint(dr, R[n]["bbox"], L["footprint"]) for n in ("ground", "typical", "site_layout")}
    if fp["typical"] is None:
        raise SystemExit("could not find the tower footprint polyline in the typical plan")
    origin = {n: (g.bounds[0], g.bounds[1]) for n, g in fp.items() if g is not None}
    # the terrace plan has no built-up polyline: its outer parapet outline is the same shape
    terr_par = unary_union(dr.lines(L["parapet"], R["terrace"]["bbox"]))
    origin["terrace"] = (terr_par.bounds[0], terr_par.bounds[1])

    def to_world(name, g):
        return translate(g, -origin[name][0], -origin[name][1])

    fp_w = to_world("typical", fp["typical"])
    ox, oy = origin["typical"]
    plan = {"levels": levels, "schedule": sched, "origin_dxf": origin["typical"], "footprint": poly_json(fp_w)}

    # ---- typical floor
    bb = R["typical"]["bbox"]
    walls = [to_world("typical", p) for p in
             solid_walls(dr.lines(L["walls"] + L["rcc"], bb), G["wall_close_radius"], G["min_wall_area"])]
    wall_u = unary_union(walls)

    # window / sliding-door frames, classified by the nearest door&window label, sized from the schedule
    labels = [(t, x, y) for t, x, y, l in dr.texts_in(bb, L["door_labels"])]
    openings = []
    for g, closed in dr.geoms(L["openings"], bb):
        if not closed:
            continue
        rect = g.minimum_rotated_rectangle
        pts = list(rect.exterior.coords)[:4]
        e1, e2 = math.dist(pts[0], pts[1]), math.dist(pts[1], pts[2])
        a, b = (pts[0], pts[1]) if e1 >= e2 else (pts[1], pts[2])
        c = rect.centroid
        lab = min(labels, key=lambda t: math.hypot(t[1] - c.x, t[2] - c.y)) if labels else None
        name = lab[0] if lab and math.hypot(lab[1] - c.x, lab[2] - c.y) < 2.5 else "W"
        w, h = sched.get(name, (max(e1, e2), 1.5))
        if name == "SD":
            sill, head = 0.0, G["door_head"]
        elif name == "V":
            sill, head = G["vent_sill"], G["vent_sill"] + h
        else:
            head = G["door_head"]
            sill = head - h
        openings.append({"kind": name, "cx": round(c.x - ox, 4), "cy": round(c.y - oy, 4),
                         "length": round(max(e1, e2), 3), "thick": round(max(min(e1, e2), 0.12), 3),
                         "angle": round(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])), 3),
                         "sill": round(sill, 3), "head": round(head, 3)})
    plan["openings"] = openings

    # door gaps: an A_DOOR line closing the gap between two wall ends gets a lintel above the door head
    doors = []
    for ln in dr.lines(L["doors"], bb):
        if ln.geom_type != "LineString" or ln.length < 0.5 or ln.length > 4.0:
            continue
        p0 = Point(ln.coords[0][0] - ox, ln.coords[0][1] - oy)
        p1 = Point(ln.coords[-1][0] - ox, ln.coords[-1][1] - oy)
        if wall_u.distance(p0) < 0.3 and wall_u.distance(p1) < 0.3:
            doors.append({"a": [round(p0.x, 4), round(p0.y, 4)], "b": [round(p1.x, 4), round(p1.y, 4)], "head": G["door_head"]})
    plan["doors"] = doors

    cols = [to_world("typical", g) for g, c in dr.geoms(L["columns"], bb) if c and g.geom_type == "Polygon"]
    plan["columns"] = [poly_json(c) for c in cols]

    # slab outline = footprint + 0.6 m cantilever where the plan draws the 'proj. @ L.L.' lines
    proj = dr.lines(["A_PROJ."], bb)
    if proj:
        near = unary_union([translate(l, -ox, -oy) for l in proj]).buffer(0.5)
        extra = fp_w.buffer(G["cantilever"], join_style=2).intersection(near)
        slab = unary_union([fp_w, extra]).buffer(0.02).buffer(-0.02)
    else:
        slab = fp_w
    plan["slab"] = poly_json(max(polys_of(slab), key=lambda p: p.area))

    # open perimeter = footprint edge not backed by a wall or opening -> balcony parapet line
    backed = unary_union([wall_u.buffer(0.25)] + [Point(o["cx"], o["cy"]).buffer(o["length"] / 2 + 0.2) for o in openings])
    open_edges = []
    for rg in [fp_w.exterior] + list(fp_w.interiors):
        pieces = rg.difference(backed)
        for seg in (pieces.geoms if hasattr(pieces, "geoms") else [pieces]):
            if seg.length > 0.4:
                open_edges.append([[round(x, 4), round(y, 4)] for x, y in seg.coords])
    plan["open_edges"] = open_edges
    plan["walls"] = [poly_json(p) for p in walls]

    # ---- ground (stilt) floor
    bb = R["ground"]["bbox"]
    gw = [to_world("ground", p) for p in
          solid_walls(dr.lines(L["walls"] + L["rcc"], bb), G["wall_close_radius"], G["min_wall_area"])]
    gcols = [to_world("ground", g) for g, c in dr.geoms(L["columns"], bb) if c and g.geom_type == "Polygon"]
    plan["ground"] = {"walls": [poly_json(p) for p in gw], "columns": [poly_json(c) for c in gcols]}

    # ---- terrace
    bb = R["terrace"]["bbox"]
    par = [to_world("terrace", p) for p in solid_walls(dr.lines(L["parapet"], bb), G["wall_close_radius"], G["min_wall_area"])]
    cab = [to_world("terrace", p) for p in
           solid_walls(dr.lines(L["cabin"] + L["rcc"], bb), G["wall_close_radius"], G["min_wall_area"])]
    plan["terrace"] = {"parapet": [poly_json(p) for p in par], "cabin_walls": [poly_json(p) for p in cab]}
    if cab:
        plan["terrace"]["cabin_hull"] = poly_json(unary_union(cab).convex_hull)

    # ---- units: text 'NNN TO MMM  CARPET AREA = xx SQ. MTS.'
    units = []
    for t, x, y, l in dr.texts_in(R["typical"]["bbox"]):
        m = re.search(r"(\d{3}) TO (\d{3}).*?CARPET AREA.*?=\s*([\d.]+)", t.replace("\n", " "), re.S)
        if m:
            units.append({"first": m.group(1), "last": m.group(2), "carpet_sqm": float(m.group(3)), "y": round(y - oy, 3)})
    units.sort(key=lambda u: u["y"])         # low y = towards the 18 m road = front
    other = "B" if cfg["floors"]["front_type"] == "A" else "A"
    for i, u in enumerate(units):
        u["type"] = cfg["floors"]["front_type"] if i == 0 else other
        u["position"] = "front" if i == 0 else "rear"
    plan["units"] = units

    # ---- rooms: labels like 'BED ROOM 4.56 x 3.20' sit at the centre of each room
    rooms = []
    for t, x, y, l in dr.texts_in(R["typical"]["bbox"]):
        m = re.match(r"^([A-Z][A-Z/ ]*?)\s*(\d+\.\d+)\s*x\s*(\d+\.\d+)$", re.sub(r"\s+", " ", t).strip())
        if m and "LIFT" not in t:
            rooms.append({"name": m.group(1).strip(), "w": float(m.group(2)), "d": float(m.group(3)),
                          "x": round(x - ox, 3), "y": round(y - oy, 3)})
    plan["rooms"] = rooms

    # ---- site: plot, roads, trees from the environmental drawing; tower located via the layout copy
    sb, eb = R["site_layout"]["bbox"], R["site_env"]["bbox"]
    plot_l = unary_union(dr.lines(L["plot"], sb))
    plot_e = unary_union(dr.lines(L["plot"], eb))
    shift = (plot_e.bounds[0] - plot_l.bounds[0], plot_e.bounds[1] - plot_l.bounds[1])
    tx, ty = origin["site_layout"][0] + shift[0], origin["site_layout"][1] + shift[1]

    def site_w(g):
        return translate(g, -tx, -ty)

    cands = list(polygonize(plot_e))
    plot_poly = max(cands, key=lambda p: p.area) if cands else plot_e.convex_hull
    plot_w = site_w(plot_poly)
    roads = []
    for g, closed in dr.geoms(L["road"], eb):
        pg = g if g.geom_type == "Polygon" else (Polygon(g.coords) if len(g.coords) > 2 else None)
        if pg is not None and pg.area > 3:
            roads.append(poly_json(site_w(pg.buffer(0))))
    trees = []
    for e in dr.msp.query("INSERT"):
        if e.dxf.name == cfg["site"]["tree_block"]:
            p = e.dxf.insert
            if eb[0] <= p.x <= eb[2] and eb[1] <= p.y <= eb[3]:
                q = site_w(Point(p.x, p.y))
                trees.append([round(q.x, 3), round(q.y, 3)])
    road_u = max((Polygon(r["outer"]) for r in roads), key=lambda p: p.area) if roads else None   # the 18 m road is the biggest
    front = []
    if road_u is not None:
        # plot edges that run along the 18 m road: close to it and parallel to its long axis, oriented west -> east
        rc = list(road_u.minimum_rotated_rectangle.exterior.coords)[:4]
        e0, e1 = (rc[1][0] - rc[0][0], rc[1][1] - rc[0][1]), (rc[2][0] - rc[1][0], rc[2][1] - rc[1][1])
        ax = e0 if math.hypot(*e0) >= math.hypot(*e1) else e1
        an = math.hypot(*ax)
        ax = (ax[0] / an, ax[1] / an)
        pc = list(plot_w.exterior.coords)
        for a_, b_ in zip(pc, pc[1:]):
            seg = LineString([a_, b_])
            if seg.length < 1.0 or seg.centroid.distance(road_u) > 1.0:
                continue
            d = ((b_[0] - a_[0]) / seg.length, (b_[1] - a_[1]) / seg.length)
            if abs(d[0] * ax[0] + d[1] * ax[1]) > 0.96:
                if a_[0] > b_[0]:
                    a_, b_ = b_, a_
                front.append([[round(a_[0], 3), round(a_[1], 3)], [round(b_[0], 3), round(b_[1], 3)]])
    plan["site"] = {"plot": poly_json(plot_w), "roads": roads, "trees": trees, "front_edges": front,
                    "layout_shift": [round(shift[0], 3), round(shift[1], 3)]}

    BUILD.mkdir(exist_ok=True)
    out = BUILD / "plan.json"
    out.write_text(json.dumps(plan))
    print(f"[extract] levels: floor_h={levels['floor_h']} m ({levels['source']}), stair cabin top {levels['stair_cabin_top']} m, lift room top {levels['lift_room_top']} m")
    print(f"[extract] typical: {len(walls)} wall solids, {len(openings)} openings {sorted({o['kind'] for o in openings})}, "
          f"{len(doors)} door lintels, {len(cols)} columns, {len(open_edges)} open edges")
    print(f"[extract] ground: {len(gw)} walls, {len(gcols)} columns | terrace: {len(par)} parapet, {len(cab)} cabin walls")
    print(f"[extract] rooms: {len(rooms)} labelled ({sorted({r['name'] for r in rooms})})")
    print(f"[extract] units: {[(u['first'], u['type'], u['carpet_sqm']) for u in units]}")
    print(f"[extract] site: plot {plot_w.area:.0f} m2, {len(roads)} road polys, {len(trees)} trees, {len(front)} front edges -> {out}")
    return plan


if __name__ == "__main__":
    extract()
