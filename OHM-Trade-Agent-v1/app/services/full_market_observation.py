from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenClient
from app.scanner.universe import TICKER_BATCH_SIZE, _is_excluded_market, _market_symbols
from app.services.registry_io import load_json, registry_lock, save_json_atomic


VERSION = "full-market-observation-v1"
OBSERVATION_FILE = Path("/app/data/full_market_observations.jsonl")
STATE_FILE = Path("/app/data/full_market_observation_state.json")
ALERT_GOVERNOR_STATE_FILE = Path("/app/data/full_market_observation_alert_governor_state.json")
HEARTBEAT_SECONDS = 60 * 60
MIN_PERSIST_PRICE_CHANGE_PCT = 1.0
MIN_PERSIST_LIFT_CHANGE_PCT = 0.75
MIN_PERSIST_HIGH_DISTANCE_CHANGE_PCT = 0.75
MIN_NOTIONAL_RATIO_CHANGE = 1.50


@dataclass(frozen=True)
class MarketObservation:
    version: str
    base_asset: str
    symbol: str
    kraken_public_symbol: str
    last_price: float
    volume_24h: float
    notional_24h_usd_approx: float
    high_24h: float
    low_24h: float
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketTransition:
    version: str
    symbol: str
    pattern: str
    score: int
    price_change_since_prior_pct: float
    lift_change_since_prior_pct: float
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float
    liquidity_24h_usd_approx: float
    alert_tier: str
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    @property
    def transition_key(self) -> str:
        score_bucket = self.score // 10 * 10
        return f"{self.pattern}:{self.alert_tier}:{score_bucket}"


@dataclass(frozen=True)
class FullMarketResult:
    observed_markets: int
    persisted_events: int
    transition_alerts: tuple[MarketTransition, ...]


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _pct(current: float, reference: float) -> float:
    if not _finite(current, reference) or reference <= 0:
        return 0.0
    result = (current / reference - 1.0) * 100.0
    return result if math.isfinite(result) else 0.0


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def collect_full_market_observations(client: KrakenClient | None = None) -> list[MarketObservation]:
    """Observe every eligible Kraken spot base asset without tradeability filtering."""
    client = client or KrakenClient()
    pair_details = client.get_asset_pairs()
    markets: list[tuple[str, str, str, str, str]] = []
    for pair_id, details in pair_details.items():
        if _is_excluded_market(details):
            continue
        symbols = _market_symbols(details)
        if symbols is None:
            continue
        base, quote = symbols
        altname = str(details.get("altname", pair_id)).upper()
        markets.append((pair_id, altname, f"{base}{quote}", base, quote))

    tickers: dict[str, dict[str, float]] = {}
    pair_ids = sorted({row[0] for row in markets})
    for start in range(0, len(pair_ids), TICKER_BATCH_SIZE):
        try:
            tickers.update(client.get_tickers(pair_ids[start:start + TICKER_BATCH_SIZE]))
        except Exception:
            continue

    by_asset: dict[str, tuple[str, str, str, str, str, dict[str, float]]] = {}
    for pair_id, altname, display_pair, base, quote in markets:
        ticker = tickers.get(pair_id) or tickers.get(altname) or tickers.get(display_pair)
        if ticker is None:
            continue
        current = by_asset.get(base)
        if current is None or (current[4] != "USD" and quote == "USD"):
            by_asset[base] = (pair_id, altname, display_pair, base, quote, ticker)

    rows: list[MarketObservation] = []
    for _, _, display_pair, base, quote, ticker in by_asset.values():
        try:
            last = float(ticker.get("last") or 0.0)
            high = float(ticker.get("high_24h") or 0.0)
            low = float(ticker.get("low_24h") or 0.0)
            volume = float(ticker.get("volume_24h") or 0.0)
        except (TypeError, ValueError):
            continue
        if not _finite(last, high, low, volume):
            continue
        if min(last, high, low) <= 0 or high < low or volume <= 0:
            continue
        notional = last * volume
        lift = _pct(last, low)
        distance = max(0.0, (high - last) / last * 100.0)
        if not _finite(notional, lift, distance):
            continue
        rows.append(MarketObservation(
            version=VERSION,
            base_asset=base,
            symbol=display_pair,
            kraken_public_symbol=f"{base}/{quote}",
            last_price=round(last, 12),
            volume_24h=round(volume, 8),
            notional_24h_usd_approx=round(notional, 2),
            high_24h=round(high, 12),
            low_24h=round(low, 12),
            lift_from_24h_low_pct=round(lift, 6),
            distance_from_24h_high_pct=round(distance, 6),
        ))
    rows.sort(key=lambda row: row.base_asset)
    return rows


