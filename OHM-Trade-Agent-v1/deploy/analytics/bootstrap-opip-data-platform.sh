#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SHA="${1:-}"
STAGE="${2:-empty}"
ROOT="/opt/opip-learning"
REPO_ROOT="$ROOT/repo"
APP_ROOT="$REPO_ROOT/OHM-Trade-Agent-v1"
COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.yml"
# The Cockpit has its own Compose surface. Docker Compose interpolates the entire file it
# is given before selecting a service, and the shared analytics file carries mandatory
# PostgreSQL/Grafana variables (for example `${OPIP_GRAFANA_ADMIN_USER:?}`), so a
# Cockpit-only deployment served from that file failed on the analytics host even though
# the shell control flow never reaches the Grafana plane. Every Cockpit build/start/status
# operation goes through this file instead; the PostgreSQL/Grafana stages keep using
# $COMPOSE.
COCKPIT_COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.cockpit.yml"
ENV_FILE="/etc/opip-data-platform.env"
GRAFANA_ENV_FILE="/etc/opip-grafana.env"
COCKPIT_ENV_FILE="/etc/opip-cockpit.env"
STATE_ROOT="/var/lib/opip-data-platform"
STATE_FILE="$STATE_ROOT/rollout.env"
# Cockpit readiness is recorded separately from PostgreSQL rollout evidence, so a
# replica-backed Cockpit stage can never modify (or appear to modify) the
# PostgreSQL rollout state. See write_cockpit_state.
COCKPIT_STATE_FILE="$STATE_ROOT/cockpit-ready.env"
# The verified canonical replica. Installed generations live under
# `generations/<id>` and are selected by a plain-text `current` pointer, so the parent
# repository is NOT itself a bundle: it holds no manifest and no canonical database.
# The parent is what gets mounted and what the running Cockpit is configured to read.
# At request time the Cockpit resolves the atomically committed `current` pointer to an
# immutable generation. `cockpit-ready` still verifies an exact resolved generation
# before readiness is recorded; runtime following of `current` only prevents a healthy
# rotating replica from pruning the generation that a long-lived Cockpit had pinned.
COCKPIT_REPLICA_PARENT_ROOT="/var/lib/opip-learning/canonical-replica"
COCKPIT_REPLICA_CONTAINER_ROOT="/app/canonical-replica"
OFFHOST_EVIDENCE="$STATE_ROOT/offhost-backup.env"
RESTORE_EVIDENCE="$STATE_ROOT/last-restore-drill.env"
ROLLBACK_EVIDENCE="$STATE_ROOT/empty-rollback.env"
POSTGRES_TLS_CA="/etc/opip-data-platform/tls/postgres-ca.crt"
POSTGRES_TLS_CERT="/etc/opip-data-platform/tls/postgres-server.crt"
POSTGRES_TLS_KEY="/etc/opip-data-platform/tls/postgres-server.key"

if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <40-char-main-sha> <empty|backfill|shipper|reads-ready|cockpit-ready>" >&2
  exit 64
fi
case "$STAGE" in
  empty|backfill|shipper|reads-ready|cockpit-ready) ;;
  *) echo "invalid rollout stage: $STAGE" >&2; exit 64 ;;
esac
# Two separate readiness planes. `cockpit-ready` covers ONLY the replica-backed
# read-only Cockpit and grants no historical analytics readiness; `reads-ready`
# covers PostgreSQL/Grafana historical reads and retains its mandatory seven-day
# shipper soak. Neither implies the other.
COCKPIT_STAGE="cockpit-ready"
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run analytics bootstrap as root" >&2
  exit 77
fi
[[ -r "$ENV_FILE" ]] || {
  echo "missing $ENV_FILE; create it with mode 0600 from env.example" >&2
  exit 78
}
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

