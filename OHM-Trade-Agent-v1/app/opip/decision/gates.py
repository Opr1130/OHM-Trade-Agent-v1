"""Gate adapters for the O'Pip Decision Engine.

Every function here is a thin adapter around the evaluator that production
already uses. None of them re-implements a threshold or a policy, and none of
them performs network I/O: they read evidence that the production scan has
already attached to the snapshot, or call a pure evaluator on it. That is what
makes the shadow engine a reproduction of the live decision rather than a
second, competing opinion.

``short_execution_is_tradeable`` is deliberately called with
``refresh_margin_book=False``. The live path already refreshed the Bitnomial
book onto the snapshot; refreshing again would make a shadow evaluation issue
exchange requests, which it must never do.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.opip.decision.models import (
    GateName,
    GateResult,
    GateStatus,
    ReasonCode,
)
from app.opip.decision.thresholds import (
    AI_MIN_CONFIDENCE,
    ALLOWED_DIRECTIONS,
    ALLOWED_RISK_LEVELS,
    PRODUCTION_MAX_CAPITAL_FRACTION,
    TARGET_MIN_QUALIFYING_SCORE,
)
from app.scanner.execution_validation import INVALID
from app.scanner.short_execution_quality import short_execution_is_tradeable
from app.services.chief_analyst import (
    SHORT_MARGIN_COST_RESERVE_PCT,
    SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
    SHORT_VALIDATION_LEVERAGE,
)
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.recommendation_gate import candidate_alert_authorized, parse_confidence
from app.services.short_target_attainability import evaluate_short_target_attainability
from app.services.target_attainability import evaluate_target_attainability


def _direction(snapshot: Any) -> str:
    return str(getattr(snapshot, "trade_direction", "LONG") or "LONG").upper()


def evaluate_margin_gate(
    snapshot: Any,
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Reproduce ``keep_margin_tradeable_candidates`` for one candidate.

    LONG candidates do not touch the margin venue, so the gate is SKIPPED
    rather than passed: "not applicable" and "checked and fine" are different
    facts.
    """
    if _direction(snapshot) != "SHORT":
        return GateResult.build(
            GateName.MARGIN_ELIGIBILITY,
            GateStatus.SKIPPED,
            ReasonCode.GATE_PASSED,
            reason="spot LONG candidate does not use the margin venue",
            evaluated_at=evaluated_at,
            metadata={"market_type": "SPOT"},
        )

    status = str(getattr(snapshot, "margin_validation_status", "") or "").upper()
    metadata = {
        "margin_validation_status": status or "UNKNOWN",
        "margin_venue_symbol": getattr(snapshot, "margin_venue_symbol", None),
        "margin_max_leverage": getattr(snapshot, "margin_max_leverage", None),
    }
    if status == "ELIGIBLE":
        return GateResult.build(
            GateName.MARGIN_ELIGIBILITY,
            GateStatus.PASS,
            ReasonCode.GATE_PASSED,
            reason="Kraken US retail margin eligible",
            evaluated_at=evaluated_at,
            metadata=metadata,
        )
    return GateResult.build(
        GateName.MARGIN_ELIGIBILITY,
        GateStatus.FAIL,
        ReasonCode.MARGIN_INELIGIBLE,
        reason=f"margin status {status or 'UNKNOWN'}",
        evaluated_at=evaluated_at,
        metadata=metadata,
    )


