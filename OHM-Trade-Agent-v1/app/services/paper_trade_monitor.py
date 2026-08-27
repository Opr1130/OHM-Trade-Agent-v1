from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenClient
from app.services.paper_trade_control import CONTROL_FILE, get_paper_trade_control
from app.services.paper_trade_engine import PaperTradeConfig
from app.services.paper_trade_models import PaperTradeLifecycle
from app.services.paper_trade_registry import (
    EVENT_FILE,
    STATE_FILE,
    get_nonterminal_lifecycles,
    save_lifecycle,
)
from app.services.paper_trade_simulation import (
    cancel_pending,
    close_remaining,
    first_full_bar_start,
    parse_utc,
    process_closed_candle,
)


@dataclass(frozen=True)
class PaperMonitorSummary:
    control_enabled: bool
    tracked: int
    checked: int
    opened: int
    tp1_hits: int
    closed: int
    cancelled: int
    failures: tuple[str, ...]


def _eligible_closed_candles(
    trade: PaperTradeLifecycle,
    candles: list[Any],
    *,
    now: datetime,
    interval_minutes: int,
) -> list[Any]:
    first = first_full_bar_start(trade.signal_at, interval_minutes)
    last = int(trade.last_processed_candle_ts or 0)
    now_ts = int(now.timestamp())
    interval_seconds = int(interval_minutes) * 60
    return [
        candle
        for candle in sorted(candles, key=lambda item: int(item.timestamp))
        if int(candle.timestamp) >= first
        and int(candle.timestamp) > last
        and int(candle.timestamp) + interval_seconds <= now_ts
    ]


def _pending_expired(trade: PaperTradeLifecycle, now: datetime) -> bool:
    signal = parse_utc(trade.signal_at)
    return bool(
        signal is not None
        and now >= signal + timedelta(hours=trade.pending_ttl_hours)
    )


def _holding_expired(trade: PaperTradeLifecycle, now: datetime) -> bool:
    opened = parse_utc(trade.opened_at)
    return bool(
        opened is not None
        and now >= opened + timedelta(hours=trade.max_hold_hours)
    )


def _save(
    trade: PaperTradeLifecycle,
    event_type: str,
    *,
    state_file: Path,
    event_file: Path,
    now: datetime,
) -> None:
    save_lifecycle(
        trade,
        event_type=event_type,
        state_file=state_file,
        event_file=event_file,
        now=now,
    )


def run_paper_trade_monitor(
    config: PaperTradeConfig,
    *,
    client: KrakenClient | None = None,
    now: datetime | None = None,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
    control_file: Path = CONTROL_FILE,
) -> PaperMonitorSummary:
    """Advance isolated paper lifecycles from Kraken public market data."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    control = get_paper_trade_control(control_file)
    trades = get_nonterminal_lifecycles(state_file=state_file)
    client = client or KrakenClient(timeout_seconds=5.0)

    checked = opened = tp1_hits = closed = cancelled = 0
    failures: list[str] = []

    for trade in trades:
        try:
            # OFF means no new simulated exposure. Pending entries are cancelled
            # immediately, while already-open paper positions continue to a
            # terminal outcome so the dataset is not censored.
            if trade.status == "PENDING_ENTRY" and not control.enabled:
                cancel_pending(
                    trade,
                    reason="OPERATOR_OFF",
                    at=now,
                    observed_price=trade.last_observed_price,
                )
                _save(
                    trade,
                    "CANCELLED_OPERATOR_OFF",
                    state_file=state_file,
                    event_file=event_file,
                    now=now,
                )
                cancelled += 1
                checked += 1
                continue

            first = first_full_bar_start(
                trade.signal_at,
                config.candle_interval_minutes,
            )
            since = max(
                0,
                int(trade.last_processed_candle_ts or first)
                - config.candle_interval_minutes * 60,
            )
            candles = client.get_ohlc(
                trade.symbol,
                interval=config.candle_interval_minutes,
                since=since,
            )
            eligible = _eligible_closed_candles(
                trade,
                candles,
                now=now,
                interval_minutes=config.candle_interval_minutes,
            )

            dirty = False
            for candle in eligible:
                before_tp1 = trade.tp1_hit
                result = process_closed_candle(
                    trade,
                    candle,
                    interval_minutes=config.candle_interval_minutes,
                )
                dirty = True
                if result == "OPENED":
                    opened += 1
                    _save(
                        trade,
                        "ENTRY_FILLED",
                        state_file=state_file,
                        event_file=event_file,
                        now=now,
                    )
                    dirty = False
                if trade.tp1_hit and not before_tp1:
                    tp1_hits += 1
                    _save(
                        trade,
                        "TARGET_1",
                        state_file=state_file,
                        event_file=event_file,
                        now=now,
                    )
                    dirty = False
                if result == "CLOSED":
                    closed += 1
                    _save(
                        trade,
                        f"CLOSED_{trade.exit_reason or 'UNKNOWN'}",
                        state_file=state_file,
                        event_file=event_file,
                        now=now,
                    )
                    dirty = False
                    break
                if result == "CANCELLED":
                    cancelled += 1
                    _save(
                        trade,
                        f"CANCELLED_{trade.exit_reason or 'UNKNOWN'}",
                        state_file=state_file,
                        event_file=event_file,
                        now=now,
                    )
                    dirty = False
                    break

            if trade.status == "PENDING_ENTRY" and _pending_expired(trade, now):
                cancel_pending(
                    trade,
                    reason="PENDING_TTL_EXPIRED",
                    at=now,
                    observed_price=trade.last_observed_price,
                )
                _save(
                    trade,
                    "CANCELLED_TTL",
                    state_file=state_file,
                    event_file=event_file,
                    now=now,
                )
                cancelled += 1
                dirty = False

            if trade.status == "OPEN" and _holding_expired(trade, now):
                ticker = client.get_ticker(trade.symbol)
                reference = float(ticker.get("bid") or ticker.get("last") or 0.0)
                close_remaining(
                    trade,
                    reference_price=reference,
                    reason="TIME_EXIT",
                    at=now,
                    market_exit=True,
                )
                _save(
                    trade,
                    "CLOSED_TIME_EXIT",
                    state_file=state_file,
                    event_file=event_file,
                    now=now,
                )
                closed += 1
                dirty = False

            if dirty and trade.status in {"PENDING_ENTRY", "OPEN"}:
                _save(
                    trade,
                    "MONITORED",
                    state_file=state_file,
                    event_file=event_file,
                    now=now,
                )
            checked += 1

        except Exception as exc:
            failures.append(
                f"{trade.paper_trade_id}:{trade.symbol}:"
                f"{type(exc).__name__}:{exc}"
            )

    return PaperMonitorSummary(
        control_enabled=control.enabled,
        tracked=len(trades),
        checked=checked,
        opened=opened,
        tp1_hits=tp1_hits,
        closed=closed,
        cancelled=cancelled,
        failures=tuple(failures),
    )