require_uri_unreserved_password() {
  local name="$1" value="${!1:-}"
  if [[ -z "$value" || ! "$value" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo "$name must be non-empty and use URI-unreserved characters only (A-Z a-z 0-9 - . _ ~)" >&2
    exit 78
  fi
}

require_grafana_verify_full() {
  if [[ "${OPIP_GRAFANA_DB_SSLMODE:-verify-full}" != "verify-full" ]]; then
    echo "OPIP_GRAFANA_DB_SSLMODE must be exactly verify-full" >&2
    exit 78
  fi
}

require_analytics_verify_full_dsn() {
  local name="$1" value="${!1:-}"
  if [[ -z "$value" \
    || "$value" != *"@opip-postgres:"* \
    || "$value" != *"sslmode=verify-full"* \
    || "$value" != *"sslrootcert=$POSTGRES_TLS_CA"* ]]; then
    echo "$name must connect to opip-postgres with sslmode=verify-full and sslrootcert=$POSTGRES_TLS_CA" >&2
    exit 78
  fi
}

write_grafana_env_file() {
  local temporary key
  local -a keys=(
    OPIP_GRAFANA_ADMIN_USER
    OPIP_GRAFANA_ADMIN_PASSWORD
    OPIP_GRAFANA_DB_USER
    OPIP_GRAFANA_DB_PASSWORD
    OPIP_GRAFANA_DB_NAME
    OPIP_GRAFANA_DB_SSLMODE
    OPIP_GRAFANA_POSTGRES_HOST
    OPIP_GRAFANA_POSTGRES_PORT
    OPIP_GRAFANA_BIND_ADDRESS
    OPIP_GRAFANA_HOST_PORT
    OPIP_GRAFANA_HTTP_PORT
    OPIP_GRAFANA_DOMAIN
    OPIP_GRAFANA_ROOT_URL
    OPIP_GRAFANA_SERVE_FROM_SUB_PATH
  )

  temporary="$(mktemp /etc/opip-grafana.env.XXXXXX)"
  : > "$temporary"
  for key in "${keys[@]}"; do
    if ! awk -F= -v key="$key" '$1 == key {print; found=1; exit} END {if (!found) exit 1}' \
      "$ENV_FILE" >> "$temporary"; then
      rm -f -- "$temporary"
      echo "missing required Grafana setting in $ENV_FILE: $key" >&2
      exit 78
    fi
  done
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  mv -f -- "$temporary" "$GRAFANA_ENV_FILE"
}

guard_no_trading_credentials() {
  # The analytics plane is a separate trust boundary. The trading host's operator
  # secret is not merely a dashboard credential: it also gates
  # POST /operator/mode, POST /operator/orders and PATCH /operator/orders/{trade_id},
  # so it can change trading mode and create or modify orders.
  #
  # Copying it here would place an order-capable credential on an externally
  # reachable read-only surface, so this fails closed rather than tolerating it. The
  # Cockpit has its own read-only OPIP_COCKPIT_SECRET instead.
  #
  # Two independent checks, because either alone is bypassable:
  #
  #   1. The sourced environment. This file has already been sourced with `set -a`,
  #      so any name Bash accepted is already present as a variable. Checking the
  #      environment directly covers every syntax Bash accepts, without this guard
  #      having to re-implement Bash's parser.
  #   2. A normalized scan of the file text. Assignment syntax is normalized before
  #      comparison - leading whitespace and an optional `export ` prefix are
  #      stripped - so `export WEBHOOK_SECRET=...` and `  WEBHOOK_SECRET=...` are
  #      recognized rather than slipping past an exact first-field compare.
  local key
  for key in \
    WEBHOOK_SECRET \
    KRAKEN_API_KEY \
    KRAKEN_API_SECRET \
    TELEGRAM_BOT_TOKEN; do
    if [[ -n "${!key:-}" ]] \
      || awk -v key="$key" '
           {
             line = $0
             sub(/^[[:space:]]+/, "", line)
             sub(/^export[[:space:]]+/, "", line)
             if (index(line, key "=") == 1) { found = 1; exit }
           }
           END { exit !found }
         ' "$ENV_FILE"; then
      echo "$key must not be present on the analytics plane" >&2
      echo "it carries trading/order authority and belongs only on the trading host" >&2
      echo "the Cockpit uses its own read-only OPIP_COCKPIT_SECRET" >&2
      exit 78
    fi
  done
}
guard_no_trading_credentials

write_cockpit_env_file() {
  # Derive a Cockpit-only env file from the sealed analytics environment. The
  # allowlist keeps PostgreSQL, Grafana, learning and trading credentials out of
  # the externally reachable read-only process.
  #
  # Runtime replica resolution deliberately receives the mounted repository root,
  # not one immutable generation. The learning sync atomically advances `current`
  # and retains only the active generation plus one fallback. Pinning a generation
  # here would therefore make a healthy long-lived Cockpit fail once normal retention
  # pruned that old directory. The application resolves `current` on each request.
  #
  # Safety is not weakened: `cockpit-ready` still resolves and verifies an exact
  # generation for TARGET_SHA before `cockpit_start` can record COCKPIT_READY_*.
  local temporary key
  local -a keys=(
    OPIP_COCKPIT_SECRET
    OPIP_COCKPIT_BIND_ADDRESS
    OPIP_COCKPIT_HOST_PORT
    OPIP_COCKPIT_HTTP_PORT
  )

  temporary="$(mktemp "${COCKPIT_ENV_FILE}.XXXXXX")"
  : > "$temporary"
  for key in "${keys[@]}"; do
    if ! awk -F= -v key="$key" '$1 == key {print; found=1; exit} END {if (!found) exit 1}' \
      "$ENV_FILE" >> "$temporary"; then
      rm -f -- "$temporary"
      echo "missing required Cockpit setting in $ENV_FILE: $key" >&2
      exit 78
    fi
  done
  printf 'OPIP_CANONICAL_REPLICA_ROOT=%s\n' "$COCKPIT_REPLICA_CONTAINER_ROOT" >> "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  mv -f -- "$temporary" "$COCKPIT_ENV_FILE"
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE" "$@"
}

cockpit_compose() {
  # The Cockpit-only Compose surface. Used by every Cockpit build/start/status operation
  # so the shared analytics file - and therefore its mandatory PostgreSQL/Grafana
  # variables - is never interpolated for a Cockpit deployment.
  #
  # `--env-file` is passed for the same reason the historical stages pass it: the sealed
  # analytics env file is the single source of the non-secret Cockpit settings
  # (OPIP_COCKPIT_BIND_ADDRESS / OPIP_COCKPIT_HOST_PORT / OPIP_COCKPIT_HTTP_PORT). It
  # contains no `${...:?}` requirement reachable from this file, and the Cockpit service
  # needs none of the PostgreSQL/Grafana variables.
  docker compose --env-file "$ENV_FILE" -f "$COCKPIT_COMPOSE" "$@"
}

cockpit_preflight() {
  # Prove the Cockpit is reachable to an OPERATOR, which is a different claim from
  # the container being healthy.
  #
  # The container healthcheck runs inside the container and would pass even when
  # nothing on the host can reach the service: the analytics network is internal, so
  # a container-loopback bind is unreachable from the host, and an unpublished port is
  # unreachable from the host reverse proxy. A green healthcheck is therefore never
  # accepted as evidence of operator reachability here.
  #
  # The reachability proof is deliberately taken at the socket layer rather than with
  # an application-protocol request. What the reverse proxy needs is a TCP endpoint on
  # host loopback, so proving that endpoint exists - and that no public endpoint
  # exists - is a direct proof of exactly the contract that broke. Application-level
  # behaviour (that the page serves, that the API is gated with 401) is proven by the
  # automated test suite, which drives the real ASGI app.
  local bind="${OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1}"
  local port="${OPIP_COCKPIT_HOST_PORT:-8000}"

  # Fail closed on any non-loopback bind. The raw Cockpit HTTP service must remain
  # host-loopback only; the TLS reverse proxy is the sole supported external entry
  # point, so a public bind is refused rather than silently accepted.
  case "$bind" in
    127.*|localhost) ;;
    *)
      echo "OPIP_COCKPIT_BIND_ADDRESS must be host loopback, not '$bind'" >&2
      echo "the cockpit must be reached through the host TLS reverse proxy" >&2
      exit 78
      ;;
  esac

  local health
  health="$(docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
    opip-cockpit 2>/dev/null || true)"
  if [[ "$health" != "healthy" ]]; then
    echo "cockpit container health is '$health', not 'healthy'" >&2
    echo "note: container health alone would not prove operator reachability" >&2
    exit 69
  fi

  command -v ss >/dev/null 2>&1 || {
    echo "iproute2 'ss' is required to prove the cockpit loopback publish" >&2
    exit 69
  }
  local listeners
  listeners="$(ss -ltnH 2>/dev/null || true)"
  [[ -n "$listeners" ]] || {
    echo "could not enumerate listening TCP sockets; cannot prove cockpit reachability" >&2
    exit 69
  }

  # The host-loopback endpoint must exist. Before the exposure fix nothing listened
  # here at all, which is precisely the defect this check exists to catch.
  local loopback_pattern public_pattern
  loopback_pattern="$(printf '%s' "$bind" | sed 's/\./\\./g')"
  if ! grep -Eq "(^|[[:space:]])${loopback_pattern}:${port}([[:space:]]|$)" <<<"$listeners"; then
    echo "cockpit container is healthy but NOT published on host loopback ${bind}:${port}" >&2
    echo "container health does not imply operator reachability; the port must be published" >&2
    exit 69
  fi

  # It must NOT be published on any public interface. This enforces the exposure rule
  # at runtime, not only in the compose file.
  public_pattern="(0\\.0\\.0\\.0|\\[::\\]|\\*|:::):${port}([[:space:]]|$)"
  if grep -Eq "$public_pattern" <<<"$listeners"; then
    echo "cockpit port ${port} is bound on a public interface; refusing" >&2
    echo "the raw cockpit service must remain host-loopback only" >&2
    exit 78
  fi

  # The listener must be this container's publish, not a stale or foreign process
  # that happens to hold the port. `docker port` reports the mapping the Cockpit
  # container itself declares, so it identifies the owner: a stale listener would not
  # produce this mapping, and before the exposure fix the mapping was empty.
  local published
  published="$(docker port opip-cockpit 2>/dev/null || true)"
  if [[ -z "$published" ]]; then
    echo "opip-cockpit publishes no host port; it is unreachable from the reverse proxy" >&2
    exit 69
  fi
  if ! grep -Eq -- "-> ${loopback_pattern}:${port}$" <<<"$published"; then
    echo "opip-cockpit is not published on host loopback ${bind}:${port}" >&2
    echo "published mappings: ${published//$'\n'/, }" >&2
    exit 69
  fi

  echo "cockpit host-loopback preflight OK: ${bind}:${port} is published by opip-cockpit and not public"
  echo "cockpit_exposure=host-loopback"
  echo "cockpit_requires_tls_reverse_proxy=true"
  echo "cockpit_publish_owner=opip-cockpit"
  echo "cockpit_app_behaviour=verified_by_healthcheck_and_test_suite"
}

