from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from functools import lru_cache
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
from app.scanner.technical_score_v2 import load_v2_config, score_long_v2, score_short_v2
from app.scanner.v2_features import extract_v2_features
from app.scanner.universe import (
    DEFAULT_UNIQUE_ASSET_LIMIT,
    UniverseAsset,
    UniverseBuildResult,
    build_kraken_asset_universe,
)


MIN_CANDLES_REQUIRED = 200
#: Historical hard-coded concurrency. Retained as the absolute ceiling: the
#: operator-facing setting may only tighten it, never widen it.
MAX_WORKERS = 8

#: Bounded deterministic replacement. A candidate can be legitimately ineligible
#: (a newly listed pair cannot yet supply MIN_CANDLES_REQUIRED hourly candles), and
#: one such candidate must not cap the whole scan below its target. The next
#: liquidity-ranked candidates are evaluated instead, up to a bounded budget so
#: provider load and runtime stay inside the existing attempt deadline. ``0``
#: reproduces the previous behaviour exactly.
DEFAULT_REPLACEMENT_CANDIDATES = 25
MAX_REPLACEMENT_CANDIDATES = 100


@lru_cache(maxsize=1)
def _read_v2_bundle():
    # Snapshot profile once per isolated worker; never mix versions per coin.
    from app.core.config import get_settings
    from app.core.strategy_policy import load_active_strategy_policy, load_strategy_policy

    active = (
        load_active_strategy_policy()
        if get_settings().opip_advisory_profile_enabled
        else load_strategy_policy()
    )
    authority = "production" if get_settings().opip_production_scoring_version == "v2" else "shadow"
    profile = replace(
        load_v2_config(), threshold=active.scoring["v2_threshold"], mode=authority,
    )
    return profile, active.digest


def _read_v2_profile():
    return _read_v2_bundle()[0]


def configured_max_workers() -> int:
    """Return the bounded acquisition concurrency for this process.

    Resolved lazily so this module stays importable without settings (tests
    import it directly) and so a malformed or over-wide configured value
    degrades to the previous behaviour instead of raising mid-scan.
    """
    try:
        from app.core.config import get_settings

        configured = int(getattr(get_settings(), "opip_scan_max_workers", MAX_WORKERS))
    except Exception:  # noqa: BLE001 - configuration must never break acquisition
        return MAX_WORKERS
    return max(1, min(MAX_WORKERS, configured))


def configured_batch_assets() -> int:
    """Return the acquisition batch size for this process.

    Batching bounds how many per-symbol working sets can coexist. It never
    changes the candidate population: selection runs after every batch has been
    collected.
    """
    try:
        from app.core.config import get_settings

        configured = int(getattr(get_settings(), "opip_scan_batch_assets", 50))
    except Exception:  # noqa: BLE001 - configuration must never break acquisition
        return 50
    return max(1, min(DEFAULT_UNIQUE_ASSET_LIMIT, configured))


