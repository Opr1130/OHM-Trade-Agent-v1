"""Narrow, shared OHLC geometry predicate.

One owner for one question: *may these four prices describe a real bar?* It is
deliberately narrower than the native scanner validator, which additionally
owns volume, VWAP, timestamp contiguity, gap and admission policy. Keeping the
predicate here lets the measurement path and the native owner agree without
coupling learning to scanner admission logic.

Pure: no clock, no I/O, no mutable state, no imports from either consumer.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

#: Failure classes. ``None`` means the bar is coherent.
PROBLEM_MISSING_FIELD = "MISSING_FIELD"
PROBLEM_NON_FINITE = "NON_FINITE"
PROBLEM_NON_POSITIVE = "NON_POSITIVE"
PROBLEM_IMPOSSIBLE_RANGE = "IMPOSSIBLE_RANGE"

#: The four prices a measured bar must carry.
OHLC_FIELDS = ("open", "high", "low", "close")

#: Problems that describe untrustworthy *prices* rather than absent data.
PRICE_PROBLEMS = frozenset(
    {PROBLEM_NON_FINITE, PROBLEM_NON_POSITIVE, PROBLEM_IMPOSSIBLE_RANGE}
)


def finite_number(value: Any) -> float | None:
    """A finite float, or ``None``. Booleans are never prices."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validated_ohlc(candle: Any) -> tuple[dict[str, float] | None, str | None]:
    """Return ``(prices, problem)`` for one candle.

    ``prices`` is ``None`` whenever ``problem`` is set. A field that is absent,
    ``None``, boolean or non-numeric is ``MISSING_FIELD`` (absent data); a NaN or
    infinity is ``NON_FINITE``; a value ``<= 0`` is ``NON_POSITIVE``; and a bar
    that violates ``low <= open, close <= high`` is ``IMPOSSIBLE_RANGE``.
    """
    if isinstance(candle, Mapping):
        get = candle.get
    else:
        get = lambda name: getattr(candle, name, None)  # noqa: E731

    prices: dict[str, float] = {}
    for name in OHLC_FIELDS:
        raw = get(name)
        if raw is None or isinstance(raw, bool):
            return None, PROBLEM_MISSING_FIELD
        if isinstance(raw, str) and not raw.strip():
            return None, PROBLEM_MISSING_FIELD
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None, PROBLEM_MISSING_FIELD
        if not math.isfinite(number):
            return None, PROBLEM_NON_FINITE
        if number <= 0.0:
            return None, PROBLEM_NON_POSITIVE
        prices[name] = number

    if (
        prices["high"] < prices["low"]
        or prices["high"] < prices["open"]
        or prices["high"] < prices["close"]
        or prices["low"] > prices["open"]
        or prices["low"] > prices["close"]
    ):
        return None, PROBLEM_IMPOSSIBLE_RANGE
    return prices, None


__all__ = [
    "OHLC_FIELDS",
    "PRICE_PROBLEMS",
    "PROBLEM_IMPOSSIBLE_RANGE",
    "PROBLEM_MISSING_FIELD",
    "PROBLEM_NON_FINITE",
    "PROBLEM_NON_POSITIVE",
    "finite_number",
    "validated_ohlc",
]
