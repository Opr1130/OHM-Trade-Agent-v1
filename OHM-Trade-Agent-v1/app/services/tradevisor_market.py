"""Read-only native market evidence for Tradevisor evaluation.

Every call here is PUBLIC market data through the existing
``KrakenPublicTransport`` (``https://api.kraken.com/0/public``). There is no
private endpoint, no signing key, no credential and no order path in this
module, so enriching evidence cannot widen trading authority.

Nothing here re-implements an indicator or a validation rule:

* candles/ATR/EMA/percentiles come from ``market_scanner.analyze_symbol``;
* spread/depth/drag come from ``execution_validation.evaluate_execution``.

Both are the same owners the native scanner uses, so a Tradevisor decision is
made from identical evidence rather than a parallel approximation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.exchanges.kraken import KrakenAPIError, KrakenClient
from app.scanner.execution_validation import ExecutionValidation, evaluate_execution
from app.scanner.market_scanner import analyze_symbol
from app.scanner.models import MarketSnapshot

#: Bounded public-data calls per enrichment. Mirrors the scanner's own
#: validation notional so spread/drag are measured at a comparable size.
DEFAULT_VALIDATION_NOTIONAL_USD = 250.0
PUBLIC_TIMEOUT_SECONDS = 5.0
RECENT_TRADE_COUNT = 20


def native_snapshot(market: str):
    """Native snapshot provider with the scanner's own return shape."""
    return analyze_symbol(market)


def kraken_execution_evidence(
    market: str,
    *,
    notional_usd: float = DEFAULT_VALIDATION_NOTIONAL_USD,
    client: KrakenClient | None = None,
    now: datetime | None = None,
) -> ExecutionValidation | None:
    """Measure executable spread/depth for one market, or return None.

    A failure is returned as ``None`` rather than an exception so the caller
    can record explicit unavailability instead of inventing a clean book.
    """
    if notional_usd <= 0:
        return None
    resolved = client or KrakenClient(timeout_seconds=PUBLIC_TIMEOUT_SECONDS)
    try:
        ticker = resolved.get_ticker(market)
        book = resolved.get_pre_trade(market)
        trades = resolved.get_post_trade(market, count=RECENT_TRADE_COUNT)
    except (KrakenAPIError, KeyError, TypeError, ValueError):
        return None
    last = float(ticker.get("last") or 0.0)
    if last <= 0:
        return None
    return evaluate_execution(
        book,
        float(notional_usd),
        last,
        trades=trades,
        now=now or datetime.now(timezone.utc),
    )


def attach_execution_evidence(
    snapshot: MarketSnapshot,
    *,
    execution_provider=None,
    notional_usd: float = DEFAULT_VALIDATION_NOTIONAL_USD,
    now: datetime | None = None,
) -> ExecutionValidation | None:
    """Attach execution evidence to a snapshot that does not already carry it.

    A snapshot that already has execution evidence from native deep validation
    keeps it: the native funnel's measurement is never replaced.
    """
    existing = getattr(snapshot, "execution_validation", None)
    if existing is not None:
        return existing
    provider = execution_provider or kraken_execution_evidence
    try:
        evidence = provider(snapshot.symbol, notional_usd=notional_usd, now=now)
    except TypeError:
        # A simpler injected provider: provider(market) -> evidence | None
        evidence = provider(snapshot.symbol)
    except Exception:  # noqa: BLE001 - enrichment failure must not fabricate a book
        evidence = None
    if evidence is not None:
        snapshot.execution_validation = evidence
    return evidence
