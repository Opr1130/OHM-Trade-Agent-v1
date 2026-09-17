"""Canonical learning replica bridge: contract, export, install, races.

Every test here asserts a *fail-closed* direction. The replica may only ever be
distrusted; nothing in this bridge may promote an unprovable artifact into
usable learning authority.

Two seams are used deliberately:

* ``_published_snapshot`` uses the canonical online-backup primitive
  (``sqlite3.Connection.backup``) plus the repository's own
  ``normalize_to_rollback_journal`` to build a genuinely self-contained
  rollback-journal artifact without the directory-fsync step. The PR-A0
  durability primitive refuses on Windows because no documented Windows
  primitive proves a directory entry reached durable storage, so the full
  publication path cannot run on this workstation. The artifact produced here
  is the same format; only the durability assertion is absent. A POSIX-marked
  test additionally exercises the real ``publish_backup_generation`` path.
* ``_production_state`` writes a real ``state.json`` in the shipped envelope.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from app.opip.canonical.backup import hash_file_sha256, normalize_to_rollback_journal
from app.opip.canonical.writer import CanonicalWriter
from app.opip.learning.canonical_replica import (
    CANONICAL_RELATIVE,
    MANIFEST_FILENAME,
    PAPER_GAP_RELATIVE,
    PAPER_STATE_RELATIVE,
    REASON_DB_HASH_MISMATCH,
    REASON_DB_MISSING,
    REASON_DB_NOT_SELF_CONTAINED,
    REASON_GENERATION_ID_INVALID,
    REASON_MANIFEST_MALFORMED,
    REASON_MANIFEST_MISSING,
    REASON_PAPER_GAP_MISMATCH,
    REASON_PAPER_STATE_ABSENT,
    REASON_PAPER_STATE_MISMATCH,
    REASON_PAPER_STATE_MISSING,
    REASON_SCHEMA_UNSUPPORTED,
    REASON_SOURCE_SHA_MISMATCH,
    REASON_STALE,
    REASON_TIMESTAMP_INVALID,
    REPLICA_FRESHNESS_SECONDS,
    REPLICA_SCHEMA_VERSION,
    ReplicaProvenanceError,
    ReplicaStaleError,
    ReplicaUnavailableError,
    build_replica_manifest,
    export_replica_bundle,
    host_current_pointer,
    host_generations_dir,
    install_replica_generation,
    read_replica_manifest,
    replica_db_path,
    replica_paper_gap_path,
    replica_paper_state_path,
    replica_supports_completeness,
    require_generation_id,
    resolve_current_generation,
    verify_installed_replica,
    verify_replica_manifest,
    write_replica_manifest,
)

RELEASE_SHA = "0cd0c30eba0d45fb97aa1032364bfd93be671657"
OTHER_SHA = "9067af25dbb011281c0c636faecf22e56f79850f"
NOW = datetime(2026, 9, 17, 3, 30, tzinfo=timezone.utc)


def _published_snapshot(source: Path, dest: Path) -> Path:
    """Build a self-contained rollback-journal snapshot of ``source``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    normalize_to_rollback_journal(dest)
    return dest


def _seed_canonical(db: Path) -> None:
    CanonicalWriter(db).close()


def _production_state(path: Path, lifecycles: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_only": True,
                "lifecycles": lifecycles or {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _empty_spool(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"unresolved": [], "updated_at": None}), encoding="utf-8")
    return path


@pytest.fixture
def bundle(tmp_path):
    """A staged, valid replica bundle."""
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    snapshot = _published_snapshot(live, tmp_path / "snapshot.sqlite3")
    state = _production_state(tmp_path / "src_state.json")
    gap = _empty_spool(tmp_path / "src_gap.json")

    staging = tmp_path / "staging"
    manifest = export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=gap,
        generation_id="gen-test-0001",
        now=NOW,
        backup_work_dir=tmp_path / "work",
    )
    assert snapshot.exists()
    return {"tmp": tmp_path, "staging": staging, "manifest": manifest}


