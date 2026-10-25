# `city.response`: emergency-response API (for Argon's plan code)

Your `apply(city)` builds infrastructure with the CityEditor **and** an emergency response with
`city.response` (a `sim.response.ResponsePlan`). The people sim (`sim/people.py`) then moves
~10k group agents (~25 people each, real buildings, real street graph) through the storm and
scores life safety with the UK Defra/EA FD2321 method. You choose actions and quantities;
**prices are fixed** in `data/reference/response_costs.json` (FEMA 2023 equipment rates, BLS
wages, DHS OIG commodity costs, FEMA P-785 shelter standards). Never set a price.

Units: local metres from the grid SW corner (x east 0..3548, y north 0..4256), the same as the
3D scene. Times: seconds from storm start; negative = before landfall (allowed -72 h .. +48 h).
The El Nino storm floods the Mission Creek basin (around x 760-1070, y 1020-1340, the
17th & Folsom area), Mission Bay (x 2340-2900, y 1390-1950) and the waterfront; it peaks
about T+4 h.

## Actions

| call | effect in the sim |
| --- | --- |
| `shelter(building_id, capacity=None, open_at_s=-6*3600, supplies={'meals': n, 'water_gal': n}, name=None)` | Opens a congregate shelter in a real building. Capacity is clamped to FEMA P-785 / Red Cross 40 sq ft (3.7 m2) per person x 60% usable floor. Evacuees and rescued people go here. Warned if it floods or has no generator. |
| `evacuate(polygon, order_at_s, to_shelters=[ids], priority='vulnerable'\|'all', transport='auto'\|'walk'\|'bus')` | Residents in the polygon (vulnerable = elderly/disabled share from DataSF `cne3-h93g`) route to the nearest listed shelter with free space. Car owners drive (6.7 m/s, stop at 0.3 m water), no-car vulnerable ride paratransit/buses (priced per bus-hour), others walk 1.2 m/s (0.9 vulnerable), slowed by water and stopped at 0.5 m. People who cannot get through become **stranded**. |
| `depot(x, y, kind='food'\|'water'\|'medical'\|'sandbags'\|'pumps', name=None)` -> id | Supply staging point. |
| `supply_run(depot_id, shelter_ids, trucks=n, depart_at_s=t, loop=True)` | Trucks (4.5 t payload) loop depot -> shelter with the biggest deficit on the street graph, rerouting around water > 0.3 m and fallen trees. Needs: 3 meals + 1 gal water / person / day (FEMA). Unmet needs are counted and priced. |
| `crew('rescue', n, stage_at=(x, y), start_s)` | n boat teams of 4. Pull at-risk (FD2321 HR >= 0.75) and stranded people out, nearest-first by hazard; 6 people per trip at 2 m/s + 10 min loading. Without staged crews only 2 SFFD units respond, 30 min after the first emergency. |
| `crew('sandbag', n, tasks=[building ids], start_s)` | 0.3 m bag line at doorways: ground-floor occupants stay dry until water overtops. |
| `crew('pump', n, tasks=[(x, y), ...], start_s)` | 6-in trash pumps, 0.095 m3/s each (exposed as `response.pump_sinks()` for the water solver). |
| `crew('tree', n, stage_at=(x, y), start_s)` | Clear fallen street trees off roads (30 min each, nearest first) so trucks/cars get through. |
| `crew('medical', n, stage_at=(x, y), start_s)` | Medical staff (labor priced). |
| `close_road(polyline, at_s)` | Barricades. Drivers learn about flooded streets with a ~10 min lag and get trapped in streets that flood in front of them; closures route them around underpasses and sags. |
| `alert(text, at_s, zone=polygon\|None)` | AlertSF / WEA. Lowers the FD2321 flood-warning score (fewer injuries) for people in the zone, more with lead time (full effect at >= 2 h). |
| `generator(building_id, start_s)` | 150 kW backup generator (shelters keep power). |
| `medical_post(x, y, start_s, name)` | Field treatment site + ambulance; rescued people are taken to the nearest one. |

Helpers: `buildings_in(polygon, occupancy_prefix=None, min_floor_area_m2=0)` -> ids;
`building_info(id)` -> location, occupancy, floors, floor area, max shelter capacity.
Invalid input raises `ResponseError` with a message telling you what to fix.

## What gets scored (people.json, ledger)

Shelter occupancy over time, evacuated, stranded, rescued, at-risk person-hours by FD2321 hazard
class, expected injuries `Ninj = 2 Nz HR AV/100 PV` and fatalities `Nf = 2 Ninj HR/100`,
meals and water delivered vs needed. The ledger adds `life_safety_usd` (fatalities x US DOT VSL
$13.7M), `injuries_usd` (MAIS-2 0.047 x VSL), `unmet_needs_usd` and `response_cost_usd`. Every
action also appears in 3D (`plan_objects.json`): shelters glow green with cots, evacuation zones,
routes along the real streets, depots, truck routes, crews, sandbags, closures, alerts.

