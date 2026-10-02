"""R4-B2 committed-snapshot reader: acceptance guards.

Proves the read-only seam that feeds canonically committed FeatureSnapshots into
the non-authoritative target spine: identity round-trip, point-in-time ordering,
bounded cursor, deterministic dedupe, fail-closed rejects, and a genuinely
read-only connection that never mutates or quarantines canonical state.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.opip.canonical.server import CanonicalWriterServer
from app.opip.canonical.client import InProcessWriterClient
from app.opip.contracts.enums import CoverageState, RestartState
from app.opip.contracts.events import FEATURE_SNAPSHOT_RECORDED
from app.opip.contracts.features import FeatureSnapshot
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.features.committed_snapshot_reader import (
    CommittedSnapshotReader,
    SnapshotRecordError,
    feature_snapshot_from_payload,
)
from app.opip.features.publisher import FeatureBusPublisher, SHADOW_CAPTURE_SETTINGS
from app.opip.ml.temporal import AvailabilityStamp

pytestmark = pytest.mark.acceptance


@pytest.fixture
def canonical_env(tmp_path, monkeypatch):
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(root))
    return {"root": root, "db": root / "opip_canonical_v1.sqlite3"}


@pytest.fixture
def writer_server(canonical_env):
    server = CanonicalWriterServer(
        db_path=canonical_env["db"],
        socket_path=canonical_env["root"] / "writer.sock",
    )
    yield server
    try:
        server.stop()
    except Exception:  # noqa: BLE001 - teardown only
        pass


def _snapshot(*, minute: int, value: float = 1.0, venue: str = "SOLUSD") -> FeatureSnapshot:
    cutoff = datetime(2026, 10, 2, 12, minute, tzinfo=timezone.utc)
    return FeatureSnapshot(
        instrument_version_id="IV:r4b2",
        venue_instrument_id=venue,
        feature_version="fv1",
        evaluation_cutoff=cutoff,
        evaluated_at_utc=cutoff,
        consumed_input_watermark=ConsumedInputWatermark(0, minute),
        values={"score": value},
        availability=AvailabilityStamp(
            None,
            cutoff,
            cutoff,
            "r4b2-test",
        ),
        coverage=CoverageState.COMPLETE,
        restart_state=RestartState.WARM,
        feature_dag_hash="dag-r4b2",
    )


def _publish(client, snapshots) -> None:
    publisher = FeatureBusPublisher(
        client, enabled=True, settings=SHADOW_CAPTURE_SETTINGS
    )
    for snapshot in snapshots:
        outcome = publisher.publish_snapshot(snapshot)
        assert outcome.committed, outcome.status


def test_ac_015_roundtrip_identity_and_ordering(canonical_env, writer_server):
    """ATDD-R4-B2-controlled-paper-activation/AC-015: the reader reconstructs committed FeatureSnapshots with identical snapshot_id/content_hash and returns them in canonical commit order."""
    client = InProcessWriterClient(writer_server)
    originals = [_snapshot(minute=m, value=float(m)) for m in (3, 1, 2)]
    _publish(client, originals)

    reader = CommittedSnapshotReader(db_path=canonical_env["db"])
    assert reader.is_read_only is True
    batch = reader.read_batch()
    # Canonical commit order is the order the publisher committed them in.
    assert [s.snapshot_id for s in batch.snapshots] == [
        s.snapshot_id for s in originals
    ]
    for original, read in zip(originals, batch.snapshots):
        assert read.content_hash() == original.content_hash()
        assert read.to_dict() == original.to_dict()
    reader.close()


def test_ac_015_cursor_is_commit_order_across_batches(canonical_env, writer_server):
    """ATDD-R4-B2-controlled-paper-activation/AC-015: across batches the reader yields canonical commit order, so the cursor never gaps or re-reads."""
    client = InProcessWriterClient(writer_server)
    originals = [_snapshot(minute=m, value=float(m)) for m in (3, 1, 2, 0)]
    _publish(client, originals)

    reader = CommittedSnapshotReader(db_path=canonical_env["db"])
    stream: list[str] = []
    cursor: tuple[int, int] | None = None
    while True:
        batch = reader.read_batch(after=cursor, limit=2)
        stream.extend(s.snapshot_id for s in batch.snapshots)
        if batch.cursor == cursor or not batch.cursor:
            break
        cursor = batch.cursor
    assert stream == [s.snapshot_id for s in originals]
    reader.close()


def test_ac_015_bounded_cursor_and_dedupe(canonical_env, writer_server):
    """ATDD-R4-B2-controlled-paper-activation/AC-015: the cursor bounds each batch and advances deterministically; a re-read never reclassifies a committed snapshot as new."""
    client = InProcessWriterClient(writer_server)
    originals = [_snapshot(minute=m) for m in (0, 1, 2, 3, 4)]
    _publish(client, originals)

    reader = CommittedSnapshotReader(db_path=canonical_env["db"])
    first = reader.read_batch(limit=2)
    assert len(first.snapshots) == 2
    assert first.cursor is not None
    second = reader.read_batch(after=first.cursor, limit=2)
    assert len(second.snapshots) == 2
    read_ids = [s.snapshot_id for s in first.snapshots + second.snapshots]
    assert read_ids == [s.snapshot_id for s in originals[:4]]

    # Re-reading an earlier range surfaces nothing new: dedupe is by identity.
    replayed = reader.read_batch(after=None, limit=2)
    assert replayed.snapshots == ()
    reader.close()


def test_ac_015_rejects_malformed_and_tampered_payloads():
    """ATDD-R4-B2-controlled-paper-activation/AC-015: a malformed or identity-inconsistent payload fails closed rather than being trusted or repaired."""
    good = _snapshot(minute=0).to_dict()

    tampered_id = dict(good)
    tampered_id["snapshot_id"] = "not-the-real-id"
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(tampered_id)

    tampered_hash = dict(good)
    tampered_hash["content_hash"] = "deadbeef"
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(tampered_hash)

    missing = dict(good)
    missing.pop("consumed_input_watermark")
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(missing)

    # A watermark present but malformed must fail closed, not escape as KeyError.
    malformed_watermark = dict(good)
    malformed_watermark["consumed_input_watermark"] = {}
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(malformed_watermark)
    partial_watermark = dict(good)
    partial_watermark["consumed_input_watermark"] = {"history_epoch": 0}
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(partial_watermark)
    non_int_watermark = dict(good)
    non_int_watermark["consumed_input_watermark"] = {
        "history_epoch": "x",
        "local_sequence": 1,
    }
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(non_int_watermark)

    # Unknown enum values fail closed.
    bad_coverage = dict(good)
    bad_coverage["coverage"] = "NOT_A_COVERAGE"
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(bad_coverage)
    bad_restart = dict(good)
    bad_restart["restart_state"] = "NOT_A_STATE"
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(bad_restart)
    bad_missingness = dict(good)
    bad_missingness["missingness"] = {"x": "NOT_A_MISSINGNESS"}
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(bad_missingness)

    # A malformed availability block fails closed (naive timestamp, order violation,
    # missing source version) rather than escaping a raw TemporalIntegrityError.
    naive_visible = dict(good)
    naive_visible["availability"] = {
        "source_at_utc": None,
        "ingested_at_utc": "2026-10-02T12:00:00",
        "visible_at_utc": "2026-10-02T12:00:00",
        "source_version": "v1",
    }
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(naive_visible)
    visible_before_ingested = dict(good)
    visible_before_ingested["availability"] = {
        "source_at_utc": None,
        "ingested_at_utc": "2026-10-02T12:00:00+00:00",
        "visible_at_utc": "2026-10-02T11:00:00+00:00",
        "source_version": "v1",
    }
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(visible_before_ingested)
    empty_source_version = dict(good)
    empty_source_version["availability"] = {
        "source_at_utc": None,
        "ingested_at_utc": "2026-10-02T12:00:00+00:00",
        "visible_at_utc": "2026-10-02T12:00:00+00:00",
        "source_version": "",
    }
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(empty_source_version)
    bad_availability_type = dict(good)
    bad_availability_type["availability"] = {"ingested_at_utc": 123}
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(bad_availability_type)

    # A non-finite integer field (JSON accepts Infinity) must fail closed rather than
    # escaping a raw OverflowError from int(float("inf")) and aborting the batch.
    infinite_grid = dict(good)
    infinite_grid["evaluation_grid_seconds"] = float("inf")
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(infinite_grid)
    infinite_schema = dict(good)
    infinite_schema["schema_version"] = float("inf")
    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload(infinite_schema)

    with pytest.raises(SnapshotRecordError):
        feature_snapshot_from_payload({"record_type": "NotASnapshot"})


def test_ac_015_reader_never_mutates_or_quarantines(canonical_env, writer_server):
    """ATDD-R4-B2-controlled-paper-activation/AC-015: reading is non-mutating - no quarantine file is created and the canonical store is left byte-identical."""
    client = InProcessWriterClient(writer_server)
    _publish(client, [_snapshot(minute=0), _snapshot(minute=1)])
    before = (canonical_env["db"]).read_bytes()

    reader = CommittedSnapshotReader(db_path=canonical_env["db"])
    try:
        reader.read_batch()
        reader.read_batch()
    finally:
        reader.close()

    assert (canonical_env["db"]).read_bytes() == before
    assert list(canonical_env["root"].glob("*.corrupt-*")) == []


def test_ac_015_batch_survives_a_corrupt_stored_record(canonical_env, writer_server):
    """ATDD-R4-B2-controlled-paper-activation/AC-015: a single corrupt stored record is counted as rejected, not allowed to abort the batch."""
    import json
    import sqlite3

    client = InProcessWriterClient(writer_server)
    _publish(client, [_snapshot(minute=0)])

    # Tamper the stored availability so it is malformed on read.
    conn = sqlite3.connect(canonical_env["db"])
    try:
        row = conn.execute(
            "SELECT event_id, payload_json FROM events WHERE event_type = ?",
            (FEATURE_SNAPSHOT_RECORDED,),
        ).fetchone()
        payload = json.loads(row[1])
        payload["availability"] = {
            "ingested_at_utc": "2026-10-02T12:00:00",  # naive -> TemporalIntegrityError
            "visible_at_utc": "2026-10-02T12:00:00",
            "source_version": "v1",
        }
        conn.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            (json.dumps(payload), row[0]),
        )
        conn.commit()
    finally:
        conn.close()

    reader = CommittedSnapshotReader(db_path=canonical_env["db"])
    try:
        batch = reader.read_batch()
        assert batch.snapshots == ()
        assert batch.rejected >= 1
        assert batch.reject_reasons
    finally:
        reader.close()
