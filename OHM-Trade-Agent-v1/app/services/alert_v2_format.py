"""Alert-v2 decision-first rendering for system/operations incidents.

The owner reads Telegram for *attention*, not for history. Alert v2 therefore
renders system incidents in a fixed, decision-first order:

``<ICON> <DECISION / STATE> — <SCOPE>``
``Action: ...``
``<essential evidence>``
``Impact: ...`` (only when it changes what the owner should do)
``Why: ...``
``<safety / mode statement>``

Everything detailed and historical stays in durable telemetry and the future
Cockpit Operations view (:func:`app.services.system_incidents.read_incidents`),
never in the alert body.

Hard rules honoured here:

* a system incident is never titled as a signal, movement, opportunity or trade
  alert;
* no user-facing legacy "OHM" branding appears in an Alert-v2 automatic title;
* no invented precision, probability, movement range, health claim or P&L;
* the action text must match the actual failure: a pricing-coverage gap must not
  tell the owner to "verify connectivity".
"""

from __future__ import annotations

from typing import Any

from app.services.system_incidents import (
    ACTION_NOTIFY_ESCALATION,
    ACTION_NOTIFY_OPEN,
    ACTION_NOTIFY_RECOVERY,
    IncidentDecision,
    IncidentSeverity,
    SystemIncidentClass,
)

#: Deterministic title + action per incident class. Titles never carry legacy
#: user-facing branding.
_TITLE_BY_CLASS: dict[str, tuple[str, str]] = {
    SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value: (
        "🛑 SYSTEM FAILURE — KRAKEN CONNECTIVITY",
        "CHECK KRAKEN / NETWORK",
    ),
    SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value: (
        "🛑 SYSTEM FAILURE — KRAKEN READ-ONLY CONNECTIVITY",
        "CHECK KRAKEN READ-ONLY CONNECTIVITY",
    ),
    SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value: (
        "🛑 SYSTEM FAILURE — KRAKEN READ-ONLY CREDENTIALS",
        "CHECK KRAKEN READ-ONLY API CREDENTIALS",
    ),
    SystemIncidentClass.KRAKEN_RATE_LIMITED.value: (
        "⚠️ SYSTEM DEGRADED — KRAKEN RATE LIMIT",
        "NO ACTION — O'Pip is backing off and retrying",
    ),
    SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value: (
        "⚠️ SYSTEM DEGRADED — HELD-ASSET PRICING",
        "CHECK MARKET-PRICE COVERAGE",
    ),
    SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE.value: (
        "⚠️ SYSTEM DEGRADED — POSITION VERIFICATION",
        "VERIFY KRAKEN READ-ONLY CONNECTIVITY / POSITION STATE",
    ),
    SystemIncidentClass.PROVIDER_RATE_LIMITED.value: (
        "⚠️ SYSTEM DEGRADED — PROVIDER RATE LIMIT",
        "NO ACTION — O'Pip is backing off and retrying",
    ),
    SystemIncidentClass.DATA_PIPELINE_STALE.value: (
        "⚠️ SYSTEM DEGRADED — DATA PIPELINE",
        "CHECK DATA PIPELINE FRESHNESS",
    ),
    SystemIncidentClass.INTERNAL_SERVICE_FAILURE.value: (
        "🛑 SYSTEM FAILURE — INTERNAL SERVICE",
        "CHECK SERVICE HEALTH",
    ),
}

_DEFAULT_TITLE_ACTION = ("⚠️ SYSTEM DEGRADED — OPERATIONS", "REVIEW OPERATIONS STATE")

_IMPACT_BY_CLASS: dict[str, str] = {
    SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value: (
        "read-only position/protection verification may be incomplete"
    ),
    SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value: (
        "read-only position verification may be incomplete"
    ),
    SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value: (
        "read-only position verification is unavailable until credentials are fixed"
    ),
    SystemIncidentClass.KRAKEN_RATE_LIMITED.value: (
        "market-data requests are being throttled; evidence may arrive late"
    ),
    SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value: (
        "valuation/protection evidence may be incomplete for affected holdings"
    ),
    SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE.value: (
        "protection decisions for affected holdings may use incomplete position state"
    ),
    SystemIncidentClass.PROVIDER_RATE_LIMITED.value: (
        "provider evidence may arrive late"
    ),
    SystemIncidentClass.DATA_PIPELINE_STALE.value: (
        "downstream evidence may be stale"
    ),
    SystemIncidentClass.INTERNAL_SERVICE_FAILURE.value: (
        "automated workflows may be partially degraded"
    ),
}

