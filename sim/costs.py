"""LOCKED cost accountant (owned by damage/costs agent). Deterministic; prices only from
data/reference/unit_costs.json (+ data/reference/response_costs.json via sim/response.py).

price_plan(grid_before, grid_after, plan_log) -> {total_usd, items: [{what, item, qty, unit, unit_cost, usd,
                                                  source, url, measured, flags, item_id}], flags, by_category}
  grid_before: dict/obj with z, ksat, manning, cover, bldg_id (base arrays) or None (= data/grid)
  grid_after:  CityEditor (preferred) or arrays dict
  plan_log:    CityEditor.items list (or the CityEditor itself, or exec_plan's log dict)

Quantities are MEASURED from the physical diff wherever possible:
  - z raised in a wall/sandbag item's own cells -> length x mean measured height (not the requested one)
  - every other z change -> earthworks |dz| x area (+ repaving where paved)
  - ksat/cover/manning changes outside bioswale cells -> permeable surfacing m2
  - bioswale cells -> bioswale m2 (length x width)
  - sinks/tunnels/inlets/floodproofing/trees/cars from the editor's state
  - city.action cost_items: re-measured when the planner can (inlets cleared, cars towed); otherwise the
    declared qty is used and flagged "declared quantity".
estimate(plan_code) -> dry run (no simulation): exec_plan + price_plan.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "data", "reference")
_UC = None


def uc():
    global _UC
    if _UC is None:
        _UC = json.load(open(os.path.join(REF, "unit_costs.json")))
    return _UC


def _a(obj, k):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(k)
    return getattr(obj, k, None)


class _Book:
    def __init__(self):
        self.lines = []
        self.flags = []

    def add(self, what, item, qty, category, item_id=None, measured=True, flags=None, unit_cost=None):
        U = uc()[item]
        c = float(U["usd"] if unit_cost is None else unit_cost)
        qty = float(qty)
        if qty <= 0:
            return 0.0
        usd = round(c * qty, 2)
        self.lines.append({"what": what, "item": item, "qty": round(qty, 3), "unit": U["unit"], "unit_cost": round(c, 2),
                           "usd": usd, "category": category, "source": U.get("source", ""), "url": U.get("url", ""),
                           "retrieved": U.get("retrieved"), "confidence": U.get("confidence"), "measured": measured,
                           "flags": list(flags or []), "item_id": item_id})
        return usd


def wall_unit_cost_per_m(h):
    """$ per m length for a wall of height h: low-wall rate up to 1.2 m, engineered floodwall from 2.5 m, linear blend."""
    U = uc()
    lo, hi = U["floodwall_low_m_per_m"]["usd"], U["floodwall_high_m_per_m"]["usd"]
    if h <= 1.2:
        r = lo
    elif h >= 2.5:
        r = hi
    else:
        f = (h - 1.2) / 1.3
        r = lo + f * (hi - lo)
    return r * h


def sandbags_per_m(h_m):
    """USACE pyramid: N = (3H + 9H^2)/2 bags per linear ft, H in ft."""
    H = h_m / 0.3048
    return (3 * H + 9 * H * H) / 2 / 0.3048


def price_plan(grid_before=None, grid_after=None, plan_log=None, duration_s=21600.0, response_measured=None):
    from sim.city_api import CityEditor, _load_base
    ed = grid_after if isinstance(grid_after, CityEditor) else (plan_log if isinstance(plan_log, CityEditor) else None)
    if grid_before is None:
        grid_before = ed.base if ed is not None else _load_base()[1]
    items = ed.items if ed is not None else (plan_log.get("items") if isinstance(plan_log, dict) else (plan_log or []))
    after = ed if ed is not None else grid_after
    U = uc()
    bk = _Book()
    dx = float(_a(after, "dx") or 4.0)
    A = dx * dx
    zb = np.asarray(_a(grid_before, "z"), np.float64)
    za = np.asarray(_a(after, "z"), np.float64)
    dz = za - zb
    claimed = np.zeros(zb.shape, bool)
    swale = np.zeros(zb.shape, bool)
    covb = np.asarray(_a(grid_before, "cover"))

    # ---- linear structures measured in their own cells
    for it in items:
        k = it["kind"]
        if k in ("floodwall", "deployable", "flipup", "sandbags") and it.get("cells"):
            iy, ix = np.asarray(it["cells"][0], int), np.asarray(it["cells"][1], int)
            if len(iy) == 0:
                continue
            d = dz[iy, ix]
            fresh = ~claimed[iy, ix]
            raised = (d > 0.01) & fresh
            claimed[iy, ix] = True
            if not raised.any():
                bk.flags.append(f"{it['id']}: no measurable height change (already higher ground)")
                continue
            h = float(d[raised].mean())
            L = float(it["length_m"]) * raised.sum() / len(d)
            if k == "floodwall":
                bk.add(f"floodwall {L:.0f} m x {h:.2f} m (measured)", "floodwall_low_m_per_m" if h <= 1.2 else "floodwall_high_m_per_m",
                       L, "flood_defence", it["id"], unit_cost=wall_unit_cost_per_m(h))
            elif k == "deployable":
                bk.add(f"deployable barrier {L:.0f} m", "deployable_barrier_m", L * max(1.0, h / 1.2), "flood_defence", it["id"])
            elif k == "flipup":
                bk.add(f"flip-up flood gate {L:.0f} m x {h:.2f} m", "flipup_barrier_m2", L * h, "flood_defence", it["id"])
            else:
                n = sandbags_per_m(h) * L
                bk.add(f"sandbag line {L:.0f} m x {h:.2f} m = {n:.0f} bags", "sandbag", n, "flood_defence", it["id"])
        if k == "bioswale" and it.get("cells"):
            iy, ix = np.asarray(it["cells"][0], int), np.asarray(it["cells"][1], int)
            fresh = ~claimed[iy, ix]
            claimed[iy, ix] = True
            swale[iy, ix] = True
            area = float(it["length_m"]) * float(it["width_m"]) * (fresh.mean() if len(fresh) else 0)
            bk.add(f"bioswale {it['length_m']:.0f} m x {it['width_m']:.1f} m", "bioswale_m2", area, "green_infrastructure", it["id"])

    # ---- unclaimed terrain change -> earthworks (+ repaving on paved cells)
    ew = (np.abs(dz) > 0.005) & ~claimed
    if ew.any():
        vol = float(np.abs(dz[ew]).sum() * A)
        bk.add(f"earthworks {vol:,.0f} m3 over {ew.sum() * A:,.0f} m2 (measured |dz|)", "earthworks_m3", vol, "earthworks")
        paved = ew & (covb == 0)
        bk.add(f"repaving {paved.sum() * A:,.0f} m2", "repave_m2", paved.sum() * A, "earthworks")

    # ---- surface changes outside swales -> permeable surfacing
    surf = np.zeros(zb.shape, bool)
    for key in ("ksat", "cover", "manning"):
        b, a = _a(grid_before, key), _a(after, key)
        if b is not None and a is not None:
            surf |= np.asarray(a) != np.asarray(b)
    surf &= ~swale
    if surf.any():
        bk.add(f"permeable surfacing {surf.sum() * A:,.0f} m2 (measured ksat/cover/manning change)", "permeable_pavement_m2",
               surf.sum() * A, "green_infrastructure")
    bb, ba = _a(grid_before, "bldg_id"), _a(after, "bldg_id")
    if bb is not None and ba is not None and not np.array_equal(np.asarray(bb), np.asarray(ba)):
        bk.flags.append("REJECTED: bldg_id raster edited (demolition/new buildings are not priced); edit ignored by the accountant")

    # ---- items from editor state
    bl = {int(b["id"]): b for b in (_a(after, "buildings") or [])}
    tr = {int(t["id"]): t for t in (_a(after, "trees") or [])}
    for it in items:
        k, iid = it["kind"], it["id"]
        if k == "green_roof":
            area = sum(float(bl.get(b, {}).get("area_m2") or 0) for b in it["building_ids"])
            bk.add(f"green roofs on {len(it['building_ids'])} buildings", "green_roof_m2", area, "green_infrastructure", iid)
        elif k == "storage":
            bk.add(f"storage vault {it['volume_m3']:,.0f} m3", "storage_m3", it["volume_m3"], "grey_infrastructure", iid)
        elif k == "pump":
            bk.add(f"pump station {it['m3s']:.2f} m3/s", "pump_station_m3s", it["m3s"], "grey_infrastructure", iid)
        elif k == "tunnel":
            c = U["tunnel_m_ref"]
            per_m = c["usd"] * it["diameter_m"] / c["d_ref_m"]
            bk.add(f"tunnel {it['length_m']:.0f} m, D {it['diameter_m']:.1f} m ({it['m3s']:.0f} m3/s)", "tunnel_m_ref",
                   it["length_m"], "grey_infrastructure", iid, unit_cost=per_m)
        elif k == "inlet_upgrade":
            bk.add(f"upgrade {len(it['inlet_ids'])} inlets x{it['factor']:.1f}", "inlet_upgrade", len(it["inlet_ids"]), "grey_infrastructure", iid)
        elif k == "inlet_new":
            bk.add(f"{it['n']} new inlets", "inlet_new", it["n"], "grey_infrastructure", iid)
        elif k == "floodproof":
            area = sum(float(bl.get(b, {}).get("area_m2") or 0) for b in it["building_ids"])
            bk.add(f"dry floodproofing {len(it['building_ids'])} buildings ({area:,.0f} m2 footprint)", "dry_floodproof_m2", area,
                   "building_protection", iid)
        elif k in ("tree_prune", "tree_brace"):
            bk.add(f"{k.split('_')[1]} {len(it['tree_ids'])} trees", k, len(it["tree_ids"]), "trees", iid)
        elif k in ("tree_remove", "tree_replace"):
            dbh = sum(float(tr.get(t, {}).get("dbh_cm") or 0) for t in it["tree_ids"])
            n = len(it["tree_ids"])
            bk.add(f"remove {n} trees ({dbh:.0f} cm DBH total)", "tree_remove_per_cm_dbh", dbh, "trees", iid)
            bk.add(f"removal permits ({n} trees, 1 per 3)", "tree_removal_permit", math.ceil(n / 3), "trees", iid)
            if k == "tree_replace":
                bk.add(f"plant {n} replacement trees", "tree_replace", n, "trees", iid)
        elif k == "wind_screen":
            bk.add(f"wind screen {it['length_m']:.0f} m x {it['height_m']:.1f} m", "wind_screen_m2", it["length_m"] * it["height_m"],
                   "wind", iid)
        elif k == "drain_clear":
            bk.add(f"clear {len(it['inlet_ids'])} catch basins", "drain_clear_inlet", len(it["inlet_ids"]), "operations", iid)
        elif k == "sewer_drawdown":
            n = len(it["basins"])
            # SFPUC operators: 2 staff x 12 h per basin drawn down (composed from the labour item)
            bk.add(f"sewer drawdown ops ({n} basins, 2 operators x 12 h each)", "labour_crew_hr", 24.0 * n, "operations", iid,
                   flags=["composed: crew-hours per basin is a planning assumption"])
        elif k == "tow":
            bk.add(f"tow {len(it['car_ids'])} cars", "tow_car", len(it["car_ids"]), "operations", iid)
        elif k == "sandbag_giveaway":
            bk.add(f"sandbag giveaway {it['bags']} bags", "sandbag", it["bags"], "operations", iid)
        elif k == "portable_pump":
            hrs = float(it.get("hours") or (duration_s / 3600.0 - it["t_s"] / 3600.0))
            units = it["n_points"] * math.ceil(it["m3s_each"] / U["portable_pump_12in_hr"]["capacity_m3s"] - 1e-9)
            bk.add(f"{units} x 12-in pumps x {hrs:.0f} h", "portable_pump_12in_hr", units * hrs, "operations", iid)
            bk.add(f"pump operators {units} x {hrs:.0f} h", "labour_crew_hr", units * hrs, "operations", iid)
        elif k == "pre_position":
            hrs = it["n"] * it["hours"]
            bk.add(f"{it['n']} x {it['equip']} for {it['hours']:.0f} h", it["equip"], hrs, "operations", iid)
            bk.add(f"operators for {it['equip']}", "labour_crew_hr", hrs, "operations", iid)
        elif k == "action":
            for item, q in it["cost_items"]:
                q2, meas, fl = q, False, ["declared quantity (not physically measurable by the accountant)"]
                if item == "drain_clear_inlet" and "inlet_clog_clear" in it.get("effects", []):
                    q2 = len([i for i, v in (_a(after, "clog_factor") or {}).items() if v >= 1.0])
                    meas, fl = True, []
                elif item == "tow_car" and "relocate_cars" in it.get("effects", []):
                    q2 = len(set(_a(after, "removed_cars") or []))
                    meas, fl = True, []
                bk.add(f"{it['name']}: {U[item]['description']}", item, q2, "operations", iid, measured=meas, flags=fl)

    # ---- emergency response (sim/response.py prices from response_costs.json)
    resp = getattr(ed, "_response", None) if ed is not None else None
    if resp is not None:
        try:
            for l in resp.cost_lines(measured=response_measured, duration_s=duration_s):
                l = dict(l)
                l.setdefault("category", "emergency_response")
                l.setdefault("measured", response_measured is not None)
                l.setdefault("flags", [])
                bk.lines.append(l)
        except Exception as e:
            bk.flags.append(f"response costs failed: {e}")

    total = round(float(sum(l["usd"] for l in bk.lines)), 2)
    cats = {}
    for l in bk.lines:
        cats[l.get("category", "other")] = round(cats.get(l.get("category", "other"), 0.0) + l["usd"], 2)
    return {"total_usd": total, "items": bk.lines, "by_category": cats, "flags": bk.flags,
            "currency_note": U["_meta"]["currency"], "sf_factor": U["_meta"]["sf_factor"]}


def estimate(plan_code, duration_s=21600.0):
    """Dry run for the model: run apply(city) on a fresh editor and price it (no simulation)."""
    from sim.city_api import exec_plan
    ed, log, err = exec_plan(plan_code)
    if err:
        return {"error": err, "total_usd": None, "items": [], "log": log}
    out = price_plan(None, ed, ed, duration_s=duration_s)
    out["log"] = log
    return out


def bca(avoided_usd_per_event, cost_usd, annual_exceedance_prob, discount=0.07, life_years=50):
    """FEMA-style BCR for a single design event: annualised avoided loss x AEP, present value over life.
    discount 7% (OMB A-94; FEMA reverted from 3.1% on 8 Apr 2025), life 50 y (FEMA BCA floodwall/drainage)."""
    pv = (1 - (1 + discount) ** -life_years) / discount
    benefit = avoided_usd_per_event * annual_exceedance_prob * pv
    return {"bcr": round(benefit / cost_usd, 3) if cost_usd else None, "pv_benefits_usd": round(benefit, 2),
            "discount": discount, "life_years": life_years, "aep": annual_exceedance_prob,
            "note": "single-event approximation; full FEMA BCA integrates damage over several return periods"}


cost_planner = type("cost_planner", (), {"estimate": staticmethod(estimate), "price_plan": staticmethod(price_plan)})
