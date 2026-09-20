"""B/C-4C portfolio, contribution and attention analytics.

Derives the owner-level portfolio view from the reconciled ledger: the realized
equity/return trajectory, realized drawdown, strategy/version contribution, recent
settled trades, current watch and attention items.

The two rules that shape this module
------------------------------------

**Marked-equity drawdown is reported as UNKNOWN, not zero.** No canonical evidence
binds a current mark price to an open Paper-v2 position, so a marked series cannot
be computed. Reporting ``0`` would claim "no drawdown", which is a *different and
false* statement than "not measurable". The realized-equity series is therefore the
authoritative risk series here, and it is exposed under its own explicit name -
never silently substituted for the marked one.

**Nothing is summed across quote currencies.** USD and USDT are separate
portfolios with separate capacity, so every aggregate in this module is computed
per currency and there is no cross-currency total anywhere in its output.

Statistical posture: registered metrics such as ``paper.net_expectancy`` carry a
``SHOW_INTERVAL`` requirement. No interval estimator is registered in this slice, so
expectancy is reported with ``INSUFFICIENT_EVIDENCE`` and an explicit reason rather
than presented as a supported estimate. Nothing here invents a sample-size,
profit-factor or drawdown threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from app.opip.cockpit.ledger import (
    EconomicResult,
    LifecycleStatus,
    PaperLedger,
    ReconciledPaperTrade,
)
from app.opip.cockpit.trust import (
    Completeness,
    Freshness,
    TrustEnvelope,
    Uncertainty,
)

COCKPIT_PORTFOLIO_PROJECTION_VERSION = "cockpit-portfolio-v1"

_TOLERANCE = 1e-9

#: Asserted whenever a marked-equity figure cannot be computed. It is a code, not a
#: human string, so a panel can branch on it.
NO_MARK_EVIDENCE = "NO_CANONICAL_MARK_EVIDENCE"

#: The registered requirement for expectancy is an interval; no estimator is
#: registered in this slice, so the requirement is explicitly unmet rather than
#: quietly downgraded.
NO_INTERVAL_ESTIMATOR = "NO_REGISTERED_INTERVAL_ESTIMATOR"


@dataclass(frozen=True)
class ValuationPosture:
    """Whether a valuation-dependent figure could be computed.

    ``UNKNOWN`` is the only honest value when the underlying marks do not exist;
    it is deliberately distinct from a computed zero.
    """

    status: str = "UNKNOWN"
    reason: str = NO_MARK_EVIDENCE
    coverage: str = "NONE"

    @property
    def is_known(self) -> bool:
        return self.status == "KNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "coverage": self.coverage,
            "is_known": self.is_known,
        }


@dataclass(frozen=True)
class EquityPoint:
    at: datetime
    realized_net_pnl: float
    cumulative_net_pnl: float
    peak_cumulative_net_pnl: float
    drawdown_quote_currency: float


@dataclass(frozen=True)
class DrawdownSummary:
    """Realized-equity drawdown, plus the marked series' explicit absence.

    ``marked_*`` fields are ``None`` with a :class:`ValuationPosture` explaining why,
    so a renderer cannot accidentally treat "no marks" as "no drawdown".
    """

    realized_max_drawdown_quote_currency: float | None = None
    realized_current_drawdown_quote_currency: float | None = None
    realized_max_drawdown_pct: float | None = None
    realized_time_underwater_seconds: float | None = None
    realized_recovery_duration_seconds: float | None = None
    realized_closed_trades: int = 0
    marked_current_drawdown: float | None = None
    marked_max_drawdown: float | None = None
    valuation: ValuationPosture = field(default_factory=ValuationPosture)

    def to_dict(self) -> dict[str, Any]:
        return {
            "realized_max_drawdown_quote_currency": (
                self.realized_max_drawdown_quote_currency
            ),
            "realized_current_drawdown_quote_currency": (
                self.realized_current_drawdown_quote_currency
            ),
            "realized_max_drawdown_pct": self.realized_max_drawdown_pct,
            "realized_time_underwater_seconds": (
                self.realized_time_underwater_seconds
            ),
            "realized_recovery_duration_seconds": (
                self.realized_recovery_duration_seconds
            ),
            "realized_closed_trades": self.realized_closed_trades,
            "marked_current_drawdown": self.marked_current_drawdown,
            "marked_max_drawdown": self.marked_max_drawdown,
            "valuation": self.valuation.to_dict(),
        }


@dataclass(frozen=True)
class StrategyContribution:
    """Contribution of one policy version. A policy version is not a "winner"."""

    policy_version: str
    policy_fingerprint: str | None = None
    settled_trades: int = 0
    realized_net_pnl: float = 0.0
    expectancy_quote_currency: float | None = None
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    realized_max_drawdown_quote_currency: float | None = None
    uncertainty: Uncertainty = Uncertainty.INSUFFICIENT_EVIDENCE
    uncertainty_reasons: tuple[str, ...] = (NO_INTERVAL_ESTIMATOR,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "settled_trades": self.settled_trades,
            "realized_net_pnl": self.realized_net_pnl,
            "expectancy_quote_currency": self.expectancy_quote_currency,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "breakeven_trades": self.breakeven_trades,
            "realized_max_drawdown_quote_currency": (
                self.realized_max_drawdown_quote_currency
            ),
            "uncertainty": self.uncertainty.value,
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }


@dataclass(frozen=True)
class AttentionItem:
    """A decision-first incident: what happened, why it matters, what to do."""

    code: str
    severity: str
    summary: str
    scope: str
    state: str
    age_seconds: float | None = None
    action_required: str | None = None
    authority_affected: bool = False
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "scope": self.scope,
            "state": self.state,
            "age_seconds": self.age_seconds,
            "action_required": self.action_required,
            "authority_affected": self.authority_affected,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class AccountabilitySummary:
    """Canonical Paper-v2 accountability counts, derived from the ledger.

    Deliberately does **not** report rejected/disqualified opportunity counts: those
    opportunities never receive a ``paper_trade_id``, and no canonical projection
    enumerates their dispositions yet. Reporting them from the ledger would be a
    silent zero, so the gap is stated instead.
    """

    admitted: int = 0
    open_positions: int = 0
    closed_settled: int = 0
    flat_unverified: int = 0
    rejected_counts_available: bool = False
    rejected_unavailable_reason: str = "NO_CANONICAL_DISPOSITION_ENUMERATION"

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "open_positions": self.open_positions,
            "closed_settled": self.closed_settled,
            "flat_unverified": self.flat_unverified,
            "rejected_counts_available": self.rejected_counts_available,
            "rejected_unavailable_reason": self.rejected_unavailable_reason,
        }


@dataclass(frozen=True)
class CurrencyPortfolio:
    """Everything computed for exactly one quote currency."""

    quote_currency: str
    equity_series: tuple[EquityPoint, ...] = ()
    drawdown: DrawdownSummary = field(default_factory=DrawdownSummary)
    realized_net_pnl: float | None = None
    expectancy_quote_currency: float | None = None
    gross_pnl: float = 0.0
    execution_costs: float = 0.0
    open_positions: int = 0
    reserved_capital: float = 0.0
    strategy_contribution: tuple[StrategyContribution, ...] = ()
    recent_settled_trades: tuple[ReconciledPaperTrade, ...] = ()
    accountability: AccountabilitySummary = field(
        default_factory=AccountabilitySummary
    )
    current_watch: tuple[ReconciledPaperTrade, ...] = ()
    attention: tuple[AttentionItem, ...] = ()
    uncertainty: Uncertainty = Uncertainty.INSUFFICIENT_EVIDENCE
    uncertainty_reasons: tuple[str, ...] = (NO_INTERVAL_ESTIMATOR,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote_currency": self.quote_currency,
            "equity_series": [
                {
                    "at": point.at.isoformat().replace("+00:00", "Z"),
                    "realized_net_pnl": point.realized_net_pnl,
                    "cumulative_net_pnl": point.cumulative_net_pnl,
                    "peak_cumulative_net_pnl": point.peak_cumulative_net_pnl,
                    "drawdown_quote_currency": point.drawdown_quote_currency,
                }
                for point in self.equity_series
            ],
            "drawdown": self.drawdown.to_dict(),
            "realized_net_pnl": self.realized_net_pnl,
            "expectancy_quote_currency": self.expectancy_quote_currency,
            "gross_pnl": self.gross_pnl,
            "execution_costs": self.execution_costs,
            "open_positions": self.open_positions,
            "reserved_capital": self.reserved_capital,
            "strategy_contribution": [
                item.to_dict() for item in self.strategy_contribution
            ],
            "recent_settled_trades": [
                item.to_dict() for item in self.recent_settled_trades
            ],
            "accountability": self.accountability.to_dict(),
            "current_watch": [item.to_dict() for item in self.current_watch],
            "attention": [item.to_dict() for item in self.attention],
            "uncertainty": self.uncertainty.value,
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }


@dataclass(frozen=True)
class CockpitOverview:
    """The owner overview payload: per-currency portfolios plus global trust."""

    portfolios: tuple[CurrencyPortfolio, ...] = ()
    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )
    projection_version: str = COCKPIT_PORTFOLIO_PROJECTION_VERSION
    ledger_projection_version: str | None = None
    valuation: ValuationPosture = field(default_factory=ValuationPosture)
    attention: tuple[AttentionItem, ...] = ()
    details: tuple[str, ...] = ()

    def by_quote_currency(self) -> dict[str, CurrencyPortfolio]:
        return {item.quote_currency: item for item in self.portfolios}

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolios": [item.to_dict() for item in self.portfolios],
            "trust": self.trust.to_dict(),
            "projection_version": self.projection_version,
            "ledger_projection_version": self.ledger_projection_version,
            "valuation": self.valuation.to_dict(),
            "attention": [item.to_dict() for item in self.attention],
            "details": list(self.details),
        }


def build_realized_equity_series(
    rows: Sequence[ReconciledPaperTrade],
) -> tuple[EquityPoint, ...]:
    """Cumulative realized net P/L over settled trades, in close order.

    Only settled rows participate: an unverified figure must never move the equity
    curve, because doing so would present indicative economics as realized.
    A row whose close instant is unknown cannot be placed on a time series and is
    excluded rather than assigned an invented timestamp.
    """
    settled = [
        row
        for row in rows
        if row.is_settled and row.last_exit_fill_at is not None
    ]
    settled.sort(key=lambda row: (row.last_exit_fill_at, row.paper_trade_id))

    points: list[EquityPoint] = []
    cumulative = 0.0
    peak = 0.0
    for row in settled:
        cumulative += row.net_pnl
        peak = max(peak, cumulative)
        points.append(
            EquityPoint(
                at=row.last_exit_fill_at,
                realized_net_pnl=row.net_pnl,
                cumulative_net_pnl=cumulative,
                peak_cumulative_net_pnl=peak,
                drawdown_quote_currency=max(0.0, peak - cumulative),
            )
        )
    return tuple(points)


def build_drawdown_summary(
    points: Sequence[EquityPoint],
    *,
    starting_equity: float | None = None,
) -> DrawdownSummary:
    """Realized-equity drawdown, with explicit marked-valuation absence.

    ``starting_equity`` is optional and only affects the *percentage* form. The
    absolute drawdown in quote currency is always reported, because it needs no
    assumed base and therefore cannot be overstated by a wrong one.
    """
    if not points:
        return DrawdownSummary(
            realized_max_drawdown_quote_currency=0.0,
            realized_current_drawdown_quote_currency=0.0,
            realized_closed_trades=0,
        )

    max_drawdown = max(point.drawdown_quote_currency for point in points)
    current = points[-1].drawdown_quote_currency

    max_pct: float | None = None
    if starting_equity is not None and starting_equity > _TOLERANCE:
        worst = 0.0
        for point in points:
            # Equity base at this point is the running peak plus the starting
            # capital; a peak at or below zero has no percentage meaning.
            base = starting_equity + point.peak_cumulative_net_pnl
            if base > _TOLERANCE:
                worst = max(
                    worst, (point.drawdown_quote_currency / base) * 100.0
                )
        max_pct = worst

    # Time underwater: total span each exit spent below the running peak.
    underwater = 0.0
    trough_at: datetime | None = None
    recovery: float | None = None
    previous: EquityPoint | None = None
    for point in points:
        if previous is not None and previous.drawdown_quote_currency > _TOLERANCE:
            underwater += (point.at - previous.at).total_seconds()
        if point.drawdown_quote_currency > _TOLERANCE:
            trough_at = point.at if trough_at is None else trough_at
        elif trough_at is not None and point.drawdown_quote_currency <= _TOLERANCE:
            if recovery is None:
                recovery = (point.at - trough_at).total_seconds()
            trough_at = None
        previous = point

    return DrawdownSummary(
        realized_max_drawdown_quote_currency=max_drawdown,
        realized_current_drawdown_quote_currency=current,
        realized_max_drawdown_pct=max_pct,
        realized_time_underwater_seconds=underwater,
        realized_recovery_duration_seconds=recovery,
        realized_closed_trades=len(points),
        # Deliberately None: no canonical mark exists for an open position, so a
        # marked figure cannot be produced and must not be fabricated as zero.
        marked_current_drawdown=None,
        marked_max_drawdown=None,
        valuation=ValuationPosture(),
    )


def build_strategy_contribution(
    rows: Sequence[ReconciledPaperTrade],
) -> tuple[StrategyContribution, ...]:
    """Per-policy-version contribution over settled trades only.

    Ordered by realized net P/L for readability, **not** as a ranking verdict:
    with no registered interval estimator the ordering carries no statistical
    authority, which is why every entry states ``INSUFFICIENT_EVIDENCE``.
    """
    grouped: dict[str, list[ReconciledPaperTrade]] = {}
    for row in rows:
        if not row.is_settled:
            continue
        grouped.setdefault(str(row.policy_version or "UNKNOWN"), []).append(row)

    contributions: list[StrategyContribution] = []
    for policy_version, members in grouped.items():
        net = sum(member.net_pnl for member in members)
        points = build_realized_equity_series(members)
        contributions.append(
            StrategyContribution(
                policy_version=policy_version,
                policy_fingerprint=next(
                    (
                        member.policy_fingerprint
                        for member in members
                        if member.policy_fingerprint
                    ),
                    None,
                ),
                settled_trades=len(members),
                realized_net_pnl=net,
                expectancy_quote_currency=net / len(members) if members else None,
                winning_trades=sum(
                    1
                    for member in members
                    if member.economic_result is EconomicResult.WIN
                ),
                losing_trades=sum(
                    1
                    for member in members
                    if member.economic_result is EconomicResult.LOSS
                ),
                breakeven_trades=sum(
                    1
                    for member in members
                    if member.economic_result is EconomicResult.BREAKEVEN
                ),
                realized_max_drawdown_quote_currency=(
                    max(
                        (point.drawdown_quote_currency for point in points),
                        default=0.0,
                    )
                ),
                uncertainty=Uncertainty.INSUFFICIENT_EVIDENCE,
                uncertainty_reasons=(NO_INTERVAL_ESTIMATOR,),
            )
        )
    contributions.sort(key=lambda item: (-item.realized_net_pnl, item.policy_version))
    return tuple(contributions)


def _attention_for(
    rows: Sequence[ReconciledPaperTrade], *, now: datetime
) -> tuple[AttentionItem, ...]:
    items: list[AttentionItem] = []

    unverified = [
        row
        for row in rows
        if row.lifecycle_status is LifecycleStatus.UNRESOLVED
        and row.remaining_quantity <= _TOLERANCE
        and not row.net_pnl_definitive
    ]
    for row in unverified:
        # An age is only meaningful when the close instant is provably in the past.
        # A negative age would mean the exit followed the reading clock, which is
        # clock skew or a wrong `now`, not evidence of anything - so it is withheld.
        age: float | None = None
        if row.last_exit_fill_at is not None:
            delta = (now - row.last_exit_fill_at).total_seconds()
            age = delta if delta >= 0.0 else None
        items.append(
            AttentionItem(
                code="FLAT_BUT_UNVERIFIED",
                severity="HIGH",
                summary=(
                    "Position is flat but its economics are not canonically "
                    "verified, so it is not a settled result."
                ),
                scope=str(row.paper_trade_id),
                state=str(row.lifecycle_status.value),
                age_seconds=age,
                action_required="reconcile the trade to FINAL_VERIFIED",
                authority_affected=False,
                evidence_refs=tuple(row.event_ids),
            )
        )

    unresolved_evidence = [
        row
        for row in rows
        if row.terminal_reconciliation_state == "UNRESOLVED_EVIDENCE"
    ]
    for row in unresolved_evidence:
        items.append(
            AttentionItem(
                code="UNRESOLVED_EVIDENCE",
                severity="HIGH",
                summary="Reconciliation for this trade is unresolved.",
                scope=str(row.paper_trade_id),
                state="UNRESOLVED_EVIDENCE",
                action_required="investigate missing or conflicting evidence",
                authority_affected=False,
                evidence_refs=tuple(row.event_ids),
            )
        )

    stuck = [
        row
        for row in rows
        if row.lifecycle_status is LifecycleStatus.OPEN and not row.protection_state
    ]
    for row in stuck:
        items.append(
            AttentionItem(
                code="OPEN_WITHOUT_PROTECTION",
                severity="HIGH",
                summary="An open position has no committed protection state.",
                scope=str(row.paper_trade_id),
                state="OPEN",
                action_required="verify protection lifecycle",
                authority_affected=False,
                evidence_refs=tuple(row.event_ids),
            )
        )

    return tuple(items)


def build_currency_portfolio(
    quote_currency: str,
    rows: Sequence[ReconciledPaperTrade],
    *,
    now: datetime,
    starting_equity: float | None = None,
    recent_limit: int = 10,
) -> CurrencyPortfolio:
    """Build one currency's portfolio. Rows must all be that currency."""
    members = tuple(rows)
    settled = tuple(row for row in members if row.is_settled)
    points = build_realized_equity_series(members)
    drawdown = build_drawdown_summary(points, starting_equity=starting_equity)

    open_rows = tuple(
        row
        for row in members
        if row.lifecycle_status is LifecycleStatus.OPEN
    )
    flat_unverified = tuple(
        row
        for row in members
        if row.lifecycle_status is LifecycleStatus.UNRESOLVED
    )

    realized_net = sum(row.net_pnl for row in settled)

    recent = sorted(
        settled,
        key=lambda row: (
            row.last_exit_fill_at or datetime.min.replace(tzinfo=now.tzinfo),
            row.paper_trade_id,
        ),
        reverse=True,
    )[:recent_limit]

    attention = _attention_for(members, now=now)

    return CurrencyPortfolio(
        quote_currency=quote_currency,
        equity_series=points,
        drawdown=drawdown,
        realized_net_pnl=realized_net,
        expectancy_quote_currency=(
            realized_net / len(settled) if settled else None
        ),
        gross_pnl=sum(row.gross_pnl for row in settled),
        execution_costs=sum(row.execution_costs for row in settled),
        open_positions=len(open_rows),
        reserved_capital=sum(row.reserved_capital for row in members),
        strategy_contribution=build_strategy_contribution(members),
        recent_settled_trades=tuple(recent),
        accountability=AccountabilitySummary(
            admitted=len(members),
            open_positions=len(open_rows),
            closed_settled=len(settled),
            flat_unverified=len(flat_unverified),
        ),
        current_watch=open_rows,
        attention=attention,
        uncertainty=Uncertainty.INSUFFICIENT_EVIDENCE,
        uncertainty_reasons=(NO_INTERVAL_ESTIMATOR,),
    )


