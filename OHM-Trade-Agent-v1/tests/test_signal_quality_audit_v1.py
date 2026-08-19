from app.services.signal_quality_audit import build_signal_quality_audit


def _row(**overrides):
    row = {
        "symbol": "BTCUSD",
        "direction": "LONG",
        "terminal_status": "closed",
        "entered_trade": True,
        "target_1_observed": False,
        "target_2_observed": False,
        "stop_observed": False,
        "mfe_pct": 0.0,
        "mae_pct": 0.0,
        "technical_score": 70,
        "target_attainability_score": 80,
        "profit_rank": 1,
        "market_regime": "TRENDING",
        "price_movement": {"state": "READY"},
        "market_intelligence": {"bias": "BULLISH"},
    }
    row.update(overrides)
    return row


def test_audit_reports_target_rates_and_failure_modes():
    rows = [
        _row(target_1_observed=True, target_2_observed=True, mfe_pct=4.0, mae_pct=0.5),
        _row(target_1_observed=True, mfe_pct=2.0, mae_pct=0.8),
        _row(stop_observed=True, mfe_pct=0.1, mae_pct=2.0),
        _row(entered_trade=False, terminal_status="expired", setup_status="expired"),
    ]

    audit = build_signal_quality_audit(rows, minimum_reliable_samples=30)

    assert audit["status"] == "PROVISIONAL"
    assert audit["resolved_entered"] == 3
    assert audit["target_1_success_rate_pct"] == 66.67
    assert audit["target_2_success_rate_pct"] == 33.33
    assert audit["stop_before_target_rate_pct"] == 33.33
    assert audit["classifications"]["TARGET_2_REACHED"] == 1
    assert audit["classifications"]["TARGET_1_ONLY"] == 1
    assert audit["classifications"]["STOP_BEFORE_TARGET"] == 1
    assert audit["classifications"]["NO_ENTRY_EXPIRED"] == 1
    assert audit["failure_reasons"]["IMMEDIATE_DIRECTIONAL_FAILURE"] == 1
    assert audit["failure_reasons"]["ENTRY_TIMING_OR_SETUP_FAILURE"] == 1
    assert audit["automatic_tuning_applied"] is False
    assert audit["advisory_only"] is True


def test_audit_becomes_reliable_only_after_required_resolved_entered_samples():
    rows = [_row(target_1_observed=True) for _ in range(30)]

    audit = build_signal_quality_audit(rows)

    assert audit["status"] == "RELIABLE"
    assert audit["resolved_entered"] == 30
    assert audit["target_1_success_rate_pct"] == 100.0


def test_breakdowns_preserve_direction_regime_and_context():
    rows = [
        _row(direction="LONG", target_1_observed=True),
        _row(
            direction="SHORT",
            stop_observed=True,
            mfe_pct=0.1,
            mae_pct=1.0,
            market_regime="RANGE",
            technical_score=61,
            target_attainability_score=55,
            profit_rank=2,
            price_movement={"classification": "WATCH"},
            market_intelligence={"directional_bias": "BEARISH"},
        ),
    ]

    audit = build_signal_quality_audit(rows)
    breakdowns = audit["breakdowns"]

    assert breakdowns["direction"]["LONG"]["target_1_success_rate_pct"] == 100.0
    assert breakdowns["direction"]["SHORT"]["stop_before_target_rate_pct"] == 100.0
    assert "RANGE" in breakdowns["market_regime"]
    assert "60-69" in breakdowns["technical_score"]
    assert "50-59" in breakdowns["target_attainability_score"]
    assert "2" in breakdowns["profit_rank"]
    assert "WATCH" in breakdowns["price_movement_state"]
    assert "BEARISH" in breakdowns["market_intelligence_bias"]


def test_open_records_are_visible_but_excluded_from_resolved_rates():
    rows = [_row(terminal_status=None, entered_trade=True)]

    audit = build_signal_quality_audit(rows)

    assert audit["records"] == 1
    assert audit["terminal_records"] == 0
    assert audit["resolved_entered"] == 0
    assert audit["target_1_success_rate_pct"] is None
    assert audit["classifications"]["OPEN_OR_UNRESOLVED"] == 1
