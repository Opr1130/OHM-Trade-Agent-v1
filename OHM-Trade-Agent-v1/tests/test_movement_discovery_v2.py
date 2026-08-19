from app.scanner.models import MarketSnapshot
from app.services.movement_discovery_v2 import (
    READY,
    WATCH,
    CoarseMover,
    discover_coarse_movers,
    evaluate_early_mover,
)


def pair(base, quote="USD"):
    return {
        "altname": f"{base}{quote}",
        "wsname": f"{base}/{quote}",
        "status": "online",
    }


def ticker(last, high, low, volume):
    return {
        "last": last,
        "bid": last * 0.999,
        "ask": last * 1.001,
        "volume_24h": volume,
        "high_24h": high,
        "low_24h": low,
    }


class FakeKraken:
    def __init__(self):
        self.pairs = {
            "FASTUSD": pair("FAST"),
            "SLOWUSD": pair("SLOW"),
            "DUSTUSD": pair("DUST"),
        }
        self.tickers = {
            "FASTUSD": ticker(10.0, 10.1, 8.0, 100_000),
            "SLOWUSD": ticker(10.0, 12.0, 9.9, 100_000),
            "DUSTUSD": ticker(1.0, 1.01, 0.70, 100),
        }
        self.calls = []

    def get_asset_pairs(self):
        return self.pairs

    def get_tickers(self, pair_ids):
        self.calls.append(list(pair_ids))
        return {key: self.tickers[key] for key in pair_ids if key in self.tickers}


def snapshot(**overrides):
    values = dict(
        symbol="FASTUSD",
        last_price=10.0,
        ema20=9.5,
        ema50=9.0,
        ema200=8.0,
        rsi=65,
        macd_line=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=0.3,
        atr_pct=3.0,
        volume_ratio=2.8,
        technical_score=90,
        trend="bullish",
        movement_data_status="AVAILABLE",
        movement_volume_ratio=2.8,
        confirmed_price_change_1h_pct=2.2,
        momentum_6h_pct=5.0,
        momentum_24h_pct=9.0,
        distance_to_24h_high_pct=0.8,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def coarse(notional=1_000_000):
    return CoarseMover(
        base_asset="FAST",
        primary_pair="FASTUSD",
        kraken_public_symbol="FAST/USD",
        last_price=10,
        volume_24h=100_000,
        notional_24h_usd_approx=notional,
        high_24h=10.1,
        low_24h=8.0,
        lift_from_24h_low_pct=25.0,
        distance_from_24h_high_pct=1.0,
        coarse_score=100.0,
    )


def test_coarse_discovery_sweeps_all_pairs_and_finds_active_mover():
    client = FakeKraken()
    result = discover_coarse_movers(client, max_candidates=40)
    assert [item.base_asset for item in result] == ["FAST"]
    assert client.calls
    assert set(client.calls[0]) == {"FASTUSD", "SLOWUSD", "DUSTUSD"}
    assert result[0].lift_from_24h_low_pct == 25.0


def test_ready_mover_requires_multiple_active_momentum_families():
    signal = evaluate_early_mover(snapshot(), coarse())
    assert signal is not None
    assert signal.stage == READY
    assert signal.discovery_score >= 65
    assert signal.direction == "LONG"
    assert signal.actionable is False
    assert signal.score_is_probability is False


def test_watch_mover_is_visible_before_full_trade_quality():
    signal = evaluate_early_mover(
        snapshot(
            confirmed_price_change_1h_pct=0.9,
            momentum_6h_pct=2.1,
            momentum_24h_pct=1.0,
            movement_volume_ratio=1.6,
            volume_ratio=1.6,
            trend="neutral",
            distance_to_24h_high_pct=1.5,
        ),
        coarse(),
    )
    assert signal is not None
    assert signal.stage == WATCH
    assert signal.actionable is False


def test_extended_and_low_liquidity_mover_warns_not_to_chase():
    signal = evaluate_early_mover(
        snapshot(momentum_24h_pct=22.0, confirmed_price_change_1h_pct=7.0),
        coarse(notional=20_000),
    )
    assert signal is not None
    assert signal.extended_move is True
    assert any("not permission to chase" in warning for warning in signal.warnings)
    assert any("execution risk" in warning for warning in signal.warnings)


def test_weak_movement_is_not_promoted_to_discovery_signal():
    signal = evaluate_early_mover(
        snapshot(
            confirmed_price_change_1h_pct=0.1,
            momentum_6h_pct=0.3,
            momentum_24h_pct=0.5,
            movement_volume_ratio=1.0,
            volume_ratio=1.0,
            trend="neutral",
            distance_to_24h_high_pct=5.0,
        ),
        coarse(),
    )
    assert signal is None
