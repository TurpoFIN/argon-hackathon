"""Run a storm with a (sandboxed) plan and summarise the result for models and the UI.

Pipeline: plan_code -> server.sandbox.run_plan (no net/secrets) -> edited city export in plan_dir
-> sim.run.run_storm(storm, plan_code=..., label=..., plan_dir=...) (mock until it exists)
-> runs/<id>/result.json (+ video.mp4 via sim.render_video if missing) -> summary vs. baseline.
"""
from __future__ import annotations

import inspect
import json
import os
import threading
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"
GRID = REPO / "data" / "grid"
PLANS = RUNS / "_plans"
_baseline_lock = threading.Lock()
try:
    _m = json.loads((GRID / "meta.json").read_text())
    X0, Y0 = float(_m["x0"]), float(_m["y0"])  # outputs use LOCAL metres from grid SW (city API convention)
except Exception:
    X0 = Y0 = 0.0
_buildings = None


def get_run_storm():
    """(fn, is_mock). Real sim.run.run_storm unless STORMSIM_MOCK=1 or it doesn't exist yet."""
    if os.environ.get("STORMSIM_MOCK") != "1" and os.environ.get("STORMSIM_REMOTE") == "1":
        try:
            from sim.remote import run_storm_remote  # Daytona / Fly via drun; same ledger_hash as local
            return run_storm_remote, False
        except ImportError:
            pass
    if os.environ.get("STORMSIM_MOCK") != "1":
        try:
            from sim.run import run_storm  # type: ignore
            return run_storm, False
        except ImportError:
            pass
    from agent.mock_sim import run_storm
    return run_storm, True


def buildings():
    global _buildings
    if _buildings is None:
        try:
            _buildings = {str(b["id"]): b for b in json.loads((GRID / "buildings.json").read_text())}
        except Exception:
            _buildings = {}
    return _buildings


def _resolve_run_dir(ret) -> Path:
    if isinstance(ret, dict):
        for k in ("run_dir", "path", "dir"):
            if ret.get(k):
                return Path(ret[k])
        if ret.get("run_id"):
            return RUNS / ret["run_id"]
    if isinstance(ret, (str, Path)):
        p = Path(ret)
        if p.exists():
            return p
        if (RUNS / str(ret)).exists():
            return RUNS / str(ret)
    if hasattr(ret, "run_dir"):
        return Path(ret.run_dir)
    raise RuntimeError(f"run_storm returned unrecognised value: {ret!r}")


def call_run_storm(storm: str, plan_code: str | None, label: str, plan_dir: str | None) -> Path:
    fn, is_mock = get_run_storm()
    # STORMSIM_REMOTE=1: run on Daytona / Fly overflow via drun (sim/remote.py); falls back to local on failure.
    # STORMSIM_REMOTE_RENDER=0 renders the video locally instead (ensure_video) of in the sandbox.
    if os.environ.get("STORMSIM_REMOTE") == "1" and not is_mock:
        try:
            from sim.remote import run_storm_remote
            return run_storm_remote(storm, plan_code=plan_code, label=label, plan_dir=plan_dir,
                                    render=os.environ.get("STORMSIM_REMOTE_RENDER", "1") == "1")
        except Exception as e:
            import sys
            print(f"[runner] remote run failed, running locally: {type(e).__name__}: {str(e)[:1500]}",
                  file=sys.stderr, flush=True)
            from sim.run import run_storm as fn  # get_run_storm() may have returned run_storm_remote
    params = inspect.signature(fn).parameters
    kw = {}
    if "plan_code" in params:
        kw["plan_code"] = plan_code
    for name in ("plan_dir", "plan_export", "plan_result", "edited_city"):
        if name in params:
            kw[name] = plan_dir
            break
    if "label" in params:
        kw["label"] = label
    if "storm_name" in params:
        kw["storm_name"] = storm
        return _resolve_run_dir(fn(**kw))
    if "storm" in params:
        kw["storm"] = storm
        return _resolve_run_dir(fn(**kw))
    return _resolve_run_dir(fn(storm, **kw))


def load_result(run_dir: Path) -> dict:
    p = Path(run_dir) / "result.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _ledger(res: dict) -> dict:
    return res.get("ledger") or res.get("damage") or {}


