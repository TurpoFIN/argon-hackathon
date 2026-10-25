"""Minimal driver around the PRODUCTION numba kernels of sim/water.py (no copies of the physics).

It reproduces the step sequence of sim.water.run_water (momentum -> swap -> outflow limiter ->
continuity, adaptive dt = alpha dx / sqrt(g h_max)) but lets a test set an arbitrary initial
water depth, terrain, roughness and boundary (closed walls, free normal-depth outflow edges,
Dirichlet "bay" cells held at a level) so the solver can be compared with analytic solutions.
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("NUMBA_NUM_THREADS", "16")
import numpy as np  # noqa: E402

from sim import water as W  # noqa: E402

G = 9.81


class SWE:
    def __init__(self, z, dx, n=0.0, h0=None, solid=None, bay=None, edge_open=None, params=None, threads=2):
        import numba
        numba.set_num_threads(threads)   # tiny test grids: thread barriers dominate on a loaded box
        p = params or W.default_params()   # production parameters (data/reference/water_params.json)
        self.p = p
        self.z = np.ascontiguousarray(np.asarray(z, np.float64))
        ny, nx = self.z.shape
        self.ny, self.nx, self.dx = ny, nx, float(dx)
        self.zf = np.ascontiguousarray(self.z.astype(np.float32))
        nn = np.broadcast_to(np.asarray(n, np.float64), (ny, nx))
        self.n2 = np.ascontiguousarray((nn ** 2).astype(np.float32))
        self.solid = np.ascontiguousarray(np.zeros((ny, nx), bool) if solid is None else solid.astype(bool))
        self.bay = np.ascontiguousarray(np.zeros((ny, nx), bool) if bay is None else bay.astype(bool))
        self.wet_ok = np.ascontiguousarray(np.where(self.solid, 0, np.where(self.bay, 2, 1)).astype(np.uint8))
        self.edge_ok = np.ascontiguousarray(np.zeros((ny, nx), bool) if edge_open is None else edge_open.astype(bool))
        self.h = np.ascontiguousarray(np.zeros((ny, nx)) if h0 is None else np.asarray(h0, np.float64).copy())
        self.qx = np.zeros((ny, nx + 1), np.float32)
        self.qy = np.zeros((ny + 1, nx), np.float32)
        self.qxn = np.zeros_like(self.qx)
        self.qyn = np.zeros_like(self.qy)
        self.r = np.ones((ny, nx))
        self.rowmax = np.zeros(ny)
        self.acc = np.zeros((ny, 6))
        self.maxd = np.zeros((ny, nx))
        self.fdur = np.zeros((ny, nx))
        z0 = np.zeros((ny, nx))
        self.pf, self.ks, self.F, self.psidt, self.fcap = z0.copy(), z0.copy(), z0.copy(), z0.copy(), np.ones((ny, nx))
        self.spillw = z0.copy()
        self.phi = np.zeros(1)
        self.basin = np.zeros((ny, nx), np.int64)
        self.t = 0.0
        self.dt = 0.1
        self.nsteps = 0
        self.edge_out = 0.0
        self.bay_gain = 0.0
        hm = float(np.where(self.solid, 0, self.h).max())
        self.hll = str(getattr(p, "scheme", "inertial")).lower() == "hll"
        if self.hll:   # full-SWE Godunov/HLL option of sim/water.py (cell-centred momentum)
            self.hu = np.zeros((ny, nx))
            self.hv = np.zeros((ny, nx))
            self.fxl, self.fxr, self.fxt = (np.zeros((ny, nx + 1)) for _ in range(3))
            self.fyl, self.fyr, self.fyt = (np.zeros((ny + 1, nx)) for _ in range(3))
            self.dt = min(p.cfl_hll * self.dx / math.sqrt(G * max(hm, 0.05)), p.dt_max)
        else:
            self.dt = min(p.alpha * self.dx / math.sqrt(G * max(hm, 0.01)), p.dt_max)

    def step(self, dt_cap=None, tide=0.0, rain_d=0.0, source=None):
        p = self.p
        dt = self.dt if dt_cap is None else min(self.dt, dt_cap)
        if self.hll:
            W._hll_flux(self.h, self.z, self.hu, self.hv, self.wet_ok, self.edge_ok, self.qx, self.fxl, self.fxr,
                        self.fxt, self.qy, self.fyl, self.fyr, self.fyt, p.hvel, self.rowmax)
        else:
            W._momentum(self.h, self.zf, self.n2, self.wet_ok, self.edge_ok, self.qx, self.qy, self.qxn, self.qyn,
                        dt, self.dx, p.theta, p.hmin, p.fr_max, self.rowmax)
            self.qx, self.qxn = self.qxn, self.qx
            self.qy, self.qyn = self.qyn, self.qy
        W._limit_ratio(self.h, self.solid, self.bay, self.qx, self.qy, self.r, dt, self.dx)
        W._limit_apply(self.qx, self.qy, self.r)
        W._continuity(self.h, self.z, self.solid, self.bay, self.qx, self.qy, dt, self.dx, rain_d, tide,
                      self.spillw, self.phi, self.basin, self.pf, self.ks, self.F, self.psidt, self.fcap,
                      self.maxd, self.fdur, p.flood_thr, self.acc)
        if self.hll:
            W._hll_momentum(self.h, self.hu, self.hv, self.wet_ok, self.n2, self.fxl, self.fxr, self.fxt, self.fyl,
                            self.fyr, self.fyt, dt, self.dx, p.hvel, p.umax)
        s = self.acc.sum(0)
        self.bay_gain += s[0] * self.dx ** 2
        self.edge_out += s[5] * self.dx ** 2
        if source is not None:          # (mask, m3/s total) point/line inflow
            m, Q = source
            self.h[m] += Q * dt / (self.dx ** 2 * m.sum())
        self.t += dt
        self.nsteps += 1
        if self.hll:
            self.dt = min(p.cfl_hll * self.dx / max(float(self.rowmax.max()), 0.05), p.dt_max)
        else:
            hm = max(float(self.rowmax.max()), 0.01)
            self.dt = min(p.alpha * self.dx / math.sqrt(G * hm), p.dt_max)
        return dt

    def run_to(self, t_end, **kw):
        tide_fn = kw.pop("tide_fn", None)
        while self.t < t_end - 1e-9:
            if tide_fn is not None:
                kw["tide"] = float(tide_fn(self.t + min(self.dt, t_end - self.t)))
            self.step(dt_cap=t_end - self.t, **kw)

    def velocity(self):
        h = self.h
        wet = h > 0.01
        hd = np.where(wet, h, 1.0)
        if self.hll:
            return np.where(wet, self.hu / hd, 0.0), np.where(wet, self.hv / hd, 0.0)
        u = np.where(wet, 0.5 * (self.qx[:, :-1] + self.qx[:, 1:]) / hd, 0.0)
        v = np.where(wet, 0.5 * (self.qy[:-1, :] + self.qy[1:, :]) / hd, 0.0)
        return u, v

    def volume(self):
        return float(np.sum(np.where(self.solid | self.bay, 0.0, self.h))) * self.dx ** 2
