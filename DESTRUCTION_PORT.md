# StormSim port: reviewer handoff

Worktree: `mm3`, branch `marc-destruction-engine`, base `481e6de`. Changes are uncommitted. The supplied `.port-src/` is unchanged and must not be committed.

Implementation and local checks are finished. **Rendered visual acceptance is unverified:** no in-app browser was available, and Chrome computer use was not approved. The state-machine test uses constructor doubles and is explicitly not a screenshot or an ArcGIS rendering test.

## Feature mapping

| Previous feature | Engine / inventory ownership | Export / 3D / analysis ownership |
| --- | --- | --- |
| Fixed city seed, paired capacity draws, determinism | `backend/destruction.py`: `CITY_SEED`, `_rng`, `run_destruction` | Same results feed both publishers and both views |
| DS1–DS4, roof design-ratio correction | `run_destruction`, `WindModel.design_ratio`; original parameter tables | `backend/destruction_web.py`: `buildings`; `loop/destruction-layer.js`: `buildingSym`; `frontend/destruction.ts`: DS fills |
| Roof cover, structure and contents costs | Original roof-area pricing; missing assessor values explicitly unpriced | `ledger_total_usd` is the priced subtotal; unpriced causes identified in both views |
| Quadratic-drag roof-debris flights and facade hits | Original debris flight integration and glazing losses | Original positional `debris`; 3D flight paths; analysis yellow paths and impact dots |
| Snap/uproot trees, anchorage scatter, decay, pruning, calibration | Original `tree_failures`, unchanged archived scales | Original failure rows; all-tree file includes every risk tree, same genus/look-alike mapping |
| Every standing tree; failing trees tilt toward fall bearing | Risk tree DBH metres converted to centimetres; same height/crown/species | 3D genus renderer with Esri realistic models, height/crown size, heading and tilt; `TREE_TILT=-88`, forward-playback animation |
| Pole fragility, domino, heavy-tree pull | Original pole and span cascade | Pole topplers; red X in analysis |
| Power spans, fused sections, customer outage, ICE losses | Original service reach, line outages, customer classes, duration/interpolation | Original `spans`, `outage`, `customers_t`; violet de-energised spans/buildings; down spans draped red in 3D |
| Service drops torn by tree strikes | Original `svc_k` and outage cause | Exported per-building outage; fed into multihazard |
| Muni OCS ≤30 m spans and feeder sections | `risk_assets.build_destruction_inventory`; original OCS cascade | `ocs`; cyan intact, violet unpowered, red down in both views |
| Collapse debris downing poles, power spans and OCS | Original collapse reach and first-event logic | DS4 mesh cut, pancake slab, rubble chunks, dust and persistent red volume outline |
| Blocked-road restoration delay | Original `road_blocks` and delay radius/cap | Red X callouts and analysis markers, feed entries |
| Trees into buildings | Original raster fall-line intersection | Magenta outline and triangle in 3D; magenta diamond in analysis; feeds in both |
| Streetlights and lamp heads: wind/tree, flood, outage | Original fragilities and dark/flood timing | 3D poles with moving lamp heads and on/off states; white fallen-light symbols in analysis |
| Signal heads, mast arms, flooded cabinets, dark signals | Original point-asset models | 3D signal mast/head states; orange damage triangles and violet dark rings in analysis |
| Shelters and masts | Original failure models, mapped inventory | 3D shelter/smashed state and mast toppling; magenta shelter squares and mast marks in analysis |
| Falling objects onto cars | Original engine path retained; host passes empty inventory | Cleanly skipped, explicitly noted in output |
| Events, monotone timelines, summaries, sourced ledger | Original event ordering, cumulative arrays and pricing | Original keys retained by exporter; feed, counters and cost labels |
| Worst-block / power-cascade cameras, Hide | Camera targeting and layer/filter visibility in `loop/destruction-layer.js` | Buttons inside the existing `#panel`; Hide restores the mesh and hides standing trees too |
| One weather timeline and play control | `destruction_adapter.Inputs.run`: the actual saved frame times, 120 s interval, one-hour envelope | 3D `setFrame(k)` calls `setTime(k*120, forwardPlayback)`; existing `#toggle` and `#scrub`; analysis existing `#time` and play |
| Per-plan and scenario delivery | `scenarios.calculate`, `loop.layers.DestructionJudge.write_layers` | `destruction.json` and tree file beside flood.bin; per-label files for loop; API serves both scenario files |