def _notional_ratio(current: float, previous: float) -> float:
    if not _finite(current, previous) or current <= 0 or previous <= 0:
        return 1.0
    ratio = max(current / previous, previous / current)
    return ratio if math.isfinite(ratio) else 1.0


def _previous_finite(previous: dict[str, Any], *keys: str) -> tuple[float, ...] | None:
    values: list[float] = []
    try:
        for key in keys:
            value = float(previous.get(key) or 0.0)
            if not math.isfinite(value):
                return None
            values.append(value)
    except (TypeError, ValueError):
        return None
    return tuple(values)


def _should_persist(current: MarketObservation, previous: dict[str, Any] | None, now: datetime) -> tuple[bool, str]:
    if previous is None:
        return True, "FIRST_OBSERVATION"
    previous_at = _parse_time(previous.get("recorded_at"))
    if previous_at is None or (now - previous_at).total_seconds() >= HEARTBEAT_SECONDS:
        return True, "HEARTBEAT"
    parsed = _previous_finite(
        previous,
        "last_price",
        "lift_from_24h_low_pct",
        "distance_from_24h_high_pct",
        "notional_24h_usd_approx",
    )
    if parsed is None:
        return True, "INVALID_PRIOR_STATE"
    prior_price, prior_lift, prior_distance, prior_notional = parsed
    if abs(_pct(current.last_price, prior_price)) >= MIN_PERSIST_PRICE_CHANGE_PCT:
        return True, "PRICE_CHANGE"
    if abs(current.lift_from_24h_low_pct - prior_lift) >= MIN_PERSIST_LIFT_CHANGE_PCT:
        return True, "LIFT_CHANGE"
    if abs(current.distance_from_24h_high_pct - prior_distance) >= MIN_PERSIST_HIGH_DISTANCE_CHANGE_PCT:
        return True, "HIGH_DISTANCE_CHANGE"
    if _notional_ratio(current.notional_24h_usd_approx, prior_notional) >= MIN_NOTIONAL_RATIO_CHANGE:
        return True, "LIQUIDITY_CHANGE"
    return False, "NO_MEANINGFUL_CHANGE"


