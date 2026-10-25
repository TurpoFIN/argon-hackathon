"""StormSim SF damage model (owned by damage/storm agent). Deterministic, every parameter sourced.

assess(grid, buildings, water_result, tree_result=None, cars_result=None, wind_result=None,
       storm=None, plan=None, people_result=None, cars=None, trees=None) -> Ledger (dict)

Inputs are duck-typed so they work with the real modules and with synthetic tests:
- grid: sim.water.Grid or dict with nx, ny, dx, x0, y0, bldg_id (ny,nx)
- buildings: list of dicts (data/grid/buildings.json schema)
- water_result: WaterResult or dict with max_depth (ny,nx) m [+ flood_duration_s (ny,nx)]
- tree_result: list/dict of failed trees [{id, fail_t, mode, fall_dir_deg, hits?: {cars: [...], buildings: [...]}}]
  (or {"trees": [...]}); trees with fail_t None are ignored. If `hits` is missing, strikes are
  computed geometrically from trees.json (base x, y, height_m, fall_dir_deg).
- cars_result: optional {"car_depth_m": {car_id: max depth}} or {"positions": (n_frames, n_cars, >=2)} ;
  otherwise depth is sampled at the parked position from water max_depth.
- wind_result: WindField (peak_pressure_pa(bid, gust)) + storm, or dict {"facade_peak_pa": {bid: Pa}}.
- plan: CityEditor (or dict) with damage_mult {bid: f}, floodproof {bid: height_m}, removed/towed cars,
  removed/braced/pruned trees (already reflected in tree_result by trees.py).
- people_result: optional {"expected_fatalities": f, "injuries": n, ...} from sim/people.py.

Ledger: {total_usd, by_cause, by_building {id: usd}, trees_failed, cars_damaged, people_affected,
         buildings_flooded, notes, sources, lines: [{cause, usd, basis, source}]}
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "data", "reference")
FT = 0.3048
SQFT = 10.7639

_DF = None
_UC = None


def ref():
    global _DF, _UC
    if _DF is None:
        _DF = json.load(open(os.path.join(REF, "damage_functions.json")))
        _UC = json.load(open(os.path.join(REF, "unit_costs.json")))
    return _DF, _UC


def _g(obj, k, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(k, default)
    return getattr(obj, k, default)


def _curve_key(b, table):
    occ = str(b.get("occupancy") or "RES1").upper()
    if occ == "RES1":
        f = int(b.get("floors") or 1)
        return "RES1_1" if f <= 1 else ("RES1_2" if f == 2 else "RES1_3")
    if occ.startswith("RES3"):
        return "RES3"
    return occ if occ in table else "COM1"


def ddf_pct(table, key, depth_ff_m):
    """Hazus FAST: linear interpolation of % damage vs depth above first floor (ft grid)."""
    df, _ = ref()
    xs = np.asarray(df["flood"]["depth_m"])
    ys = np.asarray(table[key]["pct"], float)
    return np.interp(depth_ff_m, xs, ys, left=0.0, right=ys[-1])


def vehicle_pct(depth_m):
    df, _ = ref()
    v = df["vehicles"]
    return np.interp(depth_m, v["depth_m"], v["sedan"], left=0.0, right=100.0)


def _restoration_days(occ, depth_ff_m):
    df, _ = ref()
    R = df["restoration_days"]["rows"]
    rows = R.get(occ) or R.get(occ + "A")   # Hazus splits RES3 into RES3A-F (identical rows)
    if rows is None:
        raise KeyError(f"no Hazus restoration rows for {occ}")
    dft = depth_ff_m / FT
    for lo, hi, dmin, dmax in rows:
        if lo < dft <= hi or (dft > 24 and hi >= 24):
            return dmin
    return 0.0


def _ffh(b):
    """First-floor height above grade: Hazus default for the foundation type (no foundation data in the SF inventory
    -> slab-on-grade, 1 ft). A building-specific value is used only if the inventory flags it as surveyed."""
    df, _ = ref()
    f = df["flood"]["first_floor_height"]
    if b.get("first_floor_surveyed"):
        return float(b["first_floor_m"])
    return float(f["by_foundation_m"][b.get("foundation") or f["default_foundation"]])


def storey_exposure(b, depth_ff_m):
    """Affected storeys / total storeys (min 1 storey), USACE NACCS (2015) per-storey approach.
    Storey height from the building's own lidar height / floors."""
    floors = max(1, int(b.get("floors") or 1))
    if floors == 1:
        return 1.0
    sh = max(2.5, float(b.get("height_m") or 3.5 * floors) / floors)
    n_aff = max(1, math.ceil(max(depth_ff_m, 0.0) / sh - 1e-9))
    return min(1.0, n_aff / floors)


