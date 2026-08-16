import json
import os
from dataclasses import asdict, is_dataclass

from openai import OpenAI

from app.scanner.models import MarketSnapshot
from app.scanner.short_technical_scorer import score_short_snapshot
from app.scanner.technical_scorer import score_snapshot
from app.services.chief_learning_capture import (
    capture_chief_review_decisions,
    capture_prefilter_rejection,
)
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.openai_usage_telemetry import append_usage_record
from app.services.short_target_attainability import evaluate_short_target_attainability
from app.services.target_attainability import evaluate_target_attainability


SYSTEM_PROMPT = """You are OHM AI's Chief Investment Analyst and Risk Advisor.

Compare already-screened crypto opportunities and identify which, if any, deserve human attention. Candidates may be LONG or SHORT. Do not place trades and never change the supplied direction.

SHORT candidates have already passed Kraken US retail margin pair eligibility before reaching you. Margin availability is evidence of tradability, not a recommendation to maximize leverage. OHM v1 models SHORT economics at a conservative 2x validation exposure and does not authorize automatic margin orders. Dynamic opening and rollover rates can change at execution time; the supplied margin-cost reserve is an estimate and must not be presented as a guaranteed fee.

Each payload item is one unique underlying asset/direction. Use the exact primary_pair value as the candidate symbol and return its exact direction. Do not invent a different pair or direction.

Consider trend quality, RSI extension, MACD confirmation, volume quality, volatility, relative strength/weakness, chase risk, execution evidence, deterministic target-quality and economic context. For SHORT candidates, favor bearish continuation with realistic downside room; do not recommend shorting merely because the broad regime is RISK_OFF.

Target-quality scores are deterministic opportunity-quality scores, not probabilities. AI confidence is comparative review confidence, not win probability. Do not recalculate supplied deterministic metrics.

Kraken market-data and execution values are deterministic evidence. PreTrade contains only the top 10 aggregated levels; incomplete depth is partial observed liquidity, never zero liquidity. SHORT entry depends on bid-side sellability and eventual cover depends on ask-side buyback liquidity. Never convert execution coverage into probability.

CoinGecko is independent aggregated reference evidence, not an execution venue. AMBIGUOUS means OHM selected no external identity and no price/divergence conclusion may be drawn. UNAVAILABLE is missing evidence, not automatic rejection. CryptoPanic news is associated only after identity-safe structured instrument matching; ticker-only attribution is refused. News/catalyst evidence can be missing or unresolved and is never a substitute for safe identity matching.

Market regime is deterministic breadth context, not a probability. RISK_OFF is caution for longs and context for shorts, not an automatic direction signal. Economic assumed capital is validation capital, not allocation advice.

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


def _quality_by_risk_level(
    candidate: MarketSnapshot,
    account_equity: float,
) -> tuple[dict, bool]:
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
                direction="SHORT",
                leverage=SHORT_VALIDATION_LEVERAGE,
                estimated_margin_cost_pct=SHORT_MARGIN_COST_RESERVE_PCT,
                max_account_risk_at_stop_pct=SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
            )
        else:
            target = evaluate_target_attainability(plan, candidate)
            economic = evaluate_economic_quality(plan, account_equity)
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
            "economic_account_risk_at_stop_pct": getattr(
                economic, "account_risk_at_stop_pct", 0.0
            ),
            "hypothetical_target_2_net_profit_at_assumed_capital": economic.target_2_net_profit,
        }
    return quality_by_risk_level, viable_any


def _no_trade_review(reason: str) -> dict:
    return {
        "market_view": "",
        "recommended_action": "no_trade",
        "top_candidates": [],
        "summary": reason,
        "chief_api_skipped": True,
        "chief_eligible_candidates": 0,
    }


def review_candidates(
    candidates: list[MarketSnapshot],
    model: str,
    api_key: str,
    account_equity: float | None = None,
    market_regime_context: object | None = None,
    coingecko_global_context: object | None = None,
) -> dict:
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
        # Wave 8.2: optional corroborating/candidate evidence attached by
        # merge_native_candidate_evidence / tradingview_only_candidates
        # (app/services/tradingview_inbox.py). Context only — the Chief AI
        # review never receives this as a reason to bypass any deterministic
        # gate, and it is absent whenever no matching evidence exists.
        tradingview_evidence = getattr(candidate, "_tradingview_evidence", None)
        if tradingview_evidence is not None:
            candidate_context["tradingview_candidate_evidence"] = tradingview_evidence
        payload.append(candidate_context)
        chief_eligible_candidates.append(candidate)

    if account_equity is not None and not payload:
        return _no_trade_review(
            "Chief API skipped: no LONG/SHORT finalist can pass both deterministic target and economic gates under low or medium risk."
        )

    effort = _reasoning_effort()
    max_output_tokens = _max_output_tokens()
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        reasoning={"effort": effort},
        max_output_tokens=max_output_tokens,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({
                    "candidate_count": len(payload),
                    "market_regime_context": {
                        "ohm_breadth": asdict(market_regime_context) if is_dataclass(market_regime_context) else None,
                        "coingecko_global": asdict(coingecko_global_context) if is_dataclass(coingecko_global_context) else None,
                    },
                    "candidates": payload,
                }),
            },
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
    review["chief_api_skipped"] = False
    review["chief_eligible_candidates"] = len(payload)
    try:
        review["learning_capture"] = capture_chief_review_decisions(
            review,
            eligible_candidates=chief_eligible_candidates,
            market_regime_context=market_regime_context,
        )
    except Exception:
        # Learning telemetry must never block or alter a live trading decision.
        review["learning_capture"] = {
            "captured": 0,
            "qualified_alerts_deferred": 0,
            "not_selected": 0,
            "unmatched": 0,
        }
    return review
