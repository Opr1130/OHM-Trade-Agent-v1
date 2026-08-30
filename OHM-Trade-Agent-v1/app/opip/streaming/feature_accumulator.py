"""Bounded 15-second cross-venue feature accumulator for BUILD 4.4."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.opip.events.contract import MappingStatus
from app.opip.streaming.adapter import NormalizedStreamObservation
from app.opip.streaming.contract import (
    EvidenceQualityState,
    LiquidationSide,
    LiquidationSyncState,
    StreamType,
)
from app.opip.streaming.features import (
    LiquidationAggregate,
    LiquidationObservation,
    TradeObservation,
    VenueCvdState,
    accumulate_cvd,
    accumulate_liquidation,
    assess_liquidation_synchronization,
    combine_cross_venue,
    empty_liquidation_aggregate,
    empty_venue_cvd,
    normalize_trade_side,
)
from app.opip.streaming.quality import EvidenceQuality
from app.opip.streaming.sinks import SealedWindowNotice
from app.opip.streaming.windows import WindowBounds


FEATURE_WINDOW_SECONDS = 15


@dataclass(frozen=True)
class StreamingFeatureSnapshot:
    canonical_asset_id: str
    window_start_utc: datetime
    window_end_utc: datetime
    cvd_signed_notional_usd: float
    per_venue_cvd_notional_usd: dict[str, float]
    venue_agreement: str
    evidence_quality: str
    degradations: tuple[str, ...]
    liquidation_long_notional_usd: float
    liquidation_short_notional_usd: float
    liquidation_unknown_notional_usd: float
    liquidation_sync_state: str
    liquidation_venues: tuple[str, ...]
    liquidation_evidence_quality: str
    liquidation_degradations: tuple[str, ...]
    liquidation_confirmable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_asset_id": self.canonical_asset_id,
            "window_start_utc": self.window_start_utc.isoformat(),
            "window_end_utc": self.window_end_utc.isoformat(),
            "cvd_signed_notional_usd": self.cvd_signed_notional_usd,
            "per_venue_cvd_notional_usd": dict(self.per_venue_cvd_notional_usd),
            "venue_agreement": self.venue_agreement,
            "evidence_quality": self.evidence_quality,
            "degradations": list(self.degradations),
            "liquidation_long_notional_usd": self.liquidation_long_notional_usd,
            "liquidation_short_notional_usd": self.liquidation_short_notional_usd,
            "liquidation_unknown_notional_usd": self.liquidation_unknown_notional_usd,
            "liquidation_sync_state": self.liquidation_sync_state,
            "liquidation_venues": list(self.liquidation_venues),
            "liquidation_evidence_quality": self.liquidation_evidence_quality,
            "liquidation_degradations": list(self.liquidation_degradations),
            "liquidation_confirmable": self.liquidation_confirmable,
        }


@dataclass
class _FeatureBucket:
    asset: str
    bounds: WindowBounds
    venue_cvd: dict[str, VenueCvdState] = field(default_factory=dict)
    trade_quality: dict[str, EvidenceQuality] = field(default_factory=dict)
    liquidation: LiquidationAggregate | None = None
    latest_liquidation: dict[str, LiquidationObservation] = field(default_factory=dict)
    synchronized_seen: bool = False
    emitted: bool = False


class CrossVenueFeatureAccumulator:
    """Bounded deterministic feature state; never stores raw trade history."""

    def __init__(self, *, max_buckets: int = 96) -> None:
        if int(max_buckets) < 6:
            raise ValueError("max_buckets must be at least 6")
        self._max_buckets = int(max_buckets)
        self._buckets: dict[tuple[str, datetime], _FeatureBucket] = {}
        self._ready: deque[StreamingFeatureSnapshot] = deque()
        self.dropped_buckets = 0
        self.dropped_ready_snapshots = 0
        self.invalid_identity_observations = 0
        self.trade_observations_by_provider: dict[str, int] = {}
        self.liquidation_observations_by_provider: dict[str, int] = {}
        self.seal_notices_15s_by_provider: dict[str, int] = {}
        self.seal_bucket_misses = 0
        self.pair_emissions = 0
        self.latest_trade_timestamp_by_provider: dict[str, datetime] = {}
        self.latest_seal_start_by_provider: dict[str, datetime] = {}

    def record(self, normalized: NormalizedStreamObservation) -> None:
        env = normalized.envelope
        if (
            env.identity_status is not MappingStatus.UNIQUE
            or not env.canonical_asset_id
        ):
            self.invalid_identity_observations += 1
            return
        bounds = WindowBounds.for_timestamp(
            asset=env.canonical_asset_id,
            venue="CROSS_VENUE",
            timestamp_utc=env.provider_timestamp_utc,
            window_seconds=FEATURE_WINDOW_SECONDS,
        )
        key = (env.canonical_asset_id, bounds.start_utc)
        bucket = self._buckets.get(key)
        if bucket is None:
            self._ensure_capacity()
            bucket = _FeatureBucket(
                asset=env.canonical_asset_id,
                bounds=bounds,
                liquidation=empty_liquidation_aggregate(env.canonical_asset_id),
            )
            self._buckets[key] = bucket

        payload = env.payload
        if env.stream_type is StreamType.AGG_TRADE:
            provider = env.provider.value
            self.trade_observations_by_provider[provider] = (
                self.trade_observations_by_provider.get(provider, 0) + 1
            )
            self.latest_trade_timestamp_by_provider[provider] = max(
                env.provider_timestamp_utc,
                self.latest_trade_timestamp_by_provider.get(
                    provider, env.provider_timestamp_utc
                ),
            )
            observation = TradeObservation(
                canonical_asset_id=env.canonical_asset_id,
                identity_status=env.identity_status,
                venue=env.provider.value,
                side=normalize_trade_side(payload.get("aggressor_side")),
                base_quantity=float(payload["base_quantity"]),
                notional_usd=float(payload["notional_usd"]),
                provider_timestamp_utc=env.provider_timestamp_utc,
            )
            state = bucket.venue_cvd.get(env.provider.value)
            if state is None:
                state = empty_venue_cvd(env.provider.value)
            bucket.venue_cvd[env.provider.value] = accumulate_cvd(state, observation)
        elif env.stream_type is StreamType.LIQUIDATION:
            provider = env.provider.value
            self.liquidation_observations_by_provider[provider] = (
                self.liquidation_observations_by_provider.get(provider, 0) + 1
            )
            side = LiquidationSide(
                str(payload.get("liquidation_side") or LiquidationSide.UNKNOWN.value)
            )
            observation = LiquidationObservation(
                canonical_asset_id=env.canonical_asset_id,
                identity_status=env.identity_status,
                venue=env.provider.value,
                side=side,
                base_quantity=float(payload["base_quantity"]),
                notional_usd=float(payload["notional_usd"]),
                provider_timestamp_utc=env.provider_timestamp_utc,
                ingest_timestamp_utc=env.ingest_timestamp_utc,
            )
            assert bucket.liquidation is not None
            bucket.liquidation = accumulate_liquidation(
                bucket.liquidation, observation
            )
            bucket.latest_liquidation[env.provider.value] = observation
            if len(bucket.latest_liquidation) >= 2:
                sync = assess_liquidation_synchronization(
                    list(bucket.latest_liquidation.values()),
                    window_seconds=5.0,
                )
                if sync.state is LiquidationSyncState.SYNCHRONIZED:
                    bucket.synchronized_seen = True

    def seal(self, notice: SealedWindowNotice) -> None:
        if (
            notice.window_seconds != FEATURE_WINDOW_SECONDS
            or notice.stream_type is not StreamType.AGG_TRADE
            or not notice.canonical_asset_id
        ):
            return
        provider = notice.provider
        self.seal_notices_15s_by_provider[provider] = (
            self.seal_notices_15s_by_provider.get(provider, 0) + 1
        )
        self.latest_seal_start_by_provider[provider] = max(
            notice.start_utc,
            self.latest_seal_start_by_provider.get(provider, notice.start_utc),
        )
        key = (notice.canonical_asset_id, notice.start_utc)
        bucket = self._buckets.get(key)
        if bucket is None:
            self.seal_bucket_misses += 1
            return
        bucket.trade_quality[notice.provider] = notice.quality
        if bucket.emitted:
            return
        expected = {"BINANCE", "BYBIT"}
        if not expected.issubset(bucket.trade_quality):
            return

        cvd = combine_cross_venue(
            canonical_asset_id=bucket.asset,
            venue_states=bucket.venue_cvd,
            venue_qualities=bucket.trade_quality,
        )
        liquidation = bucket.liquidation or empty_liquidation_aggregate(bucket.asset)
        liq_venues = tuple(sorted(liquidation.venue_participation))
        if bucket.synchronized_seen:
            sync_state = LiquidationSyncState.SYNCHRONIZED
        elif len(liq_venues) >= 2:
            sync_state = LiquidationSyncState.NOT_SYNCHRONIZED
        else:
            sync_state = LiquidationSyncState.INSUFFICIENT_EVIDENCE

        if len(self._ready) >= self._max_buckets:
            self._ready.popleft()
            self.dropped_ready_snapshots += 1
        self._ready.append(
            StreamingFeatureSnapshot(
                canonical_asset_id=bucket.asset,
                window_start_utc=bucket.bounds.start_utc,
                window_end_utc=bucket.bounds.end_utc,
                cvd_signed_notional_usd=cvd.combined_signed_notional_usd,
                per_venue_cvd_notional_usd=dict(
                    cvd.per_venue_signed_notional_usd
                ),
                venue_agreement=cvd.agreement.value,
                evidence_quality=cvd.quality.state.value,
                degradations=tuple(sorted(cvd.quality.degradations)),
                liquidation_long_notional_usd=liquidation.long_notional_usd,
                liquidation_short_notional_usd=liquidation.short_notional_usd,
                liquidation_unknown_notional_usd=(
                    liquidation.unknown_side_notional_usd
                ),
                liquidation_sync_state=sync_state.value,
                liquidation_venues=liq_venues,
                # Neither Binance forceOrder nor Bybit allLiquidation carries
                # a continuity sequence. Synchronization is useful observed
                # evidence, but cannot independently confirm a decision.
                liquidation_evidence_quality=(
                    EvidenceQualityState.DEGRADED.value
                    if liquidation.total_notional_usd > 0
                    else EvidenceQualityState.INCOMPLETE.value
                ),
                liquidation_degradations=(
                    ("UNKNOWN_SEQUENCE",)
                    if liquidation.total_notional_usd > 0
                    else ("EMPTY_WINDOW",)
                ),
                liquidation_confirmable=False,
            )
        )
        bucket.emitted = True
        self.pair_emissions += 1

    def diagnostics(self) -> dict[str, Any]:
        missing_provider_counts = {"BINANCE": 0, "BYBIT": 0, "BOTH": 0}
        unsealed_pairable = 0
        for bucket in self._buckets.values():
            if bucket.emitted:
                continue
            providers = set(bucket.venue_cvd)
            quality_providers = set(bucket.trade_quality)
            if {"BINANCE", "BYBIT"}.issubset(providers):
                unsealed_pairable += 1
            missing = {"BINANCE", "BYBIT"} - quality_providers
            if missing == {"BINANCE"}:
                missing_provider_counts["BINANCE"] += 1
            elif missing == {"BYBIT"}:
                missing_provider_counts["BYBIT"] += 1
            elif missing == {"BINANCE", "BYBIT"}:
                missing_provider_counts["BOTH"] += 1
        return {
            "trade_observations_by_provider": dict(
                self.trade_observations_by_provider
            ),
            "liquidation_observations_by_provider": dict(
                self.liquidation_observations_by_provider
            ),
            "seal_notices_15s_by_provider": dict(
                self.seal_notices_15s_by_provider
            ),
            "latest_trade_timestamp_by_provider": {
                provider: value.isoformat()
                for provider, value in self.latest_trade_timestamp_by_provider.items()
            },
            "latest_seal_start_by_provider": {
                provider: value.isoformat()
                for provider, value in self.latest_seal_start_by_provider.items()
            },
            "active_feature_buckets": len(self._buckets),
            "ready_feature_snapshots": len(self._ready),
            "pair_emissions": self.pair_emissions,
            "seal_bucket_misses": self.seal_bucket_misses,
            "unsealed_pairable_buckets": unsealed_pairable,
            "missing_seal_provider_counts": missing_provider_counts,
        }

    def drain_ready(self) -> tuple[StreamingFeatureSnapshot, ...]:
        rows = tuple(self._ready)
        self._ready.clear()
        return rows

    def _ensure_capacity(self) -> None:
        if len(self._buckets) < self._max_buckets:
            return
        oldest = min(self._buckets, key=lambda item: item[1])
        self._buckets.pop(oldest, None)
        self.dropped_buckets += 1