def _transition(current: MarketObservation, previous: dict[str, Any] | None) -> MarketTransition | None:
    if previous is None:
        return None
    current_values = (
        current.last_price,
        current.notional_24h_usd_approx,
        current.lift_from_24h_low_pct,
        current.distance_from_24h_high_pct,
    )
    if not _finite(*current_values):
        return None
    parsed = _previous_finite(previous, "last_price", "lift_from_24h_low_pct")
    if parsed is None:
        return None
    prior_price, prior_lift = parsed
    if prior_price <= 0:
        return None
    price_change = _pct(current.last_price, prior_price)
    lift_change = current.lift_from_24h_low_pct - prior_lift
    near_high = current.distance_from_24h_high_pct
    if not _finite(price_change, lift_change, near_high):
        return None

    pattern: str | None = None
    if prior_lift <= 3.0 and current.lift_from_24h_low_pct >= 4.0 and lift_change >= 3.0 and near_high <= 4.0:
        pattern = "COMPRESSION_RELEASE"
    elif prior_lift >= 5.0 and lift_change >= 2.0 and price_change >= 1.5 and near_high <= 4.0:
        pattern = "REACCELERATION"
    elif current.lift_from_24h_low_pct >= 5.0 and lift_change >= 1.5 and price_change >= 1.25 and near_high <= 5.0:
        pattern = "PROGRESSIVE_EXPANSION"
    if pattern is None:
        return None

    score = 0.0
    score += min(35.0, max(0.0, lift_change) * 8.0)
    score += min(25.0, max(0.0, price_change) * 7.0)
    score += max(0.0, 20.0 - near_high * 4.0)
    if current.notional_24h_usd_approx >= 1_000_000:
        score += 15.0
    elif current.notional_24h_usd_approx >= 250_000:
        score += 12.0
    elif current.notional_24h_usd_approx >= 50_000:
        score += 7.0
    else:
        score += max(0.0, min(4.0, math.log10(max(current.notional_24h_usd_approx, 1.0))))
    if not math.isfinite(score):
        return None
    bounded = int(round(max(0.0, min(100.0, score))))
    if bounded < 60:
        return None

    alert_tier = "DEEP_REVIEW" if current.notional_24h_usd_approx >= 250_000 and bounded >= 75 else "WATCH_ONLY"
    return MarketTransition(
        version=VERSION,
        symbol=current.symbol,
        pattern=pattern,
        score=bounded,
        price_change_since_prior_pct=round(price_change, 4),
        lift_change_since_prior_pct=round(lift_change, 4),
        lift_from_24h_low_pct=current.lift_from_24h_low_pct,
        distance_from_24h_high_pct=current.distance_from_24h_high_pct,
        liquidity_24h_usd_approx=current.notional_24h_usd_approx,
        alert_tier=alert_tier,
    )


def process_full_market_observations(
    *,
    client: KrakenClient | None = None,
    now: datetime | None = None,
    observation_file: Path | None = None,
    state_file: Path | None = None,
) -> FullMarketResult:
    """Capture broad market evidence and derive high-signal transition watches."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = observation_file or OBSERVATION_FILE
    state_target = state_file or STATE_FILE
    observations = collect_full_market_observations(client)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"

    persisted = 0
    transitions: list[MarketTransition] = []
    with registry_lock(lock):
        state = load_json(state_target)
        latest = state.get("latest_by_symbol") or {}
        if not isinstance(latest, dict):
            raise ValueError("full-market latest_by_symbol state must be an object")
        with target.open("a", encoding="utf-8") as handle:
            for observation in observations:
                key = observation.symbol.upper()
                previous = latest.get(key)
                transition = _transition(observation, previous if isinstance(previous, dict) else None)
                if transition is not None:
                    transitions.append(transition)

                should_persist, reason = _should_persist(observation, previous if isinstance(previous, dict) else None, now)
                if not should_persist:
                    continue
                payload = {
                    "record_type": "FULL_MARKET_OBSERVATION",
                    "observed_at": now.isoformat(),
                    "capture_reason": reason,
                    **observation.as_dict(),
                    "learning_only": True,
                    "trade_authority_changed": False,
                    "production_execution_gate_changed": False,
                }
                handle.write(json.dumps(payload, sort_keys=True, default=str, allow_nan=False) + "\n")
                handle.flush()
                latest[key] = {**observation.as_dict(), "recorded_at": now.isoformat()}
                persisted += 1
        state["latest_by_symbol"] = latest
        state["last_scan_at"] = now.isoformat()
        state["observed_markets_last_scan"] = len(observations)
        save_json_atomic(state_target, state)

    transitions.sort(key=lambda row: (-row.score, -row.liquidity_24h_usd_approx, row.symbol))
    return FullMarketResult(
        observed_markets=len(observations),
        persisted_events=persisted,
        transition_alerts=tuple(transitions),
    )
