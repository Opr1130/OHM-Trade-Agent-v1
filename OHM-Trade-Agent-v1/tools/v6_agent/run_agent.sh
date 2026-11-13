#!/usr/bin/env bash
# Run the v6 builder (Cline CLI + DeepSeek) once on the checked-out branch.
# Usage: run_agent.sh <task-file>
# Needs DEEPSEEK_API_KEY, V6_AGENT_PROVIDER and V6_AGENT_MODEL in the environment.
# Cline runs headless (-y) with a hard timeout. Verification happens afterwards
# in a separate step that has no AI credential.
set -euo pipefail
task_file="${1:?task file required}"
prompt="$(cat OHM-Trade-Agent-v1/tools/v6_agent/builder_prompt.md)

Read AGENTS.md and CLAUDE.md first.
$( [ -f OHM-Trade-Agent-v1/docs/V6_SPEC.md ] && echo "Also read OHM-Trade-Agent-v1/docs/V6_SPEC.md." )

TASK:
$(cat "$task_file")"
cline -y \
  -P "${V6_AGENT_PROVIDER:?provider required}" \
  -m "${V6_AGENT_MODEL:?model required}" \
  -k "${DEEPSEEK_API_KEY:?key required}" \
  -t 1500 \
  -c "$PWD" \
  "$prompt" || echo "builder exited with status $? (continuing: the verify step decides)"
