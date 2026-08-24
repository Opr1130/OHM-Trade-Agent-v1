"""Signal Quality v1 — Phase 2 historical replay and calibration harness.

Read-only and offline. This module answers one question: does the Phase 1
detector surface genuine explosive movers *early*, and how often does it fire
on moves that never happen? It changes no threshold, sends no message, places
no order, and touches no private Kraken path.

Three correctness properties carry the whole analysis. Each is enforced
structurally rather than by convention, and each has dedicated tests.

**No lookahead.** Features are derived only from reconstructed scans at or
before the decision time. Outcome data is consulted only after a decision has
already been scored, and never feeds a feature, a percentile, or a stage.

**Post-detection evaluation.** A detection is credited only for price movement
strictly *after* its own timestamp. The first draft of this harness credited a
detection for lying inside a 24-hour window whose move may have completed
before the detection existed - that inverts cause and effect and is the single
most dangerous error available here. Forward returns are now measured from the
detection's own price and timestamp, and "detected before +X" requires the
detection to precede the episode's actual +X crossing.

**Episode de-duplication.** One explosive run is one episode. Labelling every
persisted row produces hundreds of overlapping windows over the same move and
would inflate every capture rate by the density of the run itself.

The observation stream is event-sampled, so every number this module produces
is provisional until cross-validated against OHLC. Nothing here may be used to
tune production automatically.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Protocol, Sequence

from app.services.signal_features import (
    FeatureDerivationConfig,
    ObservationSnapshot,
    QualifyingConditions,
    derive_features_for_universe,
    derive_universe_percentiles,
    snapshot_from_mapping,
)
from app.services.signal_scoring import (
    STAGE_ACTIONABLE_REVIEW,
    STAGE_BREAKOUT_CANDIDATE,
    STAGE_EARLY_BUILDING,
    SignalQualityConfig,
    evaluate_universe,
)


VERSION = "signal-quality-phase2-replay-v2"

# The report status is ALWAYS provisional in this harness. OHLC attaches an
# auxiliary peak comparison; it does not rebuild the episode peak, class,
# timing or threshold crossings, so no metric derived from those becomes
# validated. Upgrading the whole report because one episode had candles would
# claim far more than the data supports, so there is deliberately no
# "cross-validated" status constant to reach for.
REPORT_STATUS_PROVISIONAL = "PROVISIONAL_EVENT_SAMPLED_REPLAY"

# What OHLC coverage actually establishes, stated separately from the report
# status so the two can never be conflated.
OHLC_STATUS_NONE = "NO_OHLC_VALIDATION"
OHLC_STATUS_PARTIAL_PEAK = "PARTIAL_OHLC_PEAK_COMPARISON"
OHLC_STATUS_COMPLETE_PEAK = "COMPLETE_OHLC_PEAK_COMPARISON"

# Which reported metrics OHLC coverage does and does not speak to. Every metric
# in this harness is built from close-based episode construction, so OHLC highs
# corroborate peak magnitude alone.
OHLC_FULLY_VALIDATED_METRICS: tuple[str, ...] = ()
OHLC_PARTIALLY_VALIDATED_METRICS: tuple[str, ...] = (
    "episode.peak_return_pct (magnitude only, compared not replaced)",
)
OHLC_NOT_VALIDATED_METRICS: tuple[str, ...] = (
    "episode.outcome_class",
    "episode.peak_at",
    "episode.crossings (threshold timing)",
    "detection_metrics_by_threshold_cohort",
    "detection_metrics_by_exclusive_class",
    "early-capture timing and lead times",
    "detected_before_plus_N",
    "missed_winners thresholds",
    "false_positives (forward returns use persisted last_price)",
)

DEFAULT_OBSERVATION_FILE = Path("/app/data/full_market_observations.jsonl")

# Production cadence at the time of writing. Phase 1 ships with a 600s scan
# interval, a 2.5x continuity multiplier and 3600s stale-history retention;
# the replay mirrors those so reconstructed decisions match live ones.
DEFAULT_SCAN_INTERVAL_SECONDS = 600
DEFAULT_CONTINUITY_MULTIPLIER = 2.5
DEFAULT_HISTORY_SCANS = 8
DEFAULT_MAX_CARRY_SECONDS = 3600
DEFAULT_OUTCOME_HORIZON_HOURS = 24

# Episode thresholds, in percent above the episode baseline. These generate
# CUMULATIVE cohorts: an episode peaking at +320% belongs to every one of them.
OUTCOME_THRESHOLDS: tuple[float, ...] = (20.0, 50.0, 100.0, 200.0, 300.0)

# Mutually EXCLUSIVE bands, half-open [low, high). An episode belongs to exactly
# one. Reported alongside the cumulative cohorts because the two answer
# different questions and were previously easy to confuse: "MOVE_50" reading as
# an exclusive class while actually meaning "peaked at +50% or more".
EXCLUSIVE_CLASS_BANDS: tuple[tuple[str, float, float], ...] = (
    ("MOVE_20_50", 20.0, 50.0),
    ("MOVE_50_100", 50.0, 100.0),
    ("MOVE_100_200", 100.0, 200.0),
    ("MOVE_200_300", 200.0, 300.0),
    ("MOVE_300_PLUS", 300.0, float("inf")),
)
# Additional early marks used only for missed-winner forensics.
FORENSIC_THRESHOLDS: tuple[float, ...] = (3.0, 5.0, 10.0, 20.0, 50.0)

ELIGIBLE_STAGES = frozenset({
    STAGE_EARLY_BUILDING,
    STAGE_BREAKOUT_CANDIDATE,
    STAGE_ACTIONABLE_REVIEW,
})

# Phase 1's persistence thresholds, mirrored here so the replay can state how
# far a carried value may drift without importing the runtime module (which
# would pull the Kraken client into this offline import graph).
#
# These bound the imputation error, and that is the whole reason
# last-observation-carried-forward is defensible here rather than merely
# convenient: _should_persist writes a row *because* something moved. If no row
# was written between two scans, price moved less than
# CARRY_PRICE_DRIFT_BOUND_PCT - the absence of a row is itself evidence of
# quiet. The one exception is the hourly heartbeat, which writes regardless.
#
# test_carry_error_bounds_match_phase_1 asserts these stay in sync with
# app/services/full_market_observation.py.
CARRY_PRICE_DRIFT_BOUND_PCT = 1.0
CARRY_LIFT_DRIFT_BOUND_PCT = 0.75
CARRY_HIGH_DISTANCE_DRIFT_BOUND_PCT = 0.75
CARRY_NOTIONAL_RATIO_BOUND = 1.50
CARRY_HEARTBEAT_SECONDS = 3600

FORWARD_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("FAIL_LT_5", float("-inf"), 5.0),
    ("MOVE_5_10", 5.0, 10.0),
    ("MOVE_10_20", 10.0, 20.0),
    ("MOVE_20_50", 20.0, 50.0),
    ("MOVE_50_PLUS", 50.0, float("inf")),
)

LIQUIDITY_BANDS: tuple[tuple[str, float, float], ...] = (
    ("LT_100K", 0.0, 100_000.0),
    ("100K_250K", 100_000.0, 250_000.0),
    ("250K_1M", 250_000.0, 1_000_000.0),
    ("1M_5M", 1_000_000.0, 5_000_000.0),
    ("5M_PLUS", 5_000_000.0, float("inf")),
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeConfig:
    """Parameters of the major-move episode model.

    An episode is one explosive run: a baseline low, a rise through one or more
    thresholds, a peak, and a retracement that closes it. The next episode
    cannot open until price resets, which is what stops a single run from being
    counted as many overlapping winners.
    """

    # Rise above the running baseline that opens an episode.
    trigger_pct: float = 20.0
    # Retracement from the running peak that closes an episode.
    close_retrace_pct: float = 30.0
    # How far back the baseline low may be drawn from.
    baseline_window_hours: float = 24.0
    # An episode closes if the peak is this old without a new high.
    peak_cooldown_hours: float = 12.0


@dataclass(frozen=True)
class Phase2Config:
    scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS
    continuity_multiplier: float = DEFAULT_CONTINUITY_MULTIPLIER
    history_scans: int = DEFAULT_HISTORY_SCANS
    max_carry_seconds: int = DEFAULT_MAX_CARRY_SECONDS
    horizon_hours: float = DEFAULT_OUTCOME_HORIZON_HOURS
    episodes: EpisodeConfig = field(default_factory=EpisodeConfig)
    scoring: SignalQualityConfig = field(
        default_factory=lambda: SignalQualityConfig(enabled=True, early_alerts_enabled=True)
    )
    # Fraction of the timeline used for calibration; the remainder is held out.
    calibration_fraction: float = 0.6

    @property
    def horizon(self) -> timedelta:
        return timedelta(hours=self.horizon_hours)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayObservation:
    observed_at: datetime
    symbol: str
    snapshot: ObservationSnapshot


@dataclass(frozen=True)
class ScanCell:
    """One symbol's state at one reconstructed scan.

    ``imputed`` records whether the values were actually observed at this scan
    or carried forward from an earlier event. The distinction is reported, never
    hidden: a carried cell is a real production scan (Phase 1 scans on a timer
    regardless of whether the JSONL writer persisted a row) but its *values* are
    stale, and stale values must not be presented as fresh observations.
    """

    snapshot: ObservationSnapshot
    source_at: datetime
    imputed: bool


@dataclass(frozen=True)
class ScanFrame:
    scan_at: datetime
    cells: dict[str, ScanCell]

    @property
    def observed_count(self) -> int:
        return sum(1 for cell in self.cells.values() if not cell.imputed)

    @property
    def imputed_count(self) -> int:
        return sum(1 for cell in self.cells.values() if cell.imputed)

    def snapshots(self) -> dict[str, ObservationSnapshot]:
        return {symbol: cell.snapshot for symbol, cell in self.cells.items()}


@dataclass(frozen=True)
class CandidateRow:
    """A scored candidate at one reconstructed scan, retained for forensics."""

    scan_at: datetime
    symbol: str
    stage: str
    price: float
    imputed_input: bool
    opportunity_score: int
    explosion_potential_score: int
    tradeability_score: int
    pattern_strength_score: int
    volume_acceleration_score: int
    relative_strength_score: int
    persistence_scans: int
    exhaustion_penalty: int
    liquidity_24h_usd_approx: float
    pattern: str | None
    reasons: tuple[str, ...]

    @property
    def is_detection(self) -> bool:
        return self.stage in ELIGIBLE_STAGES

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scan_at"] = self.scan_at.isoformat()
        payload["reasons"] = list(self.reasons)
        return payload


# Backwards-compatible alias: the first draft called these detections.
ReplayDetection = CandidateRow


@dataclass(frozen=True)
class MoveEpisode:
    """One distinct explosive run for one symbol."""

    symbol: str
    baseline_at: datetime
    baseline_price: float
    peak_at: datetime
    peak_price: float
    end_at: datetime
    peak_return_pct: float
    outcome_class: str
    # threshold percent -> first time price closed at or above it
    crossings: dict[float, datetime]
    ohlc_validated: bool = False
    ohlc_peak_return_pct: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "baseline_at": self.baseline_at.isoformat(),
            "baseline_price": self.baseline_price,
            "peak_at": self.peak_at.isoformat(),
            "peak_price": self.peak_price,
            "end_at": self.end_at.isoformat(),
            "peak_return_pct": round(self.peak_return_pct, 4),
            "outcome_class": self.outcome_class,
            "crossings": {str(k): v.isoformat() for k, v in sorted(self.crossings.items())},
            "ohlc_validated": self.ohlc_validated,
            "ohlc_peak_return_pct": self.ohlc_peak_return_pct,
        }


@dataclass(frozen=True)
class DetectionOutcome:
    """A detection judged strictly on what happened after it."""

    detection: CandidateRow
    forward_max_price: float | None
    forward_max_return_pct: float | None
    bucket: str
    window_complete: bool

    @property
    def reached_20(self) -> bool:
        return self.forward_max_return_pct is not None and self.forward_max_return_pct >= 20.0


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class IngestionResult:
    observations: list[ReplayObservation]
    total_lines: int
    rejected_lines: int

    @property
    def symbols(self) -> set[str]:
        return {row.symbol for row in self.observations}


def read_observations(path: Path | None = None) -> IngestionResult:
    """Parse FULL_MARKET_OBSERVATION rows, rejecting malformed ones safely."""
    target = path or DEFAULT_OBSERVATION_FILE
    if not target.exists():
        return IngestionResult(observations=[], total_lines=0, rejected_lines=0)

    rows: list[ReplayObservation] = []
    total = 0
    rejected = 0
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            total += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                rejected += 1
                continue
            if not isinstance(raw, dict) or raw.get("record_type") != "FULL_MARKET_OBSERVATION":
                rejected += 1
                continue
            observed_at = _as_utc(raw.get("observed_at"))
            symbol = str(raw.get("symbol") or "").upper()
            snapshot = snapshot_from_mapping(raw, observed_at=observed_at)
            if observed_at is None or not symbol or snapshot is None:
                rejected += 1
                continue
            if not math.isfinite(snapshot.last_price) or snapshot.last_price <= 0:
                rejected += 1
                continue
            rows.append(ReplayObservation(observed_at=observed_at, symbol=symbol, snapshot=snapshot))
    rows.sort(key=lambda row: (row.observed_at, row.symbol))
    return IngestionResult(observations=rows, total_lines=total, rejected_lines=rejected)


# ---------------------------------------------------------------------------
# Time-correct scan reconstruction
# ---------------------------------------------------------------------------


def _floor_time(moment: datetime, interval_seconds: int) -> datetime:
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % interval_seconds, tz=timezone.utc)


def _retimestamp(snapshot: ObservationSnapshot, scan_at: datetime) -> ObservationSnapshot:
    return ObservationSnapshot(
        observed_at=scan_at,
        last_price=snapshot.last_price,
        volume_24h=snapshot.volume_24h,
        notional_24h_usd_approx=snapshot.notional_24h_usd_approx,
        high_24h=snapshot.high_24h,
        low_24h=snapshot.low_24h,
        lift_from_24h_low_pct=snapshot.lift_from_24h_low_pct,
        distance_from_24h_high_pct=snapshot.distance_from_24h_high_pct,
    )


def reconstruct_scan_frames(
    observations: Iterable[ReplayObservation],
    *,
    interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
    max_carry_seconds: int = DEFAULT_MAX_CARRY_SECONDS,
) -> list[ScanFrame]:
    """Convert event-sampled observations into a regular, past-only scan grid.

    Active periods persist far more JSONL rows than quiet ones. Replaying rows
    directly would hand busy periods extra "scans" and manufacture persistence
    out of event density alone. A fixed grid removes that: the number and
    timestamps of reconstructed scans depend only on elapsed time, never on how
    many events were written.

    At each boundary a symbol carries its latest observation known *at or
    before* that boundary, for a bounded freshness window. The carried snapshot
    is re-timestamped to the scan, because production really did scan then - but
    it is flagged ``imputed`` so stale values are never mistaken for fresh ones.
    A carried value is flat, so it cannot fabricate momentum; it breaks a
    persistence chain rather than extending one.
    """
    rows = sorted(observations, key=lambda row: (row.observed_at, row.symbol))
    if not rows:
        return []
    interval_seconds = max(1, int(interval_seconds))
    horizon_end = _floor_time(rows[-1].observed_at, interval_seconds) + timedelta(seconds=interval_seconds)
    # Start on the first observation's own boundary so an event landing exactly
    # on the grid is recorded as observed rather than immediately carried.
    cursor = _floor_time(rows[0].observed_at, interval_seconds)

    frames: list[ScanFrame] = []
    latest: dict[str, ReplayObservation] = {}
    index = 0
    while cursor <= horizon_end:
        # Admit only events at or before this scan. Nothing later may enter.
        while index < len(rows) and rows[index].observed_at <= cursor:
            latest[rows[index].symbol] = rows[index]
            index += 1
        cells: dict[str, ScanCell] = {}
        for symbol, row in latest.items():
            age = (cursor - row.observed_at).total_seconds()
            if age < 0 or age > max_carry_seconds:
                continue
            cells[symbol] = ScanCell(
                snapshot=_retimestamp(row.snapshot, cursor),
                source_at=row.observed_at,
                imputed=row.observed_at != cursor,
            )
        frames.append(ScanFrame(scan_at=cursor, cells=cells))
        cursor = cursor + timedelta(seconds=interval_seconds)
    return frames


# ---------------------------------------------------------------------------
# Replay of the exact Phase 1 pipeline
# ---------------------------------------------------------------------------


def replay_signal_quality(
    frames: Iterable[ScanFrame],
    *,
    config: Phase2Config | None = None,
    retain_audit_for: set[str] | None = None,
) -> tuple[list[CandidateRow], list[CandidateRow]]:
    """Re-run the production Phase 1 feature and scoring functions on the grid.

    Returns ``(detections, audit_rows)``. Detections are candidates that reached
    an alerting stage. Audit rows are the full scored universe - suppressed rows
    included - retained only for the symbols named in ``retain_audit_for``, so
    missed-winner forensics are possible without holding the entire scored
    history of every market in memory.

    No alternate scorer exists here: ``derive_features_for_universe``,
    ``derive_universe_percentiles`` and ``evaluate_universe`` are the same
    functions production calls.
    """
    config = config or Phase2Config()
    feature_config = FeatureDerivationConfig(
        nominal_interval_seconds=float(config.scan_interval_seconds),
        continuity_multiplier=config.continuity_multiplier,
        qualifying=QualifyingConditions(),
        run_up_window_scans=config.history_scans,
    )
    retain = retain_audit_for or set()
    retention_window = timedelta(seconds=max(config.max_carry_seconds, 1))

    history: dict[str, deque[ObservationSnapshot]] = defaultdict(
        lambda: deque(maxlen=max(2, config.history_scans))
    )
    last_seen: dict[str, datetime] = {}
    detections: list[CandidateRow] = []
    audit_rows: list[CandidateRow] = []

    for frame in frames:
        for symbol, cell in frame.cells.items():
            history[symbol].append(cell.snapshot)
            last_seen[symbol] = frame.scan_at

        # Mirror production retention: a symbol unobserved past the retention
        # window loses its history entirely.
        stale = [
            symbol for symbol, seen in last_seen.items()
            if frame.scan_at - seen > retention_window
        ]
        for symbol in stale:
            history.pop(symbol, None)
            last_seen.pop(symbol, None)

        # Only symbols present in this scan are scored, exactly as production
        # does - retained history is for when a symbol returns, never for
        # ranking stale snapshots as though they were current.
        universe = {
            symbol: list(rows)
            for symbol, rows in history.items()
            if symbol in frame.cells
        }
        if not universe:
            continue
        features = derive_features_for_universe(universe, config=feature_config)
        if not features:
            continue
        percentiles = derive_universe_percentiles(features)

        for candidate in evaluate_universe(features, percentiles, config=config.scoring):
            cell = frame.cells.get(candidate.symbol)
            if cell is None or cell.snapshot.last_price <= 0:
                continue
            row = CandidateRow(
                scan_at=frame.scan_at,
                symbol=candidate.symbol,
                stage=candidate.stage,
                price=cell.snapshot.last_price,
                imputed_input=cell.imputed,
                opportunity_score=candidate.opportunity_score,
                explosion_potential_score=candidate.explosion_potential_score,
                tradeability_score=candidate.tradeability_score,
                pattern_strength_score=candidate.pattern_strength_score,
                volume_acceleration_score=candidate.volume_acceleration_score,
                relative_strength_score=candidate.relative_strength_score,
                persistence_scans=candidate.persistence_scans,
                exhaustion_penalty=candidate.exhaustion_penalty,
                liquidity_24h_usd_approx=candidate.liquidity_24h_usd_approx,
                pattern=candidate.pattern,
                reasons=tuple(candidate.reasons),
            )
            if row.is_detection:
                detections.append(row)
            if candidate.symbol in retain:
                audit_rows.append(row)
    return detections, audit_rows


# ---------------------------------------------------------------------------
# Forward price index (post-detection outcome evaluation)
# ---------------------------------------------------------------------------


class SymbolTimeline:
    """Sorted observed prices for one symbol, with O(1) amortised forward maxima.

    Every forward query is strictly exclusive of its own timestamp: the maximum
    is taken over ``(t, t + horizon]``. That exclusivity is what makes a
    detection's credit depend only on what happened after it.
    """

    __slots__ = ("times", "prices", "_epochs")

    def __init__(self, rows: Sequence[ReplayObservation]) -> None:
        ordered = sorted(rows, key=lambda row: row.observed_at)
        self.times: list[datetime] = [row.observed_at for row in ordered]
        self.prices: list[float] = [row.snapshot.last_price for row in ordered]
        self._epochs: list[float] = [row.observed_at.timestamp() for row in ordered]

    def __len__(self) -> int:
        return len(self.times)

    @property
    def last_at(self) -> datetime | None:
        return self.times[-1] if self.times else None

    def forward_maxima(
        self,
        query_times: Sequence[datetime],
        horizon: timedelta,
    ) -> list[float | None]:
        """Sliding-window maxima for ascending query times. O(n + m) total.

        A monotonic deque replaces the naive "scan all future rows for every
        row" approach, which is quadratic and unusable on production history.
        """
        if not query_times:
            return []
        horizon_seconds = horizon.total_seconds()
        window: deque[int] = deque()
        result: list[float | None] = []
        head = 0  # next index to admit
        for moment in query_times:
            start = moment.timestamp()
            limit = start + horizon_seconds
            while head < len(self._epochs) and self._epochs[head] <= limit:
                while window and self.prices[window[-1]] <= self.prices[head]:
                    window.pop()
                window.append(head)
                head += 1
            # Strictly after the query time.
            while window and self._epochs[window[0]] <= start:
                window.popleft()
            result.append(self.prices[window[0]] if window else None)
        return result

    def has_complete_window(self, moment: datetime, horizon: timedelta) -> bool:
        """Is the full forward horizon actually covered by observed data?"""
        if not self.times:
            return False
        return self.times[-1] >= moment + horizon

    def index_before(self, moment: datetime) -> int | None:
        """Index of the last observation strictly before ``moment``."""
        position = bisect_left(self._epochs, moment.timestamp())
        return position - 1 if position > 0 else None

    def slice_indices(self, start: datetime, end: datetime) -> tuple[int, int]:
        """Half-open index range covering ``[start, end]`` inclusive."""
        lo = bisect_left(self._epochs, start.timestamp())
        hi = bisect_right(self._epochs, end.timestamp())
        return lo, hi


def measure_persistence_gap_drift(
    observations: Sequence[ReplayObservation],
    frames: Sequence[ScanFrame],
) -> dict[str, Any]:
    """Distance from each carried value to the NEXT PERSISTED observation.

    **This is not the reconstruction error.** It cannot be. The true error of a
    carried value is its distance from the contemporaneous market price at that
    scan, and that price was never recorded - if it had been, there would be
    nothing to carry. Point-in-time reconstruction error is unknowable without
    a live scan log.

    What this measures instead is the gap to the next persisted row. That row
    exists *because* the market moved past a persist threshold, so the measured
    gap bundles together two different things: how stale the carried value was,
    and how much the market moved after the scan in question. It therefore
    tends to **overstate** carry error, and normal later movement can appear
    here as though it were reconstruction distortion.

    It is still worth reporting as an upper-bound-flavoured uncertainty proxy:
    a small distribution is genuine reassurance, while a large one is a reason
    to distrust reconstructed features. The defensible statement remains the
    theoretical one - a row is written because something moved, so silence
    between rows means price moved less than ``CARRY_PRICE_DRIFT_BOUND_PCT``,
    with the hourly heartbeat as the exception.
    """
    by_symbol: dict[str, list[ReplayObservation]] = defaultdict(list)
    for row in observations:
        by_symbol[row.symbol].append(row)
    epochs: dict[str, list[float]] = {}
    prices: dict[str, list[float]] = {}
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda row: row.observed_at)
        epochs[symbol] = [row.observed_at.timestamp() for row in rows]
        prices[symbol] = [row.snapshot.last_price for row in rows]

    drifts: list[float] = []
    ages: list[float] = []
    exceeded_bound = 0
    unresolved = 0
    for frame in frames:
        for symbol, cell in frame.cells.items():
            if not cell.imputed:
                continue
            ages.append((frame.scan_at - cell.source_at).total_seconds())
            series = epochs.get(symbol)
            if not series:
                unresolved += 1
                continue
            nxt = bisect_right(series, frame.scan_at.timestamp())
            if nxt >= len(series):
                # No later observation: the drift is unknowable, not zero.
                unresolved += 1
                continue
            carried = cell.snapshot.last_price
            actual = prices[symbol][nxt]
            if carried <= 0:
                unresolved += 1
                continue
            drift = abs(actual / carried - 1.0) * 100.0
            drifts.append(drift)
            if drift > CARRY_PRICE_DRIFT_BOUND_PCT:
                exceeded_bound += 1

    return {
        "metric": "drift_to_next_persisted_observation",
        "is_point_in_time_reconstruction_error": False,
        "interpretation": (
            "Distance from each carried value to the next PERSISTED observation, "
            "not to the true contemporaneous price at that scan. The next row "
            "exists because the market moved past a persist threshold, so this "
            "bundles carry staleness with genuine post-scan movement and tends "
            "to overstate reconstruction error. True point-in-time error is "
            "unknowable without a live scan log."
        ),
        "imputed_cells_measured": len(drifts),
        "imputed_cells_unresolved": unresolved,
        "theoretical_price_drift_bound_pct": CARRY_PRICE_DRIFT_BOUND_PCT,
        "bound_rationale": (
            "_should_persist writes a row when price moves at least "
            f"{CARRY_PRICE_DRIFT_BOUND_PCT}%, lift {CARRY_LIFT_DRIFT_BOUND_PCT}%, "
            f"high-distance {CARRY_HIGH_DISTANCE_DRIFT_BOUND_PCT}%, or notional "
            f"{CARRY_NOTIONAL_RATIO_BOUND}x; the absence of a row is therefore "
            "evidence the market was quiet, not evidence of missing data. The "
            f"{CARRY_HEARTBEAT_SECONDS}s heartbeat is the exception."
        ),
        "drift_to_next_persisted_observation_pct": _quartiles(drifts),
        "exceeded_bound": exceeded_bound,
        "exceeded_bound_pct": (
            round(exceeded_bound / len(drifts) * 100.0, 2) if drifts else None
        ),
        "exceeded_bound_caveat": (
            "Exceeding the bound does not prove the carried value was wrong at "
            "the scan; the market may simply have moved afterwards, which is "
            "what triggered the next persisted row."
        ),
        "carry_age_seconds": _quartiles(ages),
    }


def episode_sensitivity(
    timelines: Mapping[str, SymbolTimeline],
    *,
    base: EpisodeConfig,
    triggers: Sequence[float] = (15.0, 20.0, 30.0),
    retraces: Sequence[float] = (20.0, 30.0, 50.0),
) -> list[dict[str, Any]]:
    """Report how episode counts move as the episode priors move.

    The trigger and retrace values are priors, not calibrated figures, and they
    directly determine how many "winners" exist to be captured. A capture rate
    quoted without this sweep is not interpretable: if halving the trigger
    doubles the episode count, the headline rate is an artefact of the
    parameter rather than a property of the detector.
    """
    rows: list[dict[str, Any]] = []
    for trigger in triggers:
        for retrace in retraces:
            variant = EpisodeConfig(
                trigger_pct=trigger,
                close_retrace_pct=retrace,
                baseline_window_hours=base.baseline_window_hours,
                peak_cooldown_hours=base.peak_cooldown_hours,
            )
            episodes = build_all_episodes(timelines, config=variant)
            rows.append({
                "trigger_pct": trigger,
                "close_retrace_pct": retrace,
                "episodes": len(episodes),
                "episodes_by_class": dict(sorted(Counter(e.outcome_class for e in episodes).items())),
                "is_default": trigger == base.trigger_pct and retrace == base.close_retrace_pct,
            })
    return rows


def build_timelines(observations: Iterable[ReplayObservation]) -> dict[str, SymbolTimeline]:
    grouped: dict[str, list[ReplayObservation]] = defaultdict(list)
    for row in observations:
        grouped[row.symbol].append(row)
    return {symbol: SymbolTimeline(rows) for symbol, rows in grouped.items()}


# ---------------------------------------------------------------------------
# Episode model
# ---------------------------------------------------------------------------


def _outcome_class(move_pct: float) -> str:
    """Exclusive class for an episode peak.

    Uses EXCLUSIVE_CLASS_BANDS so an episode's own class label and the
    exclusive-class metrics share one vocabulary. Previously this returned
    "MOVE_50" for a +60% episode while the metrics block used "MOVE_50" to mean
    "+50% or more" - two different meanings behind one name.
    """
    for name, low, high in EXCLUSIVE_CLASS_BANDS:
        if low <= move_pct < high:
            return name
    return "NO_MAJOR_MOVE"


def build_episodes(
    timeline: SymbolTimeline,
    symbol: str,
    *,
    config: EpisodeConfig,
) -> list[MoveEpisode]:
    """Collapse one symbol's price history into distinct major-move episodes.

    The model is a baseline-trigger-peak-reset cycle:

    1. A running **baseline** tracks the lowest price within a trailing window.
    2. An episode **opens** the first time price rises ``trigger_pct`` above
       that baseline. The baseline low and its timestamp anchor the episode.
    3. The episode **runs** while price makes new highs, tracking the peak.
    4. It **closes** when price retraces ``close_retrace_pct`` from the peak,
       when the peak goes stale for ``peak_cooldown_hours``, or at end of data.
    5. The baseline **resets** to the lowest price after the peak, so the next
       episode requires a genuine new advance from a new low.

    A single continuous run therefore yields exactly one episode, no matter how
    many rows the event-sampled stream wrote during it. Without this, a DENT-
    style move produces hundreds of overlapping "+20% windows" and every
    capture rate is inflated by the density of the move itself.
    """
    n = len(timeline)
    if n < 2:
        return []

    prices = timeline.prices
    times = timeline.times
    baseline_window = timedelta(hours=config.baseline_window_hours)
    cooldown = timedelta(hours=config.peak_cooldown_hours)

    episodes: list[MoveEpisode] = []
    index = 1
    # Indices of candidate baseline lows within the trailing window, increasing
    # in price - a monotonic deque keeps the running minimum O(1) amortised.
    lows: deque[int] = deque([0])

    while index < n:
        # Expire baselines that fell out of the trailing window.
        while lows and times[index] - times[lows[0]] > baseline_window:
            lows.popleft()
        if not lows:
            lows.append(index - 1)

        baseline_idx = lows[0]
        baseline_price = prices[baseline_idx]
        rise = (prices[index] / baseline_price - 1.0) * 100.0 if baseline_price > 0 else 0.0

        if rise < config.trigger_pct:
            while lows and prices[lows[-1]] >= prices[index]:
                lows.pop()
            lows.append(index)
            index += 1
            continue

        # Episode opens. Walk forward to the peak and the close.
        peak_idx = index
        cursor = index
        while cursor < n:
            if prices[cursor] > prices[peak_idx]:
                peak_idx = cursor
            elif prices[peak_idx] > 0:
                retrace = (1.0 - prices[cursor] / prices[peak_idx]) * 100.0
                if retrace >= config.close_retrace_pct:
                    break
                if times[cursor] - times[peak_idx] > cooldown:
                    break
            cursor += 1
        end_idx = min(cursor, n - 1)

        peak_return = (prices[peak_idx] / baseline_price - 1.0) * 100.0
        crossings: dict[float, datetime] = {}
        for threshold in sorted(set(OUTCOME_THRESHOLDS) | set(FORENSIC_THRESHOLDS)):
            target = baseline_price * (1.0 + threshold / 100.0)
            for probe in range(baseline_idx, end_idx + 1):
                if prices[probe] >= target:
                    crossings[threshold] = times[probe]
                    break

        episodes.append(MoveEpisode(
            symbol=symbol,
            baseline_at=times[baseline_idx],
            baseline_price=baseline_price,
            peak_at=times[peak_idx],
            peak_price=prices[peak_idx],
            end_at=times[end_idx],
            peak_return_pct=peak_return,
            outcome_class=_outcome_class(peak_return),
            crossings=crossings,
        ))

        # Reset: the next episode must build from a new low after this peak.
        lows.clear()
        reset_idx = peak_idx
        for probe in range(peak_idx, end_idx + 1):
            if prices[probe] < prices[reset_idx]:
                reset_idx = probe
        lows.append(reset_idx)
        index = max(end_idx + 1, reset_idx + 1, index + 1)

    return episodes


def build_all_episodes(
    timelines: Mapping[str, SymbolTimeline],
    *,
    config: EpisodeConfig,
) -> list[MoveEpisode]:
    episodes: list[MoveEpisode] = []
    for symbol, timeline in timelines.items():
        episodes.extend(build_episodes(timeline, symbol, config=config))
    episodes.sort(key=lambda row: (row.baseline_at, row.symbol))
    return episodes


# ---------------------------------------------------------------------------
# Detection evaluation (strictly post-detection)
# ---------------------------------------------------------------------------


def evaluate_detections(
    detections: Sequence[CandidateRow],
    timelines: Mapping[str, SymbolTimeline],
    *,
    horizon: timedelta,
) -> list[DetectionOutcome]:
    """Judge every detection on price movement strictly after its timestamp.

    This is the correction at the heart of Phase 2. A detection's forward
    return is measured from its own price at its own time; a move that
    completed before the detection existed contributes nothing to its credit.
    """
    by_symbol: dict[str, list[CandidateRow]] = defaultdict(list)
    for row in detections:
        by_symbol[row.symbol].append(row)

    outcomes: list[DetectionOutcome] = []
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda row: row.scan_at)
        timeline = timelines.get(symbol)
        if timeline is None or not len(timeline):
            for row in rows:
                outcomes.append(DetectionOutcome(row, None, None, "UNKNOWN", False))
            continue
        maxima = timeline.forward_maxima([row.scan_at for row in rows], horizon)
        for row, forward_max in zip(rows, maxima):
            if forward_max is None or row.price <= 0:
                outcomes.append(DetectionOutcome(
                    row, None, None, "UNKNOWN",
                    timeline.has_complete_window(row.scan_at, horizon),
                ))
                continue
            forward_return = (forward_max / row.price - 1.0) * 100.0
            outcomes.append(DetectionOutcome(
                detection=row,
                forward_max_price=forward_max,
                forward_max_return_pct=forward_return,
                bucket=_forward_bucket(forward_return),
                window_complete=timeline.has_complete_window(row.scan_at, horizon),
            ))
    outcomes.sort(key=lambda row: (row.detection.scan_at, row.detection.symbol))
    return outcomes


def _forward_bucket(forward_return_pct: float) -> str:
    for name, low, high in FORWARD_BUCKETS:
        if low <= forward_return_pct < high:
            return name
    return FORWARD_BUCKETS[-1][0]


@dataclass(frozen=True)
class EpisodeDetectionResult:
    episode: MoveEpisode
    first_detection: CandidateRow | None
    detected_before: dict[float, bool]
    lead_minutes_to_peak: float | None
    move_completed_fraction_pct: float | None


def evaluate_episode_detection(
    episode: MoveEpisode,
    detections_for_symbol: Sequence[CandidateRow],
) -> EpisodeDetectionResult:
    """Determine whether - and how early - OHM saw this episode coming.

    Two rules make "early" mean early:

    * A detection counts for this episode only if it lands in
      ``[baseline_at, peak_at)``. A detection after the peak is not a
      prediction of it.
    * "Detected before +X" requires the detection to be *strictly earlier* than
      the episode's actual +X crossing. Sharing a labelled window with the move
      is not evidence of anticipating it.
    """
    in_episode = [
        row for row in detections_for_symbol
        if episode.baseline_at <= row.scan_at < episode.peak_at
    ]
    first = min(in_episode, key=lambda row: row.scan_at) if in_episode else None

    detected_before: dict[float, bool] = {}
    for threshold, crossed_at in episode.crossings.items():
        detected_before[threshold] = any(
            episode.baseline_at <= row.scan_at < crossed_at
            for row in detections_for_symbol
        )

    lead_minutes = None
    completed = None
    if first is not None:
        lead_minutes = (episode.peak_at - first.scan_at).total_seconds() / 60.0
        span = episode.peak_price - episode.baseline_price
        if span > 0:
            raw = (first.price - episode.baseline_price) / span
            completed = max(0.0, min(1.0, raw)) * 100.0
    return EpisodeDetectionResult(
        episode=episode,
        first_detection=first,
        detected_before=detected_before,
        lead_minutes_to_peak=lead_minutes,
        move_completed_fraction_pct=completed,
    )


# ---------------------------------------------------------------------------
# OHLC cross-validation (read-only, public endpoint only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OhlcCandle:
    start_at: datetime
    high: float
    low: float
    close: float


class OhlcProvider(Protocol):
    """Read-only historical OHLC source, used for outcomes only.

    Never consulted during feature generation, so it cannot leak future
    information into a decision. It exists to catch intraperiod peaks the
    event-sampled JSONL stream missed.
    """

    def fetch(self, symbol: str, start_at: datetime, end_at: datetime) -> list[OhlcCandle]:
        ...


class NullOhlcProvider:
    """Default provider: validates nothing and says so."""

    def fetch(self, symbol: str, start_at: datetime, end_at: datetime) -> list[OhlcCandle]:
        return []


class KrakenPublicOhlcProvider:
    """Public Kraken OHLC. Never touches a private or trading endpoint.

    Not used by unit tests - CI must stay deterministic and offline - so this
    is only exercised when an operator explicitly passes it in.
    """

    def __init__(self, client: Any = None, *, interval_minutes: int = 15) -> None:
        self._client = client
        self.interval_minutes = interval_minutes

    def _resolve(self) -> Any:
        if self._client is None:
            from app.exchanges.kraken import KrakenClient

            self._client = KrakenClient()
        return self._client

    def fetch(self, symbol: str, start_at: datetime, end_at: datetime) -> list[OhlcCandle]:
        try:
            candles = self._resolve().get_ohlc(
                symbol,
                interval=self.interval_minutes,
                since=int(start_at.timestamp()),
            )
        except Exception:
            # Outcome validation is best-effort; it must never break a report.
            return []
        rows: list[OhlcCandle] = []
        for candle in candles:
            moment = datetime.fromtimestamp(int(candle.timestamp), tz=timezone.utc)
            if moment < start_at or moment > end_at:
                continue
            rows.append(OhlcCandle(
                start_at=moment,
                high=float(candle.high),
                low=float(candle.low),
                close=float(candle.close),
            ))
        return rows


class CachedOhlcProvider:
    """OHLC read from a local JSONL cache. Offline and deterministic.

    Lets an operator fetch candles once (see ``write_ohlc_cache``) and then
    re-run validation as often as they like without touching the network, so a
    validated report is reproducible rather than depending on whatever the
    exchange returns that minute.

    Expected row shape, one JSON object per line:
    ``{"symbol": "XBTUSD", "start_at": "...ISO8601...", "high": 1.0,
       "low": 1.0, "close": 1.0}``
    """

    def __init__(self, path: Path) -> None:
        self._by_symbol: dict[str, list[OhlcCandle]] = defaultdict(list)
        self.rejected_rows = 0
        self.duplicate_rows = 0
        if not path.exists():
            return
        seen: set[tuple[str, datetime]] = set()
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    self.rejected_rows += 1
                    continue
                if not isinstance(raw, dict):
                    self.rejected_rows += 1
                    continue
                moment = _as_utc(raw.get("start_at"))
                symbol = str(raw.get("symbol") or "").upper()
                if moment is None or not symbol:
                    self.rejected_rows += 1
                    continue
                try:
                    candle = OhlcCandle(
                        start_at=moment,
                        high=float(raw["high"]),
                        low=float(raw["low"]),
                        close=float(raw["close"]),
                    )
                except (KeyError, TypeError, ValueError):
                    self.rejected_rows += 1
                    continue
                if not math.isfinite(candle.high) or candle.high <= 0:
                    self.rejected_rows += 1
                    continue
                key = (symbol, moment)
                if key in seen:
                    self.duplicate_rows += 1
                    continue
                seen.add(key)
                self._by_symbol[symbol].append(candle)
        for rows in self._by_symbol.values():
            rows.sort(key=lambda row: row.start_at)

    def fetch(self, symbol: str, start_at: datetime, end_at: datetime) -> list[OhlcCandle]:
        return [
            candle for candle in self._by_symbol.get(symbol.upper(), ())
            if start_at <= candle.start_at <= end_at
        ]


class OhlcCacheTargetError(RuntimeError):
    """Refused to write a cache over a file that is not an OHLC cache."""


def _is_ohlc_cache_file(path: Path) -> bool:
    """Does this existing file already look like an OHLC cache?"""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                return isinstance(row, dict) and {"symbol", "start_at", "high"} <= set(row)
    except (OSError, json.JSONDecodeError):
        return False
    return True  # an empty file is safe to claim


def write_ohlc_cache(
    episodes: Sequence[MoveEpisode],
    provider: OhlcProvider,
    path: Path,
) -> int:
    """Fetch candles for each episode once and persist them for reuse.

    The only function in this module that writes a file. Two guards make that
    safe: it refuses to truncate an existing file that is not already an OHLC
    cache - a mistyped path aimed at a production registry raises rather than
    destroying it - and it deduplicates candles by ``(symbol, start_at)``,
    because overlapping episodes on one symbol otherwise write the same candle
    repeatedly and inflate the cache.
    """
    if path.exists() and not _is_ohlc_cache_file(path):
        raise OhlcCacheTargetError(
            f"Refusing to overwrite {path}: it exists and does not look like an "
            "OHLC cache. Choose a new path rather than truncating an existing file."
        )

    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for episode in episodes:
        symbol = episode.symbol.upper()
        for candle in provider.fetch(episode.symbol, episode.baseline_at, episode.end_at):
            stamp = candle.start_at.astimezone(timezone.utc).isoformat()
            key = (symbol, stamp)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "symbol": symbol,
                "start_at": stamp,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
            })

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)


def build_ohlc_validation_block(
    episodes: Sequence[MoveEpisode],
    *,
    provider_name: str,
) -> dict[str, Any]:
    """State exactly what OHLC coverage does and does not establish.

    Deliberately verbose. The temptation is to reduce this to one boolean and
    let it flip the report status - which would announce that capture rates,
    threshold timings and class assignments had been cross-validated when none
    of them were touched. Coverage, peak deltas, and the per-metric validation
    lists are published instead so a reader can see the limit for themselves.
    """
    covered = [row for row in episodes if row.ohlc_validated]
    coverage_pct = round(len(covered) / len(episodes) * 100.0, 2) if episodes else None

    if not covered:
        status = OHLC_STATUS_NONE
    elif len(covered) == len(episodes):
        status = OHLC_STATUS_COMPLETE_PEAK
    else:
        status = OHLC_STATUS_PARTIAL_PEAK

    deltas = [
        row.ohlc_peak_return_pct - row.peak_return_pct
        for row in covered
        if row.ohlc_peak_return_pct is not None
    ]
    understated = sum(1 for delta in deltas if delta > 0.0)

    return {
        "status": status,
        "provider": provider_name,
        "episodes_requested": len(episodes),
        "episodes_with_candles": len(covered),
        "coverage_pct": coverage_pct,
        "event_sampled_peak_vs_ohlc_peak_delta_pct": _quartiles(deltas),
        "episodes_where_event_sampling_understated_the_peak": understated,
        "fully_validated_metrics": list(OHLC_FULLY_VALIDATED_METRICS),
        "partially_validated_metrics": list(OHLC_PARTIALLY_VALIDATED_METRICS),
        "not_validated_metrics": list(OHLC_NOT_VALIDATED_METRICS),
        "note": (
            "OHLC highs are compared against event-sampled peaks; they do not "
            "replace peak_at, outcome_class or threshold crossings, so no "
            "capture rate or timing metric here is OHLC-validated. The report "
            "status stays provisional regardless of coverage."
        ),
    }


def validate_episodes_with_ohlc(
    episodes: Sequence[MoveEpisode],
    provider: OhlcProvider,
) -> list[MoveEpisode]:
    """Attach an OHLC peak comparison to each episode.

    Outcome-side only, and deliberately non-destructive: ``peak_return_pct``,
    ``peak_at``, ``outcome_class`` and ``crossings`` are left exactly as the
    event-sampled construction produced them. Both peaks are carried so
    under-counting becomes visible rather than being silently corrected, and so
    no downstream metric can quietly start mixing close-based episode
    construction with high-based labels.
    """
    validated: list[MoveEpisode] = []
    for episode in episodes:
        candles = provider.fetch(episode.symbol, episode.baseline_at, episode.end_at)
        if not candles or episode.baseline_price <= 0:
            validated.append(episode)
            continue
        highest = max(candle.high for candle in candles)
        ohlc_return = (highest / episode.baseline_price - 1.0) * 100.0
        validated.append(MoveEpisode(
            symbol=episode.symbol,
            baseline_at=episode.baseline_at,
            baseline_price=episode.baseline_price,
            peak_at=episode.peak_at,
            peak_price=episode.peak_price,
            end_at=episode.end_at,
            peak_return_pct=episode.peak_return_pct,
            outcome_class=episode.outcome_class,
            crossings=episode.crossings,
            ohlc_validated=True,
            ohlc_peak_return_pct=round(ohlc_return, 4),
        ))
    return validated


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _median(values: Sequence[float]) -> float | None:
    finite = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return round(median(finite), 4) if finite else None


def _quartiles(values: Sequence[float]) -> dict[str, float | None]:
    finite = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not finite:
        return {"count": 0, "p25": None, "median": None, "p75": None}
    def _at(fraction: float) -> float:
        position = min(len(finite) - 1, max(0, int(round(fraction * (len(finite) - 1)))))
        return round(finite[position], 4)
    return {
        "count": len(finite),
        "p25": _at(0.25),
        "median": _at(0.5),
        "p75": _at(0.75),
    }


def _liquidity_band(value: float) -> str:
    for name, low, high in LIQUIDITY_BANDS:
        if low <= value < high:
            return name
    return LIQUIDITY_BANDS[-1][0]


def _score_bucket(score: int, width: int = 10) -> str:
    low = int(score) // width * width
    return f"{low}-{min(100, low + width - 1)}"


def _precision_table(
    outcomes: Sequence[DetectionOutcome],
    key_fn,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[DetectionOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[str(key_fn(outcome))].append(outcome)
    table: dict[str, dict[str, Any]] = {}
    for key, rows in sorted(grouped.items()):
        judged = [row for row in rows if row.forward_max_return_pct is not None]
        if not judged:
            table[key] = {"detections": len(rows), "judged": 0, "precision_plus_20_pct": None}
            continue
        hits = sum(row.reached_20 for row in judged)
        table[key] = {
            "detections": len(rows),
            "judged": len(judged),
            "reached_plus_20": hits,
            "precision_plus_20_pct": round(hits / len(judged) * 100.0, 2),
        }
    return table


def chronological_split(
    moments: Sequence[datetime],
    fraction: float,
) -> datetime | None:
    """Time cutoff separating calibration from validation.

    Chronological by construction: a random split across adjacent scans would
    put near-identical decisions on both sides and make the held-out set
    meaningless.
    """
    ordered = sorted(moments)
    if not ordered:
        return None
    fraction = min(0.95, max(0.05, float(fraction)))
    position = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[position]


# ---------------------------------------------------------------------------
# Missed-winner forensics
# ---------------------------------------------------------------------------


def missed_winner_snapshots(
    episode: MoveEpisode,
    audit_rows: Sequence[CandidateRow],
) -> dict[str, Any]:
    """What did OHM know just before each threshold of a missed episode?

    For every forensic mark, capture the last scored row strictly before the
    crossing. This is the report that explains *why* a winner was missed:
    which score fell short, which reason code fired, which stage it sat in.
    """
    rows = sorted(
        (row for row in audit_rows if row.symbol == episode.symbol),
        key=lambda row: row.scan_at,
    )
    marks: dict[str, Any] = {}
    for threshold in FORENSIC_THRESHOLDS:
        crossed_at = episode.crossings.get(threshold)
        if crossed_at is None:
            continue
        prior = [row for row in rows if episode.baseline_at <= row.scan_at < crossed_at]
        if not prior:
            marks[f"before_plus_{int(threshold)}"] = None
            continue
        latest = prior[-1]
        marks[f"before_plus_{int(threshold)}"] = {
            "scan_at": latest.scan_at.isoformat(),
            "stage": latest.stage,
            "pattern": latest.pattern,
            "pattern_strength_score": latest.pattern_strength_score,
            "tradeability_score": latest.tradeability_score,
            "explosion_potential_score": latest.explosion_potential_score,
            "opportunity_score": latest.opportunity_score,
            "relative_strength_score": latest.relative_strength_score,
            "volume_acceleration_score": latest.volume_acceleration_score,
            "persistence_scans": latest.persistence_scans,
            "exhaustion_penalty": latest.exhaustion_penalty,
            "liquidity_24h_usd_approx": latest.liquidity_24h_usd_approx,
            "reasons": list(latest.reasons),
            "imputed_input": latest.imputed_input,
        }
    return {
        "symbol": episode.symbol,
        "outcome_class": episode.outcome_class,
        "peak_return_pct": round(episode.peak_return_pct, 4),
        "baseline_at": episode.baseline_at.isoformat(),
        "peak_at": episode.peak_at.isoformat(),
        "marks": marks,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

FEATURE_COMPARISON_FIELDS = (
    "explosion_potential_score",
    "opportunity_score",
    "pattern_strength_score",
    "tradeability_score",
    "relative_strength_score",
    "volume_acceleration_score",
    "persistence_scans",
    "exhaustion_penalty",
    "liquidity_24h_usd_approx",
)


def _cohort_metrics(
    results: Sequence[EpisodeDetectionResult],
    *,
    low: float,
    high: float = float("inf"),
) -> dict[str, Any]:
    """Detection metrics for episodes whose peak lies in [low, high).

    One implementation serves both views: the cumulative cohorts pass
    ``high=inf``, the exclusive bands pass a real upper edge.
    """
    eligible = [
        row for row in results
        if low <= row.episode.peak_return_pct < high
    ]
    if not eligible:
        return {
            "episodes": 0,
            "detected_before_peak": 0,
            "early_capture_rate_pct": None,
        }
    detected = [row for row in eligible if row.first_detection is not None]
    payload: dict[str, Any] = {
        "episodes": len(eligible),
        "detected_before_peak": len(detected),
        "early_capture_rate_pct": round(len(detected) / len(eligible) * 100.0, 2),
        "median_lead_minutes_to_peak": _median([row.lead_minutes_to_peak for row in detected]),
        "median_move_completed_at_first_detection_pct": _median(
            [row.move_completed_fraction_pct for row in detected]
        ),
        "missed_episodes": len(eligible) - len(detected),
    }
    for mark in (5.0, 10.0, 20.0):
        with_crossing = [row for row in eligible if mark in row.episode.crossings]
        hits = sum(row.detected_before.get(mark, False) for row in with_crossing)
        payload[f"detected_before_plus_{int(mark)}"] = hits
        payload[f"detected_before_plus_{int(mark)}_rate_pct"] = (
            round(hits / len(with_crossing) * 100.0, 2) if with_crossing else None
        )
    if detected:
        payload["first_detection_profile"] = {
            "stage_counts": dict(Counter(row.first_detection.stage for row in detected)),
            "explosion_potential": _quartiles([row.first_detection.explosion_potential_score for row in detected]),
            "opportunity": _quartiles([row.first_detection.opportunity_score for row in detected]),
            "liquidity_24h_usd_approx": _quartiles([row.first_detection.liquidity_24h_usd_approx for row in detected]),
            "persistence_scans": _quartiles([row.first_detection.persistence_scans for row in detected]),
            "exhaustion_penalty": _quartiles([row.first_detection.exhaustion_penalty for row in detected]),
        }
    return payload


def build_phase2_report(
    ingestion: IngestionResult,
    frames: Sequence[ScanFrame],
    detections: Sequence[CandidateRow],
    audit_rows: Sequence[CandidateRow],
    episodes: Sequence[MoveEpisode],
    outcomes: Sequence[DetectionOutcome],
    *,
    config: Phase2Config,
    ohlc_validation: dict[str, Any] | None = None,
    carry_fidelity: dict[str, Any] | None = None,
    sensitivity: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    observations = ingestion.observations
    warnings: list[str] = []

    detections_by_symbol: dict[str, list[CandidateRow]] = defaultdict(list)
    for row in detections:
        detections_by_symbol[row.symbol].append(row)
    for rows in detections_by_symbol.values():
        rows.sort(key=lambda row: row.scan_at)

    results = [
        evaluate_episode_detection(episode, detections_by_symbol.get(episode.symbol, ()))
        for episode in episodes
    ]

    major = [row for row in results if row.episode.peak_return_pct >= OUTCOME_THRESHOLDS[0]]

    # Cumulative: "peaked at or above X". Overlapping by construction, so the
    # keys say GE_ rather than MOVE_ - a +320% episode appears in all five.
    threshold_cohorts = {
        f"GE_{int(threshold)}": _cohort_metrics(results, low=threshold)
        for threshold in OUTCOME_THRESHOLDS
    }
    # Exclusive: each episode appears exactly once.
    exclusive_classes = {
        name: _cohort_metrics(results, low=low, high=high)
        for name, low, high in EXCLUSIVE_CLASS_BANDS
    }

    # The precision population requires BOTH a measurable forward return and a
    # fully covered forward horizon. Requiring only the former let detections
    # near the right edge of the dataset - which have a future print but not a
    # complete window - enter every precision, failure and split denominator,
    # biasing precision downward purely because the data ran out. Incomplete
    # rows stay visible in their own cohort; they are reported, never silently
    # discarded, and never counted.
    judged = [
        row for row in outcomes
        if row.forward_max_return_pct is not None and row.window_complete
    ]
    incomplete = [row for row in outcomes if not row.window_complete]
    unmeasurable = [
        row for row in outcomes
        if row.window_complete and row.forward_max_return_pct is None
    ]
    # Bucket the judged population only; incomplete rows get their own count so
    # the two are never summed together by a reader.
    bucket_counts = Counter(row.bucket for row in judged)
    incomplete_bucket_counts = Counter(row.bucket for row in incomplete)

    # Winner vs failed-breakout: both groups judged from their own timestamps.
    winners = [
        row for row in judged
        if row.forward_max_return_pct is not None and row.forward_max_return_pct >= 20.0
    ]
    failures = [
        row for row in judged
        if row.forward_max_return_pct is not None and row.forward_max_return_pct < 5.0
    ]
    comparison = {
        field: {
            "eventual_major_movers": _quartiles([getattr(row.detection, field) for row in winners]),
            "failed_breakouts": _quartiles([getattr(row.detection, field) for row in failures]),
        }
        for field in FEATURE_COMPARISON_FIELDS
    }

    cutoff = chronological_split([row.detection.scan_at for row in judged], config.calibration_fraction)
    calibration = [row for row in judged if cutoff is not None and row.detection.scan_at <= cutoff]
    validation = [row for row in judged if cutoff is not None and row.detection.scan_at > cutoff]

    missed = [
        missed_winner_snapshots(row.episode, audit_rows)
        for row in major
        if row.first_detection is None
    ]

    if not observations:
        warnings.append("NO_OBSERVATION_ROWS: nothing was replayed; run against production history.")
    ohlc_block = ohlc_validation or build_ohlc_validation_block(episodes, provider_name="none")
    if ohlc_block["status"] == OHLC_STATUS_NONE:
        warnings.append(
            "OHLC_VALIDATION_ABSENT: peaks come from event-sampled last_price only and "
            "may undercount intraperiod highs."
        )
    elif ohlc_block["status"] == OHLC_STATUS_PARTIAL_PEAK:
        warnings.append(
            f"OHLC_COVERAGE_PARTIAL: {ohlc_block['episodes_with_candles']}/"
            f"{ohlc_block['episodes_requested']} episodes have candles; peak "
            "comparison is incomplete and no timing or class metric is validated."
        )
    else:
        warnings.append(
            "OHLC_PEAK_COMPARISON_ONLY: every episode has candles, but only peak "
            "magnitude is corroborated. Class assignment, peak timing and threshold "
            "crossings remain event-sampled."
        )
    if incomplete:
        warnings.append(
            f"INCOMPLETE_FORWARD_WINDOWS: {len(incomplete)} detections lack a full "
            f"{config.horizon_hours}h forward window and are excluded from precision."
        )
    if not episodes:
        warnings.append("NO_EPISODES: no major-move episode met the trigger threshold.")
    if len(validation) < 30:
        warnings.append(
            "VALIDATION_SAMPLE_SMALL: the held-out period has too few judged detections "
            "to support a threshold recommendation."
        )
    if ingestion.rejected_lines:
        warnings.append(f"REJECTED_ROWS: {ingestion.rejected_lines} malformed or non-observation lines skipped.")

    if carry_fidelity:
        exceeded = carry_fidelity.get("exceeded_bound_pct")
        if exceeded is not None and exceeded > 20.0:
            warnings.append(
                f"PERSISTENCE_GAP_DRIFT_HIGH: {exceeded}% of carried cells sit further "
                f"than {CARRY_PRICE_DRIFT_BOUND_PCT}% from the next persisted "
                "observation. This is an uncertainty proxy, not measured "
                "reconstruction error - the gap also contains genuine post-scan "
                "movement - but a large share is a reason to discount reconstructed "
                "features."
            )
    if sensitivity:
        counts = [row["episodes"] for row in sensitivity]
        if counts and min(counts) > 0 and max(counts) / min(counts) >= 3.0:
            warnings.append(
                "EPISODE_COUNT_PARAMETER_SENSITIVE: episode totals vary more than 3x "
                "across the trigger/retrace sweep, so capture rates depend heavily on "
                "the episode priors and must not be quoted without them."
            )

    imputed_cells = sum(frame.imputed_count for frame in frames)
    observed_cells = sum(frame.observed_count for frame in frames)

    return {
        "version": VERSION,
        # Always provisional: see the module constants. OHLC coverage is
        # reported under "ohlc_validation" and never promotes this field.
        "status": REPORT_STATUS_PROVISIONAL,
        "methodology": {
            "scan_reconstruction": (
                "fixed 10-minute grid; latest event at or before each boundary carried "
                "forward within a bounded freshness window and flagged as imputed"
            ),
            "event_sampling_bias_corrected": True,
            "no_lookahead": True,
            "detection_evaluated_only_after_its_own_timestamp": True,
            "episode_deduplication": (
                "baseline-trigger-peak-reset cycle; one explosive run is one episode"
            ),
            "outcome_source": (
                "future persisted last_price only (event-sampled proxy); any OHLC "
                "coverage is an auxiliary peak comparison, not a replacement"
            ),
            "automatic_tuning_applied": False,
            "production_thresholds_changed": False,
            "telegram_messages_sent": 0,
            "private_kraken_calls": 0,
            "advisory_only": True,
        },
        "coverage": {
            "source_lines": ingestion.total_lines,
            "rejected_lines": ingestion.rejected_lines,
            "observation_rows": len(observations),
            "symbols": len(ingestion.symbols),
            "first_observation_at": observations[0].observed_at.isoformat() if observations else None,
            "last_observation_at": observations[-1].observed_at.isoformat() if observations else None,
            "reconstructed_scans": len(frames),
            "scan_interval_seconds": config.scan_interval_seconds,
            "observed_cells": observed_cells,
            "imputed_cells": imputed_cells,
            "imputed_cell_pct": (
                round(imputed_cells / (imputed_cells + observed_cells) * 100.0, 2)
                if (imputed_cells + observed_cells) else None
            ),
            "outcome_horizon_hours": config.horizon_hours,
            "detections": len(detections),
            "episodes": len(episodes),
            "major_move_episodes": len(major),
            "episodes_by_class": dict(sorted(Counter(row.outcome_class for row in episodes).items())),
            "detections_with_incomplete_forward_window": len(incomplete),
            "ohlc_validated_episodes": sum(1 for row in episodes if row.ohlc_validated),
            "ohlc_validation_coverage_pct": ohlc_block["coverage_pct"],
        },
        "ohlc_validation": ohlc_block,
        "detection_metrics_by_threshold_cohort": {
            "counting": (
                "CUMULATIVE and overlapping: GE_20 includes every episode that "
                "peaked at +20% or more, so a +320% episode is counted in all "
                "five cohorts. Do not sum these."
            ),
            "cohorts": threshold_cohorts,
        },
        "detection_metrics_by_exclusive_class": {
            "counting": (
                "MUTUALLY EXCLUSIVE half-open bands [low, high): each episode "
                "appears in exactly one. These sum to the episode total."
            ),
            "classes": exclusive_classes,
        },
        "false_positives": {
            "judged_detections": len(judged),
            "judged_population_rule": (
                "forward_max_return_pct is not None AND window_complete is True"
            ),
            "excluded_incomplete_window": len(incomplete),
            "excluded_unmeasurable": len(unmeasurable),
            "excluded_incomplete_bucket_counts": dict(sorted(incomplete_bucket_counts.items())),
            "excluded_note": (
                "Detections without a fully covered forward horizon are reported here "
                "but contribute to no rate, precision table, cohort or split below."
            ),
            "bucket_counts": dict(sorted(bucket_counts.items())),
            "failed_to_reach_plus_5_pct": (
                round(sum(1 for row in judged if row.forward_max_return_pct < 5.0) / len(judged) * 100.0, 2)
                if judged else None
            ),
            "reached_plus_10_pct": (
                round(sum(1 for row in judged if row.forward_max_return_pct >= 10.0) / len(judged) * 100.0, 2)
                if judged else None
            ),
            "reached_plus_20_pct": (
                round(sum(1 for row in judged if row.forward_max_return_pct >= 20.0) / len(judged) * 100.0, 2)
                if judged else None
            ),
            "precision_by_stage": _precision_table(judged, lambda row: row.detection.stage),
            "precision_by_explosion_bucket": _precision_table(
                judged, lambda row: _score_bucket(row.detection.explosion_potential_score)
            ),
            "precision_by_opportunity_bucket": _precision_table(
                judged, lambda row: _score_bucket(row.detection.opportunity_score)
            ),
            "precision_by_liquidity_band": _precision_table(
                judged, lambda row: _liquidity_band(row.detection.liquidity_24h_usd_approx)
            ),
        },
        "winner_vs_failed_breakout": {
            "note": (
                "Descriptive distributions only. No model is fitted and no weight is tuned."
            ),
            "eventual_major_mover_detections": len(winners),
            "failed_breakout_detections": len(failures),
            "features": comparison,
        },
        "reconstruction_drift_proxy": carry_fidelity or {
            "note": "Persistence-gap drift not measured for this report.",
        },
        "episode_parameter_sensitivity": {
            "note": (
                "Episode priors are not calibrated. Capture rates are only "
                "interpretable alongside this sweep."
            ),
            "default": {
                "trigger_pct": config.episodes.trigger_pct,
                "close_retrace_pct": config.episodes.close_retrace_pct,
                "baseline_window_hours": config.episodes.baseline_window_hours,
                "peak_cooldown_hours": config.episodes.peak_cooldown_hours,
            },
            "sweep": list(sensitivity or ()),
        },
        "missed_winners": {
            "count": len(missed),
            "note": "Scores at the last scan before each threshold of an undetected episode.",
            "episodes": missed[:200],
        },
        "out_of_sample": {
            "split": "chronological",
            "calibration_cutoff_at": cutoff.isoformat() if cutoff else None,
            "calibration_detections": len(calibration),
            "validation_detections": len(validation),
            "calibration_precision_plus_20_pct": (
                round(sum(row.reached_20 for row in calibration) / len(calibration) * 100.0, 2)
                if calibration else None
            ),
            "validation_precision_plus_20_pct": (
                round(sum(row.reached_20 for row in validation) / len(validation) * 100.0, 2)
                if validation else None
            ),
            "note": (
                "Any threshold observation must hold on the validation period before "
                "it is proposed. Nothing here is applied automatically."
            ),
        },
        "warnings": warnings,
    }


def run_phase2_replay(
    *,
    observation_file: Path | None = None,
    config: Phase2Config | None = None,
    ohlc_provider: OhlcProvider | None = None,
) -> dict[str, Any]:
    """Full offline replay. Reads one file, writes nothing, returns a report."""
    config = config or Phase2Config()
    ingestion = read_observations(observation_file)
    timelines = build_timelines(ingestion.observations)

    # Episodes are derived first so audit retention can be scoped to the
    # symbols that actually matter for missed-winner forensics.
    episodes = build_all_episodes(timelines, config=config.episodes)
    provider_name = "none"
    if ohlc_provider is not None and not isinstance(ohlc_provider, NullOhlcProvider):
        episodes = validate_episodes_with_ohlc(episodes, ohlc_provider)
        provider_name = type(ohlc_provider).__name__

    episode_symbols = {row.symbol for row in episodes}
    frames = reconstruct_scan_frames(
        ingestion.observations,
        interval_seconds=config.scan_interval_seconds,
        max_carry_seconds=config.max_carry_seconds,
    )
    detections, audit_rows = replay_signal_quality(
        frames, config=config, retain_audit_for=episode_symbols
    )
    outcomes = evaluate_detections(detections, timelines, horizon=config.horizon)

    return build_phase2_report(
        ingestion,
        frames,
        detections,
        audit_rows,
        episodes,
        outcomes,
        config=config,
        ohlc_validation=build_ohlc_validation_block(episodes, provider_name=provider_name),
        carry_fidelity=measure_persistence_gap_drift(ingestion.observations, frames),
        sensitivity=episode_sensitivity(timelines, base=config.episodes),
    )
