from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services import pending_setup_notifier as pending_notifier
from app.services import telegram_delivery
from app.services.pending_setup_monitor import PendingSetupMonitorResult
from app.services.pending_setup_registry import PendingSetup


def test_canonical_delivery_ledger_tracks_message_id_retry_and_latency(monkeypatch, tmp_path):
    event_file = tmp_path / "telegram_delivery_events.jsonl"
    state_file = tmp_path / "telegram_delivery_state.json"
    results = iter([None, None, 4321])
    monkeypatch.setattr(
        telegram_delivery,
        "send_telegram_message_with_id",
        lambda *args, **kwargs: next(results),
    )

    kwargs = dict(
        bot_token="super-secret-token",
        chat_id="123",
        message="hello",
        identity="EARLY_MOVER:METUSD",
        alert_family="EARLY_MOVER",
        event_type="READY",
        fingerprint="READY:BREAKOUT",
        symbol="METUSD",
        journey_id="J-123",
        signal_id="S-123",
        trade_id="T-123",
        state_file=state_file,
        event_file=event_file,
    )
    first = telegram_delivery.send_tracked_telegram(**kwargs)
    second = telegram_delivery.send_tracked_telegram(**kwargs)
    third = telegram_delivery.send_tracked_telegram(**kwargs)

    assert first.status == "SEND_FAILED"
    assert first.attempt == 1
    assert first.retry_count == 0
    assert second.attempt == 2
    assert second.retry_count == 1
    assert third.delivered is True
    assert third.message_id == 4321
    assert third.attempt == 3
    assert third.retry_count == 2

    rows = [json.loads(line) for line in event_file.read_text().splitlines()]
    assert [row["status"] for row in rows] == [
        "SEND_FAILED",
        "SEND_FAILED",
        "DELIVERED",
    ]
    assert rows[-1]["message_id"] == 4321
    assert rows[-1]["journey_id"] == "J-123"
    assert rows[-1]["signal_id"] == "S-123"
    assert rows[-1]["trade_id"] == "T-123"
    assert rows[-1]["latency_ms"] >= 0
    assert "super-secret-token" not in event_file.read_text()

    state = json.loads(state_file.read_text())
    assert state["identities"]["EARLY_MOVER:METUSD"]["message_id"] == 4321


def test_delivery_summary_distinguishes_suppressed_not_eligible_and_failed(monkeypatch, tmp_path):
    event_file = tmp_path / "events.jsonl"
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(
        telegram_delivery,
        "send_telegram_message_with_id",
        lambda *args, **kwargs: None,
    )

    telegram_delivery.record_telegram_suppression(
        identity="A",
        alert_family="EARLY_MOVER",
        event_type="READY",
        fingerprint="A1",
        reason="SAME_STATE_COOLDOWN",
        symbol="AAAUSD",
        state_file=state_file,
        event_file=event_file,
    )
    telegram_delivery.record_telegram_not_eligible(
        identity="B",
        alert_family="PENDING_SETUP",
        event_type="WAITING",
        fingerprint="B1",
        reason="NON_MATERIAL_PENDING_STATE",
        symbol="BBBUSD",
        state_file=state_file,
        event_file=event_file,
    )
    telegram_delivery.send_tracked_telegram(
        bot_token="x",
        chat_id="y",
        message="z",
        identity="C",
        alert_family="ACTIVE_TRADE",
        event_type="WARNING",
        fingerprint="C1",
        symbol="CCCUSD",
        state_file=state_file,
        event_file=event_file,
    )

    summary = telegram_delivery.build_delivery_summary(path=event_file)
    assert summary["events"] == 3
    assert summary["suppressed"] == 1
    assert summary["failed"] == 1
    assert summary["by_status"]["NOT_ELIGIBLE"] == 1
    assert summary["by_status"]["SUPPRESSED"] == 1
    assert summary["by_status"]["SEND_FAILED"] == 1


def _pending_setup() -> PendingSetup:
    return PendingSetup(
        symbol="VVVUSD",
        entry_low=10.0,
        entry_high=10.5,
        chase_limit=10.8,
        stop_price=9.0,
        target_1=11.5,
        target_2=12.5,
        risk_level="medium",
        confidence=75,
        trade_id="T-1",
    )


