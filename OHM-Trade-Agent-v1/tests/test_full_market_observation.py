from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.full_market_observation import (
    DEFAULT_HISTORY_SCANS,
    MarketObservation,
    _should_persist,
    _transition,
    collect_full_market_observations,
    process_full_market_observations,
)


class FakeKraken:
    def get_asset_pairs(self):
        return {
            "LOWUSD": {"altname": "LOWUSD", "wsname": "LOW/USD"},
            "BIGUSD": {"altname": "BIGUSD", "wsname": "BIG/USD"},
            "USDCUSD": {"altname": "USDCUSD", "wsname": "USDC/USD"},
        }

    def get_tickers(self, pairs):
        data = {
            "LOWUSD": {
                "last": 1.05,
                "high_24h": 1.06,
                "low_24h": 1.00,
                "volume_24h": 2_000.0,
            },
            "BIGUSD": {
                "last": 12.0,
                "high_24h": 12.1,
                "low_24h": 10.0,
                "volume_24h": 100_000.0,
            },
        }
        return {pair: data[pair] for pair in pairs if pair in data}


def _observation(
    *,
    symbol="TESTUSD",
    last=105.0,
    notional=100_000.0,
    lift=5.0,
    distance=1.0,
):
    return MarketObservation(
        version="full-market-observation-v1",
        base_asset=symbol.removesuffix("USD"),
        symbol=symbol,
        kraken_public_symbol=f"{symbol.removesuffix('USD')}/USD",
        last_price=last,
        volume_24h=1000.0,
        notional_24h_usd_approx=notional,
        high_24h=106.0,
        low_24h=100.0,
        lift_from_24h_low_pct=lift,
        distance_from_24h_high_pct=distance,
    )


def test_full_market_learning_keeps_low_liquidity_assets():
    rows = collect_full_market_observations(FakeKraken())
    symbols = {row.symbol for row in rows}
    assert "LOWUSD" in symbols
    assert "BIGUSD" in symbols
    assert "USDCUSD" not in symbols
    low = next(row for row in rows if row.symbol == "LOWUSD")
    assert low.notional_24h_usd_approx == 2100.0


def test_compression_release_detected_without_granting_trade_authority():
    previous = {
        "last_price": 100.0,
        "lift_from_24h_low_pct": 1.0,
    }
    transition = _transition(
        _observation(last=105.0, lift=5.0, distance=1.0, notional=40_000.0),
        previous,
    )
    assert transition is not None
    assert transition.pattern == "COMPRESSION_RELEASE"
    assert transition.alert_tier == "WATCH_ONLY"
    assert transition.trade_authority_changed is False
    assert transition.production_execution_gate_changed is False


def test_reacceleration_can_request_deep_review_but_not_approve_trade():
    previous = {
        "last_price": 105.0,
        "lift_from_24h_low_pct": 5.0,
    }
    transition = _transition(
        _observation(last=108.0, lift=8.0, distance=1.0, notional=2_000_000.0),
        previous,
    )
    assert transition is not None
    assert transition.pattern == "REACCELERATION"
    assert transition.alert_tier == "DEEP_REVIEW"
    assert transition.trade_authority_changed is False


def test_small_noise_is_not_persisted_before_heartbeat():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    previous = {
        "recorded_at": (now - timedelta(minutes=10)).isoformat(),
        "last_price": 100.0,
        "lift_from_24h_low_pct": 4.0,
        "distance_from_24h_high_pct": 2.0,
        "notional_24h_usd_approx": 100_000.0,
    }
    persist, reason = _should_persist(
        _observation(last=100.2, lift=4.1, distance=2.1, notional=105_000.0),
        previous,
        now,
    )
    assert persist is False
    assert reason == "NO_MEANINGFUL_CHANGE"


def test_process_is_event_driven_and_second_scan_can_create_transition(tmp_path):
    state_file = tmp_path / "state.json"
    observation_file = tmp_path / "observations.jsonl"
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)

    class FirstClient(FakeKraken):
        def get_tickers(self, pairs):
            data = super().get_tickers(pairs)
            data["LOWUSD"] = {
                "last": 1.01,
                "high_24h": 1.06,
                "low_24h": 1.00,
                "volume_24h": 2_000.0,
            }
            return data

    first = process_full_market_observations(
        client=FirstClient(),
        now=now,
        observation_file=observation_file,
        state_file=state_file,
    )
    assert first.observed_markets == 2
    assert first.persisted_events == 2
    assert not first.transition_alerts

    second = process_full_market_observations(
        client=FakeKraken(),
        now=now + timedelta(minutes=10),
        observation_file=observation_file,
        state_file=state_file,
    )
    assert second.observed_markets == 2
    low_transition = next(item for item in second.transition_alerts if item.symbol == "LOWUSD")
    assert low_transition.pattern == "COMPRESSION_RELEASE"
    assert low_transition.alert_tier == "WATCH_ONLY"


