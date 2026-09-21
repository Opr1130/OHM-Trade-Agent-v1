from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.services.active_trade_registry import get_active_trades
from app.services.kraken_exposure_resolver import KrakenExposureResolver, ResolvedExposure
from app.services.kraken_position_verification import KrakenPositionVerifier
from app.services.position_materiality import refine_protection_action
from app.services.asset_display_identity import display_market_label
from app.services.alert_taxonomy import SYSTEM_HEALTH_FAMILY
from app.services.alert_v2_format import (
    format_system_incident_message,
    outage_seconds_between,
)
from app.services.emergency_alert_notifier import send_emergency_alert
from app.services.emergency_move_detector import detect_emergency_move
from app.services.kraken_health import (
    KrakenFailureClass,
    KrakenHealthScope,
    KrakenScopeProbe,
    public_connectivity_probe,
    rate_limit_cleared_probe,
    read_only_connectivity_probe,
    transport_connection_reset,
)
from app.services.notification_policy import record_emitted, should_emit
from app.services.system_incidents import (
    KIND_ESCALATION,
    KIND_OPEN,
    KIND_RECOVERY,
    ACTION_NOTIFY_ESCALATION,
    ACTION_NOTIFY_OPEN,
    ACTION_NOTIFY_RECOVERY,
    IncidentDecision,
    RecoveryAuthority,
    SystemIncidentScope,
    classify_degradation_reason,
    confirm_incident_notification,
    forget_local_unconfirmed_delivery,
    is_monitor_owned_scope,
    local_unconfirmed_deliveries,
    observe_degradation,
    observe_recovery,
    pending_notification_decisions,
    read_incidents,
    record_failed_recovery_cycle,
    record_unconfirmed_delivery,
    release_incident_notification,
    remember_unconfirmed_delivery_locally,
    requires_owner_recovery_cycles,
    unconfirmed_delivery,
)
from app.services.telegram_delivery import (
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)
from app.services.trade_monitor import monitor_trade
from app.services.trade_monitor_notifier import send_monitor_update
from app.services.trade_outcome_registry import update_active_observation


@dataclass
class MonitorRunSummary:
    active_trades: int
    checked: int
    monitor_notifications_sent: int
    emergency_notifications_sent: int
    positions_verified: int
    positions_absent: int
    positions_unavailable: int
    positions_unmanaged: int
    failures: list[str]


def _telegram_configured(settings) -> bool:
    return bool(
        str(getattr(settings, "telegram_bot_token", "") or "").strip()
        and str(getattr(settings, "telegram_chat_id", "") or "").strip()
    )


#: Bounded retry for committing an already-delivered message to durable incident
#: state. Kept small so a wedged registry cannot stall the monitor cadence.
_CONFIRM_RETRY_ATTEMPTS = 2
_CONFIRM_RETRY_DELAY_SECONDS = 0.2


def _confirm_sleep(seconds: float) -> None:
    """Indirection so tests can assert retry behaviour without real waiting."""

    import time

    time.sleep(seconds)