write_cockpit_state() {
  # Record the complete Cockpit readiness record in one atomic rename.
  #
  # `rollout.env` is the PostgreSQL rollout evidence (DEPLOYED_SHA, EMPTY_*, SHIPPER_*,
  # READS_READY_*), and a replica-backed Cockpit stage must not modify PostgreSQL
  # rollout evidence at all. Keeping Cockpit state in a separate file makes that
  # property provable by inspection rather than by argument.
  #
  # The timestamp and the release SHA are committed together, in one temporary file
  # followed by one rename. Updating them through two separate whole-file replacements
  # would let an interruption between them leave a new timestamp paired with the
  # previous release, which is internally inconsistent durable evidence for an
  # operator. A reader now sees either the previous complete record or the new one.
  local sha="$1" moment="$2" temporary
  install -d -o root -g root -m 0711 "$STATE_ROOT"
  temporary="$(mktemp "$COCKPIT_STATE_FILE.XXXXXX")"
  printf 'COCKPIT_READY_AT_UTC=%q\n' "$moment" > "$temporary"
  printf 'COCKPIT_READY_SHA=%q\n' "$sha" >> "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  mv -f -- "$temporary" "$COCKPIT_STATE_FILE"
}

cockpit_build_image() {
  # Build the exact target image. The tag is release-pinned, so a stale image from an
  # earlier analytics rollout cannot satisfy a different SHA, and rebuilding from the
  # checked-out release is what makes the image provably match TARGET_SHA.
  #
  # Only the Cockpit service is built, through the Cockpit-only Compose file, so the
  # shared analytics file is never interpolated and no PostgreSQL/Grafana variable is
  # required. PostgreSQL is not pulled, built or started in order to build this image.
  #
  # Compose loads this service definition - including its `env_file` - in order to build,
  # and /etc/opip-cockpit.env does not exist yet on a host that has never started the
  # Cockpit. Materialize the secret surface first so the file is loadable.
  #
  # Only when absent. An existing file may already record the generation that verified
  # for the previous release, and this build has not proven itself yet: overwriting it
  # here would drop that root, so a failed build or a failed replica verification would
  # leave a working Cockpit unable to locate its bundle on the next recreation.
  # `cockpit_start` rewrites the file with the newly verified generation immediately
  # before the container starts, so a successful run always ends up authoritative.
  export OPIP_DEPLOYED_SHA="$TARGET_SHA"
  if [[ ! -e "$COCKPIT_ENV_FILE" ]]; then
    write_cockpit_env_file
  fi
  cockpit_compose build opip-cockpit
}

