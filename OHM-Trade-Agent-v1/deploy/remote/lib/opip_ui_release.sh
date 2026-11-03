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
#   * FREEZE that verified tree into an immutable, retained per-release artifact
#     ($STATE_DIR/ui-releases/<sha>) and then build and mount ONLY from that copy;
#   * build the V2 edge image from the frozen tree and recreate EXACTLY ONE
#     container: `ohm-dashboard-edge`;
#   * restore the ACTUAL previously running edge on any post-cutover failure,
#     including a V2 -> V2 restore of the previous V2 release.
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
# Why the tree is frozen (A5): `source_prepare` does `rm -rf
# "$OPIP_STAGING_DIR/<sha>"` before re-extracting. The live V2 edge bind-mounts
# that directory, so re-deploying the same SHA - or any concurrent attempt -
# would pull the running container's Nginx configuration out from under it. The
# staging area is therefore treated as build input only, and the live mount graph
# always points at a retained, digest-verified per-release copy that nothing in
# this path ever deletes.
#
# Rollback model (A3): the ACTUAL running edge is captured read-only BEFORE
# cutover - its immutable image identity, its kind (a legacy base-Compose edge or
# a V2 edge) and, for a V2 edge, the frozen release tree it mounts. A failure at
# any post-cutover check restores exactly that edge, and the restore is only
# reported successful when the recreated container's image identity matches the
# captured one.

opip_ui_service="${OPIP_UI_EDGE_SERVICE:-ohm-dashboard-edge}"
opip_ui_image_repo="${OPIP_UI_IMAGE_REPO:-opip-dashboard-edge-v2}"
opip_ui_base_image="${OPIP_UI_BASE_IMAGE:-nginx:1.27-alpine}"
opip_ui_state="${OPIP_UI_STATE_DIR:-${STATE_DIR:?STATE_DIR is required}/ui}"
opip_ui_releases_dir="${OPIP_UI_RELEASES_DIR:-${STATE_DIR:?STATE_DIR is required}/ui-releases}"
opip_ui_staged="${OPIP_UI_STAGED_APP:-}"
opip_ui_tree=""
opip_ui_cert_dir="${OPIP_UI_CERT_DIR:-/etc/letsencrypt}"
opip_ui_cert_name="${OPIP_UI_CERT_NAME:-161-35-106-207.sslip.io}"
opip_ui_tls_host="${OPIP_UI_TLS_HOST:-178-128-147-125.sslip.io}"
opip_ui_min_free_kb="${OPIP_UI_MIN_FREE_KB:-2097152}"
opip_ui_min_avail_kb="${OPIP_UI_MIN_AVAIL_KB:-393216}"
opip_ui_http_timeout="${OPIP_UI_HTTP_TIMEOUT:-10}"
opip_ui_wait_tries="${OPIP_UI_WAIT_TRIES:-30}"
# Attempt identity: one-second resolution is not enough - two attempts can start
# and finish inside the same second, which would make two receipts collide on the
# same filename. Nanoseconds and the process id make the name unique per attempt.
opip_ui_attempt="${OPIP_UI_ATTEMPT:-$(date -u +%Y%m%dT%H%M%S%NZ)-$$}"
opip_ui_image_id=""
opip_ui_failures=""
opip_ui_restored_release="NONE"

opip_ui_say() { printf '%s\n' "$*"; }

# Record a durable failure reason without aborting: every preflight gate is
# evaluated so the receipt names ALL blockers, never just the first one.
opip_ui_fail() {
  opip_ui_failures="${opip_ui_failures}${opip_ui_failures:+|}${1}"
  printf 'OPIP_UI_BLOCKER=%s\n' "$1" >&2
}

opip_ui_ok() { [[ -z "$opip_ui_failures" ]]; }

# --- governed command wrappers ------------------------------------------------
# Every external mutation goes through these wrappers so the UI path has a fixed,
# auditable Compose shape: base file always, overlay only from the frozen
# verified release tree, one explicit service, no orphan removal.
opip_ui_compose_v2() {
  # opip_ui_compose_v2 <sha> <tree> <compose-args...>
  local sha="$1" tree="$2"
  shift 2
  (
    cd "${APP_ROOT:?APP_ROOT is required}" || exit 1
    OPIP_DEPLOYED_SHA="$sha" OPIP_UI_TREE="$tree" \
      docker compose \
      -f "${APP_ROOT}/docker-compose.yml" \
      -f "$tree/deploy/control-center/docker-compose.v2.yml" \
      "$@"
  )
}

opip_ui_compose_edge() {
  # The overlay is never resolved against the live (older) release tree.
  opip_ui_compose_v2 "$TARGET_SHA" "$opip_ui_tree" "$@"
}

opip_ui_compose_legacy() {
  (
    cd "${APP_ROOT:?APP_ROOT is required}" || exit 1
    docker compose -f "${APP_ROOT}/docker-compose.yml" "$@"
  )
}

