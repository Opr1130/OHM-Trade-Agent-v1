from datetime import datetime, timezone
from pathlib import Path
import math

from app.services.active_trade_registry import ActiveTrade
from app.services.asset_display_identity import display_market_label
from app.services.compact_alerts import one_line_reason
from app.services.notification_policy import record_emitted, should_emit
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic
from app.services.telegram_delivery import record_telegram_suppression, send_tracked_telegram
from app.services.trade_monitor import TradeMonitorResult


STATE_FILE = Path("/app/data/trade_monitor_state.json")
ACTION_REPEAT_SECONDS = {
    "TAKE_PROFIT": 10 * 60,
    "EXIT_NOW": 5 * 60,
}
WARNING_RISK_PROGRESS_STEP = 0.20
WARNING_MFE_GIVEBACK_STEP_R = 0.50


def _load_state() -> dict:
    with registry_lock(STATE_FILE.parent / f".{STATE_FILE.name}.lock"):
        return load_json(STATE_FILE)


def _save_state(state: dict) -> None:
    with registry_lock(STATE_FILE.parent / f".{STATE_FILE.name}.lock"):
        save_json_atomic(STATE_FILE, state)


def _previous_action(value) -> str | None:
    if isinstance(value, dict):
        raw = value.get("action")
    else:
        raw = value
    return str(raw) if raw not in (None, "") else None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stop_downside_pct(trade: ActiveTrade, current_price: float) -> float:
    if current_price <= 0:
        return 0.0
    direction = str(trade.direction or "LONG").upper()
    if direction == "SHORT":
        return max(0.0, (float(trade.stop_price) / current_price - 1.0) * 100.0)
    return max(0.0, (1.0 - float(trade.stop_price) / current_price) * 100.0)


def _risk_progress(trade: ActiveTrade, current_price: float) -> float:
    """Adverse movement expressed in units of the original entry-to-stop risk."""
    entry = float(trade.entry_price)
    stop = float(trade.stop_price)
    risk = abs(entry - stop)
    if risk <= 0 or not all(math.isfinite(v) for v in (entry, stop, current_price)):
        return 0.0
    if str(trade.direction or "LONG").upper() == "SHORT":
        return (current_price - entry) / risk
    return (entry - current_price) / risk


def _reason_signature(result: TradeMonitorResult) -> str:
    return "|".join(sorted(str(reason).strip() for reason in (result.reasons or []) if str(reason).strip()))


def _mfe_giveback_r(
    trade: ActiveTrade,
    result: TradeMonitorResult,
    observation: dict | None,
) -> float:
    if not observation:
        return 0.0
    try:
        mfe_pct = float(observation.get("mfe_pct") or 0.0)
        current_pct = float(result.unrealized_pct)
        entry = float(trade.entry_price)
        stop = float(trade.stop_price)
    except (TypeError, ValueError):
        return 0.0
    risk_pct = abs(entry - stop) / entry * 100.0 if entry > 0 else 0.0
    if risk_pct <= 0 or not all(math.isfinite(v) for v in (mfe_pct, current_pct, risk_pct)):
        return 0.0
    giveback_pct = max(0.0, mfe_pct - max(0.0, current_pct))
    return giveback_pct / risk_pct


def _repeat_due(previous: dict, action: str, now: datetime) -> bool:
    repeat_seconds = ACTION_REPEAT_SECONDS.get(action)
    if repeat_seconds is None:
        return False
    previous_at = _parse(previous.get("updated_at"))
    return previous_at is None or (now - previous_at).total_seconds() >= repeat_seconds


def _material_warning_change(
    *,
    previous: dict,
    reason_signature: str,
    risk_progress: float,
    mfe_giveback_r: float,
) -> bool:
    if previous.get("reason_signature") != reason_signature:
        return True
    try:
        previous_risk = float(previous.get("risk_progress") or 0.0)
    except (TypeError, ValueError):
        previous_risk = 0.0
    if risk_progress - previous_risk >= WARNING_RISK_PROGRESS_STEP:
        return True
    try:
        previous_giveback = float(previous.get("mfe_giveback_r") or 0.0)
    except (TypeError, ValueError):
        previous_giveback = 0.0
    return mfe_giveback_r - previous_giveback >= WARNING_MFE_GIVEBACK_STEP_R


def format_monitor_message(trade: ActiveTrade, result: TradeMonitorResult) -> str:
    icon = {
        "WARNING": "⚠️",
        "TAKE_PROFIT": "🎯",
        "EXIT_NOW": "🛑",
    }.get(result.action, "ℹ️")
    pnl_pct = result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct
    downside = _stop_downside_pct(trade, float(result.current_price))
    reason = one_line_reason(*(result.reasons or []))
    return (
        f"{icon} O'PIP EXISTING TRADE ACTION — {display_market_label(trade.symbol)}\n"
        f"Price: {float(result.current_price):.8g} | Entry: {float(trade.entry_price):.8g}\n"
        f"P/L: {float(pnl_pct):+.2f}% | Risk: {trade.risk_level.upper()}\n"
        f"Stop: {float(trade.stop_price):.8g} | Downside: {downside:.1f}%\n"
        f"T1 / T2: {float(trade.target_1):.8g} / {float(trade.target_2):.8g}\n"
        f"Reason: {reason}\n"
        f"Action: {result.action.replace('_', ' ')}"
    )


