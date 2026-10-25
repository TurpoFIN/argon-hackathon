"""StormSim SF wind solver: D3Q19 lattice-Boltzmann LES over the voxelised city.

Model
-----
* Lattice: D3Q19, cubic cells (8 m default, 16 m "fast" mode for plan re-runs),
  nz levels up to ~450 m (Salesforce Tower is 326 m).
* Collision: regularized BGK (Latt & Chopard 2006, Math. Comput. Simul. 72:165)
  with a Smagorinsky sub-grid eddy viscosity (Smagorinsky 1963; Cs = 0.17, Lilly
  1967) computed locally from the non-equilibrium stress (Hou et al. 1996,
  J. Comput. Phys. 118:329). The regularization filters ghost modes, which keeps
  the scheme stable at the very high cell Reynolds numbers of an 8 m urban LES.
* Walls: half-way bounce-back on building and terrain voxels (no-slip). Free-slip
  (specular reflection) at the top lid.
* Inflow: log-law atmospheric boundary layer, U(z) = U10 ln((z+z0)/z0)/ln((10+z0)/z0),
  z0 = 0.7 m (dense urban, Davenport/Wieringa roughness classification), applied
  on the upwind faces (faces whose inward normal has a positive component along
  the storm wind) with non-equilibrium extrapolation (Guo et al. 2002, Chin.
  Phys. 11:366). Downwind faces are constant-pressure outflow. A 640 m building-
  free buffer with urban-canopy drag surrounds the tile (upstream city), so the flow enters the tile as a
  developed canopy flow and inflow faces never cut through buildings.
* Units: the lattice inflow speed at 10 m is 0.03 (Mach 0.05 at street level;
  <= 0.12 at the 450 m lid). All outputs are dimensionless amplification factors
  relative to the free-stream (inflow) 10 m speed, so physical velocity =
  amplification x U10(t). This linear scaling is valid because flow around
  sharp-edged bluff bodies (buildings) is Reynolds-number independent above
  Re ~ 1e4 (separation is fixed at the edges; e.g. Snyder 1981 EPA fluid-modeling
  guideline, Re_H > 11,000; Castro & Robins 1977 JFM 79:307). Storm Re ~ 1e8.
* Facade pressure: quasi-steady mean pressure coefficient Cp = (p - p_inf) /
  (1/2 rho U10^2) from the time-averaged LES field on fluid voxels touching each
  building. Peak facade pressure = Cp x 1/2 rho U_gust^2 (quasi-steady gust
  approach of ASCE 7-22 Ch. 26-30, where the 3-s gust velocity pressure is used).
* Wind screens (plan interventions): porous voxels with a pressure-loss
  coefficient K = (1/(0.62 beta) - 1)^2 per cell thickness (perforated-plate
  correlation, Idelchik "Handbook of Hydraulic Resistance" / Laws & Livesey 1978),
  applied as a quadratic momentum sink with the exact-difference forcing method.

Public API
----------
get_wind(wind_dir_deg, grid=None, fast=False, screens=None) -> WindField  (cached)
get_wind_plan(wind_dir_deg, grid, screens) -> WindField   (8 m base x 16 m screen ratio; use for plans)
WindField.wind_at(x, y, z_agl) -> speed amplification (vectorised)
WindField.vec_at(x, y, z_agl)  -> (u, v, w) amplification components
WindField.slice(h_agl, dx_out=8) -> (ny8, nx8, 2) float32 (u, v) amplification
WindField.write_wind_slice(path)  -> web `wind_slice.f32` (10 m, 8 m grid)
WindField.facade -> {bldg_id: {cp_max, cp_min, cp_peak, z_peak}}
WindField.save_png(path)
wind_at(x, y, z, field)  module-level convenience
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field as dfield
from pathlib import Path

import numpy as np

os.environ.setdefault("NUMBA_NUM_THREADS", "16")
import numba as nb  # noqa: E402
from numba import njit, prange  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GRID_DIR = ROOT / "data" / "grid"

# ----------------------------------------------------------------------------
# D3Q19 lattice
# ----------------------------------------------------------------------------
_C = np.array([
    [0, 0, 0],
    [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1],
    [1, 1, 0], [-1, -1, 0], [1, -1, 0], [-1, 1, 0],
    [1, 0, 1], [-1, 0, -1], [1, 0, -1], [-1, 0, 1],
    [0, 1, 1], [0, -1, -1], [0, 1, -1], [0, -1, 1],
], dtype=np.int64)
CX = _C[:, 0].copy()
CY = _C[:, 1].copy()
CZ = _C[:, 2].copy()
W = np.array([1 / 3] + [1 / 18] * 6 + [1 / 36] * 12, dtype=np.float32)
OPP = np.array([int(np.where((_C == -c).all(1))[0][0]) for c in _C], dtype=np.int64)
MZ = np.array([int(np.where((_C == c * np.array([1, 1, -1])).all(1))[0][0]) for c in _C], dtype=np.int64)

KAPPA = 0.41
Z0_URBAN = 0.7          # m, inflow aerodynamic roughness (dense urban)
U10_LAT = 0.03          # lattice inflow speed at 10 m AGL
CS_SMAG = 0.17
RHO_AIR = 1.225         # kg/m3
SOLVER_VERSION = "lbm-d3q19-reg-smag-v4"
CANOPY_H, CANOPY_LF = 15.0, 0.3   # upstream urban canopy height (m) and frontal area index
NU0_LAT = 2e-3
FACADE_Q = (0.0, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0)   # quantiles of |Cp| over a building's facade cells


def log_profile(z_agl, z0=Z0_URBAN):
    """Inflow speed normalised to 1 at 10 m AGL (log law)."""
    z = np.maximum(np.asarray(z_agl, dtype=np.float64), 0.0)
    return np.log((z + z0) / z0) / np.log((10.0 + z0) / z0)


# ----------------------------------------------------------------------------
# numba kernels
# ----------------------------------------------------------------------------
@njit(inline="always", fastmath=True)
def _feq(q, rho, ux, uy, uz, w):
    cu = 3.0 * (CX[q] * ux + CY[q] * uy + CZ[q] * uz)
    usq = 1.5 * (ux * ux + uy * uy + uz * uz)
    return w * rho * (1.0 + cu + 0.5 * cu * cu - usq)


@njit(parallel=True, fastmath=True, cache=True)
def lbm_step(f, g, solid, near, resist, tau0, cs2):
    """Fused pull-stream + regularized-BGK/Smagorinsky collision, f -> g."""
    _, nz, ny, nx = f.shape
    for kj in prange(nz * ny):
        k = kj // ny
        j = kj - k * ny
        fl = np.empty(19, np.float32)
        for i in range(nx):
            if solid[k, j, i] != 0:
                continue
            # --- streaming (pull): bulk fast path, else boundary handling
            if near[k, j, i] == 0:
                for q in range(19):
                    fl[q] = f[q, k - CZ[q], j - CY[q], i - CX[q]]
            else:
              for q in range(19):
                  sk = k - CZ[q]
                  sj = j - CY[q]
                  si = i - CX[q]
                  if sj < 0 or sj >= ny or si < 0 or si >= nx:
                      fl[q] = f[q, k, j, i]          # face cells: overwritten by BC pass
                  elif sk >= nz:                      # free-slip lid: specular reflection
                      if solid[nz - 1, sj, si] != 0:
                          fl[q] = f[OPP[q], k, j, i]
                      else:
                          fl[q] = f[MZ[q], nz - 1, sj, si]
                  elif sk < 0:
                      fl[q] = f[OPP[q], k, j, i]
                  elif solid[sk, sj, si] != 0:
                      if solid[sk, sj, si] == 2 and CZ[q] == 1 and solid[k, sj, si] == 0:
                          # terrain floor: free-slip (specular); wall stress via log-law drag in `resist`
                          fl[q] = f[MZ[q], k, sj, si]
                      else:                           # half-way bounce-back (buildings, terrain steps)
                          fl[q] = f[OPP[q], k, j, i]
                  else:
                      fl[q] = f[q, sk, sj, si]
            # --- moments
            rho = 0.0
            mx = 0.0
            my = 0.0
            mz = 0.0
            pxx = 0.0
            pyy = 0.0
            pzz = 0.0
            pxy = 0.0
            pxz = 0.0
            pyz = 0.0
            for q in range(19):
                v = fl[q]
                cx = CX[q]
                cy = CY[q]
                cz = CZ[q]
                rho += v
                mx += cx * v
                my += cy * v
                mz += cz * v
                pxx += cx * cx * v
                pyy += cy * cy * v
                pzz += cz * cz * v
                pxy += cx * cy * v
                pxz += cx * cz * v
                pyz += cy * cz * v
            irho = 1.0 / rho
            ux = mx * irho
            uy = my * irho
            uz = mz * irho
            c3 = rho / 3.0
            # non-equilibrium stress
            pxx -= c3 + rho * ux * ux
            pyy -= c3 + rho * uy * uy
            pzz -= c3 + rho * uz * uz
            pxy -= rho * ux * uy
            pxz -= rho * ux * uz
            pyz -= rho * uy * uz
            qq = pxx * pxx + pyy * pyy + pzz * pzz + 2.0 * (pxy * pxy + pxz * pxz + pyz * pyz)
            tau = 0.5 * (tau0 + np.sqrt(tau0 * tau0 + 18.0 * 1.41421356 * cs2 * np.sqrt(qq) * irho))
            om = 1.0 - 1.0 / tau
            # robustness limiter: cap |u| at 0.2 (Ma 0.35) in rare transient cells (never active in the
            # converged mean field; prevents blow-up at sharp tower tops / outflow backflow)
            um2 = ux * ux + uy * uy + uz * uz
            if um2 > 0.04:
                sc = 0.2 / np.sqrt(um2)
                ux *= sc
                uy *= sc
                uz *= sc
            # porous momentum sink (wind screens / porous plan elements)
            r = resist[k, j, i]
            if r > 0.0:
                um = np.sqrt(ux * ux + uy * uy + uz * uz)
                s = 1.0 / (1.0 + r * um)
                ux *= s
                uy *= s
                uz *= s
            tr = (pxx + pyy + pzz) / 3.0
            for q in range(19):
                cx = CX[q]
                cy = CY[q]
                cz = CZ[q]
                qp = (cx * cx * pxx + cy * cy * pyy + cz * cz * pzz
                      + 2.0 * (cx * cy * pxy + cx * cz * pxz + cy * cz * pyz)) - tr
                g[q, k, j, i] = _feq(q, rho, ux, uy, uz, W[q]) + om * 4.5 * W[q] * qp


@njit(fastmath=True, inline="always")
def _set_face(g, k, j, i, nj, ni, rho_fix, ux_in, uy_in, uz_in, inflow):
    # neighbour moments
    rho = 0.0
    mx = 0.0
    my = 0.0
    mz = 0.0
    for q in range(19):
        v = g[q, k, nj, ni]
        rho += v
        mx += CX[q] * v
        my += CY[q] * v
        mz += CZ[q] * v
    ux = mx / rho
    uy = my / rho
    uz = mz / rho
    if inflow:
        rb = rho
        bx = ux_in
        by = uy_in
        bz = uz_in
    else:
        rb = rho_fix
        bx = ux
        by = uy
        bz = uz
    for q in range(19):
        w = W[q]
        g[q, k, j, i] = _feq(q, rb, bx, by, bz, w) + (g[q, k, nj, ni] - _feq(q, rho, ux, uy, uz, w))


@njit(parallel=True, fastmath=True, cache=True)
def apply_bc(g, solid, prof_w, prof_e, prof_s, prof_n, flags, ex, ey, ramp):
    """Inflow (equilibrium + non-eq extrapolation) / outflow on the 4 side faces.

    flags[0..3] = W, E, S, N: 1 inflow, 0 outflow. prof_* = speed (lattice) per face cell.
    """
    _, nz, ny, nx = g.shape
    for k in prange(nz):
        for j in range(ny):
            if solid[k, j, 0] == 0 and solid[k, j, 1] == 0:
                s = prof_w[k, j] * ramp
                _set_face(g, k, j, 0, j, 1, 1.0, s * ex, s * ey, 0.0, flags[0] == 1)
            if solid[k, j, nx - 1] == 0 and solid[k, j, nx - 2] == 0:
                s = prof_e[k, j] * ramp
                _set_face(g, k, j, nx - 1, j, nx - 2, 1.0, s * ex, s * ey, 0.0, flags[1] == 1)
        for i in range(nx):
            if solid[k, 0, i] == 0 and solid[k, 1, i] == 0:
                s = prof_s[k, i] * ramp
                _set_face(g, k, 0, i, 1, i, 1.0, s * ex, s * ey, 0.0, flags[2] == 1)
            if solid[k, ny - 1, i] == 0 and solid[k, ny - 2, i] == 0:
                s = prof_n[k, i] * ramp
                _set_face(g, k, ny - 1, i, ny - 2, i, 1.0, s * ex, s * ey, 0.0, flags[3] == 1)


@njit(parallel=True, fastmath=True, cache=True)
def sponge(g, solid, prof_w, prof_e, prof_s, prof_n, flags, ex, ey, ramp, pad, smax):
    """Absorbing layer in the building-free buffer: relax towards feq(rho=1, u_target).

    Damps the domain-scale acoustic modes that inflow/outflow BCs otherwise trap
    (cf. Xu & Sagaut 2013 absorbing layers for LBM). u_target = inflow profile on
    upwind faces, local velocity on outflow faces (pressure-only damping)."""
    _, nz, ny, nx = g.shape
    for k in prange(nz):
        for j in range(ny):
            for i in range(nx):
                d = min(min(i, nx - 1 - i), min(j, ny - 1 - j))
                if d >= pad or solid[k, j, i] != 0:
                    continue
                s = smax * ((pad - d) / pad) ** 2
                # which face is nearest
                if d == i:
                    fl = flags[0]; sp = prof_w[k, j]
                elif d == nx - 1 - i:
                    fl = flags[1]; sp = prof_e[k, j]
                elif d == j:
                    fl = flags[2]; sp = prof_s[k, i]
                else:
                    fl = flags[3]; sp = prof_n[k, i]
                rho = 0.0
                mx = 0.0
                my = 0.0
                mz = 0.0
                for q in range(19):
                    v = g[q, k, j, i]
                    rho += v
                    mx += CX[q] * v
                    my += CY[q] * v
                    mz += CZ[q] * v
                ux = mx / rho
                uy = my / rho
                uz = mz / rho
                if fl == 1:
                    tx = sp * ramp * ex
                    ty = sp * ramp * ey
                    tz = 0.0
                else:
                    tx = ux
                    ty = uy
                    tz = uz
                for q in range(19):
                    w = W[q]
                    g[q, k, j, i] += s * (_feq(q, 1.0, tx, ty, tz, w) - _feq(q, rho, ux, uy, uz, w))


@njit(parallel=True, fastmath=True, cache=True)
def accumulate(f, solid, acc):
    """acc[0..3] += (rho, ux, uy, uz)."""
    _, nz, ny, nx = f.shape
    for kj in prange(nz * ny):
        k = kj // ny
        j = kj - k * ny
        for i in range(nx):
            if solid[k, j, i] != 0:
                continue
            rho = 0.0
            mx = 0.0
            my = 0.0
            mz = 0.0
            for q in range(19):
                v = f[q, k, j, i]
                rho += v
                mx += CX[q] * v
                my += CY[q] * v
                mz += CZ[q] * v
            acc[0, k, j, i] += rho
            acc[1, k, j, i] += mx / rho
            acc[2, k, j, i] += my / rho
            acc[3, k, j, i] += mz / rho


@njit(parallel=True, fastmath=True, cache=True)
def init_eq(f, solid, ux, uy, uz):
    _, nz, ny, nx = f.shape
    for kj in prange(nz * ny):
        k = kj // ny
        j = kj - k * ny
        for i in range(nx):
            a = ux[k, j, i]
            b = uy[k, j, i]
            c = uz[k, j, i]
            if solid[k, j, i] != 0:
                a = 0.0
                b = 0.0
                c = 0.0
            for q in range(19):
                f[q, k, j, i] = _feq(q, 1.0, a, b, c, W[q])


# ----------------------------------------------------------------------------
# grid handling
# ----------------------------------------------------------------------------
def load_grid(grid_dir: Path | str = GRID_DIR) -> dict:
    """Load data/grid (meta + city.npz). Falls back to a synthetic city if absent."""
    grid_dir = Path(grid_dir)
    mp = grid_dir / "meta.json"
    cp = grid_dir / "city.npz"
    if mp.exists() and cp.exists():
        meta = json.loads(mp.read_text())
        d = dict(np.load(cp))
        d["meta"] = meta
        bj = grid_dir / "buildings.json"
        if bj.exists():
            d["buildings"] = json.loads(bj.read_text())
        return d
    return synthetic_city()


def synthetic_city(n=400, dx=4.0, seed=0) -> dict:
    """Synthetic SoMa-like test city: block grid, a diagonal 'Market St', a few towers."""
    rng = np.random.default_rng(seed)
    ny = nx = n
    yy, xx = np.mgrid[0:ny, 0:nx] * dx + dx / 2
    z = (5.0 + 20.0 * np.exp(-(((xx - 1200) ** 2 + (yy - 1300) ** 2) / (2 * 250.0 ** 2)))).astype(np.float32)
    bh = np.zeros((ny, nx), np.float32)
    bid = -np.ones((ny, nx), np.int32)
    block, street = 100.0, 24.0
    buildings = []
    b = 0
    for by0 in np.arange(150, ny * dx - 150, block):
        for bx0 in np.arange(150, nx * dx - 150, block):
            m = ((xx >= bx0) & (xx < bx0 + block - street) & (yy >= by0) & (yy < by0 + block - street))
            # diagonal Market St (NE-SW) 36 m wide
            m &= np.abs((xx - yy) / np.sqrt(2)) > 18.0
            if not m.any():
                continue
            h = float(rng.choice([12, 18, 25, 35, 50]))
            bh[m] = h
            bid[m] = b
            buildings.append({"id": b, "x": float(xx[m].mean()), "y": float(yy[m].mean()), "height_m": h,
                              "year_built": 1950})
            b += 1
    for (tx, ty, h) in [(800, 760, 300), (1000, 1000, 180), (600, 1100, 140), (1150, 700, 120)]:
        m = (np.abs(xx - tx) < 26) & (np.abs(yy - ty) < 26)
        bh[m] = h
        bid[m] = b
        buildings.append({"id": b, "x": float(tx), "y": float(ty), "height_m": float(h), "year_built": 2018})
        b += 1
    cover = np.where(bh > 0, 1, 0).astype(np.uint8)
    meta = {"crs": "EPSG:32610", "dx": dx, "nx": nx, "ny": ny, "x0": 550000.0, "y0": 4180000.0, "synthetic": True}
    return {"z": z, "bldg_h": bh, "bldg_id": bid, "cover": cover, "meta": meta, "buildings": buildings}


def _block_reduce(a, f, fn):
    ny, nx = a.shape
    py, px = (-ny) % f, (-nx) % f
    if py or px:
        a = np.pad(a, ((0, py), (0, px)), mode="edge")
    return fn(a.reshape(a.shape[0] // f, f, a.shape[1] // f, f), axis=(1, 3))


def coarse_shape(ny, nx, f):
    """Coarse grid shape used everywhere in StormSim: ceil(n / f) (edge-padded)."""
    return (-(-ny // f), -(-nx // f))


def coarsen_city(grid: dict, dx_out: float):
    meta = grid["meta"]
    f = int(round(dx_out / meta["dx"]))
    z = grid["z"].astype(np.float32).copy()
    cover = grid.get("cover")
    if cover is not None:
        z = np.where(cover == 3, np.maximum(z, 0.0), z)    # bay: water surface
    bh = grid["bldg_h"].astype(np.float32)
    bid = grid["bldg_id"].astype(np.int64)
    isb = (bh > 0.5) & (bid >= 0)
    ground = _block_reduce(z, f, np.mean)
    frac = _block_reduce(isb.astype(np.float32), f, np.mean)
    top = np.where(isb, z + bh, 0.0)
    tsum = _block_reduce(top, f, np.sum)
    nb_ = _block_reduce(isb.astype(np.float32), f, np.sum)
    btop = np.where(frac >= 0.5, tsum / np.maximum(nb_, 1), -1e9)
    # building id of the tallest sub-cell
    hh = np.where(isb, bh, -1.0)
    ny, nx = hh.shape
    py, px = (-ny) % f, (-nx) % f
    hp = np.pad(hh, ((0, py), (0, px)), constant_values=-1.0)
    ip = np.pad(bid, ((0, py), (0, px)), constant_values=-1)
    H, Wd = hp.shape[0] // f, hp.shape[1] // f
    hr = hp.reshape(H, f, Wd, f).transpose(0, 2, 1, 3).reshape(H, Wd, f * f)
    ir = ip.reshape(H, f, Wd, f).transpose(0, 2, 1, 3).reshape(H, Wd, f * f)
    am = hr.argmax(-1)
    bid8 = np.take_along_axis(ir, am[..., None], -1)[..., 0]
    bid8 = np.where(hr.max(-1) > 0, bid8, -1)
    return ground.astype(np.float32), btop.astype(np.float32), bid8.astype(np.int32), f


def screens_to_resist(screens, x0, y0, dx, ground, zc, shape):
    """Rasterise wind screens [{polyline, height_m, porosity}] into a resistance field."""
    nz, ny, nx = shape
    res = np.zeros(shape, np.float32)
    for s in screens or []:
        pts = np.asarray(s["polyline"], float)
        h = float(s.get("height_m", 3.0))
        beta = float(np.clip(s.get("porosity", 0.5), 0.05, 0.95))
        K = (1.0 / (0.62 * beta) - 1.0) ** 2
        for a, b in zip(pts[:-1], pts[1:]):
            L = np.hypot(*(b - a))
            for t in np.linspace(0, 1, max(2, int(L / (dx / 3)))):
                p = a + t * (b - a)
                i = int((p[0] - x0) // dx)
                j = int((p[1] - y0) // dx)
                if not (0 <= i < nx and 0 <= j < ny):
                    continue
                for k in range(nz):
                    lo = zc[k] - dx / 2 - ground[j, i]
                    cover = np.clip((h - max(lo, 0.0)) / dx, 0.0, 1.0)
                    if lo + dx <= 0 or cover <= 0:
                        continue
                    # quadratic loss K*u^2/2 over one cell, scaled by covered fraction
                    res[k, j, i] = max(res[k, j, i], 0.5 * K * cover)
    return res


def _near_mask(solid):
    """1 where the pull stencil touches a solid, a side face or the lid (slow path)."""
    s = solid != 0
    near = s.copy()
    nz, ny, nx = s.shape
    for q in range(1, 19):
        cz, cy, cx = CZ[q], CY[q], CX[q]
        sh = np.zeros_like(s)
        # sh[k,j,i] = s[k-cz, j-cy, i-cx]
        dst = (slice(max(cz, 0), nz + min(cz, 0)), slice(max(cy, 0), ny + min(cy, 0)), slice(max(cx, 0), nx + min(cx, 0)))
        src = (slice(max(-cz, 0), nz + min(-cz, 0)), slice(max(-cy, 0), ny + min(-cy, 0)), slice(max(-cx, 0), nx + min(-cx, 0)))
        sh[dst] = s[src]
        near |= sh
    near[:, :, :1] = near[:, :, -1:] = True
    near[:, :1, :] = near[:, -1:, :] = True
    near[-1] = near[0] = True
    return near.astype(np.uint8)


# ----------------------------------------------------------------------------
# solver driver
# ----------------------------------------------------------------------------
PAD_M = 640.0    # building-free buffer around the tile (m) carrying upstream urban-canopy drag
SPONGE = 12      # absorbing-layer width (cells) at the domain faces


def _grid_hash(grid, dx, nz, wind_dir, screens, steps):
    h = hashlib.sha1()
    for k in ("z", "bldg_h"):
        h.update(np.ascontiguousarray(grid[k]).tobytes())
    h.update(json.dumps([grid["meta"].get(k) for k in ("dx", "nx", "ny", "x0", "y0")]).encode())
    h.update(json.dumps([SOLVER_VERSION, dx, nz, round(wind_dir, 1), screens or [], steps], sort_keys=True,
                        default=str).encode())
    return h.hexdigest()[:10]


def run_lbm(grid: dict, wind_dir_deg: float, dx: float = 8.0, top_m: float = 448.0, screens=None,
            flow_throughs: float = 1.6, avg_frac: float = 0.35, threads: int | None = None, verbose=True,
            init=None, pad_m: float | None = None):
    """Run the LBM to quasi-steady state; return dict of mean fields (cropped to the tile).

    init: optional WindField on the same lattice (warm start; its velocity vectors are rotated to the new
    direction). Cuts spin-up: 0.6-0.8 flow-throughs suffice."""
    t0 = time.time()
    if threads:
        nb.set_num_threads(min(threads, nb.config.NUMBA_NUM_THREADS))
    meta = grid["meta"]
    ground, btop, bid8, f = coarsen_city(grid, dx)
    nyc, nxc = ground.shape
    PAD = max(SPONGE, int(round((PAD_M if pad_m is None else pad_m) / dx)))
    gp = np.pad(ground, PAD, mode="edge")
    bp = np.pad(btop, PAD, constant_values=-1e9)
    ny, nx = gp.shape
    zbase = float(np.floor(gp.min())) - 0.75 * dx
    nz = int(np.ceil((max(top_m, float(np.nanmax(np.where(bp > 0, bp, gp))) + 80.0) - zbase) / dx))
    zc = zbase + (np.arange(nz) + 0.5) * dx
    surf = np.maximum(gp, bp)
    # 1 = building voxel (no-slip bounce-back), 2 = terrain voxel (free-slip floor + log-law drag)
    terr = zc[:, None, None] < gp[None]
    terr[0] = True
    solid = np.where(terr, 2, (zc[:, None, None] < surf[None]).astype(np.uint8)).astype(np.uint8)
    x0p = meta["x0"] - PAD * dx
    y0p = meta["y0"] - PAD * dx
    resist = screens_to_resist(screens, x0p, y0p, dx, gp, zc, solid.shape)
    near = _near_mask(solid)
    # Wall-modelled ground (under-resolved LES): first fluid cell above terrain gets the log-law
    # surface stress tau = rho u*^2, u* = kappa U1 / ln(z1/z0) -> momentum sink r|u|u per step with
    # r = (kappa / ln(z1/z0))^2 / dz (dz = 1 lattice). z0 = 0.7 m upstream buffer (sustains the
    # urban inflow profile), 0.1 m inside the tile (sub-grid street roughness: cars, furniture);
    # resolved buildings provide the rest of the drag.
    kf = np.argmax(~terr, axis=0)                          # first fluid level above terrain
    z1 = np.maximum(zc[kf] - gp, 0.25 * dx)
    z0g = np.full(gp.shape, 0.1)
    z0g[:PAD], z0g[-PAD:], z0g[:, :PAD], z0g[:, -PAD:] = Z0_URBAN, Z0_URBAN, Z0_URBAN, Z0_URBAN
    rr = (KAPPA / np.log((z1 + z0g) / z0g)) ** 2
    jj, ii = np.nonzero(solid[kf, np.arange(ny)[:, None], np.arange(nx)[None]] == 0)
    resist[kf[jj, ii], jj, ii] += rr[jj, ii].astype(np.float32)
    # Upstream city in the building-free buffer: distributed urban-canopy drag (Coceal & Belcher 2004,
    # QJRMS 130:1349) F/V = 1/2 Cd a |u| u below h_c = 15 m, a = lambda_f / h_c = 0.3/15 m^-1, Cd = 1, so
    # the flow enters the tile as a developed canopy flow instead of an open-terrain jet.
    edge = np.zeros(gp.shape, bool)
    edge[:PAD], edge[-PAD:], edge[:, :PAD], edge[:, -PAD:] = True, True, True, True
    zag_c = zc[:, None, None] - gp[None]
    canopy_c = (zag_c > 0) & (zag_c < CANOPY_H) & edge[None] & (solid == 0)
    resist[canopy_c] += np.float32(0.5 * 1.0 * (CANOPY_LF / CANOPY_H) * dx)

    th = np.deg2rad(wind_dir_deg)
    ex, ey = -np.sin(th), -np.cos(th)      # direction the wind blows TOWARD
    flags = np.array([ex > -0.05, -ex > -0.05, ey > -0.05, -ey > -0.05], dtype=np.int64)
    # inflow profile per face cell (terrain following)
    def prof(gline):
        zag = zc[:, None] - gline[None, :]
        return (U10_LAT * log_profile(zag)).astype(np.float32) * (zag > 0)
    prof_w, prof_e = prof(gp[:, 0]), prof(gp[:, -1])
    prof_s, prof_n = prof(gp[0, :]), prof(gp[-1, :])
    umax = float(max(p.max() for p in (prof_w, prof_e, prof_s, prof_n)))

    # initial field: terrain-following log profile, zero inside solids
    zag = zc[:, None, None] - gp[None]
    sp = (U10_LAT * log_profile(zag)).astype(np.float32)
    # inside the city start slower (roughly the canopy deficit) to limit the start-up shock
    canopy = np.clip(zc[:, None, None] - np.where(bp > 0, bp, gp)[None], 0, None)
    sp *= np.clip(canopy / 40.0, 0.3, 1.0).astype(np.float32)
    ux0, uy0, uz0 = sp * ex, sp * ey, np.zeros_like(sp)
    if init is not None:
        dth = np.deg2rad(wind_dir_deg - init.wind_dir_deg)
        c, s_ = np.cos(dth), np.sin(dth)        # rotate vectors clockwise by dth (compass)
        sl0 = (slice(None), slice(PAD, PAD + nyc), slice(PAD, PAD + nxc))
        iu, iv, iw = init.u, init.v, init.w
        if iu.shape != (nz, nyc, nxc):         # nearest-neighbour resample from another lattice
            kk = np.clip(((zc - init.zb) // init.dx).astype(int), 0, iu.shape[0] - 1)
            jj = np.clip(((np.arange(nyc) + 0.5) * dx // init.dx).astype(int), 0, iu.shape[1] - 1)
            ii = np.clip(((np.arange(nxc) + 0.5) * dx // init.dx).astype(int), 0, iu.shape[2] - 1)
            ix_ = np.ix_(kk, jj, ii)
            iu, iv, iw = iu[ix_], iv[ix_], iw[ix_]
        ux0[sl0] = U10_LAT * (c * iu + s_ * iv)
        uy0[sl0] = U10_LAT * (-s_ * iu + c * iv)
        uz0[sl0] = U10_LAT * iw
        if verbose:
            print(f"[wind] warm start from {init.wind_dir_deg:.0f} deg field", flush=True)
    f_a = np.empty((19, nz, ny, nx), np.float32)
    init_eq(f_a, solid, ux0.astype(np.float32), uy0.astype(np.float32), uz0.astype(np.float32))
    f_b = np.empty_like(f_a)
    f_b[:] = f_a

    # Smagorinsky in lattice units (Delta = 1); molecular viscosity negligible
    # small background viscosity nu0 = 2e-3 (lattice) for stability; eddy viscosity dominates near buildings
    tau0 = np.float32(0.5 + 3.0 * NU0_LAT)
    cs2 = np.float32(CS_SMAG ** 2)
    # flow-through time along the wind (using the mean of the inflow profile)
    Lx = abs(ex) * nx + abs(ey) * ny
    umean = float(np.mean(prof_s[prof_s > 0])) if (prof_s > 0).any() else U10_LAT
    n_steps = int(flow_throughs * Lx / max(umean, 1e-3))
    n_avg = int(avg_frac * n_steps)
    ramp_n = 200
    acc = np.zeros((4, nz, ny, nx), np.float32)
    n_acc = 0
    if verbose:
        print(f"[wind] dir={wind_dir_deg:.0f} dx={dx} grid={nx}x{ny}x{nz} = {nx*ny*nz/1e6:.1f} M cells, "
              f"steps={n_steps} (avg {n_avg}), u_max_lat={umax:.3f} (Ma {umax*np.sqrt(3):.2f}), "
              f"threads={nb.get_num_threads()}", flush=True)
    tl = time.time()
    for step in range(n_steps):
        ramp = np.float32(min(1.0, 0.5 + 0.5 * step / ramp_n))
        lbm_step(f_a, f_b, solid, near, resist, tau0, cs2)
        apply_bc(f_b, solid, prof_w, prof_e, prof_s, prof_n, flags, np.float32(ex), np.float32(ey), ramp)
        sponge(f_b, solid, prof_w, prof_e, prof_s, prof_n, flags, np.float32(ex), np.float32(ey), ramp,
               SPONGE, np.float32(0.15))
        f_a, f_b = f_b, f_a
        if step >= n_steps - n_avg and step % 2 == 0:
            accumulate(f_a, solid, acc)
            n_acc += 1
        if verbose and (step % 500 == 0 or step == n_steps - 1):
            el = time.time() - tl
            rate = (step + 1) * nx * ny * nz / max(el, 1e-9) / 1e6
            sm = float(f_a[:, nz // 2].sum(0).mean())
            print(f"[wind]  step {step}/{n_steps}  {el:.0f}s  {rate:.0f} MLUPS  mean rho(mid)={sm:.5f}", flush=True)
            if not np.isfinite(sm):
                raise FloatingPointError("LBM diverged")
    acc /= max(n_acc, 1)
    rho, ux, uy, uz = acc
    fluid = solid == 0
    # free-stream static pressure reference: mean density over inflow face fluid cells above the canopy
    ref_mask = np.zeros_like(fluid)
    if flags[0]: ref_mask[:, :, 1] = True
    if flags[1]: ref_mask[:, :, -2] = True
    if flags[2]: ref_mask[:, 1, :] = True
    if flags[3]: ref_mask[:, -2, :] = True
    ref_mask &= fluid
    rho_ref = float(rho[ref_mask].mean())
    sl = (slice(None), slice(PAD, PAD + nyc), slice(PAD, PAD + nxc))
    out = dict(
        u=(ux[sl] / U10_LAT).astype(np.float16), v=(uy[sl] / U10_LAT).astype(np.float16),
        w=(uz[sl] / U10_LAT).astype(np.float16),
        cp=np.where(fluid[sl], (rho[sl] - rho_ref) / 3.0 / (0.5 * U10_LAT ** 2), 0).astype(np.float16),
        solid=solid[sl], ground=ground, btop=btop, bid=bid8, zc=zc.astype(np.float32),
        dx=np.float32(dx), x0=np.float64(meta["x0"]), y0=np.float64(meta["y0"]),
        wind_dir_deg=np.float32(wind_dir_deg), runtime_s=np.float32(time.time() - t0),
        n_steps=np.int64(n_steps), mlups=np.float32(n_steps * nx * ny * nz / (time.time() - tl) / 1e6),
    )
    if verbose:
        print(f"[wind] done in {time.time()-t0:.0f}s", flush=True)
    return out


# ----------------------------------------------------------------------------
# field object
# ----------------------------------------------------------------------------
@njit(parallel=True, fastmath=True, cache=True)
def _trilerp(u, v, w, solid, ground, x0, y0, dx, zb, xs, ys, zs_agl, out):
    nz, ny, nx = u.shape
    for p in prange(xs.shape[0]):
        fx = (xs[p] - x0) / dx - 0.5
        fy = (ys[p] - y0) / dx - 0.5
        fx = min(max(fx, 0.0), nx - 1.001)
        fy = min(max(fy, 0.0), ny - 1.001)
        i0 = int(fx)
        j0 = int(fy)
        tx = fx - i0
        ty = fy - j0
        g = ((1 - tx) * (1 - ty) * ground[j0, i0] + tx * (1 - ty) * ground[j0, i0 + 1]
             + (1 - tx) * ty * ground[j0 + 1, i0] + tx * ty * ground[j0 + 1, i0 + 1])
        fz = (g + zs_agl[p] - zb) / dx - 0.5
        fz = min(max(fz, 0.0), nz - 1.001)
        k0 = int(fz)
        tz = fz - k0
        su = 0.0
        sv = 0.0
        sw = 0.0
        ws = 0.0
        # lowest fluid value per column for extrapolation below the first fluid cell
        for dk in range(2):
            for dj in range(2):
                for di in range(2):
                    wt = ((tx if di else 1 - tx) * (ty if dj else 1 - ty) * (tz if dk else 1 - tz))
                    kk = k0 + dk
                    jj = j0 + dj
                    ii = i0 + di
                    if solid[kk, jj, ii] != 0:
                        # step up to the first fluid cell in this column (near-ground value)
                        while kk < nz - 1 and solid[kk, jj, ii] != 0 and kk <= k0 + 2:
                            kk += 1
                        if solid[kk, jj, ii] != 0:
                            continue
                        wt *= 0.999
                    su += wt * u[kk, jj, ii]
                    sv += wt * v[kk, jj, ii]
                    sw += wt * w[kk, jj, ii]
                    ws += wt
        if ws > 1e-6:
            out[p, 0] = su / ws
            out[p, 1] = sv / ws
            out[p, 2] = sw / ws
        else:
            out[p, 0] = 0.0
            out[p, 1] = 0.0
            out[p, 2] = 0.0


@dataclass
class WindField:
    u: np.ndarray            # (nz, ny, nx) amplification components (float32)
    v: np.ndarray
    w: np.ndarray
    cp: np.ndarray
    solid: np.ndarray
    ground: np.ndarray
    btop: np.ndarray
    bid: np.ndarray
    zc: np.ndarray
    dx: float
    x0: float
    y0: float
    wind_dir_deg: float
    runtime_s: float = 0.0
    path: str = ""
    facade: dict = dfield(default_factory=dict)

    @classmethod
    def from_npz(cls, path):
        d = np.load(path, allow_pickle=False)
        wf = cls(u=d["u"].astype(np.float32), v=d["v"].astype(np.float32), w=d["w"].astype(np.float32),
                 cp=d["cp"].astype(np.float32), solid=d["solid"], ground=d["ground"], btop=d["btop"],
                 bid=d["bid"], zc=d["zc"], dx=float(d["dx"]), x0=float(d["x0"]), y0=float(d["y0"]),
                 wind_dir_deg=float(d["wind_dir_deg"]), runtime_s=float(d["runtime_s"]), path=str(path))
        wf.facade = wf.compute_facade()
        return wf

    @property
    def zb(self):
        return float(self.zc[0] - self.dx / 2)

    def vec_at(self, x, y, z_agl):
        """(N,3) amplification vector (u east, v north, w up) at points; z is height above ground (m)."""
        x = np.atleast_1d(np.asarray(x, np.float64))
        y = np.atleast_1d(np.asarray(y, np.float64))
        z = np.broadcast_to(np.atleast_1d(np.asarray(z_agl, np.float64)), x.shape).copy()
        out = np.zeros((x.shape[0], 3), np.float64)
        _trilerp(self.u, self.v, self.w, self.solid, self.ground, self.x0, self.y0, self.dx, self.zb, x, y, z, out)
        return out

    def wind_at(self, x, y, z_agl):
        """Speed amplification |U(x,y,z)| / U10_freestream (vectorised)."""
        return np.linalg.norm(self.vec_at(x, y, z_agl), axis=1)

    def slice(self, h_agl=10.0, dx_out=None):
        """(ny, nx, 2) float32 horizontal (u, v) amplification at h m above ground, on the solver grid
        (or resampled to dx_out). Building interiors are 0."""
        ny, nx = self.ground.shape
        dxo = dx_out or self.dx
        nyo, nxo = int(round(ny * self.dx / dxo)), int(round(nx * self.dx / dxo))
        yy, xx = np.mgrid[0:nyo, 0:nxo]
        xs = self.x0 + (xx.ravel() + 0.5) * dxo
        ys = self.y0 + (yy.ravel() + 0.5) * dxo
        vec = self.vec_at(xs, ys, np.full(xs.shape, h_agl))
        # zero inside buildings taller than h
        ii = np.clip(((xs - self.x0) // self.dx).astype(int), 0, nx - 1)
        jj = np.clip(((ys - self.y0) // self.dx).astype(int), 0, ny - 1)
        inside = self.btop[jj, ii] > self.ground[jj, ii] + h_agl
        vec[inside] = 0
        return vec[:, :2].reshape(nyo, nxo, 2).astype(np.float32)

    def write_wind_slice(self, path, h_agl=10.0, shape8=None):
        """Web `wind_slice.f32`: ny8 x nx8 x 2 (u, v) at 10 m, amplification (x gust_ms -> m/s).
        ny8, nx8 = floor(ny4 / 2), floor(nx4 / 2) of data/grid (same as depth.u16)."""
        if shape8 is None:
            mp = GRID_DIR / "meta.json"
            if mp.exists():
                m = json.loads(mp.read_text())
                shape8 = (m["ny"] * int(m["dx"]) // 8, m["nx"] * int(m["dx"]) // 8)
        s = self.slice(h_agl, dx_out=8.0)
        if shape8 is not None:
            s = s[:shape8[0], :shape8[1]]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        s.astype("<f4").tofile(path)
        return s.shape

    def compute_facade(self):
        """Per-building mean Cp extremes on fluid voxels face-adjacent to the building's solid voxels."""
        solid = self.solid.astype(bool)
        nz, ny, nx = solid.shape
        fluid = ~solid
        cp = self.cp
        bid = self.bid
        isb = (self.btop[None] > self.zc[:, None, None]) & (self.btop[None] > self.ground[None])
        res: dict = {}
        cps, ids, zs, xys = [], [], [], []
        for dj, di in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            # fluid cell at (k,j,i) whose neighbour (k,j+dj,i+di) is building
            nbB = np.zeros_like(isb)
            idn = -np.ones((ny, nx), np.int64)
            sj = slice(max(dj, 0), ny + min(dj, 0))
            si = slice(max(di, 0), nx + min(di, 0))
            tj = slice(max(-dj, 0), ny + min(-dj, 0))
            ti = slice(max(-di, 0), nx + min(-di, 0))
            nbB[:, tj, ti] = isb[:, sj, si]
            idn[tj, ti] = bid[sj, si]
            m = nbB & fluid & (idn[None] >= 0)
            kk, jj, ii = np.nonzero(m)
            cps.append(cp[kk, jj, ii])
            ids.append(idn[jj, ii])
            zs.append(self.zc[kk] - self.ground[jj, ii])
            xys.append(np.stack([self.x0 + (ii + 0.5) * self.dx, self.y0 + (jj + 0.5) * self.dx], 1))
        # roofs: fluid cell directly above building top
        top_k = np.clip(np.searchsorted(self.zc, self.btop) , 0, nz - 1)
        jj, ii = np.nonzero((bid >= 0) & (self.btop > self.ground))
        kk = top_k[jj, ii]
        ok = fluid[kk, jj, ii]
        cps.append(cp[kk[ok], jj[ok], ii[ok]])
        ids.append(bid[jj[ok], ii[ok]])
        zs.append(self.zc[kk[ok]] - self.ground[jj[ok], ii[ok]])
        xys.append(np.stack([self.x0 + (ii[ok] + 0.5) * self.dx, self.y0 + (jj[ok] + 0.5) * self.dx], 1))
        cps = np.concatenate(cps).astype(np.float64)
        xys = np.concatenate(xys)
        ids = np.concatenate(ids)
        zs = np.concatenate(zs)
        if ids.size == 0:
            return res
        order = np.argsort(ids, kind="stable")
        ids, cps, zs, xys = ids[order], cps[order], zs[order], xys[order]
        uniq, start = np.unique(ids, return_index=True)
        for b, s, e in zip(uniq, start, list(start[1:]) + [ids.size]):
            c = cps[s:e]
            imx, imn = int(np.argmax(c)), int(np.argmin(c))
            peak_i = imn if abs(c[imn]) >= abs(c[imx]) else imx
            qs = np.quantile(np.abs(c), FACADE_Q)
            res[int(b)] = {"cp_max": round(float(c[imx]), 3), "cp_min": round(float(c[imn]), 3),
                           "cp_peak": round(float(c[peak_i]), 3), "z_peak": round(float(zs[s + peak_i]), 1),
                           "x_peak": round(float(xys[s + peak_i, 0]), 1), "y_peak": round(float(xys[s + peak_i, 1]), 1),
                           "n_cells": int(e - s), "cell_area_m2": float(self.dx ** 2),
                           "cp_abs_q": [round(float(v), 3) for v in qs]}
        return res

    def peak_pressure_pa(self, bldg_id, gust_ms):
        """Quasi-steady peak facade pressure magnitude (Pa) for a 10 m reference gust."""
        f = self.facade.get(int(bldg_id))
        if not f:
            return 0.0
        return abs(f["cp_peak"]) * 0.5 * RHO_AIR * gust_ms ** 2

    def save_png(self, path, h_agl=10.0, buildings=None):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        s = self.slice(h_agl)
        sp = np.hypot(s[..., 0], s[..., 1])
        ny, nx = sp.shape
        ext = [0, nx * self.dx, 0, ny * self.dx]
        fig, ax = plt.subplots(figsize=(10, 10 * ny / nx + 0.6), dpi=130)
        isb = self.btop > self.ground + h_agl
        spm = np.ma.masked_where(isb, sp)
        im = ax.imshow(spm, origin="lower", extent=ext, cmap="magma", vmin=0, vmax=1.6, interpolation="nearest")
        bl = np.ma.masked_where(~isb, self.btop - self.ground)
        ax.imshow(bl, origin="lower", extent=ext, cmap="Greys", vmin=-50, vmax=250, interpolation="nearest")
        st = max(1, nx // 45)
        yy, xx = np.mgrid[0:ny:st, 0:nx:st]
        ax.quiver((xx + 0.5) * self.dx, (yy + 0.5) * self.dx, s[::st, ::st, 0], s[::st, ::st, 1],
                  color="cyan", scale=40, width=0.0015, alpha=0.6)
        cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.01)
        cb.set_label(f"speed amplification |U|/U10 at {h_agl:.0f} m AGL")
        ax.set_title(f"StormSim SF LBM wind, from {self.wind_dir_deg:.0f}°, dx={self.dx:.0f} m "
                     f"(buildings grey)")
        ax.set_xlabel("x east (m)")
        ax.set_ylabel("y north (m)")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)


