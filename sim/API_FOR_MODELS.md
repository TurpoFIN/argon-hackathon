# StormSim SF: the `city` API for plans

Write Python that defines `def apply(city): ...`. The simulator runs it against a copy of the real city
(4 m grid over SoMa, Mission Bay, the Mission and downtown San Francisco), then runs the storm. The locked cost
accountant (`sim/costs.py`) prices everything from the physical changes you make, using only the cited unit
costs in `data/reference/unit_costs.json`. You never set a price. Same plan, same result, every time.

Allowed imports: `math`, `numpy` (already available as `np`), `itertools`, `functools`, `collections`.
No file or network access. `print()` output comes back to you in the log.

Before you run the storm, you can call `sim.costs.estimate(plan_code)`. It runs `apply` and returns the
itemised cost without simulating anything.

## Coordinates and units

- **Coordinates are LOCAL metres from the grid's south-west corner.** x runs east from 0 to 3548, y runs north
  from 0 to 4256. Bounds are lon -122.425..-122.385, lat 37.758..37.795; the bay is to the east and north-east.
  UTM 10N values (x > 100000) are also accepted. Grid cell (iy, ix) covers x = ix·4 .. ix·4+4, y = iy·4 .. iy·4+4.
- Polygons are `[[x, y], ...]` with 3 or more points. Polylines are `[[x, y], ...]` with 2 or more points.
- Elevations are m NAVD88. Heights are m above ground. Flows are m³/s, volumes m³, ksat mm/h.
- Times `t_s` are sim seconds from storm start; negative means before landfall (-72 h .. 0).
- The storms: `elnino_ar_2026` is the incoming El Niño atmospheric river. It is a NOAA Atlas 14 500-year 6-h
  core (97 mm in 6 h, 41 mm in the peak hour, a 144 mm/h burst), a 26 m/s gust from 200°, and a bay level of
  2.82 m NAVD88: a king tide plus 0.6 m of surge, above the 1983 record. `dec31_2022` replays the real New Year's
  Eve storm: 88 mm in 6 h, 18 m/s gusts and a 1.9 m tide.

## Look before you build (query helpers)

| call | returns |
| --- | --- |
| `city.low_points(n=10, min_sep_m=150)` | lowest open-ground cells `[{x, y, z}]`: underpasses and basins where water pools (Mission Creek and 17th & Folsom are known hotspots) |
| `city.elevation(x, y)` | ground z, m NAVD88 |
| `city.basin_of(x, y)` / `city.basins()` | sub-sewershed id / list of basin info |
| `city.buildings_in(polygon, occupancy=None)` | building ids. Occupancy is a Hazus prefix such as 'RES', 'COM', 'EDU1' |
| `city.building(id)` | dict: occupancy, floors, height_m, area_m2, value_structure, value_contents, first_floor_m, x_local, y_local |
| `city.trees_in(polygon, min_dbh_cm=0)` / `city.trees_near(x, y, r=50)` | street tree ids (`city.trees[i]` has species, dbh_cm, height_m) |
| `city.inlets_in(polygon)` | storm-drain inlet ids |
| `city.cars_in(polygon)` | parked car ids |
| `city.streets(name=None)` | `[{name, coords}]` street centrelines in local m, e.g. `city.streets('FOLSOM')` |
| `city.is_building(x, y)` | building id or -1 |
| `city.help()` | this document |

## Infrastructure helpers: each makes a real physical change, is priced, and is drawn in 3D

