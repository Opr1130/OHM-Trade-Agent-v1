"""Bounded live Kraken OHLC adapter for Phase 3B shadow structure telemetry.

This module is intentionally measurement-only. It consumes already-ranked
Signal Quality candidates, fetches a small bounded set of public Kraken spot
OHLC series, removes every candle that was not complete at the immutable
original decision timestamp, and passes only completed bars to the pure
``technical_structure`` service.

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

# Kraken's public REST endpoints accept legacy base aliases for a few major
# assets. Keep the mapping deliberately small and explicit rather than
# guessing prefixes for arbitrary assets.
_KRAKEN_PUBLIC_BASE_ALIASES = {
    "BTC": "XBT",
    "DOGE": "XDG",
}


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
    """Resolve OHM's canonical BASEQUOTE symbol to a Kraken public pair.

    OHM stores BTC/DOGE display identities, while Kraken still exposes XBT/XDG
    aliases on parts of its public API. USD and USDT quotes remain distinct.
    """
    upper = str(symbol or "").upper().replace("/", "")
    for quote in ("USDT", "USD"):
        if upper.endswith(quote) and len(upper) > len(quote):
            base = upper[: -len(quote)]
            public_base = _KRAKEN_PUBLIC_BASE_ALIASES.get(base, base)
            return f"{public_base}/{quote}"
    return None


def _decision_utc(decision_at: datetime) -> datetime:
    if decision_at.tzinfo is None:
        return decision_at.replace(tzinfo=timezone.utc)
    return decision_at.astimezone(timezone.utc)


def _completed_structure_bars(
    candles: Iterable[Any], *, decision_at: datetime
) -> list[StructureBar]:
    """Return deduplicated completed bars visible at ``decision_at`` only.

    Kraken candle timestamps are bucket *open* times. A 15m candle is eligible
    iff ``open_time + 15m <= original_decision_at``. This remains true even if
    the HTTP response is received minutes after the decision was produced.
    """
    decision = _decision_utc(decision_at)
    interval_seconds = INTERVAL_MINUTES * 60
    by_open_time: dict[int, StructureBar] = {}
    for candle in candles:
        try:
            opened = int(candle.timestamp)
            closed_at = datetime.fromtimestamp(
                opened + interval_seconds, tz=timezone.utc
            )
            if closed_at > decision:
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
        # Last row wins for duplicate Kraken buckets; output is sorted below.
        by_open_time[opened] = bar
    ordered = [by_open_time[key] for key in sorted(by_open_time)]
    return ordered[-LOOKBACK_COMPLETED_BARS:]


def _eligible_candidates(candidates: Iterable[Any]) -> list[Any]:
    """Use existing ranked order and keep public OHLC load bounded.

    Suppressed rows remain in Phase 3B chase telemetry, but live structure is
    fetched only for the first eight non-suppressed candidates. This is a
    deliberate measurement cohort and therefore has selection bias; downstream
    validation must stratify by rank rather than treat it as the full universe.
    """
    eligible = [
        row for row in candidates if not bool(getattr(row, "suppressed", False))
    ]
    return eligible[:MAX_STRUCTURE_CANDIDATES]


def collect_phase3b_live_structure(
    candidates: Iterable[Any],
    *,
    decision_at: datetime,
    client: KrakenClient | None = None,
) -> dict[str, Phase3BStructureSample]:
    """Collect completed 15m spot structure for a bounded candidate cohort.

    The function is fail-soft per symbol. A malformed pair, Kraken timeout,
    malformed candle, or insufficient history returns an unavailable or
    insufficient sample and never interrupts the caller's scan.
    """
    decision = _decision_utc(decision_at)
    client = client or KrakenClient()
    samples: dict[str, Phase3BStructureSample] = {}
    since = int(
        (
            decision
            - timedelta(
                minutes=INTERVAL_MINUTES * (LOOKBACK_COMPLETED_BARS + 4)
            )
        ).timestamp()
    )

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
            candles = client.get_ohlc(
                pair, interval=INTERVAL_MINUTES, since=since
            )
            bars = _completed_structure_bars(candles, decision_at=decision)
            context = analyze_technical_structure(
                symbol, bars, decision_at=decision
            )
            status = (
                STATUS_INSUFFICIENT
                if len(bars) < LOOKBACK_COMPLETED_BARS
                or context.bias == BIAS_INSUFFICIENT
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