def _deliver_system_incident_decision(
    *, settings, decision, failures: list[str] | None = None
) -> bool:
    """Deliver one Alert-v2 system-incident decision, or record why it was not sent.

    Delivery and incident state stay distinct, and a message that already reached
    Telegram is never sent twice. Four outcomes are possible:

    A. Telegram rejects the message -> the notification stays pending/retryable
       and nothing is claimed.
    B. Telegram accepts it and the durable commit succeeds -> committed once.
    C. Telegram accepts it, the commit fails, but reconciliation evidence is
       written durably -> delivered-but-pending; a later cycle commits the
       original ``message_id`` instead of resending.
    D. Telegram accepts it and *no* durable write succeeds anywhere -> a system
       durability failure. The fact that Telegram has the message is never
       discarded: the process-local guard and the retained reservation both block
       a duplicate send, an explicit error is surfaced, and reconciliation is
       retried on later cycles.

    Outcome D deliberately fails closed against duplicate human notification
    rather than pretending the delivery was safely committed.
    """

    now = datetime.now(timezone.utc)
    policy_identity = f"{SYSTEM_HEALTH_FAMILY}:{decision.scope}"

    # An earlier cycle may already have delivered this exact notification. Never
    # resend; only reconcile the durable commit.
    already_sent = unconfirmed_delivery(
        incident_id=decision.incident_id,
        kind=decision.notification_kind,
    )
    if already_sent is not None:
        message_id = already_sent.get("message_id")
        if message_id is not None and confirm_incident_notification(
            decision=decision, message_id=int(message_id), now=now
        ):
            forget_local_unconfirmed_delivery(
                incident_id=decision.incident_id,
                kind=decision.notification_kind,
            )
            return True
        print(
            "O'Pip system-incident delivery already sent; durable confirmation "
            "still pending:",
            f"incident_id={decision.incident_id}",
            f"kind={decision.notification_kind}",
            f"message_id={message_id}",
        )
        return True

    if not decision.should_notify:
        record_telegram_suppression(
            identity=policy_identity,
            alert_family=SYSTEM_HEALTH_FAMILY,
            event_type=decision.incident_class,
            fingerprint=decision.incident_key,
            reason=decision.reason,
            generated_at=now,
        )
        return False

    message = format_system_incident_message(
        decision,
        outage_seconds=outage_seconds_between(
            decision.first_seen_at, decision.recovered_at
        ),
    )

    if not _telegram_configured(settings):
        record_telegram_not_eligible(
            identity=policy_identity,
            alert_family=SYSTEM_HEALTH_FAMILY,
            event_type=decision.incident_class,
            fingerprint=decision.incident_key,
            reason="TELEGRAM_NOT_CONFIGURED",
            generated_at=now,
        )
        release_incident_notification(decision=decision, now=now)
        return False

    delivery = send_tracked_telegram(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        message=message,
        identity=policy_identity,
        alert_family=SYSTEM_HEALTH_FAMILY,
        event_type=decision.incident_class,
        fingerprint=decision.incident_key,
        generated_at=now,
    )
    if not delivery.delivered:
        # Outcome A: a failed send leaves the notification pending/retryable.
        release_incident_notification(decision=decision, now=now)
        return False

    # Telegram accepted the message. The durable commit must not be assumed:
    # retry it boundedly, and if it still fails, record reconciliation evidence
    # so a later cycle commits this message instead of sending another copy.
    if delivery.message_id is not None and confirm_incident_notification(
        decision=decision, message_id=delivery.message_id, now=now
    ):
        # Outcome B.
        return True

    for _ in range(_CONFIRM_RETRY_ATTEMPTS):
        _confirm_sleep(_CONFIRM_RETRY_DELAY_SECONDS)
        if delivery.message_id is not None and confirm_incident_notification(
            decision=decision, message_id=delivery.message_id, now=now
        ):
            # Outcome B (after bounded retry).
            return True

    if delivery.message_id is None:
        # Telegram accepted the send but returned no usable identifier. We cannot
        # prove ownership of a message, so we must not claim a committed
        # notification: keep the notification retryable and surface the anomaly.
        release_incident_notification(decision=decision, now=now)
        _record_durability_failure(
            failures=failures,
            detail=(
                "Telegram accepted a system notification without a message_id; "
                f"incident_id={decision.incident_id} kind={decision.notification_kind}"
            ),
        )
        return False

    recorded = record_unconfirmed_delivery(
        decision=decision,
        message_id=int(delivery.message_id),
        now=now,
        confirm_attempts=_CONFIRM_RETRY_ATTEMPTS + 1,
    )
    if recorded:
        # Outcome C: delivered-but-pending. Never resend; reconcile later.
        print(
            "O'Pip system-incident durable confirmation failed after delivery; "
            "recorded for reconciliation:",
            f"incident_id={decision.incident_id}",
            f"kind={decision.notification_kind}",
            f"message_id={delivery.message_id}",
        )
        return True

    # Outcome D: system durability failure. Telegram already has this message, so
    # fail closed against a duplicate send: keep the reservation (do not release
    # it), remember the delivery in-process, and surface the failure explicitly.
    remember_unconfirmed_delivery_locally(
        incident_id=decision.incident_id,
        incident_key=decision.incident_key,
        kind=decision.notification_kind,
        message_id=int(delivery.message_id),
        now=now,
        confirm_attempts=_CONFIRM_RETRY_ATTEMPTS + 1,
    )
    _record_durability_failure(
        failures=failures,
        detail=(
            "system-incident delivery durability failure: Telegram accepted "
            f"message_id={delivery.message_id} but neither durable confirmation nor "
            "durable reconciliation could be written; reservation retained and "
            "duplicate send blocked "
            f"(incident_id={decision.incident_id} kind={decision.notification_kind})"
        ),
    )
    print(
        "O'Pip CRITICAL system-incident delivery durability failure; retaining "
        "reservation and blocking duplicate send:",
        f"incident_id={decision.incident_id}",
        f"kind={decision.notification_kind}",
        f"message_id={delivery.message_id}",
    )
    return True


