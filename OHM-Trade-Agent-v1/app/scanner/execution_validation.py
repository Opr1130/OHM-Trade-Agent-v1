from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from app.exchanges.kraken import BookLevel, PreTradeBook, PublicTrade


VALID = "VALID"
UNAVAILABLE = "UNAVAILABLE"
INVALID = "INVALID"
WARN = "WARN"
FRESH = "FRESH"
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
INSUFFICIENT = "INSUFFICIENT"
DEPTH_BANDS_PCT = (0.25, 0.50)
RECENT_TRADE_WARN_AGE_SECONDS = 300
RECENT_TRADE_PRICE_WARN_PCT = 2.0


@dataclass(frozen=True)
class FillSimulation:
    filled_quantity: float
    filled_notional: float
    vwap: float | None
    market_impact_pct: float | None
    cost_vs_mid_pct: float | None
    visible_coverage_pct: float
    fully_covered: bool


@dataclass(frozen=True)
class ExecutionValidation:
    status: str
    book_coverage_status: str
    warnings: list[str]
    best_bid: float | None = None
    best_ask: float | None = None
    mid_price: float | None = None
    absolute_spread: float | None = None
    spread_pct: float | None = None
    spread_bps: float | None = None
    visible_bid_notional: float | None = None
    visible_ask_notional: float | None = None
    bid_depth_025_usd: float | None = None
    ask_depth_025_usd: float | None = None
    bid_depth_025_complete: bool = False
    ask_depth_025_complete: bool = False
    bid_depth_050_usd: float | None = None
    ask_depth_050_usd: float | None = None
    bid_depth_050_complete: bool = False
    ask_depth_050_complete: bool = False
    validation_notional_usd: float = 0.0
    buy_vwap: float | None = None
    sell_vwap: float | None = None
    buy_market_impact_pct: float | None = None
    sell_market_impact_pct: float | None = None
    buy_visible_coverage_pct: float | None = None
    sell_visible_coverage_pct: float | None = None
    buy_fully_covered: bool = False
    sell_fully_covered: bool = False
    estimated_visible_round_trip_market_drag_pct: float | None = None
    estimated_visible_short_round_trip_market_drag_pct: float | None = None
    recent_trade_status: str = UNAVAILABLE
    latest_trade_price: float | None = None
    latest_trade_age_seconds: float | None = None
    recent_trade_count: int = 0


def unavailable_execution(reason: str) -> ExecutionValidation:
    return ExecutionValidation(
        status=UNAVAILABLE,
        book_coverage_status=UNAVAILABLE,
        warnings=[reason],
    )


def _validate_levels(levels: list[BookLevel]) -> bool:
    return bool(levels) and all(
        math.isfinite(level.price) and level.price > 0
        and math.isfinite(level.quantity) and level.quantity > 0
        for level in levels
    )


def _depth(
    levels: list[BookLevel],
    mid: float,
    band_pct: float,
    side: str,
) -> tuple[float, bool]:
    if side == "ask":
        boundary = mid * (1 + band_pct / 100)
        notional = sum(level.price * level.quantity for level in levels if level.price <= boundary)
        complete = len(levels) < 10 or levels[-1].price >= boundary
    else:
        boundary = mid * (1 - band_pct / 100)
        notional = sum(level.price * level.quantity for level in levels if level.price >= boundary)
        complete = len(levels) < 10 or levels[-1].price <= boundary
    return notional, complete


def simulate_buy(
    asks: list[BookLevel],
    notional: float,
    mid: float,
) -> FillSimulation:
    remaining = notional
    quantity = 0.0
    spent = 0.0
    for level in asks:
        consumed = min(remaining, level.price * level.quantity)
        quantity += consumed / level.price
        spent += consumed
        remaining -= consumed
        if remaining <= 1e-9:
            break
    covered = remaining <= 1e-9
    vwap = spent / quantity if quantity > 0 else None
    impact = ((vwap / asks[0].price) - 1) * 100 if covered and vwap else None
    cost_mid = ((vwap / mid) - 1) * 100 if covered and vwap else None
    return FillSimulation(
        quantity, spent, vwap if covered else None, impact, cost_mid,
        min(100.0, spent / notional * 100) if notional > 0 else 0.0, covered,
    )


