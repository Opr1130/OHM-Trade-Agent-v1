"""R4-B2 Slice 3A: feasibility.evidence.recorded canonical event (AC-018).

Proves that one committed F5 feasibility-evidence record becomes one durable
canonical record through the existing single ``CanonicalWriter`` and its generic
``events``/``idempotency_keys``/``watermarks`` store; that the event class is a
SEPARATE LOW-priority evidence class (not the feature bus), carries no ops
handoff, and grants no authority; that idempotency binds evaluation identity plus
exact content so a byte-equivalent retry reaches ``DUPLICATE_OK`` while materially
different evidence never silently collapses; and that a same-key/different-payload
submission fails closed.

Every fixture is a deterministic literal. No network, wall clock, random value or
production data is read.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import sys
import typing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip import fev_evidence_event as fe  # noqa: E402
from app.opip.canonical.models import EventType, WriterIntent  # noqa: E402
from app.opip.canonical.paths import EVENT_SCHEMA_VERSION  # noqa: E402
from app.opip.canonical.writer import (  # noqa: E402
    ACCEPTED_EVENT_TYPES,
    IDEMPOTENT_PAYLOAD_EVENT_TYPES,
    CanonicalWriter,
)
from app.opip.contracts.events import FEATURE_BUS_EVENT_TYPES  # noqa: E402
from app.opip.contracts.feasibility_evidence import FeasibilityEvidence  # noqa: E402
from app.opip.feasibility_evidence_record import (  # noqa: E402
    feasibility_evidence_from_payload,
)
from app.scanner.execution_validation import ExecutionValidation  # noqa: E402
from app.scanner.market_data_validation import MarketDataValidation  # noqa: E402

RECORDED = fe.FEASIBILITY_EVIDENCE_RECORDED
EVAL = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 10, 2, 11, 59, tzinfo=timezone.utc)


def _market() -> MarketDataValidation:
    return MarketDataValidation(
        status="WARN",
        qualified=True,
        warnings=["thin 24h volume"],
        rejection_reasons=[],
        candle_count=200,
        latest_candle_timestamp=1759400000,
        latest_candle_age_seconds=120.0,
        duplicate_timestamp_count=0,
        gap_count=1,
        largest_gap_seconds=120.0,
        invalid_ohlc_count=0,
        non_finite_value_count=0,
        ticker_last=100.0,
        latest_ohlc_close=100.1,
        ticker_vs_ohlc_difference_pct=0.1,
        suspicious_spike_detected=False,
    )


def _execution(*, best_bid: float = 99.9) -> ExecutionValidation:
    return ExecutionValidation(
        status="VALID",
        book_coverage_status="COMPLETE",
        warnings=[],
        best_bid=best_bid,
        best_ask=100.1,
        mid_price=100.0,
        absolute_spread=0.2,
        spread_pct=0.2,
        spread_bps=20.0,
        visible_bid_notional=5000.0,
        visible_ask_notional=5000.0,
        bid_depth_025_usd=2500.0,
        ask_depth_025_usd=2500.0,
        bid_depth_025_complete=True,
        ask_depth_025_complete=True,
        bid_depth_050_usd=5000.0,
        ask_depth_050_usd=5000.0,
        bid_depth_050_complete=True,
        ask_depth_050_complete=True,
        validation_notional_usd=500.0,
        buy_vwap=100.2,
        sell_vwap=99.8,
        buy_market_impact_pct=0.3,
        sell_market_impact_pct=0.3,
        buy_visible_coverage_pct=100.0,
        sell_visible_coverage_pct=100.0,
        buy_fully_covered=True,
        sell_fully_covered=True,
        estimated_visible_round_trip_market_drag_pct=0.4,
        estimated_visible_short_round_trip_market_drag_pct=0.5,
        recent_trade_status="FRESH",
        latest_trade_price=100.0,
        latest_trade_age_seconds=5.0,
        recent_trade_count=42,
    )


def evidence(*, best_bid: float = 99.9) -> FeasibilityEvidence:
    return FeasibilityEvidence(
        instrument_version_id="IV:SOLUSD:1",
        venue_instrument_id="SOLUSD",
        direction="LONG",
        evaluation_time=EVAL,
        source_cutoff=CUTOFF,
        source_snapshot_id="EPSNAP:abc123",
        source_evidence_refs=("OBS:1", "OBS:2"),
        market_data_validation=_market(),
        margin_validation_status=None,
        margin_eligible=None,
        margin_venue_symbol=None,
        margin_max_leverage=None,
        execution_validation=_execution(best_bid=best_bid),
        availability="AVAILABLE",
        missingness=(),
        kraken_public_symbol="SOLUSD",
        primary_pair="SOLUSD",
    )


def payload(*, best_bid: float = 99.9) -> dict[str, Any]:
    return fe.build_feasibility_evidence_recorded_payload(evidence(best_bid=best_bid))


@pytest.fixture
def canonical_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(tmp_path / "canonical"))
    db = tmp_path / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def open_writer(db: Path) -> CanonicalWriter:
    return CanonicalWriter(db)


def _connect(db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db))
    connection.row_factory = sqlite3.Row
    return connection


def event_rows(db: Path) -> list[dict[str, Any]]:
    connection = _connect(db)
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT event_id, event_time, causation_id, correlation_id, "
                "idempotency_key, history_epoch, local_sequence, payload_json "
                "FROM events WHERE event_type = ? "
                "ORDER BY history_epoch ASC, local_sequence ASC",
                (RECORDED,),
            ).fetchall()
        ]
    finally:
        connection.close()


def event_count(db: Path) -> int:
    return len(event_rows(db))


def watermark_streams(db: Path) -> set[str]:
    connection = _connect(db)
    try:
        return {
            str(row["stream"])
            for row in connection.execute("SELECT stream FROM watermarks").fetchall()
        }
    finally:
        connection.close()


def canonical_intent(record: dict[str, Any]) -> WriterIntent:
    return WriterIntent(
        schema_version=EVENT_SCHEMA_VERSION,
        priority=fe.FEASIBILITY_EVIDENCE_PRIORITY,
        idempotency_key=fe.feasibility_evidence_event_idempotency_key(record),
        event_type=RECORDED,
        payload=record,
        event_time=fe.feasibility_evidence_event_time(record),
        correlation_id=fe.feasibility_evidence_event_correlation_id(record),
        causation_id=fe.feasibility_evidence_event_causation_id(record),
        ops_handoff=None,
    )


# ---------------------------------------------------------------------------
# AC-018
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_018_event_is_separate_low_priority_accepted_class() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: the F5 evidence event is a separate LOW-priority accepted class with no ops handoff and is not feature-bus vocabulary."""
    assert RECORDED == "feasibility.evidence.recorded"
    assert RECORDED in typing.get_args(EventType)
    assert fe.FEASIBILITY_EVIDENCE_EVENT_TYPES == frozenset({RECORDED})
    assert RECORDED in ACCEPTED_EVENT_TYPES
    assert RECORDED in IDEMPOTENT_PAYLOAD_EVENT_TYPES
    assert RECORDED not in FEATURE_BUS_EVENT_TYPES
    assert fe.FEASIBILITY_EVIDENCE_PRIORITY == "LOW"
    assert CanonicalWriter._stream_for_event(RECORDED) == fe.FEASIBILITY_EVIDENCE_STREAM
    assert CanonicalWriter._requires_ops_handoff(RECORDED) is False


