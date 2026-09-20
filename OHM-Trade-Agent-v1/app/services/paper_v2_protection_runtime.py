"""B/C-3 Paper-v2 protection, EXIT execution and terminal reconciliation runtime.

B/C-2 froze the canonical contracts that let a filled Paper-v2 position be closed:
an immutable protection plan, ``PLANNED -> ACTIVE`` arming, validated STOP/TARGET/
TIME triggers, an atomic action that commits a trigger, its EXIT order intent and
the ``TRIGGERED`` state in one transaction, SELL execution evidence, canonical fill
accounting, ``FLAT_AWAITING_RECONCILIATION`` and ``FINAL_VERIFIED`` reconciliation,
and capacity release. Only tests drove that sequence.

This module wires those *already frozen* contracts into production. It is a
coordinator, not a strategy: it never qualifies, ranks, resizes, re-prices or
re-times anything, never consults AI or Decision Intelligence, never uses the
Feature Bus, never touches a private Kraken endpoint or a funded balance, never
calls Freqtrade or Paper-v1, and never adds a scheduler. The canonical writer
remains the sole authority on ancestry, conservation and economics.

Where it runs
-------------
There is no second scheduler. The existing unified cycle invokes the recurring
opportunity scan, and this sweep rides that invocation: it processes protection
before new admissions, because protecting exposure that already exists outranks
opening more.

Why it runs regardless of new-entry authority
---------------------------------------------
Cutover readiness governs *new entries*. It must never abandon an obligation. Once
Paper v2 has created exposure, that exposure stays canonically owned and protected
until terminal closure even if the drain verdict later becomes unavailable, or the
mode is switched off. So the sweep is deliberately not gated on ``READY``: a
position that already exists is still managed. Configuration controls new
authority, not the safety of existing obligations.

Restart safety
--------------
Nothing is kept in process memory. Every decision is re-derived from the committed
projection, and every stage identity is deterministic from immutable canonical
ancestry plus a sequence. A lost ACK, a crash between stages, or a re-run therefore
re-proposes the same identity and payload - the writer answers ``DUPLICATE_OK`` -
while a changed payload under the same identity fails closed. No stage is ever
regenerated from facts that may have moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from app.opip.canonical.models import (
    PaperV2ProtectionWork,
    PaperV2ProtectionWorkItem,
)
from app.opip.contracts.paper_economics import paper_economics_for_version
from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
    ProtectionState,
    TerminalReconciliationState,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_PROTECTION_STATE_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
    paper_evidence_idempotency_key,
    validate_paper_evidence_payload,
)
from app.opip.contracts.paper_execution_runtime import (
    PaperProtectionActionRequest,
)
from app.opip.contracts.paper_v2_identity import (
    paper_v2_attempt_id,
    paper_v2_exit_order_intent_id,
    paper_v2_fill_id,
    paper_v2_protection_plan_id,
    paper_v2_protection_state_id,
    paper_v2_protection_trigger_id,
    paper_v2_reconciliation_id,
)
from app.opip.contracts.serialization import iso_z
from app.services.paper_v2_execution import (
    ExecutionRetryRequired,
    QUANTITY_TOLERANCE,
    commit_execution_quote,
    next_execution_moment,
    submit_canonical_event,
    temporal_instant,
)
from app.services.paper_v2_pretrade_adapter import system_utc_clock

#: Long-only Paper v2 exits by selling, so the executable price is the book's bid.
EXIT_SIDE = "SELL"
EXIT_INTENT_ROLE = "EXIT"
EXIT_REASON_CODE = "PROTECTION_ACTION"

#: Deterministic trigger precedence when several conditions hold at once.
#:
#: B/C-2 freezes no precedence: the frozen trigger contract validates one trigger at
#: a time and is deliberately silent on ordering. Rather than let iteration order
#: decide, this runtime applies the smallest conservative rule - protect capital
#: before banking profit, and both before a discretionary time exit. The order is
#: an explicit tuple so it can never depend on dictionary order.
TRIGGER_PRECEDENCE: tuple[str, ...] = ("STOP", "TARGET", "TIME")

#: Protection states this runtime considers armed against fresh market evidence.
ARMED_STATE = ProtectionState.ACTIVE.value


class PaperV2ProtectionError(RuntimeError):
    """Protection work could not be advanced. Never falls back to legacy."""


@dataclass(frozen=True)
class ProtectionSweepResult:
    """Structured outcome of one protection sweep. Never a silent success.

    ``new_admissions_allowed`` is the conservative gate: if any existing exposure is
    in an unsafe or unreadable protection state, no new Paper-v2 entry may be taken
    this scan. It never authorizes a legacy fallback - it only withholds new
    authority.
    """

    considered: int = 0
    activated: int = 0
    triggered: int = 0
    exit_attempted: int = 0
    filled: int = 0
    partially_exited: int = 0
    terminalized: int = 0
    retryable: int = 0
    unavailable: int = 0
    new_admissions_allowed: bool = False
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "activated": self.activated,
            "triggered": self.triggered,
            "exit_attempted": self.exit_attempted,
            "filled": self.filled,
            "partially_exited": self.partially_exited,
            "terminalized": self.terminalized,
            "retryable": self.retryable,
            "unavailable": self.unavailable,
            "new_admissions_allowed": self.new_admissions_allowed,
            "details": list(self.details),
        }


@dataclass(frozen=True)
class _QuoteTarget:
    """Minimal instrument facts ``commit_execution_quote`` needs for one exit.

    The public quote path is shared with ENTRY rather than reimplemented, so exit
    evidence is validated by exactly the freshness, future-dating and crossed-book
    rules already frozen for entry evidence.
    """

    native_symbol: str
    instrument_version_id: str
    quote_currency: str


def run_protection_sweep(
    client: Any,
    *,
    kraken_client: Any,
    settings: Any,
    execution_clock: Callable[[], datetime] | None = None,
) -> ProtectionSweepResult:
    """Advance protection, EXIT and reconciliation work for committed Paper-v2 trades.

    Pure recovery of already-authorized work: it cannot admit, requalify, resize,
    re-price, re-time or re-economise anything, and it is idempotent. One trade's
    failure never stops another, but any unsafe or unreadable exposure withholds new
    admissions for the scan.
    """
    clock = execution_clock or system_utc_clock
    considered = 0
    allowed = True
    counters = {
        "activated": 0,
        "triggered": 0,
        "exit_attempted": 0,
        "filled": 0,
        "partially_exited": 0,
        "terminalized": 0,
        "retryable": 0,
        "unavailable": 0,
    }
    details: list[str] = []
    try:
        projection = client.get_paper_v2_protection_work()
    except Exception as exc:  # noqa: BLE001 - unreadable work must be reported
        return ProtectionSweepResult(
            unavailable=1,
            new_admissions_allowed=False,
            details=[f"protection projection failed: {type(exc).__name__}"],
        )
    if not isinstance(projection, PaperV2ProtectionWork) or projection.status != "OK":
        status = getattr(projection, "status", "UNAVAILABLE")
        error = getattr(projection, "error_code", None)
        return ProtectionSweepResult(
            unavailable=1,
            new_admissions_allowed=False,
            details=[f"protection projection unavailable: {error or status}"],
        )

    for item in projection.items:
        considered += 1
        try:
            outcome = _advance_protection_item(
                item,
                client=client,
                kraken_client=kraken_client,
                settings=settings,
                clock=clock,
            )
        except Exception as exc:  # noqa: BLE001 - one trade must not stop the sweep
            counters["retryable"] += 1
            allowed = False
            details.append(
                f"retryable {item.paper_trade_id}: {type(exc).__name__}: {exc}"
            )
            continue
        if outcome == "UNSAFE":
            allowed = False
            counters["retryable"] += 1
            details.append(f"unsafe protection state for {item.paper_trade_id}")
        elif outcome == "TERMINAL":
            details.append(f"terminal {item.paper_trade_id}")
        elif outcome == "NO_EXPOSURE":
            details.append(f"no exposure {item.paper_trade_id}")
        elif outcome == "ARMED":
            details.append(f"armed, no trigger {item.paper_trade_id}")
        else:
            counters[outcome.lower()] += 1
            details.append(f"{outcome.lower()} {item.paper_trade_id}")

    return ProtectionSweepResult(
        considered=considered,
        activated=counters["activated"],
        triggered=counters["triggered"],
        exit_attempted=counters["exit_attempted"],
        filled=counters["filled"],
        partially_exited=counters["partially_exited"],
        terminalized=counters["terminalized"],
        retryable=counters["retryable"],
        unavailable=counters["unavailable"],
        new_admissions_allowed=allowed,
        details=details,
    )


def _advance_protection_item(
    item: PaperV2ProtectionWorkItem,
    *,
    client: Any,
    kraken_client: Any,
    settings: Any,
    clock: Callable[[], datetime],
) -> str:
    """Advance one trade by exactly one durable stage. Returns a disposition label."""
    if item.final_verified:
        return "TERMINAL"
    if item.remaining_quantity <= QUANTITY_TOLERANCE:
        # Flat: either nothing ever filled, or the position closed and only
        # reconciliation remains. A flat trade is absent from the active-exposure
        # projection, so this path is the only thing that can finish it.
        if item.entry_quantity > QUANTITY_TOLERANCE and item.exit_fills:
            _reconcile_flat_trade(item, client=client, clock=clock)
            return "TERMINALIZED"
        return "NO_EXPOSURE"
    if item.protection_plan is None:
        # Positive canonical exposure with no committed plan cannot be protected.
        # Refusing to invent one keeps the reservation in place and withholds new
        # admissions rather than pretending the position is safe.
        return "UNSAFE"

    # 1. An accepted, unfilled EXIT attempt can still change exposure: finish it
    #    before anything else, from committed facts only.
    if item.exit_attempt_fill_capable:
        _complete_exit_fill(item, client=client, clock=clock)
        return "FILLED"

    plan_id = str(item.protection_plan["protection_plan_id"])
    if item.protection_state == ProtectionState.PLANNED.value:
        # 2. A plan committed before exposure is armed once exposure provably
        #    exists - including after a crash between fill and activation.
        _activate_plan(item, client=client, clock=clock)
        return "ACTIVATED"

    if item.protection_state == ProtectionState.TRIGGERED.value:
        return _advance_triggered_trade(
            item,
            client=client,
            kraken_client=kraken_client,
            settings=settings,
            clock=clock,
        )

    if item.protection_state != ARMED_STATE:
        return "UNSAFE"

    # 3. Armed: evaluate the frozen conditions against fresh public evidence.
    return _evaluate_and_trigger(
        item,
        plan_id=plan_id,
        client=client,
        kraken_client=kraken_client,
        settings=settings,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Plan activation
# ---------------------------------------------------------------------------


def _activate_plan(
    item: PaperV2ProtectionWorkItem, *, client: Any, clock: Callable[[], datetime]
) -> None:
    """Arm a committed plan once canonical exposure exists (``PLANNED -> ACTIVE``).

    Uses the plan's own committed ancestry and the entry fill's own committed
    instant, so a restart in the crash window between fill and activation arms the
    exact same plan rather than a re-derived one.
    """
    plan = item.protection_plan or {}
    plan_id = str(plan["protection_plan_id"])
    plan_moment = temporal_instant(plan.get("plan_time"), field_name="plan_time")
    floor = max(
        [plan_moment]
        + [
            temporal_instant(fill.get("fill_time"), field_name="fill_time")
            for fill in item.entry_fills
        ]
    )
    moment = next_execution_moment(
        clock, floor=floor, field_name="state_time", release_safe=False
    )
    state_seq = len(item.protection_states)
    payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_event_id": paper_v2_protection_state_id(
            item.paper_trade_id, protection_plan_id=plan_id, state_seq=state_seq
        ),
        "protection_plan_id": plan_id,
        "paper_trade_id": item.paper_trade_id,
        "state_seq": state_seq,
        "from_state": ProtectionState.PLANNED.value,
        "to_state": ProtectionState.ACTIVE.value,
        "reason_code": "EXPOSURE_OBSERVED",
        "state_time": _exact(moment, "state_time"),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }
    _submit_payload(
        client,
        event_type=PAPER_PROTECTION_STATE_RECORDED,
        payload=payload,
        what="protection activation",
    )


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------


def _evaluate_and_trigger(
    item: PaperV2ProtectionWorkItem,
    *,
    plan_id: str,
    client: Any,
    kraken_client: Any,
    settings: Any,
    clock: Callable[[], datetime],
) -> str:
    """Read fresh public evidence and commit the atomic action if a condition holds.

    The trigger decision is made only from the committed effective plan. Nothing is
    resized: the requested exit is either the full canonical remaining quantity or
    the eligible target's own configured fraction of the original entry.
    """
    plan = item.protection_plan or {}
    target = _QuoteTarget(
        native_symbol=str(item.native_symbol or ""),
        instrument_version_id=str(item.instrument_version or ""),
        quote_currency=str(item.quote_currency or ""),
    )
    if not target.native_symbol or not target.instrument_version_id:
        raise PaperV2ProtectionError(
            "committed instrument facts are unavailable for exit evidence"
        )
    quote, received_at = commit_execution_quote(
        target,
        client=client,
        kraken_client=kraken_client,
        settings=settings,
        clock=clock,
    )
    decision = _evaluate_trigger(item, plan=plan, quote=quote, now=received_at)
    if decision is None:
        return "ARMED"
    trigger_type, exit_quantity, reference_price = decision
    if trigger_type in {"STOP", "TARGET"}:
        floor: datetime = received_at
    else:
        # TIME carries no market claim, so its causal floor is the expiry it proves.
        floor = _plan_expiry(plan)
    moment = next_execution_moment(
        clock,
        floor=floor,
        field_name="trigger_time",
        release_safe=False,
    )
    _commit_protection_action(
        item,
        plan_id=plan_id,
        trigger_type=trigger_type,
        exit_quantity=exit_quantity,
        reference_price=reference_price,
        quote=(quote if trigger_type in {"STOP", "TARGET"} else None),
        moment=moment,
        client=client,
    )
    return "TRIGGERED"


def _evaluate_trigger(
    item: PaperV2ProtectionWorkItem,
    *,
    plan: Mapping[str, Any],
    quote: Mapping[str, Any],
    now: datetime,
) -> tuple[str, float, float] | None:
    """The first condition that holds, in frozen conservative precedence order.

    Returns ``(trigger_type, exit_quantity, reference_price)`` or ``None``. STOP
    claims the full remaining quantity; TARGET claims only the eligible target's
    configured fraction of the *original* entry, never the remainder; TIME claims
    the full remaining quantity once the committed plan expiry has elapsed.
    """
    bid = float(quote["best_bid"])
    for trigger_type in TRIGGER_PRECEDENCE:
        if trigger_type == "STOP":
            if bid <= float(plan["stop_price"]) + QUANTITY_TOLERANCE:
                return "STOP", item.remaining_quantity, bid
        elif trigger_type == "TARGET":
            eligible = _eligible_target(item, plan)
            if eligible is None:
                continue
            if bid >= float(eligible["price"]) - QUANTITY_TOLERANCE:
                cap = min(
                    float(eligible["fraction"]) * item.entry_quantity,
                    item.remaining_quantity,
                )
                return "TARGET", cap, bid
        else:
            if _expiry_reached(plan, now):
                return "TIME", item.remaining_quantity, float(plan["stop_price"])
    return None


def _eligible_target(
    item: PaperV2ProtectionWorkItem, plan: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The next unconsumed target, in the frozen ascending-price order.

    Mirrors the writer's own derivation: each committed TARGET trigger for this plan
    consumes one target in ascending ``(price, target_id)`` order, so the next one in
    that order is the only eligible target and no target can exit twice.
    """
    plan_id = str(plan["protection_plan_id"])
    targets = sorted(
        (dict(target) for target in plan["targets"]),
        key=lambda target: (float(target["price"]), str(target["target_id"])),
    )
    consumed = sum(
        1
        for trigger in item.triggers
        if str(trigger.get("protection_plan_id")) == plan_id
        and str(trigger.get("trigger_type")) == "TARGET"
    )
    if consumed >= len(targets):
        return None
    return targets[consumed]


