#!/usr/bin/env bash
set -Eeuo pipefail

PUBLIC_KEY="${1:-}"
USER_NAME="opiplearn"
HOME_DIR="/home/$USER_NAME"
AUTHORIZED_KEYS="$HOME_DIR/.ssh/authorized_keys"
EXPORT_ROOT="/var/lib/opip-learning-export"

if [[ -z "$PUBLIC_KEY" || "$PUBLIC_KEY" != ssh-ed25519\ * ]]; then
  echo "usage: $0 'ssh-ed25519 AAAA... opip-learning-worker'" >&2
  exit 64
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run production learning-reader setup as root" >&2
  exit 77
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$USER_NAME"
fi

install -d -o root -g root -m 0755 "$EXPORT_ROOT"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0700 "$HOME_DIR/.ssh"
touch "$AUTHORIZED_KEYS"
chown "$USER_NAME:$USER_NAME" "$AUTHORIZED_KEYS"
chmod 0600 "$AUTHORIZED_KEYS"

tmp="$(mktemp)"
grep -v 'opip-learning-worker' "$AUTHORIZED_KEYS" > "$tmp" || true
printf 'restrict %s\n' "$PUBLIC_KEY" >> "$tmp"
install -o "$USER_NAME" -g "$USER_NAME" -m 0600 "$tmp" "$AUTHORIZED_KEYS"
rm -f "$tmp"

echo "O'Pip production evidence reader configured."
echo "user=$USER_NAME"
echo "export_root=$EXPORT_ROOT"
echo "sudo_authority=NONE"
