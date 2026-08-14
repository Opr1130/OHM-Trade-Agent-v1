from __future__ import annotations

from typing import Any, Iterable

from app.services.decision_learning import decision_quality_summary
from app.services.execution_analytics import execution_quality_summary
from app.services.historical_analytics import historical_summary


def build_edge_validation_report(
    *,
    outcomes: list[dict[str, Any]] | None = None,
    shadow_records: Iterable[dict[str, Any]] | None = None,
    execution_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one evidence report for profitability, decisions and execution.

    This report is deliberately advisory. It surfaces whether each evidence
    class has enough samples; it never promotes a strategy or changes live
    configuration by itself.
    """
    historical = historical_summary(outcomes)
    decisions = decision_quality_summary(shadow_records or [])
    execution = execution_quality_summary(execution_records)

    historical_ready = bool(historical["overall"].get("sufficient_samples"))
    decision_ready = decisions.get("evaluated", 0) >= decisions.get("thresholds", {}).get(
        "minimum_bucket_samples", 8
    )
    execution_ready = bool(execution["overall"].get("sufficient_samples"))

    return {
        "status": "EVIDENCE_READY" if historical_ready and decision_ready and execution_ready else "BUILDING_EVIDENCE",
        "historical": historical,
        "decision_quality": decisions,
        "execution_quality": execution,
        "readiness": {
            "historical": historical_ready,
            "decision_quality": decision_ready,
            "execution_quality": execution_ready,
            "all": historical_ready and decision_ready and execution_ready,
        },
        "auto_promotion_allowed": False,
    }
