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
# script. OPIP_BOOTSTRAP_PRODUCTION_MODE=1 additionally forces the production
# ownership requirement inside that sandbox. In normal production mode every
# override is inert.
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

#: Production ownership (root:root) and the root requirement are mandatory
#: outside the test seam. The seam forces them off so a deliberately non-root
#: sandbox can exercise the control flow, and OPIP_BOOTSTRAP_PRODUCTION_MODE=1
#: re-enables them inside the seam so the production requirement itself is
#: testable.
PRODUCTION_MODE="1"

# The AC-024 protection-preflight boundary this bootstrap exists to activate.
AC024_SIGNATURES=(
  'run_protection_preflight()'
  'OPIP_PROTECTION_PREFLIGHT='
  'OPIP_PROTECTION_PREFLIGHT_ABORT=REFUSED_BEFORE_MUTATION'
)

if [[ "$TEST_SEAMS" == "1" ]]; then
  PRODUCTION_MODE="0"
  [[ "${OPIP_BOOTSTRAP_PRODUCTION_MODE:-0}" == "1" ]] && PRODUCTION_MODE="1"
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
  #
  # Failure markers go to stderr so a refused run can never be mistaken for a
  # successful one on stdout, and are echoed here so the console shows the same
  # bounded status the receipt records.
  local code="$1" status="$2"
  shift 2
  echo "bootstrap-deploy-controller-only: $*" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=$status" >&2
  if ! emit_receipt "$status"; then
    echo "OPIP_CONTROLLER_BOOTSTRAP_RECEIPT_OWNERSHIP=UNPROVEN" >&2
  fi
  exit "$code"
}

