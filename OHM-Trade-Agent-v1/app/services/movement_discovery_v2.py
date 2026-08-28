from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Protocol

from app.exchanges.kraken import KrakenClient
from app.scanner.market_scanner import analyze_symbol
from app.scanner.models import MarketSnapshot
from app.scanner.universe import TICKER_BATCH_SIZE, _is_excluded_market, _market_symbols
from app.services.asset_display_identity import display_market_label
from app.services.notification_policy import record_emitted, should_emit
from app.services.telegram_delivery import (
    record_telegram_not_eligible,
    record_telegram_suppression,
    send_tracked_telegram,
)

VERSION = "movement-discovery-v2.1"
WATCH = "WATCH"
READY = "READY"
ENTRY_BREAKOUT = "BREAKOUT_ENTRY_POSSIBLE"
ENTRY_PULLBACK = "WAIT_FOR_PULLBACK"
ENTRY_WATCH = "WATCH_ONLY"
ENTRY_TOO_EXTENDED = "TOO_EXTENDED"
ENTRY_HIGH_RISK = "HIGH_RISK_WATCH_ONLY"
MOMENTUM_ACCELERATING = "ACCELERATING"
MOMENTUM_STEADY = "STEADY"
MOMENTUM_DECELERATING = "DECELERATING"
DEFAULT_DEEP_CANDIDATES = 40
MIN_COARSE_LIFT_FROM_24H_LOW_PCT = 2.0
MAX_COARSE_DISTANCE_FROM_24H_HIGH_PCT = 6.0
MIN_DISCOVERY_NOTIONAL_USD = 2_500.0
DEFAULT_LEARNING_PATH = "/app/data/movement_discovery_v2_1.jsonl"


@dataclass(frozen=True)
class FlowEvidence:
    available: bool = False
    bias: str = "NEUTRAL"
    strength: int = 0
    source: str = "NONE"
    freshness_seconds: float | None = None


@dataclass(frozen=True)
class WhaleEvidence:
    available: bool = False
    bias: str = "NEUTRAL"
    strength: int = 0
    source: str = "NONE"
    freshness_seconds: float | None = None
    exchange_inflow_usd: float | None = None
    exchange_outflow_usd: float | None = None
    large_transfer_count: int | None = None


@dataclass(frozen=True)
class SocialEvidence:
    available: bool = False
    bias: str = "NEUTRAL"
    strength: int = 0
    source: str = "NONE"
    freshness_seconds: float | None = None
    mention_velocity_ratio: float | None = None
    unique_author_velocity_ratio: float | None = None


class FlowEvidenceProvider(Protocol):
    def get_flow_evidence(self, *, base_asset: str, symbol: str) -> FlowEvidence: ...


class WhaleEvidenceProvider(Protocol):
    def get_whale_evidence(self, *, base_asset: str, symbol: str) -> WhaleEvidence: ...


class SocialEvidenceProvider(Protocol):
    def get_social_evidence(self, *, base_asset: str, symbol: str) -> SocialEvidence: ...


@dataclass(frozen=True)
class CoarseMover:
    base_asset: str
    primary_pair: str
    kraken_public_symbol: str
    last_price: float
    volume_24h: float
    notional_24h_usd_approx: float
    high_24h: float
    low_24h: float
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float
    coarse_score: float