def _record_durability_failure(*, failures: list[str] | None, detail: str) -> None:
    """Surface an explicit durability failure in telemetry and the run summary."""

    if failures is not None:
        failures.append(detail)
    print("O'Pip system-incident durability failure:", detail)


def _notify_monitor_degraded(*, settings, reason: str, identity: str = "ACTIVE_TRADE_MONITOR") -> bool:
    """Record one degradation occurrence and deliver any notification it earned.

    The occurrence itself is evidence. For connectivity classes the owner
    notification is *not* produced here -- it is produced by the recovery cycle
    in :func:`_attempt_connectivity_recovery`, so the "more than six failed
    recovery cycles" rule counts real probe failures.
    """

    incident_class, scope = classify_degradation_reason(reason)
    decision = observe_degradation(
        incident_class=incident_class,
        scope=scope,
        reason=reason,
    )
    # Always route through delivery: a non-notifying occurrence still records a
    # durable suppression row, so every occurrence stays observable/auditable.
    return _deliver_system_incident_decision(settings=settings, decision=decision)


def _notify_monitor_degraded_safe(
    *,
    settings,
    reason: str,
    failures: list[str],
    identity: str = "ACTIVE_TRADE_MONITOR",
) -> bool:
    try:
        return _notify_monitor_degraded(
            settings=settings,
            reason=reason,
            identity=identity,
        )
    except Exception as exc:
        failures.append(
            f"{identity}: degraded-monitor notification failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def _attempt_connectivity_recovery(
    *,
    scope: str,
    incident_class: str,
    settings,
    failures: list[str],
) -> None:
    """Run exactly ONE bounded recovery cycle for one connectivity scope.

    Recovery is proven on the same semantic scope that failed: a public probe
    cannot close a read-only incident and vice versa. A failed probe advances the
    consecutive-failure counter exactly once; a successful probe closes only this
    scope. Automatic recovery keeps running after the incident has been reported.
    """

    if scope == SystemIncidentScope.KRAKEN_PUBLIC.value:
        health_scope = KrakenHealthScope.PUBLIC_CONNECTIVITY
        probe_callable = public_connectivity_probe()
        reset = transport_connection_reset()
        authority = RecoveryAuthority.PUBLIC_PROBE
    elif scope == SystemIncidentScope.KRAKEN_READ_ONLY.value:
        health_scope = KrakenHealthScope.READ_ONLY_CONNECTIVITY
        probe_callable = read_only_connectivity_probe()
        reset = None
        authority = RecoveryAuthority.READ_ONLY_PROBE
    else:
        return

    try:
        probe = KrakenScopeProbe(connection_reset=reset)
        result = probe.run(probe_callable, scope=health_scope)
    except Exception as exc:
        failures.append(
            f"{scope}: recovery probe failed: {type(exc).__name__}: {exc}"
        )
        return

    if not result.success:
        # One failed higher-level cycle == exactly one increment. The incident
        # stays open and recovery continues on the next cycle.
        decision = record_failed_recovery_cycle(
            incident_class=incident_class,
            scope=scope,
            reason=result.reason,
            metadata={
                "recovery_attempts": result.attempts,
                "failure_class": result.failure_class.value,
                "connection_reset": result.connection_reset,
                "budget_exhausted": result.budget_exhausted,
            },
        )
        if decision.should_notify:
            try:
                _deliver_system_incident_decision(
                    settings=settings, decision=decision, failures=failures
                )
            except Exception as exc:
                failures.append(
                    f"{scope}: failure notification failed: {type(exc).__name__}: {exc}"
                )
        else:
            # Silent cycle, but still recorded so the failure stays auditable.
            _deliver_system_incident_decision(
                settings=settings, decision=decision, failures=failures
            )
        return

    decision = observe_recovery(
        incident_class=incident_class,
        scope=scope,
        evidence_source=authority,
        evidence=f"authoritative {health_scope.value} probe succeeded",
        authoritative=True,
    )
    if decision.should_notify:
        try:
            _deliver_system_incident_decision(
                settings=settings, decision=decision, failures=failures
            )
        except Exception as exc:
            failures.append(
                f"{scope}: recovery notification failed: {type(exc).__name__}: {exc}"
            )


