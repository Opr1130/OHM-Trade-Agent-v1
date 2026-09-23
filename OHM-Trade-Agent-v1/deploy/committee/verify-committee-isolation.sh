#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — OFF-mode isolation proof (IC-042).
#
# RUNS ON THE LEARNING/ANALYTICS PLANE, AS ROOT. Prints one line per check with an
# explicit PASS/FAIL, then a machine-readable summary. A FAIL is a real finding and
# must be resolved before any credentialled SHADOW validation.
#
# This proves the worker is installed and inert. It does not activate anything.
set -uo pipefail

UNIT="opip-committee-shadow.service"
TIMER="opip-committee-shadow.timer"
UNIT_DIR="/etc/systemd/system"
ENV_FILE="/etc/opip/committee-credentials.env"
COMMITTEE_HOME="${OPIP_COMMITTEE_HOME:-/var/lib/opip-committee}"
EVIDENCE_ROOT="${OPIP_COMMITTEE_EVIDENCE_ROOT:-/var/lib/opip-learning}"

#: Provider credential variable names. Only the NAMES are referenced, never a value:
#: this script must never print a credential, an environment file, or the environment.
PROVIDER_CREDENTIAL_NAMES='OPIP_COMMITTEE_OPENAI_API_KEY|OPIP_COMMITTEE_ANTHROPIC_API_KEY'

#: Reported when a systemd property cannot be read. A single constant keeps the
#: wording identical at every call site, so no check can drift from the others.
UNKNOWN_STATE='unknown'

failures=0

pass() { printf 'PASS  %s\n' "$1"; return 0; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); return 0; }
info() { printf 'INFO  %s\n' "$1"; return 0; }

#: True when a systemd `IPAddressDeny` value denies every address in both families.
#:
#: The committed unit declares `IPAddressDeny=any`, but `systemctl show` normalises
#: that to the two all-addresses entries (`0.0.0.0/0 ::/0`). Accept the literal
#: source spelling and the normalised form, and require BOTH families explicitly so a
#: partial policy (IPv4-only or IPv6-only) cannot pass.
egress_denies_everything() {
  local deny_value="$1"
  local -a tokens=()
  # shellcheck disable=SC2206
  read -r -a tokens <<< "$deny_value"
  local token saw_v4="false" saw_v6="false"
  for token in ${tokens[@]+"${tokens[@]}"}; do
    case "$token" in
      # `any` is total on its own: it covers both families.
      any) return 0 ;;
      0.0.0.0/0) saw_v4="true" ;;
      ::/0) saw_v6="true" ;;
    esac
  done
  [[ "$saw_v4" == "true" && "$saw_v6" == "true" ]]
}

#: Enablement states that mean scheduled execution is active. Any of these fails.
TIMER_ENABLED_STATES='enabled enabled-runtime linked linked-runtime alias indirect'

#: Enablement states that mean no scheduled execution. Any of these is acceptable.
TIMER_INERT_STATES='disabled masked masked-runtime static not-found generated transient'

#: Classify `systemctl is-enabled` for the timer as enabled | not_enabled | unknown.
#:
#: `systemctl is-enabled` prints `disabled` and still exits non-zero, so the exit
#: status is captured separately rather than used as an `|| echo` fallback, which
#: appended a second line and corrupted the value. Only the first non-empty line is
#: the verdict. A zero exit status means the unit IS enabled regardless of the text,
#: so an enabled timer cannot be hidden by output handling. Anything unrecognised
#: fails closed rather than passing by default.
timer_enablement_verdict() {
  local status="$1"
  local raw="$2"
  local state=""
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then
      state="$line"
      break
    fi
  done <<< "$raw"
  if [[ "$status" -eq 0 ]]; then
    printf 'enabled\n'
    return 0
  fi
  local candidate
  for candidate in $TIMER_ENABLED_STATES; do
    if [[ "$state" == "$candidate" ]]; then
      printf 'enabled\n'
      return 0
    fi
  done
  for candidate in $TIMER_INERT_STATES; do
    if [[ "$state" == "$candidate" ]]; then
      printf 'not_enabled\n'
      return 0
    fi
  done
  if [[ -z "$state" ]]; then
    # No enablement state was reported. Treated as not-enabled, matching the previous
    # contract; the installed timer FILE is asserted separately below, so a genuinely
    # missing timer cannot slip through on this path.
    printf 'not_enabled\n'
    return 0
  fi
  printf 'unknown\n'
  return 0
}

# When sourced for testing, stop after the definitions: everything below asserts the
# state of a real host and must not run in a test process.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  return 0
fi

