"""B/C-1 Paper v2 ground-truth integration tests.

These tests exercise the canonical writer boundary only.  Paper v2 remains
inactive: there is no scheduler, exchange write authority, protection runtime,
or production trading path in this slice.
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
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    paper_evidence_idempotency_key,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_ADMISSION_REQUEST_RECORDED,
    PAPER_CAPITAL_POLICY_VERSION,
    PAPER_QUOTE_EVIDENCE_RECORDED,
    PaperAdmissionRequest,
    quote_evidence_idempotency_key,
    resolve_capital_policy,
)
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    context_idempotency_key,
    context_identity,
)
from app.opip.market.instrument_version_store import instrument_version_record_payload

#: The canonical instrument identity every B/C-1 fixture is bound to. Execution
#: evidence is validated against this registered version, not a self-declared
#: string, so the fixtures must register it before any attempt or fill.
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
NATIVE_SYMBOL = "SOL/USD"
CANONICAL_VENUE = "KRAKEN"


def _exact(ts: str = "2026-09-18T16:00:00Z") -> dict:
    return {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": ts,
    }


def _context_payload(
    *,
    suffix: str = "bc1",
    eligibility: bool = True,
    environment: str = "paper",
    instrument_version: str = INSTRUMENT_VERSION_ID,
) -> dict:
    payload = {
        "context_id": "pending",
        "candidate_id": f"candidate-{suffix}",
        "episode_id": f"episode-{suffix}",
        "evaluation_id": f"evaluation-{suffix}",
        "instrument_version": instrument_version,
        "snapshot_id": f"snapshot-{suffix}",
        "snapshot_hash": f"snapshot-hash-{suffix}",
        "evaluation_time": "2026-09-18T15:59:59Z",
        "evidence_cutoff": "2026-09-18T15:59:59Z",
        "consumed_input_watermark": {
            "history_epoch": 1,
            "local_sequence": 1,
        },
        "feature_version": "features-bc1",
        "policy_version": "policy-bc1",
        "detector_version": "detector-bc1",
        "forecast_version": "forecast-bc1",
        "candidate_set_ref": f"candidate-set-{suffix}",
        "portfolio_version_ref": None,
        "environment": environment,
        "eligibility": eligibility,
        "missingness": {},
        "source_availability_times": {},
        "evidence_eligibility_manifest": {},
        "schema_version": 1,
        "provenance": {
            "producing_component": "bc1-test",
            "artifact_or_build_id": f"build-{suffix}",
            "process_instance_id": f"proc-{suffix}",
            "emitted_at": "2026-09-18T15:59:59Z",
            "source_record_refs": [f"source:{suffix}"],
            "schema_version": 1,
        },
    }
    payload["context_id"] = context_identity(payload)
    return payload


def _seed_context(
    writer: CanonicalWriter,
    *,
    suffix: str,
    eligibility: bool = True,
    environment: str = "paper",
    instrument_version: str = INSTRUMENT_VERSION_ID,
) -> dict:
    context = _context_payload(
        suffix=suffix,
        eligibility=eligibility,
        environment=environment,
        instrument_version=instrument_version,
    )
    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=context_idempotency_key(
                context_id=context["context_id"]
            ),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=context,
        )
    )
    assert ack.status == "OK"
    return context


def _instrument_version() -> InstrumentVersion:
    return InstrumentVersion(
        venue=CANONICAL_VENUE,
        base_asset="SOL",
        quote_currency="USD",
        venue_instrument_id=NATIVE_SYMBOL,
        version=1,
        reference_data_version="kraken-ref-1",
        observed_at_utc=datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc),
    )


def _seed_instrument_version(writer: CanonicalWriter) -> InstrumentVersion:
    """Commit the canonical instrument identity the fixtures execute against.

    Instrument versions are ordinary canonical evidence, so binding quote
    ancestry to them reuses the existing authoritative mapping instead of
    introducing a parallel instrument registry.
    """
    version = _instrument_version()
    payload = instrument_version_record_payload(version)
    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=instrument_version_idempotency_key(
                instrument_version_id=version.instrument_version_id,
                reference_fingerprint=version.reference_fingerprint(),
            ),
            event_type=MARKET_INSTRUMENT_VERSION_RECORDED,
            payload=payload,
        )
    )
    assert ack.status == "OK"
    assert version.instrument_version_id == INSTRUMENT_VERSION_ID
    return version


@pytest.fixture
def canonical(tmp_path):
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    _seed_instrument_version(writer)
    context = _seed_context(writer, suffix="bc1")
    try:
        yield writer, context
    finally:
        writer.close()


def _admission(
    context_id: str,
    *,
    disposition_id: str,
    quote_currency: str = "USD",
    expected_version: int = 0,
    requested_capital: float = 500.0,
    reservation_amount: float = 500.0,
    equity_limit: float = 10_000.0,
    position_limit: int = 3,
) -> PaperAdmissionRequest:
    return PaperAdmissionRequest(
        disposition_id=disposition_id,
        decision_context_id=context_id,
        disposition_seq=0,
        quote_currency=quote_currency,
        requested_capital=requested_capital,
        disposition_time=_exact(),
        expected_portfolio_version=expected_version,
        capital_policy_version="paper-capital-v1",
        portfolio_equity_limit=equity_limit,
        portfolio_position_limit=position_limit,
        requested_reservation_amount=reservation_amount,
    )


def _rows(writer: CanonicalWriter, event_type: str) -> list[dict]:
    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        """
        SELECT payload_json FROM events
        WHERE event_type = ?
        ORDER BY history_epoch, local_sequence
        """,
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


def _order_payload(context_id: str, admission_ack, *, order_id: str = "order-1") -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "order_intent_id": order_id,
        "paper_trade_id": admission_ack.paper_trade_id,
        "decision_context_id": context_id,
        "intent_seq": 0,
        "intent_role": "ENTRY",
        "side": "BUY",
        "order_type": "MARKET",
        "requested_quantity": 5.0,
        "requested_notional": 500.0,
        "reason_code": "QUALIFIED_ENTRY",
        "intent_time": _exact("2026-09-18T16:00:01Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "reservation_id": admission_ack.reservation_id,
    }


def _submit_paper(writer: CanonicalWriter, event_type: str, payload: dict):
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


def test_admission_rejects_ineligible_or_nonpaper_context_without_reservation(
    canonical,
):
    writer, _ = canonical
    ineligible = _seed_context(
        writer,
        suffix="ineligible",
        eligibility=False,
    )
    nonpaper = _seed_context(
        writer,
        suffix="nonpaper",
        environment="live",
    )

    rejected_ineligible = writer.admit_paper_opportunity(
        _admission(
            ineligible["context_id"],
            disposition_id="disp-ineligible",
        )
    )
    rejected_nonpaper = writer.admit_paper_opportunity(
        _admission(
            nonpaper["context_id"],
            disposition_id="disp-nonpaper",
        )
    )

    assert rejected_ineligible.status == "REJECTED"
    assert rejected_ineligible.error_code == "DECISION_CONTEXT_INELIGIBLE"
    assert rejected_nonpaper.status == "REJECTED"
    assert rejected_nonpaper.error_code == "DECISION_CONTEXT_NOT_PAPER"
    assert _rows(writer, PAPER_ADMISSION_REQUEST_RECORDED) == []
    assert _rows(writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED) == []


def test_atomic_admission_allows_only_one_writer_of_expected_version_zero(canonical):
    writer, context = canonical
    requests = [
        _admission(context["context_id"], disposition_id="disp-race-a"),
        _admission(context["context_id"], disposition_id="disp-race-b"),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer.admit_paper_opportunity, requests))

    assert sorted((item.status, item.disposition) for item in results) == [
        ("OK", "ADMITTED"),
        ("REJECTED", None),
    ]
    stale = next(item for item in results if item.status == "REJECTED")
    assert stale.error_code == "STALE_PORTFOLIO_VERSION"
    assert len(_rows(writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)) == 1
    assert len(_rows(writer, PAPER_ADMISSION_REQUEST_RECORDED)) == 2


def test_usd_and_usdt_are_separate_portfolio_versions(canonical):
    writer, context = canonical
    usd = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-usd", quote_currency="USD")
    )
    usdt = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-usdt",
            quote_currency="USDT",
        )
    )

    assert usd.status == usdt.status == "OK"
    assert usd.disposition == usdt.disposition == "ADMITTED"
    assert usd.portfolio_version == 1
    assert usdt.portfolio_version == 1


def test_stale_admission_replay_and_payload_conflict_are_deterministic(canonical):
    writer, context = canonical
    first = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-first")
    )
    assert first.disposition == "ADMITTED"

    stale_request = _admission(
        context["context_id"],
        disposition_id="disp-stale",
        expected_version=0,
    )
    stale = writer.admit_paper_opportunity(stale_request)
    replay = writer.admit_paper_opportunity(stale_request)
    conflict = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-stale",
            expected_version=0,
            requested_capital=600.0,
            reservation_amount=600.0,
        )
    )

    assert stale.status == replay.status == "REJECTED"
    assert stale.error_code == replay.error_code == "STALE_PORTFOLIO_VERSION"
    assert stale.request_event_id == replay.request_event_id
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_capacity_and_capital_rejections_are_canonical_without_reservations(canonical):
    """Capacity and capital rejections stay canonical and reserve nothing.

    Rejections are now reached through the *authoritative* policy rather than by
    lowering caller-supplied limits, because a request can no longer change the
    limits the writer applies. Every assertion this test previously made is
    preserved: rejections are recorded, carry no reservation identity, and do not
    advance the portfolio version.
    """
    writer, context = canonical
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)

    # Fill the USD portfolio to the authoritative position limit.
    for index in range(policy.portfolio_position_limit):
        admitted = writer.admit_paper_opportunity(
            _admission(
                context["context_id"],
                disposition_id=f"disp-capacity-seed-{index}",
                expected_version=index,
            )
        )
        assert admitted.disposition == "ADMITTED"
    seeded_version = policy.portfolio_position_limit

    capacity = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-capacity-reject",
            expected_version=seeded_version,
        )
    )

    # A separate quote-currency portfolio reaches the capital limit through size
    # alone, so the capital gate (not capacity) is what rejects it.
    usdt_capital = policy.portfolio_equity_limit * 0.4
    for index in range(2):
        admitted = writer.admit_paper_opportunity(
            _admission(
                context["context_id"],
                disposition_id=f"disp-capital-seed-{index}",
                quote_currency="USDT",
                expected_version=index,
                requested_capital=usdt_capital,
                reservation_amount=usdt_capital,
            )
        )
        assert admitted.disposition == "ADMITTED"
    leftover = policy.portfolio_equity_limit - (usdt_capital * 2) + 1.0

    capital = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-capital-reject",
            quote_currency="USDT",
            expected_version=2,
            requested_capital=leftover,
            reservation_amount=leftover,
        )
    )

    assert capacity.status == "OK"
    assert capacity.disposition == "CAPACITY_REJECTED"
    assert capacity.reservation_id is None
    assert capacity.portfolio_version == seeded_version

    assert capital.status == "OK"
    assert capital.disposition == "CAPITAL_REJECTED"
    assert capital.reservation_id is None
    assert capital.portfolio_version == 2

    by_id = {
        row["disposition_id"]: row
        for row in _rows(writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)
    }
    for disposition_id in ("disp-capacity-reject", "disp-capital-reject"):
        assert "reservation_id" not in by_id[disposition_id]
        assert "paper_trade_id" not in by_id[disposition_id]


def test_raw_disposition_submit_cannot_bypass_atomic_admission(canonical):
    writer, context = canonical
    request = _admission(context["context_id"], disposition_id="disp-bypass")
    paper_trade_id = "PTV2:bypass"
    reservation_id = "RSV2:bypass"
    payload = {
        **request.as_dict(),
        "disposition": "ADMITTED",
        "reason_code": "BYPASS",
        "paper_trade_id": paper_trade_id,
        "reservation_id": reservation_id,
    }
    ack = writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=paper_evidence_idempotency_key(
                PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
                payload,
            ),
            event_type=PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
            payload=payload,
        )
    )
    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_order_attempt_fill_require_reservation_and_exact_level1_quote(canonical):
    writer, context = canonical
    admission = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-lineage")
    )
    assert admission.disposition == "ADMITTED"

    order = _order_payload(context["context_id"], admission)
    order_ack = _submit_paper(writer, PAPER_ORDER_INTENT_RECORDED, order)
    assert order_ack.status == "OK"

    attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-1",
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact("2026-09-18T16:00:02Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 5.0,
    }
    missing_quote = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        attempt,
    )
    assert missing_quote.status == "REJECTED"
    assert "Level-1" in str(missing_quote.detail)

    quote = {
        "schema_version": 1,
        "quote_evidence_id": "quote-1",
        "instrument_version": context["instrument_version"],
        "venue": "KRAKEN",
        "native_symbol": "SOL/USD",
        "quote_currency": "USD",
        "source_kind": "LEVEL_1_BOOK",
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": _exact("2026-09-18T16:00:02Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }
    quote_ack = _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote)
    assert quote_ack.status == "OK"

    attempt["market_evidence_ref"] = quote["quote_evidence_id"]
    attempt_ack = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        attempt,
    )
    assert attempt_ack.status == "OK"

    fill = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": "fill-1",
        "execution_attempt_id": attempt["execution_attempt_id"],
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "fill_seq": 0,
        "side": "BUY",
        "quantity": 5.0,
        "price": 100.0,
        "fee_cost": 1.0,
        "spread_cost": 0.5,
        "slippage_cost": 0.25,
        "other_supported_cost": 0.0,
        "fill_time": _exact("2026-09-18T16:00:03Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    fill_ack = _submit_paper(writer, PAPER_FILL_RECORDED, fill)
    assert fill_ack.status == "OK"

    changed = dict(fill)
    changed["price"] = 101.0
    conflict = _submit_paper(writer, PAPER_FILL_RECORDED, changed)
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_execution_attempt_cannot_accept_more_than_parent_order(canonical):
    writer, context = canonical
    admission = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-attempt-cap")
    )
    order = _order_payload(
        context["context_id"],
        admission,
        order_id="order-attempt-cap",
    )
    assert _submit_paper(writer, PAPER_ORDER_INTENT_RECORDED, order).status == "OK"

    quote = {
        "schema_version": 1,
        "quote_evidence_id": "quote-attempt-cap",
        "instrument_version": context["instrument_version"],
        "venue": "KRAKEN",
        "native_symbol": "SOL/USD",
        "quote_currency": "USD",
        "source_kind": "LEVEL_1_BOOK",
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": _exact("2026-09-18T16:00:01Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }
    assert _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"

    attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-oversized",
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact("2026-09-18T16:00:02Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 6.0,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    ack = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert ack.status == "REJECTED"
    assert "accepted_quantity exceeds" in str(ack.detail)


def test_fill_requires_fillable_attempt_and_respects_attempt_quantity(canonical):
    writer, context = canonical
    admission = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-fill-cap")
    )
    order = _order_payload(
        context["context_id"],
        admission,
        order_id="order-fill-cap",
    )
    assert _submit_paper(writer, PAPER_ORDER_INTENT_RECORDED, order).status == "OK"

    quote = {
        "schema_version": 1,
        "quote_evidence_id": "quote-fill-cap",
        "instrument_version": context["instrument_version"],
        "venue": "KRAKEN",
        "native_symbol": "SOL/USD",
        "quote_currency": "USD",
        "source_kind": "LEVEL_1_BOOK",
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": _exact("2026-09-18T16:00:01Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }
    assert _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"

    rejected_attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-rejected",
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "REJECTED",
        "attempt_time": _exact("2026-09-18T16:00:02Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "rejection_reason": "VENUE_REJECTED",
    }
    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            rejected_attempt,
        ).status
        == "OK"
    )
    invalid_fill = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": "fill-rejected-attempt",
        "execution_attempt_id": rejected_attempt["execution_attempt_id"],
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "fill_seq": 0,
        "side": "BUY",
        "quantity": 1.0,
        "price": 100.0,
        "fee_cost": 0.2,
        "spread_cost": 0.1,
        "slippage_cost": 0.05,
        "other_supported_cost": 0.0,
        "fill_time": _exact("2026-09-18T16:00:03Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    invalid_ack = _submit_paper(writer, PAPER_FILL_RECORDED, invalid_fill)
    assert invalid_ack.status == "REJECTED"
    assert "non-fillable" in str(invalid_ack.detail)

    accepted_attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-small",
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 1,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact("2026-09-18T16:00:02Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 2.0,
        "market_evidence_ref": quote["quote_evidence_id"],
    }
    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            accepted_attempt,
        ).status
        == "OK"
    )

    first_fill = {
        **invalid_fill,
        "fill_id": "fill-small-1",
        "execution_attempt_id": accepted_attempt["execution_attempt_id"],
        "quantity": 1.5,
    }
    assert _submit_paper(writer, PAPER_FILL_RECORDED, first_fill).status == "OK"

    second_fill = {
        **first_fill,
        "fill_id": "fill-small-2",
        "fill_seq": 1,
        "quantity": 1.0,
    }
    oversized = _submit_paper(writer, PAPER_FILL_RECORDED, second_fill)
    assert oversized.status == "REJECTED"
    assert "accepted_quantity" in str(oversized.detail)


def test_future_or_temporally_ambiguous_quote_cannot_justify_execution(canonical):
    writer, context = canonical
    admission = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-quote-causality")
    )
    order = _order_payload(
        context["context_id"],
        admission,
        order_id="order-quote-causality",
    )
    assert _submit_paper(writer, PAPER_ORDER_INTENT_RECORDED, order).status == "OK"

    future_quote = {
        "schema_version": 1,
        "quote_evidence_id": "quote-future",
        "instrument_version": context["instrument_version"],
        "venue": "KRAKEN",
        "native_symbol": "SOL/USD",
        "quote_currency": "USD",
        "source_kind": "LEVEL_1_BOOK",
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": _exact("2026-09-18T16:00:04Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }
    assert (
        _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, future_quote).status
        == "OK"
    )
    future_attempt = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": "attempt-future-quote",
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": 0,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact("2026-09-18T16:00:02Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": 5.0,
        "market_evidence_ref": future_quote["quote_evidence_id"],
    }
    future_ack = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        future_attempt,
    )
    assert future_ack.status == "REJECTED"
    assert "not proven available" in str(future_ack.detail)

    valid_quote = {
        **future_quote,
        "quote_evidence_id": "quote-before-attempt",
        "quote_time": _exact("2026-09-18T16:00:01Z"),
    }
    assert (
        _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, valid_quote).status
        == "OK"
    )
    valid_attempt = {
        **future_attempt,
        "execution_attempt_id": "attempt-before-fill",
        "market_evidence_ref": valid_quote["quote_evidence_id"],
    }
    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            valid_attempt,
        ).status
        == "OK"
    )
    future_fill = {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": "fill-future-quote",
        "execution_attempt_id": valid_attempt["execution_attempt_id"],
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "fill_seq": 0,
        "side": "BUY",
        "quantity": 1.0,
        "price": 100.0,
        "fee_cost": 0.2,
        "spread_cost": 0.1,
        "slippage_cost": 0.05,
        "other_supported_cost": 0.0,
        "fill_time": _exact("2026-09-18T16:00:03Z"),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": future_quote["quote_evidence_id"],
    }
    future_fill_ack = _submit_paper(
        writer,
        PAPER_FILL_RECORDED,
        future_fill,
    )
    assert future_fill_ack.status == "REJECTED"
    assert "not proven available" in str(future_fill_ack.detail)

    ambiguous_quote = {
        **future_quote,
        "quote_evidence_id": "quote-overlap",
        "quote_time": {
            "precision": "BOUNDED",
            "basis": "LOCALLY_OBSERVED",
            "window_start": "2026-09-18T16:00:01Z",
            "window_end": "2026-09-18T16:00:03Z",
        },
    }
    assert (
        _submit_paper(
            writer,
            PAPER_QUOTE_EVIDENCE_RECORDED,
            ambiguous_quote,
        ).status
        == "OK"
    )
    ambiguous_attempt = {
        **future_attempt,
        "execution_attempt_id": "attempt-overlap-quote",
        # A fresh monotonic sequence: reusing attempt_seq 0 would be rejected by
        # the sibling-ordering invariant before reaching the causality check this
        # test exists to prove.
        "attempt_seq": 1,
        "market_evidence_ref": ambiguous_quote["quote_evidence_id"],
    }
    ambiguous_ack = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        ambiguous_attempt,
    )
    assert ambiguous_ack.status == "REJECTED"
    assert "not proven available" in str(ambiguous_ack.detail)


def test_quote_evidence_cannot_be_ohlc_or_unknown_time(canonical):
    writer, context = canonical
    quote = {
        "schema_version": 1,
        "quote_evidence_id": "quote-bad",
        "instrument_version": context["instrument_version"],
        "venue": "KRAKEN",
        "native_symbol": "SOL/USD",
        "quote_currency": "USD",
        "source_kind": "OHLC",
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": _exact(),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }
    with pytest.raises(ValueError, match="LEVEL_1_BOOK"):
        quote_evidence_idempotency_key(quote)

    quote["source_kind"] = "LEVEL_1_BOOK"
    quote["quote_time"] = {
        "precision": "UNKNOWN",
        "basis": "LOCALLY_OBSERVED",
        "reason": "no defensible time",
    }
    with pytest.raises(ValueError, match="exact or bounded"):
        quote_evidence_idempotency_key(quote)


def test_canonical_store_contains_no_new_mutable_portfolio_ledger(canonical):
    writer, _ = canonical
    tables = {
        row[0]
        for row in writer._conn.execute(  # noqa: SLF001 - test-only inspection
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "paper_portfolios" not in tables
    assert "paper_reservations" not in tables


# ---------------------------------------------------------------------------
# Shared execution-lineage helpers
# ---------------------------------------------------------------------------


def _quote_payload(
    context: dict,
    *,
    quote_id: str,
    venue: str = CANONICAL_VENUE,
    native_symbol: str = NATIVE_SYMBOL,
    quote_currency: str = "USD",
    quote_time: str = "2026-09-18T16:00:01Z",
    instrument_version: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "quote_evidence_id": quote_id,
        "instrument_version": (
            instrument_version if instrument_version is not None
            else context["instrument_version"]
        ),
        "venue": venue,
        "native_symbol": native_symbol,
        "quote_currency": quote_currency,
        "source_kind": "LEVEL_1_BOOK",
        "best_bid": 99.9,
        "best_ask": 100.1,
        "bid_quantity": 10.0,
        "ask_quantity": 12.0,
        "quote_time": _exact(quote_time),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
    }


def _attempt_payload(
    order: dict,
    admission,
    *,
    attempt_id: str,
    attempt_seq: int,
    quote_id: str,
    accepted_quantity: float = 5.0,
    attempt_time: str = "2026-09-18T16:00:02Z",
) -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "execution_attempt_id": attempt_id,
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "attempt_seq": attempt_seq,
        "execution_state": "ACCEPTED",
        "attempt_time": _exact(attempt_time),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "accepted_quantity": accepted_quantity,
        "market_evidence_ref": quote_id,
    }


def _fill_payload(
    order: dict,
    admission,
    *,
    fill_id: str,
    fill_seq: int,
    attempt_id: str,
    quote_id: str,
    quantity: float = 1.0,
    fill_time: str = "2026-09-18T16:00:03Z",
) -> dict:
    return {
        "schema_version": 1,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": fill_id,
        "execution_attempt_id": attempt_id,
        "order_intent_id": order["order_intent_id"],
        "paper_trade_id": admission.paper_trade_id,
        "fill_seq": fill_seq,
        "side": "BUY",
        "quantity": quantity,
        "price": 100.0,
        "fee_cost": 0.2,
        "spread_cost": 0.1,
        "slippage_cost": 0.05,
        "other_supported_cost": 0.0,
        "fill_time": _exact(fill_time),
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": quote_id,
    }


def _lineage_fixture(
    writer: CanonicalWriter,
    context: dict,
    *,
    disposition_id: str = "disp-lineage-fixture",
    order_id: str = "order-lineage-fixture",
    quote_id: str = "quote-lineage-fixture",
):
    """Admit, commit the canonical quote, and commit the parent order intent."""
    admission = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id=disposition_id)
    )
    assert admission.disposition == "ADMITTED"
    order = _order_payload(context["context_id"], admission, order_id=order_id)
    assert _submit_paper(writer, PAPER_ORDER_INTENT_RECORDED, order).status == "OK"
    assert (
        _submit_paper(
            writer,
            PAPER_QUOTE_EVIDENCE_RECORDED,
            _quote_payload(context, quote_id=quote_id),
        ).status
        == "OK"
    )
    return admission, order


# ---------------------------------------------------------------------------
# Finding 1 — the writer owns the capital/capacity gate
# ---------------------------------------------------------------------------


def test_admission_matching_authoritative_policy_is_unchanged(canonical):
    """Control: a request that matches the policy still admits."""
    writer, context = canonical
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)
    ack = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-policy-match",
            equity_limit=policy.portfolio_equity_limit,
            position_limit=policy.portfolio_position_limit,
        )
    )
    assert ack.status == "OK"
    assert ack.disposition == "ADMITTED"


def test_caller_cannot_obtain_admission_by_raising_the_equity_limit(canonical):
    writer, context = canonical
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)
    ack = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-relaxed-equity",
            equity_limit=policy.portfolio_equity_limit * 1000,
        )
    )
    assert ack.status == "REJECTED"
    assert ack.error_code == "CAPITAL_POLICY_MISMATCH"
    assert ack.disposition is None
    assert _rows(writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED) == []


def test_caller_cannot_obtain_admission_by_raising_the_position_limit(canonical):
    """A relaxed position limit must not buy capacity the policy denies."""
    writer, context = canonical
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)
    for index in range(policy.portfolio_position_limit):
        assert (
            writer.admit_paper_opportunity(
                _admission(
                    context["context_id"],
                    disposition_id=f"disp-policy-capacity-{index}",
                    expected_version=index,
                )
            ).disposition
            == "ADMITTED"
        )

    relaxed = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-relaxed-position",
            expected_version=policy.portfolio_position_limit,
            position_limit=policy.portfolio_position_limit + 50,
        )
    )
    assert relaxed.status == "REJECTED"
    assert relaxed.error_code == "CAPITAL_POLICY_MISMATCH"
    assert relaxed.disposition is None
    # The authoritative gate still rejects at capacity, so the caller gained
    # nothing by asking for more room.
    authoritative = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-authoritative-capacity",
            expected_version=policy.portfolio_position_limit,
        )
    )
    assert authoritative.disposition == "CAPACITY_REJECTED"


@pytest.mark.parametrize("field_name", ["equity", "position"])
def test_policy_value_mismatch_fails_closed_in_either_direction(canonical, field_name):
    """Neither a looser nor a stricter limit may replace the authoritative one."""
    writer, context = canonical
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)
    for multiplier in (0.01, 100.0):
        overrides = {}
        if field_name == "equity":
            overrides["equity_limit"] = policy.portfolio_equity_limit * multiplier
        else:
            overrides["position_limit"] = max(
                1, int(policy.portfolio_position_limit * multiplier)
            )
        ack = writer.admit_paper_opportunity(
            _admission(
                context["context_id"],
                disposition_id=f"disp-mismatch-{field_name}-{multiplier}",
                **overrides,
            )
        )
        assert ack.status == "REJECTED"
        assert ack.error_code == "CAPITAL_POLICY_MISMATCH"
        assert ack.disposition is None


def test_unknown_capital_policy_version_fails_closed(canonical):
    """An unsupported version must never reach an ADMITTED decision."""
    writer, context = canonical
    ack = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-unknown-policy",
        )
    )
    assert ack.status == "OK"

    request = _admission(
        context["context_id"],
        disposition_id="disp-unknown-policy",
        expected_version=1,
    )
    object.__setattr__(request, "capital_policy_version", "paper-capital-v99")
    unknown = writer.admit_paper_opportunity(request)
    assert unknown.status == "REJECTED"
    assert unknown.error_code == "UNSUPPORTED_CAPITAL_POLICY_VERSION"
    assert unknown.disposition is None


def test_policy_rejection_commits_no_admission_evidence(canonical):
    """A rejected policy must not leave a request or disposition record behind."""
    writer, context = canonical
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)
    rejected = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-no-evidence",
            equity_limit=policy.portfolio_equity_limit + 1.0,
        )
    )
    assert rejected.status == "REJECTED"
    assert _rows(writer, PAPER_ADMISSION_REQUEST_RECORDED) == []
    assert _rows(writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED) == []

    # The disposition identity is therefore still available for a valid request,
    # so a policy rejection cannot poison later legitimate admission.
    valid = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-no-evidence")
    )
    assert valid.disposition == "ADMITTED"


def test_valid_admission_retry_remains_idempotent(canonical):
    """Idempotency/retry semantics are unchanged by the policy gate."""
    writer, context = canonical
    request = _admission(context["context_id"], disposition_id="disp-policy-retry")
    first = writer.admit_paper_opportunity(request)
    replay = writer.admit_paper_opportunity(request)
    assert first.status == "OK"
    assert first.disposition == "ADMITTED"
    # A replay resolves to the committed result rather than deciding again.
    assert replay.status == "DUPLICATE_OK"
    assert replay.disposition == first.disposition
    assert replay.disposition_event_id == first.disposition_event_id
    assert replay.reservation_id == first.reservation_id
    assert len(_rows(writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)) == 1


# ---------------------------------------------------------------------------
# Finding 2 — quote evidence is bound to the canonical instrument identity
# ---------------------------------------------------------------------------


def test_correctly_bound_level1_quote_is_accepted(canonical):
    """Control: a quote matching every canonical identity dimension is accepted."""
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-bound",
        attempt_seq=0,
        quote_id="quote-lineage-fixture",
    )
    ack = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert ack.status == "OK"


def test_quote_from_the_wrong_venue_is_rejected(canonical):
    """Correct instrument version plus a different venue must not qualify."""
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    foreign = _quote_payload(context, quote_id="quote-wrong-venue", venue="COINBASE")
    assert _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, foreign).status == "OK"

    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-wrong-venue",
        attempt_seq=0,
        quote_id=foreign["quote_evidence_id"],
    )
    ack = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert ack.status == "REJECTED"
    assert "venue" in str(ack.detail)


def test_quote_with_the_wrong_native_symbol_is_rejected(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    foreign = _quote_payload(
        context,
        quote_id="quote-wrong-symbol",
        native_symbol="SOL/USDT",
    )
    assert _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, foreign).status == "OK"

    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-wrong-symbol",
        attempt_seq=0,
        quote_id=foreign["quote_evidence_id"],
    )
    ack = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert ack.status == "REJECTED"
    assert "native_symbol" in str(ack.detail)


def test_copied_instrument_version_with_foreign_identity_is_rejected(canonical):
    """The reported defect: a copied instrument-version string is not enough.

    The quote reproduces the context's exact ``instrument_version`` (the only
    thing previously compared) while describing a different venue and symbol. It
    must still be rejected, because ancestry cannot be satisfied by a
    self-declared compatible string.
    """
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    copied = _quote_payload(
        context,
        quote_id="quote-copied-version",
        venue="BINANCE",
        native_symbol="SOL/USDT",
    )
    assert copied["instrument_version"] == context["instrument_version"]
    assert _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, copied).status == "OK"

    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-copied-version",
        attempt_seq=0,
        quote_id=copied["quote_evidence_id"],
    )
    ack = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert ack.status == "REJECTED"
    assert "canonical instrument version" in str(ack.detail)


def test_quote_with_correct_venue_and_symbol_but_wrong_currency_is_rejected(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    wrong_currency = _quote_payload(
        context,
        quote_id="quote-wrong-currency",
        quote_currency="USDT",
    )
    assert (
        _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, wrong_currency).status
        == "OK"
    )

    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-wrong-currency",
        attempt_seq=0,
        quote_id=wrong_currency["quote_evidence_id"],
    )
    ack = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert ack.status == "REJECTED"
    assert "quote_currency" in str(ack.detail)


def test_fill_inherits_the_same_quote_binding(canonical):
    """Fills must be bound too, not only attempts."""
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-bound-fill",
        attempt_seq=0,
        quote_id="quote-lineage-fixture",
    )
    assert _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt).status == "OK"

    foreign = _quote_payload(context, quote_id="quote-fill-foreign", venue="COINBASE")
    assert _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, foreign).status == "OK"
    fill = _fill_payload(
        order,
        admission,
        fill_id="fill-foreign-quote",
        fill_seq=0,
        attempt_id=attempt["execution_attempt_id"],
        quote_id=foreign["quote_evidence_id"],
    )
    ack = _submit_paper(writer, PAPER_FILL_RECORDED, fill)
    assert ack.status == "REJECTED"
    assert "venue" in str(ack.detail)


def test_unregistered_instrument_version_cannot_support_execution(canonical):
    """If the instrument identity cannot be proven canonically, reject."""
    writer, context = canonical
    unregistered = _seed_context(
        writer,
        suffix="unregistered",
        instrument_version="INSTR:kraken:ETH:USD:1",
    )
    admission = writer.admit_paper_opportunity(
        _admission(unregistered["context_id"], disposition_id="disp-unregistered")
    )
    assert admission.disposition == "ADMITTED"
    order = _order_payload(
        unregistered["context_id"],
        admission,
        order_id="order-unregistered",
    )
    assert _submit_paper(writer, PAPER_ORDER_INTENT_RECORDED, order).status == "OK"
    quote = _quote_payload(
        unregistered,
        quote_id="quote-unregistered",
        native_symbol="ETH/USD",
    )
    assert _submit_paper(writer, PAPER_QUOTE_EVIDENCE_RECORDED, quote).status == "OK"

    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-unregistered",
        attempt_seq=0,
        quote_id=quote["quote_evidence_id"],
    )
    ack = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert ack.status == "REJECTED"
    assert "not a registered canonical instrument version" in str(ack.detail)


# ---------------------------------------------------------------------------
# Finding 3 — sibling sequences have one unambiguous ordering
# ---------------------------------------------------------------------------


def test_valid_monotonic_sibling_sequences_are_accepted(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    ack0 = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        _attempt_payload(
            order,
            admission,
            attempt_id="attempt-seq-0",
            attempt_seq=0,
            quote_id="quote-lineage-fixture",
        ),
    )
    ack1 = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        _attempt_payload(
            order,
            admission,
            attempt_id="attempt-seq-1",
            attempt_seq=1,
            quote_id="quote-lineage-fixture",
        ),
    )
    assert (ack0.status, ack1.status) == ("OK", "OK")

    fill0 = _submit_paper(
        writer,
        PAPER_FILL_RECORDED,
        _fill_payload(
            order,
            admission,
            fill_id="fill-seq-0",
            fill_seq=0,
            attempt_id="attempt-seq-1",
            quote_id="quote-lineage-fixture",
        ),
    )
    fill1 = _submit_paper(
        writer,
        PAPER_FILL_RECORDED,
        _fill_payload(
            order,
            admission,
            fill_id="fill-seq-1",
            fill_seq=1,
            attempt_id="attempt-seq-1",
            quote_id="quote-lineage-fixture",
            fill_time="2026-09-18T16:00:04Z",
        ),
    )
    assert (fill0.status, fill1.status) == ("OK", "OK")


def test_duplicate_attempt_seq_is_rejected(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    first = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        _attempt_payload(
            order,
            admission,
            attempt_id="attempt-dup-a",
            attempt_seq=0,
            quote_id="quote-lineage-fixture",
        ),
    )
    assert first.status == "OK"
    duplicate = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        _attempt_payload(
            order,
            admission,
            attempt_id="attempt-dup-b",
            attempt_seq=0,
            quote_id="quote-lineage-fixture",
        ),
    )
    assert duplicate.status == "REJECTED"
    assert "already committed" in str(duplicate.detail)
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1


def test_regressing_attempt_seq_is_rejected(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            _attempt_payload(
                order,
                admission,
                attempt_id="attempt-high",
                attempt_seq=5,
                quote_id="quote-lineage-fixture",
            ),
        ).status
        == "OK"
    )
    regressing = _submit_paper(
        writer,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        _attempt_payload(
            order,
            admission,
            attempt_id="attempt-low",
            attempt_seq=2,
            quote_id="quote-lineage-fixture",
        ),
    )
    assert regressing.status == "REJECTED"
    assert "regresses" in str(regressing.detail)
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1


def test_attempt_sequence_is_scoped_per_order_intent(canonical):
    """The same sequence value is legal for a different parent order."""
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context, order_id="order-scope-a")
    second_order = _order_payload(
        context["context_id"],
        admission,
        order_id="order-scope-b",
    )
    assert _submit_paper(writer, PAPER_ORDER_INTENT_RECORDED, second_order).status == "OK"

    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            _attempt_payload(
                order,
                admission,
                attempt_id="attempt-scope-a",
                attempt_seq=0,
                quote_id="quote-lineage-fixture",
            ),
        ).status
        == "OK"
    )
    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            _attempt_payload(
                second_order,
                admission,
                attempt_id="attempt-scope-b",
                attempt_seq=0,
                quote_id="quote-lineage-fixture",
            ),
        ).status
        == "OK"
    )


def test_duplicate_fill_seq_is_rejected(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    attempt_id = "attempt-fill-dup"
    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            _attempt_payload(
                order,
                admission,
                attempt_id=attempt_id,
                attempt_seq=0,
                quote_id="quote-lineage-fixture",
            ),
        ).status
        == "OK"
    )
    first = _submit_paper(
        writer,
        PAPER_FILL_RECORDED,
        _fill_payload(
            order,
            admission,
            fill_id="fill-dup-a",
            fill_seq=0,
            attempt_id=attempt_id,
            quote_id="quote-lineage-fixture",
        ),
    )
    assert first.status == "OK"
    duplicate = _submit_paper(
        writer,
        PAPER_FILL_RECORDED,
        _fill_payload(
            order,
            admission,
            fill_id="fill-dup-b",
            fill_seq=0,
            attempt_id=attempt_id,
            quote_id="quote-lineage-fixture",
        ),
    )
    assert duplicate.status == "REJECTED"
    assert "already committed" in str(duplicate.detail)
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 1


def test_regressing_fill_seq_is_rejected(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    attempt_id = "attempt-fill-regress"
    assert (
        _submit_paper(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            _attempt_payload(
                order,
                admission,
                attempt_id=attempt_id,
                attempt_seq=0,
                quote_id="quote-lineage-fixture",
            ),
        ).status
        == "OK"
    )
    assert (
        _submit_paper(
            writer,
            PAPER_FILL_RECORDED,
            _fill_payload(
                order,
                admission,
                fill_id="fill-high",
                fill_seq=3,
                attempt_id=attempt_id,
                quote_id="quote-lineage-fixture",
            ),
        ).status
        == "OK"
    )
    regressing = _submit_paper(
        writer,
        PAPER_FILL_RECORDED,
        _fill_payload(
            order,
            admission,
            fill_id="fill-low",
            fill_seq=1,
            attempt_id=attempt_id,
            quote_id="quote-lineage-fixture",
        ),
    )
    assert regressing.status == "REJECTED"
    assert "regresses" in str(regressing.detail)


def test_same_identity_identical_payload_retry_stays_idempotent(canonical):
    """The sequence rule must not break ACK-loss retry of a committed record."""
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-retry",
        attempt_seq=0,
        quote_id="quote-lineage-fixture",
    )
    first = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    retry = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt)
    assert first.status == "OK"
    assert retry.status == "DUPLICATE_OK"
    assert retry.event_id == first.event_id
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1


def test_same_identity_different_payload_remains_conflict(canonical):
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    attempt = _attempt_payload(
        order,
        admission,
        attempt_id="attempt-conflict",
        attempt_seq=0,
        quote_id="quote-lineage-fixture",
    )
    assert _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, attempt).status == "OK"
    changed = {**attempt, "accepted_quantity": 4.0}
    conflict = _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, changed)
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_concurrent_sibling_attempts_cannot_duplicate_a_sequence(canonical):
    """Serialization must make the duplicate sequence unreachable, not racy."""
    writer, context = canonical
    admission, order = _lineage_fixture(writer, context)
    attempts = [
        _attempt_payload(
            order,
            admission,
            attempt_id=f"attempt-race-{index}",
            attempt_seq=0,
            quote_id="quote-lineage-fixture",
        )
        for index in range(2)
    ]

    def _submit(payload: dict):
        return _submit_paper(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_submit, attempts))

    statuses = sorted(item.status for item in results)
    assert statuses == ["OK", "REJECTED"]
    rejected = next(item for item in results if item.status == "REJECTED")
    assert "already committed" in str(rejected.detail)
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1
