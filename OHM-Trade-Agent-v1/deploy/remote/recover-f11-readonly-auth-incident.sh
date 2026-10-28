#!/usr/bin/env bash
# O'Pip — owner-run recovery of the durable KRAKEN:READ_ONLY_AUTH incident.
#
# WHY THIS EXISTS (the deployment deadlock)
#   `KRAKEN:READ_ONLY_AUTH` is not monitor-owned: its recovery authority is
#   `system_incidents.RecoveryAuthority.AUTH_CONFIG`, so only a fresh
#   authoritative credential probe may close it. While the row is OPEN the
#   EVIDENCE_SHADOW readiness gate reports BLOCK_INCIDENT_OPEN and the release
#   protection preflight refuses BEFORE mutation - correctly, because an open
#   credential incident must not be deployed over.
#
#   The in-release operation `app.jobs.recover_f11_readonly_auth_incident` cannot
#   break that deadlock on its own: it only exists in the release that the
#   blocked preflight is trying to install. This script therefore performs the
#   SAME sanctioned transition, using only what the CURRENTLY RUNNING release
#   already provides:
#
#     * `system_incidents.observe_recovery` with evidence source AUTH_CONFIG,
#       the canonical authority for exactly this scope;
#     * the existing signed read-only `KrakenPrivateClient` transport.
#
#   No new authority, no second control plane, no database edit, no fabricated
#   evidence. The durable row is closed only by that function, and only after
#   this invocation has itself proven the credential.
#
# WHAT IT DOES NOT DO
#   * It does not deploy, activate, restart or recreate any container, and it
#     never touches `.env`, the release tree, the scheduler or the paper stack.
#   * It never places, modifies or cancels an order and needs no trading
#     permission: the probe is GetApiKeyInfo + Balance + OpenPositions only.
#   * It never reads a funded credential. Only the two dedicated F11 names are
#     copied out of the owner's `.env`, into a root-owned mode 0600 file that is
#     passed as the probe's whole environment and deleted on exit. No value is
#     ever printed.
#   * It never closes any other incident: the row it acts on is the one whose
#     key is exactly `KRAKEN:READ_ONLY_AUTH` and whose class is exactly
#     `KRAKEN_READ_ONLY_AUTH_FAILURE`.
#
# USAGE
#   sudo -n bash recover-f11-readonly-auth-incident.sh            # plan only
#   sudo -n bash recover-f11-readonly-auth-incident.sh --plan     # plan only
#   sudo -n bash recover-f11-readonly-auth-incident.sh --apply    # one transition
#
#   Plan mode performs the full fresh proof, prints the decision, and writes
#   nothing. `--apply` performs the single `observe_recovery` transition when -
#   and only when - the proof, the incident key, the incident class and the
#   incident state all match.
#
# EXIT CODES
#   0   recovered, already recovered, or no open incident for this key
#   3   refused: the proof failed, or the durable row did not match / did not
#       reach RECOVERED. The row is untouched and admissions stay suspended.
#   77  plan only (no mutation attempted)
#   69  unsafe environment (not root, missing command, unreadable inputs)
#
# EVIDENCE
#   Every outcome prints bounded `OPIP_F11_RECOVERY_*` markers. The only
#   credential-derived value is a one-way fingerprint, identical to the one the
#   deploy controller records for the preflight, so the owner can match this
#   recovery against the deployment receipt without either side holding a secret.
set -Eeuo pipefail
umask 077

APP_ROOT="${OPIP_APP_ROOT:-/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1}"
DATA_ROOT="$APP_ROOT/data"
ENV_SOURCE="$APP_ROOT/.env"
CORE_SERVICE="ohm-trade-agent"
#: The ONE incident scope this operation is authorized to close. The durable key
#: is derived by the release (it carries the alert family), so the wrapper binds
#: the scope and lets the probe report the key it actually read.
INCIDENT_SCOPE="KRAKEN:READ_ONLY_AUTH"
#: Dedicated protection names. Literals: this script never searches for a
#: `*_API_KEY` suffix and never asks the funded names for a credential.
KEY_ENV="OPIP_F11_KRAKEN_READONLY_API_KEY"
SECRET_ENV="OPIP_F11_KRAKEN_READONLY_API_SECRET"

