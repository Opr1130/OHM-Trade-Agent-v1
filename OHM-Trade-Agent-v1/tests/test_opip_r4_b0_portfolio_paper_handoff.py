"""R4-B0 F7 -> Paper-v2 handoff and PCAND -> OPIPC bridge.

These tests prove the deterministic lineage boundary between the F7 portfolio
selector and Paper-v2 execution: that only a SELECTED allocation produces a
handoff, that the ``PCAND:`` -> ``OPIPC:`` bridge is deterministic and
ancestry-preserving, that every cross-link fails closed on mismatch, and that no
capital or quantity can be enlarged.

The F7 panel is built with the real F7/F6 fixtures (a real trusted test model
artifact driving a real ``ForecastDecision``), so the selector under test is the
production selector, not a stub.

R4-B0 is NON-AUTHORITATIVE: nothing here activates a mode, wires a runtime, or
reaches a funded/exchange path.
"""

from __future__ import annotations

import ast
import re
from dataclasses import fields
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.contracts.execution_geometry import ExecutionGeometryInput
from app.opip.contracts.portfolio import (
    PortfolioDirection,
    PortfolioStatus,
)
from app.opip.contracts.portfolio_paper_handoff import (
    OPIPC_CANDIDATE_ID_PREFIX,
    PORTFOLIO_PAPER_HANDOFF_SCHEMA_VERSION,
    PaperExecutionLineage,
    PortfolioPaperHandoff,
    PortfolioPaperHandoffError,
    build_execution_candidate_bridge,
    build_portfolio_paper_handoffs,
    derive_quantity,
    require_reservation_covers_allocation,
)
from app.opip.execution_geometry import build_execution_geometry
from tests import test_opip_r3_f7_economic_portfolio_selector as f7

APP_ROOT = Path(__file__).resolve().parents[1]

CUTOFF = f7.EVAL - timedelta(seconds=30)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"


def _geometry(*, direction: str = "LONG"):
    """A real ExecutionGeometry anchored to the declared snapshot cutoff."""
    if direction == "SHORT":
        payload = dict(
            symbol="SOLUSD",
            direction="SHORT",
            risk_level="medium",
            last_price=100.0,
            atr=2.0,
            ema20=100.5,
            rolling_24h_upside_p75_pct=5.0,
            rolling_24h_downside_p75_pct=5.0,
        )
    else:
        payload = dict(
            symbol="SOLUSD",
            direction="LONG",
            risk_level="medium",
            last_price=100.0,
            atr=2.0,
            ema20=99.5,
            rolling_24h_upside_p75_pct=5.0,
            rolling_24h_downside_p75_pct=5.0,
        )
    return build_execution_geometry(
        ExecutionGeometryInput(**payload),
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id="SOLUSD",
        source_cutoff=CUTOFF,
        source_evidence_fingerprint="FEV:" + "1" * 32,
    )


def _lineage(**overrides) -> PaperExecutionLineage:
    payload = dict(
        snapshot_id="SNAP:" + "e" * 32,
        snapshot_cutoff=CUTOFF,
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id="SOLUSD",
        cohort_id="COHORT:1",
        native_symbol="SOL/USD",
        quote_currency="USD",
        entry_reference=100.0,
        detector_claim_id="CLAIM:" + "f" * 32,
        decision_context_id="DCTX:" + "0" * 32,
    )
    payload.update(overrides)
    return PaperExecutionLineage(**payload)


def _selected_panel(*, direction: PortfolioDirection = PortfolioDirection.LONG):
    geometry = _geometry(direction=direction.value)
    candidate = f7.candidate(
        "SOLUSD",
        0.05,
        direction=direction,
        stop_loss_fraction=geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
    )
    decision = f7.select([candidate])
    return decision, candidate, geometry


def _handoffs(decision, candidate, geometry, *, portfolio_version=None):
    plan = decision.reservation_plan
    return build_portfolio_paper_handoffs(
        decision,
        candidates={candidate.candidate_id: candidate},
        geometries={candidate.candidate_id: geometry},
        lineages={candidate.episode_id: _lineage()},
        portfolio_version=(
            portfolio_version
            if portfolio_version is not None
            else (str(plan.portfolio_version) if plan is not None else "PV:r3f7")
        ),
    )


