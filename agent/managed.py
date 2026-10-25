"""Managed Agents path: independent plan verification inside an Antigravity sandbox (Gemini 4 Argon).

What is real:
  1. A remote environment is provisioned; our simulator (sim/*.py), the city grid (data/grid), the locked
     reference tables (data/reference/*.json), the plan under test and verify.py are uploaded as a tar.gz
     (environments.files.upload, extract=True). No secrets go into the sandbox.
  2. Agent `antigravity-preview-09-2026` with agent_config model gemini-4-argon runs in that sandbox: installs
     deps, runs the no-plan and the plan storm itself (verify.py), audits the plan code (cost bypasses,
     locked-table edits, walls that don't tie into high ground, shelters in flooded areas...), compares its
     ledger hash with ours (determinism), and writes VERIFICATION.md.
  3. It reports a structured verdict through a CUSTOM FUNCTION (`report_verification`) -> status
     requires_action -> we record it and return a function_result (Antigravity function-calling loop).
  4. Every step is streamed as events; VERIFICATION.md + verification.json are downloaded from the sandbox.
CLI: scripts/gem.sh .venv/bin/python -m agent.managed --plan runs/_jobs/<job>.plan.py
"""
from __future__ import annotations

import argparse
import io
import json
import tarfile
import time
from pathlib import Path

from agent.events import Job, new_job

REPO = Path(__file__).resolve().parent.parent
AGENT = "antigravity-preview-09-2026"
OUT = REPO / "runs" / "_managed"

VERIFY_PY = r'''
"""Independent re-run of the StormSim storm (no-plan + plan) inside the managed sandbox."""
import hashlib, json, os, sys, time
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.environ.setdefault("NUMBA_NUM_THREADS", str(os.cpu_count() or 4))
from agent.runner import call_run_storm, load_result, get_run_storm
from agent.citylib import exec_plan_in, export_city

def lhash(rd):
    led = load_result(rd).get("ledger") or {}
    return hashlib.sha256(json.dumps(led, sort_keys=True, default=str).encode()).hexdigest()[:16]

storm = sys.argv[1] if len(sys.argv) > 1 else "elnino_ar_2026"
code = open(os.path.join(ROOT, "plan.py")).read()
fn, mock = get_run_storm()
out = {"storm": storm, "mock_sim": mock, "plan_sha": hashlib.sha256(code.encode()).hexdigest()[:16]}
t = time.time()
rd0 = call_run_storm(storm, None, "managed no-plan", None)
out["no_plan"] = {"run_dir": str(rd0), "ledger_hash": lhash(rd0), "total_usd": (load_result(rd0).get("ledger") or {}).get("total_usd"), "secs": round(time.time() - t, 1)}
t = time.time()
city, log, err = exec_plan_in(code)
if err:
    out["plan_error"] = err
else:
    pdir = os.path.join(ROOT, "runs", "_plan_export"); os.makedirs(pdir, exist_ok=True)
    export_city(city, pdir)
    rd1 = call_run_storm(storm, code, "managed plan", pdir)
    res = load_result(rd1)
    led = res.get("ledger") or {}
    pc = res.get("plan_cost") or {}
    out["plan"] = {"run_dir": str(rd1), "ledger_hash": lhash(rd1), "total_usd": led.get("total_usd"),
                   "plan_cost_usd": pc.get("total_usd") if isinstance(pc, dict) else pc, "by_cause": led.get("by_cause"),
                   "people": led.get("people"), "secs": round(time.time() - t, 1),
                   "cost_items": (pc.get("items") if isinstance(pc, dict) else None)}
    if out["no_plan"]["total_usd"] is not None and led.get("total_usd") is not None:
        av = out["no_plan"]["total_usd"] - led["total_usd"]
        c = out["plan"]["plan_cost_usd"] or 0
        out["damage_avoided_usd"] = av
        out["bcr"] = av / c if c else None
json.dump(out, open(os.path.join(ROOT, "verification.json"), "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in out.items() if k != "plan"}, default=str))
print("plan:", json.dumps({k: v for k, v in (out.get("plan") or {}).items() if k not in ("cost_items", "by_cause")}, default=str))
'''

