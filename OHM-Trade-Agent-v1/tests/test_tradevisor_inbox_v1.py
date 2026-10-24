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


def test_non_learning_terminal_rows_are_pruned_after_retention(store):
    """Retention still ages out evidence that can never become measurable.

    A ``PROCESSED`` row is deliberately excluded here: it can still owe a
    prospective measurement, so it requires a durable consumption
    acknowledgement before retention may remove it (N-01).
    """
    store.opip_tradevisor_retention_seconds = 3600
    inbox.enqueue(signal(), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key = next(iter(registry["events"]))
    registry["events"][key]["status"] = inbox.STATUS_POISONED
    registry["events"][key]["processed_at"] = NOW.isoformat()
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

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


# ---------------------------------------------------------------------------
# P1-01: claim fencing. An expired lease must transfer settlement ownership.
# ---------------------------------------------------------------------------


def _claim(store, key: str, row: dict, *, token: str, at: datetime) -> None:
    """Persist one worker's claim in the shape the drain uses."""
    row["status"] = inbox.STATUS_PROCESSING
    row["processing_started_at"] = at.isoformat()
    row["claim_token"] = token
    registry = inbox.load_json(inbox.INBOX_FILE) or {}
    registry["events"][key] = row
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)


def test_stale_worker_cannot_settle_a_reclaimed_row(store):
    """A worker whose lease lapsed must not settle another worker's claim."""
    inbox.enqueue(signal(), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key, row = next(iter(registry["events"].items()))
    _claim(store, key, row, token="claim-a", at=NOW)

    # A's lease expires; B reclaims and claims. B is still evaluating.
    later = NOW + timedelta(
        seconds=store.opip_tradevisor_processing_timeout_seconds + 30
    )
    registry = inbox.load_json(inbox.INBOX_FILE)
    reclaimed = inbox._reclaim_stale(
        registry,
        now=later,
        timeout_seconds=store.opip_tradevisor_processing_timeout_seconds,
    )
    assert reclaimed == 1
    reclaimed_row = registry["events"][key]
    assert reclaimed_row["status"] == inbox.STATUS_RETRY
    assert not reclaimed_row.get("claim_token")
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)
    _claim(store, key, reclaimed_row, token="claim-b", at=later)

    # A returns late and tries to settle its expired claim.
    applied = inbox._settle(
        key,
        status=inbox.STATUS_PROCESSED,
        disposition="EVALUATED",
        reason="worker A stale result",
        now=later,
        decision="ENTER_NOW",
        record={"dedup_id": "a", "decision": "ENTER_NOW"},
        claim_token="claim-a",
    )

    assert applied is False
    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["status"] == inbox.STATUS_PROCESSING
    assert stored["claim_token"] == "claim-b"
    assert stored.get("decision_record") is None

    # B still owns settlement.
    assert inbox._settle(
        key,
        status=inbox.STATUS_PROCESSED,
        disposition="EVALUATED",
        reason="worker B result",
        now=later,
        decision="NO_TRADE",
        record={"dedup_id": "b", "decision": "NO_TRADE"},
        claim_token="claim-b",
    ) is True
    final = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert final["decision_record"]["dedup_id"] == "b"