# --- two INDEPENDENT ingress probes -----------------------------------------
# The dashboard listens on 8443. The Tradevisor webhook vhost listens on 443 on
# the SAME container. Conflating them is how an apparently healthy dashboard can
# conceal a broken webhook ingress, so each surface has its own probe and its own
# port, and neither can ever be satisfied by the other.
#
# Certificate verification is intentionally disabled in both: these probes prove
# ROUTING on the loopback interface, not public trust. The real certificate mount
# is proven separately by `nginx -t`, and this release changes no TLS directive.
opip_ui_dashboard_code() {
  local path="$1" method="${2:-GET}"
  curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' \
    --max-time "$opip_ui_http_timeout" \
    --insecure \
    --resolve "${opip_ui_tls_host}:8443:127.0.0.1" \
    --request "$method" \
    "https://${opip_ui_tls_host}:8443${path}" 2>/dev/null || printf '000'
}

opip_ui_dashboard_body() {
  local path="$1"
  curl --silent --show-error \
    --max-time "$opip_ui_http_timeout" \
    --insecure \
    --resolve "${opip_ui_tls_host}:8443:127.0.0.1" \
    "https://${opip_ui_tls_host}:8443${path}" 2>/dev/null || true
}

opip_ui_webhook_code() {
  # GET only, always. A synthetic POST would be a side effect on a live webhook
  # endpoint, so this probe never sends one; a method-restricted route answered
  # with 403/404 still proves the listener, the SNI server and the route.
  local path="$1"
  curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' \
    --max-time "$opip_ui_http_timeout" \
    --insecure \
    --resolve "${opip_ui_tls_host}:443:127.0.0.1" \
    --request GET \
    "https://${opip_ui_tls_host}:443${path}" 2>/dev/null || printf '000'
}

