"""R4-B0 spine contract closure: acceptance guards for the dormant target spine.

These are *closure* guards. They do not re-prove the spine by execution (Items 4e,
5 and 6 do that); they assert that the closure artifact and the frozen R4-B0
invariants it claims are actually present in the shipped contracts, and that
closing the increment introduced no runtime authority.

Semantic assertions only: no test blesses arbitrary file content or snapshots a
whole module.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent

INCREMENT = "ATDD-R4-B0-spine-contract-closure"
CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT_PATH = CONTRACT_DIR / f"{INCREMENT}.md"
ACTIVE_INCREMENT_PATH = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"

APP = APP_ROOT / "app"
COMPOSE = APP_ROOT / "docker-compose.yml"
RUN_CYCLE = APP / "jobs" / "run_cycle.py"
SCAN = APP / "jobs" / "scan_opportunities.py"

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

#: The production contracts this closure increment froze. Every one must exist.
R4_B0_CONTRACTS = (
    "app/opip/contracts/feasibility_evidence.py",
    "app/opip/contracts/execution_geometry.py",
    "app/opip/execution_geometry.py",
    "app/opip/contracts/portfolio_paper_handoff.py",
    "app/opip/contracts/paper_execution_runtime.py",
)


def _source(relative: str) -> str:
    return (APP_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


def _imported_modules(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(relative)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ---------------------------------------------------------------------------
# AC-001 — the closure artifact and its movable pointer
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_closure_contract_and_pointer_are_consistent():
    """ATDD-R4-B0-spine-contract-closure/AC-001: the closure contract exists, declares its own increment, and the movable pointer resolves to an existing contract without being pinned to this increment."""
    assert CONTRACT_PATH.is_file()
    lines = CONTRACT_PATH.read_text(encoding="utf-8").splitlines()
    assert lines[0].strip() == "INCREMENT:"
    assert lines[1].strip() == INCREMENT

    pointer = ACTIVE_INCREMENT_PATH.read_text(encoding="utf-8").strip()
    assert (CONTRACT_DIR / f"{pointer}.md").is_file(), pointer

    # This closure does not pin the global pointer to its own identity; it only
    # requires the pointer to resolve. Proved structurally by the repository's own
    # AST guard rather than by a text comparison that would match its own literal.
    from tests.test_atdd_scope_control import global_pointer_pin_violations

    source = Path(__file__).read_text(encoding="utf-8")
    assert global_pointer_pin_violations(source) == []


# ---------------------------------------------------------------------------
# AC-002 — F5 target evidence is typed and the seam stays pure
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_002_f5_target_evidence_is_typed_and_pure():
    """ATDD-R4-B0-spine-contract-closure/AC-002: FeasibilityEvidence is the canonical F5 target input, F5 no longer requires the legacy MarketSnapshot type, and the seam reads no clock, network, database or environment."""
    evidence_source = _source("app/opip/contracts/feasibility_evidence.py")
    assert "class FeasibilityEvidence" in evidence_source
    assert "def feasibility_evidence_fingerprint" in evidence_source
    # The identity is derived from canonicalized primitives, not object identity.
    assert "id(" not in evidence_source
    assert "repr(" not in evidence_source

    seam = _source("app/opip/feasibility.py")
    # F5 accepts the typed record and no longer requires the legacy type.
    assert "class FeasibilityEvidence" not in seam  # the record lives in its contract
    assert "FeasibilityEvidence" in seam
    assert "def adapt_legacy_market_snapshot" in seam
    # Purity: no ambient reads in the seam.
    for pattern in (r"\bdatetime\.now\s*\(", r"\bdatetime\.utcnow\s*\(", r"\btime\.time\s*\(", r"\bos\.environ", r"\bopen\s*\(", r"\bsqlite3", r"\brequests\."):
        assert not re.search(pattern, seam), pattern
    # SHORT execution evidence is evaluated without a venue refresh: the offline
    # gate route is pinned by the F5 adapter contract.
    gates = _source("app/opip/decision/gates.py")
    assert "refresh_margin_book=False" in gates


# ---------------------------------------------------------------------------
# AC-003 — one execution-geometry owner
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_003_one_execution_geometry_owner():
    """ATDD-R4-B0-spine-contract-closure/AC-003: exactly one deterministic entry/exit geometry owner exists, the legacy advisor delegates to it instead of recomputing, and the geometry identity is content-derived."""
    contract = _source("app/opip/contracts/execution_geometry.py")
    assert "class ExecutionGeometry" in contract
    assert "def execution_geometry_identity" in contract
    assert "EGEOM" in contract

    kernel = _source("app/opip/execution_geometry.py")
    assert "def compute_geometry_kernel" in kernel
    assert "def build_execution_geometry" in kernel
    # The kernel is a pure function of primitives: no ambient reads.
    for pattern in (r"\bdatetime\.now\s*\(", r"\btime\.time\s*\(", r"\bos\.environ", r"\brequests\.", r"\bopen\s*\("):
        assert not re.search(pattern, kernel), pattern

    # The legacy advisor delegates rather than carrying a second algorithm.
    advisor = _source("app/services/entry_exit_advisor.py")
    assert "compute_geometry_kernel" in advisor
    assert "def _geometry_is_valid" not in advisor
    assert "def _realistic_target_1_multiple" not in advisor
    # And the one multiplier constant is imported from the single owner.
    assert "from app.opip.execution_geometry import" in advisor


# ---------------------------------------------------------------------------
# AC-004 — direction-bound admission contract
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_004_direction_bound_admission_contract():
    """ATDD-R4-B0-spine-contract-closure/AC-004: the canonical admission contract defines the four direction/role/side pairs, refuses any other pair, reads a historical record narrowly as LONG, and fails closed on a direction-contract record that omits its direction."""
    from app.opip.contracts.paper_execution_runtime import (
        PAPER_DIRECTION_CONTRACT_VERSION,
        expected_paper_side,
        paper_trade_direction_contract,
    )

    assert (PAPER_DIRECTION_CONTRACT_VERSION) >= 2
    assert expected_paper_side("LONG", "ENTRY") == "BUY"
    assert expected_paper_side("LONG", "EXIT") == "SELL"
    assert expected_paper_side("SHORT", "ENTRY") == "SELL"
    assert expected_paper_side("SHORT", "EXIT") == "BUY"
    with pytest.raises(ValueError):
        expected_paper_side("FLAT", "ENTRY")
    with pytest.raises(ValueError):
        expected_paper_side("LONG", "SIDEWAYS")

    # Historical (pre-contract) record: narrow LONG reading.
    assert paper_trade_direction_contract({"schema_version": 1}) == "LONG"
    # New-format record with no direction must not inherit that reading.
    with pytest.raises(ValueError):
        paper_trade_direction_contract(
            {"schema_version": 1, "direction_contract_version": PAPER_DIRECTION_CONTRACT_VERSION}
        )


# ---------------------------------------------------------------------------
# AC-005 — ancestry-derived role/side at the canonical writer
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_005_writer_derives_direction_from_ancestry():
    """ATDD-R4-B0-spine-contract-closure/AC-005: the canonical writer binds the order-intent role/side pair to the admitted trade's committed direction and no longer hard-codes a long-only pair, while the recovery fill uses the committed ENTRY side."""
    writer = _source("app/opip/canonical/writer.py")
    assert "expected_paper_side(" in writer
    assert "paper_trade_direction_contract(admission)" in writer
    # The old long-only invariant is gone.
    assert 'role == "ENTRY" and side != "BUY"' not in writer
    assert 'role == "EXIT" and side != "SELL"' not in writer
    # Recovery pricing uses the committed ENTRY side rather than assuming a BUY.
    assert "entry_order_intent" in writer
    # Direction is projected from the admission, not hard-coded.
    assert 'direction="LONG"' not in writer


