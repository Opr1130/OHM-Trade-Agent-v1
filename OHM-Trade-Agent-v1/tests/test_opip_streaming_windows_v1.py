"""BUILD 4.1 — PIT sliding-window contracts and closure semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.events.contract import MappingStatus
from app.opip.streaming.contract import ArrivalDecision, SequenceStatus, StreamProvider, StreamType
from app.opip.streaming.envelope import StreamEnvelope
from app.opip.streaming.sequencing import SequenceObservation
from app.opip.streaming.windows import WindowBounds, empty_window, route_observation


NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _env(ts: datetime, *, status=SequenceStatus.CONTIGUOUS, **flags) -> StreamEnvelope:
    return StreamEnvelope(
        provider=StreamProvider.BINANCE,
        stream_type=StreamType.AGG_TRADE,
        provider_symbol="BTCUSDT",
        provider_timestamp_utc=ts,
        ingest_timestamp_utc=ts + timedelta(milliseconds=10),
        connection_id="conn-1",
        reconnect_epoch=0,
        sequence_status=status,
        is_aggregate=True,
        identity_status=MappingStatus.UNIQUE,
        canonical_asset_id="bitcoin",
        **flags,
    )


def _obs(status=SequenceStatus.CONTIGUOUS) -> SequenceObservation:
    return SequenceObservation(
        status=status,
        sequence_value="1",
        previous_sequence_value="0",
        reconnect_epoch=0,
        epoch_changed=False,
    )


# ------------------------------------------------------------------- bounds


def test_exact_one_second_boundary():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    assert bounds.start_utc == NOW
    assert bounds.end_utc == NOW + timedelta(seconds=1)


def test_exact_fifteen_second_boundary():
    ts = datetime(2026, 8, 29, 12, 0, 22, tzinfo=timezone.utc)
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=ts, window_seconds=15
    )
    assert bounds.start_utc == datetime(2026, 8, 29, 12, 0, 15, tzinfo=timezone.utc)
    assert bounds.end_utc == datetime(2026, 8, 29, 12, 0, 30, tzinfo=timezone.utc)


def test_event_exactly_on_boundary_belongs_to_the_new_window():
    boundary = datetime(2026, 8, 29, 12, 0, 15, tzinfo=timezone.utc)
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=boundary, window_seconds=15
    )
    assert bounds.start_utc == boundary
    assert bounds.contains(boundary) is True
    prior_bounds = WindowBounds.for_timestamp(
        asset="bitcoin",
        venue="BINANCE",
        timestamp_utc=boundary - timedelta(microseconds=1),
        window_seconds=15,
    )
    assert prior_bounds.end_utc == boundary
    assert prior_bounds.contains(boundary) is False


def test_grace_period_controls_sealability():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    just_after_end = bounds.end_utc + timedelta(milliseconds=1)
    assert bounds.is_sealable(now_utc=just_after_end, grace_seconds=2) is False
    assert bounds.is_sealable(now_utc=bounds.end_utc + timedelta(seconds=2), grace_seconds=2) is True


def test_sealability_uses_local_clock_not_provider_clock():
    """A provider clock anomaly must not prevent closure: sealability is a
    pure function of the caller-supplied now_utc, never of provider data."""
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    far_future_local_clock = bounds.end_utc + timedelta(hours=1)
    assert bounds.is_sealable(now_utc=far_future_local_clock, grace_seconds=1) is True


def test_window_bounds_reject_mismatched_end():
    with pytest.raises(ValueError):
        WindowBounds(
            asset="bitcoin",
            venue="BINANCE",
            window_seconds=1,
            start_utc=NOW,
            end_utc=NOW + timedelta(seconds=2),
        )


# --------------------------------------------------------------- accumulator


def test_empty_window_semantics():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    window = empty_window(bounds)
    assert window.is_empty is True
    assert window.observation_count == 0
    assert window.first_provider_timestamp_utc is None


def test_recording_updates_bounded_counters_not_a_raw_list():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    window = empty_window(bounds)
    window = window.record(_env(NOW), _obs(SequenceStatus.CONTIGUOUS))
    window = window.record(_env(NOW + timedelta(milliseconds=500)), _obs(SequenceStatus.GAP))
    assert window.observation_count == 2
    assert window.contiguous_count == 1
    assert window.gap_count == 1
    assert not hasattr(window, "raw_events")


def test_recording_outside_window_bounds_is_rejected():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    window = empty_window(bounds)
    outside = _env(NOW + timedelta(seconds=5))
    with pytest.raises(ValueError):
        window.record(outside, _obs())


def test_out_of_order_event_before_seal_is_still_recorded():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    window = empty_window(bounds)
    window = window.record(
        _env(NOW + timedelta(milliseconds=100)), _obs(SequenceStatus.CONTIGUOUS)
    )
    window = window.record(
        _env(NOW + timedelta(milliseconds=50), status=SequenceStatus.OUT_OF_ORDER, out_of_order=True),
        _obs(SequenceStatus.OUT_OF_ORDER),
    )
    assert window.observation_count == 2
    assert window.out_of_order_count == 1


def test_no_retroactive_mutation_of_a_sealed_window():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    window = empty_window(bounds).record(_env(NOW), _obs())
    sealed = window.seal()
    assert sealed.sealed is True
    with pytest.raises(ValueError):
        sealed.record(_env(NOW + timedelta(milliseconds=1)), _obs())


def test_late_event_after_seal_is_classified_not_dropped_silently():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    sealed = empty_window(bounds).record(_env(NOW), _obs()).seal()
    frozen_observation_count = sealed.observation_count

    updated = sealed.record_late_frame()
    assert updated.late_frame_count == 1
    # The sealed aggregate's own history is untouched by the late arrival.
    assert updated.observation_count == frozen_observation_count
    assert updated.sealed is True


def test_seal_is_idempotent():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    window = empty_window(bounds).seal()
    assert window.seal() == window


def test_dropped_frame_accounted_without_inflating_observation_count():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    window = empty_window(bounds).record_dropped_frame()
    assert window.dropped_frame_count == 1
    assert window.observation_count == 0


# ------------------------------------------------------------------- routing


def test_route_observation_new_window_when_none_held():
    decision, bounds = route_observation(
        current=None, envelope=_env(NOW), seq_obs=_obs(), window_seconds=1
    )
    assert decision == ArrivalDecision.ACCEPTED_NEW_WINDOW


def test_route_observation_accepted_open_for_same_window():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    current = empty_window(bounds)
    decision, target = route_observation(
        current=current,
        envelope=_env(NOW + timedelta(milliseconds=500)),
        seq_obs=_obs(),
        window_seconds=1,
    )
    assert decision == ArrivalDecision.ACCEPTED_OPEN
    assert target == bounds


def test_route_observation_new_window_when_boundary_crossed():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    current = empty_window(bounds)
    decision, target = route_observation(
        current=current,
        envelope=_env(NOW + timedelta(seconds=2)),
        seq_obs=_obs(),
        window_seconds=1,
    )
    assert decision == ArrivalDecision.ACCEPTED_NEW_WINDOW
    assert target != bounds


def test_route_observation_late_after_seal_for_same_target():
    bounds = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )
    sealed = empty_window(bounds).seal()
    decision, target = route_observation(
        current=sealed,
        envelope=_env(NOW + timedelta(milliseconds=999)),
        seq_obs=_obs(),
        window_seconds=1,
    )
    assert decision == ArrivalDecision.LATE_AFTER_SEAL
    assert target == bounds


def test_windows_are_grid_aligned_independent_of_first_arrival():
    """Two independently-started series must agree on window boundaries."""
    ts_a = NOW + timedelta(milliseconds=730)
    ts_b = NOW + timedelta(milliseconds=10)
    bounds_a = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=ts_a, window_seconds=1
    )
    bounds_b = WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=ts_b, window_seconds=1
    )
    assert bounds_a == bounds_b
