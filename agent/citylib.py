"""City loading/export used inside the plan sandbox and by the agent tools.

Uses the real `sim.city_api.CityEditor` when it exists; until then a StubCity that records the
contract helpers (so the agent loop can be built and tested end to end).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
GRID = REPO / "data" / "grid"
ARRAYS = ("z", "ksat", "manning", "cover", "bldg_id", "bldg_h", "flood100")


def _real_api():
    try:
        import sim.city_api as ca  # noqa: F401
        return ca
    except ModuleNotFoundError as e:
        if "city_api" in str(e) or "sim" == getattr(e, "name", ""):
            return None
        raise


def load_city(grid_dir: str | Path = GRID):
    ca = _real_api()
    if ca is not None:
        return ca.CityEditor()
    return StubCity(grid_dir)


CATEGORY_RULES = [  # (substring of object type/kind, category) - first match wins
    ("shelter", "emergency"), ("evac", "emergency"), ("rescue", "emergency"), ("medical", "emergency"),
    ("alert", "emergency"), ("generator", "emergency"),
    ("depot", "logistics"), ("truck", "logistics"), ("supply", "logistics"), ("staged", "logistics"),
    ("pre_position", "logistics"), ("sandbag_giveaway", "logistics"),
    ("storage", "drainage"), ("tunnel", "drainage"), ("pump", "drainage"), ("inlet", "drainage"),
    ("bioswale", "drainage"), ("permeable", "drainage"), ("green_roof", "drainage"), ("drain", "drainage"),
    ("sewer", "drainage"), ("regrade", "drainage"),
    ("wall", "flood_defence"), ("gate", "flood_defence"), ("berm", "flood_defence"), ("sandbag", "flood_defence"),
    ("floodproof", "flood_defence"), ("barrier", "flood_defence"), ("levee", "flood_defence"),
    ("wind_screen", "flood_defence"),
]
CATEGORIES = ("flood_defence", "drainage", "emergency", "logistics", "operations")


def category_of(o: dict) -> str:
    c = o.get("category")
    if c in CATEGORIES:
        return c
    t = f"{o.get('type', '')} {o.get('item_id', '')}".lower()
    return next((cat for sub, cat in CATEGORY_RULES if sub in t), "operations")


def annotate_objects(objs: list, ann: dict | None) -> list:
    """Apply Gemini's label/notes/category (keyed by helper item id or response action id) + default category."""
    ann = ann or {}
    for o in objs:
        a = None
        for key in (o.get("id"), o.get("item_id")):
            if key in ann:
                a = ann[key]
                break
        if a is None:
            oid = str(o.get("id", ""))
            a = next((v for k, v in ann.items() if oid.startswith(f"{k}_")), None)
        if a:
            base_label = o.get("label")
            if a.get("label"):
                o["label"] = a["label"]
                if base_label and base_label != a["label"]:
                    o["spec"] = base_label  # keep the helper's measured description
            if a.get("notes"):
                o["notes"] = a["notes"] + (f" ({o['notes']})" if o.get("notes") and o["notes"] != a["notes"] else "")
            if a.get("category") in CATEGORIES:
                o["category"] = a["category"]
        o["category"] = category_of(o)
        o.setdefault("phase", "pre_landfall" if float(o.get("t_s") or -1) < 0 else "storm")
    return objs


_QUERY_METHODS = {"utm", "cell", "polygon_mask", "polyline_cells", "log", "elevation", "is_building", "low_points",
                  "basin_of", "basins", "buildings_in", "building", "trees_near", "trees_in", "inlets_in", "cars_in",
                  "streets", "help", "to_water_edits", "tree_flags", "wind_screens_utm", "damage_plan",
                  "export_objects", "summary", "merged_floodproofed", "edited_trees", "edited_cars", "of", "get", "max_capacity", "building_info",
                  "to_json", "hash", "shelters", "sandbagged", "pump_sinks", "cost_lines", "total_cost", "plan_objects",
                  "for_grid"}


