#!/usr/bin/env bash
set -euo pipefail

SHA="${OPIP_COMMITTEE_CANARY_RELEASE_SHA:-}"
PYROOT="${OPIP_COMMITTEE_CANARY_PYTHONPATH:-}"

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "credential canary refuses a non-exact release SHA" >&2
  exit 64
}
[[ -n "$PYROOT" && -d "$PYROOT/app/opip/committee" ]] || {
  echo "credential canary release root is unavailable" >&2
  exit 65
}

export PYTHONPATH="$PYROOT"
exec /usr/bin/python3 -m app.opip.committee.credential_canary "$SHA" \
  --root /var/lib/opip-committee/credential-canary
