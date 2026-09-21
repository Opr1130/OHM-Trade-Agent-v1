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
seen, latest reason, structured metadata, suppressed-notification count).

Incident identity is *semantic*: ``SYSTEM_HEALTH:<scope>``. It is deliberately
not derived from the current hour, raw exception text, a changing sentence, a
changing asset list, a stack trace, a price or a timestamp.

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
SCHEMA_VERSION = 1

#: Owner rule: notify only after connectivity has failed to recover **more than
#: six** consecutive recovery cycles, so the first notification is cycle #7.
#: This is the single source of truth for that threshold.
CONNECTIVITY_RECOVERY_FAILURES_BEFORE_NOTIFY = 7

#: Classes that are deterministic/config-shaped notify on first occurrence
#: instead of burning seven pointless network recovery cycles.
IMMEDIATE_NOTIFY_FAILURES = 1

RESERVATION_LEASE_SECONDS = 300
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
# frozen OPEN/CHANGED/ESCALATED/RECOVERED vocabulary is preserved while the
# recovery-threshold progression stays explicit and observable.
NOTIFICATION_NONE = "NONE"
NOTIFICATION_BELOW_THRESHOLD = "BELOW_THRESHOLD"
NOTIFICATION_OPEN_NOTIFIED = "OPEN_NOTIFIED"
NOTIFICATION_ESCALATED_NOTIFIED = "ESCALATED_NOTIFIED"
NOTIFICATION_RECOVERED_PENDING = "RECOVERED_PENDING"
NOTIFICATION_RECOVERED_NOTIFIED = "RECOVERED_NOTIFIED"

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
    SystemIncidentClass.INTERNAL_SERVICE_FAILURE.value: SystemIncidentScope.INTERNAL.value,
}


@dataclass(frozen=True)
class IncidentDecision:
    """What the caller should do about one observation."""

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


# ---------------------------------------------------------------------------
# Degradation classification
# ---------------------------------------------------------------------------

