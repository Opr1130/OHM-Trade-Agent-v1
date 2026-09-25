#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — one bounded shadow planning cycle.
#
# INERT UNTIL DEPLOYED. This script runs exactly one scheduling cycle and exits; it
# does not loop, so concurrency cannot exceed one. All bounds are the module's own:
# the scheduler's per-cycle budget, the per-case ceiling, and the UTC daily ceiling.
#
# Authority: none. This script cannot reach the trading droplet's credentials, the
# order path, or the canonical evidence writer. It reads a read-only evidence input
# and writes advisory output.
#
# Exit codes:
#   0  the cycle ran, or ran-and-did-nothing because the plane is disabled
#   1  a configuration error the operator must fix
#   2  the cycle could not run for an operational reason
set -euo pipefail

APP_ROOT="${OPIP_APP_ROOT:-/opt/opip/app}"
VENV_PYTHON="${OPIP_VENV_PYTHON:-/opt/opip/venv/bin/python}"
COMMITTEE_HOME="${OPIP_COMMITTEE_HOME:-/var/lib/opip-committee}"

# The plane ships dark. `off` performs no provider call and creates no case; `shadow`
# is the only value that permits committee work. Activation is a separate,
# OWNER-authorised change to the unit's environment.
MODE="${OPIP_COMMITTEE_MODE:-off}"

# An exact release SHA, never a branch. A drifted worker cannot contribute
# prospective evidence, because the prospective path fails closed on release drift.
RELEASE_SHA="${OPIP_COMMITTEE_RELEASE_SHA:-}"

if [[ -z "${RELEASE_SHA}" ]]; then
  echo "committee cycle refused: OPIP_COMMITTEE_RELEASE_SHA is not set" >&2
  exit 1
fi

if [[ "${MODE}" == "off" ]]; then
  # Record the skip rather than exiting silently, so a disabled plane is observable
  # and cannot be mistaken for a healthy one.
  mkdir -p "${COMMITTEE_HOME}"
  printf '{"disposition":"SKIPPED_MODE_OFF","release_sha":"%s","recorded_at":"%s"}\n' \
    "${RELEASE_SHA}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    >> "${COMMITTEE_HOME}/cycle_dispositions.jsonl"
  echo "committee cycle skipped: OPIP_COMMITTEE_MODE=off"
  exit 0
fi

cd "${APP_ROOT}"
unset PYTHONPATH
unset PYTHONHOME
unset PYTHONSTARTUP
export PYTHONDONTWRITEBYTECODE=1

exec "${VENV_PYTHON}" -s -m app.opip.committee.cycle_runner \
  --release-sha "${RELEASE_SHA}" \
  --committee-home "${COMMITTEE_HOME}"
