#!/usr/bin/env bash
# HF-03 / R2: guarded first-install bootstrap for V2. Runs ON the V2 host as root.
#
# Modes (each writes its own receipt):
#   --mode prepare             read-only: identity, release, image, storage, preflight
#   --mode authorize           owner gate: confirm sha+host, acknowledge an expected block
#   --mode execute             mutate: install verified release, dirs, config, core only
#   --mode activate-writer     start EXACTLY ONE writer after a verified receipt:
#                              the historical native-recovery receipt (default) or
#                              the owner-authorized fresh-genesis receipt
#                              (--genesis-receipt <path>)
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
CANONICAL_TOOLKIT_ROOT="/opt/opip-m07-toolkit"
TOOLKIT_MANIFEST="$TOOLKIT_ROOT/TOOLKIT-MANIFEST.sha256"
PROVISION_FROM=""
SOURCE_SHA=""
CHECK_APP_ROOT=""
CHECK_MANIFEST=""
CHECK_RECEIPT=""
RUNNING_IMAGE_ID=""
SRC=""                      # resolved by resolve_src (never guessed)
REPO_ROOT="/opt/OHM-Trade-Agent-v1"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
ENV_FILE="$APP_ROOT/.env"
CANONICAL_DIR="$APP_ROOT/data/opip/canonical"
LIVE_DB="$CANONICAL_DIR/opip_canonical_v1.sqlite3"
ETC="/etc/opip/v2"
STATE="/var/lib/opip-bootstrap"
RECOVERY_RECEIPT="/var/lib/opip-migration/canonical-recovery-receipt.json"
# Fresh-start route: a brand-new, empty V2 canonical history. This receipt is
# only used when the operator selects it explicitly with --genesis-receipt, so
# the historical migration route stays available but is never inferred.
GENESIS_RECEIPT="$STATE/canonical-fresh-genesis-receipt.json"
GENESIS_ROUTE="0"
PREPARED="$STATE/BOOTSTRAP_PREPARED.json"
AUTHORIZED="$STATE/BOOTSTRAP_AUTHORIZED.json"
EXECUTED="$STATE/BOOTSTRAP_EXECUTED.json"
WRITER_RECEIPT="$STATE/WRITER_ACTIVATED.json"
SCHEDULER_RECEIPT="$STATE/SCHEDULER_ACTIVATED.json"
LIVE_ACCEPTANCE="$STATE/LIVE-ACCEPTANCE.json"
MANIFEST="$STATE/release-manifest.sha256"
#: Durable incident registry the certified preflight reads at /app/data/<this>.
#: The read-only preflight must observe the REAL runtime copy under APP_ROOT/data,
#: never the immutable release tree, and a missing store is never "no incidents".
INCIDENT_STATE_FILE="system_incidents.json"
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
  for helper in opip_receipts.py opip_rollback_guard.py opip_recovery.py opip_genesis.py \
                opip_bundle.py opip_archive.py opip_layout.py opip_provenance.py \
                opip_signals.py; do
    printf '%s\n' "$helper"
  done
}

