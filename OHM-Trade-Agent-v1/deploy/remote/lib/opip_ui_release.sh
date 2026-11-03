#!/usr/bin/env bash
# O'Pip owner-gated UI-ONLY release library (Control Center V2 edge).
#
# Sourced by deploy/remote/ohm-deploy when the forced-command gateway requests
# `deploy-ui <40-hex>` or `rollback-ui <40-hex>`. It reuses the existing control
# plane - the same SSH key and forced command, the same deploy lock, the same
# exact-main-SHA owner approval, the same release-tree artifact verification, the
# same receipt convention - and adds nothing else.
#
# What this library may do:
#   * stage the exact approved commit through the existing release-tree provider
#     (`source_prepare`), which is verified against the trusted object cache and
#     is the ONLY source of the candidate Nginx config and static UI bundle;
#   * build the V2 edge image from that verified tree and recreate EXACTLY ONE
#     container: `ohm-dashboard-edge`;
#   * restore the previous edge container on any post-cutover failure.
#
# What this library must never do (asserted by
# tests/test_opip_ui_only_release_controller_v1.py):
#   * start, stop, recreate or reconfigure ohm-trade-agent, opip-canonical-writer,
#     the scanner, F11, paper/Freqtrade, learning or analytics services;
#   * run a Compose command with `--remove-orphans` or without an explicit
#     single service name;
#   * change ports, networks, credentials, resource caps, retention or evidence;
#   * write the owner `.env`, production data or the application source tree.
#
# Caller contract (established by ohm-deploy BEFORE sourcing this file):
#   TARGET_SHA, APP_ROOT, STATE_DIR, PYTHON_BIN and OPIP_STAGING_DIR are set, the
#   deploy flock is held, `set -Eeuo pipefail` is active, and `source_prepare`
#   has already materialized the manifest-verified candidate tree in
#   OPIP_UI_STAGED_APP.
#
# Rollback model: the previous edge container identity (image, mounts, resource
# caps) is captured read-only BEFORE cutover. Failure at any post-cutover check
# restores that exact container from the tracked base Compose file with the
# overlay omitted, then re-verifies the legacy dashboard.

opip_ui_service="${OPIP_UI_EDGE_SERVICE:-ohm-dashboard-edge}"
opip_ui_image_repo="${OPIP_UI_IMAGE_REPO:-opip-dashboard-edge-v2}"
opip_ui_base_image="${OPIP_UI_BASE_IMAGE:-nginx:1.27-alpine}"
opip_ui_state="${OPIP_UI_STATE_DIR:-${STATE_DIR:?STATE_DIR is required}/ui}"
opip_ui_staged="${OPIP_UI_STAGED_APP:-}"
opip_ui_cert_dir="${OPIP_UI_CERT_DIR:-/etc/letsencrypt}"
opip_ui_cert_name="${OPIP_UI_CERT_NAME:-161-35-106-207.sslip.io}"
opip_ui_tls_host="${OPIP_UI_TLS_HOST:-178-128-147-125.sslip.io}"
opip_ui_min_free_kb="${OPIP_UI_MIN_FREE_KB:-2097152}"
opip_ui_min_avail_kb="${OPIP_UI_MIN_AVAIL_KB:-393216}"
opip_ui_http_timeout="${OPIP_UI_HTTP_TIMEOUT:-10}"
opip_ui_wait_tries="${OPIP_UI_WAIT_TRIES:-30}"
opip_ui_attempt="${OPIP_UI_ATTEMPT:-$(date -u +%Y%m%dT%H%M%SZ)}"
opip_ui_failures=""

opip_ui_say() { printf '%s\n' "$*"; }

# Record a durable failure reason without aborting: every preflight gate is
# evaluated so the receipt names ALL blockers, never just the first one.
opip_ui_fail() {
  opip_ui_failures="${opip_ui_failures}${opip_ui_failures:+|}${1}"
  printf 'OPIP_UI_BLOCKER=%s\n' "$1" >&2
}