@pytest.mark.acceptance
def test_ac_018_payload_round_trips_exact_evidence() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: the event payload round-trips to the exact typed F5 evidence and retains both identities."""
    record = payload()
    restored = feasibility_evidence_from_payload(record["evidence"])
    assert restored == evidence()
    rebuilt = fe.validate_feasibility_evidence_recorded_payload(RECORDED, record)
    assert rebuilt == record
    assert fe.feasibility_evidence_event_fingerprint(record) == restored.evidence_fingerprint
    assert fe.feasibility_evidence_event_payload_hash(record) == record["evidence"]["payload_hash"]


@pytest.mark.acceptance
def test_ac_018_idempotency_binds_evaluation_and_exact_content() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: the idempotency key binds evaluation identity plus exact content and contains no clock."""
    same_a = fe.feasibility_evidence_event_idempotency_key(payload())
    same_b = fe.feasibility_evidence_event_idempotency_key(payload())
    assert same_a == same_b
    # A mutation outside the F5 summary changes the exact content -> a different key.
    different = fe.feasibility_evidence_event_idempotency_key(payload(best_bid=98.0))
    assert different != same_a
    assert same_a.startswith(fe.FEASIBILITY_EVIDENCE_IDEMPOTENCY_PREFIX)
    # The key names the record's own evaluation identity.
    body = payload()["evidence"]["evidence"]
    assert body["instrument_version_id"] in same_a
    assert body["evaluation_time"] in same_a


@pytest.mark.acceptance
def test_ac_018_malformed_or_naive_instant_is_rejected() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: a malformed or naive instant fails closed rather than being silently accepted."""
    record = payload()
    for bad in ("2026-10-02T12:00:00", "not-a-timestamp"):
        tampered = {
            "record_type": record["record_type"],
            "schema_version": record["schema_version"],
            "evidence": dict(record["evidence"]),
        }
        body = dict(record["evidence"]["evidence"])
        body["evaluation_time"] = bad
        tampered["evidence"]["evidence"] = body
        with pytest.raises(fe.FeasibilityEvidenceEventError):
            fe.validate_feasibility_evidence_recorded_payload(RECORDED, tampered)


@pytest.mark.acceptance
def test_ac_018_equivalent_instant_normalizes_to_same_identity() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: an equivalent non-canonical instant normalizes to the same durable bytes and idempotency key, so it is a duplicate rather than a new record."""
    record = payload()
    submitted = {
        "record_type": record["record_type"],
        "schema_version": record["schema_version"],
        "evidence": dict(record["evidence"]),
    }
    body = dict(record["evidence"]["evidence"])
    body["evaluation_time"] = "2026-10-02T12:00:00+00:00"
    submitted["evidence"]["evidence"] = body
    normalized = fe.validate_feasibility_evidence_recorded_payload(RECORDED, submitted)
    assert normalized["evidence"]["evidence"]["evaluation_time"] == "2026-10-02T12:00:00Z"
    assert fe.feasibility_evidence_event_idempotency_key(normalized) == (
        fe.feasibility_evidence_event_idempotency_key(record)
    )
    assert normalized == record


