# Building inventory and collision grid

`python -m backend.risk_assets` rebuilds `data/risk/assets.json`. The existing
building IDs, order and geometry are preserved. `backend/building_inventory.py`
ports the StormSim building derivation; its replacement-cost and contents tables,
including the original source descriptions, are in
`backend/reference/building_inventory.json`.

The full DataSF footprint record is joined by `sf16_bldgid`. Assessor points join
the containing footprint, then the nearest footprint within 25 m. Occupancy may
use the nearest parcel within 60 m when no parcel joins. Property area weights
occupancy; joined stories pass the original plausibility rules. The derivation
also retains the original known-tower overrides, floor-area replacement values,
contents ratios, first-floor elevations, median construction year, assessed
improvements, parcel count and ground-retail flag. Replacement value is distinct
from the assessor's taxable improvement value.

Both DataSF downloads use the existing bounded fetch/cache path. The assessor
cut has a 24 MB cap because the supplied 2023 cut exceeds the default 12 MB.
The output records source URLs, SHA-256 hashes and coverage counts. A checkout
without these ignored downloads refetches them when the inventory is built.

## Coverage and missing data

The supplied Embarcadero cut has 1,568 existing buildings: 1,463 full-footprint
matches, 1,242 buildings with joined assessor parcels and 105 unmatched full
records. All 1,568 receive positive structure and contents replacement values.
The unmatched records retain their existing heights, use the original occupancy
and floor-area fallbacks, and are marked `existing_height_fallback` in
`inventory_basis`. The narrower full-footprint query does not contain every
building in the original inventory.

Missing source values stay explicit: 372 construction years and 15 use codes
are null. A nearest-occupancy parcel does not supply stories, year or assessed
values. Ground elevation uses `gnd_min_m`; where unavailable it uses the host
terrain median within the footprint, or the nearest terrain cell for a footprint
smaller than a terrain cell, and is marked `host_dem_ground`. That fallback uses
the host 12 m DEM rather than StormSim's separate 4 m DEM.

`evidence/building-reference-parity.json` compares the unmodified reference
building loop against all 1,463 matching buildings: all 15 requested fields
match. `evidence/building-inventory-readback.json` records the preserved geometry
hash, coverage and actual collision grid.

## Collision and water sampling

`backend/destruction_adapter.py` supplies the enriched fields directly to the
unchanged destruction engine. Building IDs inside that engine remain stable row
indices; `source_id` retains the original DataSF ID.

The collision grid is EPSG:32610, 4 m, with rows increasing northward. Footprints
are rasterized with taller buildings last; a tiny footprint receives its
representative-point cell only when that cell is free. The engine uses this grid
for building and facade hits, tall-building neighbourhoods, falling-object road
blocks and adjacent flood sampling. The roadway mask is non-building land at
least 6 m from a rasterized building, measured on the 4 m grid.

The real grid has 616 rows and 439 columns. The saved water grid stays 205 by 146
at 12 m. One precomputed nearest-host-cell index per collision cell maps depth
and peak-time summaries into the engine's grid. Wind still samples the original
host field directly. No additional water or wind simulation is introduced.
Flood structural collapse remains unavailable because the saved water files do
not contain velocities. The adapter does not fabricate them.

## Rebuilding saved exports

`python -m scripts.rebuild_building_destruction` rebuilds the active scenario
from its saved `flood.bin` and every recorded plan in `demo6` and `demo6check`,
including control, the recorded heuristic, greedy and every scored round in
each plan folder. The plan exports rerun the primary water case using the
recorded forcing and actions, matching the existing layer export's primary-case
selection. They do not call the planner or rewrite the saved plans.

The optional `--baseline data/building-upgrade` compares an adapter and inventory
captured before this change against the new code on identical water frames.
`evidence/building-destruction-comparison.json` contains per-plan damage states,
all four building dollar lines, water-input hashes and output hashes. The
capture directory and generated inventory/exports are ignored data artifacts.

Run the regression suite with the requested Python interpreter using
`-m unittest discover -s tests -v`. The new cases cover exact derived values,
25 m/60 m join boundaries, invalid stories, missing records, known towers,
large-download caching, adapter pricing, water mapping, overlap priority, tiny
footprints, and a tree striking a narrow building missed by the 12 m grid.

## Acceptance results

- Full Python suite: `Ran 110 tests in 8.468s` / `OK`.
- `npm run build` and `npm run typecheck`: exit 0.
- Exact reference comparison: 1,463 buildings × 15 fields, zero differences.
- All 25 exports were read back with matching hashes, 31 frames and 4,098 trees.
  The active saved scenario is `37351e0c297d`; its water-file hash is unchanged.
- Every recorded plan is covered: six plans in each of four plan folders.
  All 24 remain at DS1/DS2/DS3/DS4 = 0/0/0/0, with each of the four building
  dollar lines equal to $0 before and after.

For the active saved scenario:

| Result | Before | After |
| --- | ---: | ---: |
| DS1 / DS2 / DS3 / DS4 | 17 / 0 / 0 / 0 | 18 / 1 / 0 / 0 |
| Roof cover | $65,701.39 | $86,945.81 |
| Structure | $0.00 | $0.00 |
| Contents | Unpriced | $499,858.23 |
| Debris glazing | $116,800.21 | $108,984.47 |

Detailed per-plan before/after results are in
`evidence/building-destruction-comparison.json`; final readback is in
`evidence/building-final-readback.json`. No destruction-engine source changed.
Changes remain uncommitted; the reviewer should exclude `.port-src/` and the
workspace-local `.helios/` continuity ledger from the commit.
