from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutonomyReadiness:
    state: str
    unattended_allowed: bool
    reasons: tuple[str, ...]


def evaluate_autonomy_readiness(
    *,
    recent_ci_green: bool,
    reconciliation_healthy: bool,
    alerting_healthy: bool,
    stale_data_detected: bool,
    unresolved_execution_errors: int,
) -> AutonomyReadiness:
    reasons: list[str] = []
    if not recent_ci_green:
        reasons.append("recent CI is not green")
    if not reconciliation_healthy:
        reasons.append("reconciliation is unhealthy")
    if not alerting_healthy:
        reasons.append("operator alerting is unhealthy")
    if stale_data_detected:
        reasons.append("stale market data detected")
    if unresolved_execution_errors > 0:
        reasons.append("unresolved execution errors remain")
    if reasons:
        return AutonomyReadiness("BLOCKED", False, tuple(reasons))
    return AutonomyReadiness("READY_FOR_SUPERVISED_AUTOMATION", True, ())
