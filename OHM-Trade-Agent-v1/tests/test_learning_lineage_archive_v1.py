import hashlib
import shutil
from pathlib import Path

import pytest

from app.services.learning_lineage_archive import (
    create_archive,
    failed,
    verify_archive,
)

LINEAGE = "LINEAGE-A-LEGACY"
SOURCE_SHA = "8b3cc2712432ca21007be4db0667301d48b89d97"


def _generation(root: Path) -> Path:
    gen = root / "canonical-replica" / "generations" / "gen-b1f2c171c5c64601"
    (gen / "opip" / "canonical").mkdir(parents=True)
    (gen / "paper_trading").mkdir()
    (gen / "replica_manifest.json").write_text(
        '{"source_release_sha": "%s", "event_count": 220721}\n' % SOURCE_SHA
    )
    (gen / "opip" / "canonical" / "opip_canonical_v1.sqlite3").write_bytes(
        b"sqlite-bytes" * 100
    )
    (gen / "paper_trading" / "state.json").write_text('{"complete": true}\n')
    return gen


def _archive(tmp_path: Path, gen: Path) -> tuple[Path, Path]:
    archive = tmp_path / "archive" / LINEAGE / gen.name
    state = tmp_path / "state"
    create_archive(
        source_generation=gen,
        archive_root=archive,
        state_root=state,
        lineage_id=LINEAGE,
        source_release_sha=SOURCE_SHA,
        disk_usage=lambda path: type("D", (), {"free": 10**12})(),
    )
    return archive, state


def _verification(gen: Path, archive: Path, state: Path):
    return verify_archive(
        source_generation=gen,
        archive_dir=archive,
        state_root=state,
        lineage_id=LINEAGE,
        source_release_sha=SOURCE_SHA,
    )


