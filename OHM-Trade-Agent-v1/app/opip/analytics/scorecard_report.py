"""Read-only O'Pip Scorecard report (v6 scorecard report slice).

The report answers one question without touching anything: for each strategy
version stamped on the evidence in a data directory, how often did a scored
outcome hit, and how many outcomes were scored for that version.

It is a thin reader around the pure core in
:mod:`app.opip.analytics.scorecard`. This module discovers the existing
qualification and outcome JSONL streams under the data root, selects the rows
that carry the Scorecard record contract (a ``strategy_version`` stamp and an
explicit boolean ``hit``) and hands them to :func:`score_outcomes`. It adds no
scoring rule of its own and never guesses an outcome: a row without a boolean
``hit`` is reported as unscored instead of being counted as a miss, and a row
whose version stamp is not a string is reported as unscored instead of being
attributed to a version it did not record.

Streams are read from the paths the existing writers already own, resolved
under the data root:

- ``opip/qualification/*.jsonl``: stamped qualification evidence written by
  ``app.opip.decision.store``.
- ``opip/discovery/*.jsonl``: discovery outcome evidence written by
  ``app.opip.discovery.store``.
- ``*_outcomes.jsonl`` in the data root itself, such as
  ``phase3c_forward_outcomes.jsonl``.

Dead-letter streams are skipped because they describe failed writes rather
than a recorded outcome.

The report opens with one coverage header line so a reader sees how much
evidence it is about to describe before reading any per-version row::

    coverage total_records=9 unversioned_records=1

``total_records`` is every parsed row the report read and ``unversioned_records``
is how many of those carry no usable strategy version stamp, using the same
:data:`~app.opip.analytics.scorecard.UNVERSIONED` rule as the per-version rows.
The header changes nothing about scoring: it is printed output only.

The report only opens files for reading. It never writes, creates, renames,
truncates, compresses or deletes a file, starts no background work and opens no
database. It has no trading, exchange, network, Telegram or learning authority:
it prints a report and exits.

Run it from the application root::

    python -m app.opip.analytics.scorecard_report --root /app/data
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import json
import sys
from typing import Any, Iterable, Mapping

from app.opip.analytics.scorecard import (
    HIT_FIELD,
    UNVERSIONED,
    VERSION_FIELD,
    VersionScore,
    score_outcomes,
    version_of,
)
from app.opip.storage.bounded_jsonl import iter_lines, parse_json_object_line

DEFAULT_DATA_ROOT = Path("/app/data")

#: Stamped qualification plane, owned by ``app.opip.decision.store``.
QUALIFICATION_PLANE = "opip/qualification"
#: Discovery outcome plane, owned by ``app.opip.discovery.store``.
OUTCOME_PLANES: tuple[str, ...] = ("opip/discovery",)
#: Root-level outcome ledgers, such as ``phase3c_forward_outcomes.jsonl``.
OUTCOME_ROOT_GLOB = "*_outcomes.jsonl"
DEAD_LETTER_MARKER = "dead_letter"


@dataclass(frozen=True)
class SourceScorecard:
    """What one evidence file contributed to the Scorecard."""

    path: str
    records: int
    scored: int
    unscored: int
    unversioned: int
    invalid: int
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "records": self.records,
            "scored": self.scored,
            "unscored": self.unscored,
            "unversioned": self.unversioned,
            "invalid": self.invalid,
            "error": self.error,
        }


@dataclass(frozen=True)
class ScorecardReport:
    """Whole-root Scorecard report. Nothing reported here was written."""

    root: str
    sources: tuple[SourceScorecard, ...]
    versions: tuple[VersionScore, ...]

    @property
    def total_records(self) -> int:
        return sum(item.records for item in self.sources)

    @property
    def total_scored(self) -> int:
        return sum(item.scored for item in self.sources)

    @property
    def total_unscored(self) -> int:
        return sum(item.unscored for item in self.sources)

    @property
    def total_unversioned(self) -> int:
        """How many read records carry no usable strategy version stamp."""

        return sum(item.unversioned for item in self.sources)

    @property
    def total_invalid(self) -> int:
        return sum(item.invalid for item in self.sources)

    def as_dict(self) -> dict[str, object]:
        return {
            "report": "OPIP_SCORECARD_REPORT",
            "read_only": True,
            "written_files": 0,
            "deleted_files": 0,
            "root": self.root,
            "source_count": len(self.sources),
            "records": self.total_records,
            "scored": self.total_scored,
            "unscored": self.total_unscored,
            "unversioned": self.total_unversioned,
            "invalid": self.total_invalid,
            "strategy_version_count": len(self.versions),
            "strategy_versions": [score.as_dict() for score in self.versions],
            "sources": [item.as_dict() for item in self.sources],
        }


def _jsonl_files(directory: Path) -> tuple[Path, ...]:
    """List the live JSONL evidence files in one plane, never following links."""

    if not directory.is_dir():
        return ()
    files: list[Path] = []
    for path in sorted(directory.glob("*.jsonl")):
        if path.is_symlink() or not path.is_file():
            continue
        if DEAD_LETTER_MARKER in path.name:
            continue
        files.append(path)
    return tuple(files)


def discover_sources(root: Path) -> tuple[Path, ...]:
    """Return the evidence files the report reads, in a deterministic order."""

    data_root = Path(root)
    files: list[Path] = list(_jsonl_files(data_root / QUALIFICATION_PLANE))
    for plane in OUTCOME_PLANES:
        files.extend(_jsonl_files(data_root / plane))
    for path in sorted(data_root.glob(OUTCOME_ROOT_GLOB)):
        if path.is_symlink() or not path.is_file():
            continue
        if DEAD_LETTER_MARKER in path.name:
            continue
        files.append(path)
    return tuple(sorted(set(files)))


def _display_path(path: Path, data_root: Path) -> str:
    try:
        return path.relative_to(data_root).as_posix()
    except ValueError:
        return str(path)


def is_scored_outcome(record: Mapping[str, Any]) -> bool:
    """Return whether a record states a scoreable outcome.

    A record is scoreable only when it carries an explicit boolean ``hit`` and a
    version stamp that is either absent, ``None`` or a string. Anything else is
    reported as unscored: an outcome is never guessed and evidence is never
    attributed to a version stamp it cannot prove.
    """

    if not isinstance(record.get(HIT_FIELD), bool):
        return False
    version = record.get(VERSION_FIELD)
    return version is None or isinstance(version, str)


def is_unversioned(record: Mapping[str, Any]) -> bool:
    """Return whether a record carries no usable strategy version stamp.

    The header and the per-version rows must agree on what "unversioned" means,
    so this reuses the pure core's :func:`version_of` instead of restating the
    rule. A stamp that is absent, ``None``, blank or whitespace is unversioned
    evidence; a stamp that is not a string at all is a malformed stamp, which
    the report already treats as unscored rather than grouping it anywhere.
    """

    try:
        return version_of(record) == UNVERSIONED
    except TypeError:
        return False


def _read_source(
    path: Path,
    data_root: Path,
) -> tuple[SourceScorecard, list[dict[str, Any]]]:
    display = _display_path(path, data_root)
    records = 0
    scored = 0
    unscored = 0
    unversioned = 0
    invalid = 0
    collected: list[dict[str, Any]] = []
    try:
        for raw in iter_lines(path):
            try:
                record = parse_json_object_line(raw)
            except (ValueError, UnicodeDecodeError):
                invalid += 1
                continue
            records += 1
            if is_unversioned(record):
                unversioned += 1
            if not is_scored_outcome(record):
                unscored += 1
                continue
            collected.append(
                {
                    VERSION_FIELD: record.get(VERSION_FIELD),
                    HIT_FIELD: record[HIT_FIELD],
                }
            )
            scored += 1
    except OSError as exc:
        return (
            SourceScorecard(
                path=display,
                records=records,
                scored=scored,
                unscored=unscored,
                unversioned=unversioned,
                invalid=invalid,
                error=str(exc),
            ),
            collected,
        )
    return (
        SourceScorecard(
            path=display,
            records=records,
            scored=scored,
            unscored=unscored,
            unversioned=unversioned,
            invalid=invalid,
        ),
        collected,
    )


def build_report(root: Path) -> ScorecardReport:
    """Read the evidence under ``root`` and score it. Read-only by construction."""

    data_root = Path(root)
    if not data_root.is_dir():
        raise NotADirectoryError(f"data root is not a directory: {data_root}")

    sources: list[SourceScorecard] = []
    collected: list[dict[str, Any]] = []
    for path in discover_sources(data_root):
        source, rows = _read_source(path, data_root)
        sources.append(source)
        collected.extend(rows)

    return ScorecardReport(
        root=str(data_root),
        sources=tuple(sources),
        versions=tuple(score_outcomes(collected).values()),
    )


def render_text(report: ScorecardReport) -> str:
    """Render the human-readable Scorecard. Printing is the only side effect."""

    lines = [
        (
            f"coverage total_records={report.total_records} "
            f"unversioned_records={report.total_unversioned}"
        ),
        "O'Pip Scorecard (read-only; nothing is written, changed or deleted)",
        f"root={report.root} sources={len(report.sources)}",
        (
            f"records={report.total_records} scored={report.total_scored} "
            f"unscored={report.total_unscored} invalid={report.total_invalid}"
        ),
    ]
    if not report.versions:
        lines.append("no scored outcome records found in the data directory")
    for score in report.versions:
        lines.append(
            f"strategy_version={score.strategy_version} hits={score.hits} "
            f"misses={score.misses} count={score.count} "
            f"hit_rate={score.hit_rate:.6f} hit_rate_pct={score.hit_rate * 100.0:.2f}"
        )
    lines.append(
        f"strategy_versions={len(report.versions)} "
        f"scored_records={report.total_scored}"
    )
    for source in report.sources:
        line = (
            f"source file={source.path} records={source.records} "
            f"scored={source.scored} unscored={source.unscored} "
            f"invalid={source.invalid}"
        )
        if source.error:
            line += f" error={source.error}"
        lines.append(line)
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Print the O'Pip Scorecard (hit rate and count per strategy "
            "version) from the stamped records in a data directory. "
            "Read-only: never writes or deletes."
        )
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_DATA_ROOT),
        help=f"Data directory to read (default {DEFAULT_DATA_ROOT})",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"data root must be an existing directory: {root}", file=sys.stderr)
        return 64

    report = build_report(root)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

