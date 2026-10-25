"""ATDD: durable learning-consumption acknowledgement (N-01).

Increment: ATDD-LEARNING-CONSUMPTION-DURABILITY-v1

The consumption acknowledgement is durable evidence. This increment proves the
write path is idempotent, refuses to rewrite valid evidence, repairs only corrupt
records, and never acknowledges evidence that is not terminal and definitive.
It adds no deployment unit and activates no learning compute.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import tradevisor_inbox as inbox
from app.services.tradevisor_ingress import TradevisorSignal

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-LEARNING-CONSUMPTION-DURABILITY-v1"
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
RELEASE = inbox.RELEASE_CURRENT


def settings(**over) -> SimpleNamespace:
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
    resolved = settings()
    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: resolved)
    return resolved


def signal(symbol: str = "BTCUSD") -> TradevisorSignal:
    bar_open = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return TradevisorSignal(
        dedup_id=f"{symbol}-BUY-{bar_open.isoformat()}",
        signal="BUY",
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


def settled_row() -> dict:
    """Enqueue and settle one row through the real claim path."""
    _, is_new = inbox.enqueue(signal(), now=NOW)
    assert is_new
    summary = inbox.process_queued_events(
        evaluator=lambda _signal: {"decision": "WATCH", "reason": "WATCH"},
        now=NOW,
        limit=5,
    )
    assert summary["processed"] == 1
    return inbox.load_json(inbox.INBOX_FILE)["events"][signal().dedup_id]


def ack(key: str, **over) -> bool:
    values = dict(
        evidence_id="tradevisor-decision-v1",
        measurement_status="MEASURED",
        release_compatibility=RELEASE,
        now=NOW,
    )
    values.update(over)
    return inbox.record_consumption_ack(key, **values)


# AC-001 ----------------------------------------------------------------------
def test_ac_001_conflicting_acknowledgement_never_rewrites_valid_evidence(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-001: valid evidence is immutable."""
    row = settled_row()
    key = row["dedup_id"]
    assert ack(key) is True
    first = inbox.load_json(inbox.INBOX_FILE)["events"][key]["consumption"]

    assert ack(key, measurement_status="NO_ENTRY_TOUCH") is False
    assert ack(key, evidence_id="other-evidence") is False
    assert ack(
        key, disposition=inbox.CONSUMPTION_TERMINAL_NON_MEASURABLE
    ) is False

    after = inbox.load_json(inbox.INBOX_FILE)["events"][key]["consumption"]
    assert after == first


# AC-002 ----------------------------------------------------------------------
def test_ac_002_identical_acknowledgement_is_idempotent(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-002: replay keeps the original ack."""
    row = settled_row()
    key = row["dedup_id"]
    assert ack(key) is True
    first = inbox.load_json(inbox.INBOX_FILE)["events"][key]["consumption"]
    later = NOW + timedelta(hours=6)
    assert ack(key, now=later) is True
    again = inbox.load_json(inbox.INBOX_FILE)["events"][key]["consumption"]
    assert again == first
    assert again["acknowledged_at"] == first["acknowledged_at"]


# AC-003 ----------------------------------------------------------------------
def test_ac_003_corrupt_acknowledgement_is_repairable(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-003: a corrupt record can be repaired."""
    row = settled_row()
    key = row["dedup_id"]
    registry = inbox.load_json(inbox.INBOX_FILE)
    registry["events"][key]["consumption"] = {
        "dedup_id": key,
        "evidence_id": "truncated",
        "disposition": "NOT_A_DISPOSITION",
        "measurement_status": "MAYBE",
        "release_compatibility": "RELEASE_DRIFT",
        "acknowledged_at": "not-a-timestamp",
    }
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)
    assert inbox._consumption_ack_is_valid(
        inbox.load_json(inbox.INBOX_FILE)["events"][key]
    ) is False
    assert ack(key) is True
    repaired = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert inbox._consumption_ack_is_valid(repaired) is True


