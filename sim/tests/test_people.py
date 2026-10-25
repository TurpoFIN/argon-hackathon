"""Tests for sim/people.py + sim/response.py (owned by the people/response agent).
Run: .venv/bin/python -m pytest -q sim/tests/test_people.py
"""
import hashlib
import json
import os
import sys
import time

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from sim.people import run_people, example_plan, write_people, hazard_rating  # noqa: E402
from sim.response import ResponsePlan, ResponseError, load_costs  # noqa: E402
from sim.storm import load_storm  # noqa: E402

STORM = load_storm("elnino_ar_2026")


def _h(res):
    h = hashlib.sha256()
    h.update(res.frames.tobytes())
    s = dict(res.summary)
    s.pop("runtime_s", None)
    h.update(json.dumps(s, sort_keys=True, default=float).encode())
    h.update(json.dumps(res.ledger_addon, sort_keys=True).encode())
    return h.hexdigest()


def test_deterministic_bit_identical():
    plan = example_plan()
    a = run_people(STORM, water=None, response=plan)
    b = run_people(STORM, water=None, response=example_plan())
    assert a.seed == b.seed
    assert _h(a) == _h(b)


def test_seed_depends_on_storm_and_plan():
    a = run_people(STORM, water=None, response=None)
    b = run_people(STORM, water=None, response=example_plan())
    assert a.seed != b.seed
    # same people face every plan
    assert a.frames.shape[1] == b.frames.shape[1]
    np.testing.assert_array_equal(a.frames[0, :, :2][a.frames[0, :, 2] == 0][:50],
                                  a.frames[0, :, :2][a.frames[0, :, 2] == 0][:50])


def test_response_helps_and_runtime(tmp_path):
    t0 = time.time()
    base = run_people(STORM, water=None, response=None)
    plan = run_people(STORM, water=None, response=example_plan())
    assert time.time() - t0 < 40          # two full runs; one run must be <= 20 s
    tb, tp = base.summary["totals"], plan.summary["totals"]
    assert tp["evacuated"] > 0 and tp["sheltered_peak"] > 0
    assert tp["expected_fatalities"] <= tb["expected_fatalities"]
    assert plan.ledger_addon["response_cost_usd"] > 0
    assert set(base.ledger_addon) >= {"life_safety_usd", "injuries_usd", "unmet_needs_usd", "response_cost_usd"}
    write_people(plan, str(tmp_path), str(tmp_path / "plan_objects.json"))
    f = np.fromfile(tmp_path / "people.f32", dtype="<f4")
    assert f.size == plan.frames.size
    st = plan.frames[:, :, 2]
    assert set(np.unique(st)).issubset({0, 1, 2, 3, 4, 5})
    objs = json.load(open(tmp_path / "plan_objects.json"))["objects"]
    types = {o["type"] for o in objs}
    assert {"shelter", "evac_zone", "evac_route", "depot", "medical_post"} <= types
    for o in objs:
        assert o["geometry"]["kind"] in ("point", "polyline", "polygon", "mesh")


def test_validation():
    r = ResponsePlan()
    with pytest.raises(ResponseError):
        r.shelter(10 ** 7)
    with pytest.raises(ResponseError):
        r.evacuate([(0, 0), (10, 0)], order_at_s=0)          # not a polygon
    with pytest.raises(ResponseError):
        r.alert("x", at_s=-100 * 3600)                       # before -72 h
    with pytest.raises(ResponseError):
        r.crew("wizard", 1, stage_at=(10, 10))
    sid = r.shelter(8637, capacity=10 ** 6)                  # clamped to FEMA 40 sq ft limit
    cap = r.get(sid).args["capacity"]
    assert cap == ResponsePlan.max_capacity(json.load(open(os.path.join(ROOT, "data/grid/buildings.json")))[8637])
    assert any("clamped" in w for w in r.warnings)


def test_every_cost_item_sourced():
    c = load_costs()
    for k, v in c["items"].items():
        assert v["source"] and v.get("retrieved") and v["usd"] >= 0, k
    for k, v in c["planning_factors"].items():
        assert v["source"] and v.get("retrieved"), k
    p = json.load(open(os.path.join(ROOT, "data/reference/people_params.json")))["params"]
    for k, v in p.items():
        assert v["source"] and v.get("retrieved"), k


def test_fd2321_hazard_rating():
    # FD2321: HR = d (v + 0.5) + DF, urban DF 0 below 0.25 m, 1 above
    assert abs(float(hazard_rating(0.2, 0.0)) - 0.1) < 1e-6
    assert abs(float(hazard_rating(0.5, 1.0)) - (0.75 + 1.0)) < 1e-6