opip_ui_ok() { [[ -z "$opip_ui_failures" ]]; }

# --- governed command wrappers ------------------------------------------------
# Every external mutation goes through these two wrappers so the UI path has a
# fixed, auditable Compose shape: base file always, overlay only from the
# verified candidate tree, one explicit service, no orphan removal.
opip_ui_compose_edge() {
  (
    cd "${APP_ROOT:?APP_ROOT is required}" || exit 1
    OPIP_DEPLOYED_SHA="$TARGET_SHA" OPIP_UI_TREE="$opip_ui_staged" \
      docker compose \
      -f "${APP_ROOT}/docker-compose.yml" \
      -f "$opip_ui_staged/deploy/control-center/docker-compose.v2.yml" \
      "$@"
  )
}

opip_ui_compose_legacy() {
  (
    cd "${APP_ROOT:?APP_ROOT is required}" || exit 1
    docker compose -f "${APP_ROOT}/docker-compose.yml" "$@"
  )
}

# Loopback membership check. SNI and Host are the served edge name; certificate
# verification is intentionally disabled here because this probe proves ROUTING
# on the private interface, not public trust. The real certificate mount is
# proven separately by `nginx -t`, and this release changes no TLS directive.
opip_ui_http_code() {
  local path="$1" method="${2:-GET}"
  curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' \
    --max-time "$opip_ui_http_timeout" \
    --insecure \
    --resolve "${opip_ui_tls_host}:8443:127.0.0.1" \
    --request "$method" \
    "https://${opip_ui_tls_host}:8443${path}" 2>/dev/null || printf '000'
}

opip_ui_http_body() {
  local path="$1"
  curl --silent --show-error \
    --max-time "$opip_ui_http_timeout" \
    --insecure \
    --resolve "${opip_ui_tls_host}:8443:127.0.0.1" \
    "https://${opip_ui_tls_host}:8443${path}" 2>/dev/null || true
}

# --- preflight (read-only: no container, image, source or data change) --------

opip_ui_require_candidate() {
  # The candidate must be the manifest-verified staged tree produced by
  # `source_prepare`. Nothing is copied from the live (older) release.
  [[ -n "$opip_ui_staged" ]] || {
    opip_ui_fail "CANDIDATE_TREE_UNSET"
    return 1
  }
  [[ -d "$opip_ui_staged" ]] || {
    opip_ui_fail "CANDIDATE_TREE_MISSING"
    return 1
  }
  local required
  for required in \
    docker-compose.yml \
    deploy/nginx/dashboard-sidecar.conf \
    deploy/control-center/docker-compose.v2.yml \
    deploy/control-center/Dockerfile.edge \
    deploy/control-center/nginx/v2.conf \
    control-center-v2/package-lock.json; do
    [[ -s "$opip_ui_staged/$required" ]] || opip_ui_fail "CANDIDATE_FILE_MISSING:${required}"
  done
  # The V2 snippet is inert without the base include, so the candidate base must
  # carry it. This is what lets a UI-only release work while the running core
  # application is an older commit.
  grep -Fq 'include /etc/nginx/opip-control-center/*.conf;' \
    "$opip_ui_staged/deploy/nginx/dashboard-sidecar.conf" 2>/dev/null \
    || opip_ui_fail "CANDIDATE_BASE_WITHOUT_V2_INCLUDE"
  # The legacy surface must still be declared by the same candidate base.
  grep -Fq 'include /etc/nginx/tradevisor/*.conf;' \
    "$opip_ui_staged/deploy/nginx/dashboard-sidecar.conf" 2>/dev/null \
    || opip_ui_fail "CANDIDATE_BASE_WITHOUT_TRADEVISOR_INCLUDE"
  grep -Fq 'location = /dashboard {' \
    "$opip_ui_staged/deploy/nginx/dashboard-sidecar.conf" 2>/dev/null \
    || opip_ui_fail "CANDIDATE_BASE_WITHOUT_LEGACY_DASHBOARD"
  opip_ui_ok
}