| helper | physics | priced as (unit_costs.json item) |
| --- | --- | --- |
| `wall(polyline, height_m, width_m=0.4, kind='floodwall'\|'deployable'\|'flipup')` | raises the crest of the 4 m cells under the line to ground + height | measured length × **measured** height. ≤1.2 m: `floodwall_low_m_per_m` ($524/m/m). ≥2.5 m: `floodwall_high_m_per_m` ($7,461/m/m), blended between. Deployable: `deployable_barrier_m` ($2,215/m). Flip-up gate: `flipup_barrier_m2` ($10k/m²) |
| `sandbags(polyline, height_m=0.3)` (≤1 m) | the same, temporary | USACE bag count (3H+9H²)/2 per ft × `sandbag` ($2.78/bag placed) |
| `berm(polyline, height_m, width_m=8)` | raised earth or street crown | earthworks |
| `regrade(polygon, dz_m=… or target_z=…)` | changes ground z (not under buildings) | `earthworks_m3` ($128/m³ of \|dz\|) + `repave_m2` on paved cells |
| `permeable(polygon, ksat_mm_h=12.7)` | paved cells infiltrate (EPA subgrade minimum 0.5 in/h) | `permeable_pavement_m2` ($112/m²) |
| `bioswale(polyline, width_m=2, depth_m=0.3)` | lowered pervious cells, 127 mm/h media (SFPUC) | `bioswale_m2` ($269/m²) |
| `green_roof(building_ids, retention_mm=25)` | roof retains the first 25 mm | `green_roof_m2` ($311/m² of roof) |
| `storage(x, y, volume_m3, intake_m3s=2)` | underground vault fills from surface water at (x, y) | `storage_m3` ($1,460/m³) |
| `pump(x, y, m3s)` | permanent pump station, surface water to the bay | `pump_station_m3s` ($1.79M per m³/s) |
| `tunnel(path_xy, m3s, depth_m=30)` | relief tunnel adds sewer conveyance to the bay along its path | per m, scaled with diameter D = √(4Q/(π·3 m/s)) (min 3 m) from `tunnel_m_ref` ($284k/m at D 9.9 m). SF context: Folsom Area Stormwater Improvement Project tunnel ≈1.2 km × 3.7 m |
| `upgrade_inlets(polygon\|ids, factor≤4)` | inlet capacity × factor | `inlet_upgrade` ($6,695 each) |
| `add_inlets(points, capacity_m3s=0.05)` | new catch basins | `inlet_new` ($6,695 each) |
| `floodproof(building_ids, height_m≤0.9)` | dry floodproofing: no water enters below the height (FEMA P-936 limit is 0.9 m) | `dry_floodproof_m2` ($1,001/m² footprint) |
| `prune(ids)` / `brace(ids)` | smaller crown area / stronger stem (sim/trees.py) | `tree_prune` ($286) / `tree_brace` ($1,495) |
| `remove_trees(ids)` / `replace_trees(ids, species='Lophostemon confertus')` | tree gone / young wind-firm tree | `tree_remove_per_cm_dbh` ($23/cm) + permit ($499 per 3 trees) (+ `tree_replace` $2,590) |
| `wind_screen(polyline, height_m=3, porosity=0.5)` | porous screen voxels in the wind LBM | `wind_screen_m2` ($116/m²) |

## City operations before the storm (each is priced)

| helper | effect | priced as |
| --- | --- | --- |
| `clear_drains(polygon\|ids)` | inlets lose their leaf clogging (`clog_factor` = 1.0). The baseline is HEC-22's 50% sag-grate clogging near trees | `drain_clear_inlet` ($95 per inlet) |
| `sewer_drawdown(basins='all', frac=0.0)` | SFPUC drains the storage/transport boxes beforehand (initial fill drops from 0.15 to frac) | `labour_crew_hr` × 24 h per basin |
| `tow_cars(polygon)` | parked cars removed from flood streets | `tow_car` ($652 per car, SFMTA fees) |
| `sandbag_giveaway(sites, bags)` | every 20 bags protects the nearest building's doorway to 0.3 m | `sandbag` per bag |
| `deploy_portable_pumps(points, m3s_each=0.368, hours=None)` | 12-in trailer pumps run from t_s to the end of the storm | `portable_pump_12in_hr` + operator `labour_crew_hr` |
| `pre_position(kind, x, y, n=1, hours=24)` | equipment staged (generator_100kw, generator_500kw, light_tower, bucket_truck, chipper, loader, dump_truck, tow_truck, vactor) | equipment-hours + operator hours |

### `city.action(name, description, effects, cost_items)` for anything else

`cost_items=[(item_id, qty), ...]` must use ids from `data/reference/unit_costs.json`. An unknown id is
rejected, and the error lists every valid id. Wherever the accountant can measure a quantity itself (inlets
cleared, cars towed), it ignores yours. Quantities it cannot measure are flagged as "declared quantity" in the
cost breakdown.

Supported `effects` levers:

- `inlet_clog_clear: polygon|ids`: clear inlets (same as `clear_drains`)
- `inlet_capacity_mult: {polygon|ids, factor}`: factor between 0.5 and 4
- `sewer_initial_storage_frac: {basin_id: frac}`: frac between 0 and 0.15
- `relocate_cars: polygon`: removes the cars in it
- `road_closed: polyline`
- `contents_mult: {building_ids|polygon, factor}`: moving contents up, de-energising. It can't go below 0.65,
  the USACE Day-curve maximum reduction with long warning
- `power_off: polygon`
- `people: {...}`: passed to the people simulation

Example:
`city.action('Close 13th St underpass', 'barricades + lights', effects={'road_closed': [[1960, 2440], [1975, 2500]]}, cost_items=[('labour_crew_hr', 48), ('light_tower_hr', 48), ('truck_flatbed_hr', 8)])`

Other menu items include `labour_tree_hr`, `labour_driver_hr`, `truck_dump_hr`, `truck_pickup_hr`,
`tow_truck_hr`, `vactor_truck_hr`, `sewer_jetter_hr`, `bucket_truck_hr`, `chipper_hr`, `loader_hr`,
`water_truck_hr`, `sweeper_hr`, `generator_*_hr`, `portable_pump_6in_hr`, `flood_shield_opening`,
`wet_floodproof_m2` and `shelter_room_night`.

