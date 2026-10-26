"""Dollar flood damage: USACE NSI 2022 structures x USACE go-consequences depth-damage curves.

Depth at a structure is the simulated peak water depth in the open (non-building, non-bay) cells
around it, so it is the water standing against the building. Depth above the first floor is that
depth minus the NSI foundation height. A structure counts as flooded at 3 cm or more.
"""
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
CURVES = json.loads((HERE / "reference" / "depth_damage.json").read_text())
NSI_FIELDS = ["fd_id", "occtype", "st_damcat", "bldgtype", "found_type", "found_ht", "num_story",
              "val_struct", "val_cont", "ground_elv", "pop2pmu65", "pop2pmo65", "pop2amu65", "pop2amo65", "x", "y"]
FT = 0.3048
WET_M = 0.03
EXPOSED_STOREYS = 3
ASSUMPTIONS = [
    "Depth at a building is the peak water depth in the adjacent open cells (12 m; wider only for footprints with no open neighbour).",
    "Below the first floor only buildings with a basement take damage; others start at the first floor.",
    "A building counts as flooded at 3 cm of water against it; damage uses depth above its NSI first-floor height.",
    f"Tall buildings expose at most {EXPOSED_STOREYS} storeys' share of their NSI value to street flooding.",
]
CATEGORY_FALLBACK = {"RES": "RES1-1SNB", "COM": "COM1", "IND": "IND1", "PUB": "GOV1"}


def nsi_structures(bbox, cache: Path):
    """All NSI structures in bbox [W, S, E, N], fetched once and cached with provenance."""
    if cache.is_file():
        return json.loads(cache.read_text())
    w, s, e, n = bbox
    ring = [w, s, w, n, e, n, e, s, w, s]
    url = f"https://nsi.sec.usace.army.mil/nsiapi/structures?bbox={','.join(map(str, ring))}&fmt=fc"
    request = urllib.request.Request(url, headers={"User-Agent": "argon-hackathon prep loop"})
    with urllib.request.urlopen(request, timeout=90) as response:
        features = json.load(response)["features"]
    rows = [{k: f["properties"].get(k) for k in NSI_FIELDS} for f in features]
    payload = {"source": url, "retrieved": datetime.now(timezone.utc).isoformat(), "structures": rows}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload))
    return payload


def _curve(occtype, category):
    curves = CURVES["curves"]
    key = occtype if occtype in curves else (occtype or "").split("-")[0]
    if key not in curves:
        key = CATEGORY_FALLBACK.get(category, "COM1")
    return key, curves[key]


