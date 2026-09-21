"""Durable SYSTEM_HEALTH incident governance for O'Pip Alert v2.

The owner problem this solves: more than 60 ``DEGRADED`` Telegram alerts in a
day, all describing one continuing operational condition. The fix is *not* to
discard degraded evidence. The fix is to stop translating every repeated
occurrence of one condition into a new human notification.

Contract
--------

One semantic incident produces **at most**:

* one OPEN notification,
* one material-escalation notification,
* one RECOVERED notification,

while every occurrence stays durably auditable (occurrence count, first/last
seen, latest reason, structured metadata, suppressed-notification count), and
every *completed* incident instance stays readable after a later outage reopens
the same scope.

Incident identity is *semantic*: ``SYSTEM_HEALTH:<scope>``. It is deliberately
not derived from the current hour, raw exception text, a changing sentence, a
changing asset list, a stack trace, a price or a timestamp.

Three separations this module exists to enforce
-----------------------------------------------

1. **Occurrence vs recovery cycle.** For connectivity classes the owner's rule
   counts *failed recovery cycles*, not degraded monitor observations. A monitor
   observation is recorded here by :func:`observe_degradation`; exactly one
   higher-level probe cycle is recorded by :func:`record_failed_recovery_cycle`.
   Only the latter advances ``consecutive_recovery_failures``. This is what makes
   "notify after more than six failed recovery cycles" true rather than a claim.

2. **Incident fact state vs human notification state.** Recovery may truthfully
   be recorded as ``RECOVERED`` before Telegram accepts the message; the
   *notification* then stays pending and retryable until a delivery is confirmed.
   Lifecycle state never implies that the owner was notified.

3. **Recovery authority.** A scope may only be closed by evidence that actually
   proves *that* scope healthy (:data:`RecoveryAuthority`). Held-position coverage
   cannot close an operator-state incident, a public probe cannot close a
   read-only incident, and so on.

Layering
--------

This module composes existing authority and forks none of it:

* ``app.services.registry_io``      -- locking + atomic durable writes
* ``app.services.alert_taxonomy``   -- family/plane separation
* ``app.services.kraken_health``    -- low-level failure classification
* ``app.services.telegram_delivery``-- delivery ledger / idempotency (caller)

It introduces no database, Redis, queue, scheduler, service or incident
platform, and it never stores a token, key or credential.

Safety posture
--------------

Lifecycle-critical trade/protection notifications (STOP, TARGET, EMERGENCY,
POSITION_WARNING, EXIT_NOW) never pass through this module, so an open system
incident cannot suppress them. If incident storage is unavailable, degradation
notification **fails open** for the human alert rather than silently hiding a
system failure; the storage failure is reported to the caller as an explicit
reason and printed as audit evidence.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.alert_taxonomy import SYSTEM_HEALTH_FAMILY
from app.services.kraken_health import KrakenFailureClass, classify_failure_text
from app.services.registry_io import (
    RegistryIOError,
    load_json,
    registry_lock,
    save_json_atomic,
)


STATE_FILE = Path("/app/data/system_incidents.json")

#: Schema 2 restructures storage into active incidents + a completed-incident
#: archive, and adds per-notification delivery state. Readers of schema 1 files
#: simply see an empty archive.
SCHEMA_VERSION = 2

#: Owner rule: notify only after connectivity has failed to recover **more than
#: six** consecutive recovery cycles, so the first notification is cycle #7.
#: This is the single source of truth for that threshold.
CONNECTIVITY_RECOVERY_FAILURES_BEFORE_NOTIFY = 7

#: Classes that are deterministic/config-shaped notify on first occurrence
#: instead of burning seven pointless network recovery cycles.
IMMEDIATE_NOTIFY_FAILURES = 1

RESERVATION_LEASE_SECONDS = 300

#: Bounded retry budget for a pending human notification. After this many failed
#: delivery attempts the pending record is retained as evidence but stops being
#: selected, so a broken transport cannot produce unlimited retry attempts.
MAX_NOTIFICATION_ATTEMPTS = 5

MAX_REASON_CHARS = 240
MAX_METADATA_CHARS = 400
MAX_METADATA_ITEMS = 24

# Incident lifecycle states (frozen by docs/architecture/v1.2 contract).
STATE_OPEN = "OPEN"
STATE_CHANGED = "CHANGED"
STATE_ESCALATED = "ESCALATED"
STATE_RECOVERED = "RECOVERED"
INCIDENT_STATES = (STATE_OPEN, STATE_CHANGED, STATE_ESCALATED, STATE_RECOVERED)

# Notification sub-state. Kept separate from the lifecycle state above so the
# frozen OPEN/CHANGED/ESCALATED/RECOVERED vocabulary is preserved while delivery
# progress stays explicit and observable.
NOTIFICATION_NONE = "NONE"
NOTIFICATION_BELOW_THRESHOLD = "BELOW_THRESHOLD"
NOTIFICATION_OPEN_PENDING = "OPEN_PENDING"
NOTIFICATION_OPEN_NOTIFIED = "OPEN_NOTIFIED"
NOTIFICATION_ESCALATION_PENDING = "ESCALATION_PENDING"
NOTIFICATION_ESCALATED_NOTIFIED = "ESCALATED_NOTIFIED"
NOTIFICATION_RECOVERED_PENDING = "RECOVERED_PENDING"
NOTIFICATION_RECOVERED_NOTIFIED = "RECOVERED_NOTIFIED"
#: A pending notification that was superseded because the scope reopened before
#: the owner could be told. Recorded explicitly, never silently dropped.
NOTIFICATION_SUPERSEDED = "SUPERSEDED"

# Notification kinds.
KIND_OPEN = "OPEN"
KIND_ESCALATION = "ESCALATION"
KIND_RECOVERY = "RECOVERY"
NOTIFICATION_KINDS = (KIND_OPEN, KIND_ESCALATION, KIND_RECOVERY)

#: Durable field pair per notification kind: (delivered-at, message-id).
_KIND_FIELDS: dict[str, tuple[str, str]] = {
    KIND_OPEN: ("opened_notification_at", "open_message_id"),
    KIND_ESCALATION: ("escalation_notification_at", "escalation_message_id"),
    KIND_RECOVERY: ("recovered_notification_at", "recovered_message_id"),
}
_KIND_PENDING_STATE: dict[str, str] = {
    KIND_OPEN: NOTIFICATION_OPEN_PENDING,
    KIND_ESCALATION: NOTIFICATION_ESCALATION_PENDING,
    KIND_RECOVERY: NOTIFICATION_RECOVERED_PENDING,
}
_KIND_NOTIFIED_STATE: dict[str, str] = {
    KIND_OPEN: NOTIFICATION_OPEN_NOTIFIED,
    KIND_ESCALATION: NOTIFICATION_ESCALATED_NOTIFIED,
    KIND_RECOVERY: NOTIFICATION_RECOVERED_NOTIFIED,
}

ACTION_NOTIFY_OPEN = "NOTIFY_OPEN"
ACTION_NOTIFY_ESCALATION = "NOTIFY_ESCALATION"
ACTION_NOTIFY_RECOVERY = "NOTIFY_RECOVERY"
ACTION_SUPPRESS_ONGOING = "SUPPRESS_ONGOING"
ACTION_SILENT = "SILENT"

#: Secret-shaped key fragments that must never reach durable incident state.
_FORBIDDEN_METADATA_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "private_key",
    "authorization",
    "auth_header",
    "signature",
    "nonce",
    "credential",
)

#: A value that looks like an opaque credential is redacted regardless of its
#: key name, so a mislabelled field cannot smuggle a secret into durable state.
_OPAQUE_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-+/=:.]{24,}$")
_SECRETISH_VALUE_RE = re.compile(
    r"(secret|token|bearer|api[_-]?key|password|passwd|private[_-]?key)",
    re.IGNORECASE,
)
REDACTED = "[redacted]"


class SystemIncidentClass(str, Enum):
    """Explicit operational incident classes."""

    KRAKEN_CONNECTIVITY_UNAVAILABLE = "KRAKEN_CONNECTIVITY_UNAVAILABLE"
    KRAKEN_READ_ONLY_CONNECTIVITY = "KRAKEN_READ_ONLY_CONNECTIVITY"
    KRAKEN_READ_ONLY_AUTH_FAILURE = "KRAKEN_READ_ONLY_AUTH_FAILURE"
    KRAKEN_RATE_LIMITED = "KRAKEN_RATE_LIMITED"
    HELD_ASSET_PRICING_DEGRADED = "HELD_ASSET_PRICING_DEGRADED"
    POSITION_VERIFICATION_UNAVAILABLE = "POSITION_VERIFICATION_UNAVAILABLE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    DATA_PIPELINE_STALE = "DATA_PIPELINE_STALE"
    UNIFIED_CYCLE_OPERATOR_STATE = "UNIFIED_CYCLE_OPERATOR_STATE"
    INTERNAL_SERVICE_FAILURE = "INTERNAL_SERVICE_FAILURE"


class SystemIncidentScope(str, Enum):
    """Stable semantic scope. This *is* the incident identity."""

    KRAKEN_PUBLIC = "KRAKEN:PUBLIC_CONNECTIVITY"
    KRAKEN_READ_ONLY = "KRAKEN:READ_ONLY_CONNECTIVITY"
    KRAKEN_READ_ONLY_AUTH = "KRAKEN:READ_ONLY_AUTH"
    KRAKEN_RATE_LIMIT = "KRAKEN:RATE_LIMIT"
    KRAKEN_HELD_ASSET_PRICING = "KRAKEN:HELD_ASSET_PRICING"
    KRAKEN_POSITION_VERIFICATION = "KRAKEN:POSITION_VERIFICATION"
    UNIFIED_CYCLE = "UNIFIED_CYCLE:OPERATOR_STATE"
    INTERNAL = "INTERNAL:SERVICE"


class IncidentSeverity(str, Enum):
    DEGRADED = "DEGRADED"
    FAILURE = "FAILURE"
    CRITICAL = "CRITICAL"


class RecoveryAuthority(str, Enum):
    """Which evidence source is allowed to close a scope.

    A scope may only be closed by the authority that can actually prove *that*
    scope healthy. This is what stops unrelated success (for example, complete
    held-position coverage) from closing an operator-state or rate-limit
    incident.
    """

    PUBLIC_PROBE = "PUBLIC_PROBE"
    READ_ONLY_PROBE = "READ_ONLY_PROBE"
    PRICING_COVERAGE = "PRICING_COVERAGE"
    POSITION_COVERAGE = "POSITION_COVERAGE"
    OPERATOR_STATE = "OPERATOR_STATE"
    RATE_LIMIT_CLEARED = "RATE_LIMIT_CLEARED"
    AUTH_CONFIG = "AUTH_CONFIG"
    OWNING_SUBSYSTEM = "OWNING_SUBSYSTEM"


_SEVERITY_RANK = {
    IncidentSeverity.DEGRADED.value: 1,
    IncidentSeverity.FAILURE.value: 2,
    IncidentSeverity.CRITICAL.value: 3,
}


@dataclass(frozen=True)
class IncidentPolicy:
    """How one incident class is governed."""

    notify_after_failures: int
    requires_recovery_cycles: bool
    severity: str


_POLICY: dict[str, IncidentPolicy] = {
    # Connectivity: silent for cycles 1..6, notify on cycle 7, keep recovering.
    SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value: IncidentPolicy(
        CONNECTIVITY_RECOVERY_FAILURES_BEFORE_NOTIFY, True, IncidentSeverity.FAILURE.value
    ),
    SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value: IncidentPolicy(
        CONNECTIVITY_RECOVERY_FAILURES_BEFORE_NOTIFY, True, IncidentSeverity.FAILURE.value
    ),
    # Deterministic / configuration shaped: notify immediately, no 7-cycle loop.
    SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.FAILURE.value
    ),
    SystemIncidentClass.KRAKEN_RATE_LIMITED.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.DEGRADED.value
    ),
    SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.DEGRADED.value
    ),
    SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.DEGRADED.value
    ),
    SystemIncidentClass.PROVIDER_RATE_LIMITED.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.DEGRADED.value
    ),
    SystemIncidentClass.DATA_PIPELINE_STALE.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.DEGRADED.value
    ),
    SystemIncidentClass.UNIFIED_CYCLE_OPERATOR_STATE.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.FAILURE.value
    ),
    SystemIncidentClass.INTERNAL_SERVICE_FAILURE.value: IncidentPolicy(
        IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.FAILURE.value
    ),
}

_DEFAULT_POLICY = IncidentPolicy(
    IMMEDIATE_NOTIFY_FAILURES, False, IncidentSeverity.DEGRADED.value
)

#: Canonical scope for each incident class. One semantic incident == one scope.
_SCOPE_BY_CLASS: dict[str, str] = {
    SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value: SystemIncidentScope.KRAKEN_PUBLIC.value,
    SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value: SystemIncidentScope.KRAKEN_READ_ONLY.value,
    SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value: SystemIncidentScope.KRAKEN_READ_ONLY_AUTH.value,
    SystemIncidentClass.KRAKEN_RATE_LIMITED.value: SystemIncidentScope.KRAKEN_RATE_LIMIT.value,
    SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value: SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value,
    SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE.value: SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value,
    SystemIncidentClass.UNIFIED_CYCLE_OPERATOR_STATE.value: SystemIncidentScope.UNIFIED_CYCLE.value,
    SystemIncidentClass.INTERNAL_SERVICE_FAILURE.value: SystemIncidentScope.INTERNAL.value,
}

#: Recovery authority per scope. Only this evidence may close the scope.
_SCOPE_RECOVERY_AUTHORITY: dict[str, RecoveryAuthority] = {
    SystemIncidentScope.KRAKEN_PUBLIC.value: RecoveryAuthority.PUBLIC_PROBE,
    SystemIncidentScope.KRAKEN_READ_ONLY.value: RecoveryAuthority.READ_ONLY_PROBE,
    SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value: RecoveryAuthority.PRICING_COVERAGE,
    SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value: RecoveryAuthority.POSITION_COVERAGE,
    SystemIncidentScope.UNIFIED_CYCLE.value: RecoveryAuthority.OPERATOR_STATE,
    SystemIncidentScope.KRAKEN_RATE_LIMIT.value: RecoveryAuthority.RATE_LIMIT_CLEARED,
    SystemIncidentScope.KRAKEN_READ_ONLY_AUTH.value: RecoveryAuthority.AUTH_CONFIG,
    SystemIncidentScope.INTERNAL.value: RecoveryAuthority.OWNING_SUBSYSTEM,
}

#: Scopes whose recovery the active-trade monitor is allowed to decide. Every
#: other scope is producer-owned: only the component that can prove the failed
#: condition healthy may close it.
MONITOR_OWNED_SCOPES: frozenset[str] = frozenset(
    {
        SystemIncidentScope.KRAKEN_PUBLIC.value,
        SystemIncidentScope.KRAKEN_READ_ONLY.value,
        SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value,
        SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value,
        SystemIncidentScope.KRAKEN_RATE_LIMIT.value,
    }
)


@dataclass(frozen=True)
class IncidentDecision:
    """What the caller should do about one observation or recovery cycle."""

    action: str
    reason: str
    incident_key: str
    incident_id: str
    state: str
    notification_state: str
    incident_class: str
    scope: str
    severity: str
    occurrence_count: int
    suppressed_notification_count: int
    consecutive_recovery_failures: int
    first_seen_at: str | None
    last_seen_at: str | None
    recovered_at: str | None
    latest_reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
    reservation_token: str | None = None
    #: How many consecutive recovery cycles had failed when the incident
    #: recovered. ``consecutive_recovery_failures`` is reset to zero on
    #: recovery, so the outage's failure history is carried separately.
    recovered_after_recovery_failures: int = 0
    #: Which human notification this decision refers to (``OPEN``,
    #: ``ESCALATION`` or ``RECOVERY``); empty for non-notification decisions.
    notification_kind: str = ""
    #: Bounded retry attempt count already recorded for the pending notification.
    notification_attempts: int = 0

    @property
    def should_notify(self) -> bool:
        return self.action in {
            ACTION_NOTIFY_OPEN,
            ACTION_NOTIFY_ESCALATION,
            ACTION_NOTIFY_RECOVERY,
        }

    @property
    def is_recovery(self) -> bool:
        return self.action == ACTION_NOTIFY_RECOVERY


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime:
    result = value or _now()
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clip(text: Any, limit: int = MAX_REASON_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:limit]


def _safe_text(value: Any, limit: int = MAX_METADATA_CHARS) -> str:
    """Clip free text and redact anything that looks like a credential."""

    text = _clip(value, limit)
    if _SECRETISH_VALUE_RE.search(text) or _OPAQUE_VALUE_RE.match(text):
        return REDACTED
    return text


def _looks_secret(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(fragment in lowered for fragment in _FORBIDDEN_METADATA_KEY_FRAGMENTS)


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only small, secret-free, JSON-safe incident metadata."""

    if not metadata:
        return {}
    safe: dict[str, Any] = {}
    for index, (key, value) in enumerate(dict(metadata).items()):
        if index >= MAX_METADATA_ITEMS:
            break
        name = _clip(key, 64)
        if not name or _looks_secret(name):
            continue
        if isinstance(value, (list, tuple)):
            items = [
                _safe_text(item, 64)
                for item in list(value)[:MAX_METADATA_ITEMS]
                if str(item or "").strip()
            ]
            safe[name] = items
        elif isinstance(value, bool) or value is None:
            safe[name] = value
        elif isinstance(value, (int, float)):
            safe[name] = value
        else:
            safe[name] = _safe_text(value)
    return safe