## Integration boundaries

- `backend/destruction_adapter.py` converts grid lon/lat to UTM once and uses a nearest-cell tree. Rows retain the host's southward ordering. Building IDs and heights are rasterized from full footprint geometry, including courtyards and multipolygon parts. Roadway cells are non-building/non-ocean land at least 6 m from rasterized buildings.
- Wind amplification is `hypot(world.u, world.v) / p.wind_mps`, multiplied by the original exposure-B height profile through `speedup`. The 2-D field is solid inside building cells, so a point at or above the local roof (4 m collision height, else the tallest roof in its 12 m cell) gets the undisturbed speed-up 1, which is how every building is loaded at roof height; a point inside a building cell below its roof reads the nearest open (street) cell. A zero mean uses a finite unit amplification; a zero gust still produces zero load. The host `potential_wind` is used for callers without a prepared field. No other wind solver runs.
- Gusts are `p.gust_mps * (.6 + .4*sin(pi*t/3600))`, direction is `p.wind_from_deg`, and rain is `p.rain_mm_h`. Water summaries come from the supplied depth frames, never from another water simulation.
- `backend/risk_assets.py` uses the existing fetch/cache path for `osm-overhead.json`, `osm-street-assets.json` and the exact DataSF `within_box(shape,37.808,-122.406,37.786,-122.386)` query. Source URLs, snapshot hashes and licences remain in the inventory.
- `multihazard.Model` no longer samples its own tree or structure failure thresholds, computes its own fall bearings, or determines conductor strikes. It projects the engine's timestamps/IDs into its existing graph and metrics. DS3+ triggers its existing partial-collapse/rubble consequence. Glass, loose roof objects, entrance flooding, substations, work budgets, finite backup and route closure logic remain there.
- Executed plan actions map explicitly: `prune_tree` → `tree_actions[id]='prune'`; `brace_structure` → `roof_retrofit`; `backup_power` → served building; `close_route` → closed graph route. Deferred/skipped work is not credited by the model.
- The analysis draws the source symbology **after** the existing weather raster in the same canvas, so the flood/wind overlay cannot cover the destruction symbols. Coordinates are projected through the actual map view; no extra view or playback control is created.
- Replaying saved loop plans accepts their archived candidates, avoiding a fresh planning-provider call just to rebuild layers. The usual Judge initialization is unchanged when that optional replay context is absent.

## Deliberate input differences / limits

1. This is the requested Embarcadero bounding box and its inventory, not the previous larger city's inventory. Numeric engine IDs are stable row indices with original IDs retained by the adapter. Buildings infer floors/class from height; no occupancy survey or assessor values are fabricated.
2. No saved water velocities exist. Flood structural collapse is skipped and noted, while depth-dependent cabinet/light flooding and rain-dependent tree saturation remain active.
3. There are no cars. The engine's car path is retained but receives an empty list.
4. All three parameter JSON files are byte copies of the supplied references. The historical 2022/311 calibration replay and old cached LBM test cannot be repeated against a different city/weather engine. Tests instead verify the retained calibration constants and the actual host wind/grid boundary.
5. Empty overhead inventories and fully undergrounded lines now return cleanly; the reference raises an empty-argmin/index error in those cases. This is necessary for host fixtures and the requested clean empty-asset behavior.
6. Missing monetary values are explicit `null`/unpriced, with any known partial amount retained as `priced_usd`. No missing amount silently becomes a quoted zero-dollar loss. The total remains the sum of priced amounts.
7. Original positional arrays and keys are retained. Additive `masts`, `unpriced` and `footprint_rings` fields carry mapped masts, missing-price labels, courtyards and multipolygon geometry. The supplied old exporter/layer omitted masts despite the engine supporting them; this port includes them as requested.
8. Existing page tokens replace the standalone card CSS. Its separate scenario selector, play button and slider are removed as requested. The remaining 3D symbols, motion, feed and cameras are ported from the source.
9. **Actual ArcGIS rendering, responsive layout, camera flights and visible animation remain unverified.** The browser availability/approval block must be resolved to complete that acceptance gate.

## Evidence

