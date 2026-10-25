"""sim/run.py integration tests (owned by the integration engineer).
Run: NUMBA_NUM_THREADS=16 .venv/bin/python -m pytest -q sim/tests/test_run.py
Uses a 10-minute slice of the storm so a full pipeline pass stays short; water is re-solved (no cache)."""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sim.run import ledger_hash, plan_hash_of, run_storm  # noqa: E402

PLAN = '''
def apply(city):
    p = city.low_points(1)[0]
    box = [[p["x"]-150, p["y"]-150], [p["x"]+150, p["y"]-150], [p["x"]+150, p["y"]+150], [p["x"]-150, p["y"]+150]]
    city.storage(p["x"], p["y"], volume_m3=10000, intake_m3s=2)
    city.clear_drains(box)
    city.prune(city.trees_in(box)[:20])
'''


def _run(code, tag):
    rd = run_storm("elnino_ar_2026", plan_code=code, label=f"test {tag}", run_id=f"_test_run_{tag}", render=False,
                   threads=16, max_duration_s=600, water_cache=False)
    return rd, json.loads((rd / "result.json").read_text())


def test_hash_helpers():
    assert plan_hash_of(None) == plan_hash_of("  ") == "noplan"
    assert plan_hash_of("a") == plan_hash_of(" a\n") != plan_hash_of("b")
    assert ledger_hash({"a": 1, "b": {2: 3}}) == ledger_hash({"b": {"2": 3}, "a": 1})


def test_plan_run_deterministic_and_complete():
    rd1, r1 = _run(PLAN, "a")
    rd2, r2 = _run(PLAN, "b")
    try:
        assert r1["ledger_hash"] == r2["ledger_hash"], (r1["ledger"]["by_cause"], r2["ledger"]["by_cause"])
        assert r1["ledger_hash"] == ledger_hash(r1["ledger"])
        assert r1["plan_cost"]["total_usd"] > 0
        for f in ("result.json", "plan_code.py", "plan_objects.json"):
            assert (rd1 / f).exists(), f
        fr = rd1 / "frames"
        man = json.loads((fr / "manifest.json").read_text())
        for k in ("depth", "trees", "cars", "debris", "wind", "people", "damage"):
            assert (fr / man["files"][k]).exists(), k
        n, ny, nx = man["n_frames"], man["ny"], man["nx"]
        assert (fr / "depth.u16").stat().st_size == n * ny * nx * 2
        assert (fr / "wind_slice.f32").stat().st_size == ny * nx * 2 * 4
        assert (fr / "cars.f32").stat().st_size == n * man["n_cars"] * 16
        assert len(set(man["frame_counts"].values())) == 1, man["frame_counts"]
        led = r1["ledger"]
        assert abs(led["total_usd"] - sum(led["by_cause"].values())) < 1.0
    finally:
        for rd in (rd1, rd2):
            shutil.rmtree(rd, ignore_errors=True)
