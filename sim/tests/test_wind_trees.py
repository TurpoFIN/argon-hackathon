"""Tests for sim/wind.py and sim/trees.py (physics sanity + determinism).

Run: NUMBA_NUM_THREADS=8 .venv/bin/python -m pytest -q sim/tests/test_wind_trees.py
"""
import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pytest

from sim import wind as W
from sim import trees as T


def _cube_city(n=160, dx=4.0, h=48.0, half=12.0):
    yy, xx = np.mgrid[0:n, 0:n] * dx + dx / 2
    c = n * dx / 2
    m = (np.abs(xx - c) < half) & (np.abs(yy - c) < half)
    bh = np.where(m, h, 0).astype(np.float32)
    bid = np.where(m, 0, -1).astype(np.int32)
    meta = {"dx": dx, "nx": n, "ny": n, "x0": 0.0, "y0": 0.0, "synthetic": True}
    return {"z": np.zeros((n, n), np.float32), "bldg_h": bh, "bldg_id": bid,
            "cover": np.where(m, 1, 0).astype(np.uint8), "meta": meta,
            "buildings": [{"id": 0, "x": c, "y": c, "height_m": h, "year_built": 1950}]}


@pytest.fixture(scope="module")
def cube_field():
    g = _cube_city()
    d = W.run_lbm(g, 180.0, dx=8.0, top_m=160.0, flow_throughs=1.2, verbose=False, pad_m=96.0)
    wf = W.WindField(**{k: (d[k].astype(np.float32) if k in ("u", "v", "w", "cp") else d[k]) for k in
                        ("u", "v", "w", "cp", "solid", "ground", "btop", "bid", "zc")},
                     dx=float(d["dx"]), x0=float(d["x0"]), y0=float(d["y0"]), wind_dir_deg=180.0)
    wf.facade = wf.compute_facade()
    return g, d, wf


def test_lattice_constants():
    assert np.isclose(W.W.sum(), 1.0)
    for q in range(19):
        assert (W._C[W.OPP[q]] == -W._C[q]).all()
        assert W._C[W.MZ[q]][2] == -W._C[q][2]


def test_cube_flow_physics(cube_field):
    g, d, wf = cube_field
    for k in ("u", "v", "w", "cp"):
        assert np.isfinite(d[k].astype(np.float32)).all()
    # southerly wind (from 180) -> flow toward +y; upstream 10 m speed ~ free stream (amplification ~1)
    up = wf.vec_at([320.0], [60.0], [10.0])[0]
    assert up[1] > 0.6 and abs(up[0]) < 0.25
    # wake directly behind (north of) the cube is slower than the free stream at the same height
    wake = wf.wind_at([320.0], [380.0], [10.0])[0]
    side = wf.wind_at([320.0 + 60.0], [320.0], [10.0])[0]
    assert wake < 0.6 * np.linalg.norm(up)
    assert side > wake                           # corner/side acceleration vs wake
    f = wf.facade[0]
    assert f["cp_max"] > 0.2                     # windward stagnation (positive)
    assert f["cp_min"] < -0.3                    # side/roof suction (negative)


def test_lbm_deterministic():
    g = _cube_city(n=80)
    a = W.run_lbm(g, 200.0, dx=16.0, top_m=128.0, flow_throughs=0.3, verbose=False, pad_m=192.0)
    b = W.run_lbm(g, 200.0, dx=16.0, top_m=128.0, flow_throughs=0.3, verbose=False, pad_m=192.0)
    for k in ("u", "v", "w", "cp"):
        assert np.array_equal(a[k], b[k])


def test_slice_shape(cube_field):
    g, d, wf = cube_field
    s = wf.slice(10.0, dx_out=8.0)
    assert s.shape == (80, 80, 2) and s.dtype == np.float32


@dataclass
class _Storm:
    name: str = "test_storm"
    wind_dir_deg: float = 180.0

    def __post_init__(self):
        self.t = np.arange(0, 6 * 3600 + 1, 300.0)
        x = np.exp(-((self.t - 3 * 3600) / 3600.0) ** 2)
        self.gust_ms = 15 + 30 * x
        self.mean_ms = self.gust_ms / 1.45
        self.rain_mm_h = 5 + 40 * x


