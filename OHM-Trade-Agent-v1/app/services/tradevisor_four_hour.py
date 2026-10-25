"""Completed-4H market-context evidence (G04).

PURE EVIDENCE CLASSIFIER — NOT WIRED, NOT TRUSTED, NO AUTHORITY.

Tradevisor SWING qualification requires independently trusted *completed* 4H
context (owner policy G-04). No such source exists in this deployment, so the
advisory evaluator records `regime_4h = UNAVAILABLE` and every SWING proposal
stays WATCH. This module supplies the missing piece that can be *tested* without
pretending the evidence is trusted: a deterministic classifier that answers one
question for a single candidate 4H bar -

    may this bar be used as completed 4H context at this evaluation instant?

It reads no clock, opens no connection, imports no exchange client and holds no
provider. A caller must still (a) obtain the bar from an approved provider and
(b) satisfy a separate owner-approved trust decision before any consumer treats
`AVAILABLE` as authoritative. Nothing here grants that trust.

Deliberate refusals:

* a bar that is not exactly 240 minutes is refused, never rescaled;
* a bar whose close is not in the past is refused (still forming);
* a bar whose close is later than the evaluation instant is refused (future data);
* a bar older than the freshness bound is refused;
* a bar whose own close time contradicts its open time is refused;
* an instrument, market or venue mismatch is refused;
* non-finite or incoherent OHLC is refused;
* provenance that does not name a provider is refused;
* a 1H bar, a source alert timeframe, or a synthetic/imputed bar can never stand
  in for 4H evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

#: The one interval this contract accepts. Nothing is rescaleable.
FOUR_HOUR_INTERVAL = timedelta(minutes=240)

#: Statuses. ``AVAILABLE`` means "structurally usable", never "trusted".
STATUS_AVAILABLE = "AVAILABLE"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_INVALID = "INVALID"

#: Refusal reasons (stable, machine-readable).
REASON_NOT_OBJECT = "NOT_OBJECT"
REASON_INTERVAL_MISMATCH = "INTERVAL_MISMATCH"
REASON_INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
REASON_MARKET_MISMATCH = "MARKET_MISMATCH"
REASON_EXCHANGE_MISMATCH = "EXCHANGE_MISMATCH"
REASON_NOT_UTC = "NOT_UTC"
REASON_MISALIGNED = "MISALIGNED_INTERVAL"
REASON_STILL_FORMING = "STILL_FORMING"
REASON_FUTURE = "FUTURE_BAR"
REASON_STALE = "STALE_BAR"
REASON_CLOSE_CONTRADICTION = "CLOSE_TIME_CONTRADICTION"
REASON_INVALID_OHLC = "INVALID_OHLC"
REASON_PROVENANCE_MISSING = "PROVENANCE_MISSING"
REASON_SYNTHETIC = "SYNTHETIC_OR_IMPUTED"
REASON_ALERT_TIMEFRAME = "ALERT_TIMEFRAME_IS_NOT_EVIDENCE"

#: Provenance markers that can never be promoted to genuine 4H evidence.
_SYNTHETIC_PROVENANCE = frozenset(
    {"SYNTHETIC", "IMPUTED", "INFERRED", "DERIVED_FROM_1H", "TEST_FIXTURE"}
)


@dataclass(frozen=True)
class FourHourContext:
    """The classification result. Immutable evidence, never an instruction."""

    status: str
    reason: str | None
    market: str | None
    instrument: str | None
    source_exchange: str | None
    interval: timedelta | None
    open_at: datetime | None
    close_at: datetime | None
    observed_at: datetime | None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    prices: Mapping[str, Any] = field(default_factory=dict)
    trusted: bool = False

    @property
    def available(self) -> bool:
        return self.status == STATUS_AVAILABLE

    def as_evidence(self) -> dict[str, Any]:
        return {
            "schema_version": "tradevisor-4h-context-v1",
            "status": self.status,
            "reason": self.reason,
            "market": self.market,
            "instrument": self.instrument,
            "source_exchange": self.source_exchange,
            "interval_minutes": (
                int(self.interval.total_seconds() // 60) if self.interval else None
            ),
            "open_at": self.open_at.isoformat() if self.open_at else None,
            "close_at": self.close_at.isoformat() if self.close_at else None,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "provenance": dict(self.provenance),
            "prices": dict(self.prices),
            # Structural usability is never production trust.
            "trusted": False,
            "trade_authority_changed": False,
        }


def _unavailable(reason: str, **over: Any) -> FourHourContext:
    values: dict[str, Any] = dict(
        status=STATUS_UNAVAILABLE,
        reason=reason,
        market=None,
        instrument=None,
        source_exchange=None,
        interval=None,
        open_at=None,
        close_at=None,
        observed_at=None,
    )
    values.update(over)
    return FourHourContext(**values)


def _invalid(reason: str, **over: Any) -> FourHourContext:
    context = _unavailable(reason, **over)
    return FourHourContext(**{**context.__dict__, "status": STATUS_INVALID})


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def classify_completed_four_hour(
    candle: Any,
    *,
    market: str,
    instrument: str,
    source_exchange: str,
    evaluation_time: datetime,
    max_age_seconds: int = 4 * 3600,
    close_tolerance_seconds: int = 60,
    now: datetime | None = None,
) -> FourHourContext:
    """Classify one candidate 4H bar as usable completed context or refuse it.

    ``now`` defaults to ``evaluation_time`` so the function stays pure: the caller
    owns the clock. A refusal is always a *recorded* disposition, never an
    exception and never an empty success.
    """
    if not isinstance(candle, Mapping):
        return _unavailable(REASON_NOT_OBJECT)

    observed_at = _as_utc(candle.get("observed_at"))
    provenance = candle.get("provenance")
    if not isinstance(provenance, Mapping) or not str(
        provenance.get("provider") or ""
    ).strip():
        return _unavailable(REASON_PROVENANCE_MISSING, observed_at=observed_at)
    declared = str(provenance.get("kind") or "").strip().upper()
    if declared in _SYNTHETIC_PROVENANCE:
        return _invalid(
            REASON_SYNTHETIC, observed_at=observed_at, provenance=dict(provenance)
        )
    if provenance.get("from_alert_timeframe") not in (None, False):
        # A TradingView alert that *claims* 240 minutes is not market data.
        return _invalid(REASON_ALERT_TIMEFRAME, observed_at=observed_at)

    interval = candle.get("interval")
    if interval != FOUR_HOUR_INTERVAL:
        return _unavailable(REASON_INTERVAL_MISMATCH, observed_at=observed_at)

    if str(candle.get("market") or "") != str(market):
        return _unavailable(REASON_MARKET_MISMATCH, observed_at=observed_at)
    if str(candle.get("instrument") or "") != str(instrument):
        return _unavailable(REASON_INSTRUMENT_MISMATCH, observed_at=observed_at)
    if (
        str(candle.get("source_exchange") or "").strip().upper()
        != str(source_exchange).strip().upper()
    ):
        return _unavailable(REASON_EXCHANGE_MISMATCH, observed_at=observed_at)

    opened = _as_utc(candle.get("open_at"))
    reported_close = _as_utc(candle.get("close_at"))
    evaluation = _as_utc(evaluation_time)
    clock = _as_utc(now) if now is not None else evaluation
    if opened is None or reported_close is None or evaluation is None or clock is None:
        return _unavailable(REASON_NOT_UTC, observed_at=observed_at)

    expected_close = opened + FOUR_HOUR_INTERVAL
    drift = abs((reported_close - expected_close).total_seconds())
    if drift > close_tolerance_seconds:
        return _invalid(
            REASON_CLOSE_CONTRADICTION,
            open_at=opened,
            close_at=reported_close,
            observed_at=observed_at,
        )
    if int(opened.timestamp()) % int(FOUR_HOUR_INTERVAL.total_seconds()) != 0:
        return _invalid(
            REASON_MISALIGNED, open_at=opened, close_at=reported_close,
            observed_at=observed_at,
        )
    if reported_close > clock:
        return _unavailable(
            REASON_STILL_FORMING, open_at=opened, close_at=reported_close,
            observed_at=observed_at,
        )
    if reported_close > evaluation:
        return _unavailable(
            REASON_FUTURE, open_at=opened, close_at=reported_close,
            observed_at=observed_at,
        )
    age = (evaluation - reported_close).total_seconds()
    if age > max_age_seconds:
        return _unavailable(
            REASON_STALE, open_at=opened, close_at=reported_close,
            observed_at=observed_at,
        )

    prices = {}
    for name in ("open", "high", "low", "close"):
        value = _finite(candle.get(name))
        if value is None or value <= 0.0:
            return _invalid(
                REASON_INVALID_OHLC, open_at=opened, close_at=reported_close,
                observed_at=observed_at,
            )
        prices[name] = value
    if (
        prices["high"] < prices["low"]
        or prices["high"] < prices["open"]
        or prices["high"] < prices["close"]
        or prices["low"] > prices["open"]
        or prices["low"] > prices["close"]
    ):
        return _invalid(
            REASON_INVALID_OHLC, open_at=opened, close_at=reported_close,
            observed_at=observed_at,
        )

    return FourHourContext(
        status=STATUS_AVAILABLE,
        reason=None,
        market=str(market),
        instrument=str(instrument),
        source_exchange=str(source_exchange).strip().upper(),
        interval=FOUR_HOUR_INTERVAL,
        open_at=opened,
        close_at=reported_close,
        observed_at=observed_at,
        provenance=dict(provenance),
        prices=dict(prices),
        # Structural usability only; a separate owner decision must grant trust.
        trusted=False,
    )


def reconcile_duplicates(contexts: list[FourHourContext]) -> FourHourContext:
    """Collapse restatements of the same bar, or refuse the whole set.

    Two records for the same open time are interchangeable only if their close
    time and full OHLC are identical; anything else is a conflicting restatement.
    """
    usable = [item for item in contexts if item.available]
    if not usable:
        return _invalid("NO_AVAILABLE_CANDLE")
    if len(usable) == 1:
        return usable[0]
    first = usable[0]
    for other in usable[1:]:
        if (
            other.open_at != first.open_at
            or other.close_at != first.close_at
            or dict(other.prices) != dict(first.prices)
            or dict(other.provenance) != dict(first.provenance)
        ):
            return _invalid("CONFLICTING_DUPLICATE")
    return first


__all__ = [
    "FOUR_HOUR_INTERVAL",
    "FourHourContext",
    "FourHourWindow",
    "STATUS_AVAILABLE",
    "STATUS_INVALID",
    "STATUS_UNAVAILABLE",
    "classify_completed_four_hour",
    "classify_window",
    "classify_window_from_provider",
    "reconcile_duplicates",
]



#: Reason codes added by window-level coverage.
REASON_MISSING_BARS = "MISSING_BARS"
REASON_NO_EXPECTED_BARS = "NO_EXPECTED_BARS"
REASON_CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
REASON_PROVIDER_ERROR = "PROVIDER_ERROR"
REASON_WINDOW_INVALID = "WINDOW_INVALID"


@dataclass(frozen=True)
class FourHourWindow:
    """A whole completed-4H evidence window, or an explicit refusal."""

    status: str
    reason: str | None
    expected_bars: int
    observed_bars: int
    missing_open_times: tuple[str, ...] = ()
    bars: tuple[FourHourContext, ...] = ()
    refusal: str | None = None

    @property
    def available(self) -> bool:
        return self.status == STATUS_AVAILABLE

    def as_evidence(self) -> dict[str, Any]:
        return {
            "schema_version": "tradevisor-4h-window-v1",
            "status": self.status,
            "reason": self.reason,
            "refusal": self.refusal,
            "expected_bars": self.expected_bars,
            "observed_bars": self.observed_bars,
            "missing_open_times": list(self.missing_open_times),
            "bars": [bar.as_evidence() for bar in self.bars],
            # A complete window is structural usability, never trust.
            "trusted": False,
            "trade_authority_changed": False,
        }


def _expected_opens(window_start: datetime, window_end: datetime) -> list[datetime]:
    step = int(FOUR_HOUR_INTERVAL.total_seconds())
    start = _as_utc(window_start)
    end = _as_utc(window_end)
    if start is None or end is None or end <= start:
        return []
    first = (int(start.timestamp()) // step + 1) * step
    opens: list[datetime] = []
    while first + step <= int(end.timestamp()):
        opens.append(datetime.fromtimestamp(first, tz=timezone.utc))
        first += step
    return opens


def classify_window(
    candles: Any,
    *,
    market: str,
    instrument: str,
    source_exchange: str,
    window_start: datetime,
    window_end: datetime,
    evaluation_time: datetime,
    max_age_seconds: int = 48 * 3600,
    close_tolerance_seconds: int = 60,
    now: datetime | None = None,
) -> FourHourWindow:
    """Prove a complete completed-4H window, or refuse it.

    A single valid candle does not prove a window: every expected bar on the
    4-hour grid must be present exactly once, coherent, from the same venue and
    instrument, and closed before the evaluation instant. Nothing is rescaled,
    aggregated from 1H data, or inferred.
    """
    if candles is None:
        return FourHourWindow(STATUS_UNAVAILABLE, REASON_PROVIDER_ERROR, 0, 0)
    expected = _expected_opens(window_start, window_end)
    if not expected:
        return FourHourWindow(STATUS_UNAVAILABLE, REASON_NO_EXPECTED_BARS, 0, 0)
    try:
        items = list(candles)
    except TypeError:
        return FourHourWindow(STATUS_UNAVAILABLE, REASON_PROVIDER_ERROR, len(expected), 0)

    grouped: dict[datetime, list[FourHourContext]] = {}
    refusal: str | None = None
    for candle in items:
        context = classify_completed_four_hour(
            candle,
            market=market,
            instrument=instrument,
            source_exchange=source_exchange,
            evaluation_time=evaluation_time,
            max_age_seconds=max_age_seconds,
            close_tolerance_seconds=close_tolerance_seconds,
            now=now,
        )
        if context.open_at is None:
            if refusal is None:
                refusal = context.reason or REASON_WINDOW_INVALID
            continue
        grouped.setdefault(context.open_at, []).append(context)

    bars: list[FourHourContext] = []
    conflicting = False
    for opened, records in grouped.items():
        usable = [record for record in records if record.available]
        if not usable:
            if refusal is None:
                refusal = records[0].reason or REASON_WINDOW_INVALID
            continue
        if len({(record.close_at, tuple(sorted(dict(record.prices).items())))
                for record in usable}) > 1:
            conflicting = True
            continue
        bars.append(usable[0])

    if conflicting:
        return FourHourWindow(
            STATUS_INVALID, REASON_CONFLICTING_DUPLICATE, len(expected), len(bars)
        )
    observed_opens = {bar.open_at for bar in bars}
    missing = tuple(
        opened.isoformat() for opened in expected if opened not in observed_opens
    )
    if missing:
        return FourHourWindow(
            STATUS_UNAVAILABLE,
            refusal or REASON_MISSING_BARS,
            len(expected),
            len(bars),
            missing_open_times=missing,
        )
    if refusal is not None:
        return FourHourWindow(
            STATUS_UNAVAILABLE, refusal, len(expected), len(bars), refusal=refusal
        )
    return FourHourWindow(
        STATUS_AVAILABLE, None, len(expected), len(bars), bars=tuple(bars)
    )


def classify_window_from_provider(
    provider: Any, **kwargs: Any
) -> FourHourWindow:
    """Call a provider and classify its window, converting faults to a refusal.

    A provider that raises is an unavailable window - never an exception that a
    caller could mistake for a success - and a provider that returns nothing is a
    provider error.
    """
    if provider is None:
        return FourHourWindow(STATUS_UNAVAILABLE, REASON_PROVIDER_ERROR, 0, 0)
    try:
        candles = provider()
    except Exception:  # noqa: BLE001 - an unreadable window is an unavailable one
        return FourHourWindow(STATUS_UNAVAILABLE, REASON_PROVIDER_ERROR, 0, 0)
    return classify_window(candles, **kwargs)