opip_ui_require_host_budget() {
  # Non-mutating: the UI state directory may not exist yet, so the budget is
  # measured on its existing parent (or the filesystem root).
  local free_kb avail_kb target
  target="$(dirname "$opip_ui_state")"
  [[ -d "$target" ]] || target="/"
  free_kb="$(df -Pk "$target" 2>/dev/null | awk 'NR==2{print $4}')"
  if [[ ! "$free_kb" =~ ^[0-9]+$ ]]; then
    opip_ui_fail "DISK_BUDGET_UNPROVEN"
    return 0
  fi
  (( free_kb >= opip_ui_min_free_kb )) || opip_ui_fail "DISK_BUDGET_LOW:free_kb=${free_kb}"
  avail_kb="$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null || true)"
  if [[ "$avail_kb" =~ ^[0-9]+$ ]] && (( avail_kb < opip_ui_min_avail_kb )); then
    opip_ui_fail "MEMORY_BUDGET_LOW:available_kb=${avail_kb}"
  fi
  return 0
}

opip_ui_require_runtime_inputs() {
  # Compose resolves the core service's env_file for every invocation, so the
  # filtered (or legacy owner) environment must already exist. This path never
  # creates it: `.env`/`.env.core` are the core deploy's authority.
  [[ -s "$APP_ROOT/.env.core" || -s "$APP_ROOT/.env" ]] \
    || opip_ui_fail "CORE_ENV_MISSING"
  [[ -s "$APP_ROOT/docker-compose.yml" ]] || opip_ui_fail "BASE_COMPOSE_MISSING"
  docker image inspect "$opip_ui_base_image" >/dev/null 2>&1 \
    || opip_ui_fail "BASE_EDGE_IMAGE_ABSENT:$opip_ui_base_image"
  [[ -s "$opip_ui_cert_dir/live/$opip_ui_cert_name/fullchain.pem" ]] \
    || opip_ui_fail "CERTIFICATE_MISSING:$opip_ui_cert_name"
  [[ -s "$opip_ui_cert_dir/live/$opip_ui_cert_name/privkey.pem" ]] \
    || opip_ui_fail "CERTIFICATE_KEY_MISSING:$opip_ui_cert_name"
  opip_ui_ok
}


opip_ui_require_composed_overlay() {
  # Resolve base + candidate overlay and prove the edge mount graph is exactly
  # the candidate tree and nothing else. Read-only Compose evaluation.
  local composed expect_image
  expect_image="${opip_ui_image_repo}:${TARGET_SHA}"
  if ! composed="$(opip_ui_compose_edge config --format json 2>&1)"; then
    opip_ui_fail "COMPOSE_RESOLUTION_FAILED"
    printf '%s\n' "$composed" >&2
    return 1
  fi
  printf '%s' "$composed" | \
    OPIP_UI_EXPECT_SERVICE="$opip_ui_service" \
    OPIP_UI_EXPECT_TREE="$opip_ui_staged" \
    OPIP_UI_EXPECT_IMAGE="$expect_image" \
    "$PYTHON_BIN" - <<'PY' || opip_ui_fail "COMPOSE_GRAPH_MISMATCH"
import json
import os
import sys

config = json.load(sys.stdin)["services"]
name = os.environ["OPIP_UI_EXPECT_SERVICE"]
tree = os.environ["OPIP_UI_EXPECT_TREE"]
image = os.environ["OPIP_UI_EXPECT_IMAGE"]
service = config.get(name)
assert service, "edge service missing from the composed overlay"
assert service.get("image") == image, service.get("image")
mounts = {m["target"]: m.get("source") for m in service.get("volumes", [])}
assert mounts.get("/etc/nginx/nginx.conf") == tree + "/deploy/nginx/dashboard-sidecar.conf", mounts
assert mounts.get("/etc/nginx/opip-control-center") == tree + "/deploy/control-center/nginx", mounts
assert mounts.get("/etc/letsencrypt") == "/etc/letsencrypt", mounts
assert service.get("read_only") is True, service.get("read_only")
ports = service.get("ports") or []
published = {str(port.get("published")) for port in ports}
assert published == {"8443", "443"}, published
PY
  opip_ui_ok
}

