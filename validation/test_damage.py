"""Damage / cost verification (tests 11, 12, 13).

11  Hazus FAST cross-check: our own implementation of the FAST method straight from FEMA's lookup CSVs
    (data/reference/hazus_fast) vs sim/damage.py, building by building.
12  Replay plausibility: sim/damage.py on the dec31_2022 replay vs reported losses.
13  Determinism (water with 1 vs 8 threads, damage, costs) and cost sourcing coverage.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time

import numpy as np

from sim import damage, costs
from sim import water as Wm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAST = os.path.join(ROOT, "data", "reference", "hazus_fast")
FT = 0.3048
SQFT = 10.7639
COLS = ["m4", "m3", "m2", "m1"] + [f"p{i}" for i in range(25)]


def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode()).hexdigest()


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def _csv(name):
    with open(os.path.join(FAST, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------------------------- 11
class FastByHand:
    """FEMA Hazus FAST method from the raw lookup tables (independent of damage_functions.json except
    for which DDF_ID the sim says it uses, which we check is a valid riverine curve for that occupancy)."""

    def __init__(self):
        self.bldg = {int(r["DDF_ID"]): r for r in _csv("Building_DDF_Riverine_LUT_Hazus4p0.csv")}
        self.cont = {int(r["DDF_ID"]): r for r in _csv("Content_DDF_Riverine_LUT_Hazus4p0.csv")}
        self.inc = {r["Occupancy"]: r for r in _csv("flBldgEconParamWageCapitalIncome.csv")}
        self.rec = {r["Occupancy"]: r for r in _csv("flBldgEconParamRecaptureFactors.csv")}
        self.rent = {r["Occupancy"]: r for r in _csv("flBldgEconParamRental.csv")}
        self.own = {r["Occupancy"]: r for r in _csv("flBldgEconParamOwnerOccupied.csv")}
        self.rest = {}
        for r in _csv("flRsFnGBS_LUT.csv"):
            self.rest.setdefault(r["Occupancy"], []).append(
                (float(r["Min_Depth"]), float(r["Max_Depth"]), float(r["Min_Restor_Days"])))

    @staticmethod
    def pct(row, depth_ff_m):
        xs = np.arange(-4, 25) * FT
        ys = np.array([float(row[c]) for c in COLS])
        if depth_ff_m < xs[0]:
            return 0.0
        return float(np.interp(depth_ff_m, xs, ys))

    def days(self, occ, d_ft):
        for lo, hi, dmin in self.rest.get(occ, self.rest["COM1"]):
            if lo < d_ft <= hi or (d_ft > 24 and hi >= 24):
                return dmin
        return 0.0

    @staticmethod
    def _k(table, occ):
        """Hazus splits RES3 into RES3A-F (identical econ rows); the building inventory uses RES3."""
        return occ if occ in table else occ + "A"

    def loss(self, b, depth_m, ddf_s, ddf_c, expo=1.0):
        d_ff = depth_m - float(damage.ref()[0]["flood"]["first_floor_height"]["by_foundation_m"]["slab"])  # Hazus slab default 1 ft
        occ = b["occupancy"]
        s = self.pct(self.bldg[ddf_s], d_ff) / 100 * b["value_structure"] * expo
        c = self.pct(self.cont[ddf_c], d_ff) / 100 * b["value_contents"] * expo
        bi = rel = 0.0
        if d_ff > 0:
            days = self.days(self._k(self.rest, occ), d_ff / FT)
            fa = float(b.get("floor_area_m2") or b["area_m2"] * max(1, int(b.get("floors") or 1))) * SQFT * expo
            k = self._k(self.inc, occ)
            inc, rec = float(self.inc[k]["IncomePerSqftPerDay"]), float(self.rec[k]["PctIncomeRecapture"])
            bi = fa * inc * days * (1 - rec)
            own = float(self.own[k]["PctOwnerOccupied"]) / 100
            rel = fa * (float(self.rent[k]["RentalCostsPerSqFtPerDay"]) * days * (1 - own)
                        + float(self.rent[k]["DisruptionCostsPerSqFt"]) * own)
        return {"structure": s, "contents": c, "bi": bi, "relocation": rel}


def _sim_one(b, depth_m):
    """sim/damage.py on a 9x9 mini grid holding one real building surrounded by uniform depth."""
    bid = -np.ones((9, 9), np.int32)
    bid[3:6, 3:6] = 0
    md = np.where(bid < 0, depth_m, 0.0).astype(np.float64)   # float64: no float32 rounding of the test depth
    bb = dict(b)
    bb["id"] = 0
    L = damage.assess({"nx": 9, "ny": 9, "dx": 4.0, "x0": 0.0, "y0": 0.0, "bldg_id": bid}, [bb],
                      {"max_depth": md}, cars=[])
    c = L["by_cause"]
    return {"structure": c["flood_structure"], "contents": c["flood_contents"], "bi": c["business_interruption"],
            "relocation": c["relocation"]}


def test_fast(out_dir):
    df, _ = damage.ref()
    fl = df["flood"]
    fh = FastByHand()
    blds = json.load(open(os.path.join(ROOT, "data", "grid", "buildings.json")))
    # curve check: every curve the sim uses equals the FEMA row with that DDF_ID, for that occupancy
    curve_ok, curve_rows = True, []
    for tab, lut in (("structure", fh.bldg), ("contents", fh.cont)):
        for k, v in fl[tab].items():
            row = lut.get(int(v["ddf_id"]))
            same = row is not None and np.allclose([float(row[c]) for c in COLS], v["pct"])
            occ_ok = row is not None and row["Occupancy"].startswith(k.split("_")[0])  # RES3 covers FEMA RES3A-F
            curve_ok &= bool(same and occ_ok)
            curve_rows.append({"table": tab, "key": k, "ddf_id": v["ddf_id"], "match": bool(same), "occupancy_ok": bool(occ_ok)})
    # building-by-building: one real building per common occupancy, three depths
    picks, seen = [], set()
    want = ["RES1", "RES3", "RES4", "COM1", "COM4", "COM8", "COM10", "IND2", "GOV1", "REL1", "EDU1"]
    for occ in want:
        for b in blds:
            if b["occupancy"] == occ and occ not in seen and b.get("value_structure", 0) > 0:
                picks.append(b)
                seen.add(occ)
                break
    for b in blds:   # one tall building to check the documented 2-storey exposure factor
        if b.get("floors", 1) >= 8 and b["occupancy"].startswith("COM"):
            picks.append(b)
            break
    rows = []
    for b in picks:
        ks = damage._curve_key(b, fl["structure"])
        kc = damage._curve_key(b, fl["contents"])
        for d in (0.45, 1.0, 1.8):
            nf = max(1, int(b.get("floors") or 1))   # NACCS storey exposure, re-derived from the JSON rule
            sh = max(2.5, float(b.get("height_m") or 3.5 * nf) / nf)
            expo = 1.0 if nf == 1 else min(1.0, max(1, math.ceil(max(d - fl["first_floor_height"]["by_foundation_m"]["slab"], 0) / sh - 1e-9)) / nf)
            hand = fh.loss(b, d, fl["structure"][ks]["ddf_id"], fl["contents"][kc]["ddf_id"], expo)
            sim = _sim_one(b, d)
            rows.append({"bldg_id": b["id"], "occupancy": b["occupancy"], "floors": b.get("floors"), "depth_m": d,
                         "exposure_factor": expo, "hand": hand, "sim": sim,
                         "max_rel_diff": max(abs(sim[k] - hand[k]) / max(abs(hand[k]), 1.0) for k in hand),
                         # the ledger is rounded to cents: a difference <= 1 cent is an exact match
                         "exact": all(abs(sim[k] - hand[k]) <= 0.011 for k in hand)})
    worst = max(r["max_rel_diff"] for r in rows)
    n_exact = sum(r["exact"] for r in rows)
    bad = sorted({(r["occupancy"], k) for r in rows if not r["exact"] for k in r["hand"]
                  if abs(r["sim"][k] - r["hand"][k]) > 0.011})
    ok = curve_ok and n_exact == len(rows)
    plt = _plt()
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.8), dpi=130)
    hs = np.array([r["hand"]["structure"] + r["hand"]["contents"] for r in rows])
    ss = np.array([r["sim"]["structure"] + r["sim"]["contents"] for r in rows])
    hb = np.array([r["hand"]["bi"] + r["hand"]["relocation"] for r in rows])
    sb = np.array([r["sim"]["bi"] + r["sim"]["relocation"] for r in rows])
    lim = [1e3, max(hs.max(), ss.max()) * 2]
    ax[0].plot(lim, lim, color="#555", ls="--", lw=1, label="1:1")
    ax[0].scatter(hs, ss, color="#2b6cb0", s=22, label="structure + contents")
    ax[0].scatter(np.maximum(hb, 1e3), np.maximum(sb, 1e3), color="#d97706", s=14, marker="s", label="BI + relocation")
    ax[0].set_xscale("log")
    ax[0].set_yscale("log")
    ax[0].set_xlabel("hand calculation from FEMA FAST CSVs, $")
    ax[0].set_ylabel("sim/damage.py, $")
    ax[0].legend(fontsize=7)
    ax[0].set_title(f"{n_exact}/{len(rows)} building-depth cases exact to the cent"
                    + (f"; mismatch: {', '.join(f'{o} {k}' for o, k in bad)}" if bad else ""), fontsize=8.5)
    xs = np.linspace(-0.5, 3.5, 200)
    for key, col in (("RES1_1", "#2b6cb0"), ("RES3", "#16a34a"), ("COM1", "#d97706"), ("COM4", "#7c3aed")):
        row = fh.bldg[int(fl["structure"][key]["ddf_id"])]
        ax[1].plot(xs, [fh.pct(row, x) for x in xs], color=col, lw=1.6, label=f"{key} (FAST DDF {row['DDF_ID']})")
        ax[1].plot(xs, damage.ddf_pct(fl["structure"], key, xs), color="k", ls=":", lw=0.8)
    ax[1].set_xlabel("depth above first floor, m")
    ax[1].set_ylabel("structure damage, %")
    ax[1].legend(fontsize=7)
    ax[1].set_title("FEMA curves (colour) vs sim (black dotted)", fontsize=9)
    fig.suptitle(f"Test 11 Hazus FAST cross-check: {'PASS (exact)' if ok else 'FAIL (see validation/BUGS.md B3)'}", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "t11_hazus_fast.png"))
    plt.close(fig)
    return {"id": "11", "name": "Hazus FAST depth-damage cross-check, building by building",
            "reference": "FEMA Hazus FAST lookup tables (Hazus 4.0 riverine DDFs, restoration, econ params), computed by hand",
            "result": f"{len(curve_rows)} curves identical to FEMA rows; {n_exact}/{len(rows)} building x depth cases exact to the cent"
                      + (f"; mismatches: {', '.join(f'{o} {k}' for o, k in bad)} (BUGS.md B3)" if bad else ""),
            "tolerance": "exact (to the cent)", "pass": bool(ok), "plot": "t11_hazus_fast.png",
            "metrics": {"curves": curve_rows, "cases": rows, "mismatches": bad, "max_rel_diff": worst},
            "notes": "FAST damage %; multi-storey buildings: USACE NACCS storey exposure (affected storeys / floors), "
                     "re-derived independently in the hand check; first floor = Hazus slab default 1 ft."}


# --------------------------------------------------------------------------------------------- 12
def test_replay_damage(out_dir, res):
    grid = Wm.load_grid()
    blds = json.load(open(os.path.join(ROOT, "data", "grid", "buildings.json")))
    L = damage.assess(grid, blds, res, cars=None)
    tile_land_km2 = float(((grid.cover != 3)).sum() * grid.dx ** 2 / 1e6)
    sf_km2 = 121.4
    area_share = tile_land_km2 / sf_km2
    val_tile = float(sum(b.get("value_structure", 0) + b.get("value_contents", 0) for b in blds))
    reported = 46e6
    implied_city = L["total_usd"] / area_share
    ratio = L["total_usd"] / (reported * area_share)
    ok = 0.1 <= ratio <= 10
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.5, 3.4), dpi=130)
    bc = {k: v for k, v in L["by_cause"].items() if v > 0}
    ks = sorted(bc, key=bc.get)
    ax.barh(ks, [bc[k] / 1e6 for k in ks], color="#2b6cb0")
    ax.axvline(reported * area_share / 1e6, color="#b91c1c", ls="--", lw=1.4,
               label=f"\\$46M citywide x tile area share {area_share:.1%} = \\${reported*area_share/1e6:.1f}M")
    ax.set_xlabel("\\$ million (tile, flood only: no trees/wind in this check)")
    ax.legend(fontsize=7.5)
    ax.set_title(f"Test 12 dec31_2022 replay damage in tile: \\${L['total_usd']/1e6:.1f}M, {L['buildings_flooded']} buildings "
                 f"with water above first floor\nratio to area-scaled reported loss: {ratio:.1f}x ({'within' if ok else 'outside'} 10x band)", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "t12_replay_damage.png"))
    plt.close(fig)
    return {"id": "12", "name": "Replay damage plausibility vs reported 2022-23 winter-storm losses",
            "reference": "SF Chronicle: >= $46M SF damage from the 2022-23 winter storms (city estimate, mostly public property)",
            "result": f"tile ${L['total_usd']/1e6:.1f}M flood damage ({L['buildings_flooded']} bldgs above first floor); "
                      f"area-scaled reference ${reported*area_share/1e6:.1f}M -> ratio {ratio:.1f}x",
            "tolerance": "order of magnitude (0.1x-10x)", "pass": bool(ok), "plot": "t12_replay_damage.png",
            "metrics": {"ledger_by_cause": L["by_cause"], "total_usd": L["total_usd"], "buildings_flooded": L["buildings_flooded"],
                        "tile_land_km2": tile_land_km2, "area_share": area_share, "tile_exposed_value_usd": val_tile,
                        "implied_citywide_usd": implied_city, "ratio": ratio},
            "notes": "Weak test by nature: the $46M is the city's public-damage estimate for a multi-storm season, "
                     "not insured private losses for one day; our tile holds SF's worst flood basin (17th & Folsom, "
                     "Mission Creek) so area scaling understates its share. Order-of-magnitude only."}


# --------------------------------------------------------------------------------------------- 13
def test_determinism(out_dir, res=None):
    out = {}
    g = Wm.make_synthetic_grid()
    st = Wm.StubStorm(duration_s=3600.0, dt_frame_s=300.0, peak_mm_h=80.0)
    hs = []
    for th in (1, 8, 8):
        r = Wm.run_water(g, st, threads=th, verbose=False)
        hs.append(_h({"max_depth": hashlib.sha256(r.max_depth.tobytes()).hexdigest(),
                      "depth": hashlib.sha256(r.depth.tobytes()).hexdigest(), "vol": r.volumes}))
    out["water_hashes_1_8_8_threads"] = [x[:12] for x in hs]
    water_ok = len(set(hs)) == 1
    dam_ok = None
    if res is not None:
        grid = Wm.load_grid()
        blds = json.load(open(os.path.join(ROOT, "data", "grid", "buildings.json")))
        a = _h(damage.assess(grid, blds, res))
        b = _h(damage.assess(grid, blds, res))
        out["damage_hashes"] = [a[:12], b[:12]]
        dam_ok = a == b
    plan = open(os.path.join(ROOT, "validation", "example_plan.py")).read()
    c1, c2 = costs.estimate(plan), costs.estimate(plan)
    err = c1.get("error")
    c1.pop("log", None)
    c2.pop("log", None)
    out["cost_hashes"] = [_h(c1)[:12], _h(c2)[:12]]
    cost_ok = err is None and _h(c1) == _h(c2)
    lines = c1.get("items", [])
    missing = [l.get("item") for l in lines if not (l.get("source") and str(l.get("url", "")).startswith("http"))]
    uc = costs.uc()
    uc_missing = [k for k, v in uc.items() if not k.startswith("_") and not (v.get("url") and v.get("retrieved"))]
    out.update({"plan_error": err, "plan_total_usd": c1.get("total_usd"), "plan_lines": len(lines),
                "plan_lines_without_source_url": missing, "unit_cost_items": len(uc),
                "unit_cost_items_without_url_or_date": uc_missing})
    ok = water_ok and (dam_ok is not False) and cost_ok and not missing and not uc_missing
    return {"id": "13", "name": "Determinism + every priced line sourced",
            "reference": "CONTRACT v3: same storm + plan -> bit-identical ledger; every unit cost has a source",
            "result": f"water 1 vs 8 threads identical: {water_ok}; damage x2 identical: {dam_ok}; cost x2 identical: {cost_ok}; "
                      f"{len(lines)} priced lines, {len(missing)} without URL; {len(uc)} unit costs, {len(uc_missing)} unsourced",
            "tolerance": "bit-identical hashes; 0 unsourced lines", "pass": bool(ok), "plot": None, "metrics": out,
            "notes": "Water determinism checked across thread counts (thread-order independence) on the synthetic "
                     "grid (1 h storm); the full pipeline re-run is too slow for run_all."}


def run(out_dir, res=None):
    out = []
    jobs = [("11", lambda: test_fast(out_dir))]
    if res is not None:
        jobs.append(("12", lambda: test_replay_damage(out_dir, res)))
    jobs.append(("13", lambda: test_determinism(out_dir, res)))
    for tid, f in jobs:
        t0 = time.time()
        try:
            r = f()
        except Exception as e:   # one crashing test must not hide the others
            import traceback
            traceback.print_exc()
            r = {"id": tid, "name": f"test {tid} (crashed)", "reference": "", "result": f"ERROR: {e!r}"[:300],
                 "tolerance": "", "pass": False, "plot": None, "metrics": {}, "notes": "crashed"}
        r["runtime_s"] = round(time.time() - t0, 1)
        out.append(r)
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ROOT)
    od = os.path.join(ROOT, "validation", "out")
    os.makedirs(od, exist_ok=True)
    print(json.dumps(run(od), indent=1, default=float)[:6000])
