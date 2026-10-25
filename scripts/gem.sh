#!/usr/bin/env bash
# Run a command with the hackathon Gemini key as GEMINI_API_KEY (never printed).
exec hsec exec --only GEMINI_ARGON_HACK_KEY -- bash -c 'GEMINI_API_KEY="$GEMINI_ARGON_HACK_KEY" exec "$@"' _ "$@"