opip_ui_require_nginx_syntax() {
  # Validate the candidate base + V2 snippet with the REAL certificate mount in a
  # throwaway, port-less, network-less container of the already-present image.
  local output
  if output="$(docker run --rm --network none \
      -v "$opip_ui_staged/deploy/nginx/dashboard-sidecar.conf:/etc/nginx/nginx.conf:ro" \
      -v "$opip_ui_staged/deploy/control-center/nginx:/etc/nginx/opip-control-center:ro" \
      -v "$opip_ui_cert_dir:/etc/letsencrypt:ro" \
      "$opip_ui_base_image" nginx -t 2>&1)"; then
    opip_ui_say "OPIP_UI_NGINX_SYNTAX=PASS"
  else
    printf '%s\n' "$output" >&2
    opip_ui_fail "NGINX_SYNTAX_FAILED"
  fi
  opip_ui_ok
}

opip_ui_capture_previous_edge() {
  # Read-only capture of the running edge identity. Without a running edge there
  # is nothing to restore, so a first-time V2 install is refused: this command is
  # a side-by-side addition, never a bootstrap of the legacy edge.
  local id
  if ! install -d -m 0700 "$opip_ui_state" 2>/dev/null; then
    opip_ui_fail "UI_STATE_DIR_UNWRITABLE"
    return 1
  fi
  id="$(opip_ui_compose_legacy ps -q "$opip_ui_service" 2>/dev/null || true)"
  if [[ -z "$id" ]]; then
    opip_ui_fail "EDGE_CONTAINER_NOT_RUNNING"
    return 1
  fi
  {
    printf 'schema=1\n'
    printf 'container=%s\n' "$opip_ui_service"
    printf 'container_id=%s\n' "$id"
    printf 'image=%s\n' "$(docker inspect --format '{{.Config.Image}}' "$id" 2>/dev/null || true)"
    printf 'image_id=%s\n' "$(docker inspect --format '{{.Image}}' "$id" 2>/dev/null || true)"
    printf 'memory=%s\n' "$(docker inspect --format '{{.HostConfig.Memory}}' "$id" 2>/dev/null || true)"
    printf 'memory_swap=%s\n' "$(docker inspect --format '{{.HostConfig.MemorySwap}}' "$id" 2>/dev/null || true)"
    printf 'pids_limit=%s\n' "$(docker inspect --format '{{.HostConfig.PidsLimit}}' "$id" 2>/dev/null || true)"
    printf 'read_only=%s\n' "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$id" 2>/dev/null || true)"
    printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$opip_ui_state/previous-edge.env"
  chmod 0600 "$opip_ui_state/previous-edge.env"
  opip_ui_say "OPIP_UI_PREVIOUS_EDGE=CAPTURED image=$(sed -n 's/^image=//p' "$opip_ui_state/previous-edge.env")"
  return 0
}



# --- post-cutover verification -----------------------------------------------

opip_ui_wait_edge() {
  local tries=0 code
  while (( tries < opip_ui_wait_tries )); do
    code="$(opip_ui_http_code /dashboard)"
    [[ "$code" != "000" ]] && return 0
    tries=$((tries + 1))
    sleep 2
  done
  opip_ui_fail "EDGE_NOT_LISTENING"
  return 1
}

opip_ui_first_asset() {
  # Deterministic single asset name from the image itself, never from the host.
  docker run --rm --network none \
    --entrypoint sh "${opip_ui_image_repo}:${TARGET_SHA}" \
    -c 'set -e; test -s /usr/share/nginx/html/opip-control-center/index.html; ls /usr/share/nginx/html/opip-control-center/assets | head -n 1' \
    2>/dev/null || true
}

