"""OQ-01 owner-decision acceptance tests: recording a decision is not ratifying policy.

Increment: ``ATDD-R5-M06-owner-decision-oq01``.

These tests prove four things and, just as importantly, one non-thing:

1. The owner-decision register records OD-001 with the two roles and no personal identifier.
2. The F1–F12 and G1–G6 accountability mapping is complete and consistent.
3. `G6`/`PM-02` still require a **separate** exact-release approval (Architecture §19).
4. Nothing beyond OQ-01 is ratified.
5. **The frozen requirements register is unchanged** — its owner cells still read
   `UNASSIGNED`, so this increment recorded a decision without ratifying the register row.

A failure here means a governance claim is unsupported, not that a product behaviour is
wrong.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
DECISIONS = APP_ROOT / "docs" / "architecture" / "OPIP_OWNER_DECISIONS_REGISTER.md"
REGISTER = APP_ROOT / "docs" / "architecture" / "OPIP_R5_REQUIREMENTS_REGISTER.md"

INCREMENT = "ATDD-R5-M06-owner-decision-oq01"

FEATURE_IDS = tuple(f"F{n}" for n in range(1, 13))
GATE_IDS = tuple(f"G{n}" for n in range(1, 7))
NOT_RATIFIED = (
    "OQ-02", "OQ-03", "OQ-04", "OQ-05", "OQ-06", "OQ-07", "OQ-08", "OQ-09", "OQ-10",
    "SC-13", "C17", "PM-02",
)

#: Tokens that would indicate a personal identifier had been invented.
PERSONAL_MARKERS = ("@", "mailto:", "Name =", "name =")


def _text() -> str:
    return DECISIONS.read_text(encoding="utf-8")


def _flat() -> str:
    return " ".join(_text().split())


def _mappings() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for line in _text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("MAPPING: "):
            continue
        cells = [cell.strip() for cell in stripped[len("MAPPING: ") :].split("|")]
        record: dict[str, str] = {}
        for cell in cells[1:]:
            key, _, value = cell.partition("=")
            record[key.strip()] = value.strip()
        found[cells[0]] = record
    return found


def _not_ratified() -> list[str]:
    out: list[str] = []
    for line in _text().splitlines():
        stripped = line.strip()
        if stripped.startswith("NOT_RATIFIED: "):
            out.append(stripped[len("NOT_RATIFIED: ") :].strip())
    return out


# ---------------------------------------------------------------------------
# AC-420 the decision is recorded, by role, without a personal identifier
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_420_od_001_is_recorded_by_role_and_ratifies_no_number():
    """ATDD-R5-M06-owner-decision-oq01/AC-420: a decision names a role, not a person.

    GIVEN the owner-decisions register
    WHEN OD-001 is read
    THEN it carries the confirmed status, both roles, an explicit authority source, an
    accountability-only scope, no ratified number and no personal identifier
    """

    text = _text()
    assert "OD-001" in text
    assert "`OWNER_DECISION_CONFIRMED`" in text
    assert "`BUSINESS_DECISION_OWNER`" in text
    assert "`TECHNICAL_RELEASE_OWNER`" in text
    assert "AUTHORITY_SOURCE" in text
    assert "accountability assignment only" in text
    assert "NUMERIC_POLICY_RATIFIED_BY_THIS_DECISION" in text

    flat = _flat()
    assert "No personal identifier is recorded" in flat
    assert "no independent human approval is claimed" in flat
    for marker in PERSONAL_MARKERS:
        assert marker not in text, marker


# ---------------------------------------------------------------------------
# AC-421 the mapping is complete and consistent
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_421_every_feature_and_gate_is_mapped_to_the_designated_owner():
    """ATDD-R5-M06-owner-decision-oq01/AC-421: no requirement is left unaccountable.

    GIVEN the accountability mapping
    WHEN every feature and gate row is compared with the register's own identifier sets
    THEN F1–F12 and G1–G6 are each mapped exactly once, every mapped owner is `OWNER`,
    and each feature's mapped gate equals the gate the frozen register already assigns
    """

    mappings = _mappings()
    assert set(mappings) == set(FEATURE_IDS) | set(GATE_IDS), sorted(mappings)

    register = REGISTER.read_text(encoding="utf-8")
    for row_id in FEATURE_IDS:
        record = mappings[row_id]
        assert record["owner"] == "OWNER", row_id
        row = next(
            line for line in register.splitlines() if line.startswith(f"| {row_id} |")
        )
        cells = [cell.strip() for cell in row.split("|")]
        # cells[0] is the empty run before the leading pipe, so Owner is 11 and Gate 12.
        assert cells[11] == "UNASSIGNED", (row_id, cells[11])
        assert record["gate"] == cells[12], (row_id, record["gate"], cells[12])

    for row_id in GATE_IDS:
        assert mappings[row_id]["owner"] == "OWNER", row_id


# ---------------------------------------------------------------------------
# AC-422 G6/PM-02 keeps its separate exact-release approval
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_422_only_g6_carries_a_separate_approver_requirement():
    """ATDD-R5-M06-owner-decision-oq01/AC-422: naming an owner is not approving a release.

    GIVEN the mapping's separate-approver flags and the architecture's §19 clause
    WHEN they are compared
    THEN G6 is the only row requiring a separate approver, the register states why, and
    the architecture clause it cites is the one that actually demands it
    """

    mappings = _mappings()
    separate = {
        row_id
        for row_id, record in mappings.items()
        if record["separate_approver"] == "YES"
    }
    assert separate == {"G6"}, sorted(separate)
    for row_id, record in mappings.items():
        assert record["separate_approver"] in {"YES", "NO"}, row_id

    flat = _flat()
    assert "separate exact-release owner approval" in flat
    assert "supplying the exact-release approval" in flat

    architecture = (
        APP_ROOT / "docs" / "architecture" / "v1.5.0" / "ARCHITECTURE.md"
    ).read_text(encoding="utf-8")
    assert "separate exact-release owner approval" in architecture


# ---------------------------------------------------------------------------
# AC-423 nothing beyond OQ-01 is ratified, and the frozen register is untouched
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_423_nothing_beyond_oq_01_is_ratified():
    """ATDD-R5-M06-owner-decision-oq01/AC-423: one decision is one decision.

    GIVEN the register's non-ratification block
    WHEN it is compared with the open inputs the decision must not touch
    THEN exactly OQ-02…OQ-10, SC-13, C17 and PM-02 are declared not ratified, and the
    register states that PASS still depends on the undecided OQ-07 thresholds
    """

    declared = _not_ratified()
    assert sorted(declared) == sorted(NOT_RATIFIED), declared
    assert len(declared) == len(set(declared))
    assert "no gate in the register can pass" in _flat()


@pytest.mark.acceptance
def test_ac_423_the_frozen_requirements_register_is_left_unmodified():
    """ATDD-R5-M06-owner-decision-oq01/AC-423: a decision record may not rewrite a freeze.

    GIVEN the frozen requirements register
    WHEN its feature and gate owner cells are read
    THEN every one still reads `UNASSIGNED`, so the decision was recorded beside the
    freeze rather than ratified into it
    """

    register = REGISTER.read_text(encoding="utf-8")
    rows = [
        line
        for line in register.splitlines()
        if re.match(r"^\| (F[0-9]{1,2}|G[1-6]) \|", line)
    ]
    assert len(rows) == 18, len(rows)
    for row in rows:
        assert "| UNASSIGNED |" in row, row[:90]
        assert "| OWNER |" not in row, row[:90]

    flat = _flat()
    assert "frozen requirements register is deliberately NOT edited" in flat
    assert "it deliberately still reads `UNASSIGNED`" in flat