# ---------------------------------------------------------------------------
# AC-006 — SHORT reachability, protection and P&L signing
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_006_short_is_reachable_and_direction_correct():
    """ATDD-R4-B0-spine-contract-closure/AC-006: the protection-plan builder accepts a SHORT's descending targets and above-entry stop, the protection runtime is direction-aware, and the canonical gross P&L is signed by the admitted direction."""
    plan = _source("app/services/paper_v2_protection_plan.py")
    assert "require_paper_direction" in plan
    # Both orderings are validated, direction-bound.
    assert "ascend for a LONG and descend for a SHORT" in plan
    assert "stop_price must sit above the first target" in plan

    runtime = _source("app/services/paper_v2_protection_runtime.py")
    assert "def _exit_side_for" in runtime
    assert "def _executable_exit_price" in runtime
    assert "EXIT_SIDE_SHORT" in runtime
    assert "_direction_of(item)" in runtime

    writer = _source("app/opip/canonical/writer.py")
    assert "is_long = direction == PAPER_DIRECTION_LONG" in writer
    assert "gross P&L is signed by the admitted trade's direction" in writer


# ---------------------------------------------------------------------------
# AC-007 — deterministic F7 -> Paper-v2 handoff and the PCAND/OPIPC bridge
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_007_deterministic_handoff_and_candidate_bridge():
    """ATDD-R4-B0-spine-contract-closure/AC-007: one typed handoff binds the full F7 lineage and the exact geometry, the PCAND to OPIPC bridge is derived rather than copied, only SELECTED produces a handoff, and the contract grants no execution or reservation authority."""
    source = _source("app/opip/contracts/portfolio_paper_handoff.py")
    assert "class PortfolioPaperHandoff" in source
    assert "class ExecutionCandidateBridge" in source
    assert "class PaperExecutionLineage" in source
    assert "OPIPC_CANDIDATE_ID_PREFIX" in source
    # Derived, not copied.
    assert "derive_execution_candidate_id" in source
    # Eligibility: only SELECTED, and the other statuses authorize nothing.
    assert "PortfolioStatus.SELECTED" in source
    assert "return ()" in source
    # It is a lineage contract, not an authority: no writer/reservation surface.
    for pattern in (r"\badmit_paper_opportunity\s*\(", r"\bsubmit_canonical_event\s*\(", r"CanonicalWriterClient\("):
        assert not re.search(pattern, source), pattern
    # Determinism: no clock, uuid, randomness or retry counter participates in
    # either identity payload.
    for pattern in (r"\bdatetime\.now\s*\(", r"\buuid\b", r"\brandom\."):
        assert not re.search(pattern, source), pattern
    for fn_name in ("_bridge_identity", "_handoff_identity", "derive_execution_candidate_id"):
        body = source[source.index(f"def {fn_name}("):]
        body = body[: body.index("\n\n\n")]
        assert "retry" not in body.lower(), fn_name
        assert "clock" not in body.lower(), fn_name


