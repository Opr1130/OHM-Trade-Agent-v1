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


def _admit(writer, context, *, disposition_id, quote_currency="USD", expected_version=0):
    return writer.admit_paper_opportunity(
        PaperAdmissionRequest(
            disposition_id=disposition_id,
            decision_context_id=context["context_id"],
            disposition_seq=0,
            quote_currency=quote_currency,
            requested_capital=500.0,
            disposition_time=_exact(),
            expected_portfolio_version=expected_version,
            capital_policy_version="paper-capital-v1",
            portfolio_equity_limit=10_000.0,
            portfolio_position_limit=3,
            requested_reservation_amount=500.0,
        )
    )


def _quote(context, *, quote_id, venue=CANONICAL_VENUE, symbol=NATIVE_SYMBOL,
           quote_currency="USD", ts="2026-09-18T16:00:00Z", price=None):
    """Level-1 quote evidence. ``price`` sets the executable best bid.

    A long-only trade exits by selling, so the best bid is the price that matters
    for proving a protection threshold was crossed. Tests that need a crossed
    level pass an explicit ``price``.
    """
    bid = 99.9 if price is None else float(price)
    return {
        "schema_version": 1,
        "quote_evidence_id": quote_id,
        "instrument_version": context["instrument_version"],
        "venue": venue,
        "native_symbol": symbol,
        "quote_currency": quote_currency,
        "source_kind": "LEVEL_1_BOOK",
        "best_bid": bid,
        "best_ask": bid + 0.2,
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


def _plan(admission, *, plan_id="plan-a", plan_seq=0, stop_price=90.0,
          target_price=120.0, targets=None, max_hold_seconds=3600,
          ts="2026-09-18T16:00:02Z"):
    canonical_targets = (
        [dict(target) for target in targets]
        if targets is not None
        else [{"target_id": "tp1", "price": target_price, "fraction": 0.5}]
    )
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_plan_id": plan_id,
        "paper_trade_id": admission.paper_trade_id,
        "plan_seq": plan_seq,
        "stop_price": stop_price,
        "targets": canonical_targets,
        "max_hold_seconds": max_hold_seconds,
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
             trigger_type="STOP", quote_id=None, ts="2026-09-18T16:00:04Z",
             reference_price=90.0):
    payload = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "protection_trigger_id": trigger_id,
        "protection_plan_id": plan_id,
        "paper_trade_id": admission.paper_trade_id,
        "trigger_seq": seq,
        "trigger_type": trigger_type,
        "reference_price": reference_price,
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

    # Hoisted so only the validator itself can raise inside pytest.raises.
    payload = _state(
        admission,
        plan_id="plan-a",
        event_id="pstate-deg",
        seq=1,
        from_state="ACTIVE",
        to_state="TRIGGERED",
    )
    with pytest.raises(ValueError, match="without committed trigger evidence"):
        writer._validate_protection_state(payload)  # noqa: SLF001
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


def _fill_existing_order(
    writer,
    context,
    admission,
    *,
    order_id,
    tag,
    quantity,
    price,
    quote_id,
    ts="2026-09-18T16:30:00Z",
    attempt_seq=0,
    fill_seq=0,
):
    """Commit an attempt and fill against an already committed order intent.

    Used where the EXIT order was created by the atomic protection action, so the
    fill consumes that order's already-committed capacity instead of competing
    with it as a second, separate exit order.
    """
    attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": f"attempt-{tag}",
        "order_intent_id": order_id,
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": attempt_seq,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": quantity,
        "market_evidence_ref": quote_id,
    }
    assert _submit(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt).status == "OK", (
        "attempt against existing order intent should commit"
    )
    fill = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": f"fill-{tag}",
        "execution_attempt_id": attempt["execution_attempt_id"],
        "order_intent_id": order_id,
        "paper_trade_id": admission.paper_trade_id,
        "fill_seq": fill_seq,
        "side": "SELL",
        "quantity": quantity,
        "price": price,
        "fee_cost": 0.25,
        "spread_cost": 0.25,
        "slippage_cost": 0.25,
        "other_supported_cost": 0.25,
        "fill_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": quote_id,
    }
    ack = _submit(writer, PAPER_FILL_RECORDED, fill)
    assert ack.status == "OK", ack.detail
    return fill


def _armed_trade(writer, context, *, tag):
    """An admitted trade with positive exposure, an ACTIVE plan, and a crossed quote.

    The returned quote is evidence that has actually crossed the configured stop,
    so a default STOP action in these tests is a genuine crossing rather than the
    pre-fix fixture assumption that any valid-lineage quote sufficed.
    """
    admission, _entry_quote = _entry_trade(writer, context, tag=tag)
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    return admission, _stop_crossed_quote(writer, context, tag=tag)


