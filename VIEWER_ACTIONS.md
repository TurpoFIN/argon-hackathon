# Actions in the preparation-run viewer

`loop/viewer3d.html` retains the round picker, saved flood ledger, playback and
original layers. `frontend/runActions.ts` adds the catalogue inside that panel;
there is no second planner mounted over it.

`backend/viewer_actions.py` reads the selected run's parameters, P50 forcing,
31 flood frames and public mapped assets. It never substitutes the other
prototype's current storm. Every action's two branches have the same storm
fingerprint and frame axis. Existing run jobs and all 12 operation types are
available; additional candidates use up to 12 exposed mapped sites per type.
The current `today/argon` round 5 yields 251 jobs across the six hazards.

The local comparisons are **extrapolations**, not isolated full-city reruns or
validated site predictions. They do not alter the saved dollar total. The
renderer raises local effect diagrams above the mapped site so real buildings
do not conceal them; it does not delete the surrounding building layer.

- Windows/items: assumed opening inventory and wind thresholds, using logged
  wind and rain. The current 7.88 m/s gust produces no broken panes; closing
  assumed open windows reduces rain entry.
- Trees/structure: explicit hypothetical capacities from the earlier mechanism
  model. No failure or avoided failure is predicted at the current low gust.
- Barriers/contents: local run depth against assumed entry, protection and
  storage heights. Overtopping cancels barrier credit.
- Pumps/inlets: local flow-balance approximations driven by the same field.
  Saved pump removal is bounded by the no-preparation depth. The saved run does
  not isolate each recorded drain's marginal effect; the UI says so.
- Power: an added compatible pump and protected backup chain. Flooded supporting
  equipment can defeat the backup. Public feeder geometry is displayed as
  proximity context, not a verified building connection.
- Routes: light-debris removal cannot remove floodwater. A cordon restricts
  entry rather than lowering physical damage or assigning avoided injuries.

Crew sizes, work ranges, equipment and inspection conditions come from the
existing operation catalogue; additional work is not claimed to be scheduled.
`evidence/viewer-action-review.json` records Argon's review of one representative
of each operation type. It is a critique of the supplied calculations, not
independent calibration or review of all 251 sites.


## Action-linked Argon work plan

`python -m loop.workplan data/prep-loop/today/argon --minutes 30` now asks actual Argon for different ordered action-ID plans, schedules them, runs the complete saved storm for each, returns the numerical results to Argon, and saves its choice. `backend/workplan.py` is the common ID/schedule/physics contract. `workplan.json` saves the choices, execution receipts, input digest, matched removals and provider receipts; the viewer reads this file rather than interpreting a catalogue review as a selected plan.

The current run retains two two-person general crews and 30 minutes, including grid walking and return. Stock is explicitly assumed; specialist crews are unavailable. The planning candidates include mapped NSI buildings near both crew starts and real drain IDs, rather than only the old top-12 display excerpt. All six hazard categories remain inspectable. This run does not assert that all operations are feasible or needed.

The same forecast fingerprint `cd8bb6f9bdaac6ff730079cbf54fd03f23670d885a4c15d1dd946fcb2797919f` reproduces the archived no-action flood total to within one dollar. Actual Argon compared drainage, windows and a hybrid, then chose inlets 158769 and 305510: 27.57 and 29.57 crew minutes including return. The full-city simulation estimates $127,046 less flood damage, a 0.012% reduction. At those sites, final local depths change 28 to 26 cm and 21 to 20 cm. These are model outputs, not measured storm benefits.

Removing either selected action loads a complete matched city replay, updates the flood layer, equipment/work counts and dollar total, and shows that action's matched before/after beside it. Unselected alternatives cannot apply an independent fake plan. Old flood rounds remain selectable as archived experiments. The original timeline, camera, cinema and hide/reopen controls remain.

Evidence is in `evidence/unified-workplan.json`. Nine scheduling/contract tests, eight mechanism tests, three serving tests and the build pass. Live browser acceptance covers the selected plan, action removal/restore and do-nothing baseline. Field validation remains unavailable. Window inventories, indoor ingress, asset condition and the local non-flood mechanism calculations remain assumptions; this change does not claim a calibrated six-hazard loss model or optimality over every city asset.
