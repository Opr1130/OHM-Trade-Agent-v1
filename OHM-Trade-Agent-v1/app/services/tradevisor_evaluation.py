"""Tradevisor V2 evaluation: external evidence into an O'Pip-owned decision.

Tradevisor proposes; O'Pip decides. This module answers exactly one question -
"does this Tradevisor BUY correspond to a defensibly tradable opportunity right
now?" - and it answers it using the components that already own each part of
the answer:

============================  =========================================
Concern                       Existing owner reused here
============================  =========================================
Native market evidence        app.scanner.market_scanner.analyze_symbol
Native regime                 MarketSnapshot.trend (1H EMA stack)
Entry/stop/target geometry    app.opip.execution_geometry (via
                              app.services.entry_exit_advisor)
Target attainability (F5)     app.services.target_attainability
Economics after costs (F5)    app.services.economic_quality_gate
Capital planning (F7)         app.services.capital_allocation
Exposure capacity             app.services.portfolio_risk
============================  =========================================

What is NOT here
-----------------
No geometry formula, no risk model, no allocation policy, no selector, no
order path. A Tradevisor-only symbol can never be admitted to native
selection: this module reads a snapshot for a symbol the operator already
mapped and returns a decision, and nothing it returns can place, confirm,
cancel or size an order.

SELL semantics
--------------
A SELL alert is bearish/exit evidence only. It never becomes a spot short,
never closes a funded position, and never suppresses a protective alert.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.scanner.market_scanner import analyze_symbol
from app.scanner.models import MarketSnapshot
from app.services.capital_allocation import CapitalAllocation, recommend_capital
from app.services.capital_efficiency_ranking import MIN_EXECUTABLE_NOTIONAL_USD
from app.services.economic_quality_gate import (
    PRODUCTION_MAX_CAPITAL_FRACTION,
    EconomicGateResult,
    evaluate_economic_quality,
)
from app.services.entry_exit_advisor import EntryExitPlan, build_entry_exit_plan
from app.services.portfolio_risk import evaluate_portfolio_risk
from app.services.target_attainability import (
    TargetAttainabilityResult,
    evaluate_target_attainability,
)
from app.services.tradevisor_ingress import TradevisorSignal
from app.services.tradevisor_market import attach_execution_evidence
from app.services.tradevisor_native_context import build_native_context

#: Source attribution. A measurement/policy identifier, never an authority.
STRATEGY_FAMILY = "TRADEVISOR_SWING_V1"

DECISION_ENTER_NOW = "ENTER_NOW"
DECISION_SET_LIMIT_ENTRY = "SET_LIMIT_ENTRY"
DECISION_WATCH = "WATCH"
DECISION_NO_TRADE = "NO_TRADE"
DECISION_EXPIRED = "EXPIRED"

#: Only these two are actionable entry recommendations.
ACTIONABLE_DECISIONS = frozenset({DECISION_ENTER_NOW, DECISION_SET_LIMIT_ENTRY})

HORIZON_TACTICAL = "TACTICAL"
HORIZON_SWING = "SWING"
HORIZON_POSITION = "POSITION"

#: The R4 kernel only supports these two levels; production iterates them in
#: this order and keeps the first that qualifies.
RISK_LEVELS = ("low", "medium")
#: Entry styles that mean "wait for a better price", i.e. a limit entry.
LIMIT_ENTRY_STYLES = frozenset({"wait_for_pullback", "wait_for_rebound"})

#: Horizon thresholds on native ATR%. Documented Tradevisor-scoped policy: the
#: baseline tree has no general O'Pip horizon classifier, so a 1H Tradevisor
#: signal is explicitly NOT assumed to be a SWING trade.
TACTICAL_MIN_ATR_PCT = 4.0
SWING_MIN_ATR_PCT = 1.5

DEFAULT_ACCOUNT_EQUITY = 1_000.0


@dataclass(frozen=True)
class TradevisorDecision:
    """One deterministic decision plus the evidence it was made from.

    ``actionable`` is the single gate the notifier relies on; it is derived from
    ``decision`` and never set independently.
    """

    decision: str
    reason: str
    rejection_code: str | None
    dedup_id: str
    signal: str
    instrument: str
    market: str
    source_exchange: str
    source_symbol: str
    alert_config_version: str
    timeframe_minutes: int
    strategy_family: str
    horizon: str | None
    market_regime: str | None
    current_price: float | None
    plan: EntryExitPlan | None
    economics: EconomicGateResult | None
    attainability: TargetAttainabilityResult | None
    allocation: CapitalAllocation | None
    portfolio: dict[str, Any] | None
    evaluated_at: datetime
    expires_at: datetime | None
    evidence: dict[str, Any]

    @property
    def actionable(self) -> bool:
        return self.decision in ACTIONABLE_DECISIONS

    def as_record(self) -> dict[str, Any]:
        """Frozen decision-time evidence for learning and audit.

        Only values known at decision time appear here. No realized price,
        outcome or fill is ever inferred, so a later replay from this record
        cannot leak the future.
        """
        return {
            "schema_version": "tradevisor-decision-v1",
            "strategy_family": self.strategy_family,
            "decision": self.decision,
            "reason": self.reason,
            "rejection_code": self.rejection_code,
            "dedup_id": self.dedup_id,
            "signal": self.signal,
            "instrument": self.instrument,
            "market": self.market,
            "source_exchange": self.source_exchange,
            "source_symbol": self.source_symbol,
            "alert_config_version": self.alert_config_version,
            "timeframe_minutes": self.timeframe_minutes,
            "horizon": self.horizon,
            "market_regime": self.market_regime,
            "current_price": self.current_price,
            "evaluated_at": self.evaluated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "plan": asdict(self.plan) if self.plan is not None else None,
            "economics": asdict(self.economics) if self.economics is not None else None,
            "attainability": (
                asdict(self.attainability) if self.attainability is not None else None
            ),
            "allocation": (
                self.allocation.as_dict() if self.allocation is not None else None
            ),
            "portfolio": self.portfolio,
            "evidence": self.evidence,
            # Explicit non-authority statements, so no downstream consumer can
            # read a decision record as a permission.
            "trade_authority_changed": False,
            "execution_authority": False,
            "admission_authority": False,
            "measurement_only": True,
        }


def _evaluate_buy_plans(
    signal: TradevisorSignal,
    *,
    snapshot: MarketSnapshot,
    evaluated_at: datetime,
    price: float,
    regime: str | None,
    horizon: str,
    equity: float,
    evidence: dict[str, Any],
    risk_per_trade_pct: float,
    expiry_minutes: int,
    active_trades: list[Any] | None,
) -> TradevisorDecision:
    """Run the existing F5/F7/R4 sequence over the source BUY evidence."""
    attempts: list[dict[str, Any]] = []
    accepted: dict[str, Any] | None = None
    last_code = "NO_VALID_ENTRY_GEOMETRY"
    last_reason = "No risk level produced a usable entry geometry."

    for risk_level in RISK_LEVELS:
        plan = build_entry_exit_plan(snapshot, risk_level, direction="LONG")
        attempt: dict[str, Any] = {
            "risk_level": risk_level,
            "valid_now": bool(plan.valid_now),
            "entry_style": plan.entry_style,
            "entry_low": plan.entry_low,
            "entry_high": plan.entry_high,
            "stop_price": plan.stop_price,
            "target_1": plan.target_1,
            "target_2": plan.target_2,
        }

        if not plan.valid_now and plan.entry_style not in LIMIT_ENTRY_STYLES:
            attempt.update(
                qualified=False,
                rejection_code="NO_VALID_ENTRY_GEOMETRY",
                rejection_reason=plan.reason,
            )
            attempts.append(attempt)
            last_code = "NO_VALID_ENTRY_GEOMETRY"
            last_reason = f"R4 geometry: {plan.reason}"
            continue

        if not _geometry_is_sane(plan):
            attempt.update(
                qualified=False,
                rejection_code="INVALID_GEOMETRY",
                rejection_reason="stop/target geometry is not a valid LONG plan",
            )
            attempts.append(attempt)
            last_code = "INVALID_GEOMETRY"
            last_reason = "R4 geometry produced an invalid stop/target relationship."
            continue

        attainability = evaluate_target_attainability(plan, snapshot)
        economics = evaluate_economic_quality(
            plan,
            equity,
            max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
        )
        attempt.update(
            attainability_qualified=bool(attainability.qualified),
            attainability_score=int(attainability.attainability_score),
            economic_qualified=bool(economics.qualified),
            economic_reason=economics.rejection_reason,
            stop_pct=economics.stop_pct,
            target_2_move_pct=economics.target_2_move_pct,
            reward_to_risk_2=plan.reward_to_risk_2,
        )

        if not attainability.qualified:
            attempt.update(
                qualified=False,
                rejection_code="TARGET_ATTAINABILITY_REJECT",
                rejection_reason="; ".join(attainability.rejection_reasons)
                or "target attainability rejected",
            )
            attempts.append(attempt)
            last_code = "TARGET_ATTAINABILITY_REJECT"
            last_reason = str(attempt["rejection_reason"])
            continue

        if not economics.qualified:
            attempt.update(
                qualified=False,
                rejection_code="ECONOMIC_REJECT",
                rejection_reason=economics.rejection_reason
                or "economic quality rejected",
            )
            attempts.append(attempt)
            last_code = "ECONOMIC_REJECT"
            last_reason = str(attempt["rejection_reason"])
            continue

        attempt["qualified"] = True
        attempts.append(attempt)
        accepted = {
            "risk_level": risk_level,
            "plan": plan,
            "attainability": attainability,
            "economics": economics,
        }
        break

    evidence["risk_level_attempts"] = attempts

    if accepted is None:
        return _finalize(
            signal,
            now=evaluated_at,
            decision=DECISION_WATCH,
            code=last_code,
            reason=f"Not qualified for an entry: {last_reason}",
            horizon=horizon,
            regime=regime,
            price=price,
            evidence=evidence,
        )

    return _finalize_qualified_buy(
        signal,
        accepted=accepted,
        evaluated_at=evaluated_at,
        price=price,
        regime=regime,
        horizon=horizon,
        equity=equity,
        evidence=evidence,
        risk_per_trade_pct=risk_per_trade_pct,
        expiry_minutes=expiry_minutes,
        active_trades=active_trades,
    )


def classify_horizon(snapshot: MarketSnapshot) -> str:
    """Classify the intended holding horizon from native volatility evidence."""
    atr_pct = float(getattr(snapshot, "atr_pct", 0.0) or 0.0)
    if atr_pct >= TACTICAL_MIN_ATR_PCT:
        return HORIZON_TACTICAL
    if atr_pct >= SWING_MIN_ATR_PCT:
        return HORIZON_SWING
    return HORIZON_POSITION


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evaluation time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _resolve_snapshot(
    provider: Callable[[str], Any], market: str
) -> tuple[MarketSnapshot | None, str | None]:
    """Accept either a snapshot or the scanner's ``(status, snapshot, reason)``."""
    result = provider(market)
    if isinstance(result, tuple) and len(result) == 3:
        status, snapshot, reason = result
        if status != "ok" or snapshot is None:
            return None, str(reason or status)
        return snapshot, None
    if result is None:
        return None, "NO_SNAPSHOT"
    return result, None


