"""CityEditor: the API a plan's `apply(city)` code uses (owned by damage/costs agent).

Coordinates: LOCAL metres from the grid SW corner (x east 0..3548, y north 0..4256), the same as
plan_objects, sim/response.py and the web scene. UTM 10N values (x > 100000) are accepted too and
converted. Heights in metres, flows in m3/s, volumes in m3, times in sim seconds.

Everything is priced by sim/costs.py from the physical diff + the items registered here, using
data/reference/unit_costs.json only (the plan never sets a price). Deterministic: no randomness.

    city = CityEditor()                      # deep copy of data/grid
    city.wall([[1200, 900], [1500, 900]], height_m=1.2)
    city.storage(1300, 1100, volume_m3=20000, intake_m3s=4)
    city.z[...] -= 0.5                         # raw arrays are allowed; diff -> earthworks
    edits = city.to_water_edits()             # -> sim.water.run_water(grid, storm, edits)
    flags = city.tree_flags()                 # -> sim/trees.py  {tree_id: 'prune'|'brace'|'remove'|'replace'}
    screens = city.wind_screens_utm()         # -> sim/wind.py
    objs = city.export_objects()              # -> runs/<id>/plan_objects.json
    editor, log, err = exec_plan(code_str)
"""
from __future__ import annotations

import copy
import json
import math
import os
import traceback

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID_DIR = os.path.join(ROOT, "data", "grid")
REF = os.path.join(ROOT, "data", "reference")

_CACHE = {}


def _load_base():
    if "base" not in _CACHE:
        meta = json.load(open(os.path.join(GRID_DIR, "meta.json")))
        d = np.load(os.path.join(GRID_DIR, "city.npz"))
        arrs = {k: d[k].copy() for k in d.files}
        js = {}
        for k in ("buildings", "trees", "cars", "inlets"):
            p = os.path.join(GRID_DIR, f"{k}.json")
            js[k] = json.load(open(p)) if os.path.exists(p) else []
        _CACHE["base"] = (meta, arrs, js)
    return _CACHE["base"]


def unit_costs():
    if "uc" not in _CACHE:
        _CACHE["uc"] = json.load(open(os.path.join(REF, "unit_costs.json")))
    return _CACHE["uc"]


class PlanError(ValueError):
    pass


# ------------------------------------------------------------------ geometry
def _pip(px, py, poly):
    """Even-odd point-in-polygon, vectorised over points."""
    poly = np.asarray(poly, float)
    inside = np.zeros(np.shape(px), bool)
    xj, yj = poly[-1]
    for xi, yi in poly:
        cond = ((yi > py) != (yj > py)) & (px < (xj - xi) * (py - yi) / ((yj - yi) + 1e-12) + xi)
        inside ^= cond
        xj, yj = xi, yi
    return inside


def _polyline_len(pl):
    pl = np.asarray(pl, float)
    return float(np.sum(np.hypot(*np.diff(pl, axis=0).T))) if len(pl) > 1 else 0.0