## Emergency response: `city.response`

Shelters, evacuation, depots, supply runs, crews, road closures, alerts, generators and medical posts: see
**sim/RESPONSE_API.md**. Sandbag crews' buildings count as floodproofed to 0.3 m. Pump crews become pump
sinks. Response items are priced from `data/reference/response_costs.json` and appear in the same cost
breakdown.

## Raw arrays: total freedom

`city.z`, `city.ksat`, `city.manning`, `city.cover` (0 paved, 1 roof, 2 pervious, 3 bay) and `city.bldg_id` are
numpy (ny=1064, nx=887) arrays, indexed `[iy, ix]` with iy=0 at the south. Use `city.cell(x, y)` to get (iy, ix)
and `city.polygon_mask(poly)` / `city.polyline_cells(pl, width_m)` to select cells. `city.base[...]` holds the
originals. Every raw edit is priced:

- any z change outside a wall, sandbag or bioswale is earthworks;
- any ksat, cover or manning change is permeable surfacing per m²;
- edits to buildings (`bldg_id`) are rejected and ignored.

## 3D: everything is drawn

Every helper emits a plan_object: walls with a cross-section `profile`, pumps and tanks as sized points,
tunnels as polylines with depth. Draw your own designs too:

- `city.add_object({type, geometry: {kind, coords, z_base, height_m, width_m, profile}, style: {color, material}, label}, represents=item_id)`
- `city.custom_mesh(positions=[x,y,z,...], indices=[i,j,k,...], label, style, cost_basis=item_id)`, where z is above ground and local metres are used

`represents` / `cost_basis` is the id returned by the helper the model depicts (for example a gate design for a
`wall(..., kind='flipup')`). Visual-only objects cost nothing and are flagged.

## Example plan

```python
def apply(city):
    # 1. Mission Creek / 17th & Folsom basin: the lowest open ground
    hot = city.low_points(5)
    p = hot[0]
    box = [[p['x']-250, p['y']-250], [p['x']+250, p['y']-250], [p['x']+250, p['y']+250], [p['x']-250, p['y']+250]]
    # 2. Store and move water: a vault under the low point, a relief tunnel to the bay (east), a pump station
    vault = city.storage(p['x'], p['y'], volume_m3=30000, intake_m3s=4)
    city.tunnel([[p['x'], p['y']], [p['x']+900, p['y']+300], [3500, p['y']+400]], m3s=25)
    city.pump(p['x']+30, p['y'], m3s=3)
    # 3. Slow the runoff upstream: permeable parking lanes + bioswales along the streets that drain into the basin
    for s in city.streets('FOLSOM')[:6]:
        city.bioswale(s['coords'], width_m=2.0)
    city.permeable([[p['x']-600, p['y']+300], [p['x']-300, p['y']+300], [p['x']-300, p['y']+600], [p['x']-600, p['y']+600]])
    # 4. Protect what still floods: dry-floodproof ground-floor businesses, deployable barrier on the bay edge
    city.floodproof(city.buildings_in(box, 'COM'), height_m=0.9)
    city.wall([[3300, 3700], [3500, 3900]], height_m=1.2, kind='deployable')
    # 5. Operations: clear the drains, draw the sewer down, tow the cars, stage pumps
    city.clear_drains(box)
    city.sewer_drawdown(basins=[city.basin_of(p['x'], p['y'])])
    city.tow_cars(box)
    city.deploy_portable_pumps([[q['x'], q['y']] for q in hot[1:4]])
    # 6. Wind: brace big street trees on the windward (SSW) streets, prune the rest
    big = city.trees_in(box, min_dbh_cm=40)
    city.brace(big)
    city.prune(city.trees_in(box)[:200])
    # 7. People: a shelter in a school and an evacuation of the basin (see RESPONSE_API.md)
    school = city.buildings_in(box, 'EDU1')
    if school:
        city.response.shelter(school[0], capacity=300, open_at_s=-6*3600)
    # 8. Your own 3D design for the tunnel portal (no extra cost: it depicts the tunnel item)
    city.log('Mission Creek package: store, convey, protect, operate')
```

## What you get back after a run

- The ledger: `total_usd` and `by_cause` (flood_structure, flood_contents, business_interruption, relocation,
  vehicles_flood, wind_glazing, tree_strike_cars, tree_strike_buildings, tree_cleanup_replacement, life_safety).
- `by_building`, `people_affected`, and the plan cost `items` with source URLs.
- The flood map and replay video.

Flood damage uses the FEMA Hazus 4.0 depth-damage curves for each occupancy, applied to the depth above the
first floor (only the lowest 2 storeys are exposed). Vehicles use USACE EGM 09-04. Benefit-cost ratio uses
`sim.costs.bca(avoided, cost, aep)` at a 7% discount rate over 50 years.
