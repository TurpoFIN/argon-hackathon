"""Finite debris inventory, real drain IDs, constrained crews, replayable decisions."""
import heapq
import json
import math
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt, label
from scipy.spatial import cKDTree
from . import argon


def route(start, target, blocked, water):
    """A* over passable terrain, not an inferred street/sewer network."""
    ny, nx = blocked.shape
    if blocked[target] or water[target] > .3:
        return []
    queue = [(0, start)]; previous = {}; cost = {start: 0.}
    while queue:
        _, here = heapq.heappop(queue)
        if here == target:
            path = [here]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return path[::-1]
        y, x = here
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            node = y+dy, x+dx
            if not (0 <= node[0] < ny and 0 <= node[1] < nx) or blocked[node] or water[node] > .3:
                continue
            new = cost[here] + 1 + float(water[node])*10
            if new < cost.get(node, float("inf")):
                cost[node] = new; previous[node] = here
                priority = new + abs(node[0]-target[0]) + abs(node[1]-target[1])
                heapq.heappush(queue, (priority, node))
    return []


class World:
    def __init__(self, data: Path, p, z, walls, ocean, lon, lat, u, v, perception=None, planner=None):
        self.p, self.z, self.walls, self.ocean = p, z, walls, ocean
        self.lon, self.lat, self.u, self.v = lon, lat, u, v
        self.ny, self.nx = z.shape; self.cell = 12.
        self.blocked = walls | ocean
        self.events = []; self.frames = []; self.plans = []; self.argon_calls = 0
        self.rng = np.random.default_rng(20261025)
        self.planner = planner or argon.plan
        self.perception = perception or {"status": "disabled", "zones": []}
        self.planner_status = "Baseline response policy"
        if p.response_mode == "argon":
            self.planner_status = "Argon ready" if argon.api_key() else "Argon unavailable: GEMINI_API_KEY not configured; baseline policy active"
        _, nearest = distance_transform_edt(self.blocked, return_indices=True)
        transform = Transformer.from_crs(4326, 32610, always_xy=True)
        source = json.loads((data / "manifest.json").read_text())["grid"]
        west, north = source["origin_easting"], source["origin_northing"]
        self.drains = []
        for row in json.loads((data / "raw/jtgq-b7c5.json").read_text()):
            x, y = transform.transform(*row["location"]["coordinates"])
            ix, iy = int((x-west)//12), int((north-y)//12)
            if 0 <= iy < self.ny and 0 <= ix < self.nx and not ocean[iy, ix]:
                if walls[iy, ix]: iy, ix = int(nearest[0, iy, ix]), int(nearest[1, iy, ix])
                self.drains.append({"id": row["dpw_node_id"], "cell": (iy, ix),
                    "lon": float(lon[iy, ix]), "lat": float(lat[iy, ix]), "type": row["drain_type"]})
        if not self.drains:
            raise ValueError("No real drains in the modeled land area")
        self.drain_tree = cKDTree([(d["cell"][1], d["cell"][0]) for d in self.drains])
        # Normalize the exponential response so the UI's 100% boundary is exactly closed.
        self.clog = np.full(len(self.drains), -3*math.log(1-.999*p.blockage_pct/100))
        self.initial_clog_kg = float(self.clog.sum()); self.cleared_kg = 0.; self.deposited_kg = 0.; self.escaped_kg = 0.
        self.delivered_kg = 0.
        valid = np.argwhere(~self.blocked & (z < 18))
        selected = valid
        zones = self.perception.get("zones", [])
        mask = np.zeros(z.shape, dtype=bool)
        w, s, e, n = self.perception.get('bbox', argon.BBOX)
        for zone in zones:
            t, l, b, r = zone["box"]
            mask |= (lon >= w+(e-w)*l/1000) & (lon <= w+(e-w)*r/1000) & (lat <= n-(n-s)*t/1000) & (lat >= n-(n-s)*b/1000)
        candidates = np.argwhere(mask & ~self.blocked & (z < 18))
        if len(candidates): selected = candidates
        self.source_mode = "Argon imagery candidate zones" if len(candidates) else "Seeded land inventory (scenario assumption)"
        count = 192 if p.debris_kg else 0
        starts = selected[self.rng.integers(0, len(selected), count)]
        self.parcels = [{"id": i, "x": float(x), "y": float(y), "kg": p.debris_kg/max(count,1),
            "release": int(self.rng.integers(0, 2400)), "active": False, "done": False} for i, (y, x) in enumerate(starts)]
        self.crews = []
        components, _ = label(~self.blocked)
        sizes = np.bincount(components.ravel()); sizes[0] = 0
        staging = np.argwhere((components == sizes.argmax()) & (z < 18))
        for i in range(p.clearing_crews):
            # Reproducible, dry initial staging cells distributed over the real land domain.
            y, x = staging[min(len(staging)-1, int((i+.5)*len(staging)/max(p.clearing_crews,1)))]
            self.crews.append({"id": f"Crew {i+1}", "y": float(y), "x": float(x), "target": None,
                "path": [], "progress": 0., "state": "idle", "source": "baseline"})
        self.priority = []; self.priority_source = "baseline"

    def event(self, seconds, kind, **data):
        self.events.append({"time_seconds": seconds, "kind": kind, **data})

    def blockage(self):
        return np.minimum(1.,(1-np.exp(-self.clog/3))/.999)

    def rates(self):
        result = np.zeros_like(self.z)
        for d, blockage in zip(self.drains, self.blockage()):
            result[d["cell"]] += self.p.drain_capacity_lps/1000*(1-blockage)/self.cell**2
        return result

    def dispatch(self, seconds, h):
        occupied = {c["target"] for c in self.crews if c["target"] is not None}
        choices = []
        for i, (d, block) in enumerate(zip(self.drains, self.blockage())):
            if i in occupied or block < .22 or h[d["cell"]] > .3: continue
            y, x = d["cell"]
            wet = float(np.max(h[max(0,y-2):y+3,max(0,x-2):x+3]))
            nearest = min((math.hypot(c["x"]-x,c["y"]-y)*12 for c in self.crews), default=0)
            choices.append({"id": d["id"], "i": i, "blockage_pct": round(block*100,1),
                "nearby_depth_m": round(wet,3), "distance_m": round(nearest), "score": float(block*(1+wet)/(1+nearest/500))})
        choices.sort(key=lambda d:d["score"], reverse=True)
        choices = choices[:16]
        if self.p.response_mode == "argon" and argon.api_key() and self.argon_calls < 2 and seconds in (600,1800) and choices:
            self.argon_calls += 1
            try:
                clean = [{k:v for k,v in d.items() if k not in {"i", "score"}} for d in choices]
                result = self.planner(clean, [{"id":c["id"],"state":c["state"]} for c in self.crews], seconds)
                self.priority = result["drain_ids"]; self.priority_source = "argon"
                self.planner_status = "Argon response plan applied"
                self.plans.append({"time_seconds":seconds, **result})
                self.event(seconds, "argon_plan", reason=result["reason"])
            except Exception as error:
                self.planner_status = f"Argon request failed ({type(error).__name__}); baseline policy active"
                self.event(seconds, "planner_unavailable", reason=self.planner_status)
        priority = {identity:i for i,identity in enumerate(self.priority)}
        choices.sort(key=lambda d:(priority.get(d["id"],1000),-d["score"]))
        for crew in self.crews:
            if crew["target"] is not None: continue
            start = (round(crew["y"]), round(crew["x"]))
            for candidate in choices:
                idx = candidate["i"]
                if idx in occupied: continue
                path = route(start, self.drains[idx]["cell"], self.blocked, h)
                if not path: continue
                source = "argon" if candidate["id"] in priority else "baseline"
                crew.update(target=idx,path=path[1:],progress=0.,state="traveling",source=source)
                occupied.add(idx)
                self.event(seconds,"dispatch",crew=crew["id"],drain_id=candidate["id"],planner=source)
                break

    def step(self, seconds, h, dt=5):
        for parcel in self.parcels:
            if parcel["done"] or seconds < parcel["release"]: continue
            if not parcel["active"]:
                parcel["active"] = True; self.delivered_kg += parcel["kg"]
            y, x = int(parcel["y"]), int(parcel["x"])
            # Assumed surface mobility: 3% of local modeled wind, capped at 1 m/s.
            dx = float(np.clip(self.u[y,x]*.03,-1,1))*dt/12
            dy = -float(np.clip(self.v[y,x]*.03,-1,1))*dt/12
            xx, yy = parcel["x"]+dx, parcel["y"]+dy
            if not (0 <= xx < self.nx and 0 <= yy < self.ny) or self.ocean[int(yy),int(xx)]:
                parcel["done"] = True; self.escaped_kg += parcel["kg"]; continue
            if not self.walls[int(yy),int(xx)]: parcel["x"], parcel["y"] = xx, yy
            distance, index = self.drain_tree.query([parcel["x"],parcel["y"]])
            # Hypothetical interception radius, scaled to the 12 m unresolved inlet geometry.
            if distance <= .7:
                before = self.blockage()[index]
                self.clog[index] += parcel["kg"]; self.deposited_kg += parcel["kg"]; parcel["done"] = True
                if before < .5 <= self.blockage()[index]:
                    self.event(seconds,"blocked",drain_id=self.drains[index]["id"])
        if seconds % 120 == 0:
            self.dispatch(seconds, h)
        for crew in self.crews:
            if crew["target"] is None: continue
            remaining = float(dt)
            if crew["path"]:
                while remaining > 0 and crew["path"]:
                    node = crew["path"][0]
                    if h[node] > .3:
                        self.event(seconds,"route_blocked",crew=crew["id"],drain_id=self.drains[crew["target"]]["id"])
                        crew.update(target=None,path=[],state="waiting",progress=0.); remaining=0; break
                    travel = (1-crew["progress"])*12/1.4
                    if travel > remaining:
                        crew["progress"] += remaining*1.4/12; remaining=0; break
                    crew["y"],crew["x"] = map(float,crew["path"].pop(0)); crew["progress"]=0; remaining-=travel
                if crew["target"] is None or crew["path"]: continue
            crew["state"] = "clearing"
            idx = crew["target"]; removed = min(float(self.clog[idx]), .025*remaining)
            self.clog[idx] -= removed; self.cleared_kg += removed
            if self.clog[idx] < .02:
                self.event(seconds,"cleared",crew=crew["id"],drain_id=self.drains[idx]["id"])
                crew.update(target=None,path=[],state="idle",progress=0.)

    def point(self, x, y):
        x,y = np.clip(x,0,self.nx-1),np.clip(y,0,self.ny-1)
        ix,iy = int(x),int(y); jx,jy = min(ix+1,self.nx-1),min(iy+1,self.ny-1)
        fx,fy=x-ix,y-iy
        return [round(float(a[iy,ix]*(1-fx)*(1-fy)+a[iy,jx]*fx*(1-fy)+a[jy,ix]*(1-fx)*fy+a[jy,jx]*fx*fy),7) for a in (self.lon,self.lat)]

    def crew_point(self, crew):
        if not crew['path']: return self.point(crew['x'],crew['y'])
        y,x=crew['path'][0];t=crew['progress']
        return self.point(crew['x']+(x-crew['x'])*t,crew['y']+(y-crew['y'])*t)

    def snapshot(self, seconds):
        blockage = self.blockage()
        self.frames.append({"time_seconds":seconds, "blockage":np.round(blockage*100).astype(int).tolist(),
            "debris":[{"id":p["id"],"position":self.point(p["x"],p["y"])} for p in self.parcels if p["active"] and not p["done"]],
            "crews":[{"id":c["id"],"position":self.crew_point(c),"state":c["state"],"planner":c["source"],
                "target":self.drains[c["target"]]["id"] if c["target"] is not None else None,
                "route":[self.point(x,y) for y,x in c["path"]]} for c in self.crews],
            "metrics":{"blocked_drains":int((blockage>=.5).sum()),"cleared_kg":round(self.cleared_kg,2),
                "deposited_kg":round(self.deposited_kg,2),"escaped_kg":round(self.escaped_kg,2)}})

    def export(self):
        airborne = sum(p["kg"] for p in self.parcels if p["active"] and not p["done"])
        residual = self.initial_clog_kg+self.delivered_kg-float(self.clog.sum())-self.cleared_kg-airborne-self.escaped_kg
        if abs(residual) > 1e-6: raise ArithmeticError("Debris mass balance failed")
        return {"drains":[{k:v for k,v in d.items() if k != "cell"} for d in self.drains],
            "frames":self.frames,"events":self.events,"plans":self.plans,"perception":self.perception,
            "planner_status":self.planner_status,"source_mode":self.source_mode,
            "budget":{"inventory_kg":self.p.debris_kg,"released_kg":self.delivered_kg,"initial_clog_kg":self.initial_clog_kg,
                "cleared_kg":self.cleared_kg,"remaining_clog_kg":float(self.clog.sum()),"airborne_kg":airborne,
                "escaped_kg":self.escaped_kg,"balance_error_kg":residual},
            "assumptions":["Debris inventory, interception radius, mobility and clearing rate are scenario assumptions, not field measurements.",
                "Crews walk at 1.4 m/s on the 12 m open-land grid, avoiding buildings, ocean and water deeper than 0.3 m. This is not a street-network or evacuation model.",
                "Aerial imagery is historical context for candidate debris sources; it does not observe current flooding or blockage."]}
