"""Regression coverage for the Wave 8.2 TradingView Intelligence Bridge.

TradingView is candidate evidence only: these tests exercise the inbox's
own defenses (schema, freshness, replay spacing, price divergence, operator
gating, crash recovery, poison-pill handling, retention) and the two ways
qualified evidence can reach the native scan cycle (merge_native_candidate_
evidence / disabled TradingView-only promotion), without ever touching order
placement — there is none to touch.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import TRADINGVIEW_VERIFICATION_HEADER, router
from app.core.config import Settings, get_settings
from app.models.signal import TradingViewSignalV2
from app.scanner.models import MarketSnapshot
from app.services import tradingview_inbox as tvi


@pytest.fixture(autouse=True)
def tradingview_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(tvi, "INBOX_FILE", tmp_path / "tradingview_v2_inbox.json")
    monkeypatch.setattr(tvi, "LOCK_FILE", tmp_path / ".tradingview_v2_inbox.lock")
    settings = get_settings()
    monkeypatch.setattr(settings, "tradingview_v2_enabled", True)
    return settings


def make_signal(**overrides) -> TradingViewSignalV2:
    base = dict(
        schema_version="2",
        script_version="ohm-signal-v1",
        signal_id="sig-00000001",
        symbol="KRAKEN:BTCUSD",
        exchange="KRAKEN",
        direction="LONG",
        bar_closed_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        timeframe="15",
        bar_confirmed=True,
        regime_4h="BULLISH",
        setup_1h="BREAKOUT",
        entry_trigger_15m="BREAKOUT",
        technical_score=90.0, trend_score=80.0, momentum_score=75.0, volume_score=70.0,
        structure_score=65.0, volatility_score=60.0, tradingview_close=100.0,
        ema_20=99.0, ema_50=95.0, ema_200=90.0, adx=30.0, rsi=60.0, macd_histogram=0.5,
        relative_volume=1.5, atr=2.0, atr_percentage=2.0, vwap=99.5, warnings=[],
    )
    base.update(overrides)
    return TradingViewSignalV2(**base)


def snapshot(**overrides) -> MarketSnapshot:
    values = dict(
        symbol="SOL/USD", last_price=100.0, ema20=99.0, ema50=95.0, ema200=90.0,
        rsi=58.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5,
        atr=2.0, atr_pct=2.0, volume_ratio=1.5, technical_score=90, trend="bullish",
        recent_24h_high=110.0, recent_24h_low=94.0,
        recent_72h_high=116.0, recent_72h_low=88.0,
        momentum_6h_pct=1.0, momentum_24h_pct=3.0, momentum_72h_pct=7.0,
        rolling_24h_range_median_pct=8.0, rolling_24h_range_p75_pct=10.0, rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0, rolling_72h_range_p75_pct=16.0, rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0, rolling_24h_upside_p75_pct=10.0, rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0, rolling_72h_upside_p75_pct=16.0, rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=8.0, rolling_24h_downside_p75_pct=10.0, rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0, rolling_72h_downside_p75_pct=16.0, rolling_72h_downside_p90_pct=20.0,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


class _FakeKrakenTicker:
    def __init__(self, last: float):
        self.last = last

    def get_ticker(self, pair):
        return {"last": self.last}


def _search_allowed(monkeypatch):
    monkeypatch.setattr(
        tvi, "get_operator_decision",
        lambda *a, **k: SimpleNamespace(effective_mode="SEARCH", search_allowed=True, reason="ok"),
    )


def _webhook_client(settings):
    settings.tradingview_webhook_token = "T" * 43
    settings.tradingview_trusted_proxy_ips = "testclient"
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _webhook_payload(signal: TradingViewSignalV2, token: str | None = None) -> dict:
    payload = signal.model_dump(mode="json")
    if token is not None:
        payload["verification_token"] = token
    return payload


# ---------------------------------------------------------------------------
# HTTP boundary authentication and bounded parsing
# ---------------------------------------------------------------------------


def test_webhook_requires_deployment_token_before_schema_details(tradingview_settings):
    client = _webhook_client(tradingview_settings)
    response = client.post(
        "/webhooks/tradingview/v2",
        json={"not": "the schema"},
        headers={TRADINGVIEW_VERIFICATION_HEADER: tradingview_settings.tradingview_internal_verification_value},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_webhook_accepts_valid_token_but_never_persists_it(tradingview_settings):
    client = _webhook_client(tradingview_settings)
    response = client.post(
        "/webhooks/tradingview/v2",
        json=_webhook_payload(make_signal(), tradingview_settings.tradingview_webhook_token),
        headers={TRADINGVIEW_VERIFICATION_HEADER: tradingview_settings.tradingview_internal_verification_value},
    )
    assert response.status_code == 202
    registry = tvi.load_json(tvi.INBOX_FILE)
    assert "verification_token" not in next(iter(registry["events"].values()))["payload"]
    assert tradingview_settings.tradingview_webhook_token not in repr(registry)


def test_webhook_enforces_incremental_body_limit(tradingview_settings):
    client = _webhook_client(tradingview_settings)
    tradingview_settings.tradingview_max_payload_bytes = 64
    response = client.post(
        "/webhooks/tradingview/v2",
        content=b"x" * 65,
        headers={TRADINGVIEW_VERIFICATION_HEADER: tradingview_settings.tradingview_internal_verification_value},
    )
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# resolve_kraken_pair
# ---------------------------------------------------------------------------


def test_resolve_kraken_pair_matches_ohm_primary_pair_format():
    assert tvi.resolve_kraken_pair("KRAKEN:BTCUSD") == "BTCUSD"


def test_resolve_kraken_pair_normalizes_xbt_alias():
    assert tvi.resolve_kraken_pair("KRAKEN:XBTUSD") == "BTCUSD"


def test_resolve_kraken_pair_normalizes_xdg_alias():
    assert tvi.resolve_kraken_pair("XDGUSD") == "DOGEUSD"


def test_resolve_kraken_pair_rejects_ambiguous_symbol():
    with pytest.raises(ValueError):
        tvi.resolve_kraken_pair("NOTAPAIR")


def test_signal_model_rejects_non_kraken_exchange():
    with pytest.raises(ValueError):
        make_signal(exchange="COINBASE")


def test_policy_requires_prefixed_kraken_chart_symbol():
    signal = make_signal(symbol="BTCUSD")
    with pytest.raises(ValueError, match="Kraken chart"):
        tvi.validate_event_policy(signal)


def test_enabled_bridge_requires_long_url_safe_deployment_token():
    with pytest.raises(ValueError, match="TRADINGVIEW_WEBHOOK_TOKEN"):
        Settings(
            _env_file=None,
            webhook_secret="operator-secret-value",
            tradingview_v2_enabled=True,
            tradingview_webhook_token="short",
        )


def test_production_validator_normalizes_environment_and_rejects_default_proxy_secret():
    with pytest.raises(ValueError, match="INTERNAL_VERIFICATION_VALUE"):
        Settings(
            _env_file=None,
            app_env="  ProDucTion  ",
            webhook_secret="operator-secret-value",
            tradingview_v2_enabled=True,
            tradingview_webhook_token="A" * 43,
            tradingview_internal_verification_value="  VERIFIED-BY-TRUSTED-PROXY  ",
        )


# ---------------------------------------------------------------------------
# enqueue: dedup, replay spacing, freshness
# ---------------------------------------------------------------------------


def test_enqueue_accepts_new_signal_and_dedups_exact_replay():
    signal = make_signal()
    row, is_new = tvi.enqueue(signal)
    assert is_new is True
    row_again, is_new_again = tvi.enqueue(signal)
    assert is_new_again is False
    assert row_again["deduplication_key"] == row["deduplication_key"]


def test_enqueue_rejects_resubmission_faster_than_one_bar_interval():
    first = make_signal()
    tvi.enqueue(first)
    second = make_signal(signal_id="sig-00000002", bar_closed_at=first.bar_closed_at + timedelta(seconds=30))
    with pytest.raises(ValueError, match="resubmitted faster"):
        tvi.enqueue(second, received_at=second.bar_closed_at + timedelta(seconds=5))


def test_enqueue_accepts_signal_spaced_beyond_min_bar_interval():
    first = make_signal()
    tvi.enqueue(first)
    later_bar = first.bar_closed_at + timedelta(seconds=tvi.MIN_BAR_SPACING_SECONDS + 30)
    second = make_signal(signal_id="sig-00000002", bar_closed_at=later_bar)
    _, is_new = tvi.enqueue(second, received_at=later_bar + timedelta(seconds=5))
    assert is_new is True


def test_validate_event_policy_rejects_stale_event():
    stale = make_signal(bar_closed_at=datetime.now(timezone.utc) - timedelta(seconds=10_000))
    with pytest.raises(ValueError, match="stale"):
        tvi.validate_event_policy(stale)


def test_validate_event_policy_rejects_future_dated_event():
    future = make_signal(bar_closed_at=datetime.now(timezone.utc) + timedelta(seconds=1_000))
    with pytest.raises(ValueError, match="future-dated"):
        tvi.validate_event_policy(future)


def test_inbox_status_tracks_queue_depth_and_replay_spacing_rejections():
    first = make_signal()
    tvi.enqueue(first)
    replay = make_signal(signal_id="sig-00000002", bar_closed_at=first.bar_closed_at + timedelta(seconds=30))
    with pytest.raises(ValueError):
        tvi.enqueue(replay, received_at=replay.bar_closed_at + timedelta(seconds=5))
    status = tvi.inbox_status()
    assert status["total_events_tracked"] == 1
    assert status["queue_depth"] == 1
    assert status["replay_spacing_rejections"] == 1


def test_inbox_capacity_is_bounded(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tradingview_max_inbox_events", 1)
    first = make_signal()
    tvi.enqueue(first)
    second = make_signal(
        signal_id="sig-cap-00002",
        symbol="KRAKEN:ETHUSD",
        bar_closed_at=first.bar_closed_at,
    )
    with pytest.raises(tvi.TradingViewInboxFullError):
        tvi.enqueue(second)
    assert tvi.inbox_status()["capacity_rejections"] == 1


# ---------------------------------------------------------------------------
# retention pruning / crash recovery (unit-level, direct registry manipulation)
# ---------------------------------------------------------------------------


def test_prune_terminal_events_removes_only_stale_terminal_rows():
    now = datetime.now(timezone.utc)
    registry = {
        "events": {
            "old_processed": {"status": "PROCESSED", "processed_at": (now - timedelta(days=10)).isoformat()},
            "recent_processed": {"status": "PROCESSED", "processed_at": now.isoformat()},
            "queued": {"status": "QUEUED", "received_at": now.isoformat()},
        },
        "metrics": {},
    }
    pruned = tvi._prune_terminal_events(registry, now, retention_seconds=7 * 24 * 3600)
    assert pruned == 1
    assert set(registry["events"]) == {"recent_processed", "queued"}
    assert registry["metrics"]["pruned_events"] == 1


def test_reclaim_stuck_processing_recovers_crashed_and_timed_out_rows():
    now = datetime.now(timezone.utc)
    registry = {
        "events": {
            "timed_out": {"status": "PROCESSING", "processing_started_at": (now - timedelta(seconds=1000)).isoformat()},
            "missing_timestamp": {"status": "PROCESSING"},
            "fresh": {"status": "PROCESSING", "processing_started_at": now.isoformat()},
        },
        "metrics": {},
    }
    reclaimed = tvi._reclaim_stuck_processing(registry, now, timeout_seconds=120)
    assert reclaimed == 2
    assert registry["events"]["timed_out"]["status"] == "RETRY"
    assert registry["events"]["missing_timestamp"]["status"] == "RETRY"
    assert registry["events"]["fresh"]["status"] == "PROCESSING"


# ---------------------------------------------------------------------------
# process_queued_events
# ---------------------------------------------------------------------------


def test_process_queued_events_qualifies_a_clean_signal(monkeypatch):
    signal = make_signal()
    tvi.enqueue(signal)
    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(client=_FakeKrakenTicker(100.3), now=signal.bar_closed_at + timedelta(seconds=30))
    assert summary["qualified"] == 1
    assert summary["processed"] == 1
    status = tvi.inbox_status()
    assert status["successful_processing"] == 1
    assert status["qualified_candidates"] == 1


def test_process_queued_events_rejects_on_price_divergence(monkeypatch):
    signal = make_signal(signal_id="sig-div-0001")
    tvi.enqueue(signal)
    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(client=_FakeKrakenTicker(150.0), now=signal.bar_closed_at + timedelta(seconds=30))
    assert summary["rejected"] == 1
    assert summary["qualified"] == 0
    row = next(iter(tvi.load_json(tvi.INBOX_FILE)["events"].values()))
    assert row["final_disposition"] == "PRICE_DIVERGENCE_REJECT"


def test_process_queued_events_rejects_when_operator_not_in_search(monkeypatch):
    signal = make_signal(signal_id="sig-gate-0001")
    tvi.enqueue(signal)
    monkeypatch.setattr(
        tvi, "get_operator_decision",
        lambda *a, **k: SimpleNamespace(effective_mode="MONITOR", search_allowed=False, reason="quiet hours"),
    )
    summary = tvi.process_queued_events(client=_FakeKrakenTicker(100.3), now=signal.bar_closed_at + timedelta(seconds=30))
    assert summary["rejected"] == 1
    row = next(iter(tvi.load_json(tvi.INBOX_FILE)["events"].values()))
    assert row["final_disposition"] == "OPERATOR_GATE_REJECT"


def test_process_queued_events_poisons_after_max_attempts(monkeypatch):
    signal = make_signal(signal_id="sig-poison-01")
    tvi.enqueue(signal)
    registry = tvi.load_json(tvi.INBOX_FILE)
    key = next(iter(registry["events"]))
    registry["events"][key]["attempts"] = get_settings().tradingview_max_attempts
    tvi.save_json_atomic(tvi.INBOX_FILE, registry)
    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(client=_FakeKrakenTicker(100.3), now=signal.bar_closed_at + timedelta(seconds=30))
    assert summary["poisoned"] == 1
    row = tvi.load_json(tvi.INBOX_FILE)["events"][key]
    assert row["status"] == "POISONED"
    assert row["final_disposition"] == "MAX_ATTEMPTS_EXCEEDED"


def test_process_queued_events_reclaims_and_reprocesses_stuck_row(monkeypatch):
    signal = make_signal(signal_id="sig-stuck-001")
    tvi.enqueue(signal)
    registry = tvi.load_json(tvi.INBOX_FILE)
    key = next(iter(registry["events"]))
    registry["events"][key]["status"] = "PROCESSING"
    registry["events"][key]["processing_started_at"] = signal.bar_closed_at.isoformat()
    tvi.save_json_atomic(tvi.INBOX_FILE, registry)
    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(client=_FakeKrakenTicker(100.3), now=signal.bar_closed_at + timedelta(seconds=200))
    assert summary["reclaimed"] == 1
    assert summary["qualified"] == 1


def test_process_queued_events_sanitizes_exception_message(monkeypatch):
    signal = make_signal(signal_id="sig-boom-0001")
    tvi.enqueue(signal)

    class _ExplodingClient:
        def get_ticker(self, pair):
            raise RuntimeError("sensitive upstream detail should never be persisted")

    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(client=_ExplodingClient(), now=signal.bar_closed_at + timedelta(seconds=30))
    assert summary["retried"] == 1
    row = next(iter(tvi.load_json(tvi.INBOX_FILE)["events"].values()))
    assert row["status"] == "RETRY"
    assert "sensitive upstream detail" not in row["failure_reason"]
    assert row["failure_reason"] == "RuntimeError during processing (see application logs)"


def test_process_queued_events_is_a_noop_when_bridge_disabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tradingview_v2_enabled", False)
    signal = make_signal(signal_id="sig-off-00001")
    tvi.enqueue(signal)
    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(client=_FakeKrakenTicker(100.3), now=signal.bar_closed_at + timedelta(seconds=30))
    assert summary["checked"] == 0
    assert summary["processed"] == 0


def test_processing_caller_cannot_exceed_configured_batch_limit(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tradingview_processing_batch_limit", 1)
    first = make_signal(signal_id="sig-batch-001")
    second = make_signal(
        signal_id="sig-batch-002",
        symbol="KRAKEN:ETHUSD",
        bar_closed_at=first.bar_closed_at,
    )
    tvi.enqueue(first)
    tvi.enqueue(second)
    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(
        client=_FakeKrakenTicker(100.0),
        now=first.bar_closed_at + timedelta(seconds=30),
        limit=10,
    )
    assert summary["checked"] == 1
    assert tvi.inbox_status()["queue_depth"] == 1


# ---------------------------------------------------------------------------
# merge_native_candidate_evidence / tradingview_only_candidates
# ---------------------------------------------------------------------------


def _qualify(signal, monkeypatch):
    tvi.enqueue(signal)
    _search_allowed(monkeypatch)
    summary = tvi.process_queued_events(client=_FakeKrakenTicker(signal.tradingview_close + 0.3), now=signal.bar_closed_at + timedelta(seconds=30))
    assert summary["qualified"] == 1


def test_merge_native_candidate_evidence_tags_only_the_matching_native_candidate(monkeypatch):
    signal = make_signal()
    _qualify(signal, monkeypatch)
    now = signal.bar_closed_at + timedelta(seconds=60)

    native_btc = SimpleNamespace(primary_pair="BTCUSD", trade_direction="LONG", technical_score=70.0)
    native_eth = SimpleNamespace(primary_pair="ETHUSD", trade_direction="LONG", technical_score=95.0)
    attached = tvi.merge_native_candidate_evidence([native_btc, native_eth], now=now)

    assert attached == 1
    assert native_btc._tradingview_evidence["source_classification"] == "TRADINGVIEW_CONFIRMED_NATIVE"
    assert not hasattr(native_eth, "_tradingview_evidence")
    assert tvi.inbox_status()["native_tradingview_duplicates"] == 1


def test_tradingview_only_candidate_promotion_is_disabled(monkeypatch):
    signal = make_signal()
    _qualify(signal, monkeypatch)
    now = signal.bar_closed_at + timedelta(seconds=60)

    btc_snapshot = snapshot(symbol="BTCUSD", primary_pair="BTCUSD", technical_score=80)
    native_candidates = [snapshot(symbol="ETHUSD", primary_pair="ETHUSD", trade_direction="LONG", technical_score=70)]

    added = tvi.tradingview_only_candidates([btc_snapshot], native_candidates, now=now)
    assert added == []
