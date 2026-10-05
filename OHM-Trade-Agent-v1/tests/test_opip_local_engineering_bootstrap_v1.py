"""Local engineering bootstrap v1: VS Code and Aider tooling stays local-only and non-authoritative.

ATDD-RELEASE-PIPELINE-v1/AC-016.

The local engineering support tooling (Microsoft VS Code workspace configuration
and the Aider editing bootstrap) is bounded to local, advisory use. GitHub Linux
CI remains the canonical full regression authority; the Windows targeted pytest
and coverage tasks are local convenience only; and no task, ignore rule or
dependency grants deployment, trading, Paper-v2, Committee or merge authority.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent

TASKS_PATH = REPO_ROOT / ".vscode" / "tasks.json"
AIDERIGNORE_PATH = REPO_ROOT / ".aiderignore"
CONTRACT_PATH = (
    APP_ROOT / "docs" / "atdd" / "scope-contracts" / "ATDD-RELEASE-PIPELINE-v1.md"
)
REQUIREMENTS_DEV_PATH = APP_ROOT / "requirements-dev.txt"
REQUIREMENTS_PATH = APP_ROOT / "requirements.txt"

_TARGET_INPUT = "${input:pytestTarget}"

_REQUIRED_AIDER_IGNORES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    ".venv/",
    "**/__pycache__/",
    "**/.pytest_cache/",
    "**/.pytest_tmp*/",
    "**/.pytest-basetemp-*/",
    "coverage/",
    "htmlcov/",
    "*.log",
    "*.tmp",
    ".aider.chat.history.md",
    ".aider.input.history",
    ".aider.llm.history",
    ".aider.tags.cache.v4/",
)

_FORBIDDEN_DEPLOY_TOKENS = (
    "deploy-production",
    "deploy-committee",
    "/deploy",
)


def _basename(command: str) -> str:
    return command.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()


def _tasks() -> list[dict]:
    document = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    tasks = document["tasks"]
    assert isinstance(tasks, list)
    return tasks


def _task(label: str) -> dict:
    matches = [task for task in _tasks() if task.get("label") == label]
    assert len(matches) == 1, f"expected exactly one task labelled {label!r}"
    return matches[0]


def _surface(task: dict) -> tuple[str, list[str]]:
    command = str(task.get("command", ""))
    args = [str(arg) for arg in task.get("args", [])]
    return command, args


def _forbidden_capability(task: dict) -> str | None:
    """Return the first forbidden capability a task's command/args provides.

    Only the executable surface (``command`` + ``args``) is inspected, so
    descriptive ``label``/``detail`` prose can never trigger a false positive -
    e.g. an architecture-gate assertion that ``PAPER_V2=OFF`` is not an
    activation surface.
    """
    command, args = _surface(task)
    basename = _basename(command)
    lowered = [arg.lower() for arg in args]
    joined = " ".join([basename, *lowered])

    if basename in {"git", "git.exe"} and "push" in lowered:
        return "git push"
    if basename in {"gh", "gh.exe"} and "pr" in lowered and "merge" in lowered:
        return "gh pr merge"
    if any(token in joined for token in _FORBIDDEN_DEPLOY_TOKENS):
        return "production deployment authority"
    if re.search(r"(^|\s)deploy(\s|$)", joined):
        return "production deployment authority"
    if re.search(r"\b(?:live|funded)[-_ ]trading\b", joined):
        return "live/funded trading activation"
    if re.search(r"paper[-_]?v2[^\s]*(?:activate|enable)", joined):
        return "Paper-v2 activation"
    if re.search(r"(?:activate|enable)[-_ ]?paper[-_]?v2", joined):
        return "Paper-v2 activation"
    if re.search(r"committee[^\s]*(?:activate|enable)", joined):
        return "Committee activation"
    if re.search(r"(?:activate|enable)[-_ ]?committee", joined):
        return "Committee activation"
    return None


def _aiderignore_lines() -> set[str]:
    lines: set[str] = set()
    for raw in AIDERIGNORE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            lines.add(stripped)
    return lines


def _requirement_pins(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _implementation_targets(ac_id: str) -> set[str]:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    section = text.split("IMPLEMENTATION MAP:", 1)[1]
    section = section.split("DEFERRED DISCOVERIES:", 1)[0]
    prefix = f"{ac_id} ->"
    targets: set[str] = set()
    for raw in section.splitlines():
        stripped = raw.strip()
        if stripped.startswith(prefix):
            targets.add(stripped[len(prefix):].strip())
    return targets


@pytest.mark.acceptance
def test_ac_016_vscode_tasks_are_valid_json() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: the VS Code task definition is valid JSON with a non-empty tasks list."""
    document = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert isinstance(document.get("tasks"), list)
    assert document["tasks"]


