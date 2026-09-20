"""B/C-4B reconciled Paper-v2 economic ledger.

Turns committed canonical Paper-v2 facts into the single analytical ledger the
cockpit, reports and exports consume. It is a **derivation**, never an authority:
every economic value it presents either comes from the canonical writer's own
verified reconciliation or is derived from committed fills using the same
relationship the writer validates (``net = gross - recorded_costs``).

Why this module exists rather than dashboard SQL
------------------------------------------------

Canonical Paper-v2 evidence lives in the canonical SQLite store on the trading
host, not in the analytics plane, so no panel can compute this itself. Putting the
derivation here - read-only, testable, deterministic - is what keeps *one* metric
authority and stops a presentation layer from becoming a second source of P&L
truth. The frontend may format, filter and navigate; it may never recalculate.

Authoritative vs indicative economics
-------------------------------------

``FINAL_VERIFIED`` is the canonical writer's own statement that the economics are
reproducible from fills. Only then may a row be presented as a settled result:
``net_pnl_definitive`` is true, and the economic outcome (win/loss/breakeven) is
resolved. Before that the same numbers are shown as **indicative**, and the
economic outcome is ``UNRESOLVED`` rather than guessed. A flat-but-unverified trade
is explicitly not a closed trade.

Outcome dimensions stay separate
--------------------------------

Economic result, execution result, lifecycle status and exit mechanism are four
independent dimensions. Collapsing them produces the classic dashboard error of
treating "closed early" as an economic outcome, or a partial fill as a loss.
"Early close" is exposed as a *relationship to plan*, never as a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from app.opip.canonical.models import PaperV2Ledger, PaperV2LedgerEntry
from app.opip.cockpit.trust import (
    Completeness,
    CorrectionState,
    Fidelity,
    Freshness,
    TrustEnvelope,
    Uncertainty,
    from_read_status,
)

#: Version of this projection's semantics. Bumped whenever a derived definition
#: changes, so a stored or cached response can be recognised as predating a change.
COCKPIT_LEDGER_PROJECTION_VERSION = "cockpit-ledger-v1"

#: Comparison tolerance for quantity and money equality. Matches the canonical
#: fill/quantity tolerance the writer itself uses, so "flat" means the same thing
#: here as it does in the store rather than being re-decided locally.
_TOLERANCE = 1e-9

#: The one direction this slice models.
_LONG_ONLY = "LONG"


class EconomicResult(str, Enum):
    """The trade's economic outcome. Resolved only once economics are verified."""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    UNRESOLVED = "UNRESOLVED"


class ExecutionResult(str, Enum):
    """How completely the entry order was filled. Independent of economics."""

    NO_FILL = "NO_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"


class LifecycleStatus(str, Enum):
    """Where the trade is in its canonical lifecycle."""

    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNRESOLVED = "UNRESOLVED"


class ExitMechanism(str, Enum):
    """What caused an exit. Independent of whether the exit made or lost money."""

    TARGET = "TARGET"
    STOP = "STOP"
    TIME = "TIME"
    OTHER = "OTHER"
    NONE = "NONE"


class EconomicsSource(str, Enum):
    """Where a row's economics came from, so no number is ambiguous about origin."""

    CANONICAL_RECONCILIATION = "CANONICAL_RECONCILIATION"
    DERIVED_FROM_FILLS = "DERIVED_FROM_FILLS"


def _parse_instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def temporal_point(evidence: Any) -> datetime | None:
    """An EXACT instant, or ``None``.

    BOUNDED and UNKNOWN evidence deliberately yield ``None`` rather than a midpoint
    or a guess: inventing point precision the retained evidence does not support is
    the failure mode the frozen temporal contract exists to prevent.
    """
    if not isinstance(evidence, Mapping):
        return None
    if str(evidence.get("precision")) != "EXACT":
        return None
    return _parse_instant(evidence.get("occurred_at"))


def temporal_interval(evidence: Any) -> tuple[datetime, datetime] | None:
    """The defensible occurrence interval, from EXACT or BOUNDED evidence.

    A well-formed interval must run forwards. A BOUNDED window whose end precedes
    its start describes no real occurrence, so it is refused here rather than being
    returned for a caller to interpret: a reversed window would otherwise yield a
    latency that looks plausible while resting on impossible chronology.
    """
    if not isinstance(evidence, Mapping):
        return None
    precision = str(evidence.get("precision"))
    if precision == "EXACT":
        point = _parse_instant(evidence.get("occurred_at"))
        return (point, point) if point is not None else None
    if precision == "BOUNDED":
        start = _parse_instant(evidence.get("window_start"))
        end = _parse_instant(evidence.get("window_end"))
        if start is None or end is None or end < start:
            return None
        return (start, end)
    return None


