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
#   4. sets mode to `shadow`, records the explicit UTC activation instant, records
#      the registry review date, and caps a cycle at one case,
#   5. leaves the timer DISABLED unless `--enable-timer` is passed.
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
UNIT_DIR="/etc/systemd/system"
DROPIN_DIR="$UNIT_DIR/$UNIT.d"
DROPIN="$DROPIN_DIR/10-provider-egress.conf"
ENV_FILE="/etc/opip/committee-credentials.env"
COMMITTEE_HOME="${OPIP_COMMITTEE_HOME:-/var/lib/opip-committee}"
EVIDENCE_ROOT="${OPIP_COMMITTEE_EVIDENCE_ROOT:-/var/lib/opip-learning}"

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
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "activate the committee shadow boundary as root" >&2
  exit 77
fi

# --- precondition: the installed worker and its environment file -------------
if [[ ! -f "$UNIT_DIR/$UNIT" ]]; then
  echo "refusing activation: $UNIT is not installed; run the OFF deployment first" >&2
  exit 78
fi
if [[ ! -r "$ENV_FILE" ]]; then
  echo "refusing activation: $ENV_FILE is not readable" >&2
  exit 78
fi

# --- precondition: dedicated provider credentials are real -------------------
# Each name must be present with a non-empty, non-placeholder value. Only a
# verdict is printed; no value and no line from the file is ever echoed.
missing_names=()
placeholder_names=()
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
if [[ "${#missing_names[@]}" -gt 0 ]]; then
  echo "refusing activation: ${#missing_names[@]} dedicated Committee provider credential(s) are unset" >&2
  printf 'unset credential names: %s\n' "${missing_names[*]}" >&2
  exit 78
fi
if [[ "${#placeholder_names[@]}" -gt 0 ]]; then
  echo "refusing activation: ${#placeholder_names[@]} dedicated Committee provider credential(s) are still placeholders" >&2
  printf 'placeholder credential names: %s\n' "${placeholder_names[*]}" >&2
  exit 78
fi
echo "PASS  both dedicated Committee provider credentials are configured (values never read into output)"

# --- precondition: the SHADOW source inputs exist ----------------------------
if [[ ! -d "$EVIDENCE_ROOT" ]]; then
  echo "refusing activation: $EVIDENCE_ROOT does not exist" >&2
  exit 78
fi
MANIFEST="$(sed -n 's/^OPIP_COMMITTEE_LEARNING_MANIFEST=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
REPLICA_ROOT="$(sed -n 's/^OPIP_CANONICAL_REPLICA_ROOT_HOST=//p' "$ENV_FILE" 2>/dev/null | head -n1)"
if [[ -z "$MANIFEST" || ! -r "$MANIFEST" ]]; then
  echo "refusing activation: OPIP_COMMITTEE_LEARNING_MANIFEST is unset or unreadable" >&2
  exit 78
fi
if [[ -z "$REPLICA_ROOT" || ! -d "$REPLICA_ROOT" ]]; then
  echo "refusing activation: OPIP_CANONICAL_REPLICA_ROOT_HOST is unset or absent" >&2
  exit 78
fi
echo "PASS  verified canonical-replica source inputs are present"

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
install -m 0644 -o root -g root "$tmp_dropin" "$DROPIN"
rm -f "$tmp_dropin"
echo "PASS  provider-only egress pinned for ${#PROVIDER_ENDPOINTS[@]} endpoints ($resolved_count address entries)"

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
chown root:root "$ENV_FILE"

systemctl daemon-reload

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
