"""Debug plot of build/plan.json (what the extractor understood) -> outputs/extraction_preview.png"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MP

from .common import BUILD, OUT


def draw_poly(ax, p, **kw):
    ax.add_patch(MP(p["outer"], closed=True, **kw))
    for h in p["holes"]:
        ax.add_patch(MP(h, closed=True, fc="white", ec="none"))


def main():
    plan = json.loads((BUILD / "plan.json").read_text())
    fig, axs = plt.subplots(1, 3, figsize=(24, 14))
    ax = axs[0]
    s = plan["site"]
    draw_poly(ax, s["plot"], fc="#dfe9cf", ec="k")
    for r in s["roads"]:
        draw_poly(ax, r, fc="#888", ec="none", alpha=.7)
    for x, y in s["trees"]:
        ax.add_patch(plt.Circle((x, y), 1.6, fc="#2d6a2d", alpha=.7))
    draw_poly(ax, plan["slab"], fc="#ccc", ec="b")
    for a, b in s["front_edges"]:
        ax.plot(*zip(a, b), "r", lw=3)
    ax.set_title("site: plot, roads, trees, tower (red = road-facing edges)")
    ax = axs[1]
    draw_poly(ax, plan["slab"], fc="#eee", ec="#999")
    for w in plan["walls"]:
        draw_poly(ax, w, fc="k", ec="k")
    for o in plan["openings"]:
        import math
        dx, dy = math.cos(math.radians(o["angle"])) * o["length"] / 2, math.sin(math.radians(o["angle"])) * o["length"] / 2
        ax.plot([o["cx"] - dx, o["cx"] + dx], [o["cy"] - dy, o["cy"] + dy], "c" if o["kind"] != "SD" else "m", lw=5)
    for d in plan["doors"]:
        ax.plot(*zip(d["a"], d["b"]), "g", lw=3)
    for e in plan["open_edges"]:
        ax.plot(*zip(*e), "r--", lw=2)
    for c in plan["columns"]:
        draw_poly(ax, c, fc="orange", ec="none")
    ax.set_title("typical floor: walls (black), windows (cyan), sliding doors (magenta), door lintels (green), open edges (red)")
    ax = axs[2]
    draw_poly(ax, plan["footprint"], fc="#eee", ec="#999")
    for w in plan["ground"]["walls"]:
        draw_poly(ax, w, fc="k", ec="k")
    for c in plan["ground"]["columns"]:
        draw_poly(ax, c, fc="orange", ec="none")
    for w in plan["terrace"]["parapet"]:
        draw_poly(ax, w, fc="none", ec="purple", lw=2)
    for w in plan["terrace"]["cabin_walls"]:
        draw_poly(ax, w, fc="none", ec="brown", lw=2)
    ax.set_title("ground (stilt) walls+columns; terrace parapet (purple) + cabin (brown)")
    for a in axs:
        a.set_aspect("equal"); a.autoscale_view()
    OUT.mkdir(exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT / "extraction_preview.png", dpi=60)


if __name__ == "__main__":
    main()
