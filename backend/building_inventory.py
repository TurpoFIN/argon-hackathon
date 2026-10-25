"""StormSim building derivation for the host's stable DataSF footprint IDs.

Replacement costs and their original Hazus/RLB sources live in
reference/building_inventory.json. Assessed values remain a separate field.
"""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import urllib.parse

import numpy as np
import shapely
from shapely.geometry import Point, shape
from shapely.ops import transform

from . import risk_assets

REFERENCE = json.loads((Path(__file__).parent / 'reference/building_inventory.json').read_text())
REPL_COST_M2 = REFERENCE['repl_cost_usd_m2']
CONTENTS_RATIO = REFERENCE['contents_ratio']


def fnum(v, default=None):
    try:
        if v is None or v == "":
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


# Post-2010 towers missing from the 2010 lidar heights: (lon, lat, roof/top height m, name); CTBUH heights.
_KT = [(-122.39675, 37.78977, 326.0, "Salesforce Tower"), (-122.39553, 37.78977, 245.0, "181 Fremont"),
       (-122.39560, 37.78952, 184.0, "Park Tower at Transbay"), (-122.39890, 37.78880, 116.0, "535 Mission"),
       (-122.39760, 37.78830, 120.0, "Mira"), (-122.39440, 37.78880, 188.0, "The Avery / Transbay Blk 8")]
KNOWN_TOWERS = [(*risk_assets.TO(lo, la), hh, nm) for lo, la, hh, nm in _KT]


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


def load_sources():
    """Bounded, reproducible public-data caches, also usable on a fresh checkout."""
    query = {'$where': 'within_box(shape,37.808,-122.406,37.786,-122.386)', '$limit': 200000}
    full, fs = risk_assets.fetch('datasf-buildings-full.geojson',
        'https://data.sf.gov/resource/ynuv-fyni.geojson?' + urllib.parse.urlencode(query))
    query = {'$where': "closed_roll_year='2023' AND within_box(the_geom,37.808,-122.406,37.786,-122.386)", '$limit': 200000}
    roll, rs = risk_assets.fetch('datasf-assessor-2023.geojson',
        'https://data.sf.gov/resource/wv5m-vpq2.geojson?' + urllib.parse.urlencode(query), max_bytes=24_000_000)
    fs.update(name='DataSF full building footprints', basis=REFERENCE['sources']['buildings'])
    rs.update(name='SF Assessor-Recorder secured roll 2023', basis=REFERENCE['sources']['assessor'])
    return full['features'], roll['features'], [fs, rs, dict(
        name='StormSim Hazus occupancy, contents and RLB replacement costs',
        file='backend/reference/building_inventory.json', sources=REFERENCE['sources'])]


def enrich(buildings, features, assessor):
    """Mutate attributes only; use the full footprint set for the original parcel assignment.

    Unmatched footprints retain their existing height. Occupancy alone may use a
    parcel within 60 m; year, stories and assessed values require a 25 m join.
    Unknown year/use code remain null rather than being invented.
    """
    bgeoms, bprops, indices = [], [], {}
    for f in features:
        g = transform(risk_assets.TO, shape(f['geometry']))
        if g.is_empty:
            continue
        if not g.is_valid:
            g = g.buffer(0)
        indices['B' + str(f['properties']['sf16_bldgid'])] = len(bgeoms)
        bgeoms.append(g)
        bprops.append(f['properties'])
    aprops, apts = [], []
    for f in assessor:
        if f.get('geometry'):
            apts.append(risk_assets.TO(*f['geometry']['coordinates'][:2]))
            aprops.append(f['properties'])
    pts = shapely.points(np.asarray(apts).reshape(-1, 2))
    tree = shapely.STRtree(bgeoms)
    pi, bi = tree.query(pts, predicate='within')
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
    a_tree = shapely.STRtree(pts)
    for b in buildings:
        full = b['id'] in indices
        i = indices.get(b['id'], -1)
        g = bgeoms[i] if full else transform(risk_assets.TO, shape(b['geometry']))
        p = bprops[i] if full else {}
        area = float(g.area)
        h = fnum(p.get("hgt_median_m"), None) if full else float(b["height_m"])
        hmax = fnum(p.get("hgt_maxcm"), None)
        hmax = hmax / 100 if hmax is not None else None
        if full and (h is None or h < 2.5):
            h = max(hmax or 0, 3.0) if (hmax or 0) < 6 else max(3.0, 0.8 * hmax)
        h = float(min(max(h, 2.5), 330.0)) if full else h
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
        if full and plaus and fl_s * fh > 2.0 * h:
            h, h_src = float(fl_s * fh), "assessor_stories"
        for (kx, ky, kh, kname) in KNOWN_TOWERS:
            if full and g.contains(Point(kx, ky)):
                h, h_src = kh, f"known:{kname}"
        if h_src != "lidar2010":
            hmax = max(hmax or 0, h)
        fl_h = max(1, round(h / fh))
        floors = int(fl_s) if fl_s and 0.7 <= fl_s * fh / h <= 1.5 else int(fl_h)
        floor_area = area * floors
        vs = floor_area * REPL_COST_M2.get(occ, 5000)
        vc = vs * CONTENTS_RATIO.get(occ, 1.0)
        ffm = 0.0 if (occ.startswith(("COM", "IND")) or retail) else 0.3
        b.update(
            height_m=round(h, 2), height_max_m=round(hmax, 2) if hmax else round(h, 2),
            floors=floors, height_source=h_src if full else 'existing_height',
            ground_z=None if gz is None else round(gz, 2), occupancy=occ,
            use_code=use_codes.most_common(1)[0][0] if use_codes else None,
            year_built=int(np.median(yrs)) if yrs else None,
            value_structure=round(vs), value_contents=round(vc), first_floor_m=ffm,
            floor_area_m2=round(floor_area, 1), assessed_improvement_usd=round(assessed),
            assessor_area_sqft=round(prop_area), n_parcels=len(ks), ground_retail=bool(retail),
            inventory_basis=('full_footprint' if full else 'existing_height_fallback') +
                (';assessor_join' if ks else ';nearest_occupancy' if ks_occ else ';height_occupancy_fallback'))

    return dict(buildings_enriched=sum(b['id'] in indices for b in buildings),
                buildings_assessor_joined=sum(b['n_parcels'] > 0 for b in buildings),
                buildings_inventory_fallback=sum(b['id'] not in indices for b in buildings))


def ground_fallback(buildings, ground, lon, lat):
    """Original DEM median fallback, using the host's saved 12 m terrain.

    The reference's separate 4 m DEM is not an input to the shared water solver.
    Use only cells within each footprint, or the nearest terrain cell for a
    footprint smaller than a terrain cell. Preserve a measured lidar ground.
    """
    from scipy.spatial import cKDTree
    x, y = risk_assets.TO(lon, lat)
    lookup = cKDTree(np.column_stack((x.ravel(), y.ravel())))
    for b in buildings:
        if b['ground_z'] is not None:
            continue
        g = transform(risk_assets.TO, shape(b['geometry']))
        mask = shapely.contains_xy(g, x, y)
        values = ground[mask]
        if not len(values):
            values = ground.ravel()[[lookup.query(b['xy'])[1]]]
        b['ground_z'] = round(float(np.median(values)), 2)
        b['inventory_basis'] += ';host_dem_ground'
