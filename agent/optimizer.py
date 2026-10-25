"""City-hardening planning cell: a Gemini model writes `def apply(city)` plan code, runs the storm,
watches the replay, patches, submits, then writes an Emergency Operations Plan (EOP).

Phases (streamed as `phase` events):
  A  situation assessment  - data pack + no-plan ledger + no-plan replay video; city_summary/inspect/query
  B  plan authoring        - thinking_level high, long decode; first run_storm(plan_code)
  C  run / watch / patch   - each run_storm returns ledger deltas + replay video (+ plan screenshots);
                             the model critiques what it sees and patches, until submit or caps
  D  EOP document          - cited markdown Emergency Operations Plan; job waits for planner approval

Usage (CLI):  scripts/gem.sh .venv/bin/python -m agent.optimizer --model gemini-4-argon --budget 50e6
Events: agent.events.Job (JSONL in runs/_jobs/<id>.jsonl + in-memory list for SSE).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

from agent.events import Job, new_job
from agent import runner

REPO = Path(__file__).resolve().parent.parent
MODELS = ("gemini-4-argon", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite")
INLINE_VIDEO_MAX = 18 * 1024 * 1024

# ----------------------------------------------------------------------------- tools
TOOLS = [
    {"type": "function", "name": "city_summary",
     "description": "Overview of the city tile and the NO-PLAN storm run: grid/extent, counts, flood basins "
                    "(id, centroid, area, max depth, damage), worst-hit buildings, streets with failed trees, damage "
                    "by cause, people at risk, validation vs. the real 31.12.2022 storm, budget and API notes.",
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "inspect",
     "description": "Details for one target. Formats: 'building:<id>', 'basin:<id>', 'tree:<id>', 'street:<name>', "
                    "'region:<xmin>,<ymin>,<xmax>,<ymax>' (local metres), 'run:<run_id>' (a previous run's summary).",
     "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}},
    {"type": "function", "name": "query",
     "description": "Evaluate read-only Python against the unedited city (variables: city = CityEditor, np). "
                    "Either a single expression, or statements that set `result`. Runs in a no-network sandbox; "
                    "output truncated to 8000 chars. Use it to measure elevations, find streets/buildings, check geometry.",
     "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"type": "function", "name": "run_storm",
     "description": "Apply plan code (must define `def apply(city): ...`) in the sandbox and run the full storm "
                    "simulation. Returns damage ledger vs. no-plan (by cause, by building), plan cost with cited items, "
                    "people/life-safety metrics, BCR, top remaining damage locations, plus the replay VIDEO of the run "
                    "and screenshots of your plan in the 3D scene. Errors in your code come back as a traceback.",
     "parameters": {"type": "object", "properties": {
         "plan_code": {"type": "string", "description": "Complete Python source defining apply(city)."},
         "note": {"type": "string", "description": "One line: what changed vs. the previous run and why."}},
         "required": ["plan_code"]}},
    {"type": "function", "name": "submit",
     "description": "Submit the final plan (must be within budget). It is evaluated on the same deterministic storm.",
     "parameters": {"type": "object", "properties": {
         "plan_code": {"type": "string"},
         "rationale": {"type": "string", "description": "Why this plan: main measures, cost, expected benefit, residual risk."}},
         "required": ["plan_code", "rationale"]}},
]


def _read(p: Path, limit: int = 200_000) -> str:
    try:
        return p.read_text()[:limit]
    except Exception:
        return ""


def api_docs() -> str:
    parts = []
    for name in ("API_FOR_MODELS.md", "RESPONSE_API.md"):
        t = _read(REPO / "sim" / name)
        if t:
            parts.append(f"## sim/{name}\n{t}")
    if not (REPO / "sim" / "API_FOR_MODELS.md").exists():
        try:
            import inspect as _insp
            import sim.city_api as ca
            lines = ["## sim/city_api.py CityEditor (auto-generated reference)", (ca.__doc__ or "").strip()]
            for n, f in _insp.getmembers(ca.CityEditor):
                if n.startswith("_") or not callable(f):
                    continue
                try:
                    sig = str(_insp.signature(f)).replace("(self, ", "(").replace("(self)", "()")
                except Exception:
                    sig = "(...)"
                doc = (_insp.getdoc(f) or "").strip().replace("\n", " ")[:300]
                lines.append(f"- city.{n}{sig}" + (f": {doc}" if doc else ""))
            lines.append("- city.response -> emergency response planner (see RESPONSE_API.md)")
            parts.insert(0, "\n".join(lines))
        except Exception:
            pass
    if not parts:
        c = _read(REPO / "CONTRACT.md")
        i = c.find("## Plans")
        parts.append("## Plan API (from CONTRACT.md; API_FOR_MODELS.md not yet available)\n" + (c[i:i + 2500] if i >= 0 else c[:4000]))
        j = c.find("## v2")
        if j >= 0:
            parts.append(c[j:])
    return "\n\n".join(parts)


def system_prompt(budget: float, storm: str, max_runs: int, max_minutes: float) -> str:
    return f"""You are the lead engineer of the City and County of San Francisco's storm planning cell.
