"""Determinism + sourcing tests for costs, damage, storm and the CityEditor (owned by damage/costs agent).
Run: .venv/bin/python -m pytest -q sim/tests/test_determinism.py
"""
import hashlib
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from sim import costs, damage, storm  # noqa: E402
from sim.city_api import CityEditor, exec_plan  # noqa: E402

PLAN = '''
def apply(city):
    p = city.low_points(1)[0]
    sq = [[p["x"]-150, p["y"]-150], [p["x"]+150, p["y"]-150], [p["x"]+150, p["y"]+150], [p["x"]-150, p["y"]+150]]
    city.wall([[p["x"]-150, p["y"]+100], [p["x"]+150, p["y"]+100]], height_m=1.5)
    city.storage(p["x"], p["y"], volume_m3=10000, intake_m3s=2)
    city.permeable(sq)
    city.bioswale([[p["x"]-100, p["y"]], [p["x"]+100, p["y"]]], width_m=2)
    city.prune(city.trees_in(sq)[:10])
    city.clear_drains(sq)
    city.tow_cars(sq)
    city.action("barricades", "close flooded underpass", effects={"road_closed": [[p["x"], p["y"]], [p["x"]+40, p["y"]]]},
                cost_items=[("labour_crew_hr", 24)])
    city.z[5:8, 5:8] += 0.2
'''


def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode()).hexdigest()


def test_costs_deterministic():
    a = costs.estimate(PLAN)
    b = costs.estimate(PLAN)
    assert a.get("error") is None, a.get("error")
    a.pop("log"); b.pop("log")
    assert _h(a) == _h(b)
    assert a["total_usd"] > 0
    for line in a["items"]:
        assert line["source"] and line["url"], line


def test_unknown_cost_item_rejected():
    r = costs.estimate('def apply(city):\n    city.action("x", "y", effects={}, cost_items=[("gold_plating", 1)])\n')
    assert r["error"] and "unknown cost items" in r["error"]


def test_damage_deterministic():
    ed = CityEditor()
    rng = np.random.default_rng(12345)
    d = np.zeros((ed.ny, ed.nx), np.float32)
    d[300:600, 300:600] = rng.random((300, 300)).astype(np.float32) * 0.8
    d[ed.bldg_id >= 0] = 0
    grid = {"nx": ed.nx, "ny": ed.ny, "dx": ed.dx, "x0": ed.x0, "y0": ed.y0, "bldg_id": ed.bldg_id}
    wr = {"max_depth": d, "flood_duration_s": (d > 0.1) * 3600.0}
    trees = [{"id": t["id"], "fail_t": 1000.0, "mode": "uproot", "fall_dir_deg": 45.0} for t in ed.trees[:500:7]]
    L1 = damage.assess(grid, ed.buildings, wr, tree_result=trees, wind_result={"facade_peak_pa": {1: 3000.0}}, plan=ed)
    L2 = damage.assess(grid, ed.buildings, wr, tree_result=trees, wind_result={"facade_peak_pa": {1: 3000.0}}, plan=ed)
    assert _h(L1) == _h(L2)
    assert L1["total_usd"] > 0 and L1["by_cause"]["flood_structure"] > 0
    assert all(l["source"] for l in L1["lines"])


def test_storms_deterministic_and_sourced():
    for n in ("dec31_2022", "elnino_ar_2026"):
        a, b = storm.load_storm(n), storm.load_storm(n)
        assert np.array_equal(a.rain_mm_h, b.rain_mm_h) and np.array_equal(a.tide_m, b.tide_m)
        assert len(a.t) == len(a.rain_mm_h) == len(a.gust_ms) == len(a.tide_m)
        assert a.sources
    s = storm.load_storm("dec31_2022")
    assert 80 < s.total_rain_mm() < 95          # SFOC1 07:45-13:45 = 3.45 in
    e = storm.load_storm("elnino_ar_2026")
    assert e.total_rain_mm() > s.total_rain_mm() and e.tide_m.max() > s.tide_m.max()


def test_reference_tables_sourced():
    uc = json.load(open(os.path.join(ROOT, "data", "reference", "unit_costs.json")))
    for k, v in uc.items():
        if k.startswith("_"):
            continue
        assert v.get("url") and v.get("retrieved") and v.get("usd", 0) > 0, k
    df = json.load(open(os.path.join(ROOT, "data", "reference", "damage_functions.json")))
    assert df["flood"]["url"] and df["vehicles"]["url"]
