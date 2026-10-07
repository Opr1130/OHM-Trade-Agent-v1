"""CI/CD advisory-hygiene and workflow-runtime contracts.

These tests freeze the ATDD-RELEASE-PIPELINE-v1 AC-021 remediation: optional and
advisory CI signals are cleaned up (advisory scanners become step-scoped instead
of job-scoped, malformed optional review workflows become valid and path/manual
scoped, missing optional review credentials produce SKIPPED steps rather than a
failed check, and every first-party action runs on a Node 24-compatible major),
while every genuine required release gate stays strict.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# First-party action majors whose action.yml declares `using: node24` (the
# runtime the GitHub-hosted runner force-migrates Node 20 actions onto). Newer
# majors are allowed; older ones are the deprecation warning being remediated.
NODE24_MIN_MAJOR = {
    "actions/checkout": 6,
    "actions/setup-python": 6,
    "actions/upload-artifact": 6,
    "actions/download-artifact": 6,
}

# The advisory scanner step in each advisory-only quality-security job. The
# scanner step is allowed to fail without failing the job; every other step
# (checkout, Python setup, dependency installation) stays strict so a real
# workflow/infrastructure failure is still rendered RED.
ADVISORY_SCANNER_STEPS = {
    "ruff": "Run Ruff (E/F, no E501)",
    "bandit": "Run Bandit on app/",
    "pip-audit": "Audit runtime requirements.txt",
    "gitleaks": "Detect secrets",
}


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _workflow_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yml"))}


def _triggers(document: dict) -> dict:
    # PyYAML resolves the bare `on:` key to the boolean True.
    return document.get("on") or document.get(True) or {}


@pytest.mark.acceptance
def test_ac_021_advisory_scanners_are_step_scoped_not_job_scoped():
    """ATDD-RELEASE-PIPELINE-v1/AC-021: each advisory quality-security job is no
    longer job-level ``continue-on-error`` (which made GitHub render the whole
    job RED on findings); only the scanner step is advisory, so a real
    checkout/setup/install/infrastructure failure still fails the job.
    """
    jobs = _load("quality-security.yml")["jobs"]
    assert set(jobs) == set(ADVISORY_SCANNER_STEPS)
    for job_name, scanner_step in ADVISORY_SCANNER_STEPS.items():
        job = jobs[job_name]
        assert job.get("continue-on-error") is not True, job_name
        by_name = {step.get("name"): step for step in job["steps"]}
        assert by_name[scanner_step].get("continue-on-error") is True, job_name
        for step in job["steps"]:
            if step.get("name") == scanner_step:
                continue
            assert step.get("continue-on-error") is not True, (job_name, step.get("name"))


@pytest.mark.acceptance
def test_ac_021_quality_security_keeps_read_only_permissions():
    """ATDD-RELEASE-PIPELINE-v1/AC-021: the advisory cleanup grants no new
    privilege to quality-security -- it keeps read-only ``contents`` permission
    and never gains a ``pull_request_target`` trigger.
    """
    document = _load("quality-security.yml")
    assert document["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in _triggers(document)


@pytest.mark.acceptance
def test_ac_021_required_release_gates_remain_strict():
    """ATDD-RELEASE-PIPELINE-v1/AC-021: the required release gates stay strict --
    the pytest workflow jobs ``test``, ``atdd scope``, ``release architecture
    gate`` and ``release security gate (Bandit)`` are present and never
    job-level ``continue-on-error``, and the required ``semgrep/ci`` check still
    runs a failing ``semgrep ci``.
    """
    pytest_jobs = _load("pytest.yml")["jobs"]
    # A job without an explicit `name:` is displayed under its job key.
    gate_names = {job.get("name") or key for key, job in pytest_jobs.items()}
    assert {
        "test",
        "atdd scope",
        "release architecture gate",
        "release security gate (Bandit)",
    } <= gate_names
    for job in pytest_jobs.values():
        assert job.get("continue-on-error") is not True

    semgrep_job = _load("semgrep.yml")["jobs"]["semgrep"]
    assert semgrep_job.get("name") == "semgrep/ci"
    assert semgrep_job.get("continue-on-error") is not True
    assert any("semgrep ci" in (step.get("run") or "") for step in semgrep_job["steps"])


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "name",
    [
        "deepseek-quant-review.yml",
        "gemini-review.yml",
        "pr69-external-reviews.yml",
        "pr69-external-reviews-manual.yml",
        "pr69-external-reviews-repository-dispatch.yml",
    ],
)
def test_ac_021_optional_review_workflows_parse_and_stop_push_noise(name):
    """ATDD-RELEASE-PIPELINE-v1/AC-021: the optional external-review workflows
    parse as valid workflow YAML (they previously failed closed at parse time on
    every push to main because a blank line inside a quoted shell body ended the
    ``run: |`` block scalar at column zero), and the optional reviews no longer
    run as unconditional push-to-main noise: the two push-triggered reviews are
    restricted to their explicit one-shot trigger path.
    """
    document = _load(name)
    triggers = _triggers(document)
    assert isinstance(document.get("jobs"), dict) and document["jobs"], name
    if "push" in triggers:
        push = triggers["push"] or {}
        assert push.get("paths") == [".github/ai-review-pr69.trigger"], (name, push.get("paths"))


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "name,secret",
    [
        ("deepseek-quant-review.yml", "DEEPSEEK_API_KEY"),
        ("gemini-review.yml", "GEMINI_API_KEY"),
    ],
)
def test_ac_021_missing_optional_review_credential_skips_instead_of_failing(name, secret):
    """ATDD-RELEASE-PIPELINE-v1/AC-021: an unavailable optional review credential
    produces SKIPPED steps, not a FAILED check -- the validation step records
    availability in ``$GITHUB_OUTPUT`` instead of a hard ``exit``, and the steps
    that need the credential are gated on that output.
    """
    steps = _load(name)["jobs"]["review"]["steps"]
    gate_steps = [step for step in steps if step.get("id") == "gate"]
    assert len(gate_steps) == 1
    gate = gate_steps[0]
    assert secret in yaml.safe_dump(gate.get("env") or {})
    assert "GITHUB_OUTPUT" in gate["run"]
    assert "exit 65" not in gate["run"]
    gated = [
        step
        for step in steps
        if "steps.gate.outputs.available" in str(step.get("if") or "")
    ]
    assert len(gated) >= 2


@pytest.mark.acceptance
def test_ac_021_first_party_actions_use_node24_majors():
    """ATDD-RELEASE-PIPELINE-v1/AC-021: every first-party action reference in
    every workflow uses a Node 24-compatible major, so the runner never has to
    force a deprecated Node 20 action onto Node 24.
    """
    pattern = re.compile(
        r"actions/(checkout|setup-python|upload-artifact|download-artifact)@v(\d+)"
    )
    offenders: list[tuple[str, str, str]] = []
    for name, text in _workflow_texts().items():
        for action, major in pattern.findall(text):
            if int(major) < NODE24_MIN_MAJOR[f"actions/{action}"]:
                offenders.append((name, action, major))
    assert offenders == []
