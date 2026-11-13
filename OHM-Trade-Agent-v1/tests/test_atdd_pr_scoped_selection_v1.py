"""ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1: PR-scoped scope-contract selection.

Pure/stdlib fixtures only. No application runtime is imported.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    CONTRACT_REPO_PREFIX,
    POINTER_REPO_PATH,
    SCOPE_CHANGE_REQUIRED,
    AcceptanceIndex,
    check_contracts,
    git_changed_paths,
    main,
    parse_scope_contract,
    resolve_pr_increment,
)

pytestmark = pytest.mark.acceptance
_ATDD_ROOT = Path("docs") / "atdd"


def _contract(increment: str, impl: str) -> str:
    node = f"tests/test_{increment}.py::test_it"
    body = (
        ("INCREMENT", increment),
        ("OWNER-APPROVED INTENT", "Synthetic."),
        ("ARCHITECTURE REFERENCES", "- synthetic"),
        ("APPROVED ACCEPTANCE CRITERIA",
         "AC-001:\nGIVEN:\na\nWHEN:\nb\nTHEN:\nc"),
        ("EXPLICITLY OUT OF SCOPE", "- none"),
        ("FROZEN BOUNDARIES", "- none"),
        ("ACCEPTANCE TEST TRACEABILITY", f"AC-001 -> {node}"),
        ("IMPLEMENTATION MAP", f"AC-001 -> {impl}\nAC-001 -> {CONTRACT_REPO_PREFIX}{increment}.md"),
        ("DEFERRED DISCOVERIES", "- none"),
        ("UNAPPROVED SCOPE CHANGES", "NONE"),
    )
    return "\n\n".join(f"{k}:\n{v}" for k, v in body) + "\n"


def _files(*pairs):
    return {inc: parse_scope_contract(_contract(inc, impl)) for inc, impl in pairs}


def _index(*incs):
    return AcceptanceIndex(
        by_ac={(i, "AC-001"): frozenset({f"tests/test_{i}.py::test_it"}) for i in incs},
        unscoped_tests=(),
    )


def _pointer(value):
    def read():
        if value is None:
            raise ValueError("active increment pointer is missing")
        return value
    return read


def _contract_path(inc):
    return f"{CONTRACT_REPO_PREFIX}{inc}.md"


A = ("INC-A", "OHM-Trade-Agent-v1/app/a.py")
B = ("INC-B", "OHM-Trade-Agent-v1/app/b.py")


def _check(files, changed, pointer):
    active = resolve_pr_increment(files, changed, _pointer(pointer))
    return check_contracts(
        tuple(files.values()), _index(*files), changed, active_increment=active
    )


def test_ac_001_single_increment_pr_resolves_its_own_contract():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-001: one changed contract selects that increment."""
    files = _files(A)
    changed = (_contract_path("INC-A"), "OHM-Trade-Agent-v1/app/a.py")
    assert resolve_pr_increment(files, changed, _pointer(None)) == "INC-A"
    assert _check(files, changed, None).status == "PASS"


def test_ac_001_concurrent_increments_coexist_without_a_shared_pointer():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-001: two PRs pass whatever the pointer holds."""
    files = _files(A, B)
    pr_a = (_contract_path("INC-A"), "OHM-Trade-Agent-v1/app/a.py")
    pr_b = (_contract_path("INC-B"), "OHM-Trade-Agent-v1/app/b.py")
    for pointer in ("INC-A", "INC-B", "INC-OLD-GONE", None):
        assert _check(files, pr_a, pointer).status == "PASS"
        assert _check(files, pr_b, pointer).status == "PASS"


def test_ac_001_legacy_global_pointer_collision_is_reproduced_and_repaired():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-001: the shared pointer authorizes only one of two PRs."""
    files = _files(A, B)
    pr_b = (_contract_path("INC-B"), "OHM-Trade-Agent-v1/app/b.py")
    legacy = check_contracts(
        tuple(files.values()), _index(*files), pr_b, active_increment="INC-A"
    )
    assert legacy.status == SCOPE_CHANGE_REQUIRED  # collision: pointer owned by PR A
    assert _check(files, pr_b, "INC-A").status == "PASS"  # repaired


def test_ac_001_stale_pointer_is_ignored_when_the_pr_has_its_own_contract():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-001: an unchanged stale pointer cannot invalidate a PR."""
    files = _files(A)
    changed = (_contract_path("INC-A"), "OHM-Trade-Agent-v1/app/a.py")
    assert _check(files, changed, "INC-DELETED-LONG-AGO").status == "PASS"


def test_ac_002_ambiguous_changed_contracts_fail_closed():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-002: two changed contracts are ambiguous."""
    files = _files(A, B)
    changed = (_contract_path("INC-A"), _contract_path("INC-B"))
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_pr_increment(files, changed, _pointer("INC-A"))


