"""Build a cited, executable Emergency Operations Plan from a scored StormSim run.

The simulator remains the authority for quantities, costs, and damage.  This module
only turns the already scored plan into an operations document.  The response action
normalisation deliberately follows Alex's ``alex/backend/action_plan.py`` contract,
while using StormSim's real ``CityEditor`` and ``ResponsePlan`` objects.

CLI: ``python -m agent.action_plan --from-tournament evidence/tournament.json``
defensively resolves the Argon winner, its scored run, and the no-plan baseline,
then writes ``runs/tournament-argon-*/eop.{json,html}``.
"""
from __future__ import annotations

import html
import argparse
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

# These are a guardrail against stale/mixed tournament artifacts, not simulation
# parameters.  The current El Niño no-plan ledger is expected in the hundreds of
# millions; a tens-of-billions value means a run from a different code/data epoch.
BASELINE_SANITY_RANGES = {
    "elnino_ar_2026": (10_000_000.0, 5_000_000_000.0),
}


def _jsonable(value):
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fmt_time(seconds: float) -> str:
    seconds = float(seconds)
    sign = "T+" if seconds >= 0 else "T-"
    h, rem = divmod(abs(seconds), 3600)
    m = round(rem / 60)
    if m == 60:
        h, m = h + 1, 0
    if h >= 1:
        return f"{sign}{int(h)}h" + (f" {int(m)}m" if m else "")
    return f"{sign}{int(m)}m"


def _coords_from_item(item: dict, editor) -> dict:
    """Return a compact, local-metre site representation for an editor item."""
    out: dict[str, Any] = {}
    for key in ("xy", "stage_at", "point"):
        if item.get(key) is not None:
            p = item[key]
            if isinstance(p, (tuple, list)) and len(p) >= 2 and isinstance(p[0], (int, float)):
                out["coords"] = [round(float(p[0]), 2), round(float(p[1]), 2)]
                break
    if "coords" not in out and item.get("x") is not None and item.get("y") is not None:
        out["coords"] = [round(float(item["x"]), 2), round(float(item["y"]), 2)]
    for key in ("polyline", "polygon"):
        if item.get(key) is not None:
            out["path"] = _jsonable(item[key])
            break
    if "coords" not in out and "path" not in out and item.get("cells"):
        cells = item["cells"]
        try:
            iy, ix = cells
            if len(ix):
                dx = float(getattr(editor, "dx", 4.0))
                out["coords"] = [round(float((ix[0] + 0.5) * dx), 2), round(float((iy[0] + 0.5) * dx), 2)]
        except (TypeError, ValueError, IndexError):
            pass
    if "coords" not in out and "path" not in out and item.get("building_ids"):
        ids = [int(x) for x in item["building_ids"]]
        points = []
        for bid in ids[:20]:
            try:
                b = editor.building(bid)
                points.append([round(float(b["x_local"]), 2), round(float(b["y_local"]), 2)])
            except Exception:
                continue
        if points:
            out["coords"] = points[0] if len(points) == 1 else points
    return out


def _site_label(site: dict, action_kind: str) -> str:
    if site.get("scope"):
        return str(site["scope"]).replace("-", " ").title()
    if site.get("coords"):
        p = site["coords"]
        if p and isinstance(p[0], list):
            p = p[0]
        return f"Local grid ({float(p[0]):.0f}, {float(p[1]):.0f})"
    if site.get("path"):
        p = site["path"]
        return f"{action_kind.replace('_', ' ').title()} corridor ({len(p)} points)"
    return "Plan-defined site"


def _kind_for_item(item: dict) -> tuple[str, str, str]:
    kind = str(item.get("kind", "action"))
    if kind in {"pump", "portable_pump"}:
        return "pump", "pump crew", "6-in trash pump, discharge hose, generator/fuel"
    if kind in {"floodwall", "deployable", "flipup", "sandbags", "sandbag_giveaway"}:
        return "flood defence", "flood-defence crew", "barrier or sandbags, PPE, traffic control"
    if kind in {"tree_prune", "tree_brace", "tree_remove", "tree_replace", "wind_screen"}:
        return "trees/wind", "tree crew", "arborist kit, saw/lift, utility clearance"
    if kind in {"drain_clear", "sewer_drawdown", "inlet_upgrade", "inlet_new"}:
        return "drainage", "drainage crew", "drainage service truck, surface-clearing kit"
    if kind in {"floodproof", "green_roof"}:
        return "building protection", "building contractor", "site access, fitted protection materials"
    if kind in {"storage", "tunnel", "regrade", "bioswale", "permeable"}:
        return "drainage", "civil works crew", "survey, excavator, compaction and traffic control"
    return "operations", "operations crew", "site PPE and communications"


def _response_site(action, editor) -> dict:
    args = action.args
    site: dict[str, Any] = {}
    if args.get("xy") is not None:
        site["coords"] = _jsonable(args["xy"])
    elif args.get("stage_at") is not None:
        site["coords"] = _jsonable(args["stage_at"])
    elif args.get("polygon") is not None:
        site["path"] = _jsonable(args["polygon"])
    elif args.get("polyline") is not None:
        site["path"] = _jsonable(args["polyline"])
    elif args.get("building_id") is not None:
        try:
            b = editor.building(int(args["building_id"]))
            site["coords"] = [round(float(b["x_local"]), 2), round(float(b["y_local"]), 2)]
            site["building_id"] = int(args["building_id"])
            site["occupancy"] = b.get("occupancy")
        except Exception:
            site["building_id"] = int(args["building_id"])
    elif args.get("tasks") and isinstance(args["tasks"][0], (list, tuple)):
        site["coords"] = _jsonable(args["tasks"][0])
    elif args.get("depot"):
        try:
            d = editor.response.get(args["depot"])
            site["coords"] = _jsonable(d.args.get("xy"))
        except Exception:
            pass
    elif action.kind == "alert":
        site["scope"] = "citywide"
    return site


def _load_plan(plan_code: str):
    from sim.city_api import exec_plan
    editor, log, error = exec_plan(plan_code)
    if error:
        raise ValueError(f"plan could not be executed: {error}")
    return editor


def _load_storm_timeline(storm_name: str) -> dict:
    try:
        from sim.storm import load_storm
        storm = load_storm(storm_name)
        peak_rain_i = int(max(range(len(storm.rain_mm_h)), key=lambda i: float(storm.rain_mm_h[i])))
        peak_gust_i = int(max(range(len(storm.gust_ms)), key=lambda i: float(storm.gust_ms[i])))
        peak_tide_i = int(max(range(len(storm.tide_m)), key=lambda i: float(storm.tide_m[i])))
        return {"name": storm.name, "title": storm.title, "duration_s": float(storm.duration_s),
                "duration": _fmt_time(storm.duration_s), "total_rain_mm": round(storm.total_rain_mm(), 2),
                "peak_rain": {"t_s": float(storm.t[peak_rain_i]), "time": _fmt_time(storm.t[peak_rain_i]),
                              "mm_h": round(float(storm.rain_mm_h[peak_rain_i]), 2)},
                "peak_gust": {"t_s": float(storm.t[peak_gust_i]), "time": _fmt_time(storm.t[peak_gust_i]),
                              "ms": round(float(storm.gust_ms[peak_gust_i]), 2)},
                "peak_tide": {"t_s": float(storm.t[peak_tide_i]), "time": _fmt_time(storm.t[peak_tide_i]),
                              "m_navd88": round(float(storm.tide_m[peak_tide_i]), 3)},
                "sources": list(storm.sources)}
    except Exception as exc:
        return {"name": storm_name, "duration_s": 6 * 3600.0, "duration": "T+6h", "sources": [],
                "warning": f"Storm timeline unavailable: {type(exc).__name__}: {exc}"}


