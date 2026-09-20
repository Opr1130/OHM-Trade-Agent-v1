"""B/C-3 canonical Level-1 quote-evidence producer for Paper v2.

Paper v2 execution attempts, fills and STOP/TARGET protection actions must cite
exact canonical Level-1 quote evidence. This module is the single converter from
a read-only Level-1 book observation into that canonical payload, so quote
evidence is built one way and cannot drift between callers.

It performs no I/O and owns no market-data fetching on purpose: the caller
supplies an already-observed book from the existing read-only book surfaces.
That keeps market-data authority where it already lives and makes this converter
deterministic and directly testable.

Fail-closed: a book that is stale, future-dated, crossed, non-positive or
otherwise not a valid Level-1 observation produces no payload at all. Nothing
here fabricates a quote or widens execution authority - every payload is
validated through the frozen B/C-0/B/C-1 quote contract before it is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math

from app.opip.contracts.paper_execution import (
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
)
from app.opip.contracts.paper_execution_runtime import (
    quote_evidence_idempotency_key,
    validate_quote_evidence_payload,
)
from app.opip.contracts.temporal import require_utc
from app.opip.contracts.serialization import iso_z

#: The only source kind the frozen quote contract accepts. Aggregate/OHLC market
#: observations are never promoted to quote fidelity.
LEVEL_1_BOOK = "LEVEL_1_BOOK"

#: Exact temporal basis for a locally observed book. The frozen temporal contract
#: rejects MODEL_ASSIGNED as an EXACT basis, so a quote time is only ever claimed
#: from an actual observation.
QUOTE_TIME_BASIS = "SOURCE_REPORTED"


class QuoteEvidenceUnavailableError(RuntimeError):
    """No defensible quote could be produced, so execution must not proceed."""


def _positive_finite(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field_name} must be a finite positive number")
    return number


def _canonical_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have leading or trailing whitespace")
    if not value:
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    return value


@dataclass(frozen=True)
class Level1BookObservation:
    """One read-only Level-1 book observation, as supplied by a market surface.

    Validation happens at construction, so an invalid observation cannot be
    carried far enough to become canonical evidence.
    """

    venue: str
    native_symbol: str
    quote_currency: str
    instrument_version: str
    best_bid: float
    best_ask: float
    bid_quantity: float
    ask_quantity: float
    observed_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "venue",
            "native_symbol",
            "quote_currency",
            "instrument_version",
        ):
            object.__setattr__(
                self, field_name, _canonical_text(getattr(self, field_name), field_name=field_name)
            )
        for field_name in ("best_bid", "best_ask", "bid_quantity", "ask_quantity"):
            object.__setattr__(
                self, field_name, _positive_finite(getattr(self, field_name), field_name=field_name)
            )
        if self.ask_quantity <= 0 or self.bid_quantity <= 0:
            raise ValueError("book quantities must be positive")
        if self.best_ask < self.best_bid:
            raise ValueError("best_ask cannot be below best_bid")
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field_name="observed_at"),
        )


def quote_is_fresh(
    observation: Level1BookObservation,
    *,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    """Whether an observation is usable evidence at ``now``.

    A future-dated book is refused as well as a stale one: evidence that has not
    happened yet cannot justify an execution.
    """
    moment = require_utc(now, field_name="now")
    if moment < observation.observed_at:
        return False
    return (moment - observation.observed_at) <= timedelta(seconds=int(max_age_seconds))


def build_quote_evidence_payload(
    observation: Level1BookObservation,
    *,
    quote_evidence_id: str,
) -> dict:
    """Build the canonical quote payload, validated by the frozen contract."""
    payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "quote_evidence_id": _canonical_text(
            quote_evidence_id, field_name="quote_evidence_id"
        ),
        "instrument_version": observation.instrument_version,
        "venue": observation.venue,
        "native_symbol": observation.native_symbol,
        "quote_currency": observation.quote_currency,
        "source_kind": LEVEL_1_BOOK,
        "best_bid": observation.best_bid,
        "best_ask": observation.best_ask,
        "bid_quantity": observation.bid_quantity,
        "ask_quantity": observation.ask_quantity,
        "quote_time": {
            "precision": "EXACT",
            "basis": QUOTE_TIME_BASIS,
            "occurred_at": iso_z(observation.observed_at, field_name="observed_at"),
        },
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }
    # The frozen contract is the authority on canonical quote shape; a payload it
    # rejects is never returned, so no caller can submit invalid quote evidence.
    return validate_quote_evidence_payload(payload)


def require_fresh_quote_evidence(
    observation: Level1BookObservation,
    *,
    quote_evidence_id: str,
    now: datetime,
    max_age_seconds: int,
) -> dict:
    """Return canonical quote evidence, or fail closed with no evidence at all."""
    if not quote_is_fresh(
        observation, now=now, max_age_seconds=max_age_seconds
    ):
        raise QuoteEvidenceUnavailableError(
            "Level-1 book observation is not fresh enough to be quote evidence"
        )
    return build_quote_evidence_payload(
        observation, quote_evidence_id=quote_evidence_id
    )


def quote_evidence_identity(payload: dict) -> str:
    """The canonical idempotency key for a quote payload."""
    return quote_evidence_idempotency_key(payload)


__all__ = [
    "LEVEL_1_BOOK",
    "QUOTE_TIME_BASIS",
    "Level1BookObservation",
    "QuoteEvidenceUnavailableError",
    "build_quote_evidence_payload",
    "quote_evidence_identity",
    "quote_is_fresh",
    "require_fresh_quote_evidence",
]
