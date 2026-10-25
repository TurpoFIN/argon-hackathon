# Ideas

Constraints: submit 5:00 PM PT (public repo + 1-min video). Must fit Problem Statement One (Million Token Engineering: big migration, measured against a baseline) or Two (More Dimensions: generate a 3D world, render it, watch it, critique, fix in a loop). 50% of the score is a live demo with a **real verification loop**. Past winners (PAST_WINNERS.md) put a real artifact in a real tool, with checks measured by plain code.

## Touko's pick: city flood-resilience simulator ("FloodLine")

Scope: **one hazard (coastal flooding), one district (SF Embarcadero/Ferry Building, where judging happens)**. Earthquakes are out: they need structural simulation.

1. **3D city:** USGS 3DEP 1 m lidar terrain + building footprints/heights (OSM or DataSF) → Three.js scene.
2. **Hazard from live data:** NOAA tide gauge 9414290 (SF), king tide + storm surge + sea-level-rise scenarios (NOAA/OPC projections, cited via Deep Research).
3. **Physics:** flood sim on the DEM grid (connected bathtub, then simple shallow-water if time), animated in 3D.
4. **Argon optimizes:** proposes interventions (seawall segments, raised promenade, deployable gates at specific streets, wetland edge) with cited unit costs. Code re-simulates and scores damage avoided (FEMA depth-damage curves × building value) per $ spent. Argon also watches the flood video to find leak paths ("water enters via the Mission St low point") and patches the plan. Loop until cost per $ avoided stops improving; show it beating a "wall everything" baseline.
5. **Human in control:** the planner picks and approves the plan; output is a cited screening report, not engineering design.

User: Port of SF Waterfront Resilience Program / city resilience planners. Checked 25.10: the 3DEP elevation export and NOAA tide API both respond; Overpass was at capacity (fallback: DataSF footprints or an Overpass mirror).

## Top 3D picks matching the winning pattern (round 3)

Pattern: one sentence → real engineering artifact in a real tool → 3D on screen → plain-code checks → auto-fix until green → human approves.

