#!/usr/bin/env bash
# One-shot credentialled Committee SHADOW validation.
#
# The persistent Committee worker stays OFF and its timer stays disabled. This script
# creates a temporary canary unit boundary, permits egress only to IPs pinned for the
# two approved provider hostnames, runs one synthetic case, proves transient cleanup,
# and finally re-proves the persistent OFF isolation boundary.
set -euo pipefail

TARGET_SHA="${1:-}"
RELEASE_ROOT="${2:-}"
UNIT="opip-committee-credential-canary.service"
UNIT_PATH="/etc/systemd/system/$UNIT"
LAUNCHER="/usr/local/sbin/run-committee-credential-canary.sh"
RUNTIME_DIR="/run/opip"
ENV_FILE="$RUNTIME_DIR/committee-credential-canary.env"
HOSTS_FILE="$RUNTIME_DIR/committee-credential-canary-hosts"
CANARY_LOG="$RUNTIME_DIR/committee-credential-canary.log"
PRE_OFF_LOG="$RUNTIME_DIR/committee-canary-pre-off.log"
POST_OFF_LOG="$RUNTIME_DIR/committee-canary-post-off.log"
DROPIN_DIR="/run/systemd/system/$UNIT.d"
DROPIN="$DROPIN_DIR/10-provider-egress.conf"
OFF_VERIFY="$RELEASE_ROOT/deploy/committee/verify-committee-isolation.sh"
SERVICE_SOURCE="$RELEASE_ROOT/deploy/committee/opip-committee-credential-canary.service"
LAUNCHER_SOURCE="$RELEASE_ROOT/deploy/committee/run-committee-credential-canary.sh"
CLEANUP_SCRIPT="$RELEASE_ROOT/deploy/committee/cleanup-committee-shadow-canary.sh"

[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "refusing canary: target is not an exact SHA" >&2
  exit 64
}
[[ -d "$RELEASE_ROOT/app/opip/committee" ]] || {
  echo "refusing canary: release root is absent" >&2
  exit 65
}
for required in "$OFF_VERIFY" "$SERVICE_SOURCE" "$LAUNCHER_SOURCE" "$CLEANUP_SCRIPT"; do
  [[ -f "$required" ]] || {
    echo "refusing canary: committed artifact is missing" >&2
    exit 66
  }
done

cleanup_on_exit() {
  bash "$CLEANUP_SCRIPT" >/dev/null 2>&1 || true
}
trap cleanup_on_exit EXIT

# Refuse stale transient authority rather than silently reusing it.
for stale in "$UNIT_PATH" "$LAUNCHER" "$DROPIN"; do
  [[ ! -e "$stale" ]] || {
    echo "refusing canary: stale transient canary artifact exists" >&2
    exit 67
  }
done
[[ ! -d "$DROPIN_DIR" ]] || {
  echo "refusing canary: stale transient canary directory exists" >&2
  exit 68
}

# The already-deployed recurring worker must be proven OFF immediately before any
# provider egress is admitted.
bash "$OFF_VERIFY" >"$PRE_OFF_LOG"
if ! grep -q '^ISOLATION_PROOF=PASS$' "$PRE_OFF_LOG"; then
  grep -E '^(PASS|FAIL|INFO|ISOLATION_PROOF=)' "$PRE_OFF_LOG" || true
  echo "refusing canary: pre-canary OFF isolation is not proven" >&2
  exit 69
fi
echo "PRE_CANARY_OFF_ISOLATION=PASS"

[[ -f "$ENV_FILE" ]] || {
  echo "refusing canary: transient credential file is absent" >&2
  exit 70
}
[[ "$(stat -c '%a %U:%G' "$ENV_FILE")" == "600 root:root" ]] || {
  echo "refusing canary: transient credential file permissions are not 600 root:root" >&2
  exit 71
}
# Only base64 material is stored in the systemd EnvironmentFile. The raw provider
# credentials are decoded inside the launcher process and are never printed.
grep -q '^OPIP_COMMITTEE_OPENAI_API_KEY_B64=[A-Za-z0-9+/=][A-Za-z0-9+/=]*$' "$ENV_FILE" || {
  echo "refusing canary: OpenAI credential material is absent or malformed" >&2
  exit 72
}
grep -q '^OPIP_COMMITTEE_ANTHROPIC_API_KEY_B64=[A-Za-z0-9+/=][A-Za-z0-9+/=]*$' "$ENV_FILE" || {
  echo "refusing canary: Anthropic credential material is absent or malformed" >&2
  exit 73
}

