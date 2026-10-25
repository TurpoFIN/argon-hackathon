"""Above-ground asset inventory for the destruction engine (sim/destruction.py).

build_assets() -> dict, written to data/grid/assets.json:
  {power_poles, power_lines, power_spans, ocs_spans, streetlights, signals, shelters, masts, sources}

Every asset is a real mapped object, archived 25.10.2026 in data/reference/assets/:
- osm_above_ground_20261025.json: OpenStreetMap (Overpass API, (c) OpenStreetMap contributors, ODbL) for the
  tile bbox: power=pole|tower, power=line|minor_line, highway=street_lamp, trolley_wire=yes streets,
  railway=tram|light_rail (electrified=contact_line), bus stops with shelter=yes, amenity=shelter, man_made=mast|tower.
- datasf_traffic_signals_ybh5-27n2_20261025.json: SFMTA Traffic Signals (DataSF ybh5-27n2), official inventory.
OSM coverage is partial (mapped objects only); the counts are a floor, not the full city stock.

Coordinates: UTM 10N metres (EPSG:32610) like buildings.json/trees.json, plus grid cell (ix, iy).
"""
from __future__ import annotations

import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "data", "reference", "assets")
OSM_FILE = "osm_above_ground_20261025.json"
SIGNALS_FILE = "datasf_traffic_signals_ybh5-27n2_20261025.json"
OCS_MAX_SPAN_M = 30.0   # trolley wire is hung from span wires every ~30 m (see destruction_params ocs_span_m)


def lonlat_to_utm10(lon, lat):
    """WGS84 -> UTM zone 10N (EPSG:32610), Snyder 1987 (USGS PP 1395) series; ~mm accuracy inside the zone."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi, lam, lam0 = math.radians(lat), math.radians(lon), math.radians(-123.0)
    s, c = math.sin(phi), math.cos(phi)
    N = a / math.sqrt(1 - e2 * s * s)
    T = math.tan(phi) ** 2
    C = ep2 * c * c
    A = c * (lam - lam0)
    e4, e6 = e2 * e2, e2 * e2 * e2
    M = a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
             - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
             + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
             - (35 * e6 / 3072) * math.sin(6 * phi))
    x = k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5 / 120) + 500000.0
    y = k0 * (M + N * math.tan(phi) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24
                                       + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6 / 720))
    return x, y


def utm10_to_lonlat(x, y):
    """UTM zone 10N (EPSG:32610) -> WGS84, Snyder 1987 inverse series (inverse of lonlat_to_utm10)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    M = y / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c = math.sin(p1), math.cos(p1)
    C1 = ep2 * c * c
    T1 = math.tan(p1) ** 2
    N1 = a / math.sqrt(1 - e2 * s * s)
    R1 = a * (1 - e2) / (1 - e2 * s * s) ** 1.5
    D = (x - 500000.0) / (N1 * k0)
    lat = p1 - (N1 * math.tan(p1) / R1) * (D * D / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * ep2) * D ** 4 / 24
                                           + (61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * ep2 - 3 * C1 * C1) * D ** 6 / 720)
    lon = (D - (1 + 2 * T1 + C1) * D ** 3 / 6 + (5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * ep2 + 24 * T1 * T1) * D ** 5 / 120) / c
    return math.degrees(lon) - 123.0, math.degrees(lat)


def _meta():
    return json.load(open(os.path.join(ROOT, "data", "grid", "meta.json")))


def _inside(meta, x, y):
    return meta["x0"] <= x < meta["x0"] + meta["nx"] * meta["dx"] and meta["y0"] <= y < meta["y0"] + meta["ny"] * meta["dx"]