# --------------------------------------------------------------- release identity
expected_sha="$(sed -n 's/^OPIP_COMMITTEE_RELEASE_SHA=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
if [[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]]; then
  pass "release identity is an exact 40-character SHA: $expected_sha"
else
  fail "release identity is not an exact 40-character SHA (got '${expected_sha}')"
fi

# ---------------------------------------------------------------------- mode
mode="$(sed -n 's/^OPIP_COMMITTEE_MODE=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
unit_mode="$(systemctl show -p Environment --value "$UNIT" 2>/dev/null | tr ' ' '\n' | sed -n 's/^OPIP_COMMITTEE_MODE=//p' | head -n1)"
if [[ "$mode" == "off" ]]; then
  pass "mode is off in the environment file"
else
  fail "mode is '${mode}', not off"
fi
if [[ "$unit_mode" == "off" ]]; then
  pass "mode is off at the unit level (defence in depth)"
else
  fail "unit-level mode is '${unit_mode}', not off"
fi

# ------------------------------------------------------- zero provider calls
# The worker records an explicit disposition on every cycle, so a cycle that ran is
# visible. An off cycle creates no case and no call outcome.
journal_hits="$(journalctl -u "$UNIT" --since '7 days ago' --no-pager 2>/dev/null | grep -c 'committee cycle skipped: OPIP_COMMITTEE_MODE=off' || true)"
failures_file="$COMMITTEE_HOME/cycle_dispositions.jsonl"
if [[ -f "$failures_file" ]]; then
  non_off="$(grep -vc '"disposition":"SKIPPED_MODE_OFF"' "$failures_file" || true)"
  info "committee cycles recorded: $(wc -l < "$failures_file") ($journal_hits off-skips in the journal)"
  if [[ "${non_off:-0}" -eq 0 ]]; then
    pass "every recorded cycle is an OFF skip; zero cycles produced committee work"
  else
    fail "${non_off} recorded cycles were not OFF skips"
  fi
else
  info "no cycle dispositions recorded yet (the timer may not be started)"
fi

# No call outcomes, case outcomes, or evaluations can exist while off.
for artifact in call_outcomes.jsonl case_outcomes.jsonl evaluations.jsonl prospective.jsonl; do
  if [[ -s "$COMMITTEE_HOME/$artifact" ]]; then
    fail "$artifact is non-empty, which indicates committee work occurred while off"
  else
    pass "$artifact is absent or empty (no committee work)"
  fi
done

# ------------------------------------------------ evidence input is read-only
if compgen -G '/proc/*/mountinfo' >/dev/null; then
  ro_mount="$(findmnt -no OPTIONS -T "$EVIDENCE_ROOT" 2>/dev/null || echo '')"
  if [[ "$ro_mount" == *ro* || "$ro_mount" == *"read-only"* ]]; then
    pass "evidence root is mounted read-only: $EVIDENCE_ROOT"
  else
    info "evidence root options: ${ro_mount:-$UNKNOWN_STATE} — confirm read-only at the unit level"
  fi
fi
unit_ro="$(systemctl show -p ReadOnlyPaths --value "$UNIT" 2>/dev/null || echo '')"
if [[ "$unit_ro" == *"$EVIDENCE_ROOT"* ]]; then
  pass "unit declares the evidence root read-only"
else
  fail "unit does not declare $EVIDENCE_ROOT in ReadOnlyPaths"
fi

# ------------------------------------------------------ advisory output isolated
perms="$(stat -c '%a %U:%G' "$COMMITTEE_HOME" 2>/dev/null || echo 'missing')"
if [[ "$perms" == 750* || "$perms" == 700* ]]; then
  pass "advisory directory is restricted: $perms"
else
  fail "advisory directory permissions are '$perms', expected 750 or 700 root-owned"
fi
unit_rw="$(systemctl show -p ReadWritePaths --value "$UNIT" 2>/dev/null || echo '')"
if [[ "$unit_rw" == *"$COMMITTEE_HOME"* ]]; then
  pass "unit declares the advisory directory writable"
else
  fail "unit does not declare $COMMITTEE_HOME in ReadWritePaths"
fi

# --------------------------------------- no trading credentials in the environment
if [[ -r "$ENV_FILE" ]]; then
  trading_hits="$(grep -ciE 'kraken|telegram|webhook|trading|order' "$ENV_FILE" || true)"
  if [[ "${trading_hits:-0}" -eq 0 ]]; then
    pass "no Kraken/Telegram/trading credential name appears in the worker environment"
  else
    fail "$trading_hits trading-credential names appear in the worker environment"
  fi
fi
service_env="$(systemctl show -p Environment --value "$UNIT" 2>/dev/null || echo '')"
if [[ "$service_env" =~ [Kk]raken|[Tt]elegram ]]; then
  fail "the unit environment names a trading credential"
