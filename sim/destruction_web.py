"""Export a destruction run as a compact lon/lat scene file for the ArcGIS viewer (destruction-layer.js).

python3 -m sim.destruction_web  -> data/web/destruction_<scenario>.json for the demo scenarios.

Arrays are positional to keep the file small; times are storm seconds (null = never). Heights are metres above
ground; the viewer draws everything relative to the ground.
"""
from __future__ import annotations

import json
import os

import numpy as np

from sim.assets import utm10_to_lonlat
from sim.destruction import P_ROOT, run_destruction, scaled_storm, params

OUT = os.path.join(P_ROOT, "data", "web")
SCENARIOS = [("elnino_ar_2026", 1.0, "El Niño atmospheric river 2026 (as forecast)"),
             ("elnino_ar_2026", 1.6, "Stress test: El Niño gusts x1.6 (ARkStorm-class wind)"),
             ("dec31_2022", 1.0, "Replay: 31.12.2022 storm")]


# Esri realistic 3D tree models (EsriRealisticTreesStyle) by genus; genera without a model get the closest look-alike.
TREE_MODELS = {"Platanus", "Quercus", "Acer", "Ficus", "Eucalyptus", "Magnolia", "Pinus", "Prunus", "Tilia", "Ulmus", "Populus",
               "Sequoia", "Washingtonia", "Fraxinus", "Cupressus", "Betula", "Liquidambar", "Arbutus", "Laurus", "Juglans",
               "Robinia", "Citrus", "Phoenix", "Sabal", "Cocos", "Salix", "Ligustrum", "Cornus", "Crataegus"}
TREE_LOOKALIKE = {"Lophostemon": "Eucalyptus", "Corymbia": "Eucalyptus", "Melaleuca": "Eucalyptus", "Tristaniopsis": "Laurus",
                  "Metrosideros": "Laurus", "Callistemon": "Laurus", "Pittosporum": "Laurus", "Olea": "Laurus", "Lagunaria": "Laurus",
                  "Afrocarpus": "Laurus", "Cupaniopsis": "Laurus", "Pyrus": "Prunus", "Malus": "Prunus", "Ginkgo": "Liquidambar",
                  "Jacaranda": "Robinia", "Acacia": "Robinia", "Pistacia": "Fraxinus", "Koelreuteria": "Fraxinus",
                  "Schinus": "Salix", "Geijera": "Salix", "Maytenus": "Salix", "Ceratonia": "Quercus", "Eriobotrya": "Magnolia",
                  "Syagrus": "Cocos", "Juniperus": "Cupressus", "Lyonothamnus": "Arbutus"}


def tree_model(species):
    g = (species or "").split(" ")[0]
    return g if g in TREE_MODELS else TREE_LOOKALIKE.get(g, "Unknown")


def _ll(x, y):
    lon, lat = utm10_to_lonlat(float(x), float(y))
    return [round(lon, 6), round(lat, 6)]


def _flat(ring):
    out = []
    for x, y in ring:
        out.extend(_ll(x, y))
    return out