def _plan_expiry(plan: Mapping[str, Any]) -> datetime:
    return temporal_instant(plan.get("plan_time"), field_name="plan_time") + timedelta(
        seconds=int(plan["max_hold_seconds"])
    )


def _expiry_reached(plan: Mapping[str, Any], now: datetime) -> bool:
    """True at or after the committed expiry. Firing exactly at expiry is allowed."""
    return now >= _plan_expiry(plan)


# ---------------------------------------------------------------------------
# Atomic protection action
# ---------------------------------------------------------------------------


def _commit_protection_action(
    item: PaperV2ProtectionWorkItem,
    *,
    plan_id: str,
    trigger_type: str,
    exit_quantity: float,
    reference_price: float,
    quote: Mapping[str, Any] | None,
    moment: datetime,
    client: Any,
) -> None:
    """Build and submit the atomic protection action - the only way to trigger.

    The writer owns the transaction that commits the trigger, the EXIT order intent
    and the ``TRIGGERED`` state together. This runtime never submits those three
    events itself, because a partial commit would leave a triggered plan without an
    order, or an order without the evidence that justifies it.
    """
    if str(item.decision_context_id or "") == "":
        raise PaperV2ProtectionError("committed decision context is unavailable")
    if str(item.reservation_id or "") == "":
        raise PaperV2ProtectionError("committed reservation is unavailable")
    trigger_seq = len(
        [
            trigger
            for trigger in item.triggers
            if str(trigger.get("protection_plan_id")) == plan_id
        ]
    )
    state_seq = len(
        [
            state
            for state in item.protection_states
            if str(state.get("protection_plan_id")) == plan_id
        ]
    )
    trigger_id = paper_v2_protection_trigger_id(
        item.paper_trade_id, protection_plan_id=plan_id, trigger_seq=trigger_seq
    )
    exit_order_id = paper_v2_exit_order_intent_id(
        item.paper_trade_id, protection_plan_id=plan_id, trigger_seq=trigger_seq
    )
    trigger_payload: dict[str, Any] = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_trigger_id": trigger_id,
        "protection_plan_id": plan_id,
        "paper_trade_id": item.paper_trade_id,
        "trigger_seq": trigger_seq,
        "trigger_type": trigger_type,
        "reference_price": reference_price,
        "trigger_time": _exact(moment, "trigger_time"),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }
    if quote is not None:
        # The cited quote and the reference price must be the same fact, so the
        # trigger can never claim a level the book never showed.
        trigger_payload["market_evidence_ref"] = str(quote["quote_evidence_id"])
    exit_order_intent = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "order_intent_id": exit_order_id,
        "paper_trade_id": item.paper_trade_id,
        "decision_context_id": item.decision_context_id,
        "intent_seq": len(item.exit_order_intents),
        "intent_role": EXIT_INTENT_ROLE,
        "side": EXIT_SIDE,
        "order_type": "MARKET",
        "requested_quantity": exit_quantity,
        "requested_notional": exit_quantity * float(reference_price),
        "reason_code": EXIT_REASON_CODE,
        "intent_time": _exact(moment, "intent_time"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "reservation_id": item.reservation_id,
    }
    protection_state = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_event_id": paper_v2_protection_state_id(
            item.paper_trade_id, protection_plan_id=plan_id, state_seq=state_seq
        ),
        "protection_plan_id": plan_id,
        "paper_trade_id": item.paper_trade_id,
        "state_seq": state_seq,
        "from_state": ARMED_STATE,
        "to_state": ProtectionState.TRIGGERED.value,
        "reason_code": EXIT_REASON_CODE,
        "state_time": _exact(moment, "state_time"),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }
    request = PaperProtectionActionRequest(
        trigger=trigger_payload,
        exit_order_intent=exit_order_intent,
        protection_state=protection_state,
    )
    ack = client.trigger_paper_protection_action(request)
    _require_accepted(ack, what="protection action")


