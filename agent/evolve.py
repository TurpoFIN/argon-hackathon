"""Evolve: AlphaEvolve-style evolution of city storm plans, scored by the deterministic StormSim.

A plan is Python code (`def apply(city): ...`). Each generation:
  1. Architect  - Gemini 4 Argon reads the whole program database (every scored plan, its ledger and lineage)
                  and writes insights + one research direction per mutation slot, then writes one crossover
                  plan that combines the strongest ideas of two elites (full code).
  2. Mutators   - Gemini 3.8 Flash workers, in parallel, each take a parent + two inspirations + a direction
                  and return SEARCH/REPLACE diff blocks against the parent (AlphaEvolve's diff format).
  3. Tweak      - a no-LLM numeric hill-climb: perturbs 1-3 sizing literals of an elite (heights, m3/s, volumes).
  4. Cascade    - compile check -> sandboxed cost estimate (Landlock, no network; locked cited cost planner)
                  -> over budget or crashing plans go back to their author once with the error -> full storm sim
                  (separate processes; same storm + plan gives the same ledger).
  5. Database   - MAP-Elites archive: dominant strategy (grey/green/building/response/trees/mixed) x cost band;
                  each cell keeps its best plan. Parents are sampled across cells, so diversity survives.
Fitness = damage avoided - plan cost (net benefit), within budget.

State for the UI: runs/evolve/<job>/state.json (rewritten on every change), plans in runs/evolve/<job>/plans/.
Events go through agent.events.Job like the optimizer (SSE at /api/jobs/<id>/events).

CLI: scripts/gem.sh .venv/bin/python -m agent.evolve --generations 4 --population 8
     --no-llm runs seeds + tweak only (no API calls), for testing the pipeline.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import hashlib
import json
import multiprocessing as mp
import os
import random
import re
import threading
import time
import uuid
from pathlib import Path

from agent import runner
from agent.events import Job, new_job

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "runs" / "evolve"
ARCHITECT = "gemini-4-argon"
MUTATOR = "gemini-3.8-flash"

STRATEGIES = ["grey", "green", "building", "response", "trees", "mixed"]
STRATEGY_LABELS = {"grey": "Grey infrastructure", "green": "Green infrastructure", "building": "Building protection",
                   "response": "Emergency response", "trees": "Trees & wind", "mixed": "Mixed portfolio"}
CATEGORY_STRATEGY = {"flood_defence": "grey", "grey_infrastructure": "grey", "earthworks": "grey",
                     "green_infrastructure": "green", "building_protection": "building", "operations": "response",
                     "response": "response", "emergency_response": "response", "trees": "trees", "wind": "trees"}
SEED_HINTS = [
    ("grey", "Grey infrastructure first: storage vaults, pump stations, a relief tunnel and inlet upgrades sized to the "
             "worst flood basins (Mission Creek, 17th & Folsom), plus a floodwall where the bay overtops."),
    ("green", "Green and distributed: permeable streets, bioswales and green roofs in the catchments that drain into the "
              "worst basins, plus inlet upgrades at the sags. Cheap per m2, spread where runoff starts."),
    ("building", "Protect what is worth most: dry floodproofing of the highest-damage buildings, deployable barriers at "
                 "the streets that feed them, sandbags, and towing cars out of the flood streets."),
    ("response", "Life safety and response: shelters in flood-safe schools, phased evacuation of vulnerable residents, "
                 "food/water depots and supply runs, pump and sandbag crews, road closures and alerts; modest grey works."),
    ("trees", "Wind and trees: prune and brace the at-risk street trees on the gust corridors, wind screens where the "
              "canyons accelerate, plus cheap drain clearing and inlet upgrades for the flood basins."),
    ("mixed", "Balanced portfolio: one strong grey measure for the worst basin, green infrastructure upstream, "
              "floodproofing for the top buildings and a full emergency response."),
]
REVIEWER = "gemini-3.5-flash-lite"
# Specialist mutator agents (Gemini 3.8 Flash with a role); the architect dispatches each direction to one of them.
SPECIALISTS = {
    "hydrologist": ("Hydrologist", "#5fe1ff", "water routing: where runoff starts and pools; storage, pumps, tunnels, inlets, regrading",
                    "You are the team's hydrologist. Think in catchments, flow paths, sags and sewer capacity: size storage (m3) "
                    "and pumps (m3/s) to the basin volumes, put inlets and pumps at the low points, and stop water upstream."),
    "cost_engineer": ("Cost engineer", "#ffd166", "value engineering: cuts measures that do not pay back, resizes to the margin",
                      "You are the team's cost engineer. Find the measures whose cost exceeds the damage they avoid and cut or "
                      "shrink them; move money to the measures with the best marginal benefit-cost ratio."),
    "emergency_manager": ("Emergency manager", "#ff7ab6", "people and response: shelters, evacuation, crews, logistics, alerts",
                          "You are the team's emergency manager (SF DEM). Cut life-safety loss: flood-safe shelters, early phased "
                          "evacuation of vulnerable residents, crews, supply runs, road closures and alerts, timed before landfall."),
    "urban_forester": ("Urban forester", "#3fd0a6", "trees and wind: pruning, bracing, removal, wind screens on gust corridors",
                       "You are the team's urban forester. Reduce tree failures and strike damage on the gust corridors: prune and "
                       "brace the at-risk trees, remove the hazardous ones, add wind screens where street canyons accelerate."),
    "green_designer": ("Green-infra designer", "#5dff9d", "permeable streets, bioswales and green roofs upstream of the sags",
                       "You are the team's green-infrastructure designer. Retain and infiltrate water where it falls: permeable "
                       "streets, bioswales and green roofs in the catchments that drain into the worst basins."),
    "building_engineer": ("Building engineer", "#ffb54a", "building protection: floodproofing, barriers, sandbags at entrances",
                          "You are the team's building engineer. Protect the highest-value buildings that still flood: dry "
                          "floodproofing up to 0.9 m, deployable barriers and sandbags at the streets that feed them."),
}
SEED_AGENT = {"grey": "hydrologist", "green": "green_designer", "building": "building_engineer",
              "response": "emergency_manager", "trees": "urban_forester", "mixed": "cost_engineer"}


def roster(architect_model: str, mutator_model: str, use_llm: bool) -> list[dict]:
    out = []
    if use_llm:
        out.append({"key": "architect", "label": "Architect", "model": architect_model, "color": "#b48cff",
                    "role": "reads the whole archive, dispatches work to the specialists, writes a crossover each generation"})
        out += [{"key": k, "label": v[0], "model": mutator_model, "color": v[1], "role": v[2]} for k, v in SPECIALISTS.items()]
        out.append({"key": "reviewer", "label": "Reviewer", "model": REVIEWER, "color": "#9fb3ff",
                    "role": "predicts each change's effect before it is simulated (calibration is scored)"})
    out += [{"key": "tweak", "label": "Hill-climber", "model": None, "color": "#ffb54a",
             "role": "no LLM: rescales sizing literals of an elite"},
            {"key": "sandbox", "label": "Sandbox + cost planner", "model": None, "color": "#7f9cff",
             "role": "runs the plan with no network and prices it from cited unit costs"},
            {"key": "sim", "label": "Storm simulator", "model": None, "color": "#4fb8ff",
             "role": "deterministic storm: flood, wind, trees, people, damage"}]
    return out


TWEAK_KEYS = ("height_m", "m3s", "volume_m3", "intake_m3s", "factor", "width_m", "depth_m", "capacity_m3s",
              "retention_mm", "capacity", "trucks", "dz_m", "n")
TWEAK_FACTORS = (0.6, 0.75, 0.85, 1.15, 1.3, 1.5)
TWEAK_KW = re.compile(r"\b(" + "|".join(TWEAK_KEYS) + r")(\s*=\s*)(\d+(?:\.\d+)?)\b")
TWEAK_POS = re.compile(r"[\])]\s*,\s*(\d+(?:\.\d+)?)\s*[,)]")


# ----------------------------------------------------------------------------- helpers
def norm_code(code: str) -> str:
    return "\n".join(line.rstrip() for line in (code or "").strip().splitlines()) + "\n"


def code_hash(code: str) -> str:
    return hashlib.sha256(norm_code(code).encode()).hexdigest()[:16]


def check_code(code: str) -> str | None:
    """None if the code parses and defines apply(city); else an error message."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    if not any(isinstance(n, ast.FunctionDef) and n.name == "apply" for n in tree.body):
        return "the plan must define a top-level `def apply(city):`"
    return None


