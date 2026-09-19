"""B/C-3 Level-1 quote-evidence producer tests.

The producer must turn a read-only Level-1 book observation into canonical quote
evidence the frozen contract accepts, and must produce nothing at all when the
book cannot defensibly support execution.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.contracts.paper_execution import PAPER_EXECUTION_MODEL_VERSION
from app.opip.contracts.paper_execution_runtime import (
    PAPER_QUOTE_EVIDENCE_RECORDED,
    quote_evidence_idempotency_key,
    validate_quote_evidence_payload,
)
from app.services.paper_v2_quote_evidence import (
    LEVEL_1_BOOK,
    QUOTE_TIME_BASIS,
    Level1BookObservation,
    QuoteEvidenceUnavailableError,
    build_quote_evidence_payload,
    quote_evidence_identity,
    quote_is_fresh,
    require_fresh_quote_evidence,
)

INSTRUMENT_VERSION = "INSTR:kraken:SOL:USD:1"
NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
MAX_AGE = 15


def _observation(**overrides) -> Level1BookObservation:
    fields = {
        "venue": "kraken",
        "native_symbol": "SOL/USD",
        "quote_currency": "USD",
        "instrument_version": INSTRUMENT_VERSION,
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "observed_at": NOW - timedelta(seconds=5),
    }
    fields.update(overrides)
    return Level1BookObservation(**fields)


# ---------------------------------------------------------------------------
# Canonical payload
# ---------------------------------------------------------------------------


def test_payload_is_accepted_by_the_frozen_quote_contract():
    payload = build_quote_evidence_payload(_observation(), quote_evidence_id="quote-1")
    # Re-validating through the frozen contract proves the producer cannot emit a
    # shape the writer would reject.
    assert validate_quote_evidence_payload(payload) == payload


def test_payload_carries_the_observed_book_exactly():
    payload = build_quote_evidence_payload(_observation(), quote_evidence_id="quote-1")
    assert payload == {
        "schema_version": 1,
        "quote_evidence_id": "quote-1",
        "instrument_version": INSTRUMENT_VERSION,
        "venue": "kraken",
        "native_symbol": "SOL/USD",
        "quote_currency": "USD",
        "source_kind": LEVEL_1_BOOK,
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": {
            "precision": "EXACT",
            "basis": QUOTE_TIME_BASIS,
            "occurred_at": "2026-09-19T11:59:55Z",
        },
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }


def test_quote_time_is_exact_and_source_reported():
    payload = build_quote_evidence_payload(_observation(), quote_evidence_id="quote-1")
    assert payload["quote_time"]["precision"] == "EXACT"
    # A model-assigned basis would be rejected as an EXACT claim by the frozen
    # temporal contract, so the basis must reflect a real observation.
    assert payload["quote_time"]["basis"] == QUOTE_TIME_BASIS


def test_identity_is_derived_from_the_canonical_quote_key():
    payload = build_quote_evidence_payload(_observation(), quote_evidence_id="quote-1")
    assert quote_evidence_identity(payload) == quote_evidence_idempotency_key(payload)
    assert quote_evidence_identity(payload) == (
        f"{PAPER_QUOTE_EVIDENCE_RECORDED}:quote-1"
    )


def test_identity_is_stable_across_rebuilt_payloads():
    first = build_quote_evidence_payload(_observation(), quote_evidence_id="quote-1")
    second = build_quote_evidence_payload(_observation(), quote_evidence_id="quote-1")
    assert quote_evidence_identity(first) == quote_evidence_identity(second)
    assert first == second


# ---------------------------------------------------------------------------
# Fail-closed construction
# ---------------------------------------------------------------------------


def test_crossed_book_cannot_be_constructed():
    with pytest.raises(ValueError, match="best_ask cannot be below best_bid"):
        _observation(best_bid=101.0, best_ask=100.0)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("best_bid", 0.0),
        ("best_bid", -1.0),
        ("best_ask", 0.0),
        ("bid_quantity", 0.0),
        ("ask_quantity", -2.0),
        ("best_bid", float("nan")),
        ("best_ask", float("inf")),
        ("best_bid", True),
        ("best_bid", "100"),
    ],
)
def test_non_positive_or_non_numeric_book_is_rejected(field_name, value):
    with pytest.raises(ValueError, match=field_name):
        _observation(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("venue", " kraken"),
        ("native_symbol", "SOL/USD "),
        ("quote_currency", "USD "),
        ("instrument_version", " INSTR:kraken:SOL:USD:1"),
        ("venue", ""),
        ("native_symbol", "   "),
        ("venue", 42),
    ],
)
def test_non_canonical_identity_cannot_be_constructed(field_name, value):
    with pytest.raises(ValueError, match=field_name):
        _observation(**{field_name: value})


def test_naive_observation_time_is_rejected():
    with pytest.raises(ValueError):
        _observation(observed_at=datetime(2026, 9, 19, 12, 0, 0))


def test_unsupported_quote_currency_is_rejected_by_the_frozen_contract():
    # Construction permits it; the frozen contract is the authority that refuses it.
    observation = _observation(quote_currency="EUR")
    with pytest.raises(ValueError, match="quote_currency"):
        build_quote_evidence_payload(observation, quote_evidence_id="quote-eur")


def test_padded_quote_id_is_rejected():
    with pytest.raises(ValueError, match="quote_evidence_id"):
        build_quote_evidence_payload(_observation(), quote_evidence_id=" quote-1 ")


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_fresh_observation_is_usable():
    assert quote_is_fresh(_observation(), now=NOW, max_age_seconds=MAX_AGE) is True


def test_exactly_at_the_age_boundary_is_still_usable():
    at_boundary = _observation(observed_at=NOW - timedelta(seconds=MAX_AGE))
    assert quote_is_fresh(at_boundary, now=NOW, max_age_seconds=MAX_AGE) is True


def test_stale_observation_is_refused():
    stale = _observation(observed_at=NOW - timedelta(seconds=MAX_AGE + 1))
    assert quote_is_fresh(stale, now=NOW, max_age_seconds=MAX_AGE) is False
    with pytest.raises(QuoteEvidenceUnavailableError, match="not fresh enough"):
        require_fresh_quote_evidence(
            stale, quote_evidence_id="quote-stale", now=NOW, max_age_seconds=MAX_AGE
        )


def test_future_dated_observation_is_refused():
    future = _observation(observed_at=NOW + timedelta(seconds=1))
    assert quote_is_fresh(future, now=NOW, max_age_seconds=MAX_AGE) is False
    with pytest.raises(QuoteEvidenceUnavailableError):
        require_fresh_quote_evidence(
            future, quote_evidence_id="quote-future", now=NOW, max_age_seconds=MAX_AGE
        )


def test_require_fresh_returns_canonical_evidence_for_a_fresh_book():
    payload = require_fresh_quote_evidence(
        _observation(), quote_evidence_id="quote-ok", now=NOW, max_age_seconds=MAX_AGE
    )
    assert payload["quote_evidence_id"] == "quote-ok"
    assert validate_quote_evidence_payload(payload) == payload


def test_stale_book_produces_no_payload_at_all():
    """Fail closed means no evidence is emitted, not a degraded one."""
    stale = _observation(observed_at=NOW - timedelta(seconds=MAX_AGE + 60))
    with pytest.raises(QuoteEvidenceUnavailableError):
        require_fresh_quote_evidence(
            stale, quote_evidence_id="quote-stale", now=NOW, max_age_seconds=MAX_AGE
        )


def test_freshness_is_independent_of_identity():
    """A fresh book for any identity is usable; freshness never fabricates one."""
    renamed = replace(_observation(), native_symbol="SOL/USDT")
    assert quote_is_fresh(renamed, now=NOW, max_age_seconds=MAX_AGE) is True
