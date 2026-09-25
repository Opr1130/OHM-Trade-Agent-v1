#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — OFF -> credentialled SHADOW activation (IC-043).
#
# RUNS ON THE LEARNING/ANALYTICS PLANE, AS ROOT. This is the separately authorised
# activation boundary the OFF deployment deliberately stops short of. It:
#
#   1. refuses unless the two dedicated Committee provider credentials are present
#      and non-placeholder (without ever printing a value),
#   2. refuses unless the phase-six source inputs the SHADOW path requires exist,
#   3. pins provider-only egress by resolving the two approved provider endpoints
#      and installing an `IPAddressAllow=` drop-in alongside the unit's existing
#      `IPAddressDeny=any`, so egress stays allowlist-only and everything else stays
#      denied,
#   4. installs `20-shadow-mode.conf` so the merged unit Environment property is
#      `shadow` while the base unit file stays `off` (a later Environment=
#      assignment replaces the earlier one; a bare Environment= is never used),
#   5. sets mode to `shadow`, records the explicit UTC activation instant, records
#      the registry review date, and caps a cycle at one case,
#   6. leaves the timer DISABLED unless `--enable-timer` is passed.
#
# MEASUREMENT ONLY: it grants no trading authority and touches no trading credential.
#
# Usage:
#   activate-committee-shadow.sh <40-char-sha> <not-before-iso8601> <registry-review-by-iso8601> [--enable-timer]
#
# Exit codes:
#   0   activation applied and verified
#   64  bad usage
#   77  not root
#   78  a required precondition is absent (credentials, source inputs)
set -Eeuo pipefail

UNIT="opip-committee-shadow.service"
TIMER="opip-committee-shadow.timer"

# Harness overrides apply only when OPIP_COMMITTEE_RUNTIME_TEST_HARNESS=1.
# Otherwise the production absolute paths are used and any override is ignored.
refuse_harness_path() {
  local label="$1" path="$2"
  if [[ -z "$path" ]]; then
    echo "test harness requires ${label}" >&2
    exit 76
  fi
  case "$path" in
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

install_conf() {
  local src="$1" dest="$2"
  if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" ]]; then
    install -T -m 0644 "$src" "$dest"
  else
    install -T -m 0644 -o root -g root "$src" "$dest"
  fi
}

current_file_mode() {
  sed -n 's/^OPIP_COMMITTEE_MODE=//p' "$ENV_FILE" 2>/dev/null | head -n1 || true
}

#: Provider endpoints. These are the exact application-level allowlist entries in
#: `app/opip/committee/transports.py`; the network policy is derived from them so
#: the two cannot silently disagree.
PROVIDER_ENDPOINTS=(api.openai.com api.anthropic.com)

#: Credential NAMES only. A value is never echoed, logged, or compared in a way
#: that could write it to the journal.
PROVIDER_CREDENTIAL_NAMES=(
  OPIP_COMMITTEE_OPENAI_API_KEY
  OPIP_COMMITTEE_ANTHROPIC_API_KEY
)

#: Values that indicate an unpopulated template rather than a real credential.
PLACEHOLDER_VALUES='CHANGEME changeme PLACEHOLDER placeholder REPLACE_ME replace_me'

fail_usage() { printf 'usage: %s <40-char-sha> <not-before-iso8601> <review-by-iso8601> [--enable-timer]\n' "$0" >&2; }

TARGET_SHA="${1:-}"
NOT_BEFORE="${2:-}"
REVIEW_BY="${3:-}"
ENABLE_TIMER="false"
for arg in "${@:4}"; do
  case "$arg" in
    --enable-timer) ENABLE_TIMER="true" ;;
    *) echo "unknown argument: $arg" >&2; fail_usage; exit 64 ;;
  esac
done

if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  fail_usage
  echo "a branch name cannot identify a released artifact" >&2
  exit 64
fi
if [[ -z "$NOT_BEFORE" || -z "$REVIEW_BY" ]]; then
  fail_usage
  exit 64
fi
configure_committee_paths
if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" != "1" ]]; then
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "activate the committee shadow boundary as root" >&2
    exit 77
  fi
fi

# --- precondition: the arguments are usable ----------------------------------
# Both instants are validated here as well as in the workflow: they are written
# into a root-owned environment file that a `sudo bash` path reads, so an
# unvalidated value must never reach them.
iso_utc_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})$'
if [[ ! "$NOT_BEFORE" =~ $iso_utc_re ]]; then
  echo "refusing activation: not-before must be an ISO-8601 UTC instant" >&2
  exit 64
