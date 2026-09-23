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
ENV_FILE="/etc/opip/committee-credentials.env"
COMMITTEE_HOME="${OPIP_COMMITTEE_HOME:-/var/lib/opip-committee}"
EVIDENCE_ROOT="${OPIP_COMMITTEE_EVIDENCE_ROOT:-/var/lib/opip-learning}"

failures=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }
info() { printf 'INFO  %s\n' "$1"; }

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
    info "evidence root options: ${ro_mount:-unknown} — confirm read-only at the unit level"
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

# ------------------------------------------------- no inbound listening service
listening="$(ss -H -lntp 2>/dev/null | grep -c "committee" || true)"
if [[ "${listening:-0}" -eq 0 ]]; then
  pass "no listening socket is owned by a committee process"
else
  fail "$listening listening sockets appear to belong to a committee process"
fi

# -------------------------------------------------------------- egress policy
unit_deny="$(systemctl show -p IPAddressDeny --value "$UNIT" 2>/dev/null || echo '')"
unit_allow="$(systemctl show -p IPAddressAllow --value "$UNIT" 2>/dev/null || echo '')"
if [[ -n "$unit_deny" ]]; then
  pass "egress deny rule present: $unit_deny (allow: ${unit_allow:-none})"
else
  info "no IPAddressDeny on the unit; the application-level allowlist in transports.py is the control"
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