REPORT_TOOL = {
    "type": "function", "name": "report_verification",
    "description": "Report the independent verification verdict back to the StormSim planning cell.",
    "parameters": {"type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["verified", "verified_with_issues", "rejected"]},
        "ledger_hash_match": {"type": "boolean", "description": "Did your re-run ledger hash equal the expected one?"},
        "no_plan_damage_usd": {"type": "number"}, "plan_damage_usd": {"type": "number"},
        "plan_cost_usd": {"type": "number"}, "bcr": {"type": "number"},
        "issues": {"type": "array", "items": {"type": "string"}, "description": "Concrete problems found in the plan or results."},
        "summary": {"type": "string"}},
        "required": ["verdict", "ledger_hash_match", "summary"]}}


def build_bundle(plan_code: str, expected: dict | None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(path: Path, arc: str):
            tar.add(str(path), arcname=arc, filter=lambda ti: None if "__pycache__" in ti.name else ti)
        for p in sorted((REPO / "sim").glob("*.py")) + sorted((REPO / "sim").glob("*.md")):
            add(p, f"sim/{p.name}")
        for n in ("__init__.py", "citylib.py", "runner.py", "mock_sim.py"):
            add(REPO / "agent" / n, f"agent/{n}")
        for n in ("meta.json", "city.npz", "buildings.json", "trees.json", "inlets.json", "cars.json", "validation.json"):
            if (REPO / "data" / "grid" / n).exists():
                add(REPO / "data" / "grid" / n, f"data/grid/{n}")
        for p in sorted((REPO / "data" / "reference").rglob("*")):
            if p.is_file() and p.suffix in (".json", ".csv") and p.stat().st_size < 5_000_000:
                add(p, f"data/reference/{p.relative_to(REPO / 'data' / 'reference')}")
        for name, content in (("plan.py", plan_code), ("verify.py", VERIFY_PY),
                              ("EXPECTED.json", json.dumps(expected or {}, indent=1, default=str)),
                              ("requirements.txt", "numpy\nscipy\nnumba\nmatplotlib\npyproj\n")):
            b = content.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(b)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(b))
    return buf.getvalue()


def _prompt(storm: str, expected: dict | None, workdir: str) -> str:
    return f"""You are an independent verifier for the City of San Francisco's storm plan (StormSim SF).
The project is uploaded at {workdir} (simulator in sim/, city grid in data/grid, locked cost/damage tables in
data/reference, the plan under test in plan.py, our claimed results in EXPECTED.json).
Do this, in the sandbox, yourself:
1. cd {workdir}; pip install -q -r requirements.txt (skip what is already installed).
2. Run `python verify.py {storm}` (it runs the no-plan storm and the plan storm with our simulator and writes
   verification.json). If it fails, debug the environment (not the simulator physics) and rerun.
3. Audit plan.py line by line: does it bypass the cost planner, edit locked tables, place walls/pumps inside
   buildings or the bay, leave floodwalls that do not tie into high ground (check city.elevation at wall ends),
   put shelters where the no-plan run floods, or claim measures it doesn't build? Use the CityEditor
   (from sim.city_api import CityEditor) and python to check facts; cite line numbers.
4. Compare your ledger hash with EXPECTED.json's ledger_hash (determinism) and your numbers with ours.
5. Write VERIFICATION.md (verdict, re-run numbers vs claimed, determinism, issues with evidence), then call
   the report_verification function tool with the structured verdict (if that tool is unavailable, write the
   same arguments to report_verification.json).
Expected (from the planning cell): {json.dumps(expected or {}, default=str)[:3000]}"""


