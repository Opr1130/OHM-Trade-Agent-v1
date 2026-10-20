#!/usr/bin/env bash
# HF-04: durable rollback for V2. Runs ON the target host as root.
#
#   --mode prepare   generate + verify the durable overlay, read the REAL epoch
#                    and sequence from the destination database, write a plan
#   --mode execute   freeze, quiesce, preserve, apply, verify
#
# Rollback kinds are NOT interchangeable (see --kind):
#   safe-baseline : release modes -> SAFE_BASELINE, same code and same data
#   code          : same data, previous CERTIFIED image only (immutable image id)
#   data          : refused here - canonical recovery must use the native
#                   restore path with a verified recovery receipt
set -Eeuo pipefail

SHA="c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
DROPLET_ID="610307364"
APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
LIVE_DB="$APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3"
DURABLE="/var/lib/opip-rollback"
PRESERVE="/var/lib/opip-cutover-evidence"
ETC="/etc/opip/v2"
# R3 correction: the certified commit does not ship this toolkit. Helpers are
# provisioned separately and integrity-verified; the release fallback only
# applies to a future release that carries them under APP_ROOT.
TOOLKIT_ROOT="/opt/opip-m07-toolkit"
TOOLS=""
LOG="/var/log/opip-rollback.log"

MODE=""; KIND="safe-baseline"; PREV_SHA=""; PREV_IMAGE=""

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG" >&2; }
die() { log "REFUSED: $*"; exit 3; }
run() { local d="$1"; shift; log "RUN: $d :: $*"; "$@" >>"$LOG" 2>&1 || { log "FAILED: $d"; exit 1; }; }

toolkit_manifest_verify() {
  local root="$1"
  local manifest="$root/TOOLKIT-MANIFEST.sha256"
  [[ -d "$root/scripts" && -s "$manifest" ]] || return 1
  local helper perms owner
  for helper in opip_receipts.py opip_rollback_guard.py opip_recovery.py opip_bundle.py \
                opip_archive.py opip_layout.py opip_provenance.py opip_signals.py; do
    [[ -f "$root/scripts/$helper" ]] || return 1
  done
  if ! perms="$(stat -c '%a' "$manifest" 2>/dev/null)"; then return 1; fi
  [[ "$perms" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$perms & 8#022) == 0 )) || return 1
  (( (8#$perms & 8#6000) == 0 )) || return 1
  if ! owner="$(stat -c '%U:%G' "$manifest" 2>/dev/null)"; then return 1; fi
  [[ "$owner" == "root:root" ]] || return 1
  if ! owner="$(stat -c '%U:%G' "$root/scripts" 2>/dev/null)"; then return 1; fi
  [[ "$owner" == "root:root" ]] || return 1
  ( cd "$root" && sha256sum -c --quiet TOOLKIT-MANIFEST.sha256 ) >/dev/null 2>&1 || return 1
}

resolve_toolkit() {
  if toolkit_manifest_verify "$TOOLKIT_ROOT"; then
    TOOLS="$TOOLKIT_ROOT/scripts"
    log "toolkit verified (separately provisioned): $TOOLKIT_ROOT"
    return 0
  fi
  if [[ -d "$APP_ROOT/deploy/m07/scripts" ]]; then
    TOOLS="$APP_ROOT/deploy/m07/scripts"
    log "toolkit resolved inside the installed release: $TOOLS"
    return 0
  fi
  return 1
}

# R3: identical Compose invocation to the certified controller.
compose() { ( cd "$APP_ROOT" && docker compose "$@" ); }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --kind) KIND="$2"; shift 2 ;;
    --previous-sha) PREV_SHA="$2"; shift 2 ;;
    --previous-image) PREV_IMAGE="$2"; shift 2 ;;
    --toolkit-root) TOOLKIT_ROOT="$2"; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "rollback must run as root (use sudo -n)"
[[ -n "$MODE" ]] || die "--mode prepare|execute is required"
[[ "$PREV_SHA" =~ ^[0-9a-f]{40}$ ]] || die "--previous-sha must be a 40-character hex commit"
[[ "$KIND" =~ ^(safe-baseline|code|data)$ ]] || die "--kind must be safe-baseline|code|data"
if [[ "$KIND" == "data" ]]; then
  die "data rollback is not performed here: use the native canonical restore with a verified recovery receipt"
fi

resolve_toolkit || die "M07 toolkit not found or failed integrity verification: $TOOLKIT_ROOT"

install -d -m 0700 "$DURABLE"; touch "$LOG"; chmod 0600 "$LOG"
PLAN_DIR="$DURABLE/$PREV_SHA"
PLAN="$PLAN_DIR/ROLLBACK_PLAN.json"
OVERLAY="$PLAN_DIR/safe-baseline.override.yml"

db_facts() {
  python3 - "$LIVE_DB" <<'PY'
import json, sqlite3, sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    epoch, seq = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1").fetchone()
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    conn.close()
    print(json.dumps({"readable": True, "history_epoch": int(epoch), "next_local_sequence": int(seq),
                      "event_count": int(events), "integrity_check": integrity,
                      "foreign_key_violations": fk}))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"readable": False, "error": f"{type(exc).__name__}: {exc}"}))