# ---------------------------------------------------------------------------
# AC-008 — real composition reaches SELECTED for LONG and SHORT
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_008_real_spine_composition_selects_for_long_and_short():
    """ATDD-R4-B0-spine-contract-closure/AC-008: the real production F3 to F7 composition reaches SELECTED and a single handoff for both LONG and SHORT, with the direction and geometry preserved."""
    from app.opip.contracts.portfolio import PortfolioStatus

    from tests import test_opip_r4_b0_spine_composition as composition

    long_result = composition._compose(direction="LONG", expected_return=0.05)
    assert long_result.decision.status is PortfolioStatus.SELECTED
    assert len(long_result.handoffs) == 1
    assert long_result.handoffs[0].direction == "LONG"

    short_result = composition._compose(direction="SHORT", expected_return=0.05)
    assert short_result.decision.status is PortfolioStatus.SELECTED
    assert len(short_result.handoffs) == 1
    assert short_result.handoffs[0].direction == "SHORT"
    # The SHORT geometry stops above and targets below its reference.
    reference = (
        short_result.geometry.entry_low + short_result.geometry.entry_high
    ) / 2
    assert short_result.geometry.stop_price > reference
    assert short_result.geometry.target_1 < reference
    assert short_result.handoffs[0].geometry_id == short_result.geometry.geometry_id