@pytest.fixture
def installed(bundle):
    """A bundle installed as the current generation on a host root."""
    host = bundle["tmp"] / "host"
    summary = install_replica_generation(
        staging_dir=bundle["staging"],
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    return {"host": host, "summary": summary, "current": resolve_current_generation(host)}


# ---------------------------------------------------------------------------
# Layout: one root, derived paths
# ---------------------------------------------------------------------------


def test_paths_derive_from_a_single_root(tmp_path):
    root = tmp_path / "r"
    assert replica_db_path(root) == root / CANONICAL_RELATIVE
    assert replica_paper_state_path(root) == root / PAPER_STATE_RELATIVE
    assert replica_paper_gap_path(root) == root / PAPER_GAP_RELATIVE
    assert replica_db_path(root).parent.name == "canonical"


def test_root_is_configurable_by_one_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_CANONICAL_REPLICA_ROOT", str(tmp_path))
    assert replica_db_path().is_relative_to(tmp_path)


def test_generation_id_rejects_path_traversal():
    for bad in ("", "  ", ".", "..", "a/b", "a\\b"):
        with pytest.raises(ReplicaProvenanceError) as exc:
            require_generation_id(bad)
        assert exc.value.reason == REASON_GENERATION_ID_INVALID
    assert require_generation_id("gen-abc") == "gen-abc"


# ---------------------------------------------------------------------------
# Export: bundle assembly
# ---------------------------------------------------------------------------


def test_export_produces_the_full_authority_bundle(bundle):
    staging = bundle["staging"]
    assert (staging / MANIFEST_FILENAME).exists()
    assert (staging / CANONICAL_RELATIVE).exists()
    assert (staging / PAPER_STATE_RELATIVE).exists()
    assert (staging / PAPER_GAP_RELATIVE).exists()


def test_export_artifact_is_sidecar_free_and_rollback_journal(bundle):
    db = bundle["staging"] / CANONICAL_RELATIVE
    assert not (db.parent / f"{db.name}-wal").exists()
    assert not (db.parent / f"{db.name}-shm").exists()
    with db.open("rb") as handle:
        header = handle.read(100)
    assert header[18:20] == b"\x01\x01"  # write/read format version 1 == rollback journal


def test_export_binds_all_three_inputs_into_the_manifest(bundle):
    manifest = bundle["manifest"]
    assert manifest["replica_schema_version"] == REPLICA_SCHEMA_VERSION
    assert manifest["source_release_sha"] == RELEASE_SHA
    assert manifest["canonical_db"]["present"] is True
    assert manifest["paper_state"]["present"] is True
    assert manifest["paper_gap_spool"]["present"] is True
    assert manifest["journal_mode"] == "delete"
    assert manifest["self_contained"] is True


def test_export_records_snapshot_facts(bundle):
    manifest = bundle["manifest"]
    assert manifest["event_count"] == 0
    assert isinstance(manifest["history_epoch"], int)
    assert manifest["max_local_sequence"] is None


def test_export_requires_lifecycle_state_by_default(tmp_path):
    """Missing lifecycle state cannot prove completeness, so export fails."""
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    with pytest.raises(ReplicaUnavailableError) as exc:
        export_replica_bundle(
            source_db=live,
            staging_dir=tmp_path / "staging",
            source_release_sha=RELEASE_SHA,
            paper_state_source=tmp_path / "absent.json",
            paper_gap_source=None,
        )
    assert exc.value.reason == REASON_PAPER_STATE_MISSING


def test_export_requires_a_valid_release_sha(tmp_path):
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    for bad in ("", "abc", "UNVERIFIED", "z" * 40):
        with pytest.raises(Exception):
            export_replica_bundle(
                source_db=live,
                staging_dir=tmp_path / "staging",
                source_release_sha=bad,
            )


def test_export_represents_absent_gap_spool_as_certified_empty(tmp_path):
    """A legitimately absent spool is never confused with a lost one."""
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    state = _production_state(tmp_path / "state.json")
    staging = tmp_path / "staging"
    manifest = export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=None,
        now=NOW,
    )
    assert manifest["paper_gap_spool"]["present"] is True
    written = json.loads((staging / PAPER_GAP_RELATIVE).read_text(encoding="utf-8"))
    assert written["unresolved"] == []