def _instant_window(value: Any) -> tuple[datetime, datetime] | None:
    """An occurrence window from canonical temporal evidence *or* an ISO instant.

    A latency's start point is usually temporal evidence (a fill, a trigger), but the
    decision instant is persisted as a plain ISO-8601 string on the decision context.
    Both describe one provable moment, so both normalize to the same window form
    rather than forcing callers to reformat canonical facts.
    """
    if isinstance(value, Mapping):
        return temporal_interval(value)
    if isinstance(value, str):
        point = _parse_instant(value)
        return (point, point) if point is not None else None
    return None


def latency_interval(
    start: Any, end: Any
) -> tuple[float, float] | None:
    """A latency as a ``(low, high)`` seconds interval, or ``None``.

    Always an interval, never a bare point: the registered
    ``paper.entry_latency`` / ``paper.exit_latency`` metrics require
    ``SHOW_INTERVAL`` because latency can derive from bounded evidence, and
    collapsing that to a single number would invent precision.

    A latency is a non-negative duration. If the *latest* possible end still
    precedes the *earliest* possible start the evidence cannot describe a real
    duration at all, so this refuses rather than reporting a negative latency or
    silently clamping to zero.
    """
    start_window = _instant_window(start)
    end_window = _instant_window(end)
    if start_window is None or end_window is None:
        return None
    low = (end_window[0] - start_window[1]).total_seconds()
    high = (end_window[1] - start_window[0]).total_seconds()
    if high < low or high < 0.0:
        return None
    return (low, high)


def point_latency_seconds(start: Any, end: Any) -> float | None:
    """A latency as one number, **only** when both endpoints are EXACT.

    Used for quantities that must not be presented as a range (a holding duration
    compared against a declared horizon). Bounded or unknown evidence yields
    ``None`` rather than a midpoint, so no claim is made that the evidence cannot
    support.
    """
    start_point = temporal_point(start)
    end_point = temporal_point(end)
    if start_point is None or end_point is None:
        return None
    delta = (end_point - start_point).total_seconds()
    if delta < 0.0:
        return None
    return delta