def _econ(occ):
    df, _ = ref()
    e = df["economic"]["by_occupancy"]
    return e.get(occ) or e.get("RES3" if occ.startswith("RES3") else "COM1")


# --------------------------------------------------------------- geometry helpers
def _bldg_adjacent_depth(bldg_id, depth, n_bldg, q=None):
    """Water depth (m) at each building's facade: quantile q of the cells touching the footprint (8-neighbour
    ring; q=None -> max). Uses each ring cell once per building."""
    ny, nx = bldg_id.shape
    if q is not None:
        bs, hs = [], []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                ys0, ys1 = max(0, -dy), ny - max(0, dy)
                xs0, xs1 = max(0, -dx), nx - max(0, dx)
                b = bldg_id[ys0:ys1, xs0:xs1]
                nb = bldg_id[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx]
                m = (b >= 0) & (nb != b) & (b < n_bldg)
                cell = (np.arange(ny)[ys0 + dy:ys1 + dy, None] * nx + np.arange(nx)[None, xs0 + dx:xs1 + dx])
                bs.append(b[m].astype(np.int64))
                hs.append(cell[m].astype(np.int64))
        b = np.concatenate(bs)
        c = np.concatenate(hs)
        key = np.unique(b * (ny * nx) + c)          # unique (building, ring cell) pairs, sorted
        b, c = key // (ny * nx), key % (ny * nx)
        h = depth.reshape(-1)[c].astype(np.float64)
        o = np.lexsort((h, b))
        b, h = b[o], h[o]
        out = np.zeros(n_bldg, np.float64)
        starts = np.searchsorted(b, np.arange(n_bldg), "left")
        ends = np.searchsorted(b, np.arange(n_bldg), "right")
        n = ends - starts
        ok = n > 0
        idx = starts[ok] + np.minimum((q * (n[ok] - 1)).round().astype(int), n[ok] - 1)
        out[ok] = h[idx]
        return out
    out = np.zeros(n_bldg, np.float64)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            ys0, ys1 = max(0, -dy), ny - max(0, dy)
            xs0, xs1 = max(0, -dx), nx - max(0, dx)
            b = bldg_id[ys0:ys1, xs0:xs1]
            nb = bldg_id[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx]
            h = depth[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx]
            m = (b >= 0) & (nb != b) & (b < n_bldg)
            if m.any():
                np.maximum.at(out, b[m], h[m].astype(np.float64))
    return out


def _cell(grid, x, y):
    nx, ny, dx = int(_g(grid, "nx")), int(_g(grid, "ny")), float(_g(grid, "dx"))
    ix = np.clip(np.floor((np.asarray(x, float) - float(_g(grid, "x0"))) / dx).astype(int), 0, nx - 1)
    iy = np.clip(np.floor((np.asarray(y, float) - float(_g(grid, "y0"))) / dx).astype(int), 0, ny - 1)
    return ix, iy


def _load_json(name):
    p = os.path.join(ROOT, "data", "grid", name)
    return json.load(open(p)) if os.path.exists(p) else []


