from __future__ import annotations

from app.backtesting.models import BacktestReport, ReplayTrade


def build_report(initial_equity: float, trades: list[ReplayTrade]) -> BacktestReport:
    equity = float(initial_equity)
    peak = equity
    max_drawdown_pct = 0.0
    wins = losses = breakeven = 0
    gross_profit = gross_loss = 0.0

    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak * 100)
        if trade.net_pnl > 0:
            wins += 1
            gross_profit += trade.net_pnl
        elif trade.net_pnl < 0:
            losses += 1
            gross_loss += abs(trade.net_pnl)
        else:
            breakeven += 1

    count = len(trades)
    net_profit = equity - initial_equity
    return BacktestReport(
        initial_equity=round(initial_equity, 2),
        final_equity=round(equity, 2),
        trades=trades,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        net_profit=round(net_profit, 2),
        profit_factor=(round(gross_profit / gross_loss, 4) if gross_loss > 0 else None),
        expectancy=(round(net_profit / count, 4) if count else None),
        max_drawdown_pct=round(max_drawdown_pct, 4),
        win_rate_pct=(round(wins / count * 100, 2) if count else None),
    )