class CityEditor:
    WATER_KEYS = ("z", "ksat", "manning", "cover")

    def __init__(self, meta=None, arrays=None, jsons=None):
        if meta is None:
            meta, arrays, jsons = _load_base()
        self.meta = dict(meta)
        self.nx, self.ny, self.dx = int(meta["nx"]), int(meta["ny"]), float(meta["dx"])
        self.x0, self.y0 = float(meta["x0"]), float(meta["y0"])
        self.width_m, self.height_m = self.nx * self.dx, self.ny * self.dx
        self.base = {k: np.asarray(v) for k, v in arrays.items()}        # read-only reference (not copied)
        for k, v in arrays.items():
            setattr(self, k, np.array(v, copy=True))                       # editable: z, ksat, manning, cover, bldg_id, bldg_h, flood100
        self.buildings = copy.deepcopy(jsons.get("buildings", []))
        self.trees = copy.deepcopy(jsons.get("trees", []))
        self.cars = copy.deepcopy(jsons.get("cars", []))
        self.inlets = copy.deepcopy(jsons.get("inlets", []))
        self._bl = {int(b["id"]): b for b in self.buildings}
        self._tr = {int(t["id"]): t for t in self.trees}
        # plan state
        self.items = []            # registered interventions (for costs.py)
        self.objects = []          # plan_objects (CONTRACT v2)
        self.notes = []
        self.storage_sinks, self.pump_sinks, self.tunnels, self.extra_inlets = [], [], [], []
        self.inlet_mult = {}       # {inlet_id: factor}
        self.clog_factor = {}      # {inlet_id: remaining capacity 0..1}; drain clearing -> 1.0
        self.sewer_initial_storage_frac = {}
        self.sewer_cap_add_m3s = {}
        self.roof_retention_mm = {}
        self.tree_actions = {}     # {tree_id: 'prune'|'brace'|'remove'|'replace'}
        self.replace_species = {}
        self.screens = []
        self.floodproofed = {}     # {bldg_id: height_m above first floor}
        self.contents_mult = {}    # {bldg_id: factor on contents damage}
        self.removed_cars = []
        self.road_closed = []
        self.power_off = []
        self.people_effects = []
        self._response = None
        self._n = 0

    # ---------------------------------------------------------------- coords
    def _xy(self, x, y):
        """-> local metres (accepts UTM)."""
        x, y = float(x), float(y)
        if x > 100000 or y > 100000:
            x, y = x - self.x0, y - self.y0
        return x, y

    def _pts(self, pts, name="points"):
        a = [self._xy(p[0], p[1]) for p in pts]
        if not a:
            raise PlanError(f"{name}: empty")
        return np.asarray(a, float)

    def utm(self, x, y):
        x, y = self._xy(x, y)
        return x + self.x0, y + self.y0

    def cell(self, x, y):
        x, y = self._xy(x, y)
        ix = int(np.clip(x // self.dx, 0, self.nx - 1))
        iy = int(np.clip(y // self.dx, 0, self.ny - 1))
        return iy, ix

    def polygon_mask(self, polygon):
        P = self._pts(polygon, "polygon")
        if len(P) < 3:
            raise PlanError("polygon needs >= 3 points [[x, y], ...]")
        xmin, ymin = np.clip(P.min(0) // self.dx, 0, None).astype(int)
        xmax = int(min(self.nx - 1, P[:, 0].max() // self.dx))
        ymax = int(min(self.ny - 1, P[:, 1].max() // self.dx))
        m = np.zeros((self.ny, self.nx), bool)
        if xmax < xmin or ymax < ymin:
            return m
        ys, xs = np.mgrid[ymin:ymax + 1, xmin:xmax + 1]
        m[ymin:ymax + 1, xmin:xmax + 1] = _pip((xs + 0.5) * self.dx, (ys + 0.5) * self.dx, P)
        return m

    def polyline_cells(self, polyline, width_m=None):
        P = self._pts(polyline, "polyline")
        if len(P) < 2:
            raise PlanError("polyline needs >= 2 points [[x, y], ...]")
        cells = set()
        half = max(0.0, (width_m or 0.0) / 2 - self.dx / 2)
        for a, b in zip(P[:-1], P[1:]):
            L = float(np.hypot(*(b - a)))
            n = max(2, int(L / (self.dx * 0.4)) + 1)
            d = (b - a) / max(L, 1e-9)
            nrm = np.array([-d[1], d[0]])
            offs = np.arange(-half, half + 1e-6, self.dx * 0.5) if half > 0 else [0.0]
            for f in np.linspace(0, 1, n):
                for o in offs:
                    q = a + f * (b - a) + o * nrm
                    ix, iy = int(q[0] // self.dx), int(q[1] // self.dx)
                    if 0 <= ix < self.nx and 0 <= iy < self.ny:
                        cells.add((iy, ix))
        cells = sorted(cells)
        return (np.array([c[0] for c in cells], int), np.array([c[1] for c in cells], int))

    def _id(self, kind):
        self._n += 1
        return f"{kind}_{self._n:03d}"

    def _local_pl(self, pl):
        return [[round(float(x), 2), round(float(y), 2)] for x, y in self._pts(pl)]

    def _reg(self, kind, qty_hint=None, obj=None, **params):
        iid = self._id(kind)
        it = {"id": iid, "kind": kind, **params}
        self.items.append(it)
        if obj is not None:
            obj = dict(obj)
            obj.setdefault("id", iid)
            obj.setdefault("type", kind)
            obj.setdefault("phase", "pre_landfall")
            obj.setdefault("t_s", params.get("t_s", -24 * 3600.0))
            obj["item_id"] = iid
            self.objects.append(obj)
        return iid

    def log(self, note):
        self.notes.append(str(note))
        return self

    # ---------------------------------------------------------------- query helpers (for the model)
    def elevation(self, x, y):
        iy, ix = self.cell(x, y)
        return float(self.z[iy, ix])

    def is_building(self, x, y):
        iy, ix = self.cell(x, y)
        return int(self.bldg_id[iy, ix])

    def low_points(self, n=10, min_sep_m=150.0, min_dist_bay_m=60.0):
        """Lowest land (non-building, non-bay) cells, separated by >= min_sep_m and >= min_dist_bay_m from the bay
        (underpasses and basins, not the seawall apron). [{x, y, z}] local m."""
        land = (self.cover != 3) & (self.bldg_id < 0)
        if min_dist_bay_m > 0 and (self.cover == 3).any():
            from scipy import ndimage
            land &= ndimage.distance_transform_edt(self.cover != 3) * self.dx >= min_dist_bay_m
        zz = np.where(land, self.z, np.inf)
        order = np.argsort(zz, axis=None, kind="stable")
        out = []
        for k in order[:200000]:
            iy, ix = divmod(int(k), self.nx)
            x, y = (ix + 0.5) * self.dx, (iy + 0.5) * self.dx
            if not np.isfinite(zz[iy, ix]):
                break
            if all(math.hypot(x - o["x"], y - o["y"]) >= min_sep_m for o in out):
                out.append({"x": x, "y": y, "z": round(float(zz[iy, ix]), 2)})
                if len(out) >= n:
                    break
        return out

    def basin_of(self, x, y):
        """Sub-sewershed id at (x, y) (sim/water.py basins; None if unavailable)."""
        b = self._basins()
        if b is None:
            return None
        iy, ix = self.cell(x, y)
        return int(b[iy, ix])

    def _basins(self):
        if "basin" not in _CACHE:
            try:
                from sim import water
                g = water.load_grid()
                _CACHE["basin"] = (g.basin, g.basin_info)
            except Exception:
                _CACHE["basin"] = (None, None)
        return _CACHE["basin"][0]

    def basins(self):
        self._basins()
        return _CACHE["basin"][1]

    def buildings_in(self, polygon, occupancy=None):
        P = self._pts(polygon)
        xs = np.array([b["x"] - self.x0 for b in self.buildings])
        ys = np.array([b["y"] - self.y0 for b in self.buildings])
        m = _pip(xs, ys, P)
        return [int(b["id"]) for b, k in zip(self.buildings, m) if k and (occupancy is None or str(b["occupancy"]).startswith(occupancy))]

    def building(self, bid):
        b = dict(self._bl[int(bid)])
        b.pop("footprint", None)
        b["x_local"], b["y_local"] = b["x"] - self.x0, b["y"] - self.y0
        return b

    def trees_near(self, x, y, r=50.0):
        x, y = self._xy(x, y)
        return [int(t["id"]) for t in self.trees if math.hypot(t["x"] - self.x0 - x, t["y"] - self.y0 - y) <= r]

    def trees_in(self, polygon, min_dbh_cm=0.0):
        P = self._pts(polygon)
        xs = np.array([t["x"] - self.x0 for t in self.trees])
        ys = np.array([t["y"] - self.y0 for t in self.trees])
        m = _pip(xs, ys, P)
        return [int(t["id"]) for t, k in zip(self.trees, m) if k and float(t.get("dbh_cm") or 0) >= min_dbh_cm]

    def inlets_in(self, polygon):
        P = self._pts(polygon)
        xs = np.array([i["x"] - self.x0 for i in self.inlets])
        ys = np.array([i["y"] - self.y0 for i in self.inlets])
        m = _pip(xs, ys, P)
        return [i["id"] for i, k in zip(self.inlets, m) if k]

    def cars_in(self, polygon):
        P = self._pts(polygon)
        xs = np.array([c["x"] - self.x0 for c in self.cars])
        ys = np.array([c["y"] - self.y0 for c in self.cars])
        m = _pip(xs, ys, P)
        return [int(c["id"]) for c, k in zip(self.cars, m) if k]

    def streets(self, name=None):
        """[{name, coords (local m)}] from DataSF street centrelines (filter by substring)."""
        if "streets" not in _CACHE:
            out = []
            p = os.path.join(ROOT, "data", "raw", "extra", "streets.geojson")
            try:
                from pyproj import Transformer
                tf = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
                for f in json.load(open(p))["features"]:
                    g = f.get("geometry") or {}
                    if g.get("type") != "LineString":
                        continue
                    xs, ys = tf.transform(*zip(*g["coordinates"]))
                    out.append({"name": f["properties"].get("streetname"),
                                "coords": [[round(a - self.x0, 1), round(b - self.y0, 1)] for a, b in zip(xs, ys)]})
            except Exception:
                pass
            _CACHE["streets"] = out
        s = _CACHE["streets"]
        return [r for r in s if name is None or (r["name"] and name.upper() in r["name"].upper())]

    def help(self):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "API_FOR_MODELS.md")
        return open(p).read() if os.path.exists(p) else __doc__

    @property
    def response(self):
        if self._response is None:
            try:
                from sim.response import ResponsePlan
                self._response = ResponsePlan.for_grid()
            except Exception as e:  # response module missing: keep plan code running
                raise PlanError(f"city.response unavailable: {e}")
        return self._response

    @response.setter
    def response(self, v):
        self._response = v

    # ---------------------------------------------------------------- infrastructure helpers
    def wall(self, polyline, height_m, width_m=0.4, kind="floodwall", t_s=-30 * 86400.0):
        """Permanent floodwall (kind='floodwall'), deployable barrier ('deployable') or passive flip-up gate ('flipup').
        Raises the crest of the cells under the line to ground + height_m (4 m cells)."""
        if not (0 < height_m <= 6):
            raise PlanError("wall height_m must be in (0, 6]")
        iy, ix = self.polyline_cells(polyline)
        keep = self.bldg_id[iy, ix] < 0
        iy, ix = iy[keep], ix[keep]
        crest = self.base["z"][iy, ix] + height_m
        self.z[iy, ix] = np.maximum(self.z[iy, ix], crest)
        L = _polyline_len(self._pts(polyline))
        prof = [[-width_m / 2, 0], [-width_m / 2, height_m], [width_m / 2, height_m], [width_m / 2, 0]]
        mat = {"floodwall": "concrete", "deployable": "fabric", "flipup": "steel"}.get(kind, "concrete")
        return self._reg(kind if kind in ("deployable", "flipup") else "floodwall", cells=(iy.tolist(), ix.tolist()),
                         length_m=L, height_m=height_m, t_s=t_s,
                         obj={"geometry": {"kind": "polyline", "coords": self._local_pl(polyline), "z_base": "ground",
                                           "height_m": height_m, "width_m": width_m, "profile": prof},
                              "style": {"color": "#9aa4ad", "material": mat},
                              "label": f"{kind} {L:.0f} m x {height_m:.1f} m"})

    def sandbags(self, polyline, height_m=0.3, t_s=-12 * 3600.0):
        """Sandbag line (USACE pyramid). Raises cells under the line by height_m (<= 1.0 m)."""
        if not (0 < height_m <= 1.0):
            raise PlanError("sandbag height_m must be in (0, 1.0]")
        iy, ix = self.polyline_cells(polyline)
        keep = self.bldg_id[iy, ix] < 0
        iy, ix = iy[keep], ix[keep]
        self.z[iy, ix] = np.maximum(self.z[iy, ix], self.base["z"][iy, ix] + height_m)
        L = _polyline_len(self._pts(polyline))
        w = 3 * height_m  # pyramid base = 3 x height (USACE)
        return self._reg("sandbags", cells=(iy.tolist(), ix.tolist()), length_m=L, height_m=height_m, t_s=t_s,
                         obj={"geometry": {"kind": "polyline", "coords": self._local_pl(polyline), "z_base": "ground",
                                           "height_m": height_m, "width_m": w,
                                           "profile": [[-w / 2, 0], [-height_m / 6, height_m], [height_m / 6, height_m], [w / 2, 0]]},
                              "style": {"color": "#c2a46b", "material": "fabric"}, "label": f"sandbags {L:.0f} m x {height_m:.1f} m"})

    def berm(self, polyline, height_m, width_m=8.0):
        """Earth berm / raised street crown: priced as earthworks (+ repaving on paved cells)."""
        iy, ix = self.polyline_cells(polyline, width_m=width_m)
        keep = self.bldg_id[iy, ix] < 0
        iy, ix = iy[keep], ix[keep]
        self.z[iy, ix] = np.maximum(self.z[iy, ix], self.base["z"][iy, ix] + height_m)
        self.objects.append({"id": self._id("berm"), "type": "berm", "phase": "pre_landfall", "t_s": -30 * 86400.0,
                             "geometry": {"kind": "polyline", "coords": self._local_pl(polyline), "z_base": "ground",
                                          "height_m": height_m, "width_m": width_m,
                                          "profile": [[-width_m / 2, 0], [-width_m / 6, height_m], [width_m / 6, height_m], [width_m / 2, 0]]},
                             "style": {"color": "#7d6b4f", "material": "concrete"}, "label": f"berm {height_m:.1f} m"})
        return self

    def regrade(self, polygon, dz_m=None, target_z=None):
        """Raise/lower ground in polygon by dz_m (or set to target_z m NAVD88). Buildings untouched. Priced as earthworks."""
        m = self.polygon_mask(polygon) & (self.bldg_id < 0) & (self.cover != 3)
        if target_z is not None:
            self.z[m] = float(target_z)
        elif dz_m is not None:
            self.z[m] += float(dz_m)
        else:
            raise PlanError("regrade needs dz_m or target_z")
        self.objects.append({"id": self._id("regrade"), "type": "regrade", "phase": "pre_landfall", "t_s": -30 * 86400.0,
                             "geometry": {"kind": "polygon", "coords": self._local_pl(polygon), "z_base": "ground", "height_m": 0.1},
                             "style": {"color": "#8d7b5f", "material": "concrete"},
                             "label": f"regrade {int(m.sum() * self.dx ** 2)} m2"})
        return int(m.sum())

    def permeable(self, polygon, ksat_mm_h=None):
        """Permeable pavement on paved (cover 0) cells in polygon: raises ksat. Priced per m2 resurfaced."""
        P = unit_costs()["permeable_pavement_m2"]
        k = float(ksat_mm_h if ksat_mm_h is not None else P.get("design_ksat_mm_h", 12.7))
        m = self.polygon_mask(polygon) & (self.cover == 0) & (self.bldg_id < 0)
        self.ksat[m] = np.maximum(self.ksat[m], k)
        self.objects.append({"id": self._id("permeable"), "type": "permeable", "phase": "pre_landfall", "t_s": -30 * 86400.0,
                             "geometry": {"kind": "polygon", "coords": self._local_pl(polygon), "z_base": "ground", "height_m": 0.05},
                             "style": {"color": "#5d8aa8", "material": "concrete"},
                             "label": f"permeable pavement {int(m.sum() * self.dx ** 2)} m2"})
        return int(m.sum() * self.dx ** 2)

    def bioswale(self, polyline, width_m=2.0, depth_m=0.3):
        """Bioretention swale: lowers cells by depth_m, engineered media ksat, pervious cover."""
        if not (0.5 <= width_m <= 12):
            raise PlanError("bioswale width_m in [0.5, 12]")
        iy, ix = self.polyline_cells(polyline, width_m=max(width_m, self.dx))
        keep = (self.bldg_id[iy, ix] < 0) & (self.cover[iy, ix] != 3)
        iy, ix = iy[keep], ix[keep]
        frac = min(1.0, width_m / self.dx)   # sub-cell swale: storage/infiltration scaled by width fraction
        self.z[iy, ix] = self.base["z"][iy, ix] - depth_m * frac
        self.ksat[iy, ix] = np.maximum(self.ksat[iy, ix], 127.0 * frac)  # SFPUC bioretention media 5 in/h
        self.cover[iy, ix] = 2
        self.manning[iy, ix] = np.maximum(self.manning[iy, ix], 0.035)
        L = _polyline_len(self._pts(polyline))
        return self._reg("bioswale", cells=(iy.tolist(), ix.tolist()), length_m=L, width_m=width_m, depth_m=depth_m,
                         obj={"geometry": {"kind": "polyline", "coords": self._local_pl(polyline), "z_base": "ground",
                                           "height_m": -depth_m, "width_m": width_m,
                                           "profile": [[-width_m / 2, 0], [-width_m / 4, -depth_m], [width_m / 4, -depth_m], [width_m / 2, 0]]},
                              "style": {"color": "#3c8d2f", "material": "holo"}, "label": f"bioswale {L:.0f} m x {width_m:.1f} m"})

    def green_roof(self, building_ids, retention_mm=25.0):
        ids = [int(b) for b in building_ids if int(b) in self._bl]
        for b in ids:
            self.roof_retention_mm[b] = float(min(retention_mm, 50.0))
        area = sum(float(self._bl[b].get("area_m2") or 0) for b in ids)
        return self._reg("green_roof", building_ids=ids, area_m2=area,
                         obj={"geometry": {"kind": "point", "coords": [[self._bl[b]["x"] - self.x0, self._bl[b]["y"] - self.y0] for b in ids][:500],
                                           "z_base": "roof", "height_m": 0.15},
                              "style": {"color": "#4caf50", "material": "holo"}, "label": f"green roofs on {len(ids)} buildings ({area:.0f} m2)"})

    def storage(self, x, y, volume_m3, intake_m3s=2.0, depth_m=8.0):
        """Underground storage vault/cistern fed from surface water at (x, y)."""
        if volume_m3 <= 0 or intake_m3s <= 0:
            raise PlanError("storage needs volume_m3 > 0 and intake_m3s > 0")
        x, y = self._xy(x, y)
        self.storage_sinks.append({"x": x + self.x0, "y": y + self.y0, "volume_m3": float(volume_m3), "intake_m3s": float(intake_m3s)})
        side = math.sqrt(volume_m3 / depth_m)
        return self._reg("storage", volume_m3=float(volume_m3), intake_m3s=float(intake_m3s), x=x, y=y,
                         obj={"geometry": {"kind": "point", "coords": [[x, y]], "z_base": "ground", "height_m": -depth_m,
                                           "width_m": side}, "capacity": float(volume_m3),
                              "style": {"color": "#2e86de", "material": "water"}, "label": f"storage vault {volume_m3:,.0f} m3"})

    def pump(self, x, y, m3s, outfall="bay"):
        if m3s <= 0:
            raise PlanError("pump m3s > 0")
        x, y = self._xy(x, y)
        self.pump_sinks.append({"x": x + self.x0, "y": y + self.y0, "m3s": float(m3s)})
        return self._reg("pump", m3s=float(m3s), x=x, y=y,
                         obj={"geometry": {"kind": "point", "coords": [[x, y]], "z_base": "ground", "height_m": 6.0,
                                           "width_m": 8.0 + 2 * m3s ** 0.5}, "capacity": float(m3s),
                              "style": {"color": "#e67e22", "material": "steel"}, "label": f"pump station {m3s:.1f} m3/s"})

    def tunnel(self, path_xy, m3s, outfall="bay", depth_m=30.0):
        if m3s <= 0:
            raise PlanError("tunnel m3s > 0")
        P = self._pts(path_xy)
        self.tunnels.append({"path_xy": [[a + self.x0, b + self.y0] for a, b in P], "m3s": float(m3s)})
        c = unit_costs()["tunnel_m_ref"]
        D = max(c["d_min_m"], math.sqrt(4 * m3s / (math.pi * c["v_design_ms"])))
        L = _polyline_len(P)
        return self._reg("tunnel", m3s=float(m3s), length_m=L, diameter_m=D,
                         obj={"geometry": {"kind": "polyline", "coords": self._local_pl(path_xy), "z_base": "ground",
                                           "depth_m": depth_m, "width_m": D, "height_m": D}, "capacity": float(m3s),
                              "style": {"color": "#34495e", "material": "concrete"}, "label": f"relief tunnel {L:.0f} m, D {D:.1f} m, {m3s:.0f} m3/s"})

    def _inlet_ids(self, sel):
        if isinstance(sel, (list, tuple)) and sel and isinstance(sel[0], (list, tuple)):
            return self.inlets_in(sel)
        return [s for s in (sel or [])]

    def upgrade_inlets(self, polygon_or_ids, factor=2.0):
        """Enlarge/rebuild inlets: capacity x factor (<= 4)."""
        if not (1.0 < factor <= 4.0):
            raise PlanError("upgrade_inlets factor in (1, 4]")
        ids = self._inlet_ids(polygon_or_ids)
        known = {i["id"] for i in self.inlets}
        ids = [i for i in ids if i in known]
        for i in ids:
            self.inlet_mult[i] = max(self.inlet_mult.get(i, 1.0), float(factor))
        return self._reg("inlet_upgrade", inlet_ids=ids, factor=factor)

    def add_inlets(self, points, capacity_m3s=0.05):
        P = self._pts(points)
        for x, y in P:
            self.extra_inlets.append({"x": x + self.x0, "y": y + self.y0, "capacity_m3s": float(capacity_m3s)})
        return self._reg("inlet_new", n=len(P), capacity_m3s=capacity_m3s,
                         obj={"geometry": {"kind": "point", "coords": P.round(2).tolist(), "z_base": "ground", "height_m": 0.1},
                              "style": {"color": "#00bcd4", "material": "steel"}, "label": f"{len(P)} new inlets"})

    def floodproof(self, building_ids, height_m=0.9):
        """Dry floodproofing (sealed walls, shields on openings, backflow valves) up to height_m (<= 0.9 m, FEMA P-936)."""
        if not (0 < height_m <= 0.9):
            raise PlanError("dry floodproofing height_m in (0, 0.9] (FEMA P-936 practical limit ~3 ft)")
        ids = [int(b) for b in building_ids if int(b) in self._bl]
        for b in ids:
            self.floodproofed[b] = max(self.floodproofed.get(b, 0.0), float(height_m))
        return self._reg("floodproof", building_ids=ids, height_m=height_m,
                         obj={"geometry": {"kind": "point", "coords": [[self._bl[b]["x"] - self.x0, self._bl[b]["y"] - self.y0] for b in ids][:500],
                                           "z_base": "ground", "height_m": height_m},
                              "style": {"color": "#f1c40f", "material": "steel"}, "label": f"dry floodproofing {len(ids)} buildings"})

    def _trees(self, ids):
        return [int(t) for t in ids if int(t) in self._tr]

    def prune(self, tree_ids):
        ids = self._trees(tree_ids)
        for t in ids:
            self.tree_actions.setdefault(t, "prune")
        return self._reg("tree_prune", tree_ids=ids)

    def brace(self, tree_ids):
        ids = self._trees(tree_ids)
        for t in ids:
            if self.tree_actions.get(t) not in ("remove", "replace"):
                self.tree_actions[t] = "brace"
        return self._reg("tree_brace", tree_ids=ids)

    def remove_trees(self, tree_ids):
        ids = self._trees(tree_ids)
        for t in ids:
            self.tree_actions[t] = "remove"
        return self._reg("tree_remove", tree_ids=ids)

    def replace_trees(self, tree_ids, species="Lophostemon confertus"):
        """Remove + plant a young wind-firm tree (default Brisbane box, SF Urban Forest Plan street tree list)."""
        ids = self._trees(tree_ids)
        for t in ids:
            self.tree_actions[t] = "replace"
            self.replace_species[t] = species
        return self._reg("tree_replace", tree_ids=ids, species=species)

    def wind_screen(self, polyline, height_m=3.0, porosity=0.5):
        if not (0 < height_m <= 12) or not (0.2 <= porosity <= 0.8):
            raise PlanError("wind_screen height_m in (0, 12], porosity in [0.2, 0.8]")
        P = self._pts(polyline)
        L = _polyline_len(P)
        self.screens.append({"polyline": P.tolist(), "height_m": float(height_m), "porosity": float(porosity)})
        return self._reg("wind_screen", length_m=L, height_m=height_m, porosity=porosity,
                         obj={"geometry": {"kind": "polyline", "coords": self._local_pl(polyline), "z_base": "ground",
                                           "height_m": height_m, "width_m": 0.1, "profile": [[0, 0], [0, height_m]]},
                              "style": {"color": "#95a5a6", "material": "fabric"}, "label": f"wind screen {L:.0f} m x {height_m:.0f} m"})

    # ---------------------------------------------------------------- city operations (pre-storm)
    def clear_drains(self, polygon_or_ids, t_s=-24 * 3600.0):
        """Pre-storm catch-basin clearing (leaf litter): inlets work at full capacity (clog_factor 1.0)."""
        ids = self._inlet_ids(polygon_or_ids)
        known = {i["id"] for i in self.inlets}
        ids = [i for i in ids if i in known]
        for i in ids:
            self.clog_factor[i] = 1.0
        return self._reg("drain_clear", inlet_ids=ids, t_s=t_s)

    def sewer_drawdown(self, basins="all", frac=0.0, t_s=-12 * 3600.0):
        """SFPUC pre-storm drawdown of storage/transport boxes: initial sewer fill V0/S -> frac."""
        if not (0.0 <= frac <= 0.15):
            raise PlanError("drawdown frac in [0, 0.15] (dry-weather fill is ~0.15)")
        if basins == "all":
            info = self.basins() or []
            basins = [int(b.get("id", k)) if isinstance(b, dict) else k for k, b in enumerate(info)]
        for b in basins:
            self.sewer_initial_storage_frac[int(b)] = float(frac)
        return self._reg("sewer_drawdown", basins=[int(b) for b in basins], frac=frac, t_s=t_s)

    def tow_cars(self, polygon, to=None, t_s=-6 * 3600.0):
        """Parking suspension + tow on flood-prone streets: cars in polygon removed before the storm."""
        ids = self.cars_in(polygon)
        new = [c for c in ids if c not in self.removed_cars]
        self.removed_cars.extend(new)
        return self._reg("tow", car_ids=new, t_s=t_s,
                         obj={"geometry": {"kind": "polygon", "coords": self._local_pl(polygon), "z_base": "ground", "height_m": 0.2},
                              "style": {"color": "#e74c3c", "material": "holo"}, "label": f"tow-away zone: {len(new)} cars"})

    def sandbag_giveaway(self, sites, bags, t_s=-48 * 3600.0):
        """Public sandbag distribution (SF Public Works: 10 bags/household). Each 20 bags = one doorway barrier
        0.3 m (1 ft x ~3.3 ft door, USACE 6 bags/ft) for the nearest residential/commercial buildings to each site."""
        P = self._pts(sites)
        bags = int(bags)
        per_bldg = 20
        nb = bags // per_bldg
        bx = np.array([b["x"] - self.x0 for b in self.buildings])
        by = np.array([b["y"] - self.y0 for b in self.buildings])
        chosen = []
        k_site = max(1, nb // len(P))
        for x, y in P:
            d = np.hypot(bx - x, by - y)
            for i in np.argsort(d, kind="stable")[:k_site]:
                chosen.append(int(self.buildings[int(i)]["id"]))
        for b in chosen:
            self.floodproofed[b] = max(self.floodproofed.get(b, 0.0), 0.3)
        return self._reg("sandbag_giveaway", bags=bags, building_ids=chosen, t_s=t_s,
                         obj={"geometry": {"kind": "point", "coords": P.round(2).tolist(), "z_base": "ground", "height_m": 1.5},
                              "style": {"color": "#c2a46b", "material": "fabric"}, "label": f"sandbag giveaway {bags} bags"})

    def deploy_portable_pumps(self, points, m3s_each=0.368, hours=None, t_s=-3 * 3600.0):
        """Trailer pumps (12 in diesel, 0.368 m3/s each, FEMA equipment schedule) at points, discharging to the bay/sewer."""
        P = self._pts(points)
        if not (0 < m3s_each <= 0.368 * 4):
            raise PlanError("m3s_each in (0, 1.47] (up to 4 x 12-in pumps per point)")
        for x, y in P:
            self.pump_sinks.append({"x": x + self.x0, "y": y + self.y0, "m3s": float(m3s_each), "start_s": float(t_s)})
        return self._reg("portable_pump", n_points=len(P), m3s_each=m3s_each, hours=hours, t_s=t_s,
                         obj={"geometry": {"kind": "point", "coords": P.round(2).tolist(), "z_base": "ground", "height_m": 1.8, "width_m": 3.0},
                              "style": {"color": "#ff9800", "material": "steel"}, "label": f"{len(P)} portable pumps"})

    def pre_position(self, kind, x, y, n=1, hours=24.0, t_s=-24 * 3600.0):
        """Stage equipment before landfall. kind: 'generator_100kw'|'generator_500kw'|'light_tower'|'bucket_truck'|
        'chipper'|'loader'|'dump_truck'|'tow_truck'|'vactor'. Priced as equipment-hours + operator hours."""
        table = {"generator_100kw": "generator_100kw_hr", "generator_500kw": "generator_500kw_hr", "light_tower": "light_tower_hr",
                 "bucket_truck": "bucket_truck_hr", "chipper": "chipper_hr", "loader": "loader_hr", "dump_truck": "truck_dump_hr",
                 "tow_truck": "tow_truck_hr", "vactor": "vactor_truck_hr"}
        if kind not in table:
            raise PlanError(f"pre_position kind must be one of {sorted(table)}")
        x, y = self._xy(x, y)
        return self._reg("pre_position", equip=table[kind], n=int(n), hours=float(min(hours, 120.0)), t_s=t_s,
                         obj={"geometry": {"kind": "point", "coords": [[x, y]], "z_base": "ground", "height_m": 3.0},
                              "style": {"color": "#9b59b6", "material": "steel"}, "label": f"{n} x {kind} staged"})

    def action(self, name, description, effects=None, cost_items=None, t_s=-24 * 3600.0):
        """Any other measure. effects: dict of supported levers (see API_FOR_MODELS.md). cost_items: [(item_id, qty)]
        referencing data/reference/unit_costs.json; quantities the planner can measure are re-measured."""
        uc = unit_costs()
        bad = [i for i, _ in (cost_items or []) if i not in uc or i.startswith("_")]
        if bad:
            raise PlanError(f"unknown cost items {bad}; available: {sorted(k for k in uc if not k.startswith('_'))}")
        if not cost_items:
            raise PlanError("city.action needs cost_items=[(item_id, qty), ...] from unit_costs.json")
        eff = dict(effects or {})
        applied = []
        for k, v in eff.items():
            if k == "inlet_clog_clear":
                self.clear_drains(v, t_s=t_s)
                self.items.pop()  # priced by the action's own items
            elif k == "inlet_capacity_mult":
                f = float(v.get("factor", 1.0))
                if not (0.5 <= f <= 4):
                    raise PlanError("inlet_capacity_mult factor in [0.5, 4]")
                for i in self._inlet_ids(v.get("polygon") or v.get("ids")):
                    self.inlet_mult[i] = f
            elif k == "sewer_initial_storage_frac":
                for b, f in (v.items() if isinstance(v, dict) else []):
                    self.sewer_initial_storage_frac[int(b)] = float(np.clip(f, 0, 0.15))
            elif k == "relocate_cars":
                ids = self.cars_in(v.get("polygon", v) if isinstance(v, dict) else v)
                self.removed_cars.extend([c for c in ids if c not in self.removed_cars])
            elif k == "road_closed":
                self.road_closed.append(self._local_pl(v))
            elif k == "contents_mult":
                f = float(v.get("factor", 1.0))
                lo = 0.65  # USACE Day curve: max ~35% contents damage reduction with long warning (EM 1110-2-1619)
                ids = v.get("building_ids") or (self.buildings_in(v["polygon"]) if "polygon" in v else [])
                for b in ids:
                    self.contents_mult[int(b)] = max(lo, min(1.0, f))
            elif k == "power_off":
                self.power_off.append(self._local_pl(v))
            elif k == "people":
                self.people_effects.append(v)
            else:
                raise PlanError(f"unsupported effect {k!r}; supported: inlet_clog_clear, inlet_capacity_mult, "
                                "sewer_initial_storage_frac, relocate_cars, road_closed, contents_mult, power_off, people")
            applied.append(k)
        return self._reg("action", name=str(name), description=str(description), effects=applied,
                         cost_items=[(str(i), float(q)) for i, q in cost_items], t_s=t_s,
                         obj={"geometry": {"kind": "point", "coords": [[self.width_m / 2, self.height_m / 2]], "z_base": "ground", "height_m": 0},
                              "style": {"color": "#bdc3c7", "material": "holo"}, "label": str(name)})

    # ---------------------------------------------------------------- custom 3D
    def add_object(self, obj, represents=None):
        """Add a plan_object (CONTRACT v2 schema, local m). `represents` = item id returned by a helper
        (the object is that item's 3D design; no extra cost). Unpriced objects are flagged."""
        o = dict(obj)
        o.setdefault("id", self._id("obj"))
        o.setdefault("phase", "pre_landfall")
        o.setdefault("t_s", -24 * 3600.0)
        o["item_id"] = represents
        if represents is None:
            self.notes.append(f"object {o['id']} has no cost basis (visual only)")
        self.objects.append(o)
        return o["id"]

    def custom_mesh(self, positions, indices, label, style=None, cost_basis=None):
        """Custom 3D model (positions flat [x,y,z,...] local m, z above ground; indices triangles).
        cost_basis = item id of the helper it depicts (e.g. a gate drawn for a wall(..., kind='flipup'))."""
        pos = [float(v) for v in positions]
        idx = [int(v) for v in indices]
        if len(pos) % 3 or len(idx) % 3 or (idx and max(idx) >= len(pos) // 3):
            raise PlanError("custom_mesh: positions must be xyz triples and indices valid triangles")
        if len(pos) > 3 * 20000:
            raise PlanError("custom_mesh: <= 20000 vertices")
        xs, ys = pos[0::3], pos[1::3]
        return self.add_object({"type": "custom_mesh", "label": label, "style": style or {"color": "#ffffff", "material": "steel"},
                                "geometry": {"kind": "mesh", "coords": [[min(xs), min(ys)], [max(xs), max(ys)]], "z_base": "ground"},
                                "mesh": {"positions": pos, "indices": idx}}, represents=cost_basis)

    # ---------------------------------------------------------------- outputs
    def merged_floodproofed(self):
        fp = dict(self.floodproofed)
        if self._response is not None:
            try:
                for b, (h, _t) in self._response.sandbagged().items():
                    fp[int(b)] = max(fp.get(int(b), 0.0), float(h))
            except Exception:
                pass
        return fp

    @property
    def damage_mult(self):
        return {}

    def to_water_edits(self) -> dict:
        d = {}
        for k in self.WATER_KEYS:
            a = getattr(self, k)
            if not np.array_equal(a, self.base[k]):
                d[k] = a
        pumps = [dict(p) for p in self.pump_sinks]
        if self._response is not None:
            try:
                for x, y, q, t in self._response.pump_sinks():
                    pumps.append({"x": x + self.x0, "y": y + self.y0, "m3s": float(q), "start_s": float(t)})
            except Exception:
                pass
        d.update(storage=list(self.storage_sinks), pumps=pumps, tunnels=list(self.tunnels),
                 extra_inlets=list(self.extra_inlets))
        if self.inlet_mult:
            d["inlet_mult"] = dict(self.inlet_mult)
        if self.clog_factor:
            d["clog_factor"] = dict(self.clog_factor)
        if self.sewer_initial_storage_frac:
            d["sewer_initial_storage_frac"] = dict(self.sewer_initial_storage_frac)
        if self.sewer_cap_add_m3s:
            d["sewer_cap_add_m3s"] = dict(self.sewer_cap_add_m3s)
        if self.roof_retention_mm:
            d["roof_retention_mm"] = dict(self.roof_retention_mm)
        return d

    def edited_trees(self) -> list:
        """Tree list for sim/trees.py: removed trees absent, pruned/braced flags set, replaced trees young (5 cm DBH)."""
        out = []
        for t in self.trees:
            a = self.tree_actions.get(int(t["id"]))
            if a == "remove":
                continue
            t = dict(t)
            if a == "prune":
                t["pruned"] = True
            elif a == "brace":
                t["braced"] = True
            elif a == "replace":
                t.update(species=self.replace_species.get(int(t["id"]), t.get("species")), dbh_cm=5.0, height_m=4.0,
                         crown_m=2.0, replaced=True)
            out.append(t)
        return out

    def edited_cars(self) -> list:
        rm = set(self.removed_cars)
        return [c for c in self.cars if int(c["id"]) not in rm]

    def tree_flags(self) -> dict:
        return dict(sorted(self.tree_actions.items()))

    def wind_screens_utm(self) -> list:
        return [{"polyline": [[x + self.x0, y + self.y0] for x, y in s["polyline"]], "height_m": s["height_m"],
                 "porosity": s["porosity"]} for s in self.screens]

    def damage_plan(self) -> dict:
        """What sim/damage.py needs: floodproofing, contents multipliers, removed cars."""
        return {"floodproofed": self.merged_floodproofed(), "damage_mult": {}, "contents_mult": dict(self.contents_mult),
                "removed_cars": sorted(set(self.removed_cars))}

    def export_objects(self, costed=None) -> list:
        """plan_objects (CONTRACT v2). costed: result of costs.price_plan (fills cost_usd per object)."""
        by_item = {}
        if costed:
            for it in costed.get("items", []):
                if it.get("item_id"):
                    by_item[it["item_id"]] = by_item.get(it["item_id"], 0.0) + it["usd"]
        out = []
        for o in self.objects:
            o = dict(o)
            o["cost_usd"] = round(by_item.get(o.get("item_id"), 0.0), 2)
            out.append(o)
        if self._response is not None:
            try:
                out.extend(self._response.plan_objects())
            except Exception as e:
                self.notes.append(f"response objects failed: {e}")
        return out

    def summary(self) -> dict:
        return {"items": [{k: v for k, v in it.items() if k != "cells"} for it in self.items], "notes": self.notes,
                "tree_actions": len(self.tree_actions), "removed_cars": len(self.removed_cars),
                "floodproofed": len(self.merged_floodproofed())}


# ------------------------------------------------------------------ exec
_SAFE_BUILTINS = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
                  for k in ("abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len",
                            "list", "map", "max", "min", "print", "range", "reversed", "round", "set", "sorted", "str",
                            "sum", "tuple", "zip", "ValueError", "Exception", "KeyError", "IndexError", "TypeError",
                            "__build_class__", "__name__", "object", "hasattr", "getattr", "divmod", "pow", "slice")}


def _imp(name, *a, **k):
    if name in ("math", "numpy", "itertools", "functools", "collections"):
        return __import__(name, *a, **k)
    raise ImportError(f"import {name!r} not allowed in plans (math, numpy, itertools, functools, collections)")


def exec_plan(code_str, editor=None):
    """Run plan code defining apply(city). Returns (editor, log, error_str|None). Pure Python, no I/O."""
    city = editor or CityEditor()
    printed = []
    b = dict(_SAFE_BUILTINS)
    b["__import__"] = _imp
    b["print"] = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    g = {"__builtins__": b, "np": np, "math": math, "__name__": "plan"}
    try:
        exec(compile(code_str, "<plan>", "exec"), g)
        if "apply" not in g:
            raise PlanError("plan must define def apply(city): ...")
        g["apply"](city)
        err = None
    except Exception as e:
        tb = traceback.format_exc().splitlines()
        err = f"{type(e).__name__}: {e}\n" + "\n".join(l for l in tb if "<plan>" in l)
    log = {"items": city.summary()["items"], "notes": city.notes, "printed": printed}
    return city, log, err
