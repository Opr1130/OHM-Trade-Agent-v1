from types import SimpleNamespace

from app.services import shadow_decision_capture as capture
from app.services.decision_learning import classify_shadow_decision, decision_quality_summary


def test_capture_snapshot_decision_preserves_gate_context(monkeypatch):
    recorded = []
    monkeypatch.setattr(capture, "record_shadow_candidate", lambda **kwargs: recorded.append(kwargs) or kwargs)
    snapshot = SimpleNamespace(
        symbol="ABCUSD",
        trade_direction="LONG",
        last_price=100.0,
        technical_score=81.5,
        volume_ratio=2.2,
        execution_validation=SimpleNamespace(spread_bps=6.4),
    )

    ok = capture.capture_snapshot_decision(
        snapshot,
        decision="ECONOMIC_REJECT",
        market_regime="RISK_OFF",
        reason="net profit below minimum",
        source="economic_quality_gate",
    )

    assert ok is True
    assert recorded[0]["decision"] == "ECONOMIC_REJECT"
    assert recorded[0]["reference_price"] == 100.0
    assert recorded[0]["market_regime"] == "RISK_OFF"
    assert recorded[0]["technical_score"] == 81.5
    assert recorded[0]["volume_ratio"] == 2.2
    assert recorded[0]["spread_bps"] == 6.4


def test_capture_snapshot_decision_forwards_wave8_market_intelligence(monkeypatch):
    recorded = []
    monkeypatch.setattr(capture, "record_shadow_candidate", lambda **kwargs: recorded.append(kwargs) or kwargs)
    snapshot = SimpleNamespace(
        symbol="BTCUSD",
        trade_direction="LONG",
        last_price=100.0,
        execution_validation=None,
    )
    evidence = {
        "assessment": {"status": "AVAILABLE", "context_score": 55},
        "authority": "context_only_no_gate_override",
    }

    ok = capture.capture_snapshot_decision(
        snapshot,
        decision="INTELLIGENCE_CONTEXT",
        market_regime="NEUTRAL",
        reason="bounded Wave 8 context",
        source="wave8_market_intelligence",
        market_intelligence=evidence,
    )

    assert ok is True
    assert recorded[0]["market_intelligence"] == evidence


def test_capture_is_fail_open(monkeypatch):
    monkeypatch.setattr(capture, "record_shadow_candidate", lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    snapshot = SimpleNamespace(symbol="ABCUSD", trade_direction="LONG", last_price=100.0)

    assert capture.capture_snapshot_decision(
        snapshot,
        decision="TARGET_REJECT",
        market_regime="RISK_ON",
        reason="blocked",
        source="target_quality_gate",
    ) is False


def test_immediate_vs_wait_uses_actual_entry_prices_long():
    row = {
        "decision": "ENTER_NOW",
        "direction": "LONG",
        "reference_price": 100.0,
        "observations": {
            "5m": {"price": 95.0, "directional_move_pct": -5.0},
            "15m": {"price": 98.0, "directional_move_pct": -2.0},
            "30m": {"price": 102.0, "directional_move_pct": 2.0},
            "1h": {"price": 104.0, "directional_move_pct": 4.0},
            "24h": {"price": 110.0, "directional_move_pct": 10.0},
        },
    }

    result = classify_shadow_decision(row)

    # Immediate return is 10%; entry at 95 would return ~15.789%, so waiting
    # five minutes had ~5.789 percentage points of additional directional edge.
    assert round(result["immediate_vs_wait_edge_pct"]["5m"], 3) == 5.789
    assert result["immediate_vs_wait_edge_pct"]["30m"] < 0


def test_immediate_vs_wait_uses_actual_entry_prices_short():
    row = {
        "decision": "ENTER_NOW",
        "direction": "SHORT",
        "reference_price": 100.0,
        "observations": {
            "5m": {"price": 105.0, "directional_move_pct": -4.761905},
            "24h": {"price": 90.0, "directional_move_pct": 11.111111},
        },
    }

    result = classify_shadow_decision(row)

    # Waiting for a rebound to 105 creates a better short entry than 100.
    assert result["immediate_vs_wait_edge_pct"]["5m"] > 0


def test_rejected_gate_decisions_count_for_missed_and_correct_avoidance():
    rows = [
        {
            "decision": "TARGET_REJECT",
            "direction": "LONG",
            "reference_price": 100.0,
            "observations": {"24h": {"price": 104.0, "directional_move_pct": 4.0}},
        },
        {
            "decision": "EXECUTION_REJECT",
            "direction": "LONG",
            "reference_price": 100.0,
            "observations": {
                "1h": {"price": 99.0, "directional_move_pct": -1.0},
                "24h": {"price": 96.0, "directional_move_pct": -4.0},
            },
        },
    ]

    summary = decision_quality_summary(rows)

    assert summary["missed_profitable_opportunities"] == 1
    assert summary["correct_wait_or_reject"] == 1