def _cost(res: dict) -> float:
    pc = res.get("plan_cost") or res.get("cost") or {}
    if isinstance(pc, (int, float)):
        return float(pc)
    return float(pc.get("total_usd") or 0.0)


def _people(res: dict) -> dict:
    led = _ledger(res)
    p = dict(led.get("people") or res.get("people") or {})
    for k in ("people_affected", "at_risk_person_hours", "expected_fatalities", "expected_injuries"):
        if k in led and k not in p:
            p[k] = led[k]
    return p


def ensure_video(run_dir: Path, log=None) -> Path | None:
    v = Path(run_dir) / "video.mp4"
    if v.exists() and v.stat().st_size > 1000:
        return v
    if not (Path(run_dir) / "frames" / "manifest.json").exists():
        return None
    try:
        from sim.render_video import render_run  # type: ignore
        return Path(render_run(str(run_dir), str(v)))
    except Exception as e:
        if log:
            log("video_error", message=f"render_video failed: {type(e).__name__}: {e}")
        return None


def baseline(storm: str, log=None) -> tuple[Path, dict]:
    """No-plan run for this storm (cached in runs/_baselines.json)."""
    with _baseline_lock:
        idx_p = RUNS / "_baselines.json"
        idx = json.loads(idx_p.read_text()) if idx_p.exists() else {}
        _, is_mock = get_run_storm()
        key = f"{storm}{'-mock' if is_mock else ''}"
        if key in idx and (RUNS / idx[key] / "result.json").exists():
            rd = RUNS / idx[key]
            return rd, load_result(rd)
        if not is_mock:  # reuse the newest finished real no-plan run of this storm (e.g. from the lead's pipeline)
            cands = []
            for rp in RUNS.glob("*/result.json"):
                try:
                    r = json.loads(rp.read_text())
                except Exception:
                    continue
                if (r.get("storm") == storm and not r.get("mock") and r.get("has_plan") is False
                        and "baseline" in str(r.get("label", "")).lower()):
                    cands.append((rp.stat().st_mtime, rp.parent))
            if cands:
                rd = max(cands)[1]
                idx[key] = rd.name
                idx_p.write_text(json.dumps(idx, indent=1))
                return rd, load_result(rd)
        if log:
            log("status", status=f"running no-plan baseline for {storm}{' (MOCK sim)' if is_mock else ''}")
        rd = call_run_storm(storm, None, f"baseline {storm}", None)
        idx[key] = rd.name
        RUNS.mkdir(exist_ok=True)
        idx_p.write_text(json.dumps(idx, indent=1))
        ensure_video(rd, log)
        return rd, load_result(rd)


