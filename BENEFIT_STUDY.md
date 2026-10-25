# Why the preparedness gain was small

**Scope correction:** the 0.027% result measures a very narrow water/drain
experiment. It is not evidence that storm preparedness has little value. The
[broader assessment below](#scope-correction-wind-and-useful-preparedness)
documents missing hazards, paired wind tests, and the revised experiment design.

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

## Scope correction: wind and useful preparedness

The user-visible question should be: **with the time, people and equipment we
actually have before this storm, which actions best protect people, access,
critical services and property?** The current objective, reducing square-metres
times minutes of shallow outdoor water, cannot answer that question. A plan can
leave precisely the same water on the street and still protect a vulnerable
entrance, remove a falling-object hazard, or keep a critical facility operating.

This correction is a research/design result. The expanded damage and service
models below are **not yet implemented in the local viewer**.

### What the current engine actually does with wind

`backend/simulation.py::potential_wind` computes an inviscid 2D diagnostic field.
Its docstring explicitly excludes turbulence, wakes and failure loads.
`backend/coupled.py::World.step` uses 3% of that local velocity, capped at 1 m/s,
to move pre-existing loose material until it encounters an inlet. It creates no
new material from failed trees or buildings. In the audited path the gust
parameter has no numerical consequence.

Five matched one-hour runs froze the original rain, tide, geometry and initial
clogs. Only the named wind/debris inputs changed:

| Case | Mean / gust, m/s | Wind from | Flood exposure, m²·min | End blocked inlets |
|---|---:|---:|---:|---:|
| Original | 20 / 28 | 168° | 24,389,061.6 | 51 |
| Gust-only change | 20 / 50 | 168° | 24,389,061.6 | 51 |
| Stronger mean and reversed direction | 35 / 50 | 348° | 24,416,956.8 | 65 |
| No loose material; calm | 0 / 0 | 168° | 24,315,652.8 | 0 |
| No loose material; strong wind | 35 / 50 | 348° | 24,315,652.8 | 0 |

The original and gust-only cases have **identical hashes of the full peak-depth,
flood-duration and final-depth arrays**. So do the two no-loose-material cases.
The stronger/reversed case changes the water metric by only +0.1144%, through
clogging. Changing both speed and direction in that row does not isolate either
variable's individual effect. The 50 m/s gust is a diagnostic input, not a forecast.

Missing outcomes—glass or roof failures, tree falls, injuries, power loss, route
disconnection, interior damage and business interruption—must be reported as
**unmodeled**, never as zero. The results and actual Argon review are archived in
`evidence/wind-scope-audit.json`; reproduce with
`.venv/bin/python scripts/audit_wind_scope.py`. The script intentionally fails
its scope assertions if future code changes these numerical dependencies; it
does not prescribe keeping the limitations.

The archive retains Argon's initial proposal, explicit review corrections and
a second, evidence-informed review. Use `--refine` to repeat that second review
without repeating the physical trials. The first proposal incorrectly treated
the coastal storage budget as land inflow, proposed unequal preparation horizons,
invented an 18 m/s work cutoff, and demanded effects that need not occur in every
world. None of those assertions is an accepted model requirement. The corrected
design below governs this assessment; raw model output is evidence of the
review process, not a validated engineering model.

### The strongest local evidence is about preventable defects

San Francisco's [20 September 2023 Building Inspection Commission presentation](https://media.api.sf.gov/documents/BIC_Meeting_092023_-_Item_9.pdf)
reports 31 broken panes investigated across seven buildings. Investigators
concluded that **30 had likely causes that could have been identified and
mitigated before the storms**. Its listed causes include thermal stress, debris
impact, window hardware and contamination; wind-load breakage was assessed as
very unlikely. This is historical forensic evidence, not a prediction of 97%
effectiveness for a new plan or proof that every repair fits a short warning.

The investigating engineers' [full WJE account](https://www.wje.com/knowledge/webinars/detail/wind-events-and-glass-breakage-lessons-learned-in-san-francisco)
describes pre-broken panes, an operable-window hardware failure, and secondary
debris damage. Building condition and maintenance can dominate a generic
wind-speed threshold. The useful model is therefore **wind loading × asset
condition × exposed people/property × feasible action**, including uncertainty
in those inputs.

[NWS preparedness guidance](https://www.weather.gov/mob/wind_mitigation) supports
removing/anchoring loose objects, maintaining trees and roofs, and protecting
openings. It does not supply numerical effectiveness for these particular
buildings. [USDA's urban-tree guidance](https://www.climatehubs.usda.gov/approach/maintain-trees-and-remove-hazards-reduce-severity-or-extent-wind-and-ice-damage)
connects tree failures to property damage, electric-service disruption and
blocked emergency access, and stresses individual-tree condition and the costs
of losing healthy trees. Blanket tree removal is not an acceptable optimizer
shortcut.

The live [DataSF street-tree schema](https://data.sfgov.org/api/views/tkzw-k3nq.json)
was checked during this audit. It exposes species, position, diameter fields and
responsibility/status information, but no explicit current structural-condition,
root-defect or anchorage-strength field. A point on the map cannot establish that
a tree is hazardous or quantify the benefit of pruning it.

### A connected model, not an expanded list of independent toggles

| Hazard and consequence chain | Before-storm choices worth testing | Outcome and essential evidence |
|---|---|---|
| Wind uplift/lateral load, breached-envelope pressure, flood forces or foundation scour → connection/member/foundation failure → partial or total structural collapse | Inspect documented structural defects; complete engineer-specified bracing/anchoring where feasible; dismantle or secure rated temporary structures; clear exclusion areas before onset; schedule permanent retrofits separately | Collapse state and footprint, debris, people exposed and severed routes/functions; structural system, load path, connection/capacity data, condition, foundation/soil and asset-specific loading |
| Gusts and direction → loose objects, failed glazing/roof components → impacts and further debris | Secure/remove identified loose objects; close operable windows; repair/remove documented defective components; restrict exposure where repair cannot finish | Exposed person-minutes and struck assets; mapped objects, facade orientation/height, condition, repair procedure, wind uncertainty and people/route data |
| Rain/antecedent wetness + wind → tree/branch failure → strikes and blocked access | Qualified targeted tree work; move parked vehicles; alter a walking route or pre-storm staging site | Conditional strikes, route availability and detour time; tree inventory, condition/root defects, soil state, potential fall geometry and accessible street graph |
| Wind-damaged opening + driving rain → interior wetting → contents loss and downtime | Secure vulnerable openings; relocate exposed contents; protect critical rooms | Interior water and asset/site downtime; opening condition/area, first-floor level, rain direction, actual contents and vulnerability data |
| Rain + tide + inlet/sewer limits → flooding at entrances/roads | Targeted clearing; correctly specified barriers, storage or pumping; move vulnerable assets | Entrances affected, access, damage and neighboring water rise; thresholds, outfall/head/capacity, sewer boundary and barrier geometry |
| Wind/tree/flood failures → loss of power → pumps, signals, lifts or facility functions unavailable | Test and connect existing backup; stage compatible equipment and fuel; protect vulnerable electrical equipment | Critical function-hours lost; verified supply topology, loads, backup connection/capacity, runtime and access. Do not assume every downtown tree threatens an overhead line |
| Any of the above → people displaced or routes severed | Timed warnings; selective pre-storm relocation or closures; preserve safe alternative routes and staging | Exposure, isolation, shelter capacity and essential-trip delay; time-varying occupancy, mobility needs, compliance, safe destination and route capacity |

These mechanisms interact. Closing a sidewalk can move pedestrians into a flood
zone; barriers can divert water to another entrance; a flood-safe staging site
can be below defective glazing. A generator has no benefit for a pump without a
compatible connection and reachable fuel. Securing an object can prevent both a
direct strike and downstream drain obstruction. The model must propagate those
links and avoid counting the same avoided loss twice.

Wind requires time-varying gust/direction, exposure and height, not a cosmetic
uniform multiplier. The current 2D arrows must not be converted directly into
facade pressures. Use validated wind/load methods or clearly labeled sensitivity
ranges appropriate to the asset class; do not extrapolate a generic residential
fragility to downtown curtain walls. Some uncertainty can only be reduced with
condition evidence, not a more expensive fluid solver.

### Structural collapse is a separate, explicit failure mode

The requested scope includes roofs, load-bearing walls and whole-building
stability, as well as scaffolds, canopies and other temporary structures. Glass
or cladding loss is not itself a building collapse, but an opening can change
internal pressure and structural demand. Failures may remain local or propagate
when a load-carrying connection/member or foundation support is lost.

[PNNL's continuous-load-path guidance](https://basc.pnnl.gov/resource-guides/continuous-load-path-provided-connections-roof-through-wall-foundation)
explains the roof-to-wall-to-foundation transfer needed to resist wind. It is
residential guidance, not a calibrated collapse model for an SF high-rise.
[FEMA's Coastal Construction Manual](https://www.fema.gov/sites/default/files/2020-08/fema55_voli_combined.pdf)
also identifies undermining by erosion/scour and failed load paths as structural
failure mechanisms. [SafeWork NSW's construction guidance](https://www.safework.nsw.gov.au/hazards-a-z/high-winds-in-construction)
specifically includes scaffold collapse and structure/equipment-specific wind
limits. These sources establish mechanisms and preparation requirements; they
do not establish which mapped SF structure will collapse in this scenario.

Represent distinct states: intact, nonstructural damage, primary
connection/member failure, and partial/total collapse. Determine transitions
from asset-specific load/capacity and condition information, or explicitly
conditional sensitivity scenarios when information is missing. Never infer a
collapse threshold from building height alone or assign every building the same
wind-speed trigger. Wave impact, dynamic structural response and scour are not
outputs of the current surface-water or 2D wind solvers.

Candidate actions include completed, engineer-specified connection repair or
temporary bracing/anchoring; safe dismantling of temporary structures; and
pre-storm exclusion/relocation when stabilizing work cannot be completed.
Authority, appropriate expertise, equipment limits and feasible lead time must
be explicit. Evacuation reduces exposure; it does not strengthen the structure.
Major retrofits belong to the capital-investment horizon.

A collapse event must update debris generation, affected neighboring assets,
the water-flow obstruction and the pedestrian/vehicle graph, with one shared
state for both views. The planner cannot quietly delete a building, grant a
generic "reinforcement" damage discount, or omit people inside the structure.
Acceptance cases must include a stable structure, a known weak connection,
wind-plus-flood compound demand, and an exclusion plan that leaves structural
failure unchanged while reducing exposure. These are required tests for the
future structural module, not results already obtained by this audit.
The actual Argon structural review is retained as `structural_addendum` in the
same evidence file; rerun that review with `--structures` without repeating the
five wind/water trials. Its crane/scaffold examples are candidate action
families, not universal procedures: any operating configuration, dismantling
sequence or temporary support must follow the asset-specific engineered method.

### Make the action budget physically credible

Keep three decision horizons distinct:

- **Short warning, such as 30 minutes:** small tasks on already identified,
  accessible assets; securing loose objects, closing windows, moving exposed
  vehicles/people and placing limited closures. Count dispatch, travel, access,
  setup, work and return to a safe position. No citywide inspection or structural
  repair is assumed possible.
- **One to three days:** owner-coordinated inspections and feasible repairs,
  qualified tree work, tested backup-power connections, specified temporary
  protection and relocation plans. Include equipment availability, trades and
  site dependencies. Inspection improves information; repair changes risk.
- **Longer-term investment:** facade/roof retrofits, permanent power redundancy,
  sewer/storage upgrades and tree replacement. Report investment tradeoffs
  separately from emergency preparations; do not let construction materialize
  during a short warning.

Each action needs a real target ID, owner/operator, skill, equipment/material
inventory, duration, travel/access, prerequisites, safe weather cutoff, physical
state change and cost basis. A task unfinished at onset yields only the work
actually completed. A generated phrase like "reinforce buildings" is not an
executable action. No arbitrary `damage *= 0.5` is accepted as its effect.

### The next experiment that can answer a useful question

Keep the Embarcadero extent and shared scenario clock. Start with one connected
district containing buildings, their entrances, real street links, mapped
trees and a small set of critical destinations. Add verified condition records
where available; otherwise label the affected assets and failure states as
**conditional test scenarios**, not observations.

Compare no preparation, the existing drain policy, a transparent simple policy,
and Argon portfolios that can spend the **same resources** on securing objects,
identified facade defects, specific tree hazards, asset relocation, closures,
entrance protection and compatible backup. Only include an action family after
its physical effect, target inventory and completion rule can be tested. Do not
reward a planner merely for asking for more crews or unavailable equipment.

Freeze the exogenous world across alternatives: the same weather trajectories,
initial defects, latent component strengths, occupants and destinations.
Sample uncertain input once per world, not again per plan. Re-run paired worlds
for changed wind direction, storm timing relative to occupancy, wetness and
condition assumptions. Use physically coherent weather sequences; do not label
the independent combination of extreme marginal values a joint return period.

Keep the simulator's latent condition separate from what Argon is allowed to
know. The planner receives observations and their uncertainty, never the hidden
defect/failure sample or future outcome. An inspection costs time, can find
nothing, can miss a defect, and updates information only. Appropriate completed
remediation changes the physical state. Measure the value of inspection by
whether the resulting allocation improves outcomes after paying that cost.

Report a vector of outcomes: hazard-exposed person-minutes, disconnected
destinations/essential-trip delay, critical function-hours unavailable,
building/contents/vehicle losses where defensible, work completed, preparation
cost and residual flood consequences. Show individual high-consequence failures
as well as averages. Casualty predictions require a defensible vulnerability
model; exposure is not automatically an injury estimate. Safety limits and
unacceptable service losses are constraints, not tiny weights in a dollar sum.

Acceptance tests must catch at least these failures:

1. Stronger gusts affect a susceptible wind asset even with zero loose debris.
   A sound asset may remain unaffected; do not force damage monotonically onto
   every building.
2. Changing direction changes the exposed facade/fall or debris path.
3. Removing a real source prevents its downstream debris; closing a route
   changes exposure/access, not physical damage or the number of people.
4. An unconnected, flooded, overloaded or fuel-exhausted backup cannot supply
   a critical function. A functioning backup has no benefit in a no-outage case.
5. A closure or barrier can make another route/entrance worse, and that cost
   appears in the result. Relocated people and cars need valid destinations.
6. Identical plans in identical sampled worlds reproduce; infeasible schedules
   and tasks finishing after the safe cutoff are rejected.
7. Argon's chosen benefit survives paired uncertainty tests and is compared with
   a simple policy under the same budget. A gain must come from completed
   physical/exposure changes, not plan-dependent random draws or prose.

### Reuse the other work without conflating it with this app

At inspected `origin/main` commit `88cb619`, existing work includes a 4 m
SoMa/Mission Bay/downtown grid, 23,564 mapped street-tree records, 6,000
**synthetic** parked cars, flood damage tables, a broader city action/cost API,
and people/response modules. These are useful starting points, not proof of an
integrated wind-preparedness system. `sim/wind.py` and `sim/trees.py` referenced
by that commit are absent from its tree. The checked-in glazing fragility has
explicitly assumed capacity parameters; the local forensic findings above show
why those cannot establish preventability from condition-specific repair.

The other grid has different bounds and row orientation, and its Git history
does not share a base with this isolated branch. Port audited modules and data
through explicit coordinate/unit contracts; preserve collaborators' work.
Audit reproducibility at the call boundary: `sim.people` already uses a
storm-only population stream, while exposing a separate plan-seeded stream.
Do not mistake its earlier docstring for evidence that the whole population
changes with each plan. Verify which randomness actually affects comparisons.

The parallel coastal forecast pilot is complementary forcing work. It cannot
supply the missing asset conditions, causal damage models, or service network.
Neither that pilot nor this scope audit changes the fact that the broader
consequences remain unimplemented in the current viewer.