def _stop_crossed_quote(writer, context, *, tag, quote_id=None):
    """A quote whose executable bid is at or below the plan's configured stop."""
    quote = _quote(
        context,
        quote_id=quote_id or f"quote-stop-{tag}",
        price=89.9,
        ts="2026-09-18T16:00:03Z",
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"
    return quote


def _target_crossed_quote(writer, context, *, tag, quote_id=None):
    """A quote whose executable bid is at or above the plan's configured target."""
    quote = _quote(
        context,
        quote_id=quote_id or f"quote-target-{tag}",
        price=120.5,
        ts="2026-09-18T16:00:03Z",
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"
    return quote


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
    trigger_quote=None,
    reference_price=None,
    trigger_overrides=None,
    exit_overrides=None,
    state_overrides=None,
    ts="2026-09-18T16:00:04Z",
):
    """Build one complete atomic protection action request.

    Defaults describe a valid STOP action against an armed plan with positive
    canonical exposure, so each test overrides exactly the fact it is about. The
    trigger's ``reference_price`` is bound to the cited quote's executable price,
    matching the writer's binding rule.
    """
    if quote_id is not None:
        evidence_ref = quote_id
    elif trigger_type in {"STOP", "TARGET"}:
        evidence_ref = quote["quote_evidence_id"]
    else:
        evidence_ref = None
    if reference_price is None:
        if trigger_type in {"STOP", "TARGET"} and quote_id is None:
            reference_price = float(quote["best_bid"])
        else:
            reference_price = 90.0
    trigger = _trigger(
        admission,
        plan_id=plan_id,
        trigger_id=trigger_id,
        seq=trigger_seq,
        trigger_type=trigger_type,
        quote_id=evidence_ref,
        ts=ts,
        reference_price=reference_price,
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
    admission, quote = _entry_trade(writer, context, tag="act-time")
    # A short holding period so expiry is exercised precisely: the plan is written
    # at 16:00:02 and expires two seconds later.
    assert _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(admission, plan_id="plan-a", max_hold_seconds=2),
    ).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"

    # Exactly at expiry is accepted.
    at_expiry = writer.trigger_paper_protection_action(
        _action_request(
            context,
            admission,
            quote,
            trigger_id="trig-time-act",
            trigger_type="TIME",
            ts="2026-09-18T16:00:04Z",
        )
    )
    assert at_expiry.status == "OK", at_expiry.detail
    triggers, _intents, states = _bundle_rows(writer)
    assert len(triggers) == 1
    assert "market_evidence_ref" not in triggers[0]
    assert len(states) == 2


def test_time_action_before_expiry_is_rejected(writer_env):
    """A TIME trigger may not fire before the configured holding period elapsed."""
    writer, context = writer_env
    admission, quote = _entry_trade(writer, context, tag="act-time-early")
    assert _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(admission, plan_id="plan-a", max_hold_seconds=3600),
    ).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"

    # One second before the 16:00:02 + 3600s expiry.
    early = writer.trigger_paper_protection_action(
        _action_request(
            context,
            admission,
            quote,
            trigger_id="trig-time-early",
            trigger_type="TIME",
            ts="2026-09-18T17:00:01Z",
        )
    )
    assert early.status == "REJECTED"
    assert "max_hold_seconds expiry" in str(early.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


@pytest.mark.parametrize("offset_seconds", [0, 1, 60])
def test_time_action_at_or_after_expiry_is_accepted(writer_env, offset_seconds):
    """Exactly at expiry and any later time are both accepted.

    Each case gets its own writer fixture, so a trade is only ever triggered once
    and the plan's armed state is not consumed by a sibling case.
    """
    writer, context = writer_env
    admission, quote = _entry_trade(writer, context, tag="act-time-ok")
    assert _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(admission, plan_id="plan-a", max_hold_seconds=3600),
    ).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"

    # plan_time 16:00:02, so expiry is 17:00:02.
    second = (17 * 3600) + 2 + offset_seconds
    ts = (
        f"2026-09-18T{second // 3600:02d}:"
        f"{(second % 3600) // 60:02d}:{second % 60:02d}Z"
    )
    ack = writer.trigger_paper_protection_action(
        _action_request(
            context,
            admission,
            quote,
            trigger_id="trig-time-ok",
            trigger_type="TIME",
            ts=ts,
        )
    )
    assert ack.status == "OK", ack.detail
    assert len(_rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED)) == 1


# ---------------------------------------------------------------------------
# 5/6/7/8. Atomic action rejections
# ---------------------------------------------------------------------------


