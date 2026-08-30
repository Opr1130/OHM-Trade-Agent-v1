from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest
import yaml

from app.services.freqtrade_result_ingest import (
    freqtrade_dry_run_status,
    ingest_freqtrade_dry_run,
)
from app.services.freqtrade_signal_bridge import (
    PaperAdmissionRejected,
    build_signal_id,
    cancel_admitted_signals,
    publish_qualified_long,
    to_freqtrade_pair,
)
from app.services.intelligence_journey import (
    link_qualified_signal,
    record_watch_observation,
)
from app.services.intelligence_learning_profile import build_intelligence_learning_profile
from app.services import paper_trade_control as paper_control


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _authoritative_status(
    *,
    open_trades=0,
    usd_stake=0.0,
    usdt_stake=0.0,
    active_signal_ids=None,
):
    return {
        "status": "OK",
        "open_trades": int(open_trades),
        "active_signal_ids": list(active_signal_ids or []),
        "workers": {
            "USD": {
                "status": "OK",
                "active_stake": float(usd_stake),
                "realized_net_pnl": 0.0,
            },
            "USDT": {
                "status": "OK",
                "active_stake": float(usdt_stake),
                "realized_net_pnl": 0.0,
            },
        },
    }


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
        starting_equity=10_000.0,
        max_positions=3,
        signals_file=signals,
        pairlist_file=pairlist,
        authoritative_status=_authoritative_status(),
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
        payload={"profit_rank": 1, "valid_now": True, "paper_requested": True},
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
        payload={"paper_requested": True},
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
    config_usdt_path = root / "freqtrade" / "user_data" / "config_usdt.json"
    strategy_path = (
        root
        / "freqtrade"
        / "user_data"
        / "strategies"
        / "OHMExternalSignalStrategy.py"
    )
    compose_path = root / "docker-compose.yml"
    paper_compose_path = root / "docker-compose.paper.yml"

    configs = [
        json.loads(config_path.read_text(encoding="utf-8")),
        json.loads(config_usdt_path.read_text(encoding="utf-8")),
    ]
    assert {config["stake_currency"] for config in configs} == {"USD", "USDT"}
    for config in configs:
        assert config["dry_run"] is True
        assert config["trading_mode"] == "spot"
        assert config["force_entry_enable"] is False
        assert config["exchange"]["name"] == "kraken"
        assert config["exchange"]["key"] == ""
        assert config["exchange"]["secret"] == ""
        assert "api_server" not in config
        assert "telegram" not in config
        assert config["pairlists"][0]["processing_mode"] == "append"
        assert config["custom_price_max_distance_ratio"] == 1.0
        assert config["exit_pricing"]["price_side"] == "other"
        assert config["pairlists"][0]["pairlist_url"].startswith(
            "file:///user_data/bridge/"
        )

    strategy_source = strategy_path.read_text(encoding="utf-8")
    ast.parse(strategy_source)
    assert "can_short = False" in strategy_source
    assert "Trade.get_trades_proxy" in strategy_source
    assert "ohm_stop_gap" in strategy_source
    assert "current_rate >= target_2" in strategy_source
    assert "if not self._enabled():" in strategy_source
    assert "exchange_write_authority" not in strategy_source

    compose = compose_path.read_text(encoding="utf-8")
    paper_compose = paper_compose_path.read_text(encoding="utf-8")
    assert "ohm-freqtrade-paper" not in compose
    compose_model = yaml.safe_load(compose)
    app_dependencies = (
        compose_model.get("services", {})
        .get("ohm-trade-agent", {})
        .get("depends_on", {})
    )
    if isinstance(app_dependencies, list):
        dependency_names = set(app_dependencies)
    else:
        dependency_names = set(app_dependencies)
    assert dependency_names.isdisjoint(
        {"ohm-freqtrade-paper", "ohm-freqtrade-paper-usdt"}
    )
    assert "./data/freqtrade/state:/app/freqtrade_paper:ro" in compose

    assert paper_compose.startswith("name: ohm-paper\n")
    assert "freqtradeorg/freqtrade:2026.7" in paper_compose
    assert "ohm-freqtrade-paper-init" in paper_compose
    assert "network_mode: none" in paper_compose
    assert "condition: service_completed_successfully" in paper_compose
    assert "ohm-freqtrade-paper-usdt:" in paper_compose
    assert "env_file:" not in paper_compose
    assert "ports:" not in paper_compose
    assert paper_compose.count('mem_limit: 384m') == 2
    assert paper_compose.count('memswap_limit: 384m') == 2
    assert 'mem_limit: 768m' not in paper_compose
    assert paper_compose.count('cpus: "0.20"') == 2
    assert "tradesv3.ohm_dry_run_usd.sqlite" in paper_compose
    assert "tradesv3.ohm_dry_run_usdt.sqlite" in paper_compose
    assert "heartbeat_USD" in paper_compose
    assert "heartbeat_USDT" in paper_compose
    assert "oom_score_adj: 500" in paper_compose
    assert "no-new-privileges:true" in paper_compose
    assert "./data/freqtrade/state:/freqtrade/state" in paper_compose
    assert "./data/freqtrade/user_data_usdt:/freqtrade/user_data" in paper_compose
    assert "./data/paper_trading:/freqtrade/control:ro" in paper_compose
    assert "./data/freqtrade_bridge:/freqtrade/user_data/bridge:ro" in paper_compose



