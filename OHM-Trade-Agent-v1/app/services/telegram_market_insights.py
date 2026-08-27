from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from app.exchanges.kraken import KrakenClient
from app.exchanges.kraken_identity import canonicalize_asset, canonicalize_pair
from app.scanner.market_scanner import analyze_symbol
from app.scanner.models import MarketSnapshot
from app.scanner.universe import _is_excluded_market
from app.services.asset_display_identity import display_market_label
from app.services.explosion_state import build_explosion_state_vector
from app.services.native_flow_evidence import NativeFlowShadowResult, evaluate_native_flow_shadow


SUPPORTED_QUOTES = ("USD", "USDT")
MAX_SYMBOL_INPUT_LENGTH = 24


class MarketResolutionError(ValueError):
    """Raised when a Telegram symbol cannot be mapped to a Kraken spot pair."""


class MarketInsightUnavailable(RuntimeError):
    """Raised when a safe deterministic market insight cannot be produced."""


@dataclass(frozen=True)
class MarketIdentity:
    base_asset: str
    quote_asset: str
    pair_id: str
    altname: str
    display_pair: str
    ws_symbol: str


@dataclass(frozen=True)
class MarketInsight:
    symbol: str
    pair: str
    current_price: float
    state: str
    phase_score: int
    risk: str
    action: str
    tradeability: str
    momentum_1h_pct: float
    momentum_6h_pct: float
    momentum_24h_pct: float
    relative_volume: float
    spread_bps: float
    distance_to_24h_high_pct: float
    historical_upside_median_pct: float
    historical_upside_p75_pct: float
    historical_downside_median_pct: float
    historical_downside_p75_pct: float
    flow_bias: str
    flow_strength: int
    reason: str
    warnings: tuple[str, ...]
    rsi: float
    atr_pct: float
    trend: str

    def material_signature(self) -> tuple[str, str, str, str]:
        return (self.state, self.action, self.risk, self.tradeability)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _positive_finite(value: Any) -> float | None:
    parsed = _finite(value, math.nan)
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _clean_query(value: str) -> str:
    cleaned = str(value or "").strip().upper().lstrip("$#")
    if not cleaned:
        raise MarketResolutionError("Add a coin, for example /scan VVV")
    if len(cleaned) > MAX_SYMBOL_INPUT_LENGTH:
        raise MarketResolutionError("Coin symbol is too long")
    if not re.fullmatch(r"[A-Z0-9._/\-]+", cleaned):
        raise MarketResolutionError("Coin symbol contains unsupported characters")
    return cleaned