def test_export_cleans_its_own_backup_work_directory(tmp_path):
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    state = _production_state(tmp_path / "state.json")
    work = tmp_path / "work"
    export_replica_bundle(
        source_db=live,
        staging_dir=tmp_path / "staging",
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=None,
        backup_work_dir=work,
        now=NOW,
    )
    assert not work.exists()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_valid_bundle_verifies(bundle):
    verified = verify_replica_manifest(
        bundle["manifest"],
        root=bundle["staging"],
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    assert verified.generation_id == "gen-test-0001"
    assert verified.source_release_sha == RELEASE_SHA
    assert verified.canonical.present is True
    assert verified.paper_state.present is True
    assert verified.paper_gap_spool.present is True
    assert replica_supports_completeness(verified) == (True, ())


def test_missing_manifest_is_unavailable(bundle):
    (bundle["staging"] / MANIFEST_FILENAME).unlink()
    with pytest.raises(ReplicaUnavailableError) as exc:
        verify_installed_replica(
            expected_source_release_sha=RELEASE_SHA, root=bundle["staging"], now=NOW
        )
    assert exc.value.reason == REASON_MANIFEST_MISSING


def test_malformed_manifest_fails_closed(bundle):
    (bundle["staging"] / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_installed_replica(
            expected_source_release_sha=RELEASE_SHA, root=bundle["staging"], now=NOW
        )
    assert exc.value.reason == REASON_MANIFEST_MALFORMED


def test_unsupported_replica_schema_fails_closed(bundle):
    manifest = dict(bundle["manifest"])
    manifest["replica_schema_version"] = 99
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            manifest, root=bundle["staging"], expected_source_release_sha=RELEASE_SHA, now=NOW
        )
    assert exc.value.reason == REASON_SCHEMA_UNSUPPORTED


def test_source_sha_mismatch_fails_closed(bundle):
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=OTHER_SHA,
            now=NOW,
        )
    assert exc.value.reason == REASON_SOURCE_SHA_MISMATCH


def test_missing_db_is_unavailable(bundle):
    (bundle["staging"] / CANONICAL_RELATIVE).unlink()
    with pytest.raises(ReplicaUnavailableError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW,
        )
    assert exc.value.reason == REASON_DB_MISSING


def test_tampered_db_fails_closed(bundle):
    db = bundle["staging"] / CANONICAL_RELATIVE
    with db.open("ab") as handle:
        handle.write(b"\x00TRAMPLE")
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW,
        )
    assert exc.value.reason in {REASON_DB_HASH_MISMATCH, REASON_DB_NOT_SELF_CONTAINED}


def test_sidecar_presence_fails_closed(bundle):
    db = bundle["staging"] / CANONICAL_RELATIVE
    (db.parent / f"{db.name}-wal").write_bytes(b"partial wal")
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW,
        )
    assert exc.value.reason == REASON_DB_NOT_SELF_CONTAINED


def test_manifest_facts_must_match_the_snapshot(bundle):
    """A manifest cannot assert an epoch or count the artifact does not have."""
    manifest = dict(bundle["manifest"])
    manifest["event_count"] = 999
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            manifest, root=bundle["staging"], expected_source_release_sha=RELEASE_SHA, now=NOW
        )
    assert exc.value.reason == REASON_DB_NOT_SELF_CONTAINED


def test_missing_lifecycle_state_is_unavailable(bundle):
    (bundle["staging"] / PAPER_STATE_RELATIVE).unlink()
    with pytest.raises(ReplicaUnavailableError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW,
        )
    assert exc.value.reason == REASON_PAPER_STATE_MISSING


def test_corrupt_lifecycle_state_fails_closed(bundle):
    (bundle["staging"] / PAPER_STATE_RELATIVE).write_text('{"lifecycles": {}}', encoding="utf-8")
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW,
        )
    assert exc.value.reason == REASON_PAPER_STATE_MISMATCH


