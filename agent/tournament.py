"""Evidence-backed StormSim tournament for the hackathon demo.

All entries use the same storm, budget, locked cost planner and deterministic
runner.  The greedy entry is deliberately a plain ``def apply(city)`` plan:
each candidate is simulated on its own, ranked by damage avoided / sourced
cost, then the affordable candidates are concatenated in that order.

Run with ``scripts/gem.sh .venv/bin/python -m agent.tournament``.  The LLM
entries use the existing Evolve implementation; this module only imports it.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path

from agent import runner
from agent.events import new_job

REPO = Path(__file__).resolve().parent.parent
EVIDENCE = REPO / "evidence"
STORM = "elnino_ar_2026"
BUDGET = 50_000_000.0


def _hash(code: str | None) -> str:
    if not code:
        return "noplan"
    return hashlib.sha256("\n".join(x.rstrip() for x in code.strip().splitlines()).encode()).hexdigest()[:16]


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _box(p: dict, r: float = 230.0) -> list[list[float]]:
    return [[p["x"] - r, p["y"] - r], [p["x"] + r, p["y"] - r],
            [p["x"] + r, p["y"] + r], [p["x"] - r, p["y"] + r]]


def _plan(body: str) -> str:
    return "def apply(city):\n" + "\n".join("    " + line if line else "" for line in body.strip().splitlines()) + "\n"


def _candidate_pack() -> list[dict]:
    """Build a small, representative menu covering every major API family.

    Coordinates and IDs are read from the same CityEditor that plans receive;
    no data is hard-coded from a model response.  A failed candidate is kept in
    the evidence with its error instead of being silently dropped.
    """
    from sim.city_api import CityEditor

    c = CityEditor()
    low = c.low_points(3, min_sep_m=260)
    if not low:
        low = [{"x": 925.0, "y": 1218.0, "z": 0.0}]
    p0 = low[0]
    p1 = low[min(1, len(low) - 1)]
    b0 = _box(p0)
    b1 = _box(p1)
    buildings = c.buildings_in(b0)
    edu = c.buildings_in([[0, 0], [3548, 0], [3548, 4256], [0, 4256]], "EDU1")
    protected = buildings[: min(24, len(buildings))]
    trees = c.trees_in(b0, min_dbh_cm=30)[:160]
    inlets = c.inlets_in(b0)
    cars = c.cars_in(b0)
    streets = c.streets("FOLSOM") or c.streets("17TH")
    street = streets[0]["coords"] if streets else [[p0["x"] - 300, p0["y"]], [p0["x"] + 300, p0["y"]]]
    candidates: list[dict] = []

    def add(name: str, family: str, body: str):
        candidates.append({"name": name, "family": family, "body": body, "code": _plan(body)})

    add("Storage vault at lowest basin", "storage", f"city.storage({p0['x']:.1f}, {p0['y']:.1f}, volume_m3=12000, intake_m3s=3.0)")
    add("Pump station at lowest basin", "pump", f"city.pump({p0['x'] + 25:.1f}, {p0['y']:.1f}, m3s=2.0)")
    add("Relief tunnel to bay", "tunnel", f"city.tunnel([[{p0['x']:.1f}, {p0['y']:.1f}], [2300, {p0['y'] + 180:.1f}], [3450, {p0['y'] + 220:.1f}]], m3s=8.0)")
    if inlets:
        add("Upgrade basin inlets", "inlets", f"city.upgrade_inlets({inlets[:min(80, len(inlets))]!r}, factor=2.5)")
    add("New inlets at basin sag", "inlets", f"city.add_inlets([[{p0['x']:.1f}, {p0['y']:.1f}], [{p0['x'] + 40:.1f}, {p0['y'] + 20:.1f}]], capacity_m3s=0.08)")
    add("Permeable pavement upstream", "green", f"city.permeable({_box(p1, 260)!r}, ksat_mm_h=12.7)")
    add("Bioswale on flood approach", "green", f"city.bioswale({street!r}, width_m=2.0, depth_m=0.3)")
    if protected:
        add("Dry floodproof exposed buildings", "buildings", f"city.floodproof({protected!r}, height_m=0.9)")
        add("Green roofs on exposed buildings", "green", f"city.green_roof({protected[:min(12, len(protected))]!r}, retention_mm=25)")
    if cars:
        add("Tow cars from flood basin", "operations", f"city.tow_cars({_box(p0, 280)!r})")
    add("Clear basin drains", "operations", f"city.clear_drains({_box(p0, 280)!r})")
    add("Draw down all sewers", "operations", "city.sewer_drawdown(basins='all', frac=0.0)")
    if trees:
        add("Brace wind-exposed trees", "trees", f"city.brace({trees!r})")
    add("Wind screen on approach", "wind", f"city.wind_screen({street!r}, height_m=3.0, porosity=0.5)")
    add("Deployable bay barrier", "floodwall", "city.wall([[3300, 3650], [3500, 3900]], height_m=1.2, kind='deployable')")
    if edu:
        sid = edu[0]
        add("Early alert and evacuation", "response", f"r = city.response\nr.alert('Flood warning: move to shelter before landfall.', at_s=-12*3600)\nr.shelter({sid}, capacity=500, open_at_s=-8*3600, supplies={{'meals': 1500, 'water_gal': 500}})\nr.evacuate({_box(p0, 280)!r}, order_at_s=-4*3600, priority='vulnerable', transport='auto')")
    return candidates


def _entry_from_run(name: str, model: str | None, code: str | None, run: dict, base: dict,
                    budget: float, wall_s: float, kind: str = "plan") -> dict:
    if not run.get("ok"):
        return {"name": name, "kind": kind, "model": model, "plan_hash": _hash(code), "status": "failed",
                "error": run.get("error"), "wall_time_s": round(wall_s, 1)}
    s = runner.summarize(runner.load_result(Path(run["run_dir"])), base, budget)
    led = s.get("by_cause") or {}
    return {"name": name, "kind": kind, "model": model, "status": "scored", "run_id": run.get("run_id"),
            "plan_hash": _hash(code), "ledger_hash": _ledger_hash(run.get("run_dir")),
            "damage_usd": s.get("damage_total_usd"), "damage_avoided_usd": s.get("damage_avoided_usd"),
            "plan_cost_usd": s.get("plan_cost_usd", 0), "net_benefit_usd": s.get("net_benefit_usd"),
            "bcr": s.get("bcr"), "within_budget": s.get("within_budget", True), "by_cause": led,
            "people": s.get("people"), "wall_time_s": round(wall_s, 1)}


def _ledger_hash(run_dir: str | None) -> str | None:
    if not run_dir:
        return None
    led = runner.load_result(Path(run_dir)).get("ledger") or {}
    return hashlib.sha256(json.dumps(led, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:16]


def _run_candidate(c: dict, storm: str, budget: float, base: dict) -> tuple[dict, dict]:
    t0 = time.time()
    r = runner.execute_run(storm, c["code"], label=f"tournament greedy {c['name']}", budget=budget)
    rec = _entry_from_run(c["name"], None, c["code"], r, base, budget, time.time() - t0, "greedy_candidate")
    c = {**c, "result": rec}
    return c, rec


def _greedy(storm: str, budget: float, base_dir: Path, base: dict, workers: int = 3,
            candidate_limit: int | None = None) -> tuple[dict, str | None, list[dict]]:
    candidates = _candidate_pack()
    if candidate_limit and candidate_limit < len(candidates):
        # Keep a named, cross-hazard slice for the time-boxed live demo.  The
        # default menu still contains all families; this is a transparent
        # measurement cap, not a score-dependent filter.
        priority = ["Storage vault at lowest basin", "Pump station at lowest basin",
                    "Upgrade basin inlets", "Permeable pavement upstream",
                    "Dry floodproof exposed buildings", "Clear basin drains",
                    "Brace wind-exposed trees", "Early alert and evacuation",
                    "Deployable bay barrier"]
        by_name = {c["name"]: c for c in candidates}
        candidates = [by_name[n] for n in priority if n in by_name][:candidate_limit]
    # One-at-a-time measurements are independent and safe to run in worker threads;
    # the simulator itself controls its numerical determinism.
    with cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(candidates)))) as pool:
        measured = list(pool.map(lambda x: _run_candidate(x, storm, budget, base), candidates))
    measured = [x[0] for x in measured]
    viable = [c for c in measured if c["result"].get("status") == "scored" and
              float(c["result"].get("plan_cost_usd") or 0) > 0 and
              float(c["result"].get("damage_avoided_usd") or 0) > 0]
    viable.sort(key=lambda c: (float(c["result"].get("damage_avoided_usd") or 0) /
                               max(1.0, float(c["result"].get("plan_cost_usd") or 0))), reverse=True)
    chosen, spent = [], 0.0
    for c in viable:
        cost = float(c["result"].get("plan_cost_usd") or 0)
        if spent + cost <= budget:
            chosen.append(c)
            spent += cost
    body = "\n\n".join(c["body"] for c in chosen)
    code = _plan(body) if body else _plan("pass")
    t0 = time.time()
    r = runner.execute_run(storm, code, label="tournament fair greedy", budget=budget)
    rec = _entry_from_run("FAIR greedy multi-hazard", "deterministic greedy", code, r, base, budget, time.time() - t0, "greedy")
    rec["selected"] = [{"name": c["name"], "family": c["family"], "one_at_a_time": c["result"]} for c in chosen]
    rec["candidate_count"] = len(candidates)
    rec["candidate_measurements"] = [c["result"] for c in measured]
    rec["selection_rule"] = "descending one-at-a-time damage avoided / sourced cost; include while cumulative cost <= $50M"
    return rec, code, [c["result"] for c in measured]


def _evolve_entry(label: str, architect: str, storm: str, budget: float, generations: int,
                  population: int, max_minutes: float, base: dict, sim_workers: int) -> dict:
    from agent import evolve as evolve_module
    # Keep this comparison focused on deterministic ledger numbers and wall
    # time; Evolve's normal UI replay rendering can dominate a short run.
    evolve_module.render_video = lambda run_id: None
    Evolver = evolve_module.Evolver

    t0 = time.time()
    job = new_job("tournament-evolve", {"label": label, "model": architect})
    e = Evolver(job, storm=storm, budget=budget, generations=generations, population=population,
                mutator_model="gemini-3.8-flash", architect_model=architect, sim_workers=sim_workers,
                llm_workers=max(2, population), max_minutes=max_minutes, use_llm=True,
                import_seeds=False, thinking_architect="high", thinking_mutator="medium")
    try:
        final = e.run()
    except Exception as exc:
        return {"name": label, "kind": "evolve", "model": architect, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}", "wall_time_s": round(time.time() - t0, 1)}
    best_id = final.get("best_id")
    code = (e.dir / "best.plan.py").read_text() if best_id and (e.dir / "best.plan.py").exists() else None
    run_id = final.get("best_run_id")
    if not code or not run_id or not (REPO / "runs" / run_id / "result.json").exists():
        return {"name": label, "kind": "evolve", "model": architect, "status": "failed",
                "error": "Evolve completed without a scored best plan", "job_id": job.id,
                "wall_time_s": round(time.time() - t0, 1)}
    run = {"ok": True, "run_id": run_id, "run_dir": str(REPO / "runs" / run_id)}
    rec = _entry_from_run(label, architect, code, run, base, budget, time.time() - t0, "evolve")
    rec.update({"job_id": job.id, "evolve_final": {k: final.get(k) for k in ("n_candidates", "n_scored", "best_id", "lineage", "usage")},
                "generations": generations, "population": population})
    return rec


def _no_plan(base_dir: Path, base: dict, budget: float) -> dict:
    s = runner.summarize(base, base, budget)
    return {"name": "No plan", "kind": "baseline", "model": None, "status": "scored", "run_id": base_dir.name,
            "plan_hash": "noplan", "ledger_hash": _ledger_hash(str(base_dir)), "damage_usd": s.get("damage_total_usd"),
            "damage_avoided_usd": 0, "plan_cost_usd": 0, "net_benefit_usd": 0, "bcr": None,
            "within_budget": True, "by_cause": s.get("by_cause") or {}, "wall_time_s": 0}


def _write_report(evidence: dict):
    entries = evidence.get("entries", [])
    lines = ["# StormSim SF tournament evidence", "", f"Storm: `{evidence['storm']}`; budget: **$50M**; all entries use the locked cost planner.", "",
             evidence.get("headline", "Headline pending: the model runs are still in progress."), "",
             "The greedy rule measures each candidate intervention as a standalone plan, ranks by damage avoided per sourced dollar, and fills the budget in that order. It is a normal `def apply(city)` plan scored by the same runner. The table is reported as measured, including a greedy win or tie if that is what this storm produces.", "",
             "| Entry | Model | Damage | Avoided | Cost | Net benefit | BCR | Run |", "|---|---|---:|---:|---:|---:|---:|---|"]
    for e in entries:
        lines.append(f"| {e.get('name')} | {e.get('model') or 'none'} | ${float(e.get('damage_usd') or 0)/1e6:.1f}M | ${float(e.get('damage_avoided_usd') or 0)/1e6:.1f}M | ${float(e.get('plan_cost_usd') or 0)/1e6:.1f}M | ${float(e.get('net_benefit_usd') or 0)/1e6:.1f}M | {e.get('bcr') if e.get('bcr') is not None else '—'} | `{e.get('run_id', '—')}` |")
    lines += ["", f"Generated: `{evidence.get('finished_utc')}`", "", "Per-cause deltas, candidate measurements, plan hashes, ledger hashes and model run IDs are in [`tournament.json`](tournament.json)."]
    (EVIDENCE / "TOURNAMENT.md").write_text("\n".join(lines) + "\n")


def run_tournament(storm: str = STORM, budget: float = BUDGET, greedy_workers: int = 3,
                   generations: int = 1, population: int = 3, max_minutes: float = 12,
                   sim_workers: int = 2, candidate_limit: int | None = 8,
                   no_llm: bool = False) -> dict:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # The headline evidence needs ledgers and hashes, not replay media.  Avoid
    # rendering a video for every one-at-a-time greedy measurement; the normal
    # UI/run pipeline still renders videos when called outside this harness.
    runner.ensure_video = lambda run_dir, log=None: None
    started = _utc()
    base_dir, base = runner.baseline(storm)
    entries = [_no_plan(base_dir, base, budget)]
    greedy, _, _ = _greedy(storm, budget, base_dir, base, greedy_workers, candidate_limit)
    entries.append(greedy)
    if not no_llm:
        # Same Evolve settings/call budget for both model conditions.  The Flash
        # condition uses Flash as both architect and mutator; Argon changes only
        # the architect, making the comparison legible.
        for label, architect in (("Gemini 3.8 Flash alone (Evolve)", "gemini-3.8-flash"),
                                 ("Gemini 4 Argon (Evolve architect)", "gemini-4-argon")):
            entries.append(_evolve_entry(label, architect, storm, budget, generations, population,
                                          max_minutes, base, sim_workers))
    evidence = {"schema_version": 1, "storm": storm, "budget_usd": budget, "started_utc": started,
                "finished_utc": _utc(), "mock_sim": runner.get_run_storm()[1], "baseline_run_id": base_dir.name,
                "entries": entries}
    greedy_rec = next((e for e in entries if e.get("kind") == "greedy"), None)
    argon_rec = next((e for e in entries if e.get("model") == "gemini-4-argon"), None)
    if argon_rec and greedy_rec and argon_rec.get("status") == greedy_rec.get("status") == "scored":
        delta = float(argon_rec.get("damage_avoided_usd") or 0) - float(greedy_rec.get("damage_avoided_usd") or 0)
        verb = "more" if delta >= 0 else "less"
        evidence["headline"] = f"Argon avoids ${abs(delta) / 1e6:.1f}M {verb} damage than the best no-AI rule at the same $50M budget."
    else:
        evidence["headline"] = "Argon vs. the best no-AI rule: model run incomplete; see the scored entries and hashes below."
    (EVIDENCE / "tournament.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    _write_report(evidence)
    return evidence


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--storm", default=STORM)
    ap.add_argument("--budget", type=float, default=BUDGET)
    ap.add_argument("--greedy-workers", type=int, default=3)
    ap.add_argument("--generations", type=int, default=1)
    ap.add_argument("--population", type=int, default=3)
    ap.add_argument("--max-minutes", type=float, default=12)
    ap.add_argument("--sim-workers", type=int, default=2)
    ap.add_argument("--candidate-limit", type=int, default=8)
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    result = run_tournament(**vars(a))
    print(json.dumps({"evidence": str(EVIDENCE / "tournament.json"), "entries": result["entries"]}, indent=1, default=str))


if __name__ == "__main__":
    main()