# ---------------------------------------------------------------------------
# Phase 3A: signal_quality_reference_prices (the same-scan price a candidate
# was actually scored from, exposed read-only for decision telemetry)
# ---------------------------------------------------------------------------


def _signal_quality_settings(**overrides):
    fields = dict(
        signal_quality_v1_enabled=True,
        signal_quality_history_scans=DEFAULT_HISTORY_SCANS,
        signal_quality_scan_interval_seconds=600,
        signal_quality_continuity_multiplier=2.5,
        signal_quality_stale_history_retention_seconds=3600,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _FakeCandidate:
    def __init__(self, symbol):
        self.symbol = symbol


def test_reference_prices_match_the_same_scan_price_used_for_scoring(tmp_path, monkeypatch):
    """The mapping must hold exactly the price this scan's history carried for
    the candidate's symbol - not a re-derived or later value.
    """
    import app.services.full_market_observation as fmo

    monkeypatch.setattr(
        fmo, "evaluate_signal_quality", lambda *a, **k: (_FakeCandidate("BIGUSD"),)
    )

    result = process_full_market_observations(
        client=FakeKraken(),
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
        observation_file=tmp_path / "observations.jsonl",
        state_file=tmp_path / "state.json",
        settings=_signal_quality_settings(),
    )

    assert result.signal_quality_reference_prices == {"BIGUSD": 12.0}


def test_reference_prices_empty_when_signal_quality_disabled(tmp_path):
    result = process_full_market_observations(
        client=FakeKraken(),
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
        observation_file=tmp_path / "observations.jsonl",
        state_file=tmp_path / "state.json",
        settings=_signal_quality_settings(signal_quality_v1_enabled=False),
    )

    assert result.signal_quality_reference_prices == {}
    assert result.signal_quality_candidates == ()


def test_reference_prices_empty_when_scoring_fails_fail_soft(tmp_path, monkeypatch, capsys):
    import app.services.full_market_observation as fmo

    def boom(*args, **kwargs):
        raise RuntimeError("scoring exploded")

    monkeypatch.setattr(fmo, "evaluate_signal_quality", boom)

    result = process_full_market_observations(
        client=FakeKraken(),
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
        observation_file=tmp_path / "observations.jsonl",
        state_file=tmp_path / "state.json",
        settings=_signal_quality_settings(),
    )

    assert result.signal_quality_candidates == ()
    assert result.signal_quality_reference_prices == {}
    assert "Signal Quality v1: fail-soft" in capsys.readouterr().out


def test_reference_price_lookup_failure_is_fail_soft_and_never_touches_candidates(
    tmp_path, monkeypatch, capsys
):
    """A defect while building the price mapping must never take the already
    -computed candidates down with it - scoring and telemetry-adjacency are
    independent fail-soft boundaries.
    """
    import app.services.full_market_observation as fmo

    class ExplodingSymbol:
        symbol = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    monkeypatch.setattr(
        fmo, "evaluate_signal_quality", lambda *a, **k: (ExplodingSymbol(),)
    )

    result = process_full_market_observations(
        client=FakeKraken(),
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
        observation_file=tmp_path / "observations.jsonl",
        state_file=tmp_path / "state.json",
        settings=_signal_quality_settings(),
    )

    assert len(result.signal_quality_candidates) == 1
    assert result.signal_quality_reference_prices == {}
    assert "reference prices: fail-soft" in capsys.readouterr().out


def test_reference_prices_only_include_scored_candidates(tmp_path, monkeypatch):
    """The mapping is not "every observed symbol's price" - only symbols that
    actually produced a candidate this scan.
    """
    import app.services.full_market_observation as fmo

    monkeypatch.setattr(
        fmo, "evaluate_signal_quality", lambda *a, **k: (_FakeCandidate("BIGUSD"),)
    )

    result = process_full_market_observations(
        client=FakeKraken(),
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
        observation_file=tmp_path / "observations.jsonl",
        state_file=tmp_path / "state.json",
        settings=_signal_quality_settings(),
    )

    assert set(result.signal_quality_reference_prices) == {"BIGUSD"}
    assert "LOWUSD" not in result.signal_quality_reference_prices
