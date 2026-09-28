"""Acceptance tests for ATDD scope control.

These tests prove the scope-control mechanism. They do not import app runtime.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    SCOPE_CHANGE_REQUIRED,
    TRACEABILITY_GAP,
    AcceptanceIndex,
    check_contracts,
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    main,
    parse_scope_contract,
)

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
SYNTHETIC_NODE = "tests/test_synthetic.py::test_criterion"
FORBIDDEN_IMPORTS = frozenset(
    {"app", "ccxt", "telegram", "pytest_bdd", "behave", "cucumber"}
)


def _contract_text(
    *,
    criteria: str | None = None,
    trace: str = f"AC-001 -> {SYNTHETIC_NODE}",
    implementation: str = "AC-001 -> docs/atdd/README.md",
    deferred: str = "- none recorded for this synthetic contract",
    unapproved: str = "NONE",
    increment: str = "ATDD-SYNTHETIC",
) -> str:
    if criteria is None:
        criteria = "\n".join(
            [
                "AC-001:",
                "GIVEN:",
                "a scope contract lists this criterion",
                "WHEN:",
                "its acceptance test is evaluated",
                "THEN:",
                "the criterion is either proved or the check fails",
            ]
        )
    sections = (
        ("INCREMENT", increment),
        ("OWNER-APPROVED INTENT", "Synthetic contract for the scope checker."),
        ("ARCHITECTURE REFERENCES", "- v1.4.3 section 1"),
        ("APPROVED ACCEPTANCE CRITERIA", criteria),
        ("EXPLICITLY OUT OF SCOPE", "- product features"),
        ("FROZEN BOUNDARIES", "- no funded trading authority"),
        ("ACCEPTANCE TEST TRACEABILITY", trace),
        ("IMPLEMENTATION MAP", implementation),
        ("DEFERRED DISCOVERIES", deferred),
        ("UNAPPROVED SCOPE CHANGES", unapproved),
    )
    return "\n\n".join(f"{name}:\n{body.strip()}" for name, body in sections) + "\n"


def _index() -> AcceptanceIndex:
    return AcceptanceIndex(
        by_ac={"AC-001": frozenset({SYNTHETIC_NODE})},
        unscoped_tests=(),
    )


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


@pytest.mark.acceptance
def test_ac_001_unmapped_criterion_fails_and_mapped_contract_passes() -> None:
    """AC-001: a criterion with no acceptance test does not complete the increment."""
    with pytest.raises(ValueError, match="missing sections"):
        parse_scope_contract("INCREMENT:\nATDD-SYNTHETIC\n")

    contract = parse_scope_contract(_contract_text())
    missing = check_scope(
        contract,
        AcceptanceIndex(by_ac={}, unscoped_tests=()),
    )
    assert missing.status == TRACEABILITY_GAP
    assert missing.passed is False

    assert check_scope(contract, _index()).status == "PASS"

    loaded = load_scope_contracts(CONTRACT_DIR)
    suite = check_contracts(loaded, load_acceptance_index(APP_ROOT / "tests"))
    assert suite.status == "PASS"
    assert main([]) == 0


@pytest.mark.acceptance
def test_ac_002_unapproved_scope_change_stops() -> None:
    """AC-002: unapproved scope is SCOPE_CHANGE_REQUIRED and is not implemented."""
    contract = parse_scope_contract(
        _contract_text(unapproved="- reserve a new order path for this increment")
    )
    result = check_scope(contract, _index())
    assert result.status == SCOPE_CHANGE_REQUIRED
    assert any("UNAPPROVED SCOPE CHANGES" in reason for reason in result.scope_reasons)

    real = load_scope_contracts(CONTRACT_DIR)[0]
    assert real.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"


@pytest.mark.acceptance
def test_ac_003_deferred_discovery_is_not_approved() -> None:
    """AC-003: a deferred discovery is not an acceptance criterion."""
    contract = parse_scope_contract(
        _contract_text(
            deferred="- AC-099: adjacent defect recorded for a later increment"
        )
    )
    assert "AC-099" in contract.sections["DEFERRED DISCOVERIES"]
    assert "AC-099" not in contract.acceptance_criteria
    assert check_scope(contract, _index()).status == "PASS"


@pytest.mark.acceptance
def test_ac_004_unmapped_changed_file_requires_scope_change(capsys: pytest.CaptureFixture[str]) -> None:
    """AC-004: a changed file outside the implementation map stops the increment."""
    contract = parse_scope_contract(_contract_text())
    blocked = check_scope(
        contract,
        _index(),
        ("OHM-Trade-Agent-v1/app/services/risk.py",),
    )
    assert blocked.status == SCOPE_CHANGE_REQUIRED
    assert any("app/services/risk.py" in reason for reason in blocked.scope_reasons)

    allowed = check_scope(
        contract,
        _index(),
        ("OHM-Trade-Agent-v1/docs/atdd/README.md",),
    )
    assert allowed.status == "PASS"

    exit_code = main(["OHM-Trade-Agent-v1/app/services/risk.py"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out.splitlines()[0] == SCOPE_CHANGE_REQUIRED


@pytest.mark.acceptance
def test_ac_005_checker_stays_outside_runtime_and_architecture() -> None:
    """AC-005: scope control does not import runtime code or edit architecture."""
    checker = (APP_ROOT / "tests" / "atdd_scope.py").read_text(encoding="utf-8")
    tests = Path(__file__).read_text(encoding="utf-8")
    assert _imported_roots(checker).isdisjoint(FORBIDDEN_IMPORTS)
    assert _imported_roots(tests).isdisjoint(FORBIDDEN_IMPORTS - {"pytest"})

    contract = load_scope_contracts(CONTRACT_DIR)[0]
    mapped: set[str] = set()
    for paths in contract.implementation_map.values():
        mapped.update(paths)
    assert mapped
    for path in mapped:
        assert not path.startswith("docs/architecture/")
        assert not path.startswith("app/")
        assert (APP_ROOT / path).is_file()

    pyproject = (APP_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(pyproject)
    assert "addopts" not in parsed.get("tool", {}).get("pytest", {}).get("ini_options", {})
    markers = parsed["tool"]["pytest"]["ini_options"]["markers"]
    assert any(str(marker).startswith("acceptance:") for marker in markers)
