from typing import Any


MIN_CONFIDENCE = 85
ALLOWED_RISK_LEVELS = {"low", "medium"}


def qualified_alerts(review: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for candidate in review.get("top_candidates", []):
        decision = str(candidate.get("decision", "")).lower()
        risk_level = str(candidate.get("risk_level", "")).lower()

        try:
            confidence = int(candidate.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0

        if (
            decision == "alert"
            and confidence >= MIN_CONFIDENCE
            and risk_level in ALLOWED_RISK_LEVELS
        ):
            results.append(candidate)

    return results
