"""Phase 3A: signal timing decomposition, forward outcomes, and counterfactual replay.

Phase 2 established *whether* OHM would have detected a move and how it graded
against forward return. This module answers a narrower, harder question about
the same replay: exactly when each stage tier was first reached within an
episode, exactly which gate was still failing at that moment, and how much of
the eventual move was already gone by the time a later stage confirmed.

The DRV case study that motivated this phase observed Stage: BREAKOUT
CANDIDATE, Persistence: 3 - which is NOT, by itself, evidence that persistence
was what kept the candidate out of ACTIONABLE_REVIEW. ACTIONABLE_REVIEW gates
on five independent thresholds (opportunity, explosion, tradeability,
persistence, exhaustion) plus a liquidity floor; persistence merely being at
3 says nothing about whether the other four were satisfied. ``evaluate_stage_gates``
exists to answer that question from replay evidence, per gate, rather than by
assumption.

No alternate scorer is reimplemented here. Every function in this module
either reuses ``app.services.signal_quality_phase2``'s existing replay
pipeline unmodified, or reuses ``app.services.signal_scoring.determine_stage``
directly for cross-validation. Counterfactual sweeps vary only
``SignalQualityConfig``/``Phase2Config`` fields via ``dataclasses.replace`` -
production settings objects are never mutated, and nothing here writes
anything or changes a running scan's behaviour. All results carry the
Phase 2 invariants forward unchanged: no lookahead (post-detection-only
outcome windows, per ``SymbolTimeline``'s strict exclusivity), no automatic
tuning (a sweep reports numbers; it never selects a winner), and
counterfactual results are always returned in a block separate from - never
merged into - the current-production replay.

Known limitation, carried honestly: two ablations from the original Phase 3A
proposal - acceleration-trigger sensitivity and volume-corroboration-cap
relaxation - are NOT reachable through ``run_threshold_ablation``, because
both are hardcoded module-level constants inside ``signal_scoring.py``
(``MOVEMENT_RATE_ANCHORS_PRIOR`` and the corroboration cap), not
``SignalQualityConfig`` fields. Touching that module is out of scope this
phase. See SIGNAL_QUALITY_PHASE3A.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.signal_quality_phase2 import (
    CandidateRow,
    MoveEpisode,
    Phase2Config,
    SymbolTimeline,
    build_all_episodes,
    build_timelines,
    evaluate_episode_detection,
    read_observations,
    reconstruct_scan_frames,
    replay_signal_quality,
)
from app.services.signal_scoring import (
    STAGE_ACTIONABLE_REVIEW,
    STAGE_BREAKOUT_CANDIDATE,
    STAGE_EARLY_BUILDING,
    SignalQualityConfig,
)


STANDARD_HORIZONS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "60m": timedelta(minutes=60),
    "4h": timedelta(hours=4),
    "8h": timedelta(hours=8),
    "24h": timedelta(hours=24),
}

MFE_MAE_HORIZON = timedelta(hours=24)

DIAGNOSABLE_STAGES = (STAGE_BREAKOUT_CANDIDATE, STAGE_ACTIONABLE_REVIEW)


# ---------------------------------------------------------------------------
# Gate-status diagnosis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    actual: float
    threshold: float
    comparison: str  # ">=" or "<"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "actual": self.actual,
            "threshold": self.threshold,
            "comparison": self.comparison,
        }


@dataclass(frozen=True)
class StageGateStatus:
    """Per-gate pass/fail for one candidate row against one target stage.

    Diagnostic only. This is a comparison table built from the same
    thresholds ``determine_stage`` reads off ``SignalQualityConfig`` - it does
    not reimplement the stage cascade, so it carries no risk of drifting from
    what production actually decided.
    """

    target_stage: str
    observation_only: bool
    liquidity_24h_usd_approx: float
    observation_liquidity_usd: float
    gates: tuple[GateCheck, ...]

    @property
    def eligible(self) -> bool:
        return not self.observation_only and all(gate.passed for gate in self.gates)

    @property
    def blocking_gates(self) -> tuple[str, ...]:
        names = tuple(gate.name for gate in self.gates if not gate.passed)
        if self.observation_only:
            names = ("liquidity_below_observation_threshold",) + names
        return names

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_stage": self.target_stage,
            "observation_only": self.observation_only,
            "liquidity_24h_usd_approx": self.liquidity_24h_usd_approx,
            "observation_liquidity_usd": self.observation_liquidity_usd,
            "eligible": self.eligible,
            "blocking_gates": list(self.blocking_gates),
            "gates": [gate.as_dict() for gate in self.gates],
        }


def evaluate_stage_gates(
    row: CandidateRow,
    *,
    config: SignalQualityConfig,
    target_stage: str,
) -> StageGateStatus:
    """Diagnose exactly which gate(s) blocked ``row`` from ``target_stage``.

    Mirrors ``determine_stage``'s per-stage threshold set exactly - same
    fields, same comparisons - so ``test_gate_status_agrees_with_determine_stage``
    can assert the two never disagree on eligibility.
    """
    if target_stage == STAGE_ACTIONABLE_REVIEW:
        thresholds: dict[str, tuple[float, float, str]] = {
            "opportunity": (row.opportunity_score, config.actionable_opportunity, ">="),
            "explosion_potential": (row.explosion_potential_score, config.actionable_explosion, ">="),
            "tradeability": (row.tradeability_score, config.actionable_tradeability, ">="),
            "persistence_scans": (row.persistence_scans, config.actionable_min_persistence_scans, ">="),
            "exhaustion_penalty": (row.exhaustion_penalty, config.actionable_max_exhaustion, "<"),
        }
    elif target_stage == STAGE_BREAKOUT_CANDIDATE:
        thresholds = {
            "opportunity": (row.opportunity_score, config.breakout_opportunity, ">="),
            "explosion_potential": (row.explosion_potential_score, config.breakout_explosion, ">="),
            "tradeability": (row.tradeability_score, config.breakout_tradeability, ">="),
            "persistence_scans": (row.persistence_scans, config.breakout_min_persistence_scans, ">="),
            "exhaustion_penalty": (row.exhaustion_penalty, config.breakout_max_exhaustion, "<"),
        }
    else:
        raise ValueError(
            f"evaluate_stage_gates only diagnoses {DIAGNOSABLE_STAGES}, got {target_stage!r}"
        )

    gates = tuple(
        GateCheck(
            name=name,
            passed=(actual >= threshold) if comparison == ">=" else (actual < threshold),
            actual=float(actual),
            threshold=float(threshold),
            comparison=comparison,
        )
        for name, (actual, threshold, comparison) in thresholds.items()
    )
    return StageGateStatus(
        target_stage=target_stage,
        observation_only=row.liquidity_24h_usd_approx < config.observation_liquidity_usd,
        liquidity_24h_usd_approx=row.liquidity_24h_usd_approx,
        observation_liquidity_usd=config.observation_liquidity_usd,
        gates=gates,
    )


# ---------------------------------------------------------------------------
# Forward outcomes (MFE / MAE / fixed-horizon returns)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForwardOutcome:
    """Everything that happened after one reference point, strictly forward.

    ``horizon_returns_pct`` uses ``price_asof`` (last observation at-or-before
    t + H): "what had the price done by then". MFE/MAE use ``forward_extreme``
    instead: the best/worst price actually touched anywhere in the window,
    which is a different question from the fixed-horizon return and is why
    both are reported rather than one being derived from the other.
    """

    reference_at: datetime
    reference_price: float
    horizon_returns_pct: Mapping[str, float | None]
    mfe_pct: float | None
    mfe_at: datetime | None
    time_to_mfe_seconds: float | None
    mae_pct: float | None
    mae_at: datetime | None
    time_to_mae_seconds: float | None
    window_complete: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_at": self.reference_at.isoformat(),
            "reference_price": self.reference_price,
            "horizon_returns_pct": dict(self.horizon_returns_pct),
            "mfe_pct": self.mfe_pct,
            "mfe_at": self.mfe_at.isoformat() if self.mfe_at else None,
            "time_to_mfe_seconds": self.time_to_mfe_seconds,
            "mae_pct": self.mae_pct,
            "mae_at": self.mae_at.isoformat() if self.mae_at else None,
            "time_to_mae_seconds": self.time_to_mae_seconds,
            "window_complete": self.window_complete,
        }


def compute_forward_outcome(
    timeline: SymbolTimeline,
    *,
    reference_at: datetime,
    reference_price: float,
    horizons: Mapping[str, timedelta] = STANDARD_HORIZONS,
    mfe_mae_horizon: timedelta = MFE_MAE_HORIZON,
) -> ForwardOutcome | None:
    """Forward returns, MFE and MAE from one reference point. None if no valid price."""
    if reference_price is None or reference_price <= 0:
        return None

    horizon_returns: dict[str, float | None] = {}
    for label, delta in horizons.items():
        price_then = timeline.price_asof(reference_at + delta)
        horizon_returns[label] = (
            (price_then / reference_price - 1.0) * 100.0 if price_then is not None else None
        )

    mfe = timeline.forward_extreme([reference_at], mfe_mae_horizon, mode="max")[0]
    mae = timeline.forward_extreme([reference_at], mfe_mae_horizon, mode="min")[0]

    mfe_pct = mfe_at = time_to_mfe = None
    if mfe is not None:
        price, at = mfe
        mfe_pct = (price / reference_price - 1.0) * 100.0
        mfe_at = at
        time_to_mfe = (at - reference_at).total_seconds()

    mae_pct = mae_at = time_to_mae = None
    if mae is not None:
        price, at = mae
        mae_pct = (price / reference_price - 1.0) * 100.0
        mae_at = at
        time_to_mae = (at - reference_at).total_seconds()

    return ForwardOutcome(
        reference_at=reference_at,
        reference_price=reference_price,
        horizon_returns_pct=horizon_returns,
        mfe_pct=mfe_pct,
        mfe_at=mfe_at,
        time_to_mfe_seconds=time_to_mfe,
        mae_pct=mae_pct,
        mae_at=mae_at,
        time_to_mae_seconds=time_to_mae,
        window_complete=timeline.has_complete_window(reference_at, mfe_mae_horizon),
    )


# ---------------------------------------------------------------------------
# Per-episode stage timing decomposition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageTimingRecord:
    """One episode's timing story: when each stage tier was first reached,
    what blocked the next tier at that moment, and what happened after.
    """

    symbol: str
    episode_baseline_at: datetime
    episode_baseline_price: float
    episode_peak_at: datetime
    episode_peak_price: float
    episode_peak_return_pct: float

    first_candidate_at: datetime | None
    first_candidate_stage: str | None
    first_candidate_price: float | None
    first_candidate_move_completed_fraction_pct: float | None
    first_candidate_distance_from_24h_high_pct: float | None
    first_candidate_gate_status_breakout: StageGateStatus | None
    first_candidate_gate_status_actionable: StageGateStatus | None
    outcome_from_first_candidate: ForwardOutcome | None

    first_early_building_at: datetime | None
    first_breakout_candidate_at: datetime | None
    first_actionable_review_at: datetime | None
    outcome_from_first_breakout_candidate: ForwardOutcome | None
    outcome_from_first_actionable_review: ForwardOutcome | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "episode_baseline_at": self.episode_baseline_at.isoformat(),
            "episode_baseline_price": self.episode_baseline_price,
            "episode_peak_at": self.episode_peak_at.isoformat(),
            "episode_peak_price": self.episode_peak_price,
            "episode_peak_return_pct": round(self.episode_peak_return_pct, 4),
            "first_candidate_at": (
                self.first_candidate_at.isoformat() if self.first_candidate_at else None
            ),
            "first_candidate_stage": self.first_candidate_stage,
            "first_candidate_price": self.first_candidate_price,
            "first_candidate_move_completed_fraction_pct": (
                self.first_candidate_move_completed_fraction_pct
            ),
            "first_candidate_distance_from_24h_high_pct": (
                self.first_candidate_distance_from_24h_high_pct
            ),
            "first_candidate_gate_status_breakout": (
                self.first_candidate_gate_status_breakout.as_dict()
                if self.first_candidate_gate_status_breakout
                else None
            ),
            "first_candidate_gate_status_actionable": (
                self.first_candidate_gate_status_actionable.as_dict()
                if self.first_candidate_gate_status_actionable
                else None
            ),
            "outcome_from_first_candidate": (
                self.outcome_from_first_candidate.as_dict()
                if self.outcome_from_first_candidate
                else None
            ),
            "first_early_building_at": (
                self.first_early_building_at.isoformat() if self.first_early_building_at else None
            ),
            "first_breakout_candidate_at": (
                self.first_breakout_candidate_at.isoformat()
                if self.first_breakout_candidate_at
                else None
            ),
            "first_actionable_review_at": (
                self.first_actionable_review_at.isoformat()
                if self.first_actionable_review_at
                else None
            ),
            "outcome_from_first_breakout_candidate": (
                self.outcome_from_first_breakout_candidate.as_dict()
                if self.outcome_from_first_breakout_candidate
                else None
            ),
            "outcome_from_first_actionable_review": (
                self.outcome_from_first_actionable_review.as_dict()
                if self.outcome_from_first_actionable_review
                else None
            ),
        }


def build_stage_timing_records(
    episodes: Sequence[MoveEpisode],
    detections: Sequence[CandidateRow],
    timelines: Mapping[str, SymbolTimeline],
    *,
    scoring_config: SignalQualityConfig,
) -> list[StageTimingRecord]:
    """One record per episode, decomposing when each stage tier was first reached.

    "First candidate" reuses ``evaluate_episode_detection``'s own definition
    (the first detection in ``[baseline_at, peak_at)``) rather than a new one,
    so ``move_completed_fraction_pct`` stays identical to Phase 2's existing,
    already-validated figure for the same concept.
    """
    by_symbol: dict[str, list[CandidateRow]] = defaultdict(list)
    for row in detections:
        by_symbol[row.symbol].append(row)
    for rows in by_symbol.values():
        rows.sort(key=lambda row: row.scan_at)

    records: list[StageTimingRecord] = []
    for episode in episodes:
        symbol_detections = by_symbol.get(episode.symbol, ())
        detection_result = evaluate_episode_detection(episode, symbol_detections)
        first = detection_result.first_detection
        timeline = timelines.get(episode.symbol)
        has_timeline = timeline is not None and len(timeline) > 0

        in_episode = [
            row for row in symbol_detections
            if episode.baseline_at <= row.scan_at < episode.peak_at
        ]

        def _first_row_at_stage(stage: str) -> CandidateRow | None:
            matches = [row for row in in_episode if row.stage == stage]
            return min(matches, key=lambda row: row.scan_at) if matches else None

        gate_breakout = gate_actionable = None
        outcome_first = None
        if first is not None:
            gate_breakout = evaluate_stage_gates(
                first, config=scoring_config, target_stage=STAGE_BREAKOUT_CANDIDATE
            )
            gate_actionable = evaluate_stage_gates(
                first, config=scoring_config, target_stage=STAGE_ACTIONABLE_REVIEW
            )
            if has_timeline:
                outcome_first = compute_forward_outcome(
                    timeline, reference_at=first.scan_at, reference_price=first.price
                )

        first_early_row = _first_row_at_stage(STAGE_EARLY_BUILDING)
        first_breakout_row = _first_row_at_stage(STAGE_BREAKOUT_CANDIDATE)
        first_actionable_row = _first_row_at_stage(STAGE_ACTIONABLE_REVIEW)

        outcome_breakout = outcome_actionable = None
        if has_timeline and first_breakout_row is not None:
            outcome_breakout = compute_forward_outcome(
                timeline,
                reference_at=first_breakout_row.scan_at,
                reference_price=first_breakout_row.price,
            )
        if has_timeline and first_actionable_row is not None:
            outcome_actionable = compute_forward_outcome(
                timeline,
                reference_at=first_actionable_row.scan_at,
                reference_price=first_actionable_row.price,
            )

        records.append(
            StageTimingRecord(
                symbol=episode.symbol,
                episode_baseline_at=episode.baseline_at,
                episode_baseline_price=episode.baseline_price,
                episode_peak_at=episode.peak_at,
                episode_peak_price=episode.peak_price,
                episode_peak_return_pct=episode.peak_return_pct,
                first_candidate_at=first.scan_at if first else None,
                first_candidate_stage=first.stage if first else None,
                first_candidate_price=first.price if first else None,
                first_candidate_move_completed_fraction_pct=(
                    detection_result.move_completed_fraction_pct
                ),
                first_candidate_distance_from_24h_high_pct=(
                    first.distance_from_24h_high_pct if first else None
                ),
                first_candidate_gate_status_breakout=gate_breakout,
                first_candidate_gate_status_actionable=gate_actionable,
                outcome_from_first_candidate=outcome_first,
                first_early_building_at=(first_early_row.scan_at if first_early_row else None),
                first_breakout_candidate_at=(
                    first_breakout_row.scan_at if first_breakout_row else None
                ),
                first_actionable_review_at=(
                    first_actionable_row.scan_at if first_actionable_row else None
                ),
                outcome_from_first_breakout_candidate=outcome_breakout,
                outcome_from_first_actionable_review=outcome_actionable,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Opportunity decay across confirmation scans
# ---------------------------------------------------------------------------


def opportunity_decay_by_persistence(records: Sequence[StageTimingRecord]) -> dict[str, Any]:
    """How much price appreciation happened between first-candidate and each
    later confirmation tier, for episodes that reached that tier.

    Purely descriptive: this reports what confirmation cost in already-elapsed
    move, it does not recommend loosening or tightening any gate.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.first_candidate_price is None or record.first_candidate_price <= 0:
            continue
        entry_price = record.first_candidate_price
        row: dict[str, Any] = {
            "symbol": record.symbol,
            "episode_baseline_at": record.episode_baseline_at.isoformat(),
            "first_candidate_at": record.first_candidate_at.isoformat(),
            "first_candidate_stage": record.first_candidate_stage,
            "first_candidate_move_completed_fraction_pct": (
                record.first_candidate_move_completed_fraction_pct
            ),
        }
        for label, later_row_at, later_price in (
            (
                "breakout_candidate",
                record.first_breakout_candidate_at,
                (
                    record.outcome_from_first_breakout_candidate.reference_price
                    if record.outcome_from_first_breakout_candidate
                    else None
                ),
            ),
            (
                "actionable_review",
                record.first_actionable_review_at,
                (
                    record.outcome_from_first_actionable_review.reference_price
                    if record.outcome_from_first_actionable_review
                    else None
                ),
            ),
        ):
            if later_row_at is None or later_price is None:
                row[f"{label}_reached_at"] = None
                row[f"{label}_gained_before_confirmation_pct"] = None
                row[f"{label}_seconds_since_first_candidate"] = None
                continue
            row[f"{label}_reached_at"] = later_row_at.isoformat()
            row[f"{label}_gained_before_confirmation_pct"] = (
                (later_price / entry_price - 1.0) * 100.0
            )
            row[f"{label}_seconds_since_first_candidate"] = (
                later_row_at - record.first_candidate_at
            ).total_seconds()
        rows.append(row)

    return {
        "status": "PROVISIONAL_EVENT_SAMPLED_REPLAY",
        "episodes_with_first_candidate": len(rows),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Counterfactual sweeps (persistence and threshold/weight ablation)
# ---------------------------------------------------------------------------


def _run_config_variant(
    frames,
    episodes: Sequence[MoveEpisode],
    timelines: Mapping[str, SymbolTimeline],
    *,
    config: Phase2Config,
) -> list[StageTimingRecord]:
    detections, _audit_rows = replay_signal_quality(frames, config=config, retain_audit_for=set())
    return build_stage_timing_records(
        episodes, detections, timelines, scoring_config=config.scoring
    )


def run_persistence_counterfactual(
    frames,
    episodes: Sequence[MoveEpisode],
    timelines: Mapping[str, SymbolTimeline],
    *,
    base_config: Phase2Config,
    breakout_persistence_values: Sequence[int] = (1, 2, 3),
    actionable_persistence_values: Sequence[int] = (1, 2, 3),
) -> dict[str, Any]:
    """Re-run the replay with persistence_scans gates varied, holding everything else fixed.

    Only combinations that keep the actionable gate at least as strict as the
    breakout gate are run - that ordering is a structural property of
    ``determine_stage``'s cascade (actionable is checked first and is
    everywhere-stricter), not a choice made here.
    """
    results = []
    for breakout_value in breakout_persistence_values:
        for actionable_value in actionable_persistence_values:
            if actionable_value < breakout_value:
                continue
            scoring = replace(
                base_config.scoring,
                breakout_min_persistence_scans=breakout_value,
                actionable_min_persistence_scans=actionable_value,
            )
            variant_config = replace(base_config, scoring=scoring)
            records = _run_config_variant(frames, episodes, timelines, config=variant_config)
            results.append(
                {
                    "breakout_min_persistence_scans": breakout_value,
                    "actionable_min_persistence_scans": actionable_value,
                    "episodes_with_first_candidate": sum(
                        1 for r in records if r.first_candidate_at is not None
                    ),
                    "episodes_with_breakout": sum(
                        1 for r in records if r.first_breakout_candidate_at is not None
                    ),
                    "episodes_with_actionable": sum(
                        1 for r in records if r.first_actionable_review_at is not None
                    ),
                }
            )
    return {
        "status": "PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION",
        "note": (
            "Reported separately from the current-production replay. No "
            "configuration is selected, recommended, or applied here."
        ),
        "sweep": results,
    }


def run_threshold_ablation(
    frames,
    episodes: Sequence[MoveEpisode],
    timelines: Mapping[str, SymbolTimeline],
    *,
    base_config: Phase2Config,
    overrides: Sequence[Mapping[str, float]],
) -> dict[str, Any]:
    """Re-run the replay under each named ``SignalQualityConfig`` field override.

    Each item in ``overrides`` is a mapping of ``SignalQualityConfig`` field
    names to values, applied via ``dataclasses.replace`` on top of
    ``base_config.scoring`` - e.g. ``{"breakout_opportunity": 65.0}``. Fields
    that are not real ``SignalQualityConfig`` attributes raise a ``TypeError``
    from ``dataclasses.replace`` rather than silently doing nothing.
    """
    results = []
    for override in overrides:
        scoring = replace(base_config.scoring, **override)
        variant_config = replace(base_config, scoring=scoring)
        records = _run_config_variant(frames, episodes, timelines, config=variant_config)
        results.append(
            {
                "override": dict(override),
                "episodes_with_first_candidate": sum(
                    1 for r in records if r.first_candidate_at is not None
                ),
                "episodes_with_breakout": sum(
                    1 for r in records if r.first_breakout_candidate_at is not None
                ),
                "episodes_with_actionable": sum(
                    1 for r in records if r.first_actionable_review_at is not None
                ),
            }
        )
    return {
        "status": "PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION",
        "note": (
            "Reported separately from the current-production replay. No "
            "configuration is selected, recommended, or applied here."
        ),
        "sweep": results,
    }


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def run_phase3a_timing_replay(
    *,
    observation_file: Path | None = None,
    config: Phase2Config | None = None,
    run_persistence_sweep: bool = True,
) -> dict[str, Any]:
    """Full offline replay for Phase 3A timing analysis. Reads one file, writes nothing.

    ``stage_timing_records`` and ``opportunity_decay`` describe the
    current-production configuration (``config.scoring`` as given, defaulting
    to the same defaults Phase 2 uses). ``persistence_counterfactual`` is
    reported in a clearly separate block per the Phase 3A validation
    requirement that counterfactual results never be conflated with the
    current-production replay.
    """
    config = config or Phase2Config()
    ingestion = read_observations(observation_file)
    timelines = build_timelines(ingestion.observations)
    episodes = build_all_episodes(timelines, config=config.episodes)
    episode_symbols = {row.symbol for row in episodes}
    frames = reconstruct_scan_frames(
        ingestion.observations,
        interval_seconds=config.scan_interval_seconds,
        max_carry_seconds=config.max_carry_seconds,
    )
    detections, _audit_rows = replay_signal_quality(
        frames, config=config, retain_audit_for=episode_symbols
    )
    records = build_stage_timing_records(
        episodes, detections, timelines, scoring_config=config.scoring
    )

    report: dict[str, Any] = {
        "status": "PROVISIONAL_EVENT_SAMPLED_REPLAY",
        "advisory_only": True,
        "weights_are_calibrated": False,
        "trade_authority_changed": False,
        "production_execution_gate_changed": False,
        "total_observations": len(ingestion.observations),
        "rejected_lines": ingestion.rejected_lines,
        "episodes": len(episodes),
        "stage_timing_records": [record.as_dict() for record in records],
        "opportunity_decay": opportunity_decay_by_persistence(records),
    }

    if run_persistence_sweep:
        report["persistence_counterfactual"] = run_persistence_counterfactual(
            frames, episodes, timelines, base_config=config
        )

    return report
