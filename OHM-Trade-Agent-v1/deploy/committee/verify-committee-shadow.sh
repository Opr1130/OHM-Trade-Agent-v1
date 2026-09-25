#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — SHADOW-mode proof and rollback (IC-043).
#
# RUNS ON THE LEARNING/ANALYTICS PLANE, AS ROOT. Prints one PASS/FAIL line per
# check and a machine-readable summary. This proves what the controller-side
# workflow claims about the host; it never prints an environment file, a
# credential, or a credential value.
#
# Usage:
#   verify-committee-shadow.sh
#   verify-committee-shadow.sh --rollback
#
# `--rollback` first returns the plane to OFF (removing the provider egress drop-in
# and the shadow mode drop-in, and restoring mode=off), then proves the OFF state.
# Advisory evidence is left in place: rollback disables measurement, it does not
# destroy it.
set -uo pipefail

UNIT="opip-committee-shadow.service"
TIMER="opip-committee-shadow.timer"

refuse_harness_path() {
  local label="$1" path="$2" resolved parent
  if [[ -z "$path" ]]; then
    echo "test harness requires ${label}" >&2
    exit 76
  fi
  case "$path" in
    *..*)
      echo "test harness refuses a parent-relative path for ${label}" >&2
      exit 76
      ;;
  esac
  if [[ -d "$path" ]]; then
    parent="$path"
  else
    parent="$(dirname "$path")"
  fi
  if [[ ! -d "$parent" ]]; then
    echo "test harness path for ${label} does not exist" >&2
    exit 76
  fi
  resolved="$(cd "$parent" && pwd -P)"
  if [[ ! -d "$path" ]]; then
    resolved="${resolved}/$(basename "$path")"
  fi
  case "$resolved" in
    /etc|/etc/*|/opt/opip|/opt/opip/*)
      echo "test harness refuses production path for ${label}" >&2
      exit 76
      ;;
  esac
}

configure_committee_paths() {
  if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" ]]; then
    UNIT_DIR="${OPIP_COMMITTEE_UNIT_DIR:-}"
    ENV_FILE="${OPIP_COMMITTEE_ENV_FILE:-}"
    COMMITTEE_HOME="${OPIP_COMMITTEE_HOME:-}"
    EVIDENCE_ROOT="${OPIP_COMMITTEE_EVIDENCE_ROOT:-}"
    RESOLV_CONF="${OPIP_COMMITTEE_RESOLV_CONF:-}"
    refuse_harness_path UNIT_DIR "$UNIT_DIR"
    refuse_harness_path ENV_FILE "$ENV_FILE"
    refuse_harness_path COMMITTEE_HOME "$COMMITTEE_HOME"
    refuse_harness_path EVIDENCE_ROOT "$EVIDENCE_ROOT"
    refuse_harness_path RESOLV_CONF "$RESOLV_CONF"
  else
    UNIT_DIR="/etc/systemd/system"
    ENV_FILE="/etc/opip/committee-credentials.env"
    COMMITTEE_HOME="${OPIP_COMMITTEE_HOME:-/var/lib/opip-committee}"
    EVIDENCE_ROOT="${OPIP_COMMITTEE_EVIDENCE_ROOT:-/var/lib/opip-learning}"
    RESOLV_CONF="/etc/resolv.conf"
  fi
  DROPIN_DIR="$UNIT_DIR/$UNIT.d"
  DROPIN="$DROPIN_DIR/10-provider-egress.conf"
  MODE_DROPIN="$DROPIN_DIR/20-shadow-mode.conf"
}

configure_committee_paths
if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" != "1" ]]; then
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "verify the committee shadow boundary as root" >&2
    exit 77
  fi
fi

#: Credential NAMES only. This script never reads, prints, or compares a value.
PROVIDER_CREDENTIAL_NAMES='OPIP_COMMITTEE_OPENAI_API_KEY|OPIP_COMMITTEE_ANTHROPIC_API_KEY'

#: The approved provider endpoints, in the same order as the activation script.
PROVIDER_ENDPOINTS=(api.openai.com api.anthropic.com)

UNKNOWN_STATE='unknown'

failures=0
pass() { printf 'PASS  %s\n' "$1"; return 0; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); return 0; }
info() { printf 'INFO  %s\n' "$1"; return 0; }

env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$ENV_FILE" 2>/dev/null | head -n1
  return 0
}

# --------------------------------------------------------------- rollback mode
if [[ "${1:-}" == "--rollback" ]]; then
  # Restore OFF first, then prove the resulting state. The order matters: proving
  # OFF before actually returning to OFF would report a state that does not exist
  # yet. Rollback removes both drop-ins. The provider egress allowlist must be
  # ABSENT, so it is proven absent rather than required present. Advisory evidence
  # is not deleted.
  systemctl disable "$TIMER" >/dev/null 2>&1 || true
  systemctl stop "$TIMER" >/dev/null 2>&1 || true
  rm -f "$DROPIN"
  rm -f "$MODE_DROPIN"
  # A renamed drop-in is still SHADOW configuration. Remove any sibling that
  # allowlists egress or forces shadow mode, then prove none remain.
  if [[ -d "$DROPIN_DIR" ]]; then
    shopt -s nullglob
    for conf in "$DROPIN_DIR"/*.conf; do
      if grep -qE '^[[:space:]]*IPAddressAllow=|^[[:space:]]*Environment=OPIP_COMMITTEE_MODE=shadow$' "$conf"; then
        rm -f "$conf"
      fi
    done
    shopt -u nullglob
  fi
  rmdir "$DROPIN_DIR" 2>/dev/null || true
  info "removed the provider egress drop-in and the shadow mode drop-in"
  if [[ -r "$ENV_FILE" ]]; then
    if grep -q '^OPIP_COMMITTEE_MODE=' "$ENV_FILE"; then
      sed -i 's|^OPIP_COMMITTEE_MODE=.*|OPIP_COMMITTEE_MODE=off|' "$ENV_FILE"
    else
      printf 'OPIP_COMMITTEE_MODE=off\n' >> "$ENV_FILE"
    fi
  fi
  systemctl daemon-reload
  PROOF_LABEL="ROLLBACK_PROOF"
  echo "ROLLBACK_APPLIED=off+deny-all"
  mode="$(env_value OPIP_COMMITTEE_MODE)"
  if [[ "$mode" == "off" ]]; then
    pass "mode is off in the environment file"
  else
    fail "mode is '${mode}' in the environment file, expected off after rollback"
  fi
  unit_mode="$(systemctl show -p Environment --value "$UNIT" 2>/dev/null | tr ' ' '\n' | sed -n 's/^OPIP_COMMITTEE_MODE=//p' | head -n1)"
  if [[ "$unit_mode" == "off" ]]; then
    pass "unit-level mode is off"
  else
    fail "unit-level mode '${unit_mode:-none}' disagrees with rollback off"
  fi
  if [[ ! -e "$MODE_DROPIN" ]]; then
    pass "shadow mode drop-in is absent"
  else
    fail "shadow mode drop-in is still present after rollback"
  fi
  if [[ ! -e "$DROPIN" ]]; then
    pass "provider egress drop-in is absent"
  else
    fail "provider egress drop-in is still present after rollback"
  fi
  allow_lines="$(grep -R -E '^[[:space:]]*IPAddressAllow=' "$DROPIN_DIR" 2>/dev/null || true)"
  if [[ -z "$allow_lines" ]]; then
    pass "no provider egress allowlist remains: OFF-mode egress is deny-all again"
  else
    fail "the provider egress allowlist is still present after rollback"
  fi
  unit_deny="$(systemctl show -p IPAddressDeny --value "$UNIT" 2>/dev/null || echo '')"
  if [[ "$unit_deny" == "any" || "$unit_deny" == *"0.0.0.0/0"* ]]; then
    pass "egress default remains deny-all (observed: ${unit_deny:-none})"
  else
    fail "egress default deny is '${unit_deny:-none}'"
  fi
  timer_active="$(systemctl show -p ActiveState --value "$TIMER" 2>/dev/null || echo '')"
  if systemctl is-enabled "$TIMER" >/dev/null 2>&1; then
    fail "the recurring timer is still enabled after rollback"
  elif [[ "$timer_active" != "inactive" ]]; then
    fail "the recurring timer is not inactive after rollback (active: ${timer_active:-none})"
  else
    pass "the recurring timer is not enabled and is inactive after rollback"
  fi
  if [[ -d "$COMMITTEE_HOME" ]]; then
    pass "advisory evidence directory survived rollback"
  else
    fail "advisory evidence directory is missing"
  fi
  echo
  if [[ "$failures" -eq 0 ]]; then
    echo "ROLLBACK_PROOF=PASS"
    exit 0
  fi
  echo "ROLLBACK_PROOF=FAIL failures=$failures"
  exit 1
fi

# ------------------------------------------------------------- SHADOW-mode only
PROOF_LABEL="SHADOW_PROOF"
mode="$(env_value OPIP_COMMITTEE_MODE)"
if [[ "$mode" == "shadow" ]]; then
  pass "mode is shadow in the environment file"
else
  fail "mode is '${mode}' in the environment file, expected shadow"
fi

# ---------------------------------------------- the cycle can actually execute
# Activation must not report PASS on a worker that cannot start one cycle: the
# OFF path returns before it needs the application at all, so a missing or
# non-executable application root is invisible until SHADOW mode is attempted.
app_root="$(sed -n 's/^OPIP_APP_ROOT=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
app_root="${OPIP_APP_ROOT:-${app_root:-/opt/opip/app}}"
venv_python="$(sed -n 's/^OPIP_VENV_PYTHON=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
venv_python="${OPIP_VENV_PYTHON:-${venv_python:-/opt/opip/venv/bin/python}}"
if [[ -d "$app_root" ]]; then
  pass "the worker application root exists: $app_root"
else
  fail "the worker application root does not exist ($app_root); a SHADOW cycle cannot start"
fi
if [[ -x "$venv_python" ]]; then
  pass "the worker interpreter is executable: $venv_python"
else
  fail "the worker interpreter is not executable ($venv_python); a SHADOW cycle cannot start"
fi
if [[ -d "$app_root" && -x "$venv_python" ]]; then
  if ( cd "$app_root" && "$venv_python" -c 'import app.opip.committee.cycle_runner' ) 2>/dev/null; then
    pass "the worker interpreter can import the committee cycle runner"
  else
    fail "the worker interpreter cannot import the committee cycle runner from $app_root"
  fi
fi

# ------------------------------------------------------------- egress allowlist
allow_lines="$(grep -E '^[[:space:]]*IPAddressAllow=' "$DROPIN" 2>/dev/null || true)"
if [[ -n "$allow_lines" ]]; then
  pass "provider egress allowlist is installed ($(printf '%s\n' "$allow_lines" | grep -c . ) entries)"
else
  fail "no provider egress allowlist is installed; SHADOW egress would be denied outright"
fi

# Every allowlisted address must still resolve from one of the approved endpoints.
# An address that no longer belongs to a provider is a widened boundary, not a
# stale one, so it fails closed.
expected_addresses="$(
  {
    for endpoint in "${PROVIDER_ENDPOINTS[@]}"; do
      getent ahosts "$endpoint" 2>/dev/null | awk '{print $1}' || true
    done
    # Resolution has to survive deny-all, so the host's configured resolvers are
    # legitimately allowlisted alongside the provider addresses.
    awk '/^[[:space:]]*nameserver[[:space:]]+/ {print $2}' "$RESOLV_CONF" 2>/dev/null || true
    if grep -qE '^[[:space:]]*nameserver[[:space:]]+(127\.|::1)' "$RESOLV_CONF" 2>/dev/null; then
      printf '127.0.0.1\n::1\n'
    fi
  } | sort -u
)"
unexpected=0
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  address="${line#IPAddressAllow=}"
  address="${address%% *}"
  if ! printf '%s\n' "$expected_addresses" | grep -qx "$address"; then
    unexpected=$((unexpected + 1))
  fi
done <<< "$allow_lines"
if [[ "$unexpected" -eq 0 ]]; then
  pass "every allowlisted address belongs to an approved provider endpoint"
else
  fail "$unexpected allowlisted address(es) do not belong to an approved endpoint"
fi

unit_deny="$(systemctl show -p IPAddressDeny --value "$UNIT" 2>/dev/null || echo '')"
if [[ "$unit_deny" == "any" || "$unit_deny" == *"0.0.0.0/0"* ]]; then
  pass "egress default remains deny-all (observed: ${unit_deny:-none})"
else
  fail "egress default deny is '${unit_deny:-none}'; the allowlist must be an exception"
fi

# --------------------------------------------------------------- mode at unit level
unit_mode="$(systemctl show -p Environment --value "$UNIT" 2>/dev/null | tr ' ' '\n' | sed -n 's/^OPIP_COMMITTEE_MODE=//p' | head -n1)"
file_mode="$(env_value OPIP_COMMITTEE_MODE)"
if [[ "$unit_mode" == "$file_mode" ]]; then
  pass "unit-level mode agrees with the environment file (${file_mode:-none})"
else
  fail "unit-level mode '${unit_mode:-none}' disagrees with the environment file '${file_mode:-none}'"
fi
base_unit="$UNIT_DIR/$UNIT"
if [[ -f "$MODE_DROPIN" ]] \
  && grep -qx 'Environment=OPIP_COMMITTEE_MODE=shadow' "$MODE_DROPIN" \
  && [[ -f "$base_unit" ]] \
  && grep -qx 'Environment=OPIP_COMMITTEE_MODE=off' "$base_unit"; then
  pass "shadow mode drop-in overrides the base unit, which stays off"
else
  fail "shadow mode drop-in is absent or does not keep the base unit off"
fi

# ------------------------------------------------------------- activation boundary
not_before="$(env_value OPIP_COMMITTEE_SHADOW_NOT_BEFORE)"
if [[ -n "$not_before" ]]; then
  pass "an explicit UTC activation boundary is recorded"
else
  fail "no activation boundary is recorded; historical contexts could be backfilled"
fi
review_by="$(env_value OPIP_COMMITTEE_REGISTRY_REVIEW_BY)"
if [[ -n "$review_by" ]]; then
  pass "a registry review date is recorded"
else
  fail "no registry review date is recorded; approved routes would never be re-reviewed"
fi
cycle_cap="$(env_value OPIP_COMMITTEE_MAX_CASES_PER_CYCLE)"
if [[ "$cycle_cap" == "1" ]]; then
  pass "a cycle is capped at one case"
else
  fail "cycle case cap is '${cycle_cap:-unset}', expected 1"
fi
release="$(env_value OPIP_COMMITTEE_RELEASE_SHA)"
if [[ "$release" =~ ^[0-9a-f]{40}$ ]]; then
  pass "release identity is an exact 40-character SHA"
else
  fail "release identity is not an exact 40-character SHA"
fi

# --------------------------------------------------------------- no trading credentials
if [[ -r "$ENV_FILE" ]]; then
  trading_hits="$(grep -ciE 'kraken|telegram|webhook|trading|order' "$ENV_FILE" || true)"
  if [[ "${trading_hits:-0}" -eq 0 ]]; then
    pass "no Kraken/Telegram/trading credential name appears in the worker environment"
  else
    fail "$trading_hits trading-credential names appear in the worker environment"
  fi
fi

# ----------------------------------------------- provider credentials not in diagnostics
show_output="$(systemctl show "$UNIT" 2>/dev/null || echo '')"
if printf '%s' "$show_output" | grep -qE "$PROVIDER_CREDENTIAL_NAMES"; then
  fail "systemctl show exposes a provider credential name for the unit"
else
  pass "systemctl show exposes no provider credential name"
fi

# --------------------------------------------------------------- no inbound listener
listening="$(ss -H -lntp 2>/dev/null | grep -c "committee" || true)"
if [[ "${listening:-0}" -eq 0 ]]; then
  pass "no listening socket is owned by a committee process"
else
  fail "$listening listening sockets appear to belong to a committee process"
fi

# --------------------------------------------------------------- advisory output isolated
perms="$(stat -c '%a %U:%G' "$COMMITTEE_HOME" 2>/dev/null || echo 'missing')"
if [[ "$perms" == 750* || "$perms" == 700* ]]; then
  pass "advisory directory is restricted: $perms"
else
  fail "advisory directory permissions are '$perms', expected 750 or 700 root-owned"
fi

# --------------------------------------------------------------- timer / service state
timer_enabled="$(systemctl is-enabled "$TIMER" 2>/dev/null || true)"
timer_active="$(systemctl show -p ActiveState --value "$TIMER" 2>/dev/null || echo "$UNKNOWN_STATE")"
timer_cap="$(env_value OPIP_COMMITTEE_MAX_CASES_PER_CYCLE)"
if [[ "$timer_enabled" == "enabled" ]]; then
  if [[ "$timer_cap" == "1" ]]; then
    # A recurring timer is only acceptable with the conservative cap still in force.
    pass "recurring timer is enabled with a one-case cycle cap"
  else
    fail "recurring timer is enabled without a one-case cycle cap"
  fi
else
  pass "recurring timer is not enabled (state: ${timer_enabled:-none}, active: ${timer_active})"
fi

# --------------------------------------------------------------- evidence survival
if [[ -d "$COMMITTEE_HOME" ]]; then
  role_rows=0
  [[ -f "$COMMITTEE_HOME/role_results.jsonl" ]] && role_rows="$(wc -l < "$COMMITTEE_HOME/role_results.jsonl")"
  role_cases=0
  [[ -f "$COMMITTEE_HOME/role_case_outcomes.jsonl" ]] && role_cases="$(wc -l < "$COMMITTEE_HOME/role_case_outcomes.jsonl")"
  info "advisory evidence present: role_results=${role_rows} role_case_outcomes=${role_cases}"
  pass "advisory evidence directory is present and was not removed"
else
  fail "advisory evidence directory is missing"
fi

echo
if [[ "$failures" -eq 0 ]]; then
  echo "${PROOF_LABEL}=PASS"
  exit 0
fi
echo "${PROOF_LABEL}=FAIL failures=$failures"
exit 1