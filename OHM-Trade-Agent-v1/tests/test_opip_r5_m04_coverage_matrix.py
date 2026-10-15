"""Mission 04 coverage-matrix acceptance tests: does the audit still tell the truth?

Increment: ``ATDD-R5-M04-learning-committee-audit``.

These tests audit an audit. They re-derive every claim it makes from the repository:
that each cited path exists, that each cited test node is still present, that the
declared totals recompute from the rows, and that the matrix claims no authority it does
not have.

They implement nothing. A failure here means the matrix has gone stale or overclaimed -
never that production behaviour is wrong.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
MATRIX = APP_ROOT / "docs" / "atdd" / "evidence" / "R5_M04_LEARNING_COMMITTEE_MATRIX.md"

INCREMENT = "ATDD-R5-M04-learning-committee-audit"

pytestmark = pytest.mark.acceptance

ROW_KEYS = ("workstream", "class", "contract", "evidence")

CLASSES = frozenset({"PROVEN", "PARTIAL", "OWNER_GATED", "DECLARED_NOT_MEASURED"})

WORKSTREAMS = frozenset({"A", "B", "C", "D", "E"})

CONTRACTS = frozenset({"SC-10", "SC-12"})

FOLLOWER_INCREMENTS = ("ATDD-R5-M04-ai-economics-evidence",)


def _text() -> str:
    return MATRIX.read_text(encoding="utf-8")


def _items() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in _text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("ITEM: "):
            continue
        cells = [cell.strip() for cell in stripped[len("ITEM: ") :].split("|")]
        record = {"id": cells[0], "item": cells[1]}
        for cell in cells[2:]:
            key, _, value = cell.partition("=")
            record[key.strip()] = value.strip()
        records.append(record)
    return records


def _negatives() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in _text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("NEG: "):
            continue
        cells = [cell.strip() for cell in stripped[len("NEG: ") :].split("|")]
        record = {"id": cells[0], "item": cells[1]}
        for cell in cells[2:]:
            key, _, value = cell.partition("=")
            record[key.strip()] = value.strip()
        records.append(record)
    return records


def _block(header: str) -> dict[str, int]:
    values: dict[str, int] = {}
    inside = False
    for line in _text().splitlines():
        if line.strip() == header:
            inside = True
            continue
        if inside:
            if line.strip() == "```" or (line and not line.startswith(" ")):
                break
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                values[parts[0]] = int(parts[1])
    return values


def _followers() -> list[tuple[str, str]]:
    followers: list[tuple[str, str]] = []
    for line in _text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("FOLLOWER: "):
            continue
        head, _, closes = stripped[len("FOLLOWER: ") :].partition("| closes ")
        if head.strip().startswith("NONE"):
            continue
        followers.append((head.strip(), closes.split("|")[0].strip()))
    return followers


def _flat() -> str:
    """The matrix's prose with all whitespace collapsed, so wrapping cannot break a check."""

    return " ".join(_text().split())


# ---------------------------------------------------------------------------
# AC-400 the matrix is well formed and its totals recompute
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_400_every_row_uses_the_declared_vocabulary():
    """ATDD-R5-M04-learning-committee-audit/AC-400: the matrix speaks its own language.

    GIVEN the recorded coverage matrix
    WHEN every item and negative row is parsed and every field compared with the
    declared vocabulary
    THEN each row carries the declared keys, every class and workstream value is one the
    document declares, every contract is one it cites, and identifiers are unique
    """

    items = _items()
    negatives = _negatives()
    assert items and negatives, "the matrix must record both coverage and negative rows"

    for record in items:
        assert set(record) == {"id", "item"} | set(ROW_KEYS), record
        assert record["class"] in CLASSES, record
        assert record["workstream"] in WORKSTREAMS, record
        assert record["contract"] in CONTRACTS, record
        assert re.fullmatch(r"[A-E]\d+", record["id"]), record

    for record in negatives:
        assert set(record) == {"id", "item", "class", "evidence"}, record
        assert record["class"] in CLASSES, record
        assert re.fullmatch(r"N\d+", record["id"]), record

    assert len({record["id"] for record in items}) == len(items)
    assert len({record["id"] for record in negatives}) == len(negatives)


@pytest.mark.acceptance
def test_ac_400_the_declared_totals_recompute_from_the_rows():
    """ATDD-R5-M04-learning-committee-audit/AC-400: the document cannot miscount itself.

    GIVEN the matrix's declared totals block and its rows
    WHEN the totals are recomputed
    THEN the item, negative and row counts, every class count and every workstream count
    equal the declared values, and every class is used by at least one row
    """

    declared = _block("TOTALS:")
    items = _items()
    negatives = _negatives()
    rows = items + negatives

    assert declared, "the matrix must declare its totals"
    assert declared["ITEMS"] == len(items)
    assert declared["NEGATIVE"] == len(negatives)
    assert declared["ROWS"] == len(rows)

    for name in CLASSES:
        assert declared[name] == sum(1 for row in rows if row["class"] == name), name
        assert any(row["class"] == name for row in rows), name

    for name in sorted(WORKSTREAMS):
        assert declared[f"WORKSTREAM_{name}"] == sum(
            1 for row in items if row["workstream"] == name
        ), name

    assert sum(declared[name] for name in CLASSES) == declared["ROWS"]