def _failed_trees(tree_result):
    if tree_result is None:
        return []
    if isinstance(tree_result, dict) and "failed_trees" in tree_result:   # sim/trees.py ledger_inputs()
        hits = {}
        for st in tree_result.get("strikes") or []:
            h = hits.setdefault(int(st["tree_id"]), {"cars": [], "buildings": []})
            h["cars" if st["kind"] == "car" else "buildings"].append(int(st["target_id"]))
        return sorted([dict(t, hits=hits.get(int(t["id"]), {"cars": [], "buildings": []})) for t in tree_result["failed_trees"]],
                      key=lambda t: int(t["id"]))
    lst = tree_result.get("trees", tree_result) if isinstance(tree_result, dict) else tree_result
    if isinstance(lst, dict):
        lst = [dict(v, id=k) if isinstance(v, dict) else v for k, v in lst.items()]
    out = []
    for t in lst:
        t = t if isinstance(t, dict) else vars(t)
        if t.get("fail_t") is not None or t.get("failed"):
            out.append(t)
    return sorted(out, key=lambda t: int(t["id"]))


def _tree_strikes(ft, trees_by_id, cars, grid, bldg_id, removed_cars):
    """Geometric strikes: segment from base along fall direction (compass deg, toward) of length height."""
    df, _ = ref()
    r = df["trees"]["strike_radius_m"]
    car_xy = np.array([[c["x"], c["y"]] for c in cars]) if cars else np.zeros((0, 2))
    car_ids = np.array([int(c["id"]) for c in cars]) if cars else np.zeros(0, int)
    hits_cars, hits_bldg = {}, {}
    for t in ft:
        tid = int(t["id"])
        base = trees_by_id.get(tid)
        h = t.get("hits")
        if h is not None:
            hits_cars[tid] = [int(c) for c in h.get("cars", []) if int(c) not in removed_cars]
            hits_bldg[tid] = [int(b) for b in h.get("buildings", [])]
            continue
        if base is None:
            continue
        L = float(base.get("height_m") or 8.0)
        ang = math.radians(float(t.get("fall_dir_deg") or 0.0))
        ux, uy = math.sin(ang), math.cos(ang)
        p0 = np.array([base["x"], base["y"]])
        if len(car_xy):
            d = car_xy - p0
            s = np.clip(d @ np.array([ux, uy]), 0, L)
            perp = np.hypot(d[:, 0] - s * ux, d[:, 1] - s * uy)
            m = (perp <= r) & (np.hypot(d[:, 0], d[:, 1]) > 0.5)
            hits_cars[tid] = [int(c) for c in car_ids[m] if int(c) not in removed_cars]
        if bldg_id is not None:
            ss = np.arange(1.0, L + 0.01, 1.0)
            ix, iy = _cell(grid, p0[0] + ss * ux, p0[1] + ss * uy)
            b = bldg_id[iy, ix]
            hb = sorted(set(int(v) for v in b[b >= 0]))
            hits_bldg[tid] = hb[:1]  # the first building in the fall path takes the hit
    return hits_cars, hits_bldg