# ---------------------------------------------------------------------------
# Triggered trade: EXIT execution, residual revision, reconciliation
# ---------------------------------------------------------------------------


def _advance_triggered_trade(
    item: PaperV2ProtectionWorkItem,
    *,
    client: Any,
    kraken_client: Any,
    settings: Any,
    clock: Callable[[], datetime],
) -> str:
    """Advance a ``TRIGGERED`` plan: execute its EXIT, or re-arm the residual.

    The atomic action already created exactly one EXIT order for the plan that
    triggered. This never creates a second one - it completes that order.
    """
    plan = item.protection_plan or {}
    plan_id = str(plan["protection_plan_id"])
    # The atomic action creates one EXIT order per trigger, so the latest trigger
    # for this plan owns the outstanding order.
    plan_orders: list[tuple[int, Mapping[str, Any]]] = []
    for trigger in item.triggers:
        if str(trigger.get("protection_plan_id")) != plan_id:
            continue
        seq = int(trigger["trigger_seq"])
        expected = paper_v2_exit_order_intent_id(
            item.paper_trade_id, protection_plan_id=plan_id, trigger_seq=seq
        )
        for order in item.exit_order_intents:
            if str(order.get("order_intent_id")) == expected:
                plan_orders.append((seq, order))
    if not plan_orders:
        raise PaperV2ProtectionError(
            "TRIGGERED plan has no committed EXIT order intent"
        )
    order = max(plan_orders, key=lambda pair: pair[0])[1]
    order_id = str(order["order_intent_id"])
    order_filled = any(
        str(fill["order_intent_id"]) == order_id for fill in item.exit_fills
    )
    if order_filled:
        # The order is done, but exposure remains: this was a partial target exit.
        # The frozen contract forbids a TRIGGERED plan from firing again, so the
        # residual gets a new immutable revision.
        _commit_residual_plan(item, client=client, clock=clock)
        return "PARTIALLY_EXITED"
    order_attempts = [
        attempt
        for attempt in item.exit_attempts
        if str(attempt["order_intent_id"]) == order_id
    ]
    if order_attempts:
        # An attempt exists without a fill: finish it from its own committed
        # evidence rather than re-reading the market.
        _write_exit_fill(order_attempts[-1], item=item, client=client, clock=clock)
        return "FILLED"
    _execute_exit_order(
        item,
        order=order,
        client=client,
        kraken_client=kraken_client,
        settings=settings,
        clock=clock,
    )
    return "EXIT_ATTEMPTED"


