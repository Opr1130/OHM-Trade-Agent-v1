#!/usr/bin/env bash
# One-shot credentialled Committee SHADOW validation.
#
# This script never enables the recurring timer and never changes the persistent
# Committee mode. It creates a temporary canary unit boundary, permits egress only
# to IPs pinned for the two approved provider hostnames, runs one synthetic case,
# removes every temporary credential/network artifact, and finally re-proves OFF.
set -euo pipefail

TARGET_SHA="${1:-}"
RELEASE_ROOT="${2:-}"
UNIT="opip-committee-credential-canary.service"
UNIT_PATH="/etc/systemd/system/$UNIT"
LAUNCHER="/usr/local/sbin/run-committee-credential-canary.sh"
RUNTIME_DIR="/run/opip"
ENV_FILE="$RUNTIME_DIR/committee-credential-canary.env"
HOSTS_FILE="$RUNTIME_DIR/committee-credential-canary-hosts"
DROPIN_DIR="/run/systemd/system/$UNIT.d"
DROPIN="$DROPIN_DIR/10-provider-egress.conf"
OFF_VERIFY="$RELEASE_ROOT/deploy/committee/verify-committee-isolation.sh"
SERVICE_SOURCE="$RELEASE_ROOT/deploy/committee/opip-committee-credential-canary.service"
LAUNCHER_SOURCE="$RELEASE_ROOT/deploy/committee/run-committee-credential-canary.sh"
CANARY_LOG="/run/opip/committee-credential-canary.log"

[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "refusing canary: target is not an exact SHA" >&2
  exit 64
}
[[ -d "$RELEASE_ROOT/.github" || -d "$RELEASE_ROOT/app" ]] || {
  echo "refusing canary: release root is absent" >&2
  exit 65
}
for required in "$OFF_VERIFY" "$SERVICE_SOURCE" "$LAUNCHER_SOURCE"; do
  [[ -f "$required" ]] || {
    echo "refusing canary: committed artifact is missing" >&2
    exit 66
  }
done

cleanup() {
  set +e
  systemctl stop "$UNIT" >/dev/null 2>&1
  rm -f -- "$ENV_FILE" "$HOSTS_FILE" "$DROPIN" "$CANARY_LOG"
  rmdir "$DROPIN_DIR" >/dev/null 2>&1
  rm -f -- "$UNIT_PATH" "$LAUNCHER"
  systemctl daemon-reload >/dev/null 2>&1
}
trap cleanup EXIT

# The already-deployed recurring worker must still be proven OFF immediately before
# credentials or provider egress are introduced.
bash "$OFF_VERIFY" >/tmp/opip-canary-pre-off.log
grep -q '^ISOLATION_PROOF=PASS$' /tmp/opip-canary-pre-off.log || {
  echo "refusing canary: pre-canary OFF isolation is not proven" >&2
  exit 67
}
echo "PRE_CANARY_OFF_ISOLATION=PASS"

[[ -f "$ENV_FILE" ]] || {
  echo "refusing canary: transient credential file is absent" >&2
  exit 68
}
[[ "$(stat -c '%a %U:%G' "$ENV_FILE")" == "600 root:root" ]] || {
  echo "refusing canary: transient credential file permissions are not 600 root:root" >&2
  exit 69
}
# Validate only presence; never print or source a credential in this shell.
grep -q '^OPIP_COMMITTEE_OPENAI_API_KEY=.' "$ENV_FILE" || {
  echo "refusing canary: OpenAI credential is absent" >&2
  exit 70
}
grep -q '^OPIP_COMMITTEE_ANTHROPIC_API_KEY=.' "$ENV_FILE" || {
  echo "refusing canary: Anthropic credential is absent" >&2
  exit 71
}

printf '\nOPIP_COMMITTEE_CANARY_RELEASE_SHA=%s\n' "$TARGET_SHA" >> "$ENV_FILE"
printf 'OPIP_COMMITTEE_CANARY_PYTHONPATH=%s\n' "$RELEASE_ROOT" >> "$ENV_FILE"

install -m 0755 "$LAUNCHER_SOURCE" "$LAUNCHER"
install -m 0644 "$SERVICE_SOURCE" "$UNIT_PATH"
install -d -m 0755 "$DROPIN_DIR" "$RUNTIME_DIR"

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
! grep -qE '^[[:space:]]*IPAddressAllow='   /etc/systemd/system/opip-committee-shadow.service || {
  echo "refusing canary: recurring worker contains a persistent egress allowlist" >&2
  exit 72
}

systemctl daemon-reload
set +e
systemctl start "$UNIT"
RC=$?
set -e
INVOCATION_ID="$(systemctl show -p InvocationID --value "$UNIT" 2>/dev/null || true)"
if [[ -n "$INVOCATION_ID" ]]; then
  journalctl "_SYSTEMD_INVOCATION_ID=$INVOCATION_ID" --no-pager -o cat > "$CANARY_LOG" || true
else
  journalctl -u "$UNIT" -n 50 --no-pager -o cat > "$CANARY_LOG" || true
fi
# The canary emits only a safe summary: no prompt, response text, or credential.
cat "$CANARY_LOG"
[[ "$RC" -eq 0 ]] && grep -q '^CREDENTIAL_CANARY_PROOF=PASS$' "$CANARY_LOG" || {
  echo "CREDENTIAL_CANARY_PROOF=FAIL"
  exit 73
}

# Remove every temporary secret and egress artifact before re-proving the durable
# OFF boundary. cleanup is idempotent and remains armed for abnormal exits.
cleanup
trap - EXIT

bash "$OFF_VERIFY" >/tmp/opip-canary-post-off.log
cat /tmp/opip-canary-post-off.log
grep -q '^ISOLATION_PROOF=PASS$' /tmp/opip-canary-post-off.log || {
  echo "POST_CANARY_OFF_ISOLATION=FAIL"
  exit 74
}
echo "POST_CANARY_OFF_ISOLATION=PASS"
