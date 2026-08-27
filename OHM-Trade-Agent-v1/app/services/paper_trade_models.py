from __future__ import annotations

from dataclasses import dataclass


NONTERMINAL_STATUSES = {"PENDING_ENTRY", "OPEN"}
TERMINAL_STATUSES = {"CLOSED", "CANCELLED"}


@dataclass
class PaperTradeLifecycle:
    paper_trade_id: str
    episode_id: str
    cohort_id: str
    symbol: str
    base_asset: str
    direction: str
    status: str
    entry_action: str
    signal_at: str
    created_at: str
    updated_at: str

    entry_low: float
    entry_high: float
    entry_limit: float
    chase_limit: float
    stop_price: float
    target_1: float
    target_2: float
    risk_level: str
    confidence: int
    profit_rank: int | None
    profit_rank_score: float | None

    capital: float
    fee_rate: float
    slippage_bps: float
    tp1_fraction: float
    pending_ttl_hours: int
    max_hold_hours: int
    reference_price: float
    reference_ask: float | None = None

    entry_price: float | None = None
    entry_fee: float = 0.0
    quantity_initial: float = 0.0
    quantity_remaining: float = 0.0
    opened_at: str | None = None

    tp1_hit: bool = False
    tp1_at: str | None = None
    tp1_price: float | None = None
    tp1_quantity: float = 0.0

    realized_gross_pnl: float = 0.0
    fees_paid: float = 0.0

    closed_at: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_pnl: float | None = None
    net_pnl: float | None = None
    net_pnl_pct: float | None = None
    outcome: str | None = None

    last_processed_candle_ts: int | None = None
    last_observed_price: float | None = None
    revision: int = 1

    paper_only: bool = True
    exchange_write_authority: bool = False


@dataclass(frozen=True)
class PaperAccountSummary:
    starting_equity: float
    realized_net_pnl: float
    closed_equity: float
    reserved_capital: float
    available_capital: float
    pending_entries: int
    open_positions: int
    closed_trades: int
    cancelled_setups: int
