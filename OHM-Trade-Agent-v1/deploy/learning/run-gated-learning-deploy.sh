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

state_value() {
  local file="$1"
  local key="$2"
  awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$file" 2>/dev/null || true
}

report_one_shot_failure() {
  local service="$1"
  local disposition_file="$2"
  echo "O'Pip learning activation one-shot failed: service=$service" >&2
  if [[ -r "$disposition_file" ]]; then
    echo "--- disposition ---" >&2
    cat "$disposition_file" >&2
  else
    echo "disposition_file=MISSING" >&2
  fi
  echo "--- systemd status ---" >&2
  systemctl status "$service" --no-pager -l >&2 || true
  echo "--- recent journal ---" >&2
  journalctl -u "$service" -n 160 --no-pager >&2 || true
}

TIMERS=(
  opip-learning-sync.timer
  opip-learning-capture.timer
  opip-learning-outcomes.timer
)
all_enabled=true
for timer in "${TIMERS[@]}"; do
  if ! systemctl is-enabled --quiet "$timer" 2>/dev/null; then
    all_enabled=false
    break
  fi
done

if [[ "$all_enabled" == "true" ]]; then
  # Normal subsequent update: preserve the already-validated scheduling state.
  for timer in "${TIMERS[@]}"; do
    systemctl restart "$timer"
  done
else
  # Bootstrap completion is fail-closed. A successful image deployment alone
  # is not enough to activate compute. Prove sync + exact release compatibility
  # + governed consumption first, then publish the fresh heartbeat, and only
  # then enable recurring timers.
  echo "O'Pip learning timers not fully enabled; running one-shot activation gates"
  systemctl stop "${TIMERS[@]}" >/dev/null 2>&1 || true

  systemctl start opip-learning-sync.service
  MANIFEST="$DATA_ROOT/data/manifest.env"
  production_sha="$(state_value "$MANIFEST" production_deployed_sha)"
  retired="$(state_value "$MANIFEST" p1_shadow_outbox_retired)"
  if [[ "$production_sha" != "$TARGET_SHA" || "$retired" != "1" ]]; then
    echo "refusing learning timer activation: sync did not prove exact schema-4 production release" >&2
    echo "worker=$TARGET_SHA production=${production_sha:-MISSING} p1_retired=${retired:-MISSING}" >&2
    exit 75
  fi

  systemctl start opip-learning-capture.service
  capture_disposition="$(state_value "$STATE_ROOT/capture.disposition.env" disposition)"
  capture_release="$(state_value "$STATE_ROOT/capture.disposition.env" release_compatibility_status)"
  if [[ "$capture_disposition" != "CONSUMED_EMPTY" || "$capture_release" != "CURRENT" ]]; then
    echo "refusing learning timer activation: capture one-shot was not governed consumed-empty/current" >&2
    echo "capture_disposition=${capture_disposition:-MISSING} release=${capture_release:-MISSING}" >&2
    exit 75
  fi

  if ! systemctl start opip-learning-outcomes.service; then
    report_one_shot_failure \
      opip-learning-outcomes.service \
      "$STATE_ROOT/outcomes.disposition.env"
    exit 75
  fi
  outcomes_disposition="$(state_value "$STATE_ROOT/outcomes.disposition.env" disposition)"
  outcomes_release="$(state_value "$STATE_ROOT/outcomes.disposition.env" release_compatibility_status)"
  if [[ "$outcomes_release" != "CURRENT" \
     || ( "$outcomes_disposition" != "CONSUMED_OK" && "$outcomes_disposition" != "CONSUMED_EMPTY" ) ]]; then
    echo "refusing learning timer activation: outcomes one-shot was not governed consumed/current" >&2
    echo "outcomes_disposition=${outcomes_disposition:-MISSING} release=${outcomes_release:-MISSING}" >&2
    exit 75
  fi

  # A second sync publishes the new capture/outcomes dispositions and the
  # current worker SHA back to the production-side diagnostic heartbeat.
  systemctl start opip-learning-sync.service

  mapfile -t remaining_jobs < <(docker ps -aq --filter label=com.opip.learning.job)
  if (( ${#remaining_jobs[@]} > 0 )); then
    echo "refusing learning timer activation: orphan learning job containers remain" >&2
    printf 'container=%s\n' "${remaining_jobs[@]}" >&2
    exit 75
  fi

  systemctl enable --now "${TIMERS[@]}"
  for timer in "${TIMERS[@]}"; do
    systemctl is-enabled --quiet "$timer"
  done
  echo "O'Pip learning timers activated after successful one-shot gates"
fi

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
