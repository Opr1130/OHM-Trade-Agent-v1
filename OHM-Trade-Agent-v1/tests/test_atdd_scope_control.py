"""Acceptance tests for ATDD scope control.

These tests prove the scope-control mechanism. They do not import app runtime.
"""

from __future__ import annotations

import ast
import re
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


# ---------------------------------------------------------------------------
# Movable active-increment pointer governance
# (ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1)
#
# `docs/atdd/ACTIVE_INCREMENT` is deliberately movable orchestration state. Exact
# current-increment enforcement belongs to tests/atdd_scope.py and the `atdd scope`
# CI job. A COMPLETED increment's acceptance test must never permanently require the
# global pointer to equal that increment, because only one increment can be active
# and pinning it blocks every later approved increment. An increment proves its own
# identity from its own frozen scope contract and test module; the global pointer
# only has to resolve to an existing scope contract.
# ---------------------------------------------------------------------------

POINTER_FILE_NAME = "ACTIVE_INCREMENT"
POINTER_READ_FUNCTION = "read_increment_pointer"
INCREMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_AC_CITATION = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)/AC-\d+")
_PIN_COMPARISONS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)
_PIN_METHODS = ("startswith", "endswith")
# A lineage prefix/suffix must be materially more specific than the shared "ATDD-"
# namespace and must align on an identifier-segment boundary. Generic fragments
# ("ATDD-", the empty string, single characters) and unrelated lineages are never
# ownership pins.
_MIN_LINEAGE_LENGTH = 8
_MIN_PREFIX_TOKENS = 3
_MIN_SUFFIX_TOKENS = 2


def _assigned_name_value(node: ast.AST) -> tuple:
    """The single ``Name`` target and value of an assign-like statement, or None.

    Covers plain assignment, annotated assignment and the walrus operator, so a
    pointer read bound through any of them still counts as pointer-derived.
    """
    targets = getattr(node, "targets", None)
    if isinstance(node, ast.Assign) and targets and len(targets) == 1:
        if isinstance(targets[0], ast.Name):
            return targets[0].id, node.value
        return None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.value is not None:
            return node.target.id, node.value
        return None
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        if node.value is not None:
            return node.target.id, node.value
    return None


def _is_pointer_read_call(node: ast.AST, reader_names: frozenset = frozenset()) -> bool:
    """True for a direct or attribute-qualified pointer-reader call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == POINTER_READ_FUNCTION or func.id in reader_names
    if isinstance(func, ast.Attribute):
        return func.attr == POINTER_READ_FUNCTION
    return False


def _mentions_pointer(node: ast.AST, reader_names: frozenset = frozenset()) -> bool:
    """True when an expression reads or names the global active-increment pointer.

    A pointer expression mentions ``ACTIVE_INCREMENT`` directly, names the canonical
    ``read_increment_pointer`` helper (or a local helper that returns the pointer),
    or calls one of them directly or as an attribute.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if POINTER_FILE_NAME in child.value:
                return True
        elif isinstance(child, ast.Name):
            if (
                POINTER_FILE_NAME in child.id
                or child.id == POINTER_READ_FUNCTION
                or child.id in reader_names
            ):
                return True
        elif isinstance(child, ast.Attribute):
            if POINTER_FILE_NAME in child.attr or child.attr == POINTER_READ_FUNCTION:
                return True
        elif _is_pointer_read_call(child, reader_names):
            return True
    return False


def _docstring_increments(tree: ast.Module) -> set:
    """Increment ids cited by this module's own acceptance-test docstrings.

    A module's identity is whichever increment its ``INCREMENT/AC-NNN`` docstrings
    name, so the guard never depends on how the module chose to spell its constant.
    """
    increments: set = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            doc = ast.get_docstring(node)
            if doc:
                increments.update(_AC_CITATION.findall(doc))
    return increments


