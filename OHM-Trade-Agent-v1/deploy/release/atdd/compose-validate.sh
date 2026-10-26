#!/usr/bin/env bash
# Combined base+artifact Compose validation (Linux, disposable runner only).
#
# Proves the artifact overlay composes correctly on top of the real base file
# with synthetic, non-secret environment values, and that the negative cases
# fail closed. No production access, no registry, no build.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."   # -> OHM-Trade-Agent-v1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s (%s)\n' "$1" "$2"; }

APP_DIGEST="ghcr.io/opip/app@sha256:$(printf 'a%.0s' {1..64})"
WRITER_DIGEST="ghcr.io/opip/writer@sha256:$(printf 'b%.0s' {1..64})"
APP_CONFIG_ID="sha256:$(printf 'c%.0s' {1..64})"
WRITER_CONFIG_ID="sha256:$(printf 'd%.0s' {1..64})"

# --- synthetic, non-secret environment ------------------------------------
# Every variable the base/paper Compose files reference gets a deterministic
# dummy value. Writing .env in the CI checkout is ephemeral and secret-free.
vars="$(grep -hoE '\$\{[A-Za-z0-9_]+' docker-compose.yml docker-compose.paper.yml 2>/dev/null \
        | sed 's/^\${//' | sort -u)"
: > .env
for v in $vars; do printf '%s=ci-synthetic-%s\n' "$v" "$v" >> .env; done
echo "synthetic_env_vars=$(printf '%s\n' "$vars" | wc -l)"

combined() {
  OPIP_APP_IMAGE="$APP_DIGEST" OPIP_WRITER_IMAGE="$WRITER_DIGEST" \
    docker compose -f docker-compose.yml -f docker-compose.artifact.yml config 2>&1
}

echo "== combined base + artifact =="
if out="$(combined)"; then
  ok "base+artifact composes with synthetic env"
else
  bad "base+artifact composes with synthetic env" "$(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
  out=""
fi

# Both services pinned by digest, never a floating tag
for pair in "ohm-trade-agent:$APP_DIGEST" "opip-canonical-writer:$WRITER_DIGEST"; do
  svc="${pair%%:*}"; ref="${pair#*:}"
  if grep -A40 "^  ${svc}:" <<< "$out" | grep -q "image: ${ref}"; then
    ok "$svc is pinned to its digest reference"
  else
    bad "$svc is pinned to its digest reference" "not found"
  fi
done

count="$(grep -c 'pull_policy: never' <<< "$out" || true)"
[ "$count" -ge 2 ] && ok "both services forbid implicit pulls (pull_policy: never)" \
  || bad "both services forbid implicit pulls" "count=$count"

# Canonical-writer independence: the app must not depend on writer health
if grep -A60 "^  ohm-trade-agent:" <<< "$out" | grep -q 'opip-canonical-writer'; then
  bad "canonical writer stays independent of the app" "depends_on leaked"
else
  ok "canonical writer stays independent of the app"
fi

# Bind-mounted runtime evidence preserved from the base file
if grep -q 'source: .*data' <<< "$out" || grep -q '/app/data' <<< "$out"; then
  ok "runtime evidence bind mount preserved"
else
  bad "runtime evidence bind mount preserved" "no data mount in combined config"
fi

# Resource limits and networks come from base, must survive the overlay
grep -q 'mem_limit\|memory:' <<< "$out" && ok "resource limits preserved" \
  || bad "resource limits preserved" "none in combined config"
grep -q 'networks:' <<< "$out" && ok "network topology preserved" \
  || bad "network topology preserved" "none in combined config"

# Negative: overlay without refs must refuse
echo "== negative =="
if docker compose -f docker-compose.yml -f docker-compose.artifact.yml config >/dev/null 2>&1; then
  bad "combined config without image refs fails closed" "unexpected success"
else
  ok "combined config without image refs fails closed"
fi

# Negative: overlay must never reintroduce a build for the pinned services
if grep -A40 '^  ohm-trade-agent:' <<< "$out" | grep -q 'dockerfile:'; then
  echo "NOTE build section present in base compose (unchanged); artifact-mode up uses --no-build"
fi
if grep -q -- '--no-build' ../../../deploy/remote/ohm-deploy 2>/dev/null || \
   grep -q -- '--no-build' ../remote/ohm-deploy; then
  ok "controller forbids implicit builds at apply time"
else
  bad "controller forbids implicit builds at apply time" "flag missing"
fi

echo "TOTAL: pass=$PASS fail=$FAIL skip=0"
[ "$FAIL" -eq 0 ] || exit 1
