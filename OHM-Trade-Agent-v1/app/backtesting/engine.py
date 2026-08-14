from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.backtesting.metrics import build_report
from app.backtesting.models import BacktestReport, Candle, ReplayTrade


@dataclass(frozen=True)
class ReplaySetup:
    symbol: str
    direction: str
    signal_at: datetime
    entry_price: float
    stop_price: float
    target_price: float
    capital: float
    leverage: float = 1.0
    round_trip_cost_pct: float = 0.60
    market_regime: str | None = None
    setup_type: str | None = None
    max_bars_held: int = 24


def _directional_pnl(direction: str, entry: float, exit_price: float, notional: float) -> float:
    if direction == "SHORT":
        return notional * (entry - exit_price) / entry
    return notional * (exit_price - entry) / entry


def _bar_touches_entry(setup: ReplaySetup, candle: Candle) -> bool:
    return candle.low <= setup.entry_price <= candle.high


def _exit_for_bar(setup: ReplaySetup, candle: Candle) -> tuple[float, str] | None:
    """Return a conservative intrabar exit.

    If stop and target are both touched in one candle, choose the stop because
    OHLC data cannot prove that the profitable target happened first.
    """
    if setup.direction == "SHORT":
        stop_hit = candle.high >= setup.stop_price
        target_hit = candle.low <= setup.target_price
    else:
        stop_hit = candle.low <= setup.stop_price
        target_hit = candle.high >= setup.target_price
    if stop_hit:
        return setup.stop_price, "STOP"
    if target_hit:
        return setup.target_price, "TARGET"
    return None


def replay_setup(setup: ReplaySetup, candles: Iterable[Candle]) -> ReplayTrade | None:
    direction = setup.direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if min(setup.entry_price, setup.stop_price, setup.target_price, setup.capital, setup.leverage) <= 0:
        raise ValueError("prices, capital and leverage must be positive")
    if setup.max_bars_held <= 0:
        raise ValueError("max_bars_held must be positive")

    eligible = [c for c in candles if c.timestamp >= setup.signal_at]
    entered_at = None
    entry_index = None
    for idx, candle in enumerate(eligible):
        if _bar_touches_entry(setup, candle):
            entered_at = candle.timestamp
            entry_index = idx
            break
    if entered_at is None or entry_index is None:
        return None

    exit_price = setup.entry_price
    exit_reason = "TIME"
    exited_at = eligible[entry_index].timestamp
    bars_held = 0
    for offset, candle in enumerate(eligible[entry_index:], start=1):
        bars_held = offset
        exited_at = candle.timestamp
        result = _exit_for_bar(setup, candle)
        if result is not None:
            exit_price, exit_reason = result
            break
        exit_price = candle.close
        if offset >= setup.max_bars_held:
            break

    notional = setup.capital * setup.leverage
    gross_pnl = _directional_pnl(direction, setup.entry_price, exit_price, notional)
    costs = notional * setup.round_trip_cost_pct / 100.0
    net_pnl = gross_pnl - costs
    outcome = "WIN" if net_pnl > 0 else "LOSS" if net_pnl < 0 else "BREAKEVEN"
    return ReplayTrade(
        symbol=setup.symbol,
        direction=direction,
        entered_at=entered_at,
        exited_at=exited_at,
        entry_price=round(setup.entry_price, 8),
        exit_price=round(exit_price, 8),
        capital=round(setup.capital, 2),
        leverage=round(setup.leverage, 4),
        gross_pnl=round(gross_pnl, 2),
        costs=round(costs, 2),
        net_pnl=round(net_pnl, 2),
        outcome=outcome,
        bars_held=bars_held,
        exit_reason=exit_reason,
        market_regime=setup.market_regime,
        setup_type=setup.setup_type,
    )


def run_backtest(
    *, initial_equity: float, setups: Iterable[ReplaySetup], candles_by_symbol: dict[str, list[Candle]]
) -> BacktestReport:
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    trades: list[ReplayTrade] = []
    for setup in sorted(setups, key=lambda item: item.signal_at):
        trade = replay_setup(setup, candles_by_symbol.get(setup.symbol, []))
        if trade is not None:
            trades.append(trade)
    trades.sort(key=lambda item: item.exited_at)
    return build_report(initial_equity, trades)