def policy_for(incident_class: SystemIncidentClass | str) -> IncidentPolicy:
    return _POLICY.get(str(getattr(incident_class, "value", incident_class)), _DEFAULT_POLICY)


def severity_rank(severity: str | None) -> int:
    return _SEVERITY_RANK.get(str(severity or "").upper(), 0)


def incident_key(scope: SystemIncidentScope | str) -> str:
    value = str(getattr(scope, "value", scope))
    return f"{SYSTEM_HEALTH_FAMILY}:{value}"


def incident_identity(
    incident_class: SystemIncidentClass | str,
    scope: SystemIncidentScope | str,
) -> tuple[str, str, str]:
    """Return ``(incident_class, scope, incident_key)`` for one semantic incident."""

    klass = SystemIncidentClass(str(getattr(incident_class, "value", incident_class)))
    resolved_scope = SystemIncidentScope(str(getattr(scope, "value", scope)))
    return klass.value, resolved_scope.value, incident_key(resolved_scope)


def recovery_authority_for_scope(
    scope: SystemIncidentScope | str,
) -> RecoveryAuthority:
    """Return the only evidence source allowed to close ``scope``."""

    value = str(getattr(scope, "value", scope))
    return _SCOPE_RECOVERY_AUTHORITY.get(value, RecoveryAuthority.OWNING_SUBSYSTEM)


