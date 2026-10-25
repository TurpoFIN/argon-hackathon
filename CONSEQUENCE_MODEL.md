# Illustrative consequence comparisons

The user explicitly authorized extrapolation on October 25, 2026. This model answers a conditional question: if the stated vulnerable assets and capacities apply, how does completing an action change the outcome under the **same** storm? It does not claim those conditions were surveyed at the displayed address.

## Geography versus assumptions

DataSF building footprints, street segments and tree locations are source geometry. Flood/power views use the mapped building outline. No basement is asserted. Interior layout is unknown; an explicitly assumed 80 m² exposure patch is shown without invented room walls. Wind, tree and structural failure capacities, loose objects, contents, electrical availability and compatible backup equipment are scenario assumptions.

The storm is fixed in both branches and its SHA-256 is returned on both. The only changed input is whether the selected action is complete. Specialist actions not in the resource plan are labelled previews.

## Mechanisms

| Mechanism | Assumed scenario |
|---|---|
| Wind | Ten possible loose objects have release thresholds of 18–36 m/s. Secure attachments raise those thresholds to 46–64 m/s. A subset of released objects hits panes; closing windows reduces hardware failure and wind-driven rain entry. |
| Trees | Wind load follows 0.613 × gust². An assumed weak, wet tree reaches failure at 26 m/s. Pruning reduces projected wind load to 65%; it does not repair the roots. |
| Structure | A vulnerable 12 m² façade section reaches failure at 26 m/s. The assumed designed support multiplies capacity by 1.7. This is partial façade failure, not whole-tower collapse. |
| Flood entry | Rain runoff from 1,200 m² minus the supplied inlet capacity accumulates over 200 m². Water above an assumed 0.15 m sill enters. A complete 0.6 m fitted barrier or 0.4 m sandbag barrier works only below its top. |
| Backup pumping | Rain over an assumed 220 m² ingress catchment collects in 80 m² of exposed floor. Grid power is assumed lost above 22 m/s gusts. A compatible 6.3 L/s pump prevents accumulation only when it can keep up. This does not restore public-grid service. |
| Access | Existing and new storm debris reduce a 3 m lane. A 2 m usable width is required. Clearing existing debris cannot prevent newly falling debris in a stronger storm. Cordon work reduces assumed passages through danger, without removing the obstruction. |

The scenario lasts one hour. Constants are explicit in `ASSUMPTIONS`; they are engineering simplifications, **not coefficients fitted to storm evidence**. The referenced Sandy, SF 2023 and Andrew reports motivate mechanisms only. No causal treatment percentages, casualty counts or dollar-loss estimates are claimed.

Tests include calm weather, stronger storms overwhelming interventions, unchanged forcing, backup leaving the public grid off, and moving contents without draining the floor. Argon chooses work from these computed comparisons subject to the existing crew, travel, time and equipment constraints. `evidence/illustrative-consequences-argon.json` records the real Argon run.

The app opens at neighborhood scale. Selecting an action preserves the camera. Model detail is explicit and uses a block-scale view; Neighborhood and All sites restore geographic context. The cutaway is an illustration, not a basement survey.
