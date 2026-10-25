"""Read-only tools for the planning models: city_summary and inspect (computed from grid + no-plan run)."""
from __future__ import annotations

import functools
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from agent import runner

REPO = Path(__file__).resolve().parent.parent
GRID = REPO / "data" / "grid"


@functools.lru_cache(maxsize=1)
def grid():
    meta = json.loads((GRID / "meta.json").read_text())
    d = np.load(GRID / "city.npz")
    return meta, {k: d[k] for k in d.files}


@functools.lru_cache(maxsize=1)
def trees():
    return json.loads((GRID / "trees.json").read_text())


@functools.lru_cache(maxsize=4)
def max_depth(run_dir: str):
    """(maxdepth_m (ny8,nx8), dx_frame, x0, y0) from frames/depth.u16, or None."""
    fd = Path(run_dir) / "frames"
    try:
        man = json.loads((fd / "manifest.json").read_text())
        nx, ny, nf = man["nx"], man["ny"], man["n_frames"]
        arr = np.memmap(fd / "depth.u16", dtype=np.uint16, mode="r", shape=(nf, ny, nx))
        md = arr.max(axis=0).astype(np.float32) / 1000.0
        meta, _ = grid()
        return md, float(man.get("dx_frame", 8.0)), float(man.get("x0", meta["x0"])), float(man.get("y0", meta["y0"]))
    except Exception:
        return None


