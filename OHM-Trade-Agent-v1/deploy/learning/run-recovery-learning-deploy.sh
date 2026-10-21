#!/usr/bin/env bash
# Recovery-only learning worker update.
#
# Normal `/deploy-learning <sha>` requires the target to equal current main.
# During recovery the healthy production core legitimately runs an earlier,
# authenticated SHA than the merged recovery tooling. This script keeps both
# identities explicit and refuses any ambiguity:
#
#   $1 PRODUCTION_SHA  - authenticated production app SHA (must equal the
#                        production export manifest, verified over the reader)
#   $2 TOOLING_SHA     - exact merged commit whose worker image is built
#   $3 CURRENT_MAIN_SHA- repository main at dispatch; must equal TOOLING_SHA
#   $4 COMPARE_STATUS  - GitHub compare PRODUCTION...TOOLING (ahead/identical)
#   $5 TOOLING_CI      - "true" only after successful exact-SHA CI
#   $6 RELEASE_DIR     - uploaded release tree for TOOLING_SHA
#
# It never enables timers, never touches production, and writes an append-only
# recovery receipt recording both identities.
set -Eeuo pipefail

PRODUCTION_SHA="${1:-}"
TOOLING_SHA="${2:-}"
CURRENT_MAIN_SHA="${3:-}"
COMPARE_STATUS="${4:-}"
TOOLING_CI="${5:-}"
RELEASE_DIR="${6:-}"

ENV_FILE="/etc/opip-learning.env"
DATA_ROOT="/var/lib/opip-learning"
STATE_ROOT="$DATA_ROOT/state"
RECEIPT_DIR="$STATE_ROOT/recovery-deploys"
TIMERS=(
  opip-learning-sync.timer
  opip-learning-capture.timer
  opip-learning-outcomes.timer
)

usage() {
  echo "usage: $0 <production-sha> <tooling-sha> <current-main-sha> <ahead|identical> <true|false> <release-dir>" >&2
}

for sha in "$PRODUCTION_SHA" "$TOOLING_SHA" "$CURRENT_MAIN_SHA"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { usage; exit 64; }
done
[[ "$PRODUCTION_SHA" != "$TOOLING_SHA" ]] || {
  echo "refusing recovery: production and tooling SHAs are identical; use /deploy-learning" >&2
  exit 65
}
[[ "$TOOLING_SHA" == "$CURRENT_MAIN_SHA" ]] || {
  echo "refusing recovery: tooling SHA is not current main" >&2
  exit 65
}
[[ "$COMPARE_STATUS" == "ahead" || "$COMPARE_STATUS" == "identical" ]] || {
  echo "refusing recovery: production is not an ancestor of tooling ($COMPARE_STATUS)" >&2
  exit 65
}
[[ "$TOOLING_CI" == "true" ]] || {
  echo "refusing recovery: exact-SHA CI for tooling is not successful" >&2
  exit 65
}
[[ -d "$RELEASE_DIR/OHM-Trade-Agent-v1" ]] || {
  echo "release directory missing OHM-Trade-Agent-v1 tree: $RELEASE_DIR" >&2
  exit 66
}
[[ "${EUID:-$(id -u)}" -eq 0 ]] || {
  echo "run recovery learning deploy as root" >&2
  exit 77
}
[[ -r "$ENV_FILE" ]] || {
  echo "missing learning environment; bootstrap required" >&2
  exit 78
}
SOURCE_APP="$RELEASE_DIR/OHM-Trade-Agent-v1"
RECOVERY_LOCK="/var/lock/opip-learning-recovery.lock"
TX_ROOT="$STATE_ROOT/recovery-transactions"
TX_DIR="$TX_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-${TOOLING_SHA:0:12}-$$"
COMPATIBILITY_MANIFEST="$RELEASE_DIR/RECOVERY_COMPATIBILITY.json"
LEGACY_SOURCE_SHA="8b3cc2712432ca21007be4db0667301d48b89d97"
LEGACY_LINEAGE="LINEAGE-A-LEGACY"
ARCHIVE_STATE_ROOT="$STATE_ROOT/recovery-archives"
BEFORE_CURRENT=""
STAGE="PREVALIDATION"

