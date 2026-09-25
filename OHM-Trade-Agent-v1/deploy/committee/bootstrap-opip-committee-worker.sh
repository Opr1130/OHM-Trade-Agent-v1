#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — install the isolated shadow worker (IC-042).
#
# RUNS ON THE LEARNING/ANALYTICS PLANE, AS ROOT. This mirrors the learning worker's
# bootstrap so the Committee worker is installed the same way that plane already
# installs its own units. Placement is deliberate: the Committee worker never shares
# a host with order authority.
#
# This installs an INERT worker. OPIP_COMMITTEE_MODE stays `off`, so the worker runs
# no provider call and creates no case. Activating SHADOW is a separate approval.
#
# Usage:
#   bootstrap-opip-committee-worker.sh <40-char-release-sha> [--enable-timer]
#
# Without --enable-timer the units are installed but the timer stays disabled and
# inactive, so no scheduled committee execution happens at all. The owner-gated
# `/deploy-committee` workflow deliberately uses that path: the initial deployment
# installs and proves OFF-mode isolation without enabling any recurring work.
# Timer activation belongs to the later, separately OWNER-authorised
# OFF -> credentialled SHADOW activation boundary.
#
# With --enable-timer the timer starts and the worker runs cycles that do nothing but
# record an OFF disposition. It remains no-provider-egress either way, because mode
# is off.
set -Eeuo pipefail

TARGET_SHA="${1:-}"
ENABLE_TIMER="false"
for arg in "${@:2}"; do
  case "$arg" in
    --enable-timer) ENABLE_TIMER="true" ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 64
      ;;
  esac
done

UNIT_DIR="/etc/systemd/system"
SBIN_DIR="/usr/local/sbin"
CONF_DIR="/etc/opip"
ENV_FILE="$CONF_DIR/committee-credentials.env"
COMMITTEE_HOME="/var/lib/opip-committee"
EVIDENCE_ROOT="/var/lib/opip-learning"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <40-char-release-sha> [--enable-timer]" >&2
  echo "a branch name cannot identify a released artifact" >&2
  exit 64
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run the committee worker bootstrap as root" >&2
  exit 77
fi

# The evidence input must already exist and be readable: the worker reads committed
# evidence and must not be the thing that creates the learning plane's data root.
if [[ ! -d "$EVIDENCE_ROOT" ]]; then
  echo "refusing to install: $EVIDENCE_ROOT does not exist" >&2
  echo "the evidence input must pre-exist; the Committee worker does not create it" >&2
  exit 78
fi

# --- advisory output, isolated from every other writer ----------------------
install -d -m 0750 -o root -g root "$COMMITTEE_HOME"
# The advisory directory is writable only by root. Nothing production-side reads it.
chmod 0750 "$COMMITTEE_HOME"

# --- environment file, root-owned and unreadable by others -------------------
install -d -m 0750 -o root -g root "$CONF_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
  install -m 0600 -o root -g root \
    "$SOURCE_DIR/committee-credentials.env.example" "$ENV_FILE"
  echo "installed a template at $ENV_FILE; populate the credentials before SHADOW"
fi
chmod 0600 "$ENV_FILE"
chown root:root "$ENV_FILE"

# The release SHA is non-secret and is set here so the worker cannot start without
# an exact release identity.
if grep -q '^OPIP_COMMITTEE_RELEASE_SHA=' "$ENV_FILE"; then
  sed -i "s|^OPIP_COMMITTEE_RELEASE_SHA=.*|OPIP_COMMITTEE_RELEASE_SHA=${TARGET_SHA}|" "$ENV_FILE"
else
  printf 'OPIP_COMMITTEE_RELEASE_SHA=%s\n' "$TARGET_SHA" >> "$ENV_FILE"
fi

# Mode is forced to off at install. Activation is a separate, approved change.
if grep -q '^OPIP_COMMITTEE_MODE=' "$ENV_FILE"; then
  sed -i 's|^OPIP_COMMITTEE_MODE=.*|OPIP_COMMITTEE_MODE=off|' "$ENV_FILE"
else
  printf 'OPIP_COMMITTEE_MODE=off\n' >> "$ENV_FILE"
fi

# --- units ------------------------------------------------------------------
install -m 0644 -o root -g root "$SOURCE_DIR/opip-committee-shadow.service" "$UNIT_DIR/"
install -m 0644 -o root -g root "$SOURCE_DIR/opip-committee-shadow.timer" "$UNIT_DIR/"
install -m 0755 -o root -g root "$SOURCE_DIR/run-committee-shadow-cycle.sh" "$SBIN_DIR/"

systemctl daemon-reload

# Provision the exact release tree and virtualenv while mode is still off.
# The timer decision below is unchanged: /deploy-committee does not pass
# --enable-timer, so the timer stays disabled after provisioning.
"$SOURCE_DIR/provision-committee-host-runtime.sh" "$TARGET_SHA"

# The service is a `Type=oneshot` unit with no `[Install]` section: it is started by
# the timer, never enabled on its own. Calling `systemctl enable` on it would fail
# ("no installation config") and, under `set -e`, would abort the install. Enablement
# of the scheduled path is therefore expressed solely through the timer below.
if [[ "$ENABLE_TIMER" == "true" ]]; then
  systemctl enable --now opip-committee-shadow.timer
  echo "committee timer enabled; mode is off so cycles perform no provider call"
else
  # Initial OFF installation: units are installed but the timer stays disabled and
  # inactive, so no scheduled committee execution happens at all.
  systemctl disable opip-committee-shadow.timer >/dev/null 2>&1 || true
  echo "committee units installed; timer disabled and NOT started (use --enable-timer to start it)"
fi

echo
echo "installed at release $TARGET_SHA with OPIP_COMMITTEE_MODE=off"
echo "verify isolation with: $SOURCE_DIR/verify-committee-isolation.sh"
echo "verify runtime with: $SOURCE_DIR/verify-committee-runtime.sh"