#: Scope-matched recovery evidence for coverage-shaped scopes. Held-position
#: coverage is only valid evidence for the scopes it actually proves healthy.
#:
#: ``KRAKEN:RATE_LIMIT`` is deliberately absent: complete held-position coverage
#: says nothing about whether Kraken is still throttling, so it must never close
#: a rate-limit incident. Rate limiting is recovered only by
#: :func:`_attempt_rate_limit_recovery`, on fresh provider evidence.
_COVERAGE_EVIDENCE_BY_SCOPE: dict[str, RecoveryAuthority] = {
    SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value: RecoveryAuthority.PRICING_COVERAGE,
    SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value: RecoveryAuthority.POSITION_COVERAGE,
}


def _attempt_rate_limit_recovery(
    *,
    scope: str,
    incident_class: str,
    settings,
    failures: list[str],
) -> None:
    """Prove throttling cleared using fresh provider evidence for that scope.

    Rate limiting is **not** a connectivity outage, so it is not governed by the
    seven-cycle rule and it is never closed by held-position coverage. One
    bounded cycle performs a fresh, cache-bypassing request on the throttled
    scope:

    * success -> authoritative proof throttling cleared, close the incident;
    * failure classified ``RATE_LIMITED`` -> still throttled, stay open;
    * failure classified as anything else (for example connectivity) -> **not**
      rate-limit evidence, so the incident stays open and no failure counter is
      advanced either way.

    Retry-After/backoff handling stays where it belongs: inside the transport
    probe's bounded retry, which this cycle reuses.
    """

    try:
        probe = KrakenScopeProbe()
        result = probe.run(
            rate_limit_cleared_probe(),
            scope=KrakenHealthScope.RATE_LIMIT,
        )
    except Exception as exc:
        failures.append(
            f"{scope}: rate-limit recovery probe failed: {type(exc).__name__}: {exc}"
        )
        return

    if not result.success:
        if result.failure_class is not KrakenFailureClass.RATE_LIMITED:
            # A non-rate-limit failure is not evidence that throttling cleared.
            failures.append(
                f"{scope}: rate-limit recovery probe did not provide rate-limit "
                f"evidence ({result.failure_class.value}): {result.reason}"
            )
        return

    decision = observe_recovery(
        incident_class=incident_class,
        scope=scope,
        evidence_source=RecoveryAuthority.RATE_LIMIT_CLEARED,
        evidence=(
            "fresh cache-bypassing provider request succeeded without rate limiting"
        ),
        authoritative=True,
    )
    if decision.should_notify:
        try:
            _deliver_system_incident_decision(
                settings=settings, decision=decision, failures=failures
            )
        except Exception as exc:
            failures.append(
                f"{scope}: rate-limit recovery notification failed: "
                f"{type(exc).__name__}: {exc}"
            )


