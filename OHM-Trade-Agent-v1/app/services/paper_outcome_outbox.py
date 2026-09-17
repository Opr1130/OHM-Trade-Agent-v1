"""Terminal paper-outcome outbox (PR-A).

Delivery contract for authoritative terminal paper economic evidence.

Ordering is deliberate and load-bearing:

    1. terminal economics determined
    2. exact WriterIntent built
    3. terminal lifecycle + that exact intent persisted atomically in state.json
    4. state commit succeeds
    5. CanonicalWriterClient.submit(persisted intent)

Step 3 is why this is an outbox and not a submission: the intent is durable
*before* any network call, so a retry resubmits byte-identical evidence rather
than rebuilding it from whatever the current market or lifecycle happens to
look like. Rebuilding would risk a different ``outcome_id`` or different
economics, which the canonical writer would correctly reject as a conflict.

Dispositions are three-way, because "not committed" is not one condition:

* ``COMMITTED``          - ``OK`` or ``DUPLICATE_OK``. Resolves any matching gap.
* ``PENDING``            - transport failure or ``RETRYABLE``. Reconcile later.
* ``PERMANENT_FAILURE``  - ``REJECTED``. Retrying cannot help, so it never
                           loops; the evidence stays visibly incomplete and an
                           integrity alarm is raised.

``DUPLICATE_OK`` is a success. It is exactly what the acknowledgement-loss path
produces, and treating it as failure would re-spool a committed outcome forever.

Production must submit through ``CanonicalWriterClient`` (the daemon owns the
store). A direct ``CanonicalWriter`` would contend for the store lock.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

from app.opip.canonical.bridge import shadow_capture_enabled
from app.opip.canonical.client import CanonicalWriterClient, WriterClient
from app.opip.canonical.gap_spool import (
    append_capture_gap,
    bump_gap_retry,
    load_gap_spool,
    resolve_gap,
)
from app.opip.canonical.models import WriterAck, WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.contracts.paper_outcome import (
    CANCELLED,
    CLOSED,
    EXIT_REASONS,
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    UNRESOLVED,
    build_terminal_outcome_payload,
    resolve_quote_currency,
    terminal_outcome_idempotency_key,
)

logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION = 1

DELIVERY_PENDING = "PENDING"
DELIVERY_COMMITTED = "COMMITTED"
DELIVERY_PERMANENT_FAILURE = "PERMANENT_FAILURE"

#: Failure codes recorded on an envelope. Kept explicit so reconciliation and
#: alerting can distinguish transport from contract defects.
ERROR_TRANSPORT_FAILURE = "TRANSPORT_FAILURE"
ERROR_RETRYABLE_ACK = "RETRYABLE_ACK"
ERROR_UNCLASSIFIED_ACK = "UNCLASSIFIED_ACK"
ERROR_PAYLOAD_CONFLICT = "IDEMPOTENCY_PAYLOAD_CONFLICT"
ERROR_INVALID_INTENT = "INVALID_INTENT"
ERROR_UNBUILDABLE_PAYLOAD = "UNBUILDABLE_PAYLOAD"

#: Outcomes whose delivery may still be in flight. Only these are reconciled.
_RECONCILABLE = (DELIVERY_PENDING,)

_test_client: WriterClient | None = None


def set_writer_client_for_tests(client: WriterClient | None) -> None:
    """Install an injectable writer client (repository convention).

    Mirrors ``bridge.set_writer_client_for_tests`` and
    ``features.publisher.set_writer_client_for_tests`` so producer tests never
    need a live UDS socket.
    """
    global _test_client
    _test_client = client


def _client() -> WriterClient:
    return _test_client if _test_client is not None else CanonicalWriterClient()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _normalized_exit_reason(trade: Any) -> str:
    """Map a lifecycle exit reason onto the canonical vocabulary.

    The simulator emits parameterised gap reasons (``OHLC_GAP:60->120``); the
    canonical reason is the family, so the numeric detail does not become part
    of the queryable contract.
    """
    raw = str(getattr(trade, "exit_reason", None) or "").strip().upper()
    if raw.startswith("OHLC_GAP"):
        return "OHLC_GAP"
    if raw in EXIT_REASONS:
        return raw
    if str(getattr(trade, "status", "")) == UNRESOLVED:
        return "UNRESOLVED"
    return raw


def build_outcome_envelope(
    trade: Any,
    *,
    terminal_event_type: str,
    terminal_event_id: str,
) -> dict[str, Any] | None:
    """Build the durable recovery envelope for a terminal lifecycle.

    Returns ``None`` for a non-terminal lifecycle - there is no outcome yet.
    Returns a ``PERMANENT_FAILURE`` envelope when the terminal facts cannot be
    represented canonically (unknown quote currency, unknown exit reason). The
    failure is recorded rather than raised so the evidence gap is durable and
    visible instead of silent.
    """
    status = str(getattr(trade, "status", "") or "").upper()
    if status not in {CLOSED, CANCELLED, UNRESOLVED}:
        return None

    outcome_id = None
    idempotency_key = None
    try:
        exit_reason = _normalized_exit_reason(trade)
        if exit_reason not in EXIT_REASONS:
            raise ValueError(f"unknown exit reason: {exit_reason!r}")

        engine = str(getattr(trade, "execution_engine", "") or "")
        paper_trade_id = str(getattr(trade, "paper_trade_id", "") or "")

        quote_currency = resolve_quote_currency(
            str(getattr(trade, "symbol", "") or ""),
            quote_currency=getattr(trade, "quote_currency", None),
        )
        if quote_currency is None:
            raise ValueError("unknown quote currency")

        # A cancelled setup never opened, so its "entry" timestamp is the
        # lifecycle origin and it carries no executed quantity or price.
        entry_at = _parse_timestamp(
            getattr(trade, "opened_at", None) or getattr(trade, "created_at", None)
        )
        exit_at = _parse_timestamp(
            getattr(trade, "closed_at", None)
            or getattr(trade, "updated_at", None)
            or getattr(trade, "created_at", None)
        )
        if entry_at is None or exit_at is None:
            raise ValueError("terminal lifecycle is missing usable timestamps")

        if status == CLOSED:
            capital_committed: float | None = float(getattr(trade, "capital", 0.0) or 0.0)
            gross_pnl: float | None = getattr(trade, "gross_pnl", None)
            fees_paid: float | None = getattr(trade, "fees_paid", 0.0)
            net_pnl: float | None = getattr(trade, "net_pnl", None)
            net_pnl_pct: float | None = getattr(trade, "net_pnl_pct", None)
            if None in (gross_pnl, net_pnl, net_pnl_pct):
                raise ValueError("closed lifecycle is missing terminal economics")
        elif status == CANCELLED:
            # Nothing was realised, so every realised figure is zero. The
            # committed capital was still committed, though: it is a planned
            # amount, not a realised result, so it survives as recorded. No
            # executed quantity or entry price is fabricated - there was none.
            capital_committed = float(getattr(trade, "capital", 0.0) or 0.0)
            gross_pnl = fees_paid = net_pnl = net_pnl_pct = 0.0
        else:
            capital_committed = None
            gross_pnl = fees_paid = net_pnl = net_pnl_pct = None

        entry_price = getattr(trade, "entry_price", None)
        quantity = getattr(trade, "quantity_initial", None)
        executed_notional = (
            float(quantity) * float(entry_price)
            if entry_price is not None and quantity and float(quantity) > 0
            else None
        )
        if status != CLOSED:
            entry_price = None
            quantity = None
            executed_notional = None

        payload = build_terminal_outcome_payload(
            engine=engine,
            paper_trade_id=paper_trade_id,
            episode_id=str(getattr(trade, "episode_id", "") or ""),
            cohort_id=str(getattr(trade, "cohort_id", "") or ""),
            strategy_version=getattr(trade, "strategy_version", None),
            exchange="KRAKEN",
            native_symbol=str(getattr(trade, "symbol", "") or ""),
            base_asset=str(getattr(trade, "base_asset", "") or ""),
            direction=str(getattr(trade, "direction", "") or "").upper(),
            quote_currency=quote_currency,
            terminal_status=status,
            exit_reason=exit_reason,
            exit_price=(float(getattr(trade, "exit_price"))
                        if getattr(trade, "exit_price", None) is not None else None),
            entry_timestamp=entry_at,
            exit_timestamp=exit_at,
            capital_committed=capital_committed,
            gross_pnl=gross_pnl,
            fees_paid=fees_paid,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl_pct,
            final_revision=int(getattr(trade, "revision", 1) or 1),
            terminal_event_id=terminal_event_id,
            intended_entry_low=getattr(trade, "entry_low", None),
            intended_entry_high=getattr(trade, "entry_high", None),
            intended_entry_limit=getattr(trade, "entry_limit", None),
            simulated_entry_price=(float(entry_price) if entry_price is not None else None),
            quantity_initial=(float(quantity) if quantity is not None else None),
            executed_notional=executed_notional,
        )
        outcome_id = str(payload["outcome_id"])
        idempotency_key = terminal_outcome_idempotency_key(outcome_id)
        intent = WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority=PAPER_OUTCOME_PRIORITY,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
            payload=payload,
            correlation_id=str(getattr(trade, "episode_id", "") or "") or None,
        )
    except Exception as exc:
        return _envelope(
            outcome_id=outcome_id,
            idempotency_key=idempotency_key,
            intent=None,
            delivery=DELIVERY_PERMANENT_FAILURE,
            error_code=ERROR_UNBUILDABLE_PAYLOAD,
            detail=f"{type(exc).__name__}: {exc}",
        )

    return _envelope(
        outcome_id=outcome_id,
        idempotency_key=idempotency_key,
        intent=intent.to_dict(),
        delivery=DELIVERY_PENDING,
        error_code=None,
        detail=None,
    )


def _envelope(
    *,
    outcome_id: str | None,
    idempotency_key: str | None,
    intent: dict[str, Any] | None,
    delivery: str,
    error_code: str | None,
    detail: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "outcome_id": outcome_id,
        "idempotency_key": idempotency_key,
        "intent": intent,
        "delivery": delivery,
        "attempts": 0,
        "gap_id": None,
        "last_error": error_code,
        "last_detail": detail,
        "last_attempt_at": None,
    }


def _disposition_for_ack(ack: WriterAck) -> tuple[str, str | None, str | None]:
    """Map a writer acknowledgement onto a delivery disposition.

    ``DUPLICATE_OK`` is success: the identical intent is already committed,
    which is precisely the acknowledgement-loss outcome. Only ``RETRYABLE``
    may be retried, and every ``REJECTED`` is terminal so a conflicting or
    malformed outcome can never retry forever.
    """
    status = str(ack.status)
    if status in {"OK", "DUPLICATE_OK"}:
        return DELIVERY_COMMITTED, None, None
    if status == "RETRYABLE":
        return DELIVERY_PENDING, ERROR_RETRYABLE_ACK, ack.detail or ack.error_code
    if status == "REJECTED":
        error = str(ack.error_code or ERROR_INVALID_INTENT)
        if error == ERROR_PAYLOAD_CONFLICT:
            return DELIVERY_PERMANENT_FAILURE, ERROR_PAYLOAD_CONFLICT, ack.detail
        return DELIVERY_PERMANENT_FAILURE, error, ack.detail
    return DELIVERY_PENDING, ERROR_UNCLASSIFIED_ACK, f"unclassified ack status {status}"


def submit_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Submit one persisted envelope, returning the updated envelope.

    Never raises: a submission problem is a delivery state, not an exception
    that should escape into the paper trading path.
    """
    if str(envelope.get("delivery")) != DELIVERY_PENDING:
        return envelope
    intent_raw = envelope.get("intent")
    if not isinstance(intent_raw, dict):
        envelope["delivery"] = DELIVERY_PERMANENT_FAILURE
        envelope["last_error"] = ERROR_UNBUILDABLE_PAYLOAD
        return envelope

    envelope["attempts"] = int(envelope.get("attempts") or 0) + 1
    envelope["last_attempt_at"] = _now()

    try:
        ack = _client().submit(WriterIntent.from_dict(intent_raw))
    except Exception as exc:
        # Transport/decoding surface. Deliberately catches broadly: the producer
        # must not be coupled to server-internal exception types.
        envelope["delivery"] = DELIVERY_PENDING
        envelope["last_error"] = ERROR_TRANSPORT_FAILURE
        envelope["last_detail"] = f"{type(exc).__name__}: {exc}"
        return envelope

    delivery, error_code, detail = _disposition_for_ack(ack)
    envelope["delivery"] = delivery
    envelope["last_error"] = error_code
    envelope["last_detail"] = detail
    if delivery == DELIVERY_PERMANENT_FAILURE:
        logger.error(
            "terminal paper outcome permanently rejected (%s): %s",
            error_code,
            envelope.get("outcome_id"),
        )
    return envelope


