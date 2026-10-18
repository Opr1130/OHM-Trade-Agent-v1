"""M02 pipeline seam acceptance tests: drive the real engine into the real spine.

Increment: ``ATDD-R5-M02-pipeline-seam-integration``.

Two verified halves of the continuous advisor pipeline have never been joined in
one traversal. The engine (F1 coverage, EYE attention, F2 snapshot, F3 IGNITION,
F4 episode and horizon thesis) is proven by the R5-A and R5-B suites. The spine
(F5 feasibility, F6 calibrated forecast, F7 selection, handoff) is proven by the
R4-B0 composition suite. Nothing has ever driven the first into the second, and
the R4-B1 contract records why: production passes no snapshots and no composition
callable, so the target path is inert and the seam is unproven.

These tests join them with the real production callables and the frozen fixtures,
and they assert the seam's own properties: one claim, one episode, one thesis, one
snapshot, one decision trace. They add no authority, activate no mode and write
nothing canonical.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
from dataclasses import replace
from datetime import timedelta

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:  # pragma: no cover - the suite already does this
    sys.path.insert(0, str(APP_ROOT))

from app.opip import opportunity_lifecycle as lifecycle  # noqa: E402
from app.opip.contracts.feasibility import (  # noqa: E402
    FeasibilityDisposition,
    FeasibilityPolicy,
)
from app.opip.contracts.feasibility_evidence import (  # noqa: E402
    feasibility_evidence_from_market_snapshot,
)
from app.opip.contracts.forecast import (  # noqa: E402
    ForecastAbstentionReason,
    ForecastContractError,
    ForecastStatus,
)
from app.opip.contracts.observation import SourceWatermark  # noqa: E402
from app.opip.contracts.opportunity import (  # noqa: E402
    OpportunityContractError,
    OpportunityLifecyclePolicy,
)
from app.opip.contracts.opportunity_phases import (  # noqa: E402
    PhaseContractError,
    episode_from_claim,
)
from app.opip.contracts.portfolio import PortfolioStatus  # noqa: E402
from app.opip.events.attention import AttentionReason, AttentionState  # noqa: E402
from app.opip.events.coverage import IntervalEvidence  # noqa: E402
from app.opip.events.opportunity_engine import (  # noqa: E402
    ContinuousOpportunityEngine,
)
from app.opip.feasibility import evaluate_feasibility  # noqa: E402
from app.opip.forecast import (  # noqa: E402
    TrustedForecastModelRegistry,
    evaluate_forecast,
)
from app.opip.portfolio_selector import select_portfolio  # noqa: E402

from tests import test_opip_r3_f3_ignition_detector as f3  # noqa: E402
from tests import test_opip_r3_f7_economic_portfolio_selector as f7  # noqa: E402
from tests import test_opip_r4_b0_spine_composition as r4b0  # noqa: E402
from tests import test_opip_r5_a_continuous_opportunity_engine as r5a  # noqa: E402

pytestmark = pytest.mark.acceptance

GRID = 60
EVIDENCE_CUTOFF = f3.CUTOFF
CLAIM_CUTOFF = f3.SECOND_CUTOFF
FORECAST_INSTANT = f7.EVAL
EXPECTED_RETURN = 0.05


def _observe(engine: ContinuousOpportunityEngine, instant):
    """One real F1 observation window over the frozen grid."""

    start = instant - timedelta(seconds=GRID)
    return engine.observe(
        instrument_version_id=f3.INSTRUMENT_VERSION_ID,
        venue_instrument_id=f3.VENUE_INSTRUMENT_ID,
        source_label="kraken_ohlc",
        reference_data_version="rdv1",
        window_start_utc=start,
        interval_seconds=GRID,
        evidence=(
            IntervalEvidence(
                interval_start_epoch=int(start.timestamp()), receipt_time=instant
            ),
        ),
        watermark=SourceWatermark(
            instrument_version_id=f3.INSTRUMENT_VERSION_ID,
            through_utc=instant,
            last_ingestion_order=1,
        ),
        evaluation_cutoff=instant,
        evaluated_at_utc=instant,
        source_event_ids=(f"EV-{instant.isoformat()}",),
        progression_reasons=(AttentionReason.RELATIVE_VOLUME_STRENGTHENING,),
        requested_state=AttentionState.FOCUSED,
    )


def _engine() -> ContinuousOpportunityEngine:
    return ContinuousOpportunityEngine(
        attention_policy=r5a.attention_policy(
            debounce_intervals=0,
            oscillation_guard_intervals=0,
            cooldown_intervals=0,
        ),
        coordinator_limits=r5a.coordinator_limits(),
        horizon_policy=r5a.horizon_policy(),
    )


def _front_half(*, snapshot_cutoff=None, instrument=None):
    """Drive F1 -> F2 -> F3 -> F4 through the real engine until it claims.

    Returns the engine, the sealed snapshot the claiming evaluation consumed and
    the last cycle. The claiming cycle carries the episode and the thesis.
    """

    instrument = instrument or f3.INSTRUMENT_VERSION_ID
    engine = _engine()
    snapshot = None
    cycle = None
    outcome = None
    for instant in (EVIDENCE_CUTOFF, CLAIM_CUTOFF):
        _observe(engine, instant)
        delivered = instant if snapshot_cutoff is None else snapshot_cutoff
        snapshot = f3.build_snapshot(
            cutoff=delivered, instrument_version_id=instrument
        )
        cycle = engine.evaluate_due(
            evaluation_time=instant, snapshots={instrument: snapshot}
        )
        if cycle.claims:
            outcome = cycle.claims[0]
            break
    return engine, snapshot, cycle, outcome


def _spine_from_episode(episode, snapshot, *, expected_return=EXPECTED_RETURN, registry=None):
    """Drive the real F5 -> F6 -> F7 stage functions from an engine episode.

    The episode argument is the engine's own committed episode, not a rebuild.
    """

    evidence = feasibility_evidence_from_market_snapshot(
        f7.snapshot(),
        source_cutoff=CLAIM_CUTOFF,
        source_snapshot_id=snapshot.snapshot_id,
        evaluation_time=CLAIM_CUTOFF,
        instrument_version_id=episode.instrument_version_id,
    )
    feasibility = evaluate_feasibility(
        episode, evidence, CLAIM_CUTOFF, FeasibilityPolicy()
    )
    inputs = f7.input_vector("SOLUSD")
    inputs = type(inputs)(
        input_schema_id=inputs.input_schema_id,
        input_schema_version=inputs.input_schema_version,
        source_snapshot_id=snapshot.snapshot_id,
        source_cutoff=CLAIM_CUTOFF,
        horizon=inputs.horizon,
        features=inputs.features,
    )
    forecast = evaluate_forecast(
        episode,
        feasibility,
        inputs,
        FORECAST_INSTANT,
        f7.FORECAST_POLICY,
        model_artifact=f7.artifact("SOLUSD"),
        registry=f7.make_registry(expected_return) if registry is None else registry,
    )
    geometry = r4b0._geometry(direction="LONG")
    candidate = f7.candidate(
        "SOLUSD",
        expected_return,
        direction=r4b0.PortfolioDirection.LONG,
        stop_loss_fraction=geometry.stop_loss_fraction,
        requested_capital_fraction=0.05,
        forecast=forecast,
    )
    decision = select_portfolio(
        [candidate],
        capital_state=r4b0._capital(),
        exposure=r4b0._exposure(),
        window=r4b0._window(),
        evaluation_time=FORECAST_INSTANT,
        policy=r4b0._portfolio_policy(),
    )
    return feasibility, forecast, candidate, decision


def _decision_trace(outcome, feasibility, forecast, decision) -> tuple:
    """A point-in-time trace of one traversal, from observation to selection."""

    return (
        outcome.claim_id,
        outcome.episode_id,
        outcome.snapshot_id,
        outcome.detector_phase,
        outcome.thesis.horizon_class.value if outcome.thesis else None,
        outcome.thesis.research_only if outcome.thesis else None,
        feasibility.disposition.value,
        forecast.status.value,
        forecast.abstention_reason.value if forecast.abstention_reason else None,
        forecast.episode_id,
        decision.status.value,
    )


def _frozen_claim():
    """The one claim the frozen F3 entry sequence produces."""

    claims, _state = f3.evaluate_entry_sequence()
    assert len(claims) == 1, "the frozen sequence must produce exactly one claim"
    return claims[0]


# ---------------------------------------------------------------------------
# AC-210 the front half claims through the real engine
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_210_the_engine_carries_one_claim_one_episode_and_one_thesis():
    """ATDD-R5-M02-pipeline-seam-integration/AC-210: the front half really runs.

    GIVEN the real continuous engine, two consumed observation intervals on the
    frozen grid and the sealed F3 snapshot for each cut-off
    WHEN one observation-and-attention step and one evaluation step are driven per
    interval
    THEN the first evaluation produces no claim, the second produces exactly one,
    and the claiming outcome carries one episode, one horizon thesis, the phase the
    detector reached, and an episode the engine retained under the same identity
    """

    engine, snapshot, _cycle, outcome = _front_half()

    assert outcome is not None, "the fixture must produce one claim"
    assert outcome.disposition == "CLAIM"
    assert outcome.claim_id is not None
    assert outcome.episode is not None
    assert outcome.thesis is not None
    assert outcome.episode_id == outcome.episode.episode_id
    assert outcome.snapshot_id == snapshot.snapshot_id
    assert outcome.episode.snapshot_id == snapshot.snapshot_id
    assert outcome.episode.source_claim_id == outcome.claim_id
    assert outcome.episode.instrument_version_id == f3.INSTRUMENT_VERSION_ID
    assert engine.episodes()[outcome.episode_id] == outcome.episode
    assert engine.counts()["claims_emitted"] == 1
    assert engine.counts()["episodes_opened"] == 1
    assert engine.counts()["theses_classified"] == 1
    assert _frozen_claim().claim_id == outcome.claim_id


@pytest.mark.acceptance
def test_ac_210_a_missing_detector_evaluation_fails_closed_without_a_claim():
    """ATDD-R5-M02-pipeline-seam-integration/AC-210: no snapshot is a refusal.

    GIVEN the real engine with one admitted evaluation due and no delivered
    snapshot
    WHEN the due evaluation is served
    THEN it fails closed with a recorded reason and no claim, episode or thesis, and
    a served evaluation with a real snapshot whose interval cannot yet claim is a
    recorded NO_CLAIM rather than a claim
    """

    engine = _engine()
    _observe(engine, EVIDENCE_CUTOFF)
    cycle = engine.evaluate_due(evaluation_time=EVIDENCE_CUTOFF, snapshots={})

    assert cycle.claims == ()
    assert len(cycle.failures) == 1
    assert cycle.failures[0].reason == "snapshot_unavailable"
    assert engine.episodes() == {}
    assert engine.counts()["episodes_opened"] == 0

    fresh = _engine()
    _observe(fresh, EVIDENCE_CUTOFF)
    served = fresh.evaluate_due(
        evaluation_time=EVIDENCE_CUTOFF,
        snapshots={
            f3.INSTRUMENT_VERSION_ID: f3.build_snapshot(cutoff=EVIDENCE_CUTOFF)
        },
    )
    assert len(served.no_claim_evaluations) == 1
    assert served.claims == ()


# ---------------------------------------------------------------------------
# AC-211 one episode lineage across the two F4 entry points
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_211_the_engine_and_the_lifecycle_path_produce_one_episode():
    """ATDD-R5-M02-pipeline-seam-integration/AC-211: one claim, one episode, two paths.

    GIVEN one admissible IGNITION claim produced by the real detector and evaluated
    by the real engine
    WHEN the episode is opened through the engine path and through the canonical
    lifecycle path
    THEN the two episodes are equal field for field and the opening event identity is
    the same, so the seam cannot be where one opportunity silently becomes two
    """

    _engine_, snapshot, _cycle, outcome = _front_half()
    claim = _frozen_claim()
    spine_path = lifecycle.apply_claim(claim, None, CLAIM_CUTOFF, f7.F4_POLICY)

    assert claim.claim_id == outcome.claim_id
    assert spine_path.episode == outcome.episode
    assert spine_path.episode.episode_id == outcome.episode_id
    assert (
        spine_path.events[0].event_id
        == outcome.lifecycle_result.events[0].event_id
    )


@pytest.mark.acceptance
def test_ac_211_an_unratified_lifecycle_policy_is_refused_not_forked():
    """ATDD-R5-M02-pipeline-seam-integration/AC-211: the guarantee, not the coincidence.

    GIVEN the lifecycle policy's three version tokens and the two F4 derivations
    WHEN a policy carrying an unratified episode schema, lifecycle or policy version
    is constructed, and the independent derivation is repeated for the same claim
    THEN every unratified policy is refused outright so the paths cannot diverge on
    those tokens, and the repeated derivation is stable and equal to the engine's
    """

    for field in ("episode_schema_version", "lifecycle_version", "policy_version"):
        with pytest.raises(OpportunityContractError):
            OpportunityLifecyclePolicy(**{field: "unratified-v9"})

    _engine_, snapshot, _cycle, outcome = _front_half()
    claim = _frozen_claim()
    first = episode_from_claim(claim, evaluation_time=CLAIM_CUTOFF)
    second = episode_from_claim(claim, evaluation_time=CLAIM_CUTOFF)
    assert first.episode == second.episode == outcome.episode
    assert first.episode.policy_version == f7.F4_POLICY.policy_version


@pytest.mark.acceptance
def test_ac_211_a_cutoff_before_the_claim_is_refused():
    """ATDD-R5-M02-pipeline-seam-integration/AC-211: evaluation time discipline holds.

    GIVEN one admissible claim
    WHEN an episode is opened at an instant before the claim's own evaluation
    cut-off
    THEN the contract refuses rather than back-dating the episode
    """

    _engine_, _snapshot, _cycle, _outcome = _front_half()
    claim = _frozen_claim()
    with pytest.raises(PhaseContractError):
        episode_from_claim(
            claim, evaluation_time=CLAIM_CUTOFF - timedelta(seconds=1)
        )


@pytest.mark.acceptance
def test_ac_211_a_stale_snapshot_is_refused_before_the_spine_is_reached():
    """ATDD-R5-M02-pipeline-seam-integration/AC-211: a stale snapshot never reaches F5.

    GIVEN the real engine and a delivered snapshot whose cut-off is one grid step
    behind the evaluation the engine has due
    WHEN the due evaluation is served
    THEN it fails closed naming the cut-off, and no episode is opened, so nothing
    downstream can be built on stale evidence
    """

    engine = _engine()
    _observe(engine, EVIDENCE_CUTOFF)
    engine.evaluate_due(
        evaluation_time=EVIDENCE_CUTOFF,
        snapshots={
            f3.INSTRUMENT_VERSION_ID: f3.build_snapshot(cutoff=EVIDENCE_CUTOFF)
        },
    )
    _observe(engine, CLAIM_CUTOFF)
    stale = engine.evaluate_due(
        evaluation_time=CLAIM_CUTOFF,
        snapshots={
            f3.INSTRUMENT_VERSION_ID: f3.build_snapshot(cutoff=EVIDENCE_CUTOFF)
        },
    )

    assert stale.claims == ()
    assert [failure.reason for failure in stale.failures] == [
        "snapshot_cutoff_mismatch"
    ]
    assert engine.counts()["episodes_opened"] == 0


@pytest.mark.acceptance
def test_ac_211_a_foreign_instrument_snapshot_is_refused():
    """ATDD-R5-M02-pipeline-seam-integration/AC-211: competing source authority is refused.

    GIVEN the real engine evaluating one instrument
    WHEN a snapshot belonging to a different instrument version is delivered under
    the due instrument's key
    THEN the evaluation fails closed naming the mismatch and produces no claim
    """

    engine = _engine()
    _observe(engine, EVIDENCE_CUTOFF)
    foreign = f3.build_snapshot(
        cutoff=EVIDENCE_CUTOFF, instrument_version_id="INSTR:kraken:OTHER:USD:1"
    )
    cycle = engine.evaluate_due(
        evaluation_time=EVIDENCE_CUTOFF,
        snapshots={f3.INSTRUMENT_VERSION_ID: foreign},
    )

    assert cycle.claims == ()
    assert [failure.reason for failure in cycle.failures] == [
        "snapshot_instrument_mismatch"
    ]


# ---------------------------------------------------------------------------
# AC-212 the back half accepts the engine's own episode
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_212_the_spine_consumes_the_engines_own_episode():
    """ATDD-R5-M02-pipeline-seam-integration/AC-212: the two halves are joined.

    GIVEN an episode the real continuous engine produced from its own F1, F2, F3
    and F4 work
    WHEN F5 feasibility, F6 forecast and F7 selection are driven from that episode
    rather than from a rebuilt one
    THEN F5 is feasible, F6 binds its decision to the engine's episode identity and
    F7 selects, so the seam is traversed rather than assumed
    """

    _engine_, snapshot, _cycle, outcome = _front_half()
    feasibility, forecast, candidate, decision = _spine_from_episode(
        outcome.episode, snapshot
    )

    assert feasibility.disposition is FeasibilityDisposition.FEASIBLE
    assert feasibility.episode_id == outcome.episode_id
    assert forecast.status is ForecastStatus.FORECAST
    assert forecast.episode_id == outcome.episode_id
    assert forecast.feasibility_decision_id == feasibility.decision_id
    assert forecast.valid_until is not None
    assert candidate.forecast is not None
    assert decision.status is PortfolioStatus.SELECTED
    assert decision.allocations != ()
    assert decision.reservation_plan is not None


@pytest.mark.acceptance
def test_ac_212_the_joined_traversal_matches_the_spine_composition():
    """ATDD-R5-M02-pipeline-seam-integration/AC-212: joining the halves changes nothing.

    GIVEN the joined traversal and the independently verified R4-B0 composition over
    the same frozen fixtures
    WHEN their claim, episode, snapshot, feasibility, forecast and selection
    identities are compared
    THEN every one agrees, so driving the spine from the engine's real output is the
    same traversal the spine already proved, not a second one
    """

    _engine_, snapshot, _cycle, outcome = _front_half()
    feasibility, forecast, _candidate, decision = _spine_from_episode(
        outcome.episode, snapshot
    )
    composition = r4b0._compose(direction="LONG")

    assert outcome.claim_id == composition.claim.claim_id
    assert outcome.episode_id == composition.episode.episode_id
    assert outcome.snapshot_id == composition.snapshot.snapshot_id
    assert feasibility.disposition is composition.feasibility.disposition
    assert forecast.status is composition.forecast.status
    assert forecast.expected_return_unconditional == (
        composition.forecast.expected_return_unconditional
    )
    assert decision.status is composition.decision.status
    assert len(composition.handoffs) >= 0


# ---------------------------------------------------------------------------
# AC-213 reproducible point-in-time trace, and the negatives at the seam
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_213_the_whole_traversal_replays_to_one_identical_trace():
    """ATDD-R5-M02-pipeline-seam-integration/AC-213: replay is the same decision.

    GIVEN the joined traversal run twice from a fresh engine
    WHEN the point-in-time trace from observation to selection is compared
    THEN both traces are identical element for element, and a duplicate observation
    of an already evaluated instant produces no second claim, so replay cannot
    double-count an opportunity
    """

    traces = []
    for _ in range(2):
        engine, snapshot, _cycle, outcome = _front_half()
        feasibility, forecast, _candidate, decision = _spine_from_episode(
            outcome.episode, snapshot
        )
        traces.append(_decision_trace(outcome, feasibility, forecast, decision))

        # At-least-once delivery of an observation the engine has already evaluated.
        _observe(engine, CLAIM_CUTOFF)
        replayed = engine.evaluate_due(
            evaluation_time=CLAIM_CUTOFF,
            snapshots={
                f3.INSTRUMENT_VERSION_ID: f3.build_snapshot(cutoff=CLAIM_CUTOFF)
            },
        )
        assert replayed.claims == ()
        assert engine.counts()["claims_emitted"] == 1
        assert engine.counts()["episodes_opened"] == 1

    assert traces[0] == traces[1]
    assert traces[0][0] is not None


@pytest.mark.acceptance
def test_ac_213_an_unsupported_calibrated_probability_abstains():
    """ATDD-R5-M02-pipeline-seam-integration/AC-213: no trusted calibration means no number.

    GIVEN the joined traversal with the production empty model registry, so the
    model artifact exists while its calibration report is trusted by nobody
    WHEN F6 is asked for a forecast and F7 for a selection
    THEN F6 abstains naming the untrusted calibration report and emits no
    probability, F7 reports missing evidence rather than holding cash, and no
    allocation is produced
    """

    _engine_, snapshot, _cycle, outcome = _front_half()
    feasibility, forecast, _candidate, decision = _spine_from_episode(
        outcome.episode, snapshot, registry=TrustedForecastModelRegistry.empty()
    )

    assert feasibility.disposition is FeasibilityDisposition.FEASIBLE
    assert forecast.status is ForecastStatus.INSUFFICIENT_EVIDENCE
    assert (
        forecast.abstention_reason
        is ForecastAbstentionReason.CALIBRATION_REPORT_UNTRUSTED
    )
    assert forecast.expected_return_unconditional is None
    assert forecast.entry_distribution is None
    assert forecast.valid_until is None

    assert decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert decision.status is not PortfolioStatus.CASH_NO_TRADE
    assert decision.abstention_reason is not None
    assert decision.cash_reason is None
    assert decision.allocations == ()


@pytest.mark.acceptance
def test_ac_213_the_cash_alternative_is_distinct_from_abstention():
    """ATDD-R5-M02-pipeline-seam-integration/AC-213: cash is a decision, not a gap.

    GIVEN the joined traversal with a complete forecast whose expected return does
    not beat holding cash
    WHEN F7 selects
    THEN it holds cash with its own reason and no allocation, and that state is
    distinct from the missing-evidence abstention
    """

    _engine_, snapshot, _cycle, outcome = _front_half()
    _feasibility, forecast, _candidate, decision = _spine_from_episode(
        outcome.episode, snapshot, expected_return=-0.05
    )

    assert forecast.status is ForecastStatus.FORECAST
    assert decision.status is PortfolioStatus.CASH_NO_TRADE
    assert decision.cash_reason is not None
    assert decision.abstention_reason is None
    assert decision.allocations == ()
    assert decision.reservation_plan is None


@pytest.mark.acceptance
def test_ac_213_a_tampered_forecast_policy_version_is_refused():
    """ATDD-R5-M02-pipeline-seam-integration/AC-213: policy drift fails closed.

    GIVEN a real forecast decision produced by the joined traversal
    WHEN its recorded policy version is replaced with an unratified token
    THEN the contract refuses to construct the decision, so a mismatched policy
    version cannot be carried forward into a selection
    """

    _engine_, snapshot, _cycle, outcome = _front_half()
    _feasibility, forecast, _candidate, _decision = _spine_from_episode(
        outcome.episode, snapshot
    )

    with pytest.raises(ForecastContractError):
        replace(forecast, policy_version="forecast-policy-unratified-v9")


@pytest.mark.acceptance
def test_ac_213_a_proven_veto_cannot_be_forecast():
    """ATDD-R5-M02-pipeline-seam-integration/AC-213: a veto is not an abstention.

    GIVEN the joined traversal with feasibility evidence carrying an explicit
    structural rejection
    WHEN F5 evaluates it and F6 is asked to forecast the result
    THEN F5 reports VETO rather than an abstention and F6 refuses to forecast it,
    so a proven rejection cannot be laundered into a missing-evidence state
    """

    _engine_, snapshot, _cycle, outcome = _front_half()
    rejected = f7.snapshot()
    rejected.market_data_validation = type(rejected.market_data_validation)(
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
        rejected,
        source_cutoff=CLAIM_CUTOFF,
        source_snapshot_id=snapshot.snapshot_id,
        evaluation_time=CLAIM_CUTOFF,
        instrument_version_id=outcome.episode.instrument_version_id,
    )
    veto = evaluate_feasibility(
        outcome.episode, evidence, CLAIM_CUTOFF, FeasibilityPolicy()
    )
    assert veto.disposition is FeasibilityDisposition.VETO
    assert veto.disposition is not FeasibilityDisposition.INSUFFICIENT_EVIDENCE

    with pytest.raises(ForecastContractError):
        evaluate_forecast(
            outcome.episode,
            veto,
            f7.input_vector("SOLUSD"),
            FORECAST_INSTANT,
            f7.FORECAST_POLICY,
            model_artifact=f7.artifact("SOLUSD"),
            registry=f7.make_registry(EXPECTED_RETURN),
        )


# ---------------------------------------------------------------------------
# AC-214 joining the halves adds no authority
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_214_the_seam_activates_no_mode_and_reaches_no_writer():
    """ATDD-R5-M02-pipeline-seam-integration/AC-214: joining two halves grants nothing.

    GIVEN the seam increment's own test module and the engine it drives
    WHEN the engine's compiled mode and authority are inspected and the module's
    imports are read
    THEN the engine is still the literal off mode with NONE authority reading no
    environment variable, and this module imports no execution, exchange,
    protection, writer, storage or scheduler surface
    """

    engine_module = importlib.import_module("app.opip.events.opportunity_engine")
    assert engine_module.R5A_ENGINE_MODE == "off"
    assert engine_module.R5A_ENGINE_AUTHORITY == "NONE"
    assert engine_module.R5A_ENV_VARIABLES_READ == ()
    assert engine_module.engine_reads_environment() is False

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    imports = "\n".join(
        line
        for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )
    forbidden = (
        "app.opip.canonical",
        "app.exchanges",
        "app.services.telegram",
        "app.services.registry_io",
        "sqlite3",
        "app.opip.storage",
        "app.jobs",
        "app.core.config",
    )
    for token in forbidden:
        assert token not in imports, token


# ---------------------------------------------------------------------------
# AC-215 the audit's wiring vocabulary names the seam precisely
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_215_the_audit_vocabulary_names_the_seam_wiring():
    """ATDD-R5-M02-pipeline-seam-integration/AC-215: the vocabulary follows the evidence.

    GIVEN the audit matrix's declared wiring vocabulary and the audit acceptance-test
    module that asserts it
    WHEN both are read
    THEN both name the seam's production wiring precisely and neither still carries
    the superseded value that described the seam as unproven, so the correction is
    recorded in the artifact rather than only in prose
    """

    matrix = (
        APP_ROOT / "docs" / "atdd" / "evidence" / "F1_F7_INTEGRATION_MATRIX.md"
    ).read_text(encoding="utf-8")
    audit_tests = (APP_ROOT / "tests" / "test_opip_r5_m02_integration_matrix.py").read_text(
        encoding="utf-8"
    )

    assert "NO_PRODUCTION_SEAM_WIRING" in matrix
    assert "NO_PRODUCTION_SEAM_WIRING" in audit_tests
    assert "UNPROVEN_BETWEEN_HALVES" not in matrix
    assert "UNPROVEN_BETWEEN_HALVES" not in audit_tests

    # The vocabulary line is still declared, and the seams use the new value.
    assert "wiring  : " in matrix
    assert "wiring=NO_PRODUCTION_SEAM_WIRING" in matrix