def resolve_market(query: str, client: KrakenClient | None = None) -> MarketIdentity:
    """Resolve a user symbol from Kraken's live spot-pair table.

    Bare assets prefer an exact live base-asset match before suffix parsing,
    then prefer USD and USDT. Explicit USD/USDT requests preserve the requested
    quote. Exact-base-first avoids treating a legitimate asset whose ticker ends
    in USD/USDT as if the suffix were necessarily a quote currency.
    """
    client = client or KrakenClient(timeout_seconds=5.0)
    cleaned = _clean_query(query)

    try:
        pair_details = client.get_asset_pairs()
    except Exception as exc:
        raise MarketInsightUnavailable(f"Kraken pair directory unavailable ({type(exc).__name__})") from exc

    def collect_candidates(requested_base: str, explicit_quote: str | None) -> list[MarketIdentity]:
        candidates: list[MarketIdentity] = []
        for pair_id, details in pair_details.items():
            if not isinstance(details, dict) or _is_excluded_market(details):
                continue
            base = canonicalize_asset(str(details.get("base") or ""))
            quote = canonicalize_asset(str(details.get("quote") or ""))
            if base != requested_base or quote not in SUPPORTED_QUOTES:
                continue
            if explicit_quote is not None and quote != explicit_quote:
                continue
            altname = str(details.get("altname") or pair_id).upper()
            ws_symbol = str(details.get("wsname") or f"{base}/{quote}").upper()
            candidates.append(
                MarketIdentity(
                    base_asset=base,
                    quote_asset=quote,
                    pair_id=str(pair_id),
                    altname=altname,
                    display_pair=f"{base}{quote}",
                    ws_symbol=ws_symbol,
                )
            )
        candidates.sort(
            key=lambda item: (
                item.quote_asset != "USD",
                ".D" in item.altname,
                item.altname,
                item.pair_id,
            )
        )
        return candidates

    # A plain ticker is first treated as a base asset exactly as written after
    # Kraken identity normalization. This protects assets such as BUSD from
    # being misread as B/USD while preserving XBT -> BTC alias behavior.
    if re.fullmatch(r"[A-Z0-9]+", cleaned):
        exact_base = canonicalize_asset(cleaned)
        if exact_base:
            exact_candidates = collect_candidates(exact_base, None)
            if exact_candidates:
                return exact_candidates[0]

    canonical = canonicalize_pair(cleaned)
    explicit_quote: str | None = None
    requested_base = canonical
    for quote in ("USDT", "USD"):
        if canonical.endswith(quote) and len(canonical) > len(quote):
            explicit_quote = quote
            requested_base = canonical[: -len(quote)]
            break
    requested_base = canonicalize_asset(requested_base)
    if not requested_base:
        raise MarketResolutionError("Coin symbol is empty")

    candidates = collect_candidates(requested_base, explicit_quote)
    if not candidates:
        quote_text = explicit_quote or "USD/USDT"
        raise MarketResolutionError(f"No online Kraken {quote_text} spot market found for {requested_base}")
    return candidates[0]


def _state_overlay(snapshot: MarketSnapshot, phase: str, acceleration: float, relative_volume: float) -> str:
    one_hour = _finite(snapshot.confirmed_price_change_1h_pct)
    six_hour = _finite(snapshot.momentum_6h_pct)
    day = _finite(snapshot.momentum_24h_pct)
    near_high = max(0.0, _finite(snapshot.distance_to_24h_high_pct))

    reversal = (
        day >= 6.0 and one_hour <= -1.5 and near_high >= 3.0
    ) or (
        six_hour >= 3.0 and one_hour <= -2.0
    )
    if reversal:
        return "REVERSAL_RISK"

    reacceleration = (
        day >= 4.0
        and six_hour >= 1.0
        and one_hour >= 0.75
        and acceleration >= 0.25
        and relative_volume >= 1.10
        and near_high <= 6.0
        and phase not in {"LATE_EXTENSION", "EXHAUSTION_RISK"}
    )
    if reacceleration:
        return "REACCELERATION_CANDIDATE"
    return phase


def _tradeability(*, spread_bps: float, notional_24h: float) -> str:
    if spread_bps > 50.0 or notional_24h < 50_000.0:
        return "POOR"
    if spread_bps > 30.0 or notional_24h < 250_000.0:
        return "LIMITED"
    return "GOOD"


def _risk_label(*, state: str, spread_bps: float, atr_pct: float) -> str:
    if state in {"REVERSAL_RISK", "LATE_EXTENSION", "EXHAUSTION_RISK"} or spread_bps > 50.0 or atr_pct >= 8.0:
        return "HIGH"
    if state in {"CONFIRMED_EXPANSION", "EARLY_EXPANSION", "REACCELERATION_CANDIDATE"} or spread_bps > 30.0 or atr_pct >= 5.0:
        return "ELEVATED"
    return "NORMAL"


def _action(*, state: str, tradeability: str, flow_bias: str) -> str:
    if state == "REVERSAL_RISK":
        return "WAIT"
    if state in {"LATE_EXTENSION", "EXHAUSTION_RISK"}:
        return "WAIT_FOR_PULLBACK"
    if state == "CONFIRMED_EXPANSION":
        if tradeability == "GOOD" and flow_bias != "BEARISH":
            return "WATCH_BREAKOUT"
        return "WATCH_ONLY"
    if state == "REACCELERATION_CANDIDATE":
        return "WATCH_REACCELERATION"
    if state in {"EARLY_EXPANSION", "IGNITION"}:
        return "WATCH"
    return "NO_ACTION"