def _wrap_annotating(target, ed, obj_list_fn):
    """Give every action method of `target` optional label=/notes=/why=/category= kwargs."""
    import inspect as _insp
    for name in dir(type(target)):
        if name.startswith("_") or name in _QUERY_METHODS:
            continue
        attr = getattr(type(target), name, None)
        if not callable(attr) or isinstance(attr, (property, staticmethod, classmethod)):
            continue
        try:
            params = set(_insp.signature(attr).parameters)
        except Exception:
            continue
        orig = getattr(target, name)

        def wrapper(*a, _orig=orig, _params=params, **k):
            meta = {}
            for key in ("label", "notes", "why", "category"):
                if key in k and key not in _params:
                    meta["notes" if key == "why" else key] = k.pop(key)
            if "label" in _params and "label" in k:
                meta["label"] = k["label"]
            n0 = len(obj_list_fn())
            res = _orig(*a, **k)
            if meta:
                if isinstance(res, str):
                    ed.annotations[res] = {**ed.annotations.get(res, {}), **meta}
                for o in obj_list_fn()[n0:]:
                    ed.annotations[o.get("id")] = {**ed.annotations.get(o.get("id"), {}), **meta}
            return res
        try:
            setattr(target, name, wrapper)
        except Exception:
            pass


def annotating_editor():
    from sim.city_api import CityEditor
    ed = CityEditor()
    ed.annotations = {}
    _wrap_annotating(ed, ed, lambda: ed.objects)
    try:
        resp = ed.response
        _wrap_annotating(resp, ed, lambda: [])
    except Exception:
        pass
    return ed


def exec_plan_in(code: str):
    """(city, log, err). Uses sim.city_api.exec_plan (restricted builtins/imports) on an annotating editor:
    every helper accepts label=, notes= (or why=), category= so Gemini names and justifies each object."""
    ca = _real_api()
    if ca is not None and hasattr(ca, "exec_plan"):
        ed = annotating_editor()
        city, log, err = ca.exec_plan(code, editor=ed)
        try:
            annotate_objects(city.objects, getattr(city, "annotations", {}))
        except Exception:
            pass
        return city, log, err
    city = StubCity()
    ns = {"__name__": "plan", "np": np}
    try:
        exec(compile(code, "<plan>", "exec"), ns)
        ns["apply"](city)
        return city, city.plan_log, None
    except Exception as e:
        import traceback
        tb = traceback.format_exc().splitlines()
        return city, city.plan_log, f"{type(e).__name__}: {e}\n" + "\n".join(l for l in tb if "<plan>" in l)


def annotate_run_objects(run_dir, plan_dir) -> int:
    """Post-process runs/<id>/plan_objects.json with the plan's annotations + categories. Returns #objects."""
    rp = Path(run_dir) / "plan_objects.json"
    if not rp.exists():
        return 0
    ann = {}
    try:
        st = json.loads((Path(plan_dir) / "editor_state.json").read_text())["state"].get("annotations") or {}
        ann = st if isinstance(st, dict) and "__items__" not in st else {}
    except Exception:
        pass
    d = json.loads(rp.read_text())
    objs = d["objects"] if isinstance(d, dict) else d
    annotate_objects(objs, ann)
    rp.write_text(json.dumps(d if isinstance(d, dict) else {"objects": objs}))
    return len(objs)


def plan_log_of(city):
    for a in ("plan_log", "items"):
        v = getattr(city, a, None)
        if v is not None and not callable(v):
            return v
    return []


# ---- type-tagged, pickle-free editor state (safe to load on the host: data only, sim.* classes only)
SKIP_ATTRS = {"base", "_bl", "_tr"}


class _Enc:
    def __init__(self):
        self.arrays, self.skipped = {}, []

    def enc(self, v, path="x"):
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, np.ndarray):
            key = f"a{len(self.arrays)}"
            self.arrays[key] = v
            return {"__nd__": key}
        if isinstance(v, tuple):
            return {"__tuple__": [self.enc(x, path) for x in v]}
        if isinstance(v, (set, frozenset)):
            return {"__set__": [self.enc(x, path) for x in sorted(v, key=repr)]}
        if isinstance(v, list):
            return [self.enc(x, f"{path}[]") for x in v]
        if isinstance(v, dict):
            if all(isinstance(k, str) for k in v):
                return {k: self.enc(x, f"{path}.{k}") for k, x in v.items()}
            return {"__items__": [[self.enc(k, path), self.enc(x, path)] for k, x in v.items()]}
        mod = type(v).__module__
        if hasattr(v, "__dict__") and (mod.startswith("sim.") or mod.startswith("agent.citylib")):
            return {"__obj__": f"{mod}:{type(v).__qualname__}",
                    "state": {k: self.enc(x, f"{path}.{k}") for k, x in vars(v).items() if k not in SKIP_ATTRS and not callable(x)}}
        self.skipped.append(f"{path}: {type(v).__name__}")
        return None


