"""B/C-4 O'Pip cockpit analytical projections.

Read-only derived analytics over canonical Paper-v2 evidence. Nothing here has
trading, exchange, promotion or write authority, and nothing here is a second
source of truth: the canonical writer remains the authority for ancestry,
conservation and economics, and the versioned metric registry remains the
authority for metric semantics.

Trust semantics are defined in :mod:`app.opip.cockpit.trust` and kept deliberately
independent (freshness, completeness, fidelity, statistical uncertainty).
"""

from __future__ import annotations

from app.opip.cockpit.ledger import (
    COCKPIT_LEDGER_PROJECTION_VERSION,
    EconomicResult,
    EconomicsSource,
    ExecutionResult,
    ExitMechanism,
    LifecycleStatus,
    PaperLedger,
    ReconciledPaperTrade,
    build_ledger,
    build_trade_row,
    read_paper_ledger,
)
from app.opip.cockpit.portfolio import (
    COCKPIT_PORTFOLIO_PROJECTION_VERSION,
    AccountabilitySummary,
    AttentionItem,
    CockpitOverview,
    CurrencyPortfolio,
    DrawdownSummary,
    EquityPoint,
    StrategyContribution,
    ValuationPosture,
    build_currency_portfolio,
    build_drawdown_summary,
    build_overview,
    build_realized_equity_series,
    build_strategy_contribution,
)
from app.opip.cockpit.trust import (
    Completeness,
    CorrectionState,
    Fidelity,
    Freshness,
    TrustEnvelope,
    Uncertainty,
)

__all__ = [
    "COCKPIT_LEDGER_PROJECTION_VERSION",
    "COCKPIT_PORTFOLIO_PROJECTION_VERSION",
    "AccountabilitySummary",
    "AttentionItem",
    "CockpitOverview",
    "Completeness",
    "CorrectionState",
    "CurrencyPortfolio",
    "DrawdownSummary",
    "EconomicResult",
    "EconomicsSource",
    "EquityPoint",
    "ExecutionResult",
    "ExitMechanism",
    "Fidelity",
    "Freshness",
    "LifecycleStatus",
    "PaperLedger",
    "ReconciledPaperTrade",
    "StrategyContribution",
    "TrustEnvelope",
    "Uncertainty",
    "ValuationPosture",
    "build_currency_portfolio",
    "build_drawdown_summary",
    "build_ledger",
    "build_overview",
    "build_realized_equity_series",
    "build_strategy_contribution",
    "build_trade_row",
    "read_paper_ledger",
]
