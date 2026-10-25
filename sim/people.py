"""StormSim SF people + emergency-response simulation (owned by the people/response agent).

    from sim.people import run_people, write_people
    res = run_people(storm, water=water_result, response=city.response, trees=tree_failures,
                     seed_key=plan_hash)
    write_people(res, "runs/<id>/frames")     # people.f32 + people.json
    res.ledger_addon   -> {life_safety_usd, injuries_usd, unmet_needs_usd, response_cost_usd}
    res.plan_objects   -> CONTRACT v2 plan_objects for the response half (shelters, zones, routes...)

Model (all parameters + sources in data/reference/people_params.json and response_costs.json):
- Population per building (buildings.json): dwelling units = floor area / 80 m2 (when unit
  counts are missing) x 90% occupied x 2.3 persons/household (ACS); hotels by floor area;
  daytime occupants by floor area per worker; present share by local hour of storm start
  (Hazus day/night pattern) and holiday factor. Ground-floor share = 1 / floors (FD2321 Nz:
  people at ground/basement level).
- Vulnerability: DataSF Flood Health Vulnerability (cne3-h93g) block groups -> elderly and
  disabled shares (FD2321 people vulnerability PV, priority evacuees), poverty -> no-car share.
- People are group agents (~25 persons) on the DataSF street-centreline graph (scipy csgraph).
  Evacuees walk 1.2 m/s (0.9 vulnerable) slowed linearly by depth, stop at 0.5 m; drivers
  6.7 m/s until 0.3 m (NWS). Drivers see water with a 10 min lag (closures fix that), so
  streets that flood in front of them trap them. Unordered people shelter in place.
- Hazard: Defra/EA FD2321 HR = d (v + 0.5) + DF; velocity from Manning on the water-surface
  slope when the water module gives none. Injuries Ninj = 2 Nz HR AV/100 PV, fatalities
  Nf = 2 Ninj HR/100 (FD2321/TR1), peak HR per agent; AV = onset 3 + nature of area +
  warning score (AlertSF alerts with lead time lower it).
- Rescue: at-risk (HR >= 0.75) and stranded agents are pulled out by boat teams (plan crews
  staged, or 2 SFFD units after a 30 min dispatch delay), trips of 6 people.
- Logistics: shelters need 3 meals + 1 gal water per person per day (FEMA); trucks loop depot
  -> shelter on the driving graph, blocked by > 0.3 m water and fallen trees; unmet needs counted.
Deterministic: one np.random.default_rng(seed) (seed = sha256(storm name | plan hash)), used
only for placement jitter and car assignment; all loops run in fixed order.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

try:
    from .response import ResponsePlan, load_costs, factor
except ImportError:  # run as script
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sim.response import ResponsePlan, load_costs, factor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = os.path.join(ROOT, "data", "grid")
REF = os.path.join(ROOT, "data", "reference")

HOME, MOVING, SHELTERED, AT_RISK, RESCUED, STRANDED = 0, 1, 2, 3, 4, 5
STATE_NAMES = ["home", "moving", "sheltered", "at_risk", "rescued", "stranded"]
WALK, DRIVE, BUS = 0, 1, 2

_P = None


def P(name):
    global _P
    if _P is None:
        with open(os.path.join(REF, "people_params.json")) as f:
            _P = json.load(f)["params"]
    return _P[name]["value"]


def seed_for(storm_name: str, plan_hash: str = "") -> int:
    return int(hashlib.sha256(f"{storm_name}|{plan_hash}".encode()).hexdigest()[:8], 16)


# =============================================================================== city
class City:
    """Static inputs: grid, buildings, street graph, vulnerability (cached per process)."""
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self):
        with open(os.path.join(GRID, "meta.json")) as f:
            m = json.load(f)
        self.meta = m
        self.nx, self.ny, self.dx = int(m["nx"]), int(m["ny"]), float(m["dx"])
        self.x0, self.y0 = float(m["x0"]), float(m["y0"])
        g = np.load(os.path.join(GRID, "city.npz"))
        self.z = g["z"].astype(np.float32).ravel()
        self.bldg = g["bldg_id"].ravel()
        self.manning = g["manning"].astype(np.float32).ravel()
        self.flood100 = g["flood100"].ravel() if "flood100" in g.files else np.zeros(self.nx * self.ny, np.uint8)
        with open(os.path.join(GRID, "buildings.json")) as f:
            self.B = json.load(f)
        self.bx = np.array([b["x"] - self.x0 for b in self.B])
        self.by = np.array([b["y"] - self.y0 for b in self.B])
        self._building_cells()
        self._graph()
        self._vulnerability()
        self._census()

    def _census(self):
        """Census 2020 block population -> per-building block index + block pop in tile."""
        from shapely import contains_xy
        from shapely.geometry import Polygon
        nb = len(self.B)
        self.b_block = np.full(nb, -1, np.int64)
        self.block_pop = np.zeros(0)
        fn = os.path.join(REF, "people_census_blocks.json")
        if not os.path.exists(fn):
            return
        with open(fn) as f:
            blocks = json.load(f)["blocks"]
        self.block_pop = np.array([b["pop"] * b["frac_in_tile"] for b in blocks])
        for k, bl in enumerate(blocks):
            for ring in bl["rings"]:
                if len(ring) < 4:
                    continue
                ins = contains_xy(Polygon(ring), self.bx, self.by) & (self.b_block < 0)
                self.b_block[ins] = k

    def depth_at_buildings(self, d):
        """Depth against each building's walls: q-quantile of street cells within 6 m."""
        q = P("building_depth_quantile")
        vals = d[self.badj]
        if not hasattr(self, "_badj_owner"):
            cnt = np.diff(np.r_[self.badj_ptr, len(self.badj)])
            self._badj_cnt = cnt
            self._badj_owner = np.repeat(np.arange(len(cnt)), cnt)
        o = np.lexsort((vals, self._badj_owner))
        sv = vals[o]
        idx = self.badj_ptr + np.floor(q * (self._badj_cnt - 1)).astype(np.int64)
        return sv[idx]

    def cell(self, x, y):
        ix = np.clip((np.asarray(x) / self.dx).astype(np.int64), 0, self.nx - 1)
        iy = np.clip((np.asarray(y) / self.dx).astype(np.int64), 0, self.ny - 1)
        return iy * self.nx + ix

    def _building_cells(self):
        """Street cells within 6 m of each building (water against its walls/doors)."""
        b2 = self.bldg.reshape(self.ny, self.nx)
        free = b2 < 0
        dist, (iy, ix) = ndimage.distance_transform_edt(free, return_indices=True)
        near = b2[iy, ix]
        sel = free & (dist <= 1.5) & (near >= 0)
        cells = np.flatnonzero(sel.ravel())
        ids = near.ravel()[cells]
        o = np.argsort(ids, kind="stable")
        cells, ids = cells[o], ids[o]
        nb = len(self.B)
        cnt = np.bincount(ids, minlength=nb)
        # buildings without an adjacent street cell: nearest free cell to the centroid
        _, (fy, fx) = ndimage.distance_transform_edt(~free, return_indices=True)
        lst_c, lst_i = [cells], [ids]
        miss = np.flatnonzero(cnt == 0)
        if len(miss):
            cc = self.cell(self.bx[miss], self.by[miss])
            cy, cx = np.divmod(cc, self.nx)
            lst_c.append(fy[cy, cx] * self.nx + fx[cy, cx])
            lst_i.append(miss)
        cells = np.concatenate(lst_c)
        ids = np.concatenate(lst_i)
        o = np.argsort(ids, kind="stable")
        self.badj = cells[o].astype(np.int64)
        cnt = np.bincount(ids[o], minlength=nb)
        self.badj_ptr = np.concatenate([[0], np.cumsum(cnt)[:-1]]).astype(np.int64)

    def _graph(self):
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, 32610, always_xy=True)
        with open(os.path.join(ROOT, "data", "raw", "extra", "streets.geojson")) as f:
            gj = json.load(f)
        node_id, nodes, edges, geoms = {}, [], {}, {}

        def nid(key, xy):
            if key not in node_id:
                node_id[key] = len(nodes)
                nodes.append(xy)
            return node_id[key]

        for f in gj["features"]:
            p = f["properties"]
            c = np.asarray(f["geometry"]["coordinates"], float)
            if len(c) < 2:
                continue
            X, Y = tr.transform(c[:, 0], c[:, 1])
            X, Y = np.asarray(X) - self.x0, np.asarray(Y) - self.y0
            fk = p.get("f_node_cnn") or f"xy{X[0]:.0f},{Y[0]:.0f}"
            tk = p.get("t_node_cnn") or f"xy{X[-1]:.0f},{Y[-1]:.0f}"
            u, v = nid(fk, (X[0], Y[0])), nid(tk, (X[-1], Y[-1]))
            if u == v:
                continue
            L = float(np.sum(np.hypot(np.diff(X), np.diff(Y))))
            k = (min(u, v), max(u, v))
            if k not in edges or L < edges[k]:
                edges[k] = L
                geoms[k] = (X, Y)
        self.node_xy = np.array(nodes, float)
        keys = sorted(edges)
        self.eu = np.array([k[0] for k in keys], np.int64)
        self.ev = np.array([k[1] for k in keys], np.int64)
        self.elen = np.maximum(np.array([edges[k] for k in keys]), 1.0)
        self.eid = {k: i for i, k in enumerate(keys)}
        self.n_nodes, self.n_edges = len(nodes), len(keys)
        self.node_tree = cKDTree(self.node_xy)
        # depth samples every ~6 m along each edge
        samp, mids = [], []
        for k in keys:
            X, Y = geoms[k]
            seg = np.hypot(np.diff(X), np.diff(Y))
            s = np.concatenate([[0], np.cumsum(seg)])
            q = np.linspace(0, s[-1], max(2, int(s[-1] / 6.0) + 1))
            sx, sy = np.interp(q, s, X), np.interp(q, s, Y)
            samp.append(self.cell(sx, sy))
            mids.append((sx[len(sx) // 2], sy[len(sy) // 2]))
        lens = np.array([len(a) for a in samp])
        self.esamp = np.concatenate(samp).astype(np.int64)
        self.esamp_ptr = np.concatenate([[0], np.cumsum(lens)[:-1]]).astype(np.int64)
        self.emid = np.array(mids)
        self.edge_tree = cKDTree(self.emid)
        self.node_cell = self.cell(self.node_xy[:, 0], self.node_xy[:, 1])
        # building -> nearest graph node
        _, self.bnode = self.node_tree.query(np.c_[self.bx, self.by])

    def _vulnerability(self):
        from shapely import contains_xy
        from shapely.geometry import Polygon
        with open(os.path.join(REF, "people_vulnerability_bg.json")) as f:
            doc = json.load(f)
        nb = len(self.B)
        eld = np.full(nb, np.nan)
        dis = np.full(nb, np.nan)
        pov = np.full(nb, np.nan)
        for bg in doc["block_groups"]:
            for ring in bg["rings"]:
                if len(ring) < 4:
                    continue
                inside = contains_xy(Polygon(ring), self.bx, self.by) & np.isnan(eld)
                eld[inside], dis[inside], pov[inside] = bg["elderly"], bg["disability"], bg["poverty"]
        allbg = doc["block_groups"]
        for arr, k in ((eld, "elderly"), (dis, "disability"), (pov, "poverty")):
            arr[np.isnan(arr)] = float(np.median([b[k] for b in allbg]))
        self.b_elderly, self.b_disab, self.b_pov = eld, dis, pov
        self.b_vuln = 1 - (1 - eld) * (1 - dis)            # share elderly or disabled
        self.b_pv = np.clip(eld + dis, 0, 1)                 # FD2321 PV (sum of shares)
        city_nc = P("no_car_household_share_city")
        self.b_nocar = np.clip(city_nc * pov / max(1e-6, float(np.median([b["poverty"] for b in allbg]))), 0.05, 0.85)

    # ------------------------------------------------------------- routing
    def csr(self, blocked: np.ndarray):
        ok = ~blocked
        u, v, w = self.eu[ok], self.ev[ok], self.elen[ok]
        return csr_matrix((np.r_[w, w], (np.r_[u, v], np.r_[v, u])), shape=(self.n_nodes, self.n_nodes))

    def edges_near_polyline(self, pts, tol=12.0):
        pts = np.asarray(pts, float)
        out = set()
        for a, b in zip(pts[:-1], pts[1:]):
            L = max(1.0, float(np.hypot(*(b - a))))
            for s in np.linspace(0, 1, int(L / 5) + 2):
                p = a + s * (b - a)
                out.update(self.edge_tree.query_ball_point(p, tol))
        return sorted(out)


# =============================================================================== water
class WaterFrames:
    """Adapter over sim/water.py WaterResult (or dict / npz path / None -> synthetic)."""

    def __init__(self, city: City, water=None, storm=None):
        self.c = city
        self.u = self.v = self.speed = None
        if water is None:
            self.t = np.asarray(storm.t, float) if storm is not None else np.arange(72) * 300.0
            self.depth = None
            self.synthetic = True
            self._synth_setup(storm)
            return
        self.synthetic = False
        if isinstance(water, str):
            water = dict(np.load(water))
        get = (lambda k: water.get(k)) if isinstance(water, dict) else (lambda k: getattr(water, k, None))
        self.depth = get("depth")
        t = get("t_frames")
        if t is None:
            t = get("t")
        self.t = np.asarray(t, float) if t is not None else np.arange(len(self.depth)) * 300.0
        for k in ("speed", "velocity"):
            if get(k) is not None:
                self.speed = get(k)
        if get("u") is not None and get("v") is not None:
            self.u, self.v = get("u"), get("v")
        if self.depth.shape[1:] != (city.ny, city.nx):
            raise ValueError(f"water depth frames {self.depth.shape} do not match grid {(city.ny, city.nx)}")

    def _synth_setup(self, storm):
        c = self.c
        m = c.flood100.astype(bool)
        if m.sum() < 100:   # no mask: Mission Creek / 17th & Folsom discs
            yy, xx = np.divmod(np.arange(c.nx * c.ny), c.nx)
            X, Y = (xx + .5) * c.dx, (yy + .5) * c.dx
            m = (np.hypot(X - 860, Y - 680) < 300) | (np.hypot(X - 2750, Y - 2000) < 350)
        zz = c.z[m]
        lo, hi = np.percentile(zz, 5), np.percentile(zz, 80)
        w = np.zeros_like(c.z)
        w[m] = np.clip((hi - c.z[m]) / max(0.5, hi - lo), 0, 1)
        w[c.bldg >= 0] = 0
        self._w = w
        n = len(self.t)
        tt = np.linspace(0, 1, n)
        self._amp = 1.1 * np.clip(np.sin(np.pi * np.clip((tt - 0.1) / 0.85, 0, 1)) ** 1.5, 0, 1)

    def frame(self, k) -> np.ndarray:
        if self.synthetic:
            return (self._w * self._amp[k]).astype(np.float32)
        return np.asarray(self.depth[k], np.float32).ravel()

    def vel(self, k, d, cells):
        """Speed (m/s) at flat cells for frame k: from the water module if given, else Manning
        on the water-surface slope (|v| = d^(2/3) S^(1/2) / n), capped at 3 m/s."""
        if self.speed is not None:
            return np.asarray(self.speed[k], np.float32).ravel()[cells]
        if self.u is not None:
            return np.hypot(np.asarray(self.u[k]).ravel()[cells], np.asarray(self.v[k]).ravel()[cells])
        c = self.c
        nx, N = c.nx, c.nx * c.ny
        eta = c.z + d
        dc = d[cells]
        out = np.zeros(len(cells), np.float32)
        wet = dc > 0.02
        if not wet.any():
            return out
        cw = cells[wet]

        def nb(off):
            j = np.clip(cw + off, 0, N - 1)
            ok = (c.bldg[j] < 0) & (d[j] > 0.0)
            return np.where(ok, eta[j], eta[cw])
        sx = (nb(1) - nb(-1)) / (2 * c.dx)
        sy = (nb(nx) - nb(-nx)) / (2 * c.dx)
        S = np.minimum(np.hypot(sx, sy), P("slope_cap"))
        v = dc[wet] ** (2 / 3) * np.sqrt(S) / np.maximum(c.manning[cw], 0.01)
        out[wet] = np.minimum(v, P("velocity_cap_ms"))
        return out


def hazard_rating(d, v):
    """FD2321: HR = d (v + 0.5) + DF, DF urban = 0 (d <= 0.25), 1 (d > 0.25)."""
    d = np.asarray(d, np.float32)
    df = np.where(d > 0.25, 1.0, 0.0)
    return d * (np.asarray(v, np.float32) + P("hr_velocity_const")) + df


def hr_class(hr):
    b = P("hr_class_bounds")
    return np.searchsorted(np.asarray(b), hr, side="right")   # 0 low, 1 some, 2 most, 3 all


# =============================================================================== result
@dataclass
class PeopleResult:
    t: np.ndarray
    frames: np.ndarray            # (n_frames, n_agents, 3) float32: x, y (local m), state
    agent_n: np.ndarray           # persons per agent
    agent_flags: np.ndarray       # bit0 vulnerable, bit1 car, bit2 ground floor, bit3 worker
    summary: dict
    ledger_addon: dict
    plan_objects: list
    runtime_s: float = 0.0
    seed: int = 0

    # convenience for sim/damage.py (people_result=...)
    @property
    def expected_fatalities(self) -> float:
        return float(self.summary["totals"]["expected_fatalities"])

    @property
    def injuries(self) -> float:
        return float(self.summary["totals"]["expected_injuries"])

    @property
    def people_affected(self) -> float:
        tt = self.summary["totals"]
        return float(tt["exposed_people"] + tt["evacuated"])

    def get(self, k, default=None):
        return getattr(self, k, default)


# =============================================================================== sim
def _parse_start(storm):
    s = getattr(storm, "start_local", "") or ""
    try:
        hh = int(s[11:13]); mmdd = s[5:10]
    except Exception:
        hh, mmdd = 2, ""
    return hh, mmdd


def build_population(city: City, storm, rng, sandbag=None):
    hh, mmdd = _parse_start(storm)
    home_f = P("home_fraction_by_hour")[hh % 24]
    work_f = P("work_fraction_by_hour")[hh % 24] * P("holiday_work_factor").get(mmdd, 1.0)
    ppu, m2u, occf = P("persons_per_household"), P("gross_m2_per_unit"), P("occupied_unit_fraction")
    wm2 = P("worker_m2")
    res = np.zeros(len(city.B))
    wrk = np.zeros(len(city.B))
    guests = np.zeros(len(city.B))
    rfa_all = np.zeros(len(city.B))
    fa_all = np.zeros(len(city.B))
    for i, b in enumerate(city.B):
        occ = b.get("occupancy") or "RES3"
        floors = max(1, int(b.get("floors") or 1))
        fa = float(b.get("floor_area_m2") or b["area_m2"] * floors)
        fa_all[i] = fa if occ != "COM10" else 0.0
        if occ.startswith("RES"):
            if occ == "RES4":
                guests[i] = fa / P("hotel_m2_per_guest")
            else:
                rfa = fa * (floors - 1) / floors if (b.get("ground_retail") and floors > 1) else fa
                rfa_all[i] = rfa
                units = b.get("units") or rfa / m2u
                res[i] = units * occf * ppu
                if b.get("ground_retail"):
                    wrk[i] = (fa - rfa) / wm2["COM1"]
        else:
            per = wm2.get(occ, 60)
            wrk[i] = fa / per if per > 0 else 0.0
    # calibrate residents to Census 2020 block population (split by residential floor area)
    calibrated = False
    if len(city.block_pop):
        kb = city.b_block
        nbk = len(city.block_pop)
        ok = kb >= 0
        rsum = np.bincount(kb[ok], weights=rfa_all[ok], minlength=nbk)
        fsum = np.bincount(kb[ok], weights=fa_all[ok], minlength=nbk)
        res_c = np.zeros_like(res)
        bp = city.block_pop[kb[ok]]
        w = np.where(rsum[kb[ok]] > 0, rfa_all[ok] / np.maximum(rsum[kb[ok]], 1e-9),
                     fa_all[ok] / np.maximum(fsum[kb[ok]], 1e-9))
        res_c[ok] = bp * w
        res = res_c
        calibrated = True
    res = res + guests
    present_res = res * home_f
    present_wrk = wrk * work_f
    pop = {"residents_total": float(res.sum()), "daytime_occupants_total": float(wrk.sum()),
           "present_residents": float(present_res.sum()), "present_workers": float(present_wrk.sum()),
           "local_hour": hh, "date": mmdd, "home_fraction": home_f, "work_fraction": work_f,
           "hotel_guests_total": float(guests.sum()), "census_calibrated": calibrated}
    total = present_res.sum() + present_wrk.sum()
    gsize = max(float(P("group_size_target")), total / P("max_agents") * 1.15)
    # categories: (worker?, vulnerable?, ground?)
    A_b, A_n, A_flag = [], [], []
    for i, b in enumerate(city.B):
        floors = max(1, int(b.get("floors") or 1))
        gshare = 1.0 / floors
        for is_w, ptot in ((0, present_res[i]), (1, present_wrk[i])):
            if ptot < 0.5:
                continue
            vshare = city.b_vuln[i] if not is_w else P("worker_vulnerable_factor") * city.b_vuln[i]
            for vul in (1, 0):
                pv_ = ptot * (vshare if vul else 1 - vshare)
                for gr in (1, 0):
                    p = pv_ * (gshare if gr else 1 - gshare)
                    # stochastic rounding: groups of exactly gsize people, total exact in expectation
                    ng = int(np.floor(p / gsize + rng.random()))
                    if ng == 0:
                        continue
                    nocar = city.b_nocar[i] if not is_w else min(0.9, city.b_nocar[i] * P("worker_nocar_factor"))
                    ncar = int(np.floor(ng * (1 - nocar) + rng.random()))
                    perm = rng.permutation(ng)
                    for j in range(ng):
                        car = 1 if perm[j] < ncar else 0
                        A_b.append(i); A_n.append(gsize)
                        A_flag.append(vul | (car << 1) | (gr << 2) | (is_w << 3))
    A_b = np.array(A_b, np.int64)
    A_n = np.array(A_n, np.float64)
    A_flag = np.array(A_flag, np.int64)
    pop["n_agents"] = int(len(A_b))
    pop["persons_per_agent_mean"] = round(float(A_n.mean()), 1)
    pop["present_total"] = float(A_n.sum())
    pop["vulnerable_present"] = float(A_n[(A_flag & 1) > 0].sum())
    pop["no_car_present"] = float(A_n[(A_flag & 2) == 0].sum())
    pop["ground_floor_present"] = float(A_n[(A_flag & 4) > 0].sum())
    return A_b, A_n, A_flag, pop


def _path_from_pred(pred, start, max_len=5000):
    out = [start]
    k = start
    while pred[k] >= 0 and len(out) < max_len:
        k = pred[k]
        out.append(k)
    return out


def _in_poly(poly, x, y):
    from shapely import contains_xy
    from shapely.geometry import Polygon
    return contains_xy(Polygon(poly), x, y)


def run_people(storm, water=None, response: Optional[ResponsePlan] = None, trees=None,
               seed_key: str = "", record_dt_s: Optional[float] = None) -> PeopleResult:
    t_wall = time.time()
    c = City.get()
    W = WaterFrames(c, water, storm)
    resp = response or ResponsePlan()
    seed = seed_for(getattr(storm, "name", "storm"), seed_key or resp.hash())
    rng = np.random.default_rng(seed)
    sandbag = resp.sandbagged()
    # population + placement use a storm-only stream so every plan faces the same people;
    # `rng` (storm + plan hash) is reserved for plan-dependent randomness
    prng = np.random.default_rng(seed_for(getattr(storm, "name", "storm"), "population"))
    A_b, A_n, A_flag, pop = build_population(c, storm, prng)
    G = len(A_b)
    vul = (A_flag & 1) > 0
    car = (A_flag & 2) > 0
    ground = (A_flag & 4) > 0
    worker = (A_flag & 8) > 0
    # positions: building centroid + jitter (within footprint scale)
    sc = np.array([math.sqrt(max(c.B[b]["area_m2"], 20.0)) * 0.3 for b in A_b])
    hx = c.bx[A_b] + (prng.random(G) - 0.5) * sc
    hy = c.by[A_b] + (prng.random(G) - 0.5) * sc
    x, y = hx.copy(), hy.copy()
    state = np.zeros(G, np.int8)
    maxhr = np.zeros(G, np.float32)
    av = np.full(G, np.nan, np.float32)
    first_exp = np.full(G, np.nan)
    floors = np.array([max(1, int(b.get("floors") or 1)) for b in c.B])
    ffe = np.array([float(b.get("first_floor_m") or 0.3) for b in c.B])
    pv = c.b_pv[A_b].astype(np.float32)
    nature = np.where(floors[A_b] >= 4, P("av_nature_of_area")["multi_storey"], P("av_nature_of_area")["typical"]).astype(np.float32)
    agent_node = c.bnode[A_b].copy()
    exp_time = np.zeros(G)
    moved_up = np.zeros(G, bool)

    # ----------------------------------------------------------- plan pieces
    shelters = resp.shelters()
    sh_bid = np.array([s[1] for s in shelters], np.int64)
    sh_cap = np.array([s[2] for s in shelters], np.float64)
    sh_open = np.array([s[3] for s in shelters], np.float64)
    sh_meals = np.array([s[4]["meals"] for s in shelters], np.float64)
    sh_water = np.array([s[4]["water_gal"] for s in shelters], np.float64)
    nS = len(shelters)
    sh_node = c.bnode[sh_bid] if nS else np.zeros(0, np.int64)
    sh_occ = np.zeros(nS)
    sh_reserved = np.zeros(nS)
    sh_need_m = np.zeros(nS); sh_need_w = np.zeros(nS)
    sh_unmet_m = np.zeros(nS); sh_unmet_w = np.zeros(nS)
    sh_del_m = np.zeros(nS); sh_del_w = np.zeros(nS)
    sh_maxd = np.zeros(nS)
    intransit_m = np.zeros(nS); intransit_w = np.zeros(nS)
    node2sh = {int(n): j for j, n in enumerate(sh_node)}
    agent_sh = np.full(G, -1, np.int64)
    agent_mode = np.zeros(G, np.int8)
    routes = {}            # gid -> [path(list of nodes), idx, offset_m]
    evac_records = []      # for plan objects
    closures = []          # (t, edge ids)
    for a in resp.of("close_road"):
        closures.append((a.t_s, c.edges_near_polyline(a.args["polyline"])))
    alerts = [(a.t_s, a.args["zone"]) for a in resp.of("alert")]
    orders = sorted(resp.of("evacuate"), key=lambda a: (a.t_s, a.id))
    order_done = set()
    med_posts = [tuple(a.args["xy"]) for a in resp.of("medical_post")]
    gen_bids = {a.args["building_id"] for a in resp.of("generator")}
    # rescue units: (stage_xy, active_from_s, active_to_s, label)
    rescue_units = []
    for a in resp.of("crew"):
        if a.args["kind"] == "rescue":
            for _ in range(a.args["n"]):
                rescue_units.append([tuple(a.args["stage_at"]), a.t_s, a.args["end_s"] or 1e12, "plan"])
    sffd_n = int(factor("sffd_baseline_rescue_units"))
    sffd_on = None      # activated after first at-risk + dispatch delay
    tree_crews = []
    for a in resp.of("crew"):
        if a.args["kind"] == "tree":
            for _ in range(a.args["n"]):
                tree_crews.append([tuple(a.args["stage_at"]), a.t_s, 0.0])
    # tree-blocked edges {edge: fail_t}
    tree_block = {}
    if trees is not None:
        tj = None
        try:
            with open(os.path.join(GRID, "trees.json")) as f:
                tj = {t["id"]: t for t in json.load(f)}
        except Exception:
            pass
        items = trees if isinstance(trees, (list, tuple)) else (getattr(trees, "trees", None) or getattr(trees, "failures", []))
        for tr in items:
            ft = tr.get("fail_t") if isinstance(tr, dict) else None
            if ft is None:
                continue
            tt = tj.get(tr.get("id")) if tj else None
            h = (tt or tr).get("height_m", 10.0)
            if h < P("tree_block_height_m"):
                continue
            tx = (tt or tr).get("x"); ty = (tt or tr).get("y")
            if tx is None:
                continue
            if tx > 1e5:
                tx, ty = tx - c.x0, ty - c.y0
            dd, e = c.edge_tree.query((tx, ty))
            if dd < 20 and (e not in tree_block or ft < tree_block[e]):
                tree_block[int(e)] = float(ft)
    tree_cleared = {}

    # supply runs: trucks
    depots = {a.id: a for a in resp.of("depot")}
    trucks = []
    for a in resp.of("supply_run"):
        dxy = depots[a.args["depot"]].args["xy"]
        dn = int(c.node_tree.query(dxy)[1])
        sj = [node2sh[int(c.bnode[b])] for b in a.args["shelters"] if int(c.bnode[b]) in node2sh]
        for k in range(a.args["trucks"]):
            trucks.append({"run": a.id, "depot_node": dn, "shelters": sj, "t0": a.t_s, "loop": a.args["loop"],
                           "phase": "idle", "busy_until": a.t_s + k * 120.0, "path": None, "idx": 0, "off": 0.0,
                           "load_m": 0.0, "load_w": 0.0, "target": -1, "node": dn, "trips": 0,
                           "track": [], "x": c.node_xy[dn, 0], "y": c.node_xy[dn, 1], "first_paths": {}})
    payload = factor("truck_payload_kg")
    meal_kg, wkg = factor("meal_kg"), factor("water_kg_per_gal")
    mpd, wpd = factor("meals_per_person_day"), factor("water_gal_per_person_day")

    # ----------------------------------------------------------- time axis
    t_frames = W.t
    nF = len(t_frames)
    dt_f = float(t_frames[1] - t_frames[0]) if nF > 1 else 300.0
    t_first = min([0.0] + [a.t_s for a in resp.actions if a.kind in ("evacuate", "supply_run", "shelter")])
    t_first = max(t_first, -72 * 3600.0)
    pre_steps = int(math.ceil(-t_first / dt_f)) if t_first < 0 else 0
    frames = np.zeros((nF, G, 3), np.float32)
    ts_counts = {s: np.zeros(nF) for s in STATE_NAMES}
    hr_ph = np.zeros(4)
    rescued_n = 0.0
    rescues_needed = np.zeros(G, bool)
    rescue_prog = np.zeros(G)
    bus_riders = 0.0
    trapped_drivers = 0.0
    walk_v0 = np.where(vul, P("walk_speed_vulnerable_ms"), P("walk_speed_ms"))
    wlim, clim = P("walk_depth_limit_m"), P("car_depth_limit_m")
    exp_d = P("exposed_depth_m")
    rth = P("rescue_hr_threshold")
    sp_c, sp_ = P("av_speed_of_onset"), P("baseline_warning_P")
    warn_base = 3 - sp_["P1"] * (sp_["P2"] + sp_["P3"])
    tgt_s = P("warning_target_s")
    e_depth_hist = []
    dij_cache = {}
    sh_flood_thr = P("shelter_flood_depth_m")
    first_atrisk_t = None
    zero_d = np.zeros(c.nx * c.ny, np.float32)

    def blocked_mask(t, e_d_known, mode):
        bl = np.zeros(c.n_edges, bool)
        for (tc, es) in closures:
            if t >= tc:
                bl[es] = True
        if e_d_known is not None:
            bl |= e_d_known > (wlim if mode == WALK else clim)
        if mode != WALK:
            for e, ft in tree_block.items():
                if t >= ft and not (e in tree_cleared and t >= tree_cleared[e]):
                    bl[e] = True
        return bl

    def route_to(start_node, targets, t, e_d_known, mode, key_extra):
        """Nearest of target shelters (indices) by street distance; returns (path, shelter) or (None, -1)."""
        if len(targets) == 0:
            return None, -1
        bl = blocked_mask(t, e_d_known, mode)
        key = (mode, tuple(targets), bl.tobytes(), key_extra)
        if key not in dij_cache:
            if len(dij_cache) > 256:
                dij_cache.clear()
            src = np.unique(sh_node[list(targets)])
            dist, pred, srcs = dijkstra(c.csr(bl), directed=False, indices=src, min_only=True, return_predecessors=True)
            dij_cache[key] = (dist, pred, srcs)
        dist, pred, srcs = dij_cache[key]
        if not np.isfinite(dist[start_node]):
            return None, -1
        path = _path_from_pred(pred, start_node)
        snode = int(srcs[start_node])
        cand = [j for j in targets if sh_node[j] == snode]
        return path, (cand[0] if cand else -1)

    def path_to_node(start, goal, t, e_d_known, mode):
        bl = blocked_mask(t, e_d_known, mode)
        key = ("node", mode, goal, bl.tobytes())
        if key not in dij_cache:
            dist, pred = dijkstra(c.csr(bl), directed=False, indices=goal, return_predecessors=True)
            dij_cache[key] = (dist, pred, None)
        dist, pred, _ = dij_cache[key]
        if not np.isfinite(dist[start]):
            return None
        return _path_from_pred(pred, start)

    def open_targets(t, allowed):
        js = [j for j in (allowed if allowed else range(nS)) if sh_cap[j] - sh_reserved[j] > 0.5]
        return js

    def step(t, dt, d, record_k):
        nonlocal rescued_n, first_atrisk_t, sffd_on, bus_riders, trapped_drivers
        e_d = np.maximum.reduceat(d[c.esamp], c.esamp_ptr)
        e_depth_hist.append(e_d)
        e_known_drive = e_depth_hist[-3] if len(e_depth_hist) >= 3 else e_depth_hist[0]
        b_d = c.depth_at_buildings(d) if d is not zero_d else np.zeros(len(c.B), np.float32)
        # ---- evacuation orders
        for a in orders:
            if a.id in order_done or t < a.t_s:
                continue
            order_done.add(a.id)
            inside = _in_poly(a.args["polygon"], hx, hy) & (state == HOME)
            if a.args["priority"] == "vulnerable":
                inside &= vul
            allowed = [j for j in range(nS) if (not a.args["to_shelters"]) or sh_bid[j] in a.args["to_shelters"]]
            gids = np.flatnonzero(inside)
            # closest first (deterministic)
            n_ok = 0.0
            n_unserved = 0.0
            for g in gids:
                tgts = open_targets(t, allowed)
                if a.args["transport"] == "walk":
                    mode = WALK
                elif car[g]:
                    mode = DRIVE
                elif vul[g] or a.args["transport"] == "bus":
                    mode = BUS
                else:
                    mode = WALK
                path, j = route_to(int(agent_node[g]), tgts, t, e_d if mode == WALK else e_known_drive, mode, 0)
                if path is None or j < 0:
                    n_unserved += A_n[g]
                    continue
                sh_reserved[j] += A_n[g]
                agent_sh[g] = j
                agent_mode[g] = mode
                routes[g] = [path, 0, 0.0]
                state[g] = MOVING
                n_ok += A_n[g]
                if mode == BUS:
                    bus_riders += A_n[g]
            evac_records.append({"order": a.id, "persons": n_ok, "unserved": n_unserved, "t": a.t_s,
                                 "polygon": a.args["polygon"], "gids": gids})
        # ---- movement
        for g in sorted(routes):
            path, i, off = routes[g]
            mode = agent_mode[g]
            budget = dt
            while budget > 0 and i < len(path) - 1:
                u_, v_ = path[i], path[i + 1]
                e = c.eid[(min(u_, v_), max(u_, v_))]
                de = float(e_d[e])
                closed_now = any(t >= tc and e in es for tc, es in closures)
                tree_now = mode != WALK and e in tree_block and t >= tree_block[e] and not (e in tree_cleared and t >= tree_cleared[e])
                lim = wlim if mode == WALK else clim
                if de > lim or closed_now or tree_now:
                    if mode != WALK and de > lim and not closed_now and float(e_known_drive[e]) <= lim and off == 0.0 and not tree_now:
                        # driver did not know: drives in and is trapped in the water
                        state[g] = STRANDED
                        x[g], y[g] = c.emid[e]
                        agent_node[g] = u_
                        trapped_drivers += A_n[g]
                        del routes[g]
                        break
                    allowed = list(range(nS))
                    newp, j = route_to(int(u_), open_targets(t, allowed) or ([agent_sh[g]] if agent_sh[g] >= 0 else []),
                                       t, e_d if mode == WALK else e_known_drive, mode, 1)
                    if newp is None or j < 0:
                        state[g] = STRANDED
                        agent_node[g] = u_
                        x[g], y[g] = c.node_xy[u_]
                        del routes[g]
                        break
                    if j != agent_sh[g]:
                        sh_reserved[agent_sh[g]] -= A_n[g]
                        sh_reserved[j] += A_n[g]
                        agent_sh[g] = j
                    path, i, off = newp, 0, 0.0
                    if len(path) < 2:
                        break
                    continue
                L = c.elen[e]
                if mode == WALK:
                    sp = walk_v0[g] * max(P("walk_min_speed_factor"), 1 - de / wlim)
                else:
                    sp = P("drive_speed_ms")
                adv = sp * budget
                if off + adv < L:
                    off += adv
                    budget = 0
                else:
                    budget -= (L - off) / sp
                    i += 1
                    off = 0.0
            if g not in routes:
                continue
            if i >= len(path) - 1:
                j = agent_sh[g]
                nd = path[-1]
                x[g], y[g] = c.node_xy[nd]
                agent_node[g] = nd
                if j >= 0 and t >= sh_open[j]:
                    state[g] = SHELTERED
                    sh_occ[j] += A_n[g]
                    ang = (g * 2.399963) % (2 * math.pi)
                    rr = 3 + 6 * math.sqrt((g % 97) / 97)
                    x[g] = c.bx[sh_bid[j]] + rr * math.cos(ang)
                    y[g] = c.by[sh_bid[j]] + rr * math.sin(ang)
                    del routes[g]
                else:
                    routes[g] = [path, i, 0.0]       # waiting at the door
            else:
                routes[g] = [path, i, off]
                a_, b_ = c.node_xy[path[i]], c.node_xy[path[i + 1]]
                e = c.eid[(min(path[i], path[i + 1]), max(path[i], path[i + 1]))]
                f = off / c.elen[e]
                x[g], y[g] = a_ + f * (b_ - a_)
                agent_node[g] = path[i]
        # ---- exposure / hazard
        hr_now = np.zeros(G, np.float32)
        outdoor = (state == MOVING) | (state == STRANDED)
        athome = (state == HOME) | (state == AT_RISK)
        hm = athome & ground
        if hm.any():
            bd = b_d[A_b[hm]]
            ff = ffe[A_b[hm]].copy()
            if sandbag:
                for k_, gid in enumerate(np.flatnonzero(hm)):
                    sb = sandbag.get(int(A_b[gid]))
                    if sb and t >= sb[1] and bd[k_] <= ff[k_] + sb[0]:
                        ff[k_] = 1e3
            din = np.maximum(0, bd - ff)
            hr_now[hm] = np.where(din > exp_d, hazard_rating(din, 0.0), 0.0)
        if outdoor.any():
            cells = c.cell(x[outdoor], y[outdoor])
            dd = d[cells]
            vv = W.vel(record_k if record_k is not None else 0, d, cells) if record_k is not None else np.zeros(len(cells))
            hr_now[outdoor] = np.where(dd > exp_d, hazard_rating(dd, vv), 0.0)
        exposed = hr_now > 0
        newly = exposed & np.isnan(first_exp)
        if newly.any():
            first_exp[newly] = t
            gi = np.flatnonzero(newly)
            lead = np.full(len(gi), -1e9)
            for (ta, zone) in alerts:
                if ta > t:
                    continue
                ins = np.ones(len(gi), bool) if zone is None else _in_poly(zone, x[gi], y[gi])
                lead = np.where(ins, np.maximum(lead, t - ta), lead)
            warn = np.where(lead >= 0, 3 - 1.0 * (np.minimum(1, lead / tgt_s) + P("warning_P3")), warn_base)
            warn = np.minimum(warn, warn_base)
            nat = np.where(outdoor[gi], P("av_nature_of_area")["high_risk"], nature[gi])
            av[gi] = sp_c + nat + warn
        np.maximum(maxhr, hr_now, out=maxhr)
        # vertical evacuation: ground-floor occupants of multi-storey buildings go upstairs
        # (warned: as soon as water arrives; un-warned: after vertical_evac_delay_s); vulnerable
        # residents and single-storey occupants cannot.
        wet_home = hm & exposed
        if wet_home.any():
            exp_time[wet_home] += dt
            can = wet_home & ~vul & (floors[A_b] >= 2)
            warned = np.zeros(G, bool)
            gi = np.flatnonzero(can)
            if len(gi) and alerts:
                for (ta, zone) in alerts:
                    if ta <= t:
                        warned[gi] |= (np.ones(len(gi), bool) if zone is None else _in_poly(zone, x[gi], y[gi]))
            up = can & (warned | (exp_time >= P("vertical_evac_delay_s")))
            ground[up] = False
            hr_now[up] = 0.0
            moved_up[up] = True
        if record_k is not None:
            cls = hr_class(hr_now[exposed])
            np.add.at(hr_ph, cls, A_n[exposed] * dt / 3600.0)
        risk = athome & (hr_now >= rth)
        state[athome & ~risk & (state == AT_RISK)] = HOME
        state[risk] = AT_RISK
        if risk.any() and first_atrisk_t is None:
            first_atrisk_t = t
        need = (state == AT_RISK) | (state == STRANDED)
        rescues_needed[need & ((hr_now >= rth) | (state == STRANDED))] = True
        # stranded people with a dry way out resume
        for g in np.flatnonzero((state == STRANDED) & (hr_now < exp_d)):
            if agent_sh[g] < 0:
                continue
            mode = agent_mode[g]
            path, j = route_to(int(agent_node[g]), [agent_sh[g]], t, e_d if mode == WALK else e_known_drive, mode, 2)
            if path is not None and j >= 0:
                routes[g] = [path, 0, 0.0]
                state[g] = MOVING
        # ---- rescue
        if first_atrisk_t is not None and sffd_on is None:
            sffd_on = first_atrisk_t + factor("sffd_dispatch_delay_s")
            for _ in range(sffd_n):
                rescue_units.append([tuple(P("sffd_station_1_xy")), sffd_on, 1e12, "sffd"])
        units = [u for u in rescue_units if u[1] <= t <= u[2]]
        q = np.flatnonzero(((state == AT_RISK) & (hr_now >= rth)) | (state == STRANDED))
        if len(q) and units:
            order_q = q[np.lexsort((q, -hr_now[q]))]
            pool = dt * len(units)        # boat-seconds available this frame (units work together)
            bspeed, bload, bcap = factor("rescue_boat_speed_ms"), factor("rescue_load_s"), factor("rescue_boat_persons_per_trip")
            shuttle = factor("rescue_boat_shuttle_m")
            for g in order_q:
                if pool <= 0:
                    break
                ui = min(range(len(units)), key=lambda k: ((x[g] - units[k][0][0]) ** 2 + (y[g] - units[k][0][1]) ** 2, k))
                sx_, sy_ = units[ui][0]
                dist = P("rescue_detour_factor") * math.hypot(x[g] - sx_, y[g] - sy_)
                # trailer the boat to the water's edge by road, then shuttle trips of 6
                need_s = dist / P("drive_speed_ms") + math.ceil(A_n[g] / bcap) * (2 * min(dist, shuttle) / bspeed + bload) - rescue_prog[g]
                use = min(pool, need_s)
                pool -= use
                rescue_prog[g] += use
                if use >= need_s - 1e-6:
                    if g in routes:
                        del routes[g]
                    state[g] = RESCUED
                    rescued_n += A_n[g]
                    # drop-off: nearest medical post, else nearest open shelter, else the staging point
                    if med_posts:
                        mp = min(med_posts, key=lambda p: (p[0] - x[g]) ** 2 + (p[1] - y[g]) ** 2)
                        x[g], y[g] = mp[0] + (g % 7) - 3, mp[1] + (g % 5) - 2
                    elif nS:
                        js = [j for j in range(nS) if t >= sh_open[j]] or list(range(nS))
                        j = min(js, key=lambda j: (c.bx[sh_bid[j]] - x[g]) ** 2 + (c.by[sh_bid[j]] - y[g]) ** 2)
                        sh_occ[j] += A_n[g]
                        agent_sh[g] = j
                        x[g], y[g] = c.bx[sh_bid[j]] + (g % 9) - 4, c.by[sh_bid[j]] + (g % 7) - 3
                    else:
                        x[g], y[g] = sx_ + (g % 7) - 3, sy_ + (g % 5) - 2
        # ---- tree crews clear blocked roads (nearest first)
        for tc in tree_crews:
            if t < tc[1]:
                continue
            tc[2] += dt
            while tc[2] >= factor("tree_crew_clear_s"):
                act = [e for e, ft in tree_block.items() if t >= ft and e not in tree_cleared]
                if not act:
                    tc[2] = 0.0
                    break
                e = min(act, key=lambda e: ((c.emid[e][0] - tc[0][0]) ** 2 + (c.emid[e][1] - tc[0][1]) ** 2, e))
                tree_cleared[e] = t
                tc[2] -= factor("tree_crew_clear_s")
        # ---- shelters: needs, flooding
        if nS:
            sh_maxd[:] = np.maximum(sh_maxd, b_d[sh_bid])
            nm = sh_occ * dt / 86400.0 * mpd
            nw = sh_occ * dt / 86400.0 * wpd
            sh_need_m[:] += nm; sh_need_w[:] += nw
            um = np.maximum(0, nm - sh_meals); uw = np.maximum(0, nw - sh_water)
            sh_meals[:] = np.maximum(0, sh_meals - nm); sh_water[:] = np.maximum(0, sh_water - nw)
            sh_unmet_m[:] += um; sh_unmet_w[:] += uw
        # ---- trucks
        for tk in trucks:
            if t < tk["t0"] or not tk["shelters"]:
                continue
            budget = dt
            guard = 0
            while budget > 0 and guard < 20:
                guard += 1
                if tk["phase"] in ("idle", "loading", "unloading"):
                    if t + (dt - budget) < tk["busy_until"]:
                        wait = min(budget, tk["busy_until"] - (t + dt - budget))
                        budget -= wait
                        continue
                    if tk["phase"] == "unloading":
                        j = tk["target"]
                        sh_meals[j] += tk["load_m"]; sh_water[j] += tk["load_w"]
                        intransit_m[j] -= tk["load_m"]; intransit_w[j] -= tk["load_w"]
                        sh_del_m[j] += tk["load_m"]; sh_del_w[j] += tk["load_w"]
                        tk["load_m"] = tk["load_w"] = 0.0
                        tk["trips"] += 1
                        if not tk["loop"] and tk["trips"] >= 1:
                            tk["phase"] = "done"
                            break
                        p = path_to_node(tk["node"], tk["depot_node"], t, e_known_drive, DRIVE)
                        if p is None:
                            tk["busy_until"] = t + dt
                            budget = 0
                            continue
                        tk.update(phase="to_depot", path=p, idx=0, off=0.0)
                        continue
                    if tk["phase"] in ("idle", "loading"):
                        if tk["phase"] == "idle":
                            tk["phase"] = "loading"
                            tk["busy_until"] = t + (dt - budget) + factor("truck_load_unload_s")
                            continue
                        # loaded: pick the shelter with the largest 24 h deficit
                        js = tk["shelters"]
                        occ_proj = np.maximum(sh_occ[js], sh_reserved[js])
                        defm = occ_proj * mpd - sh_meals[js] - intransit_m[js]
                        defw = occ_proj * wpd - sh_water[js] - intransit_w[js]
                        score = defm * meal_kg + defw * wkg
                        j = js[int(np.argmax(score))] if len(js) else -1
                        jj_ = int(np.argmax(score)) if len(js) else 0
                        dm, dw = max(float(defm[jj_]), 0.0), max(float(defw[jj_]), 0.0)
                        if dm + dw <= 0.5:
                            tk["busy_until"] = t + dt      # nothing needed yet: wait at depot
                            budget = 0
                            continue
                        kgm, kgw = dm * meal_kg, dw * wkg
                        f_ = min(1.0, payload / (kgm + kgw))
                        tk["load_m"], tk["load_w"] = dm * f_, dw * f_
                        sh_meals[j] += 0.0
                        intransit_m[j] += tk["load_m"]; intransit_w[j] += tk["load_w"]
                        p, jj = route_to(tk["node"], [j], t, e_known_drive, DRIVE, 3)
                        if p is None:
                            tk["busy_until"] = t + dt      # wait for water to recede
                            budget = 0
                            continue
                        p = p
                        tk.update(phase="to_shelter", path=p, idx=0, off=0.0, target=j)
                        if j not in tk["first_paths"]:
                            tk["first_paths"][j] = [int(n) for n in p]
                        continue
                if tk["phase"] in ("to_shelter", "to_depot"):
                    p, i, off = tk["path"], tk["idx"], tk["off"]
                    while budget > 0 and i < len(p) - 1:
                        e = c.eid[(min(p[i], p[i + 1]), max(p[i], p[i + 1]))]
                        if e_d[e] > clim or (e in tree_block and t >= tree_block[e] and not (e in tree_cleared and t >= tree_cleared[e])):
                            goal = sh_node[tk["target"]] if tk["phase"] == "to_shelter" else tk["depot_node"]
                            np_ = path_to_node(p[i], int(goal), t, e_d, DRIVE)
                            if np_ is None:
                                budget = 0
                                break
                            p, i, off = np_, 0, 0.0
                            if len(p) < 2:
                                break
                            continue
                        L = c.elen[e]
                        adv = P("drive_speed_ms") * budget
                        if off + adv < L:
                            off += adv; budget = 0
                        else:
                            budget -= (L - off) / P("drive_speed_ms"); i += 1; off = 0.0
                    tk["path"], tk["idx"], tk["off"] = p, i, off
                    tk["node"] = p[i]
                    if i < len(p) - 1:
                        a_, b_ = c.node_xy[p[i]], c.node_xy[p[i + 1]]
                        e = c.eid[(min(p[i], p[i + 1]), max(p[i], p[i + 1]))]
                        tk["x"], tk["y"] = a_ + off / c.elen[e] * (b_ - a_)
                    else:
                        tk["x"], tk["y"] = c.node_xy[p[-1]]
                        tk["phase"] = "unloading" if tk["phase"] == "to_shelter" else "idle"
                        tk["busy_until"] = t + (dt - budget) + (factor("truck_load_unload_s") if tk["phase"] == "unloading" else 0)
                elif tk["phase"] == "done":
                    break
            if record_k is not None:
                tk["track"].append([round(float(t), 1), round(float(tk["x"]), 1), round(float(tk["y"]), 1)])
        if record_k is not None:
            frames[record_k, :, 0] = x
            frames[record_k, :, 1] = y
            frames[record_k, :, 2] = state
            for si, s in enumerate(STATE_NAMES):
                ts_counts[s][record_k] = A_n[state == si].sum()
            if nS:
                occ_ts.append(sh_occ.copy())

    occ_ts = []
    occ_int = np.zeros(nS)
    # pre-landfall phase (dry streets)
    for k in range(pre_steps):
        t = t_first + k * dt_f
        step(t, dt_f, zero_d, None)
        occ_int += sh_occ * dt_f
    for k in range(nF):
        t = float(t_frames[k])
        step(t, dt_f, W.frame(k), k)
        occ_int += sh_occ * dt_f

    # ----------------------------------------------------------- results
    exp_mask = maxhr > 0
    avv = np.where(np.isnan(av), sp_c + 2 + warn_base, av)
    ninj_g = np.minimum(1.0, 2 * maxhr * avv / 100.0 * pv) * A_n * exp_mask
    nf_g = 2 * ninj_g * maxhr / 100.0
    inj, fat = float(ninj_g.sum()), float(nf_g.sum())
    vsl = P("vsl_usd")
    meals_needed, water_needed = float(sh_need_m.sum()), float(sh_need_w.sum())
    unmet_m, unmet_w = float(sh_unmet_m.sum()), float(sh_unmet_w.sum())
    peak_occ = float(np.max(np.sum(occ_ts, axis=1))) if (nS and occ_ts) else 0.0
    end_occ = float(sh_occ.sum())
    person_nights = float(occ_int.sum() / 86400.0 + end_occ * factor("projected_shelter_nights"))
    t_end = float(t_frames[-1])
    truck_hours = sum(max(0.0, t_end - tk["t0"]) / 3600.0 for tk in trucks)
    bus_hours = math.ceil(bus_riders / factor("bus_capacity")) * 1.0
    measured = {"person_nights": person_nights, "meals_delivered": float(sh_del_m.sum()),
                "water_gal_delivered": float(sh_del_w.sum()), "truck_hours": truck_hours,
                "bus_hours": bus_hours, "peak_shelter_occupancy": peak_occ}
    lines = resp.cost_lines(measured, duration_s=t_end)
    resp_cost = round(sum(l["usd"] for l in lines), 2)
    C = load_costs()["items"]
    unmet_usd = unmet_m * C["meal"]["usd"] + unmet_w * C["water_gal"]["usd"]
    ledger_addon = {"life_safety_usd": round(fat * vsl, 2),
                    "injuries_usd": round(inj * P("injury_vsl_fraction") * vsl, 2),
                    "unmet_needs_usd": round(unmet_usd, 2),
                    "response_cost_usd": resp_cost,
                    "sources": {"vsl": "people_params.json vsl_usd", "injury": "people_params.json injury_vsl_fraction",
                                "response": "response_costs.json"}}
    warnings = list(resp.warnings)
    sh_rows = []
    for j in range(nS):
        flooded = bool(sh_maxd[j] > sh_flood_thr + ffe[sh_bid[j]] - 0.3)
        if flooded:
            warnings.append(f"shelter at building {int(sh_bid[j])} floods: {sh_maxd[j]:.2f} m of water against its walls")
        if int(sh_bid[j]) not in gen_bids:
            warnings.append(f"shelter at building {int(sh_bid[j])} has no backup generator (PG&E outages in AR storms)")
        sh_rows.append({"building_id": int(sh_bid[j]), "name": resp.get(shelters[j][0]).args["name"],
                        "capacity": int(sh_cap[j]), "open_at_s": float(sh_open[j]),
                        "occupancy": [round(float(o[j]), 1) for o in occ_ts],
                        "peak": round(float(max([o[j] for o in occ_ts] or [0])), 1),
                        "max_depth_m": round(float(sh_maxd[j]), 3), "flooded": flooded,
                        "meals_needed": round(float(sh_need_m[j]), 1), "meals_delivered": round(float(sh_del_m[j]), 1),
                        "water_needed_gal": round(float(sh_need_w[j]), 1), "water_delivered_gal": round(float(sh_del_w[j]), 1),
                        "unmet_meals": round(float(sh_unmet_m[j]), 1), "unmet_water_gal": round(float(sh_unmet_w[j]), 1),
                        "generator": int(sh_bid[j]) in gen_bids})
    for r in evac_records:
        if r["unserved"] > 0:
            warnings.append(f"evacuation {r['order']}: {r['unserved']:.0f} people had no reachable shelter space")
    stranded_end = float(A_n[state == STRANDED].sum())
    atrisk_end = float(A_n[state == AT_RISK].sum())
    totals = {
        "present_population": round(pop["present_total"], 0),
        "evacuated": round(sum(r["persons"] for r in evac_records), 0),
        "sheltered_peak": round(peak_occ, 0),
        "at_risk_peak": round(float(ts_counts["at_risk"].max()), 0),
        "stranded_peak": round(float(ts_counts["stranded"].max()), 0),
        "stranded_end": round(stranded_end, 0), "at_risk_end": round(atrisk_end, 0),
        "trapped_drivers": round(trapped_drivers, 0),
        "rescues_needed": round(float(A_n[rescues_needed].sum()), 0),
        "rescued": round(rescued_n, 0),
        "exposed_people": round(float(A_n[exp_mask].sum()), 0),
        "at_risk_person_hours": {"low_lt0.75": round(hr_ph[0], 1), "danger_for_some": round(hr_ph[1], 1),
                                 "danger_for_most": round(hr_ph[2], 1), "danger_for_all": round(hr_ph[3], 1)},
        "expected_injuries": round(inj, 2), "expected_fatalities": round(fat, 3),
        "meals_needed": round(meals_needed, 0), "meals_delivered": round(float(sh_del_m.sum()), 0),
        "water_needed_gal": round(water_needed, 0), "water_delivered_gal": round(float(sh_del_w.sum()), 0),
        "unmet_meals": round(unmet_m, 0), "unmet_water_gal": round(unmet_w, 0),
        "bus_riders": round(bus_riders, 0), "person_nights": round(person_nights, 1),
        "response_cost_usd": resp_cost,
    }
    # ----------------------------------------------------------- plan objects (routes etc.)
    extra = []
    for r in evac_records:
        used = {}
        for g in r["gids"]:
            if agent_sh[g] >= 0:
                used.setdefault(int(agent_sh[g]), []).append(g)
        for j, gl in sorted(used.items()):
            gl = sorted(gl, key=lambda g: -A_n[g])
            cx_, cy_ = float(np.mean(hx[gl])), float(np.mean(hy[gl]))
            st = int(c.node_tree.query((cx_, cy_))[1])
            p, _ = route_to(st, [j], 0.0, None, WALK, 9)
            if p is None or len(p) < 2:
                continue
            npers = float(A_n[gl].sum())
            extra.append({"id": f"{r['order']}_route_s{int(sh_bid[j])}", "type": "evac_route", "phase": "pre_landfall" if r["t"] < 0 else "storm",
                          "t_s": r["t"], "geometry": {"kind": "polyline", "coords": [[round(float(c.node_xy[n, 0]), 1), round(float(c.node_xy[n, 1]), 1)] for n in p],
                                                      "z_base": "ground", "height_m": 0.3, "width_m": float(min(8, 2 + npers / 200))},
                          "style": {"color": "#ffd60a", "material": "holo"},
                          "label": f"Evacuation route -> shelter {int(sh_bid[j])} ({npers:.0f} people)", "cost_usd": 0,
                          "capacity": round(npers)})
    for ti, tk in enumerate(trucks):
        if ti % max(1, 1) != 0:
            continue
        for j, p in sorted(tk["first_paths"].items()):
            if len(p) < 2:
                continue
            extra.append({"id": f"{tk['run']}_t{ti}_s{int(sh_bid[j])}", "type": "supply_route", "phase": "pre_landfall" if tk["t0"] < 0 else "storm",
                          "t_s": tk["t0"], "geometry": {"kind": "polyline", "coords": [[round(float(c.node_xy[n, 0]), 1), round(float(c.node_xy[n, 1]), 1)] for n in p],
                                                        "z_base": "ground", "height_m": 0.2, "width_m": 2.0},
                          "style": {"color": "#f39c12", "material": "holo"}, "label": f"Supply truck route -> shelter {int(sh_bid[j])}", "cost_usd": 0})
    # dedupe identical supply routes
    seen, ex2 = set(), []
    for o in extra:
        k = (o["type"], json.dumps(o["geometry"]["coords"]))
        if k in seen:
            continue
        seen.add(k)
        ex2.append(o)
    plan_objects = resp.plan_objects(extra=ex2)
    for o in plan_objects:
        if o["type"] == "shelter":
            row = next((r for r in sh_rows if r["building_id"] == o.get("building_id")), None)
            if row:
                o["occupancy_peak"] = row["peak"]
                o["flooded"] = row["flooded"]
    line_by = {}
    for l in lines:
        line_by.setdefault(l["item"], 0.0)
        line_by[l["item"]] += l["usd"]
    summary = {
        "coords": "local m from grid SW (UTM 10N minus meta x0, y0)",
        "state_codes": {str(i): s for i, s in enumerate(STATE_NAMES)},
        "n_agents": G, "n_frames": nF, "t": [float(v) for v in t_frames],
        "seed": seed, "water": "synthetic" if W.synthetic else "sim/water.py",
        "population": pop, "totals": totals, "shelters": sh_rows,
        "timeseries_persons": {s: [round(float(v), 1) for v in ts_counts[s]] for s in STATE_NAMES},
        "trucks": [{"run": tk["run"], "trips": tk["trips"], "track": tk["track"]} for tk in trucks],
        "response_cost": {"total_usd": resp_cost, "lines": lines},
        "ledger_addon": ledger_addon,
        "warnings": warnings,
        "agent_flags_bits": {"1": "vulnerable", "2": "car", "4": "ground floor", "8": "worker/visitor"},
        "method": "FD2321 HR = d(v+0.5)+DF; Ninj = 2 Nz HR AV/100 PV; Nf = 2 Ninj HR/100 (Defra/EA FD2321/TR1 2006)",
        "sources": ["data/reference/people_params.json", "data/reference/response_costs.json",
                    "data/reference/people_vulnerability_bg.json"],
    }
    rt = time.time() - t_wall
    summary["runtime_s"] = round(rt, 2)
    return PeopleResult(t=np.asarray(t_frames), frames=frames, agent_n=A_n.astype(np.float32), agent_flags=A_flag.astype(np.uint8),
                        summary=summary, ledger_addon=ledger_addon, plan_objects=plan_objects, runtime_s=rt, seed=seed)


def write_people(res: PeopleResult, frames_dir: str, plan_objects_path: Optional[str] = None):
    """people.f32 (n_frames x n_agents x 3 float32: x, y local m, state) + people.json
    (summary + per-agent persons/flags). Optionally plan_objects.json for the response half."""
    os.makedirs(frames_dir, exist_ok=True)
    res.frames.astype("<f4").tofile(os.path.join(frames_dir, "people.f32"))
    doc = dict(res.summary)
    doc["agents"] = {"n": [round(float(v), 1) for v in res.agent_n], "flags": [int(v) for v in res.agent_flags]}
    doc["files"] = {"frames": "people.f32", "layout": "n_frames x n_agents x 3 float32 little-endian (x, y, state)"}
    with open(os.path.join(frames_dir, "people.json"), "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    if plan_objects_path:
        with open(plan_objects_path, "w") as f:
            json.dump({"objects": res.plan_objects}, f, separators=(",", ":"))


def example_plan() -> ResponsePlan:
    """The worked example from sim/RESPONSE_API.md (good response for the Mission Creek basin)."""
    ns = {}
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "RESPONSE_API.md")).read()
    code = src.split("```python example")[1].split("```")[0]
    exec(compile(code, "RESPONSE_API.md", "exec"), ns)

    class _C:
        pass
    city = _C()
    city.response = ResponsePlan()
    ns["apply"](city)
    return city.response


if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, ROOT)
    from sim.storm import load_storm
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", default="elnino_ar_2026")
    ap.add_argument("--water", default=None, help="npz with depth (n,ny,nx) and t")
    ap.add_argument("--plan", choices=["none", "example"], default="example")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    st = load_storm(a.storm)
    r = run_people(st, water=a.water, response=example_plan() if a.plan == "example" else None)
    print(json.dumps({"runtime_s": r.runtime_s, "n_agents": r.summary["n_agents"], "totals": r.summary["totals"],
                      "ledger_addon": r.ledger_addon, "warnings": r.summary["warnings"][:10]}, indent=1))
    if a.out:
        write_people(r, a.out, os.path.join(a.out, "plan_objects_response.json"))