# --- digest of a release tree -------------------------------------------------
opip_ui_tree_digest() {
  # Deterministic identity of a release tree, using the same exclusions as
  # rt_list_tree so the frozen copy and its source are comparable.
  local root="$1"
  (
    cd "$root" || exit 1
    find . -type f \
      -not -path './data/*' -not -path './.env' -not -path './.env.core' \
      -not -path './__pycache__/*' \
      -not -name '*.py[cod]' -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  ) | sed 's|\./||' | sha256sum | awk '{print $1}'
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

opip_ui_record_release() {
  # opip_ui_record_release <release-dir> <tree-digest>
  local dir="$1" digest="$2" tmp
  tmp="$dir/.release.env.$$"
  {
    printf 'schema=1\n'
    printf 'sha=%s\n' "$TARGET_SHA"
    printf 'tree_digest=%s\n' "$digest"
    printf 'promoted_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$tmp" || return 1
  chmod 0600 "$tmp" || {
    rm -f "$tmp"
    return 1
  }
  mv -f "$tmp" "$dir/release.env"
}

opip_ui_promote_release() {
  # Freeze the verified candidate into an immutable, retained per-release tree.
  #
  # This is the A5 correction: the live edge must never bind-mount the shared
  # staging directory, because `source_prepare` deletes and re-extracts
  # "$OPIP_STAGING_DIR/<sha>" and would therefore pull the running container's
  # Nginx configuration out from under it. Promotion is idempotent - a repeat
  # deploy of the same SHA reuses the retained copy untouched - and nothing in
  # this library ever deletes a retained release, so registered rollback
  # artifacts cannot be garbage-collected by a later attempt.
  local dest work source_digest
  [[ -n "$opip_ui_staged" && -d "$opip_ui_staged" ]] || {
    opip_ui_fail "CANDIDATE_TREE_MISSING"
    return 1
  }
  dest="$opip_ui_releases_dir/$TARGET_SHA"
  if ! install -d -m 0700 "$opip_ui_releases_dir"; then
    opip_ui_fail "RELEASE_DIR_UNWRITABLE"
    return 1
  fi
  source_digest="$(opip_ui_tree_digest "$opip_ui_staged")"
  if [[ -z "$source_digest" ]]; then
    opip_ui_fail "CANDIDATE_TREE_UNREADABLE"
    return 1
  fi
  if [[ -d "$dest/tree/OHM-Trade-Agent-v1" ]]; then
    if [[ "$(opip_ui_tree_digest "$dest/tree/OHM-Trade-Agent-v1")" == "$source_digest" ]]; then
      opip_ui_ok
      opip_ui_tree="$dest/tree/OHM-Trade-Agent-v1"
      opip_ui_record_release "$dest" "$source_digest" || true
      opip_ui_say "OPIP_UI_ACTIVE_ARTIFACT=REUSED path=$opip_ui_tree"
      return 0
    fi
    # A retained release that no longer matches this SHA's artifact cannot be
    # replaced safely (it may be the running edge, and it may be the rollback
    # vehicle). Retain it and refuse instead of overwriting it.
    opip_ui_fail "RETAINED_ARTIFACT_CORRUPT:$TARGET_SHA"
    return 1
  fi
  if ! work="$(mktemp -d "$opip_ui_releases_dir/.promote.XXXXXX")"; then
    opip_ui_fail "RELEASE_DIR_UNWRITABLE"
    return 1
  fi
  if ! install -d -m 0700 "$work/tree"; then
    rm -rf "$work"
    opip_ui_fail "RELEASE_DIR_UNWRITABLE"
    return 1
  fi
  # Hard links cost almost no space and are never mutated in place: the staging
  # provider unlinks and re-creates files rather than rewriting them, so the
  # frozen copy keeps its bytes. Fall back to a copy when linking is impossible.
  if ! cp -al "$opip_ui_staged" "$work/tree/OHM-Trade-Agent-v1" 2>/dev/null; then
    if ! cp -a "$opip_ui_staged" "$work/tree/OHM-Trade-Agent-v1"; then
      rm -rf "$work"
      opip_ui_fail "ACTIVE_ARTIFACT_COPY_FAILED"
      return 1
    fi
  fi
  if [[ "$(opip_ui_tree_digest "$work/tree/OHM-Trade-Agent-v1")" != "$source_digest" ]]; then
    rm -rf "$work"
    opip_ui_fail "ACTIVE_ARTIFACT_DIGEST_MISMATCH"
    return 1
  fi
  if [[ -e "$dest" ]]; then
    rm -rf "$work"
    opip_ui_fail "RETAINED_ARTIFACT_CORRUPT:$TARGET_SHA"
    return 1
  fi
  if ! mv -- "$work" "$dest"; then
    rm -rf "$work"
    opip_ui_fail "ACTIVE_ARTIFACT_COPY_FAILED"
    return 1
  fi
  opip_ui_record_release "$dest" "$source_digest" || true
  opip_ui_tree="$dest/tree/OHM-Trade-Agent-v1"
  opip_ui_say "OPIP_UI_ACTIVE_ARTIFACT=FROZEN path=$opip_ui_tree"
  opip_ui_say "OPIP_UI_ACTIVE_ARTIFACT_DIGEST=$source_digest"
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
  # Resolve base + frozen overlay and prove the edge mount graph is exactly the
  # retained candidate tree and nothing else. Read-only Compose evaluation.
  #
  # The resolved document is written to a private temporary file and passed by
  # path: a `heredoc` would claim the script's stdin and the piped JSON would be
  # silently discarded.
  local composed expect_image snapshot
  [[ -n "$opip_ui_tree" ]] || {
    opip_ui_fail "CANDIDATE_TREE_UNSET"
    return 1
  }
  expect_image="${opip_ui_image_repo}:${TARGET_SHA}"
  if ! composed="$(opip_ui_compose_edge config --format json 2>&1)"; then
    opip_ui_fail "COMPOSE_RESOLUTION_FAILED"
    printf '%s\n' "$composed" >&2
    return 1
  fi
  if ! snapshot="$(mktemp "${TMPDIR:-/tmp}/opip-ui-compose.XXXXXX" 2>/dev/null)"; then
    opip_ui_fail "COMPOSE_SNAPSHOT_UNWRITABLE"
    return 1
  fi
  printf '%s' "$composed" > "$snapshot"
  OPIP_UI_EXPECT_SERVICE="$opip_ui_service" \
  OPIP_UI_EXPECT_TREE="$opip_ui_tree" \
  OPIP_UI_EXPECT_IMAGE="$expect_image" \
    "$PYTHON_BIN" - "$snapshot" <<'PY' || opip_ui_fail "COMPOSE_GRAPH_MISMATCH"
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)["services"]
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
  rm -f "$snapshot"
  opip_ui_ok
}

opip_ui_require_nginx_syntax() {
  # Validate the candidate base + V2 snippet with the REAL certificate mount in a
  # throwaway, port-less, network-less container of the already-present image.
  local output
  [[ -n "$opip_ui_tree" ]] || {
    opip_ui_fail "CANDIDATE_TREE_UNSET"
    return 1
  }
  if output="$(docker run --rm --network none \
      -v "$opip_ui_tree/deploy/nginx/dashboard-sidecar.conf:/etc/nginx/nginx.conf:ro" \
      -v "$opip_ui_tree/deploy/control-center/nginx:/etc/nginx/opip-control-center:ro" \
      -v "$opip_ui_cert_dir:/etc/letsencrypt:ro" \
      "$opip_ui_base_image" nginx -t 2>&1)"; then
    opip_ui_say "OPIP_UI_NGINX_SYNTAX=PASS"
  else
    printf '%s\n' "$output" >&2
    opip_ui_fail "NGINX_SYNTAX_FAILED"
  fi
  opip_ui_ok
}

# --- capture the ACTUAL previous edge (read-only) -----------------------------

opip_ui_capture_previous_edge() {
  # Capture enough to restore the edge that is running right now, not the edge a
  # base Compose file would create. For a V2 edge that means the release SHA, the
  # retained tree it mounts and its immutable image identity; for a legacy edge
  # it means the image identity the tracked base file must reproduce.
  #
  # A first-time V2 install with no running edge is refused: this command is a
  # side-by-side addition, never a bootstrap of the legacy edge.
  local id spec caps
  if ! install -d -m 0700 "$opip_ui_state" 2>/dev/null; then
    opip_ui_fail "UI_STATE_DIR_UNWRITABLE"
    return 1
  fi
  id="$(opip_ui_compose_legacy ps -q "$opip_ui_service" 2>/dev/null || true)"
  if [[ -z "$id" ]]; then
    opip_ui_fail "EDGE_CONTAINER_NOT_RUNNING"
    return 1
  fi
  spec="$(
    printf '{"image":%s,"image_id":%s,"mounts":%s}\n' \
      "$(docker inspect --format '{{json .Config.Image}}' "$id" 2>/dev/null || printf 'null')" \
      "$(docker inspect --format '{{json .Image}}' "$id" 2>/dev/null || printf 'null')" \
      "$(docker inspect --format '{{json .Mounts}}' "$id" 2>/dev/null || printf '[]')"
  )"
  caps="$(docker inspect --format '{{.HostConfig.Memory}}|{{.HostConfig.MemorySwap}}|{{.HostConfig.PidsLimit}}|{{.HostConfig.ReadonlyRootfs}}' "$id" 2>/dev/null || true)"
  if ! OPIP_UI_SERVICE="$opip_ui_service" \
       OPIP_UI_RELEASES="$opip_ui_releases_dir" \
       OPIP_UI_APP_ROOT="$APP_ROOT" \
       OPIP_UI_CAPS="$caps" \
       "$PYTHON_BIN" - "$spec" "$opip_ui_state/.previous-edge.env.$$" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

spec = json.loads(sys.argv[1])
out = sys.argv[2]
mounts = {m.get("Destination"): m.get("Source") for m in (spec.get("mounts") or [])}
source = mounts.get("/etc/nginx/nginx.conf") or ""
releases = os.environ["OPIP_UI_RELEASES"]
prefix = releases + "/"
suffix = "/tree/OHM-Trade-Agent-v1/deploy/nginx/dashboard-sidecar.conf"
kind = "UNKNOWN"
sha = "NONE"
tree = "NONE"
if source.startswith(prefix) and source.endswith(suffix):
    candidate = source[len(prefix):][: -len(suffix)]
    if len(candidate) == 40 and all(c in "0123456789abcdef" for c in candidate):
        kind = "v2"
        sha = candidate
        tree = f"{releases}/{candidate}/tree/OHM-Trade-Agent-v1"
elif source and os.path.realpath(source).startswith(
    os.path.realpath(os.environ["OPIP_UI_APP_ROOT"])
):
    kind = "legacy"
fields = [
    ("schema", "2"),
    ("kind", kind),
    ("sha", sha),
    ("tree", tree),
    ("image", spec.get("image") or "NONE"),
    ("image_id", spec.get("image_id") or "NONE"),
    ("mount_source", source or "NONE"),
    ("caps", os.environ.get("OPIP_UI_CAPS") or "NONE"),
    ("captured_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
]
with open(out, "w", encoding="utf-8", newline="\n") as handle:
    for key, value in fields:
        handle.write(f"{key}={value}\n")
sys.exit(0 if kind in ("v2", "legacy") else 3)
PY
  then
    rm -f "$opip_ui_state/.previous-edge.env.$$"
    opip_ui_fail "PREVIOUS_EDGE_UNIDENTIFIED"
    return 1
  fi
  chmod 0600 "$opip_ui_state/.previous-edge.env.$$"
  mv -f "$opip_ui_state/.previous-edge.env.$$" "$opip_ui_state/previous-edge.env"
  # An unprovable restore vehicle must refuse the cutover, not be discovered
  # afterwards: verify now that the captured identity can actually be recreated.
  opip_ui_verify_restore_vehicle
}

opip_ui_verify_restore_vehicle() {
  # Prove the captured previous edge can be restored EXACTLY, before mutating.
  local kind sha tree image image_id recorded digest
  [[ -s "$opip_ui_state/previous-edge.env" ]] || {
    opip_ui_fail "ROLLBACK_VEHICLE_MISSING"
    return 1
  }
  kind="$(sed -n 's/^kind=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  image="$(sed -n 's/^image=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  image_id="$(sed -n 's/^image_id=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  case "$kind" in
    legacy)
      if [[ "$(docker image inspect --format '{{.Id}}' "$opip_ui_base_image" 2>/dev/null || true)" != "$image_id" ]]; then
        opip_ui_fail "LEGACY_IMAGE_DRIFT:${image}"
      fi
      ;;
    v2)
      sha="$(sed -n 's/^sha=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
      tree="$opip_ui_releases_dir/$sha/tree/OHM-Trade-Agent-v1"
      recorded="$(sed -n 's/^tree_digest=//p' "$opip_ui_releases_dir/$sha/release.env" 2>/dev/null | head -n 1)"
      if [[ ! -s "$tree/deploy/control-center/docker-compose.v2.yml" ]]; then
        opip_ui_fail "ROLLBACK_ARTIFACT_MISSING:$sha"
      elif [[ -z "$recorded" ]]; then
        opip_ui_fail "ROLLBACK_ARTIFACT_UNVERIFIED:$sha"
      else
        digest="$(opip_ui_tree_digest "$tree")"
        [[ "$digest" == "$recorded" ]] || opip_ui_fail "ROLLBACK_ARTIFACT_UNVERIFIED:$sha"
      fi
      if [[ "$image" != "${opip_ui_image_repo}:${sha}" ]]; then
        opip_ui_fail "ROLLBACK_IMAGE_UNEXPECTED:${image}"
      elif [[ "$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)" != "$image_id" ]]; then
        opip_ui_fail "ROLLBACK_IMAGE_IDENTITY_UNPROVEN:${image}"
      fi
      ;;
    *)
      opip_ui_fail "ROLLBACK_VEHICLE_UNIDENTIFIED"
      ;;
  esac
  opip_ui_say "OPIP_UI_PREVIOUS_EDGE=${kind} image=${image}"
  opip_ui_ok
}

