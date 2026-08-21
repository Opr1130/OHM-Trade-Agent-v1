from datetime import datetime, timedelta, timezone

from app.exchanges.kraken import Candle
from app.services.daily_gainer_reconciliation import reconcile_daily_gainers
from app.services.explosion_learning import append_explosion_state, observe_due_explosion_outcomes
from app.services.explosion_state import ExplosionStateVector


def _vector(symbol="TESTUSD", price=100.0):
    return ExplosionStateVector(
        version="explosion-state-v1",
        symbol=symbol,
        reference_price=price,
        momentum_1h_pct=1.2,
        momentum_6h_pct=3.0,
        momentum_24h_pct=5.0,
        momentum_72h_pct=7.0,
        momentum_velocity_1h=1.2,
        momentum_velocity_6h=0.5,
        momentum_acceleration=0.55,
        relative_volume=1.8,
        volume_expansion=0.8,
        relative_volume_change=0.4,
        atr_pct=1.5,
        atr_percentile=35.0,
        atr_percentile_change=12.0,
        bandwidth_percentile=30.0,
        bandwidth_percentile_change=10.0,
        compression_release_score=0.6,
        distance_to_24h_high_pct=1.0,
        distance_to_24h_low_pct=6.0,
        distance_to_high_velocity_pct=2.0,
        base_displacement_pct=6.0,
        base_displacement_velocity_pct=2.5,
        trend="bullish",
        ema_structure_score=2,
        trade_count_acceleration=1.7,
        aggressor_imbalance=0.3,
        large_print_concentration=0.2,
        book_notional_imbalance=0.1,
        flow_available=True,
        persistence_score=2,
        phase="EARLY_EXPANSION",
        phase_score=70,
        reasons=("test",),
        warnings=(),
    )


class OutcomeClient:
    def __init__(self, start):
        self.start = start

    def get_ohlc(self, pair, interval=15, since=None):
        candles = []
        for index in range(0, 17):
            ts = int((self.start + timedelta(minutes=15 * index)).timestamp())
            close = 100.0 + index * 0.5
            candles.append(Candle(ts, close - 0.2, close + 0.8, close - 0.7, close, close, 10.0, 5))
        return candles


def test_wave5_outcomes_use_fixed_horizon_ohlc(tmp_path):
    observed = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    snapshots = tmp_path / "snapshots.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "state.json"
    append_explosion_state(_vector(), path=snapshots, observed_at=observed, source_cohort="EARLY_ACCELERATION")

    summary = observe_due_explosion_outcomes(
        now=observed + timedelta(hours=4, minutes=5),
        client=OutcomeClient(observed),
        snapshot_file=snapshots,
        outcome_file=outcomes,
        state_file=state,
    )

    assert summary["outcomes_added"] == 2
    text = outcomes.read_text(encoding="utf-8")
    assert '"horizon": "1h"' in text
    assert '"horizon": "4h"' in text
    assert '"mfe_pct"' in text
    assert '"mae_pct"' in text
    assert '"time_to_mfe_minutes"' in text
    assert '"relative_volume_change": 0.4' in text
    assert '"atr_percentile_change": 12.0' in text
    assert '"distance_to_high_velocity_pct": 2.0' in text


class GainerClient:
    def get_asset_pairs(self):
        return {
            "TESTUSD": {
                "status": "online",
                "base": "TEST",
                "quote": "ZUSD",
                "altname": "TESTUSD",
                "wsname": "TEST/USD",
            }
        }

    def get_tickers(self, pairs):
        return {
            "TESTUSD": {
                "last": 130.0,
                "volume_24h": 10000.0,
                "high_today": 132.0,
                "high_24h": 132.0,
                "low_today": 99.0,
                "low_24h": 99.0,
                "ask": 130.1,
                "bid": 129.9,
                "volume_today": 9000.0,
            }
        }

    def get_ohlc(self, pair, interval=60, since=None):
        start = since or 0
        return [Candle(start, 100.0, 105.0, 99.0, 104.0, 102.0, 100.0, 20)]


def test_daily_gainer_reconciliation_reports_remaining_upside(tmp_path):
    observed = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    snapshots = tmp_path / "snapshots.jsonl"
    append_explosion_state(
        _vector(symbol="TESTUSD", price=105.0),
        path=snapshots,
        observed_at=observed,
        source_cohort="EARLY_ACCELERATION",
    )

    rows = reconcile_daily_gainers(
        client=GainerClient(),
        now=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        top_n=5,
        snapshot_path=snapshots,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.trace_status == "TRACED_FROM_RECORDED_STATE"
    assert row.gain_today_pct == 30.0
    assert row.gain_when_first_seen_pct == 5.0
    assert row.remaining_upside_after_first_seen_pct > 23.0
    assert row.first_ohm_phase == "EARLY_EXPANSION"
