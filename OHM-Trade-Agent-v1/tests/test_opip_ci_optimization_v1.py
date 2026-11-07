"""ATDD: O'Pip CI optimization v1 - focused release CI, fail-closed safety suite.

GIVEN the release pipeline spent ~9 minutes of every pull request and every main
push executing the complete 10k-test suite
WHEN automatic CI is inspected after this increment
THEN automatic release CI runs only the tests that reference the changed modules
plus a small mandatory safety suite, while the complete suite and the real-Docker
OOM/fault-injection proofs are manual-only, and the exact-SHA deployment gate
refuses any SHA whose mandatory safety tests were not executed and passed
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.ci_mandatory_safety_receipt import (  # noqa: E402
    PASS,
    ReceiptError,
    verify,
)
from tests.ci_release_selection import (  # noqa: E402
    ALWAYS_FOCUSED,
    MANDATORY_SAFETY,
    MANUAL_ONLY_TARGETS,
    MIN_MANDATORY_NODE_IDS,
    build_index,
    select_focused,
    select_safety,
)

pytestmark = pytest.mark.acceptance

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE_WORKFLOW = WORKFLOWS / "pytest.yml"
FULL_WORKFLOW = WORKFLOWS / "pytest-full-manual.yml"
DEPLOY_WORKFLOW = WORKFLOWS / "deploy-production.yml"

GATE_JOBS = (
    "test",
    "atdd-scope",
    "architecture-gate",
    "security-gate",
    "release-candidate",
)
AUTOMATIC_STEPS = (
    "Focused release selection (changed modules only)",
    "Run focused release selection",
    "Run mandatory safety suite",
    "Verify mandatory safety receipt (fail closed)",
)
REQUIRED_CATEGORIES = (
    "f11_protection",
    "funded_authorization",
    "financial_calculations",
    "portfolio_protection",
    "configuration_validation",
)
DEPLOY_GATE_NAME = (
    "Require successful focused release CI and mandatory safety evidence for "
    "exact SHA"
)
REQUIRED_CI_EVIDENCE = (
    'require_evidence "test|success"',
    'require_evidence "atdd scope|success"',
    'require_evidence "release architecture gate|success"',
    'require_evidence "release security gate (Bandit)|success"',
    'require_evidence "test|Focused release selection (changed modules only)|success"',
    'require_evidence "test|Run focused release selection|success"',
    'require_evidence "test|Run mandatory safety suite|success"',
    'require_evidence "test|Verify mandatory safety receipt (fail closed)|success"',
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _yaml(path: Path) -> dict:
    return yaml.safe_load(_text(path))


def _events(document: dict) -> dict:
    # PyYAML 1.1 reads the unquoted GitHub ``on`` key as Boolean True.
    return document.get("on", document.get(True, {}))


def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _expected_cases() -> list[tuple[str, str, str]]:
    cases: list[tuple[str, str, str]] = []
    for node_id in select_safety():
        path, _, name = node_id.partition("::")
        cases.append((path, name, "passed"))
    return cases


def _junit(cases: list[tuple[str, str, str]]) -> str:
    body = []
    for path, name, outcome in cases:
        child = ""
        if outcome == "failure":
            child = '<failure message="boom"/>'
        elif outcome == "skipped":
            child = '<skipped message="not executed"/>'
        classname = path[: -len(".py")].replace("/", ".")
        body.append(
            f'<testcase classname="{classname}" file="{path}" name="{name}">'
            f"{child}</testcase>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite>'
        + "".join(body)
        + "</testsuite></testsuites>"
    )


def _report(directory: Path, cases: list[tuple[str, str, str]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    report = directory / "mandatory-safety-junit.xml"
    report.write_text(_junit(cases), encoding="utf-8")
    return report


@pytest.mark.acceptance
def test_ac_001_automatic_release_ci_runs_focused_selection_and_mandatory_safety() -> None:
    """ATDD-CI-OPTIMIZATION-M01/AC-001: automatic release CI runs the focused selection and the mandatory safety suite only."""
    document = _yaml(RELEASE_WORKFLOW)
    triggers = _events(document)
    assert set(triggers) == {"pull_request", "push"}
    assert triggers["pull_request"] == {"branches": ["main", "feature/**"]}
    assert triggers["push"] == {"branches": ["main"]}
    assert tuple(document["jobs"]) == GATE_JOBS
    text = _text(RELEASE_WORKFLOW)
    steps = document["jobs"]["test"]["steps"]
    names = [step.get("name", "") for step in steps]
    for required in AUTOMATIC_STEPS:
        assert required in names, required
    runs = [step.get("run", "") for step in steps]
    pytest_runs = [run for run in runs if "python -m pytest" in run]
    assert pytest_runs
    for run in pytest_runs:
        # Every automatic invocation is bounded to explicit targets.
        assert (
            "$(cat focused-targets.txt)" in run
            or "$(cat safety-targets.txt)" in run
            or "tests/" in run
        ), run
    # The historical unconditional full-suite invocation is gone.
    assert not [run for run in runs if run.strip() == "python -m pytest -q"]
    assert "python -m tests.ci_release_selection" in text
    assert '--mode focused --git-base "$base"' in text
    assert "python -m tests.ci_mandatory_safety_receipt" in text
    assert "--report mandatory-safety-junit.xml" in text
    assert "--selection safety-targets.txt" in text
    # No gate may become advisory or skip its targets.
    assert "continue-on-error" not in text
    assert "--deselect" not in text
    assert "--ignore" not in text


@pytest.mark.acceptance
def test_ac_002_full_suite_and_docker_oom_proofs_are_manual_only() -> None:
    """ATDD-CI-OPTIMIZATION-M01/AC-002: the complete suite and the real-Docker OOM proofs are manual-only."""
    document = _yaml(FULL_WORKFLOW)
    triggers = _events(document)
    assert set(triggers) == {"workflow_dispatch"}
    assert document["permissions"] == {"contents": "read"}
    text = _text(FULL_WORKFLOW)
    assert "pull_request" not in text
    assert "\n  push:" not in text
    job = document["jobs"]["full-suite"]
    steps = {step.get("name", ""): step for step in job["steps"]}
    full = steps["Full pytest suite (manual only)"]
    assert "python -m pytest -q" in full["run"]
    assert f"--ignore={MANUAL_ONLY_TARGETS[0]}" in full["run"]
    oom = steps["Real-Docker OOM and fault-injection proofs (manual only)"]
    assert oom["if"] == "inputs.include_docker_fault_injection"
    assert MANUAL_ONLY_TARGETS[0] in oom["run"]
    # The selector never returns manual-only evidence automatically.
    assert MANUAL_ONLY_TARGETS == (
        "tests/test_opip_scan_docker_fault_injection_v1.py",
    )
    automated = select_focused([f"OHM-Trade-Agent-v1/{MANUAL_ONLY_TARGETS[0]}"])
    assert MANUAL_ONLY_TARGETS[0] not in automated
    assert automated == ALWAYS_FOCUSED



@pytest.mark.acceptance
def test_ac_003_mandatory_safety_suite_covers_the_protected_surfaces() -> None:
    """ATDD-CI-OPTIMIZATION-M01/AC-003: the safety suite covers every protected surface and never runs vacuously."""
    assert set(REQUIRED_CATEGORIES) <= set(MANDATORY_SAFETY)
    node_ids = select_safety()
    assert len(node_ids) >= MIN_MANDATORY_NODE_IDS
    assert len(node_ids) == len(set(node_ids))
    for category, targets in MANDATORY_SAFETY.items():
        assert targets, category
        for target in targets:
            assert target.startswith("tests/test_"), target
            assert "::" in target, target
    # Every mandatory node id still resolves to an existing test source.
    for node_id in node_ids:
        path_text, _, test_name = node_id.partition("::")
        path = APP_ROOT / path_text
        assert path.is_file(), node_id
        assert test_name in _defined_tests(path), node_id
    # The focused selection is deterministic, source-preserving and non-vacuous.
    index = build_index()
    changed = ("OHM-Trade-Agent-v1/app/services/protection_health.py",)
    first = select_focused(changed, index)
    assert first == select_focused(changed, index)
    assert first
    assert all((APP_ROOT / target).is_file() for target in first)
    assert select_focused(()) == ALWAYS_FOCUSED
    assert select_focused(("OHM-Trade-Agent-v1/docs/UNRELATED_DOC.md",)) == (
        ALWAYS_FOCUSED
    )


@pytest.mark.acceptance
def test_ac_004_receipt_fails_closed_when_safety_tests_did_not_execute(
    tmp_path: Path,
) -> None:
    """ATDD-CI-OPTIMIZATION-M01/AC-004: a skipped, missing or failed safety test is never certified as a pass."""
    expected = select_safety()
    passing = _expected_cases()
    receipt = verify(_report(tmp_path / "pass", passing))
    assert receipt.node_ids == len(expected)
    assert receipt.executed == len(expected)
    assert PASS in receipt.lines()

    skipped = _expected_cases()
    skipped[0] = (skipped[0][0], skipped[0][1], "skipped")
    with pytest.raises(ReceiptError):
        verify(_report(tmp_path / "skipped", skipped))

    with pytest.raises(ReceiptError):
        verify(_report(tmp_path / "missing", _expected_cases()[1:]))

    failing = _expected_cases()
    failing[-1] = (failing[-1][0], failing[-1][1], "failure")
    with pytest.raises(ReceiptError):
        verify(_report(tmp_path / "failure", failing))

    empty = tmp_path / "empty"
    empty.mkdir()
    empty_report = empty / "mandatory-safety-junit.xml"
    empty_report.write_text("<?xml version='1.0'?><testsuites/>", encoding="utf-8")
    with pytest.raises(ReceiptError):
        verify(empty_report)
    with pytest.raises(ReceiptError):
        verify(tmp_path / "absent.xml")

    good = _report(tmp_path / "good", _expected_cases())
    selection = tmp_path / "safety-targets.txt"
    selection.write_text("\n".join(expected[1:]) + "\n", encoding="utf-8")
    with pytest.raises(ReceiptError):
        verify(good, selection)
    selection.write_text("\n".join(expected) + "\n", encoding="utf-8")
    assert verify(good, selection).lines()[0] == PASS


@pytest.mark.acceptance
def test_ac_005_exact_sha_deployment_gate_requires_focused_release_ci_evidence() -> None:
    """ATDD-CI-OPTIMIZATION-M01/AC-005: the exact-SHA deployment gate certifies focused release CI evidence, never an unexecuted suite."""
    text = _text(DEPLOY_WORKFLOW)
    # Core /deploy and the owner-gated UI release both carry the gate.
    assert text.count(DEPLOY_GATE_NAME) == 2
    assert text.count("require_evidence() {") == 2
    for evidence in REQUIRED_CI_EVIDENCE:
        assert text.count(evidence) == 2, evidence
    assert "--workflow pytest.yml" in text
    # The gate never certifies a manual full-suite run and never deploys.
    assert "--workflow pytest-full-manual.yml" not in text
    assert "pytest-full-manual" not in text
    assert text.count("missing exact-SHA CI evidence") == 2
    assert "no completed focused release CI run" in text
    # A missing run id must refuse instead of continuing unproven.
    assert 'if [[ -z "$RUN_ID" ]]' in text
    assert 'if [[ -z "$RESULT" ]]' in text

