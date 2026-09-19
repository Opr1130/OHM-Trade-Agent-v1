"""B/C-2 Paper v2 protection and terminal reconciliation ground-truth tests.

These exercise the canonical writer boundary only. Paper v2 remains inactive:
there is no scheduler, exchange write authority, cutover, or B/C-3 behavior here.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.events import (
    MARKET_INSTRUMENT_VERSION_RECORDED,
    instrument_version_idempotency_key,
)
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_PROTECTION_STATE_RECORDED,
    PAPER_PROTECTION_TRIGGER_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
    paper_evidence_idempotency_key,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_QUOTE_EVIDENCE_RECORDED,
    PaperAdmissionRequest,
    PaperProtectionActionRequest,
    quote_evidence_idempotency_key,
)
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    context_idempotency_key,
    context_identity,
)
from app.opip.market.instrument_version_store import instrument_version_record_payload

INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
NATIVE_SYMBOL = "SOL/USD"
CANONICAL_VENUE = "KRAKEN"


def _exact(ts: str = "2026-09-18T16:00:00Z") -> dict:
    return {"precision": "EXACT", "basis": "SOURCE_REPORTED", "occurred_at": ts}


def _context_payload(*, suffix: str = "bc2") -> dict:
    payload = {
        "context_id": "pending",
        "candidate_id": f"candidate-{suffix}",
        "episode_id": f"episode-{suffix}",
        "evaluation_id": f"evaluation-{suffix}",
        "instrument_version": INSTRUMENT_VERSION_ID,
        "snapshot_id": f"snapshot-{suffix}",
        "snapshot_hash": f"snapshot-hash-{suffix}",
        "evaluation_time": "2026-09-18T15:59:59Z",
        "evidence_cutoff": "2026-09-18T15:59:59Z",
        "consumed_input_watermark": {"history_epoch": 1, "local_sequence": 1},
        "feature_version": "features-bc2",
        "policy_version": "policy-bc2",
        "detector_version": "detector-bc2",
        "forecast_version": "forecast-bc2",
        "candidate_set_ref": f"candidate-set-{suffix}",
        "portfolio_version_ref": None,
        "environment": "paper",
        "eligibility": True,
        "missingness": {},
        "source_availability_times": {},
        "evidence_eligibility_manifest": {},
        "schema_version": 1,
        "provenance": {
            "producing_component": "bc2-test",
            "artifact_or_build_id": f"build-{suffix}",
            "process_instance_id": f"proc-{suffix}",
            "emitted_at": "2026-09-18T15:59:59Z",
            "source_record_refs": [f"source:{suffix}"],
            "schema_version": 1,
        },
    }
    payload["context_id"] = context_identity(payload)
    return payload


def _submit(writer: CanonicalWriter, event_type: str, payload: dict):
    if event_type == PAPER_QUOTE_EVIDENCE_RECORDED:
        key = quote_evidence_idempotency_key(payload)
    else:
        key = paper_evidence_idempotency_key(event_type, payload)
    return writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=key,
            event_type=event_type,
            payload=payload,
        )
    )


@pytest.fixture
def writer_env(tmp_path):
    """A writer with the canonical instrument identity and one admitted trade."""
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    version = InstrumentVersion(
        venue=CANONICAL_VENUE,
        base_asset="SOL",
        quote_currency="USD",
        venue_instrument_id=NATIVE_SYMBOL,
        version=1,
        reference_data_version="kraken-ref-1",
        observed_at_utc=datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc),
    )
    assert (
        writer.submit(
            WriterIntent(
                schema_version=SCHEMA_VERSION,
                priority="LOW",
                idempotency_key=instrument_version_idempotency_key(
                    instrument_version_id=version.instrument_version_id,
                    reference_fingerprint=version.reference_fingerprint(),
                ),
                event_type=MARKET_INSTRUMENT_VERSION_RECORDED,
                payload=instrument_version_record_payload(version),
            )
        ).status
        == "OK"
    )
    context = _context_payload()
    assert (
        writer.submit(
            WriterIntent(
                schema_version=SCHEMA_VERSION,
                priority="LOW",
                idempotency_key=context_idempotency_key(
                    context_id=context["context_id"]
                ),
                event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                payload=context,
            )
        ).status
        == "OK"
    )
    try:
        yield writer, context
    finally:
        writer.close()


def _admit(writer, context, *, disposition_id, quote_currency="USD"):
    return writer.admit_paper_opportunity(
        PaperAdmissionRequest(
            disposition_id=disposition_id,
            decision_context_id=context["context_id"],
            disposition_seq=0,
            quote_currency=quote_currency,
            requested_capital=500.0,
            disposition_time=_exact(),
            expected_portfolio_version=0,
            capital_policy_version="paper-capital-v1",
            portfolio_equity_limit=10_000.0,
            portfolio_position_limit=3,
            requested_reservation_amount=500.0,
        )
    )


def _quote(context, *, quote_id, venue=CANONICAL_VENUE, symbol=NATIVE_SYMBOL,
           quote_currency="USD", ts="2026-09-18T16:00:00Z"):
    return {
        "schema_version": 1,
        "quote_evidence_id": quote_id,
        "instrument_version": context["instrument_version"],
        "venue": venue,
        "native_symbol": symbol,
        "quote_currency": quote_currency,
        "source_kind": "LEVEL_1_BOOK",
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }


def _leg(
    writer,
    context,
    admission,
    *,
    tag,
    role,
    side,
    quantity,
    price,
    quote_id,
    unit_cost=1.75,
    attempt_seq=0,
    fill_seq=0,
    ts="2026-09-18T16:00:01Z",
):
    """Commit one order intent -> attempt -> fill leg through B/C-1 contracts."""
    order = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "order_intent_id": f"order-{tag}",
        "paper_trade_id": admission.paper_trade_id,
        "decision_context_id": context["context_id"],
        "intent_seq": 0,
        "intent_role": role,
        "side": side,
        "order_type": "MARKET",
        "requested_quantity": quantity,
        "requested_notional": quantity * price,
        "reason_code": "BC2_TEST",
        "intent_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "reservation_id": admission.reservation_id,
    }
    assert _submit(writer, PAPER_ORDER_INTENT_RECORDED, order).status == "OK"

    attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": f"attempt-{tag}",
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": attempt_seq,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": quantity,
        "market_evidence_ref": quote_id,
    }
    assert _submit(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt).status == "OK"

    fill = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": f"fill-{tag}",
        "execution_attempt_id": attempt["execution_attempt_id"],
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "fill_seq": fill_seq,
        "side": side,
        "quantity": quantity,
        "price": price,
        "fee_cost": unit_cost / 4,
        "spread_cost": unit_cost / 4,
        "slippage_cost": unit_cost / 4,
        "other_supported_cost": unit_cost / 4,
        "fill_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": quote_id,
    }
    ack = _submit(writer, PAPER_FILL_RECORDED, fill)
    assert ack.status == "OK", ack.detail
    return order, attempt, fill


def _entry_trade(writer, context, *, tag="entry", quantity=5.0, price=100.0):
    """An admitted trade with canonical positive exposure (entry fill only)."""
    admission = _admit(writer, context, disposition_id=f"disp-{tag}")
    assert admission.disposition == "ADMITTED"
    quote = _quote(context, quote_id=f"quote-{tag}")
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"
    _leg(
        writer,
        context,
        admission,
        tag=tag,
        role="ENTRY",
        side="BUY",
        quantity=quantity,
        price=price,
        quote_id=quote["quote_evidence_id"],
    )
    return admission, quote


def _plan(admission, *, plan_id="plan-a", plan_seq=0, stop_price=90.0, ts="2026-09-18T16:00:02Z"):
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_plan_id": plan_id,
        "paper_trade_id": admission.paper_trade_id,
        "plan_seq": plan_seq,
        "stop_price": stop_price,
        "targets": [{"target_id": "tp1", "price": 120.0, "fraction": 0.5}],
        "max_hold_seconds": 3600,
        "plan_time": _exact(ts),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }


def _state(admission, *, plan_id="plan-a", event_id="pstate-1", seq=0,
           from_state="PLANNED", to_state="ACTIVE", ts="2026-09-18T16:00:03Z"):
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_event_id": event_id,
        "protection_plan_id": plan_id,
        "paper_trade_id": admission.paper_trade_id,
        "state_seq": seq,
        "from_state": from_state,
        "to_state": to_state,
        "reason_code": "BC2_TEST",
        "state_time": _exact(ts),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }


def _trigger(admission, *, plan_id="plan-a", trigger_id="trig-1", seq=0,
             trigger_type="STOP", quote_id=None, ts="2026-09-18T16:00:04Z"):
    payload = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_trigger_id": trigger_id,
        "protection_plan_id": plan_id,
        "paper_trade_id": admission.paper_trade_id,
        "trigger_seq": seq,
        "trigger_type": trigger_type,
        "reference_price": 90.0,
        "trigger_time": _exact(ts),
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }
    if quote_id is not None:
        payload["market_evidence_ref"] = quote_id
    return payload


def _reconciliation(admission, *, recon_id="recon-1", seq=0, terminal, position_state,
                    entry, exit_qty, remaining, gross, costs, ts="2026-09-18T17:00:00Z",
                    reason=None):
    payload = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "reconciliation_id": recon_id,
        "paper_trade_id": admission.paper_trade_id,
        "reconciliation_seq": seq,
        "position_state": position_state,
        "terminal_reconciliation_state": terminal,
        "filled_entry_quantity": entry,
        "filled_exit_quantity": exit_qty,
        "remaining_quantity": remaining,
        "reserved_capital": 500.0,
        "realized_gross_pnl": gross,
        "recorded_execution_costs": costs,
        "realized_net_pnl": gross - costs,
        "reconciled_time": _exact(ts),
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
    }
    if reason is not None:
        payload["unresolved_reason"] = reason
    return payload


def _rows(writer, event_type):
    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY history_epoch, local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


# ---------------------------------------------------------------------------
# 1. Immutable protection plans and monotonic plan_seq
# ---------------------------------------------------------------------------


def test_plan_is_recorded_before_entry_fill_and_remains_history(writer_env):
    """A plan may be recorded before an entry fill and stays immutable evidence."""
    writer, context = writer_env
    admission = _admit(writer, context, disposition_id="disp-plan-early")
    first = _plan(admission, plan_id="plan-a", plan_seq=0)
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, first).status == "OK"

    second = _plan(admission, plan_id="plan-b", plan_seq=1, stop_price=95.0)
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, second).status == "OK"

    stored = {row["protection_plan_id"]: row for row in _rows(writer, PAPER_PROTECTION_PLAN_RECORDED)}
    assert set(stored) == {"plan-a", "plan-b"}
    # The older plan is untouched history, not reinterpreted.
    assert stored["plan-a"] == first
    assert stored["plan-a"]["stop_price"] == 90.0


def test_duplicate_plan_seq_under_a_different_identity_is_rejected(writer_env):
    writer, context = writer_env
    admission = _admit(writer, context, disposition_id="disp-plan-dup")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    dup = _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(admission, plan_id="plan-b", plan_seq=0),
    )
    assert dup.status == "REJECTED"
    assert "already committed" in str(dup.detail)


def test_regressing_plan_seq_is_rejected(writer_env):
    writer, context = writer_env
    admission = _admit(writer, context, disposition_id="disp-plan-regress")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a", plan_seq=5)).status == "OK"
    regress = _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(admission, plan_id="plan-b", plan_seq=2),
    )
    assert regress.status == "REJECTED"
    assert "regresses" in str(regress.detail)


def test_plan_identical_retry_is_duplicate_ok_and_diff_payload_conflicts(writer_env):
    writer, context = writer_env
    admission = _admit(writer, context, disposition_id="disp-plan-retry")
    plan = _plan(admission, plan_id="plan-a")
    first = _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, plan)
    retry = _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, plan)
    assert first.status == "OK"
    assert retry.status == "DUPLICATE_OK"
    assert retry.event_id == first.event_id

    conflict = _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        {**plan, "stop_price": 91.0},
    )
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_plan_requires_admitted_trade_ancestry(writer_env):
    writer, context = writer_env
    admission = _admit(writer, context, disposition_id="disp-plan-anc")
    foreign = {**_plan(admission, plan_id="plan-x"), "paper_trade_id": "PTV2:unknown"}
    ack = _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, foreign)
    assert ack.status == "REJECTED"
    assert "not an admitted" in str(ack.detail)


# ---------------------------------------------------------------------------
# 2. Protection state authority
# ---------------------------------------------------------------------------


def test_activation_rejected_without_positive_canonical_exposure(writer_env):
    """A plan cannot become ACTIVE until fills prove positive exposure."""
    writer, context = writer_env
    admission = _admit(writer, context, disposition_id="disp-state-noexp")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    ack = _submit(writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a"))
    assert ack.status == "REJECTED"
    assert "positive canonical exposure" in str(ack.detail)


def test_activation_accepted_with_canonical_positive_exposure(writer_env):
    writer, context = writer_env
    admission, _quote_payload_ = _entry_trade(writer, context, tag="state-ok")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    ack = _submit(writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a"))
    assert ack.status == "OK", ack.detail
    assert writer._effective_protection_state("plan-a").value == "ACTIVE"  # noqa: SLF001


def test_invalid_state_transition_is_rejected(writer_env):
    """PLANNED -> DEGRADED is not a supported runtime transition."""
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="state-invalid")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    ack = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-a", to_state="DEGRADED"),
    )
    assert ack.status == "REJECTED"
    assert "unsupported protection state transition" in str(ack.detail)


def test_state_from_state_must_match_effective_canonical_state(writer_env):
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="state-from")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    # Plan is still PLANNED, so claiming DEGRADED as the origin is refused.
    ack = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(
            admission,
            plan_id="plan-a",
            event_id="pstate-x",
            from_state="DEGRADED",
            to_state="ACTIVE",
        ),
    )
    assert ack.status == "REJECTED"
    assert "effective canonical state" in str(ack.detail)


def test_state_seq_monotonicity_rejects_duplicate_and_regression(writer_env):
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="state-seq")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a", seq=0)
    ).status == "OK"

    dup = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-a", event_id="pstate-dup", seq=0,
               from_state="ACTIVE", to_state="DEGRADED"),
    )
    assert dup.status == "REJECTED"
    assert "already committed" in str(dup.detail)

    regress = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-a", event_id="pstate-reg", seq=0,
               from_state="ACTIVE", to_state="DEGRADED"),
    )
    assert regress.status == "REJECTED"


def test_triggered_transition_requires_committed_trigger_evidence(writer_env):
    """Defense in depth: the TRIGGERED rule itself still demands a real trigger.

    The generic path refuses TRIGGERED outright, so this asserts the underlying
    validator directly against a plan that has no committed trigger.
    """
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="state-trig")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"

    with pytest.raises(ValueError, match="without committed trigger evidence"):
        writer._validate_protection_state(  # noqa: SLF001
            _state(
                admission,
                plan_id="plan-a",
                event_id="pstate-deg",
                seq=1,
                from_state="ACTIVE",
                to_state="TRIGGERED",
            )
        )
    assert _rows(writer, PAPER_PROTECTION_STATE_RECORDED) == [
        _state(admission, plan_id="plan-a")
    ]


def test_effective_plan_is_the_highest_activated_plan(writer_env):
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="eff-plan")
    for plan_id, plan_seq in (("plan-a", 0), ("plan-b", 1), ("plan-c", 2)):
        assert _submit(
            writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id=plan_id, plan_seq=plan_seq)
        ).status == "OK"
    # Only plan-a and plan-c are activated; plan-c is higher.
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-a", event_id="s-a", seq=0),
    ).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-c", event_id="s-c", seq=0),
    ).status == "OK"

    effective = writer._effective_protection_plan(admission.paper_trade_id)  # noqa: SLF001
    assert effective is not None
    assert effective["protection_plan_id"] == "plan-c"
    assert effective["plan_seq"] == 2
    # plan-b was never activated and cannot be effective.
    assert writer._activated_plan_ids(admission.paper_trade_id) == [  # noqa: SLF001
        (0, "plan-a"),
        (2, "plan-c"),
    ]


# ---------------------------------------------------------------------------
# 3/4. Trigger evidence and trigger-is-not-exit
# ---------------------------------------------------------------------------


def _armed_trade(writer, context, *, tag):
    """An admitted trade with positive canonical exposure and an ACTIVE plan."""
    admission, quote = _entry_trade(writer, context, tag=tag)
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    return admission, quote


def _action_request(
    context,
    admission,
    quote,
    *,
    trigger_id="trig-act",
    trigger_seq=0,
    trigger_type="STOP",
    plan_id="plan-a",
    exit_quantity=5.0,
    exit_price=100.0,
    exit_order_intent_id=None,
    intent_seq=0,
    state_event_id="pstate-act",
    state_seq=1,
    from_state="ACTIVE",
    to_state="TRIGGERED",
    quote_id=None,
    trigger_overrides=None,
    exit_overrides=None,
    state_overrides=None,
    ts="2026-09-18T16:00:04Z",
):
    """Build one complete atomic protection action request.

    Defaults describe a valid STOP action against an armed plan with positive
    canonical exposure, so each test overrides exactly the fact it is about.
    """
    if quote_id is not None:
        evidence_ref = quote_id
    elif trigger_type in {"STOP", "TARGET"}:
        evidence_ref = quote["quote_evidence_id"]
    else:
        evidence_ref = None
    trigger = _trigger(
        admission,
        plan_id=plan_id,
        trigger_id=trigger_id,
        seq=trigger_seq,
        trigger_type=trigger_type,
        quote_id=evidence_ref,
        ts=ts,
    )
    exit_order_intent = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "order_intent_id": exit_order_intent_id or f"exit-{trigger_id}",
        "paper_trade_id": admission.paper_trade_id,
        "decision_context_id": context["context_id"],
        "intent_seq": intent_seq,
        "intent_role": "EXIT",
        "side": "SELL",
        "order_type": "MARKET",
        "requested_quantity": exit_quantity,
        "requested_notional": exit_quantity * exit_price,
        "reason_code": "PROTECTION_ACTION",
        "intent_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "reservation_id": admission.reservation_id,
    }
    protection_state = _state(
        admission,
        plan_id=plan_id,
        event_id=state_event_id,
        seq=state_seq,
        from_state=from_state,
        to_state=to_state,
        ts=ts,
    )
    for overrides, target in (
        (trigger_overrides, trigger),
        (exit_overrides, exit_order_intent),
        (state_overrides, protection_state),
    ):
        if overrides:
            target.update(overrides)
    return PaperProtectionActionRequest(
        trigger=trigger,
        exit_order_intent=exit_order_intent,
        protection_state=protection_state,
    )


def _run_action(writer, context, admission, quote, **kwargs):
    return writer.trigger_paper_protection_action(
        _action_request(context, admission, quote, **kwargs)
    )


def _bundle_rows(writer):
    return (
        _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED),
        _rows(writer, PAPER_ORDER_INTENT_RECORDED),
        _rows(writer, PAPER_PROTECTION_STATE_RECORDED),
    )


def _plant_trigger(writer, trigger_payload):
    """Artificially commit a lone trigger row, to model a contradictory state.

    Used only to reach integrity states a correct writer cannot produce on its
    own, so the fail-closed behaviour of the atomic action can be proven.
    """
    meta = writer._conn.execute(  # noqa: SLF001
        "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
    ).fetchone()
    event_id = writer._insert_event_row_in_transaction(  # noqa: SLF001
        event_type=PAPER_PROTECTION_TRIGGER_RECORDED,
        idempotency_key=paper_evidence_idempotency_key(
            PAPER_PROTECTION_TRIGGER_RECORDED, trigger_payload
        ),
        payload=trigger_payload,
        history_epoch=int(meta["history_epoch"]),
        local_sequence=int(meta["next_local_sequence"]),
        now="2026-09-18T16:00:00Z",
    )
    writer._conn.commit()  # noqa: SLF001
    return event_id


# ---------------------------------------------------------------------------
# 1. Direct path closure: a trigger only commits inside the atomic action
# ---------------------------------------------------------------------------


def test_standalone_trigger_writerintent_is_rejected(writer_env):
    """The generic WriterIntent path must never persist an action trigger."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="closure-trigger")
    payload = _trigger(
        admission, trigger_id="trig-standalone", quote_id=quote["quote_evidence_id"]
    )
    ack = _submit(writer, PAPER_PROTECTION_TRIGGER_RECORDED, payload)
    assert ack.status == "REJECTED"
    assert "ATOMIC_TRIGGER_ACTION_REQUIRED" in str(ack.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


def test_generic_triggered_state_transition_is_rejected(writer_env):
    """A producer must not assert TRIGGERED independently, even with evidence."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="closure-triggered")
    ack = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(
            admission,
            plan_id="plan-a",
            event_id="pstate-generic-triggered",
            seq=1,
            from_state="ACTIVE",
            to_state="TRIGGERED",
        ),
    )
    assert ack.status == "REJECTED"
    assert "ATOMIC_TRIGGER_ACTION_REQUIRED" in str(ack.detail)
    assert _rows(writer, PAPER_PROTECTION_STATE_RECORDED) == [_state(admission, plan_id="plan-a")]


def test_safe_generic_protection_transitions_still_work(writer_env):
    """Closing TRIGGERED must not disable the safe non-trigger transitions."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="closure-safe")
    degraded = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(
            admission,
            plan_id="plan-a",
            event_id="pstate-safe-degraded",
            seq=1,
            from_state="ACTIVE",
            to_state="DEGRADED",
        ),
    )
    assert degraded.status == "OK", degraded.detail
    recovered = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(
            admission,
            plan_id="plan-a",
            event_id="pstate-safe-active",
            seq=2,
            from_state="DEGRADED",
            to_state="ACTIVE",
        ),
    )
    assert recovered.status == "OK", recovered.detail
    assert writer._effective_protection_state("plan-a").value == "ACTIVE"  # noqa: SLF001


