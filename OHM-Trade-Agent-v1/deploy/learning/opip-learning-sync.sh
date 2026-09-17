#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="/etc/opip-learning.env"
LOCK_FILE="/var/lock/opip-learning-plane.lock"
DATA_ROOT="/var/lib/opip-learning/data"
STATE_ROOT="/var/lib/opip-learning/state"
INCOMING="$DATA_ROOT/.incoming"
ARCHIVE="$DATA_ROOT/.export.tar"
# Dedicated canonical replica store, deliberately OUTSIDE the writable data
# root: the data root is mounted writable into learning job containers, so a
# replica stored beneath it could be reached through a writable alias.
CANONICAL_REPLICA_ROOT="${OPIP_LEARNING_CANONICAL_REPLICA_ROOT:-/var/lib/opip-learning/canonical-replica}"
CANONICAL_REPLICA_INCOMING_NAME="canonical_learning_replica"

[[ -r "$ENV_FILE" ]] || {
  echo "missing O'Pip learning environment: $ENV_FILE" >&2
  exit 78
}
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${OPIP_PRODUCTION_HOST:?OPIP_PRODUCTION_HOST is required}"
: "${OPIP_PRODUCTION_USER:?OPIP_PRODUCTION_USER is required}"
: "${OPIP_DEPLOYED_SHA:?OPIP_DEPLOYED_SHA is required}"
: "${OPIP_LEARNING_SSH_KEY:=/root/.ssh/opip-learning}"
# The application package is guaranteed inside the learning image, not as a
# host-installed Python package. Replica verification/install must run in the
# exact configured image rather than a host interpreter.
: "${OPIP_LEARNING_IMAGE:?OPIP_LEARNING_IMAGE is required}"