def test_corrupt_gap_spool_fails_closed(bundle):
    (bundle["staging"] / PAPER_GAP_RELATIVE).write_text('{"unresolved": [}]{', encoding="utf-8")
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW,
        )
    assert exc.value.reason == REASON_PAPER_GAP_MISMATCH


def test_absent_lifecycle_state_certifies_but_cannot_support_completeness(tmp_path):
    """Verified is not the same as sufficient for supervised truth."""
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    state = _production_state(tmp_path / "state.json")
    staging = tmp_path / "staging"
    manifest = export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=None,
        generation_id="gen-no-state",
        now=NOW,
    )
    # Simulate a bundle exported without lifecycle state.
    stripped = dict(manifest)
    stripped["paper_state"] = {"present": False, "sha256": None, "size_bytes": None}
    write_replica_manifest(stripped, staging / MANIFEST_FILENAME)
    (staging / PAPER_STATE_RELATIVE).unlink()

    verified = verify_replica_manifest(
        stripped, root=staging, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    supports, reasons = replica_supports_completeness(verified)
    assert supports is False
    assert REASON_PAPER_STATE_ABSENT in reasons


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_stale_replica_fails_closed(bundle):
    with pytest.raises(ReplicaStaleError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW + timedelta(seconds=REPLICA_FRESHNESS_SECONDS + 1),
        )
    assert exc.value.reason == REASON_STALE


def test_replica_at_the_freshness_boundary_is_accepted(bundle):
    verify_replica_manifest(
        bundle["manifest"],
        root=bundle["staging"],
        expected_source_release_sha=RELEASE_SHA,
        now=NOW + timedelta(seconds=REPLICA_FRESHNESS_SECONDS),
    )


def test_implausibly_future_timestamp_fails_closed(bundle):
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW - timedelta(seconds=REPLICA_FRESHNESS_SECONDS + 1),
        )
    assert exc.value.reason == REASON_TIMESTAMP_INVALID


def test_naive_timestamp_is_rejected(bundle):
    manifest = dict(bundle["manifest"])
    manifest["snapshot_created_at_utc"] = "2026-09-17T03:30:00"
    with pytest.raises(ReplicaProvenanceError) as exc:
        verify_replica_manifest(
            manifest, root=bundle["staging"], expected_source_release_sha=RELEASE_SHA, now=NOW
        )
    assert exc.value.reason in {REASON_TIMESTAMP_INVALID, REASON_STALE}


def test_zero_max_age_is_rejected(bundle):
    with pytest.raises(ValueError):
        verify_replica_manifest(
            bundle["manifest"],
            root=bundle["staging"],
            expected_source_release_sha=RELEASE_SHA,
            now=NOW,
            max_age_seconds=0,
        )


# ---------------------------------------------------------------------------
# Generation installation and the current pointer
# ---------------------------------------------------------------------------


def test_install_publishes_a_generation_and_pointer(installed):
    host = installed["host"]
    assert installed["summary"]["installed"] == "gen-test-0001"
    pointer = host_current_pointer(host)
    assert pointer.exists()
    # The pointer is an atomically-replaced file naming the generation, not a
    # symlink, so it needs no symlink privilege and is directly auditable.
    assert pointer.read_text(encoding="utf-8").strip() == "gen-test-0001"
    assert installed["current"].name == "gen-test-0001"
    assert (installed["current"] / MANIFEST_FILENAME).exists()


def test_installed_generation_reverifies_in_place(installed):
    verified = verify_installed_replica(
        expected_source_release_sha=RELEASE_SHA, root=installed["current"], now=NOW
    )
    assert verified.generation_id == "gen-test-0001"