def _execute_exit_order(
    item: PaperV2ProtectionWorkItem,
    *,
    order: Mapping[str, Any],
    client: Any,
    kraken_client: Any,
    settings: Any,
    clock: Callable[[], datetime],
) -> None:
    """Execute the committed EXIT order against fresh public bid-side evidence.

    The trigger stays historically true even if price has since moved away, so the
    original threshold is deliberately not re-checked. What *is* required is
    executable evidence for this attempt: the order may consume a newer quote, and
    the attempt and fill cite exactly the quote they consumed.

    There is no partial-fill or depth model, so the runtime never fabricates
    liquidity. If the displayed bid cannot support the full requested exit, nothing
    is recorded, the order stays pending, and a later scan retries against new
    evidence.
    """
    order_id = str(order["order_intent_id"])
    requested = float(order["requested_quantity"])
    target = _quote_target(item)
    quote, received_at = commit_execution_quote(
        target,
        client=client,
        kraken_client=kraken_client,
        settings=settings,
        clock=clock,
    )
    if requested > float(quote["bid_quantity"]) + QUANTITY_TOLERANCE:
        raise ExecutionRetryRequired(
            "committed EXIT quantity exceeds displayed bid depth; retry later"
        )
    # Causal floor: an EXIT attempt cannot be recorded before the trigger and EXIT
    # intent that authorized it. A clock behind that floor must be retried rather
    # than silently rewritten, or the attempt would claim a chronology the store
    # never observed.
    causal_floor = max(
        received_at,
        temporal_instant(order["intent_time"], field_name="intent_time"),
    )
    attempt_moment = next_execution_moment(
        clock, floor=causal_floor, field_name="attempt_time", release_safe=False
    )
    attempt_payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": paper_v2_attempt_id(order_id),
        "order_intent_id": order_id,
        "paper_trade_id": item.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact(attempt_moment, "attempt_time"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": requested,
        "market_evidence_ref": str(quote["quote_evidence_id"]),
    }
    _submit_payload(
        client,
        event_type=PAPER_EXECUTION_ATTEMPT_RECORDED,
        payload=attempt_payload,
        what="exit execution attempt",
    )
    _write_exit_fill(attempt_payload, item=item, client=client, clock=clock, quote=quote)


