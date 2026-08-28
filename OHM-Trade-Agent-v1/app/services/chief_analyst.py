import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

from openai import OpenAI

from app.scanner.models import MarketSnapshot
from app.scanner.short_technical_scorer import score_short_snapshot
from app.scanner.technical_scorer import score_snapshot
from app.services.candidate_trace import trace_candidate_event
from app.services.chief_learning_capture import (
    capture_chief_review_decisions,
    capture_prefilter_rejection,
)
from app.services.chief_runtime_guard import (
    budget_block_reason,
    build_chief_fingerprint,
    get_cached_review,
    store_cached_review,
)
from app.services.economic_quality_gate import (
    MIN_NET_PROFIT,
    PRODUCTION_MAX_CAPITAL_FRACTION,
    evaluate_economic_quality,
)
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.openai_usage_telemetry import append_usage_record
from app.services.short_target_attainability import evaluate_short_target_attainability
from app.services.target_attainability import (
    MIN_QUALIFYING_SCORE,
    evaluate_target_attainability,
)


SYSTEM_PROMPT = """You are OHM AI's Chief Investment Analyst and Risk Advisor.

Compare already-screened crypto opportunities and identify which, if any, deserve human attention. Candidates may be LONG or SHORT. Do not place trades and never change the supplied direction.

SHORT candidates have already passed Kraken US retail margin pair eligibility before reaching you. Margin availability is evidence of tradability, not a recommendation to maximize leverage. OHM v1 models SHORT economics at a conservative 2x validation exposure and does not authorize automatic margin orders. Dynamic opening and rollover rates can change at execution time; the supplied margin-cost reserve is an estimate and must not be presented as a guaranteed fee.

Each payload item is one unique underlying asset/direction. Use the exact primary_pair value as the candidate symbol and return its exact direction. Do not invent a different pair or direction.

Consider trend quality, RSI extension, MACD confirmation, volume quality, volatility, relative strength/weakness, chase risk, execution evidence, deterministic target-quality and economic context. For SHORT candidates, favor bearish continuation with realistic downside room; do not recommend shorting merely because the broad regime is RISK_OFF.

Target-quality scores are deterministic opportunity-quality scores, not probabilities. AI confidence is comparative review confidence, not win probability. Do not recalculate supplied deterministic metrics.

Kraken market-data and execution values are deterministic evidence. PreTrade contains only the top 10 aggregated levels; incomplete depth is partial observed liquidity, never zero liquidity. SHORT entry depends on bid-side sellability and eventual cover depends on ask-side buyback liquidity. Never convert execution coverage into probability.

CoinGecko is independent aggregated reference evidence, not an execution venue. AMBIGUOUS means OHM selected no external identity and no price/divergence conclusion may be drawn. UNAVAILABLE is missing evidence, not automatic rejection. CryptoPanic news is associated only after identity-safe structured instrument matching; ticker-only attribution is refused. News/catalyst evidence can be missing or unresolved and is never a substitute for safe identity matching.

Market regime is deterministic breadth context, not a probability. RISK_OFF is caution for longs and context for shorts, not an automatic direction signal. Economic assumed capital is validation capital, not allocation advice.

Price-movement readiness is deterministic volatility-expansion context, not a probability or independent trade authorization. WATCH/READY never authorize entry. CONFIRMED/ACTIVE can only support the candidate's supplied direction and cannot override any deterministic gate.

Return JSON only in this exact shape:
{
  "market_view": "",
  "recommended_action": "alert|watch|no_trade",
  "top_candidates": [
    {
      "symbol": "",
      "direction": "LONG|SHORT",
      "rank": 1,
      "confidence": 0,
      "risk_level": "low|medium|high",
      "decision": "alert|watch|reject",
      "reason": ""
    }
  ],
  "summary": ""
}
Maximum 3 top_candidates. Confidence must be 0-100. Be conservative.
"""

_ALLOWED_REASONING_EFFORTS = {"low", "medium", "high"}

# Deterministic identity for the exact system prompt in use. This is not a
# semantic version: it is a content hash, so it changes if and only if the
# prompt text changes. O'Pip qualification evidence records it so a future
# confidence analysis can partition by the prompt that produced the number.
SYSTEM_PROMPT_VERSION = "CHIEF-PROMPT:" + hashlib.sha256(
    SYSTEM_PROMPT.encode("utf-8")
).hexdigest()[:12]

SHORT_VALIDATION_LEVERAGE = 2.0
SHORT_MARGIN_COST_RESERVE_PCT = 0.28
SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT = 5.0