def test_stale_worker_cannot_poison_a_reclaimed_row(store):
    """Poison disposition obeys the same ownership guarantee."""
    inbox.enqueue(signal(), now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key, row = next(iter(registry["events"].items()))
    _claim(store, key, row, token="claim-a", at=NOW)
    later = NOW + timedelta(
        seconds=store.opip_tradevisor_processing_timeout_seconds + 30
    )
    registry = inbox.load_json(inbox.INBOX_FILE)
    inbox._reclaim_stale(
        registry,
        now=later,
        timeout_seconds=store.opip_tradevisor_processing_timeout_seconds,
    )
    _claim(store, key, registry["events"][key], token="claim-b", at=later)

    applied = inbox._settle(
        key,
        status=inbox.STATUS_POISONED,
        disposition="POISONED",
        reason="worker A stale failure",
        now=later,
        attempts=99,
        claim_token="claim-a",
    )

    assert applied is False
    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["status"] == inbox.STATUS_PROCESSING
    assert stored["final_disposition"] is None
    assert stored.get("attempts", 0) < 99


def test_reclaimed_row_is_settled_by_its_new_owner(store):
    """Ownership transfer still lets the new owner settle (existing recovery)."""

    def dies(_signal):
        raise KeyboardInterrupt

    inbox.enqueue(signal(), now=NOW)
    with pytest.raises(KeyboardInterrupt):
        inbox.process_queued_events(evaluator=dies, now=NOW)

    later = NOW + timedelta(
        seconds=store.opip_tradevisor_processing_timeout_seconds + 5
    )
    summary = inbox.process_queued_events(evaluator=evaluate_ok, now=later)

    assert summary["reclaimed"] == 1
    assert summary["checked"] == 1
    stored = next(iter(inbox.load_json(inbox.INBOX_FILE)["events"].values()))
    assert stored["status"] == inbox.STATUS_PROCESSED


# ---------------------------------------------------------------------------
# N-01: durable learning-evidence retention (no pruning before consumption)
# ---------------------------------------------------------------------------

from app.opip.learning.job_disposition import RELEASE_CURRENT


def _consumption(**over) -> dict:
    """A durable per-record consumption disposition (the acknowledged shape)."""
    values = {
        "dedup_id": None,
        "evidence_id": "tradevisor-decision-v1",
        "disposition": inbox.CONSUMPTION_LEARNING_CONSUMED,
        "measurement_status": "MEASURED",
        "release_compatibility": RELEASE_CURRENT,
        "acknowledged_at": NOW.isoformat(),
    }
    values.update(over)
    return values


def _settled_row(*, status: str | None = None, age_days: float = 8) -> str:
    """Persist one settled row and age it past the default 7-day retention."""
    inbox.enqueue(signal(), now=NOW)
    inbox.process_queued_events(evaluator=evaluate_ok, now=NOW)
    registry = inbox.load_json(inbox.INBOX_FILE)
    key = next(iter(registry["events"]))
    row = registry["events"][key]
    if status is not None:
        row["status"] = status
    stamp = (NOW - timedelta(days=age_days)).isoformat()
    row["processed_at"] = stamp
    row["enqueued_at"] = stamp
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)
    return key


def _trigger_prune() -> None:
    """A new alert is the only thing that runs retention."""
    inbox.enqueue(signal(symbol="ETHUSD"), now=NOW)


def test_processed_evidence_is_not_pruned_without_a_consumption_ack(store):
    key = _settled_row()
    _trigger_prune()

    events = inbox.load_json(inbox.INBOX_FILE)["events"]
    assert key in events, "eligible unmeasured evidence must survive retention"
    assert events[key]["status"] == inbox.STATUS_PROCESSED


def test_one_hour_retention_cannot_erase_a_24h_hypothesis(store):
    store.opip_tradevisor_retention_seconds = 3600
    key = _settled_row(age_days=0)
    registry = inbox.load_json(inbox.INBOX_FILE)
    registry["events"][key]["processed_at"] = (NOW - timedelta(hours=2)).isoformat()
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    _trigger_prune()

    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_extended_learning_outage_does_not_erase_eligible_records(store):
    key = _settled_row(age_days=30)
    _trigger_prune()
    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_poisoned_row_is_prunable_after_retention(store):
    key = _settled_row(status=inbox.STATUS_POISONED)
    _trigger_prune()
    assert key not in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_acknowledged_row_is_prunable_after_retention(store):
    key = _settled_row()
    registry = inbox.load_json(inbox.INBOX_FILE)
    row = registry["events"][key]
    row["consumption"] = _consumption(dedup_id=row["dedup_id"])
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    _trigger_prune()

    assert key not in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_release_drift_acknowledgement_does_not_authorize_pruning(store):
    key = _settled_row()
    registry = inbox.load_json(inbox.INBOX_FILE)
    row = registry["events"][key]
    row["consumption"] = _consumption(
        dedup_id=row["dedup_id"], release_compatibility="RELEASE_DRIFT"
    )
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    _trigger_prune()

    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_nondefinitive_measurement_does_not_authorize_pruning(store):
    key = _settled_row()
    registry = inbox.load_json(inbox.INBOX_FILE)
    row = registry["events"][key]
    row["consumption"] = _consumption(
        dedup_id=row["dedup_id"], measurement_status="PARTIAL_COVERAGE"
    )
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    _trigger_prune()

    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_mismatched_acknowledgement_identity_fails_closed(store):
    key = _settled_row()
    registry = inbox.load_json(inbox.INBOX_FILE)
    row = registry["events"][key]
    row["consumption"] = _consumption(dedup_id="some-other-alert")
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    _trigger_prune()

    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]



