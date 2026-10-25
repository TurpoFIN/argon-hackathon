# 3-minute demo script (no slides, live)

Scoring: live demo with a real verification loop 50%, problem/user 25%, "only possible with Argon" 15%, pitch clarity 10%.

| Time | Screen | Say |
| --- | --- | --- |
| 0:00–0:15 | Photoreal SF, live NWS alert banner (a real Coastal Flood Advisory is active today) | "An El Niño atmospheric river is 72 hours out. I'm [USER], SF Department of Emergency Management. I have $50M. What do I do?" |
| 0:15–0:50 | No-plan storm (P director mode or 2×): rain ramps up, Mission Creek basin floods at 17th & Folsom, drains back up, trees down, people stranded, damage counter climbs. One ground-level moment (F). | "This is real physics on real data: lidar terrain, every building, all 23,564 street trees and 4,982 storm drains. Shallow-water flood model, lattice-Boltzmann wind, FEMA damage curves." |
| 0:50–1:00 | Proof overlay: 31.12.2022 replay vs 311 reports and the SFPUC map | "We replayed New Year's Eve 2022. Our flooding lands on the real 311 reports 2.6× better than chance, and the sim knocked down 11 trees against 9 real reports." |
| 1:00–1:35 | Agent drawer: Argon's stream. It watches the replay video, critiques it, writes plan code, and run 2 beats run 1. | "We gave Gemini 4 Argon the entire city: 600k tokens, every building, drain and tree, plus SF's own plans. It writes the plan as code, runs the storm, watches the video and fixes what failed." |
| 1:35–2:05 | Build tour (B): walls rise, gates, pumps, shelters light up, evacuation routes draw, depots and crews, each with Gemini's reasoning and a running cost | "Floodwalls tied into high ground, a relief tunnel, drain clearing, shelters in real buildings, evacuation of vulnerable residents, food and water runs." |
| 2:05–2:35 | Same storm with the plan: water stops at the walls, people stream into shelters. Compare (C): no plan / Flash / Argon + leaderboard | "Same storm. Damage $X → $Y, people at risk down Z%, benefit–cost ratio N. Flash with the same data and budget gets [..]." |
| 2:35–2:50 | Verification: a live 1-hour re-run on a fresh cloud machine, or the Managed Agent's independent re-run (identical ledger hash). The planner clicks Approve. | "Every price is cited, the sim is deterministic, and Argon in Google's Antigravity sandbox re-ran it independently and got the same hash. Nothing goes out until the planner approves." |
| 2:50–3:00 | Final numbers | "SF is spending $632M on flood fixes. This tells you which ones work, before the storm." |

Q&A prep: VALIDATION.md (what passes, what fails and why), the damage calibration caveat, 16 m wind resolution, local-inertial vs HLL.