fi
if [[ ! "$REVIEW_BY" =~ $iso_utc_re ]]; then
  echo "refusing activation: review-by must be an ISO-8601 UTC instant" >&2
  exit 64
fi

# --- preconditions -----------------------------------------------------------
# Every precondition is evaluated and reported before the script refuses, so an
# operator sees the complete set of things to fix rather than fixing them one
# deployment at a time. A refusal is still fail-closed: nothing is written and no
# mode is changed while any precondition is unmet.
precondition_failed=0
refuse() { printf 'REFUSED  %s\n' "$1" >&2; precondition_failed=$((precondition_failed + 1)); return 0; }
satisfy() { printf 'PASS     %s\n' "$1"; return 0; }

# The worker must be installed and have a readable environment file.
if [[ -f "$UNIT_DIR/$UNIT" ]]; then
  satisfy "$UNIT is installed"
else
  refuse "$UNIT is not installed; run the OFF deployment first"
fi
if [[ -r "$ENV_FILE" ]]; then
  satisfy "$ENV_FILE is readable"
else
  refuse "$ENV_FILE is not readable; run the OFF deployment first"
fi

# The application root and interpreter the cycle actually executes. The OFF path
# returns before it needs the application, so a missing root stays invisible
# until SHADOW is attempted. Activation refuses rather than reporting PASS on a
# worker that cannot start one cycle.
APP_ROOT="${OPIP_APP_ROOT:-$(sed -n 's/^OPIP_APP_ROOT=//p' "$ENV_FILE" 2>/dev/null | head -n1)}"
APP_ROOT="${APP_ROOT:-/opt/opip/app}"
VENV_PYTHON="${OPIP_VENV_PYTHON:-$(sed -n 's/^OPIP_VENV_PYTHON=//p' "$ENV_FILE" 2>/dev/null | head -n1)}"
VENV_PYTHON="${VENV_PYTHON:-/opt/opip/venv/bin/python}"
if [[ -d "$APP_ROOT" ]]; then
  satisfy "the worker application root exists: $APP_ROOT"
else
  refuse "the worker application root does not exist ($APP_ROOT); a SHADOW cycle cannot start"
fi
if [[ -x "$VENV_PYTHON" ]]; then
  satisfy "the worker interpreter is executable: $VENV_PYTHON"
else
  refuse "the worker interpreter is not executable ($VENV_PYTHON); a SHADOW cycle cannot start"
fi
if [[ -d "$APP_ROOT" && -x "$VENV_PYTHON" ]]; then
  if ( cd "$APP_ROOT" && "$VENV_PYTHON" -c 'import app.opip.committee.cycle_runner' ) 2>/dev/null; then
    satisfy "the worker interpreter can import the committee cycle runner"
  else
    refuse "the worker interpreter cannot import the committee cycle runner from $APP_ROOT"
  fi
fi

# Both dedicated provider credentials must be present with a real value. Each
# name is checked individually; only a verdict is printed, and no value and no
# line from the environment file is ever echoed.
missing_names=()
placeholder_names=()
if [[ -r "$ENV_FILE" ]]; then
  for name in "${PROVIDER_CREDENTIAL_NAMES[@]}"; do
    value="$(sed -n "s/^${name}=//p" "$ENV_FILE" 2>/dev/null | head -n1)"
    if [[ -z "$value" ]]; then
      missing_names+=("$name")
      continue
    fi
    for placeholder in $PLACEHOLDER_VALUES; do
      if [[ "$value" == "$placeholder" ]]; then
        placeholder_names+=("$name")
        break
      fi
    done
  done
  unset value
fi
if [[ "${#missing_names[@]}" -eq 0 && "${#placeholder_names[@]}" -eq 0 ]]; then
  satisfy "both dedicated Committee provider credentials are configured (values never read into output)"
else
  if [[ "${#missing_names[@]}" -gt 0 ]]; then
    refuse "${#missing_names[@]} dedicated Committee provider credential(s) are unset: ${missing_names[*]}"
  fi
  if [[ "${#placeholder_names[@]}" -gt 0 ]]; then
    refuse "${#placeholder_names[@]} dedicated Committee provider credential(s) are still placeholders: ${placeholder_names[*]}"
  fi
fi