[[ "$OPIP_DEPLOYED_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "invalid OPIP_DEPLOYED_SHA" >&2
  exit 78
}

# The replica store must never live under the writable data root.
case "$CANONICAL_REPLICA_ROOT/" in
  "$DATA_ROOT"/*)
    echo "O'Pip learning sync: canonical replica root must not be beneath the data root" >&2
    exit 78
    ;;
esac
[[ "$CANONICAL_REPLICA_ROOT" != "$DATA_ROOT" ]] || {
  echo "O'Pip learning sync: canonical replica root must differ from the data root" >&2
  exit 78
}

for cmd in ssh tar install flock mv date sha256sum stat awk rm find sort xargs docker; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing learning sync command: $cmd" >&2
    exit 69
  }
done

install -d -o root -g root -m 0755 "$DATA_ROOT" "$INCOMING" "$STATE_ROOT"
# Root-owned and not group/world writable: only this sync script writes here.
install -d -o root -g root -m 0755 "$CANONICAL_REPLICA_ROOT"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "O'Pip learning plane busy; sync skipped"
  exit 0
fi

state_value() {
  local file="$1"
  local key="$2"
  awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$file" 2>/dev/null || true
}

status_time() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    printf '%s\n' "$raw"
  else
    printf 'NONE\n'
  fi
}

status_rc() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]{1,3}$ ]]; then
    printf '%s\n' "$raw"
  else
    printf 'NONE\n'
  fi
}

capture_file="$STATE_ROOT/capture.last.env"
outcomes_file="$STATE_ROOT/outcomes.last.env"
capture_disposition_file="$STATE_ROOT/capture.disposition.env"
outcomes_disposition_file="$STATE_ROOT/outcomes.disposition.env"
last_sync_file="$DATA_ROOT/.last_sync"
last_sync_at="$(status_time "$(state_value "$last_sync_file" last_sync_at_utc)")"
capture_at="$(status_time "$(state_value "$capture_file" finished_at_utc)")"
capture_rc="$(status_rc "$(state_value "$capture_file" exit_code)")"
outcomes_at="$(status_time "$(state_value "$outcomes_file" finished_at_utc)")"
outcomes_rc="$(status_rc "$(state_value "$outcomes_file" exit_code)")"

status_token() {
  local raw="$1"
  local fallback="${2:-NONE}"
  if [[ "$raw" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
    printf '%s\n' "$raw"
  else
    printf '%s\n' "$fallback"
  fi
}

status_count() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]{1,9}$ ]]; then
    printf '%s\n' "$raw"
  else
    printf 'NONE\n'
  fi
}

capture_disposition="$(status_token "$(state_value "$capture_disposition_file" disposition)")"
outcomes_disposition="$(status_token "$(state_value "$outcomes_disposition_file" disposition)")"
release_compatibility="$(status_token "$(state_value "$outcomes_disposition_file" release_compatibility_status)" NONE)"
if [[ "$release_compatibility" == "NONE" ]]; then
  release_compatibility="$(status_token "$(state_value "$capture_disposition_file" release_compatibility_status)" NONE)"
fi
pending_ack="$(status_count "$(state_value "$outcomes_disposition_file" accountability_pending_count)")"
if [[ "$pending_ack" == "NONE" ]]; then
  pending_ack="$(
    awk -F'[:,]' '
      /"accountability_pending_count"/ {
        for (i = 1; i <= NF; i++) {
          if ($i ~ /accountability_pending_count/) {
            gsub(/[^0-9]/, "", $(i + 1))
            if ($(i + 1) != "") { print $(i + 1); exit }
          }
        }
      }
    ' "$DATA_ROOT/.learning_consumption/outcomes.json" 2>/dev/null || true
  )"
  pending_ack="$(status_count "$pending_ack")"
fi

status_command="opip-export-v2 sha=$OPIP_DEPLOYED_SHA sync_success_at=$last_sync_at capture_at=$capture_at capture_rc=$capture_rc outcomes_at=$outcomes_at outcomes_rc=$outcomes_rc capture_disposition=$capture_disposition outcomes_disposition=$outcomes_disposition release_compatibility=$release_compatibility outcomes_pending_ack=$pending_ack"

rm -rf "$INCOMING"/*
rm -f "$ARCHIVE"

ssh \
  -i "$OPIP_LEARNING_SSH_KEY" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=10 \
  "$OPIP_PRODUCTION_USER@$OPIP_PRODUCTION_HOST" \
  "$status_command" > "$ARCHIVE"

tar -xf "$ARCHIVE" -C "$INCOMING"

manifest="$INCOMING/manifest.env"
[[ -r "$manifest" ]] || {
  echo "O'Pip learning sync: missing manifest" >&2
  exit 66
}

manifest_value() {
  local key="$1"
  awk -F= -v k="$key" '$1 == k {print $2; exit}' "$manifest"
}

schema="$(manifest_value schema_version)"
# Migration audit only; this was the pre-retirement admission check:
# [[ "$schema" == "3" ]]
[[ "$schema" == "4" ]] || {
  echo "O'Pip learning sync: unsupported manifest schema=$schema" >&2
  exit 65
}
retired="$(manifest_value p1_shadow_outbox_retired)"
[[ "$retired" == "1" ]] || {
  echo "O'Pip learning sync: schema 4 requires retired P1 shadow outbox" >&2
  exit 65
}

production_sha="$(manifest_value production_deployed_sha)"
release_status="UNVERIFIED"
if [[ "$OPIP_DEPLOYED_SHA" =~ ^[0-9a-f]{40}$ && "$production_sha" =~ ^[0-9a-f]{40}$ ]]; then
  if [[ "$OPIP_DEPLOYED_SHA" == "$production_sha" ]]; then
    release_status="CURRENT"
  else
    release_status="RELEASE_DRIFT"
  fi
fi
printf 'release_compatibility_status=%s\nproduction_deployed_sha=%s\nworker_deployed_sha=%s\nrecorded_at_utc=%s\n' \
  "$release_status" \
  "${production_sha:-}" \
  "$OPIP_DEPLOYED_SHA" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$STATE_ROOT/release_compatibility.env.tmp"
mv -f -- "$STATE_ROOT/release_compatibility.env.tmp" "$STATE_ROOT/release_compatibility.env"
if [[ "$release_status" == "RELEASE_DRIFT" ]]; then
  echo "O'Pip learning sync: RELEASE_DRIFT worker=$OPIP_DEPLOYED_SHA production=$production_sha (sync allowed; compute blocked)" >&2
fi

# ---------------------------------------------------------------------------
# Canonical learning replica.
#
# Two distinct write boundaries are at play and must not be conflated:
#   * this sync script is the trusted administrative installer of replica
#     generations and the current pointer, so its helper container mounts the
#     store read-write;
#   * learning job containers are read-only consumers and never receive a
#     writable canonical mount.
# The replica is never promoted to runtime authority while releases differ.
# ---------------------------------------------------------------------------
tree_bytes() {
  find "$1" -type f -printf '%s\n' | awk '{total += $1} END {printf "%d\n", total}'
}
tree_sha256() {
  (
    cd "$1"
    find . -type f -print0 | sort -z | xargs -0 -r sha256sum
  ) | sha256sum | awk '{print $1}'
}

# Replica work is a one-shot container built on the same security posture as
# the learning jobs. The image is authoritative for the application code.
replica_helper() {
  local -a extra_mounts=()
  local -a extra_env=()
  while (( $# > 0 )); do
    case "$1" in
      --input-ro) extra_mounts+=(-v "$2:$2:ro"); shift 2 ;;
      --store-rw) extra_mounts+=(-v "$2:$2:rw"); extra_env+=(-e "OPIP_CANONICAL_REPLICA_STORE=$2"); shift 2 ;;
      *) break ;;
    esac
  done
  docker run --rm \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --memory 384m \
    --memory-swap 384m \
    --cpus 0.60 \
    --oom-score-adj 700 \
    --tmpfs /tmp:rw,noexec,nosuid,size=48m \
    -e PYTHONDONTWRITEBYTECODE=1 \
    "${extra_mounts[@]}" \
    "${extra_env[@]}" \
    "$OPIP_LEARNING_IMAGE" \
    python -m app.opip.learning.canonical_replica "$@"
}

validate_canonical_replica_outer() {
  local bundle="$INCOMING/$CANONICAL_REPLICA_INCOMING_NAME"
  [[ -d "$bundle" ]] || {
    echo "O'Pip learning sync: marker declares a canonical replica but the bundle is absent" >&2
    exit 66
  }
  local expected_bytes expected_sha actual_bytes actual_sha
  expected_bytes="$(manifest_value canonical_learning_replica_bytes)"
  expected_sha="$(manifest_value canonical_learning_replica_sha256)"
  [[ "$expected_bytes" =~ ^[0-9]+$ ]] || {
    echo "O'Pip learning sync: canonical replica bytes missing/invalid" >&2
    exit 65
  }
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || {
    echo "O'Pip learning sync: canonical replica sha256 missing/invalid" >&2
    exit 65
  }
  actual_bytes="$(tree_bytes "$bundle")"
  if [[ "$actual_bytes" != "$expected_bytes" ]]; then
    echo "O'Pip learning sync: canonical replica bytes mismatch ($actual_bytes != $expected_bytes)" >&2
    exit 65
  fi
  actual_sha="$(tree_sha256 "$bundle")"
  if [[ "$actual_sha" != "$expected_sha" ]]; then
    echo "O'Pip learning sync: canonical replica sha256 mismatch" >&2
    exit 65
  fi
}

# Inner provenance verification. The Python layer is authoritative for replica
# schema, generation identity, SQLite canonical validity, rollback-journal
# normalization, sidecar independence, snapshot facts, companion hashes,
# freshness and source release binding; shell reproduces none of it.
validate_canonical_replica_inner() {
  replica_helper \
    --input-ro "$INCOMING/$CANONICAL_REPLICA_INCOMING_NAME" \
    verify \
    --root "$INCOMING/$CANONICAL_REPLICA_INCOMING_NAME" \
    --release-sha "$production_sha"
}

# Install the validated generation: immutable generations/<id> plus an atomic
# current pointer, with bounded retention. A failure here leaves the previous
# current generation untouched.
install_canonical_replica() {
  replica_helper \
    --input-ro "$INCOMING/$CANONICAL_REPLICA_INCOMING_NAME" \
    --store-rw "$CANONICAL_REPLICA_ROOT" \
    install \
    --staging "$INCOMING/$CANONICAL_REPLICA_INCOMING_NAME" \
    --host-root "$CANONICAL_REPLICA_ROOT" \
    --release-sha "$production_sha"
}

CANONICAL_REPLICA_REQUIRED=0
REPLICA_MARKER="$(manifest_value canonical_learning_replica_version)"
case "$REPLICA_MARKER" in
  "1")
    # The export declares a canonical generation, so it is mandatory.
    CANONICAL_REPLICA_REQUIRED=1
    [[ "$production_sha" =~ ^[0-9a-f]{40}$ ]] || {
      echo "O'Pip learning sync: canonical replica requires a valid production_deployed_sha" >&2
      exit 65
    }
    ;;
  "")
    # The marker describes the incoming export, never the worker code version.
    # Worker generation is established from the deployed SHA and release
    # compatibility, so this is not a circular inference.
    if [[ "$release_status" == "CURRENT" ]]; then
      # A current bridge worker must not silently accept a production export
      # with no canonical replica contract: that would let readiness fall back
      # to a legacy path while claiming canonical authority is present.
      echo "O'Pip learning sync: CURRENT release but export declares no canonical replica" >&2
      exit 65
    fi
    echo "O'Pip learning sync: no canonical replica marker (release=$release_status); legacy sync only"
    ;;
  *)
    echo "O'Pip learning sync: unsupported canonical_learning_replica_version=$REPLICA_MARKER" >&2
    exit 65
    ;;
esac

validate_artifact() {
  local name="$1"
  local key="$2"
  local path="$INCOMING/$name"
  local expected_bytes expected_sha actual_bytes actual_sha

  [[ -f "$path" ]] || {
    echo "O'Pip learning sync: missing artifact=$name" >&2
    return 1
  }

  expected_bytes="$(manifest_value "${key}_bytes")"
  expected_sha="$(manifest_value "${key}_sha256")"
  actual_bytes="$(stat -c '%s' "$path")"
  actual_sha="$(sha256sum "$path" | awk '{print $1}')"

  [[ "$expected_bytes" =~ ^[0-9]+$ ]] || return 1
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ "$actual_bytes" == "$expected_bytes" ]] || {
    echo "O'Pip learning sync: size mismatch for $name" >&2
    return 1
  }
  [[ "$actual_sha" == "$expected_sha" ]] || {
    echo "O'Pip learning sync: checksum mismatch for $name" >&2
    return 1
  }
}

tree_bytes() {
  find "$1" -type f -printf '%s\n' | awk '{total += $1} END {printf "%d\n", total}'
}

tree_sha256() {
  (
    cd "$1"
    find . -type f -print0 | sort -z | xargs -0 -r sha256sum
  ) | sha256sum | awk '{print $1}'
}

validate_archive() {
  local name="$1"
  local key="$2"
  local path="$INCOMING/$name"
  local expected_bytes expected_sha actual_bytes actual_sha
  [[ -d "$path" ]] || {
    echo "O'Pip learning sync: missing archive directory=$name" >&2
    return 1
  }
  expected_bytes="$(manifest_value "${key}_bytes")"
  expected_sha="$(manifest_value "${key}_sha256")"
  actual_bytes="$(tree_bytes "$path")"
  actual_sha="$(tree_sha256 "$path")"
  [[ "$expected_bytes" =~ ^[0-9]+$ && "$actual_bytes" == "$expected_bytes" ]] || {
    echo "O'Pip learning sync: archive size mismatch for $name" >&2
    return 1
  }
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ && "$actual_sha" == "$expected_sha" ]] || {
    echo "O'Pip learning sync: archive checksum mismatch for $name" >&2
    return 1
  }
}

# Migration audit only; the retired legacy validation was:
# validate_artifact "p1_shadow_outbox.jsonl"
validate_artifact "full_market_observations.jsonl" "full_market_observations_jsonl"
validate_artifact "p1_evidence_ledger.jsonl" "p1_evidence_ledger_jsonl"
validate_artifact "intelligence_learning/events.jsonl" "intelligence_learning_events_jsonl"
validate_artifact "opip/qualification/screening_evaluations.jsonl" "opip_qualification_screening_evaluations_jsonl"
validate_artifact "opip/qualification/funnel_events.jsonl" "opip_qualification_funnel_events_jsonl"
validate_artifact "opip/qualification/scan_summaries.jsonl" "opip_qualification_scan_summaries_jsonl"
validate_artifact "paper_trading/events.jsonl" "paper_trading_events_jsonl"
validate_artifact "telegram_delivery_events.jsonl" "telegram_delivery_events_jsonl"
validate_artifact "decision_telemetry.jsonl" "decision_telemetry_jsonl"
validate_artifact "opip_trade_quality_evidence_v1.jsonl" "opip_trade_quality_evidence_v1_jsonl"
validate_artifact "candidate_trace.jsonl" "candidate_trace_jsonl"
validate_archive "opip/qualification/screening_evaluations_archive" "opip_qualification_screening_archive"
validate_archive "opip/qualification/funnel_events_archive" "opip_qualification_funnel_archive"
validate_archive "opip/qualification/scan_summaries_archive" "opip_qualification_summaries_archive"

# Canonical replica validation happens before ANY publication, so a defective
# bundle cannot leave the data root half-updated. Outer transport integrity and
# inner evidence provenance are deliberately separate checks: a bundle can be
# internally consistent at the tar layer and still be invalid evidence.
if (( CANONICAL_REPLICA_REQUIRED )); then
  validate_canonical_replica_outer
  validate_canonical_replica_inner
fi

rm -f -- \
  "$DATA_ROOT/p1_shadow_outbox.jsonl" \
  "$DATA_ROOT/p1_shadow_outbox_checkpoint.json" \
  "$DATA_ROOT/p1_shadow_outbox_dead_letter.jsonl"

for name in \
  full_market_observations.jsonl \
  p1_evidence_ledger.jsonl \
  intelligence_learning/events.jsonl \
  opip/qualification/screening_evaluations.jsonl \
  opip/qualification/funnel_events.jsonl \
  opip/qualification/scan_summaries.jsonl \
  paper_trading/events.jsonl \
  telegram_delivery_events.jsonl \
  decision_telemetry.jsonl \
  opip_trade_quality_evidence_v1.jsonl \
  candidate_trace.jsonl; do
  install -d -o root -g root -m 0755 "$(dirname "$DATA_ROOT/$name")"
  mv -f -- "$INCOMING/$name" "$DATA_ROOT/$name"
done
for name in \
  opip/qualification/screening_evaluations_archive \
  opip/qualification/funnel_events_archive \
  opip/qualification/scan_summaries_archive; do
  install -d -o root -g root -m 0755 "$(dirname "$DATA_ROOT/$name")"
  rm -rf -- "$DATA_ROOT/$name"
  mv -f -- "$INCOMING/$name" "$DATA_ROOT/$name"
done
mv -f -- "$INCOMING/manifest.env" "$DATA_ROOT/manifest.env"

# Canonical generation activation comes last, inside the plane lock. No learning
# consumer can run while this lock is held, so publishing the data manifest
# before activating the replica cannot expose an uncommitted generation. If
# activation fails, the previous current generation (or none) remains, and
# learning fails closed rather than running against a stale-but-claimed replica.
if (( CANONICAL_REPLICA_REQUIRED )); then
  install_canonical_replica
fi

rm -f "$ARCHIVE"
# Clear attempt-scoped incoming data so failed or repeated syncs cannot
# accumulate. Scoped to $INCOMING only; the replica store is never touched here.
rm -rf -- "${INCOMING:?}/"*
rm -f -- "${INCOMING:?}/".[!.]* 2>/dev/null || true

printf 'last_sync_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$DATA_ROOT/.last_sync"

echo "O'Pip learning evidence sync: OK"
