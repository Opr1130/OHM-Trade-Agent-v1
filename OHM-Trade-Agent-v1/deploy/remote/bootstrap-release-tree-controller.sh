#!/usr/bin/env bash
# O'Pip - one-time OWNER-OPERATED bootstrap of the release-tree deploy controller.
#
# The installed controller cannot install its own replacement: on a certified
# release-tree host it refuses to start at all (no Git metadata), and the running
# release predates this correction, so its tree carries neither the release-tree
# library nor the corrected controller. This script therefore materializes both
# FROM THE EXACT MERGED COMMIT in the trusted object cache, never from the
# running tree and never by fabricating anything.
#
# It runs once, by the owner, as root, from the droplet console or an
# owner-authorized SSH session. It is intentionally NOT reachable through the
# forced-command SSH gateway and is never invoked by the deploy workflow.
#
# It performs NO deployment: no container is started, stopped or recreated, and
# no application file, `.env` or production data is modified.
#
# Usage:
#   sudo -n bash bootstrap-release-tree-controller.sh --check
#   sudo -n bash bootstrap-release-tree-controller.sh --apply [--sha <40-hex>]
set -Eeuo pipefail

REPO_ROOT="/opt/OHM-Trade-Agent-v1"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
STATE_DIR="/var/lib/ohm-deploy"
LAST_GOOD_FILE="$STATE_DIR/last-good-sha"
DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"
LIBRARY_DST="/usr/local/lib/opip/opip_release_tree.sh"
CONTROLLER_PATH="OHM-Trade-Agent-v1/deploy/remote/ohm-deploy"
LIB_PATH="OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh"
REPO_URL="https://github.com/Opr1130/OHM-Trade-Agent-v1.git"
SOURCE_CACHE="/srv/opip-v2/cache/repo.git"
STAGE_DIR="$STATE_DIR/bootstrap-stage"
PREVIOUS_CONTROLLER="$STATE_DIR/controller-bootstrap-previous"
RECEIPT="$STATE_DIR/release-tree-bootstrap-receipt.env"

MODE="check"
TARGET_SHA=""
while (($#)); do
  case "$1" in
    --check) MODE="check" ;;
    --apply) MODE="apply" ;;
    --sha) TARGET_SHA="${2:-}"; shift || true ;;
    *) echo "usage: $0 [--check|--apply] [--sha <40-hex>]" >&2; exit 64 ;;
  esac
  shift
done

die() { echo "bootstrap-release-tree-controller: $*" >&2; exit 69; }
say() { printf '%s\n' "$*"; }

(( EUID == 0 )) || die "run as root: sudo -n bash $0 --check"
[[ "$REPO_ROOT" == "/opt/OHM-Trade-Agent-v1" && "$APP_ROOT" == "$REPO_ROOT/OHM-Trade-Agent-v1" ]] \
  || die "unexpected production paths"
[[ -d "$APP_ROOT" ]] || die "application root missing: $APP_ROOT"
[[ -f "$APP_ROOT/docker-compose.yml" ]] || die "compose file missing under $APP_ROOT"
command -v git >/dev/null 2>&1 || die "git is required for the trusted object cache"
command -v rsync >/dev/null 2>&1 || die "rsync is required for release-tree activation"

install -d -m 0700 "$STATE_DIR/releases" "$STATE_DIR/staging" "$STAGE_DIR"

RUNNING_SHA="$(cat "$LAST_GOOD_FILE" 2>/dev/null || true)"
[[ "$RUNNING_SHA" =~ ^[0-9a-f]{40}$ ]] || die "no exact running release SHA recorded in $LAST_GOOD_FILE"

say "== O'Pip release-tree controller bootstrap =="
say "mode=$MODE running_release=$RUNNING_SHA"
say "object_cache=$SOURCE_CACHE"

# --- 1. trusted object cache (authentic remote history, never fabricated) ---
if [[ ! -d "$SOURCE_CACHE" ]]; then
  say "object_cache=ABSENT"
  if [[ "$MODE" == "check" ]]; then
    say "CHECK=INCOMPLETE (cache absent; --apply creates it)"
    exit 0
  fi
  install -d -m 0700 "$(dirname "$SOURCE_CACHE")"
  git clone --bare --quiet "$REPO_URL" "$SOURCE_CACHE" \
    || die "cannot create the trusted object cache from $REPO_URL"
  chmod -R go-rwx "$SOURCE_CACHE"
else
  say "object_cache=PRESENT"
fi
git --git-dir="$SOURCE_CACHE" remote set-url origin "$REPO_URL"
git --git-dir="$SOURCE_CACHE" fetch --prune --no-tags origin main >/dev/null 2>&1 \
  || die "cannot fetch origin/main into the object cache"
REMOTE_MAIN="$(git --git-dir="$SOURCE_CACHE" ls-remote origin refs/heads/main | awk 'NR==1{print $1}')"
[[ "$REMOTE_MAIN" =~ ^[0-9a-f]{40}$ ]] || die "cannot resolve origin/main"
[[ -n "$TARGET_SHA" ]] || TARGET_SHA="$REMOTE_MAIN"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || die "controller commit must be an exact 40-hex SHA"
say "origin_main=$REMOTE_MAIN"
say "controller_commit=$TARGET_SHA"
git --git-dir="$SOURCE_CACHE" cat-file -e "${TARGET_SHA}^{commit}" \
  || die "controller commit $TARGET_SHA is not present in the object cache"