# The verified canonical-replica inputs the SHADOW case producer needs.
MANIFEST="$(sed -n 's/^OPIP_COMMITTEE_LEARNING_MANIFEST=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
REPLICA_ROOT="$(sed -n 's/^OPIP_CANONICAL_REPLICA_ROOT_HOST=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
if [[ -n "$MANIFEST" && -r "$MANIFEST" ]]; then
  satisfy "the learning export manifest is readable"
else
  refuse "OPIP_COMMITTEE_LEARNING_MANIFEST is unset or unreadable (got '${MANIFEST:-unset}')"
fi
if [[ -n "$REPLICA_ROOT" && -d "$REPLICA_ROOT" ]]; then
  satisfy "the verified canonical-replica root is present"
else
  refuse "OPIP_CANONICAL_REPLICA_ROOT_HOST is unset or absent (got '${REPLICA_ROOT:-unset}')"
fi
if [[ -d "$EVIDENCE_ROOT" ]]; then
  satisfy "the read-only evidence root is present: $EVIDENCE_ROOT"
else
  refuse "$EVIDENCE_ROOT does not exist"
fi

echo
if [[ "$precondition_failed" -gt 0 ]]; then
  echo "SHADOW_ACTIVATION=BLOCKED preconditions_failed=$precondition_failed"
  echo "nothing was changed; the plane remains as it was" >&2
  exit 78
fi
echo "SHADOW_ACTIVATION=PREFLIGHT_PASS"

# --- provider-only egress -----------------------------------------------------
# systemd does not resolve host names into an address policy, so the approved
# endpoint names are resolved here and pinned as addresses. A DNS change is
# therefore a visible re-activation event rather than silently widened egress.
install -d -m 0755 "$DROPIN_DIR"
tmp_dropin="$(mktemp)"
{
  echo "# Generated by activate-committee-shadow.sh at $(date -u +%Y-%m-%dT%H:%M:%SZ)."
  echo "# Provider-only egress for the approved endpoints, pinned as addresses."
  echo "# IPAddressDeny=any remains in the unit: anything not listed here is denied."
  echo "[Service]"
} > "$tmp_dropin"

# Name resolution must survive the deny-all default, or every provider call fails
# before a connection is attempted. The host's own configured resolvers are the
# only extra addresses allowed, and they are read from the resolver config rather
# than guessed.
resolver_count=0
while IFS= read -r nameserver; do
  [[ -z "$nameserver" ]] && continue
  printf 'IPAddressAllow=%s\n' "$nameserver" >> "$tmp_dropin"
  resolver_count=$((resolver_count + 1))
done < <(awk '/^[[:space:]]*nameserver[[:space:]]+/ {print $2}' "$RESOLV_CONF" 2>/dev/null || true)
# A loopback stub resolver is the common case; loopback is denied by `any`, so it
# must be allowed explicitly when the host resolves through it.
if grep -qE '^[[:space:]]*nameserver[[:space:]]+(127\.|::1)' "$RESOLV_CONF" 2>/dev/null; then
  printf 'IPAddressAllow=127.0.0.1\nIPAddressAllow=::1\n' >> "$tmp_dropin"
  resolver_count=$((resolver_count + 1))
fi

resolved_count=0
for endpoint in "${PROVIDER_ENDPOINTS[@]}"; do
  addresses="$(getent ahosts "$endpoint" 2>/dev/null | awk '{print $1}' | sort -u || true)"
  if [[ -z "$addresses" ]]; then
    rm -f "$tmp_dropin"
    echo "refusing activation: could not resolve $endpoint; egress cannot be pinned" >&2
    exit 78
  fi
  while IFS= read -r address; do
    [[ -z "$address" ]] && continue
    printf 'IPAddressAllow=%s\n' "$address" >> "$tmp_dropin"
    resolved_count=$((resolved_count + 1))
  done <<< "$addresses"
done
if [[ "$resolved_count" -eq 0 ]]; then
  rm -f "$tmp_dropin"
  echo "refusing activation: no provider address was pinned" >&2
  exit 78
fi
install_conf "$tmp_dropin" "$DROPIN"
rm -f "$tmp_dropin"
echo "PASS  provider-only egress pinned for ${#PROVIDER_ENDPOINTS[@]} endpoints ($resolved_count address entries, $resolver_count resolver entries)"