def get_wind(wind_dir_deg: float = 200.0, grid: dict | None = None, fast: bool = False, screens=None,
             cache: bool = True, cache_dir: Path | str | None = None, verbose=True, **kw) -> WindField:
    """Steady wind field for a storm direction (cached .npz). fast=True -> 16 m lattice."""
    if grid is None:
        grid = load_grid()
    if screens is None:
        screens = grid.get("wind_screens") or []
    dx = 16.0 if fast else 8.0
    # directions snap to 5 deg so storms share cached fields (sector width << gust direction scatter)
    wind_dir_deg = float(5.0 * round(float(wind_dir_deg) / 5.0)) % 360.0
    kw.setdefault("flow_throughs", 1.3)
    key = _grid_hash(grid, dx, kw.get("top_m", 448.0), wind_dir_deg, screens, kw["flow_throughs"])
    cdir = Path(cache_dir) if cache_dir else (GRID_DIR if not grid["meta"].get("synthetic") else ROOT / "runs" / "dev_wind")
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / f"wind_{int(round(wind_dir_deg)) % 360:03d}_{'f' if fast else ''}{key}.npz"
    if cache and path.exists():
        return WindField.from_npz(path)
    d = run_lbm(grid, wind_dir_deg, dx=dx, screens=screens, verbose=verbose, **kw)
    wf = WindField(**{k: (d[k].astype(np.float32) if k in ("u", "v", "w", "cp") else d[k]) for k in
                      ("u", "v", "w", "cp", "solid", "ground", "btop", "bid", "zc")},
                   dx=float(d["dx"]), x0=float(d["x0"]), y0=float(d["y0"]),
                   wind_dir_deg=float(d["wind_dir_deg"]), runtime_s=float(d["runtime_s"]), path=str(path))
    wf.facade = wf.compute_facade()
    if cache:
        np.savez_compressed(path, **d)
    return wf