# ---------------------------------------------------------------------------
# 2/4. Valid atomic actions
# ---------------------------------------------------------------------------


def test_valid_atomic_stop_commits_trigger_exit_intent_and_state(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-stop")
    ack = _run_action(writer, context, admission, quote)
    assert ack.status == "OK", ack.detail

    triggers, intents, states = _bundle_rows(writer)
    assert len(triggers) == 1
    assert len(intents) == 2  # the entry intent plus the EXIT intent
    assert len(states) == 2  # PLANNED->ACTIVE plus ACTIVE->TRIGGERED
    exit_intent = next(
        row for row in intents if row["order_intent_id"] == "exit-trig-act"
    )
    assert exit_intent["intent_role"] == "EXIT"
    assert exit_intent["side"] == "SELL"
    assert writer._effective_protection_state("plan-a").value == "TRIGGERED"  # noqa: SLF001

    # Both derived facts name the trigger that caused them; the trigger payload is
    # never mutated to carry the exit identity.
    row = writer._conn.execute(  # noqa: SLF001
        "SELECT causation_id FROM events WHERE idempotency_key = ?",
        (paper_evidence_idempotency_key(PAPER_ORDER_INTENT_RECORDED, exit_intent),),
    ).fetchone()
    assert str(row["causation_id"]) == ack.trigger_event_id
    assert "order_intent_id" not in triggers[0]


def test_valid_atomic_time_action_uses_exact_temporal_evidence(writer_env):
    """TIME uses the same single RPC; only the trigger evidence differs."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-time")
    ack = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-time-act",
        trigger_type="TIME",
    )
    assert ack.status == "OK", ack.detail
    triggers, _intents, states = _bundle_rows(writer)
    assert len(triggers) == 1
    assert "market_evidence_ref" not in triggers[0]
    assert len(states) == 2


# ---------------------------------------------------------------------------
# 5/6/7/8. Atomic action rejections
# ---------------------------------------------------------------------------


def test_exit_quantity_beyond_exposure_rolls_back_the_whole_bundle(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-overclose")
    ack = _run_action(writer, context, admission, quote, exit_quantity=6.0)
    assert ack.status == "REJECTED"
    assert "exceeds canonical remaining exposure" in str(ack.detail)
    triggers, intents, states = _bundle_rows(writer)
    assert triggers == []
    assert len(intents) == 1  # only the entry intent
    assert len(states) == 1  # only PLANNED->ACTIVE


@pytest.mark.parametrize("field_name", ["reservation_id", "decision_context_id"])
def test_invalid_exit_ancestry_rolls_back_the_whole_bundle(writer_env, field_name):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag=f"act-anc-{field_name}")
    ack = _run_action(
        writer,
        context,
        admission,
        quote,
        exit_overrides={field_name: "wrong-ancestry"},
    )
    assert ack.status == "REJECTED"
    triggers, _intents, _states = _bundle_rows(writer)
    assert triggers == []


def test_exit_intent_with_wrong_paper_trade_is_rejected(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-anc-trade")
    ack = _run_action(
        writer,
        context,
        admission,
        quote,
        exit_overrides={"paper_trade_id": "PTV2:foreign"},
    )
    assert ack.status == "REJECTED"
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


def test_trigger_against_a_superseded_plan_is_rejected(writer_env):
    """Only the effective (highest activated) plan may trigger an action."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-plan")
    assert _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(admission, plan_id="plan-b", plan_seq=1, stop_price=95.0),
    ).status == "OK"
    assert _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-b", event_id="pstate-b", seq=0),
    ).status == "OK"
    # plan-b is now effective, so plan-a may not fire.
    ack = _run_action(writer, context, admission, quote, plan_id="plan-a")
    assert ack.status == "REJECTED"
    assert "not the effective activated plan" in str(ack.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


def test_trigger_against_an_unarmed_plan_is_rejected(writer_env):
    """A PLANNED plan has no proven exposure and cannot fire an action."""
    writer, context = writer_env
    admission, quote = _entry_trade(writer, context, tag="act-unarmed")
    assert _submit(
        writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")
    ).status == "OK"
    ack = _run_action(writer, context, admission, quote)
    assert ack.status == "REJECTED"
    assert "activated protection plan" in str(ack.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


def test_bad_trigger_evidence_rolls_back_the_whole_bundle(writer_env):
    """Missing, mismatched or future evidence must never fire an action."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-evidence")

    missing = _run_action(writer, context, admission, quote, quote_id="")
    assert missing.status == "REJECTED"
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []

    foreign = _quote(context, quote_id="quote-act-foreign", venue="COINBASE")
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, foreign).status == "OK"
    wrong_venue = _run_action(
        writer, context, admission, quote, quote_id=foreign["quote_evidence_id"]
    )
    assert wrong_venue.status == "REJECTED"
    assert "venue" in str(wrong_venue.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []

    future = _quote(context, quote_id="quote-act-future", ts="2026-09-18T16:00:09Z")
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, future).status == "OK"
    future_ack = _run_action(
        writer, context, admission, quote, quote_id=future["quote_evidence_id"]
    )
    assert future_ack.status == "REJECTED"
    assert "not proven available" in str(future_ack.detail)
    # No EXIT intent and no TRIGGERED state leaked from any rejected attempt.
    _triggers, intents, states = _bundle_rows(writer)
    assert len(intents) == 1
    assert len(states) == 1

    bounded = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-act-bounded",
        trigger_type="TIME",
        trigger_overrides={
            "trigger_time": {
                "precision": "BOUNDED",
                "basis": "MODEL_ASSIGNED",
                "window_start": "2026-09-18T16:00:04Z",
                "window_end": "2026-09-18T16:00:05Z",
            }
        },
    )
    assert bounded.status == "REJECTED"
    assert "exact temporal evidence" in str(bounded.detail)


def test_state_sequence_conflict_rolls_back_the_whole_bundle(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-state-seq")
    ack = _run_action(
        writer, context, admission, quote, state_event_id="pstate-conflict", state_seq=0
    )
    assert ack.status == "REJECTED"
    assert "already committed" in str(ack.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


def test_trigger_sequence_conflict_rolls_back_the_whole_bundle(writer_env):
    """A distinct trigger identity reusing a committed trigger_seq is refused."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-trig-seq")
    request = _action_request(context, admission, quote)
    # A committed trigger already occupies seq 0 for this plan while the plan is
    # still armed, so a new action must not reuse that sequence value.
    _plant_trigger(writer, dict(request.trigger))

    ack = writer.trigger_paper_protection_action(
        _action_request(
            context,
            admission,
            quote,
            trigger_id="trig-act-2",
            trigger_seq=0,
            state_event_id="pstate-act-2",
        )
    )
    assert ack.status == "REJECTED"
    assert "already committed" in str(ack.detail)
    # The planted trigger remains the only trigger, and no EXIT intent or
    # TRIGGERED state leaked from the refused bundle.
    triggers, intents, states = _bundle_rows(writer)
    assert len(triggers) == 1
    assert len(intents) == 1
    assert len(states) == 1


def test_invalid_exit_intent_sequence_rolls_back_the_whole_bundle(writer_env):
    """An intent_seq outside the frozen contract refuses the whole bundle.

    B/C-1's frozen order-intent contract bounds intent_seq only as a
    non-negative integer, so this assertion pins that contract rather than
    inventing a stricter sequencing rule B/C-1 never had.
    """
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-intent-seq")
    ack = _run_action(writer, context, admission, quote, intent_seq=-1)
    assert ack.status == "REJECTED"
    _triggers, intents, _states = _bundle_rows(writer)
    assert len(intents) == 1  # only the entry intent remains
    assert ack.trigger_event_id is None


# ---------------------------------------------------------------------------
# 13/14. Crash safety: rollback is proven at the database level
# ---------------------------------------------------------------------------


def _fail_nth_insert(monkeypatch, writer, n):
    original = writer._insert_event_row_in_transaction  # noqa: SLF001
    calls = {"count": 0}

    def _wrapped(**kwargs):
        calls["count"] += 1
        if calls["count"] == n:
            raise ValueError(f"injected failure at insert {n}")
        return original(**kwargs)

    monkeypatch.setattr(writer, "_insert_event_row_in_transaction", _wrapped)


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_partial_failure_never_leaves_a_partial_bundle(
    writer_env, monkeypatch, fail_at
):
    """Failure after the trigger/EXIT insert must commit nothing at all."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag=f"act-fail-{fail_at}")
    _fail_nth_insert(monkeypatch, writer, fail_at)

    ack = _run_action(writer, context, admission, quote)
    assert ack.status == "REJECTED"
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []
    assert _rows(writer, PAPER_PROTECTION_STATE_RECORDED) == [
        _state(admission, plan_id="plan-a")
    ]


def test_failed_transaction_leaves_the_writer_usable(writer_env, monkeypatch):
    """After a rolled-back bundle the same action can still commit cleanly."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-fail-recover")
    _fail_nth_insert(monkeypatch, writer, 2)
    assert _run_action(writer, context, admission, quote).status == "REJECTED"

    monkeypatch.undo()
    recovered = _run_action(writer, context, admission, quote)
    assert recovered.status == "OK", recovered.detail
    assert len(_rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED)) == 1