def _reconcile_system_incident_recovery(
    *,
    settings,
    coverage_complete: bool,
    degraded_scopes: set[str],
    failures: list[str],
) -> None:
    """Advance incident recovery for the scopes the active monitor owns.

    Connectivity scopes always get their bounded probe, *including* while the
    outage is still active -- skipping the probe during an outage is precisely
    what would let an observation-driven counter claim failures that never
    happened. The same-cycle guard only prevents unrelated evidence from closing
    a scope; it never prevents the probe itself.

    Producer-owned scopes (operator state, internal service, credential
    configuration) are deliberately left alone: this monitor cannot prove them
    healthy, so their owning component closes them.
    """

    try:
        open_incidents = read_incidents(include_recovered=False)
    except Exception as exc:
        failures.append(f"system-incident read failed: {type(exc).__name__}: {exc}")
        return

    for row in open_incidents:
        scope = str(row.get("scope") or "")
        incident_class = str(row.get("incident_class") or "")
        if not scope or not is_monitor_owned_scope(scope):
            continue

        if requires_owner_recovery_cycles(incident_class):
            # Always probe the failed scope, even while it is still degraded.
            _attempt_connectivity_recovery(
                scope=scope,
                incident_class=incident_class,
                settings=settings,
                failures=failures,
            )
            continue

        if scope == SystemIncidentScope.KRAKEN_RATE_LIMIT.value:
            # Throttling needs its own fresh provider evidence; coverage proves
            # nothing about whether Kraken is still limiting us.
            _attempt_rate_limit_recovery(
                scope=scope,
                incident_class=incident_class,
                settings=settings,
                failures=failures,
            )
            continue

        authority = _COVERAGE_EVIDENCE_BY_SCOPE.get(scope)
        if authority is None or not coverage_complete:
            continue
        if scope in degraded_scopes:
            # The scope failed again in this very cycle, so this cycle's coverage
            # is not evidence that it recovered.
            continue
        decision = observe_recovery(
            incident_class=incident_class,
            scope=scope,
            evidence_source=authority,
            evidence="scope-matched coverage evidence complete for all verified holdings",
            authoritative=True,
        )
        if decision.should_notify:
            try:
                _deliver_system_incident_decision(
                    settings=settings, decision=decision, failures=failures
                )
            except Exception as exc:
                failures.append(
                    f"{scope}: recovery notification failed: {type(exc).__name__}: {exc}"
                )


def _reconcile_local_unconfirmed_deliveries(*, failures: list[str]) -> None:
    """Promote process-local delivery guards into durable reconciliation state.

    When the incident registry was unavailable, an already-delivered message was
    remembered in-process so it could not be resent. As soon as storage recovers
    that record must become durable; only then is the local guard dropped.
    """

    for entry in local_unconfirmed_deliveries():
        decision = _rebuild_decision_for_local_entry(entry)
        if decision is None:
            forget_local_unconfirmed_delivery(
                incident_id=entry["incident_id"], kind=entry["kind"]
            )
            continue
        promoted = record_unconfirmed_delivery(
            decision=decision,
            message_id=int(entry["message_id"]),
            confirm_attempts=int(entry.get("confirm_attempts") or 0),
        )
        if promoted:
            forget_local_unconfirmed_delivery(
                incident_id=entry["incident_id"], kind=entry["kind"]
            )
            print(
                "O'Pip system-incident local delivery guard promoted to durable "
                "reconciliation:",
                f"incident_id={entry['incident_id']}",
                f"kind={entry['kind']}",
                f"message_id={entry['message_id']}",
            )
        else:
            failures.append(
                "system-incident durability failure persists: delivered "
                f"message_id={entry['message_id']} for incident_id="
                f"{entry['incident_id']} kind={entry['kind']} still unreconciled"
            )


