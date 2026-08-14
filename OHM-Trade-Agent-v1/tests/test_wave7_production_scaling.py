from app.services.production_scaling import evaluate_production_stage


def _decision(**overrides):
    args = dict(
        current_stage="PAPER",
        edge_evidence_ready=True,
        out_of_sample_profit_factor=1.5,
        max_drawdown_pct=4.0,
        execution_adverse_fill_rate_pct=20.0,
        operations_ready=True,
        human_approval=True,
    )
    args.update(overrides)
    return evaluate_production_stage(**args)


def test_production_scaling_requires_human_approval():
    decision = _decision(human_approval=False)
    assert decision.advance_allowed is False
    assert decision.recommended_stage == "PAPER"
    assert "human approval" in decision.reason


def test_production_scaling_advances_only_one_stage():
    decision = _decision(current_stage="PAPER")
    assert decision.advance_allowed is True
    assert decision.recommended_stage == "TINY_LIVE"
    assert decision.max_capital_fraction == 0.05


def test_production_scaling_blocks_poor_execution():
    decision = _decision(execution_adverse_fill_rate_pct=60.0)
    assert decision.advance_allowed is False
    assert "execution quality" in decision.reason


def test_scaled_live_is_capped_and_never_auto_expands():
    decision = _decision(current_stage="SCALED_LIVE")
    assert decision.advance_allowed is False
    assert decision.max_capital_fraction == 0.30