def test_ac_002_contract_stem_must_match_increment():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-002: a renamed or copied contract cannot impersonate another."""
    files = {"INC-A": parse_scope_contract(_contract("INC-B", "OHM-Trade-Agent-v1/app/b.py"))}
    with pytest.raises(ValueError, match="does not match"):
        resolve_pr_increment(files, (_contract_path("INC-A"),), _pointer("INC-A"))


def test_ac_002_edited_pointer_must_agree_with_the_changed_contract():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-002: an edited pointer naming another increment is rejected."""
    files = _files(A, B)
    changed = (_contract_path("INC-A"), POINTER_REPO_PATH)
    with pytest.raises(ValueError, match="conflicts"):
        resolve_pr_increment(files, changed, _pointer("INC-B"))
    assert resolve_pr_increment(files, changed, _pointer("INC-A")) == "INC-A"


def test_ac_002_unauthorized_changed_file_is_rejected():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-002: a file outside the PR's own map fails closed."""
    files = _files(A, B)
    changed = (_contract_path("INC-A"), "OHM-Trade-Agent-v1/app/b.py")
    result = _check(files, changed, "INC-B")
    assert result.status == SCOPE_CHANGE_REQUIRED
    assert any("app/b.py" in reason for reason in result.scope_reasons)


def test_ac_002_new_contract_cannot_skip_its_own_authorization():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-002: the contract file must be mapped by its own contract."""
    text = _contract("INC-A", "OHM-Trade-Agent-v1/app/a.py").replace(
        f"\nAC-001 -> {CONTRACT_REPO_PREFIX}INC-A.md", ""
    )
    files = {"INC-A": parse_scope_contract(text)}
    result = _check(files, (_contract_path("INC-A"),), None)
    assert result.status == SCOPE_CHANGE_REQUIRED


def test_ac_002_missing_contract_and_missing_pointer_fail_closed(capsys):
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-002: no contract and no pointer is not a pass."""
    files = _files(A)
    with pytest.raises(ValueError, match="missing"):
        resolve_pr_increment(files, ("OHM-Trade-Agent-v1/app/a.py",), _pointer(None))
    assert main(["--pr-scoped"]) == 2
    assert "changed-file set" in capsys.readouterr().out


def test_ac_003_legacy_fallback_and_cli_contracts_are_unchanged():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-003: no changed contract uses the pointer; flags stay exclusive."""
    files = _files(A, B)
    plain = ("OHM-Trade-Agent-v1/app/a.py",)
    assert resolve_pr_increment(files, plain, _pointer("INC-A")) == "INC-A"
    assert _check(files, plain, "INC-A").status == "PASS"
    assert _check(files, plain, "INC-B").status == SCOPE_CHANGE_REQUIRED
    assert main(["--pr-scoped", "--increment", "X", "a.py"]) == 2


def test_ac_003_workflow_uses_pr_scoped_mode_with_no_bypass():
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-003: CI runs the scoped checker against the PR diff."""
    wf = (APP_ROOT.parent / ".github" / "workflows" / "pytest.yml").read_text(encoding="utf-8")
    assert "python tests/atdd_scope.py --pr-scoped --git-base" in wf
    assert "continue-on-error" not in wf.split("atdd-scope:")[1].split("architecture-gate:")[0]


def test_ac_004_selection_is_stable_across_rebase_and_merge_order(tmp_path):
    """ATDD-OPIP-ATDD-PR-SCOPED-SELECTION-v1/AC-004: the base moving its pointer does not change a PR's verdict."""
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.x", *a],
                       cwd=tmp_path, check=True, capture_output=True, text=True)

    git("init", "-b", "main")
    pointer = tmp_path / POINTER_REPO_PATH
    pointer.parent.mkdir(parents=True)
    pointer.write_text("INC-OLD\n")
    git("add", "-A"); git("commit", "-m", "base")
    git("checkout", "-b", "pr-a")
    cpath = tmp_path / _contract_path("INC-A")
    cpath.parent.mkdir(parents=True)
    cpath.write_text(_contract(*A))
    app = tmp_path / "OHM-Trade-Agent-v1/app/a.py"
    app.parent.mkdir(parents=True)
    app.write_text("x\n")
    git("add", "-A"); git("commit", "-m", "pr a")
    files = _files(A)
    base_old = subprocess.check_output(["git", "rev-parse", "main"], cwd=tmp_path, text=True).strip()
    changed_old = git_changed_paths(tmp_path, base_old)
    # main advances: another PR merges and moves the global pointer; PR A is rebased.
    git("checkout", "main"); pointer.write_text("INC-B\n"); git("commit", "-am", "other merged")
    git("checkout", "pr-a"); git("rebase", "main")
    changed_new = git_changed_paths(tmp_path, "main")
    assert set(changed_old) == set(changed_new)
    assert POINTER_REPO_PATH not in changed_new
    for pointer_value in ("INC-OLD", "INC-B"):
        assert resolve_pr_increment(files, changed_new, _pointer(pointer_value)) == "INC-A"
