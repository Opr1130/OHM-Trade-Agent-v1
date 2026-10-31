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
