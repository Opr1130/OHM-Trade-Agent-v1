"""The "why did O'Pip produce zero trades?" read model.

This is the reusable answer the Intelligence Cockpit needs. Build 1 provides
the data, not the visual: no dashboard layout changes here.

The read model is strictly read-only. It opens two append-only JSONL files and
returns a dictionary. It cannot rank, alert, admit, or change any threshold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.opip.decision.store import (
    opip_funnel_telemetry_enabled,
    read_funnel_events_for_scan,
    read_latest_scan_summary,
)


#: A scan summary older than this is reported as stale. The shipped production
#: scan cadence is well under an hour, so an hour without a summary means the
#: explanation describes history, not the current state.
DEFAULT_STALE_AFTER_MINUTES = 60

STATE_TELEMETRY_DISABLED = "TELEMETRY_DISABLED"
STATE_NO_DATA = "NO_SCAN_RECORDED"
STATE_FRESH = "FRESH"
STATE_STALE = "STALE"


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _empty(state: str, *, telemetry_enabled: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "state": state,
        "telemetry_enabled": telemetry_enabled,
        "read_only": True,
        "explanation": (
            "O'Pip qualification telemetry is not enabled, so no scan funnel "
            "has been recorded yet."
            if state == STATE_TELEMETRY_DISABLED
            else "No O'Pip qualification scan summary has been recorded yet."
        ),
        "last_scan_at_utc": None,
        "qualified": 0,
        "paper_admission_eligible": 0,
        "nearest_misses": [],
        "dominant_rejection_reasons": {},
        "operational_failure_state": "UNKNOWN",
    }


def _explanation_sentence(summary: dict[str, Any]) -> str:
    """Compose one plain-language sentence attributing the zero-trade state."""
    funnel = summary.get("funnel") or {}
    terminal = summary.get("terminal") or {}
    ai_stage = summary.get("ai_stage") or {}
    entered = int(funnel.get("entered", 0) or 0)
    qualified = int(funnel.get("qualified", 0) or 0)

    if qualified > 0:
        return f"{qualified} of {entered} directional candidates qualified."
    if entered == 0:
        return "No directional candidate entered the qualification funnel."
    eligible = int(ai_stage.get("eligible_candidates_before_ai", 0) or 0)
    earlier = max(0, entered - eligible)
    if ai_stage.get("budget_exhausted"):
        if eligible == entered:
            return (
                f"All {entered} directional candidates reached Chief review and "
                "were stopped because the daily AI budget guard suppressed the call."
            )
        return (
            f"{eligible} of {entered} directional candidates reached Chief review "
            "and were stopped because the daily AI budget guard suppressed the call; "
            f"{earlier} stopped earlier in the qualification funnel."
        )
    if ai_stage.get("unavailable"):
        failure = ai_stage.get("failure_type") or "unavailable"
        if eligible == entered:
            return (
                f"All {entered} directional candidates reached Chief review and "
                f"were stopped because the AI service was {failure}."
            )
        return (
            f"{eligible} of {entered} directional candidates reached Chief review "
            f"and were stopped because the AI service was {failure}; "
            f"{earlier} stopped earlier in the qualification funnel."
        )
    gate = terminal.get("dominant_terminal_gate") or "an unattributed gate"
    reasons = terminal.get("top_reasons") or {}
    lead = next(iter(reasons), None)
    if lead:
        return (
            f"{entered} directional candidates were analysed and none qualified; "
            f"the dominant stop was {gate} with reason {lead} "
            f"({reasons[lead]} candidates)."
        )
    return (
        f"{entered} directional candidates were analysed and none qualified; "
        f"the dominant stop was {gate}."
    )


def build_zero_trade_explanation(
    *,
    summaries_path: Path | None = None,
    events_path: Path | None = None,
    stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES,
    now: datetime | None = None,
    telemetry_enabled: bool | None = None,
    include_candidates: bool = False,
) -> dict[str, Any]:
    """Return the structured explanation for the most recent O'Pip scan.

    ``include_candidates`` additionally joins the per-candidate funnel rows for
    that scan. It is off by default because the cockpit summary does not need
    them and reading them costs a second file pass.
    """
    enabled = (
        opip_funnel_telemetry_enabled()
        if telemetry_enabled is None
        else bool(telemetry_enabled)
    )
    # Current control state is authoritative. Historical summaries may remain
    # on disk after telemetry is disabled (including rollback), but they must
    # never be presented as the current explanation.
    if not enabled:
        return _empty(STATE_TELEMETRY_DISABLED, telemetry_enabled=False)

    summary = read_latest_scan_summary(path=summaries_path)
    if summary is None:
        return _empty(STATE_NO_DATA, telemetry_enabled=True)

    moment = now or datetime.now(timezone.utc)
    recorded_at = _parse_utc(summary.get("decision_at_utc"))
    stale = (
        recorded_at is None
        or moment - recorded_at > timedelta(minutes=max(1, int(stale_after_minutes)))
    )

    funnel = summary.get("funnel") or {}
    terminal = summary.get("terminal") or {}
    ai_stage = summary.get("ai_stage") or {}
    scan = summary.get("scan") or {}
    operational = int(funnel.get("operationally_unresolved", 0) or 0)

    explanation: dict[str, Any] = {
        "schema_version": 1,
        "state": STATE_STALE if stale else STATE_FRESH,
        "telemetry_enabled": enabled,
        "read_only": True,
        "explanation": _explanation_sentence(summary),
        "scan_id": summary.get("scan_id"),
        "cohort_id": summary.get("cohort_id"),
        "last_scan_at_utc": summary.get("decision_at_utc"),
        "candidates_analyzed": scan.get("analyzed"),
        "technical_candidates": scan.get("technical_candidates"),
        "long_candidates": scan.get("long_candidates", 0),
        "short_candidates": scan.get("short_candidates", 0),
        "directional_candidates": funnel.get("entered", 0),
        "deepest_stage_reached": terminal.get("deepest_stage_reached"),
        "qualified": funnel.get("qualified", 0),
        "rejected_total": funnel.get("rejected_total", 0),
        "rejected_by_policy": funnel.get("rejected_by_policy", 0),
        "rejected_by_budget": funnel.get("rejected_by_budget", 0),
        "rejected_by_model": funnel.get("rejected_by_model", 0),
        "rejected_other": funnel.get("rejected_other", 0),
        "paper_admission_eligible": summary.get("paper_admission_eligible", 0),
        "dominant_terminal_gate": terminal.get("dominant_terminal_gate"),
        "dominant_rejection_reasons": terminal.get("top_reasons") or {},
        "reason_classes": terminal.get("reason_classes") or {},
        "nearest_misses": summary.get("nearest_misses") or [],
        "operational_failure_state": "PRESENT" if operational else "NONE",
        "operational_failures": funnel.get("operational_failures", 0),
        "incomplete": funnel.get("incomplete", 0),
        "ai_stage_reached": bool(summary.get("ai_stage_reached")),
        "ai_invoked": bool(ai_stage.get("invoked")),
        "ai_unavailable": bool(ai_stage.get("unavailable")),
        "ai_budget_exhausted": bool(ai_stage.get("budget_exhausted")),
        "ai_invocation_status": ai_stage.get("invocation_status"),
        "ai_failure_type": ai_stage.get("failure_type"),
        "ai_eligible_candidates_before_ai": ai_stage.get(
            "eligible_candidates_before_ai", 0
        ),
        "ai_candidates_returned": ai_stage.get("candidates_returned_by_ai", 0),
        "ai_confidence_distribution": ai_stage.get("confidence_summary") or {},
        "funnel_invariant_holds": bool(summary.get("invariant_holds")),
        "shadow_comparison": summary.get("shadow_comparison") or {},
        "strategy_version": summary.get("strategy_version"),
        "intelligence_version": summary.get("intelligence_version"),
        "gate_policy_version": summary.get("gate_policy_version"),
        "gate_policy_fingerprint": summary.get("gate_policy_fingerprint"),
        "feature_schema_version": summary.get("feature_schema_version"),
        "model_version": summary.get("model_version"),
    }

    if include_candidates:
        explanation["candidates"] = read_funnel_events_for_scan(
            str(summary.get("scan_id") or ""),
            path=events_path,
        )
    return explanation