# The base unit stays Environment=OPIP_COMMITTEE_MODE=off. This later drop-in
# replaces only that assignment in the merged Environment property. A bare
# Environment= line is not used: it would clear PYTHONDONTWRITEBYTECODE=1.
tmp_mode="$(mktemp)"
printf '%s\n' '[Service]' 'Environment=OPIP_COMMITTEE_MODE=shadow' > "$tmp_mode"
# A directory at the drop-in path is not a unit file. install -T refuses to
# treat that directory as the destination file.
mode_installed=0
if [[ ! -d "$MODE_DROPIN" ]] && install_conf "$tmp_mode" "$MODE_DROPIN"; then
  mode_installed=1
fi
rm -f "$tmp_mode"
if [[ "$mode_installed" -ne 1 ]]; then
  if [[ "$(current_file_mode)" != "shadow" ]]; then
    rm -f "$DROPIN"
    systemctl daemon-reload
  fi
  echo "refusing activation: the shadow mode drop-in was not installed" >&2
  exit 78
fi
echo "PASS  shadow mode drop-in installed; base unit stays off"

# --- precondition: the pinned policy actually permits a provider connection ---
# A pinned address that cannot be reached is a denial of legitimate egress, so
# reachability is proven here rather than assumed from the allowlist.
systemctl daemon-reload
reachable=0
for endpoint in "${PROVIDER_ENDPOINTS[@]}"; do
  if timeout 10 bash -c "exec 3<>/dev/tcp/$endpoint/443" 2>/dev/null; then
    reachable=$((reachable + 1))
  else
    echo "WARN  $endpoint:443 was not reachable during activation" >&2
  fi
done
if [[ "$reachable" -eq 0 ]]; then
  if [[ "$(current_file_mode)" != "shadow" ]]; then
    rm -f "$DROPIN" "$MODE_DROPIN"
  else
    rm -f "$DROPIN"
  fi
  rmdir "$DROPIN_DIR" 2>/dev/null || true
  systemctl daemon-reload
  echo "refusing activation: no approved provider endpoint is reachable under the pinned policy" >&2
  exit 78
fi
echo "PASS  $reachable of ${#PROVIDER_ENDPOINTS[@]} approved provider endpoints are reachable"

# --- mode and activation boundary --------------------------------------------
set_env_value() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    # `|` as the delimiter keeps ISO-8601 values with colons unambiguous.
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_env_value OPIP_COMMITTEE_RELEASE_SHA "$TARGET_SHA"
set_env_value OPIP_COMMITTEE_MODE shadow
set_env_value OPIP_COMMITTEE_SHADOW_NOT_BEFORE "$NOT_BEFORE"
set_env_value OPIP_COMMITTEE_REGISTRY_REVIEW_BY "$REVIEW_BY"
# One case per cycle for the canary. Raising it is a separate decision.
set_env_value OPIP_COMMITTEE_MAX_CASES_PER_CYCLE 1
chmod 0600 "$ENV_FILE"
if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" != "1" ]]; then
  chown root:root "$ENV_FILE"
fi

systemctl daemon-reload

# Same pipeline verify-committee-shadow.sh uses for the merged unit Environment
# property. EnvironmentFile= is not part of that property, so the drop-in must
# have replaced the base unit's off assignment before activation can pass.
effective_mode="$(systemctl show -p Environment --value "$UNIT" 2>/dev/null | tr ' ' '\n' | sed -n 's/^OPIP_COMMITTEE_MODE=//p' | head -n1 || true)"
file_mode="$(current_file_mode)"
if [[ "$effective_mode" != "shadow" || "$file_mode" != "shadow" || ! -f "$DROPIN" ]]; then
  if [[ "$effective_mode" != "shadow" ]]; then
    set_env_value OPIP_COMMITTEE_MODE off
    rm -f "$MODE_DROPIN"
    systemctl daemon-reload
  fi
  echo "refusing activation: effective unit mode '${effective_mode:-none}' is not shadow" >&2
  exit 1
fi

if [[ "$ENABLE_TIMER" == "true" ]]; then
  systemctl enable --now "$TIMER"
  echo "PASS  recurring committee timer enabled (one case per cycle)"
else
  systemctl disable "$TIMER" >/dev/null 2>&1 || true
  systemctl stop "$TIMER" >/dev/null 2>&1 || true
  echo "PASS  committee timer left disabled and inactive (manual canary only)"
fi

echo
echo "SHADOW_ACTIVATION=PASS release=$TARGET_SHA not_before=$NOT_BEFORE review_by=$REVIEW_BY timer_enabled=$ENABLE_TIMER"
