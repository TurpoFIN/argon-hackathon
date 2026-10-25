# Repository instructions

## Keep the GitHub remote up to date

- The canonical repository is `https://github.com/TurpoFIN/argon-hackathon` and the remote is `origin`.
- Before starting changes, inspect the working tree and fetch `origin` to check for collaborators' updates. Preserve existing local work and integrate remote changes without discarding anyone's commits.
- After completing project changes, run the relevant checks, commit the task's changes, and push the current branch to `origin` before reporting the task finished. During longer tasks, push meaningful, checked milestones so the remote stays current.
- Verify that the remote branch points to the local commit before saying the repository is synchronized. If a push is blocked, report the exact blocker and leave the local work intact.
- Never force-push, rewrite shared history, or commit credentials, local environment files, dependencies, or generated build output.
- Honor any explicit user request to keep a particular change local or to pause committing or pushing.

## Hackathon project: StormSim SF — Gemini 4 Argon SF Hackathon (Cerebral Valley × Google DeepMind), Sun 25.10.2026

- Submit by **5:00 PM PT** with public repo + 1-min demo video. Judging 5–7 PM, finals 7–8, winners 8:15. Shack15, Ferry Building.
- PARTICIPANT_GUIDE.md: official guide (problem statements, schedule, rules, judging weights). RULES.md: summary + full waiver. BUILDER_GUIDE.md: models, API, Managed Agents. PAST_WINNERS.md, IDEAS.md.
- Module contract: CONTRACT.md. Product: STORMSIM.md. The ArcGIS photoreal scene (index.html, scene.js) is the visual base.
- Use the Interactions API (`client.interactions.create`), `google-genai` ≥ 2.3.0, model `gemini-4-argon`, managed agent `antigravity-preview-09-2026`. Gemini API skills are in `.claude/skills` and `.agents/skills` (google-gemini/gemini-skills @ ee9da52).
- No `temperature`/`top_p`/`thinking_budget` with Argon; use `thinking_level`.
- API key comes from the Google team at kickoff; keep it out of git.
- The submission is licensed to CV + partners and nothing shared is confidential: no Helios code or customer data.
