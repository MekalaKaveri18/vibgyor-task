"""Tests for the extraction step.

The first group is synthetic (no drawing needed). The second runs the real extractor on the architect's
DXF and checks it against numbers printed on the drawing itself; it is skipped if input/ is absent.
"""
import math
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import extract as ex  # noqa: E402
from pipeline.common import ROOT, load_config  # noqa: E402


# ----------------------------------------------------------------------------- synthetic
def test_two_faces_of_a_wall_become_one_solid():
    # a 0.23 m thick wall drawn as two parallel lines
    lines = [LineString([(0, 0), (5, 0)]), LineString([(0, 0.23), (5, 0.23)])]
    walls = ex.solid_walls(lines, 0.13, 0.03)
    assert len(walls) == 1
    w = walls[0]
    assert w.bounds[3] - w.bounds[1] == pytest.approx(0.23, abs=0.03)
    assert w.area == pytest.approx(5 * 0.23, rel=0.1)


def test_isolated_line_is_not_a_wall():
    assert ex.solid_walls([LineString([(0, 0), (4, 0)])], 0.13, 0.03) == []


def test_rooms_stay_open():
    # two walls 3 m apart (a room) must not be merged into a slab
    lines = [LineString([(0, 0), (5, 0)]), LineString([(0, 0.23), (5, 0.23)]),
             LineString([(0, 3), (5, 3)]), LineString([(0, 3.23), (5, 3.23)])]
    walls = ex.solid_walls(lines, 0.13, 0.03)
    assert len(walls) == 2
    assert sum(w.area for w in walls) < 2.5


def test_a_gap_in_a_wall_is_kept_as_an_opening():
    # a 1 m door gap: both wall pieces survive and the gap stays empty
    lines = [LineString([(0, 0), (2, 0)]), LineString([(0, 0.23), (2, 0.23)]),
             LineString([(3, 0), (5, 0)]), LineString([(3, 0.23), (5, 0.23)])]
    walls = ex.solid_walls(lines, 0.13, 0.03)
    assert len(walls) == 2
    assert not any(w.contains(Point(2.5, 0.1)) for w in walls)


def test_poly_json_roundtrip_keeps_holes():
    from shapely.geometry import Polygon
    p = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (2, 1), (2, 2), (1, 2)]])
    j = ex.poly_json(p)
    assert len(j["outer"]) == 4 and len(j["holes"]) == 1


# ----------------------------------------------------------------------------- real drawing
CFG = load_config()
DXF = ROOT / CFG["input"]["dxf"]
needs_dxf = pytest.mark.skipif(not DXF.exists(), reason="input/ drawing not present (confidential, not in the repo)")


@pytest.fixture(scope="module")
def plan():
    return ex.extract(CFG)


@needs_dxf
def test_every_region_title_is_found():
    dr = ex.Drawing(DXF)
    for name, r in CFG["regions"].items():
        ex.region_check(dr, name, r)


@needs_dxf
def test_levels_come_from_the_section(plan):
    lv = plan["levels"]
    assert lv["source"] == "section annotations"
    assert lv["floor_h"] == pytest.approx(3.05, abs=0.01)       # '0.15 MT. TH. R.C.C. SLAB' labels every 3.05 m
    assert lv["lift_room_top"] > lv["stair_cabin_top"] > lv["floor_h"] * 8


@needs_dxf
def test_footprint_matches_the_printed_dimensions(plan):
    from shapely.geometry import Polygon
    b = Polygon(plan["footprint"]["outer"]).bounds
    assert b[2] - b[0] == pytest.approx(13.12, abs=0.05)       # dimension on the plan: 13.12
    assert b[3] - b[1] == pytest.approx(38.73, abs=0.05)       # dimension on the plan: 38.73
    assert b[0] == pytest.approx(0, abs=1e-6) and b[1] == pytest.approx(0, abs=1e-6)


@needs_dxf
def test_openings_follow_the_schedule(plan):
    kinds = {o["kind"] for o in plan["openings"]}
    assert {"W", "W1", "W2", "W3", "W4", "SD"} <= kinds
    for o in plan["openings"]:
        assert o["head"] <= 2.1 + 1e-6 and o["sill"] >= 0
        if o["kind"] == "SD":
            assert o["sill"] == 0
        if o["kind"].startswith("W"):
            assert o["head"] - o["sill"] == pytest.approx(1.5, abs=0.01)   # schedule: every window is x 1.50


@needs_dxf
def test_wall_solids_are_wall_thickness(plan):
    from shapely.geometry import Polygon
    thick = []
    for w in plan["walls"]:
        p = Polygon(w["outer"], w["holes"])
        if p.length > 0:
            thick.append(2 * p.area / p.length)                  # mean width of a thin solid
    assert thick and max(thick) < 0.6 and min(thick) > 0.05


@needs_dxf
def test_units_and_types(plan):
    u = {x["type"]: x for x in plan["units"]}
    assert u["A"]["carpet_sqm"] == pytest.approx(160.05) and u["A"]["position"] == "front"
    assert u["B"]["carpet_sqm"] == pytest.approx(143.18) and u["B"]["position"] == "rear"


@needs_dxf
def test_site_is_registered_to_the_tower(plan):
    from shapely.geometry import Polygon
    plot = Polygon(plan["site"]["plot"]["outer"])
    fp = Polygon(plan["footprint"]["outer"])
    assert plot.contains(fp.buffer(-0.01))                      # the tower sits inside its plot
    assert len(plan["site"]["trees"]) == 25
    assert plan["site"]["front_edges"]
    # the road-facing edges run along the road, i.e. they are not the east/west boundary
    for a, b in plan["site"]["front_edges"]:
        assert abs(b[1] - a[1]) < abs(b[0] - a[0]) * 1.0
