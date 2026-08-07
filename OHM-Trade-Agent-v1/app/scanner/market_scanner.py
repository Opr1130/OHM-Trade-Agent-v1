from app.exchanges.kraken import KrakenClient
from app.indicators.technical import atr, ema, macd, rsi, volume_ratio
from app.scanner.models import MarketSnapshot
from app.scanner.technical_scorer import score_snapshot


WATCHLIST = [
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
    "XRPUSD",
    "ADAUSD",
    "LINKUSD",
    "AVAXUSD",
    "DOGEUSD",
    "SUIUSD",
    "DOTUSD",
]


def determine_trend(
    last_price: float,
    ema20: float,
    ema50: float,
    ema200: float,
) -> str:
    if last_price > ema20 > ema50 > ema200:
        return "bullish"

    if last_price < ema20 < ema50 < ema200:
        return "bearish"

    return "neutral"


def scan_market() -> list[MarketSnapshot]:
    client = KrakenClient()
    snapshots: list[MarketSnapshot] = []

    for symbol in WATCHLIST:
        try:
            candles = client.get_ohlc(symbol, interval=60)

            closes = [candle.close for candle in candles]
            highs = [candle.high for candle in candles]
            lows = [candle.low for candle in candles]
            volumes = [candle.volume for candle in candles]

            last_price = closes[-1]
            ema20_value = ema(closes, 20)
            ema50_value = ema(closes, 50)
            ema200_value = ema(closes, 200)
            rsi_value = rsi(closes, 14)

            macd_line, macd_signal, macd_histogram = macd(closes)
            atr_value = atr(highs, lows, closes, 14)
            atr_pct = (atr_value / last_price) * 100
            volume_value = volume_ratio(volumes, 20)

            trend = determine_trend(
                last_price,
                ema20_value,
                ema50_value,
                ema200_value,
            )

            snapshot = MarketSnapshot(
                symbol=symbol,
                last_price=last_price,
                ema20=ema20_value,
                ema50=ema50_value,
                ema200=ema200_value,
                rsi=rsi_value,
                macd_line=macd_line,
                macd_signal=macd_signal,
                macd_histogram=macd_histogram,
                atr=atr_value,
                atr_pct=atr_pct,
                volume_ratio=volume_value,
                technical_score=0,
                trend=trend,
            )

            snapshot.technical_score = score_snapshot(snapshot).score
            snapshots.append(snapshot)

        except Exception as exc:
            print(f"{symbol}: {exc}")

    snapshots.sort(
        key=lambda item: item.technical_score,
        reverse=True,
    )

    return snapshots
