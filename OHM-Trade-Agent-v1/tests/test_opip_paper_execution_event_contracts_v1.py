from __future__ import annotations

import pytest

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
    QualifiedOpportunityDisposition,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_V2_ATOMIC_ONLY_EVENT_TYPES,
    PAPER_QUOTE_EVIDENCE_RECORDED,
)

from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_EXECUTION_EVENT_TYPES,
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_PROTECTION_STATE_RECORDED,
    PAPER_PROTECTION_TRIGGER_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
    event_contract,
    paper_evidence_idempotency_key,
    validate_paper_evidence_payload,
)


def _exact(at: str = "2026-09-18T12:00:00Z") -> dict:
    return {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": at,
    }


def _admission() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "disposition_id": "disp-1",
        "decision_context_id": "ctx-1",
        "disposition_seq": 0,
        "disposition": "ADMITTED",
        "evaluation_population": "QUALIFIED_INTENT",
        "quote_currency": "USD",
        "requested_capital": 1000.0,
        "expected_portfolio_version": 0,
        "capital_policy_version": "paper-capital-v1",
        "portfolio_equity_limit": 10000.0,
        "portfolio_position_limit": 3,
        "requested_reservation_amount": 1004.0,
        "disposition_time": _exact(),
        "reason_code": "QUALIFIED",
        "paper_trade_id": "paper-1",
        "reservation_id": "reserve-1",
    }


def _order_intent() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "order_intent_id": "intent-1",
        "paper_trade_id": "paper-1",
        "decision_context_id": "ctx-1",
        "intent_seq": 0,
        "intent_role": "ENTRY",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quantity": 5.0,
        "requested_notional": 1000.0,
        "reason_code": "ENTER_NOW",
        "intent_time": _exact(),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "reservation_id": "reserve-1",
    }


def _attempt() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-1",
        "order_intent_id": "intent-1",
        "paper_trade_id": "paper-1",
        "attempt_seq": 0,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact(),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 5.0,
        "market_evidence_ref": "quote-1",
    }


def _fill() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": "fill-1",
        "execution_attempt_id": "attempt-1",
        "order_intent_id": "intent-1",
        "paper_trade_id": "paper-1",
        "fill_seq": 0,
        "side": "BUY",
        "quantity": 5.0,
        "price": 200.0,
        "fee_cost": 4.0,
        "spread_cost": 1.0,
        "slippage_cost": 2.0,
        "other_supported_cost": 0.0,
        "fill_time": _exact(),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": "quote-1",
    }


def _plan() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_plan_id": "plan-1",
        "paper_trade_id": "paper-1",
        "plan_seq": 0,
        "stop_price": 180.0,
        "targets": [
            {"target_id": "tp1", "price": 220.0, "fraction": 0.5},
            {"target_id": "tp2", "price": 240.0, "fraction": 0.5},
        ],
        "max_hold_seconds": 86400,
        "plan_time": _exact(),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }


def _protection_state() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_event_id": "protect-event-1",
        "protection_plan_id": "plan-1",
        "paper_trade_id": "paper-1",
        "state_seq": 0,
        "from_state": "PLANNED",
        "to_state": "ACTIVE",
        "reason_code": "ENTRY_FILLED",
        "state_time": _exact(),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }


def _trigger() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_trigger_id": "trigger-1",
        "protection_plan_id": "plan-1",
        "paper_trade_id": "paper-1",
        "trigger_seq": 0,
        "trigger_type": "STOP",
        "reference_price": 180.0,
        "trigger_time": _exact(),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
        "market_evidence_ref": "quote-stop-1",
    }