@dataclass(frozen=True)
class EarlyMoverSignal:
    version: str
    symbol: str
    base_asset: str
    stage: str
    direction: str
    discovery_score: int
    score_is_probability: bool
    continuation_confidence: int
    continuation_confidence_is_probability: bool
    entry_quality: int
    entry_recommendation: str
    momentum_state: str
    momentum_1h_pct: float
    momentum_6h_pct: float
    momentum_24h_pct: float
    relative_volume: float
    distance_to_24h_high_pct: float
    liquidity_24h_usd_approx: float
    extended_move: bool
    flow_confirmation: str
    whale_confirmation: str
    social_confirmation: str
    alert_eligible: bool
    actionable: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    reference_price: float = 0.0
    detection_timeframe: str = "1H"

    @property
    def fingerprint(self) -> str:
        return ":".join((self.stage, str(self.continuation_confidence // 5 * 5),
                         str(self.entry_quality // 5 * 5), self.entry_recommendation,
                         self.momentum_state))

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = self.fingerprint
        return payload


def _pct(current: float, reference: float) -> float:
    return 0.0 if reference <= 0 else (current / reference - 1.0) * 100.0


def _ticker_for_market(tickers: dict[str, dict[str, float]], pair_id: str, altname: str, display_pair: str):
    return tickers.get(pair_id) or tickers.get(altname) or tickers.get(display_pair)


def discover_coarse_movers(client: KrakenClient | None = None, *, max_candidates: int = DEFAULT_DEEP_CANDIDATES,
                            min_notional_usd: float = MIN_DISCOVERY_NOTIONAL_USD) -> list[CoarseMover]:
    """Cheap, permissive full-universe discovery. Never authorizes a trade."""
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
    pair_ids = sorted({item[0] for item in markets})
    for start in range(0, len(pair_ids), TICKER_BATCH_SIZE):
        try:
            tickers.update(client.get_tickers(pair_ids[start:start + TICKER_BATCH_SIZE]))
        except Exception:
            continue

    by_asset: dict[str, tuple[str, str, str, str, str, dict[str, float]]] = {}
    for pair_id, altname, display_pair, base, quote in markets:
        ticker = _ticker_for_market(tickers, pair_id, altname, display_pair)
        if ticker is None:
            continue
        current = by_asset.get(base)
        if current is None or (current[4] != "USD" and quote == "USD"):
            by_asset[base] = (pair_id, altname, display_pair, base, quote, ticker)

    movers: list[CoarseMover] = []
    for _, _, display_pair, base, quote, ticker in by_asset.values():
        try:
            last = float(ticker.get("last") or 0.0)
            high = float(ticker.get("high_24h") or 0.0)
            low = float(ticker.get("low_24h") or 0.0)
            volume = float(ticker.get("volume_24h") or 0.0)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (last, high, low, volume)):
            continue
        if min(last, high, low) <= 0 or high < low or volume <= 0:
            continue
        notional = last * volume
        lift = _pct(last, low)
        distance = max(0.0, (high - last) / last * 100.0)
        if not all(math.isfinite(value) for value in (notional, lift, distance)):
            continue
        if notional < min_notional_usd or lift < MIN_COARSE_LIFT_FROM_24H_LOW_PCT or distance > MAX_COARSE_DISTANCE_FROM_24H_HIGH_PCT:
            continue
        liquidity_component = max(0.0, min(15.0, math.log10(max(notional, 1.0)) * 2.0))
        coarse_score = lift * 4.0 + max(0.0, 6.0 - distance) * 2.0 + liquidity_component
        if not math.isfinite(coarse_score):
            continue
        movers.append(CoarseMover(base, display_pair, f"{base}/{quote}", last, volume, notional,
                                  high, low, lift, distance, round(coarse_score, 4)))

    movers.sort(key=lambda item: (-item.coarse_score, -item.lift_from_24h_low_pct,
                                  -item.notional_24h_usd_approx, item.base_asset))
    return movers[:max_candidates]


def _momentum_state(one_hour: float, six_hour: float, day: float) -> str:
    if one_hour >= 1.0 and six_hour >= 2.0 and day >= 3.0:
        return MOMENTUM_ACCELERATING
    if (day >= 8.0 and one_hour <= 0.25) or (six_hour >= 4.0 and one_hour < 0.50):
        return MOMENTUM_DECELERATING
    return MOMENTUM_STEADY


def _optional_adjustment(flow: FlowEvidence | None, whale: WhaleEvidence | None, social: SocialEvidence | None):
    adjustment = 0
    reasons: list[str] = []
    warnings: list[str] = []
    statuses = []
    for label, evidence, positive_cap, negative_cap in (
        ("trade-flow", flow, 10, 12), ("whale", whale, 8, 10), ("social", social, 6, 6)
    ):
        status = "UNAVAILABLE"
        if evidence is not None and evidence.available:
            status = evidence.bias
            strength = max(0, int(evidence.strength))
            if evidence.bias == "BULLISH":
                adjustment += min(positive_cap, strength)
                reasons.append(f"{label} evidence is supportive ({strength}/10)")
            elif evidence.bias == "BEARISH":
                adjustment -= min(negative_cap, strength)
                warnings.append(f"{label} evidence is adverse ({strength}/10)")
        statuses.append(status)
    return adjustment, reasons, warnings, statuses


def evaluate_early_mover(snapshot: MarketSnapshot, coarse: CoarseMover, *, flow_evidence: FlowEvidence | None = None,
                         whale_evidence: WhaleEvidence | None = None, social_evidence: SocialEvidence | None = None) -> EarlyMoverSignal | None:
    """Score discovery separately from continuation and entry quality."""
    try:
        one_hour = float(snapshot.confirmed_price_change_1h_pct)
        six_hour = float(snapshot.momentum_6h_pct)
        day = float(snapshot.momentum_24h_pct)
        volume = float(snapshot.movement_volume_ratio or snapshot.volume_ratio or 0.0)
        near_high = float(snapshot.distance_to_24h_high_pct)
        liquidity = float(coarse.notional_24h_usd_approx)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (one_hour, six_hour, day, volume, near_high, liquidity)):
        return None
    if volume < 0 or near_high < 0 or liquidity < 0:
        return None

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    if one_hour >= 2.0:
        score += 30; reasons.append(f"1h momentum is accelerating at {one_hour:+.2f}%")
    elif one_hour >= 0.75:
        score += 20; reasons.append(f"1h momentum is positive at {one_hour:+.2f}%")
    if six_hour >= 4.0:
        score += 25; reasons.append(f"6h momentum is strong at {six_hour:+.2f}%")
    elif six_hour >= 2.0:
        score += 18; reasons.append(f"6h momentum is building at {six_hour:+.2f}%")
    if day >= 8.0:
        score += 20; reasons.append(f"24h momentum is strong at {day:+.2f}%")
    elif day >= 4.0:
        score += 14; reasons.append(f"24h momentum is positive at {day:+.2f}%")
    if volume >= 2.5:
        score += 20; reasons.append(f"relative volume expanded to {volume:.2f}x")
    elif volume >= 1.5:
        score += 14; reasons.append(f"relative volume is elevated at {volume:.2f}x")
    if near_high <= 2.0:
        score += 10; reasons.append(f"price is within {near_high:.2f}% of its 24h high")
    if snapshot.trend == "bullish":
        score += 8; reasons.append("EMA structure is bullish")
    score = min(100, score)
    if score < 45:
        return None

    state = _momentum_state(one_hour, six_hour, day)
    continuation = score
    if volume < 0.50:
        continuation -= 18; warnings.append(f"relative volume is weak at {volume:.2f}x")
    elif volume < 1.00:
        continuation -= 8; warnings.append(f"relative volume is below baseline at {volume:.2f}x")
    if liquidity < 50_000:
        continuation -= 20; warnings.append("low approximate 24h liquidity; execution risk may be extreme")
    elif liquidity < 250_000:
        continuation -= 8; warnings.append("limited approximate 24h liquidity; execution quality requires validation")
    if state == MOMENTUM_ACCELERATING:
        continuation += 8; reasons.append("multi-horizon momentum is accelerating")
    elif state == MOMENTUM_DECELERATING:
        continuation -= 15; warnings.append("momentum is decelerating across the latest horizon")

    extended = day >= 15.0 or one_hour >= 6.0
    if extended:
        continuation -= 8; warnings.append("move is already extended; discovery alert is not permission to chase")

    adjustment, opt_reasons, opt_warnings, statuses = _optional_adjustment(flow_evidence, whale_evidence, social_evidence)
    continuation = max(0, min(100, continuation + adjustment))
    reasons.extend(opt_reasons); warnings.extend(opt_warnings)

    entry_quality = 50
    entry_quality += 15 if state == MOMENTUM_ACCELERATING else (-20 if state == MOMENTUM_DECELERATING else 0)
    entry_quality += 10 if volume >= 1.5 else (-15 if volume < 0.5 else (-8 if volume < 1.0 else 0))
    entry_quality += 8 if near_high <= 1.0 else (-8 if near_high > 4.0 else 0)
    entry_quality += 8 if snapshot.trend == "bullish" else 0
    entry_quality -= 30 if extended else 0
    entry_quality -= 25 if liquidity < 50_000 else (8 if liquidity < 250_000 else 0)
    entry_quality = max(0, min(100, entry_quality))

    if liquidity < 50_000:
        recommendation = ENTRY_HIGH_RISK
    elif extended and entry_quality < 55:
        recommendation = ENTRY_TOO_EXTENDED
    elif extended:
        recommendation = ENTRY_PULLBACK
    elif state == MOMENTUM_ACCELERATING and entry_quality >= 65:
        recommendation = ENTRY_BREAKOUT
    elif entry_quality >= 50:
        recommendation = ENTRY_PULLBACK
    else:
        recommendation = ENTRY_WATCH

    stage = READY if (continuation >= 65 and liquidity >= 50_000 and volume >= 0.50) else WATCH
    alert_eligible = (stage == READY and entry_quality >= 55 and recommendation not in {ENTRY_TOO_EXTENDED, ENTRY_HIGH_RISK})

    return EarlyMoverSignal(VERSION, snapshot.symbol, coarse.base_asset, stage, "LONG", score, False,
                            continuation, False, entry_quality, recommendation, state, round(one_hour, 4),
                            round(six_hour, 4), round(day, 4), round(volume, 4), round(near_high, 4),
                            round(liquidity, 2), extended, statuses[0], statuses[1],
                            statuses[2], alert_eligible, False, tuple(reasons), tuple(warnings),
                            reference_price=round(float(snapshot.last_price), 8),
                            detection_timeframe=str(snapshot.movement_timeframe or "1H"))