def _trees_and_cars(g, n=300, seed=1):
    rng = np.random.default_rng(seed)
    pts = []
    while len(pts) < n:
        x, y = rng.uniform(40, 600, 2)
        if g["bldg_h"][int(y // 4), int(x // 4)] <= 0:
            pts.append((x, y))
    trees = [{"id": i, "x": x, "y": y, "species": "Platanus", "dbh_cm": float(rng.uniform(10, 70)),
              "height_m": float(rng.uniform(6, 20)), "crown_m": float(rng.uniform(3, 12)),
              "wood_mor_mpa": 40.0} for i, (x, y) in enumerate(pts)]
    cars = [{"id": i, "x": x + 2.0, "y": y + 1.0, "heading_deg": 0.0, "value_usd": 25000} for i, (x, y) in enumerate(pts[:100])]
    inlets = [{"id": f"I{i}", "x": x + 3.0, "y": y} for i, (x, y) in enumerate(pts[::3])]
    return trees, cars, inlets


def _hash_result(r):
    h = hashlib.sha256()
    h.update(json.dumps([r.trees, r.strikes, r.glazing, r.inlet_clog, r.summary], sort_keys=True).encode())
    h.update(r.debris_frames.tobytes())
    h.update(r.debris_flight.tobytes())
    return h.hexdigest()


def test_trees_deterministic_and_sane(cube_field):
    g, d, wf = cube_field
    trees, cars, inlets = _trees_and_cars(g)
    P = T.load_params()
    st = _Storm()
    kw = dict(grid=g, trees=trees, cars=cars, buildings=g["buildings"], inlets=inlets, P=P, verbose=False,
              n_stat_leaves=2000, n_debris_export=500)
    r1 = T.run_trees(st, wf, **kw)
    r2 = T.run_trees(st, wf, **kw)
    assert _hash_result(r1) == _hash_result(r2)
    # a different plan hash changes only the noise, not the failure set
    r3 = T.run_trees(st, wf, plan_hash="other", **kw)
    f1 = {(t["id"], t["mode"], t["fail_t"]) for t in r1.trees if t["fail_t"] is not None}
    f3 = {(t["id"], t["mode"], t["fail_t"]) for t in r3.trees if t["fail_t"] is not None}
    assert f1 == f3
    assert r1.debris_frames.shape[0] == st.t.size and r1.debris_frames.shape[2] == 3
    assert np.isfinite(r1.debris_frames).all()
    hit_b = {s["tree_id"] for s in r1.strikes if s["kind"] == "building"}
    durs = []
    for t in r1.trees:
        if t["fail_t"] is not None:
            kf = t["keyframes"]
            assert 0.0 < kf[-1][0] < 8.0 and kf[-1][1] <= 90.01
            if t["id"] not in hit_b:
                assert kf[-1][1] > 89.9
                durs.append(kf[-1][0])
    if durs:
        assert 1.0 < np.median(durs) < 5.0                    # ~2-4 s rigid-body fall


def test_saturation_lowers_anchorage(cube_field):
    g, d, wf = cube_field
    trees, cars, inlets = _trees_and_cars(g)
    st = _Storm()
    kw = dict(grid=g, trees=trees, cars=[], buildings=[], inlets=[], verbose=False, n_stat_leaves=0)
    dry = T.run_trees(st, wf, saturation=np.zeros((st.t.size, len(trees))), **kw)
    wet = T.run_trees(st, wf, saturation=np.ones((st.t.size, len(trees))), **kw)
    assert wet.summary["uprooted"] >= dry.summary["uprooted"]


def test_virot_scale_and_reconfiguration():
    P = T.load_params()
    # drag grows slower than U^2 above the reconfiguration onset (Vogel exponent)
    f20, f40 = T.drag_force(np.array([20.0, 40.0]), 10.0, P)
    assert 2.0 < f40 / f20 < 4.0
    trees = [{"id": i, "dbh_cm": d, "height_m": h, "crown_m": c, "wood_mor_mpa": 45.0, "x": 0, "y": 0}
             for i, (d, h, c) in enumerate([(30, 12, 7), (50, 16, 9), (70, 20, 12)] * 50)]
    chk = T.virot_check(trees, P)
    assert 30.0 < chk["snap_ucrit_p10_50_90"][1] < 200.0          # same order as Virot (42 m/s); conservative


def test_car_stability_thresholds():
    P = T.load_params()
    hf = T.float_depth(P)
    assert 0.3 <= hf <= 0.6                                   # Shu 2011 / Martinez-Gomariz 2018 range
    fl, sl = T.car_stability(np.array([0.1, 0.25, hf + 0.05]), np.array([2.0, 0.2, 0.0]), P)
    assert not fl[0] and not sl[0]                            # below clearance-ish: stable
    assert not sl[1]                                          # slow shallow water: stable
    assert fl[2]                                              # above float depth: floats
    fl, sl = T.car_stability(np.array([0.3]), np.array([2.5]), P)
    assert sl[0]                                              # 0.3 m at 2.5 m/s slides


def test_cars_move_with_flood():
    meta = {"x0": 0.0, "y0": 0.0, "dx": 4.0}
    nf, ny, nx = 6, 50, 50
    depth = np.zeros((nf, ny, nx), np.float32)
    depth[2:] = 0.6
    u = np.zeros_like(depth)
    u[2:] = 1.0
    cars = [{"id": 0, "x": 50.0, "y": 100.0, "heading_deg": 0.0}, {"id": 1, "x": 150.0, "y": 100.0, "heading_deg": 0.0}]
    fr, st = T.run_cars(cars, np.arange(nf) * 30.0, depth, u, np.zeros_like(depth), meta)
    assert fr.shape == (nf, 2, 4)
    assert fr[-1, 0, 0] > fr[0, 0, 0] + 10.0 and st[0]["floated"]
    assert fr[-1, 0, 2] > 0.0                                 # floating above ground