cockpit_replica_root() {
  # Resolve the committed replica generation, through the EXISTING resolver, and print
  # its container-side path.
  #
  # This matters because the replica root is a *repository*: installed bundles live
  # under `generations/<id>` and are selected by a plain-text `current` pointer, so the
  # parent directory holds no manifest and no canonical database. Verifying or reading
  # the parent would look for a bundle where none exists. The pointer semantics
  # (pointer present, well-formed, naming an installed generation) are not reproduced
  # here - `resolve` owns them and already fails closed on every case.
  #
  # Returns non-zero and prints nothing when the generation cannot be resolved, so each
  # caller can choose how strict to be: `cockpit-ready` treats it as fatal, while
  # `reads-ready` falls back to the parent mount so historical readiness never gains a
  # replica dependency.
  local resolved
  [[ -d "$COCKPIT_REPLICA_PARENT_ROOT" ]] || return 1
  resolved="$(
    docker run --rm \
      --network none \
      --read-only \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      --pids-limit 64 \
      --memory 256m \
      --memory-swap 256m \
      --tmpfs /tmp:rw,noexec,nosuid,size=32m \
      -e PYTHONDONTWRITEBYTECODE=1 \
      -v "$COCKPIT_REPLICA_PARENT_ROOT:$COCKPIT_REPLICA_CONTAINER_ROOT:ro" \
      "opip-data-platform:${TARGET_SHA}" \
      python -m app.opip.learning.canonical_replica resolve \
      --host-root "$COCKPIT_REPLICA_CONTAINER_ROOT" 2>/dev/null || true
  )"
  resolved="${resolved%%$'\n'*}"
  [[ -n "$resolved" && "$resolved" != "$COCKPIT_REPLICA_CONTAINER_ROOT" ]] || return 1
  printf '%s\n' "$resolved"
}

