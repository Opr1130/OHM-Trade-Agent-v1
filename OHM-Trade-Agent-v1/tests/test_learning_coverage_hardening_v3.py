from types import SimpleNamespace

from app.services.decision_learning import is_non_entry_decision


def _candidate(symbol: str, direction: str = "LONG"):
    return SimpleNamespace(
        symbol=symbol,
        trade_direction=direction,
        last_price=10.0,
        technical_score=80.0,
        volume_ratio=1.5,
        execution_validation=None,
    )


def test_chief_review_captures_watch_reject_and_not_selected(monkeypatch):
    import app.services.chief_learning_capture as capture

    recorded = []

    def fake_capture(snapshot, **kwargs):
        recorded.append((snapshot.symbol, kwargs["decision"], kwargs["source"]))
        return True

    monkeypatch.setattr(capture, "capture_snapshot_decision", fake_capture)
    candidates = [_candidate("AAAUSD"), _candidate("BBBUSD"), _candidate("CCCUSD"), _candidate("DDDUSD")]
    review = {
        "chief_api_skipped": False,
        "top_candidates": [
            {"symbol": "AAAUSD", "direction": "LONG", "decision": "watch", "confidence": 78, "risk_level": "medium", "reason": "wait"},
            {"symbol": "BBBUSD", "direction": "LONG", "decision": "reject", "confidence": 40, "risk_level": "high", "reason": "weak"},
            {"symbol": "CCCUSD", "direction": "LONG", "decision": "alert", "confidence": 90, "risk_level": "low", "reason": "good"},
        ],
    }

    result = capture.capture_chief_review_decisions(
        review,
        eligible_candidates=candidates,
        market_regime_context=SimpleNamespace(regime="RISK_OFF"),
    )

    assert ("AAAUSD", "AI_WATCH", "chief_ai_review") in recorded
    assert ("BBBUSD", "AI_REJECT", "chief_ai_review") in recorded
    assert ("DDDUSD", "AI_NOT_SELECTED", "chief_ai_review") in recorded
    assert not any(symbol == "CCCUSD" for symbol, _, _ in recorded)
    assert result["captured"] == 3
    assert result["qualified_alerts_deferred"] == 1
    assert result["not_selected"] == 1


def test_chief_review_captures_low_confidence_alert(monkeypatch):
    import app.services.chief_learning_capture as capture

    recorded = []
    monkeypatch.setattr(
        capture,
        "capture_snapshot_decision",
        lambda snapshot, **kwargs: recorded.append(kwargs["decision"]) or True,
    )
    review = {
        "chief_api_skipped": False,
        "top_candidates": [
            {"symbol": "AAAUSD", "direction": "LONG", "decision": "alert", "confidence": 84, "risk_level": "low", "reason": "almost"},
        ],
    }
    capture.capture_chief_review_decisions(
        review,
        eligible_candidates=[_candidate("AAAUSD")],
        market_regime_context=None,
    )
    assert recorded == ["AI_CONFIDENCE_REJECT"]


def test_prefilter_rejection_distinguishes_target_from_economic(monkeypatch):
    import app.services.chief_learning_capture as capture

    recorded = []
    monkeypatch.setattr(
        capture,
        "capture_snapshot_decision",
        lambda snapshot, **kwargs: recorded.append(kwargs["decision"]) or True,
    )
    candidate = _candidate("AAAUSD")

    capture.capture_prefilter_rejection(
        candidate,
        quality_by_risk_level={
            "low": {"target_quality_qualified": False, "target_quality_rejections": ["far target"], "economic_rejection": "low dollars"},
            "medium": {"target_quality_qualified": False, "target_quality_rejections": ["far target"], "economic_rejection": "low dollars"},
        },
        market_regime_context=None,
    )
    capture.capture_prefilter_rejection(
        candidate,
        quality_by_risk_level={
            "low": {"target_quality_qualified": True, "target_quality_rejections": [], "economic_rejection": "low dollars"},
            "medium": {"target_quality_qualified": False, "target_quality_rejections": ["far target"], "economic_rejection": "low dollars"},
        },
        market_regime_context=None,
    )

    assert recorded == ["TARGET_REJECT", "ECONOMIC_REJECT"]


def test_new_non_entry_labels_are_future_proofed():
    assert is_non_entry_decision("AI_WATCH")
    assert is_non_entry_decision("AI_NOT_SELECTED")
    assert is_non_entry_decision("AI_CONFIDENCE_REJECT")
    assert is_non_entry_decision("TARGET_REJECT")
    assert is_non_entry_decision("FUTURE_GATE_REJECT")
    assert not is_non_entry_decision("ENTER_NOW")
    assert not is_non_entry_decision("ALERT")