else
  pass "the unit environment names no trading credential"
fi

# ------------------------------- provider credentials absent from diagnostics
# Item 4: prove that no provider credential is exposed through the systemd
# diagnostic surfaces. Three distinct surfaces are checked separately so a clean
# result on one cannot mask a leak on another:
#
#   (a) the unit's own `Environment=` property, which `systemctl show` prints;
#   (b) the installed unit file text, which `systemctl cat`/journal reporting may
#       reproduce, and which must reference a file rather than inline a value;
#   (c) the unit's journal, which is the diagnostic output an operator reads.
#
# Only counts and PASS/FAIL are printed. A matching line is NEVER echoed, because a
# match could be a live credential value. Nothing here reads or prints the
# environment-file contents.
diag_hits=0

show_output="$(systemctl show "$UNIT" 2>/dev/null || echo '')"
if printf '%s' "$show_output" | grep -qE "$PROVIDER_CREDENTIAL_NAMES"; then
  fail "systemctl show exposes a provider credential name for the unit"
  diag_hits=$((diag_hits + 1))
else
  pass "systemctl show exposes no provider credential name"
fi

cat_output="$(systemctl cat "$UNIT" 2>/dev/null || echo '')"
if printf '%s' "$cat_output" | grep -qE "$PROVIDER_CREDENTIAL_NAMES"; then
  fail "the unit file text exposes a provider credential name"
  diag_hits=$((diag_hits + 1))
else
  pass "the unit file text exposes no provider credential name"
fi
# A value inlined into `Environment=` would be readable by any local user permitted to
# run `systemctl show`. The credential must arrive only through an EnvironmentFile.
if printf '%s' "$show_output" | grep -qE '^Environment=.*(sk-|api[_-]?key=)'; then
  fail "the unit inlines a credential-looking value in Environment="
  diag_hits=$((diag_hits + 1))
else
  pass "no credential-looking value is inlined in the unit Environment="
fi
if grep -qE '^[[:space:]]*EnvironmentFile=-?/etc/opip/committee-credentials\.env' "$UNIT_DIR/$UNIT" 2>/dev/null; then
  pass "credentials are supplied only by file reference (EnvironmentFile=)"
else
  fail "the unit does not declare the committee EnvironmentFile"
fi

journal_output="$(journalctl -u "$UNIT" --since '30 days ago' --no-pager 2>/dev/null || echo '')"
journal_lines="$(printf '%s' "$journal_output" | grep -cE "$PROVIDER_CREDENTIAL_NAMES" || true)"
if [[ "${journal_lines:-0}" -eq 0 ]]; then
  pass "service diagnostics emit no provider credential name (journal lines checked)"
else
  fail "$journal_lines diagnostic lines mention a provider credential name"
  diag_hits=$((diag_hits + 1))
fi
if printf '%s' "$journal_output" | grep -qE '(sk-[A-Za-z0-9]{16,}|api[_-]?key=[^[:space:]]{8,})'; then
  fail "service diagnostics appear to contain a credential-shaped value"
  diag_hits=$((diag_hits + 1))
else
  pass "service diagnostics contain no credential-shaped value"
fi
if [[ "$diag_hits" -eq 0 ]]; then
  pass "no provider credential is exposed through service diagnostics"
fi

# ------------------------------------------------- no inbound listening service
listening="$(ss -H -lntp 2>/dev/null | grep -c "committee" || true)"
if [[ "${listening:-0}" -eq 0 ]]; then
  pass "no listening socket is owned by a committee process"
else
  fail "$listening listening sockets appear to belong to a committee process"
fi

# -------------------------------------------------------------- egress policy
# OFF-mode boundary is deny-all, fail-closed. The worker must make no provider
# egress while mode is off, so an allowlist must not be present: provider-specific
# egress belongs to the later, separately OWNER-authorised SHADOW activation.
unit_deny="$(systemctl show -p IPAddressDeny --value "$UNIT" 2>/dev/null || echo '')"
unit_allow="$(systemctl show -p IPAddressAllow --value "$UNIT" 2>/dev/null || echo '')"
if egress_denies_everything "$unit_deny"; then
  pass "egress is deny-all at the unit level (observed: ${unit_deny:-none})"
else
  fail "egress deny rule '${unit_deny:-none}' does not deny every address in both families"
fi
if [[ -z "$unit_allow" ]]; then
  pass "no egress allowlist entries: OFF-mode egress fails closed"
else
  fail "IPAddressAllow is non-empty ('$unit_allow'); provider egress must stay closed while OFF"
