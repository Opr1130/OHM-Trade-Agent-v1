#!/usr/bin/env bash
set -Eeuo pipefail

# Defaults are the production paths; the overrides exist so tests can run hermetically.
# The dump runs through `docker exec` on the running container, never `docker compose`:
# compose interpolates the whole analytics file (including unrelated OPIP_GRAFANA_*
# variables) before it selects a service, which made every backup fail.
ENV_FILE="${OPIP_BACKUP_ENV_FILE:-/etc/opip-data-platform.env}"
BACKUP_ROOT="${OPIP_BACKUP_ROOT:-/var/backups/opip-postgres}"
LOCK_FILE="${OPIP_BACKUP_LOCK_FILE:-/var/lock/opip-postgres-backup.lock}"
STATE_ROOT="${OPIP_BACKUP_STATE_ROOT:-/var/lib/opip-data-platform}"
CONTAINER="${OPIP_BACKUP_CONTAINER:-opip-postgres}"
MIN_FREE_BYTES=$((2 * 1024 * 1024 * 1024))   # D1 absolute minimum: never write below it
DEFAULT_DUMP_BYTES="${OPIP_BACKUP_DEFAULT_DUMP_BYTES:-$((512 * 1024 * 1024))}"  # used when no earlier dump exists

[[ -r "$ENV_FILE" ]] || exit 78
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0
install -d -m 0700 "$BACKUP_ROOT"

# Fail closed before writing: free space must cover the expected dump (the newest
# earlier dump, else a default) on top of the 2 GiB floor. Nothing is deleted here.
last_dump="$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump' -printf '%T@ %s\n' | sort -nr | awk 'NR == 1 {print $2}')"
expected="$DEFAULT_DUMP_BYTES"
(( ${last_dump:-0} * 2 > expected )) && expected=$((last_dump * 2))   # 2x margin for growth
free_bytes="$(df -PB1 "$BACKUP_ROOT" | awk 'NR == 2 {print $4}')"
[[ "$free_bytes" =~ ^[0-9]+$ ]] || {
  echo "CRITICAL: free space on the backup volume could not be read; backup refused" >&2
  exit 75
}
if (( free_bytes < MIN_FREE_BYTES + expected )); then
  echo "CRITICAL: free space ${free_bytes} bytes is below the 2 GiB floor plus the expected dump (${expected} bytes); backup refused, nothing deleted" >&2
  exit 75
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="opip-postgres-$stamp.dump"
tmp="$BACKUP_ROOT/.$name.tmp"
target="$BACKUP_ROOT/$name"
trap 'rm -f -- "$tmp"' EXIT INT TERM

# The write itself is bounded: the dump may use only the headroom above the 2 GiB
# floor (ulimit -f is in 1024-byte units), so a larger-than-expected dump is killed
# by the kernel, fails closed and is removed by the trap, instead of breaching the floor.
# `-e PGPASSWORD` with no value forwards the variable, so the secret never enters argv.
(
  ulimit -f $(( (free_bytes - MIN_FREE_BYTES) / 1024 ))
  PGPASSWORD="$OPIP_POSTGRES_ADMIN_PASSWORD" docker exec -i -e PGPASSWORD "$CONTAINER" \
    pg_dump --format=custom --compress=9 \
    --username "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" \
    --dbname "${OPIP_POSTGRES_DB:-opip}" > "$tmp"
)
# A recoverable dump, not merely a present one: it must be non-empty and its archive
# table of contents must be readable by pg_restore (no database credentials needed).
[[ -s "$tmp" ]] || {
  echo "pg_dump produced an empty file; backup discarded" >&2
  exit 1
}
docker exec -i "$CONTAINER" pg_restore --list < "$tmp" > /dev/null || {
  echo "dump table of contents is unreadable; backup discarded" >&2
  exit 1
}
# The table of contents sits at the front of the archive, so it stays readable when the
# data blocks behind it are truncated or damaged. Stream the whole archive through
# pg_restore (SQL to /dev/null, no database or credentials) so every block is decoded.
docker exec -i "$CONTAINER" pg_restore -f - < "$tmp" > /dev/null || {
  echo "dump data blocks are unreadable; backup discarded" >&2
  exit 1
}
mv -f -- "$tmp" "$target"
sha256sum "$target" > "$target.sha256"
chmod 0600 "$target" "$target.sha256"
sha256sum --check --status "$target.sha256"

# Mark this dump as verified (checksum + readable table of contents). Only marked
# dumps are ever eligible for retention; older unmarked dumps are never touched and
# need a separate, reviewed pruning manifest.
printf 'toc_verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$target.verified"
chmod 0600 "$target.verified"

# Retention (approved rule): keep the newest 3 verified dumps plus the newest verified
# dump of each of the 4 most recent ISO weeks. Always keep the newest verified dump and
# the dump named by the last restore drill (the last known recoverable backup), even
# beyond the counts. Refuse to prune more than 10 dumps in one run.
keep_daily=3
keep_weekly=4
max_delete=10
retain() {
  local -a verified=()
  local -A keep=() weeks=()
  local f d w i=0 drilled n=0
  mapfile -t verified < <(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump.verified' -printf '%f\n' | sed 's/\.verified$//' | sort -r)
  for f in "${verified[@]}"; do
    i=$((i + 1))
    (( i <= keep_daily )) && keep[$f]=1
    d="${f#opip-postgres-}"
    w="$(date -u -d "${d:0:8}" +%G-%V 2>/dev/null)" || { keep[$f]=1; continue; }
    if [[ -z "${weeks[$w]:-}" && ${#weeks[@]} -lt $keep_weekly ]]; then
      weeks[$w]=1
      keep[$f]=1
    fi
  done
  drilled="$(awk -F= '$1 == "backup_file" {print $2; exit}' "$STATE_ROOT/last-restore-drill.env" 2>/dev/null || true)"
  [[ "$drilled" =~ ^opip-postgres-[0-9TZ]+\.dump$ ]] && keep[$drilled]=1
  local -a doomed=()
  for f in "${verified[@]}"; do
    [[ -n "${keep[$f]:-}" ]] || doomed+=("$f")
  done
  n=${#doomed[@]}
  (( n == 0 )) && return 0
  if (( n > max_delete )); then
    echo "WARNING: retention would prune $n dumps (limit $max_delete); nothing pruned, review required" >&2
    return 0
  fi
  for f in "${doomed[@]}"; do
    rm -f -- "$BACKUP_ROOT/$f" "$BACKUP_ROOT/$f.sha256" "$BACKUP_ROOT/$f.verified"
  done
  echo "retention pruned $n verified dump(s); kept ${#keep[@]}"
}
retain || echo "WARNING: retention step failed; the new backup is intact" >&2

echo "O'Pip PostgreSQL backup created: $target"
echo "A restore drill and an off-host copy are still required before this backup is relied on."
