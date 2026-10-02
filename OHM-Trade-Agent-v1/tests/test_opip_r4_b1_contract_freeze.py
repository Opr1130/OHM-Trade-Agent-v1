"""R4-B1 contract freeze: acceptance guards for the frozen R4-B1 contract.

This module proves the freeze artifact, not production behavior. It asserts that
the R4-B1 scope contract exists, declares its own increment, freezes the
retry/requalification semantics and the orchestration and authority boundaries,
and that this increment changed no production posture and granted no authority.

It deliberately imports no application runtime module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
ATDD = APP_ROOT / "docs" / "atdd"
SCOPE_CONTRACTS = ATDD / "scope-contracts"
CONTRACT = SCOPE_CONTRACTS / "ATDD-R4-B1-contract-freeze.md"
CRON_DIR = APP_ROOT / "deploy" / "cron.d"

UNIFIED_CYCLE_MODULE = "app.jobs.run_cycle"


def _pointer() -> str:
    return (ATDD / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()


def _contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _cron_files_invoking_unified_cycle() -> list[str]:
    return sorted(
        path.name
        for path in CRON_DIR.glob("*")
        if path.is_file() and UNIFIED_CYCLE_MODULE in path.read_text(encoding="utf-8")
    )


def test_ac_001_contract_and_pointer_are_consistent():
    """ATDD-R4-B1-contract-freeze/AC-001: the contract exists, declares its own increment, and the movable pointer resolves to an existing contract without being pinned to this increment."""
    assert CONTRACT.is_file(), CONTRACT
    text = _contract_text()
    # The contract declares its own increment. The pointer is expected to move
    # later, so it is not pinned here; the scope checker enforces that the active
    # pointer matches the checked increment.
    assert f"INCREMENT:\nATDD-R4-B1-contract-freeze" in text
    pointer = _pointer()
    assert (SCOPE_CONTRACTS / f"{pointer}.md").is_file(), pointer


def test_ac_002_retry_and_requalification_semantics_are_frozen():
    """ATDD-R4-B1-contract-freeze/AC-002: the freeze distinguishes exact retry, re-qualified new decision and conflicting attempt, covers every enumerated trigger case, and requires immutable committed ancestry and economics with fail-closed ambiguity."""
    text = _contract_text()
    for disposition in (
        "EXACT_RETRY",
        "REQUALIFIED_NEW_DECISION",
        "CONFLICTING_ATTEMPT",
    ):
        assert disposition in text, disposition
    for trigger in (
        "the disposition identity already exists",
        "an admission already exists",
        "retry facts are byte or semantically identical",
        "qualification facts differ",
        "snapshot changes",
        "evidence cutoff changes",
        "policy or version changes",
        "quantity changes",
        "requested capital or notional changes",
        "stop changes",
        "targets change",
        "execution geometry changes",
        "decision context ancestry changes",
        "an earlier terminal stop already exists",
        "a committed reservation exists",
        "canonical progress is temporarily unreadable",
    ):
        assert trigger in text, trigger
    assert "admission ancestry is immutable" in text
    assert "Ambiguity fails closed" in text
    assert "duplicate trade, duplicate reservation or duplicate disposition" in text


def test_ac_003_single_orchestration_boundary():
    """ATDD-R4-B1-contract-freeze/AC-003: one scheduler entry owns the unified cycle, the contract names that single boundary, and no second scheduler is permitted."""
    text = _contract_text()
    assert "deploy/cron.d/ohm-unified-cycle" in text
    assert UNIFIED_CYCLE_MODULE in text
    assert "must not add a second scheduler" in text
    assert "timer, cron entry, daemon or orchestration loop" in text
    # The repository has exactly one scheduler entry that owns the unified cycle.
    assert _cron_files_invoking_unified_cycle() == ["ohm-unified-cycle"]


def test_ac_004_dormant_posture_and_authority_boundary():
    """ATDD-R4-B1-contract-freeze/AC-004: the frozen posture is wiring present and non-authoritative with the Feature Bus and Paper-v2 off, and the authority boundary names the forbidden funded, exchange and Committee authority."""
    text = _contract_text()
    assert "wiring PRESENT" in text
    assert "NON-AUTHORITATIVE" in text
    assert "OPIP_FEATURE_BUS_MODE" in text and "OFF" in text
    assert "OPIP_PAPER_V2_MODE" in text and "OFF/unset" in text
    for authority in (
        "funded trading",
        "funded exchange order authority",
        "Committee runtime authority",
        "dashboard trading authority",
        "Telegram trading authority",
    ):
        assert authority in text, authority
    # The repository-controlled production posture is unchanged by the freeze.
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose


def test_ac_005_rollback_and_exclusions_are_explicit():
    """ATDD-R4-B1-contract-freeze/AC-005: the contract names an exact rollback to the pre-R4-B1 orchestration and lists the explicit exclusions."""
    text = _contract_text()
    assert "Rollback." in text
    assert "remove the R4-B1 orchestration hook" in text
    assert "never leave two allocation authorities" in text
    for exclusion in (
        "R4-B2 activation",
        "funded or exchange authority",
        "Committee runtime authority",
        "legacy retirement",
        "hidden mode activation",
    ):
        assert exclusion in text, exclusion
    assert "F11" in text


def test_ac_006_no_authority_and_single_scheduler_entry():
    """ATDD-R4-B1-contract-freeze/AC-006: the freeze leaves the production posture pinned off, keeps a single unified-cycle scheduler entry, and imports no funded, exchange, order or Committee authority."""
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose
    assert "OPIP_PAPER_V2_MODE" not in compose
    assert _cron_files_invoking_unified_cycle() == ["ohm-unified-cycle"]

    modules: set[str] = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for name in modules:
        lowered = name.lower()
        for forbidden in (
            "kraken_private",
            "exchanges",
            "committee",
            "order",
            "scan_opportunities",
        ):
            assert forbidden not in lowered, (name, forbidden)
