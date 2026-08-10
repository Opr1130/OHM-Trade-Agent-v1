from typing import Any


MIN_CONFIDENCE = 85
ALLOWED_RISK_LEVELS = {"low", "medium"}
ALLOWED_DIRECTIONS = {"LONG", "SHORT"}


def qualified_alerts(review: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for candidate in review.get("top_candidates", []):
        decision = str(candidate.get("decision", "")).lower()
        risk_level = str(candidate.get("risk_level", "")).lower()
        direction = str(candidate.get("direction", "LONG")).upper()

        try:
            confidence = int(candidate.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0

        if (
            decision == "alert"
            and confidence >= MIN_CONFIDENCE
            and risk_level in ALLOWED_RISK_LEVELS
            and direction in ALLOWED_DIRECTIONS
        ):
            candidate["direction"] = direction
            results.append(candidate)

    return results
