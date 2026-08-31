"""Adversarial remediation tests for Sequence 4 BUILD 4.1 review findings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest

from app.opip.events.contract import MappingStatus
from app.opip.streaming.contract import (
    EvidenceQualityState,
    LiquidationSide,
    LiquidationSyncState,
    SequenceStatus,
    StreamProvider,
    StreamType,
    TradeSide,
    VenueAgreementState,
)
from app.opip.streaming.envelope import StreamEnvelope
from app.opip.streaming.features import (
    LiquidationObservation,
    TradeObservation,
    VenueCvdState,
    assess_liquidation_synchronization,
    combine_cross_venue,
    empty_venue_cvd,
)
from app.opip.streaming.quality import COMPLETE
from app.opip.streaming.sequencing import NonDecreasingSequenceTracker
from app.opip.streaming.windows import WindowBounds


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _env(payload=None, quality=None) -> StreamEnvelope:
    return StreamEnvelope(
        provider=StreamProvider.BINANCE,
        stream_type=StreamType.AGG_TRADE,
        provider_symbol="BTCUSDT",
        provider_timestamp_utc=NOW,
        ingest_timestamp_utc=NOW,
        connection_id="c1",
        reconnect_epoch=0,
        sequence_status=SequenceStatus.FIRST,
        is_aggregate=True,
        identity_status=MappingStatus.UNIQUE,
        canonical_asset_id="bitcoin",
        payload=payload or {},
        quality=quality or {},
    )


def _liq(asset: str, venue: str, ts: datetime) -> LiquidationObservation:
    return LiquidationObservation(
        canonical_asset_id=asset,
        identity_status=MappingStatus.UNIQUE,
        venue=venue,
        side=LiquidationSide.LONG_LIQUIDATION,
        base_quantity=1.0,
        notional_usd=100.0,
        provider_timestamp_utc=ts,
        ingest_timestamp_utc=ts,
    )


def test_envelope_is_deeply_immutable_and_source_mutation_cannot_change_bytes():
    source = {"price": 10.0, "nested": {"levels": [1, 2]}}
    env = _env(payload=source)
    before = env.canonical_bytes()

    source["price"] = 99.0
    source["nested"]["levels"].append(3)

    assert env.canonical_bytes() == before
    with pytest.raises(TypeError):
        env.payload["price"] = 11.0
    with pytest.raises(TypeError):
        env.payload["nested"]["x"] = 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_envelope_rejects_nonfinite_nested_numeric_evidence(bad):
    with pytest.raises(ValueError):
        _env(payload={"nested": {"bad": bad}})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["base_quantity", "notional_usd"])
def test_trade_observation_rejects_nonfinite_values(field, bad):
    values = dict(
        canonical_asset_id="bitcoin",
        identity_status=MappingStatus.UNIQUE,
        venue="BINANCE",
        side=TradeSide.BUY_AGGRESSOR,
        base_quantity=1.0,
        notional_usd=100.0,
        provider_timestamp_utc=NOW,
    )
    values[field] = bad
    with pytest.raises(ValueError):
        TradeObservation(**values)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["base_quantity", "notional_usd"])
def test_liquidation_observation_rejects_nonfinite_values(field, bad):
    values = dict(
        canonical_asset_id="bitcoin",
        identity_status=MappingStatus.UNIQUE,
        venue="BINANCE",
        side=LiquidationSide.LONG_LIQUIDATION,
        base_quantity=1.0,
        notional_usd=100.0,
        provider_timestamp_utc=NOW,
        ingest_timestamp_utc=NOW,
    )
    values[field] = bad
    with pytest.raises(ValueError):
        LiquidationObservation(**values)


def test_venue_cvd_state_rejects_nan_poisoning():
    with pytest.raises(ValueError):
        VenueCvdState(venue="BINANCE", canonical_asset_id="bitcoin", signed_notional_usd=float("nan"))


def test_cross_venue_missing_quality_fails_closed_without_keyerror():
    states = {
        "BINANCE": VenueCvdState(
            venue="BINANCE",
            canonical_asset_id="bitcoin",
            signed_base_volume=1.0,
            signed_notional_usd=100.0,
            gross_notional_usd=100.0,
            trade_count=1,
        ),
        "BYBIT": VenueCvdState(
            venue="BYBIT",
            canonical_asset_id="bitcoin",
            signed_base_volume=1.0,
            signed_notional_usd=100.0,
            gross_notional_usd=100.0,
            trade_count=1,
        ),
    }
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states=states,
        venue_qualities={"BINANCE": COMPLETE},
    )
    assert snapshot.quality.state == EvidenceQualityState.INCOMPLETE
    assert snapshot.agreement == VenueAgreementState.INSUFFICIENT_EVIDENCE
    assert "BYBIT" in snapshot.excluded_venues


def test_cross_venue_extra_quality_fails_closed_without_keyerror():
    states = {
        "BINANCE": VenueCvdState(
            venue="BINANCE",
            canonical_asset_id="bitcoin",
            signed_base_volume=1.0,
            signed_notional_usd=100.0,
            gross_notional_usd=100.0,
            trade_count=1,
        )
    }
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states=states,
        venue_qualities={"BINANCE": COMPLETE, "BYBIT": COMPLETE},
    )
    assert snapshot.quality.state == EvidenceQualityState.INCOMPLETE
    assert snapshot.agreement == VenueAgreementState.INSUFFICIENT_EVIDENCE


def test_cross_venue_snapshot_maps_are_immutable():
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={
            "BINANCE": VenueCvdState(
                venue="BINANCE",
                signed_base_volume=1.0,
                signed_notional_usd=100.0,
                gross_notional_usd=100.0,
                trade_count=1,
            )
        },
        venue_qualities={"BINANCE": COMPLETE},
    )
    with pytest.raises(TypeError):
        snapshot.per_venue_signed_notional_usd["BINANCE"] = 0.0


def test_non_decreasing_repeated_sequence_is_valid_not_message_duplicate():
    tracker = NonDecreasingSequenceTracker()
    assert tracker.observe("100", reconnect_epoch=0).status == SequenceStatus.FIRST
    assert tracker.observe("101", reconnect_epoch=0).status == SequenceStatus.CONTIGUOUS
    assert tracker.observe("101", reconnect_epoch=0).status == SequenceStatus.CONTIGUOUS
    assert tracker.observe("103", reconnect_epoch=0).status == SequenceStatus.CONTIGUOUS
    assert tracker.observe("102", reconnect_epoch=0).status == SequenceStatus.OUT_OF_ORDER


def test_15_second_window_microsecond_boundary_is_exact():
    before = datetime(2026, 8, 29, 12, 0, 14, 999999, tzinfo=timezone.utc)
    edge = datetime(2026, 8, 29, 12, 0, 15, 0, tzinfo=timezone.utc)
    after = datetime(2026, 8, 29, 12, 0, 15, 1, tzinfo=timezone.utc)

    a = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=before, window_seconds=15
    )
    b = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=edge, window_seconds=15
    )
    c = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=after, window_seconds=15
    )
    assert a.end_utc == edge
    assert b.start_utc == edge
    assert c.start_utc == edge


def test_equivalent_non_utc_instant_maps_to_same_window():
    local = datetime.fromisoformat("2026-08-29T08:00:15-04:00")
    utc = datetime.fromisoformat("2026-08-29T12:00:15+00:00")
    left = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=local, window_seconds=15
    )
    right = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=utc, window_seconds=15
    )
    assert left == right


def test_liquidation_sync_never_combines_different_assets():
    result = assess_liquidation_synchronization(
        [
            _liq("bitcoin", "BINANCE", NOW),
            _liq("ethereum", "BYBIT", NOW + timedelta(seconds=1)),
        ],
        window_seconds=5,
    )
    assert result.state == LiquidationSyncState.INSUFFICIENT_EVIDENCE


def test_liquidation_sync_detects_later_cross_venue_burst_not_only_earliest_events():
    result = assess_liquidation_synchronization(
        [
            _liq("bitcoin", "BINANCE", NOW),
            _liq("bitcoin", "BINANCE", NOW + timedelta(seconds=100)),
            _liq("bitcoin", "BYBIT", NOW + timedelta(seconds=101)),
        ],
        window_seconds=5,
    )
    assert result.state == LiquidationSyncState.SYNCHRONIZED
    assert result.max_pairwise_delta_seconds == pytest.approx(1.0)