# ---------------------------------------------------------------------------
# 15-19. Idempotency of the bundle
# ---------------------------------------------------------------------------


def test_ack_loss_retry_is_idempotent(writer_env):
    """A retry of the identical request is DUPLICATE_OK, never a second action."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-retry")
    request = _action_request(context, admission, quote)
    first = writer.trigger_paper_protection_action(request)
    assert first.status == "OK", first.detail

    retry = writer.trigger_paper_protection_action(request)
    assert retry.status == "DUPLICATE_OK"
    assert retry.trigger_event_id == first.trigger_event_id
    assert retry.exit_order_intent_event_id == first.exit_order_intent_event_id
    assert retry.protection_state_event_id == first.protection_state_event_id

    triggers, intents, states = _bundle_rows(writer)
    assert len(triggers) == 1
    assert len(intents) == 2
    assert len(states) == 2


@pytest.mark.parametrize("member", ["trigger", "exit", "state"])
def test_changed_bundle_member_payload_fails_closed(writer_env, member):
    """Same identities, different payload, must conflict rather than re-commit."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag=f"act-change-{member}")
    request = _action_request(context, admission, quote)
    assert writer.trigger_paper_protection_action(request).status == "OK"

    changed = request.as_dict()
    if member == "trigger":
        changed["trigger"]["reference_price"] = 89.0
    elif member == "exit":
        changed["exit_order_intent"]["requested_quantity"] = 4.0
    else:
        changed["protection_state"]["reason_code"] = "DIFFERENT"
    conflict = writer.trigger_paper_protection_action(
        PaperProtectionActionRequest.from_dict(changed)
    )
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    # Nothing extra was committed, and the original facts are intact.
    triggers, intents, states = _bundle_rows(writer)
    assert len(triggers) == 1
    assert len(intents) == 2
    assert len(states) == 2


