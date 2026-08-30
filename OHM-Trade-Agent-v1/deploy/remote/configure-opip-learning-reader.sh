#!/usr/bin/env bash
set -Eeuo pipefail

PUBLIC_KEY="${1:-}"
SOURCE_CIDR="${2:-}"
APP_ROOT="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
USER_NAME="opiplearn"
HOME_DIR="/home/$USER_NAME"
AUTHORIZED_KEYS="$HOME_DIR/.ssh/authorized_keys"
EXPORT_ROOT="/var/lib/opip-learning-export"
FORCED_COMMAND="/usr/local/sbin/opip-learning-read-export"

if [[ -z "$PUBLIC_KEY" || "$PUBLIC_KEY" != ssh-ed25519\ * || -z "$SOURCE_CIDR" ]]; then
  echo "usage: $0 'ssh-ed25519 AAAA... opip-learning-worker' <learning-private-ip/32>" >&2
  exit 64
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run production learning-reader setup as root" >&2
  exit 77
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$USER_NAME"
fi
passwd -l "$USER_NAME" >/dev/null 2>&1 || true

install -o root -g root -m 0755   "$APP_ROOT/deploy/remote/opip-learning-read-export.sh"   "$FORCED_COMMAND"

install -d -o root -g "$USER_NAME" -m 0750 "$EXPORT_ROOT"
for name in .publish.lock p1_shadow_outbox.jsonl full_market_observations.jsonl manifest.env; do
  if [[ -e "$EXPORT_ROOT/$name" ]]; then
    chown root:"$USER_NAME" "$EXPORT_ROOT/$name"
    chmod 0640 "$EXPORT_ROOT/$name"
  fi
done

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0700 "$HOME_DIR/.ssh"
touch "$AUTHORIZED_KEYS"
chown "$USER_NAME:$USER_NAME" "$AUTHORIZED_KEYS"
chmod 0600 "$AUTHORIZED_KEYS"

tmp="$(mktemp)"
grep -v 'opip-learning-worker' "$AUTHORIZED_KEYS" > "$tmp" || true
printf 'from="%s",restrict,command="%s" %s\n'   "$SOURCE_CIDR" "$FORCED_COMMAND" "$PUBLIC_KEY" >> "$tmp"
install -o "$USER_NAME" -g "$USER_NAME" -m 0600 "$tmp" "$AUTHORIZED_KEYS"
rm -f "$tmp"

echo "O'Pip production evidence reader configured."
echo "user=$USER_NAME"
echo "source=$SOURCE_CIDR"
echo "forced_command=$FORCED_COMMAND"
echo "sudo_authority=NONE"
echo "interactive_shell_authority=NONE_FOR_THIS_KEY"
