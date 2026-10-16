"""Release pipeline: stacked feature pull requests run the same release gates.

Increment: `ATDD-RELEASE-PIPELINE-v1`, criterion `AC-027`. The authority for what
each assertion means is
`docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md`.

This increment is CI-only. It lets every pull request whose base is a `feature/...`
branch run the workflow that already exists for `main`, so the real pytest, ATDD
scope, architecture and security gates execute against the actual stacked commits
(R5-F, R6) instead of never starting. It adds no job, no runner, no deploy step
and no caller-supplied scope base, and it cannot weaken a gate: `push` stays
`main`-only, the job set is asserted exactly, and no step may turn a gate into an
advisory signal.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    PASS,
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)
from tests.test_atdd_scope_control import global_pointer_pin_violations  # noqa: E402

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-RELEASE-PIPELINE-v1"
CONTRACTS = APP_ROOT / "docs" / "atdd" / "scope-contracts"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
WORKFLOW = APP_ROOT.parent / ".github" / "workflows" / "pytest.yml"

GATE_JOBS = (
    "test",
    "atdd-scope",
    "architecture-gate",
    "security-gate",
    "release-candidate",
)
CHANGED_SURFACES = (
    ".github/workflows/pytest.yml",
    "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
    "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md",
    "OHM-Trade-Agent-v1/tests/test_opip_feature_stack_gates_v1.py",
)
FEATURE_BRANCH = "feature/opip-r6-committee-shadow-review-readiness"
UNMATCHED_BRANCH = "chore/ci-advisory-hygiene"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _trigger(document: dict) -> dict:
    # An unquoted `on` key is YAML 1.1 boolean True; no second form may exist.
    assert "on" not in document
    triggers = document.get(True)
    assert isinstance(triggers, dict), triggers
    return triggers


def _branch_pattern_matches(name: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return name.startswith(pattern[: -len("**")])
    return name == pattern


@pytest.mark.acceptance
def test_ac_027_feature_stack_pull_requests_run_the_same_gates() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-027: stacked feature pull requests run the gates.

    GIVEN a pull request whose base is a `feature/...` branch
    WHEN the release workflow trigger is evaluated
    THEN `pull_request` is exactly the explicit allowlist `main` plus
    `feature/**` while `push` stays exactly `main`, so the existing gates start
    for the stacked commits without adding a trigger event, a manual-dispatch
    input or any new push authority
    """
    triggers = _trigger(yaml.safe_load(_workflow_text()))
    assert set(triggers) == {"pull_request", "push"}
    assert triggers["pull_request"] == {"branches": ["main", "feature/**"]}
    assert triggers["push"] == {"branches": ["main"]}
    patterns = triggers["pull_request"]["branches"]
    assert _branch_pattern_matches("main", "main")
    assert any(_branch_pattern_matches(FEATURE_BRANCH, item) for item in patterns)
    assert not any(_branch_pattern_matches(UNMATCHED_BRANCH, item) for item in patterns)


@pytest.mark.acceptance
def test_ac_027_the_gate_set_is_unchanged_and_never_advisory() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-027: no gate is skipped or made advisory.

    GIVEN the widened trigger
    WHEN the workflow's jobs and steps are inspected
    THEN the exact five-job set still runs on GitHub-hosted Ubuntu, the four gate
    jobs carry no `if` guard, the release candidate stays non-deploying and
    main-push-only, and no `continue-on-error`, deselect or ignore directive can
    turn any gate into an advisory signal
    """
    document = yaml.safe_load(_workflow_text())
    jobs = document["jobs"]
    assert tuple(jobs) == GATE_JOBS
    for gate in GATE_JOBS:
        assert jobs[gate]["runs-on"] == "ubuntu-latest", gate
    assert jobs["atdd-scope"]["name"] == "atdd scope"
    assert jobs["architecture-gate"]["name"] == "release architecture gate"
    assert jobs["security-gate"]["name"] == "release security gate (Bandit)"
    assert jobs["release-candidate"]["name"] == "non-deploying main release candidate"
    assert jobs["release-candidate"]["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )
    assert jobs["release-candidate"]["needs"] == list(GATE_JOBS[:4])
    for gate in GATE_JOBS[:4]:
        assert "if" not in jobs[gate], gate
    text = _workflow_text()
    assert "continue-on-error" not in text
    assert "--deselect" not in text
    assert "--ignore" not in text
    assert "python -m pytest -q" in text
    assert "bandit==1.9.4" in text
    assert "release-architecture-gate.txt" in text


@pytest.mark.acceptance
def test_ac_027_the_scope_base_stays_github_derived_and_fail_closed() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-027: the scope base stays GitHub-derived.

    GIVEN the ATDD scope job
    WHEN its diff base is resolved
    THEN a pull request always diffs against `origin/<github.base_ref>` from
    GitHub, a push diffs against `github.event.before`, a missing or all-zero base
    fails closed, the increment stays the movable pointer, and no caller-supplied
    input may replace either base
    """
    text = _workflow_text()
    assert 'git fetch origin "${{ github.base_ref }}"' in text
    assert 'base="origin/${{ github.base_ref }}"' in text
    assert 'base="${{ github.event.before }}"' in text
    assert "changed-file base is missing" in text
    assert (
        'python tests/atdd_scope.py --increment "$increment" --git-base "$base"' in text
    )
    assert "increment=\"$(tr -d '\\r\\n' < docs/atdd/ACTIVE_INCREMENT)\"" in text
    assert "workflow_dispatch" not in text
    assert "inputs." not in text


@pytest.mark.acceptance
def test_ac_027_the_contract_authorizes_every_changed_surface() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-027: every changed CI surface is authorized.

    GIVEN this increment's contract, the acceptance index and the changed files
    WHEN the real scope gate evaluates them
    THEN the movable pointer resolves to a declared contract, every surface this
    change touches is mapped to AC-027 of this contract, and the gate passes with
    no unmapped file, no unapproved criterion and no traceability gap
    """
    pointer = read_increment_pointer(POINTER)
    contracts = load_scope_contracts(CONTRACTS)
    assert pointer in {item.increment for item in contracts}
    ours = [item for item in contracts if item.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    allowed = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    for surface in CHANGED_SURFACES:
        assert surface in allowed, surface
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    result = check_scope(
        contract,
        load_acceptance_index(APP_ROOT / "tests"),
        CHANGED_SURFACES,
    )
    assert result.status == PASS, result.reasons


@pytest.mark.acceptance
def test_ac_027_the_ci_increment_does_not_pin_the_movable_pointer() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-027: this module pins no global pointer.

    GIVEN this CI-only guard module
    WHEN the movable-pointer guard scans its own source
    THEN it reports no global-pointer pin, so a later increment may move
    `docs/atdd/ACTIVE_INCREMENT` without this contract claiming that identity
    """
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert global_pointer_pin_violations(source) == []


