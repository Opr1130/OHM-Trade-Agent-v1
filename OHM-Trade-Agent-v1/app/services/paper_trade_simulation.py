from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

from app.services.paper_trade_models import PaperTradeLifecycle


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def first_full_bar_start(signal_at: str, interval_minutes: int) -> int:
    """First OHLC bar whose entire duration occurs after the signal."""
    signal = parse_utc(signal_at)
    if signal is None:
        raise ValueError("signal_at is invalid")
    interval_seconds = int(interval_minutes) * 60
    timestamp = int(signal.timestamp())
    return ((timestamp + interval_seconds - 1) // interval_seconds) * interval_seconds


def _valid_price(value: Any) -> float:
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("paper simulation price must be finite and positive")
    return price


def _market_sell_fill(reference: float, slippage_bps: float) -> float:
    return _valid_price(reference) * (1.0 - float(slippage_bps) / 10_000.0)


def _realize(
    trade: PaperTradeLifecycle,
    *,
    quantity: float,
    exit_price: float,
) -> None:
    if trade.entry_price is None:
        raise ValueError("paper trade has no entry price")
    quantity = max(0.0, min(float(quantity), trade.quantity_remaining))
    if quantity <= 0:
        return
    price = _valid_price(exit_price)
    trade.realized_gross_pnl += quantity * (price - trade.entry_price)
    trade.fees_paid += quantity * price * trade.fee_rate
    trade.quantity_remaining = max(0.0, trade.quantity_remaining - quantity)


def open_limit_entry(
    trade: PaperTradeLifecycle,
    *,
    at: datetime,
) -> None:
    price = _valid_price(trade.entry_limit)
    trade.entry_price = price
    trade.quantity_initial = trade.capital / price
    trade.quantity_remaining = trade.quantity_initial
    trade.entry_fee = trade.capital * trade.fee_rate
    trade.fees_paid = trade.entry_fee
    trade.opened_at = at.astimezone(timezone.utc).isoformat()
    trade.status = "OPEN"


def cancel_pending(
    trade: PaperTradeLifecycle,
    *,
    reason: str,
    at: datetime,
    observed_price: float | None = None,
) -> None:
    trade.status = "CANCELLED"
    trade.closed_at = at.astimezone(timezone.utc).isoformat()
    trade.exit_reason = str(reason)
    trade.last_observed_price = observed_price
    trade.gross_pnl = 0.0
    trade.net_pnl = 0.0
    trade.net_pnl_pct = 0.0
    trade.outcome = "NO_TRADE"


def take_profit_1(
    trade: PaperTradeLifecycle,
    *,
    at: datetime,
) -> None:
    if trade.tp1_hit or trade.quantity_remaining <= 0:
        return
    quantity = min(
        trade.quantity_remaining,
        trade.quantity_initial * trade.tp1_fraction,
    )
    _realize(trade, quantity=quantity, exit_price=trade.target_1)
    trade.tp1_hit = True
    trade.tp1_at = at.astimezone(timezone.utc).isoformat()
    trade.tp1_price = trade.target_1
    trade.tp1_quantity = quantity


def close_remaining(
    trade: PaperTradeLifecycle,
    *,
    reference_price: float,
    reason: str,
    at: datetime,
    market_exit: bool,
) -> None:
    fill = (
        _market_sell_fill(reference_price, trade.slippage_bps)
        if market_exit
        else _valid_price(reference_price)
    )
    _realize(trade, quantity=trade.quantity_remaining, exit_price=fill)
    trade.status = "CLOSED"
    trade.closed_at = at.astimezone(timezone.utc).isoformat()
    trade.exit_price = fill
    trade.exit_reason = str(reason)
    trade.gross_pnl = trade.realized_gross_pnl
    trade.net_pnl = trade.realized_gross_pnl - trade.fees_paid
    trade.net_pnl_pct = (
        trade.net_pnl / trade.capital * 100.0
        if trade.capital > 0
        else 0.0
    )
    trade.outcome = (
        "WIN"
        if trade.net_pnl > 0
        else "LOSS"
        if trade.net_pnl < 0
        else "BREAKEVEN"
    )


def process_closed_candle(
    trade: PaperTradeLifecycle,
    candle: Any,
    *,
    interval_minutes: int,
) -> str:
    """Apply one fully post-signal closed candle.

    Intrabar path is unknowable. For an open position, stop always wins if a
    stop and any target are touched in the same candle. For a newly filled
    limit entry, targets are never credited in the entry candle; a same-candle
    stop is charged immediately.
    """
    ts = int(candle.timestamp)
    start = datetime.fromtimestamp(ts, tz=timezone.utc)
    end = start + timedelta(minutes=int(interval_minutes))
    low = _valid_price(candle.low)
    high = _valid_price(candle.high)
    close = _valid_price(candle.close)
    open_price = _valid_price(candle.open)

    trade.last_processed_candle_ts = ts
    trade.last_observed_price = close

    if trade.status == "PENDING_ENTRY":
        signal = parse_utc(trade.signal_at)
        if signal is None:
            raise ValueError("paper signal time is invalid")
        expiry = signal + timedelta(hours=trade.pending_ttl_hours)
        if start >= expiry or end > expiry:
            # Never attribute an entry from a candle that is wholly or partly
            # after the pending-order lifetime. Intrabar timing cannot prove
            # that a touch occurred before expiry.
            cancel_pending(
                trade,
                reason="PENDING_TTL_EXPIRED",
                at=min(max(expiry, start), end),
                observed_price=open_price,
            )
            return "CANCELLED"

        if low <= trade.entry_limit:
            open_limit_entry(trade, at=start)
            if low <= trade.stop_price:
                stop_reference = min(trade.stop_price, open_price)
                close_remaining(
                    trade,
                    reference_price=stop_reference,
                    reason="ENTRY_CANDLE_STOP",
                    at=end,
                    market_exit=True,
                )
                return "CLOSED"
            # Do not credit target touches in the fill candle because OHLC does
            # not prove they happened after the entry touch.
            return "OPENED"

        if close > trade.chase_limit:
            cancel_pending(
                trade,
                reason="DO_NOT_CHASE",
                at=end,
                observed_price=close,
            )
            return "CANCELLED"
        return "NO_CHANGE"

    if trade.status != "OPEN":
        return "NO_CHANGE"

    opened = parse_utc(trade.opened_at)
    if opened is None:
        raise ValueError("open paper trade has invalid opened_at")
    deadline = opened + timedelta(hours=trade.max_hold_hours)
    straddles_deadline = start < deadline < end
    expired_before_bar = start >= deadline

    stop_hit = low <= trade.stop_price
    if stop_hit:
        stop_reference = min(trade.stop_price, open_price)
        close_remaining(
            trade,
            reference_price=stop_reference,
            reason="STOP",
            at=end,
            market_exit=True,
        )
        return "CLOSED"

    if expired_before_bar or straddles_deadline:
        # If the deadline shares a candle with a stop, the adverse stop above
        # already wins. Otherwise close at the bar open and never credit a
        # target that may have occurred after the allowed holding window.
        close_remaining(
            trade,
            reference_price=open_price,
            reason="TIME_EXIT",
            at=start if expired_before_bar else deadline,
            market_exit=True,
        )
        return "CLOSED"

    if not trade.tp1_hit and high >= trade.target_2:
        take_profit_1(trade, at=end)
        close_remaining(
            trade,
            reference_price=trade.target_2,
            reason="TARGET_2",
            at=end,
            market_exit=False,
        )
        return "CLOSED"

    if not trade.tp1_hit and high >= trade.target_1:
        take_profit_1(trade, at=end)

    if trade.tp1_hit and trade.status == "OPEN" and high >= trade.target_2:
        close_remaining(
            trade,
            reference_price=trade.target_2,
            reason="TARGET_2",
            at=end,
            market_exit=False,
        )
        return "CLOSED"

    if end >= deadline:
        close_remaining(
            trade,
            reference_price=close,
            reason="TIME_EXIT",
            at=deadline,
            market_exit=True,
        )
        return "CLOSED"

    return "TP1" if trade.tp1_hit else "NO_CHANGE"
