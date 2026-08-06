import json

from openai import OpenAI

from app.models.signal import TradingSignal


SYSTEM_PROMPT = """You are a conservative trade-signal reviewer.
You do not place trades. You assess whether a technically valid setup deserves a human alert.
Reject stale, contradictory, overextended, or poorly structured setups.
Return JSON only with keys: score (0-100 integer), verdict (alert/watch/reject), summary (max 40 words).
Never override deterministic risk controls.
"""


def review_signal(signal: TradingSignal, model: str, api_key: str) -> dict:
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(
                signal.model_dump(mode="json", exclude={"timestamp"})
            )},
        ],
    )
    result = json.loads(response.output_text)
    score = max(0, min(100, int(result["score"])))
    verdict = result.get("verdict", "reject")
    if verdict not in {"alert", "watch", "reject"}:
        verdict = "reject"
    return {"score": score, "verdict": verdict, "summary": str(result.get("summary", ""))[:240]}
