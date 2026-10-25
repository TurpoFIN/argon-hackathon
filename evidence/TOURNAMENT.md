# StormSim SF tournament evidence

Storm: `elnino_ar_2026`; budget: **$50M**; all entries use the locked cost planner.

Argon avoids $186.1M more damage than the best no-AI rule (walls everywhere) at the same $50M budget.

The greedy rule measures each candidate intervention as a standalone plan, ranks by damage avoided per sourced dollar, and fills the budget in that order. It is a normal `def apply(city)` plan scored by the same runner. The table is reported as measured, including a greedy win or tie if that is what this storm produces.
The live deadline run measured 1 greedy candidate intervention(s) one at a time; this smaller menu is stated explicitly in the JSON.

| Entry | Model | Damage | Avoided | Cost | Net benefit | BCR | Run |
|---|---|---:|---:|---:|---:|---:|---|
| No plan | none | $258.5M | $0.0M | $0.0M | $0.0M | — | `20261025-230037-1a20-tournament-current-basel` |
| FAIR greedy multi-hazard | deterministic greedy | $251.4M | $7.1M | $17.5M | $-10.5M | 0.403 | `20261025-230821-ccd6-tournament-fair-greedy` |
| Gemini 3.8 Flash | gemini-3.8-flash | $222.1M | $36.4M | $25.6M | $10.8M | 1.422 | `20261025-230844-6f23-tournament-supplied-gemi` |
| Gemini 4 Argon | gemini-4-argon | $54.4M | $204.1M | $42.9M | $161.2M | 4.757 | `20261025-230845-ea27-tournament-supplied-gemi` |
| Walls everywhere | none | $240.4M | $18.0M | $10.9M | $7.1M | 1.648 | `20261025-230844-4064-tournament-supplied-wall` |

Generated: `2026-10-25T23:13:20Z`

Per-cause deltas, candidate measurements, plan hashes, ledger hashes and model run IDs are in [`tournament.json`](tournament.json).
