# Pre-storm planning: evidence and limits

The default app now follows one chain: select a real site, inspect current conditions, choose the corresponding work, assign qualified teams and total time estimates, then review the schedule. All six hazards remain visible. The numerical damage comparisons have been removed from the planning surface. Hydrology and wind layers are explicitly research tools.

## What the storm evidence establishes

| Hazard | Evidence used | Decision consequence | What remains unvalidated |
| --- | --- | --- | --- |
| Wind and debris | SF DBI/WJE, March 2023 glass investigation; PDF slides 5–8 | Inspect glazing, hardware and loose items; correct identified defects | Current condition and local avoided damage |
| Trees | UF/IFAS post-hurricane observations; SF Public Works storm account | Use an arborist to identify and address defects; distinguish proper maintenance from emergency cutting | Local species/condition transfer and immediate pruning effect |
| Structural collapse | FEMA P-942 §3.1.3 and §4.2.2 | Identify the actual failure mechanism before an engineer designs repair/support | Building capacities, scour, whole-building collapse and benefit of a generic brace |
| Flooding | Local 311 reports, Public Works account, FEMA P-942 §4.2.3 | Inspect current entry paths and inlets; check barriers against overtopping and backflow | Local avoided volume or property loss |
| Power | FEMA P-942 §4.2.3 and §5.3.4–5.3.5 | Verify the complete backup chain and supporting pumps/switches | Customer feeder mapping, critical loads, runtime and outage duration |
| Routes | Public Works account and 311 reports | Inspect primary/alternative access and prepare authorized closure triggers | Exposure avoided or access reliability |

These checks validate the relevance and limitations of mechanisms. They do **not** validate a local numerical benefit, a citywide ranking, or a calibrated storm model. No local treatment/outcome dataset is available in this prototype. The earlier 0.027% and 400 m³ estimates are not used to choose the plan.

The catalog links every claim to primary sources and locators. It includes 69 public flooding/fallen-tree reports in this geographic cut during the selected 2022–23 storm periods; reports can be duplicates or misclassified and do not constitute 69 verified failures. Glass addresses are geocoded from the DBI table. Other building, tree and street choices remain selectable from the inventory without inventing a condition.

## What Argon does

`gemini-4-argon` reviews the chosen scenario, current operator observations, known historical evidence and dependencies. Its constrained output is a permutation of the six tasks plus enumerated reasons. It cannot write benefits, invent field observations or return new asset IDs. Python validates those reasons and computes a schedule from operator-entered team availability and total task durations. Blank capacity is missing; zero is unavailable. Current concern requires a dated/source note. Mitigation additionally requires the listed site prerequisites to be confirmed. Inspection and completed physical mitigation are never conflated.

Changing a site clears its previous condition record. Changes to weather, observations, estimates or evidence invalidate the previous review. The shared versioned plan rejects stale writes, retains drafts on failed saves, and survives reloads. The local map and 3D view use the same saved plan.

## Power provenance

PG&E's GRIP distribution feeder/substation sources and the California Energy Commission transmission layer remain linked in the app. This cut has 77 distribution feeder features, 3 transmission features and 6 substations. It contains no positively identified overhead segment and 77 segments with unknown installation. These maps are neither live outages nor customer connection records. No tree-to-line contact or generator-to-building connection is inferred from proximity.

## Verification

Source findings were checked against the DBI PDF table, the UF/IFAS publication, FEMA's report text and rendered pages 119 and 121, the Public Works account and the original DataSF query. The exact checks and local software/browser observations are in `evidence/planning-validation.json`. Resource examples used during UI testing were restored; the app does not seed invented operational availability.