printf 'OPIP_COMMITTEE_CANARY_RELEASE_SHA=%s\n' "$TARGET_SHA" >> "$ENV_FILE"
printf 'OPIP_COMMITTEE_CANARY_PYTHONPATH=%s\n' "$RELEASE_ROOT" >> "$ENV_FILE"

install -m 0755 "$LAUNCHER_SOURCE" "$LAUNCHER"
install -m 0644 "$SERVICE_SOURCE" "$UNIT_PATH"
install -d -m 0755 "$RUNTIME_DIR" "$DROPIN_DIR"
install -d -o root -g root -m 0750 /var/lib/opip-committee/credential-canary

: > "$HOSTS_FILE"
printf '127.0.0.1 localhost\n::1 localhost\n' >> "$HOSTS_FILE"
: > "$DROPIN"
printf '[Service]\n' >> "$DROPIN"
resolve_provider() {
  local host="$1"
  local found=0
  local ip
  while read -r ip; do
    [[ -n "$ip" ]] || continue
    case "$ip" in
      *:*) printf 'IPAddressAllow=%s/128\n' "$ip" >> "$DROPIN" ;;
      *.*) printf 'IPAddressAllow=%s/32\n' "$ip" >> "$DROPIN" ;;
      *) continue ;;
    esac
    printf '%s %s\n' "$ip" "$host" >> "$HOSTS_FILE"
    found=1
  done < <(getent ahosts "$host" | awk '{print $1}' | sort -u)
  [[ "$found" -eq 1 ]] || {
    echo "refusing canary: provider hostname could not be pinned" >&2
    return 1
  }
}

resolve_provider "api.openai.com"
resolve_provider "api.anthropic.com"
chmod 0644 "$HOSTS_FILE" "$DROPIN"

# No persistent provider allowlist may be added to the recurring worker.
if grep -qE '^[[:space:]]*IPAddressAllow=' /etc/systemd/system/opip-committee-shadow.service; then
  echo "refusing canary: recurring worker contains a persistent egress allowlist" >&2
  exit 74
fi

systemctl daemon-reload
ALLOW_STATE="$(systemctl show -p IPAddressAllow --value "$UNIT" 2>/dev/null || true)"
DENY_STATE="$(systemctl show -p IPAddressDeny --value "$UNIT" 2>/dev/null || true)"
[[ -n "$ALLOW_STATE" ]] || {
  echo "refusing canary: transient provider egress allowlist is not loaded" >&2
  exit 75
}
[[ "$DENY_STATE" == *"0.0.0.0/0"* && "$DENY_STATE" == *"::/0"* ]] || {
  echo "refusing canary: deny-all base policy is not loaded" >&2
  exit 76
}
echo "CANARY_EGRESS_POLICY=PASS"

START_AT="$(date --iso-8601=seconds)"
set +e
systemctl start "$UNIT"
RC=$?
set -e
INVOCATION_ID="$(systemctl show -p InvocationID --value "$UNIT" 2>/dev/null || true)"
if [[ -n "$INVOCATION_ID" ]]; then
  journalctl "_SYSTEMD_INVOCATION_ID=$INVOCATION_ID" --no-pager -o cat >"$CANARY_LOG" || true
else
  journalctl -u "$UNIT" --since "$START_AT" --no-pager -o cat >"$CANARY_LOG" || true
fi
cat "$CANARY_LOG"
if [[ "$RC" -ne 0 ]] || ! grep -q '^CREDENTIAL_CANARY_PROOF=PASS$' "$CANARY_LOG"; then
  echo "CREDENTIAL_CANARY_PROOF=FAIL"
  exit 77
fi

# Cleanup is a first-class proof. A restored persistent OFF worker is not enough if a
# transient credential file, unit, provider allowlist, or bind target remains behind.
bash "$CLEANUP_SCRIPT"
trap - EXIT

bash "$OFF_VERIFY" >"$POST_OFF_LOG"
if ! grep -q '^ISOLATION_PROOF=PASS$' "$POST_OFF_LOG"; then
  grep -E '^(PASS|FAIL|INFO|ISOLATION_PROOF=)' "$POST_OFF_LOG" || true
  echo "POST_CANARY_OFF_ISOLATION=FAIL"
  rm -f -- "$POST_OFF_LOG" "$PRE_OFF_LOG"
  exit 78
fi
echo "POST_CANARY_OFF_ISOLATION=PASS"
rm -f -- "$POST_OFF_LOG" "$PRE_OFF_LOG"
