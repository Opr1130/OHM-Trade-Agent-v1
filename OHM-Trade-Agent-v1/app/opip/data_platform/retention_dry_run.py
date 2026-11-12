"""Read-only O'Pip retention dry-run report (v6 slice C).

The report answers two questions without touching anything:

- how large is each data dataset on this host, and
- which files would a hot-window prune (14 days by default) select.

This module only reads directory entries and file metadata. It never deletes,
moves, renames, truncates, compresses or writes any file, it opens no database
and it starts no background work. It has no trading, exchange, network or
Telegram authority: it prints a report and exits.

Run it from the application root::

    python -m app.opip.data_platform.retention_dry_run --root /app/data

A dataset is a top-level entry under the data root; files that sit directly in
the root are reported as the ``(root)`` dataset.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Iterable

HOT_WINDOW_DAYS = 14
ROOT_DATASET = "(root)"
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class RetentionFile:
    """One observed file: its size and how old it already is."""

    path: str
    size_bytes: int
    age_days: float

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "age_days": round(self.age_days, 2),
        }


@dataclass(frozen=True)
class DatasetRetentionReport:
    """Size of one dataset and the files the hot window would prune."""

    dataset: str
    size_bytes: int
    file_count: int
    prune_candidates: tuple[RetentionFile, ...]

    @property
    def prune_bytes(self) -> int:
        return sum(item.size_bytes for item in self.prune_candidates)

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
            "prune_candidate_count": len(self.prune_candidates),
            "prune_bytes": self.prune_bytes,
            "prune_candidates": [item.as_dict() for item in self.prune_candidates],
        }


@dataclass(frozen=True)
class RetentionDryRunReport:
    """Whole-root dry-run report. Nothing reported here has been applied."""

    root: str
    hot_window_days: int
    generated_at: str
    datasets: tuple[DatasetRetentionReport, ...]

    @property
    def total_files(self) -> int:
        return sum(item.file_count for item in self.datasets)

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.datasets)

    @property
    def total_prune_candidates(self) -> int:
        return sum(len(item.prune_candidates) for item in self.datasets)

    @property
    def total_prune_bytes(self) -> int:
        return sum(item.prune_bytes for item in self.datasets)

    def as_dict(self) -> dict[str, object]:
        return {
            "report": "OPIP_RETENTION_DRY_RUN",
            "dry_run": True,
            "deleted": 0,
            "root": self.root,
            "hot_window_days": self.hot_window_days,
            "generated_at": self.generated_at,
            "dataset_count": len(self.datasets),
            "total_files": self.total_files,
            "total_size_bytes": self.total_size_bytes,
            "total_prune_candidates": self.total_prune_candidates,
            "total_prune_bytes": self.total_prune_bytes,
            "datasets": [item.as_dict() for item in self.datasets],
        }


def _iter_files(root: Path) -> list[Path]:
    """List regular files under ``root`` in a deterministic order.

    Symlinks are skipped rather than followed, so a report can never wander out
    of the data directory it was pointed at.
    """
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_file():
            files.append(path)
    return files


def _dataset_for(relative: Path) -> str:
    parts = relative.parts
    return parts[0] if len(parts) > 1 else ROOT_DATASET


def _age_days(modified: float, *, now: datetime) -> float:
    modified_at = datetime.fromtimestamp(modified, tz=timezone.utc)
    return max(0.0, (now - modified_at).total_seconds() / SECONDS_PER_DAY)


def build_report(
    root: Path,
    *,
    hot_window_days: int = HOT_WINDOW_DAYS,
    now: datetime | None = None,
) -> RetentionDryRunReport:
    """Collect sizes and hot-window prune candidates. Read-only by construction."""
    if hot_window_days <= 0:
        raise ValueError("hot_window_days must be a positive number of days")
    data_root = Path(root)
    if not data_root.is_dir():
        raise NotADirectoryError(f"data root is not a directory: {data_root}")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    sizes: dict[str, int] = {}
    files_by_dataset: dict[str, list[RetentionFile]] = {}

    # A dataset that holds no files yet still appears, so an empty dataset can
    # never be silently missing from the report.
    for child in sorted(data_root.iterdir()):
        if child.is_symlink() or not child.is_dir():
            continue
        sizes.setdefault(child.name, 0)
        files_by_dataset.setdefault(child.name, [])

    for path in _iter_files(data_root):
        try:
            stat_result = path.stat()
        except OSError:
            continue
        relative = path.relative_to(data_root)
        dataset = _dataset_for(relative)
        size_bytes = int(stat_result.st_size)
        sizes[dataset] = sizes.get(dataset, 0) + size_bytes
        entry = RetentionFile(
            path=relative.as_posix(),
            size_bytes=size_bytes,
            age_days=_age_days(stat_result.st_mtime, now=current),
        )
        files_by_dataset.setdefault(dataset, []).append(entry)

    datasets = tuple(
        DatasetRetentionReport(
            dataset=dataset,
            size_bytes=sizes.get(dataset, 0),
            file_count=len(files),
            prune_candidates=tuple(
                sorted(
                    (item for item in files if item.age_days > hot_window_days),
                    key=lambda item: (-item.age_days, item.path),
                )
            ),
        )
        for dataset, files in sorted(files_by_dataset.items())
    )

    return RetentionDryRunReport(
        root=str(data_root),
        hot_window_days=hot_window_days,
        generated_at=current.isoformat(),
        datasets=datasets,
    )


def render_text(report: RetentionDryRunReport) -> str:
    """Render the human-readable report. Printing is the only side effect."""
    lines = [
        "O'Pip retention dry-run (read-only; nothing is deleted or modified)",
        f"root={report.root} hot_window_days={report.hot_window_days}",
        (
            f"datasets={len(report.datasets)} files={report.total_files} "
            f"size_bytes={report.total_size_bytes}"
        ),
    ]
    for item in report.datasets:
        lines.append(
            f"dataset={item.dataset} files={item.file_count} "
            f"size_bytes={item.size_bytes} "
            f"prune_candidates={len(item.prune_candidates)} "
            f"prune_bytes={item.prune_bytes}"
        )
        for candidate in item.prune_candidates:
            lines.append(
                f"  PRUNE age_days={candidate.age_days:.1f} "
                f"size_bytes={candidate.size_bytes} {candidate.path}"
            )
    lines.append(
        f"total_prune_candidates={report.total_prune_candidates} "
        f"total_prune_bytes={report.total_prune_bytes}"
    )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report O'Pip data sizes per dataset and the files a hot-window "
            "prune would select. Read-only: never deletes or modifies."
        )
    )
    parser.add_argument("--root", required=True, help="Data directory to report on")
    parser.add_argument(
        "--hot-window-days",
        type=int,
        default=HOT_WINDOW_DAYS,
        help=f"Hot data window in days (default {HOT_WINDOW_DAYS})",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"data root must be an existing directory: {root}", file=sys.stderr)
        return 64

    report = build_report(root, hot_window_days=args.hot_window_days)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