def test_exit_quantity_beyond_exposure_rolls_back_the_whole_bundle(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="act-overclose")
    ack = _run_action(writer, context, admission, quote, exit_quantity=6.0)
    assert ack.status == "REJECTED"
    assert "available canonical exit capacity" in str(ack.detail)
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
    admission, _stop_quote = _armed_trade(writer, context, tag="act-target")
    target_quote = _target_crossed_quote(writer, context, tag="act-target")
    ack = writer.trigger_paper_protection_action(
        _action_request(
            context,
            admission,
            target_quote,
            trigger_id="trig-target",
            trigger_type="TARGET",
            exit_quantity=2.0,
        )
    )
    assert ack.status == "OK", ack.detail
    # The bundle itself changed no quantity.
    assert writer._canonical_fill_totals(admission.paper_trade_id)["remaining_quantity"] == 5.0  # noqa: SLF001

    # Only a canonical SELL fill reduces exposure.
    _leg(writer, context, admission, tag="act-target-exit", role="EXIT", side="SELL",
         quantity=2.0, price=110.0, quote_id=target_quote["quote_evidence_id"],
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

    # Fill the EXIT order the action itself created, rather than opening a second
    # competing exit: the action already claimed the whole remaining exposure.
    _fill_existing_order(
        writer,
        context,
        admission,
        order_id="exit-trig-stop-full",
        tag="act-stop-full-exit",
        quantity=5.0,
        price=90.0,
        quote_id=quote["quote_evidence_id"],
    )
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

    # Fill the action's own EXIT order: it already claimed the full exposure.
    _fill_existing_order(
        writer,
        context,
        admission,
        order_id="exit-trig-act",
        tag="act-release-exit",
        quantity=5.0,
        price=110.0,
        quote_id=quote["quote_evidence_id"],
    )
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
    stop_quote = _stop_crossed_quote(writer, context, tag="release-partial")
    action = writer.trigger_paper_protection_action(
        _action_request(
            context,
            admission,
            stop_quote,
            trigger_id="trig-partial",
            exit_quantity=3.0,
        )
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


# ---------------------------------------------------------------------------
# Remediation: STOP/TARGET threshold enforcement
# ---------------------------------------------------------------------------


def _uncrossed_quote(writer, context, *, tag):
    """A valid-lineage quote whose executable bid has crossed nothing."""
    quote = _quote(
        context,
        quote_id=f"quote-uncrossed-{tag}",
        price=99.9,
        ts="2026-09-18T16:00:03Z",
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"
    return quote


def test_stop_action_rejects_quote_that_has_not_crossed_the_stop(writer_env):
    """A valid-lineage quote is not sufficient: the stop must actually be crossed."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="thr-stop")
    uncrossed = _uncrossed_quote(writer, context, tag="thr-stop")
    assert float(uncrossed["best_bid"]) > 90.0

    ack = _run_action(writer, context, admission, uncrossed, trigger_id="trig-thr-stop")
    assert ack.status == "REJECTED"
    assert "at or below the configured stop_price" in str(ack.detail)
    triggers, intents, states = _bundle_rows(writer)
    assert triggers == []
    assert len(intents) == 1
    assert len(states) == 1


def test_stop_action_is_accepted_at_exactly_the_stop_level(writer_env):
    """The boundary is inclusive: executable price == stop_price crosses."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="thr-stop-eq")
    at_stop = _quote(
        context, quote_id="quote-at-stop", price=90.0, ts="2026-09-18T16:00:03Z"
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, at_stop).status == "OK"
    ack = _run_action(writer, context, admission, at_stop, trigger_id="trig-at-stop")
    assert ack.status == "OK", ack.detail


def test_target_action_rejects_quote_that_has_not_crossed_the_target(writer_env):
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="thr-target")
    uncrossed = _uncrossed_quote(writer, context, tag="thr-target")
    assert float(uncrossed["best_bid"]) < 120.0

    ack = _run_action(
        writer,
        context,
        admission,
        uncrossed,
        trigger_id="trig-thr-target",
        trigger_type="TARGET",
    )
    assert ack.status == "REJECTED"
    assert "at or above the eligible target price" in str(ack.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


def test_target_action_is_accepted_at_exactly_the_target_level(writer_env):
    """The boundary is inclusive once the exit is sized to the target's fraction."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="thr-target-eq")
    at_target = _quote(
        context, quote_id="quote-at-target", price=120.0, ts="2026-09-18T16:00:03Z"
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, at_target).status == "OK"
    ack = _run_action(
        writer,
        context,
        admission,
        at_target,
        trigger_id="trig-at-target",
        trigger_type="TARGET",
        # TP1 is configured for 0.5 of the 5.0 position, so 2.5 is its full size.
        exit_quantity=2.5,
    )
    assert ack.status == "OK", ack.detail


def test_first_target_cannot_full_close_the_position(writer_env):
    """Crossing the first target does not authorise exiting the whole position."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="thr-tp1-nofull")
    target_quote = _target_crossed_quote(writer, context, tag="thr-tp1-nofull")

    full_close = _run_action(
        writer,
        context,
        admission,
        target_quote,
        trigger_id="trig-tp1-full",
        trigger_type="TARGET",
        exit_quantity=5.0,
    )
    assert full_close.status == "REJECTED"
    assert "exceeds the eligible target's configured fraction" in str(full_close.detail)
    # Nothing from the refused bundle was committed.
    triggers, intents, states = _bundle_rows(writer)
    assert triggers == []
    assert len(intents) == 1
    assert len(states) == 1

    # The target's own fraction is accepted.
    sized = _run_action(
        writer,
        context,
        admission,
        target_quote,
        trigger_id="trig-tp1-sized",
        trigger_type="TARGET",
        exit_quantity=2.5,
    )
    assert sized.status == "OK", sized.detail


def test_staged_targets_are_consumed_in_ascending_price_order(writer_env):
    """Each committed TARGET consumes one target, lowest price first.

    A quote that has crossed both targets still only authorises the first
    untriggered target, so one action can never collapse a staged plan into a
    full exit.
    """
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="thr-staged")
    plan = _plan(
        admission,
        plan_id="plan-a",
        targets=(
            {"target_id": "tp1", "price": 110.0, "fraction": 0.4},
            {"target_id": "tp2", "price": 120.0, "fraction": 0.5},
        ),
    )
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, plan).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    above_both = _quote(
        context, quote_id="quote-above-both", price=125.0, ts="2026-09-18T16:00:03Z"
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, above_both).status == "OK"

    # Before any target commits, tp1 is the eligible one (0.4 x 5.0 = 2.0).
    assert writer._eligible_target(plan, plan_id="plan-a")["target_id"] == "tp1"  # noqa: SLF001

    # tp2's size is refused while tp1 is the eligible target.
    oversize = _run_action(
        writer,
        context,
        admission,
        above_both,
        trigger_id="trig-staged-oversize",
        trigger_type="TARGET",
        exit_quantity=2.5,
    )
    assert oversize.status == "REJECTED"
    assert "configured fraction" in str(oversize.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []

    # tp1's own fraction is accepted.
    first = _run_action(
        writer,
        context,
        admission,
        above_both,
        trigger_id="trig-staged-tp1",
        trigger_type="TARGET",
        exit_quantity=2.0,
    )
    assert first.status == "OK", first.detail

    # tp1 is now consumed, so the deterministic derivation advances to tp2.
    assert writer._eligible_target(plan, plan_id="plan-a")["target_id"] == "tp2"  # noqa: SLF001
    assert len(_rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED)) == 1


def test_triggered_plan_cannot_fire_a_second_target(writer_env):
    """The frozen state machine bounds later targets to a new plan revision.

    After a target fires, the plan is TRIGGERED and no longer armed, so a second
    action on the same plan is refused. Consuming a later target therefore
    requires a new immutable plan revision, which is the frozen B/C-2 design and
    deliberately not widened here.
    """
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="thr-second")
    plan = _plan(
        admission,
        plan_id="plan-a",
        targets=(
            {"target_id": "tp1", "price": 110.0, "fraction": 0.4},
            {"target_id": "tp2", "price": 120.0, "fraction": 0.5},
        ),
    )
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, plan).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    above_both = _quote(
        context, quote_id="quote-second-both", price=125.0, ts="2026-09-18T16:00:03Z"
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, above_both).status == "OK"

    assert _run_action(
        writer,
        context,
        admission,
        above_both,
        trigger_id="trig-second-tp1",
        trigger_type="TARGET",
        exit_quantity=2.0,
    ).status == "OK"

    second = _run_action(
        writer,
        context,
        admission,
        above_both,
        trigger_id="trig-second-tp2",
        trigger_type="TARGET",
        trigger_seq=1,
        state_event_id="pstate-second-tp2",
        state_seq=2,
        exit_quantity=2.5,
    )
    assert second.status == "REJECTED"
    assert "armed" in str(second.detail)
    assert len(_rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED)) == 1


@pytest.mark.parametrize(
    ("fraction", "expected_cap"),
    [(0.2, 1.0), (0.5, 2.5), (1.0, 5.0)],
)
def test_target_exit_cap_tracks_the_configured_fraction(
    writer_env, fraction, expected_cap
):
    """The cap follows the plan's own fraction, in both directions.

    A fraction of 1.0 legitimately authorises a full close; smaller fractions do
    not. This proves the bound is the plan's authority rather than a blanket ban.
    """
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag=f"thr-frac-{fraction}")
    assert _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(
            admission,
            plan_id="plan-a",
            targets=(
                {"target_id": "tp1", "price": 120.0, "fraction": fraction},
            ),
        ),
    ).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    above = _quote(
        context, quote_id="quote-frac", price=125.0, ts="2026-09-18T16:00:03Z"
    )
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, above).status == "OK"

    # A request above the fraction cap is refused.
    over = _run_action(
        writer,
        context,
        admission,
        above,
        trigger_id="trig-frac-over",
        trigger_type="TARGET",
        exit_quantity=expected_cap + 0.5,
    )
    if expected_cap + 0.5 > 5.0 + 1e-9:
        # Cannot exceed canonical exposure either way, so assert the other bound.
        assert over.status == "REJECTED"
    else:
        assert over.status == "REJECTED"
        assert "configured fraction" in str(over.detail)

    # Exactly the fraction cap is accepted.
    exact = _run_action(
        writer,
        context,
        admission,
        above,
        trigger_id="trig-frac-exact",
        trigger_type="TARGET",
        exit_quantity=expected_cap,
    )
    assert exact.status == "OK", exact.detail