def is_monitor_owned_scope(scope: SystemIncidentScope | str) -> bool:
    return str(getattr(scope, "value", scope)) in MONITOR_OWNED_SCOPES


# ---------------------------------------------------------------------------
# Degradation classification
# ---------------------------------------------------------------------------

_RATE_LIMIT_REASON_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "retry-after",
    "retry after",
)
_CREDENTIAL_REASON_MARKERS = (
    "credentials are not configured",
    "credential is not configured",
    "missing credential",
    "private credentials",
    "invalid key",
    "invalid api key",
    "revoked",
    "insufficient permission",
    "permission denied",
)
_PRIVATE_STATE_REASON_MARKERS = (
    "account state unavailable",
    "direct snapshot unavailable",
    "private snapshot",
)
_PRICING_REASON_MARKERS = (
    "usd/stable-quote pricing unavailable",
    "stable-quote pricing unavailable",
    "no usd/stable-quote pair",
)
_POSITION_REASON_MARKERS = (
    "not fully protected",
    "position protection unavailable",
    "managed lifecycle verification incomplete",
    "exposure coverage is incomplete",
    "coverage is incomplete",
    "coverage incomplete",
    "position verification",
)
_OPERATOR_REASON_MARKERS = (
    "operator/capacity state unavailable",
    "operator state unavailable",
)


def classify_degradation_reason(
    reason: str | None,
) -> tuple[SystemIncidentClass, SystemIncidentScope]:
    """Map an operational degradation reason to its semantic incident.

    Precedence is deliberate and load-bearing:

    1. explicit rate limiting (so a 429 is never a fabricated outage),
    2. explicit auth/configuration failure (never a network recovery loop),
    3. explicit genuine connectivity -- including private/read-only reachability,
    4. derived pricing coverage gaps,
    5. derived position/verification gaps,
    6. operator/state read failures,
    7. anything else.

    A resolver returns a *combined* reason. When provider reachability failed and
    pricing gaps are merely a consequence of that outage, the connectivity class
    must win: otherwise an immediate pricing incident would bypass the owner's
    seven-cycle connectivity recovery policy. A pure pricing gap with healthy
    connectivity still classifies as pricing.

    The asset list, wording and ordering inside ``reason`` are metadata, never
    identity: ``USD/stable-quote pricing unavailable for held assets: A,B`` and
    ``...: B,A,C`` are the same open pricing incident.
    """

    text = " ".join(str(reason or "").split())
    lowered = text.lower()
    if not lowered:
        return SystemIncidentClass.INTERNAL_SERVICE_FAILURE, SystemIncidentScope.INTERNAL

    if any(marker in lowered for marker in _RATE_LIMIT_REASON_MARKERS):
        return (
            SystemIncidentClass.KRAKEN_RATE_LIMITED,
            SystemIncidentScope.KRAKEN_RATE_LIMIT,
        )
    if any(marker in lowered for marker in _CREDENTIAL_REASON_MARKERS):
        return (
            SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE,
            SystemIncidentScope.KRAKEN_READ_ONLY_AUTH,
        )

    connectivity_proven = (
        classify_failure_text(text) is KrakenFailureClass.CONNECTIVITY
    )
    private_state = any(marker in lowered for marker in _PRIVATE_STATE_REASON_MARKERS)
    if connectivity_proven:
        # Private/account reachability is a distinct scope from public
        # market-data reachability, even though both are connectivity failures.
        if private_state:
            return (
                SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY,
                SystemIncidentScope.KRAKEN_READ_ONLY,
            )
        return (
            SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
            SystemIncidentScope.KRAKEN_PUBLIC,
        )

    if any(marker in lowered for marker in _PRICING_REASON_MARKERS):
        return (
            SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
            SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        )
    if private_state or any(marker in lowered for marker in _POSITION_REASON_MARKERS):
        return (
            SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE,
            SystemIncidentScope.KRAKEN_POSITION_VERIFICATION,
        )
    if any(marker in lowered for marker in _OPERATOR_REASON_MARKERS):
        return (
            SystemIncidentClass.UNIFIED_CYCLE_OPERATOR_STATE,
            SystemIncidentScope.UNIFIED_CYCLE,
        )
    return SystemIncidentClass.INTERNAL_SERVICE_FAILURE, SystemIncidentScope.INTERNAL