# AC-004 ----------------------------------------------------------------------
def test_ac_004_unknown_or_non_terminal_rows_are_never_acknowledged(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-004: only terminal rows can be consumed."""
    assert ack("no-such-record") is False
    _, is_new = inbox.enqueue(signal("ETHUSD"), now=NOW)
    assert is_new
    queued = signal("ETHUSD").dedup_id
    row = inbox.load_json(inbox.INBOX_FILE)["events"][queued]
    assert row["status"] == inbox.STATUS_QUEUED
    assert ack(queued) is False
    assert inbox.load_json(inbox.INBOX_FILE)["events"][queued]["consumption"] is None


# AC-005 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "over",
    [
        {"evidence_id": ""},
        {"disposition": "INVENTED_DISPOSITION"},
        {"measurement_status": "PARTIAL_COVERAGE"},
        {"measurement_status": "PENDING"},
        {"release_compatibility": "RELEASE_DRIFT"},
        {"release_compatibility": ""},
    ],
)
def test_ac_005_non_definitive_or_foreign_acknowledgements_are_refused(store, over):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-005: non-definitive evidence never consumes."""
    row = settled_row()
    key = row["dedup_id"]
    assert ack(key, **over) is False
    assert inbox.load_json(inbox.INBOX_FILE)["events"][key]["consumption"] is None


# AC-006 ----------------------------------------------------------------------
def test_ac_006_retention_requires_a_valid_acknowledgement(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-006: only acknowledged evidence may prune."""
    row = settled_row()
    key = row["dedup_id"]
    registry = inbox.load_json(inbox.INBOX_FILE)
    events = registry["events"]
    old = (NOW - timedelta(days=30)).isoformat()
    events[key]["processed_at"] = old
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    # Unconsumed, old terminal evidence is retained and cannot be evicted.
    assert inbox.pending_count() == 0
    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]

    # A corrupt acknowledgement is not evidence either.
    registry = inbox.load_json(inbox.INBOX_FILE)
    registry["events"][key]["consumption"] = {"evidence_id": "", "disposition": "x"}
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)
    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]

    # A valid acknowledgement makes the row retention-eligible at enqueue time.
    assert ack(key) is True
    inbox.enqueue(signal("SOLUSD"), now=NOW)
    assert key not in inbox.load_json(inbox.INBOX_FILE)["events"]



# AC-007 ----------------------------------------------------------------------
def test_ac_007_repairing_an_invalid_record_keeps_an_audit_trail(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-007: a repair preserves provenance."""
    row = settled_row()
    key = row["dedup_id"]
    registry = inbox.load_json(inbox.INBOX_FILE)
    corrupt = {
        "dedup_id": key,
        "evidence_id": "truncated",
        "disposition": "NOT_A_DISPOSITION",
        "measurement_status": "MAYBE",
        "release_compatibility": "RELEASE_DRIFT",
        "acknowledged_at": "not-a-timestamp",
    }
    registry["events"][key]["consumption"] = corrupt
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    assert ack(key) is True
    repaired = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert repaired["consumption"]["measurement_status"] == "MEASURED"
    assert repaired["consumption_history"] == [
        {
            "record": corrupt,
            "superseded_at": NOW.isoformat(),
            "reason": "REPAIRED_INVALID_OR_FOREIGN_RELEASE",
        }
    ]

    # An identical replay must not append a second history entry.
    assert ack(key) is True
    again = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert len(again["consumption_history"]) == 1


def test_ac_007_history_is_bounded(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-007: the audit trail cannot grow without limit."""
    row = settled_row()
    key = row["dedup_id"]
    for attempt in range(inbox.CONSUMPTION_HISTORY_LIMIT * 2):
        registry = inbox.load_json(inbox.INBOX_FILE)
        registry["events"][key]["consumption"] = {
            "evidence_id": f"corrupt-{attempt}",
            "release_compatibility": "RELEASE_DRIFT",
        }
        inbox.save_json_atomic(inbox.INBOX_FILE, registry)
        assert ack(key) is True
    history = inbox.load_json(inbox.INBOX_FILE)["events"][key]["consumption_history"]
    assert len(history) == inbox.CONSUMPTION_HISTORY_LIMIT


# AC-008 ----------------------------------------------------------------------
def test_ac_008_terminal_non_measurable_cannot_be_acked_by_fabricating_a_measurement(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-008: non-measurable rows are never faked into retention."""
    row = settled_row()
    key = row["dedup_id"]
    # The disposition exists, but no definitive measurement status does: the
    # acknowledgement is refused and the evidence stays retained.
    assert ack(
        key, disposition=inbox.CONSUMPTION_TERMINAL_NON_MEASURABLE, measurement_status="PENDING"
    ) is False
    assert ack(
        key,
        disposition=inbox.CONSUMPTION_TERMINAL_NON_MEASURABLE,
        measurement_status="PARTIAL_COVERAGE",
    ) is False
    assert inbox.load_json(inbox.INBOX_FILE)["events"][key]["consumption"] is None
    assert inbox.pending_count() == 0
    assert key in inbox.load_json(inbox.INBOX_FILE)["events"]
    # BLOCKED_OWNER: making this disposition first-class needs an owner decision
    # about the measurement-status vocabulary; this increment only pins that the
    # current behaviour fails closed.
    assert inbox.CONSUMPTION_TERMINAL_NON_MEASURABLE in inbox.CONSUMPTION_DISPOSITIONS
    assert "PENDING" not in inbox.DEFINITIVE_MEASUREMENT_STATUSES


# AC-009 ----------------------------------------------------------------------
def test_ac_009_concurrent_acknowledgements_settle_on_one_valid_record(store):
    """ATDD-LEARNING-CONSUMPTION-DURABILITY-v1/AC-009: concurrent claims cannot both win."""
    import threading

    row = settled_row()
    key = row["dedup_id"]
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def claim(status: str) -> None:
        barrier.wait()
        results.append(ack(key, measurement_status=status))

    threads = [
        threading.Thread(target=claim, args=("MEASURED",)),
        threading.Thread(target=claim, args=("NO_ENTRY_TOUCH",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    stored = inbox.load_json(inbox.INBOX_FILE)["events"][key]
    assert stored["consumption"] is not None
    assert inbox._consumption_ack_is_valid(stored) is True
    # At most one claim may be accepted; a losing claim is refused, never merged.
    assert results.count(True) <= 1
    assert stored["consumption"]["measurement_status"] in {"MEASURED", "NO_ENTRY_TOUCH"}

