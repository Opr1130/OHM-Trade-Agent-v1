#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — host runtime library (Module 2H).
#
# Shared by the OFF-mode provisioner and the rollback tool. This file does not
# activate SHADOW, does not read provider credential values into output, and does
# not open provider egress. Source it; do not execute it.
set -euo pipefail

_TRIM='[:space:]'
_ROLLBACK_FAIL='ROLLBACK_RUNTIME=FAIL'

require_exact_sha() {
  local sha="$1"
  if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "a 40-character lowercase Git SHA is required; branch names and other refs are refused" >&2
    return 64
  fi
}

normalize_dir() {
  local dir="$1"
  mkdir -p "$dir"
  (cd "$dir" && pwd)
  return
}

normalize_file() {
  local path="$1" dir base
  dir="$(dirname "$path")"
  base="$(basename "$path")"
  mkdir -p "$dir"
  printf '%s/%s\n' "$(cd "$dir" && pwd)" "$base"
  return
}

runtime_paths_init() {
  if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" ]]; then
    PREFIX="$(normalize_dir "${OPIP_RUNTIME_PREFIX:?test harness requires OPIP_RUNTIME_PREFIX}")"
    ENV_FILE="$(normalize_file "${OPIP_COMMITTEE_ENV_FILE:?test harness requires OPIP_COMMITTEE_ENV_FILE}")"
    MANIFEST="$(normalize_file "${OPIP_TEST_MANIFEST:?test harness requires OPIP_TEST_MANIFEST}")"
    REPLICA="$(normalize_dir "${OPIP_TEST_REPLICA:?test harness requires OPIP_TEST_REPLICA}")"
    UNIT_FILE="$(normalize_file "${OPIP_TEST_UNIT_FILE:?test harness requires OPIP_TEST_UNIT_FILE}")"
    case "$PREFIX" in
      /opt/opip|/opt/opip/*|/etc|/etc/*)
        echo "test harness cannot target the production runtime prefix" >&2
        exit 76
        ;;
      *) ;;
    esac
    case "$ENV_FILE" in
      /etc/opip/*)
        echo "test harness cannot target the production environment file" >&2
        exit 76
        ;;
      *) ;;
    esac
    SBIN_DIR="$PREFIX/sbin"
    LIB_DIR="$PREFIX/lib"
    DROPIN_DIR="$PREFIX/dropin"
    COMMITTEE_HOME="$PREFIX/committee-home"
  else
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
      echo "run the committee host runtime tool as root" >&2
      exit 77
    fi
    PREFIX=/opt/opip
    ENV_FILE=/etc/opip/committee-credentials.env
    MANIFEST=/var/lib/opip-learning/data/manifest.env
    REPLICA=/var/lib/opip-learning/canonical-replica
    UNIT_FILE=/etc/systemd/system/opip-committee-shadow.service
    SBIN_DIR=/usr/local/sbin
    LIB_DIR=/usr/local/lib/opip
    DROPIN_DIR=/etc/systemd/system/opip-committee-shadow.service.d
    COMMITTEE_HOME=/var/lib/opip-committee
  fi
  APP_ROOT="$PREFIX/app"
  VENV_ROOT="$PREFIX/venv"
  VENV_PYTHON="$VENV_ROOT/bin/python"
  if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" != "1" ]]; then
    case "$PREFIX" in
      /*) ;;
      *)
        echo "runtime prefix must be absolute" >&2
        exit 64
        ;;
    esac
  fi
  if [[ "$PREFIX" == *$'\n'* || "$PREFIX" == *..* ]]; then
    echo "runtime prefix is unsafe" >&2
    exit 64
  fi
}

resolve_source_root() {
  local script_dir="$1"
  if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" ]]; then
    SOURCE_ROOT="$(normalize_dir "${OPIP_COMMITTEE_SOURCE_ROOT:?test harness requires OPIP_COMMITTEE_SOURCE_ROOT}")"
  else
    SOURCE_ROOT="$(cd "$script_dir/../.." && pwd)"
  fi
  if [[ ! -d "$SOURCE_ROOT" || -L "$SOURCE_ROOT" ]]; then
    echo "release source root is missing" >&2
    return 79
  fi
  if [[ ! -f "$SOURCE_ROOT/requirements.txt" || -L "$SOURCE_ROOT/requirements.txt" ]]; then
    echo "committed requirements.txt is missing from the release tree" >&2
    return 79
  fi
  if [[ ! -s "$SOURCE_ROOT/requirements.txt" ]]; then
    echo "committed requirements.txt is empty" >&2
    return 79
  fi
  if [[ ! -f "$SOURCE_ROOT/app/opip/committee/cycle_runner.py" || -L "$SOURCE_ROOT/app/opip/committee/cycle_runner.py" ]]; then
    echo "cycle_runner.py is missing from the release tree" >&2
    return 79
  fi
  local src_real prefix_real
  src_real="$(realpath "$SOURCE_ROOT")"
  mkdir -p "$PREFIX"
  prefix_real="$(realpath "$PREFIX")"
  case "$src_real" in
    "$prefix_real"|"$prefix_real"/*)
      echo "release source must not live inside the runtime prefix" >&2
      return 79
      ;;
    *) ;;
  esac
}

extract_provider_lines() {
  local file="$1" line
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      OPIP_COMMITTEE_OPENAI_API_KEY=*|OPIP_COMMITTEE_ANTHROPIC_API_KEY=*)
        printf '%s\n' "$line"
        ;;
      *) ;;
    esac
  done < "$file"
}

assert_no_duplicate_keys() {
  local file="$1"
  awk -F= '
    /^[A-Za-z_][A-Za-z0-9_]*=/ { count[$1]++ }
    END {
      for (key in count) if (count[key] > 1) exit 1
    }
  ' "$file"
}

# Rewrite non-secret keys. Provider credential lines are copied unchanged and
# compared before the file is replaced. Their values are never printed.
rewrite_nonsecret_env() {
  local sha="$1"
  local tmp before after line key
  local -a order=(
    OPIP_APP_ROOT
    OPIP_VENV_PYTHON
    OPIP_COMMITTEE_LEARNING_MANIFEST
    OPIP_CANONICAL_REPLICA_ROOT_HOST
    OPIP_COMMITTEE_MAX_CASES_PER_CYCLE
    OPIP_COMMITTEE_MODE
    OPIP_COMMITTEE_RELEASE_SHA
  )
  local -A managed=(
    [OPIP_APP_ROOT]="$APP_ROOT"
    [OPIP_VENV_PYTHON]="$VENV_PYTHON"
    [OPIP_COMMITTEE_LEARNING_MANIFEST]="$MANIFEST"
    [OPIP_CANONICAL_REPLICA_ROOT_HOST]="$REPLICA"
    [OPIP_COMMITTEE_MAX_CASES_PER_CYCLE]="1"
    [OPIP_COMMITTEE_MODE]="off"
    [OPIP_COMMITTEE_RELEASE_SHA]="$sha"
  )
  local value
  for value in "$APP_ROOT" "$VENV_PYTHON" "$MANIFEST" "$REPLICA" "$sha" "1" "off"; do
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
      echo "refusing unsafe configuration value" >&2
      return 64
    fi
  done
  mkdir -p "$(dirname "$ENV_FILE")"
  if [[ ! -f "$ENV_FILE" ]]; then
    local example
    example="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/committee-credentials.env.example"
    if [[ ! -f "$example" ]]; then
      echo "environment file is missing and no template is available" >&2
      return 78
    fi
    install -m 0600 "$example" "$ENV_FILE"
  fi
  tmp="$(mktemp "$(dirname "$ENV_FILE")/committee-env.XXXXXX")"
  before="$(mktemp "$(dirname "$ENV_FILE")/committee-cred.XXXXXX")"
  after="$(mktemp "$(dirname "$ENV_FILE")/committee-cred.XXXXXX")"
  chmod 0600 "$tmp" "$before" "$after"
  extract_provider_lines "$ENV_FILE" > "$before"
  local -A seen=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      OPIP_COMMITTEE_OPENAI_API_KEY=*|OPIP_COMMITTEE_ANTHROPIC_API_KEY=*)
        printf '%s\n' "$line" >> "$tmp"
        continue
        ;;
      *) ;;
    esac
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
      key="${BASH_REMATCH[1]}"
      if [[ -n "${managed[$key]+x}" ]]; then
        if [[ -z "${seen[$key]+x}" ]]; then
          printf '%s=%s\n' "$key" "${managed[$key]}" >> "$tmp"
          seen[$key]=1
        fi
        continue
      fi
    fi
    printf '%s\n' "$line" >> "$tmp"
  done < "$ENV_FILE"
  for key in "${order[@]}"; do
    if [[ -z "${seen[$key]+x}" ]]; then
      printf '%s=%s\n' "$key" "${managed[$key]}" >> "$tmp"
    fi
  done
  extract_provider_lines "$tmp" > "$after"
  if ! cmp -s "$before" "$after"; then
    rm -f "$tmp" "$before" "$after"
    echo "refusing environment update because provider credential lines would change" >&2
    return 80
  fi
  if ! assert_no_duplicate_keys "$tmp"; then
    rm -f "$tmp" "$before" "$after"
    echo "refusing environment update because a key would be duplicated" >&2
    return 80
  fi
  chmod 0600 "$tmp"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    chown root:root "$tmp"
  fi
  mv -f "$tmp" "$ENV_FILE"
  chmod 0600 "$ENV_FILE"
  rm -f "$before" "$after"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    chown root:root "$ENV_FILE"
  fi
}

# Failure path: force mode OFF without moving the release SHA or any credential.
force_mode_off_only() {
  local tmp before after line seen=0
  [[ -f "$ENV_FILE" ]] || return 0
  tmp="$(mktemp "$(dirname "$ENV_FILE")/committee-env.XXXXXX")"
  before="$(mktemp "$(dirname "$ENV_FILE")/committee-cred.XXXXXX")"
  after="$(mktemp "$(dirname "$ENV_FILE")/committee-cred.XXXXXX")"
  chmod 0600 "$tmp" "$before" "$after"
  extract_provider_lines "$ENV_FILE" > "$before"
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      OPIP_COMMITTEE_OPENAI_API_KEY=*|OPIP_COMMITTEE_ANTHROPIC_API_KEY=*)
        printf '%s\n' "$line" >> "$tmp"
        continue
        ;;
      OPIP_COMMITTEE_MODE=*)
        if [[ "$seen" -eq 0 ]]; then
          printf 'OPIP_COMMITTEE_MODE=off\n' >> "$tmp"
          seen=1
        fi
        continue
        ;;
      *) ;;
    esac
    printf '%s\n' "$line" >> "$tmp"
  done < "$ENV_FILE"
  if [[ "$seen" -eq 0 ]]; then
    printf 'OPIP_COMMITTEE_MODE=off\n' >> "$tmp"
  fi
  extract_provider_lines "$tmp" > "$after"
  if ! cmp -s "$before" "$after"; then
    rm -f "$tmp" "$before" "$after"
    echo "refusing mode update because provider credential lines would change" >&2
    return 80
  fi
  chmod 0600 "$tmp"
  mv -f "$tmp" "$ENV_FILE"
  chmod 0600 "$ENV_FILE"
  rm -f "$before" "$after"
}

keep_timer_disabled() {
  if [[ "${OPIP_COMMITTEE_RUNTIME_TEST_HARNESS:-}" == "1" ]]; then
    mkdir -p "$PREFIX"
    printf 'disabled\n' > "$PREFIX/timer-enablement"
    printf 'inactive\n' > "$PREFIX/timer-active"
    return 0
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required to keep the Committee timer inactive" >&2
    return 1
  fi
  if ! systemctl disable opip-committee-shadow.timer >/dev/null 2>&1; then
    echo "Committee timer could not be disabled" >&2
    return 1
  fi
  if ! systemctl stop opip-committee-shadow.timer >/dev/null 2>&1; then
    echo "Committee timer could not be stopped" >&2
    return 1
  fi
}

prove_cycle_runner_import() {
  local app_root="$1"
  local python="$2"
  local resolved venv_root
  [[ -d "$app_root" && ! -L "$app_root" ]] || return 1
  [[ -e "$python" && -x "$python" ]] || return 1
  resolved="$(realpath "$python")" || return 1
  venv_root="$(cd "$(dirname "$python")/.." && pwd)" || return 1
  case "$resolved" in
    "$venv_root"/*|/usr/bin/*|/usr/local/bin/*) ;;
    *)
      echo "refusing interpreter outside the virtualenv or system Python" >&2
      return 83
      ;;
  esac
  (
    cd "$app_root" || exit 1
    # cwd is the release root, so `app` resolves from sys.path[0]. PYTHONPATH is
    # removed so an ambient path cannot shadow that package. Do not use python -I:
    # isolated mode also drops the release root from sys.path. -s skips user site
    # packages and leaves the virtualenv's own site-packages in place.
    env -u PYTHONPATH -u PYTHONHOME -u PYTHONSTARTUP -u PYTHONUSERBASE \
      "$python" -s -c 'import os, sys
root = os.path.realpath(os.getcwd())
entry = sys.path[0]
resolved = root if entry in ("", ".") else os.path.realpath(entry)
if os.path.realpath(resolved) != root:
    raise SystemExit(2)
import app.opip.committee.cycle_runner'
  )
}

copy_release_tree() {
  local src="$1"
  local dest="$2"
  mkdir -p "$dest"
  chmod 0700 "$dest"
  cp -a "$src"/. "$dest"/ || return 79
  local dir
  while IFS= read -r -d '' dir; do
    rm -rf "$dir"
  done < <(find -P "$dest" -type d -name '__pycache__' -print0)
  find -P "$dest" -type f \( -name '*.pyc' -o -name '.env' \) -delete
  local links
  links="$(find -P "$dest" -type l | wc -l | tr -d "$_TRIM")"
  if [[ "$links" != "0" ]]; then
    echo "refusing release tree that contains symlinks" >&2
    return 79
  fi
  local dest_real prefix_real
  dest_real="$(realpath "$dest")"
  prefix_real="$(realpath "$PREFIX")"
  case "$dest_real" in
    "$prefix_real"/*) ;;
    *)
      echo "staged release tree escaped the runtime prefix" >&2
      return 79
      ;;
  esac
  if [[ ! -f "$dest/app/opip/committee/cycle_runner.py" || -L "$dest/app/opip/committee/cycle_runner.py" ]]; then
    echo "staged release tree lost cycle_runner.py" >&2
    return 79
  fi
  if ! cmp -s "$src/requirements.txt" "$dest/requirements.txt"; then
    echo "staged requirements.txt does not match the release tree" >&2
    return 79
  fi
  find "$dest" -type d -exec chmod 0755 {} +
  find "$dest" -type f -exec chmod 0644 {} +
}

install_runtime_tools() {
  local script_dir="$1"
  install -d -m 0755 "$SBIN_DIR" "$LIB_DIR"
  install -m 0644 "$script_dir/committee-host-runtime-lib.sh" "$LIB_DIR/committee-host-runtime-lib.sh"
  install -m 0755 "$script_dir/rollback-committee-host-runtime.sh" "$SBIN_DIR/opip-rollback-committee-host-runtime"
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    chown -R root:root "$LIB_DIR" "$SBIN_DIR/opip-rollback-committee-host-runtime"
  fi
}

restore_retiring() {
  if [[ -e "$PREFIX/app.retiring" && -e "$PREFIX/app" ]]; then
    return 1
  fi
  if [[ -e "$PREFIX/venv.retiring" && -e "$PREFIX/venv" ]]; then
    return 1
  fi
  if [[ -e "$PREFIX/app.retiring" ]]; then
    mv "$PREFIX/app.retiring" "$PREFIX/app"
  fi
  if [[ -e "$PREFIX/venv.retiring" ]]; then
    mv "$PREFIX/venv.retiring" "$PREFIX/venv"
  fi
}

fail_provision() {
  local code="$1"
  shift
  printf 'COMMITTEE_RUNTIME_INSTALL=FAIL %s\n' "$*" >&2
  if [[ "${SWAP_COMMITTED:-0}" != "1" ]]; then
    restore_retiring || true
  fi
  if [[ -n "${STAGE:-}" && -d "${STAGE}" ]]; then
    rm -rf "$STAGE"
  fi
  force_mode_off_only || true
  keep_timer_disabled || true
  exit "$code"
}

activate_staged_runtime() {
  if [[ -e "$PREFIX/app.retiring" || -e "$PREFIX/venv.retiring" ]]; then
    restore_retiring || fail_provision 81 "incomplete runtime swap is still present; refusing to discard either tree"
  fi
  if [[ -e "$PREFIX/app" ]]; then
    mv "$PREFIX/app" "$PREFIX/app.retiring" || fail_provision 81 "could not move the active application aside"
  fi
  if [[ -e "$PREFIX/venv" ]]; then
    mv "$PREFIX/venv" "$PREFIX/venv.retiring" || fail_provision 81 "could not move the active interpreter aside"
  fi
  if ! mv "$STAGE/app" "$PREFIX/app"; then
    restore_retiring
    fail_provision 81 "could not activate the staged application"
  fi
  if ! mv "$STAGE/venv" "$PREFIX/venv"; then
    rm -rf "$PREFIX/app"
    restore_retiring
    fail_provision 81 "could not activate the staged interpreter"
  fi
  if ! prove_cycle_runner_import "$APP_ROOT" "$VENV_PYTHON"; then
    rm -rf "$PREFIX/app" "$PREFIX/venv"
    restore_retiring
    fail_provision 82 "import proof failed; the previous runtime was restored"
  fi
  SWAP_COMMITTED=1
  if [[ -e "$PREFIX/app.retiring" ]]; then
    mv "$PREFIX/app.retiring" "$PREFIX/previous.app.next" || fail_provision 81 "could not retain the previous application"
    rm -rf "$PREFIX/previous/app"
    mkdir -p "$PREFIX/previous"
    chmod 0755 "$PREFIX/previous"
    mv "$PREFIX/previous.app.next" "$PREFIX/previous/app" || fail_provision 81 "could not publish the previous application"
  fi
  if [[ -e "$PREFIX/venv.retiring" ]]; then
    mv "$PREFIX/venv.retiring" "$PREFIX/previous.venv.next" || fail_provision 81 "could not retain the previous interpreter"
    rm -rf "$PREFIX/previous/venv"
    mkdir -p "$PREFIX/previous"
    chmod 0755 "$PREFIX/previous"
    mv "$PREFIX/previous.venv.next" "$PREFIX/previous/venv" || fail_provision 81 "could not publish the previous interpreter"
  fi
  rm -rf "$STAGE"
  STAGE=""
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    chown -R root:root "$APP_ROOT" "$VENV_ROOT"
  fi
}

runtime_already_ready() {
  local sha="$1" current
  [[ -f "$APP_ROOT/.opip-release-sha" && ! -L "$APP_ROOT/.opip-release-sha" ]] || return 1
  current="$(tr -d "$_TRIM" < "$APP_ROOT/.opip-release-sha")"
  [[ "$current" == "$sha" ]] || return 1
  prove_cycle_runner_import "$APP_ROOT" "$VENV_PYTHON"
}

provision_main() {
  local sha="$1"
  local script_dir="$2"
  umask 077
  SWAP_COMMITTED=0
  STAGE=""
  mkdir -p "$PREFIX"
  chmod 0755 "$PREFIX"
  resolve_source_root "$script_dir" || fail_provision 79 "release source was rejected"
  if runtime_already_ready "$sha"; then
    rewrite_nonsecret_env "$sha" || fail_provision 80 "environment update failed"
    install_runtime_tools "$script_dir" || fail_provision 81 "runtime tools were not installed"
    keep_timer_disabled || fail_provision 83 "Committee timer could not be stopped"
    printf 'COMMITTEE_RUNTIME_INSTALL=PASS sha=%s\n' "$sha"
    return 0
  fi
  STAGE="$(mktemp -d "$PREFIX/staging.XXXXXXXX")"
  chmod 0700 "$STAGE"
  copy_release_tree "$SOURCE_ROOT" "$STAGE/app" || fail_provision 79 "release tree was rejected"
  printf '%s\n' "$sha" > "$STAGE/app/.opip-release-sha"
  chmod 0644 "$STAGE/app/.opip-release-sha"
  if ! python3 -m venv "$STAGE/venv"; then
    fail_provision 81 "virtualenv creation failed"
  fi
  if ! "$STAGE/venv/bin/python" -m pip install \
    --disable-pip-version-check \
    --no-cache-dir \
    --no-input \
    --require-virtualenv \
    -r "$STAGE/app/requirements.txt"; then
    fail_provision 81 "dependency install from requirements.txt failed"
  fi
  if ! prove_cycle_runner_import "$STAGE/app" "$STAGE/venv/bin/python"; then
    fail_provision 82 "staged cycle_runner import failed"
  fi
  install_runtime_tools "$script_dir" || fail_provision 81 "runtime tools were not installed"
  activate_staged_runtime
  if ! rewrite_nonsecret_env "$sha"; then
    # The new tree is active and proven, but configuration did not commit.
    # Put the previous tree back so a stale SHA cannot stay paired with new files
    # or the reverse. Mode is forced OFF either way.
    if [[ -d "$PREFIX/previous/app" && -d "$PREFIX/previous/venv" ]]; then
      rm -rf "$PREFIX/app.failed" "$PREFIX/venv.failed"
      mv "$PREFIX/app" "$PREFIX/app.failed" || fail_provision 80 "could not move the new application aside"
      mv "$PREFIX/venv" "$PREFIX/venv.failed" || fail_provision 80 "could not move the new interpreter aside"
      mv "$PREFIX/previous/app" "$PREFIX/app" || fail_provision 80 "could not restore the previous application"
      mv "$PREFIX/previous/venv" "$PREFIX/venv" || fail_provision 80 "could not restore the previous interpreter"
      mkdir -p "$PREFIX/previous"
      mv "$PREFIX/app.failed" "$PREFIX/previous/app" || fail_provision 80 "could not retain the failed application"
      mv "$PREFIX/venv.failed" "$PREFIX/previous/venv" || fail_provision 80 "could not retain the failed interpreter"
      SWAP_COMMITTED=0
    fi
    fail_provision 80 "environment update failed after staging; previous runtime restored when present"
  fi
  keep_timer_disabled || fail_provision 83 "Committee timer could not be stopped"
  if ! prove_cycle_runner_import "$APP_ROOT" "$VENV_PYTHON"; then
    fail_provision 82 "active import proof failed after configuration"
  fi
  local installed
  installed="$(tr -d "$_TRIM" < "$APP_ROOT/.opip-release-sha")"
  if [[ "$installed" != "$sha" ]]; then
    fail_provision 82 "active release identity does not match the authorized SHA"
  fi
  printf 'COMMITTEE_RUNTIME_INSTALL=PASS sha=%s\n' "$sha"
}

rollback_main() {
  local mode="${1:-apply}"
  umask 077
  if [[ "$mode" == "--check" ]]; then
    if [[ -d "$PREFIX/previous/app" ]]; then
      if prove_cycle_runner_import "$PREFIX/previous/app" "$PREFIX/previous/venv/bin/python"; then
        echo "ROLLBACK_CHECK=PASS"
        return 0
      fi
      echo "ROLLBACK_CHECK=FAIL" >&2
      return 1
    fi
    if prove_cycle_runner_import "$APP_ROOT" "$VENV_PYTHON"; then
      echo "ROLLBACK_CHECK=PASS"
      return 0
    fi
    echo "ROLLBACK_CHECK=FAIL" >&2
    return 1
  fi
  if ! prove_cycle_runner_import "$PREFIX/previous/app" "$PREFIX/previous/venv/bin/python"; then
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  fi
  if [[ ! -f "$PREFIX/previous/app/.opip-release-sha" || -L "$PREFIX/previous/app/.opip-release-sha" ]]; then
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  fi
  local restored
  restored="$(tr -d "$_TRIM" < "$PREFIX/previous/app/.opip-release-sha")" || {
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  }
  require_exact_sha "$restored" || {
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  }
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$PREFIX/displaced/$stamp"
  chmod 0700 "$PREFIX/displaced" "$PREFIX/displaced/$stamp"
  mv "$PREFIX/app" "$PREFIX/displaced/$stamp/app"
  mv "$PREFIX/venv" "$PREFIX/displaced/$stamp/venv"
  if ! mv "$PREFIX/previous/app" "$PREFIX/app"; then
    mv "$PREFIX/displaced/$stamp/app" "$PREFIX/app" || true
    mv "$PREFIX/displaced/$stamp/venv" "$PREFIX/venv" || true
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  fi
  if ! mv "$PREFIX/previous/venv" "$PREFIX/venv"; then
    rm -rf "$PREFIX/app"
    mv "$PREFIX/displaced/$stamp/app" "$PREFIX/app" || true
    mv "$PREFIX/displaced/$stamp/venv" "$PREFIX/venv" || true
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  fi
  if ! prove_cycle_runner_import "$APP_ROOT" "$VENV_PYTHON"; then
    rm -rf "$PREFIX/app" "$PREFIX/venv"
    mv "$PREFIX/displaced/$stamp/app" "$PREFIX/app" || true
    mv "$PREFIX/displaced/$stamp/venv" "$PREFIX/venv" || true
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  fi
  if ! rewrite_nonsecret_env "$restored"; then
    rm -rf "$PREFIX/app" "$PREFIX/venv"
    mv "$PREFIX/displaced/$stamp/app" "$PREFIX/app" || true
    mv "$PREFIX/displaced/$stamp/venv" "$PREFIX/venv" || true
    force_mode_off_only || true
    keep_timer_disabled || true
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  fi
  mkdir -p "$PREFIX/previous"
  mv "$PREFIX/displaced/$stamp/app" "$PREFIX/previous/app"
  mv "$PREFIX/displaced/$stamp/venv" "$PREFIX/previous/venv"
  rmdir "$PREFIX/displaced/$stamp" 2>/dev/null || true
  if ! keep_timer_disabled; then
    echo "$_ROLLBACK_FAIL" >&2
    return 1
  fi
  echo "ROLLBACK_RUNTIME=PASS"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "source this library from the committee runtime tools" >&2
  exit 64
fi
