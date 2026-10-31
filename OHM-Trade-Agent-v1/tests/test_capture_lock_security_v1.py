"""ATDD: secure shared capture lock without changing producer identities."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.jobs.capture_feature_bus_shadow import CaptureProcessLock
from tests.atdd_scope import (
    SCOPE_CHANGE_REQUIRED,
    check_contracts,
    load_acceptance_index,
    load_scope_contracts,
)

pytestmark = pytest.mark.acceptance
APP_ROOT = Path(__file__).resolve().parents[1]


def test_private_creation_and_existing_lock_identity(tmp_path: Path) -> None:
    """ATDD-BANDIT-CAPTURE-LOCK-M01/AC-001: preserve inode and non-overlap, create owner-private files."""
    path = tmp_path / "feature.lock"
    first = CaptureProcessLock(str(path))
    assert first.acquire() is True
    try:
        created = path.stat()
        assert stat.S_ISREG(created.st_mode)
        if os.name == "posix":
            assert created.st_mode & 0o077 == 0
        assert CaptureProcessLock(str(path)).acquire() is False
    finally:
        first.release()
    assert path.is_file()
    second = CaptureProcessLock(str(path))
    assert second.acquire() is True
    try:
        assert path.stat().st_ino == created.st_ino
    finally:
        second.release()


def test_legacy_safe_owner_readable_lock_is_accepted(tmp_path: Path) -> None:
    """ATDD-BANDIT-CAPTURE-LOCK-M01/AC-001: legacy 0644 inode stays usable without chmod, rename or unlink."""
    path = tmp_path / "legacy.lock"
    path.write_bytes(b"")
    if os.name == "posix":
        path.chmod(0o644)
    original = path.stat()
    lock = CaptureProcessLock(str(path))
    assert lock.acquire()
    lock.release()
    assert path.stat().st_ino == original.st_ino
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name != "posix", reason="atomic no-follow is a POSIX production guarantee")
def test_symlink_fifo_hardlink_and_writable_lock_rejected(tmp_path: Path) -> None:
    """ATDD-BANDIT-CAPTURE-LOCK-M01/AC-002: attacker-controlled lock inodes fail closed without writes."""
    target = tmp_path / "victim.txt"
    target.write_bytes(b"immutable")
    link = tmp_path / "symlink.lock"
    link.symlink_to(target)
    with pytest.raises(OSError):
        CaptureProcessLock(str(link)).acquire()
    assert target.read_bytes() == b"immutable"

    hardlink = tmp_path / "hardlink.lock"
    os.link(target, hardlink)
    with pytest.raises(PermissionError):
        CaptureProcessLock(str(hardlink)).acquire()
    assert target.read_bytes() == b"immutable"

    fifo = tmp_path / "fifo.lock"
    os.mkfifo(fifo)
    with pytest.raises(PermissionError):
        CaptureProcessLock(str(fifo)).acquire()

    writable = tmp_path / "writable.lock"
    writable.write_bytes(b"guard")
    writable.chmod(0o666)
    with pytest.raises(PermissionError):
        CaptureProcessLock(str(writable)).acquire()
    assert writable.read_bytes() == b"guard"


def test_maintenance_scope_stays_fail_closed() -> None:
    """ATDD-BANDIT-CAPTURE-LOCK-M01/AC-003: lock hardening is mapped, mixed runtime edits are forbidden."""
    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    acceptance = load_acceptance_index(APP_ROOT / "tests")
    increment = "ATDD-SCANNER-RELIABILITY-M01"
    lock_file = "OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py"
    allowed = check_contracts(contracts, acceptance, (lock_file,), active_increment=increment)
    assert allowed.passed, allowed.reasons
    denied = check_contracts(
        contracts,
        acceptance,
        (lock_file, "OHM-Trade-Agent-v1/app/opip/canonical/writer.py"),
        active_increment=increment,
    )
    assert denied.status == SCOPE_CHANGE_REQUIRED
