#!/usr/bin/env bash
# O'Pip - one-time OWNER-OPERATED first-use bootstrap of the complete release
# controller set that the owner-gated UI-ONLY edge release depends on.
#
# Why this exists
# ---------------
# `deploy-ui <40-hex>` needs four installed, root-owned components:
#
#   /usr/local/sbin/ohm-deploy                      the release controller
#   /usr/local/sbin/ohm-deploy-ssh                  the forced-command gateway
#   /usr/local/lib/opip/opip_release_tree.sh        the verified tree provider
#   /usr/local/lib/opip/opip_ui_release.sh          the UI-only release library
#
# The previously deployed production release predates all four corrections, so
# nothing it carries can install them; `bootstrap-release-tree-controller.sh`
# installs only the first two and `bootstrap-deploy-controller-only.sh` installs
# only the controller. On a host in that state the gateway never recognises
# `deploy-ui`, and the controller refuses with "opip_ui_release.sh is not
# installed". This bootstrap closes that first-use gap and nothing else.
#
# It is NOT a deployment. It does not start, stop or recreate a container, does
# not run Compose, does not touch cron/the scheduler/sudoers/authorized_keys, does
# not write `.env`/`.env.core`, does not move the application checkout's HEAD and
# does not read or write production data or trading state. It is intentionally
# NOT reachable through the forced-command SSH gateway and is never invoked by a
# workflow.
#
# The forced-command boundary is preserved rather than rewritten: the deploy key
# already authorises exactly `restrict,command="/usr/local/sbin/ohm-deploy-ssh"`,
# so replacing the gateway at that stable path extends the reachable allowlist
# without editing authorized_keys at all. The wiring is verified read-only.
#
# Usage (root, from the droplet console or an owner SSH session):
#   sudo -n bash bootstrap-ui-release-controller.sh --check
#   sudo -n bash bootstrap-ui-release-controller.sh --apply [--sha <40-hex>]
#
# `--sha` defaults to the origin/main the trusted checkout can prove, and is only
# accepted when it equals that origin/main.
#
# Test seams: with OPIP_UI_BOOTSTRAP_TEST_SEAMS=1 only, the repository root, state
# directory, sbin/lib destinations and the gateway wiring file may be overridden
# and the root requirement is relaxed so the sandboxed tests drive this real
# script. OPIP_UI_BOOTSTRAP_PRODUCTION_MODE=1 re-enables the production ownership
# requirement inside the seam so that requirement stays testable. In production
# every override is inert.
set -Eeuo pipefail

MODE="check"
TARGET_SHA=""
TEST_SEAMS="${OPIP_UI_BOOTSTRAP_TEST_SEAMS:-0}"
PRODUCTION_MODE="1"

SOURCE_CACHE="/srv/opip-v2/cache/repo.git"
EXPECTED_REMOTE="https://github.com/Opr1130/OHM-Trade-Agent-v1.git"
STATE_DIR="/var/lib/ohm-deploy"
SBIN_DIR="/usr/local/sbin"
LIB_DIR="/usr/local/lib/opip"
LOCK_FILE="/var/lock/ohm-deploy.lock"
BACKUP_DIR_NAME="ui-controller-bootstrap-previous"
RECEIPT_FILE_NAME="ui-controller-bootstrap-receipt.env"
GATEWAY_WIRING_FILE="/home/ohmdeploy/.ssh/authorized_keys"
GATEWAY_WIRING_EXPECTED='command="/usr/local/sbin/ohm-deploy-ssh"'

if [[ "$TEST_SEAMS" == "1" ]]; then
  PRODUCTION_MODE="0"
  [[ "${OPIP_UI_BOOTSTRAP_PRODUCTION_MODE:-0}" == "1" ]] && PRODUCTION_MODE="1"
  [[ -n "${OPIP_UI_BOOTSTRAP_SOURCE_CACHE:-}" ]] && SOURCE_CACHE="$OPIP_UI_BOOTSTRAP_SOURCE_CACHE"
  [[ -n "${OPIP_UI_BOOTSTRAP_STATE_DIR:-}" ]] && STATE_DIR="$OPIP_UI_BOOTSTRAP_STATE_DIR"
  [[ -n "${OPIP_UI_BOOTSTRAP_SBIN_DIR:-}" ]] && SBIN_DIR="$OPIP_UI_BOOTSTRAP_SBIN_DIR"
  [[ -n "${OPIP_UI_BOOTSTRAP_LIB_DIR:-}" ]] && LIB_DIR="$OPIP_UI_BOOTSTRAP_LIB_DIR"
  [[ -n "${OPIP_UI_BOOTSTRAP_GATEWAY_WIRING_FILE:-}" ]] && GATEWAY_WIRING_FILE="$OPIP_UI_BOOTSTRAP_GATEWAY_WIRING_FILE"
  [[ -n "${OPIP_UI_BOOTSTRAP_LOCK_FILE:-}" ]] && LOCK_FILE="$OPIP_UI_BOOTSTRAP_LOCK_FILE"
