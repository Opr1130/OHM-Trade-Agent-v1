"""Acceptance checks for the O'Pip v6 retention dry-run report (v6 slice C).

The report is the read-only half of the v6 retention policy: it says how large
each data dataset is and which files a 14-day hot window would prune. It is not
allowed to delete, move or modify anything, and these tests prove both halves.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

import pytest

from app.opip.data_platform.retention_dry_run import HOT_WINDOW_DAYS, main

ROOT_DATASET = "(root)"
CANONICAL_OLD = "canonical/canonical-2026-10-01.sqlite3"
CANONICAL_LIVE = "canonical/canonical-live.sqlite3"
EVENTS_OLD = "events/events-2026-10-05.jsonl"
ROOT_OLD = "scan_activity.jsonl"


def _write(path: Path, size_bytes: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    return path


def _age(path: Path, days: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def _seed_data_root(tmp_path: Path) -> Path:
    """Build a small data root with old and fresh files in several datasets."""
    root = tmp_path / "data"
    _age(_write(root / CANONICAL_OLD, 4096), 21)
    _age(_write(root / CANONICAL_LIVE, 1024), 0.25)
    _age(_write(root / EVENTS_OLD, 2048), 40)
    _age(_write(root / ROOT_OLD, 512), 30)
    (root / "learning").mkdir(parents=True)
    return root


def _prune_paths(output: str) -> list[str]:
    return [
        line.split()[-1]
        for line in output.splitlines()
        if line.strip().startswith("PRUNE ")
    ]


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """Record every file's size, mtime and content hash before the tool runs."""
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        stat_result = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return snapshot


@pytest.mark.acceptance
def test_report_prints_each_dataset_size_and_files_older_than_window(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-RETENTION-DRYRUN-v1/AC-001"""
    root = _seed_data_root(tmp_path)

    assert main(["--root", str(root)]) == 0
    output = capsys.readouterr().out

    # The window is the v6 hot window, 14 days, and the report states it.
    assert HOT_WINDOW_DAYS == 14
    assert f"root={root.resolve()}" in output
    assert f"hot_window_days={HOT_WINDOW_DAYS}" in output

    # Every dataset carries its own size, including one that holds no files.
    assert f"dataset=canonical files=2 size_bytes={4096 + 1024}" in output
    assert f"dataset=events files=1 size_bytes={2048}" in output
    assert f"dataset=learning files=0 size_bytes=0" in output
    assert f"dataset={ROOT_DATASET} files=1 size_bytes={512}" in output
    assert f"files=4 size_bytes={4096 + 1024 + 2048 + 512}" in output

    # Only the files older than the window are prune candidates.
    assert sorted(_prune_paths(output)) == sorted([CANONICAL_OLD, EVENTS_OLD, ROOT_OLD])
    assert CANONICAL_LIVE not in _prune_paths(output)
    assert f"total_prune_candidates=3 total_prune_bytes={4096 + 2048 + 512}" in output


@pytest.mark.acceptance
def test_dry_run_never_changes_or_removes_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-RETENTION-DRYRUN-v1/AC-002"""
    root = _seed_data_root(tmp_path)
    before = _snapshot(root)

    def _forbidden(label: str):
        def _fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(f"retention dry-run must not call {label}")

        return _fail

    for name in (
        "chmod",
        "hardlink_to",
        "link_to",
        "mkdir",
        "rename",
        "replace",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    ):
        if hasattr(Path, name):
            monkeypatch.setattr(Path, name, _forbidden(f"Path.{name}"))
    for name in (
        "chmod",
        "makedirs",
        "mkdir",
        "remove",
        "removedirs",
        "rename",
        "replace",
        "rmdir",
        "truncate",
        "unlink",
        "utime",
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _forbidden(f"os.{name}"))
    for name in ("copy", "copy2", "copytree", "move", "rmtree"):
        if hasattr(shutil, name):
            monkeypatch.setattr(shutil, name, _forbidden(f"shutil.{name}"))

    assert main(["--root", str(root)]) == 0
    capsys.readouterr()
    assert main(["--root", str(root), "--json"]) == 0
    json_output = capsys.readouterr().out
    assert '"dry_run": true' in json_output
    assert '"deleted": 0' in json_output
    assert main(["--root", str(root), "--hot-window-days", "1"]) == 0
    capsys.readouterr()

    missing = tmp_path / "absent-data-root"
    assert main(["--root", str(missing)]) == 64
    capsys.readouterr()
    assert not missing.exists()

    after = _snapshot(root)
    assert after == before
    assert sorted(after) == sorted([CANONICAL_LIVE, CANONICAL_OLD, EVENTS_OLD, ROOT_OLD])