def send_monitor_update(
    trade: ActiveTrade,
    result: TradeMonitorResult,
    bot_token: str,
    chat_id: str,
    *,
    observation: dict | None = None,
) -> bool:
    if trade.status != "active":
        return False

    now = datetime.now(timezone.utc)
    identity = f"ACTIVE_TRADE:{trade.trade_id or trade.symbol}"
    reason_signature = _reason_signature(result)
    risk_progress = _risk_progress(trade, float(result.current_price))
    mfe_giveback_r = _mfe_giveback_r(trade, result, observation)

    try:
        state = _load_state()
    except (OSError, TimeoutError, RegistryIOError):
        # Protection actions fail open into NotificationPolicy for critical
        # classes; do not let a local monitor-state file silence EXIT/TP.
        state = {}

    # HEALTHY/HOLD is user-silent, but record the recovery internally. Without
    # this transition a later return to the same WARNING fingerprint could be
    # mistaken for one uninterrupted warning and be suppressed.
    if result.action == "HOLD":
        previous = state.get(trade.symbol)
        if isinstance(previous, dict) and _previous_action(previous) not in {None, "HOLD"}:
            state[trade.symbol] = {
                "action": "HOLD",
                "message_id": previous.get("message_id"),
                "updated_at": now.isoformat(),
                "price": float(result.current_price),
                "pnl_pct": float(
                    result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct
                ),
                "reason_signature": reason_signature,
                "risk_progress": risk_progress,
                "mfe_giveback_r": mfe_giveback_r,
            }
            try:
                _save_state(state)
            except (OSError, TimeoutError, RegistryIOError):
                pass
        return False

    previous = state.get(trade.symbol)
    if not isinstance(previous, dict):
        previous = {}

    previous_action = _previous_action(previous)
    action_changed = previous_action != result.action

    if not action_changed:
        if result.action == "WARNING":
            if not _material_warning_change(
                previous=previous,
                reason_signature=reason_signature,
                risk_progress=risk_progress,
                mfe_giveback_r=mfe_giveback_r,
            ):
                record_telegram_suppression(
                    identity=identity,
                    alert_family="ACTIVE_TRADE",
                    event_type=result.action,
                    fingerprint=f"{trade.direction}:{result.action}",
                    reason="NO_MATERIAL_DETERIORATION",
                    symbol=trade.symbol,
                    trade_id=trade.trade_id,
                )
                return False
        elif result.action in ACTION_REPEAT_SECONDS:
            if not _repeat_due(previous, result.action, now):
                record_telegram_suppression(
                    identity=identity,
                    alert_family="ACTIVE_TRADE",
                    event_type=result.action,
                    fingerprint=f"{trade.direction}:{result.action}",
                    reason="ACTION_REPEAT_NOT_DUE",
                    symbol=trade.symbol,
                    trade_id=trade.trade_id,
                )
                return False
        else:
            return False

    if result.action == "WARNING":
        risk_bucket = math.floor(max(0.0, risk_progress) / WARNING_RISK_PROGRESS_STEP)
        giveback_bucket = math.floor(max(0.0, mfe_giveback_r) / WARNING_MFE_GIVEBACK_STEP_R)
        fingerprint = (
            f"{trade.direction}:WARNING:{reason_signature}:"
            f"risk={risk_bucket}:giveback={giveback_bucket}"
        )
    elif result.action in ACTION_REPEAT_SECONDS:
        repeat_seconds = ACTION_REPEAT_SECONDS[result.action]
        bucket = int(now.timestamp()) // repeat_seconds
        fingerprint = f"{trade.direction}:{result.action}:{bucket}"
    else:
        fingerprint = f"{trade.direction}:{result.action}:{reason_signature}"

    policy_event_type = "POSITION_WARNING" if result.action == "WARNING" else result.action
    if not should_emit(
        identity=identity,
        event_type=policy_event_type,
        fingerprint=fingerprint,
        now=now,
    ):
        record_telegram_suppression(
            identity=identity,
            alert_family="ACTIVE_TRADE",
            event_type=result.action,
            fingerprint=fingerprint,
            reason="NOTIFICATION_POLICY",
            symbol=trade.symbol,
            trade_id=trade.trade_id,
            generated_at=now,
        )
        return False

    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=format_monitor_message(trade, result),
        identity=identity,
        alert_family="ACTIVE_TRADE",
        event_type=result.action,
        fingerprint=fingerprint,
        symbol=trade.symbol,
        trade_id=trade.trade_id,
        generated_at=now,
    )
    if delivery.delivered:
        state[trade.symbol] = {
            "action": result.action,
            "message_id": delivery.message_id,
            "updated_at": now.isoformat(),
            "price": float(result.current_price),
            "pnl_pct": float(
                result.net_pnl_pct if result.net_pnl_pct is not None else result.unrealized_pct
            ),
            "reason_signature": reason_signature,
            "risk_progress": risk_progress,
            "mfe_giveback_r": mfe_giveback_r,
        }
        try:
            _save_state(state)
        except (OSError, TimeoutError, RegistryIOError):
            pass
        record_emitted(
            identity=identity,
            event_type=policy_event_type,
            fingerprint=fingerprint,
            now=now,
        )
    return delivery.delivered