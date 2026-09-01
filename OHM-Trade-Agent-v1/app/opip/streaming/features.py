"""Pure feature math: trade-side normalization, CVD, liquidations, cross-venue.

Everything here is a pure function or an immutable accumulator update. No
network access, no wall clock, no exchange execution. Identity is fail-closed
throughout: two venues' evidence may only be combined once both sides carry a
UNIQUE canonical asset mapping to the *same* asset — ticker similarity alone
is never sufficient (see combinable_identity).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
import math
from types import MappingProxyType

from app.opip.events.contract import MappingStatus, require_utc
from app.opip.streaming.contract import (
    LiquidationSide,
    LiquidationSyncState,
    TradeSide,
    VenueAgreementState,
)
from app.opip.streaming.quality import (
    DEGRADATION_INCOMPLETE_VENUE_COVERAGE,
    EvidenceQuality,
    EvidenceQualityState,
    can_independently_confirm,
    combine_quality,
)


# Interpretable prior, not a statistically calibrated threshold: a venue's
# signed notional within this fraction of its own gross notional is treated
# as directionally neutral rather than aligned/opposed. Configuration, not a
# claim about live market behavior.
DEFAULT_NEUTRAL_NOTIONAL_RATIO = 0.05

_RAW_SIDE_MAP = {"BUY": TradeSide.BUY_AGGRESSOR, "SELL": TradeSide.SELL_AGGRESSOR}


def _require_finite(name: str, value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def _require_nonnegative_finite(name: str, value: float) -> float:
    numeric = _require_finite(name, value)
    if numeric < 0:
        raise ValueError(f"{name} cannot be negative")
    return numeric


def _freeze_mapping(values: Mapping[str, float | int]) -> Mapping[str, float | int]:
    return MappingProxyType(dict(values))


def normalize_trade_side(raw: str | None) -> TradeSide:
    """Canonical aggressor side. Missing/malformed input fails closed to
    UNKNOWN — it is never inferred and never defaulted to a direction."""
    token = str(raw or "").strip().upper()
    return _RAW_SIDE_MAP.get(token, TradeSide.UNKNOWN)


# ---------------------------------------------------------------- trade CVD


@dataclass(frozen=True)
class TradeObservation:
    canonical_asset_id: str
    identity_status: MappingStatus
    venue: str
    side: TradeSide
    base_quantity: float
    notional_usd: float
    provider_timestamp_utc: datetime

    def __post_init__(self) -> None:
        if not str(self.venue or "").strip():
            raise ValueError("venue is required")
        object.__setattr__(self, "side", TradeSide(self.side))
        object.__setattr__(self, "identity_status", MappingStatus(self.identity_status))
        object.__setattr__(
            self, "base_quantity", _require_nonnegative_finite(
                "base_quantity", self.base_quantity
            )
        )
        object.__setattr__(
            self, "notional_usd", _require_nonnegative_finite(
                "notional_usd", self.notional_usd
            )
        )
        require_utc(self.provider_timestamp_utc, field_name="provider_timestamp_utc")
        canonical = str(self.canonical_asset_id or "").strip()
        if self.identity_status == MappingStatus.UNIQUE and not canonical:
            raise ValueError("UNIQUE identity_status requires canonical_asset_id")
        if self.identity_status != MappingStatus.UNIQUE and canonical:
            raise ValueError(
                "canonical_asset_id may only be set when identity_status is UNIQUE"
            )


@dataclass(frozen=True)
class VenueCvdState:
    venue: str
    canonical_asset_id: str
    signed_base_volume: float = 0.0
    signed_notional_usd: float = 0.0
    gross_notional_usd: float = 0.0
    trade_count: int = 0
    excluded_unknown_base_volume: float = 0.0
    excluded_unknown_notional_usd: float = 0.0
    excluded_unknown_count: int = 0

    def __post_init__(self) -> None:
        if not str(self.venue or "").strip():
            raise ValueError("venue is required")
        if not str(self.canonical_asset_id or "").strip():
            raise ValueError("canonical_asset_id is required")
        for name in (
            "signed_base_volume",
            "signed_notional_usd",
            "gross_notional_usd",
            "excluded_unknown_base_volume",
            "excluded_unknown_notional_usd",
        ):
            _require_finite(name, getattr(self, name))
        if self.gross_notional_usd < 0:
            raise ValueError("gross_notional_usd cannot be negative")
        if self.excluded_unknown_base_volume < 0:
            raise ValueError("excluded_unknown_base_volume cannot be negative")
        if self.excluded_unknown_notional_usd < 0:
            raise ValueError("excluded_unknown_notional_usd cannot be negative")
        if self.trade_count < 0 or self.excluded_unknown_count < 0:
            raise ValueError("trade counts cannot be negative")

    @property
    def has_directional_evidence(self) -> bool:
        return self.trade_count > 0


def empty_venue_cvd(venue: str, canonical_asset_id: str) -> VenueCvdState:
    return VenueCvdState(
        venue=venue,
        canonical_asset_id=canonical_asset_id,
    )


def accumulate_cvd(existing: VenueCvdState, observation: TradeObservation) -> VenueCvdState:
    """Fold one trade into venue-level CVD.

    BUY_AGGRESSOR adds, SELL_AGGRESSOR subtracts. UNKNOWN is excluded from the
    directional delta but tracked explicitly rather than silently discarded.
    """
    if observation.canonical_asset_id != existing.canonical_asset_id:
        raise ValueError("observation asset does not match accumulator asset")
    if observation.venue != existing.venue:
        raise ValueError("observation venue does not match accumulator venue")

    if observation.side == TradeSide.UNKNOWN:
        return replace(
            existing,
            excluded_unknown_base_volume=(
                existing.excluded_unknown_base_volume + observation.base_quantity
            ),
            excluded_unknown_notional_usd=(
                existing.excluded_unknown_notional_usd + observation.notional_usd
            ),
            excluded_unknown_count=existing.excluded_unknown_count + 1,
        )

    sign = 1.0 if observation.side == TradeSide.BUY_AGGRESSOR else -1.0
    return replace(
        existing,
        signed_base_volume=existing.signed_base_volume + sign * observation.base_quantity,
        signed_notional_usd=existing.signed_notional_usd + sign * observation.notional_usd,
        gross_notional_usd=existing.gross_notional_usd + observation.notional_usd,
        trade_count=existing.trade_count + 1,
    )


# --------------------------------------------------------------- cross-venue


def combinable_identity(
    *,
    left_status: MappingStatus,
    left_canonical_id: str | None,
    right_status: MappingStatus,
    right_canonical_id: str | None,
) -> bool:
    """Two venues' evidence may combine only with matching UNIQUE identity.

    Symbol similarity, matching tickers, or "looks like the same asset" never
    substitutes for this check.
    """
    if left_status != MappingStatus.UNIQUE or right_status != MappingStatus.UNIQUE:
        return False
    left = str(left_canonical_id or "").strip()
    right = str(right_canonical_id or "").strip()
    return bool(left) and bool(right) and left == right


@dataclass(frozen=True)
class CrossVenueCvdSnapshot:
    canonical_asset_id: str
    combined_signed_notional_usd: float
    per_venue_signed_notional_usd: Mapping[str, float]
    per_venue_signed_base_volume: Mapping[str, float]
    agreement: VenueAgreementState
    quality: EvidenceQuality
    excluded_venues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_finite(
            "combined_signed_notional_usd", self.combined_signed_notional_usd
        )
        for name, mapping in (
            ("per_venue_signed_notional_usd", self.per_venue_signed_notional_usd),
            ("per_venue_signed_base_volume", self.per_venue_signed_base_volume),
        ):
            for venue, value in mapping.items():
                if not str(venue or "").strip():
                    raise ValueError(f"{name} contains an empty venue")
                _require_finite(f"{name}[{venue}]", value)
        object.__setattr__(
            self,
            "per_venue_signed_notional_usd",
            _freeze_mapping(self.per_venue_signed_notional_usd),
        )
        object.__setattr__(
            self,
            "per_venue_signed_base_volume",
            _freeze_mapping(self.per_venue_signed_base_volume),
        )


def _venue_polarity(state: VenueCvdState, *, neutral_ratio: float) -> int:
    """-1 / 0 / +1, where 0 means "directionally neutral for this venue"."""
    if not state.has_directional_evidence or state.gross_notional_usd <= 0:
        return 0
    ratio = state.signed_notional_usd / state.gross_notional_usd
    if abs(ratio) <= neutral_ratio:
        return 0
    return 1 if ratio > 0 else -1


def combine_cross_venue(
    *,
    canonical_asset_id: str,
    venue_states: dict[str, VenueCvdState],
    venue_qualities: dict[str, EvidenceQuality],
    neutral_notional_ratio: float = DEFAULT_NEUTRAL_NOTIONAL_RATIO,
) -> CrossVenueCvdSnapshot:
    """Normalized (USD-notional) cross-venue CVD combination.

    Deliberately not `binance_raw_volume + bybit_raw_volume`: venues differ in
    contract size and liquidity conventions, so combination happens on
    notional terms, and a venue whose quality cannot independently confirm is
    still included in the combined figure (it is evidence, just imperfect)
    but is excluded from the *agreement* verdict and named in
    excluded_venues.
    """

    if not str(canonical_asset_id or "").strip():
        raise ValueError("canonical_asset_id is required")
    neutral = _require_finite("neutral_notional_ratio", neutral_notional_ratio)
    if not 0 <= neutral <= 1:
        raise ValueError("neutral_notional_ratio must be within [0, 1]")

    if not venue_states:
        return CrossVenueCvdSnapshot(
            canonical_asset_id=canonical_asset_id,
            combined_signed_notional_usd=0.0,
            per_venue_signed_notional_usd={},
            per_venue_signed_base_volume={},
            agreement=VenueAgreementState.INSUFFICIENT_EVIDENCE,
            quality=EvidenceQuality(
                state=EvidenceQualityState.UNUSABLE,
                degradations=frozenset({DEGRADATION_INCOMPLETE_VENUE_COVERAGE}),
            ),
        )

    for venue, state in venue_states.items():
        if state.venue != venue:
            raise ValueError(
                f"venue_states key {venue!r} does not match state.venue {state.venue!r}"
            )
        if state.canonical_asset_id != canonical_asset_id:
            raise ValueError(
                f"venue state {venue!r} asset does not match canonical_asset_id"
            )

    combined_notional = sum(state.signed_notional_usd for state in venue_states.values())
    per_venue_notional = {
        venue: state.signed_notional_usd for venue, state in venue_states.items()
    }
    per_venue_base = {
        venue: state.signed_base_volume for venue, state in venue_states.items()
    }

    state_keys = set(venue_states)
    quality_keys = set(venue_qualities)
    quality_inputs = [
        venue_qualities[venue] for venue in sorted(state_keys & quality_keys)
    ]
    if state_keys != quality_keys:
        quality_inputs.append(
            EvidenceQuality(
                state=EvidenceQualityState.INCOMPLETE,
                degradations=frozenset({DEGRADATION_INCOMPLETE_VENUE_COVERAGE}),
            )
        )
    combined_quality = combine_quality(quality_inputs)

    confirmable_venues = [
        venue
        for venue in sorted(state_keys & quality_keys)
        if can_independently_confirm(venue_qualities[venue])
        and venue_states[venue].has_directional_evidence
    ]
    excluded = tuple(
        sorted(venue for venue in venue_states if venue not in confirmable_venues)
    )

    if len(confirmable_venues) == 0:
        agreement = VenueAgreementState.INSUFFICIENT_EVIDENCE
    elif len(confirmable_venues) == 1:
        polarity = _venue_polarity(
            venue_states[confirmable_venues[0]], neutral_ratio=neutral
        )
        agreement = (
            VenueAgreementState.MIXED_NEUTRAL
            if polarity == 0
            else VenueAgreementState.INSUFFICIENT_EVIDENCE
        )
    else:
        polarities = {
            _venue_polarity(venue_states[venue], neutral_ratio=neutral)
            for venue in confirmable_venues
        }
        if polarities == {0}:
            agreement = VenueAgreementState.MIXED_NEUTRAL
        elif polarities == {1}:
            agreement = VenueAgreementState.ALIGNED_POSITIVE
        elif polarities == {-1}:
            agreement = VenueAgreementState.ALIGNED_NEGATIVE
        elif polarities in ({1, -1}, {1, -1, 0}):
            agreement = VenueAgreementState.DISAGREEMENT
        else:
            # {0, 1} or {0, -1}: one venue neutral, the other directional.
            agreement = VenueAgreementState.MIXED_NEUTRAL

    return CrossVenueCvdSnapshot(
        canonical_asset_id=canonical_asset_id,
        combined_signed_notional_usd=combined_notional,
        per_venue_signed_notional_usd=per_venue_notional,
        per_venue_signed_base_volume=per_venue_base,
        agreement=agreement,
        quality=combined_quality,
        excluded_venues=excluded,
    )


# ------------------------------------------------------------- liquidations


@dataclass(frozen=True)
class LiquidationObservation:
    canonical_asset_id: str
    identity_status: MappingStatus
    venue: str
    side: LiquidationSide
    base_quantity: float
    notional_usd: float
    provider_timestamp_utc: datetime
    ingest_timestamp_utc: datetime

    def __post_init__(self) -> None:
        if not str(self.venue or "").strip():
            raise ValueError("venue is required")
        object.__setattr__(self, "side", LiquidationSide(self.side))
        object.__setattr__(self, "identity_status", MappingStatus(self.identity_status))
        object.__setattr__(
            self, "base_quantity", _require_nonnegative_finite(
                "base_quantity", self.base_quantity
            )
        )
        object.__setattr__(
            self, "notional_usd", _require_nonnegative_finite(
                "notional_usd", self.notional_usd
            )
        )
        require_utc(self.provider_timestamp_utc, field_name="provider_timestamp_utc")
        require_utc(self.ingest_timestamp_utc, field_name="ingest_timestamp_utc")
        canonical = str(self.canonical_asset_id or "").strip()
        if self.identity_status == MappingStatus.UNIQUE and not canonical:
            raise ValueError("UNIQUE identity_status requires canonical_asset_id")
        if self.identity_status != MappingStatus.UNIQUE and canonical:
            raise ValueError(
                "canonical_asset_id may only be set when identity_status is UNIQUE"
            )


@dataclass(frozen=True)
class LiquidationAggregate:
    canonical_asset_id: str
    long_notional_usd: float = 0.0
    short_notional_usd: float = 0.0
    long_base_volume: float = 0.0
    short_base_volume: float = 0.0
    unknown_side_notional_usd: float = 0.0
    unknown_side_count: int = 0
    venue_participation: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        for name in (
            "long_notional_usd",
            "short_notional_usd",
            "long_base_volume",
            "short_base_volume",
            "unknown_side_notional_usd",
        ):
            value = _require_nonnegative_finite(name, getattr(self, name))
            object.__setattr__(self, name, value)
        if self.unknown_side_count < 0:
            raise ValueError("unknown_side_count cannot be negative")
        participation = dict(self.venue_participation or {})
        for venue, count in participation.items():
            if not str(venue or "").strip() or int(count) < 0:
                raise ValueError("venue participation must be non-negative")
        object.__setattr__(
            self, "venue_participation", MappingProxyType(participation)
        )

    @property
    def imbalance_notional_usd(self) -> float:
        return self.long_notional_usd - self.short_notional_usd

    @property
    def total_notional_usd(self) -> float:
        return (
            self.long_notional_usd
            + self.short_notional_usd
            + self.unknown_side_notional_usd
        )


def empty_liquidation_aggregate(canonical_asset_id: str) -> LiquidationAggregate:
    return LiquidationAggregate(canonical_asset_id=canonical_asset_id, venue_participation={})


def accumulate_liquidation(
    existing: LiquidationAggregate, observation: LiquidationObservation
) -> LiquidationAggregate:
    if observation.canonical_asset_id != existing.canonical_asset_id:
        raise ValueError("observation asset does not match aggregate asset")

    participation = dict(existing.venue_participation)
    participation[observation.venue] = participation.get(observation.venue, 0) + 1

    if observation.side == LiquidationSide.LONG_LIQUIDATION:
        return replace(
            existing,
            long_notional_usd=existing.long_notional_usd + observation.notional_usd,
            long_base_volume=existing.long_base_volume + observation.base_quantity,
            venue_participation=participation,
        )
    if observation.side == LiquidationSide.SHORT_LIQUIDATION:
        return replace(
            existing,
            short_notional_usd=existing.short_notional_usd + observation.notional_usd,
            short_base_volume=existing.short_base_volume + observation.base_quantity,
            venue_participation=participation,
        )
    return replace(
        existing,
        unknown_side_notional_usd=(
            existing.unknown_side_notional_usd + observation.notional_usd
        ),
        unknown_side_count=existing.unknown_side_count + 1,
        venue_participation=participation,
    )


@dataclass(frozen=True)
class LiquidationSyncResult:
    state: LiquidationSyncState
    window_seconds: float
    participating_venues: tuple[str, ...]
    max_pairwise_delta_seconds: float | None = None


def assess_liquidation_synchronization(
    observations: list[LiquidationObservation],
    *,
    window_seconds: float,
    min_venues: int = 2,
) -> LiquidationSyncResult:
    """Deterministic cross-venue liquidation synchronization check.

    Synchronized means at least `min_venues` distinct venues each reported a
    liquidation such that the venues' earliest observations fall within
    `window_seconds` of one another (by provider timestamp). Degraded/unknown-
    identity observations are the caller's responsibility to filter out
    before calling this — this function only reasons about what it's given.
    """
    window = _require_finite("window_seconds", window_seconds)
    if window <= 0:
        raise ValueError("window_seconds must be positive")
    if int(min_venues) < 2:
        raise ValueError("min_venues must be at least 2")

    eligible = [
        observation
        for observation in observations
        if observation.identity_status == MappingStatus.UNIQUE
        and str(observation.canonical_asset_id or "").strip()
    ]
    if not eligible:
        return LiquidationSyncResult(
            state=LiquidationSyncState.INSUFFICIENT_EVIDENCE,
            window_seconds=window,
            participating_venues=(),
        )

    assets = {observation.canonical_asset_id for observation in eligible}
    venues_all = tuple(sorted({observation.venue for observation in eligible}))
    if len(assets) != 1 or len(venues_all) < min_venues:
        return LiquidationSyncResult(
            state=LiquidationSyncState.INSUFFICIENT_EVIDENCE,
            window_seconds=window,
            participating_venues=venues_all,
        )

    ordered = sorted(eligible, key=lambda item: item.provider_timestamp_utc)
    best_delta: float | None = None
    best_venues: tuple[str, ...] = ()
    left = 0
    counts: dict[str, int] = {}

    for right, observation in enumerate(ordered):
        counts[observation.venue] = counts.get(observation.venue, 0) + 1
        while left <= right:
            delta = (
                ordered[right].provider_timestamp_utc
                - ordered[left].provider_timestamp_utc
            ).total_seconds()
            if delta <= window:
                break
            left_venue = ordered[left].venue
            counts[left_venue] -= 1
            if counts[left_venue] <= 0:
                del counts[left_venue]
            left += 1

        if len(counts) >= min_venues:
            current_delta = (
                ordered[right].provider_timestamp_utc
                - ordered[left].provider_timestamp_utc
            ).total_seconds()
            if best_delta is None or current_delta < best_delta:
                best_delta = current_delta
                best_venues = tuple(sorted(counts))

    if best_delta is not None:
        return LiquidationSyncResult(
            state=LiquidationSyncState.SYNCHRONIZED,
            window_seconds=window,
            participating_venues=best_venues,
            max_pairwise_delta_seconds=best_delta,
        )

    full_delta = (
        ordered[-1].provider_timestamp_utc
        - ordered[0].provider_timestamp_utc
    ).total_seconds()
    return LiquidationSyncResult(
        state=LiquidationSyncState.NOT_SYNCHRONIZED,
        window_seconds=window,
        participating_venues=venues_all,
        max_pairwise_delta_seconds=full_delta,
    )