@pytest.mark.acceptance
def test_ac_016_targeted_pytest_task_defers_to_canonical_regression() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: the Windows targeted pytest task runs pytest on one selected target and defers to GitHub as canonical."""
    task = _task("O'Pip: Targeted Pytest (Windows)")
    _command, args = _surface(task)
    assert "pytest" in args
    assert _TARGET_INPUT in args
    # Exactly one explicitly selected target; a full-suite target is never hard-coded.
    assert not any(arg.strip() in {"tests", "tests/", "."} for arg in args)
    label = str(task.get("label", "")).lower()
    detail = str(task.get("detail", "")).lower()
    assert "targeted" in label
    # It names GitHub as canonical and never claims that identity for itself.
    assert "canonical" in detail
    assert "github" in detail
    assert "full regression" in detail
    assert "is the canonical full regression" not in detail


@pytest.mark.acceptance
def test_ac_016_targeted_coverage_task_is_local_and_advisory() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: the Windows targeted coverage task is local/advisory and carries the app branch-coverage flags."""
    task = _task("O'Pip: Targeted Coverage (Windows)")
    _command, args = _surface(task)
    assert _TARGET_INPUT in args
    assert "--cov=app" in args
    assert "--cov-branch" in args
    detail = str(task.get("detail", "")).lower()
    # Not a substitute for GitHub regression, and no canonical claim of its own.
    assert "not a substitute" in detail
    assert "github" in detail
    assert "canonical" not in detail


@pytest.mark.acceptance
def test_ac_016_pre_pr_local_gate_is_not_a_full_suite_run() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: the pre-PR local gate runs no pytest suite and declares GitHub pytest.yml the canonical full Linux regression."""
    task = _task("O'Pip: Pre-PR Local Gate")
    # It composes other local checks; it never invokes a test runner itself.
    assert "command" not in task
    _command, args = _surface(task)
    assert args == []
    depends_on = task["dependsOn"]
    assert isinstance(depends_on, list) and depends_on
    for label in depends_on:
        dependency = _task(label)
        _dep_command, dep_args = _surface(dependency)
        assert "pytest" not in dep_args
    detail = str(task.get("detail", "")).lower()
    assert "github pytest.yml" in detail
    assert "canonical" in detail
    assert "full regression" in detail
    assert "linux" in detail


@pytest.mark.acceptance
def test_ac_016_engineering_health_remains_advisory() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: the Engineering Health task is explicitly advisory and does not replace GitHub required checks."""
    task = _task("O'Pip: Engineering Health (Advisory)")
    assert str(task["label"]).endswith("(Advisory)")
    detail = str(task.get("detail", "")).lower()
    assert "advisory" in detail
    assert "do not replace" in detail or "do not substitute" in detail
    assert "github" in detail
    assert "required checks" in detail