# ---------------------------------------------------------------------------
# Evidence-gap spool: descriptor only, never the payload
# ---------------------------------------------------------------------------


def _resolve_gap_for(envelope: dict[str, Any], *, spool_file: Path) -> None:
    gap_id = envelope.get("gap_id")
    if not gap_id:
        return
    try:
        resolve_gap(str(gap_id), path=spool_file)
    except Exception as exc:
        logger.error("could not resolve paper evidence gap %s: %s", gap_id, exc)
        return
    envelope["gap_id"] = None


def _record_gap_for(
    envelope: dict[str, Any], *, spool_file: Path, paper_trade_id: str, episode_id: str
) -> None:
    """Record or advance a descriptor-only gap for an undelivered outcome."""
    key = str(envelope.get("idempotency_key") or envelope.get("outcome_id") or "")
    existing = envelope.get("gap_id")
    try:
        if existing:
            bump_gap_retry(str(existing), path=spool_file)
            return
        for row in load_gap_spool(spool_file)["unresolved"]:
            if str(row.get("idempotency_key") or "") == key:
                envelope["gap_id"] = str(row.get("gap_id"))
                bump_gap_retry(str(row.get("gap_id")), path=spool_file)
                return
        envelope["gap_id"] = append_capture_gap(
            idempotency_key=key,
            scan_id=episode_id,
            identity=paper_trade_id,
            intended_event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
            error_code=str(envelope.get("last_error") or ERROR_TRANSPORT_FAILURE),
            path=spool_file,
        )
    except Exception as exc:
        # A corrupt spool also makes any completeness claim unprovable, so this
        # must be observable rather than silently swallowed.
        logger.error(
            "paper outcome evidence-gap disposition failed for %s: %s",
            paper_trade_id,
            exc,
        )