def _own_increment_constants(tree: ast.Module) -> dict:
    """Module-level NAME -> increment identifier for this module's own identity.

    Accepts plain or annotated constants, and recognises a constant by its value
    when it matches an increment the module's own acceptance docstrings cite, not
    only by an ``INCREMENT``-shaped variable name.
    """
    own = {}
    cited = _docstring_increments(tree)
    for node in tree.body:
        assigned = _assigned_name_value(node)
        if assigned is None:
            continue
        name, value = assigned
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and INCREMENT_ID.fullmatch(value.value)
            and ("INCREMENT" in name.upper() or value.value in cited)
        ):
            own[name] = value.value
    return own


def _is_pointer_expr(
    node: ast.AST | None,
    pointer_names: set,
    reader_names: frozenset = frozenset(),
) -> bool:
    """True when an expression is the global ACTIVE_INCREMENT value.

    Recognizes (1) a known pointer-derived local, (2) an expression that directly
    mentions ``ACTIVE_INCREMENT``, (3) a direct ``read_increment_pointer()`` call,
    (4) an attribute-qualified call whose attribute is ``read_increment_pointer``,
    (5) a call to a local helper that returns the pointer, and (6) any expression
    derived transitively from one of those. The fixpoints in ``_pointer_derived_names``
    and ``_module_pointer_names`` fold transitive derivations into ``pointer_names``.
    """
    if node is None:
        return False
    if any(
        isinstance(child, ast.Name) and child.id in pointer_names
        for child in ast.walk(node)
    ):
        return True
    return _mentions_pointer(node, reader_names)


def _is_exact_own_identity(node: ast.AST, own: dict) -> bool:
    """Equality/inequality/membership: this module's own increment name, exact
    identifier, or a literal tuple/list/set of them."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(
            elt is not None and _is_exact_own_identity(elt, own) for elt in node.elts
        )
    if isinstance(node, ast.Name) and node.id in own:
        return True
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in set(own.values())
    )


def _is_own_lineage_prefix(candidate: str, own_value: str) -> bool:
    """A substantive own-lineage prefix: segment-aligned and more specific than "ATDD-"."""
    if not candidate.endswith("-") or not own_value.startswith(candidate):
        return False
    if candidate == own_value:
        return False
    tokens = [token for token in candidate.split("-") if token]
    return len(tokens) >= _MIN_PREFIX_TOKENS


def _is_own_lineage_suffix(candidate: str, own_value: str) -> bool:
    """A substantive own-lineage suffix: segment-aligned and more specific than a bare word."""
    if not candidate.startswith("-") or not own_value.endswith(candidate):
        return False
    if candidate == own_value:
        return False
    tokens = [token for token in candidate.split("-") if token]
    return len(tokens) >= _MIN_SUFFIX_TOKENS


def _is_own_identity_or_lineage(node: ast.AST, own: dict, *, method: str) -> bool:
    """startswith/endswith: exact own identity or a substantive own lineage prefix/suffix.

    A tuple/list of prefixes (as accepted by ``str.startswith``/``str.endswith``) is
    inspected element by element. Generic fragments ("ATDD-", "", single characters)
    and unrelated lineages are accepted: they are not materially more specific than
    the shared namespace and do not correspond to this module's own increment lineage.
    """
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(
            elt is not None and _is_own_identity_or_lineage(elt, own, method=method)
            for elt in node.elts
        )
    if isinstance(node, ast.Name) and node.id in own:
        return True
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    candidate = node.value
    if candidate in set(own.values()):
        return True
    if len(candidate) < _MIN_LINEAGE_LENGTH:
        return False
    for own_value in set(own.values()):
        if method == "startswith" and _is_own_lineage_prefix(candidate, own_value):
            return True
        if method == "endswith" and _is_own_lineage_suffix(candidate, own_value):
            return True
    return False


def _pointer_derived_names(
    func: ast.AST, seed: set, reader_names: frozenset = frozenset()
) -> set:
    """Fixpoint of locals whose value came from the global pointer."""
    names = set(seed)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(func):
            assigned = _assigned_name_value(node)
            if assigned is None:
                continue
            name, value = assigned
            if name in names:
                continue
            if _mentions_pointer(value, reader_names) or any(
                isinstance(child, ast.Name) and child.id in names
                for child in ast.walk(value)
            ):
                names.add(name)
                changed = True
    return names


def _module_pointer_names(tree: ast.Module, reader_names: frozenset = frozenset()) -> set:
    """Fixpoint of module-level names whose value came from the global pointer."""
    names: set = set()
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            assigned = _assigned_name_value(node)
            if assigned is None:
                continue
            name, value = assigned
            if name in names:
                continue
            if _mentions_pointer(value, reader_names) or any(
                isinstance(child, ast.Name) and child.id in names
                for child in ast.walk(value)
            ):
                names.add(name)
                changed = True
    return names


def _pointer_reader_functions(tree: ast.Module, module_names: set) -> frozenset:
    """Local functions that return the global pointer (or a pointer-derived value).

    Resolved to a fixpoint so a helper that wraps another pointer helper is also a
    reader.
    """
    readers: set = set()
    changed = True
    while changed:
        changed = False
        known = frozenset(readers)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if func.name in readers:
                continue
            local = _pointer_derived_names(func, module_names, known)
            for child in ast.walk(func):
                if isinstance(child, ast.Return) and child.value is not None:
                    if _mentions_pointer(child.value, known) or any(
                        isinstance(node, ast.Name) and node.id in local
                        for node in ast.walk(child.value)
                    ):
                        readers.add(func.name)
                        changed = True
                        break
    return frozenset(readers)


def _function_params(func: ast.AST) -> tuple:
    """The positional parameter names of a function, in call order."""
    args = func.args
    order = [arg.arg for arg in args.posonlyargs] + [arg.arg for arg in args.args]
    return tuple(order)


def _walk_pointer_scopes(
    node: ast.AST,
    pointer_names: set,
    reader_names: frozenset,
    own: dict,
    pin_helpers: dict,
    violations: list,
) -> None:
    """Track pointer-derived locals per function scope and flag pins anywhere in it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _walk_pointer_scopes(
                child,
                _pointer_derived_names(child, pointer_names, reader_names),
                reader_names,
                own,
                pin_helpers,
                violations,
            )
            continue
        _check_pin_node(
            child, pointer_names, reader_names, own, pin_helpers, violations
        )
        _walk_pointer_scopes(
            child, pointer_names, reader_names, own, pin_helpers, violations
        )