opip_ui_capture_ingress_baseline() {
  # The 443 surface is a SEPARATE listener on the same container. Capture what it
  # actually answers before the cutover so a listener that disappears afterwards
  # cannot be excused as "dark". GET only: a synthetic POST to a live webhook
  # endpoint would be a side effect.
  local root_code route_code
  if ! install -d -m 0700 "$opip_ui_state" 2>/dev/null; then
    opip_ui_fail "UI_STATE_DIR_UNWRITABLE"
    return 1
  fi
  root_code="$(opip_ui_webhook_code /)"
  route_code="$(opip_ui_webhook_code /webhooks/tradevisor/v1)"
  {
    printf 'schema=1\n'
    printf 'root=%s\n' "$root_code"
    printf 'webhook=%s\n' "$route_code"
    printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$opip_ui_state/.ingress-baseline.env.$$"
  chmod 0600 "$opip_ui_state/.ingress-baseline.env.$$"
  mv -f "$opip_ui_state/.ingress-baseline.env.$$" "$opip_ui_state/ingress-baseline.env"
  opip_ui_say "OPIP_UI_INGRESS_BASELINE_443_ROOT=${root_code}"
  opip_ui_say "OPIP_UI_INGRESS_BASELINE_443_WEBHOOK=${route_code}"
  opip_ui_ok
}

# --- post-cutover verification -----------------------------------------------

opip_ui_wait_edge() {
  local tries=0 code
  while (( tries < opip_ui_wait_tries )); do
    code="$(opip_ui_dashboard_code /dashboard)"
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
  code="$(opip_ui_dashboard_code /dashboard)"
  body="$(opip_ui_dashboard_body /dashboard)"
  [[ "$code" == "200" ]] || opip_ui_fail "LEGACY_DASHBOARD_STATUS:${code}"
  grep -Fq 'Intelligence Cockpit' <<<"$body" || opip_ui_fail "LEGACY_DASHBOARD_BODY_UNRECOGNIZED"
  # The legacy entry point keeps its redirect contract.
  [[ "$(opip_ui_dashboard_code /dashboard/)" == "308" ]] || opip_ui_fail "LEGACY_DASHBOARD_REDIRECT_CHANGED"
  [[ "$opip_ui_failures" == "$baseline" ]]
}

opip_ui_check_v2_bundle() {
  local code body asset
  [[ "$(opip_ui_dashboard_code /dashboard/v2)" == "308" ]] || opip_ui_fail "V2_ENTRY_REDIRECT_CHANGED"
  code="$(opip_ui_dashboard_code /dashboard/v2/)"
  [[ "$code" == "200" ]] || opip_ui_fail "V2_INDEX_STATUS:${code}"
  body="$(opip_ui_dashboard_body /dashboard/v2/)"
  grep -Fq '<div id="root"></div>' <<<"$body" || opip_ui_fail "V2_INDEX_BODY_UNRECOGNIZED"
  asset="$(opip_ui_first_asset)"
  if [[ -z "$asset" ]]; then
    opip_ui_fail "V2_ASSET_NOT_PACKAGED"
  elif [[ "$(opip_ui_dashboard_code "/dashboard/v2/assets/${asset}")" != "200" ]]; then
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
    code="$(opip_ui_dashboard_code "$path" "$method")"
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
  delete_code="$(opip_ui_dashboard_code /api/opip/scoring-comparison DELETE)"
  [[ "$delete_code" == "403" ]] || opip_ui_fail "API_METHOD_NOT_DENIED:DELETE:${delete_code}"
  opip_ui_ok
}

opip_ui_check_tradevisor_vhost() {
  # A SEPARATE 443 check. The dashboard's 8443 answer can never satisfy this, so a
  # healthy dashboard cannot conceal a broken webhook ingress. The verdict is a
  # comparison against the pre-cutover baseline: a listener that answered before
  # the cutover and answers 000 afterwards is a regression, never "dark".
  #
  # GET only. The route is method-restricted by design, so a 403/404 there is a
  # healthy listener+SNI+route proof and a 000 is a failure.
  local baseline root_code route_code live=0
  baseline="$(sed -n 's/^root=//p' "$opip_ui_state/ingress-baseline.env" 2>/dev/null | head -n 1)"
  root_code="$(opip_ui_webhook_code /)"
  opip_ui_say "OPIP_UI_TRADEVISOR_443=BASELINE=${baseline:-UNRECORDED} NOW=${root_code}"
  case "$root_code" in
    403|404)
      live=1
      ;;
    000)
      if [[ -n "$baseline" && "$baseline" != "000" ]]; then
        opip_ui_fail "TRADEVISOR_443_REGRESSION:baseline=${baseline}:now=000"
      else
        live=0
      fi
      ;;
    *)
      opip_ui_fail "TRADEVISOR_VHOST_UNEXPECTED:${root_code}"
      ;;
  esac
  if [[ "$live" -eq 1 ]]; then
    opip_ui_say "OPIP_UI_TRADEVISOR_VHOST=LIVE"
    route_code="$(opip_ui_webhook_code /webhooks/tradevisor/v1)"
    opip_ui_say "OPIP_UI_TRADEVISOR_ROUTE_443=${route_code}"
    case "$route_code" in
      403|404) ;;
      000) opip_ui_fail "TRADEVISOR_WEBHOOK_ROUTE_UNREACHABLE" ;;
      *) opip_ui_fail "TRADEVISOR_ROUTE_UNEXPECTED:${route_code}" ;;
    esac
  else
    # A vhost that is dark before AND after the cutover is the pre-existing
    # posture (no rendered verification value), which this release does not
    # change; the legacy dashboard is unaffected either way.
    opip_ui_say "OPIP_UI_TRADEVISOR_VHOST=DARK"
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

