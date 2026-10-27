#!/usr/bin/env bash
# O'Pip - one-time OWNER-OPERATED bootstrap of the release-tree deploy controller.
#
# The installed controller cannot install its own replacement: until this
# bootstrap runs it refuses to start at all on a certified release-tree host,
# because it requires Git metadata that host does not have. This script runs
# once, by the owner, as root, from the droplet console or an owner-authorized
# SSH session. It is intentionally NOT reachable through the forced-command SSH
# gateway and is never invoked by the deploy workflow.
#
# It performs NO deployment:
#   * creates the trusted Git object cache used to bind a commit to an artifact
#   * records the RUNNING release as the verified source baseline and its
#     rollback vehicle
#   * installs the release-tree-aware controller atomically, retaining the
#     previous controller and writing a bounded receipt
# No container is started, stopped or recreated. No application file, `.env` or
# production data is modified.
#
# Usage:
#   sudo -n bash bootstrap-release-tree-controller.sh --check
#   sudo -n bash bootstrap-release-tree-controller.sh --apply
set -Eeuo pipefail

REPO_ROOT="/opt/OHM-Trade-Agent-v1"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
STATE_DIR="/var/lib/ohm-deploy"
LAST_GOOD_FILE="$STATE_DIR/last-good-sha"
DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"
CONTROLLER_REL="OHM-Trade-Agent-v1/deploy/remote/ohm-deploy"
LIB_REL="OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh"
REPO_URL="https://github.com/Opr1130/OHM-Trade-Agent-v1.git"
PREVIOUS_CONTROLLER="$STATE_DIR/controller-bootstrap-previous"
RECEIPT="$STATE_DIR/release-tree-bootstrap-receipt.env"

MODE="check"
case "${1:---check}" in
  --check) MODE="check" ;;
  --apply) MODE="apply" ;;
  *) echo "usage: $0 [--check|--apply]" >&2; exit 64 ;;
esac

die() { echo "bootstrap-release-tree-controller: $*" >&2; exit 69; }
say() { printf '%s\n' "$*"; }

(( EUID == 0 )) || die "run as root: sudo -n bash $0 --check"
[[ "$REPO_ROOT" == "/opt/OHM-Trade-Agent-v1" && "$APP_ROOT" == "$REPO_ROOT/OHM-Trade-Agent-v1" ]] \
  || die "unexpected production paths"
[[ -d "$APP_ROOT" ]] || die "application root missing: $APP_ROOT"
[[ -f "$APP_ROOT/docker-compose.yml" ]] || die "compose file missing under $APP_ROOT"
[[ -f "$APP_ROOT/$CONTROLLER_REL" ]] || die "controller source missing: $APP_ROOT/$CONTROLLER_REL"
[[ -f "$APP_ROOT/$LIB_REL" ]] || die "release-tree library missing: $APP_ROOT/$LIB_REL"
command -v git >/dev/null 2>&1 || die "git is required for the trusted object cache"
command -v rsync >/dev/null 2>&1 || die "rsync is required for release-tree activation"

# shellcheck source=lib/opip_release_tree.sh
# shellcheck disable=SC1091
source "$APP_ROOT/$LIB_REL"

install -d -m 0700 "$STATE_DIR/releases" "$STATE_DIR/staging"

RUNNING_SHA="$(cat "$LAST_GOOD_FILE" 2>/dev/null || true)"
[[ "$RUNNING_SHA" =~ ^[0-9a-f]{40}$ ]] || die "no exact running release SHA recorded in $LAST_GOOD_FILE"

say "== O'Pip release-tree controller bootstrap =="
say "mode=$MODE running_release=$RUNNING_SHA"
say "object_cache=$OPIP_SOURCE_CACHE"

if [[ -d "$OPIP_SOURCE_CACHE" ]]; then
  say "object_cache=PRESENT"
  if git --git-dir="$OPIP_SOURCE_CACHE" cat-file -e "${RUNNING_SHA}^{commit}" 2>/dev/null; then
    say "running_release_object=PRESENT"
  else
    say "running_release_object=FETCH_REQUIRED"
  fi
