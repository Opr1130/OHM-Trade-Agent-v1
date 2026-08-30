"""Canonical enums shared across the O'Pip streaming contracts.

These are provider-neutral by design: nothing here encodes a specific
exchange's WebSocket topic names, message layouts, or field semantics. Provider
adapters (a later build) translate raw venue messages into these types.
"""

from __future__ import annotations

from enum import Enum


STREAMING_SCHEMA_VERSION = 1


class StreamProvider(str, Enum):
    """Known evidence venues. Adding a venue never requires touching feature
    math — every downstream module treats providers as opaque comparable
    labels."""

    BINANCE = "BINANCE"
    BYBIT = "BYBIT"


class StreamType(str, Enum):
    AGG_TRADE = "AGG_TRADE"
    LIQUIDATION = "LIQUIDATION"


class StreamTransportState(str, Enum):
    """WebSocket connection lifecycle only.

    Deliberately separate from ProviderHealthState (app.opip.events.
    provider_health), which describes evidence pipeline health. A future
    build may derive ProviderHealthState from StreamTransportState plus
    evidence freshness, but the two enums are never merged: a connection can
    be CONNECTED while producing degraded evidence, and GAPPED describes a
    detected discontinuity, not a health verdict.
    """

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    GAPPED = "GAPPED"
    BACKOFF = "BACKOFF"
    OVERFLOW = "OVERFLOW"
    STOPPED = "STOPPED"


class SequenceStatus(str, Enum):
    """Outcome of one sequence observation. See sequencing.py for policy."""

    FIRST = "FIRST"
    CONTIGUOUS = "CONTIGUOUS"
    DUPLICATE = "DUPLICATE"
    GAP = "GAP"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    RESET_NEW_EPOCH = "RESET_NEW_EPOCH"
    UNSUPPORTED = "UNSUPPORTED"


# Sequence statuses that represent a genuine, evidenced discontinuity within
# one connection epoch. RESET_NEW_EPOCH is intentionally excluded: a reconnect
# alone is not evidence of a lost event (see sequencing.py docstring).
DISCONTINUOUS_SEQUENCE_STATUSES = frozenset(
    {SequenceStatus.GAP, SequenceStatus.OUT_OF_ORDER}
)


class EvidenceQualityState(str, Enum):
    """Coarse evidence-quality verdict. See quality.py for the full model."""

    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    INCOMPLETE = "INCOMPLETE"
    UNUSABLE = "UNUSABLE"


# Ordering for combining quality across venues/windows: the combined result is
# never better than the worst input.
EVIDENCE_QUALITY_RANK: dict[EvidenceQualityState, int] = {
    EvidenceQualityState.COMPLETE: 0,
    EvidenceQualityState.DEGRADED: 1,
    EvidenceQualityState.INCOMPLETE: 2,
    EvidenceQualityState.UNUSABLE: 3,
}


class TradeSide(str, Enum):
    """Canonical aggressor side, independent of exchange wording.

    UNKNOWN is a first-class value, never a fallback silently treated as a
    direction. See features.normalize_trade_side.
    """

    BUY_AGGRESSOR = "BUY_AGGRESSOR"
    SELL_AGGRESSOR = "SELL_AGGRESSOR"
    UNKNOWN = "UNKNOWN"


class LiquidationSide(str, Enum):
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    SHORT_LIQUIDATION = "SHORT_LIQUIDATION"
    UNKNOWN = "UNKNOWN"


class VenueAgreementState(str, Enum):
    """Richer than a binary agree/disagree flag; carries evidence context."""

    ALIGNED_POSITIVE = "ALIGNED_POSITIVE"
    ALIGNED_NEGATIVE = "ALIGNED_NEGATIVE"
    DISAGREEMENT = "DISAGREEMENT"
    MIXED_NEUTRAL = "MIXED_NEUTRAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class LiquidationSyncState(str, Enum):
    SYNCHRONIZED = "SYNCHRONIZED"
    NOT_SYNCHRONIZED = "NOT_SYNCHRONIZED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ArrivalDecision(str, Enum):
    """How a single observation relates to a window's closure state.

    This is the policy BUILD 4.2's worker must follow; BUILD 4.1 defines and
    tests it but does not run it against a live clock.
    """

    ACCEPTED_OPEN = "ACCEPTED_OPEN"
    ACCEPTED_NEW_WINDOW = "ACCEPTED_NEW_WINDOW"
    LATE_AFTER_SEAL = "LATE_AFTER_SEAL"