# Only one recovery may mutate the worker at a time. The three learning jobs
# also take the plane lock, so a duplicate recovery or an active one-shot is
# refused rather than racing.
exec 7>"$RECOVERY_LOCK"
if ! flock -n 7; then
  echo "refusing recovery: another recovery holds $RECOVERY_LOCK" >&2
  exit 75
fi
for service in opip-learning-sync.service opip-learning-capture.service opip-learning-outcomes.service; do
  if systemctl is-active --quiet "$service" 2>/dev/null; then
    echo "refusing recovery: $service is active" >&2
    exit 75
  fi
done
if [[ -n "$(docker ps -q --filter label=com.opip.learning.job 2>/dev/null)" ]]; then
  echo "refusing recovery: learning job containers are active" >&2
  exit 75
fi

# Cross-SHA learning-contract compatibility, produced by the trusted workflow
# from the exact production and tooling commits.
if [[ ! -r "$COMPATIBILITY_MANIFEST" ]]; then
  echo "refusing recovery: missing RECOVERY_COMPATIBILITY.json" >&2
  exit 66
fi
PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_recovery_compatibility \
  verify \
  --manifest "$COMPATIBILITY_MANIFEST" \
  --source-app "$SOURCE_APP" \
  --production-sha "$PRODUCTION_SHA" \
  --tooling-sha "$TOOLING_SHA" >/dev/null || {
  echo "refusing recovery: cross-SHA learning contract compatibility failed" >&2
  PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_recovery_compatibility \
    verify \
    --manifest "$COMPATIBILITY_MANIFEST" \
    --source-app "$SOURCE_APP" \
    --production-sha "$PRODUCTION_SHA" \
    --tooling-sha "$TOOLING_SHA" >&2 || true
  exit 65
}

# A new canonical lineage may be synchronized only after the outgoing legacy
# generation has a verified copy-only archive. The archive is never created or
# repaired here; a missing or corrupt archive refuses the recovery.
CURRENT_GEN=""
CURRENT_SRC=""
if [[ -r "$DATA_ROOT/canonical-replica/current" ]]; then
  CURRENT_GEN="$(tr -d '[:space:]' <"$DATA_ROOT/canonical-replica/current")"
fi
BEFORE_CURRENT="$CURRENT_GEN"
if [[ -n "$CURRENT_GEN" && -r "$DATA_ROOT/canonical-replica/generations/$CURRENT_GEN/replica_manifest.json" ]]; then
  CURRENT_SRC="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("source_release_sha",""))' \
    "$DATA_ROOT/canonical-replica/generations/$CURRENT_GEN/replica_manifest.json")"
fi
if [[ -n "$CURRENT_GEN" && "$CURRENT_SRC" != "$PRODUCTION_SHA" ]]; then
  if [[ "$CURRENT_SRC" != "$LEGACY_SOURCE_SHA" ]]; then
    echo "refusing recovery: unknown outgoing lineage source=$CURRENT_SRC generation=$CURRENT_GEN" >&2
    exit 65
  fi
  ARCHIVE_DIR="$DATA_ROOT/canonical-replica/archive/$LEGACY_LINEAGE/$CURRENT_GEN"
  PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_lineage_archive \
    verify \
    --source-generation "$DATA_ROOT/canonical-replica/generations/$CURRENT_GEN" \
    --archive-dir "$ARCHIVE_DIR" \
    --state-root "$ARCHIVE_STATE_ROOT" \
    --lineage-id "$LEGACY_LINEAGE" \
    --source-sha "$LEGACY_SOURCE_SHA" || {
    echo "refusing recovery: verified lineage archive is required before a new lineage sync" >&2
    exit 65
  }