## Example: a good response for the Mission Creek basin

```python example
def apply(city):
    r = city.response
    basin = [(740, 1000), (1100, 1000), (1100, 1370), (740, 1370)]   # 17th & Folsom / Mission Creek basin
    # Shelters out of the flood zone: Moscone Center South + 3 schools, opened 8 h before landfall
    moscone = r.shelter(8637, open_at_s=-8*3600, supplies={'meals': 6000, 'water_gal': 2000}, name='Moscone Center South')
    s1 = r.shelter(8194, open_at_s=-8*3600, supplies={'meals': 1500, 'water_gal': 500}, name='School A (Mission Bay)')
    s2 = r.shelter(4271, open_at_s=-8*3600, supplies={'meals': 1500, 'water_gal': 500}, name='School B (SoMa)')
    s3 = r.shelter(7128, open_at_s=-8*3600, supplies={'meals': 1500, 'water_gal': 500}, name='School C (Potrero)')
    for b in (8637, 8194, 4271, 7128):
        r.generator(b, start_s=-8*3600)
    # Warn early, then evacuate the basin (everyone) and vulnerable residents of the low Mission/SoMa ~6 h before the ~T+4 h peak (buses for no-car residents)
    r.alert('Flash flood risk tonight in the Mission Creek basin. Move to upper floors or go to a shelter.', at_s=-12*3600, zone=basin)
    r.alert('Storm landfall: avoid low streets around 17th & Folsom.', at_s=-1*3600, zone=None)
    r.evacuate(basin, order_at_s=-2*3600, to_shelters=[s2, s3, moscone], priority='all', transport='auto')
    low_mission_soma = [(600, 800), (1450, 800), (1450, 1650), (600, 1650)]
    r.evacuate(low_mission_soma, order_at_s=-2*3600, to_shelters=[s1, s2, s3, moscone], priority='vulnerable', transport='auto')
    # Food and water: two depots, trucks looping to all shelters
    food = r.depot(1500, 1900, kind='food', name='Food depot (SoMa)')
    water = r.depot(1300, 2300, kind='water', name='Water depot (SoMa)')
    r.supply_run(food, [moscone, s1, s2, s3], trucks=4, depart_at_s=-6*3600)
    r.supply_run(water, [moscone, s1, s2, s3], trucks=3, depart_at_s=-6*3600)
    # Sandbag the ground-floor homes in the basin, pumps at the sag, close the low streets
    homes = r.buildings_in(basin, occupancy_prefix='RES')[:60]
    r.crew('sandbag', n=6, tasks=homes, start_s=-10*3600, end_s=-2*3600)
    r.crew('pump', n=4, tasks=[(925, 1218), (880, 1150)], start_s=0)
    r.close_road([(760, 1218), (1070, 1218)], at_s=1*3600)
    r.close_road([(925, 1020), (925, 1340)], at_s=1*3600)
    # Rescue boats staged at the basin edge, tree crews, a medical post
    r.crew('rescue', n=4, stage_at=(1000, 1450), start_s=0)
    r.crew('tree', n=2, stage_at=(1500, 1900), start_s=0)
    r.medical_post(1250, 1500, start_s=0, name='Medical post (Folsom & 13th)')
```

Run it: `.venv/bin/python sim/people.py --plan example --water <npz>`; compare with `--plan none`.

## Integration (lead / run pipeline)

```python
from sim.people import run_people, write_people
editor, log, err = exec_plan(plan_code)                 # CityEditor; editor.response is the ResponsePlan
res = run_people(storm, water=water_result, response=editor._response, trees=tree_result,
                 seed_key=hashlib.sha256(plan_code.encode()).hexdigest()[:16])
write_people(res, f"runs/{rid}/frames")                  # people.f32 (n_frames x n_agents x 3: x, y local m, state) + people.json
plan_objects += res.plan_objects                         # response half of plan_objects.json
ledger_addon = res.ledger_addon                          # life_safety_usd, injuries_usd, unmet_needs_usd, response_cost_usd
```

`water` = `WaterResult` (or dict/npz with `depth` (n, ny, nx) and `t_frames`); `None` = synthetic flood on the SFPUC
100-yr zone. `trees` = `TreeResult` or list of `{id, fail_t}`. Output frames follow the water frames (73 for the 6 h storm).
`res` also exposes `expected_fatalities`, `injuries`, `people_affected` for `sim/damage.py(people_result=res)`.
Do not add both damage.py's life_safety and `ledger_addon.life_safety_usd`: pick one.
`ResponsePlan.pump_sinks()` -> [(x, y, m3/s, start_s)] can be fed to the water solver; `sandbagged()` is already merged
into CityEditor.merged_floodproofed().
