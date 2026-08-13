from app.services.decision_learning import classify_shadow_decision, decision_quality_summary


def _row(decision, moves):
    return {
        "decision": decision,
        "observations": {
            horizon: {"directional_move_pct": move}
            for horizon, move in moves.items()
        },
    }


def test_rejected_candidate_can_be_labeled_missed_profit():
    result = classify_shadow_decision(
        _row("ECONOMIC_REJECT", {"5m": -0.2, "1h": 0.8, "24h": 3.4})
    )
    assert result["status"] == "EVALUATED"
    assert result["missed_profitable_opportunity"] is True
    assert result["correct_wait_or_reject"] is False


def test_wait_can_be_labeled_correct_avoidance():
    result = classify_shadow_decision(
        _row("WAIT", {"5m": -0.5, "1h": -2.3, "24h": -4.0})
    )
    assert result["missed_profitable_opportunity"] is False
    assert result["correct_wait_or_reject"] is True


def test_enter_records_immediate_vs_wait_edges():
    result = classify_shadow_decision(
        _row("ENTER_NOW", {"5m": -0.5, "15m": -1.0, "1h": 0.5, "24h": 4.0})
    )
    assert result["immediate_vs_wait_edge_pct"]["5m"] == 4.5
    assert result["immediate_vs_wait_edge_pct"]["15m"] == 5.0
    assert result["immediate_vs_wait_edge_pct"]["1h"] == 3.5


def test_summary_separates_misses_and_correct_rejections():
    summary = decision_quality_summary(
        [
            _row("WAIT", {"1h": -2.5, "24h": -3.0}),
            _row("TARGET_REJECT", {"1h": 1.0, "24h": 2.5}),
        ]
    )
    assert summary["status"] == "OK"
    assert summary["evaluated"] == 2
    assert summary["missed_profitable_opportunities"] == 1
    assert summary["correct_wait_or_reject"] == 1
