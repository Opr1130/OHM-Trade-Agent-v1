from contextlib import contextmanager
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services import release_provenance_reconcile
from app.services.release_provenance_reconcile import (
    ContainerFacts,
    DatabaseFacts,
    Evidence,
    DeployLockBusy,
    deploy_lock,
    failed_checks,
    evaluate,
    reconcile,
)

SHA = "a26d664972075b2a220a466215cf71e389decb86"
CORE_IMAGE = "sha256:aa565b64f89ebc8877dfb17d952b0faa5dbed9058a3c9ecfa4269bf480e069a3"
WRITER_IMAGE = "sha256:076753a6a41eeffcc23be7bc0173d4ad2a4780b771c47a515c9d12fbb3b5fe01"
WRITER_ID = "cd18c9cf943a78f44db6b8691c92b5159bc9c14e0a64c82258b28cde8a46540e"
CORE_ID = "13fa3a21ed513d9e0e8a74321e683700275a0bf85f509e4a595e4ac717d11e43"


def _live() -> dict:
    return {
        "receipt": "LIVE_ACCEPTANCE",
        "receipt_schema": 1,
        "release": {
            "app_sha": SHA,
            "architecture_gate": "PASS",
            "certified_writer_image": WRITER_IMAGE,
            "core_image": CORE_IMAGE,
        },
        "authority": {
            "canonical_writer_mode": "shadow",
            "committee_mode": "off",
            "feature_bus": "shadow",
            "funded_authority": "ABSENT",
            "new_entry_authority": "LEGACY_ONLY",
            "paper_v2": "OFF",
            "release_profile": "EVIDENCE_SHADOW",
            "target_spine": "shadow",
        },
        "database": {
            "event_count": 0,
            "foreign_key_violations": 0,
            "history_epoch": 1,
            "integrity_check": "ok",
            "next_local_sequence": 1,
        },
        "target_state": "LIVE_EVIDENCE_SHADOW",
    }


def _upgrade() -> dict:
    return {
        "receipt": "CORE_SECURITY_UPGRADE_QUALIFICATION",
        "receipt_schema": 1,
        "append_only": True,
        "candidate_image": {
            "built_from_sha": SHA,
            "image_id": CORE_IMAGE,
            "revision_label": SHA,
            "tag": "opip-v2:sec-a26d66497207",
        },
        "merged_main_sha": SHA,
        "preserved_state": {
            "writer_container_id": WRITER_ID,
            "writer_image": WRITER_IMAGE,
            "writer_status": "running",
            "writer_untouched": True,
        },
        "provenance": {"verdict": "VERIFIED"},
        "status": "QUALIFIED_NOT_DEPLOYED",
    }


def _runtime_env(**overrides) -> dict:
    env = {
        "OPIP_RELEASE_PROFILE": "EVIDENCE_SHADOW",
        "OPIP_FEATURE_BUS_MODE": "shadow",
        "OPIP_CANONICAL_WRITER_MODE": "shadow",
        "OPIP_TARGET_SPINE_MODE": "shadow",
        "OPIP_PAPER_V2_MODE": "off",
        "OPIP_COMMITTEE_MODE": "off",
    }
    env.update(overrides)
    return env


def _core(**overrides) -> ContainerFacts:
    values = {
        "name": "ohm-trade-agent",
        "container_id": CORE_ID,
        "image_id": CORE_IMAGE,
        "revision": SHA,
        "status": "running",
        "health": "healthy",
        "env": _runtime_env(),
    }
    values.update(overrides)
    return ContainerFacts(**values)


def _writer(**overrides) -> ContainerFacts:
    values = {
        "name": "opip-canonical-writer",
        "container_id": WRITER_ID,
        "image_id": WRITER_IMAGE,
        "revision": "c196dd674678b3b1ee91c351ee97c1c9e2b3938d",
        "status": "running",
        "health": "healthy",
        "env": {"OPIP_CANONICAL_WRITER_MODE": "shadow"},
    }
    values.update(overrides)
    return ContainerFacts(**values)