def _reasoning_effort() -> str:
    effort = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip().lower()
    if effort not in _ALLOWED_REASONING_EFFORTS:
        raise ValueError("OPENAI_REASONING_EFFORT must be one of: low, medium, high")
    return effort


def _max_output_tokens() -> int:
    raw = os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "1200").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be an integer") from exc
    if value < 256 or value > 4096:
        raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be between 256 and 4096")
    return value


def _quality_by_risk_level(candidate: MarketSnapshot, account_equity: float) -> tuple[dict, bool]:
    quality_by_risk_level: dict = {}
    viable_any = False
    direction = candidate.trade_direction.upper()
    for risk_level in ("low", "medium"):
        plan = (
            build_entry_exit_plan(candidate, risk_level, direction="SHORT")
            if direction == "SHORT"
            else build_entry_exit_plan(candidate, risk_level)
        )
        if direction == "SHORT":
            target = evaluate_short_target_attainability(plan, candidate)
            economic = evaluate_economic_quality(
                plan,
                account_equity,
                max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
                direction="SHORT",
                leverage=SHORT_VALIDATION_LEVERAGE,
                estimated_margin_cost_pct=SHORT_MARGIN_COST_RESERVE_PCT,
                max_account_risk_at_stop_pct=SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
            )
        else:
            target = evaluate_target_attainability(plan, candidate)
            economic = evaluate_economic_quality(
                plan,
                account_equity,
                max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
            )
        viable = target.qualified and economic.qualified
        viable_any = viable_any or viable
        quality_by_risk_level[risk_level] = {
            "direction": direction,
            "target_quality_score": target.attainability_score,
            "target_quality_qualified": target.qualified,
            "target_quality_warnings": target.warnings,
            "target_quality_rejections": target.rejection_reasons,
            "target_2_atr_multiple": target.target_2_atr_multiple,
            "clearance_24h_pct": target.clearance_to_24h_resistance_pct,
            "clearance_72h_pct": target.clearance_to_72h_resistance_pct,
            "momentum_context": target.momentum_context,
            "economic_qualified": economic.qualified,
            "economic_rejection": economic.rejection_reason,
            "economic_assumed_capital": economic.recommended_capital,
            "economic_validation_leverage": getattr(economic, "leverage", 1.0),
            "economic_account_risk_at_stop_pct": getattr(economic, "account_risk_at_stop_pct", 0.0),
            "hypothetical_target_2_net_profit_at_assumed_capital": economic.target_2_net_profit,
        }
    return quality_by_risk_level, viable_any


#: O'Pip Chief invocation states. These are recorded separately because
#: "no eligible candidate", "budget suppressed", "service failed", "cache
#: reused" and "the model answered" are materially different events that the
#: legacy ``AI top candidates = 0`` operator line collapses into one.
CHIEF_SKIPPED_NO_ELIGIBLE = "SKIPPED_NO_ELIGIBLE_CANDIDATES"
CHIEF_BUDGET_BLOCKED = "BUDGET_BLOCKED"
CHIEF_CACHE_REUSED = "CACHE_REUSED"
CHIEF_FAILED = "FAILED"
CHIEF_SUCCEEDED = "SUCCEEDED"


def _new_stage_evidence() -> dict:
    """Return the mutable O'Pip stage-evidence accumulator for one review.

    Measurement only: nothing read or written here participates in the review
    decision. It exists so the qualification funnel can attribute the AI stage
    exactly instead of inferring it from an empty candidate list.
    """
    return {
        "prefiltered": [],
        "eligible": [],
        "invocation_status": CHIEF_SKIPPED_NO_ELIGIBLE,
        "failure_type": None,
        "invoked_at": None,
        "model": None,
        "prompt_version": SYSTEM_PROMPT_VERSION,
        "eligible_candidate_count": 0,
        "returned_candidate_count": 0,
    }


def binding_deterministic_constraint(quality_by_risk_level: dict) -> dict:
    """Return the constraint that actually stopped a deterministic screen.

    When no risk level clears the target gate, the target score is what stopped
    the candidate. When the target gate cleared and only the economics failed,
    the target score is irrelevant and quoting it would suggest the candidate
    was comfortably clear when it was not.

    Shared by the Chief prefilter evidence and the O'Pip shadow gate so the two
    can never disagree about which number was binding.
    """
    levels = list(quality_by_risk_level.values())
    target_passed = any(
        bool(level.get("target_quality_qualified")) for level in levels
    )
    best_target_score = max(
        (float(level.get("target_quality_score") or 0.0) for level in levels),
        default=0.0,
    )
    best_net_profit = max(
        (
            float(
                level.get("hypothetical_target_2_net_profit_at_assumed_capital")
                or 0.0
            )
            for level in levels
        ),
        default=0.0,
    )
    if target_passed:
        metric, measured, threshold = (
            "ECONOMIC_NET_PROFIT_AT_TARGET_2",
            best_net_profit,
            float(MIN_NET_PROFIT),
        )
    else:
        metric, measured, threshold = (
            "TARGET_QUALITY_SCORE",
            best_target_score,
            float(MIN_QUALIFYING_SCORE),
        )
    return {
        "target_qualified_any": target_passed,
        "economic_qualified_any": any(
            bool(level.get("economic_qualified")) for level in levels
        ),
        "best_target_quality_score": best_target_score,
        "best_economic_net_profit": best_net_profit,
        "binding_metric": metric,
        "binding_measured": measured,
        "binding_threshold": threshold,
    }