def configured_replacement_candidates() -> int:
    """Return the bounded number of replacement candidates this process may evaluate.

    Resolved lazily so this module stays importable and usable without a complete
    settings environment. An *invalid* configured value is rejected earlier, by
    ``app.core.config.Settings`` itself: the acquisition worker reads settings on
    entry, so an out-of-range or unparseable ``OPIP_SCAN_REPLACEMENT_CANDIDATES``
    fails the attempt closed before any candidate is evaluated and is never
    silently replaced with the default here. This helper therefore only has to
    handle "settings are unavailable" (import-only/test context), where the
    documented default is the truthful answer.
    """
    try:
        from app.core.config import get_settings

        configured = int(
            getattr(
                get_settings(),
                "opip_scan_replacement_candidates",
                DEFAULT_REPLACEMENT_CANDIDATES,
            )
        )
    except Exception:  # noqa: BLE001 - configuration must never break acquisition
        return DEFAULT_REPLACEMENT_CANDIDATES
    return max(0, min(MAX_REPLACEMENT_CANDIDATES, configured))

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
    #: Every attempt made during this scan, including replacements. ``requested``
    #: stays the target and ``analyzed`` stays the count of valid snapshots, so a
    #: complete scan has ``attempted == analyzed`` plus any recorded exclusions.
    attempted: int = 0
    #: Candidates evaluated beyond the initial selection because an earlier
    #: candidate was legitimately excluded.
    replacement_attempts: int = 0
    #: Ranked candidates that were never evaluated (pool or budget exhaustion).
    remaining_candidate_capacity: int = 0
    #: Terminal classification of each excluded candidate, in attempt order.
    #: Persisted (bounded) by the isolated worker so an exclusion is never silent.
    exclusions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _AcquisitionTally:
    """Classify every attempted candidate exactly once.

    ``snapshots``/``skips``/``failures``/``data_quality_rejections`` keep the
    established counter meanings (total observations in this scan), while
    ``exclusions`` preserves identity and reason for each of them.
    """

    snapshots: list[MarketSnapshot] = field(default_factory=list)
    skips: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    data_rejections: list[str] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    attempted: int = 0

    def record(
        self,
        asset: UniverseAsset,
        status: str,
        snapshot: MarketSnapshot | None,
        message: str | None,
        stage: str,
    ) -> None:
        self.attempted += 1
        if status == "ok" and snapshot is not None:
            self.snapshots.append(snapshot)
            return
        reason = str(message or "")
        if status == "skip":
            self.skips.append(reason)
        elif status == "data_reject":
            self.data_rejections.append(reason)
        else:
            self.failures.append(reason)
        self.exclusions.append(
            {
                "pair": str(getattr(asset, "primary_pair", "") or ""),
                "base_asset": str(getattr(asset, "base_asset", "") or ""),
                "liquidity_rank": int(getattr(asset, "liquidity_rank", 0) or 0),
                "status": status,
                "reason": reason,
                "stage": stage,
            }
        )


@dataclass
class SecondaryConfirmationSummary:
    requested: int
    analyzed: int
    failed: int