def _reconciliation() -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "reconciliation_id": "recon-1",
        "paper_trade_id": "paper-1",
        "reconciliation_seq": 0,
        "position_state": "FLAT",
        "terminal_reconciliation_state": "FINAL_VERIFIED",
        "filled_entry_quantity": 5.0,
        "filled_exit_quantity": 5.0,
        "remaining_quantity": 0.0,
        "reserved_capital": 1000.0,
        "realized_gross_pnl": 100.0,
        "recorded_execution_costs": 10.0,
        "realized_net_pnl": 90.0,
        "reconciled_time": _exact("2026-09-18T13:00:00Z"),
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
    }


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (PAPER_OPPORTUNITY_DISPOSITION_RECORDED, _admission()),
        (PAPER_ORDER_INTENT_RECORDED, _order_intent()),
        (PAPER_EXECUTION_ATTEMPT_RECORDED, _attempt()),
        (PAPER_FILL_RECORDED, _fill()),
        (PAPER_PROTECTION_PLAN_RECORDED, _plan()),
        (PAPER_PROTECTION_STATE_RECORDED, _protection_state()),
        (PAPER_PROTECTION_TRIGGER_RECORDED, _trigger()),
        (PAPER_RECONCILIATION_RECORDED, _reconciliation()),
    ],
)
def test_all_frozen_event_skeletons_validate(event_type, payload):
    normalized = validate_paper_evidence_payload(event_type, payload)
    assert normalized == payload


def test_event_types_are_closed_and_have_unique_identity_fields():
    assert len(PAPER_EXECUTION_EVENT_TYPES) == 8
    identity_fields = {
        event_contract(event_type).identity_field
        for event_type in PAPER_EXECUTION_EVENT_TYPES
    }
    assert len(identity_fields) == len(PAPER_EXECUTION_EVENT_TYPES)


def test_event_type_must_be_canonical_exact_string():
    with pytest.raises(ValueError, match="canonical string"):
        event_contract(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported"):
        event_contract(f" {PAPER_FILL_RECORDED}")


def test_idempotency_uses_preassigned_immutable_event_identity():
    payload = _fill()
    assert paper_evidence_idempotency_key(PAPER_FILL_RECORDED, payload) == (
        "paper_execution.fill.recorded:fill-1"
    )


def test_payload_schema_is_closed_against_unreviewed_fields():
    payload = _fill()
    payload["magic_profit"] = 999.0
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)


def test_temporal_payload_must_be_canonical_not_fake_exact():
    payload = _fill()
    payload["fill_time"] = {
        "precision": "BOUNDED",
        "basis": "MODEL_ASSIGNED",
        "window_start": "2026-09-18T12:00:00Z",
        "window_end": "2026-09-18T12:15:00Z",
    }
    assert validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)

    payload["fill_time"] = {
        "precision": "EXACT",
        "basis": "MODEL_ASSIGNED",
        "occurred_at": "2026-09-18T12:00:00Z",
        "window_start": "2026-09-18T12:00:00Z",
    }
    with pytest.raises(ValueError):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)


def test_admitted_opportunity_requires_trade_and_reservation_identity():
    payload = _admission()
    del payload["reservation_id"]
    with pytest.raises(ValueError, match="reservation_id"):
        validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload,
        )


def test_admitted_disposition_requires_expected_portfolio_version():
    payload = _admission()
    del payload["expected_portfolio_version"]
    with pytest.raises(ValueError, match="expected_portfolio_version"):
        validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload,
        )


def test_admitted_reservation_cannot_underfund_requested_capital():
    payload = _admission()
    payload["requested_reservation_amount"] = 999.0
    with pytest.raises(ValueError, match="requested_reservation_amount"):
        validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload,
        )


def test_capital_rejection_records_policy_context_but_reserves_nothing():
    payload = _admission()
    payload["disposition"] = "CAPITAL_REJECTED"
    payload.pop("reservation_id")
    payload.pop("paper_trade_id")
    assert validate_paper_evidence_payload(
        PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
        payload,
    )


def test_non_capital_disposition_cannot_smuggle_reservation_policy():
    payload = _admission()
    payload["disposition"] = "NOT_ACTIONABLE"
    with pytest.raises(ValueError, match="reservation-policy"):
        validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload,
        )