def _pinning_helper_functions(
    tree: ast.Module, own: dict, reader_names: frozenset
) -> dict:
    """Local helpers that pin a parameter to this module's own increment identity.

    For each such helper the guard records which parameters are pinned and their
    call order, so a call that passes the pointer into a pinned parameter is caught
    even though the pointer never appears inside the helper.
    """
    helpers: dict = {}
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        order = _function_params(func)
        if not order:
            continue
        # One probe with every parameter seeded: only functions that contain a
        # pin-shaped comparison pay for the per-parameter probes below.
        probe_all: list = []
        _walk_pointer_scopes(func, set(order), reader_names, own, {}, probe_all)
        if not probe_all:
            continue
        pinned = set()
        for param in order:
            probe: list = []
            _walk_pointer_scopes(func, {param}, reader_names, own, {}, probe)
            if probe:
                pinned.add(param)
        if pinned:
            helpers[func.name] = (frozenset(pinned), order)
    return helpers


def _check_pin_call(
    node: ast.Call,
    pointer_names: set,
    reader_names: frozenset,
    own: dict,
    pin_helpers: dict,
    violations: list,
) -> None:
    func = node.func
    if isinstance(func, ast.Attribute):
        method = func.attr
        if method not in _PIN_METHODS or not node.args:
            return
        if _is_pointer_expr(
            func.value, pointer_names, reader_names
        ) and _is_own_identity_or_lineage(node.args[0], own, method=method):
            violations.append(
                f"line {node.lineno}: global ACTIVE_INCREMENT "
                f"{method}-compared to this module's own increment identity"
            )
        return
    if not isinstance(func, ast.Name):
        return
    helper = pin_helpers.get(func.id)
    if helper is None:
        return
    pinned, order = helper
    for index, arg in enumerate(node.args):
        if (
            index < len(order)
            and order[index] in pinned
            and _is_pointer_expr(arg, pointer_names, reader_names)
        ):
            violations.append(
                f"line {node.lineno}: global ACTIVE_INCREMENT passed to a helper "
                "that pins it to this module's own increment identity"
            )
            return
    for keyword in node.keywords:
        if keyword.arg in pinned and _is_pointer_expr(
            keyword.value, pointer_names, reader_names
        ):
            violations.append(
                f"line {node.lineno}: global ACTIVE_INCREMENT passed to a helper "
                "that pins it to this module's own increment identity"
            )
            return


