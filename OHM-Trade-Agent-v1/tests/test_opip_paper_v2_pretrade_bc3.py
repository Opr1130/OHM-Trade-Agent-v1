"""B/C-3 pre-trade book adapter tests (ARB decision 1).

Proves the approved public pre-trade book is converted into defensible execution
quote evidence, with per-side timestamp validation and fail-closed behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.exchanges.kraken import BookLevel, KrakenAPIError, PreTradeBook
from app.opip.contracts.serialization import iso_z
from app.services.paper_v2_pretrade_adapter import (
    CANONICAL_VENUE,
    fetch_level1_observation,
    observation_from_pre_trade_book,
    parse_source_timestamp,
)
from app.services.paper_v2_quote_evidence import (
    Level1BookObservation,
    QuoteEvidenceUnavailableError,
)

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
NATIVE_SYMBOL = "SOL/USD"
MAX_AGE = 15


def _level(
    price: float,
    quantity: float,
    *,
    published: datetime | None = None,
) -> BookLevel:
    return BookLevel(
        price=price,
        quantity=quantity,
        publication_timestamp=(
            iso_z(published, field_name="published") if published is not None else None
        ),
    )


def _book(
    *,
    symbol: str = NATIVE_SYMBOL,
    bid: float = 99.9,
    ask: float = 100.1,
    bid_qty: float = 10.0,
    ask_qty: float = 12.0,
    bid_time: datetime | None = None,
    ask_time: datetime | None = None,
    bids: list | None = None,
    asks: list | None = None,
) -> PreTradeBook:
    default_time = NOW - timedelta(seconds=5)
    return PreTradeBook(
        symbol=symbol,
        bids=bids
        if bids is not None
        else [_level(bid, bid_qty, published=bid_time or default_time)],
        asks=asks
        if asks is not None
        else [_level(ask, ask_qty, published=ask_time or default_time)],
    )


def _convert(book: PreTradeBook, **overrides) -> Level1BookObservation:
    fields = {
        "instrument_version_id": INSTRUMENT_VERSION_ID,
        "native_symbol": NATIVE_SYMBOL,
        "quote_currency": "USD",
        "now": NOW,
        "max_age_seconds": MAX_AGE,
    }
    fields.update(overrides)
    return observation_from_pre_trade_book(book, **fields)


# ---------------------------------------------------------------------------
# Accepted
# ---------------------------------------------------------------------------


def test_fresh_top_bid_and_ask_are_accepted():
    observation = _convert(_book())
    assert observation.best_bid == 99.9
    assert observation.best_ask == 100.1
    assert observation.venue == CANONICAL_VENUE
    assert observation.native_symbol == NATIVE_SYMBOL
    assert observation.quote_currency == "USD"
    assert observation.instrument_version == INSTRUMENT_VERSION_ID


def test_top_of_book_quantities_are_recorded_exactly():
    observation = _convert(_book(bid_qty=3.25, ask_qty=7.5))
    assert observation.bid_quantity == 3.25
    assert observation.ask_quantity == 7.5


def test_non_top_levels_are_ignored():
    """Only the top of book is execution authority."""
    book = _book(
        bids=[
            _level(99.9, 10.0, published=NOW - timedelta(seconds=5)),
            _level(99.0, 99.0, published=NOW - timedelta(seconds=5)),
        ],
        asks=[
            _level(100.1, 12.0, published=NOW - timedelta(seconds=5)),
            _level(101.0, 99.0, published=NOW - timedelta(seconds=5)),
        ],
    )
    observation = _convert(book)
    assert observation.best_bid == 99.9
    assert observation.best_ask == 100.1


def test_observation_timestamp_is_the_later_validated_side():
    """The later side is the conservative basis for causality."""
    bid_time = NOW - timedelta(seconds=9)
    ask_time = NOW - timedelta(seconds=2)
    observation = _convert(_book(bid_time=bid_time, ask_time=ask_time))
    assert observation.observed_at == ask_time


def test_exactly_at_the_age_boundary_is_accepted():
    boundary = NOW - timedelta(seconds=MAX_AGE)
    observation = _convert(_book(bid_time=boundary, ask_time=boundary))
    assert observation.observed_at == boundary


# ---------------------------------------------------------------------------
# Rejected: presence and shape
# ---------------------------------------------------------------------------


def test_missing_bids_are_rejected():
    with pytest.raises(QuoteEvidenceUnavailableError, match="no bids"):
        _convert(_book(bids=[]))


def test_missing_asks_are_rejected():
    with pytest.raises(QuoteEvidenceUnavailableError, match="no asks"):
        _convert(_book(asks=[]))


@pytest.mark.parametrize("price", [0.0, -1.0])
def test_invalid_price_is_rejected(price):
    with pytest.raises(QuoteEvidenceUnavailableError, match="price must be positive"):
        _convert(_book(bid=price))


@pytest.mark.parametrize("quantity", [0.0, -2.0])
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(QuoteEvidenceUnavailableError, match="quantity must be positive"):
        _convert(_book(ask_qty=quantity))


def test_non_book_like_input_is_rejected():
    with pytest.raises(QuoteEvidenceUnavailableError, match="not a Kraken book"):
        _convert({"bids": [], "asks": []})


def test_mismatched_symbol_is_rejected():
    with pytest.raises(QuoteEvidenceUnavailableError, match="does not match"):
        _convert(_book(symbol="SOL/USDT"))


def test_crossed_book_is_rejected():
    with pytest.raises(QuoteEvidenceUnavailableError, match="crossed"):
        _convert(_book(bid=101.0, ask=100.0))


# ---------------------------------------------------------------------------
# Rejected: timestamps, per side independently
# ---------------------------------------------------------------------------


def test_missing_bid_publication_timestamp_is_rejected():
    book = _book(bids=[_level(99.9, 10.0, published=None)])
    with pytest.raises(QuoteEvidenceUnavailableError, match="bid publication"):
        _convert(book)


def test_missing_ask_publication_timestamp_is_rejected():
    book = _book(asks=[_level(100.1, 12.0, published=None)])
    with pytest.raises(QuoteEvidenceUnavailableError, match="ask publication"):
        _convert(book)


@pytest.mark.parametrize("bad", ["not-a-time", "", "   ", "2026-13-45T99:99:99Z"])
def test_unparseable_publication_timestamp_is_rejected(bad):
    book = _book(bids=[BookLevel(price=99.9, quantity=10.0, publication_timestamp=bad)])
    with pytest.raises(QuoteEvidenceUnavailableError, match="bid publication"):
        _convert(book)


def test_naive_publication_timestamp_is_rejected():
    book = _book(
        bids=[BookLevel(price=99.9, quantity=10.0, publication_timestamp="2026-09-19T11:59:55")]
    )
    with pytest.raises(QuoteEvidenceUnavailableError, match="timezone-aware"):
        _convert(book)


def test_stale_bid_is_rejected_even_when_the_ask_is_fresh():
    book = _book(
        bid_time=NOW - timedelta(seconds=MAX_AGE + 1),
        ask_time=NOW - timedelta(seconds=1),
    )
    with pytest.raises(QuoteEvidenceUnavailableError, match="bid publication time is stale"):
        _convert(book)


def test_stale_ask_is_rejected_even_when_the_bid_is_fresh():
    book = _book(
        bid_time=NOW - timedelta(seconds=1),
        ask_time=NOW - timedelta(seconds=MAX_AGE + 1),
    )
    with pytest.raises(QuoteEvidenceUnavailableError, match="ask publication time is stale"):
        _convert(book)


def test_future_dated_bid_is_rejected():
    book = _book(bid_time=NOW + timedelta(seconds=1))
    with pytest.raises(QuoteEvidenceUnavailableError, match="bid publication time is in the future"):
        _convert(book)


def test_future_dated_ask_is_rejected():
    book = _book(ask_time=NOW + timedelta(seconds=1))
    with pytest.raises(QuoteEvidenceUnavailableError, match="ask publication time is in the future"):
        _convert(book)


def test_non_positive_max_age_is_rejected():
    with pytest.raises(QuoteEvidenceUnavailableError, match="max age must be positive"):
        _convert(_book(), max_age_seconds=0)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def test_iso_timestamp_with_z_is_parsed_as_utc():
    assert parse_source_timestamp("2026-09-19T11:59:55Z", side="bid") == NOW - timedelta(
        seconds=5
    )


def test_epoch_seconds_are_parsed():
    epoch = (NOW - timedelta(seconds=5)).timestamp()
    assert parse_source_timestamp(epoch, side="bid") == NOW - timedelta(seconds=5)


def test_epoch_milliseconds_are_disambiguated():
    epoch_ms = (NOW - timedelta(seconds=5)).timestamp() * 1000.0
    assert parse_source_timestamp(epoch_ms, side="bid") == NOW - timedelta(seconds=5)


@pytest.mark.parametrize("bad", [None, "", "   ", 0, -1, float("nan"), [1, 2], {"a": 1}])
def test_unusable_timestamp_value_is_rejected(bad):
    with pytest.raises(QuoteEvidenceUnavailableError):
        parse_source_timestamp(bad, side="bid")


# ---------------------------------------------------------------------------
# Fetch path
# ---------------------------------------------------------------------------


class _FakeKraken:
    """A public-client stand-in that is accepted as a KrakenClient subclass."""

    def __init__(self, *, book=None, error=None):
        self._book = book
        self._error = error

    def get_pre_trade(self, symbol):
        if self._error is not None:
            raise self._error
        return self._book


def _public_client(book=None, error=None):
    """Build a real KrakenClient whose transport returns the supplied book body."""
    from app.exchanges.kraken import KrakenClient

    class _Transport:
        def request(self, endpoint, params, timeout_seconds):
            if error is not None:
                raise error
            levels = book
            return {
                "symbol": NATIVE_SYMBOL,
                **levels,
            }

        def telemetry_snapshot(self):
            return {}

    return KrakenClient(transport=_Transport())


def test_fetch_reads_the_public_pre_trade_book():
    client = _public_client(
        book={
            "bids": [{"price": 99.9, "qty": 10.0, "publication_ts": "2026-09-19T11:59:55Z"}],
            "asks": [{"price": 100.1, "qty": 12.0, "publication_ts": "2026-09-19T11:59:55Z"}],
        }
    )
    observation = fetch_level1_observation(
        client,
        symbol=NATIVE_SYMBOL,
        instrument_version_id=INSTRUMENT_VERSION_ID,
        native_symbol=NATIVE_SYMBOL,
        quote_currency="USD",
        now=NOW,
        max_age_seconds=MAX_AGE,
    )
    assert observation.best_bid == 99.9
    assert observation.best_ask == 100.1
    assert observation.bid_quantity == 10.0
    assert observation.ask_quantity == 12.0


def test_fetch_converts_a_kraken_read_failure_to_fail_closed():
    from app.exchanges.kraken import KrakenTransportError

    client = _public_client(error=KrakenTransportError("network down"))
    with pytest.raises(QuoteEvidenceUnavailableError, match="unavailable"):
        fetch_level1_observation(
            client,
            symbol=NATIVE_SYMBOL,
            instrument_version_id=INSTRUMENT_VERSION_ID,
            native_symbol=NATIVE_SYMBOL,
            quote_currency="USD",
            now=NOW,
            max_age_seconds=MAX_AGE,
        )


def test_fetch_rejects_a_non_public_client():
    with pytest.raises(QuoteEvidenceUnavailableError, match="not a public Kraken client"):
        fetch_level1_observation(
            _FakeKraken(book=_book()),
            symbol=NATIVE_SYMBOL,
            instrument_version_id=INSTRUMENT_VERSION_ID,
            native_symbol=NATIVE_SYMBOL,
            quote_currency="USD",
            now=NOW,
            max_age_seconds=MAX_AGE,
        )


def test_adapter_uses_only_the_public_pre_trade_call():
    """The adapter consumes the public book call and never the private client."""
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "paper_v2_pretrade_adapter.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert "app.exchanges.kraken" in modules
    assert not any("kraken_private" in name for name in modules)
    assert not any("kraken_position_verification" in name for name in modules)
    assert "kraken_private" not in source
    assert KrakenAPIError is not None
