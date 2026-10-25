# Build with Gemini 4 Argon — 1-day hackathon builder guide

Cleaned copy of Google's shared doc (https://docs.google.com/document/d/1kN0n6hPNNTIHMVqf5-hDACrZuIg4KFrWiYpla4WQW8w). Model IDs that the export split across lines are rejoined. Raw export: `source/builder-guide-raw-export.md`.

Gemini 4 Argon (`gemini-4-argon`) is Google's new frontier model for software engineering, complex knowledge work, cybersecurity defense and multimodal creation. Input, thinking and output share one 1M-token context window. It works longer on its own and retains context across large codebases and dense document sets.

## What makes a strong 1-day project

Judges score working demos, not slide decks. Strong projects pass 4 tests:

1. It solves a specific problem for a real person you can name, not a generic audience.
2. It does work that needed Argon: long-horizon coding, autonomous verification loops, multi-file synthesis, 3D and WebGPU generation, or multi-agent orchestration.
3. It keeps people in control when taking actions that affect external systems, money or private data.
4. It runs live during judging with real inputs in under 3 minutes.

## Toolkit

Most teams pair Argon as lead brain/orchestrator with one or two fast companions.

| Job | Model / agent | ID |
| --- | --- | --- |
| Frontier reasoning, long-horizon coding, 3D, orchestration | Gemini 4 Argon | `gemini-4-argon` |
| Autonomous sandbox execution (hosted Linux: code, tests, files, MCP, web search) | Antigravity managed agent | `antigravity-preview-09-2026` |
| Fast parallel subagents, tool calls, codebase exploration | Gemini 3.8 Flash | `gemini-3.8-flash` |
| Lowest-latency routing, classification, guardrails | Gemini 3.5 Flash-Lite | `gemini-3.5-flash-lite` |
| Deep web research with citations | Deep Research agent | `deep-research-preview-04-2026` |
| Real-time voice + camera | Gemini 3.8 Live | `gemini-3.8-live` (deep reasoning while talking: `gemini-3.8-live-extended-thinking`) |
| Speech-to-text / translation | Gemini 3.5 Transcribe / Live Translate | `gemini-3.5-transcribe-live`, `gemini-3.5-transcribe` (speaker labels), `gemini-3.5-live-translate-preview` (70+ languages) |
| Speech, voice design/replication | Gemini 3.8 Flash TTS | `gemini-3.8-flash-tts` (faster: `gemini-3.8-flash-lite-tts`) |
| Images, textures, sprites, 4K | Nano Banana 2.1 | `gemini-nano-banana-2.1` (sub-second: `gemini-3.1-flash-lite-image`) |
| Video with native audio | Gemini Omni Flash | `gemini-omni-1.1-flash` (run in background) |
| Songs, vocals, loops | Lyria 3.5 | `lyria-3.5` (30 s loops: `lyria-3-clip-preview`) |
| Open / on-device | Gemma 4 | `gemma-4-31b-it` (E2B/E4B run locally) |

## Get started in 15 minutes

1. Claim the pre-provisioned account from the Google team at kickoff.
2. Install the Gemini API skills in your coding agent (already in `.claude/skills` and `.agents/skills` here), or prototype in AI Studio Build / Antigravity.
3. First call on the Interactions API with `google-genai` / `@google/genai` ≥ 2.3.0.
4. Ask the onsite Google mentors when blocked.

```sh
npx skills add google-gemini/gemini-skills --skill gemini-api-dev
npx skills add google-gemini/gemini-skills --skill gemini-live-api-dev
agy plugin install https://github.com/google-gemini/gemini-skills
```

## Calling Argon

Use the Interactions API: one API for models and managed agents, server-side state (`previous_interaction_id`), background execution.

### 1. `thinking_level`: `"low"` | `"medium"` (default) | `"high"`

- medium: interactive coding and agentic tool use
- high: multi-file debugging, security patching, formal maths, upfront system design
- low: fast classification, routing, extraction

```python
from google import genai
client = genai.Client()
interaction = client.interactions.create(
    model="gemini-4-argon",
    input="Build a single-file HTML drum machine ...",
    generation_config={"thinking_level": "medium"},
)
print(interaction.output_text)
```

### 2. Long decode (`background=True`)

Default output limit is 65,536 tokens. To go beyond (up to 1M minus input), set `background=True` and `max_output_tokens` > 65536:

```python
import time
interaction = client.interactions.create(
    model="gemini-4-argon",
    input="Write a complete multi-module physics simulation and test suite.",
    background=True,
    generation_config={"max_output_tokens": 262144, "thinking_level": "high"},
)
while interaction.status in ("in_progress", "queued"):
    time.sleep(5)
    interaction = client.interactions.get(id=interaction.id)
print(interaction.output_text)
```

### 3. Legacy parameters to remove

- `thinking_budget` → use `thinking_level`
- `temperature`, `top_p`, `top_k` → ignored, deprecation warning
- explicit `cached_content` → implicit caching runs automatically (95% off cached input)
- Pricing through 30.11.2026: $2 / 1M input, $10 / 1M output
- HTTP timeouts 15–20 min for `"high"` prompts, or use `background=True`

### 4. Managed Agents (the $5K prize)

```python
interaction = client.interactions.create(
    agent="antigravity-preview-09-2026",
    agent_config={"type": "antigravity", "model": "gemini-4-argon"},
    input="Audit https://web.dev for performance, Core Web Vitals, and SEO. ...",
    environment="remote",
)
print(interaction.output_text)
```

Secrets: register with the Credentials API (`client.credentials.create`) and restrict the sandbox network allowlist (`environment={"type": "remote", "network": {"allowlist": [...]}}`) so secrets are injected at the proxy and never enter model context.

## 5 patterns that save hours

1. **Align on intent before long runs.** Have Argon surface questions, edge cases and trade-offs first; then let it run the multi-file build to completion.
2. **Give Argon a verification loop.** Tests, compiler output, runtime logs, or a headless-Chrome check script with a baseline score; iterate until all pass with zero regressions.
3. **Multimodal references + video understanding.** 1–2 reference images for frontends/3D; extract a `DESIGN.md`; record a screen-share of a bug with console logs and let Argon scrub it.
4. **Orchestrate Argon with Flash and media subagents.** Argon (medium/high) as architect defining interfaces and reviewing diffs; `gemini-3.8-flash` for parallel leaf modules; Nano Banana for textures, Live for voice, TTS/Lyria for audio.
5. **Capture quirks.** Write lessons into `AGENTS.md` or a skill; before the demo, have Argon generate an interactive HTML architecture diagram.

## Resources

- Developer guide: https://aistudio.google.com/learn/gemini-4-argon-developer-guide
- AI Studio: https://aistudio.google.com · Build: https://aistudio.google.com/build
- Antigravity: https://antigravity.google
- Skills: https://github.com/google-gemini/gemini-skills
- Docs: https://ai.google.dev/gemini-api/docs · Cookbook: https://github.com/google-gemini/cookbook
- Live API examples: https://github.com/google-gemini/gemini-live-api-examples
- Feedback: gemini-developer-feedback@google.com