def _dec(v, arrays):
    if isinstance(v, list):
        return [_dec(x, arrays) for x in v]
    if isinstance(v, dict):
        if "__nd__" in v:
            return arrays[v["__nd__"]]
        if "__tuple__" in v:
            return tuple(_dec(x, arrays) for x in v["__tuple__"])
        if "__set__" in v:
            return set(_dec(x, arrays) for x in v["__set__"])
        if "__items__" in v:
            return {_dec(k, arrays): _dec(x, arrays) for k, x in v["__items__"]}
        if "__obj__" in v:
            import importlib
            mod, qual = v["__obj__"].split(":")
            if not (mod.startswith("sim.") or mod == "agent.citylib"):
                raise ValueError(f"refusing to load class from {mod}")
            cls = importlib.import_module(mod)
            for part in qual.split("."):
                cls = getattr(cls, part)
            o = object.__new__(cls)
            o.__dict__.update({k: _dec(x, arrays) for k, x in v["state"].items()})
            return o
        return {k: _dec(x, arrays) for k, x in v.items()}
    return v


def export_city(city, out_dir: str | Path) -> dict:
    """Write the edited city to out_dir (no pickle): editor_state.json + editor_state.npz (changed arrays only),
    plan_objects.json, plan_summary.json. Reload on the host with load_editor(out_dir)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    e = _Enc()
    base = getattr(city, "base", None) or {}
    state = {}
    fresh = None
    for k, v in vars(city).items():
        if k in SKIP_ATTRS or callable(v):  # annotating wrappers are not state
            continue
        if k in ("buildings", "trees", "cars", "inlets") and isinstance(v, list):
            if fresh is None:
                try:
                    fresh = load_city()
                except Exception:
                    fresh = False
            if fresh is not False and v == getattr(fresh, k, None):
                continue  # unchanged: rebuilt from data/grid on load
        if isinstance(v, np.ndarray) and k in base and np.array_equal(v, base[k]):
            continue  # unchanged grid array: rebuilt from data/grid on load
        state[k] = e.enc(v, k)
    np.savez_compressed(out / "editor_state.npz", **e.arrays)
    (out / "editor_state.json").write_text(json.dumps({"class": f"{type(city).__module__}:{type(city).__qualname__}",
                                                        "state": state, "skipped": e.skipped}))
    info = {"method": "editor_state", "path": str(out), "skipped": e.skipped[:20]}
    for name, fn in (("plan_objects.json", "export_objects"), ("plan_summary.json", "summary")):
        f = getattr(city, fn, None)
        if callable(f):
            try:
                r = f()
                if name == "plan_objects.json":
                    r = annotate_objects(r, getattr(city, "annotations", {}))
                (out / name).write_text(json.dumps({"objects": r} if name == "plan_objects.json" else r, default=str))
            except Exception as ex:
                info[f"{fn}_error"] = f"{type(ex).__name__}: {ex}"
    return info


def load_editor(plan_dir: str | Path):
    """Host side: rebuild the edited CityEditor from a sandbox export (data only, never executes plan code)."""
    p = Path(plan_dir)
    meta = json.loads((p / "editor_state.json").read_text())
    with np.load(p / "editor_state.npz", allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    city = load_city()
    for k, v in meta["state"].items():
        if v is None and callable(getattr(type(city), k, None)):
            continue  # never shadow a method (old exports stored wrappers as null)
        setattr(city, k, _dec(v, arrays))
    if hasattr(city, "_bl"):
        city._bl = {int(b["id"]): b for b in city.buildings}
    if hasattr(city, "_tr"):
        city._tr = {int(t["id"]): t for t in city.trees}
    return city


# --------------------------------------------------------------------------- stub
class StubCity:
    """Minimal stand-in for sim.city_api.CityEditor (records ops, edits raw arrays)."""

    def __init__(self, grid_dir=GRID):
        g = Path(grid_dir)
        self.meta = json.loads((g / "meta.json").read_text())
        d = np.load(g / "city.npz")
        for k in d.files:
            setattr(self, k, d[k].copy())
        self.dx, self.x0, self.y0 = self.meta["dx"], self.meta["x0"], self.meta["y0"]
        self.ny, self.nx = self.z.shape
        self._z0 = self.z.copy()
        self.buildings = json.loads((g / "buildings.json").read_text())
        self.trees = json.loads((g / "trees.json").read_text())
        self.inlets = json.loads((g / "inlets.json").read_text())
        self.sinks: list = []
        self.plan_log: list = []

    # geometry helpers
    def ij(self, x, y):
        return int((y - self.y0) // self.dx), int((x - self.x0) // self.dx)

    def xy(self, iy, ix):
        return self.x0 + (ix + 0.5) * self.dx, self.y0 + (iy + 0.5) * self.dx

    def _mask(self, polygon):
        from matplotlib.path import Path as MPath
        poly = np.asarray(polygon, float)
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.dx
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.dx
        i0, i1 = np.searchsorted(ys, poly[:, 1].min()), np.searchsorted(ys, poly[:, 1].max())
        j0, j1 = np.searchsorted(xs, poly[:, 0].min()), np.searchsorted(xs, poly[:, 0].max())
        m = np.zeros((self.ny, self.nx), bool)
        if i1 > i0 and j1 > j0:
            X, Y = np.meshgrid(xs[j0:j1], ys[i0:i1])
            m[i0:i1, j0:j1] = MPath(poly).contains_points(np.c_[X.ravel(), Y.ravel()]).reshape(X.shape)
        return m

    def _op(self, kind, **kw):
        self.plan_log.append({"op": kind, **kw})

    def log(self, note):
        self._op("note", note=str(note))

    def wall(self, polyline, height_m):
        self._op("wall", polyline=polyline, height_m=height_m)

    def sandbags(self, polyline, height_m=0.6):
        self._op("sandbags", polyline=polyline, height_m=height_m)

    def regrade(self, polygon, dz_m):
        m = self._mask(polygon)
        self.z[m] += dz_m
        self._op("regrade", cells=int(m.sum()), dz_m=dz_m)

    def permeable(self, polygon):
        m = self._mask(polygon) & (self.cover == 0)
        self.ksat[m] = 50.0
        self._op("permeable", cells=int(m.sum()))

    def green_roof(self, building_ids):
        self._op("green_roof", n=len(list(building_ids)))

    def bioswale(self, polyline, width_m=3.0):
        self._op("bioswale", polyline=polyline, width_m=width_m)

    def storage(self, x, y, volume_m3, intake_m3s=1.0):
        self.sinks.append({"kind": "storage", "x": x, "y": y, "volume_m3": volume_m3, "intake_m3s": intake_m3s})
        self._op("storage", x=x, y=y, volume_m3=volume_m3, intake_m3s=intake_m3s)

    def pump(self, x, y, m3s, outfall="bay"):
        self.sinks.append({"kind": "pump", "x": x, "y": y, "m3s": m3s})
        self._op("pump", x=x, y=y, m3s=m3s)

    def tunnel(self, path_xy, m3s, outfall="bay"):
        self.sinks.append({"kind": "tunnel", "path": path_xy, "m3s": m3s})
        self._op("tunnel", n_pts=len(path_xy), m3s=m3s)

    def upgrade_inlets(self, sel, factor=2.0):
        self._op("upgrade_inlets", factor=factor)

    def add_inlets(self, points):
        self._op("add_inlets", n=len(points))

    def floodproof(self, building_ids, height_m=1.0):
        self._op("floodproof", n=len(list(building_ids)), height_m=height_m)

    def prune(self, tree_ids):
        self._op("prune", n=len(list(tree_ids)))

    def remove_trees(self, ids):
        self._op("remove_trees", n=len(list(ids)))

    def replace_trees(self, ids, species="Platanus x hispanica"):
        self._op("replace_trees", n=len(list(ids)), species=species)

    def brace(self, tree_ids):
        self._op("brace", n=len(list(tree_ids)))

    def wind_screen(self, polyline, height_m=3.0, porosity=0.5):
        self._op("wind_screen", height_m=height_m, porosity=porosity)

    def export(self, out_dir):
        out = Path(out_dir)
        np.savez_compressed(out / "edited_city.npz", z=self.z, ksat=self.ksat, manning=self.manning, cover=self.cover)
        (out / "plan_log.json").write_text(json.dumps(self.plan_log, default=str))
        (out / "sinks.json").write_text(json.dumps(self.sinks, default=str))
        return str(out)