def _cell(meta, x, y):
    return int((x - meta["x0"]) // meta["dx"]), int((y - meta["y0"]) // meta["dx"])


def _pt(meta, kind, i, x, y, **kw):
    ix, iy = _cell(meta, x, y)
    return dict(id=i, kind=kind, x=round(x, 2), y=round(y, 2), ix=ix, iy=iy, **kw)


def _split(p, q, max_len):
    """Split segment p->q into n equal spans no longer than max_len."""
    L = math.hypot(q[0] - p[0], q[1] - p[1])
    n = max(1, int(math.ceil(L / max_len)))
    return [((p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n),
             (p[0] + (q[0] - p[0]) * (k + 1) / n, p[1] + (q[1] - p[1]) * (k + 1) / n)) for k in range(n)]


def build_assets(write=True):
    meta = _meta()
    osm = json.load(open(os.path.join(SRC_DIR, OSM_FILE)))
    nodes = {}
    for e in osm["elements"]:   # "out body; >; out skel" repeats tagged nodes without tags: keep the tagged copy
        if e["type"] == "node" and (e.get("tags") or e["id"] not in nodes):
            nodes[e["id"]] = e
    utm = {nid: lonlat_to_utm10(n["lon"], n["lat"]) for nid, n in nodes.items()}
    tagged = [e for e in osm["elements"] if e.get("tags") and (e["type"] != "node" or nodes[e["id"]] is e)]

    # ---------------- power: poles, lines (OSM ways), spans between consecutive line nodes
    pole_nodes = sorted(e["id"] for e in tagged if e["type"] == "node" and e["tags"].get("power") in ("pole", "tower"))
    pole_idx = {}
    poles = []
    for nid in pole_nodes:
        x, y = utm[nid]
        if not _inside(meta, x, y):
            continue
        t = nodes[nid]["tags"]
        pole_idx[nid] = len(poles)
        poles.append(_pt(meta, "power_pole", len(poles), x, y, osm_id=nid, material=t.get("material", "wood"),
                         operator=t.get("operator:short") or t.get("operator") or "", tower=t.get("power") == "tower",
                         spans=[]))
    lines, spans = [], []
    for w in sorted((e for e in tagged if e["type"] == "way" and e["tags"].get("power") in ("line", "minor_line")),
                    key=lambda e: e["id"]):
        if w["tags"].get("layer", "0").startswith("-"):
            continue   # mapped as underground
        lid = len(lines)
        span_ids = []
        for a, b in zip(w["nodes"][:-1], w["nodes"][1:]):
            if a not in utm or b not in utm:
                continue
            pa, pb = utm[a], utm[b]
            if not (_inside(meta, *pa) or _inside(meta, *pb)):
                continue
            sid = len(spans)
            ia, ib = pole_idx.get(a), pole_idx.get(b)
            spans.append(dict(id=sid, kind="power_span", line_id=lid, a=[round(pa[0], 2), round(pa[1], 2)],
                              b=[round(pb[0], 2), round(pb[1], 2)], pole_a=ia, pole_b=ib,
                              length_m=round(math.hypot(pb[0] - pa[0], pb[1] - pa[1]), 2)))
            for ip in (ia, ib):
                if ip is not None:
                    poles[ip]["spans"].append(sid)
            span_ids.append(sid)
        if span_ids:
            lines.append(dict(id=lid, osm_id=w["id"], kind=w["tags"]["power"],
                              operator=w["tags"].get("operator:short") or w["tags"].get("operator") or "",
                              spans=span_ids))

    # ---------------- Muni overhead contact system: trolleybus wire streets + electrified tram/light rail
    ocs = []
    sections = {}
    for w in sorted((e for e in tagged if e["type"] == "way" and (
            e["tags"].get("trolley_wire") == "yes" or
            (e["tags"].get("railway") in ("tram", "light_rail") and e["tags"].get("electrified") == "contact_line"))),
            key=lambda e: e["id"]):
        t = w["tags"]
        mode = "trolleybus" if t.get("trolley_wire") == "yes" else t["railway"]
        name = t.get("name") or f"way {w['id']}"
        sec = sections.setdefault((mode, name), len(sections))
        for a, b in zip(w["nodes"][:-1], w["nodes"][1:]):
            if a not in utm or b not in utm:
                continue
            for p, q in _split(utm[a], utm[b], OCS_MAX_SPAN_M):
                if not (_inside(meta, *p) or _inside(meta, *q)):
                    continue
                ocs.append(dict(id=len(ocs), kind="ocs_span", mode=mode, street=name, section=sec, osm_id=w["id"],
                                a=[round(p[0], 2), round(p[1], 2)], b=[round(q[0], 2), round(q[1], 2)],
                                length_m=round(math.hypot(q[0] - p[0], q[1] - p[1]), 2)))

    # ---------------- streetlights, shelters, masts (OSM points)
    lights, shelters, masts = [], [], []
    for e in sorted((e for e in tagged if e["type"] == "node"), key=lambda e: e["id"]):
        t = e["tags"]
        x, y = utm[e["id"]]
        if not _inside(meta, x, y):
            continue
        if t.get("highway") == "street_lamp":
            mount = t.get("lamp_mount", "straight_mast")
            lights.append(_pt(meta, "streetlight", len(lights), x, y, osm_id=e["id"], mount=mount,
                              high_mast=mount == "high_mast"))
        elif (t.get("highway") == "bus_stop" and t.get("shelter") == "yes") or t.get("amenity") == "shelter":
            shelters.append(_pt(meta, "shelter", len(shelters), x, y, osm_id=e["id"], name=t.get("name", "")))
        elif t.get("man_made") in ("mast", "tower") and t.get("tower:type") != "bell_tower" and t.get("location") != "roof":
            masts.append(_pt(meta, "mast", len(masts), x, y, osm_id=e["id"], tower_type=t.get("tower:type", ""),
                             lattice=t.get("tower:construction") == "lattice"))

    # ---------------- traffic signals (SFMTA official inventory)
    signals = []
    for r in sorted(json.load(open(os.path.join(SRC_DIR, SIGNALS_FILE))), key=lambda r: int(r["objectid"])):
        if r.get("type") not in ("SIGNAL", "FLASHER", "BEACON") or not r.get("shape"):
            continue
        lon, lat = r["shape"]["coordinates"]
        x, y = lonlat_to_utm10(lon, lat)
        if _inside(meta, x, y):
            signals.append(_pt(meta, "signal", len(signals), x, y, sfmta_id=int(r["objectid"]), type=r["type"],
                               street1=r.get("street1", ""), street2=r.get("street2", "")))

    out = dict(power_poles=poles, power_lines=lines, power_spans=spans, ocs_spans=ocs,
               ocs_sections=[dict(id=v, mode=k[0], street=k[1]) for k, v in sorted(sections.items(), key=lambda kv: kv[1])],
               streetlights=lights, signals=signals, shelters=shelters, masts=masts,
               sources={
                   "osm": f"OpenStreetMap via Overpass API (overpass-api.de), bbox S37.758 W-122.425 N37.795 E-122.385, "
                          f"retrieved 2026-10-25; (c) OpenStreetMap contributors, ODbL. Archived data/reference/assets/{OSM_FILE}",
                   "signals": f"SFMTA Traffic Signals, DataSF ybh5-27n2 (https://data.sf.gov/resource/ybh5-27n2.json), retrieved "
                              f"2026-10-25. Archived data/reference/assets/{SIGNALS_FILE}",
                   "coverage": "Mapped objects only: OSM street lamps and poles are a floor on the real stock."})
    if write:
        with open(os.path.join(ROOT, "data", "grid", "assets.json"), "w") as fh:
            json.dump(out, fh, separators=(",", ":"))
    return out


if __name__ == "__main__":
    a = build_assets()
    print({k: len(v) for k, v in a.items() if isinstance(v, list)})