cockpit_verify_replica() {
  # Prove the canonical replica before the Cockpit is allowed to serve from it.
  #
  # This invokes the EXISTING verifier CLI - the same `verify` subcommand the learning
  # sync already runs to validate a transferred generation - inside a one-off container
  # built from the exact target image, with no network and the parent mounted
  # read-only. In one implementation it re-checks:
  #
  #   * manifest present, readable, and schema-compatible
  #   * source release SHA == TARGET_SHA (compatibility with the deployed release)
  #   * canonical snapshot present, self-contained, hash- and structurally verified
  #   * companion paper-state / gap artifacts
  #   * freshness against the existing canonical replica freshness contract
  #
  # The shell deliberately reproduces none of those rules, and `--max-age-seconds` is
  # NOT passed: the freshness bound is exactly the existing contract default, so this
  # stage cannot widen it. Missing, unreadable, structurally invalid, SHA-mismatched or
  # stale replicas all exit non-zero from the CLI and therefore fail this stage closed.
  local resolved
  if ! resolved="$(cockpit_replica_root)"; then
    echo "refusing cockpit-ready: no committed canonical replica generation" >&2
    echo "the replica root is a repository of generations selected by 'current'," >&2
    echo "and no usable generation is committed at $COCKPIT_REPLICA_PARENT_ROOT" >&2
    exit 69
  fi

  if ! docker run --rm \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 64 \
    --memory 256m \
    --memory-swap 256m \
    --tmpfs /tmp:rw,noexec,nosuid,size=32m \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$COCKPIT_REPLICA_PARENT_ROOT:$COCKPIT_REPLICA_CONTAINER_ROOT:ro" \
    "opip-data-platform:${TARGET_SHA}" \
    python -m app.opip.learning.canonical_replica verify \
    --root "$resolved" \
    --release-sha "$TARGET_SHA"; then
    echo "refusing cockpit-ready: the canonical replica did not verify for $TARGET_SHA" >&2
    echo "missing, unreadable, structurally invalid, SHA-mismatched or stale replicas fail closed" >&2
    exit 69
  fi
}

