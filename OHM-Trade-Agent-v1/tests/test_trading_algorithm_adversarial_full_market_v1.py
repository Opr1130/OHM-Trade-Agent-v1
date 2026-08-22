from __future__ import annotations

import math

from app.services.full_market_observation import (
    MarketObservation,
    _transition,
    collect_full_market_observations,
    process_full_market_observations,
)


class _PoisonedKraken:
    def get_asset_pairs(self):
        return {
            "BADUSD": {"altname": "BADUSD", "wsname": "BAD/USD"},
            "NANUSD": {"altname": "NANUSD", "wsname": "NAN/USD"},
        }

    def get_tickers(self, pairs):
        data = {
            "BADUSD": {
                "last": math.inf,
                "high_24h": math.inf,
                "low_24h": 1.0,
                "volume_24h": 100_000.0,
            },
            "NANUSD": {
                "last": 10.0,
                "high_24h": 11.0,
                "low_24h": math.nan,
                "volume_24h": 100_000.0,
            },
        }
        return {pair: data[pair] for pair in pairs if pair in data}


def test_full_market_collection_rejects_non_finite_tickers():
    rows = collect_full_market_observations(_PoisonedKraken())
    assert rows == []


def test_full_market_processing_never_persists_nan_or_infinity(tmp_path):
    observation_file = tmp_path / "observations.jsonl"
    state_file = tmp_path / "state.json"
    result = process_full_market_observations(
        client=_PoisonedKraken(),
        observation_file=observation_file,
        state_file=state_file,
    )
    assert result.observed_markets == 0
    assert result.persisted_events == 0
    if observation_file.exists():
        payload = observation_file.read_text(encoding="utf-8")
        assert "NaN" not in payload
        assert "Infinity" not in payload
    if state_file.exists():
        payload = state_file.read_text(encoding="utf-8")
        assert "NaN" not in payload
        assert "Infinity" not in payload


def test_full_market_transition_fails_closed_on_non_finite_state():
    current = MarketObservation(
        version="full-market-observation-v1",
        base_asset="BAD",
        symbol="BADUSD",
        kraken_public_symbol="BAD/USD",
        last_price=105.0,
        volume_24h=100_000.0,
        notional_24h_usd_approx=10_500_000.0,
        high_24h=106.0,
        low_24h=100.0,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=1.0,
    )
    previous = {
        "last_price": math.nan,
        "lift_from_24h_low_pct": math.inf,
    }
    assert _transition(current, previous) is None