def _complete_exit_fill(
    item: PaperV2ProtectionWorkItem, *, client: Any, clock: Callable[[], datetime]
) -> None:
    """Finish the fill for an already-committed, fill-capable EXIT attempt.

    Rebuilt only from committed facts - the committed accepted quantity and the
    committed quote the attempt itself cites - so a lost ACK or a crash between
    attempt and fill resumes the same fill and never invents one.
    """
    fillable_order_ids = {
        str(fill["order_intent_id"]) for fill in item.exit_fills
    }
    candidates = [
        attempt
        for attempt in item.exit_attempts
        if str(attempt["execution_state"]) == "ACCEPTED"
        and str(attempt["order_intent_id"]) not in fillable_order_ids
    ]
    if not candidates:
        raise PaperV2ProtectionError("no committed fill-capable EXIT attempt")
    _write_exit_fill(candidates[-1], item=item, client=client, clock=clock)


def _write_exit_fill(
    attempt: Mapping[str, Any],
    *,
    item: PaperV2ProtectionWorkItem,
    client: Any,
    clock: Callable[[], datetime],
    quote: Mapping[str, Any] | None = None,
) -> None:
    """Commit the SELL fill implied by a committed attempt and its cited quote.

    Price is the bid the order actually exits into, quantity is the committed
    accepted quantity, and the costs come from the frozen economic model for this
    execution - never from mutable runtime configuration, so a restart cannot
    reinterpret an existing attempt.
    """
    order_id = str(attempt["order_intent_id"])
    quote_ref = str(attempt.get("market_evidence_ref") or "")
    resolved = quote
    if resolved is None:
        resolved = item.quote_evidence.get(quote_ref)
    if not isinstance(resolved, Mapping):
        raise PaperV2ProtectionError(
            "committed EXIT quote evidence is unavailable for the attempt"
        )
    quantity = float(attempt["accepted_quantity"])
    price = float(resolved["best_bid"])
    economics = paper_economics_for_version(PAPER_ECONOMIC_MODEL_VERSION)
    cost = economics.cost_components(quantity, price)
    attempt_floor = temporal_instant(attempt["attempt_time"], field_name="attempt_time")
    fill_moment = next_execution_moment(
        clock,
        floor=attempt_floor,
        field_name="fill_time",
        # An accepted attempt exists, so this trade can still change exposure. A
        # clock regression must be retried, never released.
        release_safe=False,
    )
    fill_payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": paper_v2_fill_id(order_id),
        "execution_attempt_id": str(attempt["execution_attempt_id"]),
        "order_intent_id": order_id,
        "paper_trade_id": item.paper_trade_id,
        "fill_seq": 0,
        "side": EXIT_SIDE,
        "quantity": quantity,
        "price": price,
        "fee_cost": cost["fee_cost"],
        "spread_cost": cost["spread_cost"],
        "slippage_cost": cost["slippage_cost"],
        "other_supported_cost": cost["other_supported_cost"],
        "fill_time": _exact(fill_moment, "fill_time"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": quote_ref,
    }
    _submit_payload(
        client,
        event_type=PAPER_FILL_RECORDED,
        payload=fill_payload,
        what="exit fill",
    )


