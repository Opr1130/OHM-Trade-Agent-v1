"""BUILD 4.1 — provider-neutral sequencing abstraction."""

from __future__ import annotations

import pytest

from app.opip.streaming.contract import SequenceStatus
from app.opip.streaming.sequencing import (
    NoSequenceTracker,
    NonDecreasingSequenceTracker,
    StrictIncrementingSequenceTracker,
)


# ------------------------------------------------------ strict incrementing


def test_strict_first_event_is_first():
    tracker = StrictIncrementingSequenceTracker()
    obs = tracker.observe("100", reconnect_epoch=0)
    assert obs.status == SequenceStatus.FIRST


def test_strict_contiguous_sequence():
    tracker = StrictIncrementingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    obs = tracker.observe("101", reconnect_epoch=0)
    assert obs.status == SequenceStatus.CONTIGUOUS


def test_strict_duplicate():
    tracker = StrictIncrementingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    obs = tracker.observe("100", reconnect_epoch=0)
    assert obs.status == SequenceStatus.DUPLICATE


def test_strict_gap_reports_size():
    tracker = StrictIncrementingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    obs = tracker.observe("105", reconnect_epoch=0)
    assert obs.status == SequenceStatus.GAP
    assert obs.gap_size == 4


def test_strict_out_of_order():
    tracker = StrictIncrementingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    tracker.observe("101", reconnect_epoch=0)
    obs = tracker.observe("99", reconnect_epoch=0)
    assert obs.status == SequenceStatus.OUT_OF_ORDER


def test_strict_unsupported_on_malformed_value():
    tracker = StrictIncrementingSequenceTracker()
    obs = tracker.observe("not-a-number", reconnect_epoch=0)
    assert obs.status == SequenceStatus.UNSUPPORTED


def test_strict_reconnect_epoch_reset_is_not_a_gap():
    tracker = StrictIncrementingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    tracker.observe("101", reconnect_epoch=0)
    obs = tracker.observe("5", reconnect_epoch=1)
    assert obs.status == SequenceStatus.RESET_NEW_EPOCH
    assert obs.epoch_changed is True
    # Contiguity resumes cleanly inside the new epoch.
    obs2 = tracker.observe("6", reconnect_epoch=1)
    assert obs2.status == SequenceStatus.CONTIGUOUS


def test_strict_explicit_reset_clears_memory():
    tracker = StrictIncrementingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    tracker.reset()
    obs = tracker.observe("101", reconnect_epoch=0)
    assert obs.status == SequenceStatus.FIRST


# ---------------------------------------------------------- non-decreasing


def test_non_decreasing_allows_non_contiguous_increase():
    tracker = NonDecreasingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    obs = tracker.observe("9000", reconnect_epoch=0)
    assert obs.status == SequenceStatus.CONTIGUOUS


def test_non_decreasing_repeat_is_contiguous_not_duplicate():
    tracker = NonDecreasingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    obs = tracker.observe("100", reconnect_epoch=0)
    assert obs.status == SequenceStatus.CONTIGUOUS


def test_non_decreasing_decrease_is_out_of_order():
    tracker = NonDecreasingSequenceTracker()
    tracker.observe("100", reconnect_epoch=0)
    obs = tracker.observe("50", reconnect_epoch=0)
    assert obs.status == SequenceStatus.OUT_OF_ORDER


def test_non_decreasing_never_reports_a_gap():
    tracker = NonDecreasingSequenceTracker()
    tracker.observe("1", reconnect_epoch=0)
    obs = tracker.observe("1000000", reconnect_epoch=0)
    assert obs.status != SequenceStatus.GAP
    assert obs.gap_size is None


def test_non_decreasing_epoch_reset_is_not_out_of_order():
    tracker = NonDecreasingSequenceTracker()
    tracker.observe("1000", reconnect_epoch=0)
    obs = tracker.observe("1", reconnect_epoch=1)
    assert obs.status == SequenceStatus.RESET_NEW_EPOCH


# --------------------------------------------------------------- no sequence


def test_no_sequence_tracker_always_unsupported():
    tracker = NoSequenceTracker()
    for value in (None, "", "123", "abc"):
        obs = tracker.observe(value, reconnect_epoch=0)
        assert obs.status == SequenceStatus.UNSUPPORTED


def test_no_sequence_tracker_tracks_epoch_changes():
    tracker = NoSequenceTracker()
    obs1 = tracker.observe(None, reconnect_epoch=0)
    obs2 = tracker.observe(None, reconnect_epoch=1)
    assert obs1.epoch_changed is False
    assert obs2.epoch_changed is True


# --------------------------------------------------------------------- misc


def test_negative_reconnect_epoch_rejected():
    tracker = StrictIncrementingSequenceTracker()
    with pytest.raises(ValueError):
        tracker.observe("1", reconnect_epoch=-1)


def test_two_different_policies_diverge_on_the_same_input():
    """The universal '!= previous+1 => gap' rule is unsafe across providers:
    the same non-contiguous input must classify differently per policy."""
    strict = StrictIncrementingSequenceTracker()
    lenient = NonDecreasingSequenceTracker()
    strict.observe("1", reconnect_epoch=0)
    lenient.observe("1", reconnect_epoch=0)

    strict_obs = strict.observe("50", reconnect_epoch=0)
    lenient_obs = lenient.observe("50", reconnect_epoch=0)

    assert strict_obs.status == SequenceStatus.GAP
    assert lenient_obs.status == SequenceStatus.CONTIGUOUS