def incident_class_for_scope(scope: SystemIncidentScope | str) -> SystemIncidentClass:
    """Return the canonical incident class for a scope."""

    value = str(getattr(scope, "value", scope))
    for klass_value, scope_value in _SCOPE_BY_CLASS.items():
        if scope_value == value:
            return SystemIncidentClass(klass_value)
    return SystemIncidentClass.INTERNAL_SERVICE_FAILURE


def requires_owner_recovery_cycles(incident_class: SystemIncidentClass | str) -> bool:
    return policy_for(incident_class).requires_recovery_cycles


# ---------------------------------------------------------------------------
# Durable store
# ---------------------------------------------------------------------------


def _load_payload(target: Path) -> dict[str, Any]:
    payload = load_json(target)
    if not isinstance(payload, dict):
        payload = {}
    for name in ("incidents", "archive", "reservations", "unconfirmed_deliveries"):
        if not isinstance(payload.get(name), dict):
            payload[name] = {}
    payload["schema_version"] = SCHEMA_VERSION
    return payload


def _active_reservations(payload: dict[str, Any], *, now: datetime) -> dict[str, dict]:
    cutoff = now.timestamp() - RESERVATION_LEASE_SECONDS
    raw = payload.get("reservations")
    active: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return active
    for token, row in raw.items():
        if not isinstance(row, dict):
            continue
        reserved_at = _parse(row.get("reserved_at"))
        if reserved_at is None or reserved_at.timestamp() < cutoff:
            continue
        active[str(token)] = dict(row)
    return active


def _reservation_for(
    reservations: dict[str, dict],
    key: str,
    kind: str | None = None,
) -> str | None:
    for token, row in reservations.items():
        if str(row.get("incident_key") or "") != key:
            continue
        if kind is not None and str(row.get("kind") or "") != kind:
            continue
        return token
    return None


def _new_row(
    *,
    key: str,
    incident_class: SystemIncidentClass,
    scope: SystemIncidentScope,
    policy: IncidentPolicy,
    now: datetime,
) -> dict[str, Any]:
    return {
        # Timestamp plus a short random suffix keeps reopens of the same scope
        # distinct without any fuzzy reconstruction.
        "incident_id": f"INC:{key}:{now.strftime('%Y%m%dT%H%M%SZ')}:{uuid4().hex[:8]}",
        "incident_key": key,
        "alert_family": SYSTEM_HEALTH_FAMILY,
        "incident_class": incident_class.value,
        "scope": scope.value,
        "state": STATE_OPEN,
        "severity": policy.severity,
        "first_seen_at": now.isoformat(),
        "last_seen_at": now.isoformat(),
        "occurrence_count": 0,
        "consecutive_recovery_failures": 0,
        "recovery_cycles_attempted": 0,
        "suppressed_notification_count": 0,
        "latest_reason": "",
        "metadata": {},
        "notification_state": NOTIFICATION_NONE,
        "pending_notifications": {},
        "opened_notification_at": None,
        "open_message_id": None,
        "last_escalation_at": None,
        "escalation_notification_at": None,
        "escalation_message_id": None,
        "recovered_at": None,
        "recovered_notification_at": None,
        "recovered_message_id": None,
        "last_success_at": None,
    }


def _decision_from_row(
    row: dict[str, Any],
    *,
    action: str,
    reason: str,
    reservation_token: str | None = None,
    notification_kind: str = "",
) -> IncidentDecision:
    pending = row.get("pending_notifications")
    attempts = 0
    if notification_kind and isinstance(pending, dict):
        entry = pending.get(notification_kind)
        if isinstance(entry, dict):
            attempts = int(entry.get("attempts") or 0)
    return IncidentDecision(
        action=action,
        reason=reason,
        incident_key=str(row.get("incident_key") or ""),
        incident_id=str(row.get("incident_id") or ""),
        state=str(row.get("state") or STATE_OPEN),
        notification_state=str(row.get("notification_state") or NOTIFICATION_NONE),
        incident_class=str(row.get("incident_class") or ""),
        scope=str(row.get("scope") or ""),
        severity=str(row.get("severity") or IncidentSeverity.DEGRADED.value),
        occurrence_count=int(row.get("occurrence_count") or 0),
        suppressed_notification_count=int(row.get("suppressed_notification_count") or 0),
        consecutive_recovery_failures=int(row.get("consecutive_recovery_failures") or 0),
        first_seen_at=row.get("first_seen_at"),
        last_seen_at=row.get("last_seen_at"),
        recovered_at=row.get("recovered_at"),
        latest_reason=str(row.get("latest_reason") or ""),
        metadata=dict(row.get("metadata") or {}),
        reservation_token=reservation_token,
        recovered_after_recovery_failures=int(
            row.get("recovery_failures_before_reset") or 0
        ),
        notification_kind=notification_kind,
        notification_attempts=attempts,
    )


def _fail_open_decision(
    *,
    incident_class: SystemIncidentClass,
    scope: SystemIncidentScope,
    reason: str,
    detail: str,
) -> IncidentDecision:
    key = incident_key(scope)
    return IncidentDecision(
        action=ACTION_NOTIFY_OPEN,
        reason="STATE_UNAVAILABLE_FAIL_OPEN",
        incident_key=key,
        incident_id=f"INC:{key}:UNPERSISTED",
        state=STATE_OPEN,
        notification_state=NOTIFICATION_NONE,
        incident_class=incident_class.value,
        scope=scope.value,
        severity=policy_for(incident_class).severity,
        occurrence_count=1,
        suppressed_notification_count=0,
        consecutive_recovery_failures=0,
        first_seen_at=None,
        last_seen_at=None,
        recovered_at=None,
        latest_reason=_safe_text(reason, MAX_REASON_CHARS),
        metadata={"storage_failure": _safe_text(detail, 160)},
        notification_kind=KIND_OPEN,
    )


def _reserve(
    reservations: dict[str, dict],
    *,
    key: str,
    kind: str,
    moment: datetime,
) -> str | None:
    """Reserve one notification attempt, or return None if one is in flight."""

    if _reservation_for(reservations, key, kind) is not None:
        return None
    token = uuid4().hex
    reservations[token] = {
        "incident_key": key,
        "kind": kind,
        "reserved_at": moment.isoformat(),
    }
    return token


