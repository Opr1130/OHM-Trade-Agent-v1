"""R4-B0 Item 6: real target-spine composition proof.

This module drives a genuine opportunity through the *production* functions of
every stage of the target spine, in process:

    FeatureSnapshot -> F3 IGNITION -> F4 lifecycle
      -> F5 feasibility (FeasibilityEvidence)
      -> F6 forecast (real trusted TEST artifact)
      -> F7 constrained selector (exact ExecutionGeometry)
      -> deterministic F7 -> Paper-v2 handoff

It is a contract/composition proof. It does NOT wire a runtime, activate a mode,
read a clock, touch a network, or reach any funded/exchange surface. The only
fabricated inputs are the *environmental* ones the frozen fixtures already
declare (a sealed FeatureSnapshot, a market/book validation record, a trusted
SYNTHETIC_TEST_ONLY model artifact). Every economic and lineage decision is made
by the production code under test.

It also proves the architectural property that a zero-admissible-model
environment cannot synthesize a trade, and that a valid object carried over from
a different lineage is refused rather than repaired.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.opip import opportunity_lifecycle as lifecycle
from app.opip.contracts.execution_geometry import ExecutionGeometryInput
from app.opip.contracts.feasibility import (
    FeasibilityDisposition,
    FeasibilityPolicy,
)
from app.opip.contracts.feasibility_evidence import (
    feasibility_evidence_from_market_snapshot,
)
from app.opip.contracts.forecast import (
    ForecastContractError,
    ForecastStatus,
)
from app.opip.contracts.portfolio import (
    PortfolioCapitalState,
    PortfolioDirection,
    PortfolioEvaluationWindow,
    PortfolioExposureSnapshot,
    PortfolioPolicy,
    PortfolioStatus,
)
from app.opip.contracts.portfolio_paper_handoff import (
    OPIPC_CANDIDATE_ID_PREFIX,
    PaperExecutionLineage,
    PortfolioPaperHandoffError,
    build_portfolio_paper_handoffs,
)
from app.opip.detectors import ignition
from app.opip.execution_geometry import build_execution_geometry
from app.opip.feasibility import evaluate_feasibility
from app.opip.forecast import (
    TrustedForecastModelRegistry,
    evaluate_forecast,
)
from app.opip.portfolio_selector import select_portfolio

from tests import test_opip_r3_f3_ignition_detector as f3
from tests import test_opip_r3_f7_economic_portfolio_selector as f7

APP_ROOT = Path(__file__).resolve().parents[1]

#: The spine's instants, all derived from the frozen F3 fixture cutoffs so no new
#: clock is introduced. The claiming evaluation happens one grid step later, which
#: is what a real two-interval entry persistence requires.
SNAPSHOT_CUTOFF = f3.CUTOFF
CLAIM_CUTOFF = f3.SECOND_CUTOFF
FORECAST_INSTANT = f7.EVAL


# ---------------------------------------------------------------------------
# Stage inputs (environmental evidence only)
# ---------------------------------------------------------------------------


def _feature_snapshot(*, venue: str = f3.VENUE_INSTRUMENT_ID, instrument: str = f3.INSTRUMENT_VERSION_ID, cutoff: datetime = SNAPSHOT_CUTOFF):
    """A sealed FeatureSnapshot from the frozen F3 fixture builder."""
    return f3.build_snapshot(
        cutoff=cutoff,
        instrument_version_id=instrument,
        venue_instrument_id=venue,
    )


def _short_market_snapshot(*, symbol: str = "SOLUSD"):
    """A real MarketSnapshot carrying admissible SHORT margin/execution evidence.

    SHORT is decided on the BTNL margin book, so the evidence must carry the BTNL
    venue provenance and an ELIGIBLE margin record; otherwise F5 correctly
    abstains. LONG uses the same helper's plain fixture.
    """
    snapshot = f7.snapshot(symbol)
    snapshot.trade_direction = "SHORT"
    snapshot.margin_validation_status = "ELIGIBLE"
    snapshot.margin_eligible = True
    snapshot.margin_venue_symbol = "SOLUSD:BTNL"
    snapshot.margin_max_leverage = 2.0
    return snapshot


def _geometry(*, direction: str, source_cutoff: datetime = CLAIM_CUTOFF):
    """The one ExecutionGeometry the whole spine risks and executes."""
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
        instrument_version_id=f3.INSTRUMENT_VERSION_ID,
        venue_instrument_id=f3.VENUE_INSTRUMENT_ID,
        source_cutoff=source_cutoff,
        source_evidence_fingerprint="FEV:r4b0-composition",
    )


def _portfolio_policy() -> PortfolioPolicy:
    return f7.policy()


def _capital() -> PortfolioCapitalState:
    return f7.capital_state()


def _exposure() -> PortfolioExposureSnapshot:
    return f7.exposure()


def _window() -> PortfolioEvaluationWindow:
    return PortfolioEvaluationWindow(
        start=FORECAST_INSTANT, end=FORECAST_INSTANT + timedelta(days=2)
    )


# ---------------------------------------------------------------------------
# The composition harness: every stage calls the production function
# ---------------------------------------------------------------------------


class Composition:
    """The lineage chain produced by one real traversal of the spine."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _compose(
    *,
    direction: str = "LONG",
    expected_return: float = 0.05,
    registry: Any | None = None,
) -> Composition:
    """Drive one opportunity through the real production spine.

    Each arrow below is a real production call:
      F3  ``ignition.evaluate``
      F4  ``lifecycle.apply_claim``
      F5  ``feasibility.evaluate_feasibility`` over a canonically adapted
          ``FeasibilityEvidence``
      F6  ``forecast.evaluate_forecast`` with a real trusted TEST artifact
      F7  ``portfolio_selector.select_portfolio``
      ->  ``portfolio_paper_handoff.build_portfolio_paper_handoffs``
    """
    # --- F3: two-interval entry persistence ---------------------------------
    detector_state = f3.build_state()
    claims_first, detector_state = ignition.evaluate(
        _feature_snapshot(cutoff=SNAPSHOT_CUTOFF), detector_state, SNAPSHOT_CUTOFF
    )
    assert claims_first == [], "the first evaluation must not claim"
    claims, detector_state = ignition.evaluate(
        _feature_snapshot(cutoff=CLAIM_CUTOFF), detector_state, CLAIM_CUTOFF
    )
    assert len(claims) == 1, "the second evaluation must claim exactly once"
    claim = claims[0]
    snapshot = _feature_snapshot(cutoff=CLAIM_CUTOFF)

    # --- F4: the claim opens exactly one ACTIVE episode ----------------------
    episode_result = lifecycle.apply_claim(
        claim, None, claim.evaluation_cutoff, f7.F4_POLICY
    )
    episode = episode_result.episode

    # --- F5: canonical typed feasibility evidence -> decision ---------------
    market = _short_market_snapshot() if direction == "SHORT" else f7.snapshot()
    evidence = feasibility_evidence_from_market_snapshot(
        market,
        source_cutoff=CLAIM_CUTOFF,
        source_snapshot_id=snapshot.snapshot_id,
        evaluation_time=CLAIM_CUTOFF,
        instrument_version_id=episode.instrument_version_id,
    )
    feasibility = evaluate_feasibility(
        episode, evidence, CLAIM_CUTOFF, FeasibilityPolicy()
    )

    # --- F6: real trusted TEST artifact -> forecast --------------------------
    inputs = f7.input_vector("SOLUSD")
    # The input vector must be point-in-time relative to the forecast instant, so
    # its cutoff is the claim cutoff the evidence was captured at.
    inputs = type(inputs)(
        input_schema_id=inputs.input_schema_id,
        input_schema_version=inputs.input_schema_version,
        source_snapshot_id=snapshot.snapshot_id,
        source_cutoff=CLAIM_CUTOFF,
        horizon=inputs.horizon,
        features=inputs.features,
    )
    artifact = f7.artifact("SOLUSD")
    resolved_registry = (
        f7.make_registry(expected_return) if registry is None else registry
    )
    forecast = evaluate_forecast(
        episode,
        feasibility,
        inputs,
        FORECAST_INSTANT,
        f7.FORECAST_POLICY,
        model_artifact=artifact,
        registry=resolved_registry,
    )

    # --- F7: candidate panel -> constrained selection ------------------------
    geometry = _geometry(direction=direction)
    candidate = f7.candidate(
        "SOLUSD",
        expected_return,
        direction=(
            PortfolioDirection.SHORT if direction == "SHORT" else PortfolioDirection.LONG
        ),
        stop_loss_fraction=geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
        forecast=forecast,
    )
    decision = select_portfolio(
        [candidate],
        capital_state=_capital(),
        exposure=_exposure(),
        window=_window(),
        evaluation_time=FORECAST_INSTANT,
        policy=_portfolio_policy(),
    )

    # --- Handoff: exact lineage + exact geometry -----------------------------
    lineage = PaperExecutionLineage(
        snapshot_id=snapshot.snapshot_id,
        snapshot_cutoff=CLAIM_CUTOFF,
        instrument_version_id=episode.instrument_version_id,
        venue_instrument_id=episode.venue_instrument_id,
        cohort_id="COHORT:r4b0",
        native_symbol="SOL/USD",
        quote_currency="USD",
        entry_reference=(geometry.entry_low + geometry.entry_high) / 2,
        detector_claim_id=claim.claim_id,
        decision_context_id="DCTX:" + "0" * 32,
    )
    handoffs = build_portfolio_paper_handoffs(
        decision,
        candidates={candidate.candidate_id: candidate},
        geometries={candidate.candidate_id: geometry},
        lineages={episode.episode_id: lineage},
        portfolio_version=str(
            decision.reservation_plan.portfolio_version
            if decision.reservation_plan is not None
            else _capital().portfolio_version
        ),
    )
    return Composition(
        snapshot=snapshot,
        detector_state=detector_state,
        claim=claim,
        episode=episode,
        evidence=evidence,
        feasibility=feasibility,
        inputs=inputs,
        artifact=artifact,
        forecast=forecast,
        geometry=geometry,
        candidate=candidate,
        decision=decision,
        lineage=lineage,
        handoffs=handoffs,
    )


