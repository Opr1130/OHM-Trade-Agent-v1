import json

from openai import OpenAI

from app.scanner.models import MarketSnapshot
from app.scanner.technical_scorer import score_snapshot
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.target_attainability import evaluate_target_attainability


SYSTEM_PROMPT = """You are OHM AI's Chief Investment Analyst and Risk Advisor.

Your job is to compare technically qualified crypto candidates and identify which, if any, deserve human attention.

Do not place trades.
Do not assume every high technical score is investable.
Each payload item is one unique underlying asset. Never return duplicate
candidates for its USD and USDT markets. Use the exact primary_pair value as
the candidate symbol; primary market selection is analysis context, not an
execution or currency-conversion recommendation.
Consider:
- trend quality
- RSI extension
- MACD confirmation
- volume quality
- volatility
- relative strength versus other candidates
- risk of chasing momentum
- whether waiting is preferable
- calculated target-quality, resistance, momentum, and economic-gate context

Target-quality scores are deterministic opportunity-quality scores, not a
probability of success. AI confidence is comparative review confidence, not a
win probability. Do not recalculate supplied deterministic metrics.

Economic assumed capital is ACCOUNT_EQUITY used only as validation capital for
hypothetical economic comparison. It is not a recommended allocation. Capital
allocation is outside this review and will be implemented separately.

Return JSON only in this exact shape:

{
  "market_view": "",
  "recommended_action": "alert|watch|no_trade",
  "top_candidates": [
    {
      "symbol": "",
      "rank": 1,
      "confidence": 0,
      "risk_level": "low|medium|high",
      "decision": "alert|watch|reject",
      "reason": ""
    }
  ],
  "summary": ""
}

Maximum 3 top_candidates.
Confidence must be 0-100.
Be conservative.
"""


def review_candidates(
    candidates: list[MarketSnapshot],
    model: str,
    api_key: str,
    account_equity: float | None = None,
) -> dict:
    client = OpenAI(api_key=api_key)

    payload = []

    unique_candidates: list[MarketSnapshot] = []
    seen_assets: set[str] = set()
    for candidate in candidates:
        asset_key = candidate.underlying_asset or candidate.symbol
        if asset_key in seen_assets:
            continue
        seen_assets.add(asset_key)
        unique_candidates.append(candidate)

    for candidate in unique_candidates:
        card = score_snapshot(candidate)

        candidate_context = {
                "symbol": candidate.symbol,
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
                "liquidity_context": {
                    "underlying_asset": (
                        candidate.underlying_asset or candidate.symbol
                    ),
                    "primary_pair": candidate.primary_pair or candidate.symbol,
                    "secondary_pair": candidate.secondary_pair,
                    "primary_quote_currency": candidate.primary_quote_currency,
                    "primary_24h_liquidity_usd": round(
                        candidate.primary_24h_liquidity_usd, 2
                    ),
                    "secondary_24h_liquidity_usd": round(
                        candidate.secondary_24h_liquidity_usd, 2
                    ),
                    "combined_24h_liquidity_usd": round(
                        candidate.combined_24h_liquidity_usd, 2
                    ),
                    "liquidity_rank": candidate.liquidity_rank,
                    "primary_volume_ratio": round(candidate.volume_ratio, 2),
                    "secondary_volume_ratio": candidate.secondary_volume_ratio,
                    "cross_pair_confirmation_status": (
                        candidate.cross_pair_confirmation_status
                    ),
                    "cross_pair_strengths": candidate.cross_pair_strengths or [],
                    "cross_pair_warnings": candidate.cross_pair_warnings or [],
                },
                "market_structure": {
                    "distance_to_24h_high_pct": round(candidate.distance_to_24h_high_pct, 2),
                    "distance_to_72h_high_pct": round(candidate.distance_to_72h_high_pct, 2),
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
                },
            }

        if account_equity is not None:
            quality_by_risk_level = {}
            for risk_level in ("low", "medium"):
                plan = build_entry_exit_plan(candidate, risk_level)
                target_quality = evaluate_target_attainability(plan, candidate)
                economic = evaluate_economic_quality(plan, account_equity)
                quality_by_risk_level[risk_level] = {
                    "target_quality_score": target_quality.attainability_score,
                    "target_quality_qualified": target_quality.qualified,
                    "target_quality_warnings": target_quality.warnings,
                    "target_quality_rejections": target_quality.rejection_reasons,
                    "target_2_atr_multiple": target_quality.target_2_atr_multiple,
                    "resistance_clearance_24h_pct": target_quality.clearance_to_24h_resistance_pct,
                    "resistance_clearance_72h_pct": target_quality.clearance_to_72h_resistance_pct,
                    "momentum_context": target_quality.momentum_context,
                    "economic_qualified": economic.qualified,
                    "economic_rejection": economic.rejection_reason,
                    "economic_assumed_capital": economic.recommended_capital,
                    "hypothetical_target_2_net_profit_at_assumed_capital": (
                        economic.target_2_net_profit
                    ),
                }
            candidate_context["deterministic_quality_by_risk_level"] = quality_by_risk_level

        payload.append(candidate_context)

    response = client.responses.create(
        model=model,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "candidate_count": len(payload),
                        "candidates": payload,
                    }
                ),
            },
        ],
    )

    return json.loads(response.output_text)