PY
}

write_overlay() {
  install -d -m 0750 "$PLAN_DIR"
  umask 027
  cat >"$OVERLAY" <<PROFILE
services:
  ohm-trade-agent:
    environment:
      OPIP_RELEASE_PROFILE: "SAFE_BASELINE"
      OPIP_FEATURE_BUS_MODE: "off"
      OPIP_CANONICAL_WRITER_MODE: "off"
      OPIP_TARGET_SPINE_MODE: "off"
      OPIP_PAPER_V2_MODE: "off"
      OPIP_COMMITTEE_MODE: "off"
      OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD: "0.0"
PROFILE
  chmod 0640 "$OVERLAY"
  chown root:root "$OVERLAY"
  local hash perms
  hash="$(sha256sum "$OVERLAY" | awk '{print $1}')"
  perms="$(stat -c '%a %U:%G' "$OVERLAY")"
  [[ "$perms" == "640 root:root" ]] || die "overlay permissions are $perms, expected 640 root:root"
  OVERLAY_SHA="$hash"
}
mode_prepare() {
  write_overlay
  local facts
  facts="$(db_facts)"
  if [[ "$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["readable"])' "$facts")" != "True" ]]; then
    die "destination database is not readable: $facts"
  fi
  python3 - "$PLAN" "$PREV_SHA" "$KIND" "$OVERLAY" "$OVERLAY_SHA" "$facts" "$DROPLET_ID" "$LIVE_DB" <<'PY'
import json, os, sys
from datetime import datetime, timezone
plan_path, prev, kind, overlay, overlay_sha, facts, droplet, live_db = sys.argv[1:9]
facts = json.loads(facts)
json.dump({
    "receipt": "ROLLBACK_PREPARED", "receipt_schema": 1,
    "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
    "droplet_id": droplet, "kind": kind, "previous_sha": prev,
    "durable_overlay": overlay, "overlay_sha256": overlay_sha,
    "overlay_mode": oct(os.stat(overlay).st_mode & 0o777),
    "destination_db": os.path.realpath(live_db),
    "live_at_prepare": facts,
    "depends_on_transient_controller_override": False,
}, open(plan_path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  chmod 0600 "$PLAN"
  log "ROLLBACK_PREPARED: $PLAN"
  python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(json.dumps({"plan":sys.argv[1],"overlay":d["durable_overlay"],"overlay_sha256":d["overlay_sha256"],"epoch":d["live_at_prepare"]["history_epoch"],"sequence":d["live_at_prepare"]["next_local_sequence"],"kind":d["kind"]}))' "$PLAN"
}

mode_execute() {
  [[ -s "$PLAN" ]] || die "no ROLLBACK_PREPARED plan for $PREV_SHA; run --mode prepare first"
  [[ -s "$OVERLAY" ]] || die "durable overlay missing: $OVERLAY"
  local now_sha stored_sha perms
  now_sha="$(sha256sum "$OVERLAY" | awk '{print $1}')"
  stored_sha="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["overlay_sha256"])' "$PLAN")"
  [[ "$now_sha" == "$stored_sha" ]] || die "overlay hash drifted: $now_sha != $stored_sha"
  perms="$(stat -c '%a %U:%G' "$OVERLAY")"
  [[ "$perms" == "640 root:root" ]] || die "overlay permissions drifted: $perms"

  local stamp preserve
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  preserve="$PRESERVE/$stamp"
  run "freeze scheduler" bash -c "crontab -l 2>/dev/null | grep -v 'app.jobs.run_cycle' | crontab -"
  run "remove cycle cron" rm -f /etc/cron.d/ohm-unified-cycle
  run "preserve dir" install -d -m 0700 "$preserve"
  run "quiesce writer" compose stop opip-canonical-writer
  local still_running
  still_running="$(compose ps --status running --services | grep -cx 'opip-canonical-writer' || true)"
  [[ "$still_running" == "0" ]] || die "canonical writer did not quiesce; refusing a hot snapshot"
  # D03: a raw `cp` of a WAL-mode SQLite directory is not a consistent snapshot.
  # Use the SQLite backup API, then verify integrity/FK and hash the produced file.
  local snap
  if ! snap="$(python3 "$TOOLS/opip_rollback_guard.py" snapshot \
      --db "$LIVE_DB" --out "$preserve/opip_canonical_v1.sqlite3")"; then
    log "$snap"
    die "WAL-safe canonical snapshot failed verification; refusing rollback"
  fi
  log "canonical snapshot verified: $preserve ($snap)"
  python3 - "$preserve/snapshot-facts.json" "$snap" <<'PY'
import json, sys
json.dump(json.loads(sys.argv[2]), open(sys.argv[1], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  [[ -s "$preserve/opip_canonical_v1.sqlite3" ]] || die "snapshot file missing after a reported success"

  # AC-003: the authoritative comparison runs AFTER the writer is quiesced and
  # proved stopped and AFTER the WAL-consistent snapshot is preserved. A
  # pre-quiescence comparison cannot close the race and is not used as the gate.
  local facts guard
  facts="$(db_facts)"
  [[ "$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["readable"])' "$facts")" == "True" ]] \
    || die "destination database unreadable after quiescence; refusing rollback"
  # D07: epoch alone is not enough - sequence and event count must not advance either.
  if ! guard="$(python3 "$TOOLS/opip_rollback_guard.py" compare --plan "$PLAN" --db "$LIVE_DB")"; then
    log "$guard"
    die "live canonical history advanced since prepare; refusing rollback"
  fi
  log "history guard PASS: $guard"

  if [[ "$KIND" == "code" ]]; then
    [[ -n "$PREV_IMAGE" ]] || die "code rollback requires --previous-image (immutable image id)"
    docker image inspect -f '{{.Id}}' "$PREV_IMAGE" >/dev/null 2>&1 || die "previous image not present locally: $PREV_IMAGE"
    local label
    label="$(docker image inspect -f '{{json .Config.Labels}}' "$PREV_IMAGE")"
    [[ "$label" == *"$PREV_SHA"* ]] || die "previous image label does not carry $PREV_SHA"
    cat >"$PLAN_DIR/code.override.yml" <<YAML
services:
  ohm-trade-agent:
    image: $PREV_IMAGE
  opip-canonical-writer:
    image: $PREV_IMAGE
YAML
    run "stop services" compose stop ohm-trade-agent ohm-dashboard-edge opip-stream-worker
    run "code rollback up" compose -f docker-compose.yml -f "$PLAN_DIR/code.override.yml" \
      up -d --remove-orphans ohm-trade-agent opip-canonical-writer
  else
    run "stop services" compose stop ohm-trade-agent ohm-dashboard-edge opip-stream-worker
    run "safe-baseline up" compose -f docker-compose.yml -f "$OVERLAY" \
      up -d --remove-orphans ohm-trade-agent opip-canonical-writer ohm-dashboard-edge
  fi

  local healthy=0
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health | grep -q '"status":"ok"'; then healthy=1; break; fi
    sleep 2
  done
  [[ "$healthy" == "1" ]] || die "core health failed after rollback"

  local writers crons pollers
  writers="$(docker ps --format '{{.Names}}' | grep -c '^opip-canonical-writer$' || true)"
  crons="$(crontab -l 2>/dev/null | grep -c 'app.jobs.run_cycle' || true)"
  pollers="$(docker logs --tail=200 ohm-trade-agent 2>&1 | grep -c 'Telegram callback/command listener started' || true)"
  [[ "$writers" == "1" ]] || die "writer uniqueness violated after rollback: $writers"
  [[ "$crons" -le 1 ]] || die "scheduler uniqueness violated after rollback: $crons"
  [[ "$pollers" -le 1 ]] || die "telegram consumer uniqueness violated after rollback: $pollers"

  python3 - "$PLAN_DIR/ROLLBACK_EXECUTED.json" "$PREV_SHA" "$KIND" "$preserve" "$facts" "$writers" "$crons" <<'PY'
import json, sys
from datetime import datetime, timezone
path, prev, kind, preserve, facts, writers, crons = sys.argv[1:8]
json.dump({"receipt": "ROLLBACK_EXECUTED", "receipt_schema": 1,
           "executed_at_utc": datetime.now(timezone.utc).isoformat(),
           "previous_sha": prev, "kind": kind, "preserved_canonical": preserve,
           "live_after": json.loads(facts), "writer_containers": int(writers),
           "cycle_cron_lines": int(crons), "scheduler_reactivated": False},
          open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  chmod 0600 "$PLAN_DIR/ROLLBACK_EXECUTED.json"
  log "ROLLBACK_EXECUTED: $PLAN_DIR/ROLLBACK_EXECUTED.json (scheduler left frozen)"
}

case "$MODE" in
  prepare) mode_prepare ;;
  execute) mode_execute ;;
  *) die "unknown mode $MODE" ;;
esac


