from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from app.exchanges.kraken import Candle, KrakenClient
from app.indicators.technical import (
    atr,
    atr_percentage_series,
    bollinger_bandwidth_series,
    ema,
    macd,
    percentile_rank,
    rsi,
    volume_ratio,
)
from app.scanner.models import MarketSnapshot
from app.scanner.execution_validation import (
    INVALID,
    ExecutionValidation,
    evaluate_execution,
    unavailable_execution,
)
from app.scanner.market_data_validation import validate_market_data
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
EXECUTION_VALIDATION_MAX_POSITION_FRACTION = 0.20
MOVEMENT_COMPRESSION_PREFILTER_PERCENTILE = 40.0
MOVEMENT_MIN_CANDLES = 120


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
    data_quality_rejected: int = 0
    data_quality_warnings: int = 0
    data_quality_rejections: list[str] | None = None


@dataclass
class SecondaryConfirmationSummary:
    requested: int
    analyzed: int
    failed: int


def _movement_metrics(
    candles: list[Candle],
    *,
    interval_minutes: int,
) -> dict[str, Any]:
    """Calculate movement features from completed candles only."""
    if len(candles) < MOVEMENT_MIN_CANDLES:
        raise ValueError("insufficient candles for movement radar")
    completed = candles[:-1]
    closes = [item.close for item in completed]
    highs = [item.high for item in completed]
    lows = [item.low for item in completed]
    volumes = [item.volume for item in candles]

    bandwidths = bollinger_bandwidth_series(closes, 20, 2.0)
    atr_percentages = atr_percentage_series(highs, lows, closes, 14)
    current_bandwidth = bandwidths[-1]
    current_atr_percent = atr_percentages[-1]

    bars_24h = max(2, int(24 * 60 / interval_minutes))
    if len(completed) < bars_24h + 1:
        raise ValueError("insufficient completed candles for prior 24h range")
    previous_highs = highs[-(bars_24h + 1):-1]
    previous_lows = lows[-(bars_24h + 1):-1]

    bars_1h = max(1, int(60 / interval_minutes))
    if len(closes) < bars_1h + 1:
        raise ValueError("insufficient completed candles for 1h change")
    reference = closes[-(bars_1h + 1)]
    confirmed_close = closes[-1]
    price_change_1h = (
        (confirmed_close / reference - 1.0) * 100.0
        if reference > 0
        else 0.0
    )

    return {
        "movement_timeframe": "1H" if interval_minutes == 60 else f"{interval_minutes}M",
        "movement_data_status": "AVAILABLE",
        "bollinger_bandwidth_pct": current_bandwidth,
        "bollinger_bandwidth_percentile": percentile_rank(
            bandwidths,
            current_bandwidth,
        ),
        "atr_percentile": percentile_rank(
            atr_percentages,
            current_atr_percent,
        ),
        "movement_volume_ratio": volume_ratio(volumes, 20),
        "confirmed_close": confirmed_close,
        "confirmed_price_change_1h_pct": price_change_1h,
        "prior_24h_range_high": max(previous_highs),
        "prior_24h_range_low": min(previous_lows),
        "movement_warnings": [],
    }


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
    return recent_high, recent_low, realized_range_pct, sum(hourly_ranges) / len(hourly_ranges)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def _rolling_range_percentiles(
    highs: list[float], lows: list[float], closes: list[float], hours: int
) -> tuple[float, float, float]:
    ranges: list[float] = []
    for end in range(hours - 1, len(closes)):
        start = end - hours + 1
        ending_close = closes[end]
        if ending_close <= 0:
            continue
        window_range = max(highs[start:end + 1]) - min(lows[start:end + 1])
        ranges.append(window_range / ending_close * 100)
    return (_percentile(ranges, 50), _percentile(ranges, 75), _percentile(ranges, 90))