fi
# Prove it from the installed unit file too, so a stale daemon view cannot mask it.
if grep -qE '^[[:space:]]*IPAddressAllow=' "$UNIT_DIR/$UNIT" 2>/dev/null; then
  fail "the installed unit declares an IPAddressAllow entry"
else
  pass "the installed unit declares no IPAddressAllow entry"
fi
# The file must carry the deny explicitly, so the boundary survives a daemon reload.
if grep -qE '^[[:space:]]*IPAddressDeny=any[[:space:]]*$' "$UNIT_DIR/$UNIT" 2>/dev/null; then
  pass "the installed unit declares IPAddressDeny=any"
else
  fail "the installed unit does not declare IPAddressDeny=any"
fi

# ------------------------------------------- scheduled execution is off
# The initial OFF installation must leave the timer disabled and inactive, and the
# oneshot service must not be continuously active: no recurring committee work runs.
# The installed timer FILE is asserted too, so an unrecognised/empty enablement state
# cannot mask a genuinely missing timer.
if [[ -f "$UNIT_DIR/$TIMER" ]]; then
  pass "the timer unit file is installed"
else
  fail "the timer unit file is not installed"
fi
timer_enabled_raw="$(systemctl is-enabled "$TIMER" 2>/dev/null)"
timer_enabled_rc=$?
timer_verdict="$(timer_enablement_verdict "$timer_enabled_rc" "$timer_enabled_raw")"
timer_state_label="${timer_enabled_raw//$'\n'/ }"
case "$timer_verdict" in
  enabled)
    fail "timer is enabled (state: ${timer_state_label:-none}): scheduled committee execution is active"
    ;;
  not_enabled)
    pass "timer is not enabled (state: ${timer_state_label:-none}): no scheduled committee execution"
    ;;
  *)
    fail "timer enablement state is unrecognised (state: ${timer_state_label:-none}, rc=$timer_enabled_rc)"
    ;;
esac
timer_active="$(systemctl show -p ActiveState --value "$TIMER" 2>/dev/null || echo "$UNKNOWN_STATE")"
if [[ "$timer_active" == "inactive" || "$timer_active" == "$UNKNOWN_STATE" ]]; then
  pass "timer is inactive (state: $timer_active)"
else
  fail "timer active state is '$timer_active', expected inactive"
fi
service_active="$(systemctl show -p ActiveState --value "$UNIT" 2>/dev/null || echo "$UNKNOWN_STATE")"
service_sub="$(systemctl show -p SubState --value "$UNIT" 2>/dev/null || echo "$UNKNOWN_STATE")"
if [[ "$service_active" == "inactive" ]]; then
  pass "service is not continuously active (oneshot: $service_active/$service_sub)"
else
  fail "service active state is '$service_active' ($service_sub); expected inactive for OFF"
fi

# ------------------------------------------------------------ resource limits
mem="$(systemctl show -p MemoryMax --value "$UNIT" 2>/dev/null || echo '')"
cpu="$(systemctl show -p CPUQuota --value "$UNIT" 2>/dev/null || echo '')"
if [[ -n "$mem" && "$mem" != "infinity" ]]; then
  pass "memory limit applied: $mem (cpu ${cpu:-unset})"
else
  fail "no memory limit is applied to the worker"
fi

# --------------------------------------------------- rollback / disable path
if systemctl is-enabled "$TIMER" >/dev/null 2>&1; then
  info "timer is enabled; rollback is: systemctl disable --now $TIMER"
else
  info "timer is not enabled; rollback is already the current state"
fi
if systemctl cat "$UNIT" >/dev/null 2>&1; then
  pass "disable path is available (systemctl disable --now $TIMER)"
else
  fail "the unit is not installed, so the disable path cannot be exercised"
fi
# A disable path is only real if the timer unit can be disabled: it must carry an
# [Install] section, and the oneshot service must be installed alongside it.
if grep -qE '^\[Install\]' "$UNIT_DIR/$TIMER" 2>/dev/null; then
  pass "rollback path is valid: the timer unit declares an [Install] section"
else
  fail "the timer unit has no [Install] section, so disable would not be meaningful"
fi

# --------------------------------------------------- trading runtime unchanged
if command -v docker >/dev/null 2>&1; then
  committee_containers="$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -ci committee || true)"
  if [[ "${committee_containers:-0}" -eq 0 ]]; then
    pass "no committee container is running (the worker is a systemd unit, absent from the trading stacks)"
  else
    fail "$committee_containers committee containers are running"
  fi
fi

echo
if [[ "$failures" -eq 0 ]]; then
  echo "ISOLATION_PROOF=PASS"
  exit 0
fi
echo "ISOLATION_PROOF=FAIL failures=$failures"
exit 1