def _db(**overrides) -> DatabaseFacts:
    values = {
        "journal_mode": "wal",
        "quick_check": "ok",
        "foreign_key_violations": 0,
        "event_count": 8819,
        "max_local_sequence": 8980,
        "history_epoch": 1,
        "schema_version": 1,
    }
    values.update(overrides)
    return DatabaseFacts(**values)


def _evidence(**overrides) -> Evidence:
    values = {
        "expected_sha": SHA,
        "core": _core(),
        "writer": _writer(),
        "live_acceptance": _live(),
        "core_upgrade": _upgrade(),
        "receipt_hashes": {"LIVE-ACCEPTANCE.json": "0" * 64},
        "manifest_entries": 1186,
        "manifest_mismatches": 0,
        "database": _db(),
        "last_good_before": "",
    }
    values.update(overrides)
    return Evidence(**values)



def _failed_names(evidence: Evidence) -> set[str]:
    return {check.name for check in failed_checks(evaluate(evidence))}


@pytest.mark.acceptance
def test_complete_evidence_passes_every_check():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-002: contract criterion."""
    checks = evaluate(_evidence())
    assert failed_checks(checks) == []
    assert len(checks) >= 20


def test_core_image_revision_must_match_expected_sha():
    failed = _failed_names(_evidence(core=_core(revision="b" * 40)))
    assert "core_image_revision_matches_expected" in failed


def test_core_image_id_must_match_both_receipts():
    failed = _failed_names(
        _evidence(core=_core(image_id="sha256:" + "0" * 64))
    )
    assert "core_image_matches_live_acceptance" in failed
    assert "core_image_matches_core_upgrade_candidate" in failed


@pytest.mark.acceptance
def test_canonical_writer_drift_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-002: contract criterion."""
    failed = _failed_names(
        _evidence(writer=_writer(image_id="sha256:" + "1" * 64))
    )
    assert "writer_image_unchanged" in failed


def test_canonical_writer_container_identity_drift_is_refused():
    failed = _failed_names(_evidence(writer=_writer(container_id="deadbeef")))
    assert "writer_container_identity_unchanged" in failed


def test_acceptance_release_sha_mismatch_is_refused():
    live = _live()
    live["release"]["app_sha"] = "c" * 40
    assert "live_acceptance_release_sha" in _failed_names(
        _evidence(live_acceptance=live)
    )


@pytest.mark.acceptance
def test_authority_widening_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-002: contract criterion."""
    live = _live()
    live["authority"]["paper_v2"] = "ON"
    live["authority"]["funded_authority"] = "PRESENT"
    failed = _failed_names(_evidence(live_acceptance=live))
    assert "authority_unchanged" in failed


@pytest.mark.parametrize(
    "database,expected_check",
    [
        (_db(journal_mode="delete"), "db_journal_wal"),
        (_db(quick_check="database disk image is malformed"), "db_integrity_ok"),
        (_db(foreign_key_violations=1), "db_foreign_keys_clean"),
        (_db(history_epoch=2), "db_history_epoch_matches_acceptance"),
        (_db(event_count=0, max_local_sequence=0), "db_sequence_monotonic"),
    ],
)
@pytest.mark.acceptance
def test_database_integrity_and_continuity_fail_closed(database, expected_check):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-002: contract criterion."""
    assert expected_check in _failed_names(_evidence(database=database))


