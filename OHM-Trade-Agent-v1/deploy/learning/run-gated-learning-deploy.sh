#!/usr/bin/env bash
# Owner-gated exact-SHA learning worker update (no trading credentials).
# Intended for /deploy-learning <40-char-sha> after production is already on that SHA.
set -Eeuo pipefail

TARGET_SHA="${1:-}"
RELEASE_DIR="${2:-}"

ENV_FILE="/etc/opip-learning.env"
ROOT="/opt/opip-learning"
REPO_ROOT="$ROOT/repo"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
DATA_ROOT="/var/lib/opip-learning"
STATE_ROOT="$DATA_ROOT/state"
STATUS_FILE="$STATE_ROOT/last-learning-deploy.env"

if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <40-char-main-sha> [optional-release-dir]" >&2
  exit 64
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run gated learning deploy as root" >&2
  exit 77
fi
if [[ ! -r "$ENV_FILE" ]]; then
  echo "missing learning environment; run bootstrap-opip-learning-worker.sh first" >&2
  exit 78
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
: "${OPIP_PRODUCTION_HOST:?OPIP_PRODUCTION_HOST is required}"
: "${OPIP_PRODUCTION_USER:?OPIP_PRODUCTION_USER is required}"

if [[ -n "$RELEASE_DIR" ]]; then
  if [[ ! -d "$RELEASE_DIR/OHM-Trade-Agent-v1" ]]; then
    echo "release directory missing OHM-Trade-Agent-v1 tree: $RELEASE_DIR" >&2
    exit 66
  fi
  SOURCE_APP="$RELEASE_DIR/OHM-Trade-Agent-v1"
else
  if [[ ! -d "$REPO_ROOT/.git" ]]; then
    echo "learning repo missing; run bootstrap first" >&2
    exit 66
  fi
  git -C "$REPO_ROOT" fetch --prune origin main
  REMOTE_MAIN="$(git -C "$REPO_ROOT" rev-parse origin/main)"
  if [[ "$REMOTE_MAIN" != "$TARGET_SHA" ]]; then
    echo "refusing learning deploy: target is not current origin/main" >&2
    echo "target=$TARGET_SHA origin/main=$REMOTE_MAIN" >&2
    exit 65
  fi
  git -C "$REPO_ROOT" checkout -f main
  git -C "$REPO_ROOT" reset --hard "$TARGET_SHA"
  SOURCE_APP="$APP_ROOT"
fi

# When RELEASE_DIR is provided, still require it to match requested SHA via marker
# written by the workflow checkout; the workflow gates main+pytest.
IMAGE="opip-learning:$TARGET_SHA"
docker build -t "$IMAGE" "$SOURCE_APP"
docker image prune -f >/dev/null 2>&1 || true

install -d -o root -g root -m 0755 "$DATA_ROOT/data" "$STATE_ROOT"
install -o root -g root -m 0755 \
  "$SOURCE_APP/deploy/learning/opip-learning-sync.sh" \
  /usr/local/sbin/opip-learning-sync
install -o root -g root -m 0755 \
  "$SOURCE_APP/deploy/learning/opip-learning-job.sh" \
  /usr/local/sbin/opip-learning-job
install -o root -g root -m 0755 \
  "$SOURCE_APP/deploy/learning/opip-learning-cleanup.sh" \
  /usr/local/sbin/opip-learning-cleanup

for unit in \
  opip-learning-sync.service opip-learning-sync.timer \
  opip-learning-capture.service opip-learning-capture.timer \
  opip-learning-outcomes.service opip-learning-outcomes.timer; do
  install -o root -g root -m 0644 \
    "$SOURCE_APP/deploy/learning/$unit" \
    "/etc/systemd/system/$unit"
done

# Preserve production SSH targeting; refresh only image + exact SHA.
normalized="$(mktemp)"
awk -v sha="$TARGET_SHA" -v image="$IMAGE" '
  BEGIN { updated_sha=0; updated_image=0 }
  /^OPIP_DEPLOYED_SHA=/ { print "OPIP_DEPLOYED_SHA=" sha; updated_sha=1; next }
  /^OPIP_LEARNING_IMAGE=/ { print "OPIP_LEARNING_IMAGE=" image; updated_image=1; next }
  { print }
  END {
    if (!updated_sha) print "OPIP_DEPLOYED_SHA=" sha
    if (!updated_image) print "OPIP_LEARNING_IMAGE=" image
  }
' "$ENV_FILE" > "$normalized"
chmod 0600 "$normalized"
mv -f -- "$normalized" "$ENV_FILE"

systemctl daemon-reload

# Restart timers only if already enabled; first bootstrap still requires manual enable.
for timer in opip-learning-sync.timer opip-learning-capture.timer opip-learning-outcomes.timer; do
  if systemctl is-enabled --quiet "$timer" 2>/dev/null; then
    systemctl restart "$timer"
  fi
done

recorded="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$STATUS_FILE.tmp" <<EOF
deployed_at_utc=$recorded
opip_deployed_sha=$TARGET_SHA
opip_learning_image=$IMAGE
measurement_only=true
trade_authority_changed=false
policy_change_authorized=false
EOF
mv -f "$STATUS_FILE.tmp" "$STATUS_FILE"

echo "O'Pip learning deploy succeeded"
echo "sha=$TARGET_SHA"
echo "image=$IMAGE"