def test_partially_committed_bundle_fails_closed_without_repair(writer_env):
    """A pre-existing fragment is an integrity contradiction, not a resumable job."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-partial")
    request = _action_request(context, admission, quote)
    _plant_trigger(writer, dict(request.trigger))

    ack = writer.trigger_paper_protection_action(request)
    assert ack.status == "REJECTED"
    assert ack.error_code == "PROTECTION_ACTION_BUNDLE_INCOMPLETE"
    assert "refusing to reconstruct" in str(ack.detail)
    # The missing EXIT intent and state were NOT manufactured.
    _triggers, intents, states = _bundle_rows(writer)
    assert len(intents) == 1
    assert len(states) == 1


def test_concurrent_sibling_plans_cannot_share_a_sequence(writer_env):
    writer, context = writer_env
    admission = _admit(writer, context, disposition_id="disp-plan-race")
    plans = [
        _plan(admission, plan_id=f"plan-race-{index}", plan_seq=0)
        for index in range(2)
    ]

    def _go(payload):
        return _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_go, plans))

    assert sorted(item.status for item in results) == ["OK", "REJECTED"]
    assert len(_rows(writer, PAPER_PROTECTION_PLAN_RECORDED)) == 1


# ---------------------------------------------------------------------------
# 20-25. Trigger is not exit; residual protection; capacity; restart
# ---------------------------------------------------------------------------


def test_trigger_bundle_is_economically_inert(writer_env):
    """Trigger, EXIT intent and TRIGGERED state change no quantity and no P/L."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-inert")
    before = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    fills_before = _rows(writer, PAPER_FILL_RECORDED)

    assert _run_action(writer, context, admission, quote).status == "OK"

    after = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert after == before
    assert after["remaining_quantity"] == 5.0
    assert _rows(writer, PAPER_FILL_RECORDED) == fills_before
    # No economic exit and no terminal state resulted from protection alone.
    assert _rows(writer, PAPER_RECONCILIATION_RECORDED) == []
    # The reservation is still active: triggering is not a release event.
    _version, reserved, active = writer._portfolio_state("USD")  # noqa: SLF001
    assert (reserved, active) == (500.0, 1)