def test_signal_to_paper_conversion_counts_distinct_signal_ids(tmp_path):
    journey_state = tmp_path / "journeys.json"
    events = tmp_path / "events.jsonl"

    record_watch_observation(
        symbol="SOLUSD",
        observed_at=NOW,
        watch_type="EARLY_WATCH",
        payload={"stage": "EARLY_BUILDING", "pattern": "TEST"},
        delivery_action="CREATED",
        delivered=True,
        state_file=journey_state,
        event_file=events,
    )
    for index in (1, 2):
        link_qualified_signal(
            symbol="SOLUSD",
            signal_id=f"OHM:signal-{index}",
            observed_at=NOW + timedelta(minutes=index),
            payload={"paper_requested": True},
            state_file=journey_state,
            event_file=events,
        )

    from app.services.intelligence_journey import record_paper_outcome

    record_paper_outcome(
        signal_id="OHM:signal-1",
        symbol="SOLUSD",
        observed_at=NOW + timedelta(hours=1),
        payload={
            "net_pnl": 10.0,
            "pnl_currency": "USD",
            "close_profit_ratio": 0.01,
        },
        state_file=journey_state,
        event_file=events,
    )

    profile = build_intelligence_learning_profile(
        event_file=events,
        persist=False,
    )
    assert profile["qualified_signals"] == 2
    assert profile["paper_outcome_signals"] == 1
    assert profile["signal_to_paper_outcome_conversion_pct"] == 50.0