def _ledger_deltas(ledger: dict, baseline: dict | None) -> tuple[dict, float]:
    current = ledger.get("by_cause") or {}
    before = (baseline or {}).get("by_cause") or {}
    causes = sorted(set(current) | set(before))
    avoided = {}
    for cause in causes:
        c = current.get(cause)
        b = before.get(cause)
        if isinstance(c, dict):
            c = c.get("now", c.get("value", 0))
        if isinstance(b, dict):
            b = b.get("now", b.get("value", 0))
        avoided[cause] = round(_money(b) - _money(c), 2)
    total = _money((baseline or {}).get("total_usd")) - _money(ledger.get("total_usd"))
    # Tournament entries round each cause independently while retaining a more
    # precise total damage figure.  Keep the total authoritative and put that
    # sub-cent/dollar rounding residue on the largest cause so action rows and
    # the headline ledger always reconcile exactly.
    residue = round(total - sum(avoided.values()), 2)
    if avoided and abs(residue) >= 0.01:
        key = max(avoided, key=lambda name: abs(avoided[name]))
        avoided[key] = round(avoided[key] + residue, 2)
    return avoided, round(total, 2)


def _cost_for_item(item: dict, cost_items: list[dict]) -> tuple[float, list[dict]]:
    iid = item.get("id")
    matches = [x for x in cost_items if x.get("item_id") == iid]
    if not matches:
        # Response lines do not carry item_id; the what text names the action kind.
        words = [w for w in re.split(r"[_\s]+", str(item.get("kind", "")).lower()) if len(w) > 2]
        matches = [x for x in cost_items if words and any(w in str(x.get("what", "")).lower() for w in words)]
    return round(sum(_money(x.get("usd")) for x in matches), 2), matches


def _evidence(cost_lines: list[dict], item: dict) -> list[dict]:
    out = []
    for line in cost_lines:
        src = line.get("source") or line.get("url")
        if src:
            out.append({"source": src, "url": line.get("url", ""), "retrieved": line.get("retrieved"),
                        "item": line.get("item"), "unit": line.get("unit"), "unit_cost": line.get("unit_cost")})
    if not out:
        out.append({"source": "StormSim plan item and locked cost planner", "item": item.get("kind")})
    return out


def _default_response_evidence(kind: str) -> list[dict]:
    """Cite the locked response rate even when sim.people aggregated its line."""
    mapping = {"shelter": "cot", "generator": "generator_hour", "crew_rescue": "rescue_boat_hour",
               "crew_pump": "pump_hour", "crew_sandbag": "sandbag", "trucks": "truck_hour",
               "alert_zone": "alert_message", "medical_post": "ambulance_hour", "evac_zone": "bus_hour"}
    key = mapping.get(kind)
    if not key:
        return []
    try:
        doc = json.loads((REPO / "data/reference/response_costs.json").read_text())
        item = doc["items"][key]
        return [{"source": item.get("source"), "url": item.get("url", ""), "retrieved": item.get("retrieved"),
                 "item": key, "unit": item.get("unit"), "unit_cost": item.get("usd")}]
    except Exception:
        return []


def _response_work(action) -> tuple[str, str, int, str]:
    k, a = action.kind, action.args
    if k == "shelter":
        return f"Open {a.get('name', 'shelter')} for {int(a.get('capacity', 0))} people; stage supplies.", "shelter staff", int(a.get("capacity", 0)), "cots, meals, drinking water, generator"
    if k == "evacuate":
        return f"Issue {a.get('priority', 'vulnerable')} evacuation order; route residents to listed shelters.", "evacuation", 1, "AlertSF, buses/paratransit as required, route maps"
    if k == "depot":
        return f"Stage {a.get('kind', 'response')} supplies at the depot.", "logistics", 1, "staging pallets, inventory, radio"
    if k == "supply_run":
        return f"Run {int(a.get('trucks', 1))} truck loop to shelters {a.get('shelters', [])}.", "logistics", int(a.get("trucks", 1)), "4x2 supply trucks, drivers, meals/water"
    if k == "crew":
        kind = a.get("kind", "response")
        tasks = a.get("tasks") or []
        work = f"Deploy {int(a.get('n', 1))} {kind} crew(s) for {len(tasks)} assigned task(s)."
        equip = {"pump": "6-in trash pump and discharge hose", "rescue": "shallow-draft rescue boat", "sandbag": "sandbags and PPE", "tree": "arborist saw/lift and traffic control", "medical": "medical kit and ambulance"}.get(kind, "response PPE")
        return work, f"{kind} crew", int(a.get("n", 1)), equip
    if k == "close_road":
        return "Install an authorized barricade and close the low route at the trigger time.", "traffic control", 2, "barricades, signs, radio"
    if k == "alert":
        return f"Send AlertSF/WEA: {a.get('text', '')}", "public information", 1, "AlertSF/IPAWS"
    if k == "generator":
        return "Connect and test a 150 kW backup generator at the facility.", "generator technician", 1, "150 kW diesel generator, fuel, transfer switch"
    if k == "medical_post":
        return "Open field treatment site and ambulance standby.", "medical", 6, "medical strike kit, ambulance, radio"
    return f"Execute {k.replace('_', ' ')}.", "response", 1, "PPE and communications"


def _allocate_avoided(actions: list[dict], avoided_by_cause: dict) -> None:
    """Allocate ledger deltas to actions using only deterministic Python arithmetic."""
    groups = defaultdict(list)
    for action in actions:
        groups[action.get("cause", "operations")].append(action)
    # Ledger cause names are more granular than the dispatch categories. Fold
    # them into the first matching action group so the displayed per-action
    # amounts reconcile to the total without asking Argon to invent attribution.
    buckets = defaultdict(float)
    fallback_group = "flood" if "flood" in groups else next(iter(groups), "operations")
    for raw, value in avoided_by_cause.items():
        raw_text = str(raw).lower()
        if raw in groups:
            target = raw
        elif "life" in raw_text or "unmet" in raw_text:
            target = "life_safety" if "life_safety" in groups else fallback_group
        elif any(x in raw_text for x in ("wind", "tree", "glazing")):
            target = "wind/tree" if "wind/tree" in groups else fallback_group
        else:
            target = fallback_group
        buckets[target] += _money(value)
    for cause, rows in groups.items():
        total = round(buckets.get(cause, 0.0), 2)
        weights = [max(_money(r.get("cost_usd")), 1.0) for r in rows]
        denom = sum(weights) or 1.0
        remaining = total
        for i, (row, weight) in enumerate(zip(rows, weights)):
            share = total - sum(_money(x.get("damage_avoided_usd")) for x in rows[:i]) if i == len(rows) - 1 else round(total * weight / denom, 2)
            row["damage_avoided_usd"] = round(share, 2)
            remaining -= share


def _cause_for(category: str, kind: str) -> str:
    text = f"{category} {kind}".lower()
    if any(x in text for x in ("tree", "wind")):
        return "wind/tree"
    if any(x in text for x in ("response", "shelter", "evac", "rescue", "medical", "alert", "logistics", "generator")):
        return "life_safety"
    if any(x in text for x in ("building", "floodproof")):
        return "flood/building"
    return "flood"


def _reported_avoided(result: dict, baseline: dict | None, avoided_by_cause: dict, avoided_total: float) -> tuple[dict, float]:
    """Use a final report's already computed total when the baseline file is absent."""
    if baseline is None and result.get("damage_avoided_usd") is not None:
        reported = _money(result.get("damage_avoided_usd"))
        if not avoided_by_cause or not any(abs(_money(v)) > 0.01 for v in avoided_by_cause.values()):
            return {"unattributed": reported}, reported
    return avoided_by_cause, avoided_total


