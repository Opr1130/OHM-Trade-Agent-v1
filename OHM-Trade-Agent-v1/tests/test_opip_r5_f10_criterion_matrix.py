"""F10 criterion matrix acceptance tests: the traceability binding, asserted.

Increment: ``ATDD-R5-F10-traceability-matrix``.

These tests prove the *documentation* slice only: that the matrix records exactly the criteria
the census plans for the F10 page increment, binds each one to the census requirement that
defines it, names the page the frozen registry presents it on, carries a governed governance
status and functional disposition, and references evidence that exists. They certify nothing:
the parity predicate stays ``NOT_VERIFIED`` with zero verified pages.

The matrix deliberately does not restate census wording, so these tests read the meaning from
the census and fail closed if the document ever starts carrying a re-worded copy of it.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

from app.opip.cockpit import semantic

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)

INCREMENT = "ATDD-R5-F10-traceability-matrix"
CENSUS_OWNER_INCREMENT = "ATDD-R5-F10-dashboard-parity-implementation"

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"
MATRIX = APP_ROOT / "docs" / "atdd" / "evidence" / "F10_CRITERION_MATRIX.md"
CENSUS = APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
PARITY_SPEC = APP_ROOT / "docs" / "architecture" / "OPIP_F10_DASHBOARD_PARITY_SPEC.md"

GOVERNANCE = frozenset({"DEFERRED_UNCLAIMED", "OWNER_RESERVED"})
DISPOSITIONS = frozenset(
    {
        "IMPLEMENTED_AND_VERIFIED",
        "IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE",
        "BLOCKED_BY_UPSTREAM_EVIDENCE",
        "BLOCKED_BY_OWNER_DECISION",
        "NOT_IMPLEMENTED_NEXT_INCREMENT",
    }
)
OWNER_RESERVED_CRITERIA = frozenset({"AC-010", "AC-101"})
NO_PANEL = "NO_PANEL"
COLUMNS = (
    "ac",
    "requirement",
    "page",
    "gap",
    "governance",
    "disposition",
    "owner_increment",
    "evidence",
)

pytestmark = pytest.mark.acceptance


def _matrix_text() -> str:
    return MATRIX.read_text(encoding="utf-8")


def _rows() -> tuple[dict[str, str], ...]:
    """Every matrix row, as a mapping over the declared columns."""
    text = _matrix_text()
    start = text.index("## 4. The matrix")
    block = text[text.index("```text", start) + len("```text") :]
    block = block[: block.index("```")]
    rows: list[dict[str, str]] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split("|")]
        assert len(fields) == len(COLUMNS), line
        rows.append(dict(zip(COLUMNS, fields)))
    return tuple(rows)


def _totals() -> dict[str, str]:
    """The matrix's own totals block."""
    text = _matrix_text()
    start = text.index("## 5. Totals")
    block = text[text.index("```text", start) + len("```text") :]
    block = block[: block.index("```")]
    totals: dict[str, str] = {}
    for raw in block.splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        totals[key.strip()] = value.strip()
    return totals


def _census_rows() -> dict[str, dict[str, str]]:
    """Every requirement row the census records, keyed by requirement identifier."""
    rows: dict[str, dict[str, str]] = {}
    for line in CENSUS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| F10-"):
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 12:
            continue
        atdd = fields[9]
        criterion = re.findall(r"/(AC-\d{3})", atdd)
        increment = re.findall(r"PLANNED:([A-Za-z0-9][A-Za-z0-9._-]*)/AC-\d{3}", atdd)
        rows[fields[1]] = {
            "requirement": fields[1],
            "current_implementation": fields[3],
            "missing_capability": fields[4],
            "evidence_cell": fields[10],
            "status": fields[11],
            "criterion": criterion[0] if criterion else "",
            "increment": increment[0] if increment else "",
        }
    return rows


def _registered_gaps() -> frozenset[str]:
    """The gap identifiers the census registers."""
    gaps = set()
    for line in CENSUS.read_text(encoding="utf-8").splitlines():
        if line.startswith("GAP: "):
            gaps.add(line.split("|")[0][len("GAP: ") :].strip())
    return frozenset(gaps)


def _planned() -> frozenset[str]:
    """Every criterion the census plans for the F10 page increment."""
    return frozenset(
        re.findall(
            r"PLANNED:" + re.escape(CENSUS_OWNER_INCREMENT) + r"/(AC-\d{3})",
            CENSUS.read_text(encoding="utf-8"),
        )
    )


def _panel_index() -> dict[str, dict[str, str]]:
    """For each criterion, the panel the frozen registry declares for it."""
    index: dict[str, dict[str, str]] = {}
    for page in semantic.PAGE_REGISTRY:
        for panel_id in page.panels:
            binding = semantic.PANEL_BY_ID[panel_id]
            index[binding.criterion.rsplit("/", 1)[-1]] = {
                "panel_id": panel_id,
                "page_id": page.page_id,
                "state": binding.state.value,
                "gap": binding.gap_id or "-",
            }
    return index