@pytest.mark.acceptance
def test_verified_archive_passes_and_preserves_source(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    source_manifest_hash = hashlib.sha256(
        (gen / "replica_manifest.json").read_bytes()
    ).hexdigest()
    archive, state = _archive(tmp_path, gen)

    checks = _verification(gen, archive, state)
    assert failed(checks) == []
    assert (gen / "replica_manifest.json").is_file()
    assert hashlib.sha256((gen / "replica_manifest.json").read_bytes()).hexdigest() == (
        source_manifest_hash
    )
    assert (archive / "archive_manifest.json").is_file()


@pytest.mark.acceptance
def test_missing_archive_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    checks = _verification(gen, tmp_path / "missing", state)
    assert any(not check["passed"] for check in checks)


@pytest.mark.acceptance
def test_incomplete_archive_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    archive, state = _archive(tmp_path, gen)
    (archive / "paper_trading" / "state.json").unlink()
    checks = _verification(gen, archive, state)
    assert any(check["name"] == "archive_no_missing_files" and not check["passed"] for check in checks)


@pytest.mark.acceptance
def test_corrupted_archive_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    archive, state = _archive(tmp_path, gen)
    target = archive / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    target.write_bytes(b"corrupted")
    checks = _verification(gen, archive, state)
    assert any(check["name"] == "archive_content_verified" and not check["passed"] for check in checks)


@pytest.mark.acceptance
def test_extra_archive_file_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    archive, state = _archive(tmp_path, gen)
    (archive / "extra.txt").write_text("not in manifest\n")
    checks = _verification(gen, archive, state)
    assert any(check["name"] == "archive_no_extra_files" and not check["passed"] for check in checks)


@pytest.mark.parametrize("field,value", [("lineage_id", "LINEAGE-B"), ("source_release_sha", "f" * 40)])
@pytest.mark.acceptance
def test_wrong_lineage_or_source_is_refused(tmp_path, field, value):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    archive, state = _archive(tmp_path, gen)
    checks = verify_archive(
        source_generation=gen,
        archive_dir=archive,
        state_root=state,
        lineage_id=value if field == "lineage_id" else LINEAGE,
        source_release_sha=value if field == "source_release_sha" else SOURCE_SHA,
    )
    assert any(not check["passed"] for check in checks)


@pytest.mark.acceptance
def test_missing_receipt_is_refused(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    archive, state = _archive(tmp_path, gen)
    for receipt in state.glob("*.json"):
        receipt.unlink()
    checks = _verification(gen, archive, state)
    assert any(check["name"] == "archive_receipt_present" and not check["passed"] for check in checks)


@pytest.mark.acceptance
def test_interrupted_copy_leaves_no_verified_archive_or_receipt(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    archive = tmp_path / "archive" / LINEAGE / gen.name
    state = tmp_path / "state"

    def interrupted(source, target):
        shutil.copy2(source, target)
        raise OSError(5, "simulated interruption")

    with pytest.raises(OSError):
        create_archive(
            source_generation=gen,
            archive_root=archive,
            state_root=state,
            lineage_id=LINEAGE,
            source_release_sha=SOURCE_SHA,
            disk_usage=lambda path: type("D", (), {"free": 10**12})(),
            copy_fn=interrupted,
        )
    assert not archive.exists()
    assert not list(state.glob("*.json")) if state.exists() else True
    assert (gen / "replica_manifest.json").is_file()


@pytest.mark.acceptance
def test_insufficient_disk_refuses_before_copy(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    gen = _generation(tmp_path)
    archive = tmp_path / "archive" / LINEAGE / gen.name
    with pytest.raises(ValueError, match="insufficient disk"):
        create_archive(
            source_generation=gen,
            archive_root=archive,
            state_root=tmp_path / "state",
            lineage_id=LINEAGE,
            source_release_sha=SOURCE_SHA,
            disk_usage=lambda path: type("D", (), {"free": 1})(),
        )
    assert not archive.exists()


@pytest.mark.acceptance
def test_file_sync_failure_prevents_verified_archive(tmp_path, monkeypatch):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    import app.services.learning_lineage_archive as module
    from app.services.fs_durability import DurabilityError

    gen = _generation(tmp_path)
    archive = tmp_path / "archive" / LINEAGE / gen.name
    state = tmp_path / "state"

    def failing_sync(path):
        raise DurabilityError("injected file fsync failure")

    monkeypatch.setattr(module, "fsync_file", failing_sync)
    with pytest.raises(DurabilityError):
        create_archive(
            source_generation=gen,
            archive_root=archive,
            state_root=state,
            lineage_id=LINEAGE,
            source_release_sha=SOURCE_SHA,
            disk_usage=lambda path: type("D", (), {"free": 10**12})(),
        )
    assert not archive.exists()
    assert not (state / "receipt.json").exists()
    assert not list(state.glob("*.json")) if state.exists() else True
    assert (gen / "replica_manifest.json").is_file()


@pytest.mark.acceptance
def test_directory_sync_failure_prevents_verified_archive(tmp_path, monkeypatch):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    import app.services.learning_lineage_archive as module
    from app.services.fs_durability import DurabilityError

    gen = _generation(tmp_path)
    archive = tmp_path / "archive" / LINEAGE / gen.name
    state = tmp_path / "state"

    def failing_sync(path):
        raise DurabilityError("injected directory fsync failure")

    monkeypatch.setattr(module, "fsync_dir", failing_sync)
    with pytest.raises(DurabilityError):
        create_archive(
            source_generation=gen,
            archive_root=archive,
            state_root=state,
            lineage_id=LINEAGE,
            source_release_sha=SOURCE_SHA,
            disk_usage=lambda path: type("D", (), {"free": 10**12})(),
        )
    assert not archive.exists()
    assert not list(state.glob("*.json")) if state.exists() else True


@pytest.mark.acceptance
def test_receipt_sync_failure_discards_unverified_archive(tmp_path, monkeypatch):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-013: contract criterion."""
    import app.services.learning_lineage_archive as module
    from app.services.fs_durability import DurabilityError

    gen = _generation(tmp_path)
    archive = tmp_path / "archive" / LINEAGE / gen.name
    state = tmp_path / "state"
    real_sync = module.fsync_file

    def failing_receipt_sync(path):
        if path.parent == state:
            raise DurabilityError("injected receipt fsync failure")
        real_sync(path)

    monkeypatch.setattr(module, "fsync_file", failing_receipt_sync)
    with pytest.raises(DurabilityError):
        create_archive(
            source_generation=gen,
            archive_root=archive,
            state_root=state,
            lineage_id=LINEAGE,
            source_release_sha=SOURCE_SHA,
            disk_usage=lambda path: type("D", (), {"free": 10**12})(),
        )
    assert not archive.exists()
    assert not list(state.glob("*.json"))

