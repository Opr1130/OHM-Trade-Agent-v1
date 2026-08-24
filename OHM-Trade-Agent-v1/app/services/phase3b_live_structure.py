"""Bounded live Kraken OHLC adapter for Phase 3B shadow structure telemetry.

This module is intentionally measurement-only. It runs inside the existing
Signal Quality scan cycle, fetches a small bounded set of public Kraken spot
OHLC series for already-ranked candidates, removes the still-forming candle,
and passes completed bars to the pure ``technical_structure`` service.

Nothing returned here is consumed by ranking, Telegram, PendingSetup, Kraken
execution, or any order lifecycle. Failures are represented as unavailable
samples and never escape into the live scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.exchanges.kraken import KrakenClient
from app.services.technical_structure import (
    BIAS_INSUFFICIENT,
    StructureBar,
    TechnicalStructureContext,
    analyze_technical_structure,
)

INTERVAL_MINUTES = 15
LOOKBACK_COMPLETED_BARS = 96
MAX_STRUCTURE_CANDIDATES = 8

STATUS_AVAILABLE = "AVAILABLE_COMPLETED_KRAKEN_SPOT_OHLC"
STATUS_INSUFFICIENT = "AVAILABLE_INSUFFICIENT_CONFIRMED_STRUCTURE"
STATUS_UNAVAILABLE_ERROR = "UNAVAILABLE_KRAKEN_OHLC_ERROR"
STATUS_UNAVAILABLE_SYMBOL = "UNAVAILABLE_SYMBOL_MAPPING"


@dataclass(frozen=True)
class Phase3BStructureSample:
    symbol: str
    status: str
    kraken_pair: str | None
    interval_minutes: int
    completed_bar_count: int
    latest_completed_at: datetime | None
    context: TechnicalStructureContext | None
    error_type: str | None = None
    measurement_only: bool = True
    advisory_only: bool = True
    affects_ranking: bool = False
    affects_telegram: bool = False
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False


def _canonical_kraken_pair(symbol: str) -> str | None:
    upper = str(symbol or "").upper().replace("/", "")
    for quote in ("USDT", "USD"):
        if upper.endswith(quote) and len(upper) > len(quote):
            base = upper[: -len(quote)]
            return f"{base}/{quote}"
    return None


def _completed_structure_bars(candles: Iterable[Any], *, decision_at: datetime) -> list[StructureBar]:
    decision = decision_at.astimezone(timezone.utc)
    interval_seconds = INTERVAL_MINUTES * 60
    by_open_time: dict[int, StructureBar] = {}
    for candle in candles:
        try:
            opened = int(candle.timestamp)
            closed_at = datetime.fromtimestamp(opened + interval_seconds, tz=timezone.utc)
            if closed_at > decision:
                # Kraken includes the current still-forming OHLC bucket. It is
                # never eligible for Phase 3B structure at this decision time.
                continue
            bar = StructureBar(
                observed_at=closed_at,
                open=float(candle.open),
                high=float(candle.high),
                low=float(candle.low),
                close=float(candle.close),
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        by_open_time[opened] = bar
    ordered = [by_open_time[key] for key in sorted(by_open_time)]
    return ordered[-LOOKBACK_COMPLETED_BARS:]


def _eligible_candidates(candidates: Iterable[Any]) -> list[Any]:
    """Use the existing ranked order and keep public OHLC load bounded.

    Suppressed rows remain in Phase 3B chase telemetry, but live structure is
    fetched only for currently non-suppressed candidates because structure is
    most useful around EARLY/BREAKOUT/ACTIONABLE decisions and an OHLC request
    per full-universe row would create unnecessary Kraken load.
    """
    eligible = [row for row in candidates if not bool(getattr(row, "suppressed", False))]
    return eligible[:MAX_STRUCTURE_CANDIDATES]


def collect_phase3b_live_structure(
    candidates: Iterable[Any],
    *,
    decision_at: datetime,
    client: KrakenClient | None = None,
) -> dict[str, Phase3BStructureSample]:
    """Collect completed 15m spot structure for a bounded candidate cohort.

    The function is fail-soft per symbol. A single malformed pair, Kraken
    timeout, or insufficient history returns an unavailable/insufficient sample
    and never interrupts the caller's scan.
    """
    decision = decision_at
    if decision.tzinfo is None:
        decision = decision.replace(tzinfo=timezone.utc)
    decision = decision.astimezone(timezone.utc)
    client = client or KrakenClient()
    samples: dict[str, Phase3BStructureSample] = {}
    since = int((decision - timedelta(minutes=INTERVAL_MINUTES * (LOOKBACK_COMPLETED_BARS + 4))).timestamp())

    for candidate in _eligible_candidates(candidates):
        symbol = str(getattr(candidate, "symbol", "") or "").upper()
        pair = _canonical_kraken_pair(symbol)
        if not pair:
            samples[symbol] = Phase3BStructureSample(
                symbol=symbol,
                status=STATUS_UNAVAILABLE_SYMBOL,
                kraken_pair=None,
                interval_minutes=INTERVAL_MINUTES,
                completed_bar_count=0,
                latest_completed_at=None,
                context=None,
            )
            continue

        try:
            candles = client.get_ohlc(pair, interval=INTERVAL_MINUTES, since=since)
            bars = _completed_structure_bars(candles, decision_at=decision)
            context = analyze_technical_structure(symbol, bars, decision_at=decision)
            status = (
                STATUS_INSUFFICIENT
                if context.bias == BIAS_INSUFFICIENT
                else STATUS_AVAILABLE
            )
            samples[symbol] = Phase3BStructureSample(
                symbol=symbol,
                status=status,
                kraken_pair=pair,
                interval_minutes=INTERVAL_MINUTES,
                completed_bar_count=len(bars),
                latest_completed_at=bars[-1].observed_at if bars else None,
                context=context,
            )
        except Exception as exc:
            samples[symbol] = Phase3BStructureSample(
                symbol=symbol,
                status=STATUS_UNAVAILABLE_ERROR,
                kraken_pair=pair,
                interval_minutes=INTERVAL_MINUTES,
                completed_bar_count=0,
                latest_completed_at=None,
                context=None,
                error_type=type(exc).__name__,
            )

    return samples