def test_target_trigger_retry_stays_idempotent(writer_env):
    """A staged target action retried unchanged is DUPLICATE_OK, not a second target."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="thr-retry")
    target_quote = _target_crossed_quote(writer, context, tag="thr-retry")
    request = _action_request(
        context,
        admission,
        target_quote,
        trigger_id="trig-thr-retry",
        trigger_type="TARGET",
        exit_quantity=2.5,
    )
    first = writer.trigger_paper_protection_action(request)
    assert first.status == "OK", first.detail
    retry = writer.trigger_paper_protection_action(request)
    assert retry.status == "DUPLICATE_OK"
    assert retry.trigger_event_id == first.trigger_event_id
    assert len(_rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED)) == 1


def test_stop_action_rejects_reference_price_disagreeing_with_the_quote(writer_env):
    """reference_price is bound to the cited evidence, not caller-chosen."""
    writer, context = writer_env
    admission, stop_quote = _armed_trade(writer, context, tag="thr-ref")
    ack = _run_action(
        writer,
        context,
        admission,
        stop_quote,
        trigger_id="trig-thr-ref",
        reference_price=80.0,
    )
    assert ack.status == "REJECTED"
    assert "must equal the executable quote price" in str(ack.detail)
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []


# ---------------------------------------------------------------------------
# Remediation: aggregate exit conservation at commit
# ---------------------------------------------------------------------------


def _exit_order_payload(admission, context, *, order_id, quantity, ts):
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "order_intent_id": order_id,
        "paper_trade_id": admission.paper_trade_id,
        "decision_context_id": context["context_id"],
        "intent_seq": 0,
        "intent_role": "EXIT",
        "side": "SELL",
        "order_type": "MARKET",
        "requested_quantity": quantity,
        "requested_notional": quantity * 100.0,
        "reason_code": "EXIT_PROBE",
        "intent_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "reservation_id": admission.reservation_id,
    }


def test_competing_exit_intents_cannot_together_exceed_exposure(writer_env):
    """An unfilled EXIT intent's claim is not available to a second exit."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="cons-compete")
    first = _run_action(
        writer, context, admission, quote, trigger_id="trig-cons-1", exit_quantity=3.0
    )
    assert first.status == "OK", first.detail

    # 3.0 is already claimed and unfilled, so only 2.0 of the 5.0 remains free.
    competing = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-cons-competing",
            quantity=3.0,
            ts="2026-09-18T16:00:05Z",
        ),
    )
    assert competing.status == "REJECTED"
    assert "available canonical exit capacity" in str(competing.detail)


def test_exit_intent_within_remaining_capacity_is_accepted(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="cons-fit")
    assert _run_action(
        writer, context, admission, quote, trigger_id="trig-cons-fit", exit_quantity=3.0
    ).status == "OK"

    fitting = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-cons-fit",
            quantity=2.0,
            ts="2026-09-18T16:00:05Z",
        ),
    )
    assert fitting.status == "OK", fitting.detail


def _plant_order_intent(writer, order_payload):
    """Artificially commit an order intent row, bypassing writer capacity rules.

    Used only to reach an over-committed canonical state a correct writer cannot
    produce, so the aggregate fill backstop can be proven to fire.
    """
    meta = writer._conn.execute(  # noqa: SLF001
        "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
    ).fetchone()
    sequence = int(meta["next_local_sequence"])
    event_id = writer._insert_event_row_in_transaction(  # noqa: SLF001
        event_type=PAPER_ORDER_INTENT_RECORDED,
        idempotency_key=paper_evidence_idempotency_key(
            PAPER_ORDER_INTENT_RECORDED, order_payload
        ),
        payload=order_payload,
        history_epoch=int(meta["history_epoch"]),
        local_sequence=sequence,
        now="2026-09-18T16:00:06Z",
    )
    # Advance the canonical sequence so a second plant cannot reuse the slot.
    writer._conn.execute(  # noqa: SLF001
        "UPDATE meta SET next_local_sequence = ? WHERE id = 1",
        (sequence + 1,),
    )
    writer._conn.commit()  # noqa: SLF001
    return event_id


def test_aggregate_exit_fills_cannot_exceed_entry_exposure(writer_env):
    """Backstop: even an over-committed canonical state cannot over-close.

    Two exit orders that together promise more than the trade holds cannot both
    fill; the second fill is refused by aggregate conservation rather than by the
    per-order cap.
    """
    writer, context = writer_env
    admission, quote = _entry_trade(writer, context, tag="cons-backstop")
    for index in range(2):
        _plant_order_intent(
            writer,
            _exit_order_payload(
                admission,
                context,
                order_id=f"order-planted-{index}",
                quantity=5.0,
                ts="2026-09-18T16:00:06Z",
            ),
        )
    # The first full-size exit consumes the whole entry exposure.
    _fill_existing_order(
        writer,
        context,
        admission,
        order_id="order-planted-0",
        tag="cons-backstop-0",
        quantity=5.0,
        price=100.0,
        quote_id=quote["quote_evidence_id"],
    )
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["exit_quantity"] == totals["entry_quantity"] == 5.0

    # The second would take exit quantity past entry exposure.
    second_attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-cons-backstop-1",
        "order_intent_id": "order-planted-1",
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact("2026-09-18T16:30:00Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 5.0,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    assert _submit(
        writer, PAPER_EXECUTION_ATTEMPT_RECORDED, second_attempt
    ).status == "OK"
    over = _submit(
        writer,
        PAPER_FILL_RECORDED,
        {
            "schema_version": 1,
            "engine": ENGINE_OPIP_PAPER_V2,
            "fill_id": "fill-cons-backstop-1",
            "execution_attempt_id": "attempt-cons-backstop-1",
            "order_intent_id": "order-planted-1",
            "paper_trade_id": admission.paper_trade_id,
            "fill_seq": 0,
            "side": "SELL",
            "quantity": 5.0,
            "price": 100.0,
            "fee_cost": 0.25,
            "spread_cost": 0.25,
            "slippage_cost": 0.25,
            "other_supported_cost": 0.25,
            "fill_time": _exact("2026-09-18T16:30:00Z"),
            "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
            "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
            "market_evidence_ref": quote["quote_evidence_id"],
        },
    )
    assert over.status == "REJECTED"
    assert "aggregate exit quantity exceeds canonical entry exposure" in str(over.detail)
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 2  # entry plus the first exit