cockpit_wait_healthy() {
  # `compose up -d` returns while the container is still `starting`. With the configured
  # 30s healthcheck interval, proving reachability immediately would fail a first
  # deployment even though the service becomes healthy moments later, and the operator
  # would have to run the same stage twice. The wait is bounded and never replaces the
  # preflight, which still proves host-loopback exposure independently.
  local attempt health
  for attempt in $(seq 1 36); do
    health="$(docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      opip-cockpit 2>/dev/null || true)"
    if [[ "$health" == "healthy" ]]; then
      return 0
    fi
    sleep 5
  done
  echo "cockpit container did not become healthy within the bounded wait" >&2
  echo "last container health was '$health'" >&2
  docker logs --tail 40 opip-cockpit >&2 || true
  exit 69
}

cockpit_start() {
  # Start the Cockpit, prove operator reachability, and (only when the replica was
  # verified) record readiness.
  #
  # ``$1`` is the container-side replica root the Cockpit must read. It is written into
  # the Cockpit env file so the process reads the committed generation rather than the
  # parent repository, which contains neither a manifest nor a canonical database.
  #
  # ``$2`` is ``verified`` only when that root passed the replica verifier for this
  # release. COCKPIT_READY_* is published only in that case, because the marker means
  # "verified replica + reachable Cockpit". Health and loopback checks prove neither
  # replica integrity, freshness, nor release binding, so a caller that did not verify
  # the replica must not publish that evidence. `reads-ready` is such a caller: it starts
  # the Cockpit without a replica dependency (historical readiness must not depend on the
  # replica plane) and grants only READS_READY_*, which is its own claim.
  #
  # Ordering is the safety property for the whole primitive: the image and the env file
  # come first, then the service, then the bounded health wait, then the reachability
  # proof, and readiness is recorded last. A failure at any step aborts the stage under
  # `set -e` before any readiness marker is written, so a failed attempt can never leave
  # a false COCKPIT_READY record.
  #
  # Idempotent: the image tag is release-pinned, `compose up -d` recreates only when the
  # definition changed, and the health wait and preflight are re-proven on every run.
  local replica_root="$1"
  local verification="${2:-unverified}"

  write_cockpit_env_file
  cockpit_compose up -d opip-cockpit
  cockpit_wait_healthy
  cockpit_preflight

  if [[ "$verification" == "verified" ]]; then
    write_cockpit_state "$TARGET_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    echo "cockpit_start: replica not verified; COCKPIT_READY_* deliberately not recorded"
  fi

  echo "cockpit_ready_sha=$TARGET_SHA"
  echo "cockpit_replica_root=$replica_root"
  echo "cockpit_replica_verified=$verification"
  echo "cockpit_historical_analytics_ready=false"
  echo "cockpit_raw_port_scope=host_loopback"
}

