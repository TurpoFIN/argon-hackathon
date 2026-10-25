"""Tests for sim/water.py on a synthetic city (run: .venv/bin/python -m pytest sim/tests/test_water.py -q)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import hashlib  # noqa: E402

from sim.water import (PlanEdits, StubStorm, WaterParams, default_params, make_synthetic_grid,  # noqa: E402
                       run_water, validate, write_web_depth)


def _storm(**kw):
    return StubStorm(duration_s=kw.pop("duration_s", 2 * 3600.0), dt_frame_s=300.0, **kw)


def test_mass_balance_and_shapes():
    g = make_synthetic_grid()
    res = run_water(g, _storm(), verbose=False)
    assert res.mass_error_pct < 0.5, res.volumes
    assert res.depth.shape == (len(res.t_frames), g.ny, g.nx)
    assert np.isfinite(res.depth).all() and (res.depth >= 0).all()
    assert (res.max_depth[g.bldg_id >= 0] == 0).all()
    assert 0.0 <= res.saturation.min() and res.saturation.max() <= 1.0


def test_bowl_floods_and_sewer_surcharges():
    g = make_synthetic_grid()
    p = WaterParams(i_design_mm_h=5.0, store_mm=2.0)
    res = run_water(g, _storm(peak_mm_h=60.0), params=p, verbose=False)
    bowl = g.flood100 > 0
    assert res.max_depth[bowl].mean() > 3 * res.max_depth[(g.cover == 0) & ~bowl].mean()
    assert (res.surcharge_s > 0).any()
    assert (res.inlet_state == 2).any(), "surcharged sewer must back up at low inlets"
    v = validate(res, g)
    assert v["flood100"]["hit_rate_POD"] > 0.05


def test_no_rain_dry_and_bay_inflow_when_tide_high():
    g = make_synthetic_grid()
    st = _storm(peak_mm_h=0.0)
    res = run_water(g, st, verbose=False)
    assert res.max_depth[15:].max() < 1e-6  # inland ground above the tide stays dry without rain
    g.z[6:9, :] = 0.5                       # lower the shore below the tide -> Bay floods in
    res2 = run_water(g, st, verbose=False)
    assert res2.volumes["bay_to_land"] > 0 and res2.max_depth[6:9].max() > 0.5
    assert res2.mass_error_pct < 0.5


def test_plan_edits_reduce_flooding(tmp_path):
    g = make_synthetic_grid()
    p = WaterParams(i_design_mm_h=5.0, store_mm=2.0)
    st = _storm(peak_mm_h=60.0)
    base = run_water(g, st, params=p, verbose=False)
    iy, ix = np.argwhere(g.flood100 > 0)[len(np.argwhere(g.flood100 > 0)) // 2]
    x = g.x0 + (ix + .5) * g.dx
    y = g.y0 + (iy + .5) * g.dx
    pe = PlanEdits(storage=[{"x": x, "y": y, "volume_m3": 3000, "intake_m3s": 1.0}],
                   pumps=[{"ix": ix + 2, "iy": iy, "m3s": 0.5}],
                   tunnels=[{"cells": [[ix, iy], [ix + 1, iy]], "m3s": 2.0}],
                   sewer_cap_mult=2.0, inlet_mult=2.0)
    plan = run_water(g, st, edits=pe, params=p, verbose=False)
    bowl = g.flood100 > 0
    assert plan.max_depth[bowl].mean() < base.max_depth[bowl].mean()
    assert plan.volumes["plan_storage"] > 0 and plan.volumes["tunnel_to_bay"] > 0
    assert plan.mass_error_pct < 0.5
    # dict / sinks form (CityEditor style)
    plan2 = run_water(g, st, edits={"sinks": [{"kind": "pump", "x": x, "y": y, "m3s": 1.0}]}, params=p, verbose=False)
    assert plan2.volumes["pumped_to_bay"] > 0
    m = write_web_depth(base, str(tmp_path), g)
    raw = np.fromfile(tmp_path / "depth.u16", "<u2")
    assert raw.size == m["n_frames"] * m["ny"] * m["nx"]


def test_green_roof_retention():
    g = make_synthetic_grid()
    st = _storm()
    a = run_water(g, st, verbose=False)
    b = run_water(g, st, edits=PlanEdits(roof_retention_mm={i: 1000.0 for i in range(500)}), verbose=False)
    assert b.volumes["roof_to_sewer"] < 0.01 * a.volumes["roof_to_sewer"] + 1
    assert b.mass_error_pct < 0.5


def _hash(res):
    m = hashlib.sha256()
    for a in (res.depth, res.u, res.v, res.max_depth, res.saturation, res.sewer_fill, res.inlet_state):
        m.update(np.ascontiguousarray(a).tobytes())
    m.update(repr(sorted((k, float(v)) for k, v in res.volumes.items())).encode())
    return m.hexdigest()


def test_deterministic_bit_identical():
    import numba
    g = make_synthetic_grid()
    st = _storm(peak_mm_h=60.0)
    p = WaterParams(i_design_mm_h=5.0, store_mm=2.0)
    a = _hash(run_water(g, st, params=p, verbose=False))
    b = _hash(run_water(g, st, params=p, verbose=False))
    n0 = numba.get_num_threads()
    c = _hash(run_water(g, st, params=p, verbose=False, threads=max(1, n0 // 2)))
    numba.set_num_threads(n0)
    assert a == b == c


def test_clog_initial_storage_and_velocity():
    g = make_synthetic_grid()
    p = WaterParams(i_design_mm_h=5.0, store_mm=2.0)
    st = _storm(peak_mm_h=60.0)
    clogged = run_water(g, st, params=p, edits=PlanEdits(clog_factor=0.2), verbose=False)
    cleared = run_water(g, st, params=p, edits=PlanEdits(clog_factor=1.0), verbose=False)
    assert cleared.volumes["inlet_capture"] > clogged.volumes["inlet_capture"]
    full = run_water(g, st, params=p, edits=PlanEdits(sewer_initial_storage_frac=0.95), verbose=False)
    empty = run_water(g, st, params=p, edits=PlanEdits(sewer_initial_storage_frac={b: 0.0 for b in range(99)}),
                      verbose=False)
    assert full.surcharge_s.sum() > empty.surcharge_s.sum()
    assert cleared.u.shape == cleared.depth.shape and np.isfinite(cleared.u).all()
    assert cleared.max_speed.max() > 0.1          # runoff down a 4 % street moves
    assert empty.mass_error_pct < 0.5 and full.mass_error_pct < 0.5


def test_params_are_sourced():
    import json
    import os
    from sim.water import PARAMS_JSON
    d = json.load(open(PARAMS_JSON))
    p = default_params()
    for k, v in d["params"].items():
        assert v.get("source"), k
        if hasattr(p, k):
            assert getattr(p, k) == v["value"], k
    for k in WaterParams.__dataclass_fields__:
        if k != "s0":
            assert k in d["params"], f"{k} missing a source in {os.path.basename(PARAMS_JSON)}"


# ------------------------------------------------------------------ full SWE (HLL + Audusse) option
def _hll_1d(h0, z, dx, T):
    from sim import water as W
    ny, nx = h0.shape
    h = h0.astype(np.float64).copy()
    hu = np.zeros_like(h)
    hv = np.zeros_like(h)
    wet = np.ones((ny, nx), np.uint8)
    edge = np.zeros((ny, nx), bool)
    solid = np.zeros((ny, nx), bool)
    qx = np.zeros((ny, nx + 1), np.float32)
    qy = np.zeros((ny + 1, nx), np.float32)
    qxn, qyn = qx.copy(), qy.copy()
    fxl, fxr, fxt = (np.zeros((ny, nx + 1)) for _ in range(3))
    fyl, fyr, fyt = (np.zeros((ny + 1, nx)) for _ in range(3))
    r, rm, acc, z0 = np.ones((ny, nx)), np.zeros(ny), np.zeros((ny, 6)), np.zeros((ny, nx))
    n2 = np.zeros((ny, nx), np.float32)
    t, dt = 0.0, 0.01
    while t < T - 1e-9:
        d = min(dt, T - t)
        W._hll_flux(h, z, hu, hv, wet, edge, qx, fxl, fxr, fxt, qy, fyl, fyr, fyt, 1e-3, rm)
        W._limit_ratio(h, solid, solid, qx, qy, r, d, dx)
        W._continuity_fused(h, z, solid, solid, qx, qy, r, qxn, qyn, d, dx, 0.0, 0.0, z0, np.zeros(1),
                            np.zeros((ny, nx), np.int64), z0, z0, z0.copy(), z0, np.ones((ny, nx)), z0.copy(),
                            z0.copy(), 0.1, acc)
        qx, qxn = qxn, qx
        qy, qyn = qyn, qy
        W._hll_momentum(h, hu, hv, wet, n2, fxl, fxr, fxt, fyl, fyr, fyt, d, dx, 1e-3, 10.0)
        t += d
        dt = 0.45 * dx / max(rm.max(), 0.05)
    return h, hu


def test_hll_stoker_dam_break_and_lake_at_rest():
    g_, dx, T, hl, hr = 9.81, 4.0, 40.0, 1.0, 0.5
    nx = 300
    x = (np.arange(nx) + 0.5) * dx - 600
    h, _ = _hll_1d(np.repeat(np.where(x < 0, hl, hr)[None, :], 3, 0), np.zeros((3, nx)), dx, T)
    # Stoker (1957) middle state for h_r/h_l = 0.5: h_m = 0.7269 m, shock speed 2.95 m/s
    from scipy.optimize import brentq
    cl, cr = np.sqrt(g_ * hl), np.sqrt(g_ * hr)

    def f(hm):
        cm = np.sqrt(g_ * hm)
        um = 2 * (cl - cm)
        S = hm * um / (hm - hr)
        return um - (S - g_ * hr / (4 * S) * (1 + np.sqrt(1 + 8 * S * S / (g_ * hr))))
    hm = brentq(f, hr + 1e-6, hl - 1e-6)
    S = hm * 2 * (cl - np.sqrt(g_ * hm)) / (hm - hr)
    plateau = np.median(h[1][(x > 0.2 * S * T) & (x < 0.6 * S * T)])
    front = x[np.nonzero(h[1] > hr + 0.5 * (hm - hr))[0].max()]
    assert abs(plateau - hm) < 0.01 and abs(front - S * T) < 0.06 * S * T
    zb = np.repeat((0.5 * np.exp(-(x / 50) ** 2))[None, :], 3, 0)
    h2, hu2 = _hll_1d(np.maximum(0.8 - zb, 0), zb, dx, 100.0)
    assert np.abs(h2 + zb - 0.8).max() < 1e-10 and np.abs(hu2).max() < 1e-10   # well-balanced


def test_hll_scheme_mass_and_determinism():
    g = make_synthetic_grid()
    p = WaterParams(i_design_mm_h=5.0, store_mm=2.0, scheme="hll")
    st = _storm(peak_mm_h=60.0)
    a = run_water(g, st, params=p, verbose=False)
    b = run_water(g, st, params=p, verbose=False, threads=3)
    assert a.mass_error_pct < 0.5 and np.isfinite(a.depth).all() and (a.depth >= 0).all()
    assert _hash(a) == _hash(b)
    assert (a.surcharge_s > 0).any() and a.max_speed.max() > 0.1