def settle_envelope(
    envelope: dict[str, Any],
    *,
    paper_trade_id: str,
    episode_id: str,
    spool_file: Path,
) -> bool:
    """Reconcile one envelope against the gap spool. Returns True when committed."""
    if str(envelope.get("delivery")) == DELIVERY_COMMITTED:
        _resolve_gap_for(envelope, spool_file=spool_file)
        return True
    if str(envelope.get("delivery")) == DELIVERY_PENDING:
        _record_gap_for(
            envelope,
            spool_file=spool_file,
            paper_trade_id=paper_trade_id,
            episode_id=episode_id,
        )
        return False
    return False


def canonical_capture_enabled(settings: Any | None = None) -> bool:
    """Whether canonical outcome capture is switched on.

    Reuses the existing writer-mode gate rather than adding a new flag, so PR-A
    is inert until canonical writing is enabled and becomes active with it.
    """
    try:
        return bool(shadow_capture_enabled(settings))
    except Exception:
        return False


__all__ = [
    "DELIVERY_COMMITTED",
    "DELIVERY_PENDING",
    "DELIVERY_PERMANENT_FAILURE",
    "ENVELOPE_SCHEMA_VERSION",
    "ERROR_INVALID_INTENT",
    "ERROR_PAYLOAD_CONFLICT",
    "ERROR_RETRYABLE_ACK",
    "ERROR_TRANSPORT_FAILURE",
    "ERROR_UNBUILDABLE_PAYLOAD",
    "ERROR_UNCLASSIFIED_ACK",
    "build_outcome_envelope",
    "canonical_capture_enabled",
    "settle_envelope",
    "set_writer_client_for_tests",
    "submit_envelope",
]