def get_wind_plan(wind_dir_deg: float, grid: dict | None = None, screens=None, verbose=True) -> WindField:
    """8 m field for a plan with wind screens: base 8 m field x (fast screened / fast unscreened) speed ratio.

    The two 16 m runs share numerics, so their ratio isolates the screens' sheltering; applying it to the
    8 m base keeps plan vs. no-plan comparisons at the same resolution (no resolution bias in the ledger)."""
    if grid is None:
        grid = load_grid()
    screens = screens if screens is not None else (grid.get("wind_screens") or [])
    g0 = {k: v for k, v in grid.items() if k != "wind_screens"}
    d5 = float(5.0 * round(float(wind_dir_deg) / 5.0)) % 360.0
    p8 = (GRID_DIR if not g0["meta"].get("synthetic") else ROOT / "runs" / "dev_wind") / \
        f"wind_{int(d5):03d}_{_grid_hash(g0, 8.0, 448.0, d5, [], 1.3)}.npz"
    f0 = get_wind(wind_dir_deg, g0, fast=True, screens=[], verbose=verbose)
    # 8 m base if it is cached, else the 16 m field itself (never blocks on a >1 h 8 m solve)
    base = WindField.from_npz(p8) if p8.exists() else f0
    if not screens:
        return base
    f1 = get_wind(wind_dir_deg, g0, fast=True, screens=screens, verbose=verbose, init=f0, flow_throughs=0.6)
    s0 = np.sqrt(f0.u ** 2 + f0.v ** 2 + f0.w ** 2)
    s1 = np.sqrt(f1.u ** 2 + f1.v ** 2 + f1.w ** 2)
    r16 = np.clip(s1 / np.maximum(s0, 0.05), 0.0, 1.5).astype(np.float32)
    nz, ny, nx = base.u.shape
    k16 = np.clip(((base.zc - f0.zb) // f0.dx).astype(int), 0, r16.shape[0] - 1)
    j16 = np.clip(np.arange(ny) * int(round(base.dx)) // int(round(f0.dx)), 0, r16.shape[1] - 1)
    i16 = np.clip(np.arange(nx) * int(round(base.dx)) // int(round(f0.dx)), 0, r16.shape[2] - 1)
    r8 = r16[k16[:, None, None], j16[None, :, None], i16[None, None, :]]
    out = WindField(u=base.u * r8, v=base.v * r8, w=base.w * r8, cp=base.cp, solid=base.solid, ground=base.ground,
                    btop=base.btop, bid=base.bid, zc=base.zc, dx=base.dx, x0=base.x0, y0=base.y0,
                    wind_dir_deg=base.wind_dir_deg, runtime_s=f1.runtime_s, path=f1.path)
    out.facade = base.facade
    return out


def wind_at(x, y, z_agl, field: WindField | None = None):
    """Module-level convenience: speed amplification at points (default field: 200°)."""
    return (field or get_wind()).wind_at(x, y, z_agl)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=float, default=200.0)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--init", default=None, help="warm-start from a cached wind_*.npz on the same lattice")
    ap.add_argument("--flow-throughs", type=float, default=1.3)
    a = ap.parse_args()
    g = synthetic_city() if a.synthetic else load_grid()
    extra = {"init": WindField.from_npz(a.init)} if a.init else {}
    wf = get_wind(a.dir, g, fast=a.fast, cache=not a.no_cache, flow_throughs=a.flow_throughs, **extra)
    out = ROOT / "runs" / "dev_wind"
    tag = ("syn_" if g["meta"].get("synthetic") else "") + f"{int(a.dir):03d}{'_fast' if a.fast else ''}"
    wf.save_png(out / f"wind10m_{tag}.png")
    wf.write_wind_slice(out / f"wind_slice_{tag}.f32")
    cps = sorted(wf.facade.items(), key=lambda kv: kv[1]["cp_min"])[:5]
    print("runtime", round(wf.runtime_s, 1), "s; most negative Cp:", [(k, v["cp_min"], v["z_peak"]) for k, v in cps])
