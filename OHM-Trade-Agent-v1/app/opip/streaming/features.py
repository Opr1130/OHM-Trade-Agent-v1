"""Pure feature math: trade-side normalization, CVD, liquidations, cross-venue.

Everything here is a pure function or an immutable accumulator update. No
network access, no wall clock, no exchange execution. Identity is fail-closed
throughout: two venues' evidence may only be combined once both sides carry a
UNIQUE canonical asset mapping to the *same* asset — ticker similarity alone
is never sufficient (see combinable_identity).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from app.opip.events.contract import MappingStatus, require_utc
from app.opip.streaming.contract import (
    LiquidationSide,
    LiquidationSyncState,
    TradeSide,
    VenueAgreementState,
)
from app.opip.streaming.quality import (
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
        if self.base_quantity < 0:
            raise ValueError("base_quantity cannot be negative")
        if self.notional_usd < 0:
            raise ValueError("notional_usd cannot be negative")
        require_utc(self.provider_timestamp_utc, field_name="provider_timestamp_utc")
        if (
            self.identity_status != MappingStatus.UNIQUE
            and str(self.canonical_asset_id or "").strip()
        ):
            raise ValueError(
                "canonical_asset_id may only be set when identity_status is UNIQUE"
            )


@dataclass(frozen=True)
class VenueCvdState:
    venue: str
    signed_base_volume: float = 0.0
    signed_notional_usd: float = 0.0
    gross_notional_usd: float = 0.0
    trade_count: int = 0
    excluded_unknown_base_volume: float = 0.0
    excluded_unknown_notional_usd: float = 0.0
    excluded_unknown_count: int = 0

    @property
    def has_directional_evidence(self) -> bool:
        return self.trade_count > 0


def empty_venue_cvd(venue: str) -> VenueCvdState:
    return VenueCvdState(venue=venue)


def accumulate_cvd(existing: VenueCvdState, observation: TradeObservation) -> VenueCvdState:
    """Fold one trade into venue-level CVD.

    BUY_AGGRESSOR adds, SELL_AGGRESSOR subtracts. UNKNOWN is excluded from the
    directional delta but tracked explicitly rather than silently discarded.
    """
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
    per_venue_signed_notional_usd: dict[str, float]
    per_venue_signed_base_volume: dict[str, float]
    agreement: VenueAgreementState
    quality: EvidenceQuality
    excluded_venues: tuple[str, ...] = ()


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

    if not venue_states:
        return CrossVenueCvdSnapshot(
            canonical_asset_id=canonical_asset_id,
            combined_signed_notional_usd=0.0,
            per_venue_signed_notional_usd={},
            per_venue_signed_base_volume={},
            agreement=VenueAgreementState.INSUFFICIENT_EVIDENCE,
            quality=EvidenceQuality(
                state=EvidenceQualityState.UNUSABLE,
                degradations=frozenset({"INCOMPLETE_VENUE_COVERAGE"}),
            ),
        )

    combined_notional = sum(state.signed_notional_usd for state in venue_states.values())
    per_venue_notional = {
        venue: state.signed_notional_usd for venue, state in venue_states.items()
    }
    per_venue_base = {
        venue: state.signed_base_volume for venue, state in venue_states.items()
    }

    combined_quality = combine_quality(list(venue_qualities.values()))

    confirmable_venues = [
        venue
        for venue, quality in venue_qualities.items()
        if can_independently_confirm(quality) and venue_states[venue].has_directional_evidence
    ]
    excluded = tuple(
        sorted(venue for venue in venue_states if venue not in confirmable_venues)
    )

    if len(confirmable_venues) == 0:
        agreement = VenueAgreementState.INSUFFICIENT_EVIDENCE
    elif len(confirmable_venues) == 1:
        polarity = _venue_polarity(
            venue_states[confirmable_venues[0]], neutral_ratio=neutral_notional_ratio
        )
        agreement = (
            VenueAgreementState.MIXED_NEUTRAL
            if polarity == 0
            else VenueAgreementState.INSUFFICIENT_EVIDENCE
        )
    else:
        polarities = {
            _venue_polarity(venue_states[venue], neutral_ratio=neutral_notional_ratio)
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
        if self.base_quantity < 0:
            raise ValueError("base_quantity cannot be negative")
        if self.notional_usd < 0:
            raise ValueError("notional_usd cannot be negative")
        require_utc(self.provider_timestamp_utc, field_name="provider_timestamp_utc")
        require_utc(self.ingest_timestamp_utc, field_name="ingest_timestamp_utc")
        if (
            self.identity_status != MappingStatus.UNIQUE
            and str(self.canonical_asset_id or "").strip()
        ):
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
    venue_participation: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.venue_participation is None:
            object.__setattr__(self, "venue_participation", {})

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
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    if not observations:
        return LiquidationSyncResult(
            state=LiquidationSyncState.INSUFFICIENT_EVIDENCE,
            window_seconds=window_seconds,
            participating_venues=(),
        )

    earliest_by_venue: dict[str, datetime] = {}
    for observation in observations:
        current = earliest_by_venue.get(observation.venue)
        if current is None or observation.provider_timestamp_utc < current:
            earliest_by_venue[observation.venue] = observation.provider_timestamp_utc

    venues = tuple(sorted(earliest_by_venue))
    if len(venues) < min_venues:
        return LiquidationSyncResult(
            state=LiquidationSyncState.INSUFFICIENT_EVIDENCE,
            window_seconds=window_seconds,
            participating_venues=venues,
        )

    timestamps = list(earliest_by_venue.values())
    delta = (max(timestamps) - min(timestamps)).total_seconds()
    state = (
        LiquidationSyncState.SYNCHRONIZED
        if delta <= window_seconds
        else LiquidationSyncState.NOT_SYNCHRONIZED
    )
    return LiquidationSyncResult(
        state=state,
        window_seconds=window_seconds,
        participating_venues=venues,
        max_pairwise_delta_seconds=delta,
    )
