from dataclasses import dataclass
from datetime import datetime, timezone

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
    KrakenHealthScope,
    KrakenScopeProbe,
    public_connectivity_probe,
    read_only_connectivity_probe,
    transport_connection_reset,
)
from app.services.notification_policy import record_emitted, should_emit
from app.services.system_incidents import (
    SystemIncidentScope,
    classify_degradation_reason,
    confirm_incident_notification,
    observe_degradation,
    observe_recovery,
    read_incidents,
    release_incident_notification,
    requires_owner_recovery_cycles,
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


def _deliver_system_incident_decision(*, settings, decision) -> bool:
    """Deliver one Alert-v2 system-incident decision, or record why it was not sent.

    Delivery and incident state stay distinct: only a delivered message may mark
    the incident as notified. A failed send releases the reservation so a later
    cycle can retry without creating a second incident.
    """

    now = datetime.now(timezone.utc)
    policy_identity = f"{SYSTEM_HEALTH_FAMILY}:{decision.scope}"

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
    if delivery.delivered:
        confirm_incident_notification(
            decision=decision,
            message_id=delivery.message_id,
            now=now,
        )
    else:
        release_incident_notification(decision=decision, now=now)
    return delivery.delivered


def _notify_monitor_degraded(*, settings, reason: str, identity: str = "ACTIVE_TRADE_MONITOR") -> bool:
    """Alert-v2 system-incident governance for one degradation occurrence.

    Every occurrence is recorded durably. The owner is interrupted only when
    incident governance says a human genuinely needs to act -- a new incident, a
    material escalation, or a recovery. Repeated occurrences of one continuing
    condition update counters instead of producing another message.
    """

    incident_class, scope = classify_degradation_reason(reason)
    decision = observe_degradation(
        incident_class=incident_class,
        scope=scope,
        reason=reason,
    )
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
    """Run exactly one bounded recovery cycle for one connectivity scope.

    Recovery is proven on the same semantic scope that failed. A successful
    public call cannot close a read-only incident and vice versa.
    """

    if scope == SystemIncidentScope.KRAKEN_PUBLIC.value:
        health_scope = KrakenHealthScope.PUBLIC_CONNECTIVITY
        probe_callable = public_connectivity_probe()
        reset = transport_connection_reset()
    elif scope == SystemIncidentScope.KRAKEN_READ_ONLY.value:
        health_scope = KrakenHealthScope.READ_ONLY_CONNECTIVITY
        probe_callable = read_only_connectivity_probe()
        reset = None
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
        # Keep recovering. The incident stays open and the next cycle's
        # degradation observation advances the consecutive-failure counter.
        return

    decision = observe_recovery(
        incident_class=incident_class,
        scope=scope,
        evidence=f"authoritative {health_scope.value} probe succeeded",
        authoritative=True,
    )
    if decision.should_notify:
        try:
            _deliver_system_incident_decision(settings=settings, decision=decision)
        except Exception as exc:
            failures.append(
                f"{scope}: recovery notification failed: {type(exc).__name__}: {exc}"
            )


def _reconcile_system_incident_recovery(
    *,
    settings,
    coverage_complete: bool,
    degraded_scopes: set[str],
    failures: list[str],
) -> None:
    """Close incidents whose failed scope is now provably healthy again.

    Connectivity scopes need a fresh authoritative probe. Coverage-shaped scopes
    (pricing, position verification, auth, internal state) need complete coverage
    evidence. Nothing else may close an incident: a cache hit, a stale response
    or a local registry value is not proof that the failed scope recovered.
    """

    try:
        open_incidents = read_incidents(include_recovered=False)
    except Exception as exc:
        failures.append(
            f"system-incident read failed: {type(exc).__name__}: {exc}"
        )
        return

    for row in open_incidents:
        scope = str(row.get("scope") or "")
        incident_class = str(row.get("incident_class") or "")
        if not scope or scope in degraded_scopes:
            continue
        if requires_owner_recovery_cycles(incident_class):
            _attempt_connectivity_recovery(
                scope=scope,
                incident_class=incident_class,
                settings=settings,
                failures=failures,
            )
            continue
        if not coverage_complete:
            continue
        decision = observe_recovery(
            incident_class=incident_class,
            scope=scope,
            evidence="coverage complete for all verified holdings",
            authoritative=True,
        )
        if decision.should_notify:
            try:
                _deliver_system_incident_decision(settings=settings, decision=decision)
            except Exception as exc:
                failures.append(
                    f"{scope}: recovery notification failed: {type(exc).__name__}: {exc}"
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
    #: must not be closed as recovered in the same cycle that observed it failing.
    degraded_scopes: set[str] = set()

    def _record_degraded_scope(reason: str) -> None:
        try:
            _, scope = classify_degradation_reason(reason)
        except Exception:
            return
        degraded_scopes.add(scope.value)

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

    # Close incidents whose failed scope is provably healthy again. This runs
    # after protection work so recovery can never delay a protection decision.
    try:
        _reconcile_system_incident_recovery(
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