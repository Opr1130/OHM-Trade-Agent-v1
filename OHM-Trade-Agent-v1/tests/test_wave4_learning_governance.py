from app.services.learning_governance import evaluate_learning_promotion


def test_learning_requires_enough_samples():
    decision = evaluate_learning_promotion(samples=12, expectancy=5, profit_factor=1.8, max_drawdown_pct=3)
    assert decision.state == "INSUFFICIENT_DATA"
    assert decision.auto_apply_allowed is False


def test_learning_rejects_negative_expectancy():
    decision = evaluate_learning_promotion(samples=40, expectancy=-1, profit_factor=1.8, max_drawdown_pct=3)
    assert decision.state == "REJECT"


def test_learning_can_only_reach_human_review():
    decision = evaluate_learning_promotion(samples=40, expectancy=4, profit_factor=1.5, max_drawdown_pct=4)
    assert decision.state == "READY_FOR_HUMAN_REVIEW"
    assert decision.eligible_for_review is True
    assert decision.auto_apply_allowed is False