def evaluate_execution_gate(
    snapshot: Any,
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Reproduce structural execution validation plus the SHORT quality gate."""
    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        return GateResult.build(
            GateName.EXECUTION_VALIDATION,
            GateStatus.ERROR,
            ReasonCode.GATE_EVALUATION_ERROR,
            reason="execution validation evidence is missing",
            evaluated_at=evaluated_at,
        )

    status = str(getattr(execution, "status", "") or "")
    metadata: dict[str, Any] = {
        "structural_status": status,
        "book_coverage_status": getattr(execution, "book_coverage_status", None),
        "spread_bps": getattr(execution, "spread_bps", None),
    }
    if status == INVALID:
        return GateResult.build(
            GateName.EXECUTION_VALIDATION,
            GateStatus.FAIL,
            ReasonCode.EXECUTION_VALIDATION_FAILED,
            reason="structural execution validation returned INVALID",
            measured_value=getattr(execution, "spread_bps", None),
            evaluated_at=evaluated_at,
            metadata=metadata,
        )

    if _direction(snapshot) == "SHORT":
        tradeable, reasons = short_execution_is_tradeable(
            snapshot,
            refresh_margin_book=False,
        )
        metadata["short_execution_reasons"] = list(reasons)
        if not tradeable:
            return GateResult.build(
                GateName.EXECUTION_VALIDATION,
                GateStatus.FAIL,
                ReasonCode.SHORT_EXECUTION_QUALITY_FAILED,
                reason="; ".join(reasons),
                evaluated_at=evaluated_at,
                metadata=metadata,
            )

    return GateResult.build(
        GateName.EXECUTION_VALIDATION,
        GateStatus.PASS,
        ReasonCode.GATE_PASSED,
        reason=f"execution evidence {status}",
        measured_value=getattr(execution, "spread_bps", None),
        evaluated_at=evaluated_at,
        metadata=metadata,
    )


def evaluate_cross_market_gate(
    snapshot: Any,
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Record cross-pair confirmation.

    Production treats cross-pair confirmation as evidence, not as a filter: no
    candidate is dropped for it. The gate therefore never FAILs, and recording
    it as PASS would overstate what was proven, so an unconfirmed candidate is
    SKIPPED with the reason preserved.
    """
    status = str(getattr(snapshot, "cross_pair_confirmation_status", "") or "UNKNOWN")
    metadata = {
        "cross_pair_confirmation_status": status,
        "primary_pair": getattr(snapshot, "primary_pair", None),
        "secondary_pair": getattr(snapshot, "secondary_pair", None),
        "combined_24h_liquidity_usd": getattr(
            snapshot, "combined_24h_liquidity_usd", None
        ),
    }
    confirmed = status.upper() in {"CONFIRMED", "PRIMARY_ONLY_CONFIRMED"}
    return GateResult.build(
        GateName.CROSS_MARKET_CONFIRMATION,
        GateStatus.PASS if confirmed else GateStatus.SKIPPED,
        ReasonCode.GATE_PASSED if confirmed else ReasonCode.CROSS_MARKET_UNCONFIRMED,
        reason=f"cross-pair confirmation {status}",
        evaluated_at=evaluated_at,
        metadata=metadata,
    )


def evaluate_reference_gate(
    snapshot: Any,
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Record independent reference validation.

    UNAVAILABLE is missing evidence, never an automatic rejection - the same
    rule the Chief prompt states. It is recorded as SKIPPED so an evidence gap
    can be measured without being mistaken for a policy decision.
    """
    reference = getattr(snapshot, "independent_market_reference", None)
    status = str(getattr(reference, "status", "") or "UNAVAILABLE").upper()
    metadata = {
        "reference_status": status,
        "coingecko_id": getattr(reference, "coingecko_id", None),
        "price_divergence_pct": getattr(reference, "price_divergence_pct", None),
    }
    available = status == "AVAILABLE"
    return GateResult.build(
        GateName.REFERENCE_VALIDATION,
        GateStatus.PASS if available else GateStatus.SKIPPED,
        ReasonCode.GATE_PASSED if available else ReasonCode.REFERENCE_EVIDENCE_UNAVAILABLE,
        reason=f"independent reference {status}",
        measured_value=getattr(reference, "price_divergence_pct", None),
        evaluated_at=evaluated_at,
        metadata=metadata,
    )


def evaluate_market_intelligence_gate(
    snapshot: Any,
    *,
    assessment: Any = None,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Record external market intelligence enrichment (evidence only)."""
    context = assessment
    if context is None:
        context = getattr(snapshot, "_wave8_market_intelligence", None)
    status = str(getattr(context, "status", "") or "UNAVAILABLE").upper()
    if isinstance(context, dict):
        status = str(context.get("status") or "UNAVAILABLE").upper()
    available = status == "AVAILABLE"
    return GateResult.build(
        GateName.MARKET_INTELLIGENCE,
        GateStatus.PASS if available else GateStatus.SKIPPED,
        ReasonCode.GATE_PASSED
        if available
        else ReasonCode.MARKET_INTELLIGENCE_UNAVAILABLE,
        reason=f"external market intelligence {status}",
        evaluated_at=evaluated_at,
        metadata={"intelligence_status": status},
    )


def evaluate_deterministic_quality_gate(
    snapshot: Any,
    *,
    account_equity: float | None,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Reproduce the Chief pre-AI deterministic viability prefilter.

    ``chief_analyst`` screens each finalist across the low and medium risk
    levels and drops any candidate that cannot clear both the target and
    economic gates at either level. That screen decides whether the AI is ever
    consulted, so it is the single most important stage to attribute
    correctly - and today it is invisible in operator output.
    """
    if account_equity is None:
        return GateResult.build(
            GateName.DETERMINISTIC_QUALITY,
            GateStatus.SKIPPED,
            ReasonCode.GATE_PASSED,
            reason="account equity unavailable; production skips the prefilter too",
            evaluated_at=evaluated_at,
        )

    from app.services.chief_analyst import (
        _quality_by_risk_level,
        binding_deterministic_constraint,
    )

    quality_by_risk_level, viable = _quality_by_risk_level(snapshot, account_equity)
    binding = binding_deterministic_constraint(quality_by_risk_level)
    reasons: list[str] = []
    for risk_level, level in quality_by_risk_level.items():
        parts: list[str] = []
        rejections = level.get("target_quality_rejections") or []
        if rejections:
            parts.append("target=" + "; ".join(str(item) for item in rejections))
        economic_rejection = level.get("economic_rejection")
        if economic_rejection:
            parts.append("economic=" + str(economic_rejection))
        if parts:
            reasons.append(f"{risk_level}: " + " | ".join(parts))

    metadata = {"risk_levels_evaluated": sorted(quality_by_risk_level), **binding}
    if viable:
        return GateResult.build(
            GateName.DETERMINISTIC_QUALITY,
            GateStatus.PASS,
            ReasonCode.GATE_PASSED,
            reason="deterministic target and economic gates viable at low or medium risk",
            measured_value=binding["binding_measured"],
            threshold=binding["binding_threshold"],
            higher_is_better=bool(binding.get("binding_higher_is_better", True)),
            evaluated_at=evaluated_at,
            metadata=metadata,
        )
    return GateResult.build(
        GateName.DETERMINISTIC_QUALITY,
        GateStatus.FAIL,
        ReasonCode.DETERMINISTIC_VIABILITY_FAILED,
        reason="; ".join(reasons)
        or "no risk level clears both the target and economic gates",
        measured_value=binding["binding_measured"],
        threshold=binding["binding_threshold"],
        higher_is_better=bool(binding.get("binding_higher_is_better", True)),
        evaluated_at=evaluated_at,
        metadata=metadata,
    )


def evaluate_recommendation_gate_item(
    item: dict[str, Any],
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Evaluate one Chief recommendation without treating confidence as authority.

    The Chief's numeric confidence is comparative review evidence, not a
    calibrated probability. ALERT items with valid schema continue to the
    unchanged deterministic gates even below the legacy 85 boundary.
    """
    decision = str(item.get("decision") or "").lower()
    risk_level = str(item.get("risk_level") or "").lower()
    direction = str(item.get("direction") or "").upper()
    confidence = parse_confidence(item)

    metadata = {
        "ai_decision": decision,
        "ai_risk_level": risk_level,
        "ai_direction": direction,
        "ai_confidence": confidence,
        "ai_rank": item.get("rank"),
        "calibrated_probability": False,
        "confidence_is_trade_authority": False,
    }

    def _fail(code: ReasonCode, reason: str) -> GateResult:
        return GateResult.build(
            GateName.RECOMMENDATION_GATE,
            GateStatus.FAIL,
            code,
            reason=reason,
            measured_value=confidence,
            threshold=AI_MIN_CONFIDENCE,
            evaluated_at=evaluated_at,
            metadata=metadata,
        )

    if decision == "watch":
        return _fail(ReasonCode.AI_DECISION_WATCH, "Chief returned watch, not alert")
    if decision != "alert":
        return _fail(
            ReasonCode.AI_DECISION_REJECT,
            f"Chief decision {decision or 'unknown'}",
        )
    if direction not in ALLOWED_DIRECTIONS:
        return _fail(
            ReasonCode.AI_DIRECTION_REJECTED,
            f"direction {direction} is outside the allowed set",
        )
    if risk_level not in ALLOWED_RISK_LEVELS:
        return _fail(
            ReasonCode.AI_RISK_LEVEL_REJECTED,
            f"risk level {risk_level or 'unknown'} is outside the allowed set",
        )
    if confidence is None or confidence < 0 or confidence > 100:
        return _fail(
            ReasonCode.AI_CONFIDENCE_INVALID,
            "Chief confidence is missing or outside the required 0-100 schema",
        )
    if not candidate_alert_authorized(item):
        return _fail(
            ReasonCode.AI_DECISION_REJECT,
            "Chief candidate failed recommendation schema validation",
        )

    below_boundary = confidence < AI_MIN_CONFIDENCE
    return GateResult.build(
        GateName.RECOMMENDATION_GATE,
        GateStatus.PASS,
        (
            ReasonCode.AI_CONFIDENCE_COUNTERFACTUAL
            if below_boundary
            else ReasonCode.GATE_PASSED
        ),
        reason=(
            f"Chief alert admitted; confidence {confidence} is below the legacy "
            f"measurement boundary {AI_MIN_CONFIDENCE}"
            if below_boundary
            else "Chief alert cleared the recommendation gate"
        ),
        measured_value=confidence,
        threshold=AI_MIN_CONFIDENCE,
        evaluated_at=evaluated_at,
        metadata={
            **metadata,
            "below_legacy_confidence_boundary": below_boundary,
            "measurement_only_confidence_boundary": True,
        },
    )

def target_quality_gate_from_result(
    result: Any,
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Convert an already-computed target attainability result to a GateResult.

    The live scan has usually computed this already. Reusing its result keeps
    the funnel free of a second evaluation, which matters because the funnel
    records what production actually did.
    """
    metadata = {
        "attainability_score": result.attainability_score,
        "target_2_atr_multiple": result.target_2_atr_multiple,
        "clearance_24h_pct": result.clearance_to_24h_resistance_pct,
        "clearance_72h_pct": result.clearance_to_72h_resistance_pct,
        "rejection_reasons": list(result.rejection_reasons),
    }
    if result.qualified:
        return GateResult.build(
            GateName.TARGET_QUALITY,
            GateStatus.PASS,
            ReasonCode.GATE_PASSED,
            reason=f"attainability score {result.attainability_score}",
            measured_value=result.attainability_score,
            threshold=TARGET_MIN_QUALIFYING_SCORE,
            evaluated_at=evaluated_at,
            metadata=metadata,
        )
    return GateResult.build(
        GateName.TARGET_QUALITY,
        GateStatus.FAIL,
        ReasonCode.TARGET_ATTAINABILITY_FAILED,
        reason="; ".join(result.rejection_reasons) or "target attainability rejected",
        measured_value=result.attainability_score,
        threshold=TARGET_MIN_QUALIFYING_SCORE,
        evaluated_at=evaluated_at,
        metadata=metadata,
    )


def economic_quality_gate_from_result(
    result: Any,
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Convert an already-computed economic gate result to a GateResult."""
    metadata = {
        "target_1_move_pct": result.target_1_move_pct,
        "target_2_move_pct": result.target_2_move_pct,
        "target_2_net_profit": result.target_2_net_profit,
        "estimated_costs": result.estimated_costs,
        "leverage": getattr(result, "leverage", 1.0),
        "account_risk_at_stop_pct": getattr(result, "account_risk_at_stop_pct", 0.0),
    }
    if result.qualified:
        return GateResult.build(
            GateName.ECONOMIC_QUALITY,
            GateStatus.PASS,
            ReasonCode.GATE_PASSED,
            reason=f"net profit at target 2 is {result.target_2_net_profit:.2f}",
            measured_value=result.target_2_net_profit,
            threshold=0.0,
            evaluated_at=evaluated_at,
            metadata=metadata,
        )
    return GateResult.build(
        GateName.ECONOMIC_QUALITY,
        GateStatus.FAIL,
        ReasonCode.ECONOMIC_GATE_FAILED,
        reason=str(result.rejection_reason or "economic gate rejected"),
        measured_value=result.target_2_net_profit,
        threshold=0.0,
        evaluated_at=evaluated_at,
        metadata=metadata,
    )


def evaluate_target_quality_gate(
    plan: Any,
    snapshot: Any,
    *,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Independently recompute the final target attainability gate."""
    direction = _direction(snapshot)
    result = (
        evaluate_short_target_attainability(plan, snapshot)
        if direction == "SHORT"
        else evaluate_target_attainability(plan, snapshot)
    )
    return target_quality_gate_from_result(result, evaluated_at=evaluated_at)


def evaluate_economic_quality_gate(
    plan: Any,
    snapshot: Any,
    *,
    account_equity: float,
    evaluated_at: datetime | None = None,
) -> GateResult:
    """Independently recompute the final economic quality gate."""
    if _direction(snapshot) == "SHORT":
        result = evaluate_economic_quality(
            plan,
            available_capital=account_equity,
            max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
            direction="SHORT",
            leverage=SHORT_VALIDATION_LEVERAGE,
            estimated_margin_cost_pct=SHORT_MARGIN_COST_RESERVE_PCT,
            max_account_risk_at_stop_pct=SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
        )
    else:
        result = evaluate_economic_quality(
            plan,
            available_capital=account_equity,
            max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
        )
    return economic_quality_gate_from_result(result, evaluated_at=evaluated_at)
