#!/usr/bin/env bash
# AC-025: one-time OWNER-OPERATED controller-only bootstrap.
#
# Purpose: atomically replace ONLY /usr/local/sbin/ohm-deploy with the exact
# controller from an explicitly qualified current-main SHA, so the NEXT
# deployment begins under an AC-024-capable controller and reaches the read-only
# protection preflight before any production runtime mutation.
#
# This is NOT a deployment. It never touches runtime services, containers, the
# scheduler, cron, the SSH gateway, sudoers, credentials, the application
# checkout, last-good-sha, the canonical writer, Feature Bus, or protection /
# incident / exchange state. It is intentionally NOT reachable through the
# forced-command SSH gateway and is never invoked by the deploy workflow.
#
# Usage:
#   sudo bash bootstrap-deploy-controller-only.sh <40-char-qualified-main-sha>
#
# Test seams: when OPIP_DEPLOY_TEST_SEAMS=1 only, the repository root, state
# directory, destination controller path and lock path may be overridden, and
# the root requirement is relaxed so the sandboxed tests can exercise the real
# script. In normal production mode those overrides are inert.
set -Eeuo pipefail

TARGET_SHA="${1:-}"
TEST_SEAMS="${OPIP_DEPLOY_TEST_SEAMS:-0}"

REPO_ROOT="/opt/OHM-Trade-Agent-v1"
STATE_DIR="/var/lib/ohm-deploy"
DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"
LOCK_FILE="/var/lock/ohm-deploy.lock"
CONTROLLER_REL="OHM-Trade-Agent-v1/deploy/remote/ohm-deploy"
BACKUP_FILE_NAME="controller-bootstrap-previous"
RECEIPT_FILE_NAME="controller-bootstrap-receipt.env"

# The AC-024 protection-preflight boundary this bootstrap exists to activate.
AC024_SIGNATURES=(
  'run_protection_preflight()'
  'OPIP_PROTECTION_PREFLIGHT='
  'OPIP_PROTECTION_PREFLIGHT_ABORT=REFUSED_BEFORE_MUTATION'
)

if [[ "$TEST_SEAMS" == "1" ]]; then
  [[ -n "${OPIP_BOOTSTRAP_REPO_ROOT:-}" ]] && REPO_ROOT="$OPIP_BOOTSTRAP_REPO_ROOT"
  [[ -n "${OPIP_BOOTSTRAP_STATE_DIR:-}" ]] && STATE_DIR="$OPIP_BOOTSTRAP_STATE_DIR"
  [[ -n "${OPIP_BOOTSTRAP_DEPLOY_DST:-}" ]] && DEPLOY_SCRIPT_DST="$OPIP_BOOTSTRAP_DEPLOY_DST"
  [[ -n "${OPIP_BOOTSTRAP_LOCK_FILE:-}" ]] && LOCK_FILE="$OPIP_BOOTSTRAP_LOCK_FILE"
fi
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
BACKUP_FILE="$STATE_DIR/$BACKUP_FILE_NAME"
RECEIPT_FILE="$STATE_DIR/$RECEIPT_FILE_NAME"

TARGET_SHA256="NONE"
PREVIOUS_SHA256="NONE"
INSTALLED_SHA256="NONE"
LIVE_HEAD_BEFORE=""
LIVE_HEAD_AFTER=""
BACKUP_WRITTEN=0
TMP_DIR=""

fail() {
  # fail <exit-code> <receipt-status> <message>
  local code="$1" status="$2"
  shift 2
  echo "bootstrap-deploy-controller-only: $*" >&2
  emit_receipt "$status"
  exit "$code"
}

emit_receipt() {
  local status="$1"
  [[ -d "$STATE_DIR" ]] || return 0
  local completed_at tmp
  completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  # Staged in the destination directory so the final rename is atomic and the
  # receipt never lands half-written.
  tmp="$STATE_DIR/.controller-bootstrap-receipt.$$"
  {
    echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=$status"
    echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
    echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256=$TARGET_SHA256"
    echo "OPIP_CONTROLLER_BOOTSTRAP_PREVIOUS_SHA256=$PREVIOUS_SHA256"
    echo "OPIP_CONTROLLER_BOOTSTRAP_INSTALLED_SHA256=$INSTALLED_SHA256"
    echo "OPIP_CONTROLLER_BOOTSTRAP_AC024=$([[ "$status" == "SUCCESS" || "$status" == "NOT_NEEDED" ]] && echo PROVEN || echo UNPROVEN)"
    echo "OPIP_CONTROLLER_BOOTSTRAP_COMPLETED_AT_UTC=$completed_at"
  } > "$tmp"
  chmod 0600 "$tmp"
  mv -f -- "$tmp" "$RECEIPT_FILE"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    chown root:root "$RECEIPT_FILE" 2>/dev/null || true
  fi
  chmod 0600 "$RECEIPT_FILE"
}

