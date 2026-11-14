"""Deterministic signal outcome adapter (price-path measurement).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Given one signal (direction, entry, stop, target, signal time, horizon) and a
price path, :func:`measure_outcome` answers one question: which level was
reached first? The answer is one of

- ``TARGET_HIT``: the target was reached before the stop, inside the horizon;
- ``STOP_HIT``: the stop was reached before the target, inside the horizon;
- ``EXPIRED_NO_HIT``: the path covers the whole horizon and neither level was
  reached;
- ``INSUFFICIENT_EVIDENCE``: anything else. The reason is always stated.

What this module refuses to do, by design:

- It never fabricates a ``hit``. :func:`to_scorecard_record` returns ``None`` for
  ``INSUFFICIENT_EVIDENCE`` so such a signal cannot enter a hit rate (the
  Scorecard core also refuses a record with no boolean ``hit``).
- It never guesses ordering. A single observation that touches both the stop and
  the target (a candle whose range spans both) is ``AMBIGUOUS_ORDERING``.
  A stop or target on the wrong side of entry, a missing level, a missing
  timestamp, a gap in the path before the deciding observation, or a path that
  ends before the horizon is also ``INSUFFICIENT_EVIDENCE``.
- It never labels a measurement as realized. Every result carries
  ``evidence_class = HYPOTHETICAL_PRICE_PATH``: entry is assumed at the signal's
  entry price, no fill is modelled, and no order existed. Realized or paper
  returns come from fill records, not from this module.
- Hypothetical excursion (MFE/MAE, :func:`hypothetical_excursion`) is a separate
  function with a separate class and cannot produce a hit.

The module is pure: no file, clock, network or runtime service. It grants no
trading, exchange, ranking, alert or admission authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

TARGET_HIT = "TARGET_HIT"
STOP_HIT = "STOP_HIT"
EXPIRED_NO_HIT = "EXPIRED_NO_HIT"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

EVIDENCE_CLASS_PRICE_PATH = "HYPOTHETICAL_PRICE_PATH"
EVIDENCE_CLASS_EXCURSION = "HYPOTHETICAL_EXCURSION"

LONG = "LONG"
SHORT = "SHORT"

#: Insufficient-evidence reasons (stable vocabulary).
R_BAD_DIRECTION = "DIRECTION_MISSING_OR_INVALID"
R_BAD_LEVELS = "LEVEL_MISSING_OR_INVALID"
R_LEVEL_ORDER = "LEVEL_ORDER_INVALID"
R_BAD_SIGNAL_TIME = "SIGNAL_TIME_MISSING_OR_INVALID"
R_BAD_HORIZON = "HORIZON_MISSING_OR_INVALID"
R_NO_PATH = "NO_PRICE_OBSERVATIONS_IN_WINDOW"
R_BAD_OBSERVATION = "OBSERVATION_INVALID"
R_AMBIGUOUS = "AMBIGUOUS_ORDERING"
R_GAP = "PATH_GAP_BEFORE_DECISION"
R_SHORT_PATH = "PATH_ENDS_BEFORE_HORIZON"

#: A gap longer than this between consecutive observations (or between the
#: signal and the first observation) means the first-hit order is not known.
DEFAULT_MAX_GAP_SECONDS = 900.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _epoch(value: Any) -> float | None:
    """Epoch seconds from a number or a timezone-aware ISO-8601 string.

    A naive timestamp is refused: its zone is unknown, so its ordering against
    the signal time is unknown.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).timestamp()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc).timestamp()
    return None


@dataclass(frozen=True)
class OutcomeMeasurement:
    """Result of measuring one signal against one price path."""

    outcome: str
    reason: str | None
    direction: str | None
    entry: float | None
    stop: float | None
    target: float | None
    decided_at_epoch: float | None
    exit_price: float | None
    gross_return_pct: float | None
    net_return_pct: float | None
    observations_used: int
    evidence_class: str = EVIDENCE_CLASS_PRICE_PATH

    @property
    def determinate(self) -> bool:
        return self.outcome != INSUFFICIENT_EVIDENCE

    @property
    def hit(self) -> bool | None:
        """``True``/``False`` only for a determinate result, else ``None``."""
        if self.outcome == TARGET_HIT:
            return True
        if self.outcome in (STOP_HIT, EXPIRED_NO_HIT):
            return False
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "hit": self.hit,
            "direction": self.direction,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "decided_at_epoch": self.decided_at_epoch,
            "exit_price": self.exit_price,
            "gross_return_pct": self.gross_return_pct,
            "net_return_pct": self.net_return_pct,
            "observations_used": self.observations_used,
            "evidence_class": self.evidence_class,
        }


