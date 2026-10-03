"""R4-B2 Slice 3A: read seam over committed F5 feasibility evidence (AC-019).

Proves the reader opens the canonical store read-only, returns committed
``feasibility.evidence.recorded`` records in canonical order, reconstructs the
exact typed evidence and identities, is bounded and deduped, and fails closed per
record (a malformed record is rejected without aborting the batch). No network,
wall clock, random value or production data is read.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip import fev_evidence_event as fe  # noqa: E402
from app.opip.canonical.models import WriterIntent  # noqa: E402
from app.opip.canonical.paths import EVENT_SCHEMA_VERSION  # noqa: E402
from app.opip.canonical.writer import CanonicalWriter  # noqa: E402
from app.opip.contracts.feasibility_evidence import FeasibilityEvidence  # noqa: E402
from app.opip.fev_evidence_reader import (  # noqa: E402
    CommittedFeasibilityEvidenceReader,
    FeasibilityEvidenceRecordReadError,
    committed_feasibility_evidence_from_payload,
    read_all_committed_feasibility_evidence,
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
        warnings=[],
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


def evidence(*, best_bid: float = 99.9, snapshot: str = "EPSNAP:abc123") -> FeasibilityEvidence:
    return FeasibilityEvidence(
        instrument_version_id="IV:SOLUSD:1",
        venue_instrument_id="SOLUSD",
        direction="LONG",
        evaluation_time=EVAL,
        source_cutoff=CUTOFF,
        source_snapshot_id=snapshot,
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


def payload(*, best_bid: float = 99.9, snapshot: str = "EPSNAP:abc123") -> dict[str, Any]:
    return fe.build_feasibility_evidence_recorded_payload(
        evidence(best_bid=best_bid, snapshot=snapshot)
    )


@pytest.fixture
def canonical_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(tmp_path / "canonical"))
    db = tmp_path / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def write_record(db: Path, record: dict[str, Any]) -> str:
    writer = CanonicalWriter(db)
    try:
        intent = WriterIntent(
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
        ack = writer.submit(intent)
        assert ack.status == "OK"
        return intent.idempotency_key
    finally:
        writer.close()


def event_count(db: Path) -> int:
    connection = sqlite3.connect(str(db))
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = ?", (RECORDED,)
            ).fetchone()[0]
        )
    finally:
        connection.close()


def corrupt_payload(db: Path, *, index: int = 0) -> None:
    """Overwrite one committed row's payload with a structurally invalid record."""
    connection = sqlite3.connect(str(db))
    try:
        row = connection.execute(
            "SELECT event_id FROM events WHERE event_type = ? "
            "ORDER BY history_epoch ASC, local_sequence ASC LIMIT 1 OFFSET ?",
            (RECORDED, index),
        ).fetchone()
        assert row is not None
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            (json.dumps({"record_type": "corrupt"}), str(row[0])),
        )
        connection.commit()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# AC-019
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_019_reader_is_read_only_and_reconstructs_exact_evidence(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-019: the reader opens read-only and returns the exact typed evidence with both identities."""
    write_record(canonical_store, payload())
    reader = CommittedFeasibilityEvidenceReader(db_path=canonical_store)
    try:
        assert reader.is_read_only is True
        batch = reader.read_batch()
        assert batch.rejected == 0
        assert len(batch.records) == 1
        record = batch.records[0]
        assert isinstance(record.evidence, FeasibilityEvidence)
        assert record.evidence == evidence()
        assert record.evidence_fingerprint == record.evidence.evidence_fingerprint
        assert record.payload_hash.startswith("FEVH:")
    finally:
        reader.close()


@pytest.mark.acceptance
def test_ac_019_bounded_cursor_dedupes_and_preserves_order(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-019: reads are bounded, advance by cursor, are stateless, and preserve canonical order."""
    write_record(canonical_store, payload(snapshot="EPSNAP:1", best_bid=99.9))
    write_record(canonical_store, payload(snapshot="EPSNAP:2", best_bid=98.0))
    write_record(canonical_store, payload(snapshot="EPSNAP:3", best_bid=97.0))
    reader = CommittedFeasibilityEvidenceReader(db_path=canonical_store)
    try:
        first = reader.read_batch(limit=2)
        assert len(first.records) == 2
        assert first.cursor is not None
        assert [r.evidence.source_snapshot_id for r in first.records] == ["EPSNAP:1", "EPSNAP:2"]
        # The reader is stateless: dedupe is owned by the caller's exclusive
        # cursor. Re-reading from an earlier cursor deterministically re-surfaces
        # the same committed rows again (and holds no unbounded dedupe set).
        replay = reader.read_batch(limit=2)
        assert [r.evidence.source_snapshot_id for r in replay.records] == [
            "EPSNAP:1",
            "EPSNAP:2",
        ]
        assert replay.cursor == first.cursor
        second = reader.read_batch(after=first.cursor, limit=2)
        assert [r.evidence.source_snapshot_id for r in second.records] == ["EPSNAP:3"]
        assert reader.read_batch(after=second.cursor, limit=2).records == ()
    finally:
        reader.close()
    all_records = read_all_committed_feasibility_evidence(canonical_store)
    assert [r.evidence.source_snapshot_id for r in all_records] == ["EPSNAP:1", "EPSNAP:2", "EPSNAP:3"]


@pytest.mark.acceptance
def test_ac_019_malformed_record_is_rejected_without_aborting_batch(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-019: a corrupt committed record is rejected and no valid record is lost, so one bad row cannot abort the batch."""
    write_record(canonical_store, payload(snapshot="EPSNAP:1"))
    write_record(canonical_store, payload(snapshot="EPSNAP:2", best_bid=98.0))
    corrupt_payload(canonical_store, index=0)
    reader = CommittedFeasibilityEvidenceReader(db_path=canonical_store)
    try:
        batch = reader.read_batch()
        assert batch.rejected == 1
        assert len(batch.reject_reasons) == 1
        assert [r.evidence.source_snapshot_id for r in batch.records] == ["EPSNAP:2"]
    finally:
        reader.close()


@pytest.mark.acceptance
def test_ac_019_tampered_record_is_rejected(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-019: a record whose exact content no longer matches its payload_hash is rejected."""
    record = payload()
    write_record(canonical_store, record)
    connection = sqlite3.connect(str(canonical_store))
    try:
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_type = ?",
            (
                json.dumps(
                    {
                        **record,
                        "evidence": {
                            **record["evidence"],
                            "evidence": {
                                **record["evidence"]["evidence"],
                                "execution_validation": {
                                    **record["evidence"]["evidence"]["execution_validation"],
                                    "best_bid": 1.0,
                                },
                            },
                        },
                    }
                ),
                RECORDED,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(FeasibilityEvidenceRecordReadError):
        committed_feasibility_evidence_from_payload(
            {
                **record,
                "evidence": {
                    **record["evidence"],
                    "evidence": {
                        **record["evidence"]["evidence"],
                        "execution_validation": {
                            **record["evidence"]["evidence"]["execution_validation"],
                            "best_bid": 1.0,
                        },
                    },
                },
            }
        )
    reader = CommittedFeasibilityEvidenceReader(db_path=canonical_store)
    try:
        batch = reader.read_batch()
        assert batch.records == ()
        assert batch.rejected == 1
    finally:
        reader.close()


@pytest.mark.acceptance
def test_ac_019_reader_holds_no_authority_and_never_mutates(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-019: reading mutates nothing and the reader exposes no write path."""
    write_record(canonical_store, payload())
    before = event_count(canonical_store)
    reader = CommittedFeasibilityEvidenceReader(db_path=canonical_store)
    try:
        reader.read_batch()
        reader.read_batch()
    finally:
        reader.close()
    assert event_count(canonical_store) == before
    for attribute in ("submit", "write", "commit", "mutate", "delete", "quarantine"):
        assert not hasattr(reader, attribute)


@pytest.mark.acceptance
def test_ac_019_non_json_committed_row_is_rejected_without_aborting_batch(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-019: a committed row whose payload is not decodable JSON is counted as rejected rather than raising out of the batch."""
    write_record(canonical_store, payload(snapshot="EPSNAP:1"))
    write_record(canonical_store, payload(snapshot="EPSNAP:2", best_bid=98.0))
    connection = sqlite3.connect(str(canonical_store))
    try:
        # The store normally rejects malformed payload_json via the JSON-guard
        # expression index; drop it to simulate a store/replica where that guard is
        # absent, then poison one row with non-JSON text.
        connection.execute("DROP INDEX IF EXISTS idx_events_paper_trade")
        row = connection.execute(
            "SELECT event_id FROM events WHERE event_type = ? "
            "ORDER BY history_epoch ASC, local_sequence ASC LIMIT 1",
            (RECORDED,),
        ).fetchone()
        assert row is not None
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            ("this is not json", str(row[0])),
        )
        connection.commit()
    finally:
        connection.close()
    reader = CommittedFeasibilityEvidenceReader(db_path=canonical_store)
    try:
        # Must not raise: the undecodable row is rejected, the valid row survives.
        batch = reader.read_batch()
        assert batch.rejected == 1
        assert [r.evidence.source_snapshot_id for r in batch.records] == ["EPSNAP:2"]
    finally:
        reader.close()


@pytest.mark.acceptance
def test_ac_019_empty_store_yields_empty_batch(canonical_store) -> None:
    """ATDD-R4-B2-controlled-paper-activation/AC-019: an empty store yields an empty batch with no fabrication and no error."""
    # Initialize the canonical store (schema only) without committing any record.
    CanonicalWriter(canonical_store).close()
    reader = CommittedFeasibilityEvidenceReader(db_path=canonical_store)
    try:
        batch = reader.read_batch()
        assert batch.records == ()
        assert batch.rejected == 0
        assert batch.cursor is None
    finally:
        reader.close()
