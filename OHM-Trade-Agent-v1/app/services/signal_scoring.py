"""Signal Quality / Explosion Detection v1 scoring, staging and ranking.

Phase 1 is an **interpretable composite**, not a model. Nothing here is
trained, fitted, or self-tuned, and no runtime feedback loop adjusts a weight.
Every number is a prior chosen for legibility; Phase 2 replays production
observation history to decide whether these priors actually separate explosive
movers from failed pumps.

Three separations carry the design:

* ``tradeability_score`` answers "could we act on this at all?" and is derived
  from liquidity alone in Phase 1, because this path has no trustworthy
  spread, depth or slippage feed and must not pretend otherwise.
* ``pattern_strength_score`` answers "is the chart moving?" and contains no
  liquidity term whatsoever.
* ``opportunity_score`` answers "how much attention should OHM give this now?"
  and is the only score that combines the others.

``explosion_potential_score`` is measured *before* the exhaustion penalty, and
``opportunity_score`` applies that penalty exactly once. A mover can therefore
read as highly explosive while scoring poorly on attention because it is
already extended - a distinction worth keeping for audit and Phase 2.

The hard liquidity gate runs *before* scoring, so no amount of pattern strength
can lift an untradeable market into a serious ranking.

Everything in this module is advisory. It never places, confirms, cancels or
modifies an order, and it changes no execution gate.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from app.services.signal_features import (
    ROLLING_VOLUME_GROWTH_PROXY_NOTE,
    SymbolFeatures,
    UniversePercentiles,
)


VERSION = "signal-scoring-v1"

# Stage vocabulary. All four are advisory; none authorises an entry.
STAGE_SUPPRESSED = "SUPPRESSED"
STAGE_EARLY_BUILDING = "EARLY_BUILDING"
STAGE_BREAKOUT_CANDIDATE = "BREAKOUT_CANDIDATE"
STAGE_ACTIONABLE_REVIEW = "ACTIONABLE_REVIEW"

STAGE_PRIORITY = {
    STAGE_ACTIONABLE_REVIEW: 0,
    STAGE_BREAKOUT_CANDIDATE: 1,
    STAGE_EARLY_BUILDING: 2,
    STAGE_SUPPRESSED: 3,
}

# Stages whose cards may reach the main Telegram Broad Watch feed. Anything
# else is audit/log material only.
MAIN_FEED_STAGES = (STAGE_ACTIONABLE_REVIEW, STAGE_BREAKOUT_CANDIDATE)

PATTERN_COMPRESSION_RELEASE = "COMPRESSION_RELEASE"
PATTERN_REACCELERATION = "REACCELERATION"
PATTERN_PROGRESSIVE_EXPANSION = "PROGRESSIVE_EXPANSION"

REASON_INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
REASON_OBSERVATION_ONLY_LIQUIDITY = "OBSERVATION_ONLY_LIQUIDITY"
REASON_WEAK_PATTERN = "WEAK_PATTERN"
REASON_WEAK_RELATIVE_STRENGTH = "WEAK_RELATIVE_STRENGTH"
REASON_INSUFFICIENT_PERSISTENCE = "INSUFFICIENT_PERSISTENCE"
REASON_VOLUME_NOT_CONFIRMING = "VOLUME_NOT_CONFIRMING"
REASON_EXTENDED_MOVE = "EXTENDED_MOVE"
REASON_MOMENTUM_DECELERATING = "MOMENTUM_DECELERATING"
REASON_BLOW_OFF_RISK = "BLOW_OFF_RISK"
REASON_INVALID_MARKET_DATA = "INVALID_MARKET_DATA"

# --------------------------------------------------------------------------
# Prior curves. Each is a monotonic anchor table read by _ramp(); they define
# what a score *means*, and are as provisional as everything else in Phase 1.
# --------------------------------------------------------------------------

# 24h USD notional -> tradeability. Interpolated in log space because
# liquidity is a multiplicative quantity: $250k is to $1M as $1M is to $4M.
TRADEABILITY_ANCHORS_PRIOR: tuple[tuple[float, float], ...] = (
    (100_000.0, 20.0),
    (250_000.0, 40.0),
    (500_000.0, 55.0),
    (1_000_000.0, 70.0),
    (2_500_000.0, 82.0),
    (5_000_000.0, 90.0),
    (10_000_000.0, 100.0),
)

# Percent change per nominal scan interval -> component score.
MOVEMENT_RATE_ANCHORS_PRIOR: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.5, 25.0),
    (1.5, 50.0),
    (3.0, 75.0),
    (6.0, 100.0),
)

# Rolling-notional growth rate per interval -> volume-acceleration proxy score.
# Bands follow the design: flat/falling 0-20, modest 20-45, clearly
# accelerating 45-70, extreme and consistent 70-100.
VOLUME_GROWTH_ANCHORS_PRIOR: tuple[tuple[float, float], ...] = (
    (-2.0, 0.0),
    (0.0, 20.0),
    (1.0, 45.0),
    (3.0, 70.0),
    (10.0, 100.0),
)

# Consecutive qualifying runtime scans -> persistence score.
PERSISTENCE_LADDER_PRIOR: tuple[float, ...] = (0.0, 20.0, 40.0, 60.0, 75.0, 88.0, 100.0)

# Second-derivative adjustment applied to a movement component, in points.
ACCELERATION_ADJUSTMENT_LIMIT_PRIOR = 15.0
# Percent-per-interval of second-derivative that earns the full adjustment.
ACCELERATION_FULL_SCALE_PRIOR = 1.0
# Distance below the 24h high, in percent, at which proximity credit reaches 0.
NEAR_HIGH_ZERO_AT_PCT_PRIOR = 8.0

PATTERN_QUALITY_PRIOR = {
    PATTERN_COMPRESSION_RELEASE: 100.0,
    PATTERN_REACCELERATION: 85.0,
    PATTERN_PROGRESSIVE_EXPANSION: 70.0,
}


@dataclass(frozen=True)
class PatternThresholds:
    """Structural pattern boundaries inherited from the existing detector.

    These reproduce app.services.full_market_observation._transition so the
    useful structural concepts survive the refactor unchanged. What changes is
    the *consequence*: a matched pattern now feeds pattern_strength_score only,
    and no longer implies tradeability or opportunity quality.
    """

    compression_prior_lift_max_pct: float = 3.0
    compression_lift_min_pct: float = 4.0
    compression_lift_change_min_pct: float = 3.0
    compression_near_high_max_pct: float = 4.0

    reacceleration_prior_lift_min_pct: float = 5.0
    reacceleration_lift_change_min_pct: float = 2.0
    reacceleration_price_change_min_pct: float = 1.5
    reacceleration_near_high_max_pct: float = 4.0

    expansion_lift_min_pct: float = 5.0
    expansion_lift_change_min_pct: float = 1.5
    expansion_price_change_min_pct: float = 1.25
    expansion_near_high_max_pct: float = 5.0


@dataclass(frozen=True)
class ScoreWeights:
    """Composition weights. Priors; not calibrated, not learned."""

    # explosion_potential_score
    explosion_price_acceleration: float = 0.30
    explosion_volume_acceleration: float = 0.25
    explosion_relative_strength: float = 0.20
    explosion_persistence: float = 0.15
    explosion_structural_breakout: float = 0.10

    # opportunity_score
    opportunity_explosion_potential: float = 0.30
    opportunity_tradeability: float = 0.25
    opportunity_pattern_strength: float = 0.20
    opportunity_relative_strength: float = 0.15
    opportunity_persistence: float = 0.10

    # pattern_strength_score
    pattern_price_acceleration: float = 0.35
    pattern_structural_expansion: float = 0.30
    pattern_near_high: float = 0.20
    pattern_quality_bonus: float = 0.15

    # relative_strength_score
    relative_price_change_percentile: float = 0.60
    relative_structural_percentile: float = 0.40


@dataclass(frozen=True)
class ExhaustionThresholds:
    """Chase-penalty boundaries.

    The objective is to separate early strong acceleration from late parabolic
    extension. Strength alone is never penalised: the extension term does not
    engage at all below ``run_up_soft_pct``.
    """

    run_up_soft_pct: float = 12.0
    run_up_hard_pct: float = 35.0
    extension_max_points: float = 25.0

    # lift_from_24h_low is weak legacy evidence only and is capped so it can
    # never dominate the penalty.
    lift_legacy_start_pct: float = 25.0
    lift_legacy_full_pct: float = 60.0
    lift_legacy_max_points: float = 6.0

    decelerating_points: float = 10.0
    blow_off_points: float = 12.0

    total_max_points: float = 50.0
    # Penalty at or above which EXTENDED_MOVE is reported.
    extended_move_reason_at: float = 10.0


@dataclass(frozen=True)
class SignalQualityConfig:
    """Every tunable Phase 1 uses. No scorer reads a bare literal threshold."""

    enabled: bool = False
    early_alerts_enabled: bool = False

    min_liquidity_usd: float = 100_000.0
    observation_liquidity_usd: float = 250_000.0
    preferred_liquidity_usd: float = 1_000_000.0

    max_cards_per_scan: int = 4

    early_building_opportunity: float = 55.0
    early_building_explosion: float = 50.0
    early_building_tradeability: float = 20.0

    breakout_opportunity: float = 70.0
    breakout_explosion: float = 65.0
    breakout_tradeability: float = 40.0
    breakout_min_persistence_scans: int = 2
    breakout_max_exhaustion: float = 25.0

    actionable_opportunity: float = 80.0
    actionable_explosion: float = 75.0
    actionable_tradeability: float = 70.0
    actionable_min_persistence_scans: int = 3
    actionable_max_exhaustion: float = 20.0

    # Diagnostic reason-code boundaries. These annotate a candidate; they do
    # not gate it - the stage machine does that.
    weak_pattern_below: float = 40.0
    weak_relative_strength_below: float = 50.0
    volume_not_confirming_below: float = 45.0

    weights: ScoreWeights = field(default_factory=ScoreWeights)
    patterns: PatternThresholds = field(default_factory=PatternThresholds)
    exhaustion: ExhaustionThresholds = field(default_factory=ExhaustionThresholds)

    @classmethod
    def from_settings(cls, settings: Any) -> "SignalQualityConfig":
        """Map the flat Pydantic Settings fields onto the scoring config.

        Only the operator-facing knobs are environment-driven; composition
        weights and structural pattern boundaries stay in code where they are
        reviewable as priors rather than becoming production drift surface.
        """

        def _get(name: str, fallback: Any) -> Any:
            value = getattr(settings, name, None)
            return fallback if value is None else value

        return cls(
            enabled=bool(_get("signal_quality_v1_enabled", False)),
            early_alerts_enabled=bool(_get("signal_quality_early_alerts_enabled", False)),
            min_liquidity_usd=float(_get("signal_quality_min_liquidity_usd", 100_000.0)),
            observation_liquidity_usd=float(_get("signal_quality_observation_liquidity_usd", 250_000.0)),
            preferred_liquidity_usd=float(_get("signal_quality_preferred_liquidity_usd", 1_000_000.0)),
            max_cards_per_scan=int(_get("signal_quality_max_cards_per_scan", 4)),
            early_building_opportunity=float(_get("signal_quality_early_building_opportunity", 55)),
            early_building_explosion=float(_get("signal_quality_early_building_explosion", 50)),
            early_building_tradeability=float(_get("signal_quality_early_building_tradeability", 20)),
            breakout_opportunity=float(_get("signal_quality_breakout_opportunity", 70)),
            breakout_explosion=float(_get("signal_quality_breakout_explosion", 65)),
            breakout_tradeability=float(_get("signal_quality_breakout_tradeability", 40)),
            breakout_min_persistence_scans=int(_get("signal_quality_breakout_min_persistence_scans", 2)),
            breakout_max_exhaustion=float(_get("signal_quality_breakout_max_exhaustion", 25)),
            actionable_opportunity=float(_get("signal_quality_actionable_opportunity", 80)),
            actionable_explosion=float(_get("signal_quality_actionable_explosion", 75)),
            actionable_tradeability=float(_get("signal_quality_actionable_tradeability", 70)),
            actionable_min_persistence_scans=int(_get("signal_quality_actionable_min_persistence_scans", 3)),
            actionable_max_exhaustion=float(_get("signal_quality_actionable_max_exhaustion", 20)),
        )


@dataclass(frozen=True)
class ExhaustionAssessment:
    penalty: float = 0.0
    reasons: tuple[str, ...] = ()
    band: str = "NONE"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalQualityCandidate:
    """One market's full advisory assessment, kept inspectable end to end."""

    version: str
    symbol: str
    stage: str
    pattern: str | None

    tradeability_score: int
    pattern_strength_score: int
    volume_acceleration_score: int
    persistence_score: int
    relative_strength_score: int
    explosion_potential_score: int
    opportunity_score: int

    exhaustion_penalty: int
    exhaustion_band: str
    liquidity_24h_usd_approx: float
    persistence_scans: int
    relative_strength_percentile: float
    universe_size: int

    reasons: tuple[str, ...]
    components: Mapping[str, float]

    # Phase 1 invariants. Asserted in tests; never flipped by this module.
    advisory_only: bool = True
    weights_are_calibrated: bool = False
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    @property
    def suppressed(self) -> bool:
        return self.stage == STAGE_SUPPRESSED

    @property
    def rank_key(self) -> tuple[int, float, float, float, float, str]:
        """Leaderboard ordering per the design: stage, then score cascade."""
        return (
            STAGE_PRIORITY.get(self.stage, len(STAGE_PRIORITY)),
            -float(self.opportunity_score),
            -float(self.explosion_potential_score),
            -float(self.tradeability_score),
            -float(self.liquidity_24h_usd_approx),
            self.symbol,
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        payload["components"] = dict(self.components)
        payload["suppressed"] = self.suppressed
        return payload


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return low
    return max(low, min(high, float(value)))


def _ramp(value: float, anchors: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation across a monotonic anchor table."""
    if not math.isfinite(value):
        return anchors[0][1]
    if value <= anchors[0][0]:
        return anchors[0][1]
    if value >= anchors[-1][0]:
        return anchors[-1][1]
    for (low_x, low_y), (high_x, high_y) in zip(anchors, anchors[1:]):
        if low_x <= value <= high_x:
            span = high_x - low_x
            if span <= 0:
                return high_y
            return low_y + (high_y - low_y) * (value - low_x) / span
    return anchors[-1][1]


def tradeability_score(notional_24h_usd: float, *, config: SignalQualityConfig) -> float:
    """Score how actionable a market is, from 24h USD notional alone.

    Phase 1 has no spread, depth, slippage, trade-count or turnover feed on
    this path, so this deliberately measures one thing and says so. The
    signature is the extension point: Phase 2 adds those inputs here without
    disturbing any caller.

    Hard gating is independent of this score - see ``apply_hard_gate``.
    """
    if not math.isfinite(notional_24h_usd) or notional_24h_usd < config.min_liquidity_usd:
        return 0.0
    log_anchors = tuple(
        (math.log10(max(value, 1.0)), score) for value, score in TRADEABILITY_ANCHORS_PRIOR
    )
    return _clamp(_ramp(math.log10(max(notional_24h_usd, 1.0)), log_anchors))


def _acceleration_adjustment(second_derivative_pct: float) -> float:
    """Points added or removed for a rate that is itself rising or falling."""
    if not math.isfinite(second_derivative_pct) or ACCELERATION_FULL_SCALE_PRIOR <= 0:
        return 0.0
    scaled = second_derivative_pct / ACCELERATION_FULL_SCALE_PRIOR * ACCELERATION_ADJUSTMENT_LIMIT_PRIOR
    return max(-ACCELERATION_ADJUSTMENT_LIMIT_PRIOR, min(ACCELERATION_ADJUSTMENT_LIMIT_PRIOR, scaled))


def price_acceleration_component(features: SymbolFeatures) -> float:
    """Rate of advance, adjusted for whether that rate is building or fading."""
    base = _ramp(features.price_change_rate_pct, MOVEMENT_RATE_ANCHORS_PRIOR)
    return _clamp(base + _acceleration_adjustment(features.price_acceleration_pct))


def structural_expansion_component(features: SymbolFeatures) -> float:
    """Expansion of lift off the 24h low - the structural analogue of price."""
    base = _ramp(features.lift_change_rate_pct, MOVEMENT_RATE_ANCHORS_PRIOR)
    return _clamp(base + _acceleration_adjustment(features.lift_acceleration_pct))


def near_high_component(features: SymbolFeatures) -> float:
    """Proximity to the current 24h high, as breakout position evidence."""
    if NEAR_HIGH_ZERO_AT_PCT_PRIOR <= 0:
        return 0.0
    decay = 100.0 / NEAR_HIGH_ZERO_AT_PCT_PRIOR
    return _clamp(100.0 - max(0.0, features.distance_from_24h_high_pct) * decay)


def classify_pattern(features: SymbolFeatures, *, config: SignalQualityConfig) -> str | None:
    """Match the inherited structural patterns, or None.

    Requires intact continuity. The structural boundaries below are raw
    interval deltas inherited from the legacy detector, so across an outage a
    move that merely *accumulated* while OHM was not looking would satisfy them
    exactly as a genuine acceleration does. A pattern is a claim that OHM
    observed a transition happen; it cannot be made about an interval OHM did
    not observe. Broken continuity therefore yields None, and the market keeps
    its leaderboard row for audit without collecting pattern-quality credit.

    Takes features only. Coin identity is not an input and must never become
    one.
    """
    if not features.valid or not features.continuity_intact:
        return None
    limits = config.patterns
    lift = features.lift_from_24h_low_pct
    lift_change = features.lift_change_since_prior_pct
    prior_lift = lift - lift_change
    price_change = features.price_change_since_prior_pct
    near_high = features.distance_from_24h_high_pct

    if (
        prior_lift <= limits.compression_prior_lift_max_pct
        and lift >= limits.compression_lift_min_pct
        and lift_change >= limits.compression_lift_change_min_pct
        and near_high <= limits.compression_near_high_max_pct
    ):
        return PATTERN_COMPRESSION_RELEASE
    if (
        prior_lift >= limits.reacceleration_prior_lift_min_pct
        and lift_change >= limits.reacceleration_lift_change_min_pct
        and price_change >= limits.reacceleration_price_change_min_pct
        and near_high <= limits.reacceleration_near_high_max_pct
    ):
        return PATTERN_REACCELERATION
    if (
        lift >= limits.expansion_lift_min_pct
        and lift_change >= limits.expansion_lift_change_min_pct
        and price_change >= limits.expansion_price_change_min_pct
        and near_high <= limits.expansion_near_high_max_pct
    ):
        return PATTERN_PROGRESSIVE_EXPANSION
    return None


def pattern_strength_score(
    features: SymbolFeatures,
    pattern: str | None,
    *,
    config: SignalQualityConfig,
) -> float:
    """Answer "is the chart moving?" and nothing else.

    Contains no liquidity term by construction. A $4k market and a $40M market
    with identical geometry score identically here; they diverge at
    tradeability and at the hard gate.
    """
    weights = config.weights
    score = (
        weights.pattern_price_acceleration * price_acceleration_component(features)
        + weights.pattern_structural_expansion * structural_expansion_component(features)
        + weights.pattern_near_high * near_high_component(features)
        + weights.pattern_quality_bonus * PATTERN_QUALITY_PRIOR.get(pattern or "", 0.0)
    )
    return _clamp(score)


def volume_acceleration_score(features: SymbolFeatures) -> float:
    """Bounded score for the rolling-notional growth proxy.

    Corroboration caps implement the design's refusal to award a high score
    from a single anomalous snapshot: reaching the upper bands requires several
    consecutive rising intervals *and* growth against the multi-scan median,
    not one outsized print.
    """
    base = _clamp(_ramp(features.rolling_notional_growth_rate_pct, VOLUME_GROWTH_ANCHORS_PRIOR))

    if features.positive_growth_intervals < 1:
        cap = VOLUME_GROWTH_ANCHORS_PRIOR[1][1]  # flat/falling ceiling
    elif features.positive_growth_intervals < 2:
        cap = VOLUME_GROWTH_ANCHORS_PRIOR[2][1]  # modest ceiling
    elif features.positive_growth_intervals < 3:
        cap = VOLUME_GROWTH_ANCHORS_PRIOR[3][1]  # clearly-accelerating ceiling
    else:
        cap = 100.0

    if features.rolling_notional_vs_median3_pct <= 0.0:
        cap = min(cap, VOLUME_GROWTH_ANCHORS_PRIOR[2][1])

    return _clamp(min(base, cap))


def persistence_score(consecutive_qualifying_scans: int) -> float:
    """Ladder over consecutive qualifying runtime scans.

    Counted in scans, not in persisted JSONL rows: the observation stream is
    event-sampled, so a quiet-but-qualifying scan that was never written to
    disk still earns its place in the chain.
    """
    if consecutive_qualifying_scans <= 0:
        return 0.0
    index = min(int(consecutive_qualifying_scans), len(PERSISTENCE_LADDER_PRIOR) - 1)
    return PERSISTENCE_LADDER_PRIOR[index]


def relative_strength_score(
    percentiles: UniversePercentiles,
    *,
    config: SignalQualityConfig,
) -> float:
    """Blend the whole-universe percentile ranks into one 0-100 score."""
    weights = config.weights
    return _clamp(
        weights.relative_price_change_percentile * percentiles.price_change_percentile
        + weights.relative_structural_percentile * percentiles.structural_acceleration_percentile
    )


def structural_breakout_component(features: SymbolFeatures) -> float:
    """Breakout quality: position at the high plus genuine structural expansion."""
    return _clamp(0.5 * near_high_component(features) + 0.5 * structural_expansion_component(features))


def assess_exhaustion(
    features: SymbolFeatures,
    *,
    config: SignalQualityConfig,
) -> ExhaustionAssessment:
    """Distinguish early strong acceleration from late parabolic extension.

    A coin is never penalised for being strong. The extension term stays at
    zero until the recent run-up passes ``run_up_soft_pct``; below that a
    fast, fresh mover collects no chase penalty at all.
    """
    limits = config.exhaustion
    if not features.valid:
        return ExhaustionAssessment()

    penalty = 0.0
    reasons: list[str] = []

    run_up = features.window_run_up_pct
    if run_up > limits.run_up_soft_pct:
        penalty += _ramp(
            run_up,
            (
                (limits.run_up_soft_pct, 0.0),
                (limits.run_up_hard_pct, limits.extension_max_points),
            ),
        )

    # Weak legacy evidence only, capped so it cannot dominate.
    lift = features.lift_from_24h_low_pct
    if lift > limits.lift_legacy_start_pct:
        penalty += _ramp(
            lift,
            (
                (limits.lift_legacy_start_pct, 0.0),
                (limits.lift_legacy_full_pct, limits.lift_legacy_max_points),
            ),
        )

    if run_up >= limits.run_up_soft_pct and features.momentum_decelerating:
        penalty += limits.decelerating_points
        reasons.append(REASON_MOMENTUM_DECELERATING)

    volume_not_strengthening = (
        features.rolling_notional_growth_rate_pct <= 0.0
        or features.rolling_notional_vs_median3_pct <= 0.0
    )
    if run_up >= limits.run_up_hard_pct and volume_not_strengthening:
        penalty += limits.blow_off_points
        reasons.append(REASON_BLOW_OFF_RISK)

    penalty = max(0.0, min(limits.total_max_points, penalty))
    if penalty >= limits.extended_move_reason_at:
        reasons.insert(0, REASON_EXTENDED_MOVE)

    if penalty >= 35.0:
        band = "BLOW_OFF"
    elif penalty >= 20.0:
        band = "HIGH"
    elif penalty >= 10.0:
        band = "MODERATE"
    elif penalty > 0.0:
        band = "LOW"
    else:
        band = "NONE"

    return ExhaustionAssessment(penalty=round(penalty, 4), reasons=tuple(reasons), band=band)


def explosion_potential_score(
    *,
    price_acceleration: float,
    volume_acceleration: float,
    relative_strength: float,
    persistence: float,
    structural_breakout: float,
    config: SignalQualityConfig,
) -> float:
    """Resemblance to an explosive setup, *before* any chase adjustment.

    Deliberately pre-exhaustion. Keeping the two separate preserves a
    distinction that matters for audit and for Phase 2 calibration: a mover can
    genuinely look explosive (high potential) while being a poor thing to look
    at right now because it is already extended (low opportunity). Folding the
    penalty in here would erase that difference and, because opportunity also
    consumes this score, would charge the penalty twice.

    Explicitly not a probability.
    """
    weights = config.weights
    return _clamp(
        weights.explosion_price_acceleration * price_acceleration
        + weights.explosion_volume_acceleration * volume_acceleration
        + weights.explosion_relative_strength * relative_strength
        + weights.explosion_persistence * persistence
        + weights.explosion_structural_breakout * structural_breakout
    )


def opportunity_score(
    *,
    explosion_potential: float,
    tradeability: float,
    pattern_strength: float,
    relative_strength: float,
    persistence: float,
    exhaustion_penalty: float,
    config: SignalQualityConfig,
) -> float:
    """How much attention OHM should give this setup now.

    This is the single place the exhaustion penalty is applied. Every input
    below is pre-exhaustion, so the configured penalty costs exactly its
    nominal points here and nowhere else.

    The hard liquidity gate has already run by the time this is reached, so a
    thin market cannot arrive here at all - which is what keeps a $4k pump off
    the leaderboard regardless of its acceleration.
    """
    weights = config.weights
    raw = (
        weights.opportunity_explosion_potential * explosion_potential
        + weights.opportunity_tradeability * tradeability
        + weights.opportunity_pattern_strength * pattern_strength
        + weights.opportunity_relative_strength * relative_strength
        + weights.opportunity_persistence * persistence
    )
    return _clamp(raw - exhaustion_penalty)


def apply_hard_gate(
    features: SymbolFeatures,
    *,
    config: SignalQualityConfig,
) -> tuple[bool, str | None]:
    """Tradeability gate, applied before any score is trusted.

    Returns (suppressed, reason). Independent of ``tradeability_score``: no
    pattern score can overturn it.
    """
    if not features.valid:
        return True, REASON_INVALID_MARKET_DATA
    notional = features.notional_24h_usd_approx
    if not math.isfinite(notional) or notional < config.min_liquidity_usd:
        return True, REASON_INSUFFICIENT_LIQUIDITY
    return False, None


def determine_stage(
    *,
    opportunity: float,
    explosion: float,
    tradeability: float,
    persistence_scans: int,
    exhaustion_penalty: float,
    liquidity_24h_usd: float,
    config: SignalQualityConfig,
) -> str:
    """Advisory stage. ACTIONABLE_REVIEW authorises human review, never entry."""
    observation_only = liquidity_24h_usd < config.observation_liquidity_usd

    if (
        not observation_only
        and opportunity >= config.actionable_opportunity
        and explosion >= config.actionable_explosion
        and tradeability >= config.actionable_tradeability
        and persistence_scans >= config.actionable_min_persistence_scans
        and exhaustion_penalty < config.actionable_max_exhaustion
    ):
        return STAGE_ACTIONABLE_REVIEW

    if (
        not observation_only
        and opportunity >= config.breakout_opportunity
        and explosion >= config.breakout_explosion
        and tradeability >= config.breakout_tradeability
        and persistence_scans >= config.breakout_min_persistence_scans
        and exhaustion_penalty < config.breakout_max_exhaustion
    ):
        return STAGE_BREAKOUT_CANDIDATE

    if (
        opportunity >= config.early_building_opportunity
        and explosion >= config.early_building_explosion
        and tradeability >= config.early_building_tradeability
        and exhaustion_penalty < config.breakout_max_exhaustion
    ):
        return STAGE_EARLY_BUILDING

    return STAGE_SUPPRESSED


def _diagnostic_reasons(
    *,
    features: SymbolFeatures,
    pattern_strength: float,
    relative_strength: float,
    volume_acceleration: float,
    persistence_scans: int,
    config: SignalQualityConfig,
) -> list[str]:
    """Deterministic annotations describing why a candidate looks as it does."""
    reasons: list[str] = []
    if features.notional_24h_usd_approx < config.observation_liquidity_usd:
        reasons.append(REASON_OBSERVATION_ONLY_LIQUIDITY)
    if pattern_strength < config.weak_pattern_below:
        reasons.append(REASON_WEAK_PATTERN)
    if relative_strength < config.weak_relative_strength_below:
        reasons.append(REASON_WEAK_RELATIVE_STRENGTH)
    if persistence_scans < config.breakout_min_persistence_scans:
        reasons.append(REASON_INSUFFICIENT_PERSISTENCE)
    if volume_acceleration < config.volume_not_confirming_below:
        reasons.append(REASON_VOLUME_NOT_CONFIRMING)
    return reasons


def evaluate_candidate(
    symbol: str,
    features: SymbolFeatures,
    percentiles: UniversePercentiles,
    *,
    config: SignalQualityConfig,
) -> SignalQualityCandidate:
    """Score, stage and annotate one market.

    ``symbol`` is carried through for rendering and audit only. It is passed to
    no scorer and influences no number on the returned candidate.
    """
    suppressed, gate_reason = apply_hard_gate(features, config=config)

    if suppressed:
        # A gated market still gets a tradeability reading (0 by definition
        # below the floor) and a retained reason, so the audit trail explains
        # the suppression instead of the row simply vanishing.
        liquidity = features.notional_24h_usd_approx if features.valid else 0.0
        return SignalQualityCandidate(
            version=VERSION,
            symbol=symbol,
            stage=STAGE_SUPPRESSED,
            pattern=classify_pattern(features, config=config),
            tradeability_score=int(round(tradeability_score(liquidity, config=config))),
            pattern_strength_score=int(
                round(pattern_strength_score(features, classify_pattern(features, config=config), config=config))
            )
            if features.valid
            else 0,
            volume_acceleration_score=int(round(volume_acceleration_score(features))) if features.valid else 0,
            persistence_score=int(round(persistence_score(features.consecutive_qualifying_scans))),
            relative_strength_score=int(round(relative_strength_score(percentiles, config=config))),
            explosion_potential_score=0,
            opportunity_score=0,
            exhaustion_penalty=0,
            exhaustion_band="NONE",
            liquidity_24h_usd_approx=round(liquidity, 2),
            persistence_scans=features.consecutive_qualifying_scans,
            relative_strength_percentile=percentiles.price_change_percentile,
            universe_size=percentiles.universe_size,
            reasons=(gate_reason,) if gate_reason else (),
            components={},
        )

    pattern = classify_pattern(features, config=config)
    price_component = price_acceleration_component(features)
    structural_component = structural_expansion_component(features)
    breakout_component = structural_breakout_component(features)

    tradeability = tradeability_score(features.notional_24h_usd_approx, config=config)
    pattern_strength = pattern_strength_score(features, pattern, config=config)
    volume_acceleration = volume_acceleration_score(features)
    persistence = persistence_score(features.consecutive_qualifying_scans)
    relative_strength = relative_strength_score(percentiles, config=config)
    exhaustion = assess_exhaustion(features, config=config)

    explosion = explosion_potential_score(
        price_acceleration=price_component,
        volume_acceleration=volume_acceleration,
        relative_strength=relative_strength,
        persistence=persistence,
        structural_breakout=breakout_component,
        config=config,
    )
    opportunity = opportunity_score(
        explosion_potential=explosion,
        tradeability=tradeability,
        pattern_strength=pattern_strength,
        relative_strength=relative_strength,
        persistence=persistence,
        exhaustion_penalty=exhaustion.penalty,
        config=config,
    )

    stage = determine_stage(
        opportunity=opportunity,
        explosion=explosion,
        tradeability=tradeability,
        persistence_scans=features.consecutive_qualifying_scans,
        exhaustion_penalty=exhaustion.penalty,
        liquidity_24h_usd=features.notional_24h_usd_approx,
        config=config,
    )

    reasons = _diagnostic_reasons(
        features=features,
        pattern_strength=pattern_strength,
        relative_strength=relative_strength,
        volume_acceleration=volume_acceleration,
        persistence_scans=features.consecutive_qualifying_scans,
        config=config,
    )
    reasons.extend(reason for reason in exhaustion.reasons if reason not in reasons)

    return SignalQualityCandidate(
        version=VERSION,
        symbol=symbol,
        stage=stage,
        pattern=pattern,
        tradeability_score=int(round(tradeability)),
        pattern_strength_score=int(round(pattern_strength)),
        volume_acceleration_score=int(round(volume_acceleration)),
        persistence_score=int(round(persistence)),
        relative_strength_score=int(round(relative_strength)),
        explosion_potential_score=int(round(explosion)),
        opportunity_score=int(round(opportunity)),
        exhaustion_penalty=int(round(exhaustion.penalty)),
        exhaustion_band=exhaustion.band,
        liquidity_24h_usd_approx=round(features.notional_24h_usd_approx, 2),
        persistence_scans=features.consecutive_qualifying_scans,
        relative_strength_percentile=percentiles.price_change_percentile,
        universe_size=percentiles.universe_size,
        reasons=tuple(reasons),
        components={
            "price_acceleration": round(price_component, 4),
            "structural_expansion": round(structural_component, 4),
            "near_high": round(near_high_component(features), 4),
            "structural_breakout": round(breakout_component, 4),
            "rolling_notional_growth_rate_pct": features.rolling_notional_growth_rate_pct,
            "window_run_up_pct": features.window_run_up_pct,
        },
    )


def evaluate_universe(
    features_by_symbol: Mapping[str, SymbolFeatures],
    percentiles_by_symbol: Mapping[str, UniversePercentiles],
    *,
    config: SignalQualityConfig,
) -> tuple[SignalQualityCandidate, ...]:
    """Score every observed market and return the ranked leaderboard.

    Suppressed rows are retained with their reasons: they are audit evidence,
    and dropping them here would make the gate invisible to review.
    """
    empty = UniversePercentiles()
    candidates = [
        evaluate_candidate(
            str(symbol).upper(),
            features,
            percentiles_by_symbol.get(symbol, empty),
            config=config,
        )
        for symbol, features in features_by_symbol.items()
    ]
    candidates.sort(key=lambda row: row.rank_key)
    return tuple(candidates)


def main_feed_candidates(
    candidates: Sequence[SignalQualityCandidate],
    *,
    config: SignalQualityConfig,
) -> tuple[SignalQualityCandidate, ...]:
    """The only candidates permitted to reach the main Telegram feed.

    Replaces the previous ``broad_candidates[:4]`` truncation, which sliced an
    unfiltered list and so let low-liquidity WATCH_ONLY rows consume the feed.
    Filtering happens first; the cap applies to what survives.
    """
    allowed = set(MAIN_FEED_STAGES)
    if config.early_alerts_enabled:
        allowed.add(STAGE_EARLY_BUILDING)
    eligible = [row for row in candidates if row.stage in allowed]
    return tuple(eligible[: max(1, int(config.max_cards_per_scan))])


def leaderboard_rows(candidates: Sequence[SignalQualityCandidate]) -> list[dict[str, Any]]:
    """Audit projection retaining suppressed rows and their reasons."""
    return [row.as_dict() for row in candidates]


def volume_growth_proxy_label(score: float) -> str:
    """Human label for the rolling-growth proxy. Not a volume measurement."""
    if score >= VOLUME_GROWTH_ANCHORS_PRIOR[3][1]:
        return "STRONG"
    if score >= VOLUME_GROWTH_ANCHORS_PRIOR[2][1]:
        return "BUILDING"
    if score >= VOLUME_GROWTH_ANCHORS_PRIOR[1][1]:
        return "MODEST"
    return "FLAT"


__all__ = [
    "ROLLING_VOLUME_GROWTH_PROXY_NOTE",
    "SignalQualityCandidate",
    "SignalQualityConfig",
    "STAGE_ACTIONABLE_REVIEW",
    "STAGE_BREAKOUT_CANDIDATE",
    "STAGE_EARLY_BUILDING",
    "STAGE_SUPPRESSED",
    "VERSION",
    "apply_hard_gate",
    "assess_exhaustion",
    "classify_pattern",
    "determine_stage",
    "evaluate_candidate",
    "evaluate_universe",
    "explosion_potential_score",
    "leaderboard_rows",
    "main_feed_candidates",
    "opportunity_score",
    "pattern_strength_score",
    "persistence_score",
    "relative_strength_score",
    "tradeability_score",
    "volume_acceleration_score",
    "volume_growth_proxy_label",
]
