"""StormSim SF validation suite: one command, writes validation/out/*.png + summary.json, VALIDATION.md,
web/public/data/validation.json and web/public/validation/*.png.

    NUMBA_NUM_THREADS=16 .venv/bin/python -m validation.run_all          # ~3-5 min with a cached replay
    ... --rerun-replay    # force the real-grid dec31_2022 replay (~15-20 min on a loaded box)

The real-grid replay (tests 2, 6, 12) is cached in validation/cache/ keyed by a hash of sim/water.py and
data/reference/water_params.json, so any solver/parameter change re-runs it automatically.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("NUMBA_NUM_THREADS", "16")

OUT = os.path.join(ROOT, "validation", "out")
WEB_IMG = os.path.join(ROOT, "web", "public", "validation")
WEB_JSON = os.path.join(ROOT, "web", "public", "data", "validation.json")
ORDER = ["1", "2", "3", "4", "5", "6a", "6b", "7", "8", "9", "10", "11", "12", "13"]


def _safe(name, fn):
    t0 = time.time()
    try:
        r = fn()
        print(f"[validation] {name}: {len(r)} results in {time.time()-t0:.0f}s", flush=True)
        return r
    except Exception as e:  # a crashing module is a failed test, never a silent skip
        traceback.print_exc()
        return [{"id": name, "name": f"{name} (crashed)", "reference": "", "result": f"ERROR: {e!r}"[:300],
                 "tolerance": "", "pass": False, "plot": None, "metrics": {}, "notes": "module crashed"}]


def main(rerun_replay=False):
    t_all = time.time()
    os.makedirs(OUT, exist_ok=True)
    from validation import replay_run
    results = []
    from validation import test_water
    results += _safe("water", lambda: test_water.run(OUT))
    res = None
    path, current = replay_run.cached()
    if path is None or (rerun_replay and not current):
        print("[validation] running the real-grid dec31_2022 replay (slow) ...", flush=True)
        path, current = replay_run.run(), True
    elif not current:
        print(f"[validation] WARNING: sim/water.py or water_params.json changed since the cached replay "
              f"{os.path.basename(path)}; using it anyway (run with --rerun-replay to refresh)", flush=True)
    try:
        from sim import water as W
        res = W.load_result(path)
    except Exception:
        traceback.print_exc()
    from validation import test_replay
    results += _safe("replay", lambda: test_replay.run(OUT, path))
    try:
        from validation import test_wind
        results += _safe("wind", lambda: test_wind.run(OUT))
    except ImportError as e:
        print("[validation] wind tests not available:", e)
    from validation import test_damage
    results += _safe("damage", lambda: test_damage.run(OUT, res))
    if res is not None:
        rp = res.params
        tag = (f"Replay parameters: scheme {rp.get('scheme', 'inertial')}, i_design {rp.get('i_design_mm_h')} mm/h, "
               f"alpha {rp.get('alpha')}" + ("" if current else " (STALE: production water_params.json has changed "
                                                                 "since this replay; rerun with --rerun-replay)"))
        for r in results:
            if r["id"] in ("2", "6a", "6b", "12"):
                r["notes"] = (r.get("notes") or "") + " " + tag
    results.sort(key=lambda r: ORDER.index(r["id"]) if r["id"] in ORDER else 99)
    n_pass = sum(bool(r["pass"]) for r in results)
    summary = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "runtime_s": round(time.time() - t_all, 1), "n_tests": len(results), "n_pass": n_pass,
               "replay_cache": os.path.basename(path) if path else None, "replay_cache_current": bool(current),
               "tests": results}
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1, default=float)
    # web: compact numbers + images
    os.makedirs(WEB_IMG, exist_ok=True)
    os.makedirs(os.path.dirname(WEB_JSON), exist_ok=True)
    web = {"generated_utc": summary["generated_utc"], "n_tests": len(results), "n_pass": n_pass, "tests": []}
    for r in results:
        img = None
        if r.get("plot") and os.path.exists(os.path.join(OUT, r["plot"])):
            shutil.copy(os.path.join(OUT, r["plot"]), os.path.join(WEB_IMG, r["plot"]))
            img = f"/validation/{r['plot']}"
        web["tests"].append({"id": r["id"], "name": r["name"], "reference": r["reference"], "result": r["result"],
                             "tolerance": r["tolerance"], "pass": bool(r["pass"]), "image": img})
    json.dump(web, open(WEB_JSON, "w"), indent=1)
    write_md(summary)
    print(f"[validation] {n_pass}/{len(results)} pass, {time.time()-t_all:.0f}s total")
    for r in results:
        print(f"  {r['id']:>3} {'PASS' if r['pass'] else 'FAIL'}  {r['name']}: {r['result']}")
    return summary


def _md_escape(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def write_md(summary):
    T = summary["tests"]
    by = {r["id"]: r for r in T}
    L = []
    L.append("# StormSim SF: verification and validation\n")
    L.append(f"Generated {summary['generated_utc']} by `NUMBA_NUM_THREADS=16 .venv/bin/python -m validation.run_all` "
             f"({summary['runtime_s']:.0f} s). **{summary['n_pass']} of {summary['n_tests']} checks pass.** "
             "Every number below is recomputed by that command; raw metrics are in `validation/out/summary.json`, "
             "bugs found are in `validation/BUGS.md`.\n")
    L.append("- **Verification** (is the code solving its equations right?): the production numba kernels of "
             "`sim/water.py` and `sim/wind.py` against analytic solutions and benchmark behaviour (tests 1, 3, 4, 5, 7, 8, 9), "
             "plus FEMA Hazus FAST by hand (11).")
    L.append("- **Validation** (does it match the real city?): the 31.12.2022 storm replayed on the real 4 m SF grid "
             "against SFPUC's 100-year flood map and the 311 reports of that day (6), and against reported losses (12).")
    L.append("- Tolerances were set from the reference/literature before looking at our answer; failures are kept "
             "as failures.\n")
    L.append("| # | Test | Reference | Our result | Tolerance | Verdict |")
    L.append("|---|---|---|---|---|---|")
    for r in T:
        L.append(f"| {r['id']} | {_md_escape(r['name'])} | {_md_escape(r['reference'])} | {_md_escape(r['result'])} | "
                 f"{_md_escape(r['tolerance'])} | {'**PASS**' if r['pass'] else '**FAIL**'} |")
    L.append("")
    fails = [r for r in T if not r["pass"]]
    if fails:
        L.append("## What failed and why\n")
        for r in fails:
            L.append(f"- **{r['id']}. {r['name']}**: {_md_escape(r['result'])}. {_md_escape(r.get('notes', ''))}")
        L.append("\nRepros and suggested fixes: `validation/BUGS.md`.\n")
    L.append("## Plots\n")
    for r in T:
        if r.get("plot"):
            L.append(f"### {r['id']}. {r['name']}\n")
            L.append(f"![{r['id']}](validation/out/{r['plot']})\n")
            if r.get("notes"):
                L.append(f"{r['notes']}\n")
    L.append(LIMITS)
    open(os.path.join(ROOT, "VALIDATION.md"), "w").write("\n".join(L) + "\n")


LIMITS = """## Honest limits

