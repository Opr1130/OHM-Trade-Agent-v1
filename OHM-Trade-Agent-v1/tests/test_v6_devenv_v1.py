"""Acceptance checks for the v6 automated build loop."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "OHM-Trade-Agent-v1"
sys.path.insert(0, str(APP / "tools" / "v6_agent"))

import advance  # noqa: E402


def _workflow() -> dict:
    data = yaml.safe_load((ROOT / ".github/workflows/v6-agent.yml").read_text(encoding="utf-8"))
    data["_on"] = data.get("on", data.get(True))
    return data


def _action() -> dict:
    return yaml.safe_load((ROOT / ".github/actions/v6-run/action.yml").read_text(encoding="utf-8"))


@pytest.mark.acceptance
def test_agent_workflow_triggers_are_event_limited() -> None:
    """ATDD-OPIP-V6-DEVENV-v1/AC-801"""
    wf = _workflow()
    assert set(wf["_on"]) == {"issues", "issue_comment", "workflow_run", "pull_request_target"}
    assert wf["_on"]["issues"]["types"] == ["labeled"]
    for name, job in wf["jobs"].items():
        assert "if" in job, f"{name} must be gated"
    assert "github.actor == github.repository_owner" in wf["jobs"]["build"]["if"]
    assert "github.actor == github.repository_owner" in wf["jobs"]["fix"]["if"]
    assert "startsWith(github.event.workflow_run.head_branch, 'v6/')" in wf["jobs"]["autofix"]["if"]
    assert "startsWith(github.event.pull_request.head.ref, 'v6/')" in wf["jobs"]["advance"]["if"]
    for job in wf["jobs"].values():
        assert job["timeout-minutes"] <= 30


@pytest.mark.acceptance
def test_builder_pushes_only_v6_branches_and_protects_paths() -> None:
    """ATDD-OPIP-V6-DEVENV-v1/AC-802"""
    text = (ROOT / ".github/workflows/v6-agent.yml").read_text(encoding="utf-8")
    assert "refs/heads/main" not in text
    assert 'git push origin "HEAD:refs/heads/v6/issue-' in text
    assert "case \"$ref\" in v6/*" in text
    verify = (APP / "tools/v6_agent/verify.sh").read_text(encoding="utf-8")
    for protected in (r"\.github/", "deploy/", r"CLAUDE\.md", r"AGENTS\.md", "atdd_scope", "tools/v6_agent/"):
        assert protected in verify


@pytest.mark.acceptance
def test_verification_steps_have_no_ai_credential() -> None:
    """ATDD-OPIP-V6-DEVENV-v1/AC-803"""
    steps = _action()["runs"]["steps"]
    verify_steps = [s for s in steps if s["name"].startswith("Verify")]
    assert len(verify_steps) == 2
    for step in verify_steps:
        assert "DEEPSEEK" not in str(step).upper()
        assert "api_key" not in str(step).lower()
    agent_steps = [s for s in steps if s["name"].startswith("Agent attempt")]
    assert len(agent_steps) == 2
    assert "inputs.model" in str(agent_steps[0]["env"])
    assert "inputs.retry_model" in str(agent_steps[1]["env"])
    install = next(s for s in steps if s["name"] == "Install builder")
    assert "npm install -g cline" in install["run"]
    run_script = (APP / "tools/v6_agent/verify.sh").read_text(encoding="utf-8")
    assert "DEEPSEEK" not in run_script.upper()


@pytest.mark.acceptance
def test_slice_template_requires_scope_fields() -> None:
    """ATDD-OPIP-V6-DEVENV-v1/AC-804"""
    template = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/v6-slice.yml").read_text(encoding="utf-8"))
    required = {f["id"] for f in template["body"] if f.get("validations", {}).get("required")}
    assert required == {"goal", "acceptance", "files", "out_of_scope", "depends_on"}


@pytest.mark.acceptance
def test_advance_picks_next_unblocked_slice_one_at_a_time() -> None:
    """ATDD-OPIP-V6-DEVENV-v1/AC-805"""

    def issue(number: int, depends: str, labels=("v6-queue",)) -> dict:
        return {
            "number": number,
            "labels": [{"name": name} for name in labels],
            "body": f"### Goal\n\nx\n\n### Depends on\n\n{depends}\n\n### Other\n\ny",
        }

    issues = [issue(12, "#11"), issue(11, "#10"), issue(13, "none", labels=("v6-queue", "built"))]
    assert advance.dependencies(issues[0]["body"]) == [11]
    assert advance.pick_next(issues, 0, {10}) == 11
    assert advance.pick_next(issues, 0, {10, 11}) == 11
    assert advance.pick_next([issue(12, "#11")], 0, {10}) is None
    assert advance.pick_next(issues, 1, {10, 11}) is None
    assert advance.pick_next([issue(14, "none")], 0, set()) == 14


@pytest.mark.acceptance
def test_autofix_is_bounded() -> None:
    """ATDD-OPIP-V6-DEVENV-v1/AC-806"""
    wf = _workflow()
    assert wf["env"]["MAX_AUTOFIX_ATTEMPTS"] == "2"
    script = "\n".join(step.get("run", "") for step in wf["jobs"]["autofix"]["steps"])
    assert '-ge "$MAX_AUTOFIX_ATTEMPTS"' in script
    assert "Owner decision needed" in script
