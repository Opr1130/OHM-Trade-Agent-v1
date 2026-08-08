from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.exchanges.kraken import KrakenClient
from app.indicators.technical import atr, ema, macd, rsi, volume_ratio
from app.scanner.models import MarketSnapshot
from app.scanner.technical_scorer import score_snapshot
from app.scanner.universe import get_kraken_usd_universe


MIN_CANDLES_REQUIRED = 200
MAX_WORKERS = 8


@dataclass
class ScanResult:
    snapshots: list[MarketSnapshot]
    requested: int
    analyzed: int
    skipped: int
    failed: int
    skips: list[str]
    failures: list[str]


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


def analyze_symbol(symbol: str) -> tuple[str, MarketSnapshot | None, str | None]:
    client = KrakenClient()

    try:
        candles = client.get_ohlc(symbol, interval=60)

        if len(candles) < MIN_CANDLES_REQUIRED:
            return (
                "skip",
                None,
                f"{symbol}: insufficient history ({len(candles)} candles)",
            )

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

        return "ok", snapshot, None

    except Exception as exc:
        return "fail", None, f"{symbol}: {exc}"


def scan_market(limit: int = 100) -> ScanResult:
    universe_client = KrakenClient()
    symbols = get_kraken_usd_universe(
        client=universe_client,
        limit=limit,
    )

    snapshots: list[MarketSnapshot] = []
    skips: list[str] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(analyze_symbol, symbol): symbol
            for symbol in symbols
        }

        for future in as_completed(futures):
            status, snapshot, message = future.result()

            if status == "ok" and snapshot is not None:
                snapshots.append(snapshot)
            elif status == "skip" and message:
                skips.append(message)
            elif status == "fail" and message:
                failures.append(message)

    snapshots.sort(
        key=lambda item: item.technical_score,
        reverse=True,
    )

    return ScanResult(
        snapshots=snapshots,
        requested=len(symbols),
        analyzed=len(snapshots),
        skipped=len(skips),
        failed=len(failures),
        skips=sorted(skips),
        failures=sorted(failures),
    )
