from app.exchanges.kraken import KrakenClient
from app.scanner.execution_validation import (
    COMPLETE,
    FRESH,
    PARTIAL,
    evaluate_execution,
    unavailable_execution,
)
from app.scanner.models import MarketSnapshot


MAX_SHORT_SPREAD_BPS = 30.0
MAX_SHORT_ROUND_TRIP_DRAG_PCT = 0.75
MIN_VISIBLE_COVERAGE_PCT = 99.0


def _refresh_btln_margin_execution(
    candidate: MarketSnapshot,
    *,
    client: KrakenClient | None = None,
) -> None:
    """Replace reference-spot execution evidence with the actual BTNL book.

    Kraken US retail margin executes on a Bitnomial order book that is separate
    from standard spot. A SHORT cannot pass the execution-quality gate using
    only the reference spot book.
    """
    venue_symbol = candidate.margin_venue_symbol
    existing = candidate.execution_validation
    if not venue_symbol or existing is None:
        candidate.execution_validation = unavailable_execution(
            "BTNL margin execution symbol or validation notional unavailable"
        )
        return

    client = client or KrakenClient()
    try:
        book = client.get_pre_trade(venue_symbol)
    except Exception as exc:
        candidate.execution_validation = unavailable_execution(
            f"BTNL PreTrade unavailable: {exc}"
        )
        return

    try:
        trades = client.get_post_trade(venue_symbol, count=100)
    except Exception:
        trades = None

    candidate.execution_validation = evaluate_execution(
        book=book,
        validation_notional_usd=existing.validation_notional_usd,
        ticker_last=candidate.ticker_last or candidate.last_price,
        quote_to_usd_rate=1.0,
        trades=trades,
    )


def short_execution_is_tradeable(
    candidate: MarketSnapshot,
    *,
    client: KrakenClient | None = None,
    refresh_margin_book: bool = True,
) -> tuple[bool, list[str]]:
    """Require executable entry and cover liquidity on the BTNL margin book."""
    if refresh_margin_book:
        _refresh_btln_margin_execution(candidate, client=client)

    execution = candidate.execution_validation
    reasons: list[str] = []
    if execution is None:
        return False, ["Execution validation unavailable"]
    if execution.book_coverage_status not in {COMPLETE, PARTIAL}:
        reasons.append("BTNL top-10 book does not fully cover both sides")
    if not execution.sell_fully_covered:
        reasons.append("BTNL bid book cannot cover short-entry validation quantity")
    if not execution.buy_fully_covered:
        reasons.append("BTNL ask book cannot cover comparable buyback quantity")
    if (execution.sell_visible_coverage_pct or 0.0) < MIN_VISIBLE_COVERAGE_PCT:
        reasons.append("BTNL short-entry sell coverage is below 99%")
    if (execution.buy_visible_coverage_pct or 0.0) < MIN_VISIBLE_COVERAGE_PCT:
        reasons.append("BTNL buyback coverage is below 99%")
    if execution.spread_bps is None or execution.spread_bps > MAX_SHORT_SPREAD_BPS:
        reasons.append("BTNL spread exceeds short quality limit")
    drag = execution.estimated_visible_short_round_trip_market_drag_pct
    if drag is None or drag > MAX_SHORT_ROUND_TRIP_DRAG_PCT:
        reasons.append("BTNL short sell-then-buyback market drag exceeds quality limit")
    if execution.recent_trade_status != FRESH:
        reasons.append("BTNL recent public trade evidence is not fresh")
    return not reasons, reasons