#: The one identity field each frozen F7 record derives from its own content.
_IDENTITY_FIELD_BY_CLASS = {
    "PortfolioAllocation": "allocation_id",
    "PortfolioReservation": "reservation_id",
    "PortfolioReservationPlan": "plan_id",
    "PortfolioCandidate": "candidate_id",
}


def _rebuild(obj, **changes):
    """Rebuild a frozen F7 record after changing a validated field.

    ``dataclasses.replace`` re-runs ``__post_init__`` while keeping the old derived
    identity, which the frozen contracts correctly refuse. This drops the record's
    own derived identity field so the new record re-derives it from its content.
    """
    identity = _IDENTITY_FIELD_BY_CLASS.get(type(obj).__name__)
    payload = {}
    for field_info in fields(obj):
        name = field_info.name
        if name == identity and name not in changes:
            continue
        payload[name] = getattr(obj, name)
    payload.update(changes)
    return type(obj)(**payload)


# ---------------------------------------------------------------------------
# 1-3. Only SELECTED produces a handoff
# ---------------------------------------------------------------------------


def test_01_selected_produces_one_handoff_per_allocation():
    decision, candidate, geometry = _selected_panel()
    assert decision.status is PortfolioStatus.SELECTED, decision.status
    handoffs = _handoffs(decision, candidate, geometry)
    assert len(handoffs) == len(decision.allocations) == 1
    handoff = handoffs[0]
    assert isinstance(handoff, PortfolioPaperHandoff)
    assert handoff.schema_version == PORTFOLIO_PAPER_HANDOFF_SCHEMA_VERSION
    assert handoff.execution_candidate_id.startswith(OPIPC_CANDIDATE_ID_PREFIX + ":")
    assert handoff.geometry_id == geometry.geometry_id


def test_02_cash_no_trade_produces_no_handoff():
    geometry = _geometry()
    losing = f7.candidate(
        "SOLUSD",
        -0.5,
        stop_loss_fraction=geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
    )
    decision = f7.select([losing])
    assert decision.status in {
        PortfolioStatus.CASH_NO_TRADE,
        PortfolioStatus.INSUFFICIENT_EVIDENCE,
    }, decision.status
    assert _handoffs(decision, losing, geometry) == ()


def test_03_insufficient_evidence_produces_no_handoff():
    # An empty panel is a governed abstention, not a trade.
    decision = f7.select([])
    assert decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE, decision.status
    assert build_portfolio_paper_handoffs(
        decision, candidates={}, geometries={}, lineages={}, portfolio_version="PV:r3f7"
    ) == ()


# ---------------------------------------------------------------------------
# 4-7. Determinism
# ---------------------------------------------------------------------------


def test_04_handoff_identity_is_deterministic_and_replay_stable():
    decision, candidate, geometry = _selected_panel()
    first = _handoffs(decision, candidate, geometry)
    second = _handoffs(decision, candidate, geometry)
    assert [h.handoff_id for h in first] == [h.handoff_id for h in second]
    assert first[0].to_dict() == second[0].to_dict()
    assert first[0].handoff_id.startswith("PHAND:")


def test_05_bridge_is_deterministic_and_preserves_lineage():
    decision, candidate, geometry = _selected_panel()
    args = dict(
        candidate=candidate,
        allocation=decision.allocations[0],
        geometry=geometry,
        lineage=_lineage(),
        portfolio_decision_id=str(decision.decision_id),
        portfolio_version=str(decision.reservation_plan.portfolio_version),
    )
    first = build_execution_candidate_bridge(**args)
    second = build_execution_candidate_bridge(**args)
    assert first.bridge_id == second.bridge_id
    assert first.economic_candidate_id == candidate.candidate_id
    assert first.episode_id == candidate.episode_id
    assert first.feasibility_decision_id == candidate.feasibility_decision_id
    assert first.forecast_id == candidate.forecast.decision_id
    assert first.geometry_id == geometry.geometry_id
    assert first.direction == candidate.direction.value