def _rolling_upside_excursions(
    highs: list[float], closes: list[float], hours: int
) -> list[float]:
    excursions: list[float] = []
    for start in range(len(closes) - hours):
        reference = closes[start]
        if reference <= 0:
            continue
        maximum_future_high = max(highs[start + 1:start + hours + 1])
        excursions.append(max(0.0, (maximum_future_high - reference) / reference * 100))
    return excursions


def _rolling_upside_percentiles(
    highs: list[float], closes: list[float], hours: int
) -> tuple[float, float, float]:
    values = _rolling_upside_excursions(highs, closes, hours)
    return (_percentile(values, 50), _percentile(values, 75), _percentile(values, 90))


def _rolling_downside_excursions(
    lows: list[float], closes: list[float], hours: int
) -> list[float]:
    """Fully observed favorable excursions for hypothetical short entries."""
    excursions: list[float] = []
    for start in range(len(closes) - hours):
        reference = closes[start]
        if reference <= 0:
            continue
        minimum_future_low = min(lows[start + 1:start + hours + 1])
        excursions.append(max(0.0, (reference - minimum_future_low) / reference * 100))
    return excursions


def _rolling_downside_percentiles(
    lows: list[float], closes: list[float], hours: int
) -> tuple[float, float, float]:
    values = _rolling_downside_excursions(lows, closes, hours)
    return (_percentile(values, 50), _percentile(values, 75), _percentile(values, 90))


