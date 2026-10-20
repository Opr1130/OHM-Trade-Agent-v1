"""HF-01 regression: hostile archives must fail BEFORE any filesystem write.

Reproduces the R1 defect (unsafe tar extraction + digest checked after unpack)
and proves the corrected behaviour.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

import opip_archive as archive_mod
import opip_bundle as bundle

CERTIFIED_SHA = bundle.CERTIFIED_SHA


def _write_sidecar(archive: Path, *, sha: str | None = None, name: str | None = None) -> Path:
    digest = sha or archive_mod.sha256_file(archive)
    sidecar = archive.with_name(bundle.ARCHIVE_DIGEST_NAME)
    sidecar.write_text(json.dumps({
        "archive": name or archive.name, "sha256": digest, "bytes": archive.stat().st_size,
    }) + "\n", encoding="utf-8")
    return sidecar


def _tar(path: Path, members: list[tuple[str, bytes]], *, duplicate: bool = False) -> Path:
    with tarfile.open(path, "w") as tar:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
            if duplicate:
                tar.addfile(info, io.BytesIO(payload))
    return path


def _symlink_tar(path: Path, linkname: str) -> Path:
    with tarfile.open(path, "w") as tar:
        info = tarfile.TarInfo("payload/evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = linkname
        tar.addfile(info)
        payload = b"x"
        real = tarfile.TarInfo("payload/real.txt")
        real.size = len(payload)
        tar.addfile(real, io.BytesIO(payload))
    return path


def test_wrong_checksum_with_traversal_member_creates_nothing_outside(tmp_path):
    """MANDATORY: invalid checksum + ../../outside-proof.txt must not escape."""
    archive = _tar(tmp_path / "hostile.tar", [
        ("payload/data/trade_outcomes.json", b"{}"),
        ("../../outside-proof.txt", b"escaped"),
    ])
    _write_sidecar(archive, sha="0" * 64)
    workdir = tmp_path / "extract"

    report = bundle.verify_bundle(archive=archive, workdir=workdir)

    assert report["verdict"] == "FAIL"
    assert report["checks"][0]["id"] == "ARCHIVE_DIGEST"
    assert report["checks"][0]["status"] == "FAIL"
    assert report.get("extraction_skipped", "").startswith("integrity evidence failed")
    assert not (tmp_path / "outside-proof.txt").exists()
    assert not (tmp_path.parent / "outside-proof.txt").exists()
    assert not workdir.exists(), "no extraction directory may be created before validation"


def test_correct_checksum_but_traversal_member_is_still_rejected(tmp_path):
    archive = _tar(tmp_path / "traversal.tar", [
        ("payload/data/trade_outcomes.json", b"{}"),
        ("../../outside-proof.txt", b"escaped"),
    ])
    _write_sidecar(archive)
    workdir = tmp_path / "extract"

    report = bundle.verify_bundle(archive=archive, workdir=workdir)

    assert report["verdict"] == "FAIL"
    failed = {c["id"] for c in report["checks"] if c["status"] == "FAIL"}
    assert "ARCHIVE_MEMBERS_SAFE" in failed
    assert not (tmp_path / "outside-proof.txt").exists()
    assert not (tmp_path.parent / "outside-proof.txt").exists()


def test_missing_digest_evidence_fails_before_extraction(tmp_path):
    archive = _tar(tmp_path / "no-sidecar.tar", [("payload/data/trade_outcomes.json", b"{}")])
    report = bundle.verify_bundle(archive=archive, workdir=tmp_path / "extract")
    assert report["verdict"] == "FAIL"
    assert report["checks"][0]["id"] == "ARCHIVE_DIGEST"
    assert not (tmp_path / "extract").exists()


def test_symlink_member_is_rejected(tmp_path):
    archive = _symlink_tar(tmp_path / "symlink.tar", "/etc/passwd")
    _write_sidecar(archive)
    with pytest.raises(archive_mod.ArchiveError, match="link"):
        archive_mod.safe_extract(archive, tmp_path / "extract")


def test_duplicate_member_is_rejected(tmp_path):
    archive = _tar(tmp_path / "dup.tar", [("payload/data/trade_outcomes.json", b"{}")], duplicate=True)
    _write_sidecar(archive)
    with pytest.raises(archive_mod.ArchiveError, match="duplicate"):
        archive_mod.safe_extract(archive, tmp_path / "extract")


def test_existing_verification_directory_is_never_deleted(tmp_path):
    archive = _tar(tmp_path / "ok.tar", [("payload/data/trade_outcomes.json", b"{}")])
    _write_sidecar(archive)
    workdir = tmp_path / "extract"
    workdir.mkdir()
    sentinel = workdir / "keep-me.txt"
    sentinel.write_text("do not delete", encoding="utf-8")

    report = bundle.verify_bundle(archive=archive, workdir=workdir)

    assert report["verdict"] == "FAIL"
    assert sentinel.is_file(), "the existing directory must not be cleared"
    assert sentinel.read_text(encoding="utf-8") == "do not delete"


def test_sidecar_naming_a_different_archive_is_rejected(tmp_path):
    archive = _tar(tmp_path / "renamed.tar", [("payload/data/trade_outcomes.json", b"{}")])
    _write_sidecar(archive, name="something-else.tar")
    with pytest.raises(archive_mod.ArchiveError, match="names"):
        archive_mod.verify_external_digest(archive)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_source_symlink_escape_is_rejected(tmp_path):
    source = tmp_path / "source"
    inside = source / "data"
    inside.mkdir(parents=True)
    (inside / "trade_outcomes.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": true}', encoding="utf-8")
    try:
        (inside / "escape.json").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted on this platform")

    with pytest.raises(archive_mod.ArchiveError, match="escapes"):
        archive_mod.assert_source_tree(source)