def extract_code(text: str) -> str | None:
    """Full plan from a ```python block (or the raw text if it is code)."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
    for b in sorted(blocks, key=len, reverse=True):
        if "def apply" in b:
            return b.strip() + "\n"
    if text and text.lstrip().startswith(("def apply", "import ", "from ", "#")) and "def apply" in text:
        return text.strip() + "\n"
    return None


DIFF_RE = re.compile(r"<{5,}\s*SEARCH\s*\n(.*?)\n?={5,}\s*\n(.*?)\n?>{5,}\s*REPLACE", re.S)


def apply_diff(parent: str, text: str) -> tuple[str | None, int, list[str]]:
    """Apply SEARCH/REPLACE blocks to parent. Returns (child or None, n_applied, problems)."""
    blocks = DIFF_RE.findall(text or "")
    child, n, problems = parent, 0, []
    for search, replace in blocks:
        if search in child:
            child = child.replace(search, replace, 1)
            n += 1
            continue
        # tolerate trailing-whitespace / indentation-of-blank-line differences
        s_lines = [ln.rstrip() for ln in search.splitlines()]
        c_lines = child.splitlines()
        hit = None
        for i in range(len(c_lines) - len(s_lines) + 1):
            if [ln.rstrip() for ln in c_lines[i:i + len(s_lines)]] == s_lines:
                hit = i
                break
        if hit is None:
            problems.append("SEARCH block not found in parent: " + search.strip().splitlines()[0][:120] if search.strip() else "empty SEARCH")
            continue
        c_lines[hit:hit + len(s_lines)] = replace.splitlines()
        child = "\n".join(c_lines) + "\n"
        n += 1
    return (child if n else None), n, problems


def first_line(text: str, key: str = "NOTE:") -> str:
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s.upper().startswith(key):
            return s[len(key):].strip()[:240]
    return ""


def parse_json(text: str):
    """First JSON object in text (```json fenced or bare)."""
    m = re.search(r"```(?:json)?\s*\n(\{.*?\})\s*```", text or "", re.S)
    cands = [m.group(1)] if m else []
    i = (text or "").find("{")
    j = (text or "").rfind("}")
    if i >= 0 and j > i:
        cands.append(text[i:j + 1])
    for c in cands:
        try:
            return json.loads(c)
        except Exception:
            continue
    return None


def money(x) -> str:
    try:
        x = float(x)
    except Exception:
        return "?"
    return f"${x / 1e6:,.1f}M"


def strategy_of(cost_items: list[dict] | None) -> tuple[str, dict]:
    by = {k: 0.0 for k in STRATEGIES if k != "mixed"}
    by["other"] = 0.0
    for it in cost_items or []:
        usd = float(it.get("usd") or 0)
        cat = str(it.get("category") or "")
        s = CATEGORY_STRATEGY.get(cat)
        if not s:
            w = (str(it.get("item") or "") + " " + str(it.get("what") or "")).lower()
            s = ("trees" if "tree" in w or "wind" in w else "green" if any(k in w for k in ("permeable", "bioswale", "green_roof"))
                 else "building" if "floodproof" in w else
                 "response" if any(k in w for k in ("shelter", "evac", "meal", "water", "crew", "truck", "alert", "medical", "generator"))
                 else "grey" if any(k in w for k in ("wall", "pump", "tunnel", "storage", "inlet", "sandbag", "berm", "earth", "barrier"))
                 else "other")
        by[s] = by.get(s, 0.0) + usd
    total = sum(by.values())
    if total <= 0:
        return "mixed", by
    top = max((k for k in by if k != "other"), key=lambda k: by[k])
    return (top if by[top] / total > 0.5 else "mixed"), {k: round(v) for k, v in by.items()}


def cost_band(cost: float, budget: float) -> int:
    f = cost / budget if budget else 0
    return 0 if f < 0.2 else 1 if f < 0.5 else 2 if f < 0.8 else 3


def band_edges(budget: float) -> list[list[float]]:
    return [[0, 0.2 * budget], [0.2 * budget, 0.5 * budget], [0.5 * budget, 0.8 * budget], [0.8 * budget, budget]]


# ----------------------------------------------------------------------------- evaluation (worker processes)
def estimate_cost(code: str, timeout: float = 120) -> dict:
    """Sandboxed dry run (no network, no secrets): the locked cost planner prices the plan without simulating."""
    from server.sandbox import run_query
    # aggregate inside the sandbox: query output is truncated at 8k chars
    q = ("import json\nfrom sim.costs import estimate\n_r = estimate(" + repr(code) + ")\n"
         "_it = _r.get('items') or []\n_cat = {}\n"
         "for _x in _it:\n    _k = _x.get('category') or 'other'\n    _cat[_k] = _cat.get(_k, 0) + float(_x.get('usd') or 0)\n"
         "_top = sorted(_it, key=lambda x: -float(x.get('usd') or 0))[:12]\n"
         "result = json.dumps({'total_usd': _r.get('total_usd'), 'error': (str(_r['error'])[-1500:] if _r.get('error') else None), "
         "'by_category': _cat, 'items': [{k: x.get(k) for k in ('what', 'usd', 'category')} for x in _top]})")
    r = run_query(q, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "error": (r.get("error") or "sandbox failed")[-1500:]}
    raw = r.get("result")
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        try:
            d = ast.literal_eval(raw)
        except Exception:
            return {"ok": False, "error": f"could not parse estimate: {str(raw)[:300]}"}
    if d.get("error"):
        return {"ok": False, "error": str(d["error"])[-1500:], "stdout": r.get("stdout")}
    return {"ok": True, "total_usd": float(d.get("total_usd") or 0), "items": d.get("items") or [],
            "by_category": d.get("by_category") or {}, "stdout": r.get("stdout")}


def simulate(storm: str, code: str, label: str, budget: float) -> dict:
    """Sandbox apply -> storm sim -> summary vs. the cached no-plan baseline. No video (rendered later for elites)."""
    import traceback
    from server.sandbox import run_plan
    t0 = time.time()
    plan_dir = runner.PLANS / f"evo-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
    sb = run_plan(code, out_dir=plan_dir, timeout=180)
    if not sb.get("ok"):
        return {"ok": False, "stage": "plan", "error": (sb.get("error") or "plan failed")[-2000:], "stdout": sb.get("stdout")}
    base_dir, base = runner.baseline(storm)
    try:
        import inspect
        fn, _ = runner.get_run_storm()
        params = inspect.signature(fn).parameters
        if "render" in params and "storm_name" in params:  # real sim: skip the video, size threads to the pool
            kw = {"render": False}
            if "threads" in params:
                kw["threads"] = int(os.environ.get("EVOLVE_SIM_THREADS", "16"))
            rd = runner._resolve_run_dir(fn(storm_name=storm, plan_code=code, label=label, plan_dir=str(plan_dir), **kw))
        else:
            rd = runner.call_run_storm(storm, code, label, str(plan_dir))
    except Exception as e:
        return {"ok": False, "stage": "sim", "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-2000:]}
    res = runner.load_result(rd)
    res.setdefault("run_id", rd.name)
    summ = runner.summarize(res, base, budget, top_n=10)
    return {"ok": True, "run_id": rd.name, "run_dir": str(rd), "summary": summ, "eval_s": round(time.time() - t0, 1),
            "mock": runner.get_run_storm()[1], "baseline_run_id": base_dir.name}


def render_video(run_id: str) -> str | None:
    v = runner.ensure_video(runner.RUNS / run_id)
    return str(v) if v else None


def _worker_init():
    os.environ.setdefault("NUMBA_NUM_THREADS", "16")


# ----------------------------------------------------------------------------- the evolver
class Evolver:
    def __init__(self, job: Job, storm: str = "elnino_ar_2026", budget: float = 50e6, generations: int = 4,
                 population: int = 8, mutator_model: str = MUTATOR, architect_model: str = ARCHITECT,
                 sim_workers: int = 4, llm_workers: int = 8, max_minutes: float = 40, use_llm: bool = True,
                 import_seeds: bool = True, thinking_architect: str = "high", thinking_mutator: str = "medium",
                 seed: int = 0, client=None):
        self.job, self.storm, self.budget = job, storm, float(budget)
        self.generations, self.population = int(generations), max(3, int(population))
        self.mutator_model, self.architect_model = mutator_model, architect_model
        self.sim_workers, self.llm_workers = max(1, int(sim_workers)), max(1, int(llm_workers))
        self.max_minutes, self.use_llm, self.import_seeds = float(max_minutes), bool(use_llm), bool(import_seeds)
        self.thinking_architect, self.thinking_mutator = thinking_architect, thinking_mutator
        self.rng = random.Random(f"{storm}-{seed}")
        self.client = client
        if self.use_llm and client is None:
            from google import genai
            self.client = genai.Client()
        self.dir = OUT / job.id
        (self.dir / "plans").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cands: dict[str, dict] = {}
        self.codes: dict[str, str] = {}
        self.summaries: dict[str, dict] = {}
        self.by_hash: dict[str, str] = {}
        self.cells: dict[str, str] = {}
        self.t0 = time.time()
        self._last_write = 0.0
        self.llm_sem = threading.Semaphore(self.llm_workers)
        self.sandbox_sem = threading.Semaphore(8)
        self.sim_slots = threading.BoundedSemaphore(self.sim_workers)
        self.pool = cf.ProcessPoolExecutor(max_workers=self.sim_workers, mp_context=mp.get_context("spawn"),
                                           initializer=_worker_init)
        self.state = {
            "id": job.id, "status": "running", "started": self.t0, "updated": self.t0,
            "config": {"storm": storm, "budget_usd": self.budget, "generations": self.generations,
                       "population": self.population, "mutator_model": mutator_model if use_llm else None,
                       "architect_model": architect_model if use_llm else None, "sim_workers": self.sim_workers,
                       "max_minutes": self.max_minutes, "mock_sim": runner.get_run_storm()[1], "use_llm": self.use_llm},
            "baseline": None, "generation": 0, "stage": "seeding", "candidates": [], "generations": [],
            "archive": {"strategies": STRATEGIES, "strategy_labels": STRATEGY_LABELS,
                        "cost_bands": band_edges(self.budget), "cells": {}},
            "best_id": None, "usage": {}, "log": [],
            "agents": roster(architect_model, mutator_model, use_llm), "activity": [], "funnel": {},
        }
        self.n = 0
        self.api_doc = None
        self.situation = None

    # ---- state + events
    def emit(self, type, **kw):
        return self.job.emit(type, **kw)

    def log(self, kind: str, text: str):
        with self.lock:
            self.state["log"] = (self.state["log"] + [{"t": round(time.time(), 2), "kind": kind, "text": text[:600]}])[-200:]
        self.emit("evolve_log", kind=kind, text=text[:600])
        self.save()

    def save(self, force: bool = False):
        with self.lock:
            now = time.time()
            if not force and now - self._last_write < 0.4:
                return
            self._last_write = now
            self.state["updated"] = now
            self.state["candidates"] = list(self.cands.values())
            self.state["archive"]["cells"] = dict(self.cells)
            self.state["funnel"] = self.funnel()
            tmp = self.dir / f".state.{threading.get_ident()}.tmp"
            tmp.write_text(json.dumps(self.state, default=str))
            os.replace(tmp, self.dir / "state.json")

    def span_start(self, agent: str, kind: str, cand: str | None = None, model: str | None = None) -> int:
        with self.lock:
            sid = len(self.state["activity"])
            self.state["activity"].append({"id": sid, "agent": agent, "model": model, "kind": kind, "cand": cand,
                                           "gen": self.state["generation"], "t0": round(time.time(), 2), "t1": None, "ok": None})
        self.save(True)
        return sid

    def span_end(self, sid: int, ok: bool | None = True):
        with self.lock:
            a = self.state["activity"][sid]
            a["t1"], a["ok"] = round(time.time(), 2), ok
        self.save()

    def funnel(self) -> dict:
        cs = list(self.cands.values())
        sim_kinds = {a["cand"] for a in self.state["activity"] if a["kind"] == "sim"}
        return {"proposed": len(cs),
                "compiled": sum(1 for c in cs if c.get("plan_hash") and c["status"] != "duplicate"),
                "cost_ok": sum(1 for c in cs if c["id"] in sim_kinds),
                "repaired": sum(1 for c in cs if c.get("repaired")),
                "simulated": sum(1 for c in cs if c["status"] in ("scored", "over_budget") and c.get("run_id")),
                "within_budget": sum(1 for c in cs if c["status"] == "scored"),
                "elites": len(set(self.cells.values())),
                "improved": sum(1 for c in cs if c.get("improved"))}

    def set_stage(self, stage: str):
        with self.lock:
            self.state["stage"] = stage
        self.emit("evolve_stage", stage=stage, generation=self.state["generation"])
        self.save(True)

    def update(self, cid: str, **kw):
        with self.lock:
            self.cands[cid].update(kw)
            c = dict(self.cands[cid])
        self.emit("candidate", candidate=c)
        self.save()

    def new_cand(self, gen: int, op: str, model: str | None, parents: list[str], directive: str) -> str:
        with self.lock:
            self.n += 1
            cid = f"c{self.n:03d}"
            self.cands[cid] = {"id": cid, "gen": gen, "op": op, "model": model, "parents": parents, "directive": directive,
                               "note": "", "status": "writing", "plan_hash": None, "lines": None, "code_url": None,
                               "score": None, "by_cause": None, "cost_by_strategy": None, "strategy": None,
                               "cost_band": None, "run_id": None, "video_url": None, "error": None,
                               "t_created": round(time.time(), 2), "t_done": None, "eval_s": None, "llm_s": None,
                               "agent": None, "agent_label": None, "selection": None, "review": None,
                               "repaired": False, "improved": None}
        self.update(cid)
        return cid

    def usage(self, model: str, it):
        u = getattr(it, "usage", None)
        with self.lock:
            d = self.state["usage"].setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            d["calls"] += 1
            for k_out, k in (("input_tokens", "total_input_tokens"), ("output_tokens", "total_output_tokens")):
                v = getattr(u, k, None) if u is not None else None
                if isinstance(v, int):
                    d[k_out] += v

    def elapsed_min(self) -> float:
        return (time.time() - self.t0) / 60

    # ---- context for the models
    def system_prompt(self) -> str:
        if self.api_doc is None:
            from agent.optimizer import api_docs
            self.api_doc = api_docs()
        return f"""You are part of an evolutionary search for the best storm plan for San Francisco (AlphaEvolve-style).
An El Nino atmospheric-river storm (`{self.storm}`) is about to hit the city tile (Mission, SoMa, Mission Bay,
Downtown; LOCAL metres from the grid SW corner, x east 0..3548, y north 0..4256, 4 m cells).
Budget: ${self.budget:,.0f}. A plan is Python `def apply(city): ...` using the city API below.

Fitness = damage avoided (incl. life-safety value) - plan cost, measured by a locked, deterministic storm simulation
(shallow-water flooding, sewer capacity, lattice-Boltzmann wind, tree failure, people and emergency response, Hazus
depth-damage) and a locked cost planner with cited unit costs. You never set prices. Plans over budget are discarded.
Spend only where it pays back: a measure that costs more than the damage it avoids lowers fitness.

# Situation (no-plan storm run)
{json.dumps(self.situation, default=str)[:24000]}

# City API
{self.api_doc}
"""

    def cand_brief(self, cid: str, code: bool = False, code_chars: int = 9000) -> str:
        c = self.cands[cid]
        s = c.get("score") or {}
        sm = self.summaries.get(cid) or {}
        lines = [f"## {cid} (gen {c['gen']}, {c['op']}{' by ' + c['model'] if c.get('model') else ''}, parents {c['parents'] or '-'})",
                 f"what it did: {c.get('note') or c.get('directive') or '-'}",
                 f"cost {money(s.get('plan_cost_usd'))}, damage avoided {money(s.get('damage_avoided_usd'))}, "
                 f"NET {money(s.get('net_benefit_usd'))}, BCR {s.get('bcr')}, strategy {c.get('strategy')}"]
        if sm.get("by_cause"):
            lines.append("damage by cause (now vs no-plan): " + ", ".join(
                f"{k} {money(v.get('now'))} (was {money(v.get('baseline'))})" for k, v in list(sm["by_cause"].items())[:8]))
        if sm.get("top_remaining_damage"):
            lines.append("top remaining damage: " + "; ".join(
                f"bldg {b['building_id']} {b.get('occupancy')} at ({b['x']:.0f},{b['y']:.0f}) {money(b['usd'])}"
                for b in sm["top_remaining_damage"][:6]))
        if sm.get("cost_items"):
            lines.append("biggest cost items: " + "; ".join(f"{it.get('what')} {money(it.get('usd'))}" for it in sm["cost_items"][:8]))
        if code:
            src = self.codes.get(cid, "")
            lines.append("```python\n" + (src if len(src) <= code_chars else src[:code_chars] + "\n# ... (truncated)") + "\n```")
        return "\n".join(lines)

    # ---- model calls
    def call(self, model: str, prompt: str, thinking: str, max_output_tokens: int | None = None,
             agent: str | None = None, kind: str = "call", cand: str | None = None, system: str | None = None) -> str:
        sid = self.span_start(agent or model, kind, cand, model)
        try:
            out = self._call(model, prompt, thinking, max_output_tokens, system)
        except Exception:
            self.span_end(sid, False)
            raise
        self.span_end(sid, True)
        return out

    def _call(self, model: str, prompt: str, thinking: str, max_output_tokens: int | None = None,
              system: str | None = None) -> str:
        gen = {"thinking_level": thinking}
        if max_output_tokens:
            gen["max_output_tokens"] = max_output_tokens
        last = None
        for attempt in range(3):
            try:
                with self.llm_sem:
                    it = self.client.interactions.create(model=model, input=prompt, system_instruction=system or self.system_prompt(),
                                                         generation_config=gen, timeout=1200)
                self.usage(model, it)
                return it.output_text or ""
            except Exception as e:
                last = e
                self.log("api_error", f"{model}: {type(e).__name__}: {str(e)[:200]}")
                time.sleep(4 * (attempt + 1))
        raise RuntimeError(f"{model} failed: {last}")

    # ---- archive
    def scored(self, within_budget=True) -> list[dict]:
        out = [c for c in self.cands.values() if c["status"] == "scored" and c.get("score")]
        if within_budget:
            out = [c for c in out if c["score"].get("within_budget")]
        return sorted(out, key=lambda c: -c["score"]["net_benefit_usd"])

    def best(self) -> dict | None:
        s = self.scored()
        return s[0] if s else None

    def insert_archive(self, cid: str):
        c = self.cands[cid]
        if not (c.get("score") and c["score"].get("within_budget")):
            return False
        key = f"{c['strategy']}|{c['cost_band']}"
        cur = self.cells.get(key)
        if cur is None or self.cands[cur]["score"]["net_benefit_usd"] < c["score"]["net_benefit_usd"]:
            self.cells[key] = cid
            return True
        return False

    def sample_parent(self, exclude: set | None = None) -> str | None:
        """Tournament over archive cells (keeps diversity), biased to the global best half the time."""
        elites = [cid for cid in set(self.cells.values()) if cid not in (exclude or set())]
        if not elites:
            pool = [c["id"] for c in self.scored()] or [c["id"] for c in self.scored(False)]
            return self.rng.choice(pool) if pool else None
        if self.rng.random() < 0.4:
            return max(elites, key=lambda e: self.cands[e]["score"]["net_benefit_usd"])
        a, b = self.rng.choice(elites), self.rng.choice(elites)
        return max((a, b), key=lambda e: self.cands[e]["score"]["net_benefit_usd"])

    def inspirations(self, parent: str, k: int = 2) -> list[str]:
        pool = [c["id"] for c in self.scored() if c["id"] != parent]
        top = pool[:4]
        other = [e for e in set(self.cells.values()) if e != parent and e not in top]
        picks = top[:1] + self.rng.sample(other, min(len(other), k - 1)) if other else top[:k]
        return [p for p in picks if p][:k]

    # ---- candidate pipeline
    def evaluate(self, cid: str, code: str, author=None) -> None:
        """Cascade: compile -> sandboxed cost estimate (one repair round for LLM authors) -> storm sim -> archive."""
        repaired = False
        while True:
            err = check_code(code)
            est, h = None, None
            if not err:
                h = code_hash(code)
                with self.lock:
                    dup = self.by_hash.get(h)
                    if dup is None:
                        self.by_hash[h] = cid
                if dup:
                    self.save_code(cid, code)
                    self.update(cid, status="duplicate", error=f"identical to {dup}", t_done=round(time.time(), 2))
                    return
                self.save_code(cid, code)
                self.update(cid, status="checking")
                with self.sandbox_sem:
                    sid = self.span_start("sandbox", "check", cid)
                    est = estimate_cost(code)
                    self.span_end(sid, bool(est.get("ok")) and est.get("total_usd", 0) <= self.budget)
                if not est.get("ok"):
                    err = "plan raised an error in the sandbox: " + est.get("error", "")
                elif est["total_usd"] > self.budget:
                    top = sorted(est["items"], key=lambda it: -float(it.get("usd") or 0))[:10]
                    err = (f"plan costs {money(est['total_usd'])} > budget {money(self.budget)}. Biggest items: "
                           + "; ".join(f"{it.get('what')} {money(it.get('usd'))}" for it in top))
                with self.lock:
                    if err and h and self.by_hash.get(h) == cid:
                        del self.by_hash[h]
            if not err:
                break
            if author is None or repaired:
                status = "over_budget" if "> budget" in err else "failed"
                self.update(cid, status=status, error=err[:1500], t_done=round(time.time(), 2),
                            score={"plan_cost_usd": round(est["total_usd"]), "within_budget": False} if est and est.get("ok") else None)
                self.log("reject", f"{cid}: {' '.join(err.split())[:220]}")
                return
            repaired = True
            self.update(cid, status="writing", error=None, repaired=True)
            self.log("repair", f"{cid}: sent back to its author: {' '.join(err.split())[:180]}")
            try:
                code = author(err, code) or code
            except Exception as e:
                self.update(cid, status="failed", error=f"repair failed: {e}", t_done=round(time.time(), 2))
                return
        # full storm simulation in a worker process
        self.update(cid, status="simulating", lines=code.count("\n") + 1)
        with self.sim_slots:
            sid = self.span_start("sim", "sim", cid)
            try:
                fut = self.pool.submit(simulate, self.storm, code, f"evolve {self.job.id} {cid}", self.budget)
                r = fut.result(timeout=60 * 30)
            except Exception as e:
                r = {"ok": False, "stage": "sim", "error": f"{type(e).__name__}: {e}"}
            self.span_end(sid, bool(r.get("ok")))
        if not r.get("ok"):
            self.update(cid, status="failed", error=(r.get("error") or "")[:1500], t_done=round(time.time(), 2))
            self.log("sim_error", f"{cid}: {r.get('stage')}: {(r.get('error') or '')[:200]}")
            return
        s = r["summary"]
        if r.get("mock") and est and est.get("ok"):
            # the mock sim prices plans with placeholder unit costs; use the locked cost planner's figure instead
            cost = round(est["total_usd"])
            s.update(plan_cost_usd=cost, net_benefit_usd=round((s.get("damage_avoided_usd") or 0) - cost),
                     bcr=round((s.get("damage_avoided_usd") or 0) / cost, 3) if cost > 0 else None,
                     within_budget=cost <= self.budget)
        ppl = s.get("people") or {}
        strat, by_strat = strategy_of([{"category": k, "usd": v} for k, v in ((est or {}).get("by_category") or {}).items()]
                                       or s.get("cost_items"))
        score = {"plan_cost_usd": s.get("plan_cost_usd"), "damage_usd": s.get("damage_total_usd"),
                 "damage_avoided_usd": s.get("damage_avoided_usd"), "net_benefit_usd": s.get("net_benefit_usd"),
                 "bcr": s.get("bcr"), "within_budget": bool(s.get("within_budget", True)),
                 "at_risk_person_hours": (ppl.get("at_risk_person_hours") or {}).get("now"),
                 "expected_fatalities": (ppl.get("expected_fatalities") or {}).get("now")}
        with self.lock:
            self.summaries[cid] = s
        status = "scored" if score["within_budget"] else "over_budget"
        par = self.cands.get((self.cands[cid].get("parents") or [None])[0] or "")
        pnet = ((par or {}).get("score") or {}).get("net_benefit_usd")
        improved = (score["net_benefit_usd"] or 0) > pnet + 1 if pnet is not None and status == "scored" else None
        self.update(cid, status=status, score=score, improved=improved, run_id=r["run_id"], eval_s=r.get("eval_s"),
                    by_cause={k: v.get("now") for k, v in (s.get("by_cause") or {}).items()},
                    cost_by_strategy=by_strat, strategy=strat, cost_band=cost_band(score["plan_cost_usd"] or 0, self.budget),
                    t_done=round(time.time(), 2))
        with self.lock:
            new_cell = self.insert_archive(cid)
            prev = self.state.get("best_id")
            b = self.best()
            if b:
                self.state["best_id"] = b["id"]
        if b and b["id"] == cid and prev != cid:
            self.log("best", f"new best {cid} ({self.cands[cid]['op']}): net {money(score['net_benefit_usd'])}, "
                             f"cost {money(score['plan_cost_usd'])}, BCR {score['bcr']}")
        elif new_cell:
            self.log("elite", f"{cid} is the new elite for {STRATEGY_LABELS.get(strat, strat)}, band {self.cands[cid]['cost_band']}")
        self.save(True)

    def save_code(self, cid: str, code: str):
        p = self.dir / "plans" / f"{cid}.py"
        p.write_text(code)
        with self.lock:
            self.codes[cid] = code
        self.update(cid, plan_hash=code_hash(code), lines=code.count("\n") + 1, code_url=runner_rel(p))

    # ---- operators
    def set_agent(self, cid: str, agent: str, selection: str):
        label = "Architect" if agent == "architect" else SPECIALISTS.get(agent, (agent,))[0]
        self.update(cid, agent=agent, agent_label=label, selection=selection)

    @staticmethod
    def persona(agent: str) -> str:
        return SPECIALISTS[agent][3] + "\n\n" if agent in SPECIALISTS else ""

    def op_seed_llm(self, gen: int, strat: str, hint: str):
        cid = self.new_cand(gen, "seed", self.mutator_model, [], f"Seed ({STRATEGY_LABELS[strat]}): {hint}")
        agent = SEED_AGENT.get(strat, "cost_engineer")
        self.set_agent(cid, agent, "seed")
        prompt = (self.persona(agent) + f"Write a complete first plan. Strategy for this seed: {hint}\n"
                  f"Stay well inside the ${self.budget:,.0f} budget (estimate quantities from the unit costs). Target the "
                  "places in the situation where damage concentrates. Start your answer with one line `NOTE: <what the "
                  "plan does>` and then the full plan in one ```python block defining apply(city).")
        self.run_llm_candidate(cid, self.mutator_model, prompt, self.thinking_mutator, parent_code=None, agent=agent, kind="seed")

    def op_mutate(self, gen: int, parent: str, directive: str, agent: str = "cost_engineer", selection: str = "archive tournament"):
        insp = self.inspirations(parent)
        cid = self.new_cand(gen, "mutate", self.mutator_model, [parent], directive)
        self.set_agent(cid, agent, selection)
        budget_left = self.budget - (self.cands[parent]["score"].get("plan_cost_usd") or 0)
        prompt = (self.persona(agent) + f"Improve the PARENT plan. Direction from the lead engineer (Gemini 4 Argon): {directive}\n"
                  f"The parent leaves {money(budget_left)} of budget unused. Make one focused, well-reasoned change set "
                  "(add, resize, move or cut measures) that raises net benefit; cut measures that do not pay back.\n\n"
                  f"# PARENT\n{self.cand_brief(parent, code=True, code_chars=30000)}\n\n"
                  "# INSPIRATIONS (other strong plans in the database; borrow ideas, not necessarily code)\n"
                  + "\n\n".join(self.cand_brief(i, code=True, code_chars=6000) for i in insp) +
                  "\n\n# OUTPUT FORMAT\nFirst line: `NOTE: <one line: what you changed and why>`. Then one or more diff "
                  "blocks against the PARENT code, exactly:\n<<<<<<< SEARCH\n<exact lines copied from the parent>\n=======\n"
                  "<replacement lines>\n>>>>>>> REPLACE\nSEARCH text must match the parent exactly (keep indentation). "
                  "To add code, SEARCH an existing line and REPLACE it with that line plus the new lines.")
        self.run_llm_candidate(cid, self.mutator_model, prompt, self.thinking_mutator, parent_code=self.codes[parent],
                               agent=agent, kind="mutate", review_parent=parent)

    def op_crossover(self, gen: int, a: str, b: str, idea: str):
        cid = self.new_cand(gen, "crossover", self.architect_model, [a, b], idea)
        self.set_agent(cid, "architect", "architect pick")
        prompt = (f"Write a new complete plan that combines the strongest parts of these two elites. Idea: {idea}\n"
                  f"Keep it within ${self.budget:,.0f}; drop the measures whose cost exceeds the damage they avoid.\n\n"
                  f"# PARENT A\n{self.cand_brief(a, code=True, code_chars=30000)}\n\n# PARENT B\n"
                  f"{self.cand_brief(b, code=True, code_chars=30000)}\n\nStart with `NOTE: <one line>` then the full plan "
                  "in one ```python block defining apply(city).")
        self.run_llm_candidate(cid, self.architect_model, prompt, "medium", parent_code=None, agent="architect",
                               kind="crossover", review_parent=a)

    @staticmethod
    def tweak_sites(code: str) -> list[tuple[int, int, str]]:
        """(start, end, key) of sizing literals: `height_m=1.2`-style kwargs and the positional size after a
        coordinate list, e.g. `city.wall([...], 1.2)`."""
        sites = [(m.start(3), m.end(3), m.group(1)) for m in TWEAK_KW.finditer(code)]
        sites += [(m.start(1), m.end(1), "size") for m in TWEAK_POS.finditer(code)]
        return sorted(set(sites))

    def tweak_parent(self, exclude: set) -> str | None:
        for _ in range(12):
            p = self.sample_parent(exclude=exclude)
            if p and self.tweak_sites(self.codes.get(p, "")):
                return p
            if p:
                exclude = exclude | {p}
        return None

    def op_tweak(self, gen: int, parent: str):
        code = self.codes[parent]
        sites = self.tweak_sites(code)
        if not sites:
            return
        cid = self.new_cand(gen, "tweak", None, [parent], "Numeric hill-climb: rescale 1-3 sizing parameters of the parent")
        self.update(cid, agent="tweak", agent_label="Hill-climber", selection="archive tournament")
        sid = self.span_start("tweak", "tweak", cid)
        picks = sorted(self.rng.sample(sites, min(len(sites), self.rng.choice((1, 2, 3)))), key=lambda t: -t[0])
        changes = []
        for a, b, key in picks:
            old = code[a:b]
            line = code[code.rfind("\n", 0, a) + 1: code.find("\n", b)]
            v = float(old) * self.rng.choice(TWEAK_FACTORS)
            if key == "factor":
                v = min(v, 4.0)
            if "floodproof" in line and key in ("height_m", "size"):
                v = min(v, 0.9)
            if "sandbags" in line and key in ("height_m", "size"):
                v = min(v, 1.0)
            new = str(max(1, int(round(v)))) if "." not in old else f"{v:.3g}"
            if new == old:
                continue
            code = code[:a] + new + code[b:]
            fn = re.search(r"city\.(\w+)\(", line)
            changes.append(f"{fn.group(1) + ' ' if fn else ''}{key if key != 'size' else 'size'} {old}->{new} "
                           f"(line {code.count(chr(10), 0, a) + 1})")
        self.update(cid, note="tweak: " + (", ".join(changes) if changes else "no change"))
        self.span_end(sid, bool(changes))
        self.evaluate(cid, code, author=None)

    def run_llm_candidate(self, cid: str, model: str, prompt: str, thinking: str, parent_code: str | None,
                          agent: str | None = None, kind: str = "mutate", review_parent: str | None = None):
        t0 = time.time()
        try:
            text = self.call(model, prompt, thinking, agent=agent, kind=kind, cand=cid)
        except Exception as e:
            self.update(cid, status="failed", error=str(e)[:800], t_done=round(time.time(), 2))
            return
        code = self.code_from(text, parent_code)
        self.update(cid, note=first_line(text) or self.cands[cid]["note"], llm_s=round(time.time() - t0, 1))
        if code is None:
            self.update(cid, status="failed", error="model output had no usable diff or plan code",
                        t_done=round(time.time(), 2))
            return

        if review_parent and review_parent in self.codes:
            self.review(cid, review_parent, code)
        self.evaluate(cid, code, author=self.repairer(cid, model, thinking, agent))

    def review(self, cid: str, parent: str, code: str):
        """Reviewer agent (Flash-Lite): predicts the change's effect before the sim; calibration is scored later."""
        import difflib
        diff = "\n".join(difflib.unified_diff(self.codes[parent].splitlines(), code.splitlines(), "parent", "child", n=1, lineterm=""))
        prompt = (f"A storm-plan change is about to be simulated. Predict its effect on NET benefit (damage avoided - cost).\n\n"
                  f"# PARENT RESULT\n{self.cand_brief(parent)}\n\n# CHANGE NOTE\n{self.cands[cid].get('note')}\n\n"
                  f"# DIFF\n{diff[:8000]}\n\nReturn ONLY JSON: {{\"verdict\": \"run\"|\"risky\", \"predicted\": "
                  "\"better\"|\"worse\"|\"same\", \"reason\": \"<one sentence>\"}}")
        try:
            t = self.call(REVIEWER, prompt, "low", agent="reviewer", kind="review", cand=cid,
                          system="You review city storm-plan code changes for a flood-damage simulation. Be terse and concrete.")
            d = parse_json(t) or {}
            if d.get("predicted") in ("better", "worse", "same"):
                self.update(cid, review={"verdict": d.get("verdict") or "run", "predicted": d["predicted"],
                                         "reason": str(d.get("reason") or "")[:300], "model": REVIEWER})
        except Exception as e:
            self.log("api_error", f"reviewer failed on {cid}: {str(e)[:160]}")

    def repairer(self, cid: str, model: str, thinking: str, agent: str | None = None):
        """Feedback loop: a plan rejected before simulation goes back to a model once with the error."""
        def author(err: str, bad_code: str) -> str | None:
            fix = (f"Your plan was rejected before simulation:\n{err[:3000]}\n\nReturn the corrected FULL plan "
                   "(start with `NOTE: <one line>`, then one ```python block). Keep what works; fix or cut what broke.\n\n"
                   f"```python\n{bad_code}\n```")
            t = self.call(model, fix, thinking, agent=agent or "repair", kind="repair", cand=cid)
            note = first_line(t)
            if note:
                self.update(cid, note=note + " (repaired)", model=self.cands[cid].get("model") or model)
            return extract_code(t)
        return author

    @staticmethod
    def code_from(text: str, parent_code: str | None) -> str | None:
        if parent_code is not None and DIFF_RE.search(text or ""):
            child, n, _ = apply_diff(parent_code, text)
            if child:
                return child
        return extract_code(text)

    # ---- architect
    def architect(self, gen: int, n_dirs: int) -> dict:
        rows = []
        for c in sorted(self.cands.values(), key=lambda c: c["id"]):
            s = c.get("score") or {}
            rows.append(f"{c['id']} g{c['gen']} {c['op']:<9} parents={','.join(c['parents']) or '-':<9} {c['status']:<11} "
                        f"cost={money(s.get('plan_cost_usd'))} avoided={money(s.get('damage_avoided_usd'))} "
                        f"net={money(s.get('net_benefit_usd'))} strat={c.get('strategy')} | {(c.get('note') or c.get('error') or '')[:160]}")
        elites = sorted(set(self.cells.values()), key=lambda e: -self.cands[e]["score"]["net_benefit_usd"])
        best = elites[0] if elites else None
        prev = self.state["generations"][-1] if self.state["generations"] else None
        prompt = (f"You are the lead engineer steering generation {gen} of {self.generations} of the evolutionary search.\n"
                  "Read the whole program database below, find what the evidence says (which measures pay back, which "
                  "waste money, where damage remains, which lineages are stuck), then direct the next generation.\n\n"
                  "# PROGRAM DATABASE (every plan so far)\n" + "\n".join(rows) +
                  "\n\n# ARCHIVE ELITES (best per strategy x cost band)\n" + "\n\n".join(self.cand_brief(e) for e in elites[:8]) +
                  (f"\n\n# CURRENT BEST, full code\n{self.cand_brief(best, code=True, code_chars=40000)}" if best else "") +
                  (f"\n\n# YOUR DIRECTIONS LAST GENERATION\n" + json.dumps(prev.get("directions_full") or prev.get("directions"))
                   if prev else "") +
                  f"\n\nReturn ONLY a JSON object:\n{{\"insights\": [3-5 short evidence-based findings],\n"
                  f" \"directions\": [exactly {n_dirs} objects {{\"parent\": \"<candidate id from the archive>\", "
                  "\"agent\": \"<one of: " + ", ".join(f"{k} ({v[2]})" for k, v in SPECIALISTS.items()) + ">\", "
                  "\"directive\": \"<one concrete instruction: what to add/resize/move/cut, where (coordinates/streets/"
                  "building ids) and why>\"}; spread them over different parents and ideas],\n"
                  " \"crossover\": {\"parents\": [\"<id>\", \"<id>\"], \"idea\": \"<what to combine>\"}}")
        text = self.call(self.architect_model, prompt, self.thinking_architect, agent="architect", kind="architect")
        d = parse_json(text) or {}
        return d

    # ---- generations
    def seed_generation(self):
        self.set_stage("seeding")
        jobs = []
        if self.import_seeds:
            for p in sorted((REPO / "runs" / "_jobs").glob("*.plan.py"), key=lambda p: -p.stat().st_mtime)[:2]:
                code = p.read_text()
                if check_code(code) is None:
                    jobs.append(("import", p.name.replace(".plan.py", ""), code))
        if self.state["baseline"] and not self.use_llm:
            from agent.benchmark import walls_plan
            try:
                jobs.append(("import", "walls-everywhere baseline", walls_plan(self.base_dir)))
            except Exception:
                pass
        futs = []
        with cf.ThreadPoolExecutor(max_workers=self.population + 2) as ex:
            for _, src, code in jobs:
                cid = self.new_cand(0, "seed", None, [], f"Imported: {src}")
                self.update(cid, note=f"imported from {src}", agent="import", agent_label="Imported", selection="warm start")
                futs.append(ex.submit(self.evaluate, cid, code,
                                      self.repairer(cid, self.mutator_model, self.thinking_mutator, "cost_engineer") if self.use_llm else None))
            if self.use_llm:
                hints = SEED_HINTS[: max(0, self.population - len(jobs))]
                for strat, hint in hints:
                    futs.append(ex.submit(self.op_seed_llm, 0, strat, hint))
            for f in futs:
                f.result()
        self.close_generation(0, [], [], None)

    def close_generation(self, gen: int, directions: list, insights: list, t_start: float | None, directions_full=None):
        gc = [c for c in self.cands.values() if c["gen"] == gen]
        b = self.best()
        rec = {"gen": gen, "directions": directions, "directions_full": directions_full, "insights": insights,
               "architect_model": self.architect_model if (gen and self.use_llm) else None,
               "best_id": b["id"] if b else None, "best_net_usd": b["score"]["net_benefit_usd"] if b else None,
               "n_scored": sum(c["status"] == "scored" for c in gc), "n_failed": sum(c["status"] == "failed" for c in gc),
               "n_over_budget": sum(c["status"] == "over_budget" for c in gc),
               "t_start": t_start or self.t0, "t_end": round(time.time(), 2)}
        with self.lock:
            self.state["generations"] = [g for g in self.state["generations"] if g["gen"] != gen] + [rec]
        self.emit("generation", **rec)
        self.log("generation", f"generation {gen} done: best {money(rec['best_net_usd'])} net "
                               f"({rec['n_scored']} scored, {rec['n_over_budget']} over budget, {rec['n_failed']} failed)")

    def generation(self, gen: int):
        t_start = round(time.time(), 2)
        with self.lock:
            self.state["generation"] = gen
        n_tweak = 2 if self.population >= 6 else 1
        n_cross = 1 if self.use_llm else 0
        n_mut = (self.population - n_tweak - n_cross) if self.use_llm else 0
        if not self.use_llm:
            n_tweak = self.population
        directions, insights, cross = [], [], None
        if self.use_llm:
            self.set_stage("architect")
            self.log("architect", f"{self.architect_model} is reading {len(self.cands)} plans to direct generation {gen}")
            try:
                d = self.architect(gen, n_mut)
                insights = [str(x) for x in (d.get("insights") or [])][:6]
                for x in d.get("directions") or []:
                    if isinstance(x, dict) and x.get("directive"):
                        directions.append({"parent": str(x.get("parent") or ""), "directive": str(x["directive"]),
                                           "agent": str(x.get("agent") or "").split(" ")[0].strip()})
                    elif isinstance(x, str):
                        directions.append({"parent": "", "directive": x, "agent": ""})
                cross = d.get("crossover") if isinstance(d.get("crossover"), dict) else None
                for ins in insights:
                    self.log("insight", ins)
            except Exception as e:
                self.log("api_error", f"architect failed, using default directions: {e}")
            with self.lock:
                self.state["generations"].append({"gen": gen, "directions": [d["directive"] for d in directions],
                                                  "insights": insights, "architect_model": self.architect_model,
                                                  "best_id": None, "best_net_usd": None, "n_scored": 0, "n_failed": 0,
                                                  "n_over_budget": 0, "t_start": t_start, "t_end": None})
        self.set_stage("mutating")
        default_dirs = ["Find the largest remaining damage location and add the cheapest measure that removes it.",
                        "Cut the measure with the worst cost per dollar of damage avoided and reinvest in the best one.",
                        "Use the unused budget on the second-worst flood basin.",
                        "Strengthen emergency response for the people still at risk (shelters, evacuation, crews).",
                        "Resize the grey infrastructure to the basin volumes (storage m3, pump m3/s) instead of round numbers."]
        with cf.ThreadPoolExecutor(max_workers=self.population + 1) as ex:
            futs = []
            for i in range(n_mut):
                dd = directions[i] if i < len(directions) else {"parent": "", "directive": default_dirs[i % len(default_dirs)]}
                picked = dd["parent"] in self.cands and self.cands[dd["parent"]].get("status") == "scored" and dd["parent"] in self.codes
                parent = dd["parent"] if picked else self.sample_parent()
                agent = dd.get("agent") if dd.get("agent") in SPECIALISTS else list(SPECIALISTS)[i % len(SPECIALISTS)]
                if parent:
                    futs.append(ex.submit(self.op_mutate, gen, parent, dd["directive"], agent,
                                          "architect pick" if picked else "archive tournament"))
            if n_cross:
                elites = sorted(set(self.cells.values()), key=lambda e: -self.cands[e]["score"]["net_benefit_usd"])
                ps = [p for p in ((cross or {}).get("parents") or []) if p in self.codes and self.cands[p].get("status") == "scored"]
                if len(ps) < 2:
                    ps = elites[:2]
                if len(ps) < 2:
                    ps = [c["id"] for c in self.scored()][:2]
                if len(ps) >= 2:
                    idea = (cross or {}).get("idea") or "Combine the best measures of both plans; keep the cheaper of overlapping ones."
                    futs.append(ex.submit(self.op_crossover, gen, ps[0], ps[1], idea))
            used: set = set()
            for i in range(n_tweak):
                p = self.tweak_parent(used if len(used) < len(self.cells) else set())
                if p:
                    used.add(p)
                    futs.append(ex.submit(self.op_tweak, gen, p))
            for f in futs:
                try:
                    f.result()
                except Exception as e:
                    self.log("error", f"operator failed: {type(e).__name__}: {e}")
        self.set_stage("selecting")
        self.close_generation(gen, [d["directive"] for d in directions], insights, t_start, directions_full=directions)

    def finalize(self) -> dict:
        self.set_stage("done")
        b = self.best()
        final = {"job_id": self.job.id, "storm": self.storm, "budget_usd": self.budget, "n_candidates": len(self.cands),
                 "n_scored": len(self.scored(False)), "wall_clock_s": round(time.time() - self.t0, 1),
                 "usage": self.state["usage"], "state_url": runner_rel(self.dir / "state.json")}
        if b:
            # replay videos for the best and the archive elites (top 4) so the UI can play them
            self.log("video", "rendering replay videos for the best plan and the top archive elites")
            top = [b["id"]] + [e for e in sorted(set(self.cells.values()), key=lambda e: -self.cands[e]["score"]["net_benefit_usd"])
                               if e != b["id"]][:3]
            futs = {cid: self.pool.submit(render_video, self.cands[cid]["run_id"]) for cid in top if self.cands[cid].get("run_id")}
            for cid, f in futs.items():
                try:
                    v = f.result(timeout=600)
                except Exception as e:
                    v = None
                    self.log("video", f"{cid}: video failed: {e}")
                if v:
                    self.update(cid, video_url=runner_rel(v))
            code = self.codes[b["id"]]
            (self.dir / "best.plan.py").write_text(code)
            (REPO / "runs" / "_jobs").mkdir(parents=True, exist_ok=True)
            (REPO / "runs" / "_jobs" / f"{self.job.id}.plan.py").write_text(code)  # /api/managed {job_id} can verify it
            lineage, cur = [], b["id"]
            while cur:
                lineage.append(cur)
                ps = self.cands[cur]["parents"]
                cur = ps[0] if ps else None
            final.update(best_id=b["id"], best=b["score"], best_run_id=b.get("run_id"), lineage=lineage,
                         best_plan_url=runner_rel(self.dir / "best.plan.py"), video_url=self.cands[b["id"]].get("video_url"))
            seeds = [c for c in self.scored() if c["gen"] == 0]
            if seeds:
                final["best_seed_net_usd"] = seeds[0]["score"]["net_benefit_usd"]
                final["improvement_over_best_seed_usd"] = b["score"]["net_benefit_usd"] - seeds[0]["score"]["net_benefit_usd"]
        with self.lock:
            # people stay in control: the best plan waits for a planner (POST /api/evolve/<id>/approve)
            self.state["status"] = "awaiting_approval" if b else "completed"
            self.state["final"] = final
        self.save(True)
        (self.dir / "final.json").write_text(json.dumps(final, indent=1, default=str))
        self.emit("final", report=final)
        return final

    def run(self) -> dict:
        self.emit("evolve_start", config=self.state["config"], state_url=runner_rel(self.dir / "state.json"))
        self.save(True)
        try:
            self.base_dir, base = runner.baseline(self.storm, log=lambda t, **k: self.emit(t, **k))
            bvid = runner.ensure_video(self.base_dir)
            bs = runner.summarize(base, None)
            with self.lock:
                self.state["baseline"] = {"run_id": self.base_dir.name, "damage_usd": bs.get("damage_total_usd"),
                                          "video_url": runner_rel(bvid) if bvid else None,
                                          "by_cause": {k: v.get("now") for k, v in (bs.get("by_cause") or {}).items()}}
            from agent.tools import city_summary
            try:
                self.situation = city_summary(self.storm, self.base_dir, base, self.budget)
            except Exception as e:
                self.situation = {"no_plan": bs, "note": f"city_summary unavailable: {e}"}
            self.log("start", f"baseline {self.base_dir.name}: no-plan damage {money(bs.get('damage_total_usd'))}"
                              f"{' (MOCK sim)' if self.state['config']['mock_sim'] else ''}")
            self.seed_generation()
            for gen in range(1, self.generations + 1):
                if self.elapsed_min() > self.max_minutes:
                    self.log("stop", f"time cap {self.max_minutes:.0f} min reached before generation {gen}")
                    break
                if not self.scored(False):
                    self.log("stop", "no plan could be scored; stopping")
                    break
                self.generation(gen)
            return self.finalize()
        except Exception as e:
            import traceback
            with self.lock:
                self.state["status"] = "failed"
                self.state["error"] = f"{type(e).__name__}: {e}"
            self.log("error", traceback.format_exc()[-1500:])
            self.save(True)
            raise
        finally:
            self.pool.shutdown(wait=False, cancel_futures=True)


def runner_rel(p) -> str | None:
    from agent.optimizer import rel
    return rel(p)


def evolve_job(job: Job, cfg: dict) -> dict:
    keys = ("storm", "budget", "generations", "population", "mutator_model", "architect_model", "sim_workers",
            "llm_workers", "max_minutes", "use_llm", "import_seeds", "thinking_architect", "thinking_mutator", "seed")
    kw = {k: cfg[k] for k in keys if cfg.get(k) is not None}
    if cfg.get("budget_usd") is not None:
        kw["budget"] = cfg["budget_usd"]
    res = Evolver(job, **kw).run()
    if res.get("best_id"):
        res["_final_status"] = "awaiting_approval"
    return res


def list_runs(limit: int = 50) -> list[dict]:
    out = []
    for p in sorted(OUT.glob("*/state.json"), key=lambda p: -p.stat().st_mtime)[:limit]:
        try:
            s = json.loads(p.read_text())
        except Exception:
            continue
        best = next((c for c in s.get("candidates", []) if c["id"] == s.get("best_id")), None)
        out.append({"id": s["id"], "status": s.get("status"), "started": s.get("started"), "updated": s.get("updated"),
                    "best_net_usd": (best or {}).get("score", {}).get("net_benefit_usd") if best else None,
                    "n_candidates": len(s.get("candidates", [])), "config": s.get("config")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", default="elnino_ar_2026")
    ap.add_argument("--budget", type=float, default=50e6)
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--population", type=int, default=8)
    ap.add_argument("--mutator", default=MUTATOR)
    ap.add_argument("--architect", default=ARCHITECT)
    ap.add_argument("--sim-workers", type=int, default=4)
    ap.add_argument("--max-minutes", type=float, default=40)
    ap.add_argument("--no-llm", action="store_true", help="seeds + numeric tweaks only (no API calls)")
    ap.add_argument("--no-import", action="store_true", help="don't seed from earlier optimizer plans")
    a = ap.parse_args()
    job = new_job("evolve", {"storm": a.storm, "budget_usd": a.budget})
    orig = job.emit

    def emit(type, **d):
        ev = orig(type, **d)
        if type in ("evolve_log", "generation", "final", "evolve_stage"):
            print(f"[{time.strftime('%H:%M:%S')}] {type}: {json.dumps(d, default=str)[:260]}", flush=True)
        return ev
    job.emit = emit
    res = Evolver(job, a.storm, a.budget, a.generations, a.population, a.mutator, a.architect, a.sim_workers,
                  max_minutes=a.max_minutes, use_llm=not a.no_llm, import_seeds=not a.no_import).run()
    print(json.dumps(res, indent=1, default=str))
    print("state:", OUT / job.id / "state.json")


if __name__ == "__main__":
    main()
