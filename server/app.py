"""StormSim SF API server (FastAPI, 127.0.0.1:8765).

Static:  /            web/dist (SPA)          /data/...  data/grid, then web/public/data
         /runs/...    runs/ (frames, video.mp4, result.json, plan_objects.json, screens/)
API:     GET  /api/health
         POST /api/run        {storm, plan_code?, label?}                         -> {job_id}
         POST /api/optimize   {model, budget_usd, storm, max_runs?, max_minutes?, thinking_level?,
                               watch_video?, screenshots?, pack?, long_decode?, write_eop?} -> {job_id}
         POST /api/benchmark  {storm?, budget_usd?, max_runs?, max_minutes?, entries?}   -> {job_id}
         POST /api/managed    {storm?, plan_code? | job_id?, mode?}                       -> {job_id}
         GET  /api/jobs, /api/jobs/{id}, /api/jobs/{id}/events (SSE; Last-Event-ID / ?since=)
         POST /api/jobs/{id}/approve {approved: bool, approver?, note?}  (planner approval gate; writes EOP)
         GET  /api/jobs/{id}/eop                                                   (cited EOP JSON)
         GET  /api/runs, /api/runs/{id}, /api/leaderboard, /api/alerts, /api/forecast
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent import events  # noqa: E402
from agent.events import JOBS, new_job, sse_stream  # noqa: E402

RUNS = REPO / "runs"
RUNS.mkdir(exist_ok=True)
DATA_DIRS = [REPO / "data" / "grid", REPO / "web" / "public" / "data"]
DIST = REPO / "web" / "dist"

app = FastAPI(title="StormSim SF")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class RunReq(BaseModel):
    storm: str = "elnino_ar_2026"
    plan_code: str | None = None
    label: str | None = None
    budget_usd: float | None = None


class OptReq(BaseModel):
    model: str = "gemini-4-argon"
    budget_usd: float = 50e6
    storm: str = "elnino_ar_2026"
    max_runs: int = 8
    max_minutes: float = 15
    thinking_level: str | None = None
    watch_video: bool = True
    screenshots: bool = True
    pack: str = "full"
    long_decode: bool = False
    write_eop: bool = True


class BenchReq(BaseModel):
    storm: str = "elnino_ar_2026"
    budget_usd: float = 50e6
    max_runs: int = 6
    max_minutes: float = 15
    entries: list[dict] | None = None
    pack: str = "full"


class ManagedReq(BaseModel):
    storm: str = "elnino_ar_2026"
    plan_code: str | None = None
    job_id: str | None = None
    mode: str = "verify"
    budget_usd: float = 50e6


class ApproveReq(BaseModel):
    approved: bool
    approver: str = "planner"
    note: str = ""


@app.get("/api/health")
def health():
    from agent.runner import get_run_storm
    from server.sandbox import detect_backend
    return {"ok": True, "mock_sim": get_run_storm()[1], "sandbox": detect_backend(), "jobs": len(JOBS),
            "gemini_key": bool(os.environ.get("GEMINI_API_KEY"))}


def _start(kind, meta, fn, *a, **kw):
    job = new_job(kind, meta)
    events.start_thread(job, fn, *a, **kw)
    return {"job_id": job.id, "events_url": f"/api/jobs/{job.id}/events"}


def _run_job(job, req: RunReq):
    from agent.runner import execute_run
    job.emit("job_start", storm=req.storm, label=req.label, has_plan=bool(req.plan_code))
    r = execute_run(req.storm, req.plan_code, label=req.label or "manual run", budget=req.budget_usd,
                    log=lambda t, **k: job.emit(t, **k))
    if r.get("ok"):
        job.emit("run_result", run_id=r["run_id"], summary=r["summary"],
                 video_url=f"/runs/{r['run_id']}/video.mp4" if r.get("video") else None, mock=r.get("mock"))
        return {"ok": True, "run_id": r["run_id"], "summary": r["summary"]}
    job.emit("run_error", stage=r.get("stage"), error=r.get("error"))
    return {"ok": False, "error": r.get("error"), "stage": r.get("stage")}


@app.post("/api/run")
def api_run(req: RunReq):
    return _start("run", req.model_dump(exclude={"plan_code"}), _run_job, req)


def _opt_job(job, req: OptReq):
    from agent.optimizer import optimize
    res = optimize(job, req.model, req.storm, req.budget_usd, max_runs=req.max_runs, max_minutes=req.max_minutes,
                   thinking_level=req.thinking_level, watch_video=req.watch_video, screenshots=req.screenshots,
                   pack=req.pack, long_decode=req.long_decode, write_eop=req.write_eop)
    if res.get("submitted"):
        res["_final_status"] = "awaiting_approval"
    return res


@app.post("/api/optimize")
def api_optimize(req: OptReq):
    from agent.optimizer import MODELS
    if req.model not in MODELS:
        raise HTTPException(400, f"model must be one of {MODELS}")
    return _start("optimize", req.model_dump(), _opt_job, req)


@app.post("/api/benchmark")
def api_benchmark(req: BenchReq):
    from agent.benchmark import benchmark_job
    return _start("benchmark", req.model_dump(), benchmark_job, req.model_dump())


@app.post("/api/managed")
def api_managed(req: ManagedReq):
    from agent.managed import managed_job
    plan = req.plan_code
    if not plan and req.job_id:
        p = RUNS / "_jobs" / f"{req.job_id}.plan.py"
        if p.exists():
            plan = p.read_text()
    return _start("managed", req.model_dump(exclude={"plan_code"}), managed_job,
                  storm=req.storm, plan_code=plan, mode=req.mode, budget_usd=req.budget_usd)


@app.get("/api/jobs")
def api_jobs():
    return sorted((j.summary() for j in JOBS.values()), key=lambda s: -s["created"])


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    j = JOBS.get(job_id)
    if j:
        return j.summary()
    p = RUNS / "_jobs" / f"{job_id}.final.json"
    if p.exists():
        return {"id": job_id, "status": "completed", "result": json.loads(p.read_text())}
    raise HTTPException(404, "no such job")


@app.get("/api/jobs/{job_id}/events")
async def api_events(job_id: str, request: Request, since: int = -1):
    last = request.headers.get("last-event-id")
    if last is not None and last.isdigit():
        since = max(since, int(last))
    j = JOBS.get(job_id)
    if j is None:
        p = RUNS / "_jobs" / f"{job_id}.jsonl"
        if not p.exists():
            raise HTTPException(404, "no such job")

        async def replay():
            for line in p.read_text().splitlines():
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("seq", 0) > since:
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {line}\n\n"
        return StreamingResponse(replay(), media_type="text/event-stream")
    return StreamingResponse(sse_stream(j, since), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{job_id}/approve")
def api_approve(job_id: str, req: ApproveReq):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "no such job (or not in memory)")
    if j.status != "awaiting_approval":
        raise HTTPException(409, f"job status is {j.status}, not awaiting_approval")
    rec = {"approved": req.approved, "approver": req.approver, "note": req.note, "t": time.time()}
    (RUNS / "_jobs" / f"{job_id}.approval.json").write_text(json.dumps(rec))
    j.emit("approval", **rec)
    res = dict(j.result or {})
    res["approval"] = rec
    # The planner's approval is the hand-off point from a scored plan to field
    # operations.  Build the cited EOP from the submitted run; all numerical
    # values remain sourced from result.json and the locked cost tables.
    if req.approved:
        try:
            from agent.action_plan import generate_eop_for_job
            eop = generate_eop_for_job(job_id, res, repo=REPO)
            rec.update({"eop_json": eop["eop_json"], "eop_html": eop["eop_html"]})
            if not eop["document"].get("eligible_for_dispatch", False):
                rec["eop_blocked"] = True
            res["approval"] = rec
            res["eop_path"] = eop["eop_json"]
            (RUNS / "_jobs" / f"{job_id}.approval.json").write_text(json.dumps(rec))
            j.emit("document", kind="eop", title="Emergency Operations Plan",
                   json_path=eop["eop_json"], html_path=eop["eop_html"])
        except Exception as exc:
            # Approval remains durable even if a malformed historical job cannot
            # be rendered; the API exposes the exact failure for remediation.
            rec["eop_error"] = f"{type(exc).__name__}: {exc}"
            res["approval"] = rec
            (RUNS / "_jobs" / f"{job_id}.approval.json").write_text(json.dumps(rec))
            j.emit("eop_error", error=rec["eop_error"])
    j.finish("approved" if req.approved else "rejected", res)
    j.status = "completed"
    return {"ok": True, **rec}


@app.get("/api/jobs/{job_id}/eop")
def api_job_eop(job_id: str):
    """Return the cited EOP generated when the planner approved this job."""
    p = RUNS / job_id / "eop.json"
    if not p.exists() or RUNS.resolve() not in p.resolve().parents:
        raise HTTPException(404, "no EOP for this job")
    try:
        return JSONResponse(json.loads(p.read_text()))
    except Exception as exc:
        raise HTTPException(500, f"invalid EOP: {exc}")


def _run_summary(d: Path) -> dict | None:
    p = d / "result.json"
    if not p.exists():
        return None
    try:
        r = json.loads(p.read_text())
    except Exception:
        return None
    led = r.get("ledger") or {}
    pc = r.get("plan_cost") or {}
    return {"run_id": d.name, "label": r.get("label"), "storm": r.get("storm"), "mock": r.get("mock", False),
            "damage_usd": led.get("total_usd"), "plan_cost_usd": pc.get("total_usd") if isinstance(pc, dict) else pc,
            "has_plan": r.get("has_plan", bool(pc)), "video": (d / "video.mp4").exists(),
            "plan_objects": (d / "plan_objects.json").exists(), "mtime": d.stat().st_mtime}


@app.get("/api/runs")
def api_runs(limit: int = 200):
    out = [s for d in RUNS.iterdir() if d.is_dir() and not d.name.startswith("_") and (s := _run_summary(d))]
    return sorted(out, key=lambda s: -s["mtime"])[:limit]


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str):
    from agent import runner
    d = RUNS / run_id
    if not (d / "result.json").exists():
        raise HTTPException(404, "no such run")
    res = runner.load_result(d)
    base = None
    idx = RUNS / "_baselines.json"
    if idx.exists() and res.get("storm"):
        for k, v in json.loads(idx.read_text()).items():
            if k.startswith(res["storm"]) and (RUNS / v / "result.json").exists() and v != run_id:
                base = runner.load_result(RUNS / v)
                break
    return runner.summarize(res, base)


@app.get("/api/leaderboard")
def api_leaderboard():
    p = RUNS / "benchmark" / "leaderboard.json"
    if not p.exists():
        return {"entries": [], "note": "no benchmark yet"}
    return json.loads(p.read_text())


_alerts_cache: dict = {}


@app.get("/api/alerts")
def api_alerts(live: bool = True):
    now = time.time()
    if live:
        if _alerts_cache.get("t", 0) > now - 300:
            return _alerts_cache["data"]
        try:
            import requests
            r = requests.get("https://api.weather.gov/alerts/active", params={"point": "37.7749,-122.4194"},
                             headers={"User-Agent": "StormSimSF hackathon (contact: stormsim@example.com)",
                                      "Accept": "application/geo+json"}, timeout=6)
            r.raise_for_status()
            data = {"source": "api.weather.gov (live)", "fetched": now, **r.json()}
            _alerts_cache.update(t=now, data=data)
            return data
        except Exception as e:
            err = str(e)
    else:
        err = None
    p = REPO / "data" / "raw" / "nws_alerts_now.json"
    if p.exists():
        return {"source": "data/raw/nws_alerts_now.json (cached)", "live_error": err, **json.loads(p.read_text())}
    return {"source": "none", "features": [], "live_error": err}


@app.get("/api/forecast")
def api_forecast(refresh: bool = False):
    """Return the tracked live tide/wind/QPF forecast and its provenance.

    The default is offline-safe and reads ``data/reference/storm/forecast_live.json``.  A
    ``?refresh=true`` request explicitly performs the NOAA/NWS fetch before returning.
    """
    try:
        from sim.forecast import load_or_create
        return load_or_create(refresh=refresh)
    except Exception as exc:
        raise HTTPException(502, f"forecast unavailable: {type(exc).__name__}: {exc}") from exc


@app.get("/data/{path:path}")
def data_file(path: str):
    for base in DATA_DIRS:
        p = (base / path).resolve()
        if p.is_file() and base.resolve() in p.parents:
            return FileResponse(p)
    raise HTTPException(404, "not found")


app.mount("/runs", StaticFiles(directory=str(RUNS)), name="runs")

if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="web")
else:
    @app.get("/")
    def root():
        return JSONResponse({"app": "StormSim SF", "note": "web/dist not built yet", "api": "/docs"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8765)))