fi

BACKUP_DIR="$STATE_DIR/$BACKUP_DIR_NAME"
RECEIPT_FILE="$STATE_DIR/$RECEIPT_FILE_NAME"

# The four components, as one ordered table. `rel` is repository-relative, `dest`
# is the stable installed location, `mode` the required file mode and `sig` a
# string the installed bytes must contain - so "installed" means "carries the
# behaviour this bootstrap exists to make reachable", never merely "a file is
# there".
COMPONENT_REL=(
  "OHM-Trade-Agent-v1/deploy/remote/ohm-deploy"
  "OHM-Trade-Agent-v1/deploy/remote/ohm-deploy-ssh"
  "OHM-Trade-Agent-v1/deploy/remote/lib/opip_release_tree.sh"
  "OHM-Trade-Agent-v1/deploy/remote/lib/opip_ui_release.sh"
)
COMPONENT_NAME=(
  "ohm-deploy"
  "ohm-deploy-ssh"
  "opip_release_tree.sh"
  "opip_ui_release.sh"
)
COMPONENT_MODE=(0755 0755 0644 0644)
COMPONENT_SIG=(
  "OPIP_UI_STABLE_LIB"
  "deploy-ui"
  "opip_source_init"
  "opip_ui_run"
)
COMPONENT_DEST=()

TMP_DIR=""
TARGET_SHA256=()
PREVIOUS_SHA256=()
BACKUP_WRITTEN=()

say() { printf '%s\n' "$*"; }

die() {
  # die <exit-code> <receipt-status> <message>
  # Failure markers go to stderr so a refused or failed run can never be mistaken
  # for a successful one on stdout.
  local code="$1" status="$2"
  shift 2
  echo "bootstrap-ui-release-controller: $*" >&2
  echo "OPIP_UI_BOOTSTRAP_STATUS=$status" >&2
  if ! emit_receipt "$status"; then
    echo "OPIP_UI_BOOTSTRAP_RECEIPT_OWNERSHIP=UNPROVEN" >&2
  fi
  exit "$code"
}

