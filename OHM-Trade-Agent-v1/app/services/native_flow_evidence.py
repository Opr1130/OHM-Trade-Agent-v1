from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from typing import Any

from app.exchanges.kraken import KrakenClient, PreTradeBook, PublicTrade
from app.services.movement_discovery_v2 import FlowEvidence
from app.services.registry_io import registry_lock


VERSION = "native-flow-shadow-v1"
SHADOW_FILE = Path("/app/data/native_flow_shadow.jsonl")


@dataclass(frozen=True)
class NativeFlowMetrics:
    symbol: str
    midpoint: float
    buy_notional: float
    sell_notional: float
    neutral_notional: float
    buy_share: float
    aggressor_imbalance: float
    trade_count_acceleration: float | None
    large_print_concentration: float
    book_notional_imbalance: float
    recent_trade_count: int
    sample_trades: int


@dataclass(frozen=True)
class NativeFlowShadowResult:
    version: str
    symbol: str
    evidence: FlowEvidence
    metrics: NativeFlowMetrics | None
    warnings: tuple[str, ...]
    shadow_only: bool = True
    production_score_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = asdict(self.evidence)
        payload["metrics"] = asdict(self.metrics) if self.metrics is not None else None
        return payload


def _midpoint(book: PreTradeBook) -> float:
    if not book.bids or not book.asks:
        return 0.0
    best_bid = float(book.bids[0].price)
    best_ask = float(book.asks[0].price)
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return 0.0
    return (best_bid + best_ask) / 2.0


def _book_imbalance(book: PreTradeBook) -> float:
    bid = sum(float(level.price) * float(level.quantity) for level in book.bids)
    ask = sum(float(level.price) * float(level.quantity) for level in book.asks)
    total = bid + ask
    return 0.0 if total <= 0 else (bid - ask) / total


def _trade_metrics(trades: list[PublicTrade], midpoint: float) -> tuple[float, float, float, float, float]:
    if midpoint <= 0 or not trades:
        return 0.0, 0.0, 0.0, 0.5, 0.0
    rows = sorted(
        (max(0.0, float(trade.price) * float(trade.quantity)), float(trade.price))
        for trade in trades
    )
    total = sum(notional for notional, _ in rows)
    if total <= 0:
        return 0.0, 0.0, 0.0, 0.5, 0.0
    buy = sum(notional for notional, price in rows if price > midpoint)
    sell = sum(notional for notional, price in rows if price < midpoint)
    neutral = max(0.0, total - buy - sell)
    directional = buy + sell
    buy_share = 0.5 if directional <= 0 else buy / directional
    top_count = max(1, int(round(len(rows) * 0.10)))
    large_share = sum(notional for notional, _ in rows[-top_count:]) / total
    return buy, sell, neutral, buy_share, large_share


def _trade_count_acceleration(client: KrakenClient, symbol: str) -> tuple[float | None, int]:
    # PreTrade/PostTrade use ws-style BASE/QUOTE while OHLC is most portable
    # with the compact Kraken pair form already used throughout the scanner.
    candles = client.get_ohlc(symbol.replace("/", ""), interval=60)
    completed = candles[:-1] if len(candles) > 1 else []
    if len(completed) < 8:
        return None, 0
    recent = completed[-3:]
    baseline = completed[-15:-3]
    if not baseline:
        return None, sum(candle.trade_count for candle in recent)
    baseline_mean = mean(max(0, candle.trade_count) for candle in baseline)
    recent_mean = mean(max(0, candle.trade_count) for candle in recent)
    if baseline_mean <= 0:
        return None, sum(candle.trade_count for candle in recent)
    return recent_mean / baseline_mean, sum(candle.trade_count for candle in recent)


def evaluate_native_flow_shadow(
    symbol: str,
    *,
    client: KrakenClient | None = None,
    trade_count: int = 100,
) -> NativeFlowShadowResult:
    """Compute exchange-native flow evidence without modifying live scoring."""
    client = client or KrakenClient()
    warnings: list[str] = []
    try:
        book = client.get_pre_trade(symbol)
        trades = client.get_post_trade(symbol, count=trade_count)
        midpoint = _midpoint(book)
        if midpoint <= 0:
            raise ValueError("valid top-of-book midpoint unavailable")
        buy, sell, neutral, buy_share, large_share = _trade_metrics(trades, midpoint)
        book_imbalance = _book_imbalance(book)
        try:
            trade_accel, recent_trade_count = _trade_count_acceleration(client, symbol)
        except Exception:
            trade_accel, recent_trade_count = None, 0
            warnings.append("trade-count acceleration unavailable")
    except Exception as exc:
        return NativeFlowShadowResult(
            version=VERSION,
            symbol=symbol.upper(),
            evidence=FlowEvidence(
                available=False,
                bias="NEUTRAL",
                strength=0,
                source="KRAKEN_NATIVE_FLOW",
                freshness_seconds=None,
            ),
            metrics=None,
            warnings=(f"native flow unavailable: {type(exc).__name__}",),
        )

    aggressor_imbalance = (buy_share - 0.5) * 2.0
    signed = aggressor_imbalance * 5.0 + book_imbalance * 3.0
    if trade_accel is not None and trade_accel > 1.0:
        signed += (1.0 if aggressor_imbalance >= 0 else -1.0) * min(2.0, (trade_accel - 1.0) * 2.0)
    if large_share >= 0.60:
        warnings.append("large-print concentration is high; follow-through must be verified")
        signed *= 0.8
    signed = max(-10.0, min(10.0, signed))
    bias = "BULLISH" if signed >= 2.0 else ("BEARISH" if signed <= -2.0 else "NEUTRAL")
    strength = min(10, int(round(abs(signed))))

    metrics = NativeFlowMetrics(
        symbol=symbol.upper(),
        midpoint=round(midpoint, 12),
        buy_notional=round(buy, 2),
        sell_notional=round(sell, 2),
        neutral_notional=round(neutral, 2),
        buy_share=round(buy_share, 6),
        aggressor_imbalance=round(aggressor_imbalance, 6),
        trade_count_acceleration=round(trade_accel, 6) if trade_accel is not None else None,
        large_print_concentration=round(large_share, 6),
        book_notional_imbalance=round(book_imbalance, 6),
        recent_trade_count=recent_trade_count,
        sample_trades=len(trades),
    )
    return NativeFlowShadowResult(
        version=VERSION,
        symbol=symbol.upper(),
        evidence=FlowEvidence(
            available=True,
            bias=bias,
            strength=strength,
            source="KRAKEN_NATIVE_FLOW",
            freshness_seconds=0.0,
        ),
        metrics=metrics,
        warnings=tuple(warnings),
    )


def append_native_flow_shadow(
    rows: list[NativeFlowShadowResult],
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    if not rows:
        return 0
    now = now or datetime.now(timezone.utc)
    target = path or SHADOW_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"
    with registry_lock(lock):
        with target.open("a", encoding="utf-8") as handle:
            for row in rows:
                payload = row.as_dict()
                payload["record_type"] = "NATIVE_FLOW_SHADOW"
                payload["observed_at"] = now.isoformat()
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            handle.flush()
    return len(rows)
