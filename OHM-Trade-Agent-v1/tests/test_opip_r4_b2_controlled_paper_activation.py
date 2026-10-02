"""R4-B2 controlled paper activation: acceptance guards for the freeze artifact.

This module proves the R4-B2 scope contract and the unchanged frozen posture. The
activation implementation is a later commit of the same increment; these guards
assert the frozen sequence, authority, preconditions, proofs, rollback and
exclusions, and that this freeze PR activates nothing and grants no authority.

Where a frozen claim is about existing code, the guard proves it against that
code (mode vocabulary, single-authority routing) rather than only matching prose.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.paper_v2_cutover_readiness as cutover

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
ATDD = APP_ROOT / "docs" / "atdd"
SCOPE_CONTRACTS = ATDD / "scope-contracts"
CONTRACT = SCOPE_CONTRACTS / "ATDD-R4-B2-controlled-paper-activation.md"
R4B1_FREEZE = SCOPE_CONTRACTS / "ATDD-R4-B1-contract-freeze.md"
F11_CONTRACT = SCOPE_CONTRACTS / "ATDD-R4-F11-precutover-protection.md"
ACTIVATION = APP_ROOT / "app" / "services" / "paper_v2_activation.py"
SCAN = APP_ROOT / "app" / "jobs" / "scan_opportunities.py"

FORBIDDEN_AUTHORITY_TOKENS = (
    "kraken_private",
    "exchanges",
    "committee",
    "order",
)

#: The R4-B2 acceptance module may import only the frozen contract surface and
#: the read-only seams it anchors to.
ALLOWED_APP_IMPORT_PREFIXES = (
    "app.opip.contracts.",
    "app.jobs.scan_opportunities",
    "app.services.paper_v2_",
    "app.services.protection_health",
    "app.services.system_incidents",
)


def _pointer() -> str:
    return (ATDD / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()


def _contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _app_imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_ac_001_contract_and_pointer_are_consistent():
    """ATDD-R4-B2-controlled-paper-activation/AC-001: the contract exists, declares its own increment, and the movable pointer resolves to an existing contract without being pinned to this increment."""
    assert CONTRACT.is_file(), CONTRACT
    assert "INCREMENT:\nATDD-R4-B2-controlled-paper-activation" in _contract_text()
    pointer = _pointer()
    assert (SCOPE_CONTRACTS / f"{pointer}.md").is_file(), pointer


def test_ac_002_activation_sequence_and_vocabulary():
    """ATDD-R4-B2-controlled-paper-activation/AC-002: the contract freezes the ordered activation sequence using the repository vocabulary and states that activation is a distinct owner switch this freeze does not set."""
    text = _contract_text()
    assert "off` -> `shadow/comparator` -> controlled PAPER target path -> verified target paper authority" in text
    assert "No new mode token is invented" in text
    assert "this freeze defines the gates and proofs it must satisfy and sets nothing" in text
    # Code-anchored: the repository's canonical vocabulary is off/active.
    from app.services.paper_v2_activation import (
        PAPER_V2_MODE_ACTIVE,
        resolve_paper_v2_mode,
    )

    assert PAPER_V2_MODE_ACTIVE == "active"
    assert resolve_paper_v2_mode(None) == "off"


def test_ac_003_single_new_entry_authority():
    """ATDD-R4-B2-controlled-paper-activation/AC-003: exactly one new-entry paper authority (the target route), legacy retained as comparator/rollback only, and no second admission/allocation/reservation authority."""
    text = _contract_text()
    assert "Exactly one authority may create a new paper entry" in text
    assert "the target F7 selector as the admission source being papered" in text
    assert "one reservation authority: the canonical writer" in text
    assert "may not create new entries while the target authority is active" in text

    # Code-anchored: the resolved paper authority is mutually exclusive. For every
    # grantable value, at most one of the target-route and legacy flags is true, so
    # the runtime can never grant two new-entry authorities at once.
    import app.jobs.scan_opportunities as scan

    granted_values = (
        scan.AUTHORITY_LEGACY,
        scan.AUTHORITY_PAPER_V2_READY,
        scan.AUTHORITY_PAPER_V2_DRAINING,
        scan.AUTHORITY_PAPER_V2_UNAVAILABLE,
    )
    seen_granted: set[str] = set()
    for granted in granted_values:
        authority = scan.PaperAuthority(requested=True, granted=granted, reason="test")
        flags = (authority.paper_v2_routing, authority.legacy_new_entry_allowed)
        assert sum(flags) <= 1, (granted, flags)
        if any(flags):
            seen_granted.add(granted)
    # Exactly the two single-authority grants may ever authorize a new entry.
    assert seen_granted == {scan.AUTHORITY_LEGACY, scan.AUTHORITY_PAPER_V2_READY}
    # The draining/unavailable states grant no new-entry authority at all.
    for granted in (scan.AUTHORITY_PAPER_V2_DRAINING, scan.AUTHORITY_PAPER_V2_UNAVAILABLE):
        authority = scan.PaperAuthority(requested=True, granted=granted, reason="test")
        assert not authority.paper_v2_routing
        assert not authority.legacy_new_entry_allowed


def test_ac_004_cutover_preconditions_fail_closed():
    """ATDD-R4-B2-controlled-paper-activation/AC-004: activation is gated on F11 health, legacy drain READY, universe metadata, resolved direction coverage and a reachable target spine, failing closed on anything unproven."""
    text = _contract_text()
    assert "F11 protection health proven" in text
    assert "legacy drain READY" in text
    assert "universe metadata present" in text
    assert "direction coverage resolved" in text
    assert "the target spine reachable" in text
    assert "withholds activation" in text
    # The direction-coverage blocker is named explicitly.
    assert "SHORT_AUTHORITY_MISSING" in text


def test_ac_005_authority_collision_test_defined():
    """ATDD-R4-B2-controlled-paper-activation/AC-005: the contract requires a collision test proving no two independent executions for one opportunity, states F7 and Paper-v2 are one authority, and requires at most one admission per selected opportunity."""
    text = _contract_text()
    assert "two independent executions for one economic opportunity" in text
    assert "are ONE authority" in text
    assert "at most one admission" in text


def test_ac_006_execution_proofs_enumerated():
    """ATDD-R4-B2-controlled-paper-activation/AC-006: the contract enumerates the realistic execution and economic proofs, with direction-aware P&L for every authorized direction and an explicit disposition for any unauthorized direction."""
    text = _contract_text()
    for proof in (
        "realistic entry and exit side",
        "size-sensitive depth and liquidity binding",
        "fees, slippage and latency",
        "NO_FILL, PARTIAL_FILL and FULL_FILL",
        "TARGET, STOP, TIMEOUT and independent RISK_EXIT",
        "a limit touch alone insufficient for a fill",
        "restart and terminal reconciliation",
        "capacity release",
        "direction-aware P&L for every direction the activated route authorizes",
        "never silently dropped",
        "idempotency for stale or replayed input",
    ):
        assert proof in text, proof


def test_ac_007_retry_semantics_preserved():
    """ATDD-R4-B2-controlled-paper-activation/AC-007: the activated path preserves the R4-B1 retry freeze and the single reservation authority."""
    text = _contract_text()
    assert "preserves the R4-B1 freeze in full" in text
    assert "an exact retry is idempotent" in text
    assert "refused as a conflicting decision" in text
    assert "no duplicate trade, reservation or disposition is created" in text
    assert "committed ancestry and economics are immutable" in text
    # The R4-B1 freeze it references exists and is the identity authority.
    assert R4B1_FREEZE.is_file()


def test_ac_008_rollback_restores_one_authority():
    """ATDD-R4-B2-controlled-paper-activation/AC-008: rollback stops new target admissions immediately, withholds legacy new-entry authority until the rollback-ready gate, never runs two authorities, and needs no canonical-data migration."""
    text = _contract_text()
    assert "setting the mode back to `off`" in text
    assert "does not resume new entries until the rollback-ready gate" in text
    assert "must never leave two allocation authorities running" in text
    assert "no canonical-data migration" in text
    assert "obsolete code is not deleted by this increment" in text


def test_ac_009_authority_boundary_and_exclusions():
    """ATDD-R4-B2-controlled-paper-activation/AC-009: the contract forbids funded/exchange/Committee/second-authority surfaces and states F11 is not bypassed."""
    text = _contract_text()
    assert "funded trading" in text
    assert "funded exchange order authority" in text
    assert "Committee runtime authority" in text
    assert "dashboard or Telegram trading authority" in text
    assert "a second scheduler" in text
    assert "F11 is not bypassed" in text
    assert F11_CONTRACT.is_file()


def test_ac_010_freeze_sets_no_mode_and_no_authority_import():
    """ATDD-R4-B2-controlled-paper-activation/AC-010: this freeze sets no production mode (default remains off), its artifacts assign no mode and import no funded/exchange/order/Committee authority, and the current posture stays asserted by the dedicated guard."""
    text = _contract_text()
    assert "this freeze sets no production mode" in text
    assert "repository default remains `off`" in text
    # The freeze declares it activates nothing; the roadmap citation of the switch
    # is legitimate, so no raw substring ban on the token is used. Instead, prove
    # the artifacts assign no mode.
    assert "sets nothing" in text
    module_source = Path(__file__).read_text(encoding="utf-8")
    # Assembled at runtime so the guard does not match its own source text.
    for forbidden in ("set" + "env", "environ" + "[", "put" + "env"):
        assert forbidden not in module_source, forbidden
    assert not re.search(r"OPIP_[A-Z_]+MODE\s*=", module_source)
    # A dedicated current-posture guard exists and checks both mode tokens exactly;
    # that guard (not this immutable freeze) is what an activation increment moves.
    posture_guard = (
        APP_ROOT / "tests" / "test_opip_current_runtime_posture.py"
    ).read_text(encoding="utf-8")
    assert "OPIP_FEATURE_BUS_MODE" in posture_guard
    assert "OPIP_PAPER_V2_MODE" in posture_guard

    modules: set[str] = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for name in modules:
        lowered = name.lower()
        for forbidden in FORBIDDEN_AUTHORITY_TOKENS:
            assert forbidden not in lowered, (name, forbidden)
        if name.startswith("app."):
            assert name.startswith(ALLOWED_APP_IMPORT_PREFIXES), name


def test_ac_011_comparator_evidence_required():
    """ATDD-R4-B2-controlled-paper-activation/AC-011: the contract requires matched-window comparison evidence against cash and the frozen profit-ranking comparator over the full intent population before admission is replaced."""
    text = _contract_text()
    assert "matched-window comparison evidence" in text
    assert "frozen profit-ranking comparator" in text
    assert "cash/no-trade" in text
    assert "full intent population" in text
    assert "before the legacy admission source is replaced" in text


def test_ac_004_drain_requires_no_unresolved_or_reserved_legacy(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-004: a READY legacy drain proves zero unresolved legacy lifecycle AND zero retained legacy reserved capital, not only zero counted obligations."""
    def _sources(*, unresolved, reserved, v1_pending=0, v1_open=0):
        monkeypatch.setattr(
            "app.services.freqtrade_result_ingest.freqtrade_dry_run_status",
            lambda **kwargs: {"status": "OK", "open_trades": 0},
        )
        monkeypatch.setattr(
            "app.services.freqtrade_signal_bridge.outstanding_admitted_signals",
            lambda **kwargs: [],
        )
        monkeypatch.setattr(
            "app.services.paper_trade_control.paper_trade_enabled", lambda *a, **k: False
        )
        monkeypatch.setattr(
            "app.services.paper_trade_registry.account_summary",
            lambda equity, **kwargs: SimpleNamespace(
                pending_entries=v1_pending,
                open_positions=v1_open,
                unresolved_trades=unresolved,
                reserved_capital=reserved,
            ),
        )

    # Unresolved legacy lifecycle with retained reserved capital is NOT drained,
    # even though no pending/open obligation is counted.
    _sources(unresolved=2, reserved=500.0)
    draining = cutover.evaluate_legacy_drain(starting_equity=10_000.0)
    assert draining.status == cutover.DRAIN_DRAINING
    assert draining.ready is False
    assert draining.to_dict()["paper_v1_unresolved_trades"] == 2

    # An unresolved lifecycle ALONE (no reserved capital, no pending/open) still
    # blocks the drain: the outcome is not proven.
    _sources(unresolved=1, reserved=0.0)
    unresolved_only = cutover.evaluate_legacy_drain(starting_equity=10_000.0)
    assert unresolved_only.status == cutover.DRAIN_DRAINING
    assert unresolved_only.ready is False

    # Retained reserved capital alone (no counted obligation) is NOT drained.
    _sources(unresolved=0, reserved=250.0)
    reserved_only = cutover.evaluate_legacy_drain(starting_equity=10_000.0)
    assert reserved_only.status == cutover.DRAIN_DRAINING

    # Fully cleared: zero obligations, zero unresolved, zero reserved -> READY.
    _sources(unresolved=0, reserved=0.0)
    ready = cutover.evaluate_legacy_drain(starting_equity=10_000.0)
    assert ready.status == cutover.DRAIN_READY
    assert ready.ready is True


