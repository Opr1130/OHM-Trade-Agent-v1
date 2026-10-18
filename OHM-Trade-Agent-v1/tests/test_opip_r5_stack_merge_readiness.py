"""R5 stacked-chain merge readiness: the record stays honest and consistent.

Increment: `ATDD-R5-STACK-MERGE-READINESS`. The authority for what each assertion
means is `docs/atdd/scope-contracts/ATDD-R5-STACK-MERGE-READINESS.md`, and the
record under test is `docs/atdd/evidence/R5-STACK-MERGE-READINESS-369-373.md`.

These tests verify evidence, not behaviour. They keep the readiness record honest
in four ways: the chain it records must be dependency-aware (`base(n)` is exactly
`head(n-1)`), every in-scope pull request must record all five required contexts
from a closed disposition vocabulary with no unearned "ready", the sections must
agree with each other (a row may claim gate evidence only when every required
context passed, and the verdict must follow), and the document's factual anchors
must still be present in the repository artifacts they came from (the release
workflow's job names and the bridge contract's required-check classification).

They also pin the two findings that make this chain not-yet-landable: three heads
have never run the required gates because the widened trigger reached them only
from #372 onward, and landing a pull request into its base branch invalidates the
`atdd scope` gate of the pull request below it. Nothing here merges, re-points,
deploys or changes a gate.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-STACK-MERGE-READINESS"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
EVIDENCE = APP_ROOT / "docs" / "atdd" / "evidence" / "R5-STACK-MERGE-READINESS-369-373.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest.yml"
BRIDGE = APP_ROOT / "docs" / "engineering" / "LOCAL_AGENT_BRIDGE_V1.md"

IN_SCOPE = (369, 370, 371, 372, 373)
GATE_ROWS = IN_SCOPE + (374,)
CHAIN = tuple(range(362, 374))
REQUIRED_CONTEXTS = (
    "test",
    "atdd scope",
    "release architecture gate",
    "release security gate (Bandit)",
    "semgrep/ci",
)
RELEASE_WORKFLOW_CONTEXTS = (
    "atdd scope",
    "release architecture gate",
    "release security gate (Bandit)",
    "non-deploying main release candidate",
)
DISPOSITIONS = ("SUCCESS", "FAILURE", "NOT RUN")
READINESS_CELLS = ("NO", "YES (gate evidence)")
CIRCLE_DESCRIPTION = "No configuration was found in your project"
MAIN_SHA = "54ea31b4a86128858db2e73b799a4ad6dd8629e3"

_CHAIN_ROW = re.compile(
    r"^\| #(?P<number>\d+) \| `(?P<base>[^`]+)` \| `(?P<head>[^`]+)` \| "
    r"`(?P<sha>[0-9a-f]{10})` \| (?P<state>[^|]+) \|$"
)
_GATE_ROW = re.compile(r"^\| #(?P<number>\d+) \| (?P<cells>.+) \|$")
_VERDICT_ROW = re.compile(r"^\| #(?P<number>\d+) \| (?P<cells>.+) \|$")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace so a phrase check does not depend on line wrapping."""
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.index(marker)
    rest = text[start + len(marker) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _chain_rows(text: str) -> list[tuple[int, str, str, str]]:
    rows: list[tuple[int, str, str, str]] = []
    for line in _section(text, "2. The chain and its dependency-aware order").splitlines():
        match = _CHAIN_ROW.match(line.strip())
        if match is None:
            continue
        rows.append(
            (
                int(match.group("number")),
                match.group("base"),
                match.group("head"),
                match.group("sha"),
            )
        )
    return rows


def _gate_rows(text: str) -> dict[int, dict[str, str]]:
    rows: dict[int, dict[str, str]] = {}
    body = _section(text, "4. Gate evidence at each head (what actually ran)")
    for line in body.splitlines():
        match = _GATE_ROW.match(line.strip())
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group("cells").split("|")]
        if len(cells) != 7:
            continue
        rows[int(match.group("number"))] = {
            "head_sha": cells[0].strip("`"),
            "test": cells[1],
            "atdd scope": cells[2],
            "release architecture gate": cells[3],
            "release security gate (Bandit)": cells[4],
            "semgrep/ci": cells[5],
            "readiness": cells[6],
        }
    return rows