if [[ "$MODE" == "check" ]]; then
  say "controller_installed=$([[ -x "$DEPLOY_SCRIPT_DST" ]] && echo yes || echo no)"
  say "library_installed=$([[ -r "$LIBRARY_DST" ]] && echo yes || echo no)"
  say "baseline=$([[ -s "$STATE_DIR/current-release.env" ]] && echo RECORDED || echo PENDING)"
  say "rollback_vehicle=$([[ -s "$STATE_DIR/releases/$RUNNING_SHA/source.tar" ]] && echo RETAINED || echo PENDING)"
  say "CHECK=DONE (no change made)"
  exit 0
fi


# --- 2. materialize the reviewed controller + library FROM THAT COMMIT ------
STAGE_LIB="$STAGE_DIR/opip_release_tree.sh"
STAGE_CTRL="$STAGE_DIR/ohm-deploy"
git --git-dir="$SOURCE_CACHE" show "${TARGET_SHA}:${LIB_PATH}" > "$STAGE_LIB" \
  || die "cannot read $LIB_PATH at $TARGET_SHA"
git --git-dir="$SOURCE_CACHE" show "${TARGET_SHA}:${CONTROLLER_PATH}" > "$STAGE_CTRL" \
  || die "cannot read $CONTROLLER_PATH at $TARGET_SHA"
chmod 0600 "$STAGE_LIB" "$STAGE_CTRL"
bash -n "$STAGE_LIB" || die "release-tree library fails syntax validation"
bash -n "$STAGE_CTRL" || die "controller source fails syntax validation"
say "controller_source=MATERIALIZED_FROM_COMMIT"

# shellcheck source=/dev/null
source "$STAGE_LIB"
OPIP_SOURCE_MODE="release-tree"
REPO_OWNER="root"
GIT=(git --git-dir="$SOURCE_CACHE")

# --- 3. running release becomes the verified baseline + rollback vehicle ----
# The activation record written by a manifest-verified install is AUTHORITATIVE
# and is preserved when it provably describes the live tree. It differs from the
# recorded last-good release after a deploy whose activation succeeded and whose
# post-activation sequence then failed (the recovery state this controller
# exists for): overwriting it with the last-good SHA would label the live tree
# with a source identity it does not have and would make the very next deploy
# refuse with SOURCE_BASELINE unverified. A record that cannot be proven is
# still replaced, exactly as before, so nothing is ever taken on trust.
LIVE_RECORD="$(rt_state_get "$OPIP_CURRENT_RELEASE" sha || true)"
if [[ "$LIVE_RECORD" =~ ^[0-9a-f]{40}$ ]] \
  && [[ -s "$STATE_DIR/releases/$LIVE_RECORD/manifest.sha256" ]] \
  && rt_verify_tree "$APP_ROOT" "$STATE_DIR/releases/$LIVE_RECORD/manifest.sha256"; then
  say "baseline=PRESERVED sha=$LIVE_RECORD (live tree matches its manifest-verified install)"
else
  rt_snapshot_current "$RUNNING_SHA" || die "cannot retain the running release for rollback"
  rt_write_state "$OPIP_CURRENT_RELEASE" "$RUNNING_SHA" "unverified-baseline" \
    "$(rt_manifest_digest "$STATE_DIR/releases/$RUNNING_SHA/manifest.sha256")" \
    "unverified-baseline"
  say "baseline=RECORDED"
fi
say "rollback_vehicle=RETAINED"

# --- 4. install the library at its stable root-controlled location ----------
# ohm-deploy resolves this path BEFORE the release tree, so it must exist before
# the corrected controller can initialise: the running release predates this
# correction and cannot supply it. It lives beside the controller, not inside a
# release, so every later release keeps it.
install -d -o root -g root -m 0755 "$(dirname "$LIBRARY_DST")"
libstaged="$(mktemp "$(dirname "$LIBRARY_DST")/.opip_release_tree.stage.XXXXXX")"
cp -- "$STAGE_LIB" "$libstaged"
chmod 0644 "$libstaged"
chown root:root "$libstaged"
mv -f -- "$libstaged" "$LIBRARY_DST"
bash -n "$LIBRARY_DST" || die "installed library fails syntax validation"
say "library_installed=$LIBRARY_DST"

# --- 5. install the controller atomically, retaining the previous one -------
if [[ -f "$DEPLOY_SCRIPT_DST" ]]; then
  cp -a "$DEPLOY_SCRIPT_DST" "$PREVIOUS_CONTROLLER"
  chmod 0600 "$PREVIOUS_CONTROLLER"
  say "previous_controller=$PREVIOUS_CONTROLLER"
fi
staged="$(mktemp "$(dirname "$DEPLOY_SCRIPT_DST")/.ohm-deploy.stage.XXXXXX")"
cp -- "$STAGE_CTRL" "$staged"
chmod 0755 "$staged"
chown root:root "$staged"
mv -f -- "$staged" "$DEPLOY_SCRIPT_DST"
[[ -x "$DEPLOY_SCRIPT_DST" ]] || die "controller installation failed"

{
  printf 'status=SUCCESS\n'
  printf 'bootstrapped_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'controller_commit=%s\n' "$TARGET_SHA"
  printf 'running_release=%s\n' "$RUNNING_SHA"
  printf 'origin_main=%s\n' "$REMOTE_MAIN"
  printf 'controller_sha256=%s\n' "$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"
  printf 'library_dst=%s\n' "$LIBRARY_DST"
  printf 'library_sha256=%s\n' "$(sha256sum "$LIBRARY_DST" | awk '{print $1}')"
  printf 'previous_controller=%s\n' "$PREVIOUS_CONTROLLER"
} > "$RECEIPT"
chmod 0600 "$RECEIPT"

say "CONTROLLER_BOOTSTRAP=SUCCESS"
say "receipt=$RECEIPT"
say "next: owner-gated deployment through the existing workflow"
