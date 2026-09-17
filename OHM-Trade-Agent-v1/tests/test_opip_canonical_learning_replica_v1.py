"""Canonical learning replica contract: provenance and the failure matrix.

Every test here asserts a *fail-closed* direction. The replica is only ever
allowed to be distrusted; nothing in this module may promote an unprovable
artifact into usable learning authority.

The snapshot under test is produced by the real PR-A0 canonical backup
generation path (``publish_backup_generation``), not by a hand-built file, so
these tests also prove the bridge reuses the established online-backup,
manifest and rollback-journal normalization primitives rather than inventing a
second SQLite copy algorithm.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import shutil
from pathlib import Path

import pytest

from app.opip.canonical.backup import publish_backup_generation
from app.opip.canonical.writer import CanonicalWriter
from app.opip.learning.canonical_replica import (
    CANONICAL_DB_FILENAME,
    DEFAULT_REPLICA_FRESHNESS_SECONDS,
    MANIFEST_FILENAME,
    REASON_CONTRACT_UNSUPPORTED,
    REASON_DB_HASH_MISMATCH,
    REASON_DB_MISSING,
    REASON_DB_NOT_SELF_CONTAINED,
    REASON_MANIFEST_MALFORMED,
    REASON_MANIFEST_MISSING,
    REASON_PAPER_GAP_MISMATCH,
    REASON_PAPER_STATE_MISMATCH,
    REASON_PAPER_STATE_MISSING,
    REASON_SOURCE_SHA_MISMATCH,
    REASON_STALE,
    REASON_TIMESTAMP_INVALID,
    REPLICA_CONTRACT_VERSION,
    ReplicaProvenanceError,
    ReplicaStaleError,
    ReplicaUnavailableError,
    build_replica_manifest,
    replica_supports_completeness,
    verify_installed_replica,
    verify_replica_manifest,
)

RELEASE_SHA = "0cd0c30eba0d45fb97aa1032364bfd93be671657"
OTHER_SHA = "9067af25dbb011281c0c636faecf22e56f79850f"
NOW = datetime(2026, 9, 17, 3, 30, tzinfo=timezone.utc)


@pytest.fixture
def replica(tmp_path, monkeypatch):
    """A verified installed replica produced by the real backup generation."""
    source = tmp_path / "source.sqlite3"
    CanonicalWriter(source).close()  # establish canonical schema
    backup_dir = tmp_path / "generations"
    generation = publish_backup_generation(
        source, backup_dir, source_release_sha=RELEASE_SHA
    )

    directory = tmp_path / "replica"
    directory.mkdir()
    db = directory / CANONICAL_DB_FILENAME
    shutil.copyfile(generation.backup_path, db)

    paper_dir = directory / "paper_trading"
    paper_dir.mkdir()
    state = paper_dir / "state.json"
    state.write_text(
        json.dumps(
            {"schema_version": 1, "paper_only": True, "lifecycles": {}},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    spool = paper_dir / "evidence_gap_spool.json"
    spool.write_text(json.dumps({"unresolved": [], "updated_at": None}), encoding="utf-8")

    manifest = build_replica_manifest(
        source_release_sha=RELEASE_SHA,
        canonical_db=db,
        paper_state=state,
        paper_gap_spool=spool,
        snapshot_created_at_utc=NOW,
    )
    (directory / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")

    return {
        "dir": directory,
        "db": db,
        "state": state,
        "spool": spool,
        "manifest": manifest,
        "manifest_path": directory / MANIFEST_FILENAME,
    }


def _verify(replica, **overrides):
    kwargs = {
        "expected_source_release_sha": RELEASE_SHA,
        "replica_directory": replica["dir"],
        "paper_state_path": replica["state"],
        "paper_gap_spool_path": replica["spool"],
        "now": NOW,
    }
    kwargs.update(overrides)
    return verify_installed_replica(**kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_replica_verifies(replica):
    verified = _verify(replica)
    assert verified.contract_version == REPLICA_CONTRACT_VERSION
    assert verified.source_release_sha == RELEASE_SHA
    assert verified.canonical.present is True
    assert verified.canonical.sha256
    assert verified.paper_state.present is True
    assert verified.paper_gap_spool.present is True


def test_snapshot_is_self_contained_and_sidecar_free(replica):
    """The artifact must be a normalized, snapshot-consistent SQLite file."""
    assert not (replica["dir"] / f"{CANONICAL_DB_FILENAME}-wal").exists()
    assert not (replica["dir"] / f"{CANONICAL_DB_FILENAME}-shm").exists()
    assert _verify(replica).canonical.present is True


def test_legitimately_empty_outcome_stream_still_verifies(replica):
    """Provenance and emptiness are separate questions.

    A valid replica of a store with no terminal paper outcomes must verify.
    Emptiness is the reader's concern - it distinguishes an empty stream from an
    unavailable source - and must not be collapsed into a provenance failure.
    """
    from app.opip.learning.paper_outcome_reader import read_canonical_paper_outcomes

    assert _verify(replica).canonical.present is True
    read = read_canonical_paper_outcomes(replica["db"])
    assert read.outcomes == ()
    assert read.stream_present is False


def test_absent_gap_spool_is_legitimate_when_recorded_absent(tmp_path):
    """A spool that was legitimately absent is not a mismatch."""
    source = tmp_path / "src.sqlite3"
    CanonicalWriter(source).close()
    gen = publish_backup_generation(
        source, tmp_path / "gen", source_release_sha=RELEASE_SHA
    )
    directory = tmp_path / "replica"
    directory.mkdir()
    db = directory / CANONICAL_DB_FILENAME
    shutil.copyfile(gen.backup_path, db)

    manifest = build_replica_manifest(
        source_release_sha=RELEASE_SHA,
        canonical_db=db,
        paper_state=None,
        paper_gap_spool=None,
        snapshot_created_at_utc=NOW,
    )
    assert manifest["paper_gap_spool"]["present"] is False

    verified = verify_replica_manifest(
        manifest,
        replica_directory=directory,
        expected_source_release_sha=RELEASE_SHA,
        paper_state_path=directory / "absent-state.json",
        paper_gap_spool_path=directory / "absent-spool.json",
        now=NOW,
    )
    assert verified.paper_gap_spool.present is False
    assert verified.paper_state.present is False
    # Provenance is satisfied, but absent lifecycle state cannot prove
    # completeness - so supervised truth must still be refused downstream.
    supports, reasons = replica_supports_completeness(verified)
    assert supports is False
    assert "CANONICAL_REPLICA_PAPER_STATE_ABSENT" in reasons


def test_complete_replica_supports_completeness(replica):
    verified = _verify(replica)
    supports, reasons = replica_supports_completeness(verified)
    assert supports is True
    assert reasons == ()


# ---------------------------------------------------------------------------
# Manifest and contract
# ---------------------------------------------------------------------------


def test_missing_manifest_is_unavailable(replica):
    replica["manifest_path"].unlink()
    with pytest.raises(ReplicaUnavailableError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_MANIFEST_MISSING


def test_malformed_manifest_fails_closed(replica):
    replica["manifest_path"].write_text("{not json", encoding="utf-8")
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_MANIFEST_MALFORMED


def test_non_object_manifest_fails_closed(replica):
    replica["manifest_path"].write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_MANIFEST_MALFORMED


def test_unsupported_contract_version_fails_closed(replica):
    manifest = dict(replica["manifest"])
    manifest["contract_version"] = 99
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            manifest,
            replica_directory=replica["dir"],
            expected_source_release_sha=RELEASE_SHA,
            paper_state_path=replica["state"],
            paper_gap_spool_path=replica["spool"],
            now=NOW,
        )
    assert exc.value.reason == REASON_CONTRACT_UNSUPPORTED


# ---------------------------------------------------------------------------
# Release provenance
# ---------------------------------------------------------------------------


def test_source_sha_mismatch_fails_closed(replica):
    """A replica from a different release must never be accepted."""
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica, expected_source_release_sha=OTHER_SHA)
    assert exc.value.reason == REASON_SOURCE_SHA_MISMATCH


def test_malformed_expected_sha_fails_closed(replica):
    for bad in ("", "abc", "z" * 40, "0cd0c30e"):
        with pytest.raises(Exception):
            _verify(replica, expected_source_release_sha=bad)


# ---------------------------------------------------------------------------
# Canonical artifact integrity
# ---------------------------------------------------------------------------


def test_missing_db_is_unavailable(replica):
    replica["db"].unlink()
    with pytest.raises(ReplicaUnavailableError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_DB_MISSING


def test_tampered_db_hash_fails_closed(replica):
    """A byte-level change must be detected, not tolerated."""
    with replica["db"].open("ab") as handle:
        handle.write(b"\x00TRAMPLE")
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica)
    assert exc.value.reason in {REASON_DB_HASH_MISMATCH, REASON_DB_NOT_SELF_CONTAINED}


def test_size_mismatch_fails_closed(replica):
    with replica["db"].open("ab") as handle:
        handle.write(b"\x00")
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica)
    assert exc.value.reason in {REASON_DB_HASH_MISMATCH, REASON_DB_NOT_SELF_CONTAINED}


def test_sidecar_present_fails_closed(replica):
    """A sidecar-dependent artifact is not a self-contained replica.

    Matches the pre-existing copy-safety rule: a snapshot that still depends on
    its ``-wal``/``-shm`` companions can be internally inconsistent.
    """
    (replica["dir"] / f"{CANONICAL_DB_FILENAME}-wal").write_bytes(b"partial wal")
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_DB_NOT_SELF_CONTAINED


def test_wal_format_artifact_fails_closed(tmp_path):
    """The live WAL store is not an acceptable replica artifact.

    Guards the exact hazard the task calls out: never ship the live WAL file.
    """
    source = tmp_path / "live.sqlite3"
    writer = CanonicalWriter(source)
    try:
        pass
    finally:
        writer.close()

    directory = tmp_path / "replica"
    directory.mkdir()
    db = directory / CANONICAL_DB_FILENAME
    shutil.copyfile(source, db)  # raw copy of the live artifact

    manifest = {
        "schema_version": REPLICA_CONTRACT_VERSION,
        "contract_version": REPLICA_CONTRACT_VERSION,
        "source_release_sha": RELEASE_SHA,
        "snapshot_created_at_utc": "2026-09-17T03:30:00Z",
        "canonical": {
            "present": True,
            "sha256": __import__("app.opip.canonical.backup", fromlist=["x"]).hash_file_sha256(db),
            "size_bytes": db.stat().st_size,
        },
        "paper_state": {"present": False, "sha256": None, "size_bytes": None},
        "paper_gap_spool": {"present": False, "sha256": None, "size_bytes": None},
    }
    # A raw live copy is in WAL mode and/or carries sidecars; either way it is
    # rejected as not self-contained.
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            manifest,
            replica_directory=directory,
            expected_source_release_sha=RELEASE_SHA,
            paper_state_path=directory / "nope.json",
            paper_gap_spool_path=directory / "nope-spool.json",
            now=NOW,
        )
    assert exc.value.reason == REASON_DB_NOT_SELF_CONTAINED


# ---------------------------------------------------------------------------
# Companion artifacts (completeness)
# ---------------------------------------------------------------------------


def test_missing_paper_state_is_unavailable(replica):
    replica["state"].unlink()
    with pytest.raises(ReplicaUnavailableError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_PAPER_STATE_MISSING


def test_corrupt_paper_state_fails_closed(replica):
    """Outbox completeness cannot be judged from a damaged lifecycle file."""
    replica["state"].write_text('{"lifecycles": {}}', encoding="utf-8")
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_PAPER_STATE_MISMATCH


def test_corrupt_gap_spool_fails_closed(replica):
    replica["spool"].write_text('{"unresolved": [}]{', encoding="utf-8")
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_PAPER_GAP_MISMATCH


def test_manifest_claiming_present_state_that_is_absent_fails(replica):
    """Recorded-present but missing on disk is unavailable, not complete."""
    replica["state"].unlink()
    with pytest.raises(ReplicaUnavailableError) as exc:
        _verify(replica)
    assert exc.value.reason == REASON_PAPER_STATE_MISSING


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_stale_replica_fails_closed(replica):
    with pytest.raises(ReplicaStaleError) as exc:
        _verify(replica, now=NOW + timedelta(seconds=DEFAULT_REPLICA_FRESHNESS_SECONDS + 1))
    assert exc.value.reason == REASON_STALE


def test_replica_at_the_freshness_boundary_is_accepted(replica):
    _verify(replica, now=NOW + timedelta(seconds=DEFAULT_REPLICA_FRESHNESS_SECONDS))


def test_implausibly_future_timestamp_fails_closed(replica):
    """A future stamp is a provenance defect, not freshness."""
    with pytest.raises(ReplicaProvenanceError) as exc:
        _verify(replica, now=NOW - timedelta(seconds=DEFAULT_REPLICA_FRESHNESS_SECONDS + 1))
    assert exc.value.reason == REASON_TIMESTAMP_INVALID


def test_naive_timestamp_is_rejected(replica):
    manifest = dict(replica["manifest"])
    manifest["snapshot_created_at_utc"] = "2026-09-17T03:30:00"
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            manifest,
            replica_directory=replica["dir"],
            expected_source_release_sha=RELEASE_SHA,
            paper_state_path=replica["state"],
            paper_gap_spool_path=replica["spool"],
            now=NOW,
        )
    assert exc.value.reason in {REASON_TIMESTAMP_INVALID, REASON_STALE}


def test_zero_max_age_is_rejected(replica):
    with pytest.raises(ValueError):
        _verify(replica, max_age_seconds=0)


# ---------------------------------------------------------------------------
# Build-time guards
# ---------------------------------------------------------------------------


def test_build_requires_a_valid_release_sha(tmp_path):
    source = tmp_path / "src.sqlite3"
    CanonicalWriter(source).close()
    gen = publish_backup_generation(
        source, tmp_path / "gen", source_release_sha=RELEASE_SHA
    )
    for bad in ("", "abc", "UNVERIFIED", "z" * 40):
        with pytest.raises(Exception):
            build_replica_manifest(
                source_release_sha=bad, canonical_db=gen.backup_path
            )


def test_build_refuses_a_missing_canonical_snapshot(tmp_path):
    with pytest.raises(ReplicaUnavailableError):
        build_replica_manifest(
            source_release_sha=RELEASE_SHA, canonical_db=tmp_path / "nope.sqlite3"
        )


def test_build_refuses_a_wal_format_source(tmp_path):
    """Guards against wiring a raw live file into the bridge."""
    live = tmp_path / "live.sqlite3"
    writer = CanonicalWriter(live)
    writer.close()
    with pytest.raises(ReplicaProvenanceError) as exc:
        build_replica_manifest(source_release_sha=RELEASE_SHA, canonical_db=live)
    assert exc.value.reason == REASON_DB_NOT_SELF_CONTAINED
