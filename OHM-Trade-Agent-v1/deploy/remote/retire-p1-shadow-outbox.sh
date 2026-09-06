#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
DATA_ROOT="$APP_ROOT/data"
STATE_DIR="/var/lib/ohm-deploy"
RECEIPT="$STATE_DIR/p1-shadow-outbox-retirement.env"
OUTBOX="$DATA_ROOT/p1_shadow_outbox.jsonl"
CHECKPOINT="$DATA_ROOT/p1_shadow_outbox_checkpoint.json"
DEAD_LETTER="$DATA_ROOT/p1_shadow_outbox_dead_letter.jsonl"
SOURCE_LOCK="$DATA_ROOT/.p1_shadow_outbox.jsonl.lock"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run P1 outbox retirement as root" >&2
  exit 77
fi

for cmd in docker flock stat date install rm mv; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing P1 retirement command: $cmd" >&2
    exit 69
  }
done

cd "$APP_ROOT"

# Fail closed unless the newly deployed runtime explicitly disables the legacy
# producer. A stale .env value must never be able to recreate the deleted file.
docker compose exec -T ohm-trade-agent python - <<'PY'
import os
value = str(os.getenv("P1_SHADOW_OUTBOX_ENABLED", "")).strip().lower()
assert value in {"0", "false", "no", "off"}, value
PY

before_bytes=0
if [[ -e "$OUTBOX" ]]; then
  before_bytes="$(stat -c '%s' "$OUTBOX")"
fi

# Use the same writer lock as the P1 producer before removing the retired
# artifacts. The running container has already been proven disabled above.
exec 8>>"$SOURCE_LOCK"
flock -x 8
rm -f -- "$OUTBOX" "$CHECKPOINT" "$DEAD_LETTER"
flock -u 8

if [[ -e "$OUTBOX" || -e "$CHECKPOINT" || -e "$DEAD_LETTER" ]]; then
  echo "P1 shadow outbox retirement failed: retired artifact still exists" >&2
  exit 1
fi

install -d -m 0755 "$STATE_DIR"
tmp="$RECEIPT.tmp.$$"
{
  printf 'status=RETIRED_OWNER_DISCARDED\n'
  printf 'retired_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_path=%s\n' "$OUTBOX"
  printf 'bytes_deleted=%s\n' "$before_bytes"
  printf 'producer_enabled=false\n'
  printf 'historical_backfill_required=false\n'
  printf 'trade_authority_changed=false\n'
} > "$tmp"
mv -f -- "$tmp" "$RECEIPT"

echo "O'Pip P1 shadow outbox retirement: OK"
echo "bytes_deleted=$before_bytes"
echo "disposition=RETIRED_OWNER_DISCARDED"
