from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.exchanges.kraken import KrakenClient
from app.indicators.technical import atr, ema, macd, rsi, volume_ratio
from app.scanner.models import MarketSnapshot
from app.scanner.cross_pair_confirmation import evaluate_cross_pair_confirmation
from app.scanner.technical_scorer import score_snapshot
from app.scanner.universe import (
    DEFAULT_UNIQUE_ASSET_LIMIT,
    UniverseAsset,
    UniverseBuildResult,
    build_kraken_asset_universe,
)


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
    universe: UniverseBuildResult | None = None


@dataclass
class SecondaryConfirmationSummary:
    requested: int
    analyzed: int
    failed: int


def _percentage_change(current: float, previous: float) -> float:
    if previous <= 0:
        return 0.0
    return (current - previous) / previous * 100


def _window_metrics(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    hours: int,
) -> tuple[float, float, float, float]:
    """Return high, low, total range %, and mean hourly range %.

    Range percentages use the latest close as a stable common denominator.
    Hourly range is averaged after normalizing each candle by its close.
    """
    window_highs = highs[-hours:]
    window_lows = lows[-hours:]
    window_closes = closes[-hours:]
    recent_high = max(window_highs)
    recent_low = min(window_lows)
    current = closes[-1]
    realized_range_pct = (recent_high - recent_low) / current * 100
    hourly_ranges = [
        (high - low) / close * 100 if close > 0 else 0.0
        for high, low, close in zip(window_highs, window_lows, window_closes)
    ]
    average_hourly_range_pct = sum(hourly_ranges) / len(hourly_ranges)
    return recent_high, recent_low, realized_range_pct, average_hourly_range_pct


def _percentile(values: list[float], percentile: float) -> float:
    """Return a deterministic linearly interpolated percentile."""
    if not values:
        return 0.0
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (
        ordered[upper_index] - ordered[lower_index]
    ) * fraction


def _rolling_range_percentiles(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    hours: int,
) -> tuple[float, float, float]:
    """Calculate p50/p75/p90 of overlapping rolling high-low ranges.

    Each window is normalized by its ending close so assets with different
    prices remain comparable. With 200 candles this yields 177 observations
    for 24h windows and 129 observations for 72h windows.
    """
    ranges: list[float] = []
    for end in range(hours - 1, len(closes)):
        start = end - hours + 1
        ending_close = closes[end]
        if ending_close <= 0:
            continue
        window_range = max(highs[start:end + 1]) - min(lows[start:end + 1])
        ranges.append(window_range / ending_close * 100)
    return (
        _percentile(ranges, 50),
        _percentile(ranges, 75),
        _percentile(ranges, 90),
    )


def _rolling_upside_excursions(
    highs: list[float],
    closes: list[float],
    hours: int,
) -> list[float]:
    """Return fully observed forward favorable excursions for long entries.

    For entry reference close[s], the forward window contains exactly `hours`
    future candles: highs[s + 1:s + hours + 1]. Incomplete windows at the end
    are excluded, so N candles produce N - hours observations.
    """
    excursions: list[float] = []
    for start in range(len(closes) - hours):
        starting_reference = closes[start]
        if starting_reference <= 0:
            continue
        maximum_future_high = max(highs[start + 1:start + hours + 1])
        excursion_pct = (
            (maximum_future_high - starting_reference)
            / starting_reference
            * 100
        )
        excursions.append(max(0.0, excursion_pct))
    return excursions


def _rolling_upside_percentiles(
    highs: list[float],
    closes: list[float],
    hours: int,
) -> tuple[float, float, float]:
    excursions = _rolling_upside_excursions(highs, closes, hours)
    return (
        _percentile(excursions, 50),
        _percentile(excursions, 75),
        _percentile(excursions, 90),
    )


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


