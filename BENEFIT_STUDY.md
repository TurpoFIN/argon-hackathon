# Why the preparedness gain was small

This audit replays the original manual-weather scenario and asks Argon to run
mechanism tests, then stronger preventive-action experiments. It does not change
the displayed scenario or the concurrently developed coastal forecasting pilot.

## Finding

The original plan cleared 9 of 1,003 inlets, removing 6.018 kg from 670.687 kg
of initial clogging. Each inlet was only 20% blocked and had an assumed clean
capacity of 12 L/s. Even with continuous water available, those nine clearings
could add at most **77.76 m³** of gravity drainage over one hour. The actual
increase was **60.44 m³**, against **198,420 m³ of rain input**. The measured
full-domain exposure reduction was 0.02666%.

The planning search was also narrow. A new routing policy considers all 1,003
inlets, targets shallow inundation, follows the actual open-land grid, and reserves
travel to dry staging. With the same 2 teams and 30-minute cap, it completed
42 clearings and improved exposure by 0.11044%. Better selection helped, but
it did not remove the resource and hydraulic limits.

## Measured action experiments

All rows use the same 85 mm/h rain, wind, additional 0.5 m coastal rise, initial
20% inlet blockage and 600 kg loose inventory. Exposure is land area deeper than
5 cm integrated over the full hour. Reductions compare with no preparation.

| Preparation | Teams | Work cap | Completed clearings | Pumps | Exposure reduction |
|---|---:|---:|---:|---:|---:|
| Original selected plan | 2 | 30 min | 9 | 0 | 0.027% |
| Shallow-inundation targeting | 2 | 30 min | 42 | 0 | 0.110% |
| Shallow-inundation targeting | 20 | 120 min | 504 | 0 | 1.804% |
| Drain-throughput targeting | 40 | 120 min | 965 | 0 | 3.178% |
| Shallow targeting + pumping | 20 | 120 min | 504 | 8 × 100 L/s | 2.502% |
| Shallow targeting + high-capacity pumping | 40 | 120 min | 504 | 8 × 500 L/s | 3.621% |

These are improvements in integrated flooded area, not uniform safety gains.
Peak modeled depth is unchanged at 4.266 m for the fixed-resource plan, but rises
to 4.328–4.396 m in the scaled plans despite lower final water volume. A plan
selection that forbids worsening peak depth has not been optimized here.

The 20-team clearing run's longest team route used 64.8 minutes; 120 minutes was
the tested cap, not a demonstrated minimum. The combined 20-team run used at
most 73.1 minutes. Additional teams start at the same two original staging
positions. These are explicit resource changes, not improvements obtained with
unchanged staffing.

Pumps are modeled as finite sinks at real inlet locations, installed before the
storm. The assumptions are 600 seconds of setup per pump plus 1.4 m/s travel,
and constant 100 or 500 L/s capacity when water is available. Discharge leaves
the modeled domain. Hose friction, discharge head, fuel, offsite receiving
capacity and equipment availability are not modeled. The 500 L/s row is a
high-capacity sensitivity, not a validated portable-equipment specification.

## Mechanism tests

| Diagnostic change | Exposure reduction |
|---|---:|
| Remove all initial clogs | 3.249% |
| Remove all loose debris | 0.301% |
| Remove both inventories | 3.601% |
| Remove both + double every inlet's capacity | 18.872% |

These are idealized benchmarks, not feasible crew plans or mathematically proven
upper bounds. Flow redistribution and the 5 cm threshold make outcomes
nonlinear. The double-capacity row is an infrastructure sensitivity.

Only 80.10 m³ of potential gravity drainage was prevented by the tide gate in the
control trajectory, versus 31,783.22 m³ actually drained. Widespread tide locking
was not the explanation for this case. With coastal exchange sealed, the rain-only
case retained 96.59% of control exposure. With rain removed, the coast-only case
retained 13.43%. Those percentages overlap and must not be added as source shares.
The large net coastal water budget includes ocean-cell storage; it is not a
measurement of water delivered to streets.

Some initial Argon hypotheses were rejected by these tests. Its first diagnostic
narrative also overstates the meaning of a zero-duration control cell and calls
idealized comparisons strict ceilings. The second experiment explicitly supplies
those corrections. Its final review still overstates the evidence by claiming a
1% gain is impossible under the original resources. The tested fixed-resource
policy did not reach 1%; this is not a proof that no policy could. A control-cell observation is not evidence of treatment
benefit; only matched counterfactual differences support that claim.

## Reproduce

Use the repository Python environment, prepared public geographic inputs under
`data/`, and the existing authorized Argon credential configuration:

```sh
.venv/bin/python -m scripts.investigate_benefit
.venv/bin/python -m scripts.test_preventive_actions
.venv/bin/python -m unittest discover -s tests -p 'test_benefit.py' -v
```

`evidence/benefit-baseline.json` freezes the original parameters, real cached
perception, selected targets and physical-array hashes. Changed inputs fail the
reproduction check. The first script must reproduce the original control and
selected plan before invoking Argon. Each provider interaction uses `store=False`
and a 90-second request timeout; numerical trials are bounded to one hour.

Results: `evidence/benefit-diagnostics.json` and
`evidence/preventive-action-results.json`. Both retain the actual Argon tool-call
receipt, measured outputs and interpretation. Partial action results are saved
under ignored `work/` before the next trial. This study works with the committed
coupled core and does not depend on the uncommitted coastal integration.

A 1% reduction was used as an explicit comparison yardstick, not a calibrated
safety standard. These findings explain this hypothetical model and parameter
set. They do not establish field effectiveness or globally optimal routing.