@pytest.mark.acceptance
def test_ac_016_no_task_grants_deploy_trading_or_merge_authority() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: no VS Code task provides deploy, live/funded trading, Paper-v2, Committee or merge authority."""
    offenders = {
        task.get("label"): capability
        for task in _tasks()
        for capability in (_forbidden_capability(task),)
        if capability is not None
    }
    assert offenders == {}

    # The detector is non-vacuous: planted authority surfaces are all caught.
    assert _forbidden_capability({"command": "git", "args": ["push"]}) == "git push"
    assert (
        _forbidden_capability({"command": "gh", "args": ["pr", "merge", "1"]})
        == "gh pr merge"
    )
    assert (
        _forbidden_capability({"command": "bash", "args": ["deploy-production"]})
        == "production deployment authority"
    )
    assert (
        _forbidden_capability({"command": "bash", "args": ["deploy-committee"]})
        == "production deployment authority"
    )
    assert (
        _forbidden_capability({"command": "bash", "args": ["/deploy", "abc"]})
        == "production deployment authority"
    )
    assert (
        _forbidden_capability({"command": "bash", "args": ["deploy", "abc"]})
        == "production deployment authority"
    )
    assert (
        _forbidden_capability({"command": "bash", "args": ["enable-live-trading"]})
        == "live/funded trading activation"
    )
    assert (
        _forbidden_capability({"command": "bash", "args": ["funded-trading"]})
        == "live/funded trading activation"
    )
    assert (
        _forbidden_capability({"command": "sh", "args": ["paper-v2-activate"]})
        == "Paper-v2 activation"
    )
    assert (
        _forbidden_capability({"command": "sh", "args": ["committee-activate"]})
        == "Committee activation"
    )
    # Descriptive prose outside the command surface is not an authority grant.
    assert _forbidden_capability({"label": "deploy notes", "detail": "git push"}) is None
    # A mode assertion that Paper-v2 is OFF is not an activation surface.
    assert (
        _forbidden_capability({"command": "sh", "args": ["-c", "echo PAPER_V2=OFF"]})
        is None
    )


@pytest.mark.acceptance
def test_ac_016_aiderignore_excludes_secrets_and_generated_artifacts() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: .aiderignore excludes secrets, environments, caches, coverage and runtime artifacts."""
    lines = _aiderignore_lines()
    for required in _REQUIRED_AIDER_IGNORES:
        assert required in lines, required
    # Secret families are excluded via glob patterns, not a single literal.
    assert any("api-key" in line or "apikey" in line for line in lines)
    assert any("secret" in line for line in lines)
    # Coverage output is excluded in more than one form.
    assert any(line.startswith("coverage") for line in lines)
    # Runtime artifacts (local databases) are excluded as well.
    assert any(line.endswith((".db", ".sqlite")) for line in lines)


@pytest.mark.acceptance
def test_ac_016_dev_dependencies_are_reproducible_and_local_only() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: the local bootstrap pins pytest-cov and pyright in requirements-dev.txt beside the existing dev scanners."""
    lines = _requirement_pins(REQUIREMENTS_DEV_PATH)
    assert "ruff==0.15.16" in lines
    assert "bandit==1.9.4" in lines
    assert "pip-audit==2.10.1" in lines
    assert "pytest-cov==7.1.0" in lines
    assert "pyright==1.1.414" in lines


@pytest.mark.acceptance
def test_ac_016_requirements_txt_remains_runtime_authority() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-016: requirements.txt is unchanged, carries no local-tooling dependency, and AC-016 does not authorize it."""
    runtime = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    assert "pytest-cov" not in runtime
    assert "pyright" not in runtime
    # The ATDD changed-file authorization cannot admit a runtime file: it is
    # absent from AC-016's implementation map while the dev pins are present.
    targets = _implementation_targets("AC-016")
    assert targets, "AC-016 implementation map is missing"
    basenames = {target.rsplit("/", 1)[-1] for target in targets}
    assert "requirements-dev.txt" in basenames
    assert "requirements.txt" not in basenames
    assert "test_opip_local_engineering_bootstrap_v1.py" in basenames
    assert "ATDD-RELEASE-PIPELINE-v1.md" in basenames