def _commit_residual_plan(
    item: PaperV2ProtectionWorkItem, *, client: Any, clock: Callable[[], datetime]
) -> None:
    """Commit a new immutable plan revision covering residual exposure.

    The frozen contract forbids a ``TRIGGERED`` plan from firing again, so a partial
    target exit leaves the remainder needing its own revision. The revision changes
    only the sequence and the consumed target set: stop, remaining target prices,
    target ids and target fractions are carried over verbatim, so each residual
    fraction keeps its original meaning relative to the original entry (the writer
    proves a target exit against ``fraction x original entry quantity``).

    The original max-hold *deadline* is preserved rather than restarted, so a
    partial exit can never extend how long a position is held.
    """
    plan = item.protection_plan or {}
    plan_id = str(plan["protection_plan_id"])
    consumed = sum(
        1
        for trigger in item.triggers
        if str(trigger.get("protection_plan_id")) == plan_id
        and str(trigger.get("trigger_type")) == "TARGET"
    )
    targets = sorted(
        (dict(target) for target in plan["targets"]),
        key=lambda target: (float(target["price"]), str(target["target_id"])),
    )
    residual_targets = targets[consumed:]
    if not residual_targets:
        raise PaperV2ProtectionError(
            "no residual target remains but canonical exposure is still open"
        )
    plan_seq = int(plan["plan_seq"]) + 1
    deadline = _plan_expiry(plan)
    floor = max(
        [temporal_instant(plan.get("plan_time"), field_name="plan_time")]
        + [
            temporal_instant(fill.get("fill_time"), field_name="fill_time")
            for fill in item.exit_fills
        ]
    )
    moment = next_execution_moment(
        clock, floor=floor, field_name="plan_time", release_safe=False
    )
    # Truncating toward zero can only make the residual expire *earlier* than the
    # original deadline, which is the safe direction. A deadline already in the
    # past yields zero, so TIME fires on the next evaluation instead of extending.
    remaining_hold = max(0, int((deadline - moment).total_seconds()))
    payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_plan_id": paper_v2_protection_plan_id(
            item.paper_trade_id, plan_seq=plan_seq
        ),
        "paper_trade_id": item.paper_trade_id,
        "plan_seq": plan_seq,
        "stop_price": float(plan["stop_price"]),
        "targets": residual_targets,
        "max_hold_seconds": remaining_hold,
        "plan_time": _exact(moment, "plan_time"),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }
    _submit_payload(
        client,
        event_type=PAPER_PROTECTION_PLAN_RECORDED,
        payload=payload,
        what="residual protection plan",
    )
    _activate_plan(
        _replacement(item, protection_plan=payload), client=client, clock=clock
    )