def analyze_symbol(
    symbol: str,
    universe_asset: UniverseAsset | None = None,
) -> tuple[str, MarketSnapshot | None, str | None]:
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

        (
            recent_24h_high,
            recent_24h_low,
            realized_range_24h_pct,
            average_hourly_range_24h_pct,
        ) = _window_metrics(highs, lows, closes, 24)
        (
            recent_72h_high,
            recent_72h_low,
            realized_range_72h_pct,
            average_hourly_range_72h_pct,
        ) = _window_metrics(highs, lows, closes, 72)
        rolling_24h_percentiles = _rolling_range_percentiles(
            highs, lows, closes, 24
        )
        rolling_72h_percentiles = _rolling_range_percentiles(
            highs, lows, closes, 72
        )
        rolling_24h_upside = _rolling_upside_percentiles(
            highs, closes, 24
        )
        rolling_72h_upside = _rolling_upside_percentiles(
            highs, closes, 72
        )

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
            recent_24h_high=recent_24h_high,
            recent_24h_low=recent_24h_low,
            recent_72h_high=recent_72h_high,
            recent_72h_low=recent_72h_low,
            momentum_6h_pct=_percentage_change(last_price, closes[-7]),
            momentum_24h_pct=_percentage_change(last_price, closes[-25]),
            momentum_72h_pct=_percentage_change(last_price, closes[-73]),
            distance_to_24h_high_pct=(recent_24h_high - last_price) / last_price * 100,
            distance_to_72h_high_pct=(recent_72h_high - last_price) / last_price * 100,
            realized_range_24h_pct=realized_range_24h_pct,
            realized_range_72h_pct=realized_range_72h_pct,
            average_hourly_range_24h_pct=average_hourly_range_24h_pct,
            average_hourly_range_72h_pct=average_hourly_range_72h_pct,
            rolling_24h_range_median_pct=rolling_24h_percentiles[0],
            rolling_24h_range_p75_pct=rolling_24h_percentiles[1],
            rolling_24h_range_p90_pct=rolling_24h_percentiles[2],
            rolling_72h_range_median_pct=rolling_72h_percentiles[0],
            rolling_72h_range_p75_pct=rolling_72h_percentiles[1],
            rolling_72h_range_p90_pct=rolling_72h_percentiles[2],
            rolling_24h_upside_median_pct=rolling_24h_upside[0],
            rolling_24h_upside_p75_pct=rolling_24h_upside[1],
            rolling_24h_upside_p90_pct=rolling_24h_upside[2],
            rolling_72h_upside_median_pct=rolling_72h_upside[0],
            rolling_72h_upside_p75_pct=rolling_72h_upside[1],
            rolling_72h_upside_p90_pct=rolling_72h_upside[2],
        )

        if universe_asset is not None:
            if universe_asset.primary_quote_currency == "USD":
                primary_liquidity = universe_asset.usd_24h_notional_usd
                secondary_liquidity = (
                    universe_asset.usdt_24h_notional_usd_equivalent
                )
            else:
                primary_liquidity = (
                    universe_asset.usdt_24h_notional_usd_equivalent
                )
                secondary_liquidity = universe_asset.usd_24h_notional_usd
            snapshot.underlying_asset = universe_asset.base_asset
            snapshot.primary_pair = universe_asset.primary_pair
            snapshot.secondary_pair = universe_asset.secondary_pair
            snapshot.primary_quote_currency = (
                universe_asset.primary_quote_currency
            )
            snapshot.primary_24h_liquidity_usd = primary_liquidity
            snapshot.secondary_24h_liquidity_usd = secondary_liquidity
            snapshot.combined_24h_liquidity_usd = (
                universe_asset.combined_24h_notional_usd
            )
            snapshot.liquidity_rank = universe_asset.liquidity_rank

        snapshot.technical_score = score_snapshot(snapshot).score

        return "ok", snapshot, None

    except Exception as exc:
        return "fail", None, f"{symbol}: {exc}"


def scan_market(
    limit: int = DEFAULT_UNIQUE_ASSET_LIMIT,
) -> ScanResult:
    universe_client = KrakenClient()
    universe = build_kraken_asset_universe(
        client=universe_client,
        limit=limit,
    )

    snapshots: list[MarketSnapshot] = []
    skips: list[str] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                analyze_symbol,
                asset.primary_pair,
                asset,
            ): asset.primary_pair
            for asset in universe.assets
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
        requested=len(universe.assets),
        analyzed=len(snapshots),
        skipped=len(skips),
        failed=len(failures),
        skips=sorted(skips),
        failures=sorted(failures),
        universe=universe,
    )


def confirm_secondary_markets(
    candidates: list[MarketSnapshot],
) -> SecondaryConfirmationSummary:
    """Fetch secondary OHLC only for shortlisted assets that have one."""
    requested_candidates = [
        candidate for candidate in candidates if candidate.secondary_pair
    ]
    for candidate in candidates:
        if not candidate.secondary_pair:
            result = evaluate_cross_pair_confirmation(candidate, None)
            candidate.cross_pair_confirmation_status = result.status
            candidate.cross_pair_strengths = result.strengths
            candidate.cross_pair_warnings = result.warnings

    analyzed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(analyze_symbol, candidate.secondary_pair): candidate
            for candidate in requested_candidates
        }
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                status, secondary, _ = future.result()
            except Exception:
                status, secondary = "fail", None
            if status == "ok" and secondary is not None:
                analyzed += 1
                candidate.secondary_volume_ratio = secondary.volume_ratio
            else:
                failed += 1
                secondary = None
            result = evaluate_cross_pair_confirmation(candidate, secondary)
            candidate.cross_pair_confirmation_status = result.status
            candidate.cross_pair_strengths = result.strengths
            candidate.cross_pair_warnings = result.warnings

    return SecondaryConfirmationSummary(
        requested=len(requested_candidates),
        analyzed=analyzed,
        failed=failed,
    )