def test_06_repeated_conversion_is_identical():
    decision, candidate, geometry = _selected_panel()
    handoff = _handoffs(decision, candidate, geometry)[0]
    assert handoff.bridge.execution_candidate_id == handoff.execution_candidate_id
    assert handoff.to_dict()["handoff_id"] == handoff.handoff_id


def test_07_different_allocation_changes_the_identity():
    decision, candidate, geometry = _selected_panel()
    allocation = decision.allocations[0]
    other = _rebuild(allocation, allocated_capital=float(allocation.allocated_capital) / 2)
    base = build_execution_candidate_bridge(
        candidate=candidate,
        allocation=allocation,
        geometry=geometry,
        lineage=_lineage(),
        portfolio_decision_id=str(decision.decision_id),
        portfolio_version="PV:r3f7",
    )
    changed = build_execution_candidate_bridge(
        candidate=candidate,
        allocation=other,
        geometry=geometry,
        lineage=_lineage(),
        portfolio_decision_id=str(decision.decision_id),
        portfolio_version="PV:r3f7",
    )
    assert base.execution_candidate_id != changed.execution_candidate_id


# ---------------------------------------------------------------------------
# 8-17. Every cross-link fails closed
# ---------------------------------------------------------------------------


def _bridge_args(decision, candidate, geometry):
    return dict(
        candidate=candidate,
        allocation=decision.allocations[0],
        geometry=geometry,
        lineage=_lineage(),
        portfolio_decision_id=str(decision.decision_id),
        portfolio_version=str(decision.reservation_plan.portfolio_version),
    )


def test_08_episode_mismatch_fails_closed():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    # A real candidate for a different episode: the bridge must refuse to pair it
    # with this allocation.
    args["candidate"] = f7.candidate(
        "ETHUSD",
        0.05,
        stop_loss_fraction=geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
    )
    with pytest.raises(PortfolioPaperHandoffError, match="episode"):
        build_execution_candidate_bridge(**args)


def test_09_instrument_mismatch_fails_closed():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    args["lineage"] = _lineage(instrument_version_id="INSTR:kraken:OTHER:USD:9")
    with pytest.raises(PortfolioPaperHandoffError, match="instrument_version_id"):
        build_execution_candidate_bridge(**args)


def test_09b_venue_mismatch_fails_closed():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    args["lineage"] = _lineage(venue_instrument_id="OTHERUSD")
    with pytest.raises(PortfolioPaperHandoffError, match="venue_instrument_id"):
        build_execution_candidate_bridge(**args)


def test_10_direction_mismatch_fails_closed():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    args["geometry"] = _geometry(direction="SHORT")
    with pytest.raises(PortfolioPaperHandoffError, match="direction"):
        build_execution_candidate_bridge(**args)


def test_11_cutoff_mismatch_fails_closed():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    args["lineage"] = _lineage(snapshot_cutoff=CUTOFF - timedelta(seconds=1))
    with pytest.raises(PortfolioPaperHandoffError, match="cutoff"):
        build_execution_candidate_bridge(**args)


def test_12_candidate_without_forecast_cannot_bridge():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    args["candidate"] = _rebuild(candidate, forecast=None)
    with pytest.raises(PortfolioPaperHandoffError, match="forecast"):
        build_execution_candidate_bridge(**args)


def test_13_forecast_lineage_change_changes_the_bridge():
    decision, candidate, geometry = _selected_panel()
    base = build_execution_candidate_bridge(**_bridge_args(decision, candidate, geometry))
    other = f7.candidate(
        "SOLUSD",
        0.05,
        forecast=f7.forecast_for("SOLUSD", 0.05, validity_seconds=1800),
        stop_loss_fraction=geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
    )
    changed = build_execution_candidate_bridge(**_bridge_args(decision, other, geometry))
    assert other.forecast.decision_id != candidate.forecast.decision_id
    assert changed.execution_candidate_id != base.execution_candidate_id


def test_14_geometry_stop_fraction_mismatch_fails_closed():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    args["candidate"] = _rebuild(candidate, stop_loss_fraction=0.5)
    with pytest.raises(PortfolioPaperHandoffError, match="stop_loss_fraction"):
        build_execution_candidate_bridge(**args)


