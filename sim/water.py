"""StormSim SF surface water + combined sewer solver (owned by the water agent).

Physics
=======
Surface flow: 2D local-inertial shallow-water equations on the 4 m grid
  * Bates, Horritt & Fewtrell (2010), "A simple inertial formulation of the shallow water
    equations for efficient two-dimensional flood inundation modelling", J. Hydrol. 387:33-45
    (the LISFLOOD-FP "acceleration" solver). Fluxes q [m2/s] on cell faces, staggered grid,
    semi-implicit Manning friction, adaptive time step dt = alpha*dx/sqrt(g*h_max), alpha=0.7.
  * de Almeida, Bates, Freer & Souvignet (2012), "Improving the stability of a simple
    formulation of the shallow water equations for 2-D flood modeling", Water Resour. Res. 48,
    W05528: theta-weighted flux diffusion q* = theta q_i + (1-theta)/2 (q_{i-1}+q_{i+1}),
    theta = 0.8, suppresses checkerboard oscillations at low friction.
  * Friction uses the full vector |q| (x flux + 4-face average of y flux, de Almeida & Bates 2013),
    face depth h_flow = max(eta) - max(z) (Bates 2010), wetting/drying threshold 1 mm, a Froude
    cap (Fr <= 3) and a mass-conserving outflow limiter (no cell can export more than it holds).
  * Buildings (bldg_id >= 0) are solid walls (zero normal flux). Bay cells (cover == 3) are a
    Dirichlet boundary held at the storm tide (NOAA 9414290, m NAVD88); land below the tide that is
    hydraulically connected (Mission Creek, Mission Bay, Embarcadero) floods from the Bay.
    Tile edges are free (normal-depth) outflow boundaries.
  Option params.scheme = "hll": full shallow-water equations, first-order Godunov finite volumes with
    the HLL Riemann solver + Audusse et al. (2004) hydrostatic reconstruction (well-balanced, positivity
    preserving, wet/dry; passes Stoker/Ritter dam breaks and lake-at-rest), ~2.5x the runtime.
Rain: storm.rain_at(t) (block-constant, mass exact) on every land cell. Roofs are connected
  impervious area: SF downspouts drain through laterals to the combined sewer, so roof rain goes
  straight into the sewer sub-basin; when that basin is surcharged the downspouts back up and the
  roof water spills onto the nearest street cell.
Infiltration: Green & Ampt (1911) capacity f = Ks (1 + psi*dtheta/F), parameters after Rawls,
  Brakensiek & Miller (1983), J. Hydraul. Eng. 109(1):62-70 (psi ~ 110 mm, porosity 0.43).
  Cumulative infiltration F fills a root-zone store D*dtheta (D = 0.6 m, street-tree wells 0.9 m);
  soil saturation = s0 + (1-s0) * min(F / (D*dtheta), 1); s0 from the storm's antecedent rain.
  Street trees on paved cells get a tree well (10 % of the cell, Ks 15 mm/h) that receives the
  cell's runoff, so tree soils saturate in a big storm (used by sim/trees.py for uprooting).
Inlets (data/grid/inlets.json): each inlet takes water from its cell at
  min(capacity, weir Cw*P*h^1.5, orifice Co*A*sqrt(2gh)) per FHWA HEC-22 (3rd ed., 2009) sag-inlet
  equations, Cw=1.66, P=3.0 m, Co=0.67, A=0.30 m2.
Combined sewer (lumped dual drainage, cf. Djordjevic et al. 1999/2005; Chen et al. 2007): the tile
  is split into sub-sewersheds (k-means on inlet x, y, 8z, every cell -> nearest inlet's basin).
  Each basin has in-pipe storage S = 6 mm x area, a conveyance capacity to the outfalls of
  Qcap = i_design x (0.9 A_imp + 0.2 A_perv) (rational method; pipes sized for SFPUC's
  5-year/3-hour level of service: Atlas 14 5-yr 3-h = 31 mm -> i_design 10.3 mm/h), plus its share of
  SF's 80 Mgal/day dry-weather sanitary flow. Of the outflow, its area share of the ~500 Mgal/day
  wet-weather treatment capacity goes to the plants, the rest is CSO discharge to the Bay
  (SFPUC combined system). The hydraulic grade line HGL(V) rises from 2 m below the lowest inlet
  (empty) to its grate (pipes full) and then by (V-S)/A_manholes. Inlets below the HGL reverse:
  submerged-orifice backflow Co*A*sqrt(2g(HGL-eta)) (uncapped, so HGL stays near ground) = sewage backups
  at the lowest inlets of a surcharged basin, exactly where SF floods (Mission Creek, 17th/Folsom).
  Upstream sewersheds outside the tile can feed edge basins (ext_upstream_m, off by default).
  Enclosed courtyards / rear yards without street access drain to the building lateral (= roof).
  Inlet capacity is reduced by leaf clogging (HEC-22 50 % sag-grate factor, scaled by nearby street trees).
All parameters (with sources) live in data/reference/water_params.json. Deterministic: every prange
  iteration writes only its own row; all sums are fixed-order numpy / serial loops.
Plans: PlanEdits (z/ksat/manning/cover rasters, inlet capacity multipliers, extra inlets,
  storage nodes, pumps, relief tunnels, per-basin sewer capacity upgrades, green-roof retention).

API
===
  grid = load_grid()                      # data/grid -> Grid (+ sewer basins)
  storm = sim.storm.load_storm("dec31_2022")
  res = run_water(grid, storm, edits=None, frame_cb=None)   # -> WaterResult
  write_web_depth(res, "runs/<id>/frames")                   # depth.u16 at 8 m, mm
  validate(res, grid)                                        # CSI vs flood100, 311 hit rates
Mass balance is checked every run (res.volumes, res.mass_error_pct).
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

os.environ.setdefault("NUMBA_NUM_THREADS", "16")
import numba as nb  # noqa: E402
from numba import njit, prange  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = 9.81
SF_LAND_KM2 = 121.4           # San Francisco land area (US Census 2020: 46.9 mi2)
MGD = 0.0438126               # 1 Mgal/day in m3/s
DRY_MGD, WET_MGD = 80.0, 500.0  # SFPUC combined system: ~80 Mgal/d dry weather, up to ~500 wet

SOURCES = [
    "Bates, Horritt & Fewtrell 2010, J. Hydrol. 387:33-45 (local-inertial SWE, LISFLOOD-FP)",
    "de Almeida, Bates, Freer & Souvignet 2012, Water Resour. Res. 48 W05528 (theta scheme)",
    "Green & Ampt 1911; Rawls, Brakensiek & Miller 1983, J. Hydraul. Eng. 109(1) (infiltration)",
    "FHWA HEC-22 Urban Drainage Design Manual, 3rd ed. 2009 (inlet weir/orifice equations)",
    "Chen, Djordjevic, Leandro & Savic 2007 (dual-drainage surface-sewer coupling)",
    "SFPUC: combined sewer ~80 Mgal/day dry weather, up to ~500 Mgal/day treated in storms",
    "SFPUC 100-Year Storm Flood Risk Map (DataSF jzu3-4yxp) and SF 311 31.12.2022 (validation)",
]


# =============================================================================================
# Parameters, grid, plan edits
# =============================================================================================
@dataclass
class WaterParams:
    scheme: str = "inertial"      # "inertial" (Bates 2010 / de Almeida 2012) or "hll" (full SWE, Audusse 2004)
    alpha: float = 0.6            # CFL coefficient (Bates 2010; 0.7 unstable in 2D, validation B1)
    cfl_hll: float = 0.45         # Courant number of the 2D first-order Godunov scheme (<= 0.5)
    hvel: float = 1e-3            # HLL: below this depth velocity is zero (wet/dry)
    umax: float = 10.0            # HLL: velocity cap, m/s
    theta: float = 0.8            # de Almeida 2012 weighting
    hmin: float = 1e-3            # wet/dry threshold for face flow depth, m
    fr_max: float = 3.0           # Froude cap
    dt_max: float = 5.0
    flood_thr: float = 0.10       # m, for flood_duration_s
    # sewer
    i_design_mm_h: float = 10.3   # 5-yr 3-h Atlas 14 depth (31 mm) / 3 h: SFPUC level of service
    c_imp: float = 0.9
    c_perv: float = 0.2
    store_mm: float = 6.0         # in-pipe storage as depth over basin area
    pipe_depth_m: float = 2.0     # lowest-inlet grate to pipe invert
    a_manhole_m2: float = 1.0     # free surface area per inlet/manhole once surcharged
    v0_frac: float = 0.15         # initial pipe fill (dry-weather flow)
    basin_km2: float = 0.6        # target sub-sewershed size
    # inlets (HEC-22)
    cw: float = 1.66
    perim_m: float = 3.0
    co: float = 0.67
    a_open_m2: float = 0.30
    inlet_mult: float = 1.0       # global multiplier on inlet capacities
    # soil (Rawls et al. 1983)
    psi_m: float = 0.11
    porosity: float = 0.43
    root_m: float = 0.6
    pit_root_m: float = 0.9
    pit_frac: float = 0.10
    pit_ksat_mm_h: float = 15.0
    s0: Optional[float] = None    # antecedent saturation; None -> from storm.antecedent_rain_mm
    # boundaries
    intertidal_z: float = 0.5     # open cells below this touching the Bay are Bay (m NAVD88)
    edge_freeboard_m: float = 0.5
    # inlet clogging by leaves (HEC-22 50 % sag-grate design clogging)
    clog_max: float = 0.5
    clog_tree_radius_m: float = 15.0
    clog_trees_full: float = 3.0
    court_max_m2: float = 5000.0      # enclosed open pockets up to this size drain to the lateral
    ext_upstream_m: float = 0.0       # upstream sewershed length beyond inflowing tile edges
    c_ext: float = 0.7                # runoff coefficient of that upstream (residential) area
    # SF-wide sewer scale
    sf_land_km2: float = 121.4
    dry_mgd: float = 80.0
    wet_mgd: float = 500.0


_FUSED = True   # limiter fused into the continuity kernel (bit-identical to the 2-pass form, 1 fewer launch)

PARAMS_JSON = os.path.join(ROOT, "data", "reference", "water_params.json")


def default_params(path: str = PARAMS_JSON) -> WaterParams:
    """WaterParams from data/reference/water_params.json (every value carries its source)."""
    p = WaterParams()
    if os.path.exists(path):
        for k, v in json.load(open(path))["params"].items():
            if k in p.__dataclass_fields__:
                setattr(p, k, type(getattr(p, k))(v["value"]) if getattr(p, k) is not None else v["value"])
    return p


@dataclass
class Grid:
    nx: int
    ny: int
    dx: float
    x0: float
    y0: float
    z: np.ndarray
    bldg_id: np.ndarray
    cover: np.ndarray
    manning: np.ndarray
    ksat: np.ndarray
    flood100: np.ndarray
    bldg_h: Optional[np.ndarray] = None
    inlets: dict = field(default_factory=dict)       # arrays: id(list), ix, iy, cap, type(list)
    validation: dict = field(default_factory=dict)
    tree_ix: Optional[np.ndarray] = None
    tree_iy: Optional[np.ndarray] = None
    meta: dict = field(default_factory=dict)
    basin: Optional[np.ndarray] = None               # (ny, nx) int32 sub-sewershed id
    basin_info: Optional[list] = None

    def to_ix(self, x, y):
        ix = np.clip(np.floor((np.asarray(x, float) - self.x0) / self.dx).astype(int), 0, self.nx - 1)
        iy = np.clip(np.floor((np.asarray(y, float) - self.y0) / self.dx).astype(int), 0, self.ny - 1)
        return ix, iy


@dataclass
class PlanEdits:
    """Plan changes the water solver understands (CityEditor may hand over this or a dict).

    Rasters (ny, nx) replace the grid's. Point items take either ix/iy or x/y (UTM m).
      inlet_mult: float | array per grid inlet | {inlet_id: factor}
      extra_inlets: [{x, y, capacity_m3s}]
      storage: [{x, y, volume_m3, intake_m3s}]         surface water -> cistern/vault until full
      pumps:   [{x, y, m3s}]                             surface water -> Bay
      tunnels: [{path_xy: [[x,y],...], m3s}]             relief tunnel: extra sewer conveyance to the
               Bay, split over the basins the path crosses (also accepts cells=[[ix,iy],...])
      sewer_cap_mult / sewer_cap_add_m3s: float or {basin_id: value}
      roof_retention_mm: {bldg_id: mm} or (ny, nx) array (green roofs / rain barrels)
      clog_factor: remaining inlet capacity 0..1 replacing the leaf-clogging baseline
               (float | array per inlet | {inlet_id: f}); drain clearing = 1.0
      sewer_initial_storage_frac: pre-storm sewer fill V0/S (float | {basin_id: frac}); 0 = drawn down
    """
    z: Optional[np.ndarray] = None
    ksat: Optional[np.ndarray] = None
    manning: Optional[np.ndarray] = None
    cover: Optional[np.ndarray] = None
    bldg_id: Optional[np.ndarray] = None
    inlet_mult: Any = None
    extra_inlets: list = field(default_factory=list)
    storage: list = field(default_factory=list)
    pumps: list = field(default_factory=list)
    tunnels: list = field(default_factory=list)
    sewer_cap_mult: Any = None
    sewer_cap_add_m3s: Any = None
    roof_retention_mm: Any = None
    clog_factor: Any = None                  # remaining capacity 0..1: float | array | {inlet_id: f}; 1 = cleared
    sewer_initial_storage_frac: Any = None   # pre-storm fill V0/S: float | {basin_id: frac} (drawdown)


@dataclass
class WaterResult:
    t_frames: np.ndarray
    depth: np.ndarray               # (n, ny, nx) float32, m; buildings and bay = 0
    u: np.ndarray                   # (n, ny, nx) float32 depth-averaged velocity east, m/s (0 where h<1 cm)
    v: np.ndarray                   # (n, ny, nx) float32 velocity north, m/s
    max_speed: np.ndarray           # (ny, nx) max |u| over frames
    max_hazard: np.ndarray          # (ny, nx) max d*(|u|+0.5) over frames (DEFRA FD2321 hazard term)
    saturation: np.ndarray          # (n, ny, nx) float32, 0..1
    max_depth: np.ndarray           # (ny, nx) float32
    flood_duration_s: np.ndarray    # (ny, nx) float32, time with depth > flood_thr
    sewer_fill: np.ndarray          # (n, n_basins) V/S (>1 = surcharged)
    sewer_hgl: np.ndarray           # (n, n_basins) m NAVD88
    surcharge_s: np.ndarray         # (n_basins,) seconds surcharged
    inlet_state: np.ndarray         # (n, n_inlets) int8 0 ok/idle, 1 full, 2 backflow
    inlet_q: np.ndarray             # (n, n_inlets) float32 m3/s (+ into sewer, - backflow)
    inlet_ix: np.ndarray
    inlet_iy: np.ndarray
    inlet_ids: list
    basin: np.ndarray
    basin_info: list
    volumes: dict
    mass_error_pct: float
    runtime_s: float
    n_steps: int
    params: dict
    tide_m: np.ndarray
    rain_mm_h: np.ndarray
    dx: float = 4.0
    x0: float = 0.0
    y0: float = 0.0
    validation: dict = field(default_factory=dict)

    def depth_at(self, x, y, k=None):
        ix = np.clip(((np.asarray(x) - self.x0) // self.dx).astype(int), 0, self.depth.shape[2] - 1)
        iy = np.clip(((np.asarray(y) - self.y0) // self.dx).astype(int), 0, self.depth.shape[1] - 1)
        return self.max_depth[iy, ix] if k is None else self.depth[k, iy, ix]

    def summary(self) -> dict:
        return {"runtime_s": round(self.runtime_s, 1), "n_steps": self.n_steps,
                "mass_error_pct": round(self.mass_error_pct, 4),
                "volumes_m3": {k: round(v, 1) for k, v in self.volumes.items()},
                "flooded_area_m2_gt10cm": float((self.max_depth > 0.10).sum() * self.dx ** 2),
                "basins_surcharged": int((self.surcharge_s > 0).sum()),
                "inlets_backflow_ever": int((self.inlet_state == 2).any(0).sum()),
                "validation": self.validation}


def load_grid(path: Optional[str] = None, params: Optional[WaterParams] = None) -> Grid:
    path = path or os.path.join(ROOT, "data", "grid")
    meta = json.load(open(os.path.join(path, "meta.json")))
    d = np.load(os.path.join(path, "city.npz"))
    g = Grid(nx=int(meta["nx"]), ny=int(meta["ny"]), dx=float(meta["dx"]), x0=float(meta["x0"]),
             y0=float(meta["y0"]), z=d["z"].astype(np.float32), bldg_id=d["bldg_id"].astype(np.int32),
             cover=d["cover"].astype(np.uint8), manning=d["manning"].astype(np.float32),
             ksat=d["ksat"].astype(np.float32), flood100=d["flood100"].astype(np.uint8),
             bldg_h=d["bldg_h"] if "bldg_h" in d else None, meta=meta)
    inl = json.load(open(os.path.join(path, "inlets.json")))
    ix = np.array([i["ix"] for i in inl], np.int64)
    iy = np.array([i["iy"] for i in inl], np.int64)
    g.inlets = {"id": [i["id"] for i in inl], "ix": ix, "iy": iy,
                "cap": np.array([i["capacity_m3s"] for i in inl], np.float64),
                "type": [i.get("type", "") for i in inl]}
    vp = os.path.join(path, "validation.json")
    if os.path.exists(vp):
        g.validation = json.load(open(vp))
    tp = os.path.join(path, "trees.json")
    if os.path.exists(tp):
        tr = json.load(open(tp))
        g.tree_ix = np.array([t["ix"] for t in tr], np.int64)
        g.tree_iy = np.array([t["iy"] for t in tr], np.int64)
    build_basins(g, params or default_params())
    return g


def inlet_clog_baseline(grid: Grid, p: Optional[WaterParams] = None) -> np.ndarray:
    """Remaining capacity fraction per grid inlet from leaf clogging: 1 - clog_max * min(1, n/n_full),
    n = street trees within clog_tree_radius_m (HEC-22 50 % sag-grate clogging at full exposure)."""
    p = p or default_params()
    n_in = len(grid.inlets["ix"])
    if grid.tree_ix is None or not len(grid.tree_ix) or p.clog_max <= 0:
        return np.ones(n_in)
    from scipy.spatial import cKDTree
    tr = cKDTree(np.stack([grid.tree_ix, grid.tree_iy], 1) * grid.dx)
    pts = np.stack([grid.inlets["ix"], grid.inlets["iy"]], 1) * grid.dx
    cnt = np.array([len(x) for x in tr.query_ball_point(pts, p.clog_tree_radius_m)], float)
    return 1.0 - p.clog_max * np.minimum(1.0, cnt / p.clog_trees_full)


def make_synthetic_grid(nx=120, ny=100, dx=4.0, seed=0, n_inlets=40, bay_rows=6) -> Grid:
    """Sloped street grid falling toward a bay at the south edge, a depression (old creek bed),
    building blocks, a park and inlets along the streets. For tests / development."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:ny, 0:nx].astype(np.float64)
    z = 1.0 + 0.04 * dx * yy + 0.01 * dx * np.abs(xx - nx / 2)          # 4 % slope to the south
    cy, cx = ny * 0.35, nx * 0.55
    z -= 2.5 * np.exp(-(((yy - cy) / (ny * 0.12)) ** 2 + ((xx - cx) / (nx * 0.15)) ** 2))  # bowl
    z = z.astype(np.float32)
    cover = np.zeros((ny, nx), np.uint8)
    bldg = -np.ones((ny, nx), np.int32)
    k = 0
    for by in range(bay_rows + 4, ny - 3, 14):
        for bx in range(3, nx - 3, 14):
            if abs(by - cy) < 6 and abs(bx - cx) < 8:
                continue
            bldg[by:by + 10, bx:bx + 10] = k
            k += 1
    cover[bldg >= 0] = 1
    cover[ny - 20:ny - 8, 4:30][bldg[ny - 20:ny - 8, 4:30] < 0] = 2
    cover[:bay_rows] = 3
    z[:bay_rows] = -2.0
    bldg[:bay_rows] = -1
    manning = np.choose(cover, [0.015, 0.015, 0.035, 0.02]).astype(np.float32)
    ksat = np.where(cover == 2, 20.0, np.where(cover == 0, 0.5, 0.0)).astype(np.float32)
    street = (bldg < 0) & (cover != 3)
    cand = np.argwhere(street & (np.arange(ny)[:, None] > bay_rows + 1))
    sel = cand[rng.choice(len(cand), n_inlets, replace=False)]
    g = Grid(nx=nx, ny=ny, dx=dx, x0=500000.0, y0=4180000.0, z=z, bldg_id=bldg, cover=cover,
             manning=manning, ksat=ksat, flood100=np.zeros((ny, nx), np.uint8))
    g.inlets = {"id": [f"syn{i}" for i in range(n_inlets)], "ix": sel[:, 1].astype(np.int64),
                "iy": sel[:, 0].astype(np.int64), "cap": np.full(n_inlets, 0.05), "type": ["syn"] * n_inlets}
    tr = cand[rng.choice(len(cand), 30, replace=False)]
    g.tree_iy, g.tree_ix = tr[:, 0].astype(np.int64), tr[:, 1].astype(np.int64)
    g.flood100[(np.abs(yy - cy) < ny * 0.08) & (np.abs(xx - cx) < nx * 0.1) & street] = 1
    build_basins(g, WaterParams(basin_km2=0.02))
    return g


