from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class ReplayTrade:
    symbol: str
    direction: str
    entered_at: datetime
    exited_at: datetime
    entry_price: float
    exit_price: float
    capital: float
    leverage: float
    gross_pnl: float
    costs: float
    net_pnl: float
    outcome: str
    bars_held: int
    exit_reason: str
    market_regime: str | None = None
    setup_type: str | None = None


@dataclass
class BacktestReport:
    initial_equity: float
    final_equity: float
    trades: list[ReplayTrade] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    profit_factor: float | None = None
    expectancy: float | None = None
    max_drawdown_pct: float = 0.0
    win_rate_pct: float | None = None

    @property
    def trade_count(self) -> int:
        return len(self.trades)
