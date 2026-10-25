# StormSim SF — module contract (read this first)

Deadline: submissions 17:00 PT today; code freeze 16:15 PT. Read STORMSIM.md for the product. Python: `/home/ubuntu/work/argonhack/.venv/bin/python` (install with `uv pip install --python .venv/bin/python <pkg>`). Gemini: prefix commands with `scripts/gem.sh` (injects GEMINI_API_KEY; never print it). Box: 80 CPU, 500 GB RAM, **no GPU**. Cap dev runs at `NUMBA_NUM_THREADS=16`. Don't `git commit`; the lead commits. Only edit your own directory/files.

## Coordinates and grid (`data/grid/`, owned by prep)

- CRS EPSG:32610 (UTM 10N, metres). Tile lon/lat S37.758 N37.795 W-122.425 E-122.385.
- `data/grid/meta.json`: `{crs, dx: 4.0, nx, ny, x0, y0}`. (x0, y0) is the SW corner of cell [0,0]; array index `[iy, ix]` with iy=0 at **south**; cell centre x = x0 + (ix+0.5)·dx.
- `data/grid/city.npz` (all shape (ny, nx)):
  - `z` float32: ground/bare-earth elevation, m NAVD88 (bay cells = bathymetry or -2.0)
  - `bldg_id` int32: building index into buildings.json, -1 = none
  - `bldg_h` float32: building height above ground, m (0 none)
  - `cover` uint8: 0 impervious street/paved, 1 roof, 2 pervious (park/soil), 3 bay/water
  - `manning` float32 (0.015 paved, 0.035 pervious, 0.02 bay)
  - `ksat` float32: saturated hydraulic conductivity, mm/h (≈0 paved/roof, 10–25 pervious)
  - `flood100` uint8: SFPUC 100-yr storm flood zone mask (validation)
- `data/grid/inlets.json`: `[{id, x, y, ix, iy, type, capacity_m3s}]`
- `data/grid/buildings.json`: `[{id, x, y, area_m2, height_m, floors, ground_z, occupancy (Hazus class e.g. RES1, RES3, COM1, COM4, IND1, GOV1), use_code, year_built, value_structure, value_contents, first_floor_m, footprint: [[x,y],...] (simplified, UTM)}]`
- `data/grid/trees.json`: `[{id, x, y, ix, iy, species, dbh_cm, height_m, crown_m, wood_mor_mpa, street}]`
- `data/grid/cars.json`: `[{id, x, y, heading_deg, value_usd}]` (parked cars on curbs)
- `data/grid/validation.json`: `{"311_2022_12_31": [{x, y, kind: flooding|sewage|tree}], ...}`

## Storm (`sim/storm.py`, owned by damage/storm)

`load_storm(name) -> Storm` with fields: `name, duration_s, dt_frame_s, t (s array), rain_mm_h (array), gust_ms (array, 10 m reference gust), mean_ms, wind_dir_deg (meteorological, FROM), tide_m (NAVD88, bay boundary level), sources (list of citations)`. Scenarios: `dec31_2022` (replay for validation) and `elnino_ar_2026` (the incoming storm; bigger). Demo storm compresses to ≈6 h simulated.

## Physics

