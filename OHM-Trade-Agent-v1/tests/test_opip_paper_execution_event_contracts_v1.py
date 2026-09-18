from __future__ import annotations

import pytest

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
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


def test_paper_v2_event_contract_is_not_registered_with_writer_yet():
    from app.opip.canonical.writer import ACCEPTED_EVENT_TYPES

    assert PAPER_EXECUTION_EVENT_TYPES.isdisjoint(ACCEPTED_EVENT_TYPES)
