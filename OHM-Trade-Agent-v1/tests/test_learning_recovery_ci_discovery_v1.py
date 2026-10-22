"""ATDD regression for exact-SHA GitHub workflow CI discovery.

A successful run must be discoverable even when other workflows are newer.
An absent, wrong-SHA, wrong-branch, wrong-workflow, incomplete or failed run
must never authorize learning-production recovery.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-learning-recovery.yml"
SHA = "b" * 40
PROD = "a" * 40
WORKFLOWS = ("pytest.yml", "quality-security.yml", "semgrep.yml")
STEPS = (
    "Resolve tooling main and require exact-SHA CI",
    "Revalidate main and exact-SHA CI before mutation",
)
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash") or not shutil.which("jq"),
    reason="real bash and jq required (run on Linux CI)",
)


def _step(name):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(step["run"] for step in workflow["jobs"]["deploy"]["steps"]
                if step.get("name") == name)


def _run_entry(workflow, number=1, attempt=1, **overrides):
    payload = {
        "id": number,
        "run_number": number,
        "run_attempt": attempt,
        "head_sha": SHA,
        "head_branch": "main",
        "path": ".github/workflows/" + workflow,
        "status": "completed",
        "conclusion": "success",
    }
    payload.update(overrides)
    return payload


def _invoke(tmp_path, *, broken=None, step_name=STEPS[0], api_error=None):
    directory = tmp_path / "responses"
    directory.mkdir()
    broken = broken or {}
    for workflow in WORKFLOWS:
        runs = broken.get(workflow, [_run_entry(workflow)])
        (directory / (workflow + ".json")).write_text(
            json.dumps({"total_count": len(runs), "workflow_runs": runs}),
            encoding="utf-8",
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "api" ]] || exit 50
if [[ "$2" == *"/commits/main" ]]; then
  printf '%s\n' "$FAKE_MAIN_SHA"
  exit 0
fi
if [[ "$2" == *"/compare/"* ]]; then
  printf 'ahead\n'
  exit 0
fi
if [[ "$2" == *"/actions/workflows/"*"/runs?"* ]]; then
  w="$(printf '%s' "$2" | cut -d / -f 6)"
  [[ "$w" != "$FAKE_API_ERROR" ]] || exit 52
  [[ "$3" == "--jq" ]] || exit 53
  jq -r "$4" "$FAKE_RUNS_ROOT/$w.json"
  exit $?
fi
exit 54
''',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    gh_output = tmp_path / "github-output"
    gh_output.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
        "FAKE_RUNS_ROOT": str(directory),
        "FAKE_MAIN_SHA": SHA,
        "FAKE_API_ERROR": api_error or "",
        "GITHUB_REPOSITORY": "Opr1130/OHM-Trade-Agent-v1",
        "GITHUB_OUTPUT": str(gh_output),
        "PRODUCTION_SHA": PROD,
        "TOOLING_SHA": SHA,
    })
    execution = subprocess.run(
        ["bash", "-c", _step(step_name)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return execution, gh_output.read_text(encoding="utf-8")


@pytest.mark.acceptance
@pytest.mark.parametrize("name", STEPS)
def test_exact_sha_green_ci_accepted_through_workflow_api(tmp_path, name):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-020: exact-SHA workflow CI gate."""
    result, output = _invoke(tmp_path, step_name=name)
    assert result.returncode == 0, result.stderr
    if name == STEPS[0]:
        assert "sha=" + SHA in output
        assert "compare=ahead" in output


@pytest.mark.acceptance
@pytest.mark.parametrize("name", STEPS)
@pytest.mark.parametrize("workflow", WORKFLOWS)
@pytest.mark.parametrize("failure", ["missing", "wrong_sha", "wrong_branch",
                                      "wrong_workflow", "failure", "queued",
                                      "newer_failed", "newer_in_progress"])
def test_exact_sha_ci_fail_closed_on_invalid_observation(
    tmp_path, name, workflow, failure
):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-020: exact-SHA workflow CI gate."""
    good = _run_entry(workflow)
    if failure == "missing":
        entries = []
    elif failure == "wrong_sha":
        entries = [_run_entry(workflow, head_sha="c" * 40)]
    elif failure == "wrong_branch":
        entries = [_run_entry(workflow, head_branch="feature/other")]
    elif failure == "wrong_workflow":
        entries = [_run_entry(workflow, path=".github/workflows/other.yml")]
    elif failure == "failure":
        entries = [_run_entry(workflow, conclusion="failure")]
    elif failure == "queued":
        entries = [_run_entry(workflow, status="queued", conclusion=None)]
    elif failure == "newer_failed":
        entries = [good, _run_entry(workflow, number=2, conclusion="failure")]
    else:
        entries = [good, _run_entry(workflow, number=2,
                                    status="in_progress", conclusion=None)]
    result, _ = _invoke(
        tmp_path, broken={workflow: entries}, step_name=name
    )
    assert result.returncode != 0, (workflow, failure, result.stdout)
    assert workflow in result.stderr or failure == "missing"


@pytest.mark.acceptance
@pytest.mark.parametrize("name", STEPS)
def test_ci_lookup_api_error_fails_closed(tmp_path, name):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-020: exact-SHA workflow CI gate."""
    result, _ = _invoke(tmp_path, step_name=name, api_error="pytest.yml")
    assert result.returncode != 0



@pytest.mark.acceptance
@pytest.mark.parametrize("name", STEPS)
@pytest.mark.parametrize("suffix", ["@main", "@refs/heads/main", "@" + SHA])
def test_workflow_path_with_git_ref_suffix_is_valid(tmp_path, name, suffix):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-020: GitHub path @ref form remains valid."""
    entries = {w: [_run_entry(w, path=".github/workflows/" + w + suffix)]
               for w in WORKFLOWS}
    result, _ = _invoke(tmp_path, broken=entries, step_name=name)
    assert result.returncode == 0, result.stderr

@pytest.mark.acceptance
def test_gate_queries_workflow_identity_sha_and_branch_in_both_steps():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-020: exact-SHA workflow CI gate."""
    for name in STEPS:
        script = _step(name)
        assert "actions/workflows/$WORKFLOW/runs?head_sha=$TOOLING_SHA&branch=main" in script
        assert '.head_sha ==' in script
        assert '.head_branch ==' in script
        assert '.path | split(' in script
        assert "sort_by(.run_number, .run_attempt)" in script
        assert "completed" in script
        assert "success" in script
        assert "gh run list" not in script
