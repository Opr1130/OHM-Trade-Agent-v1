"""Reconciliation-matrix integrity (documentation guard).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The IC-001 to IC-045 matrix is an acceptance artifact, so it must not be able to
rot silently. These tests assert that every requirement is present exactly once,
that the status vocabulary is the declared one, and that the summary counts agree
with the rows. They do not assert *which* status a requirement has: that judgement
belongs to the reconciliation, not to a test that would freeze it in place.
"""

from __future__ import annotations

import pathlib
import re

DOC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "docs"
    / "MODULE2_INTELLIGENCE_COMMITTEE.md"
)

#: The status vocabulary the matrix is allowed to use. The last entry is the
#: OWNER-mandated status for a requirement that is implemented and offline-tested but
#: awaits credentialled validation.
STATUSES = (
    "GREEN",
    "PARTIAL",
    "MISSING",
    "IMPLEMENTED_AWAITING_CREDENTIALLED_SHADOW_VALIDATION",
    "IMPLEMENTED_AWAITING_DEPLOYMENT_APPROVAL",
    "OUT_OF_SCOPE",
)

#: Every requirement the matrix must cover.
REQUIRED_IDS = tuple(f"IC-{index:03d}" for index in range(1, 46))


def _matrix_rows() -> list[tuple[str, str]]:
    """Return (requirement id, status) for each matrix row."""
    text = DOC.read_text(encoding="utf-8")
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = re.match(r"^\|\s*(IC-\d{3})\s*\|(.+)\|\s*$", line)
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group(2).split("|")]
        if len(cells) < 2:
            continue
        status = cells[1].replace("*", "").strip()
        rows.append((match.group(1), status))
    return rows


def test_the_matrix_covers_every_requirement_exactly_once():
    ids = [requirement for requirement, _ in _matrix_rows()]
    assert len(ids) == len(REQUIRED_IDS), ids
    assert set(ids) == set(REQUIRED_IDS)
    assert len(set(ids)) == len(ids), "a requirement appears more than once"


def test_every_row_uses_the_declared_status_vocabulary():
    """A free-text status would make the matrix uncomparable between revisions."""
    for requirement, status in _matrix_rows():
        assert status in STATUSES, f"{requirement} uses {status!r}"


def test_every_row_cites_evidence():
    """A status without evidence is an assertion, which the mandate forbids."""
    text = DOC.read_text(encoding="utf-8")
    for line in text.splitlines():
        match = re.match(r"^\|\s*(IC-\d{3})\s*\|", line)
        if match is None:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) >= 4, f"{match.group(1)} has no evidence column"
        assert len(cells[3]) > 20, f"{match.group(1)} cites nothing substantive"


def test_the_summary_counts_agree_with_the_rows():
    rows = _matrix_rows()
    observed = {status: 0 for status in STATUSES}
    for _, status in rows:
        observed[status] += 1

    text = DOC.read_text(encoding="utf-8")
    alternation = "|".join(STATUSES)
    summary = {
        match.group(1): int(match.group(2))
        for match in re.finditer(
            rf"^\|\s*({alternation})\s*\|\s*(\d+)\s*\|", text, re.M
        )
    }
    assert summary, "the summary table is missing"
    for status, count in summary.items():
        assert observed[status] == count, (
            f"summary says {status}={count} but the rows show {observed[status]}"
        )
    assert sum(summary.values()) == len(REQUIRED_IDS)


def test_the_blockers_section_names_the_unfinished_requirements():
    """The blockers must agree with the matrix rather than being prose beside it."""
    text = DOC.read_text(encoding="utf-8")
    blocking = [
        requirement
        for requirement, status in _matrix_rows()
        if status
        in (
            "MISSING",
            "IMPLEMENTED_AWAITING_CREDENTIALLED_SHADOW_VALIDATION",
            "IMPLEMENTED_AWAITING_DEPLOYMENT_APPROVAL",
        )
    ]
    assert blocking, "expected unfinished requirements to be explained"
    tail = text.split("### What blocks READY_TO_FREEZE")[-1]
    for requirement in blocking:
        assert requirement in tail, (
            f"{requirement} is unfinished but not explained in the blockers section"
        )