_PRICING_REASON_MARKERS = (
    "usd/stable-quote pricing unavailable",
    "stable-quote pricing unavailable",
    "no usd/stable-quote pair",
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
_RATE_LIMIT_REASON_MARKERS = ("429", "too many requests", "rate limit", "retry-after", "retry after")
_PRIVATE_STATE_REASON_MARKERS = (
    "account state unavailable",
    "direct snapshot unavailable",
    "private snapshot",
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
_OPERATOR_REASON_MARKERS = ("operator/capacity state unavailable", "operator state unavailable")


def classify_degradation_reason(
    reason: str | None,
) -> tuple[SystemIncidentClass, SystemIncidentScope]:
    """Map an operational degradation reason to its semantic incident.

    The asset list, wording and ordering inside ``reason`` are metadata, never
    identity: ``USD/stable-quote pricing unavailable for held assets: A,B`` and
    ``...: B,A,C`` are the same open pricing incident.
    """

    text = " ".join(str(reason or "").split())
    lowered = text.lower()
    if not lowered:
        return SystemIncidentClass.INTERNAL_SERVICE_FAILURE, SystemIncidentScope.INTERNAL
    if any(marker in lowered for marker in _PRICING_REASON_MARKERS):
        return (
            SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
            SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        )
    if any(marker in lowered for marker in _CREDENTIAL_REASON_MARKERS):
        return (
            SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE,
            SystemIncidentScope.KRAKEN_READ_ONLY_AUTH,
        )
    if any(marker in lowered for marker in _RATE_LIMIT_REASON_MARKERS):
        return (
            SystemIncidentClass.KRAKEN_RATE_LIMITED,
            SystemIncidentScope.KRAKEN_RATE_LIMIT,
        )
    if any(marker in lowered for marker in _PRIVATE_STATE_REASON_MARKERS):
        # Incomplete private account state is only a connectivity incident when
        # the low-level evidence actually proves reachability failed.
        if classify_failure_text(text) is KrakenFailureClass.CONNECTIVITY:
            return (
                SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY,
                SystemIncidentScope.KRAKEN_READ_ONLY,
            )
        return (
            SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE,
            SystemIncidentScope.KRAKEN_POSITION_VERIFICATION,
        )
    if any(marker in lowered for marker in _POSITION_REASON_MARKERS):
        return (
            SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE,
            SystemIncidentScope.KRAKEN_POSITION_VERIFICATION,
        )
    if any(marker in lowered for marker in _OPERATOR_REASON_MARKERS):
        return (
            SystemIncidentClass.INTERNAL_SERVICE_FAILURE,
            SystemIncidentScope.UNIFIED_CYCLE,
        )
    if classify_failure_text(text) is KrakenFailureClass.CONNECTIVITY:
        return (
            SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
            SystemIncidentScope.KRAKEN_PUBLIC,
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


def is_notification_eligible_occurrence(
    *,
    incident_class: SystemIncidentClass | str,
    consecutive_recovery_failures: int,
) -> bool:
    """Whether *this* occurrence is allowed to interrupt the owner."""

    policy = policy_for(incident_class)
    return int(consecutive_recovery_failures) >= policy.notify_after_failures


# ---------------------------------------------------------------------------
# Durable store
# ---------------------------------------------------------------------------


def _load_payload(target: Path) -> dict[str, Any]:
    payload = load_json(target)
    if not isinstance(payload, dict):
        payload = {}
    incidents = payload.get("incidents")
    if not isinstance(incidents, dict):
        payload["incidents"] = {}
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


def _reservation_for(reservations: dict[str, dict], key: str) -> str | None:
    for token, row in reservations.items():
        if str(row.get("incident_key") or "") == key:
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
        "incident_id": f"INC:{key}:{now.strftime('%Y%m%dT%H%M%SZ')}",
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
        "suppressed_notification_count": 0,
        "latest_reason": "",
        "metadata": {},
        "notification_state": NOTIFICATION_NONE,
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
) -> IncidentDecision:
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
    )


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
    """Record one degradation occurrence and decide whether to interrupt the owner.

    Notification-eligible only when the incident has no OPEN notification yet and
    the class policy threshold has been reached, or when severity materially
    escalated. Every other occurrence updates durable evidence and increments the
    suppressed-notification counter.
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
            incidents = payload["incidents"]
            reservations = _active_reservations(payload, now=moment)
            row = incidents.get(key)
            if not isinstance(row, dict) or str(row.get("state")) == STATE_RECOVERED:
                row = _new_row(
                    key=key,
                    incident_class=resolved_class,
                    scope=resolved_scope,
                    policy=policy,
                    now=moment,
                )
                if severity_rank(observed_severity) > severity_rank(str(row.get("severity"))):
                    row["severity"] = observed_severity
            elif str(row.get("incident_class")) != resolved_class.value:
                # Same scope, genuinely different class: close the old incident
                # and open the new one rather than silently rewriting history.
                row = _new_row(
                    key=key,
                    incident_class=resolved_class,
                    scope=resolved_scope,
                    policy=policy,
                    now=moment,
                )

            row["occurrence_count"] = int(row.get("occurrence_count") or 0) + 1
            row["last_seen_at"] = moment.isoformat()
            row["latest_reason"] = _safe_text(reason, MAX_REASON_CHARS)
            if metadata:
                row["metadata"] = _safe_metadata(metadata)
            if policy.requires_recovery_cycles:
                row["consecutive_recovery_failures"] = (
                    int(row.get("consecutive_recovery_failures") or 0) + 1
                )
            failures = int(row.get("consecutive_recovery_failures") or 0)
            opened = bool(row.get("opened_notification_at"))

            # Classes that are not governed by the recovery-cycle rule are
            # measured in occurrences instead. For connectivity, the counter is
            # what implements the owner's "more than six failed cycles" rule.
            effective_failures = (
                failures
                if policy.requires_recovery_cycles
                else int(row.get("occurrence_count") or 0)
            )

            reservation_token: str | None = None
            in_flight = _reservation_for(reservations, key)

            if not opened:
                if effective_failures >= policy.notify_after_failures:
                    if in_flight is not None:
                        row["suppressed_notification_count"] = (
                            int(row.get("suppressed_notification_count") or 0) + 1
                        )
                        action, reason_code = ACTION_SUPPRESS_ONGOING, "NOTIFICATION_IN_FLIGHT"
                    else:
                        reservation_token = uuid4().hex
                        reservations[reservation_token] = {
                            "incident_key": key,
                            "action": ACTION_NOTIFY_OPEN,
                            "reserved_at": moment.isoformat(),
                        }
                        # The incident is OPEN, but it is only *notified* once a
                        # delivered message is confirmed. A failed send must not
                        # leave durable state claiming the owner was told.
                        row["state"] = STATE_OPEN
                        action, reason_code = ACTION_NOTIFY_OPEN, "INCIDENT_OPEN"
                else:
                    row["notification_state"] = NOTIFICATION_BELOW_THRESHOLD
                    row["state"] = STATE_OPEN
                    action, reason_code = ACTION_SILENT, "BELOW_RECOVERY_THRESHOLD"
            else:
                if severity_rank(observed_severity) > severity_rank(str(row.get("severity"))):
                    if in_flight is not None:
                        row["suppressed_notification_count"] = (
                            int(row.get("suppressed_notification_count") or 0) + 1
                        )
                        action, reason_code = ACTION_SUPPRESS_ONGOING, "NOTIFICATION_IN_FLIGHT"
                    else:
                        reservation_token = uuid4().hex
                        reservations[reservation_token] = {
                            "incident_key": key,
                            "action": ACTION_NOTIFY_ESCALATION,
                            "reserved_at": moment.isoformat(),
                        }
                        # A material escalation is a fact about the incident, so
                        # severity and the escalation timestamp advance now; the
                        # *notification* claim waits for delivery confirmation.
                        row["severity"] = observed_severity
                        row["state"] = STATE_ESCALATED
                        row["last_escalation_at"] = moment.isoformat()
                        action, reason_code = ACTION_NOTIFY_ESCALATION, "MATERIAL_ESCALATION"
                else:
                    row["suppressed_notification_count"] = (
                        int(row.get("suppressed_notification_count") or 0) + 1
                    )
                    row["state"] = STATE_CHANGED
                    action, reason_code = ACTION_SUPPRESS_ONGOING, "INCIDENT_ONGOING"

            incidents[key] = row
            payload["incidents"] = incidents
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

    return _decision_from_row(row, action=action, reason=reason_code, reservation_token=reservation_token)


def observe_recovery(
    *,
    incident_class: SystemIncidentClass | str,
    scope: SystemIncidentScope | str,
    evidence: str = "authoritative probe succeeded",
    authoritative: bool = True,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> IncidentDecision:
    """Close an open incident and decide whether to emit one RECOVERED message.

    ``authoritative=False`` models a cache hit or stale local data: it can never
    close an incident, because a TTL cache hit is not proof that connectivity
    recovered.
    """

    klass, scope_value, key = incident_identity(incident_class, scope)
    resolved_class = SystemIncidentClass(klass)
    resolved_scope = SystemIncidentScope(scope_value)
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"

    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            incidents = payload["incidents"]
            reservations = _active_reservations(payload, now=moment)
            row = incidents.get(key)

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

            if not authoritative:
                # A TTL cache hit, stale response or local registry value is not
                # proof that the failed scope recovered.
                return _decision_from_row(
                    row,
                    action=ACTION_SILENT,
                    reason="EVIDENCE_NOT_AUTHORITATIVE",
                )

            opened = bool(row.get("opened_notification_at"))
            recovery_owed = bool(row.get("recovered_notification_at")) is False and opened

            if str(row.get("state")) == STATE_RECOVERED:
                if recovery_owed:
                    reservation_token = _reservation_for(reservations, key)
                    if reservation_token is None:
                        reservation_token = uuid4().hex
                        reservations[reservation_token] = {
                            "incident_key": key,
                            "action": ACTION_NOTIFY_RECOVERY,
                            "reserved_at": moment.isoformat(),
                        }
                        incidents[key] = row
                        payload["incidents"] = incidents
                        payload["reservations"] = reservations
                        save_json_atomic(target, payload)
                        return _decision_from_row(
                            row,
                            action=ACTION_NOTIFY_RECOVERY,
                            reason="RECOVERY_NOTIFICATION_RETRY",
                            reservation_token=reservation_token,
                        )
                    return _decision_from_row(
                        row, action=ACTION_SILENT, reason="RECOVERY_NOTIFICATION_IN_FLIGHT"
                    )
                # The incident was already closed and already reported (or was
                # never worth reporting). Repeated healthy cycles emit nothing.
                return _decision_from_row(
                    row, action=ACTION_SILENT, reason="ALREADY_RECOVERED"
                )

            reservation_token: str | None = None
            in_flight = _reservation_for(reservations, key)

            failures_before_reset = int(row.get("consecutive_recovery_failures") or 0)
            row["state"] = STATE_RECOVERED
            row["recovered_at"] = moment.isoformat()
            row["last_success_at"] = moment.isoformat()
            row["consecutive_recovery_failures"] = 0
            row["recovery_failures_before_reset"] = failures_before_reset
            row["recovery_evidence"] = _safe_text(evidence, MAX_METADATA_CHARS)

            if opened and in_flight is None:
                reservation_token = uuid4().hex
                reservations[reservation_token] = {
                    "incident_key": key,
                    "action": ACTION_NOTIFY_RECOVERY,
                    "reserved_at": moment.isoformat(),
                }
                row["notification_state"] = NOTIFICATION_RECOVERED_PENDING
                action, reason_code = ACTION_NOTIFY_RECOVERY, "INCIDENT_RECOVERED"
            elif opened:
                action, reason_code = ACTION_SILENT, "RECOVERY_NOTIFICATION_IN_FLIGHT"
            else:
                # The owner was never interrupted below the threshold, so the
                # restoration is silent: no outage alert, no recovery alert.
                row["notification_state"] = NOTIFICATION_NONE
                action, reason_code = ACTION_SILENT, "SILENT_HEALTHY_RESTORATION"

            incidents[key] = row
            payload["incidents"] = incidents
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
        synthetic = _new_row(
            key=key,
            incident_class=resolved_class,
            scope=resolved_scope,
            policy=policy_for(resolved_class),
            now=moment,
        )
        return _decision_from_row(
            synthetic,
            action=ACTION_SILENT,
            reason="STATE_UNAVAILABLE_RECOVERY_UNVERIFIED",
        )

    return _decision_from_row(row, action=action, reason=reason_code, reservation_token=reservation_token)


def confirm_incident_notification(
    *,
    decision: IncidentDecision,
    message_id: int | None = None,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> bool:
    """Commit a delivered notification against its reservation.

    Only a *delivered* message may reach this path. A failed delivery must call
    :func:`release_incident_notification` so the incident stays OPEN and keeps
    its unfilled-notification state.
    """

    if not decision.reservation_token:
        return False
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            incidents = payload["incidents"]
            reservations = _active_reservations(payload, now=moment)
            row = incidents.get(decision.incident_key)
            if not isinstance(row, dict):
                return False
            reservations.pop(str(decision.reservation_token), None)
            if decision.action == ACTION_NOTIFY_OPEN:
                row["opened_notification_at"] = moment.isoformat()
                row["notification_state"] = NOTIFICATION_OPEN_NOTIFIED
                if message_id is not None:
                    row["open_message_id"] = int(message_id)
            elif decision.action == ACTION_NOTIFY_ESCALATION:
                row["escalation_notification_at"] = moment.isoformat()
                row["notification_state"] = NOTIFICATION_ESCALATED_NOTIFIED
                if message_id is not None:
                    row["escalation_message_id"] = int(message_id)
            elif decision.action == ACTION_NOTIFY_RECOVERY:
                row["recovered_notification_at"] = moment.isoformat()
                row["notification_state"] = NOTIFICATION_RECOVERED_NOTIFIED
                if message_id is not None:
                    row["recovered_message_id"] = int(message_id)
            else:
                return False
            incidents[decision.incident_key] = row
            payload["incidents"] = incidents
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
    """Release an unused reservation after a failed delivery attempt."""

    if not decision.reservation_token:
        return False
    moment = _utc(now)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    try:
        with registry_lock(lock):
            payload = _load_payload(target)
            reservations = _active_reservations(payload, now=moment)
            reservations.pop(str(decision.reservation_token), None)
            payload["reservations"] = reservations
            save_json_atomic(target, payload)
        return True
    except (OSError, TimeoutError, RegistryIOError):
        return False


# ---------------------------------------------------------------------------
# Read-only projection (future Cockpit / Operations view)
# ---------------------------------------------------------------------------


def read_incidents(
    *,
    state_file: Path | None = None,
    include_recovered: bool = True,
) -> list[dict[str, Any]]:
    """Return a deterministic, secret-free projection of durable incidents.

    This is a read-only projection for a future Operations/Incidents view. It
    deliberately does not import or influence any notification authority.
    """

    target = state_file or STATE_FILE
    try:
        payload = _load_payload(target)
    except (OSError, TimeoutError, RegistryIOError):
        return []
    rows: list[dict[str, Any]] = []
    for key in sorted(payload.get("incidents") or {}):
        row = payload["incidents"][key]
        if not isinstance(row, dict):
            continue
        if not include_recovered and str(row.get("state")) == STATE_RECOVERED:
            continue
        rows.append(
            {
                "incident_id": row.get("incident_id"),
                "incident_key": row.get("incident_key") or key,
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
                "suppressed_notification_count": int(
                    row.get("suppressed_notification_count") or 0
                ),
                "notification_delivered": bool(row.get("opened_notification_at")),
                "recovered_at": row.get("recovered_at"),
                "outage_seconds": _outage_seconds(row),
                "latest_reason": row.get("latest_reason"),
                "metadata": dict(row.get("metadata") or {}),
            }
        )
    return rows


def _outage_seconds(row: dict[str, Any]) -> float | None:
    first = _parse(row.get("first_seen_at"))
    end = _parse(row.get("recovered_at")) or _parse(row.get("last_seen_at"))
    if first is None or end is None:
        return None
    return max(0.0, (end - first).total_seconds())
