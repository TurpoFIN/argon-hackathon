# Pre-storm planning: evidence and limits

The default app now compares timed physical work against doing nothing; see [PREVENTIVE_WORK.md](PREVENTIVE_WORK.md) for resource profiles, scheduling and evidence limits. The earlier site-check planner remains an internal compatibility path. Owner/agency requests can proceed alongside field work. Subsequent stops queue until a crew reports back; physical repairs stay behind current inspection and qualified-resource prerequisites. All six hazards receive an explicit decision or deferral. The numerical damage comparisons have been removed from the planning surface. Hydrology and wind layers are explicitly research tools.

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

`gemini-4-argon` receives location-specific candidates built from the local incident cut, DBI addresses, current operator selections and nearest mapped inlet identifiers. It returns 3–8 selected candidate IDs with explicit crew assignments, free-text priority reasoning and a disposition for all six hazards. The application rejects unknown or repeated actions, nonexistent crews and general-crew assignments to coordinator work. It allocates at most one initial stop per general crew and queues later stops without inventing travel or work durations. An initial stop is a proposed dispatch, not proof that a crew was released or that mitigation was completed.

The existing scenario supplies two general crews and a 30-minute window when operator overrides are blank. That is displayed as a provisional scenario profile, not a municipal roster. Zero overrides it as unavailable. `evidence/planning-resources.json` identifies actual public resource programs and request owners: SFPUC surface-clearing guidance and limited tools, Public Works sandbag pickup, tree service, qualified building/electrical work, traffic control and drainage/pumping support. It supplies neither invented specialist counts nor guaranteed response times. Sandbag pickup is subject to hours, eligibility, transport and stock; pumps, barriers, generators and specialist equipment are requested against a site need, not assumed in inventory.

Inspection findings remain editable below the plan. Current concern requires a dated/source note; a clear inspection removes that hazard/site candidate. Physical follow-ups list the inspection, ownership, equipment and qualification needed before work. The earlier six-item review endpoint is retained for compatibility, but no longer drives the primary action plan.
Changing a site clears its previous condition record. Changes to effective weather, resources, observations or evidence invalidate the generated action plan. A changed input while generation is running rejects that result. The shared versioned plan rejects stale writes, retains drafts on failed saves, and survives reloads. The local map and 3D view use the same saved plan.

## Power provenance

PG&E's GRIP distribution feeder/substation sources and the California Energy Commission transmission layer remain linked in the app. This cut has 77 distribution feeder features, 3 transmission features and 6 substations. It contains no positively identified overhead segment and 77 segments with unknown installation. These maps are neither live outages nor customer connection records. No tree-to-line contact or generator-to-building connection is inferred from proximity.

## Verification

Source findings were checked against the DBI PDF table, the UF/IFAS publication, FEMA's report text and rendered pages 119 and 121, the Public Works account and the original DataSF query. The exact checks and local software/browser observations are in `evidence/planning-validation.json`. Resource examples used during UI testing were restored. The action planner displays the existing scenario capacity explicitly and keeps public services distinct from committed operational availability. Live plan generation, user-surface checks and resource-boundary tests are recorded separately in `evidence/action-plan-validation.json`.
