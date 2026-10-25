"""StormSim SF run pipeline (owned by the integration engineer).

    from sim.run import run_storm
    run_dir = run_storm("elnino_ar_2026", plan_code=None, label="baseline")   # -> Path(runs/<run_id>/)

Wires the modules in CONTRACT.md:
  storm (sim.storm) -> plan (sim.city_api CityEditor, or the sandbox export in plan_dir)
  -> wind (sim.wind, cached LBM field, screened if the plan has wind screens)
  -> water (sim.water, plan edits) -> trees/debris/glazing + flood cars (sim.trees, soil saturation from water)
  -> people/response (sim.people) -> damage ledger (sim.damage; life safety priced once there)
  -> plan cost (sim.costs, response lines measured by sim.people) + FEMA BCA vs the cached no-plan baseline
  -> runs/<id>/{result.json, plan_code.py, plan_objects.json, frames/..., video.mp4}

Deterministic: every random stream is seeded from (storm name, plan hash); the ledger hash is over the
ledger only (runtimes and run ids are excluded).
CLI: .venv/bin/python -m sim.run elnino_ar_2026 [--plan file.py] [--label x] [--no-video]
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

os.environ.setdefault("NUMBA_NUM_THREADS", "32")   # production runs; box is shared (80 cores)

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUNS = ROOT / "runs"
GRID_DIR = ROOT / "data" / "grid"
DEFAULT_THREADS = 32


# ----------------------------------------------------------------------------- helpers
def plan_hash_of(plan_code: str | None) -> str:
    if not plan_code or not plan_code.strip():
        return "noplan"
    return hashlib.sha256(plan_code.strip().encode()).hexdigest()[:16]


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


def ledger_hash(ledger: dict) -> str:
    s = json.dumps(_jsonable(ledger), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode()).hexdigest()


def _new_run_id(label: str) -> str:
    tag = "".join(c if c.isalnum() else "-" for c in (label or "run").lower())[:24].strip("-") or "run"
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}-{tag}"


def _aep(storm) -> float | None:
    st = getattr(storm, "stats", {}) or {}
    for k in ("atlas14_ari_years", "atlas14_6h_ari_years", "ari_years"):
        if st.get(k):
            return 1.0 / float(st[k])
    return None


def _baseline_total(storm_name: str) -> tuple[float | None, str | None]:
    """No-plan ledger total for this storm from runs/_baselines.json (real sim only)."""
    try:
        idx = json.loads((RUNS / "_baselines.json").read_text())
        rid = idx.get(storm_name)
        if rid:
            r = json.loads((RUNS / rid / "result.json").read_text())
            if not r.get("mock"):
                return float(r["ledger"]["total_usd"]), rid
    except Exception:
        pass
    return None, None


def _load_editor(plan_code, plan_dir, notes):
    """(editor, log, source). Prefer the sandbox export (data only); else run exec_plan in-process
    (restricted builtins/imports, no I/O)."""
    from sim.city_api import CityEditor, exec_plan
    if not plan_code or not plan_code.strip():
        if plan_dir and (Path(plan_dir) / "editor_state.json").exists():
            notes.append("plan_dir given without plan_code: using the sandbox export")
        else:
            return CityEditor(), {"items": [], "notes": [], "printed": []}, "none"
    if plan_dir and (Path(plan_dir) / "editor_state.json").exists():
        try:
            from agent.citylib import load_editor
            ed = load_editor(plan_dir)
            if isinstance(ed, CityEditor):
                if plan_code and "response" in plan_code and ed._response is None:
                    raise ValueError("sandbox export lost city.response")
                log = {"items": ed.summary()["items"], "notes": ed.notes, "printed": []}
                return ed, log, "sandbox_export"
            notes.append(f"sandbox export is {type(ed).__name__}, not CityEditor: re-running plan in-process")
        except Exception as e:
            notes.append(f"sandbox export unusable ({type(e).__name__}: {e}); re-running plan in-process")
    ed, log, err = exec_plan(plan_code)
    if err:
        raise ValueError(f"plan failed: {err}")
    return ed, log, "exec_plan"


def _truncate_storm(st, max_duration_s: float):
    """Shortened copy of a storm (tests / quick looks): first max_duration_s of the forcing."""
    import dataclasses
    n = int(max_duration_s // st.dt_frame_s) + 1
    kw = {f.name: getattr(st, f.name) for f in dataclasses.fields(st)}
    for k in ("t", "rain_mm_h", "gust_ms", "mean_ms", "tide_m", "wind_dir_t"):
        if isinstance(kw.get(k), np.ndarray) and kw[k].ndim >= 1 and len(kw[k]) >= n:
            kw[k] = kw[k][:n].copy()
    kw["duration_s"] = float(kw["t"][-1])
    kw["name"] = st.name   # same seeds as the full storm
    return type(st)(**kw)


_FRAME_FIELDS = ("depth", "u", "v", "saturation")


def _quantize_frames(wres):
    """Round frame fields through float16 (as stored in the water cache) so cached and fresh runs are identical."""
    for k in _FRAME_FIELDS:
        a = getattr(wres, k, None)
        if isinstance(a, np.ndarray) and a.dtype != np.float16:
            setattr(wres, k, a.astype(np.float16).astype(np.float32))
    return wres


def _water_key(storm, phash: str) -> str:
    h = hashlib.sha256()
    h.update(f"{storm.name}|{storm.duration_s}|{phash}".encode())
    for p in (ROOT / "sim" / "water.py", GRID_DIR / "city.npz", GRID_DIR / "inlets.json", GRID_DIR / "trees.json"):
        h.update(p.read_bytes() if p.exists() else b"-")
    for p in sorted((ROOT / "data" / "reference").glob("water*.json")):
        h.update(p.read_bytes())
    for a in ("t", "rain_mm_h", "tide_m"):
        h.update(np.ascontiguousarray(getattr(storm, a), np.float64).tobytes())
    return h.hexdigest()[:16]


WATER_CACHE = RUNS / "_cache"


def _run_water_cached(water, wgrid, st, ed, phash, threads, verbose, notes, use_cache=True):
    """run_water with a disk cache keyed by (storm, plan hash, water.py + grid + params bytes): a re-run of the
    same storm + plan (e.g. baseline, determinism checks, a crash downstream) reloads instead of re-solving."""
    key = _water_key(st, phash)
    path = WATER_CACHE / f"water_{st.name}_{key}.npz"
    if use_cache and path.exists():
        try:
            wres = water.load_result(str(path))
            notes.append(f"water: reused cached result {path.name}")
            return _quantize_frames(wres), True
        except Exception as e:
            notes.append(f"water cache unreadable ({e}); re-solving")
    wres = water.run_water(wgrid, st, edits=ed.to_water_edits(), threads=threads, verbose=verbose)
    _quantize_frames(wres)
    if not use_cache:
        return wres, False
    try:
        WATER_CACHE.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + f".{os.getpid()}.tmp.npz")
        water.save_result(wres, str(tmp))
        os.replace(tmp, path)
    except Exception as e:
        notes.append(f"water cache write failed: {e}")
    return wres, False


_WIND_MEM: dict = {}
PINS = RUNS / "_wind_pins.json"


def _wind_from_npz(path: Path):
    from sim.wind import WindField
    key = (str(path), path.stat().st_mtime)
    if key not in _WIND_MEM:
        _WIND_MEM.clear()
        _WIND_MEM[key] = WindField.from_npz(path)
    return _WIND_MEM[key]


def _cached_fields():
    out = []
    for p in sorted(GRID_DIR.glob("wind_*.npz")):
        parts = p.stem.split("_")
        try:
            d = float(parts[1])
        except (IndexError, ValueError):
            continue
        out.append((p, d, parts[2].startswith("f") and len(parts[2]) == 11))
    return out


def _wind_field(wind_dir_deg: float, agrid: dict, screens: list, notes: list):
    """Steady wind field for the storm, never blocking on a fresh LBM solve (8 m takes >1 h on the shared box).

    Order: pinned field for this direction (runs/_wind_pins.json, so baseline and plan runs of a storm always
    share one field) > exact 8 m cache > best cached field (nearest direction, 8 m before 16 m) > fresh 16 m solve.
    Screens are simulated only if their cached screened fields exist (get_wind_plan), else noted."""
    from sim import wind
    snapped = float(5.0 * round(float(wind_dir_deg) / 5.0)) % 360.0
    key = f"{int(snapped):03d}"
    pins = json.loads(PINS.read_text()) if PINS.exists() else {}
    path = Path(pins[key]) if key in pins else None
    if path is not None and not path.exists():
        notes.append(f"pinned wind field {path.name} missing; re-pinning")
        path = None
    if path is None:
        exact = GRID_DIR / f"wind_{key}_{wind._grid_hash(agrid, 8.0, 448.0, snapped, [], 1.3)}.npz"
        if exact.exists():
            path = exact
        else:
            cands = _cached_fields()
            if cands:
                def score(c):
                    dd = abs((c[1] - snapped + 180) % 360 - 180)
                    return (dd, c[2], -c[0].stat().st_mtime)
                path = min(cands, key=score)[0]
            else:
                notes.append("no cached wind field: computing a 16 m field now")
                wf = wind.get_wind(snapped, agrid, fast=True, verbose=False)
                path = Path(wf.path)
        try:   # atomic, multi-process safe (re-read so concurrent pins for other directions survive)
            cur = json.loads(PINS.read_text()) if PINS.exists() else {}
            if key in cur and Path(cur[key]).exists():
                path = Path(cur[key])
            cur[key] = str(path)
            tmp = PINS.with_name(f"{PINS.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(cur, indent=1))
            os.replace(tmp, PINS)
        except Exception as e:
            notes.append(f"wind pin write failed: {e}")
    wf = _wind_from_npz(path)
    if abs((wf.wind_dir_deg - snapped + 180) % 360 - 180) > 0.5 or wf.dx > 8.0:
        notes.append(f"wind: storm direction {snapped:.0f} deg uses cached field {path.name} "
                     f"({wf.wind_dir_deg:.0f} deg, {wf.dx:.0f} m lattice)")
    if screens:
        try:
            g0 = {k: v for k, v in agrid.items() if k != "wind_screens"}
            need = [GRID_DIR / f"wind_{key}_f{wind._grid_hash(g0, 16.0, 448.0, snapped, s, ft)}.npz"
                    for s, ft in (([], 1.3), (screens, 0.6))]
            if all(p.exists() for p in need) and path.name.startswith(f"wind_{key}_") and wf.dx == 8.0:
                return wind.get_wind_plan(snapped, agrid, screens=screens, verbose=False)
            notes.append(f"wind screens ({len(screens)}) not simulated: no cached screened LBM field "
                         "(a fresh solve takes too long on the shared box)")
        except Exception as e:
            notes.append(f"wind screens failed: {type(e).__name__}: {e}")
    return wf


def _people_metrics(pres) -> dict:
    if pres is None:
        return {}
    tt = pres.summary.get("totals", {})
    keys = ("present_population", "evacuated", "sheltered_peak", "at_risk_peak", "stranded_peak", "stranded_end",
            "trapped_drivers", "rescues_needed", "rescued", "exposed_people", "expected_injuries",
            "expected_fatalities", "unmet_meals", "unmet_water_gal", "at_risk_person_hours")
    return {k: tt.get(k) for k in keys if k in tt}


# ----------------------------------------------------------------------------- main
def run_storm(storm_name: str, plan_code: str | None = None, label: str = "run", plan_dir: str | None = None,
              seed_key: str | None = None, run_id: str | None = None, render: bool = True,
              threads: int | None = None, verbose: bool = False, max_duration_s: float | None = None,
              water_cache: bool = True) -> Path:
    """Simulate one storm (optionally with a plan) and write runs/<run_id>/. Returns the run dir."""
    t_all = time.time()
    rt = {}
    notes = []
    threads = threads or DEFAULT_THREADS
    try:
        import numba
        threads = max(1, min(int(threads), int(numba.config.NUMBA_NUM_THREADS)))
        numba.set_num_threads(threads)
    except Exception:
        pass

    from sim import costs, damage, people, storm as storm_mod, trees as trees_mod, water, wind
    run_id = run_id or _new_run_id(label)
    rd = RUNS / run_id
    fr = rd / "frames"
    fr.mkdir(parents=True, exist_ok=True)
    phash = plan_hash_of(plan_code)
    seed_key = seed_key or phash
    has_plan = bool(plan_code and plan_code.strip()) or bool(plan_dir)

    # ---- 1. storm, grid, plan
    t = time.time()
    st = storm_mod.load_storm(storm_name)
    if max_duration_s:
        st = _truncate_storm(st, max_duration_s)
        notes.append(f"storm truncated to {st.duration_s:.0f} s (test mode)")
    wgrid = water.load_grid()
    rt["load_grid"] = time.time() - t
    t = time.time()
    ed, plan_log, plan_src = _load_editor(plan_code, plan_dir, notes)
    rt["plan"] = time.time() - t

    # ---- 2. wind (cached steady LBM field; screened ratio field if the plan has screens)
    t = time.time()
    agrid = wind.load_grid()
    screens = ed.wind_screens_utm()
    wf = _wind_field(st.wind_dir_deg, agrid, screens, notes)
    rt["wind"] = time.time() - t

    # ---- 3. water (baseline leaf clog in water.py; plan clear_drains via clog_factor)
    t = time.time()
    wres, water_cached = _run_water_cached(water, wgrid, st, ed, phash, threads, verbose, notes, water_cache)
    try:
        validation = water.validate(wres, wgrid)
    except Exception as e:
        validation = {"error": f"{type(e).__name__}: {e}"}
    rt["water"] = time.time() - t

    # ---- 4. trees, debris, glazing (saturation from water) + flood-driven cars
    t = time.time()
    tw = np.asarray(wres.t_frames, float)
    ts = np.asarray(st.t, float)
    sat = trees_mod._resample_frames(tw, ts, wres.saturation) if wres.saturation is not None else None
    tree_list = ed.edited_trees()
    car_list = ed.edited_cars()
    tres = trees_mod.run_trees(st, wf, grid=agrid, trees=tree_list, cars=car_list, buildings=ed.buildings,
                               saturation=sat, plan_hash=seed_key, verbose=verbose)
    rt["trees"] = time.time() - t
    t = time.time()
    struck_ids = {s["target_id"] for s in tres.strikes if s["kind"] == "car"}
    struck = np.array([c["id"] in struck_ids for c in car_list], bool)
    car_frames, car_states = trees_mod.run_cars(car_list, tw, wres.depth, wres.u, wres.v, agrid["meta"],
                                                bldg_id=agrid["bldg_id"], ground=agrid["z"].astype(np.float32),
                                                struck=struck)
    for s_, c in zip(car_states, car_list):
        s_["struck_by_tree"] = bool(c["id"] in struck_ids)
    if len(tw) != len(ts) or not np.allclose(tw, ts):
        car_frames = trees_mod._resample_frames(tw, ts, car_frames)
    tree_inputs = trees_mod.ledger_inputs(tres, car_states)
    rt["cars"] = time.time() - t

    # ---- 5. people + emergency response
    t = time.time()
    pres = people.run_people(st, water=wres, response=ed._response, trees=tres.trees, seed_key=seed_key)
    rt["people"] = time.time() - t

    # ---- 6. damage (life safety priced once, in damage.py, from people's expected fatalities)
    t = time.time()
    led = damage.assess(wgrid, ed.buildings, wres, tree_result=tree_inputs, cars_result=None,
                        wind_result=None if "glazing" in tree_inputs else wf, storm=st,
                        plan=ed if has_plan else None, people_result=pres)
    addon = pres.ledger_addon or {}
    unmet = float(addon.get("unmet_needs_usd") or 0.0)
    led["by_cause"]["unmet_needs"] = round(unmet, 2)
    if unmet > 0:
        led["lines"].append({"cause": "unmet_needs", "usd": round(unmet, 2),
                             "basis": "shelter meals + water not delivered (FEMA 3 meals, 1 gal/person/day) x unit cost",
                             "source": "data/reference/response_costs.json"})
    led["total_usd"] = round(float(sum(led["by_cause"].values())), 2)
    led["people"] = _people_metrics(pres)
    led["expected_fatalities"] = led["people"].get("expected_fatalities")
    led["expected_injuries"] = led["people"].get("expected_injuries")
    led["at_risk_person_hours"] = led["people"].get("at_risk_person_hours")
    led["notes"] = list(led.get("notes", [])) + [
        "life_safety priced once in sim/damage.py (people.ledger_addon.life_safety_usd not added again)",
        "emergency response operating cost is in plan_cost (measured by sim/people.py), not in the damage total",
        "injuries are reported (expected_injuries) but not priced"]
    rt["damage"] = time.time() - t

    # ---- 7. plan cost (+ response lines measured by the people sim) and BCA
    t = time.time()
    if has_plan:
        pc = costs.price_plan(None, ed, ed, duration_s=float(st.duration_s))
        rlines = (pres.summary.get("response_cost") or {}).get("lines") or []
        if ed._response is not None and rlines:
            keep = [l for l in pc["items"] if l.get("category") != "emergency_response"]
            for l in rlines:
                l = dict(l)
                l.setdefault("category", "emergency_response")
                l["measured"] = True
                l.setdefault("flags", [])
                keep.append(l)
            pc["items"] = keep
            pc["total_usd"] = round(float(sum(l["usd"] for l in keep)), 2)
            cats = {}
            for l in keep:
                cats[l.get("category", "other")] = round(cats.get(l.get("category", "other"), 0.0) + l["usd"], 2)
            pc["by_category"] = cats
    else:
        pc = {"total_usd": 0.0, "items": [], "by_category": {}, "flags": []}
    base_total, base_id = (None, None)
    bca = None
    if has_plan:
        base_total, base_id = _baseline_total(storm_name)
        aep = _aep(st)
        if base_total is not None and aep and pc["total_usd"] > 0:
            bca = costs.bca(base_total - led["total_usd"], pc["total_usd"], aep)
            bca.update(baseline_run_id=base_id, baseline_damage_usd=base_total,
                       damage_avoided_usd=round(base_total - led["total_usd"], 2))
    rt["costs"] = time.time() - t

    # ---- 8. outputs
    t = time.time()
    dman = water.write_web_depth(wres, str(fr), grid=wgrid)
    trees_mod.export_web(tres, fr, car_frames=car_frames)
    wf.write_wind_slice(fr / "wind_slice.f32")
    people.write_people(pres, str(fr))
    (fr / "damage.json").write_text(json.dumps(_jsonable({"by_building": led["by_building"], "total_usd": led["total_usd"],
                                                          "by_cause": led["by_cause"]}), separators=(",", ":")))
    nfr = int(dman["n_frames"])
    tfr = np.asarray(dman["t"], float)
    manifest = {
        "run_id": run_id, "label": label, "storm": storm_name, "nx": dman["nx"], "ny": dman["ny"],
        "dx_frame": dman["dx_frame"], "x0": float(wgrid.x0), "y0": float(wgrid.y0), "n_frames": nfr,
        "t": tfr.tolist(), "gust": [round(float(st.gust_at(x)), 3) for x in tfr],
        "rain": [round(float(st.rain_at(x)), 3) for x in tfr], "tide": [round(float(st.tide_at(x)), 3) for x in tfr],
        "n_cars": int(car_frames.shape[1]), "n_debris": int(tres.debris_frames.shape[1]),
        "n_people": int(pres.frames.shape[1]),
        "files": {"depth": "depth.u16", "saturation": "saturation.u8", "velocity": "velocity.i16",
                  "max_depth": "max_depth.u16", "inlets": "inlets_state.json", "trees": "trees.json",
                  "debris": "debris.f32", "debris_meta": "debris.json", "debris_flight": "debris_flight.f32",
                  "inlet_clog": "inlet_clog.json", "cars": "cars.f32", "wind": "wind_slice.f32",
                  "people": "people.f32", "people_meta": "people.json", "damage": "damage.json"},
        "layouts": {"depth": "n_frames x ny x nx uint16 mm (8 m)", "velocity": "n_frames x ny x nx x 2 int16 mm/s",
                    "saturation": "n_frames x ny x nx uint8 (0..255)", "cars": "n_frames x n_cars x 4 f32 (x, y, z, yaw) UTM",
                    "debris": "n_frames x n_debris x 3 f32 UTM", "wind": "ny x nx x 2 f32 (u, v) amplification at 10 m",
                    "people": "n_frames x n_people x 3 f32 (x, y local m, state)"},
        "frame_counts": {"water": nfr, "storm": int(len(ts)), "cars": int(car_frames.shape[0]),
                         "debris": int(tres.debris_frames.shape[0]), "people": int(pres.frames.shape[0])},
    }
    if len({nfr, manifest["frame_counts"]["cars"], manifest["frame_counts"]["debris"], manifest["frame_counts"]["people"]}) > 1:
        notes.append(f"frame count mismatch: {manifest['frame_counts']}")
    (fr / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")))

    (rd / "plan_code.py").write_text(plan_code if plan_code else "# no plan (baseline)\n")
    objs = ed.export_objects(costed=pc) if has_plan else []
    if pres.plan_objects:
        pids = {o.get("id") for o in pres.plan_objects}
        objs = [o for o in objs if o.get("id") not in pids] + list(pres.plan_objects)
    (rd / "plan_objects.json").write_text(json.dumps(_jsonable({"objects": objs}), separators=(",", ":")))

    md = wres.max_depth
    land = (wgrid.cover != 3) & (wgrid.bldg_id < 0)
    pm = _people_metrics(pres)
    metrics = {
        "buildings_flooded": led.get("buildings_flooded"),
        "max_depth_m": round(float(md.max()), 3),
        "flooded_area_m2_gt10cm": float(((md > 0.10) & land).sum() * wgrid.dx ** 2),
        "flooded_area_m2_gt30cm": float(((md > 0.30) & land).sum() * wgrid.dx ** 2),
        "trees_failed": tres.summary.get("trees_failed"), "trees_snapped": tres.summary.get("snapped"),
        "trees_uprooted": tres.summary.get("uprooted"),
        "tree_strikes_cars": tres.summary.get("strikes_car"), "tree_strikes_buildings": tres.summary.get("strikes_building"),
        "glazing_buildings": tres.summary.get("glazing_buildings"),
        "cars_floated": int(sum(s["floated"] for s in car_states)),
        "cars_moved": int(sum(s["moved_m"] > 1.0 for s in car_states)),
        "cars_damaged": led.get("cars_damaged"),
        "people_at_risk_peak": pm.get("at_risk_peak"), "people_stranded_peak": pm.get("stranded_peak"),
        "people_rescued": pm.get("rescued"), "people_evacuated": pm.get("evacuated"),
        "expected_fatalities": pm.get("expected_fatalities"), "expected_injuries": pm.get("expected_injuries"),
        "sewer_basins_surcharged": int((wres.surcharge_s > 0).sum()), "sewer_basins": int(len(wres.surcharge_s)),
        "sewer_max_fill": round(float(np.max(wres.sewer_fill)), 3) if wres.sewer_fill.size else None,
        "inlets_backflow_ever": int((wres.inlet_state == 2).any(0).sum()) if wres.inlet_state.size else 0,
        "water_volumes_m3": {k: round(float(v), 1) for k, v in wres.volumes.items()},
    }
    rt["write"] = time.time() - t
    lh = ledger_hash(led)
    result = {
        "run_id": run_id, "label": label, "storm": storm_name, "storm_title": getattr(st, "title", storm_name),
        "mock": False, "has_plan": has_plan, "plan_hash": phash, "seed_key": seed_key, "plan_source": plan_src,
        "ledger": led, "ledger_hash": lh, "plan_cost": pc, "bca": bca, "metrics": metrics, "validation": validation,
        "mass_error_pct": round(float(wres.mass_error_pct), 5), "water_cached": water_cached,
        "water_solver_runtime_s": round(float(wres.runtime_s), 1), "plan_log": plan_log,
        "people_warnings": (pres.summary.get("warnings") or [])[:30],
        "trees_summary": tres.summary, "wind": {"dir_deg": float(wf.wind_dir_deg), "path": wf.path, "screens": len(screens)},
        "threads": threads, "notes": notes,
    }
    rt["total_before_video"] = time.time() - t_all
    result["runtimes_s"] = {k: round(v, 2) for k, v in rt.items()}
    (rd / "result.json").write_text(json.dumps(_jsonable(result), indent=1, default=str))

    # ---- video (top-down render for the model to watch)
    if render:
        t = time.time()
        try:
            from sim.render_video import render_run
            render_run(str(rd), str(rd / "video.mp4"), grid_dir=str(GRID_DIR), workers=16)
        except Exception as e:
            notes.append(f"video failed: {type(e).__name__}: {e}")
        rt["video"] = time.time() - t
        rt["total"] = time.time() - t_all
        result["runtimes_s"] = {k: round(v, 2) for k, v in rt.items()}
        result["notes"] = notes
        (rd / "result.json").write_text(json.dumps(_jsonable(result), indent=1, default=str))
    if verbose:
        print(json.dumps({"run_id": run_id, "total_usd": led["total_usd"], "ledger_hash": lh[:12],
                          "runtimes_s": result["runtimes_s"]}), flush=True)
    return rd


def register_baseline(storm_name: str, run_dir: Path):
    """Record a no-plan run as the baseline for this storm in runs/_baselines.json (used by agent/runner.py)."""
    p = RUNS / "_baselines.json"
    idx = json.loads(p.read_text()) if p.exists() else {}
    idx[storm_name] = Path(run_dir).name
    p.write_text(json.dumps(idx, indent=1))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("storm", nargs="?", default="elnino_ar_2026")
    ap.add_argument("--plan", default=None, help="file with def apply(city)")
    ap.add_argument("--plan-dir", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--baseline", action="store_true", help="register as the storm's no-plan baseline")
    ap.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    a = ap.parse_args()
    code = Path(a.plan).read_text() if a.plan else None
    rd = run_storm(a.storm, plan_code=code, label=a.label or ("baseline " + a.storm if not code else "plan"),
                   plan_dir=a.plan_dir, render=not a.no_video, threads=a.threads, verbose=True)
    if a.baseline and not code:
        register_baseline(a.storm, rd)
    print(rd)