emit_receipt() {
  # emit_receipt <status> -> 0 only when the bounded receipt is written with the
  # ownership the production contract requires.
  local status="$1" tmp i
  [[ -d "$STATE_DIR" ]] || return 1
  tmp="$STATE_DIR/.ui-controller-bootstrap-receipt.$$"
  {
    echo "schema=1"
    echo "OPIP_UI_BOOTSTRAP_STATUS=$status"
    echo "OPIP_UI_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
    echo "OPIP_UI_BOOTSTRAP_ORIGIN_MAIN=${REMOTE_MAIN_SHA:-UNRESOLVED}"
    echo "OPIP_UI_BOOTSTRAP_PRODUCTION_MODE=$PRODUCTION_MODE"
    for i in "${!COMPONENT_NAME[@]}"; do
      echo "OPIP_UI_BOOTSTRAP_COMPONENT_${COMPONENT_NAME[$i]}=${TARGET_SHA256[$i]:-NONE}"
      echo "OPIP_UI_BOOTSTRAP_PREVIOUS_${COMPONENT_NAME[$i]}=${PREVIOUS_SHA256[$i]:-NONE}"
      echo "OPIP_UI_BOOTSTRAP_DEST_${COMPONENT_NAME[$i]}=${COMPONENT_DEST[$i]:-NONE}"
    done
    echo "OPIP_UI_BOOTSTRAP_GATEWAY_WIRING=${GATEWAY_WIRING_STATE:-UNVERIFIED}"
    echo "OPIP_UI_BOOTSTRAP_COMPLETED_AT_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
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

component_metadata_ok() {
  # component_metadata_ok <path> <mode>
  local path="$1" mode="$2"
  [[ -f "$path" && ! -L "$path" ]] || return 1
  [[ "$(stat -c '%a' "$path")" == "${mode#0}" ]] || return 1
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    [[ "$(stat -c '%U:%G' "$path")" == "root:root" ]] || return 1
  fi
  return 0
}

component_valid() {
  # component_valid <index> <path> -> bytes, mode, ownership and behaviour.
  local index="$1" path="$2"
  component_metadata_ok "$path" "${COMPONENT_MODE[$index]}" || return 1
  grep -Fq -- "${COMPONENT_SIG[$index]}" "$path" || return 1
  case "${COMPONENT_MODE[$index]}" in
    0755) [[ -x "$path" ]] || return 1 ;;
  esac
  return 0
}

verify_targets() {
  # verify_targets -> every materialized target satisfies the full invariant.
  local index
  for index in "${!COMPONENT_NAME[@]}"; do
    grep -Fq -- "${COMPONENT_SIG[$index]}" "$TMP_DIR/${COMPONENT_NAME[$index]}" || return 1
    cmp -s -- "$TMP_DIR/${COMPONENT_NAME[$index]}" "${COMPONENT_DEST[$index]}" || return 1
    [[ "$(sha256sum "${COMPONENT_DEST[$index]}" | awk '{print $1}')" == "${TARGET_SHA256[$index]}" ]] || return 1
    bash -n "${COMPONENT_DEST[$index]}" 2>/dev/null || return 1
    component_valid "$index" "${COMPONENT_DEST[$index]}" || return 1
  done
  return 0
}

atomic_install_component() {
  # atomic_install_component <index> <source-file>
  local index="$1" source="$2" dest="${COMPONENT_DEST[$1]}" dest_dir staged
  dest_dir="$(dirname "$dest")"
  install -d -m 0755 "$dest_dir" || return 1
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    chown root:root -- "$dest_dir" 2>/dev/null || true
  fi
  staged="$(mktemp "$dest_dir/.opip-ui-bootstrap.XXXXXX")" || return 1
  if ! cp -- "$source" "$staged"; then
    rm -f -- "$staged"
    return 1
  fi
  if ! chmod "${COMPONENT_MODE[$index]}" "$staged"; then
    rm -f -- "$staged"
    return 1
  fi
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    if ! chown root:root -- "$staged" 2>/dev/null; then
      rm -f -- "$staged"
      return 1
    fi
  fi
  mv -f -- "$staged" "$dest"
}

restore_previous_components() {
  # Every failure after the first backup attempt MUST report a restoration
  # verdict. A component that did not exist before is restored to absence, which
  # is the only faithful state in that case.
  local index restored
  for index in "${!COMPONENT_NAME[@]}"; do
    if [[ "${BACKUP_WRITTEN[$index]:-0}" -eq 1 && -f "$BACKUP_DIR/${COMPONENT_NAME[$index]}" ]]; then
      atomic_install_component "$index" "$BACKUP_DIR/${COMPONENT_NAME[$index]}" || return 1
      restored="$(sha256sum "${COMPONENT_DEST[$index]}" 2>/dev/null | awk '{print $1}')"
      [[ "$restored" == "${PREVIOUS_SHA256[$index]}" ]] || return 1
    else
      rm -f -- "${COMPONENT_DEST[$index]}"
      [[ ! -e "${COMPONENT_DEST[$index]}" ]] || return 1
    fi
  done
  return 0
}

report_restore_result() {
  if restore_previous_components; then
    echo "OPIP_UI_BOOTSTRAP_RESTORE=VERIFIED" >&2
    return 0
  fi
  echo "OPIP_UI_BOOTSTRAP_RESTORE=UNPROVEN" >&2
  echo "OPIP_UI_BOOTSTRAP_OPERATOR_ACTION=CONTROLLER_RESTORE_UNPROVEN" >&2
  return 1
}

# --- 1. argument contract --------------------------------------------------
while (($#)); do
  case "$1" in
    --check) MODE="check" ;;
    --apply) MODE="apply" ;;
    --sha)
      TARGET_SHA="${2:-}"
      shift || true
      ;;
    *)
      echo "usage: $0 [--check|--apply] [--sha <40-hex>]" >&2
      exit 64
      ;;
  esac
  shift
