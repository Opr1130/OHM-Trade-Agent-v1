#!/bin/sh
set -eu

HOSTNAME_EXPECTED="161-35-106-207.sslip.io"
SNIPPET_DST="/etc/nginx/snippets/ohm-dashboard-locations.conf"
SNIPPET_SRC="/opt/ohm-dashboard/dashboard-locations.conf"
INCLUDE_LINE="    include /etc/nginx/snippets/ohm-dashboard-locations.conf;"
TARGET=""
BACKUP=""
SNIPPET_BACKUP=""

find_target() {
  for candidate in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf; do
    [ -f "$candidate" ] || continue
    if grep -q "server_name[[:space:]].*${HOSTNAME_EXPECTED}" "$candidate" \
       && grep -q "/webhooks/tradingview/v2" "$candidate"; then
      readlink -f "$candidate"
      return 0
    fi
  done
  return 1
}

restore() {
  rc=$?
  trap - EXIT INT TERM
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ] && [ -n "$TARGET" ]; then
    cp "$BACKUP" "$TARGET"
  fi
  if [ -n "$SNIPPET_BACKUP" ] && [ -f "$SNIPPET_BACKUP" ]; then
    cp "$SNIPPET_BACKUP" "$SNIPPET_DST"
  else
    rm -f "$SNIPPET_DST"
  fi
  if [ -r /run/nginx.pid ]; then
    kill -HUP "$(cat /run/nginx.pid)" 2>/dev/null || true
  fi
  echo "O'Pip dashboard edge installation failed; prior Nginx configuration restored." >&2
  exit "$rc"
}

TARGET="$(find_target)" || {
  echo "Could not identify the production Nginx server block for ${HOSTNAME_EXPECTED}." >&2
  exit 69
}

mkdir -p /etc/nginx/snippets
BACKUP="${TARGET}.pre-ohm-dashboard"
cp "$TARGET" "$BACKUP"

if [ -f "$SNIPPET_DST" ]; then
  SNIPPET_BACKUP="${SNIPPET_DST}.pre-ohm-dashboard"
  cp "$SNIPPET_DST" "$SNIPPET_BACKUP"
fi

trap restore EXIT INT TERM
cp "$SNIPPET_SRC" "$SNIPPET_DST"
chmod 0644 "$SNIPPET_DST"

if ! grep -Fq "include /etc/nginx/snippets/ohm-dashboard-locations.conf;" "$TARGET"; then
  tmp="${TARGET}.ohm-dashboard.tmp"
  awk -v include_line="$INCLUDE_LINE" '
    BEGIN { inserted=0 }
    {
      if (!inserted && $0 ~ /^[[:space:]]*location[[:space:]]+\/[[:space:]]*\{/) {
        print include_line
        inserted=1
      }
      print
    }
    END {
      if (!inserted) exit 42
    }
  ' "$TARGET" > "$tmp"
  mv "$tmp" "$TARGET"
fi

nginx -t

PID="$(cat /run/nginx.pid)"
kill -HUP "$PID"
sleep 1

BODY="$(wget -qO- --no-check-certificate \
  --header="Host: ${HOSTNAME_EXPECTED}" \
  https://127.0.0.1/dashboard)"
printf '%s' "$BODY" | grep -Fq "O’Pip Intelligence Cockpit"

trap - EXIT INT TERM
rm -f "$BACKUP" "$SNIPPET_BACKUP" 2>/dev/null || true
echo "OHM dashboard edge ready: https://${HOSTNAME_EXPECTED}/dashboard"
