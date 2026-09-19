"""B/C-3 pre-trade book adapter for Paper v2 execution-time quote evidence.

The approved execution-time Level-1 source is the public read-only Kraken
pre-trade book (``KrakenClient.get_pre_trade``). This adapter converts that book
into the :class:`Level1BookObservation` the canonical quote-evidence producer
consumes.

Why the scanner snapshot is not used: a scan-time snapshot may be decision
evidence, but it is not sufficiently point-in-time for execution. Execution
evidence must come from a book observed immediately before the attempt/fill.

No private API, no credentials, no exchange-write authority: only the public
read-only client is reachable from here, and only its pre-trade book call.

Timestamp discipline (ARB decision 1)
-------------------------------------

Kraken reports a publication timestamp per book level. Each side is validated
**independently** for presence, parseability, future-dating and staleness, so a
fresh ask can never rescue a stale bid. The observation timestamp handed to the
quote contract is the *later* of the two validated sides: that is when the whole
top of book was definitively observed, and taking the later value is the
conservative choice for causality (an earlier value would overstate how early the
book was known). Nothing is derived from the local system clock - a side with no
usable source timestamp fails closed rather than being timestamped locally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.exchanges.kraken import BookLevel, KrakenAPIError, KrakenClient, PreTradeBook
from app.opip.contracts.temporal import require_utc
from app.services.paper_v2_quote_evidence import (
    Level1BookObservation,
    QuoteEvidenceUnavailableError,
)

#: Canonical venue for the approved source. Normalized to lower case because the
#: canonical instrument contract stores venue lower-case.
CANONICAL_VENUE = "kraken"

_UTC_OFFSET = "+00:00"
_UTC_Z = "Z"


def parse_source_timestamp(value: object, *, side: str) -> datetime:
    """Parse one Kraken publication timestamp, failing closed.

    Accepts the source-reported ISO-8601 form, and a source-reported numeric
    epoch (seconds, milliseconds, microseconds or nanoseconds are disambiguated by
    magnitude). Both are values Kraken supplied - nothing is inferred from the
    local clock - so accepting either form does not weaken temporal semantics.
    """
    if isinstance(value, bool) or value is None:
        raise QuoteEvidenceUnavailableError(
            f"{side} publication timestamp is missing"
        )
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0 or number != number or number in (float("inf"), float("-inf")):
            raise QuoteEvidenceUnavailableError(
                f"{side} publication timestamp is not a usable epoch"
            )
        # Disambiguate by magnitude: seconds until ~year 2286 (1e10).
        if number >= 1e17:
            seconds = number / 1e9
        elif number >= 1e14:
            seconds = number / 1e6
        elif number >= 1e11:
            seconds = number / 1e3
        else:
            seconds = number
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise QuoteEvidenceUnavailableError(
                f"{side} publication timestamp is not a usable epoch"
            ) from exc

    if not isinstance(value, str) or not value.strip():
        raise QuoteEvidenceUnavailableError(
            f"{side} publication timestamp is missing"
        )
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace(_UTC_Z, _UTC_OFFSET))
    except ValueError as exc:
        raise QuoteEvidenceUnavailableError(
            f"{side} publication timestamp is not parseable"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QuoteEvidenceUnavailableError(
            f"{side} publication timestamp must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _require_top_level(levels: object, *, side: str) -> BookLevel:
    if not isinstance(levels, (list, tuple)) or not levels:
        raise QuoteEvidenceUnavailableError(f"pre-trade book has no {side}")
    top = levels[0]
    if not isinstance(top, BookLevel):
        raise QuoteEvidenceUnavailableError(f"{side} top level is not a book level")
    price = top.price
    quantity = top.quantity
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise QuoteEvidenceUnavailableError(f"{side} price is not a number")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        raise QuoteEvidenceUnavailableError(f"{side} quantity is not a number")
    if not float(price) > 0:
        raise QuoteEvidenceUnavailableError(f"{side} price must be positive")
    if not float(quantity) > 0:
        raise QuoteEvidenceUnavailableError(f"{side} quantity must be positive")
    return top


def observation_from_pre_trade_book(
    book: object,
    *,
    instrument_version_id: str,
    native_symbol: str,
    quote_currency: str,
    now: datetime,
    max_age_seconds: int,
) -> Level1BookObservation:
    """Convert a public pre-trade book into a validated Level-1 observation.

    Fails closed on: missing bids or asks, invalid price or quantity, a missing or
    unparseable publication timestamp on either side, either side future-dated,
    either side stale beyond ``max_age_seconds``, a crossed book, or a symbol that
    does not match the requested canonical native symbol.
    """
    if not isinstance(book, PreTradeBook):
        raise QuoteEvidenceUnavailableError("pre-trade book is not a Kraken book")
    moment = require_utc(now, field_name="now")
    bound = int(max_age_seconds)
    if bound < 1:
        raise QuoteEvidenceUnavailableError("quote max age must be positive")

    book_symbol = str(book.symbol or "").strip()
    if book_symbol != str(native_symbol).strip():
        raise QuoteEvidenceUnavailableError(
            "pre-trade book symbol does not match the canonical native symbol"
        )

    top_bid = _require_top_level(book.bids, side="bids")
    top_ask = _require_top_level(book.asks, side="asks")

    bid_time = parse_source_timestamp(top_bid.publication_timestamp, side="bid")
    ask_time = parse_source_timestamp(top_ask.publication_timestamp, side="ask")

    for side, observed in (("bid", bid_time), ("ask", ask_time)):
        if observed > moment:
            raise QuoteEvidenceUnavailableError(f"{side} publication time is in the future")
        if (moment - observed).total_seconds() > bound:
            raise QuoteEvidenceUnavailableError(
                f"{side} publication time is stale beyond the quote max age"
            )

    if float(top_ask.price) < float(top_bid.price):
        raise QuoteEvidenceUnavailableError("pre-trade book is crossed")

    # The observation is the later of the two validated sides.
    observed_at = max(bid_time, ask_time)

    return Level1BookObservation(
        venue=CANONICAL_VENUE,
        native_symbol=str(native_symbol),
        quote_currency=str(quote_currency),
        instrument_version=instrument_version_id,
        best_bid=float(top_bid.price),
        best_ask=float(top_ask.price),
        bid_quantity=float(top_bid.quantity),
        ask_quantity=float(top_ask.quantity),
        observed_at=observed_at,
    )


def fetch_level1_observation(
    client: Any,
    *,
    symbol: str,
    instrument_version_id: str,
    native_symbol: str,
    quote_currency: str,
    now: datetime,
    max_age_seconds: int,
) -> Level1BookObservation:
    """Read a fresh public pre-trade book and convert it, failing closed.

    A Kraken read failure is a fail-closed condition, not a licensing reason to
    fall back to another paper engine.
    """
    if not isinstance(client, KrakenClient):
        raise QuoteEvidenceUnavailableError("client is not a public Kraken client")
    try:
        book = client.get_pre_trade(symbol)
    except KrakenAPIError as exc:
        raise QuoteEvidenceUnavailableError(
            f"public pre-trade book is unavailable: {type(exc).__name__}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - any read failure is fail-closed
        raise QuoteEvidenceUnavailableError(
            f"public pre-trade book read failed: {type(exc).__name__}"
        ) from exc
    return observation_from_pre_trade_book(
        book,
        instrument_version_id=instrument_version_id,
        native_symbol=native_symbol,
        quote_currency=quote_currency,
        now=now,
        max_age_seconds=max_age_seconds,
    )


__all__ = [
    "CANONICAL_VENUE",
    "fetch_level1_observation",
    "observation_from_pre_trade_book",
    "parse_source_timestamp",
]