opip_ui_check_legacy_dashboard() {
  # Reused by cutover verification and by rollback verification, so it reports
  # only the blockers IT finds instead of the global accumulator.
  local baseline="$opip_ui_failures" code body
  code="$(opip_ui_http_code /dashboard)"
  body="$(opip_ui_http_body /dashboard)"
  [[ "$code" == "200" ]] || opip_ui_fail "LEGACY_DASHBOARD_STATUS:${code}"
  grep -Fq 'Intelligence Cockpit' <<<"$body" || opip_ui_fail "LEGACY_DASHBOARD_BODY_UNRECOGNIZED"
  # The legacy entry point keeps its redirect contract.
  [[ "$(opip_ui_http_code /dashboard/)" == "308" ]] || opip_ui_fail "LEGACY_DASHBOARD_REDIRECT_CHANGED"
  [[ "$opip_ui_failures" == "$baseline" ]]
}

opip_ui_check_v2_bundle() {
  local code body asset
  [[ "$(opip_ui_http_code /dashboard/v2)" == "308" ]] || opip_ui_fail "V2_ENTRY_REDIRECT_CHANGED"
  code="$(opip_ui_http_code /dashboard/v2/)"
  [[ "$code" == "200" ]] || opip_ui_fail "V2_INDEX_STATUS:${code}"
  body="$(opip_ui_http_body /dashboard/v2/)"
  grep -Fq '<div id="root"></div>' <<<"$body" || opip_ui_fail "V2_INDEX_BODY_UNRECOGNIZED"
  asset="$(opip_ui_first_asset)"
  if [[ -z "$asset" ]]; then
    opip_ui_fail "V2_ASSET_NOT_PACKAGED"
  elif [[ "$(opip_ui_http_code "/dashboard/v2/assets/${asset}")" != "200" ]]; then
    opip_ui_fail "V2_ASSET_STATUS_FAILED:${asset}"
  fi
  opip_ui_ok
}

opip_ui_check_api_auth() {
  # Unauthenticated access must be refused, never served. A 200 here would mean
  # an exposure; a 404/5xx would mean the allowlist no longer reaches the
  # backend. Method denial is enforced by the edge itself.
  local path method code delete_code
  while read -r method path; do
    [[ -n "$method" ]] || continue
    code="$(opip_ui_http_code "$path" "$method")"
    case "$code" in
      401|403) ;;
      *) opip_ui_fail "API_AUTH_UNEXPECTED:${method}:${path}:${code}" ;;
    esac
  done <<'ROUTES'
GET /api/opip/zero-trade-explanation
GET /api/opip/scoring-comparison
GET /api/opip/strategy-admin/draft
PUT /api/opip/strategy-admin/draft
GET /api/opip/strategy-admin/active
POST /api/opip/strategy-admin/activate
POST /api/opip/strategy-admin/rollback
ROUTES
  # A method outside the allowlist is denied by Nginx before it can reach the app.
  delete_code="$(opip_ui_http_code /api/opip/scoring-comparison DELETE)"
  [[ "$delete_code" == "403" ]] || opip_ui_fail "API_METHOD_NOT_DENIED:DELETE:${delete_code}"
  opip_ui_ok
}

opip_ui_check_tradevisor_vhost() {
  # GET only: this probe never posts a webhook body. A dark vhost (no rendered
  # verification value) is a legitimate pre-existing posture and is reported as
  # DARK; a 5xx or a proxy error would mean the 443 surface was broken.
  local code
  code="$(opip_ui_http_code /)"
  case "$code" in
    000) opip_ui_say "OPIP_UI_TRADEVISOR_VHOST=DARK" ;;
    403|404) opip_ui_say "OPIP_UI_TRADEVISOR_VHOST=REFUSING" ;;
    *) opip_ui_fail "TRADEVISOR_VHOST_UNEXPECTED:${code}" ;;
  esac
  if [[ "$code" != "000" ]]; then
    code="$(opip_ui_http_code /webhooks/tradevisor/v1)"
    case "$code" in
      403|404) ;;
      *) opip_ui_fail "TRADEVISOR_ROUTE_UNEXPECTED:${code}" ;;
    esac
  fi
  opip_ui_ok
}

