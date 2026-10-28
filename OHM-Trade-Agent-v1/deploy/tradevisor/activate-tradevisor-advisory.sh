#!/usr/bin/env bash
# O'Pip — Tradevisor V2 advisory ingress activation (Trading droplet only).
#
# WHAT THIS IS
#   One operator-run, idempotent, fail-fast activation gate for the *existing*
#   Trading droplet. It verifies every prerequisite the repository already
#   requires, validates the composed nginx configuration before touching the
#   running edge, and only then turns on the advisory webhook route.
#
# WHAT THIS IS NOT
#   * It is NOT a deployment system. Application and writer releases are still
#     deployed only by the GitHub controller: `/deploy <sha>` and
#     `/deploy-learning <sha>`. This script never invokes `ohm-deploy`, never
#     opens an SSH session, and never bypasses a protected approval.
#   * It never issues, renames or rotates a certificate or a secret. Certbot
#     directories and their symlinks are read-only inputs; `.env` values are
#     read but never printed.
#   * It never enables funded/live execution, Paper-v2, the Committee, or any
#     learning worker. Those authorities are asserted to stay off, and the
#     script fails closed if any of them moved.
#   * It never fabricates Tradevisor evidence. The only requests it sends are
#     a no-token probe that must be refused and a deliberately stale payload
#     that must be refused with 422. Both are refused upstream of the durable
#     inbox, so neither writes a row. A genuine TradingView delivery is
#     required for final G-01 acceptance.
#
# USAGE
#   sudo bash activate-tradevisor-advisory.sh --sha <40-hex-approved-main-sha>
#        [--apply --acknowledge-backup] [--rollback]
#
#   No flags        : read-only gate run. Nothing on the host is changed.
#   --apply         : run every gate, then light the webhook route when all pass.
#   --acknowledge-backup
#                   : required with --apply; the owner attests a restorable
#                     DigitalOcean droplet backup exists for this host.
#   --rollback      : restore the pre-activation posture
#                     (OPIP_TRADEVISOR_ENABLED=false) and re-verify.
#
# EXIT CODES
#   0  gates passed (and, with --apply, activation verified)
#   1  a gate failed; the host was left in its pre-activation posture
#   2  usage error
#   3  rollback failed — treat as an incident and follow the owner checklist
#
set -Eeuo pipefail
umask 077

# --- Verified production identity (bound deliberately, never discovered) -----
readonly PUBLIC_IP="178.128.147.125"
readonly PRIVATE_IP="10.116.0.3"
#: Hostname the webhook is served under; resolves to PUBLIC_IP.
readonly SERVED_NAME="178-128-147-125.sslip.io"
#: Certbot's *certificate name* for the material that covers SERVED_NAME.
#: It is a directory name, not a served identity, and is never renamed here.
readonly CERTBOT_NAME="161-35-106-207.sslip.io"
readonly LIVE_DIR="/etc/letsencrypt/live/${CERTBOT_NAME}"

# --- Existing production layout (owned by the deploy controller) -------------
readonly REPO_ROOT="/opt/OHM-Trade-Agent-v1"
readonly APP_ROOT="${REPO_ROOT}/OHM-Trade-Agent-v1"
readonly ENV_FILE="${APP_ROOT}/.env"
readonly STATE_DIR="/var/lib/ohm-deploy"
readonly LAST_GOOD_FILE="${STATE_DIR}/last-good-sha"
#: Controller-recorded identity of the ACTIVE release. Written only by a
#: manifest-verified activation, so it is the Git-less equivalent of HEAD.
readonly CURRENT_RELEASE_FILE="${STATE_DIR}/current-release.env"
#: Retained per-release manifests and rollback vehicles, and the staged
#: exact-commit artifact provenance. All root-owned controller state.
readonly RELEASES_DIR="${STATE_DIR}/releases"
readonly STAGING_DIR="${STATE_DIR}/staging"
readonly DEPLOY_LOCK="/var/lock/ohm-deploy.lock"

readonly EDGE_SERVICE="ohm-dashboard-edge"
readonly CORE_SERVICE="ohm-trade-agent"
readonly WRITER_SERVICE="opip-canonical-writer"
readonly APP_NET_IP="172.31.240.3"
readonly ROUTE="/webhooks/tradevisor/v1"
readonly NGINX_IMAGE="nginx:1.27-alpine"
readonly NGINX_TEMPLATE="${APP_ROOT}/deploy/nginx/tradevisor-webhook.conf.template"
readonly DASHBOARD_CONF="${APP_ROOT}/deploy/nginx/dashboard-sidecar.conf"

readonly ENABLE_KEY="OPIP_TRADEVISOR_ENABLED"
readonly TOKEN_KEY="OPIP_TRADEVISOR_WEBHOOK_TOKEN"
readonly STAMP_KEY="OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE"
readonly PROXY_KEY="OPIP_TRADEVISOR_TRUSTED_PROXY_IPS"
readonly EXCHANGE_KEY="OPIP_TRADEVISOR_ALLOWED_EXCHANGES"
readonly ALERTS_KEY="OPIP_TRADEVISOR_ALERT_CONFIGS"
readonly INSTRUMENTS_KEY="OPIP_TRADEVISOR_INSTRUMENTS"
readonly OBSERVATION_KEY="OPIP_TRADEVISOR_OBSERVATION_ENABLED"
readonly REQUIRED_KEYS=(
  "$ENABLE_KEY" "$TOKEN_KEY" "$STAMP_KEY" "$PROXY_KEY" "$EXCHANGE_KEY"
  "$ALERTS_KEY" "$INSTRUMENTS_KEY" "$OBSERVATION_KEY"
)
#: TradingView's documented webhook source addresses (public documentation:
#: https://www.tradingview.com/support/solutions/43000529348-about-webhooks/).
readonly -a TRADINGVIEW_IPS=(
  "52.89.214.238/32" "34.212.75.30/32" "54.218.53.128/32" "52.32.178.7/32"
)
readonly DOCUMENTED_DEFAULT_STAMP="tradevisor-verified-by-trusted-proxy"

# --- Run state ---------------------------------------------------------------
APPLY=0
ACK_BACKUP=0
ROLLBACK_ONLY=0
SHA=""
RENDER_DIR=""
EXPECTED_VHOST=""
EDGE_RECREATED=0
ENABLED_FLIPPED=0
PRIOR_ENABLED="false"
FIREWALL_STATE="NOT_CHECKED"
TELEGRAM_STATE="NOT_CHECKED"