@functools.lru_cache(maxsize=4)
def basins(run_dir: str, thresh: float = 0.15):
    from scipy import ndimage
    r = max_depth(run_dir)
    if r is None:
        return []
    md, dx, x0, y0 = r
    meta, g = grid()
    z = g["z"]
    z8 = z[: md.shape[0] * 2, : md.shape[1] * 2]
    z8 = z8.reshape(z8.shape[0] // 2, 2, z8.shape[1] // 2, 2).mean((1, 3)) if z8.shape[0] >= 2 else None
    land = (z8 > -1.0) if z8 is not None and z8.shape == md.shape else np.ones_like(md, bool)
    lab, n = ndimage.label((md > thresh) & land)
    if n == 0:
        return []
    idx = np.arange(1, n + 1)
    area = ndimage.sum(np.ones_like(md), lab, idx) * dx * dx
    vol = ndimage.sum(md, lab, idx) * dx * dx
    mx = ndimage.maximum(md, lab, idx)
    com = ndimage.center_of_mass(np.ones_like(md), lab, idx)
    bbs = ndimage.find_objects(lab)
    order = np.argsort(-vol)[:25]
    out = []
    for rank, k in enumerate(order):
        cy, cx = com[k]
        sl = bbs[k]
        zmin = float(z8[lab == k + 1].min()) if z8 is not None and z8.shape == md.shape else None
        gx0, gy0 = meta["x0"], meta["y0"]
        out.append({"basin_id": rank + 1, "centroid_xy": [round(x0 - gx0 + (cx + 0.5) * dx, 1), round(y0 - gy0 + (cy + 0.5) * dx, 1)],
                    "area_m2": round(float(area[k])), "volume_m3": round(float(vol[k])), "max_depth_m": round(float(mx[k]), 2),
                    "bbox_xy": [round(x0 - gx0 + sl[1].start * dx), round(y0 - gy0 + sl[0].start * dx), round(x0 - gx0 + sl[1].stop * dx), round(y0 - gy0 + sl[0].stop * dx)],
                    "min_ground_z_m": round(zmin, 2) if zmin is not None else None, "_label": int(k + 1)})
    return out


def _bmap():
    return runner.buildings()


def _street_failures(run_dir: str):
    p = Path(run_dir) / "frames" / "trees.json"
    if not p.exists():
        return []
    failed = json.loads(p.read_text())
    T = {t["id"]: t for t in trees()}
    c = Counter()
    for f in failed:
        if f.get("fail_t") is not None and f.get("id") in T:
            c[T[f["id"]].get("street") or "?"] += 1
    return [{"street": s, "trees_failed": n} for s, n in c.most_common(15)]


def city_summary(storm, base_dir, base, budget):
    meta, g = grid()
    z = g["z"]
    land = g["cover"] != 3
    s = runner.summarize(base, None, budget, top_n=20)
    bs = basins(str(base_dir))
    B = _bmap()
    for b in bs[:12]:
        x0, y0, x1, y1 = b["bbox_xy"]
        gx, gy = meta["x0"], meta["y0"]
        ids = [k for k, v in (runner._ledger(base).get("by_building") or {}).items()
               if k in B and x0 <= B[k]["x"] - gx <= x1 and y0 <= B[k]["y"] - gy <= y1]
        b["buildings_damaged"] = len(ids)
        b["damage_usd"] = round(sum(float(runner._ledger(base)["by_building"][k]) for k in ids))
    try:
        from sim.storm import load_storm
        st = load_storm(storm)
        storm_info = {"name": st.name, "duration_s": float(st.duration_s), "peak_rain_mm_h": float(np.max(st.rain_mm_h)),
                      "total_rain_mm": float(np.trapezoid(st.rain_mm_h, st.t) / 3600), "peak_gust_ms": float(np.max(st.gust_ms)),
                      "wind_dir_deg": float(np.median(st.wind_dir_deg)) if np.ndim(st.wind_dir_deg) else float(st.wind_dir_deg),
                      "tide_max_m": float(np.max(st.tide_m)), "sources": list(getattr(st, "sources", []))[:8]}
    except Exception as e:
        storm_info = {"name": storm, "note": f"storm details unavailable: {e}"}
    return {
        "grid": {k: meta[k] for k in ("crs", "dx", "nx", "ny", "x0", "y0", "x1", "y1") if k in meta},
        "counts": meta.get("counts"),
        "elevation_m": {"land_min": round(float(z[land].min()), 2), "land_p5": round(float(np.percentile(z[land], 5)), 2),
                        "land_median": round(float(np.median(z[land])), 2), "land_max": round(float(z[land].max()), 2)},
        "storm": storm_info,
        "budget_usd": budget,
        "no_plan_run_id": Path(base_dir).name,
        "no_plan": {k: s[k] for k in ("damage_total_usd", "by_cause", "people", "trees_failed", "cars_damaged",
                                      "n_buildings_damaged", "validation", "metrics", "notes") if k in s},
        "flood_basins": [{k: v for k, v in b.items() if not k.startswith("_")} for b in bs[:12]],
        "worst_hit_buildings": s.get("top_remaining_damage"),
        "streets_with_failed_trees": _street_failures(str(base_dir)),
        "hint": "inspect('basin:1'), inspect('building:<id>'), inspect('region:x0,y0,x1,y1'), query('<python>')",
    }


def _region(x0, y0, x1, y1, base_dir, base):
    """bbox in LOCAL metres (UTM accepted)."""
    meta, g = grid()
    if max(x0, x1) < 100000:
        x0, x1 = x0 + meta["x0"], x1 + meta["x0"]
    if max(y0, y1) < 100000:
        y0, y1 = y0 + meta["y0"], y1 + meta["y0"]
    dx = meta["dx"]
    i0, i1 = int((y0 - meta["y0"]) // dx), int((y1 - meta["y0"]) // dx)
    j0, j1 = int((x0 - meta["x0"]) // dx), int((x1 - meta["x0"]) // dx)
    i0, j0 = max(i0, 0), max(j0, 0)
    sub = {k: v[i0:i1, j0:j1] for k, v in g.items()}
    if sub["z"].size == 0:
        return {"error": "region outside grid"}
    cov = Counter(sub["cover"].ravel().tolist())
    B = _bmap()
    by_b = runner._ledger(base).get("by_building") or {}
    inside = [b for b in B.values() if x0 <= b["x"] <= x1 and y0 <= b["y"] <= y1]
    dmg = sorted(((str(b["id"]), float(by_b.get(str(b["id"]), 0))) for b in inside), key=lambda t: -t[1])
    out = {"bbox_local": [round(x0 - meta["x0"]), round(y0 - meta["y0"]), round(x1 - meta["x0"]), round(y1 - meta["y0"])], "cells": int(sub["z"].size),
           "ground_z": {"min": round(float(sub["z"].min()), 2), "mean": round(float(sub["z"].mean()), 2), "max": round(float(sub["z"].max()), 2)},
           "cover_fraction": {["paved", "roof", "pervious", "bay"][k]: round(v / sub["z"].size, 3) for k, v in cov.items() if k < 4},
           "buildings": len(inside), "no_plan_damage_usd": round(sum(d for _, d in dmg)),
           "top_damaged": [{"id": k, "usd": round(v), "occupancy": B[k].get("occupancy"), "ground_z": B[k].get("ground_z")} for k, v in dmg[:10] if v > 0],
           "trees": sum(1 for t in trees() if x0 <= t["x"] <= x1 and y0 <= t["y"] <= y1)}
    try:
        inl = json.loads((GRID / "inlets.json").read_text())
        ii = [i for i in inl if x0 <= i["x"] <= x1 and y0 <= i["y"] <= y1]
        out["inlets"] = {"n": len(ii), "capacity_m3s": round(sum(i.get("capacity_m3s", 0) for i in ii), 2)}
    except Exception:
        pass
    r = max_depth(str(base_dir))
    if r is not None:
        md, fdx, fx0, fy0 = r
        a0, a1 = int((y0 - fy0) // fdx), int((y1 - fy0) // fdx)
        b0, b1 = int((x0 - fx0) // fdx), int((x1 - fx0) // fdx)
        sm = md[max(a0, 0):a1, max(b0, 0):b1]
        if sm.size:
            out["no_plan_max_depth_m"] = {"max": round(float(sm.max()), 2), "flooded_area_m2_gt15cm": round(float((sm > 0.15).sum() * fdx * fdx))}
    return out


def inspect_target(target: str, storm, base_dir, base, runs):
    kind, _, val = target.partition(":")
    kind, val = kind.strip().lower(), val.strip()
    B = _bmap()
    if kind == "building":
        b = B.get(val)
        if not b:
            return {"error": f"no building {val}"}
        d = {k: v for k, v in b.items() if k != "footprint"}
        meta, _ = grid()
        d["x_local"], d["y_local"] = round(b["x"] - meta["x0"], 1), round(b["y"] - meta["y0"], 1)
        d["footprint_vertices"] = len(b.get("footprint") or [])
        d["footprint"] = (b.get("footprint") or [])[:40]
        d["no_plan_damage_usd"] = (runner._ledger(base).get("by_building") or {}).get(val, 0)
        r = max_depth(str(base_dir))
        if r is not None:
            md, fdx, fx0, fy0 = r
            iy, ix = int((b["y"] - fy0) // fdx), int((b["x"] - fx0) // fdx)
            win = md[max(iy - 2, 0):iy + 3, max(ix - 2, 0):ix + 3]
            d["no_plan_max_water_depth_nearby_m"] = round(float(win.max()), 2) if win.size else None
        return d
    if kind == "basin":
        bs = basins(str(base_dir))
        b = next((x for x in bs if str(x["basin_id"]) == val), None)
        if not b:
            return {"error": f"no basin {val}; have 1..{len(bs)}"}
        out = {k: v for k, v in b.items() if not k.startswith("_")}
        out.update(_region(*b["bbox_xy"], base_dir, base))
        return out
    if kind == "tree":
        T = {str(t["id"]): t for t in trees()}
        t = T.get(val)
        if not t:
            return {"error": f"no tree {val}"}
        p = Path(base_dir) / "frames" / "trees.json"
        if p.exists():
            f = {str(x["id"]): x for x in json.loads(p.read_text())}.get(val)
            t = {**t, "no_plan_failure": f}
        return t
    if kind == "street":
        ts = [t for t in trees() if (t.get("street") or "").lower() == val.lower()]
        if not ts:
            return {"error": f"no trees on street '{val}' (street names come from trees.json)"}
        xs, ys = [t["x"] for t in ts], [t["y"] for t in ts]
        sp = Counter(t["species"] for t in ts).most_common(8)
        out = {"street": val, "trees": len(ts), "bbox": [min(xs), min(ys), max(xs), max(ys)], "top_species": sp}
        p = Path(base_dir) / "frames" / "trees.json"
        if p.exists():
            ids = {t["id"] for t in ts}
            out["no_plan_trees_failed"] = sum(1 for f in json.loads(p.read_text()) if f.get("id") in ids and f.get("fail_t") is not None)
        return out
    if kind == "region":
        try:
            x0, y0, x1, y1 = [float(v) for v in val.split(",")]
        except Exception:
            return {"error": "region:<xmin>,<ymin>,<xmax>,<ymax> (local m)"}
        return _region(x0, y0, x1, y1, base_dir, base)
    if kind == "run":
        r = next((r for r in runs if r.get("run_id") == val), None)
        return r["summary"] if r and r.get("ok") else {"error": f"no run {val}"}
    return {"error": "target must be building:/basin:/tree:/street:/region:/run:"}
