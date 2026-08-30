"""BUILD 4.1 — evidence-quality contract and fail-closed confirmation rule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.events.contract import MappingStatus
from app.opip.streaming.contract import EvidenceQualityState, SequenceStatus, StreamProvider, StreamType
from app.opip.streaming.envelope import StreamEnvelope
from app.opip.streaming.quality import (
    COMPLETE,
    EvidenceQuality,
    assess_window_quality,
    can_independently_confirm,
    combine_quality,
)
from app.opip.streaming.sequencing import SequenceObservation
from app.opip.streaming.windows import WindowBounds, empty_window


NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _env(ts: datetime, *, status=SequenceStatus.CONTIGUOUS, **flags) -> StreamEnvelope:
    return StreamEnvelope(
        provider=StreamProvider.BINANCE,
        stream_type=StreamType.AGG_TRADE,
        provider_symbol="BTCUSDT",
        provider_timestamp_utc=ts,
        ingest_timestamp_utc=ts,
        connection_id="conn-1",
        reconnect_epoch=0,
        sequence_status=status,
        is_aggregate=True,
        identity_status=MappingStatus.UNIQUE,
        canonical_asset_id="bitcoin",
        **flags,
    )


def _obs(status) -> SequenceObservation:
    return SequenceObservation(
        status=status,
        sequence_value="1",
        previous_sequence_value="0",
        reconnect_epoch=0,
        epoch_changed=(status == SequenceStatus.RESET_NEW_EPOCH),
    )


def _bounds() -> WindowBounds:
    return WindowBounds.for_timestamp(
        asset="bitcoin", venue="BINANCE", timestamp_utc=NOW, window_seconds=1
    )


def test_complete_window_has_no_degradations():
    window = empty_window(_bounds())
    for offset in range(5):
        window = window.record(
            _env(NOW + timedelta(milliseconds=offset * 100)), _obs(SequenceStatus.CONTIGUOUS)
        )
    quality = assess_window_quality(window)
    assert quality.state == EvidenceQualityState.COMPLETE
    assert quality.degradations == frozenset()
    assert can_independently_confirm(quality) is True


def test_sequence_gap_degrades_to_incomplete():
    window = empty_window(_bounds())
    window = window.record(
        _env(NOW, status=SequenceStatus.GAP, gap_before=True), _obs(SequenceStatus.GAP)
    )
    quality = assess_window_quality(window)
    assert quality.state == EvidenceQualityState.INCOMPLETE
    assert "SEQUENCE_GAP" in quality.degradations
    assert can_independently_confirm(quality) is False


def test_dropped_frames_above_threshold_degrade_to_incomplete():
    window = empty_window(_bounds())
    window = window.record(_env(NOW), _obs(SequenceStatus.CONTIGUOUS))
    for _ in range(10):
        window = window.record_dropped_frame()
    quality = assess_window_quality(window, max_dropped_frame_ratio=0.05)
    assert quality.state == EvidenceQualityState.INCOMPLETE
    assert "FRAMES_DROPPED" in quality.degradations


def test_incomplete_venue_evidence_via_empty_window_input():
    window = empty_window(_bounds())
    quality = assess_window_quality(window)
    assert quality.state == EvidenceQualityState.INCOMPLETE
    assert "EMPTY_WINDOW" in quality.degradations


def test_unknown_sequence_marks_degraded():
    window = empty_window(_bounds())
    window = window.record(_env(NOW), _obs(SequenceStatus.UNSUPPORTED))
    quality = assess_window_quality(window)
    assert quality.state == EvidenceQualityState.DEGRADED
    assert "UNKNOWN_SEQUENCE" in quality.degradations


def test_reconnect_boundary_marks_degraded_not_gap():
    window = empty_window(_bounds())
    window = window.record(_env(NOW), _obs(SequenceStatus.RESET_NEW_EPOCH))
    quality = assess_window_quality(window)
    assert "RECONNECT_BOUNDARY" in quality.degradations
    assert "SEQUENCE_GAP" not in quality.degradations


def test_quality_aggregation_takes_the_worst_and_unions_reasons():
    complete = COMPLETE
    degraded = EvidenceQuality(
        state=EvidenceQualityState.DEGRADED, degradations=frozenset({"UNKNOWN_SEQUENCE"})
    )
    incomplete = EvidenceQuality(
        state=EvidenceQualityState.INCOMPLETE, degradations=frozenset({"SEQUENCE_GAP"})
    )
    combined = combine_quality([complete, degraded, incomplete])
    assert combined.state == EvidenceQualityState.INCOMPLETE
    assert combined.degradations == frozenset({"UNKNOWN_SEQUENCE", "SEQUENCE_GAP"})


def test_combining_no_quality_inputs_is_unusable_not_complete():
    combined = combine_quality([])
    assert combined.state == EvidenceQualityState.UNUSABLE
    assert can_independently_confirm(combined) is False


def test_degraded_evidence_can_never_construct_as_complete_looking():
    with pytest.raises(ValueError):
        EvidenceQuality(state=EvidenceQualityState.COMPLETE, degradations=frozenset({"X"}))
    with pytest.raises(ValueError):
        EvidenceQuality(state=EvidenceQualityState.DEGRADED, degradations=frozenset())


def test_only_complete_evidence_can_independently_confirm():
    for state in (
        EvidenceQualityState.DEGRADED,
        EvidenceQualityState.INCOMPLETE,
        EvidenceQualityState.UNUSABLE,
    ):
        quality = EvidenceQuality(state=state, degradations=frozenset({"X"}))
        assert can_independently_confirm(quality) is False
    assert can_independently_confirm(COMPLETE) is True
