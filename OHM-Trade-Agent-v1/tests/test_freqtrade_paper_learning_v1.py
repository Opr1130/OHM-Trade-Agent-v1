from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from app.services.freqtrade_result_ingest import ingest_freqtrade_dry_run
from app.services.freqtrade_signal_bridge import (
    build_signal_id,
    mirror_control,
    publish_qualified_long,
    to_freqtrade_pair,
)
from app.services.intelligence_journey import (
    link_qualified_signal,
    record_watch_observation,
)
from app.services.intelligence_learning_profile import build_intelligence_learning_profile


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def test_freqtrade_pair_normalizes_kraken_aliases():
    assert to_freqtrade_pair("XBT", "USD") == "BTC/USD"
    assert to_freqtrade_pair("SOL", "USDT") == "SOL/USDT"


def test_publish_qualified_long_writes_signal_and_dynamic_pairlist(tmp_path):
    signals = tmp_path / "signals.json"
    pairlist = tmp_path / "pairlist.json"
    row = publish_qualified_long(
        episode_id="EP:1",
        cohort_id="COHORT:1",
        journey_id="JOURNEY:1",
        ohm_symbol="SOLUSD",
        base_asset="SOL",
        quote_asset="USD",
        decision_at=NOW,
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=102.0,
        stop_price=95.0,
        target_1=105.0,
        target_2=110.0,
        stake_amount=1000.0,
        max_hold_hours=24,
        pending_ttl_hours=24,
        confidence=90,
        profit_rank=1,
        profit_rank_score=88.0,
        signals_file=signals,
        pairlist_file=pairlist,
    )
    assert row["pair"] == "SOL/USD"
    assert row["direction"] == "LONG"
    assert row["entry_price"] == 100.0
    assert row["exchange_write_authority"] is False

    saved = json.loads(signals.read_text(encoding="utf-8"))
    assert saved["signals"][0]["signal_id"] == row["signal_id"]

    pairs = json.loads(pairlist.read_text(encoding="utf-8"))
    assert pairs["pairs"] == ["SOL/USD"]


def test_freqtrade_signal_id_is_deterministic():
    first = build_signal_id(
        episode_id="EP:abc",
        pair="BTC/USD",
        decision_at=NOW,
    )
    second = build_signal_id(
        episode_id="EP:abc",
        pair="BTC/USD",
        decision_at=NOW,
    )
    assert first == second
    assert first.startswith("OHM:")