#: The reservation-policy fields a non-capital disposition must never carry.
_RESERVATION_POLICY_FIELDS = (
    "paper_trade_id",
    "reservation_id",
    "expected_portfolio_version",
    "capital_policy_version",
    "portfolio_equity_limit",
    "portfolio_position_limit",
    "requested_reservation_amount",
)

_NEW_DISPOSITIONS = (
    QualifiedOpportunityDisposition.EVIDENCE_INCOMPLETE.value,
    QualifiedOpportunityDisposition.DISABLED.value,
)


def _non_capital_disposition(disposition: str) -> dict:
    """A valid non-capital disposition payload for the given disposition.

    Non-capital dispositions may not carry reservation-policy fields, so they are
    removed from the ADMITTED-shaped fixture while keeping every required field.
    """
    payload = _admission()
    payload["disposition"] = disposition
    for field_name in _RESERVATION_POLICY_FIELDS:
        payload.pop(field_name, None)
    return payload


@pytest.mark.parametrize("disposition", _NEW_DISPOSITIONS)
def test_new_dispositions_are_accepted_as_qualified_intent_non_capital(disposition):
    """EVIDENCE_INCOMPLETE and DISABLED are real dispositions, not UNRESOLVED."""
    payload = _non_capital_disposition(disposition)
    assert payload["evaluation_population"] == "QUALIFIED_INTENT"

    normalized = validate_paper_evidence_payload(
        PAPER_OPPORTUNITY_DISPOSITION_RECORDED, payload
    )
    assert normalized["disposition"] == disposition
    # Non-capital: neither may claim a reservation or a trade it never obtained.
    assert set(_RESERVATION_POLICY_FIELDS) & set(normalized) == set()
    # And neither collapses onto the catch-all.
    assert normalized["disposition"] != "UNRESOLVED"


@pytest.mark.parametrize("field_name", _RESERVATION_POLICY_FIELDS)
def test_new_dispositions_cannot_carry_reservation_policy_fields(field_name):
    """Smuggling any reservation-policy field onto either new reason is rejected."""
    for disposition in _NEW_DISPOSITIONS:
        payload = _non_capital_disposition(disposition)
        payload[field_name] = _admission()[field_name]
        with pytest.raises(ValueError, match="reservation-policy"):
            validate_paper_evidence_payload(
                PAPER_OPPORTUNITY_DISPOSITION_RECORDED, payload
            )


def test_new_dispositions_are_not_capital_decisions():
    """A capital decision requires the full reservation-policy context.

    Neither new reason carries it, so omitting those fields must NOT be reported as
    a missing-capital-context error: they are genuinely non-capital dispositions.
    """
    for disposition in _NEW_DISPOSITIONS:
        payload = _non_capital_disposition(disposition)
        for field_name in (
            "expected_portfolio_version",
            "capital_policy_version",
            "portfolio_equity_limit",
            "portfolio_position_limit",
            "requested_reservation_amount",
        ):
            assert field_name not in payload
        assert validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED, payload
        )


# ---------------------------------------------------------------------------
# Greptile P1: ADMITTED trade/reservation identity follows the canonical contract
# ---------------------------------------------------------------------------


def test_admitted_disposition_accepts_canonical_trade_and_reservation_ids():
    payload = _admission()
    normalized = validate_paper_evidence_payload(
        PAPER_OPPORTUNITY_DISPOSITION_RECORDED, payload
    )
    assert normalized["paper_trade_id"] == "paper-1"
    assert normalized["reservation_id"] == "reserve-1"


@pytest.mark.parametrize(
    "padded",
    [" paper-1", "paper-1 ", "  paper-1  ", "\tpaper-1", "paper-1\n"],
)
def test_admitted_disposition_rejects_padded_paper_trade_id(padded):
    """A padded admission id would not match the canonical ref an intent declares."""
    payload = _admission()
    payload["paper_trade_id"] = padded
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_OPPORTUNITY_DISPOSITION_RECORDED, payload)


