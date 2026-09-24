#!/usr/bin/env bash
set -euo pipefail

SHA="${OPIP_COMMITTEE_CANARY_RELEASE_SHA:-}"
PYROOT="${OPIP_COMMITTEE_CANARY_PYTHONPATH:-}"
OPENAI_B64="${OPIP_COMMITTEE_OPENAI_API_KEY_B64:-}"
ANTHROPIC_B64="${OPIP_COMMITTEE_ANTHROPIC_API_KEY_B64:-}"

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "credential canary refuses a non-exact release SHA" >&2
  exit 64
}
[[ -n "$PYROOT" && -d "$PYROOT/app/opip/committee" ]] || {
  echo "credential canary release root is unavailable" >&2
  exit 65
}
[[ -n "$OPENAI_B64" && -n "$ANTHROPIC_B64" ]] || {
  echo "credential canary provider credential material is unavailable" >&2
  exit 66
}

OPENAI_KEY="$(printf '%s' "$OPENAI_B64" | base64 --decode)"
ANTHROPIC_KEY="$(printf '%s' "$ANTHROPIC_B64" | base64 --decode)"
[[ -n "$OPENAI_KEY" && -n "$ANTHROPIC_KEY" ]] || {
  echo "credential canary provider credential material is invalid" >&2
  exit 67
}

export OPIP_COMMITTEE_OPENAI_API_KEY="$OPENAI_KEY"
export OPIP_COMMITTEE_ANTHROPIC_API_KEY="$ANTHROPIC_KEY"
unset OPIP_COMMITTEE_OPENAI_API_KEY_B64 OPIP_COMMITTEE_ANTHROPIC_API_KEY_B64
unset OPENAI_B64 ANTHROPIC_B64 OPENAI_KEY ANTHROPIC_KEY
export PYTHONPATH="$PYROOT"

exec /usr/bin/python3 -m app.opip.committee.credential_canary "$SHA" \
  --root /var/lib/opip-committee/credential-canary
