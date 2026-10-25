# Evidence-constrained harm model

`backend/harm_model.py` retains the strict measurement-based comparison. Following the user's explicit authorization to extrapolate, the main planner displays a separate **illustrative scenario** from `backend/consequence_scenario.py`. Its assumed consequences are not measured or validated local outcomes. See `CONSEQUENCE_MODEL.md`.

No measured San Francisco site inputs have been supplied. Current local before/after harm is therefore **unavailable**, not zero. The research simulator's random capacities and nearest-feeder assumptions remain uncalibrated research; they are not used by this comparison.

## What is calculated

- Water entry at a specified opening, including wave crest, barrier overtopping, verified use within tested barrier conditions, completeness, other entry paths and verified leakage control.
- Inundation of specified contents from interior water level and original/destination elevations.
- Pump operation through the complete grid/backup/distribution/pump/discharge chain, and water control only when inflow and pump duty are known.
- Each result has its measurement provenance and identical before/after storm hashes. Unknown inputs propagate to unavailable outcomes.

Glazing damage, tree failure, structural failure, inlet flood response and route access are not estimated from task completion. They require further measured models/data. No casualty, monetary-loss or failure-probability outputs are produced.

## Evidence and validation limits

FEMA P-942, printed pp. 4-21–4-23 (PDF pp. 119–121), documents: 42-inch floodgates overtopped by wave action; protected pumping/fuel equipment supporting operation; and submerged supporting systems causing failures. These support mechanism checks, not San Francisco damage calibration. The tests explicitly distinguish these checks from synthetic numerical boundary tests.

The observed six-pump Sandy case does not supply inflow, a counterfactual water level, or a local San Francisco pump installation. The model refuses to turn it into an avoided-water-volume claim.

See `evidence/harm-model-validation.json`, `evidence/harm-model-audit.json` and `evidence/harm-comparison-argon.json`. The last file records the actual Argon review of the computed local comparisons; it may establish that no supported positive effect is identifiable.

## Recording evidence

`PUT /api/prestorm/measurements` accepts `expected_version`, a source-backed `site_id`, and a `measurements` object matching `harm_model.Site`.

Every record requires `source_ref`. Records labelled `measured` also require `observed_at` and `observer`. All elevations require a common `vertical_datum`. Bounded inputs use `{ "low": value, "high": value }`; absent inputs stay absent. Dates and provenance must refer to real evidence, not generated examples.

Hazard levels and outages must be tied to the current forcing using `storm_fingerprint` (the SHA-256 of sorted-key JSON storm parameters, also returned in each comparison). A different storm invalidates the consequence calculation. Barrier height alone cannot establish protection: verified operation within tested loading, duration and installation conditions is also required.

This endpoint does not verify an observer's claim, automatically declare field validation, or upgrade a conditional calculation to a calibrated prediction. No example measurements are installed into the live plan.