# ---------------------------------------------------------------------------
# AC-009 — zero model evidence yields no trade, with distinct states
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_009_zero_model_yields_no_trade_with_distinct_states():
    """ATDD-R4-B0-spine-contract-closure/AC-009: an empty trusted forecast registry makes F6 abstain without fabricating a probability, F7 then reports INSUFFICIENT_EVIDENCE which stays distinct from CASH_NO_TRADE and SELECTED, and no allocation, reservation or handoff is produced."""
    from app.opip.contracts.portfolio import PortfolioStatus
    from app.opip.forecast import TrustedForecastModelRegistry

    from tests import test_opip_r4_b0_spine_composition as composition

    zero = composition._compose(registry=TrustedForecastModelRegistry.empty())
    assert zero.forecast.expected_return_unconditional is None
    assert zero.forecast.valid_until is None
    assert zero.decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert zero.decision.abstention_reason is not None
    assert zero.decision.cash_reason is None
    assert zero.decision.allocations == ()
    assert zero.decision.reservation_plan is None
    assert zero.handoffs == ()

    cash = composition._compose(expected_return=-0.05)
    assert cash.decision.status is PortfolioStatus.CASH_NO_TRADE
    assert cash.decision.cash_reason is not None
    # The two outcomes are not collapsed into one another.
    assert cash.decision.status is not zero.decision.status


# ---------------------------------------------------------------------------
# AC-010 — dormant posture is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_010_dormant_posture_unchanged_by_closure():
    """ATDD-R4-B0-spine-contract-closure/AC-010: the closure keeps the Feature Bus off and Paper-v2 unset in repository-controlled configuration, and neither run_cycle nor the scan imports the target spine."""
    compose = COMPOSE.read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose
    assert "OPIP_PAPER_V2_MODE" not in compose

    run_cycle = RUN_CYCLE.read_text(encoding="utf-8")
    scan = SCAN.read_text(encoding="utf-8")
    for forbidden in (
        "portfolio_selector",
        "portfolio_paper_handoff",
        "evaluate_forecast",
        "evaluate_feasibility",
        "detectors.ignition",
    ):
        assert forbidden not in run_cycle, forbidden
        assert forbidden not in scan, forbidden

    # The Feature Bus vocabulary admits an active mode, but production is off.
    publisher = _source("app/opip/features/publisher.py")
    assert "FEATURE_BUS_MODES" in publisher
    assert '"active"' in publisher


# ---------------------------------------------------------------------------
# AC-011 — no funded, exchange or Committee authority introduced
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_011_no_funded_exchange_or_committee_authority():
    """ATDD-R4-B0-spine-contract-closure/AC-011: none of the R4-B0 production contracts import or call a private exchange client, an order or borrow API, a leverage or margin activation surface, or the Committee."""
    for relative in R4_B0_CONTRACTS:
        modules = _imported_modules(relative)
        for name in modules:
            lowered = name.lower()
            for forbidden in ("kraken_private", "exchanges", "committee", "freqtrade"):
                assert forbidden not in lowered, (relative, name)
        source = _source(relative)
        for pattern in (
            r"\bcreate_order\s*\(",
            r"\bcancel_order\s*\(",
            r"\bplace_order\s*\(",
            r"\bmodify_order\s*\(",
            r"\bborrow\s*\(",
            r"\bset_leverage\s*\(",
            r"\benable_margin\s*\(",
            r"KrakenPrivateClient\(",
        ):
            assert not re.search(pattern, source), (relative, pattern)


# ---------------------------------------------------------------------------
# AC-012 — historical R4-A fact preserved; evolution recorded, not rewritten
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_012_historical_long_only_fact_is_preserved():
    """ATDD-R4-B0-spine-contract-closure/AC-012: the closure records that the R4-A LONG-only paper contract was correct at that time and that R4-B0 extends the canonical representation, without rewriting the historical increment or deleting its assertions."""
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    # The completed R4-A increment remains a real, frozen contract.
    r4a = CONTRACT_DIR / "ATDD-R4-F8-paper-v2-cutover-readiness.md"
    assert r4a.is_file()
    assert "SHORT_AUTHORITY_MISSING" in r4a.read_text(encoding="utf-8")

    # The closure records the evolution explicitly, in both directions.
    lowered = contract.lower()
    assert "r4-a" in lowered
    assert "SHORT_AUTHORITY_MISSING" in contract
    assert "long-only" in lowered
    assert "correct" in lowered

    # The historical long-only assertions were converted, not deleted: the
    # direction contract is still asserted somewhere in the suite.
    increment6a = _source("tests/test_opip_paper_v2_increment6a_bc3.py")
    assert "test_short_is_a_supported_direction_only_through_the_direction_contract" in increment6a
    assert "expected_paper_side" in increment6a
