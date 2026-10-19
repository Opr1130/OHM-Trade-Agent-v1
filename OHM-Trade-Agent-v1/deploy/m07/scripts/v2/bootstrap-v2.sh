#!/usr/bin/env bash
# HF-03 / R2: guarded first-install bootstrap for V2. Runs ON the V2 host as root.
#
# Modes (each writes its own receipt):
#   --mode prepare             read-only: identity, release, image, storage, preflight
#   --mode authorize           owner gate: confirm sha+host, acknowledge an expected block
#   --mode execute             mutate: install verified release, dirs, config, core only
#   --mode activate-writer     start EXACTLY ONE writer after a verified recovery receipt
#   --mode activate-scheduler  reconcile the scheduler only after live acceptance
#
# Single host-layout contract (proven from the certified controller and Compose):
#   REPO_ROOT=/opt/OHM-Trade-Agent-v1   APP_ROOT=$REPO_ROOT/OHM-Trade-Agent-v1
#   Compose, .env, data and the canonical DB live ONLY under APP_ROOT.
#   /srv/opip-v2/releases/<sha> is an immutable INSTALL SOURCE, never a runtime root.
#
# Hard rules:
#   * no interactive clone; the release must already be on disk and hash-verified
#   * no `|| true` around required setup; no partial overwrite of a foreign runtime root
#   * tracked nginx config is never modified; our config is bind-mounted
#   * no Freqtrade paper stack, no P1 retirement
#   * the canonical writer is NEVER started by `execute` (it starts only after recovery)
set -Eeuo pipefail

SHA="c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
DROPLET_ID="610307364"
IMAGE_TAG="opip-v2:certified-c196dd67"
RELEASES_ROOT="/srv/opip-v2/releases"
RELEASE_ROOT="$RELEASES_ROOT/$SHA"
# R3 correction: the certified commit does not contain this toolkit. Helpers are
# provisioned separately, integrity-verified here, and never copied into the
# certified application source.
TOOLKIT_ROOT="/opt/opip-m07-toolkit"
TOOLKIT_MANIFEST="$TOOLKIT_ROOT/TOOLKIT-MANIFEST.sha256"
PROVISION_FROM=""
SRC=""                      # resolved by resolve_src (never guessed)
REPO_ROOT="/opt/OHM-Trade-Agent-v1"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
ENV_FILE="$APP_ROOT/.env"
CANONICAL_DIR="$APP_ROOT/data/opip/canonical"
LIVE_DB="$CANONICAL_DIR/opip_canonical_v1.sqlite3"
ETC="/etc/opip/v2"
STATE="/var/lib/opip-bootstrap"
RECOVERY_RECEIPT="/var/lib/opip-migration/canonical-recovery-receipt.json"
PREPARED="$STATE/BOOTSTRAP_PREPARED.json"
AUTHORIZED="$STATE/BOOTSTRAP_AUTHORIZED.json"
EXECUTED="$STATE/BOOTSTRAP_EXECUTED.json"
WRITER_RECEIPT="$STATE/WRITER_ACTIVATED.json"
SCHEDULER_RECEIPT="$STATE/SCHEDULER_ACTIVATED.json"
LIVE_ACCEPTANCE="$STATE/LIVE-ACCEPTANCE.json"
MANIFEST="$STATE/release-manifest.sha256"
TOOLS=""
# Filled only for privileged host modes; check/provision and early argument
# errors must never write to the host log path.
LOG=""

MODE=""
CONFIRM_SHA=""
CONFIRM_HOST=""
ACK_FRESH="0"
OWNER="owner"

log() {
  local line
  line="$(printf '%s %s' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*")"
  if [[ -n "$LOG" ]]; then
    printf '%s\n' "$line" | tee -a "$LOG" >&2
  else
    printf '%s\n' "$line" >&2
  fi
}
die() { log "REFUSED: $*"; exit 3; }
run() {
  local desc="$1"; shift
  log "RUN: $desc :: $*"
  if ! "$@" >>"$LOG" 2>&1; then
    log "FAILED: $desc"
    exit 1
  fi
}

