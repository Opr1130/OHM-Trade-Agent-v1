from __future__ import annotations

from dataclasses import dataclass, field

from app.opip.contracts.paper_outcome import ENGINE_OHM_PAPER_SIM


NONTERMINAL_STATUSES = {"PENDING_ENTRY", "OPEN"}
TERMINAL_STATUSES = {"CLOSED", "CANCELLED", "UNRESOLVED"}


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
    candle_interval_minutes: int = 15
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

    # Captured at enrollment, never derived at close time. Reconstructing
    # provenance retrospectively would attribute an old trade either to the
    # currently deployed strategy or to an assumed quote currency.
    quote_currency: str | None = None
    strategy_version: str | None = None
    execution_engine: str = ENGINE_OHM_PAPER_SIM

    #: Exact canonical WriterIntent recovery envelope for the terminal outcome,
    #: persisted atomically with the lifecycle row it belongs to. This is what
    #: makes a retry byte-identical rather than a rebuild from current state,
    #: and it is why the intent is built before the lifecycle is committed.
    outcome_outbox: dict | None = None


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
    unresolved_trades: int
    #: Realised net P/L per quote currency. USD and USDT are never combined:
    #: there is no trusted conversion source for realised P/L, so a single
    #: total would be a number with no defensible meaning. ``available_capital``
    #: deliberately keeps its existing single-currency behaviour in this PR.
    realized_net_pnl_by_currency: dict[str, float] = field(default_factory=dict)
