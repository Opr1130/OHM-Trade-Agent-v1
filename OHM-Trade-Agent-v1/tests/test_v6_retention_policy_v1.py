"""Acceptance checks for the O'Pip v6 retention policy table (v6 retention policy slice).

The table is the policy half of v6 retention: per data dataset it states the hot
window and the minimum floor that window may never go below. These tests pin two
criteria: the validator refuses a window below its floor, and the existing
read-only dry-run report takes its per-dataset windows from the table while
still changing nothing on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

import pytest

from app.opip.data_platform.retention_dry_run import (
    HOT_WINDOW_DAYS,
    build_report,
    main,
)
from app.opip.data_platform.retention_policy import (
    DEFAULT_HOT_WINDOW_DAYS,
    DEFAULT_MIN_FLOOR_DAYS,
    DEFAULT_RETENTION_POLICY,
    ROOT_DATASET,
    RetentionPolicyEntry,
    RetentionPolicyError,
    entry_for_dataset,
    validate_retention_policy,
)

CANONICAL_OLD = "canonical/canonical-20d.sqlite3"
CANONICAL_LIVE = "canonical/canonical-live.sqlite3"
EVENTS_OLD = "events/events-40d.jsonl"
EVENTS_BORDERLINE = "events/events-20d.jsonl"
ROOT_OLD = "scan_activity.jsonl"


def _write(path: Path, size_bytes: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    return path


def _age(path: Path, days: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def _seed_data_root(tmp_path: Path) -> Path:
    """Build a small data root with files inside and outside a 14-day window."""
    root = tmp_path / "data"
    _age(_write(root / CANONICAL_OLD, 4096), 20)
    _age(_write(root / CANONICAL_LIVE, 1024), 0.25)
    _age(_write(root / EVENTS_OLD, 2048), 40)
    _age(_write(root / EVENTS_BORDERLINE, 1024), 20)
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


def _forbid_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.mark.acceptance
def test_policy_table_rejects_a_window_below_its_floor() -> None:
    """ATDD-OPIP-V6-RETENTION-POLICY-v1/AC-001"""
    # The default is the owner's 14-day hot window, and the report alias matches.
    assert DEFAULT_HOT_WINDOW_DAYS == 14
    assert HOT_WINDOW_DAYS == DEFAULT_HOT_WINDOW_DAYS

    # The shipped table validates unchanged, and every floor is at or below its
    # own window, so the default can never be silently below a floor.
    validated = validate_retention_policy(DEFAULT_RETENTION_POLICY)
    assert validated == tuple(DEFAULT_RETENTION_POLICY)
    for entry in validated:
        assert entry.hot_window_days == DEFAULT_HOT_WINDOW_DAYS
        assert 1 <= entry.min_floor_days <= entry.hot_window_days
    assert {entry.dataset for entry in validated} == {
        "canonical",
        "events",
        ROOT_DATASET,
    }

    # A window below its dataset floor is rejected, naming the dataset and days.
    with pytest.raises(RetentionPolicyError) as rejected:
        validate_retention_policy(
            (RetentionPolicyEntry(dataset="events", hot_window_days=6, min_floor_days=7),)
        )
    message = str(rejected.value)
    assert "events" in message
    assert "6" in message and "7" in message

    # A window equal to the floor is allowed; every smaller window is not.
    at_floor = validate_retention_policy(
        (RetentionPolicyEntry(dataset="events", hot_window_days=7, min_floor_days=7),)
    )
    assert at_floor[0].hot_window_days == 7
    for window in (1, 3, 6):
        with pytest.raises(RetentionPolicyError):
            validate_retention_policy(
                (
                    RetentionPolicyEntry(
                        dataset="events",
                        hot_window_days=window,
                        min_floor_days=7,
                    ),
                )
            )

    # Other unusable rows fail closed instead of being corrected or clamped.
    with pytest.raises(RetentionPolicyError):
        validate_retention_policy(
            (RetentionPolicyEntry(dataset=" ", hot_window_days=14, min_floor_days=7),)
        )
    with pytest.raises(RetentionPolicyError):
        validate_retention_policy(
            (RetentionPolicyEntry(dataset="events", hot_window_days=0, min_floor_days=0),)
        )
    with pytest.raises(RetentionPolicyError):
        validate_retention_policy(
            (RetentionPolicyEntry(dataset="events"), RetentionPolicyEntry(dataset="events"))
        )

    # A valid table that widens one dataset still validates and is not rewritten.
    mixed = validate_retention_policy(
        (
            RetentionPolicyEntry(dataset="canonical", hot_window_days=30, min_floor_days=14),
            RetentionPolicyEntry(dataset="events", hot_window_days=7, min_floor_days=7),
        )
    )
    assert mixed[0].hot_window_days == 30
    assert mixed[1].hot_window_days == 7


@pytest.mark.acceptance
def test_dry_run_uses_policy_windows_and_changes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-RETENTION-POLICY-v1/AC-002"""
    root = _seed_data_root(tmp_path)
    before = _snapshot(root)
    _forbid_mutation(monkeypatch)

    # The default run takes its 14-day windows from the policy table.
    assert main(["--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert f"hot_window_days={DEFAULT_HOT_WINDOW_DAYS}" in output

    canonical_line = next(
        line for line in output.splitlines() if line.startswith("dataset=canonical ")
    )
    assert f"hot_window_days={DEFAULT_HOT_WINDOW_DAYS}" in canonical_line
    assert "min_floor_days=14" in canonical_line
    events_line = next(
        line for line in output.splitlines() if line.startswith("dataset=events ")
    )
    assert f"hot_window_days={DEFAULT_HOT_WINDOW_DAYS}" in events_line
    assert "min_floor_days=7" in events_line

    # Both old event files and the old canonical file cross the 14-day window.
    assert sorted(_prune_paths(output)) == sorted(
        [CANONICAL_OLD, EVENTS_OLD, EVENTS_BORDERLINE, ROOT_OLD]
    )
    assert CANONICAL_LIVE not in _prune_paths(output)

    # The JSON report carries each dataset's table window and floor, and still
    # reports that nothing was deleted.
    assert main(["--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["report"] == "OPIP_RETENTION_DRY_RUN"
    assert payload["dry_run"] is True
    assert payload["deleted"] == 0
    windows = {
        row["dataset"]: (row["hot_window_days"], row["min_floor_days"])
        for row in payload["datasets"]
    }
    assert windows["canonical"] == (14, 14)
    assert windows["events"] == (14, 7)
    assert windows[ROOT_DATASET] == (14, 7)

    # A different table really drives the report: with a 30-day events window
    # only the 40-day event file is a candidate, while canonical keeps 14 days.
    wider_events = validate_retention_policy(
        (
            RetentionPolicyEntry(dataset="canonical", hot_window_days=14, min_floor_days=14),
            RetentionPolicyEntry(dataset="events", hot_window_days=30, min_floor_days=7),
        )
    )
    report = build_report(root, policy=wider_events)
    events = next(item for item in report.datasets if item.dataset == "events")
    assert events.hot_window_days == 30
    assert events.min_floor_days == 7
    assert [candidate.path for candidate in events.prune_candidates] == [EVENTS_OLD]
    canonical = next(item for item in report.datasets if item.dataset == "canonical")
    assert canonical.hot_window_days == 14
    assert [candidate.path for candidate in canonical.prune_candidates] == [CANONICAL_OLD]

    # A dataset the table does not list gets the table default, never a laxer one.
    unlisted = entry_for_dataset("opip")
    assert unlisted.hot_window_days == DEFAULT_HOT_WINDOW_DAYS
    assert unlisted.min_floor_days == DEFAULT_MIN_FLOOR_DAYS

    # An explicit ad-hoc window is read-only too, and an invalid table fails closed.
    assert main(["--root", str(root), "--hot-window-days", "30"]) == 0
    capsys.readouterr()
    with pytest.raises(RetentionPolicyError):
        build_report(
            root,
            policy=(
                RetentionPolicyEntry(
                    dataset="canonical", hot_window_days=3, min_floor_days=14
                ),
            ),
        )

    missing = tmp_path / "absent-data-root"
    assert main(["--root", str(missing)]) == 64
    capsys.readouterr()
    assert not missing.exists()

    # Nothing was created, changed or removed by any run above.
    assert _snapshot(root) == before
    assert sorted(before) == sorted(
        [CANONICAL_LIVE, CANONICAL_OLD, EVENTS_BORDERLINE, EVENTS_OLD, ROOT_OLD]
    )


