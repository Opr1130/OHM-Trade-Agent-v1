"""ATDD: Tradevisor advisory-observation delivery reliability.

Bounded reliability assessment of the observation lifecycle:
ingress -> inbox -> evaluation -> settlement -> candidate -> policy reservation
-> transport -> acknowledgement -> retention.

Every scenario here is deterministic: the inbox is a real isolated store, the
clock is injected, and the transport is a fake. Nothing is deployed, activated
or pushed, and no learning, retention or risk contract is modified.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import notification_policy
from app.services import tradevisor_observation as observation
from app.services.tradevisor_ingress import TradevisorSignal
from app.services.tradevisor_pipeline import evaluate_and_notify

NOW = datetime(2026, 10, 23, 13, 0, 5, tzinfo=timezone.utc)
BAR_OPEN = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
MARKETS = (
    "BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XRPUSD", "LINKUSD", "AVAXUSD", "NEARUSD",
)

pytestmark = pytest.mark.acceptance


def settings(**over) -> Settings:
    values = dict(_env_file=None, webhook_secret="x" * 12)
    values.update(over)
    return Settings(**values)


def delivery_settings(**over) -> Settings:
    values = dict(
        opip_tradevisor_observation_enabled=True,
        telegram_enabled=True,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
    )
    values.update(over)
    return settings(**values)


def enabled_settings(**over) -> Settings:
    """Settings with Tradevisor ingress enabled (config requires a token)."""
    values = dict(
        opip_tradevisor_enabled=True,
        opip_tradevisor_webhook_token="T" * 43,
    )
    values.update(over)
    return settings(**values)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Policy state, budget and lock all live inside the test's tmp_path."""
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "policy.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".policy.lock")
    return tmp_path


@pytest.fixture()
def durable_inbox(tmp_path, monkeypatch):
    from app.services import tradevisor_inbox as inbox

    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: settings())
    return inbox


def sender(sink: list[str], *, delivered: bool = True):
    def send(**kwargs):
        if delivered:
            sink.append(kwargs["message"])
        return SimpleNamespace(delivered=delivered, message_id=len(sink))

    return send


def market(**over) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD", last_price=100.0, ema20=99.0, ema50=95.0, ema200=90.0,
        rsi=58.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5, atr=2.0,
        atr_pct=2.0, volume_ratio=1.5, technical_score=90, trend="bullish",
        recent_24h_high=112.0, recent_24h_low=94.0, recent_72h_high=120.0,
        recent_72h_low=88.0, momentum_6h_pct=1.0, momentum_24h_pct=3.0,
        momentum_72h_pct=7.0, rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0, rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0, rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0, rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0, rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0, rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0, rolling_24h_downside_median_pct=8.0,
        rolling_24h_downside_p75_pct=10.0, rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0, rolling_72h_downside_p75_pct=16.0,
        rolling_72h_downside_p90_pct=20.0, ticker_last=100.0,
        execution_validation=ExecutionValidation(
            status="VALID", book_coverage_status="COMPLETE", warnings=[],
            best_bid=99.95, best_ask=100.05, mid_price=100.0, spread_pct=0.1,
            spread_bps=5.0, validation_notional_usd=250.0, buy_vwap=100.02,
            sell_vwap=99.98, buy_fully_covered=True, sell_fully_covered=True,
            estimated_visible_round_trip_market_drag_pct=0.10,
            recent_trade_status="FRESH", latest_trade_price=100.0,
            latest_trade_age_seconds=10.0, recent_trade_count=20,
        ),
    )
    values.update(over)
    return MarketSnapshot(**values)


def pipeline_result(
    sig: TradevisorSignal, *, equity: float = 10_000.0, now: datetime = NOW
) -> dict:
    return evaluate_and_notify(
        sig,
        settings=settings(),
        now=now,
        snapshot_provider=lambda _market: ("ok", market(), None),
        account_equity=equity,
        active_trades=[],
    )


