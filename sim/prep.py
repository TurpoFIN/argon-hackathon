"""StormSim SF PREP: data/raw -> data/grid (+ web/public/data). See CONTRACT.md.

Run:  .venv/bin/python sim/prep.py [--no-preview]
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject
from pyproj import Transformer
import shapely
from shapely.geometry import shape, Point, LineString, mapping
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "grid"
WEB = ROOT / "web" / "public" / "data"
CRS = "EPSG:32610"
DX = 4.0
RNG = np.random.default_rng(20221231)
TO_UTM = Transformer.from_crs(4326, 32610, always_xy=True)
T0 = time.time()


def log(*a):
    print(f"[prep {time.time() - T0:6.1f}s]", *a, flush=True)


def load_features(name):
    g = json.load(open(RAW / name))
    return g["features"] if isinstance(g, dict) else g


def to_utm_geom(geom):
    g = shape(geom)
    return shapely.transform(g, lambda c: np.column_stack(TO_UTM.transform(c[:, 0], c[:, 1])))


def fnum(v, default=None):
    try:
        if v is None or v == "":
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------------------------
# Cited constants
# ----------------------------------------------------------------------------------------------
SOURCES = {
    "dem": "USGS 3D Elevation Program (3DEP) bare-earth DEM, elevation.nationalmap.gov ImageServer, NAVD88 m; "
           "bay surface is hydro-flattened at -1.07 m in the source.",
    "buildings": "DataSF Building Footprints (ynuv-fyni), 2010 lidar heights (hgt_median_m, gnd_min_m).",
    "assessor": "SF Assessor-Recorder Secured Property Tax Roll 2023 (DataSF wv5m-vpq2).",
    "hazus_occ": "FEMA Hazus 6.1 Inventory Technical Manual (2024), Table 3-2 occupancy classes.",
    "hazus_contents": "FEMA Hazus 6.1 Flood Technical Manual (2024), contents value as % of structure "
                      "replacement value: RES 50%, COM 100% (COM6/COM7 150%, COM10 50%), IND 150%, "
                      "AGR/REL/GOV1/EDU1 100%, GOV2/EDU2 150%.",
    "repl_cost": "Replacement (hard) cost per m2 of gross floor area, San Francisco: Rider Levett Bucknall "
                 "Quarterly Construction Cost Report Q3 2025 (rlb.com/wp-content/uploads/sites/4/2025/09/"
                 "Q3-2025-QCR.pdf): prime office $460-800/sf, secondary office $360-580/sf, 3-star hotel "
                 "$430-660/sf; RLB Q3 2024 via The Real Deal 11.10.2024: multifamily $400-625/sf, single-family "
                 "$330-550/sf. Midpoints used (1 sf = 0.0929 m2). Retail/industrial/civic: estimates scaled from "
                 "these. Method as Hazus 6.1 (cost/area x floor area).",
    "inlets": "SFPUC Stormwater inlets (DataSF jtgq-b7c5). Capacity per FHWA HEC-22 (Urban Drainage Design "
              "Manual, 3rd ed., FHWA-NHI-10-009) grate/curb inlet interception for a ~0.6x0.9 m SF catch-basin "
              "grate at ~5-10 cm ponding: 0.03-0.06 m3/s.",
    "trees": "DataSF Street Tree Inventory (tkzw-k3nq). Height/crown allometry: form after McPherson, van "
             "Doorn & Peper 2016, Urban Tree Database (USFS GTR PSW-253), simplified per form group. Green "
             "modulus of rupture: USDA FPL Wood Handbook (FPL-GTR-282, 2021) Table 5-3a/5-4a where the genus "
             "is listed; others default 40 MPa.",
    "streets": "DataSF Streets active and retired (3psu-pn9h) centerlines; SF Better Streets Plan typical "
               "roadway widths (residential ~12 m curb-curb, arterial ~16-20 m).",
    "parks": "SF Recreation and Parks Properties (DataSF gtr9-ntp6).",
    "flood100": "SFPUC 100-Year Storm Flood Risk Map (DataSF jzu3-4yxp).",
    "validation": "SF 311 Cases (DataSF vw6y-z8j6), 30.12.2022-01.01.2023: Sewer Issues (Flooding, "
                  "Sewage_back_up) and Tree Maintenance.",
    "cars": "Synthetic parked cars along curbs of DataSF street centerlines (~6.5 m stall). Value: Kelley Blue "
            "Book / Cox Automotive average used-vehicle listing price ~US$25k (2024-25).",
    "soil": "Infiltration ksat: Rawls, Brakensiek & Miller 1983 (Green-Ampt parameters; loamy sand ~30 mm/h, "
            "sandy loam ~11 mm/h), reduced for urban compaction; Manning n: Chow 1959 / HEC-RAS 2D manual.",
}

# $/m2 of floor area (2025 USD, San Francisco)
REPL_COST_M2 = {
    "RES1": 4750, "RES2": 2500, "RES3": 5500, "RES4": 6000, "RES5": 5500, "RES6": 6000,
    "COM1": 4000, "COM2": 2500, "COM3": 3800, "COM4": 6000, "COM5": 6000, "COM6": 10000,
    "COM7": 6500, "COM8": 5000, "COM9": 5000, "COM10": 2200,  # COM4: prime/secondary blend
    "IND1": 3000, "IND2": 2600, "IND3": 3500, "IND4": 3000, "IND5": 4500, "IND6": 2500,
    "AGR1": 1500, "REL1": 4500, "GOV1": 6000, "GOV2": 6500, "EDU1": 6000, "EDU2": 7000,
}
CONTENTS_RATIO = {k: 0.5 for k in REPL_COST_M2 if k.startswith("RES")}
CONTENTS_RATIO.update({k: 1.0 for k in REPL_COST_M2 if k.startswith("COM")})
CONTENTS_RATIO.update({"COM6": 1.5, "COM7": 1.5, "COM10": 0.5})
CONTENTS_RATIO.update({k: 1.5 for k in REPL_COST_M2 if k.startswith("IND")})
CONTENTS_RATIO.update({"AGR1": 1.0, "REL1": 1.0, "GOV1": 1.0, "GOV2": 1.5, "EDU1": 1.0, "EDU2": 1.5})

INLET_CAP = {  # m3/s
    "Catch Basin Drain": 0.05, "Storm Water Inlet Drain": 0.04, "Drop Inlet": 0.08,
    "Area Drain, used in Mission Bay": 0.02, "Track Drain": 0.02, "Trench Drain": 0.04,
    "Curb Inlet: Drain w/ no grate": 0.04, "Caltrans G1 Grate Inlet": 0.06, "Caltrans G2 Grate Inlet": 0.08,
    "MH Inlet: Grated Cover": 0.05,
}
INLET_CAP_DEFAULT = 0.02

# Tree species: (form group, green MOR MPa or None, source genus)
# Wood Handbook green MOR (Table 5-3a, kPa->MPa): sycamore 45, elm(American) 50, red maple 53, sugar maple 65,
# southern magnolia 47, sweetgum 49, white ash 66, black cherry 55, black locust 95, honeylocust 70,
# red oak 57, live oak 82, black walnut 66, Pacific madrone 52, red alder 45, eastern cottonwood 37,
# black willow 33, basswood 34, hackberry 45, coast redwood 52 (young-growth 41), Douglas-fir 53,
# Monterey/ponderosa pine 35, Oregon ash 52, bigleaf maple 51, ginkgo n/a.
GENUS = {
    "platanus": ("large", 45), "ulmus": ("large", 50), "zelkova": ("large", 50), "acer": ("medium", 53),
    "magnolia": ("medium", 47), "liquidambar": ("large", 49), "fraxinus": ("medium", 66),
    "prunus": ("small", 55), "robinia": ("medium", 95), "gleditsia": ("medium", 70), "quercus": ("large", 57),
    "juglans": ("large", 66), "arbutus": ("small", 52), "alnus": ("medium", 45), "populus": ("large", 37),
    "salix": ("medium", 33), "tilia": ("medium", 34), "celtis": ("medium", 45), "sequoia": ("conifer", 41),
    "pseudotsuga": ("conifer", 53), "pinus": ("conifer", 35), "cupressus": ("conifer", 40),
    "hesperocyparis": ("conifer", 40), "eucalyptus": ("large", 70), "corymbia": ("medium", 70),
    "lophostemon": ("medium", 70), "melaleuca": ("medium", 50), "metrosideros": ("medium", 45),
    "ficus": ("medium", 30), "pyrus": ("small", 50), "malus": ("small", 50), "ginkgo": ("medium", 40),
    "tristaniopsis": ("small", 55), "pittosporum": ("small", 40), "olea": ("small", 60),
    "ligustrum": ("small", 40), "jacaranda": ("medium", 40), "callistemon": ("small", 45),
    "koelreuteria": ("small", 40), "laurus": ("small", 40), "eriobotrya": ("small", 40),
    "maytenus": ("small", 40), "afrocarpus": ("medium", 40), "podocarpus": ("medium", 40),
    "pistacia": ("medium", 45), "geijera": ("small", 40), "schinus": ("medium", 40), "acacia": ("medium", 60),
    "ceratonia": ("small", 50), "lagunaria": ("small", 40), "cercis": ("small", 40), "betula": ("medium", 44),
    "washingtonia": ("palm", None), "syagrus": ("palm", None), "phoenix": ("palm", None),
    "trachycarpus": ("palm", None), "archontophoenix": ("palm", None),
}
# form group: (Hmax m, k for H = 1.37 + (Hmax-1.37)(1-exp(-k dbh_cm)), crown a, crown b: crown = a*dbh^b)
FORM = {
    "large": (24.0, 0.030, 0.42, 0.80), "medium": (16.0, 0.035, 0.40, 0.78),
    "small": (10.0, 0.045, 0.38, 0.78), "conifer": (28.0, 0.025, 0.30, 0.75),
    "palm": (20.0, 0.020, 0.0, 0.0),
}
MOR_DEFAULT = 40.0

# Post-2010 towers missing from the 2010 lidar heights: (lon, lat, roof/top height m, name); CTBUH heights.
_KT = [(-122.39675, 37.78977, 326.0, "Salesforce Tower"), (-122.39553, 37.78977, 245.0, "181 Fremont"),
       (-122.39560, 37.78952, 184.0, "Park Tower at Transbay"), (-122.39890, 37.78880, 116.0, "535 Mission"),
       (-122.39760, 37.78830, 120.0, "Mira"), (-122.39440, 37.78880, 188.0, "The Avery / Transbay Blk 8")]
KNOWN_TOWERS = [(*TO_UTM.transform(lo, la), hh, nm) for lo, la, hh, nm in _KT]


def hazus_class(p):
    """Map an assessor-roll parcel point to a Hazus occupancy class."""
    uc = (p.get("use_code") or "").upper()
    d = (p.get("property_class_code_definition") or "").lower()
    if "church" in d or "convent" in d:
        return "REL1"
    if "school" in d:
        return "EDU1"
    if "hospital" in d:
        return "COM6"
    if "medical" in d or "dental" in d:
        return "COM7"
    if "bank" in d:
        return "COM5"
    if "theatre" in d or "entertainment" in d or "club" in d or "lodge" in d or "recreation" in d:
        return "COM8"
    if "garage" in d or "parking" in d:
        return "COM10"
    if "gas station" in d or "service station" in d or "auto" in d:
        return "COM3"
    if "warehouse" in d:
        return "COM2"
    if "hotel" in d or "motel" in d or "timeshare" in d or "time share" in d or "sro" in d:
        return "RES4"
    if uc == "GOVT" or " gov" in d or d.endswith("gov"):
        return "GOV1"
    if "dwelling" in d and "apt" not in d and "2 dwellings" not in d:
        return "RES1"
    if "town house" in d:
        return "RES1"
    if uc == "SRES":
        return "RES3" if any(k in d for k in ("condo", "flat", "coop", "tic", "duplex", "apartment")) else "RES1"
    if uc == "MRES":
        return "RES3"
    if "store" in d and ("flat" in d or "apartment" in d):
        return "RES3"  # residential over ground-floor retail
    if uc == "COMR" or "store" in d or "retail" in d or "shopping" in d:
        return "COM1"
    if uc == "COMO" or "office" in d:
        return "COM4"
    if uc == "COMH":
        return "RES4"
    if uc == "IND":
        return "IND2"
    if "live/work" in d:
        return "RES3"
    if uc in ("COMM", "MISC"):
        return "COM1"
    return "RES3"


def ground_retail(p):
    d = (p.get("property_class_code_definition") or "").lower()
    uc = (p.get("use_code") or "").upper()
    return uc == "COMR" or "store" in d or "retail" in d or "commercial/mixed" in d


# ----------------------------------------------------------------------------------------------
def main(preview=True):
    OUT.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)

    # ---------------- grid + DEM ----------------
    src = rasterio.open(RAW / "dem_3dep.tif")
    if src.crs is None:
        raise SystemExit("DEM has no CRS; expected EPSG:32610")
    b = src.bounds
    x0 = math.ceil(b.left / DX) * DX
    y0 = math.ceil(b.bottom / DX) * DX
    nx = int((b.right - x0) // DX)
    ny = int((b.top - y0) // DX)
    xmax, ymax = x0 + nx * DX, y0 + ny * DX
    tr_n = from_origin(x0, ymax, DX, DX)  # north-up transform for rasterize/reproject
    log(f"grid nx={nx} ny={ny} x0={x0} y0={y0} ({nx*DX:.0f} x {ny*DX:.0f} m)")

    dem_src = src.read(1).astype(np.float32)
    zn = np.full((ny, nx), np.nan, np.float32)
    reproject(dem_src, zn, src_transform=src.transform, src_crs=src.crs, dst_transform=tr_n,
              dst_crs=CRS, resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan)
    if np.isnan(zn).any():
        zn = np.where(np.isnan(zn), np.nanmin(zn), zn)

    def flip(a):  # north-up raster -> south-first
        return np.ascontiguousarray(a[::-1])

    z_raw = flip(zn)  # [iy, ix], iy=0 south
    xc = x0 + (np.arange(nx) + 0.5) * DX
    yc = y0 + (np.arange(ny) + 0.5) * DX

    def in_grid(x, y):
        return (x >= x0) & (x < xmax) & (y >= y0) & (y < ymax)

    def to_ix(x, y):
        return (np.floor((np.asarray(x) - x0) / DX).astype(int), np.floor((np.asarray(y) - y0) / DX).astype(int))

    def rast(shapes, fill=0, dtype=np.uint8, all_touched=False):
        shapes = list(shapes)
        if not shapes:
            return np.full((ny, nx), fill, dtype)
        r = rasterize(shapes, out_shape=(ny, nx), transform=tr_n, fill=fill, dtype=dtype, all_touched=all_touched)
        return flip(r)

    # Bay: hydro-flattened water surface (-1.07) and anything below 0 m connected to the grid edge.
    low = z_raw < 0.0
    lab, nlab = ndimage.label(low)
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    bay = np.isin(lab, edge[edge > 0])
    bay = ndimage.binary_opening(bay, iterations=1) | (bay & (z_raw < -0.9))
    log(f"bay cells {bay.sum()} ({bay.mean()*100:.1f}%)")

    # ---------------- buildings ----------------
    feats = load_features("buildings.geojson")
    bgeoms, bprops = [], []
    for f in feats:
        g = to_utm_geom(f["geometry"])
        if g.is_empty:
            continue
        if not g.is_valid:
            g = g.buffer(0)
        c = g.centroid
        if not in_grid(c.x, c.y):
            continue
        bgeoms.append(g)
        bprops.append(f["properties"])
    nb = len(bgeoms)
    log(f"buildings in grid: {nb}")

    # assessor join: parcel point -> containing building, else nearest within 25 m
    afeats = load_features("assessor_roll.geojson")
    apts, aprops = [], []
    for f in afeats:
        if not f.get("geometry"):
            continue
        x, y = TO_UTM.transform(*f["geometry"]["coordinates"][:2])
        if x0 - 50 <= x <= xmax + 50 and y0 - 50 <= y <= ymax + 50:
            apts.append((x, y))
            aprops.append(f["properties"])
    apts = np.array(apts)
    tree = shapely.STRtree(bgeoms)
    pts = shapely.points(apts)
    pi, bi = tree.query(pts, predicate="within")
    assign = np.full(len(pts), -1)
    assign[pi] = bi
    miss = np.where(assign < 0)[0]
    if len(miss):
        idx, dist = tree.query_nearest(pts[miss], max_distance=25.0, return_distance=True, all_matches=False)
        assign[miss[idx[0]]] = idx[1]
    per_b = defaultdict(list)
    for k, bidx in enumerate(assign):
        if bidx >= 0:
            per_b[int(bidx)].append(k)
    log(f"assessor points {len(apts)}, joined {int((assign >= 0).sum())}, buildings with parcels {len(per_b)}")
    a_tree = shapely.STRtree(pts)

    buildings = []
    bshapes = []
    order = []
    for i, (g, p) in enumerate(zip(bgeoms, bprops)):
        area = float(g.area)
        h = fnum(p.get("hgt_median_m"), None)
        hmax = fnum(p.get("hgt_maxcm"), None)
        hmax = hmax / 100 if hmax is not None else None
        if h is None or h < 2.5:
            h = max(hmax or 0, 3.0) if (hmax or 0) < 6 else max(3.0, 0.8 * hmax)
        h = float(min(max(h, 2.5), 330.0))
        gz = fnum(p.get("gnd_min_m"), None)
        ks = per_b.get(i, [])
        if not ks:  # nearest parcel within 60 m for occupancy only
            idx = a_tree.query_nearest(g.centroid, max_distance=60.0)
            ks_occ = [int(idx[0])] if len(idx) else []
        else:
            ks_occ = ks
        occ_w = Counter()
        retail = False
        stories = []
        yrs = []
        assessed = 0.0
        prop_area = 0.0
        use_codes = Counter()
        for k in ks_occ:
            ap = aprops[k]
            w = max(fnum(ap.get("property_area"), 0.0) or 0.0, 1.0)
            occ_w[hazus_class(ap)] += w
            use_codes[ap.get("use_code")] += 1
            retail |= ground_retail(ap)
        for k in ks:
            ap = aprops[k]
            s = fnum(ap.get("number_of_stories"), 0) or 0
            if s > 0:
                stories.append(s)
            yv = fnum(ap.get("year_property_built"), 0) or 0
            if 1840 < yv < 2030:
                yrs.append(yv)
            assessed += (fnum(ap.get("assessed_improvement_value"), 0) or 0) + (fnum(ap.get("assessed_fixtures_value"), 0) or 0)
            prop_area += fnum(ap.get("property_area"), 0) or 0
        occ = occ_w.most_common(1)[0][0] if occ_w else ("RES3" if h < 25 else "COM4")
        fh = 3.1 if occ.startswith("RES") else 3.9  # storey height
        fl_s = max(stories) if stories else 0
        h_src = "lidar2010"
        # 2010 lidar predates many towers (Transbay, Mission Bay): trust assessor stories when much taller
        plaus = 3 <= fl_s <= 65 and (fl_s <= 12 or area >= 800)  # roll has junk values (e.g. 900 stories)
        if not plaus:
            fl_s = 0
        if plaus and fl_s * fh > 2.0 * h:
            h, h_src = float(fl_s * fh), "assessor_stories"
        for (kx, ky, kh, kname) in KNOWN_TOWERS:
            if g.contains(Point(kx, ky)):
                h, h_src = kh, f"known:{kname}"
        if h_src != "lidar2010":
            hmax = max(hmax or 0, h)
        fl_h = max(1, round(h / fh))
        floors = int(fl_s) if fl_s and 0.7 <= fl_s * fh / h <= 1.5 else int(fl_h)
        floor_area = area * floors
        vs = floor_area * REPL_COST_M2.get(occ, 5000)
        vc = vs * CONTENTS_RATIO.get(occ, 1.0)
        ffm = 0.0 if (occ.startswith(("COM", "IND")) or retail) else 0.3
        cent = g.representative_point()
        poly = max(g.geoms, key=lambda q: q.area) if g.geom_type == "MultiPolygon" else g
        simp = poly.simplify(0.5, preserve_topology=True)
        fp = [[round(x, 2), round(y, 2)] for x, y in np.asarray(simp.exterior.coords)[:-1]]
        buildings.append({
            "id": i, "x": round(cent.x, 2), "y": round(cent.y, 2), "area_m2": round(area, 1),
            "height_m": round(h, 2), "height_max_m": round(hmax, 2) if hmax else round(h, 2),
            "floors": floors, "height_source": h_src, "ground_z": None if gz is None else round(gz, 2),
            "occupancy": occ, "use_code": use_codes.most_common(1)[0][0] if use_codes else None,
            "year_built": int(np.median(yrs)) if yrs else None,
            "value_structure": round(vs), "value_contents": round(vc), "first_floor_m": ffm,
            "floor_area_m2": round(floor_area, 1), "assessed_improvement_usd": round(assessed),
            "assessor_area_sqft": round(prop_area), "n_parcels": len(ks), "ground_retail": bool(retail),
            "footprint": fp,
        })
        bshapes.append(g)
        order.append(h)
    # rasterize: taller buildings drawn last (win shared cells)
    srt = np.argsort(order)
    bldg_id = rast(((bshapes[i], int(i)) for i in srt), fill=-1, dtype=np.int32)
    hh = np.array([bb["height_m"] for bb in buildings], np.float32)
    bldg_h = np.where(bldg_id >= 0, hh[np.clip(bldg_id, 0, None)], 0).astype(np.float32)
    # tiny buildings that got no cell: stamp their centroid cell
    have = np.zeros(nb, bool)
    have[np.unique(bldg_id[bldg_id >= 0])] = True
    for i in np.where(~have)[0]:
        ix, iy = to_ix(buildings[i]["x"], buildings[i]["y"])
        if 0 <= ix < nx and 0 <= iy < ny and bldg_id[iy, ix] < 0:
            bldg_id[iy, ix] = i
            bldg_h[iy, ix] = hh[i]

    # ground z per building (fallback DEM), and bare-earth z: DEM is a 3DEP DTM already; clamp any
    # roof leakage inside footprints to the footprint's lidar ground max.
    z = z_raw.copy()
    inside = bldg_id >= 0
    gmax = np.array([(fnum(p.get("gnd_maxcm"), np.nan) or np.nan) / 100.0 for p in bprops], np.float32)
    gmed = np.array([(fnum(p.get("gnd_mediancm"), np.nan) or np.nan) / 100.0 for p in bprops], np.float32)
    bid = np.clip(bldg_id, 0, None)
    lim = np.where(inside, gmax[bid] + 0.5, np.inf)
    lim = np.where(np.isnan(lim), np.inf, lim)
    nclamp = int((z > lim).sum())
    z = np.minimum(z, lim)
    # buildings over the bay (piers): ground = lidar median ground of footprint
    pier = inside & bay
    z = np.where(pier & ~np.isnan(gmed[bid]), np.maximum(z, gmed[bid]), z)
    bay = bay & ~inside
    z = np.where(bay, -2.0, z).astype(np.float32)
    zb_med = ndimage.median(z, labels=np.where(inside, bldg_id + 1, 0), index=np.arange(1, nb + 1))
    for i, bb in enumerate(buildings):
        if bb["ground_z"] is None:
            bb["ground_z"] = round(float(zb_med[i]), 2) if np.isfinite(zb_med[i]) else 0.0
    log(f"bldg cells {inside.sum()}, roof-clamped z cells {nclamp}, pier bldg cells {pier.sum()}")

    # ---------------- streets / parks / cover ----------------
    sfe = json.load(open(RAW / "extra" / "streets.geojson"))["features"]
    HALF_ROAD = {"1": 0.0, "2": 10.0, "3": 9.0, "4": 7.0, "5": 6.0, "0": 3.0, "6": 0.0}  # centerline->curb, m
    HALF_ROW = {"1": 6.0, "2": 18.0, "3": 13.0, "4": 12.0, "5": 10.0, "0": 4.0, "6": 4.0}  # incl. sidewalks
    streets = []
    for f in sfe:
        p = f["properties"]
        if not p.get("active") or f["geometry"] is None:
            continue
        g = to_utm_geom(f["geometry"])
        cc = str(p.get("classcode") or "0")
        streets.append((g, cc, p.get("streetname") or p.get("street") or ""))
    row = rast(((g.buffer(HALF_ROW.get(cc, 4.0), cap_style="flat"), 1) for g, cc, _ in streets
                if cc not in ("1", "6")), fill=0)
    road = rast(((g.buffer(max(HALF_ROAD.get(cc, 3.0), 2.0), cap_style="flat"), 1) for g, cc, _ in streets
                 if cc not in ("1", "6")), fill=0)
    pfe = json.load(open(RAW / "extra" / "parks.geojson"))["features"]
    parks_g = [to_utm_geom(f["geometry"]) for f in pfe if f.get("geometry")]
    parks_g = [g for g in parks_g if g.intersects(shapely.box(x0, y0, xmax, ymax))]
    park = rast(((g, 1) for g in parks_g), fill=0)
    log(f"streets {len(streets)}, parks in tile {len(parks_g)}")

    # residential yards heuristic: non-street, non-building cells whose nearest parcel is residential
    res_mask = np.array([(ap.get("use_code") or "") in ("SRES", "MRES") for ap in aprops])
    open_cells = (row == 0) & (~inside) & (~bay) & (park == 0)
    oy, ox = np.where(open_cells)
    q = shapely.points(xc[ox], yc[oy])
    near = a_tree.query_nearest(q, max_distance=40.0, all_matches=False)
    resid = np.zeros(len(q), bool)
    resid[near[0]] = res_mask[near[1]]
    yard = np.zeros((ny, nx), bool)
    # yards: residential context and at least 1 cell away from a building edge on the street side is too
    # fine to model; use the honest coarse rule + keep 50% (SF rear yards are roughly half paved).
    keep = resid & (RNG.random(len(q)) < 0.5)
    yard[oy[keep], ox[keep]] = True
    yard = ndimage.binary_opening(yard) | (yard & ndimage.binary_erosion(yard | ~open_cells))

    cover = np.zeros((ny, nx), np.uint8)
    cover[(park == 1) & (road == 0)] = 2
    cover[yard] = 2
    cover[inside] = 1
    cover[bay] = 3
    manning = np.choose(cover, [0.015, 0.015, 0.035, 0.02]).astype(np.float32)
    ksat = np.zeros((ny, nx), np.float32)
    ksat[cover == 0] = 0.5
    ksat[cover == 2] = np.where(park[cover == 2] == 1, 20.0, 8.0)
    log("cover counts", {k: int((cover == k).sum()) for k in range(4)})

    # ---------------- flood100 ----------------
    fz = load_features("sfpuc_100yr_flood_zone.geojson")
    fl_g = [to_utm_geom(f["geometry"]) for f in fz if f.get("geometry")]
    flood100 = rast(((g, 1) for g in fl_g), fill=0)
    log(f"flood100 cells {int(flood100.sum())}")

    # ---------------- inlets ----------------
    inlets = []
    types = Counter()
    for f in load_features("storm_inlets.geojson"):
        if not f.get("geometry"):
            continue
        x, y = TO_UTM.transform(*f["geometry"]["coordinates"][:2])
        if not in_grid(x, y):
            continue
        ix, iy = to_ix(x, y)
        if bay[iy, ix]:
            continue
        t = f["properties"].get("drain_type") or "unknown"
        if "outlet" in t.lower():
            continue
        types[t] += 1
        inlets.append({"id": f["properties"].get("puc_maximo_asset_id") or f"inlet{len(inlets)}",
                       "x": round(x, 2), "y": round(y, 2), "ix": int(ix), "iy": int(iy), "type": t,
                       "capacity_m3s": INLET_CAP.get(t, INLET_CAP_DEFAULT)})
    log(f"inlets {len(inlets)}; total capacity {sum(i['capacity_m3s'] for i in inlets):.1f} m3/s")

    # ---------------- trees ----------------
    trees = []
    sp_count = Counter()
    raw_trees = json.load(open(RAW / "trees.json"))
    dbh_by_sp = defaultdict(list)
    for t in raw_trees:
        d = fnum(t.get("mapdbh"), 0)
        if d and 0 < d < 120:
            dbh_by_sp[t.get("species", "")].append(d)
    for t in raw_trees:
        sp = (t.get("species") or "").strip()
        sci = sp.split("::")[0].strip()
        if sci.lower().startswith(("planting site", "tree(s)")) or not sci:
            sci_ok = False
        else:
            sci_ok = True
        lat, lon = fnum(t.get("latitude")), fnum(t.get("longitude"))
        if lat is None or lon is None:
            continue
        x, y = TO_UTM.transform(lon, lat)
        if not in_grid(x, y):
            continue
        ix, iy = to_ix(x, y)
        d_in = fnum(t.get("mapdbh"), 0)
        if not d_in or d_in <= 0 or d_in > 120:
            arr = dbh_by_sp.get(t.get("species", ""), [])
            d_in = float(np.median(arr)) if arr else 8.0
        dbh = d_in * 2.54
        genus = sci.split()[0].lower() if sci_ok else ""
        form, mor = GENUS.get(genus, ("medium", None))
        Hmax, k, ca, cb = FORM[form]
        H = 1.37 + (Hmax - 1.37) * (1 - math.exp(-k * dbh))
        crown = 3.0 + 0.04 * dbh if form == "palm" else ca * dbh ** cb
        crown = float(min(max(crown, 1.0), 25.0))
        sp_count[form] += 1
        trees.append({"id": len(trees), "src_id": t.get("treeid"), "x": round(x, 2), "y": round(y, 2),
                      "ix": int(ix), "iy": int(iy), "species": sci or "unknown",
                      "common": sp.split("::")[1].strip() if "::" in sp else "",
                      "dbh_cm": round(dbh, 1), "height_m": round(H, 1), "crown_m": round(crown, 1),
                      "wood_mor_mpa": float(mor if mor else MOR_DEFAULT), "form": form,
                      "street": t.get("onstreet") or "", "site": t.get("siteinfo") or "",
                      "on_bldg": bool(bldg_id[iy, ix] >= 0)})
    log(f"trees {len(trees)} forms {dict(sp_count)}")

    # ---------------- cars ----------------
    cars = []
    blocked = inside | bay | (cover == 2)
    STALL = 6.5
    for g, cc, name in streets:
        if cc not in ("2", "3", "4", "5"):
            continue
        L = g.length
        if L < 30:
            continue
        off = HALF_ROAD[cc] - 1.1
        n = int((L - 24) // STALL)
        if n <= 0:
            continue
        s = 12 + STALL * (np.arange(n) + 0.5)
        for side in (+1, -1):
            for si in s:
                if RNG.random() > 0.80:  # ~80% curb occupancy
                    continue
                p0 = g.interpolate(si)
                p1 = g.interpolate(min(si + 1.0, L))
                dxs, dys = p1.x - p0.x, p1.y - p0.y
                nrm = math.hypot(dxs, dys) or 1.0
                ux, uy = dxs / nrm, dys / nrm
                cx, cy = p0.x + side * off * uy, p0.y - side * off * ux  # right of travel for side=+1
                if not in_grid(cx, cy):
                    continue
                ix, iy = to_ix(cx, cy)
                if blocked[iy, ix]:
                    continue
                hd = (math.degrees(math.atan2(ux, uy)) + (0 if side > 0 else 180)) % 360
                cars.append((cx, cy, hd))
    if len(cars) > 6000:
        sel = np.sort(RNG.choice(len(cars), 6000, replace=False))
        cars = [cars[i] for i in sel]
    vals = np.clip(RNG.lognormal(math.log(22000), 0.5, len(cars)), 4000, 150000)
    vals *= 25000 / vals.mean()
    cars = [{"id": i, "x": round(c[0], 2), "y": round(c[1], 2), "heading_deg": round(c[2], 1),
             "value_usd": int(round(v, -2))} for i, (c, v) in enumerate(zip(cars, vals))]
    log(f"cars {len(cars)}")

    # ---------------- validation ----------------
    val = []
    for f in load_features("311_storm_2022-12-31.geojson"):
        if not f.get("geometry"):
            continue
        p = f["properties"]
        sub = p.get("service_subtype") or ""
        if sub == "Flooding":
            kind = "flooding"
        elif sub == "Sewage_back_up":
            kind = "sewage"
        elif p.get("service_name") == "Tree Maintenance" and sub in ("Trees - Damaged_Tree", "Trees - Damaging_Property"):
            kind = "tree"
        else:
            continue
        x, y = TO_UTM.transform(*f["geometry"]["coordinates"][:2])
        if not in_grid(x, y):
            continue
        ix, iy = to_ix(x, y)
        val.append({"x": round(x, 2), "y": round(y, 2), "ix": int(ix), "iy": int(iy), "kind": kind,
                    "subtype": sub, "t": p.get("requested_datetime"), "address": p.get("address")})
    vk = Counter(v["kind"] for v in val)
    validation = {"311_2022_12_31": val,
                  "counts": dict(vk),
                  "flood100_cells": int(flood100.sum()),
                  "sources": [SOURCES["validation"], SOURCES["flood100"]]}
    log(f"validation 311 in tile {dict(vk)}")

    # ---------------- write data/grid ----------------
    meta = {"crs": CRS, "dx": DX, "nx": nx, "ny": ny, "x0": x0, "y0": y0,
            "x1": xmax, "y1": ymax, "order": "[iy, ix], iy=0 south; cell centre x0+(ix+0.5)dx",
            "bbox_lonlat": [-122.425, 37.758, -122.385, 37.795],
            "z_datum": "NAVD88 m", "bay_z": -2.0, "bay_source_level_m": -1.07,
            "counts": {"buildings": nb, "inlets": len(inlets), "trees": len(trees), "cars": len(cars),
                       "bay_cells": int(bay.sum())},
            "cover_codes": {"0": "impervious street/paved", "1": "roof", "2": "pervious", "3": "bay"},
            "repl_cost_usd_m2": REPL_COST_M2, "contents_ratio": CONTENTS_RATIO,
            "sources": SOURCES, "built": time.strftime("%Y-%m-%dT%H:%M:%S")}
    json.dump(meta, open(OUT / "meta.json", "w"), indent=1)
    np.savez_compressed(OUT / "city.npz", z=z.astype(np.float32), bldg_id=bldg_id.astype(np.int32),
                        bldg_h=bldg_h.astype(np.float32), cover=cover, manning=manning, ksat=ksat,
                        flood100=flood100.astype(np.uint8))
    json.dump(inlets, open(OUT / "inlets.json", "w"))
    json.dump(buildings, open(OUT / "buildings.json", "w"))
    json.dump(trees, open(OUT / "trees.json", "w"))
    json.dump(cars, open(OUT / "cars.json", "w"))
    json.dump(validation, open(OUT / "validation.json", "w"))

    # ---------------- web assets (local metres rel. to x0,y0) ----------------
    ny8, nx8 = ny // 2, nx // 2
    z8 = z[: ny8 * 2, : nx8 * 2].reshape(ny8, 2, nx8, 2).mean(axis=(1, 3)).astype(np.float32)
    f8 = flood100[: ny8 * 2, : nx8 * 2].reshape(ny8, 2, nx8, 2).max(axis=(1, 3)).astype(np.uint8)
    c8 = cover[: ny8 * 2, : nx8 * 2].reshape(ny8, 2, nx8, 2)
    bay8 = (c8 == 3).mean(axis=(1, 3)) >= 0.5
    z8.tofile(WEB / "terrain_8m.f32")
    f8.tofile(WEB / "flood100_8m.u8")
    bay8.astype(np.uint8).tofile(WEB / "bay_8m.u8")
    json.dump({"nx": nx8, "ny": ny8, "dx": 8.0, "x0": x0, "y0": y0, "crs": CRS,
               "order": "row-major, row 0 = south, cell centre local x=(ix+0.5)*8, y=(iy+0.5)*8",
               "coords": "local metres: x east, y north, relative to (x0, y0) UTM 10N",
               "z_min": float(z8.min()), "z_max": float(z8.max()), "z_datum": "NAVD88 m",
               "files": {"terrain": "terrain_8m.f32 (float32)", "flood100": "flood100_8m.u8",
                         "bay": "bay_8m.u8 (1 = bay cell)"},
               "grid4": {"nx": nx, "ny": ny, "dx": DX}}, open(WEB / "terrain_meta.json", "w"), indent=1)

    def L(x, y):
        return [round(x - x0, 1), round(y - y0, 1)]

    bweb = [{"id": bb["id"], "footprint": [L(*q) for q in bb["footprint"]], "height": bb["height_m"],
             "ground_z": bb["ground_z"], "occupancy": bb["occupancy"],
             "value": bb["value_structure"] + bb["value_contents"], "floors": bb["floors"]} for bb in buildings]
    json.dump(bweb, open(WEB / "buildings_web.json", "w"), separators=(",", ":"))
    zat = lambda x, y: float(z[min(max(int((y - y0) // DX), 0), ny - 1), min(max(int((x - x0) // DX), 0), nx - 1)])
    json.dump([{"id": t["id"], "p": L(t["x"], t["y"]), "z": round(zat(t["x"], t["y"]), 2), "h": t["height_m"],
                "crown": t["crown_m"], "dbh": t["dbh_cm"], "form": t["form"], "species": t["species"]}
               for t in trees], open(WEB / "trees_web.json", "w"), separators=(",", ":"))
    json.dump([{"id": c["id"], "p": L(c["x"], c["y"]), "z": round(zat(c["x"], c["y"]), 2),
                "heading": c["heading_deg"], "value": c["value_usd"]} for c in cars],
              open(WEB / "cars_web.json", "w"), separators=(",", ":"))
    json.dump([{"id": i["id"], "p": L(i["x"], i["y"]), "z": round(zat(i["x"], i["y"]), 2),
                "type": i["type"], "cap": i["capacity_m3s"]} for i in inlets],
              open(WEB / "inlets_web.json", "w"), separators=(",", ":"))
    json.dump({k: [{"p": L(v["x"], v["y"]), "kind": v["kind"], "t": v["t"], "address": v["address"]}
                   for v in val] for k in ["311_2022_12_31"]}, open(WEB / "validation_web.json", "w"),
              separators=(",", ":"))

    # ---------------- summary ----------------
    land = ~bay
    occ = Counter(bb["occupancy"] for bb in buildings)
    tv = sum(bb["value_structure"] for bb in buildings)
    tc = sum(bb["value_contents"] for bb in buildings)
    ta = sum(bb["assessed_improvement_usd"] for bb in buildings)
    log(f"z land min/median/max {z[land].min():.2f}/{np.median(z[land]):.2f}/{z[land].max():.2f} m")
    log(f"occupancy {dict(occ.most_common())}")
    log(f"value structure ${tv/1e9:.1f}B contents ${tc/1e9:.1f}B (assessed improvements ${ta/1e9:.1f}B)")
    log(f"web: terrain_8m {ny8}x{nx8}")
    if preview:
        render_preview(z, cover, flood100, bldg_id, val, inlets, x0, y0, nx, ny)
    log("done")


def render_preview(z, cover, flood100, bldg_id, val, inlets, x0, y0, nx, ny):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    ls = LightSource(azdeg=315, altdeg=45)
    zz = np.where(cover == 3, np.nan, z)
    hs = ls.hillshade(np.nan_to_num(z, nan=-2.0)[::-1], vert_exag=3, dx=DX, dy=DX)[::-1]
    fig, ax = plt.subplots(figsize=(12, 14), dpi=110)
    ext = [0, nx * DX, 0, ny * DX]
    ax.imshow(hs, cmap="gray", origin="lower", extent=ext, alpha=1.0)
    ax.imshow(zz, cmap="terrain", origin="lower", extent=ext, alpha=0.35, vmin=-5, vmax=80)
    rgba = np.zeros((ny, nx, 4))
    rgba[cover == 3] = (0.2, 0.4, 0.8, 0.9)
    rgba[cover == 2] = (0.2, 0.7, 0.2, 0.45)
    rgba[bldg_id >= 0] = (0.35, 0.35, 0.35, 0.75)
    rgba[(flood100 == 1) & (cover != 3)] = (0.1, 0.8, 1.0, 0.55)
    ax.imshow(rgba, origin="lower", extent=ext)
    col = {"flooding": "red", "sewage": "orange", "tree": "lime"}
    for k, c in col.items():
        pp = np.array([[v["x"] - x0, v["y"] - y0] for v in val if v["kind"] == k]).reshape(-1, 2)
        ax.scatter(pp[:, 0], pp[:, 1], s=9, c=c, edgecolors="k", linewidths=0.3, label=f"311 {k} ({len(pp)})")
    ip = np.array([[i["x"] - x0, i["y"] - y0] for i in inlets])
    ax.scatter(ip[:, 0], ip[:, 1], s=1, c="navy", label=f"inlets ({len(ip)})")
    ax.legend(loc="lower left")
    ax.set_title("StormSim SF prep: hillshade, buildings, parks/yards (green), SFPUC 100-yr flood zone (cyan), "
                 "311 on 31.12.2022", fontsize=9)
    ax.set_xlabel("x east (m from x0)")
    ax.set_ylabel("y north (m from y0)")
    fig.tight_layout()
    fig.savefig(OUT / "preview.png")
    plt.close(fig)
    log("preview.png written")


if __name__ == "__main__":
    main(preview="--no-preview" not in sys.argv)