def determine_trend(last_price: float, ema20: float, ema50: float, ema200: float) -> str:
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
            return "skip", None, f"{symbol}: insufficient history ({len(candles)} candles)"

        ticker_last = universe_asset.primary_ticker_last if universe_asset else None
        data_validation = validate_market_data(candles, ticker_last, interval_minutes=60)
        if not data_validation.qualified:
            return "data_reject", None, f"{symbol}: {'; '.join(data_validation.rejection_reasons)}"

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        last_price = closes[-1]
        ema20_value = ema(closes, 20)
        ema50_value = ema(closes, 50)
        ema200_value = ema(closes, 200)
        rsi_value = rsi(closes, 14)
        macd_line, macd_signal, macd_histogram = macd(closes)
        atr_value = atr(highs, lows, closes, 14)
        atr_pct = (atr_value / last_price) * 100
        volume_value = volume_ratio(volumes, 20)

        hourly_movement = _movement_metrics(candles, interval_minutes=60)
        movement = hourly_movement
        movement_warnings: list[str] = []
        compressed_hourly = (
            hourly_movement["bollinger_bandwidth_percentile"]
            <= MOVEMENT_COMPRESSION_PREFILTER_PERCENTILE
            or hourly_movement["atr_percentile"]
            <= MOVEMENT_COMPRESSION_PREFILTER_PERCENTILE
        )
        if compressed_hourly:
            try:
                movement = _movement_metrics(
                    client.get_ohlc(symbol, interval=15),
                    interval_minutes=15,
                )
            except Exception as exc:
                # The radar is advisory and must fail open to the existing 1H
                # evidence instead of rejecting an otherwise valid market.
                movement = dict(hourly_movement)
                movement_warnings.append(
                    f"15m movement evidence unavailable: {type(exc).__name__}"
                )
        movement["movement_warnings"] = movement_warnings

        recent_24h_high, recent_24h_low, realized_range_24h_pct, avg_hourly_24 = _window_metrics(highs, lows, closes, 24)
        recent_72h_high, recent_72h_low, realized_range_72h_pct, avg_hourly_72 = _window_metrics(highs, lows, closes, 72)
        rolling_24h_percentiles = _rolling_range_percentiles(highs, lows, closes, 24)
        rolling_72h_percentiles = _rolling_range_percentiles(highs, lows, closes, 72)
        rolling_24h_upside = _rolling_upside_percentiles(highs, closes, 24)
        rolling_72h_upside = _rolling_upside_percentiles(highs, closes, 72)
        rolling_24h_downside = _rolling_downside_percentiles(lows, closes, 24)
        rolling_72h_downside = _rolling_downside_percentiles(lows, closes, 72)
        trend = determine_trend(last_price, ema20_value, ema50_value, ema200_value)

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
            movement_timeframe=movement["movement_timeframe"],
            movement_data_status=movement["movement_data_status"],
            bollinger_bandwidth_pct=movement["bollinger_bandwidth_pct"],
            bollinger_bandwidth_percentile=movement["bollinger_bandwidth_percentile"],
            atr_percentile=movement["atr_percentile"],
            movement_volume_ratio=movement["movement_volume_ratio"],
            confirmed_close=movement["confirmed_close"],
            confirmed_price_change_1h_pct=movement["confirmed_price_change_1h_pct"],
            prior_24h_range_high=movement["prior_24h_range_high"],
            prior_24h_range_low=movement["prior_24h_range_low"],
            movement_warnings=movement["movement_warnings"],
            recent_24h_high=recent_24h_high,
            recent_24h_low=recent_24h_low,
            recent_72h_high=recent_72h_high,
            recent_72h_low=recent_72h_low,
            momentum_6h_pct=_percentage_change(last_price, closes[-7]),
            momentum_24h_pct=_percentage_change(last_price, closes[-25]),
            momentum_72h_pct=_percentage_change(last_price, closes[-73]),
            distance_to_24h_high_pct=(recent_24h_high - last_price) / last_price * 100,
            distance_to_72h_high_pct=(recent_72h_high - last_price) / last_price * 100,
            distance_to_24h_low_pct=(last_price - recent_24h_low) / last_price * 100,
            distance_to_72h_low_pct=(last_price - recent_72h_low) / last_price * 100,
            realized_range_24h_pct=realized_range_24h_pct,
            realized_range_72h_pct=realized_range_72h_pct,
            average_hourly_range_24h_pct=avg_hourly_24,
            average_hourly_range_72h_pct=avg_hourly_72,
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
            rolling_24h_downside_median_pct=rolling_24h_downside[0],
            rolling_24h_downside_p75_pct=rolling_24h_downside[1],
            rolling_24h_downside_p90_pct=rolling_24h_downside[2],
            rolling_72h_downside_median_pct=rolling_72h_downside[0],
            rolling_72h_downside_p75_pct=rolling_72h_downside[1],
            rolling_72h_downside_p90_pct=rolling_72h_downside[2],
            market_data_validation=data_validation,
        )

        if universe_asset is not None:
            if universe_asset.primary_quote_currency == "USD":
                primary_liquidity = universe_asset.usd_24h_notional_usd
                secondary_liquidity = universe_asset.usdt_24h_notional_usd_equivalent
            else:
                primary_liquidity = universe_asset.usdt_24h_notional_usd_equivalent
                secondary_liquidity = universe_asset.usd_24h_notional_usd
            snapshot.underlying_asset = universe_asset.base_asset
            snapshot.primary_pair = universe_asset.primary_pair
            snapshot.secondary_pair = universe_asset.secondary_pair
            snapshot.primary_quote_currency = universe_asset.primary_quote_currency
            snapshot.primary_24h_liquidity_usd = primary_liquidity
            snapshot.secondary_24h_liquidity_usd = secondary_liquidity
            snapshot.combined_24h_liquidity_usd = universe_asset.combined_24h_notional_usd
            snapshot.liquidity_rank = universe_asset.liquidity_rank
            snapshot.kraken_public_symbol = universe_asset.primary_kraken_symbol
            snapshot.ticker_last = universe_asset.primary_ticker_last
            snapshot.ticker_bid = universe_asset.primary_ticker_bid
            snapshot.ticker_ask = universe_asset.primary_ticker_ask

        snapshot.technical_score = score_snapshot(snapshot).score
        return "ok", snapshot, None
    except Exception as exc:
        return "fail", None, f"{symbol}: {exc}"


