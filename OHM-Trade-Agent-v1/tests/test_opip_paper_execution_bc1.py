"""B/C-1 Paper v2 ground-truth integration tests.

These tests exercise the canonical writer boundary only.  Paper v2 remains
inactive: there is no scheduler, exchange write authority, protection runtime,
or production trading path in this slice.
"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.writer import CanonicalWriter
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
    PAPER_QUOTE_EVIDENCE_RECORDED,
    PaperAdmissionRequest,
    quote_evidence_idempotency_key,
)
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    context_idempotency_key,
    context_identity,
)


def _exact(ts: str = "2026-09-18T16:00:00Z") -> dict:
    return {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": ts,
    }


def _context_payload() -> dict:
    payload = {
        "context_id": "pending",
        "candidate_id": "candidate-bc1",
        "episode_id": "episode-bc1",
        "evaluation_id": "evaluation-bc1",
        "instrument_version": "KRAKEN:SOLUSD:v1",
        "snapshot_id": "snapshot-bc1",
        "snapshot_hash": "snapshot-hash-bc1",
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
        "candidate_set_ref": "candidate-set-bc1",
        "portfolio_version_ref": None,
        "environment": "paper",
        "eligibility": True,
        "missingness": {},
        "source_availability_times": {},
        "evidence_eligibility_manifest": {},
        "schema_version": 1,
        "provenance": {
            "producing_component": "bc1-test",
            "artifact_or_build_id": "build-bc1",
            "process_instance_id": "proc-bc1",
            "emitted_at": "2026-09-18T15:59:59Z",
            "source_record_refs": ["source:bc1"],
            "schema_version": 1,
        },
    }
    payload["context_id"] = context_identity(payload)
    return payload


@pytest.fixture
def canonical(tmp_path):
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    context = _context_payload()
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
    writer, context = canonical
    admitted = writer.admit_paper_opportunity(
        _admission(context["context_id"], disposition_id="disp-capacity-seed")
    )
    assert admitted.disposition == "ADMITTED"

    capacity = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-capacity-reject",
            expected_version=1,
            position_limit=1,
        )
    )
    capital = writer.admit_paper_opportunity(
        _admission(
            context["context_id"],
            disposition_id="disp-capital-reject",
            quote_currency="USDT",
            equity_limit=400.0,
        )
    )

    assert capacity.status == "OK"
    assert capacity.disposition == "CAPACITY_REJECTED"
    assert capacity.reservation_id is None
    assert capacity.portfolio_version == 1

    assert capital.status == "OK"
    assert capital.disposition == "CAPITAL_REJECTED"
    assert capital.reservation_id is None
    assert capital.portfolio_version == 0

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
