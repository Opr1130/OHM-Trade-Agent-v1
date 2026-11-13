#!/usr/bin/env bash
set -Eeuo pipefail

# The legacy export cron may be reinstalled by scheduler reconciliation.
# Learning is off by default; do not touch locks, files or the canonical DB.
[[ -f /etc/opip/learning-export.enabled && ! -L /etc/opip/learning-export.enabled ]] || exit 0

# Migration audit: the pre-retirement export contract used schema_version=3.
# Active output below is schema 4 and intentionally excludes the P1 shadow outbox.

APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
DATA_ROOT="$APP_ROOT/data"
EXPORT_ROOT="/var/lib/opip-learning-export"
# Canonical replica snapshot/manifest logic lives in Python; this script only
# orchestrates. Use the same interpreter convention as the rest of the
# production scripts (python3 on the Debian droplet).
PYTHON_BIN="${OPIP_PYTHON_BIN:-/usr/bin/python3}"
TRIGGER_LOCK="/var/run/opip-learning-export.lock"
PUBLISH_LOCK="$EXPORT_ROOT/.publish.lock"
READER_GROUP="opiplearn"
# Authoritative production release receipt, published by ohm-deploy at its commit
# point. Every artifact this exporter publishes is bound to it.
RELEASE_RECEIPT="/var/lib/ohm-deploy/last-good-sha"