fi


# Recovery must start from a stopped plane. It never stops a live timer as a
# side effect of an authorization mistake.
for timer in "${TIMERS[@]}"; do
  if systemctl is-enabled --quiet "$timer" 2>/dev/null; then
    echo "refusing recovery: $timer is enabled; disable learning timers first" >&2
    exit 75
  fi
done

# The release tree must self-identify as the exact tooling commit.
MARKER="$RELEASE_DIR/RECOVERY_SOURCE_SHA"
[[ -r "$MARKER" ]] || {
  echo "refusing recovery: missing RECOVERY_SOURCE_SHA in release tree" >&2
  exit 66
}
[[ "$(tr -d '[:space:]' <"$MARKER")" == "$TOOLING_SHA" ]] || {
  echo "refusing recovery: release tree marker does not match tooling SHA" >&2
  exit 65
}

# shellcheck disable=SC1090
source "$ENV_FILE"
: "${OPIP_PRODUCTION_HOST:?OPIP_PRODUCTION_HOST is required}"
: "${OPIP_PRODUCTION_USER:?OPIP_PRODUCTION_USER is required}"
: "${OPIP_LEARNING_SSH_KEY:=/root/.ssh/opip-learning}"

install -d -o root -g root -m 0755 "$DATA_ROOT/data" "$STATE_ROOT"
install -d -o root -g root -m 0700 "$RECEIPT_DIR"
install -d -o root -g root -m 0700 "$TX_ROOT" "$TX_DIR"

INSTALLED_SCRIPTS=(
  /usr/local/sbin/opip-learning-sync
  /usr/local/sbin/opip-learning-job
  /usr/local/sbin/opip-learning-cleanup
)
INSTALLED_UNITS=(
  /etc/systemd/system/opip-learning-sync.service
  /etc/systemd/system/opip-learning-sync.timer
  /etc/systemd/system/opip-learning-capture.service
  /etc/systemd/system/opip-learning-capture.timer
  /etc/systemd/system/opip-learning-outcomes.service
  /etc/systemd/system/opip-learning-outcomes.timer
)
SNAPSHOT_ARGS=()
for config_path in \
  "$ENV_FILE" \
  "${INSTALLED_SCRIPTS[@]}" \
  "${INSTALLED_UNITS[@]}" \
  "$STATE_ROOT/last-learning-deploy.env"; do
  SNAPSHOT_ARGS+=(--path "$config_path")
done
PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_recovery_transaction \
  snapshot --transaction-dir "$TX_DIR" "${SNAPSHOT_ARGS[@]}" >/dev/null

state_value() {
  local file="$1"
  local key="$2"
  awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$file" 2>/dev/null || true
}