# ---------------------------------------------------------------------------
# Remediation: canonical long-only role/side semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "side"),
    [("EXIT", "BUY"), ("ENTRY", "SELL")],
)
def test_unsupported_role_side_pairs_are_rejected(writer_env, role, side):
    """EXIT/BUY and ENTRY/SELL are not Paper v2 semantics and must not commit."""
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag=f"role-{role}-{side}")
    ack = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        {
            "schema_version": 1,
            "engine": ENGINE_OPIP_PAPER_V2,
            "order_intent_id": f"probe-role-{role}-{side}",
            "paper_trade_id": admission.paper_trade_id,
            "decision_context_id": context["context_id"],
            "intent_seq": 0,
            "intent_role": role,
            "side": side,
            "order_type": "MARKET",
            "requested_quantity": 1.0,
            "requested_notional": 100.0,
            "reason_code": "ROLE_SIDE_PROBE",
            "intent_time": _exact("2026-09-18T16:00:05Z"),
            "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
            "reservation_id": admission.reservation_id,
        },
    )
    assert ack.status == "REJECTED"
    assert f"{role} order intent must use side" in str(ack.detail)
    # The refused pair created no exposure or protection eligibility.
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 1
    assert writer._canonical_fill_totals(admission.paper_trade_id)["entry_quantity"] == 5.0  # noqa: SLF001


def test_exposure_is_derived_from_parent_order_role_not_fill_side(writer_env):
    """Economics follow the parent order's canonical role.

    An exit-role fill reduces remaining exposure even though it is also a SELL, so
    role and side agree by construction now that the pairing is enforced.
    """
    writer, context = writer_env
    admission, quote = _entry_trade(writer, context, tag="role-role")
    _leg(
        writer,
        context,
        admission,
        tag="role-role-exit",
        role="EXIT",
        side="SELL",
        quantity=2.0,
        price=110.0,
        quote_id=quote["quote_evidence_id"],
        ts="2026-09-18T16:30:00Z",
    )
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["entry_quantity"] == 5.0
    assert totals["exit_quantity"] == 2.0
    assert totals["remaining_quantity"] == 3.0


# ---------------------------------------------------------------------------
# Remediation: reconciliation binding
# ---------------------------------------------------------------------------


def test_reconciliation_rejects_reserved_capital_not_matching_admission(writer_env):
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="rec-reserve")
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    payload = _reconciliation(
        admission,
        terminal="FINAL_VERIFIED",
        position_state="FLAT",
        entry=totals["entry_quantity"],
        exit_qty=totals["exit_quantity"],
        remaining=totals["remaining_quantity"],
        gross=totals["gross_pnl"],
        costs=totals["execution_costs"],
    )
    payload["reserved_capital"] = 0.0
    ack = _submit(writer, PAPER_RECONCILIATION_RECORDED, payload)
    assert ack.status == "REJECTED"
    assert "reserved_capital does not match" in str(ack.detail)
    assert _rows(writer, PAPER_RECONCILIATION_RECORDED) == []


def test_reconciliation_rejects_position_state_contradicting_fills(writer_env):
    """A partially exited trade is REDUCING; claiming OPEN is refused."""
    writer, context = writer_env
    admission, _ = _round_trip(
        writer, context, tag="rec-position", exit_quantity=2.0
    )
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert writer._expected_position_state(totals) == "REDUCING"  # noqa: SLF001

    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            terminal="OPEN",
            position_state="OPEN",
            entry=totals["entry_quantity"],
            exit_qty=totals["exit_quantity"],
            remaining=totals["remaining_quantity"],
            gross=totals["gross_pnl"],
            costs=totals["execution_costs"],
        ),
    )
    assert ack.status == "REJECTED"
    assert "position_state does not agree" in str(ack.detail)


def test_reconciliation_accepts_position_state_matching_fills(writer_env):
    """Control: the fill-derived position state is accepted."""
    writer, context = writer_env
    admission, _ = _round_trip(
        writer, context, tag="rec-position-ok", exit_quantity=2.0
    )
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            terminal="OPEN",
            position_state="REDUCING",
            entry=totals["entry_quantity"],
            exit_qty=totals["exit_quantity"],
            remaining=totals["remaining_quantity"],
            gross=totals["gross_pnl"],
            costs=totals["execution_costs"],
        ),
    )
    assert ack.status == "OK", ack.detail


def test_reconciliation_rejects_remaining_quantity_not_matching_fills(writer_env):
    """Canonical quantity claims must match fills, not merely be self-consistent.

    The frozen contract already enforces ``entry == exit + remaining``, so a
    payload cannot disagree on ``remaining`` alone while the other two agree. This
    therefore proves the binding with a self-consistent payload whose quantities
    still contradict canonical fills.
    """
    writer, context = writer_env
    admission, _ = _round_trip(writer, context, tag="rec-qty", exit_quantity=2.0)
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["entry_quantity"] == 5.0
    assert totals["exit_quantity"] == 2.0
    assert totals["remaining_quantity"] == 3.0

    # Internally consistent (4 == 2 + 2) but not reproducible from canonical fills.
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            terminal="OPEN",
            position_state="REDUCING",
            entry=4.0,
            exit_qty=2.0,
            remaining=2.0,
            gross=totals["gross_pnl"],
            costs=totals["execution_costs"],
        ),
    )
    assert ack.status == "REJECTED"
    assert "is not reproducible from canonical fills" in str(ack.detail)
    assert _rows(writer, PAPER_RECONCILIATION_RECORDED) == []


# ---------------------------------------------------------------------------
# ARB terminality: FINAL_VERIFIED ends the trade's economic lifecycle
# ---------------------------------------------------------------------------


def _terminal_attempt(
    writer,
    admission,
    *,
    order_id,
    tag,
    state="REJECTED",
    seq=0,
    ts="2026-09-18T16:30:00Z",
):
    """Commit a terminal (non-fillable) attempt for an existing EXIT order."""
    attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        # Distinct from the fixture's entry attempt id, which is `attempt-{tag}`.
        "execution_attempt_id": f"attempt-terminal-{tag}",
        "order_intent_id": order_id,
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": seq,
        "execution_state": state,
        "attempt_time": _exact(ts),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }
    if state == "REJECTED":
        attempt["rejection_reason"] = "VENUE_REJECTED"
    return _submit(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)


def _finalized_trade(writer, context, *, tag="finalized", exit_quantity=5.0):
    """Drive a trade all the way to terminal FINAL_VERIFIED reconciliation.

    Entry 5 BUY, exit 5 SELL, then the terminal reconciliation that requires the
    economics to be reproducible from those fills.
    """
    admission, quote = _round_trip(
        writer, context, tag=tag, exit_quantity=exit_quantity
    )
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            recon_id=f"recon-{tag}",
            seq=0,
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
    assert admission.paper_trade_id in writer._final_verified_trade_ids()  # noqa: SLF001
    return admission, quote, totals


def test_new_order_intent_after_final_verified_is_rejected(writer_env):
    writer, context = writer_env
    admission, _quote, _totals = _finalized_trade(writer, context, tag="term-intent")
    ack = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="post-final-intent",
            quantity=1.0,
            ts="2026-09-18T18:00:00Z",
        ),
    )
    assert ack.status == "REJECTED"
    assert "FINAL_VERIFIED" in str(ack.detail)
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 2


