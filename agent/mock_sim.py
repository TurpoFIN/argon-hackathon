"""Mock of sim.run.run_storm used until the real pipeline exists (STORMSIM_MOCK=1 forces it).

Writes a CONTRACT-shaped run dir: result.json (ledger incl. people metrics, plan cost), frames/
(manifest, depth.u16 at 8 m, trees.json, damage.json), plan_objects.json. Water = bathtub fill of the
real DEM that a plan lowers; damage/cost respond to the plan log so the agent loop has a signal.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
GRID = REPO / "data" / "grid"
RUNS = REPO / "runs"

UNIT = {"wall": 4.0e6, "sandbags": 0.2e6, "regrade": 1.5e6, "permeable": 2.0e6, "green_roof": 0.8e6,
        "bioswale": 0.6e6, "storage": 6.0e6, "pump": 5.0e6, "tunnel": 30e6, "upgrade_inlets": 1.0e6,
        "add_inlets": 0.5e6, "floodproof": 1.2e6, "prune": 0.1e6, "remove_trees": 0.2e6,
        "replace_trees": 0.4e6, "brace": 0.1e6, "wind_screen": 0.5e6, "note": 0.0}
EFFECT = {"wall": 0.06, "sandbags": 0.01, "regrade": 0.02, "permeable": 0.03, "green_roof": 0.01,
          "bioswale": 0.015, "storage": 0.08, "pump": 0.07, "tunnel": 0.2, "upgrade_inlets": 0.05,
          "add_inlets": 0.03, "floodproof": 0.04, "prune": 0.01, "remove_trees": 0.01,
          "replace_trees": 0.01, "brace": 0.01, "wind_screen": 0.01}


def _plan_log(plan_dir):
    if not plan_dir:
        return []
    out = []
    try:
        ps = Path(plan_dir) / "plan_summary.json"
        if ps.exists():
            for it in json.loads(ps.read_text()).get("items", []):
                kind = str(it.get("kind", ""))
                op = next((k for k in UNIT if k in kind), "regrade" if "tree" not in kind else "prune")
                out.append({"op": op, **{k: v for k, v in it.items() if k in ("x", "y")}})
        p = Path(plan_dir) / "plan_log.json"
        if p.exists():
            out += json.loads(p.read_text())
    except Exception:
        pass
    return out


def run_storm(storm_name="elnino_ar_2026", plan_code=None, label=None, plan_dir=None, **kw):
    t0 = time.time()
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}-mock"
    rd = RUNS / run_id
    (rd / "frames").mkdir(parents=True, exist_ok=True)
    log = _plan_log(plan_dir)
    ops = [o for o in log if isinstance(o, dict) and o.get("op") != "note"]
    reduction = 1 - math.exp(-sum(EFFECT.get(o.get("op"), 0.01) for o in ops))
    cost = sum(UNIT.get(o.get("op"), 0.3e6) for o in ops)

    meta = json.loads((GRID / "meta.json").read_text())
    z = np.load(GRID / "city.npz")["z"]
    bid = np.load(GRID / "city.npz")["bldg_id"]
    z8 = z[: z.shape[0] // 2 * 2, : z.shape[1] // 2 * 2].reshape(z.shape[0] // 2, 2, z.shape[1] // 2, 2).mean((1, 3))
    ny8, nx8 = z8.shape
    n_frames = 36
    t = np.linspace(0, 6 * 3600, n_frames)
    rain = 40 * np.exp(-((t - 2.5 * 3600) / 4000) ** 2)
    gust = 18 + 14 * np.exp(-((t - 3 * 3600) / 5000) ** 2)
    land = z8 > -1.0
    peak = 3.2 * (1 - 0.8 * reduction)  # bathtub level above local minimum (Mission Creek low)
    depth = np.zeros((n_frames, ny8, nx8), np.uint16)
    for k in range(n_frames):
        lvl = np.percentile(z8[land], 3) + peak * float(np.clip(np.cumsum(rain)[k] / np.sum(rain), 0, 1))
        d = np.clip(lvl - z8, 0, 5) * land
        depth[k] = (d * 1000).astype(np.uint16)
    depth.tofile(rd / "frames" / "depth.u16")
    maxd = depth.max(0) / 1000.0

    buildings = json.loads((GRID / "buildings.json").read_text())
    by_b = {}
    for b in buildings:
        ix = int((b["x"] - meta["x0"]) // 8)
        iy = int((b["y"] - meta["y0"]) // 8)
        if 0 <= iy < ny8 and 0 <= ix < nx8:
            dd = maxd[iy, ix] - (b.get("first_floor_m") or 0.5)
            if dd > 0:
                frac = min(0.6, 0.18 * dd + 0.08)
                by_b[str(b["id"])] = round(frac * ((b.get("value_structure") or 0) + (b.get("value_contents") or 0)), 0)
    trees = json.loads((GRID / "trees.json").read_text())
    rng = np.random.default_rng(len(ops))
    fail_p = 0.02 * (1 - 0.5 * reduction)
    tr = []
    for tt in trees:
        if rng.random() < fail_p:
            tr.append({"id": tt["id"], "fail_t": float(rng.uniform(2, 5) * 3600),
                       "mode": "uproot" if rng.random() < 0.6 else "snap", "fall_dir_deg": float(rng.uniform(0, 360))})
    (rd / "frames" / "trees.json").write_text(json.dumps(tr))
    flood = sum(by_b.values())
    by_cause = {"building_structure": flood * 0.6, "contents": flood * 0.3, "business_interruption": flood * 0.1,
                "vehicles": 4000 * len(tr) + 20e6 * (1 - reduction), "tree_strikes": 15000 * len(tr),
                "tree_removal": 2500 * len(tr), "life_safety_usd": 9e6 * (1 - reduction)}
    total = float(sum(by_cause.values()))
    at_risk_ph = round(48000 * (1 - reduction) ** 1.3)
    damage = {"by_building": by_b, "total_usd": total, "by_cause": by_cause}
    (rd / "frames" / "damage.json").write_text(json.dumps(damage))
    manifest = {"nx": nx8, "ny": ny8, "dx_frame": 8.0, "x0": meta["x0"], "y0": meta["y0"], "n_frames": n_frames,
                "t": t.tolist(), "gust": gust.tolist(), "rain": rain.tolist(), "files": ["depth.u16", "trees.json", "damage.json"]}
    (rd / "frames" / "manifest.json").write_text(json.dumps(manifest))
    objs = []
    for i, o in enumerate(ops):
        geom = {"kind": "point", "coords": [[o.get("x", 0) - meta["x0"], o.get("y", 0) - meta["y0"]]]} if "x" in o else {"kind": "point", "coords": []}
        objs.append({"id": f"obj{i}", "type": o.get("op"), "phase": "build", "t_s": -72 * 3600, "geometry": geom,
                     "label": o.get("op"), "cost_usd": UNIT.get(o.get("op"), 0.3e6)})
    sp = Path(plan_dir or "/nonexistent") / "plan_objects.json"
    if sp.exists():  # real CityEditor objects from the sandbox export
        (rd / "plan_objects.json").write_text(sp.read_text())
    else:
        (rd / "plan_objects.json").write_text(json.dumps({"objects": objs}))
    time.sleep(max(0, 2.0 - (time.time() - t0)))
    result = {
        "run_id": run_id, "storm": storm_name, "label": label, "mock": True, "has_plan": bool(plan_code),
        "ledger": {"total_usd": total, "by_cause": by_cause, "by_building": by_b, "trees_failed": len(tr),
                   "cars_damaged": int(900 * (1 - reduction)), "people_affected": int(14000 * (1 - reduction)),
                   "people": {"at_risk_person_hours": at_risk_ph, "expected_fatalities": round(2.4 * (1 - reduction), 2),
                              "expected_injuries": round(31 * (1 - reduction), 1), "stranded": int(800 * (1 - reduction)),
                              "sheltered": 0, "meals_delivered": 0, "meals_needed": 0},
                   "notes": ["MOCK physics (bathtub fill) — replace with sim.run"], "sources": []},
        "plan_cost": {"total_usd": cost, "items": [{"what": o.get("op"), "usd": UNIT.get(o.get("op"), 0.3e6)} for o in ops]},
        "metrics": {"max_depth_m": float(maxd.max()), "flooded_area_m2": float((maxd > 0.15).sum() * 64)},
        "validation": {"note": "mock"},
    }
    (rd / "result.json").write_text(json.dumps(result))
    return str(rd)