def _verdict_rows(text: str) -> dict[int, str]:
    rows: dict[int, str] = {}
    body = _section(text, "8. Verdict")
    for line in body.splitlines():
        match = _VERDICT_ROW.match(line.strip())
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group("cells").split("|")]
        if len(cells) != 3:
            continue
        rows[int(match.group("number"))] = cells[2]
    return rows


def _implementation_map(contract: str) -> tuple[str, ...]:
    lines = contract.split("IMPLEMENTATION MAP:")[1].split("DEFERRED DISCOVERIES:")[0]
    return tuple(
        line.split("->", 1)[1].strip() for line in lines.splitlines() if "->" in line
    )


@pytest.mark.acceptance
def test_ac_001_recorded_chain_is_dependency_aware():
    """ATDD-R5-STACK-MERGE-READINESS/AC-001: the chain is recorded in order.

    GIVEN the stacked pull-request chain and its base/head construction
    WHEN the recorded chain is inspected
    THEN one row exists per pull request in ascending order with its base branch,
    head branch and head SHA, `base(n)` is exactly `head(n-1)`, the first base is
    `main`, and the record states where a merge lands
    """
    text = _read(EVIDENCE)
    rows = _chain_rows(text)
    assert [row[0] for row in rows] == list(CHAIN)
    assert rows[0][1] == "main"
    for previous, current in zip(rows, rows[1:]):
        assert current[1] == previous[2], (previous[0], current[0])
    for _number, _base, head, sha in rows:
        assert head.startswith("feature/"), head
        assert re.fullmatch(r"[0-9a-f]{10}", sha), sha
    assert "lands its commits into the head branch of pull request `n-1`" in _flat(text)


@pytest.mark.acceptance
def test_ac_002_gate_evidence_matches_the_workflow_and_the_verdicts():
    """ATDD-R5-STACK-MERGE-READINESS/AC-002: evidence, vocabulary and verdict agree.

    GIVEN the required gate set and the recorded gate evidence per head
    WHEN each in-scope pull request's evidence is read
    THEN it records all five required contexts from the closed disposition
    vocabulary, the release-workflow context names are the ones the workflow
    defines, a row is gate-ready only when every required context passed, the
    verdict agrees with that, and no gate or authority is changed
    """
    text = _read(EVIDENCE)
    workflow = _read(WORKFLOW)
    assert "\n  test:" in workflow
    for context in RELEASE_WORKFLOW_CONTEXTS:
        assert f"name: {context}" in workflow, context

    rows = _gate_rows(text)
    assert sorted(rows) == list(GATE_ROWS)
    verdicts = _verdict_rows(text)
    assert sorted(verdicts) == list(GATE_ROWS)

    for number in GATE_ROWS:
        row = rows[number]
        dispositions = [row[context] for context in REQUIRED_CONTEXTS]
        for disposition in dispositions:
            assert disposition in DISPOSITIONS, (number, disposition)
        assert row["readiness"] in READINESS_CELLS, row["readiness"]
        all_passed = all(disposition == "SUCCESS" for disposition in dispositions)
        assert (row["readiness"] == "YES (gate evidence)") is all_passed, number
        if all_passed:
            assert "gate-ready" in verdicts[number], number
        else:
            assert verdicts[number].startswith("NOT READY"), number

    for path in _implementation_map(_read(CONTRACT)):
        assert not path.startswith(
            (
                "OHM-Trade-Agent-v1/app/",
                "OHM-Trade-Agent-v1/.github/",
                "OHM-Trade-Agent-v1/deploy/",
            )
        ), path