@pytest.mark.parametrize(
    "padded",
    [" reserve-1", "reserve-1 ", "  reserve-1  ", "\treserve-1", "reserve-1\n"],
)
def test_admitted_disposition_rejects_padded_reservation_id(padded):
    payload = _admission()
    payload["reservation_id"] = padded
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_OPPORTUNITY_DISPOSITION_RECORDED, payload)


def test_admitted_disposition_rejects_whitespace_only_trade_and_reservation_ids():
    for field_name in ("paper_trade_id", "reservation_id"):
        payload = _admission()
        payload[field_name] = "   "
        with pytest.raises(ValueError, match=field_name):
            validate_paper_evidence_payload(
                PAPER_OPPORTUNITY_DISPOSITION_RECORDED, payload
            )


def test_admitted_ids_and_intent_ancestry_share_one_canonical_rule():
    """The admission id and the intent parent ref cannot disagree by whitespace.

    The intent declares `reservation_id` as ancestry, so both the admission that
    creates it and the intent that references it must accept exactly the same
    canonical form.
    """
    admission = _admission()
    intent = _order_intent()
    assert admission["reservation_id"] == intent["reservation_id"] == "reserve-1"

    padded = _admission()
    padded["reservation_id"] = " reserve-1 "
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_OPPORTUNITY_DISPOSITION_RECORDED, padded)

    padded_intent = _order_intent()
    padded_intent["reservation_id"] = " reserve-1 "
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, padded_intent)


def test_counterfactual_population_cannot_enter_qualified_intent_stream():
    payload = _admission()
    payload["evaluation_population"] = "COUNTERFACTUAL_POLICY"
    with pytest.raises(ValueError, match="QUALIFIED_INTENT"):
        validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload,
        )


def test_actual_realized_population_cannot_enter_qualified_intent_stream():
    payload = _admission()
    payload["evaluation_population"] = "ACTUAL_REALIZED"
    with pytest.raises(ValueError, match="QUALIFIED_INTENT"):
        validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload,
        )


def test_opportunity_disposition_rejects_unsupported_quote_currency():
    payload = _admission()
    payload["quote_currency"] = "EUR"
    with pytest.raises(ValueError, match="quote_currency"):
        validate_paper_evidence_payload(
            PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload,
        )