@dataclass
class StubStorm:
    """Same fields as sim.storm.Storm (CONTRACT.md) for tests."""
    name: str = "stub"
    duration_s: float = 6 * 3600.0
    dt_frame_s: float = 300.0
    peak_mm_h: float = 50.0
    t: np.ndarray = None
    rain_mm_h: np.ndarray = None
    tide_m: np.ndarray = None
    antecedent_rain_mm: float = 20.0

    def __post_init__(self):
        n = int(round(self.duration_s / self.dt_frame_s))
        self.t = np.arange(n) * self.dt_frame_s
        tp = 0.5 * self.duration_s
        self.rain_mm_h = self.peak_mm_h * np.exp(-0.5 * ((self.t - tp) / (0.15 * self.duration_s)) ** 2)
        self.tide_m = 1.6 + 0.4 * np.cos(2 * np.pi * (self.t - tp) / (12.42 * 3600))

    def rain_at(self, ts):
        i = np.clip((np.asarray(ts, float) // self.dt_frame_s).astype(int), 0, len(self.t) - 1)
        return self.rain_mm_h[i]

    def tide_at(self, ts):
        return np.interp(ts, self.t, self.tide_m)

    def total_rain_mm(self):
        return float(np.sum(self.rain_mm_h) * self.dt_frame_s / 3600.0)


# =============================================================================================
# Sewer sub-basins
# =============================================================================================
def _nearest_open(open_mask):
    from scipy import ndimage
    _, idx = ndimage.distance_transform_edt(~open_mask, return_indices=True)
    return idx  # (2, ny, nx) nearest open cell for every cell


def build_basins(g: Grid, p: WaterParams):
    """Sub-sewersheds: k-means of inlets on (x, y, 8 z) so basins follow the terrain, every land
    cell joins the basin of its nearest inlet. Deterministic (seeded)."""
    from scipy import ndimage
    from scipy.cluster.vq import kmeans2
    land = g.cover != 3
    ix, iy = g.inlets["ix"], g.inlets["iy"]
    n_in = len(ix)
    area_km2 = land.sum() * g.dx ** 2 / 1e6
    K = int(max(2, min(n_in, round(area_km2 / p.basin_km2))))
    pts = np.stack([ix * g.dx, iy * g.dx, 8.0 * g.z[iy, ix]], 1).astype(np.float64)
    rng = np.random.default_rng(0)
    np.random.seed(0)
    _, lab = kmeans2(pts, K, minit="++", seed=rng, iter=30)
    # compact labels
    uniq, lab = np.unique(lab, return_inverse=True)
    K = len(uniq)
    imask = np.zeros(g.z.shape, bool)
    imask[iy, ix] = True
    lab_r = -np.ones(g.z.shape, np.int32)
    lab_r[iy, ix] = lab
    _, (ny_i, nx_i) = ndimage.distance_transform_edt(~imask, return_indices=True)
    basin = lab_r[ny_i, nx_i].astype(np.int32)
    g.basin = basin
    g.inlets["basin"] = lab.astype(np.int64)
    info = []
    for b in range(K):
        m = basin == b
        sel = lab == b
        info.append({"id": b, "n_inlets": int(sel.sum()),
                     "area_m2": float((m & land).sum() * g.dx ** 2),
                     "cx": float(g.x0 + (np.mean(ix[sel]) + 0.5) * g.dx),
                     "cy": float(g.y0 + (np.mean(iy[sel]) + 0.5) * g.dx),
                     "z_low": float(g.z[iy[sel], ix[sel]].min())})
    g.basin_info = info
    return basin


# =============================================================================================
# Numba kernels
# =============================================================================================
@njit(inline="always", fastmath=True)
def _face_q(q0, qm1, qp1, qcross, ea, eb, za, zb, nn, dt, dx, theta, hmin, frmax):
    """Bates et al. (2010) eq. 11 with de Almeida et al. (2012) theta-weighting and vector |q|."""
    hf = max(ea, eb) - max(za, zb)
    if hf <= hmin:
        return 0.0, 0.0
    g = 9.81
    qt = theta * q0 + 0.5 * (1.0 - theta) * (qm1 + qp1)
    qm = math.sqrt(q0 * q0 + qcross * qcross)
    # hf^(1/3) by two Newton steps from hf^(1/4): < 2 % error above 1 cm, 3x cheaper than cbrt
    y = math.sqrt(math.sqrt(hf))
    y = (2.0 * y + hf / (y * y)) * (1.0 / 3.0)
    y = (2.0 * y + hf / (y * y)) * (1.0 / 3.0)
    q = (qt - g * hf * dt * (eb - ea) / dx) / (1.0 + g * dt * nn * qm / (hf * hf * y))
    lim2 = frmax * frmax * g * hf * hf * hf          # Froude cap |q| <= Fr h sqrt(g h)
    if q * q > lim2:
        q = math.copysign(math.sqrt(lim2), q)
    return q, hf


@njit(inline="always", fastmath=True)
def _edge_q(hc, zc, zin, n2c, dx, hmin, frmax, open_):
    """Free outflow at the tile edge: normal depth q = h^5/3 S^1/2 / n with the outward ground slope."""
    if not open_ or hc <= hmin:
        return 0.0
    s = (zin - zc) / dx
    if s <= 0.0:
        return 0.0
    q = hc * np.cbrt(hc * hc) * math.sqrt(s / n2c)
    lim = frmax * hc * math.sqrt(9.81 * hc)
    return q if q < lim else lim


@njit(parallel=True, cache=True, fastmath=True)
def _momentum(h, z, n2, wet_ok, edge_ok, qx, qy, qxn, qyn, dt, dx, theta, hmin, frmax, rowmax):
    """wet_ok: uint8 1 = open land, 2 = bay (Dirichlet), 0 = solid. edge_ok: edge cell may drain out."""
    ny, nx = h.shape
    for i in prange(ny):
        hm = 0.0
        # x faces, face j between (i, j-1) and (i, j)
        qxn[i, 0] = -_edge_q(h[i, 0], z[i, 0], z[i, 1], n2[i, 0], dx, hmin, frmax,
                             wet_ok[i, 0] == 1 and edge_ok[i, 0])
        qxn[i, nx] = _edge_q(h[i, nx - 1], z[i, nx - 1], z[i, nx - 2], n2[i, nx - 1], dx, hmin, frmax,
                             wet_ok[i, nx - 1] == 1 and edge_ok[i, nx - 1])
        for j in range(1, nx):
            a = j - 1
            wa = wet_ok[i, a]
            wb = wet_ok[i, j]
            if wa == 0 or wb == 0 or (wa == 2 and wb == 2) or (h[i, a] <= hmin and h[i, j] <= hmin):
                qxn[i, j] = 0.0
                continue
            qc = 0.25 * (qy[i, a] + qy[i, j] + qy[i + 1, a] + qy[i + 1, j])
            q, hf = _face_q(qx[i, j], qx[i, j - 1], qx[i, j + 1], qc, z[i, a] + h[i, a], z[i, j] + h[i, j],
                            z[i, a], z[i, j], 0.5 * (n2[i, a] + n2[i, j]), dt, dx, theta, hmin, frmax)
            qxn[i, j] = q
            if hf > hm:
                hm = hf
        # y faces, face i between (i-1, j) and (i, j); bottom edge (i=0) and top edge (face ny)
        if i == 0:
            for j in range(nx):
                qyn[0, j] = -_edge_q(h[0, j], z[0, j], z[1, j], n2[0, j], dx, hmin, frmax,
                                     wet_ok[0, j] == 1 and edge_ok[0, j])
        else:
            a = i - 1
            for j in range(nx):
                wa = wet_ok[a, j]
                wb = wet_ok[i, j]
                if wa == 0 or wb == 0 or (wa == 2 and wb == 2) or (h[a, j] <= hmin and h[i, j] <= hmin):
                    qyn[i, j] = 0.0
                    continue
                qc = 0.25 * (qx[a, j] + qx[a, j + 1] + qx[i, j] + qx[i, j + 1])
                q, hf = _face_q(qy[i, j], qy[i - 1, j], qy[i + 1, j], qc, z[a, j] + h[a, j], z[i, j] + h[i, j],
                                z[a, j], z[i, j], 0.5 * (n2[a, j] + n2[i, j]), dt, dx, theta, hmin, frmax)
                qyn[i, j] = q
                if hf > hm:
                    hm = hf
        if i == ny - 1:
            for j in range(nx):
                qyn[ny, j] = _edge_q(h[i, j], z[i, j], z[i - 1, j], n2[i, j], dx, hmin, frmax,
                                     wet_ok[i, j] == 1 and edge_ok[i, j])
        rowmax[i] = hm


@njit(parallel=True, cache=True, fastmath=True)
def _limit_ratio(h, solid, bay, qx, qy, r, dt, dx):
    ny, nx = h.shape
    for i in prange(ny):
        for j in range(nx):
            if solid[i, j] or bay[i, j]:
                r[i, j] = 1.0
                continue
            out = (max(qx[i, j + 1], 0.0) + max(-qx[i, j], 0.0)
                   + max(qy[i + 1, j], 0.0) + max(-qy[i, j], 0.0)) * dt / dx
            hv = h[i, j]
            r[i, j] = hv / out if out > hv else 1.0


@njit(parallel=True, cache=True, fastmath=True)
def _limit_apply(qx, qy, r):
    ny, nx = r.shape
    for i in prange(ny):
        for j in range(nx + 1):
            q = qx[i, j]
            if q > 0.0:
                if j >= 1:
                    qx[i, j] = q * r[i, j - 1]
            elif q < 0.0:
                if j < nx:
                    qx[i, j] = q * r[i, j]
        for j in range(nx):
            q = qy[i, j]
            if q > 0.0:
                if i >= 1:
                    qy[i, j] = q * r[i - 1, j]
            elif q < 0.0:
                qy[i, j] = q * r[i, j]
            if i == ny - 1:
                q = qy[ny, j]
                if q > 0.0:
                    qy[ny, j] = q * r[i, j]


@njit(parallel=True, cache=True, fastmath=False)
def _continuity(h, z, solid, bay, qx, qy, dt, dx, rain_d, tide, spillw, phi, basin,
                pf, ks, F, psidt, fcap, maxd, fdur, flood_thr, acc_rows):
    """h update + rain + roof spill + Green-Ampt; acc_rows[i] = [bay_gain, rain, spill, inf, clamp, edge]"""
    ny, nx = h.shape
    for i in prange(ny):
        bayg = 0.0
        rv = 0.0
        sv = 0.0
        iv = 0.0
        cv = 0.0
        ev = 0.0
        for j in range(nx):
            if solid[i, j]:
                continue
            div = (qx[i, j] - qx[i, j + 1] + qy[i, j] - qy[i + 1, j]) * dt / dx
            if bay[i, j]:
                bayg += div
                hb = tide - z[i, j]
                h[i, j] = hb if hb > 0.0 else 0.0
                continue
            if j == 0 and qx[i, 0] < 0.0:
                ev -= qx[i, 0] * dt / dx
            if j == nx - 1 and qx[i, nx] > 0.0:
                ev += qx[i, nx] * dt / dx
            if i == 0 and qy[0, j] < 0.0:
                ev -= qy[0, j] * dt / dx
            if i == ny - 1 and qy[ny, j] > 0.0:
                ev += qy[ny, j] * dt / dx
            hv = h[i, j] + div + rain_d
            rv += rain_d
            sw = spillw[i, j]
            if sw > 0.0:
                s = sw * rain_d * phi[basin[i, j]]
                hv += s
                sv += s
            p = pf[i, j]
            if p > 0.0 and hv > 0.0:
                Fv = F[i, j]
                fr = ks[i, j] * (1.0 + psidt[i, j] / max(Fv, 1e-4)) * dt
                lim = hv / p
                if fr > lim:
                    fr = lim
                F[i, j] = Fv + fr
                hv -= fr * p
                iv += fr * p
            if hv < 0.0:
                cv -= hv
                hv = 0.0
            h[i, j] = hv
            if hv > maxd[i, j]:
                maxd[i, j] = hv
            if hv > flood_thr:
                fdur[i, j] += dt
        acc_rows[i, 0] = bayg
        acc_rows[i, 1] = rv
        acc_rows[i, 2] = sv
        acc_rows[i, 3] = iv
        acc_rows[i, 4] = cv
        acc_rows[i, 5] = ev


@njit(parallel=True, cache=True, fastmath=False)
def _continuity_fused(h, z, solid, bay, qx, qy, r, qxs, qys, dt, dx, rain_d, tide, spillw, phi, basin,
                pf, ks, F, psidt, fcap, maxd, fdur, flood_thr, acc_rows):
    """Outflow limiter applied on the fly (raw q x upstream ratio r -> qxs/qys, own row only) +
    h update + rain + roof spill + Green-Ampt; acc_rows[i] = [bay_gain, rain, spill, inf, clamp, edge]"""
    ny, nx = h.shape
    for i in prange(ny):
        for j in range(nx + 1):
            q = qx[i, j]
            if q > 0.0:
                if j >= 1:
                    q = q * r[i, j - 1]
            elif q < 0.0:
                if j < nx:
                    q = q * r[i, j]
            qxs[i, j] = q
        for j in range(nx):
            q = qy[i, j]
            if q > 0.0:
                if i >= 1:
                    q = q * r[i - 1, j]
            elif q < 0.0:
                q = q * r[i, j]
            qys[i, j] = q
        if i == ny - 1:
            for j in range(nx):
                q = qy[ny, j]
                qys[ny, j] = q * r[i, j] if q > 0.0 else q
        bayg = 0.0
        rv = 0.0
        sv = 0.0
        iv = 0.0
        cv = 0.0
        ev = 0.0
        for j in range(nx):
            if solid[i, j]:
                continue
            qn = qy[i + 1, j]       # north face is written by row i+1: recompute, rounded like qys
            if qn > 0.0:
                qn = np.float32(qn * r[i, j])
            elif qn < 0.0 and i + 1 < ny:
                qn = np.float32(qn * r[i + 1, j])
            div = (qxs[i, j] - qxs[i, j + 1] + qys[i, j] - qn) * dt / dx
            if bay[i, j]:
                bayg += div
                hb = tide - z[i, j]
                h[i, j] = hb if hb > 0.0 else 0.0
                continue
            if j == 0 and qxs[i, 0] < 0.0:
                ev -= qxs[i, 0] * dt / dx
            if j == nx - 1 and qxs[i, nx] > 0.0:
                ev += qxs[i, nx] * dt / dx
            if i == 0 and qys[0, j] < 0.0:
                ev -= qys[0, j] * dt / dx
            if i == ny - 1 and qn > 0.0:
                ev += qn * dt / dx
            hv = h[i, j] + div + rain_d
            rv += rain_d
            sw = spillw[i, j]
            if sw > 0.0:
                s = sw * rain_d * phi[basin[i, j]]
                hv += s
                sv += s
            p = pf[i, j]
            if p > 0.0 and hv > 0.0:
                Fv = F[i, j]
                fr = ks[i, j] * (1.0 + psidt[i, j] / max(Fv, 1e-4)) * dt
                lim = hv / p
                if fr > lim:
                    fr = lim
                F[i, j] = Fv + fr
                hv -= fr * p
                iv += fr * p
            if hv < 0.0:
                cv -= hv
                hv = 0.0
            h[i, j] = hv
            if hv > maxd[i, j]:
                maxd[i, j] = hv
            if hv > flood_thr:
                fdur[i, j] += dt
        acc_rows[i, 0] = bayg
        acc_rows[i, 1] = rv
        acc_rows[i, 2] = sv
        acc_rows[i, 3] = iv
        acc_rows[i, 4] = cv
        acc_rows[i, 5] = ev


# ---------------------------------------------------------------------------------------------
# Full shallow-water option (params.scheme = "hll"): first-order finite-volume Godunov scheme with the
# HLL approximate Riemann solver (Harten, Lax & van Leer 1983; Toro 2001, Shock-Capturing Methods for
# Free-Surface Shallow Flows) and the hydrostatic reconstruction of Audusse, Bouchut, Bristeau, Klein &
# Perthame 2004 (SIAM J. Sci. Comput. 25:2050): well-balanced (lake at rest exact), positivity
# preserving under CFL <= 0.5, wet/dry capable. Cell-centred (h, hu, hv); face mass fluxes go through
# the same limiter/continuity kernel as the local-inertial scheme, so rain, infiltration, inlets,
# sewer, bay and plan sinks are identical. Semi-implicit Manning friction. Per-row writes only.
# ---------------------------------------------------------------------------------------------
@njit(inline="always", fastmath=True)
def _hll(hl, ul, vl, hr, ur, vr):
    g = 9.81
    if hl <= 0.0 and hr <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    cl = math.sqrt(g * hl)
    cr = math.sqrt(g * hr)
    if hl <= 0.0:
        sl = ur - 2.0 * cr
        sr = ur + cr
    elif hr <= 0.0:
        sl = ul - cl
        sr = ul + 2.0 * cl
    else:
        sl = min(ul - cl, ur - cr)
        sr = max(ul + cl, ur + cr)
    fml = hl * ul
    fmr = hr * ur
    fpl = hl * ul * ul + 0.5 * g * hl * hl
    fpr = hr * ur * ur + 0.5 * g * hr * hr
    if sl >= 0.0:
        fm, fp = fml, fpl
    elif sr <= 0.0:
        fm, fp = fmr, fpr
    else:
        inv = 1.0 / (sr - sl)
        fm = (sr * fml - sl * fmr + sl * sr * (hr - hl)) * inv
        fp = (sr * fpl - sl * fpr + sl * sr * (hr * ur - hl * ul)) * inv
    ft = fm * (vl if fm > 0.0 else vr)
    return fm, fp, ft, max(abs(sl), abs(sr))


@njit(inline="always", fastmath=True)
def _vel(h, m, hv):
    return m / h if h > hv else 0.0


@njit(inline="always", fastmath=True)
def _face_hll(ha, za, ua, va, hb, zb, ub, vb):
    """Audusse hydrostatic reconstruction at a face between a (left/bottom) and b (right/top).
    Returns mass flux, normal-momentum flux seen by a and by b, tangential flux, max wave speed."""
    zf = max(za, zb)
    hsa = max(0.0, ha + za - zf)
    hsb = max(0.0, hb + zb - zf)
    fm, fp, ft, sm = _hll(hsa, ua, va, hsb, ub, vb)
    g2 = 0.5 * 9.81
    return fm, fp + g2 * (ha * ha - hsa * hsa), fp + g2 * (hb * hb - hsb * hsb), ft, sm


@njit(parallel=True, cache=True, fastmath=True)
def _hll_flux(h, z, hu, hv, wet_ok, edge_ok, qx, fxl, fxr, fxt, qy, fyl, fyr, fyt, hvel, rowmax):
    ny, nx = h.shape
    g2 = 0.5 * 9.81
    for i in prange(ny):
        sm = 0.0
        # ---------------- x faces
        for j in range(nx + 1):
            a = j - 1
            wa = wet_ok[i, a] if j > 0 else 0
            wb = wet_ok[i, j] if j < nx else 0
            if j == 0 or j == nx or wa == 0 or wb == 0:
                # wall or tile edge; edge cells with edge_ok are transmissive outflow (zero gradient)
                fm = 0.0
                pa = 0.0
                pb = 0.0
                ft = 0.0
                if wa != 0:
                    ha = h[i, a]
                    pa = g2 * ha * ha
                    if j == nx and wa == 1 and edge_ok[i, a] and ha > hvel:
                        u = hu[i, a] / ha
                        if u > 0.0:
                            fm = ha * u
                            pa += fm * u
                            ft = fm * hv[i, a] / ha
                            c = abs(u) + math.sqrt(9.81 * ha)
                            if c > sm:
                                sm = c
                if wb != 0:
                    hb = h[i, j]
                    pb = g2 * hb * hb
                    if j == 0 and wb == 1 and edge_ok[i, j] and hb > hvel:
                        u = hu[i, j] / hb
                        if u < 0.0:
                            fm = hb * u
                            pb += fm * u
                            ft = fm * hv[i, j] / hb
                            c = abs(u) + math.sqrt(9.81 * hb)
                            if c > sm:
                                sm = c
                qx[i, j] = fm
                fxl[i, j] = pa
                fxr[i, j] = pb
                fxt[i, j] = ft
                continue
            if wa == 2 and wb == 2:
                qx[i, j] = 0.0
                fxl[i, j] = 0.0
                fxr[i, j] = 0.0
                fxt[i, j] = 0.0
                continue
            ha = h[i, a]
            hb = h[i, j]
            fm, pa, pb, ft, s_ = _face_hll(ha, z[i, a], _vel(ha, hu[i, a], hvel), _vel(ha, hv[i, a], hvel),
                                           hb, z[i, j], _vel(hb, hu[i, j], hvel), _vel(hb, hv[i, j], hvel))
            qx[i, j] = fm
            fxl[i, j] = pa
            fxr[i, j] = pb
            fxt[i, j] = ft
            if s_ > sm:
                sm = s_
        # ---------------- y faces: row i (between i-1 and i), plus top edge row ny for the last row
        for k in range(2 if i == ny - 1 else 1):
            fi = i + k                         # face row index
            for j in range(nx):
                a = fi - 1
                wa = wet_ok[a, j] if fi > 0 else 0
                wb = wet_ok[fi, j] if fi < ny else 0
                if fi == 0 or fi == ny or wa == 0 or wb == 0:
                    fm = 0.0
                    pa = 0.0
                    pb = 0.0
                    ft = 0.0
                    if wa != 0:
                        ha = h[a, j]
                        pa = g2 * ha * ha
                        if fi == ny and wa == 1 and edge_ok[a, j] and ha > hvel:
                            v = hv[a, j] / ha
                            if v > 0.0:
                                fm = ha * v
                                pa += fm * v
                                ft = fm * hu[a, j] / ha
                                c = abs(v) + math.sqrt(9.81 * ha)
                                if c > sm:
                                    sm = c
                    if wb != 0:
                        hb = h[fi, j]
                        pb = g2 * hb * hb
                        if fi == 0 and wb == 1 and edge_ok[fi, j] and hb > hvel:
                            v = hv[fi, j] / hb
                            if v < 0.0:
                                fm = hb * v
                                pb += fm * v
                                ft = fm * hu[fi, j] / hb
                                c = abs(v) + math.sqrt(9.81 * hb)
                                if c > sm:
                                    sm = c
                    qy[fi, j] = fm
                    fyl[fi, j] = pa
                    fyr[fi, j] = pb
                    fyt[fi, j] = ft
                    continue
                if wa == 2 and wb == 2:
                    qy[fi, j] = 0.0
                    fyl[fi, j] = 0.0
                    fyr[fi, j] = 0.0
                    fyt[fi, j] = 0.0
                    continue
                ha = h[a, j]
                hb = h[fi, j]
                fm, pa, pb, ft, s_ = _face_hll(ha, z[a, j], _vel(ha, hv[a, j], hvel), _vel(ha, hu[a, j], hvel),
                                               hb, z[fi, j], _vel(hb, hv[fi, j], hvel), _vel(hb, hu[fi, j], hvel))
                qy[fi, j] = fm
                fyl[fi, j] = pa
                fyr[fi, j] = pb
                fyt[fi, j] = ft
                if s_ > sm:
                    sm = s_
        rowmax[i] = sm


@njit(parallel=True, cache=True, fastmath=True)
def _hll_momentum(h, hu, hv, wet_ok, n2, fxl, fxr, fxt, fyl, fyr, fyt, dt, dx, hvel, umax):
    """Momentum update from the HLL/Audusse face fluxes, then semi-implicit Manning friction
    hu /= 1 + dt g n^2 |u| / h^(4/3) evaluated with the post-continuity depth."""
    ny, nx = h.shape
    c = dt / dx
    for i in prange(ny):
        for j in range(nx):
            if wet_ok[i, j] != 1:
                hu[i, j] = 0.0
                hv[i, j] = 0.0
                continue
            hn = h[i, j]
            if hn <= hvel:
                hu[i, j] = 0.0
                hv[i, j] = 0.0
                continue
            mu = hu[i, j] - c * (fxl[i, j + 1] - fxr[i, j]) - c * (fyt[i + 1, j] - fyt[i, j])
            mv = hv[i, j] - c * (fxt[i, j + 1] - fxt[i, j]) - c * (fyl[i + 1, j] - fyr[i, j])
            sp = math.sqrt(mu * mu + mv * mv) / hn
            y = math.sqrt(math.sqrt(hn))
            y = (2.0 * y + hn / (y * y)) * (1.0 / 3.0)
            y = (2.0 * y + hn / (y * y)) * (1.0 / 3.0)
            den = 1.0 + dt * 9.81 * n2[i, j] * sp / (hn * y)
            mu /= den
            mv /= den
            sp /= den
            if sp > umax:
                mu *= umax / sp
                mv *= umax / sp
            hu[i, j] = mu
            hv[i, j] = mv


@njit(cache=True)
def _sinks(hf, zf, dt, A, inl_c, inl_cap, inl_b, inl_q, inl_st,
           bV, bS, bzlow, bAmh, bDp, bQcap, bQcap0, bQtreat, bQbase, roofA, extA, rain_ms, phi, bsur,
           cw, perim, co, aopen,
           st_c, st_rem, st_q, pu_c, pu_q, acc):
    """Inlet exchange, lumped sewer basins, storage, pumps. acc: [inlet_in, inlet_back, roof_sewer,
    base, treated, cso, tunnel, storage, pump]. phi (roof spill fraction) is updated for next step."""
    nb_ = bV.shape[0]
    g2 = 2.0 * 9.81
    hgl = np.empty(nb_)
    for b in range(nb_):
        V = bV[b]
        if V <= bS[b]:
            hgl[b] = bzlow[b] - bDp[b] + bDp[b] * V / bS[b]
        else:
            hgl[b] = bzlow[b] + (V - bS[b]) / bAmh[b]
    qin = np.zeros(nb_)
    qback = np.zeros(nb_)
    nin = inl_c.shape[0]
    for k in range(nin):
        c = inl_c[k]
        b = inl_b[k]
        hv = hf[c]
        zc = zf[c]
        cap = inl_cap[k]
        H = hgl[b]
        q = 0.0
        if H < zc:
            if hv > 1e-4:
                qw = cw * perim * hv ** 1.5
                qo = co * aopen * math.sqrt(g2 * hv)
                q = min(cap, qw, qo, hv * A / dt)
        else:
            d = zc + hv - H
            if d >= 0.0:
                q = min(cap, co * aopen * math.sqrt(g2 * d), hv * A / dt)
            else:
                q = -co * aopen * math.sqrt(-g2 * d)   # reverse orifice (grate/lid not capacity-limited)
        inl_q[k] = q
        if q > 0.0:
            hf[c] = hv - q * dt / A
            qin[b] += q
            inl_st[k] = 1 if (q >= 0.95 * cap or (H >= zc and hv > 0.03)) else 0
        elif q < 0.0:
            qback[b] -= q
            inl_st[k] = 2
        else:
            inl_st[k] = 1 if (H >= zc and hv > 0.03) else 0
    scale = np.ones(nb_)
    for b in range(nb_):
        qr = rain_ms * roofA[b] * (1.0 - phi[b])
        qe = rain_ms * extA[b]
        acc[9] += qe * dt
        qr += qe
        avail = bV[b] + (qin[b] + qr + bQbase[b]) * dt
        qo = min(bQcap[b] * dt, avail)
        Vn = avail - qo
        tr = min(qo, bQtreat[b] * dt)
        tun = max(qo - bQcap0[b] * dt, 0.0)
        acc[0] += qin[b] * dt
        acc[2] += (qr - qe) * dt
        acc[3] += bQbase[b] * dt
        acc[4] += tr
        acc[6] += tun
        acc[5] += qo - tr - tun
        bv = qback[b] * dt
        if bv > 0.0:
            allowed = Vn - bS[b]
            if allowed < 0.0:
                allowed = 0.0
            if bv > allowed:
                scale[b] = allowed / bv
                bv = allowed
            Vn -= bv
            acc[1] += bv
        bV[b] = Vn
        if Vn >= bS[b]:
            phi[b] = 1.0
            bsur[b] += dt
        else:
            phi[b] = 0.0
    for k in range(nin):
        q = inl_q[k]
        if q < 0.0:
            q *= scale[inl_b[k]]
            inl_q[k] = q
            hf[inl_c[k]] -= q * dt / A
    for k in range(st_c.shape[0]):
        c = st_c[k]
        q = min(st_q[k], hf[c] * A / dt, st_rem[k] / dt)
        if q > 0.0:
            hf[c] -= q * dt / A
            st_rem[k] -= q * dt
            acc[7] += q * dt
    for k in range(pu_c.shape[0]):
        c = pu_c[k]
        q = min(pu_q[k], hf[c] * A / dt)
        if q > 0.0:
            hf[c] -= q * dt / A
            acc[8] += q * dt


def _land_volume(h, solid, bay):
    """Deterministic (fixed-order numpy) sum of surface water on land cells, in m (x cell area)."""
    return float(np.sum(np.where(solid | bay, 0.0, h), dtype=np.float64))


def _enclosed_courtyards(open_, iy, ix, max_cells):
    """Open pockets fully enclosed by buildings (light wells, courtyards, rear yards) without an inlet
    or street access drain through private area drains into the building lateral: treated as roof."""
    from scipy import ndimage
    lab, n = ndimage.label(open_)
    if n == 0:
        return np.zeros(open_.shape, bool)
    size = np.bincount(lab.ravel(), minlength=n + 1)
    small = size <= max_cells
    small[0] = False
    has_inlet = np.zeros(n + 1, bool)
    has_inlet[lab[iy, ix]] = True
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    small[has_inlet] = False
    small[edge] = False
    return small[lab]


def _bay_mask(cover, bid, z, intertidal_z):
    """Bay = cover 3 plus open cells below `intertidal_z` (m NAVD88, ~MLLW+0.5) that touch the Bay:
    hydro-flattened / no-data water surfaces in the lidar (e.g. the NE corner of the tile)."""
    from scipy import ndimage
    bay = cover == 3
    low = (z < intertidal_z) & (bid < 0) & ~bay
    if low.any():
        lab, n = ndimage.label(low | bay)
        keep = np.unique(lab[bay])
        bay = np.isin(lab, keep[keep > 0]) | bay
    return np.ascontiguousarray(bay)


# =============================================================================================
# Plan edits
# =============================================================================================
def coerce_edits(edits) -> Optional[PlanEdits]:
    """Accept None, PlanEdits, a dict, or a CityEditor-like object (to_water_edits() or attributes
    z/ksat/manning/cover + sinks list of {kind: storage|pump|tunnel|inlet|sewer, ...})."""
    if edits is None or isinstance(edits, PlanEdits):
        return edits
    if hasattr(edits, "to_water_edits"):
        return coerce_edits(edits.to_water_edits())
    pe = PlanEdits()
    get = (lambda k: edits.get(k)) if isinstance(edits, dict) else (lambda k: getattr(edits, k, None))
    for k in ("z", "ksat", "manning", "cover", "bldg_id", "inlet_mult", "sewer_cap_mult",
              "sewer_cap_add_m3s", "roof_retention_mm", "clog_factor", "sewer_initial_storage_frac"):
        v = get(k)
        if v is not None:
            setattr(pe, k, v)
    for k in ("extra_inlets", "storage", "pumps", "tunnels"):
        v = get(k)
        if v:
            setattr(pe, k, list(v))
    for s in (get("sinks") or []):
        s = dict(s) if isinstance(s, dict) else dict(vars(s))
        kind = (s.get("kind") or s.get("type") or "").lower()
        if kind in ("storage", "cistern", "vault"):
            pe.storage.append(s)
        elif kind == "pump":
            pe.pumps.append(s)
        elif kind == "tunnel":
            pe.tunnels.append(s)
        elif kind in ("inlet", "extra_inlet"):
            pe.extra_inlets.append(s)
    return pe


def _cell_of(g: Grid, d: dict):
    if "ix" in d and "iy" in d:
        return int(d["iy"]), int(d["ix"])
    ix, iy = g.to_ix(d["x"], d["y"])
    return int(iy), int(ix)


def _basin_vals(v, nb_, default):
    out = np.full(nb_, default, np.float64)
    if v is None:
        return out
    if isinstance(v, dict):
        for k, x in v.items():
            if 0 <= int(k) < nb_:
                out[int(k)] = float(x)
        return out
    a = np.asarray(v, float)
    return np.broadcast_to(a, (nb_,)).astype(np.float64).copy()


# =============================================================================================
# Main driver
# =============================================================================================
def _s0_from_storm(storm):
    ant = float(getattr(storm, "antecedent_rain_mm", 0.0) or 0.0)
    return float(np.clip(0.30 + 0.5 * (1 - math.exp(-ant / 60.0)), 0.3, 0.8))


def run_water(grid: Grid, storm, edits=None, frame_cb: Optional[Callable] = None,
              params: Optional[WaterParams] = None, threads: Optional[int] = None,
              verbose: bool = True, keep_frames: bool = True) -> WaterResult:
    """Run the storm on the grid. frame_cb(k, t, depth_f32, info) is called at every frame."""
    t_wall = time.time()
    p = params or default_params()
    # cap threads (numba defaults to all 80 cores; many sims share this box). Override: threads= or
    # STORMSIM_WATER_THREADS. Results are bit-identical for any thread count.
    nthr = int(threads or os.environ.get("STORMSIM_WATER_THREADS", 16))
    nb.set_num_threads(max(1, min(nthr, nb.config.NUMBA_NUM_THREADS)))
    pe = coerce_edits(edits)
    ny, nx, dx = grid.ny, grid.nx, grid.dx
    A = dx * dx
    z = grid.z if pe is None or pe.z is None else np.asarray(pe.z, np.float32)
    cover = grid.cover if pe is None or pe.cover is None else np.asarray(pe.cover, np.uint8)
    man = grid.manning if pe is None or pe.manning is None else np.asarray(pe.manning, np.float32)
    ksat = grid.ksat if pe is None or pe.ksat is None else np.asarray(pe.ksat, np.float32)
    bid = grid.bldg_id if pe is None or pe.bldg_id is None else np.asarray(pe.bldg_id, np.int32)
    z = np.ascontiguousarray(z, np.float64)
    bay = _bay_mask(cover, bid, z, p.intertidal_z)
    solid = (bid >= 0) & ~bay
    court = _enclosed_courtyards(~solid & ~bay, grid.inlets["iy"], grid.inlets["ix"], p.court_max_m2 / dx ** 2)
    solid = np.ascontiguousarray(solid | court)
    land = ~solid & ~bay
    n2 = np.ascontiguousarray(np.maximum(man, 0.008).astype(np.float32) ** 2)
    zf32 = np.ascontiguousarray(z.astype(np.float32))
    wet_ok = np.ascontiguousarray(np.where(solid, 0, np.where(bay, 2, 1)).astype(np.uint8))
    tide_max = float(np.max(storm.tide_at(np.linspace(0.0, float(storm.duration_s), 200))))
    edge_ok = np.ascontiguousarray(z >= tide_max + p.edge_freeboard_m)
    basin = np.ascontiguousarray(grid.basin.astype(np.int64))
    nbas = len(grid.basin_info)

    # ---------------- soil
    s0 = p.s0 if p.s0 is not None else _s0_from_storm(storm)
    dtheta = p.porosity * (1.0 - s0)
    ks = np.where(land, ksat, 0.0).astype(np.float64) / 3.6e6       # m/s
    pf = (ks > 0).astype(np.float64)
    root = np.full((ny, nx), p.root_m)
    tree_cell = np.zeros((ny, nx), bool)
    if grid.tree_ix is not None and len(grid.tree_ix):
        tree_cell[grid.tree_iy, grid.tree_ix] = True
        pit = tree_cell & land & (ksat < 2.0)
        ks[pit] = p.pit_ksat_mm_h / 3.6e6
        pf[pit] = p.pit_frac
        root[pit] = p.pit_root_m
    fcap = root * dtheta
    psidt = np.full((ny, nx), p.psi_m * dtheta)
    F = np.zeros((ny, nx))

    # ---------------- roofs -> sewer, spill target = nearest street cell
    nidx = _nearest_open(land)
    roof = solid.copy()
    ret = None
    if pe is not None and pe.roof_retention_mm is not None:
        r = pe.roof_retention_mm
        if isinstance(r, dict):
            ret = np.zeros((ny, nx))
            ids = np.array([int(k) for k in r], np.int64)
            vals = np.array([float(v) for v in r.values()])
            lut = np.zeros(max(int(bid.max()) + 1, 1))
            ok = ids < len(lut)
            lut[ids[ok]] = vals[ok]
            ret[solid] = lut[np.clip(bid[solid], 0, len(lut) - 1)]
        else:
            ret = np.asarray(r, float)
    ry, rx = np.nonzero(roof)
    ty, tx = nidx[0][ry, rx], nidx[1][ry, rx]
    roof_ret = ret[ry, rx] if ret is not None else np.zeros(len(ry))

    def roof_weights(P_mm):
        w = (roof_ret < P_mm).astype(np.float64) if ret is not None else np.ones(len(ry))
        sw = np.zeros((ny, nx))
        np.add.at(sw, (ty, tx), w)
        rA = np.bincount(basin[ty, tx], weights=w * A, minlength=nbas).astype(np.float64)
        return sw, rA

    # ---------------- inlets
    icx = grid.inlets["ix"].astype(np.int64).copy()
    icy = grid.inlets["iy"].astype(np.int64).copy()
    cap = grid.inlets["cap"].astype(np.float64) * p.inlet_mult
    ids = list(grid.inlets["id"])
    clog = inlet_clog_baseline(grid, p)
    if pe is not None and pe.clog_factor is not None:
        cf = pe.clog_factor
        if isinstance(cf, dict):
            pos = {k: i for i, k in enumerate(ids)}
            for k, v in cf.items():
                if k in pos:
                    clog[pos[k]] = float(v)
                elif isinstance(k, (int, np.integer)) and 0 <= int(k) < len(clog):
                    clog[int(k)] = float(v)
        else:
            clog = np.broadcast_to(np.asarray(cf, float), clog.shape).copy()
    cap = cap * np.clip(clog, 0.0, 1.0)
    if pe is not None and pe.inlet_mult is not None:
        m = pe.inlet_mult
        if isinstance(m, dict):
            pos = {k: i for i, k in enumerate(ids)}
            for k, v in m.items():
                if k in pos:
                    cap[pos[k]] *= float(v)
                elif isinstance(k, (int, np.integer)) and 0 <= int(k) < len(cap):
                    cap[int(k)] *= float(v)
        else:
            cap = cap * np.broadcast_to(np.asarray(m, float), cap.shape)
    if pe is not None and pe.extra_inlets:
        ex = [(_cell_of(grid, d), float(d.get("capacity_m3s", d.get("cap", 0.05)))) for d in pe.extra_inlets]
        icy = np.concatenate([icy, [e[0][0] for e in ex]]).astype(np.int64)
        icx = np.concatenate([icx, [e[0][1] for e in ex]]).astype(np.int64)
        cap = np.concatenate([cap, [e[1] for e in ex]])
        ids += [f"plan{i}" for i in range(len(ex))]
    # inlets must sit on open land cells
    bad = ~land[icy, icx]
    if bad.any():
        icy[bad], icx[bad] = nidx[0][icy[bad], icx[bad]], nidx[1][icy[bad], icx[bad]]
    inl_c = (icy * nx + icx).astype(np.int64)
    inl_b = basin[icy, icx].astype(np.int64)
    inl_q = np.zeros(len(inl_c))
    inl_st = np.zeros(len(inl_c), np.int8)

    # ---------------- sewer basins
    imp = (solid | (land & (cover == 0)))
    perv = land & (cover == 2)
    A_imp = np.bincount(basin[imp], minlength=nbas) * A
    A_perv = np.bincount(basin[perv], minlength=nbas) * A
    A_all = np.bincount(basin[land | solid], minlength=nbas) * A
    # upstream sewersheds outside the tile: edge cells whose ground falls into the tile collect a strip
    # of length ext_upstream_m beyond the edge; its runoff enters that basin's trunk sewer
    extA = np.zeros(nbas)
    if p.ext_upstream_m > 0:
        e_in = np.zeros((ny, nx), bool)
        e_in[0, :] |= z[0, :] > z[1, :]
        e_in[-1, :] |= z[-1, :] > z[-2, :]
        e_in[:, 0] |= z[:, 0] > z[:, 1]
        e_in[:, -1] |= z[:, -1] > z[:, -2]
        e_in &= ~bay
        extA = np.bincount(basin[e_in], minlength=nbas) * dx * p.ext_upstream_m * p.c_ext
    bQcap0 = p.i_design_mm_h / 3.6e6 * (p.c_imp * A_imp + p.c_perv * A_perv)
    if pe is not None:
        bQcap0 = bQcap0 * _basin_vals(pe.sewer_cap_mult, nbas, 1.0) + _basin_vals(pe.sewer_cap_add_m3s, nbas, 0.0)
    bQtun = np.zeros(nbas)
    if pe is not None:
        for tdef in pe.tunnels:
            if "cells" in tdef:
                cells = [(int(c[1]), int(c[0])) for c in tdef["cells"]]
            else:
                path = np.asarray(tdef.get("path_xy") or tdef.get("path"), float)
                pts = []
                for a_, b_ in zip(path[:-1], path[1:]):
                    nseg = max(2, int(np.hypot(*(b_ - a_)) / dx) + 1)
                    for f_ in np.linspace(0, 1, nseg):
                        pts.append(a_ + f_ * (b_ - a_))
                ixs, iys = grid.to_ix(np.array([q[0] for q in pts]), np.array([q[1] for q in pts]))
                cells = list(zip(iys.tolist(), ixs.tolist()))
            bs = np.array([basin[c] for c in cells])
            cnt = np.bincount(bs, minlength=nbas).astype(float)
            bQtun += float(tdef.get("m3s", 0.0)) * cnt / max(cnt.sum(), 1)
    bQcap = bQcap0 + bQtun
    bQbase = p.dry_mgd * MGD * (A_all / 1e6) / p.sf_land_km2
    bQtreat = p.wet_mgd * MGD * (A_all / 1e6) / p.sf_land_km2
    bS = np.maximum(p.store_mm / 1000.0 * A_all, 50.0)
    bzlow = np.array([bi["z_low"] for bi in grid.basin_info], np.float64)
    nin_b = np.bincount(inl_b, minlength=nbas)
    bAmh = np.maximum(nin_b, 1) * p.a_manhole_m2
    bDp = np.full(nbas, p.pipe_depth_m)
    v0 = _basin_vals(pe.sewer_initial_storage_frac if pe is not None else None, nbas, p.v0_frac)
    bV = np.clip(v0, 0.0, 1.0) * bS
    phi = np.zeros(nbas)
    bsur = np.zeros(nbas)

    # ---------------- plan storage / pumps
    def cells_of(lst):
        if not lst:
            return np.zeros(0, np.int64)
        cc = []
        for d in lst:
            iy_, ix_ = _cell_of(grid, d)
            if not land[iy_, ix_]:
                iy_, ix_ = int(nidx[0][iy_, ix_]), int(nidx[1][iy_, ix_])
            cc.append(iy_ * nx + ix_)
        return np.array(cc, np.int64)
    st_list = pe.storage if pe is not None else []
    pu_list = pe.pumps if pe is not None else []
    st_c = cells_of(st_list)
    st_rem = np.array([float(d.get("volume_m3", 0)) for d in st_list], np.float64)
    st_q = np.array([float(d.get("intake_m3s", d.get("m3s", 1.0))) for d in st_list], np.float64)
    pu_c = cells_of(pu_list)
    pu_q = np.array([float(d.get("m3s", 0)) for d in pu_list], np.float64)

    # ---------------- state
    h = np.zeros((ny, nx))
    tide0 = float(storm.tide_at(0.0))
    h[bay] = np.maximum(tide0 - z[bay], 0.0)
    qx = np.zeros((ny, nx + 1), np.float32)
    qy = np.zeros((ny + 1, nx), np.float32)
    qxn = np.zeros_like(qx)
    qyn = np.zeros_like(qy)
    r = np.ones((ny, nx))
    rowmax = np.zeros(ny)
    acc_rows = np.zeros((ny, 6))
    hll = p.scheme.lower() == "hll"
    if hll:
        hu = np.zeros((ny, nx))
        hv = np.zeros((ny, nx))
        fxl, fxr, fxt = (np.zeros((ny, nx + 1)) for _ in range(3))
        fyl, fyr, fyt = (np.zeros((ny + 1, nx)) for _ in range(3))
    maxd = np.zeros((ny, nx))
    fdur = np.zeros((ny, nx))
    acc_s = np.zeros(10)
    tot = dict(bay_gain=0.0, rain_surface=0.0, roof_spill=0.0, infiltration=0.0, clamp=0.0, edge_out=0.0)
    hflat = h.reshape(-1)
    zflat = z.reshape(-1)

    dur = float(storm.duration_s)
    dtf = float(storm.dt_frame_s)
    t_frames = np.arange(0.0, dur + 1e-6, dtf)
    nfr = len(t_frames)
    depth_fr = np.zeros((nfr, ny, nx), np.float32) if keep_frames else None
    u_fr = np.zeros((nfr, ny, nx), np.float32) if keep_frames else None
    v_fr = np.zeros((nfr, ny, nx), np.float32) if keep_frames else None
    max_speed = np.zeros((ny, nx), np.float32)
    max_hazard = np.zeros((ny, nx), np.float32)
    sat_fr = np.zeros((nfr, ny, nx), np.float32) if keep_frames else None
    fill_fr = np.zeros((nfr, nbas), np.float32)
    hgl_fr = np.zeros((nfr, nbas), np.float32)
    ist_fr = np.zeros((nfr, len(inl_c)), np.int8)
    iq_fr = np.zeros((nfr, len(inl_c)), np.float32)
    ist_max = np.zeros(len(inl_c), np.int8)   # worst state since last frame

    sat_mask = pf > 0

    def sat_now():
        s = np.full((ny, nx), s0, np.float32)
        s[sat_mask] = (s0 + (1 - s0) * np.minimum(F[sat_mask] / fcap[sat_mask], 1.0)).astype(np.float32)
        s[bay] = 1.0
        return s

    def hgl_now():
        return np.where(bV <= bS, bzlow - bDp + bDp * bV / bS, bzlow + (bV - bS) / bAmh)

    def snap(k, t):
        d = np.where(land, h, 0.0).astype(np.float32)
        wet = d > 0.01
        hd = np.where(wet, d, 1.0)
        if hll:
            u = np.where(wet, hu / hd, 0.0).astype(np.float32)
            v = np.where(wet, hv / hd, 0.0).astype(np.float32)
        else:
            u = np.where(wet, 0.5 * (qx[:, :-1] + qx[:, 1:]) / hd, 0.0).astype(np.float32)
            v = np.where(wet, 0.5 * (qy[:-1, :] + qy[1:, :]) / hd, 0.0).astype(np.float32)
        sp = np.hypot(u, v)
        np.maximum(max_speed, sp, out=max_speed)
        np.maximum(max_hazard, d * (sp + 0.5), out=max_hazard)   # DEFRA FD2321 d(v+0.5)
        if keep_frames:
            depth_fr[k] = d
            u_fr[k] = u
            v_fr[k] = v
            sat_fr[k] = sat_now()
        fill_fr[k] = bV / bS
        hgl_fr[k] = hgl_now()
        ist_fr[k] = ist_max
        iq_fr[k] = inl_q
        if frame_cb is not None:
            frame_cb(k, t, d, {"u": u, "v": v, "sewer_fill": bV / bS, "inlet_state": ist_max.copy(),
                               "tide_m": float(storm.tide_at(t))})

    vol0 = _land_volume(h, solid, bay) * A + bV.sum()
    t = 0.0
    P_mm = 0.0
    spillw, roofA = roof_weights(P_mm)
    dt = 0.1 if hll else 0.5
    nsteps = 0
    snap(0, 0.0)
    for k in range(1, nfr):
        t_end = t_frames[k]
        ist_max[:] = 0
        if ret is not None:
            spillw, roofA = roof_weights(P_mm)
        while t < t_end - 1e-9:
            dt_step = min(dt, t_end - t)
            rain_mm_h = float(storm.rain_at(t))
            rain_ms = rain_mm_h / 3.6e6
            tide = float(storm.tide_at(t + dt_step))
            if hll:
                _hll_flux(h, z, hu, hv, wet_ok, edge_ok, qx, fxl, fxr, fxt, qy, fyl, fyr, fyt, p.hvel, rowmax)
            else:
                _momentum(h, zf32, n2, wet_ok, edge_ok, qx, qy, qxn, qyn, dt_step, dx, p.theta, p.hmin, p.fr_max,
                          rowmax)
                qx, qxn = qxn, qx
                qy, qyn = qyn, qy
            _limit_ratio(h, solid, bay, qx, qy, r, dt_step, dx)
            if _FUSED:
                _continuity_fused(h, z, solid, bay, qx, qy, r, qxn, qyn, dt_step, dx, rain_ms * dt_step, tide,
                                  spillw, phi, basin, pf, ks, F, psidt, fcap, maxd, fdur, p.flood_thr, acc_rows)
                qx, qxn = qxn, qx
                qy, qyn = qyn, qy
            else:
                _limit_apply(qx, qy, r)
                _continuity(h, z, solid, bay, qx, qy, dt_step, dx, rain_ms * dt_step, tide, spillw, phi, basin,
                            pf, ks, F, psidt, fcap, maxd, fdur, p.flood_thr, acc_rows)
            if hll:
                _hll_momentum(h, hu, hv, wet_ok, n2, fxl, fxr, fxt, fyl, fyr, fyt, dt_step, dx, p.hvel, p.umax)
            s = acc_rows.sum(0)
            tot["bay_gain"] += s[0] * A
            tot["rain_surface"] += s[1] * A
            tot["roof_spill"] += s[2] * A
            tot["infiltration"] += s[3] * A
            tot["clamp"] += s[4] * A
            tot["edge_out"] += s[5] * A
            _sinks(hflat, zflat, dt_step, A, inl_c, cap, inl_b, inl_q, inl_st,
                   bV, bS, bzlow, bAmh, bDp, bQcap, bQcap0, bQtreat, bQbase, roofA, extA, rain_ms, phi, bsur,
                   p.cw, p.perim_m, p.co, p.a_open_m2, st_c, st_rem, st_q, pu_c, pu_q, acc_s)
            np.maximum(ist_max, inl_st, out=ist_max)
            t += dt_step
            P_mm += rain_mm_h * dt_step / 3600.0
            nsteps += 1
            if hll:
                dt = min(p.cfl_hll * dx / max(float(rowmax.max()), 0.05), p.dt_max)
            else:
                hm = max(float(rowmax.max()), 0.01)
                dt = min(p.alpha * dx / math.sqrt(G * hm), p.dt_max)
        snap(k, t)
        if verbose and (k % max(1, nfr // 12) == 0 or k == nfr - 1):
            print(f"  water t={t/3600:5.2f} h  dt={dt:5.2f}s steps={nsteps:6d}  rain={float(storm.rain_at(t-1)):5.1f} mm/h "
                  f"tide={float(storm.tide_at(t)):.2f}  maxh={float(maxd.max()):.2f}  surcharged={int((bV>=bS).sum())}/{nbas}"
                  f"  wall={time.time()-t_wall:5.1f}s", flush=True)

    vol1 = _land_volume(h, solid, bay) * A + bV.sum()
    roof_rain = float(P_mm / 1000.0 * len(ry) * A)  # all roof rain (spilled + to sewer)
    V = dict(
        rain_total=tot["rain_surface"] + roof_rain,
        rain_on_roofs=roof_rain,
        roof_to_sewer=float(acc_s[2]), roof_spill_to_street=tot["roof_spill"],
        infiltration=tot["infiltration"], inlet_capture=float(acc_s[0]), sewer_backflow=float(acc_s[1]),
        dry_weather_flow=float(acc_s[3]), upstream_sewer_inflow=float(acc_s[9]), sewer_to_treatment=float(acc_s[4]), sewer_cso_to_bay=float(acc_s[5]),
        tunnel_to_bay=float(acc_s[6]), plan_storage=float(acc_s[7]), pumped_to_bay=float(acc_s[8]),
        bay_to_land=-tot["bay_gain"], edge_outflow=tot["edge_out"], surface_end=float(_land_volume(h, solid, bay) * A),
        sewer_end=float(bV.sum()), clamp=tot["clamp"])
    # roof water retained on green roofs never enters the system
    roof_retained = roof_rain - V["roof_to_sewer"] - V["roof_spill_to_street"]
    V["roof_retained"] = roof_retained
    inflow = (V["rain_total"] - roof_retained + V["dry_weather_flow"] + V["upstream_sewer_inflow"]
              + V["bay_to_land"])
    outflow = (V["infiltration"] + V["sewer_to_treatment"] + V["sewer_cso_to_bay"] + V["tunnel_to_bay"]
               + V["plan_storage"] + V["pumped_to_bay"] + V["edge_outflow"])
    err = (vol1 - vol0) - (inflow - outflow)
    scale = max(V["rain_total"] + abs(V["bay_to_land"]), 1e-9)
    V["mass_error_m3"] = float(err)
    mass_err = 100.0 * abs(err) / scale
    rt = time.time() - t_wall
    if verbose:
        print(f"  water done: {nsteps} steps, {rt:.1f}s, mass error {mass_err:.4f}% "
              f"(rain {V['rain_total']:.0f} m3, inf {V['infiltration']:.0f}, sewer out "
              f"{V['sewer_to_treatment']+V['sewer_cso_to_bay']:.0f}, bay->land {V['bay_to_land']:.0f})", flush=True)
    info = []
    for b, bi in enumerate(grid.basin_info):
        info.append({**bi, "q_cap_m3s": float(bQcap[b]), "storage_m3": float(bS[b]),
                     "q_treat_m3s": float(bQtreat[b]), "surcharge_s": float(bsur[b])})
    land_f = land.astype(np.float32)
    return WaterResult(
        t_frames=t_frames, depth=depth_fr, u=u_fr, v=v_fr, max_speed=max_speed, max_hazard=max_hazard,
        saturation=sat_fr,
        max_depth=(maxd * land_f).astype(np.float32), flood_duration_s=(fdur * land_f).astype(np.float32),
        sewer_fill=fill_fr, sewer_hgl=hgl_fr, surcharge_s=bsur.copy(), inlet_state=ist_fr, inlet_q=iq_fr,
        inlet_ix=icx, inlet_iy=icy, inlet_ids=ids, basin=grid.basin, basin_info=info, volumes=V,
        mass_error_pct=float(mass_err), runtime_s=rt, n_steps=nsteps,
        params={k: getattr(p, k) for k in p.__dataclass_fields__} | {"s0": s0},
        tide_m=np.array([float(storm.tide_at(tt)) for tt in t_frames]),
        rain_mm_h=np.array([float(storm.rain_at(tt)) for tt in t_frames]), dx=dx, x0=grid.x0, y0=grid.y0)


# =============================================================================================
# Outputs: web frames, validation, plot
# =============================================================================================
def depth_8m(d4: np.ndarray, land: np.ndarray) -> np.ndarray:
    """Mean water depth over the open (non-building, non-bay) 4 m cells of each 8 m cell."""
    ny8, nx8 = d4.shape[-2] // 2, d4.shape[-1] // 2
    d = d4[..., :ny8 * 2, :nx8 * 2].reshape(d4.shape[:-2] + (ny8, 2, nx8, 2))
    w = land[:ny8 * 2, :nx8 * 2].reshape(ny8, 2, nx8, 2).astype(np.float32)
    s = (d * w).sum(axis=(-3, -1))
    n = w.sum(axis=(1, 3))
    return np.where(n > 0, s / np.maximum(n, 1), 0.0)


def write_web_depth(res: WaterResult, out_dir: str, grid: Optional[Grid] = None) -> dict:
    """Write depth.u16 (n_frames x ny8 x nx8, mm, uint16, 8 m) + sat.u8 (0..255) and return the
    manifest fields. Bay cells are 0 (the frontend draws the Bay plane at tide_m)."""
    os.makedirs(out_dir, exist_ok=True)
    land = res.depth.max(axis=0) >= 0  # placeholder shape
    if grid is not None:
        land = (grid.cover != 3) & (grid.bldg_id < 0)
    else:
        land = np.ones(res.depth.shape[1:], bool)
    n = res.depth.shape[0]
    ny8, nx8 = res.depth.shape[1] // 2, res.depth.shape[2] // 2
    with open(os.path.join(out_dir, "depth.u16"), "wb") as f:
        for k in range(n):
            mm = np.clip(np.round(depth_8m(res.depth[k], land) * 1000.0), 0, 65535).astype("<u2")
            f.write(mm.tobytes())
    with open(os.path.join(out_dir, "saturation.u8"), "wb") as f:
        for k in range(n):
            s = res.saturation[k, :ny8 * 2, :nx8 * 2].reshape(ny8, 2, nx8, 2).mean(axis=(1, 3))
            f.write(np.clip(np.round(s * 255), 0, 255).astype(np.uint8).tobytes())
    with open(os.path.join(out_dir, "velocity.i16"), "wb") as f:
        for k in range(n):
            uv = np.stack([depth_8m(res.u[k], land), depth_8m(res.v[k], land)], -1)
            f.write(np.clip(np.round(uv * 1000.0), -32767, 32767).astype("<i2").tobytes())
    mx = np.clip(np.round(depth_8m(res.max_depth, land) * 1000.0), 0, 65535).astype("<u2")
    mx.tofile(os.path.join(out_dir, "max_depth.u16"))
    inl = {"t": res.t_frames.tolist(), "ix": res.inlet_ix.tolist(), "iy": res.inlet_iy.tolist(),
           "state": res.inlet_state.tolist(), "basin_fill": np.round(res.sewer_fill, 3).tolist()}
    json.dump(inl, open(os.path.join(out_dir, "inlets_state.json"), "w"), separators=(",", ":"))
    return {"nx": int(nx8), "ny": int(ny8), "dx_frame": res.dx * 2, "n_frames": int(n),
            "t": res.t_frames.tolist(), "files": {"depth": "depth.u16 (uint16 mm)", "saturation": "saturation.u8",
                                                    "velocity": "velocity.i16 (n x ny x nx x 2: u,v int16 mm/s)",
                                                    "max_depth": "max_depth.u16", "inlets": "inlets_state.json"}}


_FRAME_FIELDS = ("depth", "u", "v", "saturation")


def save_result(res: WaterResult, path: str):
    """Save a WaterResult to .npz (frames as float16 to halve size) for other modules to reuse."""
    d = {}
    for k in res.__dataclass_fields__:
        v = getattr(res, k)
        if isinstance(v, np.ndarray):
            d[k] = v.astype(np.float16) if (k in _FRAME_FIELDS and v.dtype == np.float32) else v
    meta = {k: getattr(res, k) for k in ("inlet_ids", "basin_info", "volumes", "mass_error_pct", "runtime_s",
                                          "n_steps", "params", "dx", "x0", "y0", "validation")}
    d["_meta"] = np.frombuffer(json.dumps(meta, default=float).encode(), np.uint8)
    np.savez(path, **d)


def load_result(path: str) -> WaterResult:
    z = np.load(path)
    kw = {k: (z[k].astype(np.float32) if k in _FRAME_FIELDS else z[k]) for k in z.files if k != "_meta"}
    kw.update(json.loads(bytes(z["_meta"]).decode()))
    return WaterResult(**kw)


def validate(res: WaterResult, grid: Grid, thr: float = 0.15, buffer_m: float = 30.0,
             min_area_m2: float = 200.0) -> dict:
    """Skill vs SFPUC 100-yr flood map (cells, land only) and 311 31.12.2022 reports.
    Predicted flooding = max depth > thr in contiguous patches >= min_area_m2 (isolated lidar-pit
    puddles removed; set 0 to keep them, the raw scores are reported under 'flood100_raw')."""
    from scipy import ndimage
    land = (grid.cover != 3) & (grid.bldg_id < 0)
    pred_raw = (res.max_depth > thr) & land
    pred = pred_raw
    if min_area_m2 > 0:
        lab, n = ndimage.label(pred_raw)
        if n:
            sz = np.bincount(lab.ravel()) * grid.dx ** 2
            keep = sz >= min_area_m2
            keep[0] = False
            pred = keep[lab]
    obs0 = (grid.flood100 > 0) & land
    h0, m0, f0 = int((pred_raw & obs0).sum()), int((~pred_raw & obs0).sum()), int((pred_raw & ~obs0).sum())
    raw = {"hit_rate_POD": h0 / max(h0 + m0, 1), "false_alarm_ratio": f0 / max(h0 + f0, 1),
           "CSI": h0 / max(h0 + m0 + f0, 1), "pred_cells": int(pred_raw.sum())}
    obs = (grid.flood100 > 0) & land
    hits = int((pred & obs).sum())
    miss = int((~pred & obs).sum())
    fa = int((pred & ~obs).sum())
    out = {"threshold_m": thr, "flood100": {
        "hit_rate_POD": hits / max(hits + miss, 1), "false_alarm_ratio": fa / max(hits + fa, 1),
        "CSI": hits / max(hits + miss + fa, 1), "pred_cells": int(pred.sum()), "obs_cells": int(obs.sum())},
        "flood100_raw": raw, "min_area_m2": min_area_m2}
    rad = max(1, int(round(buffer_m / grid.dx)))
    yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1]
    fp = (xx ** 2 + yy ** 2) <= rad ** 2
    near_pred = ndimage.binary_dilation(pred, structure=fp)
    near_obs = ndimage.binary_dilation(obs, structure=fp)
    lf = land | (grid.bldg_id >= 0)
    base_pred = float(near_pred[lf].mean())
    base_obs = float(near_obs[lf].mean())
    pts = (grid.validation or {}).get("311_2022_12_31", [])
    bf = res.inlet_state.max(0) == 2 if res.inlet_state.size else np.zeros(0, bool)
    bmask = np.zeros(grid.z.shape, bool)
    if bf.any():
        bmask[res.inlet_iy[bf], res.inlet_ix[bf]] = True
    rb = int(round(100.0 / grid.dx))
    yb, xb = np.mgrid[-rb:rb + 1, -rb:rb + 1]
    near_bf = ndimage.binary_dilation(bmask, structure=(xb ** 2 + yb ** 2) <= rb ** 2) if bf.any() else bmask
    for kind in ("flooding", "sewage"):
        P = [q for q in pts if q.get("kind") == kind]
        if not P:
            continue
        ix, iy = grid.to_ix([q["x"] for q in P], [q["y"] for q in P])
        hp = float(near_pred[iy, ix].mean())
        ho = float(near_obs[iy, ix].mean())
        d = {"n": len(P), f"hit_rate_sim_{int(buffer_m)}m": hp, "chance_rate_sim": base_pred,
             "lift_sim": hp / max(base_pred, 1e-9), "hit_rate_flood100_map": ho, "chance_rate_flood100": base_obs,
             "lift_flood100_map": ho / max(base_obs, 1e-9)}
        if kind == "sewage":
            d["hit_rate_backflow_100m"] = float(near_bf[iy, ix].mean())
            d["chance_rate_backflow_100m"] = float(near_bf[lf].mean())
        out[f"311_{kind}"] = d
    res.validation = out
    return out


def plot_result(res: WaterResult, grid: Grid, path: str, title: str = ""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    ext = [grid.x0, grid.x0 + grid.nx * grid.dx, grid.y0, grid.y0 + grid.ny * grid.dx]
    fig, ax = plt.subplots(figsize=(11, 12.5), dpi=110)
    zz = np.where(grid.cover == 3, np.nan, grid.z)
    ax.imshow(zz, origin="lower", extent=ext, cmap="Greys_r", alpha=0.55, vmin=-2, vmax=np.nanpercentile(zz, 99))
    bl = np.ma.masked_where(grid.bldg_id < 0, np.ones_like(grid.z))
    ax.imshow(bl, origin="lower", extent=ext, cmap="Greys", vmin=0, vmax=3, alpha=0.6)
    bay = np.ma.masked_where(grid.cover != 3, np.ones_like(grid.z))
    ax.imshow(bay, origin="lower", extent=ext, cmap=LinearSegmentedColormap.from_list("b", ["#6d8fb3", "#6d8fb3"]))
    md = np.ma.masked_where(res.max_depth < 0.05, res.max_depth)
    im = ax.imshow(md, origin="lower", extent=ext, cmap="turbo", vmin=0.05, vmax=1.0, interpolation="nearest")
    ax.contour(np.where(grid.flood100 > 0, 1.0, 0.0), levels=[0.5], origin="lower", extent=ext,
               colors="magenta", linewidths=0.7)
    pts = (grid.validation or {}).get("311_2022_12_31", [])
    for kind, col, mk in (("flooding", "red", "o"), ("sewage", "saddlebrown", "s")):
        P = [q for q in pts if q.get("kind") == kind]
        if P:
            ax.scatter([q["x"] for q in P], [q["y"] for q in P], s=10, facecolors="none", edgecolors=col,
                       marker=mk, linewidths=0.8, label=f"311 {kind} ({len(P)})")
    bf = res.inlet_state.max(0) == 2
    if bf.any():
        ax.scatter(grid.x0 + (res.inlet_ix[bf] + .5) * grid.dx, grid.y0 + (res.inlet_iy[bf] + .5) * grid.dx,
                   s=4, c="k", marker="x", linewidths=0.5, label=f"inlets backflowing ({int(bf.sum())})")
    ax.plot([], [], color="magenta", label="SFPUC 100-yr flood zone")
    ax.legend(loc="upper left", fontsize=8)
    plt.colorbar(im, ax=ax, shrink=0.6, label="max depth (m)")
    v = res.validation.get("flood100", {})
    ax.set_title(title + (f"\nCSI vs flood100 {v.get('CSI', 0):.2f}, POD {v.get('hit_rate_POD', 0):.2f}, "
                          f"mass err {res.mass_error_pct:.3f}%, {res.runtime_s:.0f}s" if v else ""), fontsize=10)
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, ROOT)
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", default="dec31_2022")
    ap.add_argument("--out", default=os.path.join(ROOT, "runs", "dev_water"))
    ap.add_argument("--i-design", type=float, default=None)
    ap.add_argument("--store-mm", type=float, default=None)
    ap.add_argument("--ext-m", type=float, default=None)
    ap.add_argument("--scheme", default=None, choices=["inertial", "hll"])
    ap.add_argument("--clog-max", type=float, default=None)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--no-frames", action="store_true")
    a = ap.parse_args()
    from sim.storm import load_storm
    prm = default_params()
    if a.i_design is not None:
        prm.i_design_mm_h = a.i_design
    if a.store_mm is not None:
        prm.store_mm = a.store_mm
    if a.scheme:
        prm.scheme = a.scheme
    if a.ext_m is not None:
        prm.ext_upstream_m = a.ext_m
    if a.clog_max is not None:
        prm.clog_max = a.clog_max
    t0 = time.time()
    grid = load_grid(params=prm)
    print(f"grid {grid.ny}x{grid.nx}, {len(grid.basin_info)} basins, {len(grid.inlets['ix'])} inlets, "
          f"load {time.time()-t0:.1f}s")
    storm = load_storm(a.storm)
    res = run_water(grid, storm, params=prm, threads=a.threads, keep_frames=not a.no_frames)
    val = validate(res, grid)
    os.makedirs(a.out, exist_ok=True)
    tag = f"{a.storm}_i{prm.i_design_mm_h:g}_s{prm.store_mm:g}_e{prm.ext_upstream_m:g}_c{prm.clog_max:g}"
    plot_result(res, grid, os.path.join(a.out, f"max_depth_{tag}.png"), title=f"StormSim water: {a.storm}")
    if not a.no_frames:
        save_result(res, os.path.join(a.out, f"water_{a.storm}.npz"))
        write_web_depth(res, os.path.join(a.out, f"frames_{a.storm}"), grid)
    json.dump(res.summary() | {"params": res.params, "basins": res.basin_info},
              open(os.path.join(a.out, f"summary_{tag}.json"), "w"), indent=1, default=float)
    print(json.dumps(val, indent=1))
