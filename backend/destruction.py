"""StormSim SF destruction engine: what breaks above ground, when, why, and what it costs.

run_destruction(storm, plan=None, wind=None, water=None, tree_result=None, ...) -> dict

Covers every mapped above-ground asset in the tile (sim/assets.py -> data/grid/assets.json, buildings.json,
trees.json, cars.json):
- buildings: Hazus-hurricane-style damage states DS1-DS4 (roof cover, roof deck, roof structure / walls) from the
  local roof-height gust; flood structural failure from depth x velocity when water.py gives velocities.
- wind-borne roof debris: closed-form quadratic-drag flight from each damaged roof, facade hits break windows.
- trees: sim/trees.py results when passed, else a built-in bending-moment snap / uproot model (wind_tree_params.json).
- power distribution: wood poles (published lognormal fragility), pole-to-pole domino, spans brought down by
  falling trees or poles, fused line sections de-energised -> customers out -> traffic signals and lights dark.
- Muni overhead contact system (trolleybus + tram wire): trees on the wire trip the feeder section.
- streetlights, traffic signals (heads, mast arms, flooded cabinets), bus shelters, lighting/telecom masts.
- falling poles / lights / masts hitting parked cars.

Deterministic (CONTRACT v3): each asset gets one uniform draw from a fixed city seed + asset kind (common random
numbers, not storm/plan-seeded, so plan comparisons are paired), giving bit-identical results for the same storm and
plan; a bigger storm can only break more.
Every parameter lives in backend/reference/destruction_params.json with its source.
Host inputs are adapted in destruction_adapter.py; no second weather engine is run.

Fragility convention: lognormal in the LOCAL 3-s gust at the asset's height = storm reference gust (10 m open
exposure) x ASCE 7 exposure-B profile ratio x speed-up. The speed-up is sim/wind.py's field when `wind` is passed
(`wind.speedup(x, y, z)` or `|wind.wind_at(x, y, z)| / wind.u_ref`), else a tall-building proxy.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import os
import zlib

import numpy as np
from scipy.ndimage import maximum_filter
from scipy.special import ndtri

ROOT = P_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "backend", "reference")
GRID = os.path.join(ROOT, "data", "grid")
RHO_AIR = 1.225
G = 9.81
# Common random numbers: each asset's capacity draw is a property of the city, the same for every storm and plan,
# so a plan-vs-baseline difference is the plan's effect only and a stronger storm can only break more.
CITY_SEED = int(hashlib.sha256(b"stormsim-sf|destruction|city-v1").hexdigest()[:16], 16)
KINDS = ("buildings", "trees", "power_poles", "power_spans", "ocs_spans", "streetlights", "signals", "shelters", "masts")

_P = None


def params():
    global _P
    if _P is None:
        d = json.loads(Path(REF, "destruction_params.json").read_text())
        wt = json.loads(Path(REF, "wind_tree_params.json").read_text())["params"]
        df = json.loads(Path(REF, "damage_functions.json").read_text())
        d["wt"] = {k: v["value"] for k, v in wt.items()}
        d["glazing"] = df["glazing"]
        d["trees_df"] = df["trees"]
        d["people"] = df["people"]
        _P = d
    return _P


def _load(name):
    p = os.path.join(GRID, name)
    return json.load(open(p)) if os.path.exists(p) else None


def _g(obj, k, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(k, default)
    return getattr(obj, k, default)


def plan_hash(plan):
    if plan is None:
        return "noplan"
    code = _g(plan, "code") or _g(plan, "plan_code")
    if code:
        return hashlib.sha256(str(code).encode()).hexdigest()[:16]
    keys = ("tree_actions", "undergrounded_lines", "hardened_poles", "roof_retrofit", "removed_cars")
    values = {k: _g(plan, k) for k in keys}
    values.update({k: _g(plan, k) for k in ("backup_power", "closed_routes") if _g(plan, k)})
    blob = json.dumps(values, sort_keys=True, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _rng(seed, kind):
    return np.random.default_rng([seed & 0xFFFFFFFF, (seed >> 32) & 0xFFFFFFFF, zlib.crc32(kind.encode())])


def _lognormal_cap(median, beta, u):
    return np.asarray(median, float) * np.exp(np.asarray(beta, float) * ndtri(np.clip(u, 1e-9, 1 - 1e-9)))


def _first(hit):
    """Index of the first True along axis 1, -1 where none."""
    idx = np.argmax(hit, axis=1)
    return np.where(hit.any(axis=1), idx, -1)


def _unit(deg):
    a = np.radians(np.asarray(deg, float))
    return np.sin(a), np.cos(a)        # compass bearing (toward) -> (east, north)


def _pt_seg_dist(px, py, ax, ay, bx, by):
    """Distance from points (n,) to segments (m,) -> (n, m); and the along-segment parameter."""
    px, py = np.asarray(px, float)[:, None], np.asarray(py, float)[:, None]
    dx, dy = (bx - ax)[None, :], (by - ay)[None, :]
    L2 = np.maximum(dx * dx + dy * dy, 1e-9)
    s = np.clip(((px - ax[None, :]) * dx + (py - ay[None, :]) * dy) / L2, 0.0, 1.0)
    return np.hypot(px - ax[None, :] - s * dx, py - ay[None, :] - s * dy), s


def _seg_seg_dist(p0, p1, q0, q1):
    """Min distance between one segment p0-p1 (2,) and m segments q0-q1 (m, 2) (Ericson 2005, RTCD 5.1.9)."""
    d1 = (p1 - p0)[None, :]
    d2 = q1 - q0
    r = p0[None, :] - q0
    a = float((d1 * d1).sum())
    e = (d2 * d2).sum(1)
    f = (d2 * r).sum(1)
    c = (d1 * r).sum(1)
    b = (d1 * d2).sum(1)
    den = a * e - b * b
    s = np.where(den > 1e-12, np.clip((b * f - c * e) / np.maximum(den, 1e-12), 0, 1), 0.0)
    t = (b * s + f) / np.maximum(e, 1e-12)
    t_c = np.clip(t, 0, 1)
    s = np.where(t < 0, np.clip(-c / max(a, 1e-12), 0, 1), np.where(t > 1, np.clip((b - c) / max(a, 1e-12), 0, 1), s))
    cp = p0[None, :] + d1 * s[:, None]
    cq = q0 + d2 * t_c[:, None]
    return np.hypot(*(cp - cq).T)


# ------------------------------------------------------------------ city grid + wind
class _City:
    def __init__(self, grid=None):
        meta = grid if grid is not None else json.load(open(os.path.join(GRID, "meta.json")))
        self._cell = _g(grid, "cell")
        self.x0, self.y0, self.dx = float(_g(grid, "x0", _g(meta, "x0"))), float(_g(grid, "y0", _g(meta, "y0"))), float(_g(grid, "dx", _g(meta, "dx")))
        if grid is not None and _g(grid, "bldg_id") is not None:
            self.bldg_id = np.asarray(_g(grid, "bldg_id"))
            self.bldg_h = np.asarray(_g(grid, "bldg_h"))
            cover = _g(grid, "cover")
        else:
            z = np.load(os.path.join(GRID, "city.npz"))
            self.bldg_id, self.bldg_h, cover = z["bldg_id"], z["bldg_h"], z["cover"]
        self.ny, self.nx = self.bldg_id.shape
        # Roadway: paved cells at least ~6 m from any building (sidewalks and building edges excluded).
        from scipy.ndimage import distance_transform_edt
        dist = distance_transform_edt(self.bldg_id < 0) * self.dx
        self.roadway = (np.asarray(cover) == 0) & (dist >= 6.0) if cover is not None else (dist >= 6.0)

    def cell(self, x, y):
        if self._cell is not None:
            return self._cell(x, y)
        ix = np.clip(np.floor((np.asarray(x, float) - self.x0) / self.dx).astype(int), 0, self.nx - 1)
        iy = np.clip(np.floor((np.asarray(y, float) - self.y0) / self.dx).astype(int), 0, self.ny - 1)
        return ix, iy

    def sample(self, arr, x, y):
        ix, iy = self.cell(x, y)
        return np.asarray(arr)[iy, ix]


class WindModel:
    def __init__(self, storm, city, P, wind=None):
        self.t = np.asarray(storm.t, float)
        self.gust = np.asarray(storm.gust_ms, float)
        dt = _g(storm, "wind_dir_t")
        self.dir_from = np.asarray(dt, float) if dt is not None else np.full(len(self.t), float(storm.wind_dir_deg))
        self.city, self.P, self.wind = city, P, wind
        tb = P["tall_building_speedup"]
        r = int(round(tb["radius_m"] / city.dx))
        self._tall = maximum_filter(city.bldg_h.astype(np.float32), size=2 * r + 1, mode="nearest")
        if wind is not None:
            self.source = (f"sim/wind.py LBM field ({_g(wind, 'dx', 0):.0f} m lattice, {_g(wind, 'wind_dir_deg', 0):.0f} deg)"
                           if hasattr(wind, "wind_at") else "wind field passed in")
        else:
            self.source = "ASCE 7 exposure-B profile x tall-building proxy (no LBM field passed)"

    def _kz(self, z, exp):
        e = self.P["wind_profile"][exp]
        z = np.maximum(np.asarray(z, float), self.P["wind_profile"]["z_min_m"])
        return 2.01 * (z / e["zg_m"]) ** (2.0 / e["alpha"])

    def profile(self, z):
        return np.sqrt(self._kz(z, "exposure_B") / self._kz(10.0, "exposure_C"))

    def factor(self, x, y, z, street=True):
        x, y = np.asarray(x, float), np.asarray(y, float)
        z = np.broadcast_to(np.asarray(z, float), x.shape)
        w = self.wind
        if w is not None:
            if hasattr(w, "speedup"):
                return np.asarray(w.speedup(x, y, z), float)
            if hasattr(w, "wind_at"):
                v = np.nan_to_num(np.asarray(w.wind_at(x, y, np.ascontiguousarray(z)), float))
                uref = _g(w, "u_ref") or _g(w, "U_ref")
                if v.ndim == 1 and uref is None:
                    # sim/wind.py WindField: speed amplification vs the 10 m inflow, U = gust(t) x A (as sim/trees.py)
                    return v
                return np.linalg.norm(v.reshape(len(x), -1)[:, :3], axis=1) / float(uref)
        f = self.profile(z)
        if street:
            tb = self.P["tall_building_speedup"]
            f = f * np.minimum(tb["max_factor"], 1.0 + tb["k_per_100m"] * self.city.sample(self._tall, x, y) / 100.0)
        return f

    def design_ratio(self, z):
        """Undisturbed wind at height z relative to 10 m (>= 1): the LBM inflow log law, or the exposure-B profile."""
        if self.wind is not None and hasattr(self.wind, "log_profile"):
            return np.maximum(1.0, self.wind.log_profile(z))
        return np.maximum(1.0, self.profile(z) / self.profile(10.0))

    def V(self, factor):
        return np.asarray(factor, float)[:, None] * self.gust[None, :]

    def toward(self, k):
        return (self.dir_from[np.asarray(k)] + 180.0) % 360.0


# ------------------------------------------------------------------ trees
def _saturation(storm, P, n):
    rain = np.asarray(storm.rain_mm_h, float)
    dt = float(storm.dt_frame_s)
    cum = np.concatenate([[0.0], np.cumsum(rain[:-1] * dt / 3600.0)])[:n]
    ante = float(_g(storm, "antecedent_rain_mm", 0.0) or 0.0)
    return np.clip((cum + ante) / P["tree_fallback"]["sat_storage_mm"], 0.0, 1.0)


def tree_failures(trees, wind, storm, plan, seed, P, water=None):
    """Built-in snap / uproot model (used when sim/trees.py results are not passed)."""
    W = P["wt"]
    n = len(trees)
    x = np.array([t["x"] for t in trees]); y = np.array([t["y"] for t in trees])
    H = np.maximum(np.array([t.get("height_m") or 6.0 for t in trees], float), 2.0)
    d = np.array([t.get("dbh_cm") or 15.0 for t in trees], float) / 100.0
    Dc = np.array([t.get("crown_m") or 3.0 for t in trees], float)
    mor = np.array([t.get("wood_mor_mpa") or 40.0 for t in trees], float) * 1e6
    acts = _g(plan, "tree_actions", {}) or {}
    act = np.array([acts.get(int(t["id"]), acts.get(str(t["id"]), "")) for t in trees])
    keep = ~np.isin(act, ("remove", "replace"))
    Lc = W["tree_crown_length_frac"] * H
    A = math.pi / 4 * Dc * Lc * np.where(act == "prune", W["prune_area_factor"], 1.0)
    zc = H - Lc / 2
    rng = _rng(seed, "trees")
    u_decay, rr, noise = rng.random(n), rng.uniform(0.3, W["snap_decay_rr_max"], n), rng.normal(0.0, W["fall_noise_deg"], n)
    anch = np.exp(P["tree_fallback"]["anchorage_beta"] * ndtri(np.clip(rng.random(n), 1e-9, 1 - 1e-9)))
    snap_fac = np.where(u_decay < W["snap_decay_prevalence"], 1 - rr ** 4, 1.0)
    Msnap = mor * math.pi * d ** 3 / 32 * snap_fac * np.where(act == "brace", W["brace_strength_factor"], 1.0)
    mstem = W["wood_density_green"] * W["stem_form_factor"] * math.pi / 4 * d ** 2 * H
    Mup0 = W["uproot_creg_nm_per_kg"] * mstem * W["uproot_calib_factor"] * anch
    nf = len(wind.t)
    sat_grid = _g(water, "soil_sat")
    if sat_grid is not None and np.ndim(sat_grid) == 3 and np.shape(sat_grid)[1:] == wind.city.bldg_id.shape:
        ix, iy = wind.city.cell(x, y)
        sat = np.asarray(sat_grid)[:nf, iy, ix].T
    else:
        sat = np.broadcast_to(_saturation(storm, P, nf)[None, :], (n, nf))
    U = wind.V(wind.factor(x, y, zc)) * W["tree_gust_dyn_factor"]
    u0 = W["tree_vogel_u0_ms"]
    F = 0.5 * W["rho_air"] * W["tree_cd0"] * A[:, None] * U ** 2 * np.where(U > u0, (np.maximum(U, 1e-6) / u0) ** (-W["tree_vogel_exponent"]), 1.0)
    tf = P["tree_fallback"]
    M = W["tree_load_factor"] * tf.get("load_scale", 1.0) * F * zc[:, None]
    Msnap = Msnap * tf.get("strength_scale", 1.0)
    Mup = Mup0[:, None] * tf.get("strength_scale", 1.0) * (1 - W["uproot_sat_reduction"] * sat)
    k = _first((M >= np.minimum(Msnap[:, None], Mup)) & keep[:, None])
    out = []
    for i in np.nonzero(k >= 0)[0]:
        kk = int(k[i])
        out.append({"id": int(trees[i]["id"]), "frame": kk, "fail_t": float(wind.t[kk]),
                    "mode": "snap" if Msnap[i] <= Mup[i, kk] else "uproot",
                    "fall_dir_deg": round(float((wind.toward(kk) + noise[i]) % 360.0), 1), "source": "destruction.fallback"})
    return out


def calibrate_trees(target_ms=42.0, target_reports=None, storm_name="dec31_2022", inputs=None):
    """Calibrate the fallback tree model (run once; results stored in destruction_params.json tree_fallback):
    1. load_scale so the median critical crown gust of sound (defect-free) inventory trees is 42 m/s (Virot et al.
       2016, Phys. Rev. E 93:023001), solved in closed form from M(U) = c U^(2-b) u0^b = M_snap;
    2. strength_scale (bisection, on both snap and uproot capacity) so the 31.12.2022 replay fails as many trees in
       the tile as SF 311 logged tree reports there (data/grid/validation.json counts.tree)."""
    P = params()
    W_ = P["wt"]
    if inputs is None or target_reports is None:
        raise ValueError("Calibration requires host inputs and an observed report count")
    trees = inputs.trees
    H = np.maximum(np.array([t.get("height_m") or 6.0 for t in trees], float), 2.0)
    d = np.array([t.get("dbh_cm") or 15.0 for t in trees], float) / 100.0
    Dc = np.array([t.get("crown_m") or 3.0 for t in trees], float)
    mor = np.array([t.get("wood_mor_mpa") or 40.0 for t in trees], float) * 1e6
    Lc = W_["tree_crown_length_frac"] * H
    A, zc = math.pi / 4 * Dc * Lc, H - Lc / 2
    b, u0 = W_["tree_vogel_exponent"], W_["tree_vogel_u0_ms"]
    c = W_["tree_load_factor"] * 0.5 * W_["rho_air"] * W_["tree_cd0"] * A * zc
    ucrit = (mor * math.pi * d ** 3 / 32 / (c * u0 ** b)) ** (1.0 / (2.0 - b))
    load_scale = float((np.median(ucrit) / target_ms) ** (2.0 - b))
    P["tree_fallback"]["load_scale"] = load_scale
    target = json.load(open(os.path.join(GRID, "validation.json")))["counts"]["tree"] if target_reports is None else target_reports
    st = inputs.storm
    Wm = WindModel(st, _City(inputs.grid), P, inputs.wind)
    seed = CITY_SEED
    lo, hi = 0.2, 50.0
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        P["tree_fallback"]["strength_scale"] = mid
        n = len(tree_failures(trees, Wm, st, None, seed, P))
        lo, hi = (mid, hi) if n > target else (lo, mid)
    P["tree_fallback"]["strength_scale"] = hi
    n = len(tree_failures(trees, Wm, st, None, seed, P))
    return {"load_scale": load_scale, "strength_scale": math.ceil(hi * 1e5) / 1e5,   # round up: stay at/below the target
            "median_ucrit_raw_ms": round(float(np.median(ucrit)), 2),
            "median_ucrit_sound_ms": round(target_ms * hi ** (1.0 / (2.0 - b)), 2),
            "replay_failures": n, "target_reports": int(target), "storm": storm_name}


def _external_trees(tree_result, wind):
    lst = tree_result.get("failed_trees") or tree_result.get("trees") if isinstance(tree_result, dict) else tree_result
    out = []
    for t in lst or []:
        t = t if isinstance(t, dict) else vars(t)
        if t.get("fail_t") is None:
            continue
        kk = int(np.clip(np.searchsorted(wind.t, float(t["fail_t"]), "left"), 0, len(wind.t) - 1))
        out.append({"id": int(t["id"]), "frame": kk, "fail_t": float(t["fail_t"]), "mode": t.get("mode", "snap"),
                    "fall_dir_deg": float(t.get("fall_dir_deg") or 0.0), "source": "sim/trees.py"})
    return sorted(out, key=lambda t: t["id"])


# ------------------------------------------------------------------ engine
def _class(b):
    occ = str(b.get("occupancy") or "RES1").upper()
    fl = int(b.get("floors") or 1)
    h = float(b.get("height_m") or 3.0 * fl)
    if fl >= 10 or h > 35:
        return "engineered_high"
    if occ.startswith("RES") and fl <= 4:
        return "wood_lowrise"
    if fl <= 3 and int(b.get("year_built") or 1950) < 1950:
        return "masonry_lowrise"
    if fl <= 3:
        return "wood_lowrise" if occ.startswith("RES") else "masonry_lowrise"
    return "engineered_mid"


def _interp_event_cost(hours, table, h):
    hs = np.asarray(table["hours"], float)
    v = np.asarray(h, float)
    out = {}
    for cls in ("residential", "small_ci", "medium_large_ci"):
        ys = np.asarray(table[cls], float)
        slope = (ys[-1] - ys[-2]) / (hs[-1] - hs[-2])
        out[cls] = np.where(v <= hs[-1], np.interp(v, hs, ys), ys[-1] + slope * (v - hs[-1]))
    return out


def run_destruction(storm, plan=None, wind=None, water=None, tree_result=None, assets=None, buildings=None,
                    trees=None, cars=None, grid=None, seed=None):
    P = params()
    costs = P["costs"]
    assets = assets if assets is not None else _load("assets.json")
    if assets is None:
        from .risk_assets import build_destruction_inventory
        assets = build_destruction_inventory()
    buildings = buildings if buildings is not None else _load("buildings.json")
    trees = trees if trees is not None else _load("trees.json")
    cars = cars if cars is not None else (_load("cars.json") or [])
    city = _City(grid)
    W = WindModel(storm, city, P, wind)
    t = W.t
    nf = len(t)
    t_end = float(t[-1])
    ph = plan_hash(plan)
    seed = seed if seed is not None else CITY_SEED
    notes, events = [], []
    under = set(int(i) for i in (_g(plan, "undergrounded_lines", []) or []))
    hardened = set(int(i) for i in (_g(plan, "hardened_poles", []) or []))
    retro = set(int(i) for i in (_g(plan, "roof_retrofit", []) or []))
    removed_cars = set(int(c) for c in (_g(plan, "removed_cars", []) or []))

    def ev(k, kind, i, x, y, cause, **kw):
        if k >= 0:
            events.append(dict(t=float(t[k]), kind=kind, id=int(i), x=round(float(x), 1), y=round(float(y), 1), cause=cause, **kw))

    # ---------------- trees (cascade driver)
    if tree_result is not None:
        ft = _external_trees(tree_result, W)
    else:
        ft = tree_failures(trees, W, storm, plan, seed, P, water)
        notes.append("trees: built-in snap/uproot model (sim/trees.py result not passed)")
    tby = {int(tr["id"]): tr for tr in trees}
    heavy_dbh = P["power_pole"]["tree_break_dbh_cm"]
    for f in ft:
        tr = tby.get(f["id"], {})
        ev(f["frame"], "tree", f["id"], tr.get("x", 0), tr.get("y", 0), f["mode"], fall_dir_deg=f["fall_dir_deg"],
           height_m=tr.get("height_m"))

    def tree_geom(f):
        tr = tby.get(f["id"])
        if tr is None:
            return None
        H = float(tr.get("height_m") or 6.0)
        rc = float(tr.get("crown_m") or 3.0) / 2
        ux, uy = _unit(f["fall_dir_deg"])
        return np.array([tr["x"], tr["y"]], float), np.array([ux, uy], float), H, rc, float(tr.get("dbh_cm") or 15.0)

    TG = [(f, tree_geom(f)) for f in ft]
    TG = [(f, g) for f, g in TG if g is not None]

    def tree_hits_segments(segs_a, segs_b, h_wire, reach_extra):
        """For each segment, earliest frame a failed tree lands on it, and that tree (DBH)."""
        m = len(segs_a)
        frame = np.full(m, -1)
        by = np.full(m, -1)
        dbh = np.zeros(m)
        if m == 0 or not TG:
            return frame, by, dbh
        mid = (segs_a + segs_b) / 2
        half = np.hypot(*(segs_b - segs_a).T) / 2
        for f, (p0, u, H, rc, d) in TG:
            reach = math.sqrt(max(0.0, (H + rc) ** 2 - h_wire ** 2))
            if reach <= 0.5:
                continue
            near = np.nonzero(np.hypot(*(mid - p0).T) <= reach + half + rc + reach_extra)[0]
            if len(near) == 0:
                continue
            dist = _seg_seg_dist(p0, p0 + u * reach, segs_a[near], segs_b[near])
            hit = near[dist <= rc + reach_extra]
            k = f["frame"]
            upd = hit[(frame[hit] < 0) | (frame[hit] > k)]
            frame[upd], by[upd], dbh[upd] = k, f["id"], d
        return frame, by, dbh

    def tree_hits_points(px, py):
        n = len(px)
        frame = np.full(n, -1)
        dbh = np.zeros(n)
        if n == 0 or not TG:
            return frame, dbh
        px, py = np.asarray(px, float), np.asarray(py, float)
        for f, (p0, u, H, rc, d) in TG:
            rx, ry = px - p0[0], py - p0[1]
            along = rx * u[0] + ry * u[1]
            perp = np.abs(-rx * u[1] + ry * u[0])
            hit = np.nonzero((along > 0.5) & (along <= H + rc) & (perp <= rc + 0.3))[0]
            k = f["frame"]
            upd = hit[(frame[hit] < 0) | (frame[hit] > k)]
            frame[upd], dbh[upd] = k, d
        return frame, dbh

    # ---------------- flood at assets (when water.py result is passed)
    maxd = _g(water, "max_depth")
    maxd = np.asarray(maxd, float) if maxd is not None else None

    def depth_at(px, py):
        if maxd is None or len(px) == 0:
            return np.zeros(len(px))
        return city.sample(maxd, px, py)

    k_peak = int(np.argmax(W.gust))
    if water is not None and _g(water, "max_dv") is None and _g(water, "depth") is not None and _g(water, "u") is not None:
        dep = np.asarray(_g(water, "depth"), np.float32)   # full sim/water.py WaterResult (sim/run.py): summarise it here
        dvf = dep * np.sqrt(np.asarray(_g(water, "u"), np.float32) ** 2 + np.asarray(_g(water, "v"), np.float32) ** 2)
        water = WaterSummary({"t_frames": np.asarray(_g(water, "t_frames"), float), "max_depth": dep.max(0),
                              "max_speed": np.asarray(_g(water, "max_speed"), np.float32), "max_dv": dvf.max(0),
                              "k_max_depth": dep.argmax(0).astype(np.int16), "k_max_dv": dvf.argmax(0).astype(np.int16),
                              "k_flood_peak": int(np.argmax(dep.reshape(len(dep), -1).sum(1)))})
        maxd = water.max_depth.astype(float)
        del dep, dvf
    w_t = _g(water, "t_frames")
    to_storm_k = (lambda kw: np.clip(np.searchsorted(t, np.asarray(w_t, float)[np.asarray(kw)], "left"), 0, nf - 1)) if w_t is not None else None
    k_kmax = _g(water, "k_max_depth")
    k_flood = int(to_storm_k(int(_g(water, "k_flood_peak")))) if (to_storm_k is not None and _g(water, "k_flood_peak") is not None) else k_peak

    def flood_k(px, py):
        """Storm frame the water peaks at each point (the gust peak when the water run has no timing)."""
        if k_kmax is None or to_storm_k is None or len(px) == 0:
            return np.full(len(px), k_peak)
        return to_storm_k(city.sample(k_kmax, px, py))

    nb = len(buildings)
    bx = np.array([b["x"] for b in buildings], float)
    by_ = np.array([b["y"] for b in buildings], float)
    # ---------------- buildings: wind damage states + flood structural
    bp = P["building"]
    cls = [_class(b) for b in buildings]
    med = np.array([bp["classes"][c]["ds_median_ms"] for c in cls], float).reshape(-1, 4)
    beta = np.array([bp["classes"][c]["beta"] for c in cls], float)
    bid = np.array([int(b.get("id", i)) for i, b in enumerate(buildings)])
    med *= np.where(np.isin(bid, list(retro)), 1.15, 1.0)[:, None]
    Hb = np.maximum(np.array([float(b.get("height_m") or 3.0) for b in buildings]), 3.0)
    ub = _rng(seed, "buildings").random(nb)
    capb = med * np.exp(beta * ndtri(np.clip(ub, 1e-9, 1 - 1e-9)))[:, None]
    # Fragility is referenced to the design wind at the roof: taller buildings are designed for the stronger wind
    # aloft (ASCE 7 Kz), so the local gust is divided by the undisturbed profile ratio at the roof height (>= 1).
    Vb = W.V(W.factor(bx, by_, Hb + (2.0 if wind is not None else 0.0), street=False) / W.design_ratio(Hb))
    ds_k = np.stack([_first(Vb >= capb[:, j:j + 1]) for j in range(4)], axis=1)   # frame each DS is reached
    ds = (ds_k >= 0).sum(1)
    ds_cause = np.where(ds > 0, "wind", "").astype(object)
    vmax = _g(water, "max_speed") if water is not None else None
    vmax = vmax if vmax is not None else _g(water, "max_velocity")
    if maxd is not None and vmax is not None:
        from scipy.ndimage import maximum_filter
        def _bldg_adjacent_depth(ids, values, count):
            out = np.zeros(count)
            mask = ids >= 0
            np.maximum.at(out, ids[mask], maximum_filter(values, size=3)[mask])
            return out
        mdv = _g(water, "max_dv")   # per-frame max of depth x speed (max depth x max speed overstates it)
        dv = _bldg_adjacent_depth(city.bldg_id, np.asarray(mdv, float) if mdv is not None else maxd * np.asarray(vmax, float), nb)
        vv = _bldg_adjacent_depth(city.bldg_id, np.asarray(vmax, float), nb)
        occupiable = np.array([float(b.get("area_m2") or 0.0) for b in buildings]) >= bp["flood_min_footprint_m2"]
        coll = occupiable & (vv >= bp["flood_collapse_v_ms"]) & (dv >= bp["flood_collapse_dv_m2s"])
        part = occupiable & ~coll & (dv >= bp["flood_partial_dv_m2s"])
        for m_, lvl in ((coll, 4), (part, 2)):
            up = m_ & (ds < lvl)
            ds[up] = lvl
            ds_cause[up] = "flood"
            for j in range(lvl):
                ds_k[up & (ds_k[:, j] < 0), j] = k_flood
        notes.append(f"flood structural: {int(coll.sum())} collapse, {int(part.sum())} partial (d*v)")
    elif water is not None:
        notes.append("flood structural damage skipped: water result has no velocities (max_speed)")
    # collapse debris field: wires, poles and trolley wire within ~half the building height of a collapsed building
    coll_i = np.nonzero(ds >= 4)[0]
    area_b = np.array([float(b.get("area_m2") or 0.0) for b in buildings])
    coll_reach = {int(i): math.sqrt(max(area_b[i], 1.0) / math.pi) + bp["collapse_debris_reach_h"] * Hb[i] for i in coll_i}

    def collapse_on_segments(a, b_):
        kk = np.full(len(a), -1)
        for i in coll_i:
            if not len(a):
                break
            dd, _ = _pt_seg_dist([bx[i]], [by_[i]], a[:, 0], a[:, 1], b_[:, 0], b_[:, 1])
            k4 = int(ds_k[i, 3])
            hit = (dd[0] <= coll_reach[int(i)]) & ((kk < 0) | (kk > k4))
            kk[hit] = k4
        return kk

    # trees into buildings (sim/trees.py strikes when given, else the first building on the fall line)
    tree_strikes = []
    ext_strikes = [s_ for s_ in ((tree_result.get("strikes") if isinstance(tree_result, dict) else None) or []) if s_.get("kind") == "building"]
    if ext_strikes:
        tree_strikes = [dict(t=float(s_["t_s"]), tree_id=int(s_["tree_id"]), bldg_id=int(s_["target_id"]),
                             x=round(float(tby.get(int(s_["tree_id"]), {}).get("x", 0.0)), 1),
                             y=round(float(tby.get(int(s_["tree_id"]), {}).get("y", 0.0)), 1)) for s_ in ext_strikes]
    else:
        for f, (p0, u_, H_, rc_, d_) in TG:
            ss = np.arange(1.0, H_ + 0.01, 1.0)
            X, Y = p0[0] + ss * u_[0], p0[1] + ss * u_[1]
            cid = city.sample(city.bldg_id, X, Y)
            own = city.sample(city.bldg_id, p0[0], p0[1])
            hit = np.nonzero((cid >= 0) & (cid != own))[0]
            if len(hit):
                tree_strikes.append(dict(t=float(t[f["frame"]]), tree_id=int(f["id"]), bldg_id=int(cid[hit[0]]),
                                         x=round(float(X[hit[0]]), 1), y=round(float(Y[hit[0]]), 1)))
    tree_strikes.sort(key=lambda r: (r["t"], r["tree_id"]))

    # ---------------- power: spans hit by trees
    pp, sp, lines = assets["power_poles"], assets["power_spans"], assets["power_lines"]
    span_line = np.array([s["line_id"] for s in sp], int)
    live_span = ~np.isin(span_line, list(under))
    SA = np.array([s["a"] for s in sp], float).reshape(-1, 2)
    SB = np.array([s["b"] for s in sp], float).reshape(-1, 2)
    hw = P["power_span"]["wire_height_m"]
    s_tree_k, s_tree_id, s_tree_dbh = tree_hits_segments(SA, SB, hw, P["power_span"]["contact_reach_m"])
    s_tree_k[~live_span] = -1
    s_coll_k = collapse_on_segments(SA, SB)
    s_coll_k[~live_span] = -1

    # ---------------- power poles: wind + domino + heavy trees pulling spans
    npole = len(pp)
    pole_xy = np.array([[p["x"], p["y"]] for p in pp], float).reshape(-1, 2)
    pole_live = np.array([not p["spans"] or any(live_span[s] for s in p["spans"]) for p in pp], bool)
    rng = _rng(seed, "power_poles")
    u, young = rng.random(npole), rng.random(npole) < P["power_pole"]["share_young"]
    pc = P["power_pole"]
    cap = np.where(young, _lognormal_cap(pc["young_median_gust_ms"], pc["young_beta"], u), _lognormal_cap(pc["median_gust_ms"], pc["beta"], u))
    cap *= np.array([1.5 if i in hardened else 1.0 for i in range(npole)])
    Vp = W.V(W.factor(pole_xy[:, 0], pole_xy[:, 1], 10.0)) if npole else np.zeros((0, nf))
    forced = np.full(npole, nf + 1)
    forced_cause = np.array([""] * npole, dtype=object)
    for i in coll_i:   # poles inside a collapse debris field
        if npole:
            hit = np.nonzero(np.hypot(pole_xy[:, 0] - bx[i], pole_xy[:, 1] - by_[i]) <= coll_reach[int(i)])[0]
            k4 = int(ds_k[i, 3])
            for e in hit:
                if k4 < forced[e]:
                    forced[e], forced_cause[e] = k4, "collapse"
    for s_i in np.nonzero((s_tree_k >= 0) & (s_tree_dbh >= heavy_dbh))[0]:
        s = sp[s_i]
        ends = [e for e in (s["pole_a"], s["pole_b"]) if e is not None]
        if ends:   # the nearer pole to the impact takes the pull
            tr = tby[int(s_tree_id[s_i])]
            e = min(ends, key=lambda j: math.hypot(pp[j]["x"] - tr["x"], pp[j]["y"] - tr["y"]))
            if s_tree_k[s_i] < forced[e]:
                forced[e], forced_cause[e] = s_tree_k[s_i], "tree"
    nbrs = [set() for _ in range(npole)]
    for s in sp:
        if s["pole_a"] is not None and s["pole_b"] is not None:
            nbrs[s["pole_a"]].add(s["pole_b"]); nbrs[s["pole_b"]].add(s["pole_a"])
    pole_k = np.full(npole, -1)
    pole_cause = np.array([""] * npole, dtype=object)
    mult = np.ones(npole)
    for k in range(nf):
        for _ in range(25):
            free = (pole_k < 0) & pole_live
            by_force = free & (forced == k)
            by_wind = free & ~by_force & (Vp[:, k] >= cap * mult)
            new = np.nonzero(by_force | by_wind)[0]
            if len(new) == 0:
                break
            for i in new:
                pole_k[i] = k
                pole_cause[i] = forced_cause[i] if by_force[i] else ("domino" if mult[i] < 1 else "wind")
                for j in nbrs[i]:
                    mult[j] = pc["domino_factor"]
    for i in np.nonzero(pole_k >= 0)[0]:
        ev(pole_k[i], "power_pole", i, pp[i]["x"], pp[i]["y"], pole_cause[i], fall_dir_deg=round(float(W.toward(pole_k[i])), 1))

    # spans down: tree on the wire or a supporting pole down
    span_k = s_tree_k.copy()
    span_cause = np.where(s_tree_k >= 0, "tree", "").astype(object)
    c_first = (s_coll_k >= 0) & ((span_k < 0) | (s_coll_k < span_k))
    span_k[c_first], span_cause[c_first] = s_coll_k[c_first], "collapse"
    for i, s in enumerate(sp):
        for e in (s["pole_a"], s["pole_b"]):
            if e is not None and pole_k[e] >= 0 and live_span[i] and (span_k[i] < 0 or pole_k[e] < span_k[i]):
                span_k[i], span_cause[i] = pole_k[e], "pole"
    for i in np.nonzero(span_k >= 0)[0]:
        ev(span_k[i], "power_span", i, *((SA[i] + SB[i]) / 2), span_cause[i], line_id=int(span_line[i]))

    # ---------------- fused line sections -> customers out
    line_k = np.full(len(lines), -1)
    line_pole = np.zeros(len(lines), bool)
    for i in np.nonzero(span_k >= 0)[0]:
        L = span_line[i]
        if line_k[L] < 0 or span_k[i] < line_k[L]:
            line_k[L] = span_k[i]
        line_pole[L] |= span_cause[i] == "pole"
    b_line = np.full(nb, -1)
    if live_span.any():
        live_idx = np.nonzero(live_span)[0]
        best = np.full(nb, np.inf)
        for c0 in range(0, nb, 2000):
            dd, _ = _pt_seg_dist(bx[c0:c0 + 2000], by_[c0:c0 + 2000], SA[live_idx, 0], SA[live_idx, 1], SB[live_idx, 0], SB[live_idx, 1])
            j = dd.argmin(1)
            best[c0:c0 + 2000] = dd[np.arange(len(j)), j]
            b_line[c0:c0 + 2000] = span_line[live_idx[j]]
        b_line[best > P["power_span"]["service_drop_m"]] = -1
    occ = np.array([str(b.get("occupancy") or "RES1").upper() for b in buildings])
    fa = np.array([float(b.get("floor_area_m2") or (b.get("area_m2") or 0) * max(1, int(b.get("floors") or 1))) for b in buildings])
    res = np.char.startswith(occ.astype(str), "RES")
    units = np.where(occ == "RES1", 1.0, np.maximum(1.0, fa / P["people"]["unit_floor_area_m2"]))
    cust = np.where(res, np.round(units), 1.0)
    oc = costs["outage_usd_per_event"]
    # a tree into an overhead-served building tears its service drop: that building is out on its own
    idx_of = {int(b.get("id", i)): i for i, b in enumerate(buildings)}
    svc_k = np.full(nb, -1)
    for r in tree_strikes:
        i = idx_of.get(r["bldg_id"])
        k = int(np.clip(np.searchsorted(t, r["t"], "left"), 0, nf - 1))
        if i is not None and b_line[i] >= 0 and (svc_k[i] < 0 or k < svc_k[i]):
            svc_k[i] = k
    line_out_k = np.where(b_line >= 0, line_k[np.maximum(b_line, 0)], -1) if len(lines) else np.full(nb, -1)
    out_k = np.where((line_out_k >= 0) & ((svc_k < 0) | (line_out_k <= svc_k)), line_out_k, svc_k)
    backup = set(_g(plan, "backup_power", []) or [])
    out_k[np.isin(bid, list(backup))] = -1
    out_b = (out_k >= 0) & (ds < 4)          # collapsed buildings are a total loss, not an outage
    out_cause = np.where(out_b & (out_k == line_out_k), "line", np.where(out_b, "service_drop", ""))
    for L in np.nonzero(line_k >= 0)[0]:
        sel = out_b & (b_line == L) & (out_cause == "line")
        ev(line_k[L], "outage", L, *SA[[j for j in lines[L]["spans"]][0]], "pole" if line_pole[L] else "span",
           customers=int(cust[sel].sum()), buildings=int(sel.sum()))

    def dark_near(px, py):
        """Frame a point goes dark: within power_reach of a span of a de-energised line."""
        n = len(px)
        kk = np.full(n, -1)
        down = np.nonzero(line_k[span_line] >= 0)[0] if len(sp) else np.zeros(0, int)
        if n == 0 or len(down) == 0:
            return kk
        dd, _ = _pt_seg_dist(px, py, SA[down, 0], SA[down, 1], SB[down, 0], SB[down, 1])
        reach = P["signal"]["power_reach_m"]
        lk = line_k[span_line[down]].astype(float)
        cand = np.where(dd <= reach, lk[None, :], np.inf).min(1)
        return np.where(np.isfinite(cand), cand, -1).astype(int)

    # ---------------- Muni overhead contact system
    ocs = assets["ocs_spans"]
    OA = np.array([s["a"] for s in ocs], float).reshape(-1, 2)
    OB = np.array([s["b"] for s in ocs], float).reshape(-1, 2)
    o_k, o_by, _ = tree_hits_segments(OA, OB, P["ocs"]["wire_height_m"], 0.5)
    o_coll = collapse_on_segments(OA, OB)
    o_cfirst = (o_coll >= 0) & ((o_k < 0) | (o_coll < o_k))
    o_k[o_cfirst], o_by[o_cfirst] = o_coll[o_cfirst], -1
    o_sec = np.array([s["section"] for s in ocs], int)
    o_mid = (OA + OB) / 2
    o_out = np.full(len(ocs), -1)
    for i in np.nonzero(o_k >= 0)[0]:
        ev(o_k[i], "ocs_span", i, *o_mid[i], "collapse" if o_by[i] < 0 else "tree", street=ocs[i]["street"], mode=ocs[i]["mode"])
        sel = (o_sec == o_sec[i]) & (np.hypot(*(o_mid - o_mid[i]).T) <= P["ocs"]["section_half_length_m"])
        o_out[sel] = np.where((o_out[sel] < 0) | (o_out[sel] > o_k[i]), o_k[i], o_out[sel])
    ocs_km_out = float(sum(ocs[i]["length_m"] for i in np.nonzero(o_out >= 0)[0]) / 1000.0)

    # ---------------- point assets: streetlights, signals, shelters, masts
    def wind_points(name, items, med, beta, z, degraded=None):
        n = len(items)
        if n == 0:
            return np.zeros(0, int), np.zeros(0), np.zeros(0)
        px = np.array([a["x"] for a in items], float); py = np.array([a["y"] for a in items], float)
        r = _rng(seed, name)
        uu = r.random(n)
        c = _lognormal_cap(med, beta, uu)
        if degraded:
            c = c * np.where(r.random(n) < degraded[0], degraded[1], 1.0)
        return _first(W.V(W.factor(px, py, z)) >= c[:, None]), px, py

    sl = P["streetlight"]
    lights = assets["streetlights"]
    lz = np.array([sl["high_mast_height_m"] if a.get("high_mast") else sl["height_m"] for a in lights], float)
    l_wk, lx, ly = wind_points("streetlights", lights, sl["median_gust_ms"], sl["beta"], lz, (sl["degraded_share"], sl["degraded_factor"]))
    l_tk, l_td = tree_hits_points(lx, ly)
    l_tk[l_td < heavy_dbh] = -1
    l_k = np.where((l_wk >= 0) & ((l_tk < 0) | (l_wk <= l_tk)), l_wk, l_tk)
    l_cause = np.where(l_k < 0, "", np.where(l_k == l_wk, "wind", "tree"))
    l_dark = dark_near(lx, ly)
    l_flood = depth_at(lx, ly) >= sl["flood_dark_depth_m"]
    l_flood_k = np.where(l_flood, flood_k(lx, ly), -1)
    for i in range(len(lights)):
        ev(l_k[i], "streetlight", i, lx[i], ly[i], l_cause[i], fall_dir_deg=round(float(W.toward(l_k[i])), 1) if l_k[i] >= 0 else None)

    sg = P["signal"]
    sigs = assets["signals"]
    h_wk, gx, gy = wind_points("signal_heads", sigs, sg["head_median_gust_ms"], sg["head_beta"], sg["height_m"])
    s_wk, _, _ = wind_points("signal_structures", sigs, sg["structure_median_gust_ms"], sg["structure_beta"], sg["height_m"])
    g_tk, g_td = tree_hits_points(gx, gy)
    g_dark = dark_near(gx, gy)
    g_flood = depth_at(gx, gy) >= sg["cabinet_flood_depth_m"]
    g_flood_k = np.where(g_flood, flood_k(gx, gy), -1)
    sig_state = []
    for i in range(len(sigs)):
        struct_k = min([k for k in (s_wk[i], g_tk[i] if g_td[i] >= heavy_dbh else -1) if k >= 0], default=-1)
        head_k = min([k for k in (h_wk[i], g_tk[i]) if k >= 0], default=-1)
        st = {"head_k": int(head_k), "struct_k": int(struct_k), "dark_k": int(g_dark[i]), "flooded": bool(g_flood[i]),
              "flood_k": int(g_flood_k[i])}
        sig_state.append(st)
        if struct_k >= 0:
            ev(struct_k, "signal", i, gx[i], gy[i], "tree" if struct_k == g_tk[i] else "wind", what="mast arm down")
        elif head_k >= 0:
            ev(head_k, "signal", i, gx[i], gy[i], "tree" if head_k == g_tk[i] else "wind", what="signal heads torn")
        if g_dark[i] >= 0:
            ev(g_dark[i], "signal_dark", i, gx[i], gy[i], "outage")
        if g_flood[i]:
            ev(int(g_flood_k[i]), "signal_dark", i, gx[i], gy[i], "flooded cabinet")

    shel = assets["shelters"]
    sh_wk, hx, hy = wind_points("shelters", shel, P["shelter"]["median_gust_ms"], P["shelter"]["beta"], 2.5)
    sh_tk, _ = tree_hits_points(hx, hy)
    sh_k = np.where((sh_wk >= 0) & ((sh_tk < 0) | (sh_wk <= sh_tk)), sh_wk, sh_tk)
    for i in range(len(shel)):
        ev(sh_k[i], "shelter", i, hx[i], hy[i], "wind" if sh_k[i] == sh_wk[i] else "tree")

    ms = assets["masts"]
    m_k, mx, my = wind_points("masts", ms, P["mast"]["median_gust_ms"], P["mast"]["beta"], P["mast"]["height_m"] * 0.6)
    for i in range(len(ms)):
        ev(m_k[i], "mast", i, mx[i], my[i], "wind", fall_dir_deg=round(float(W.toward(m_k[i])), 1) if m_k[i] >= 0 else None)

    # ---------------- falling poles / lights / masts onto parked cars
    car_xy = np.array([[c["x"], c["y"]] for c in cars], float).reshape(-1, 2)
    car_ids = np.array([int(c["id"]) for c in cars], int)
    car_hit = {}
    fallers = [(pole_k, pole_xy, P["power_pole"]["height_m"], "power_pole"),
               (l_k, np.c_[lx, ly] if len(lights) else np.zeros((0, 2)), lz, "streetlight"),
               (m_k, np.c_[mx, my] if len(ms) else np.zeros((0, 2)), P["mast"]["height_m"], "mast")]
    for kk, xy, hh, kind in fallers:
        for i in np.nonzero(np.asarray(kk) >= 0)[0]:
            if not len(car_xy):
                break
            ux, uy = _unit(W.toward(kk[i]))
            L = float(np.broadcast_to(hh, (len(xy),))[i])
            rx, ry = car_xy[:, 0] - xy[i, 0], car_xy[:, 1] - xy[i, 1]
            along, perp = rx * ux + ry * uy, np.abs(-rx * uy + ry * ux)
            for c in car_ids[(along > 0) & (along <= L) & (perp <= 1.2)]:
                if int(c) not in removed_cars:
                    car_hit.setdefault(int(c), (int(kk[i]), kind, int(i)))

    frac = np.r_[0.0, bp["roof_cover_lost_frac"]]
    area = np.array([float(b.get("area_m2") or 0.0) for b in buildings])
    roof_lost = area * frac[ds] * (ds_cause == "wind")
    floors = np.maximum(1, np.array([int(b.get("floors") or 1) for b in buildings]))
    top = np.minimum(1.0, 2.0 / floors)
    vs = np.array([float(b.get("value_structure") or 0) for b in buildings])
    vc = np.array([float(b.get("value_contents") or 0) for b in buildings])
    sr = np.r_[0.0, bp["structure_loss_ratio"]][ds]
    cr = np.r_[0.0, bp["contents_loss_ratio"]][ds]
    wind_b = ds_cause == "wind"
    usd_roof = roof_lost * costs["roof_cover_usd_m2"]["usd"]
    usd_struct = np.where(wind_b & (ds >= 3), sr * vs * np.where(ds == 4, 1.0, top), 0.0)
    usd_cont = np.where(wind_b, cr * vc * np.where(ds == 4, 1.0, top), 0.0)
    for i in np.nonzero(ds > 0)[0]:
        ev(int(ds_k[i, ds[i] - 1]), "building", int(bid[i]), bx[i], by_[i], ds_cause[i], ds=int(ds[i]),
           roof_lost_m2=round(float(roof_lost[i]), 1))

    # ---------------- wind-borne roof debris -> windows of downwind buildings
    rd = P["roof_debris"]
    gl = P["glazing"]
    rdr = _rng(seed, "debris")
    debris = []
    win = {}
    bh_by = {int(b): float(h) for b, h in zip(bid, Hb)}
    occ_by = {int(b): o for b, o in zip(bid, occ)}
    for i in np.nonzero((roof_lost > 0))[0]:
        k1 = int(ds_k[i, 0])
        U = float(Vb[i, k1])
        L = 2 * rd["areal_mass_kg_m2"][cls[i]] / (RHO_AIR * rd["cd"])
        wt = math.sqrt(G * L)
        h = float(Hb[i])
        tf = (wt / G) * math.acosh(math.exp(min(h / L, 30.0)))
        reps = rd["reps_per_building"]
        weight = roof_lost[i] * rd["airborne_fraction"] / rd["piece_area_m2"] / reps
        half = math.sqrt(max(area[i], 1.0)) / 2
        for r_ in range(reps):
            ox, oy = rdr.uniform(-half, half, 2)
            dirn = W.toward(k1) + rdr.normal(0.0, P["wt"]["fall_noise_deg"])
            ux, uy = _unit(dirn)
            ts = np.linspace(0.0, tf, 24)
            xs = U * ts - L * np.log1p(U * ts / L)
            zs = h - L * np.log(np.cosh(G * ts / wt))
            X, Y = bx[i] + ox + ux * xs, by_[i] + oy + uy * xs
            cid = city.sample(city.bldg_id, X, Y)
            chh = city.sample(city.bldg_h, X, Y)
            hitm = (cid >= 0) & (cid != bid[i]) & (chh >= zs)
            j = int(np.argmax(hitm)) if hitm.any() else -1
            end = j if j >= 0 else len(ts) - 1
            rec = dict(src=int(bid[i]), t0=float(t[k1]), x0=round(float(X[0]), 1), y0=round(float(Y[0]), 1), z0=round(h, 1),
                       x1=round(float(X[end]), 1), y1=round(float(Y[end]), 1), z1=round(float(max(zs[end], 0.0)), 1),
                       flight_s=round(float(ts[end]), 2), hit=int(cid[j]) if j >= 0 else -1, weight=round(float(weight), 2))
            debris.append(rec)
            if j >= 0:
                tb_ = int(cid[j])
                wwr = gl["window_to_wall"]["res" if occ_by.get(tb_, "RES").startswith("RES") else "com"]
                win[tb_] = win.get(tb_, 0.0) + weight * wwr
    usd_glass = {}
    for tb_, nwin in win.items():
        H_ = bh_by.get(tb_, 10.0)
        bi = int(np.nonzero(bid == tb_)[0][0]) if (bid == tb_).any() else None
        per = 4.0 * math.sqrt(max(area[bi], 1.0)) if bi is not None else 40.0
        cap_w = per * H_ * 0.25 * gl["window_to_wall"]["com"] / rd["window_hit_m2"]   # windward face glass
        nwin = min(nwin, cap_w)
        rate = gl["curtain_wall_usd_m2"]["usd_m2"] if H_ >= gl["curtain_wall_min_height_m"] else gl["storefront_usd_m2"]["usd_m2"]
        usd_glass[tb_] = nwin * rd["window_hit_m2"] * rate
        win[tb_] = nwin

    # ---------------- incidents: roads blocked by fallen objects / downed wire, trees into buildings
    street_names = None
    named = [tr for tr in trees if tr.get("street")]
    if named:
        from scipy.spatial import cKDTree
        st_tree = cKDTree(np.array([[tr["x"], tr["y"]] for tr in named]))
        street_names = lambda px, py: named[int(st_tree.query([px, py])[1])]["street"]
    road_blocks = []

    def lay(kind, i, k, x0, y0, bearing, L, tree_id=None):
        """Walk the fallen object's length from its base; report a road block and, for trees, the first building."""
        ux, uy = _unit(bearing)
        ss = np.arange(1.0, L + 0.01, 1.0)
        X, Y = x0 + ss * ux, y0 + ss * uy
        cid = city.sample(city.bldg_id, X, Y)
        own = city.sample(city.bldg_id, x0, y0)
        hit = np.nonzero((cid >= 0) & (cid != own))[0]
        stop = hit[0] if len(hit) else len(ss)
        on_road = city.sample(city.roadway, X[:stop], Y[:stop]).astype(bool)
        if on_road.sum() >= 3:
            j = np.nonzero(on_road)[0]
            road_blocks.append(dict(t=float(t[k]), cause=kind, id=int(i), x=round(float(X[j].mean()), 1), y=round(float(Y[j].mean()), 1),
                                    length_m=float(on_road.sum()), street=street_names(x0, y0) if street_names else None))

    for f, (p0, u, H, rc, d) in TG:
        bearing = math.degrees(math.atan2(u[0], u[1])) % 360
        lay("tree", f["id"], f["frame"], p0[0], p0[1], bearing, H)
    for i in np.nonzero(pole_k >= 0)[0]:
        lay("power_pole", i, pole_k[i], pp[i]["x"], pp[i]["y"], float(W.toward(pole_k[i])), P["power_pole"]["height_m"])
    for i in np.nonzero(l_k >= 0)[0]:
        lay("streetlight", i, l_k[i], lx[i], ly[i], float(W.toward(l_k[i])), float(lz[i]))
    for i in np.nonzero(m_k >= 0)[0]:
        lay("mast", i, m_k[i], mx[i], my[i], float(W.toward(m_k[i])), P["mast"]["height_m"])
    for i in np.nonzero(span_k >= 0)[0]:   # a downed conductor across the street: assume live, keep out
        mid = (SA[i] + SB[i]) / 2
        if city.sample(city.roadway, [mid[0]], [mid[1]])[0]:
            road_blocks.append(dict(t=float(t[span_k[i]]), cause="power_wire", id=int(i), x=round(float(mid[0]), 1), y=round(float(mid[1]), 1),
                                    length_m=float(sp[i]["length_m"]), street=street_names(*mid) if street_names else None))
    road_blocks.sort(key=lambda r: (r["t"], r["cause"], r["id"]))

    # outage cost: restoration starts after the storm and waits on blocked roads near each fault
    po = P["outage"]
    delay_h = np.zeros(len(lines))
    rbx = np.array([[r["x"], r["y"]] for r in road_blocks], float).reshape(-1, 2)
    for L in np.nonzero(line_k >= 0)[0]:
        f_ = [j for j in lines[L]["spans"] if span_k[j] >= 0]
        mids = (SA[f_] + SB[f_]) / 2
        if len(rbx) and len(mids):
            near = (np.hypot(rbx[:, None, 0] - mids[None, :, 0], rbx[:, None, 1] - mids[None, :, 1]) <= po["access_radius_m"]).any(1).sum()
            delay_h[L] = min(po["access_delay_cap_h"], po["access_delay_h_per_block"] * near)
    restore = np.where(out_cause == "service_drop", po["restore_h_span"],
                       np.where(line_pole[np.maximum(b_line, 0)], po["restore_h_pole"], po["restore_h_span"]) + delay_h[np.maximum(b_line, 0)]) if len(lines) else np.full(nb, po["restore_h_span"])
    dur_h = np.where(out_b, (t_end - t[np.maximum(out_k, 0)]) / 3600.0 + restore, 0.0)
    ec = _interp_event_cost(None, oc, dur_h)
    cls_cost = np.where(res, ec["residential"], np.where(fa < 300.0, ec["small_ci"], ec["medium_large_ci"]))
    outage_usd = float((cls_cost * cust * out_b).sum())
    customers_out = int((cust * out_b).sum())
    customer_hours = float((cust * dur_h * out_b).sum())
    for r in road_blocks:
        events.append(dict(t=r["t"], kind="road_block", id=r["id"], x=r["x"], y=r["y"], cause=r["cause"], street=r["street"]))
    for r in tree_strikes:
        events.append(dict(t=r["t"], kind="tree_strike", id=r["tree_id"], x=r["x"], y=r["y"], cause="tree", bldg_id=r["bldg_id"]))

    # ---------------- ledger
    def price(key, n):
        c = costs.get(key, {})
        return (float(c["usd"]) * n, c.get("source")) if c.get("usd") is not None else (0.0, None)

    n_pole, n_span = int((pole_k >= 0).sum()), int((span_k >= 0).sum())
    span_m = float(sum(sp[i]["length_m"] for i in np.nonzero(span_k >= 0)[0]))
    n_ocs = int((o_k >= 0).sum())
    n_light, n_shel, n_mast = int((l_k >= 0).sum()), int((sh_k >= 0).sum()), int((m_k >= 0).sum())
    n_head = sum(1 for s in sig_state if s["head_k"] >= 0 and s["struct_k"] < 0)
    n_struct = sum(1 for s in sig_state if s["struct_k"] >= 0)
    n_cab = sum(1 for s in sig_state if s["flooded"])
    by_cause, lines_out, unpriced = {}, [], []

    def add(cause, usd, basis, src):
        by_cause[cause] = round(float(usd), 2)
        lines_out.append({"cause": cause, "usd": round(float(usd), 2), "basis": basis, "source": src or "unpriced: no verified unit cost"})

    add("wind_roof_cover", usd_roof.sum(), f"{float(roof_lost.sum()):.0f} m2 roof cover lost (Hazus DS midpoints) x re-roof $/m2",
        costs["roof_cover_usd_m2"]["source"])
    add("wind_structure", usd_struct.sum(), "DS3 top storeys / DS4 whole building x structure loss ratio x assessor value", bp["source"][:160])
    add("wind_contents", usd_cont.sum(), "rain through failed roofs: contents loss ratio by DS", bp["source"][:160])
    add("debris_glazing", sum(usd_glass.values()), f"{sum(win.values()):.0f} windows broken by roof debris on {len(win)} buildings x glazing $/m2",
        gl["storefront_usd_m2"]["url"])
    for cause, key, n, what in (("power_poles", "power_pole_usd", n_pole, f"{n_pole} poles"),
                                ("power_spans", "power_span_usd", n_span, f"{n_span} spans ({span_m:.0f} m) down"),
                                ("ocs_wire", "ocs_span_usd", n_ocs, f"{n_ocs} trolley-wire spans down, {ocs_km_out:.1f} route-km de-energised"),
                                ("streetlights", "streetlight_usd", n_light, f"{n_light} streetlights down"),
                                ("signal_heads", "signal_head_usd", n_head, f"{n_head} signals with heads torn"),
                                ("signal_structures", "signal_structure_usd", n_struct, f"{n_struct} signal mast arms down"),
                                ("signal_cabinets", "signal_cabinet_usd", n_cab, f"{n_cab} flooded controller cabinets"),
                                ("shelters", "shelter_usd", n_shel, f"{n_shel} bus shelters smashed"),
                                ("masts", "mast_usd", n_mast, f"{n_mast} masts down")):
        usd, src = price(key, n)
        if src is None and n:
            unpriced.append(what)
        add(cause, usd, what, src)
        if src is None and n:
            lines_out[-1]["priced_usd"] = 0.0
            lines_out[-1]["usd"] = None
            by_cause[cause] = None
    car_usd = len(car_hit) * P["trees_df"]["car_partial_claim_usd"]
    add("falling_objects_cars", car_usd, f"{len(car_hit)} parked cars hit by falling poles/lights/masts x average claim",
        P["trees_df"]["car_partial_source"]["url"])
    add("power_outage", outage_usd, f"{customers_out} customers out, {customer_hours:.0f} customer-hours (LBNL ICE by class and duration)",
        oc["url"])
    missing_structure = [int(bid[i]) for i in range(nb) if wind_b[i] and ds[i] >= 3 and not buildings[i].get("value_structure")]
    missing_contents = [int(bid[i]) for i in range(nb) if wind_b[i] and ds[i] > 0 and not buildings[i].get("value_contents")]
    for cause, missing in (("wind_structure", missing_structure), ("wind_contents", missing_contents)):
        if missing:
            unpriced.append(f"{cause}: {len(missing)} buildings without assessor values")
            line = next(l for l in lines_out if l["cause"] == cause)
            line["unpriced_building_ids"] = missing
            line["priced_usd"] = line["usd"]
            line["usd"] = None
            by_cause[cause] = None
            line["basis"] += "; missing building values are unpriced"
    if unpriced:
        notes.append("counted but not priced (no verified unit cost yet): " + "; ".join(unpriced))
    by_bldg = {}
    for i in np.nonzero(usd_roof + usd_struct + usd_cont > 0)[0]:
        by_bldg[int(bid[i])] = round(float(usd_roof[i] + usd_struct[i] + usd_cont[i]), 2)
    for tb_, usd in usd_glass.items():
        by_bldg[tb_] = round(by_bldg.get(tb_, 0.0) + usd, 2)

    events.sort(key=lambda e: (e["t"], e["kind"], e["id"]))
    cum = {}
    for kind in ("tree", "building", "power_pole", "power_span", "ocs_span", "streetlight", "signal", "signal_dark", "shelter", "mast", "outage",
                 "road_block", "tree_strike"):
        ts_ = np.array([e["t"] for e in events if e["kind"] == kind])
        cum[kind] = np.searchsorted(np.sort(ts_), t, "right").tolist() if len(ts_) else [0] * nf
    cust_t = [int(sum(e.get("customers", 0) for e in events if e["kind"] == "outage" and e["t"] <= tt)) for tt in t]

    total = float(sum(l.get("priced_usd", l["usd"]) or 0.0 for l in lines_out))
    return {
        "storm": storm.name, "plan_hash": ph, "seed": str(seed), "wind_source": W.source, "t": t.tolist(),
        "summary": {
            "trees_failed": len(ft), "buildings_damaged": int((ds > 0).sum()),
            "buildings_by_ds": {f"DS{j}": int((ds == j).sum()) for j in range(1, 5)},
            "power_poles_down": n_pole, "power_spans_down": n_span, "power_lines_out": int((line_k >= 0).sum()),
            "customers_out": customers_out, "customer_hours": round(customer_hours, 1),
            "ocs_spans_down": n_ocs, "ocs_route_km_out": round(ocs_km_out, 2),
            "streetlights_down": n_light, "streetlights_dark": int(((l_dark >= 0) | l_flood).sum()),
            "signals_damaged": n_head + n_struct, "signals_dark": int(sum(1 for s in sig_state if s["dark_k"] >= 0 or s["flooded"])),
            "shelters_smashed": n_shel, "masts_down": n_mast, "debris_particles": len(debris),
            "windows_broken_by_debris": round(float(sum(win.values())), 1), "cars_hit_by_falling_objects": len(car_hit),
            "roads_blocked": len(road_blocks), "trees_into_buildings": len(tree_strikes),
            "service_drops_torn": int((out_cause == "service_drop").sum()),
            "power_spans_down_by_collapse": int((span_cause == "collapse").sum()),
            "inventory": {k: len(assets[k]) for k in ("power_poles", "power_spans", "ocs_spans", "streetlights", "signals", "shelters", "masts")}
                         | {"buildings": nb, "trees": len(trees)},
        },
        "ledger": {"total_usd": round(total, 2), "by_cause": by_cause, "by_building": by_bldg, "lines": lines_out,
                   "sources": sorted({l["source"] for l in lines_out}), "unpriced": unpriced},
        "trees": ft,
        "buildings": [{"id": int(bid[i]), "ds": int(ds[i]), "cause": ds_cause[i], "ds_t": [float(t[k]) if k >= 0 else None for k in ds_k[i]],
                       "roof_lost_m2": round(float(roof_lost[i]), 1)} for i in np.nonzero(ds > 0)[0]],
        "power_poles": [{"id": int(i), "t": float(t[pole_k[i]]), "cause": pole_cause[i], "fall_dir_deg": round(float(W.toward(pole_k[i])), 1)}
                        for i in np.nonzero(pole_k >= 0)[0]],
        "power_spans": [{"id": int(i), "t": float(t[span_k[i]]), "cause": span_cause[i], "line_id": int(span_line[i]),
                         "tree_id": int(s_tree_id[i]) if s_tree_k[i] >= 0 else None} for i in np.nonzero(span_k >= 0)[0]],
        "power_lines_out": [{"id": int(L), "t": float(t[line_k[L]]), "customers": int(cust[out_b & (b_line == L)].sum())}
                            for L in np.nonzero(line_k >= 0)[0]],
        "ocs_spans": [{"id": int(i), "t": float(t[o_k[i]]), "tree_id": int(o_by[i]), "street": ocs[i]["street"]} for i in np.nonzero(o_k >= 0)[0]],
        "ocs_out": [{"id": int(i), "t": float(t[o_out[i]])} for i in np.nonzero(o_out >= 0)[0]],
        "streetlights": [{"id": int(i), "t": float(t[l_k[i]]) if l_k[i] >= 0 else None, "cause": l_cause[i] or None,
                          "dark_t": float(t[l_dark[i]]) if l_dark[i] >= 0 else None, "flooded": bool(l_flood[i]),
                          "flood_t": float(t[l_flood_k[i]]) if l_flood_k[i] >= 0 else None}
                         for i in range(len(lights)) if l_k[i] >= 0 or l_dark[i] >= 0 or l_flood[i]],
        "signals": [dict(id=i, head_t=float(t[s["head_k"]]) if s["head_k"] >= 0 else None,
                         struct_t=float(t[s["struct_k"]]) if s["struct_k"] >= 0 else None,
                         dark_t=float(t[s["dark_k"]]) if s["dark_k"] >= 0 else None, flooded=s["flooded"],
                         flood_t=float(t[s["flood_k"]]) if s["flood_k"] >= 0 else None)
                    for i, s in enumerate(sig_state) if s["head_k"] >= 0 or s["struct_k"] >= 0 or s["dark_k"] >= 0 or s["flooded"]],
        "shelters": [{"id": int(i), "t": float(t[sh_k[i]])} for i in np.nonzero(sh_k >= 0)[0]],
        "masts": [{"id": int(i), "t": float(t[m_k[i]])} for i in np.nonzero(m_k >= 0)[0]],
        "cars_hit": [{"id": c, "t": float(t[v[0]]), "by": v[1], "by_id": v[2]} for c, v in sorted(car_hit.items())],
        "debris": debris,
        "road_blocks": road_blocks,
        "tree_strikes": tree_strikes,
        "outage": {"customers_out": customers_out, "customer_hours": round(customer_hours, 1), "customers_t": cust_t,
                   "buildings_out": [int(bid[i]) for i in np.nonzero(out_b)[0]],
                   "buildings_out_t": [[int(bid[i]), float(t[out_k[i]])] for i in np.nonzero(out_b)[0]],
                   "service_drops_torn": int((out_cause == "service_drop").sum()),
                   "restoration_delay_h": {int(L): float(delay_h[L]) for L in np.nonzero(delay_h > 0)[0]}},
        "timeline": cum,
        "events": events,
        "notes": notes,
    }