def _prefilter_evidence(candidate: MarketSnapshot, quality_by_risk_level: dict) -> dict:
    """Summarise why the deterministic prefilter dropped one finalist.

    This is the stage that most often ends a scan, and today it is invisible:
    the candidate simply never appears in the Chief payload.
    """
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
    return {
        "symbol": candidate.symbol,
        "direction": candidate.trade_direction.upper(),
        **binding_deterministic_constraint(quality_by_risk_level),
        "reason": "; ".join(reasons)
        or "no risk level clears both the target and economic gates",
    }


def _no_trade_review(
    reason: str,
    *,
    failure_code: str | None = None,
    eligible_candidates: int = 0,
    stage_evidence: dict | None = None,
) -> dict:
    review = {
        "market_view": "",
        "recommended_action": "no_trade",
        "top_candidates": [],
        "summary": reason,
        "chief_api_skipped": True,
        "chief_eligible_candidates": int(eligible_candidates),
    }
    if failure_code:
        review["chief_failure_code"] = failure_code
    if stage_evidence is not None:
        review["opip_stage_evidence"] = stage_evidence
    return review


def _trace_chief_failure(candidates: list[MarketSnapshot], *, reason_code: str, detail: str) -> None:
    for candidate in candidates:
        try:
            trace_candidate_event(
                symbol=candidate.symbol,
                direction=candidate.trade_direction or "LONG",
                stage="CHIEF",
                reason_code=reason_code,
                details={"detail": detail},
            )
        except Exception:
            pass


def _request_payload(payload: list[dict], *, market_regime_context: object | None, coingecko_global_context: object | None) -> dict:
    return {
        "candidate_count": len(payload),
        "market_regime_context": {
            "ohm_breadth": asdict(market_regime_context) if is_dataclass(market_regime_context) else None,
            "coingecko_global": asdict(coingecko_global_context) if is_dataclass(coingecko_global_context) else None,
        },
        "candidates": payload,
    }