@dataclass(frozen=True)
class ReconciledPaperTrade:
    """One Paper-v2 trade's reconciled analytical row (the Trade Detail record)."""

    # --- identity ---------------------------------------------------------
    paper_trade_id: str
    disposition_id: str | None = None
    decision_context_id: str | None = None
    reservation_id: str | None = None
    candidate_id: str | None = None
    episode_id: str | None = None
    cohort_id: str | None = None
    native_symbol: str | None = None
    quote_currency: str | None = None
    direction: str = _LONG_ONLY
    engine: str = "OPIP_PAPER_V2"
    instrument_version: str | None = None
    #: The strategy axis. There is no canonical strategy *name*; this version and
    #: fingerprint pair is what the decision context actually committed.
    policy_version: str | None = None
    policy_fingerprint: str | None = None

    # --- lifecycle --------------------------------------------------------
    lifecycle_status: LifecycleStatus = LifecycleStatus.PENDING
    disposition_at: datetime | None = None
    evaluation_at: datetime | None = None
    entry_intent_at: datetime | None = None
    entry_attempt_at: datetime | None = None
    first_entry_fill_at: datetime | None = None
    last_exit_fill_at: datetime | None = None
    holding_seconds: float | None = None
    holding_seconds_interval: tuple[float, float] | None = None
    entry_latency_seconds: tuple[float, float] | None = None
    exit_latency_seconds: tuple[float, float] | None = None

    # --- economics --------------------------------------------------------
    requested_entry_quantity: float | None = None
    entry_quantity: float = 0.0
    exited_quantity: float = 0.0
    remaining_quantity: float = 0.0
    entry_notional: float | None = None
    exit_notional: float | None = None
    entry_price_vwap: float | None = None
    exit_price_vwap: float | None = None
    gross_pnl: float = 0.0
    fee_cost: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    other_cost: float = 0.0
    execution_costs: float = 0.0
    net_pnl: float = 0.0
    reserved_capital: float = 0.0
    execution_model_version: str | None = None
    economic_model_version: str | None = None
    economics_source: EconomicsSource = EconomicsSource.DERIVED_FROM_FILLS
    #: True only when canonical FINAL_VERIFIED proves the economics reproducible.
    net_pnl_definitive: bool = False

    # --- outcome dimensions (deliberately independent) --------------------
    economic_result: EconomicResult = EconomicResult.UNRESOLVED
    execution_result: ExecutionResult = ExecutionResult.NO_FILL
    exit_mechanism: ExitMechanism = ExitMechanism.NONE
    exit_mechanisms: tuple[ExitMechanism, ...] = ()
    terminal_reconciliation_state: str | None = None

    # --- planned vs observed ---------------------------------------------
    plan_seq: int | None = None
    planned_stop_price: float | None = None
    planned_targets: tuple[Mapping[str, Any], ...] = ()
    planned_max_hold_seconds: int | None = None
    protection_state: str | None = None
    #: A relation to plan/horizon, explicitly not an economic outcome.
    early_close: bool | None = None

    # --- audit ------------------------------------------------------------
    event_ids: tuple[str, ...] = ()

    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )

    @property
    def is_settled(self) -> bool:
        """A settled row: terminal lifecycle with canonically verified economics."""
        return (
            self.lifecycle_status is LifecycleStatus.CLOSED
            and self.net_pnl_definitive
        )

    def to_dict(self) -> dict[str, Any]:
        def _dt(value: datetime | None) -> str | None:
            return value.isoformat().replace("+00:00", "Z") if value else None

        return {
            "paper_trade_id": self.paper_trade_id,
            "disposition_id": self.disposition_id,
            "decision_context_id": self.decision_context_id,
            "reservation_id": self.reservation_id,
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "cohort_id": self.cohort_id,
            "native_symbol": self.native_symbol,
            "quote_currency": self.quote_currency,
            "direction": self.direction,
            "engine": self.engine,
            "instrument_version": self.instrument_version,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "lifecycle_status": self.lifecycle_status.value,
            "disposition_at": _dt(self.disposition_at),
            "evaluation_at": _dt(self.evaluation_at),
            "entry_intent_at": _dt(self.entry_intent_at),
            "entry_attempt_at": _dt(self.entry_attempt_at),
            "first_entry_fill_at": _dt(self.first_entry_fill_at),
            "last_exit_fill_at": _dt(self.last_exit_fill_at),
            "holding_seconds": self.holding_seconds,
            "holding_seconds_interval": self.holding_seconds_interval,
            "entry_latency_seconds": self.entry_latency_seconds,
            "exit_latency_seconds": self.exit_latency_seconds,
            "requested_entry_quantity": self.requested_entry_quantity,
            "entry_quantity": self.entry_quantity,
            "exited_quantity": self.exited_quantity,
            "remaining_quantity": self.remaining_quantity,
            "entry_notional": self.entry_notional,
            "exit_notional": self.exit_notional,
            "entry_price_vwap": self.entry_price_vwap,
            "exit_price_vwap": self.exit_price_vwap,
            "gross_pnl": self.gross_pnl,
            "fee_cost": self.fee_cost,
            "spread_cost": self.spread_cost,
            "slippage_cost": self.slippage_cost,
            "other_cost": self.other_cost,
            "execution_costs": self.execution_costs,
            "net_pnl": self.net_pnl,
            "reserved_capital": self.reserved_capital,
            "execution_model_version": self.execution_model_version,
            "economic_model_version": self.economic_model_version,
            "economics_source": self.economics_source.value,
            "net_pnl_definitive": self.net_pnl_definitive,
            "economic_result": self.economic_result.value,
            "execution_result": self.execution_result.value,
            "exit_mechanism": self.exit_mechanism.value,
            "exit_mechanisms": [item.value for item in self.exit_mechanisms],
            "terminal_reconciliation_state": self.terminal_reconciliation_state,
            "plan_seq": self.plan_seq,
            "planned_stop_price": self.planned_stop_price,
            "planned_targets": [dict(target) for target in self.planned_targets],
            "planned_max_hold_seconds": self.planned_max_hold_seconds,
            "protection_state": self.protection_state,
            "early_close": self.early_close,
            "event_ids": list(self.event_ids),
            "trust": self.trust.to_dict(),
        }


