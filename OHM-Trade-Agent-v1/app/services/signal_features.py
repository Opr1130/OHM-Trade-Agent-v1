"""Temporal + cross-universe feature derivation for Signal Quality v1 (Phase 1).

Pure functions only: no network, no filesystem, no clock reads. Everything is
derived from an explicitly supplied runtime scan history, which makes the
no-lookahead property directly testable — features for scan *i* are computed
from ``history[: i + 1]`` and can never consult a later observation.

Two deliberate boundaries:

* **Coin identity never enters this module's feature maths.** Symbols are used
  only as opaque dictionary keys when ranking the universe, never as an input
  to any derived value. A scorer must not be able to learn "DUCK is bad".
* **Feature state is not learning persistence.** A scan that
  ``full_market_observation._should_persist`` declines to append to the
  event-sampled JSONL stream still belongs in this history. Persistence and
  continuity are counted in *runtime scans*, not in persisted rows.

Every numeric constant here is an interpretable Phase 1 prior. None of it has
been fitted to outcome data; Phase 2 calibration is pending.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


VERSION = "signal-features-v1"

# Minimum runtime scans before any temporal feature is meaningful. One snapshot
# yields no change, no acceleration and no persistence.
MIN_SCANS_FOR_FEATURES = 2


@dataclass(frozen=True)
class ObservationSnapshot:
    """One runtime scan of one market.

    Mirrors the fields the design requires history to retain. Deliberately
    carries no symbol: a snapshot is positional evidence, not an identity.
    """

    observed_at: datetime
    last_price: float
    volume_24h: float
    notional_24h_usd_approx: float
    high_24h: float
    low_24h: float
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_at"] = self.observed_at.astimezone(timezone.utc).isoformat()
        return payload


@dataclass(frozen=True)
class SymbolFeatures:
    """Temporal features for one market, derived from its own history alone.

    ``valid`` is False when the history cannot support a defensible feature
    set. An invalid feature row must be suppressed rather than scored on
    partial evidence.
    """

    version: str = VERSION
    valid: bool = False
    invalid_reason: str | None = None

    scans_available: int = 0
    interval_seconds: float = 0.0
    continuity_intact: bool = False

    # Current-state levels (last snapshot).
    last_price: float = 0.0
    notional_24h_usd_approx: float = 0.0
    lift_from_24h_low_pct: float = 0.0
    distance_from_24h_high_pct: float = 0.0

    # First-order change across the most recent runtime interval, normalised to
    # the nominal scan cadence so an unusually long gap cannot masquerade as a
    # burst of momentum.
    price_change_since_prior_pct: float = 0.0
    price_change_rate_pct: float = 0.0
    lift_change_since_prior_pct: float = 0.0
    lift_change_rate_pct: float = 0.0

    # Second-order: is the rate itself rising or falling?
    price_acceleration_pct: float = 0.0
    lift_acceleration_pct: float = 0.0

    # Rolling-24h growth proxy. NOT interval volume - see
    # rolling_volume_growth_proxy_note.
    rolling_notional_growth_rate_pct: float = 0.0
    rolling_notional_vs_median3_pct: float = 0.0
    positive_growth_intervals: int = 0
    growth_intervals_observed: int = 0

    # Persistence, counted in consecutive qualifying runtime scans.
    consecutive_qualifying_scans: int = 0

    # Exhaustion evidence.
    window_run_up_pct: float = 0.0
    momentum_decelerating: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualifyingConditions:
    """Minimum directional structure a scan must show to extend persistence.

    A scan that merely exists does not count. Without a positive advance the
    chain breaks, which is what stops a single spike surrounded by flat scans
    from inheriting persistence credit it never earned.
    """

    min_price_change_pct: float = 0.10
    min_lift_from_low_pct: float = 2.0
    max_distance_from_high_pct: float = 6.0


@dataclass(frozen=True)
class FeatureDerivationConfig:
    """Inputs governing feature derivation. All values are Phase 1 priors."""

    nominal_interval_seconds: float = 600.0
    continuity_multiplier: float = 2.5
    qualifying: QualifyingConditions = field(default_factory=QualifyingConditions)
    # History longer than this is ignored when measuring the run-up window, so
    # "recent extension" stays recent regardless of retention depth.
    run_up_window_scans: int = 8

    @property
    def continuity_window_seconds(self) -> float:
        return max(1.0, self.nominal_interval_seconds * self.continuity_multiplier)


ROLLING_VOLUME_GROWTH_PROXY_NOTE = (
    "Kraken volume_24h is a rolling 24-hour aggregate, so the change between "
    "two scans is not interval volume. It is a growth proxy only: a rising "
    "rolling window means recent trade activity exceeds the activity aging out "
    "of it. Phase 2 must replace this with true interval volume before any "
    "claim about volume confirmation is treated as calibrated."
)


def _finite(*values: Any) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        return False


def _pct_change(current: float, reference: float) -> float:
    if not _finite(current, reference) or reference <= 0:
        return 0.0
    result = (current / reference - 1.0) * 100.0
    return result if math.isfinite(result) else 0.0


def _normalise_rate(value: float, elapsed_seconds: float, nominal_seconds: float) -> float:
    """Express a per-interval change at the nominal scan cadence.

    Without this a 40-minute gap and a 10-minute gap produce the same headline
    number, and the slower one looks four times as explosive as it is.
    """
    if not _finite(value, elapsed_seconds, nominal_seconds):
        return 0.0
    if elapsed_seconds <= 0 or nominal_seconds <= 0:
        return 0.0
    scaled = value * (nominal_seconds / elapsed_seconds)
    return scaled if math.isfinite(scaled) else 0.0


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


def snapshot_from_mapping(row: Mapping[str, Any], *, observed_at: Any = None) -> ObservationSnapshot | None:
    """Rebuild a snapshot from persisted state, rejecting unusable rows."""
    if not isinstance(row, Mapping):
        return None
    moment = _as_utc(observed_at if observed_at is not None else row.get("observed_at") or row.get("recorded_at"))
    if moment is None:
        return None
    try:
        last_price = float(row.get("last_price") or 0.0)
        volume = float(row.get("volume_24h") or 0.0)
        notional = float(row.get("notional_24h_usd_approx") or 0.0)
        high = float(row.get("high_24h") or 0.0)
        low = float(row.get("low_24h") or 0.0)
        lift = float(row.get("lift_from_24h_low_pct") or 0.0)
        distance = float(row.get("distance_from_24h_high_pct") or 0.0)
    except (TypeError, ValueError):
        return None
    if not _finite(last_price, volume, notional, high, low, lift, distance):
        return None
    if last_price <= 0 or notional < 0:
        return None
    return ObservationSnapshot(
        observed_at=moment,
        last_price=last_price,
        volume_24h=volume,
        notional_24h_usd_approx=notional,
        high_24h=high,
        low_24h=low,
        lift_from_24h_low_pct=lift,
        distance_from_24h_high_pct=distance,
    )


def _is_qualifying(
    current: ObservationSnapshot,
    previous: ObservationSnapshot,
    conditions: QualifyingConditions,
) -> bool:
    """Does this scan continue a pre-explosion structure?"""
    price_change = _pct_change(current.last_price, previous.last_price)
    if price_change < conditions.min_price_change_pct:
        return False
    if current.lift_from_24h_low_pct < conditions.min_lift_from_low_pct:
        return False
    if current.distance_from_24h_high_pct > conditions.max_distance_from_high_pct:
        return False
    return True


def _count_qualifying_scans(
    history: Sequence[ObservationSnapshot],
    config: FeatureDerivationConfig,
) -> int:
    """Consecutive qualifying runtime scans, counted backwards from the newest.

    The walk stops at the first non-qualifying scan and at any continuity gap,
    so a broken chain cannot be silently stitched back together across an
    outage.
    """
    window = config.continuity_window_seconds
    count = 0
    for index in range(len(history) - 1, 0, -1):
        current = history[index]
        previous = history[index - 1]
        elapsed = (current.observed_at - previous.observed_at).total_seconds()
        if elapsed <= 0 or elapsed > window:
            break
        if not _is_qualifying(current, previous, config.qualifying):
            break
        count += 1
    return count


def derive_symbol_features(
    history: Sequence[ObservationSnapshot],
    *,
    config: FeatureDerivationConfig | None = None,
) -> SymbolFeatures:
    """Derive temporal features from one market's runtime scan history.

    Only ``history`` is consulted, and only in order, so the result for a
    prefix of a series is identical to the result computed live at that point.
    """
    config = config or FeatureDerivationConfig()
    rows = [row for row in history if isinstance(row, ObservationSnapshot)]
    if len(rows) < MIN_SCANS_FOR_FEATURES:
        return SymbolFeatures(
            valid=False,
            invalid_reason="INSUFFICIENT_HISTORY",
            scans_available=len(rows),
        )

    current = rows[-1]
    previous = rows[-2]
    if not _finite(
        current.last_price,
        current.notional_24h_usd_approx,
        current.lift_from_24h_low_pct,
        current.distance_from_24h_high_pct,
        previous.last_price,
    ):
        return SymbolFeatures(
            valid=False,
            invalid_reason="INVALID_MARKET_DATA",
            scans_available=len(rows),
        )
    if current.last_price <= 0 or previous.last_price <= 0:
        return SymbolFeatures(
            valid=False,
            invalid_reason="INVALID_MARKET_DATA",
            scans_available=len(rows),
        )

    interval = (current.observed_at - previous.observed_at).total_seconds()
    if interval <= 0:
        return SymbolFeatures(
            valid=False,
            invalid_reason="INVALID_MARKET_DATA",
            scans_available=len(rows),
        )
    nominal = config.nominal_interval_seconds
    continuity_intact = interval <= config.continuity_window_seconds

    price_change = _pct_change(current.last_price, previous.last_price)
    price_rate = _normalise_rate(price_change, interval, nominal)
    lift_change = current.lift_from_24h_low_pct - previous.lift_from_24h_low_pct
    lift_rate = _normalise_rate(lift_change, interval, nominal)

    # Second order. Absent a third scan the rate is treated as steady rather
    # than as accelerating - an unknown is not evidence of acceleration.
    price_acceleration = 0.0
    lift_acceleration = 0.0
    if len(rows) >= 3:
        older = rows[-3]
        prior_interval = (previous.observed_at - older.observed_at).total_seconds()
        if prior_interval > 0 and older.last_price > 0:
            prior_price_rate = _normalise_rate(
                _pct_change(previous.last_price, older.last_price), prior_interval, nominal
            )
            price_acceleration = price_rate - prior_price_rate
            prior_lift_rate = _normalise_rate(
                previous.lift_from_24h_low_pct - older.lift_from_24h_low_pct,
                prior_interval,
                nominal,
            )
            lift_acceleration = lift_rate - prior_lift_rate

    growth_rate, vs_median3, positive_intervals, intervals_observed = _rolling_growth_proxy(
        rows, nominal_seconds=nominal
    )

    qualifying = _count_qualifying_scans(rows, config)

    window = rows[-min(len(rows), max(2, config.run_up_window_scans)):]
    run_up = _pct_change(current.last_price, min(row.last_price for row in window))
    momentum_decelerating = price_acceleration < 0.0 and price_rate > 0.0

    return SymbolFeatures(
        valid=True,
        invalid_reason=None,
        scans_available=len(rows),
        interval_seconds=round(interval, 3),
        continuity_intact=continuity_intact,
        last_price=current.last_price,
        notional_24h_usd_approx=current.notional_24h_usd_approx,
        lift_from_24h_low_pct=round(current.lift_from_24h_low_pct, 6),
        distance_from_24h_high_pct=round(current.distance_from_24h_high_pct, 6),
        price_change_since_prior_pct=round(price_change, 6),
        price_change_rate_pct=round(price_rate, 6),
        lift_change_since_prior_pct=round(lift_change, 6),
        lift_change_rate_pct=round(lift_rate, 6),
        price_acceleration_pct=round(price_acceleration, 6),
        lift_acceleration_pct=round(lift_acceleration, 6),
        rolling_notional_growth_rate_pct=round(growth_rate, 6),
        rolling_notional_vs_median3_pct=round(vs_median3, 6),
        positive_growth_intervals=positive_intervals,
        growth_intervals_observed=intervals_observed,
        consecutive_qualifying_scans=qualifying,
        window_run_up_pct=round(run_up, 6),
        momentum_decelerating=momentum_decelerating,
    )


def _rolling_growth_proxy(
    rows: Sequence[ObservationSnapshot],
    *,
    nominal_seconds: float,
) -> tuple[float, float, int, int]:
    """Growth of the rolling 24h notional window across recent scans.

    Returns (latest growth rate %, growth vs 3-scan median %, count of positive
    intervals, intervals observed). See ROLLING_VOLUME_GROWTH_PROXY_NOTE: this
    is not interval volume and must not be described as such.
    """
    current = rows[-1]
    previous = rows[-2]
    if current.notional_24h_usd_approx <= 0 or previous.notional_24h_usd_approx <= 0:
        return 0.0, 0.0, 0, 0

    interval = (current.observed_at - previous.observed_at).total_seconds()
    growth_rate = _normalise_rate(
        _pct_change(current.notional_24h_usd_approx, previous.notional_24h_usd_approx),
        interval,
        nominal_seconds,
    )

    # Compare against the median of the preceding scans rather than a single
    # prior reading, so one anomalous print cannot define the baseline.
    baseline_rows = [row.notional_24h_usd_approx for row in rows[-4:-1] if row.notional_24h_usd_approx > 0]
    vs_median3 = _pct_change(current.notional_24h_usd_approx, median(baseline_rows)) if baseline_rows else 0.0

    positive = 0
    observed = 0
    for index in range(len(rows) - 1, 0, -1):
        newer = rows[index]
        older = rows[index - 1]
        if newer.notional_24h_usd_approx <= 0 or older.notional_24h_usd_approx <= 0:
            break
        step = (newer.observed_at - older.observed_at).total_seconds()
        if step <= 0:
            break
        observed += 1
        if newer.notional_24h_usd_approx > older.notional_24h_usd_approx:
            positive += 1
        else:
            break
        if observed >= 5:
            break

    return growth_rate, vs_median3, positive, observed


def percentile_rank(values: Sequence[float], value: float) -> float:
    """Percentile of ``value`` within ``values`` (0-100).

    Ties share their midpoint so a universe of identical readings ranks at the
    50th percentile rather than handing every member the top score.
    """
    finite = [float(item) for item in values if _finite(item)]
    if not finite or not _finite(value):
        return 0.0
    below = sum(1 for item in finite if item < value)
    equal = sum(1 for item in finite if item == value)
    rank = (below + 0.5 * equal) / len(finite) * 100.0
    return round(max(0.0, min(100.0, rank)), 4)


@dataclass(frozen=True)
class UniversePercentiles:
    """Cross-universe relative-strength inputs for one market."""

    price_change_percentile: float = 0.0
    structural_acceleration_percentile: float = 0.0
    universe_size: int = 0


def derive_universe_percentiles(
    features_by_symbol: Mapping[str, SymbolFeatures],
) -> dict[str, UniversePercentiles]:
    """Rank every valid market against the whole observed eligible universe.

    Ranking only among markets that already passed transition rules would make
    the percentile selection-biased: everything left would look strong by
    construction. The comparison set here is every market with derivable
    features, whether or not it is a candidate.

    Symbols are opaque keys. No identity reaches the ranking maths.
    """
    valid = {symbol: row for symbol, row in features_by_symbol.items() if row.valid}
    if not valid:
        return {}

    price_values = [row.price_change_rate_pct for row in valid.values()]
    structural_values = [row.lift_acceleration_pct for row in valid.values()]
    size = len(valid)

    return {
        symbol: UniversePercentiles(
            price_change_percentile=percentile_rank(price_values, row.price_change_rate_pct),
            structural_acceleration_percentile=percentile_rank(
                structural_values, row.lift_acceleration_pct
            ),
            universe_size=size,
        )
        for symbol, row in valid.items()
    }


def derive_features_for_universe(
    history_by_symbol: Mapping[str, Iterable[ObservationSnapshot]],
    *,
    config: FeatureDerivationConfig | None = None,
) -> dict[str, SymbolFeatures]:
    """Derive per-symbol features for every market in the observed universe."""
    config = config or FeatureDerivationConfig()
    return {
        str(symbol).upper(): derive_symbol_features(list(history), config=config)
        for symbol, history in history_by_symbol.items()
    }
