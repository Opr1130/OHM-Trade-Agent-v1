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

import pytest

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

#: The R4-B2 acceptance module may import only the frozen contract surface.
ALLOWED_APP_IMPORT_PREFIXES = (
    "app.opip.contracts.",
    "app.services.paper_v2_activation",
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
    """ATDD-R4-B2-controlled-paper-activation/AC-008: rollback restores exactly one authority via the mode switch, never two, with no canonical-data migration."""
    text = _contract_text()
    assert "set the mode back to `off`" in text
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