def _reconcile_flat_trade(
    item: PaperV2ProtectionWorkItem, *, client: Any, clock: Callable[[], datetime]
) -> None:
    """Reconcile a canonically flat trade and release its reservation exactly once.

    Totals come from canonical fills, never from producer arithmetic, so the claim
    the writer verifies is one the store can already prove. ``FINAL_VERIFIED`` is
    submitted only after ``FLAT_AWAITING_RECONCILIATION`` and only when every frozen
    verification requirement holds; only ``FINAL_VERIFIED`` releases capacity.
    """
    reconciliations = (
        [item.latest_reconciliation] if item.latest_reconciliation else []
    )
    existing_states = {
        str(record.get("terminal_reconciliation_state"))
        for record in reconciliations
    }
    floor = max(
        [temporal_instant(fill.get("fill_time"), field_name="fill_time") for fill in item.exit_fills]
    )
    if TerminalReconciliationState.FLAT_AWAITING_RECONCILIATION.value not in existing_states:
        moment = next_execution_moment(
            clock, floor=floor, field_name="reconciled_time", release_safe=False
        )
        _submit_payload(
            client,
            event_type=PAPER_RECONCILIATION_RECORDED,
            payload=_reconciliation_payload(
                item,
                sequence=len(reconciliations),
                terminal=TerminalReconciliationState.FLAT_AWAITING_RECONCILIATION.value,
                moment=moment,
            ),
            what="flat reconciliation",
        )
        return
    sequence = int(reconciliations[-1]["reconciliation_seq"]) + 1 if reconciliations else 1
    moment = next_execution_moment(
        clock, floor=floor, field_name="reconciled_time", release_safe=False
    )
    _submit_payload(
        client,
        event_type=PAPER_RECONCILIATION_RECORDED,
        payload=_reconciliation_payload(
            item,
            sequence=sequence,
            terminal=TerminalReconciliationState.FINAL_VERIFIED.value,
            moment=moment,
        ),
        what="final reconciliation",
    )


