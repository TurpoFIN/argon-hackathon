"""Job registry + event stream (JSONL on disk + in-memory replayable queue for SSE)."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JOBS_DIR = REPO / "runs" / "_jobs"


class Job:
    def __init__(self, kind: str, meta: dict | None = None, job_id: str | None = None):
        self.id = job_id or f"{kind}-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.kind = kind
        self.meta = meta or {}
        self.status = "queued"
        self.result: dict | None = None
        self.events: list[dict] = []
        self.created = time.time()
        self._lock = threading.Lock()
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self.jsonl = JOBS_DIR / f"{self.id}.jsonl"
        self._fh = open(self.jsonl, "a", buffering=1)

    def emit(self, type: str, **data) -> dict:
        with self._lock:
            ev = {"seq": len(self.events), "t": round(time.time(), 3), "job": self.id, "type": type, **data}
            self.events.append(ev)
            try:
                self._fh.write(json.dumps(ev, default=str) + "\n")
            except Exception:
                pass
        return ev

    def finish(self, status: str, result: dict | None = None):
        self.status = status
        self.result = result
        self.emit("done", status=status, result=result)
        try:
            self._fh.close()
        except Exception:
            pass

    def summary(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "meta": self.meta,
                "created": self.created, "n_events": len(self.events), "result": self.result}


JOBS: dict[str, Job] = {}


def new_job(kind: str, meta: dict | None = None) -> Job:
    j = Job(kind, meta)
    JOBS[j.id] = j
    return j


def start_thread(job: Job, fn, *args, **kw) -> Job:
    def _run():
        job.status = "running"
        job.emit("status", status="running")
        try:
            res = fn(job, *args, **kw)
            final = res.pop("_final_status", "completed") if isinstance(res, dict) else "completed"
            if final == "awaiting_approval":  # keep SSE open until the planner approves
                job.result = res
                job.status = final
                job.emit("awaiting_approval", result=res)
            else:
                job.finish(final, res)
        except Exception as e:  # report, never crash the server
            import traceback
            job.emit("error", message=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-4000:])
            job.finish("failed", {"error": str(e)})
    threading.Thread(target=_run, daemon=True, name=job.id).start()
    return job


async def sse_stream(job: Job, last_seq: int = -1, poll: float = 0.15):
    """Async generator of SSE frames; replays history, then follows until the job is done."""
    i = last_seq + 1
    idle = 0.0
    while True:
        n = len(job.events)
        while i < n:
            ev = job.events[i]
            yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            i += 1
            idle = 0.0
        if job.status in ("completed", "failed", "cancelled") and i >= len(job.events):
            return
        await asyncio.sleep(poll)
        idle += poll
        if idle > 15:
            yield ": keepalive\n\n"
            idle = 0.0


class PrintSink:
    """Stand-in job for CLI runs: emits to stdout + JSONL."""

    def __init__(self, job: Job, verbose: bool = True):
        self.job, self.verbose = job, verbose