def simulate_buy_quantity(
    asks: list[BookLevel],
    quantity: float,
    mid: float,
) -> FillSimulation:
    """Simulate buying an exact base quantity, used to cover a short."""
    remaining = quantity
    bought = 0.0
    spent = 0.0
    for level in asks:
        consumed = min(remaining, level.quantity)
        bought += consumed
        spent += consumed * level.price
        remaining -= consumed
        if remaining <= 1e-12:
            break
    covered = remaining <= 1e-12
    vwap = spent / bought if bought > 0 else None
    impact = ((vwap / asks[0].price) - 1) * 100 if covered and vwap else None
    cost_mid = ((vwap / mid) - 1) * 100 if covered and vwap else None
    return FillSimulation(
        bought, spent, vwap if covered else None, impact, cost_mid,
        min(100.0, bought / quantity * 100) if quantity > 0 else 0.0, covered,
    )


def simulate_sell(
    bids: list[BookLevel],
    quantity: float,
    mid: float,
) -> FillSimulation:
    remaining = quantity
    sold = 0.0
    proceeds = 0.0
    for level in bids:
        consumed = min(remaining, level.quantity)
        sold += consumed
        proceeds += consumed * level.price
        remaining -= consumed
        if remaining <= 1e-12:
            break
    covered = remaining <= 1e-12
    vwap = proceeds / sold if sold > 0 else None
    impact = ((bids[0].price - vwap) / bids[0].price * 100) if covered and vwap else None
    cost_mid = ((mid - vwap) / mid * 100) if covered and vwap else None
    return FillSimulation(
        sold, proceeds, vwap if covered else None, impact, cost_mid,
        min(100.0, sold / quantity * 100) if quantity > 0 else 0.0, covered,
    )


def _trade_context(
    trades: list[PublicTrade] | None,
    ticker_last: float,
    mid: float,
    now: datetime,
) -> tuple[str, float | None, float | None, list[str]]:
    if trades is None:
        return UNAVAILABLE, None, None, ["recent PostTrade validation unavailable"]
    if not trades:
        return WARN, None, None, ["PostTrade returned no recent trades"]
    latest = max(trades, key=lambda trade: trade.trade_timestamp)
    if not math.isfinite(float(latest.price)) or latest.price <= 0:
        return WARN, None, None, ["latest public trade price is invalid"]
    try:
        trade_time = datetime.fromisoformat(latest.trade_timestamp.replace("Z", "+00:00"))
        age = max(0.0, (now - trade_time).total_seconds())
    except ValueError:
        return WARN, latest.price, None, ["latest trade timestamp is invalid"]
    warnings: list[str] = []
    if age > RECENT_TRADE_WARN_AGE_SECONDS:
        warnings.append("latest public trade is stale")
    reference_difference = max(
        abs(latest.price - ticker_last) / ticker_last * 100,
        abs(latest.price - mid) / mid * 100,
    )
    if reference_difference > RECENT_TRADE_PRICE_WARN_PCT:
        warnings.append("latest public trade price disagrees with ticker/book")
    return WARN if warnings else FRESH, latest.price, age, warnings


