from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Iterable

from app.backtesting.metrics import build_report
from app.backtesting.models import ReplayTrade


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass(frozen=True)
class WindowResult:
    window: WalkForwardWindow
    train_trades: int
    test_trades: int
    train_expectancy: float | None
    test_expectancy: float | None
    test_profit_factor: float | None
    test_max_drawdown_pct: float


def evaluate_walk_forward(
    trades: Iterable[ReplayTrade], windows: Iterable[WalkForwardWindow], initial_equity: float
) -> dict[str, object]:
    """Evaluate fixed, non-overlapping out-of-sample windows without tuning."""
    ordered = sorted(trades, key=lambda trade: trade.exited_at)
    results: list[WindowResult] = []
    seen_test_exits: set[datetime] = set()

    for window in windows:
        if not (window.train_start < window.train_end <= window.test_start < window.test_end):
            raise ValueError("walk-forward windows must have train before test")
        train = [t for t in ordered if window.train_start <= t.exited_at < window.train_end]
        test = [t for t in ordered if window.test_start <= t.exited_at < window.test_end]
        for trade in test:
            if trade.exited_at in seen_test_exits:
                raise ValueError("test windows must not overlap")
            seen_test_exits.add(trade.exited_at)
        train_report = build_report(initial_equity, train)
        test_report = build_report(initial_equity, test)
        results.append(WindowResult(
            window=window,
            train_trades=train_report.trade_count,
            test_trades=test_report.trade_count,
            train_expectancy=train_report.expectancy,
            test_expectancy=test_report.expectancy,
            test_profit_factor=test_report.profit_factor,
            test_max_drawdown_pct=test_report.max_drawdown_pct,
        ))

    test_expectancies = [r.test_expectancy for r in results if r.test_expectancy is not None]
    profitable_windows = sum(1 for value in test_expectancies if value > 0)
    return {
        "windows": results,
        "window_count": len(results),
        "evaluated_test_windows": len(test_expectancies),
        "profitable_test_windows": profitable_windows,
        "profitable_window_rate_pct": (
            round(profitable_windows / len(test_expectancies) * 100.0, 2) if test_expectancies else None
        ),
        "mean_test_expectancy": round(mean(test_expectancies), 4) if test_expectancies else None,
    }
