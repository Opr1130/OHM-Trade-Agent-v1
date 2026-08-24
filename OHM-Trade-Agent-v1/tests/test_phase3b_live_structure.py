from datetime import datetime, timezone

from app.exchanges.kraken import Candle
from app.services.phase3b_live_structure import (
    MAX_STRUCTURE_CANDIDATES,
    STATUS_AVAILABLE,
    STATUS_UNAVAILABLE_ERROR,
    _canonical_kraken_pair,
    _completed_structure_bars,
    collect_phase3b_live_structure,
)
from app.services.signal_scoring import SignalQualityCandidate

NOW = datetime(2026, 8, 24, 18, 7, tzinfo=timezone.utc)


def candidate(symbol: str, *, stage: str = "BREAKOUT_CANDIDATE"):
    return SignalQualityCandidate(
        version="v1",
        symbol=symbol,
        stage=stage,
        pattern="REACCELERATION",
        tradeability_score=70,
        pattern_strength_score=80,
        volume_acceleration_score=75,
        persistence_score=70,
        relative_strength_score=90,
        explosion_potential_score=85,
        opportunity_score=78,
        exhaustion_penalty=10,
        exhaustion_band="LOW",
        liquidity_24h_usd_approx=2_000_000.0,
        persistence_scans=3,
        relative_strength_percentile=95.0,
        universe_size=200,
        reasons=(),
        components={},
    )


def candle(opened: int, o: float, h: float, l: float, c: float):
    return Candle(
        timestamp=opened,
        open=o,
        high=h,
        low=l,
        close=c,
        vwap=c,
        volume=100.0,
        trade_count=10,
    )


class FakeClient:
    def __init__(self, candles=None, error=None):
        self.candles = list(candles or [])
        self.error = error
        self.calls = []

    def get_ohlc(self, pair, interval=60, since=None):
        self.calls.append((pair, interval, since))
        if self.error:
            raise self.error
        return self.candles


def test_canonical_pair_preserves_spot_quote_and_slash():
    assert _canonical_kraken_pair("BTCUSD") == "BTC/USD"
    assert _canonical_kraken_pair("mon/usd") == "MON/USD"
    assert _canonical_kraken_pair("ETHUSDT") == "ETH/USDT"
    assert _canonical_kraken_pair("BAD") is None


def test_still_forming_15m_candle_is_excluded():
    # 17:45 candle closed at 18:00 and is eligible. 18:00 closes at 18:15 and
    # must not be visible to a decision made at 18:07.
    candles = [
        candle(1_777_? if False else 0, 1, 1, 1, 1),
    ]
    # Use literal epoch values generated from UTC datetimes for readability.
    closed_open = int(datetime(2026, 8, 24, 17, 45, tzinfo=timezone.utc).timestamp())
    forming_open = int(datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc).timestamp())
    bars = _completed_structure_bars(
        [
            candle(closed_open, 10.0, 10.5, 9.8, 10.3),
            candle(forming_open, 10.3, 11.0, 10.2, 10.9),
        ],
        decision_at=NOW,
    )
    assert len(bars) == 1
    assert bars[0].observed_at == datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)
    assert bars[0].close == 10.3


def test_collector_uses_completed_bars_and_existing_ranked_candidate_order():
    start = datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)
    rows = []
    price = 10.0
    for i in range(97):
        opened = int((start.timestamp()) + i * 15 * 60)
        # Deterministic oscillation creates confirmed swings without relying on
        # the still-forming endpoint candle.
        bump = (i % 6) * 0.08
        o = price + bump
        c = o + (0.05 if i % 2 == 0 else -0.03)
        rows.append(candle(opened, o, max(o, c) + 0.10, min(o, c) - 0.10, c))
    client = FakeClient(rows)

    samples = collect_phase3b_live_structure(
        [candidate("MONUSD"), candidate("ABCUSD", stage="SUPPRESSED")],
        decision_at=NOW,
        client=client,
    )

    assert set(samples) == {"MONUSD"}
    sample = samples["MONUSD"]
    assert sample.status == STATUS_AVAILABLE
    assert sample.kraken_pair == "MON/USD"
    assert sample.interval_minutes == 15
    assert sample.completed_bar_count <= 96
    assert sample.latest_completed_at <= NOW
    assert sample.context is not None
    assert sample.context.advisory_only is True
    assert client.calls[0][0] == "MON/USD"
    assert client.calls[0][1] == 15


def test_collector_is_bounded_to_avoid_full_universe_ohlc_fanout():
    start = int(datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc).timestamp())
    client = FakeClient([
        candle(start + i * 900, 10, 10.2, 9.8, 10.1) for i in range(8)
    ])
    candidates = [candidate(f"C{i}USD") for i in range(MAX_STRUCTURE_CANDIDATES + 5)]

    samples = collect_phase3b_live_structure(candidates, decision_at=NOW, client=client)

    assert len(samples) == MAX_STRUCTURE_CANDIDATES
    assert len(client.calls) == MAX_STRUCTURE_CANDIDATES


def test_kraken_error_is_fail_soft_per_symbol():
    client = FakeClient(error=RuntimeError("timeout"))
    samples = collect_phase3b_live_structure(
        [candidate("MONUSD")], decision_at=NOW, client=client
    )
    sample = samples["MONUSD"]
    assert sample.status == STATUS_UNAVAILABLE_ERROR
    assert sample.context is None
    assert sample.error_type == "RuntimeError"
    assert sample.trade_authority_changed is False
    assert sample.production_execution_gate_changed is False