cockpit_deploy_verified() {
  # The `cockpit-ready` flow: build, prove the replica, then start.
  #
  # Replica verification belongs to this flow only, and it is what makes COCKPIT_READY_*
  # a proven claim. `reads-ready` starts the Cockpit through `cockpit_start` without a
  # replica dependency, because historical PostgreSQL/Grafana readiness must not be
  # blocked by an independent plane; it therefore publishes no COCKPIT_READY_* evidence,
  # and the Cockpit reports replica unavailability through its own API instead.
  local resolved
  cockpit_build_image
  # Verification precedes any container start, so the Cockpit can never serve from an
  # unproven replica.
  cockpit_verify_replica
  if ! resolved="$(cockpit_replica_root)"; then
    echo "refusing cockpit-ready: the verified replica generation is no longer resolvable" >&2
    exit 69
  fi
  cockpit_start "$resolved" verified
}

analytics_host_lock() {
  # Serialize with sync/capture/outcomes on the shared learning/analytics host. Timers
  # use this same lock and will skip rather than compete for RAM or files. Taken by both
  # planes, because both run work on that host.
  exec 8>/var/lock/opip-learning-plane.lock
  if ! flock -w 300 8; then
    echo "learning plane remained busy for five minutes; retry this stage later" >&2
    exit 75
  fi
}

sync_release_checkout() {
  # The target must still be current main, and this checkout is what the Cockpit image is
  # built from, so both planes use it.
  git -C "$REPO_ROOT" fetch --prune origin main
  remote_main="$(git -C "$REPO_ROOT" rev-parse origin/main)"
  [[ "$remote_main" == "$TARGET_SHA" ]] || {
    echo "refusing analytics deploy: target is not current origin/main" >&2
    exit 65
  }
  git -C "$REPO_ROOT" checkout -f main
  git -C "$REPO_ROOT" reset --hard "$TARGET_SHA"
}

# ---------------------------------------------------------------------------
# Cockpit-only stage
# ---------------------------------------------------------------------------
#
# This dispatch sits BEFORE every PostgreSQL/Grafana validation and side effect below,
# and returns immediately. That placement is the isolation guarantee: a Cockpit-only
# deployment neither depends on nor mutates PostgreSQL/Grafana host state.
#
# Excluding the Cockpit later, with guards scattered around the PostgreSQL work, was not
# sufficient: the shared prelude had already required the PostgreSQL admin and shipper
# passwords, the verify-full DSNs and the Grafana settings, rewritten
# /etc/opip-grafana.env, created the PostgreSQL data and state directories, re-derived
# PGDATA ownership, rewritten config/pg_hba.conf, created rollout.env and enforced the
# PostgreSQL capacity floor. A Cockpit deployment could therefore fail on unrelated
# PostgreSQL configuration, or change PostgreSQL host state, while claiming to be
# independent.
#
# `cockpit-ready` grants exactly one thing: the verified canonical replica is good and
# the read-only Cockpit is reachable on host loopback. It writes only COCKPIT_READY_*,
# in its own file, and never READS_READY_* or any other PostgreSQL rollout evidence.
if [[ "$STAGE" == "$COCKPIT_STAGE" ]]; then
  analytics_host_lock
  sync_release_checkout
  cockpit_deploy_verified
  # Status through the Cockpit-only surface: parsing the shared analytics file here would
  # reintroduce the interpolation dependency this stage exists to avoid.
  cockpit_compose ps
  echo "O'Pip analytics data-platform stage succeeded"
  echo "stage=$STAGE"
  echo "sha=$TARGET_SHA"
  exit 0
fi

# ---------------------------------------------------------------------------
# PostgreSQL / Grafana plane
# ---------------------------------------------------------------------------
#
# Every PostgreSQL/Grafana precondition and side effect lives below this line, and the
# Cockpit-only stage has already returned.