# --------------------------------------------------------------- main
def assess(grid, buildings, water_result, tree_result=None, cars_result=None, wind_result=None,
           storm=None, plan=None, people_result=None, cars=None, trees=None):
    df, uc = ref()
    lines = []
    by_cause = {k: 0.0 for k in ("flood_structure", "flood_contents", "business_interruption", "relocation",
                                 "vehicles_flood", "wind_glazing", "tree_strike_cars", "tree_strike_buildings",
                                 "tree_cleanup_replacement", "life_safety")}
    by_bldg = {}
    notes = []
    nb = len(buildings)
    bldg_id = _g(grid, "bldg_id")
    maxd = np.asarray(_g(water_result, "max_depth"), np.float64) if water_result is not None else None
    fdur = _g(water_result, "flood_duration_s") if water_result is not None else None
    if plan is not None and hasattr(plan, "damage_plan"):
        plan = plan.damage_plan()
    dmult = dict(_g(plan, "damage_mult", {}) or {})
    cmult = dict(_g(plan, "contents_mult", {}) or {})
    fproof = dict(_g(plan, "floodproofed", {}) or {})
    removed_cars = set(int(c) for c in (_g(plan, "removed_cars", []) or []))

    def add_b(bid, usd):
        if usd > 0:
            by_bldg[bid] = by_bldg.get(bid, 0.0) + usd

    # ---------------- flood: structure, contents, BI, relocation (Hazus)
    people_aff = 0.0
    n_flooded = 0
    pe = df["people"]
    if maxd is not None and bldg_id is not None:
        adj = _bldg_adjacent_depth(np.asarray(bldg_id), maxd, nb, q=float(df["flood"].get("facade_depth_quantile", 0.5)))
        if fdur is not None:
            adj_dur = _bldg_adjacent_depth(np.asarray(bldg_id), np.asarray(fdur, np.float64), nb)
        else:
            adj_dur = np.zeros(nb)
        st_tab, ct_tab = df["flood"]["structure"], df["flood"]["contents"]
        for i, b in enumerate(buildings):
            h = adj[i]
            if h <= 0.02:
                continue
            bid = int(b.get("id", i))
            ffh = _ffh(b)
            fp = float(fproof.get(bid, fproof.get(str(bid), 0.0)) or 0.0)
            depth_ff = h - ffh
            if fp > 0 and depth_ff <= fp:   # dry floodproofed up to fp above first floor: no entry
                depth_ff = -1.0
            key_s = _curve_key(b, st_tab)
            occ = str(b.get("occupancy") or "RES1").upper()
            m = float(dmult.get(bid, dmult.get(str(bid), 1.0)))
            mc = m * float(cmult.get(bid, cmult.get(str(bid), 1.0)))
            expo = storey_exposure(b, depth_ff)  # USACE NACCS: value share of the storeys the water reaches
            ds = float(ddf_pct(st_tab, key_s, depth_ff)) / 100.0 * float(b.get("value_structure") or 0) * m * expo
            dc = float(ddf_pct(ct_tab, _curve_key(b, ct_tab), depth_ff)) / 100.0 * float(b.get("value_contents") or 0) * mc * expo
            fa_sqft = float(b.get("floor_area_m2") or b.get("area_m2", 0) * max(1, int(b.get("floors") or 1))) * SQFT * expo
            e = _econ(occ)
            bi = rel = 0.0
            if depth_ff > 0:
                n_flooded += 1
                days = _restoration_days("RES3A" if occ.startswith("RES3") else occ, depth_ff)
                bi = fa_sqft * e.get("IncomePerSqftPerDay", 0) * days * (1 - e.get("PctIncomeRecapture", 0))
                own = e.get("PctOwnerOccupied", 50) / 100.0
                rel = fa_sqft * (e.get("RentalCostsPerSqFtPerDay", 0) * days * (1 - own) + e.get("DisruptionCostsPerSqFt", 0) * own)
            elif occ.startswith(("COM", "IND")) and adj_dur[i] > 0 and h >= pe["access_cut_depth_m"]:
                # street flooded, interior dry: lost trading hours (no recapture applied: conservative low)
                bi = fa_sqft * e.get("IncomePerSqftPerDay", 0) * adj_dur[i] / 86400.0 * (1 - e.get("PctIncomeRecapture", 0))
            bi *= m
            rel *= m
            if occ.startswith("RES") and (depth_ff > 0 or h >= pe["access_cut_depth_m"]):
                units = max(1.0, float(b.get("floor_area_m2") or 0) / pe["unit_floor_area_m2"]) if occ != "RES1" else 1.0
                people_aff += units * pe["persons_per_household"]
            by_cause["flood_structure"] += ds
            by_cause["flood_contents"] += dc
            by_cause["business_interruption"] += bi
            by_cause["relocation"] += rel
            add_b(bid, ds + dc + bi + rel)
        lines.append({"cause": "flood_structure+contents", "basis": "Hazus 4.0 riverine DDF by occupancy x assessor-based value, depth above first floor",
                      "source": df["flood"]["url"]})
        lines.append({"cause": "business_interruption+relocation", "basis": "Hazus restoration days (min) x income/rent per sq ft, recapture, owner-occupancy",
                      "source": df["flood"]["url"]})

    # ---------------- vehicles (flood)
    cars = cars if cars is not None else _load_json("cars.json")
    cars_damaged = set()
    if cars and maxd is not None:
        cd = _g(cars_result, "car_depth_m") if cars_result is not None else None
        states = cars_result if isinstance(cars_result, list) else (tree_result.get("cars") if isinstance(tree_result, dict) else None)
        if cd is None and states:
            cd = {int(st["id"]): float(st.get("max_depth_m", 0.0)) for st in states}
        if cd is None:
            ix, iy = _cell(grid, [c["x"] for c in cars], [c["y"] for c in cars])
            dep = maxd[iy, ix]
        else:
            dep = np.array([float(cd.get(int(c["id"]), cd.get(str(c["id"]), 0.0))) for c in cars])
        dmin = float(df["vehicles"].get("min_damage_depth_m", 0.1524))
        pct = np.where(dep >= dmin, vehicle_pct(dep), 0.0) / 100.0
        for c, p in zip(cars, pct):
            if p > 0 and int(c["id"]) not in removed_cars:
                by_cause["vehicles_flood"] += p * float(c.get("value_usd") or 0)
                cars_damaged.add(int(c["id"]))
        lines.append({"cause": "vehicles_flood", "basis": "USACE EGM 09-04 sedan depth-damage x car value (cars.json)",
                      "source": df["vehicles"]["url"]})

    # ---------------- wind glazing
    gl = df["glazing"]
    peak = _g(wind_result, "facade_peak_pa") if isinstance(wind_result, dict) else None
    gust = float(np.max(storm.gust_ms)) if storm is not None else None
    tglz = tree_result.get("glazing") if isinstance(tree_result, dict) else None
    if tglz:
        bmap = {int(b.get("id", i)): b for i, b in enumerate(buildings)}
        for gq in tglz:
            bid = int(gq["bldg_id"])
            H = float(bmap.get(bid, {}).get("height_m") or 10.0)
            rate = gl["curtain_wall_usd_m2"]["usd_m2"] if H >= gl["curtain_wall_min_height_m"] else gl["storefront_usd_m2"]["usd_m2"]
            usd = float(gq.get("area_failed_m2", 0.0)) * rate
            by_cause["wind_glazing"] += usd
            add_b(bid, usd)
        lines.append({"cause": "wind_glazing", "basis": f"failed glazing area from sim/trees.py (|Cp| q_gust > ASTM E1300 capacity) x Bay Area glazing $/m2; {len(tglz)} buildings",
                      "source": gl["storefront_usd_m2"]["url"]})
    elif peak is not None or (wind_result is not None and hasattr(wind_result, "peak_pressure_pa") and gust):
        nglass = 0
        for i, b in enumerate(buildings):
            bid = int(b.get("id", i))
            if peak is not None:
                p = float(peak.get(bid, peak.get(str(bid), 0.0)) or 0.0)
            else:
                try:
                    p = float(wind_result.peak_pressure_pa(bid, gust) or 0.0)
                except Exception:
                    p = 0.0
            if p <= 0:
                continue
            occ = str(b.get("occupancy") or "RES1").upper()
            yb = int(b.get("year_built") or 1950)
            dp = gl["design_pressure_pa"]["pre1980" if yb < 1980 else ("res" if occ.startswith("RES") else "com")]
            med = gl["median_capacity_factor"] * dp
            pf = 0.5 * (1 + math.erf(math.log(p / med) / (gl["beta"] * math.sqrt(2))))
            if pf < 0.01:
                continue
            H = float(b.get("height_m") or 10.0)
            per = 4.0 * math.sqrt(max(float(b.get("area_m2") or 100.0), 1.0))
            wwr = gl["window_to_wall"]["res" if occ.startswith("RES") else "com"]
            glass_m2 = per * H * gl["facade_fraction_exposed"] * wwr
            rate = gl["curtain_wall_usd_m2"]["usd_m2"] if H >= gl["curtain_wall_min_height_m"] else gl["storefront_usd_m2"]["usd_m2"]
            usd = pf * glass_m2 * rate * float(dmult.get(bid, 1.0))
            by_cause["wind_glazing"] += usd
            add_b(bid, usd)
            nglass += 1
        lines.append({"cause": "wind_glazing", "basis": f"lognormal glazing fragility (median 2xDP, beta {gl['beta']}) on LBM peak facade pressure; {nglass} buildings",
                      "source": gl["url"]})

    # ---------------- trees
    ft = _failed_trees(tree_result)
    if ft:
        trees = trees if trees is not None else _load_json("trees.json")
        tby = {int(t["id"]): t for t in trees}
        cby = {int(c["id"]): c for c in cars} if cars else {}
        hc, hb = _tree_strikes(ft, tby, cars, grid, None if bldg_id is None else np.asarray(bldg_id), removed_cars)
        tdf = df["trees"]
        struck_cars = {}
        for t in ft:
            tid = int(t["id"])
            dbh = float(tby.get(tid, {}).get("dbh_cm") or 20.0)
            mode = t.get("mode", "snap")
            for c in hc.get(tid, []):
                val = float(cby.get(c, {}).get("value_usd") or 20000)
                loss = val if (dbh >= tdf["car_total_loss_dbh_cm"] and mode in ("snap", "uproot")) else min(val, tdf["car_partial_claim_usd"])
                struck_cars[c] = max(struck_cars.get(c, 0.0), loss)
            for bid in hb.get(tid, []):
                by_cause["tree_strike_buildings"] += tdf["building_strike_usd"]
                add_b(bid, tdf["building_strike_usd"])
            by_cause["tree_cleanup_replacement"] += uc["tree_remove_per_cm_dbh"]["usd"] * dbh + uc["tree_replace"]["usd"]
        by_cause["tree_strike_cars"] = float(sum(struck_cars.values()))
        cars_damaged |= set(struck_cars)
        lines.append({"cause": "tree_strike_cars", "basis": "DBH>=30 cm trunk = total loss (car value), else average tree claim",
                      "source": tdf["car_partial_source"]["url"]})
        lines.append({"cause": "tree_strike_buildings", "basis": "average wind/hail homeowner claim per hit (Triple-I)",
                      "source": tdf["building_strike_source"]["url"]})
        lines.append({"cause": "tree_cleanup_replacement", "basis": "removal per cm DBH (contract rate x SF factor) + SF in-lieu replacement fee",
                      "source": uc["tree_replace"]["url"]})

    # ---------------- life safety (from sim/people.py)
    if people_result is not None:
        fat = float(_g(people_result, "expected_fatalities", 0.0) or 0.0)
        if fat > 0:
            by_cause["life_safety"] = fat * pe["vsl_usd"]
            lines.append({"cause": "life_safety", "basis": f"{fat:.3f} expected fatalities x VSL ${pe['vsl_usd']/1e6:.1f}M",
                          "source": pe["vsl_source"]["url"]})
        people_aff = max(people_aff, float(_g(people_result, "people_affected", 0.0) or 0.0))

    for l in lines:
        c = l["cause"].split("+")
        l["usd"] = round(sum(by_cause.get(k, 0.0) for k in c), 2)
    total = float(sum(by_cause.values()))
    if maxd is None:
        notes.append("no water result: flood damage not assessed")
    return {
        "total_usd": round(total, 2),
        "by_cause": {k: round(v, 2) for k, v in by_cause.items()},
        "by_building": {int(k): round(v, 2) for k, v in sorted(by_bldg.items())},
        "trees_failed": len(ft),
        "cars_damaged": len(cars_damaged),
        "people_affected": int(round(people_aff)),
        "buildings_flooded": n_flooded,
        "lines": lines,
        "notes": notes,
        "sources": sorted({l["source"] for l in lines}),
    }


def top_buildings(ledger, buildings, n=10):
    bl = {int(b["id"]): b for b in buildings}
    out = []
    for bid, usd in sorted(ledger["by_building"].items(), key=lambda kv: -kv[1])[:n]:
        b = bl.get(int(bid), {})
        out.append({"id": int(bid), "usd": usd, "occupancy": b.get("occupancy"), "x": b.get("x"), "y": b.get("y")})
    return out