def test_malformed_consumption_metadata_fails_closed(store):
    key = _settled_row()
    registry = inbox.load_json(inbox.INBOX_FILE)
    registry["events"][key]["consumption"] = "consumed"
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    _trigger_prune()

    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_legacy_row_without_consumption_fields_is_retained(store):
    key = _settled_row()
    registry = inbox.load_json(inbox.INBOX_FILE)
    registry["events"][key].pop("consumption", None)
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    _trigger_prune()

    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]


def test_duplicate_delivery_cannot_reset_a_consumption_ack(store):
    key = _settled_row()
    registry = inbox.load_json(inbox.INBOX_FILE)
    row = registry["events"][key]
    ack = _consumption(dedup_id=row["dedup_id"])
    row["consumption"] = ack
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    inbox.enqueue(signal(), now=NOW)  # exact replay of the same alert

    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["consumption"] == ack


def test_consumption_acknowledgement_is_idempotent(store):
    key = _settled_row(age_days=0)
    row = inbox.load_json(inbox.INBOX_FILE)["events"][key]

    first = inbox.record_consumption_ack(
        row["dedup_id"],
        evidence_id="tradevisor-decision-v1",
        measurement_status="MEASURED",
        release_compatibility=RELEASE_CURRENT,
        now=NOW,
    )
    second = inbox.record_consumption_ack(
        row["dedup_id"],
        evidence_id="tradevisor-decision-v1",
        measurement_status="MEASURED",
        release_compatibility=RELEASE_CURRENT,
        now=NOW + timedelta(hours=1),
    )

    assert first is True and second is True
    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["consumption"]["acknowledged_at"] == NOW.isoformat()


def test_consumption_bookkeeping_preserves_processing_state(store):
    key = _settled_row(age_days=0)
    before = inbox.load_json(inbox.INBOX_FILE)["events"][key]

    inbox.record_consumption_ack(
        before["dedup_id"],
        evidence_id="tradevisor-decision-v1",
        measurement_status="MEASURED",
        release_compatibility=RELEASE_CURRENT,
        now=NOW,
    )

    after = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    for field in (
        "status",
        "decision",
        "decision_record",
        "notification",
        "claim_token",
        "attempts",
        "final_disposition",
    ):
        assert after.get(field) == before.get(field), field


def test_capacity_backpressure_is_preserved_and_counted(store):
    store.opip_tradevisor_max_inbox_events = 1
    inbox.enqueue(signal(), now=NOW)

    with pytest.raises(inbox.TradevisorInboxFullError):
        inbox.enqueue(signal(symbol="ETHUSD"), now=NOW)

    status = inbox.inbox_status()
    assert status["capacity_backpressure_events"] >= 1
    assert status["depth"] == 1, "no silent oldest-first eviction"


def test_inbox_status_reports_the_unconsumed_backlog(store):
    _settled_row()
    status = inbox.inbox_status()

    assert status["unconsumed"] >= 1
    assert status["consumed"] == 0
    assert status["oldest_unconsumed_age_seconds"] > 0
    assert "dedup" not in repr(status)  # aggregate only: no record identifiers
