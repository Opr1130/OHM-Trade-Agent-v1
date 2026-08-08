import json

from openai import OpenAI

from app.scanner.models import MarketSnapshot
from app.scanner.technical_scorer import score_snapshot


SYSTEM_PROMPT = """You are OHM AI's Chief Investment Analyst and Risk Advisor.

Your job is to compare technically qualified crypto candidates and identify which, if any, deserve human attention.

Do not place trades.
Do not assume every high technical score is investable.
Consider:
- trend quality
- RSI extension
- MACD confirmation
- volume quality
- volatility
- relative strength versus other candidates
- risk of chasing momentum
- whether waiting is preferable

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
) -> dict:
    client = OpenAI(api_key=api_key)

    payload = []

    for candidate in candidates:
        card = score_snapshot(candidate)

        payload.append(
            {
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
            }
        )

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
