"""Issue #223: early detection, high-confidence qualification, validation parity.

The RAY episode is the anchoring fixture: +19% in 1h, +33% in 6h, ~11.4x
relative volume, effectively at its 24h high. That is a completed move, and
the tests below assert it can never again be presented as an early,
actionable discovery — while also asserting that nothing in this build widens
discovery, loosens a threshold, or changes trading authority.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.jobs import scan_movers
from app.opip.early import flags
from app.opip.early.cohort_selector import (
    COHORT_CURRENT_MOMENTUM,
    COHORT_IGNITION,
    DEFAULT_COHORT_QUOTAS,
    EarlyCandidateFeatures,
    select_early_candidates,
)
from app.opip.early.operator_semantics import (
    build_operator_assessment,
    continuation_score_label,
    format_heuristic_score,
    misleading_confidence_language,
    misleading_early_language,
    score_headroom,
)
from app.opip.early.point_in_time import (
    LookaheadError,
    PointInTimeWindow,
    assert_point_in_time_safe,
)
from app.opip.early.promotion import (
    GATE_ORDER,
    GateVerdict,
    PromotionStatus,
    evaluate_promotion,
)
from app.opip.early.replay import (
    CohortLabel,
    label_cohort_member,
    replay_counterfactual,
    replay_forensic,
    verify_forensic_replay_matches_production,
)
from app.opip.early.stage0_evidence import (
    CoarseRankContext,
    ScoreComponents,
    Stage0DecisionFeatures,
    build_below_threshold_metadata,
    build_rank_limit_metadata,
    rank_contexts_for_truncation,
)
from app.opip.early.taxonomy import (
    EvidenceGrade,
    MarketPhase,
    OperatorDisposition,
    extension_state,
    is_early_phase,
    is_extended_phase,
    resolve_market_phase,
    resolve_operator_disposition,
)
from app.opip.early.timeframe_policy import (
    TIMEFRAME_FINE,
    TIMEFRAME_HOURLY,
    candidate_timeframe_decision,
    compare_timeframe_policies,
    current_timeframe_decision,
)
from app.opip.early.timing_ledger import (
    MILESTONE_FIRST_DELIVERED,
    MILESTONE_FIRST_OBSERVED,
    MILESTONE_FIRST_OPERATOR_ALERT,
    new_ledger,
    observe_phase,
    record_card_created,
    record_card_edited,
    record_notification_delivered,
    summarize_delivery_timing,
)
from app.opip.early.validation_parity import (
    CHECK_BAD_PRINT,
    CHECK_MIN_LIQUIDITY,
    CHECK_PERSISTENCE,
    CHECK_SPREAD,
    MANDATORY_CHECKS,
    advisory_only_promotion_attempt,
    evaluate_early_watch_validations,
)
from app.opip.early.taxonomy import ValidationClass, ValidationResult
from app.services import movement_discovery_v2 as discovery

# RAY at the moment it was alerted: the move had already happened.
RAY_MOMENTUM_1H_PCT = 19.02
RAY_MOMENTUM_6H_PCT = 33.18
RAY_RELATIVE_VOLUME = 11.4
RAY_DISTANCE_TO_HIGH_PCT = 0.4

DECISION_AT = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)


def _fully_valid_validation_kwargs(**overrides):
    """Arguments under which every mandatory check passes.

    Each fail-closed test starts from this and breaks exactly one thing, which
    is what proves the gates block independently rather than collectively.
    """
    kwargs = {
        "market_data_validation": SimpleNamespace(
            qualified=True, status="PASS", rejection_reasons=[], candle_count=720
        ),
        "symbol_identity_resolved": True,
        "completed_candle_count": 720,
        "liquidity_24h_usd": 1_500_000.0,
        "ticker_last": 100.0,
        "latest_ohlc_close": 100.2,
        "ticker_bid": 99.95,
        "ticker_ask": 100.05,
        "finite_features": True,
        "persistence_scans": 3,
        "prior_observation_count": 5,
        "duplicate_state_detected": False,
    }
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# RAY regression: a completed move cannot masquerade as an early discovery
# ---------------------------------------------------------------------------

def test_ray_fixture_resolves_to_late_extension_not_early():
    phase = resolve_market_phase(
        momentum_1h_pct=RAY_MOMENTUM_1H_PCT,
        momentum_6h_pct=RAY_MOMENTUM_6H_PCT,
        momentum_24h_pct=0.0,
        distance_to_24h_high_pct=RAY_DISTANCE_TO_HIGH_PCT,
        relative_volume=RAY_RELATIVE_VOLUME,
    )

    assert phase in {MarketPhase.LATE_EXTENSION, MarketPhase.EXHAUSTION_RISK}
    assert is_extended_phase(phase)
    assert not is_early_phase(phase)


def test_ray_fixture_is_do_not_chase_even_when_qualified():
    phase = resolve_market_phase(
        momentum_1h_pct=RAY_MOMENTUM_1H_PCT,
        momentum_6h_pct=RAY_MOMENTUM_6H_PCT,
        momentum_24h_pct=0.0,
        distance_to_24h_high_pct=RAY_DISTANCE_TO_HIGH_PCT,
        relative_volume=RAY_RELATIVE_VOLUME,
    )

    disposition = resolve_operator_disposition(
        phase=phase,
        grade=EvidenceGrade.QUALIFIED,
        entry_recommendation="WAIT_FOR_PULLBACK",
    )

    # LATE_EXTENSION + QUALIFIED + DO_NOT_CHASE is an explicitly valid triple.
    assert disposition is OperatorDisposition.DO_NOT_CHASE


def test_ray_card_contains_no_early_or_ready_wording():
    signal = SimpleNamespace(
        symbol="RAYUSD",
        stage="READY",
        reference_price=2.6,
        detection_timeframe="1H",
        momentum_1h_pct=RAY_MOMENTUM_1H_PCT,
        momentum_6h_pct=RAY_MOMENTUM_6H_PCT,
        momentum_24h_pct=41.0,
        momentum_state="ACCELERATING",
        continuation_confidence=100,
        continuation_confidence_is_probability=False,
        entry_quality=40,
        entry_recommendation="WAIT_FOR_PULLBACK",
        relative_volume=RAY_RELATIVE_VOLUME,
        distance_to_24h_high_pct=RAY_DISTANCE_TO_HIGH_PCT,
        liquidity_24h_usd_approx=4_000_000.0,
        extended_move=True,
        reasons=("relative volume expanded to 11.40x",),
        market_phase=MarketPhase.LATE_EXTENSION.value,
        evidence_grade=EvidenceGrade.QUALIFIED.value,
        operator_disposition=OperatorDisposition.DO_NOT_CHASE.value,
        actionability_reasons=("move is already extended",),
    )

    card = scan_movers._compact_card(signal)
    assessment = scan_movers._signal_assessment(signal)

    assert misleading_early_language(card, assessment) == ()
    assert "EARLY WATCH" not in card
    # "already extended" is fine; a bare READY claim is not.
    assert "— READY" not in card
    assert "Market: LATE_EXTENSION" in card
    assert "Disposition: DO NOT CHASE" in card
    assert "Action: WATCH ONLY — no entry is authorized" in card


def test_extension_state_flags_ray_and_reports_definition_disagreement():
    state = extension_state(
        momentum_1h_pct=RAY_MOMENTUM_1H_PCT,
        momentum_6h_pct=RAY_MOMENTUM_6H_PCT,
        momentum_24h_pct=0.0,
        distance_to_24h_high_pct=RAY_DISTANCE_TO_HIGH_PCT,
    )

    assert state.extended
    assert state.late_extension
    # The point of the canonical policy is that disagreement is recorded
    # rather than silently resolved in production gating.
    assert "movement_discovery_v2" in state.agreeing_definitions
    assert state.as_dict()["policy_version"]


# ---------------------------------------------------------------------------
# Confidence / score rendering
# ---------------------------------------------------------------------------

def test_non_probability_score_renders_out_of_one_hundred():
    assert format_heuristic_score(82, is_probability=False) == "82/100"
    assert format_heuristic_score(82, is_probability=True) == "82%"
    assert format_heuristic_score(None, is_probability=False) == "unavailable"


@pytest.mark.parametrize("value", [0, 1, 37, 65, 99, 100])
def test_score_label_never_uses_percentage_confidence(value):
    signal = SimpleNamespace(
        continuation_confidence=value,
        continuation_confidence_is_probability=False,
    )

    label = continuation_score_label(signal)

    assert label == f"Continuation score*: {value}/100"
    assert "%" not in label
    assert misleading_confidence_language(label, is_probability=False) == ()


def test_percentage_confidence_for_heuristic_score_is_detected():
    offenders = misleading_confidence_language("Confidence*: 82%", is_probability=False)

    assert offenders


def test_saturated_score_exposes_raw_headroom():
    # The RAY component sum exceeds 100 before clamping, so 100/100 carries no
    # cross-sectional information on its own.
    headroom = score_headroom(bounded_score=100, raw_score=118, peer_scores=[40, 55, 70, 100])

    assert headroom.saturated
    assert headroom.headroom == pytest.approx(18.0)
    assert headroom.as_dict()["is_probability"] is False
    assert headroom.cross_sectional_percentile == pytest.approx(100.0)


def test_evaluate_early_mover_keeps_bounded_score_and_adds_raw():
    snapshot = _ray_snapshot()
    mover = _ray_mover()

    signal = discovery.evaluate_early_mover(snapshot, mover, validation_parity_enabled=False)

    assert signal is not None
    # Bounded values keep their exact historical meaning for every existing
    # consumer; the raw totals are additive.
    assert 0 <= signal.discovery_score <= 100
    assert 0 <= signal.continuation_confidence <= 100
    assert signal.discovery_score_raw >= signal.discovery_score


# ---------------------------------------------------------------------------
# Selection: reserved cohorts cannot be monopolised by extended movers
# ---------------------------------------------------------------------------

def _extended_mover(index: int) -> EarlyCandidateFeatures:
    """An already-completed move: high lift, at its high, no delta evidence."""
    return EarlyCandidateFeatures(
        identifier=f"EXT{index}USD",
        base_asset=f"EXT{index}",
        lift_from_24h_low_pct=4.0 + index * 0.05,
        distance_from_24h_high_pct=0.2,
        notional_usd=5_000_000.0,
    )


def _igniting_candidate(index: int) -> EarlyCandidateFeatures:
    """An igniting asset: modest level, real acceleration and volume delta."""
    return EarlyCandidateFeatures(
        identifier=f"IGN{index}USD",
        base_asset=f"IGN{index}",
        lift_from_24h_low_pct=2.4,
        distance_from_24h_high_pct=1.5,
        notional_usd=400_000.0,
        momentum_acceleration=0.8,
        relative_volume_change=0.45,
        trade_count_acceleration=1.6,
    )


def test_reserved_ignition_cohort_survives_a_field_of_extended_movers():
    rows = [_extended_mover(index) for index in range(60)]
    rows.extend(_igniting_candidate(index) for index in range(5))

    selection = select_early_candidates(rows, total_candidates=40)

    ignition_members = selection.cohort_members(COHORT_IGNITION)
    assert len(ignition_members) == 5
    assert all(member.startswith("IGN") for member in ignition_members)
    # Extended lift may still take unused reserved capacity by backfill, but
    # it cannot claim more than its own quota of *reserved* slots.
    reserved_momentum = sum(
        1
        for item in selection.selected
        if item.cohort == COHORT_CURRENT_MOMENTUM and item.slot_kind == "RESERVED"
    )
    assert reserved_momentum <= DEFAULT_COHORT_QUOTAS[COHORT_CURRENT_MOMENTUM]


def test_selector_never_exceeds_the_production_candidate_budget():
    rows = [_extended_mover(index) for index in range(200)]
    rows.extend(_igniting_candidate(index) for index in range(50))

    selection = select_early_candidates(rows, total_candidates=40)

    assert len(selection.selected) == 40
    assert selection.total_candidates == 40
    assert selection.shadow_only is True
    assert selection.production_selection_changed is False


def test_unused_reserved_quota_is_backfilled_not_wasted():
    # No delta features anywhere, so only CURRENT_MOMENTUM can admit.
    rows = [_extended_mover(index) for index in range(40)]

    selection = select_early_candidates(rows, total_candidates=40)

    assert len(selection.selected) == 40
    assert any(item.slot_kind == "BACKFILL" for item in selection.selected)


def test_selector_will_not_admit_a_candidate_on_level_alone_into_early_cohorts():
    rows = [_extended_mover(index) for index in range(10)]

    selection = select_early_candidates(rows, total_candidates=40)

    assert selection.cohort_members(COHORT_IGNITION) == ()


# ---------------------------------------------------------------------------
# Phase 0A instrumentation
# ---------------------------------------------------------------------------

def test_rank_limit_metadata_records_rank_cutoff_and_margin():
    contexts = rank_contexts_for_truncation(
        ranked_scores=[("AUSD", 90.0), ("BUSD", 80.0), ("CUSD", 70.0), ("DUSD", 60.0)],
        selected_count=2,
        universe_count=500,
    )

    metadata = build_rank_limit_metadata(
        rank_context=contexts["CUSD"],
        features=Stage0DecisionFeatures(
            lift_from_24h_low_pct=3.1,
            distance_from_24h_high_pct=2.2,
            notional_usd=120_000.0,
            decision_at=DECISION_AT,
        ),
    )

    rank = metadata["coarse_rank"]
    assert rank["rank_position"] == 3
    assert rank["selected_count"] == 2
    assert rank["universe_count"] == 500
    assert rank["candidate_score"] == pytest.approx(70.0)
    assert rank["cutoff_score"] == pytest.approx(80.0)
    assert rank["margin_to_cutoff"] == pytest.approx(-10.0)
    assert metadata["measurement_only"] is True
    assert metadata["production_selection_changed"] is False


def test_below_threshold_metadata_records_achieved_score_and_reason():
    metadata = build_below_threshold_metadata(
        universe_count=500,
        score=ScoreComponents(
            achieved_score=38.0,
            required_score=45.0,
            components={"momentum_1h": 20.0},
            failed_predicates=("minimum_lift_from_24h_low_pct",),
            blocking_reason="deep_discovery_score_below_minimum",
        ),
        features=Stage0DecisionFeatures(lift_from_24h_low_pct=1.1),
    )

    evidence = metadata["score_evidence"]
    assert evidence["achieved_score"] == pytest.approx(38.0)
    assert evidence["required_score"] == pytest.approx(45.0)
    assert evidence["score_margin"] == pytest.approx(-7.0)
    assert evidence["blocking_reason"] == "deep_discovery_score_below_minimum"
    assert evidence["failed_predicates"] == ["minimum_lift_from_24h_low_pct"]


def test_unmeasured_features_stay_explicitly_unavailable():
    payload = Stage0DecisionFeatures(lift_from_24h_low_pct=3.0).as_dict()

    assert payload["relative_volume_change"] is None
    assert payload["trade_count_acceleration"] is None
    # A reader must be able to tell "not measured" from "measured as zero".
    assert "relative_volume_change" in payload["unavailable_features"]
    assert "lift_from_24h_low_pct" not in payload["unavailable_features"]


def test_stage0_evidence_cannot_carry_forward_looking_features():
    # Every emitted payload is screened, so a forward field cannot be smuggled
    # into decision-time evidence.
    assert Stage0DecisionFeatures().as_dict()["decision_at"] is None
    with pytest.raises(LookaheadError):
        assert_point_in_time_safe({"peak_price": 1.0})


def test_rank_context_margin_is_none_when_a_score_is_unmeasurable():
    context = CoarseRankContext(
        selector="PRODUCTION_COARSE_V2_1",
        rank_position=41,
        selected_count=40,
        universe_count=500,
        ranked_count=80,
        candidate_score=None,
        cutoff_score=50.0,
    )

    assert context.margin_to_cutoff is None


# ---------------------------------------------------------------------------
# Phase 0B validation parity: every mandatory gate blocks independently
# ---------------------------------------------------------------------------

def test_all_mandatory_checks_passing_yields_qualified():
    report = evaluate_early_watch_validations(**_fully_valid_validation_kwargs())

    assert report.evidence_grade is EvidenceGrade.QUALIFIED
    assert report.blocking_failures == ()


@pytest.mark.parametrize(
    "override, expected_check",
    [
        ({"market_data_validation": None}, "market_data_integrity"),
        (
            {
                "market_data_validation": SimpleNamespace(
                    qualified=False, status="REJECT", rejection_reasons=["stale"], candle_count=720
                )
            },
            "market_data_integrity",
        ),
        ({"finite_features": False}, "finite_decision_features"),
        ({"symbol_identity_resolved": False}, "canonical_symbol_identity"),
        ({"completed_candle_count": 10}, "history_sufficiency"),
        ({"liquidity_24h_usd": 1_000.0}, CHECK_MIN_LIQUIDITY),
        ({"ticker_bid": None, "ticker_ask": None}, CHECK_SPREAD),
        ({"ticker_bid": 90.0, "ticker_ask": 100.0}, CHECK_SPREAD),
        ({"ticker_last": None}, CHECK_BAD_PRINT),
        ({"ticker_last": 140.0}, CHECK_BAD_PRINT),
        ({"persistence_scans": 1}, CHECK_PERSISTENCE),
        ({"duplicate_state_detected": True}, "duplicate_state_consistency"),
    ],
)
def test_each_mandatory_gate_independently_blocks_qualification(override, expected_check):
    report = evaluate_early_watch_validations(**_fully_valid_validation_kwargs(**override))

    assert report.evidence_grade is not EvidenceGrade.QUALIFIED
    assert expected_check in report.blocking_failures


def test_absent_ticker_last_cannot_silently_disable_the_bad_print_check():
    """The exact production defect: no ticker context meant no comparison."""
    report = evaluate_early_watch_validations(
        **_fully_valid_validation_kwargs(ticker_last=None, latest_ohlc_close=None)
    )

    check = report.check(CHECK_BAD_PRINT)
    assert check is not None
    assert check.result is ValidationResult.NOT_EVALUATED
    assert check.blocks_qualification is True
    assert not report.qualified


def test_unavailable_mandatory_data_is_never_treated_as_pass():
    report = evaluate_early_watch_validations()

    for name in MANDATORY_CHECKS:
        assert report.result_for(name) is not ValidationResult.PASS
    assert report.evidence_grade is EvidenceGrade.OBSERVED


def test_absent_measurement_is_insufficient_rather_than_rejected():
    # REJECTED requires an actual FAIL: silence is not adverse evidence.
    report = evaluate_early_watch_validations()

    assert report.evidence_grade is EvidenceGrade.OBSERVED

    rejected = evaluate_early_watch_validations(
        **_fully_valid_validation_kwargs(finite_features=False)
    )
    assert rejected.evidence_grade is EvidenceGrade.REJECTED


def test_soft_unavailable_evidence_does_not_terminally_reject():
    report = evaluate_early_watch_validations(
        **_fully_valid_validation_kwargs(
            native_flow_available=False,
            cross_venue_available=False,
            depth_slippage_available=False,
        )
    )

    assert report.evidence_grade is EvidenceGrade.QUALIFIED
    assert "native_flow_evidence" in report.soft_unavailable
    assert "native_flow_evidence" not in report.blocking_failures


def test_advisory_evidence_alone_cannot_promote():
    report = evaluate_early_watch_validations(
        social_available=True,
        whale_available=True,
        news_available=True,
    )

    assert report.evidence_grade is not EvidenceGrade.QUALIFIED
    advisory_passes = [
        check
        for check in report.checks
        if check.classification is ValidationClass.ADVISORY
        and check.result is ValidationResult.PASS
    ]
    assert advisory_passes
    assert advisory_only_promotion_attempt(advisory_passes)


def test_actionability_gates_never_affect_qualification():
    report = evaluate_early_watch_validations(
        **_fully_valid_validation_kwargs(
            extension_blocked=True,
            entry_geometry_available=True,
            entry_geometry_acceptable=False,
        )
    )

    # QUALIFIED describes what was validated; actionability is separate.
    assert report.evidence_grade is EvidenceGrade.QUALIFIED
    assert report.actionability_blocked is True
    assert "move is already extended" in report.actionability_reasons


def test_zero_bid_ask_is_unavailable_not_a_zero_width_spread():
    report = evaluate_early_watch_validations(
        **_fully_valid_validation_kwargs(ticker_bid=0.0, ticker_ask=0.0)
    )

    check = report.check(CHECK_SPREAD)
    assert check is not None
    assert check.result is ValidationResult.UNAVAILABLE


def test_first_sighting_persistence_is_unavailable_not_a_failure():
    report = evaluate_early_watch_validations(
        **_fully_valid_validation_kwargs(prior_observation_count=0, persistence_scans=None)
    )

    check = report.check(CHECK_PERSISTENCE)
    assert check is not None
    assert check.result is ValidationResult.UNAVAILABLE
    assert not report.qualified


# ---------------------------------------------------------------------------
# Validation parity wiring into the Early Watch path
# ---------------------------------------------------------------------------

def _ray_mover() -> discovery.CoarseMover:
    return discovery.CoarseMover(
        base_asset="RAY",
        primary_pair="RAYUSD",
        kraken_public_symbol="RAY/USD",
        last_price=2.6,
        volume_24h=5_000_000.0,
        notional_24h_usd_approx=4_000_000.0,
        high_24h=2.61,
        low_24h=1.85,
        lift_from_24h_low_pct=40.5,
        distance_from_24h_high_pct=RAY_DISTANCE_TO_HIGH_PCT,
        coarse_score=180.0,
        universe_count=500,
        ticker_bid=2.599,
        ticker_ask=2.601,
    )


def _ray_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        symbol="RAYUSD",
        confirmed_price_change_1h_pct=RAY_MOMENTUM_1H_PCT,
        momentum_6h_pct=RAY_MOMENTUM_6H_PCT,
        momentum_24h_pct=41.0,
        movement_volume_ratio=RAY_RELATIVE_VOLUME,
        volume_ratio=RAY_RELATIVE_VOLUME,
        distance_to_24h_high_pct=RAY_DISTANCE_TO_HIGH_PCT,
        trend="bullish",
        last_price=2.6,
        movement_timeframe="1H",
        bollinger_bandwidth_percentile=92.0,
        atr_percentile=95.0,
        market_data_validation=None,
    )


def test_ray_signal_reports_late_extension_and_is_not_alert_eligible_under_parity():
    signal = discovery.evaluate_early_mover(
        _ray_snapshot(), _ray_mover(), validation_parity_enabled=True
    )

    assert signal is not None
    assert signal.market_phase == MarketPhase.LATE_EXTENSION.value
    assert signal.extended_move is True
    # Fail-closed: market_data_validation was never run for this snapshot.
    assert signal.evidence_grade != EvidenceGrade.QUALIFIED.value
    assert signal.alert_eligible is False
    assert signal.qualification_blocking_failures


def test_parity_flag_dark_preserves_historical_alert_eligibility():
    snapshot = _ray_snapshot()
    mover = _ray_mover()

    dark = discovery.evaluate_early_mover(snapshot, mover, validation_parity_enabled=False)
    lit = discovery.evaluate_early_mover(snapshot, mover, validation_parity_enabled=True)

    assert dark is not None and lit is not None
    # Enabling parity can only remove candidates, never add them.
    assert dark.stage == lit.stage
    assert dark.entry_recommendation == lit.entry_recommendation
    assert dark.discovery_score == lit.discovery_score
    assert lit.alert_eligible <= dark.alert_eligible


def test_universe_context_passes_real_quote_without_inventing_one():
    mover = _ray_mover()

    context = discovery.universe_context_for(mover)

    assert context.primary_ticker_last == pytest.approx(2.6)
    assert context.primary_ticker_bid == pytest.approx(2.599)
    assert context.primary_ticker_ask == pytest.approx(2.601)

    quoteless = discovery.universe_context_for(
        discovery.CoarseMover(
            base_asset="ZZZ",
            primary_pair="ZZZUSD",
            kraken_public_symbol="ZZZ/USD",
            last_price=1.0,
            volume_24h=1.0,
            notional_24h_usd_approx=100_000.0,
            high_24h=1.1,
            low_24h=0.9,
            lift_from_24h_low_pct=5.0,
            distance_from_24h_high_pct=1.0,
            coarse_score=20.0,
            universe_count=10,
        )
    )
    # Absent bid/ask stays absent rather than becoming a zero-width spread.
    assert quoteless.primary_ticker_bid == 0.0
    assert quoteless.primary_ticker_ask == 0.0


# ---------------------------------------------------------------------------
# Point-in-time safety
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "feature",
    [
        "peak_price",
        "forward_max_move_pct",
        "mfe_pct",
        "mae_pct",
        "final_outcome",
        "realized_return_pct",
        "winner",
    ],
)
def test_runtime_feature_builders_reject_forward_information(feature):
    with pytest.raises(LookaheadError):
        assert_point_in_time_safe({feature: 1.0})


def test_point_in_time_features_are_accepted():
    assert_point_in_time_safe(
        {
            "lift_from_24h_low_pct": 3.0,
            "relative_volume_change": 0.4,
            "prior_observation_count": 2,
        }
    )


def test_point_in_time_window_excludes_later_observations():
    window = PointInTimeWindow(DECISION_AT)
    rows = [
        {"observed_at": (DECISION_AT - timedelta(minutes=10)).isoformat(), "id": "past"},
        {"observed_at": DECISION_AT.isoformat(), "id": "now"},
        {"observed_at": (DECISION_AT + timedelta(minutes=10)).isoformat(), "id": "future"},
    ]

    admitted = window.filter(rows)

    assert [row["id"] for row in admitted] == ["past", "now"]


def test_counterfactual_replay_excludes_future_rows_and_flags_no_lookahead():
    rows = [
        {
            "observed_at": (DECISION_AT - timedelta(minutes=10)).isoformat(),
            "identifier": "IGN1USD",
            "momentum_acceleration": 0.9,
        },
        {
            "observed_at": (DECISION_AT + timedelta(hours=2)).isoformat(),
            "identifier": "LATERUSD",
            "momentum_acceleration": 5.0,
        },
    ]

    result = replay_counterfactual(
        history_rows=rows,
        decision_at=DECISION_AT,
        selector=lambda admitted: [str(row["identifier"]) for row in admitted],
        production_selection=["OTHERUSD"],
    )

    assert result["history_rows_admitted"] == 1
    assert result["history_rows_excluded_as_future"] == 1
    assert result["challenger_selection"] == ["IGN1USD"]
    assert result["lookahead_detected"] is False
    assert result["production_selection_changed"] is False


def test_counterfactual_replay_fails_closed_on_forward_features():
    rows = [
        {
            "observed_at": (DECISION_AT - timedelta(minutes=5)).isoformat(),
            "identifier": "XUSD",
            "forward_max_move_pct": 40.0,
        }
    ]

    with pytest.raises(LookaheadError):
        replay_counterfactual(
            history_rows=rows,
            decision_at=DECISION_AT,
            selector=lambda admitted: [],
            production_selection=[],
        )


# ---------------------------------------------------------------------------
# Forensic replay
# ---------------------------------------------------------------------------

def test_forensic_replay_reconstructs_rank_limited_rejections():
    screening_rows = [
        {
            "observed_at": (DECISION_AT - timedelta(minutes=1)).isoformat(),
            "venue_instrument_id": "KRAKEN:RAYUSD",
            "outcome": "COARSE_RANK_LIMIT",
            "scan_id": "scan-1",
            "long_score": 70.0,
            "reason": "coarse candidate fell outside the deep-analysis cap",
            "metadata": build_rank_limit_metadata(
                rank_context=CoarseRankContext(
                    selector="PRODUCTION_COARSE_V2_1",
                    rank_position=41,
                    selected_count=40,
                    universe_count=500,
                    ranked_count=90,
                    candidate_score=70.0,
                    cutoff_score=80.0,
                ),
            ),
        }
    ]

    result = replay_forensic(screening_rows, decision_at=DECISION_AT)

    assert result["rows"] == 1
    assert result["unreconstructable_rows"] == 0
    assert result["outcome_counts"]["COARSE_RANK_LIMIT"] == 1
    assert result["rank_margin_distribution"]["median"] == pytest.approx(-10.0)


def test_forensic_replay_matches_production_outcomes_exactly():
    verdict = verify_forensic_replay_matches_production(
        replayed={"KRAKEN:AUSD": "ADVANCED", "KRAKEN:BUSD": "COARSE_RANK_LIMIT"},
        production={"KRAKEN:AUSD": "ADVANCED", "KRAKEN:BUSD": "COARSE_RANK_LIMIT"},
    )

    assert verdict["exact_match"] is True
    assert verdict["mismatches"] == {}


def test_forensic_replay_reports_mismatch_rather_than_asserting_success():
    verdict = verify_forensic_replay_matches_production(
        replayed={"KRAKEN:AUSD": "ADVANCED"},
        production={"KRAKEN:AUSD": "BELOW_THRESHOLD"},
    )

    assert verdict["exact_match"] is False
    assert "KRAKEN:AUSD" in verdict["mismatches"]


# ---------------------------------------------------------------------------
# Cohorts: no survivorship bias, unresolved is not success
# ---------------------------------------------------------------------------

def test_positive_and_negative_cohorts_are_defined_independently_of_alerts():
    positive = label_cohort_member(
        symbol="WINUSD",
        liquidity_24h_usd=1_000_000.0,
        forward_max_move_pct=35.0,
    )
    negative = label_cohort_member(
        symbol="FAILUSD",
        liquidity_24h_usd=1_000_000.0,
        forward_max_move_pct=3.0,
        had_early_precursor=True,
    )

    assert positive.label is CohortLabel.POSITIVE
    assert negative.label is CohortLabel.NEGATIVE
    # Neither label depends on whether O'Pip ever alerted.
    assert positive.alerted is False
    assert negative.alerted is False


def test_illiquid_or_unmeasured_assets_are_unresolved_not_counted_as_wins():
    illiquid = label_cohort_member(
        symbol="THINUSD", liquidity_24h_usd=1_000.0, forward_max_move_pct=90.0
    )
    unmeasured = label_cohort_member(
        symbol="UNKUSD", liquidity_24h_usd=1_000_000.0, forward_max_move_pct=None
    )

    assert illiquid.label is CohortLabel.UNRESOLVED
    assert unmeasured.label is CohortLabel.UNRESOLVED


# ---------------------------------------------------------------------------
# Timeframe experiment
# ---------------------------------------------------------------------------

def test_production_timeframe_uses_fine_candles_only_under_compression():
    compressed = current_timeframe_decision(bandwidth_percentile=20.0, atr_percentile=90.0)
    expanded = current_timeframe_decision(bandwidth_percentile=85.0, atr_percentile=90.0)

    assert compressed.timeframe == TIMEFRAME_FINE
    assert expanded.timeframe == TIMEFRAME_HOURLY


def test_ignition_inverts_the_production_fine_timeframe_decision():
    """Root cause D: ignition raises bandwidth/ATR and turns fine candles off."""
    comparison = compare_timeframe_policies(
        symbol="IGNUSD",
        bandwidth_percentile=85.0,
        atr_percentile=90.0,
        phase=MarketPhase.IGNITION,
    )

    assert comparison.production.timeframe == TIMEFRAME_HOURLY
    assert comparison.candidate.timeframe == TIMEFRAME_FINE
    assert comparison.inversion_detected is True
    assert comparison.information_age_reduction_seconds > 0


def test_candidate_timeframe_agrees_with_production_when_no_expansion():
    comparison = compare_timeframe_policies(
        symbol="QUIETUSD",
        bandwidth_percentile=85.0,
        atr_percentile=90.0,
        phase=MarketPhase.DORMANT,
    )

    assert comparison.production.timeframe == comparison.candidate.timeframe
    assert comparison.inversion_detected is False


def test_timeframe_experiment_never_changes_production_behaviour():
    comparison = compare_timeframe_policies(
        symbol="IGNUSD",
        bandwidth_percentile=85.0,
        atr_percentile=90.0,
        phase=MarketPhase.IGNITION,
    )
    payload = comparison.as_dict()

    assert payload["shadow_only"] is True
    assert payload["production_decision_changed"] is False
    assert payload["trade_authority_changed"] is False
    # The candidate policy is a pure function; it holds no production hook.
    assert candidate_timeframe_decision(
        bandwidth_percentile=85.0, atr_percentile=90.0, phase=MarketPhase.IGNITION
    ).fine_timeframe_selected is True


# ---------------------------------------------------------------------------
# Alert governor coupling and card-versus-delivery timing
# ---------------------------------------------------------------------------

def test_priority_transition_stages_are_unchanged_by_the_new_taxonomy():
    from app.services import alert_governor

    # READY survives as an internal transition token precisely so governor
    # priority behaviour is untouched by the operator-facing rename.
    assert "READY" in alert_governor.PRIORITY_TRANSITION_STAGES


def test_transition_key_still_derives_from_stage_not_from_disposition():
    base = dict(
        stage="READY",
        entry_recommendation="BREAKOUT_ENTRY_POSSIBLE",
        momentum_state="ACCELERATING",
        extended_move=False,
    )
    without_taxonomy = SimpleNamespace(**base)
    with_taxonomy = SimpleNamespace(
        **base,
        market_phase=MarketPhase.LATE_EXTENSION.value,
        evidence_grade=EvidenceGrade.QUALIFIED.value,
        operator_disposition=OperatorDisposition.DO_NOT_CHASE.value,
    )

    assert scan_movers._transition_key(without_taxonomy) == scan_movers._transition_key(
        with_taxonomy
    )
    assert scan_movers._transition_key(with_taxonomy) == (
        "READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED"
    )


def test_card_creation_is_not_operator_notification():
    ledger = record_card_created(new_ledger(symbol="XUSD", episode_id="e1"), created_at=DECISION_AT)

    assert ledger.card_created_at is not None
    assert ledger.operator_was_notified is False
    assert ledger.milestone(MILESTONE_FIRST_DELIVERED) is None


def test_silent_card_edit_counts_churn_without_claiming_delivery():
    ledger = record_card_created(new_ledger(symbol="XUSD", episode_id="e1"), created_at=DECISION_AT)
    ledger = record_card_edited(ledger, edited_at=DECISION_AT + timedelta(minutes=5))
    ledger = record_card_edited(ledger, edited_at=DECISION_AT + timedelta(minutes=10))

    assert ledger.card_edit_count == 2
    assert ledger.operator_was_notified is False

    summary = summarize_delivery_timing([ledger])
    assert summary["episodes_with_card_but_no_delivery"] == 1
    assert summary["median_observation_to_delivery_seconds"] is None


def test_delivered_notification_is_the_only_basis_for_a_lead_time_claim():
    ledger = new_ledger(symbol="XUSD", episode_id="e1")
    ledger = observe_phase(
        ledger,
        phase=MarketPhase.IGNITION,
        grade=EvidenceGrade.OBSERVED,
        observed_at=DECISION_AT,
        reference_price=1.0,
    )
    ledger = record_card_created(ledger, created_at=DECISION_AT + timedelta(minutes=10))
    ledger = record_notification_delivered(
        ledger, delivered_at=DECISION_AT + timedelta(minutes=30), anchor_price=1.2
    )

    assert ledger.operator_was_notified is True
    assert ledger.lead_time_seconds(
        start=MILESTONE_FIRST_OBSERVED, end=MILESTONE_FIRST_DELIVERED
    ) == pytest.approx(1800.0)
    assert ledger.move_consumed_pct(
        start=MILESTONE_FIRST_OBSERVED, end=MILESTONE_FIRST_DELIVERED
    ) == pytest.approx(20.0)


def test_milestones_are_monotonic_and_cannot_be_rewritten():
    ledger = new_ledger(symbol="XUSD", episode_id="e1")
    ledger = observe_phase(
        ledger, phase=MarketPhase.IGNITION, observed_at=DECISION_AT, reference_price=1.0
    )
    first = ledger.milestone(MILESTONE_FIRST_OBSERVED)

    ledger = observe_phase(
        ledger,
        phase=MarketPhase.IGNITION,
        observed_at=DECISION_AT - timedelta(hours=1),
        reference_price=0.5,
    )

    assert ledger.milestone(MILESTONE_FIRST_OBSERVED) == first


def test_operator_alert_milestone_is_separate_from_qualification():
    ledger = new_ledger(symbol="XUSD", episode_id="e1")
    ledger = observe_phase(
        ledger,
        phase=MarketPhase.EARLY_EXPANSION,
        grade=EvidenceGrade.QUALIFIED,
        observed_at=DECISION_AT,
        reference_price=1.0,
    )

    assert ledger.milestone("first_qualified_at") is not None
    assert ledger.milestone(MILESTONE_FIRST_OPERATOR_ALERT) is None


# ---------------------------------------------------------------------------
# Promotion gates
# ---------------------------------------------------------------------------

def test_promotion_is_insufficient_evidence_with_no_data():
    evaluation = evaluate_promotion(baseline={}, candidate={})

    assert evaluation.status is PromotionStatus.INSUFFICIENT_EVIDENCE
    assert not evaluation.eligible
    assert len(evaluation.gates) == len(GATE_ORDER)
    assert evaluation.unproven_gates


def test_promotion_gates_cover_every_criterion_in_the_issue():
    evaluation = evaluate_promotion(baseline={}, candidate={})

    assert tuple(gate.name for gate in evaluation.gates) == GATE_ORDER


def test_promotion_reports_blocked_when_alert_volume_would_increase():
    evaluation = evaluate_promotion(
        baseline={
            "qualified_precision_pct": 60.0,
            "operator_alert_volume": 40,
            "median_move_consumed_before_alert_pct": 18.0,
            "first_observation_early_phase_share_pct": 30.0,
            "total_card_edits": 40,
            "delivered_notification_volume": 40,
            "median_observation_to_delivery_seconds": 1_200.0,
        },
        candidate={
            "qualified_precision_pct": 62.0,
            "operator_alert_volume": 90,
            "median_move_consumed_before_alert_pct": 10.0,
            "first_observation_early_phase_share_pct": 60.0,
            "total_card_edits": 90,
            "delivered_notification_volume": 90,
            "median_observation_to_delivery_seconds": 600.0,
        },
        resolved_cohort_members=400,
        observation_days=30.0,
        unreconstructable_rejections=0,
        lookahead_detected=False,
        authority_changed=False,
    )

    assert evaluation.status is PromotionStatus.BLOCKED
    assert "operator_alert_volume_not_increased" in evaluation.failing_gates


def test_promotion_still_requires_a_human_flag():
    payload = evaluate_promotion(baseline={}, candidate={}).as_dict()

    assert payload["operator_promotion_requires_human_flag"] is True
    assert payload["trade_authority_changed"] is False


def test_authority_change_fails_its_gate_outright():
    evaluation = evaluate_promotion(
        baseline={}, candidate={}, authority_changed=True
    )

    gate = next(
        item
        for item in evaluation.gates
        if item.name == "no_execution_or_risk_authority_change"
    )
    assert gate.verdict is GateVerdict.FAIL


# ---------------------------------------------------------------------------
# Safety: nothing here expands authority, and every flag ships dark
# ---------------------------------------------------------------------------

def test_every_issue_223_flag_defaults_dark():
    state = flags.flag_state({})

    assert state == {
        flags.VALIDATION_PARITY_FLAG: False,
        flags.SELECTOR_PROMOTED_FLAG: False,
        flags.SELECTOR_SHADOW_FLAG: False,
        flags.TIMEFRAME_SHADOW_FLAG: False,
        flags.TIMING_LEDGER_FLAG: False,
    }


def test_selector_promotion_flag_is_independent_of_shadow_flags():
    environ = {
        flags.SELECTOR_SHADOW_FLAG: "true",
        flags.TIMEFRAME_SHADOW_FLAG: "true",
        flags.TIMING_LEDGER_FLAG: "true",
    }

    assert flags.early_selector_promoted(environ) is False
    assert flags.early_selector_shadow_enabled(environ) is True


def test_no_early_module_grants_trading_or_execution_authority():
    import importlib
    import pkgutil

    import app.opip.early as early

    forbidden = (
        "add_order",
        "place_order",
        "cancel_order",
        "create_order",
        "position_size",
        "send_telegram_message",
    )
    checked = 0
    for module_info in pkgutil.iter_modules(early.__path__):
        module = importlib.import_module(f"app.opip.early.{module_info.name}")
        source = module.__doc__ or ""
        assert "trading authority" not in source.lower() or "no " in source.lower()
        for name in forbidden:
            assert not hasattr(module, name), f"{module_info.name} exposes {name}"
        checked += 1

    assert checked >= 10


def test_taxonomy_payloads_assert_no_authority_change():
    assessment = build_operator_assessment(
        symbol="XUSD",
        phase=MarketPhase.IGNITION,
        grade=EvidenceGrade.OBSERVED,
        disposition=OperatorDisposition.MONITOR,
    )

    assert assessment.as_dict()["trade_authority_changed"] is False


def test_shadow_observation_is_inert_while_flags_are_dark():
    from app.opip.early import shadow_observer

    rows = shadow_observer.observe_scan_shadow(
        all_movers=[_ray_mover()],
        production_selection=[_ray_mover()],
        signals=[],
        universe_count=500,
        environ={},
    )

    assert rows == []


# ---------------------------------------------------------------------------
# Shadow observer: delivery separation and delta derivation
# ---------------------------------------------------------------------------

def test_governor_edit_is_not_counted_as_operator_notification(tmp_path):
    from app.opip.early import shadow_observer

    rows = shadow_observer.record_card_delivery_outcomes(
        {"AUSD": ("CREATED", True), "BUSD": ("EDITED", True), "CUSD": ("SUPPRESSED", False)},
        anchor_prices={"AUSD": 1.0, "BUSD": 2.0},
        decision_at=DECISION_AT,
        state_path=tmp_path / "ledger.json",
        environ={flags.TIMING_LEDGER_FLAG: "true"},
    )

    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["AUSD"]["operator_was_notified"] is True
    # An in-place edit may be silent, so it is churn, not notification.
    assert by_symbol["BUSD"]["operator_was_notified"] is False
    assert by_symbol["BUSD"]["card_edit_count"] == 1
    assert "CUSD" not in by_symbol


def test_delivery_recording_is_dark_by_default(tmp_path):
    from app.opip.early import shadow_observer

    rows = shadow_observer.record_card_delivery_outcomes(
        {"AUSD": ("CREATED", True)},
        decision_at=DECISION_AT,
        state_path=tmp_path / "ledger.json",
        environ={},
    )

    assert rows == []


def test_delta_features_are_unavailable_on_a_first_sighting():
    from app.opip.early.shadow_observer import derive_delta_features

    features = derive_delta_features([{"observed_at": DECISION_AT.isoformat()}])

    assert features["relative_volume_change"] is None
    assert features["momentum_acceleration"] is None
    assert features["prior_observation_count"] == 0


def test_delta_features_are_derived_from_existing_observation_history():
    from app.opip.early.shadow_observer import derive_delta_features

    rows = [
        {
            "observed_at": (DECISION_AT - timedelta(minutes=20)).isoformat(),
            "last_price": 1.00,
            "volume_24h": 1_000.0,
            "lift_from_24h_low_pct": 1.0,
            "distance_from_24h_high_pct": 5.0,
        },
        {
            "observed_at": (DECISION_AT - timedelta(minutes=10)).isoformat(),
            "last_price": 1.01,
            "volume_24h": 1_200.0,
            "lift_from_24h_low_pct": 2.0,
            "distance_from_24h_high_pct": 4.0,
        },
        {
            "observed_at": DECISION_AT.isoformat(),
            "last_price": 1.05,
            "volume_24h": 1_800.0,
            "lift_from_24h_low_pct": 6.0,
            "distance_from_24h_high_pct": 1.0,
        },
    ]

    features = derive_delta_features(rows)

    assert features["relative_volume_change"] == pytest.approx(0.5)
    assert features["base_displacement_velocity_pct"] == pytest.approx(4.0)
    assert features["distance_to_high_velocity_pct"] == pytest.approx(3.0)
    # Return went from +1.0% to ~+3.96% per interval: genuine acceleration.
    assert features["momentum_acceleration"] > 0
    assert features["prior_observation_count"] == 2


def test_selector_shadow_row_reports_no_production_change(tmp_path):
    from app.opip.early import shadow_observer

    rows = shadow_observer.observe_scan_shadow(
        all_movers=[_ray_mover()],
        production_selection=[_ray_mover()],
        signals=[],
        universe_count=500,
        decision_at=DECISION_AT,
        history={},
        environ={flags.SELECTOR_SHADOW_FLAG: "true"},
        ledger_state_path=tmp_path / "ledger.json",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["record_type"] == shadow_observer.RECORD_SELECTOR_COMPARISON
    assert row["shadow_only"] is True
    assert row["production_selection_changed"] is False
    assert row["trade_authority_changed"] is False
