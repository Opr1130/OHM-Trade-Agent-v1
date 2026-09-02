from typing import Any


# Retained as a measurement/calibration boundary only. It is deliberately not
# a production authorization threshold: Chief confidence is comparative review
# confidence, not a calibrated win probability.
MIN_CONFIDENCE = 85
ALLOWED_RISK_LEVELS = {"low", "medium"}
ALLOWED_DIRECTIONS = {"LONG", "SHORT"}


def _parsed_confidence(candidate: dict[str, Any]) -> int | None:
    try:
        value = int(candidate.get("confidence"))
    except (TypeError, ValueError):
        return None
    if value < 0 or value > 100:
        return None
    return value


def candidate_alert_authorized(candidate: dict[str, Any]) -> bool:
    """Return whether a Chief item may continue to deterministic qualification.

    Confidence is required to be well-formed evidence, but its numeric value is
    not trade authority. WATCH/REJECT, invalid risk/direction, or malformed
    confidence remain fail-closed.
    """
    decision = str(candidate.get("decision", "")).lower()
    risk_level = str(candidate.get("risk_level", "")).lower()
    direction = str(candidate.get("direction", "LONG")).upper()
    return (
        decision == "alert"
        and risk_level in ALLOWED_RISK_LEVELS
        and direction in ALLOWED_DIRECTIONS
        and _parsed_confidence(candidate) is not None
    )


def confidence_below_measurement_boundary(candidate: dict[str, Any]) -> bool:
    value = _parsed_confidence(candidate)
    return value is not None and value < MIN_CONFIDENCE


def qualified_alerts(review: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for candidate in review.get("top_candidates", []):
        if not isinstance(candidate, dict):
            continue
        if candidate_alert_authorized(candidate):
            candidate["direction"] = str(candidate.get("direction", "LONG")).upper()
            results.append(candidate)

    return results