def build_overview(
    ledger: PaperLedger,
    *,
    now: datetime,
    starting_equity: float | None = None,
    recent_limit: int = 10,
) -> CockpitOverview:
    """Build the owner overview from a ledger, keeping currencies separate."""
    grouped = ledger.by_quote_currency()
    portfolios = tuple(
        build_currency_portfolio(
            currency,
            rows,
            now=now,
            starting_equity=starting_equity,
            recent_limit=recent_limit,
        )
        for currency, rows in sorted(grouped.items())
    )

    # Global attention: any unavailability, plus per-currency incidents. Never a
    # single collapsed health score - the incidents carry their own codes.
    attention: list[AttentionItem] = []
    if not ledger.trust.is_healthy:
        attention.append(
            AttentionItem(
                code="EVIDENCE_UNAVAILABLE",
                severity="HIGH",
                summary=(
                    "Canonical evidence could not be authoritatively read, so "
                    "portfolio figures may be incomplete."
                ),
                scope="PORTFOLIO",
                state=ledger.trust.freshness.value,
                action_required="restore canonical read availability",
                authority_affected=False,
                evidence_refs=(),
            )
        )
    for portfolio in portfolios:
        attention.extend(portfolio.attention)

    return CockpitOverview(
        portfolios=portfolios,
        trust=ledger.trust,
        ledger_projection_version=ledger.projection_version,
        valuation=ValuationPosture(),
        attention=tuple(attention),
        details=ledger.details,
    )


__all__ = [
    "COCKPIT_PORTFOLIO_PROJECTION_VERSION",
    "NO_INTERVAL_ESTIMATOR",
    "NO_MARK_EVIDENCE",
    "AccountabilitySummary",
    "AttentionItem",
    "CockpitOverview",
    "CurrencyPortfolio",
    "DrawdownSummary",
    "EquityPoint",
    "StrategyContribution",
    "ValuationPosture",
    "build_currency_portfolio",
    "build_drawdown_summary",
    "build_overview",
    "build_realized_equity_series",
    "build_strategy_contribution",
]