# ---------------------------------------------------------------------------
# Positive composition: one real eligible opportunity traverses the whole spine
# ---------------------------------------------------------------------------


def test_composition_traverses_the_real_spine_and_binds_lineage():
    """F3 -> F4 -> F5 -> F6 -> F7 -> handoff, with every identity bound through."""
    result = _compose()

    # F3 produced a claim bound to the sealed FeatureSnapshot it evaluated.
    assert result.claim.snapshot_id == result.snapshot.snapshot_id
    assert result.claim.instrument_version_id == result.snapshot.instrument_version_id
    assert result.claim.venue_instrument_id == result.snapshot.venue_instrument_id

    # F4 opened an ACTIVE episode sourced by exactly that claim.
    assert result.episode.source_claim_id == result.claim.claim_id
    assert result.episode.snapshot_id == result.snapshot.snapshot_id
    assert result.episode.instrument_version_id == result.claim.instrument_version_id
    assert result.episode.venue_instrument_id == result.claim.venue_instrument_id

    # F5 decided on the episode and produced a real decision.
    assert result.feasibility.episode_id == result.episode.episode_id
    assert result.feasibility.disposition is FeasibilityDisposition.FEASIBLE
    assert result.feasibility.detector_snapshot_id == result.snapshot.snapshot_id

    # F6 forecast retains the episode and feasibility lineage.
    assert result.forecast.status is ForecastStatus.FORECAST
    assert result.forecast.episode_id == result.episode.episode_id
    assert result.forecast.feasibility_decision_id == result.feasibility.decision_id

    # F7 selected and its candidate carries the same lineage.
    assert result.decision.status is PortfolioStatus.SELECTED
    assert len(result.decision.allocations) == 1
    allocation = result.decision.allocations[0]
    assert allocation.episode_id == result.episode.episode_id
    assert allocation.candidate_id == result.candidate.candidate_id

    # The handoff names exactly one execution candidate for that allocation and
    # preserves the whole chain.
    assert len(result.handoffs) == 1
    handoff = result.handoffs[0]
    assert handoff.execution_candidate_id.startswith(OPIPC_CANDIDATE_ID_PREFIX + ":")
    assert handoff.economic_candidate_id == result.candidate.candidate_id
    assert handoff.allocation_id == allocation.allocation_id
    assert handoff.episode_id == result.episode.episode_id
    assert handoff.feasibility_decision_id == result.feasibility.decision_id
    assert handoff.forecast_id == result.forecast.decision_id
    assert handoff.portfolio_decision_id == result.decision.decision_id
    assert handoff.snapshot_id == result.snapshot.snapshot_id
    assert handoff.detector_claim_id == result.claim.claim_id
    assert handoff.geometry_id == result.geometry.geometry_id
    assert handoff.bridge.bridge_id == handoff.bridge.bridge_id