def observation_signal(
    index: int, *, bar_hours: int = 0, when: datetime = NOW
) -> TradevisorSignal:
    """One distinct observation signal (unique market/bar/direction tuple)."""
    name = MARKETS[index % len(MARKETS)]
    return TradevisorSignal(
        dedup_id=f"rel-{index}-{name}",
        signal="BUY",
        instrument=f"{name[:-3]}/USD",
        market=name,
        source_exchange="COINBASE",
        source_symbol=name,
        timeframe_minutes=60,
        bar_opened_at=BAR_OPEN - timedelta(hours=bar_hours),
        bar_closed_at=BAR_OPEN - timedelta(hours=bar_hours - 1),
        triggered_at=when,
        received_at=when,
        signal_price=100.0,
        alert_config_version="tradevisor-1h-buy",
    )


def settle(
    inbox, count: int, *, when=NOW, equity: float = 10_000.0, offset: int = 0
) -> dict:
    """Enqueue and settle ``count`` observations through the real inbox."""
    for index in range(offset, offset + count):
        inbox.enqueue(observation_signal(index, when=when), now=when)
    totals = {"checked": 0, "processed": 0}
    for _ in range(20):
        result = inbox.process_queued_events(
            evaluator=lambda queued: pipeline_result(queued, equity=equity, now=when),
            now=when,
        )
        for key in totals:
            totals[key] += int(result.get(key, 0) or 0)
        if int(result.get("checked", 0) or 0) == 0:
            break
    return totals


# ===========================================================================
# AC-036 - delivery guarantee and every ambiguous outcome
# ===========================================================================


def _pending(**kwargs):
    kwargs.setdefault("settings", settings())
    return observation.pending_observations(**kwargs)


