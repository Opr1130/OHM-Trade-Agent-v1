#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SHA="${1:-}"
PRODUCTION_HOST="${2:-}"
PRODUCTION_USER="${3:-opiplearn}"

REPO_URL="https://github.com/Opr1130/OHM-Trade-Agent-v1.git"
ROOT="/opt/opip-learning"
REPO_ROOT="$ROOT/repo"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
DATA_ROOT="/var/lib/opip-learning"
ENV_FILE="/etc/opip-learning.env"
SSH_KEY="/root/.ssh/opip-learning"

if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <40-char-main-sha> <production-private-host> [production-user]" >&2
  exit 64
fi
if [[ -z "$PRODUCTION_HOST" ]]; then
  echo "production private host is required" >&2
  exit 64
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run learning worker bootstrap as root" >&2
  exit 77
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 git openssh-client ca-certificates

systemctl enable --now docker
install -d -o root -g root -m 0755 "$ROOT" "$DATA_ROOT/data" "$DATA_ROOT/state"
install -d -o root -g root -m 0700 /root/.ssh

if [[ ! -d "$REPO_ROOT/.git" ]]; then
  git clone "$REPO_URL" "$REPO_ROOT"
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

IMAGE="opip-learning:$TARGET_SHA"
docker build -t "$IMAGE" "$APP_ROOT"

install -o root -g root -m 0755   "$APP_ROOT/deploy/learning/opip-learning-sync.sh"   /usr/local/sbin/opip-learning-sync
install -o root -g root -m 0755   "$APP_ROOT/deploy/learning/opip-learning-job.sh"   /usr/local/sbin/opip-learning-job
install -o root -g root -m 0755   "$APP_ROOT/deploy/learning/opip-learning-cleanup.sh"   /usr/local/sbin/opip-learning-cleanup

for unit in   opip-learning-sync.service opip-learning-sync.timer   opip-learning-capture.service opip-learning-capture.timer   opip-learning-outcomes.service opip-learning-outcomes.timer; do
  install -o root -g root -m 0644     "$APP_ROOT/deploy/learning/$unit"     "/etc/systemd/system/$unit"
done

if [[ ! -s "$SSH_KEY" ]]; then
  ssh-keygen -q -t ed25519 -N '' -f "$SSH_KEY" -C opip-learning-worker
fi
chmod 0600 "$SSH_KEY"
chmod 0644 "$SSH_KEY.pub"

cat > "$ENV_FILE" <<EOF
OPIP_PRODUCTION_HOST=$PRODUCTION_HOST
OPIP_PRODUCTION_USER=$PRODUCTION_USER
OPIP_PRODUCTION_EXPORT_PATH=/var/lib/opip-learning-export
OPIP_LEARNING_SSH_KEY=$SSH_KEY
OPIP_LEARNING_IMAGE=$IMAGE
OPIP_DEPLOYED_SHA=$TARGET_SHA
EOF
chmod 0600 "$ENV_FILE"

systemctl daemon-reload
# Timers intentionally remain disabled until SSH host-key validation,
# production reader authorization, and all one-shot checks have passed.

echo "O'Pip learning worker staged."
echo "sha=$TARGET_SHA"
echo "image=$IMAGE"
echo "production_host=$PRODUCTION_HOST"
echo
echo "Authorize this public key on production before starting timers:"
cat "$SSH_KEY.pub"
echo
echo "After production reader access and known_hosts are configured, run:"
echo "  systemctl start opip-learning-sync.service"
echo "  systemctl start opip-learning-capture.service"
echo "  systemctl start opip-learning-outcomes.service"
echo "  systemctl enable --now opip-learning-sync.timer opip-learning-capture.timer opip-learning-outcomes.timer"
