from app.services.autonomy_readiness import evaluate_autonomy_readiness


def test_autonomy_blocks_on_stale_data_or_execution_errors():
    result = evaluate_autonomy_readiness(
        recent_ci_green=True,
        reconciliation_healthy=True,
        alerting_healthy=True,
        stale_data_detected=True,
        unresolved_execution_errors=1,
    )
    assert result.state == "BLOCKED"
    assert result.unattended_allowed is False
    assert len(result.reasons) == 2


def test_autonomy_requires_all_operational_gates_green():
    result = evaluate_autonomy_readiness(
        recent_ci_green=True,
        reconciliation_healthy=True,
        alerting_healthy=True,
        stale_data_detected=False,
        unresolved_execution_errors=0,
    )
    assert result.state == "READY_FOR_SUPERVISED_AUTOMATION"
    assert result.unattended_allowed is True