def test_ac_036_crash_before_transport_leaves_evidence_available(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: settlement does not lose the report."""
    settle(durable_inbox, 2)
    # The process died between settlement and the transport call.
    assert len(_pending(now=NOW)) == 2
    sent: list[str] = []
    outcome = observation.send_observation_digest(
        _pending(now=NOW), settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert outcome.status == observation.STATUS_DELIVERED
    assert len(sent) == 1
    assert _pending(now=NOW) == []


def test_ac_036_missing_acknowledgement_after_delivery_is_repaired_not_duplicated(
    durable_inbox, monkeypatch
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: a lost ack converges, no resend."""
    settle(durable_inbox, 1)
    pending = _pending(now=NOW)

    # The transport accepted the digest, then the acknowledgement write died.
    original_marker = observation._mark_reported
    monkeypatch.setattr(observation, "_mark_reported", lambda *_a, **_k: 0)
    sent: list[str] = []
    first = observation.send_observation_digest(
        pending, settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert first.status == observation.STATUS_DELIVERED
    monkeypatch.setattr(observation, "_mark_reported", original_marker)

    # The row is still unreported, so the next cycle must resolve it without a
    # second send: the content is already a confirmed emission.
    assert len(_pending(now=NOW)) == 1
    second = observation.send_observation_digest(
        _pending(now=NOW), settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert second.status == observation.STATUS_ALREADY_DELIVERED
    assert len(sent) == 1, "a confirmed emission must never be sent twice"
    assert ">=1" in (second.reason or "") or "repaired" in (second.reason or "")
    # The repair wrote the missing acknowledgement, so the backlog is clear.
    assert _pending(now=NOW) == []


def test_ac_036_transport_timeout_is_an_unknown_outcome_and_is_retried(
    durable_inbox,
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: unknown outcome = at-least-once."""
    settle(durable_inbox, 1)
    pending = _pending(now=NOW)

    def timing_out(**_kwargs):
        raise TimeoutError("read timeout after the request was written")

    unknown = observation.send_observation_digest(
        pending, settings=delivery_settings(), now=NOW, sender=timing_out
    )
    assert unknown.status == observation.STATUS_SEND_FAILED
    assert "TimeoutError" in (unknown.reason or "")
    assert len(_pending(now=NOW)) == 1

    # A later retry delivers: the receiver may have already seen it, so the
    # guarantee for this path is at-least-once, not exactly-once.
    sent: list[str] = []
    retry = observation.send_observation_digest(
        _pending(now=NOW), settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert retry.status == observation.STATUS_DELIVERED
    assert len(sent) == 1


def test_ac_036_transport_refusal_keeps_evidence_pending(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: refusal is not delivery."""
    settle(durable_inbox, 1)
    refused = observation.send_observation_digest(
        _pending(now=NOW),
        settings=delivery_settings(),
        now=NOW,
        sender=sender([], delivered=False),
    )
    assert refused.status == observation.STATUS_SEND_FAILED
    assert len(_pending(now=NOW)) == 1


def test_ac_036_policy_state_write_failure_after_delivery_is_repairable(
    durable_inbox, monkeypatch
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: a ledger fault cannot strand rows."""
    settle(durable_inbox, 1)
    pending = _pending(now=NOW)
    sent: list[str] = []

    from app.services import registry_io

    def broken(*_args, **_kwargs):
        raise OSError("state store unavailable")

    original_save = registry_io.save_json_atomic
    monkeypatch.setattr(registry_io, "save_json_atomic", broken)
    first = observation.send_observation_digest(
        pending, settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert first.status == observation.STATUS_DELIVERED  # the send did happen
    monkeypatch.setattr(registry_io, "save_json_atomic", original_save)

    second = observation.send_observation_digest(
        _pending(now=NOW), settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert second.status == observation.STATUS_ALREADY_DELIVERED
    assert len(sent) == 1


def test_ac_036_concurrent_worker_reservation_expires(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: a dead worker cannot block forever."""
    settle(durable_inbox, 1)
    pending = _pending(now=NOW)
    fingerprint = observation._fingerprint(pending)

    held = notification_policy.reserve_emit(
        identity=observation.OBSERVATION_IDENTITY,
        event_type=observation.OBSERVATION_EVENT_TYPE,
        fingerprint=fingerprint,
        lease_seconds=60,
        cooldown_seconds=1,
        now=NOW,
    )
    assert held is not None
    sent: list[str] = []
    blocked = observation.send_observation_digest(
        pending, settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert blocked.status == observation.STATUS_SUPPRESSED_POLICY
    assert sent == []

    # After the abandoned reservation's lease expires the next worker proceeds.
    later = NOW + timedelta(seconds=120)
    recovered = observation.send_observation_digest(
        _pending(now=later), settings=delivery_settings(), now=later, sender=sender(sent)
    )
    assert recovered.status == observation.STATUS_DELIVERED
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# AC-037 - bounded backlog: window expiry and saturation are observable
# ---------------------------------------------------------------------------


def test_ac_037_recovery_window_expiry_is_counted_not_silent(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-037: aged evidence is reported."""
    old = NOW - timedelta(hours=80)
    settle(durable_inbox, 1, when=old)
    settle(durable_inbox, 1, when=NOW, offset=1)

    backlog = observation.observation_backlog(now=NOW, settings=settings())
    assert backlog["total_rows"] == 2
    assert backlog["eligible"] == 1
    assert backlog["aged_out"] == 1
    assert backlog["window_minutes"] >= 60
    # The horizon covers a full per-call backlog at the governed cadence.
    cooldown_minutes = observation._effective_cooldown_seconds(settings()) // 60
    assert backlog["window_minutes"] >= cooldown_minutes * 2
    assert len(_pending(now=NOW)) == 1


def test_ac_037_backlog_is_capped_per_call_without_losing_rows(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-037: bounded work, no deletion."""
    settle(durable_inbox, 12)
    rows_before = len(durable_inbox.iter_records())
    pending = _pending(now=NOW)
    assert len(pending) == 12
    assert len(pending) <= observation.MAX_OBSERVATIONS_PER_DIGEST * 4
    backlog = observation.observation_backlog(now=NOW, settings=settings())
    assert backlog["eligible"] == 12
    assert backlog["aged_out"] == 0
    assert len(durable_inbox.iter_records()) == rows_before


def test_ac_037_sustained_ingress_drains_across_cycles_without_loss(
    durable_inbox, tmp_path
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-037: throughput catches up, once each."""
    settle(durable_inbox, 12)
    delivered: list[str] = []
    when = NOW
    for _ in range(6):
        pending = _pending(now=when)
        if not pending:
            break
        observation.send_observation_digest(
            pending, settings=delivery_settings(), now=when, sender=sender(delivered)
        )
        when = when + timedelta(hours=7)

    assert len(delivered) == 3, "12 rows at 5 per digest needs three digests"
    assert "5 observation(s)" in delivered[0]
    assert "2 observation(s)" in delivered[2]
    # Every row ends up acknowledged exactly once, and the backlog is clear.
    state = json.loads((tmp_path / "policy.json").read_text(encoding="utf-8"))
    markers = [k for k in state if k.startswith(observation.OBSERVATION_MARKER_PREFIX)]
    assert len(markers) == 12
    assert _pending(now=when) == []


def test_ac_037_inbox_capacity_rejects_ingress_without_losing_pending(
    durable_inbox, monkeypatch
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-037: capacity backpressure, no loss."""
    from app.services.tradevisor_inbox import TradevisorInboxFullError

    monkeypatch.setattr(
        durable_inbox,
        "get_settings",
        lambda: settings(opip_tradevisor_max_inbox_events=100),
    )
    settle(durable_inbox, 2)
    for index in range(2, 100):
        durable_inbox.enqueue(observation_signal(index, bar_hours=index + 1), now=NOW)
    assert len(durable_inbox.iter_records()) == 100

    with pytest.raises(TradevisorInboxFullError):
        durable_inbox.enqueue(observation_signal(500, bar_hours=900), now=NOW)

    # Existing evidence is untouched and still deliverable.
    assert len(_pending(now=NOW)) == 2
    sent: list[str] = []
    assert observation.send_observation_digest(
        _pending(now=NOW), settings=delivery_settings(), now=NOW, sender=sender(sent)
    ).status == observation.STATUS_DELIVERED


def _seed_state(tmp_path, entries: dict) -> None:
    (tmp_path / "policy.json").write_text(json.dumps(entries), encoding="utf-8")


def test_ac_038_marker_compaction_prunes_old_and_keeps_active(tmp_path):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: markers are bounded bookkeeping."""
    old = (NOW - timedelta(days=60)).isoformat()
    fresh = (NOW - timedelta(hours=2)).isoformat()
    _seed_state(
        tmp_path,
        {
            f"{observation.OBSERVATION_MARKER_PREFIX}old:{observation.OBSERVATION_EVENT_TYPE}": {
                "fingerprint": "x",
                "sent_at": old,
            },
            f"{observation.OBSERVATION_MARKER_PREFIX}fresh:{observation.OBSERVATION_EVENT_TYPE}": {
                "fingerprint": "y",
                "sent_at": fresh,
            },
            f"{observation.OBSERVATION_IDENTITY}:{observation.OBSERVATION_EVENT_TYPE}": {
                "fingerprint": "z",
                "sent_at": fresh,
            },
            "BTCUSD:INVALIDATED": {"fingerprint": "p", "sent_at": fresh},
        },
    )
    removed = observation.compact_markers(now=NOW)
    assert removed == 1

    state = json.loads((tmp_path / "policy.json").read_text(encoding="utf-8"))
    assert f"{observation.OBSERVATION_MARKER_PREFIX}old:{observation.OBSERVATION_EVENT_TYPE}" not in state
    assert f"{observation.OBSERVATION_MARKER_PREFIX}fresh:{observation.OBSERVATION_EVENT_TYPE}" in state
    # Non-marker policy entries and the digest identity are never touched.
    assert f"{observation.OBSERVATION_IDENTITY}:{observation.OBSERVATION_EVENT_TYPE}" in state
    assert "BTCUSD:INVALIDATED" in state


def test_ac_038_acknowledgement_write_compacts_in_the_same_pass(durable_inbox, tmp_path):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: growth is bounded on the write path."""
    _seed_state(
        tmp_path,
        {
            f"{observation.OBSERVATION_MARKER_PREFIX}ancient:{observation.OBSERVATION_EVENT_TYPE}": {
                "fingerprint": "x",
                "sent_at": (NOW - timedelta(days=90)).isoformat(),
            }
        },
    )
    settle(durable_inbox, 1)
    sent: list[str] = []
    assert observation.send_observation_digest(
        _pending(now=NOW), settings=delivery_settings(), now=NOW, sender=sender(sent)
    ).status == observation.STATUS_DELIVERED

    state = json.loads((tmp_path / "policy.json").read_text(encoding="utf-8"))
    assert f"{observation.OBSERVATION_MARKER_PREFIX}ancient:{observation.OBSERVATION_EVENT_TYPE}" not in state
    markers = [key for key in state if key.startswith(observation.OBSERVATION_MARKER_PREFIX)]
    assert len(markers) == 1, "only the new acknowledgement remains"


def test_ac_038_protection_alerts_survive_saturated_observation_backlog(
    durable_inbox, tmp_path
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: F11 priority is never starved."""
    # A large marker population plus a real backlog of undelivered observations.
    stamps = (NOW - timedelta(hours=1)).isoformat()
    bulk = {
        f"{observation.OBSERVATION_MARKER_PREFIX}bulk-{index}:"
        f"{observation.OBSERVATION_EVENT_TYPE}": {"fingerprint": "f", "sent_at": stamps}
        for index in range(5000)
    }
    _seed_state(tmp_path, bulk)
    settle(durable_inbox, 6)
    assert len(_pending(now=NOW)) == 6

    # Protection is a critical event: it bypasses the noncritical budget and the
    # observation cooldown entirely.
    protected = notification_policy.should_emit(
        identity="BTCUSD:INVALIDATED",
        event_type="INVALIDATED",
        fingerprint="stop-hit",
        now=NOW,
    )
    assert protected is True
    token = notification_policy.reserve_emit(
        identity="BTCUSD:INVALIDATED",
        event_type="INVALIDATED",
        fingerprint="stop-hit",
        now=NOW,
    )
    assert token is not None
    # Observation delivery is not enabled to compete with it.
    assert observation.OBSERVATION_EVENT_TYPE not in notification_policy.CRITICAL_EVENTS


def test_ac_038_observation_lookup_stays_bounded_with_many_markers(tmp_path):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: marker volume cannot stall a cycle."""
    stamps = (NOW - timedelta(hours=1)).isoformat()
    _seed_state(
        tmp_path,
        {
            f"{observation.OBSERVATION_MARKER_PREFIX}bulk-{index}:"
            f"{observation.OBSERVATION_EVENT_TYPE}": {"fingerprint": "f", "sent_at": stamps}
            for index in range(20000)
        },
    )
    started = datetime.now(timezone.utc)
    assert observation.observation_already_reported("bulk-19999") is True
    assert observation.observation_already_reported("absent") is False
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    assert elapsed < 5.0, f"marker lookup took {elapsed:.2f}s with 20000 markers"


# ===========================================================================
# AC-036 / AC-038 - operational hardening (cost, boundary, real transport)
# ===========================================================================


def _durable_record(index: int) -> dict:
    """One settled inbox row in its durable shape, with a distinct identity."""
    dedup = f"row-{index}"
    return {
        "dedup_id": dedup,
        "signal": {"market": "BTCUSD", "dedup_id": dedup},
        "decision_record": {
            "rejection_code": "ADVISORY_EVIDENCE_INCOMPLETE",
            "decision": "WATCH",
            "dedup_id": dedup,
            "market": "BTCUSD",
            "evaluated_at": NOW.isoformat(),
            "timeframe_minutes": 60,
            "evidence": {"timing": {"source_bar_closed_at": BAR_OPEN.isoformat()}},
        },
    }


def _count_policy_reads(monkeypatch) -> list:
    """Record every notification-state read this module performs."""
    reads: list = []
    real_load = observation.load_json

    def counting(path):
        reads.append(path)
        return real_load(path)

    monkeypatch.setattr(observation, "load_json", counting)
    return reads


def test_ac_038_disabled_observation_path_skips_inbox_and_policy_reads(
    monkeypatch, capsys
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: an off class must cost nothing."""
    from app.jobs import drain_tradevisor_inbox as drain

    processed: list[int] = []
    reads = _count_policy_reads(monkeypatch)
    monkeypatch.setattr(drain, "get_settings", lambda: enabled_settings())
    monkeypatch.setattr(
        drain,
        "process_queued_events",
        lambda evaluator: processed.append(1) or {"checked": 0, "processed": 0},
    )
    monkeypatch.setattr(drain, "pending_count", lambda: 0)
    monkeypatch.setattr(
        drain,
        "pending_observations",
        lambda **kwargs: pytest.fail("disabled observations performed an inbox scan"),
    )
    monkeypatch.setattr(
        drain,
        "send_observation_digest",
        lambda *args, **kwargs: pytest.fail("disabled observations attempted delivery"),
    )

    drain.main()

    # The Tradevisor drain itself is untouched by the observation switch.
    assert processed, "queued Tradevisor processing must still run"
    assert reads == [], "the disabled path must not read the notification state"
    assert "observations: DISABLED" in capsys.readouterr().out


def test_ac_038_disabled_observation_path_keeps_drain_and_lease_recovery(monkeypatch):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: expiry recovery is not disabled."""
    from app.jobs import run_cycle
    from app.services import tradevisor_inbox, tradevisor_pipeline

    processed: list[int] = []
    monkeypatch.setattr(run_cycle, "get_settings", lambda: enabled_settings())
    monkeypatch.setattr(
        tradevisor_inbox,
        "process_queued_events",
        lambda evaluator: processed.append(1) or {"checked": 0, "processed": 0},
    )
    monkeypatch.setattr(tradevisor_inbox, "pending_count", lambda: 0)
    monkeypatch.setattr(
        tradevisor_pipeline,
        "retry_undelivered_notifications",
        lambda **kwargs: {"candidates": 0, "delivered": 0, "not_actionable": 0},
    )
    monkeypatch.setattr(
        observation,
        "pending_observations",
        lambda **kwargs: pytest.fail("disabled observations performed an inbox scan"),
    )
    monkeypatch.setattr(
        observation,
        "send_observation_digest",
        lambda *args, **kwargs: pytest.fail("disabled observations attempted delivery"),
    )

    run_cycle._run_tradevisor_fail_open()

    assert processed, "lease reclamation still runs inside process_queued_events"


def test_ac_038_recovery_lookup_reads_policy_state_once_at_any_scale(
    tmp_path, monkeypatch
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: reads are O(1) in rows and state."""
    stamp = (NOW - timedelta(hours=1)).isoformat()
    _seed_state(
        tmp_path,
        {
            f"{observation.OBSERVATION_MARKER_PREFIX}settled-{index}:"
            f"{observation.OBSERVATION_EVENT_TYPE}": {
                "fingerprint": "f",
                "sent_at": stamp,
            }
            for index in range(3600)
        },
    )
    reads = _count_policy_reads(monkeypatch)

    for rows in (2000, 50000):
        records = [_durable_record(index) for index in range(rows)]
        reads.clear()
        pending = observation.pending_observations(
            now=NOW, records=records, settings=settings()
        )
        assert len(reads) == 1, (
            f"{rows} rows and 3600 markers must still take one policy-state read, "
            f"took {len(reads)}"
        )
        # The per-call cap is unchanged: the remainder stays recoverable.
        assert len(pending) == observation.MAX_OBSERVATIONS_PER_DIGEST * 4


def test_ac_038_malformed_markers_never_acknowledge_unrelated_rows(
    durable_inbox, tmp_path
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: only the exact marker acknowledges."""
    settle(durable_inbox, 1)
    row = _pending(now=NOW)[0]
    dedup = row["dedup_id"]
    prefix = observation.OBSERVATION_MARKER_PREFIX
    event = observation.OBSERVATION_EVENT_TYPE
    near_misses = {
        f"{prefix}{dedup}": {"sent_at": NOW.isoformat()},
        f"{prefix}{dedup}:SOME_OTHER_EVENT": {"sent_at": NOW.isoformat()},
        f"{prefix}:{event}": {"sent_at": NOW.isoformat()},
        f"{prefix}{dedup}:{event}x": {"sent_at": NOW.isoformat()},
        f"{prefix}other-{dedup}:{event}": {"sent_at": NOW.isoformat()},
    }
    _seed_state(tmp_path, near_misses)

    assert len(_pending(now=NOW)) == 1, "a near-miss marker must not acknowledge"

    # Presence of the exact key is the durable acknowledgement contract, so a
    # corrupt value can never resurrect an already delivered row.
    _seed_state(tmp_path, {f"{prefix}{dedup}:{event}": "corrupt"})
    assert _pending(now=NOW) == []


def test_ac_038_confirmed_emission_without_marker_keeps_rows_pending(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: crash after confirm stays recoverable."""
    settle(durable_inbox, 1)
    pending = _pending(now=NOW)
    fingerprint = observation._fingerprint(
        pending[: observation.MAX_OBSERVATIONS_PER_DIGEST]
    )
    # The confirmation landed, then the process died before the acknowledgement.
    notification_policy.record_emitted(
        identity=observation.OBSERVATION_IDENTITY,
        event_type=observation.OBSERVATION_EVENT_TYPE,
        fingerprint=fingerprint,
        now=NOW,
    )
    assert len(_pending(now=NOW)) == 1

    sent: list[str] = []
    repaired = observation.send_observation_digest(
        pending, settings=delivery_settings(), now=NOW, sender=sender(sent)
    )
    assert repaired.status == observation.STATUS_ALREADY_DELIVERED
    assert sent == [], "a confirmed emission must never be transmitted twice"
    assert _pending(now=NOW) == []


def test_ac_038_cross_row_acknowledgement_filters_only_its_own_row(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-038: an ack is per durable identity."""
    settle(durable_inbox, 2)
    first, second = _pending(now=NOW)

    assert observation._mark_reported([first], now=NOW) == 1

    remaining = _pending(now=NOW)
    assert [row["dedup_id"] for row in remaining] == [second["dedup_id"]]


def test_ac_036_formatting_refusal_is_a_reported_non_delivery(durable_inbox):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: a refused render sends nothing."""
    settle(durable_inbox, 1)
    pending = _pending(now=NOW)
    poisoned = dict(pending[0])
    poisoned["abstention_reason"] = "ACTIONABLE"
    sent: list[str] = []

    outcome = observation.send_observation_digest(
        [poisoned], settings=delivery_settings(), now=NOW, sender=sender(sent)
    )

    assert outcome.status == observation.STATUS_FORMAT_REFUSED
    assert outcome.delivered is False
    assert sent == [], "a refused render must not reach the transport"
    # The reason is sanitized: the forbidden phrase itself is never echoed.
    assert "ACTIONABLE" not in (outcome.reason or "")
    assert "refused" in (outcome.reason or "").lower()

    # No reservation, confirmation or acknowledgement was taken.
    state_path = notification_policy.STATE_FILE
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {}
    )
    assert (
        f"{observation.OBSERVATION_IDENTITY}:{observation.OBSERVATION_EVENT_TYPE}"
        not in state
    )
    assert not [
        key for key in state if key.startswith(observation.OBSERVATION_MARKER_PREFIX)
    ]
    assert len(_pending(now=NOW)) == 1


def test_ac_036_standalone_drain_survives_an_observation_formatting_refusal(
    durable_inbox, monkeypatch, capsys
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: a refused render cannot kill the job."""
    from app.jobs import drain_tradevisor_inbox as drain

    settle(durable_inbox, 1)
    poisoned = dict(_pending(now=NOW)[0])
    poisoned["abstention_reason"] = "ACTIONABLE"
    monkeypatch.setattr(
        drain,
        "get_settings",
        lambda: delivery_settings(
            opip_tradevisor_enabled=True, opip_tradevisor_webhook_token="T" * 43
        ),
    )
    monkeypatch.setattr(drain, "process_queued_events", lambda evaluator: {"checked": 0})
    monkeypatch.setattr(drain, "pending_count", lambda: 0)
    monkeypatch.setattr(drain, "pending_observations", lambda **kwargs: [poisoned])

    drain.main()  # must not raise

    assert observation.STATUS_FORMAT_REFUSED in capsys.readouterr().out


def _isolate_delivery_ledger(monkeypatch, tmp_path) -> None:
    """Point the real wrapper's ledger at tmp_path (destination, not transport)."""
    from app.services import telegram_delivery

    defaults = telegram_delivery.send_tracked_telegram.__kwdefaults__
    monkeypatch.setitem(defaults, "state_file", tmp_path / "delivery_state.json")
    monkeypatch.setitem(defaults, "event_file", tmp_path / "delivery_events.jsonl")


def test_ac_036_production_sender_contract_is_satisfied_end_to_end(
    durable_inbox, tmp_path, monkeypatch
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: the real wrapper accepts the call."""
    from app.services import telegram_delivery, telegram_notifier

    _isolate_delivery_ledger(monkeypatch, tmp_path)
    transmission: list[tuple] = []

    def fake_network(bot_token, chat_id, message):
        transmission.append((bot_token, chat_id, message))
        return 4242

    monkeypatch.setattr(telegram_delivery, "send_telegram_message_with_id", fake_network)
    # Any real network attempt is a test failure, not a silent pass.
    monkeypatch.setattr(
        telegram_notifier.httpx, "post", lambda *a, **k: pytest.fail("network attempted")
    )

    settle(durable_inbox, 2)
    pending = _pending(now=NOW)
    expected = observation._fingerprint(
        pending[: observation.MAX_OBSERVATIONS_PER_DIGEST]
    )

    outcome = observation.send_observation_digest(
        pending, settings=delivery_settings(), now=NOW
    )

    assert outcome.status == observation.STATUS_DELIVERED
    assert outcome.delivered is True
    assert len(transmission) == 1, "exactly one external transmission"
    bot_token, chat_id, message = transmission[0]
    assert (bot_token, chat_id) == ("bot-token", "chat-id")
    assert observation.OBSERVATION_TITLE in message
    for phrase in observation.FORBIDDEN_PHRASES:
        assert phrase.upper() not in message.upper()

    events = [
        json.loads(line)
        for line in (tmp_path / "delivery_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert events, "the production wrapper must record the delivery"
    recorded = events[-1]
    assert recorded["identity"] == observation.OBSERVATION_IDENTITY
    assert recorded["event_type"] == observation.OBSERVATION_EVENT_TYPE
    assert recorded["alert_family"] == observation.OBSERVATION_FAMILY
    assert recorded["fingerprint"] == expected
    assert recorded["status"] == "DELIVERED"
    assert recorded["message_id"] == 4242
    assert recorded["signal_id"] == expected[:16]
    assert _pending(now=NOW) == []


def test_ac_036_production_sender_negative_outcomes_never_acknowledge(
    durable_inbox, tmp_path, monkeypatch
):
    """ATDD-TRADEVISOR-V2-ADVISORY-v1/AC-036: a refused transport acks nothing."""
    from app.services import telegram_delivery

    _isolate_delivery_ledger(monkeypatch, tmp_path)
    settle(durable_inbox, 1)
    pending = _pending(now=NOW)

    def raising(bot_token, chat_id, message):
        raise RuntimeError("socket closed")

    monkeypatch.setattr(telegram_delivery, "send_telegram_message_with_id", raising)
    failed = observation.send_observation_digest(
        pending, settings=delivery_settings(), now=NOW
    )
    assert failed.status == observation.STATUS_SEND_FAILED
    assert failed.delivered is False
    assert len(_pending(now=NOW)) == len(pending)

    # A transport that accepts the call but returns no message id is not delivery.
    monkeypatch.setattr(
        telegram_delivery, "send_telegram_message_with_id", lambda *a: None
    )
    refused = observation.send_observation_digest(
        _pending(now=NOW), settings=delivery_settings(), now=NOW
    )
    assert refused.status == observation.STATUS_SEND_FAILED
    assert refused.delivered is False
    assert len(_pending(now=NOW)) == len(pending)
