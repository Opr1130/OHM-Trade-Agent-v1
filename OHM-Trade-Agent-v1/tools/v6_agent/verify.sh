#!/usr/bin/env bash
# Verify the committed agent branch. Runs WITHOUT any AI credentials.
# Usage: verify.sh <report-file>. Exit 0 only if every check passes.
set -uo pipefail
report="${1:?report file required}"
: > "$report"
fail=0
note() { echo "$1" | tee -a "$report"; }

protected='^(\.github/|OHM-Trade-Agent-v1/deploy/|CLAUDE\.md$|AGENTS\.md$|OHM-Trade-Agent-v1/tests/atdd_scope\.py$|OHM-Trade-Agent-v1/tools/v6_agent/)'
touched="$(git diff --name-only origin/main...HEAD | grep -E "$protected" || true)"
if [ -n "$touched" ]; then
  note "FAIL protected paths changed:"; note "$touched"; fail=1
else
  note "PASS no protected paths changed"
fi

increment="$(tr -d '[:space:]' < OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT)"
if python OHM-Trade-Agent-v1/tests/atdd_scope.py --increment "$increment" --git-base origin/main >/tmp/v6-atdd.log 2>&1; then
  note "PASS scope gate ($increment)"
else
  note "FAIL scope gate ($increment)"; tail -n 20 /tmp/v6-atdd.log | tee -a "$report"; fail=1
fi

if (cd OHM-Trade-Agent-v1 && python -m pytest -q -x tests >/tmp/v6-pytest.log 2>&1); then
  note "PASS pytest"
else
  note "FAIL pytest"; tail -n 30 /tmp/v6-pytest.log | tee -a "$report"; fail=1
fi

if (cd OHM-Trade-Agent-v1 && python -m compileall -q app); then
  note "PASS compileall"
else
  note "FAIL compileall"; fail=1
fi
exit "$fail"
