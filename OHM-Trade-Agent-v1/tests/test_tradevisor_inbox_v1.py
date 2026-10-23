"""ATDD: Tradevisor durable inbox (durability, replay, recovery, quarantine).

Covers ATDD-36, ATDD-37, ATDD-39 (capacity), ATDD-40 (duplicate worker),
ATDD-45 (poison payload). Synthetic only; no network call is made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import tradevisor_inbox as inbox
from app.services.tradevisor_ingress import TradevisorSignal

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def make_settings(**over) -> SimpleNamespace:
    values = dict(
        opip_tradevisor_retention_seconds=7 * 24 * 3600,
        opip_tradevisor_max_inbox_events=50,
        opip_tradevisor_max_attempts=3,
        opip_tradevisor_processing_timeout_seconds=120,
        opip_tradevisor_processing_batch_limit=2,
        opip_tradevisor_enabled=True,
    )
    values.update(over)
    return SimpleNamespace(**values)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    settings = make_settings()
    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: settings)
    return settings


def signal(symbol: str = "BTCUSD", bar_hour: int = 11, direction: str = "BUY") -> TradevisorSignal:
    bar_open = NOW.replace(hour=bar_hour, minute=0, second=0, microsecond=0)
    return TradevisorSignal(
        dedup_id=f"{symbol}-{direction}-{bar_open.isoformat()}",
        signal=direction,
        instrument=f"{symbol[:-3]}/USD",
        market=symbol,
        source_exchange="COINBASE",
        source_symbol=symbol,
        timeframe_minutes=60,
        bar_opened_at=bar_open,
        bar_closed_at=bar_open + timedelta(hours=1),
        triggered_at=NOW,
        received_at=NOW,
        signal_price=100.0,
        alert_config_version="tradevisor-1h-buy",
    )


def evaluate_ok(_signal):
    return {
        "decision": "ENTER_NOW",
        "reason": "qualified",
        "record": {"schema_version": "tradevisor-decision-v1", "decision": "ENTER_NOW"},
    }


# ---------------------------------------------------------------------------
# Durability and idempotency
# ---------------------------------------------------------------------------


def test_receipt_is_durable_before_acknowledgement(store):
    row, is_new = inbox.enqueue(signal(), now=NOW)
    assert is_new is True
    assert row["status"] == inbox.STATUS_QUEUED
    stored = inbox.load_json(inbox.INBOX_FILE)["events"]
    assert len(stored) == 1
    assert inbox.pending_count() == 1


def test_repeat_receipt_returns_the_same_row(store):
    first, is_new = inbox.enqueue(signal(), now=NOW)
    second, again = inbox.enqueue(signal(), now=NOW + timedelta(seconds=30))
    assert is_new is True and again is False
    assert first["dedup_id"] == second["dedup_id"]
    assert len(inbox.load_json(inbox.INBOX_FILE)["events"]) == 1


def test_capacity_is_bounded_and_raises_backpressure(store):
    store.opip_tradevisor_max_inbox_events = 100
    for hour in range(20):
        inbox.enqueue(signal(symbol=f"AAA{hour}USD", bar_hour=1), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    while len(registry["events"]) < 100:
        registry["events"][f"filler-{len(registry['events'])}"] = {
            "status": "PROCESSED",
            "processed_at": NOW.isoformat(),
        }
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)
    with pytest.raises(inbox.TradevisorInboxFullError):
        inbox.enqueue(signal(symbol="ZZZUSD"), now=NOW)


def test_terminal_rows_are_pruned_after_retention(store):
    store.opip_tradevisor_retention_seconds = 3600
    inbox.enqueue(signal(), now=NOW)
    inbox.process_queued_events(evaluator=evaluate_ok, now=NOW)
    old = NOW + timedelta(hours=3)
    inbox.enqueue(signal(symbol="ETHUSD"), now=old)
    keys = list(inbox.load_json(inbox.INBOX_FILE)["events"])
    assert not any("BTCUSD" in key for key in keys)


def test_inbox_status_declares_no_authority(store):
    inbox.enqueue(signal(), now=NOW)
    status = inbox.inbox_status()
    assert status["depth"] == 1
    assert status["authority"] == {
        "trading_authority_changed": False,
        "admission_authority": False,
        "execution_authority": False,
    }
    assert "token" not in repr(status).lower()



# ---------------------------------------------------------------------------
# Processing, crash recovery, duplicates, quarantine
# ---------------------------------------------------------------------------


def test_durable_receipt_survives_a_crash_before_handoff(store):
    # ATDD-37: durably received, never processed, then recovered later.
    inbox.enqueue(signal(), now=NOW)
    summary = inbox.process_queued_events(evaluator=evaluate_ok, now=NOW + timedelta(minutes=1))
    assert summary["processed"] == 1
    stored = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"].values()))
    assert stored["status"] == inbox.STATUS_PROCESSED
    assert stored["decision"] == "ENTER_NOW"
    assert stored["decision_record"]["decision"] == "ENTER_NOW"


def test_crash_during_processing_is_reclaimed_not_lost(store):
    # ATDD-36: a worker claims the row and dies before settling it.
    inbox.enqueue(signal(), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key = next(iter(registry["events"]))
    registry["events"][key]["status"] = inbox.STATUS_PROCESSING
    registry["events"][key]["processing_started_at"] = NOW.isoformat()
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    later = NOW + timedelta(minutes=10)
    summary = inbox.process_queued_events(evaluator=evaluate_ok, now=later)
    assert summary["reclaimed"] == 1
    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["status"] == inbox.STATUS_PROCESSED
    assert stored["decision"] == "ENTER_NOW"


def test_settle_is_a_noop_when_a_duplicate_worker_lost_the_lease(store):
    # ATDD-40: the second worker must not overwrite a settled decision.
    inbox.enqueue(signal(), now=NOW)
    inbox.process_queued_events(evaluator=evaluate_ok, now=NOW)
    key = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"]))
    applied = inbox._settle(
        key,
        status=inbox.STATUS_PROCESSED,
        disposition="DUPLICATE_WORKER",
        reason="second worker",
        now=NOW,
        decision="NO_TRADE",
    )
    assert applied is False
    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["decision"] == "ENTER_NOW"


def test_partial_batch_leaves_the_rest_queued(store):
    inbox.enqueue(signal(symbol="BTCUSD", bar_hour=9), now=NOW)
    inbox.enqueue(signal(symbol="ETHUSD", bar_hour=9), now=NOW)
    inbox.enqueue(signal(symbol="SOLUSD", bar_hour=9), now=NOW)
    summary = inbox.process_queued_events(evaluator=evaluate_ok, now=NOW, limit=2)
    assert summary["checked"] == 2
    assert inbox.pending_count() == 1


def test_repeated_failure_becomes_an_explicit_poisoned_disposition(store):
    # ATDD-45: a poison payload reaches a terminal, visible state.
    def explode(_signal):
        raise RuntimeError("boom")

    inbox.enqueue(signal(), now=NOW)
    for attempt in range(3):
        inbox.process_queued_events(
            evaluator=explode, now=NOW + timedelta(minutes=attempt)
        )
    stored = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"].values()))
    assert stored["status"] == inbox.STATUS_POISONED
    assert stored["final_disposition"] == "POISONED"
    assert stored["attempts"] == 3
    # The persisted reason carries the exception type only, never its message.
    assert "boom" not in str(stored["failure_reason"])


def test_malformed_row_is_quarantined_not_evaluated(store):
    inbox.enqueue(signal(), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key = next(iter(registry["events"]))
    registry["events"][key]["signal"]["signal_price"] = "not-a-price"
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)
    summary = inbox.process_queued_events(evaluator=evaluate_ok, now=NOW)
    assert summary["poisoned"] == 1
    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["final_disposition"] == "MALFORMED_RECORD"


def test_missing_evaluator_never_implies_approval(store):
    inbox.enqueue(signal(), now=NOW)
    summary = inbox.process_queued_events(evaluator=None, now=NOW)
    assert summary["qualified"] == 0
    stored = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"].values()))
    assert stored["status"] == inbox.STATUS_PROCESSED
    assert stored["final_disposition"] == "EVALUATION_NOT_CONFIGURED"
    assert stored["decision"] == "NO_TRADE"