An El Nino atmospheric-river storm (`{storm}`) is about to hit the city tile (Mission, SoMa, Mission Bay, Downtown;
local metres from the grid SW corner, grid 4 m). You have a capital + operations budget of ${budget:,.0f}.

Goal: the cheapest effective, COMPLETE city storm plan that maximises damage avoided minus plan cost (and
life-safety), like a real city would: engineered infrastructure designed in detail (floodwalls with real
cross-sections tied into high ground, deployable gates at specific streets, pump stations, storage/tunnels,
inlet upgrades, permeable streets, bioswales, green roofs, building floodproofing, tree work, wind screens),
AND a full emergency response via `city.response` (shelters in real buildings, phased evacuation of vulnerable
residents from -72 h to 0, food/water depots and truck supply runs, sandbag/pump/tree/rescue/medical crews,
road closures, public alerts), AND any other action the city would take via `city.action(...)` (drain clearing,
sewer drawdown, towing cars from flood streets, de-energising, transit reroutes, school closures, hospital surge...).
Everything you build appears as 3D objects in the scene; you may author custom meshes for your own designs.

Rules (hard): the storm, solvers, damage functions and cost table are locked and deterministic (same plan ->
same ledger). You never set prices: costs are measured by the cost planner from the physical diff and cited unit
costs in data/reference; `city.action` must reference existing cost items. Over-budget plans can't be submitted.
Score = damage avoided - plan cost; also report BCR (FEMA BCA >= 1) and at-risk person-hours / lives.

Workflow:
 Phase A - situation assessment: read the data pack, the no-plan ledger and WATCH the no-plan replay video.
   Use city_summary / inspect / query to find where water comes from and pools, what drives damage, who is at risk.
   End phase A with a concise written assessment (no run_storm yet).
 Phase B - write the complete plan as Python `def apply(city): ...` (as long as it needs to be; helper functions,
   loops over buildings/streets are fine) and call run_storm.
 Phase C - after every run: first WATCH the replay video and the plan screenshots and write a short critique of what
   you SEE (where water still gets in, walls that don't tie into high ground, shelters that flood, routes through
   flooded underpasses, wasted spend), then patch and rerun. Cut measures that don't pay for themselves.
   You have at most {max_runs} storm runs and {max_minutes:.0f} minutes. Then call submit.
 Phase D - you will then be asked for the Emergency Operations Plan document.

