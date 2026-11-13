"""Acceptance checks for the O'Pip v6 Scorecard report coverage header.

The read-only Scorecard report now opens with one coverage line so a reader
sees how much evidence the report is about, and how much of it carries no
strategy version stamp, before reading any per-version row. These tests prove
the header states the real totals and that adding it changed nothing else: the
report still writes, changes and removes nothing.
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

#: Two stamped qualification rows plus one row that carries no stamp at all.
SCREENING_RECORDS = 3
#: Stamped outcome row, one unstamped scored row and one unstamped unscored row.
OUTCOME_RECORDS = 3
TOTAL_RECORDS = SCREENING_RECORDS + OUTCOME_RECORDS
#: One unstamped screening row, one unstamped scored row, one unstamped unscored.
TOTAL_UNVERSIONED = 3
TOTAL_SCORED = 5
TOTAL_UNSCORED = 1


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed_data_root(tmp_path: Path) -> Path:
    """Build a data root that mixes stamped and unstamped outcome records."""

    root = tmp_path / "data"
    current = version_stamp()
    previous = dict(current, strategy_version=PREVIOUS_STRATEGY_VERSION)
    assert current["strategy_version"] == STRATEGY_VERSION
    _write_jsonl(
        root / SCREENING_STREAM,
        [
            {**current, "outcome": "ADVANCED", "hit": True},
            {**current, "outcome": "REJECTED", "hit": False},
            # Evidence recorded before versioning existed: no stamp at all.
            {"outcome": "ADVANCED", "hit": True},
        ],
    )
    _write_jsonl(
        root / OUTCOME_STREAM,
        [
            {**previous, "hit": False},
            {"hit": True},
            # No stamp and no explicit hit: unversioned and unscored.
            {"barrier": "NO_BREAKOUT"},
        ],
    )
    return root


def _coverage_line() -> str:
    return (
        f"coverage total_records={TOTAL_RECORDS} "
        f"unversioned_records={TOTAL_UNVERSIONED}"
    )


@pytest.mark.acceptance
def test_report_first_line_reports_total_and_unversioned_records(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-SCORECARD-COVERAGE-v1/AC-001"""
    root = _seed_data_root(tmp_path)

    assert main(["--root", str(root)]) == 0
    lines = capsys.readouterr().out.splitlines()

    # AC-001: the very first line is the coverage header, before any row.
    assert lines[0] == _coverage_line()
    assert lines[1].startswith("O'Pip Scorecard (read-only")
    assert f"root={root.resolve()} sources=2" in lines[2]

    # The header counts agree with the rest of the report, so coverage is not
    # a second, independently computed number.
    counts = next(line for line in lines if line.startswith("records="))
    assert counts == (
        f"records={TOTAL_RECORDS} scored={TOTAL_SCORED} "
        f"unscored={TOTAL_UNSCORED} invalid=0"
    )

    # The per-version rows still follow the header unchanged, including the
    # UNVERSIONED group the header counts.
    assert (
        f"strategy_version={STRATEGY_VERSION} hits=1 misses=1 count=2 "
        "hit_rate=0.500000 hit_rate_pct=50.00"
    ) in lines
    assert (
        f"strategy_version={PREVIOUS_STRATEGY_VERSION} hits=0 misses=1 count=1 "
        "hit_rate=0.000000 hit_rate_pct=0.00"
    ) in lines
    assert (
        "strategy_version=UNVERSIONED hits=2 misses=0 count=2 "
        "hit_rate=1.000000 hit_rate_pct=100.00"
    ) in lines
    header_index = lines.index(_coverage_line())
    version_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith("strategy_version=")
    ]
    assert version_indexes
    assert all(index > header_index for index in version_indexes)

    # JSON output carries the same coverage counts without reading anything new.
    assert main(["--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["records"] == TOTAL_RECORDS
    assert payload["unversioned"] == TOTAL_UNVERSIONED
    assert payload["unscored"] == TOTAL_UNSCORED
    assert payload["read_only"] is True
    assert payload["written_files"] == 0


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
def test_report_coverage_header_never_writes_changes_or_removes_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-SCORECARD-COVERAGE-v1/AC-002"""
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
    text_lines = capsys.readouterr().out.splitlines()
    # The malformed row is still counted as invalid, and the header still
    # reports only the records the report actually parsed.
    assert text_lines[0] == _coverage_line()
    assert (
        f"source file={BROKEN_STREAM} records=0 scored=0 unscored=0 invalid=1"
    ) in text_lines

    assert main(["--root", str(root), "--json"]) == 0
    capsys.readouterr()

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