timers_any_enabled() {
  local timer
  for timer in "${TIMERS[@]}"; do
    if systemctl is-enabled --quiet "$timer" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

collect_diagnostics() {
  local diag="$TX_DIR/diagnostics"
  mkdir -p "$diag"
  for service in opip-learning-sync.service opip-learning-capture.service opip-learning-outcomes.service; do
    journalctl -u "$service" -n 200 --no-pager >"$diag/${service}.log" 2>&1 || true
  done
  for file in \
    "$STATE_ROOT/capture.disposition.env" \
    "$STATE_ROOT/outcomes.disposition.env" \
    "$STATE_ROOT/release_compatibility.env" \
    "$STATE_ROOT/last-learning-deploy.env"; do
    [[ -r "$file" ]] && cp -- "$file" "$diag/" || true
  done
}

collect_failure() {
  local stage="$1"
  local rc="$2"
  set +e
  trap - ERR
  local after_current="" committed=false timers_enabled=false action rollback
  if [[ -r "$DATA_ROOT/canonical-replica/current" ]]; then
    after_current="$(tr -d '[:space:]' <"$DATA_ROOT/canonical-replica/current")"
  fi
  if [[ -n "$after_current" && "$after_current" != "$BEFORE_CURRENT" ]]; then
    committed=true
  fi
  if timers_any_enabled; then
    timers_enabled=true
  fi
  action="$(PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_recovery_transaction \
    decide --stage "$stage" --evidence-committed "$committed" \
    --timers-enabled "$timers_enabled" 2>/dev/null || echo KEEP_CONFIG_BLOCKED)"
  rollback="none"
  if [[ "$action" == "ROLLBACK_CONFIG" ]]; then
    if PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_recovery_transaction \
      restore --transaction-dir "$TX_DIR" >/dev/null; then
      rollback="restored_configuration"
      systemctl daemon-reload || true
    else
      rollback="rollback_failed"
    fi
  elif [[ "$action" == "KEEP_CONFIG_BLOCKED" ]]; then
    rollback="kept_configuration_evidence_committed"
  fi
  if timers_any_enabled; then
    systemctl disable --now "${TIMERS[@]}" >/dev/null 2>&1 || true
  fi
  collect_diagnostics
  TX_DIR="$TX_DIR" STAGE="$stage" RC="$rc" ACTION="$action" ROLLBACK="$rollback" \
  COMMITTED="$committed" BEFORE_CURRENT="$BEFORE_CURRENT" AFTER_CURRENT="$after_current" \
  TIMERS_ENABLED="$(timers_any_enabled && echo true || echo false)" \
  PRODUCTION_SHA="$PRODUCTION_SHA" TOOLING_SHA="$TOOLING_SHA" \
  python3 - <<'PY'
import json
import os
from pathlib import Path

transaction_dir = Path(os.environ["TX_DIR"])
payload = {
    "receipt": "OPIP_LEARNING_RECOVERY_FAILURE",
    "schema": 1,
    "status": "FAILED",
    "stage": os.environ["STAGE"],
    "exit_code": int(os.environ["RC"]),
    "action": os.environ["ACTION"],
    "rollback": os.environ["ROLLBACK"],
    "evidence_committed": os.environ["COMMITTED"] == "true",
    "before_current": os.environ["BEFORE_CURRENT"],
    "after_current": os.environ["AFTER_CURRENT"],
    "timers_enabled_after_failure": os.environ["TIMERS_ENABLED"] == "true",
    "production_sha": os.environ["PRODUCTION_SHA"],
    "tooling_sha": os.environ["TOOLING_SHA"],
    "transaction_dir": str(transaction_dir),
    "diagnostics_dir": str(transaction_dir / "diagnostics"),
    "historical_evidence_modified": False,
    "trading_authority_changed": False,
}
path = transaction_dir / "failure.json"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.flush()
    os.fsync(handle.fileno())
dir_fd = os.open(transaction_dir, os.O_RDONLY)
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
print(f"failure_receipt={path}")
PY
  echo "recovery_failed stage=$stage rc=$rc action=$action rollback=$rollback committed=$committed" >&2
  echo "transaction_dir=$TX_DIR" >&2
  exit "$rc"
}

on_error() {
  local rc="$1"
  collect_failure "${STAGE:-UNKNOWN}" "$rc"
}

trap 'on_error $?' ERR


# ---------------------------------------------------------------------------
# Authenticated production provenance. The production SHA is never trusted
# from the caller alone; it must equal the manifest served by the forced-command
# reader over the pinned production identity.
# ---------------------------------------------------------------------------
ARCHIVE="$(mktemp)"
MANIFEST="$(mktemp)"
cleanup() { rm -f -- "$ARCHIVE" "$MANIFEST"; }
trap cleanup EXIT
ssh \
  -i "$OPIP_LEARNING_SSH_KEY" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=10 \
  "$OPIP_PRODUCTION_USER@$OPIP_PRODUCTION_HOST" \
  opip-export-v1 >"$ARCHIVE"
tar -xOf "$ARCHIVE" manifest.env >"$MANIFEST"
manifest_value() {
  awk -F= -v k="$1" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$MANIFEST" 2>/dev/null || true
}
manifest_production_sha="$(manifest_value production_deployed_sha)"
manifest_schema="$(manifest_value schema_version)"
manifest_retired="$(manifest_value p1_shadow_outbox_retired)"
if [[ "$manifest_production_sha" != "$PRODUCTION_SHA" \
   || "$manifest_schema" != "4" \
   || "$manifest_retired" != "1" ]]; then
  echo "refusing recovery: authenticated production export disagrees" >&2
  echo "production=$PRODUCTION_SHA manifest=${manifest_production_sha:-MISSING} schema=${manifest_schema:-MISSING} retired=${manifest_retired:-MISSING}" >&2
  exit 65
fi

timers_enabled=false
for timer in "${TIMERS[@]}"; do
  if systemctl is-enabled --quiet "$timer" 2>/dev/null; then
    timers_enabled=true
  fi
done

PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_recovery_binding \
  --production-sha "$PRODUCTION_SHA" \
  --tooling-sha "$TOOLING_SHA" \
  --current-main-sha "$CURRENT_MAIN_SHA" \
  --manifest-sha "$manifest_production_sha" \
  --compare-status "$COMPARE_STATUS" \
  --tooling-ci-success "$TOOLING_CI" \
  --timers-enabled "$timers_enabled" >/dev/null

STAGE="BUILD"
IMAGE="opip-learning:recovery-${TOOLING_SHA:0:12}-prod-${PRODUCTION_SHA:0:12}"
docker build \
  --label "org.opencontainers.image.revision=$TOOLING_SHA" \
  --label "com.opip.recovery.production-sha=$PRODUCTION_SHA" \
  -t "$IMAGE" "$SOURCE_APP"
docker image prune -f >/dev/null 2>&1 || true
IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}')"