def _movement_metrics(candles: list[Candle], *, interval_minutes: int) -> dict[str, Any]:
    """Calculate movement features from completed candles only."""
    if len(candles) < MOVEMENT_MIN_CANDLES:
        raise ValueError("insufficient candles for movement radar")
    completed = candles[:-1]
    closes = [item.close for item in completed]
    highs = [item.high for item in completed]
    lows = [item.low for item in completed]
    volumes = [item.volume for item in completed]

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
    price_change_1h = ((confirmed_close / reference - 1.0) * 100.0 if reference > 0 else 0.0)

    return {
        "movement_timeframe": "1H" if interval_minutes == 60 else f"{interval_minutes}M",
        "movement_data_status": "AVAILABLE",
        "bollinger_bandwidth_pct": current_bandwidth,
        "bollinger_bandwidth_percentile": percentile_rank(bandwidths, current_bandwidth),
        "atr_percentile": percentile_rank(atr_percentages, current_atr_percent),
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


def _window_metrics(highs: list[float], lows: list[float], closes: list[float], hours: int) -> tuple[float, float, float, float]:
    window_highs = highs[-hours:]
    window_lows = lows[-hours:]
    window_closes = closes[-hours:]
    recent_high = max(window_highs)
    recent_low = min(window_lows)
    current = closes[-1]
    realized_range_pct = (recent_high - recent_low) / current * 100
    hourly_ranges = [(high - low) / close * 100 if close > 0 else 0.0 for high, low, close in zip(window_highs, window_lows, window_closes)]
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


def _rolling_range_percentiles(highs: list[float], lows: list[float], closes: list[float], hours: int) -> tuple[float, float, float]:
    ranges: list[float] = []
    for end in range(hours - 1, len(closes)):
        start = end - hours + 1
        ending_close = closes[end]
        if ending_close <= 0:
            continue
        window_range = max(highs[start:end + 1]) - min(lows[start:end + 1])
        ranges.append(window_range / ending_close * 100)
    return (_percentile(ranges, 50), _percentile(ranges, 75), _percentile(ranges, 90))


def _rolling_upside_excursions(highs: list[float], closes: list[float], hours: int) -> list[float]:
    excursions: list[float] = []
    for start in range(len(closes) - hours):
        reference = closes[start]
        if reference <= 0:
            continue
        maximum_future_high = max(highs[start + 1:start + hours + 1])
        excursions.append(max(0.0, (maximum_future_high - reference) / reference * 100))
    return excursions


def _rolling_upside_percentiles(highs: list[float], closes: list[float], hours: int) -> tuple[float, float, float]:
    values = _rolling_upside_excursions(highs, closes, hours)
    return (_percentile(values, 50), _percentile(values, 75), _percentile(values, 90))


def _rolling_downside_excursions(lows: list[float], closes: list[float], hours: int) -> list[float]:
    excursions: list[float] = []
    for start in range(len(closes) - hours):
        reference = closes[start]
        if reference <= 0:
            continue
        minimum_future_low = min(lows[start + 1:start + hours + 1])
        excursions.append(max(0.0, (reference - minimum_future_low) / reference * 100))
    return excursions


def _rolling_downside_percentiles(lows: list[float], closes: list[float], hours: int) -> tuple[float, float, float]:
    values = _rolling_downside_excursions(lows, closes, hours)
    return (_percentile(values, 50), _percentile(values, 75), _percentile(values, 90))


def determine_trend(last_price: float, ema20: float, ema50: float, ema200: float) -> str:
    if last_price > ema20 > ema50 > ema200:
        return "bullish"
    if last_price < ema20 < ema50 < ema200:
        return "bearish"
    return "neutral"


def analyze_symbol(symbol: str, universe_asset: UniverseAsset | None = None) -> tuple[str, MarketSnapshot | None, str | None]:
    client = KrakenClient()
    try:
        candles = client.get_ohlc(symbol, interval=60)
        if len(candles) < MIN_CANDLES_REQUIRED:
            return "skip", None, f"{symbol}: insufficient history ({len(candles)} candles)"

        live_candle_price = float(candles[-1].close)
        ticker_last = universe_asset.primary_ticker_last if universe_asset else None
        data_validation = validate_market_data(candles, ticker_last, interval_minutes=60)
        if not data_validation.qualified:
            return "data_reject", None, f"{symbol}: {'; '.join(data_validation.rejection_reasons)}"

        # Kraken's last OHLC row may still be forming. All decision features are
        # therefore based only on completed candles. The current/live price is
        # retained separately in ticker_last for execution and monitoring.
        completed = candles[:-1]
        if len(completed) < MIN_CANDLES_REQUIRED - 1:
            return "skip", None, f"{symbol}: insufficient completed history ({len(completed)} candles)"
        closes = [c.close for c in completed]
        highs = [c.high for c in completed]
        lows = [c.low for c in completed]
        volumes = [c.volume for c in completed]
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
        compressed_hourly = (hourly_movement["bollinger_bandwidth_percentile"] <= MOVEMENT_COMPRESSION_PREFILTER_PERCENTILE or hourly_movement["atr_percentile"] <= MOVEMENT_COMPRESSION_PREFILTER_PERCENTILE)
        if compressed_hourly:
            try:
                movement = _movement_metrics(client.get_ohlc(symbol, interval=15), interval_minutes=15)
            except Exception as exc:
                movement = dict(hourly_movement)
                movement_warnings.append(f"15m movement evidence unavailable: {type(exc).__name__}")
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
        snapshot.ticker_last = float(ticker_last) if ticker_last is not None else live_candle_price

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
        # Parallel V2 evidence is opt-in and makes no extra market-data requests.
        # V1 remains the production ranking and qualification authority.
        from app.core.config import get_settings

        if bool(get_settings().opip_score_v2_evaluation_enabled) or get_settings().opip_production_scoring_version == "v2":
            try:
                profile, policy_digest = _read_v2_bundle()
                evidence = extract_v2_features(completed)
                long = score_long_v2(snapshot, evidence, profile)
                short = score_short_v2(snapshot, evidence, profile)
                snapshot.v2_profile_version = profile.version
                snapshot.v2_profile_digest = policy_digest
                snapshot.v2_long_score = long.score
                snapshot.v2_short_score = short.score
                snapshot.v2_long_components = dict(long.components)
                snapshot.v2_short_components = dict(short.components)
                snapshot.v2_feature_status = (
                    "READY" if long.status == "READY" and short.status == "READY"
                    else "INSUFFICIENT_FEATURES"
                )
            except Exception as exc:
                snapshot.v2_feature_status = f"ERROR:{type(exc).__name__}"
        return "ok", snapshot, None
    except Exception as exc:
        return "fail", None, f"{symbol}: {exc}"


def _evaluate_candidates(
    candidates: list[UniverseAsset], tally: _AcquisitionTally, *, stage: str
) -> None:
    """Evaluate candidates with the existing validation, recording every outcome.

    Batching is unchanged: assets are submitted in bounded batches so the number of
    simultaneously-live per-symbol working sets is capped by the batch size as well
    as by worker count. A candidate that raises unexpectedly is classified rather
    than allowed to void the whole attempt.
    """
    if not candidates:
        return
    batch_size = configured_batch_assets()
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        with ThreadPoolExecutor(max_workers=configured_max_workers()) as executor:
            futures = {
                executor.submit(analyze_symbol, asset.primary_pair, asset): asset
                for asset in batch
            }
            for future in as_completed(futures):
                asset = futures[future]
                try:
                    status, snapshot, message = future.result()
                except Exception as exc:  # noqa: BLE001 - classified, never hidden
                    status, snapshot, message = (
                        "fail",
                        None,
                        f"{asset.primary_pair}: {exc}",
                    )
                tally.record(asset, status, snapshot, message, stage)


def scan_market(limit: int = DEFAULT_UNIQUE_ASSET_LIMIT) -> ScanResult:
    universe_client = KrakenClient()
    universe = build_kraken_asset_universe(client=universe_client, limit=limit)
    target = max(0, int(limit))
    tally = _AcquisitionTally()

    selected = list(getattr(universe, "assets", []) or [])
    _evaluate_candidates(selected, tally, stage="initial")

    # Deterministic replacement. The universe builder already ranked every eligible
    # candidate, so the replacement pool is its suffix after the selection: no extra
    # AssetPairs or Ticker request is issued, only the OHLC read that an evaluation
    # always costs. Candidates are taken strictly in rank order and each underlying
    # asset is attempted at most once, so a replacement can never duplicate an
    # already-counted asset. Nothing is fabricated: a replacement must pass exactly
    # the same validation as an original candidate.
    attempted_bases = {asset.base_asset for asset in selected}
    ranked = list(getattr(universe, "ranked_assets", []) or [])
    pool = [
        asset
        for asset in ranked[len(selected):]
        if asset.base_asset not in attempted_bases
    ]
    budget = configured_replacement_candidates()
    replacements = 0
    for candidate in pool:
        if target <= 0 or len(tally.snapshots) >= target:
            break
        if replacements >= budget:
            break
        replacements += 1
        attempted_bases.add(candidate.base_asset)
        _evaluate_candidates([candidate], tally, stage="replacement")

    snapshots = sorted(
        tally.snapshots, key=lambda item: item.technical_score, reverse=True
    )
    coverage_exclusions = list(tally.exclusions)
    return ScanResult(
        snapshots=snapshots,
        requested=len(selected),
        analyzed=len(snapshots),
        skipped=len(tally.skips),
        failed=len(tally.failures),
        skips=sorted(tally.skips),
        failures=sorted(tally.failures),
        universe=universe,
        data_quality_rejected=len(tally.data_rejections),
        data_quality_warnings=sum(
            snapshot.market_data_validation.status == "WARN"
            for snapshot in snapshots
            if snapshot.market_data_validation is not None
        ),
        data_quality_rejections=sorted(tally.data_rejections),
        attempted=tally.attempted,
        replacement_attempts=replacements,
        remaining_candidate_capacity=max(0, len(pool) - replacements),
        exclusions=coverage_exclusions,
    )


def confirm_secondary_markets(candidates: list[MarketSnapshot], usdt_usd_rate: float | None = None) -> SecondaryConfirmationSummary:
    requested_candidates = [candidate for candidate in candidates if candidate.secondary_pair]
    for candidate in candidates:
        if not candidate.secondary_pair:
            result = evaluate_cross_pair_confirmation(candidate, None)
            candidate.cross_pair_confirmation_status = result.status
            candidate.cross_pair_strengths = result.strengths
            candidate.cross_pair_warnings = result.warnings

    analyzed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=configured_max_workers()) as executor:
        futures = {executor.submit(analyze_symbol, candidate.secondary_pair): candidate for candidate in requested_candidates}
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

    return SecondaryConfirmationSummary(requested=len(requested_candidates), analyzed=analyzed, failed=failed)


def deep_validate_candidates(candidates: list[MarketSnapshot], account_equity_usd: float, usdt_usd_rate: float | None, client: KrakenClient | None = None) -> list[MarketSnapshot]:
    client = client or KrakenClient()
    validated: list[MarketSnapshot] = []
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
        quote_rate = usdt_usd_rate if candidate.primary_quote_currency == "USDT" and usdt_usd_rate else 1.0
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