def _check_pin_node(
    node: ast.AST,
    pointer_names: set,
    reader_names: frozenset,
    own: dict,
    pin_helpers: dict,
    violations: list,
) -> None:
    """Flag one comparison or call that pins the pointer to this module's identity."""
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        for left, op, right in zip(operands, node.ops, operands[1:]):
            if not isinstance(op, _PIN_COMPARISONS):
                continue
            if _is_exact_own_identity(left, own) and _is_pointer_expr(
                right, pointer_names, reader_names
            ):
                left, right = right, left
            if _is_pointer_expr(left, pointer_names, reader_names) and (
                _is_exact_own_identity(right, own)
            ):
                violations.append(
                    f"line {node.lineno}: global ACTIVE_INCREMENT "
                    "compared to this module's own increment identity"
                )
    elif isinstance(node, ast.Call):
        _check_pin_call(node, pointer_names, reader_names, own, pin_helpers, violations)


def global_pointer_pin_violations(source: str) -> list:
    """Return fixed, non-secret descriptions of global-pointer pins.

    Catches only the obsolete completed-increment pattern: a test that reads the
    global ACTIVE_INCREMENT value and compares it to that module's own increment
    identity. It accepts pointer mechanics, scope-control fixtures, and the R0/R1
    pattern that merely verifies the pointer resolves to an existing contract.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ["unparseable source"]

    own = _own_increment_constants(tree)
    if not own:
        return []

    module_names = _module_pointer_names(tree)
    reader_names = _pointer_reader_functions(tree, module_names)
    pin_helpers = _pinning_helper_functions(tree, own, reader_names)
    violations: list = []
    _walk_pointer_scopes(
        tree,
        _module_pointer_names(tree, reader_names),
        reader_names,
        own,
        pin_helpers,
        violations,
    )
    return sorted(set(violations))


def _scanned_test_files() -> list:
    """Every increment acceptance module except this scope-control module.

    This module hosts the scope-control fixtures and the synthetic examples the
    guard is proved against, so it is exempt; every product increment test module,
    including F3, F4, F5, F6 and R0/R1, is scanned with no filename special case.
    """
    here = Path(__file__).resolve()
    return sorted(
        path
        for path in (APP_ROOT / "tests").glob("test_*.py")
        if path.resolve() != here
    )


STALE_PIN_EXAMPLE = (
    "from pathlib import Path\n"
    "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
    "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
    "def test_x():\n"
    "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
    "    assert active == INCREMENT\n"
)
LINEAGE_PIN_EXAMPLE = (
    "from pathlib import Path\n"
    "IMPLEMENTATION_INCREMENT = 'ATDD-R3-F4-opportunity-lifecycle-implementation'\n"
    "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
    "def test_y():\n"
    "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
    "    assert active == IMPLEMENTATION_INCREMENT or active.startswith(\n"
    "        'ATDD-R3-F4-opportunity-lifecycle-'\n"
    "    )\n"
)
MOVABLE_POINTER_EXAMPLE = (
    "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
    "def test_z(atdd):\n"
    "    pointer = _read(atdd / 'ACTIVE_INCREMENT').strip()\n"
    "    assert (atdd / 'scope-contracts' / f'{pointer}.md').is_file(), pointer\n"
)
SCOPE_CHECKER_EXAMPLE = (
    "INCREMENT = 'ATDD-000-scope-control'\n"
    "def test_w(contract, index):\n"
    "    assert check_contracts((contract,), index, ('a.py',),\n"
    "                           active_increment='ATDD-SYNTHETIC').status == 'PASS'\n"
)

# Synthetic negatives the AST guard MUST reject. Each reads the global pointer and
# compares it to the module's own increment identity through a different expression
# shape: a pointer-derived local, a direct pointer expression, a direct call, an
# attribute-qualified call, a transitive call, and an own-lineage prefix.
SYNTHETIC_PIN_REJECTS = {
    "A_pointer_local_equality": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active == INCREMENT\n"
    ),
    "B_direct_pointer_expression": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case(atdd):\n"
        "    assert _read(atdd / 'ACTIVE_INCREMENT').strip() == INCREMENT\n"
    ),
    "C_direct_pointer_call": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case():\n"
        "    assert read_increment_pointer() == INCREMENT\n"
    ),
    "D_transitive_pointer_call_inequality": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case():\n"
        "    active = read_increment_pointer()\n"
        "    assert active != INCREMENT\n"
    ),
    "E_own_lineage_prefix": (
        "from pathlib import Path\n"
        "IMPLEMENTATION_INCREMENT = 'ATDD-R3-F4-opportunity-lifecycle-implementation'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active.startswith('ATDD-R3-F4-opportunity-lifecycle-')\n"
    ),
    "F_exact_own_identity": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-F6-forecast-engine'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active == INCREMENT\n"
    ),
    "G_attribute_qualified_pointer_call": (
        "import tests.atdd_scope as atdd\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case():\n"
        "    assert atdd.read_increment_pointer() == INCREMENT\n"
    ),
    "H_annotated_local_equality": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active: str = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active == INCREMENT\n"
    ),
    "I_helper_returns_pointer": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def _active():\n"
        "    return ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "def test_case():\n"
        "    assert _active() == INCREMENT\n"
    ),
    "J_non_assert_if_check": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    if active != INCREMENT:\n"
        "        raise AssertionError('the pointer moved')\n"
    ),
    "K_membership_own_in_pointer": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    assert INCREMENT in ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8')\n"
    ),
    "L_annotated_own_increment_constant": (
        "from pathlib import Path\n"
        "INCREMENT: str = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active == INCREMENT\n"
    ),
    "M_transitive_reader_helper": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def _active():\n"
        "    return ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "def _current():\n"
        "    return _active()\n"
        "def test_case():\n"
        "    assert _current() == INCREMENT\n"
    ),
    "N_membership_in_own_id_tuple": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "IMPLEMENTATION_INCREMENT = 'ATDD-R3-EXAMPLE-implementation'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active in (INCREMENT, IMPLEMENTATION_INCREMENT)\n"
    ),
    "O_membership_in_own_id_set": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active not in {INCREMENT}\n"
    ),
    "P_chained_comparison": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case(contract):\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert contract.increment == active == INCREMENT\n"
    ),
    "Q_own_lineage_suffix": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-F6-forecast-engine'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active.endswith('-forecast-engine')\n"
    ),
    "R_own_identity_from_docstring": (
        "from pathlib import Path\n"
        "SCOPE_ID = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    \"\"\"ATDD-R3-EXAMPLE-increment/AC-001: a synthetic pin.\"\"\"\n"
        "    assert read_increment_pointer() == SCOPE_ID\n"
    ),
    "S_helper_parameter_pin": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def _check(value):\n"
        "    assert value == INCREMENT\n"
        "def test_case():\n"
        "    _check(ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip())\n"
    ),
    "T_startswith_tuple_of_own_ids": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "IMPLEMENTATION_INCREMENT = 'ATDD-R3-EXAMPLE-implementation'\n"
        "ACTIVE_INCREMENT_PATH = Path('docs') / 'atdd' / 'ACTIVE_INCREMENT'\n"
        "def test_case():\n"
        "    active = ACTIVE_INCREMENT_PATH.read_text(encoding='utf-8').strip()\n"
        "    assert active.startswith((INCREMENT, IMPLEMENTATION_INCREMENT))\n"
    ),
}

# Synthetic positives the AST guard MUST accept: pointer mechanics, generic
# namespace checks, unrelated lineages, and the R0/R1 lifecycle-safe resolution.
SYNTHETIC_PIN_ACCEPTS = {
    "A_pointer_resolution_only": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case(atdd):\n"
        "    pointer = _read(atdd / 'ACTIVE_INCREMENT').strip()\n"
        "    assert (atdd / 'scope-contracts' / f'{pointer}.md').is_file()\n"
    ),
    "B_generic_namespace_prefix": (
        "INCREMENT = 'ATDD-R3-F6-forecast-engine'\n"
        "def test_case():\n"
        "    pointer = read_increment_pointer()\n"
        "    assert pointer.startswith('ATDD-')\n"
    ),
    "C_empty_string_check": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case():\n"
        "    pointer = read_increment_pointer()\n"
        "    assert pointer != ''\n"
    ),
    "D_unrelated_lineage": (
        "INCREMENT = 'ATDD-R3-F6-forecast-engine'\n"
        "def test_case():\n"
        "    pointer = read_increment_pointer()\n"
        "    assert pointer.startswith('ATDD-BRIDGE-')\n"
    ),
    "E_scope_checker_mechanics": SCOPE_CHECKER_EXAMPLE,
    "F_r0r1_lifecycle_safe": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R0R1-audit-reconciliation'\n"
        "ATDD = Path('docs') / 'atdd'\n"
        "def _read(path):\n"
        "    return path.read_text(encoding='utf-8')\n"
        "def test_case():\n"
        "    pointer = _read(ATDD / 'ACTIVE_INCREMENT').strip()\n"
        "    assert (ATDD / 'scope-contracts' / f'{pointer}.md').is_file(), pointer\n"
    ),
    "G_membership_of_own_in_non_pointer": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "CONTRACT = 'docs/atdd/scope-contracts/example.md'\n"
        "def test_case():\n"
        "    assert INCREMENT in CONTRACT\n"
    ),
    "H_annotated_pointer_resolution_only": (
        "from pathlib import Path\n"
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case(atdd):\n"
        "    active: str = _read(atdd / 'ACTIVE_INCREMENT').strip()\n"
        "    assert (atdd / 'scope-contracts' / f'{active}.md').is_file()\n"
    ),
    "I_annotated_own_constant_no_pin": (
        "INCREMENT: str = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case(contract):\n"
        "    assert contract.increment == INCREMENT\n"
    ),
    "J_membership_in_non_own_tuple": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case():\n"
        "    pointer = read_increment_pointer()\n"
        "    assert pointer in ('ATDD-R3-OTHER-a', 'ATDD-R3-OTHER-b')\n"
    ),
    "K_generic_suffix": (
        "INCREMENT = 'ATDD-R3-F6-forecast-engine'\n"
        "def test_case():\n"
        "    pointer = read_increment_pointer()\n"
        "    assert pointer.endswith('-engine')\n"
    ),
    "L_helper_called_with_non_pointer": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def _check(value):\n"
        "    assert value == INCREMENT\n"
        "def test_case(contract):\n"
        "    _check(contract.increment)\n"
    ),
    "M_startswith_generic_tuple": (
        "INCREMENT = 'ATDD-R3-EXAMPLE-increment'\n"
        "def test_case():\n"
        "    pointer = read_increment_pointer()\n"
        "    assert pointer.startswith(('ATDD-', 'OTHER-'))\n"
    ),
}

SCANNED_INCREMENT_MODULES = (
    "test_opip_r3_f3_ignition_detector.py",
    "test_opip_r3_f4_opportunity_lifecycle.py",
    "test_opip_r3_f4_opportunity_persistence.py",
    "test_opip_r3_f5_feasibility.py",
    "test_opip_r3_f6_forecast_engine.py",
    "test_opip_r0r1_audit_reconciliation.py",
)

GOVERNANCE_ID = "ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1"
F5_ID = "ATDD-R3-F5-feasibility-safety"
F6_ID = "ATDD-R3-F6-forecast-engine"
GOVERNANCE_PATHS = frozenset(
    {
        "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
        "OHM-Trade-Agent-v1/docs/atdd/README.md",
        "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/"
        "ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md",
        "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F5-feasibility-safety.md",
        "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F6-forecast-engine.md",
        "OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py",
        "OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py",
    }
)


def _governance_contract_path() -> Path:
    increment = (
        APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
    ).read_text(encoding="utf-8").strip()
    return CONTRACT_DIR / f"{increment}.md"


@pytest.mark.acceptance
def test_movable_pointer_governance_contract_and_map() -> None:
    """ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1/AC-001: the pointer resolves and the map is minimal."""
    # The pointer is movable, so it is not pinned here; it must merely resolve.
    resolved = _governance_contract_path()
    assert resolved.is_file(), resolved

    contract_path = CONTRACT_DIR / f"{GOVERNANCE_ID}.md"
    assert contract_path.is_file()
    contract = parse_scope_contract(contract_path.read_text(encoding="utf-8"))
    assert contract.increment == GOVERNANCE_ID

    allowed = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    assert allowed == GOVERNANCE_PATHS
    # No runtime, deployment or architecture path is authorized.
    for path in allowed:
        assert not path.startswith(
            ("OHM-Trade-Agent-v1/app/", ".github/", "OHM-Trade-Agent-v1/deploy/")
        ), path


@pytest.mark.acceptance
def test_movable_pointer_semantics_documented() -> None:
    """ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1/AC-002: the README states the canonical movable-pointer rule."""
    readme = (APP_ROOT / "docs" / "atdd" / "README.md").read_text(encoding="utf-8")
    for required in (
        "### The active increment pointer is movable",
        "mutable orchestration state",
        "deliberately movable",
        "Exact current-increment enforcement belongs to",
        "must **not** permanently require the global pointer to equal that increment",
        "resolves to an existing scope contract",
        "own** contract and test identity",
        "fails closed if any increment test reads the global pointer",
        "prevents a future completed increment from re-introducing a global-pointer "
        "ownership pin",
    ):
        assert required in readme, required


@pytest.mark.acceptance
def test_f5_pointer_handoff_is_lifecycle_safe() -> None:
    """ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1/AC-003: F5 proves its own identity without pinning the pointer."""
    f5_test = APP_ROOT / "tests" / "test_opip_r3_f5_feasibility.py"
    source = f5_test.read_text(encoding="utf-8")

    # The obsolete permanent pin is gone, and the module is accepted by the guard.
    assert "assert active == INCREMENT" not in source
    assert global_pointer_pin_violations(source) == []

    # The lifecycle-safe resolution pattern is present instead.
    assert "scope-contracts" in source and "is_file()" in source

    # F5's own identity still comes from its own contract.
    f5_contract = (CONTRACT_DIR / f"{F5_ID}.md").read_text(encoding="utf-8")
    lines = f5_contract.splitlines()
    assert lines[0].strip() == "INCREMENT:"
    assert lines[1].strip() == F5_ID
    # Its AC-027 no longer claims the pointer names F5.
    ac027 = f5_contract.split("AC-027:")[1].split("AC-028:")[0]
    assert "the ATDD pointer names this F5 increment" not in ac027

    # The F4 no-ownership proof and isolation assertions are preserved.
    for required in (
        "FROZEN_F4_INCREMENT",
        'assert "ACTIVE_INCREMENT" not in f4_test',
        "IMPLEMENTATION_CONTRACT_PATH",
        'assert "shadow" in lowered',
        'assert "non-authoritative" in lowered',
    ):
        assert required in source, required

    # When F5 is the selected active increment, scope control requires the F5 contract
    # and nothing weaker; an unknown active increment still fails closed.
    contracts = load_scope_contracts(CONTRACT_DIR)
    index = load_acceptance_index(APP_ROOT / "tests")
    f5_path = "OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py"
    selected = [c for c in contracts if c.increment == F5_ID]
    assert len(selected) == 1
    assert check_contracts(
        contracts, index, (f5_path,), active_increment=F5_ID
    ).status == "PASS"
    assert check_contracts(
        contracts, index, (f5_path,), active_increment="ATDD-NOT-A-CONTRACT"
    ).status != "PASS"


@pytest.mark.acceptance
def test_f6_pointer_handoff_is_lifecycle_safe() -> None:
    """ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1/AC-005: F6 proves its own identity and keeps its isolation guarantees without pinning the pointer."""
    f6_test = APP_ROOT / "tests" / "test_opip_r3_f6_forecast_engine.py"
    source = f6_test.read_text(encoding="utf-8")

    # The completed F6 increment neither owns nor pins the movable global pointer and
    # the structural guard accepts the module.
    assert "assert active == INCREMENT" not in source
    assert global_pointer_pin_violations(source) == []

    # F6's own identity comes from its own frozen scope contract.
    f6_contract = (CONTRACT_DIR / f"{F6_ID}.md").read_text(encoding="utf-8")
    lines = f6_contract.splitlines()
    assert lines[0].strip() == "INCREMENT:"
    assert lines[1].strip() == F6_ID

    # The pointer is movable and only has to resolve to an existing scope contract.
    pointer = (APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT").read_text(
        encoding="utf-8"
    ).strip()
    assert (CONTRACT_DIR / f"{pointer}.md").is_file(), pointer

    # Every substantive F6 isolation guarantee is still asserted by the module.
    for required in (
        "test_ac_030_no_ai_or_committee_authority",
        "test_ac_031_no_allocation_or_f7",
        "test_ac_032_no_runtime_integration",
        "test_ac_033_no_new_writer_db_jsonl",
        "test_ac_034_feature_bus_off",
        "test_ac_035_f3_f4_f5_semantics_unchanged",
        'OPIP_FEATURE_BUS_MODE: "off"',
    ):
        assert required in source, required

    # The F6 acceptance criteria remain traced in the F6 contract.
    for ac in ("AC-030", "AC-031", "AC-032", "AC-033", "AC-034", "AC-035"):
        assert f"{ac} ->" in f6_contract, ac


@pytest.mark.acceptance
def test_global_pointer_pin_guard() -> None:
    """ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1/AC-004: the structural guard fails closed and stays precise."""
    offenders = {}
    for path in _scanned_test_files():
        found = global_pointer_pin_violations(path.read_text(encoding="utf-8"))
        if found:
            offenders[path.name] = found
    assert offenders == {}, offenders

    # No filename special case: every increment acceptance module is scanned.
    scanned = {path.name for path in _scanned_test_files()}
    for name in SCANNED_INCREMENT_MODULES:
        assert name in scanned, name

    # Every synthetic stale pin is caught, whatever expression shape it uses.
    for name, source in SYNTHETIC_PIN_REJECTS.items():
        assert global_pointer_pin_violations(source), name
    assert global_pointer_pin_violations(STALE_PIN_EXAMPLE)
    assert global_pointer_pin_violations(LINEAGE_PIN_EXAMPLE)

    # Legitimate forms, including generic namespaces and unrelated lineages, are accepted.
    for name, source in SYNTHETIC_PIN_ACCEPTS.items():
        assert global_pointer_pin_violations(source) == [], name
    assert global_pointer_pin_violations(MOVABLE_POINTER_EXAMPLE) == []
    assert global_pointer_pin_violations(SCOPE_CHECKER_EXAMPLE) == []
    assert global_pointer_pin_violations(Path(__file__).read_text(encoding="utf-8")) == []
