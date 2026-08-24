"""Deterministic technical-structure primitives for Signal Quality Phase 3B.

Pure, advisory-only analysis over explicitly supplied completed bars. The
module has no network, execution, PendingSetup, Telegram callback, or clock
side effects. Callers are responsible for passing only bars at-or-before the
decision timestamp; ``analyze_technical_structure`` enforces that cutoff again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


BIAS_BULLISH = "BULLISH"
BIAS_BEARISH = "BEARISH"
BIAS_MIXED = "MIXED"
BIAS_INSUFFICIENT = "INSUFFICIENT_DATA"

SWEEP_HIGH = "HIGH_SWEEP_RECLAIM"
SWEEP_LOW = "LOW_SWEEP_RECLAIM"

RETEST_HELD = "HELD"
RETEST_FAILED = "FAILED"
RETEST_NOT_SEEN = "NOT_SEEN"


@dataclass(frozen=True)
class StructureBar:
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class SwingPoint:
    index: int
    observed_at: datetime
    price: float
    kind: str  # HIGH / LOW


@dataclass(frozen=True)
class TechnicalStructureContext:
    symbol: str
    observed_at: datetime
    bias: str
    last_swing_high: float | None
    last_swing_low: float | None
    bullish_break_level: float | None
    bearish_break_level: float | None
    change_of_character: bool
    imbalance_zone_low: float | None
    imbalance_zone_high: float | None
    liquidity_sweep: str | None
    retest_state: str | None
    distance_from_breakout_pct: float | None
    reasons: tuple[str, ...]
    advisory_only: bool = True


def _valid_bar(bar: StructureBar) -> bool:
    return (
        bar.high >= bar.low
        and bar.high >= max(bar.open, bar.close)
        and bar.low <= min(bar.open, bar.close)
        and min(bar.open, bar.high, bar.low, bar.close) > 0
    )


def _completed_bars(bars: Sequence[StructureBar], decision_at: datetime) -> list[StructureBar]:
    return [bar for bar in bars if bar.observed_at <= decision_at and _valid_bar(bar)]


def confirmed_swings(
    bars: Sequence[StructureBar], *, left: int = 2, right: int = 2
) -> tuple[tuple[SwingPoint, ...], tuple[SwingPoint, ...]]:
    """Return confirmed swing highs/lows using completed neighbors only.

    A swing at ``i`` is confirmed only when ``right`` later bars are already
    present. Equal highs/lows do not count as confirmed pivots; at least one
    strict inequality is required on each side to avoid flat-plateau noise.
    """
    if left < 1 or right < 1:
        raise ValueError("left/right must be >= 1")
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    for i in range(left, len(bars) - right):
        window_left = bars[i - left : i]
        window_right = bars[i + 1 : i + 1 + right]
        current = bars[i]
        high_ge = all(current.high >= bar.high for bar in (*window_left, *window_right))
        high_strict_left = any(current.high > bar.high for bar in window_left)
        high_strict_right = any(current.high > bar.high for bar in window_right)
        if high_ge and high_strict_left and high_strict_right:
            highs.append(SwingPoint(i, current.observed_at, current.high, "HIGH"))

        low_le = all(current.low <= bar.low for bar in (*window_left, *window_right))
        low_strict_left = any(current.low < bar.low for bar in window_left)
        low_strict_right = any(current.low < bar.low for bar in window_right)
        if low_le and low_strict_left and low_strict_right:
            lows.append(SwingPoint(i, current.observed_at, current.low, "LOW"))
    return tuple(highs), tuple(lows)


def _last_breaks(
    bars: Sequence[StructureBar], highs: Sequence[SwingPoint], lows: Sequence[SwingPoint]
) -> tuple[float | None, float | None]:
    bullish: float | None = None
    bearish: float | None = None
    for swing in highs:
        if any(bar.close > swing.price for bar in bars[swing.index + 1 :]):
            bullish = swing.price
    for swing in lows:
        if any(bar.close < swing.price for bar in bars[swing.index + 1 :]):
            bearish = swing.price
    return bullish, bearish


def _structure_sequence_bias(
    highs: Sequence[SwingPoint], lows: Sequence[SwingPoint]
) -> tuple[str, bool]:
    if len(highs) < 2 or len(lows) < 2:
        return BIAS_INSUFFICIENT, False
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    bullish = h2.price > h1.price and l2.price > l1.price
    bearish = h2.price < h1.price and l2.price < l1.price
    if bullish:
        return BIAS_BULLISH, False
    if bearish:
        return BIAS_BEARISH, False
    return BIAS_MIXED, True


def latest_fvg_zone(bars: Sequence[StructureBar]) -> tuple[float | None, float | None]:
    """Return the most recent three-bar non-overlap imbalance/FVG-style zone.

    Bullish gap: bar[i].low > bar[i-2].high -> [older high, newer low].
    Bearish gap: bar[i].high < bar[i-2].low -> [newer high, older low].
    This is deliberately mechanical and does not claim subjective ICT validity.
    """
    zone: tuple[float | None, float | None] = (None, None)
    for i in range(2, len(bars)):
        older, newer = bars[i - 2], bars[i]
        if newer.low > older.high:
            zone = (older.high, newer.low)
        elif newer.high < older.low:
            zone = (newer.high, older.low)
    return zone


def latest_liquidity_sweep(
    bars: Sequence[StructureBar], highs: Sequence[SwingPoint], lows: Sequence[SwingPoint]
) -> str | None:
    """Detect the latest wick-through-and-reclaim of a confirmed swing level."""
    found: tuple[int, str] | None = None
    for swing in highs:
        for i, bar in enumerate(bars[swing.index + 1 :], start=swing.index + 1):
            if bar.high > swing.price and bar.close < swing.price:
                if found is None or i > found[0]:
                    found = (i, SWEEP_HIGH)
    for swing in lows:
        for i, bar in enumerate(bars[swing.index + 1 :], start=swing.index + 1):
            if bar.low < swing.price and bar.close > swing.price:
                if found is None or i > found[0]:
                    found = (i, SWEEP_LOW)
    return found[1] if found else None


def breakout_retest_state(
    bars: Sequence[StructureBar], breakout_level: float | None, *, bullish: bool
) -> str | None:
    """Classify the first retest after the first qualifying break of a level.

    Later continuation closes beyond the same level must not move the breakout
    timestamp forward; doing so can erase an already-observed retest. A failed
    retest is terminal for this breakout event, while a touch that closes back
    on the breakout side is reported as held.
    """
    if breakout_level is None or len(bars) < 2:
        return None
    break_idx: int | None = None
    for i, bar in enumerate(bars):
        broke = (bullish and bar.close > breakout_level) or (
            not bullish and bar.close < breakout_level
        )
        if broke:
            break_idx = i
            break
    if break_idx is None or break_idx == len(bars) - 1:
        return RETEST_NOT_SEEN
    post = bars[break_idx + 1 :]
    for bar in post:
        if bullish and bar.low <= breakout_level:
            return RETEST_FAILED if bar.close < breakout_level else RETEST_HELD
        if not bullish and bar.high >= breakout_level:
            return RETEST_FAILED if bar.close > breakout_level else RETEST_HELD
    return RETEST_NOT_SEEN


def analyze_technical_structure(
    symbol: str,
    bars: Sequence[StructureBar],
    *,
    decision_at: datetime,
    swing_left: int = 2,
    swing_right: int = 2,
) -> TechnicalStructureContext:
    completed = _completed_bars(bars, decision_at)
    if len(completed) < swing_left + swing_right + 3:
        return TechnicalStructureContext(
            symbol=symbol.upper(), observed_at=decision_at, bias=BIAS_INSUFFICIENT,
            last_swing_high=None, last_swing_low=None, bullish_break_level=None,
            bearish_break_level=None, change_of_character=False,
            imbalance_zone_low=None, imbalance_zone_high=None, liquidity_sweep=None,
            retest_state=None, distance_from_breakout_pct=None,
            reasons=("insufficient completed bars for confirmed structure",),
        )

    highs, lows = confirmed_swings(completed, left=swing_left, right=swing_right)
    bull_break, bear_break = _last_breaks(completed, highs, lows)
    seq_bias, choch = _structure_sequence_bias(highs, lows)
    zone_low, zone_high = latest_fvg_zone(completed)
    sweep = latest_liquidity_sweep(completed, highs, lows)

    bias = seq_bias
    if bull_break is not None and bear_break is None and bias == BIAS_INSUFFICIENT:
        bias = BIAS_BULLISH
    elif bear_break is not None and bull_break is None and bias == BIAS_INSUFFICIENT:
        bias = BIAS_BEARISH
    elif bull_break is not None and bear_break is not None and bias == BIAS_INSUFFICIENT:
        bias = BIAS_MIXED

    latest_close = completed[-1].close
    chosen_break = bull_break if bias == BIAS_BULLISH else bear_break if bias == BIAS_BEARISH else None
    distance = None
    if chosen_break and chosen_break > 0:
        distance = (latest_close / chosen_break - 1.0) * 100.0

    retest = None
    if bias == BIAS_BULLISH:
        retest = breakout_retest_state(completed, bull_break, bullish=True)
    elif bias == BIAS_BEARISH:
        retest = breakout_retest_state(completed, bear_break, bullish=False)

    reasons: list[str] = []
    if highs:
        reasons.append(f"confirmed swing high {highs[-1].price:.8g}")
    if lows:
        reasons.append(f"confirmed swing low {lows[-1].price:.8g}")
    if bull_break is not None:
        reasons.append(f"BOS bullish close above {bull_break:.8g}")
    if bear_break is not None:
        reasons.append(f"BOS bearish close below {bear_break:.8g}")
    if choch:
        reasons.append("mixed HH/HL vs LH/LL sequence (CHoCH-style transition)")
    if zone_low is not None and zone_high is not None:
        reasons.append(f"measurable imbalance zone {zone_low:.8g}-{zone_high:.8g}")
    if sweep:
        reasons.append(f"liquidity sweep: {sweep}")
    if retest:
        reasons.append(f"retest: {retest}")
    if not reasons:
        reasons.append("no confirmed structural event")

    return TechnicalStructureContext(
        symbol=symbol.upper(), observed_at=decision_at, bias=bias,
        last_swing_high=highs[-1].price if highs else None,
        last_swing_low=lows[-1].price if lows else None,
        bullish_break_level=bull_break, bearish_break_level=bear_break,
        change_of_character=choch, imbalance_zone_low=zone_low,
        imbalance_zone_high=zone_high, liquidity_sweep=sweep,
        retest_state=retest, distance_from_breakout_pct=distance,
        reasons=tuple(reasons),
    )