# ---------------------------------------------------------------------------
# AC-401 every citation resolves
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_401_every_cited_path_and_node_resolves():
    """ATDD-R5-M04-learning-committee-audit/AC-401: a citation that does not resolve is a defect.

    GIVEN the evidence path and the named node every row cites
    WHEN each path is resolved against the repository root and each node looked for in
    the file it is attributed to
    THEN every path exists and every node is present, so a renamed or deleted test breaks
    this document rather than silently weakening it
    """

    cited_nodes = 0
    for record in _items() + _negatives():
        path, _, node = record["evidence"].partition("::")
        cited = REPO_ROOT / path
        assert cited.exists(), (record["id"], path)
        if not node:
            # A module citation is allowed only where the item is not claimed as proven.
            assert record["class"] != "PROVEN", record
            continue
        cited_nodes += 1
        source = cited.read_text(encoding="utf-8")
        assert f"def {node}(" in source, (record["id"], node, path)

    assert cited_nodes >= 50, cited_nodes


@pytest.mark.acceptance
def test_ac_401_no_proven_item_rests_on_a_module_citation_alone():
    """ATDD-R5-M04-learning-committee-audit/AC-401: promotion requires a test.

    GIVEN every row and its declared class
    WHEN the evidence form is compared with the class
    THEN every `PROVEN` row cites a named test node, and the three owner-gated items cite
    the module or page that would have to change
    """

    for record in _items() + _negatives():
        if record["class"] == "PROVEN":
            assert "::" in record["evidence"], record

    gated = [row for row in _items() if row["class"] == "OWNER_GATED"]
    assert sorted(row["id"] for row in gated) == ["D3", "D7", "E1"], gated


# ---------------------------------------------------------------------------
# AC-402 the follower closes exactly the partial rows
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_402_every_follower_closes_named_rows_and_no_owner_gated_row():
    """ATDD-R5-M04-learning-committee-audit/AC-402: no partial row is orphaned.

    GIVEN the declared follower increments and the rows that are `PARTIAL`
    WHEN the closed sets are compared with the partial set
    THEN every partial row is named by exactly one follower, no follower claims a
    proven, declared or owner-gated row, and the follower identifiers are the declared
    ones in order
    """

    rows = {row["id"]: row for row in _items() + _negatives()}
    partial = {
        key for key, row in rows.items() if row["class"] == "PARTIAL"
    }
    not_closable = {
        key for key, row in rows.items() if row["class"] != "PARTIAL"
    }
    assert partial, "the matrix must record its partial rows"

    followers = _followers()
    assert [head for head, _ in followers] == list(FOLLOWER_INCREMENTS)

    closed: list[str] = []
    for _, closes in followers:
        closed.extend(part.strip() for part in closes.split(","))
    assert sorted(closed) == sorted(partial)
    assert len(closed) == len(set(closed))
    assert not (set(closed) & not_closable), closed


# ---------------------------------------------------------------------------
# AC-403 the audit claims no authority it does not have
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_403_the_matrix_claims_no_ai_value_and_no_authority():
    """ATDD-R5-M04-learning-committee-audit/AC-403: an audit may not promote itself.

    GIVEN the matrix's own prose
    WHEN its refusal section is read
    THEN it states that no AI net value is claimed, that development spend is not
    conflated with Committee operating expense, and that the Committee has no authority
    """

    text = _flat()
    assert "does not say AI net value is favourable" in text
    assert "INSUFFICIENT_EVIDENCE" in text
    assert "engineering-development spend with Committee trading-operating expense" in text
    assert "does not say the Committee has any authority" in text


@pytest.mark.acceptance
def test_ac_403_the_frozen_ownership_and_read_plane_are_declared_unchanged():
    """ATDD-R5-M04-learning-committee-audit/AC-403: recording is not changing.

    GIVEN the matrix's scope statement and the read-plane reason it cites
    WHEN they are inspected against the application
    THEN the frozen F6/F9/F12 ownership is declared, the read plane still withholds AI
    cost for the declared reason, and the audit states it created no producer,
    publication boundary or store
    """

    text = _flat()
    assert "F6** statistical forecast ownership" in text
    assert "F9** learning ownership" in text
    assert "F12** advisory-only status" in text
    assert "No producer, registry, store, scheduler or publication boundary was created" in text

    semantic = (APP_ROOT / "app" / "opip" / "cockpit" / "semantic.py").read_text(
        encoding="utf-8"
    )
    assert 'REASON_NO_CANONICAL_AI_COST = "NO_CANONICAL_AI_COST_EVIDENCE"' in semantic
    assert not (APP_ROOT / "app" / "opip" / "cockpit" / "ai_cost.py").exists()