#: Canonical, deterministic "Why" text. Never invented per-occurrence.
_CANONICAL_WHY: dict[str, str] = {
    SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value: (
        "public market-data connectivity is unreachable"
    ),
    SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value: (
        "read-only account connectivity is unreachable"
    ),
    SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value: (
        "Kraken read-only credentials are missing, invalid or insufficient"
    ),
    SystemIncidentClass.KRAKEN_RATE_LIMITED.value: "Kraken is rate limiting requests",
    SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value: (
        "held assets have no usable USD/stable-quote pricing path"
    ),
    SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE.value: (
        "position/lifecycle verification is incomplete"
    ),
    SystemIncidentClass.PROVIDER_RATE_LIMITED.value: "an external provider is rate limiting",
    SystemIncidentClass.DATA_PIPELINE_STALE.value: "an evidence pipeline is stale",
    SystemIncidentClass.INTERNAL_SERVICE_FAILURE.value: (
        "an internal service or local state read failed"
    ),
}

#: The pricing phrase prefix used to extract the affected asset set from a
#: reason. The asset list is incident *metadata*, never incident identity.
_PRICING_PREFIXES = (
    "usd/stable-quote pricing unavailable for held assets:",
    "stable-quote pricing unavailable for held assets:",
)

NO_ORDER_LINE = "No order was placed or changed."
PAPER_LINE = "Paper/read-only mode. No funded trading authority is engaged."


def title_and_action(incident_class: str) -> tuple[str, str]:
    return _TITLE_BY_CLASS.get(str(incident_class), _DEFAULT_TITLE_ACTION)


def canonical_why(incident_class: str) -> str:
    return _CANONICAL_WHY.get(
        str(incident_class), "an operational condition needs review"
    )