def _active_row(
    payload: dict[str, Any],
    *,
    key: str,
    incident_class: SystemIncidentClass,
    scope: SystemIncidentScope,
    policy: IncidentPolicy,
    moment: datetime,
    initial_severity: str | None = None,
) -> dict[str, Any]:
    """Return the row for ``key``, archiving (never overwriting) a finished one.

    A recovered incident is moved to the archive under its own ``incident_id``
    before a fresh row is created, so a later outage on the same scope can never
    destroy the previous lifecycle's counts, reasons, timestamps, message ids or
    recovery evidence.
    """

    incidents = payload["incidents"]
    row = incidents.get(key)
    if isinstance(row, dict) and str(row.get("state")) != STATE_RECOVERED:
        if str(row.get("incident_class")) == incident_class.value:
            return row
        # Same scope, genuinely different class: archive the old lifecycle too.
    if isinstance(row, dict):
        _archive_row(payload, row, moment)
    created = _new_row(
        key=key,
        incident_class=incident_class,
        scope=scope,
        policy=policy,
        now=moment,
    )
    if initial_severity and severity_rank(initial_severity) > severity_rank(
        str(created.get("severity"))
    ):
        # A first observation may already be more severe than the class default.
        created["severity"] = str(initial_severity).upper()
    return created


def _archive_row(payload: dict[str, Any], row: dict[str, Any], moment: datetime) -> None:
    """Move a finished (or superseded) incident row into the durable archive."""

    incident_id = str(row.get("incident_id") or "")
    if not incident_id:
        return
    archived = dict(row)
    archived["archived_at"] = moment.isoformat()
    # A pending notification that can no longer be delivered because the scope
    # already failed again is recorded as explicitly superseded, never dropped
    # silently.
    pending = archived.get("pending_notifications")
    if isinstance(pending, dict) and pending:
        archived["superseded_notifications"] = sorted(pending)
        archived["pending_notifications"] = {}
        archived["notification_state"] = NOTIFICATION_SUPERSEDED
    payload["archive"][incident_id] = archived
    payload["incidents"].pop(str(row.get("incident_key") or ""), None)


def _bump_suppressed(row: dict[str, Any]) -> None:
    row["suppressed_notification_count"] = (
        int(row.get("suppressed_notification_count") or 0) + 1
    )


def _escalate_if_material(
    row: dict[str, Any],
    *,
    observed_severity: str,
    moment: datetime,
) -> bool:
    """Advance severity on a material increase and report a one-time escalation."""

    if severity_rank(observed_severity) <= severity_rank(str(row.get("severity"))):
        return False
    row["severity"] = observed_severity
    row["last_escalation_at"] = moment.isoformat()
    return True