def test_15_portfolio_version_mismatch_fails_closed():
    decision, candidate, geometry = _selected_panel()
    with pytest.raises(PortfolioPaperHandoffError, match="portfolio version"):
        _handoffs(decision, candidate, geometry, portfolio_version="PVER:999")


def test_16_allocation_missing_from_candidates_fails_closed():
    decision, candidate, geometry = _selected_panel()
    with pytest.raises(PortfolioPaperHandoffError, match="no F7 candidate"):
        build_portfolio_paper_handoffs(
            decision,
            candidates={},
            geometries={candidate.candidate_id: geometry},
            lineages={candidate.episode_id: _lineage()},
            portfolio_version=str(decision.reservation_plan.portfolio_version),
        )


def test_16b_allocation_missing_its_geometry_fails_closed():
    decision, candidate, geometry = _selected_panel()
    with pytest.raises(PortfolioPaperHandoffError, match="no execution geometry"):
        build_portfolio_paper_handoffs(
            decision,
            candidates={candidate.candidate_id: candidate},
            geometries={},
            lineages={candidate.episode_id: _lineage()},
            portfolio_version=str(decision.reservation_plan.portfolio_version),
        )


def test_17_reservation_not_covering_allocation_fails_closed():
    decision, candidate, geometry = _selected_panel()
    plan = decision.reservation_plan
    allocation = decision.allocations[0]
    # The plan's own reservation must cover the allocation it authorized; a plan
    # that promises less fails closed.
    under = _rebuild(plan.reservations[0], reserved_capital=0.0)
    with pytest.raises(PortfolioPaperHandoffError, match="reservation does not cover"):
        require_reservation_covers_allocation(
            reservation=under, allocated_capital=float(allocation.allocated_capital)
        )
    require_reservation_covers_allocation(
        reservation=plan.reservations[0],
        allocated_capital=float(allocation.allocated_capital),
    )


# ---------------------------------------------------------------------------
# 18-19. Capital and quantity can never be enlarged
# ---------------------------------------------------------------------------


def test_18_allocated_notional_cannot_be_enlarged():
    decision, candidate, geometry = _selected_panel()
    handoff = _handoffs(decision, candidate, geometry)[0]
    assert handoff.allocated_capital == pytest.approx(
        float(decision.allocations[0].allocated_capital)
    )
    assert handoff.requested_notional <= handoff.allocated_capital + 1e-9
    assert handoff.requested_reservation_amount == pytest.approx(handoff.allocated_capital)


def test_19_quantity_rounding_cannot_exceed_allocation():
    quantity = derive_quantity(allocated_capital=100.0, entry_reference=3.0)
    assert quantity == pytest.approx(33.33333333)
    assert quantity * 3.0 <= 100.0
    assert derive_quantity(allocated_capital=0.001, entry_reference=1000.0) == pytest.approx(
        1e-06
    )
    # Whatever the rounding, the derived notional can never exceed the allocation.
    for capital, reference in ((0.001, 1000.0), (7.0, 3.0), (123.456, 7.77)):
        qty = derive_quantity(allocated_capital=capital, entry_reference=reference)
        assert qty * reference <= capital + 1e-9, (capital, reference, qty)


def test_19b_candidate_liquidity_capacity_bounds_the_allocation():
    geometry = _geometry()
    thin = f7.candidate(
        "SOLUSD",
        0.05,
        capacity_notional=1.0,
        stop_loss_fraction=geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
    )
    decision = f7.select([thin])
    if decision.status is not PortfolioStatus.SELECTED:
        # The selector already refused on capacity; nothing to hand off.
        assert _handoffs(decision, thin, geometry) == ()
        return
    handoff = _handoffs(decision, thin, geometry)[0]
    # The invariant the guard enforces: no allocation may exceed the candidate's
    # own declared liquidity capacity.
    assert handoff.allocated_capital <= float(thin.liquidity_capacity_notional) + 1e-9


# ---------------------------------------------------------------------------
# 20. One allocation -> at most one admission identity
# ---------------------------------------------------------------------------