def scan_market(limit: int = DEFAULT_UNIQUE_ASSET_LIMIT) -> ScanResult:
    universe_client = KrakenClient()
    universe = build_kraken_asset_universe(client=universe_client, limit=limit)
    snapshots: list[MarketSnapshot] = []
    skips: list[str] = []
    failures: list[str] = []
    data_rejections: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(analyze_symbol, asset.primary_pair, asset): asset.primary_pair
            for asset in universe.assets
        }
        for future in as_completed(futures):
            status, snapshot, message = future.result()
            if status == "ok" and snapshot is not None:
                snapshots.append(snapshot)
            elif status == "skip" and message:
                skips.append(message)
            elif status == "data_reject" and message:
                data_rejections.append(message)
            elif status == "fail" and message:
                failures.append(message)

    snapshots.sort(key=lambda item: item.technical_score, reverse=True)
    return ScanResult(
        snapshots=snapshots,
        requested=len(universe.assets),
        analyzed=len(snapshots),
        skipped=len(skips),
        failed=len(failures),
        skips=sorted(skips),
        failures=sorted(failures),
        universe=universe,
        data_quality_rejected=len(data_rejections),
        data_quality_warnings=sum(
            snapshot.market_data_validation.status == "WARN"
            for snapshot in snapshots
            if snapshot.market_data_validation is not None
        ),
        data_quality_rejections=sorted(data_rejections),
    )


def confirm_secondary_markets(
    candidates: list[MarketSnapshot],
    usdt_usd_rate: float | None = None,
) -> SecondaryConfirmationSummary:
    requested_candidates = [candidate for candidate in candidates if candidate.secondary_pair]
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
            if secondary is not None and usdt_usd_rate is not None:
                normalized_secondary = secondary.last_price * usdt_usd_rate
                divergence = abs(normalized_secondary - candidate.last_price) / candidate.last_price * 100
                candidate.cross_pair_price_divergence_pct = divergence
                if divergence <= 0.50:
                    candidate.cross_pair_price_status = "NORMAL"
                elif divergence <= 2.00:
                    candidate.cross_pair_price_status = "WARNING"
                    candidate.cross_pair_warnings.append("Normalized USD/USDT prices differ by more than 0.50%")
                else:
                    candidate.cross_pair_price_status = "MATERIAL_DIVERGENCE"
                    candidate.cross_pair_warnings.append("Normalized USD/USDT prices differ by more than 2.00%")

    return SecondaryConfirmationSummary(
        requested=len(requested_candidates), analyzed=analyzed, failed=failed
    )


def deep_validate_candidates(
    candidates: list[MarketSnapshot],
    account_equity_usd: float,
    usdt_usd_rate: float | None,
    client: KrakenClient | None = None,
) -> list[MarketSnapshot]:
    client = client or KrakenClient()
    validated: list[MarketSnapshot] = []
    # Execution quality should model a plausible maximum posted position, not
    # pretend every candidate will consume the entire account. The 20% ceiling
    # matches the allocator's default max-position envelope and remains an
    # upper-bound validation notional before final per-trade sizing exists.
    validation_notional_usd = max(0.01, account_equity_usd * EXECUTION_VALIDATION_MAX_POSITION_FRACTION)
    for candidate in candidates[:8]:
        symbol = candidate.kraken_public_symbol or candidate.symbol
        try:
            book = client.get_pre_trade(symbol)
        except Exception as exc:
            candidate.execution_validation = unavailable_execution(f"PreTrade unavailable: {exc}")
            validated.append(candidate)
            continue
        try:
            trades = client.get_post_trade(symbol, count=100)
        except Exception:
            trades = None
        quote_rate = (
            usdt_usd_rate
            if candidate.primary_quote_currency == "USDT" and usdt_usd_rate
            else 1.0
        )
        result: ExecutionValidation = evaluate_execution(
            book=book,
            validation_notional_usd=validation_notional_usd,
            ticker_last=candidate.ticker_last or candidate.last_price,
            quote_to_usd_rate=quote_rate,
            trades=trades,
        )
        candidate.execution_validation = result
        if result.status != INVALID:
            validated.append(candidate)
    return validated
