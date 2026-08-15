from types import SimpleNamespace

from app.services import shadow_decision_capture as capture


def test_shadow_capture_uses_attached_wave8_evidence_when_caller_does_not_pass_it(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        capture,
        "record_shadow_candidate",
        lambda **kwargs: recorded.append(kwargs) or kwargs,
    )
    evidence = {
        "assessment": {"status": "AVAILABLE", "context_score": 60},
        "authority": "context_only_no_gate_override",
    }
    snapshot = SimpleNamespace(
        symbol="BTCUSD",
        trade_direction="LONG",
        last_price=100.0,
        technical_score=80,
        volume_ratio=2.0,
        execution_validation=None,
        _wave8_market_intelligence=evidence,
    )

    assert capture.capture_snapshot_decision(
        snapshot,
        decision="AI_NOT_SELECTED",
        market_regime="NEUTRAL",
        reason="Chief omitted candidate",
        source="chief_ai_review",
    ) is True

    assert recorded[0]["market_intelligence"] == evidence


def test_explicit_shadow_evidence_overrides_attached_fallback(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        capture,
        "record_shadow_candidate",
        lambda **kwargs: recorded.append(kwargs) or kwargs,
    )
    snapshot = SimpleNamespace(
        symbol="BTCUSD",
        trade_direction="LONG",
        last_price=100.0,
        execution_validation=None,
        _wave8_market_intelligence={"assessment": {"context_score": 40}},
    )
    explicit = {"assessment": {"context_score": 65}}

    capture.capture_snapshot_decision(
        snapshot,
        decision="WAIT",
        market_regime="NEUTRAL",
        reason="wait",
        source="qualified_profit_rank",
        market_intelligence=explicit,
    )

    assert recorded[0]["market_intelligence"] == explicit