def test_20_one_allocation_maps_to_at_most_one_execution_candidate():
    decision, candidate, geometry = _selected_panel()
    handoffs = _handoffs(decision, candidate, geometry)
    assert len(handoffs) == len(decision.allocations) == 1
    assert len({h.execution_candidate_id for h in handoffs}) == len(handoffs)
    assert len({h.handoff_id for h in handoffs}) == len(handoffs)


# ---------------------------------------------------------------------------
# 21. No legacy ranking / alert / Committee input is accepted
# ---------------------------------------------------------------------------


def test_21_no_legacy_ranking_or_top8_input_is_accepted():
    legacy_like = SimpleNamespace(
        status="SELECTED",
        allocations=[SimpleNamespace(candidate_id="PCAND:" + "a" * 32)],
        profit_ranking=SimpleNamespace(total_score=9.9),
        rank=1,
    )
    with pytest.raises(PortfolioPaperHandoffError, match="PortfolioDecision"):
        build_portfolio_paper_handoffs(
            legacy_like,
            candidates={},
            geometries={},
            lineages={},
            portfolio_version="PVER:1",
        )


def test_21b_handoff_module_imports_no_legacy_or_authority_surface():
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for name in modules:
        lowered = name.lower()
        assert "scan_opportunities" not in lowered
        assert "profit_ranking" not in lowered
        assert "committee" not in lowered
        assert "exchanges" not in lowered
        assert "run_cycle" not in lowered


# ---------------------------------------------------------------------------
# 22. Pure: no clock, network, DB, environment, or write authority
# ---------------------------------------------------------------------------


def _source() -> str:
    return (
        APP_ROOT / "app" / "opip" / "contracts" / "portfolio_paper_handoff.py"
    ).read_text(encoding="utf-8")


def test_22_handoff_construction_is_pure():
    source = _source()
    for forbidden in (
        "datetime.now",
        "datetime.utcnow",
        "time.time",
        "uuid",
        "os.environ",
        "getenv",
        "requests.",
        "urlopen",
        "sqlite3",
        "open(",
        "random.",
    ):
        assert forbidden not in source, forbidden


def test_22b_handoff_has_no_execution_authority_surface():
    """The handoff is a lineage contract, not an authority: it writes nothing."""
    source = _source()
    for verb in (
        "admit_paper_opportunity",
        "submit_canonical_event",
        "trigger_paper_protection_action",
    ):
        assert not re.search(rf"\b{verb}\s*\(", source), verb
    assert "CanonicalWriterClient(" not in source
    assert "reserve(" not in source


# ---------------------------------------------------------------------------
# LONG + SHORT support
# ---------------------------------------------------------------------------


def test_direction_support_covers_long_and_short_geometry():
    long_geometry = _geometry(direction="LONG")
    short_geometry = _geometry(direction="SHORT")
    assert long_geometry.direction == "LONG"
    assert short_geometry.direction == "SHORT"
    assert short_geometry.stop_price > 0
    assert short_geometry.geometry_id != long_geometry.geometry_id


def test_short_allocation_bridges_with_short_direction():
    decision, candidate, geometry = _selected_panel(direction=PortfolioDirection.SHORT)
    # If the real F7 selector abstained for this direction, the contract still must
    # not invent a handoff.
    if decision.status is not PortfolioStatus.SELECTED:
        assert build_portfolio_paper_handoffs(
            decision,
            candidates={candidate.candidate_id: candidate},
            geometries={candidate.candidate_id: geometry},
            lineages={candidate.episode_id: _lineage()},
            portfolio_version="PV:r3f7",
        ) == ()
        return
    handoff = _handoffs(decision, candidate, geometry)[0]
    assert handoff.direction == "SHORT"
    assert handoff.bridge.direction == "SHORT"


def test_bridge_rejects_an_unknown_direction():
    decision, candidate, geometry = _selected_panel()
    args = _bridge_args(decision, candidate, geometry)
    args["geometry"] = SimpleNamespace(
        geometry_id="EGEOM:x",
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id="SOLUSD",
        direction="SIDEWAYS",
        source_cutoff=CUTOFF,
        stop_loss_fraction=float(candidate.stop_loss_fraction),
    )
    with pytest.raises(PortfolioPaperHandoffError, match="direction"):
        build_execution_candidate_bridge(**args)