# --- restore + active state + receipt ----------------------------------------

opip_ui_write_active() {
  # opip_ui_write_active <sha> <image-id>
  local sha="$1" image_id="$2" tmp
  tmp="$opip_ui_state/.active.env.$$"
  {
    printf 'schema=1\n'
    printf 'sha=%s\n' "$sha"
    printf 'image=%s:%s\n' "$opip_ui_image_repo" "$sha"
    printf 'image_id=%s\n' "$image_id"
    printf 'activated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$tmp" || return 1
  chmod 0600 "$tmp" || {
    rm -f "$tmp"
    return 1
  }
  mv -f "$tmp" "$opip_ui_state/active.env"
}

opip_ui_state_after_restore() {
  # After a restore the recorded active state must agree with the edge that is
  # actually running. A V2 restore therefore re-records that release as active,
  # and a legacy restore removes the record because no V2 edge is live.
  local kind sha image_id
  kind="$(sed -n 's/^kind=//p' "$opip_ui_state/previous-edge.env" 2>/dev/null | head -n 1)"
  if [[ "$kind" == "v2" ]]; then
    sha="$(sed -n 's/^sha=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
    image_id="$(sed -n 's/^image_id=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
    if [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
      opip_ui_write_active "$sha" "$image_id" || true
      opip_ui_restored_release="$sha"
      return 0
    fi
  fi
  rm -f "$opip_ui_state/active.env"
  opip_ui_restored_release="LEGACY"
  return 0
}

opip_ui_restore_legacy_edge() {
  # The tracked base Compose file alone reproduces the legacy edge. No other
  # service is named, so nothing else can be recreated.
  opip_ui_compose_legacy up -d --no-deps --force-recreate "$opip_ui_service"
}

opip_ui_restore_v2_edge() {
  # A V2 -> V2 restore must re-install the PREVIOUS V2 release: the base file
  # alone would silently substitute the legacy image and lose the V2 bundle.
  local sha tree image
  sha="$(sed -n 's/^sha=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  tree="$(sed -n 's/^tree=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  image="$(sed -n 's/^image=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  [[ -n "$sha" && -n "$tree" && -n "$image" ]] || return 1
  [[ -s "$tree/deploy/control-center/docker-compose.v2.yml" ]] || return 1
  opip_ui_compose_v2 "$sha" "$tree" up -d --no-deps --no-build --force-recreate "$opip_ui_service"
}

opip_ui_restore_previous_edge() {
  # Restore the exact edge captured before cutover and PROVE it: the recreated
  # container's immutable image id must match the captured one.
  local kind image_id id restored_id
  if [[ ! -s "$opip_ui_state/previous-edge.env" ]]; then
    opip_ui_say "OPIP_UI_ROLLBACK=NO_RESTORE_RECORD"
    return 1
  fi
  kind="$(sed -n 's/^kind=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  image_id="$(sed -n 's/^image_id=//p' "$opip_ui_state/previous-edge.env" | head -n 1)"
  opip_ui_say "OPIP_UI_ROLLBACK=START"
  opip_ui_say "OPIP_UI_RESTORE_KIND=${kind}"
  case "$kind" in
    legacy)
      if ! opip_ui_restore_legacy_edge; then
        opip_ui_say "OPIP_UI_ROLLBACK=COMMAND_FAILED"
        return 1
      fi
      ;;
    v2)
      if ! opip_ui_restore_v2_edge; then
        opip_ui_say "OPIP_UI_ROLLBACK=COMMAND_FAILED"
        return 1
      fi
      ;;
    *)
      opip_ui_say "OPIP_UI_ROLLBACK=VEHICLE_UNIDENTIFIED"
      return 1
      ;;
  esac
  id="$(opip_ui_compose_legacy ps -q "$opip_ui_service" 2>/dev/null || true)"
  if [[ -z "$id" ]]; then
    opip_ui_say "OPIP_UI_ROLLBACK=CONTAINER_ABSENT"
    return 1
  fi
  restored_id="$(docker inspect --format '{{.Image}}' "$id" 2>/dev/null || true)"
  if [[ -n "$image_id" && "$image_id" != "NONE" && "$restored_id" != "$image_id" ]]; then
    opip_ui_say "OPIP_UI_ROLLBACK=IMAGE_IDENTITY_MISMATCH"
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
  # <result> <rollback> -> one immutable, atomically-written per-attempt receipt
  # plus an append-only history line. The filename carries the SHA and the unique
  # attempt identity, so two attempts can never collide on it, and a failed and a
  # successful attempt are always distinguishable.
  local result="$1" rollback="$2" dir prov receipt tree archive manifest tmp kind
  dir="$opip_ui_state/receipts"
  install -d -m 0700 "$dir" 2>/dev/null || true
  prov="${OPIP_STAGING_DIR:-}/${TARGET_SHA}/provenance.env"
  tree="$(sed -n 's/^tree=//p' "$prov" 2>/dev/null | head -n 1)"
  archive="$(sed -n 's/^archive_digest=//p' "$prov" 2>/dev/null | head -n 1)"
  manifest="$(sed -n 's/^manifest_digest=//p' "$prov" 2>/dev/null | head -n 1)"
  kind="$(sed -n 's/^kind=//p' "$opip_ui_state/previous-edge.env" 2>/dev/null | head -n 1)"
  receipt="$dir/${TARGET_SHA}.${opip_ui_attempt}.env"
  tmp="$dir/.${TARGET_SHA}.${opip_ui_attempt}.partial"
  {
    printf 'schema=1\n'
    printf 'action=UI_ONLY_EDGE_RELEASE\n'
    printf 'result=%s\n' "$result"
    printf 'rollback=%s\n' "$rollback"
    printf 'sha=%s\n' "$TARGET_SHA"
    printf 'restored_release=%s\n' "$opip_ui_restored_release"
    printf 'previous_kind=%s\n' "${kind:-UNRECORDED}"
    printf 'tree=%s\n' "${tree:-UNRECORDED}"
    printf 'artifact_sha256=%s\n' "${archive:-UNRECORDED}"
    printf 'manifest_sha256=%s\n' "${manifest:-UNRECORDED}"
    printf 'active_artifact=%s\n' "${opip_ui_tree:-UNRECORDED}"
    printf 'active_artifact_digest=%s\n' "$(sed -n 's/^tree_digest=//p' "$opip_ui_releases_dir/$TARGET_SHA/release.env" 2>/dev/null | head -n 1)"
    printf 'service=%s\n' "$opip_ui_service"
    printf 'image=%s:%s\n' "$opip_ui_image_repo" "$TARGET_SHA"
    printf 'image_id=%s\n' "${opip_ui_image_id:-NONE}"
    printf 'previous_image=%s\n' "$(sed -n 's/^image=//p' "$opip_ui_state/previous-edge.env" 2>/dev/null | head -n 1)"
    printf 'previous_image_id=%s\n' "$(sed -n 's/^image_id=//p' "$opip_ui_state/previous-edge.env" 2>/dev/null | head -n 1)"
    printf 'blockers=%s\n' "${opip_ui_failures:-NONE}"
    printf 'attempt=%s\n' "$opip_ui_attempt"
    printf 'recorded_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$tmp" || true
  chmod 0600 "$tmp" 2>/dev/null || true
  if ! mv -f "$tmp" "$receipt"; then
    printf 'OPIP_UI_RECEIPT=UNWRITABLE:%s\n' "$receipt" >&2
  fi
  printf '%s result=%s rollback=%s restored=%s blockers=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$result" "$rollback" \
    "$opip_ui_restored_release" "${opip_ui_failures:-NONE}" \
    >> "$opip_ui_state/history.log" 2>/dev/null || true
  opip_ui_say "OPIP_UI_RECEIPT=$receipt"
}

# --- build / cutover ----------------------------------------------------------

opip_ui_build_image() {
  # Built from the FROZEN tree, so a concurrent staging wipe cannot change what
  # is baked into the image after the digest was verified.
  local tag="${opip_ui_image_repo}:${TARGET_SHA}"
  [[ -n "$opip_ui_tree" ]] || {
    opip_ui_fail "CANDIDATE_TREE_UNSET"
    return 1
  }
  if ! docker build \
    -f "$opip_ui_tree/deploy/control-center/Dockerfile.edge" \
    -t "$tag" "$opip_ui_tree"; then
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
  # Recreate ONLY the edge service from base + frozen overlay. `--no-deps`
  # forbids touching any other service and `--no-build` forbids an implicit
  # build from an unverified tree.
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
  # refused as a whole, so the receipt names ALL blockers. Only the frozen
  # artifact promotion mutates anything, and it writes to this controller's own
  # state directory - never a container, image or the application tree.
  opip_ui_require_candidate || true
  if opip_ui_ok; then
    opip_ui_promote_release || true
  fi
  opip_ui_require_host_budget || true
  opip_ui_require_runtime_inputs || true
  opip_ui_require_composed_overlay || true
  opip_ui_require_nginx_syntax || true
  # The restore vehicle must exist BEFORE any mutation and must be provably
  # recreatable; a missing or corrupt artifact refuses the cutover here.
  opip_ui_capture_previous_edge || true
  opip_ui_capture_ingress_baseline || true

  if ! opip_ui_ok; then
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
    opip_ui_write_active "$TARGET_SHA" "$opip_ui_image_id"
    opip_ui_write_receipt DEPLOYED NONE
    opip_ui_say "OPIP_UI_RESULT=DEPLOYED"
    opip_ui_say "OPIP_UI_CORE_UNTOUCHED=YES"
    return 0
  fi

  return opip_ui_fail_after_cutover
}

opip_ui_fail_after_cutover() {
  # Any failed gate after cutover restores the previously captured edge. The
  # deploy blockers and the restore's own observations are kept apart so a
  # successful restore is never reported as a blocker nor hidden by one.
  local deploy_blockers rollback_state="FAILED" restore_blockers ingress_blockers
  deploy_blockers="$opip_ui_failures"
  opip_ui_failures=""
  if opip_ui_restore_previous_edge; then
    rollback_state="SUCCESS"
    opip_ui_state_after_restore
  fi
  restore_blockers="$opip_ui_failures"
  opip_ui_failures=""
  if [[ "$rollback_state" == "SUCCESS" ]]; then
    # Both ingress paths must be usable again after the restore, not just 8443.
    if ! opip_ui_check_tradevisor_vhost; then
      rollback_state="INGRESS_REGRESSION"
    fi
  fi
  ingress_blockers="$opip_ui_failures"
  opip_ui_failures="$deploy_blockers"
  [[ -n "$restore_blockers" ]] && opip_ui_failures="${opip_ui_failures}|RESTORE:${restore_blockers}"
  [[ -n "$ingress_blockers" ]] && opip_ui_failures="${opip_ui_failures}|RESTORE_INGRESS:${ingress_blockers}"
  opip_ui_write_receipt DEPLOY_FAILED "$rollback_state"
  opip_ui_say "OPIP_UI_RESULT=DEPLOY_FAILED"
  opip_ui_say "OPIP_UI_ROLLBACK_STATE=${rollback_state}"
  opip_ui_say "OPIP_UI_RESTORED_RELEASE=${opip_ui_restored_release}"
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
  if ! opip_ui_verify_restore_vehicle; then
    opip_ui_write_receipt ROLLBACK_REFUSED NONE
    opip_ui_say "OPIP_UI_RESULT=ROLLBACK_REFUSED"
    opip_ui_say "OPIP_UI_LIVE_RELEASE=UNCHANGED"
    return 65
  fi
  if opip_ui_restore_previous_edge; then
    opip_ui_state_after_restore
    if opip_ui_check_tradevisor_vhost; then
      # The consumed vehicle is removed so a second rollback cannot silently
      # re-apply it; the next deploy records a fresh one from the running edge.
      rm -f "$opip_ui_state/previous-edge.env"
      opip_ui_write_receipt ROLLED_BACK SUCCESS
      opip_ui_say "OPIP_UI_RESULT=ROLLED_BACK"
      opip_ui_say "OPIP_UI_RESTORED_RELEASE=${opip_ui_restored_release}"
      return 0
    fi
    opip_ui_write_receipt ROLLBACK_INGRESS_REGRESSION SUCCESS
    opip_ui_say "OPIP_UI_RESULT=ROLLBACK_INGRESS_REGRESSION"
    return 70
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