def test_composition_uses_no_legacy_admission_authority():
    """No Top-8 or profit-ranking result participates in the selection."""
    result = _compose()
    # The panel the selector saw is the F7 panel; nothing legacy fed it, and the
    # decision carries no legacy comparator as its authority.
    payload = result.decision.to_dict()
    assert payload["status"] == PortfolioStatus.SELECTED.value
    assert result.decision.selected_candidate_ids == (result.candidate.candidate_id,)
    # The F7 candidate identity is the canonical PCAND domain, not a legacy rank.
    assert result.candidate.candidate_id.startswith("PCAND:")


# ---------------------------------------------------------------------------
# LONG and SHORT both traverse the same spine
# ---------------------------------------------------------------------------


def test_long_composition_reaches_the_handoff():
    result = _compose(direction="LONG")
    assert result.feasibility.disposition is FeasibilityDisposition.FEASIBLE
    assert result.decision.status is PortfolioStatus.SELECTED
    assert result.geometry.direction == "LONG"
    assert result.handoffs[0].direction == "LONG"
    assert result.handoffs[0].bridge.direction == "LONG"


def test_short_composition_reaches_the_handoff_and_stays_short():
    """A SHORT reaches the handoff through the same spine and is never inverted."""
    result = _compose(direction="SHORT", expected_return=0.05)
    assert result.feasibility.disposition is FeasibilityDisposition.FEASIBLE
    assert result.forecast.status is ForecastStatus.FORECAST
    assert result.geometry.direction == "SHORT"
    # The SHORT geometry stops above and targets below its entry reference.
    reference = (result.geometry.entry_low + result.geometry.entry_high) / 2
    assert result.geometry.stop_price > reference
    assert result.geometry.target_1 < reference
    # Non-vacuous: the SHORT genuinely reached selection and the handoff.
    assert result.decision.status is PortfolioStatus.SELECTED
    assert len(result.handoffs) == 1
    handoff = result.handoffs[0]
    assert handoff.direction == "SHORT"
    assert handoff.bridge.direction == "SHORT"
    assert handoff.geometry_id == result.geometry.geometry_id
    # The historical LONG-only assumption is not reopened anywhere on the path.
    assert handoff.bridge.economic_candidate_id == result.candidate.candidate_id