- `evidence/port-tests-final.txt`: **98 tests, 7.977 s, OK**, including the final transport-preservation and plan-hash checks.
- `evidence/port-reference-parity.json`: seven direct supplied-source comparisons, identical physics, events, times, summaries and priced totals after normalizing only explicit missing-price metadata. The separate empty-inventory correction is covered by the unit suite.
- `evidence/port-layer-state-final.txt`: constructor-double contract test on actual exported data: **4,098 standing trees, 705 failed trees, 61 collapsed buildings**. It checks forward animation state, rewind, clockwise mesh rings, hide/show and clearing a plan. It does not prove real ArcGIS rendering.
- `evidence/port-real-scenario.json`: actual 80 m/s gust / 40 m/s mean / 270° host-weather scenario, 31 saved frames: **705 failed trees, 147 damaged buildings, 72 downed OCS spans, 133 fallen lights, 86 damaged signals, 8 smashed shelters, 805 road blocks and 65 tree strikes**. This is a labeled stress scenario, not a forecast.
- `evidence/port-publication.json`: eight artifact reads (active and stress scenarios) returned HTTP 200 through the actual API; control, greedy and all three recorded rounds each exported 31 frames, 4,098 street trees and all 1,568 footprint records.
- `evidence/port-loop-export-final.txt`: final replay exports the control, greedy and every scored round; each uses its own simulation. The earlier failure is retained in `port-loop-export.txt`; it identified the unnecessary fresh planning-context dependency.
- `evidence/port-build-final.txt` and `evidence/port-typecheck-final.txt`: successful frontend build and typecheck after the last overlay change.

## Tests changed from the rejected implementation

- `test_destruction_replay.py`: the fixed 90° bearing now compares against the engine's seeded fall bearing; the old 3 m contact boundary now tests crown radius plus the original 1.5 m allowance; the nearer intact-line case also expects the restored service-drop outage; artifact assertions use the full positional scene format instead of the reduced referenced-geometry object. Determinism, no water stepping/mutation, service reach, backup, trolley/power separation and run/replay parity remain checked.
- `test_multihazard.py`: the wind-response probe uses 80 m/s and the marginal-bracing search extends to 80 m/s because the original roof fragility replaces the rejected low-capacity model. Its threshold is not weakened in production code.
- `test_destruction_engine.py`: 13 unittest ports cover calm, stronger storms, determinism, cascades, undergrounding, removal, sorted events, monotone timelines, downwind debris, ledger merge/provenance, incidents, the host grid/wind/export boundary and action mapping.
- `tests/destruction_layer_state.mjs`: run separately with Node; state-machine acceptance only.

To finish visual acceptance, run the normal app and loop servers from this worktree, open `/analysis` against a generated scenario and `/3d?run=demo6&model=argon`, then check populated playback/rewind, plan switches, Hide, both cameras and narrow/wide panel layout. Do not treat these instructions as completed visual evidence.

## Files changed

- Engine and inputs: `backend/destruction.py`, `backend/destruction_adapter.py`, `backend/risk_assets.py`, `backend/reference/destruction_params.json`, `backend/reference/wind_tree_params.json`, `backend/reference/damage_functions.json`.
- Shared model and publication: `backend/multihazard.py`, `backend/destruction_web.py`, `backend/scenarios.py`, `backend/app.py`, `loop/layers.py`, `loop/judge.py`.
- 3D: `loop/destruction-layer.js`, `loop/viewer3d.html`, `loop/serve.py`.
- Analysis: `frontend/destruction.ts`, `frontend/analysis.ts`, `frontend/analysis.html`, `frontend/analysis.css`.
- Tests: `tests/test_destruction_engine.py`, `tests/test_destruction_replay.py`, `tests/test_multihazard.py`, `tests/destruction_layer_state.mjs`.
- Handoff and evidence: this report, `evidence/check_reference_parity.py`, and the `evidence/port-*` receipts. Existing unrelated evidence was preserved. Build outputs and generated datasets remain in their existing ignored directories.

The active saved scenario was also re-exported from its existing flood.bin, without rerunning or replacing its water frames. Legacy reduced exports are detected and display a rebuild message instead of crashing either view.

Continuity ledger: `stormsim-port-20261025`. Source/local acceptance is proven; the rendered-views gate remains blocked and is not marked complete.