# --- 1. exact argument contract -------------------------------------------
if [[ "$#" -ne 1 || "$TARGET_SHA" == "" ]]; then
  echo "usage: bootstrap-deploy-controller-only.sh <40-char-lowercase-main-sha>" >&2
  exit 64
fi
if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "invalid target sha (must be 40 lowercase hex characters)" >&2
  exit 64
fi

# --- 2. root requirement ---------------------------------------------------
if [[ "$TEST_SEAMS" != "1" && "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this bootstrap with sudo" >&2
  exit 77
fi

# --- required tools (deliberately excludes docker/systemctl/cron) ----------
for cmd in git flock mktemp sha256sum cmp stat chmod mv cp mkdir bash; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

# --- 3. canonical deploy lock (shared with ohm-deploy) ---------------------
mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "another deployment or bootstrap already holds $LOCK_FILE" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED"
  echo "OPIP_CONTROLLER_BOOTSTRAP_REASON=LOCK_HELD"
  exit 75
fi

# --- 4. prove there is NO active deploy transaction ------------------------
shopt -s nullglob
ACTIVE_TRANSACTIONS=("$STATE_DIR"/scheduler-before.*)
if [[ "${#ACTIVE_TRANSACTIONS[@]}" -gt 0 ]]; then
  echo "active deploy transaction present: ${ACTIVE_TRANSACTIONS[*]}" >&2
  echo "operator recovery is required; refusing to mutate the controller" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED"
  echo "OPIP_CONTROLLER_BOOTSTRAP_REASON=ACTIVE_DEPLOY_TRANSACTION"
  exit 76
fi

# --- 5. repository owner + git identity -----------------------------------
if [[ ! -d "$REPO_ROOT/.git" ]]; then
  echo "repository not found at $REPO_ROOT" >&2
  exit 69
fi
REPO_OWNER="$(stat -c '%U' "$REPO_ROOT/.git")"
if [[ "$TEST_SEAMS" == "1" ]]; then
  GIT=(git -C "$REPO_ROOT")
else
  GIT=(sudo -u "$REPO_OWNER" git -C "$REPO_ROOT")
fi

TMP_DIR="$(mktemp -d)"
umask 077
trap '[[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf -- "$TMP_DIR"' EXIT

# --- 6/7. fetch and resolve origin/main -----------------------------------
"${GIT[@]}" fetch --prune origin main
REMOTE_MAIN_SHA="$("${GIT[@]}" rev-parse origin/main)"
if [[ "$TARGET_SHA" != "$REMOTE_MAIN_SHA" ]]; then
  echo "refusing: target is not current origin/main" >&2
  echo "target=$TARGET_SHA origin/main=$REMOTE_MAIN_SHA" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_REMOTE_MAIN=$REMOTE_MAIN_SHA"
  fail 65 FAILED "target SHA does not match origin/main"
fi

# --- 8. capture live HEAD (never mutated by this helper) ------------------
LIVE_HEAD_BEFORE="$("${GIT[@]}" rev-parse HEAD)"

# --- 9. materialize the target controller from the git OBJECT DATABASE ----
TARGET_CTRL="$TMP_DIR/ohm-deploy.target"
if ! "${GIT[@]}" show "$TARGET_SHA:$CONTROLLER_REL" > "$TARGET_CTRL" 2>/dev/null; then
  fail 69 FAILED "target controller is missing from $TARGET_SHA"
fi

# --- 10. validate BEFORE installation -------------------------------------
if [[ ! -s "$TARGET_CTRL" ]]; then
  fail 70 FAILED "extracted target controller is empty"
fi
if [[ ! -f "$TARGET_CTRL" || -L "$TARGET_CTRL" ]]; then
  fail 70 FAILED "extracted target controller is not a regular file"
fi
if ! bash -n "$TARGET_CTRL" 2>/dev/null; then
  fail 70 FAILED "target controller fails bash -n"
fi
for signature in "${AC024_SIGNATURES[@]}"; do
  if ! grep -Fq -- "$signature" "$TARGET_CTRL"; then
    fail 70 FAILED "target controller is missing AC-024 signature: $signature"
  fi
done

# --- 11. target hash -------------------------------------------------------
TARGET_SHA256="$(sha256sum "$TARGET_CTRL" | awk '{print $1}')"

# --- 12. idempotence -------------------------------------------------------
if [[ -f "$DEPLOY_SCRIPT_DST" ]]; then
  PREVIOUS_SHA256="$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"
fi
if [[ "$PREVIOUS_SHA256" == "$TARGET_SHA256" ]]; then
  INSTALLED_SHA256="$PREVIOUS_SHA256"
  emit_receipt "NOT_NEEDED"
  echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=NOT_NEEDED"
  echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
  echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256=$TARGET_SHA256"
  echo "OPIP_CONTROLLER_BOOTSTRAP_AC024=PROVEN"
  exit 0
fi

# --- 13. bounded backup of ONLY the current controller --------------------
if [[ "$PREVIOUS_SHA256" != "NONE" ]]; then
  cp -p -- "$DEPLOY_SCRIPT_DST" "$BACKUP_FILE"
  chmod 0600 "$BACKUP_FILE"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    chown root:root "$BACKUP_FILE" 2>/dev/null || true
  fi
  BACKUP_WRITTEN=1
fi

# --- 14. atomic install ----------------------------------------------------
atomic_install_controller() {
  # atomic_install_controller <source-file>
  local source="$1" dest_dir staged
  dest_dir="$(dirname "$DEPLOY_SCRIPT_DST")"
  if ! staged="$(mktemp "$dest_dir/.ohm-deploy.stage.XXXXXX")"; then
    return 1
  fi
  if ! cp -- "$source" "$staged"; then
    rm -f -- "$staged"
    return 1
  fi
  chmod 0755 "$staged" || {
    rm -f -- "$staged"
    return 1
  }
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    chown root:root "$staged" 2>/dev/null || true
  fi
  mv -f -- "$staged" "$DEPLOY_SCRIPT_DST"
}

restore_previous_controller() {
  # restore_previous_controller -> 0 when the prior state is proven restored
  if [[ "$BACKUP_WRITTEN" -eq 1 && -f "$BACKUP_FILE" ]]; then
    atomic_install_controller "$BACKUP_FILE"
    local restored
    restored="$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"
    if [[ "$restored" == "$PREVIOUS_SHA256" ]]; then
      return 0
    fi
    return 1
  fi
  # No previous controller existed: the only faithful restore is absence.
  rm -f -- "$DEPLOY_SCRIPT_DST"
  if [[ ! -e "$DEPLOY_SCRIPT_DST" ]]; then
    return 0
  fi
  return 1
}

# Fail closed BEFORE mutating if the live checkout already moved.
LIVE_HEAD_PRE_INSTALL="$("${GIT[@]}" rev-parse HEAD)"
if [[ "$LIVE_HEAD_PRE_INSTALL" != "$LIVE_HEAD_BEFORE" ]]; then
  fail 72 FAILED "live repository HEAD changed before installation"
fi

if ! atomic_install_controller "$TARGET_CTRL"; then
  # Nothing usable was installed; the destination is either untouched or, in the
  # worst case, already staged away. Restore is idempotent and reported.
  restore_previous_controller >/dev/null 2>&1 || true
  fail 71 FAILED "controller installation failed"
fi

# --- 15. post-install verification ----------------------------------------
verify_installed_controller() {
  [[ -f "$DEPLOY_SCRIPT_DST" && ! -L "$DEPLOY_SCRIPT_DST" ]] || return 1
  cmp -s -- "$DEPLOY_SCRIPT_DST" "$TARGET_CTRL" || return 1
  local installed
  installed="$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"
  [[ "$installed" == "$TARGET_SHA256" ]] || return 1
  bash -n "$DEPLOY_SCRIPT_DST" 2>/dev/null || return 1
  local signature
  for signature in "${AC024_SIGNATURES[@]}"; do
    grep -Fq -- "$signature" "$DEPLOY_SCRIPT_DST" || return 1
  done
  [[ "$(stat -c '%a' "$DEPLOY_SCRIPT_DST")" == "755" ]] || return 1
  return 0
}

# --- 17. live HEAD must be identical (checked with the post-install proof) -
LIVE_HEAD_AFTER="$("${GIT[@]}" rev-parse HEAD)"

if ! verify_installed_controller || [[ "$LIVE_HEAD_AFTER" != "$LIVE_HEAD_BEFORE" ]]; then
  echo "post-install verification failed; restoring the previous controller" >&2
  if restore_previous_controller; then
    echo "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=VERIFIED" >&2
  else
    echo "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=UNPROVEN" >&2
  fi
  fail 71 FAILED "post-install verification failed"
fi

INSTALLED_SHA256="$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"

# --- 18. receipt -----------------------------------------------------------
emit_receipt "SUCCESS"

echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=SUCCESS"
echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256=$TARGET_SHA256"
echo "OPIP_CONTROLLER_BOOTSTRAP_PREVIOUS_SHA256=$PREVIOUS_SHA256"
echo "OPIP_CONTROLLER_BOOTSTRAP_INSTALLED_SHA256=$INSTALLED_SHA256"
echo "OPIP_CONTROLLER_BOOTSTRAP_AC024=PROVEN"
echo "OPIP_CONTROLLER_BOOTSTRAP_RECEIPT=$RECEIPT_FILE"
echo "OPIP_CONTROLLER_BOOTSTRAP_LIVE_HEAD=$LIVE_HEAD_AFTER"
exit 0
