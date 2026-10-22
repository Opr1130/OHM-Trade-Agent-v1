import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.learning_derived_state import (
    DerivedStateError,
    IncompleteArchiveError,
    InsufficientSpaceError,
    LineageIdentity,
    StateLineageError,
    archive_derived_state,
    bind_state_connection,
    inventory,
    read_lineage,
    verify_archive,
    write_lineage,
)

LEGACY = LineageIdentity(
    "LINEAGE-A-LEGACY",
    "8b3cc2712432ca21007be4db0667301d48b89d97",
    "gen-b1f2c171c5c64601",
)
GENESIS = LineageIdentity(
    "LINEAGE-B-V2-GENESIS",
    "a26d664972075b2a220a466215cf71e389decb86",
    "gen-bd1613ddd16a449b",
)


def _db(path: Path, *, offset: int, pending: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
    connection.execute(
        "INSERT INTO metadata VALUES('indexed_offset', ?)", (str(offset),)
    )
    connection.execute(
        "CREATE TABLE paper_event_pending(event_key TEXT PRIMARY KEY)"
    )
    if pending:
        connection.execute(
            "INSERT INTO paper_event_pending VALUES('pending-1')"
        )
    connection.commit()
    connection.close()


def _data_root(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    (data / "p1_evidence_ledger.jsonl").write_bytes(b"")
    (data / "opip/qualification").mkdir(parents=True)
    (
        data / "opip/qualification/screening_evaluations.jsonl"
    ).write_bytes(b"new-v2-screening\n")
    (data / "paper_trading").mkdir()
    (data / "paper_trading/events.jsonl").write_bytes(b"")
    _db(
        data / ".p1_evidence_ledger.jsonl.dedup.sqlite3",
        offset=133851714,
        pending=True,
    )
    _db(
        data / ".phase3c_forward_outcomes.jsonl.state.sqlite3",
        offset=218698467,
    )
    (data / "phase3c_forward_outcomes.jsonl").write_bytes(b"legacy-phase3c\n")
    (data / "opip/opportunity_accountability_summary.json").write_bytes(
        b'{"legacy": true}\n'
    )
    (data / "manifest.env").write_text(
        "production_deployed_sha=" + GENESIS.source_release_sha + "\n",
        encoding="utf-8",
    )
    return data, tmp_path / "archive"


@pytest.mark.acceptance
def test_legacy_offsets_are_archived_before_v2_inputs_are_used(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: lineage isolation."""
    data, archive = _data_root(tmp_path)
    before = inventory(data)
    offsets = {
        entry["path"]: entry["sqlite_metadata"].get("indexed_offset")
        for entry in before["artifacts"]
        if entry["kind"] == "sqlite"
    }
    assert offsets[".p1_evidence_ledger.jsonl.dedup.sqlite3"] == "133851714"

    result = archive_derived_state(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="LINEAGE_TRANSITION",
        retire=True,
    )

    assert result["status"] == "VERIFIED"
    assert (data / "p1_evidence_ledger.jsonl").exists()
    assert (data / "opip/qualification/screening_evaluations.jsonl").exists()
    assert not (data / ".p1_evidence_ledger.jsonl.dedup.sqlite3").exists()
    assert not (data / "phase3c_forward_outcomes.jsonl").exists()
    assert (archive / "retirement.json").is_file()
    archived_db = archive / "payload/.p1_evidence_ledger.jsonl.dedup.sqlite3"
    connection = sqlite3.connect(f"file:{archived_db}?mode=ro", uri=True)
    try:
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='indexed_offset'"
        ).fetchone()[0] == "133851714"
        assert connection.execute(
            "SELECT count(*) FROM paper_event_pending"
        ).fetchone()[0] == 1
    finally:
        connection.close()
    assert verify_archive(archive)["failures"] == []


@pytest.mark.acceptance
def test_new_consumer_state_is_empty_and_bound_to_v2(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: independent V2 checkpoints."""
    data, archive = _data_root(tmp_path)
    archive_derived_state(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="LINEAGE_TRANSITION",
        retire=True,
    )
    connection = sqlite3.connect(data / ".p1_evidence_ledger.jsonl.dedup.sqlite3")
    try:
        bind_state_connection(connection, data, "p1")
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='lineage_id'"
        ).fetchone()[0] == GENESIS.lineage_id
    finally:
        connection.close()
    assert read_lineage(data) == GENESIS

@pytest.mark.acceptance
def test_unbound_legacy_sidecar_fails_closed(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: no filename-based adoption."""
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    db = data / ".legacy.state.sqlite3"
    _db(db, offset=42)
    connection = sqlite3.connect(db)
    try:
        with pytest.raises(StateLineageError, match="STATE_LINEAGE_UNBOUND"):
            bind_state_connection(connection, data, "legacy")
    finally:
        connection.close()


@pytest.mark.acceptance
def test_empty_new_sidecar_is_bound_and_mismatch_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: explicit lineage identity."""
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    connection = sqlite3.connect(data / "empty.sqlite3")
    try:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        bind_state_connection(connection, data, "new")
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='lineage_id'"
        ).fetchone()[0] == GENESIS.lineage_id
        connection.execute(
            "UPDATE metadata SET value='LINEAGE-OTHER' WHERE key='lineage_id'"
        )
        with pytest.raises(StateLineageError, match="STATE_LINEAGE_MISMATCH"):
            bind_state_connection(connection, data, "new")
    finally:
        connection.close()


@pytest.mark.acceptance
def test_interrupted_archive_is_not_verified_and_preserves_source(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: crash before durability."""
    data, archive = _data_root(tmp_path)

    def interrupted(source: Path, destination: Path) -> None:
        destination.write_bytes(source.read_bytes())
        raise OSError(5, "injected archive interruption")

    with pytest.raises(OSError):
        archive_derived_state(
            data_root=data,
            archive_root=archive,
            lineage=LEGACY,
            input_lineage=GENESIS.lineage_id,
            input_source_release_sha=GENESIS.source_release_sha,
            input_manifest=data / "manifest.env",
            active_lineage=GENESIS.lineage_id,
            reason="LINEAGE_TRANSITION",
            retire=True,
            copy_hook=interrupted,
        )
    assert not archive.exists()
    assert (data / ".p1_evidence_ledger.jsonl.dedup.sqlite3").exists()
    assert (data / "phase3c_forward_outcomes.jsonl").exists()


@pytest.mark.acceptance
def test_insufficient_disk_refuses_before_copy(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: storage headroom."""
    data, archive = _data_root(tmp_path)
    with pytest.raises(InsufficientSpaceError):
        archive_derived_state(
            data_root=data,
            archive_root=archive,
            lineage=LEGACY,
            input_lineage=GENESIS.lineage_id,
            input_source_release_sha=GENESIS.source_release_sha,
            input_manifest=data / "manifest.env",
            active_lineage=GENESIS.lineage_id,
            reason="LINEAGE_TRANSITION",
            retire=True,
            disk_usage=lambda _: SimpleNamespace(free=1),
        )
    assert not archive.exists()
    assert (data / ".p1_evidence_ledger.jsonl.dedup.sqlite3").exists()


@pytest.mark.acceptance
def test_partial_archive_destination_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: incomplete archive is not VERIFIED."""
    data, archive = _data_root(tmp_path)
    archive.mkdir()
    (archive / "archive_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(IncompleteArchiveError):
        archive_derived_state(
            data_root=data,
            archive_root=archive,
            lineage=LEGACY,
            input_lineage=GENESIS.lineage_id,
            input_source_release_sha=GENESIS.source_release_sha,
            input_manifest=data / "manifest.env",
            active_lineage=GENESIS.lineage_id,
            reason="LINEAGE_TRANSITION",
            retire=True,
        )
    assert (data / ".p1_evidence_ledger.jsonl.dedup.sqlite3").exists()


@pytest.mark.acceptance
def test_corrupted_archive_payload_is_not_verified(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: checksum mismatch is refused."""
    data, archive = _data_root(tmp_path)
    archive_derived_state(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="LINEAGE_TRANSITION",
        retire=False,
    )
    (archive / "payload/phase3c_forward_outcomes.jsonl").write_bytes(b"tampered\n")
    assert "payload_content_verified" in verify_archive(archive)["failures"]


@pytest.mark.acceptance
def test_equal_size_different_content_is_recorded_with_hash(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: identity is hash-bound."""
    data, archive = _data_root(tmp_path)
    entry = next(
        item
        for item in inventory(data)["artifacts"]
        if item["path"] == "opip/qualification/screening_evaluations.jsonl"
    )
    source = data / "opip/qualification/screening_evaluations.jsonl"
    source.write_bytes(b"x" * len(b"new-v2-screening\n"))
    changed_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    archive_derived_state(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="LINEAGE_TRANSITION",
        retire=False,
    )
    manifest = (archive / "archive_manifest.json").read_text(encoding="utf-8")
    assert entry["sha256"] != changed_sha
    assert changed_sha in manifest

@pytest.mark.acceptance
def test_phase3c_open_refuses_legacy_offset_under_v2_marker(tmp_path, monkeypatch):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: consumer integration."""
    from app.jobs.build_phase3c_forward_outcomes import _open_bounded_state

    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    db = data / ".phase3c_forward_outcomes.jsonl.state.sqlite3"
    _db(db, offset=133851714)
    monkeypatch.setenv("OPIP_LEARNING_DATA_ROOT", str(data))
    with pytest.raises(StateLineageError, match="STATE_LINEAGE_UNBOUND"):
        _open_bounded_state(db)

@pytest.mark.acceptance
def test_sidecar_identity_compares_lineage_and_source(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: complete identity tuple."""
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    connection = sqlite3.connect(data / "state.sqlite3")
    try:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        bind_state_connection(connection, data, "consumer")
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='lineage_source_release_sha'"
        ).fetchone()[0] == GENESIS.source_release_sha
        connection.execute(
            "UPDATE metadata SET value=? WHERE key='lineage_source_release_sha'",
            ("f" * 40,),
        )
        with pytest.raises(StateLineageError, match="STATE_LINEAGE_MISMATCH"):
            bind_state_connection(connection, data, "consumer")
    finally:
        connection.close()


@pytest.mark.acceptance
def test_routine_generation_rotation_does_not_invalidate_v2_sidecars(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: release-lineage granularity."""
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    connection = sqlite3.connect(data / "state.sqlite3")
    try:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        bind_state_connection(connection, data, "consumer")
        rotated = LineageIdentity(
            GENESIS.lineage_id,
            GENESIS.source_release_sha,
            "gen-1111111111111111",
        )
        manifest = tmp_path / "replica_manifest.json"
        manifest.write_text(
            '{"generation_id": "gen-1111111111111111", '
            f'"source_release_sha": "{GENESIS.source_release_sha}"}}',
            encoding="utf-8",
        )
        write_lineage(
            data,
            rotated,
            rotate_generation=True,
            verified_generation_manifest=manifest,
        )
        assert read_lineage(data) == rotated
        bind_state_connection(connection, data, "consumer")
    finally:
        connection.close()


@pytest.mark.acceptance
def test_generation_rotation_cannot_change_release_identity(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: no marker substitution."""
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    other = LineageIdentity(
        GENESIS.lineage_id,
        "f" * 40,
        "gen-1111111111111111",
    )
    with pytest.raises(StateLineageError, match="refusing to overwrite"):
        write_lineage(data, other, rotate_generation=True)

@pytest.mark.acceptance
def test_sidecar_stable_generation_mismatch_fails_closed(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: complete identity tuple."""
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    connection = sqlite3.connect(data / "state.sqlite3")
    try:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        bind_state_connection(connection, data, "consumer")
        connection.execute(
            "UPDATE metadata SET value=? WHERE key='lineage_generation_id'",
            ("gen-2222222222222222",),
        )
        with pytest.raises(StateLineageError, match="STATE_LINEAGE_MISMATCH"):
            bind_state_connection(connection, data, "consumer")
    finally:
        connection.close()

@pytest.mark.acceptance
def test_checkpoint_anchor_validation_accepts_current_input(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: anchor continuity."""
    from app.services.learning_derived_state import verify_checkpoint_anchors

    data = tmp_path / "data"
    data.mkdir()
    payload = b"{\"snapshot_id\":\"s1\"}\n"
    (data / "p1_evidence_ledger.jsonl").write_bytes(payload)
    db = data / ".p1_evidence_ledger.jsonl.dedup.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
    for key, value in {
        "indexed_offset": len(payload),
        "indexed_anchor_start": 0,
        "indexed_anchor_size": len(payload),
        "indexed_anchor_sha256": hashlib.sha256(payload).hexdigest(),
    }.items():
        connection.execute("INSERT INTO metadata VALUES(?, ?)", (key, str(value)))
    connection.commit()
    connection.close()
    assert verify_checkpoint_anchors(data)["failures"] == []


@pytest.mark.acceptance
def test_checkpoint_anchor_validation_rejects_truncation(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: truncation is not continuity."""
    from app.services.learning_derived_state import verify_checkpoint_anchors

    data = tmp_path / "data"
    data.mkdir()
    (data / "p1_evidence_ledger.jsonl").write_bytes(b"short\n")
    db = data / ".p1_evidence_ledger.jsonl.dedup.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO metadata VALUES('indexed_offset', '100')")
    connection.commit()
    connection.close()
    failures = verify_checkpoint_anchors(data)["failures"]
    assert failures

@pytest.mark.acceptance
def test_mixed_lineage_archive_requires_input_provenance(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: no single misleading label."""
    data, archive = _data_root(tmp_path)
    with pytest.raises(DerivedStateError, match="INPUT_PROVENANCE_REQUIRED"):
        archive_derived_state(
            data_root=data,
            archive_root=archive,
            lineage=LEGACY,
            active_lineage=GENESIS.lineage_id,
            reason="PARTIALLY_COMMITTED_RECOVERY",
            retire=True,
        )
    assert not archive.exists()


@pytest.mark.acceptance
def test_mixed_lineage_receipt_separates_derived_and_input_provenance(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: artifact-level provenance."""
    data, archive = _data_root(tmp_path)
    archive_derived_state(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="PARTIALLY_COMMITTED_RECOVERY",
        retire=True,
    )
    receipt = json.loads((archive / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["derived_lineage_id"] == LEGACY.lineage_id
    assert receipt["input_lineage_id"] == GENESIS.lineage_id
    assert receipt["input_source_release_sha"] == GENESIS.source_release_sha
    assert receipt["input_manifest_sha256"]
    for artifact in receipt["artifacts"]:
        if artifact["category"] == "derived":
            assert artifact["artifact_lineage_id"] == LEGACY.lineage_id
        else:
            assert artifact["artifact_lineage_id"] == GENESIS.lineage_id

@pytest.mark.acceptance
def test_existing_archive_is_freshly_verified_before_retirement(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: stale VERIFIED is insufficient."""
    data, archive = _data_root(tmp_path)
    kwargs = dict(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="PARTIALLY_COMMITTED_RECOVERY",
    )
    archive_derived_state(**kwargs, retire=False)
    (archive / "payload/phase3c_forward_outcomes.jsonl").write_bytes(b"tampered\n")
    with pytest.raises(IncompleteArchiveError, match="fresh verification"):
        archive_derived_state(**kwargs, retire=True)
    assert (data / "phase3c_forward_outcomes.jsonl").exists()


@pytest.mark.acceptance
def test_existing_archive_retry_is_idempotent(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: crash-safe retry."""
    data, archive = _data_root(tmp_path)
    kwargs = dict(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="PARTIALLY_COMMITTED_RECOVERY",
    )
    first = archive_derived_state(**kwargs, retire=True)
    second = archive_derived_state(**kwargs, retire=True)
    assert first["status"] == "VERIFIED"
    assert second["status"] == "ALREADY_VERIFIED"
    assert verify_archive(archive)["failures"] == []


@pytest.mark.acceptance
def test_retry_refuses_changed_active_derived_state(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: no new V2 state deletion."""
    data, archive = _data_root(tmp_path)
    kwargs = dict(
        data_root=data,
        archive_root=archive,
        lineage=LEGACY,
        input_lineage=GENESIS.lineage_id,
        input_source_release_sha=GENESIS.source_release_sha,
        input_manifest=data / "manifest.env",
        active_lineage=GENESIS.lineage_id,
        reason="PARTIALLY_COMMITTED_RECOVERY",
    )
    archive_derived_state(**kwargs, retire=False)
    (data / "phase3c_forward_outcomes.jsonl").write_bytes(b"new-v2-state\n")
    with pytest.raises(DerivedStateError, match="DERIVED_STATE_CHANGED_SINCE_ARCHIVE"):
        archive_derived_state(**kwargs, retire=True)
    assert (data / "phase3c_forward_outcomes.jsonl").read_bytes() == b"new-v2-state\n"

@pytest.mark.acceptance
def test_generation_rotation_requires_verified_manifest(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: rotation is not blind."""
    data = tmp_path / "data"
    data.mkdir()
    write_lineage(data, GENESIS)
    rotated = LineageIdentity(
        GENESIS.lineage_id, GENESIS.source_release_sha, "gen-1111111111111111"
    )
    with pytest.raises(StateLineageError, match="verified replica manifest"):
        write_lineage(data, rotated, rotate_generation=True)