- **Local-inertial water model.** Like LISFLOOD-FP (Bates et al. 2010), the solver drops the advection term. It is
  exact for still and slow water (tests 1, 4 subcritical, 5) but converges to a slightly different PDE at higher
  Froude numbers: the dam-break bore is ~10 % late even at Fr 0.35 and the dry-bed front is far too slow
  (test 3); sheet flow on steep streets (Fr >~ 0.9) oscillates instead of reaching Manning normal depth (test 4).
  Damage is driven by ponding at low points, which is subcritical, but velocities on steep streets are not reliable.
- **Sewer is lumped.** Inlets use HEC-22 weir/orifice equations, but the pipe network is a lumped storage per
  sub-sewershed (no pipe-by-pipe hydraulics like SWMM); pipe capacity is a calibrated design intensity.
- **Validation data are proxies.** The SFPUC map is a 100-year *design* storm from SFPUC's own model, not an
  observation; 311 reports are geocoded to addresses and reflect who reports, not every flooded street. Both
  are the best public truth for SF; neither gives depths.
- **Damage totals** are Hazus FAST (depth-damage x value) applied exactly; values come from the assessor roll and
  replacement-cost assumptions, and the reported-loss comparison (test 12) is order-of-magnitude only.
- **Wind** is an 8 m LES with a wall model: it resolves street canyons and building wakes, not facade details;
  peak pressures use the quasi-steady gust approach (ASCE 7).
"""


if __name__ == "__main__":
    main(rerun_replay="--rerun-replay" in sys.argv)