# --------------------------------------------------------------------------- #
# AC-001: the matrix covers exactly the planned criteria and binds each to its requirement
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_001_the_matrix_covers_exactly_the_planned_criteria_and_binds_each_to_its_requirement():
    """ATDD-R5-F10-traceability-matrix/AC-001: every criterion once, bound to its authority.

    GIVEN the frozen source census with the criteria it plans for the F10 page increment, and
    the criterion matrix this increment adds
    WHEN the rows are compared with the planned set and with the census requirement each names
    THEN every planned criterion appears exactly once, nothing else appears, each row's
    requirement is the census row whose ATDD cell names that criterion, every row's owner is the
    increment that census cell assigns, and the document's own totals agree with its rows
    """
    census = _census_rows()
    planned = _planned()
    assert len(planned) == 54, sorted(planned)

    rows = _rows()
    recorded = [row["ac"] for row in rows]
    assert len(recorded) == len(set(recorded)), "the matrix records a criterion twice"
    assert set(recorded) == set(planned), sorted(set(recorded) ^ set(planned))
    assert len(recorded) == 54

    for row in rows:
        entry = census.get(row["requirement"])
        assert entry is not None, row["requirement"]
        assert entry["criterion"] == row["ac"], (row["requirement"], row["ac"])
        assert entry["increment"] == row["owner_increment"], row["ac"]
        assert row["owner_increment"] == CENSUS_OWNER_INCREMENT, row["ac"]

    totals = _totals()
    assert totals["CRITERIA_RECORDED"] == str(len(rows))
    for disposition in sorted(DISPOSITIONS):
        counted = sum(1 for row in rows if row["disposition"] == disposition)
        assert totals[f"DISPOSITION_{disposition}"] == str(counted), disposition
    for governance in sorted(GOVERNANCE):
        counted = sum(1 for row in rows if row["governance"] == governance)
        assert totals[f"GOVERNANCE_{governance}"] == str(counted), governance
    assert sum(
        int(totals[f"DISPOSITION_{name}"]) for name in sorted(DISPOSITIONS)
    ) == len(rows)


# --------------------------------------------------------------------------- #
# AC-002: each planned criterion names the page the registry serves it on
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_002_each_planned_criterion_names_the_page_the_registry_serves_it_on():
    """ATDD-R5-F10-traceability-matrix/AC-002: the page column is derived, not asserted.

    GIVEN the matrix's page column and the frozen page and panel registries
    WHEN each row is compared with the page that serves the criterion's panel
    THEN a panel-backed criterion names exactly that page, a criterion no panel declares says so
    instead of naming a page, and every frozen page is represented by at least one criterion
    """
    panels = _panel_index()
    rows = _rows()

    named_pages = set()
    for row in rows:
        declared = panels.get(row["ac"])
        if declared is None:
            assert row["page"] == NO_PANEL, row["ac"]
            continue
        assert row["page"] == declared["page_id"], row["ac"]
        named_pages.add(row["page"])

    assert named_pages == set(semantic.PAGE_IDS), sorted(
        set(semantic.PAGE_IDS) - named_pages
    )
    assert len(named_pages) == 8
    for panel in semantic.PANEL_BY_ID.values():
        assert panel.criterion.rsplit("/", 1)[-1] in {row["ac"] for row in rows}


# --------------------------------------------------------------------------- #
# AC-003: every disposition, owner and evidence reference is governed and resolves
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_003_every_disposition_owner_and_evidence_reference_is_governed_and_resolves():
    """ATDD-R5-F10-traceability-matrix/AC-003: derived facts and governed judgments.

    GIVEN the matrix's gap, governance, disposition, owner and evidence columns, the governed
    vocabularies and the repository
    WHEN every row is read
    THEN the gap column equals what the registry or the census records, the governance and
    disposition values are governed, the owner is a declared contract increment, the evidence
    reference exists or is explicitly absent, and no panel the service declares absent is
    recorded as implemented
    """
    census = _census_rows()
    panels = _panel_index()
    gaps = _registered_gaps()
    assert gaps
    declared_increments = {item.increment for item in load_scope_contracts(CONTRACT_DIR)}

    rows = _rows()
    blocked_states = {
        semantic.FactState.BLOCKED_BY_UPSTREAM_EVIDENCE.value,
        semantic.FactState.ABSENT.value,
    }
    served_states = {
        semantic.FactState.SERVED.value,
        semantic.FactState.SERVED_PARTIAL.value,
    }
    for row in rows:
        declared = panels.get(row["ac"])
        if declared is None:
            expected_gap = (
                census[row["requirement"]]["evidence_cell"]
                if census[row["requirement"]]["evidence_cell"] in gaps
                else "-"
            )
        else:
            expected_gap = declared["gap"]
            if declared["state"] in blocked_states:
                assert row["disposition"] == "BLOCKED_BY_UPSTREAM_EVIDENCE", row["ac"]
            elif declared["state"] in served_states:
                assert row["disposition"] == "IMPLEMENTED_INSUFFICIENT_PARITY_EVIDENCE", (
                    row["ac"]
                )
            else:
                raise AssertionError(f"unclassified panel state: {declared['state']}")
        assert row["gap"] == expected_gap, (row["ac"], row["gap"], expected_gap)

        assert row["governance"] in GOVERNANCE, row["ac"]
        assert row["disposition"] in DISPOSITIONS, row["ac"]
        assert row["owner_increment"] in declared_increments, row["ac"]

        if row["evidence"] == "NONE":
            assert row["disposition"] == "NOT_IMPLEMENTED_NEXT_INCREMENT", row["ac"]
        else:
            assert (REPO_ROOT / row["evidence"]).is_file(), row["evidence"]

    reserved = {row["ac"] for row in rows if row["governance"] == "OWNER_RESERVED"}
    assert reserved == set(OWNER_RESERVED_CRITERIA), sorted(reserved)
    for row in rows:
        if row["ac"] in OWNER_RESERVED_CRITERIA:
            assert row["disposition"] == "BLOCKED_BY_OWNER_DECISION", row["ac"]
        else:
            assert row["governance"] == "DEFERRED_UNCLAIMED", row["ac"]

    # A row may not record a satisfied state for a fact family the census classifies as absent
    # from this plane.
    for row in rows:
        if census[row["requirement"]]["status"] == "BLOCKED_BY_UPSTREAM_EVIDENCE":
            assert row["disposition"] != "IMPLEMENTED_AND_VERIFIED", row["ac"]


