# Destruction engine (`sim/destruction.py`)

What breaks above ground, when, why, and what it costs. Deterministic, ~1 s per storm on a laptop.

```python
from sim.storm import load_storm
from sim.destruction import run_destruction, merge_ledger, write_web
res = run_destruction(load_storm("elnino_ar_2026"), plan=city, wind=wind_field, water=water_result, tree_result=trees_out)
merge_ledger(ledger, res)                 # adds destruction_* causes to sim/damage.py's ledger, no double counting
write_web(res, "runs/<id>/frames")         # frames/destruction.json for the viewer
```

`physics_inputs(storm)` gives the team's physics: the LBM wind field (`sim/wind.py`, cached 16 m lattice) and
`sim/trees.py` failures with fall directions. The CLI and the viewer export use it by default; `--fallback` skips it.

CLI: `python3 -m sim.destruction --storm elnino_ar_2026 --png [--gust-scale 1.6] [--fallback]` writes `runs/destruction_<storm>/`
(`destruction_result.json`, `frames/destruction.json`, `destruction.png` top-down render).

## What it simulates

| Asset (inventory) | Failure modes | Cascades |
| --- | --- | --- |
| 11,782 buildings (`buildings.json`) | Hazus-hurricane DS1-DS4 from local roof-height gust (roof cover, deck, structure); flood collapse from depth x velocity | roof debris flies downwind (closed-form drag flight) and breaks windows of the buildings it hits |
| 23,564 street trees (`trees.json`) | snap / uproot (built-in model, or `sim/trees.py` output when passed) | fall line hits wires, poles, streetlights, signals, shelters |
| 1,268 power poles, 1,173 spans, 91 lines (OSM, PG&E) | published wood-pole wind fragility; heavy trunks on a span pull the nearer pole down | pole-to-pole domino; spans down -> fused line section de-energised -> customers out (LBNL ICE cost) -> signals and streetlights near it go dark |
| 95 km Muni overhead contact wire (OSM trolley_wire + electrified tram/light rail) | trees on the 5.8 m wire | feeder section (~800 m) de-energised: route-km out |
| 1,046 streetlights (OSM) | wind (AASHTO LTS) + degraded bases, heavy trees, flooded handholes | fall onto parked cars |
| 487 traffic signals (SFMTA, DataSF ybh5-27n2) | heads torn, mast arms down, flooded controller cabinets | dark when their overhead feed is out |
| 246 bus shelters, 38 masts (OSM) | wind, trees | masts fall onto cars |

Inventory: `python3 -m sim.assets` rebuilds `data/grid/assets.json` from the archived snapshots in `data/reference/assets/`.

## Plan hooks (duck-typed on the CityEditor / plan dict)

`tree_actions` (prune / brace / remove / replace, already in `CityEditor`), `undergrounded_lines` (OSM line ids),
`hardened_poles` (pole ids, capacity x1.5), `roof_retrofit` (building ids, DS medians x1.15), `removed_cars`.
Capacities are drawn once per asset from a fixed city seed (common random numbers), so a plan-vs-baseline
difference is the plan's effect only.

## Calibration and honesty

- Parameters and sources: `data/reference/destruction_params.json`; assumed values say so.
- Trees (fallback model): calibrated so the 31.12.2022 replay fails 9 trees in the tile = 9 SF 311 tree reports
  there. That anchor implies sound trees almost never fail (only decayed / poorly anchored ones), which is stricter
  than the Virot 42 m/s median; 311 undercounts, so this is a low estimate.
- Local gust = storm reference gust x LBM speed amplification at the asset's height (same convention as
  `sim/trees.py`). The ASCE 7 exposure-B profile x tall-building proxy is only the fallback when no field is passed.
- Viewer: `python3 -m sim.destruction_web` exports `data/web/destruction_*.json` for `destruction-layer.js`.
- Counted but not priced (no verified repair unit cost found): power spans, trolley wire, signal heads and mast arms, masts.