def build_market_insight(
    identity: MarketIdentity,
    ticker: dict[str, float],
    snapshot: MarketSnapshot,
    native_flow: NativeFlowShadowResult | None,
) -> MarketInsight:
    current = _positive_finite(ticker.get("last"))
    bid = _positive_finite(ticker.get("bid"))
    ask = _positive_finite(ticker.get("ask"))
    volume_24h = _positive_finite(ticker.get("volume_24h"))
    if current is None or bid is None or ask is None or volume_24h is None or ask < bid:
        raise MarketInsightUnavailable("Kraken ticker is incomplete or non-finite")

    spread_bps = (ask - bid) / ((ask + bid) / 2.0) * 10_000.0
    if not math.isfinite(spread_bps) or spread_bps < 0:
        raise MarketInsightUnavailable("Kraken spread evidence is invalid")
    notional_24h = current * volume_24h

    metrics = native_flow.metrics if native_flow is not None else None
    vector = build_explosion_state_vector(snapshot, native_flow=metrics)
    state = _state_overlay(
        snapshot,
        vector.phase,
        vector.momentum_acceleration,
        vector.relative_volume,
    )
    flow_bias = native_flow.evidence.bias if native_flow is not None and native_flow.evidence.available else "UNAVAILABLE"
    flow_strength = native_flow.evidence.strength if native_flow is not None and native_flow.evidence.available else 0
    tradeability = _tradeability(spread_bps=spread_bps, notional_24h=notional_24h)
    risk = _risk_label(state=state, spread_bps=spread_bps, atr_pct=_finite(snapshot.atr_pct))
    action = _action(state=state, tradeability=tradeability, flow_bias=flow_bias)

    if state == "REVERSAL_RISK":
        reason = (
            f"prior strength is reversing: 1h {vector.momentum_1h_pct:+.2f}% while "
            f"24h remains {vector.momentum_24h_pct:+.2f}%"
        )
    elif state == "REACCELERATION_CANDIDATE":
        reason = (
            f"momentum is reaccelerating near the 24h high with {vector.relative_volume:.2f}x relative volume"
        )
    elif vector.reasons:
        reason = "; ".join(vector.reasons[:2])
    else:
        reason = "no strong expansion transition is confirmed"

    warnings: list[str] = list(vector.warnings)
    if native_flow is not None:
        warnings.extend(native_flow.warnings)
    if flow_bias == "BEARISH":
        warnings.append("Kraken-native flow is bearish")
    if tradeability != "GOOD":
        warnings.append(f"tradeability is {tradeability.lower()} at current spread/liquidity")

    return MarketInsight(
        symbol=identity.base_asset,
        pair=identity.display_pair,
        current_price=current,
        state=state,
        phase_score=max(0, min(100, int(vector.phase_score))),
        risk=risk,
        action=action,
        tradeability=tradeability,
        momentum_1h_pct=_finite(snapshot.confirmed_price_change_1h_pct),
        momentum_6h_pct=_finite(snapshot.momentum_6h_pct),
        momentum_24h_pct=_finite(snapshot.momentum_24h_pct),
        relative_volume=max(0.0, _finite(snapshot.movement_volume_ratio or snapshot.volume_ratio)),
        spread_bps=spread_bps,
        distance_to_24h_high_pct=max(0.0, _finite(snapshot.distance_to_24h_high_pct)),
        historical_upside_median_pct=max(0.0, _finite(snapshot.rolling_24h_upside_median_pct)),
        historical_upside_p75_pct=max(0.0, _finite(snapshot.rolling_24h_upside_p75_pct)),
        historical_downside_median_pct=max(0.0, _finite(snapshot.rolling_24h_downside_median_pct)),
        historical_downside_p75_pct=max(0.0, _finite(snapshot.rolling_24h_downside_p75_pct)),
        flow_bias=flow_bias,
        flow_strength=max(0, min(10, int(flow_strength))),
        reason=reason,
        warnings=tuple(dict.fromkeys(warnings)),
        rsi=_finite(snapshot.rsi),
        atr_pct=max(0.0, _finite(snapshot.atr_pct)),
        trend=str(snapshot.trend).upper(),
    )


