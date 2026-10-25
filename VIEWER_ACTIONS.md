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