def format_duration(seconds: float | int | None) -> str:
    """Render a compact, truthful duration. Never guesses a missing value."""

    if seconds is None:
        return "unknown"
    total = int(max(0.0, float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def unpriced_assets(decision: IncidentDecision) -> list[str]:
    """Extract the affected asset set from incident metadata or reason."""

    metadata = decision.metadata or {}
    raw = metadata.get("unpriced_assets")
    if isinstance(raw, (list, tuple)):
        assets = [str(item).strip() for item in raw if str(item or "").strip()]
        if assets:
            return assets
    reason = str(decision.latest_reason or "")
    lowered = reason.lower()
    for prefix in _PRICING_PREFIXES:
        index = lowered.find(prefix)
        if index >= 0:
            tail = reason[index + len(prefix) :]
            assets = [item.strip() for item in tail.split(",") if item.strip()]
            if assets:
                return assets
    return []


def _context_lines(decision: IncidentDecision) -> list[str]:
    klass = str(decision.incident_class)
    lines: list[str] = []

    if klass == SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value:
        lines.append("State: UNAVAILABLE")
        lines.append(
            "Recovery: automatic recovery failed "
            f"{max(0, decision.consecutive_recovery_failures)} consecutive attempts"
        )
    elif klass == SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value:
        lines.append("State: UNAVAILABLE")
        lines.append(
            "Recovery: automatic recovery failed "
            f"{max(0, decision.consecutive_recovery_failures)} consecutive attempts"
        )
    elif klass == SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value:
        lines.append("State: UNAVAILABLE")
        lines.append("Recovery: automatic retry does not apply to a credential failure")
    elif klass in {
        SystemIncidentClass.KRAKEN_RATE_LIMITED.value,
        SystemIncidentClass.PROVIDER_RATE_LIMITED.value,
    }:
        lines.append("State: RATE LIMITED — backing off, not a connectivity outage")
    elif klass == SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value:
        assets = unpriced_assets(decision)
        if assets:
            lines.append(f"Unpriced: {', '.join(assets)}")
        lines.append("State: monitoring continues where evidence is available")
    else:
        lines.append("State: DEGRADED")

    impact = _IMPACT_BY_CLASS.get(klass)
    if impact:
        lines.append(f"Impact: {impact}")
    return lines


def _why_line(decision: IncidentDecision) -> str:
    klass = str(decision.incident_class)
    reason = " ".join(str(decision.latest_reason or "").split())
    if klass == SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value and unpriced_assets(decision):
        # The affected set is already shown; keep the rationale canonical and
        # avoid repeating the same asset names twice in one alert.
        return canonical_why(klass)
    if reason:
        return reason[:160]
    return canonical_why(klass)


def format_system_incident_open(decision: IncidentDecision) -> str:
    """Decision-first SYSTEM FAILURE / SYSTEM DEGRADED alert."""

    title, action = title_and_action(decision.incident_class)
    lines = [title, f"Action: {action}"]
    lines.extend(_context_lines(decision))
    lines.append(f"Why: {_why_line(decision)}")

    if decision.occurrence_count > 1:
        lines.append(
            "Occurrences observed: "
            f"{decision.occurrence_count} "
            f"(human notifications suppressed: {decision.suppressed_notification_count})"
        )

    if str(decision.incident_class) in {
        SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value,
        SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value,
    }:
        lines.append("O'Pip continues attempting recovery automatically.")
    lines.append(NO_ORDER_LINE)
    return "\n".join(lines)


def format_system_incident_escalation(decision: IncidentDecision) -> str:
    """Exactly one escalation per material severity increase."""

    title, action = title_and_action(decision.incident_class)
    lines = [f"{title} (ESCALATED)", f"Action: {action}"]
    lines.append(f"Severity: {str(decision.severity).upper()}")
    if decision.metadata.get("critical_reason"):
        lines.append(f"Reason: {decision.metadata['critical_reason']}")
    lines.extend(_context_lines(decision))
    lines.append(f"Why: {_why_line(decision)}")
    if str(decision.severity).upper() == IncidentSeverity.CRITICAL.value:
        lines.append("Impact: protection verification cannot be assumed for held positions")
    lines.append(NO_ORDER_LINE)
    return "\n".join(lines)


def format_system_incident_recovery(
    decision: IncidentDecision,
    *,
    outage_seconds: float | None = None,
) -> str:
    """Decision-first SYSTEM RECOVERED alert. Emitted at most once per incident."""

    klass = str(decision.incident_class)
    scope_label = {
        SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE.value: "KRAKEN CONNECTIVITY",
        SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY.value: (
            "KRAKEN READ-ONLY CONNECTIVITY"
        ),
        SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value: (
            "KRAKEN READ-ONLY CREDENTIALS"
        ),
        SystemIncidentClass.KRAKEN_RATE_LIMITED.value: "KRAKEN RATE LIMIT",
        SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED.value: "HELD-ASSET PRICING",
        SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE.value: (
            "POSITION VERIFICATION"
        ),
        SystemIncidentClass.PROVIDER_RATE_LIMITED.value: "PROVIDER RATE LIMIT",
        SystemIncidentClass.DATA_PIPELINE_STALE.value: "DATA PIPELINE",
        SystemIncidentClass.INTERNAL_SERVICE_FAILURE.value: "INTERNAL SERVICE",
    }.get(klass, "OPERATIONS")

    lines = [
        f"✅ SYSTEM RECOVERED — {scope_label}",
        "Action: NO ACTION",
        "State: HEALTHY",
    ]
    if decision.recovered_after_recovery_failures:
        lines.append(
            f"Failed recovery cycles: {decision.recovered_after_recovery_failures}"
        )
    else:
        lines.append("Recovery: automatic recovery succeeded")
    lines.append(f"Outage duration: {format_duration(outage_seconds)}")
    lines.append(f"Occurrences while degraded: {decision.occurrence_count}")
    lines.append("Read-only monitoring restored. " + NO_ORDER_LINE)
    return "\n".join(lines)


def format_system_incident_message(
    decision: IncidentDecision,
    *,
    outage_seconds: float | None = None,
) -> str:
    """Render whichever Alert-v2 system message the decision calls for."""

    if decision.action == ACTION_NOTIFY_RECOVERY:
        return format_system_incident_recovery(decision, outage_seconds=outage_seconds)
    if decision.action == ACTION_NOTIFY_ESCALATION:
        return format_system_incident_escalation(decision)
    if decision.action == ACTION_NOTIFY_OPEN:
        return format_system_incident_open(decision)
    raise ValueError(f"decision is not notification-eligible: {decision.action}")


def outage_seconds_between(first_seen_at: Any, recovered_at: Any) -> float | None:
    """Compute outage duration from ISO timestamps, or ``None`` if unknown."""

    from datetime import datetime, timezone

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

    first = _parse(first_seen_at)
    last = _parse(recovered_at)
    if first is None or last is None:
        return None
    return max(0.0, (last - first).total_seconds())
