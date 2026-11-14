#!/usr/bin/env bash
set -Eeuo pipefail

# Isolated restore drill. The restore runs in a THROWAWAY postgres container with no
# network and its own data directory. The live database, its container and its admin
# credentials are never touched. All storage gates are in bytes.
BACKUP_ROOT="${OPIP_BACKUP_ROOT:-/var/backups/opip-postgres}"
STATE_ROOT="${OPIP_BACKUP_STATE_ROOT:-/var/lib/opip-data-platform}"
LOCK_FILE="${OPIP_DRILL_LOCK_FILE:-/var/lock/opip-postgres-restore-drill.lock}"
PLANE_LOCK_FILE="${OPIP_PLANE_LOCK_FILE:-/var/lock/opip-learning-plane.lock}"
LIVE_DATA_DIR="${OPIP_DRILL_LIVE_DATA_DIR:-/var/lib/opip-data-platform/postgres}"
WORK_ROOT="${OPIP_DRILL_WORK_ROOT:-/var/lib/opip-data-platform/restore-drill}"
IMAGE="${OPIP_DRILL_IMAGE:-postgres:17-alpine}"
RESERVE_BYTES=$((3 * 1024 * 1024 * 1024))     # normal reserve that must remain free
MIN_FREE_BYTES=$((2 * 1024 * 1024 * 1024))    # hard floor: the drill is killed below it

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

free_bytes() { df -PB1 "$STATE_ROOT" | awk 'NR == 2 {print $4}'; }

# Space requirement is measured, not assumed: the live data directory's size (an upper
# bound for a freshly restored copy) plus 25% for index builds and temp files, on top
# of the 3 GiB reserve. The dump size is irrelevant to the expanded size.
live_bytes="$(du -sb "$LIVE_DATA_DIR" | awk '{print $1}')"
[[ "$live_bytes" =~ ^[0-9]+$ ]] || {
  echo "CRITICAL: live data size could not be measured; restore drill refused" >&2
  exit 75
}
need_bytes=$((live_bytes + live_bytes / 4))
free_before="$(free_bytes)"
[[ "$free_before" =~ ^[0-9]+$ ]] || {
  echo "CRITICAL: free space could not be read; restore drill refused" >&2
  exit 75
}
if (( free_before < RESERVE_BYTES + need_bytes )); then
  echo "CRITICAL: free ${free_before} bytes < reserve ${RESERVE_BYTES} + required ${need_bytes}; restore drill refused, nothing changed" >&2
  exit 75
fi

stamp="$(date -u +%Y%m%d%H%M%S)"
database="opip_restore_drill_$stamp"
[[ "$database" =~ ^opip_restore_drill_[0-9]{14}$ ]] || exit 70
cname="opip-restore-drill-$stamp"

install -d -m 0700 "$WORK_ROOT"
work="$(mktemp -d "$WORK_ROOT/drill.XXXXXX")"
[[ "$work" == "$WORK_ROOT"/drill.* ]] || exit 70
min_free="$free_before"
sampler=""
cleanup() {
  [[ -n "$sampler" ]] && kill "$sampler" 2>/dev/null || true
  docker exec "$cname" dropdb --if-exists --force --username drill "$database" >/dev/null 2>&1 || true
  docker rm -f "$cname" >/dev/null 2>&1 || true
  [[ -n "$work" && "$work" == "$WORK_ROOT"/drill.* ]] && rm -rf -- "$work"
}
trap cleanup EXIT INT TERM

docker run -d --name "$cname" --network none --memory 384m --memory-swap 384m --cpus 0.5 \
  -e POSTGRES_USER=drill -e POSTGRES_HOST_AUTH_METHOD=trust \
  -v "$work:/var/lib/postgresql/data" "$IMAGE" \
  postgres -c shared_buffers=64MB -c fsync=off -c max_wal_size=512MB > /dev/null
# The official image first runs a TEMPORARY server that listens on the unix socket only,
# then restarts the real one. Checking over TCP loopback (--network none still has lo)
# succeeds only once the real server is up, so the restore cannot race the restart.
ready=0
for _ in $(seq 1 "${OPIP_DRILL_READY_TRIES:-90}"); do
  if docker exec "$cname" pg_isready --host 127.0.0.1 --username drill > /dev/null 2>&1; then
    ready=1
    break
  fi
  sleep "${OPIP_DRILL_READY_SLEEP:-1}"
done
if (( ready != 1 )); then
  echo "CRITICAL: throwaway postgres did not become ready; drill aborted, nothing recorded" >&2
  docker logs --tail 20 "$cname" >&2 || true
  exit 1
fi

# Hard-floor watchdog: below 2 GiB free the throwaway container is killed, the restore
# fails closed and cleanup removes everything the drill wrote.
(
  while sleep 5; do
    f="$(free_bytes)"
    [[ "$f" =~ ^[0-9]+$ ]] || continue
    (( f < min_free )) && { min_free="$f"; echo "$f" > "$work.min"; }
    (( f < MIN_FREE_BYTES )) && docker rm -f "$cname" > /dev/null 2>&1
  done
) > /dev/null 2>&1 &
sampler=$!

docker exec "$cname" createdb --username drill "$database"
docker exec -i "$cname" pg_restore --exit-on-error --no-owner --no-privileges \
  --username drill --dbname "$database" < "$backup"
schema_version="$(
  docker exec "$cname" psql --no-psqlrc --tuples-only --no-align \
    --username drill --dbname "$database" \
    --command "SELECT max(version) FROM ops.schema_version"
)"
[[ "$schema_version" =~ ^[0-9]+$ ]] || {
  echo "restored database did not contain a valid O'Pip schema" >&2
  exit 1
}
restored_bytes="$(du -sb "$work" | awk '{print $1}')"
free_after="$(free_bytes)"
[[ -f "$work.min" ]] && min_free="$(cat "$work.min")"

temporary="$(mktemp "$STATE_ROOT/last-restore-drill.env.XXXXXX")"
{
  printf 'verified_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'backup_file=%s\n' "$(basename "$backup")"
  printf 'schema_version=%s\n' "$schema_version"
  printf 'isolated=true\n'
  printf 'live_data_bytes=%s\n' "$live_bytes"
  printf 'required_bytes_estimate=%s\n' "$need_bytes"
  printf 'restored_data_bytes=%s\n' "$restored_bytes"
  printf 'free_bytes_before=%s\n' "$free_before"
  printf 'min_free_bytes_observed=%s\n' "$min_free"
  printf 'free_bytes_after_restore=%s\n' "$free_after"
} > "$temporary"
chown root:root "$temporary"
chmod 0600 "$temporary"
mv -f -- "$temporary" "$STATE_ROOT/last-restore-drill.env"
rm -f -- "$work.min"

echo "O'Pip PostgreSQL restore drill succeeded"
echo "schema_version=$schema_version restored_data_bytes=$restored_bytes min_free_bytes_observed=$min_free"