def test_new_execution_attempt_after_final_verified_is_rejected(writer_env):
    writer, context = writer_env
    admission, _quote, _totals = _finalized_trade(writer, context, tag="term-attempt")
    ack = _terminal_attempt(
        writer, admission, order_id="intent-term-attempt", tag="post-final", seq=5
    )
    assert ack.status == "REJECTED"
    assert "FINAL_VERIFIED" in str(ack.detail)


def test_new_fill_after_final_verified_is_rejected(writer_env):
    """A fill after terminal reconciliation cannot recreate exposure."""
    writer, context = writer_env
    admission, quote, _totals = _finalized_trade(writer, context, tag="term-fill")
    attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-post-final",
        "order_intent_id": "intent-term-fill",
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 9,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact("2026-09-18T18:00:00Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 1.0,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    # The attempt itself is refused, so the fill that would follow is unreachable.
    assert _submit(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt).status == "REJECTED"

    fill = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": "fill-post-final",
        "execution_attempt_id": "attempt-term-fill-exit",
        "order_intent_id": "intent-term-fill",
        "paper_trade_id": admission.paper_trade_id,
        "fill_seq": 5,
        "side": "SELL",
        "quantity": 1.0,
        "price": 100.0,
        "fee_cost": 0.25,
        "spread_cost": 0.25,
        "slippage_cost": 0.25,
        "other_supported_cost": 0.25,
        "fill_time": _exact("2026-09-18T18:00:00Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    ack = _submit(writer, PAPER_FILL_RECORDED, fill)
    assert ack.status == "REJECTED"
    assert "FINAL_VERIFIED" in str(ack.detail)


def test_later_non_final_reconciliation_after_final_verified_is_rejected(writer_env):
    """A higher-sequence, non-final reconciliation cannot reopen a final trade."""
    writer, context = writer_env
    admission, _quote, totals = _finalized_trade(writer, context, tag="term-recon")
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            recon_id="recon-post-final",
            seq=1,
            terminal="OPEN",
            position_state="OPEN",
            entry=totals["entry_quantity"],
            exit_qty=totals["exit_quantity"],
            remaining=totals["remaining_quantity"],
            gross=totals["gross_pnl"],
            costs=totals["execution_costs"],
        ),
    )
    assert ack.status == "REJECTED"
    assert "FINAL_VERIFIED" in str(ack.detail)
    assert len(_rows(writer, PAPER_RECONCILIATION_RECORDED)) == 1


def test_unresolved_evidence_reconciliation_after_final_verified_is_rejected(writer_env):
    """Unverifiable evidence cannot be appended once the trade is terminal."""
    writer, context = writer_env
    admission, _quote, _totals = _finalized_trade(writer, context, tag="term-unresolved")
    ack = _submit(
        writer,
        PAPER_RECONCILIATION_RECORDED,
        _reconciliation(
            admission,
            recon_id="recon-post-unresolved",
            seq=1,
            terminal="UNRESOLVED_EVIDENCE",
            position_state="OPEN",
            entry=0.0,
            exit_qty=0.0,
            remaining=0.0,
            gross=0.0,
            costs=0.0,
            reason="late contradictory evidence",
        ),
    )
    assert ack.status == "REJECTED"
    assert "FINAL_VERIFIED" in str(ack.detail)
    assert len(_rows(writer, PAPER_RECONCILIATION_RECORDED)) == 1


def test_protection_mutations_after_final_verified_are_rejected(writer_env):
    """No new protection plan, state, trigger action or fill may follow."""
    writer, context = writer_env
    admission, quote, _totals = _finalized_trade(writer, context, tag="term-protect")

    plan = _submit(
        writer,
        PAPER_PROTECTION_PLAN_RECORDED,
        _plan(admission, plan_id="plan-post-final"),
    )
    assert plan.status == "REJECTED"
    assert "FINAL_VERIFIED" in str(plan.detail)

    action = _run_action(
        writer, context, admission, quote, trigger_id="trig-post-final"
    )
    assert action.status == "REJECTED"
    assert _rows(writer, PAPER_PROTECTION_PLAN_RECORDED) == []
    assert _rows(writer, PAPER_PROTECTION_TRIGGER_RECORDED) == []
    assert _rows(writer, PAPER_PROTECTION_STATE_RECORDED) == []


def test_terminality_is_scoped_to_the_finalized_trade(writer_env):
    """Only the terminal trade is frozen; a sibling trade still reserves capacity."""
    writer, context = writer_env
    finalized, _quote, _totals = _finalized_trade(writer, context, tag="term-scope")

    # A second, still-open trade in the same quote currency keeps its reservation.
    open_admission = _admit(
        writer, context, disposition_id="disp-term-open", expected_version=1
    )
    assert open_admission.disposition == "ADMITTED"

    version, reserved, active = writer._portfolio_state("USD")  # noqa: SLF001
    assert version == 2
    assert reserved == 500.0  # only the open trade's reservation remains
    assert active == 1
    assert writer._final_verified_trade_ids() == {finalized.paper_trade_id}  # noqa: SLF001
    assert writer._verified_paper_trade_ids("USD") == {finalized.paper_trade_id}  # noqa: SLF001

    # The open trade can still be mutated; the terminal one cannot.
    assert (
        _submit(
            writer,
            PAPER_ORDER_INTENT_RECORDED,
            _exit_order_payload(
                open_admission,
                context,
                order_id="open-trade-intent",
                quantity=1.0,
                ts="2026-09-18T18:00:00Z",
            ),
        ).status
        == "REJECTED"  # no exposure yet on that trade, so no exit capacity
    )


def test_exact_replay_of_committed_evidence_after_final_verified_is_safe(writer_env):
    """Terminality must not break idempotent retry or ACK-loss recovery."""
    writer, context = writer_env
    admission, quote, totals = _finalized_trade(writer, context, tag="term-replay")

    # Replay the committed exit fill, the terminal reconciliation, and an entry
    # order intent: each must resolve through idempotency rather than terminality.
    exit_fill = next(
        fill
        for fill in _rows(writer, PAPER_FILL_RECORDED)
        if fill["side"] == "SELL"
    )
    assert _submit(writer, PAPER_FILL_RECORDED, exit_fill).status == "DUPLICATE_OK"

    terminal_reconciliation = _rows(writer, PAPER_RECONCILIATION_RECORDED)[0]
    assert (
        _submit(writer, PAPER_RECONCILIATION_RECORDED, terminal_reconciliation).status
        == "DUPLICATE_OK"
    )

    entry_order = next(
        order
        for order in _rows(writer, PAPER_ORDER_INTENT_RECORDED)
        if order["intent_role"] == "ENTRY"
    )
    assert _submit(writer, PAPER_ORDER_INTENT_RECORDED, entry_order).status == "DUPLICATE_OK"

    # A divergent payload on a committed identity is still a conflict, not a pass.
    changed = dict(exit_fill)
    changed["price"] = float(exit_fill["price"]) + 1.0
    conflict = _submit(writer, PAPER_FILL_RECORDED, changed)
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"

    # Nothing new was written by any of the replays.
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 2
    assert len(_rows(writer, PAPER_RECONCILIATION_RECORDED)) == 1
    assert writer._canonical_fill_totals(admission.paper_trade_id) == totals  # noqa: SLF001


