#!/usr/bin/env bash
set -Eeuo pipefail

# Defaults are the production paths; the overrides exist so tests can run hermetically.
# The dump runs through `docker exec` on the running container, never `docker compose`:
# compose interpolates the whole analytics file (including unrelated OPIP_GRAFANA_*
# variables) before it selects a service, which made every backup fail.
ENV_FILE="${OPIP_BACKUP_ENV_FILE:-/etc/opip-data-platform.env}"
BACKUP_ROOT="${OPIP_BACKUP_ROOT:-/var/backups/opip-postgres}"
LOCK_FILE="${OPIP_BACKUP_LOCK_FILE:-/var/lock/opip-postgres-backup.lock}"
CONTAINER="${OPIP_BACKUP_CONTAINER:-opip-postgres}"
MIN_FREE_BYTES=$((2 * 1024 * 1024 * 1024))   # D1 absolute minimum: never write below it
DEFAULT_DUMP_BYTES=$((256 * 1024 * 1024))    # used when no earlier dump exists

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
expected="${last_dump:-$DEFAULT_DUMP_BYTES}"
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

# `-e PGPASSWORD` with no value forwards the variable, so the secret never enters argv.
PGPASSWORD="$OPIP_POSTGRES_ADMIN_PASSWORD" docker exec -i -e PGPASSWORD "$CONTAINER" \
  pg_dump --format=custom --compress=9 \
  --username "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" \
  --dbname "${OPIP_POSTGRES_DB:-opip}" > "$tmp"
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
mv -f -- "$tmp" "$target"
sha256sum "$target" > "$target.sha256"
chmod 0600 "$target" "$target.sha256"
sha256sum --check --status "$target.sha256"

find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump' -mtime +30 -delete
find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump.sha256' -mtime +30 -delete

echo "O'Pip PostgreSQL backup created: $target"
echo "A restore drill and an off-host copy are still required before this backup is relied on."