def test_failed_terminal_pending_alert_persists_truth_and_retries_out_of_band(monkeypatch, tmp_path):
    setup = _pending_setup()
    result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="INVALIDATED",
        current_price=8.9,
        reason="stop breached",
    )
    state_file = tmp_path / "pending_alert_state.json"
    retry_file = tmp_path / "pending_terminal_outbox.json"
    monkeypatch.setattr(pending_notifier, "STATE_FILE", state_file)
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)

    lifecycle = {"status": "waiting"}
    terminalized = []

    def terminalize(trade_id, status):
        terminalized.append((trade_id, status))
        lifecycle["status"] = status
        return True

    monkeypatch.setattr(pending_notifier, "terminalize_pending_setup", terminalize)
    monkeypatch.setattr(
        pending_notifier,
        "get_pending_setup_record",
        lambda trade_id: {"trade_id": trade_id, "status": lifecycle["status"]},
    )
    monkeypatch.setattr(
        pending_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=False, message_id=None),
    )

    assert pending_notifier.send_pending_setup_update(
        setup,
        result,
        "token",
        "chat",
    ) is False

    # Market truth advances even though Telegram failed.
    assert terminalized == [("T-1", "invalidated")]
    assert lifecycle["status"] == "invalidated"
    queued = json.loads(retry_file.read_text())
    assert queued["T-1"]["result"]["status"] == "INVALIDATED"

    monkeypatch.setattr(
        pending_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=99),
    )
    emitted = []
    monkeypatch.setattr(
        pending_notifier,
        "record_emitted",
        lambda **kwargs: emitted.append(kwargs),
    )

    sent, failed = pending_notifier.retry_terminal_pending_notifications(
        bot_token="token",
        chat_id="chat",
    )
    assert (sent, failed) == (1, 0)
    assert json.loads(retry_file.read_text()) == {}
    saved = json.loads(state_file.read_text())
    assert saved["T-1"]["status"] == "INVALIDATED"
    assert saved["T-1"]["message_id"] == 99
    assert emitted


def test_outbox_retry_reapplies_terminal_truth_after_crash_window(monkeypatch, tmp_path):
    setup = _pending_setup()
    result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="INVALIDATED",
        current_price=8.9,
        reason="stop breached",
    )
    retry_file = tmp_path / "pending_terminal_outbox.json"
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)

    # Simulate a crash after the durable outbox write but before terminalization.
    pending_notifier._queue_terminal_notification(setup, result)
    lifecycle = {"status": "waiting"}
    terminalized = []

    def terminalize(trade_id, status):
        terminalized.append((trade_id, status))
        lifecycle["status"] = status
        return True

    monkeypatch.setattr(pending_notifier, "terminalize_pending_setup", terminalize)
    monkeypatch.setattr(
        pending_notifier,
        "get_pending_setup_record",
        lambda trade_id: {"trade_id": trade_id, "status": lifecycle["status"]},
    )
    monkeypatch.setattr(
        pending_notifier,
        "accepted_delivery_message_id",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        pending_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=501),
    )
    monkeypatch.setattr(pending_notifier, "record_emitted", lambda **kwargs: None)

    sent, failed = pending_notifier.retry_terminal_pending_notifications(
        bot_token="token",
        chat_id="chat",
    )

    assert (sent, failed) == (1, 0)
    assert terminalized == [("T-1", "invalidated")]
    assert lifecycle["status"] == "invalidated"
    assert json.loads(retry_file.read_text()) == {}


def test_terminal_outbox_claim_prevents_concurrent_duplicate_workers(monkeypatch, tmp_path):
    setup = _pending_setup()
    result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="INVALIDATED",
        current_price=8.9,
        reason="stop breached",
    )
    retry_file = tmp_path / "pending_terminal_outbox.json"
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)
    pending_notifier._queue_terminal_notification(setup, result)

    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)
    first = pending_notifier._claim_terminal_retry("T-1", now=now)
    second = pending_notifier._claim_terminal_retry("T-1", now=now + timedelta(seconds=1))

    assert first is not None
    assert second is None

    # Lease expiry makes the claim recoverable after worker death.
    recovered = pending_notifier._claim_terminal_retry(
        "T-1",
        now=now + timedelta(seconds=pending_notifier.TERMINAL_RETRY_LEASE_SECONDS + 1),
    )
    assert recovered is not None
    assert recovered[0] != first[0]


def test_requeue_preserves_active_terminal_outbox_lease(monkeypatch, tmp_path):
    setup = _pending_setup()
    result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="INVALIDATED",
        current_price=8.9,
        reason="stop breached",
    )
    retry_file = tmp_path / "pending_terminal_outbox.json"
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)
    pending_notifier._queue_terminal_notification(setup, result)
    claim = pending_notifier._claim_terminal_retry("T-1")
    assert claim is not None
    token, _ = claim

    pending_notifier._queue_terminal_notification(setup, result)
    row = json.loads(retry_file.read_text())["T-1"]
    assert row["lease_token"] == token


