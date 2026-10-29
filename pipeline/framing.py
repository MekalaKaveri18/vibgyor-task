"""Reference points and directions derived from the extracted site (no Blender needed)."""
import math

import numpy as np
from shapely.geometry import Point, Polygon


class Frame:
    """Reference points derived from the site, so cameras follow the building, not fixed numbers."""

    def __init__(self, plan):
        S = plan["site"]
        self.fp = Polygon(plan["footprint"]["outer"])
        self.plot = Polygon(S["plot"]["outer"])
        self.h = plan["levels"]["floor_h"]
        self.top = self.h * 8
        pts = [p for e in S["front_edges"] for p in e]         # the road-facing plot line, west end -> east end
        a, b = min(pts), max(pts)
        ux, uy = (b[0] - a[0]), (b[1] - a[1])
        n = math.hypot(ux, uy)
        self.u = np.array([ux / n, uy / n])
        road = Polygon(max(S["roads"], key=lambda r: Polygon(r["outer"]).area)["outer"])
        mid = np.array([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])
        cand = np.array([self.u[1], -self.u[0]])
        if road.centroid.distance(Point(*(mid + cand * 10))) > \
           road.centroid.distance(Point(*(mid - cand * 10))):
            cand = -cand
        self.n = cand                              # unit vector from the plot towards the 18 m road
        self.front_mid = mid
        self.edge = (np.array(a), np.array(b))
        self.centre = np.array([self.fp.centroid.x, self.fp.centroid.y])
        self.road = road

    def pt(self, along=0.0, out=0.0, z=0.0):
        p = self.front_mid + self.u * along + self.n * out
        return (float(p[0]), float(p[1]), z)


