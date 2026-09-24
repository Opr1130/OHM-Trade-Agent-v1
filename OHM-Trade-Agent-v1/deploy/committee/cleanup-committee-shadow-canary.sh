#!/usr/bin/env bash
# Remove and prove removal of every transient credentialled-canary runtime artifact.
#
# The synthetic evidence archive under /var/lib/opip-committee/credential-canary is
# intentionally durable audit evidence and is NOT removed here.
set -euo pipefail

UNIT="opip-committee-credential-canary.service"
UNIT_PATH="/etc/systemd/system/$UNIT"
LAUNCHER="/usr/local/sbin/run-committee-credential-canary.sh"
RUNTIME_DIR="/run/opip"
ENV_FILE="$RUNTIME_DIR/committee-credential-canary.env"
HOSTS_FILE="$RUNTIME_DIR/committee-credential-canary-hosts"
CANARY_LOG="$RUNTIME_DIR/committee-credential-canary.log"
PRE_OFF_LOG="$RUNTIME_DIR/committee-canary-pre-off.log"
POST_OFF_LOG="$RUNTIME_DIR/committee-canary-post-off.log"
BOUND_RELEASE="$RUNTIME_DIR/committee-canary-release"
DROPIN_DIR="/run/systemd/system/$UNIT.d"
DROPIN="$DROPIN_DIR/10-provider-egress.conf"

set +e
systemctl stop "$UNIT" >/dev/null 2>&1
rm -f -- "$ENV_FILE" "$HOSTS_FILE" "$CANARY_LOG" "$PRE_OFF_LOG" "$POST_OFF_LOG" "$DROPIN"
rmdir "$DROPIN_DIR" >/dev/null 2>&1
rmdir "$BOUND_RELEASE" >/dev/null 2>&1
rm -f -- "$UNIT_PATH" "$LAUNCHER"
systemctl daemon-reload >/dev/null 2>&1
set -e

residue=0
for path in "$ENV_FILE" "$HOSTS_FILE" "$CANARY_LOG" "$DROPIN" "$UNIT_PATH" "$LAUNCHER"; do
  if [[ -e "$path" ]]; then
    residue=1
  fi
done
if [[ -d "$DROPIN_DIR" || -d "$BOUND_RELEASE" ]]; then
  residue=1
fi
if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
  residue=1
fi
if systemctl cat "$UNIT" >/dev/null 2>&1; then
  residue=1
fi
if [[ "$residue" -ne 0 ]]; then
  echo "TRANSIENT_CANARY_CLEANUP=FAIL"
  exit 1
fi

echo "TRANSIENT_CANARY_CLEANUP=PASS"
