#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="/etc/opip-data-platform.env"
BACKUP_ROOT="/var/backups/opip-postgres"
COMPOSE="/opt/opip-learning/repo/OHM-Trade-Agent-v1/deploy/analytics/docker-compose.yml"
LOCK_FILE="/var/lock/opip-postgres-backup.lock"

[[ -r "$ENV_FILE" ]] || exit 78
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0
install -d -o root -g root -m 0700 "$BACKUP_ROOT"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="opip-postgres-$stamp.dump"
tmp="$BACKUP_ROOT/.$name.tmp"
target="$BACKUP_ROOT/$name"
trap 'rm -f -- "$tmp"' EXIT INT TERM

docker compose -f "$COMPOSE" exec -T \
  -e PGPASSWORD="$OPIP_POSTGRES_ADMIN_PASSWORD" opip-postgres \
  pg_dump --format=custom --compress=9 \
  --username "${OPIP_POSTGRES_ADMIN_USER:-opip_admin}" \
  --dbname "${OPIP_POSTGRES_DB:-opip}" > "$tmp"
mv -f -- "$tmp" "$target"
sha256sum "$target" > "$target.sha256"
chmod 0600 "$target" "$target.sha256"
sha256sum --check --status "$target.sha256"

find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump' -mtime +30 -delete
find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'opip-postgres-*.dump.sha256' -mtime +30 -delete

echo "O'Pip PostgreSQL backup created: $target"
echo "An off-host droplet backup or object-storage copy is still required."