# --- separately provisioned toolkit (R3 integration correction) --------------
# The certified commit c196dd67 does not contain deploy/m07. Helpers are
# provisioned out of band into TOOLKIT_ROOT with a SHA-256 manifest, and every
# mode verifies that manifest before using them. The certified release is still
# verified against its own manifest in verify_source(), so this adds an
# integrity control and weakens no SHA verification.
toolkit_required_helpers() {
  for helper in opip_receipts.py opip_rollback_guard.py opip_recovery.py opip_bundle.py \
                opip_archive.py opip_layout.py opip_provenance.py opip_signals.py; do
    printf '%s\n' "$helper"
  done
}

toolkit_manifest_verify() {
  local root="$1"
  local manifest="$root/TOOLKIT-MANIFEST.sha256"
  [[ -d "$root/scripts" && -s "$manifest" ]] || return 1
  local helper perms owner
  while IFS= read -r helper; do
    [[ -f "$root/scripts/$helper" ]] || return 1
  done < <(toolkit_required_helpers)
  if ! perms="$(stat -c '%a' "$manifest" 2>/dev/null)"; then return 1; fi
  [[ "$perms" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$perms & 8#022) == 0 )) || return 1   # never group/world writable
  (( (8#$perms & 8#6000) == 0 )) || return 1  # never setuid/setgid
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    if ! owner="$(stat -c '%U:%G' "$manifest" 2>/dev/null)"; then return 1; fi
    [[ "$owner" == "root:root" ]] || return 1
    if ! owner="$(stat -c '%U:%G' "$root/scripts" 2>/dev/null)"; then return 1; fi
    [[ "$owner" == "root:root" ]] || return 1
  fi
  ( cd "$root" && sha256sum -c --quiet TOOLKIT-MANIFEST.sha256 ) >/dev/null 2>&1 || return 1
}

resolve_toolkit() {
  if toolkit_manifest_verify "$TOOLKIT_ROOT"; then
    TOOLS="$TOOLKIT_ROOT/scripts"
    log "toolkit verified (separately provisioned): $TOOLKIT_ROOT"
    return 0
  fi
  # A future release that ships the toolkit is already covered by the release
  # manifest verified in verify_source(); no second manifest is needed there.
  if [[ -n "${SRC:-}" && -d "$SRC/deploy/m07/scripts" ]]; then
    TOOLS="$SRC/deploy/m07/scripts"
    log "toolkit resolved inside the verified release: $TOOLS"
    return 0
  fi
  return 1
}

toolkit_manifest_build() {
  local root="$1"
  ( cd "$root" \
    && find . -type f ! -name 'TOOLKIT-MANIFEST.sha256' -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum ) >"$root/TOOLKIT-MANIFEST.sha256"
  chmod 0644 "$root/TOOLKIT-MANIFEST.sha256"
}

mode_provision_toolkit() {
  [[ -n "$PROVISION_FROM" ]] || die "--from <verified toolkit directory> is required"
  [[ -d "$PROVISION_FROM/scripts" ]] || die "toolkit source has no scripts/: $PROVISION_FROM"
  [[ -f "$PROVISION_FROM/scripts/v2/bootstrap-v2.sh" ]] \
    || die "toolkit source has no scripts/v2/bootstrap-v2.sh"
  if [[ -n "$(find "$PROVISION_FROM" -type l -print -quit 2>/dev/null)" ]]; then
    die "toolkit source contains symlinks; refusing to provision"
  fi
  install -d -m 0755 "$TOOLKIT_ROOT"
  local staging="$TOOLKIT_ROOT/.staging.$$"
  local owner_opt=()
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then owner_opt=(--chown=root:root); fi
  rm -rf "$staging"
  install -d -m 0755 "$staging"
  rsync -a --delete "${owner_opt[@]}" --exclude '__pycache__' --exclude 'tests' --exclude 'evidence' \
    --exclude 'TOOLKIT-MANIFEST.sha256' "$PROVISION_FROM/" "$staging/"
  toolkit_manifest_build "$staging"
  rsync -a --delete --exclude '.staging.*' "$staging/" "$TOOLKIT_ROOT/"
  rm -rf "$staging"
  find "$TOOLKIT_ROOT" -type d -exec chmod 0755 {} +
  find "$TOOLKIT_ROOT" -type f -exec chmod 0644 {} +
  find "$TOOLKIT_ROOT" -type f -name '*.sh' -exec chmod 0755 {} +
  toolkit_manifest_verify "$TOOLKIT_ROOT" || die "provisioned toolkit failed integrity verification"
  log "toolkit provisioned and verified: $TOOLKIT_ROOT"
}

mode_check_toolkit() {
  resolve_src
  resolve_toolkit \
    || die "M07 toolkit not found or failed integrity verification: $TOOLKIT_ROOT"
  printf 'TOOLKIT=%s\n' "$TOOLS"
  if [[ "$TOOLS" == "$TOOLKIT_ROOT/scripts" ]]; then
    printf 'TOOLKIT_SOURCE=separate\n'
  else
    printf 'TOOLKIT_SOURCE=release\n'
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --confirm-sha) CONFIRM_SHA="$2"; shift 2 ;;
    --confirm-host) CONFIRM_HOST="$2"; shift 2 ;;
    --acknowledge-fresh-host) ACK_FRESH="1"; shift ;;
    --toolkit-root) TOOLKIT_ROOT="$2"; TOOLKIT_MANIFEST="$TOOLKIT_ROOT/TOOLKIT-MANIFEST.sha256"; shift 2 ;;
    --to) TOOLKIT_ROOT="$2"; TOOLKIT_MANIFEST="$TOOLKIT_ROOT/TOOLKIT-MANIFEST.sha256"; shift 2 ;;
    --release-root) RELEASE_ROOT="$2"; shift 2 ;;
    --from) PROVISION_FROM="$2"; shift 2 ;;
    --activate-scheduler) shift ;;   # legacy flag: scheduler needs its own mode now
    *) die "unknown argument $1" ;;
  esac
