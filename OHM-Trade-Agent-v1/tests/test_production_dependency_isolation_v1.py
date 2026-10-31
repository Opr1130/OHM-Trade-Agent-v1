"""ATDD: isolate the patched pytest runner from the production image."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from tests.atdd_scope import (  # noqa: E402
    SCOPE_CHANGE_REQUIRED,
    check_contracts,
    load_acceptance_index,
    load_scope_contracts,
)

pytestmark = pytest.mark.acceptance


def _file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _workflow(name: str) -> dict:
    return yaml.safe_load(_file(".github/workflows/" + name))


def test_runtime_dependencies_exclude_pytest_and_image_installs_only_runtime():
    """ATDD-PYTEST-ISOLATION-M01/AC-001: production never installs test runner."""
    runtime = _file("OHM-Trade-Agent-v1/requirements.txt")
    assert not any(line.strip().lower().startswith("pytest") for line in runtime.splitlines())
    dockerfile = _file("OHM-Trade-Agent-v1/Dockerfile")
    assert "pip install --no-cache-dir -r requirements.txt" in dockerfile
    assert "requirements-test.txt" not in dockerfile
    assert "requirements-dev.txt" not in dockerfile


def test_patched_pytest_is_explicitly_test_only():
    """ATDD-PYTEST-ISOLATION-M01/AC-002: test environment uses patched pytest."""
    test_reqs = _file("OHM-Trade-Agent-v1/requirements-test.txt")
    assert any(line.strip() == "pytest==9.0.3" for line in test_reqs.splitlines())
    workflow = _file(".github/workflows/pytest.yml")
    assert "pip install -r requirements.txt -r requirements-test.txt" in workflow
    assert "Verify production image excludes pytest" in workflow
    assert 'importlib.util.find_spec("pytest") is None' in workflow
    for name in ("m07-cutover-toolkit.yml", "opip-claude-code.yml"):
        text = _file(".github/workflows/" + name)
        assert "requirements-test.txt" in text
        yaml.safe_load(text)


def test_scope_remains_fail_closed_for_mixed_or_unmapped_paths():
    """ATDD-PYTEST-ISOLATION-M01/AC-003: maintenance scope cannot authorize runtime edits."""
    contracts = load_scope_contracts(APP / "docs" / "atdd" / "scope-contracts")
    index = load_acceptance_index(APP / "tests")
    active = "ATDD-SCANNER-RELIABILITY-M01"
    assert any(contract.increment == active for contract in contracts)
    allowed = check_contracts(
        contracts, index,
        ("OHM-Trade-Agent-v1/requirements.txt",),
        active_increment=active,
    )
    assert allowed.passed, allowed.reasons
    denied = check_contracts(
        contracts, index,
        ("OHM-Trade-Agent-v1/requirements.txt", "OHM-Trade-Agent-v1/app/core/config.py"),
        active_increment=active,
    )
    assert denied.status == SCOPE_CHANGE_REQUIRED
    unrelated = check_contracts(
        contracts, index,
        ("OHM-Trade-Agent-v1/app/jobs/suspicious.py",),
        active_increment=active,
    )
    assert unrelated.status == SCOPE_CHANGE_REQUIRED