else
  say "object_cache=ABSENT"
fi

if [[ "$MODE" == "check" ]]; then
  say "controller_installed=$([[ -x "$DEPLOY_SCRIPT_DST" ]] && echo yes || echo no)"
  say "baseline=$([[ -s "$STATE_DIR/current-release.env" ]] && echo RECORDED || echo PENDING)"
  say "rollback_vehicle=$([[ -s "$STATE_DIR/releases/$RUNNING_SHA/source.tar" ]] && echo RETAINED || echo PENDING)"
  say "CHECK=DONE (no change made)"
  exit 0

# --- 1. trusted object cache (authentic remote history, never fabricated) ---
if [[ ! -d "$OPIP_SOURCE_CACHE" ]]; then
  install -d -m 0700 "$(dirname "$OPIP_SOURCE_CACHE")"
  git clone --bare --quiet "$REPO_URL" "$OPIP_SOURCE_CACHE" \
    || die "cannot create the trusted object cache from $REPO_URL"
  chmod -R go-rwx "$OPIP_SOURCE_CACHE"
fi
git --git-dir="$OPIP_SOURCE_CACHE" remote set-url origin "$REPO_URL"
git --git-dir="$OPIP_SOURCE_CACHE" fetch --prune --no-tags origin main >/dev/null 2>&1 \
  || die "cannot fetch origin/main into the object cache"
REMOTE_MAIN="$(git --git-dir="$OPIP_SOURCE_CACHE" ls-remote origin refs/heads/main | awk 'NR==1{print $1}')"
[[ "$REMOTE_MAIN" =~ ^[0-9a-f]{40}$ ]] || die "cannot resolve origin/main"
say "origin_main=$REMOTE_MAIN"

# --- 2. running release becomes the verified baseline + rollback vehicle ---
OPIP_SOURCE_MODE="release-tree"
REPO_OWNER="root"
GIT=(git --git-dir="$OPIP_SOURCE_CACHE")
rt_snapshot_current "$RUNNING_SHA" || die "cannot retain the running release for rollback"
rt_write_state "$OPIP_CURRENT_RELEASE" "$RUNNING_SHA" "unverified-baseline" \
  "$(rt_manifest_digest "$STATE_DIR/releases/$RUNNING_SHA/manifest.sha256")" \
  "unverified-baseline"
say "baseline=RECORDED"
say "rollback_vehicle=RETAINED"

# --- 3. install the controller atomically, retaining the previous one -------
if [[ -f "$DEPLOY_SCRIPT_DST" ]]; then
  cp -a "$DEPLOY_SCRIPT_DST" "$PREVIOUS_CONTROLLER"
  chmod 0600 "$PREVIOUS_CONTROLLER"
  say "previous_controller=$PREVIOUS_CONTROLLER"
fi
bash -n "$APP_ROOT/$CONTROLLER_REL" || die "controller source fails syntax validation"
bash -n "$APP_ROOT/$LIB_REL" || die "release-tree library fails syntax validation"
staged="$(mktemp "$(dirname "$DEPLOY_SCRIPT_DST")/.ohm-deploy.stage.XXXXXX")"
cp -- "$APP_ROOT/$CONTROLLER_REL" "$staged"
chmod 0755 "$staged"
chown root:root "$staged"
mv -f -- "$staged" "$DEPLOY_SCRIPT_DST"
[[ -x "$DEPLOY_SCRIPT_DST" ]] || die "controller installation failed"

{
  printf 'status=SUCCESS\n'
  printf 'bootstrapped_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'running_release=%s\n' "$RUNNING_SHA"
  printf 'origin_main=%s\n' "$REMOTE_MAIN"
  printf 'controller_sha256=%s\n' "$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"
  printf 'previous_controller=%s\n' "$PREVIOUS_CONTROLLER"
} > "$RECEIPT"
chmod 0600 "$RECEIPT"

say "CONTROLLER_BOOTSTRAP=SUCCESS"
say "receipt=$RECEIPT"
say "next: owner-gated deployment through the existing workflow"

fi