def test_ac_008_rollback_holds_legacy_until_drained():
    """ATDD-R4-B2-controlled-paper-activation/AC-008: rollback stops new target admissions immediately but withholds legacy new-entry authority until the rollback-ready gate proves no collision."""
    text = _contract_text()
    assert "Stopping new Paper-v2 admissions is immediate" in text
    assert "Restoring legacy new-entry authority is NOT immediate" in text
    assert "rollback-ready gate proves that no cross-authority collision can occur" in text
    # The rollback paragraph is fail-closed and keeps the mode-off mechanics.
    assert "setting the mode back to `off`" in text


def test_ac_012_rollback_transition_scenarios():
    """ATDD-R4-B2-controlled-paper-activation/AC-012: the contract enumerates the rollback scenarios and the one-authority invariant, and the P&L/auth no-authority guarantee holds for the draining states."""
    text = _contract_text()
    for scenario in (
        "an open target position during rollback",
        "a pending target reservation during rollback",
        "a target terminal/reconciliation not yet complete during rollback",
        "a clean fully-drained rollback",
        "exactly one new-entry authority exists throughout the transition",
    ):
        assert scenario in text, scenario

    # Code-anchored: during a draining/unavailable rollback transition the runtime
    # grants no new-entry authority at all (neither target nor legacy), so it can
    # never run two authorities.
    import app.jobs.scan_opportunities as scan

    for granted in (scan.AUTHORITY_PAPER_V2_DRAINING, scan.AUTHORITY_PAPER_V2_UNAVAILABLE):
        authority = scan.PaperAuthority(requested=True, granted=granted, reason="rollback")
        assert not authority.paper_v2_routing
        assert not authority.legacy_new_entry_allowed


def test_ac_013_resume_requires_authorized_gate():
    """ATDD-R4-B2-controlled-paper-activation/AC-013: a healthy observation alone does not resume admissions; resumption requires an authorized gate, and F11 owns no resume authority."""
    text = _contract_text()
    assert "A suspended safety state is not cleared by an instantaneous healthy observation" in text
    assert "a later healthy result alone does NOT resume new admissions" in text
    assert "Resumption requires an explicit, authorized resume gate" in text
    assert "F11 owns no such authority" in text
    assert "requires_owner_recovery_cycles" in text

    # Code-anchored: the resume authority lives in the incident lifecycle, and the
    # F11 protection-health module exposes no resume/latch API.
    import app.services.protection_health as ph

    assert not hasattr(ph, "resume")
    assert not hasattr(ph, "clear_suspension")
    assert not hasattr(ph, "reset_suspension")
    from app.services.system_incidents import requires_owner_recovery_cycles  # owner exists

    assert callable(requires_owner_recovery_cycles)
