"""Data pack: everything a planning model gets, as one text block (same pack for every model).

Sections: storm forcing; no-plan ledger (by cause, people); ALL buildings (id, xy, occupancy, floors, value,
ground z, first floor, no-plan depth + damage); ALL inlets (capacity, basin, no-plan depth); street
segments (low point, max no-plan depth); trees (all, compact: species code, DBH, height, no-plan failure);
shelter candidates (EDU/GOV/COM8/REL, capacity at 40 sq ft/person, flood-safe?); population + flood-health
vulnerability by census block group; flood basins; locked cost table + response costs + params
(data/reference/*.json); SF's own plans (agent/pack_cache/sf_plans) and the Deep Research report.

Levels: full (~0.6-0.7M tokens, fits Gemini's 1M window), medium (~200k), small (~50k).
build_pack(storm, base_dir, level, model) -> (text, meta) ; meta records exactly what was included.
CLI: .venv/bin/python -m agent.datapack [--level full] [--base runs/<id>]
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from agent import runner
from agent.tools import basins, grid, max_depth, trees

REPO = Path(__file__).resolve().parent.parent
GRID = REPO / "data" / "grid"
REF = REPO / "data" / "reference"
CACHE = Path(__file__).resolve().parent / "pack_cache"
SHELTER_OCC = ("EDU1", "EDU2", "GOV1", "GOV2", "COM8", "REL1", "COM7")
LIMITS = {"full": {"doc_chars": 8_000, "trees": "failed", "buildings": "all", "inlets": "flooded", "streets": "flooded"},
          "medium": {"doc_chars": 8_000, "trees": "failed", "buildings": "damaged+shelters", "inlets": "flooded", "streets": "flooded"},
          "small": {"doc_chars": 2_500, "trees": "summary", "buildings": "top", "inlets": "summary", "streets": "top"}}
XO, YO = 550624.0, 4179056.0  # grid SW corner: tables use LOCAL metres (same as the city API)


def _depth_at(md_info, x, y, r=1):
    if md_info is None:
        return 0.0
    md, dx, x0, y0 = md_info
    iy, ix = int((y - y0) // dx), int((x - x0) // dx)
    w = md[max(iy - r, 0):iy + r + 1, max(ix - r, 0):ix + r + 1]
    return float(w.max()) if w.size else 0.0


def _f(v, nd=1):
    if v is None:
        return ""
    try:
        return f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def sec_storm(storm):
    try:
        from sim.storm import load_storm
        st = load_storm(storm)
        t = np.asarray(st.t)
        step = max(1, int(round(900 / max(float(np.median(np.diff(t))), 1))))
        rows = ["t_h,rain_mm_h,gust_ms,mean_ms,wind_dir_deg,tide_m"]
        wd = np.broadcast_to(np.asarray(st.wind_dir_deg, float), t.shape)
        mean = np.broadcast_to(np.asarray(getattr(st, "mean_ms", np.nan), float), t.shape)
        tide = np.broadcast_to(np.asarray(st.tide_m, float), t.shape)
        for i in range(0, len(t), step):
            rows.append(f"{t[i]/3600:.2f},{st.rain_mm_h[i]:.1f},{st.gust_ms[i]:.1f},{mean[i]:.1f},{wd[i]:.0f},{tide[i]:.2f}")
        src = "\n".join(f"- {s}" for s in list(getattr(st, "sources", []))[:15])
        return f"## Storm forcing: {st.name} (duration {st.duration_s/3600:.1f} h simulated)\n" + "\n".join(rows) + f"\nSources:\n{src}"
    except Exception as e:
        return f"## Storm forcing: {storm}\n(unavailable: {e})"


def sec_ledger(base):
    s = runner.summarize(base, None, top_n=0)
    keep = {k: s.get(k) for k in ("damage_total_usd", "by_cause", "people", "trees_failed", "cars_damaged",
                                  "n_buildings_damaged", "validation", "metrics", "notes", "response") if s.get(k) is not None}
    return "## No-plan ledger (the city does nothing)\n" + json.dumps(keep, default=str)


def sec_buildings(base, md, mode):
    by_b = runner._ledger(base).get("by_building") or {}
    B = runner.buildings()
    rows = ["id,xr,yr,occ,floors,ground_z,ff_m,value_k,np_depth_cm,np_damage_k"]
    items = list(B.values())
    if mode == "damaged+shelters":
        items = [b for b in items if float(by_b.get(str(b["id"]), 0)) > 0 or b.get("occupancy") in SHELTER_OCC]
    elif mode == "top":
        items = sorted(items, key=lambda b: -float(by_b.get(str(b["id"]), 0)))[:300]
    for b in items:
        dmg = float(by_b.get(str(b["id"]), 0) or 0)
        dep = _depth_at(md, b["x"], b["y"])
        rows.append(",".join([str(b["id"]), str(int(b["x"] - XO)), str(int(b["y"] - YO)), str(b.get("occupancy") or ""),
                              _f(b.get("floors"), 0), _f(b.get("ground_z"), 1), _f(b.get("first_floor_m"), 1),
                              str(int(((b.get("value_structure") or 0) + (b.get("value_contents") or 0)) / 1000)),
                              str(int(dep * 100)) if dep >= 0.01 else "", str(int(dmg / 1000)) if dmg >= 1000 else ""]))
    return (f"## Buildings ({len(items)} of {len(B)}; xr,yr = LOCAL metres from grid SW (same as city API); value_k = structure+contents $k; "
            f"np_ = no-plan run, blank = 0; full footprints via query/inspect)\n" + "\n".join(rows))


def sec_shelters(base, md):
    B = runner.buildings()
    rows = ["id,xr,yr,occ,floors,floor_area_m2,capacity_persons,ground_z,np_depth_cm"]
    n = 0
    for b in B.values():
        if b.get("occupancy") not in SHELTER_OCC:
            continue
        fa = float(b.get("floor_area_m2") or (b.get("area_m2") or 0) * max(1, b.get("floors") or 1))
        cap = int(fa * 0.5 / 3.716)  # 50% usable, FEMA 40 sq ft (3.716 m2) per person
        d = _depth_at(md, b["x"], b["y"], 2)
        rows.append(f"{b['id']},{int(b['x']-XO)},{int(b['y']-YO)},{b['occupancy']},{_f(b.get('floors'),0)},{fa:.0f},{cap},{_f(b.get('ground_z'),1)},{int(d*100)}")
        n += 1
    return (f"## Shelter candidates ({n}; capacity = 50% of floor area / 40 sq ft per person, FEMA shelter guidance)\n"
            + "\n".join(rows))


def sec_inlets(md, bs, mode):
    inl = json.loads((GRID / "inlets.json").read_text())

    def basin_of(x, y):
        for b in bs[:25]:
            x0, y0, x1, y1 = b["bbox_xy"]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return b["basin_id"]
        return ""
    if mode == "summary":
        c = Counter(i.get("type") for i in inl)
        return f"## Inlets: {len(inl)} total; by type {dict(c)}; total capacity {sum(i.get('capacity_m3s',0) for i in inl):.1f} m3/s"
    tcode = {t: f"T{i}" for i, (t, _) in enumerate(Counter(i.get("type") for i in inl).most_common())}
    rows = ["Type codes: " + "; ".join(f"{c}={t}" for t, c in tcode.items()), "id,xr,yr,type,cap_Ls,basin,np_depth_cm"]
    k = 0
    for i in inl:
        d = _depth_at(md, i["x"], i["y"])
        if mode == "flooded" and d < 0.1:
            continue
        rows.append(f"{i['id']},{int(i['x']-XO)},{int(i['y']-YO)},{tcode.get(i.get('type'))},{int((i.get('capacity_m3s') or 0)*1000)},{basin_of(i['x']-XO, i['y']-YO)},{int(d*100) if d >= 0.01 else ''}")
        k += 1
    return f"## Storm-drain inlets ({k} of {len(inl)}; surcharge/backup is per-run in the ledger)\n" + "\n".join(rows)


def sec_streets(md, mode):
    p = REPO / "data" / "raw" / "extra" / "streets.geojson"
    if not p.exists():
        return ""
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
    meta, g = grid()
    z, dx = g["z"], meta["dx"]
    feats = json.loads(p.read_text())["features"]
    rows = []
    for f in feats:
        geo = f.get("geometry") or {}
        cs = geo.get("coordinates") or []
        if geo.get("type") == "MultiLineString":
            cs = [c for part in cs for c in part]
        if len(cs) < 2:
            continue
        xs, ys = tr.transform([c[0] for c in cs], [c[1] for c in cs])
        xs, ys = np.asarray(xs), np.asarray(ys)
        L = float(np.hypot(np.diff(xs), np.diff(ys)).sum())
        n = max(2, int(L / 4))
        sx = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(xs)), xs)
        sy = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(ys)), ys)
        iy = ((sy - meta["y0"]) // dx).astype(int)
        ix = ((sx - meta["x0"]) // dx).astype(int)
        ok = (iy >= 0) & (iy < z.shape[0]) & (ix >= 0) & (ix < z.shape[1])
        if ok.sum() < 2:
            continue
        zz = z[iy[ok], ix[ok]]
        k = int(np.argmin(zz))
        dmax = max((_depth_at(md, a, b, 0) for a, b in zip(sx[ok][::3], sy[ok][::3])), default=0.0)
        pr = f.get("properties") or {}
        rows.append((dmax, f"{pr.get('cnn','')},{pr.get('streetname','')},{pr.get('f_st','')},{pr.get('t_st','')},{L:.0f},"
                           f"{xs[0]-XO:.0f},{ys[0]-YO:.0f},{xs[-1]-XO:.0f},{ys[-1]-YO:.0f},{zz.min():.1f},{sx[ok][k]-XO:.0f},{sy[ok][k]-YO:.0f},{int(dmax*100)}"))
    if mode == "flooded":
        rows = [r for r in rows if r[0] >= 0.1]
    elif mode == "top":
        rows = sorted(rows, key=lambda r: -r[0])[:150]
    hdr = f"cnn,street,from,to,length_m,xr_start,yr_start,xr_end,yr_end,low_z,low_xr,low_yr,np_max_depth_cm  (xr,yr local m)"
    return f"## Street segments ({len(rows)}; centrelines DataSF)\n{hdr}\n" + "\n".join(r[1] for r in rows)


def sec_trees(base_dir, mode):
    T = trees()
    fp = Path(base_dir) / "frames" / "trees.json"
    failed = {f["id"]: f for f in (json.loads(fp.read_text()) if fp.exists() else []) if f.get("fail_t") is not None}
    sp = Counter(t["species"] for t in T)
    codes = {s: f"S{i}" for i, (s, _) in enumerate(sp.most_common())}
    by_street = defaultdict(lambda: [0, 0])
    for t in T:
        by_street[t.get("street") or "?"][0] += 1
        if t["id"] in failed:
            by_street[t.get("street") or "?"][1] += 1
    out = [f"## Street trees ({len(T)}; no-plan failures {len(failed)})"]
    out.append("Failures by street (street,trees,failed): " + "; ".join(f"{s},{a},{b}" for s, (a, b) in sorted(by_street.items(), key=lambda kv: -kv[1][1])[:120]))
    if mode == "summary":
        return "\n".join(out)
    out.append("Species codes (code=species:count): " + "; ".join(f"{c}={s}:{sp[s]}" for s, c in list(codes.items())[:300]))
    rows = [f"Individual trees ({'no-plan failures' if mode == 'failed' else 'all'}; full list via query): id,xr,yr,sp,dbh_cm,h_m,np_fail(t_h/mode)"]
    for t in T:
        f = failed.get(t["id"])
        if mode == "failed" and not f:
            continue
        rows.append(f"{t['id']},{t['x']-XO:.0f},{t['y']-YO:.0f},{codes[t['species']]},{_f(t.get('dbh_cm'),0)},{_f(t.get('height_m'),0)},"
                    f"{(_f(f['fail_t']/3600,1)+'/'+f.get('mode','')[:1]) if f else ''}")
    return "\n".join(out + rows)


def sec_people():
    p = REF / "people_vulnerability_bg.json"
    if not p.exists():
        return ""
    v = json.loads(p.read_text())
    B = runner.buildings()
    meta, _ = grid()
    from matplotlib.path import Path as MPath
    res = [b for b in B.values() if str(b.get("occupancy", "")).startswith("RES")]
    pts = np.array([[b["x"] - meta["x0"], b["y"] - meta["y0"]] for b in res]) if res else np.zeros((0, 2))
    units = np.array([max(1.0, float(b.get("floor_area_m2") or b.get("area_m2") or 0) / 90.0) for b in res])
    rows = ["geoid,centroid_x,centroid_y,est_residents,elderly,disability,poverty,livalone,limited_english,children,flood_health_index"]
    for bg in v["block_groups"]:
        try:
            ring = np.asarray(bg["rings"][0], float)
        except Exception:
            continue
        inside = MPath(ring).contains_points(pts) if len(pts) else np.zeros(0, bool)
        pop = float(units[inside].sum() * 2.3)
        cx, cy = ring.mean(0) + [meta["x0"], meta["y0"]]
        rows.append(f"{bg['geoid']},{cx:.0f},{cy:.0f},{pop:.0f},{bg.get('elderly')},{bg.get('disability')},{bg.get('poverty')},"
                    f"{bg.get('livalone')},{bg.get('english')},{bg.get('children')},{bg.get('floodhealthindex')}")
    return ("## Population and flood-health vulnerability by census block group (shares; residents estimated from "
            "residential floor area / 90 m2 per unit x 2.3 persons; source: " + "; ".join(v.get("source", [])) + ")\n" + "\n".join(rows))


def sec_basins(base_dir):
    bs = basins(str(base_dir))
    return "## Flood basins in the no-plan run (depth > 0.15 m)\n" + json.dumps([{k: v for k, v in b.items() if not k.startswith("_")} for b in bs])


def sec_reference(doc_chars):
    out = ["## Locked reference tables (data/reference; the cost planner uses ONLY these items)"]
    for name in ("unit_costs.json", "response_costs.json", "damage_functions.json", "people_params.json",
                 "water_params.json", "wind_tree_params.json"):
        p = REF / name
        if p.exists():
            t = p.read_text()
            lim = 200_000 if name in ("unit_costs.json", "response_costs.json") else max(doc_chars, 8000)
            out.append(f"### {name}\n{t[:lim]}")
    return "\n".join(out)


def sec_docs(doc_chars):
    out = ["## San Francisco's own plans and documents (extracted text; cite by title + URL)"]
    d = CACHE / "sf_plans"
    idx = {}
    if (d / "index.json").exists():
        idx = {e["file"]: e for e in json.loads((d / "index.json").read_text())}
    for p in sorted(d.glob("*.md")):
        t = p.read_text()
        e = idx.get(p.name, {})
        out.append(f"### {e.get('title', p.stem)} ({e.get('url', '')})\n{t[:doc_chars]}" + ("\n[...truncated]" if len(t) > doc_chars else ""))
    dr = CACHE / "deep_research_sf_ar.md"
    if dr.exists() and dr.stat().st_size > 500:
        out.append("### Deep Research report: SF atmospheric-river preparedness (deep-research-preview-04-2026)\n" + dr.read_text()[:max(doc_chars * 2, 20000)])
    return "\n\n".join(out)


def build_pack(storm: str, base_dir, level: str = "full", model: str | None = None) -> tuple[str, dict]:
    lim = LIMITS.get(level, LIMITS["full"])
    base_dir = Path(base_dir)
    base = runner.load_result(base_dir)
    key = hashlib.sha256(f"{storm}|{base_dir.name}|{level}|{json.dumps(lim, sort_keys=True)}|v4|{sorted(p.name for p in (CACHE/'sf_plans').glob('*.md'))}|"
                         f"{(CACHE/'deep_research_sf_ar.md').exists()}".encode()).hexdigest()[:12]
    cp = CACHE / f"pack_{key}.md"
    mp = CACHE / f"pack_{key}.json"
    if cp.exists() and mp.exists():
        meta = json.loads(mp.read_text())
        meta["model"] = model
        return cp.read_text(), meta
    md = max_depth(str(base_dir))
    bs = basins(str(base_dir))
    secs = [("storm", sec_storm(storm)), ("ledger", sec_ledger(base)), ("basins", sec_basins(base_dir)),
            ("buildings", sec_buildings(base, md, lim["buildings"])), ("shelters", sec_shelters(base, md)),
            ("inlets", sec_inlets(md, bs, lim["inlets"])), ("streets", sec_streets(md, lim["streets"])),
            ("trees", sec_trees(base_dir, lim["trees"] if lim["trees"] is not True else "all")),
            ("people", sec_people()), ("reference", sec_reference(lim["doc_chars"])), ("documents", sec_docs(lim["doc_chars"]))]
    text = "\n\n".join(s for _, s in secs if s)
    meta = {"level": level, "key": key, "chars": len(text), "est_tokens": int(len(text) / 1.4),
            "sections": {n: len(s) for n, s in secs}, "baseline_run_id": base_dir.name, "storm": storm,
            "sha": hashlib.sha256(text.encode()).hexdigest()[:16]}
    meta["summary"] = f"{level}: {meta['chars']:,} chars (~{meta['est_tokens']:,} tokens), sha {meta['sha']}"
    cp.write_text(text)
    mp.write_text(json.dumps(meta, indent=1))
    meta["model"] = model
    return text, meta


if __name__ == "__main__":
    import argparse
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", default="elnino_ar_2026")
    ap.add_argument("--level", default="full")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()
    t = time.time()
    bd = Path(a.base) if a.base else runner.baseline(a.storm)[0]
    txt, meta = build_pack(a.storm, bd, a.level)
    print(json.dumps(meta, indent=1), f"{time.time()-t:.1f}s")
