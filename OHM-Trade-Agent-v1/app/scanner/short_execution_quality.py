from app.scanner.execution_validation import COMPLETE, FRESH, PARTIAL
from app.scanner.models import MarketSnapshot


MAX_SHORT_SPREAD_BPS = 30.0
MAX_SHORT_ROUND_TRIP_DRAG_PCT = 0.75
MIN_VISIBLE_COVERAGE_PCT = 99.0


def short_execution_is_tradeable(candidate: MarketSnapshot) -> tuple[bool, list[str]]:
    """Require both short-entry sellability and eventual buyback liquidity."""
    execution = candidate.execution_validation
    reasons: list[str] = []
    if execution is None:
        return False, ["Execution validation unavailable"]
    if execution.book_coverage_status not in {COMPLETE, PARTIAL}:
        reasons.append("Top-10 book does not fully cover both sides")
    if not execution.sell_fully_covered:
        reasons.append("Bid book cannot cover short-entry validation quantity")
    if not execution.buy_fully_covered:
        reasons.append("Ask book cannot cover comparable buyback quantity")
    if (execution.sell_visible_coverage_pct or 0.0) < MIN_VISIBLE_COVERAGE_PCT:
        reasons.append("Short-entry sell coverage is below 99%")
    if (execution.buy_visible_coverage_pct or 0.0) < MIN_VISIBLE_COVERAGE_PCT:
        reasons.append("Buyback coverage is below 99%")
    if execution.spread_bps is None or execution.spread_bps > MAX_SHORT_SPREAD_BPS:
        reasons.append("Spread exceeds short quality limit")
    drag = execution.estimated_visible_short_round_trip_market_drag_pct
    if drag is None or drag > MAX_SHORT_ROUND_TRIP_DRAG_PCT:
        reasons.append("Short sell-then-buyback market drag exceeds quality limit")
    if execution.recent_trade_status != FRESH:
        reasons.append("Recent public trade evidence is not fresh")
    return not reasons, reasons
