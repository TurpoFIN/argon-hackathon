"""What Gemini was given, what it is trying to do, what actions it has, and what it did.

build_briefing(job_id) -> dict, written to runs/_jobs/<job>.briefing.json (served at /api/jobs/<id>/briefing and
/runs/_jobs/<job>.briefing.json). Built from the job's JSONL + data pack meta + sim.city_api / cost tables, so it
works for running, finished and CLI jobs alike.
CLI: .venv/bin/python -m agent.briefing <job_id> [<job_id> ...]
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JOBS = REPO / "runs" / "_jobs"
REF = REPO / "data" / "reference"

INPUT_DESC = {
    "storm": ("Storm forcing", "rain mm/h, gusts, wind direction, tide every 15 min + citations"),
    "ledger": ("No-plan damage ledger", "damage $ by cause, people at risk, validation vs 31.12.2022"),
    "basins": ("Flood basins", "no-plan flooded basins: area, volume, max depth, damage"),
    "buildings": ("Buildings", "every building: occupancy, floors, ground z, first floor, value, no-plan depth + damage"),
    "shelters": ("Shelter candidates", "schools/government/community buildings with FEMA capacity and flood exposure"),
    "inlets": ("Storm-drain inlets", "inlets in flooded areas: capacity, basin, no-plan depth"),
    "streets": ("Street segments", "centrelines: low points and no-plan max depth"),
    "trees": ("Street trees", "failures by street + every failed tree (species, DBH, failure time/mode)"),
    "people": ("Population + vulnerability", "residents and flood-health vulnerability by census block group"),
    "reference": ("Locked cost & parameter tables", "unit costs, response costs, damage functions, people/water/wind params"),
    "documents": ("SF's own plans + Deep Research", "HCRP 2020, SFPUC flood map, ERP + annexes, Folsom project, SSIP, ... + cited Deep Research report"),
}
COUNTS = {"buildings": "counts.buildings", "inlets": "counts.inlets", "trees": "counts.trees"}


def _events(job_id):
    p = JOBS / f"{job_id}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _cost_basis():
    out = {}
    for name in ("unit_costs.json", "response_costs.json"):
        p = REF / name
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        items = d.get("items", d) if isinstance(d, dict) else {}
        for k, v in items.items():
            if isinstance(v, dict) and "usd" in v:
                out[k] = {"item": k, "usd": v.get("usd"), "unit": v.get("unit"), "source": v.get("source") or v.get("url"),
                          "url": v.get("url")}
    return out


ACTION_COST_HINTS = {  # helper -> cost table item ids (best effort substring match)
    "wall": "floodwall", "sandbags": "sandbag", "berm": "earthworks", "regrade": "earthworks", "permeable": "permeable",
    "bioswale": "bioswale", "green_roof": "green_roof", "storage": "storage", "pump": "pump", "tunnel": "tunnel",
    "upgrade_inlets": "inlet", "add_inlets": "inlet", "floodproof": "floodproof", "prune": "prune", "brace": "brac",
    "remove_trees": "tree_remov", "replace_trees": "tree_replac", "wind_screen": "wind_screen", "clear_drains": "drain",
    "sewer_drawdown": "sewer", "tow_cars": "tow", "sandbag_giveaway": "sandbag", "deploy_portable_pumps": "portable",
    "pre_position": "", "action": "", "shelter": "cot", "evacuate": "bus", "depot": "", "supply_run": "truck",
    "crew": "crew", "close_road": "barricade", "alert": "", "generator": "generator", "medical_post": "medical",
}


def _actions():
    from agent.citylib import category_of
    costs = _cost_basis()
    acts = []
    try:
        import sim.city_api as ca
        from agent.citylib import _QUERY_METHODS
        targets = [("city", ca.CityEditor)]
        try:
            import sim.response as rs
            cls = next((c for _, c in inspect.getmembers(rs, inspect.isclass) if hasattr(c, "shelter") and hasattr(c, "evacuate")), None)
            if cls:
                targets.append(("city.response", cls))
        except Exception:
            pass
        for prefix, cls in targets:
            for n, f in inspect.getmembers(cls):
                if n.startswith("_") or n in _QUERY_METHODS or not callable(f) or isinstance(inspect.getattr_static(cls, n), property):
                    continue
                try:
                    sig = str(inspect.signature(f)).replace("(self, ", "(").replace("(self)", "()")
                except Exception:
                    sig = "(...)"
                doc = (inspect.getdoc(f) or "").split("\n")[0][:240]
                hint = ACTION_COST_HINTS.get(n, n)
                basis = [v for k, v in costs.items() if hint and hint in k][:3]
                acts.append({"category": category_of({"type": n}) if prefix == "city" else
                             ("logistics" if n in ("depot", "supply_run") else "emergency" if n not in ("close_road", "crew") else "operations"),
                             "name": f"{prefix}.{n}", "signature": f"{prefix}.{n}{sig}", "what_it_does": doc,
                             "cost_basis": basis})
    except Exception as e:
        acts.append({"error": f"introspection failed: {e}"})
    acts.append({"category": "operations", "name": "raw arrays", "signature": "city.z / city.ksat / city.manning / city.cover [...]",
                 "what_it_does": "edit terrain, infiltration, roughness and land cover directly; priced from the diff (earthworks, repaving)",
                 "cost_basis": [v for k, v in costs.items() if k in ("earthworks_m3", "repave_m2")]})
    return acts


def build_briefing(job_id: str) -> dict:
    ev = _events(job_id)
    if not ev:
        raise FileNotFoundError(job_id)
    start = next((e for e in ev if e["type"] == "job_start"), {})
    model = start.get("model") or next((e.get("model") for e in ev if e.get("model")), None)
    budget = start.get("budget_usd")
    storm = start.get("storm", "elnino_ar_2026")
    base = next((e for e in ev if e["type"] == "baseline"), {})
    bs = base.get("summary") or {}
    pack = next((e for e in ev if e["type"] == "datapack"), {})
    try:
        from agent.optimizer import system_prompt
        goal = system_prompt(budget or 50e6, storm, start.get("max_runs", 8), start.get("max_minutes", 15)).split("Rules (hard)")[0].strip()
    except Exception:
        goal = f"Cheapest effective complete storm plan for {storm} within ${budget:,.0f}: maximise damage avoided minus cost, protect lives."
    alerts = None
    try:
        a = json.loads((REPO / "data" / "raw" / "nws_alerts_now.json").read_text())
        alerts = [f["properties"].get("headline") for f in a.get("features", [])][:3]
    except Exception:
        pass
    meta = {}
    try:
        meta = json.loads((REPO / "data" / "grid" / "meta.json").read_text())
    except Exception:
        pass
    inputs = []
    secs = pack.get("sections") or {}
    for k, chars in secs.items():
        name, what = INPUT_DESC.get(k, (k, ""))
        cnt = None
        if k in COUNTS:
            cnt = (meta.get("counts") or {}).get(k)
        inputs.append({"name": name, "what": what, "count": cnt, "chars": chars, "tokens": int(chars / 1.4),
                       "source": "data/grid + no-plan run" if k not in ("documents", "reference") else
                       ("agent/pack_cache/sf_plans + Deep Research" if k == "documents" else "data/reference/*.json")})
    try:
        idx = json.loads((REPO / "agent" / "pack_cache" / "sf_plans" / "index.json").read_text())
        docs = [d["title"] for d in idx if not d.get("missing")]
    except Exception:
        docs = []
    inputs.append({"name": "No-plan replay video", "what": "24 s top-down + oblique render of the storm hitting the city", "source": f"/runs/{base.get('run_id')}/video.mp4"})
    # runs + critiques
    runs, cur_text, last_run = [], [], None
    critiques = {e.get("run"): e for e in ev if e["type"] == "critique"}
    for e in ev:
        if e["type"] == "run_result":
            s = e.get("summary") or {}
            ppl = (s.get("people") or {})
            risk = next((v.get("now") for k, v in ppl.items() if "at_risk" in k or "fatal" in k), None)
            runs.append({"run": e.get("run_index"), "run_id": e.get("run_id"), "damage_usd": s.get("damage_total_usd"),
                         "plan_cost_usd": s.get("plan_cost_usd"), "bcr": s.get("bcr"), "lives_at_risk": risk,
                         "video_url": e.get("video_url"), "critique": None, "change_made": None})
            last_run, cur_text = runs[-1], []
        elif e["type"] == "run_error":
            runs.append({"run": e.get("run_index"), "error": e.get("error"), "critique": None, "change_made": None})
            last_run, cur_text = runs[-1], []
        elif e["type"] in ("text",) and last_run is not None:
            cur_text.append(e.get("text", ""))
        elif e["type"] == "tool_call" and e.get("name") in ("run_storm", "submit") and last_run is not None:
            t = "".join(cur_text).strip()
            last_run["critique"] = last_run["critique"] or (t[:600] if t else None)
            last_run["change_made"] = (e.get("args") or {}).get("note") or ("submitted" if e.get("name") == "submit" else None)
            last_run, cur_text = None, []
    for r in runs:
        c = critiques.get(r.get("run"))
        if c:
            r["critique"], r["change_made"] = c.get("text") or r["critique"], c.get("change") or r["change_made"]
    sub = next((e for e in reversed(ev) if e["type"] == "submit"), None)
    plan_items = []
    rid = (sub or {}).get("run_id") or (runs[-1].get("run_id") if runs else None)
    if rid and (REPO / "runs" / rid / "plan_objects.json").exists():
        d = json.loads((REPO / "runs" / rid / "plan_objects.json").read_text())
        for o in (d.get("objects") if isinstance(d, dict) else d)[:600]:
            plan_items.append({"name": o.get("label"), "category": o.get("category"), "cost_usd": o.get("cost_usd"),
                               "why": o.get("notes"), "phase": o.get("phase"), "t_s": o.get("t_s"), "id": o.get("id")})
    tot = sum(i.get("tokens") or 0 for i in inputs)
    b = {"job_id": job_id, "model": model, "goal": goal, "budget_usd": budget,
         "storm": {"name": storm, "no_plan_damage_usd": bs.get("damage_total_usd"), "no_plan_people": bs.get("people"),
                   "baseline_run_id": base.get("run_id"), "alerts": alerts},
         "inputs": inputs, "documents": docs, "total_tokens": tot, "pack": pack.get("summary"),
         "actions": _actions(), "strategy": (sub or {}).get("rationale"),
         "assessment": next((e.get("markdown") for e in ev if e["type"] == "document" and e.get("kind") == "assessment"), None),
         "plan_items": plan_items, "runs": runs, "submitted_run_id": (sub or {}).get("run_id"),
         "status": "submitted" if sub else "in_progress"}
    (JOBS / f"{job_id}.briefing.json").write_text(json.dumps(b, indent=1, default=str))
    return b


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    for j in sys.argv[1:]:
        b = build_briefing(j)
        print(j, b["model"], "inputs", len(b["inputs"]), "tokens", b["total_tokens"], "actions", len(b["actions"]),
              "runs", len(b["runs"]), "plan_items", len(b["plan_items"]), b["status"])
