"""Intent / ACK models for the canonical writer IPC."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Priority = Literal["HIGH", "NORMAL", "LOW"]
AckStatus = Literal["OK", "DUPLICATE_OK", "REJECTED", "RETRYABLE"]
EventType = Literal[
    "alert_governor.transition.recorded",
    "alert_governor.reservation.released",
    "alert_governor.capture_gap.recorded",
    # PR3 feature bus. These reuse the existing generic events and watermarks
    # tables, so no physical schema version bump is required. Names are owned
    # by app.opip.contracts.events; the literal is restated here only because
    # the type annotation cannot be computed from that frozenset.
    "market.observation.recorded",
    "market.instrument_version.recorded",
    "feature.snapshot.recorded",
    "feature.checkpoint.recorded",
    "coverage.gap.recorded",
    "feature.restart.recorded",
    # P1A decision intelligence foundation. Kept isolated to its own stream and
    # does not mutate the physical schema. The runtime connector only allows the
    # canonical writer to accept the event names below.
    "decision_intelligence.context.recorded",
    "decision_intelligence.request.recorded",
    "decision_intelligence.transition.recorded",
    "decision_intelligence.role_result.recorded",
    "decision_intelligence.assessment.recorded",
    "decision_intelligence.invocation.recorded",
    "decision_intelligence.comparison.recorded",
    # PR-A terminal paper outcome authority. Owned by
    # app.opip.contracts.paper_outcome; restated here because the annotation
    # cannot be computed from that frozenset. Admission is unchanged in form:
    # LOW priority, no ops handoff, validated payload.
    "paper_outcome.terminal.recorded",
    # B/C-1 Paper v2 execution ground truth. Opportunity disposition is writer-
    # owned through the atomic admission RPC.
    "paper_execution.quote_evidence.recorded",
    "paper_execution.order_intent.recorded",
    "paper_execution.attempt.recorded",
    "paper_execution.fill.recorded",
    # B/C-2 protection and terminal reconciliation ground truth. The protection
    # trigger is listed for completeness of the paper v2 vocabulary but is
    # writer-owned through the atomic protection action RPC; it is deliberately
    # absent from the generic submission set.
    "paper_protection.plan.recorded",
    "paper_protection.state.recorded",
    "paper_protection.trigger.recorded",
    "paper_execution.reconciliation.recorded",
]
OpsOperation = Literal["RECORD", "RELEASE"]
HandoffStatus = Literal["PENDING", "APPLIED", "SUPERSEDED"]


@dataclass(frozen=True)
class WriterIntent:
    schema_version: int
    priority: Priority
    idempotency_key: str
    event_type: EventType
    payload: dict[str, Any]
    event_time: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    # Present for transition.recorded / reservation.released producer intents.
    ops_handoff: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WriterIntent":
        return cls(
            schema_version=int(raw["schema_version"]),
            priority=str(raw["priority"]),  # type: ignore[arg-type]
            idempotency_key=str(raw["idempotency_key"]),
            event_type=str(raw["event_type"]),  # type: ignore[arg-type]
            payload=dict(raw.get("payload") or {}),
            event_time=(str(raw["event_time"]) if raw.get("event_time") else None),
            causation_id=(str(raw["causation_id"]) if raw.get("causation_id") else None),
            correlation_id=(
                str(raw["correlation_id"]) if raw.get("correlation_id") else None
            ),
            ops_handoff=(
                dict(raw["ops_handoff"]) if isinstance(raw.get("ops_handoff"), dict) else None
            ),
        )


@dataclass(frozen=True)
class WriterAck:
    status: AckStatus
    event_id: str | None = None
    history_epoch: int | None = None
    local_sequence: int | None = None
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WriterAck":
        return cls(
            status=str(raw["status"]),  # type: ignore[arg-type]
            event_id=(str(raw["event_id"]) if raw.get("event_id") else None),
            history_epoch=(
                int(raw["history_epoch"]) if raw.get("history_epoch") is not None else None
            ),
            local_sequence=(
                int(raw["local_sequence"]) if raw.get("local_sequence") is not None else None
            ),
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


@dataclass(frozen=True)
class PaperPortfolioState:
    """Read-only projection of one paper portfolio's concurrency token and capacity.

    Carries exactly what a Paper-v2 producer needs to construct the frozen
    admission request, and nothing speculative: the quote currency, the monotone
    portfolio version, the reserved capital and the active reservation count.
    """

    status: str
    quote_currency: str | None = None
    portfolio_version: int | None = None
    reserved_capital: float | None = None
    active_reservations: int | None = None
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperPortfolioState":
        return cls(
            status=str(raw["status"]),  # type: ignore[arg-type]
            quote_currency=(
                str(raw["quote_currency"]) if raw.get("quote_currency") else None
            ),
            portfolio_version=(
                int(raw["portfolio_version"])
                if raw.get("portfolio_version") is not None
                else None
            ),
            reserved_capital=(
                float(raw["reserved_capital"])
                if raw.get("reserved_capital") is not None
                else None
            ),
            active_reservations=(
                int(raw["active_reservations"])
                if raw.get("active_reservations") is not None
                else None
            ),
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


@dataclass(frozen=True)
class PaperV2ExecutionState:
    """Read-only projection of one Paper-v2 trade's canonical progress.

    Exists so the producer can resume from canonical evidence instead of process
    memory: it reports which stages are already committed, with the identities
    needed to continue only the missing ones. It carries no capability - reading
    progress confers no authority to change anything.
    """

    status: str
    disposition_id: str | None = None
    admitted: bool = False
    paper_trade_id: str | None = None
    reservation_id: str | None = None
    quote_currency: str | None = None
    decision_context_id: str | None = None
    expected_portfolio_version: int | None = None
    disposition: str | None = None
    entry_order_intent_id: str | None = None
    execution_attempt_id: str | None = None
    fill_id: str | None = None
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    protection_plan: dict[str, Any] | None = None
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperV2ExecutionState":
        return cls(
            status=str(raw["status"]),
            disposition_id=(
                str(raw["disposition_id"]) if raw.get("disposition_id") else None
            ),
            admitted=bool(raw.get("admitted", False)),
            paper_trade_id=(
                str(raw["paper_trade_id"]) if raw.get("paper_trade_id") else None
            ),
            reservation_id=(
                str(raw["reservation_id"]) if raw.get("reservation_id") else None
            ),
            quote_currency=(
                str(raw["quote_currency"]) if raw.get("quote_currency") else None
            ),
            decision_context_id=(
                str(raw["decision_context_id"])
                if raw.get("decision_context_id")
                else None
            ),
            expected_portfolio_version=(
                int(raw["expected_portfolio_version"])
                if raw.get("expected_portfolio_version") is not None
                else None
            ),
            disposition=(
                str(raw["disposition"]) if raw.get("disposition") else None
            ),
            entry_order_intent_id=(
                str(raw["entry_order_intent_id"])
                if raw.get("entry_order_intent_id")
                else None
            ),
            execution_attempt_id=(
                str(raw["execution_attempt_id"])
                if raw.get("execution_attempt_id")
                else None
            ),
            fill_id=str(raw["fill_id"]) if raw.get("fill_id") else None,
            filled_quantity=float(raw.get("filled_quantity") or 0.0),
            remaining_quantity=float(raw.get("remaining_quantity") or 0.0),
            protection_plan=(
                dict(raw["protection_plan"])
                if isinstance(raw.get("protection_plan"), dict)
                else None
            ),
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


@dataclass
class PendingHandoff:
    event_id: str
    operation: OpsOperation
    identity: str
    transition_key: str
    message_id: int | None
    created_new: bool
    reservation_token: str | None
    state_file: str
    status: HandoffStatus
    created_at: str
    applied_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
