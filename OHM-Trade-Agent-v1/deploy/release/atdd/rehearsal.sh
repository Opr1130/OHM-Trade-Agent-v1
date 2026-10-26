#!/usr/bin/env bash
# Real Linux/Docker rehearsal of the artifact-mode apply/rollback mechanism.
# Disposable GitHub Actions runner only: ephemeral local registry, two synthetic
# images with distinct identities, digest-pinned apply, rollback without rebuild,
# and fail-closed negatives. Executes the SHIPPED overlay + release_artifact.py
# contract against real containers. It does NOT execute the controller's health/
# quiescence/scheduler orchestration (needs production-shaped host state).
set -uo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR/../../.."   # -> app root
PY="${PYTHON:-python3}"; TOOL="deploy/release/release_artifact.py"; REG=localhost:5000
if [ ! -f "$TOOL" ] || [ ! -r "$TOOL" ]; then
  echo "FAIL  release artifact tool missing/unreadable: $TOOL" >&2
  echo "TOTAL: pass=0 fail=1 skip=0" >&2
  exit 1
fi
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s (%s)\n' "$1" "$2"; }
trap 'docker rm -f opip-rh-reg >/dev/null 2>&1 || true' EXIT

docker run -d --rm --name opip-rh-reg -p 5000:5000 registry:2 >/dev/null
for _ in $(seq 1 30); do curl -fsS "http://$REG/v2/" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://$REG/v2/" >/dev/null 2>&1 && ok "ephemeral registry live" \
  || { bad "ephemeral registry live" "unreachable"; exit 1; }

build_img() { # name tag payload
  # The base Compose file overrides CMD with `command:`, which for the real
  # services is a `python ...` entrypoint. The synthetic image therefore needs an
  # inert `python` shim so the fixture exercises image identity, not app code.
  printf 'FROM busybox:1.36\nARG R=%s\nRUN echo %s > /identity.txt\nRUN printf "#!/bin/sh\\\\nexec sleep 3600\\\\n" > /usr/local/bin/python && chmod +x /usr/local/bin/python\nLABEL org.opencontainers.image.revision=%s\nCMD ["sleep","3600"]\n' "$2" "$3" "$2" \
    | docker build -q -t "$REG/opip/$1:$2" -f - . >/dev/null
  docker push -q "$REG/opip/$1:$2" >/dev/null
  docker inspect --format '{{index .RepoDigests 0}}' "$REG/opip/$1:$2"
}
APP_REF="$(build_img app aaaa1111 a)"; WRITER_REF="$(build_img writer bbbb2222 b)"
APP_V2="$(build_img app cccc3333 c)"
[ -n "$APP_REF" ] && [ "$APP_REF" != "$WRITER_REF" ] \
  && ok "two distinct registry digests resolved" || bad "two distinct registry digests" "missing"
APP_CONFIG="$(docker inspect --format '{{.Id}}' "$APP_REF")"
"$PY" "$TOOL" registry-digest --expected "${APP_REF#*@}" --repo-digest "$APP_REF" \
  --config-id "$APP_CONFIG" >/dev/null \
  && ok "registry digest proven distinct from local config id" \
  || bad "registry digest proven distinct from local config id" "rejected"

export OPIP_APP_IMAGE="$APP_REF" OPIP_WRITER_IMAGE="$WRITER_REF"
docker compose -f docker-compose.yml -f docker-compose.artifact.yml up -d --no-build \
  ohm-trade-agent opip-canonical-writer >/tmp/apply.log 2>&1 \
  && ok "apply started both services with --no-build" \
  || bad "apply started both services with --no-build" "$(tail -2 /tmp/apply.log | tr '\n' ' ')"
grep -qi 'building' /tmp/apply.log && bad "apply built nothing" "build output" || ok "apply built nothing"
RUN_APP="$(docker inspect --format '{{.Image}}' ohm-trade-agent)"
RUN_W="$(docker inspect --format '{{.Image}}' opip-canonical-writer)"
[ "$RUN_APP" = "$APP_CONFIG" ] && ok "running app == pinned digest" || bad "running app == pinned digest" "$RUN_APP"
[ -n "$RUN_W" ] && [ "$RUN_W" != "$RUN_APP" ] && ok "writer identity independent" || bad "writer identity independent" "shared"

export OPIP_APP_IMAGE="$APP_V2"
docker compose -f docker-compose.yml -f docker-compose.artifact.yml up -d --no-build \
  ohm-trade-agent >/tmp/fwd.log 2>&1
NEW_APP="$(docker inspect --format '{{.Image}}' ohm-trade-agent)"
export OPIP_APP_IMAGE="$APP_REF"
docker compose -f docker-compose.yml -f docker-compose.artifact.yml up -d --no-build \
  ohm-trade-agent >/tmp/roll.log 2>&1
BACK="$(docker inspect --format '{{.Image}}' ohm-trade-agent)"
[ "$NEW_APP" != "$RUN_APP" ] && ok "forward moved the app digest" || bad "forward moved the app digest" "unchanged"
[ "$BACK" = "$RUN_APP" ] && ok "rollback restored the exact previous digest" || bad "rollback restored the exact previous digest" "$BACK"
grep -qi 'building' /tmp/roll.log && bad "rollback rebuilt nothing" "build output" || ok "rollback rebuilt nothing"
grep -qi 'git ' /tmp/roll.log && bad "rollback used no Git" "git output" || ok "rollback used no Git"

unset OPIP_APP_IMAGE OPIP_WRITER_IMAGE
docker compose -f docker-compose.yml -f docker-compose.artifact.yml up -d --no-build \
  ohm-trade-agent >/tmp/noenv.log 2>&1 \
  && bad "missing image refs fail closed" "unexpected success" || ok "missing image refs fail closed"
export OPIP_APP_IMAGE="$REG/opip/app@sha256:$(printf 'e%.0s' {1..64})" OPIP_WRITER_IMAGE="$WRITER_REF"
docker compose -f docker-compose.yml -f docker-compose.artifact.yml up -d --no-build \
  ohm-trade-agent >/tmp/absent.log 2>&1 \
  && bad "absent artifact fails closed (no pull)" "unexpected success" \
  || ok "absent artifact fails closed (no pull)"
grep -qi 'building' /tmp/absent.log && bad "absent path built nothing" "build output" || ok "absent path built nothing"

echo "TOTAL: pass=$PASS fail=$FAIL skip=0"
[ "$FAIL" -eq 0 ] || exit 1
