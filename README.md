# Embarcadero storm prototype

Python computes flooding and a diagnostic wind field from public geographic data.
TypeScript draws the resulting flood surface and animated wind trails directly
inside the real ArcGIS 3D scene. The screen contains only the scene, source
attribution, and a transient loading/error message. Pan, orbit and zoom use the
map's ordinary mouse/touch gestures.

This checkout is isolated on `codex/embarcadero-storm`; it does not modify the
original checkout used by Claude. The original viewer remains on port 5173.

## Run

Requires Python 3.11+ and Node 22.18+ (tested with Python 3.14 and Node 26).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
npm run prepare:data
npm run build
npm start
```

Open http://localhost:5174. The server listens only on loopback. It has no cloud
deployment, API keys, new billing configuration, or connection to customer data.
Internet access is needed for the map and public data sources. Data preparation
caches bounded source responses locally in `data/raw/`; outputs are in `data/`.
Cached data is intentionally not added to Git. Delete only this checkout's
`data/` directory if a deliberate fresh source download is needed.

## Actual source data

The area is approximately -122.406 to -122.386 longitude, 37.786 to 37.808
latitude. The numerical grid is 146 × 205 at 12 metres in UTM zone 10N.

| Input | Source and use |
| --- | --- |
| Bare-earth elevation | [USGS 3DEP elevation service](https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer). Bounded float32 GeoTIFF, bilinear-resampled to 12 m. The mosaic may mix acquisition dates; no claim that this cut uses only the 2023 flight. |
| Building obstacles and heights | [DataSF ynuv-fyni](https://data.sf.gov/Geographic-Locations-and-Boundaries/Building-Footprints/ynuv-fyni). 1,603 footprints; older source geometry, with valid height values required. |
| Drain positions | [DataSF jtgq-b7c5](https://data.sf.gov/City-Infrastructure/Stormwater-inlets-drains-and-catch-basins/jtgq-b7c5). 1,009 records in the cut; 1,003 map into modeled land cells. The dataset does not represent every drain. |
| Rainfall depth | [NOAA Atlas 14](https://hdsc.nws.noaa.gov/cgi-bin/hdsc/new/fe_text_mean.csv?lat=37.7955&lon=-122.3947&data=depth&units=metric&series=pds). 32 mm for the one-hour, 100-year event at the sample coordinate. |
| Wind forcing | [NOAA CO-OPS](https://api.tidesandcurrents.noaa.gov/api/dev/) station 9414290, Jan 4–5, 2023: selected peak gust 19.3 m/s; corresponding mean 11.3 m/s from 138° at 2023-01-05 01:42 UTC. |
| Coastal baseline | Same NOAA station/date window: maximum verified water level 2.368 m NAVD88 at 2023-01-05 17:54 UTC. |
| Visible city | Public PLW/Esri San Francisco textured 3D buildings and Esri World Imagery/Elevation. |

`data/manifest.json` contains exact source request URLs, output time, SHA-256
hashes of downloaded inputs, units, scenario assumptions and numerical
water-budget metrics. Units in the numerical solver are metres, seconds and m³.

## What the first version simulates

- Positive, conservative finite-volume surface-water transport using a Manning
  diffusive-wave approximation. Roof rain routes to the nearest open ground cell.
  Building cells are impermeable obstacles.
- A one-hour scenario with uniform Atlas 14 design rain, 90% runoff, assumed
  Manning roughness 0.035, and closed non-coastal domain boundaries.
- The coastal baseline is the observed high water level, with a **hypothetical
  additional 1.0 m water-level rise** ramping over 30 minutes.
- Real inlet locations, with **assumed** 6 L/s capacity per inlet, 65% blockage,
  and a simplified tidal-locking rule. No pipe network or backflow is modeled.
- A 2-D incompressible potential-flow approximation around building footprints,
  driven by the observed wind direction and mean speed. Trails are illustrative
  tracers, played at twice real-time wind advection and blended toward free-stream
  wind above the buildings. An illustrative gust envelope varies between the
  observed mean and peak gust; it does not reconstruct sub-minute weather.
  This is not turbulence or structural-load CFD.

This is a compound stress-test scenario, **not a reconstruction of one observed
storm, a forecast, or a claim of a joint 100-year event**. Wind and coastal maxima
occurred at different times. Rainfall and coastal rise are combined deliberately.
The finite grid does not resolve curb heights, doorsteps, sub-grid drainage,
individual pipe condition, waves or wave overtopping. No building damage, safety
or evacuation prediction is made.

## 3D alignment and playback

The physics terrain and coastal forcing use NAVD88. The visible map has its own
terrain/elevation datum, so the renderer samples that actual terrain and applies
the simulated **relative depth** above it, instead of mixing absolute heights.
The displayed surface can therefore vary slightly from the solver's absolute
water-surface elevation. Vertex elevation has a 12 cm rendering clearance plus
small illustrative ripples to prevent z-fighting. Hue/opacity follow water depth;
building interiors and the open-water background are excluded from the overlay.

The flood timeline moves forward at 15 simulated seconds per real second. It
starts at minute 35 so flooding is immediately visible, reaches minute 60, then
restarts at minute zero. Wind tracer motion is continuous. Animation pauses when
the document is hidden. Browser developer access is available at `window.storm`;
the API exposes the manifest and two bounded binary arrays.

## Checks

```sh
npm run typecheck
npm run test:physics
```

Tests cover closed-domain conservation, nonnegative depth, a lake at rest,
coastal/rain/drain volume accounting, impermeable buildings, and unobstructed
wind direction. The generated scenario separately checks its own full water
budget and fails preparation if it is inconsistent.

## Files

- `backend/prepare.py`: public data acquisition, rasterization and scenario output.
- `backend/simulation.py`: water transport and diagnostic wind calculation.
- `backend/app.py`: loopback Python HTTP/API server.
- `frontend/main.ts`: real 3D scene and terrain alignment.
- `frontend/renderer.ts`: flood mesh and wind-trail RenderNode.
- `frontend/types.ts`: typed frontend/API contracts.
- `tests/test_simulation.py`: numerical boundary and conservation checks.

Next fidelity improvements would require a usable, connected sewer network with
verified capacities and elevations; better coastal boundary geography; validation
against observed flood extents; and a dedicated wind solver if local turbulence
or structural loading becomes part of the scope.

## Separate analysis and 3D views

- `/` is the 3D view with only the view switch and source attribution.
- `/analysis` is a top-down analytical map with flood depth, wind speed and
  rainfall fields, wind arrows, a cell-value readout and a shared time slider.
- Set rain intensity, mean/gust wind, wind direction, added coastal rise, inlet
  capacity and blockage in Analysis, then select **Run simulation**. Advanced
  surface assumptions expose runoff fraction and Manning roughness.
- Inputs are validated by Python. The old result remains visible during a run;
  failed runs preserve it and show the error. The latest applied parameters and
  playback state persist locally in `data/scenario-state.json`.
- Both routes poll the same scenario state. A finished run updates both views
  without resetting the 3D camera. Time, playback speed, and play/pause also sync
  between tabs. The 2D field selection is local to the analytical view.
- The selected rain intensity is spatially uniform over the modeled land area;
  it is not a radar rainfall map. Wind maps show the diagnostic mean field;
  illustrative 3D gust trails range up to the configured gust speed.
- Up to four generated runs are retained in `data/scenarios/`, in addition to
  the baseline. Only generated runs owned by this checkout are pruned.

The Python API adds `GET /api/state`, `POST /api/scenarios`,
`POST /api/playback`, and versioned result files under
`/api/results/{scenario_id}/`. Scenario computation runs in one background
worker. The frontend includes `analysis.ts` for the 2D map and parameter editor
and `session.ts` for shared scenario/playback access. Design tokens in
`frontend/tokens.css` are copied from the owner's UI/UX archive.
