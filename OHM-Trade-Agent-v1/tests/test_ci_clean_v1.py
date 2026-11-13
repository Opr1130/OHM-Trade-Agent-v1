"""Acceptance checks for the CI cleanup increment."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on` as boolean True.
    data["_on"] = data.get("on", data.get(True))
    return data


@pytest.mark.acceptance
def test_quality_security_is_main_push_only_and_non_blocking() -> None:
    """ATDD-OPIP-CI-CLEAN-v1/AC-701"""
    workflow = _load("quality-security.yml")
    triggers = workflow["_on"]
    assert "pull_request" not in triggers
    assert triggers["push"]["branches"] == ["main"]
    for name, job in workflow["jobs"].items():
        assert job.get("continue-on-error") is True, name


@pytest.mark.acceptance
def test_pr_secret_scan_scans_only_pr_range_and_blocks() -> None:
    """ATDD-OPIP-CI-CLEAN-v1/AC-702"""
    workflow = _load("secret-scan-pr.yml")
    assert list(workflow["_on"]) == ["pull_request"]
    job = workflow["jobs"]["gitleaks"]
    assert job.get("continue-on-error") is not True
    run_text = "\n".join(step.get("run", "") for step in job["steps"])
    assert "--log-opts" in run_text
    assert "BASE_SHA" in run_text and "HEAD_SHA" in run_text
