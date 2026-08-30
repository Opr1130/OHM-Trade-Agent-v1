#!/usr/bin/env bash
set -Eeuo pipefail

EXPORT_ROOT="/var/lib/opip-learning-export"
PUBLISH_LOCK="$EXPORT_ROOT/.publish.lock"
EXPECTED_COMMAND="opip-export-v1"

if [[ "${SSH_ORIGINAL_COMMAND:-}" != "$EXPECTED_COMMAND" ]]; then
  echo "O'Pip learning reader: command rejected" >&2
  exit 126
fi

for cmd in flock tar; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "O'Pip learning reader: missing $cmd" >&2
    exit 69
  }
done

for name in p1_shadow_outbox.jsonl full_market_observations.jsonl manifest.env; do
  if [[ ! -r "$EXPORT_ROOT/$name" ]]; then
    echo "O'Pip learning reader: export unavailable: $name" >&2
    exit 66
  fi
done

# The forced command is read-only. A shared publish lock prevents the exporter
# from replacing any artifact while the tar stream is being emitted.
exec 8<"$PUBLISH_LOCK"
flock -s 8
exec tar -C "$EXPORT_ROOT" -cf -   p1_shadow_outbox.jsonl   full_market_observations.jsonl   manifest.env