def test_restart_reconstructs_terminal_state_from_canonical_evidence(writer_env, tmp_path):
    """A reopened writer derives the same terminality from canonical evidence."""
    writer, context = writer_env
    admission, _quote, totals = _finalized_trade(writer, context, tag="term-restart")
    db_path = tmp_path / "canonical.sqlite3"
    writer.close()

    reopened = CanonicalWriter(db_path)
    try:
        assert reopened._final_verified_trade_ids() == {admission.paper_trade_id}  # noqa: SLF001
        assert reopened._verified_paper_trade_ids("USD") == {admission.paper_trade_id}  # noqa: SLF001
        assert reopened._canonical_fill_totals(admission.paper_trade_id) == totals  # noqa: SLF001
        # Terminality is enforced identically after restart.
        blocked = reopened.submit(
            WriterIntent(
                schema_version=SCHEMA_VERSION,
                priority="LOW",
                idempotency_key=paper_evidence_idempotency_key(
                    PAPER_ORDER_INTENT_RECORDED,
                    _exit_order_payload(
                        admission,
                        context,
                        order_id="restart-post-final",
                        quantity=1.0,
                        ts="2026-09-18T18:00:00Z",
                    ),
                ),
                event_type=PAPER_ORDER_INTENT_RECORDED,
                payload=_exit_order_payload(
                    admission,
                    context,
                    order_id="restart-post-final",
                    quantity=1.0,
                    ts="2026-09-18T18:00:00Z",
                ),
            )
        )
        assert blocked.status == "REJECTED"
        assert "FINAL_VERIFIED" in str(blocked.detail)
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# ARB: a terminal failed exit releases its unfilled capacity
# ---------------------------------------------------------------------------


def test_terminal_failed_exit_releases_unfilled_capacity(writer_env):
    """A dead EXIT order must not permanently shrink available exit capacity."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="rel-fail")
    action = _run_action(
        writer, context, admission, quote, trigger_id="trig-rel-fail", exit_quantity=3.0
    )
    assert action.status == "OK", action.detail

    # The action's 3.0 is claimed and live, so only 2.0 of the 5.0 is free.
    blocked = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-rel-blocked",
            quantity=5.0,
            ts="2026-09-18T16:00:05Z",
        ),
    )
    assert blocked.status == "REJECTED"
    assert "available canonical exit capacity" in str(blocked.detail)

    # The order cannot fill again once its only attempt is terminal.
    failed = _terminal_attempt(
        writer, admission, order_id="exit-trig-rel-fail", tag="rel-fail"
    )
    assert failed.status == "OK", failed.detail
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 0.0  # noqa: SLF001

    replacement = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-rel-replacement",
            quantity=3.0,
            ts="2026-09-18T16:00:05Z",
        ),
    )
    assert replacement.status == "OK", replacement.detail


@pytest.mark.parametrize("terminal_state", ["REJECTED", "CANCELLED", "EXPIRED"])
def test_every_terminal_attempt_state_releases_capacity(writer_env, terminal_state):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag=f"rel-{terminal_state}")
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id=f"trig-rel-{terminal_state}",
        exit_quantity=5.0,
    )
    assert action.status == "OK", action.detail
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 5.0  # noqa: SLF001

    assert (
        _terminal_attempt(
            writer,
            admission,
            order_id=f"exit-trig-rel-{terminal_state}",
            tag=f"rel-{terminal_state}",
            state=terminal_state,
        ).status
        == "OK"
    )
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 0.0  # noqa: SLF001


def test_partial_fill_then_later_terminal_attempt_keeps_remainder_reserved(
    writer_env,
):
    """Case 2: a later terminal attempt does not kill an earlier fill-capable one.

    The partial-fill attempt remains canonically fill-capable, so the order's
    unfilled remainder stays reserved even though a later attempt was rejected.
    Releasing it would let a replacement EXIT claim exposure this order can still
    consume.
    """
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="rel-partial")
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-rel-partial",
        exit_quantity=5.0,
    )
    assert action.status == "OK", action.detail

    # Fill 2.0 of the exit order; its attempt stays ACCEPTED (fill-capable).
    _fill_existing_order(
        writer,
        context,
        admission,
        order_id="exit-trig-rel-partial",
        tag="rel-partial-fill",
        quantity=2.0,
        price=90.0,
        quote_id=quote["quote_evidence_id"],
    )
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["remaining_quantity"] == 3.0
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 3.0  # noqa: SLF001

    assert (
        _terminal_attempt(
            writer,
            admission,
            order_id="exit-trig-rel-partial",
            tag="rel-partial",
            # The partial-fill attempt already took seq 0 for this order.
            seq=1,
            ts="2026-09-18T16:45:00Z",
        ).status
        == "OK"
    )
    # The earlier attempt is still fill-capable, so the remainder stays claimed.
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 3.0  # noqa: SLF001
    assert writer._exit_capacity(admission.paper_trade_id)["available_exit_quantity"] == 0.0  # noqa: SLF001
    # The 2.0 fill remains canonical economic history either way.
    assert writer._canonical_fill_totals(admission.paper_trade_id) == totals  # noqa: SLF001
    # And a replacement EXIT cannot double-claim that exposure.
    replacement = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-rel-partial-replacement",
            quantity=3.0,
            ts="2026-09-18T16:46:00Z",
        ),
    )
    assert replacement.status == "REJECTED"
    assert "available canonical exit capacity" in str(replacement.detail)


def test_partial_fill_with_later_cancel_keeps_remainder_reserved(writer_env):
    """Case 2 (CANCELLED variant): a cancelled later attempt does not free capacity.

    The partial-fill attempt is still fill-capable, so the order keeps reserving its
    unfilled remainder; the cancellation changes nothing economically.
    """
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="rel-allterm")
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-rel-allterm",
        exit_quantity=5.0,
    )
    assert action.status == "OK", action.detail

    _fill_existing_order(
        writer,
        context,
        admission,
        order_id="exit-trig-rel-allterm",
        tag="rel-allterm-fill",
        quantity=2.0,
        price=90.0,
        quote_id=quote["quote_evidence_id"],
    )
    totals = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert totals["remaining_quantity"] == 3.0
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 3.0  # noqa: SLF001

    # Terminate the order: the only attempt becomes non-fillable, so the 3.0
    # remainder can never be filled and is released.
    assert (
        _terminal_attempt(
            writer,
            admission,
            order_id="exit-trig-rel-allterm",
            tag="rel-allterm",
            state="CANCELLED",
            seq=0,
            ts="2026-09-18T16:45:00Z",
        ).status
        == "REJECTED"  # seq 0 is already committed for this order
    )
    assert (
        _terminal_attempt(
            writer,
            admission,
            order_id="exit-trig-rel-allterm",
            tag="rel-allterm",
            state="CANCELLED",
            seq=1,
            ts="2026-09-18T16:45:00Z",
        ).status
        == "OK"
    )
    # The fill-capable attempt is superseded only by being non-fillable itself:
    # here the order still has a fill-capable attempt, so it stays reserved.
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 3.0  # noqa: SLF001
    # Filled quantity is untouched canonical history.
    assert writer._canonical_fill_totals(admission.paper_trade_id) == totals  # noqa: SLF001


def test_all_attempts_terminal_releases_capacity_for_a_replacement_exit(writer_env):
    """Case 3 (release path): no fill-capable attempt remains, so capacity frees."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="rel-dead")
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-rel-dead",
        exit_quantity=3.0,
    )
    assert action.status == "OK", action.detail
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 3.0  # noqa: SLF001

    # Only a terminal attempt ever exists for this order, so nothing is fill-capable.
    assert (
        _terminal_attempt(
            writer, admission, order_id="exit-trig-rel-dead", tag="rel-dead"
        ).status
        == "OK"
    )
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 0.0  # noqa: SLF001
    assert writer._exit_capacity(admission.paper_trade_id)["available_exit_quantity"] == 5.0  # noqa: SLF001

    # A replacement protection EXIT can now use the freed capacity, and it is
    # still bounded by canonical exposure.
    replacement = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-rel-replacement",
            quantity=3.0,
            ts="2026-09-18T16:46:00Z",
        ),
    )
    assert replacement.status == "OK", replacement.detail
    over = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-rel-overclaim",
            quantity=3.0,
            ts="2026-09-18T16:46:00Z",
        ),
    )
    assert over.status == "REJECTED"
    assert "available canonical exit capacity" in str(over.detail)