def _finalize(
    signal: TradevisorSignal,
    *,
    now: datetime,
    decision: str,
    code: str | None,
    reason: str,
    horizon: str | None = None,
    regime: str | None = None,
    price: float | None = None,
    plan: EntryExitPlan | None = None,
    economics: EconomicGateResult | None = None,
    attainability: TargetAttainabilityResult | None = None,
    allocation: CapitalAllocation | None = None,
    portfolio: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> TradevisorDecision:
    return TradevisorDecision(
        decision=decision,
        reason=reason,
        rejection_code=code,
        dedup_id=signal.dedup_id,
        signal=signal.signal,
        instrument=signal.instrument,
        market=signal.market,
        source_exchange=signal.source_exchange,
        source_symbol=signal.source_symbol,
        alert_config_version=signal.alert_config_version,
        timeframe_minutes=signal.timeframe_minutes,
        strategy_family=STRATEGY_FAMILY,
        horizon=horizon,
        market_regime=regime,
        current_price=price,
        plan=plan,
        economics=economics,
        attainability=attainability,
        allocation=allocation,
        portfolio=portfolio,
        evaluated_at=now,
        expires_at=expires_at,
        evidence=dict(evidence or {}),
    )


def _base_evidence(
    signal: TradevisorSignal,
    *,
    equity: float,
    snapshot: MarketSnapshot | None,
    snapshot_error: str | None,
) -> dict[str, Any]:
    return {
        "source": signal.as_evidence(),
        # What the external alert claimed, and what O'Pip could verify about it.
        "provenance": dict(signal.provenance or {}),
        "timing": {
            "source_bar_opened_at": signal.bar_opened_at.isoformat(),
            "source_bar_closed_at": signal.bar_closed_at.isoformat(),
            "source_triggered_at": signal.triggered_at.isoformat(),
            "received_at": signal.received_at.isoformat(),
        },
        "strategy_family": STRATEGY_FAMILY,
        "account_equity": equity,
        "native_snapshot_status": "AVAILABLE" if snapshot is not None else "UNAVAILABLE",
        "native_snapshot_reason": snapshot_error,
        # Honest capability statements, not placeholders for missing values.
        "regime_basis": "NATIVE_1H_EMA_STACK",
        "regime_4h": "UNAVAILABLE",
        "f6_forecast_status": "UNAVAILABLE_NO_TRUSTED_MODEL",
        "measurement_only": True,
        "trade_authority_changed": False,
        "native_admission": "NOT_PERFORMED",
    }


def _executable_price(snapshot: MarketSnapshot) -> float:
    """The price O'Pip would actually transact near, on its own venue."""

    for candidate in (
        getattr(snapshot, "ticker_last", 0.0),
        getattr(snapshot, "last_price", 0.0),
    ):
        try:
            value = float(candidate or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _geometry_is_sane(plan: EntryExitPlan) -> bool:
    """Structural validation of an already-computed plan.

    Validation only: no price is calculated here. The R4 kernel remains the
    sole geometry owner; this rejects a plan whose numbers cannot describe a
    LONG trade rather than re-deriving them.
    """
    import math

    values = (
        plan.entry_low,
        plan.entry_high,
        plan.chase_limit,
        plan.stop_price,
        plan.target_1,
        plan.target_2,
    )
    if not all(math.isfinite(float(value)) and float(value) > 0 for value in values):
        return False
    if not plan.stop_price < plan.entry_low <= plan.entry_high:
        return False
    if not plan.entry_high < plan.target_1 < plan.target_2:
        return False
    if not plan.chase_limit >= plan.entry_high:
        return False
    return True


def _evaluate_sell(
    signal: TradevisorSignal,
    *,
    evaluated_at: datetime,
    snapshot: MarketSnapshot | None,
    regime: str | None,
    price: float | None,
    evidence: dict[str, Any],
) -> TradevisorDecision:
    """A SELL alert is bearish/exit evidence, never an order.

    O'Pip trades unleveraged crypto spot from this path, so a SELL cannot
    become a short. It also never closes or reduces a funded position: that
    stays with the existing protection owners.
    """
    evidence["sell_semantics"] = {
        "short_order": "NOT_PERMITTED",
        "auto_close_funded_position": "NOT_PERFORMED",
        "native_corroboration": (
            "BEARISH_REGIME" if regime == "BEARISH" else "NOT_BEARISH"
        )
        if snapshot is not None
        else "UNAVAILABLE",
    }
    if snapshot is None:
        return _finalize(
            signal,
            now=evaluated_at,
            decision=DECISION_WATCH,
            code="SELL_EVIDENCE_ONLY_NO_NATIVE_DATA",
            reason=(
                "Tradevisor SELL recorded as bearish evidence only; native market "
                "data was unavailable, so it is not corroborated. No short is "
                "opened and no funded position is closed."
            ),
            regime=regime,
            price=price,
            evidence=evidence,
        )
    corroborated = regime == "BEARISH"
    return _finalize(
        signal,
        now=evaluated_at,
        decision=DECISION_WATCH,
        code="SELL_CORROBORATED_BEARISH" if corroborated else "SELL_UNCONFIRMED_BY_NATIVE",
        reason=(
            "Tradevisor SELL recorded as bearish/exit evidence only"
            + (
                "; native 1H structure is also bearish"
                if corroborated
                else "; native 1H structure is not bearish, so the signal is not corroborated"
            )
            + ". No short is opened and no funded position is closed."
        ),
        regime=regime,
        price=price,
        evidence=evidence,
    )


def evaluate_signal(
    signal: TradevisorSignal,
    *,
    now: datetime | None = None,
    settings: Any | None = None,
    snapshot_provider: Callable[[str], Any] | None = None,
    execution_provider: Callable[..., Any] | None = None,
    account_equity: float | None = None,
    active_trades: list[Any] | None = None,
    btc_context_provider: Callable[[str], Any] | None = None,
) -> TradevisorDecision:
    """Evaluate one validated Tradevisor signal against native O'Pip evidence.

    ``active_trades=None`` means exposure capacity was NOT evaluated; that is
    recorded explicitly and the recommendation is labelled
    ``EXPOSURE_NOT_EVALUATED`` rather than being treated as clear.
    """
    from app.core.config import get_settings  # local: keeps the import surface flat

    resolved = settings if settings is not None else get_settings()
    evaluated_at = _utc(now or datetime.now(timezone.utc))
    provider = snapshot_provider or analyze_symbol
    equity = float(
        account_equity
        if account_equity is not None
        else getattr(resolved, "account_equity", DEFAULT_ACCOUNT_EQUITY)
    )
    max_divergence = float(
        getattr(resolved, "opip_tradevisor_max_price_divergence_pct", 1.0)
    )
    expiry_minutes = int(
        getattr(resolved, "opip_tradevisor_actionable_expiry_minutes", 90)
    )
    risk_per_trade_pct = float(getattr(resolved, "risk_per_trade_pct", 0.75))

    snapshot, snapshot_error = _resolve_snapshot(provider, signal.market)
    evidence = _base_evidence(
        signal, equity=equity, snapshot=snapshot, snapshot_error=snapshot_error
    )
    regime = (
        (str(getattr(snapshot, "trend", "") or "").upper() or None)
        if snapshot is not None
        else None
    )
    price = _executable_price(snapshot) if snapshot is not None else None

    if signal.signal == "SELL":
        return _evaluate_sell(
            signal,
            evaluated_at=evaluated_at,
            snapshot=snapshot,
            regime=regime,
            price=price or None,
            evidence=evidence,
        )

    if snapshot is None:
        return _finalize(
            signal,
            now=evaluated_at,
            decision=DECISION_NO_TRADE,
            code="MARKET_DATA_UNAVAILABLE",
            reason=(
                "Native market evidence for the mapped execution market was "
                f"unavailable ({snapshot_error or 'unknown reason'}), so no "
                "entry can be validated."
            ),
            regime=regime,
            price=None,
            evidence=evidence,
        )

    if not price:
        return _finalize(
            signal,
            now=evaluated_at,
            decision=DECISION_NO_TRADE,
            code="MARKET_PRICE_UNAVAILABLE",
            reason="Native market evidence carried no usable executable price.",
            regime=regime,
            evidence=evidence,
        )

    divergence = abs(float(signal.signal_price) - price) / price * 100.0
    evidence["reference_price"] = price
    evidence["source_price"] = float(signal.signal_price)
    evidence["price_divergence_pct"] = round(divergence, 6)
    evidence["max_price_divergence_pct"] = max_divergence
    if divergence > max_divergence:
        # A source quote on another venue that disagrees with the execution
        # venue is not the same market: refuse instead of recommending a price
        # that does not exist where O'Pip would trade.
        return _finalize(
            signal,
            now=evaluated_at,
            decision=DECISION_NO_TRADE,
            code="CROSS_VENUE_DIVERGENCE",
            reason=(
                f"Tradevisor price {float(signal.signal_price):.8g} diverges "
                f"{divergence:.4f}% from the execution market {price:.8g} "
                f"(limit {max_divergence:.4f}%)."
            ),
            regime=regime,
            price=price,
            evidence=evidence,
        )

    horizon = classify_horizon(snapshot)
    evidence["horizon"] = horizon
    evidence["horizon_policy"] = {
        "tactical_min_atr_pct": TACTICAL_MIN_ATR_PCT,
        "swing_min_atr_pct": SWING_MIN_ATR_PCT,
        "atr_pct": float(getattr(snapshot, "atr_pct", 0.0) or 0.0),
        "note": "A 1H source signal is not automatically classified SWING.",
    }

    # Execution evidence (spread, depth, round-trip drag) is a hard input to the
    # existing attainability gate: without it the native gate rejects a LONG, so
    # the reason is reported explicitly instead of as a generic rejection.
    execution = attach_execution_evidence(
        snapshot, execution_provider=execution_provider, now=evaluated_at
    )
    if execution is None:
        evidence["execution_evidence"] = "UNAVAILABLE"
        return _finalize(
            signal,
            now=evaluated_at,
            decision=DECISION_WATCH,
            code="EXECUTION_EVIDENCE_UNAVAILABLE",
            reason=(
                "Executable spread/depth evidence for the execution market was "
                "unavailable, so net after-cost reward cannot be validated."
            ),
            horizon=horizon,
            regime=regime,
            price=price,
            evidence=evidence,
        )
    evidence["execution_evidence"] = {
        "status": getattr(execution, "status", None),
        "book_coverage_status": getattr(execution, "book_coverage_status", None),
        "spread_bps": getattr(execution, "spread_bps", None),
        "round_trip_market_drag_pct": getattr(
            execution, "estimated_visible_round_trip_market_drag_pct", None
        ),
        "warnings": list(getattr(execution, "warnings", []) or []),
    }
    # Independent native context (BTC regime, chase risk, breadth, ranking).
    # Read-only: it informs the operator and never grants authority.
    btc_market = str(
        getattr(resolved, "opip_tradevisor_btc_market", "XBTUSD") or ""
    ).strip().upper()
    evidence["native_context"] = build_native_context(
        snapshot=snapshot,
        execution=execution,
        now=evaluated_at,
        btc_provider=btc_context_provider or provider,
        btc_market=btc_market,
    )
    return _evaluate_buy_plans(
        signal,
        snapshot=snapshot,
        evaluated_at=evaluated_at,
        price=price,
        regime=regime,
        horizon=horizon,
        equity=equity,
        evidence=evidence,
        risk_per_trade_pct=risk_per_trade_pct,
        expiry_minutes=expiry_minutes,
        active_trades=active_trades,
    )




def _finalize_qualified_buy(
    signal: TradevisorSignal,
    *,
    accepted: dict[str, Any],
    evaluated_at: datetime,
    price: float,
    regime: str | None,
    horizon: str,
    equity: float,
    evidence: dict[str, Any],
    risk_per_trade_pct: float,
    expiry_minutes: int,
    active_trades: list[Any] | None,
) -> TradevisorDecision:
    """F7 capital planning and exposure capacity for an already-qualified plan."""
    plan: EntryExitPlan = accepted["plan"]
    attainability: TargetAttainabilityResult = accepted["attainability"]
    economics: EconomicGateResult = accepted["economics"]
    evidence["selected_risk_level"] = accepted["risk_level"]

    notional = float(economics.position_notional or 0.0)
    net_edge_pct = (
        float(economics.target_2_net_profit) / notional * 100.0 if notional > 0 else 0.0
    )
    allocation = recommend_capital(
        available_capital=equity,
        stop_distance_pct=float(economics.stop_pct),
        confidence_score=float(attainability.attainability_score),
        quality_score=float(attainability.attainability_score),
        net_edge_pct=net_edge_pct,
        max_position_pct=PRODUCTION_MAX_CAPITAL_FRACTION * 100.0,
        risk_per_trade_pct=risk_per_trade_pct,
    )
    evidence["f7_capital"] = {
        "recommended_capital": allocation.recommended_capital,
        "risk_dollars": allocation.risk_dollars,
        "position_pct": allocation.position_pct,
        "reason": allocation.reason,
        "net_edge_pct": round(net_edge_pct, 4),
        "min_executable_notional_usd": MIN_EXECUTABLE_NOTIONAL_USD,
    }

    # ------------------------------------------------------------------
    # Final-allocation economic qualification.
    #
    # The F5 call above decides at the *account-envelope* notional
    # (equity x PRODUCTION_MAX_CAPITAL_FRACTION), which is not the capital F7
    # would actually permit. Re-running the SAME F5 owner against the final
    # allocation makes the qualifying number and the displayed number the same
    # figure. No threshold, fee, ratio or formula changes: only the notional
    # basis. ``max_account_risk_at_stop_pct`` is left to the envelope call
    # above, which owns that limit by design; F7's fixed risk budget bounds the
    # actual risk of the permitted allocation.
    # ------------------------------------------------------------------
    final_economics: EconomicGateResult = economics
    if allocation.recommended_capital > 0:
        final_economics = evaluate_economic_quality(
            plan,
            float(allocation.recommended_capital),
            max_capital_fraction=1.0,
            max_account_risk_at_stop_pct=None,
        )
        evidence["economics_at_envelope"] = {
            "recommended_capital": economics.recommended_capital,
            "position_notional": economics.position_notional,
            "target_1_net_profit": economics.target_1_net_profit,
            "target_2_net_profit": economics.target_2_net_profit,
            "qualified": economics.qualified,
        }
        evidence["final_allocation_basis"] = {
            "allocation": allocation.recommended_capital,
            "economics_recommended_capital": final_economics.recommended_capital,
            "position_notional": final_economics.position_notional,
            "parity": abs(
                float(final_economics.recommended_capital)
                - float(allocation.recommended_capital)
            )
            <= 0.01,
            "policy": "F5 thresholds unchanged; the notional basis is the F7 allocation",
        }
        evidence["f5_final"] = {
            "qualified": final_economics.qualified,
            "target_1_net_profit": final_economics.target_1_net_profit,
            "target_2_net_profit": final_economics.target_2_net_profit,
            "rejection_reason": final_economics.rejection_reason,
        }

    rejection: tuple[str, str] | None = None
    if allocation.recommended_capital <= 0:
        rejection = (
            "CAPITAL_REJECT",
            f"F7 capital planning allocated no capital: {allocation.reason}",
        )
    elif allocation.recommended_capital < MIN_EXECUTABLE_NOTIONAL_USD:
        rejection = (
            "CAPITAL_BELOW_EXECUTABLE_MINIMUM",
            f"F7 recommended capital USD {allocation.recommended_capital:.2f} is below "
            f"the minimum executable notional USD {MIN_EXECUTABLE_NOTIONAL_USD:.2f}.",
        )
    elif not final_economics.qualified:
        # The candidate clears the bar only at capital the operator may not
        # deploy, so it is NOT an economic pass at the permitted size.
        rejection = (
            "ECONOMIC_REJECT_AT_FINAL_ALLOCATION",
            f"The economic gate fails at the F7-permitted allocation "
            f"USD {allocation.recommended_capital:.2f}: "
            f"{final_economics.rejection_reason}",
        )

    portfolio: dict[str, Any] | None = None
    if rejection is None:
        if active_trades is not None:
            exposure = evaluate_portfolio_risk(
                active_trades=list(active_trades),
                proposed_symbol=signal.market,
                proposed_direction="LONG",
                proposed_capital=float(allocation.recommended_capital),
                account_capital=equity,
                proposed_leverage=1.0,
            )
            portfolio = exposure.as_dict()
            evidence["exposure"] = portfolio
            if not exposure.allowed:
                rejection = (
                    "EXPOSURE_REJECT",
                    f"Exposure capacity rejected the trade: {exposure.reason}",
                )
        else:
            # Not evaluated is not the same as clear.
            evidence["exposure"] = {"status": "EXPOSURE_NOT_EVALUATED"}

    if rejection is not None:
        code, reason = rejection
        return _finalize(
            signal,
            now=evaluated_at,
            decision=DECISION_WATCH,
            code=code,
            reason=reason,
            horizon=horizon,
            regime=regime,
            price=price,
            plan=plan,
            economics=final_economics,
            attainability=attainability,
            allocation=allocation,
            portfolio=portfolio,
            evidence=evidence,
        )

    proposed_action = DECISION_ENTER_NOW if plan.valid_now else DECISION_SET_LIMIT_ENTRY
    # G-02 owner decision (2026-10-23): no trusted F6 production forecast and
    # no approved deterministic fallback exist. The calculated trade is a
    # shadow hypothesis, *never* an actionable recommendation. This explicit
    # blocker must be revisited in an independently approved increment; merely
    # changing a forecast status string cannot grant actionable authority.
    missing = ["F6_TRUSTED_FORECAST"]
    # G-04: a SWING requires completed, trusted higher-timeframe context.
    # The existing path only has a native completed 1H snapshot. Do not infer
    # a 4H close from the alert or allow a 1H proxy to stand in for it.
    if horizon == HORIZON_SWING:
        missing.append("SWING_4H_CLOSED_CONTEXT")
    if active_trades is None:
        missing.append("PORTFOLIO_EXPOSURE")
    evidence["shadow_proposed_action"] = proposed_action
    evidence["advisory_qualification"] = {
        "status": "SHADOW_ONLY",
        "missing": missing,
        "policy": "OWNER_APPROVED_G02_G04_2026-10-23",
    }
    return _finalize(
        signal,
        now=evaluated_at,
        decision=DECISION_WATCH,
        code="ADVISORY_EVIDENCE_INCOMPLETE",
        reason=(
            f"Shadow-only {horizon.lower()} LONG hypothesis ({proposed_action}); "
            f"actionable qualification withheld pending {', '.join(missing)}. "
            "No approved F6 fallback or 4H inference is permitted."
        ),
        horizon=horizon,
        regime=regime,
        price=price,
        plan=plan,
        economics=final_economics,
        attainability=attainability,
        allocation=allocation,
        portfolio=portfolio,
        evidence=evidence,
    )