def test_order_intent_requires_admission_reservation_identity():
    payload = _order_intent()
    del payload["reservation_id"]
    with pytest.raises(ValueError, match="reservation_id"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


def test_market_order_cannot_smuggle_limit_price():
    payload = _order_intent()
    payload["limit_price"] = 199.0
    with pytest.raises(ValueError, match="MARKET"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


def test_order_intent_requires_positive_requested_quantity_and_limit_price():
    payload = _order_intent()
    payload["requested_quantity"] = 0.0
    with pytest.raises(ValueError, match="requested_quantity"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)

    payload = _order_intent()
    payload["order_type"] = "LIMIT"
    payload["limit_price"] = 0.0
    with pytest.raises(ValueError, match="limit_price"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


def test_rejected_attempt_requires_reason():
    payload = _attempt()
    payload["execution_state"] = "REJECTED"
    payload.pop("accepted_quantity")
    with pytest.raises(ValueError, match="rejection_reason"):
        validate_paper_evidence_payload(
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            payload,
        )


def test_fill_requires_positive_quantity_and_price():
    payload = _fill()
    payload["quantity"] = 0.0
    with pytest.raises(ValueError, match="positive"):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)


def test_protection_plan_requires_structured_targets():
    payload = _plan()
    payload["targets"] = [220.0]
    with pytest.raises(ValueError, match="object"):
        validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)

    payload = _plan()
    payload["targets"] = [{"target_id": "tp1", "price": 220.0}]
    with pytest.raises(ValueError, match="target_id, price, and fraction"):
        validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


def test_protection_trigger_contains_no_exit_claim():
    contract = event_contract(PAPER_PROTECTION_TRIGGER_RECORDED)
    assert "exit_price" not in contract.allowed_fields
    assert "exit_fill_id" not in contract.allowed_fields
    assert "order_intent_id" not in contract.allowed_fields


def test_final_reconciliation_requires_flat_zero_remaining_and_exact_economics():
    payload = _reconciliation()
    payload["filled_exit_quantity"] = 4.0
    payload["remaining_quantity"] = 1.0
    with pytest.raises(ValueError, match="FINAL_VERIFIED"):
        validate_paper_evidence_payload(PAPER_RECONCILIATION_RECORDED, payload)

    payload = _reconciliation()
    payload["realized_net_pnl"] = 91.0
    with pytest.raises(ValueError, match="realized_net_pnl"):
        validate_paper_evidence_payload(PAPER_RECONCILIATION_RECORDED, payload)


def test_reconciliation_enforces_quantity_conservation():
    payload = _reconciliation()
    payload["filled_exit_quantity"] = 4.0
    with pytest.raises(ValueError, match="filled_entry_quantity"):
        validate_paper_evidence_payload(PAPER_RECONCILIATION_RECORDED, payload)


def test_flat_awaiting_reconciliation_requires_flat_zero_remaining():
    payload = _reconciliation()
    payload["terminal_reconciliation_state"] = "FLAT_AWAITING_RECONCILIATION"
    payload["position_state"] = "OPEN"
    payload["filled_exit_quantity"] = 4.0
    payload["remaining_quantity"] = 1.0
    with pytest.raises(ValueError, match="FLAT_AWAITING_RECONCILIATION"):
        validate_paper_evidence_payload(PAPER_RECONCILIATION_RECORDED, payload)

    payload["position_state"] = "FLAT"
    payload["filled_exit_quantity"] = 5.0
    payload["remaining_quantity"] = 0.0
    assert validate_paper_evidence_payload(PAPER_RECONCILIATION_RECORDED, payload)


def test_unresolved_reconciliation_requires_explicit_reason():
    payload = _reconciliation()
    payload["position_state"] = "OPEN"
    payload["terminal_reconciliation_state"] = "UNRESOLVED_EVIDENCE"
    payload["filled_exit_quantity"] = 4.0
    payload["remaining_quantity"] = 1.0
    with pytest.raises(ValueError, match="unresolved_reason"):
        validate_paper_evidence_payload(PAPER_RECONCILIATION_RECORDED, payload)


def test_paper_v2_writer_registration_boundary_after_bc2():
    from app.opip.canonical.writer import ACCEPTED_EVENT_TYPES

    # B/C-1 evidence families remain registered.
    assert {
        PAPER_QUOTE_EVIDENCE_RECORDED,
        PAPER_ORDER_INTENT_RECORDED,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        PAPER_FILL_RECORDED,
    } <= ACCEPTED_EVENT_TYPES

    # Qualified disposition remains writer-owned through the atomic admission
    # RPC, so a generic WriterIntent cannot bypass version/capital reservation.
    # This is unchanged by B/C-2.
    assert PAPER_OPPORTUNITY_DISPOSITION_RECORDED not in ACCEPTED_EVENT_TYPES

    # B/C-2 arrived: plan and state and terminal reconciliation are registered, so
    # their runtime authority is the canonical writer. The protection TRIGGER is
    # deliberately NOT registered: it is writer-owned through the atomic
    # protection action RPC, so a standalone trigger can never be submitted. This
    # phase-boundary assertion is updated rather than dropped so the registration
    # set stays explicitly pinned.
    assert {
        PAPER_PROTECTION_PLAN_RECORDED,
        PAPER_PROTECTION_STATE_RECORDED,
        PAPER_RECONCILIATION_RECORDED,
    } <= ACCEPTED_EVENT_TYPES
    assert PAPER_PROTECTION_TRIGGER_RECORDED not in ACCEPTED_EVENT_TYPES
    assert PAPER_PROTECTION_TRIGGER_RECORDED in PAPER_V2_ATOMIC_ONLY_EVENT_TYPES

    registered_frozen_events = PAPER_EXECUTION_EVENT_TYPES & ACCEPTED_EVENT_TYPES
    assert registered_frozen_events == {
        PAPER_ORDER_INTENT_RECORDED,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        PAPER_FILL_RECORDED,
        PAPER_PROTECTION_PLAN_RECORDED,
        PAPER_PROTECTION_STATE_RECORDED,
        PAPER_RECONCILIATION_RECORDED,
    }


# ---------------------------------------------------------------------------
# Greptile P1-1: identity/reference canonical form (validation == idempotency)
# ---------------------------------------------------------------------------

_SKELETON_FACTORIES = (
    (PAPER_OPPORTUNITY_DISPOSITION_RECORDED, _admission),
    (PAPER_ORDER_INTENT_RECORDED, _order_intent),
    (PAPER_EXECUTION_ATTEMPT_RECORDED, _attempt),
    (PAPER_FILL_RECORDED, _fill),
    (PAPER_PROTECTION_PLAN_RECORDED, _plan),
    (PAPER_PROTECTION_STATE_RECORDED, _protection_state),
    (PAPER_PROTECTION_TRIGGER_RECORDED, _trigger),
    (PAPER_RECONCILIATION_RECORDED, _reconciliation),
)


@pytest.mark.parametrize(
    ("event_type", "payload_factory"),
    _SKELETON_FACTORIES,
)
def test_every_event_rejects_a_padded_identity(event_type, payload_factory):
    """Identity canonicalization applies to every event, not only fills."""
    contract = event_contract(event_type)
    payload = payload_factory()
    payload[contract.identity_field] = f" {payload[contract.identity_field]} "
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(event_type, payload)


@pytest.mark.parametrize(
    ("event_type", "payload_factory"),
    _SKELETON_FACTORIES,
)
def test_every_event_rejects_a_padded_parent_reference(event_type, payload_factory):
    """Declared ancestry must also be canonical, or lineage keys could collide."""
    contract = event_contract(event_type)
    parent_ref = contract.parent_refs[0]
    payload = payload_factory()
    payload[parent_ref] = f" {payload[parent_ref]} "
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(event_type, payload)


@pytest.mark.parametrize("padded", [" fill-1", "fill-1 ", "\tfill-1", "fill-1\n"])
def test_padded_fill_identity_is_rejected_not_silently_trimmed(padded):
    payload = _fill()
    payload["fill_id"] = padded
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)


