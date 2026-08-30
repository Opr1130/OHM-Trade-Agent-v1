from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import get_settings
from app.services.active_trade_registry import get_active_trades
from app.services.kraken_exposure_resolver import KrakenExposureResolver, ResolvedExposure
from app.services.kraken_position_verification import KrakenPositionVerifier
from app.services.position_materiality import refine_protection_action
from app.services.asset_display_identity import display_market_label
from app.services.emergency_alert_notifier import send_emergency_alert
from app.services.emergency_move_detector import detect_emergency_move
from app.services.notification_policy import record_emitted, should_emit
from app.services.telegram_delivery import record_telegram_suppression, send_tracked_telegram
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


def _notify_monitor_degraded(*, settings, reason: str, identity: str = "ACTIVE_TRADE_MONITOR") -> bool:
    now = datetime.now(timezone.utc)
    hour_bucket = now.strftime("%Y%m%dT%H")
    fingerprint = f"{hour_bucket}:{reason}"
    if not should_emit(
        identity=identity,
        event_type="MONITOR_DEGRADED",
        fingerprint=fingerprint,
        cooldown_seconds=3600,
        now=now,
    ):
        record_telegram_suppression(
            identity=identity,
            alert_family="MONITOR_DEGRADED",
            event_type="MONITOR_DEGRADED",
            fingerprint=fingerprint,
            reason="NOTIFICATION_POLICY",
            generated_at=now,
        )
        return False
    message = (
        "🚨 O'PIP MONITORING DEGRADED\n"
        f"Reason: {reason}\n"
        "Protection: stop/target/emergency monitoring may be incomplete\n"
        "Action: VERIFY KRAKEN READ-ONLY CONNECTIVITY / POSITION STATE\n"
        "No order was placed or changed."
    )
    delivery = send_tracked_telegram(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        message=message,
        identity=identity,
        alert_family="MONITOR_DEGRADED",
        event_type="MONITOR_DEGRADED",
        fingerprint=fingerprint,
        generated_at=now,
    )
    if delivery.delivered:
        record_emitted(
            identity=identity,
            event_type="MONITOR_DEGRADED",
            fingerprint=fingerprint,
            now=now,
        )
    return delivery.delivered


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
        _notify_monitor_degraded(settings=settings, reason=reason)
        return MonitorRunSummary(
            active_trades=0,
            checked=0,
            monitor_notifications_sent=0,
            emergency_notifications_sent=0,
            positions_verified=0,
            positions_absent=0,
            positions_unavailable=0,
            positions_unmanaged=0,
            failures=[reason],
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

    if not resolution.coverage_complete:
        reason = resolution.reason or "Kraken exposure coverage is incomplete"
        failures.append(reason)
        _notify_monitor_degraded(settings=settings, reason=reason)

    for exposure in resolution.exposures:
        if exposure.status == "VERIFIED_UNMANAGED":
            positions_unmanaged += 1
            _notify_unmanaged_holding(settings=settings, exposure=exposure)
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

            if send_monitor_update(
                trade=trade,
                result=monitor_result,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                observation=observation,
            ):
                monitor_notifications_sent += 1

            emergency_result = detect_emergency_move(trade)
            if send_emergency_alert(
                trade=trade,
                result=emergency_result,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            ):
                emergency_notifications_sent += 1

            checked += 1
        except Exception as exc:
            degraded_symbols.append(f"{trade.symbol}:ERROR")
            failures.append(f"{trade.symbol}: {exc}")

    if degraded_symbols:
        reason = (
            f"{len(degraded_symbols)} verified/expected holding(s) not fully protected: "
            + ", ".join(degraded_symbols[:8])
        )
        _notify_monitor_degraded(settings=settings, reason=reason)

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