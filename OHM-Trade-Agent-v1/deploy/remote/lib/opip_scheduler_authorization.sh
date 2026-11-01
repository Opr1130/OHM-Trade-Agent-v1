#!/usr/bin/env bash
# O'Pip Stage-2 IRR scheduler authorization marker contract.
#
# Sourced by deploy/remote/reconcile-scheduler.sh before any scheduler mutation.
# Sourcing has no side effects: this file only defines functions.
#
# The marker is the ONLY durable authorization for installing
# /etc/cron.d/opip-irr-shadow. It must be a root-owned, non-executable,
# non-group/world-writable regular file containing exactly one of these byte
# sequences:
#
#   installed        installed\n
#   absent           absent\n
#
# Anything else - CRLF, leading/trailing spaces, a double newline, any other
# token, a directory, a symlink, an unreadable file, a foreign owner or unsafe
# permissions - is refused with exit 65 before the caller mutates anything.

opip_scheduler_authorization_state() {
  # opip_scheduler_authorization_state <marker-path>
  #   -> prints ABSENT when the marker does not exist
  #   -> prints INSTALLED / ABSENT for a valid marker
  #   -> prints the reason to stderr and returns 65 for an unacceptable marker
  local path="${1:-}"
  if [[ -z "$path" ]]; then
    echo "IRR shadow scheduler authorization path is empty" >&2
    return 65
  fi
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    printf 'ABSENT\n'
    return 0
  fi
  if [[ -L "$path" || ! -f "$path" ]]; then
    echo "IRR shadow scheduler authorization is not a regular file" >&2
    return 65
  fi
  if [[ ! -r "$path" ]]; then
    echo "IRR shadow scheduler authorization is not readable" >&2
    return 65
  fi

  # Ownership and mode: root-owned by default, never writable by group/other and
  # never executable. The test seam may relax ONLY the expected uid, and only
  # while the repository's deploy test seams are explicitly enabled.
  local allowed_uid=0 mode uid
  if [[ "${OPIP_DEPLOY_TEST_SEAMS:-0}" == "1" && -n "${OPIP_SCHEDULER_AUTH_ALLOWED_UID:-}" ]]; then
    allowed_uid="$OPIP_SCHEDULER_AUTH_ALLOWED_UID"
  fi
  uid="$(stat -c '%u' -- "$path" 2>/dev/null)" || {
    echo "IRR shadow scheduler authorization ownership is unreadable" >&2
    return 65
  }
  if [[ "$uid" != "$allowed_uid" ]]; then
    echo "IRR shadow scheduler authorization must be owned by uid $allowed_uid (found $uid)" >&2
    return 65
  fi
  mode="$(stat -c '%a' -- "$path" 2>/dev/null)" || {
    echo "IRR shadow scheduler authorization mode is unreadable" >&2
    return 65
  }
  if [[ ! "$mode" =~ ^[0-7]{3,4}$ ]]; then
    echo "IRR shadow scheduler authorization mode is invalid" >&2
    return 65
  fi
  if (( (8#$mode & 8#022) != 0 )); then
    echo "IRR shadow scheduler authorization must not be group- or world-writable" >&2
    return 65
  fi
  if (( (8#$mode & 8#111) != 0 )); then
    echo "IRR shadow scheduler authorization must not be executable" >&2
    return 65
  fi

  local size raw
  size="$(stat -c '%s' -- "$path" 2>/dev/null)" || {
    echo "IRR shadow scheduler authorization size is unreadable" >&2
    return 65
  }
  # "installed" and "absent" are the only accepted tokens; the longest accepted
  # file is "installed\n" (10 bytes). Anything larger is refused unread.
  if [[ ! "$size" =~ ^[0-9]+$ ]] || (( size < 1 || size > 10 )); then
    echo "IRR shadow scheduler authorization must contain one short token" >&2
    return 65
  fi
  # `cat` preserves the exact bytes; the sentinel keeps trailing newlines that
  # command substitution would otherwise strip.
  raw="$(cat -- "$path" 2>/dev/null; printf 'x')" || {
    echo "IRR shadow scheduler authorization is unreadable" >&2
    return 65
  }
  raw="${raw%x}"
  case "$raw" in
    installed | installed$'\n')
      printf 'INSTALLED\n'
      return 0
      ;;
    absent | absent$'\n')
      printf 'ABSENT\n'
      return 0
      ;;
  esac
  echo "IRR shadow scheduler authorization must be exactly 'installed' or 'absent' (optional final newline)" >&2
  return 65
}

# IRR activation-state snapshot contract.
#
# /etc/cron.d/opip-irr-shadow, /usr/local/bin/opip-irr-shadow-run and the image
# pin are ONE activation state. Worker-image verification can fail, and a
# failure must never destroy state that was not captured first, so the caller
# captures with `opip_irr_capture_activation_state` BEFORE it verifies anything
# and restores exactly what was there with `opip_irr_restore_activation_state`.
# A file that existed comes back with its content, mode and owner; a file that
# did not exist stays absent. Both take a capture directory plus the three
# artifact paths, so they can be exercised directly by focused tests and never
# touch any path they were not given.

opip_irr_capture_activation_state() {
  # opip_irr_capture_activation_state <capture-dir> <entry> <runner> <pin>
  local dir="${1:-}" entry="${2:-}" runner="${3:-}" pin="${4:-}"
  if [[ -z "$dir" || -z "$entry" || -z "$runner" || -z "$pin" ]]; then
    echo "IRR activation-state capture arguments are incomplete" >&2
    return 65
  fi
  if ! mkdir -p -- "$dir"; then
    echo "IRR activation-state capture directory is unusable: $dir" >&2
    return 69
  fi
  local pair name path
  for pair in "entry:$entry" "runner:$runner" "pin:$pin"; do
    name="${pair%%:*}"
    path="${pair#*:}"
    if [[ -e "$path" ]]; then
      if ! cp -a -- "$path" "$dir/$name.before"; then
        echo "IRR activation state could not be captured: $path" >&2
        return 69
      fi
      : > "$dir/$name.present"
    else
      rm -f -- "$dir/$name.present"
    fi
  done
  return 0
}

opip_irr_restore_activation_state() {
  # opip_irr_restore_activation_state <capture-dir> <entry> <runner> <pin>
  local dir="${1:-}" entry="${2:-}" runner="${3:-}" pin="${4:-}"
  if [[ -z "$dir" || -z "$entry" || -z "$runner" || -z "$pin" ]]; then
    echo "IRR activation-state restore arguments are incomplete" >&2
    return 65
  fi
  local pair name path
  for pair in "entry:$entry" "runner:$runner" "pin:$pin"; do
    name="${pair%%:*}"
    path="${pair#*:}"
    if [[ -e "$dir/$name.present" ]]; then
      if ! cp -a -- "$dir/$name.before" "$path"; then
        echo "IRR activation state could not be restored: $path" >&2
        return 69
      fi
    elif ! rm -f -- "$path"; then
      echo "IRR activation state could not be cleared: $path" >&2
      return 69
    fi
  done
  return 0
}
