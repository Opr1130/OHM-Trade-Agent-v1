#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — OFF-mode runtime readiness proof (Module 2H).
#
# Independent of the provisioner. Prints PASS/FAIL lines and
# COMMITTEE_RUNTIME_PROOF=PASS or FAIL. Does not activate SHADOW, does not read
# provider credential values into output, and does not change trading authority.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=verify-committee-isolation.sh
source "$SCRIPT_DIR/verify-committee-isolation.sh"

failures=0

normalize_dir() {
  mkdir -p "$1"
  (cd "$1" && pwd)
}

normalize_file() {
  local dir base
  dir="$(dirname "$1")"
  base="$(basename "$1")"
  mkdir -p "$dir"
  printf '%s/%s\n' "$(cd "$dir" && pwd)" "$base"
}

if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" ]]; then
  PREFIX="$(normalize_dir "${OPIP_RUNTIME_PREFIX:?}")"
  ENV_FILE="$(normalize_file "${OPIP_COMMITTEE_ENV_FILE:?}")"
  MANIFEST="$(normalize_file "${OPIP_TEST_MANIFEST:?}")"
  REPLICA="$(normalize_dir "${OPIP_TEST_REPLICA:?}")"
  UNIT_FILE="$(normalize_file "${OPIP_TEST_UNIT_FILE:?}")"
  SBIN_DIR="$PREFIX/sbin"
  DROPIN_DIR="$PREFIX/dropin"
  COMMITTEE_HOME="$PREFIX/committee-home"
  case "$PREFIX" in
    /opt/opip|/opt/opip/*|/etc|/etc/*)
      echo "test harness cannot target the production runtime prefix" >&2
      exit 76
      ;;
  esac
else
  PREFIX=/opt/opip
  ENV_FILE=/etc/opip/committee-credentials.env
  MANIFEST=/var/lib/opip-learning/data/manifest.env
  REPLICA=/var/lib/opip-learning/canonical-replica
  UNIT_FILE=/etc/systemd/system/opip-committee-shadow.service
  SBIN_DIR=/usr/local/sbin
  DROPIN_DIR=/etc/systemd/system/opip-committee-shadow.service.d
  COMMITTEE_HOME=/var/lib/opip-committee
fi
APP_ROOT="$PREFIX/app"
VENV_PYTHON="$PREFIX/venv/bin/python"

env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$ENV_FILE" 2>/dev/null | head -n1
}

if [[ -d "$APP_ROOT" && ! -L "$APP_ROOT" ]]; then
  pass "application root exists"
else
  fail "application root is missing"
fi

file_sha=""
if [[ -f "$APP_ROOT/.opip-release-sha" && ! -L "$APP_ROOT/.opip-release-sha" ]]; then
  file_sha="$(tr -d '[:space:]' < "$APP_ROOT/.opip-release-sha")"
fi
env_sha="$(env_value OPIP_COMMITTEE_RELEASE_SHA)"
if [[ "$file_sha" =~ ^[0-9a-f]{40}$ && "$file_sha" == "$env_sha" ]]; then
  pass "release identity matches the authorized SHA $file_sha"
else
  fail "release identity does not match an exact SHA"
fi

if [[ -e "$VENV_PYTHON" && -x "$VENV_PYTHON" ]]; then
  pass "virtualenv interpreter is executable"
else
  fail "virtualenv interpreter is not executable"
fi

import_log="$(mktemp)"
chmod 0600 "$import_log"
import_ok=0
if [[ -d "$APP_ROOT" && -x "$VENV_PYTHON" ]]; then
  if (
    cd "$APP_ROOT" &&
      env -u PYTHONPATH -u PYTHONHOME -u PYTHONSTARTUP -u PYTHONUSERBASE \
        "$VENV_PYTHON" -s -c 'import os, sys
root = os.path.realpath(os.getcwd())
entry = sys.path[0]
resolved = root if entry in ("", ".") else os.path.realpath(entry)
if os.path.realpath(resolved) != root:
    raise SystemExit(2)
import app.opip.committee.cycle_runner'
  ) >"$import_log" 2>&1; then
    import_ok=1
  fi
fi
if [[ "$import_ok" -eq 1 ]]; then
  pass "cycle_runner import succeeded"
else
  if grep -qE 'OPIP_COMMITTEE_OPENAI_API_KEY=|OPIP_COMMITTEE_ANTHROPIC_API_KEY=|sk-' "$import_log" 2>/dev/null; then
    fail "cycle_runner import failed (output suppressed)"
  else
    fail "cycle_runner import failed"
  fi
fi
rm -f "$import_log"

if [[ "$(env_value OPIP_APP_ROOT)" == "$APP_ROOT" ]]; then
  pass "OPIP_APP_ROOT points at the application root"
else
  fail "OPIP_APP_ROOT is not the application root"
fi
if [[ "$(env_value OPIP_VENV_PYTHON)" == "$VENV_PYTHON" ]]; then
  pass "OPIP_VENV_PYTHON points at the virtualenv interpreter"
else
  fail "OPIP_VENV_PYTHON is not the virtualenv interpreter"
fi
if [[ "$(env_value OPIP_COMMITTEE_LEARNING_MANIFEST)" == "$MANIFEST" && -f "$MANIFEST" && -r "$MANIFEST" && ! -L "$MANIFEST" ]]; then
  pass "learning manifest path is readable"
else
  fail "learning manifest path is not a readable file"
fi
if [[ "$(env_value OPIP_CANONICAL_REPLICA_ROOT_HOST)" == "$REPLICA" && -d "$REPLICA" && ! -L "$REPLICA" ]]; then
  pass "canonical replica root is present"
else
  fail "canonical replica root is absent"
fi
if [[ "$(env_value OPIP_COMMITTEE_MODE)" == "off" ]]; then
  pass "mode is off"
else
  fail "mode is not off"
fi
if [[ "$(env_value OPIP_COMMITTEE_MAX_CASES_PER_CYCLE)" == "1" ]]; then
  pass "max cases per cycle is 1"
else
  fail "max cases per cycle is not 1"
fi
if awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/ { count[$1]++ } END { for (key in count) if (count[key] > 1) exit 1 }' "$ENV_FILE"; then
  pass "environment file has no duplicate keys"
else
  fail "environment file has duplicate keys"
fi
if [[ -r "$ENV_FILE" ]]; then
  trading_hits="$(grep -ciE 'kraken|telegram' "$ENV_FILE" || true)"
  if [[ "${trading_hits:-0}" -eq 0 ]]; then
    pass "no Kraken or Telegram credential name is present"
  else
    fail "trading credential names are present in the worker environment"
  fi
fi

if [[ -f "$UNIT_FILE" ]] && grep -q '^Environment=OPIP_COMMITTEE_MODE=off$' "$UNIT_FILE"; then
  pass "unit forces mode off"
else
  fail "unit does not force mode off"
fi
if [[ -f "$UNIT_FILE" ]] && grep -q '^IPAddressDeny=any$' "$UNIT_FILE"; then
  pass "installed unit declares deny-all egress"
else
  fail "installed unit does not declare deny-all egress"
fi
if [[ -f "$UNIT_FILE" ]] && grep -q '^IPAddressAllow=' "$UNIT_FILE"; then
  fail "installed unit opens an egress allowlist"
else
  pass "installed unit has no egress allowlist"
fi
if [[ -d "$DROPIN_DIR" ]] && grep -R -q -E '^[[:space:]]*IPAddressAllow=' "$DROPIN_DIR"; then
  fail "a drop-in opens provider egress"
else
  pass "no provider egress drop-in is installed"
fi

if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" ]]; then
  timer_state="$(head -n 1 "$PREFIX/timer-enablement" 2>/dev/null || true)"
  timer_active="$(head -n 1 "$PREFIX/timer-active" 2>/dev/null || true)"
  if [[ "$timer_state" == "disabled" ]]; then
    pass "timer is disabled"
  else
    fail "timer is not disabled"
  fi
  if [[ "$timer_active" == "inactive" ]]; then
    pass "timer is inactive"
  else
    fail "timer is not inactive"
  fi
else
  timer_enabled_raw="$(systemctl is-enabled opip-committee-shadow.timer 2>/dev/null)"
  timer_enabled_rc=$?
  timer_verdict="$(timer_enablement_verdict "$timer_enabled_rc" "$timer_enabled_raw")"
  report_timer_enablement "$timer_verdict" "${timer_enabled_raw//$'\n'/ }" "$timer_enabled_rc"
  timer_active="$(systemctl show -p ActiveState --value opip-committee-shadow.timer 2>/dev/null || echo unknown)"
  if [[ "$timer_active" == "inactive" ]]; then
    pass "timer is inactive"
  else
    fail "timer is not inactive (state: $timer_active)"
  fi
  unit_deny="$(systemctl show -p IPAddressDeny --value opip-committee-shadow.service 2>/dev/null || echo '')"
  unit_allow="$(systemctl show -p IPAddressAllow --value opip-committee-shadow.service 2>/dev/null || echo '')"
  if egress_denies_everything "$unit_deny"; then
    pass "egress is deny-all at the unit level"
  else
    fail "egress is not deny-all at the unit level"
  fi
  if [[ -z "$unit_allow" ]]; then
    pass "systemd reports no egress allowlist"
  else
    fail "systemd reports an egress allowlist"
  fi
fi

provider_files=0
for artifact in call_outcomes.jsonl case_outcomes.jsonl evaluations.jsonl prospective.jsonl role_results.jsonl role_case_outcomes.jsonl; do
  if [[ -s "$COMMITTEE_HOME/$artifact" ]]; then
    fail "$artifact is non-empty"
    provider_files=$((provider_files + 1))
  fi
done
if [[ "$provider_files" -eq 0 ]]; then
  pass "provider-call artifacts are absent"
  info "provider_call_count=0"
else
  info "provider_call_count=blocked"
fi

if command -v ss >/dev/null 2>&1; then
  listening="$(ss -H -lntp 2>/dev/null | grep -c committee || true)"
  if [[ "${listening:-0}" -eq 0 ]]; then
    pass "no listening socket is owned by a committee process"
  else
    fail "a committee process is listening"
  fi
else
  info "listener check skipped because ss is unavailable"
fi

rollback_tool="$SBIN_DIR/opip-rollback-committee-host-runtime"
if [[ -x "$rollback_tool" && ! -L "$rollback_tool" ]]; then
  pass "rollback tool is installed"
else
  fail "rollback tool is not installed"
fi
if [[ -d "$PREFIX/previous/app" ]]; then
  previous_sha="$(tr -d '[:space:]' < "$PREFIX/previous/app/.opip-release-sha" 2>/dev/null || true)"
  if [[ "$previous_sha" =~ ^[0-9a-f]{40}$ && "$previous_sha" != "$file_sha" && -x "$PREFIX/previous/venv/bin/python" ]]; then
    pass "previous runtime is retained for rollback"
  else
    fail "previous runtime is not a restorable release"
  fi
else
  pass "no previous runtime is retained because the active tree is the first install"
fi
if compgen -G "$PREFIX/staging.*" >/dev/null; then
  fail "a staging directory is still present"
else
  pass "no staging directory remains"
fi

echo
if [[ "$failures" -eq 0 ]]; then
  echo "COMMITTEE_RUNTIME_PROOF=PASS"
  exit 0
fi
echo "COMMITTEE_RUNTIME_PROOF=FAIL failures=$failures"
exit 1