def _integrity_checks(result: dict, baseline: dict | None, storm_name: str | None, plan_total: float) -> dict:
    """Validate the two numbers most likely to be corrupted by stale runs."""
    budget = result.get("budget_usd")
    if budget is None:
        budget = result.get("budget")
    try:
        budget = float(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget = None
    if budget is None or budget <= 0:
        budget_check = {"status": "unknown", "budget_usd": budget, "plan_cost_usd": plan_total,
                        "over_budget": None, "message": "No positive job budget was supplied; dispatch is unverified."}
    else:
        over = plan_total > budget + 0.01
        budget_check = {"status": "over_budget" if over else "within_budget", "budget_usd": budget,
                        "plan_cost_usd": plan_total, "over_budget": over,
                        "utilization_pct": round(100.0 * plan_total / budget, 2),
                        "message": (f"BLOCKED: plan cost ${plan_total:,.0f} exceeds job budget ${budget:,.0f}. "
                                    "Do not dispatch this plan." if over else "Plan cost is within the supplied job budget.")}

    baseline_total = _money((baseline or {}).get("total_usd"))
    range_for_storm = BASELINE_SANITY_RANGES.get(storm_name or "")
    baseline_meta = result.get("baseline_meta") or {}
    baseline_errors = []
    if baseline is None:
        baseline_errors.append("No current-sim baseline ledger was supplied.")
    elif range_for_storm and not (range_for_storm[0] <= baseline_total <= range_for_storm[1]):
        baseline_errors.append(f"Baseline ${baseline_total:,.0f} is outside the current {storm_name} sanity range "
                               f"${range_for_storm[0]:,.0f}–${range_for_storm[1]:,.0f}; likely stale or mixed-run data.")
    reported = result.get("baseline_damage_usd")
    if reported is not None and baseline_total and abs(_money(reported) - baseline_total) > max(1_000_000.0, baseline_total * 0.05):
        baseline_errors.append(f"Run report baseline ${_money(reported):,.0f} does not match supplied baseline ${baseline_total:,.0f}.")
    if baseline_meta.get("mock") is True:
        baseline_errors.append("Baseline is marked mock; a current simulator ledger is required for dispatch.")
    baseline_check = {"status": "invalid" if baseline_errors else "verified", "total_usd": baseline_total,
                      "sanity_range_usd": list(range_for_storm) if range_for_storm else None,
                      "run_id": baseline_meta.get("run_id"), "mock": baseline_meta.get("mock"),
                      "errors": baseline_errors}
    flags = []
    if budget_check["status"] != "within_budget":
        flags.append(budget_check["message"])
    flags.extend(baseline_errors)
    blocked = budget_check["status"] == "over_budget" or baseline_check["status"] == "invalid"
    if budget_check["status"] == "unknown":
        blocked = True
    return {"budget_check": budget_check, "baseline_check": baseline_check, "flags": flags,
            "dispatch_status": "blocked" if blocked else "ready", "eligible_for_dispatch": not blocked}


def _top_actions(actions: list[dict], limit: int = 10) -> list[dict]:
    ranked = sorted(actions, key=lambda a: (-_money(a.get("damage_avoided_usd")), -_money(a.get("cost_usd")), str(a.get("id"))))
    out = []
    for rank, action in enumerate(ranked[:limit], 1):
        row = {k: action.get(k) for k in ("id", "kind", "site", "site_label", "work", "crew_type", "crew_size",
                                           "equipment", "start_by", "end_by", "cost_usd", "damage_avoided_usd", "evidence")}
        row["rank"] = rank
        out.append(row)
    return out


def _build_action_plan_from_objects(objects: list[dict], result: dict, baseline: dict | None, timeline: dict) -> dict:
    """Fast path for approved runs: sim.run already emitted plan_objects.json.

    Reusing that artifact avoids applying a large winning plan a second time during
    the approval click (some evolved plans contain hundreds of shoreline segments).
    """
    ledger = result.get("ledger") or result.get("damage") or {}
    cost = result.get("plan_cost") or {}
    cost_items = list(cost.get("items") or []) if isinstance(cost, dict) else []
    by_item = defaultdict(list)
    for line in cost_items:
        if line.get("item_id"):
            by_item[line["item_id"]].append(line)
    avoided_by_cause, avoided_total = _ledger_deltas(ledger, baseline)
    avoided_by_cause, avoided_total = _reported_avoided(result, baseline, avoided_by_cause, avoided_total)
    actions = []
    seen = set()
    for obj in objects:
        typ = str(obj.get("type") or "action")
        if typ == "shelter_cots" or typ.endswith("_route"):
            continue
        oid = str(obj.get("item_id") or obj.get("id") or typ)
        if oid in seen:
            continue
        seen.add(oid)
        geom = obj.get("geometry") or {}
        coords = geom.get("coords") or []
        site = {}
        if geom.get("kind") == "point" and coords:
            site["coords"] = _jsonable(coords[0])
        elif coords:
            site["path"] = _jsonable(coords)
        if typ in ("depot", "trucks", "shelter", "generator", "tow", "evac_zone", "alert_zone"):
            category, crew_type, equipment = _kind_for_item({"kind": typ.replace("_zone", "")})
        elif typ.startswith("crew_"):
            category, crew_type, equipment = _kind_for_item({"kind": "crew"})
            crew_type = typ.removeprefix("crew_") + " crew"
            equipment = {"rescue": "shallow-draft rescue boat", "pump": "6-in trash pump"}.get(typ.removeprefix("crew_"), "response PPE")
        else:
            category, crew_type, equipment = _kind_for_item({"kind": typ})
        lines = by_item.get(oid, [])
        item_cost = round(sum(_money(x.get("usd")) for x in lines), 2)
        if not item_cost:
            item_cost = _money(obj.get("cost_usd"))
        label = str(obj.get("label") or typ.replace("_", " ").title())
        size = 1
        m = re.search(r"(?:^|\s)(\d+)\s+(?:rescue boat teams?|supply trucks?|cots)", label, re.I)
        if m:
            size = int(m.group(1))
        t_s = float(obj.get("t_s") or 0)
        end_s = t_s + (48 * 3600 if typ == "shelter" else 2 * 3600)
        evidence = _evidence(lines, {"kind": typ})
        if not lines:
            evidence = _default_response_evidence(typ) or evidence
        actions.append({"id": oid, "kind": typ, "site": site, "site_label": _site_label(site, typ),
                        "work": label, "crew_type": crew_type, "crew_size": size, "equipment": equipment,
                        "start_t_s": t_s, "start_by": _fmt_time(t_s), "end_t_s": end_s, "end_by": _fmt_time(end_s),
                        "phase": "pre_landfall" if t_s < 0 else "storm", "cost_usd": item_cost,
                        "cost_lines": _jsonable(lines), "evidence": evidence,
                        "cause": _cause_for(category, typ), "source_kind": "sim plan_objects.json"})
    actions.sort(key=lambda a: (a["start_t_s"], a["id"]))
    _allocate_avoided(actions, avoided_by_cause)
    plan_total = _money(cost.get("total_usd"))
    action_cost = round(sum(_money(a.get("cost_usd")) for a in actions), 2)
    if abs(plan_total - action_cost) > 0.01:
        actions.append({"id": "shared_cost_lines", "kind": "shared_cost_lines", "site": {"scope": "plan-wide"}, "site_label": "Plan-wide",
                        "work": "Plan-wide measured or aggregated operations", "crew_type": "planner", "crew_size": 0,
                        "equipment": "See cited cost ledger", "start_t_s": -72 * 3600.0, "end_t_s": 0.0, "start_by": "T-72h", "end_by": "T+0m",
                        "phase": "pre_landfall", "cost_usd": round(plan_total - action_cost, 2), "cost_lines": [],
                        "evidence": [{"source": "runs result.json plan_cost.items", "item": "unmatched shared lines"}],
                        "cause": "operations", "damage_avoided_usd": 0.0, "source_kind": "sim.costs aggregate"})
    queues = []
    for a in actions:
        if a["kind"].startswith("crew_"):
            prefix = a["crew_type"].replace(" ", "-")
            a["crew_queue"] = [f"{prefix}-{i}" for i in range(1, a["crew_size"] + 1)]
            queues.extend({"crew_id": f"{prefix}-{i}", "crew_type": a["crew_type"], "declared_size": 1, "actions": [a["id"]]}
                         for i in range(1, a["crew_size"] + 1))
    checks = {"plan_cost_matches_actions": abs(plan_total - round(sum(_money(a.get("cost_usd")) for a in actions), 2)) <= 0.01,
              "ledger_avoided_damage_usd": avoided_total,
              "action_avoided_damage_usd": round(sum(_money(a.get("damage_avoided_usd")) for a in actions), 2),
              "action_avoided_damage_matches_ledger": abs(avoided_total - round(sum(_money(a.get("damage_avoided_usd")) for a in actions), 2)) <= 0.01,
              "missing_cost_citations": [], "missing_sites": [a["id"] for a in actions if not a.get("site")]}
    checks["flags"] = []
    if not checks["plan_cost_matches_actions"]:
        checks["flags"].append("Action costs do not reconcile to result.json plan_cost.total_usd.")
    if not checks["action_avoided_damage_matches_ledger"]:
        checks["flags"].append("Action avoided-damage allocation does not reconcile to the ledger delta.")
    return {"storm": timeline, "actions": actions, "crew_queues": queues, "ledger": _jsonable(ledger),
            "baseline_ledger": _jsonable(baseline or {}), "damage_avoided_by_cause": avoided_by_cause,
            "damage_avoided_usd": avoided_total, "plan_cost": _jsonable(cost), "checks": checks,
            "plan_hash": result.get("plan_hash"), "run_id": result.get("run_id") or result.get("best_run_id"),
            "sources": sorted({s.get("source") for a in actions for s in a.get("evidence", []) if s.get("source")})}


def build_action_plan(plan_code: str, result: dict, baseline: dict | None = None, storm_name: str | None = None,
                      precomputed_objects: list[dict] | None = None) -> dict:
    """Return a fully numeric action plan; no model call is made here."""
    ledger = result.get("ledger") or result.get("damage") or {}
    cost = result.get("plan_cost") or {}
    cost_items = list(cost.get("items") or []) if isinstance(cost, dict) else []
    timeline = _load_storm_timeline(storm_name or result.get("storm") or "elnino_ar_2026")
    if precomputed_objects is not None:
        return _build_action_plan_from_objects(precomputed_objects, result, baseline, timeline)
    editor = _load_plan(plan_code)
    avoided_by_cause, avoided_total = _ledger_deltas(ledger, baseline)
    avoided_by_cause, avoided_total = _reported_avoided(result, baseline, avoided_by_cause, avoided_total)
    actions: list[dict] = []

    for item in list(getattr(editor, "items", []) or []):
        category, crew_type, equipment = _kind_for_item(item)
        item_cost, lines = _cost_for_item(item, cost_items)
        t_s = float(item.get("t_s", -24 * 3600.0))
        site = _coords_from_item(item, editor)
        actions.append({"id": item.get("id"), "kind": item.get("kind"), "site": site,
                        "site_label": _site_label(site, str(item.get("kind", "action"))),
                        "work": str(item.get("name") or item.get("kind", "")).replace("_", " ").capitalize(),
                        "crew_type": crew_type, "crew_size": 1, "equipment": equipment,
                        "start_t_s": t_s, "start_by": _fmt_time(t_s), "phase": "pre_landfall" if t_s < 0 else "storm",
                        "cost_usd": item_cost, "cost_lines": _jsonable(lines), "evidence": _evidence(lines, item),
                        "cause": _cause_for(category, str(item.get("kind", ""))), "source_kind": "sim.city_api item"})

    response = getattr(editor, "_response", None)
    response_actions = list(getattr(response, "actions", []) or [])
    for action in response_actions:
        work, crew_type, crew_size, equipment = _response_work(action)
        site = _response_site(action, editor)
        item_cost, lines = _cost_for_item({"id": action.id, "kind": action.kind}, cost_items)
        actions.append({"id": action.id, "kind": action.kind, "site": site,
                        "site_label": _site_label(site, action.kind), "work": work,
                        "crew_type": crew_type, "crew_size": crew_size, "equipment": equipment,
                        "start_t_s": float(action.t_s), "start_by": _fmt_time(action.t_s),
                        "phase": "pre_landfall" if action.t_s < 0 else "storm", "cost_usd": item_cost,
                        "cost_lines": _jsonable(lines), "evidence": _evidence(lines, {"kind": action.kind}),
                        "cause": _cause_for("emergency_response", action.kind), "source_kind": "sim.response action",
                        "args": _jsonable(action.args), "note": action.note})

    actions.sort(key=lambda a: (float(a["start_t_s"]), str(a["id"])))
    default_end = float(timeline.get("duration_s", 6 * 3600.0))
    duration_by_kind = {"alert": 300.0, "close_road": 1800.0, "evacuate": 3600.0,
                        "supply_run": 3600.0, "crew": 2 * 3600.0, "medical_post": 2 * 3600.0,
                        "generator": max(3600.0, default_end), "shelter": default_end + 48 * 3600.0}
    for action in actions:
        args = action.get("args") or {}
        end_s = args.get("end_s")
        if end_s is None:
            end_s = float(action["start_t_s"]) + duration_by_kind.get(str(action.get("kind")), 3600.0)
        action["end_t_s"] = max(float(action["start_t_s"]), float(end_s))
        action["end_by"] = _fmt_time(action["end_t_s"])
    _allocate_avoided(actions, avoided_by_cause)

    # A response line may be aggregated by sim.people and cannot be linked to a single
    # action. Keep the report auditable by exposing it as a shared operations line.
    action_cost = round(sum(_money(a.get("cost_usd")) for a in actions), 2)
    plan_total = _money(cost.get("total_usd"))
    if abs(plan_total - action_cost) > 0.01:
        actions.append({"id": "shared_cost_lines", "kind": "shared_cost_lines", "site": {"scope": "plan-wide"}, "site_label": "Plan-wide",
                        "work": "Plan-wide measured or aggregated operations", "crew_type": "planner", "crew_size": 0,
                        "equipment": "See cited cost ledger", "start_t_s": -72 * 3600.0, "start_by": "T-72h",
                        "phase": "pre_landfall", "cost_usd": round(plan_total - action_cost, 2), "cost_lines": [],
                        "evidence": [{"source": "runs result.json plan_cost.items", "item": "unmatched shared lines"}],
                        "cause": "operations", "damage_avoided_usd": 0.0, "source_kind": "sim.costs aggregate"})

    # Assign explicit response crew queues. Each declared unit gets one queue; tasks
    # are placed round-robin, so no queue exceeds the declared count.
    queues: dict[str, dict] = {}
    for a in actions:
        if a["kind"] != "crew":
            continue
        n = max(1, int(a.get("crew_size", 1)))
        for unit in range(1, n + 1):
            key = f"{a['crew_type']}-{unit}"
            queues.setdefault(key, {"crew_id": key, "crew_type": a["crew_type"], "declared_size": 1, "actions": []})
        for i, task in enumerate((a.get("args") or {}).get("tasks") or [None]):
            key = f"{a['crew_type']}-{i % n + 1}"
            queues[key]["actions"].append(a["id"])
        a["crew_queue"] = [f"{a['crew_type']}-{i}" for i in range(1, n + 1)]
    for a in actions:
        if "crew_queue" not in a:
            key = a["crew_type"].replace(" ", "-")
            queues.setdefault(key, {"crew_id": key, "crew_type": a["crew_type"], "declared_size": a["crew_size"], "actions": []})
            queues[key]["actions"].append(a["id"])

    checks = {
        "plan_cost_matches_actions": abs(plan_total - round(sum(_money(a.get("cost_usd")) for a in actions), 2)) <= 0.01,
        "ledger_avoided_damage_usd": avoided_total,
        "action_avoided_damage_usd": round(sum(_money(a.get("damage_avoided_usd")) for a in actions), 2),
        "action_avoided_damage_matches_ledger": abs(avoided_total - round(sum(_money(a.get("damage_avoided_usd")) for a in actions), 2)) <= 0.01,
        "missing_cost_citations": [a["id"] for a in actions if not a.get("evidence")],
        "missing_sites": [a["id"] for a in actions if not a.get("site")],
    }
    checks["flags"] = []
    if not checks["plan_cost_matches_actions"]:
        checks["flags"].append("Action costs do not reconcile to result.json plan_cost.total_usd.")
    if not checks["action_avoided_damage_matches_ledger"]:
        checks["flags"].append("Action avoided-damage allocation does not reconcile to the ledger delta.")
    checks["flags"] += [f"{x}: no site geometry" for x in checks["missing_sites"]]
    return {"storm": timeline, "actions": actions, "crew_queues": list(queues.values()), "ledger": _jsonable(ledger),
            "baseline_ledger": _jsonable(baseline or {}), "damage_avoided_by_cause": avoided_by_cause,
            "damage_avoided_usd": avoided_total, "plan_cost": _jsonable(cost), "checks": checks,
            "plan_hash": result.get("plan_hash"), "run_id": result.get("run_id") or result.get("best_run_id"),
            "sources": sorted({s.get("source") for a in actions for s in a.get("evidence", []) if s.get("source")})}


def _argon_text(payload: dict, mode: str) -> str | None:
    """Ask Argon for prose only; never accept model-produced numeric state."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return None
    try:
        from google import genai
        prompt = ("You are the SF emergency management duty officer closing a live planning cell. Review the "
                  "following Python-computed Emergency Operations Plan facts. In under 180 words, return exactly "
                  "two labeled paragraphs: EXECUTIVE BRIEF (what the emergency manager should dispatch first) and "
                  "ARGON AUDIT (call out any action whose cited cost or avoided damage is inconsistent with the "
                  "supplied machine checks, or say no mismatches). Do not change, recalculate, or invent numbers; "
                  "the checks and ledger are authoritative. Plain text only.\n\n" + json.dumps(payload, default=str))
        with genai.Client(api_key=key) as client:
            response = client.interactions.create(model="gemini-4-argon", input=prompt, store=False,
                                                  generation_config={"thinking_level": "low", "max_output_tokens": 900})
        return response.output_text.strip() if getattr(response, "output_text", None) else None
    except Exception as exc:
        return f"Argon {mode} unavailable: {type(exc).__name__}: {exc}"


def _fallback_brief(doc: dict) -> str:
    storm = doc["storm"]
    return (f"StormSim's {storm.get('title', storm.get('name', 'storm'))} runs for {storm.get('duration', 'the modeled window')} "
            f"and contains {len(doc['actions'])} executable actions. The plan cost is ${_money(doc['plan_cost'].get('total_usd')):,.0f}; "
            f"the deterministic ledger reports ${_money(doc['damage_avoided_usd']):,.0f} avoided damage. "
            "Dispatch crews in the listed queues, starting pre-landfall actions at their T-minus times, and escalate any "
            "machine-check flags before field execution.")


def make_eop(plan_code: str, result: dict, baseline: dict | None = None, storm_name: str | None = None,
             precomputed_objects: list[dict] | None = None) -> dict:
    doc = build_action_plan(plan_code, result, baseline=baseline, storm_name=storm_name,
                            precomputed_objects=precomputed_objects)
    plan_total = _money((doc.get("plan_cost") or {}).get("total_usd"))
    integrity = _integrity_checks(result, baseline, storm_name or result.get("storm"), plan_total)
    doc.update(integrity)
    doc["top_actions"] = _top_actions(doc.get("actions", []), 10)
    doc["summary"] = {"dispatch_status": doc["dispatch_status"], "eligible_for_dispatch": doc["eligible_for_dispatch"],
                      "plan_cost_usd": plan_total, "budget_usd": doc["budget_check"].get("budget_usd"),
                      "damage_avoided_usd": _money(doc.get("damage_avoided_usd")),
                      "baseline_total_usd": _money(doc["baseline_check"].get("total_usd")),
                      "top_action_count": len(doc["top_actions"])}
    doc.setdefault("checks", {}).setdefault("flags", [])
    for flag in integrity["flags"]:
        if flag not in doc["checks"]["flags"]:
            doc["checks"]["flags"].append(flag)
    brief_payload = {"storm": doc["storm"], "plan_cost_usd": _money(doc["plan_cost"].get("total_usd")),
                     "damage_avoided_usd": doc["damage_avoided_usd"], "checks": doc["checks"],
                     "budget_check": doc["budget_check"], "baseline_check": doc["baseline_check"],
                     "actions": [{k: a.get(k) for k in ("id", "work", "site_label", "start_by", "cost_usd", "damage_avoided_usd", "evidence")} for a in doc["top_actions"]]}
    model_text = None if not doc["eligible_for_dispatch"] else _argon_text(brief_payload, "brief_review")
    if model_text:
        brief, sep, audit = model_text.partition("ARGON AUDIT")
        doc["executive_brief"] = brief.replace("EXECUTIVE BRIEF", "").strip() or model_text
        doc["argon_review"] = audit.lstrip(" :\n") if sep else model_text
    elif not doc["eligible_for_dispatch"]:
        doc["executive_brief"] = "DISPATCH BLOCKED. " + " ".join(integrity["flags"])
        doc["argon_review"] = "Argon review skipped because the deterministic integrity checks blocked dispatch."
    else:
        doc["executive_brief"] = _fallback_brief(doc)
        doc["argon_review"] = "No Argon review was available; machine checks remain authoritative."
    doc["schema_version"] = 1
    return _jsonable(doc)


def render_eop_html(doc: dict) -> str:
    esc = lambda x: html.escape(str(x if x is not None else ""))
    def action_row(a, include_evidence=True):
        ev = "; ".join(str(x.get("source", "")) for x in a.get("evidence", [])[:2])
        cells = (a.get("start_by"), a.get("end_by"), a.get("site_label"), a.get("work"),
            f"{a.get('crew_type')} ({a.get('crew_size')})", a.get("equipment"), f"${_money(a.get('cost_usd')):,.2f}",
            f"${_money(a.get('damage_avoided_usd')):,.2f}") + ((ev,) if include_evidence else ())
        return "<tr>" + "".join(f"<td>{esc(v)}</td>" for v in cells) + "</tr>"
    rows = [action_row(a) for a in doc.get("actions", [])]
    queue_rows = []
    for q in doc.get("crew_queues", []):
        queue_rows.append(f"<li><b>{esc(q.get('crew_id'))}</b>: " + esc(", ".join(q.get("actions", [])) or "standby") + "</li>")
    flags = doc.get("checks", {}).get("flags", [])
    flag_html = "".join(f"<li>{esc(x)}</li>" for x in flags) or "<li>None</li>"
    sources = "".join(f"<li>{esc(x)}</li>" for x in doc.get("sources", [])) or "<li>See each action citation.</li>"
    # A small inline SVG keeps positions useful when the report is printed or
    # opened offline. Coordinates are StormSim local metres from the grid SW.
    map_marks = []
    for i, a in enumerate(doc.get("actions", [])):
        site = a.get("site") or {}
        points = site.get("coords")
        if points and points and isinstance(points[0], list):
            points = points[0]
        if points and len(points) >= 2 and isinstance(points[0], (int, float)):
            x, y = float(points[0]), float(points[1])
            map_marks.append(f'<circle cx="{x:.1f}" cy="{4256-y:.1f}" r="18" fill="#d35400"><title>{esc(a.get("id"))}: {esc(a.get("work"))}</title></circle>')
        path = site.get("path") or []
        if path and isinstance(path[0], list):
            pts = " ".join(f"{float(p[0]):.1f},{4256-float(p[1]):.1f}" for p in path if len(p) >= 2)
            if pts:
                map_marks.append(f'<polyline points="{pts}" fill="none" stroke="#2471a3" stroke-width="10"><title>{esc(a.get("id"))}</title></polyline>')
    map_svg = '<svg viewBox="0 0 3548 4256" role="img" aria-label="StormSim local action positions"><rect width="3548" height="4256" fill="#edf3f5"/>' + "".join(map_marks) + '</svg>'
    gantt_rows = []
    start_bound = -72 * 3600.0
    end_bound = max(float(doc.get("storm", {}).get("duration_s", 21600.0)), max((float(a.get("end_t_s", 0)) for a in doc.get("actions", [])), default=0.0))
    span = max(1.0, end_bound - start_bound)
    landfall_pct = (0.0 - start_bound) / span * 100.0
    for a in doc.get("actions", []):
        left = max(0.0, min(100.0, (float(a.get("start_t_s", 0)) - start_bound) / span * 100.0))
        width = max(0.6, min(100.0 - left, (float(a.get("end_t_s", 0)) - float(a.get("start_t_s", 0))) / span * 100.0))
        gantt_rows.append(f'<div class="gantt-row"><span>{esc(a.get("id"))}</span><div class="gantt-track"><i style="left:{left:.2f}%;width:{width:.2f}%" title="{esc(a.get("start_by"))} to {esc(a.get("end_by"))}"></i></div></div>')
    status = str(doc.get("dispatch_status", "unverified")).upper()
    status_class = "ok" if doc.get("eligible_for_dispatch") else "bad"
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>StormSim SF Emergency Operations Plan</title>
<style>body{{font:14px system-ui,sans-serif;color:#17202a;max-width:1400px;margin:32px auto;padding:0 24px}}h1{{margin-bottom:4px}}h2{{border-bottom:2px solid #d7e1ea;padding-bottom:5px;margin-top:28px}}.meta{{color:#52616b}}.brief{{background:#eef7ff;border-left:4px solid #2471a3;padding:14px;white-space:pre-wrap}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border:1px solid #ccd6dd;padding:7px;vertical-align:top}}th{{background:#1f4057;color:white;text-align:left}}tr:nth-child(even){{background:#f6f8fa}}.warn{{color:#9b2c2c}}svg{{width:100%;max-height:520px;border:1px solid #ccd6dd}}.gantt-row{{display:flex;gap:8px;align-items:center;margin:3px 0;font:11px monospace}}.gantt-row>span{{width:145px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.gantt-track{{position:relative;height:13px;flex:1;background:linear-gradient(90deg,#dcecf4 0 {landfall_pct:.2f}%,#f7e4c2 {landfall_pct:.2f}%);border:1px solid #bccbd4}}.gantt-track i{{position:absolute;top:0;height:100%;background:#2471a3;display:block}}@media print{{body{{margin:10mm;max-width:none}}h2{{break-after:avoid}}tr{{break-inside:avoid}}}}</style>
<style>.status{{padding:14px;margin:12px 0;font-weight:700;border:2px solid}}.status.ok{{background:#e9f8ef;color:#176b3a;border-color:#39a96b}}.status.bad{{background:#fff0f0;color:#9b1c1c;border-color:#d64545}}.summary-table td{{font-weight:600}}@media print{{.status{{break-inside:avoid}}}}</style></head>
<body><h1>Emergency Operations Plan · StormSim SF</h1><p class="meta">{esc(doc.get('storm', {}).get('title',doc.get('storm', {}).get('name','')))} · Run {esc(doc.get('run_id'))}</p>
<div class="status {status_class}">DISPATCH STATUS: {esc(status)}<br>{esc(doc.get('budget_check', {}).get('message'))}<br>{esc('; '.join(doc.get('baseline_check', {}).get('errors', [])) or 'Baseline sanity check passed.')}</div>
<div class="brief"><b>Executive brief</b><br>{esc(doc.get('executive_brief'))}</div>
<h2>Storm timeline</h2><p>Modeled duration {esc(doc.get('storm', {}).get('duration'))}; total rain {esc(doc.get('storm', {}).get('total_rain_mm'))} mm. Peak rain {esc(doc.get('storm', {}).get('peak_rain', {}).get('time'))}; peak gust {esc(doc.get('storm', {}).get('peak_gust', {}).get('time'))}; peak tide {esc(doc.get('storm', {}).get('peak_tide', {}).get('time'))}.</p>
<h2>One-page dispatch summary</h2><p>Plan cost <b>${_money(doc.get('plan_cost', {}).get('total_usd')):,.2f}</b> · Budget <b>${_money(doc.get('budget_check', {}).get('budget_usd')):,.2f}</b> · Deterministic damage avoided <b>${_money(doc.get('damage_avoided_usd')):,.2f}</b> · Baseline <b>${_money(doc.get('baseline_check', {}).get('total_usd')):,.2f}</b></p>
<table class="summary-table"><thead><tr><th>Rank</th><th>Start</th><th>End</th><th>Site</th><th>Work</th><th>Crew</th><th>Equipment</th><th>Cost</th><th>Damage avoided</th></tr></thead><tbody>{''.join('<tr><td>'+esc(a.get('rank'))+'</td>'+action_row(a, False)[4:] for a in doc.get('top_actions', []))}</tbody></table>
<h2>Dispatch timeline (hours relative to landfall)</h2><p>T-72h is the left edge; the shaded transition marks T0 (storm arrival) and bars end at their release time.</p>{''.join(gantt_rows)}
<h2>Action positions</h2>{map_svg}
<h2>Crew queues</h2><ul>{''.join(queue_rows) or '<li>No explicit crew queues.</li>'}</ul>
<h2>Full executable action ledger</h2><table><thead><tr><th>Start by</th><th>End</th><th>Site</th><th>Work</th><th>Crew</th><th>Equipment</th><th>Cost</th><th>Damage avoided</th><th>Evidence</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Machine checks</h2><p>Ledger delta by cause: {esc(json.dumps(doc.get('damage_avoided_by_cause', {}), sort_keys=True))}</p><ul class="warn">{flag_html}</ul><p>{esc(doc.get('argon_review'))}</p>
<h2>Sources</h2><ul>{sources}</ul></body></html>'''


def write_eop(job_id: str, plan_code: str, result: dict, baseline: dict | None = None, out_dir: str | Path | None = None,
              precomputed_objects: list[dict] | None = None) -> dict:
    destination = Path(out_dir) if out_dir else REPO / "runs" / str(job_id)
    destination.mkdir(parents=True, exist_ok=True)
    doc = make_eop(plan_code, result, baseline=baseline, storm_name=result.get("storm"),
                   precomputed_objects=precomputed_objects)
    (destination / "eop.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    (destination / "eop.html").write_text(render_eop_html(doc))
    return {"eop_json": f"/runs/{job_id}/eop.json", "eop_html": f"/runs/{job_id}/eop.html", "document": doc}


def _safe_read(path: Path) -> str:
    if path.exists() and path.is_file():
        return path.read_text()
    raise FileNotFoundError(path)


def generate_eop_for_job(job_id: str, job_result: dict, repo: str | Path = REPO) -> dict:
    """Resolve an optimizer/evolver result and write ``runs/<job>/eop.*``."""
    root = Path(repo)
    plan_candidates = []
    if job_result.get("plan_path"):
        plan_candidates.append(str(job_result["plan_path"]).lstrip("/"))
    plan_candidates += [f"runs/_jobs/{job_id}.plan.py", f"runs/evolve/{job_id}/best.plan.py"]
    plan_path = next((root / p for p in plan_candidates if (root / p).exists()), None)
    if plan_path is None:
        raise FileNotFoundError(f"no submitted plan for job {job_id}")
    run_id = job_result.get("run_id") or job_result.get("best_run_id")
    if not run_id and isinstance(job_result.get("best"), dict):
        run_id = job_result["best"].get("run_id")
    if not run_id:
        raise FileNotFoundError(f"no scored run for job {job_id}")
    run_path = Path(run_id)
    if not run_path.is_absolute():
        run_path = root / "runs" / run_id
    result = dict(job_result)
    result_path = run_path / "result.json"
    if result_path.exists():
        scored = json.loads(result_path.read_text())
        scored.update({k: v for k, v in result.items() if k not in ("ledger", "plan_cost")})
        result = scored
    baseline = None
    baseline_meta = {}
    idx = root / "runs" / "_baselines.json"
    if idx.exists():
        entries = json.loads(idx.read_text())
        bid = entries.get(f"{result.get('storm', job_result.get('storm', 'elnino_ar_2026'))}") or entries.get(f"{result.get('storm', job_result.get('storm', 'elnino_ar_2026'))}-mock")
        if bid and (root / "runs" / bid / "result.json").exists():
            baseline_doc = json.loads((root / "runs" / bid / "result.json").read_text())
            baseline = baseline_doc.get("ledger")
            baseline_meta = {"run_id": bid, "mock": bool(baseline_doc.get("mock")), "storm": baseline_doc.get("storm")}
    if baseline_meta:
        result["baseline_meta"] = baseline_meta
    objects = None
    objects_path = run_path / "plan_objects.json"
    if objects_path.exists():
        try:
            payload = json.loads(objects_path.read_text())
            objects = payload.get("objects", payload) if isinstance(payload, (dict, list)) else None
        except Exception:
            objects = None
    return write_eop(job_id, _safe_read(plan_path), result, baseline=baseline, out_dir=root / "runs" / job_id,
                     precomputed_objects=objects)


def _dicts(value):
    """Yield nested dicts from any tournament schema without assuming its shape."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def _first(record: dict, keys):
    for key in keys:
        value = record.get(key)
        if value not in (None, "", []):
            return value
    return None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _path_candidates(root: Path, value) -> list[Path]:
    if not isinstance(value, str) or not value:
        return []
    raw = value.split("?", 1)[0]
    supplied = Path(raw)
    # Tournament provenance is allowed to point at the lead checkout.  Keep
    # absolute paths when they are available, then fall back to the tracked
    # copy under evidence/ for a checkout on another machine.
    out = [supplied] if supplied.is_absolute() else []
    clean = raw.lstrip("/")
    out += [root / clean, root / "runs" / clean.removeprefix("runs/")]
    return [p for p in out if p.is_file()]


def _run_result_paths(root: Path):
    for path in (root / "runs").glob("**/result.json"):
        if "/_jobs/" in str(path) or "/evolve/" in str(path):
            continue
        yield path


def _resolve_tournament_winner(root: Path, tournament: dict) -> tuple[dict, Path, dict | None]:
    """Resolve an Argon winner from several plausible tournament JSON layouts."""
    records = list(_dicts(tournament))
    run_docs = {}
    for path in _run_result_paths(root):
        try:
            doc = json.loads(path.read_text())
        except Exception:
            continue
        run_docs[path.parent.name] = (path, doc)
    candidates = []
    for rec in records:
        model_blob = " ".join(str(rec.get(k, "")) for k in ("model", "planner", "agent", "name", "label", "winner")).lower()
        plan_hash = _first(rec, ("plan_hash", "hash", "winner_plan_hash"))
        run_id = _first(rec, ("run_id", "best_run_id", "plan_run_id", "winner_run_id"))
        plan_value = _first(rec, ("plan_path", "best_plan_path", "plan_url", "best_plan_url", "path"))
        has_argon = "argon" in model_blob or str(rec.get("model", "")).lower() == "gemini-4-argon"
        winner_flag = bool(rec.get("winner") is True or rec.get("is_winner") is True or rec.get("rank") == 1)
        score = _as_float(_first(rec, ("net_benefit_usd", "fitness", "score", "damage_avoided_usd"))) or 0.0
        if has_argon or winner_flag or plan_hash or run_id or plan_value:
            candidates.append((int(winner_flag) * 10_000_000 + int(has_argon) * 1_000_000 + score, rec))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    provenance = tournament.get("provenance", {}) if isinstance(tournament, dict) else {}
    provenance_paths = provenance.get("plan_paths", {}) if isinstance(provenance, dict) else {}
    provenance_jobs = provenance.get("source_job_ids", {}) if isinstance(provenance, dict) else {}
    for _, rec in candidates:
        model_blob = " ".join(str(rec.get(k, "")) for k in ("model", "planner", "agent", "name", "label", "winner")).lower()
        run_id = _first(rec, ("run_id", "best_run_id", "plan_run_id", "winner_run_id"))
        source_job_id = _first(rec, ("source_job_id", "source_run_id"))
        if "argon" in model_blob and not source_job_id:
            source_job_id = provenance_jobs.get("Gemini 4 Argon") or provenance_jobs.get("argon")
        run_path = None
        if isinstance(run_id, str):
            run_id = Path(run_id.rstrip("/")).name
            if run_id in run_docs:
                run_path = run_docs[run_id][0]
        if run_path is None and source_job_id:
            for candidate in (root / "runs" / str(source_job_id) / "result.json",
                              root / "runs" / "evolve" / str(source_job_id) / "result.json"):
                if candidate.is_file():
                    run_path = candidate
                    break
        plan_value = _first(rec, ("plan_path", "best_plan_path", "plan_url", "best_plan_url", "path"))
        # The real tournament stores supplied plans in provenance and gives the
        # scored entry a different (not locally materialised) tournament run id.
        # Resolve the Argon source explicitly before trying generic layouts.
        if "argon" in model_blob and not plan_value:
            plan_value = provenance_paths.get("argon") or provenance_paths.get("Argon")
        plan_path = next(iter(_path_candidates(root, plan_value)), None)
        if plan_path is None and "argon" in model_blob:
            plan_path = next(iter(_path_candidates(root, "evidence/argon-winner/plan_code.py")), None)
        plan_code = _first(rec, ("plan_code", "code", "winner_plan_code"))
        if plan_path is None and isinstance(plan_code, str) and "def apply" in plan_code:
            plan_path = root / "runs" / "_jobs" / "tournament-argon.plan.py"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(plan_code)
        expected_hash = _first(rec, ("plan_hash", "hash", "winner_plan_hash"))
        if plan_path is None and expected_hash:
            for candidate in list((root / "runs").glob("**/*.plan.py")):
                try:
                    digest = hashlib.sha256(candidate.read_text().strip().encode()).hexdigest()[:16]
                except Exception:
                    continue
                full_digest = hashlib.sha256(candidate.read_text().strip().encode()).hexdigest()
                if digest == str(expected_hash) or digest.startswith(str(expected_hash)) or full_digest == str(expected_hash):
                    plan_path = candidate
                    break
        if run_path is None and expected_hash:
            for candidate_path, run_doc in run_docs.values():
                if str(run_doc.get("plan_hash", "")) == str(expected_hash):
                    run_path = candidate_path
                    break
        # A supplied plan's parent is itself a complete scored run artifact,
        # even when the tournament assigned it a synthetic run_id.
        if run_path is None and plan_path is not None and (plan_path.parent / "result.json").is_file():
            run_path = plan_path.parent / "result.json"
        if plan_path is not None and run_path is not None:
            if isinstance(run_path, Path) and run_path.is_file():
                return rec, plan_path, json.loads(run_path.read_text())
            return rec, plan_path, run_docs[run_path.parent.name][1]
    raise FileNotFoundError("could not resolve a tournament Argon winner with both plan and result artifacts")


def _resolve_tournament_baseline(root: Path, tournament: dict, storm: str, winner_run_id: str | None):
    """Find the no-plan artifact from either the tournament file or the baseline index."""
    # Prefer the current tournament's embedded no-plan ledger.  A checkout may
    # contain an older ``runs/_baselines.json`` (including the historical
    # tens-of-billions mock), which must never override this tournament.
    if isinstance(tournament, dict):
        entries = tournament.get("entries") or []
        for rec in entries:
            if not isinstance(rec, dict):
                continue
            blob = " ".join(str(rec.get(k, "")) for k in ("model", "name", "label", "kind", "role", "scenario")).lower()
            if not any(term in blob for term in ("baseline", "no-plan", "noplan", "no plan")):
                continue
            causes = rec.get("by_cause") or {}
            by_cause = {}
            for cause, value in causes.items():
                if isinstance(value, dict):
                    by_cause[cause] = value.get("now", value.get("baseline", value.get("value", 0)))
                else:
                    by_cause[cause] = value
            total = rec.get("damage_usd")
            if total is not None and by_cause:
                return {"total_usd": total, "by_cause": by_cause}, {
                    "run_id": rec.get("run_id") or tournament.get("baseline_run_id"),
                    "mock": bool(tournament.get("mock_sim", False)), "storm": storm,
                    "source": "evidence/tournament.json baseline entry",
                }
    index = root / "runs" / "_baselines.json"
    if index.exists():
        try:
            entries = json.loads(index.read_text())
            baseline_id = entries.get(storm) or entries.get(f"{storm}-mock")
            candidate = root / "runs" / str(baseline_id) / "result.json"
            if baseline_id and candidate.exists():
                doc = json.loads(candidate.read_text())
                if str(doc.get("run_id", candidate.parent.name)) != str(winner_run_id):
                    return doc.get("ledger"), {"run_id": candidate.parent.name, "mock": bool(doc.get("mock")), "storm": doc.get("storm")}
        except Exception:
            pass
    for rec in _dicts(tournament):
        blob = " ".join(str(rec.get(k, "")) for k in ("model", "name", "label", "kind", "role", "scenario")).lower()
        if not any(term in blob for term in ("baseline", "no-plan", "noplan", "no plan")):
            continue
        rid = _first(rec, ("run_id", "baseline_run_id", "id"))
        if not rid or str(rid) == str(winner_run_id):
            continue
        rid = Path(str(rid).rstrip("/")).name
        candidate = root / "runs" / rid / "result.json"
        if candidate.exists():
            doc = json.loads(candidate.read_text())
            return doc.get("ledger"), {"run_id": rid, "mock": bool(doc.get("mock")), "storm": doc.get("storm")}
    return None, {}


def precompute_from_tournament(path: str | Path, repo: str | Path = REPO) -> dict:
    """Precompute an EOP for the Argon winner in a defensively-read tournament file."""
    root = Path(repo)
    tournament_path = Path(path)
    if not tournament_path.is_absolute():
        tournament_path = root / tournament_path
    tournament = json.loads(tournament_path.read_text())
    if not isinstance(tournament, (dict, list)):
        raise ValueError("tournament JSON must contain an object or list")
    record, plan_path, run_doc = _resolve_tournament_winner(root, tournament)
    result = dict(run_doc or {})
    for key in ("storm", "budget_usd", "budget", "plan_hash", "run_id", "best_run_id", "damage_avoided_usd"):
        if key in record and key not in result:
            result[key] = record[key]
    if isinstance(tournament, dict):
        result.setdefault("budget_usd", tournament.get("budget_usd"))
    result.setdefault("run_id", _first(record, ("run_id", "best_run_id")))
    storm = result.get("storm") or tournament.get("storm", "elnino_ar_2026") if isinstance(tournament, dict) else "elnino_ar_2026"
    result["storm"] = storm
    baseline, baseline_meta = _resolve_tournament_baseline(root, tournament, storm, result.get("run_id"))
    if baseline_meta:
        result["baseline_meta"] = baseline_meta
    job_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", f"tournament-argon-{tournament_path.stem}").strip("-")
    run_id = result.get("run_id")
    run_path = root / "runs" / str(run_id) if run_id and not Path(str(run_id)).is_absolute() else Path(str(run_id or ""))
    if not (run_path / "plan_objects.json").exists() and plan_path.parent != run_path:
        run_path = plan_path.parent
    objects = None
    if (run_path / "plan_objects.json").exists():
        payload = json.loads((run_path / "plan_objects.json").read_text())
        objects = payload.get("objects", payload) if isinstance(payload, dict) else payload
    output = write_eop(job_id, plan_path.read_text(), result, baseline=baseline, out_dir=root / "runs" / job_id,
                       precomputed_objects=objects)
    output["job_id"] = job_id
    output["winner"] = {"plan_path": str(plan_path), "run_id": result.get("run_id"),
                        "plan_hash": result.get("plan_hash") or hashlib.sha256(plan_path.read_text().strip().encode()).hexdigest()[:16]}
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Precompute a cited StormSim Emergency Operations Plan.")
    parser.add_argument("--from-tournament", metavar="PATH", help="tournament JSON containing a scored Argon winner")
    args = parser.parse_args(argv)
    if not args.from_tournament:
        parser.error("--from-tournament PATH is required")
    output = precompute_from_tournament(args.from_tournament)
    doc = output["document"]
    print(json.dumps({"job_id": output["job_id"], "eop_json": output["eop_json"], "eop_html": output["eop_html"],
                      "dispatch_status": doc.get("dispatch_status"), "plan_cost_usd": doc.get("summary", {}).get("plan_cost_usd"),
                      "budget_usd": doc.get("summary", {}).get("budget_usd"), "damage_avoided_usd": doc.get("summary", {}).get("damage_avoided_usd"),
                      "baseline_usd": doc.get("summary", {}).get("baseline_total_usd"), "top_actions": len(doc.get("top_actions", []))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