@dataclass(frozen=True)
class PaperLedger:
    """The full reconciled ledger envelope for one read.

    ``trust`` describes the whole response. When the canonical store cannot be read
    the ledger is ``UNAVAILABLE`` with ``UNKNOWN`` completeness and an explicit
    reason - never an empty, apparently healthy ledger.
    """

    entries: tuple[ReconciledPaperTrade, ...] = ()
    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )
    projection_version: str = COCKPIT_LEDGER_PROJECTION_VERSION
    details: tuple[str, ...] = ()

    def by_quote_currency(self) -> dict[str, tuple[ReconciledPaperTrade, ...]]:
        """Rows grouped by quote currency.

        USD and USDT are distinct portfolios and are never summed together, so the
        grouping is the only supported way to aggregate across rows.
        """
        grouped: dict[str, list[ReconciledPaperTrade]] = {}
        for entry in self.entries:
            key = str(entry.quote_currency or "UNKNOWN")
            grouped.setdefault(key, []).append(entry)
        return {key: tuple(rows) for key, rows in grouped.items()}

    def settled(self) -> tuple[ReconciledPaperTrade, ...]:
        """Only rows whose economics are canonically verified."""
        return tuple(entry for entry in self.entries if entry.is_settled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "trust": self.trust.to_dict(),
            "projection_version": self.projection_version,
            "details": list(self.details),
        }


def _sum_field(fills: Iterable[Mapping[str, Any]], key: str) -> float:
    return sum(float(fill.get(key) or 0.0) for fill in fills)


def _vwap(fills: Iterable[Mapping[str, Any]]) -> float | None:
    fills = list(fills)
    quantity = sum(float(fill.get("quantity") or 0.0) for fill in fills)
    if quantity <= _TOLERANCE:
        return None
    return (
        sum(
            float(fill.get("quantity") or 0.0) * float(fill.get("price") or 0.0)
            for fill in fills
        )
        / quantity
    )


def _mechanism_for(trigger_type: str) -> ExitMechanism:
    normalized = str(trigger_type or "").strip().upper()
    if normalized in {"TARGET", "STOP", "TIME"}:
        return ExitMechanism(normalized)
    return ExitMechanism.OTHER