def test_target_partial_exit_leaves_residual_exposure_protected(writer_env):
    """A partial TARGET action leaves the residual position and its protection."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-target")
    ack = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-target",
        trigger_type="TARGET",
        exit_quantity=2.0,
    )
    assert ack.status == "OK", ack.detail
    # The bundle itself changed no quantity.
    assert writer._canonical_fill_totals(admission.paper_trade_id)["remaining_quantity"] == 5.0  # noqa: SLF001

    # Only a canonical SELL fill reduces exposure.
    _leg(writer, context, admission, tag="act-target-exit", role="EXIT", side="SELL",
         quantity=2.0, price=110.0, quote_id=quote["quote_evidence_id"],
         ts="2026-09-18T16:30:00Z")
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["remaining_quantity"] == 3.0
    # Residual exposure is still protected and still not terminal.
    assert writer._effective_protection_state("plan-a").value == "TRIGGERED"  # noqa: SLF001
    assert writer._effective_protection_plan(admission.paper_trade_id)["protection_plan_id"] == "plan-a"  # noqa: SLF001
    assert _rows(writer, PAPER_RECONCILIATION_RECORDED) == []


def test_stop_full_exit_does_not_auto_verify_reconciliation(writer_env):
    """A full-size STOP request still only reaches reconciliation via its rules."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-stop-full")
    ack = _run_action(writer, context, admission, quote, trigger_id="trig-stop-full")
    assert ack.status == "OK", ack.detail

    _leg(writer, context, admission, tag="act-stop-full-exit", role="EXIT", side="SELL",
         quantity=5.0, price=90.0, quote_id=quote["quote_evidence_id"],
         ts="2026-09-18T16:30:00Z")
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["remaining_quantity"] == 0.0
    # Nothing terminalized on its own.
    assert _rows(writer, PAPER_RECONCILIATION_RECORDED) == []
    _version, reserved, active = writer._portfolio_state("USD")  # noqa: SLF001
    assert (reserved, active) == (500.0, 1)


