"""LONG/SHORT identity safety.

Direction used to be a hard-coded literal in the signal and paper identity
builders, so ``BTCUSD LONG`` and ``BTCUSD SHORT`` in the same episode produced
the same id. These tests pin both halves of the fix: the collision is gone, and
every id previously issued for a LONG is still reproduced byte-for-byte.
"""

import hashlib
from datetime import datetime, timezone

from app.opip.decision.identity import (
    MARGIN,
    SPOT,
    candidate_key,
    decision_boundary,
    market_type_for,
    normalize_direction,
    opip_candidate_id,
)
from app.services.freqtrade_signal_bridge import build_signal_id
from app.services.paper_trade_engine import _paper_id


DECISION_AT = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
EPISODE = "EP:0123456789abcdef01234567"


# ------------------------------------------------------------- collisions --


def test_signal_id_cannot_collide_across_directions():
    long_id = build_signal_id(
        episode_id=EPISODE, pair="BTC/USD", decision_at=DECISION_AT, direction="LONG"
    )
    short_id = build_signal_id(
        episode_id=EPISODE, pair="BTC/USD", decision_at=DECISION_AT, direction="SHORT"
    )
    assert long_id != short_id


def test_paper_id_cannot_collide_across_directions():
    assert _paper_id(EPISODE, "BTCUSD", "LONG") != _paper_id(
        EPISODE, "BTCUSD", "SHORT"
    )


def test_opip_candidate_id_cannot_collide_across_directions_or_venues():
    ids = {
        opip_candidate_id(episode_id=EPISODE, pair="BTCUSD", direction="LONG"),
        opip_candidate_id(episode_id=EPISODE, pair="BTCUSD", direction="SHORT"),
        opip_candidate_id(
            episode_id=EPISODE, pair="BTCUSD", direction="LONG", market_type=MARGIN
        ),
    }
    assert len(ids) == 3


def test_candidate_identity_is_unique_across_a_realistic_scan_cohort():
    """Every (pair, direction) pair in one episode gets a distinct identity."""
    pairs = ["BTCUSD", "ETHUSD", "SOLUSD", "RAYUSD", "BTCUSDT"]
    ids = [
        opip_candidate_id(episode_id=EPISODE, pair=pair, direction=direction)
        for pair in pairs
        for direction in ("LONG", "SHORT")
    ]
    assert len(set(ids)) == len(ids)


# --------------------------------------------------- backward compatibility --


def test_long_signal_id_is_byte_identical_to_the_historical_formula():
    """The pre-fix formula hard-coded the literal 'LONG'; reproduce it here."""
    historical_basis = f"{EPISODE}|BTC/USD|{DECISION_AT.isoformat()}|LONG"
    expected = "OHM:" + hashlib.sha256(
        historical_basis.encode("utf-8")
    ).hexdigest()[:28]
    assert (
        build_signal_id(
            episode_id=EPISODE, pair="BTC/USD", decision_at=DECISION_AT
        )
        == expected
    )
    assert (
        build_signal_id(
            episode_id=EPISODE,
            pair="BTC/USD",
            decision_at=DECISION_AT,
            direction="LONG",
        )
        == expected
    )


def test_long_paper_id_is_byte_identical_to_the_historical_formula():
    historical_basis = f"{EPISODE}|BTCUSD|LONG"
    expected = "PAPER:" + hashlib.sha256(
        historical_basis.encode("utf-8")
    ).hexdigest()[:20]
    assert _paper_id(EPISODE, "BTCUSD") == expected
    assert _paper_id(EPISODE, "btcusd", "long") == expected


# ----------------------------------------------------------- normalisation --


def test_direction_normalisation_is_case_insensitive_and_defaults_to_long():
    assert normalize_direction("short") == "SHORT"
    assert normalize_direction(None) == "LONG"
    assert normalize_direction("") == "LONG"
    assert candidate_key("btcusd", "short") == ("BTCUSD", "SHORT")


def test_market_type_follows_direction():
    assert market_type_for("LONG") == SPOT
    assert market_type_for("SHORT") == MARGIN


def test_identity_does_not_depend_on_microseconds():
    boundary = decision_boundary(DECISION_AT)
    assert boundary == decision_boundary(DECISION_AT.replace(microsecond=987_654))
    assert "." not in boundary


def test_decision_boundary_requires_timezone_aware_input():
    try:
        decision_boundary(datetime(2026, 8, 28, 12, 0))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("naive datetime must be rejected")


# ------------------------------------------------------- scan-level lineage --


def test_a_long_and_a_short_on_one_asset_produce_distinct_scan_identities():
    """The exact case the pre-fix identity merged into one record."""
    pair = "BTC/USD"
    signals = {
        direction: build_signal_id(
            episode_id=EPISODE,
            pair=pair,
            decision_at=DECISION_AT,
            direction=direction,
        )
        for direction in ("LONG", "SHORT")
    }
    candidates = {
        direction: opip_candidate_id(
            episode_id=EPISODE, pair=pair, direction=direction
        )
        for direction in ("LONG", "SHORT")
    }
    assert len(set(signals.values())) == 2
    assert len(set(candidates.values())) == 2
    # The canonical episode is intentionally shared: one market episode can
    # host both directions. Disambiguation belongs to the candidate and signal
    # identities, which is exactly what was missing before.
    assert signals["LONG"].startswith("OHM:")
    assert candidates["SHORT"].startswith("OPIPC:")