class WaterSummary:
    """What the destruction engine and trees.py need from a sim/water.py run, small enough to cache on disk."""

    def __init__(self, d):
        for k, v in d.items():
            setattr(self, k, v)


def water_inputs(storm, verbose=False):
    """Summarise saved host depth frames; this application does not save velocity."""
    depth = np.asarray(storm.depth_frames)
    return WaterSummary(dict(t_frames=storm.t, max_depth=depth.max(0),
                             k_max_depth=depth.argmax(0),
                             k_flood_peak=int(np.argmax(depth.sum(axis=(1, 2))))))


def physics_inputs(storm, fast=True, water=True, verbose=False):
    """Use only the host potential-flow field and calibrated fallback trees."""
    return storm.wind, None, None, water_inputs(storm) if water else None


def merge_ledger(ledger, result):
    """Add the destruction ledger to a sim/damage.py ledger (in place) and return it. No double counting:
    damage.py prices tree strikes on cars/buildings, flood depth-damage and pressure glazing; this adds what it
    does not see (structural wind damage, debris glazing, infrastructure, outages, falling objects)."""
    d = result["ledger"]
    for k, v in d["by_cause"].items():
        ledger["by_cause"]["destruction_" + k] = v
    for bid, usd in d["by_building"].items():
        ledger["by_building"][int(bid)] = round(ledger["by_building"].get(int(bid), 0.0) + usd, 2)
    ledger["lines"].extend(dict(l, cause="destruction_" + l["cause"]) for l in d["lines"])
    ledger["total_usd"] = round(ledger["total_usd"] + d["total_usd"], 2)
    ledger["sources"] = sorted(set(ledger.get("sources", [])) | set(d["sources"]))
    ledger.setdefault("destruction", result["summary"])
    return ledger