EXIT_REFUSED=3
EXIT_PLAN=77
EXIT_UNSAFE=69

MODE=plan
PAIR_FILE=""

die() {
  echo "$*" >&2
  exit "$EXIT_UNSAFE"
}

cleanup() {
  if [[ -n "$PAIR_FILE" && -f "$PAIR_FILE" ]]; then
    rm -f -- "$PAIR_FILE"
  fi
}

trap cleanup EXIT

while (($# > 0)); do
  case "$1" in
    --plan) MODE=plan ;;
    --apply) MODE=apply ;;
    *)
      echo "unknown argument: $1" >&2
      exit 64
      ;;
  esac
  shift
done

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  die "run the F11 incident recovery as root"
fi
for cmd in docker awk sed grep mktemp; do
  command -v "$cmd" >/dev/null 2>&1 || die "missing recovery command: $cmd"
done

# --- The credential pair: exactly the two dedicated names, nothing else -------
# One definition each, non-empty, from a regular non-symlinked owner file. A
# missing or duplicated definition refuses instead of guessing which rotation is
# authoritative.
pair_value() {
  local name="$1" count value quote
  count="$(grep -cE "^[[:space:]]*${name}[[:space:]]*=" "$ENV_SOURCE" || true)"
  [[ "$count" =~ ^[0-9]+$ ]] || count=0
  if [[ "$count" != "1" ]]; then
    die "refusing recovery: $name must be defined exactly once in $ENV_SOURCE (found ${count})"
  fi
  value="$(sed -n "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*//p" "$ENV_SOURCE" \
    | tail -n 1 | tr -d '\r')"
  for quote in '"' "'"; do
    if [[ "$value" == "$quote"*"$quote" && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    fi
  done
  [[ -n "$value" ]] || die "refusing recovery: $name is defined but empty in $ENV_SOURCE"
  printf '%s=%s\n' "$name" "$value"
}

[[ -f "$ENV_SOURCE" && ! -L "$ENV_SOURCE" && -r "$ENV_SOURCE" ]] \
  || die "refusing recovery: $ENV_SOURCE is missing, unreadable or a symlink"

PAIR_FILE="$(mktemp -p /root .opip-f11-recovery.XXXXXX)" \
  || die "refusing recovery: cannot create a secure environment file"
chmod 0600 "$PAIR_FILE"
# Written by the function itself, so a refusal inside it terminates the script
# instead of silently producing a half or empty pair.
pair_value "$KEY_ENV" >> "$PAIR_FILE"
pair_value "$SECRET_ENV" >> "$PAIR_FILE"
# Only the two protection names may be present: the probe must never be able to
# resolve a funded credential from its environment.
pair_lines="$(grep -cE '^[A-Za-z0-9_]+=' "$PAIR_FILE" || true)"
[[ "$pair_lines" == "2" ]] \
  || die "refusing recovery: the probe environment is not exactly the two protection names"
unset pair_lines

# --- The running release is the probe vehicle ---------------------------------
# A content-addressed image ID is executed, so a concurrent retag cannot change
# what runs, and the executed code is the code production is running RIGHT NOW -
# not the candidate release this recovery unblocks.
CORE_IMAGE="$(docker inspect --format '{{.Image}}' "$CORE_SERVICE" 2>/dev/null || true)"
[[ "$CORE_IMAGE" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || die "refusing recovery: the running $CORE_SERVICE image identity is unproven"
[[ -d "$DATA_ROOT" ]] \
  || die "refusing recovery: the durable data root $DATA_ROOT is missing"
echo "OPIP_F11_RECOVERY_CORE_IMAGE=$CORE_IMAGE"
echo "OPIP_F11_RECOVERY_MODE=$MODE"

# --- Fresh proof, then at most one sanctioned transition ----------------------
# The container is read-only except the durable data directory, which
# `observe_recovery` needs for the single registry write under its own lock.
PROBE_RC=0
PROBE_OUTPUT="$(docker run --rm -i \
  --read-only \
  --tmpfs /tmp:rw,nosuid,size=32m \
  --pids-limit 64 \
  --memory 384m \
  --env-file "$PAIR_FILE" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  --workdir /app \
  -v "$DATA_ROOT:/app/data" \
  "$CORE_IMAGE" \
  python - "$MODE" <<'PY'
"""Fresh AUTH_CONFIG proof for the dedicated F11 read-only credential.

Runs inside the currently deployed core image, so it uses exactly the durable
incident registry and the signed read-only transport production already has.
Nothing here mutates market state; the only write is the incident transition,
and only through `system_incidents.observe_recovery`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

MODE = (sys.argv[1] if len(sys.argv) > 1 else "plan").strip().lower()

KEY_ENV = "OPIP_F11_KRAKEN_READONLY_API_KEY"
SECRET_ENV = "OPIP_F11_KRAKEN_READONLY_API_SECRET"

#: The one approved protection key, its exact two scopes and its confirmed
#: Trading-droplet allowlist. Literal mirrors of
#: ``app.exchanges.kraken_protection_credentials``; a test asserts both sides
#: are identical so this pre-merge probe cannot drift from the authority.
KEY_NAME = "OPIP-V2-PROTECTION-READONLY"
PERMISSIONS = frozenset({"query-funds", "query-open-trades"})
PROTECTION_HOST = "178.128.147.125"

EXIT_REFUSED = 3
EXIT_PLAN = 77

_TOKEN = re.compile(r"^[A-Za-z0-9._:/-]{1,96}$")
_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")


def token(value: object) -> str:
    text = str(value or "").strip()
    return text if _TOKEN.fullmatch(text) else "NONE"


def code(value: object) -> str:
    head = str(value or "").strip().split(":", 1)[0].strip()
    return head if _CODE.fullmatch(head) else "NONE"


def normalize_allowlist(entries: object) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in entries or ():
        text = str(raw or "").strip()
        if not text:
            continue
        if text.endswith("/32"):
            text = text[: -len("/32")]
        normalized.append(text)
    return tuple(sorted(normalized))


def fingerprint(api_key: str, name: str, permissions: object,
                allowlist: object) -> str:
    digest = hashlib.sha256()
    digest.update(str(api_key or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(name or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update(",".join(sorted(str(item) for item in permissions or ())).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(",".join(normalize_allowlist(allowlist)).encode("utf-8"))
    return digest.hexdigest()[:16]


from app.exchanges.kraken_private import KrakenPrivateClient  # noqa: E402
from app.services import system_incidents  # noqa: E402

#: The durable incident registry the RUNNING release itself uses. Read from the
#: canonical authority instead of re-deriving "/app/data/...", so this probe can
#: never point at a different file than the runtime that records the incident.
STATE_PATH = str(system_incidents.STATE_FILE)

#: The incident identity, derived - never hand-written. The durable key carries
#: the alert family (``SYSTEM_HEALTH:KRAKEN:READ_ONLY_AUTH``), so a literal
#: "KRAKEN:READ_ONLY_AUTH" lookup would silently match nothing.
SCOPE = system_incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH
INCIDENT_SCOPE = SCOPE.value
INCIDENT_KEY = system_incidents.incident_key(SCOPE)
INCIDENT_CLASS = system_incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value
OPEN_STATES = (
    system_incidents.STATE_OPEN,
    system_incidents.STATE_CHANGED,
    system_incidents.STATE_ESCALATED,
)
STATE_RECOVERED = system_incidents.STATE_RECOVERED

MARKER_ORDER = (
    "OPIP_F11_RECOVERY_INCIDENT_KEY",
    "OPIP_F11_RECOVERY_INCIDENT_SCOPE",
    "OPIP_F11_RECOVERY_IDENTITY",
    "OPIP_F11_RECOVERY_KEY_NAME",
    "OPIP_F11_RECOVERY_PERMISSIONS",
    "OPIP_F11_RECOVERY_IP_ALLOWLIST",
    "OPIP_F11_RECOVERY_FINGERPRINT",
    "OPIP_F11_RECOVERY_BALANCE",
    "OPIP_F11_RECOVERY_OPEN_POSITIONS",
    "OPIP_F11_RECOVERY_INCIDENT_STATE_BEFORE",
    "OPIP_F11_RECOVERY_REASON",
)

markers = {
    "OPIP_F11_RECOVERY_INCIDENT_KEY": token(INCIDENT_KEY),
    "OPIP_F11_RECOVERY_INCIDENT_SCOPE": token(INCIDENT_SCOPE),
    "OPIP_F11_RECOVERY_IDENTITY": "UNVERIFIED",
    "OPIP_F11_RECOVERY_KEY_NAME": "NONE",
    "OPIP_F11_RECOVERY_PERMISSIONS": "NONE",
    "OPIP_F11_RECOVERY_IP_ALLOWLIST": "NONE",
    "OPIP_F11_RECOVERY_FINGERPRINT": "NONE",
    "OPIP_F11_RECOVERY_BALANCE": "NOT_RUN",
    "OPIP_F11_RECOVERY_OPEN_POSITIONS": "NOT_RUN",
    "OPIP_F11_RECOVERY_INCIDENT_STATE_BEFORE": "NONE",
    "OPIP_F11_RECOVERY_REASON": "NOT_RUN",
}


def emit(outcome: str, state_after: str) -> None:
    for key in MARKER_ORDER:
        print(f"{key}={markers[key]}")
    print(f"OPIP_F11_RECOVERY={outcome}")
    print(f"OPIP_F11_RECOVERY_INCIDENT_STATE_AFTER={token(state_after)}")


def reported(raw: dict, names: tuple[str, ...]) -> tuple[bool, object]:
    """First documented spelling, refusing two spellings that disagree."""

    values = [raw[name] for name in names if name in raw]
    if not values:
        return False, None
    first = values[0]
    for value in values[1:]:
        if value != first:
            raise ValueError("conflicting representations")
    return True, first


def read_row() -> dict | None:
    """The durable row whose key is exactly this incident, or None.

    The registry file is the same durable evidence the release reads. A missing
    file means nothing durable has been recorded; an unreadable or malformed one
    refuses (it is never read as "no incident").
    """

    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("state payload is not an object")
    incidents = payload.get("incidents")
    if not isinstance(incidents, dict):
        raise ValueError("state payload carries no incidents object")
    row = incidents.get(INCIDENT_KEY)
    return row if isinstance(row, dict) else None


def prove() -> tuple[bool, str]:
    """Fresh GetApiKeyInfo + Balance + OpenPositions from the injected pair."""

    api_key = os.environ.get(KEY_ENV, "")
    api_secret = os.environ.get(SECRET_ENV, "")
    if not (api_key and api_secret):
        return False, "CREDENTIAL_ABSENT"
    client = KrakenPrivateClient(api_key=api_key, api_secret=api_secret)
    # The signed read-only transport is the only network seam the running
    # release exposes; nothing reached through it here is a mutation endpoint.
    try:
        raw = client._post("GetApiKeyInfo")
    except Exception:  # noqa: BLE001 - an unreadable identity is not proof
        return False, "IDENTITY_UNREADABLE"
    if not isinstance(raw, dict):
        return False, "IDENTITY_MALFORMED"
    try:
        name_seen, name_value = reported(raw, ("api_key_name", "apiKeyName"))
        perms_seen, perms_value = reported(raw, ("permissions",))
        allow_seen, allow_value = reported(raw, ("ip_allowlist", "ipAllowlist"))
    except ValueError:
        return False, "IDENTITY_CONFLICTING"
    if not name_seen or not isinstance(name_value, str) or name_value != KEY_NAME:
        return False, "IDENTITY_KEY_MISMATCH"
    if not perms_seen or not isinstance(perms_value, list) or not all(
        isinstance(item, str) for item in perms_value
    ):
        return False, "SCOPES_UNREADABLE"
    if frozenset(perms_value) != PERMISSIONS:
        return False, "SCOPES_MISMATCH"
    if not allow_seen or not isinstance(allow_value, list) or not all(
        isinstance(item, str) for item in allow_value
    ):
        return False, "IP_ALLOWLIST_UNREADABLE"
    if normalize_allowlist(allow_value) != (PROTECTION_HOST,):
        return False, "IP_ALLOWLIST_MISMATCH"

    markers["OPIP_F11_RECOVERY_IDENTITY"] = "PASS"
    markers["OPIP_F11_RECOVERY_KEY_NAME"] = token(KEY_NAME)
    markers["OPIP_F11_RECOVERY_PERMISSIONS"] = ",".join(
        sorted(token(item) for item in PERMISSIONS)
    )
    markers["OPIP_F11_RECOVERY_IP_ALLOWLIST"] = token(PROTECTION_HOST)
    markers["OPIP_F11_RECOVERY_FINGERPRINT"] = token(
        fingerprint(api_key, name_value, perms_value, allow_value)
    )
    try:
        markers["OPIP_F11_RECOVERY_BALANCE"] = f"OK:{len(client.get_balance())}"
    except Exception as exc:  # noqa: BLE001 - an unreadable account is not proof
        markers["OPIP_F11_RECOVERY_BALANCE"] = f"FAIL:{type(exc).__name__}"
        return False, "BALANCE_UNPROVEN"
    try:
        markers["OPIP_F11_RECOVERY_OPEN_POSITIONS"] = (
            f"OK:{len(client.get_open_positions())}"
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable account is not proof
        markers["OPIP_F11_RECOVERY_OPEN_POSITIONS"] = f"FAIL:{type(exc).__name__}"
        return False, "OPEN_POSITIONS_UNPROVEN"
    return True, "PROOF_PASS"


try:
    row = read_row()
except Exception:  # noqa: BLE001 - unreadable durable evidence is not "absent"
    markers["OPIP_F11_RECOVERY_REASON"] = "STATE_UNREADABLE"
    emit("REFUSED", "UNPROVEN")
    sys.exit(EXIT_REFUSED)

state_before = str(row.get("state") or "NONE") if row else "NONE"
markers["OPIP_F11_RECOVERY_INCIDENT_STATE_BEFORE"] = token(state_before)

proven, reason = prove()
markers["OPIP_F11_RECOVERY_REASON"] = code(reason)
if not proven:
    # No proof, no transition: the row keeps blocking strict F11.
    emit("REFUSED", state_before)
    sys.exit(EXIT_REFUSED)

if row is None:
    # Nothing to close on this host; no evidence is invented either way.
    emit("NO_OPEN_INCIDENT", "NONE")
    sys.exit(0)
if state_before == STATE_RECOVERED:
    emit("ALREADY_RECOVERED", state_before)
    sys.exit(0)
if str(row.get("incident_class") or "") != INCIDENT_CLASS or str(
    row.get("scope") or ""
) != INCIDENT_SCOPE:
    markers["OPIP_F11_RECOVERY_REASON"] = "INCIDENT_IDENTITY_MISMATCH"
    emit("REFUSED", state_before)
    sys.exit(EXIT_REFUSED)
if state_before not in OPEN_STATES:
    markers["OPIP_F11_RECOVERY_REASON"] = "INCIDENT_STATE_UNEXPECTED"
    emit("REFUSED", state_before)
    sys.exit(EXIT_REFUSED)

if MODE != "apply":
    emit("PLAN", state_before)
    sys.exit(EXIT_PLAN)

evidence = (
    "AUTH_CONFIG recovery: dedicated F11 read-only credential proven "
    f"(key={markers['OPIP_F11_RECOVERY_KEY_NAME']} "
    f"fingerprint={markers['OPIP_F11_RECOVERY_FINGERPRINT']} "
    f"balance={markers['OPIP_F11_RECOVERY_BALANCE']} "
    f"open_positions={markers['OPIP_F11_RECOVERY_OPEN_POSITIONS']} "
    f"at={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})"
)
try:
    decision = system_incidents.observe_recovery(
        incident_class=system_incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE,
        scope=system_incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH,
        evidence_source=system_incidents.RecoveryAuthority.AUTH_CONFIG,
        evidence=evidence,
        authoritative=True,
    )
except Exception:  # noqa: BLE001 - a recovery that cannot be attributed is not one
    markers["OPIP_F11_RECOVERY_REASON"] = "RECOVERY_ERROR"
    emit("REFUSED", state_before)
    sys.exit(EXIT_REFUSED)

try:
    after = read_row()
except Exception:  # noqa: BLE001
    after = None
state_after = str(after.get("state") or "NONE") if after else "NONE"
markers["OPIP_F11_RECOVERY_REASON"] = code(getattr(decision, "reason", "") or "NONE")
emit("RECOVERED" if state_after == STATE_RECOVERED else "REFUSED", state_after)
sys.exit(0 if state_after == STATE_RECOVERED else EXIT_REFUSED)
PY
)" || PROBE_RC=$?

printf '%s\n' "$PROBE_OUTPUT"
PROBE_STATE="$(printf '%s\n' "$PROBE_OUTPUT" \
  | sed -n 's/^OPIP_F11_RECOVERY=//p' | tail -n 1)"
PROBE_KEY="$(printf '%s\n' "$PROBE_OUTPUT" \
  | sed -n 's/^OPIP_F11_RECOVERY_INCIDENT_KEY=//p' | tail -n 1)"
PROBE_SCOPE="$(printf '%s\n' "$PROBE_OUTPUT" \
  | sed -n 's/^OPIP_F11_RECOVERY_INCIDENT_SCOPE=//p' | tail -n 1)"
PROBE_AFTER="$(printf '%s\n' "$PROBE_OUTPUT" \
  | sed -n 's/^OPIP_F11_RECOVERY_INCIDENT_STATE_AFTER=//p' | tail -n 1)"
echo "OPIP_F11_RECOVERY_EXIT=$PROBE_RC"
echo "OPIP_F11_RECOVERY_DURABLE_KEY=${PROBE_KEY:-NONE}"

# --- Outcome decision ---------------------------------------------------------
# One decision point, so `--apply` cannot be read as success from a partial or
# masked result. Success needs the exit status AND the explicit outcome marker
# AND, for `--apply`, a confirmed RECOVERED row - exactly the discipline the
# deploy controller applies to the credential verifier.
probe_verdict() {
  # probe_verdict <mode> <exit-code> <outcome> <state-after> <incident-scope>
  #   -> RECOVERED | PLAN_ONLY | NOTHING_TO_RECOVER | REFUSED
  local mode="${1:-}" exit_code="${2:-}" outcome="${3:-}"
  local after="${4:-}" scope="${5:-}"
  [[ "$scope" == "KRAKEN:READ_ONLY_AUTH" ]] || { printf 'REFUSED\n'; return 0; }
  [[ "$exit_code" =~ ^[0-9]+$ ]] || { printf 'REFUSED\n'; return 0; }
  case "$outcome" in
    PLAN)
      if [[ "$mode" == "plan" && "$exit_code" == "77" ]]; then
        printf 'PLAN_ONLY\n'
      else
        printf 'REFUSED\n'
      fi
      ;;
    RECOVERED)
      if [[ "$exit_code" == "0" && "$after" == "RECOVERED" ]]; then
        printf 'RECOVERED\n'
      else
        printf 'REFUSED\n'
      fi
      ;;
    ALREADY_RECOVERED | NO_OPEN_INCIDENT)
      if [[ "$exit_code" == "0" ]]; then
        printf 'NOTHING_TO_RECOVER\n'
      else
        printf 'REFUSED\n'
      fi
      ;;
    *) printf 'REFUSED\n' ;;
  esac
  return 0
}

RECOVERY_VERDICT="$(probe_verdict "$MODE" "$PROBE_RC" "$PROBE_STATE" \
  "$PROBE_AFTER" "$PROBE_SCOPE")"
echo "OPIP_F11_RECOVERY_VERDICT=$RECOVERY_VERDICT"
case "$RECOVERY_VERDICT" in
  RECOVERED)
    exit 0
    ;;
  NOTHING_TO_RECOVER)
    exit 0
    ;;
  PLAN_ONLY)
    exit "$EXIT_PLAN"
    ;;
  *)
    echo "refusing recovery: the incident was not recovered (outcome=${PROBE_STATE:-NONE} exit=$PROBE_RC after=${PROBE_AFTER:-NONE}); the row is unchanged and admissions stay suspended" >&2
    exit "$EXIT_REFUSED"
    ;;
esac