# --------------------------------------------------------------------------- #
# AC-004: the matrix restates no census text and claims no parity
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_004_the_matrix_restates_no_census_text_and_claims_no_parity():
    """ATDD-R5-F10-traceability-matrix/AC-004: a reference, never a re-wording.

    GIVEN the matrix, the frozen census and the service's parity posture
    WHEN the matrix's text is compared with the census's own wording and the parity constants
    THEN no census meaning or current-implementation text is copied into the matrix, the document
    states that it certifies nothing, the two out-of-matrix increments keep their own criteria,
    and the parity, authority, reconciliation and threshold posture is unchanged
    """
    text = _matrix_text()
    census = _census_rows()
    assert census

    for entry in census.values():
        for field in ("missing_capability", "current_implementation"):
            value = entry[field]
            if len(value) < 24:
                continue
            assert value not in text, (entry["requirement"], field)

    for required in (
        "Authority granted by this document: `NONE`",
        "NOT_VERIFIED",
        "does not claim a page, a panel or a criterion",
        "OWNER_DECISION_REQUIRED",
        "ATDD-R5-F10-F",
        "ATDD-R5-F10-G",
    ):
        assert required in text, required

    assert semantic.PARITY_STATUS_NOT_VERIFIED == "NOT_VERIFIED"
    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.AUTHORITY_UNDECIDED == "UNDECIDED"
    assert semantic.COUNTS_AND_PNL_RECONCILIATION == "NOT_RECORDED"
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"
    assert semantic.RATIFIED_THRESHOLDS == ()
    assert "NOT_VERIFIED" in PARITY_SPEC.read_text(encoding="utf-8")

    # The criteria the census plans for the Telegram and certification increments stay theirs.
    census_text = CENSUS.read_text(encoding="utf-8")
    others = set(
        re.findall(r"PLANNED:ATDD-R5-F10-[FG]/(AC-\d{3})", census_text)
    )
    assert others
    recorded = {row["ac"] for row in _rows()}
    assert recorded.isdisjoint(others), sorted(recorded & others)


# --------------------------------------------------------------------------- #
# AC-005: the report, the pointer and the criteria agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_005_the_report_the_pointer_and_the_criteria_agree():
    """ATDD-R5-F10-traceability-matrix/AC-005: recorded, never claimed.

    GIVEN the autonomous build report, this increment's scope contract, the movable pointer and
    the acceptance-test index
    WHEN the report, the contract, the pointer and the index are read together
    THEN the report records this increment with the base head it stacks on, its own contract and
    its own test count, the pointer resolves to a declared contract, every approved criterion is
    traced to the test that cites it and mapped to a path, and the parity row stays OUTSTANDING
    """
    contracts = load_scope_contracts(CONTRACT_DIR)
    declared = {item.increment for item in contracts}
    assert INCREMENT in declared
    assert read_increment_pointer(POINTER) in declared
    assert (CONTRACT_DIR / f"{read_increment_pointer(POINTER)}.md").is_file()
    assert CONTRACT.is_file()
    assert MATRIX.is_file()

    ours = [item for item in contracts if item.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id
        for path in contract.implementation_map[ac_id]:
            assert path.startswith("OHM-Trade-Agent-v1/"), path
            assert not path.startswith("OHM-Trade-Agent-v1/app/"), path

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    cited = set(re.findall(re.escape(INCREMENT) + r"/(AC-\d{3})", source))
    assert cited == set(contract.acceptance_criteria)

    report = BUILD_REPORT.read_text(encoding="utf-8")
    assert INCREMENT in report
    assert f"{INCREMENT}.md" in report
    assert "pull/376" in report
    assert "pull/377" in report
    assert "pull/378" in report
    assert "2979dcfb" in report
    assert f"{source.count('@pytest.mark.acceptance')} acceptance tests" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "| R7 governed retirement | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report

    assert semantic.PARITY_VERIFIED_PAGES == 0