require_uri_unreserved_password OPIP_POSTGRES_ADMIN_PASSWORD
require_uri_unreserved_password OPIP_SHIPPER_PASSWORD
require_grafana_verify_full
require_analytics_verify_full_dsn OPIP_ANALYTICS_ADMIN_DATABASE_URL
require_analytics_verify_full_dsn OPIP_ANALYTICS_DATABASE_URL
write_grafana_env_file

analytics_host_lock

# Capacity floor for the PostgreSQL plane. Running PostgreSQL is what needs the memory,
# so this precondition belongs here rather than in the shared prelude.
total_kb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo)"
if [[ ! "$total_kb" =~ ^[0-9]+$ ]] || (( total_kb < 1800 * 1024 )); then
  echo "analytics host must be resized to at least 2 GiB before PostgreSQL" >&2
  exit 70
fi

: "${OPIP_PRODUCTION_PRIVATE_CIDR:?set OPIP_PRODUCTION_PRIVATE_CIDR to production-private-ip/32}"
if [[ ! "$OPIP_PRODUCTION_PRIVATE_CIDR" =~ ^10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/32$ ]]; then
  echo "OPIP_PRODUCTION_PRIVATE_CIDR must be a private 10.x.x.x/32 address" >&2
  exit 78
fi
now_epoch="$(date -u +%s)"
backup_epoch=""
restore_epoch=""

sync_release_checkout

install -d -o root -g root -m 0711 "$STATE_ROOT"
install -d -o root -g root -m 0700 \
  "$STATE_ROOT/config" \
  /var/backups/opip-postgres
install -d -o 472 -g 472 -m 0750 "$STATE_ROOT/grafana"
# Never reset an initialized bind-mounted PGDATA directory to root:root while
# PostgreSQL is running. Recover the directory owner from a canonical file so
# repeat empty deployments remain safe without hard-coding the image UID/GID.
if [[ -r "$STATE_ROOT/postgres/PG_VERSION" ]]; then
  pgdata_owner="$(stat -c '%u:%g' "$STATE_ROOT/postgres/PG_VERSION")"
  if [[ ! "$pgdata_owner" =~ ^[0-9]+:[0-9]+$ ]]; then
    echo "unable to determine existing PostgreSQL data owner" >&2
    exit 70
  fi
  chown "$pgdata_owner" "$STATE_ROOT/postgres"
  chmod 0700 "$STATE_ROOT/postgres"
else
  install -d -o root -g root -m 0700 "$STATE_ROOT/postgres"
fi
if [[ ! -e "$STATE_FILE" ]]; then
  install -o root -g root -m 0600 /dev/null "$STATE_FILE"
fi
# rollout.env is created by this root-only script and contains scalar evidence.
# shellcheck disable=SC1090
source "$STATE_FILE"
EMPTY_DEPLOY_COUNT="${EMPTY_DEPLOY_COUNT:-0}"

cat > "$STATE_ROOT/config/pg_hba.conf" <<EOF
local all all scram-sha-256
host all all 127.0.0.1/32 scram-sha-256
hostssl all all 172.29.0.0/24 scram-sha-256
hostssl all opip_dashboard $OPIP_PRODUCTION_PRIVATE_CIDR scram-sha-256
EOF
chmod 0644 "$STATE_ROOT/config/pg_hba.conf"

write_state() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp "$STATE_ROOT/rollout.env.XXXXXX")"
  awk -F= -v key="$key" '$1 != key' "$STATE_FILE" > "$temporary"
  printf '%s=%q\n' "$key" "$value" >> "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  mv -f -- "$temporary" "$STATE_FILE"
}

validate_postgres_tls_key() {
  local postgres_image runtime_ids pg_uid pg_gid key_metadata key_mode key_uid key_gid

  [[ -f "$POSTGRES_TLS_CA" && -r "$POSTGRES_TLS_CA" ]] || {
    echo "missing or unreadable PostgreSQL TLS CA: $POSTGRES_TLS_CA" >&2
    exit 78
  }
  [[ -f "$POSTGRES_TLS_CERT" && -r "$POSTGRES_TLS_CERT" ]] || {
    echo "missing or unreadable PostgreSQL TLS certificate: $POSTGRES_TLS_CERT" >&2
    exit 78