def test_reservation_releases_only_after_final_verified_following_an_action(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-release")
    assert _run_action(writer, context, admission, quote).status == "OK"
    # Active through trigger and TRIGGERED.
    assert writer._portfolio_state("USD")[1:] == (500.0, 1)  # noqa: SLF001

    _leg(writer, context, admission, tag="act-release-exit", role="EXIT", side="SELL",
         quantity=5.0, price=110.0, quote_id=quote["quote_evidence_id"],
         ts="2026-09-18T16:30:00Z")
    assert writer._portfolio_state("USD")[1:] == (500.0, 1)  # noqa: SLF001

    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    flat = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(admission, recon_id="recon-act-flat", seq=0,
                        terminal="FLAT_AWAITING_RECONCILIATION", position_state="FLAT",
                        entry=totals["entry_quantity"], exit_qty=totals["exit_quantity"],
                        remaining=0.0, gross=totals["gross_pnl"],
                        costs=totals["execution_costs"]),
    )
    assert flat.status == "OK", flat.detail
    # Still reserved through unverified FLAT.
    assert writer._portfolio_state("USD")[1:] == (500.0, 1)  # noqa: SLF001

    verified = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(admission, recon_id="recon-act-final", seq=1,
                        terminal="FINAL_VERIFIED", position_state="FLAT",
                        entry=totals["entry_quantity"], exit_qty=totals["exit_quantity"],
                        remaining=0.0, gross=totals["gross_pnl"],
                        costs=totals["execution_costs"]),
    )
    assert verified.status == "OK", verified.detail
    assert writer._portfolio_state("USD")[1:] == (0.0, 0)  # noqa: SLF001


