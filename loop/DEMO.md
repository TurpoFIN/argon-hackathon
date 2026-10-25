# 3-minute demo

Before judging: run `env PYTHONPATH=. <python> -m loop.serve --port 5182`, where `<python>` is the Codex app's
interpreter (on this laptop: `~/.codex/worktrees/embarcadero-storm/argon-hackathon/.venv/bin/python`). Then open two tabs:

- `http://localhost:5182/runs/today/index.html`, the demo page for today's forecast run
- `http://localhost:5182/3d?run=today&model=argon`, the 3D plan

**0:00. Start it live.** On the demo page, set Storm to "Today: TimesFM forecast from the gauge" and Rounds to 2,
untick Flash, and click **Run live**. Argon's first round lands in about 2 minutes, while you talk.

**0:10. The problem.** "Tuesday at noon, the bay at the Ferry Building is forecast to reach 2.41 m. That forecast
comes from TimesFM 3, running on the last 30 days of the real SF tide gauge, and is 0.36 m above the astronomical
tide. The city has 25,000 sandbags, 8 portable pumps and 2 drain crews. Where do they go?" Point at the forecast
chart's P10–P90 band: the plan has to hold up in the high case too.

**0:30. No preparation.** In the 3D tab, click **No preparation**, then **▶ Play the storm**. Water fills the
Embarcadero and the streets behind it. Expected damage: **$1,083.9M**, across 2,120 real buildings (USACE NSI values,
USACE depth-damage curves).

**1:10. Argon's plan.** Click **Argon round 2 ★**, then play the same storm.

- Cyan roofs are 109 buildings whose doorway sandbags keep them dry.
- Magenta cylinders are the pumps; green dots are the drains cleared before the storm.
- Expected damage: **$532.3M, −51%**.

Read one line of "What Argon saw and changed". Argon watched the rendered replay and the storm video, named the
buildings and streets where the last plan failed, and moved the pumps and bags.

**2:00. Why Argon.** Same storm, same stock:

| Plan | Expected damage |
| --- | --- |
| Best simple rule | $603.3M |
| Gemini 3.8 Flash | $1,036.0M (used no sandbags at all) |
| **Argon** | **$532.3M** |

Argon's whole context holds every flooded building, with its depth in each forecast scenario. It coordinates
sandbags, pumps and crews against the physics, and it catches buildings that are only safe in the median case
(P50) but flood in the high case (P90).

**2:30. Live.** Switch back to the demo page: the live run has forecast today's storm and Argon's first round is
landing.

**2:50. Who it's for.** A city emergency manager or SFPUC planner, the day before an atmospheric river. Every number
is sourced (NOAA, USGS, USACE, DataSF), and the assumptions are listed on the page.

Likely questions:
- **Is the damage real?** It's simulated: the Codex water model, an uncalibrated surface model with no pipe network.
- **The forecast?** TimesFM 3, backtested on the January 2023 storm. The observed 2.37 m peak fell inside its
  P10–P90 band, but its gust forecast was too low (P90 12.8 m/s against 19.3 m/s observed).
- **Is it just a rule?** The rule is on the page. Argon beats it by 12% today; on a single-lever version it only
  matched it.