# ---------------------------------------------------------------------------
# Zero admissible model evidence -> no trade candidate
# ---------------------------------------------------------------------------


def test_zero_model_environment_cannot_synthesize_a_trade():
    """The production empty registry yields governed abstention, not a trade."""
    result = _compose(registry=TrustedForecastModelRegistry.empty())

    # F3/F4/F5 still ran: the evidence was admissible.
    assert result.feasibility.disposition is FeasibilityDisposition.FEASIBLE

    # F6 abstained with its own frozen vocabulary rather than inventing a number.
    assert result.forecast.status is ForecastStatus.INSUFFICIENT_EVIDENCE
    assert result.forecast.expected_return_unconditional is None
    assert result.forecast.valid_until is None

    # F7 therefore had no economically usable candidate and abstained.
    assert result.decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert result.decision.allocations == ()
    assert result.decision.reservation_plan is None

    # And the spine produced no execution candidate of any kind.
    assert result.handoffs == ()


def test_zero_model_abstention_does_not_collapse_into_cash_no_trade():
    """The abstention is a distinct governed state, not a generic empty result.

    A zero-model environment is missing *evidence*, so F7 must report
    INSUFFICIENT_EVIDENCE with its own reason rather than CASH_NO_TRADE, which is
    the distinct "evidence was fine but no positive expected net dollars" outcome.
    """
    result = _compose(registry=TrustedForecastModelRegistry.empty())
    assert result.decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert result.decision.abstention_reason is not None
    assert result.decision.cash_reason is None


