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
    """PLANNED -> TRIGGERED is not a supported runtime transition."""
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="state-invalid")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    ack = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-a", to_state="TRIGGERED"),
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


def test_triggered_state_requires_committed_trigger_evidence(writer_env):
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="state-trig")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    # No trigger exists yet, so DEGRADED -> TRIGGERED is refused.
    assert _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-a", event_id="pstate-deg", seq=1,
               from_state="ACTIVE", to_state="DEGRADED"),
    ).status == "OK"
    ack = _submit(
        writer,
        PAPER_PROTECTION_STATE_RECORDED,
        _state(admission, plan_id="plan-a", event_id="pstate-trig", seq=2,
               from_state="DEGRADED", to_state="TRIGGERED"),
    )
    assert ack.status == "REJECTED"
    assert "without committed trigger evidence" in str(ack.detail)


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
    admission, quote = _entry_trade(writer, context, tag=tag)
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    return admission, quote


def test_trigger_requires_armed_plan(writer_env):
    writer, context = writer_env
    admission, _ = _entry_trade(writer, context, tag="trig-unarmed")
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    ack = _submit(writer, PAPER_PROTECTION_TRIGGER_RECORDED, _trigger(admission, plan_id="plan-a"))
    assert ack.status == "REJECTED"
    assert "armed" in str(ack.detail)


def test_stop_trigger_requires_canonical_market_evidence(writer_env):
    """Missing required evidence fails closed rather than firing."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="trig-noevidence")
    ack = _submit(
        writer, PAPER_PROTECTION_TRIGGER_RECORDED, _trigger(admission, trigger_id="trig-none")
    )
    assert ack.status == "REJECTED"
    assert "Level-1 market evidence" in str(ack.detail)


def test_stop_trigger_rejects_quote_from_wrong_venue(writer_env):
    """Quote ancestry binding from B/C-1 applies to protection triggers too."""
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="trig-venue")
    foreign = _quote(context, quote_id="quote-foreign", venue="COINBASE")
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, foreign).status == "OK"
    ack = _submit(
        writer,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        _trigger(admission, trigger_id="trig-venue", quote_id=foreign["quote_evidence_id"]),
    )
    assert ack.status == "REJECTED"
    assert "venue" in str(ack.detail)


def test_trigger_cannot_cite_future_quote_evidence(writer_env):
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="trig-future")
    future = _quote(context, quote_id="quote-future", ts="2026-09-18T16:00:09Z")
    assert _submit(writer, PAPER_QUOTE_EVIDENCE_RECORDED, future).status == "OK"
    ack = _submit(
        writer,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        _trigger(
            admission,
            trigger_id="trig-future",
            quote_id=future["quote_evidence_id"],
            ts="2026-09-18T16:00:04Z",
        ),
    )
    assert ack.status == "REJECTED"
    assert "not proven available" in str(ack.detail)


def test_time_trigger_requires_exact_temporal_evidence(writer_env):
    writer, context = writer_env
    admission, _ = _armed_trade(writer, context, tag="trig-time")
    ok = _submit(
        writer,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        _trigger(admission, trigger_id="trig-time", trigger_type="TIME"),
    )
    assert ok.status == "OK", ok.detail

    bounded = _trigger(
        admission,
        trigger_id="trig-time-bounded",
        trigger_type="TIME",
        # A fresh monotonic sequence: reusing trigger_seq 0 would be rejected by
        # the sibling-ordering invariant before reaching the temporal rule this
        # test exists to prove.
        seq=1,
    )
    bounded["trigger_time"] = {
        "precision": "BOUNDED",
        "basis": "MODEL_ASSIGNED",
        "window_start": "2026-09-18T16:00:04Z",
        "window_end": "2026-09-18T16:00:05Z",
    }
    ack = _submit(writer, PAPER_PROTECTION_TRIGGER_RECORDED, bounded)
    assert ack.status == "REJECTED"
    assert "exact temporal evidence" in str(ack.detail)


def test_trigger_sequence_integrity(writer_env):
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="trig-seq")
    first = _trigger(admission, trigger_id="trig-a", seq=0, quote_id=quote["quote_evidence_id"])
    assert _submit(writer, PAPER_PROTECTION_TRIGGER_RECORDED, first).status == "OK"
    dup = _submit(
        writer,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        _trigger(admission, trigger_id="trig-b", seq=0, quote_id=quote["quote_evidence_id"]),
    )
    assert dup.status == "REJECTED"
    assert "already committed" in str(dup.detail)


def test_trigger_alone_changes_nothing_economically(writer_env):
    """A trigger is not an exit: no quantity, P/L or terminal state changes."""
    writer, context = writer_env
    admission, quote = _armed_trade(writer, context, tag="trig-noexit")
    before = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001

    assert _submit(
        writer,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        _trigger(admission, trigger_id="trig-noexit", quote_id=quote["quote_evidence_id"]),
    ).status == "OK"

    after = writer._canonical_fill_totals(admission.paper_trade_id)  # noqa: SLF001
    assert after == before
    assert after["remaining_quantity"] == 5.0
    # No reconciliation exists merely because protection triggered.
    assert _rows(writer, PAPER_RECONCILIATION_RECORDED) == []


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
    writer, context = writer_env
    admission, quote = _round_trip(writer, context, tag="release-partial", exit_quantity=2.0)
    assert _submit(writer, PAPER_PROTECTION_PLAN_RECORDED, _plan(admission, plan_id="plan-a")).status == "OK"
    assert _submit(
        writer, PAPER_PROTECTION_STATE_RECORDED, _state(admission, plan_id="plan-a")
    ).status == "OK"
    assert _submit(
        writer,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        _trigger(admission, trigger_id="trig-partial", quote_id=quote["quote_evidence_id"]),
    ).status == "OK"
    # Still reserved through plan, activation, trigger and a partial exit.
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
    assert _submit(
        writer,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        _trigger(admission, trigger_id="trig-restart", quote_id=quote["quote_evidence_id"]),
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