@pytest.mark.acceptance
def test_ac_018_exact_replay_is_duplicate_ok_with_one_row(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: an exact replay reaches DUPLICATE_OK and leaves exactly one canonical row."""
    writer = open_writer(canonical_store)
    try:
        assert writer.submit(canonical_intent(payload())).status == "OK"
        again = writer.submit(canonical_intent(payload()))
        assert again.status == "DUPLICATE_OK"
        assert event_count(canonical_store) == 1
        assert fe.FEASIBILITY_EVIDENCE_STREAM in watermark_streams(canonical_store)
        assert fe.FEASIBILITY_EVIDENCE_STREAM != "feature_bus.v1"
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_018_materially_different_evidence_is_not_collapsed(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: materially different durable evidence yields a distinct idempotency key and a second row, never a silent collapse."""
    writer = open_writer(canonical_store)
    try:
        assert writer.submit(canonical_intent(payload(best_bid=99.9))).status == "OK"
        second = writer.submit(canonical_intent(payload(best_bid=98.0)))
        assert second.status == "OK"
        assert event_count(canonical_store) == 2
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_018_envelope_cannot_decouple_from_record(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: the envelope identity is bound to the record, so a key from one record presented with another record's payload is rejected, and never collapses into a duplicate."""
    writer = open_writer(canonical_store)
    try:
        assert writer.submit(canonical_intent(payload(best_bid=99.9))).status == "OK"
        # Force a key/payload mismatch: the key of record A with the payload of B.
        mismatched = dataclasses.replace(
            canonical_intent(payload(best_bid=99.9)),
            payload=payload(best_bid=98.0),
        )
        conflict = writer.submit(mismatched)
        assert conflict.status == "REJECTED"
        # The content-bound key makes same-key/different-payload structurally
        # impossible for a validated intent, so the envelope mismatch is caught at
        # the boundary before it can ever reach the DUPLICATE_OK path.
        assert conflict.error_code == "INVALID_INTENT"
        assert event_count(canonical_store) == 1
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_018_writer_rejects_wrong_priority_and_ops_handoff(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: the persistence boundary rejects a non-LOW priority or any ops handoff for an F5 evidence intent, so the no-authority property is enforced at the writer, not only asserted on the contract."""
    writer = open_writer(canonical_store)
    try:
        assert CanonicalWriter._requires_ops_handoff(RECORDED) is False
        wrong_priority = dataclasses.replace(
            canonical_intent(payload()), priority="NORMAL"
        )
        ack = writer.submit(wrong_priority)
        assert ack.status == "REJECTED"
        assert ack.error_code == "INVALID_INTENT"

        with_handoff = dataclasses.replace(
            canonical_intent(payload()),
            ops_handoff={"operation": "RECORD"},
        )
        handoff_ack = writer.submit(with_handoff)
        assert handoff_ack.status == "REJECTED"
        assert handoff_ack.error_code == "INVALID_INTENT"

        assert event_count(canonical_store) == 0
        # A valid intent still commits, proving the rejections are not blanket.
        assert writer.submit(canonical_intent(payload())).status == "OK"
        assert event_count(canonical_store) == 1
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_018_validator_rejects_forged_or_tampered_payload() -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: a forged event type, an unknown key, a bad discriminator or a tampered record is rejected."""
    with pytest.raises(fe.FeasibilityEvidenceEventError):
        fe.validate_feasibility_evidence_recorded_payload("feasibility.evidence.forged", payload())

    unknown_key = payload()
    unknown_key["extra"] = 1
    with pytest.raises(fe.FeasibilityEvidenceEventError):
        fe.validate_feasibility_evidence_recorded_payload(RECORDED, unknown_key)

    bad_discriminator = payload()
    bad_discriminator["record_type"] = "something_else"
    with pytest.raises(fe.FeasibilityEvidenceEventError):
        fe.validate_feasibility_evidence_recorded_payload(RECORDED, bad_discriminator)

    tampered = payload()
    tampered["evidence"]["evidence"]["execution_validation"]["best_bid"] = 1.0
    with pytest.raises(fe.FeasibilityEvidenceEventError):
        fe.validate_feasibility_evidence_recorded_payload(RECORDED, tampered)


@pytest.mark.acceptance
def test_ac_018_restart_rehydrates_and_replays_as_duplicate(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-018: a fresh writer over the same store treats an exact replay as DUPLICATE_OK with one row."""
    first = open_writer(canonical_store)
    try:
        assert first.submit(canonical_intent(payload())).status == "OK"
    finally:
        first.close()
    reopened = open_writer(canonical_store)
    try:
        replay = reopened.submit(canonical_intent(payload()))
        assert replay.status == "DUPLICATE_OK"
        assert event_count(canonical_store) == 1
    finally:
        reopened.close()
