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
    # B/C-3 increment 6A decision snapshot. Owned by
    # app.opip.contracts.paper_execution_runtime; restated here because the
    # annotation cannot be computed from that frozenset. It is the durable proof
    # of the canonical episode snapshot a decision was taken against, so a
    # DecisionContext can cite it as real provenance.
    "paper_execution.decision_snapshot.recorded",
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
class PaperV2ActiveExposure:
    """One canonical Paper-v2 position that still has remaining exposure.

    Read-only, derived only from committed canonical execution evidence. Every
    field is canonically provable; a fact that cannot be proven is absent rather
    than guessed. There is no reservation-only entry here: an admitted trade with
    no fills has no exposure and is not listed.
    """

    paper_trade_id: str
    disposition_id: str
    symbol: str
    quote_currency: str
    direction: str
    filled_quantity: float
    exited_quantity: float
    remaining_quantity: float
    #: The canonical economic basis of the *remaining* exposure, in quote currency:
    #: the committed cost basis of the quantity still held, derived from the trade's
    #: own committed entry fills. It is deliberately not mark-to-market - no
    #: market-risk model is frozen for Paper v2 - and deliberately not the raw
    #: quantity, which is not a currency amount. The existing portfolio gate compares
    #: position size against account capital, so it needs this basis.
    remaining_notional_basis: float = 0.0
    protection_plan_id: str | None = None
    protection_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperV2ActiveExposure":
        return cls(
            paper_trade_id=str(raw["paper_trade_id"]),
            disposition_id=str(raw["disposition_id"]),
            symbol=str(raw["symbol"]),
            quote_currency=str(raw["quote_currency"]),
            direction=str(raw["direction"]),
            filled_quantity=float(raw.get("filled_quantity") or 0.0),
            exited_quantity=float(raw.get("exited_quantity") or 0.0),
            remaining_quantity=float(raw.get("remaining_quantity") or 0.0),
            remaining_notional_basis=float(
                raw.get("remaining_notional_basis") or 0.0
            ),
            protection_plan_id=(
                str(raw["protection_plan_id"])
                if raw.get("protection_plan_id")
                else None
            ),
            protection_state=(
                str(raw["protection_state"])
                if raw.get("protection_state")
                else None
            ),
        )