def observe_degradation(
    *,
    incident_class: SystemIncidentClass | str,
    scope: SystemIncidentScope | str,
    reason: str,
    severity: IncidentSeverity | str | None = None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> IncidentDecision:
    """Record one degradation occurrence.

    This records *evidence*: occurrence count, last-seen, latest reason and
    structured metadata. For connectivity classes it deliberately does **not**
    notify and does **not** advance ``consecutive_recovery_failures`` -- those are
    driven exclusively by :func:`record_failed_recovery_cycle`, so the owner's
    "more than six failed recovery cycles" rule cannot be satisfied by mere
    observations.
    """

    klass, scope_value, key = incident_identity(incident_class, scope)
    resolved_class = SystemIncidentClass(klass)
    resolved_scope = SystemIncidentScope(scope_value)
    policy = policy_for(resolved_class)
    observed_severity = str(
        getattr(severity, "value", severity) or policy.severity
    ).upper()
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"

    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            row = _active_row(
                payload,
                key=key,
                incident_class=resolved_class,
                scope=resolved_scope,
                policy=policy,
                moment=moment,
                initial_severity=observed_severity,
            )
            row["occurrence_count"] = int(row.get("occurrence_count") or 0) + 1
            row["last_seen_at"] = moment.isoformat()
            row["latest_reason"] = _safe_text(reason, MAX_REASON_CHARS)
            if metadata:
                row["metadata"] = _safe_metadata(metadata)

            opened = bool(row.get("opened_notification_at"))
            escalated = opened and _escalate_if_material(
                row, observed_severity=observed_severity, moment=moment
            )

            token: str | None = None
            kind = ""
            if not opened and not policy.requires_recovery_cycles:
                # Occurrence-driven classes notify immediately.
                if int(row.get("occurrence_count") or 0) >= policy.notify_after_failures:
                    token = _reserve(reservations, key=key, kind=KIND_OPEN, moment=moment)
                    if token is not None:
                        row["state"] = STATE_OPEN
                        kind = KIND_OPEN
                        action, code = ACTION_NOTIFY_OPEN, "INCIDENT_OPEN"
                    else:
                        _bump_suppressed(row)
                        action, code = ACTION_SUPPRESS_ONGOING, "NOTIFICATION_IN_FLIGHT"
                else:
                    row["notification_state"] = NOTIFICATION_BELOW_THRESHOLD
                    row["state"] = STATE_OPEN
                    _bump_suppressed(row)
                    action, code = ACTION_SILENT, "BELOW_RECOVERY_THRESHOLD"
            elif policy.requires_recovery_cycles:
                # Connectivity notification is owned by the recovery cycle.
                row["state"] = STATE_ESCALATED if escalated else (
                    STATE_CHANGED if opened else STATE_OPEN
                )
                if escalated:
                    _bump_suppressed(row)
                    action, code = ACTION_SUPPRESS_ONGOING, "ESCALATION_ALREADY_NOTIFIED"
                else:
                    action, code = ACTION_SILENT, "GOVERNED_BY_RECOVERY_CYCLE"
            elif escalated:
                token = _reserve(
                    reservations, key=key, kind=KIND_ESCALATION, moment=moment
                )
                if token is not None:
                    row["state"] = STATE_ESCALATED
                    kind = KIND_ESCALATION
                    action, code = ACTION_NOTIFY_ESCALATION, "MATERIAL_ESCALATION"
                else:
                    _bump_suppressed(row)
                    action, code = ACTION_SUPPRESS_ONGOING, "NOTIFICATION_IN_FLIGHT"
            else:
                row["state"] = STATE_CHANGED
                _bump_suppressed(row)
                action, code = ACTION_SUPPRESS_ONGOING, "INCIDENT_ONGOING"

            if kind:
                row["notification_state"] = _KIND_PENDING_STATE[kind]

            payload["incidents"][key] = row
            payload["reservations"] = reservations
            payload["updated_at_utc"] = moment.isoformat()
            save_json_atomic(target, payload)
    except (OSError, TimeoutError, RegistryIOError) as exc:
        # A broken incident registry must never hide a system failure. Fail open
        # for the human notification and keep the storage failure observable.
        print(
            "O'Pip system-incident state unavailable; failing open:",
            f"incident_key={key}",
            f"{type(exc).__name__}: {exc}",
        )
        return _fail_open_decision(
            incident_class=resolved_class,
            scope=resolved_scope,
            reason=reason,
            detail=f"{type(exc).__name__}: {exc}",
        )

    return _decision_from_row(
        row,
        action=action,
        reason=code,
        reservation_token=token,
        notification_kind=kind,
    )


def record_failed_recovery_cycle(
    *,
    incident_class: SystemIncidentClass | str,
    scope: SystemIncidentScope | str,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> IncidentDecision:
    """Record exactly ONE failed higher-level recovery cycle for a scope.

    This is the only path that advances ``consecutive_recovery_failures``, so the
    count is a count of *actual failed probes* rather than of monitor
    observations. Internal transport retries live inside a single probe cycle and
    are not counted here.
    """

    klass, scope_value, key = incident_identity(incident_class, scope)
    resolved_class = SystemIncidentClass(klass)
    resolved_scope = SystemIncidentScope(scope_value)
    policy = policy_for(resolved_class)
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"

    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            row = _active_row(
                payload,
                key=key,
                incident_class=resolved_class,
                scope=resolved_scope,
                policy=policy,
                moment=moment,
            )

            row["consecutive_recovery_failures"] = (
                int(row.get("consecutive_recovery_failures") or 0) + 1
            )
            row["recovery_cycles_attempted"] = (
                int(row.get("recovery_cycles_attempted") or 0) + 1
            )
            row["last_recovery_attempt_at"] = moment.isoformat()
            row["last_seen_at"] = moment.isoformat()
            if reason:
                row["latest_reason"] = _safe_text(reason, MAX_REASON_CHARS)
            if metadata:
                row["metadata"] = _safe_metadata(metadata)

            failures = int(row["consecutive_recovery_failures"])
            opened = bool(row.get("opened_notification_at"))

            token: str | None = None
            kind = ""
            if not opened:
                if failures >= policy.notify_after_failures:
                    token = _reserve(reservations, key=key, kind=KIND_OPEN, moment=moment)
                    if token is not None:
                        row["state"] = STATE_OPEN
                        kind = KIND_OPEN
                        action, code = ACTION_NOTIFY_OPEN, "INCIDENT_OPEN"
                    else:
                        _bump_suppressed(row)
                        action, code = ACTION_SUPPRESS_ONGOING, "NOTIFICATION_IN_FLIGHT"
                else:
                    row["notification_state"] = NOTIFICATION_BELOW_THRESHOLD
                    row["state"] = STATE_OPEN
                    _bump_suppressed(row)
                    action, code = ACTION_SILENT, "BELOW_RECOVERY_THRESHOLD"
            else:
                row["state"] = STATE_CHANGED
                _bump_suppressed(row)
                action, code = ACTION_SUPPRESS_ONGOING, "INCIDENT_ONGOING"

            if kind:
                row["notification_state"] = _KIND_PENDING_STATE[kind]

            payload["incidents"][key] = row
            payload["reservations"] = reservations
            payload["updated_at_utc"] = moment.isoformat()
            save_json_atomic(target, payload)
    except (OSError, TimeoutError, RegistryIOError) as exc:
        print(
            "O'Pip system-incident state unavailable during recovery cycle:",
            f"incident_key={key}",
            f"{type(exc).__name__}: {exc}",
        )
        return _fail_open_decision(
            incident_class=resolved_class,
            scope=resolved_scope,
            reason=reason or "recovery cycle failed",
            detail=f"{type(exc).__name__}: {exc}",
        )

    return _decision_from_row(
        row,
        action=action,
        reason=code,
        reservation_token=token,
        notification_kind=kind,
    )


def observe_recovery(
    *,
    incident_class: SystemIncidentClass | str,
    scope: SystemIncidentScope | str,
    evidence_source: RecoveryAuthority | str,
    evidence: str = "authoritative probe succeeded",
    authoritative: bool = True,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> IncidentDecision:
    """Close an open incident, but only on scope-matched authoritative evidence.

    ``evidence_source`` must equal the scope's :data:`RecoveryAuthority`. A cache
    hit, stale response or unrelated subsystem's success is refused, so complete
    held-position coverage can never close an operator-state, rate-limit or
    internal-service incident.
    """

    klass, scope_value, key = incident_identity(incident_class, scope)
    resolved_class = SystemIncidentClass(klass)
    resolved_scope = SystemIncidentScope(scope_value)
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"

    expected = recovery_authority_for_scope(resolved_scope)
    supplied = str(getattr(evidence_source, "value", evidence_source) or "").upper()
    if supplied != expected.value:
        return IncidentDecision(
            action=ACTION_SILENT,
            reason="EVIDENCE_SOURCE_MISMATCH",
            incident_key=key,
            incident_id="",
            state=STATE_OPEN,
            notification_state=NOTIFICATION_NONE,
            incident_class=resolved_class.value,
            scope=resolved_scope.value,
            severity=policy_for(resolved_class).severity,
            occurrence_count=0,
            suppressed_notification_count=0,
            consecutive_recovery_failures=0,
            first_seen_at=None,
            last_seen_at=None,
            recovered_at=None,
            latest_reason=_safe_text(evidence, MAX_REASON_CHARS),
            metadata={"expected_authority": expected.value, "supplied": supplied},
        )

    if not authoritative:
        # A TTL cache hit, stale response or local registry value is not proof
        # that the failed scope recovered.
        return IncidentDecision(
            action=ACTION_SILENT,
            reason="EVIDENCE_NOT_AUTHORITATIVE",
            incident_key=key,
            incident_id="",
            state=STATE_OPEN,
            notification_state=NOTIFICATION_NONE,
            incident_class=resolved_class.value,
            scope=resolved_scope.value,
            severity=policy_for(resolved_class).severity,
            occurrence_count=0,
            suppressed_notification_count=0,
            consecutive_recovery_failures=0,
            first_seen_at=None,
            last_seen_at=None,
            recovered_at=None,
            latest_reason=_safe_text(evidence, MAX_REASON_CHARS),
        )

    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            row = payload["incidents"].get(key)

            if not isinstance(row, dict):
                synthetic = _new_row(
                    key=key,
                    incident_class=resolved_class,
                    scope=resolved_scope,
                    policy=policy_for(resolved_class),
                    now=moment,
                )
                synthetic["state"] = STATE_RECOVERED
                synthetic["recovered_at"] = moment.isoformat()
                return _decision_from_row(
                    synthetic, action=ACTION_SILENT, reason="NO_OPEN_INCIDENT"
                )

            if str(row.get("state")) == STATE_RECOVERED:
                # Recovery is already a fact. Only the human notification may
                # still be owed, and it is retried through the pending path --
                # never by resending inside observe_recovery.
                return _decision_from_row(
                    row, action=ACTION_SILENT, reason="ALREADY_RECOVERED"
                )

            opened = bool(row.get("opened_notification_at"))
            failures_before_reset = int(row.get("consecutive_recovery_failures") or 0)

            row["state"] = STATE_RECOVERED
            row["recovered_at"] = moment.isoformat()
            row["last_success_at"] = moment.isoformat()
            row["consecutive_recovery_failures"] = 0
            row["recovery_failures_before_reset"] = failures_before_reset
            row["recovery_evidence"] = _safe_text(evidence, MAX_METADATA_CHARS)
            row["recovery_authority"] = expected.value

            token: str | None = None
            kind = ""
            if opened:
                token = _reserve(reservations, key=key, kind=KIND_RECOVERY, moment=moment)
                if token is not None:
                    kind = KIND_RECOVERY
                    row["notification_state"] = NOTIFICATION_RECOVERED_PENDING
                    action, code = ACTION_NOTIFY_RECOVERY, "INCIDENT_RECOVERED"
                else:
                    # Another worker already owes this recovery message.
                    row["pending_notifications"][KIND_RECOVERY] = (
                        row["pending_notifications"].get(KIND_RECOVERY) or {}
                    )
                    action, code = ACTION_SILENT, "RECOVERY_NOTIFICATION_IN_FLIGHT"
            else:
                # The owner was never interrupted below the threshold, so the
                # restoration is silent: no outage alert, no recovery alert.
                row["notification_state"] = NOTIFICATION_NONE
                action, code = ACTION_SILENT, "SILENT_HEALTHY_RESTORATION"

            payload["incidents"][key] = row
            payload["reservations"] = reservations
            payload["updated_at_utc"] = moment.isoformat()
            save_json_atomic(target, payload)
    except (OSError, TimeoutError, RegistryIOError) as exc:
        # Never claim a recovery that cannot be attributed to durable state.
        print(
            "O'Pip system-incident state unavailable; recovery not claimed:",
            f"incident_key={key}",
            f"{type(exc).__name__}: {exc}",
        )
        return IncidentDecision(
            action=ACTION_SILENT,
            reason="STATE_UNAVAILABLE_RECOVERY_UNVERIFIED",
            incident_key=key,
            incident_id="",
            state=STATE_OPEN,
            notification_state=NOTIFICATION_NONE,
            incident_class=resolved_class.value,
            scope=resolved_scope.value,
            severity=policy_for(resolved_class).severity,
            occurrence_count=0,
            suppressed_notification_count=0,
            consecutive_recovery_failures=0,
            first_seen_at=None,
            last_seen_at=None,
            recovered_at=None,
            latest_reason=_safe_text(evidence, MAX_REASON_CHARS),
        )

    return _decision_from_row(
        row,
        action=action,
        reason=code,
        reservation_token=token,
        notification_kind=kind,
    )


# ---------------------------------------------------------------------------
# Notification delivery state (separate from incident fact state)
# ---------------------------------------------------------------------------


def _mark_pending(row: dict[str, Any], kind: str, moment: datetime) -> None:
    pending = row.setdefault("pending_notifications", {})
    entry = pending.get(kind)
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    entry["last_failed_at"] = moment.isoformat()
    pending[kind] = entry
    row["notification_state"] = _KIND_PENDING_STATE[kind]


def confirm_incident_notification(
    *,
    decision: IncidentDecision,
    message_id: int | None = None,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> bool:
    """Commit a *delivered* notification against its reservation.

    Called only after Telegram accepted the message. Clears any pending-retry
    record and any unconfirmed-delivery evidence for this kind.
    """

    kind = decision.notification_kind
    if kind not in NOTIFICATION_KINDS:
        return False
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    at_field, id_field = _KIND_FIELDS[kind]
    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            row = payload["incidents"].get(decision.incident_key)
            if not isinstance(row, dict):
                return False
            if decision.reservation_token:
                reservations.pop(str(decision.reservation_token), None)
            row[at_field] = moment.isoformat()
            if message_id is not None:
                row[id_field] = int(message_id)
            pending = row.get("pending_notifications")
            if isinstance(pending, dict):
                pending.pop(kind, None)
            unconfirmed = payload.get("unconfirmed_deliveries")
            if isinstance(unconfirmed, dict):
                unconfirmed.pop(f"{decision.incident_id}:{kind}", None)
            row["notification_state"] = _KIND_NOTIFIED_STATE[kind]
            payload["incidents"][decision.incident_key] = row
            payload["reservations"] = reservations
            save_json_atomic(target, payload)
        return True
    except (OSError, TimeoutError, RegistryIOError):
        return False


def release_incident_notification(
    *,
    decision: IncidentDecision,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> bool:
    """Record a failed delivery attempt, keeping the notification retryable.

    The incident's *fact* state is never rewound (a recovery really did happen).
    Only the delivery obligation stays pending, recorded with a bounded attempt
    count so it remains selected for reconciliation.
    """

    kind = decision.notification_kind
    if kind not in NOTIFICATION_KINDS:
        return False
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            if decision.reservation_token:
                reservations.pop(str(decision.reservation_token), None)
            row = payload["incidents"].get(decision.incident_key)
            if not isinstance(row, dict):
                return False
            _mark_pending(row, kind, moment)
            payload["incidents"][decision.incident_key] = row
            payload["reservations"] = reservations
            save_json_atomic(target, payload)
        return True
    except (OSError, TimeoutError, RegistryIOError):
        return False


def record_unconfirmed_delivery(
    *,
    decision: IncidentDecision,
    message_id: int,
    now: datetime | None = None,
    state_file: Path | None = None,
    confirm_attempts: int = 0,
) -> bool:
    """Durably record a Telegram message that could not be committed to state.

    This is reconciliation evidence tied to ``incident_id`` + notification kind +
    Telegram ``message_id``, so a later cycle can commit the message that was
    already sent instead of sending a duplicate.
    """

    kind = decision.notification_kind
    if kind not in NOTIFICATION_KINDS:
        return False
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            # The send already completed, so no worker should still hold a lease
            # for this notification. Clearing it lets the very next cycle
            # reconcile the commit instead of waiting out the lease TTL.
            for token, row in list(reservations.items()):
                if (
                    str(row.get("incident_key") or "") == decision.incident_key
                    and str(row.get("kind") or "") == kind
                ):
                    reservations.pop(token, None)
            payload["reservations"] = reservations
            unconfirmed = payload["unconfirmed_deliveries"]
            unconfirmed[f"{decision.incident_id}:{kind}"] = {
                "incident_id": decision.incident_id,
                "incident_key": decision.incident_key,
                "kind": kind,
                "message_id": int(message_id),
                "recorded_at": moment.isoformat(),
                "confirm_attempts": int(confirm_attempts),
            }
            payload["unconfirmed_deliveries"] = unconfirmed
            save_json_atomic(target, payload)
        return True
    except (OSError, TimeoutError, RegistryIOError):
        return False


#: Process-local memory of Telegram messages that were delivered but whose durable
#: reconciliation record could not be written. The incident registry is the
#: durable authority; this only exists so that a storage outage cannot cause a
#: blind duplicate human notification within the life of this process.
#:
#: It is deliberately *not* a second evidence store: entries are promoted into the
#: durable registry as soon as storage recovers and are then forgotten, and they
#: are never read as proof that a notification was committed.
_LOCAL_UNCONFIRMED: dict[str, dict[str, Any]] = {}
_LOCAL_UNCONFIRMED_LOCK = threading.Lock()


def remember_unconfirmed_delivery_locally(
    *,
    incident_id: str,
    incident_key: str,
    kind: str,
    message_id: int,
    now: datetime | None = None,
    confirm_attempts: int = 0,
) -> None:
    """Guard against a duplicate send while durable reconciliation is unavailable."""

    if kind not in NOTIFICATION_KINDS or not incident_id:
        return
    moment = _utc(now)
    with _LOCAL_UNCONFIRMED_LOCK:
        _LOCAL_UNCONFIRMED[f"{incident_id}:{kind}"] = {
            "incident_id": incident_id,
            "incident_key": incident_key,
            "kind": kind,
            "message_id": int(message_id),
            "recorded_at": moment.isoformat(),
            "confirm_attempts": int(confirm_attempts),
            "durable": False,
        }


def locally_remembered_delivery(
    *,
    incident_id: str,
    kind: str,
) -> dict[str, Any] | None:
    """Return a process-local already-delivered record for this kind, if any."""

    if kind not in NOTIFICATION_KINDS or not incident_id:
        return None
    with _LOCAL_UNCONFIRMED_LOCK:
        entry = _LOCAL_UNCONFIRMED.get(f"{incident_id}:{kind}")
    return dict(entry) if isinstance(entry, dict) else None


def forget_local_unconfirmed_delivery(*, incident_id: str, kind: str) -> None:
    """Drop a process-local record once durable state owns it (or it is resolved)."""

    with _LOCAL_UNCONFIRMED_LOCK:
        _LOCAL_UNCONFIRMED.pop(f"{incident_id}:{kind}", None)


def local_unconfirmed_deliveries() -> tuple[dict[str, Any], ...]:
    """Snapshot the process-local guard for reconciliation. Deterministic order."""

    with _LOCAL_UNCONFIRMED_LOCK:
        return tuple(dict(_LOCAL_UNCONFIRMED[key]) for key in sorted(_LOCAL_UNCONFIRMED))


def reset_local_unconfirmed_deliveries_for_tests() -> None:
    """Clear the process-local guard. Test-only; never called by production code."""

    with _LOCAL_UNCONFIRMED_LOCK:
        _LOCAL_UNCONFIRMED.clear()


class UnconfirmedDeliveryDurabilityError(RuntimeError):
    """Telegram accepted a message that could not be recorded durably anywhere.

    Raised only when the durable reconciliation write *and* the durable
    confirmation both fail. The caller must treat this as a system durability
    failure: the message exists, so it must not be sent again, and the owning
    reservation must be retained rather than released.
    """

    def __init__(
        self,
        *,
        incident_id: str,
        incident_key: str,
        kind: str,
        message_id: int | None,
    ) -> None:
        self.incident_id = incident_id
        self.incident_key = incident_key
        self.kind = kind
        self.message_id = message_id
        super().__init__(
            "system-incident delivery durability failure: Telegram accepted "
            f"message_id={message_id} for incident_id={incident_id} kind={kind} "
            "but neither durable confirmation nor durable reconciliation could "
            "be written"
        )


def unconfirmed_delivery(
    *,
    incident_id: str,
    kind: str,
    state_file: Path | None = None,
) -> dict[str, Any] | None:
    """Return reconciliation evidence for an already-sent, uncommitted message.

    Durable evidence is authoritative and is checked first. If it is missing --
    because the durable write failed while other writes succeeded, or because the
    registry is unreadable -- the process-local guard is consulted so an
    already-delivered message is still recognised and never sent twice.
    """

    target = state_file or STATE_FILE
    try:
        payload = _load_payload(target)
    except (OSError, TimeoutError, RegistryIOError):
        return locally_remembered_delivery(incident_id=incident_id, kind=kind)
    entry = payload["unconfirmed_deliveries"].get(f"{incident_id}:{kind}")
    if isinstance(entry, dict):
        return dict(entry)
    return locally_remembered_delivery(incident_id=incident_id, kind=kind)


def pending_notification_decisions(
    *,
    now: datetime | None = None,
    state_file: Path | None = None,
    max_attempts: int = MAX_NOTIFICATION_ATTEMPTS,
) -> list[IncidentDecision]:
    """Return decisions for notifications still owed to the owner.

    Includes rows whose lifecycle state is already ``RECOVERED``, because the
    recovery *message* may still be undelivered. Selection is deterministic and
    bounded; exhausted records stay durably visible as evidence.
    """

    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    decisions: list[IncidentDecision] = []
    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            for key in sorted(payload["incidents"]):
                row = payload["incidents"][key]
                if not isinstance(row, dict):
                    continue
                pending = row.get("pending_notifications")
                if not isinstance(pending, dict) or not pending:
                    continue
                for kind in NOTIFICATION_KINDS:
                    entry = pending.get(kind)
                    if not isinstance(entry, dict):
                        continue
                    if int(entry.get("attempts") or 0) >= max_attempts:
                        continue
                    # An active (non-expired) lease means another worker is
                    # already delivering this kind; skip rather than double-send.
                    if _reservation_for(reservations, key, kind) is not None:
                        continue
                    token = _reserve(reservations, key=key, kind=kind, moment=moment)
                    if token is None:
                        continue
                    decisions.append(
                        _decision_from_row(
                            row,
                            action=_ACTION_FOR_KIND[kind],
                            reason="PENDING_NOTIFICATION_RETRY",
                            reservation_token=token,
                            notification_kind=kind,
                        )
                    )
            if decisions:
                payload["reservations"] = reservations
                save_json_atomic(target, payload)
    except (OSError, TimeoutError, RegistryIOError):
        return []
    return decisions


#: Which human notification a pending kind maps to.
_ACTION_FOR_KIND = {
    KIND_OPEN: ACTION_NOTIFY_OPEN,
    KIND_ESCALATION: ACTION_NOTIFY_ESCALATION,
    KIND_RECOVERY: ACTION_NOTIFY_RECOVERY,
}


# ---------------------------------------------------------------------------
# Read-only projection (future Cockpit / Operations view)
# ---------------------------------------------------------------------------


def _project_row(row: dict[str, Any]) -> dict[str, Any]:
    pending = row.get("pending_notifications")
    pending_kinds = sorted(pending) if isinstance(pending, dict) else []
    return {
        "incident_id": row.get("incident_id"),
        "incident_key": row.get("incident_key"),
        "alert_family": row.get("alert_family") or SYSTEM_HEALTH_FAMILY,
        "incident_class": row.get("incident_class"),
        "scope": row.get("scope"),
        "severity": row.get("severity"),
        "state": row.get("state"),
        "notification_state": row.get("notification_state"),
        "first_seen_at": row.get("first_seen_at"),
        "last_seen_at": row.get("last_seen_at"),
        "occurrence_count": int(row.get("occurrence_count") or 0),
        "consecutive_recovery_failures": int(
            row.get("consecutive_recovery_failures") or 0
        ),
        "recovery_cycles_attempted": int(row.get("recovery_cycles_attempted") or 0),
        "suppressed_notification_count": int(
            row.get("suppressed_notification_count") or 0
        ),
        "notification_delivered": bool(row.get("opened_notification_at")),
        "notification_pending": pending_kinds,
        "recovered_at": row.get("recovered_at"),
        "outage_seconds": _outage_seconds(row),
        "latest_reason": row.get("latest_reason"),
        "metadata": dict(row.get("metadata") or {}),
    }


def read_incidents(
    *,
    state_file: Path | None = None,
    include_recovered: bool = True,
    include_archived: bool = True,
) -> list[dict[str, Any]]:
    """Return a deterministic, secret-free projection of durable incidents.

    Active incidents and every completed (archived) incident instance are
    returned, ordered by ``first_seen_at`` then ``incident_id``, so a later
    outage on a scope never hides the previous lifecycle.

    This is a read-only projection for a future Operations/Incidents view. It
    deliberately does not import or influence any notification authority.
    """

    target = state_file or STATE_FILE
    try:
        payload = _load_payload(target)
    except (OSError, TimeoutError, RegistryIOError):
        return []

    rows: list[dict[str, Any]] = []
    for row in payload["incidents"].values():
        if not isinstance(row, dict):
            continue
        if not include_recovered and str(row.get("state")) == STATE_RECOVERED:
            continue
        rows.append(_project_row(row))
    if include_archived:
        for row in payload["archive"].values():
            if isinstance(row, dict):
                rows.append(_project_row(row))

    rows.sort(key=lambda item: (str(item.get("first_seen_at") or ""), str(item.get("incident_id") or "")))
    return rows


def _outage_seconds(row: dict[str, Any]) -> float | None:
    first = _parse(row.get("first_seen_at"))
    end = _parse(row.get("recovered_at")) or _parse(row.get("last_seen_at"))
    if first is None or end is None:
        return None
    return max(0.0, (end - first).total_seconds())
