"""Evolve API + view (AlphaEvolve-style plan evolution, agent/evolve.py).

Mounted by server/app.py with `app.include_router(evolve_api.router)` before the static "/" mount.
  POST /api/evolve        {storm?, budget_usd?, generations?, population?, mutator_model?, architect_model?,
                           sim_workers?, max_minutes?, use_llm?}             -> {job_id, events_url, state_url}
  GET  /api/evolve        recent evolution runs (newest first)
  GET  /api/evolve/{id}   state.json of one run ("latest" = newest)
  POST /api/evolve/{id}/approve {approved, approver?, note?}   planner decision on the best plan
  GET  /evolve/...        the view (web/public/evolve, no build step)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from agent import events

REPO = Path(__file__).resolve().parent.parent
VIEW = REPO / "web" / "public" / "evolve"
VIEW_DIST = REPO / "web" / "dist" / "evolve"
router = APIRouter()


class EvolveReq(BaseModel):
    storm: str = "elnino_ar_2026"
    budget_usd: float = 50e6
    generations: int = 4
    population: int = 8
    mutator_model: str = "gemini-3.8-flash"
    architect_model: str = "gemini-4-argon"
    sim_workers: int = 4
    max_minutes: float = 40
    use_llm: bool = True


@router.post("/api/evolve")
def api_evolve(req: EvolveReq):
    from agent.evolve import evolve_job
    from agent.optimizer import MODELS
    for m in (req.mutator_model, req.architect_model):
        if m not in MODELS:
            raise HTTPException(400, f"model must be one of {MODELS}")
    job = events.new_job("evolve", req.model_dump())
    events.start_thread(job, evolve_job, req.model_dump())
    return {"job_id": job.id, "events_url": f"/api/jobs/{job.id}/events",
            "state_url": f"/runs/evolve/{job.id}/state.json", "view_url": f"/evolve/?job={job.id}"}


@router.get("/api/evolve")
def api_evolve_list(limit: int = 50):
    from agent.evolve import list_runs
    return list_runs(limit)


@router.get("/api/evolve/{job_id}")
def api_evolve_state(job_id: str):
    from agent.evolve import OUT, list_runs
    if job_id == "latest":
        runs = list_runs(1)
        if not runs:
            raise HTTPException(404, "no evolution runs yet")
        job_id = runs[0]["id"]
    p = OUT / job_id / "state.json"
    if not p.exists() or OUT.resolve() not in p.resolve().parents:
        raise HTTPException(404, "no such evolution run")
    st = json.loads(p.read_text())
    if st.get("status") == "running" and events.JOBS.get(job_id) is None and time.time() - st.get("updated", 0) > 900:
        st["status"] = "interrupted"  # no progress for 15 min and not running in this server
    ap = REPO / "runs" / "_jobs" / f"{job_id}.approval.json"
    if ap.exists():
        st["approval"] = json.loads(ap.read_text())
        st["status"] = "approved" if st["approval"].get("approved") else "rejected"
    return st


class ApproveReq(BaseModel):
    approved: bool
    approver: str = "planner"
    note: str = ""


@router.post("/api/evolve/{job_id}/approve")
def api_evolve_approve(job_id: str, req: ApproveReq):
    """Planner decision on the best plan. Works for runs started here or from the CLI."""
    from agent.evolve import OUT
    p = OUT / job_id / "state.json"
    if not p.exists() or OUT.resolve() not in p.resolve().parents:
        raise HTTPException(404, "no such evolution run")
    st = json.loads(p.read_text())
    if st.get("status") != "awaiting_approval":
        raise HTTPException(409, f"run status is {st.get('status')}, not awaiting_approval")
    rec = {"approved": req.approved, "approver": req.approver, "note": req.note, "t": time.time(),
           "best_id": st.get("best_id"), "plan": f"/runs/evolve/{job_id}/best.plan.py"}
    (REPO / "runs" / "_jobs").mkdir(parents=True, exist_ok=True)
    (REPO / "runs" / "_jobs" / f"{job_id}.approval.json").write_text(json.dumps(rec))
    j = events.JOBS.get(job_id)
    if j is not None and j.status == "awaiting_approval":
        j.emit("approval", **rec)
        j.finish("approved" if req.approved else "rejected", {**(j.result or {}), "approval": rec})
        j.status = "completed"
    return {"ok": True, **rec}


@router.get("/evolve")
def evolve_root():
    return RedirectResponse("/evolve/")


@router.get("/evolve/{path:path}")
def evolve_view(path: str = ""):
    for base in (VIEW, VIEW_DIST):
        p = (base / (path or "index.html")).resolve()
        if p.is_file() and base.resolve() in p.parents:
            return FileResponse(p, headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "not found")
