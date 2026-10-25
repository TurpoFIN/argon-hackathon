"""StormSim SF emergency-response plan (owned by the people/response agent).

`ResponsePlan` is the object a plan's `apply(city)` code reaches as `city.response`.
It records emergency-management actions (shelters, evacuation orders, logistics, crews,
road closures, alerts, generators, medical posts), validates them against the real city
(buildings.json) and the cited planning factors in data/reference/response_costs.json,
and emits 3D `plan_objects` (CONTRACT v2) for every action. sim/people.py simulates it.

Prices: the model supplies quantities and choices only. Every dollar comes from
data/reference/response_costs.json (`cost_lines()`); quantities that depend on the storm
(person-nights, meals, crew-hours) are measured by sim/people.py.

Coordinates: local metres from the grid SW corner (UTM 10N minus meta x0, y0), same as
plan_objects and the web scene. Times: sim seconds from storm start (negative = before;
allowed range -72 h .. storm end).

Usage (CityEditor owner):  from sim.response import ResponsePlan
    city.response = ResponsePlan.for_grid()          # attach; plan code calls city.response.shelter(...)
    people = sim.people.run_people(storm, water, response=city.response, seed_key=plan_hash)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = os.path.join(ROOT, "data", "grid")
REF = os.path.join(ROOT, "data", "reference")

T_MIN = -72 * 3600.0
T_MAX = 48 * 3600.0
CREW_KINDS = ("sandbag", "pump", "tree", "rescue", "medical")
DEPOT_KINDS = ("food", "water", "medical", "sandbags", "pumps")
SHELTER_OK_OCC = ("EDU1", "EDU2", "GOV1", "GOV2", "REL1", "COM1", "COM2", "COM3", "COM4", "COM5",
                  "COM6", "COM7", "COM8", "COM9", "RES4", "IND1", "IND2", "IND3", "IND4", "IND5", "IND6")

_COSTS = None
_BLD = None
_META = None


def load_costs() -> dict:
    global _COSTS
    if _COSTS is None:
        with open(os.path.join(REF, "response_costs.json")) as f:
            _COSTS = json.load(f)
    return _COSTS


def factor(name: str) -> float:
    return float(load_costs()["planning_factors"][name]["value"])


def _meta():
    global _META
    if _META is None:
        with open(os.path.join(GRID, "meta.json")) as f:
            _META = json.load(f)
    return _META


def _buildings():
    global _BLD
    if _BLD is None:
        with open(os.path.join(GRID, "buildings.json")) as f:
            _BLD = json.load(f)
    return _BLD


class ResponseError(ValueError):
    """Invalid response action (message is written for the model to fix its code)."""


def _t(v, name):
    try:
        v = float(v)
    except Exception:
        raise ResponseError(f"{name} must be a number of sim seconds, got {v!r}")
    if not (T_MIN <= v <= T_MAX):
        raise ResponseError(f"{name}={v:.0f}s outside allowed range [-72 h, +48 h] relative to storm start")
    return v


def _xy(p, name):
    try:
        x, y = float(p[0]), float(p[1])
    except Exception:
        raise ResponseError(f"{name} must be (x, y) local metres, got {p!r}")
    m = _meta()
    W, H = m["nx"] * m["dx"], m["ny"] * m["dx"]
    if x > 1e5 or y > 1e5:          # UTM given by mistake: convert
        x, y = x - m["x0"], y - m["y0"]
    if not (-50 <= x <= W + 50 and -50 <= y <= H + 50):
        raise ResponseError(f"{name}=({x:.0f},{y:.0f}) outside the tile (0..{W:.0f}, 0..{H:.0f}) local m")
    return [round(x, 2), round(y, 2)]


def _poly(coords, name, closed=True):
    if coords is None or len(coords) < (3 if closed else 2):
        raise ResponseError(f"{name} needs >= {3 if closed else 2} (x, y) points")
    return [_xy(p, f"{name}[{i}]") for i, p in enumerate(coords)]


@dataclass
class Action:
    id: str
    kind: str
    t_s: float
    args: dict
    note: str = ""


class ResponsePlan:
    """Emergency-response half of a StormSim plan. See sim/RESPONSE_API.md."""

    def __init__(self):
        self.actions: list[Action] = []
        self.warnings: list[str] = []
        self._n = 0

    @classmethod
    def for_grid(cls):
        return cls()

    # ------------------------------------------------------------------ helpers
    def _add(self, kind, t_s, args, note=""):
        self._n += 1
        a = Action(id=f"{kind}_{self._n}", kind=kind, t_s=float(t_s), args=args, note=note)
        self.actions.append(a)
        return a.id

    def of(self, kind):
        return [a for a in self.actions if a.kind == kind]

    def get(self, aid):
        for a in self.actions:
            if a.id == aid:
                return a
        raise ResponseError(f"unknown action id {aid!r}")

    def _bld(self, bid, name="building_id"):
        B = _buildings()
        try:
            bid = int(bid)
        except Exception:
            raise ResponseError(f"{name} must be an integer index into buildings.json, got {bid!r}")
        if not (0 <= bid < len(B)):
            raise ResponseError(f"{name}={bid} is not a building (0..{len(B) - 1})")
        return bid, B[bid]

    @staticmethod
    def max_capacity(b: dict) -> int:
        """FEMA P-785 / Red Cross: 40 sq ft (3.716 m2) per person of usable sleeping floor."""
        fa = float(b.get("floor_area_m2") or b["area_m2"] * max(1, b.get("floors") or 1))
        return int(fa * factor("shelter_usable_fraction") / factor("shelter_m2_per_person"))

    # ------------------------------------------------------------------ API
    def shelter(self, building_id, capacity=None, open_at_s=-6 * 3600, supplies=None, name=None):
        """Open a congregate shelter in a real building. capacity is clamped to the
        FEMA/Red Cross 40 sq ft per person limit. supplies = {'meals': n, 'water_gal': n} on hand."""
        bid, b = self._bld(building_id)
        cap_max = self.max_capacity(b)
        if cap_max < 25:
            raise ResponseError(f"building {bid} ({b['occupancy']}, {b.get('floor_area_m2', 0):.0f} m2) is too small "
                                f"for a shelter (max {cap_max} people at 40 sq ft each)")
        cap = cap_max if capacity is None else int(capacity)
        if cap <= 0:
            raise ResponseError("shelter capacity must be > 0")
        if cap > cap_max:
            self.warnings.append(f"shelter {bid}: capacity {cap} > FEMA/ARC 40 sq ft limit {cap_max}; clamped")
            cap = cap_max
        if b["occupancy"] not in SHELTER_OK_OCC:
            self.warnings.append(f"shelter {bid}: occupancy {b['occupancy']} is unusual for a shelter "
                                 f"(prefer EDU1 schools, GOV1, REL1, COM halls)")
        sup = {"meals": 0.0, "water_gal": 0.0}
        for k, v in (supplies or {}).items():
            if k not in sup:
                raise ResponseError(f"shelter supplies keys are 'meals' and 'water_gal', got {k!r}")
            sup[k] = max(0.0, float(v))
        return self._add("shelter", _t(open_at_s, "open_at_s"),
                         {"building_id": bid, "capacity": cap, "supplies": sup, "name": name or f"Shelter {bid}"})

    def evacuate(self, polygon, order_at_s, to_shelters=None, priority="vulnerable", transport="auto"):
        """Evacuation order for everyone (priority='all') or vulnerable residents only
        (priority='vulnerable': elderly/disabled share from DataSF cne3-h93g) inside polygon.
        to_shelters: list of shelter action ids or building ids (default: all open shelters).
        transport: 'auto' (car owners drive, no-car vulnerable get paratransit/buses, others walk),
        'walk', or 'bus' (city buses for all no-car evacuees)."""
        if priority not in ("vulnerable", "all"):
            raise ResponseError("priority must be 'vulnerable' or 'all'")
        if transport not in ("auto", "walk", "bus"):
            raise ResponseError("transport must be 'auto', 'walk' or 'bus'")
        targets = []
        for s in (to_shelters or []):
            targets.append(self._shelter_bid(s))
        return self._add("evacuate", _t(order_at_s, "order_at_s"),
                         {"polygon": _poly(polygon, "polygon"), "to_shelters": targets,
                          "priority": priority, "transport": transport})

    def _shelter_bid(self, s):
        if isinstance(s, str):
            return int(self.get(s).args["building_id"])
        return self._bld(s, "to_shelters entry")[0]

    def depot(self, x, y=None, kind="food", name=None):
        """Staging depot for supplies. Returns its id for supply_run()."""
        if y is None and hasattr(x, "__len__"):
            x, y = x
        if kind not in DEPOT_KINDS:
            raise ResponseError(f"depot kind must be one of {DEPOT_KINDS}")
        return self._add("depot", T_MIN, {"xy": _xy((x, y), "depot"), "kind": kind, "name": name or f"{kind} depot"})

    def supply_run(self, depot, shelter_ids, trucks=1, depart_at_s=0, loop=True):
        """Trucks drive depot -> shelters on the street graph (blocked by water > 0.3 m and
        fallen trees), delivering meals and water in proportion to need, looping until the end."""
        d = self.get(depot) if isinstance(depot, str) else None
        if d is None or d.kind != "depot":
            raise ResponseError("supply_run depot must be an id returned by depot()")
        trucks = int(trucks)
        if not (1 <= trucks <= 200):
            raise ResponseError("trucks must be 1..200")
        bids = [self._shelter_bid(s) for s in shelter_ids]
        if not bids:
            raise ResponseError("supply_run needs at least one shelter")
        return self._add("supply_run", _t(depart_at_s, "depart_at_s"),
                         {"depot": depot, "shelters": bids, "trucks": trucks, "loop": bool(loop)})

    def crew(self, kind, n=1, stage_at=None, tasks=None, start_s=0, end_s=None):
        """Response crews. kind: 'rescue' (boat teams of 4: pull at-risk people out of water),
        'sandbag' (tasks = building ids; 0.3 m bag line at doorways), 'pump' (tasks = [(x, y)],
        6-in trash pumps), 'tree' (clear fallen trees off roads near stage_at), 'medical'."""
        if kind not in CREW_KINDS:
            raise ResponseError(f"crew kind must be one of {CREW_KINDS}")
        n = int(n)
        if not (1 <= n <= 500):
            raise ResponseError("crew n must be 1..500")
        st = _xy(stage_at, "stage_at") if stage_at is not None else None
        if kind in ("rescue", "tree", "medical") and st is None:
            raise ResponseError(f"{kind} crews need stage_at=(x, y)")
        tk = []
        for t in (tasks or []):
            if kind == "sandbag":
                tk.append(self._bld(t, "sandbag task")[0])
            elif kind == "pump":
                tk.append(_xy(t, "pump task"))
            else:
                tk.append(t)
        if kind == "sandbag" and not tk:
            raise ResponseError("sandbag crews need tasks=[building ids] to protect")
        if kind == "pump" and not tk:
            raise ResponseError("pump crews need tasks=[(x, y)] pump locations")
        s0 = _t(start_s, "start_s")
        e0 = _t(end_s, "end_s") if end_s is not None else None
        return self._add("crew", s0, {"kind": kind, "n": n, "stage_at": st, "tasks": tk, "end_s": e0})

    def close_road(self, polyline, at_s):
        """Close streets along a polyline (barricades): no routing through them after at_s.
        Use it on underpasses/low streets so drivers are not trapped in deep water."""
        return self._add("close_road", _t(at_s, "at_s"), {"polyline": _poly(polyline, "polyline", closed=False)})

    def alert(self, text, at_s, zone=None):
        """AlertSF / Wireless Emergency Alert. zone = polygon (None = whole tile). Improves the
        FD2321 flood-warning score for people in the zone (more lead time = fewer injuries)."""
        z = _poly(zone, "zone") if zone is not None else None
        return self._add("alert", _t(at_s, "at_s"), {"text": str(text)[:360], "zone": z})

    def generator(self, building_id, start_s=-3600):
        """150 kW backup generator at a building (shelters keep power)."""
        bid, b = self._bld(building_id)
        return self._add("generator", _t(start_s, "start_s"), {"building_id": bid})

    def medical_post(self, x, y=None, start_s=-3600, name=None):
        """Field treatment site with an ambulance on standby; rescued people go to the nearest one."""
        if y is None and hasattr(x, "__len__"):
            x, y = x
        return self._add("medical_post", _t(start_s, "start_s"), {"xy": _xy((x, y), "medical_post"),
                                                                   "name": name or "Medical post"})

    # ------------------------------------------------------------------ lookups (read-only)
    def buildings_in(self, polygon, occupancy_prefix=None, min_floor_area_m2=0.0):
        """Building ids whose centroid lies in polygon (local m), optionally filtered by
        Hazus occupancy prefix ('RES', 'EDU', 'GOV', 'COM8', ...). Sorted by id."""
        from shapely import contains_xy
        from shapely.geometry import Polygon
        B, m = _buildings(), _meta()
        poly = Polygon(_poly(polygon, "polygon"))
        xs = np.array([b["x"] - m["x0"] for b in B]); ys = np.array([b["y"] - m["y0"] for b in B])
        ins = np.flatnonzero(contains_xy(poly, xs, ys))
        out = []
        for i in ins:
            b = B[i]
            if occupancy_prefix and not str(b.get("occupancy", "")).startswith(occupancy_prefix):
                continue
            if float(b.get("floor_area_m2") or 0) < min_floor_area_m2:
                continue
            out.append(int(i))
        return out

    def building_info(self, building_id):
        """{id, x, y (local m), occupancy, floors, floor_area_m2, max_shelter_capacity, first_floor_m}."""
        bid, b = self._bld(building_id)
        m = _meta()
        return {"id": bid, "x": round(b["x"] - m["x0"], 1), "y": round(b["y"] - m["y0"], 1),
                "occupancy": b.get("occupancy"), "floors": b.get("floors"), "floor_area_m2": b.get("floor_area_m2"),
                "max_shelter_capacity": self.max_capacity(b), "first_floor_m": b.get("first_floor_m")}

    # ------------------------------------------------------------------ derived
    def to_json(self) -> dict:
        return {"actions": [asdict(a) for a in self.actions], "warnings": list(self.warnings)}

    def hash(self) -> str:
        s = json.dumps([asdict(a) for a in self.actions], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    def shelters(self):
        """[(action_id, building_id, capacity, open_at_s, supplies)] sorted by building id (deterministic)."""
        out = {}
        for a in self.of("shelter"):
            bid = a.args["building_id"]
            if bid in out:   # duplicate: merge capacity
                prev = out[bid]
                out[bid] = (prev[0], bid, prev[2] + a.args["capacity"], min(prev[3], a.t_s),
                            {k: prev[4][k] + a.args["supplies"][k] for k in prev[4]})
            else:
                out[bid] = (a.id, bid, a.args["capacity"], a.t_s, dict(a.args["supplies"]))
        return [out[k] for k in sorted(out)]

    def sandbagged(self) -> dict:
        """{building_id: (height_m, start_s)} from sandbag crews (doorway bag lines)."""
        h = factor("sandbag_height_m")
        out = {}
        for a in self.of("crew"):
            if a.args["kind"] == "sandbag":
                for bid in a.args["tasks"]:
                    out[int(bid)] = (h, a.t_s)
        return out

    def pump_sinks(self) -> list:
        """[(x_local, y_local, m3s, start_s)] for the water solver (6-in trash pumps)."""
        q = factor("pump_m3s")
        out = []
        for a in self.of("crew"):
            if a.args["kind"] == "pump":
                per = max(1, a.args["n"] // max(1, len(a.args["tasks"])))
                for xy in a.args["tasks"]:
                    out.append((xy[0], xy[1], q * per, a.t_s))
        return out

    # ------------------------------------------------------------------ costs
    def cost_lines(self, measured: Optional[dict] = None, duration_s: float = 21600.0) -> list:
        """Priced lines [{what, item, qty, unit, unit_cost, usd, source}] from response_costs.json.
        measured (from sim/people.py): person_nights, meals_delivered, water_gal_delivered,
        truck_hours, bus_hours, rescue_boat_hours, peak_shelter_occupancy."""
        C = load_costs()["items"]
        m = measured or {}
        lines = []

        def add(what, item, qty):
            if qty <= 0:
                return
            c = C[item]
            lines.append({"what": what, "item": item, "qty": round(float(qty), 2), "unit": c["unit"],
                          "unit_cost": c["usd"], "usd": round(float(qty) * c["usd"], 2), "source": c["source"]})

        t_end = duration_s
        sh = self.shelters()
        if sh:
            cap_total = sum(s[2] for s in sh)
            add("Shelter cots (one per space opened)", "cot", cap_total)
            pn = m.get("person_nights")
            if pn is None:
                pn = 0.0
            add("Shelter operations (occupant-nights, sim + projected stay)", "shelter_person_night", pn)
            # opening staff: 4 per 100 occupants per shift, from open time to storm end + projected stay
            occ = m.get("peak_shelter_occupancy", 0)
            hrs = sum(max(0.0, t_end - s[3]) for s in sh) / 3600.0 / max(1, len(sh))
            staff_h = max(len(sh) * 4, occ * factor("shelter_staff_per_100") / 100.0) * (hrs + 24 * factor("projected_shelter_nights"))
            add("Shelter staff (4 per 100 occupants per shift)", "crew_person_hour", staff_h)
        add("Meals delivered", "meal", m.get("meals_delivered", 0) + sum(s[4]["meals"] for s in sh))
        add("Drinking water delivered", "water_gal", m.get("water_gal_delivered", 0) + sum(s[4]["water_gal"] for s in sh))
        add("Supply trucks", "truck_hour", m.get("truck_hours", 0))
        add("Supply truck drivers", "driver_hour", m.get("truck_hours", 0))
        add("Evacuation buses / paratransit", "bus_hour", m.get("bus_hours", 0))
        add("Bus drivers", "driver_hour", m.get("bus_hours", 0))
        for a in self.of("crew"):
            k, n = a.args["kind"], a.args["n"]
            end = a.args["end_s"] if a.args["end_s"] is not None else t_end
            hrs = max(0.0, end - a.t_s) / 3600.0
            size = factor("rescue_crew_size") if k == "rescue" else 4
            add(f"{k} crew x{n} labor ({hrs:.1f} h)", "crew_person_hour", n * size * hrs)
            if k == "rescue":
                add(f"rescue boats x{n} ({hrs:.1f} h)", "rescue_boat_hour", n * hrs)
            elif k == "pump":
                add(f"6-in trash pumps x{n} ({hrs:.1f} h)", "pump_hour", n * hrs)
            elif k == "sandbag":
                bags = len(a.args["tasks"]) * factor("building_doorway_m") * factor("sandbag_bags_per_m_03m")
                add(f"sandbags for {len(a.args['tasks'])} buildings", "sandbag", bags)
                add("sandbag filling/placing labor", "crew_person_hour", bags / factor("sandbag_bags_per_crew_hour"))
        for a in self.of("generator"):
            hrs = max(0.0, t_end - a.t_s) / 3600.0 + 24 * factor("projected_shelter_nights")
            add(f"generator at building {a.args['building_id']}", "generator_hour", hrs)
        for a in self.of("medical_post"):
            hrs = max(0.0, t_end - a.t_s) / 3600.0
            add(f"medical post staff ({a.args['name']})", "crew_person_hour", factor("medical_post_staff") * hrs)
            add(f"ambulance standby ({a.args['name']})", "ambulance_hour", hrs)
        for a in self.of("close_road"):
            hrs = max(0.0, t_end - a.t_s) / 3600.0
            add("road closure barricade crew (2 people)", "crew_person_hour", 2 * min(hrs, 2.0))
        n_alert = len(self.of("alert"))
        if n_alert:
            add("AlertSF / WEA alerts", "alert_message", n_alert)
        return lines

    def total_cost(self, measured=None, duration_s=21600.0) -> float:
        return round(sum(l["usd"] for l in self.cost_lines(measured, duration_s)), 2)

    # ------------------------------------------------------------------ 3D objects
    def plan_objects(self, extra: Optional[list] = None, measured: Optional[dict] = None) -> list:
        """CONTRACT v2 plan_objects for every action (local m). sim/people.py passes `extra`
        (street-graph routes, truck paths, shelter occupancy) to append."""
        B = _buildings()
        m = _meta()
        x0, y0 = m["x0"], m["y0"]
        C = load_costs()["items"]
        objs = []

        def phase(t):
            return "pre_landfall" if t < 0 else "storm"

        for aid, bid, cap, t, sup in self.shelters():
            b = B[bid]
            fp = [[round(px - x0, 2), round(py - y0, 2)] for px, py in (b.get("footprint") or [])]
            cx, cy = round(b["x"] - x0, 2), round(b["y"] - y0, 2)
            if len(fp) >= 3:
                objs.append({"id": f"{aid}_bldg", "type": "shelter", "phase": phase(t), "t_s": t,
                             "geometry": {"kind": "polygon", "coords": fp, "z_base": "ground",
                                          "height_m": float(b.get("height_m") or 10)},
                             "style": {"color": "#2ecc71", "material": "holo"},
                             "label": f"Shelter: {self.get(aid).args['name']} ({cap} cots)",
                             "cost_usd": round(cap * C["cot"]["usd"], 2), "capacity": cap,
                             "building_id": bid, "notes": f"{b['occupancy']}, {b.get('floor_area_m2', 0):.0f} m2 floor; "
                                                          f"FEMA P-785 40 sq ft/person max {self.max_capacity(b)}"})
            objs.append({"id": f"{aid}_cots", "type": "shelter_cots", "phase": phase(t), "t_s": t,
                         "geometry": {"kind": "point", "coords": [[cx, cy]], "z_base": "ground",
                                      "height_m": 3.0, "width_m": max(8.0, min(40.0, math.sqrt(cap) * 1.2))},
                         "style": {"color": "#ffffff", "material": "fabric"},
                         "label": f"{cap} cots, {int(sup['meals'])} meals, {int(sup['water_gal'])} gal on hand",
                         "cost_usd": 0, "capacity": cap, "building_id": bid})
        for a in self.of("evacuate"):
            objs.append({"id": a.id, "type": "evac_zone", "phase": phase(a.t_s), "t_s": a.t_s,
                         "geometry": {"kind": "polygon", "coords": a.args["polygon"], "z_base": "ground", "height_m": 2.0},
                         "style": {"color": "#ff8c1a" if a.args["priority"] == "vulnerable" else "#ff3b30", "material": "holo"},
                         "label": f"Evacuation order ({a.args['priority']}) at T{a.t_s / 3600:+.1f} h",
                         "cost_usd": 0, "notes": f"transport={a.args['transport']}; to shelters {a.args['to_shelters'] or 'nearest open'}"})
        for a in self.of("depot"):
            objs.append({"id": a.id, "type": "depot", "phase": "pre_landfall", "t_s": a.t_s,
                         "geometry": {"kind": "point", "coords": [a.args["xy"]], "z_base": "ground", "height_m": 4.0, "width_m": 14.0},
                         "style": {"color": {"food": "#f1c40f", "water": "#3498db", "medical": "#e74c3c",
                                             "sandbags": "#a0522d", "pumps": "#7f8c8d"}[a.args["kind"]], "material": "steel"},
                         "label": a.args["name"], "cost_usd": 0})
        for a in self.of("supply_run"):
            d = self.get(a.args["depot"])
            objs.append({"id": a.id, "type": "trucks", "phase": phase(a.t_s), "t_s": a.t_s,
                         "geometry": {"kind": "point", "coords": [d.args["xy"]], "z_base": "ground", "height_m": 3.5, "width_m": 8.0},
                         "style": {"color": "#f39c12", "material": "steel"},
                         "label": f"{a.args['trucks']} supply trucks -> {len(a.args['shelters'])} shelters",
                         "cost_usd": 0, "capacity": a.args["trucks"]})
        for a in self.of("crew"):
            k = a.args["kind"]
            col = {"rescue": "#ff2d55", "sandbag": "#a0522d", "pump": "#5ac8fa", "tree": "#34c759", "medical": "#ffffff"}[k]
            if a.args["stage_at"] is not None:
                objs.append({"id": a.id, "type": f"crew_{k}", "phase": phase(a.t_s), "t_s": a.t_s,
                             "geometry": {"kind": "point", "coords": [a.args["stage_at"]], "z_base": "ground", "height_m": 2.5, "width_m": 6.0},
                             "style": {"color": col, "material": "holo"},
                             "label": f"{a.args['n']} {k} {'boat teams' if k == 'rescue' else 'crews'} staged T{a.t_s / 3600:+.1f} h",
                             "cost_usd": 0, "capacity": a.args["n"]})
            if k == "sandbag":
                h = factor("sandbag_height_m")
                for bid in a.args["tasks"]:
                    b = B[bid]
                    fp = [[round(px - x0, 2), round(py - y0, 2)] for px, py in (b.get("footprint") or [])]
                    if len(fp) >= 3:
                        objs.append({"id": f"{a.id}_b{bid}", "type": "sandbags", "phase": phase(a.t_s), "t_s": a.t_s,
                                     "geometry": {"kind": "polyline", "coords": fp + [fp[0]], "z_base": "ground",
                                                  "height_m": h, "width_m": 0.9, "profile": [[-0.45, 0], [-0.15, h], [0.15, h], [0.45, 0]]},
                                     "style": {"color": "#c2a878", "material": "fabric"},
                                     "label": f"Sandbags {h:.1f} m at building {bid}", "cost_usd": 0, "building_id": bid})
            if k == "pump":
                for j, xy in enumerate(a.args["tasks"]):
                    objs.append({"id": f"{a.id}_p{j}", "type": "pump_portable", "phase": phase(a.t_s), "t_s": a.t_s,
                                 "geometry": {"kind": "point", "coords": [xy], "z_base": "ground", "height_m": 1.5, "width_m": 2.5},
                                 "style": {"color": "#5ac8fa", "material": "steel"},
                                 "label": f"6-in trash pump ({factor('pump_m3s'):.3f} m3/s)", "cost_usd": 0})
        for a in self.of("close_road"):
            objs.append({"id": a.id, "type": "road_closure", "phase": phase(a.t_s), "t_s": a.t_s,
                         "geometry": {"kind": "polyline", "coords": a.args["polyline"], "z_base": "ground", "height_m": 1.1, "width_m": 0.5},
                         "style": {"color": "#ff9500", "material": "holo"}, "label": f"Road closed T{a.t_s / 3600:+.1f} h", "cost_usd": 0})
        for a in self.of("alert"):
            if a.args["zone"]:
                objs.append({"id": a.id, "type": "alert_zone", "phase": phase(a.t_s), "t_s": a.t_s,
                             "geometry": {"kind": "polygon", "coords": a.args["zone"], "z_base": "ground", "height_m": 60.0},
                             "style": {"color": "#ffcc00", "material": "holo"}, "label": f"AlertSF: {a.args['text'][:80]}", "cost_usd": 0})
        for a in self.of("generator"):
            b = B[a.args["building_id"]]
            objs.append({"id": a.id, "type": "generator", "phase": phase(a.t_s), "t_s": a.t_s,
                         "geometry": {"kind": "point", "coords": [[round(b["x"] - x0 + 6, 2), round(b["y"] - y0, 2)]],
                                      "z_base": "ground", "height_m": 2.2, "width_m": 4.5},
                         "style": {"color": "#8e8e93", "material": "steel"}, "label": "150 kW generator", "cost_usd": 0,
                         "building_id": a.args["building_id"]})
        for a in self.of("medical_post"):
            objs.append({"id": a.id, "type": "medical_post", "phase": phase(a.t_s), "t_s": a.t_s,
                         "geometry": {"kind": "point", "coords": [a.args["xy"]], "z_base": "ground", "height_m": 3.0, "width_m": 10.0},
                         "style": {"color": "#ff3b30", "material": "fabric"}, "label": a.args["name"], "cost_usd": 0})
        if extra:
            objs.extend(extra)
        return objs
