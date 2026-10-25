# StormSim SF: El Niño storm vs. San Francisco

Problem Statement Two. Argon builds and hardens a physically simulated 3D San Francisco against an incoming atmospheric-river storm. The sim measures the dollar damage; Argon (vs. Gemini Flash) spends a budget to cut it.

## Demo (3 min)

1. **0:00** "It's coming." Live NWS alerts for SF (a Coastal Flood Advisory is active today, to 29.10), plus the storm forcing: rain hyetograph and wind gusts.
2. **0:20** Storm hits the 3D city. Rain sheets down; water runs off the real lidar terrain and fills the Mission Creek basin (17th & Folsom); storm drains choke; gusts accelerate through downtown canyons; street trees on saturated soil snap, uproot and tumble into cars and windows.
3. **1:00** Damage ledger: $X of building, contents, vehicle and tree damage. Checked against the real 31.12.2022 storm: the 311 flood and tree-down reports and SFPUC's own 100-year flood map overlay the sim.
4. **1:20** "Argon, you have $50M." Argon writes the plan as code (anything it wants: cisterns, a relief tunnel, permeable streets, bioswales, gates, re-graded streets, tree species swaps, pruning, wind baffles, ...) and places it in the city. It watches the replay video and patches its plan where water still gets in.
5. **2:20** Same storm again. Side by side: no plan, Gemini Flash's plan, and Argon's plan. Spend, damage avoided, benefit–cost ratio (FEMA BCA ≥ 1 is the real federal grant test).
6. **2:50** The planner approves the plan. Out comes a cited report.

## Data (verified 25.10.2026 unless marked; tile S37.758 N37.795 W-122.425 E-122.385 in data/raw via scripts/fetch_data.sh: 11,782 buildings, 23,564 street trees, 4,988 drain inlets, 40,568 assessor parcels, 900 storm 311 reports)

| Layer | Source | Notes |
| --- | --- | --- |
| Terrain | USGS 3DEP lidar (`elevation.nationalmap.gov` ImageServer) | downloaded: 2000×2400 at ~1.75 m, UTM 10N |
| Buildings | DataSF Building Footprints `ynuv-fyni` | lidar heights (p2010 z min/max, height stats) |
| Street trees | DataSF Street Tree Inventory `tkzw-k3nq` | 144,522 trees: species, DBH, plot size, lat/lon |
| Canopy | DataSF Urban Tree Canopy `ni2e-vpbg` | park/private trees |
| Drainage | DataSF Stormwater inlets, drains, catch basins `jtgq-b7c5` | 23,878 inlets |
| Flood truth | SFPUC 100-Year Storm Flood Risk Zone `jzu3-4yxp`; Flood Elevations Dec 2024 `wppz-u2hi`; + sea-level-rise variants `esku-ejgv`, `6ann-usi8` | SFPUC's own 1D-2D model output: calibration target |
| Damage truth | 311 Cases `vw6y-z8j6`, 31.12.2022: 567 flooding, 118 sewage backups, 71 damaged trees | SF Chronicle: winter storms ≥ $46M SF damage; $632M flood investment |
| Property value | Assessor secured roll `wv5m-vpq2` | building + land value per parcel |
| Live alerts | NWS API `api.weather.gov/alerts` | Coastal Flood Advisory active now |
| Forecast forcing | NOAA HRRR 3 km hourly on AWS (`noaa-hrrr-bdp-pds`, today's runs present): gusts, precip | Google WeatherNext 2/3 on BigQuery/Earth Engine needs an access request; ask the DeepMind team onsite |
| Design storm | NOAA Atlas 14 PFDS (100-yr 24-h depth for SF) | not yet fetched |
| Scenario | USGS ARkStorm (California megastorm scenario) | narrative + magnitudes |
| Tide | NOAA CO-OPS 9414290 SF | verified live |
| Damage functions | FEMA Hazus flood depth-damage curves (by occupancy), Hazus wind; FEMA BCA Toolkit | standard US planner method |
| Tree failure | Virot et al. 2016 (Phys. Rev. E): trunks break near ~42 m/s across species; uprooting falls sharply on saturated soil | cite in UI |
| Costs | Deep Research agent → cited unit costs (SFPUC green-infrastructure, tunnels, cisterns, permeable pavement) | locked into a cost table |

## Physics engine

No off-the-shelf engine does city-scale wind + rain + drainage + flying debris, so we combine the right solver for each, all on the GPU in the browser (WebGPU), running live on the MacBook. The box has no GPU; Verda for heavy runs.

| Phenomenon | Model | Why |
| --- | --- | --- |
| Wind | 3D lattice-Boltzmann (D3Q19 + Smagorinsky LES) on voxelised buildings, log-law inflow from HRRR gusts | real CFD; gives street-canyon acceleration and facade pressures (SF's Planning Code §148 wind rules make this planner-native) |
| Rain, runoff, surface flooding | 2D shallow-water equations (finite-volume, GPU) on the 1 m lidar DEM | same physics as LISFLOOD-FP / HEC-RAS 2D / SFPUC's models |
| Storm drains + sewer | each real inlet is a capacity-limited sink into a lumped combined-sewer network; overflow surcharges back up | captures "drains choke" + sewage backups; can upgrade to EPA SWMM (pyswmm) coupling |
| Soil | infiltration (Green-Ampt) by land cover; saturation tracked per cell | wet soil drives tree uprooting |
| Trees | each real street tree: bending moment from LBM wind drag vs. species/DBH strength → snap or uproot → becomes a rigid body | trees actually fly |
| Debris, cars, glass | Jolt Physics (WASM, AAA-game engine) rigid bodies, pre-fractured glass/signage; drag from the LBM field, buoyancy from the water depth | flying/floating debris that hits things |
| Rendering | three.js WebGPU: water surface, rain particles, wind streaklines; Google Photorealistic 3D Tiles for the photoreal city if we have a Maps key, otherwise lidar buildings | the 3-min wow |

Later (earthquakes, towers falling): Genesis (MPM/FEM, runs on Apple GPU) or NVIDIA Newton/Warp on a Verda GPU, fed by USGS ShakeMap scenarios (Hayward/San Andreas).

## Damage model

Per building: Hazus depth-damage (structure + contents) × assessor value. Plus wind facade/glazing failures, tree-strike hits on cars/buildings (unit costs), and business interruption by flooded hours. Outputs a $ ledger by cause and by parcel, calibrated so the 31.12.2022 replay lands near the real hotspots and reports.

## How Argon improves the city

- **Full freedom:** Argon gets the whole city scene and a `city` API. It writes code that adds, removes or reshapes anything: geometry, terrain grading, pipes and tunnels, cisterns, green roofs, permeable surfaces, tree species, pruning, gates, wind baffles, timing (pre-storm actions). It can invent new intervention types and write their physics behaviour.
- **Its own loop:** it runs the storm in the Antigravity managed agent sandbox, watches the replay video and the per-parcel ledger, and iterates as long as it wants within the budget.
- **Locked, so the score means something:** the storm forcing, the core solvers, the damage functions and the cost table (every new item needs a cited cost). Same reason Etch measured its boards with plain code.
- **Baseline:** Gemini Flash (ID confirmed at kickoff: `gemini-3.8-flash` per the guide) gets the same budget, storm and API. We also run a "flat walls everywhere" baseline.
