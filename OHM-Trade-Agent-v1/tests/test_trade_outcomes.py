from app.services.active_trade_registry import ActiveTrade
from app.services.entry_exit_advisor import EntryExitPlan
from app.services import trade_outcome_registry as outcomes


def plan(valid_now=True, entry_style="pullback_or_retest"):
    return EntryExitPlan(
        symbol="BTCUSD",
        valid_now=valid_now,
        entry_style=entry_style,
        entry_low=100,
        entry_high=101,
        chase_limit=102,
        stop_price=95,
        target_1=110,
        target_2=120,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="test",
    )


def candidate():
    return {
        "underlying_asset": "BTC",
        "primary_pair": "BTCUSD",
        "confidence": 82,
        "decision": "alert",
        "technical_score": 88,
        "target_attainability_score": 76,
        "profit_rank": 1,
        "profit_rank_score": 81.25,
        "target_quality_qualified": True,
        "economic_qualified": True,
        "economic_target_2_move_pct": 7.2,
        "economic_validation_net_t2": 128.0,
    }


def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(outcomes, "OUTCOME_FILE", tmp_path / "trade_outcomes.json")


def test_recommendation_is_idempotent(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    first = outcomes.record_recommendation(
        trade_id="OHM-BTC-1",
        candidate=candidate(),
        plan=plan(),
        action="ENTER_NOW",
    )
    changed = candidate()
    changed["profit_rank"] = 2
    second = outcomes.record_recommendation(
        trade_id="OHM-BTC-1",
        candidate=changed,
        plan=plan(),
        action="ENTER_NOW",
    )
    assert first["profit_rank"] == 1
    assert second["profit_rank"] == 1
    assert len(outcomes.get_outcomes()) == 1
    assert first["chief_confidence_semantics"].endswith("not_probability")


def test_place_limit_is_setup_not_entered(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    record = outcomes.record_recommendation(
        trade_id="OHM-BTC-LIMIT",
        candidate=candidate(),
        plan=plan(valid_now=False, entry_style="wait_for_pullback"),
        action="PLACE_LIMIT",
    )
    assert record["entered_trade"] is False
    assert record["recommendation_action"] == "PLACE_LIMIT"


def test_recommendation_persists_wave8_market_intelligence(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    item = candidate()
    evidence = {
        "assessment": {"status": "AVAILABLE", "context_score": 60},
        "authority": "context_only_no_gate_override",
    }
    item["market_intelligence"] = evidence

    record = outcomes.record_recommendation(
        trade_id="OHM-BTC-INTELLIGENCE",
        candidate=item,
        plan=plan(),
        action="ENTER_NOW",
    )

    assert record["schema_version"] == 3
    assert record["market_intelligence"] == evidence


def test_mark_entered_records_explicit_entry_source(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    outcomes.record_recommendation(
        trade_id="OHM-BTC-2", candidate=candidate(), plan=plan(), action="ENTER_NOW"
    )
    trade = ActiveTrade(
        symbol="BTCUSD",
        entry_price=101,
        stop_price=95,
        target_1=110,
        target_2=120,
        risk_level="low",
        opened_at="2026-08-09T20:00:00+00:00",
        trade_id="OHM-BTC-2",
    )
    record = outcomes.mark_trade_entered(
        trade, entry_price_source="confirmation_reference"
    )
    assert record["entered_trade"] is True
    assert record["entry_price"] == 101
    assert record["entry_price_source"] == "confirmation_reference"


def test_mfe_mae_only_move_with_new_extremes(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    trade = ActiveTrade(
        symbol="BTCUSD", entry_price=100, stop_price=90, target_1=110,
        target_2=120, risk_level="low", opened_at="2026-08-09T20:00:00+00:00",
        trade_id="OHM-BTC-3",
    )
    outcomes.mark_trade_entered(trade, entry_price_source="manual_actual_fill")
    one = outcomes.update_active_observation(
        trade, 105, observed_at="2026-08-09T21:00:00+00:00"
    )
    two = outcomes.update_active_observation(
        trade, 103, observed_at="2026-08-09T22:00:00+00:00"
    )
    three = outcomes.update_active_observation(
        trade, 95, observed_at="2026-08-09T23:00:00+00:00"
    )
    assert one["mfe_pct"] == 5.0
    assert two["mfe_pct"] == 5.0
    assert three["mfe_pct"] == 5.0
    assert three["mae_pct"] == 5.0


def test_target_first_hit_times_are_stable(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    trade = ActiveTrade(
        symbol="BTCUSD", entry_price=100, stop_price=90, target_1=110,
        target_2=120, risk_level="low", opened_at="2026-08-09T20:00:00+00:00",
        trade_id="OHM-BTC-4",
    )
    outcomes.mark_trade_entered(trade, entry_price_source="manual_actual_fill")
    first = outcomes.update_active_observation(
        trade, 111, observed_at="2026-08-09T21:00:00+00:00"
    )
    second = outcomes.update_active_observation(
        trade, 115, observed_at="2026-08-09T22:00:00+00:00"
    )
    assert first["target_1_first_observed_at"] == "2026-08-09T21:00:00+00:00"
    assert second["target_1_first_observed_at"] == "2026-08-09T21:00:00+00:00"


def test_sample_above_t2_records_ambiguity_not_fabricated_order(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    trade = ActiveTrade(
        symbol="BTCUSD", entry_price=100, stop_price=90, target_1=110,
        target_2=120, risk_level="low", opened_at="2026-08-09T20:00:00+00:00",
        trade_id="OHM-BTC-5",
    )
    outcomes.mark_trade_entered(trade, entry_price_source="manual_actual_fill")
    record = outcomes.update_active_observation(
        trade, 125, observed_at="2026-08-09T21:00:00+00:00"
    )
    assert record["target_1_observed"] is True
    assert record["target_2_observed"] is True
    assert record["target_1_first_observed_at"] == record["target_2_first_observed_at"]
    assert any("crossing order was not inferred" in w for w in record["observation_warnings"])


def test_stop_first_hit_time_is_stable(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    trade = ActiveTrade(
        symbol="BTCUSD", entry_price=100, stop_price=90, target_1=110,
        target_2=120, risk_level="low", opened_at="2026-08-09T20:00:00+00:00",
        trade_id="OHM-BTC-6",
    )
    outcomes.mark_trade_entered(trade, entry_price_source="manual_actual_fill")
    outcomes.update_active_observation(
        trade, 89, observed_at="2026-08-09T21:00:00+00:00"
    )
    record = outcomes.update_active_observation(
        trade, 88, observed_at="2026-08-09T22:00:00+00:00"
    )
    assert record["stop_first_observed_at"] == "2026-08-09T21:00:00+00:00"


def test_skipped_setup_is_not_counted_as_losing_entered_trade(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    outcomes.record_recommendation(
        trade_id="OHM-BTC-7", candidate=candidate(), plan=plan(), action="ENTER_NOW"
    )
    assert outcomes.terminalize_setup_outcome("OHM-BTC-7", "skipped") is True
    record = outcomes.get_outcomes()[0]
    assert record["terminal_status"] == "skipped"
    assert record["entered_trade"] is False


def test_active_terminalization_is_idempotent(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    trade = ActiveTrade(
        symbol="BTCUSD", entry_price=100, stop_price=90, target_1=110,
        target_2=120, risk_level="low", opened_at="2026-08-09T20:00:00+00:00",
        trade_id="OHM-BTC-8",
    )
    outcomes.mark_trade_entered(trade, entry_price_source="manual_actual_fill")
    assert outcomes.terminalize_active_outcome(
        trade_id=trade.trade_id, symbol=trade.symbol, status="closed",
        reason="manual_close", final_price=106,
    ) is True
    assert outcomes.terminalize_active_outcome(
        trade_id=trade.trade_id, symbol=trade.symbol, status="closed",
        reason="second_close", final_price=80,
    ) is True
    record = outcomes.get_outcomes()[0]
    assert record["terminal_reason"] == "manual_close"
    assert record["final_observed_price"] == 106


def test_legacy_active_trade_gets_deterministic_legacy_key(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    trade = ActiveTrade(
        symbol="BTCUSD", entry_price=100, stop_price=90, target_1=110,
        target_2=120, risk_level="low", opened_at="2026-08-09T20:00:00+00:00",
        trade_id="",
    )
    record = outcomes.update_active_observation(trade, 101)
    assert record["record_key"].startswith("legacy:BTCUSD:")
    assert record["trade_id"] == ""


def test_calibration_refuses_small_samples(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    summary = outcomes.calibration_summary(min_resolved_entered=30)
    assert summary["status"] == "INSUFFICIENT_DATA"
    assert summary["confidence_is_probability"] is False