@pytest.mark.acceptance
def test_release_manifest_mismatch_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-002: contract criterion."""
    failed = _failed_names(_evidence(manifest_mismatches=1))
    assert "release_manifest_verified" in failed
    assert "release_manifest_verified" in _failed_names(
        _evidence(manifest_entries=None, manifest_mismatches=None)
    )


def test_conflicting_last_good_sha_is_refused():
    failed = _failed_names(_evidence(last_good_before="d" * 40))
    assert "last_good_not_conflicting" in failed


def test_missing_core_upgrade_receipt_is_refused():
    failed = _failed_names(_evidence(core_upgrade={}))
    assert "core_upgrade_receipt_identity" in failed
    assert "core_upgrade_merged_main_sha" in failed


def _prepare(tmp_path: Path) -> tuple[Path, Path, Path]:
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()
    (bootstrap / "LIVE-ACCEPTANCE.json").write_text(json.dumps(_live()))
    (bootstrap / "CORE-UPGRADE-20261021T045227Z.json").write_text(
        json.dumps(_upgrade())
    )
    (bootstrap / "SCHEDULER_ACTIVATED.json").write_text(
        json.dumps({"certified_sha": "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"})
    )
    app_root = tmp_path / "app"
    app_root.mkdir()
    return bootstrap, app_root, tmp_path / "state"


@contextmanager
def _noop_lock():
    yield Path("test-deploy.lock")


def _run(tmp_path: Path, *, apply: bool, **overrides):
    bootstrap, app_root, state_dir = _prepare(tmp_path)
    calls = {"containers": [], "db": 0, "manifest": 0}
    core = overrides.pop("core", _core())
    writer = overrides.pop("writer", _writer())
    database = overrides.pop("database", _db())
    entries = overrides.pop("entries", 1186)
    mismatches = overrides.pop("mismatches", 0)
    lock = overrides.pop("lock", _noop_lock)
    receipt_writer = overrides.pop("receipt_writer", None)
    publisher = overrides.pop("publisher", None)
    before = {
        path.name: path.read_bytes() for path in bootstrap.iterdir() if path.is_file()
    }

    def container_reader(name: str) -> ContainerFacts:
        calls["containers"].append(name)
        return core if name == "ohm-trade-agent" else writer

    def db_reader(path: Path) -> DatabaseFacts:
        calls["db"] += 1
        return database

    def manifest_verifier(app_root: Path, manifest: Path):
        calls["manifest"] += 1
        return (entries, mismatches)

    receipt, code = reconcile(
        expected_sha=SHA,
        app_root=app_root,
        state_dir=state_dir,
        bootstrap_dir=bootstrap,
        manifest=bootstrap / "release-manifest.sha256",
        apply=apply,
        lock=lock,
        receipt_writer=receipt_writer or release_provenance_reconcile._append_receipt,
        publisher=publisher or release_provenance_reconcile._publish_last_good,
        container_reader=container_reader,
        db_reader=db_reader,
        manifest_verifier=manifest_verifier,
        now=datetime(2026, 10, 21, 18, 30, tzinfo=timezone.utc),
    )
    after = {
        path.name: path.read_bytes() for path in bootstrap.iterdir() if path.is_file()
    }
    assert before == after, "historical receipts must never be modified"
    assert calls["containers"] == ["ohm-trade-agent", "opip-canonical-writer"]
    assert receipt["historical_evidence_modified"] is False
    assert receipt["deploy_invoked"] is False
    assert receipt["services_restarted"] is False
    return receipt, code, state_dir, bootstrap


@pytest.mark.acceptance
def test_dry_run_passes_without_writing_anything(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-003: contract criterion."""
    receipt, code, state_dir, _ = _run(tmp_path, apply=False)
    assert code == 0
    assert receipt["status"] == "DRY_RUN_PASS"
    assert receipt["last_good_sha_published"] is False
    assert not state_dir.exists()