def build_trade_row(entry: PaperV2LedgerEntry) -> ReconciledPaperTrade:
    """Derive one reconciled analytical row from committed canonical facts."""
    reconciliation = entry.latest_reconciliation or {}
    terminal_state = (
        str(reconciliation.get("terminal_reconciliation_state"))
        if isinstance(reconciliation, Mapping) and reconciliation
        else None
    )

    # Economics: prefer the canonical writer's own verified statement. It is the
    # authority; deriving independently would create a second P&L truth.
    definitive = bool(entry.final_verified)
    if reconciliation:
        gross = float(reconciliation.get("realized_gross_pnl") or 0.0)
        costs = float(reconciliation.get("recorded_execution_costs") or 0.0)
        net = float(reconciliation.get("realized_net_pnl") or 0.0)
        source = EconomicsSource.CANONICAL_RECONCILIATION
    else:
        all_fills = list(entry.entry_fills) + list(entry.exit_fills)
        gross = sum(
            float(fill.get("quantity") or 0.0)
            * float(fill.get("price") or 0.0)
            * (1.0 if str(fill.get("side")) == "SELL" else -1.0)
            for fill in all_fills
        )
        costs = sum(
            float(fill.get("fee_cost") or 0.0)
            + float(fill.get("spread_cost") or 0.0)
            + float(fill.get("slippage_cost") or 0.0)
            + float(fill.get("other_supported_cost") or 0.0)
            for fill in all_fills
        )
        net = gross - costs
        source = EconomicsSource.DERIVED_FROM_FILLS

    # Outcome dimensions, each derived from its own evidence and kept separate.
    requested = None
    if isinstance(entry.entry_order_intent, Mapping):
        raw_requested = entry.entry_order_intent.get("requested_quantity")
        if isinstance(raw_requested, (int, float)) and not isinstance(
            raw_requested, bool
        ):
            requested = float(raw_requested)

    if entry.entry_quantity <= _TOLERANCE:
        execution = ExecutionResult.NO_FILL
    elif requested is not None and entry.entry_quantity < requested - _TOLERANCE:
        execution = ExecutionResult.PARTIAL_FILL
    else:
        execution = ExecutionResult.FULL_FILL

    if entry.remaining_quantity > _TOLERANCE:
        lifecycle = LifecycleStatus.OPEN
    elif definitive:
        lifecycle = LifecycleStatus.CLOSED
    elif entry.exit_fills:
        # Flat, but the canonical writer has not yet verified the economics.
        # Presenting this as CLOSED would claim a settled result that no evidence
        # supports.
        lifecycle = LifecycleStatus.UNRESOLVED
    else:
        lifecycle = LifecycleStatus.PENDING

    mechanisms: tuple[ExitMechanism, ...] = tuple(
        dict.fromkeys(_mechanism_for(item) for item in entry.trigger_types)
    )
    # The last committed trigger is the one that produced the final exit action.
    primary = (
        _mechanism_for(entry.trigger_types[-1])
        if entry.trigger_types
        else ExitMechanism.NONE
    )
    if not entry.exit_fills and mechanisms == ():
        primary = ExitMechanism.NONE

    if not definitive:
        economic = EconomicResult.UNRESOLVED
    elif net > _TOLERANCE:
        economic = EconomicResult.WIN
    elif net < -_TOLERANCE:
        economic = EconomicResult.LOSS
    else:
        economic = EconomicResult.BREAKEVEN

    entry_fills = list(entry.entry_fills)
    exit_fills = list(entry.exit_fills)
    entry_vwap = _vwap(entry_fills)
    exit_vwap = _vwap(exit_fills)

    holding_interval = latency_interval(
        entry.first_entry_fill_time, entry.last_exit_fill_time
    )
    # A holding duration that is compared against the declared horizon must be a
    # proven instant-to-instant span. With bounded evidence the interval is
    # reported instead and no point claim is made.
    holding = point_latency_seconds(
        entry.first_entry_fill_time, entry.last_exit_fill_time
    )

    plan = entry.protection_plan or {}
    planned_targets: tuple[Mapping[str, Any], ...] = ()
    planned_stop = None
    planned_hold = None
    if isinstance(plan, Mapping) and plan:
        raw_targets = plan.get("targets")
        if isinstance(raw_targets, list):
            planned_targets = tuple(
                dict(target) for target in raw_targets if isinstance(target, Mapping)
            )
        raw_stop = plan.get("stop_price")
        if isinstance(raw_stop, (int, float)) and not isinstance(raw_stop, bool):
            planned_stop = float(raw_stop)
        raw_hold = plan.get("max_hold_seconds")
        if isinstance(raw_hold, int) and not isinstance(raw_hold, bool):
            planned_hold = int(raw_hold)

    # "Early close" is a relationship to the declared horizon, explicitly not an
    # economic outcome: a trade can close early at a profit or a loss.
    early_close: bool | None = None
    if (
        lifecycle is LifecycleStatus.CLOSED
        and holding is not None
        and planned_hold is not None
    ):
        early_close = holding < planned_hold - _TOLERANCE

    reasons: list[str] = []
    if not definitive:
        reasons.append("ECONOMICS_NOT_FINAL_VERIFIED")

    trust = TrustEnvelope(
        freshness=Freshness.LIVE,
        completeness=(
            Completeness.COMPLETE if definitive else Completeness.INCOMPLETE
        ),
        fidelity=Fidelity.NOT_EVALUATED,
        uncertainty=(
            Uncertainty.NONE if definitive else Uncertainty.INSUFFICIENT_EVIDENCE
        ),
        correction_state=CorrectionState.CURRENT,
        reasons=tuple(reasons),
    )

    return ReconciledPaperTrade(
        paper_trade_id=entry.paper_trade_id,
        disposition_id=entry.disposition_id,
        decision_context_id=entry.decision_context_id,
        reservation_id=entry.reservation_id,
        candidate_id=entry.candidate_id,
        episode_id=entry.episode_id,
        cohort_id=entry.cohort_id,
        native_symbol=entry.native_symbol,
        quote_currency=entry.quote_currency,
        direction=_LONG_ONLY,
        instrument_version=entry.instrument_version,
        policy_version=entry.policy_version,
        policy_fingerprint=entry.policy_fingerprint,
        lifecycle_status=lifecycle,
        disposition_at=temporal_point(entry.disposition_time),
        evaluation_at=_parse_instant(entry.evaluation_time),
        entry_intent_at=temporal_point(entry.entry_intent_time),
        entry_attempt_at=temporal_point(entry.entry_attempt_time),
        first_entry_fill_at=temporal_point(entry.first_entry_fill_time),
        last_exit_fill_at=temporal_point(entry.last_exit_fill_time),
        holding_seconds=holding,
        holding_seconds_interval=holding_interval,
        entry_latency_seconds=latency_interval(
            # The registered ``paper.entry_latency`` is
            # ``first_entry_fill_time - decision_time``, so the measurement starts at
            # the decision context's evaluation instant rather than at the entry
            # order intent. Starting later would silently omit the decision-to-order
            # interval and report a flattering latency under the registered name.
            entry.evaluation_time,
            entry.first_entry_fill_time,
        ),
        exit_latency_seconds=latency_interval(
            (
                entry.triggers[-1].get("trigger_time")
                if entry.triggers
                else None
            ),
            entry.last_exit_fill_time,
        ),
        requested_entry_quantity=requested,
        entry_quantity=float(entry.entry_quantity),
        exited_quantity=float(entry.exited_quantity),
        remaining_quantity=float(entry.remaining_quantity),
        entry_notional=(
            float(entry.entry_quantity) * entry_vwap
            if entry_vwap is not None
            else None
        ),
        exit_notional=(
            float(entry.exited_quantity) * exit_vwap
            if exit_vwap is not None
            else None
        ),
        entry_price_vwap=entry_vwap,
        exit_price_vwap=exit_vwap,
        gross_pnl=gross,
        fee_cost=_sum_field(list(entry_fills) + list(exit_fills), "fee_cost"),
        spread_cost=_sum_field(list(entry_fills) + list(exit_fills), "spread_cost"),
        slippage_cost=_sum_field(
            list(entry_fills) + list(exit_fills), "slippage_cost"
        ),
        other_cost=_sum_field(
            list(entry_fills) + list(exit_fills), "other_supported_cost"
        ),
        execution_costs=costs,
        net_pnl=net,
        reserved_capital=float(entry.reserved_capital),
        execution_model_version=entry.execution_model_version,
        economic_model_version=(
            entry.economic_model_version
            # Fall back to the economic model version the fills themselves
            # declare, so a row is never silently missing the model that priced it.
            or (
                str(entry_fills[0].get("economic_model_version"))
                if entry_fills and entry_fills[0].get("economic_model_version")
                else None
            )
        ),
        economics_source=source,
        net_pnl_definitive=definitive,
        economic_result=economic,
        execution_result=execution,
        exit_mechanism=primary,
        exit_mechanisms=mechanisms,
        terminal_reconciliation_state=terminal_state,
        plan_seq=entry.plan_seq,
        planned_stop_price=planned_stop,
        planned_targets=planned_targets,
        planned_max_hold_seconds=planned_hold,
        protection_state=entry.protection_state,
        early_close=early_close,
        event_ids=tuple(entry.event_ids),
        trust=trust,
    )


