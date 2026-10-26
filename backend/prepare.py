"""Download a bounded public-data cut and precompute the Embarcadero scenario."""
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import io
import json
import urllib.parse
import urllib.request

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from pyproj import Transformer
from scipy.ndimage import label, distance_transform_edt
from .simulation import Budget, advance_water, potential_wind

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
BBOX = [-122.486, 37.786, -122.386, 37.812]  # Presidio to North Beach and the Embarcadero
DX = 12.0
CRS = "EPSG:32610"


def retrieve(name, url):
    path = RAW / name
    if not path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "EmbarcaderoStormPrototype/0.1", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read(120_000_000)
        if len(raw) >= 120_000_000:
            raise RuntimeError(f"Bounded download exceeded: {name}")
        path.write_bytes(raw)
    return path.read_bytes()


def source_json(name, url):
    return json.loads(retrieve(name, url))


def socrata(dataset, select, geometry):
    w, s, e, n = BBOX
    polygon = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"
    params = {"$select": select, "$where": f"intersects({geometry},'{polygon}')", "$limit": 60000}
    url = f"https://data.sf.gov/resource/{dataset}.json?" + urllib.parse.urlencode(params)
    return source_json(dataset + ".json", url), url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    forward = Transformer.from_crs(4326, CRS, always_xy=True)
    inverse = Transformer.from_crs(CRS, 4326, always_xy=True)
    w, s = forward.transform(BBOX[0], BBOX[1])
    e, n = forward.transform(BBOX[2], BBOX[3])
    nx, ny = int(np.ceil((e - w) / DX)), int(np.ceil((n - s) / DX))
    e, s = w + nx * DX, n - ny * DX
    transform = from_origin(w, n, DX, DX)
    dem_url = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?" + urllib.parse.urlencode({
        "bbox": f"{w},{s},{e},{n}", "bboxSR": "32610", "imageSR": "32610",
        "size": f"{nx},{ny}", "format": "tiff", "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation", "renderingRule": '{"rasterFunction":"None"}', "f": "image"
    })
    atlas_url = "https://hdsc.nws.noaa.gov/cgi-bin/hdsc/new/fe_text_mean.csv?lat=37.7955&lon=-122.3947&data=depth&units=metric&series=pds"
    common = {"begin_date": "20230104", "end_date": "20230105", "station": "9414290", "time_zone": "gmt", "units": "metric", "format": "json"}
    noaa = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?"
    wind_url = noaa + urllib.parse.urlencode({**common, "product": "wind"})
    tide_url = noaa + urllib.parse.urlencode({**common, "product": "water_level", "datum": "NAVD"})
    with ThreadPoolExecutor(max_workers=6) as pool:
        dem_task = pool.submit(retrieve, "terrain.tif", dem_url)
        bldg_task = pool.submit(socrata, "ynuv-fyni", "sf16_bldgid,hgt_median_m,gnd_min_m,shape", "shape")
        drain_task = pool.submit(socrata, "jtgq-b7c5", "dpw_node_id,drain_type,location", "location")
        atlas_task = pool.submit(retrieve, "atlas14.csv", atlas_url)
        wind_task = pool.submit(source_json, "wind-20230104.json", wind_url)
        tide_task = pool.submit(source_json, "tides-20230104.json", tide_url)
        with rasterio.MemoryFile(dem_task.result()) as mem, mem.open() as dataset:
            ground = dataset.read(1).astype(np.float64)
            if ground.shape != (ny, nx) or dataset.count != 1:
                raise RuntimeError("DEM raster shape differs from requested grid")
            if np.any(~np.isfinite(ground)) or np.any(ground < -100):
                raise RuntimeError("Missing DEM cells: cannot fabricate elevations")
        building_rows, building_url = bldg_task.result()
        drains, drain_url = drain_task.result()
        atlas = atlas_task.result().decode()
        winds, tides = wind_task.result(), tide_task.result()
    rain_row = next(line for line in atlas.splitlines() if line.startswith("60-min:"))
    rainfall_mm = float(rain_row.split(",")[7])  # published 100-year column
    observed_wind = max((r for r in winds["data"] if r.get("g")), key=lambda r: float(r["g"]))
    observed_tide = max((r for r in tides["data"] if r.get("v") and r.get("q") == "v"), key=lambda r: float(r["v"]))
    print(f"Downloaded {nx}x{ny} terrain, {len(building_rows)} buildings, {len(drains)} drains", flush=True)

    def projected_shape(geometry):
        from shapely.geometry import shape, mapping
        from shapely.ops import transform as project
        return mapping(project(forward.transform, shape(geometry)))

    # The larger Presidio-to-North-Beach area includes footprints without lidar heights; skip and count them.
    missing_height = sum(1 for b in building_rows if not b.get("hgt_median_m") or float(b["hgt_median_m"]) <= 0)
    building_rows = [b for b in building_rows if b.get("hgt_median_m") and float(b["hgt_median_m"]) > 0]
    print(f"Skipped {missing_height} footprints without a valid height", flush=True)
    shapes = [(projected_shape(b["shape"]), float(b["hgt_median_m"])) for b in building_rows]
    height = rasterize(shapes, out_shape=(ny, nx), transform=transform, fill=0, dtype="float32")
    buildings = height > 2
    low = ground < 0.8
    labels, _ = label(low)
    sea_labels = np.unique(labels[:, -1])
    ocean = np.isin(labels, sea_labels[sea_labels != 0])
    if ocean.sum() < 20:
        raise RuntimeError("No coastal boundary resolved in DEM; inspect real data")
    buildings[ocean] = False
    # Roof rain is routed to the nearest cell of the connected street network, conserving volume. Rear yards
    # walled in by row houses are separate components with no inlet; routing whole roofs into them ponded
    # metres of water there that real downspouts never send.
    land = ~ocean
    pieces, _ = label(land & ~buildings)
    sizes = np.bincount(pieces.ravel())
    sizes[0] = 0
    rim = np.unique(np.concatenate([pieces[0], pieces[-1], pieces[:, 0], pieces[:, -1], pieces[ocean | np.roll(ocean, 1, 0) | np.roll(ocean, 1, 1)]]))
    street = np.isin(pieces, [sizes.argmax()] + [k for k in rim if k and sizes[k] >= 200])
    print(f"Street network {int(street.sum())} cells; {int((land & ~buildings & ~street).sum())} enclosed yard cells keep only their own rain", flush=True)
    _, nearest = distance_transform_edt(~(street | ocean), return_indices=True)
    rain_rate = np.zeros_like(ground)
    ri, rj = np.indices(ground.shape)
    target_i = np.where(buildings, nearest[0], ri)[land]
    target_j = np.where(buildings, nearest[1], rj)[land]
    np.add.at(rain_rate, (target_i, target_j), rainfall_mm / 1000 / 3600 * 0.9)
    drain_rate = np.zeros_like(ground)
    used_drains = 0
    for d in drains:
        lon, lat = d["location"]["coordinates"]
        x, y = forward.transform(lon, lat)
        j, i = int((x - w) // DX), int((n - y) // DX)
        if 0 <= i < ny and 0 <= j < nx and not ocean[i, j]:
            if buildings[i, j]:
                i, j = int(nearest[0, i, j]), int(nearest[1, i, j])
            drain_rate[i, j] += 0.006 * 0.35 / DX ** 2
            used_drains += 1
    speed, direction, gust = [float(observed_wind[k]) for k in ["s", "d", "g"]]
    u, v = potential_wind(buildings, speed, direction, DX)
    tide_base = float(observed_tide["v"])
    h = np.zeros_like(ground)
    h[ocean] = np.maximum(tide_base - ground[ocean], 0)
    budget = Budget(initial_m3=float(h.sum() * DX ** 2))
    snapshots, times = [], []
    dt = 1.0
    for step in range(args.minutes * 60 + 1):
        if step % 120 == 0:
            snapshots.append(h.copy().astype("float32"))
            times.append(step)
        if step == args.minutes * 60:
            break
        tide = tide_base + min(step / 1800, 1) * 1.0
        advance_water(h, ground, buildings, ocean, rain_rate, drain_rate, tide, DX, dt, budget)
    final_volume = float(h.sum() * DX ** 2)
    balance_error = final_volume - budget.initial_m3 - budget.rain_m3 + budget.drained_m3 - budget.coastal_exchange_m3
    if abs(balance_error) > 0.01:
        raise RuntimeError(f"Water budget mismatch: {balance_error} m3")
    lon, lat = inverse.transform(w + (rj + 0.5) * DX, n - (ri + 0.5) * DX)
    np.savez_compressed(DATA / "model-inputs.npz", ground=ground, height=height,
        buildings=buildings, ocean=ocean, lon=lon, lat=lat,
        runoff_weights=rain_rate / (rainfall_mm / 1000 / 3600 * 0.9),
        drain_counts=drain_rate * DX ** 2 / (0.006 * 0.35))
    arrays = np.stack([lon, lat, ground, height, u, v, ocean.astype(float)]).astype("float32")
    arrays.tofile(DATA / "grid.bin")
    np.stack(snapshots).tofile(DATA / "flood.bin")
    source_urls = {"terrain": dem_url, "buildings": building_url, "drains": drain_url, "rainfall": atlas_url, "wind": wind_url, "tide": tide_url}
    metrics = {"building_records": len(building_rows), "drain_records": len(drains), "modeled_drains": used_drains,
        "wet_land_cells": int(((h > 0.05) & ~ocean & ~buildings).sum()),
        "max_land_depth_m": float(h[~ocean & ~buildings].max()), "mass_balance_error_m3": balance_error,
        "water_budget": vars(budget), "final_volume_m3": final_volume}
    manifest = {
        "name": "Embarcadero compound storm · scenario, not a forecast", "generated_at": datetime.now(timezone.utc).isoformat(),
        "bbox": BBOX, "grid": {"nx": nx, "ny": ny, "cell_m": DX, "crs": CRS, "origin_easting": w, "origin_northing": n,
            "bands": ["longitude", "latitude", "ground_navd88_m", "building_height_m", "wind_east_mps", "wind_north_mps", "ocean_mask"]},
        "times_seconds": times, "rainfall_mm_per_hour": rainfall_mm, "tide_base_navd88_m": tide_base,
        "wind": {"mean_mps": speed, "gust_mps": gust, "from_deg": direction, "observation": observed_wind},
        "tide_observation": observed_tide, "sources": source_urls, "metrics": metrics,
        "assumptions": ["Compound stress test combines Atlas 14 100-year one-hour design rain with Jan 2023 observed wind and peak tide; not a historical replay or joint 100-year probability.",
            "An additional hypothetical 1.0 m coastal water-level rise ramps in over 30 minutes.",
            "Uniform one-hour rain; 90% runoff coefficient; 0.035 Manning roughness.",
            "Drain capacity is assumed 6 L/s per mapped inlet, with 65% blockage and simplified tidal locking; no sewer network or backflow simulation.",
            "12 m terrain raster from USGS 3DEP, buildings from older DataSF footprint/height data; smaller curbs and walls unresolved.",
            "Non-coastal domain boundaries closed; roof runoff routed to nearest open cell.",
            "Wind is a 2-D inviscid potential-flow visualization derived from real footprints and observed forcing; no turbulence, wake, pressure or damage prediction.",
            "Hydrology uses NAVD88 metres. Rendering samples the map ground and overlays relative modeled water depth; the renderer ground and hydrology terrain can differ."],
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(RAW.iterdir())}
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(metrics, indent=2), flush=True)


if __name__ == "__main__":
    main()