@pytest.mark.acceptance
def test_apply_publishes_exact_sha_and_append_only_receipt(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-003: contract criterion."""
    receipt, code, state_dir, _ = _run(tmp_path, apply=True)
    assert code == 0
    assert receipt["status"] == "RECONCILED"
    last_good = state_dir / "last-good-sha"
    assert last_good.read_text() == f"{SHA}\n"
    if os.name == "posix":
        assert stat.S_IMODE(last_good.stat().st_mode) == 0o600
    reconciled = list((state_dir / "provenance" / "reconciled").glob("*.json"))
    assert len(reconciled) == 2
    pending = next(path for path in reconciled if path.name.endswith("-pending.json"))
    published = next(
        path for path in reconciled if path.name.endswith("-published.json")
    )
    payload = json.loads(pending.read_text())
    assert payload["status"] == "RECONCILED"
    assert payload["publication_state"] == "PENDING"
    assert payload["expected_sha"] == SHA
    assert payload["evidence"]["core"]["image_id"] == CORE_IMAGE
    assert payload["evidence"]["writer"]["container_id"] == WRITER_ID
    assert payload["evidence"]["database"]["max_local_sequence"] == 8980
    confirmation = json.loads(published.read_text())
    assert confirmation["status"] == "PUBLISHED"
    assert confirmation["last_good_sha"] == SHA
    assert confirmation["marker_sha256"] == hashlib.sha256(
        f"{SHA}\n".encode("utf-8")
    ).hexdigest()


@pytest.mark.acceptance
def test_idempotent_apply_does_not_change_identical_sha(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-003: contract criterion."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "last-good-sha").write_text(f"{SHA}\n")
    receipt, code, _, _ = _run(tmp_path, apply=True)
    assert code == 0
    assert receipt["status"] == "RECONCILED"
    assert receipt["last_good_sha_changed"] is False
    assert (state_dir / "last-good-sha").read_text() == f"{SHA}\n"


@pytest.mark.acceptance
def test_refused_apply_writes_rejection_and_never_publishes(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-002: contract criterion."""
    receipt, code, state_dir, _ = _run(
        tmp_path, apply=True, core=_core(revision="b" * 40)
    )
    assert code == 2
    assert receipt["status"] == "REFUSED"
    assert "core_image_revision_matches_expected" in receipt["failures"]
    assert not (state_dir / "last-good-sha").exists()
    rejected = list((state_dir / "provenance" / "rejected").glob("*.json"))
    assert len(rejected) == 1
    assert json.loads(rejected[0].read_text())["status"] == "REFUSED"


@pytest.mark.acceptance
def test_conflicting_last_good_sha_is_never_overwritten(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-003: contract criterion."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "last-good-sha").write_text(f"{'d' * 40}\n")
    receipt, code, _, _ = _run(tmp_path, apply=True)
    assert code == 2
    assert "last_good_not_conflicting" in receipt["failures"]
    assert (state_dir / "last-good-sha").read_text() == f"{'d' * 40}\n"
    assert not (state_dir / "provenance" / "reconciled").exists()


@pytest.mark.acceptance
def test_writer_drift_blocks_publication(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-002: contract criterion."""
    receipt, code, state_dir, _ = _run(
        tmp_path, apply=True, writer=_writer(image_id="sha256:" + "9" * 64)
    )
    assert code == 2
    assert "writer_image_unchanged" in receipt["failures"]
    assert not (state_dir / "last-good-sha").exists()



@pytest.mark.acceptance
def test_live_runtime_authority_override_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-007: contract criterion."""
    env = _runtime_env(OPIP_PAPER_V2_MODE="active")
    failed = _failed_names(_evidence(core=_core(env=env)))
    assert "runtime_authority_modes" in failed


@pytest.mark.acceptance
def test_missing_runtime_authority_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-007: contract criterion."""
    env = _runtime_env()
    del env["OPIP_RELEASE_PROFILE"]
    assert "runtime_authority_modes" in _failed_names(
        _evidence(core=_core(env=env))
    )


@pytest.mark.acceptance
def test_truthy_funded_authority_key_is_refused():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-007: contract criterion."""
    env = _runtime_env(OPIP_FUNDED_TRADING_ENABLED="true")
    assert "runtime_funded_authority_absent" in _failed_names(
        _evidence(core=_core(env=env))
    )


@pytest.mark.acceptance
def test_core_writer_mode_must_stay_shadow():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-007: contract criterion."""
    env = _runtime_env(OPIP_CANONICAL_WRITER_MODE="active")
    assert "runtime_authority_modes" in _failed_names(
        _evidence(core=_core(env=env))
    )


@pytest.mark.skipif(os.name != "posix", reason="fcntl lock is POSIX-only")
@pytest.mark.acceptance
def test_busy_deploy_lock_is_detected(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-006: contract criterion."""
    lock_path = tmp_path / "deploy.lock"
    with deploy_lock(lock_path):
        with pytest.raises(DeployLockBusy):
            with deploy_lock(lock_path):
                pass


@pytest.mark.skipif(os.name != "posix", reason="fcntl lock is POSIX-only")
@pytest.mark.acceptance
def test_reconcile_refuses_when_controller_lock_is_held(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-006: contract criterion."""
    lock_path = tmp_path / "deploy.lock"
    with deploy_lock(lock_path):
        receipt, code = reconcile(
            expected_sha=SHA,
            app_root=tmp_path,
            state_dir=tmp_path / "state",
            bootstrap_dir=tmp_path,
            manifest=tmp_path / "release-manifest.sha256",
            apply=True,
            lock=lambda: deploy_lock(lock_path),
        )
    assert code == 2
    assert receipt["status"] == "REFUSED"
    assert receipt["failures"] == ["deploy_lock_busy"]
    assert not (tmp_path / "state" / "last-good-sha").exists()



@pytest.mark.acceptance
def test_newer_marker_written_after_validation_is_never_overwritten(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-006: contract criterion."""

    def racing_publisher(state_dir: Path, sha: str) -> bool:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "last-good-sha").write_text(f"{'e' * 40}\n")
        return release_provenance_reconcile._publish_last_good(state_dir, sha)

    receipt, code, state_dir, _ = _run(
        tmp_path, apply=True, publisher=racing_publisher
    )
    assert code == 2
    assert receipt["status"] == "REFUSED"
    assert receipt["failures"] == ["conflicting_release_marker"]
    assert (state_dir / "last-good-sha").read_text() == f"{'e' * 40}\n"
    rejected = list((state_dir / "provenance" / "rejected").glob("*.json"))
    assert any("conflict" in path.name for path in rejected)


@pytest.mark.acceptance
def test_receipt_write_failure_blocks_publication(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-008: contract criterion."""

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    receipt, code, state_dir, _ = _run(
        tmp_path, apply=True, receipt_writer=disk_full
    )
    assert code == 4
    assert receipt["status"] == "PROVENANCE_WRITE_FAILED"
    assert not (state_dir / "last-good-sha").exists()


@pytest.mark.acceptance
def test_publication_failure_leaves_durable_pending_provenance(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-008: contract criterion."""

    def failed_publish(state_dir: Path, sha: str) -> bool:
        raise OSError(28, "No space left on device")

    receipt, code, state_dir, _ = _run(
        tmp_path, apply=True, publisher=failed_publish
    )
    assert code == 3
    assert receipt["status"] == "PUBLICATION_FAILED"
    assert not (state_dir / "last-good-sha").exists()
    pending = list((state_dir / "provenance" / "reconciled").glob("*-pending.json"))
    failed = list((state_dir / "provenance" / "rejected").glob("*-failed.json"))
    assert len(pending) == 1
    assert len(failed) == 1


@pytest.mark.acceptance
def test_interrupted_replace_never_publishes_marker(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-008: contract criterion."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    import app.services.release_provenance_reconcile as module

    original_replace = module.os.replace

    def interrupted(src, dst):
        raise OSError(5, "Input/output error")

    module.os.replace = interrupted
    try:
        with pytest.raises(OSError):
            module._publish_last_good(state_dir, SHA)
    finally:
        module.os.replace = original_replace
    assert not (state_dir / "last-good-sha").exists()


@pytest.mark.acceptance
def test_receipt_is_fsynced_before_marker_replace(tmp_path, monkeypatch):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-008: contract criterion."""
    import app.services.release_provenance_reconcile as module

    events: list[str] = []
    real_fsync = module.os.fsync
    real_replace = module.os.replace

    def recording_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def recording_replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "fsync", recording_fsync)
    monkeypatch.setattr(module.os, "replace", recording_replace)

    state_dir = tmp_path / "state"
    module._append_receipt(
        state_dir / "provenance" / "reconciled",
        "pending",
        "20261021T190000Z",
        SHA,
        {"status": "RECONCILED"},
    )
    module._publish_last_good(state_dir, SHA)

    assert "fsync" in events
    assert "replace" in events
    assert events.index("fsync") < events.index("replace")