# Read the authoritative production release SHA, or print nothing when the
# receipt is absent or malformed.
#
# One implementation only: the generation identity, the empty-export attestation
# and the pre-commit revalidation must never disagree about which release the
# evidence belongs to. Never falls back to the checked-out revision - an export
# that cannot be attributed to a release must stay unattributable, not guessed.
read_authoritative_release_sha() {
  local sha
  sha="$(cat "$RELEASE_RECEIPT" 2>/dev/null || true)"
  if [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    printf '%s' "$sha"
  fi
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run O'Pip learning evidence export as root" >&2
  exit 77
fi

for cmd in install flock cp mv stat date sha256sum getent chown chmod touch dirname rm find sort xargs awk grep tr; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required export command: $cmd" >&2
    exit 69
  }
done
[[ -x "$PYTHON_BIN" ]] || {
  echo "missing executable O'Pip Python interpreter: $PYTHON_BIN" >&2
  exit 69
}

if getent group "$READER_GROUP" >/dev/null 2>&1; then
  install -d -o root -g "$READER_GROUP" -m 0750 "$EXPORT_ROOT"
else
  install -d -o root -g root -m 0700 "$EXPORT_ROOT"
fi

exec 9>"$TRIGGER_LOCK"
if ! flock -n 9; then
  echo "O'Pip learning export already active; skipping"
  exit 0
fi

# Remove at most one abandoned *staging* tree per export, never a committed
# content-addressed replica. This runs after winning the sole exporter lock,
# but before the reader-facing publish lock. Require a full 24-hour grace
# period, the root-owned 0700 staging contract, and a non-live originating PID.
# Fresh, live, nonconforming and symlinked paths are never removed. Cleanup is
# advisory: a filesystem cleanup failure must not prevent valid evidence.
cleanup_abandoned_replica_staging() {
  local candidate pid owner_mode
  while IFS= read -r -d '' candidate; do
    [[ ! -L "$candidate" ]] || continue
    pid="${candidate##*.}"
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    [[ "$pid" != "$$" ]] || continue
    owner_mode="$(stat -c '%u:%a' -- "$candidate" 2>/dev/null || true)"
    [[ "$owner_mode" == "${EUID}:700" ]] || continue
    if kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    if rm -rf -- "$candidate"; then
      echo "O'Pip learning export: pruned one abandoned staging tree"
    else
      echo "O'Pip learning export: abandoned staging cleanup failed" >&2
    fi
    break
  done < <(find "$EXPORT_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name '.canonical_learning_replica.staging.*' -mmin +1440 -print0)
}
cleanup_abandoned_replica_staging

touch "$PUBLISH_LOCK"
if getent group "$READER_GROUP" >/dev/null 2>&1; then
  chown root:"$READER_GROUP" "$PUBLISH_LOCK"
  chmod 0640 "$PUBLISH_LOCK"
else
  chown root:root "$PUBLISH_LOCK"
  chmod 0600 "$PUBLISH_LOCK"
fi

# Readers take a shared lock on this same file. Holding the exclusive lock for
# the complete publish guarantees they can never receive mixed generations.
exec 8>"$PUBLISH_LOCK"
flock -x 8

# Write export-tree empty attestation from canonical copied files only.
# Never writes into DATA_ROOT. Eligibility must match
# production_empty_export_attestation_eligible() in
# app/opip/learning/empty_export_attestation.py. A complete=false
# window-index is ambiguous production lineage and never mints proof.
# When state.json exists it must match the exact certified-empty schema
# (same keys/values as _window_index_state_proves_empty_archive_without_manifest).
state_json_is_certified_empty_without_manifest() {
  local state="$1"
  [[ -f "$state" ]] || return 1
  local compact
  # Production writers emit compact sorted JSON. Strip insignificant
  # whitespace so a pretty-printed certified-empty state still matches.
  # Fail closed on any other key set, value, or malformed fragment.
  compact="$(tr -d '[:space:]' <"$state")"
  [[ -n "$compact" ]] || return 1
  [[ "$compact" =~ ^\{\"complete\":true,\"coverage_day_count\":0,\"coverage_start_day\":null,\"coverage_through_day\":null,\"manifest_mtime_ns\":0,\"manifest_present\":false,\"manifest_sha256\":\"\",\"manifest_size\":0,\"schema_version\":1,\"shard_sha256\":\{\},\"updated_at_utc\":\"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})\"\}$ ]]
}

write_empty_export_attestation_if_canonical() {
  local hot_file="$1"
  local archive_dir="$2"
  local prefix="$3"
  [[ -n "$archive_dir" && -d "$archive_dir" && -n "$prefix" ]] || return 0

  local hot_bytes=0
  if [[ -f "$hot_file" ]]; then
    hot_bytes="$(stat -c '%s' "$hot_file")"
  fi
  if [[ "$hot_bytes" != "0" ]]; then
    return 0
  fi
  if [[ -f "$archive_dir/manifest.json" || -f "$archive_dir/manifest.json.sha256" ]]; then
    return 0
  fi

  local segment_count=0
  local segment
  while IFS= read -r -d '' segment; do
    segment_count=$((segment_count + 1))
  done < <(find "$archive_dir" -type f -name "${prefix}-*.jsonl.gz" -print0)
  if (( segment_count > 0 )); then
    return 0
  fi

  local index_dir="$archive_dir/window_index_v1"
  if [[ -d "$index_dir" ]]; then
    local extra_index=0
    local extra
    while IFS= read -r -d '' extra; do
      extra_index=$((extra_index + 1))
    done < <(find "$index_dir" -mindepth 1 ! -name 'state.json' -print0)
    if (( extra_index > 0 )); then
      return 0
    fi
    local state="$index_dir/state.json"
    if [[ ! -f "$state" ]]; then
      return 0
    fi
    if ! state_json_is_certified_empty_without_manifest "$state"; then
      return 0
    fi
  fi

  local sha
  # Bind every copied attestation to the same generation as the replica.
  # A release flip during copying is rejected at the manifest commit point.
  sha="$production_deployed_sha"
  local exported_at
  exported_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s\n' "{\"archive_prefix\":\"${prefix}\",\"exported_at_utc\":\"${exported_at}\",\"hot_bytes\":0,\"kind\":\"empty_export_attestation_v1\",\"manifest_present\":false,\"production_deployed_sha\":\"${sha}\",\"schema_version\":1,\"segment_count\":0,\"signature_present\":false}" > "$archive_dir/empty_export_attestation_v1.json"
}

copy_locked_jsonl() {
  local source="$1"
  local name="$2"
  local archive_source="${3:-}"
  local archive_name="${4:-}"
  local source_lock="$(dirname "$source")/.$(basename "$source").lock"
  local temp="$EXPORT_ROOT/.$name.tmp.$$"
  local target="$EXPORT_ROOT/$name"
  local archive_temp=""
  local archive_target=""

  if getent group "$READER_GROUP" >/dev/null 2>&1; then
    install -d -o root -g "$READER_GROUP" -m 0750 \
      "$(dirname "$temp")" "$(dirname "$target")"
  else
    install -d -o root -g root -m 0700 \
      "$(dirname "$temp")" "$(dirname "$target")"
  fi
  if [[ -n "$archive_name" ]]; then
    archive_temp="$EXPORT_ROOT/.$archive_name.tmp.$$"
    archive_target="$EXPORT_ROOT/$archive_name"
    rm -rf -- "$archive_temp"
    if getent group "$READER_GROUP" >/dev/null 2>&1; then
      install -d -o root -g "$READER_GROUP" -m 0750 "$archive_temp"
    else
      install -d -o root -g root -m 0700 "$archive_temp"
    fi
  fi

  exec {source_fd}>>"$source_lock"
  flock -s "$source_fd"
  if [[ -e "$source" ]]; then
    cp -- "$source" "$temp"
  else
    : > "$temp"
  fi
  if [[ -n "$archive_name" && -d "$archive_source" ]]; then
    cp -a -- "$archive_source/." "$archive_temp/"
  fi
  flock -u "$source_fd"
  eval "exec ${source_fd}>&-"

  if [[ -n "$archive_name" ]]; then
    write_empty_export_attestation_if_canonical \
      "$temp" \
      "$archive_temp" \
      "$(basename "$source" .jsonl)"
  fi

  if getent group "$READER_GROUP" >/dev/null 2>&1; then
    chown root:"$READER_GROUP" "$temp"
    chmod 0640 "$temp"
  else
    chown root:root "$temp"
    chmod 0600 "$temp"
  fi
  mv -f -- "$temp" "$target"
  if [[ -n "$archive_name" ]]; then
    if getent group "$READER_GROUP" >/dev/null 2>&1; then
      chown -R root:"$READER_GROUP" "$archive_temp"
      find "$archive_temp" -type d -exec chmod 0750 {} +
      find "$archive_temp" -type f -exec chmod 0640 {} +
    else
      chown -R root:root "$archive_temp"
      find "$archive_temp" -type d -exec chmod 0700 {} +
      find "$archive_temp" -type f -exec chmod 0600 {} +
    fi
    rm -rf -- "$archive_target"
    mv -f -- "$archive_temp" "$archive_target"
  fi
}

# A release can change while a scheduled export holds both locks. Retry the
# entire generation once, under those same locks, immediately after a
# commit-point refusal instead of waiting for another cron tick. A second
# release change fails closed; there is never an unbounded regeneration loop.
# The SHA is captured BEFORE copying any artifact/attestation, so the final
# revalidation covers the entire generation rather than only the replica.
for generation_attempt in 1 2; do
production_deployed_sha="$(read_authoritative_release_sha)"

# P1 shadow outbox was retired after it grew large enough to choke the evidence
# plane. Never republish the legacy artifact; remove any stale exported copy
# while holding the exclusive publish lock.
rm -f -- "$EXPORT_ROOT/p1_shadow_outbox.jsonl"

copy_locked_jsonl "$DATA_ROOT/full_market_observations.jsonl" "full_market_observations.jsonl"
copy_locked_jsonl "$DATA_ROOT/p1_evidence_ledger.jsonl" "p1_evidence_ledger.jsonl"
copy_locked_jsonl "$DATA_ROOT/intelligence_learning/events.jsonl" "intelligence_learning/events.jsonl"
copy_locked_jsonl "$DATA_ROOT/opip/qualification/screening_evaluations.jsonl" "opip/qualification/screening_evaluations.jsonl" "$DATA_ROOT/opip/qualification/screening_evaluations_archive" "opip/qualification/screening_evaluations_archive"
copy_locked_jsonl "$DATA_ROOT/opip/qualification/funnel_events.jsonl" "opip/qualification/funnel_events.jsonl" "$DATA_ROOT/opip/qualification/funnel_events_archive" "opip/qualification/funnel_events_archive"
copy_locked_jsonl "$DATA_ROOT/opip/qualification/scan_summaries.jsonl" "opip/qualification/scan_summaries.jsonl" "$DATA_ROOT/opip/qualification/scan_summaries_archive" "opip/qualification/scan_summaries_archive"
copy_locked_jsonl "$DATA_ROOT/paper_trading/events.jsonl" "paper_trading/events.jsonl"
copy_locked_jsonl "$DATA_ROOT/telegram_delivery_events.jsonl" "telegram_delivery_events.jsonl"
copy_locked_jsonl "$DATA_ROOT/decision_telemetry.jsonl" "decision_telemetry.jsonl"
copy_locked_jsonl "$DATA_ROOT/opip_trade_quality_evidence_v1.jsonl" "opip_trade_quality_evidence_v1.jsonl"
copy_locked_jsonl "$DATA_ROOT/candidate_trace.jsonl" "candidate_trace.jsonl"

# Deterministic tree size and digest. Used to bind the canonical replica bundle
# at the outer transport layer just as archive directories are bound.
tree_bytes() {
  find "$1" -type f -printf '%s\n' | awk '{total += $1} END {printf "%d\n", total}'
}
tree_sha256() {
  (
    cd "$1"
    find . -type f -print0 | sort -z | xargs -0 -r sha256sum
  ) | sha256sum | awk '{print $1}'
}

read_replica_tree_digest() {
  # Content address ONE replica tree using the single shared implementation.
  #
  # The exporter and the deploy-side verifier must never drift, so neither
  # recomputes the digest itself: both call the same module. Note the ordering is
  # a byte-sort of each file's record, defined in Python, rather than the
  # locale-dependent shell sort this replaced - a content address must be a pure
  # function of content, not of the environment that produced it.
  local root="$1"
  local out
  out="$(PYTHONPATH="$APP_ROOT" "$PYTHON_BIN" -m app.opip.learning.canonical_replica \
    tree-digest --root "$root" 2>/dev/null)" || return 1
  TREE_SHA256="$(printf '%s\n' "$out" | awk -F= '$1 == "OPIP_REPLICA_TREE_SHA256" {print $2; exit}')"
  TREE_BYTES="$(printf '%s\n' "$out" | awk -F= '$1 == "OPIP_REPLICA_TREE_BYTES" {print $2; exit}')"
  [[ "$TREE_SHA256" =~ ^[0-9a-f]{64}$ && "$TREE_BYTES" =~ ^[0-9]+$ ]]
}

# ---------------------------------------------------------------------------
# Canonical learning replica bundle.
#
# The replica is release-bound evidence, so it requires an authoritative
# production release SHA. If none exists we deliberately do NOT publish the v1
# marker: a bundle attributed to an unknown release could never be accepted by
# learning anyway, and minting provenance with an empty SHA would be a false
# claim. Legacy artifacts above keep their existing UNVERIFIED behaviour.
#
# All snapshot/manifest logic lives in Python. This shell only orchestrates.
# ---------------------------------------------------------------------------
# The generation SHA was captured before the artifact copies above.

REPLICA_EXPORT_NAME="canonical_learning_replica"
REPLICA_STAGING="$EXPORT_ROOT/.$REPLICA_EXPORT_NAME.staging.$$"
REPLICA_PUBLISH_NAME=""
REPLICA_PUBLISH_DIR=""
# Set only when this run moved its own staging tree into place. A reused
# content-addressed directory may still be the live generation, so cleanup after
# a refused commit must never assume it owns an existing directory.
REPLICA_CREATED_THIS_RUN=""
REPLICA_MARKER_LINES=""

rm -rf -- "$REPLICA_STAGING"

if [[ -z "$production_deployed_sha" ]]; then
  echo "O'Pip learning export: canonical replica skipped (no authoritative deployed SHA)"
  REPLICA_MARKER_LINES=""
else
  install -d -m 0700 "$REPLICA_STAGING"
  replica_rc=0
  PYTHONPATH="$APP_ROOT" "$PYTHON_BIN" -m app.opip.learning.canonical_replica export \
    --source-db "$DATA_ROOT/opip/canonical/opip_canonical_v1.sqlite3" \
    --staging "$REPLICA_STAGING" \
    --release-sha "$production_deployed_sha" \
    --paper-state "$DATA_ROOT/paper_trading/state.json" \
    --paper-gap "$DATA_ROOT/paper_trading/evidence_gap_spool.json" \
    >/dev/null || replica_rc=$?

  if (( replica_rc != 0 )); then
    # Fail visibly and publish nothing for the replica. The legacy export
    # remains uncommitted because manifest.env is the generation commit marker.
    echo "O'Pip learning export: canonical replica export FAILED (rc=$replica_rc)" >&2
    rm -rf -- "$REPLICA_STAGING"
    echo "O'Pip learning evidence export: JSON artifacts OK, canonical replica FAILED" >&2
    exit 70
  fi

  if getent group "$READER_GROUP" >/dev/null 2>&1; then
    chown -R root:"$READER_GROUP" "$REPLICA_STAGING"
  else
    chown -R root:root "$REPLICA_STAGING"
  fi
  find "$REPLICA_STAGING" -type d -exec chmod 0750 {} +
  find "$REPLICA_STAGING" -type f -exec chmod 0640 {} +

  # Publish into a content-addressed directory that has never represented a
  # different generation. The previously committed directory remains present
  # until manifest.env has atomically committed the new directory name, so a
  # crash before the manifest flip cannot destroy the last known-good replica.
  if ! read_replica_tree_digest "$REPLICA_STAGING"; then
    echo "O'Pip learning export: canonical replica content address failed (rc=70)" >&2
    rm -rf -- "$REPLICA_STAGING"
    exit 70
  fi
  replica_bytes="$TREE_BYTES"
  replica_sha="$TREE_SHA256"
  REPLICA_PUBLISH_NAME="${REPLICA_EXPORT_NAME}.${replica_sha}"
  REPLICA_PUBLISH_DIR="$EXPORT_ROOT/$REPLICA_PUBLISH_NAME"
  if [[ -e "$REPLICA_PUBLISH_DIR" ]]; then
    if ! read_replica_tree_digest "$REPLICA_PUBLISH_DIR"; then
      echo "O'Pip learning export: existing replica content address failed at $REPLICA_PUBLISH_NAME" >&2
      rm -rf -- "$REPLICA_STAGING"
      exit 70
    fi
    existing_bytes="$TREE_BYTES"
    existing_sha="$TREE_SHA256"
    if [[ "$existing_bytes" != "$replica_bytes" || "$existing_sha" != "$replica_sha" ]]; then
      echo "O'Pip learning export: content-addressed replica collision at $REPLICA_PUBLISH_NAME" >&2
      rm -rf -- "$REPLICA_STAGING"
      exit 70
    fi
    rm -rf -- "$REPLICA_STAGING"
  else
    mv -- "$REPLICA_STAGING" "$REPLICA_PUBLISH_DIR"
    REPLICA_CREATED_THIS_RUN=1
  fi

  REPLICA_MARKER_LINES="canonical_learning_replica_version=1
canonical_learning_replica_dir=${REPLICA_PUBLISH_NAME}
canonical_learning_replica_bytes=${replica_bytes}
canonical_learning_replica_sha256=${replica_sha}"
  echo "O'Pip learning export: canonical replica bundle OK bytes=${replica_bytes}"
fi

# ---------------------------------------------------------------------------
# Commit-point release revalidation.
#
# The generation identity is captured once and then baked into the copied
# artifacts, the inner replica manifest and the outer manifest. Publishing that
# generation happens only after the artifact copy and the canonical replica
# export - tens of seconds, sometimes minutes. For that whole window this run
# holds the export lock, but ohm-deploy does *not*: it publishes
# RELEASE_RECEIPT at its own commit point without participating in this lock.
#
# Re-reading the receipt at the commit point is therefore the only way to know
# the generation still describes the deployed release. Without it a deploy that
# flips the receipt mid-generation receives a fresh, internally consistent
# export bound to the *previous* release, which the deploy-side readiness gate
# must then reject as LEARNING_EXPORT_RELEASE_SHA_MISMATCH even though nothing
# about the export is actually stale.
#
# When the identity has moved, the generation is discarded rather than
# relabelled: rewriting the SHA into evidence that was built for another release
# would mint a provenance claim the artifacts cannot support. The committed
# manifest is left exactly as it was, so the previous valid generation stays
# authoritative; the unpublished replica directory is removed so retention stays
# bounded; and the run fails closed with rc=75 so the next scheduled run
# regenerates against the release that is now deployed.
# ---------------------------------------------------------------------------
commit_export_or_discard() {
  local generation_sha="$1"
  local manifest_tmp="$2"
  local commit_sha
  commit_sha="$(read_authoritative_release_sha)"
  if [[ "$commit_sha" == "$generation_sha" ]]; then
    return 0
  fi

  echo "O'Pip learning export: production release changed during generation (was ${generation_sha:-none}, now ${commit_sha:-none}); discarding uncommitted export and preserving the previous committed export" >&2
  rm -f -- "$manifest_tmp"
  rm -rf -- "$REPLICA_STAGING"

  # Only a directory this run created may be removed. A reused content-addressed
  # directory can still be the one the committed manifest references, so
  # deleting it would destroy the last valid evidence instead of protecting it.
  local committed_dir=""
  if [[ -r "$EXPORT_ROOT/manifest.env" ]]; then
    committed_dir="$(awk -F= '$1 == "canonical_learning_replica_dir" { sub(/^[^=]*=/, ""); print; exit }' "$EXPORT_ROOT/manifest.env" 2>/dev/null || true)"
  fi
  if [[ -n "$REPLICA_CREATED_THIS_RUN" && -n "$REPLICA_PUBLISH_NAME" \
    && "$committed_dir" != "$REPLICA_PUBLISH_NAME" ]]; then
    rm -rf -- "$REPLICA_PUBLISH_DIR"
  fi
  return 75
}

manifest_tmp="$EXPORT_ROOT/.manifest.env.tmp.$$"

{
  printf 'schema_version=4\n'
  printf 'exported_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'production_deployed_sha=%s\n' "$production_deployed_sha"
  printf 'p1_shadow_outbox_retired=1\n'
  # Additive schema-4 extension. Absent when no canonical bundle was published,
  # so a legacy reader sees exactly the schema it saw before.
  if [[ -n "$REPLICA_MARKER_LINES" ]]; then
    printf '%s\n' "$REPLICA_MARKER_LINES"
  fi
  while IFS='|' read -r name key; do
    path="$EXPORT_ROOT/$name"
    printf '%s_bytes=%s\n' "$key" "$(stat -c '%s' "$path")"
    printf '%s_sha256=%s\n' "$key" "$(sha256sum "$path" | awk '{print $1}')"
  done <<'ARTIFACTS'
full_market_observations.jsonl|full_market_observations_jsonl
p1_evidence_ledger.jsonl|p1_evidence_ledger_jsonl
intelligence_learning/events.jsonl|intelligence_learning_events_jsonl
opip/qualification/screening_evaluations.jsonl|opip_qualification_screening_evaluations_jsonl
opip/qualification/funnel_events.jsonl|opip_qualification_funnel_events_jsonl
opip/qualification/scan_summaries.jsonl|opip_qualification_scan_summaries_jsonl
paper_trading/events.jsonl|paper_trading_events_jsonl
telegram_delivery_events.jsonl|telegram_delivery_events_jsonl
decision_telemetry.jsonl|decision_telemetry_jsonl
opip_trade_quality_evidence_v1.jsonl|opip_trade_quality_evidence_v1_jsonl
candidate_trace.jsonl|candidate_trace_jsonl
ARTIFACTS
  while IFS='|' read -r name key; do
    path="$EXPORT_ROOT/$name"
    printf '%s_bytes=%s\n' "$key" "$(tree_bytes "$path")"
    printf '%s_sha256=%s\n' "$key" "$(tree_sha256 "$path")"
  done <<'ARCHIVES'
opip/qualification/screening_evaluations_archive|opip_qualification_screening_archive
opip/qualification/funnel_events_archive|opip_qualification_funnel_archive
opip/qualification/scan_summaries_archive|opip_qualification_summaries_archive
ARCHIVES
} > "$manifest_tmp"

if getent group "$READER_GROUP" >/dev/null 2>&1; then
  chown root:"$READER_GROUP" "$manifest_tmp"
  chmod 0640 "$manifest_tmp"
else
  chown root:root "$manifest_tmp"
  chmod 0600 "$manifest_tmp"
fi

# The commit point. Everything above is still unpublishable, and this is the last
# moment at which the release identity can still disagree with the generation.
# Placed immediately before the rename so the unvalidated window is a single
# syscall rather than an attribute update plus a rename.
revalidation_rc=0
commit_export_or_discard "$production_deployed_sha" "$manifest_tmp" || revalidation_rc=$?
if (( revalidation_rc != 0 )); then
  if (( generation_attempt == 1 )) && [[ -n "$(read_authoritative_release_sha)" ]]; then
    echo "O'Pip learning export: bounded retry after release change (attempt 2 of 2)"
    continue
  fi
  # No valid new release or a second concurrent release change: fail closed.
  exit 75
fi
mv -f -- "$manifest_tmp" "$EXPORT_ROOT/manifest.env"

# manifest.env is the commit marker. Only after it points at the new immutable
# directory may older/uncommitted replica directories be removed. Therefore an
# interrupted pre-commit export always leaves the previous committed directory
# available to the forced reader.
if [[ -n "$REPLICA_PUBLISH_NAME" ]]; then
  while IFS= read -r -d '' old_replica; do
    [[ "$(basename "$old_replica")" == "$REPLICA_PUBLISH_NAME" ]] && continue
    rm -rf -- "$old_replica"
  done < <(find "$EXPORT_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name "${REPLICA_EXPORT_NAME}.*" -print0)
else
  find "$EXPORT_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name "${REPLICA_EXPORT_NAME}.*" -exec rm -rf -- {} +
fi

echo "O'Pip learning evidence export: OK"
break
done