def export(storm, title, res=None):
    P = params()
    res = res or run_destruction(storm)
    grid = os.path.join(P_ROOT, "data", "grid")
    assets = json.load(open(os.path.join(grid, "assets.json")))
    bl = {int(b["id"]): b for b in json.load(open(os.path.join(grid, "buildings.json")))}
    trees = {int(t["id"]): t for t in json.load(open(os.path.join(grid, "trees.json")))}
    t = res["t"]
    tn = lambda v: None if v is None else round(float(v), 1)

    buildings = []
    for b in res["buildings"]:
        bb = bl.get(b["id"])
        if bb and bb.get("footprint"):
            buildings.append([b["id"], b["ds"], [tn(v) for v in b["ds_t"]], round(float(bb.get("height_m") or 6.0), 1), _flat(bb["footprint"])])
    out_t = dict((int(i), tt) for i, tt in res["outage"].get("buildings_out_t", []))
    outage = []
    for i, tt in sorted(out_t.items()):
        bb = bl.get(i)
        if bb and bb.get("footprint"):
            outage.append([i, tn(tt), round(float(bb.get("height_m") or 6.0), 1), _flat(bb["footprint"])])

    pole_fail = {p["id"]: p for p in res["power_poles"]}
    poles = [[*_ll(p["x"], p["y"]), tn(pole_fail[i]["t"]) if i in pole_fail else None,
              pole_fail[i]["fall_dir_deg"] if i in pole_fail else None] for i, p in enumerate(assets["power_poles"])]
    span_down = {s["id"]: s["t"] for s in res["power_spans"]}
    line_out = {L["id"]: L["t"] for L in res["power_lines_out"]}
    spans = [[*_ll(*s["a"]), *_ll(*s["b"]), tn(span_down.get(i)), tn(line_out.get(s["line_id"])),
              s["pole_a"] if s["pole_a"] is not None else -1, s["pole_b"] if s["pole_b"] is not None else -1]
             for i, s in enumerate(assets["power_spans"])]
    ocs_down = {s["id"]: s["t"] for s in res["ocs_spans"]}
    ocs_out = {s["id"]: s["t"] for s in res["ocs_out"]}
    ocs = [[*_ll(*s["a"]), *_ll(*s["b"]), tn(ocs_down.get(i)), tn(ocs_out.get(i)), s["section"]] for i, s in enumerate(assets["ocs_spans"])]
    sl = P["streetlight"]
    ls = {s["id"]: s for s in res["streetlights"]}
    lights = []
    for i, a in enumerate(assets["streetlights"]):
        s = ls.get(i, {})
        k = s.get("t")
        ev = [e for e in res["events"] if e["kind"] == "streetlight" and e["id"] == i] if k is not None else []
        lights.append([*_ll(a["x"], a["y"]), sl["high_mast_height_m"] if a.get("high_mast") else sl["height_m"], tn(k),
                       ev[0].get("fall_dir_deg") if ev else None, tn(s.get("dark_t")), bool(s.get("flooded", False))])
    ss = {s["id"]: s for s in res["signals"]}
    signals = []
    for i, a in enumerate(assets["signals"]):
        s = ss.get(i, {})
        signals.append([*_ll(a["x"], a["y"]), tn(s.get("head_t")), tn(s.get("struct_t")), tn(s.get("dark_t")), bool(s.get("flooded", False))])
    sh = {s["id"]: s["t"] for s in res["shelters"]}
    shelters = [[*_ll(a["x"], a["y"]), tn(sh.get(i))] for i, a in enumerate(assets["shelters"])]
    tr = []
    for f in res["trees"]:
        tt = trees.get(f["id"])
        if tt:
            tr.append([*_ll(tt["x"], tt["y"]), round(float(tt.get("height_m") or 6.0), 1), round(float(tt.get("crown_m") or 3.0), 1),
                       round(float(tt.get("dbh_cm") or 15.0), 1), tn(f["fail_t"]), f["fall_dir_deg"], f["mode"],
                       tree_model(tt.get("species")), tt.get("common") or tt.get("species") or "", int(f["id"])])
    deb = res["debris"]
    hits = [d for d in deb if d["hit"] >= 0]
    rest = [d for d in deb if d["hit"] < 0]
    keep = hits[:1500] + rest[: max(0, 1500 - len(hits))]
    debris = [[*_ll(d["x0"], d["y0"]), d["z0"], *_ll(d["x1"], d["y1"]), d["z1"], tn(d["t0"]), round(d["t0"] + d["flight_s"], 1), d["hit"]]
              for d in keep]
    S = res["summary"]
    return {
        "scenario": storm.name, "title": title, "t": [round(v, 1) for v in t],
        "gust": [round(float(v), 2) for v in storm.gust_ms], "dir_from": [round(float(v), 1) for v in np.asarray(storm.wind_dir_t)],
        "summary": S, "ledger_total_usd": res["ledger"]["total_usd"], "by_cause": res["ledger"]["by_cause"],
        "customers_t": res["outage"]["customers_t"], "timeline": res["timeline"], "wind_source": res["wind_source"],
        "heights": {"power_wire": P["power_span"]["wire_height_m"], "pole": P["power_pole"]["height_m"],
                    "ocs_wire": P["ocs"]["wire_height_m"], "signal": P["signal"]["height_m"]},
        "buildings": buildings, "outage": outage, "poles": poles, "spans": spans, "ocs": ocs, "lights": lights,
        "signals": signals, "shelters": shelters, "trees": tr, "debris": debris,
        "notes": res["notes"],
    }


def export_street_trees():
    """Every inventory street tree (standing city), in trees.json id order: [lon, lat, height_m, crown_m, model index]."""
    trees = json.load(open(os.path.join(P_ROOT, "data", "grid", "trees.json")))
    models = sorted({tree_model(t.get("species")) for t in trees})
    mi = {m: i for i, m in enumerate(models)}
    rows = [[*_ll(t["x"], t["y"]), round(float(t.get("height_m") or 6.0), 1), round(float(t.get("crown_m") or 3.0), 1),
             mi[tree_model(t.get("species"))]] for t in sorted(trees, key=lambda t: int(t["id"]))]
    with open(os.path.join(OUT, "destruction_street_trees.json"), "w") as fh:
        json.dump({"models": models, "ids": "row index = trees.json id", "trees": rows,
                   "source": "DataSF Street Tree Inventory tkzw-k3nq via data/grid/trees.json"}, fh, separators=(",", ":"))


def main():
    from sim.storm import load_storm
    os.makedirs(OUT, exist_ok=True)
    export_street_trees()
    index = []
    for name, scale, title in SCENARIOS:
        st = load_storm(name)
        if scale != 1.0:
            st = scaled_storm(st, scale)
        d = export(st, title)
        p = os.path.join(OUT, f"destruction_{st.name}.json")
        with open(p, "w") as fh:
            json.dump(d, fh, separators=(",", ":"))
        index.append({"id": st.name, "title": title, "file": f"/data/web/destruction_{st.name}.json",
                      "ledger_total_usd": d["ledger_total_usd"]})
        print(p, round(os.path.getsize(p) / 1e6, 2), "MB", d["summary"]["trees_failed"], "trees")
    with open(os.path.join(OUT, "destruction_index.json"), "w") as fh:
        json.dump(index, fh, indent=1)


if __name__ == "__main__":
    main()
