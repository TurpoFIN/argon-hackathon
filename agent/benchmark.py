"""Benchmark: identical storm, budget, tools, time cap, data pack and deterministic sim for every model.

Entries run in parallel (one optimizer job each, followable over SSE) + two fixed baselines:
"no plan" and "walls everywhere". Writes runs/benchmark/leaderboard.json (served at /api/leaderboard).
CLI: scripts/gem.sh .venv/bin/python -m agent.benchmark --budget 50e6 --max-minutes 15
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from agent import runner
from agent.events import Job, new_job, start_thread
from agent.optimizer import optimize, rel, ledger_hash, sha

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "runs" / "benchmark"

DEFAULT_ENTRIES = [
    {"name": "Gemini 4 Argon (high)", "model": "gemini-4-argon", "thinking_level": "high"},
    {"name": "Gemini 4 Argon (medium)", "model": "gemini-4-argon", "thinking_level": "medium"},
    {"name": "Gemini 3.8 Flash", "model": "gemini-3.8-flash", "thinking_level": None},
    {"name": "Gemini 3.5 Flash", "model": "gemini-3.5-flash", "thinking_level": None},
    {"name": "Gemini 3.5 Flash-Lite", "model": "gemini-3.5-flash-lite", "thinking_level": None},
]

WALLS_EVERYWHERE = '''
# Baseline "walls everywhere": a 1.2 m floodwall around every no-plan flood basin's bounding box,
# no targeting, no emergency response. Deterministic; priced by the same cost planner.
BASINS = {basins}
def apply(city):
    for (x0, y0, x1, y1) in BASINS:
        city.wall([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], 1.2)
    city.log("walls-everywhere baseline")
'''


def walls_plan(base_dir) -> str:
    from agent.tools import basins
    bs = [tuple(b["bbox_xy"]) for b in basins(str(base_dir))[:12]]
    return WALLS_EVERYWHERE.format(basins=json.dumps(bs))


def _fixed_entry(name, storm, plan_code, budget, base_dir, base, log):
    t0 = time.time()
    if plan_code is None:
        s = runner.summarize(base, base, budget)
        return {"name": name, "model": None, "kind": "baseline", "run_id": base_dir.name, "plan_cost_usd": 0,
                "damage_usd": s["damage_total_usd"], "damage_avoided_usd": 0, "net_benefit_usd": 0, "bcr": None,
                "runs_used": 0, "wall_clock_s": 0, "ledger_hash": ledger_hash(str(base_dir)), "plan_hash": None,
                "video_url": rel(runner.ensure_video(base_dir))}
    r = runner.execute_run(storm, plan_code, label=name, budget=budget, log=log)
    if not r.get("ok"):
        return {"name": name, "kind": "baseline", "error": r.get("error")}
    s = r["summary"]
    return {"name": name, "model": None, "kind": "baseline", "run_id": r["run_id"], "plan_cost_usd": s.get("plan_cost_usd"),
            "damage_usd": s.get("damage_total_usd"), "damage_avoided_usd": s.get("damage_avoided_usd"),
            "net_benefit_usd": s.get("net_benefit_usd"), "bcr": s.get("bcr"), "runs_used": 1,
            "wall_clock_s": round(time.time() - t0, 1), "plan_hash": sha(plan_code), "ledger_hash": ledger_hash(r["run_dir"]),
            "over_budget": s.get("plan_cost_usd", 0) > budget, "video_url": rel(r.get("video")), "people": s.get("people"),
            "life_safety_avoided_usd": -next((v["delta"] for k, v in (s.get("by_cause") or {}).items() if "life" in k), 0)}


def benchmark_job(job: Job, cfg: dict) -> dict:
    storm = cfg.get("storm", "elnino_ar_2026")
    budget = float(cfg.get("budget_usd", 50e6))
    max_runs = int(cfg.get("max_runs", 6))
    max_minutes = float(cfg.get("max_minutes", 15))
    pack = cfg.get("pack", "full")
    entries = cfg.get("entries") or DEFAULT_ENTRIES
    OUT.mkdir(parents=True, exist_ok=True)
    log = lambda t, **k: job.emit(t, **k)  # noqa: E731
    base_dir, base = runner.baseline(storm, log)
    # build the pack once so every model gets byte-identical data
    pack_meta = None
    if pack != "none":
        from agent.datapack import build_pack
        _, pack_meta = build_pack(storm, base_dir, level=pack)
        pack_meta = {k: v for k, v in pack_meta.items() if k != "model"}
    job.emit("benchmark_start", storm=storm, budget_usd=budget, max_runs=max_runs, max_minutes=max_minutes,
             entries=[e["name"] for e in entries], pack=pack_meta, baseline_run_id=base_dir.name,
             mock_sim=runner.get_run_storm()[1])
    board = {"storm": storm, "budget_usd": budget, "max_runs": max_runs, "max_minutes": max_minutes,
             "pack": pack_meta, "baseline_run_id": base_dir.name, "mock_sim": runner.get_run_storm()[1],
             "started": time.time(), "entries": [], "status": "running", "benchmark_job": job.id}
    lock = threading.Lock()

    def save():
        board["entries"].sort(key=lambda e: -(e.get("net_benefit_usd") if e.get("net_benefit_usd") is not None else -1e18))
        (OUT / "leaderboard.json").write_text(json.dumps(board, indent=1, default=str))

    def add(entry):
        with lock:
            board["entries"] = [e for e in board["entries"] if e["name"] != entry["name"]] + [entry]
            save()
        job.emit("leaderboard_entry", entry=entry)

    add(_fixed_entry("No plan", storm, None, budget, base_dir, base, log))
    threads = []

    def run_model(e):
        sub = new_job("optimize", {"model": e["model"], "benchmark": job.id, "name": e["name"]})
        job.emit("benchmark_model_start", name=e["name"], model=e["model"], job_id=sub.id,
                 events_url=f"/api/jobs/{sub.id}/events")
        add({"name": e["name"], "model": e["model"], "job_id": sub.id, "status": "running"})
        sub.status = "running"
        try:
            rep = optimize(sub, e["model"], storm, budget, max_runs=max_runs, max_minutes=max_minutes,
                           thinking_level=e.get("thinking_level"), pack=pack, write_eop=bool(cfg.get("write_eop", False)),
                           watch_video=True, screenshots=True)
            sub.finish("completed", rep)
            u = rep.get("usage") or {}
            add({"name": e["name"], "model": e["model"], "thinking": e.get("thinking_level") or "medium/high",
                 "job_id": sub.id, "status": "done", "submitted": rep.get("submitted"), "run_id": rep.get("run_id"),
                 "plan_cost_usd": rep.get("plan_cost_usd"), "damage_usd": rep.get("damage_usd"),
                 "damage_avoided_usd": rep.get("damage_avoided_usd"), "net_benefit_usd": rep.get("net_benefit_usd"),
                 "bcr": rep.get("bcr"), "life_safety_avoided_usd": rep.get("life_safety_avoided_usd"),
                 "at_risk_person_hours_avoided": rep.get("at_risk_person_hours_avoided"),
                 "expected_fatalities_avoided": rep.get("expected_fatalities_avoided"),
                 "runs_used": rep.get("runs_used"), "wall_clock_s": rep.get("wall_clock_s"),
                 "tokens": {k: u.get(k) for k in ("input_tokens", "output_tokens", "thought_tokens", "cached_tokens", "turns")},
                 "plan_lines": rep.get("plan_lines"), "plan_chars": rep.get("plan_chars"), "plan_hash": rep.get("plan_hash"),
                 "ledger_hash": rep.get("ledger_hash"), "over_budget": rep.get("over_budget"), "video_url": rep.get("video_url"),
                 "plan_path": rep.get("plan_path"), "pack": rep.get("pack")})
        except Exception as ex:
            sub.finish("failed", {"error": str(ex)})
            add({"name": e["name"], "model": e["model"], "job_id": sub.id, "status": "failed", "error": f"{type(ex).__name__}: {ex}"})

    for e in entries:
        th = threading.Thread(target=run_model, args=(e,), daemon=True)
        th.start()
        threads.append(th)
    add(_fixed_entry("Walls everywhere", storm, walls_plan(base_dir), budget, base_dir, base, log))
    for th in threads:
        th.join()
    board["status"] = "done"
    board["finished"] = time.time()
    with lock:
        save()
    return {"leaderboard": "/runs/benchmark/leaderboard.json", "entries": len(board["entries"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", default="elnino_ar_2026")
    ap.add_argument("--budget", type=float, default=50e6)
    ap.add_argument("--max-runs", type=int, default=6)
    ap.add_argument("--max-minutes", type=float, default=15)
    ap.add_argument("--pack", default="full")
    ap.add_argument("--models", default=None, help="comma list of entry names' models to include, e.g. gemini-3.5-flash-lite")
    a = ap.parse_args()
    entries = DEFAULT_ENTRIES
    if a.models:
        want = a.models.split(",")
        entries = [e for e in DEFAULT_ENTRIES if e["model"] in want]
    job = new_job("benchmark", vars(a))
    orig = job.emit

    def emit(type, **d):
        ev = orig(type, **d)
        if type not in ("thought", "text", "code_delta", "plan_object"):
            print(f"[{time.strftime('%H:%M:%S')}] {type}: {json.dumps(d, default=str)[:240]}", flush=True)
        return ev
    job.emit = emit
    res = benchmark_job(job, {"storm": a.storm, "budget_usd": a.budget, "max_runs": a.max_runs,
                              "max_minutes": a.max_minutes, "pack": a.pack, "entries": entries})
    print(json.dumps(res))
    print((OUT / "leaderboard.json").read_text()[:3000])


if __name__ == "__main__":
    main()