def _reconciliation_payload(
    item: PaperV2ProtectionWorkItem,
    *,
    sequence: int,
    terminal: str,
    moment: datetime,
) -> dict[str, Any]:
    """A reconciliation stating exactly the canonical totals already committed."""
    filled_entry = sum(float(fill["quantity"]) for fill in item.entry_fills)
    filled_exit = sum(float(fill["quantity"]) for fill in item.exit_fills)
    gross = sum(
        float(fill["quantity"]) * float(fill["price"])
        * (1.0 if str(fill["side"]) == "SELL" else -1.0)
        for fill in list(item.entry_fills) + list(item.exit_fills)
    )
    costs = sum(
        float(fill["fee_cost"])
        + float(fill["spread_cost"])
        + float(fill["slippage_cost"])
        + float(fill["other_supported_cost"])
        for fill in list(item.entry_fills) + list(item.exit_fills)
    )
    return {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "reconciliation_id": paper_v2_reconciliation_id(
            item.paper_trade_id, reconciliation_seq=sequence
        ),
        "paper_trade_id": item.paper_trade_id,
        "reconciliation_seq": sequence,
        "position_state": "FLAT",
        "terminal_reconciliation_state": terminal,
        "filled_entry_quantity": filled_entry,
        "filled_exit_quantity": filled_exit,
        "remaining_quantity": 0.0,
        "reserved_capital": item.reserved_capital,
        "realized_gross_pnl": gross,
        "recorded_execution_costs": costs,
        "realized_net_pnl": gross - costs,
        "reconciled_time": _exact(moment, "reconciled_time"),
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
    }


def _quote_target(item: PaperV2ProtectionWorkItem) -> _QuoteTarget:
    target = _QuoteTarget(
        native_symbol=str(item.native_symbol or ""),
        instrument_version_id=str(item.instrument_version or ""),
        quote_currency=str(item.quote_currency or ""),
    )
    if not target.native_symbol or not target.instrument_version_id:
        raise PaperV2ProtectionError(
            "committed instrument facts are unavailable for exit evidence"
        )
    return target


def _replacement(
    item: PaperV2ProtectionWorkItem, *, protection_plan: dict[str, Any]
) -> PaperV2ProtectionWorkItem:
    """The item as it will read once ``protection_plan`` is committed."""
    from dataclasses import replace

    return replace(
        item,
        protection_plan=protection_plan,
        protection_state=ProtectionState.PLANNED.value,
        protection_states=[],
        plan_seq=int(protection_plan["plan_seq"]),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _exact(moment: datetime, field_name: str) -> dict[str, str]:
    return {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": iso_z(moment, field_name=field_name),
    }


def _submit_payload(
    client: Any, *, event_type: str, payload: dict[str, Any], what: str
) -> None:
    validated = validate_paper_evidence_payload(event_type, payload)
    submit_canonical_event(
        client,
        event_type=event_type,
        key=paper_evidence_idempotency_key(event_type, validated),
        payload=validated,
        what=what,
    )


def _require_accepted(ack: Any, *, what: str) -> None:
    """Require durable commit proof from the writer.

    The atomic protection action answer uses ``OK``/``DUPLICATE_OK`` (its own ack
    type), while generic event submission uses ``COMMITTED``/``DUPLICATE_OK``. Both
    are accepted, and neither is treated as success unless the store proved it.
    """
    status = str(getattr(ack, "status", "") or "")
    if status not in {"OK", "COMMITTED", "DUPLICATE_OK"}:
        error = getattr(ack, "error_code", None)
        detail = getattr(ack, "detail", None)
        raise PaperV2ProtectionError(
            f"{what} was not committed: {status or 'unknown'}"
            f"{f' ({error})' if error else ''}{f': {detail}' if detail else ''}"
        )