def scan_early_movers(client: KrakenClient | None = None, *, max_candidates: int = DEFAULT_DEEP_CANDIDATES):
    coarse = discover_coarse_movers(client, max_candidates=max_candidates)
    signals: list[EarlyMoverSignal] = []
    for mover in coarse:
        status, snapshot, _ = analyze_symbol(mover.primary_pair)
        if status == "ok" and snapshot is not None:
            signal = evaluate_early_mover(snapshot, mover)
            if signal is not None:
                signals.append(signal)
    signals.sort(key=lambda item: (-item.continuation_confidence, -item.entry_quality, -item.discovery_score, item.symbol))
    return coarse, signals


def append_detection_snapshots(signals: list[EarlyMoverSignal], *, path: str = DEFAULT_LEARNING_PATH) -> int:
    """Capture immutable detection-time evidence; never backfill future outcomes here."""
    if not signals:
        return 0
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for signal in signals:
            payload = signal.as_dict()
            payload["record_type"] = "DETECTION"
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return len(signals)


def format_early_mover_message(signal: EarlyMoverSignal) -> str:
    warning = "; ".join(str(item) for item in signal.warnings[:2])
    reason = "; ".join(str(item) for item in signal.reasons[:3]) or "Early movement conditions detected"
    caution = f" | Caution: {warning}" if warning else ""
    return (
        f"🚀 OHM EARLY WATCH — {display_market_label(signal.symbol)} — {signal.stage}\n"
        f"Price: {signal.reference_price:.8g} | TF: {signal.detection_timeframe}\n"
        f"Momentum: 1h {signal.momentum_1h_pct:+.2f}% | 6h {signal.momentum_6h_pct:+.2f}% | 24h {signal.momentum_24h_pct:+.2f}%\n"
        f"Continuation*: {signal.continuation_confidence}/100 | Entry quality*: {signal.entry_quality}/100\n"
        f"Volume: {signal.relative_volume:.2f}x | Liquidity: ${signal.liquidity_24h_usd_approx:,.0f}/24h\n"
        f"Why now: {reason}{caution}\n"
        f"Action: {signal.entry_recommendation.replace('_', ' ')} — EARLY WATCH ONLY; no entry is authorized\n"
        "*Heuristic scores, not probabilities."
    )