opip_ui_check_edge_isolation() {
  # The recreated edge must keep the tracked resource caps, read-only root and
  # the two published ports. Anything else is a configuration regression.
  local id caps published
  id="$(opip_ui_compose_legacy ps -q "$opip_ui_service" 2>/dev/null || true)"
  if [[ -z "$id" ]]; then
    opip_ui_fail "EDGE_CONTAINER_ABSENT_AFTER_CUTOVER"
    return 1
  fi
  caps="$(docker inspect --format '{{.HostConfig.Memory}}|{{.HostConfig.MemorySwap}}|{{.HostConfig.PidsLimit}}|{{.HostConfig.ReadonlyRootfs}}' "$id" 2>/dev/null || true)"
  [[ "$caps" == "67108864|67108864|128|true" ]] || opip_ui_fail "EDGE_RESOURCE_CAPS_CHANGED:${caps}"
  published="$(docker port "$id" 2>/dev/null | awk -F: '{print $NF}' | sort -u | tr '\n' ',')"
  [[ "$published" == "443,8443," ]] || opip_ui_fail "EDGE_PORTS_CHANGED:${published}"
  opip_ui_ok
}

# --- restore + receipt --------------------------------------------------------

opip_ui_restore_previous_edge() {
  # Rollback is the overlay being OMITTED: the tracked base Compose file alone
  # recreates the edge with its original image, config mount and caps. No other
  # service is named, so nothing else can be recreated.
  opip_ui_say "OPIP_UI_ROLLBACK=START"
  if ! opip_ui_compose_legacy up -d --no-deps --force-recreate "$opip_ui_service"; then
    opip_ui_say "OPIP_UI_ROLLBACK=COMMAND_FAILED"
    return 1
  fi
  if ! opip_ui_wait_edge; then
    opip_ui_say "OPIP_UI_ROLLBACK=EDGE_NOT_LISTENING"
    return 1
  fi
  if opip_ui_check_legacy_dashboard; then
    opip_ui_say "OPIP_UI_ROLLBACK=SUCCESS"
    return 0
  fi
  opip_ui_say "OPIP_UI_ROLLBACK=LEGACY_UNVERIFIED"
  return 1
}

