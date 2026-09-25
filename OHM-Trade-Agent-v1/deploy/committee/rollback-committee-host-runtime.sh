#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — restore the previous proven host runtime.
#
# Does not enable the timer, does not open provider egress, and does not print
# provider credential values. With --check it only reads.
#
# Usage:
#   rollback-committee-host-runtime.sh [--check]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" && -n "${OPIP_RUNTIME_PREFIX:-}" && -f "${OPIP_RUNTIME_PREFIX}/lib/committee-host-runtime-lib.sh" ]]; then
  # shellcheck source=committee-host-runtime-lib.sh
  source "${OPIP_RUNTIME_PREFIX}/lib/committee-host-runtime-lib.sh"
elif [[ -f "$SCRIPT_DIR/committee-host-runtime-lib.sh" ]]; then
  # shellcheck source=committee-host-runtime-lib.sh
  source "$SCRIPT_DIR/committee-host-runtime-lib.sh"
elif [[ -f /usr/local/lib/opip/committee-host-runtime-lib.sh ]]; then
  # shellcheck source=/usr/local/lib/opip/committee-host-runtime-lib.sh
  source /usr/local/lib/opip/committee-host-runtime-lib.sh
else
  echo "committee runtime library is not installed" >&2
  exit 69
fi

if [[ "$#" -gt 1 ]]; then
  echo "usage: $0 [--check]" >&2
  exit 64
fi
if [[ "$#" -eq 1 && "$1" != "--check" ]]; then
  echo "usage: $0 [--check]" >&2
  exit 64
fi
runtime_paths_init
if [[ "${1:-}" == "--check" ]]; then
  rollback_main --check
else
  rollback_main apply
fi