1. **CellBuilder: sentence → verified robot cell (recommended).** "UR5e picks 2 kg boxes off a conveyor onto a pallet, 6 per minute." Argon writes the MuJoCo scene (robots from MuJoCo Menagerie: UR5e/Franka) plus the controller in one pass. The managed agent runs it headless and records video. Code measures pick success %, cycle time, collisions, joint/torque limits and reach. Argon watches the video for failures (drops, near-misses), patches, and reruns until all checks pass. Output: a sim, a layout and a cycle-time report the integrator signs off before quoting. User: a robot system integrator who spends weeks building sims to quote one cell. Fits robotics + world models + Problem Two + the factory story.
2. **Sentence → working mechanism (Etch for mechanical).** "Gearbox: 12 V motor to 60 RPM, 3 N·m, printable." Argon writes a CadQuery assembly. Code checks interference between parts, the gear ratio (spin it in the sim), wall thickness for printing, and mass. A turntable render is watched for floating parts. Output: STEP/STL plus an exploded 3D animation. User: a hardware-startup mechanical engineer.
3. **Spare part from a phone video.** Film a broken part beside a ruler. Argon uses long-video understanding to rebuild a parametric CAD, overlays renders on the frames, and code scores silhouette match and the ruler-scaled dimensions. Fix until it matches, then quote. User: factory maintenance tech with an obsolete part and no drawing. Highest wow and Helios fit, but the riskiest in 4 h (camera pose).
4. CellForge (virtual cell, #1 above) and Doom port (#4) stay as alternates.

## 1. Virtual cell — "CellForge" (alternate, Problem Two)

Argon builds a 3D virtual minimal cell (JCVI-syn3A, the 493-gene synthetic bacterium) in one long decode, then renders it, watches it, and fixes it until it matches the real biology.

- **Spec from real data:** the Deep Research agent pulls a cited ground-truth sheet from the published syn3A whole-cell papers (Thornburg 2022, Gilbert 2023): cell size, ribosome count, genome length, major protein counts, crowding fraction, diffusion rates. Use exact values from the papers, not from memory.
- **Generate:** Argon writes the Three.js/WebGPU world in one pass: membrane, chromosome, ribosomes and proteins as particles with Brownian motion.
- **Verification loop (the 50%):** the Antigravity managed agent runs the world in headless Chrome and records a video. Plain code measures the sim: particle counts, volume fraction, mean-squared displacement → diffusion coefficient, membrane closure, frame rate. Argon watches the video for visual errors (clipping, overlap, empty regions). Each failure becomes a patch, then it re-renders. On stage: score 41% → 96% of checks passing.
- **Interaction:** "Knock out the ribosomal protein gene" or "starve it of glucose" → the spec changes, the world rebuilds and re-verifies.
- **User:** a computational biologist building whole-cell models. The published 4D syn3A sim took a lab years. Also teachers and science communicators.
- **Why Argon only:** one-pass multi-thousand-line world generation, long-video critique, Deep Research + managed sandbox orchestration.
- **Managed Agents prize:** the whole render → measure → patch loop runs in the Antigravity sandbox.
- **Risks:** WebGPU in headless Chrome. Fall back to WebGL Three.js and puppeteer screencast. Don't claim it predicts biology; it's a spatially faithful, verified model of the cell.
- **Not this:** training a gene-expression or perturbation predictor (the Arc "virtual cell" sense). Can't be built or verified in 4 hours.

## 2. Virtual cell, Problem One version: port the 2012 whole-cell model

Port the Karr/Covert *M. genitalium* whole-cell model (MATLAB, 28 submodels) to Python/JAX in one long decode, then measure against the original outputs. Very "Million Token", but it needs baseline outputs from the MATLAB code. Only if we can get MATLAB/Octave running or find published simulation outputs within 30 minutes.

## 3. Drawing → part → quote (Problem Two, Helios-flavoured)

Argon reads a machining drawing PDF and writes a CadQuery model in the sandbox. It renders the model and compares the views and dimensions to the drawing (plain-code dimension checks plus Argon's visual critique), fixing until it matches. Then it outputs an operation plan and a quote, and a shop estimator approves before sending. Closest to past winners Etch and CAD Sandboxes, with the strongest real user. Public sample drawings only (waiver + public repo).

## More ideas (round 2)

| # | Idea | PS | Verification loop (plain code) | User |
| --- | --- | --- | --- | --- |
| 4 | **Doom → WebGPU in one pass**: port the GPL Doom C source to TypeScript/WebGPU with Freedoom assets | 1 | Doom demos replay deterministically: diff game state and frames between original and port per tic; % matching climbs live | Game-preservation / engine devs |
| 5 | **Mainframe exit**: AWS CardDemo (open COBOL/CICS bank app) → Java/Python | 1 | Run the original in GnuCOBOL and the port on the same generated transactions; diff outputs; baseline = Flash or single pass | Bank mainframe team |
| 6 | **Obsolete PLC rescue**: Siemens S5/legacy ladder → IEC 61131 Structured Text | 1 | Simulate both on the same I/O traces (OpenPLC); diff outputs and timing; human signs off before download to the PLC | Factory automation engineer (AGI Factory follow-up) |
| 7 | **Photo → robot sim**: photos of a real workcell → MuJoCo scene | 2 | Render from the photo's camera pose, compare image and depth to the photo, drop-test physics; fix until it matches | Robotics engineer doing sim-to-real |
| 8 | **Floor plan → walkable 3D + code check**: PDF plan → 3D model and walkthrough video | 2 | Measure door widths, wheelchair turning circles and egress distances against ADA rules; Argon watches the walkthrough for errors | Small architecture firm |
| 9 | **Self-playtesting game levels**: Argon builds a 3D level, a bot plays it, and Argon watches the video | 2 | Bot completion rate, impossible jumps, stuck spots, frame rate; patch until beatable and tuned | Indie level designer |

## Dropped

- Bug video → fixed PR: crowded and generic.
- Robot digital twin from a phone video: hard to make reliable live in 4 hours.
- Anything chat/RAG/dashboard-shaped: on the banned list.