def test_action_release_stays_quote_currency_separate(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-usd")
    assert _run_action(writer, context, admission, quote).status == "OK"

    # The USD action must not touch the USDT portfolio.
    assert writer._verified_paper_trade_ids("USDT") == set()  # noqa: SLF001
    assert writer._portfolio_state("USDT")[1:] == (0.0, 0)  # noqa: SLF001
    assert writer._portfolio_state("USD")[1:] == (500.0, 1)  # noqa: SLF001


def test_restart_reconstructs_the_committed_action_from_canonical_evidence(
    writer_env, tmp_path
):
    """A reopened writer reproduces the action's authority deterministically."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-restart")
    ack = _run_action(writer, context, admission, quote)
    assert ack.status == "OK", ack.detail

    db_path = tmp_path / "canonical.sqlite3"
    expected_totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    expected_plan = writer._effective_protection_plan(admission.paper_trade_id)  # noqa: SLF001
    writer.close()

    reopened = CanonicalWriter(db_path)
    try:
        assert reopened._effective_protection_state("plan-a").value == "TRIGGERED"  # noqa: SLF001
        assert reopened._canonical_fill_totals(admission.paper_trade_id) == expected_totals  # noqa: SLF001
        assert reopened._effective_protection_plan(admission.paper_trade_id) == expected_plan  # noqa: SLF001
        assert len(_rows(reopened, PAPER_PROTECTION_TRIGGER_RECORDED)) == 1
        assert len(_rows(reopened, PAPER_PROTECTION_STATE_RECORDED)) == 2
        # The bundle is still idempotent across a restart.
        replay = reopened.trigger_paper_protection_action(
            _action_request(context, admission, quote)
        )
        assert replay.status == "DUPLICATE_OK"
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# 5/6/7. Position from fills, terminal reconciliation, capacity release
# ---------------------------------------------------------------------------


def _round_trip(writer, context, *, tag, entry_price=100.0, exit_price=110.0,
                quantity=5.0, exit_quantity=5.0):
    admission = _admit(writer, context, disposition_id=f"disp-{tag}")
    quote = _quote(context, quote_id=f"quote-{tag}")
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"
    _leg(writer, context, admission, tag=f"{tag}-entry", role="ENTRY", side="BUY",
         quantity=quantity, price=entry_price, quote_id=quote["quote_evidence_id"])
    if exit_quantity > 0:
        _leg(writer, context, admission, tag=f"{tag}-exit", role="EXIT", side="SELL",
             quantity=exit_quantity, price=exit_price, quote_id=quote["quote_evidence_id"],
             ts="2026-09-18T16:30:00Z")
    return admission, quote


def test_partial_sell_fills_reduce_remaining_quantity_exactly(writer_env):
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="partial", exit_quantity=2.0)
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["entry_quantity"] == 5.0
    assert totals["exit_quantity"] == 2.0
    assert totals["remaining_quantity"] == 3.0


def test_reconciliation_rejects_quantities_not_reproducible_from_fills(writer_env):
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="recon-badqty")
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(admission, terminal="FINAL_VERIFIED", position_state="FLAT",
                        entry=5.0, exit_qty=4.0, remaining=1.0, gross=50.0, costs=3.5),
    )
    assert ack.status == "REJECTED"


def test_final_verified_accepted_when_fully_reproducible(writer_env):
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="recon-ok")
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            terminal="FINAL_VERIFIED",
            position_state="FLAT",
            entry=totals["entry_quantity"],
            exit_qty=totals["exit_quantity"],
            remaining=totals["remaining_quantity"],
            gross=totals["gross_pnl"],
            costs=totals["execution_costs"],
        ),
    )
    assert ack.status == "OK", ack.detail


def test_final_verified_rejects_economics_that_fills_do_not_support(writer_env):
    """The hard economic gate: unverifiable economics cannot claim FINAL_VERIFIED."""
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="recon-fake")
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            terminal="FINAL_VERIFIED",
            position_state="FLAT",
            entry=totals["entry_quantity"],
            exit_qty=totals["exit_quantity"],
            remaining=totals["remaining_quantity"],
            gross=totals["gross_pnl"] + 500.0,
            costs=totals["execution_costs"],
        ),
    )
    assert ack.status == "REJECTED"
    assert "not reproducible" in str(ack.detail)


def test_unresolved_evidence_is_recordable_and_never_releases_capacity(writer_env):
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="recon-unresolved")
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(admission, terminal="UNRESOLVED_EVIDENCE", position_state="OPEN",
                        entry=0.0, exit_qty=0.0, remaining=0.0, gross=0.0, costs=0.0,
                        reason="fills cannot be reconciled"),
    )
    assert ack.status == "OK", ack.detail
    # Unresolved evidence must not release the reservation.
    assert writer._verified_paper_trade_ids("USD") == set()  # noqa: SLF001
    version, reserved, count = writer._portfolio_state("USD")  # noqa: SLF001
    assert (reserved, count) == (500.0, 1)


def test_reservation_stays_active_through_protection_and_partial_exit(writer_env):
    """Reservation survives plan, activation, atomic trigger and a partial exit."""
    writer, context = writer_env
    admission, quote = _round_trip(writer, context, tag="release-partial", exit_quantity=2.0)
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    # Remaining canonical exposure is 3.0 after the partial exit fill, so the
    # action's exit request must stay inside it.
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-partial",
        exit_quantity=3.0,
    )
    assert action.status == "OK", action.detail
    # Still reserved through plan, activation, atomic action and a partial exit.
    _version, reserved, count = writer._portfolio_state("USD")  # noqa: SLF001
    assert (reserved, count) == (500.0, 1)


def test_reservation_released_only_after_final_verified(writer_env):
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="release-final")
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001

    # Unverified FLAT state must not release capacity.
    flat_unverified = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(admission, recon_id="recon-flat", seq=0,
                        terminal="FLAT_AWAITING_RECONCILIATION", position_state="FLAT",
                        entry=totals["entry_quantity"], exit_qty=totals["exit_quantity"],
                        remaining=0.0, gross=totals["gross_pnl"],
                        costs=totals["execution_costs"]),
    )
    assert flat_unverified.status == "OK", flat_unverified.detail
    _version, reserved, count = writer._portfolio_state("USD")  # noqa: SLF001
    assert (reserved, count) == (500.0, 1)

    verified = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(admission, recon_id="recon-final", seq=1,
                        terminal="FINAL_VERIFIED", position_state="FLAT",
                        entry=totals["entry_quantity"], exit_qty=totals["exit_quantity"],
                        remaining=0.0, gross=totals["gross_pnl"],
                        costs=totals["execution_costs"]),
    )
    assert verified.status == "OK", verified.detail
    version, reserved, count = writer._portfolio_state("USD")  # noqa: SLF001
    # Capacity is released, while the version stays a monotone admission count.
    assert (reserved, count) == (0.0, 0)
    assert version == 1
    assert writer._verified_paper_trade_ids("USD") == {admission.paper_trade_id}  # noqa: SLF001


def test_release_is_quote_currency_scoped(writer_env):
    """USD release must not free USDT capacity without conversion evidence."""
    writer, context = writer_env
    usd, _ = _round_trip(writer, context, tag="release-usd")
    totals = writer._canonical_fill_totals(usd.paper_trade_id)  # noqa: SLF001
    assert _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(usd, recon_id="recon-usd", terminal="FINAL_VERIFIED",
                        position_state="FLAT", entry=totals["entry_quantity"],
                        exit_qty=totals["exit_quantity"], remaining=0.0,
                        gross=totals["gross_pnl"], costs=totals["execution_costs"]),
    ).status == "OK"

    assert writer._verified_paper_trade_ids("USDT") == set()  # noqa: SLF001
    _v, _r, usdt_count = writer._portfolio_state("USDT")  # noqa: SLF001
    assert usdt_count == 0


def test_reconciliation_seq_monotonicity(writer_env):
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="recon-seq")
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    payload = _reconciliation(admission, recon_id="recon-a", seq=0,
                              terminal="FLAT_AWAITING_RECONCILIATION", position_state="FLAT",
                              entry=totals["entry_quantity"], exit_qty=totals["exit_quantity"],
                              remaining=0.0, gross=totals["gross_pnl"],
                              costs=totals["execution_costs"])
    assert _submit(writer, PAPER_RECONCILIATION_RECORDED, payload).status == "OK"
    dup = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        {**payload, "reconciliation_id": "recon-b"},
    )
    assert dup.status == "REJECTED"
    assert "already committed" in str(dup.detail)


def test_restart_reconstructs_protection_authority_from_canonical_evidence(writer_env, tmp_path):
    """A fresh writer on the same store reproduces the same authority state."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="restart")
    assert _run_action(
        writer, context, admission, quote, trigger_id="trig-restart"
    ).status == "OK"
    db_path = tmp_path / "canonical.sqlite3"
    expected_state = writer._effective_protection_state("plan-a")  # noqa: SLF001
    expected_totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    expected_plan = writer._effective_protection_plan(admission.paper_trade_id)  # noqa: SLF001
    writer.close()

    reopened = CanonicalWriter(db_path)
    try:
        assert reopened._effective_protection_state("plan-a") is expected_state  # noqa: SLF001
        assert reopened._canonical_fill_totals(  # noqa: SLF001
            admission.paper_trade_id
        ) == expected_totals
        assert reopened._effective_protection_plan(  # noqa: SLF001
            admission.paper_trade_id
        ) == expected_plan
    finally:
        reopened.close()