def build_ledger(ledger: PaperV2Ledger) -> PaperLedger:
    """Build the analytical ledger envelope from a canonical ledger read.

    A read that is not ``OK`` yields an ``UNAVAILABLE`` envelope with an explicit
    reason and **no** entries. It never yields an empty, healthy-looking ledger,
    because "the store is unreadable" and "there are no trades" are different facts
    with very different operator consequences.
    """
    if not isinstance(ledger, PaperV2Ledger):
        raise TypeError("ledger must be a PaperV2Ledger")
    trust = from_read_status(
        ledger.status,
        reason=ledger.error_code or ledger.detail or None,
    )
    if ledger.status != "OK":
        return PaperLedger(
            entries=(),
            trust=trust,
            details=(
                f"canonical ledger unavailable: {ledger.error_code or ledger.status}",
            ),
        )
    rows = tuple(build_trade_row(entry) for entry in ledger.entries)
    return PaperLedger(entries=rows, trust=trust)


def read_paper_ledger(client: Any) -> PaperLedger:
    """Read the canonical ledger and build the analytical envelope.

    Fail-soft in the sense that an unreadable store produces a typed
    ``UNAVAILABLE`` envelope rather than raising into a caller, but never
    fail-*open*: the failure is always visible in ``trust``.
    """
    try:
        ledger = client.get_paper_v2_ledger()
    except Exception as exc:  # noqa: BLE001 - an unreadable store is reported
        from app.opip.cockpit.trust import unavailable

        return PaperLedger(
            entries=(),
            trust=unavailable(f"LEDGER_READ_FAILED:{type(exc).__name__}"),
            details=("canonical ledger read failed",),
        )
    return build_ledger(ledger)


__all__ = [
    "COCKPIT_LEDGER_PROJECTION_VERSION",
    "EconomicResult",
    "EconomicsSource",
    "ExecutionResult",
    "ExitMechanism",
    "LifecycleStatus",
    "PaperLedger",
    "ReconciledPaperTrade",
    "build_ledger",
    "build_trade_row",
    "latency_interval",
    "point_latency_seconds",
    "read_paper_ledger",
    "temporal_interval",
    "temporal_point",
]