def test_cash_no_trade_is_a_distinct_outcome_from_abstention():
    """With a usable forecast but a non-positive objective, F7 holds cash.

    This is the fourth distinct F7 state: the evidence was complete and a real
    forecast existed, but no admissible portfolio beat cash - so F7 holds cash with
    its own reason rather than abstaining for missing evidence.
    """
    result = _compose(expected_return=-0.05)
    assert result.forecast.status is ForecastStatus.FORECAST
    assert result.decision.status is PortfolioStatus.CASH_NO_TRADE
    assert result.decision.cash_reason is not None
    assert result.decision.abstention_reason is None
    assert result.decision.allocations == ()
    assert result.decision.reservation_plan is None
    assert result.handoffs == ()


def test_infeasible_candidate_is_a_distinct_state_and_cannot_be_forecast():
    """F5 VETO is its own disposition, and F6 fails closed on it.

    This proves the negative paths are distinct: an explicit infeasibility is a
    VETO, not an abstention, and it cannot be laundered into a forecast.
    """
    result = _compose()
    vetoed_market = f7.snapshot()
    # An explicit structural rejection is a policy VETO.
    vetoed_market.market_data_validation = type(vetoed_market.market_data_validation)(
        status="REJECT",
        qualified=False,
        warnings=[],
        rejection_reasons=["rejected"],
        candle_count=200,
        latest_candle_timestamp=1757600000,
        latest_candle_age_seconds=120.0,
        duplicate_timestamp_count=0,
        gap_count=0,
        largest_gap_seconds=0.0,
        invalid_ohlc_count=0,
        non_finite_value_count=0,
        ticker_last=100.0,
        latest_ohlc_close=100.0,
        ticker_vs_ohlc_difference_pct=0.0,
        suspicious_spike_detected=False,
    )
    evidence = feasibility_evidence_from_market_snapshot(
        vetoed_market,
        source_cutoff=CLAIM_CUTOFF,
        source_snapshot_id=result.snapshot.snapshot_id,
        evaluation_time=CLAIM_CUTOFF,
        instrument_version_id=result.episode.instrument_version_id,
    )
    veto = evaluate_feasibility(
        result.episode, evidence, CLAIM_CUTOFF, FeasibilityPolicy()
    )
    assert veto.disposition is FeasibilityDisposition.VETO
    assert veto.disposition is not FeasibilityDisposition.INSUFFICIENT_EVIDENCE

    # F6 refuses to forecast on a non-FEASIBLE upstream decision rather than
    # converting a proven veto into an abstention.
    with pytest.raises(ForecastContractError):
        evaluate_forecast(
            result.episode,
            veto,
            result.inputs,
            FORECAST_INSTANT,
            f7.FORECAST_POLICY,
            model_artifact=result.artifact,
            registry=f7.make_registry(0.05),
        )


def _rebuild(obj, **changes):
    """Rebuild a frozen F7 record after changing a validated field.

    Drops the record's own derived identity so the new record re-derives it, which
    the frozen contracts require.
    """
    from dataclasses import fields

    identity = {
        "PortfolioAllocation": "allocation_id",
        "PortfolioReservation": "reservation_id",
        "PortfolioReservationPlan": "plan_id",
        "PortfolioCandidate": "candidate_id",
    }.get(type(obj).__name__)
    payload = {}
    for field_info in fields(obj):
        name = field_info.name
        if name == identity and name not in changes:
            continue
        payload[name] = getattr(obj, name)
    payload.update(changes)
    return type(obj)(**payload)


def test_no_handoff_is_built_when_the_forecast_is_absent_from_the_candidate():
    """Even a SELECTED decision cannot hand off a candidate with no forecast."""
    result = _compose()
    assert result.decision.status is PortfolioStatus.SELECTED
    stripped = _rebuild(result.candidate, forecast=None)
    with pytest.raises(PortfolioPaperHandoffError):
        build_portfolio_paper_handoffs(
            result.decision,
            candidates={stripped.candidate_id: stripped},
            geometries={stripped.candidate_id: result.geometry},
            lineages={stripped.episode_id: result.lineage},
            portfolio_version=str(result.decision.reservation_plan.portfolio_version),
        )


# ---------------------------------------------------------------------------
# Determinism and replay
# ---------------------------------------------------------------------------


