from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from app.exchanges.kraken import KrakenClient
from app.scanner.market_scanner import analyze_symbol
from app.scanner.models import MarketSnapshot
from app.scanner.universe import TICKER_BATCH_SIZE, _is_excluded_market, _market_symbols
from app.services.notification_policy import record_emitted, should_emit
from app.services.telegram_notifier import send_telegram_message


VERSION = "movement-discovery-v2"
WATCH = "WATCH"
READY = "READY"

DEFAULT_DEEP_CANDIDATES = 40
MIN_COARSE_LIFT_FROM_24H_LOW_PCT = 2.0
MAX_COARSE_DISTANCE_FROM_24H_HIGH_PCT = 6.0
MIN_DISCOVERY_NOTIONAL_USD = 2_500.0


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
    momentum_1h_pct: float
    momentum_6h_pct: float
    momentum_24h_pct: float
    relative_volume: float
    distance_to_24h_high_pct: float
    liquidity_24h_usd_approx: float
    extended_move: bool
    actionable: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return ":".join(
            (
                self.stage,
                str(int(self.discovery_score / 5) * 5),
                str(round(self.momentum_1h_pct, 1)),
                str(round(self.momentum_6h_pct, 1)),
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = self.fingerprint
        return payload


def _pct(current: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return (current / reference - 1.0) * 100.0


def _ticker_for_market(tickers: dict[str, dict[str, float]], pair_id: str, altname: str, display_pair: str):
    return tickers.get(pair_id) or tickers.get(altname) or tickers.get(display_pair)


def discover_coarse_movers(
    client: KrakenClient | None = None,
    *,
    max_candidates: int = DEFAULT_DEEP_CANDIDATES,
    min_notional_usd: float = MIN_DISCOVERY_NOTIONAL_USD,
) -> list[CoarseMover]:
    """Screen the complete Kraken USD/USDT universe using ticker data only.

    This stage is deliberately cheap: no OHLC, book, AI, news, or derivatives
    requests. It finds assets behaving like active upside movers and returns a
    small set for deep analysis. It is discovery evidence only and does not
    create a trade candidate.
    """
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
        display_pair = f"{base}{quote}"
        markets.append((pair_id, altname, display_pair, base, quote))

    tickers: dict[str, dict[str, float]] = {}
    pair_ids = sorted({item[0] for item in markets})
    for start in range(0, len(pair_ids), TICKER_BATCH_SIZE):
        batch = pair_ids[start:start + TICKER_BATCH_SIZE]
        try:
            tickers.update(client.get_tickers(batch))
        except Exception:
            # Discovery must fail open by batch. Other batches still provide
            # coverage and the main trading scanner remains independent.
            continue

    # One lifecycle identity per underlying. Prefer USD over USDT so discovery
    # names line up with the rest of OHM whenever both markets exist.
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
        last = float(ticker.get("last") or 0.0)
        high = float(ticker.get("high_24h") or 0.0)
        low = float(ticker.get("low_24h") or 0.0)
        volume = float(ticker.get("volume_24h") or 0.0)
        if min(last, high, low) <= 0 or high < low or volume <= 0:
            continue
        notional = last * volume
        if quote == "USDT":
            # Approximation is acceptable for discovery ranking only. Final
            # trade economics still use the live USDT/USD conversion path.
            notional = notional
        lift = _pct(last, low)
        distance = max(0.0, (high - last) / last * 100.0)
        if (
            notional < min_notional_usd
            or lift < MIN_COARSE_LIFT_FROM_24H_LOW_PCT
            or distance > MAX_COARSE_DISTANCE_FROM_24H_HIGH_PCT
        ):
            continue
        liquidity_component = max(0.0, min(15.0, math.log10(max(notional, 1.0)) * 2.0))
        coarse_score = lift * 4.0 + max(0.0, 6.0 - distance) * 2.0 + liquidity_component
        movers.append(
            CoarseMover(
                base_asset=base,
                primary_pair=display_pair,
                kraken_public_symbol=f"{base}/{quote}",
                last_price=last,
                volume_24h=volume,
                notional_24h_usd_approx=notional,
                high_24h=high,
                low_24h=low,
                lift_from_24h_low_pct=lift,
                distance_from_24h_high_pct=distance,
                coarse_score=round(coarse_score, 4),
            )
        )

    movers.sort(
        key=lambda item: (
            -item.coarse_score,
            -item.lift_from_24h_low_pct,
            -item.notional_24h_usd_approx,
            item.base_asset,
        )
    )
    return movers[:max_candidates]


def evaluate_early_mover(snapshot: MarketSnapshot, coarse: CoarseMover) -> EarlyMoverSignal | None:
    """Classify active upside acceleration without authorizing a trade."""
    one_hour = float(snapshot.confirmed_price_change_1h_pct)
    six_hour = float(snapshot.momentum_6h_pct)
    day = float(snapshot.momentum_24h_pct)
    volume = float(snapshot.movement_volume_ratio or snapshot.volume_ratio or 0.0)
    near_high = float(snapshot.distance_to_24h_high_pct)

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    if one_hour >= 2.0:
        score += 30
        reasons.append(f"1h momentum is accelerating at {one_hour:+.2f}%")
    elif one_hour >= 0.75:
        score += 20
        reasons.append(f"1h momentum is positive at {one_hour:+.2f}%")

    if six_hour >= 4.0:
        score += 25
        reasons.append(f"6h momentum is strong at {six_hour:+.2f}%")
    elif six_hour >= 2.0:
        score += 18
        reasons.append(f"6h momentum is building at {six_hour:+.2f}%")

    if day >= 8.0:
        score += 20
        reasons.append(f"24h momentum is strong at {day:+.2f}%")
    elif day >= 4.0:
        score += 14
        reasons.append(f"24h momentum is positive at {day:+.2f}%")

    if volume >= 2.5:
        score += 20
        reasons.append(f"relative volume expanded to {volume:.2f}x")
    elif volume >= 1.5:
        score += 14
        reasons.append(f"relative volume is elevated at {volume:.2f}x")

    if near_high <= 2.0:
        score += 10
        reasons.append(f"price is within {near_high:.2f}% of its 24h high")

    if snapshot.trend == "bullish":
        score += 8
        reasons.append("EMA structure is bullish")

    score = min(100, score)
    if score < 45:
        return None
    stage = READY if score >= 65 else WATCH

    extended = day >= 15.0 or one_hour >= 6.0
    if extended:
        warnings.append("move is already extended; discovery alert is not permission to chase")
    if coarse.notional_24h_usd_approx < 50_000:
        warnings.append("low approximate 24h liquidity; execution risk may be extreme")
    elif coarse.notional_24h_usd_approx < 250_000:
        warnings.append("limited approximate 24h liquidity; execution quality requires validation")

    return EarlyMoverSignal(
        version=VERSION,
        symbol=snapshot.symbol,
        base_asset=coarse.base_asset,
        stage=stage,
        direction="LONG",
        discovery_score=score,
        score_is_probability=False,
        momentum_1h_pct=round(one_hour, 4),
        momentum_6h_pct=round(six_hour, 4),
        momentum_24h_pct=round(day, 4),
        relative_volume=round(volume, 4),
        distance_to_24h_high_pct=round(near_high, 4),
        liquidity_24h_usd_approx=round(coarse.notional_24h_usd_approx, 2),
        extended_move=extended,
        actionable=False,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def scan_early_movers(
    client: KrakenClient | None = None,
    *,
    max_candidates: int = DEFAULT_DEEP_CANDIDATES,
) -> tuple[list[CoarseMover], list[EarlyMoverSignal]]:
    coarse = discover_coarse_movers(client, max_candidates=max_candidates)
    signals: list[EarlyMoverSignal] = []
    for mover in coarse:
        status, snapshot, _ = analyze_symbol(mover.primary_pair)
        if status != "ok" or snapshot is None:
            continue
        signal = evaluate_early_mover(snapshot, mover)
        if signal is not None:
            signals.append(signal)
    signals.sort(
        key=lambda item: (
            -item.discovery_score,
            -item.momentum_1h_pct,
            -item.momentum_6h_pct,
            item.symbol,
        )
    )
    return coarse, signals


def format_early_mover_message(signal: EarlyMoverSignal) -> str:
    warning = "\n".join(f"⚠️ {item}" for item in signal.warnings)
    reasons = "\n".join(f"• {item}" for item in signal.reasons[:6])
    return (
        f"🚀 OHM EARLY MOVER — {signal.stage}\n\n"
        f"Market: {signal.symbol}\n"
        f"Direction: {signal.direction}\n"
        f"Discovery score: {signal.discovery_score}/100 (not probability)\n"
        f"1h: {signal.momentum_1h_pct:+.2f}% | 6h: {signal.momentum_6h_pct:+.2f}% | "
        f"24h: {signal.momentum_24h_pct:+.2f}%\n"
        f"Relative volume: {signal.relative_volume:.2f}x\n"
        f"Approx 24h liquidity: ${signal.liquidity_24h_usd_approx:,.0f}\n"
        f"Distance to 24h high: {signal.distance_to_24h_high_pct:.2f}%\n\n"
        f"Evidence:\n{reasons}\n"
        + (f"\n{warning}\n" if warning else "\n")
        + "\nAction: MONITOR ONLY — this is not an entry signal. Existing OHM execution, economic, target, AI, and human-confirmation gates remain authoritative."
    )


def send_early_mover_update(
    signal: EarlyMoverSignal,
    *,
    bot_token: str,
    chat_id: str,
    cooldown_seconds: int = 1800,
) -> bool:
    if not should_emit(
        identity=f"EARLY_MOVER:{signal.symbol}",
        event_type=signal.stage,
        fingerprint=signal.fingerprint,
        cooldown_seconds=cooldown_seconds,
    ):
        return False
    sent = send_telegram_message(bot_token, chat_id, format_early_mover_message(signal))
    if sent:
        record_emitted(
            identity=f"EARLY_MOVER:{signal.symbol}",
            event_type=signal.stage,
            fingerprint=signal.fingerprint,
        )
    return sent
