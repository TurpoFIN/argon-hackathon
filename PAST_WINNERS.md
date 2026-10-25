# Past Cerebral Valley hackathon winners

From the official CV gallery pages (award badges in the page data), fetched 25.10.2026.

## Crusoe Recursive Agents (SF, 17.10.2026)

- 🥇 **Etch**: one sentence → fab-ready circuit board. Agents pick real parts and place and route in KiCad. "Done" is defined up front as measurable checks, and plain code measures them from the real outputs (gerbers, ERC/DRC, ngspice; the firmware actually runs). On a failed check it repairs and rebuilds. https://github.com/PranavAchar01/etch
- 🥈 **SPARKLETEAM**: self-improving agent team that teaches itself to load a dishwasher, with the effect of each learned "law" measured.
- 🥉 **AGI Factory** (Touko): robot-maintenance team that rewrites itself and keeps a change only when unseen factory months prove it better and safer.
- Finalists: Antaeus (synthetic environments for recursive improvement), Mato, Trendify.

## GPT-6 Astra SF (8.9.2026)

- 🥇 **CAD Sandboxes**: Fusion running in a VM behind an HTTP API, with live workspace state streamed to the agent and a split-screen TUI.
- 🥈 **Homebuddy**: 3D renders of your rooms plus furniture modelled from real catalogs.
- 🥉 **Pearl Atlas**: voice-driven 3D travel globe from discovery to booking.
- Finalists: Astra(l) Projection (real hardware → interactive 3D explanation), Soren (synthetic surgical robot training data).

## GPT-6 Astra NYC (10.9.2026)

- 🥇 **Astra HQ**: a team of AI employees; you set the goal and approve their work.
- 🥈 **Counterfactual Worlds**: readings and museum objects → interactive, teacher-guided worlds.
- 🥉 **Method**: continuous policy search for long-horizon tasks (Factorio).

## GPT-6 Astra London (6.10.2026)

- 🥇 **dot-dash**: visualise Codex agent sessions and bring them to life. 🥈 **Sim Sketch Artist**: a simulated robot draws your selfie. 🥉 **Wayfinder**: hospital navigation by phone camera.

## Google / DeepMind events (closest to today's judges)

- **Google I/O Hackathon 2026**: 🥇 **Billie Gene**, a raw pathogen sequence → research-stage mRNA vaccine candidates in ~10 min via an agent-driven scientific workflow. 🥈 Sky Guardian (drones). 🥉 AgentGym (RL environment for managed agents).
- **Gemini 3 Hack SF (12.2025)**: 🥇 **SAV.ai / NNPort**, autonomous embedded-engineering porting with transparent logic tracing. 🥈 ForenSight (multimodal scene reconstruction). 🥉 live chess commentator.
- **Gemini Vibe Code SF (11.2025)**: 🥇 per-repo Docker sandboxes so PMs can review code. 🥈 atelierAI (fashion). 🥉 MCP 3D game-asset generator.

## Pattern

1. **Real artifact, real tool.** KiCad boards, Fusion CAD, embedded ports, vaccine candidates. Not chat, not dashboards.
2. **Verification by plain code, not by the model.** Etch is the template: a requirement becomes a check, the check is measured from the real output, and the build repairs itself until it passes. Today's rubric gives 50% to "live demo with a real verification loop".
3. **A specific expert user.** Hardware engineer, embedded dev, vaccine researcher, factory maintenance.
4. **Visible 3D/physical output** wins on stage (Homebuddy, Pearl, Etch boards).
5. **Science wins with Google judges.** Billie Gene took 1st at Google I/O.
6. **Human approval gate** shows up in most winners (Astra HQ, Etch's "cannot measure").
