#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/opt/OHM-Trade-Agent-v1"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
DEPLOY_USER="ohmdeploy"
DEPLOY_HOME="/home/$DEPLOY_USER"
SSH_DIR="$DEPLOY_HOME/.ssh"
KEY_DIR="/root/.ohm-remote-ops"
PRIVATE_KEY="$KEY_DIR/github-actions-deploy"
PUBLIC_KEY="$PRIVATE_KEY.pub"
DEPLOY_SCRIPT_SRC="$APP_ROOT/deploy/remote/ohm-deploy"
SSH_GATEWAY_SRC="$APP_ROOT/deploy/remote/ohm-deploy-ssh"
DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"
SSH_GATEWAY_DST="/usr/local/sbin/ohm-deploy-ssh"
SUDOERS_FILE="/etc/sudoers.d/ohmdeploy"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run this bootstrap with sudo" >&2
  exit 77
fi

for cmd in git docker curl flock ssh-keygen install visudo; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "missing required command: $cmd" >&2
    exit 69
  }
done

if [[ ! -d "$REPO_ROOT/.git" ]]; then
  echo "repository not found at $REPO_ROOT" >&2
  exit 69
fi

if [[ ! -f "$DEPLOY_SCRIPT_SRC" || ! -f "$SSH_GATEWAY_SRC" ]]; then
  echo "remote deploy scripts are missing from $APP_ROOT/deploy/remote" >&2
  exit 69
fi

install -o root -g root -m 0755 "$DEPLOY_SCRIPT_SRC" "$DEPLOY_SCRIPT_DST"
install -o root -g root -m 0755 "$SSH_GATEWAY_SRC" "$SSH_GATEWAY_DST"

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --home-dir "$DEPLOY_HOME" --shell /bin/bash "$DEPLOY_USER"
fi
passwd -l "$DEPLOY_USER" >/dev/null 2>&1 || true

install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0700 "$SSH_DIR"
install -d -o root -g root -m 0700 "$KEY_DIR"

if [[ ! -f "$PRIVATE_KEY" || ! -f "$PUBLIC_KEY" ]]; then
  rm -f "$PRIVATE_KEY" "$PUBLIC_KEY"
  ssh-keygen -q -t ed25519 -N '' -C 'ohm-github-actions-deploy' -f "$PRIVATE_KEY"
fi
chmod 0600 "$PRIVATE_KEY"
chmod 0644 "$PUBLIC_KEY"

PUB="$(cat "$PUBLIC_KEY")"
printf 'restrict,command="%s" %s\n' "$SSH_GATEWAY_DST" "$PUB" > "$SSH_DIR/authorized_keys"
chown "$DEPLOY_USER:$DEPLOY_USER" "$SSH_DIR/authorized_keys"
chmod 0600 "$SSH_DIR/authorized_keys"

cat > "$SUDOERS_FILE" <<EOF
$DEPLOY_USER ALL=(root) NOPASSWD: $DEPLOY_SCRIPT_DST *
EOF
chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" >/dev/null

install -d -o root -g root -m 0755 /var/lib/ohm-deploy

REPO_OWNER="$(stat -c '%U' "$REPO_ROOT/.git")"
MAIN_SHA="$(sudo -u "$REPO_OWNER" git -C "$REPO_ROOT" rev-parse main)"
printf '%s\n' "$MAIN_SHA" > /var/lib/ohm-deploy/last-good-sha

HOST="$(curl -4fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
if [[ -z "$HOST" ]]; then
  HOST="<YOUR_DROPLET_PUBLIC_IP_OR_DNS>"
fi
PORT="${OHM_DEPLOY_PORT:-22}"
HOST_KEY_LINE=""
if [[ -r /etc/ssh/ssh_host_ed25519_key.pub ]]; then
  read -r KEY_TYPE KEY_DATA _ < /etc/ssh/ssh_host_ed25519_key.pub
  if [[ -n "${KEY_TYPE:-}" && -n "${KEY_DATA:-}" ]]; then
    if [[ "$PORT" == "22" ]]; then
      HOST_KEY_LINE="$HOST $KEY_TYPE $KEY_DATA"
    else
      HOST_KEY_LINE="[$HOST]:$PORT $KEY_TYPE $KEY_DATA"
    fi
  fi
fi

cat <<EOF

OHM remote operations bootstrap: INSTALLED

GitHub production secrets to configure:

OHM_DEPLOY_HOST=$HOST
OHM_DEPLOY_USER=$DEPLOY_USER
OHM_DEPLOY_PORT=$PORT

OHM_DEPLOY_SSH_KEY (copy the complete block below):
-----BEGIN OHM_DEPLOY_SSH_KEY-----
$(cat "$PRIVATE_KEY")
-----END OHM_DEPLOY_SSH_KEY-----

OHM_DEPLOY_KNOWN_HOSTS (copy the line below):
${HOST_KEY_LINE:-<host ed25519 key unavailable; obtain it from the droplet console or a trusted machine>}

IMPORTANT:
- Verify OHM_DEPLOY_HOST is the intended production droplet before saving GitHub secrets.
- The deploy SSH key is forced-command only: it cannot open an interactive shell.
- Deployment accepts only an exact 40-character commit SHA that equals current origin/main.
- Kraken credentials and trading permissions are not changed by this bootstrap.

Private deploy key is retained root-only at:
$PRIVATE_KEY

After GitHub secrets are configured, the deploy workflow can operate end-to-end.
EOF