def _insufficient(
    reason: str,
    *,
    direction: str | None = None,
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    used: int = 0,
) -> OutcomeMeasurement:
    return OutcomeMeasurement(
        outcome=INSUFFICIENT_EVIDENCE,
        reason=reason,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        decided_at_epoch=None,
        exit_price=None,
        gross_return_pct=None,
        net_return_pct=None,
        observations_used=used,
    )


def _directional_return_pct(direction: str, entry: float, exit_price: float) -> float:
    raw = (exit_price - entry) / entry * 100.0
    return raw if direction == LONG else -raw


def _observation_range(raw: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """``(epoch, low, high)`` for a point price or a candle, else ``None``."""
    when = _epoch(raw.get("ts", raw.get("timestamp")))
    if when is None:
        return None
    price = _positive(raw.get("price"))
    low = _positive(raw.get("low"))
    high = _positive(raw.get("high"))
    if price is not None and low is None and high is None:
        return when, price, price
    if low is not None and high is not None and low <= high:
        return when, low, high
    return None


def measure_outcome(
    signal: Mapping[str, Any],
    observations: Iterable[Mapping[str, Any]],
    *,
    horizon_seconds: float | None = None,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> OutcomeMeasurement:
    """Measure which level a signal reached first on the given price path.

    ``signal`` carries ``direction`` (``LONG``/``SHORT``), ``entry``, ``stop``,
    ``target``, ``ts`` (signal time) and optionally ``horizon_seconds``.
    ``observations`` are mappings with ``ts`` and either ``price`` (a point) or
    ``low``/``high`` (a candle). Fees and slippage are basis points charged per
    side and affect only the net return, never the outcome class.
    """
    if isinstance(observations, (str, bytes, bytearray, Mapping)):
        raise TypeError("observations must be an iterable of mappings")

    direction = signal.get("direction")
    direction = direction.strip().upper() if isinstance(direction, str) else None
    if direction not in (LONG, SHORT):
        return _insufficient(R_BAD_DIRECTION)

    entry = _positive(signal.get("entry"))
    stop = _positive(signal.get("stop"))
    target = _positive(signal.get("target"))
    if entry is None or stop is None or target is None:
        return _insufficient(
            R_BAD_LEVELS, direction=direction, entry=entry, stop=stop, target=target
        )
    levels = dict(direction=direction, entry=entry, stop=stop, target=target)
    ordered = (stop < entry < target) if direction == LONG else (target < entry < stop)
    if not ordered:
        return _insufficient(R_LEVEL_ORDER, **levels)

    start = _epoch(signal.get("ts"))
    if start is None:
        return _insufficient(R_BAD_SIGNAL_TIME, **levels)
    horizon = _positive(
        horizon_seconds if horizon_seconds is not None else signal.get("horizon_seconds")
    )
    if horizon is None:
        return _insufficient(R_BAD_HORIZON, **levels)
    gap_limit = _positive(max_gap_seconds)
    if gap_limit is None:
        raise ValueError("max_gap_seconds must be a positive finite number")
    fee = _number(fee_bps)
    slip = _number(slippage_bps)
    if fee is None or slip is None or fee < 0 or slip < 0:
        raise ValueError("fee_bps and slippage_bps must be non-negative finite numbers")
    cost_pct = 2.0 * (fee + slip) / 100.0  # entry and exit, bps -> percent

    end = start + horizon
    window: list[tuple[float, float, float]] = []
    for raw in observations:
        if not isinstance(raw, Mapping):
            return _insufficient(R_BAD_OBSERVATION, **levels)
        parsed = _observation_range(raw)
        if parsed is None:
            return _insufficient(R_BAD_OBSERVATION, **levels)
        if parsed[0] > start and parsed[0] <= end:
            window.append(parsed)
    if not window:
        return _insufficient(R_NO_PATH, **levels)
    window.sort(key=lambda item: item[0])

    def hit_stop(low: float, high: float) -> bool:
        return low <= stop if direction == LONG else high >= stop

    def hit_target(low: float, high: float) -> bool:
        return high >= target if direction == LONG else low <= target

    previous = start
    for index, (when, low, high) in enumerate(window, start=1):
        stop_touched = hit_stop(low, high)
        target_touched = hit_target(low, high)
        if stop_touched or target_touched:
            if when - previous > gap_limit:
                return _insufficient(R_GAP, used=index, **levels)
            if stop_touched and target_touched:
                return _insufficient(R_AMBIGUOUS, used=index, **levels)
            exit_price = target if target_touched else stop
            gross = _directional_return_pct(direction, entry, exit_price)
            return OutcomeMeasurement(
                outcome=TARGET_HIT if target_touched else STOP_HIT,
                reason=None,
                direction=direction,
                entry=entry,
                stop=stop,
                target=target,
                decided_at_epoch=when,
                exit_price=exit_price,
                gross_return_pct=gross,
                net_return_pct=gross - cost_pct,
                observations_used=index,
            )
        if when - previous > gap_limit:
            return _insufficient(R_GAP, used=index, **levels)
        previous = when

    if end - previous > gap_limit:
        return _insufficient(R_SHORT_PATH, used=len(window), **levels)

    last_price = window[-1][1] if window[-1][1] == window[-1][2] else None
    if last_price is None:
        # A candle has no single closing price here; do not invent one.
        return OutcomeMeasurement(
            outcome=EXPIRED_NO_HIT,
            reason=None,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            decided_at_epoch=end,
            exit_price=None,
            gross_return_pct=None,
            net_return_pct=None,
            observations_used=len(window),
        )
    gross = _directional_return_pct(direction, entry, last_price)
    return OutcomeMeasurement(
        outcome=EXPIRED_NO_HIT,
        reason=None,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        decided_at_epoch=end,
        exit_price=last_price,
        gross_return_pct=gross,
        net_return_pct=gross - cost_pct,
        observations_used=len(window),
    )


def to_scorecard_record(
    signal: Mapping[str, Any], measurement: OutcomeMeasurement
) -> dict[str, Any] | None:
    """Scorecard input record, or ``None`` when the outcome is not determinate.

    The record carries a boolean ``hit`` only because the measurement is
    determinate, and keeps the evidence class so a reader can see it is a
    hypothetical price-path measurement and not a realized return.
    """
    hit = measurement.hit
    if hit is None:
        return None
    return {
        "signal_id": signal.get("signal_id"),
        "strategy_version": signal.get("strategy_version"),
        "hit": hit,
        "outcome": measurement.outcome,
        "evidence_class": measurement.evidence_class,
    }


def summarize(measurements: Iterable[OutcomeMeasurement]) -> dict[str, Any]:
    """Counts by outcome. ``INSUFFICIENT_EVIDENCE`` is reported, never dropped."""
    counts = {TARGET_HIT: 0, STOP_HIT: 0, EXPIRED_NO_HIT: 0, INSUFFICIENT_EVIDENCE: 0}
    reasons: dict[str, int] = {}
    for item in measurements:
        counts[item.outcome] += 1
        if item.reason:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
    determinate = counts[TARGET_HIT] + counts[STOP_HIT] + counts[EXPIRED_NO_HIT]
    total = determinate + counts[INSUFFICIENT_EVIDENCE]
    return {
        "total": total,
        "determinate": determinate,
        "counts": counts,
        "insufficient_reasons": dict(sorted(reasons.items())),
        "hit_rate": (counts[TARGET_HIT] / determinate) if determinate else None,
        "evidence_class": EVIDENCE_CLASS_PRICE_PATH,
    }


def hypothetical_excursion(
    signal: Mapping[str, Any],
    observations: Iterable[Mapping[str, Any]],
    *,
    horizon_seconds: float | None = None,
) -> dict[str, Any]:
    """Maximum favourable/adverse excursion over the horizon (hypothetical).

    Separate from :func:`measure_outcome` on purpose: an excursion says how far
    price travelled, not which level was reached first, so it can never produce
    a ``hit`` and never feeds the Scorecard.
    """
    direction = signal.get("direction")
    direction = direction.strip().upper() if isinstance(direction, str) else None
    entry = _positive(signal.get("entry"))
    start = _epoch(signal.get("ts"))
    horizon = _positive(
        horizon_seconds if horizon_seconds is not None else signal.get("horizon_seconds")
    )
    base = {"evidence_class": EVIDENCE_CLASS_EXCURSION, "hit": None}
    if direction not in (LONG, SHORT) or entry is None or start is None or horizon is None:
        return {**base, "status": INSUFFICIENT_EVIDENCE, "mfe_pct": None, "mae_pct": None}
    low_seen: float | None = None
    high_seen: float | None = None
    for raw in observations:
        parsed = _observation_range(raw) if isinstance(raw, Mapping) else None
        if parsed is None:
            return {**base, "status": INSUFFICIENT_EVIDENCE, "mfe_pct": None, "mae_pct": None}
        when, low, high = parsed
        if start < when <= start + horizon:
            low_seen = low if low_seen is None else min(low_seen, low)
            high_seen = high if high_seen is None else max(high_seen, high)
    if low_seen is None or high_seen is None:
        return {**base, "status": INSUFFICIENT_EVIDENCE, "mfe_pct": None, "mae_pct": None}
    up = (high_seen - entry) / entry * 100.0
    down = (entry - low_seen) / entry * 100.0
    mfe, mae = (up, down) if direction == LONG else (down, up)
    return {
        **base,
        "status": "MEASURED",
        "mfe_pct": max(0.0, mfe),
        "mae_pct": max(0.0, mae),
    }
