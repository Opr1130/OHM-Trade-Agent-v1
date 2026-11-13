#!/usr/bin/env bash
set -Eeuo pipefail

# Defaults are the production paths; overrides exist for hermetic tests. Container
# access uses `docker exec`, not `docker compose` (see opip-postgres-backup.sh).
ENV_FILE="${OPIP_BACKUP_ENV_FILE:-/etc/opip-data-platform.env}"
BACKUP_ROOT="${OPIP_BACKUP_ROOT:-/var/backups/opip-postgres}"
STATE_ROOT="${OPIP_BACKUP_STATE_ROOT:-/var/lib/opip-data-platform}"
LOCK_FILE="${OPIP_DRILL_LOCK_FILE:-/var/lock/opip-postgres-restore-drill.lock}"
PLANE_LOCK_FILE="${OPIP_PLANE_LOCK_FILE:-/var/lock/opip-learning-plane.lock}"
CONTAINER="${OPIP_BACKUP_CONTAINER:-opip-postgres}"
MIN_FREE_BYTES=$((2 * 1024 * 1024 * 1024))   # D1 absolute minimum

[[ -r "$ENV_FILE" ]] || exit 78
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "O'Pip restore drill already active" >&2
  exit 75
}
exec 8>"$PLANE_LOCK_FILE"
flock -w 300 8 || {
  echo "learning plane remained busy for five minutes" >&2
  exit 75
}

backup="$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump' -printf '%T@ %p\n' | sort -nr | awk 'NR == 1 {sub(/^[^ ]+ /, ""); print}')"
[[ -n "$backup" && -r "$backup" && -r "$backup.sha256" ]] || {
  echo "no checksummed PostgreSQL dump is available for a restore drill" >&2
  exit 66
}
sha256sum --check --status "$backup.sha256"

# Restoring writes roughly the dump size several times over into the Postgres volume.
# Refuse unless the host keeps the 2 GiB floor after four times the dump size.
dump_bytes="$(stat -c %s "$backup")"
free_bytes="$(df -PB1 "$STATE_ROOT" | awk 'NR == 2 {print $4}')"
[[ "$free_bytes" =~ ^[0-9]+$ ]] || {
  echo "CRITICAL: free space could not be read; restore drill refused" >&2
  exit 75
}
if (( free_bytes < MIN_FREE_BYTES + 4 * dump_bytes )); then
  echo "CRITICAL: free space ${free_bytes} bytes is too low for a restore drill; refused, nothing changed" >&2
  exit 75
fi

stamp="$(date -u +%Y%m%d%H%M%S)"
database="opip_restore_drill_$stamp"
[[ "$database" =~ ^opip_restore_drill_[0-9]{14}$ ]] || exit 70

pg_exec() {
  PGPASSWORD="$OPIP_POSTGRES_ADMIN_PASSWORD" docker exec -i -e PGPASSWORD "$CONTAINER" "$@"
}
cleanup() {
  pg_exec dropdb --if-exists --force \
    --username "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" "$database" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

pg_exec createdb \
  --username "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" "$database"
pg_exec pg_restore --exit-on-error --no-owner \
  --username "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" \
  --dbname "$database" < "$backup"
schema_version="$(
  pg_exec psql --no-psqlrc --tuples-only --no-align \
    --username "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" \
    --dbname "$database" \
    --command "SELECT max(version) FROM ops.schema_version"
)"
[[ "$schema_version" =~ ^[0-9]+$ ]] || {
  echo "restored database did not contain a valid O'Pip schema" >&2
  exit 1
}

temporary="$(mktemp "$STATE_ROOT/last-restore-drill.env.XXXXXX")"
{
  printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'backup_file=%s\n' "$(basename "$backup")"
  printf 'schema_version=%s\n' "$schema_version"
} > "$temporary"
chown root:root "$temporary"
chmod 0600 "$temporary"
mv -f -- "$temporary" "$STATE_ROOT/last-restore-drill.env"

echo "O'Pip PostgreSQL restore drill succeeded"
echo "schema_version=$schema_version"
