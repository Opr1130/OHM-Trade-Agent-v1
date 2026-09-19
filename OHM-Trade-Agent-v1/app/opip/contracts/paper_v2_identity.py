"""Deterministic Paper-v2 stage identities.

Every Paper-v2 record a trade emits has a deterministic identity derived only from
its canonical ancestry and its stage sequence number. One trade therefore always
resolves to the same ENTRY intent, attempt, fill, quote and protection plan, which
is what makes a retry after a restart idempotent rather than duplicating.

Why this is a shared contracts module
-------------------------------------

The producer needs these identities to *construct* a record. The canonical writer
needs the same identities to *find* an already-committed record without scanning
event history. If the two layers each derived identities themselves they could
drift, and a drifted lookup would silently miss a committed stage - which is
exactly the restart defect this exists to prevent. So both import from here.

Every value below reproduces the producer's existing derivation byte-for-byte, so
all previously committed identities remain valid. The digests are produced by the
repository's canonical ``stable_hash`` over the same component mapping.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.opip.contracts.serialization import iso_z, stable_hash

#: Stage-sequence domain prefixes. These are part of the identity, so they are
#: frozen: changing one would invalidate every committed identity.
ENTRY_ORDER_INTENT_PREFIX = "ENTRY"
EXECUTION_ATTEMPT_PREFIX = "ATTEMPT"
FILL_PREFIX = "FILL"
QUOTE_EVIDENCE_PREFIX = "PQUOTE"
NO_FILL_RECONCILIATION_PREFIX = "PNOREC"

#: The only stage sequence this frozen engine emits. A multi-stage engine would
#: extend the sequence, not the derivation.
ENTRY_STAGE_SEQ = 0

#: Protection plan sequence for the initial immutable plan.
INITIAL_PROTECTION_PLAN_SEQ = 0

#: Protection plan identity domain. Reproduces the producer's existing ``PPLAN``
#: derivation, so plans committed before this module existed still resolve.
PROTECTION_PLAN_ID_PREFIX = "PPLAN"


def paper_v2_protection_plan_id(
    paper_trade_id: str, *, plan_seq: int = INITIAL_PROTECTION_PLAN_SEQ
) -> str:
    """Canonical immutable protection-plan identity for one trade and sequence."""
    return stable_hash(
        PROTECTION_PLAN_ID_PREFIX,
        {"paper_trade_id": str(paper_trade_id), "plan_seq": int(plan_seq)},
    )


def paper_v2_entry_order_intent_id(paper_trade_id: str) -> str:
    """Canonical ENTRY order-intent identity for one paper trade."""
    return stable_hash(
        ENTRY_ORDER_INTENT_PREFIX,
        {"paper_trade_id": str(paper_trade_id), "seq": ENTRY_STAGE_SEQ},
    )


def paper_v2_entry_attempt_id(order_intent_id: str) -> str:
    """Canonical ENTRY execution-attempt identity for one order intent."""
    return stable_hash(
        EXECUTION_ATTEMPT_PREFIX,
        {"order_intent_id": str(order_intent_id), "seq": ENTRY_STAGE_SEQ},
    )


def paper_v2_entry_fill_id(order_intent_id: str) -> str:
    """Canonical ENTRY fill identity for one order intent."""
    return stable_hash(
        FILL_PREFIX,
        {"order_intent_id": str(order_intent_id), "seq": ENTRY_STAGE_SEQ},
    )


def paper_v2_quote_evidence_id(
    *,
    instrument_version_id: str,
    native_symbol: str,
    observed_at: datetime,
) -> str:
    """Canonical quote-evidence identity for one observed book.

    The identity is anchored on the *source* observation instant, so the same
    observed book always resolves to the same evidence record.
    """
    return stable_hash(
        QUOTE_EVIDENCE_PREFIX,
        {
            "instrument_version_id": str(instrument_version_id),
            "native_symbol": str(native_symbol),
            "observed_at": iso_z(observed_at, field_name="observed_at"),
        },
    )


def paper_v2_no_fill_reconciliation_id(paper_trade_id: str) -> str:
    """Canonical identity for the terminal zero-fill reconciliation.

    Deterministic from the trade, so a restarted producer finds the already
    terminal no-fill record instead of opening a second one.
    """
    return stable_hash(
        NO_FILL_RECONCILIATION_PREFIX,
        {"paper_trade_id": str(paper_trade_id), "seq": ENTRY_STAGE_SEQ},
    )


def paper_v2_stage_payload_identity(payload: Any, *, field_name: str) -> str:
    """Read a stage identity out of a committed payload, failing closed if absent."""
    if not isinstance(payload, dict):
        raise ValueError("stage payload must be a mapping")
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stage payload is missing {field_name}")
    return value


__all__ = [
    "ENTRY_ORDER_INTENT_PREFIX",
    "ENTRY_STAGE_SEQ",
    "EXECUTION_ATTEMPT_PREFIX",
    "FILL_PREFIX",
    "INITIAL_PROTECTION_PLAN_SEQ",
    "NO_FILL_RECONCILIATION_PREFIX",
    "PROTECTION_PLAN_ID_PREFIX",
    "QUOTE_EVIDENCE_PREFIX",
    "paper_v2_entry_attempt_id",
    "paper_v2_entry_fill_id",
    "paper_v2_entry_order_intent_id",
    "paper_v2_no_fill_reconciliation_id",
    "paper_v2_protection_plan_id",
    "paper_v2_quote_evidence_id",
    "paper_v2_stage_payload_identity",
]