def evaluate_execution(
    book: PreTradeBook,
    validation_notional_usd: float,
    ticker_last: float,
    quote_to_usd_rate: float = 1.0,
    trades: list[PublicTrade] | None = None,
    now: datetime | None = None,
) -> ExecutionValidation:
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []

    numeric_inputs = (validation_notional_usd, ticker_last, quote_to_usd_rate)
    if not all(math.isfinite(float(value)) for value in numeric_inputs):
        return ExecutionValidation(
            status=INVALID,
            book_coverage_status=UNAVAILABLE,
            warnings=["execution validation inputs must be finite"],
            validation_notional_usd=validation_notional_usd if math.isfinite(float(validation_notional_usd)) else 0.0,
        )
    if validation_notional_usd <= 0 or ticker_last <= 0 or quote_to_usd_rate <= 0:
        return ExecutionValidation(
            status=INVALID,
            book_coverage_status=UNAVAILABLE,
            warnings=["execution validation notional, ticker, and quote conversion must be positive"],
            validation_notional_usd=max(0.0, validation_notional_usd),
        )

    if not _validate_levels(book.bids) or not _validate_levels(book.asks):
        return ExecutionValidation(
            status=INVALID, book_coverage_status=UNAVAILABLE,
            warnings=["book is empty or contains invalid levels"],
            validation_notional_usd=validation_notional_usd,
        )
    bids = sorted(book.bids, key=lambda level: level.price, reverse=True)
    asks = sorted(book.asks, key=lambda level: level.price)
    best_bid, best_ask = bids[0].price, asks[0].price
    if best_bid >= best_ask:
        return ExecutionValidation(
            status=INVALID, book_coverage_status=UNAVAILABLE,
            warnings=["book is crossed or locked"],
            best_bid=best_bid, best_ask=best_ask,
            validation_notional_usd=validation_notional_usd,
        )
    mid = (best_bid + best_ask) / 2
    spread = best_ask - best_bid
    spread_pct = spread / mid * 100
    spread_bps = spread / mid * 10_000
    bid025, bid025complete = _depth(bids, mid, 0.25, "bid")
    ask025, ask025complete = _depth(asks, mid, 0.25, "ask")
    bid050, bid050complete = _depth(bids, mid, 0.50, "bid")
    ask050, ask050complete = _depth(asks, mid, 0.50, "ask")

    quote_notional = validation_notional_usd / quote_to_usd_rate

    buy = simulate_buy(asks, quote_notional, mid)
    sell = simulate_sell(bids, buy.filled_quantity, mid)
    if not buy.fully_covered:
        warnings.append("top-10 asks do not cover validation notional")
    if not sell.fully_covered:
        warnings.append("top-10 bids do not cover comparable exit quantity")
    drag = None
    if buy.fully_covered and sell.fully_covered and buy.vwap and sell.vwap:
        drag = (buy.vwap - sell.vwap) / buy.vwap * 100

    short_quantity = quote_notional / mid if mid > 0 else 0.0
    short_sell = simulate_sell(bids, short_quantity, mid)
    short_cover = simulate_buy_quantity(asks, short_sell.filled_quantity, mid)
    short_drag = None
    if (
        short_sell.fully_covered
        and short_cover.fully_covered
        and short_sell.filled_notional > 0
    ):
        short_drag = (
            short_cover.filled_notional - short_sell.filled_notional
        ) / short_sell.filled_notional * 100

    trade_status, trade_price, trade_age, trade_warnings = _trade_context(
        trades, ticker_last, mid, now
    )
    warnings.extend(trade_warnings)
    if not buy.fully_covered or not sell.fully_covered:
        book_coverage_status = INSUFFICIENT
    elif ask050complete and bid050complete:
        book_coverage_status = COMPLETE
    else:
        book_coverage_status = PARTIAL
    return ExecutionValidation(
        status=VALID, book_coverage_status=book_coverage_status,
        warnings=warnings, best_bid=best_bid, best_ask=best_ask,
        mid_price=mid, absolute_spread=spread, spread_pct=spread_pct,
        spread_bps=spread_bps,
        visible_bid_notional=sum(level.price * level.quantity for level in bids) * quote_to_usd_rate,
        visible_ask_notional=sum(level.price * level.quantity for level in asks) * quote_to_usd_rate,
        bid_depth_025_usd=bid025 * quote_to_usd_rate,
        ask_depth_025_usd=ask025 * quote_to_usd_rate,
        bid_depth_025_complete=bid025complete,
        ask_depth_025_complete=ask025complete,
        bid_depth_050_usd=bid050 * quote_to_usd_rate,
        ask_depth_050_usd=ask050 * quote_to_usd_rate,
        bid_depth_050_complete=bid050complete,
        ask_depth_050_complete=ask050complete,
        validation_notional_usd=validation_notional_usd,
        buy_vwap=buy.vwap, sell_vwap=sell.vwap,
        buy_market_impact_pct=buy.market_impact_pct,
        sell_market_impact_pct=sell.market_impact_pct,
        buy_visible_coverage_pct=buy.visible_coverage_pct,
        sell_visible_coverage_pct=sell.visible_coverage_pct,
        buy_fully_covered=buy.fully_covered, sell_fully_covered=sell.fully_covered,
        estimated_visible_round_trip_market_drag_pct=drag,
        estimated_visible_short_round_trip_market_drag_pct=short_drag,
        recent_trade_status=trade_status, latest_trade_price=trade_price,
        latest_trade_age_seconds=trade_age,
        recent_trade_count=len(trades) if trades is not None else 0,
    )
