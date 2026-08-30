from __future__ import annotations

from typing import Any, Iterable

from app.services.recommendation_gate import (
    ALLOWED_DIRECTIONS,
    ALLOWED_RISK_LEVELS,
    MIN_CONFIDENCE,
)
from app.services.shadow_decision_capture import capture_snapshot_decision


def _regime_name(context: object | None) -> str:
    value = getattr(context, "regime", None)
    return str(value or "UNKNOWN").upper()


def _candidate_key(candidate: Any) -> tuple[str, str]:
    return (
        str(getattr(candidate, "symbol", "") or "").upper(),
        str(getattr(candidate, "trade_direction", "LONG") or "LONG").upper(),
    )


def _review_key(candidate: dict[str, Any]) -> tuple[str, str]:
    return (
        str(candidate.get("symbol") or "").upper(),
        str(candidate.get("direction") or "LONG").upper(),
    )


def _market_intelligence_with_ai_route(
    snapshot: Any,
    review: dict[str, Any],
) -> dict[str, Any] | None:
    route = review.get("opip_ai_route")
    existing = getattr(snapshot, "_wave8_market_intelligence", None)
    payload = dict(existing) if isinstance(existing, dict) else {}
    if isinstance(route, dict):
        payload["ai_route"] = {
            "provider": route.get("provider"),
            "model": route.get("model"),
            "route_tier": route.get("route_tier"),
            "route_reason": route.get("route_reason"),
            "reasoning_effort": route.get("reasoning_effort"),
            "advisory_only": True,
            "funded_trade_authority_changed": False,
        }
    return payload or None


def _qualified_ai_alert(candidate: dict[str, Any]) -> bool:
    decision = str(candidate.get("decision") or "").lower()
    risk_level = str(candidate.get("risk_level") or "").lower()
    direction = str(candidate.get("direction") or "LONG").upper()
    try:
        confidence = int(candidate.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    return (
        decision == "alert"
        and confidence >= MIN_CONFIDENCE
        and risk_level in ALLOWED_RISK_LEVELS
        and direction in ALLOWED_DIRECTIONS
    )


def _non_alert_label(candidate: dict[str, Any]) -> str:
    decision = str(candidate.get("decision") or "").lower()
    risk_level = str(candidate.get("risk_level") or "").lower()
    direction = str(candidate.get("direction") or "LONG").upper()
    try:
        confidence = int(candidate.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0

    if decision == "watch":
        return "AI_WATCH"
    if decision == "reject":
        return "AI_REJECT"
    if decision == "alert" and direction not in ALLOWED_DIRECTIONS:
        return "AI_DIRECTION_REJECT"
    if decision == "alert" and risk_level not in ALLOWED_RISK_LEVELS:
        return "AI_RISK_REJECT"
    if decision == "alert" and confidence < MIN_CONFIDENCE:
        return "AI_CONFIDENCE_REJECT"
    return "AI_REJECT"


def capture_prefilter_rejection(
    candidate: Any,
    *,
    quality_by_risk_level: dict[str, dict[str, Any]],
    market_regime_context: object | None,
) -> bool:
    """Capture a finalist rejected before the paid Chief call.

    chief_analyst performs target/economic viability screening before building
    the OpenAI payload. Without this hook those decisions disappear from the
    learning dataset and OHM cannot later verify whether skipping them was
    profitable. The write is fail-open through capture_snapshot_decision.
    """
    levels = list(quality_by_risk_level.values())
    target_passes = any(bool(level.get("target_quality_qualified")) for level in levels)
    label = "ECONOMIC_REJECT" if target_passes else "TARGET_REJECT"

    reasons: list[str] = []
    for risk_level, level in quality_by_risk_level.items():
        target_rejections = level.get("target_quality_rejections") or []
        economic_rejection = level.get("economic_rejection")
        parts: list[str] = []
        if target_rejections:
            parts.append("target=" + "; ".join(str(x) for x in target_rejections))
        if economic_rejection:
            parts.append("economic=" + str(economic_rejection))
        if parts:
            reasons.append(f"{risk_level}: " + " | ".join(parts))

    return capture_snapshot_decision(
        candidate,
        decision=label,
        market_regime=_regime_name(market_regime_context),
        reason="; ".join(reasons) or "Chief deterministic viability prefilter rejected candidate",
        source="chief_deterministic_prefilter",
    )


def capture_chief_review_decisions(
    review: dict[str, Any],
    *,
    eligible_candidates: Iterable[Any],
    market_regime_context: object | None,
) -> dict[str, int]:
    """Capture every meaningful Chief non-entry decision.

    Qualified ALERT candidates are deliberately not captured here because the
    downstream deterministic/ranking path records their final ENTER_NOW or WAIT
    action. This prevents one opportunity from being double-counted as both an
    AI alert and a final trade decision.

    Candidates compared by the Chief but omitted from the maximum-three result
    are recorded as AI_NOT_SELECTED so rank misses also become measurable.
    """
    snapshots = {_candidate_key(candidate): candidate for candidate in eligible_candidates}
    returned_keys: set[tuple[str, str]] = set()
    captured = 0
    qualified_alerts = 0
    unmatched = 0

    for item in review.get("top_candidates", []) or []:
        if not isinstance(item, dict):
            continue
        key = _review_key(item)
        snapshot = snapshots.get(key)
        if snapshot is None:
            unmatched += 1
            continue
        returned_keys.add(key)
        if _qualified_ai_alert(item):
            qualified_alerts += 1
            continue

        label = _non_alert_label(item)
        reason = str(item.get("reason") or "Chief did not qualify candidate for an alert")
        confidence = item.get("confidence")
        risk_level = item.get("risk_level")
        detail = f"{reason} | confidence={confidence} risk={risk_level} decision={item.get('decision')}"
        if capture_snapshot_decision(
            snapshot,
            decision=label,
            market_regime=_regime_name(market_regime_context),
            reason=detail,
            source="chief_ai_review",
            market_intelligence=_market_intelligence_with_ai_route(snapshot, review),
        ):
            captured += 1

    not_selected = 0
    if not bool(review.get("chief_api_skipped")):
        for key, snapshot in snapshots.items():
            if key in returned_keys:
                continue
            if capture_snapshot_decision(
                snapshot,
                decision="AI_NOT_SELECTED",
                market_regime=_regime_name(market_regime_context),
                reason="Candidate was compared by Chief but omitted from the maximum-three top_candidates result",
                source="chief_ai_review",
                market_intelligence=_market_intelligence_with_ai_route(snapshot, review),
            ):
                captured += 1
                not_selected += 1

    return {
        "captured": captured,
        "qualified_alerts_deferred": qualified_alerts,
        "not_selected": not_selected,
        "unmatched": unmatched,
    }