#: Which human notification each kind maps to, for rebuilding a decision during
#: process-local reconciliation.
_ACTION_FOR_NOTIFICATION_KIND: dict[str, str] = {
    KIND_OPEN: ACTION_NOTIFY_OPEN,
    KIND_ESCALATION: ACTION_NOTIFY_ESCALATION,
    KIND_RECOVERY: ACTION_NOTIFY_RECOVERY,
}


def _rebuild_decision_for_local_entry(entry: dict) -> Any | None:
    """Reconstruct a minimal decision so a local guard can be promoted durably."""

    try:
        return IncidentDecision(
            action=_ACTION_FOR_NOTIFICATION_KIND.get(str(entry.get("kind")), ""),
            reason="LOCAL_UNCONFIRMED_PROMOTION",
            incident_key=str(entry.get("incident_key") or ""),
            incident_id=str(entry.get("incident_id") or ""),
            state="",
            notification_state="",
            incident_class="",
            scope=str(entry.get("incident_key") or "").split(":", 1)[-1],
            severity="",
            occurrence_count=0,
            suppressed_notification_count=0,
            consecutive_recovery_failures=0,
            first_seen_at=None,
            last_seen_at=None,
            recovered_at=None,
            latest_reason="",
            notification_kind=str(entry.get("kind") or ""),
        )
    except Exception:
        return None


def _retry_pending_system_notifications(*, settings, failures: list[str]) -> None:
    """Deliver notifications the owner is still owed, including after recovery.

    A recovery is recorded as a fact the moment it is proven, but its *message*
    may still be undelivered. This selects those pending obligations -- including
    rows whose lifecycle state is already ``RECOVERED`` -- so a transient Telegram
    failure cannot permanently lose an escalation or recovery alert.
    """

    try:
        decisions = pending_notification_decisions()
    except Exception as exc:
        failures.append(
            f"pending system-notification read failed: {type(exc).__name__}: {exc}"
        )
        return
    for decision in decisions:
        try:
            _deliver_system_incident_decision(
                settings=settings, decision=decision, failures=failures
            )
        except Exception as exc:
            failures.append(
                f"{decision.scope}: pending {decision.notification_kind} delivery failed: "
                f"{type(exc).__name__}: {exc}"
            )


def _scopes_for_reason(reason: str) -> set[str]:
    """Return the semantic scopes a degradation reason implicates."""

    try:
        _, scope = classify_degradation_reason(reason)
    except Exception:
        return set()
    return {scope.value}


def _run_recovery_sweep(
    *,
    settings,
    coverage_complete: bool,
    degraded_scopes: set[str],
    failures: list[str],
) -> None:
    """Single entry point for recovery work, used on every cycle path.

    Kept separate so the resolver-exception early-return path performs the
    required recovery cycle instead of returning before it can occur.
    """

    try:
        _reconcile_local_unconfirmed_deliveries(failures=failures)
    except Exception as exc:
        failures.append(
            f"local delivery-guard reconciliation failed: {type(exc).__name__}: {exc}"
        )
    try:
        _retry_pending_system_notifications(settings=settings, failures=failures)
    except Exception as exc:
        failures.append(
            f"pending system-notification retry failed: {type(exc).__name__}: {exc}"
        )
    _reconcile_system_incident_recovery(
        settings=settings,
        coverage_complete=coverage_complete,
        degraded_scopes=degraded_scopes,
        failures=failures,
    )


