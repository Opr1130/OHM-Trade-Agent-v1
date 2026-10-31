"""CI stabilization acceptance tests; no production or runner side effects."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    SCOPE_CHANGE_REQUIRED,
    check_contracts,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)

pytestmark = pytest.mark.acceptance


def _workflow(name: str) -> dict:
    path = REPO_ROOT / ".github" / "workflows" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _events(document: dict) -> dict:
    # PyYAML 1.1 interprets the unquoted GitHub 'on' key as Boolean True.
    return document.get("on", document.get(True, {}))


def test_semgrep_covers_every_main_commit():
    """ATDD-CI-STABILIZATION-M01/AC-001: no path-filtered main SHA admission gap."""
    events = _events(_workflow("semgrep.yml"))
    push = events["push"]
    assert "main" in push["branches"]
    assert "paths" not in push
    assert "paths-ignore" not in push
    assert "pull_request" in events
    assert "workflow_dispatch" in events
    assert "schedule" in events
    assert _workflow("semgrep.yml")["jobs"]["semgrep"]["name"] == "semgrep/ci"


def test_obsolete_pr69_review_workflows_removed():
    """ATDD-CI-STABILIZATION-M01/AC-002: merged PR69 cannot dispatch retired jobs."""
    for name in (
        "pr69-ai-review-dispatch.yml",
        "pr69-external-reviews.yml",
        "pr69-external-reviews-manual.yml",
        "pr69-external-reviews-repository-dispatch.yml",
    ):
        assert not (REPO_ROOT / ".github" / "workflows" / name).exists(), name


def test_general_ai_reviews_require_explicit_pr_number():
    """ATDD-CI-STABILIZATION-M01/AC-002: no implicit PR69 or push-triggered review."""
    for name in ("gemini-review.yml", "deepseek-quant-review.yml"):
        workflow = _workflow(name)
        events = _events(workflow)
        assert set(events) == {"workflow_dispatch"}
        assert events["workflow_dispatch"]["inputs"]["pr_number"]["required"] is True
        assert workflow["jobs"]["review"]["env"]["PR_NUMBER"] == "$" + "{{ inputs.pr_number }}"


def test_ci_scope_coexists_without_repointing_active_increment():
    """ATDD-CI-STABILIZATION-M01/AC-003: CI paths approved; mixed scopes rejected."""
    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    acceptance = load_acceptance_index(APP_ROOT / "tests")
    pointer = read_increment_pointer()
    assert pointer != "ATDD-CI-STABILIZATION-M01"
    path = ".github/workflows/semgrep.yml"
    good = check_contracts(contracts, acceptance, (path,), active_increment=pointer)
    assert good.passed, good.reasons
    unmapped = check_contracts(
        contracts, acceptance, ("OHM-Trade-Agent-v1/app/jobs/unapproved_ci.py",),
        active_increment=pointer,
    )
    assert unmapped.status == SCOPE_CHANGE_REQUIRED
    mixed = check_contracts(
        contracts,
        acceptance,
        (path, "OHM-Trade-Agent-v1/app/core/config.py"),
        active_increment=pointer,
    )
    assert mixed.status == SCOPE_CHANGE_REQUIRED
