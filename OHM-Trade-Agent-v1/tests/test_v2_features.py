"""Point-in-time V2 feature extraction and MACD parity tests."""
from app.exchanges.kraken import Candle
from app.indicators.technical import macd
from app.scanner.v2_features import _macd_histograms_last_two, extract_v2_features


def candles(n: int = 240, direction: int = 1):
    return [
        Candle(
            timestamp=i * 3600,
            open=100 + direction * i * 0.1,
            high=101 + direction * i * 0.1,
            low=99 + direction * i * 0.1,
            close=100 + direction * i * 0.1,
            vwap=100 + direction * i * 0.1,
            volume=1000,
            trade_count=10,
        )
        for i in range(n)
    ]


def test_bullish_complete_hourly_features():
    extracted = extract_v2_features(candles())
    assert extracted.higher_timeframe_ema50_rising is True
    assert extracted.higher_timeframe_ema50_falling is False
    assert extracted.adx is not None and 0 <= extracted.adx <= 100
    assert extracted.atr_percentile is not None
    assert 0 <= extracted.candle_close_location <= 1


def test_bearish_complete_hourly_features():
    extracted = extract_v2_features(candles(direction=-1))
    assert extracted.higher_timeframe_ema50_falling is True
    assert extracted.higher_timeframe_ema50_rising is False


def test_insufficient_four_hour_history_is_explicit():
    extracted = extract_v2_features(candles(n=199))
    assert extracted.higher_timeframe_ema50_rising is None
    assert extracted.higher_timeframe_ema50_falling is None


def test_recent_hourly_gap_causes_no_score():
    rows = candles()
    rows[-4] = Candle(timestamp=rows[-4].timestamp - 30, open=1, high=2, low=0.5, close=1, vwap=1, volume=1, trade_count=1)
    features = extract_v2_features(rows)
    assert features.adx is None


def test_macd_histogram_matches_existing_calculator():
    values = [100.0 + i * 0.1 + (i % 7) * 0.2 for i in range(240)]
    last_two = _macd_histograms_last_two(values)
    assert last_two is not None
    assert abs(last_two[0] - macd(values[:-1])[2]) < 1e-9
    assert abs(last_two[1] - macd(values)[2]) < 1e-9
