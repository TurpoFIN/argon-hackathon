"""Destruction engine tests: determinism, monotonicity, cascades on the real inventory, ledger merge.
Run: python3 -m pytest -q sim/tests/test_destruction.py
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from sim import destruction as D  # noqa: E402
from sim.storm import load_storm  # noqa: E402


def _run(name="elnino_ar_2026", scale=1.0, **kw):
    st = load_storm(name)
    if scale != 1.0:
        st = D.scaled_storm(st, scale)
    return D.run_destruction(st, **kw)


def test_deterministic_bit_identical():
    assert D.result_hash(_run()) == D.result_hash(_run())


def test_calm_storm_breaks_nothing():
    r = _run(scale=0.2)
    s = r["summary"]
    assert s["trees_failed"] == 0 and s["buildings_damaged"] == 0 and s["power_poles_down"] == 0
    assert r["ledger"]["total_usd"] == 0.0


def test_replay_matches_311_tree_reports():
    """Calibration anchor: the 31.12.2022 replay fails as many trees in the tile as 311 logged there."""
    target = json.load(open(os.path.join(ROOT, "data", "grid", "validation.json")))["counts"]["tree"]
    assert _run("dec31_2022")["summary"]["trees_failed"] == target


def test_bigger_storm_breaks_more():
    a, b = _run(scale=1.0), _run(scale=1.6)
    for k in ("trees_failed", "buildings_damaged", "power_spans_down", "ocs_spans_down", "signals_damaged"):
        assert b["summary"][k] >= a["summary"][k], k
    assert b["ledger"]["total_usd"] > a["ledger"]["total_usd"]


def test_power_cascade_tree_to_outage_to_dark_signals():
    """An ARkStorm-class gust (x1.6) brings trees onto PG&E wire: spans down -> sections out -> customers out."""
    r = _run(scale=1.6)
    s = r["summary"]
    assert s["power_spans_down"] > 0 and s["power_lines_out"] > 0 and s["customers_out"] > 0
    assert r["ledger"]["by_cause"]["power_outage"] > 0
    tree_ids = {t["id"] for t in r["trees"]}
    for sp in r["power_spans"]:
        assert sp["cause"] in ("tree", "pole")
        if sp["cause"] == "tree":
            assert sp["tree_id"] in tree_ids
    # every de-energised line has at least one downed span no later than its outage time
    downs = {}
    for sp in r["power_spans"]:
        downs[sp["line_id"]] = min(downs.get(sp["line_id"], 1e18), sp["t"])
    for L in r["power_lines_out"]:
        assert downs[L["id"]] <= L["t"]


def test_undergrounding_removes_line_exposure():
    r = _run(scale=1.6)
    out_lines = [L["id"] for L in r["power_lines_out"]]
    assert out_lines
    r2 = _run(scale=1.6, plan={"undergrounded_lines": out_lines})
    assert r2["summary"]["power_lines_out"] < r["summary"]["power_lines_out"]
    assert not set(L["id"] for L in r2["power_lines_out"]) & set(out_lines)


def test_removing_failed_trees_prevents_their_strikes():
    r = _run()
    culprits = sorted({o["tree_id"] for o in r["ocs_spans"]})
    assert culprits
    r2 = _run(plan={"tree_actions": {t: "remove" for t in culprits}})
    assert not {o["tree_id"] for o in r2["ocs_spans"]} & set(culprits)
    assert r2["summary"]["trees_failed"] <= r["summary"]["trees_failed"] - len(culprits)


def test_events_sorted_and_timeline_monotone():
    r = _run(scale=1.4)
    ts = [e["t"] for e in r["events"]]
    assert ts == sorted(ts)
    for kind, cum in r["timeline"].items():
        assert all(np.diff(cum) >= 0), kind


def test_debris_lands_downwind():
    st = load_storm("elnino_ar_2026")
    r = D.run_destruction(st)
    assert r["debris"]
    toward = (np.asarray(st.wind_dir_t) + 180.0) % 360.0
    for d in r["debris"]:
        dx, dy = d["x1"] - d["x0"], d["y1"] - d["y0"]
        if np.hypot(dx, dy) < 2:
            continue
        brg = np.degrees(np.arctan2(dx, dy)) % 360.0
        diff = np.abs((brg - toward[None, :] + 180.0) % 360.0 - 180.0).min()
        assert diff < 75.0, (d, brg)


def test_merge_ledger_adds_without_touching_damage_causes():
    r = _run(scale=1.4)
    ledger = {"total_usd": 1000.0, "by_cause": {"flood_structure": 1000.0}, "by_building": {}, "lines": [], "sources": []}
    D.merge_ledger(ledger, r)
    assert ledger["by_cause"]["flood_structure"] == 1000.0
    assert abs(ledger["total_usd"] - (1000.0 + r["ledger"]["total_usd"])) < 0.05
    assert all(k == "flood_structure" or k.startswith("destruction_") for k in ledger["by_cause"])


def test_every_priced_line_has_a_source():
    r = _run(scale=1.6)
    for line in r["ledger"]["lines"]:
        if line["usd"] > 0:
            assert line["source"] and not line["source"].startswith("unpriced"), line


def test_physics_path_uses_lbm_field_and_trees_py():
    """With the team's cached LBM field, wind comes from sim/wind.py and tree failures from sim/trees.py."""
    import glob
    st = load_storm("elnino_ar_2026")
    snapped = int(round(5.0 * round(st.wind_dir_deg / 5.0))) % 360
    if not glob.glob(os.path.join(ROOT, "data", "grid", f"wind_{snapped:03d}_*.npz")):
        return   # no cached field on this machine (a fresh solve takes minutes): nothing to check
    wf, trees_out, _ = D.physics_inputs(st)
    r = D.run_destruction(st, wind=wf, tree_result=trees_out)
    assert r["wind_source"].startswith("sim/wind.py LBM field")
    failed = {t["id"] for t in trees_out if t["fail_t"] is not None}
    assert {t["id"] for t in r["trees"]} == failed
    assert all(t["source"] == "sim/trees.py" for t in r["trees"])
    assert D.result_hash(r) == D.result_hash(D.run_destruction(st, wind=wf, tree_result=trees_out))