done
if [[ -n "$TARGET_SHA" && ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "invalid --sha (must be 40 lowercase hex characters)" >&2
  exit 64
fi

# --- 2. root requirement ---------------------------------------------------
# The root requirement follows the test seam; the ownership requirement follows
# PRODUCTION_MODE so it stays mandatory in production and stays testable.
if [[ "$TEST_SEAMS" != "1" && "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this bootstrap with sudo" >&2
  exit 77
fi

# --- 3. required tools (deliberately excludes docker/systemctl/cron) -------
REQUIRED_TOOLS=(git flock mktemp sha256sum awk cmp stat chmod mv cp mkdir rm
  dirname grep date id bash install cat)
if [[ "$TEST_SEAMS" != "1" ]]; then
  REQUIRED_TOOLS+=(sudo chown)
fi
for cmd in "${REQUIRED_TOOLS[@]}"; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

for index in "${!COMPONENT_NAME[@]}"; do
  case "$index" in
    0|1) COMPONENT_DEST+=("$SBIN_DIR/${COMPONENT_NAME[$index]}") ;;
    *) COMPONENT_DEST+=("$LIB_DIR/${COMPONENT_NAME[$index]}") ;;
  esac
  TARGET_SHA256+=("NONE")
  PREVIOUS_SHA256+=("NONE")
  BACKUP_WRITTEN+=(0)
done

# --- 4. canonical deploy lock (shared with ohm-deploy) ---------------------
mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "another deployment or bootstrap already holds $LOCK_FILE" >&2
  echo "OPIP_UI_BOOTSTRAP_STATUS=FAILED" >&2
  echo "OPIP_UI_BOOTSTRAP_REASON=LOCK_HELD" >&2
  exit 75
fi

# --- 5. no active deploy transaction ---------------------------------------
shopt -s nullglob
ACTIVE_TRANSACTIONS=("$STATE_DIR"/scheduler-before.*)
shopt -u nullglob
if [[ "${#ACTIVE_TRANSACTIONS[@]}" -gt 0 ]]; then
  echo "active deploy transaction present: ${ACTIVE_TRANSACTIONS[*]}" >&2
  echo "operator recovery is required; refusing to mutate the controller set" >&2
  echo "OPIP_UI_BOOTSTRAP_STATUS=FAILED" >&2
  echo "OPIP_UI_BOOTSTRAP_REASON=ACTIVE_DEPLOY_TRANSACTION" >&2
  exit 76
fi

# --- 6. trusted release-tree object cache (no live checkout .git required) --
# The host is a manifest-installed release tree, not necessarily a Git
# worktree. The existing controller uses this bare cache for source provenance.
if [[ ! -d "$SOURCE_CACHE/objects" || ! -f "$SOURCE_CACHE/HEAD" ]]; then
  echo "trusted release object cache missing: $SOURCE_CACHE" >&2
  exit 69
fi
GIT=(git --git-dir="$SOURCE_CACHE")
REMOTE_URL="$("${GIT[@]}" remote get-url origin 2>/dev/null || true)"
if [[ "$PRODUCTION_MODE" == "1" && "$REMOTE_URL" != "$EXPECTED_REMOTE" ]]; then
  echo "untrusted origin remote: expected $EXPECTED_REMOTE" >&2
  exit 69
fi
[[ -n "$REMOTE_URL" ]] || {
  echo "trusted cache has no origin remote" >&2
  exit 69
}

TMP_DIR="$(mktemp -d)"
umask 077
trap '[[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf -- "$TMP_DIR"' EXIT

# --- 7. fetch and resolve the exact current origin/main ---------------------
"${GIT[@]}" fetch --prune --no-tags origin main >/dev/null 2>&1 || {
  echo "cannot fetch origin/main into the trusted release cache" >&2
  exit 69
}
REMOTE_MAIN_SHA="$("${GIT[@]}" ls-remote origin refs/heads/main | awk 'NR==1 { print $1 }')"
[[ "$REMOTE_MAIN_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "cannot resolve origin/main" >&2
  exit 69
}
"${GIT[@]}" cat-file -e "${REMOTE_MAIN_SHA}^{commit}" || {
  echo "origin/main commit not present in the verified cache" >&2
  exit 69
}
[[ -n "$TARGET_SHA" ]] || TARGET_SHA="$REMOTE_MAIN_SHA"
if [[ "$TARGET_SHA" != "$REMOTE_MAIN_SHA" ]]; then
  echo "refusing: target is not current origin/main" >&2
  echo "target=$TARGET_SHA origin/main=$REMOTE_MAIN_SHA" >&2
  echo "OPIP_UI_BOOTSTRAP_REMOTE_MAIN=$REMOTE_MAIN_SHA" >&2
  die 65 FAILED "target SHA does not match origin/main"
fi
# The bootstrap has no reason to mutate the currently active core release.
# Monitor its canonical installed-release record rather than assuming a
# nonexistent live .git worktree.
live_record_digest() {
  if [[ -f "$STATE_DIR/current-release.env" ]]; then
    sha256sum "$STATE_DIR/current-release.env" | awk '{print $1}'
  else
    printf 'ABSENT\\n'
  fi
}
LIVE_RECORD_BEFORE="$(live_record_digest)"

# --- 8. forced-command wiring is verified, never rewritten ----------------
GATEWAY_WIRING_STATE="UNVERIFIED"
if [[ -f "$GATEWAY_WIRING_FILE" ]]; then
  if grep -Fq -- "$GATEWAY_WIRING_EXPECTED" "$GATEWAY_WIRING_FILE"; then
    GATEWAY_WIRING_STATE="OK"
  else
    GATEWAY_WIRING_STATE="MISMATCH"
    echo "the deploy key is not wired to $GATEWAY_WIRING_EXPECTED" >&2
    echo "an extended gateway installed there would not be reachable; refusing" >&2
    die 71 FAILED "forced-command wiring does not target the stable gateway path"
  fi
else
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    die 71 FAILED "forced-command wiring file is missing"
  fi
fi

# --- 9. materialize all four components FROM THE COMMIT -------------------
for index in "${!COMPONENT_NAME[@]}"; do
  if ! "${GIT[@]}" show "$TARGET_SHA:${COMPONENT_REL[$index]}" > "$TMP_DIR/${COMPONENT_NAME[$index]}" 2>/dev/null; then
    die 69 FAILED "${COMPONENT_REL[$index]} is missing from $TARGET_SHA"
  fi
done

# --- 10. validate BEFORE installation ------------------------------------
for index in "${!COMPONENT_NAME[@]}"; do
  staged="$TMP_DIR/${COMPONENT_NAME[$index]}"
  [[ -s "$staged" ]] || die 70 FAILED "extracted ${COMPONENT_REL[$index]} is empty"
  [[ -f "$staged" && ! -L "$staged" ]] \
    || die 70 FAILED "extracted ${COMPONENT_REL[$index]} is not a regular file"
  bash -n "$staged" 2>/dev/null || die 70 FAILED "${COMPONENT_REL[$index]} fails bash -n"
  grep -Fq -- "${COMPONENT_SIG[$index]}" "$staged" \
    || die 70 FAILED "${COMPONENT_REL[$index]} is missing required signature: ${COMPONENT_SIG[$index]}"
  TARGET_SHA256[$index]="$(sha256sum "$staged" | awk '{print $1}')"
done

# --- 11. --check makes no change -----------------------------------------
if [[ "$MODE" == "check" ]]; then
  say "== O'Pip UI release controller bootstrap =="
  say "mode=check target_sha=$TARGET_SHA origin_main=$REMOTE_MAIN_SHA"
  say "gateway_wiring=$GATEWAY_WIRING_STATE"
  all_present=1
  for index in "${!COMPONENT_NAME[@]}"; do
    if component_valid "$index" "${COMPONENT_DEST[$index]}"; then
      state="PRESENT"
    elif [[ -e "${COMPONENT_DEST[$index]}" ]]; then
      state="PRESENT_STALE"
      all_present=0
    else
      state="ABSENT"
      all_present=0
    fi
    say "component_${COMPONENT_NAME[$index]}=$state dest=${COMPONENT_DEST[$index]}"
  done
  if [[ "$all_present" -eq 1 ]]; then
    say "CHECK=DONE (all components installed; --apply would report NOT_NEEDED)"
  else
    say "CHECK=DONE (no change made)"
  fi
  exit 0
fi

# --- 12. idempotence ------------------------------------------------------
# NOT_NEEDED is allowed ONLY when every component already satisfies the COMPLETE
# invariant (bytes, mode, ownership and behaviour). Matching bytes with a wrong
# mode or owner must NOT short-circuit: it falls through to the correction path.
installed=1
for index in "${!COMPONENT_NAME[@]}"; do
  if [[ -f "${COMPONENT_DEST[$index]}" ]]; then
    PREVIOUS_SHA256[$index]="$(sha256sum "${COMPONENT_DEST[$index]}" | awk '{print $1}')"
  fi
  if [[ "${PREVIOUS_SHA256[$index]}" != "${TARGET_SHA256[$index]}" ]] \
    || ! component_valid "$index" "${COMPONENT_DEST[$index]}" \
    || ! bash -n "${COMPONENT_DEST[$index]}" 2>/dev/null; then
    installed=0
  fi
done

if [[ "$installed" -eq 1 ]]; then
  LIVE_RECORD_AFTER="$(live_record_digest)"
  [[ "$LIVE_RECORD_AFTER" == "$LIVE_RECORD_BEFORE" ]] \
    || die 72 FAILED "active release record changed before NOT_NEEDED completion"
  if ! emit_receipt "NOT_NEEDED"; then
    die 71 FAILED "controller receipt ownership could not be proven"
  fi
  say "OPIP_UI_BOOTSTRAP_STATUS=NOT_NEEDED"
  say "OPIP_UI_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
  say "OPIP_UI_BOOTSTRAP_GATEWAY_WIRING=$GATEWAY_WIRING_STATE"
  say "OPIP_UI_BOOTSTRAP_RECEIPT=$RECEIPT_FILE"
  exit 0
fi

# --- 13. bounded backup of the currently installed set --------------------
install -d -m 0700 "$BACKUP_DIR" || die 71 FAILED "controller backup directory is unusable"
for index in "${!COMPONENT_NAME[@]}"; do
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    chown root:root -- "$BACKUP_DIR" 2>/dev/null || true
  fi
  if [[ "${PREVIOUS_SHA256[$index]}" == "NONE" ]]; then
    continue
  fi
  if ! cp -p -- "${COMPONENT_DEST[$index]}" "$BACKUP_DIR/${COMPONENT_NAME[$index]}"; then
    die 71 FAILED "backup of ${COMPONENT_NAME[$index]} failed"
  fi
  # Ownership of the backup is claimed from the moment it exists, so a later
  # failure can always attempt a faithful restore.
  BACKUP_WRITTEN[$index]=1
  chmod 0600 "$BACKUP_DIR/${COMPONENT_NAME[$index]}" \
    || die 71 FAILED "backup of ${COMPONENT_NAME[$index]} could not be sealed"
  if [[ "$PRODUCTION_MODE" == "1" ]]; then
    chown root:root -- "$BACKUP_DIR/${COMPONENT_NAME[$index]}" 2>/dev/null \
      || die 71 FAILED "backup of ${COMPONENT_NAME[$index]} could not be owned by root"
  fi
done

# --- 14. atomic installation ---------------------------------------------
LIVE_RECORD_PRE_INSTALL="$(live_record_digest)"
[[ "$LIVE_RECORD_PRE_INSTALL" == "$LIVE_RECORD_BEFORE" ]] \
  || die 72 FAILED "active release record changed before installation"

for index in "${!COMPONENT_NAME[@]}"; do
  if ! atomic_install_component "$index" "$TMP_DIR/${COMPONENT_NAME[$index]}"; then
    report_restore_result || true
    die 71 FAILED "installation of ${COMPONENT_NAME[$index]} failed"
  fi
done

# --- 15. post-install verification ---------------------------------------
LIVE_RECORD_AFTER="$(live_record_digest)"
if ! verify_targets || [[ "$LIVE_RECORD_AFTER" != "$LIVE_RECORD_BEFORE" ]]; then
  echo "post-install verification failed; restoring the previous controller set" >&2
  report_restore_result || true
  die 71 FAILED "post-install verification failed"
fi

# --- 16. receipt ---------------------------------------------------------
if ! emit_receipt "SUCCESS"; then
  echo "controller receipt ownership could not be proven" >&2
  report_restore_result || true
  die 71 FAILED "controller receipt ownership could not be proven"
fi

say "OPIP_UI_BOOTSTRAP_STATUS=SUCCESS"
say "OPIP_UI_BOOTSTRAP_TARGET_SHA=$TARGET_SHA"
say "OPIP_UI_BOOTSTRAP_ORIGIN_MAIN=$REMOTE_MAIN_SHA"
say "OPIP_UI_BOOTSTRAP_GATEWAY_WIRING=$GATEWAY_WIRING_STATE"
for index in "${!COMPONENT_NAME[@]}"; do
  say "OPIP_UI_BOOTSTRAP_INSTALLED_${COMPONENT_NAME[$index]}=${TARGET_SHA256[$index]}"
done
say "OPIP_UI_BOOTSTRAP_RECEIPT=$RECEIPT_FILE"
say "OPIP_UI_BOOTSTRAP_ACTIVE_RECORD_DIGEST=$LIVE_RECORD_AFTER"
say "next: owner-gated /deploy-ui <merged main SHA> through the existing workflow"
exit 0
