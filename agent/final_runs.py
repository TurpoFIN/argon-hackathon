"""Final evaluation on the FROZEN sim: fresh no-plan baseline + walls-everywhere + each model's final plan,
all in parallel (STORMSIM_REMOTE=1 -> Daytona/Fly). Writes runs/benchmark/leaderboard.json,
runs/benchmark/final_runs.json (run ids by role, for the web) and re-registers the baseline in runs/_baselines.json.

CLI: STORMSIM_REMOTE=1 scripts/gem.sh .venv/bin/python -m agent.final_runs --job argon=optimize-... --job flash=optimize-...
A job's plan = runs/_jobs/<job>.plan.py (submitted) else the last plan_code it ran (from its JSONL).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from pathlib import Path

from agent import runner
from agent.benchmark import walls_plan

REPO = Path(__file__).resolve().parent.parent
JOBS = REPO / "runs" / "_jobs"
OUT = REPO / "runs" / "benchmark"
NAMES = {"gemini-4-argon": "Gemini 4 Argon", "gemini-3.8-flash": "Gemini 3.8 Flash", "gemini-3.5-flash": "Gemini 3.5 Flash",
         "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite"}


def job_plan(job_id: str) -> tuple[str | None, dict]:
    info = {"job_id": job_id}
    fin = JOBS / f"{job_id}.final.json"
    if fin.exists():
        f = json.loads(fin.read_text())
        info.update({k: f.get(k) for k in ("model", "runs_used", "usage", "wall_clock_s", "submitted", "rationale", "pack")})
    p = JOBS / f"{job_id}.plan.py"
    if p.exists():
        info["plan_source"] = "submitted"
        return p.read_text(), info
    code, model = None, None
    for line in (JOBS / f"{job_id}.jsonl").read_text().splitlines():
        e = json.loads(line)
        model = e.get("model") or model
        if e.get("type") == "code" and e.get("code"):
            code = e["code"]
    info.setdefault("model", model)
    info["plan_source"] = "last run_storm plan (not submitted)"
    return code, info


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def lhash(rd):
    led = runner.load_result(Path(rd)).get("ledger") or {}
    return hashlib.sha256(json.dumps(led, sort_keys=True, default=str).encode()).hexdigest()[:16]


def run_one(role, storm, code, label, out, log):
    t0 = time.time()
    try:
        plan_dir = None
        if code:
            from server.sandbox import run_plan
            plan_dir = runner.PLANS / f"final-{role}-{int(t0)}"
            sb = run_plan(code, out_dir=plan_dir, timeout=180)
            if not sb.get("ok"):
                out[role] = {"error": f"plan failed in sandbox: {sb.get('error')}"}
                return
        rd = runner.call_run_storm(storm, code, label, str(plan_dir) if plan_dir else None)
        if plan_dir:
            from agent.citylib import annotate_run_objects
            annotate_run_objects(rd, plan_dir)
        runner.ensure_video(rd)
        out[role] = {"run_dir": str(rd), "run_id": rd.name, "secs": round(time.time() - t0, 1),
                     "plan_hash": sha(code) if code else None, "ledger_hash": lhash(rd)}
        log(f"{role} done: {rd.name} in {time.time()-t0:.0f}s")
    except Exception as e:
        out[role] = {"error": f"{type(e).__name__}: {e}"}
        log(f"{role} FAILED: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", default="elnino_ar_2026")
    ap.add_argument("--budget", type=float, default=50e6)
    ap.add_argument("--job", action="append", default=[], help="role=job_id, e.g. argon=optimize-215118-1866e8")
    ap.add_argument("--no-walls", action="store_true")
    a = ap.parse_args()
    log = lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)  # noqa: E731
    _, is_mock = runner.get_run_storm()
    log(f"sim: {runner.get_run_storm()[0].__module__} mock={is_mock}")
    base_dir_old, _ = runner.baseline(a.storm)
    tasks = {"baseline": (None, f"FINAL baseline {a.storm}", {})}
    if not a.no_walls:
        tasks["walls"] = (walls_plan(base_dir_old), "FINAL walls everywhere", {"name": "Walls everywhere"})
    for spec in a.job:
        role, job_id = spec.split("=", 1)
        code, info = job_plan(job_id)
        if not code:
            log(f"{role}: no plan found in {job_id}")
            continue
        name = NAMES.get(info.get("model"), info.get("model") or role)
        if any(m.get("name") == name for _, _, m in tasks.values()):
            name = f"{name} ({job_id[-6:]})"
        tasks[role] = (code, f"FINAL {name}", {"name": name, **info})
    out: dict = {}
    ths = [threading.Thread(target=run_one, args=(r, a.storm, c, lbl, out, log)) for r, (c, lbl, _) in tasks.items()]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    if "run_dir" not in out.get("baseline", {}):
        log(f"baseline failed: {out.get('baseline')}")
        return
    bdir = Path(out["baseline"]["run_dir"])
    base = runner.load_result(bdir)
    idx_p = REPO / "runs" / "_baselines.json"
    idx = json.loads(idx_p.read_text()) if idx_p.exists() else {}
    idx[a.storm] = bdir.name
    idx_p.write_text(json.dumps(idx, indent=1))
    entries = [{"name": "No plan", "kind": "baseline", "run_id": bdir.name, "damage_usd": runner._ledger(base).get("total_usd"),
                "plan_cost_usd": 0, "damage_avoided_usd": 0, "net_benefit_usd": 0, "bcr": None,
                "ledger_hash": out["baseline"]["ledger_hash"], "people": runner.summarize(base, None).get("people")}]
    for role, (code, lbl, meta) in tasks.items():
        if role == "baseline":
            continue
        r = out.get(role, {})
        if "run_dir" not in r:
            entries.append({"name": meta.get("name", role), "role": role, "error": r.get("error")})
            continue
        s = runner.summarize(runner.load_result(Path(r["run_dir"])), base, a.budget)
        bc = s.get("by_cause") or {}
        ppl = s.get("people") or {}

        def av(k):
            d = ppl.get(k) or {}
            try:
                return round((d.get("baseline") or 0) - (d.get("now") or 0), 2)
            except Exception:
                return None
        u = meta.get("usage") or {}
        entries.append({"name": meta.get("name", role), "role": role, "model": meta.get("model"),
                        "kind": "baseline" if role == "walls" else "model", "run_id": r["run_id"],
                        "job_id": meta.get("job_id"), "plan_source": meta.get("plan_source"),
                        "plan_cost_usd": s.get("plan_cost_usd"), "damage_usd": s.get("damage_total_usd"),
                        "damage_avoided_usd": s.get("damage_avoided_usd"), "net_benefit_usd": s.get("net_benefit_usd"),
                        "bcr": s.get("bcr"), "within_budget": s.get("within_budget"),
                        "life_safety_avoided_usd": -next((v["delta"] for k, v in bc.items() if "life" in k), 0),
                        "at_risk_person_hours_avoided": av("at_risk_person_hours"),
                        "expected_fatalities_avoided": av("expected_fatalities"),
                        "runs_used": meta.get("runs_used"), "wall_clock_s": meta.get("wall_clock_s"),
                        "tokens": {k: u.get(k) for k in ("input_tokens", "output_tokens", "thought_tokens", "cached_tokens", "turns")} if u else None,
                        "plan_lines": code.count("\n") + 1 if code else None, "plan_chars": len(code) if code else None,
                        "plan_hash": r["plan_hash"], "ledger_hash": r["ledger_hash"],
                        "video_url": f"/runs/{r['run_id']}/video.mp4"})
    entries.sort(key=lambda e: -(e.get("net_benefit_usd") if e.get("net_benefit_usd") is not None else -1e18))
    OUT.mkdir(parents=True, exist_ok=True)
    board = {"storm": a.storm, "budget_usd": a.budget, "final": True, "frozen_sim": True, "mock_sim": is_mock,
             "baseline_run_id": bdir.name, "finished": time.time(), "status": "done", "entries": entries}
    (OUT / "leaderboard.json").write_text(json.dumps(board, indent=1, default=str))
    roles = {role: out[role]["run_id"] for role in out if "run_id" in out[role]}
    (OUT / "final_runs.json").write_text(json.dumps({"storm": a.storm, "runs": roles,
                                                     "labels": {r: tasks[r][1] for r in roles}}, indent=1))
    log("leaderboard written")
    for e in entries:
        print(f"{e['name']:<24} cost ${(e.get('plan_cost_usd') or 0)/1e6:7.2f}M  damage ${(e.get('damage_usd') or 0)/1e6:8.2f}M  "
              f"avoided ${(e.get('damage_avoided_usd') or 0)/1e6:7.2f}M  net ${(e.get('net_benefit_usd') or 0)/1e6:7.2f}M  "
              f"BCR {e.get('bcr')}  run {e.get('run_id')} {e.get('error') or ''}")


if __name__ == "__main__":
    main()
