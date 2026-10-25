# StormSim SF: Gemini 4 Argon defends San Francisco from an El Niño storm

An El Niño atmospheric river is heading for San Francisco. StormSim SF simulates it hitting a real 3.5 × 4.3 km slice of the city (downtown towers, SoMa and the Mission Creek basin around 17th & Folsom): rain, runoff, overwhelmed storm drains, a surging bay, wind through the tower canyons, street trees snapping and uprooting, cars floating, and 250,000 people. It prices the damage in dollars.

Gemini 4 Argon then gets the whole city in its 1M-token context and a budget, and designs the city's response the way a planning cell would: floodwalls and gates, pumps, cisterns, a relief tunnel, permeable streets, drain clearing, sewer drawdown, shelters, evacuation of vulnerable residents, food and water logistics, and crews. It writes the plan as code against a city API, builds it in 3D, runs the storm, watches the replay video, critiques its own plan and fixes it. Every model gets the same storm, data, tools and budget. The planner approves the final Emergency Operations Plan.

**Problem statement:** Two, More Dimensions (generate a 3D world, render it, watch it, critique it, fix it in a loop).

## What's real

- **Data:**
  - USGS 3DEP lidar terrain.
  - DataSF building footprints with lidar heights, 23,564 street trees, 4,982 storm inlets, assessor parcels, census population and flood-health vulnerability.
  - The SFPUC 100-year storm flood map.
  - 311 reports from the 31.12.2022 storm.
  - NOAA tides, Atlas 14 rainfall statistics and the live NWS alerts.
- **Physics:**
  - 2D shallow-water flood model on a 4 m grid, with rain, infiltration, capacity-limited inlets and a combined-sewer model that surcharges.
  - D3Q19 lattice-Boltzmann LES wind over the voxelised city.
  - Tree failure by bending versus wood strength and root anchorage on saturated soil.
  - Vehicle stability in floodwater.
  - Agent-based evacuation on the street graph with DEFRA FD2321 flood hazard.
- **Damage:** FEMA Hazus flood depth-damage curves (FAST tables), with the USACE NACCS storey-exposure method for multi-storey buildings. Also USACE vehicle curves, glazing, tree-strike and business interruption, and life safety at the US DOT value of a statistical life.
- **Costs:** a locked, diff-based cost planner. The model never sets a price. Every line item comes from a cited unit cost in `data/reference/`.
- **Deterministic:** the same plan always gives a bit-identical ledger, on this box, in Daytona/Fly sandboxes and inside the Antigravity managed agent.
- **Validation:** `VALIDATION.md` (`python -m validation.run_all`) checks the models against analytic solutions, published benchmarks and the real 31.12.2022 storm, and reports failures honestly.

## Run it

```sh
uv venv .venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python sim/prep.py                         # build the city grid from data/raw (scripts/fetch_data.sh)
GEMINI_API_KEY=... scripts/serve.sh                  # FastAPI on :8765, serves web/dist
cd web && npm ci && npm run build                    # 3D app (ArcGIS photoreal base + three.js storm layer)
.venv/bin/python -m sim.run elnino_ar_2026            # one storm run -> runs/<id>/
```

Built at the Gemini 4 Argon SF Hackathon (Cerebral Valley × Google DeepMind), 25.10.2026, by Touko Ursin and Alex Smeds. The photoreal city base (ArcGIS Maps SDK; Esri / Precision LightWorks SF buildings) is Alex's scene from the first commit.