STAGE="INSTALL_SCRIPTS"
for script in opip-learning-sync.sh opip-learning-job.sh opip-learning-cleanup.sh; do
  target="/usr/local/sbin/${script%.sh}"
  install -o root -g root -m 0755 "$SOURCE_APP/deploy/learning/$script" "$target"
done

STAGE="INSTALL_UNITS"
for unit in \
  opip-learning-sync.service opip-learning-sync.timer \
  opip-learning-capture.service opip-learning-capture.timer \
  opip-learning-outcomes.service opip-learning-outcomes.timer; do
  install -o root -g root -m 0644 \
    "$SOURCE_APP/deploy/learning/$unit" \
    "/etc/systemd/system/$unit"
done

BINDING_REPORT="$(mktemp)"
PYTHONPATH="$SOURCE_APP" python3 -m app.services.learning_recovery_binding \
  --production-sha "$PRODUCTION_SHA" \
  --tooling-sha "$TOOLING_SHA" \
  --current-main-sha "$CURRENT_MAIN_SHA" \
  --manifest-sha "$manifest_production_sha" \
  --compare-status "$COMPARE_STATUS" \
  --tooling-ci-success "$TOOLING_CI" \
  --timers-enabled "$timers_enabled" \
  --json >"$BINDING_REPORT"
BINDING_SHA256="$(sha256sum "$BINDING_REPORT" | awk '{print $1}')"

RECEIPT="$RECEIPT_DIR/$(date -u +%Y%m%dT%H%M%SZ)-${TOOLING_SHA:0:12}-prod-${PRODUCTION_SHA:0:12}.json"
STAGE="ENV"
ENV_BACKUP="$RECEIPT_DIR/$(date -u +%Y%m%dT%H%M%SZ)-opip-learning.env.before-recovery"
install -o root -g root -m 0600 "$ENV_FILE" "$ENV_BACKUP"