def review_candidates(
    candidates: list[MarketSnapshot],
    model: str,
    api_key: str,
    account_equity: float | None = None,
    market_regime_context: object | None = None,
    coingecko_global_context: object | None = None,
) -> dict:
    stage_evidence = _new_stage_evidence()
    stage_evidence["model"] = model
    payload = []
    unique_candidates: list[MarketSnapshot] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates[:8]:
        key = (candidate.underlying_asset or candidate.symbol, candidate.trade_direction.upper())
        if key in seen:
            continue
        seen.add(key)
        if candidate.trade_direction.upper() == "SHORT" and not candidate.margin_eligible:
            continue
        unique_candidates.append(candidate)

    chief_eligible_candidates: list[MarketSnapshot] = []
    for candidate in unique_candidates:
        quality_by_risk_level = None
        if account_equity is not None:
            quality_by_risk_level, viable = _quality_by_risk_level(candidate, account_equity)
            if not viable:
                capture_prefilter_rejection(
                    candidate,
                    quality_by_risk_level=quality_by_risk_level,
                    market_regime_context=market_regime_context,
                )
                stage_evidence["prefiltered"].append(
                    _prefilter_evidence(candidate, quality_by_risk_level)
                )
                continue

        direction = candidate.trade_direction.upper()
        card = score_short_snapshot(candidate) if direction == "SHORT" else score_snapshot(candidate)
        candidate_context = {
            "symbol": candidate.symbol,
            "direction": direction,
            "technical_score": candidate.technical_score,
            "price": candidate.last_price,
            "trend": candidate.trend,
            "rsi": round(candidate.rsi, 2),
            "macd_histogram": round(candidate.macd_histogram, 6),
            "atr_pct": round(candidate.atr_pct, 3),
            "volume_ratio": round(candidate.volume_ratio, 2),
            "strengths": card.strengths,
            "warnings": card.warnings,
            "weaknesses": card.weaknesses,
            "margin_evidence": {
                "eligible": candidate.margin_eligible,
                "status": candidate.margin_validation_status,
                "venue_symbol": candidate.margin_venue_symbol,
                "max_leverage": candidate.margin_max_leverage,
                "warnings": candidate.margin_warnings or [],
            } if direction == "SHORT" else None,
            "market_data_quality": asdict(candidate.market_data_validation) if is_dataclass(candidate.market_data_validation) else None,
            "execution_evidence": asdict(candidate.execution_validation) if is_dataclass(candidate.execution_validation) else None,
            "independent_market_reference": asdict(candidate.independent_market_reference) if is_dataclass(candidate.independent_market_reference) else None,
            "news_context": asdict(candidate.news_context) if is_dataclass(candidate.news_context) else None,
            "scheduled_catalyst_context": asdict(candidate.scheduled_catalyst_context) if is_dataclass(candidate.scheduled_catalyst_context) else None,
            "liquidity_context": {
                "underlying_asset": candidate.underlying_asset or candidate.symbol,
                "primary_pair": candidate.primary_pair or candidate.symbol,
                "secondary_pair": candidate.secondary_pair,
                "primary_quote_currency": candidate.primary_quote_currency,
                "primary_24h_liquidity_usd": round(candidate.primary_24h_liquidity_usd, 2),
                "secondary_24h_liquidity_usd": round(candidate.secondary_24h_liquidity_usd, 2),
                "combined_24h_liquidity_usd": round(candidate.combined_24h_liquidity_usd, 2),
                "liquidity_rank": candidate.liquidity_rank,
                "primary_volume_ratio": round(candidate.volume_ratio, 2),
                "secondary_volume_ratio": candidate.secondary_volume_ratio,
                "cross_pair_confirmation_status": candidate.cross_pair_confirmation_status,
                "cross_pair_strengths": candidate.cross_pair_strengths or [],
                "cross_pair_warnings": candidate.cross_pair_warnings or [],
                "cross_pair_price_divergence_pct": candidate.cross_pair_price_divergence_pct,
                "cross_pair_price_status": candidate.cross_pair_price_status,
            },
            "market_structure": {
                "distance_to_24h_high_pct": round(candidate.distance_to_24h_high_pct, 2),
                "distance_to_72h_high_pct": round(candidate.distance_to_72h_high_pct, 2),
                "distance_to_24h_low_pct": round(candidate.distance_to_24h_low_pct, 2),
                "distance_to_72h_low_pct": round(candidate.distance_to_72h_low_pct, 2),
                "momentum_6h_pct": round(candidate.momentum_6h_pct, 2),
                "momentum_24h_pct": round(candidate.momentum_24h_pct, 2),
                "momentum_72h_pct": round(candidate.momentum_72h_pct, 2),
                "realized_range_24h_pct": round(candidate.realized_range_24h_pct, 2),
                "realized_range_72h_pct": round(candidate.realized_range_72h_pct, 2),
                "rolling_24h_range_percentiles": {
                    "p50": round(candidate.rolling_24h_range_median_pct, 2),
                    "p75": round(candidate.rolling_24h_range_p75_pct, 2),
                    "p90": round(candidate.rolling_24h_range_p90_pct, 2),
                },
                "rolling_72h_range_percentiles": {
                    "p50": round(candidate.rolling_72h_range_median_pct, 2),
                    "p75": round(candidate.rolling_72h_range_p75_pct, 2),
                    "p90": round(candidate.rolling_72h_range_p90_pct, 2),
                },
                "rolling_24h_long_upside_percentiles": {
                    "p50": round(candidate.rolling_24h_upside_median_pct, 2),
                    "p75": round(candidate.rolling_24h_upside_p75_pct, 2),
                    "p90": round(candidate.rolling_24h_upside_p90_pct, 2),
                },
                "rolling_72h_long_upside_percentiles": {
                    "p50": round(candidate.rolling_72h_upside_median_pct, 2),
                    "p75": round(candidate.rolling_72h_upside_p75_pct, 2),
                    "p90": round(candidate.rolling_72h_upside_p90_pct, 2),
                },
                "rolling_24h_short_downside_percentiles": {
                    "p50": round(candidate.rolling_24h_downside_median_pct, 2),
                    "p75": round(candidate.rolling_24h_downside_p75_pct, 2),
                    "p90": round(candidate.rolling_24h_downside_p90_pct, 2),
                },
                "rolling_72h_short_downside_percentiles": {
                    "p50": round(candidate.rolling_72h_downside_median_pct, 2),
                    "p75": round(candidate.rolling_72h_downside_p75_pct, 2),
                    "p90": round(candidate.rolling_72h_downside_p90_pct, 2),
                },
            },
        }
        if quality_by_risk_level is not None:
            candidate_context["deterministic_quality_by_risk_level"] = quality_by_risk_level
        tradingview_evidence = getattr(candidate, "_tradingview_evidence", None)
        if tradingview_evidence is not None:
            candidate_context["tradingview_candidate_evidence"] = tradingview_evidence
        candidate_context["price_movement_intelligence"] = candidate.price_movement_signal
        payload.append(candidate_context)
        chief_eligible_candidates.append(candidate)
        stage_evidence["eligible"].append(
            {"symbol": candidate.symbol, "direction": direction}
        )

    stage_evidence["eligible_candidate_count"] = len(payload)
    if account_equity is not None and not payload:
        return _no_trade_review(
            "Chief API skipped: no LONG/SHORT finalist can pass both deterministic target and economic gates under low or medium risk.",
            stage_evidence=stage_evidence,
        )

    request_payload = _request_payload(
        payload,
        market_regime_context=market_regime_context,
        coingecko_global_context=coingecko_global_context,
    )
    fingerprint = build_chief_fingerprint(request_payload)
    cached = get_cached_review(fingerprint)
    if cached is not None:
        cached["chief_api_skipped"] = True
        cached["chief_cache_reused"] = True
        cached["chief_eligible_candidates"] = len(payload)
        stage_evidence["invocation_status"] = CHIEF_CACHE_REUSED
        stage_evidence["returned_candidate_count"] = len(
            cached.get("top_candidates") or []
        )
        cached["opip_stage_evidence"] = stage_evidence
        return cached

    blocked = budget_block_reason()
    if blocked:
        _trace_chief_failure(
            chief_eligible_candidates,
            reason_code="CHIEF_BUDGET_LIMIT",
            detail=blocked,
        )
        stage_evidence["invocation_status"] = CHIEF_BUDGET_BLOCKED
        stage_evidence["failure_type"] = "CHIEF_BUDGET_LIMIT"
        return _no_trade_review(
            f"Chief API skipped: {blocked}.",
            failure_code="CHIEF_BUDGET_LIMIT",
            eligible_candidates=len(payload),
            stage_evidence=stage_evidence,
        )

    try:
        effort = _reasoning_effort()
        max_output_tokens = _max_output_tokens()
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            reasoning={"effort": effort},
            max_output_tokens=max_output_tokens,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request_payload)},
            ],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            record = append_usage_record(
                model=model,
                reasoning_effort=effort,
                candidate_count=len(payload),
                usage=usage,
            )
            print(
                "OPENAI USAGE "
                f"Model={record['model']} Candidates={record['candidate_count']} "
                f"Input={record['input_tokens']} Cached={record['cached_input_tokens']} "
                f"Output={record['output_tokens']} Reasoning={record['reasoning_tokens']} "
                f"Total={record['total_tokens']}"
            )

        review = json.loads(response.output_text)
        if not isinstance(review, dict):
            raise ValueError("Chief response JSON was not an object")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        _trace_chief_failure(
            chief_eligible_candidates,
            reason_code="CHIEF_UNAVAILABLE",
            detail=detail,
        )
        print(f"CHIEF SUPPRESSED Reason=CHIEF_UNAVAILABLE Error={type(exc).__name__}")
        stage_evidence["invocation_status"] = CHIEF_FAILED
        stage_evidence["failure_type"] = type(exc).__name__
        stage_evidence["invoked_at"] = datetime.now(timezone.utc).isoformat()
        return _no_trade_review(
            f"Chief unavailable; fail-closed: {type(exc).__name__}.",
            failure_code="CHIEF_UNAVAILABLE",
            eligible_candidates=len(payload),
            stage_evidence=stage_evidence,
        )

    review["chief_api_skipped"] = False
    review["chief_cache_reused"] = False
    review["chief_eligible_candidates"] = len(payload)
    stage_evidence["invocation_status"] = CHIEF_SUCCEEDED
    stage_evidence["invoked_at"] = datetime.now(timezone.utc).isoformat()
    stage_evidence["returned_candidate_count"] = len(
        review.get("top_candidates") or []
    )
    review["opip_stage_evidence"] = stage_evidence
    store_cached_review(fingerprint, review)
    try:
        review["learning_capture"] = capture_chief_review_decisions(
            review,
            eligible_candidates=chief_eligible_candidates,
            market_regime_context=market_regime_context,
        )
    except Exception:
        review["learning_capture"] = {
            "captured": 0,
            "qualified_alerts_deferred": 0,
            "not_selected": 0,
            "unmatched": 0,
        }
    return review