class Exposure:
    """Structures mapped onto the simulation grid once; score() then runs per flood result."""

    def __init__(self, lon, lat, walls, ocean, nsi):
        self.source, self.retrieved = nsi["source"], nsi["retrieved"]
        lat0 = float(lat.mean())
        k = 111320.0
        cells = np.column_stack([(lon.ravel() - lon.mean()) * k * np.cos(np.radians(lat0)), (lat.ravel() - lat0) * k])
        rows = nsi["structures"]
        points = np.column_stack([(np.array([r["x"] for r in rows]) - lon.mean()) * k * np.cos(np.radians(lat0)),
                                  (np.array([r["y"] for r in rows]) - lat0) * k])
        distance, index = cKDTree(cells).query(points)
        inside = distance <= 12.0  # one grid cell; structures beyond it are outside the model domain
        self.rows = [r for r, ok in zip(rows, inside) if ok]
        self.cell = np.array(np.unravel_index(index[inside], lon.shape)).T
        self.on_bay = ocean[self.cell[:, 0], self.cell[:, 1]]
        self.open = ~walls & ~ocean
        self.ocean = ocean
        self.found_ft = np.array([float(r["found_ht"] or 0) for r in self.rows])
        # Assumption: street flooding reaches only the lowest floors, so a tall building exposes at most
        # three storeys' share of its value (the curves already cover typical low-rise buildings).
        stories = np.array([float(r["num_story"] or 1) for r in self.rows])
        self.exposed_share = np.minimum(1.0, EXPOSED_STOREYS / np.maximum(stories, 1.0))
        self.value_s = np.array([float(r["val_struct"] or 0) for r in self.rows]) * self.exposed_share
        self.value_c = np.array([float(r["val_cont"] or 0) for r in self.rows]) * self.exposed_share
        self.curves = [_curve(r["occtype"], r["st_damcat"]) for r in self.rows]
        self.unmatched = sorted({r["occtype"] for r, (key, _) in zip(self.rows, self.curves) if key != r["occtype"]})
        r, c = self.cell[:, 0], self.cell[:, 1]
        self.use3 = (maximum_filter(self.open.astype(np.uint8), size=3) > 0)[r, c]
        self.use5 = (maximum_filter(self.open.astype(np.uint8), size=5) > 0)[r, c]
        # Only buildings with a basement take damage while the water stays below the first floor.
        self.basement = np.array([(row.get("found_type") or "") == "B" for row in self.rows])

    def depths(self, peak):
        land = np.where(self.ocean, 0.0, peak)
        d3, d5, d9 = (maximum_filter(land, size=k) for k in (3, 5, 9))
        r, c = self.cell[:, 0], self.cell[:, 1]
        # Water against the building: adjacent cells (12 m); wider only when the footprint has no open neighbour.
        return np.where(self.use3, d3[r, c], np.where(self.use5, d5[r, c], d9[r, c]))

    def score(self, peak, protected=(), protect_m=0.0):
        """protected: fd_ids with sandbagged doorways; water below protect_m stays out of them."""
        depth_m = self.depths(peak)
        above_ft = depth_m / FT - self.found_ft
        above_ft = np.where(self.basement, above_ft, np.where(above_ft < 0, -99.0, above_ft))
        protected = set(protected)
        guarded = np.array([r["fd_id"] in protected for r in self.rows]) & (depth_m < protect_m)
        wet = (depth_m >= WET_M) & ~self.on_bay & ~guarded
        pct_s = np.array([np.interp(a, cv["structure"]["depth_ft"], cv["structure"]["damage_pct"], left=0.0)
                          for a, (_, cv) in zip(above_ft, self.curves)])
        pct_c = np.array([np.interp(a, cv["contents"]["depth_ft"], cv["contents"]["damage_pct"], left=0.0)
                          for a, (_, cv) in zip(above_ft, self.curves)])
        usd_s = np.where(wet, pct_s / 100 * self.value_s, 0.0)
        usd_c = np.where(wet, pct_c / 100 * self.value_c, 0.0)
        usd = usd_s + usd_c
        people_day = np.array([(r["pop2pmu65"] or 0) + (r["pop2pmo65"] or 0) for r in self.rows])
        by_category = {}
        for row, value in zip(self.rows, usd):
            if value > 0:
                by_category[row["st_damcat"]] = by_category.get(row["st_damcat"], 0.0) + float(value)
        order = np.argsort(-usd)[:12]
        return {
            "assumptions": ASSUMPTIONS,
            "total_usd": round(float(usd.sum()), 2),
            "structure_usd": round(float(usd_s.sum()), 2),
            "contents_usd": round(float(usd_c.sum()), 2),
            "structures_in_domain": len(self.rows),
            "structures_flooded": int(wet.sum()),
            "structures_damaged": int((usd > 0).sum()),
            "structures_kept_dry_by_doorway_sandbags": int(guarded.sum()),
            "people_daytime_in_flooded": int(people_day[wet].sum()),
            "by_category_usd": {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
            "top_structures": [{"fd_id": self.rows[i]["fd_id"], "occtype": self.rows[i]["occtype"],
                                "lon": round(self.rows[i]["x"], 6), "lat": round(self.rows[i]["y"], 6),
                                "depth_m": round(float(depth_m[i]), 3), "above_first_floor_ft": round(float(above_ft[i]), 2),
                                "usd": round(float(usd[i]), 2)} for i in order if usd[i] > 0],
            "_per_structure_usd": usd,
            "_per_structure_depth_m": depth_m,
        }
