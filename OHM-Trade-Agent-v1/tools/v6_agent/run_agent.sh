#!/usr/bin/env bash
# Run the v6 builder (Aider + DeepSeek) once on the checked-out branch.
# Usage: run_agent.sh <task-file>
# Needs DEEPSEEK_API_KEY and V6_AGENT_MODEL in the environment. Edits files only.
set -euo pipefail
task_file="${1:?task file required}"
reads=(--read AGENTS.md --read CLAUDE.md --read OHM-Trade-Agent-v1/tools/v6_agent/builder_prompt.md)
if [ -f OHM-Trade-Agent-v1/docs/V6_SPEC.md ]; then
  reads+=(--read OHM-Trade-Agent-v1/docs/V6_SPEC.md)
fi
aider \
  --model "${V6_AGENT_MODEL:?model required}" \
  --yes-always --no-auto-commits --no-suggest-shell-commands \
  --no-check-update --analytics-disable --no-pretty --no-stream \
  "${reads[@]}" \
  --message-file "$task_file"