- `sim/water.py` (water): `run_water(grid, storm, plan_grid) -> WaterResult`. Local-inertial shallow water (Bates 2010) at 4 m, rain + Green-Ampt-ish infiltration + inlets as capacity-limited sinks into a lumped combined sewer with finite capacity (surcharge → inlets back up) + bay boundary at tide level + plan sinks (storage, pumps, tunnels). Returns depth frames (every `dt_frame_s`), max depth, flooded duration, soil saturation frames, sewer surcharge time series, inlet states.
- `sim/wind.py` (wind): D3Q19 lattice-Boltzmann LES on voxelised buildings (8 m, ~400 m tall), steady field for the storm direction at reference speed, cached in `data/grid/wind_<dir>_<hash>.npz`; scaled by `gust(t)/U_ref`. Exposes `wind_at(x, y, z)`, horizontal slices for the frontend, facade peak pressure per building.
- `sim/trees.py` (wind owner too): per-tree bending moment from drag vs. stem strength (snap) and vs. root anchorage reduced by soil saturation (uproot); failure time, fall direction, fall animation keyframes; debris (branches, glass from facade pressure) as rigid particles advected by the wind field; strikes on cars/buildings.
- `sim/damage.py` (damage): Hazus flood depth-damage (structure + contents) by occupancy, vehicle depth-damage, wind/glazing damage, tree-strike damage, tree removal cost, business interruption → `Ledger` dict: `{total_usd, by_cause: {...}, by_building: {id: usd}, trees_failed, cars_damaged, people_affected, notes, sources}`.
- `sim/costs.py` (damage): **locked** cost accountant. `price_plan(grid_before, grid_after, plan_log) -> {total_usd, items:[{what, qty, unit, unit_cost, usd, source}]}`. Prices are measured from physical diffs (earth volume moved, wall length×height, area resurfaced, storage m³, pump/tunnel m³/s, inlets upgraded, buildings floodproofed, trees pruned/removed/replaced) with cited unit costs.

## Plans (interventions)

A plan is Python code defining `def apply(city): ...` against `sim/city_api.py` (`CityEditor`, owned by damage/costs). The CityEditor exposes high-level helpers **and** raw grid arrays (`city.z`, `city.ksat`, `city.manning`, `city.cover`, sinks list, `city.trees`, `city.buildings`), so the model has total freedom; everything is priced by the diff-based accountant. Helpers: `wall(polyline, height_m)`, `regrade(polygon, dz_m)`, `permeable(polygon)`, `green_roof(building_ids)`, `bioswale(polyline, width_m)`, `storage(x, y, volume_m3, intake_m3s)`, `pump(x, y, m3s, outfall='bay')`, `tunnel(path_xy, m3s, outfall='bay')`, `upgrade_inlets(polygon|ids, factor)`, `add_inlets(points)`, `floodproof(building_ids, height_m)`, `prune(tree_ids)`, `remove_trees(ids)`, `replace_trees(ids, species)`, `brace(tree_ids)`, `wind_screen(polyline, height_m, porosity)`, `sandbags(polyline, height_m)`, `log(note)`.

## Run pipeline (`sim/run.py`, owned by lead, built from the above)

`run_storm(storm_name, plan_code=None, label) -> runs/<run_id>/` containing `result.json` (ledger, plan cost, metrics, validation scores), `frames/` (binary for the web: see below), `video.mp4` (top-down + oblique render for the model to watch).

## Web playback format (`runs/<run_id>/frames/`)

- `manifest.json`: `{nx, ny, dx_frame (8 m), n_frames, t: [...], gust: [...], rain: [...], files...}`
- `depth.u16`: n_frames × ny8 × nx8 uint16, water depth in mm (8 m cells)
- `trees.json`: `[{id, fail_t (s or null), mode: snap|uproot, fall_dir_deg}]`
- `debris.f32`: n_frames × n_debris × 3 (x, y, z UTM/metres) + `debris.json` meta
- `cars.f32`: n_frames × n_cars × 4 (x, y, z, yaw)
- `wind_slice.f32`: ny8 × nx8 × 2 at 10 m (u, v) at reference speed (scale by gust)
- `damage.json`: `{by_building: {id: usd}, total_usd, by_cause}`

## Server (`server/`, owned by agent/server)

FastAPI on port 8765: serves `web/dist`, `data/grid` static (`/data/...`), `runs/` (`/runs/...`), `POST /api/run {storm, plan_code?, label}`, `POST /api/optimize {model: gemini-4-argon|gemini-3.8-flash, budget_usd, storm}` → job id, `GET /api/jobs/<id>/events` (SSE: thoughts, code, tool calls, run results), `GET /api/runs`.
