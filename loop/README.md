# Storm prep loop

## Results so far (simulated, 2026-10-25)

All runs use the same storm, crews, candidate drains and 25,000-bag stock.

| Run | No preparation | Greedy rule, no AI | Flash, best round | Argon, best round |
| --- | --- | --- | --- | --- |
| TimesFM January 2023 storm (`demo6`), with the full building table and the greedy plan as round 0 | $1,067.9M | $582.3M | $582.3M | **$580.6M** |
| Same storm, brief showing only the 60 costliest buildings (`live-143427`) | $1,067.9M | not run | $738.1M | $639.3M |

Showing the models only the top 60 buildings made both lose to a simple rule. With the full table
in context, Flash reproduced the greedy plan, and Argon matched it and improved it by about 0.3%.
The simulation, damage curves and sandbag rules decide most of the outcome. The loop's value is
that every plan is checked against them, rendered and critiqued; this run does not show a large
gap over a good heuristic.

## What was merged here, and what was not

Merged, because the loop needs it:
- The Codex app branch (`origin/codex/embarcadero-storm`).
- A labeled snapshot of eight Codex backend files the loop imports. On any later merge, prefer
  Codex's own versions.

Not merged:
- **Codex's single-round planner and the pre-storm text review.** The loop replaces them with
  rendered critique and revision.
- **The analysis dashboard.** It's a dashboard-style view.
- **The lead's `sim/` and `evolve`.** `evolve` has an incomplete set of dependencies, and `sim/`
  uses a different damage schema.
- **The viewer layers and cinema mode in the main checkout.** That's a separate front end, and
  cinema mode is uncommitted work from another session.

Local data: `data/` is gitignored. Copy or regenerate the Codex inputs (`model-inputs.npz`,
`manifest.json`, `raw/`, `footage/`, `argon-perception.json`) with the Codex prepare step.

Gemini 4 Argon writes a pre-storm preparation plan for the Embarcadero. The Codex storm
simulator runs it, the result is scored in dollars and rendered, and Argon watches the
render and video, says what failed, and revises. Gemini 3.8 Flash runs the same loop as
the baseline, with the same storm, crews, candidates and sandbag stock.

```sh
PY=.venv/bin/python   # the Codex environment (numpy, scipy, google-genai, Pillow, matplotlib)
env PYTHONPATH=. $PY -m loop.judge --model argon --run demo1/argon
env PYTHONPATH=. $PY -m loop.judge --model flash --run demo1/flash
env PYTHONPATH=. $PY -m loop.report data/prep-loop/demo1   # writes data/prep-loop/demo1/index.html
```

For the live demo, start the server and open http://localhost:5182:

The main app also serves this same viewer at `/3d`, and at `/` when completed run
artifacts are present in `data/prep-loop` (`STORM_PREP_RUNS_DIR` can point to an
existing run directory). Build with `npm run build` to include the shared
trackpad/keyboard camera controls. The existing plan panel closes with × or H
and reopens with Plan; its round comparison, playback and map layers are retained.

```sh
env PYTHONPATH=. $PY -m loop.serve --port 5182
```

The page opens the newest run. **Run live** starts Argon and Flash together on a fresh run, and
the page refreshes as each round lands. The first model round arrives about a minute after the
click. That time covers the control run, Codex's heuristic and the first model call.

The Gemini key is read the same way as `backend/argon.py`: `GEMINI_API_KEY`, or the hsec
secret named by `GEMINI_HSEC_SECRET` (default `argon-hack-api-key`). If this package sits
beside a `backend/` that has `preparedness.py`, it uses that; otherwise set `CODEX_ROOT`.

## Storm forecast (TimesFM 3)

`loop/forecast.py` forecasts the San Francisco tide gauge (NOAA CO-OPS 9414290) with
[TimesFM 3.0](https://github.com/google-research/timesfm), running on its Apple-silicon MLX
backend in a separate venv so the Codex environment is untouched:

```sh
python3 -m venv .venv && .venv/bin/python -m pip install "timesfm[mlx]==3.0.2"
.venv/bin/python -I -m loop.forecast --issued 2023-01-04T18:00Z --out data/prep-loop/forcing-2023-01-04.json
.venv/bin/python -I -m loop.forecast --out data/prep-loop/forcing-live.json   # forecast from now
```

- **Targets:** the last 30 days of 6-minute water level (m NAVD88), wind speed and gust.
- **Covariate:** NOAA's harmonic tide prediction, a past-and-future covariate.
- **Output:** 9 quantile paths over 48 h, plus the P10, P50 and P90 scenarios.

Each scenario holds the bay boundary at its forecast peak and uses that scenario's wind. Pass
`--forcing <file>` to `loop.judge`; every plan then runs in all three scenarios and is scored
on expected damage, weighted 0.3/0.4/0.3 (Swanson's rule). The live server uses the January
2023 backtest file when it exists.

**January 2023 backtest.** The forecast was issued 24 h before the record:

- **Water level:** the P50 peak came within 6 minutes of the observed 2.368 m peak at
  17:54Z, and the observed level fell inside the P10–P90 band (2.13–2.52 m).
- **Gusts:** the forecast was too low (P90 12.8 m/s against an observed 19.3 m/s). Gauge
  history alone can't see an incoming storm's wind, so wind should come from a weather model
  (HRRR or WeatherNext) as a future covariate.
- **Rain:** not forecast. Every scenario keeps the Atlas 14 design rain.

TimesFM 3.0 weights are licensed for non-commercial, non-production use only. Production use
goes through BigQuery ML.

## What a plan can do

All actions happen before the storm.

- **Drain crews**: up to 16 candidate drains to clear, plus a staging drain per crew. Codex's
  `preparedness.prepare()` enforces the 30-minute crew budget, walking time and hazard areas.
- **Sandbag walls**: polylines of 1, 2 or 3 ft. They raise the simulated ground under the
  line, so water must overtop them.
- **Doorway sandbags**: chosen buildings take no damage while the water against them stays
  below 0.6 m.
- **Budget**: 25,000 sandbags in total (a benchmark assumption). Plans over the stock are
  rejected.

## Score

`loop/damage.py` maps every [USACE NSI 2022](https://www.hec.usace.army.mil/confluence/nsi)
structure in the domain onto the grid. It takes the peak water depth against each
building, subtracts the NSI first-floor height, and applies the USACE
[go-consequences](https://github.com/USACE/go-consequences) default depth-damage curve for
its occupancy type. The result is multiplied by the NSI structure and contents value.
Sources and assumptions are listed in `loop/reference/*.json` and in each run log.

## Rounds

Each round, the model gets three things:

- the full brief
- every earlier run's plan, ledger and clearing outcome
- the latest run's rendered map (`<label>.jpg`) and storm video (`<label>.mp4`)

It returns a critique of the latest run and a revised plan as JSON (schema `Revision` in
`loop/judge.py`). Invalid plans get one repair attempt; the error text is fed back.

## Integration with the Codex app

This folder only adds files, and it calls only the Codex code listed here:

- `backend.scenarios` (`Parameters`, `DATA`)
- `backend.coupled.World`
- `backend.preparedness` (`study`, `simulate`)
- `backend.planning.choose`, which is swapped out briefly to capture the candidate context
- `backend.argon` (`api_key`, `perceive`, `BBOX`)

If those signatures change, `loop/judge.py` is the only place to update. To show a loop result in
the 3D view, read `data/prep-loop/<run>/<model>/<model>.json`. The best round's `plan` holds the
walls as `[longitude, latitude]` polylines, the protected `fd_id`s and the cleared drains.
