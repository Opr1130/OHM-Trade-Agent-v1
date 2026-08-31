from datetime import datetime, timezone

from app.exchanges.kraken import Candle
from app.services.phase3b_live_structure import (
    LOOKBACK_COMPLETED_BARS,
    MAX_STRUCTURE_CANDIDATES,
    STATUS_AVAILABLE,
    STATUS_INSUFFICIENT,
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


def test_canonical_pair_resolves_kraken_legacy_base_aliases():
    assert _canonical_kraken_pair("BTCUSD") == "XBT/USD"
    assert _canonical_kraken_pair("DOGEUSD") == "XDG/USD"
    assert _canonical_kraken_pair("BTCUSDT") == "XBT/USDT"
    assert _canonical_kraken_pair("mon/usd") == "MON/USD"
    assert _canonical_kraken_pair("ETHUSDT") == "ETH/USDT"
    assert _canonical_kraken_pair("SOLUSD") == "SOL/USD"
    assert _canonical_kraken_pair("BAD") is None


def test_still_forming_15m_candle_is_excluded():
    closed_open = int(
        datetime(2026, 8, 24, 17, 45, tzinfo=timezone.utc).timestamp()
    )
    forming_open = int(
        datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc).timestamp()
    )
    bars = _completed_structure_bars(
        [
            candle(closed_open, 10.0, 10.5, 9.8, 10.3),
            candle(forming_open, 10.3, 11.0, 10.2, 10.9),
        ],
        decision_at=NOW,
    )
    assert len(bars) == 1
    assert bars[0].observed_at == datetime(
        2026, 8, 24, 18, 0, tzinfo=timezone.utc
    )
    assert bars[0].close == 10.3


def test_exact_15m_boundary_accepts_at_close_and_rejects_one_second_before():
    opened = int(
        datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc).timestamp()
    )
    row = candle(opened, 10.0, 10.5, 9.9, 10.2)

    accepted = _completed_structure_bars(
        [row],
        decision_at=datetime(2026, 8, 24, 12, 15, 0, tzinfo=timezone.utc),
    )
    rejected = _completed_structure_bars(
        [row],
        decision_at=datetime(2026, 8, 24, 12, 14, 59, tzinfo=timezone.utc),
    )

    assert len(accepted) == 1
    assert rejected == []


def test_delayed_fetch_cannot_leak_candle_closed_after_original_decision():
    before = int(
        datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc).timestamp()
    )
    after = int(
        datetime(2026, 8, 24, 12, 15, tzinfo=timezone.utc).timestamp()
    )
    original_decision = datetime(
        2026, 8, 24, 12, 20, tzinfo=timezone.utc
    )

    # Imagine the HTTP response arrives at 12:25. The 12:15 bucket then exists
    # in Kraken's response, but it closes at 12:30 and was not visible at 12:20.
    bars = _completed_structure_bars(
        [
            candle(before, 10.0, 10.3, 9.9, 10.2),
            candle(after, 10.2, 10.8, 10.1, 10.7),
        ],
        decision_at=original_decision,
    )

    assert len(bars) == 1
    assert bars[0].observed_at == datetime(
        2026, 8, 24, 12, 15, tzinfo=timezone.utc
    )


def test_out_of_order_duplicate_candles_are_sorted_and_deduplicated():
    first = int(datetime(2026, 8, 24, 11, 30, tzinfo=timezone.utc).timestamp())
    second = int(datetime(2026, 8, 24, 11, 45, tzinfo=timezone.utc).timestamp())
    bars = _completed_structure_bars(
        [
            candle(second, 11, 12, 10.5, 11.5),
            candle(first, 10, 11, 9.5, 10.5),
            candle(first, 10, 11.2, 9.4, 10.7),
        ],
        decision_at=datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc),
    )

    assert len(bars) == 2
    assert bars[0].observed_at < bars[1].observed_at
    assert bars[0].close == 10.7


def test_naive_decision_timestamp_is_coerced_to_utc():
    opened = int(
        datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc).timestamp()
    )
    bars = _completed_structure_bars(
        [candle(opened, 10, 11, 9, 10.5)],
        decision_at=datetime(2026, 8, 24, 12, 15),
    )
    assert len(bars) == 1


def test_collector_uses_completed_bars_and_existing_ranked_candidate_order():
    start = datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)
    rows = []
    price = 10.0
    for i in range(97):
        opened = int(start.timestamp() + i * 15 * 60)
        bump = (i % 6) * 0.08
        o = price + bump
        c = o + (0.05 if i % 2 == 0 else -0.03)
        rows.append(
            candle(opened, o, max(o, c) + 0.10, min(o, c) - 0.10, c)
        )
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
    assert sample.completed_bar_count == LOOKBACK_COMPLETED_BARS
    assert sample.latest_completed_at <= NOW
    assert sample.context is not None
    assert sample.context.advisory_only is True
    assert client.calls[0][0] == "MON/USD"
    assert client.calls[0][1] == 15


def test_less_than_96_completed_bars_is_explicitly_insufficient():
    start = int(
        datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc).timestamp()
    )
    rows = [
        candle(start + i * 900, 10, 10.2, 9.8, 10.1)
        for i in range(LOOKBACK_COMPLETED_BARS - 1)
    ]
    sample = collect_phase3b_live_structure(
        [candidate("MONUSD")],
        decision_at=NOW,
        client=FakeClient(rows),
    )["MONUSD"]

    assert sample.completed_bar_count == LOOKBACK_COMPLETED_BARS - 1
    assert sample.status == STATUS_INSUFFICIENT


def test_collector_is_bounded_to_avoid_full_universe_ohlc_fanout():
    start = int(
        datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc).timestamp()
    )
    client = FakeClient(
        [candle(start + i * 900, 10, 10.2, 9.8, 10.1) for i in range(8)]
    )
    candidates = [
        candidate(f"C{i}USD") for i in range(MAX_STRUCTURE_CANDIDATES + 5)
    ]

    samples = collect_phase3b_live_structure(
        candidates, decision_at=NOW, client=client
    )

    assert len(samples) == MAX_STRUCTURE_CANDIDATES
    assert len(client.calls) == MAX_STRUCTURE_CANDIDATES


def test_kraken_error_is_fail_soft_per_symbol():
    client = FakeClient(error=TimeoutError("timeout"))
    samples = collect_phase3b_live_structure(
        [candidate("MONUSD")], decision_at=NOW, client=client
    )
    sample = samples["MONUSD"]
    assert sample.status == STATUS_UNAVAILABLE_ERROR
    assert sample.context is None
    assert sample.error_type == "TimeoutError"
    assert sample.trade_authority_changed is False
    assert sample.production_execution_gate_changed is False