def summarize(res: dict, base: dict | None, budget: float | None = None, top_n: int = 12) -> dict:
    led, bled = _ledger(res), _ledger(base or {})
    total = float(led.get("total_usd") or 0.0)
    btotal = float(bled.get("total_usd") or 0.0) if base else None
    cost = _cost(res)
    out = {"run_id": res.get("run_id"), "storm": res.get("storm"), "label": res.get("label"),
           "damage_total_usd": round(total)}
    if btotal is not None:
        avoided = btotal - total
        out.update(baseline_damage_usd=round(btotal), damage_avoided_usd=round(avoided), plan_cost_usd=round(cost),
                   net_benefit_usd=round(avoided - cost), bcr=round(avoided / cost, 3) if cost > 0 else None)
    else:
        out["plan_cost_usd"] = round(cost)
    if budget is not None:
        out["budget_usd"] = budget
        out["within_budget"] = cost <= budget
    bc, bbc = led.get("by_cause") or {}, bled.get("by_cause") or {}
    out["by_cause"] = {k: {"now": round(float(bc.get(k) or 0)), "baseline": round(float(bbc.get(k) or 0)),
                           "delta": round(float(bc.get(k) or 0) - float(bbc.get(k) or 0))}
                       for k in sorted(set(bc) | set(bbc), key=lambda k: -float(bc.get(k) or 0))}
    p, bp = _people(res), _people(base or {})
    if p or bp:
        out["people"] = {k: {"now": p.get(k), "baseline": bp.get(k)} for k in sorted(set(p) | set(bp))
                         if isinstance(p.get(k, bp.get(k)), (int, float))}
    for k in ("trees_failed", "cars_damaged", "people_affected"):
        if k in led:
            out[k] = {"now": led.get(k), "baseline": bled.get(k)}
    bb = led.get("by_building") or {}
    B = buildings()
    top = sorted(bb.items(), key=lambda kv: -float(kv[1] or 0))[:top_n]
    out["top_remaining_damage"] = [
        {"building_id": k, "usd": round(float(v)), "x": round(B.get(str(k), {}).get("x", X0) - X0, 1),
         "y": round(B.get(str(k), {}).get("y", Y0) - Y0, 1), "occupancy": B.get(str(k), {}).get("occupancy"),
         "ground_z": B.get(str(k), {}).get("ground_z"), "baseline_usd": round(float((bled.get("by_building") or {}).get(k, 0)))}
        for k, v in top]
    out["n_buildings_damaged"] = {"now": len([v for v in bb.values() if v]),
                                  "baseline": len([v for v in (bled.get("by_building") or {}).values() if v])}
    items = (res.get("plan_cost") or {}).get("items") if isinstance(res.get("plan_cost"), dict) else None
    if items:
        out["cost_items"] = sorted(items, key=lambda it: -float(it.get("usd") or 0))[:20]
    for k in ("metrics", "validation", "response", "warnings"):
        if res.get(k):
            out[k] = res[k]
    notes = led.get("notes") or res.get("notes")
    if notes:
        out["notes"] = notes[:10] if isinstance(notes, list) else notes
    return out


def execute_run(storm: str, plan_code: str | None, label: str = "run", budget: float | None = None,
                log=None, plan_timeout: float = 120) -> dict:
    """Full run. Returns {ok, error?, summary?, run_dir?, video?, plan_objects?, sandbox}."""
    log = log or (lambda *a, **k: None)
    plan_dir = None
    sandbox_info = None
    if plan_code and plan_code.strip():
        from server.sandbox import run_plan
        plan_dir = PLANS / f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        log("status", status="applying plan in sandbox")
        sb = run_plan(plan_code, out_dir=plan_dir, timeout=plan_timeout)
        sandbox_info = {k: sb.get(k) for k in ("ok", "backend", "secs", "stdout", "export")}
        if not sb.get("ok"):
            return {"ok": False, "stage": "plan", "error": sb.get("error") or "plan failed",
                    "stdout": sb.get("stdout"), "sandbox": sandbox_info}
        sandbox_info["plan_log_tail"] = (sb.get("plan_log") or [])[-30:] if isinstance(sb.get("plan_log"), list) else sb.get("plan_log")
        po = Path(plan_dir) / "plan_objects.json"
        if po.exists():
            log("plan_objects", source="sandbox", path=str(po.relative_to(REPO)))
    base_dir, base = baseline(storm, log)
    log("status", status="simulating storm")
    t0 = time.time()
    try:
        rd = call_run_storm(storm, plan_code, label, str(plan_dir) if plan_dir else None)
    except Exception as e:
        import traceback
        return {"ok": False, "stage": "sim", "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2500:], "sandbox": sandbox_info}
    res = load_result(rd)
    res.setdefault("run_id", rd.name)
    summ = summarize(res, base, budget)
    summ["sim_seconds"] = round(time.time() - t0, 1)
    if plan_dir:
        try:
            from agent.citylib import annotate_run_objects
            annotate_run_objects(rd, plan_dir)
        except Exception as e:
            log("status", status=f"plan object annotation failed: {e}")
    video = ensure_video(rd, log)
    objs = None
    for cand in (rd / "plan_objects.json", Path(plan_dir or "/nonexistent") / "plan_objects.json"):
        if cand.exists():
            try:
                objs = json.loads(cand.read_text()).get("objects", [])
            except Exception:
                objs = None
            break
    return {"ok": True, "summary": summ, "run_dir": str(rd), "run_id": rd.name,
            "video": str(video) if video else None, "plan_objects": objs, "sandbox": sandbox_info,
            "baseline_run_id": base_dir.name, "mock": get_run_storm()[1]}