usage() { sed -n '2,42p' "$0" | sed -e 's/^# \{0,1\}//'; }
log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
step() { printf '\n== %s ==\n' "$*"; }
pass() { printf 'PASS  %s\n' "$*"; }
info() { printf 'INFO  %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*" >&2; }
die()  { printf 'FAIL  %s\n' "$*" >&2; exit 1; }

cleanup() {
  [[ -n "$RENDER_DIR" && -d "$RENDER_DIR" ]] && rm -rf -- "$RENDER_DIR"
  # set_env_value renames atomically; a leftover temporary means an aborted write.
  find "$APP_ROOT" -maxdepth 1 -name '.env.opip.*' -type f -delete 2>/dev/null || true
  return 0
}
trap cleanup EXIT

# --- Argument parsing --------------------------------------------------------
while (($#)); do
  case "$1" in
    --sha)
      [[ $# -ge 2 ]] || { printf 'FAIL  --sha requires a value\n' >&2; exit 2; }
      SHA="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --acknowledge-backup) ACK_BACKUP=1; shift ;;
    --rollback) ROLLBACK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'FAIL  unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] \
  || { printf 'FAIL  --sha must be 40 lowercase hex characters\n' >&2; exit 2; }
if [[ "$APPLY" == "1" && "$ACK_BACKUP" != "1" ]]; then
  printf 'FAIL  --apply requires --acknowledge-backup (restorable backup attested)\n' >&2
  exit 2
fi

# --- Small helpers -----------------------------------------------------------
compose() { ( cd "$APP_ROOT" && docker compose "$@" ); }

require_cmd() {
  local name
  for name in "$@"; do
    command -v "$name" >/dev/null 2>&1 || die "required command missing: $name"
  done
}

env_count() {
  awk -v k="$1" '
    { line=$0; sub(/\r$/, "", line) }
    line ~ ("^[[:space:]]*(export[[:space:]]+)?" k "=") { n++ }
    END { print n + 0 }
  ' "$ENV_FILE"
}

env_raw() {
  awk -v k="$1" '
    { line=$0; sub(/\r$/, "", line) }
    line ~ ("^[[:space:]]*(export[[:space:]]+)?" k "=") {
      sub(/^[^=]*=/, "", line); value=line
    }
    END { printf "%s", value }
  ' "$ENV_FILE"
}

env_value() {
  local value
  value="$(env_raw "$1")"
  printf '%s' "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

env_digest_masked() {
  sed -E "s|^[[:space:]]*(export[[:space:]]+)?$1=.*|$1=MASKED|" "$ENV_FILE" \
    | sha256sum | cut -d' ' -f1
}

set_env_value() {
  local key="$1" value="$2" tmp count
  count="$(env_count "$key")"
  [[ "$count" -le 1 ]] || die "refusing to rewrite ${key}: ${count} definitions in .env"
  tmp="$(mktemp "${APP_ROOT}/.env.opip.XXXXXX")"
  OPIP_ENV_KEY="$key" OPIP_ENV_VALUE="$value" awk '
    BEGIN { k=ENVIRON["OPIP_ENV_KEY"]; v=ENVIRON["OPIP_ENV_VALUE"]; done=0 }
    {
      line=$0; sub(/\r$/, "", line)
      if (line ~ ("^[[:space:]]*(export[[:space:]]+)?" k "=")) {
        print k "=" v; done=1; next
      }
      print
    }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" > "$tmp"
  chown --reference="$ENV_FILE" "$tmp"
  chmod --reference="$ENV_FILE" "$tmp"
  mv -f -- "$tmp" "$ENV_FILE"
}

is_true() { [[ "${1,,}" =~ ^(1|true|yes|on)$ ]]; }
valid_urlsafe() { [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]; }

valid_alert_configs() {
  local IFS=',' row seen=0
  for row in $1; do
    [[ -n "$row" ]] || continue
    [[ "$row" =~ ^[A-Za-z0-9._-]+=(BUY|SELL)$ ]] || return 1
    seen=$((seen + 1))
  done
  [[ "$seen" -gt 0 ]]
}

valid_instruments() {
  local IFS=',' row seen=0
  for row in $1; do
    [[ -n "$row" ]] || continue
    [[ "$row" =~ ^[A-Za-z0-9]+:[A-Za-z0-9._-]+=[A-Za-z0-9]+/[A-Za-z0-9]+:[A-Za-z0-9]+$ ]] \
      || return 1
    seen=$((seen + 1))
  done
  [[ "$seen" -gt 0 ]]
}

# --- Gate 1: this is the verified Trading droplet ----------------------------
metadata_path() {
  curl -fsS --max-time 3 \
    "http://169.254.169.254/metadata/v1/interfaces/$1/0/ipv4/address" 2>/dev/null \
    | tr -d '\r\n'
}

verify_host_identity() {
  step "GATE host identity"
  [[ "$(id -u)" == "0" ]] || die "run as root on the Trading droplet"
  require_cmd curl openssl docker git awk sed grep flock sha256sum find stat
  local public private
  public="$(metadata_path public || true)"
  private="$(metadata_path private || true)"
  if [[ -z "$public" || -z "$private" ]]; then
    public="$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)"
    private="$public"
    info "droplet metadata service unavailable; falling back to the local address"
  fi
  [[ "$public" == "$PUBLIC_IP" ]] \
    || die "public address ${public:-unknown} is not the verified Trading droplet ${PUBLIC_IP}"
  [[ "$private" == "$PRIVATE_IP" || "$private" == "$public" ]] \
    || die "private address ${private} is not the verified Trading droplet ${PRIVATE_IP}"
  pass "host identity: public ${PUBLIC_IP}, private ${PRIVATE_IP}"
  docker info >/dev/null 2>&1 || die "docker is not usable by root"
  pass "docker engine reachable"
  [[ -f "$APP_ROOT/docker-compose.yml" ]] || die "production compose root missing: $APP_ROOT"
  pass "production compose root present: $APP_ROOT"
}

take_deploy_lock() {
  # The approved controller uses this exact lock, so taking it stops a concurrent
  # /deploy from rewriting .env or the edge while activation is in flight.
  exec 9>"$DEPLOY_LOCK"
  flock -n 9 || die "another deployment or activation holds $DEPLOY_LOCK"
  pass "exclusive deploy lock acquired"
}

# --- Gate 2: credentials and configuration formats ---------------------------
verify_env_file_and_credentials() {
  step "GATE .env integrity and credentials"
  [[ -f "$ENV_FILE" ]] || die ".env missing at $ENV_FILE"
  local mode owner
  mode="$(stat -c '%a' "$ENV_FILE")"
  owner="$(stat -c '%U:%G' "$ENV_FILE")"
  [[ "$mode" == "600" ]] || die ".env mode is ${mode}, expected 600"
  [[ "$owner" == "root:root" ]] || die ".env owner is ${owner}, expected root:root"
  pass ".env is root:root 0600"

  local key count duplicated=""
  for key in "${REQUIRED_KEYS[@]}"; do
    count="$(env_count "$key")"
    [[ "$count" -le 1 ]] || duplicated="${duplicated} ${key}x${count}"
  done
  [[ -z "$duplicated" ]] || die "duplicate .env definitions (last-wins is unsafe):${duplicated}"
  pass "no duplicate definitions for the required Tradevisor keys"

  local enabled token stamp proxy exchange alerts instruments observation
  enabled="$(env_value "$ENABLE_KEY")"
  PRIOR_ENABLED="${enabled:-false}"
  [[ -n "$enabled" ]] || die "$ENABLE_KEY must be present (false before activation)"
  if ! is_true "$enabled" && [[ "${enabled,,}" != "false" ]]; then
    die "$ENABLE_KEY is not a boolean literal (${enabled})"
  fi

  token="$(env_value "$TOKEN_KEY")"
  [[ ${#token} -ge 43 ]] || die "$TOKEN_KEY is missing or shorter than 43 characters"
  valid_urlsafe "$token" || die "$TOKEN_KEY must be URL-safe (letters, digits, '-', '_')"
  pass "$TOKEN_KEY present, ${#token} characters, URL-safe"

  stamp="$(env_value "$STAMP_KEY")"
  [[ ${#stamp} -ge 32 ]] || die "$STAMP_KEY is missing or shorter than 32 characters"
  valid_urlsafe "$stamp" || die "$STAMP_KEY must be URL-safe (letters, digits, '-', '_')"
  [[ "${stamp,,}" != "${DOCUMENTED_DEFAULT_STAMP,,}" ]] \
    || die "$STAMP_KEY is the documented default; production refuses to boot with it"
  pass "$STAMP_KEY present, ${#stamp} characters, not the documented default"

  proxy="$(env_value "$PROXY_KEY")"
  [[ "$proxy" == "172.31.240.2" ]] \
    || die "$PROXY_KEY must be exactly the pinned edge address 172.31.240.2 (got '${proxy}')"
  pass "$PROXY_KEY pins the edge peer only"

  exchange="$(env_value "$EXCHANGE_KEY")"
  [[ "${exchange^^}" == "COINBASE" ]] || die "$EXCHANGE_KEY must be COINBASE (got '${exchange}')"
  pass "$EXCHANGE_KEY=COINBASE"

  alerts="$(env_value "$ALERTS_KEY")"
  if [[ -z "$alerts" ]]; then
    info "$ALERTS_KEY unset: application default tradevisor-1h-buy=BUY,tradevisor-1h-sell=SELL applies"
  else
    valid_alert_configs "$alerts" \
      || die "$ALERTS_KEY must be CSV of <alert-config>=BUY|SELL (e.g. tradevisor-1h-buy=BUY)"
    pass "$ALERTS_KEY matches <alert-config>=BUY|SELL"
  fi

  instruments="$(env_value "$INSTRUMENTS_KEY")"
  if [[ -z "$instruments" ]]; then
    info "$INSTRUMENTS_KEY unset: the application's explicit 10-pair Coinbase table applies"
  else
    valid_instruments "$instruments" \
      || die "$INSTRUMENTS_KEY must be CSV of EXCHANGE:SYMBOL=INSTRUMENT:MARKET (e.g. COINBASE:BTCUSD=BTC/USD:XBTUSD)"
    pass "$INSTRUMENTS_KEY matches EXCHANGE:SYMBOL=INSTRUMENT:MARKET"
  fi

  observation="$(env_value "$OBSERVATION_KEY")"
  if is_true "$observation"; then
    die "$OBSERVATION_KEY must stay false: unqualified WATCH traffic is not part of this activation"
  fi
  pass "$OBSERVATION_KEY is off (no unrestricted WATCH notification traffic)"
}

verify_runtime_config_parses() {
  step "GATE application config parser accepts the operator tables"
  local report
  report="$(compose exec -T "$CORE_SERVICE" python - <<'PY' 2>&1
from app.core.config import Settings
from app.services.tradevisor_ingress import config_from_settings

settings = Settings()
config = config_from_settings(settings)
print(f"ENABLED={str(settings.opip_tradevisor_enabled).lower()}")
print(f"ALERT_CONFIGS={len(config.alert_configs)}:{','.join(sorted(config.alert_configs))}")
print(f"INSTRUMENTS={len(config.instruments)}")
print(f"INTERVAL_MINUTES={config.interval_minutes}")
print(f"EXCHANGES={','.join(sorted(config.allowed_exchanges))}")
print(f"OBSERVATION_ENABLED={str(settings.opip_tradevisor_observation_enabled).lower()}")
PY
)" || die "the application could not resolve the Tradevisor configuration"
  printf '%s\n' "$report" | grep -q '^INSTRUMENTS=[1-9]' \
    || die "OPIP_TRADEVISOR_INSTRUMENTS resolved to no instruments"
  printf '%s\n' "$report" | grep -q '^ALERT_CONFIGS=[1-9]' \
    || die "OPIP_TRADEVISOR_ALERT_CONFIGS resolved to no alert configs"
  printf '%s\n' "$report" | grep -qx '^EXCHANGES=COINBASE' \
    || die "allowed exchanges resolved to something other than COINBASE"
  printf '%s\n' "$report" | grep -qx '^OBSERVATION_ENABLED=false' \
    || die "observation lane resolved to enabled"
  printf '%s\n' "$report" \
    | grep -E '^(ALERT_CONFIGS|INSTRUMENTS|INTERVAL_MINUTES|EXCHANGES)=' \
    | sed 's/^/INFO  /'
  pass "application parser resolved the operator tables"
}

# --- Gate 3: certificate material --------------------------------------------
verify_certificate() {
  step "GATE certificate for ${SERVED_NAME}"
  local cert="${LIVE_DIR}/fullchain.pem" key="${LIVE_DIR}/privkey.pem"
  [[ -e "$cert" ]] || die "missing ${cert} (Certbot certificate name ${CERTBOT_NAME})"
  [[ -s "$cert" ]] || die "empty ${cert}"
  [[ -e "$key" ]] || die "missing ${key}"
  [[ -s "$key" ]] || die "empty ${key}"
  pass "certificate and private key exist under ${LIVE_DIR}"

  local check
  check="$(openssl x509 -in "$cert" -noout -checkhost "$SERVED_NAME" 2>&1 || true)"
  [[ "$check" == *"does match"* ]] || die "certificate does not cover ${SERVED_NAME}: ${check}"
  pass "certificate SAN covers ${SERVED_NAME}"

  openssl x509 -in "$cert" -noout -checkend 604800 >/dev/null 2>&1 \
    || die "certificate expires within 7 days"
  info "notAfter: $(openssl x509 -in "$cert" -noout -enddate | sed 's/^notAfter=//')"

  local cert_pub key_pub
  cert_pub="$(openssl x509 -in "$cert" -noout -pubkey | openssl sha256)"
  key_pub="$(openssl pkey -in "$key" -pubout 2>/dev/null | openssl sha256)"
  [[ -n "$cert_pub" && "$cert_pub" == "$key_pub" ]] \
    || die "privkey.pem does not match fullchain.pem"
  pass "private key matches the certificate"

  grep -q "ssl_certificate     /etc/letsencrypt/live/${CERTBOT_NAME}/fullchain.pem;" \
    "$NGINX_TEMPLATE" \
    || die "deployed webhook template does not use the Certbot-managed certificate path"
  grep -q "cert=/etc/letsencrypt/live/${CERTBOT_NAME}/fullchain.pem" \
    "$APP_ROOT/docker-compose.yml" \
    || die "deployed compose render gate does not use the Certbot-managed certificate path"
  pass "deployed edge config points at the Certbot-managed path"
}

# --- Gate 4: release provenance and recovery artifacts -----------------------
image_revision() {
  local cid
  cid="$(compose ps -q "$1" 2>/dev/null || true)"
  [[ -n "$cid" ]] || { printf 'MISSING'; return 0; }
  docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    "$cid" 2>/dev/null || printf 'UNLABELLED'
}

record_field() {
  # record_field <file> <key> - one value from a controller receipt (.env form).
  awk -F= -v key="$2" '$1 == key { print substr($0, length(key) + 2); exit }' "$1"
}

source_listing() {
  # Deterministic `sha256  relative-path` stream for an application source tree.
  # The same runtime-only exclusions the controller uses, so production data,
  # secrets and byte-code caches never participate in provenance.
  ( cd "$1" || exit 1
    find . -type f \
      -not -path './data/*' -not -path './.env' -not -path './__pycache__/*' \
      -not -name '*.py[cod]' -print0 \
      | LC_ALL=C sort -z | xargs -0 -r sha256sum ) | sed 's|\./||'
}

verify_release_git_worktree() {
  # Historical Git-worktree validation, unchanged.
  local head dirty last_good previous
  [[ -d "$REPO_ROOT/.git" ]] || die "production git checkout missing at $REPO_ROOT"
  head="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  [[ "$head" == "$SHA" ]] || die "checked-out HEAD ${head} is not the approved SHA ${SHA}"
  git -C "$REPO_ROOT" cat-file -e "${SHA}^{commit}" 2>/dev/null \
    || die "approved SHA is not present in the production checkout"
  pass "production checkout is the approved SHA ${SHA}"

  dirty="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
  [[ -z "$dirty" ]] \
    || die "production worktree has tracked modifications: $(printf '%s' "$dirty" | tr '\n' ' ')"
  pass "production worktree is clean"

  [[ -s "$LAST_GOOD_FILE" ]] || die "deploy controller receipt missing: $LAST_GOOD_FILE"
  last_good="$(tr -d '\r\n' < "$LAST_GOOD_FILE")"
  [[ "$last_good" == "$SHA" ]] \
    || die "controller last-good-sha ${last_good} is not the approved SHA ${SHA}"
  pass "controller receipt: last-good-sha matches the approved SHA"

  previous="$(git -C "$REPO_ROOT" rev-parse "${SHA}^" 2>/dev/null || true)"
  if [[ -n "$previous" ]]; then
    info "deployed parent commit (controller rollback candidate): ${previous}"
  else
    warn "approved SHA has no parent; rollback target must come from the controller record"
  fi
}


verify_release_tree() {
  # Certified Git-less release-tree validation. Every input is root-owned
  # controller state, and every value is bound to the approved SHA: the record,
  # the installed manifest, the exact-commit artifact provenance and the live
  # source tree must agree, or the gate refuses.
  local current manifest release_dir provenance actual
  local recorded_sha recorded_tree recorded_digest
  local release_sha release_digest
  local prov_sha prov_tree prov_digest prov_archive prov_count
  local last_good extra previous_sha best_sha best_mtime candidate dir_sha dir_digest mtime
  local manifest_paths live_paths

  current="$CURRENT_RELEASE_FILE"
  [[ -f "$current" && ! -L "$current" ]] \
    || die "controller release record missing at ${current}"
  recorded_sha="$(record_field "$current" sha)"
  recorded_tree="$(record_field "$current" tree)"
  recorded_digest="$(record_field "$current" manifest_digest)"
  [[ "$recorded_sha" =~ ^[0-9a-f]{40}$ ]] \
    || die "controller release record carries no exact SHA"
  [[ "$recorded_sha" == "$SHA" ]] \
    || die "controller release record SHA ${recorded_sha} is not the approved SHA ${SHA}"
  [[ "$recorded_tree" =~ ^[0-9a-f]{40}$ ]] \
    || die "controller release record carries no source tree identity"
  [[ "$recorded_digest" =~ ^[0-9a-f]{64}$ ]] \
    || die "controller release record carries no source manifest digest"
  pass "controller release record names the approved SHA ${SHA}"
  pass "controller release record binds source tree ${recorded_tree} and manifest ${recorded_digest:0:16}"

  [[ -s "$LAST_GOOD_FILE" ]] || die "deploy controller receipt missing: $LAST_GOOD_FILE"
  last_good="$(tr -d '\r\n' < "$LAST_GOOD_FILE")"
  [[ "$last_good" == "$SHA" ]] \
    || die "controller last-good-sha ${last_good} is not the approved SHA ${SHA}"
  pass "controller receipt: last-good-sha matches the approved SHA"

  release_dir="$RELEASES_DIR/$SHA"
  manifest="$release_dir/manifest.sha256"
  [[ -f "$manifest" && ! -L "$manifest" && -s "$manifest" ]] \
    || die "installed release manifest missing: ${manifest}"
  actual="$(sha256sum "$manifest" | awk '{print $1}')"
  [[ "$actual" == "$recorded_digest" ]] \
    || die "installed release manifest ${actual} does not match the controller record ${recorded_digest}"
  pass "installed release manifest matches the controller record"

  [[ -f "$release_dir/release.env" && ! -L "$release_dir/release.env" ]] \
    || die "installed release record missing: ${release_dir}/release.env"
  release_sha="$(record_field "$release_dir/release.env" sha)"
  release_digest="$(record_field "$release_dir/release.env" manifest_digest)"
  [[ "$release_sha" == "$SHA" ]] \
    || die "installed release record SHA ${release_sha} is not the approved SHA ${SHA}"
  [[ "$release_digest" == "$recorded_digest" ]] \
    || die "installed release record digest ${release_digest} is not ${recorded_digest}"

  provenance="$STAGING_DIR/$SHA/provenance.env"
  [[ -f "$provenance" && ! -L "$provenance" ]] \
    || die "exact-commit artifact provenance missing: ${provenance}"
  prov_sha="$(record_field "$provenance" sha)"
  prov_tree="$(record_field "$provenance" tree)"
  prov_digest="$(record_field "$provenance" manifest_digest)"
  prov_archive="$(record_field "$provenance" archive_digest)"
  prov_count="$(record_field "$provenance" file_count)"
  [[ "$prov_sha" == "$SHA" ]] \
    || die "artifact provenance SHA ${prov_sha} is not the approved SHA ${SHA}"
  [[ "$prov_tree" == "$recorded_tree" ]] \
    || die "artifact provenance tree ${prov_tree} is not the recorded tree ${recorded_tree}"
  [[ "$prov_digest" == "$recorded_digest" ]] \
    || die "artifact provenance manifest ${prov_digest} is not ${recorded_digest}"
  [[ "$prov_archive" =~ ^[0-9a-f]{64}$ ]] \
    || die "artifact provenance carries no archive digest"
  [[ "$prov_count" =~ ^[0-9]+$ ]] || die "artifact provenance carries no file count"
  [[ "$prov_count" -eq "$(grep -c . "$manifest")" ]] \
    || die "artifact provenance file count ${prov_count} does not match the installed manifest"
  pass "exact-commit artifact provenance matches the installed release"
  pass "artifact digest ${prov_archive:0:16} binds the commit to the installed source"

  ( cd "$APP_ROOT" && sha256sum --quiet -c "$manifest" ) >/dev/null 2>&1 \
    || die "installed application source does not match the release manifest"
  manifest_paths="$(awk '{print $NF}' "$manifest" | LC_ALL=C sort)"
  live_paths="$(source_listing "$APP_ROOT" | awk '{print $NF}' | LC_ALL=C sort)"
  extra="$(comm -13 <(printf '%s\n' "$manifest_paths") <(printf '%s\n' "$live_paths") || true)"
  [[ -z "$extra" ]] \
    || die "installed application source carries files absent from the release manifest: $(printf '%s' "$extra" | tr '\n' ' ')"
  pass "installed application source matches the release manifest (${prov_count} files)"

  previous_sha=""
  best_sha=""
  best_mtime=0
  shopt -s nullglob
  for candidate in "$RELEASES_DIR"/*/; do
    [[ -s "$candidate/source.tar" && -s "$candidate/manifest.sha256" ]] || continue
    [[ -f "$candidate/release.env" && ! -L "$candidate/release.env" ]] || continue
    dir_sha="$(record_field "$candidate/release.env" sha)"
    [[ "$dir_sha" =~ ^[0-9a-f]{40}$ && "$dir_sha" != "$SHA" ]] || continue
    dir_digest="$(record_field "$candidate/release.env" manifest_digest)"
    [[ "$(sha256sum "$candidate/manifest.sha256" | awk '{print $1}')" == "$dir_digest" ]] \
      || die "retained recovery manifest for ${dir_sha} is tampered"
    tar -tf "$candidate/source.tar" >/dev/null 2>&1 \
      || die "retained recovery artifact for ${dir_sha} is not a valid archive"
    mtime="$(stat -c %Y "$candidate/source.tar" 2>/dev/null || printf '0')"
    if [[ -z "$best_sha" || "$mtime" -gt "$best_mtime" ]]; then
      best_sha="$dir_sha"
      best_mtime="$mtime"
    fi
  done
  shopt -u nullglob
  previous_sha="$best_sha"
  [[ -n "$previous_sha" ]] \
    || die "no retained recovery artifact is available for rollback under ${RELEASES_DIR}"
  pass "retained recovery artifact verified for previous release ${previous_sha}"
  info "controller rollback candidate (verified vehicle): ${previous_sha}"
}

verify_release_and_recovery() {
  step "GATE approved SHA, provenance and recovery artifacts"
  local core_rev writer_rev
  # A Git checkout keeps its historical validation unchanged; a certified
  # release-tree host is proven from the controller's own recorded evidence.
  if [[ -d "$REPO_ROOT/.git" ]]; then
    verify_release_git_worktree
  else
    verify_release_tree
  fi

  core_rev="$(image_revision "$CORE_SERVICE")"
  writer_rev="$(image_revision "$WRITER_SERVICE")"
  [[ "$core_rev" == "$SHA" ]] || die "core image revision ${core_rev} is not ${SHA}"
  [[ "$writer_rev" == "$SHA" ]] || die "writer image revision ${writer_rev} is not ${SHA}"
  pass "application and writer images carry the same approved revision"

  compose config --quiet || die "docker compose rejects the production configuration"
  pass "compose configuration renders with the current .env"

  if docker image ls --filter label=org.opencontainers.image.revision \
      --format '{{.Label "org.opencontainers.image.revision"}}' 2>/dev/null \
      | grep -E '^[0-9a-f]{40}$' | grep -vx "$SHA" | grep -q .; then
    pass "a previous release image is retained locally"
  else
    info "no previous release image retained; recovery is controller redeploy plus droplet backup"
  fi
  pass "owner attested a restorable DigitalOcean backup (--acknowledge-backup)"
}

# --- Gate 5: compose the edge config and validate it before replacing --------
render_expected_vhost() {
  local stamp="$1" target="$2"
  sed "s|__EDGE_VERIFICATION_VALUE__|${stamp}|g" "$NGINX_TEMPLATE" > "$target"
  chmod 600 "$target"
  if grep -q '__EDGE_VERIFICATION_VALUE__' "$target"; then
    die "rendered vhost still contains the unsubstituted placeholder"
  fi
  return 0
}

validate_composed_nginx() {
  step "GATE nginx validates the composed configuration before the edge is replaced"
  local stamp output
  stamp="$(env_value "$STAMP_KEY")"
  RENDER_DIR="$(mktemp -d /dev/shm/opip-tradevisor.XXXXXX)"
  chmod 700 "$RENDER_DIR"
  render_expected_vhost "$stamp" "$RENDER_DIR/tradevisor-webhook.conf"

  output="$(docker run --rm --network none \
    -v "${DASHBOARD_CONF}:/etc/nginx/nginx.conf:ro" \
    -v "${RENDER_DIR}:/etc/nginx/tradevisor:ro" \
    -v "/etc/letsencrypt:/etc/letsencrypt:ro" \
    "$NGINX_IMAGE" nginx -t 2>&1 || true)"
  if ! printf '%s' "$output" | grep -q 'syntax is ok'; then
    printf '%s\n' "$output" >&2
    die "nginx rejected the composed dashboard + webhook configuration"
  fi
  pass "nginx -t accepts the composed configuration (throwaway container, no live change)"
  EXPECTED_VHOST="$RENDER_DIR/tradevisor-webhook.conf"
}

# --- Health checks and route probes ------------------------------------------
wait_core_health() {
  local deadline=$((SECONDS + ${1:-240}))
  while ((SECONDS < deadline)); do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/health 2>/dev/null \
        | grep -q '"status":"ok"'; then
      return 0
    fi
    sleep 3
  done
  return 1
}

wait_writer_health() {
  local deadline=$((SECONDS + ${1:-180})) cid status
  while ((SECONDS < deadline)); do
    cid="$(compose ps -q "$WRITER_SERVICE" 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      status="$(docker inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        "$cid" 2>/dev/null || true)"
      [[ "$status" == "healthy" ]] && return 0
    fi
    sleep 3
  done
  return 1
}

dashboard_code() {
  local code
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
    https://127.0.0.1:8443/dashboard 2>/dev/null || true)"
  printf '%s' "${code:-000}"
}

wait_dashboard() {
  local deadline=$((SECONDS + ${1:-120})) code
  while ((SECONDS < deadline)); do
    code="$(dashboard_code)"
    [[ "$code" =~ ^[23] ]] && return 0
    sleep 2
  done
  return 1
}

served_certificate_matches() {
  local local_fp served_fp
  local_fp="$(openssl x509 -in "${LIVE_DIR}/fullchain.pem" -noout -fingerprint -sha256 \
    2>/dev/null | sed 's/^.*=//')"
  served_fp="$(openssl s_client -connect 127.0.0.1:443 -servername "$SERVED_NAME" </dev/null \
    2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null | sed 's/^.*=//')"
  [[ -n "$local_fp" && -n "$served_fp" && "$local_fp" == "$served_fp" ]]
}

# Sends ONE request from the edge container, so the application sees the pinned
# peer and the edge's own stamp header. The body travels on stdin, so no secret
# ever appears in a process argument list.
probe_route() {
  local body="${1-}" script out code
  script="$(cat <<'SH'
IFS= read -r body || true
wget -S -O /dev/null --timeout=5 \
  --header="x-ohm-tradevisor-verification: ${OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE:-}" \
  --post-data="$body" "http://@APP_IP@:8000@ROUTE@" 2>&1 || true
SH
)"
  script="${script//@APP_IP@/$APP_NET_IP}"
  script="${script//@ROUTE@/$ROUTE}"
  out="$(printf '%s\n' "$body" | compose exec -T "$EDGE_SERVICE" sh -c "$script" 2>&1 || true)"
  code="$(printf '%s' "$out" \
    | sed -n 's/.*HTTP\/1\.[01][[:space:]]\+\([0-9][0-9][0-9]\).*/\1/p' | head -n1)"
  printf '%s' "${code:-000}"
}

# A schema-shaped, deliberately stale alert. It is refused upstream of the
# durable inbox (no row is written), so it proves the route is live and the body
# token verified without inventing a Tradevisor event.
stale_probe_body() {
  local token
  token="$(env_value "$TOKEN_KEY")"
  printf '%s' '{"schema_version":"tradevisor-ingress-v1","source":"TRADEVISOR",'
  printf '%s' '"indicator":"TRADEVISOR_V2","signal":"BUY","symbol":"BTCUSD",'
  printf '%s' '"exchange":"COINBASE","timeframe":"60","signal_price":67000.5,'
  printf '%s' '"bar_opened_at":"2020-01-01T00:00:00Z",'
  printf '%s' '"triggered_at":"2020-01-01T01:00:00Z",'
  printf '%s' '"alert_config_version":"tradevisor-1h-buy",'
  printf '%s' "\"ingest_token\":\"${token}\"}"
}

edge_render_is_current() {
  local live
  live="$(compose exec -T "$EDGE_SERVICE" sh -c \
    'cat /etc/nginx/tradevisor/tradevisor-webhook.conf 2>/dev/null' || true)"
  [[ -n "$live" && "$live" == "$(cat "$EXPECTED_VHOST")" ]]
}

recreate_edge() {
  step "APPLY replace the edge with the validated configuration"
  if edge_render_is_current; then
    pass "edge already runs the validated webhook vhost (idempotent skip)"
    return 0
  fi
  compose up -d --no-deps "$EDGE_SERVICE" || die "edge recreation failed"
  EDGE_RECREATED=1
  wait_dashboard 120 || die "dashboard did not answer on 8443 after the edge restart"
  pass "dashboard answers on 8443 after the edge restart"
  compose exec -T "$EDGE_SERVICE" nginx -t >/dev/null 2>&1 \
    || die "nginx inside the recreated edge rejects its own configuration"
  pass "nginx -t passes inside the recreated edge"
  served_certificate_matches \
    || die "443 does not serve the on-disk certificate for ${SERVED_NAME}"
  pass "443 serves exactly the on-disk certificate for ${SERVED_NAME}"

  local code
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
    --resolve "${SERVED_NAME}:443:127.0.0.1" -X POST --data '{}' \
    "https://${SERVED_NAME}${ROUTE}" 2>/dev/null || true)"
  [[ "$code" == "403" ]] \
    || die "the edge did not refuse a non-TradingView source (got ${code})"
  pass "edge allowlist refuses a non-TradingView source (403)"
}

# --- Gate 6: authorities stay where they were --------------------------------
f11_credential_state() {
  # f11_credential_state <exit-code> <identity> <verdict> <source> <fingerprint>
  #   -> VERIFIED | ABSENT | REFUSED
  #
  # Identical semantics to opip_f11_credential_state in
  # deploy/remote/lib/opip_release_tree.sh (the deploy controller's copy).
  # tests/test_f11_readonly_credential_exception_v1.py runs the same scenario
  # matrix through both, so the two implementations cannot drift.
  #
  # VERIFIED requires the successful process exit AND the explicit PASS verdict
  # AND a PASS identity AND the dedicated protection-pair provenance AND a
  # well-formed one-way fingerprint. A masked exit status, a PASS marker from a
  # job that then failed Balance/OpenPositions, or a missing fingerprint can
  # therefore never be read as success.
  local exit_code="${1:-}" identity="${2:-}" verdict="${3:-}"
  local source="${4:-}" fingerprint="${5:-}"
  [[ "$exit_code" =~ ^[0-9]+$ ]] || { printf 'REFUSED\n'; return 0; }
  if [[ "$exit_code" == "0" && "$verdict" == "PASS" && "$identity" == "PASS" &&
    "$source" == "OPIP_F11_READONLY" && "$fingerprint" =~ ^[0-9a-f]{16}$ ]]; then
    printf 'VERIFIED\n'
    return 0
  fi
  if [[ "$exit_code" != "0" && "$identity" == "UNVERIFIED" &&
    "$verdict" == "FAIL" && "$source" == "NONE" ]]; then
    printf 'ABSENT\n'
    return 0
  fi
  printf 'REFUSED\n'
  return 0
}

verify_authority_boundaries() {
  step "GATE trading and learning authority remain disabled"
  local cid cfg pair key units
  cid="$(compose ps -q "$CORE_SERVICE" 2>/dev/null || true)"
  [[ -n "$cid" ]] || die "core container is not running"
  cfg="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$cid")"
  for pair in \
      "OPIP_PAPER_V2_MODE=off" \
      "OPIP_COMMITTEE_MODE=off" \
      "OPIP_CANONICAL_WRITER_MODE=shadow" \
      "OPIP_FEATURE_BUS_MODE=shadow" \
      "OPIP_TARGET_SPINE_MODE=shadow" \
      "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW"; do
    printf '%s\n' "$cfg" | grep -qx -- "$pair" \
      || die "authority boundary not held: expected ${pair}"
  done
  pass "Paper-v2, Committee, funded execution and learning activation remain off"

  for key in KRAKEN_API_KEY KRAKEN_API_SECRET KRAKEN_PRIVATE_KEY KRKN_API_KEY LIVE_TRADING_KEY; do
    if printf '%s\n' "$cfg" | grep -q "^${key}="; then
      die "funded credential name ${key} is present in the core environment; remove it before activating advisory ingress"
    fi
  done
  pass "no funded/live exchange credential is present in the core environment"

  # The single approved exception, recognised EXPLICITLY and verified
  # INDEPENDENTLY rather than ignored: the dedicated F11 protection key may sit
  # in the core under its own two names, because F11 protection health must be
  # able to read the live account. It is not a funded credential, it is proven
  # read-only, and this gate re-proves it from inside the running core - so an
  # advisory ingress is only ever enabled while the core holds a credential that
  # is positively the dedicated read-only protection key, and never one that
  # merely claims to be.
  local f11_key=0 f11_secret=0
  if printf '%s\n' "$cfg" | grep -q "^OPIP_F11_KRAKEN_READONLY_API_KEY="; then
    f11_key=1
  fi
  if printf '%s\n' "$cfg" | grep -q "^OPIP_F11_KRAKEN_READONLY_API_SECRET="; then
    f11_secret=1
  fi
  if ((f11_key != f11_secret)); then
    die "the F11 read-only protection credential pair is incomplete in the core environment; a half pair is refused"
  fi
  if ((f11_key == 0)); then
    # The profile proven above is EVIDENCE_SHADOW, which requires a verified
    # read-only protection credential. The credential-less posture is the
    # SAFE_BASELINE exception and is never an EVIDENCE_SHADOW success.
    die "the core carries no F11 read-only protection credential; EVIDENCE_SHADOW requires a verified one before advisory ingress is enabled"
  fi
  local f11_markers="" f11_rc=1 f11_identity="" f11_verdict=""
  local f11_source="" f11_fingerprint="" f11_state=""
  # The exit status is captured on BOTH outcomes. `... || true` would erase the
  # only proof that the job completed its own verification, and a PASS marker
  # from a job that then failed Balance/OpenPositions must never be accepted, so
  # success requires the exit code AND the explicit verdict AND a real
  # fingerprint.
  if f11_markers="$(compose exec -T "$CORE_SERVICE" \
    python -m app.jobs.verify_f11_readonly_credential 2>/dev/null)"; then
    f11_rc=0
  else
    f11_rc=$?
  fi
  f11_identity="$(printf '%s\n' "$f11_markers" \
    | sed -n 's/^OPIP_F11_READONLY_IDENTITY=//p' | tail -n 1)"
  f11_verdict="$(printf '%s\n' "$f11_markers" \
    | sed -n 's/^OPIP_F11_READONLY_VERDICT=//p' | tail -n 1)"
  f11_source="$(printf '%s\n' "$f11_markers" \
    | sed -n 's/^OPIP_F11_READONLY_CREDENTIAL_SOURCE=//p' | tail -n 1)"
  f11_fingerprint="$(printf '%s\n' "$f11_markers" \
    | sed -n 's/^OPIP_F11_READONLY_FINGERPRINT=//p' | tail -n 1)"
  f11_state="$(f11_credential_state "$f11_rc" "$f11_identity" "$f11_verdict" \
    "$f11_source" "$f11_fingerprint")"
  echo "OPIP_F11_READONLY_GATE_EXIT=$f11_rc"
  echo "OPIP_F11_READONLY_GATE_IDENTITY=${f11_identity:-UNPROVEN}"
  echo "OPIP_F11_READONLY_GATE_VERDICT=${f11_verdict:-UNPROVEN}"
  echo "OPIP_F11_READONLY_GATE_FINGERPRINT=${f11_fingerprint:-NONE}"
  echo "OPIP_F11_READONLY_GATE_CREDENTIAL=$f11_state"
  [[ "$f11_state" == "VERIFIED" ]] \
    || die "the F11 read-only protection credential did not verify in the running core (state=${f11_state} exit=${f11_rc}); refusing to enable advisory ingress"
  pass "the approved F11 read-only protection credential is present and independently verified"

  units="$(systemctl list-units --all --no-legend --plain 'opip-learning-*' 2>/dev/null \
    | awk '{print $3}' | sort -u || true)"
  if [[ -n "$units" ]] && printf '%s\n' "$units" | grep -qv '^inactive$'; then
    die "a learning worker unit is present on the Trading droplet (${units})"
  fi
  pass "no active learning worker on the Trading droplet"
}

# --- Gate 7: notification transport ------------------------------------------
verify_notification_transport() {
  step "GATE Telegram notification transport"
  local enabled token chat response
  enabled="$(env_value TELEGRAM_ENABLED)"
  token="$(env_value TELEGRAM_BOT_TOKEN)"
  chat="$(env_value TELEGRAM_CHAT_ID)"
  if ! is_true "$enabled"; then
    TELEGRAM_STATE="DISABLED"
    return 1
  fi
  if [[ -z "$token" || -z "$chat" ]]; then
    TELEGRAM_STATE="UNCONFIGURED"
    return 1
  fi
  response="$(curl -fsS --max-time 10 \
    "https://api.telegram.org/bot${token}/getMe" 2>/dev/null || true)"
  if [[ "$response" != *'"ok":true'* ]]; then
    TELEGRAM_STATE="BOT_UNREACHABLE"
    return 1
  fi
  response="$(curl -fsS --max-time 10 --data-urlencode "chat_id=${chat}" \
    "https://api.telegram.org/bot${token}/getChat" 2>/dev/null || true)"
  if [[ "$response" != *'"ok":true'* ]]; then
    TELEGRAM_STATE="CHAT_UNREACHABLE"
    return 1
  fi
  TELEGRAM_STATE="OPERATIONAL"
  pass "bot identity and chat are reachable (no message sent)"
  return 0
}

# --- Cloud firewall (external CSP control) -----------------------------------
print_firewall_action() {
  cat <<'TXT'
OWNER ACTION (DigitalOcean console -> Networking -> Firewalls -> this droplet):
  Add ONE inbound rule and keep every existing rule in place:
    Type:      Custom
    Protocol:  TCP
    Port:      443
    Sources:   52.89.214.238/32, 34.212.75.30/32, 54.218.53.128/32, 52.32.178.7/32
  Do not widen 443 to 0.0.0.0/0. Do not remove or edit any existing rule.
TXT
}

verify_cloud_firewall() {
  step "CLOUD FIREWALL (DigitalOcean)"
  local ip rules id all
  if command -v doctl >/dev/null 2>&1 && doctl account get >/dev/null 2>&1; then
    local found=0
    for id in $(doctl compute firewall list --format ID --no-header 2>/dev/null || true); do
      rules="$(doctl compute firewall get "$id" --format InboundRules --no-header 2>/dev/null || true)"
      all=1
      for ip in "${TRADINGVIEW_IPS[@]}"; do
        printf '%s' "$rules" | grep -q "${ip%/*}" || all=0
      done
      if [[ "$all" == "1" ]]; then
        found=1
      fi
    done
    if [[ "$found" == "1" ]]; then
      FIREWALL_STATE="VERIFIED"
      pass "a droplet firewall allows the four documented TradingView sources on 443"
    else
      FIREWALL_STATE="OWNER_ACTION_REQUIRED"
      warn "no droplet firewall currently allows all four TradingView sources"
      print_firewall_action
    fi
  else
    FIREWALL_STATE="OWNER_ACTION_REQUIRED"
    info "DigitalOcean API access is not available here; the cloud rule is an owner action"
    print_firewall_action
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    if ufw status 2>/dev/null | grep -qE '^443(/tcp)?[[:space:]].*ALLOW'; then
      pass "host ufw allows 443"
    else
      warn "host ufw is active and does not obviously allow 443"
    fi
  fi
}

# --- Route state transitions -------------------------------------------------
verify_route_is_dark() {
  step "GATE the route answers nothing while ${ENABLE_KEY}=false"
  local code
  if is_true "$PRIOR_ENABLED"; then
    info "${ENABLE_KEY} is already true; the dark-route gate does not apply"
    return 0
  fi
  code="$(probe_route '{}')"
  [[ "$code" == "404" ]] \
    || die "the disabled route answered ${code} from the trusted edge, expected 404"
  pass "disabled route answers 404 (no receipt, no route disclosure)"
}

enable_ingress() {
  step "APPLY enable advisory ingress"
  local before_digest
  if is_true "$PRIOR_ENABLED"; then
    pass "${ENABLE_KEY} already true (idempotent skip)"
    return 0
  fi
  before_digest="$(env_digest_masked "$ENABLE_KEY")"
  set_env_value "$ENABLE_KEY" "true"
  ENABLED_FLIPPED=1
  [[ "$(env_digest_masked "$ENABLE_KEY")" == "$before_digest" ]] \
    || die ".env changed outside ${ENABLE_KEY}"
  pass "${ENABLE_KEY}=true written atomically; no other line changed (masked-digest proof)"
  pass ".env permissions preserved: $(stat -c '%U:%G %a' "$ENV_FILE")"
  compose up -d --no-deps "$CORE_SERVICE" || die "core recreation failed"
  wait_core_health 240 \
    || die "core did not become healthy after enabling the ingress (config rejected or boot failure)"
  pass "core healthy with ${ENABLE_KEY}=true"
}

verify_route_authenticated() {
  step "GATE the enabled route authenticates and refuses"
  local wrong_token stale
  wrong_token="$(probe_route '{"ingest_token":"not-the-configured-token"}')"
  [[ "$wrong_token" == "404" ]] \
    || die "a wrong body token was not refused with 404 (got ${wrong_token})"
  pass "wrong body token refused (404)"
  stale="$(probe_route "$(stale_probe_body)")"
  [[ "$stale" == "422" ]] \
    || die "the authenticated route did not refuse a stale payload with 422 (got ${stale})"
  pass "authenticated route refuses a deliberately stale payload (422, no durable row)"
}

# --- Rollback ----------------------------------------------------------------
restore_pre_activation_posture() {
  trap - ERR
  set +e
  warn "restoring the pre-activation posture"
  if [[ "$ENABLED_FLIPPED" == "1" ]]; then
    set_env_value "$ENABLE_KEY" "$PRIOR_ENABLED" || return 3
    compose up -d --no-deps "$CORE_SERVICE" || return 3
    wait_core_health 240 || return 3
    ENABLED_FLIPPED=0
  fi
  if [[ "$EDGE_RECREATED" == "1" ]]; then
    compose up -d --no-deps "$EDGE_SERVICE" || return 3
    wait_dashboard 120 || return 3
  fi
  if is_true "$(env_value "$ENABLE_KEY")"; then
    warn "rollback did not clear ${ENABLE_KEY}"
    return 3
  fi
  wait_writer_health 120 || warn "writer health was not confirmed after rollback"
  printf 'ROLLBACK=%s\n' "RESTORED"
  printf 'ROLLBACK_ENABLED=%s\n' "$(env_value "$ENABLE_KEY")"
  return 0
}

die() {
  printf 'FAIL  %s\n' "$*" >&2
  if [[ "$ENABLED_FLIPPED" == "1" || "$EDGE_RECREATED" == "1" ]]; then
    restore_pre_activation_posture || exit 3
  fi
  exit 1
}

on_error() {
  local rc=$?
  printf 'FAIL  unexpected error at line %s (exit %s)\n' "${1:-?}" "$rc" >&2
  if [[ "$ENABLED_FLIPPED" == "1" || "$EDGE_RECREATED" == "1" ]]; then
    restore_pre_activation_posture || exit 3
  fi
  exit 1
}
trap 'on_error $LINENO' ERR

# --- Final verification and receipt ------------------------------------------
final_verification() {
  step "POST-ACTIVATION verification"
  curl -fsS --max-time 5 http://127.0.0.1:8000/health 2>/dev/null | grep -q '"status":"ok"' \
    || die "application /health is not ok"
  pass "application /health ok"
  wait_writer_health 120 || die "canonical writer is not healthy"
  pass "canonical writer healthy"
  wait_dashboard 120 || die "dashboard is not answering on 8443"
  pass "dashboard answers on 8443 (HTTP $(dashboard_code))"
  served_certificate_matches \
    || die "443 does not serve the on-disk certificate for ${SERVED_NAME}"
  pass "443 serves the certificate for ${SERVED_NAME}"
  compose exec -T "$EDGE_SERVICE" nginx -t >/dev/null 2>&1 \
    || die "nginx -t fails inside the running edge"
  pass "nginx -t passes inside the running edge"
  local code
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
    --resolve "${SERVED_NAME}:443:127.0.0.1" -X POST --data '{}' \
    "https://${SERVED_NAME}${ROUTE}" 2>/dev/null || true)"
  [[ "$code" == "403" ]] \
    || die "an unauthorized source was not refused on 443 (got ${code})"
  pass "unauthorized source refused on 443 (403)"
  verify_authority_boundaries
}

receipt() {
  step "RECEIPT"
  printf 'SHA=%s\n' "$SHA"
  printf 'SERVED_NAME=%s\n' "$SERVED_NAME"
  printf 'CERTBOT_NAME=%s\n' "$CERTBOT_NAME"
  printf 'INGRESS=%s\n' "$(is_true "$(env_value "$ENABLE_KEY")" && echo ENABLED || echo DISABLED)"
  printf 'OBSERVATION_LANE=%s\n' "$(is_true "$(env_value "$OBSERVATION_KEY")" && echo ENABLED || echo DISABLED)"
  printf 'TELEGRAM=%s\n' "$TELEGRAM_STATE"
  printf 'CLOUD_FIREWALL=%s\n' "$FIREWALL_STATE"
  printf 'TRADE_AUTHORITY=UNCHANGED\n'
  printf 'LEARNING=DISABLED\n'
  printf 'GENUINE_TRADINGVIEW_EVENT=REQUIRED\n'
  if is_true "$(env_value "$ENABLE_KEY")"; then
    printf 'VERDICT=%s\n' "LIVE - ADVISORY PLUMBING VERIFIED (genuine TradingView event still required)"
  else
    printf 'VERDICT=%s\n' "READY FOR CONTROLLED ACTIVATION"
  fi
}

main() {
  printf "O'Pip Tradevisor advisory activation (%s)\n" "$SCRIPT_NAME"
  verify_host_identity
  take_deploy_lock
  verify_env_file_and_credentials
  verify_release_and_recovery
  verify_certificate
  verify_runtime_config_parses
  verify_authority_boundaries
  if ! verify_notification_transport; then
    if [[ "$APPLY" == "1" || "$ROLLBACK_ONLY" == "1" ]]; then
      die "Telegram transport is not operational (${TELEGRAM_STATE}): alerts could not be delivered"
    fi
    warn "Telegram transport is not operational (${TELEGRAM_STATE}); --apply would refuse this"
  fi
  validate_composed_nginx
  verify_route_is_dark

  if [[ "$ROLLBACK_ONLY" == "1" ]]; then
    restore_pre_activation_posture || exit 3
    final_verification
    receipt
    return 0
  fi

  if [[ "$APPLY" != "1" ]]; then
    info "read-only gate run complete; no host change was made"
    info "re-run with --apply --acknowledge-backup to activate"
    receipt
    return 0
  fi

  recreate_edge
  enable_ingress
  verify_route_authenticated
  final_verification
  verify_cloud_firewall
  receipt
}

SCRIPT_NAME="${0##*/}"
main "$@"