def test_whitespace_only_identity_is_rejected():
    payload = _fill()
    payload["fill_id"] = "   "
    with pytest.raises(ValueError, match="fill_id"):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)


def test_missing_identity_is_rejected():
    payload = _fill()
    del payload["fill_id"]
    with pytest.raises(ValueError, match="fill_id"):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)


def test_canonical_identity_passes_and_keys_exactly():
    payload = _fill()
    normalized = validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)
    assert normalized["fill_id"] == "fill-1"
    assert paper_evidence_idempotency_key(PAPER_FILL_RECORDED, normalized) == (
        "paper_execution.fill.recorded:fill-1"
    )


def test_padded_and_canonical_payloads_cannot_collapse_to_one_identity():
    """The defect: both validated, and both keyed to the same identity.

    Before the fix, `"fill-1"` and `" fill-1 "` were distinct payload identities
    that produced one idempotency key. Now the padded form cannot validate at all,
    so it can never reach the key function and collapse onto the canonical value.
    """
    canonical = _fill()
    padded = _fill()
    padded["fill_id"] = " fill-1 "

    assert paper_evidence_idempotency_key(
        PAPER_FILL_RECORDED, canonical
    ) == "paper_execution.fill.recorded:fill-1"

    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, padded)
    # The key function enforces the identical contract, so there is no second,
    # more permissive path into the same identity.
    with pytest.raises(ValueError, match="whitespace"):
        paper_evidence_idempotency_key(PAPER_FILL_RECORDED, padded)


def test_padded_market_evidence_reference_is_rejected():
    """Reference fields other than identity/ancestry follow the same rule."""
    payload = _fill()
    payload["market_evidence_ref"] = " quote-1 "
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_FILL_RECORDED, payload)