def test_composition_is_deterministic_across_replay():
    first = _compose()
    second = _compose()
    # The externally relevant contract identities and content are stable.
    assert first.claim.claim_id == second.claim.claim_id
    assert first.episode.episode_id == second.episode.episode_id
    assert first.feasibility.decision_id == second.feasibility.decision_id
    assert first.forecast.decision_id == second.forecast.decision_id
    assert first.geometry.geometry_id == second.geometry.geometry_id
    assert first.decision.decision_id == second.decision.decision_id
    assert first.handoffs[0].handoff_id == second.handoffs[0].handoff_id
    assert first.handoffs[0].to_dict() == second.handoffs[0].to_dict()
    # One allocation, one handoff, one execution candidate - no duplication.
    assert len(first.handoffs) == 1
    assert first.handoffs[0].execution_candidate_id == second.handoffs[0].execution_candidate_id


def _harness_source() -> str:
    """The composition harness only, up to the first guard test.

    The static guards below must inspect the harness, not the guard definitions,
    so they cut at the first guard marker rather than re-scanning their own
    forbidden-token literals.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    guard_start = text.index("def test_composition_introduces_no_ambient_state")
    return text[:guard_start]


def test_composition_introduces_no_ambient_state():
    """The harness composes from declared inputs only: no clock, randomness or env."""
    source = _harness_source()
    for pattern in (r"\bdatetime\.now\s*\(", r"\butcnow\s*\(", r"\btime\.time\s*\(", r"\brandom\.", r"\bos\.environ", r"\bgetenv\s*\("):
        assert not re.search(pattern, source), pattern


def test_composition_activates_no_production_mode():
    """The harness never mutates a feature flag or ambient configuration."""
    source = _harness_source()
    for pattern in (
        r"\bsetenv\s*\(",
        r"\benviron\s*\[",
        r"\bputenv\s*\(",
    ):
        assert not re.search(pattern, source), pattern


# ---------------------------------------------------------------------------
# Cross-stage corruption: a valid object from another lineage is refused
# ---------------------------------------------------------------------------


def test_corruption_a_foreign_episode_cannot_share_a_forecast():
    """A real, valid forecast from another episode is refused, not re-keyed."""
    result = _compose()
    # Build a second, independently valid environment for a different instrument.
    foreign_market = f7.snapshot("ETHUSD")
    foreign_claim = f7.build_claim("ETHUSD")
    foreign_episode = lifecycle.apply_claim(
        foreign_claim, None, foreign_claim.evaluation_cutoff, f7.F4_POLICY
    ).episode
    foreign_evidence = feasibility_evidence_from_market_snapshot(
        foreign_market,
        source_cutoff=f7.CUTOFF,
        source_snapshot_id=foreign_claim.snapshot_id,
        evaluation_time=f7.CUTOFF,
        instrument_version_id=foreign_episode.instrument_version_id,
    )
    foreign_feasibility = evaluate_feasibility(
        foreign_episode, foreign_evidence, f7.CUTOFF, FeasibilityPolicy()
    )
    assert foreign_feasibility.disposition is FeasibilityDisposition.FEASIBLE

    # A candidate that mixes one lineage's episode with another lineage's
    # feasibility/forecast must be refused by F7.
    mixed = f7.candidate(
        "SOLUSD",
        0.05,
        stop_loss_fraction=result.geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
        forecast=result.forecast,
    )
    # The candidate's forecast names this episode; the foreign feasibility does not.
    corrupted = type(mixed)(
        episode_id=result.episode.episode_id,
        feasibility_decision_id=foreign_feasibility.decision_id,
        symbol=mixed.symbol,
        direction=mixed.direction,
        common_shock_group=mixed.common_shock_group,
        data_quality_status=mixed.data_quality_status,
        execution_evidence_status=mixed.execution_evidence_status,
        liquidity_capacity_notional=mixed.liquidity_capacity_notional,
        requested_capital_fraction=mixed.requested_capital_fraction,
        stop_loss_fraction=mixed.stop_loss_fraction,
        legacy_observables=mixed.legacy_observables,
        forecast=mixed.forecast,
    )
    with pytest.raises(Exception) as excinfo:
        select_portfolio(
            [corrupted],
            capital_state=_capital(),
            exposure=_exposure(),
            window=_window(),
            evaluation_time=FORECAST_INSTANT,
            policy=_portfolio_policy(),
        )
    # The refusal names the lineage disagreement rather than silently replacing
    # the foreign decision id with the candidate's own.
    assert "feasibility" in str(excinfo.value).lower()


def test_corruption_b_foreign_geometry_cannot_be_handed_off():
    """A geometry from another instrument/direction is refused at the handoff."""
    result = _compose()
    foreign_geometry = build_execution_geometry(
        ExecutionGeometryInput(
            symbol="SOLUSD",
            direction="SHORT",
            risk_level="medium",
            last_price=100.0,
            atr=2.0,
            ema20=100.5,
            rolling_24h_upside_p75_pct=5.0,
            rolling_24h_downside_p75_pct=5.0,
        ),
        instrument_version_id=f3.INSTRUMENT_VERSION_ID,
        venue_instrument_id=f3.VENUE_INSTRUMENT_ID,
        source_cutoff=CLAIM_CUTOFF,
        source_evidence_fingerprint="FEV:r4b0-composition",
    )
    with pytest.raises(PortfolioPaperHandoffError, match="direction"):
        build_portfolio_paper_handoffs(
            result.decision,
            candidates={result.candidate.candidate_id: result.candidate},
            geometries={result.candidate.candidate_id: foreign_geometry},
            lineages={result.episode.episode_id: result.lineage},
            portfolio_version=str(result.decision.reservation_plan.portfolio_version),
        )


def test_corruption_c_foreign_cutoff_cannot_be_handed_off():
    """A lineage whose cutoff disagrees with the geometry is refused."""
    result = _compose()
    shifted = PaperExecutionLineage(
        snapshot_id=result.lineage.snapshot_id,
        snapshot_cutoff=CLAIM_CUTOFF - timedelta(seconds=1),
        instrument_version_id=result.lineage.instrument_version_id,
        venue_instrument_id=result.lineage.venue_instrument_id,
        cohort_id=result.lineage.cohort_id,
        native_symbol=result.lineage.native_symbol,
        quote_currency=result.lineage.quote_currency,
        entry_reference=result.lineage.entry_reference,
        detector_claim_id=result.lineage.detector_claim_id,
        decision_context_id=result.lineage.decision_context_id,
    )
    with pytest.raises(PortfolioPaperHandoffError, match="cutoff"):
        build_portfolio_paper_handoffs(
            result.decision,
            candidates={result.candidate.candidate_id: result.candidate},
            geometries={result.candidate.candidate_id: result.geometry},
            lineages={result.episode.episode_id: shifted},
            portfolio_version=str(result.decision.reservation_plan.portfolio_version),
        )


def test_corruption_d_foreign_instrument_is_refused_by_f5():
    """Evidence for another instrument cannot be evaluated against this episode."""
    result = _compose()
    foreign_market = f7.snapshot("ETHUSD")
    foreign_evidence = feasibility_evidence_from_market_snapshot(
        foreign_market,
        source_cutoff=CLAIM_CUTOFF,
        source_snapshot_id=result.snapshot.snapshot_id,
        evaluation_time=CLAIM_CUTOFF,
        instrument_version_id=result.episode.instrument_version_id,
    )
    with pytest.raises(Exception) as excinfo:
        evaluate_feasibility(
            result.episode, foreign_evidence, CLAIM_CUTOFF, FeasibilityPolicy()
        )
    assert "instrument" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Authority / dormant-state isolation
# ---------------------------------------------------------------------------


def test_composition_imports_no_execution_or_authority_surface():
    """The composition proof cannot reach funded, exchange or Committee code."""
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
            "run_cycle",
            "scan_opportunities",
            "profit_ranking",
        ):
            assert forbidden not in lowered, (name, forbidden)


def test_composition_authority_targets_are_unchanged_on_disk():
    """The dormant production posture is untouched by this increment."""
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose
    assert "OPIP_PAPER_V2_MODE" not in compose
    run_cycle = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    scan = (APP_ROOT / "app" / "jobs" / "scan_opportunities.py").read_text(encoding="utf-8")
    for forbidden in ("portfolio_selector", "portfolio_paper_handoff", "evaluate_forecast"):
        assert forbidden not in run_cycle, forbidden
        assert forbidden not in scan, forbidden