@pytest.mark.acceptance
def test_ac_003_missing_gate_evidence_is_recorded_not_assumed():
    """ATDD-R5-STACK-MERGE-READINESS/AC-003: three heads never ran the gates.

    GIVEN the upstream dependency that decides when the release gates start
    WHEN the three heads without gate evidence are inspected
    THEN the record shows their heads still carry the `branches: [main]` trigger
    while the widened trigger exists only from #372's head, so their readiness is
    unproven rather than proven
    """
    text = _read(EVIDENCE)
    trigger_lines = [
        line.strip()
        for line in text.splitlines()
        if "pytest.yml trigger:" in line and "branches: [main" in line
    ]
    assert len(trigger_lines) == 5, trigger_lines
    narrow = [line for line in trigger_lines if "feature/**" not in line]
    widened = [line for line in trigger_lines if "feature/**" in line]
    assert len(narrow) == 3 and len(widened) == 2
    for branch in (
        "feature/opip-r5-e-f11-independent-protection",
        "feature/opip-r5-f-prospective-outcome-integration",
        "feature/opip-r6-committee-shadow-review-readiness",
    ):
        assert any(line.startswith(branch) for line in narrow), branch
    for branch in (
        "feature/opip-release-pipeline-stack-gates",
        "feature/opip-atdd-ml-dependency-policy-v1",
    ):
        assert any(line.startswith(branch) for line in widened), branch
    assert "have never run the required gates" in _flat(text)
    assert "unproven rather than proven" in _flat(text)



@pytest.mark.acceptance
def test_ac_004_circleci_is_recorded_as_not_required():
    """ATDD-R5-STACK-MERGE-READINESS/AC-004: CircleCI is pre-existing and not required.

    GIVEN the CircleCI status and the other non-required checks
    WHEN they are classified
    THEN the exact external failure description, its occurrence on all five heads
    and on `main`, its pre-existing classification and the bridge contract's own
    not-required rule are all recorded
    """
    text = _read(EVIDENCE)
    assert text.count(CIRCLE_DESCRIPTION) >= 6
    assert MAIN_SHA in text
    assert "pre-existing" in text
    assert "deliberately not required" in text
    bridge = _read(BRIDGE)
    assert "unrelated failures such as `CircleCI Pipeline`" in bridge
    assert "are deliberately not required" in bridge
    for context in ("`CodeRabbit`", "`Gitar`", "`claude`", "`dispatch`"):
        assert context in text, context


@pytest.mark.acceptance
def test_ac_005_pointer_merge_order_interaction_is_recorded():
    """ATDD-R5-STACK-MERGE-READINESS/AC-005: the merge mechanics are reported.

    GIVEN the movable active-increment pointer and the merge mechanics of a
    linear stack
    WHEN the merge-order interaction is reported
    THEN the reproduction, the passing baseline and both `SCOPE_CHANGE_REQUIRED`
    outcomes are recorded, the failure is stated to be a false scope violation
    caused by merge mechanics, the decision stays owner-gated, and the record
    asserts no merge, re-point or deployment authority
    """
    text = _read(EVIDENCE)
    assert "Authority granted by this document: `NONE`" in text
    assert text.count("SCOPE_CHANGE_REQUIRED (exit 2)") >= 2
    interaction = _section(
        text,
        "7. Upstream dependency: merge order interacts with the movable ATDD pointer",
    )
    assert "PASS   (exit 0)" in interaction
    assert "false scope violation" in interaction
    assert "owner-gated" in interaction
    assert "No merge, no squash, no rebase, no force-push" in text
    assert "git merge-base --is-ancestor" in text


@pytest.mark.acceptance
def test_ac_006_regression_evidence_is_per_head():
    """ATDD-R5-STACK-MERGE-READINESS/AC-006: regression evidence is per head.

    GIVEN the regression evidence at each head
    WHEN the suite results are compared
    THEN the red base head and the green head are recorded with their exact
    counts and run identifiers, the three heads where no suite ran are recorded
    as absent evidence, and no green result is transferred to them
    """
    text = _read(EVIDENCE)
    assert "1 failed, 8157 passed, 9 skipped, 6 warnings in 408.93s" in text
    assert "8172 passed, 9 skipped" in text
    assert "38413239394" in text and "38422747017" in text
    section = _section(text, "5. Regression evidence")
    assert "**absent**, not green" in section
    assert "no suite has ever run at those heads" in section
    assert "Nothing may be inferred from the green result at #373" in section
    assert "ATDD-ML-DEPENDENCY-POLICY-v1" in section