@dataclass(frozen=True)
class PaperV2ActiveExposures:
    """Read-only projection of every canonically active Paper-v2 exposure.

    An active exposure is one whose committed execution evidence proves a positive
    remaining quantity. A reservation with no fills is not exposure, and a fully
    reconciled trade is no longer active. Health-gated like the other canonical
    read projections: an unhealthy store never returns an apparently authoritative
    exposure set.
    """

    status: str
    exposures: list[PaperV2ActiveExposure] = field(default_factory=list)
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exposures": [item.to_dict() for item in self.exposures],
            "error_code": self.error_code,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperV2ActiveExposures":
        entries = raw.get("exposures")
        return cls(
            status=str(raw["status"]),
            exposures=[
                PaperV2ActiveExposure.from_dict(item)
                for item in (entries if isinstance(entries, list) else [])
            ],
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


@dataclass(frozen=True)
class PaperV2RecoverableExecutions:
    """Read-only projection of committed Paper-v2 trades that need continuation.

    Lifecycle recovery must not depend on the original opportunity qualifying again,
    so this lists work that is already authorized and merely incomplete: a committed
    ENTRY attempt that is still fill-capable with no fill yet. Each entry carries the
    committed payloads needed to finish it, so recovery never re-derives anything and
    never needs the originating scan.

    This projection cannot admit: it reports existing trades only.
    """

    status: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "entries": [dict(entry) for entry in self.entries],
            "error_code": self.error_code,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperV2RecoverableExecutions":
        entries = raw.get("entries")
        return cls(
            status=str(raw["status"]),
            entries=[
                dict(entry) for entry in (entries if isinstance(entries, list) else [])
            ],
            error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
            detail=(str(raw["detail"]) if raw.get("detail") else None),
        )


@dataclass(frozen=True)
class PaperV2ProtectionWorkItem:
    """One Paper-v2 trade's committed protection/exit lifecycle state.

    Read-only, derived only from committed canonical evidence: every field is a fact
    the store already holds, and a fact that cannot be proven is absent rather than
    guessed. It deliberately includes trades that are already flat but not yet
    ``FINAL_VERIFIED`` - those disappear from the active-exposure projection the
    moment the SELL fill lands, and reconciliation recovery must still find them.
    """

    paper_trade_id: str
    disposition_id: str | None = None
    decision_context_id: str | None = None
    reservation_id: str | None = None
    quote_currency: str | None = None
    instrument_version: str | None = None
    native_symbol: str | None = None

    entry_quantity: float = 0.0
    exited_quantity: float = 0.0
    remaining_quantity: float = 0.0
    gross_pnl: float = 0.0
    execution_costs: float = 0.0
    #: The admission's approved reservation, which a truthful reconciliation must
    #: state exactly (the writer binds it to the canonical admission).
    reserved_capital: float = 0.0

    #: Effective (highest-sequenced, activated) protection plan and its state.
    protection_plan: dict[str, Any] | None = None
    protection_state: str | None = None
    plan_seq: int | None = None
    state_seq: int | None = None
    trigger_seq: int | None = None

    #: Committed lifecycle evidence, oldest first where it is a list.
    triggers: list[dict[str, Any]] = field(default_factory=list)
    protection_states: list[dict[str, Any]] = field(default_factory=list)
    exit_order_intents: list[dict[str, Any]] = field(default_factory=list)
    exit_attempts: list[dict[str, Any]] = field(default_factory=list)
    exit_fills: list[dict[str, Any]] = field(default_factory=list)
    entry_fills: list[dict[str, Any]] = field(default_factory=list)
    latest_reconciliation: dict[str, Any] | None = None

    #: Committed quote payloads cited by this trade's triggers and EXIT attempts,
    #: keyed by ``quote_evidence_id``. Carried so a fill can be completed after a
    #: restart purely from the evidence the attempt itself cites, with no fresh
    #: market read and no mutable setting.
    quote_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: Whether a fill-capable EXIT attempt is outstanding (so the trade can still
    #: change exposure), and whether it is terminally verified.
    exit_attempt_fill_capable: bool = False
    final_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperV2ProtectionWorkItem":
        def _opt_str(key: str) -> str | None:
            value = raw.get(key)
            return str(value) if value else None

        def _opt_int(key: str) -> int | None:
            value = raw.get(key)
            return int(value) if value is not None else None

        def _dicts(key: str) -> list[dict[str, Any]]:
            value = raw.get(key)
            return [dict(item) for item in value] if isinstance(value, list) else []

        return cls(
            paper_trade_id=str(raw["paper_trade_id"]),
            disposition_id=_opt_str("disposition_id"),
            decision_context_id=_opt_str("decision_context_id"),
            reservation_id=_opt_str("reservation_id"),
            quote_currency=_opt_str("quote_currency"),
            instrument_version=_opt_str("instrument_version"),
            native_symbol=_opt_str("native_symbol"),
            entry_quantity=float(raw.get("entry_quantity") or 0.0),
            exited_quantity=float(raw.get("exited_quantity") or 0.0),
            remaining_quantity=float(raw.get("remaining_quantity") or 0.0),
            gross_pnl=float(raw.get("gross_pnl") or 0.0),
            execution_costs=float(raw.get("execution_costs") or 0.0),
            reserved_capital=float(raw.get("reserved_capital") or 0.0),
            protection_plan=(
                dict(raw["protection_plan"])
                if isinstance(raw.get("protection_plan"), dict)
                else None
            ),
            protection_state=_opt_str("protection_state"),
            plan_seq=_opt_int("plan_seq"),
            state_seq=_opt_int("state_seq"),
            trigger_seq=_opt_int("trigger_seq"),
            triggers=_dicts("triggers"),
            protection_states=_dicts("protection_states"),
            exit_order_intents=_dicts("exit_order_intents"),
            exit_attempts=_dicts("exit_attempts"),
            exit_fills=_dicts("exit_fills"),
            entry_fills=_dicts("entry_fills"),
            latest_reconciliation=(
                dict(raw["latest_reconciliation"])
                if isinstance(raw.get("latest_reconciliation"), dict)
                else None
            ),
            quote_evidence={
                str(key): dict(value)
                for key, value in (raw.get("quote_evidence") or {}).items()
                if isinstance(value, dict)
            },
            exit_attempt_fill_capable=bool(
                raw.get("exit_attempt_fill_capable", False)
            ),
            final_verified=bool(raw.get("final_verified", False)),
        )


@dataclass(frozen=True)
class PaperV2ProtectionWork:
    """Read-only projection of every trade the protection runtime may need to act on.

    Health-gated like the other canonical reads: a degraded store must not report an
    empty work list, because that would read as "nothing to protect" and could let
    exposure go unmanaged.
    """

    status: str
    items: list[PaperV2ProtectionWorkItem] = field(default_factory=list)
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "items": [item.to_dict() for item in self.items],
            "error_code": self.error_code,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaperV2ProtectionWork":
        items = raw.get("items")
        return cls(
            status=str(raw["status"]),
            items=[
                PaperV2ProtectionWorkItem.from_dict(item)
                for item in (items if isinstance(items, list) else [])
            ],
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
    #: The approved reservation, from the committed admission request. Needed to
    #: write a truthful terminal reconciliation, which must state the canonical
    #: reservation exactly.
    requested_reservation_amount: float | None = None
    entry_order_intent_id: str | None = None
    execution_attempt_id: str | None = None
    fill_id: str | None = None
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    protection_plan: dict[str, Any] | None = None
    #: Committed stage payloads, copied verbatim from canonical evidence. A restart
    #: resumes from these instead of rebuilding a stage from mutable runtime state,
    #: which is what keeps a committed stage immutable across a restart.
    entry_order_intent: dict[str, Any] | None = None
    execution_attempt: dict[str, Any] | None = None
    quote_evidence: dict[str, Any] | None = None
    fill: dict[str, Any] | None = None
    terminal_reconciliation: dict[str, Any] | None = None
    #: Whether the committed ENTRY attempt can still receive fills. A caller must
    #: not treat a trade as safely releasable while this is true, because exposure
    #: could still appear.
    entry_attempt_fill_capable: bool = False
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
            requested_reservation_amount=(
                float(raw["requested_reservation_amount"])
                if raw.get("requested_reservation_amount") is not None
                else None
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
            entry_order_intent=(
                dict(raw["entry_order_intent"])
                if isinstance(raw.get("entry_order_intent"), dict)
                else None
            ),
            execution_attempt=(
                dict(raw["execution_attempt"])
                if isinstance(raw.get("execution_attempt"), dict)
                else None
            ),
            quote_evidence=(
                dict(raw["quote_evidence"])
                if isinstance(raw.get("quote_evidence"), dict)
                else None
            ),
            fill=(
                dict(raw["fill"]) if isinstance(raw.get("fill"), dict) else None
            ),
            terminal_reconciliation=(
                dict(raw["terminal_reconciliation"])
                if isinstance(raw.get("terminal_reconciliation"), dict)
                else None
            ),
            entry_attempt_fill_capable=bool(
                raw.get("entry_attempt_fill_capable", False)
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
