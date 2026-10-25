# Evolve: AlphaEvolve-style search over city storm plans

AlphaEvolve (Google DeepMind) found better algorithms by letting Gemini write many candidate programs, scoring
every one with a machine-checkable evaluator, and evolving the best. StormSim has that evaluator: a plan is a
Python program (`def apply(city)`), and the storm simulation plus the locked cost planner give it a deterministic
dollar score. Evolve runs that loop with Gemini 4 Argon as the architect and Gemini 3.8 Flash as parallel mutators.
AlphaEvolve itself is a Google Cloud product (Gemini Enterprise) and is not reachable with a Gemini API key, so this is
our own implementation on the Interactions API.

## The loop (`agent/evolve.py`)

| Step | Who | What |
| --- | --- | --- |
| Seed | Gemini 3.8 Flash x N | One full plan per strategy (grey, green, building protection, response, trees, mixed); earlier optimizer plans are imported as warm starts |
| Architect | Gemini 4 Argon (thinking high) | Reads the whole program database (every plan, its ledger, cost items and lineage) and returns insights plus one research direction per mutation slot |
| Mutate | Gemini 3.8 Flash x (N-3), in parallel | Parent + two inspirations + Argon's direction, answers with SEARCH/REPLACE diff blocks against the parent |
| Crossover | Gemini 4 Argon | Writes one full plan that combines two elites |
| Tweak | no LLM | Numeric hill-climb: rescales 1-3 sizing literals (heights, m3/s, volumes, factors) of an elite |
| Cascade | sandbox + sim | Compile check, then sandboxed cost estimate (Landlock + seccomp, no network; locked cost planner). Crashing or over-budget plans go back to their author once with the error. Survivors run the full storm in worker processes |
| Database | MAP-Elites | Archive cell = dominant strategy x cost band; each cell keeps its best plan. Parents are sampled across cells so diversity survives |

Fitness = damage avoided (incl. life safety) - plan cost, within the budget. Same plan, same ledger, so the score
cannot be talked up. Duplicate plans (same normalised code hash) are never re-simulated.

## Run it

```sh
scripts/gem.sh .venv/bin/python -m agent.evolve --generations 3 --population 8      # CLI
curl -XPOST localhost:8765/api/evolve -H 'content-type: application/json' -d '{"generations":3,"population":8}'
open http://localhost:8765/evolve/                                                  # live view
STORMSIM_MOCK=1 ... --no-llm                                                        # pipeline test, no API calls
.venv/bin/python -m pytest agent/tests/test_evolve.py -q                            # tests (fake LLM, mock sim)
```

Outputs: `runs/evolve/<job>/state.json` (the view polls it via `GET /api/evolve/<job>`), `plans/<cand>.py`,
`best.plan.py`, `final.json`; the best plan is also written to `runs/_jobs/<job>.plan.py`, so
`POST /api/managed {job_id}` can verify it in the Antigravity sandbox.

## Wall clock

A generation takes the LLM time (Argon ~1.5 min, Flash ~1 min in parallel) plus the slowest storm run. On the mock
sim a 3-generation run of 8 plans takes ~20 min. With the full sim each storm run is minutes, so size the run to it:
`--population 4 --sim-workers 4` keeps one generation to about one sim run plus the model time.

## The view (`/evolve/`)

Evolution chart (net benefit per candidate, best-so-far line), cost vs damage-avoided Pareto front with the budget
and BCR = 1 lines, the MAP-Elites archive grid, leaderboard, Argon's insights and directions per generation, and a
candidate panel with lineage, ledger by cause vs no plan, spend by strategy, the code diff against its parent,
replay video and "Open in 3D". The Architecture tab shows the loop live: nodes light up with the candidates
currently in each stage.