emit_receipt() {
  # emit_receipt <status> -> 0 only when the bounded receipt is written with the
  # required ownership. In production the receipt must end root:root mode 0600,
  # so a chown/stat failure is reported rather than silently accepted.
  local status="$1"
  [[ -d "$STATE_DIR" ]] || return 1
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
  } > "$tmp" || return 1
  chmod 0600 "$tmp" || {
    rm -f -- "$tmp"
    return 1
  }
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    chown root:root -- "$tmp" 2>/dev/null || {
      rm -f -- "$tmp"
      return 1
    }
  fi
  mv -f -- "$tmp" "$RECEIPT_FILE" || return 1
  chmod 0600 "$RECEIPT_FILE" || return 1
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    [[ "$(stat -c '%U:%G' "$RECEIPT_FILE")" == "root:root" ]] || return 1
  fi
  return 0
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
# The root requirement follows the test seam (a sandbox is deliberately
# non-root); the ownership requirement follows PRODUCTION_MODE so it stays
# mandatory in production and remains testable inside the seam.
if [[ "$TEST_SEAMS" != "1" && "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this bootstrap with sudo" >&2
  exit 77
fi

# --- required tools (deliberately excludes docker/systemctl/cron) ----------
# Every external command the implementation can reach, including the ones used
# only by a later branch, so a missing tool is a clear pre-mutation refusal
# rather than an unstructured command-not-found exit.
REQUIRED_TOOLS=(git flock mktemp sha256sum awk cmp stat chmod mv cp mkdir rm dirname grep date id bash)
if [[ "$TEST_SEAMS" != "1" ]]; then
  # Production executes git as the repository owner and owns its artifacts.
  REQUIRED_TOOLS+=(sudo chown)
fi
for cmd in "${REQUIRED_TOOLS[@]}"; do
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
  echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_REASON=LOCK_HELD" >&2
  exit 75
fi

# --- 4. prove there is NO active deploy transaction ------------------------
shopt -s nullglob
ACTIVE_TRANSACTIONS=("$STATE_DIR"/scheduler-before.*)
shopt -u nullglob
if [[ "${#ACTIVE_TRANSACTIONS[@]}" -gt 0 ]]; then
  echo "active deploy transaction present: ${ACTIVE_TRANSACTIONS[*]}" >&2
  echo "operator recovery is required; refusing to mutate the controller" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_REASON=ACTIVE_DEPLOY_TRANSACTION" >&2
  exit 76
fi

# --- 5. repository owner + git identity -----------------------------------
if [[ ! -d "$REPO_ROOT/.git" ]]; then
  echo "repository not found at $REPO_ROOT" >&2
  exit 69
fi
REPO_OWNER="$(stat -c '%U' "$REPO_ROOT/.git")"
if [[ "$TEST_SEAMS" == "1" ]]; then
  # Sandbox: run git directly. Production runs git as the repository owner so
  # the checkout is never touched as root.
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
  echo "OPIP_CONTROLLER_BOOTSTRAP_REMOTE_MAIN=$REMOTE_MAIN_SHA" >&2
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

# --- 12. ONE shared installed-controller invariant ------------------------
# Used by BOTH the idempotence decision and post-install verification so the two
# definitions can never drift. ``$1`` is the expected controller bytes.
controller_metadata_ok() {
  # controller_metadata_ok <path>
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] || return 1
  [[ "$(stat -c '%a' "$path")" == "755" ]] || return 1
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    [[ "$(stat -c '%U:%G' "$path")" == "root:root" ]] || return 1
  fi
  return 0
}

verify_installed_controller() {
  # verify_installed_controller <expected-controller-file>
  local expected="$1" signature
  [[ -f "$DEPLOY_SCRIPT_DST" && ! -L "$DEPLOY_SCRIPT_DST" ]] || return 1
  cmp -s -- "$DEPLOY_SCRIPT_DST" "$expected" || return 1
  [[ "$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')" == "$TARGET_SHA256" ]] || return 1
  bash -n "$DEPLOY_SCRIPT_DST" 2>/dev/null || return 1
  for signature in "${AC024_SIGNATURES[@]}"; do
    grep -Fq -- "$signature" "$DEPLOY_SCRIPT_DST" || return 1
  done
  controller_metadata_ok "$DEPLOY_SCRIPT_DST"
}

# --- 13. idempotence -------------------------------------------------------
# NOT_NEEDED is allowed ONLY when the installed controller already satisfies the
# COMPLETE invariant above. Matching bytes with wrong type, mode or (in
# production) owner/group must NOT short-circuit: it falls through to the
# correction path below.
if [[ -f "$DEPLOY_SCRIPT_DST" ]]; then
  PREVIOUS_SHA256="$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"
fi
if [[ "$PREVIOUS_SHA256" == "$TARGET_SHA256" ]] && verify_installed_controller "$TARGET_CTRL"; then
  # NOT_NEEDED still proves the live checkout was not moved for this run.
  LIVE_HEAD_AFTER="$("${GIT[@]}" rev-parse HEAD)"
  if [[ "$LIVE_HEAD_AFTER" != "$LIVE_HEAD_BEFORE" ]]; then
    fail 72 FAILED "live repository HEAD changed before NOT_NEEDED completion"
  fi
  INSTALLED_SHA256="$TARGET_SHA256"
  if ! emit_receipt "NOT_NEEDED"; then
    fail 71 FAILED "controller receipt ownership could not be proven"
  fi
  echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=NOT_NEEDED"
  echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
  echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256=$TARGET_SHA256"
  echo "OPIP_CONTROLLER_BOOTSTRAP_AC024=PROVEN"
  echo "OPIP_CONTROLLER_BOOTSTRAP_LIVE_HEAD=$LIVE_HEAD_AFTER"
  exit 0
fi

# --- 14. bounded backup of ONLY the current controller --------------------
if [[ "$PREVIOUS_SHA256" != "NONE" ]]; then
  if ! cp -p -- "$DEPLOY_SCRIPT_DST" "$BACKUP_FILE"; then
    fail 71 FAILED "controller backup failed"
  fi
  # Ownership of the backup is claimed from the moment it exists, so a later
  # failure can always attempt a faithful restore.
  BACKUP_WRITTEN=1
  chmod 0600 "$BACKUP_FILE" || fail 71 FAILED "controller backup mode could not be set"
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    if ! chown root:root -- "$BACKUP_FILE" 2>/dev/null; then
      fail 71 FAILED "controller backup could not be owned by root"
    fi
    [[ "$(stat -c '%U:%G' "$BACKUP_FILE")" == "root:root" ]] \
      || fail 71 FAILED "controller backup ownership is not root:root"
  fi
fi

# --- 15. atomic install ----------------------------------------------------
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
  if ! chmod 0755 "$staged"; then
    rm -f -- "$staged"
    return 1
  fi
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    # Production ownership is a requirement, not a best effort. A missing or
    # failed chown fails the installation so the caller restores.
    if ! chown root:root -- "$staged" 2>/dev/null; then
      rm -f -- "$staged"
      return 1
    fi
  fi
  # rename(2) replaces the destination entry atomically, including a symlink,
  # so the installed path is always a regular file.
  mv -f -- "$staged" "$DEPLOY_SCRIPT_DST"
}