def test_install_refuses_an_invalid_staged_bundle_and_keeps_previous(tmp_path):
    """A failed incoming generation must leave the previous one untouched."""
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    state = _production_state(tmp_path / "state.json")
    good = tmp_path / "good"
    export_replica_bundle(
        source_db=live,
        staging_dir=good,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=None,
        generation_id="gen-good",
        now=NOW,
    )
    host = tmp_path / "host"
    install_replica_generation(
        staging_dir=good, host_root=host, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    before = resolve_current_generation(host)
    assert before.name == "gen-good"

    bad = tmp_path / "bad"
    bad.mkdir()
    shutil_copy_tree(good, bad)
    (bad / CANONICAL_RELATIVE).write_bytes(b"not a database")

    with pytest.raises(ReplicaProvenanceError):
        install_replica_generation(
            staging_dir=bad, host_root=host, expected_source_release_sha=RELEASE_SHA, now=NOW
        )

    after = resolve_current_generation(host)
    assert after.name == "gen-good"
    assert after == before


def test_install_refuses_a_source_sha_mismatch(bundle, tmp_path):
    with pytest.raises(ReplicaProvenanceError) as exc:
        install_replica_generation(
            staging_dir=bundle["staging"],
            host_root=tmp_path / "host",
            expected_source_release_sha=OTHER_SHA,
            now=NOW,
        )
    assert exc.value.reason == REASON_SOURCE_SHA_MISMATCH


def test_install_refuses_a_stale_bundle(bundle, tmp_path):
    with pytest.raises(ReplicaStaleError):
        install_replica_generation(
            staging_dir=bundle["staging"],
            host_root=tmp_path / "host",
            expected_source_release_sha=RELEASE_SHA,
            now=NOW + timedelta(seconds=REPLICA_FRESHNESS_SECONDS + 1),
        )


def test_retention_is_bounded_and_never_removes_current(tmp_path):
    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    state = _production_state(tmp_path / "state.json")
    host = tmp_path / "host"
    for index in range(5):
        staging = tmp_path / f"staging-{index}"
        export_replica_bundle(
            source_db=live,
            staging_dir=staging,
            source_release_sha=RELEASE_SHA,
            paper_state_source=state,
            paper_gap_source=None,
            generation_id=f"gen-{index:04d}",
            now=NOW + timedelta(seconds=index),
        )
        install_replica_generation(
            staging_dir=staging,
            host_root=host,
            expected_source_release_sha=RELEASE_SHA,
            now=NOW + timedelta(seconds=index),
            retain=2,
        )
    generations = [p.name for p in host_generations_dir(host).iterdir() if p.is_dir()]
    assert len(generations) <= 2
    current = resolve_current_generation(host)
    assert current.name == "gen-0004"
    assert current.exists()


def test_resolve_refuses_a_missing_pointer(tmp_path):
    with pytest.raises(ReplicaUnavailableError) as exc:
        resolve_current_generation(tmp_path / "host")
    assert exc.value.reason == REASON_MANIFEST_MISSING


# ---------------------------------------------------------------------------
# Multi-epoch snapshot through the whole bridge
# ---------------------------------------------------------------------------


def _insert_outcome_at(db: Path, payload: dict, epoch: int, seq: int) -> None:
    connection = sqlite3.connect(str(db))
    try:
        connection.execute(
            """
            INSERT INTO events(event_id, schema_version, event_type, history_epoch,
                local_sequence, recorded_at, idempotency_key, payload_json)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                f"EVT:{epoch}{seq}",
                __import__(
                    "app.opip.canonical.paths", fromlist=["x"]
                ).EVENT_SCHEMA_VERSION,
                "paper_outcome.terminal.recorded",
                epoch,
                seq,
                "2026-09-17T03:00:00Z",
                f"paper_outcome.terminal.recorded:{payload['outcome_id']}",
                json.dumps(payload),
            ),
        )
        connection.execute(
            """
            INSERT INTO watermarks(stream, history_epoch, local_sequence, updated_at)
            VALUES ('paper_outcome.v1', ?, ?, ?)
            ON CONFLICT(stream) DO UPDATE SET
                history_epoch=excluded.history_epoch,
                local_sequence=excluded.local_sequence
            """,
            (epoch, seq, "2026-09-17T03:00:00Z"),
        )
        # A canonical restore advances the meta epoch and resets the sequence
        # counter. Reproduce that here, otherwise this fixture would not
        # actually exercise the post-restore coordinate layout.
        connection.execute(
            "UPDATE meta SET history_epoch = ?, next_local_sequence = ? WHERE id = 1",
            (epoch, seq + 1),
        )
        connection.commit()
    finally:
        connection.close()


def _outcome_payload(trade_id: str, episode_id: str) -> dict:
    from datetime import datetime as _dt

    from app.opip.contracts.paper_outcome import build_terminal_outcome_payload

    return build_terminal_outcome_payload(
        engine="OHM_PAPER_SIM_V1",
        paper_trade_id=trade_id,
        episode_id=episode_id,
        cohort_id="COH:1",
        strategy_version="OPIP-STRATEGY-V1",
        exchange="KRAKEN",
        native_symbol="BTCUSD",
        base_asset="BTC",
        direction="LONG",
        quote_currency="USD",
        terminal_status="CLOSED",
        exit_reason="STOP",
        exit_price=98.0,
        entry_timestamp=_dt(2026, 9, 17, 2, 0, tzinfo=timezone.utc),
        exit_timestamp=_dt(2026, 9, 17, 3, 0, tzinfo=timezone.utc),
        capital_committed=1000.0,
        gross_pnl=-25.0,
        fees_paid=4.0,
        net_pnl=-29.0,
        net_pnl_pct=-2.9,
        final_revision=7,
        terminal_event_id="PTE:" + "b" * 24,
        candidate_id="CAND:1",
        decision_context_id="DI-CONTEXT:" + "c" * 32,
    )


def test_multi_epoch_snapshot_preserves_the_true_tip_through_the_bridge(tmp_path):
    """The latent PR-A defect must not reappear through the replica path.

    ``(2, 1)`` is a higher coordinate than ``(1, 40)``; independent column
    maxima would fabricate ``(2, 40)``.
    """
    from app.opip.learning.paper_outcome_reader import read_canonical_paper_outcomes

    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    old = _outcome_payload("PAPER:" + "a" * 20, "EP:1")
    new = _outcome_payload("PAPER:" + "e" * 20, "EP:2")
    _insert_outcome_at(live, old, epoch=1, seq=40)
    _insert_outcome_at(live, new, epoch=2, seq=1)

    state = _production_state(tmp_path / "state.json")
    staging = tmp_path / "staging"
    manifest = export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=None,
        generation_id="gen-epochs",
        now=NOW,
    )
    assert manifest["event_count"] == 2
    assert manifest["history_epoch"] == 2
    assert manifest["next_local_sequence"] == 2
    # (2, 1) is the true tip. Independent column maxima across all events would
    # fabricate (2, 40), which is exactly the latent defect PR-A already fixed.
    assert manifest["max_local_sequence"] == 1

    host = tmp_path / "host"
    install_replica_generation(
        staging_dir=staging, host_root=host, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    current = resolve_current_generation(host)

    read = read_canonical_paper_outcomes(current / CANONICAL_RELATIVE)
    assert [(o.history_epoch, o.local_sequence) for o in read.outcomes] == [(1, 40), (2, 1)]
    assert read.stream_present is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only directory durability")
def test_real_publication_path_is_used_on_posix(tmp_path):
    """On POSIX the bridge uses the real PR-A0 generation path."""
    from app.opip.canonical.backup import publish_backup_generation

    live = tmp_path / "live.sqlite3"
    _seed_canonical(live)
    state = _production_state(tmp_path / "state.json")
    staging = tmp_path / "staging"
    export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=None,
        generation_id="gen-posix",
        now=NOW,
    )
    assert (staging / CANONICAL_RELATIVE).exists()
    generation = publish_backup_generation(
        live, tmp_path / "gen", source_release_sha=RELEASE_SHA
    )
    assert generation.manifest_path.exists()


def shutil_copy_tree(source: Path, dest: Path) -> None:
    import shutil as _shutil

    _shutil.copytree(source, dest, dirs_exist_ok=True)


def test_db_hash_helper_matches_manifest_value(bundle):
    db = bundle["staging"] / CANONICAL_RELATIVE
    assert hash_file_sha256(db) == bundle["manifest"]["canonical_db"]["sha256"]
