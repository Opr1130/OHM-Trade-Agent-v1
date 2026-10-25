"""ATDD: recovery, delivery retry, and learning consumption (review corrections).

Covers the defects found during independent review:
- an actionable alert the transport refused had no production retry path;
- persisted decision evidence had no consumer (orphaned learning evidence);
- degraded F6 / 4H evidence must not permit notification on retry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.jobs import report_tradevisor_learning as learning_job
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import notification_policy, tradevisor_learning as learning
from app.services import tradevisor_inbox as inbox
from app.services import tradevisor_pipeline as pipeline
from app.services.tradevisor_ingress import TradevisorSignal

NOW = datetime(2026, 10, 23, 13, 0, 5, tzinfo=timezone.utc)
SHA_A = "a" * 40
SHA_B = "b" * 40


def market(**over) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=58.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
        recent_24h_high=112.0,
        recent_24h_low=94.0,
        recent_72h_high=120.0,
        recent_72h_low=88.0,
        momentum_6h_pct=1.0,
        momentum_24h_pct=3.0,
        momentum_72h_pct=7.0,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=8.0,
        rolling_24h_downside_p75_pct=10.0,
        rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0,
        rolling_72h_downside_p75_pct=16.0,
        rolling_72h_downside_p90_pct=20.0,
        ticker_last=100.0,
        execution_validation=ExecutionValidation(
            status="VALID",
            book_coverage_status="COMPLETE",
            warnings=[],
            best_bid=99.95,
            best_ask=100.05,
            mid_price=100.0,
            spread_pct=0.1,
            spread_bps=5.0,
            validation_notional_usd=250.0,
            buy_vwap=100.02,
            sell_vwap=99.98,
            buy_fully_covered=True,
            sell_fully_covered=True,
            estimated_visible_round_trip_market_drag_pct=0.10,
            recent_trade_status="FRESH",
            latest_trade_price=100.0,
            latest_trade_age_seconds=10.0,
            recent_trade_count=20,
        ),
    )
    values.update(over)
    return MarketSnapshot(**values)


def signal(price: float = 100.0) -> TradevisorSignal:
    return TradevisorSignal(
        dedup_id="dedup-retry-1",
        signal="BUY",
        instrument="BTC/USD",
        market="BTCUSD",
        source_exchange="COINBASE",
        source_symbol="BTCUSD",
        timeframe_minutes=60,
        bar_opened_at=NOW - timedelta(minutes=65),
        bar_closed_at=NOW - timedelta(minutes=5),
        triggered_at=NOW - timedelta(seconds=5),
        received_at=NOW,
        signal_price=price,
        alert_config_version="tradevisor-1h-buy",
    )


def telegram_settings() -> Settings:
    return Settings(
        _env_file=None,
        webhook_secret="x" * 12,
        telegram_enabled=True,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
    )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: SimpleNamespace(
        opip_tradevisor_retention_seconds=7 * 24 * 3600,
        opip_tradevisor_max_inbox_events=200,
        opip_tradevisor_max_attempts=3,
        opip_tradevisor_processing_timeout_seconds=120,
        opip_tradevisor_processing_batch_limit=2,
        opip_tradevisor_enabled=True,
    ))
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification_state.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification_state.lock")
    monkeypatch.setattr(pipeline, "_active_trades", lambda: [])
    return tmp_path


class Sender:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(delivered=self.delivered, message_id=1)


def _settle_failed(row_signal=None, *, expires_in_minutes: int = 90, status: str = "SEND_FAILED"):
    """Persist one processed row whose notification failed."""
    resolved = row_signal or signal()
    row, _ = inbox.enqueue(resolved, now=NOW)
    inbox.process_queued_events(
        evaluator=lambda _s: {
            "decision": "ENTER_NOW",
            "reason": "qualified",
            "record": {
                "schema_version": "tradevisor-decision-v1",
                "decision": "ENTER_NOW",
                "dedup_id": resolved.dedup_id,
                "market": "BTCUSD",
                "instrument": "BTC/USD",
                "evaluated_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(minutes=expires_in_minutes)).isoformat(),
                "plan": {
                    "entry_low": 99.0,
                    "entry_high": 100.0,
                    "stop_price": 96.5,
                    "target_1": 105.5,
                    "target_2": 108.5,
                },
                "allocation": {"recommended_capital": 1000.0, "risk_dollars": 35.0},
                "economics": {
                    "recommended_capital": 2000.0,
                    "target_1_net_profit": 100.0,
                    "target_2_net_profit": 160.0,
                },
            },
            "notification": {"status": status, "delivered": False},
        },
        now=NOW,
    )
    return row


# ---------------------------------------------------------------------------
# P1-A: delivery retry exists, and never re-sends a frozen plan
# ---------------------------------------------------------------------------


def test_undelivered_rows_are_retry_candidates_only_while_unexpired():
    _settle_failed()
    assert len(inbox.undelivered_notification_rows(limit=5, now=NOW)) == 1

    # Once the frozen plan has expired the row is terminally undeliverable.
    later = NOW + timedelta(hours=4)
    assert inbox.undelivered_notification_rows(limit=5, now=later) == []


def test_retry_counts_an_unreadable_row_and_leaves_it_untouched(monkeypatch):
    """An unreadable stored signal is counted, not silently dropped."""
    _settle_failed()

    def corrupt(_row):
        raise ValueError("unreadable stored signal")

    monkeypatch.setattr(pipeline, "rebuild_signal", corrupt)
    sender = Sender()
    summary = pipeline.retry_undelivered_notifications(
        settings=telegram_settings(),
        now=NOW + timedelta(minutes=2),
        sender=sender,
    )
    assert summary == {
        "candidates": 1,
        "delivered": 0,
        "still_undelivered": 0,
        "not_actionable": 0,
        "unreadable": 1,
    }
    assert sender.calls == []
    row = inbox.iter_records()[0]
    # The retry pass does not consume the row; lease reclaim owns it.
    assert int(row["notification"].get("retry_count", 0)) == 0


def test_retry_re_evaluates_and_withholds_delivery_without_f6_and_4h():
    _settle_failed()
    sender = Sender()
    summary = pipeline.retry_undelivered_notifications(
        settings=telegram_settings(),
        now=NOW + timedelta(minutes=2),
        snapshot_provider=lambda _m: ("ok", market(), None),
        sender=sender,
    )
    assert summary == {
        "candidates": 1,
        "delivered": 0,
        "still_undelivered": 0,
        "not_actionable": 1,
        "unreadable": 0,
    }
    # A legacy undelivered actionable row must never bypass today's stricter
    # F6/4H policy. Retry re-evaluates fresh evidence and sends no message.
    assert sender.calls == []
    row = inbox.iter_records()[0]
    assert row["notification"]["retry_count"] == 1
    assert row["notification"]["status"] == "RETRY_NOT_ACTIONABLE"


def test_retry_records_a_non_actionable_outcome_without_sending():
    _settle_failed()
    sender = Sender()
    summary = pipeline.retry_undelivered_notifications(
        settings=telegram_settings(),
        now=NOW + timedelta(minutes=2),
        snapshot_provider=lambda _m: ("skip", None, "history unavailable"),
        sender=sender,
    )
    assert summary["not_actionable"] == 1
    assert sender.calls == []
    assert inbox.iter_records()[0]["notification"]["status"] == "RETRY_NOT_ACTIONABLE"


def test_retry_leaves_the_frozen_decision_immutable():
    _settle_failed()
    before = inbox.iter_records()[0]["decision_record"]
    pipeline.retry_undelivered_notifications(
        settings=telegram_settings(),
        now=NOW + timedelta(minutes=2),
        snapshot_provider=lambda _m: ("skip", None, "history unavailable"),
        sender=Sender(),
    )
    after = inbox.iter_records()[0]
    assert after["status"] == "PROCESSED"
    assert after["decision_record"] == before


def test_retry_is_bounded_and_skips_expired_rows():
    _settle_failed(expires_in_minutes=1)
    summary = pipeline.retry_undelivered_notifications(
        settings=telegram_settings(),
        now=NOW + timedelta(hours=2),
        snapshot_provider=lambda _m: ("ok", market(), None),
        sender=Sender(),
    )
    assert summary["candidates"] == 0
    assert summary["delivered"] == 0


# ---------------------------------------------------------------------------
# P1-B: learning evidence reaches a consumption disposition
# ---------------------------------------------------------------------------


class _Candle:
    """A candle with the four prices a real Kraken candle carries.

    ``open``/``close`` default to ``low`` so high/low-only construction still
    yields a coherent bar: measurement geometry validates all four prices
    (ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1).
    """

    def __init__(
        self,
        ts: datetime,
        high: float,
        low: float,
        open: float | None = None,
        close: float | None = None,
    ) -> None:
        self.timestamp = int(ts.timestamp())
        self.high = high
        self.low = low
        self.open = low if open is None else open
        self.close = low if close is None else close


def test_inbox_declares_its_evidence_class_and_consumer():
    _settle_failed()
    status = inbox.inbox_status()
    assert status["evidence_class"] == "ADVISORY_NON_CANONICAL"
    assert status["canonical_handoff"] == "NOT_PERFORMED"
    assert status["consumption"] == "app.jobs.report_tradevisor_learning"


def test_learning_job_measures_persisted_records():
    _settle_failed()
    later = NOW + timedelta(days=2)
    def provider(_market, decided_at):
        grid = decided_at.replace(minute=0, second=0, microsecond=0)
        return [
            _Candle(grid + timedelta(hours=1), 101.0, 99.5),
            _Candle(grid + timedelta(hours=2), 106.0, 100.5),
        ]

    result = learning_job.build_report(
        now=later,
        deployed_sha=SHA_A,
        expected_sha=SHA_A,
        candles_provider=provider,
        measurement_hours=3,
    )
    report = result["report"]
    assert report["status"] == learning.RELEASE_CURRENT
    assert report["policy_promotion"] == "NONE"
    measured = [
        outcome
        for outcome in result["outcomes"].values()
        if outcome["status"] == learning.OUTCOME_MEASURED
    ]
    assert len(measured) == 1
    assert measured[0]["target_1_touched"] is True
    assert measured[0]["fill_attribution"] == learning.FILL_ATTRIBUTION_NONE


def test_learning_job_withholds_metrics_under_release_drift():
    _settle_failed()
    result = learning_job.build_report(
        now=NOW + timedelta(days=2),
        deployed_sha=SHA_A,
        expected_sha=SHA_B,
    )
    assert result["report"]["status"] == learning.RELEASE_DRIFT
    assert result["report"]["cohorts"] == {}
    assert result["report"]["incremental_value"]["status"] == "WITHHELD_RELEASE_DRIFT"


def test_learning_job_reads_the_release_identity_from_the_learning_contract(monkeypatch):
    _settle_failed()
    monkeypatch.setenv("OPIP_DEPLOYED_SHA", SHA_A)
    monkeypatch.setenv("EXPECTED_SHA", SHA_B)
    result = learning_job.build_report(now=NOW + timedelta(days=2))
    assert result["report"]["release_compatibility"] == learning.RELEASE_DRIFT


def test_learning_job_without_candles_reports_pending_not_fabricated():
    _settle_failed()
    result = learning_job.build_report(
        now=NOW + timedelta(days=2), deployed_sha=SHA_A, expected_sha=SHA_A
    )
    statuses = {outcome["status"] for outcome in result["outcomes"].values()}
    assert statuses == {learning.OUTCOME_PENDING}


def test_learning_job_main_prints_a_disposition(capsys, monkeypatch, tmp_path):
    _settle_failed()
    monkeypatch.setenv("OPIP_DEPLOYED_SHA", SHA_A)
    monkeypatch.setenv("EXPECTED_SHA", SHA_B)
    monkeypatch.setenv("OPIP_LEARNING_DATA_ROOT", str(tmp_path))
    learning_job.main()
    printed = capsys.readouterr().out
    assert "RELEASE_DRIFT" in printed
    assert "Consumption disposition: BLOCKED_RELEASE_DRIFT" in printed
    assert "Trade authority changed: False" in printed


def test_consumption_disposition_maps_to_the_existing_vocabulary():
    from app.opip.learning.job_disposition import (
        BLOCKED_RELEASE_DRIFT,
        CONSUMED_EMPTY,
        CONSUMED_OK,
    )

    assert (
        learning_job.consumption_disposition(
            {"release_compatibility": learning.RELEASE_DRIFT, "status": "x"}
        )
        == BLOCKED_RELEASE_DRIFT
    )
    assert (
        learning_job.consumption_disposition(
            {"release_compatibility": learning.RELEASE_CURRENT, "status": "NO_EVIDENCE"}
        )
        == CONSUMED_EMPTY
    )
    assert (
        learning_job.consumption_disposition(
            {"release_compatibility": learning.RELEASE_CURRENT, "status": "CURRENT"}
        )
        == CONSUMED_OK
    )


def test_consumption_summary_is_written_with_no_policy_authority(tmp_path):
    import json

    _settle_failed()
    result = learning_job.build_report(
        now=NOW + timedelta(days=2), deployed_sha=SHA_A, expected_sha=SHA_A
    )
    disposition = learning_job.record_consumption(
        result["report"], data_root=tmp_path, outcomes=result["outcomes"]
    )
    assert disposition == "CONSUMED_OK"
    written = (
        tmp_path / ".learning_consumption" / "tradevisor_learning.json"
    ).read_text(encoding="utf-8")
    payload = json.loads(written)
    assert payload["disposition"] == "CONSUMED_OK"
    assert payload["policy_change_authorized"] is False
    assert payload["trade_authority_changed"] is False
    assert payload["policy_promotion"] == "NONE"
    assert payload["job"] == "tradevisor_learning"


def test_missing_data_root_records_nothing_and_does_not_raise(tmp_path):
    result = learning_job.build_report(
        now=NOW + timedelta(days=2), deployed_sha=SHA_A, expected_sha=SHA_A
    )
    assert learning_job.record_consumption(result["report"], data_root=None) is None


# ---------------------------------------------------------------------------
# P1-02: a stranded PROCESSING row must be recovered by the normal cycle
# ---------------------------------------------------------------------------


def test_normal_cycle_recovers_an_expired_processing_row(isolated, monkeypatch):
    """A dead worker's PROCESSING row is invisible to queue depth, not to the cycle."""
    from app.jobs import run_cycle

    inbox.enqueue(signal(), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key = next(iter(registry["events"]))
    registry["events"][key]["status"] = inbox.STATUS_PROCESSING
    registry["events"][key]["processing_started_at"] = (
        NOW - timedelta(minutes=10)
    ).isoformat()
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    # The queue-depth check cannot see it: no QUEUED and no RETRY rows exist.
    assert inbox.pending_count() == 0

    monkeypatch.setattr(
        run_cycle,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            webhook_secret="x" * 12,
            opip_tradevisor_enabled=True,
            opip_tradevisor_webhook_token="T" * 43,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_and_notify",
        lambda _signal, settings=None, **_kwargs: {
            "decision": "NO_TRADE",
            "reason": "recovered by the normal cycle",
        },
    )

    run_cycle._run_tradevisor_fail_open()

    stored = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"].values()))
    assert stored["status"] == inbox.STATUS_PROCESSED
    assert stored["final_disposition"] == "EVALUATED"


def test_normal_cycle_leaves_a_healthy_processing_row_alone(isolated, monkeypatch):
    """A live lease is not stolen by the cycle."""
    from app.jobs import run_cycle

    inbox.enqueue(signal(), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key = next(iter(registry["events"]))
    registry["events"][key]["status"] = inbox.STATUS_PROCESSING
    registry["events"][key]["processing_started_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    monkeypatch.setattr(
        run_cycle,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            webhook_secret="x" * 12,
            opip_tradevisor_enabled=True,
            opip_tradevisor_webhook_token="T" * 43,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_and_notify",
        lambda _signal, settings=None, **_kwargs: {
            "decision": "NO_TRADE",
            "reason": "must not run",
        },
    )

    run_cycle._run_tradevisor_fail_open()

    stored = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"].values()))
    assert stored["status"] == inbox.STATUS_PROCESSING


# ---------------------------------------------------------------------------
# P1-03: the exact-SHA gate must run before any outcome is computed
# ---------------------------------------------------------------------------


def test_release_drift_never_invokes_the_candle_provider(isolated):
    """A drifted worker must not read candles or expose outcomes."""
    _settle_failed()
    calls: list[tuple[str, datetime]] = []

    def provider(market, decided_at):
        calls.append((market, decided_at))
        return []

    result = learning_job.build_report(
        now=NOW + timedelta(days=2),
        deployed_sha=SHA_A,
        expected_sha=SHA_B,
        candles_provider=provider,
    )

    assert calls == []
    assert result["outcomes"] == {}
    assert result["report"]["status"] == learning.RELEASE_DRIFT
    assert result["report"]["cohorts"] == {}
    assert result["report"]["policy_promotion"] == "NONE"

    disposition = learning_job.record_consumption(
        result["report"], data_root=isolated, outcomes=result["outcomes"]
    )
    assert disposition == learning_job.BLOCKED_RELEASE_DRIFT


def test_unverified_release_never_invokes_the_candle_provider(isolated):
    """An unverifiable worker fails closed before measuring too."""
    _settle_failed()
    calls: list[str] = []

    def provider(market, decided_at):
        calls.append(market)
        return []

    result = learning_job.build_report(
        now=NOW + timedelta(days=2),
        deployed_sha="",
        expected_sha=SHA_A,
        candles_provider=provider,
    )

    assert calls == []
    assert result["outcomes"] == {}
    assert result["report"]["status"] == learning.RELEASE_UNVERIFIED


def test_current_release_still_measures_outcomes(isolated):
    """The gate must not suppress measurement for the exact trusted SHA."""
    _settle_failed()
    calls: list[str] = []

    def provider(market, decided_at):
        calls.append(market)
        grid = decided_at.replace(minute=0, second=0, microsecond=0)
        return [
            _Candle(grid + timedelta(hours=1), 101.0, 99.5),
            _Candle(grid + timedelta(hours=2), 106.0, 100.5),
        ]

    result = learning_job.build_report(
        now=NOW + timedelta(days=2),
        deployed_sha=SHA_A,
        expected_sha=SHA_A,
        candles_provider=provider,
        measurement_hours=3,
    )

    assert calls == ["BTCUSD"]
    assert result["report"]["status"] == learning.RELEASE_CURRENT
    measured = [
        outcome
        for outcome in result["outcomes"].values()
        if outcome["status"] == learning.OUTCOME_MEASURED
    ]
    assert len(measured) == 1
