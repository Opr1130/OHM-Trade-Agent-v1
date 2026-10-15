"""Mission 03 coverage-matrix acceptance tests: does the audit still tell the truth?

Increment: ``ATDD-R5-M03-portfolio-execution-audit``.

These tests audit an audit. They re-derive every claim the matrix makes from the
repository: that each cited evidence path exists, that each cited test node still
exists in the file it is attributed to, that the declared totals recompute from the
rows, and that the matrix claims no authority it does not have.

They implement nothing. A failure here means the matrix has gone stale or has
overclaimed - never that production behaviour is wrong.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
MATRIX = APP_ROOT / "docs" / "atdd" / "evidence" / "R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md"

INCREMENT = "ATDD-R5-M03-portfolio-execution-audit"

pytestmark = pytest.mark.acceptance

ROW_KEYS = ("workstream", "class", "contract", "evidence")

CLASSES = frozenset(
    {
        "PROVEN",
        "UNPROVEN",
        "BLOCKED_BY_OWNER_DECISION",
    }
)

WORKSTREAMS = frozenset({"A", "B", "C"})

CONTRACTS = frozenset(
    {f"SC-{index:02d}" for index in range(6, 14)}
)

FOLLOWER_INCREMENTS = (
    "ATDD-R5-M03-paper-lifecycle-economic-invariants",
)


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
        followers.append((head.strip(), closes.split("|")[0].strip()))
    return followers


# ---------------------------------------------------------------------------
# AC-300 the matrix is well formed and speaks its own vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_300_every_item_uses_the_declared_vocabulary():
    """ATDD-R5-M03-portfolio-execution-audit/AC-300: the matrix speaks its own language.

    GIVEN the recorded coverage matrix
    WHEN every item is parsed and every field is compared with the declared
    vocabulary
    THEN each item carries exactly the declared keys, every class, workstream and
    contract value is one the document declares, and every item identifier is unique
    """

    items = _items()
    assert items, "the matrix must record at least one item"

    for record in items:
        assert set(record) == {"id", "item"} | set(ROW_KEYS), record
        assert record["class"] in CLASSES, record
        assert record["workstream"] in WORKSTREAMS, record
        assert record["contract"] in CONTRACTS, record
        assert re.fullmatch(r"[A-C]\d+", record["id"]), record

    assert len({record["id"] for record in items}) == len(items)


@pytest.mark.acceptance
def test_ac_300_the_declared_totals_recompute_from_the_items():
    """ATDD-R5-M03-portfolio-execution-audit/AC-300: the document cannot miscount itself.

    GIVEN the matrix's declared totals block and its items
    WHEN the totals are recomputed
    THEN the item count, every class count and every workstream count equal the
    declared values
    """

    declared = _block("TOTALS:")
    items = _items()
    assert declared, "the matrix must declare its totals"
    assert declared["ITEMS"] == len(items)

    for name in CLASSES:
        assert declared[name] == sum(1 for record in items if record["class"] == name)
    for name in sorted(WORKSTREAMS):
        key = f"WORKSTREAM_{name}"
        assert declared[key] == sum(
            1 for record in items if record["workstream"] == name
        )
    assert sum(declared[name] for name in CLASSES) == declared["ITEMS"]
    assert (
        declared["WORKSTREAM_A"]
        + declared["WORKSTREAM_B"]
        + declared["WORKSTREAM_C"]
        == declared["ITEMS"]
    )


# ---------------------------------------------------------------------------
# AC-301 every citation resolves: the evidence path exists and the node is there
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_301_every_cited_evidence_path_exists():
    """ATDD-R5-M03-portfolio-execution-audit/AC-301: a citation that does not resolve is a defect.

    GIVEN the evidence path every item cites
    WHEN each is resolved against the repository root
    THEN every path exists, so no item points at a file that was renamed, moved or
    deleted
    """

    for record in _items():
        cited = record["evidence"].partition("::")[0]
        assert (REPO_ROOT / cited).exists(), (record["id"], cited)


@pytest.mark.acceptance
def test_ac_301_every_cited_test_node_still_exists():
    """ATDD-R5-M03-portfolio-execution-audit/AC-301: the node is the item's claim.

    GIVEN the test node each cited evidence entry names
    WHEN the node name is looked for in the file it is attributed to
    THEN every node is present, so the matrix cannot cite a renamed, moved or
    invented acceptance test
    """

    cited_nodes = 0
    for record in _items():
        path, _, node = record["evidence"].partition("::")
        if not node:
            continue
        cited_nodes += 1
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert f"def {node}(" in source, (record["id"], node, path)

    assert cited_nodes >= 35, cited_nodes


@pytest.mark.acceptance
def test_ac_301_a_proven_item_and_a_diminished_item_are_treated_differently():
    """ATDD-R5-M03-portfolio-execution-audit/AC-301: promotion requires a test.

    GIVEN every item and its declared class
    WHEN the classes are inspected against the evidence they cite
    THEN every class is used by at least one item, so the vocabulary has no
    decorative member, and no item is recorded `PROVEN` on a module citation alone
    """

    items = _items()
    for name in CLASSES:
        assert any(record["class"] == name for record in items), name

    for record in items:
        if record["class"] == "PROVEN":
            assert "::" in record["evidence"], record


# ---------------------------------------------------------------------------
# AC-302 the follower increments close exactly the unproven items
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_302_every_follower_closes_named_items_and_covers_every_gap():
    """ATDD-R5-M03-portfolio-execution-audit/AC-302: no gap is orphaned.

    GIVEN the declared follower increments and the items that are not `PROVEN`
    WHEN the closed sets are compared with the gap set
    THEN every non-`PROVEN` item is named by exactly one follower, every closed
    identifier exists, and no follower claims a `PROVEN` item
    """

    items = {record["id"]: record for record in _items()}
    # A blocked item is not closable by a follower increment: it waits on an owner
    # decision, so it is excluded from the gap set a follower must close and is
    # asserted separately below.
    gaps = {
        key
        for key, record in items.items()
        if record["class"] not in {"PROVEN", "BLOCKED_BY_OWNER_DECISION"}
    }
    blocked = {
        key
        for key, record in items.items()
        if record["class"] == "BLOCKED_BY_OWNER_DECISION"
    }
    assert blocked, "the matrix must record its owner-gated item"

    followers = _followers()
    assert [head for head, _ in followers] == list(FOLLOWER_INCREMENTS)

    closed: list[str] = []
    for _, closes in followers:
        closed.extend(part.strip() for part in closes.split(","))
    assert sorted(closed) == sorted(gaps)
    assert len(closed) == len(set(closed))
    for identifier in closed:
        assert identifier in items, identifier
    assert not (set(closed) & blocked), closed


# ---------------------------------------------------------------------------
# AC-303 the audit claims no authority it does not have
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_303_the_matrix_claims_no_activation_and_no_production_proof():
    """ATDD-R5-M03-portfolio-execution-audit/AC-303: an audit may not promote itself.

    GIVEN the matrix's own prose and the items it records
    WHEN its refusal section is read and its workstream is inspected
    THEN it states that the paper runtime is not active, that no item is
    production-proven, and that rotation is owner-gated rather than implemented
    """

    text = _text()
    assert "does not say the paper runtime is active" in text
    assert "`TARGET_PAPER` is `BLOCKED`" in text
    assert "does not say any item is production-proven" in text
    assert "does not say rotation is implemented" in text

    assert "EVIDENCE_SHADOW" in text
    assert items_rotation_is_owner_gated(_items())


def items_rotation_is_owner_gated(items: list[dict[str, str]]) -> bool:
    """Whether the rotation item is recorded as owner-gated, not implemented."""

    rotation = [record for record in items if record["item"] == "rotation"]
    assert len(rotation) == 1, rotation
    return rotation[0]["class"] == "BLOCKED_BY_OWNER_DECISION"


@pytest.mark.acceptance
def test_ac_303_the_matrix_touches_no_application_module():
    """ATDD-R5-M03-portfolio-execution-audit/AC-303: recording is not changing.

    GIVEN the paths the matrix cites as its own evidence base
    WHEN they are inspected
    THEN every application citation is read-only evidence for an existing module,
    and the matrix's own follower section names increments rather than describing
    changes it made
    """

    text = _text()
    app_citations = [
        record for record in _items() if record["evidence"].startswith("OHM-Trade-Agent-v1/app/")
    ]
    assert app_citations, "the matrix must cite the application modules it audits"
    for record in app_citations:
        assert record["class"] in {"UNPROVEN", "BLOCKED_BY_OWNER_DECISION"}, record

    assert "FOLLOWER:" in text
    assert "implements nothing and authorizes" in text