restore_previous_controller() {
  # restore_previous_controller -> 0 only when the prior state is PROVEN restored
  if [[ "$BACKUP_WRITTEN" -eq 1 && -f "$BACKUP_FILE" ]]; then
    atomic_install_controller "$BACKUP_FILE" || return 1
    local restored
    restored="$(sha256sum "$DEPLOY_SCRIPT_DST" 2>/dev/null | awk '{print $1}')"
    [[ "$restored" == "$PREVIOUS_SHA256" ]] || return 1
    return 0
  fi
  # No previous controller existed: the only faithful restore is absence.
  rm -f -- "$DEPLOY_SCRIPT_DST"
  [[ ! -e "$DEPLOY_SCRIPT_DST" ]] || return 1
  return 0
}

report_restore_result() {
  # Every post-install failure path MUST surface the restoration verdict.
  if restore_previous_controller; then
    echo "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=VERIFIED" >&2
    return 0
  fi
  echo "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=UNPROVEN" >&2
  echo "OPIP_CONTROLLER_BOOTSTRAP_OPERATOR_ACTION=CONTROLLER_RESTORE_UNPROVEN" >&2
  return 1
}

# Fail closed BEFORE mutating if the live checkout already moved.
LIVE_HEAD_PRE_INSTALL="$("${GIT[@]}" rev-parse HEAD)"
if [[ "$LIVE_HEAD_PRE_INSTALL" != "$LIVE_HEAD_BEFORE" ]]; then
  fail 72 FAILED "live repository HEAD changed before installation"
fi

if ! atomic_install_controller "$TARGET_CTRL"; then
  # Installation may have begun, so the restore verdict is mandatory here.
  report_restore_result || true
  fail 71 FAILED "controller installation failed"
fi

# --- 16. post-install verification ----------------------------------------
LIVE_HEAD_AFTER="$("${GIT[@]}" rev-parse HEAD)"

if ! verify_installed_controller "$TARGET_CTRL" || [[ "$LIVE_HEAD_AFTER" != "$LIVE_HEAD_BEFORE" ]]; then
  echo "post-install verification failed; restoring the previous controller" >&2
  report_restore_result || true
  fail 71 FAILED "post-install verification failed"
fi

INSTALLED_SHA256="$(sha256sum "$DEPLOY_SCRIPT_DST" | awk '{print $1}')"

# --- 17. receipt -----------------------------------------------------------
if ! emit_receipt "SUCCESS"; then
  echo "controller receipt ownership could not be proven" >&2
  report_restore_result || true
  fail 71 FAILED "controller receipt ownership could not be proven"
fi

echo "OPIP_CONTROLLER_BOOTSTRAP_STATUS=SUCCESS"
echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
echo "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256=$TARGET_SHA256"
echo "OPIP_CONTROLLER_BOOTSTRAP_PREVIOUS_SHA256=$PREVIOUS_SHA256"
echo "OPIP_CONTROLLER_BOOTSTRAP_INSTALLED_SHA256=$INSTALLED_SHA256"
echo "OPIP_CONTROLLER_BOOTSTRAP_AC024=PROVEN"
echo "OPIP_CONTROLLER_BOOTSTRAP_RECEIPT=$RECEIPT_FILE"
echo "OPIP_CONTROLLER_BOOTSTRAP_LIVE_HEAD=$LIVE_HEAD_AFTER"
exit 0
