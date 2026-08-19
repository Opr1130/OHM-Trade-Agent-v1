from app.services.counterfactual_gate_audit import (
    build_counterfactual_gate_audit,
    classify_counterfactual_rejection,
)


def _row(decision="AI_REJECT", **overrides):
    row = {
        "decision": decision,
        "direction": "LONG",
        "reference_price": 100.0,
        "observations": {
            "5m": {"directional_move_pct": 0.5, "price": 100.5},
            "15m": {"directional_move_pct": 1.0, "price": 101.0},
            "1h": {"directional_move_pct": 2.5, "price": 102.5},
            "24h": {"directional_move_pct": 3.0, "price": 103.0},
        },
    }
    row.update(overrides)
    return row


def test_false_reject_requires_material_upside_without_sampled_avoided_loss():
    result = classify_counterfactual_rejection(_row())
    assert result["classification"] == "FALSE_REJECT_HEURISTIC"
    assert result["heuristic_only"] is True
    assert result["does_not_prove_executability"] is True


def test_safety_gate_is_never_relabelled_as_false_reject():
    result = classify_counterfactual_rejection(_row("EXECUTION_REJECT"))
    assert result["classification"] == "SAFETY_REJECT_WITH_LATER_UPSIDE"
    assert result["gate_family"] == "SAFETY"


def test_good_reject_detects_adverse_path_without_material_upside():
    result = classify_counterfactual_rejection(
        _row(
            observations={
                "5m": {"directional_move_pct": -0.5},
                "1h": {"directional_move_pct": -2.5},
                "24h": {"directional_move_pct": -1.0},
            }
        )
    )
    assert result["classification"] == "GOOD_REJECT"


def test_right_idea_wrong_entry_when_early_path_is_bad_then_later_recovers():
    result = classify_counterfactual_rejection(
        _row(
            observations={
                "5m": {"directional_move_pct": -0.4},
                "15m": {"directional_move_pct": 0.2},
                "1h": {"directional_move_pct": 2.7},
                "24h": {"directional_move_pct": 3.1},
            }
        )
    )
    assert result["classification"] == "RIGHT_IDEA_WRONG_ENTRY"


def test_target_reject_can_surface_target_too_ambitious_heuristic():
    result = classify_counterfactual_rejection(
        _row(
            "TARGET_REJECT",
            observations={
                "5m": {"directional_move_pct": 0.2},
                "1h": {"directional_move_pct": 1.3},
                "24h": {"directional_move_pct": 1.5},
            },
        )
    )
    assert result["classification"] == "TARGET_TOO_AMBITIOUS_HEURISTIC"


def test_summary_separates_safety_upside_from_false_reject_like():
    rows = [
        _row("AI_REJECT"),
        _row("EXECUTION_REJECT"),
        _row(
            "AI_WATCH",
            observations={
                "5m": {"directional_move_pct": -0.3},
                "1h": {"directional_move_pct": -2.2},
                "24h": {"directional_move_pct": -1.0},
            },
        ),
    ]
    audit = build_counterfactual_gate_audit(rows)
    assert audit["evaluated_rejections"] == 3
    assert audit["by_gate_family"]["AI"]["false_reject_like"] == 1
    assert audit["by_gate_family"]["SAFETY"]["safety_rejects_with_later_upside"] == 1
    assert audit["interpretation"]["automatic_tuning_applied"] is False