def write_web(result, out_dir):
    """runs/<id>/frames/destruction.json for the viewer (geometry comes from /data/grid/assets.json)."""
    os.makedirs(out_dir, exist_ok=True)
    keep = ("storm", "t", "summary", "trees", "buildings", "power_poles", "power_spans", "power_lines_out", "ocs_spans",
            "ocs_out", "streetlights", "signals", "shelters", "masts", "cars_hit", "debris", "timeline")
    web = {k: result[k] for k in keep}
    web["outage"] = {k: result["outage"][k] for k in ("customers_out", "customer_hours", "customers_t", "buildings_out")}
    web["ledger"] = {"total_usd": result["ledger"]["total_usd"], "by_cause": result["ledger"]["by_cause"]}
    p = os.path.join(out_dir, "destruction.json")
    with open(p, "w") as fh:
        json.dump(web, fh, separators=(",", ":"))
    return p


def scaled_storm(storm, gust_scale):
    """What-if copy of a storm with gusts x gust_scale (name gets a suffix so the seed and outputs differ)."""
    import copy
    st = copy.copy(storm)
    st.gust_ms = np.asarray(storm.gust_ms, float) * gust_scale
    st.mean_ms = np.asarray(storm.mean_ms, float) * gust_scale
    st.name = f"{storm.name}_x{gust_scale:g}"
    return st


def result_hash(result):
    return hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()