normalized="$(mktemp)"
awk \
  -v production="$PRODUCTION_SHA" \
  -v tooling="$TOOLING_SHA" \
  -v image="$IMAGE" \
  -v receipt="$RECEIPT" '
  BEGIN { sha=0; code=0; img=0; bind=0 }
  /^OPIP_DEPLOYED_SHA=/ { print "OPIP_DEPLOYED_SHA=" production; sha=1; next }
  /^OPIP_LEARNING_CODE_SHA=/ { print "OPIP_LEARNING_CODE_SHA=" tooling; code=1; next }
  /^OPIP_LEARNING_IMAGE=/ { print "OPIP_LEARNING_IMAGE=" image; img=1; next }
  /^OPIP_LEARNING_RECOVERY_BINDING=/ {
    print "OPIP_LEARNING_RECOVERY_BINDING=" receipt; bind=1; next
  }
  { print }
  END {
    if (!sha) print "OPIP_DEPLOYED_SHA=" production
    if (!code) print "OPIP_LEARNING_CODE_SHA=" tooling
    if (!img) print "OPIP_LEARNING_IMAGE=" image
    if (!bind) print "OPIP_LEARNING_RECOVERY_BINDING=" receipt
  }
' "$ENV_FILE" >"$normalized"
chmod 0600 "$normalized"
mv -f -- "$normalized" "$ENV_FILE"

systemctl daemon-reload

# One-shot acceptance. Timers stay disabled regardless of outcome.
STAGE="SYNC"
systemctl start opip-learning-sync.service
SYNCED_SHA="$(state_value "$DATA_ROOT/data/manifest.env" production_deployed_sha)"
if [[ "$SYNCED_SHA" != "$PRODUCTION_SHA" ]]; then
  echo "refusing recovery completion: sync did not bind production SHA" >&2
  echo "synced=${SYNCED_SHA:-MISSING} production=$PRODUCTION_SHA" >&2
  collect_failure "$STAGE" 75
fi

STAGE="CAPTURE"
systemctl start opip-learning-capture.service
CAPTURE_DISPOSITION="$(state_value "$STATE_ROOT/capture.disposition.env" disposition)"
CAPTURE_RELEASE="$(state_value "$STATE_ROOT/capture.disposition.env" release_compatibility_status)"
if [[ "$CAPTURE_DISPOSITION" != "CONSUMED_EMPTY" || "$CAPTURE_RELEASE" != "CURRENT" ]]; then
  echo "refusing recovery completion: capture one-shot not consumed-empty/current" >&2
  collect_failure "$STAGE" 75
fi

STAGE="OUTCOMES"
systemctl start opip-learning-outcomes.service
OUTCOMES_DISPOSITION="$(state_value "$STATE_ROOT/outcomes.disposition.env" disposition)"
OUTCOMES_RELEASE="$(state_value "$STATE_ROOT/outcomes.disposition.env" release_compatibility_status)"
if [[ "$OUTCOMES_RELEASE" != "CURRENT" \
   || ( "$OUTCOMES_DISPOSITION" != "CONSUMED_OK" && "$OUTCOMES_DISPOSITION" != "CONSUMED_EMPTY" ) ]]; then
  echo "refusing recovery completion: outcomes one-shot not consumed/current" >&2
  collect_failure "$STAGE" 75
fi

