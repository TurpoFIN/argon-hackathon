# Bugs found by validation (owner: validation lead; fixes routed by the lead)

## B1 (HIGH) sim/water.py: 2D instability at the production CFL alpha = 0.7 in deep, still water

A 1 mm bump in a 0.8 m deep flat pool (4 m cells, n = 0.03, theta = 0.8, alpha = 0.7) does not decay: within
5 minutes the water surface range grows to 0.6-1.5 m and unit discharges reach 2-4 m2/s (bounded only by the
Froude cap and the outflow limiter). 1D (perturbation uniform across rows) is stable; the 2D case is not.
alpha = 0.6 or 0.5 is stable (range stays 0.0 m); theta = 0.9 or n = 0.015 does not help at alpha 0.7.
The explicit 2D staggered scheme needs Courant <= 1/sqrt(2) = 0.707 even without the theta stencil;
with theta < 1 the bound is lower. de Almeida et al. 2012 also recommend alpha 0.5-0.7 depending on case.

Impact: deep ponds in the city (Mission Creek / 17th & Folsom reach 3-6 m in the sweeps) can slosh spuriously:
inflated max_speed / max_hazard (people, cars, DEFRA hazard) and noisy max_depth. Mass is still conserved.

Repro (uses the production kernels via validation/swe_driver.py; ~10 s):
```python
import sys; sys.path.insert(0, '.')
import numpy as np
from validation.swe_driver import SWE
from sim import water as Wm
for alpha in (0.7, 0.6):
    z = np.full((21, 40), 0.2); z[:, :2] = 3; z[:, -2:] = 3
    h0 = np.maximum(1.0 - z, 0); h0[10, 20] += 1e-3
    p = Wm.WaterParams(); p.alpha = alpha
    s = SWE(z, 4.0, n=0.03, h0=h0, params=p); s.run_to(600)
    e = (s.h + z)[s.h > 0.01]; print(alpha, 'surface range after 10 min', e.max() - e.min())
# 0.7 -> ~0.9 m (unstable), 0.6 -> 0.0 m
```
Suggested fix: `alpha` 0.7 -> 0.5 in data/reference/water_params.json (cite de Almeida et al. 2012 / Bates 2010
stability range); ~40 % more time steps. Re-run the validation (`validation/run_all.py`) afterwards: test 1b
checks exactly this.

## B2 (MEDIUM, known model limitation) sim/water.py: oscillatory sheet flow at Froude >= ~0.9

Steady uniform channel flow (test 4) matches Manning to 0.0 % for Fr <= 0.7 but at Fr ~0.9-1.4 (steep SF streets,
S >= 0.4 % with n = 0.015) the flow never becomes steady: roll-wave-like oscillations with std ~ mean depth and a
mean depth 15-35 % below normal depth. Setting fr_max = 1 stabilises it but forces critical depth (+25 % to +100 %).
This is the documented local-inertial limitation (de Almeida & Bates 2013, WRR 49: valid for Fr < ~1).
Repro: `.venv/bin/python -m validation.test_water 4` (see validation/out/t04_manning.png).
Possible mitigation: alpha 0.5 (B1) may damp part of it; otherwise document it (ponding is subcritical).

## B3 (LOW-MEDIUM) sim/damage.py: RES3 restoration days fall back to COM1 (relocation cost ~2x for apartments)

`assess()` calls `_restoration_days("RES3", d)` for every RES3 building (5,865 of 11,782 in the tile), but
`damage_functions.json["restoration_days"]["rows"]` has no `RES3` key (FEMA's table uses RES3A-RES3F), so
`rows.get(occ) or rows.get("COM1")` silently uses COM1's 360 days instead of RES3A's 180 days (0-1 ft).
Relocation for a 3-storey RES3 building at 0.45 m: sim $17,005 vs Hazus FAST by hand $9,061.
Repro: `.venv/bin/python -c "import sys;sys.path.insert(0,'.');from sim import damage;print(damage._restoration_days('RES3',0.3), damage._restoration_days('RES3A',0.3))"`
Fix: in `_restoration_days`, map `RES3*` -> `RES3A` (all RES3A-F rows are identical), or add a `RES3` row
copied from RES3A in data/reference/build_reference.py. Test 11 (`validation/test_damage.py`) then passes exactly.

## B4 (INFO) sim/water.py HLL option: steady but biased on steep, thin sheet flow

With `scheme='hll'` the Manning channel (test 4) is steady at every slope (good: no B2 oscillation) but the
depth error vs normal depth is -1.7 % (S 0.2 %, n 0.035, Fr 0.3), +2.8 % (Fr 0.93), +9.8 % (S 1 %, Fr 1.4) and
+64 % (S 5 %, Fr 2.9). First-order Audusse hydrostatic reconstruction is known to be inaccurate when the depth
is small compared with the bed step per cell (Delestre et al. 2012, "On the hydrostatic reconstruction";
remedy: second-order MUSCL reconstruction or the Chen & Noelle 2017 scheme). The HLL dam break (test 3) is
excellent (1 % error at every Froude). Repro: `.venv/bin/python -m validation.test_water 4`.

## B5 (HIGH for the demo numbers) sim/damage.py: replay flood damage ~46x the area-scaled reported loss

With the calibrated replay (i_design 10.3, runs/dev_water/water_dec31_2022.npz), `damage.assess` gives
$250.5M of flood damage in the tile ($118M structure + $111M contents + $14M BI + $5M relocation + $3M vehicles)
for 31.12.2022. The city's estimate was >= $46M for the whole 2022-23 storm season citywide (tile = 11.8 % of SF
land: $5.4M area-scaled). Even allowing for private losses and the tile holding the worst basin, 46x is
implausible. 47 % of the total comes from 12 large buildings with shallow water (0.25-0.9 m) and
`first_floor_m = 0.0`, e.g.:
- 8642 COM4 7 floors, facade depth 0.64 m, value $249M structure + $249M contents -> $27.1M
- 574 GOV1 6 floors, 0.58 m -> $18.8M; 10210 COM4 4 floors, 0.36 m -> $14.4M; 10212 RES3 6 floors, 0.25 m -> $12.5M
Drivers: (1) `exposed_storeys = 2` applies the whole-building Hazus % to two full storeys of value even for
0.25-0.6 m of water (NACCS damages per affected storey: only the ground storey is wet below ~3 m);
(2) `first_floor_m = 0.0` for large commercial buildings (FEMA/Hazus default for non-residential slab is
~1 ft; most SoMa/Mission entries are a step up); (3) contents = 100 % of structure value for COM4/GOV1.
Repro: `.venv/bin/python -m validation.run_all` (test 12) or
`damage.assess(load_grid(), buildings, load_result('runs/dev_water/water_dec31_2022.npz'))`.
Suggested: exposed storeys = number of storeys below the flood level (ceil(depth / storey height), min 1);
review first_floor_m defaults; then re-check test 12. Mass and depth physics are not the problem here.