def analyze_market(query: str, client: KrakenClient | None = None) -> MarketInsight:
    client = client or KrakenClient(timeout_seconds=5.0)
    identity = resolve_market(query, client)
    try:
        ticker = client.get_ticker(identity.altname)
    except Exception as exc:
        raise MarketInsightUnavailable(f"Kraken ticker unavailable ({type(exc).__name__})") from exc

    status, snapshot, error = analyze_symbol(identity.altname)
    if status != "ok" or snapshot is None:
        message = error or f"market analysis returned {status}"
        raise MarketInsightUnavailable(message[:180])
    snapshot.ticker_last = _finite(ticker.get("last"), snapshot.ticker_last or snapshot.last_price)
    snapshot.ticker_bid = _finite(ticker.get("bid"), 0.0)
    snapshot.ticker_ask = _finite(ticker.get("ask"), 0.0)
    snapshot.underlying_asset = identity.base_asset
    snapshot.primary_pair = identity.display_pair
    snapshot.primary_quote_currency = identity.quote_asset
    snapshot.kraken_public_symbol = identity.ws_symbol

    try:
        native_flow = evaluate_native_flow_shadow(identity.ws_symbol, client=client)
    except Exception:
        native_flow = None
    return build_market_insight(identity, ticker, snapshot, native_flow)


def _fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if value >= 0.01:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.10f}".rstrip("0").rstrip(".")


def format_market_insight(insight: MarketInsight, *, verbose: bool = False, watch: bool = False) -> str:
    prefix = "👁 OHM WATCH" if watch else "🔎 OHM SCAN"
    flow_text = insight.flow_bias
    if insight.flow_bias != "UNAVAILABLE":
        flow_text += f" {insight.flow_strength}/10"
    lines = [
        f"{prefix} — {display_market_label(insight.pair, base_asset=insight.symbol, pair=insight.pair)}",
        f"State: {insight.state}",
        f"Price: {_fmt_price(insight.current_price)}",
        (
            f"Momentum: 1h {insight.momentum_1h_pct:+.2f}% | "
            f"6h {insight.momentum_6h_pct:+.2f}% | 24h {insight.momentum_24h_pct:+.2f}%"
        ),
        f"Volume: {insight.relative_volume:.2f}x | Spread: {insight.spread_bps:.1f} bps",
        f"Flow: {flow_text}",
        (
            "Potential (historical 24h): "
            f"+{insight.historical_upside_median_pct:.1f}% to +{insight.historical_upside_p75_pct:.1f}%"
        ),
        (
            "Downside ref (historical): "
            f"-{insight.historical_downside_median_pct:.1f}% to -{insight.historical_downside_p75_pct:.1f}%"
        ),
        f"Tradeability: {insight.tradeability}",
        f"Risk: {insight.risk}",
        f"Reason: {insight.reason[:220]}",
        f"Action: {insight.action}",
    ]
    if verbose:
        lines.extend(
            [
                "",
                f"Trend: {insight.trend} | RSI: {insight.rsi:.1f} | ATR: {insight.atr_pct:.2f}%",
                f"Distance from 24h high: {insight.distance_to_24h_high_pct:.2f}%",
                f"Warnings: {'; '.join(insight.warnings[:3]) if insight.warnings else 'none'}",
            ]
        )
    lines.append(
        f"*State score {insight.phase_score}/100 is heuristic, not probability. Historical ranges are not forecasts."
    )
    return "\n".join(lines)[:4000]