opip_ui_write_receipt() {
  # <result> <rollback> -> immutable per-attempt receipt plus an append-only log.
  local result="$1" rollback="$2" dir prov receipt tree archive manifest
  dir="$opip_ui_state/receipts"
  install -d -m 0700 "$dir" 2>/dev/null || true
  prov="${OPIP_STAGING_DIR:-}/${TARGET_SHA}/provenance.env"
  tree="$(sed -n 's/^tree=//p' "$prov" 2>/dev/null | head -n 1)"
  archive="$(sed -n 's/^archive_digest=//p' "$prov" 2>/dev/null | head -n 1)"
  manifest="$(sed -n 's/^manifest_digest=//p' "$prov" 2>/dev/null | head -n 1)"
  receipt="$dir/${TARGET_SHA}.${opip_ui_attempt}.env"
  {
    printf 'schema=1\n'
    printf 'action=UI_ONLY_EDGE_RELEASE\n'
    printf 'result=%s\n' "$result"
    printf 'rollback=%s\n' "$rollback"
    printf 'sha=%s\n' "$TARGET_SHA"
    printf 'tree=%s\n' "${tree:-UNRECORDED}"
    printf 'artifact_sha256=%s\n' "${archive:-UNRECORDED}"
    printf 'manifest_sha256=%s\n' "${manifest:-UNRECORDED}"
    printf 'service=%s\n' "$opip_ui_service"
    printf 'image=%s:%s\n' "$opip_ui_image_repo" "$TARGET_SHA"
    printf 'image_id=%s\n' "${opip_ui_image_id:-NONE}"
    printf 'previous_image=%s\n' "$(sed -n 's/^image=//p' "$opip_ui_state/previous-edge.env" 2>/dev/null | head -n 1)"
    printf 'blockers=%s\n' "${opip_ui_failures:-NONE}"
    printf 'attempt=%s\n' "$opip_ui_attempt"
    printf 'recorded_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$receipt"
  chmod 0600 "$receipt"
  printf '%s result=%s rollback=%s blockers=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$result" "$rollback" "${opip_ui_failures:-NONE}" \
    >> "$opip_ui_state/history.log" 2>/dev/null || true
  opip_ui_say "OPIP_UI_RECEIPT=$receipt"
}

# --- build / cutover ----------------------------------------------------------

opip_ui_build_image() {
  local tag="${opip_ui_image_repo}:${TARGET_SHA}"
  if ! docker build \
    -f "$opip_ui_staged/deploy/control-center/Dockerfile.edge" \
    -t "$tag" "$opip_ui_staged"; then
    opip_ui_fail "IMAGE_BUILD_FAILED"
    return 1
  fi
  opip_ui_image_id="$(docker image inspect --format '{{.Id}}' "$tag" 2>/dev/null || true)"
  if [[ -z "$opip_ui_image_id" ]]; then
    opip_ui_fail "IMAGE_IDENTITY_UNPROVEN"
    return 1
  fi
  opip_ui_say "OPIP_UI_IMAGE=${tag}"
  opip_ui_say "OPIP_UI_IMAGE_ID=${opip_ui_image_id}"
  return 0
}

opip_ui_cutover() {
  # Recreate ONLY the edge service from base + verified overlay. `--no-deps`
  # forbids touching any other service and `--no-build` forbids an implicit
  # build from the live (older) tree.
  if ! opip_ui_compose_edge up -d --no-deps --no-build --force-recreate "$opip_ui_service"; then
    opip_ui_fail "CUTOVER_COMMAND_FAILED"
    return 1
  fi
  opip_ui_say "OPIP_UI_CUTOVER=EDGE_RECREATED"
  return 0
}



# --- entry points -------------------------------------------------------------

opip_ui_deploy() {
  opip_ui_image_id=""
  opip_ui_say "OPIP_UI_MODE=deploy"
  opip_ui_say "OPIP_UI_SHA=${TARGET_SHA}"

  # Preflight never aborts: every gate records its own blocker and the run is
  # refused as a whole, so the receipt names ALL blockers.
  opip_ui_require_candidate || true
  opip_ui_require_host_budget || true
  opip_ui_require_runtime_inputs || true
  opip_ui_require_composed_overlay || true
  opip_ui_require_nginx_syntax || true

  if ! opip_ui_ok; then
    opip_ui_write_receipt BLOCKED NOT_REQUIRED
    opip_ui_say "OPIP_UI_RESULT=BLOCKED"
    opip_ui_say "OPIP_UI_LIVE_RELEASE=UNCHANGED"
    return 65
  fi

  # The rollback vehicle must exist BEFORE any mutation.
  if ! opip_ui_capture_previous_edge; then
    opip_ui_write_receipt BLOCKED NOT_REQUIRED
    opip_ui_say "OPIP_UI_RESULT=BLOCKED"
    opip_ui_say "OPIP_UI_LIVE_RELEASE=UNCHANGED"
    return 65
  fi

  if ! opip_ui_build_image; then
    opip_ui_write_receipt BLOCKED NOT_REQUIRED
    opip_ui_say "OPIP_UI_RESULT=BLOCKED"
    opip_ui_say "OPIP_UI_LIVE_RELEASE=UNCHANGED"
    return 65
  fi

  # Post-cutover checks record blockers and never abort: an abort here would skip
  # the restore path below, which is the one thing that must always run.
  if opip_ui_cutover && opip_ui_wait_edge; then
    opip_ui_check_legacy_dashboard || true
    opip_ui_check_v2_bundle || true
    opip_ui_check_api_auth || true
    opip_ui_check_tradevisor_vhost || true
    opip_ui_check_edge_isolation || true
  fi

  if opip_ui_ok; then
    {
      printf 'schema=1\n'
      printf 'sha=%s\n' "$TARGET_SHA"
      printf 'image=%s:%s\n' "$opip_ui_image_repo" "$TARGET_SHA"
      printf 'image_id=%s\n' "$opip_ui_image_id"
      printf 'activated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$opip_ui_state/active.env"
    chmod 0600 "$opip_ui_state/active.env"
    opip_ui_write_receipt DEPLOYED NONE
    opip_ui_say "OPIP_UI_RESULT=DEPLOYED"
    opip_ui_say "OPIP_UI_CORE_UNTOUCHED=YES"
    return 0
  fi

  # Any failed gate after cutover restores the previously captured edge.
  local rollback_state="FAILED"
  if opip_ui_restore_previous_edge; then
    rollback_state="SUCCESS"
  fi
  opip_ui_write_receipt DEPLOY_FAILED "$rollback_state"
  opip_ui_say "OPIP_UI_RESULT=DEPLOY_FAILED"
  opip_ui_say "OPIP_UI_ROLLBACK_STATE=${rollback_state}"
  return 70
}

opip_ui_rollback() {
  local active_sha
  opip_ui_say "OPIP_UI_MODE=rollback"
  opip_ui_say "OPIP_UI_SHA=${TARGET_SHA}"
  if [[ ! -s "$opip_ui_state/active.env" ]]; then
    opip_ui_fail "NO_ACTIVE_UI_RELEASE"
    opip_ui_write_receipt ROLLBACK_REFUSED NONE
    opip_ui_say "OPIP_UI_RESULT=ROLLBACK_REFUSED"
    return 65
  fi
  active_sha="$(sed -n 's/^sha=//p' "$opip_ui_state/active.env" | head -n 1)"
  if [[ "$active_sha" != "$TARGET_SHA" ]]; then
    # Only the recorded active UI release may be rolled back: an arbitrary SHA
    # can never be used to drive an edge target.
    opip_ui_fail "ACTIVE_UI_SHA_MISMATCH:active=${active_sha}"
    opip_ui_write_receipt ROLLBACK_REFUSED NONE
    opip_ui_say "OPIP_UI_RESULT=ROLLBACK_REFUSED"
    return 65
  fi
  if [[ ! -s "$opip_ui_state/previous-edge.env" ]]; then
    opip_ui_fail "ROLLBACK_VEHICLE_MISSING"
    opip_ui_write_receipt ROLLBACK_REFUSED NONE
    opip_ui_say "OPIP_UI_RESULT=ROLLBACK_REFUSED"
    return 65
  fi
  if opip_ui_restore_previous_edge; then
    rm -f "$opip_ui_state/active.env"
    opip_ui_write_receipt ROLLED_BACK SUCCESS
    opip_ui_say "OPIP_UI_RESULT=ROLLED_BACK"
    return 0
  fi
  opip_ui_write_receipt ROLLBACK_FAILED FAILED
  opip_ui_say "OPIP_UI_RESULT=ROLLBACK_FAILED"
  return 70
}

opip_ui_run() {
  case "${1:-}" in
    ui-deploy) opip_ui_deploy ;;
    rollback-ui) opip_ui_rollback ;;
    *)
      opip_ui_say "refusing UI release: unknown mode '${1:-}'"
      return 64
      ;;
  esac
}