def run_managed(job: Job, storm: str = "elnino_ar_2026", plan_code: str | None = None, expected: dict | None = None,
                model: str = "gemini-4-argon", max_turns: int = 6) -> dict:
    from google import genai
    client = genai.Client()
    OUT.mkdir(parents=True, exist_ok=True)
    emit = job.emit
    if not plan_code:
        raise ValueError("plan_code required (or job_id of an optimize job with a submitted plan)")
    emit("phase", phase="M", title="Managed agent: independent verification in a remote sandbox")
    # 1. provision a sandbox (tiny call), 2. upload bundle
    t0 = time.time()
    it0 = client.interactions.create(agent=AGENT, agent_config={"type": "antigravity", "model": model},
                                     input="Run `pwd; ls -la` and reply with the output only.", environment="remote")
    env_id = it0.environment_id
    emit("managed_env", environment_id=env_id, secs=round(time.time() - t0, 1), pwd=(it0.output_text or "")[:400])
    bundle = build_bundle(plan_code, expected)
    emit("status", status=f"uploading sim + grid + plan bundle ({len(bundle)/1e6:.1f} MB) to the sandbox")
    up = client.environments.files.upload(environment=env_id, path="workspace/stormsim/", file=bundle, extract=True,
                                          overwrite=True, mime_type="application/gzip")
    n_files = len(getattr(up, "files", None) or [])
    emit("managed_upload", files=n_files, bytes=len(bundle))
    pwd = (it0.output_text or "").strip().splitlines()
    workdir = "workspace/stormsim (relative to the sandbox root; find it with `ls -d /workspace/stormsim ~/workspace/stormsim $PWD/workspace/stormsim 2>/dev/null`)"
    # 3. verification run (streamed), with custom function loop
    inp = _prompt(storm, expected, workdir)
    prev = None  # fresh conversation on the same environment so the custom tool declarations apply
    verdict = None
    final_text = ""
    for turn in range(max_turns):
        iid = None
        cur = None
        kw = {"previous_interaction_id": prev} if prev else {}
        for ev in client.interactions.create(agent=AGENT, agent_config={"type": "antigravity", "model": model},
                                             input=inp, environment=env_id, **kw,
                                             tools=[{"type": "code_execution"}, REPORT_TOOL], stream=True, timeout=3600):
            et = getattr(ev, "event_type", None)
            if et == "interaction.created":
                iid = ev.interaction.id
            elif et == "step.start":
                st = ev.step
                cur = getattr(st, "type", None)
                info = {"step": cur}
                for k in ("name", "code", "arguments", "command"):
                    v = getattr(st, k, None)
                    if v:
                        info[k] = v if isinstance(v, (str, dict)) else str(v)
                emit("managed_step", **{k: (str(v)[:2000]) for k, v in info.items()})
            elif et == "step.delta":
                d = ev.delta
                dt = getattr(d, "type", None)
                if dt == "text" and d.text:
                    emit("text", text=d.text, source="managed")
                elif dt == "thought_summary":
                    emit("thought", text=getattr(getattr(d, "content", None), "text", "") or "", source="managed")
                elif dt not in ("thought_signature", "arguments_delta"):
                    s = d.model_dump() if hasattr(d, "model_dump") else str(d)
                    emit("managed_delta", delta=str(s)[:1500])
            elif et == "interaction.completed" and iid is None:
                iid = ev.interaction.id
        it = client.interactions.get(iid)
        prev = it.id
        for s in it.steps or []:
            if s.type in ("code_execution_call", "code_execution_result"):
                emit("managed_exec", step=s.type, detail=str(s.model_dump(exclude_none=True))[:2500])
        final_text = it.output_text or final_text
        done_ids = {getattr(s, "call_id", None) for s in it.steps or [] if s.type == "function_result"}
        pending = [s for s in it.steps or [] if s.type == "function_call" and s.id not in done_ids and s.name == "report_verification"]
        if it.status == "requires_action" and pending:
            fc = pending[0]
            verdict = dict(fc.arguments or {})
            emit("managed_verdict", **verdict)
            inp = [{"type": "function_result", "name": fc.name, "call_id": fc.id,
                    "result": [{"type": "text", "text": json.dumps({"recorded": True})}]}]
            continue
        if it.status in ("completed", "failed", "cancelled", "incomplete"):
            if verdict or it.status != "completed":
                break
            inp = "Now call report_verification with your verdict."
    # 4. download artefacts
    files = {}
    for rel in ("workspace/stormsim/VERIFICATION.md", "workspace/stormsim/verification.json",
                "workspace/stormsim/report_verification.json"):
        try:
            b = client.environments.files.download(environment=env_id, path=rel)
            p = OUT / f"{job.id}_{Path(rel).name}"
            p.write_bytes(b if isinstance(b, (bytes, bytearray)) else bytes(str(b), "utf-8"))
            files[Path(rel).name] = "/" + str(p.relative_to(REPO))
        except Exception as e:
            files[Path(rel).name] = f"download failed: {type(e).__name__}: {str(e)[:200]}"
    if "VERIFICATION.md" in files and files["VERIFICATION.md"].startswith("/"):
        emit("document", kind="verification", title="Independent verification (Antigravity managed agent)",
             markdown=(REPO / files["VERIFICATION.md"].lstrip("/")).read_text()[:60000], path=files["VERIFICATION.md"])
    if verdict is None and str(files.get("report_verification.json", "")).startswith("/"):
        try:
            rv = json.loads((REPO / files["report_verification.json"].lstrip("/")).read_text())
            verdict = rv.get("arguments", rv)
            verdict["_source"] = "report_verification.json in sandbox (function call not made)"
        except Exception:
            pass
    res = {"environment_id": env_id, "verdict": verdict, "files": files, "final_text": final_text[:4000],
           "secs": round(time.time() - t0, 1), "agent": AGENT, "model": model}
    (OUT / f"{job.id}.json").write_text(json.dumps(res, indent=1, default=str))
    emit("managed_result", **{k: v for k, v in res.items() if k != "final_text"})
    return res


