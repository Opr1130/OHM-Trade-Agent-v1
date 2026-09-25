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
# and restoring mode=off), then proves the OFF state. Advisory evidence is left in
# place: rollback disables measurement, it does not destroy it.
set -uo pipefail

UNIT="opip-committee-shadow.service"
TIMER="opip-committee-shadow.timer"
UNIT_DIR="/etc/systemd/system"
DROPIN_DIR="$UNIT_DIR/$UNIT.d"
DROPIN="$DROPIN_DIR/10-provider-egress.conf"
ENV_FILE="/etc/opip/committee-credentials.env"
COMMITTEE_HOME="${OPIP_COMMITTEE_HOME:-/var/lib/opip-committee}"

#: Credential NAMES only. This script never reads, prints, or compares a value.
PROVIDER_CREDENTIAL_NAMES='OPIP_COMMITTEE_OPENAI_API_KEY|OPIP_COMMITTEE_ANTHROPIC_API_KEY'

#: The approved provider endpoints, in the same order as the activation script.
PROVIDER_ENDPOINTS=(api.openai.com api.anthropic.com)

UNKNOWN_STATE='unknown'

failures=0
pass() { printf 'PASS  %s\n' "$1"; return 0; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); return 0; }
info() { printf 'INFO  %s\n' "$1"; return 0; }

env_value() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | head -n1; }

# --------------------------------------------------------------- rollback mode
if [[ "${1:-}" == "--rollback" ]]; then
  # Restore OFF first, then fall through to prove the resulting state. The order
  # matters: proving OFF before actually returning to OFF would report a state
  # that does not exist yet.
  if [[ -f "$DROPIN" ]]; then
    rm -f "$DROPIN"
    rmdir "$DROPIN_DIR" 2>/dev/null || true
    info "removed the provider egress drop-in"
  fi
  if [[ -r "$ENV_FILE" ]]; then
    if grep -q '^OPIP_COMMITTEE_MODE=' "$ENV_FILE"; then
      sed -i 's|^OPIP_COMMITTEE_MODE=.*|OPIP_COMMITTEE_MODE=off|' "$ENV_FILE"
    else
      printf 'OPIP_COMMITTEE_MODE=off\n' >> "$ENV_FILE"
    fi
  fi
  systemctl disable "$TIMER" >/dev/null 2>&1 || true
  systemctl stop "$TIMER" >/dev/null 2>&1 || true
  systemctl daemon-reload
  PROOF_LABEL="ROLLBACK_PROOF"
  echo "ROLLBACK_APPLIED=off+deny-all"
else
  PROOF_LABEL="SHADOW_PROOF"
  mode="$(env_value OPIP_COMMITTEE_MODE)"
  if [[ "$mode" == "shadow" ]]; then
    pass "mode is shadow in the environment file"
  else
    fail "mode is '${mode}' in the environment file, expected shadow"
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
  for endpoint in "${PROVIDER_ENDPOINTS[@]}"; do
    getent ahosts "$endpoint" 2>/dev/null | awk '{print $1}' || true
  done | sort -u
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