STAGE="FINAL_SYNC"
systemctl start opip-learning-sync.service
mapfile -t remaining_jobs < <(docker ps -aq --filter label=com.opip.learning.job)
if (( ${#remaining_jobs[@]} > 0 )); then
  echo "refusing recovery completion: orphan learning containers remain" >&2
  collect_failure "$STAGE" 75
fi

STAGE="RECEIPT"
AFTER_CURRENT=""
if [[ -r "$DATA_ROOT/canonical-replica/current" ]]; then
  AFTER_CURRENT="$(tr -d '[:space:]' <"$DATA_ROOT/canonical-replica/current")"
fi
COMPATIBILITY_SHA256="$(sha256sum "$COMPATIBILITY_MANIFEST" | awk '{print $1}')"

RECEIPT_PATH="$RECEIPT" \
PRODUCTION_SHA="$PRODUCTION_SHA" \
TOOLING_SHA="$TOOLING_SHA" \
CURRENT_MAIN_SHA="$CURRENT_MAIN_SHA" \
COMPARE_STATUS="$COMPARE_STATUS" \
MANIFEST_SHA="$manifest_production_sha" \
IMAGE="$IMAGE" \
IMAGE_ID="$IMAGE_ID" \
TX_DIR="$TX_DIR" \
BEFORE_CURRENT="$BEFORE_CURRENT" \
AFTER_CURRENT="$AFTER_CURRENT" \
COMPATIBILITY_SHA256="$COMPATIBILITY_SHA256" \
ARCHIVE_DIR="${ARCHIVE_DIR:-}" \
BINDING_REPORT="$BINDING_REPORT" \
BINDING_SHA256="$BINDING_SHA256" \
ENV_BACKUP="$ENV_BACKUP" \
CAPTURE_DISPOSITION="$CAPTURE_DISPOSITION" \
OUTCOMES_DISPOSITION="$OUTCOMES_DISPOSITION" \
python3 - <<'PY'
import datetime
import json
import os
from pathlib import Path

report_path = Path(os.environ["BINDING_REPORT"])
report = json.loads(report_path.read_text(encoding="utf-8"))
payload = {
    "receipt": "OPIP_LEARNING_RECOVERY_DEPLOY",
    "schema": 1,
    "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc)
    .replace(microsecond=0)
    .isoformat(),
    "production_sha": os.environ["PRODUCTION_SHA"],
    "tooling_sha": os.environ["TOOLING_SHA"],
    "current_main_sha": os.environ["CURRENT_MAIN_SHA"],
    "manifest_production_sha": os.environ["MANIFEST_SHA"],
    "compare_status": os.environ["COMPARE_STATUS"],
    "image": os.environ["IMAGE"],
    "image_id": os.environ["IMAGE_ID"],
    "image_revision_label": os.environ["TOOLING_SHA"],
    "binding_report_sha256": os.environ["BINDING_SHA256"],
    "env_backup_path": os.environ["ENV_BACKUP"],
    "transaction_dir": os.environ["TX_DIR"],
    "config_snapshot": str(Path(os.environ["TX_DIR"]) / "snapshot.json"),
    "compatibility_manifest_sha256": os.environ["COMPATIBILITY_SHA256"],
    "lineage_archive_dir": os.environ.get("ARCHIVE_DIR") or None,
    "replica_current_before": os.environ["BEFORE_CURRENT"],
    "replica_current_after": os.environ["AFTER_CURRENT"],
    "evidence_generation_committed": (
        bool(os.environ["AFTER_CURRENT"])
        and os.environ["AFTER_CURRENT"] != os.environ["BEFORE_CURRENT"]
    ),
    "binding_checks": report.get("checks", []),
    "binding_status": report.get("status"),
    "capture_disposition": os.environ["CAPTURE_DISPOSITION"],
    "outcomes_disposition": os.environ["OUTCOMES_DISPOSITION"],
    "release_compatibility_status": "CURRENT",
    "timers_enabled": False,
    "timers_enabled_by_recovery": False,
    "measurement_only": True,
    "trade_authority_changed": False,
    "policy_change_authorized": False,
}
path = Path(os.environ["RECEIPT_PATH"])
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.flush()
    os.fsync(handle.fileno())
dir_fd = os.open(path.parent, os.O_RDONLY)
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
print(f"receipt={path}")
PY

echo "O'Pip recovery learning deploy succeeded"
echo "production_sha=$PRODUCTION_SHA"
echo "tooling_sha=$TOOLING_SHA"
echo "image=$IMAGE"
echo "timers_enabled=false"
echo "env_backup=$ENV_BACKUP"
echo "rollback=install -m 0600 $ENV_BACKUP $ENV_FILE && systemctl daemon-reload"
