"""Acceptance checks for the O'Pip v6 Scorecard report tool.

The report is the read-only half of the Scorecard: it reads the stamped
qualification and outcome JSONL streams from a data directory and prints the
hit rate and count per strategy version from the pure Scorecard core. It is not
allowed to write, change or remove anything, and these tests prove both halves.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from app.opip.analytics.scorecard_report import main
from app.opip.decision.versioning import STRATEGY_VERSION, version_stamp

PREVIOUS_STRATEGY_VERSION = "OPIP-STRATEGY-V0"
SCREENING_STREAM = "opip/qualification/screening_evaluations.jsonl"
OUTCOME_STREAM = "opip/discovery/forward_outcomes.jsonl"
BROKEN_STREAM = "opip/discovery/broken_rows.jsonl"


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed_data_root(tmp_path: Path) -> Path:
    """Build a data root with stamped qualification rows and outcome rows."""
    root = tmp_path / "data"
    current = version_stamp()
    previous = dict(current, strategy_version=PREVIOUS_STRATEGY_VERSION)
    assert current["strategy_version"] == STRATEGY_VERSION
    _write_jsonl(
        root / SCREENING_STREAM,
        [
            {**current, "outcome": "ADVANCED", "hit": True},
            {**current, "outcome": "ADVANCED", "hit": True},
            {**current, "outcome": "REJECTED", "hit": False},
            # No explicit hit outcome: reported as unscored, never as a miss.
            {**current, "outcome": "ADVANCED"},
        ],
    )
    _write_jsonl(
        root / OUTCOME_STREAM,
        [
            {**previous, "hit": True},
            {**previous, "hit": False},
            {**previous, "hit": False},
            # Unstamped evidence is counted under UNVERSIONED.
            {"hit": True},
            # A stamp that is not a string is never attributed to a version.
            {"strategy_version": 7, "hit": True},
        ],
    )
    return root


@pytest.mark.acceptance
def test_report_prints_hit_rate_and_count_per_strategy_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-SCORECARD-REPORT-v1/AC-001"""
    root = _seed_data_root(tmp_path)

    assert main(["--root", str(root)]) == 0
    output = capsys.readouterr().out

    assert f"root={root.resolve()} sources=2" in output
    assert "records=9 scored=7 unscored=2 invalid=0" in output

    # One line per strategy version, with its hit rate and count. Both the
    # qualification plane and the outcome plane are read into the Scorecard.
    assert (
        f"strategy_version={STRATEGY_VERSION} hits=2 misses=1 count=3 "
        "hit_rate=0.666667 hit_rate_pct=66.67"
    ) in output
    assert (
        f"strategy_version={PREVIOUS_STRATEGY_VERSION} hits=1 misses=2 count=3 "
        "hit_rate=0.333333 hit_rate_pct=33.33"
    ) in output
    assert (
        "strategy_version=UNVERSIONED hits=1 misses=0 count=1 "
        "hit_rate=1.000000 hit_rate_pct=100.00"
    ) in output
    assert "strategy_versions=3 scored_records=7" in output

    # Each evidence file reports what it contributed, so an unscoreable row is
    # visible instead of being silently dropped.
    assert (
        f"source file={SCREENING_STREAM} records=4 scored=3 unscored=1 invalid=0"
    ) in output
    assert (
        f"source file={OUTCOME_STREAM} records=5 scored=4 unscored=1 invalid=0"
    ) in output


def _snapshot(root: Path) -> tuple[tuple[str, ...], dict[str, tuple[int, int, str]]]:
    """Record every entry plus each file's size, mtime and content hash."""
    entries = tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    )
    files: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        stat_result = path.stat()
        files[path.relative_to(root).as_posix()] = (
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return entries, files


@pytest.mark.acceptance
def test_report_never_writes_changes_or_removes_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-SCORECARD-REPORT-v1/AC-002"""
    root = _seed_data_root(tmp_path)
    (root / BROKEN_STREAM).write_text("not json\n", encoding="utf-8")
    before = _snapshot(root)

    def _forbidden(label: str):
        def _fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(f"scorecard report must not call {label}")

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
    text_output = capsys.readouterr().out
    # A malformed row is reported, never repaired or rewritten.
    assert (
        f"source file={BROKEN_STREAM} records=0 scored=0 unscored=0 invalid=1"
    ) in text_output

    assert main(["--root", str(root), "--json"]) == 0
    json_output = capsys.readouterr().out
    assert '"read_only": true' in json_output
    assert '"written_files": 0' in json_output
    assert '"deleted_files": 0' in json_output
    assert '"strategy_version": "UNVERSIONED"' in json_output

    missing = tmp_path / "absent-data-root"
    assert main(["--root", str(missing)]) == 64
    capsys.readouterr()
    assert not missing.exists()

    assert _snapshot(root) == before
    entries, _files = before
    assert entries == (
        "opip",
        "opip/discovery",
        f"opip/discovery/{Path(BROKEN_STREAM).name}",
        f"opip/discovery/{Path(OUTCOME_STREAM).name}",
        "opip/qualification",
        f"opip/qualification/{Path(SCREENING_STREAM).name}",
    )