toolkit_manifest_verify() {
  local root="$1"
  local manifest="$root/TOOLKIT-MANIFEST.sha256"
  [[ -d "$root/scripts" && -s "$manifest" ]] || return 1
  [[ -s "$root/TOOLKIT-PROVENANCE.json" ]] || return 1
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

  local src dest parent tmp_root dest_raw
  src="$(realpath -e -- "$PROVISION_FROM" 2>/dev/null)" \
    || die "toolkit source does not resolve: $PROVISION_FROM"

  # AC-010: trusted source origin. A locally generated manifest is not origin
  # evidence; the source must be a clean git checkout at an explicit commit.
  [[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] \
    || die "--source-sha <40-hex reviewed toolkit commit> is required for provisioning"
  # safe.directory applies to the repository top-level; these are read-only
  # provenance checks, so an explicit wildcard is used rather than embedding a
  # subdirectory path that git will not match.
  [[ "$(git -c safe.directory='*' -C "$src" rev-parse --is-inside-work-tree 2>/dev/null)" == "true" ]] \
    || die "toolkit source must be a git checkout for provenance: $src"
  [[ "$(git -c safe.directory='*' -C "$src" rev-parse HEAD 2>/dev/null)" == "$SOURCE_SHA" ]] \
    || die "toolkit source HEAD does not equal --source-sha $SOURCE_SHA"
  [[ -z "$(git -c safe.directory='*' -C "$src" status --porcelain -- . 2>/dev/null)" ]] \
    || die "toolkit source has uncommitted changes; review and commit before provisioning"
  dest_raw="${TOOLKIT_ROOT%/}"
  [[ "$dest_raw" == /* ]] || die "destination must be an absolute path: $TOOLKIT_ROOT"
  case "$dest_raw" in
    *"/../"*|*"/./"*|*"//"*|*/..|*/.) die "destination contains non-canonical components: $TOOLKIT_ROOT" ;;
  esac
  dest="$(realpath -m -- "$dest_raw" 2>/dev/null)" || die "invalid destination: $TOOLKIT_ROOT"
  [[ "$dest" == "$dest_raw" ]] \
    || die "destination traverses a symlink or non-canonical prefix: $dest_raw -> $dest"

  # 1. Production (root) provisioning is restricted to the canonical location.
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    [[ "$dest" == "$CANONICAL_TOOLKIT_ROOT" ]] \
      || die "root provisioning is restricted to $CANONICAL_TOOLKIT_ROOT (got $dest)"
  else
    tmp_root="${TMPDIR:-/tmp}"
    case "$dest" in
      "$CANONICAL_TOOLKIT_ROOT"|"$tmp_root"/*|/tmp/*) ;;
      *) die "non-root provisioning is limited to $CANONICAL_TOOLKIT_ROOT or a temporary root (got $dest)" ;;
    esac
  fi

  # 2. Refuse root and protected destinations outright.
  case "$dest" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
      die "refusing protected destination: $dest" ;;
    /bin/*|/boot/*|/dev/*|/etc/*|/lib/*|/lib64/*|/media/*|/mnt/*|/proc/*|/root/*|/run/*|/sbin/*|/srv/*|/sys/*|/usr/*|/var/*)
      die "refusing protected destination: $dest" ;;
  esac

  # 3. Refuse source/destination overlap in either direction.
  case "$dest/" in "$src/"*) die "destination is inside the toolkit source: $dest" ;; esac
  case "$src/" in "$dest/"*) die "toolkit source is inside the destination: $src" ;; esac

  # 4. An existing destination may only be a previously verified toolkit with
  #    exactly the expected files; anything else is left untouched.
  if [[ -e "$dest" || -L "$dest" ]]; then
    [[ -d "$dest" && ! -L "$dest" ]] \
      || die "destination exists and is not a plain directory: $dest"
    if [[ -n "$(find "$dest" -type l -print -quit 2>/dev/null)" ]]; then
      die "destination contains symlinks; refusing to modify: $dest"
    fi
    toolkit_manifest_verify "$dest" \
      || die "destination exists with unexpected contents; refusing to modify: $dest"
    local allowed_top top manifested present
    allowed_top="$(awk '{print $2}' "$dest/TOOLKIT-MANIFEST.sha256" \
      | sed 's#^\./##' | cut -d/ -f1 | sort -u)"
    while IFS= read -r top; do
      [[ "$top" == "TOOLKIT-MANIFEST.sha256" ]] && continue
      grep -Fxq -- "$top" <<<"$allowed_top" \
        || die "destination contains an unexpected entry: $dest/$top"
    done < <(find "$dest" -mindepth 1 -maxdepth 1 -printf '%f\n')
    manifested="$(wc -l <"$dest/TOOLKIT-MANIFEST.sha256")"
    present="$(find "$dest" -type f ! -name 'TOOLKIT-MANIFEST.sha256' | wc -l)"
    [[ "$manifested" -eq "$present" ]] \
      || die "destination contains unexpected files; refusing to modify: $dest"
  fi

  parent="$(dirname -- "$dest")"
  [[ -d "$parent" && ! -L "$parent" ]] \
    || die "destination parent is missing or a symlink: $parent"

  # 5. Build in an isolated staging directory on the destination filesystem.
  #    No destructive sync ever targets the destination itself.
  local staging previous owner_opt=()
  staging="$(mktemp -d -- "$parent/.opip-m07-toolkit.staging.XXXXXX")" \
    || die "cannot create a staging directory in $parent"
  trap '[[ -n "${staging:-}" && -d "${staging:-}" ]] && rm -rf -- "$staging"' EXIT
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then owner_opt=(--chown=root:root); fi
  rsync -a "${owner_opt[@]}" --exclude '__pycache__' --exclude 'tests' --exclude 'evidence' \
    --exclude 'TOOLKIT-MANIFEST.sha256' "$src/" "$staging/" \
    || die "staging copy failed; destination was not modified"
  find "$staging" -type d -exec chmod 0755 {} +
  find "$staging" -type f -exec chmod 0644 {} +
  find "$staging" -type f -name '*.sh' -exec chmod 0755 {} +
  python3 - "$staging/TOOLKIT-PROVENANCE.json" "$SOURCE_SHA" "$SHA" <<'PY'
import json, sys
from datetime import datetime, timezone
path, source_sha, certified = sys.argv[1:4]
json.dump({
    "toolkit_schema": 1,
    "source_git_sha": source_sha,
    "certified_app_sha": certified,
    "provisioned_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
}, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  chmod 0644 "$staging/TOOLKIT-PROVENANCE.json"
  toolkit_manifest_build "$staging"
  toolkit_manifest_verify "$staging" \
    || die "staged toolkit failed integrity verification; destination was not modified"

  # 6. Atomic promotion. A verified previous toolkit is moved aside first and
  #    restored if the promotion fails.
  if [[ -e "$dest" ]]; then
    previous="$parent/.opip-m07-toolkit.previous.$$"
    [[ ! -e "$previous" ]] || die "stale previous directory present: $previous"
    mv -- "$dest" "$previous" \
      || die "cannot move the existing toolkit aside; nothing was changed"
    if ! mv -- "$staging" "$dest"; then
      mv -- "$previous" "$dest" \
        || die "promotion failed and the previous toolkit could not be restored: $previous"
      die "promotion failed; the previous toolkit was restored"
    fi
    rm -rf -- "$previous"
  else
    mv -- "$staging" "$dest" || die "cannot promote the staged toolkit to $dest"
  fi
  trap - EXIT

  toolkit_manifest_verify "$dest" || die "promoted toolkit failed integrity verification"
  log "toolkit provisioned and verified: $dest"
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

mode_check_release() {
  # AC-009: verify the installed tree against the installer manifest using
  # exactly the installation exclusions. No .git is required or consulted.
  local root="${CHECK_APP_ROOT:-$APP_ROOT}"
  local manifest="${CHECK_MANIFEST:-$MANIFEST}"
  local receipt="${CHECK_RECEIPT:-$EXECUTED}"
  [[ -d "$root" ]] || die "installed release root missing: $root"
  [[ -s "$manifest" ]] || die "release manifest missing: $manifest"
  manifest_verify "$root" "$manifest" \
    || die "installed release does not match the certified manifest"
  [[ -s "$receipt" ]] || die "bootstrap execution receipt missing: $receipt"
  python3 - "$receipt" "$SHA" <<'PY' || die "execution receipt does not attest the certified SHA"
import json, sys
path, sha = sys.argv[1:3]
doc = json.load(open(path, encoding="utf-8"))
if doc.get("receipt") != "BOOTSTRAP_EXECUTED":
    raise SystemExit(3)
if str(doc.get("certified_sha")) != sha:
    raise SystemExit(3)
if doc.get("writer_started") is not False:
    raise SystemExit(3)
PY
  printf 'RELEASE_CHECK=PASS\n'
  printf 'RELEASE_ROOT=%s\n' "$root"
  printf 'CERTIFIED_SHA=%s\n' "$SHA"
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
    --source-sha) SOURCE_SHA="$2"; shift 2 ;;
    --app-root) CHECK_APP_ROOT="$2"; shift 2 ;;
    --manifest) CHECK_MANIFEST="$2"; shift 2 ;;
    --receipt) CHECK_RECEIPT="$2"; shift 2 ;;
    --genesis-receipt) GENESIS_RECEIPT="$2"; GENESIS_ROUTE="1"; shift 2 ;;
    --activate-scheduler) shift ;;   # legacy flag: scheduler needs its own mode now
    *) die "unknown argument $1" ;;
  esac
done

[[ -n "$MODE" ]] || die "--mode prepare|authorize|execute|activate-writer|activate-scheduler|provision-toolkit|check-toolkit is required"
case "$MODE" in
  check-toolkit|provision-toolkit|check-release)
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
  # `.env` (owner-provided before prepare) and `data` (created by prepare/execute)
  # are runtime state, never release content. Their presence must not classify the
  # root as an already-installed release: `prepare` requires `.env` to exist at
  # APP_ROOT before `execute` can run, so treating it as foreign content deadlocks
  # the documented sequence. Both entries are also excluded from the install copy,
  # so `--delete` cannot remove them.
  if [[ -d "$APP_ROOT" ]]; then
    local foreign
    foreign="$(find "$APP_ROOT" -mindepth 1 -maxdepth 1 \
      ! -name data ! -name .env -print -quit 2>/dev/null)"
    if [[ -n "$foreign" ]]; then
      manifest_verify "$APP_ROOT" "$MANIFEST" \
        || die "runtime root exists with non-certified content; refusing to overwrite $APP_ROOT"
      log "runtime root already installed and verified: $APP_ROOT"
      return 0
    fi
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

verify_service_image() {
  local service="$1" rendered verdict
  rendered="$(mktemp "${TMPDIR:-/tmp}/opip-compose-render.XXXXXX.json")" \
    || die "cannot create a Compose render file"
  if ! compose config --format json >"$rendered" 2>/dev/null; then
    rm -f -- "$rendered"
    die "cannot render the certified Compose configuration"
  fi
  if ! verdict="$(python3 "$TOOLS/opip_image.py" verify --compose-json "$rendered" \
      --service "$service" --certified-image-id "$IMAGE_ID" --docker docker)"; then
    log "$verdict"
    rm -f -- "$rendered"
    die "certified image identity check failed for $service"
  fi
  rm -f -- "$rendered"
  log "$verdict"
}

verify_running_image() {
  local service="$1" cid image
  cid="$(compose ps -q "$service" 2>/dev/null | head -n 1)"
  [[ -n "$cid" ]] || die "no running container for $service"
  image="$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null)"
  [[ -n "$image" ]] || die "cannot read the running image for $service"
  python3 "$TOOLS/opip_image.py" container --observed-image-id "$image" \
    --certified-image-id "$IMAGE_ID" >/dev/null \
    || die "running $service image $image does not match the certified $IMAGE_ID"
  RUNNING_IMAGE_ID="$image"
  log "running $service image verified: $image"
}

mode_execute() {
  verify_identity
  verify_source
  verify_image
  resolve_toolkit || die "M07 toolkit not found or failed integrity verification: $TOOLKIT_ROOT"
  [[ -s "$AUTHORIZED" ]] || die "no BOOTSTRAP_AUTHORIZED receipt; owner authorisation is required"
  # Last pre-mutation check: the evidence the authorization was bound to must
  # still describe the live host.
  assert_incident_store_unchanged
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

  verify_service_image ohm-trade-agent
  run "start core" compose up -d --no-build --no-deps ohm-trade-agent
  verify_running_image ohm-trade-agent
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

  python3 - "$EXECUTED" "$DROPLET_ID" "$SHA" "$IMAGE_ID" "$cfg" "$RUNNING_IMAGE_ID" <<'PY'
import json, sys
from datetime import datetime, timezone
path, host, sha, image, cfg, runtime_image = sys.argv[1:7]
json.dump({"receipt": "BOOTSTRAP_EXECUTED", "receipt_schema": 2,
           "executed_at_utc": datetime.now(timezone.utc).isoformat(),
           "host": host, "certified_sha": sha, "image_id": image,
           "runtime_image_id": runtime_image, "config_digest": cfg,
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
  # A failed or refused prepare must never leave an earlier PREPARED receipt behind
  # for authorize to bind against.
  rm -f "$PREPARED"
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
  # The protection block was derived from a specific incident-store digest; it
  # must not have moved between observation and authorization.
  assert_incident_store_unchanged
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
  local runtime_data="$APP_ROOT/data"
  local incident_store="$runtime_data/$INCIDENT_STATE_FILE"
  [[ -s "$ENV_FILE" ]] || die ".env is missing at $ENV_FILE (canonical location, owner-provided)"

  # --- Phase B: observe the REAL runtime data, never the release tree ----------
  # $SRC is the immutable certified release (an install source, not a runtime
  # root), so $SRC/data is never V2 evidence. The app writes its durable incident
  # registry under $APP_ROOT/data.
  if [[ -L "$runtime_data" ]]; then
    die "runtime data directory is a symlink; refusing: $runtime_data"
  fi
  if [[ ! -d "$runtime_data" ]]; then
    die "runtime data directory is missing: $runtime_data (prepare creates it before the preflight)"
  fi
  if [[ ! -r "$runtime_data" ]]; then
    die "runtime data directory is not readable: $runtime_data"
  fi
  if [[ -L "$incident_store" ]]; then
    die "incident store is a symlink; refusing: $incident_store"
  fi
  if [[ -e "$incident_store" && ! -f "$incident_store" ]]; then
    die "incident store is not a regular file: $incident_store"
  fi

  # Read-only preflight against the immutable release code; the environment file is
  # ALWAYS the canonical one under APP_ROOT (never a second .env inside the release).
  set +e
  docker run --rm --read-only --tmpfs /tmp:rw,size=32m --pids-limit 64 --memory 384m \
    --env-file "$ENV_FILE" -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$SRC/app:/app/app:ro" -v "$runtime_data:/app/data:ro" "$IMAGE_ID" \
    python -m app.jobs.preflight_protection_health \
      --candidate-sha "$SHA" --release-profile EVIDENCE_SHADOW >"$receipt" 2>>"$LOG"
  pf_rc=$?
  set -e
  [[ -s "$receipt" ]] || die "protection preflight produced no receipt (rc=$pf_rc)"
  log "protection preflight exit=$pf_rc (contract-validated below)"

  local evaluated
  evaluated="$(python3 - "$receipt" "$pf_rc" "$SHA" "$incident_store" <<'PY'
# --- M07-PREFLIGHT-CONTRACT-BEGIN ---
import hashlib
import json
import re
import sys
from pathlib import Path

receipt_path, raw_rc, expected_sha, store_raw = sys.argv[1:5]
store_path = Path(store_raw)
PROFILE = "EVIDENCE_SHADOW"
STATE_READY, STATE_BLOCKED = "READY", "BLOCKED"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def refuse(reason: str) -> None:
    print(json.dumps({"contract_error": reason}, sort_keys=True))
    raise SystemExit(4)


def string_list(value: object, name: str) -> list:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        refuse(f"{name} is missing or is not a list of strings")
    return [str(item) for item in value]


try:
    rc = int(raw_rc)
except (TypeError, ValueError):
    refuse(f"preflight exit code is not an integer: {raw_rc!r}")

try:
    document = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
except Exception as exc:  # noqa: BLE001 - unreadable evidence must refuse
    refuse(f"preflight receipt is unreadable: {type(exc).__name__}")
if not isinstance(document, dict):
    refuse("preflight receipt is not a JSON object")

if document.get("schema_version") != 1:
    refuse(f"unexpected preflight schema_version={document.get('schema_version')!r}")
if document.get("candidate_sha") != expected_sha:
    refuse(f"candidate_sha={document.get('candidate_sha')!r} != expected {expected_sha}")
if not SHA_RE.fullmatch(str(document.get("candidate_sha"))):
    refuse("candidate_sha is not a 40-hex SHA")
if document.get("release_profile") != PROFILE:
    refuse(f"release_profile={document.get('release_profile')!r} is not {PROFILE}")
if document.get("read_only") is not True:
    refuse("preflight document is not marked read_only")

verdict = document.get("verdict")
if not isinstance(verdict, dict):
    refuse("verdict missing")
ready = verdict.get("ready")
if not isinstance(ready, bool):
    refuse("verdict.ready is missing or is not a boolean")
reason = verdict.get("reason")
if not isinstance(reason, str) or not reason.strip():
    refuse("verdict.reason is missing or empty")

shadow = document.get("evidence_shadow")
if not isinstance(shadow, dict):
    refuse("evidence_shadow missing")
shadow_ready = shadow.get("ready")
shadow_state = shadow.get("state")
if not isinstance(shadow_ready, bool):
    refuse("evidence_shadow.ready is missing or is not a boolean")
if shadow_state not in (STATE_READY, STATE_BLOCKED):
    refuse(f"evidence_shadow.state={shadow_state!r} is not READY/BLOCKED")
blocking_codes = string_list(
    shadow.get("blocking_reason_codes"), "evidence_shadow.blocking_reason_codes"
)
blocking_incidents = string_list(
    shadow.get("blocking_incidents"), "evidence_shadow.blocking_incidents"
)

strict = document.get("strict_f11")
if not isinstance(strict, dict):
    refuse("strict_f11 missing")
strict_codes = string_list(strict.get("reason_codes"), "strict_f11.reason_codes")

if shadow_state == STATE_READY and (not shadow_ready or blocking_codes):
    refuse("evidence_shadow is READY but not ready or carries blocking codes")
if shadow_state == STATE_BLOCKED and shadow_ready:
    refuse("evidence_shadow.state=BLOCKED contradicts evidence_shadow.ready=true")
if ready and not shadow_ready:
    refuse("verdict.ready=true contradicts evidence_shadow.ready=false")
if ready and shadow_state != STATE_READY:
    refuse("verdict.ready=true contradicts evidence_shadow.state")
if not ready and shadow_state == STATE_READY and shadow_ready:
    refuse("verdict.ready=false contradicts a READY evidence_shadow")
expected_rc = 0 if ready else 76
if rc != expected_rc:
    refuse(f"preflight exit {rc} contradicts verdict.ready={ready} (expected {expected_rc})")

# --- durable incident store observation (never "absent means no incidents") ---
# Aligned with the certified observers:
#   read_json_without_quarantine() rejects non-finite constants and non-objects;
#   read_incident_projection() treats a non-dict `incidents` map or any non-dict
#   row as UNPROVEN, and skips rows whose state is RECOVERED.
SCHEMA_SUPPORTED = (1, 2)          # system_incidents.SCHEMA_VERSION is 2; 1 is readable
STATE_RECOVERED = "RECOVERED"


def _reject_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


store_state = "ABSENT"
store_digest = None
store_bytes = 0
store_active = 0
store_recovered = 0

if store_path.exists() and not store_path.is_file():
    refuse("incident store is not a regular file")
if store_path.is_file():
    try:
        raw = store_path.read_bytes()
    except OSError:
        store_state = "UNREADABLE"
    else:
        store_digest = hashlib.sha256(raw).hexdigest()
        store_bytes = len(raw)
        try:
            payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except Exception:  # noqa: BLE001 - malformed state is never "no incident"
            store_state = "MALFORMED"
        else:
            schema = payload.get("schema_version") if isinstance(payload, dict) else None
            schema_ok = (
                isinstance(schema, int)
                and not isinstance(schema, bool)
                and schema in SCHEMA_SUPPORTED
            )
            if not isinstance(payload, dict) or not schema_ok:
                store_state = "MALFORMED"
            elif not isinstance(payload.get("incidents"), dict):
                store_state = "MALFORMED"
            else:
                malformed_row = False
                for row in payload["incidents"].values():
                    if not isinstance(row, dict):
                        malformed_row = True
                        break
                    if str(row.get("state") or "") == STATE_RECOVERED:
                        store_recovered += 1
                    else:
                        store_active += 1
                if malformed_row:
                    # A structurally corrupt record cannot be proven recovered and
                    # is never "no incident": the whole store is unproven.
                    store_state = "MALFORMED"
                    store_recovered = store_active = 0
                elif store_active:
                    store_state = "VALID_OPEN_INCIDENTS"
                elif store_recovered:
                    store_state = "VALID_RECOVERED_ONLY"
                else:
                    store_state = "VALID_EMPTY"

# --- READY must not contradict host incident evidence -------------------------
# A READY verdict requires the certified projection to have seen a valid store
# with no active incident. If the host-side store is absent, unreadable,
# malformed or holds active rows, the observation and the host disagree and the
# whole document is refused rather than trusted.
if ready and store_state not in ("VALID_EMPTY", "VALID_RECOVERED_ONLY"):
    refuse(
        "verdict.ready=true contradicts host incident evidence "
        f"(store={store_state}); refusing to trust the observation"
    )

# A first-host acknowledgement requires POSITIVE proof: a provably valid, EMPTY
# incident store (incident state genuinely known) plus a block attributable only
# to reviewed codes. It is never inferred from absent reason codes, from a generic
# UNAVAILABLE state, or from a missing store. ACK_ELIGIBLE_BLOCKING is empty, so
# `--acknowledge-fresh-host` cannot currently bypass any certified block.
ACK_ELIGIBLE_BLOCKING = frozenset()
fresh_host_only = bool(
    not ready
    and not blocking_incidents
    and store_state == "VALID_EMPTY"
    and blocking_codes
    and set(blocking_codes).issubset(ACK_ELIGIBLE_BLOCKING)
)

print(json.dumps({
    "ready": ready,
    "reason": reason,
    "reasons": blocking_codes,
    "blocking_reason_codes": blocking_codes,
    "blocking_incidents": blocking_incidents,
    "evidence_shadow_state": shadow_state,
    "strict_f11_reason_codes": strict_codes,
    "incident_store": {
        "state": store_state,
        "sha256": store_digest,
        "bytes": store_bytes,
        "active_records": store_active,
        "recovered_records": store_recovered,
    },
    "fresh_host_only": fresh_host_only,
}, sort_keys=True))
# --- M07-PREFLIGHT-CONTRACT-END ---
PY
)" || die "protection preflight evidence is malformed or contradictory; refusing to prepare"

  log "protection preflight contract: $evaluated"
  printf '%s\n' "$evaluated"
}

# --- incident-store drift protection -----------------------------------------
# The preflight records the digest of the durable incident store it observed.
# Authorization and the last pre-mutation check before execute re-derive the live
# digest and refuse on any drift, so the phases can never silently disagree about
# the evidence the protection block was based on.
incident_store_digest() {
  local store="$APP_ROOT/data/$INCIDENT_STATE_FILE"
  if [[ -L "$store" ]]; then
    die "incident store is a symlink; refusing: $store"
  fi
  if [[ ! -e "$store" ]]; then
    printf 'ABSENT\n'
    return 0
  fi
  if [[ ! -f "$store" ]]; then
    die "incident store is not a regular file: $store"
  fi
  sha256sum "$store" | cut -d' ' -f1
}

assert_incident_store_unchanged() {
  local recorded current
  recorded="$(python3 - "$PREPARED" <<'PY2'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
store = (document.get("preflight") or {}).get("incident_store") or {}
print(store.get("sha256") or "ABSENT")
PY2
)"
  current="$(incident_store_digest)"
  if [[ "$recorded" != "$current" ]]; then
    die "incident store drifted since prepare (prepared=$recorded live=$current); re-run prepare"
  fi
  log "incident store unchanged since prepare: $current"
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
  # Activation is a one-shot owner gate. Re-running it must never admit a second
  # writer, and must never rewrite the activation evidence.
  [[ ! -e "$WRITER_RECEIPT" ]] \
    || die "WRITER_ACTIVATED already exists at $WRITER_RECEIPT; duplicate activation is refused"
  if compose ps --status running --services | grep -qx 'opip-canonical-writer'; then
    die "a canonical writer is already running; refusing to start a second writer"
  fi
  local receipt route gate
  if [[ "$GENESIS_ROUTE" == "1" ]]; then
    # Fresh-start route: a brand-new empty V2 history. The gate re-verifies the
    # store bytes, schema, coordinates, certified source and host identity.
    receipt="$GENESIS_RECEIPT"
    route="fresh-genesis"
    [[ -f "$receipt" ]] \
      || die "no fresh-genesis receipt at $receipt; the writer stays OFF"
  else
    # Historical migration route (unchanged, and never inferred):
    # the native-recovery receipt must be bound to this destination.
    receipt="$RECOVERY_RECEIPT"
    route="native-recovery"
    [[ -f "$receipt" ]] \
      || die "no canonical recovery receipt at $RECOVERY_RECEIPT; the writer stays OFF (use --genesis-receipt for the documented fresh-start route)"
  fi
  if ! gate="$(python3 "$TOOLS/opip_rollback_guard.py" writer-gate \
      --receipt "$receipt" --db "$LIVE_DB" --app-root "$APP_ROOT" \
      --expected-host "$DROPLET_ID")"; then
    log "$gate"
    die "authorising receipt does not permit a canonical writer for $LIVE_DB"
  fi
  log "writer gate PASS ($route): $gate"
  verify_service_image opip-canonical-writer
  run "start writer" compose up -d --no-build --no-deps opip-canonical-writer
  verify_running_image opip-canonical-writer
  local running
  running="$(compose ps --status running --services | grep -cx 'opip-canonical-writer' || true)"
  [[ "$running" == "1" ]] || die "expected exactly one running canonical writer, observed $running"
  python3 - "$WRITER_RECEIPT" "$DROPLET_ID" "$SHA" "$receipt" "$(sha256sum "$receipt" | cut -d' ' -f1)" "$RUNNING_IMAGE_ID" "$route" <<'PY'
import json, sys
from datetime import datetime, timezone
path, host, sha, receipt, digest, runtime_image, route = sys.argv[1:8]
json.dump({"receipt": "WRITER_ACTIVATED", "receipt_schema": 2,
           "activated_at_utc": datetime.now(timezone.utc).isoformat(),
           "host": host, "certified_sha": sha,
           "activation_route": route,
           "authorizing_receipt": receipt, "authorizing_receipt_sha256": digest,
           "runtime_image_id": runtime_image,
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
  check-release) mode_check_release ;;
  *) die "unknown mode $MODE" ;;
esac



