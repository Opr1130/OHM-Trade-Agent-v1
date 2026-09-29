"""R3 F4 Opportunity Lifecycle: contract-stage acceptance skeletons.

``ATDD-R3-F4-opportunity-lifecycle`` freezes the acceptance criteria for the
pure opportunity-lifecycle transition surface over ``DetectorClaim`` evidence
(episode identity, dedup, deferral, deadline, expiry, terminal reason).

The lifecycle implementation is a later, OWNER-approved implementation
increment. Every criterion below is therefore an explicit, non-executing
skeleton: each test skips with the same stated reason and no skeleton
fabricates a lifecycle result or asserts behaviour that is not implemented
yet. The implementation increment completes these bodies in place; it does
not replace this file.

The guard test is the sole executing check in this freeze: it proves AC
count, contract mapping and that no F4 runtime was smuggled in.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

INCREMENT = "ATDD-R3-F4-opportunity-lifecycle"
EXPECTED_AC_COUNT = 17
_DEFERRED = (
    "R3-F4 runtime implementation is deferred to a separate OWNER-approved "
    "implementation increment"
)

APP_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
)
THIS_FILE = Path(__file__).resolve()


@pytest.mark.acceptance
def test_ac_001_episode_identity_is_deterministic() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-001: episode identity is deterministic and never wall-clock/UUID/process/order/retry."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_002_deduplication_is_at_least_once_safe() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-002: at-least-once delivery creates no duplicate episodes or extra work."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_003_claim_episode_lineage_reuses_identities() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-003: claim→episode lineage reuses identities and does not reinterpret F3."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_004_lifecycle_state_vocabulary() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-004: lifecycle state covers active/open, deferred and terminal concepts."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_005_deferral_is_explicit_and_not_detector_dormant() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-005: deferral is explicit F4 disposition, not detector DORMANT."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_006_deferred_deadlines_are_bounded() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-006: deferred episodes carry deadlines bounded by validity horizon."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_007_expiry_is_explicit_terminal() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-007: expiry is explicit terminal with no silent resume."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_008_terminal_reasons_are_f4_vocabulary() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-008: terminal reasons use F4 vocabulary, not funnel/scan codes."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_009_terminality_requires_new_lifecycle() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-009: terminal is terminal; a new lifecycle needs an eligible new claim."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_010_transition_core_is_pure() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-010: pure transition core performs no I/O and reads no hidden state."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_011_replay_is_deterministic() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-011: identical inputs replay to equivalent episode state and events."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_012_f3_boundary_unchanged() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-012: F3 detector contracts and semantics remain unmodified."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_013_f5_plus_isolation() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-013: F5+/Paper/Committee/Feature Bus authorities are unchanged."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_014_current_vs_target_authority() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-014: contract stage transfers no authority from legacy clocks."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_015_consumer_census_recorded() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-015: consumer census is recorded for future cutover planning."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_016_cutover_retirement_boundary() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-016: no retirement or deletion; only cutover prerequisites are frozen."""
    pytest.skip(_DEFERRED)


@pytest.mark.acceptance
def test_ac_017_no_fake_implementation() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-017: skeletons share one skip reason and fabricate no lifecycle results."""
    pytest.skip(_DEFERRED)


def test_contract_freeze_guard_ac_count_and_no_runtime() -> None:
    """Guard: expected AC count, contract mapping, shared skip reason, no F4 runtime smuggled."""
    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    assert f"INCREMENT:\n{INCREMENT}" in contract or f"INCREMENT:\r\n{INCREMENT}" in contract

    contract_acs = sorted(set(re.findall(r"^AC-(\d{3}):\s*$", contract, flags=re.MULTILINE)))
    assert contract_acs == [f"{i:03d}" for i in range(1, EXPECTED_AC_COUNT + 1)], (
        f"contract AC set mismatch: {contract_acs!r}"
    )

    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"), filename=str(THIS_FILE))
    skipped_acs: list[str] = []
    skip_reasons: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("test_ac_"):
            continue
        doc = ast.get_docstring(node) or ""
        match = re.search(rf"{re.escape(INCREMENT)}/AC-(\d{{3}})", doc)
        assert match, f"{node.name} docstring must cite {INCREMENT}/AC-NNN"
        skipped_acs.append(match.group(1))

        has_acceptance = False
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Attribute)
                and dec.attr == "acceptance"
                and isinstance(dec.value, ast.Attribute)
                and dec.value.attr == "mark"
                and isinstance(dec.value.value, ast.Name)
                and dec.value.value.id == "pytest"
            ):
                has_acceptance = True
        assert has_acceptance, f"{node.name} must be @pytest.mark.acceptance"

        skip_calls = [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "skip"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "pytest"
        ]
        assert len(skip_calls) == 1, f"{node.name} must skip exactly once"
        arg = skip_calls[0].args[0]
        if isinstance(arg, ast.Name) and arg.id == "_DEFERRED":
            skip_reasons.add(_DEFERRED)
        elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            skip_reasons.add(arg.value)
        else:
            raise AssertionError(f"{node.name} must skip with _DEFERRED or a string literal")

    assert sorted(skipped_acs) == [f"{i:03d}" for i in range(1, EXPECTED_AC_COUNT + 1)]
    assert skip_reasons == {_DEFERRED}

    # No F4 application runtime may appear in this freeze.
    opportunity_pkg = APP_ROOT / "app" / "opip" / "opportunity"
    assert not opportunity_pkg.exists(), "F4 runtime package must not be smuggled in"
    for smuggled in (
        APP_ROOT / "app" / "opip" / "contracts" / "opportunity.py",
        APP_ROOT / "app" / "opip" / "opportunity" / "lifecycle.py",
    ):
        assert not smuggled.exists(), f"smuggled runtime path present: {smuggled}"

    source_tree = ast.parse(
        THIS_FILE.read_text(encoding="utf-8"),
        filename=str(THIS_FILE),
    )
    for node in ast.walk(source_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.opip.opportunity"), alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("app.opip.opportunity"), module