def test_padded_protection_target_identity_is_rejected():
    payload = _plan()
    payload["targets"][0]["target_id"] = " tp1 "
    with pytest.raises(ValueError, match="whitespace"):
        validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


# ---------------------------------------------------------------------------
# Greptile P1-2: the admitting reservation must be declared ancestry
# ---------------------------------------------------------------------------


def test_order_intent_declares_reservation_as_ancestry():
    """`reservation_id` is lineage, not a free-floating attribute.

    Previously it was required but absent from `parent_refs`, so an intent could
    name any reservation with no resolvable link to the admission that created it.
    """
    contract = event_contract(PAPER_ORDER_INTENT_RECORDED)
    assert "reservation_id" in contract.required_fields
    assert "reservation_id" in contract.parent_refs
    # The admitting artifact also carries the reservation, so the pair is resolvable
    # (on the disposition it is conditional: only an ADMITTED disposition reserves).
    assert "reservation_id" in event_contract(
        PAPER_OPPORTUNITY_DISPOSITION_RECORDED
    ).allowed_fields


def test_order_intent_without_reservation_ancestry_is_rejected():
    payload = _order_intent()
    del payload["reservation_id"]
    with pytest.raises(ValueError, match="reservation_id"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


def test_order_intent_with_blank_reservation_ancestry_is_rejected():
    payload = _order_intent()
    payload["reservation_id"] = "   "
    with pytest.raises(ValueError, match="reservation_id"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


def test_reservation_mismatch_is_not_decidable_at_contract_level():
    """Records the honest limit of this layer rather than inventing a lookup.

    Contract validation sees exactly one payload, so it can require that a
    reservation is *declared* ancestry but cannot compare it to the admitting
    disposition's reservation. Enforcing the match belongs to the canonical writer
    in B/C-1; there is not enough information here to reject a mismatch
    deterministically, and no runtime lookup is fabricated to pretend otherwise.
    """
    admission = _admission()
    intent = _order_intent()
    intent["reservation_id"] = "reserve-999"

    assert admission["reservation_id"] == "reserve-1"
    assert intent["reservation_id"] != admission["reservation_id"]
    # Still contract-valid: ancestry is declared; the cross-artifact comparison is
    # explicitly out of scope for single-payload validation.
    assert validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, intent)


# ---------------------------------------------------------------------------
# Greptile P1-3: executable intents require positive notional
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("notional", [0.0, 0, -1.0, -1000.0])
def test_order_intent_requires_positive_requested_notional(notional):
    payload = _order_intent()
    payload["requested_notional"] = notional
    with pytest.raises(ValueError, match="requested_notional"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


def test_market_intent_with_positive_size_still_validates_unchanged():
    """MARKET behaviour is untouched: positive size and no limit_price."""
    payload = _order_intent()
    assert payload["order_type"] == "MARKET"
    assert "limit_price" not in payload
    assert validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


def test_limit_intent_requires_positive_notional_and_positive_limit_price():
    payload = _order_intent()
    payload["order_type"] = "LIMIT"
    payload["limit_price"] = 199.0
    assert validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)

    payload["requested_notional"] = 0.0
    with pytest.raises(ValueError, match="requested_notional"):
        validate_paper_evidence_payload(PAPER_ORDER_INTENT_RECORDED, payload)


# ---------------------------------------------------------------------------
# Greptile P1-5: aggregate target allocation cannot exceed the position
# ---------------------------------------------------------------------------


def test_protection_target_allocation_below_full_position_is_legal():
    """A residual is legal: another exit mechanism may own the remainder."""
    payload = _plan()
    payload["targets"] = [
        {"target_id": "tp1", "price": 220.0, "fraction": 0.3},
        {"target_id": "tp2", "price": 240.0, "fraction": 0.4},
    ]
    assert validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


def test_protection_target_allocation_exactly_full_position_is_legal():
    payload = _plan()  # 0.5 + 0.5
    fractions = [target["fraction"] for target in payload["targets"]]
    assert sum(fractions) == 1.0
    assert validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


@pytest.mark.parametrize(
    "fractions",
    [
        (0.6, 0.6),
        (1.0, 0.5),
        (0.5, 0.5, 0.5),
    ],
)
def test_protection_target_allocation_cannot_exceed_the_position(fractions):
    payload = _plan()
    payload["targets"] = [
        {"target_id": f"tp{index}", "price": 220.0 + index, "fraction": fraction}
        for index, fraction in enumerate(fractions)
    ]
    with pytest.raises(ValueError, match="sum"):
        validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


def test_protection_target_allocation_tolerance_scale_is_tight():
    """Pins the tolerance's scale: sub-nanoscale excess is absorbed, real excess is not.

    No ordinary decimal fraction grid sums to just above 1.0 in binary floating
    point, so the sub-tolerance case is constructed explicitly. This bounds what
    the tolerance means - it absorbs representation-scale error only, while the
    0.6 + 0.6 case above is still rejected.
    """
    payload = _plan()
    payload["targets"] = [
        {"target_id": "tp1", "price": 220.0, "fraction": 0.9999999999},
        {"target_id": "tp2", "price": 240.0, "fraction": 0.0000000002},
    ]
    total = sum(target["fraction"] for target in payload["targets"])
    assert total > 1.0
    assert total - 1.0 < 1e-9
    assert validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


# ---------------------------------------------------------------------------
# Greptile P1-6: a declared stop price must be a usable price
# ---------------------------------------------------------------------------


def test_protection_plan_accepts_positive_stop_price():
    payload = _plan()
    payload["stop_price"] = 180.0
    assert validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


@pytest.mark.parametrize("stop_price", [0, 0.0, -1.0, -180.0])
def test_protection_plan_rejects_non_positive_stop_price(stop_price):
    payload = _plan()
    payload["stop_price"] = stop_price
    with pytest.raises(ValueError, match="stop_price"):
        validate_paper_evidence_payload(PAPER_PROTECTION_PLAN_RECORDED, payload)


# ---------------------------------------------------------------------------
# Architecture consistency: frozen lineage stays resolvable and trigger != exit
# ---------------------------------------------------------------------------


def test_frozen_lineage_is_resolvable_through_declared_parent_refs():
    """admission(reservation) → intent → attempt → fill → reconciliation.

    Every artifact declares ancestry so none is orphaned, and the protection
    trigger deliberately carries no exit reference: a trigger is not an exit, and
    the later exit intent/fill remains a separate, explicitly linked artifact.
    """
    for event_type in PAPER_EXECUTION_EVENT_TYPES:
        assert event_contract(event_type).parent_refs

    admission = event_contract(PAPER_OPPORTUNITY_DISPOSITION_RECORDED)
    intent = event_contract(PAPER_ORDER_INTENT_RECORDED)
    attempt = event_contract(PAPER_EXECUTION_ATTEMPT_RECORDED)
    fill = event_contract(PAPER_FILL_RECORDED)
    reconciliation = event_contract(PAPER_RECONCILIATION_RECORDED)
    plan = event_contract(PAPER_PROTECTION_PLAN_RECORDED)
    state = event_contract(PAPER_PROTECTION_STATE_RECORDED)
    trigger = event_contract(PAPER_PROTECTION_TRIGGER_RECORDED)

    # Reservation ancestry: admission carries it, intent declares it.
    assert "reservation_id" in admission.allowed_fields
    assert "reservation_id" in intent.parent_refs
    # The execution chain is walkable through declared identity fields.
    assert intent.identity_field in attempt.parent_refs
    assert attempt.identity_field in fill.parent_refs
    assert "paper_trade_id" in reconciliation.parent_refs
    # Protection chain: plan → state → trigger, with trigger never an exit.
    assert plan.identity_field in state.parent_refs
    assert plan.identity_field in trigger.parent_refs
    assert "order_intent_id" not in trigger.allowed_fields
    assert "exit_fill_id" not in trigger.allowed_fields
