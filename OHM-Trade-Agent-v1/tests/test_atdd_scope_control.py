"""Acceptance tests for ATDD scope control.

These tests prove the scope-control mechanism. They do not import app runtime.
"""

from __future__ import annotations

import ast
import subprocess
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
    ScopeContract,
    acceptance_ids_in_source,
    check_contracts,
    check_scope,
    git_changed_paths,
    load_acceptance_index,
    load_scope_contracts,
    main,
    parse_name_status,
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
    implementation: str = "AC-001 -> OHM-Trade-Agent-v1/docs/atdd/README.md",
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


def _index(increment: str = "ATDD-SYNTHETIC") -> AcceptanceIndex:
    return AcceptanceIndex(
        by_ac={(increment, "AC-001"): frozenset({SYNTHETIC_NODE})},
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
    """ATDD-000-scope-control/AC-001: a criterion with no acceptance test does not complete the increment."""
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
    suite = check_contracts(
        loaded,
        load_acceptance_index(APP_ROOT / "tests"),
        ("OHM-Trade-Agent-v1/docs/atdd/README.md",),
        active_increment="ATDD-000-scope-control",
    )
    assert suite.status == "PASS"
    assert main([]) == 2


@pytest.mark.acceptance
def test_ac_002_unapproved_scope_change_stops() -> None:
    """ATDD-000-scope-control/AC-002: unapproved scope is SCOPE_CHANGE_REQUIRED and is not implemented."""
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
    """ATDD-000-scope-control/AC-003: a deferred discovery is not an acceptance criterion."""
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
    """ATDD-000-scope-control/AC-004: a changed file outside the implementation map stops the increment."""
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
    """ATDD-000-scope-control/AC-005: scope control does not import runtime code or edit architecture."""
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
        assert not path.startswith("OHM-Trade-Agent-v1/docs/architecture/")
        assert not path.startswith("OHM-Trade-Agent-v1/app/")
        assert (APP_ROOT.parent / path).is_file()

    pyproject = (APP_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(pyproject)
    assert "addopts" not in parsed.get("tool", {}).get("pytest", {}).get("ini_options", {})
    markers = parsed["tool"]["pytest"]["ini_options"]["markers"]
    assert any(str(marker).startswith("acceptance:") for marker in markers)


@pytest.mark.acceptance
def test_ac_001_reused_criterion_numbers_stay_independent() -> None:
    """ATDD-000-scope-control/AC-001: two increments may both define AC-001."""
    older = parse_scope_contract(
        _contract_text(
            increment="ATDD-000",
            trace="AC-001 -> tests/test_old.py::test_old",
            implementation="AC-001 -> app/old.py",
        )
    )
    current = parse_scope_contract(
        _contract_text(
            increment="ATDD-001",
            trace="AC-001 -> tests/test_new.py::test_new",
            implementation="AC-001 -> app/new.py",
        )
    )
    index = AcceptanceIndex(
        by_ac={
            ("ATDD-000", "AC-001"): frozenset({"tests/test_old.py::test_old"}),
            ("ATDD-001", "AC-001"): frozenset({"tests/test_new.py::test_new"}),
        },
        unscoped_tests=(),
    )
    assert check_scope(older, index).status == "PASS"
    assert check_scope(current, index).status == "PASS"
    blocked = check_contracts(
        (older, current),
        index,
        ("app/old.py",),
        active_increment="ATDD-001",
    )
    assert blocked.status == SCOPE_CHANGE_REQUIRED


@pytest.mark.acceptance
def test_ac_001_discovers_pytest_acceptance_forms() -> None:
    """ATDD-000-scope-control/AC-001: class, async, and pytestmark acceptance tests are indexed."""
    source = "\n".join(
        [
            "import pytest",
            "pytestmark = [pytest.mark.acceptance]",
            "def test_sync():",
            '    """ATDD-001/AC-001: module sync test."""',
            "async def test_module():",
            '    """ATDD-001/AC-001: module async test."""',
            "@pytest.mark.acceptance",
            "class TestSomething:",
            "    async def test_behavior(self):",
            '        """ATDD-001/AC-002: class async test."""',
            "class TestOther:",
            "    pytestmark = pytest.mark.acceptance",
            "    def test_method(self):",
            '        """ATDD-001/AC-003: class pytestmark test."""',
        ]
    )
    found, unscoped = acceptance_ids_in_source(source, "tests/test_x.py")
    assert found[("ATDD-001", "AC-001")] == {
        "tests/test_x.py::test_sync",
        "tests/test_x.py::test_module",
    }
    assert found[("ATDD-001", "AC-002")] == {
        "tests/test_x.py::TestSomething::test_behavior"
    }
    assert found[("ATDD-001", "AC-003")] == {"tests/test_x.py::TestOther::test_method"}
    assert unscoped == []
    direct = "\n".join(
        [
            "import pytest",
            "def test_plain():",
            '    """ATDD-001/AC-004: unmarked sync test is not acceptance."""',
            "@pytest.mark.acceptance",
            "def test_direct():",
            '    """ATDD-001/AC-004: function-level acceptance mark."""',
        ]
    )
    found, unscoped = acceptance_ids_in_source(direct, "tests/test_x.py")
    assert found[("ATDD-001", "AC-004")] == {"tests/test_x.py::test_direct"}
    assert unscoped == []
    alias = "import pytest\nmark = pytest.mark.acceptance\npytestmark = mark\n"
    with pytest.raises(ValueError, match="unsupported pytestmark alias"):
        acceptance_ids_in_source(alias, "tests/test_alias.py")
    named = "@acceptance\ndef test_named():\n    pass\n"
    with pytest.raises(ValueError, match="unsupported decorator alias"):
        acceptance_ids_in_source(named, "tests/test_alias.py")


@pytest.mark.acceptance
def test_ac_001_empty_behavior_labels_fail() -> None:
    """ATDD-000-scope-control/AC-001: empty GIVEN, WHEN, and THEN labels do not validate."""
    contract = parse_scope_contract(
        _contract_text(
            criteria="\n".join(["AC-001:", "GIVEN:", "WHEN:", "THEN:"])
        )
    )
    result = check_scope(contract, _index())
    assert result.status == TRACEABILITY_GAP
    assert any("empty GIVEN" in reason for reason in result.trace_reasons)


@pytest.mark.acceptance
def test_ac_004_only_active_increment_authorizes_files() -> None:
    """ATDD-000-scope-control/AC-004: an older map does not authorize the active increment."""
    older = parse_scope_contract(
        _contract_text(
            increment="ATDD-000",
            trace="AC-001 -> tests/test_old.py::test_old",
            implementation="AC-001 -> app/old.py",
        )
    )
    current = parse_scope_contract(
        _contract_text(
            increment="ATDD-001",
            trace="AC-001 -> tests/test_new.py::test_new",
            implementation="AC-001 -> app/new.py",
        )
    )
    index = AcceptanceIndex(
        by_ac={
            ("ATDD-000", "AC-001"): frozenset({"tests/test_old.py::test_old"}),
            ("ATDD-001", "AC-001"): frozenset({"tests/test_new.py::test_new"}),
        },
        unscoped_tests=(),
    )
    allowed = check_contracts(
        (older, current),
        index,
        ("app/new.py",),
        active_increment="ATDD-001",
    )
    assert allowed.status == "PASS"
    historical = check_contracts(
        (older, current),
        index,
        ("app/old.py",),
        active_increment="ATDD-001",
    )
    assert historical.status == SCOPE_CHANGE_REQUIRED
    assert any("app/old.py" in reason for reason in historical.scope_reasons)
    unknown = check_contracts(
        (older, current),
        index,
        ("app/new.py",),
        active_increment="ATDD-MISSING",
    )
    assert unknown.status == SCOPE_CHANGE_REQUIRED
    assert any("unknown or ambiguous" in reason for reason in unknown.scope_reasons)


@pytest.mark.acceptance
def test_ac_004_omitted_changed_file_set_fails_closed(capsys: pytest.CaptureFixture[str]) -> None:
    """ATDD-000-scope-control/AC-004: a green gate requires the changed-file set."""
    omitted = check_contracts(
        load_scope_contracts(CONTRACT_DIR),
        load_acceptance_index(APP_ROOT / "tests"),
        None,
        active_increment="ATDD-000-scope-control",
    )
    assert omitted.status == SCOPE_CHANGE_REQUIRED
    assert any("changed-file set was not provided" in reason for reason in omitted.scope_reasons)
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "changed-file set was not provided" in captured.out
    unmapped = main(["app/old.py"])
    captured = capsys.readouterr()
    assert unmapped == 2
    assert "app/old.py" in captured.out
    workflow = (APP_ROOT.parent / ".github" / "workflows" / "pytest.yml").read_text(
        encoding="utf-8"
    )
    assert "atdd-scope:" in workflow
    assert "docs/atdd/ACTIVE_INCREMENT" in workflow
    assert "python tests/atdd_scope.py --increment" in workflow
    assert "--git-base" in workflow


@pytest.mark.acceptance
def test_ac_004_git_diff_includes_rename_and_delete(tmp_path: Path) -> None:
    """ATDD-000-scope-control/AC-004: rename and delete paths are part of the changed-file set."""
    status = "\n".join(
        [
            "A\tapp/new.py",
            "M\tdocs/atdd/README.md",
            "D\tapp/old.py",
            "R100\tapp/from.py\tapp/to.py",
        ]
    )
    assert parse_name_status(status) == (
        "app/new.py",
        "docs/atdd/README.md",
        "app/old.py",
        "app/from.py",
        "app/to.py",
    )

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=ATDD Test", "-c", "user.email=atdd@example.com", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init")
    (tmp_path / "old.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "gone.py").write_text("gone\n", encoding="utf-8")
    git("add", "old.py", "gone.py")
    git("commit", "-m", "base")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    git("mv", "old.py", "new.py")
    (tmp_path / "gone.py").unlink()
    git("add", "-A")
    git("commit", "-m", "change")
    changed = set(git_changed_paths(tmp_path, base))
    assert changed == {"old.py", "new.py", "gone.py"}
    renamed = parse_scope_contract(
        _contract_text(implementation="AC-001 -> app/to.py")
    )
    only_new = check_contracts(
        (renamed,),
        _index(),
        ("app/from.py", "app/to.py"),
        active_increment="ATDD-SYNTHETIC",
    )
    assert only_new.status == SCOPE_CHANGE_REQUIRED
    assert any("app/from.py" in reason for reason in only_new.scope_reasons)
    assert all("app/to.py" not in reason for reason in only_new.scope_reasons)


@pytest.mark.acceptance
def test_ac_004_repo_root_paths_do_not_collapse() -> None:
    """ATDD-000-scope-control/AC-004: a repository-root path is not an application-root path."""
    index = _index()

    def gate(contract: ScopeContract, changed: tuple[str, ...]):
        return check_contracts(
            (contract,),
            index,
            changed,
            active_increment="ATDD-SYNTHETIC",
        )

    app_file = parse_scope_contract(
        _contract_text(implementation="AC-001 -> OHM-Trade-Agent-v1/pyproject.toml")
    )
    assert gate(app_file, ("OHM-Trade-Agent-v1/pyproject.toml",)).status == "PASS"
    assert gate(app_file, ("./OHM-Trade-Agent-v1/pyproject.toml",)).status == "PASS"
    assert gate(app_file, ("OHM-Trade-Agent-v1\\pyproject.toml",)).status == "PASS"
    root_pyproject = gate(app_file, ("pyproject.toml",))
    assert root_pyproject.status == SCOPE_CHANGE_REQUIRED
    assert any(
        reason.endswith(": pyproject.toml") for reason in root_pyproject.scope_reasons
    )

    workflow = parse_scope_contract(
        _contract_text(implementation="AC-001 -> .github/workflows/pytest.yml")
    )
    assert gate(workflow, (".github/workflows/pytest.yml",)).status == "PASS"
    assert gate(workflow, ("./.github/workflows/pytest.yml",)).status == "PASS"
    nested_workflow = gate(
        workflow,
        ("OHM-Trade-Agent-v1/.github/workflows/pytest.yml",),
    )
    assert nested_workflow.status == SCOPE_CHANGE_REQUIRED
    assert any(
        "OHM-Trade-Agent-v1/.github/workflows/pytest.yml" in reason
        for reason in nested_workflow.scope_reasons
    )

    for escaped in (
        "../pyproject.toml",
        "OHM-Trade-Agent-v1/../pyproject.toml",
        "OHM-Trade-Agent-v1/docs/../../pyproject.toml",
        "/tmp/pyproject.toml",
        "OHM-Trade-Agent-v1//pyproject.toml",
    ):
        rejected = gate(app_file, (escaped,))
        assert rejected.status == SCOPE_CHANGE_REQUIRED
        assert any("escapes the repository" in reason for reason in rejected.scope_reasons)

    with pytest.raises(ValueError, match="escapes the repository"):
        parse_scope_contract(_contract_text(implementation="AC-001 -> ../pyproject.toml"))