done

[[ -n "$MODE" ]] || die "--mode prepare|authorize|execute|activate-writer|activate-scheduler|provision-toolkit|check-toolkit is required"
case "$MODE" in
  check-toolkit|provision-toolkit)
    LOG=""   # read-only / provisioning modes must work without a host state dir
    ;;
  *)
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "bootstrap must run as root (use sudo -n)"
    LOG="/var/log/opip-bootstrap.log"
    install -d -m 0700 "$STATE"
    touch "$LOG"; chmod 0600 "$LOG"
    ;;
esac

metadata_id() { curl -fsS --max-time 5 http://169.254.169.254/metadata/v1/id; }

verify_identity() {
  local observed
  observed="$(metadata_id)" || die "cannot read droplet metadata"
  [[ "$observed" == "$DROPLET_ID" ]] || die "droplet identity mismatch: $observed != $DROPLET_ID"
  log "identity verified: $observed"
}

manifest_stream() {
  local root="$1"
  ( cd "$root" \
    && find . -path ./.git -prune -o -path ./data -prune -o -name .env -prune \
         -o -name __pycache__ -prune -o -type f -print \
    | LC_ALL=C sort \
    | while IFS= read -r f; do printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "${f#./}"; done )
}

manifest_build() { manifest_stream "$1" >"$2"; chmod 0600 "$2"; }
manifest_verify() { diff -q <(manifest_stream "$1") "$2" >/dev/null 2>&1; }

resolve_src() {
  # Accept exactly two layouts and refuse anything else (no guessing, no second checkout).
  if [[ -f "$RELEASE_ROOT/docker-compose.yml" ]]; then
    SRC="$RELEASE_ROOT"
  elif [[ -f "$RELEASE_ROOT/OHM-Trade-Agent-v1/docker-compose.yml" ]]; then
    SRC="$RELEASE_ROOT/OHM-Trade-Agent-v1"
  else
    die "certified release not found under $RELEASE_ROOT (expected docker-compose.yml at the root or under OHM-Trade-Agent-v1/)"
  fi
  TOOLS="$SRC/deploy/m07/scripts"
}

verify_source() {
  local check_manifest="${1:-1}"
  resolve_src
  [[ -d "$SRC" ]] || die "certified source release missing: $SRC (bootstrap never clones a private repo)"
  [[ -f "$SRC/docker-compose.yml" ]] || die "compose file missing in $SRC"
  [[ -d "$SRC/app" ]] || die "application package missing in $SRC/app"
  if [[ -d "$SRC/.git" ]]; then
    local head
    head="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
    [[ "$head" == "$SHA" ]] || die "source release HEAD is '$head', not $SHA"
  fi
  if [[ "$check_manifest" == "1" && -s "$MANIFEST" ]]; then
    manifest_verify "$SRC" "$MANIFEST" || die "release source drifted from the prepared manifest"
  fi
  log "source release verified: $SRC"
}

verify_image() {
  IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$IMAGE_TAG" 2>/dev/null || true)"
  [[ -n "$IMAGE_ID" ]] || die "certified image $IMAGE_TAG is absent (build it from $SRC, then re-run prepare)"
  local labels
  labels="$(docker image inspect -f '{{json .Config.Labels}}' "$IMAGE_TAG")"
  [[ "$labels" == *"$SHA"* ]] || die "image $IMAGE_TAG label does not carry $SHA"
  log "certified image verified: $IMAGE_ID"
}

install_release() {
  if [[ -L "$APP_ROOT" ]]; then
    die "runtime root is a symlink; refusing (single-root contract)"
  fi
  if [[ -d "$APP_ROOT/.git" ]]; then
    local head
    head="$(git -C "$APP_ROOT" rev-parse HEAD 2>/dev/null || true)"
    [[ "$head" == "$SHA" ]] || die "runtime checkout HEAD is '$head', not $SHA"
    manifest_verify "$APP_ROOT" "$MANIFEST" || die "runtime checkout drifted from the prepared manifest"
    log "runtime checkout already installed and verified: $APP_ROOT"
    return 0
  fi
  if [[ -d "$APP_ROOT" ]] && [[ -n "$(find "$APP_ROOT" -mindepth 1 -maxdepth 1 ! -name data -print -quit 2>/dev/null)" ]]; then
    manifest_verify "$APP_ROOT" "$MANIFEST" \
      || die "runtime root exists with non-certified content; refusing to overwrite $APP_ROOT"
    log "runtime root already installed and verified: $APP_ROOT"
    return 0
  fi
  install -d -m 0755 "$APP_ROOT"
  run "install release" rsync -a --delete --exclude '.git' --exclude 'data' --exclude '.env' \
    --exclude '__pycache__' "$SRC/" "$APP_ROOT/"
  manifest_verify "$APP_ROOT" "$MANIFEST" || die "installed runtime root does not match the release manifest"
  log "release installed into runtime root: $APP_ROOT"
}

compose() {
  # R3: identical invocation to the certified controller (`cd "$APP_ROOT" &&
  # docker compose ...`). No `-f` overlay is layered on the core stack: the
  # certified docker-compose.yml is the single source of truth, it already
  # declares all four services, mounts the tracked nginx config and reads .env.
  ( cd "$APP_ROOT" && docker compose "$@" )
}

mode_execute() {
  verify_identity
  verify_source
  verify_image
  resolve_toolkit || die "M07 toolkit not found or failed integrity verification: $TOOLKIT_ROOT"
  [[ -s "$AUTHORIZED" ]] || die "no BOOTSTRAP_AUTHORIZED receipt; owner authorisation is required"
  local cfg
  cfg="$(config_digest_value)"
  python3 - "$TOOLS/opip_receipts.py" "$PREPARED" "$AUTHORIZED" "$DROPLET_ID" "$SHA" "$IMAGE_ID" "$cfg" <<'PY' || exit 3
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("opip_receipts", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
_, prepared, authorized, host, sha, image, cfg = sys.argv[1:8]
observed = {"host": host, "certified_sha": sha, "image_id": image, "config_digest": cfg}
gate = mod.verify_for_execute(prepared_path=prepared, authorized_path=authorized, observed=observed)
print(json.dumps(gate, indent=2))
raise SystemExit(0 if gate["verdict"] == "PASS" else 3)
PY

  install_release
  install -d -m 0755 -o root -g root "$APP_ROOT/data" "$CANONICAL_DIR" "$ETC"
  [[ -s "$ENV_FILE" ]] || die ".env is missing at $ENV_FILE (populate it before execute)"
  [[ -s "$APP_ROOT/deploy/nginx/dashboard-sidecar.conf" ]] \
    || die "tracked nginx config missing in the release; refusing to synthesise one"
  [[ -s "$APP_ROOT/deploy/remote/reconcile-scheduler.sh" ]] \
    || die "certified scheduler reconciler missing; refusing to proceed"

  # R3: the tracked nginx config is the certified one; nothing is generated over it.
  [[ "$(config_digest_value)" == "$cfg" ]] || die "configuration changed after authorisation validation"
  run "compose render" compose config --quiet
  local services
  services="$(compose config --services)"
  if grep -q 'ohm-freqtrade-paper' <<<"$services"; then
    die "paper services selected by the V2 project: $services"
  fi
  if grep -qx 'opip-canonical-writer' <<<"$services"; then
    log "writer service present in the project (must stay stopped until activate-writer)"
  fi

  run "start core" compose up -d --no-deps ohm-trade-agent
  local healthy=0
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health | grep -q '"status":"ok"'; then healthy=1; break; fi
    sleep 2
  done
  [[ "$healthy" == "1" ]] || die "core health check failed after start"
  run "release gate" compose run --rm --no-deps ohm-trade-agent \
    python -m app.services.release_profiles --profile EVIDENCE_SHADOW

  if compose ps --status running --services | grep -qx 'opip-canonical-writer'; then
    die "canonical writer is running before canonical recovery; refusing to continue"
  fi

  python3 - "$EXECUTED" "$DROPLET_ID" "$SHA" "$IMAGE_ID" "$cfg" <<'PY'
import json, sys
from datetime import datetime, timezone
path, host, sha, image, cfg = sys.argv[1:6]
json.dump({"receipt": "BOOTSTRAP_EXECUTED", "receipt_schema": 2,
           "executed_at_utc": datetime.now(timezone.utc).isoformat(),
           "host": host, "certified_sha": sha, "image_id": image, "config_digest": cfg,
           "writer_started": False, "scheduler_started": False,
           "next_required_gate": "canonical recovery, then --mode activate-writer",
           "paper_stack": "ABSENT", "p1_retirement": "NOT_RUN"},
          open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  chmod 0600 "$EXECUTED"
  python3 - "$TOOLS/opip_receipts.py" "$AUTHORIZED" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("opip_receipts", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.consume(sys.argv[2])
PY
  log "EXECUTED receipt written: $EXECUTED (writer and scheduler intentionally NOT started)"
}

mode_prepare() {
  verify_identity
  verify_source 0
  verify_image
  resolve_toolkit || die "M07 toolkit not found or failed integrity verification: $TOOLKIT_ROOT"
  [[ -s "$ENV_FILE" ]] || die ".env is missing at $ENV_FILE (canonical location, owner-provided)"
  install -d -m 0755 -o root -g root "$ETC" "$APP_ROOT" "$APP_ROOT/data"
  local df_kb
  df_kb="$(df -Pk / | tail -1 | awk '{print $4}')"
  [[ "$df_kb" -ge 524288 ]] || die "insufficient free space: ${df_kb}KiB"
  log "storage check: ${df_kb}KiB free on /"
  # Record the exact release content now; execute/authorize re-verify against it.
  manifest_build "$SRC" "$MANIFEST"
  local evaluated ackable="false"
  evaluated="$(preflight_evaluate)"
  if [[ "$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["fresh_host_only"])' "$evaluated")" == "True" ]]; then
    ackable="true"
  fi
  write_prepared "$evaluated" "$ackable"
  log "PREPARED receipt written: $PREPARED"
  echo "$evaluated"
}

mode_authorize() {
  verify_identity
  verify_source
  verify_image
  resolve_toolkit || die "M07 toolkit not found or failed integrity verification: $TOOLKIT_ROOT"
  [[ -s "$PREPARED" ]] || die "no BOOTSTRAP_PREPARED receipt; run --mode prepare first"
  [[ "$CONFIRM_SHA" == "$SHA" ]] || die "--confirm-sha must equal $SHA"
  [[ "$CONFIRM_HOST" == "$DROPLET_ID" ]] || die "--confirm-host must equal $DROPLET_ID"
  local cfg
  cfg="$(config_digest_value)"
  python3 - "$TOOLS/opip_receipts.py" "$PREPARED" "$AUTHORIZED" "$CONFIRM_SHA" "$CONFIRM_HOST" \
    "$ACK_FRESH" "$OWNER" "$cfg" "$IMAGE_ID" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("opip_receipts", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
_, prepared, out, sha, host, ack, owner, cfg, image = sys.argv[1:10]
if cfg and cfg != mod.read(prepared).get("config_digest"):
    raise SystemExit("refusing authorization: configuration digest changed since prepare")
mod.bind_authorization(prepared_path=prepared, path=out, confirm_sha=sha, confirm_host=host,
                       acknowledge_fresh_host=(ack == "1"), owner=owner)
PY
  chmod 0600 "$AUTHORIZED"
  log "AUTHORIZED receipt written: $AUTHORIZED (bound to prepared digest, 60-minute expiry)"
}

preflight_evaluate() {
  local receipt="$STATE/protection-preflight.json" pf_rc=0
  [[ -s "$ENV_FILE" ]] || die ".env is missing at $ENV_FILE (canonical location, owner-provided)"
  # Read-only preflight against the immutable release code; the environment file is
  # ALWAYS the canonical one under APP_ROOT (never a second .env inside the release).
  set +e
  local mounts=(-v "$SRC/app:/app/app:ro")
  if [[ -d "$SRC/data" ]]; then
    mounts+=(-v "$SRC/data:/app/data:ro")
  fi
  docker run --rm --read-only --tmpfs /tmp:rw,size=32m --pids-limit 64 --memory 384m \
    --env-file "$ENV_FILE" -e PYTHONDONTWRITEBYTECODE=1 \
    "${mounts[@]}" "$IMAGE_ID" \
    python -m app.jobs.preflight_protection_health \
      --candidate-sha "$SHA" --release-profile EVIDENCE_SHADOW >"$receipt" 2>>"$LOG"
  pf_rc=$?
  set -e
  [[ -s "$receipt" ]] || die "protection preflight produced no receipt (rc=$pf_rc)"
  log "protection preflight exit=$pf_rc (content evaluated below)"
  python3 - "$receipt" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
verdict = doc.get("verdict") or {}
blocking = [c for c in (verdict.get("blocking_incidents") or []) if c]
reasons = [str(r) for r in (verdict.get("reason_codes") or [])]
no_incumbent = all(r in {"UNAVAILABLE", "NO_STATE", "NOT_DEPLOYED", "MISSING"} for r in reasons) if reasons else False
print(json.dumps({"ready": bool(verdict.get("ready")), "reasons": reasons,
                  "blocking_incidents": blocking, "fresh_host_only": no_incumbent}))
PY
}

config_digest_value() {
  # Before installation the release copy is authoritative; content is identical by
  # construction (the install is manifest-verified), so the digest is stable.
  local base="$APP_ROOT/docker-compose.yml"
  if [[ ! -f "$base" ]]; then
    base="$SRC/docker-compose.yml"
  fi
  python3 - "$base" "$ENV_FILE" <<'PY'
import hashlib, os, sys
digest = hashlib.sha256()
for path in sys.argv[1:]:
    digest.update(os.path.basename(path).encode() + b"\0")
    if os.path.isfile(path):
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    else:
        digest.update(b"MISSING")
    digest.update(b"\0")
print(digest.hexdigest())
PY
}

write_prepared() {
  local evaluated="$1" ackable="$2" cfg
  cfg="$(config_digest_value)"
  python3 - "$TOOLS/opip_receipts.py" "$PREPARED" "$DROPLET_ID" "$SHA" "$IMAGE_ID" \
    "$cfg" "$(sha256sum "$STATE/protection-preflight.json" | cut -d' ' -f1)" "$evaluated" "$ackable" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("opip_receipts", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
path, host, sha, image, cfg, pf_digest, evaluated, ackable = sys.argv[2:10]
preflight = json.loads(evaluated)
preflight["fresh_host_only"] = ackable == "true"
mod.build_prepared(path=path, host=host, sha=sha, image_id=image, config_digest=cfg,
                   preflight_digest=pf_digest, preflight=preflight)
PY
  chmod 0600 "$PREPARED"
}

mode_activate_writer() {
  verify_identity
  verify_source
  verify_image
  resolve_toolkit || die "M07 toolkit not found or failed integrity verification: $TOOLKIT_ROOT"
  [[ -s "$EXECUTED" ]] || die "no BOOTSTRAP_EXECUTED receipt; run --mode execute first"
  if compose ps --status running --services | grep -qx 'opip-canonical-writer'; then
    die "a canonical writer is already running; refusing to start a second writer"
  fi
  [[ -f "$RECOVERY_RECEIPT" ]] \
    || die "no canonical recovery receipt at $RECOVERY_RECEIPT; the writer stays OFF"
  local gate
  if ! gate="$(python3 "$TOOLS/opip_rollback_guard.py" writer-gate \
      --receipt "$RECOVERY_RECEIPT" --db "$LIVE_DB")"; then
    log "$gate"
    die "recovery receipt does not authorise a canonical writer for $LIVE_DB"
  fi
  log "writer gate PASS: $gate"
  run "start writer" compose up -d --no-deps opip-canonical-writer
  local running
  running="$(compose ps --status running --services | grep -cx 'opip-canonical-writer' || true)"
  [[ "$running" == "1" ]] || die "expected exactly one running canonical writer, observed $running"
  python3 - "$WRITER_RECEIPT" "$DROPLET_ID" "$SHA" "$RECOVERY_RECEIPT" "$(sha256sum "$RECOVERY_RECEIPT" | cut -d' ' -f1)" <<'PY'
import json, sys
from datetime import datetime, timezone
path, host, sha, receipt, digest = sys.argv[1:6]
json.dump({"receipt": "WRITER_ACTIVATED", "receipt_schema": 2,
           "activated_at_utc": datetime.now(timezone.utc).isoformat(),
           "host": host, "certified_sha": sha,
           "recovery_receipt": receipt, "recovery_receipt_sha256": digest,
           "writers_running": 1}, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  chmod 0600 "$WRITER_RECEIPT"
  log "exactly one canonical writer started; receipt: $WRITER_RECEIPT"
}

mode_activate_scheduler() {
  verify_identity
  [[ -s "$WRITER_RECEIPT" ]] || die "no WRITER_ACTIVATED receipt; activate the writer first"
  [[ -s "$LIVE_ACCEPTANCE" ]] \
    || die "no LIVE-ACCEPTANCE receipt at $LIVE_ACCEPTANCE (HTTPS + Telegram acceptance must be recorded)"
  python3 - "$LIVE_ACCEPTANCE" <<'PY' || die "live acceptance is not satisfied; scheduler stays OFF"
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
missing = [k for k in ("https_ok", "telegram_ok") if doc.get(k) is not True]
if missing:
    print("missing acceptance: " + ", ".join(missing))
    raise SystemExit(3)
PY
  run "scheduler reconciliation" bash "$APP_ROOT/deploy/remote/reconcile-scheduler.sh"
  python3 - "$SCHEDULER_RECEIPT" "$DROPLET_ID" "$SHA" <<'PY'
import json, sys
from datetime import datetime, timezone
path, host, sha = sys.argv[1:4]
json.dump({"receipt": "SCHEDULER_ACTIVATED", "receipt_schema": 2,
           "activated_at_utc": datetime.now(timezone.utc).isoformat(),
           "host": host, "certified_sha": sha,
           "https_accepted": True, "telegram_accepted": True},
          open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  chmod 0600 "$SCHEDULER_RECEIPT"
  log "scheduler reconciliation completed; receipt: $SCHEDULER_RECEIPT"
}

case "$MODE" in
  prepare) mode_prepare ;;
  authorize) mode_authorize ;;
  execute) mode_execute ;;
  activate-writer) mode_activate_writer ;;
  activate-scheduler) mode_activate_scheduler ;;
  provision-toolkit) mode_provision_toolkit ;;
  check-toolkit) mode_check_toolkit ;;
  *) die "unknown mode $MODE" ;;
esac