def _notify_unmanaged_holding(*, settings, exposure: ResolvedExposure) -> bool:
    now = datetime.now(timezone.utc)
    day_bucket = now.strftime("%Y%m%d")
    identity = f"UNMANAGED_KRAKEN:{exposure.symbol}:{exposure.direction}"
    fingerprint = f"{day_bucket}:{exposure.symbol}:{exposure.direction}"
    if not should_emit(
        identity=identity,
        event_type="UNMANAGED_HOLDING",
        fingerprint=fingerprint,
        cooldown_seconds=24 * 60 * 60,
        now=now,
    ):
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type="UNMANAGED_HOLDING",
            fingerprint=fingerprint,
            reason="NOTIFICATION_POLICY",
            generated_at=now,
        )
        return False

    quantity = (
        "unknown"
        if exposure.observed_quantity is None
        else f"{float(exposure.observed_quantity):.8g}"
    )
    notional = (
        ""
        if exposure.notional_usd is None
        else f" | Approx notional USD: {float(exposure.notional_usd):,.2f}"
    )
    message = (
        f"⚠️ O'PIP EXISTING HOLDING NEEDS CONTEXT — {display_market_label(exposure.symbol)}\n"
        f"Kraken quantity: {quantity}{notional}\n"
        f"Direction: {exposure.direction}\n"
        "Protection: LIMITED — entry/stop/targets are not known to O'Pip\n"
        "Action: REVIEW THIS HOLDING / ATTACH LIFECYCLE CONTEXT\n"
        "No order was placed or changed."
    )
    delivery = send_tracked_telegram(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        message=message,
        identity=identity,
        alert_family="ACTIVE_TRADE",
        event_type="UNMANAGED_HOLDING",
        fingerprint=fingerprint,
        symbol=exposure.symbol,
        generated_at=now,
    )
    if delivery.delivered:
        record_emitted(
            identity=identity,
            event_type="UNMANAGED_HOLDING",
            fingerprint=fingerprint,
            now=now,
        )
    return delivery.delivered


