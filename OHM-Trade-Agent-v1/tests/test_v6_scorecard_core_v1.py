"""Acceptance checks for the O'Pip v6 Scorecard core (v6 slice E).

The Scorecard core is the pure half of v6 slice E: it takes outcome records that
already carry the version stamp and returns the hit rate and count for each
strategy version. It has no I/O, no UI, no scheduler and no trading authority.
These tests pin both criteria: stamped records split by version, and unstamped
records stay counted under one unversioned group instead of being dropped.
"""

from __future__ import annotations

import pytest

from app.opip.analytics.scorecard import UNVERSIONED, score_outcomes
from app.opip.decision.versioning import STRATEGY_VERSION, version_stamp

PREVIOUS_STRATEGY_VERSION = "OPIP-STRATEGY-V0"


@pytest.mark.acceptance
def test_hit_rate_and_count_are_reported_per_strategy_version() -> None:
    """ATDD-OPIP-V6-SCORECARD-CORE-v1/AC-001"""
    current_stamp = version_stamp()
    previous_stamp = dict(current_stamp, strategy_version=PREVIOUS_STRATEGY_VERSION)
    assert current_stamp["strategy_version"] == STRATEGY_VERSION

    records = [
        {**current_stamp, "hit": True},
        {**current_stamp, "hit": True},
        {**current_stamp, "hit": False},
        {**previous_stamp, "hit": True},
        {**previous_stamp, "hit": False},
        {**previous_stamp, "hit": False},
        {**previous_stamp, "hit": False},
    ]
    originals = [dict(record) for record in records]

    scores = score_outcomes(records)

    # One bucket per strategy version, and only for versions present.
    assert set(scores) == {STRATEGY_VERSION, PREVIOUS_STRATEGY_VERSION}

    current = scores[STRATEGY_VERSION]
    assert current.count == 3
    assert current.hits == 2
    assert current.misses == 1
    assert current.hit_rate == pytest.approx(2 / 3)

    previous = scores[PREVIOUS_STRATEGY_VERSION]
    assert previous.count == 4
    assert previous.hits == 1
    assert previous.misses == 3
    assert previous.hit_rate == pytest.approx(0.25)

    # The returned shape names both figures explicitly for a caller that only
    # consumes dictionaries (for example a later read-only report).
    assert current.as_dict()["hit_rate"] == pytest.approx(2 / 3)
    assert current.as_dict()["count"] == 3

    # Scoring is pure: it does not rewrite the evidence it was handed.
    assert records == originals


@pytest.mark.acceptance
def test_records_without_a_stamp_are_grouped_as_unversioned() -> None:
    """ATDD-OPIP-V6-SCORECARD-CORE-v1/AC-002"""
    records = [
        {"hit": True},
        {"hit": False},
        {"strategy_version": None, "hit": True},
        {"strategy_version": "   ", "hit": True},
        {**version_stamp(), "hit": False},
    ]

    scores = score_outcomes(records)

    assert UNVERSIONED in scores
    unversioned = scores[UNVERSIONED]
    assert unversioned.strategy_version == UNVERSIONED
    assert unversioned.count == 4
    assert unversioned.hits == 3
    assert unversioned.misses == 1
    assert unversioned.hit_rate == pytest.approx(0.75)

    # Unstamped evidence is never attributed to the live strategy version.
    assert scores[STRATEGY_VERSION].count == 1
    assert scores[STRATEGY_VERSION].hits == 0
    assert len(scores) == 2


def test_an_unscorable_record_is_refused_instead_of_guessed() -> None:
    """The core fails closed: no hit outcome is never counted as a miss."""
    with pytest.raises(ValueError):
        score_outcomes([{"strategy_version": STRATEGY_VERSION}])
    with pytest.raises(ValueError):
        score_outcomes([{**version_stamp(), "hit": "yes"}])