def test_pending_entry_alert_is_quarantined_while_terminal_outbox_exists(monkeypatch, tmp_path):
    setup = _pending_setup()
    terminal_result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="INVALIDATED",
        current_price=8.9,
        reason="stop breached",
    )
    retry_file = tmp_path / "pending_terminal_outbox.json"
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)
    pending_notifier._queue_terminal_notification(setup, terminal_result)

    entry_result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="ENTRY_ZONE_REACHED",
        current_price=10.2,
        reason="price returned",
    )
    monkeypatch.setattr(
        pending_notifier,
        "send_tracked_telegram",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("entry-ready alert must be quarantined")
        ),
    )

    assert pending_notifier.send_pending_setup_update(
        setup,
        entry_result,
        "token",
        "chat",
    ) is False


def test_malformed_terminal_outbox_row_remains_quarantined_and_observable(monkeypatch, tmp_path):
    setup = _pending_setup()
    retry_file = tmp_path / "pending_terminal_outbox.json"
    retry_file.write_text(json.dumps({"T-1": None}))
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)

    assert pending_notifier.terminal_notification_pending("T-1") is True

    sent, failed = pending_notifier.retry_terminal_pending_notifications(
        bot_token="token",
        chat_id="chat",
    )
    assert (sent, failed) == (0, 1)
    assert json.loads(retry_file.read_text()) == {"T-1": None}

    entry_result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="ENTRY_ZONE_REACHED",
        current_price=10.2,
        reason="price returned",
    )
    monkeypatch.setattr(
        pending_notifier,
        "send_tracked_telegram",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("malformed unresolved terminal row must quarantine entry alert")
        ),
    )
    assert pending_notifier.send_pending_setup_update(
        setup,
        entry_result,
        "token",
        "chat",
    ) is False


def test_terminal_retry_does_not_duplicate_already_recorded_delivery(monkeypatch, tmp_path):
    setup = _pending_setup()
    result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="INVALIDATED",
        current_price=8.9,
        reason="stop breached",
    )
    retry_file = tmp_path / "pending_terminal_outbox.json"
    state_file = tmp_path / "pending_alert_state.json"
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)
    monkeypatch.setattr(pending_notifier, "STATE_FILE", state_file)
    pending_notifier._queue_terminal_notification(setup, result)
    monkeypatch.setattr(
        pending_notifier,
        "get_pending_setup_record",
        lambda trade_id: {"trade_id": trade_id, "status": "invalidated"},
    )
    monkeypatch.setattr(
        pending_notifier,
        "accepted_delivery_message_id",
        lambda **kwargs: 777,
    )
    monkeypatch.setattr(
        pending_notifier,
        "send_tracked_telegram",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not duplicate accepted push")),
    )
    monkeypatch.setattr(pending_notifier, "record_emitted", lambda **kwargs: None)

    sent, failed = pending_notifier.retry_terminal_pending_notifications(
        bot_token="token",
        chat_id="chat",
    )

    assert (sent, failed) == (1, 0)
    assert json.loads(retry_file.read_text()) == {}
    saved = json.loads(state_file.read_text())
    assert saved["T-1"]["message_id"] == 777


def test_terminal_alert_outbox_retires_when_exchange_fill_supersedes_market_alert(monkeypatch, tmp_path):
    setup = _pending_setup()
    result = PendingSetupMonitorResult(
        symbol=setup.symbol,
        status="INVALIDATED",
        current_price=8.9,
        reason="stop breached",
    )
    retry_file = tmp_path / "pending_terminal_outbox.json"
    monkeypatch.setattr(pending_notifier, "RETRY_FILE", retry_file)
    monkeypatch.setattr(pending_notifier, "terminalize_pending_setup", lambda *args: False)
    monkeypatch.setattr(
        pending_notifier,
        "get_pending_setup_record",
        lambda trade_id: {"trade_id": trade_id, "status": "entered"},
    )
    monkeypatch.setattr(
        pending_notifier,
        "send_tracked_telegram",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("stale alert must not send")),
    )

    assert pending_notifier.send_pending_setup_update(
        setup,
        result,
        "token",
        "chat",
    ) is False
    assert json.loads(retry_file.read_text()) == {}


def test_telegram_alert_state_writers_use_atomic_registry_io():
    sources = [
        Path(pending_notifier.__file__).read_text(),
    ]
    from app.services import trade_monitor_notifier, emergency_alert_notifier

    sources.extend(
        [
            Path(trade_monitor_notifier.__file__).read_text(),
            Path(emergency_alert_notifier.__file__).read_text(),
        ]
    )
    for source in sources:
        assert "save_json_atomic(" in source
        assert "STATE_FILE.write_text" not in source