def send_early_mover_update(signal: EarlyMoverSignal, *, bot_token: str, chat_id: str, cooldown_seconds: int = 1800) -> bool:
    identity = f"EARLY_MOVER:{signal.symbol}"
    if not signal.alert_eligible:
        record_telegram_not_eligible(
            identity=identity,
            alert_family="EARLY_MOVER",
            event_type=signal.stage,
            fingerprint=signal.fingerprint,
            reason="ALERT_NOT_ELIGIBLE",
            symbol=signal.symbol,
        )
        return False
    if not should_emit(identity=identity, event_type=signal.stage,
                       fingerprint=signal.fingerprint, cooldown_seconds=cooldown_seconds):
        record_telegram_suppression(
            identity=identity,
            alert_family="EARLY_MOVER",
            event_type=signal.stage,
            fingerprint=signal.fingerprint,
            reason="NOTIFICATION_POLICY",
            symbol=signal.symbol,
        )
        return False
    delivery = send_tracked_telegram(
        bot_token=bot_token,
        chat_id=chat_id,
        message=format_early_mover_message(signal),
        identity=identity,
        alert_family="EARLY_MOVER",
        event_type=signal.stage,
        fingerprint=signal.fingerprint,
        symbol=signal.symbol,
    )
    if delivery.delivered:
        record_emitted(identity=identity, event_type=signal.stage, fingerprint=signal.fingerprint)
    return delivery.delivered