def test_freqtrade_bridge_control_contains_no_exchange_authority(tmp_path):
    path = tmp_path / "control.json"
    mirror_control(
        enabled=True,
        updated_at=NOW,
        updated_by="TEST",
        control_file=path,
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["enabled"] is True
    assert row["authoritative_engine"] == "FREQTRADE_DRY_RUN"
    assert row["exchange_write_authority"] is False


def _create_dry_run_db(path: Path, signal_id: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                pair TEXT,
                is_open INTEGER,
                enter_tag TEXT,
                open_date TEXT,
                close_date TEXT,
                open_rate REAL,
                open_rate_requested REAL,
                close_rate REAL,
                close_rate_requested REAL,
                stake_amount REAL,
                amount REAL,
                fee_open REAL,
                fee_close REAL,
                close_profit REAL,
                close_profit_abs REAL,
                exit_reason TEXT,
                strategy TEXT,
                timeframe INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO trades (
                id, pair, is_open, enter_tag, open_date, close_date,
                open_rate, open_rate_requested, close_rate, close_rate_requested,
                stake_amount, amount, fee_open, fee_close, close_profit,
                close_profit_abs, exit_reason, strategy, timeframe
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "SOL/USD",
                0,
                signal_id,
                NOW.isoformat(),
                (NOW + timedelta(hours=2)).isoformat(),
                100.0,
                100.0,
                105.0,
                105.0,
                1000.0,
                10.0,
                0.004,
                0.004,
                0.025,
                25.0,
                "ohm_tp2",
                "OHMExternalSignalStrategy",
                15,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_early_watch_signal_and_freqtrade_outcome_form_one_learning_journey(tmp_path):
    journey_state = tmp_path / "journeys.json"
    events = tmp_path / "events.jsonl"
    ingest_state = tmp_path / "ingest.json"
    db = tmp_path / "dry.sqlite"

    journey_id = record_watch_observation(
        symbol="SOLUSD",
        observed_at=NOW,
        watch_type="EARLY_WATCH",
        payload={
            "stage": "EARLY_BUILDING",
            "pattern": "COMPRESSION_RELEASE",
            "opportunity_score": 72,
            "explosion_potential_score": 76,
        },
        delivery_action="CREATED",
        delivered=True,
        state_file=journey_state,
        event_file=events,
    )
    signal_id = "OHM:test-signal"
    linked = link_qualified_signal(
        symbol="SOLUSD",
        signal_id=signal_id,
        observed_at=NOW + timedelta(minutes=30),
        payload={"profit_rank": 1, "valid_now": True},
        state_file=journey_state,
        event_file=events,
    )
    assert linked == journey_id

    _create_dry_run_db(db, signal_id)
    result = ingest_freqtrade_dry_run(
        db_file=db,
        state_file=ingest_state,
        journey_state_file=journey_state,
        journey_event_file=events,
    )
    assert result["status"] == "OK"
    assert result["outcomes_added"] == 1

    profile = build_intelligence_learning_profile(
        event_file=events,
        persist=False,
    )
    assert profile["early_watch_to_signal_conversion_pct"] == 100.0
    assert profile["signal_to_paper_outcome_conversion_pct"] == 100.0
    assert profile["early_watch_to_signal_latency_minutes"]["average"] == 30.0
    assert profile["paper_performance"]["count"] == 1
    assert profile["paper_performance"]["win_rate_pct"] == 100.0
    assert profile["paper_performance"]["net_pnl"] == 25.0
    assert profile["paper_performance_with_early_watch"]["count"] == 1
    assert profile["paper_performance_after_delivered_early_alert"]["count"] == 1
    assert profile["paper_performance_after_nondelivered_early_detection"]["count"] == 0


def test_freqtrade_outcome_ingest_is_idempotent(tmp_path):
    journey_state = tmp_path / "journeys.json"
    events = tmp_path / "events.jsonl"
    ingest_state = tmp_path / "ingest.json"
    db = tmp_path / "dry.sqlite"

    signal_id = "OHM:idempotent"
    link_qualified_signal(
        symbol="SOLUSD",
        signal_id=signal_id,
        observed_at=NOW,
        payload={},
        state_file=journey_state,
        event_file=events,
    )
    _create_dry_run_db(db, signal_id)

    first = ingest_freqtrade_dry_run(
        db_file=db,
        state_file=ingest_state,
        journey_state_file=journey_state,
        journey_event_file=events,
    )
    second = ingest_freqtrade_dry_run(
        db_file=db,
        state_file=ingest_state,
        journey_state_file=journey_state,
        journey_event_file=events,
    )
    assert first["outcomes_added"] == 1
    assert second["outcomes_added"] == 0


def test_freqtrade_artifacts_enforce_dry_run_and_secret_isolation():
    root = Path(__file__).resolve().parents[1]
    config_path = root / "freqtrade" / "user_data" / "config.json"
    strategy_path = (
        root
        / "freqtrade"
        / "user_data"
        / "strategies"
        / "OHMExternalSignalStrategy.py"
    )
    compose_path = root / "docker-compose.yml"

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["dry_run"] is True
    assert config["trading_mode"] == "spot"
    assert config["force_entry_enable"] is False
    assert config["exchange"]["name"] == "kraken"
    assert config["exchange"]["key"] == ""
    assert config["exchange"]["secret"] == ""
    assert config["api_server"]["enabled"] is False
    assert config["telegram"]["enabled"] is False

    strategy_source = strategy_path.read_text(encoding="utf-8")
    ast.parse(strategy_source)
    assert "can_short = False" in strategy_source
    assert "Trade.get_trades_proxy" in strategy_source
    assert "ohm_stop_gap" in strategy_source
    assert "current_rate >= target_2" in strategy_source
    assert "exchange_write_authority" not in strategy_source

    compose = compose_path.read_text(encoding="utf-8")
    sidecar = compose.split("  ohm-freqtrade-paper:", 1)[1].split(
        "  ohm-trade-agent:", 1
    )[0]
    assert "freqtradeorg/freqtrade:2026.7" in sidecar
    assert "env_file:" not in sidecar
    assert "ports:" not in sidecar
    assert "tradesv3.ohm_dry_run.sqlite" in sidecar
    assert "service_healthy" in compose
    assert "./freqtrade/user_data:/app/freqtrade_paper:ro" in compose
