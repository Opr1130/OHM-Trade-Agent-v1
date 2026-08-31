"""BUILD 4.1 — trade-direction normalization, CVD, cross-venue, liquidations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.events.contract import MappingStatus
from app.opip.streaming.contract import (
    EvidenceQualityState,
    LiquidationSide,
    LiquidationSyncState,
    TradeSide,
    VenueAgreementState,
)
from app.opip.streaming.features import (
    LiquidationObservation,
    TradeObservation,
    accumulate_cvd,
    accumulate_liquidation,
    assess_liquidation_synchronization,
    combinable_identity,
    combine_cross_venue,
    empty_liquidation_aggregate,
    empty_venue_cvd,
    normalize_trade_side,
)
from app.opip.streaming.quality import COMPLETE, EvidenceQuality


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)

DEGRADED = EvidenceQuality(
    state=EvidenceQualityState.DEGRADED, degradations=frozenset({"UNKNOWN_SEQUENCE"})
)


# ------------------------------------------------------------ trade direction


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BUY", TradeSide.BUY_AGGRESSOR),
        ("buy", TradeSide.BUY_AGGRESSOR),
        ("SELL", TradeSide.SELL_AGGRESSOR),
        ("sell", TradeSide.SELL_AGGRESSOR),
    ],
)
def test_normalize_trade_side_known_values(raw, expected):
    assert normalize_trade_side(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "MAYBE", "BUYY", "0", "1"])
def test_normalize_trade_side_malformed_fails_closed_to_unknown(raw):
    assert normalize_trade_side(raw) == TradeSide.UNKNOWN


def test_unknown_never_defaults_directional():
    # A malformed value must never resolve to BUY or SELL by any path.
    for raw in (None, "", "garbage", "0", "-1", "unset"):
        assert normalize_trade_side(raw) not in (
            TradeSide.BUY_AGGRESSOR,
            TradeSide.SELL_AGGRESSOR,
        )


# ------------------------------------------------------------------ venue CVD


def _trade(side: TradeSide, base: float, notional: float, venue="BINANCE") -> TradeObservation:
    return TradeObservation(
        canonical_asset_id="bitcoin",
        identity_status=MappingStatus.UNIQUE,
        venue=venue,
        side=side,
        base_quantity=base,
        notional_usd=notional,
        provider_timestamp_utc=NOW,
    )


def test_positive_cvd_from_buy_pressure():
    state = accumulate_cvd(empty_venue_cvd("BINANCE", "bitcoin"), _trade(TradeSide.BUY_AGGRESSOR, 1.0, 100.0))
    assert state.signed_base_volume == 1.0
    assert state.signed_notional_usd == 100.0


def test_negative_cvd_from_sell_pressure():
    state = accumulate_cvd(empty_venue_cvd("BINANCE", "bitcoin"), _trade(TradeSide.SELL_AGGRESSOR, 1.0, 100.0))
    assert state.signed_base_volume == -1.0
    assert state.signed_notional_usd == -100.0


def test_mixed_trades_net_correctly():
    state = empty_venue_cvd("BINANCE", "bitcoin")
    state = accumulate_cvd(state, _trade(TradeSide.BUY_AGGRESSOR, 2.0, 200.0))
    state = accumulate_cvd(state, _trade(TradeSide.SELL_AGGRESSOR, 0.5, 50.0))
    assert state.signed_base_volume == pytest.approx(1.5)
    assert state.signed_notional_usd == pytest.approx(150.0)
    assert state.gross_notional_usd == pytest.approx(250.0)


def test_unknown_side_excluded_from_directional_delta_but_tracked():
    state = empty_venue_cvd("BINANCE", "bitcoin")
    state = accumulate_cvd(state, _trade(TradeSide.BUY_AGGRESSOR, 1.0, 100.0))
    state = accumulate_cvd(state, _trade(TradeSide.UNKNOWN, 5.0, 500.0))
    assert state.signed_base_volume == 1.0
    assert state.signed_notional_usd == 100.0
    assert state.excluded_unknown_base_volume == 5.0
    assert state.excluded_unknown_notional_usd == 500.0
    assert state.excluded_unknown_count == 1


def test_cvd_uses_deterministic_floating_point_accumulation():
    state = empty_venue_cvd("BINANCE", "bitcoin")
    for _ in range(3):
        state = accumulate_cvd(state, _trade(TradeSide.BUY_AGGRESSOR, 0.1, 10.0))
    assert state.signed_base_volume == pytest.approx(0.3, abs=1e-9)


def test_venue_mismatch_rejected():
    state = empty_venue_cvd("BINANCE", "bitcoin")
    with pytest.raises(ValueError):
        accumulate_cvd(state, _trade(TradeSide.BUY_AGGRESSOR, 1.0, 100.0, venue="BYBIT"))


def test_trade_observation_identity_fails_closed():
    with pytest.raises(ValueError):
        TradeObservation(
            canonical_asset_id="bitcoin",
            identity_status=MappingStatus.UNKNOWN,
            venue="BINANCE",
            side=TradeSide.BUY_AGGRESSOR,
            base_quantity=1.0,
            notional_usd=1.0,
            provider_timestamp_utc=NOW,
        )


# -------------------------------------------------------------- cross-venue


def test_identity_mismatch_cannot_combine():
    assert (
        combinable_identity(
            left_status=MappingStatus.UNIQUE,
            left_canonical_id="bitcoin",
            right_status=MappingStatus.UNIQUE,
            right_canonical_id="ethereum",
        )
        is False
    )


def test_identity_ambiguous_cannot_combine():
    assert (
        combinable_identity(
            left_status=MappingStatus.UNIQUE,
            left_canonical_id="bitcoin",
            right_status=MappingStatus.AMBIGUOUS,
            right_canonical_id=None,
        )
        is False
    )


def test_identity_unknown_cannot_combine():
    assert (
        combinable_identity(
            left_status=MappingStatus.UNKNOWN,
            left_canonical_id=None,
            right_status=MappingStatus.UNKNOWN,
            right_canonical_id=None,
        )
        is False
    )


def test_symbol_similarity_alone_cannot_create_identity():
    """Two venues both happening to use a 'BTC'-looking symbol is not
    identity: only matching UNIQUE canonical ids may combine."""
    assert (
        combinable_identity(
            left_status=MappingStatus.UNKNOWN,
            left_canonical_id="BTC",
            right_status=MappingStatus.UNKNOWN,
            right_canonical_id="BTC",
        )
        is False
    )


def test_matching_unique_identity_combines():
    assert (
        combinable_identity(
            left_status=MappingStatus.UNIQUE,
            left_canonical_id="bitcoin",
            right_status=MappingStatus.UNIQUE,
            right_canonical_id="bitcoin",
        )
        is True
    )


def _venue_state(notional: float, base: float = 1.0, venue="BINANCE", gross=None):
    state = empty_venue_cvd(venue, "bitcoin")
    side = TradeSide.BUY_AGGRESSOR if notional >= 0 else TradeSide.SELL_AGGRESSOR
    return accumulate_cvd(state, _trade(side, base, abs(notional), venue=venue))


def test_cross_venue_aligned_bullish():
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={
            "BINANCE": _venue_state(1000.0, venue="BINANCE"),
            "BYBIT": _venue_state(500.0, venue="BYBIT"),
        },
        venue_qualities={"BINANCE": COMPLETE, "BYBIT": COMPLETE},
    )
    assert snapshot.agreement == VenueAgreementState.ALIGNED_POSITIVE
    assert snapshot.combined_signed_notional_usd == pytest.approx(1500.0)


def test_cross_venue_aligned_bearish():
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={
            "BINANCE": _venue_state(-1000.0, venue="BINANCE"),
            "BYBIT": _venue_state(-500.0, venue="BYBIT"),
        },
        venue_qualities={"BINANCE": COMPLETE, "BYBIT": COMPLETE},
    )
    assert snapshot.agreement == VenueAgreementState.ALIGNED_NEGATIVE


def test_cross_venue_disagreement():
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={
            "BINANCE": _venue_state(1000.0, venue="BINANCE"),
            "BYBIT": _venue_state(-1000.0, venue="BYBIT"),
        },
        venue_qualities={"BINANCE": COMPLETE, "BYBIT": COMPLETE},
    )
    assert snapshot.agreement == VenueAgreementState.DISAGREEMENT


def test_cross_venue_one_venue_missing_is_insufficient_for_agreement():
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={"BINANCE": _venue_state(1000.0, venue="BINANCE")},
        venue_qualities={"BINANCE": COMPLETE},
    )
    assert snapshot.agreement == VenueAgreementState.INSUFFICIENT_EVIDENCE
    assert snapshot.excluded_venues == ()


def test_cross_venue_one_venue_degraded_excludes_it_from_agreement():
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={
            "BINANCE": _venue_state(1000.0, venue="BINANCE"),
            "BYBIT": _venue_state(-1000.0, venue="BYBIT"),
        },
        venue_qualities={"BINANCE": COMPLETE, "BYBIT": DEGRADED},
    )
    # Only one venue is independently confirmable, so agreement cannot be
    # asserted even though the combined figure still reflects both venues.
    assert snapshot.agreement == VenueAgreementState.INSUFFICIENT_EVIDENCE
    assert "BYBIT" in snapshot.excluded_venues
    assert snapshot.combined_signed_notional_usd == pytest.approx(0.0)
    assert snapshot.quality.state != EvidenceQualityState.COMPLETE


def test_cross_venue_no_venues_is_unusable():
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin", venue_states={}, venue_qualities={}
    )
    assert snapshot.agreement == VenueAgreementState.INSUFFICIENT_EVIDENCE
    assert snapshot.quality.state == EvidenceQualityState.UNUSABLE


def test_cross_venue_normalized_notional_not_raw_volume_sum():
    """Base-volume sums would be misleading across venues with different
    contract conventions; combination must happen on notional terms."""
    binance = accumulate_cvd(
        empty_venue_cvd("BINANCE", "bitcoin"),
        _trade(TradeSide.BUY_AGGRESSOR, 1.0, 60000.0, venue="BINANCE"),
    )
    bybit = accumulate_cvd(
        empty_venue_cvd("BYBIT", "bitcoin"),
        _trade(TradeSide.BUY_AGGRESSOR, 100.0, 60000.0, venue="BYBIT"),
    )
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={"BINANCE": binance, "BYBIT": bybit},
        venue_qualities={"BINANCE": COMPLETE, "BYBIT": COMPLETE},
    )
    assert snapshot.combined_signed_notional_usd == pytest.approx(120000.0)
    assert snapshot.agreement == VenueAgreementState.ALIGNED_POSITIVE


def test_cross_venue_mixed_neutral_when_both_within_neutral_band():
    tiny = accumulate_cvd(
        empty_venue_cvd("BINANCE", "bitcoin"),
        _trade(TradeSide.BUY_AGGRESSOR, 0.001, 1.0, venue="BINANCE"),
    )
    tiny = accumulate_cvd(
        tiny, _trade(TradeSide.SELL_AGGRESSOR, 0.001, 0.99, venue="BINANCE")
    )
    other = accumulate_cvd(
        empty_venue_cvd("BYBIT", "bitcoin"), _trade(TradeSide.BUY_AGGRESSOR, 0.001, 1.0, venue="BYBIT")
    )
    other = accumulate_cvd(
        other, _trade(TradeSide.SELL_AGGRESSOR, 0.001, 0.99, venue="BYBIT")
    )
    snapshot = combine_cross_venue(
        canonical_asset_id="bitcoin",
        venue_states={"BINANCE": tiny, "BYBIT": other},
        venue_qualities={"BINANCE": COMPLETE, "BYBIT": COMPLETE},
    )
    assert snapshot.agreement == VenueAgreementState.MIXED_NEUTRAL


# --------------------------------------------------------------- liquidations


def _liq(side: LiquidationSide, notional: float, venue="BINANCE", ts=NOW) -> LiquidationObservation:
    return LiquidationObservation(
        canonical_asset_id="bitcoin",
        identity_status=MappingStatus.UNIQUE,
        venue=venue,
        side=side,
        base_quantity=notional / 60000.0,
        notional_usd=notional,
        provider_timestamp_utc=ts,
        ingest_timestamp_utc=ts,
    )


def test_long_liquidation_accumulates():
    agg = accumulate_liquidation(
        empty_liquidation_aggregate("bitcoin"), _liq(LiquidationSide.LONG_LIQUIDATION, 1000.0)
    )
    assert agg.long_notional_usd == 1000.0
    assert agg.short_notional_usd == 0.0


def test_short_liquidation_accumulates():
    agg = accumulate_liquidation(
        empty_liquidation_aggregate("bitcoin"), _liq(LiquidationSide.SHORT_LIQUIDATION, 1000.0)
    )
    assert agg.short_notional_usd == 1000.0


def test_liquidation_aggregation_and_imbalance():
    agg = empty_liquidation_aggregate("bitcoin")
    agg = accumulate_liquidation(agg, _liq(LiquidationSide.LONG_LIQUIDATION, 1500.0, venue="BINANCE"))
    agg = accumulate_liquidation(agg, _liq(LiquidationSide.SHORT_LIQUIDATION, 500.0, venue="BYBIT"))
    assert agg.imbalance_notional_usd == pytest.approx(1000.0)
    assert agg.venue_participation == {"BINANCE": 1, "BYBIT": 1}


def test_liquidation_unknown_side_tracked_not_dropped():
    agg = accumulate_liquidation(
        empty_liquidation_aggregate("bitcoin"), _liq(LiquidationSide.UNKNOWN, 1000.0)
    )
    assert agg.unknown_side_notional_usd == 1000.0
    assert agg.unknown_side_count == 1
    assert agg.imbalance_notional_usd == 0.0


def test_liquidation_asset_mismatch_rejected():
    with pytest.raises(ValueError):
        accumulate_liquidation(
            empty_liquidation_aggregate("bitcoin"),
            LiquidationObservation(
                canonical_asset_id="ethereum",
                identity_status=MappingStatus.UNIQUE,
                venue="BINANCE",
                side=LiquidationSide.LONG_LIQUIDATION,
                base_quantity=1.0,
                notional_usd=1.0,
                provider_timestamp_utc=NOW,
                ingest_timestamp_utc=NOW,
            ),
        )


def test_liquidation_synchronized_across_venues_within_window():
    obs = [
        _liq(LiquidationSide.LONG_LIQUIDATION, 1000.0, venue="BINANCE", ts=NOW),
        _liq(LiquidationSide.LONG_LIQUIDATION, 900.0, venue="BYBIT", ts=NOW + timedelta(seconds=2)),
    ]
    result = assess_liquidation_synchronization(obs, window_seconds=5)
    assert result.state == LiquidationSyncState.SYNCHRONIZED
    assert set(result.participating_venues) == {"BINANCE", "BYBIT"}


def test_liquidation_outside_synchronization_window():
    obs = [
        _liq(LiquidationSide.LONG_LIQUIDATION, 1000.0, venue="BINANCE", ts=NOW),
        _liq(LiquidationSide.LONG_LIQUIDATION, 900.0, venue="BYBIT", ts=NOW + timedelta(seconds=30)),
    ]
    result = assess_liquidation_synchronization(obs, window_seconds=5)
    assert result.state == LiquidationSyncState.NOT_SYNCHRONIZED


def test_liquidation_synchronization_insufficient_with_one_venue():
    obs = [_liq(LiquidationSide.LONG_LIQUIDATION, 1000.0, venue="BINANCE", ts=NOW)]
    result = assess_liquidation_synchronization(obs, window_seconds=5)
    assert result.state == LiquidationSyncState.INSUFFICIENT_EVIDENCE


def test_liquidation_synchronization_insufficient_with_no_evidence():
    result = assess_liquidation_synchronization([], window_seconds=5)
    assert result.state == LiquidationSyncState.INSUFFICIENT_EVIDENCE
    assert result.participating_venues == ()


def test_liquidation_synchronization_window_must_be_positive():
    with pytest.raises(ValueError):
        assess_liquidation_synchronization([], window_seconds=0)


def test_degraded_liquidation_evidence_is_caller_filtered_not_silently_included():
    """This module reasons only about what it is given; a caller responsible
    for filtering degraded evidence keeps a degraded observation out."""
    all_obs = [
        _liq(LiquidationSide.LONG_LIQUIDATION, 1000.0, venue="BINANCE", ts=NOW),
        _liq(LiquidationSide.LONG_LIQUIDATION, 900.0, venue="BYBIT", ts=NOW + timedelta(seconds=2)),
    ]
    degraded_venues = {"BYBIT"}
    filtered = [item for item in all_obs if item.venue not in degraded_venues]
    result = assess_liquidation_synchronization(filtered, window_seconds=5)
    assert result.state == LiquidationSyncState.INSUFFICIENT_EVIDENCE
    assert result.participating_venues == ("BINANCE",)


def test_cvd_rejects_cross_asset_observation():
    state = empty_venue_cvd("BINANCE", "bitcoin")
    observation = TradeObservation(
        canonical_asset_id="ethereum",
        identity_status=MappingStatus.UNIQUE,
        venue="BINANCE",
        side=TradeSide.BUY_AGGRESSOR,
        base_quantity=1.0,
        notional_usd=100.0,
        provider_timestamp_utc=NOW,
    )
    with pytest.raises(ValueError, match="asset"):
        accumulate_cvd(state, observation)


def test_cross_venue_rejects_state_for_different_asset():
    state = empty_venue_cvd("BINANCE", "ethereum")
    with pytest.raises(ValueError, match="asset"):
        combine_cross_venue(
            canonical_asset_id="bitcoin",
            venue_states={"BINANCE": state},
            venue_qualities={"BINANCE": COMPLETE},
        )