def run_active_trade_monitor() -> MonitorRunSummary:
    settings = get_settings()
    failures: list[str] = []

    try:
        resolution = KrakenExposureResolver(
            trade_loader=get_active_trades,
            managed_verifier_factory=KrakenPositionVerifier,
        ).resolve()
    except Exception as exc:
        reason = f"Kraken-first exposure resolution failed: {exc}"
        failures = [reason]
        _notify_monitor_degraded_safe(
            settings=settings,
            reason=reason,
            failures=failures,
        )
        # This path must still perform the required recovery cycle rather than
        # returning before automatic recovery can run.
        _run_recovery_sweep(
            settings=settings,
            coverage_complete=False,
            degraded_scopes=_scopes_for_reason(reason),
            failures=failures,
        )
        return MonitorRunSummary(
            active_trades=0,
            checked=0,
            monitor_notifications_sent=0,
            emergency_notifications_sent=0,
            positions_verified=0,
            positions_absent=0,
            positions_unavailable=0,
            positions_unmanaged=0,
            failures=failures,
        )

    managed = [e for e in resolution.exposures if e.trade is not None]
    checked = 0
    monitor_notifications_sent = 0
    emergency_notifications_sent = 0
    positions_verified = 0
    positions_absent = 0
    positions_unavailable = 0
    positions_unmanaged = 0
    degraded_symbols: list[str] = []
    #: Scopes implicated as degraded during *this* cycle. A scope in this set
    #: must not be closed as recovered by coverage evidence in the same cycle
    #: that observed it failing. Connectivity scopes are exempt from the guard:
    #: their probe still runs while the outage is active.
    degraded_scopes: set[str] = set()

    def _record_degraded_scope(reason: str) -> None:
        degraded_scopes.update(_scopes_for_reason(reason))

    if not resolution.coverage_complete:
        reason = resolution.reason or "Kraken exposure coverage is incomplete"
        failures.append(reason)
        _record_degraded_scope(reason)
        _notify_monitor_degraded_safe(
            settings=settings,
            reason=reason,
            failures=failures,
        )

    for exposure in resolution.exposures:
        if exposure.status == "VERIFIED_UNMANAGED":
            positions_unmanaged += 1
            try:
                _notify_unmanaged_holding(settings=settings, exposure=exposure)
            except Exception as exc:
                failures.append(
                    f"{exposure.symbol}: unmanaged-holding notification failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            continue

        trade = exposure.trade
        if trade is None:
            if exposure.status in {"UNKNOWN", "DEGRADED"}:
                positions_unavailable += 1
                degraded_symbols.append(f"{exposure.symbol}:{exposure.status}")
            continue

        if exposure.status == "ABSENT":
            positions_absent += 1
            failures.append(
                f"{trade.symbol}: active registry lifecycle has no verified Kraken exposure; "
                "terminalization remains owned by reconciliation"
            )
            continue

        if exposure.status != "VERIFIED_MANAGED":
            positions_unavailable += 1
            degraded_symbols.append(f"{trade.symbol}:{exposure.status}")
            failures.append(
                f"{trade.symbol}: position protection unavailable; {exposure.reason}"
            )
            continue

        positions_verified += 1
        monitor_result = None
        observation = None
        try:
            monitor_result = monitor_trade(trade)
            observation = update_active_observation(
                trade,
                monitor_result.current_price,
            )
            monitor_result = refine_protection_action(
                trade,
                monitor_result,
                observation,
            )
            checked += 1
        except Exception as exc:
            degraded_symbols.append(f"{trade.symbol}:MONITOR_ERROR")
            failures.append(
                f"{trade.symbol}: deterministic monitor failed: "
                f"{type(exc).__name__}: {exc}"
            )

        if monitor_result is not None:
            try:
                if send_monitor_update(
                    trade=trade,
                    result=monitor_result,
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                    observation=observation,
                ):
                    monitor_notifications_sent += 1
            except Exception as exc:
                failures.append(
                    f"{trade.symbol}: monitor notification failed: "
                    f"{type(exc).__name__}: {exc}"
                )

        # Emergency protection is independent from routine monitor state and
        # notification transport. A failure in either path must not suppress
        # the other protection path for the same verified holding.
        try:
            emergency_result = detect_emergency_move(trade)
        except Exception as exc:
            degraded_symbols.append(f"{trade.symbol}:EMERGENCY_ERROR")
            failures.append(
                f"{trade.symbol}: emergency detection failed: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            try:
                if send_emergency_alert(
                    trade=trade,
                    result=emergency_result,
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                ):
                    emergency_notifications_sent += 1
            except Exception as exc:
                failures.append(
                    f"{trade.symbol}: emergency notification failed: "
                    f"{type(exc).__name__}: {exc}"
                )

    if degraded_symbols:
        reason = (
            f"{len(degraded_symbols)} verified/expected holding(s) not fully protected: "
            + ", ".join(degraded_symbols[:8])
        )
        _record_degraded_scope(reason)
        _notify_monitor_degraded_safe(
            settings=settings,
            reason=reason,
            failures=failures,
        )

    # Advance recovery for monitor-owned scopes. This runs after protection work
    # so recovery can never delay a protection decision.
    try:
        _run_recovery_sweep(
            settings=settings,
            coverage_complete=bool(resolution.coverage_complete),
            degraded_scopes=degraded_scopes,
            failures=failures,
        )
    except Exception as exc:
        failures.append(
            f"system-incident recovery reconciliation failed: {type(exc).__name__}: {exc}"
        )

    return MonitorRunSummary(
        active_trades=len(managed),
        checked=checked,
        monitor_notifications_sent=monitor_notifications_sent,
        emergency_notifications_sent=emergency_notifications_sent,
        positions_verified=positions_verified,
        positions_absent=positions_absent,
        positions_unavailable=positions_unavailable,
        positions_unmanaged=positions_unmanaged,
        failures=failures,
    )