Name and justify everything you build: EVERY helper and city.response call accepts extra keyword arguments
label="<human name, specific place + key spec>" (e.g. "Folsom St floodwall, 1.2 m, ties into Bernal slope"),
notes="<one sentence: why, what it protects, with numbers>" (e.g. "Blocks overland flow entering the 17th & Folsom
basin from Mission Creek; protects 312 ground-floor units"), and optional category=
flood_defence|drainage|emergency|logistics|operations. Planners review your plan object by object in a 3D Plan
Inspector and build tour, so unnamed or unjustified objects count against you. Costs are filled in by the cost planner.

Coordinates: tool outputs, the data pack and the city API use LOCAL metres from the grid SW corner (x east 0..3548,
y north 0..4256); the API also accepts UTM 10N (x > 100000). Grid cells are 4 m, arrays indexed [iy, ix], iy=0 south.

{api_docs()}
"""


# ----------------------------------------------------------------------------- helpers
def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def ledger_hash(run_dir: str | None) -> str | None:
    if not run_dir:
        return None
    res = runner.load_result(Path(run_dir))
    led = res.get("ledger") or {}
    return hashlib.sha256(json.dumps(led, sort_keys=True, default=str).encode()).hexdigest()[:16]


def video_part(client, path: str | None, job: Job | None = None):
    if not path or not Path(path).exists():
        return None
    data = Path(path).read_bytes()
    if len(data) <= INLINE_VIDEO_MAX:
        return {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(data).decode()}
    f = client.files.upload(file=path)
    while not f.state or f.state.name != "ACTIVE":
        time.sleep(2)
        f = client.files.get(name=f.name)
    return {"type": "video", "mime_type": f.mime_type or "video/mp4", "uri": f.uri}


def image_parts(paths):
    out = []
    for p in paths or []:
        try:
            out.append({"type": "image", "mime_type": "image/png", "data": base64.b64encode(Path(p).read_bytes()).decode()})
        except Exception:
            pass
    return out


def add_text(inp, text: str):
    """Append an instruction to a turn input (steps list -> user_input step; content list -> text part)."""
    if isinstance(inp, str):
        return inp + "\n\n" + text
    inp = list(inp or [])
    if any(isinstance(x, dict) and x.get("type") in ("function_result", "user_input") for x in inp):
        return inp + [{"type": "user_input", "content": [{"type": "text", "text": text}]}]
    return inp + [{"type": "text", "text": text}]


def rel(p) -> str | None:
    try:
        return "/" + str(Path(p).resolve().relative_to(REPO))
    except Exception:
        return str(p) if p else None


# ----------------------------------------------------------------------------- planning cell
class PlanningCell:
    def __init__(self, job: Job, model: str, storm: str = "elnino_ar_2026", budget: float = 50e6,
                 max_runs: int = 8, max_minutes: float = 15, thinking_level: str | None = None,
                 watch_video: bool = True, screenshots: bool = True, pack: str = "full",
                 long_decode: bool = False, write_eop: bool = True, client=None):
        from google import genai
        assert model in MODELS, f"model must be one of {MODELS}"
        self.job, self.model, self.storm, self.budget = job, model, storm, float(budget)
        self.max_runs, self.max_minutes = int(max_runs), float(max_minutes)
        self.thinking = thinking_level or "medium"
        self.thinking_plan = thinking_level or "high"
        self.watch_video, self.screenshots, self.pack = watch_video, screenshots, pack
        self.long_decode, self.write_eop = long_decode, write_eop
        self.client = client or genai.Client()
        self.prev_id: str | None = None
        self.runs: list[dict] = []
        self.by_hash: dict[str, dict] = {}
        self.usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0, "turns": 0}
        self.t0 = time.time()
        self.phase = None
        self.submitted: dict | None = None
        self.base_dir = None
        self.base = None
        self.pack_meta: dict = {}

    # ---- events
    def emit(self, type, **kw):
        return self.job.emit(type, model=self.model, **kw)

    def set_phase(self, p, title):
        if self.phase != p:
            self.phase = p
            self.emit("phase", phase=p, title=title, elapsed_s=round(time.time() - self.t0, 1))

    # ---- model turn (streamed)
    def turn(self, input, tools=TOOLS, thinking=None, tool_choice=None, max_output_tokens=None, extra_tools=None):
        gen = {"thinking_level": thinking or self.thinking, "thinking_summaries": "auto"}
        if tool_choice:
            gen["tool_choice"] = tool_choice
        if max_output_tokens:
            gen["max_output_tokens"] = max_output_tokens
        all_tools = list(tools or []) + list(extra_tools or [])
        kw = dict(model=self.model, input=input, generation_config=gen,
                  system_instruction=self.sys_prompt, timeout=1800)
        if all_tools:
            kw["tools"] = all_tools
        if self.prev_id:
            kw["previous_interaction_id"] = self.prev_id
        for attempt in range(4):
            try:
                if self.long_decode and max_output_tokens and max_output_tokens > 65536:
                    return self._turn_background(kw)
                return self._turn_stream(kw)
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:400]}"
                self.emit("api_error", message=msg, attempt=attempt)
                if attempt == 3 or "ValidationError" in msg or ("400" in msg[:60] and "INVALID" in msg.upper()):
                    raise
                time.sleep(5 * (attempt + 1))

    def _turn_stream(self, kw):
        iid = None
        buf = {"thought": "", "text": "", "args": ""}
        last_flush = time.time()
        cur_fn = None

        def flush(force=False):
            nonlocal last_flush
            if not force and time.time() - last_flush < 0.35:
                return
            if buf["thought"]:
                self.emit("thought", text=buf["thought"], phase=self.phase)
                buf["thought"] = ""
            if buf["text"]:
                self.emit("text", text=buf["text"], phase=self.phase)
                buf["text"] = ""
            if buf["args"]:
                self.emit("code_delta", fn=cur_fn, text=buf["args"])
                buf["args"] = ""
            last_flush = time.time()

        for ev in self.client.interactions.create(stream=True, **kw):
            et = getattr(ev, "event_type", None)
            if et == "interaction.created":
                iid = ev.interaction.id
            elif et == "step.start":
                st = getattr(ev, "step", None)
                stype = getattr(st, "type", None)
                if stype == "function_call":
                    flush(True)
                    cur_fn = getattr(st, "name", None)
                    self.emit("tool_call_start", name=cur_fn)
            elif et == "step.delta":
                d = ev.delta
                dt = getattr(d, "type", None)
                if dt == "thought_summary":
                    c = getattr(d, "content", None)
                    buf["thought"] += getattr(c, "text", "") or ""
                elif dt == "text":
                    buf["text"] += d.text or ""
                elif dt == "arguments_delta" and cur_fn in ("run_storm", "submit"):
                    buf["args"] += getattr(d, "arguments", "") or ""
                flush()
            elif et == "interaction.completed":
                if iid is None:
                    iid = ev.interaction.id
        flush(True)
        it = self.client.interactions.get(iid)
        return self._finish_turn(it)

    def _turn_background(self, kw):
        it = self.client.interactions.create(background=True, **kw)
        self.emit("status", status="long decode running in background", interaction=it.id)
        while it.status in ("in_progress", "queued"):
            time.sleep(5)
            it = self.client.interactions.get(it.id)
        for s in it.steps or []:
            if s.type == "thought" and getattr(s, "summary", None):
                self.emit("thought", text="".join(getattr(c, "text", "") or "" for c in s.summary), phase=self.phase)
            elif s.type == "model_output":
                self.emit("text", text="".join(getattr(c, "text", "") or "" for c in (s.content or [])), phase=self.phase)
        return self._finish_turn(it)

    def _finish_turn(self, it):
        self.prev_id = it.id
        self.usage["turns"] += 1
        u = getattr(it, "usage", None)
        if u is not None:
            for k_out, keys in (("input_tokens", ("total_input_tokens",)), ("output_tokens", ("total_output_tokens",)),
                                ("thought_tokens", ("total_thought_tokens",)), ("cached_tokens", ("total_cached_tokens",))):
                for k in keys:
                    v = getattr(u, k, None)
                    if isinstance(v, int):
                        self.usage[k_out] += v
        # only the trailing steps of this turn matter: function calls without results
        steps = it.steps or []
        done = {getattr(s, "call_id", None) for s in steps if s.type == "function_result"}
        calls = [s for s in steps if s.type == "function_call" and s.id not in done]
        text = it.output_text or ""
        return it, calls, text

    # ---- tool implementations
    def tool_city_summary(self, args):
        from agent.tools import city_summary
        return city_summary(self.storm, self.base_dir, self.base, self.budget)

    def tool_inspect(self, args):
        from agent.tools import inspect_target
        return inspect_target(args.get("target", ""), self.storm, self.base_dir, self.base, self.runs)

    def tool_query(self, args):
        from server.sandbox import run_query
        r = run_query(args.get("code", ""), timeout=60)
        return {k: r.get(k) for k in ("ok", "result", "error", "stdout", "truncated") if r.get(k) not in (None, "")}

    def evaluate(self, plan_code: str, label: str, note: str = "") -> dict:
        h = sha(plan_code)
        if h in self.by_hash:
            return self.by_hash[h]
        n = len(self.runs) + 1
        self.emit("code", code=plan_code, plan_hash=h, run_index=n, note=note, lines=plan_code.count("\n") + 1)
        r = runner.execute_run(self.storm, plan_code, label=f"{self.model} {label} #{n}", budget=self.budget,
                               log=lambda t, **k: self.emit(t, **k))
        r["plan_hash"] = h
        r["run_index"] = n
        r["note"] = note
        if r.get("ok"):
            r["ledger_hash"] = ledger_hash(r.get("run_dir"))
            if self.screenshots:
                try:
                    from agent.screens import plan_screenshots
                    r["screens"] = plan_screenshots(r["run_dir"], r.get("plan_objects"))
                except Exception as e:
                    self.emit("status", status=f"screenshots unavailable: {e}")
            for o in (r.get("plan_objects") or [])[:400]:
                self.emit("plan_object", run_id=r["run_id"], object=o)
            s = r["summary"]
            self.emit("run_result", run_id=r["run_id"], run_index=n, plan_hash=h, ledger_hash=r["ledger_hash"],
                      summary=s, video_url=rel(r.get("video")), screens=[rel(p) for p in r.get("screens") or []],
                      mock=r.get("mock"))
        else:
            self.emit("run_error", run_index=n, plan_hash=h, stage=r.get("stage"), error=r.get("error"))
        self.runs.append(r)
        self.by_hash[h] = r
        return r

    def tool_run_storm(self, args):
        code = args.get("plan_code", "")
        r = self.evaluate(code, "run", args.get("note", ""))
        if not r.get("ok"):
            return {"ok": False, "stage": r.get("stage"), "error": r.get("error"), "stdout": r.get("stdout")}, []
        payload = {"ok": True, "run_id": r["run_id"], "run_index": r["run_index"],
                   "runs_left": max(0, self.max_runs - len(self.runs)),
                   "minutes_left": round(max(0, self.max_minutes - (time.time() - self.t0) / 60), 1),
                   **r["summary"]}
        if r.get("sandbox", {}).get("stdout"):
            payload["plan_stdout"] = r["sandbox"]["stdout"][-1500:]
        media = []
        if self.watch_video:
            v = video_part(self.client, r.get("video"), self.job)
            if v:
                media.append({"type": "text", "text": "Replay video of this run (top-down + oblique; water depth, failed trees, damage):"})
                media.append(v)
        if r.get("screens"):
            media.append({"type": "text", "text": "Screenshots of your plan in the 3D city:"})
            media += image_parts(r["screens"])
        return payload, media

    def tool_submit(self, args, forced=False):
        code = args.get("plan_code", "")
        r = self.evaluate(code, "final")
        if not r.get("ok"):
            return {"ok": False, "error": f"submitted plan failed: {r.get('error')}"}, False
        s = r["summary"]
        if s.get("plan_cost_usd", 0) > self.budget and forced:
            best = self._best_run()
            if best and best.get("_code") and best["plan_hash"] != r["plan_hash"]:
                self.emit("status", status="forced submission over budget: substituting the best within-budget run")
                r, code, s = best, best["_code"], best["summary"]
                args = {**args, "rationale": "[auto-substituted best within-budget run] " + args.get("rationale", "")}
        if s.get("plan_cost_usd", 0) > self.budget and not forced:
            return {"ok": False, "error": f"plan costs ${s['plan_cost_usd']:,.0f} > budget ${self.budget:,.0f}; cut measures and resubmit"}, False
        self.submitted = {"run": r, "rationale": args.get("rationale", ""), "plan_code": code, "forced": forced}
        self.emit("submit", run_id=r["run_id"], plan_hash=r["plan_hash"], rationale=args.get("rationale", ""),
                  summary=s, over_budget=s.get("plan_cost_usd", 0) > self.budget)
        return {"ok": True, "accepted": True, **s}, True

    def dispatch(self, call, forced_submit=False):
        name, args = call.name, dict(call.arguments or {})
        shown = {k: (v if k not in ("plan_code", "code") else f"<{len(v)} chars>") for k, v in args.items()}
        self.emit("tool_call", name=name, args=shown, code=args.get("code") if name == "query" else None)
        media, submitted = [], False
        try:
            if name == "city_summary":
                res = self.tool_city_summary(args)
            elif name == "inspect":
                res = self.tool_inspect(args)
            elif name == "query":
                res = self.tool_query(args)
            elif name == "run_storm":
                if len(self.runs) >= self.max_runs:
                    res = {"ok": False, "error": "storm-run budget exhausted; call submit with your best plan"}
                else:
                    res, media = self.tool_run_storm(args)
            elif name == "submit":
                res, submitted = self.tool_submit(args, forced=forced_submit)
            else:
                res = {"ok": False, "error": f"unknown tool {name}"}
        except Exception as e:
            import traceback
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            self.emit("error", message=f"tool {name} failed: {e}", traceback=traceback.format_exc()[-2000:])
        txt = json.dumps(res, default=str)
        self.emit("tool_result", name=name, ok=res.get("ok", True) if isinstance(res, dict) else True,
                  preview=txt[:1500])
        return {"type": "function_result", "name": name, "call_id": call.id,
                "result": [{"type": "text", "text": txt}] + media}, submitted

    # ---- phases
    def briefing(self) -> list:
        parts = []
        text = (f"Storm: {self.storm}. Budget: ${self.budget:,.0f}. Max storm runs: {self.max_runs}; time cap "
                f"{self.max_minutes:.0f} min.\n\nNo-plan run ({self.base_dir.name}) summary:\n"
                + json.dumps(runner.summarize(self.base, None), default=str)[:20000])
        if self.pack != "none":
            try:
                from agent.datapack import build_pack
                pack_text, meta = build_pack(self.storm, self.base_dir, level=self.pack, model=self.model)
                self.pack_meta = meta
                self.emit("datapack", **{k: v for k, v in meta.items() if k not in ("sections_text", "model")})
                text += "\n\n# DATA PACK\n" + pack_text
            except Exception as e:
                self.emit("status", status=f"data pack unavailable: {type(e).__name__}: {e}")
        parts.append({"type": "text", "text": text})
        if self.watch_video:
            v = video_part(self.client, str(runner.ensure_video(self.base_dir) or ""), self.job)
            if v:
                parts += [{"type": "text", "text": "No-plan replay video (what happens if the city does nothing):"}, v]
        parts.append({"type": "text", "text": "Phase A: situation assessment. Investigate with city_summary/inspect/query "
                      "as needed (no run_storm yet), then write your assessment: main flood mechanisms and basins, "
                      "damage drivers, people at risk, and the plan concept with a rough budget split."})
        return parts

    def run(self) -> dict:
        self.emit("job_start", storm=self.storm, budget_usd=self.budget, max_runs=self.max_runs,
                  max_minutes=self.max_minutes, thinking=self.thinking, thinking_plan=self.thinking_plan,
                  watch_video=self.watch_video, mock_sim=runner.get_run_storm()[1])
        self.sys_prompt = system_prompt(self.budget, self.storm, self.max_runs, self.max_minutes)
        self.set_phase("A", "Situation assessment")
        self.base_dir, self.base = runner.baseline(self.storm, log=lambda t, **k: self.emit(t, **k))
        self.t0 = time.time()  # the planning clock starts once the no-plan baseline exists
        bvid = runner.ensure_video(self.base_dir)
        self.emit("baseline", run_id=self.base_dir.name, summary=runner.summarize(self.base, None),
                  video_url=rel(bvid))
        # Phase A: assessment, read-only tools
        ro_tools = [t for t in TOOLS if t["name"] in ("city_summary", "inspect", "query")]
        inp = self.briefing()
        a_deadline = self.t0 + 60 * self.max_minutes * 0.25
        for i in range(12):
            last = i == 11 or time.time() > a_deadline
            if last and isinstance(inp, list):
                inp = add_text(inp, "Assessment time is up: write your situation assessment now (no more tools).")
            it, calls, text = self.turn(inp, tools=None if last else ro_tools)
            if not calls:
                self.emit("document", kind="assessment", title="Situation assessment", markdown=text)
                break
            inp = [self.dispatch(c)[0] for c in calls]
        # Phase B: write the plan
        self.set_phase("B", "Writing the plan")
        inp = [{"type": "text", "text": "Phase B: write the complete plan now as Python `def apply(city): ...` "
                "(infrastructure + city.response emergency plan + city.action measures) and call run_storm with it."}]
        mot = 200_000 if self.long_decode else None
        nudges = 0
        while True:
            over_time = (time.time() - self.t0) / 60 > self.max_minutes
            out_of_runs = len(self.runs) >= self.max_runs
            force = over_time or out_of_runs
            tc = None
            if force:
                tc = {"allowed_tools": {"mode": "any", "tools": ["submit"]}}
                if isinstance(inp, list):
                    inp = add_text(inp, "Run/time budget is used up. Call submit now with your best within-budget plan.")
            elif not self.runs and time.time() > self.t0 + 60 * self.max_minutes * 0.55:
                tc = {"allowed_tools": {"mode": "any", "tools": ["run_storm"]}}
                if isinstance(inp, list):
                    inp = add_text(inp, "Time check: stop investigating and call run_storm with your complete plan now.")
            it, calls, text = self.turn(inp, thinking=self.thinking_plan if self.phase == "B" else self.thinking,
                                        tool_choice=tc, max_output_tokens=mot if self.phase == "B" else None)
            if not calls:
                nudges += 1
                if nudges > 3:
                    if self.runs:
                        best = self._best_run()
                        if best:
                            self.tool_submit({"plan_code": best["_code"], "rationale": "auto-submitted best run (model stopped calling tools)"}, forced=True)
                    break
                inp = [{"type": "text", "text": "Continue: use the tools (run_storm to test, submit when done)."}]
                continue
            results = []
            done = False
            for c in calls:
                if c.name == "run_storm":
                    self.set_phase("C", "Run, watch, critique, patch")
                fr, submitted = self.dispatch(c, forced_submit=force)
                if c.name in ("run_storm", "submit") and self.runs:
                    self.runs[-1]["_code"] = (c.arguments or {}).get("plan_code", "")
                results.append(fr)
                done = done or submitted
            if done:
                break
            inp = results
            if force and not done and nudges > 5:
                break
            if force:
                nudges += 1
        # Phase D: EOP
        final = self.final_report()
        if self.write_eop and self.submitted:
            self.set_phase("D", "Emergency Operations Plan")
            try:
                eop = self.eop()
                final["eop_path"] = eop
            except Exception as e:
                self.emit("error", message=f"EOP failed: {e}")
        final["usage"] = self.usage
        final["wall_clock_s"] = round(time.time() - self.t0, 1)
        out = REPO / "runs" / "_jobs" / f"{self.job.id}.final.json"
        out.write_text(json.dumps(final, indent=1, default=str))
        self.emit("final", report=final)
        self.job.status = "awaiting_approval" if self.submitted else self.job.status
        return final

    def _best_run(self):
        ok = [r for r in self.runs if r.get("ok") and r["summary"].get("plan_cost_usd", 0) <= self.budget]
        return max(ok, key=lambda r: r["summary"].get("net_benefit_usd") or -1e18) if ok else None

    def final_report(self) -> dict:
        sub = self.submitted
        rep = {"model": self.model, "storm": self.storm, "budget_usd": self.budget, "runs_used": len(self.runs),
               "pack": self.pack_meta.get("summary"), "thinking": self.thinking, "thinking_plan": self.thinking_plan,
               "baseline_run_id": self.base_dir.name if self.base_dir else None}
        if not sub:
            rep["submitted"] = False
            return rep
        r = sub["run"]
        s = r["summary"]
        bc = s.get("by_cause", {})
        life = next((v for k, v in bc.items() if "life" in k), None)
        ppl = s.get("people", {})

        def avoided(d):
            try:
                return round((d.get("baseline") or 0) - (d.get("now") or 0), 2)
            except Exception:
                return None
        rep.update(submitted=True, run_id=r["run_id"], plan_hash=r["plan_hash"], ledger_hash=r.get("ledger_hash"),
                   plan_cost_usd=s.get("plan_cost_usd"), damage_usd=s.get("damage_total_usd"),
                   baseline_damage_usd=s.get("baseline_damage_usd"), damage_avoided_usd=s.get("damage_avoided_usd"),
                   net_benefit_usd=s.get("net_benefit_usd"), bcr=s.get("bcr"),
                   life_safety_avoided_usd=-life["delta"] if life else None,
                   at_risk_person_hours_avoided=avoided(ppl.get("at_risk_person_hours", {})),
                   expected_fatalities_avoided=avoided(ppl.get("expected_fatalities", {})),
                   plan_lines=sub["plan_code"].count("\n") + 1, plan_chars=len(sub["plan_code"]),
                   rationale=sub["rationale"], over_budget=s.get("plan_cost_usd", 0) > self.budget,
                   video_url=rel(r.get("video")), mock_sim=r.get("mock"))
        plan_p = REPO / "runs" / "_jobs" / f"{self.job.id}.plan.py"
        plan_p.write_text(sub["plan_code"])
        rep["plan_path"] = rel(plan_p)
        return rep

    def eop(self) -> str:
        s = self.submitted["run"]["summary"]
        prompt = ("Phase D: write the Emergency Operations Plan for this storm as a complete markdown document for the "
                  "SF Department of Emergency Management and SFPUC: 1) situation and threat, 2) concept of operations and "
                  "phased timeline (-72 h ... +24 h) with triggers, 3) infrastructure measures (what, where, design, cost "
                  "with cited unit costs), 4) emergency response: shelters, evacuation, logistics (meals/water vs need), "
                  "crews, road closures, public alerts, 5) results from the simulation (damage avoided, BCR, people at risk, "
                  "residual risk) and 6) sources (cite the data pack documents and unit-cost sources; use URLs). Final run "
                  "results: " + json.dumps({k: s.get(k) for k in ("plan_cost_usd", "damage_avoided_usd", "bcr", "people",
                                                                   "net_benefit_usd", "baseline_damage_usd")}, default=str))
        it, calls, text = self.turn([{"type": "text", "text": prompt}], tools=None, thinking=self.thinking_plan)
        p = REPO / "runs" / "_jobs" / f"{self.job.id}.eop.md"
        p.write_text(text or "")
        self.emit("document", kind="eop", title="Emergency Operations Plan", markdown=text, path=rel(p))
        return rel(p)


def optimize(job: Job, model: str = "gemini-4-argon", storm: str = "elnino_ar_2026", budget_usd: float = 50e6, **kw) -> dict:
    return PlanningCell(job, model, storm, budget_usd, **kw).run()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-4-argon", choices=MODELS)
    ap.add_argument("--storm", default="elnino_ar_2026")
    ap.add_argument("--budget", type=float, default=50e6)
    ap.add_argument("--max-runs", type=int, default=8)
    ap.add_argument("--max-minutes", type=float, default=15)
    ap.add_argument("--thinking", default=None, choices=[None, "low", "medium", "high"])
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-screens", action="store_true")
    ap.add_argument("--no-eop", action="store_true")
    ap.add_argument("--pack", default="full", choices=["full", "medium", "small", "none"])
    ap.add_argument("--long-decode", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    job = new_job("optimize", {"model": a.model, "storm": a.storm, "budget_usd": a.budget})
    orig = job.emit

    def emit(type, **d):
        ev = orig(type, **d)
        if not a.quiet:
            short = {k: v for k, v in d.items() if k not in ("model",)}
            s = json.dumps(short, default=str)
            print(f"[{time.strftime('%H:%M:%S')}] {type}: {s[:300]}", flush=True)
        return ev
    job.emit = emit
    res = optimize(job, a.model, a.storm, a.budget, max_runs=a.max_runs, max_minutes=a.max_minutes,
                   thinking_level=a.thinking, watch_video=not a.no_video, screenshots=not a.no_screens,
                   write_eop=not a.no_eop, pack=a.pack, long_decode=a.long_decode)
    print(json.dumps({k: v for k, v in res.items() if k != "rationale"}, indent=1, default=str))
    print("events:", job.jsonl)


if __name__ == "__main__":
    main()
