from app.services import shadow_learning


def _base_row():
    return {
        "record_key": "BTCUSD:LONG:AI_WATCH:2026-08-19T04:00:00+00:00",
        "symbol": "BTCUSD",
        "direction": "LONG",
        "decision": "AI_WATCH",
        "source": "chief_ai_review",
        "observed_at": "2026-08-19T04:00:00+00:00",
        "reference_price": 100.0,
        "market_regime": "RISK_OFF",
        "technical_score": 80,
        "profit_rank_score": None,
        "volume_ratio": 1.5,
        "spread_bps": 2.0,
        "reason": "watch",
        "market_intelligence": {"assessment": {"context_score": 50}},
        "price_movement": None,
        "observations": {"5m": {"directional_move_pct": 0.2}},
        "complete": False,
        "updated_at": "2026-08-19T04:05:00+00:00",
    }


def test_dedup_backfills_target_v2_without_changing_original_measurement(monkeypatch):
    row = _base_row()
    data = {row["record_key"]: row}
    saves = []
    monkeypatch.setattr(shadow_learning, "_load", lambda: data)
    monkeypatch.setattr(shadow_learning, "_save", lambda payload: saves.append(payload.copy()))

    target_v2 = {
        "target_class": "FULL_TARGET",
        "proposed_t1_move_pct": 2.0,
        "shadow_only": True,
        "production_authoritative": False,
    }
    result = shadow_learning.record_shadow_candidate(
        symbol="BTCUSD",
        direction="LONG",
        decision="AI_WATCH",
        reference_price=105.0,
        market_regime="RISK_OFF",
        source="chief_ai_review",
        observed_at="2026-08-19T04:27:00+00:00",
        target_v2_shadow=target_v2,
    )

    stored = data[row["record_key"]]
    assert stored["target_v2_shadow"] == target_v2
    assert stored["reference_price"] == 100.0
    assert stored["observed_at"] == "2026-08-19T04:00:00+00:00"
    assert stored["observations"] == {"5m": {"directional_move_pct": 0.2}}
    assert stored["market_intelligence"] == {"assessment": {"context_score": 50}}
    assert result["deduplicated"] is True
    assert result["dedup_enriched_fields"] == ["target_v2_shadow"]
    assert saves


def test_dedup_does_not_overwrite_existing_target_v2(monkeypatch):
    row = _base_row()
    original = {"target_class": "REDUCED_TARGET", "proposed_t1_move_pct": 1.5}
    row["target_v2_shadow"] = original
    data = {row["record_key"]: row}
    saves = []
    monkeypatch.setattr(shadow_learning, "_load", lambda: data)
    monkeypatch.setattr(shadow_learning, "_save", lambda payload: saves.append(payload.copy()))

    result = shadow_learning.record_shadow_candidate(
        symbol="BTCUSD",
        direction="LONG",
        decision="AI_WATCH",
        reference_price=101.0,
        market_regime="RISK_OFF",
        source="chief_ai_review",
        observed_at="2026-08-19T04:30:00+00:00",
        target_v2_shadow={"target_class": "FULL_TARGET", "proposed_t1_move_pct": 2.5},
    )

    assert data[row["record_key"]]["target_v2_shadow"] == original
    assert "dedup_enriched_fields" not in result
    assert saves == []