def test_usd_and_usdt_trade_ids_do_not_collide_during_ingest(tmp_path):
    journey_state = tmp_path / "journeys.json"
    events = tmp_path / "events.jsonl"
    ingest_state = tmp_path / "ingest.json"
    usd_db = tmp_path / "tradesv3.ohm_dry_run_usd.sqlite"
    usdt_db = tmp_path / "tradesv3.ohm_dry_run_usdt.sqlite"

    for signal_id in ("OHM:usd", "OHM:usdt"):
        link_qualified_signal(
            symbol="SOLUSD",
            signal_id=signal_id,
            observed_at=NOW,
            payload={"paper_requested": True},
            state_file=journey_state,
            event_file=events,
        )

    _create_dry_run_db(usd_db, "OHM:usd")
    _create_dry_run_db(usdt_db, "OHM:usdt")

    # Inject each database separately to prove identical numeric trade ids
    # are namespaced by quote currency in the persisted ingest state.
    first = ingest_freqtrade_dry_run(
        db_file=usd_db,
        state_file=ingest_state,
        journey_state_file=journey_state,
        journey_event_file=events,
    )
    second = ingest_freqtrade_dry_run(
        db_file=usdt_db,
        state_file=ingest_state,
        journey_state_file=journey_state,
        journey_event_file=events,
    )
    assert first["outcomes_added"] == 1
    assert second["outcomes_added"] == 1
    dedup = ingest_state.with_name("ingest_dedup.sqlite")
    connection = sqlite3.connect(dedup)
    try:
        keys = {
            row[0]
            for row in connection.execute(
                "SELECT trade_key FROM processed_trade_outcomes "
                "WHERE status = 'APPLIED'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert keys == {"USD:1", "USDT:1"}



def test_paper_off_signals_do_not_reduce_paper_conversion(tmp_path):
    journey_state = tmp_path / "journeys.json"
    events = tmp_path / "events.jsonl"

    link_qualified_signal(
        symbol="SOLUSD",
        signal_id="OHM:paper-off",
        observed_at=NOW,
        payload={"paper_requested": False},
        state_file=journey_state,
        event_file=events,
    )
    profile = build_intelligence_learning_profile(
        event_file=events,
        persist=False,
    )
    assert profile["qualified_signals"] == 1
    assert profile["paper_requested_signals"] == 0
    assert profile["signal_to_paper_outcome_conversion_pct"] is None


def test_unmatched_freqtrade_outcome_is_left_retryable(tmp_path):
    events = tmp_path / "events.jsonl"
    journey_state = tmp_path / "journeys.json"
    ingest_state = tmp_path / "ingest.json"
    db = tmp_path / "tradesv3.ohm_dry_run_usd.sqlite"
    _create_dry_run_db(db, "OHM:not-linked")

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
    assert first["outcomes_added"] == 0
    assert first["unmatched_outcomes"] == 1
    assert second["unmatched_outcomes"] == 1
    dedup = ingest_state.with_name("ingest_dedup.sqlite")
    connection = sqlite3.connect(dedup)
    try:
        row = connection.execute(
            "SELECT status FROM processed_trade_outcomes WHERE trade_key = ?",
            ("USD:1",),
        ).fetchone()
    finally:
        connection.close()
    assert row is None


def test_strategy_reads_the_single_canonical_operator_control():
    root = Path(__file__).resolve().parents[1]
    strategy_source = (
        root
        / "freqtrade"
        / "user_data"
        / "strategies"
        / "OHMExternalSignalStrategy.py"
    ).read_text(encoding="utf-8")
    assert 'Path("/freqtrade/control/control.json")' in strategy_source
    bridge_source = (
        root / "app" / "services" / "freqtrade_signal_bridge.py"
    ).read_text(encoding="utf-8")
    assert "mirror_control" not in bridge_source



def test_canonical_paper_on_refuses_when_authoritative_workers_are_not_healthy(
    tmp_path,
    monkeypatch,
):
    control = tmp_path / "control.json"
    monkeypatch.setattr(paper_control, "CONTROL_FILE", control)
    monkeypatch.setattr(paper_control, "LOCK_FILE", tmp_path / ".control.lock")

    from app.services import freqtrade_result_ingest

    monkeypatch.setattr(
        freqtrade_result_ingest,
        "freqtrade_dry_run_status",
        lambda: {
            "status": "PARTIAL",
            "workers": {
                "USD": {"status": "OK"},
                "USDT": {"status": "STALE"},
            },
        },
    )

    with pytest.raises(
        paper_control.PaperTradeActivationError,
        match="USDT=STALE",
    ):
        paper_control.set_paper_trade_enabled(
            True,
            path=control,
            now=NOW,
        )

    assert not control.exists()


def test_canonical_paper_on_succeeds_only_after_both_workers_are_healthy(
    tmp_path,
    monkeypatch,
):
    control = tmp_path / "control.json"
    monkeypatch.setattr(paper_control, "CONTROL_FILE", control)
    monkeypatch.setattr(paper_control, "LOCK_FILE", tmp_path / ".control.lock")

    from app.services import freqtrade_result_ingest

    monkeypatch.setattr(
        freqtrade_result_ingest,
        "freqtrade_dry_run_status",
        lambda: {
            "status": "OK",
            "workers": {
                "USD": {"status": "OK"},
                "USDT": {"status": "OK"},
            },
        },
    )

    state = paper_control.set_paper_trade_enabled(
        True,
        path=control,
        now=NOW,
    )
    assert state.enabled is True
    saved = json.loads(control.read_text(encoding="utf-8"))
    assert saved["enabled"] is True
    assert saved["kraken_execution_authority"] is False



def test_freqtrade_status_requires_fresh_authoritative_heartbeat(tmp_path):
    db = tmp_path / "tradesv3.ohm_dry_run_usd.sqlite"
    _create_dry_run_db(db, "OHM:status")

    stale = freqtrade_dry_run_status(db_file=db)
    assert stale["status"] == "NOT_READY"
    assert stale["workers"]["USD"]["status"] == "STALE"

    heartbeat = tmp_path / "heartbeat_USD"
    heartbeat.write_text(NOW.isoformat(), encoding="utf-8")
    fresh = freqtrade_dry_run_status(db_file=db)
    assert fresh["status"] == "OK"
    assert fresh["workers"]["USD"]["status"] == "OK"



def test_authoritative_open_positions_continue_to_reserve_global_capacity(tmp_path):
    with pytest.raises(PaperAdmissionRejected, match="GLOBAL_POSITION_CAPACITY"):
        publish_qualified_long(
            episode_id="EP:capacity",
            cohort_id="COHORT:capacity",
            journey_id="JOURNEY:capacity",
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
            starting_equity=10_000.0,
            max_positions=3,
            signals_file=tmp_path / "signals.json",
            pairlist_file=tmp_path / "pairs.json",
            authoritative_status=_authoritative_status(open_trades=3),
        )


def test_authoritative_open_stake_reserves_actual_worker_capital(tmp_path):
    with pytest.raises(PaperAdmissionRejected, match="GLOBAL_CAPITAL_CAPACITY"):
        publish_qualified_long(
            episode_id="EP:capital",
            cohort_id="COHORT:capital",
            journey_id="JOURNEY:capital",
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
            starting_equity=10_000.0,
            max_positions=10,
            signals_file=tmp_path / "signals.json",
            pairlist_file=tmp_path / "pairs.json",
            authoritative_status=_authoritative_status(
                open_trades=1,
                usd_stake=4500.0,
            ),
        )


def test_operator_cancellation_is_permanent_and_cannot_be_reactivated(tmp_path):
    signals = tmp_path / "signals.json"
    pairlist = tmp_path / "pairs.json"
    first = publish_qualified_long(
        episode_id="EP:cancel",
        cohort_id="COHORT:cancel",
        journey_id="JOURNEY:cancel",
        ohm_symbol="SOLUSD",
        base_asset="SOL",
        quote_asset="USD",
        decision_at=NOW,
        valid_now=False,
        entry_style="wait_for_pullback",
        entry_low=95.0,
        entry_high=97.0,
        chase_limit=101.0,
        stop_price=92.0,
        target_1=104.0,
        target_2=108.0,
        stake_amount=1000.0,
        max_hold_hours=24,
        pending_ttl_hours=24,
        confidence=88,
        profit_rank=1,
        profit_rank_score=82.0,
        starting_equity=10_000.0,
        max_positions=3,
        signals_file=signals,
        pairlist_file=pairlist,
        authoritative_status=_authoritative_status(),
    )
    assert first["admission_status"] == "ADMITTED"
    assert cancel_admitted_signals(
        cancelled_at=NOW + timedelta(minutes=5),
        signals_file=signals,
    ) == 1

    replay = publish_qualified_long(
        episode_id="EP:cancel",
        cohort_id="COHORT:cancel",
        journey_id="JOURNEY:cancel",
        ohm_symbol="SOLUSD",
        base_asset="SOL",
        quote_asset="USD",
        decision_at=NOW,
        valid_now=False,
        entry_style="wait_for_pullback",
        entry_low=95.0,
        entry_high=97.0,
        chase_limit=101.0,
        stop_price=92.0,
        target_1=104.0,
        target_2=108.0,
        stake_amount=1000.0,
        max_hold_hours=24,
        pending_ttl_hours=24,
        confidence=88,
        profit_rank=1,
        profit_rank_score=82.0,
        starting_equity=10_000.0,
        max_positions=3,
        signals_file=signals,
        pairlist_file=pairlist,
        authoritative_status=_authoritative_status(),
    )
    assert replay["signal_id"] == first["signal_id"]
    assert replay["admission_status"] == "CANCELLED"


def test_durable_dedup_does_not_forget_old_trade_after_more_than_5000_rows(tmp_path):
    events = tmp_path / "events.jsonl"
    journey_state = tmp_path / "journeys.json"
    ingest_state = tmp_path / "ingest.json"
    db = tmp_path / "tradesv3.ohm_dry_run_usd.sqlite"

    signal_id = "OHM:durable"
    link_qualified_signal(
        symbol="SOLUSD",
        signal_id=signal_id,
        observed_at=NOW,
        payload={"paper_requested": True},
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
    assert first["outcomes_added"] == 1

    dedup = ingest_state.with_name("ingest_dedup.sqlite")
    connection = sqlite3.connect(dedup)
    try:
        now_iso = NOW.isoformat()
        connection.executemany(
            """
            INSERT OR IGNORE INTO processed_trade_outcomes
                (trade_key, signal_id, status, updated_at)
            VALUES (?, ?, 'APPLIED', ?)
            """,
            [
                (f"USD:{index}", f"OHM:history-{index}", now_iso)
                for index in range(2, 6003)
            ],
        )
        connection.commit()
    finally:
        connection.close()

    second = ingest_freqtrade_dry_run(
        db_file=db,
        state_file=ingest_state,
        journey_state_file=journey_state,
        journey_event_file=events,
    )
    assert second["outcomes_added"] == 0

    profile = build_intelligence_learning_profile(
        event_file=events,
        persist=False,
    )
    assert profile["paper_performance"]["count"] == 1


def test_strategy_rejects_cancelled_terminal_signals_and_material_entry_clamping():
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "freqtrade"
        / "user_data"
        / "strategies"
        / "OHMExternalSignalStrategy.py"
    ).read_text(encoding="utf-8")
    assert '"admission_status"' in source
    assert '"ADMITTED"' in source
    assert "abs(requested - expected_entry) / expected_entry > 0.0025" in source