def test_exit_order_with_no_attempt_still_reserves_its_capacity(writer_env):
    """Case 4: a committed EXIT intent is a claim before any attempt exists."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="rel-noattempt")
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-rel-noattempt",
        exit_quantity=5.0,
    )
    assert action.status == "OK", action.detail

    # The action's EXIT order has no attempt at all, yet its full 5.0 is claimed.
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 5.0  # noqa: SLF001
    assert writer._exit_capacity(admission.paper_trade_id)["available_exit_quantity"] == 0.0  # noqa: SLF001

    competing = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-rel-noattempt",
            quantity=5.0,
            ts="2026-09-18T16:46:00Z",
        ),
    )
    assert competing.status == "REJECTED"
    assert "available canonical exit capacity" in str(competing.detail)


def test_competing_active_attempt_keeps_capacity_reserved(writer_env):
    """Case 5: an active attempt on the action's EXIT order blocks a competitor.

    This is the Greptile defect scenario: an earlier WORKING attempt plus a later
    REJECTED attempt must not free the order's unfilled capacity, because the
    WORKING attempt can still be filled by canonical B/C-1 fill evidence.
    """
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="rel-live")
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-rel-live",
        exit_quantity=5.0,
    )
    assert action.status == "OK", action.detail

    # attempt_seq 0 = WORKING: still fill-capable.
    working = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-rel-live-working",
        "order_intent_id": "exit-trig-rel-live",
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "WORKING",
        "attempt_time": _exact("2026-09-18T16:30:00Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        # A fill-capable attempt must declare the quantity it accepted.
        "accepted_quantity": 5.0,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    assert _submit(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, working).status == "OK"

    # attempt_seq 1 = REJECTED: the later attempt does not supersede seq 0.
    assert (
        _terminal_attempt(
            writer,
            admission,
            order_id="exit-trig-rel-live",
            tag="rel-live",
            seq=1,
            ts="2026-09-18T16:31:00Z",
        ).status
        == "OK"
    )

    # The order still holds its full claim, so a competing EXIT is refused.
    assert writer._outstanding_exit_quantity(admission.paper_trade_id) == 5.0  # noqa: SLF001
    assert writer._exit_capacity(admission.paper_trade_id)["available_exit_quantity"] == 0.0  # noqa: SLF001
    competing = _submit(
        writer,
        PAPER_ORDER_INTENT_RECORDED,
        _exit_order_payload(
            admission,
            context,
            order_id="probe-rel-live-competing",
            quantity=5.0,
            ts="2026-09-18T16:32:00Z",
        ),
    )
    assert competing.status == "REJECTED"
    assert "available canonical exit capacity" in str(competing.detail)
    # The WORKING attempt is still fill-capable, so only one EXIT intent exists.
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 2


def test_restart_rederives_outstanding_exit_capacity_from_canonical_evidence(
    writer_env, tmp_path
):
    """Case 6: a reopened writer derives the same outstanding capacity."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="rel-restart")
    action = _run_action(
        writer,
        context,
        admission,
        quote,
        trigger_id="trig-rel-restart",
        exit_quantity=4.0,
    )
    assert action.status == "OK", action.detail
    working = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-rel-restart-working",
        "order_intent_id": "exit-trig-rel-restart",
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "WORKING",
        "attempt_time": _exact("2026-09-18T16:30:00Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 4.0,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    assert _submit(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, working).status == "OK"
    assert (
        _terminal_attempt(
            writer,
            admission,
            order_id="exit-trig-rel-restart",
            tag="rel-restart",
            seq=1,
            ts="2026-09-18T16:31:00Z",
        ).status
        == "OK"
    )
    expected_outstanding = writer._outstanding_exit_quantity(admission.paper_trade_id)  # noqa: SLF001
    expected_capacity = writer._exit_capacity(admission.paper_trade_id)  # noqa: SLF001
    # The live-attempt rule holds before the restart too.
    assert expected_outstanding == 4.0
    db_path = tmp_path / "canonical.sqlite3"
    writer.close()

    reopened = CanonicalWriter(db_path)
    try:
        assert reopened._outstanding_exit_quantity(admission.paper_trade_id) == expected_outstanding  # noqa: SLF001
        assert reopened._exit_capacity(admission.paper_trade_id) == expected_capacity  # noqa: SLF001
    finally:
        reopened.close()
