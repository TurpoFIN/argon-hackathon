"""StormSim SF: street-tree failure, wind-borne debris, glazing failure and flood-driven cars.

All parameters (with sources) live in data/reference/wind_tree_params.json.

Trees (per real street tree, quasi-static, after GALES: Gardiner et al. 2000 Ecol. Model. 129:1;
Peltola et al. 1999 Can. J. For. Res. 29:647)
* Local wind at the crown centroid: U(t) = gust(t) x A(x, y, h_c), A = LBM amplification (sim/wind.py).
* Crown drag with reconfiguration (Vogel 1989; de Langre 2008):
      F = 1/2 rho Cd0 A_crown U^2 (U/U0)^-b      (U > U0),   A_crown = pi/4 * crown_d * 0.6 H
* Snap:   F (h_c - 1.3) > MOR * pi d^3/32 * eta,   eta = 1 - (r/R)^4 for trees with decay (Kane & Ryan 2004)
* Load factor 1.3 = dynamic gust/sway amplification (Gardiner et al. 2008).
* Uproot: F h_c > Creg * stem_mass * (1 - 0.4 s(t)), Creg = 160 N m/kg (inside Nicoll's 100-200 range,
  checked on the 31.12.2022 replay; see calibration_check in the params file). Saplings (DBH < 10 cm) excluded.   (Nicoll et al. 2006; Kamimura et al. 2012),
  s(t) = soil saturation at the tree cell from sim/water.py.
* Cross-check (Virot et al. 2016 PRE 93:023001): critical snapping speed of sound trees ~42 m/s,
  nearly size independent; `virot_check()` reports the inventory distribution (ours: median 78 m/s, i.e. our
  stem-strength model is conservative and failures are dominated by uprooting on wet soil / decayed stems).
* Failure time: first crossing of M/M_crit = 1 (linear in time between frames). Fall direction = local
  wind direction + N(0, 15 deg) (seeded from storm name + plan hash). Fall: rigid rod pivoting at the base
  (uproot) or at 1.3 m (snap) under gravity + residual wind torque -> angle keyframes (~2-4 s).
* Strikes: fall line vs buildings (4 m bldg_id grid; tree stops at the wall) and parked cars (lateral
  distance < crown radius).

Debris (wind-borne, rigid particles with quadratic drag + gravity + ground bounce, advected by the
LBM field scaled by the gust, plus Ornstein-Uhlenbeck turbulence, intensity 0.2):
* leaves/twigs from crowns above Beaufort 6-10 (mean crown wind), branches above Beaufort 9,
* glass from buildings where |Cp| q_gust > glazing capacity (ASTM E1300; ASCE 7 quasi-steady).
* Leaf litter landing within 5 m of a storm inlet -> per-inlet clog fraction (for sim/water.py
  `clog_factor`, which takes the *remaining* capacity = 1 - clog).

Cars in floods (Shu et al. 2011 J. Hydraul. Res. 49:709; Xia et al. 2014; Martinez-Gomariz et al. 2018):
buoyancy with partial sealing vs weight -> float depth ~0.35-0.5 m; sliding when side drag > mu (W - B);
unstable cars drift with 0.8 x the depth-averaged water velocity and stop at buildings / shallow water.

Determinism: tree defects are per-tree hashes of the tree id (a property of the city); everything storm- or
plan-dependent uses np.random.default_rng(seed(storm_name, plan_hash)); no parallel reductions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numba import njit

ROOT = Path(__file__).resolve().parents[1]
GRID_DIR = ROOT / "data" / "grid"
PARAMS_PATH = ROOT / "data" / "reference" / "wind_tree_params.json"
G = 9.81
RHO_W = 1000.0


def load_params(path=PARAMS_PATH) -> dict:
    d = json.loads(Path(path).read_text())["params"]
    return {k: float(v["value"]) for k, v in d.items()}


def param_sources(path=PARAMS_PATH) -> dict:
    d = json.loads(Path(path).read_text())["params"]
    return {k: v["source"] for k, v in d.items()}


def make_seed(storm_name: str, plan_hash: str = "", salt: str = "") -> int:
    h = hashlib.sha256(f"{storm_name}|{plan_hash}|{salt}".encode()).digest()
    return int.from_bytes(h[:8], "little")


def _hash_u01(ids, salt: int):
    """Deterministic per-id uniforms in [0,1) (splitmix64)."""
    with np.errstate(over="ignore"):
        x = np.asarray(ids, np.uint64) * np.uint64(0x9E3779B97F4A7C15) + np.uint64(salt)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


# ----------------------------------------------------------------------------
# tree mechanics
# ----------------------------------------------------------------------------
@dataclass
class TreeArrays:
    ids: np.ndarray
    x: np.ndarray
    y: np.ndarray
    H: np.ndarray
    D: np.ndarray        # dbh, m
    crown: np.ndarray    # crown diameter, m
    hc: np.ndarray       # crown centroid height
    area: np.ndarray     # crown frontal area m2
    m_snap: np.ndarray   # N m (about 1.3 m)
    m_anchor_dry: np.ndarray  # N m (about base)
    mass: np.ndarray     # stem mass kg
    eta: np.ndarray      # decay strength factor


def tree_arrays(trees: list, P: dict) -> TreeArrays:
    n = len(trees)
    g = lambda k, d=0.0: np.array([float(t.get(k) if t.get(k) is not None else d) for t in trees])
    ids = np.array([int(t["id"]) for t in trees], np.int64)
    H = np.maximum(g("height_m", 6.0), 2.0)
    D = np.maximum(g("dbh_cm", 15.0), 3.0) / 100.0
    crown = np.maximum(g("crown_m", 3.0), 0.5)
    mor = g("wood_mor_mpa", 40.0) * 1e6
    pruned = np.array([bool(t.get("pruned")) for t in trees])
    braced = np.array([bool(t.get("braced")) for t in trees])
    L = P["tree_crown_length_frac"] * H
    hc = H - L / 2
    area = np.pi / 4 * crown * L * np.where(pruned, P["prune_area_factor"], 1.0)
    # decay: per-tree property hashed from the id (stable across storms and plans)
    u1, u2 = _hash_u01(ids, 11), _hash_u01(ids, 23)
    decayed = u1 < P["snap_decay_prevalence"]
    rr = 0.3 + (P["snap_decay_rr_max"] - 0.3) * u2
    eta = np.where(decayed, 1.0 - rr ** 4, 1.0) * np.where(braced, P["brace_strength_factor"], 1.0)
    m_snap = mor * np.pi * D ** 3 / 32 * eta
    # saplings (DBH < min) are staked/flexible: they bend or lean rather than snap/uproot -> not failed here
    sap = D < P.get("tree_min_dbh_m", 0.0)
    mass = P["wood_density_green"] * P["stem_form_factor"] * np.pi / 4 * D ** 2 * H
    m_anchor = P["uproot_creg_nm_per_kg"] * mass * P["uproot_calib_factor"]
    m_snap = np.where(sap, np.inf, m_snap)
    m_anchor = np.where(sap, np.inf, m_anchor)
    return TreeArrays(ids, g("x"), g("y"), H, D, crown, hc, area, m_snap, m_anchor, mass, eta)


def drag_force(U, area, P):
    """Crown drag (N) with Vogel reconfiguration."""
    U = np.asarray(U, float)
    u0, b = P["tree_vogel_u0_ms"], P["tree_vogel_exponent"]
    recon = np.where(U > u0, (np.maximum(U, 1e-6) / u0) ** (-b), 1.0)
    return 0.5 * P["rho_air"] * P["tree_cd0"] * P.get("tree_load_factor", 1.0) * area * U ** 2 * recon


def critical_speed(moment_cap, lever, area, P):
    """Crown wind speed (m/s) at which drag moment reaches moment_cap (bisection, vectorised)."""
    lo = np.full(np.shape(area), 0.1)
    hi = np.full(np.shape(area), 400.0)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        ok = drag_force(mid, area, P) * lever < moment_cap
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return 0.5 * (lo + hi)


def virot_check(trees=None, P=None) -> dict:
    """Distribution of critical snapping speed for sound (eta=1) trees: Virot et al. 2016 -> ~42 m/s."""
    P = P or load_params()
    trees = trees if trees is not None else json.loads((GRID_DIR / "trees.json").read_text())
    ta = tree_arrays(trees, P)
    big = ta.D >= 0.10
    sound = ta.m_snap / ta.eta
    uc = critical_speed(sound[big], np.maximum(ta.hc - 1.3, 0.5)[big], ta.area[big], P)
    ucr = critical_speed(ta.m_anchor_dry[big], ta.hc[big], ta.area[big], P)
    q = lambda a: [round(float(v), 1) for v in np.percentile(a, [10, 50, 90])]
    # size dependence: correlation of Uc with height
    r = float(np.corrcoef(np.log(ta.H[big]), np.log(uc))[0, 1])
    return {"n": int(big.sum()), "snap_ucrit_p10_50_90": q(uc), "uproot_dry_ucrit_p10_50_90": q(ucr),
            "corr_log_uc_log_h": round(r, 3), "virot_2016_ms": 42.0}


# ----------------------------------------------------------------------------
# fall dynamics
# ----------------------------------------------------------------------------
def fall_keyframes(H, pivot_z, F0, lever, mass, theta_stop=np.pi / 2, dt=0.01, every=0.1, residual=True,
                   theta_r=0.5, U=None):
    """Rigid rod pivoting about its base:
        I th'' = m g l_cm sin th + F lever cos^2 th (1 - w l cos th / U)^2 - M_res(th),
    M_res = residual root-plate resistance after the peak, decaying linearly to 0 at theta_r = 0.5 rad
    (turning-moment vs rotation curves of tree-pulling tests, Coutts 1986 Forestry 59:173; Nicoll et al. 2006).
    For uprooting M_res(0) = F0 lever (failure point); snapped stems have none.
    Returns [[t_s, angle_deg_from_vertical], ...] until theta_stop (ground or wall)."""
    Lr = max(H - pivot_z, 1.0)
    m = mass * 1.3 + 1.0               # stem + crown/branches (~30 %)
    I = m * Lr ** 2 / 3.0
    th, om, t = 0.05, 0.0, 0.0
    out = [[0.0, round(np.degrees(th), 2)]]
    nxt = every
    while th < theta_stop and t < 8.0:
        mw = F0 * min(lever, Lr)
        if U:
            # drag acts on the wind speed relative to the moving crown (aerodynamic damping)
            vrel = max(U - om * min(lever, Lr) * np.cos(th), 0.0) / U
            mw *= vrel * vrel
        mres = mw * max(0.0, 1.0 - th / theta_r) if residual else 0.0
        a = (m * G * Lr / 2 * np.sin(th) + mw * np.cos(th) ** 2 - mres) / I
        om += a * dt
        th += om * dt
        t += dt
        if t >= nxt - 1e-9:
            out.append([round(t, 3), round(float(np.degrees(min(th, theta_stop))), 2)])
            nxt += every
    if out[-1][1] < round(float(np.degrees(theta_stop)), 2):
        out.append([round(t, 3), round(float(np.degrees(theta_stop)), 2)])
    return out, float(om * Lr)


# ----------------------------------------------------------------------------
# particle integrator (numba, serial per particle -> deterministic)
# ----------------------------------------------------------------------------
@njit(cache=True, fastmath=False)
def _wind_vec(u, v, w, solid, ground8, x0, y0, dx, zb, x, y, zagl):
    nz, ny, nx = u.shape
    fx = min(max((x - x0) / dx - 0.5, 0.0), nx - 1.001)
    fy = min(max((y - y0) / dx - 0.5, 0.0), ny - 1.001)
    i0 = int(fx)
    j0 = int(fy)
    tx = fx - i0
    ty = fy - j0
    g = ((1 - tx) * (1 - ty) * ground8[j0, i0] + tx * (1 - ty) * ground8[j0, i0 + 1]
         + (1 - tx) * ty * ground8[j0 + 1, i0] + tx * ty * ground8[j0 + 1, i0 + 1])
    fz = min(max((g + zagl - zb) / dx - 0.5, 0.0), nz - 1.001)
    k0 = int(fz)
    tz = fz - k0
    su = 0.0
    sv = 0.0
    sw = 0.0
    ws = 0.0
    for dk in range(2):
        for dj in range(2):
            for di in range(2):
                wt = (tx if di else 1 - tx) * (ty if dj else 1 - ty) * (tz if dk else 1 - tz)
                kk = k0 + dk
                jj = j0 + dj
                ii = i0 + di
                while kk < nz - 1 and solid[kk, jj, ii] != 0 and kk <= k0 + 2:
                    kk += 1
                if solid[kk, jj, ii] != 0:
                    continue
                su += wt * u[kk, jj, ii]
                sv += wt * v[kk, jj, ii]
                sw += wt * w[kk, jj, ii]
                ws += wt
    if ws < 1e-6:
        return 0.0, 0.0, 0.0
    return su / ws, sv / ws, sw / ws


@njit(cache=True, fastmath=False)
def _fly(u, v, w, solid, ground8, x0, y0, dx, zb, surf4, x04, y04, dx4,
         pos0, gust, kdrag, vterm, rest, noise, dt, tmax, traj, traj_every, mu):
    """Integrate each particle from release to rest. noise: (n, nsteps_max, 3) N(0,1) draws.
    Returns final positions (n,3) and flight time; fills traj (n, ntraj, 4) [t, x, y, z]."""
    n = pos0.shape[0]
    ny4, nx4 = surf4.shape
    out = np.empty((n, 3))
    tf = np.zeros(n)
    nsteps = noise.shape[1]
    ntraj = traj.shape[1]
    for p in range(n):
        x = pos0[p, 0]
        y = pos0[p, 1]
        z = pos0[p, 2]
        vx = 0.0
        vy = 0.0
        vz = 0.0
        tu = 0.0
        tv = 0.0
        tw = 0.0
        t = 0.0
        it = 0
        kt = 0
        for s in range(nsteps):
            ii = min(max(int((x - x04) / dx4), 0), nx4 - 1)
            jj = min(max(int((y - y04) / dx4), 0), ny4 - 1)
            zs = surf4[jj, ii]
            a, b, c = _wind_vec(u, v, w, solid, ground8, x0, y0, dx, zb, x, y, max(z - zs, 0.5))
            U = gust[p]
            # OU turbulence, intensity 0.2, integral time 2 s
            sig = 0.2 * U * np.sqrt(a * a + b * b + 0.01)
            al = dt / 2.0
            tu += -al * tu + sig * np.sqrt(2 * al) * noise[p, s, 0]
            tv += -al * tv + sig * np.sqrt(2 * al) * noise[p, s, 1]
            tw += -al * tw + 0.5 * sig * np.sqrt(2 * al) * noise[p, s, 2]
            wx = a * U + tu
            wy = b * U + tv
            wz = c * U + tw
            rx = wx - vx
            ry = wy - vy
            rz = wz - vz
            rm = np.sqrt(rx * rx + ry * ry + rz * rz)
            if vterm[p] > 0:
                k = 9.81 / (vterm[p] * vterm[p])
            else:
                k = kdrag[p]
            # semi-implicit drag (unconditionally stable for light leaves)
            den = 1.0 + dt * k * rm
            vx = (vx + dt * k * rm * wx) / den
            vy = (vy + dt * k * rm * wy) / den
            vz = (vz + dt * (k * rm * wz - 9.81)) / den
            nxp = x + vx * dt
            nyp = y + vy * dt
            nzp = z + vz * dt
            ii2 = min(max(int((nxp - x04) / dx4), 0), nx4 - 1)
            jj2 = min(max(int((nyp - y04) / dx4), 0), ny4 - 1)
            zs2 = surf4[jj2, ii2]
            if nzp < zs2:
                if zs2 > zs + 1.0 and z > zs + 0.0 and z < zs2:
                    # hit a wall: lose horizontal momentum, slide down
                    vx = -rest[p] * vx
                    vy = -rest[p] * vy
                    nxp = x
                    nyp = y
                    nzp = max(z + vz * dt, zs)
                    if nzp <= zs + 1e-3:
                        nzp = zs
                else:
                    nzp = zs2
                    if vterm[p] > 0:
                        # wet leaves stick to wet pavement / roofs at first contact
                        vx = 0.0
                        vy = 0.0
                        vz = 0.0
                    elif abs(vz) > 1.0 and rest[p] > 0:
                        vz = -rest[p] * vz
                        vx *= 0.5
                        vy *= 0.5
                    else:
                        vz = 0.0
                        # Coulomb friction: slides only while horizontal drag exceeds mu g
                        ah = k * rm * np.sqrt(rx * rx + ry * ry)
                        vh = np.sqrt(vx * vx + vy * vy)
                        if ah < mu * 9.81:
                            vx = 0.0
                            vy = 0.0
                        elif vh > 0:
                            red = max(0.0, 1.0 - mu * 9.81 * dt / vh)
                            vx *= red
                            vy *= red
            x = nxp
            y = nyp
            z = nzp
            t += dt
            if it % traj_every == 0 and kt < ntraj:
                traj[p, kt, 0] = t
                traj[p, kt, 1] = x
                traj[p, kt, 2] = y
                traj[p, kt, 3] = z
                kt += 1
            it += 1
            ii3 = min(max(int((x - x04) / dx4), 0), nx4 - 1)
            jj3 = min(max(int((y - y04) / dx4), 0), ny4 - 1)
            if z <= surf4[jj3, ii3] + 1e-3 and vx * vx + vy * vy < 0.01 and vz == 0.0:
                break
            if t > tmax:
                break
        for r in range(kt, ntraj):
            traj[p, r, 0] = t
            traj[p, r, 1] = x
            traj[p, r, 2] = y
            traj[p, r, 3] = z
        out[p, 0] = x
        out[p, 1] = y
        out[p, 2] = z
        tf[p] = t
    return out, tf


# ----------------------------------------------------------------------------
# main entry
# ----------------------------------------------------------------------------
@dataclass
class TreeResult:
    trees: list                      # web trees.json records (+ extras)
    strikes: list                    # [{tree_id, kind: car|building, target_id, t_s, mass_kg, tip_speed_ms}]
    glazing: list                    # [{bldg_id, t_s, p_peak_pa, capacity_pa, area_failed_m2, panes_est}]
    debris_frames: np.ndarray        # (n_frames, n_debris, 3) float32
    debris_meta: dict
    debris_flight: np.ndarray        # (n_debris, n_key, 4) [t_rel, x, y, z]
    inlet_clog: dict                 # {inlet_id: {clog, remaining, t_first_s, leaf_area_m2}}
    summary: dict


def _sample_frames(frames, ix, iy):
    """frames (n, ny, nx) -> (n, len(ix))."""
    return frames[:, iy, ix]


def synthetic_saturation(storm, n_trees, s0=0.4, D_mm=60.0):
    """Placeholder until sim/water.py frames are wired: s = s0 + (1-s0) * P/(P + D)."""
    dt = np.diff(np.concatenate([[0.0], storm.t]))
    P = np.cumsum(storm.rain_mm_h * dt / 3600.0)
    s = s0 + (1 - s0) * P / (P + D_mm)
    return np.repeat(s[:, None], n_trees, 1)


def _crossing_time(t, r):
    """First time r(t) >= 1 (linear between frames); None if never."""
    idx = np.nonzero(r >= 1.0)[0]
    if idx.size == 0:
        return None
    k = int(idx[0])
    if k == 0:
        return float(t[0])
    a, b = r[k - 1], r[k]
    f = (1.0 - a) / max(b - a, 1e-12)
    return float(t[k - 1] + f * (t[k] - t[k - 1]))


def run_trees(storm, wind, grid=None, trees=None, cars=None, buildings=None, inlets=None,
              saturation=None, plan_hash: str = "", n_debris_export: int = 4000, n_stat_leaves: int = 20000,
              P: dict | None = None, verbose=True) -> TreeResult:
    """Tree failures, strikes, glazing failures, debris and inlet clogging for one storm.

    storm: sim.storm.Storm; wind: sim.wind.WindField (for storm.wind_dir_deg).
    saturation: (n_frames, ny4, nx4) soil saturation frames from sim/water.py, or (n_frames, n_trees), or None
    (synthetic from cumulative rain). trees/cars/buildings/inlets default to data/grid/*.json (plans may pass
    edited lists: removed trees absent; `pruned`/`braced` flags honoured).
    """
    P = P or load_params()
    rd = lambda n: json.loads((GRID_DIR / n).read_text())
    trees = trees if trees is not None else rd("trees.json")
    cars = cars if cars is not None else rd("cars.json")
    buildings = buildings if buildings is not None else rd("buildings.json")
    inlets = inlets if inlets is not None else rd("inlets.json")
    if grid is None:
        from sim.wind import load_grid
        grid = load_grid()
    meta = grid["meta"]
    x04, y04, dx4 = float(meta["x0"]), float(meta["y0"]), float(meta["dx"])
    bid4 = grid["bldg_id"]
    surf4 = (grid["z"] + grid["bldg_h"]).astype(np.float64)
    if "cover" in grid:
        surf4 = np.where(grid["cover"] == 3, np.maximum(surf4, 0.0), surf4)
    ny4, nx4 = bid4.shape
    t = np.asarray(storm.t, float)
    nfr = t.size
    gust = np.asarray(storm.gust_ms, float)
    mean = np.asarray(storm.mean_ms, float) * np.ones(nfr)
    seed = make_seed(storm.name, plan_hash)
    rng = np.random.default_rng(seed)

    ta = tree_arrays(trees, P)
    n = ta.ids.size
    ix = np.clip(((ta.x - x04) // dx4).astype(int), 0, nx4 - 1)
    iy = np.clip(((ta.y - y04) // dx4).astype(int), 0, ny4 - 1)
    vec = wind.vec_at(ta.x, ta.y, ta.hc)
    amp = np.hypot(vec[:, 0], vec[:, 1])
    if saturation is None:
        sat = synthetic_saturation(storm, n)
    else:
        sat = np.asarray(saturation, np.float32)
        sat = sat if sat.ndim == 2 else _sample_frames(sat, ix, iy)
        if sat.shape[0] != nfr:   # resample in time
            ts = np.linspace(t[0], t[-1], sat.shape[0])
            sat = np.stack([np.interp(t, ts, sat[:, i]) for i in range(n)], 1)
    U = gust[:, None] * amp[None, :]                       # (nfr, n) crown gust speed
    F = drag_force(U, ta.area[None, :], P)
    r_snap = F * np.maximum(ta.hc - 1.3, 0.5)[None] / ta.m_snap[None]
    m_anchor = ta.m_anchor_dry[None] * (1.0 - P["uproot_sat_reduction"] * np.clip(sat, 0, 1))
    r_up = F * ta.hc[None] / m_anchor

    # ---- failures
    fall_noise = rng.normal(0.0, P["fall_noise_deg"], n)
    car_xy = np.array([[c["x"], c["y"]] for c in cars]) if cars else np.zeros((0, 2))
    from scipy.spatial import cKDTree
    ctree = cKDTree(car_xy) if len(car_xy) else None
    out_trees, strikes = [], []
    for i in range(n):
        ts_ = _crossing_time(t, r_snap[:, i])
        tu_ = _crossing_time(t, r_up[:, i])
        rec = {"id": int(ta.ids[i]), "fail_t": None, "mode": None, "fall_dir_deg": None}
        if ts_ is None and tu_ is None:
            out_trees.append(rec)
            continue
        if tu_ is not None and (ts_ is None or tu_ <= ts_):
            mode, tf = "uproot", tu_
        else:
            mode, tf = "snap", ts_
        k = min(int(np.searchsorted(t, tf)), nfr - 1)
        # fall toward where the wind blows (compass bearing, deg clockwise from north)
        bearing = (np.degrees(np.arctan2(vec[i, 0], vec[i, 1])) + fall_noise[i]) % 360.0
        pivot = 1.3 if mode == "snap" else 0.0
        reach = ta.H[i] - pivot
        ex, ey = np.sin(np.radians(bearing)), np.cos(np.radians(bearing))
        # building hit along the fall line
        d_hit, b_hit = None, None
        for d in np.arange(1.0, reach + 0.01, 1.0):
            jj = int((ta.y[i] + ey * d - y04) // dx4)
            ii = int((ta.x[i] + ex * d - x04) // dx4)
            if 0 <= jj < ny4 and 0 <= ii < nx4 and bid4[jj, ii] >= 0 and bid4[jj, ii] != bid4[iy[i], ix[i]]:
                d_hit, b_hit = float(d), int(bid4[jj, ii])
                break
        th_stop = np.pi / 2 if d_hit is None else float(np.arcsin(min(d_hit / reach, 1.0)))
        kf, tip = fall_keyframes(ta.H[i], pivot, float(F[k, i]), float(ta.hc[i] - pivot), float(ta.mass[i]),
                                 theta_stop=max(th_stop, 0.2), residual=(mode == "uproot"), U=float(U[k, i]))
        rec.update({"fail_t": round(tf, 1), "mode": mode, "fall_dir_deg": round(float(bearing), 1),
                    "pivot_z": pivot, "keyframes": kf, "u_crown_ms": round(float(U[k, i]), 1),
                    "sat": round(float(sat[k, i]), 2)})
        out_trees.append(rec)
        t_imp = tf + kf[-1][0]
        if b_hit is not None:
            strikes.append({"tree_id": int(ta.ids[i]), "kind": "building", "target_id": b_hit, "t_s": round(t_imp, 1),
                            "mass_kg": round(float(ta.mass[i] * 1.3), 0), "tip_speed_ms": round(tip, 1)})
        if ctree is not None:
            L = d_hit if d_hit is not None else reach
            mid = np.array([ta.x[i] + ex * L / 2, ta.y[i] + ey * L / 2])
            for c in ctree.query_ball_point(mid, L / 2 + ta.crown[i] / 2 + 1.0):
                rel = car_xy[c] - np.array([ta.x[i], ta.y[i]])
                along = rel[0] * ex + rel[1] * ey
                lat = abs(-rel[0] * ey + rel[1] * ex)
                if 0.5 < along <= L + 1.0 and lat <= max(1.0, ta.crown[i] / 2) + 0.9:
                    strikes.append({"tree_id": int(ta.ids[i]), "kind": "car", "target_id": int(cars[c]["id"]),
                                    "t_s": round(t_imp, 1), "mass_kg": round(float(ta.mass[i] * 1.3), 0),
                                    "tip_speed_ms": round(tip, 1)})

    # ---- glazing failures (quasi-steady: |Cp| q_gust vs capacity)
    q = P["velocity_pressure"] * gust ** 2
    glazing, glass_src = [], []
    for b in buildings:
        f = wind.facade.get(int(b["id"]))
        if not f:
            continue
        yb = b.get("year_built") or 1950
        cap = 1000.0 * (P["glass_capacity_pre1980_kpa"] if (yb or 0) < 1980 else P["glass_capacity_post1980_kpa"])
        pk = abs(f["cp_peak"]) * q
        kk = np.nonzero(pk > cap)[0]
        if kk.size == 0:
            continue
        cpq = np.asarray(f["cp_abs_q"])
        thr = cap / q.max()
        frac = float(np.interp(thr, cpq, 1.0 - np.asarray((0.0, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0))))
        area = max(frac * f["n_cells"] * f["cell_area_m2"], 2.0)
        glazing.append({"bldg_id": int(b["id"]), "t_s": float(t[kk[0]]), "p_peak_pa": round(float(pk.max()), 0),
                        "capacity_pa": cap, "area_failed_m2": round(area, 1), "panes_est": int(np.ceil(area / 2.0))})
        glass_src.append((float(t[kk[0]]), f["x_peak"], f["y_peak"], f["z_peak"], int(b["id"]), area))

    # ---- debris sources
    srcs = []   # (kind, t_rel, x, y, z, src_id, kdrag, vterm, rest, leaf_area)
    # leaves/twigs: shedding fraction rises from Beaufort 6 (10.8 m/s) to Beaufort 10 (24.5 m/s), mean crown wind
    Um = mean[:, None] * amp[None, :]
    fshed = P["leaf_shed_frac_max"] * np.clip((Um - 10.8) / (24.5 - 10.8), 0, 1)
    fshed = np.maximum.accumulate(fshed, axis=0)
    leaf_tot = P["leaf_area_index"] * np.pi / 4 * ta.crown ** 2 * fshed[-1]
    total_leaf = float(leaf_tot.sum())
    if total_leaf > 0:
        share = leaf_tot / total_leaf * n_stat_leaves
        cnt = np.floor(share).astype(int)
        rem = n_stat_leaves - cnt.sum()
        if rem > 0:
            cnt[np.argsort(-(share - cnt), kind="stable")[:rem]] += 1
        for i in np.nonzero(cnt)[0]:
            # release times: inverse CDF of this tree's shedding curve
            cdf = fshed[:, i] / max(fshed[-1, i], 1e-12)
            uu = (np.arange(cnt[i]) + rng.random(cnt[i])) / cnt[i]
            tr = np.interp(uu, cdf, t)
            a_each = leaf_tot[i] / cnt[i]
            for tt in tr:
                srcs.append(("leaf", tt, ta.x[i] + rng.normal(0, ta.crown[i] / 4), ta.y[i] + rng.normal(0, ta.crown[i] / 4),
                             surf4[iy[i], ix[i]] + ta.hc[i], int(ta.ids[i]), 0.0, P["leaf_terminal_ms"], 0.0, a_each))
    # branches above Beaufort 9 (20.8 m/s mean crown wind), and from every failed tree
    for i in range(n):
        kb = np.nonzero(Um[:, i] > P["beaufort_branches_ms"])[0]
        nb_ = min(int(kb.size), 4)
        failed = out_trees[i]["fail_t"] is not None
        if failed:
            nb_ += 3
        for j in range(nb_):
            tt = float(t[kb[min(j, kb.size - 1)]]) if kb.size else float(out_trees[i]["fail_t"])
            if failed and j >= nb_ - 3:
                tt = float(out_trees[i]["fail_t"]) + 1.0
            d_b = 0.03 + 0.05 * rng.random()
            l_b = 0.5 + 1.5 * rng.random()
            m_b = P["wood_density_green"] * np.pi / 4 * d_b ** 2 * l_b
            A_b = d_b * l_b + 0.3 * l_b ** 2       # stick + attached foliage
            kd = 0.5 * P["rho_air"] * P["debris_cd_rod"] * A_b / m_b
            srcs.append(("branch", tt, ta.x[i], ta.y[i], surf4[iy[i], ix[i]] + ta.hc[i], int(ta.ids[i]), kd, 0.0,
                         P["ground_restitution"], 0.0))
    # glass shards
    for (tt, xg, yg, zg, b, area) in glass_src:
        ns = int(np.clip(area / 4.0, 4, 60))
        j4 = min(max(int((yg - y04) // dx4), 0), ny4 - 1)
        i4 = min(max(int((xg - x04) // dx4), 0), nx4 - 1)
        for _ in range(ns):
            a_s = 0.05 + 0.15 * rng.random()          # m2 shard
            m_s = 2500.0 * 0.006 * a_s                 # 6 mm glass
            kd = 0.5 * P["rho_air"] * P["debris_cd_plate"] * a_s / m_s
            srcs.append(("glass", tt + 10 * rng.random(), xg + rng.normal(0, 3), yg + rng.normal(0, 3),
                         surf4[j4, i4] - (grid["bldg_h"][j4, i4]) + zg + rng.normal(0, 4), b, kd, 0.0,
                         P["ground_restitution"], 0.0))

    nsrc = len(srcs)
    kinds = np.array([s[0] for s in srcs])
    if nsrc:
        trel = np.array([s[1] for s in srcs])
        pos0 = np.array([[s[2], s[3], s[4]] for s in srcs], float)
        sid = np.array([s[5] for s in srcs])
        kdr = np.array([s[6] for s in srcs])
        vtr = np.array([s[7] for s in srcs])
        rst = np.array([s[8] for s in srcs])
        larea = np.array([s[9] for s in srcs])
        gp = np.interp(trel, t, gust)
        dt, tmax = 0.05, 90.0
        nsteps = int(tmax / dt) + 1
        traj_every, ntraj = 10, 48
        traj = np.zeros((nsrc, ntraj, 4))
        final = np.empty((nsrc, 3))
        tflight = np.empty(nsrc)
        # chunked to bound the noise array; noise drawn from the seeded generator in a fixed order
        CH = 2000
        for s0 in range(0, nsrc, CH):
            sl = slice(s0, min(nsrc, s0 + CH))
            noise = rng.standard_normal((sl.stop - sl.start, nsteps, 3)).astype(np.float64)
            tr_ = np.zeros((sl.stop - sl.start, ntraj, 4))
            fo, tfo = _fly(wind.u, wind.v, wind.w, wind.solid, wind.ground, wind.x0, wind.y0, wind.dx, wind.zb,
                           surf4, x04, y04, dx4, pos0[sl], gp[sl], kdr[sl], vtr[sl], rst[sl], noise, dt, tmax,
                           tr_, traj_every, P["ground_friction"])
            final[sl], tflight[sl], traj[sl] = fo, tfo, tr_
    else:
        trel = np.zeros(0); pos0 = final = np.zeros((0, 3)); sid = np.zeros(0, int); larea = np.zeros(0)
        traj = np.zeros((0, 48, 4)); tflight = np.zeros(0)

    # ---- inlet clogging from landed leaf litter
    inlet_clog = {}
    if nsrc and inlets:
        ixy = np.array([[d["x"], d["y"]] for d in inlets])
        it_ = cKDTree(ixy)
        leaf = kinds == "leaf"
        acc = np.zeros(len(inlets))
        tfirst = np.full(len(inlets), np.inf)
        landed_t = trel + tflight
        for p in np.nonzero(leaf)[0]:
            for j in it_.query_ball_point(final[p, :2], P["inlet_capture_radius_m"]):
                acc[j] += larea[p]
                tfirst[j] = min(tfirst[j], landed_t[p])
        for j in np.nonzero(acc)[0]:
            c = min(P["inlet_clog_max"], acc[j] / P["inlet_grate_area_m2"])
            inlet_clog[str(inlets[j]["id"])] = {"clog": round(float(c), 3), "remaining": round(1 - float(c), 3),
                                                "t_first_s": round(float(tfirst[j]), 1),
                                                "leaf_area_m2": round(float(acc[j]), 3)}

    # ---- export subset for the web: all branches + glass, then leaves (deterministic stride)
    idx_b = np.nonzero(kinds != "leaf")[0] if nsrc else np.zeros(0, int)
    idx_l = np.nonzero(kinds == "leaf")[0] if nsrc else np.zeros(0, int)
    n_left = max(0, n_debris_export - idx_b.size)
    if idx_b.size > n_debris_export:
        idx_b = idx_b[np.linspace(0, idx_b.size - 1, n_debris_export).astype(int)]
        n_left = 0
    if idx_l.size > n_left:
        idx_l = idx_l[np.linspace(0, idx_l.size - 1, n_left).astype(int)] if n_left else idx_l[:0]
    sel = np.concatenate([idx_b, idx_l]).astype(int)
    nd = sel.size
    frames = np.empty((nfr, nd, 3), np.float32)
    for k in range(nfr):
        landed = (trel[sel] + tflight[sel]) <= t[k]
        frames[k] = np.where(landed[:, None], final[sel], pos0[sel])
    flight = traj[sel].astype(np.float32)
    dmeta = {"n_debris": int(nd), "n_frames": int(nfr), "layout": "debris.f32: n_frames x n_debris x 3 (x, y, z UTM m); "
             "before release_t the particle sits at its source; debris_flight.f32: n_debris x 48 x 4 (t since release s, x, y, z)",
             "kind": [str(k) for k in kinds[sel]], "release_t": [round(float(v), 1) for v in trel[sel]],
             "flight_s": [round(float(v), 2) for v in tflight[sel]], "source_id": [int(v) for v in sid[sel]],
             "n_simulated": int(nsrc), "n_leaf_statistical": int((kinds == "leaf").sum()) if nsrc else 0}

    nf = [r for r in out_trees if r["fail_t"] is not None]
    summary = {"storm": storm.name, "wind_dir_deg": float(wind.wind_dir_deg), "trees": n,
               "trees_failed": len(nf), "snapped": sum(r["mode"] == "snap" for r in nf),
               "uprooted": sum(r["mode"] == "uproot" for r in nf),
               "strikes_car": sum(s["kind"] == "car" for s in strikes),
               "strikes_building": sum(s["kind"] == "building" for s in strikes),
               "glazing_buildings": len(glazing), "glazing_area_m2": round(sum(g["area_failed_m2"] for g in glazing), 1),
               "debris_simulated": int(nsrc), "debris_exported": int(nd), "inlets_clogged": len(inlet_clog),
               "max_crown_gust_ms": round(float(U.max()), 1) if n else 0.0, "seed": str(seed)}
    if verbose:
        print("[trees]", summary, flush=True)
    return TreeResult(out_trees, strikes, glazing, frames, dmeta, flight, inlet_clog, summary)


# ----------------------------------------------------------------------------
# flood-driven cars
# ----------------------------------------------------------------------------
def car_stability(h, vel, P, rel_angle=np.pi / 2):
    """(floating, sliding) for depth h (m) and flow speed vel (m/s); rel_angle = flow vs car axis."""
    Afp = P["car_length_m"] * P["car_width_m"]
    W = P["car_mass_kg"] * G
    B = RHO_W * G * Afp * P["car_seal_frac"] * np.maximum(h - P["car_clearance_m"], 0.0)
    Wn = W - B
    Lp = np.abs(P["car_length_m"] * np.sin(rel_angle)) + np.abs(P["car_width_m"] * np.cos(rel_angle))
    Fd = 0.5 * RHO_W * P["car_cd_side"] * Lp * np.minimum(h, 1.5) * vel ** 2
    floating = Wn <= 0
    sliding = (~floating) & (Fd > P["car_friction"] * Wn) & (h > P["car_clearance_m"])
    return floating, sliding


def float_depth(P=None):
    P = P or load_params()
    return P["car_clearance_m"] + P["car_mass_kg"] / (RHO_W * P["car_length_m"] * P["car_width_m"] * P["car_seal_frac"])


def run_cars(cars, t_frames, depth, u, v, meta, bldg_id=None, ground=None, P=None, substeps=10, struck=None):
    """Move parked cars with the flood. depth/u/v: (n_frames, ny, nx) at meta dx (4 m), m and m/s.

    Returns (frames (n_frames, n_cars, 4) float32 [x, y, z, yaw_deg compass], states list)."""
    P = P or load_params()
    x0, y0, dx = float(meta["x0"]), float(meta["y0"]), float(meta["dx"])
    nfr, ny, nx = depth.shape
    nc = len(cars)
    xy = np.array([[c["x"], c["y"]] for c in cars], float).reshape(nc, 2)
    yaw = np.array([float(c.get("heading_deg", 0.0)) for c in cars])
    gz = ground if ground is not None else np.zeros((ny, nx), np.float32)
    out = np.zeros((nfr, nc, 4), np.float32)
    moved = np.zeros(nc)
    maxd = np.zeros(nc)
    floated = np.zeros(nc, bool)
    slid = np.zeros(nc, bool)
    hf = float_depth(P)
    t_frames = np.asarray(t_frames, float)

    def cell(p):
        ii = np.clip(((p[:, 0] - x0) // dx).astype(int), 0, nx - 1)
        jj = np.clip(((p[:, 1] - y0) // dx).astype(int), 0, ny - 1)
        return ii, jj

    for k in range(nfr):
        if k > 0:
            dt = (t_frames[k] - t_frames[k - 1]) / substeps
            for s in range(substeps):
                a = (s + 0.5) / substeps
                ii, jj = cell(xy)
                h = (1 - a) * depth[k - 1, jj, ii] + a * depth[k, jj, ii]
                uu = (1 - a) * u[k - 1, jj, ii] + a * u[k, jj, ii]
                vv = (1 - a) * v[k - 1, jj, ii] + a * v[k, jj, ii]
                sp = np.hypot(uu, vv)
                flow_brg = np.degrees(np.arctan2(uu, vv)) % 360
                rel = np.radians(flow_brg - yaw)
                fl, sl = car_stability(h, sp, P, rel)
                mv = (fl | sl) & (sp > 0.05)
                if struck is not None:
                    mv &= ~struck
                floated |= fl
                slid |= sl
                maxd = np.maximum(maxd, h)
                if mv.any():
                    step = P["car_drift_frac"] * np.stack([uu, vv], 1) * dt
                    step = np.clip(step, -dx, dx)
                    newp = xy + np.where(mv[:, None], step, 0.0)
                    ni, nj = cell(newp)
                    ok = mv & (depth[k, nj, ni] > P["car_clearance_m"])
                    if bldg_id is not None:
                        ok &= bldg_id[nj, ni] < 0
                    moved += np.where(ok, np.hypot(*(newp - xy).T), 0.0)
                    xy = np.where(ok[:, None], newp, xy)
                    # yaw relaxes toward the flow when floating
                    dyaw = ((flow_brg - yaw + 180) % 360) - 180
                    yaw = np.where(ok & fl, (yaw + 0.2 * dyaw) % 360, yaw)
        ii, jj = cell(xy)
        hk = depth[k, jj, ii]
        zf = np.where(hk > hf, hk - hf, 0.0)
        out[k, :, 0], out[k, :, 1] = xy[:, 0], xy[:, 1]
        out[k, :, 2] = gz[jj, ii] + zf
        out[k, :, 3] = yaw
    states = [{"id": int(c["id"]), "max_depth_m": round(float(maxd[i]), 2), "floated": bool(floated[i]),
               "slid": bool(slid[i]), "moved_m": round(float(moved[i]), 1)} for i, c in enumerate(cars)]
    return out, states


def synthetic_water(grid, storm, n_frames=None, seed=0):
    """Synthetic depth/velocity frames (until sim/water.py is wired): water ponds in local lows,
    rising with cumulative rain and flowing down the terrain gradient."""
    from scipy.ndimage import uniform_filter
    z = grid["z"].astype(np.float64)
    nfr = n_frames or len(storm.t)
    dt = np.diff(np.concatenate([[0.0], storm.t]))
    P = np.cumsum(storm.rain_mm_h * dt / 3600.0) / 1000.0
    low = uniform_filter(z, 25) - z                         # positive in local depressions
    gy, gx = np.gradient(z, float(grid["meta"]["dx"]))
    depth = np.zeros((nfr, *z.shape), np.float32)
    for k in range(nfr):
        depth[k] = np.clip(low * 0.3 + 2 * P[k] - 0.05, 0, 2.0) * (grid["bldg_h"] <= 0)
    s = np.hypot(gx, gy) + 1e-6
    sp = np.clip(4.0 * np.sqrt(s), 0, 3.0)
    u = np.where(depth > 0.01, -gx / s * sp, 0).astype(np.float32)
    v = np.where(depth > 0.01, -gy / s * sp, 0).astype(np.float32)
    return depth, u, v


# ----------------------------------------------------------------------------
# web export
# ----------------------------------------------------------------------------
def export_web(res: TreeResult, out_dir, car_frames=None):
    """Write trees.json, debris.f32, debris.json, debris_flight.f32, inlet_clog.json (+ cars.f32)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "trees.json").write_text(json.dumps(res.trees, separators=(",", ":")))
    np.ascontiguousarray(res.debris_frames, dtype="<f4").tofile(out / "debris.f32")
    np.ascontiguousarray(res.debris_flight, dtype="<f4").tofile(out / "debris_flight.f32")
    (out / "debris.json").write_text(json.dumps(res.debris_meta, separators=(",", ":")))
    (out / "inlet_clog.json").write_text(json.dumps(
        {"about": "Storm leaf-litter clogging per inlet id: clog = blocked fraction of grate; remaining = 1 - clog "
                  "(sim/water.py PlanEdits.clog_factor); t_first_s = first litter landing.",
         "sources": param_sources()["inlet_clog_max"], "inlets": res.inlet_clog}, separators=(",", ":")))
    if car_frames is not None:
        np.ascontiguousarray(car_frames, dtype="<f4").tofile(out / "cars.f32")


def ledger_inputs(res: TreeResult, car_states=None) -> dict:
    """Compact dict for sim/damage.py."""
    return {"summary": res.summary, "strikes": res.strikes, "glazing": res.glazing,
            "failed_trees": [{"id": r["id"], "mode": r["mode"], "fail_t": r["fail_t"]} for r in res.trees if r["fail_t"] is not None],
            "cars": car_states or []}


# ----------------------------------------------------------------------------
# one-call pipeline for sim/run.py
# ----------------------------------------------------------------------------
def _resample_frames(src_t, dst_t, arr):
    """Pick the nearest source frame for every destination time (arr: (n_src, ...))."""
    src_t = np.asarray(src_t, float)
    idx = np.clip(np.searchsorted(src_t, np.asarray(dst_t, float)), 0, len(src_t) - 1)
    prev = np.clip(idx - 1, 0, len(src_t) - 1)
    better = np.abs(src_t[prev] - dst_t) < np.abs(src_t[idx] - dst_t)
    return arr[np.where(better, prev, idx)]


def simulate(storm, water=None, grid=None, trees=None, cars=None, wind_screens=None, plan_hash: str = "",
             out_dir=None, verbose=True) -> dict:
    """Wind + trees + debris + glazing + flood cars for one storm (and optional plan).

    water: sim.water.WaterResult (uses .t_frames, .depth, .u, .v, .saturation) or None (synthetic).
    trees/cars: plan-edited lists (default data/grid). wind_screens: [{polyline, height_m, porosity}].
    out_dir: runs/<id>/frames -> writes trees.json, debris.f32, debris.json, debris_flight.f32,
    inlet_clog.json, cars.f32, wind_slice.f32. Returns ledger inputs for sim/damage.py (+ 'inlet_clog')."""
    from sim.wind import get_wind_plan, load_grid
    grid = grid if grid is not None else load_grid()
    wf = get_wind_plan(storm.wind_dir_deg, grid, screens=wind_screens or [], verbose=verbose)
    cars = cars if cars is not None else json.loads((GRID_DIR / "cars.json").read_text())
    t = np.asarray(storm.t, float)
    sat = None
    if water is not None and getattr(water, "saturation", None) is not None:
        sat = _resample_frames(water.t_frames, t, water.saturation)
    res = run_trees(storm, wf, grid=grid, trees=trees, cars=cars, saturation=sat, plan_hash=plan_hash,
                    verbose=verbose)
    if water is not None:
        depth, u, v, tw = water.depth, water.u, water.v, np.asarray(water.t_frames, float)
    else:
        depth, u, v = synthetic_water(grid, storm)
        tw = t
    struck_ids = {s["target_id"] for s in res.strikes if s["kind"] == "car"}
    struck = np.array([c["id"] in struck_ids for c in cars], bool)
    zg = grid["z"].astype(np.float32)
    cf, cstates = run_cars(cars, tw, depth, u, v, grid["meta"], bldg_id=grid["bldg_id"], ground=zg, struck=struck)
    if len(tw) != len(t) or not np.allclose(tw, t):
        cf = _resample_frames(tw, t, cf)
    for s_, c in zip(cstates, cars):
        s_["struck_by_tree"] = bool(c["id"] in struck_ids)
    if out_dir is not None:
        export_web(res, out_dir, car_frames=cf)
        wf.write_wind_slice(Path(out_dir) / "wind_slice.f32")
    out = ledger_inputs(res, cstates)
    out["inlet_clog"] = res.inlet_clog
    out["summary"]["cars_floated"] = int(sum(s["floated"] for s in cstates))
    out["summary"]["cars_moved"] = int(sum(s["moved_m"] > 1.0 for s in cstates))
    return out


def failure_ratios(storm, wind, trees=None, grid=None, saturation=None, P=None):
    """Peak M/M_crit per tree for snap and uproot (fast path for calibration/what-if)."""
    P = P or load_params()
    trees = trees if trees is not None else json.loads((GRID_DIR / "trees.json").read_text())
    ta = tree_arrays(trees, P)
    vec = wind.vec_at(ta.x, ta.y, ta.hc)
    amp = np.hypot(vec[:, 0], vec[:, 1])
    t = np.asarray(storm.t, float)
    if saturation is None:
        sat = synthetic_saturation(storm, ta.ids.size)
    else:
        sat = np.asarray(saturation, np.float32)
        if sat.ndim == 3:
            meta = grid["meta"]
            ix = np.clip(((ta.x - meta["x0"]) // meta["dx"]).astype(int), 0, sat.shape[2] - 1)
            iy = np.clip(((ta.y - meta["y0"]) // meta["dx"]).astype(int), 0, sat.shape[1] - 1)
            sat = sat[:, iy, ix]
    U = np.asarray(storm.gust_ms, float)[:, None] * amp[None]
    F = drag_force(U, ta.area[None], P)
    r_snap = (F * np.maximum(ta.hc - 1.3, 0.5)[None] / ta.m_snap[None]).max(0)
    r_up = (F * ta.hc[None] / (ta.m_anchor_dry[None] * (1.0 - P["uproot_sat_reduction"] * np.clip(sat, 0, 1)))).max(0)
    return r_snap, r_up, amp


def calibrate_anchorage(storm, wind, target: int, **kw) -> float:
    """uproot_calib_factor such that (snap | uproot) failures == target (anchorage scales r_up as 1/f)."""
    r_snap, r_up, _ = failure_ratios(storm, wind, **kw)
    n_snap = int((r_snap >= 1).sum())
    need = max(target - n_snap, 0)
    cand = np.sort(r_up[r_snap < 1])[::-1]
    if need == 0:
        return float(cand[0] * 1.001) if cand.size else 1.0
    return float(cand[min(need, cand.size) - 1])   # r_up / f >= 1 for exactly `need` trees