def managed_job(job: Job, storm="elnino_ar_2026", plan_code=None, mode="verify", budget_usd=50e6, expected=None):
    if expected is None and plan_code:
        expected = _expected_for(storm, plan_code)
    return run_managed(job, storm, plan_code, expected)


def _expected_for(storm, plan_code):
    """Our host-side result for this plan (runs it if needed) -> claimed numbers + ledger hash."""
    from agent import runner
    from agent.optimizer import ledger_hash, sha
    r = runner.execute_run(storm, plan_code, label="managed-expected")
    if not r.get("ok"):
        return {"error": r.get("error")}
    s = r["summary"]
    return {"plan_hash": sha(plan_code), "ledger_hash": ledger_hash(r["run_dir"]), "run_id": r["run_id"],
            "damage_total_usd": s.get("damage_total_usd"), "baseline_damage_usd": s.get("baseline_damage_usd"),
            "plan_cost_usd": s.get("plan_cost_usd"), "bcr": s.get("bcr"), "mock_sim": r.get("mock")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--storm", default="elnino_ar_2026")
    ap.add_argument("--no-expected", action="store_true", help="skip the host-side expected run (compare hashes later)")
    ap.add_argument("--label", default="Independent audit · Antigravity managed agent")
    ap.add_argument("--source-job", default=None)
    a = ap.parse_args()
    job = new_job("managed", {"plan": a.plan, "label": a.label, "source_job": a.source_job})
    job.emit("job_start", label=a.label, plan=a.plan, source_job=a.source_job, agent=AGENT, model="gemini-4-argon")
    orig = job.emit

    def emit(type, **d):
        ev = orig(type, **d)
        if type not in ("text", "thought"):
            print(f"[{time.strftime('%H:%M:%S')}] {type}: {json.dumps(d, default=str)[:400]}", flush=True)
        return ev
    job.emit = emit
    res = managed_job(job, a.storm, Path(a.plan).read_text(), expected={} if a.no_expected else None)
    job.finish("completed", res)
    print(json.dumps(res, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main()


# ------------------------------------------------------------------ staged audit (exact Daytona job bundle)
def staged_audit(job: Job, plan_code: str, storm: str = "elnino_ar_2026", max_duration_s: float | None = 7200,
                 model: str = "gemini-4-argon", host_compare: bool = True) -> dict:
    """Ship the identical job dir that sim/remote.py runs on Daytona (pinned wheels, wind cache, streets, sandbox
    export, same CPU feature env) into the Antigravity sandbox; Argon re-runs it there and audits the plan, while
    the host runs the same job on Daytona -> both ledger hashes are compared."""
    import shutil
    import tempfile
    import threading
    import uuid as _uuid
    from google import genai
    from sim import remote
    from server.sandbox import run_plan
    from agent import runner
    client = genai.Client()
    emit = job.emit
    t0 = time.time()
    emit("phase", phase="M", title="Independent audit · Antigravity managed agent (real frozen sim)")
    pdir = runner.PLANS / f"audit-{int(t0)}"
    sb = run_plan(plan_code, out_dir=pdir, timeout=180)
    if not sb.get("ok"):
        raise RuntimeError(f"plan failed in sandbox: {sb.get('error')}")
    kw = {"max_duration_s": max_duration_s} if max_duration_s else {}
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{_uuid.uuid4().hex[:4]}-audit-sandbox"
    tmp = Path(tempfile.mkdtemp(prefix="audit_job_"))
    info = remote.stage_job(tmp, storm, plan_code, "audit in Antigravity sandbox", run_id, False, 8, str(pdir), kw)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(tmp), arcname=".")
    bundle = buf.getvalue()
    shutil.rmtree(tmp, ignore_errors=True)
    host = {}

    def host_run():
        try:
            rd = remote.run_storm_remote(storm, plan_code, label="audit host twin", plan_dir=str(pdir), **kw)
            r = runner.load_result(rd)
            host.update(run_id=rd.name, ledger_hash=r.get("ledger_hash"), total_usd=(r.get("ledger") or {}).get("total_usd"))
            emit("audit_host_run", **host)
        except Exception as e:
            host["error"] = f"{type(e).__name__}: {e}"
    th = threading.Thread(target=host_run, daemon=True)
    if host_compare:
        th.start()
    it0 = client.interactions.create(agent=AGENT, agent_config={"type": "antigravity", "model": model},
                                     input="Run `pwd` and reply with the output only.", environment="remote")
    env_id = it0.environment_id
    emit("managed_env", environment_id=env_id)
    client.environments.files.upload(environment=env_id, path="workspace/audit/", file=bundle, extract=True,
                                     overwrite=True, mime_type="application/gzip")
    emit("managed_upload", bytes=len(bundle), wind_fields=info.get("wind_fields"), max_duration_s=max_duration_s)
    window = f"the first {max_duration_s/3600:.1f} h of the storm (reduced window for time)" if max_duration_s else "the full storm"
    prompt = f"""You are an independent auditor for San Francisco's storm plan (StormSim SF), Gemini 4 Argon's final plan.
/workspace/audit holds the exact job that our cloud runner executes: the frozen simulator (sim/), city grid + cached
wind fields (data/), locked cost/damage tables (data/reference), the plan (plan.py) and its sandbox export, job.json.
1. cd /workspace/audit && pip install -q --break-system-packages -r requirements.txt (pinned versions; required for
   bit-identical numerics) and run: NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR" python _remote_job.py
   It simulates {window} with the plan and prints @@LEDGER <hash>. Do NOT modify sim/ or data/.
2. While it runs, audit plan.py against the city with python (from sim.city_api import CityEditor; city=CityEditor()):
   pricing gaps (raw array edits or measures the cost planner can't see), floodwalls crossing buildings or not tying
   into high ground (city.elevation at the ends), shelters in buildings that flood (runs/<run_id>/result.json ledger
   + frames), evacuation routes or depots on flooded streets. Cite plan.py line numbers and numbers.
3. Read runs/{run_id}/result.json: ledger_hash, ledger total_usd, plan_cost total_usd, people metrics.
4. Write AUDIT.md (verdict, numbers, issues with evidence) and call report_verification with ledger_hash_match=false
   if you have no expected hash to compare against (the host compares), plus your ledger hash in the summary text."""
    verdict, prev, inp, final_text = None, None, prompt, ""
    for _ in range(6):
        kwp = {"previous_interaction_id": prev} if prev else {}
        iid = None
        for ev in client.interactions.create(agent=AGENT, agent_config={"type": "antigravity", "model": model}, input=inp,
                                             environment=env_id, tools=[{"type": "code_execution"}, REPORT_TOOL],
                                             stream=True, timeout=3600, **kwp):
            et = getattr(ev, "event_type", None)
            if et == "interaction.created":
                iid = ev.interaction.id
            elif et == "step.start":
                st = ev.step
                d = {"step": getattr(st, "type", None)}
                for k in ("name", "arguments"):
                    v = getattr(st, k, None)
                    if v:
                        d[k] = str(v)[:1500]
                emit("managed_step", **d)
            elif et == "step.delta" and getattr(ev.delta, "type", None) == "text" and ev.delta.text:
                emit("text", text=ev.delta.text, source="managed")
        it = client.interactions.get(iid)
        prev = it.id
        final_text = it.output_text or final_text
        done_ids = {getattr(s, "call_id", None) for s in it.steps or [] if s.type == "function_result"}
        pend = [s for s in it.steps or [] if s.type == "function_call" and s.id not in done_ids and s.name == "report_verification"]
        if it.status == "requires_action" and pend:
            verdict = dict(pend[0].arguments or {})
            emit("managed_verdict", **verdict)
            inp = [{"type": "function_result", "name": pend[0].name, "call_id": pend[0].id,
                    "result": [{"type": "text", "text": json.dumps({"recorded": True})}]}]
            continue
        if verdict or it.status != "completed":
            break
        inp = "Now call report_verification with your verdict."
    sandbox = {}
    for rel, key in ((f"workspace/audit/runs/{run_id}/result.json", "result"), ("workspace/audit/AUDIT.md", "audit")):
        try:
            b = client.environments.files.download(environment=env_id, path=rel)
            p = OUT / f"{job.id}_{Path(rel).name}"
            p.write_bytes(b if isinstance(b, (bytes, bytearray)) else str(b).encode())
            sandbox[key] = "/" + str(p.relative_to(REPO))
        except Exception as e:
            sandbox[key] = f"download failed: {e}"
    if str(sandbox.get("result", "")).startswith("/"):
        r = json.loads((REPO / sandbox["result"].lstrip("/")).read_text())
        sandbox.update(ledger_hash=r.get("ledger_hash"), total_usd=(r.get("ledger") or {}).get("total_usd"), mock_sim=r.get("mock"))
    if str(sandbox.get("audit", "")).startswith("/"):
        emit("document", kind="verification", title="Independent audit · Antigravity managed agent",
             markdown=(REPO / sandbox["audit"].lstrip("/")).read_text()[:60000], path=sandbox["audit"])
    if host_compare:
        th.join(timeout=900)
    res = {"environment_id": env_id, "verdict": verdict, "sandbox": sandbox, "host": host, "mock_sim": False,
           "max_duration_s": max_duration_s, "ledger_hash_match": bool(sandbox.get("ledger_hash")) and sandbox.get("ledger_hash") == host.get("ledger_hash"),
           "final_text": final_text[:3000], "secs": round(time.time() - t0, 1)}
    (OUT / f"{job.id}.json").write_text(json.dumps(res, indent=1, default=str))
    emit("managed_result", **{k: v for k, v in res.items() if k != "final_text"})
    return res


def staged_main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--max-duration", type=float, default=7200)
    ap.add_argument("--source-job", default=None)
    a = ap.parse_args()
    label = "Independent audit · Antigravity managed agent"
    job = new_job("managed", {"plan": a.plan, "label": label, "source_job": a.source_job})
    job.emit("job_start", label=label, plan=a.plan, source_job=a.source_job, agent=AGENT, model="gemini-4-argon",
             max_duration_s=a.max_duration)
    orig = job.emit

    def emit(type, **d):
        ev = orig(type, **d)
        if type not in ("text",):
            print(f"[{time.strftime('%H:%M:%S')}] {type}: {json.dumps(d, default=str)[:300]}", flush=True)
        return ev
    job.emit = emit
    res = staged_audit(job, Path(a.plan).read_text(), max_duration_s=a.max_duration or None)
    job.finish("completed", res)
    print(json.dumps({k: v for k, v in res.items() if k != "final_text"}, indent=1, default=str))
