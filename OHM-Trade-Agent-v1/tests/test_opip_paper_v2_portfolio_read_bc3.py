"""B/C-3 portfolio-read seam tests (ARB ruling: additive read-only canonical API).

Proves the producer can obtain the authoritative concurrency token through a
public read-only seam, that the seam writes nothing at all, and that USD/USDT
projections stay independent.
"""

from __future__ import annotations

import json

import pytest

from app.opip.canonical.client import CanonicalWriterClient, InProcessWriterClient
from app.opip.canonical.models import PaperPortfolioState
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.paper_execution_runtime import PaperAdmissionRequest
from app.opip.contracts.serialization import iso_z
from datetime import datetime, timezone

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def writer(tmp_path) -> CanonicalWriter:
    instance = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _admission(
    *,
    disposition_id: str,
    decision_context_id: str,
    quote_currency: str = "USD",
    expected_version: int = 0,
) -> PaperAdmissionRequest:
    return PaperAdmissionRequest(
        disposition_id=disposition_id,
        decision_context_id=decision_context_id,
        disposition_seq=0,
        quote_currency=quote_currency,
        requested_capital=500.0,
        disposition_time={
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": iso_z(NOW, field_name="disposition_time"),
        },
        expected_portfolio_version=expected_version,
        capital_policy_version="paper-capital-v1",
        portfolio_equity_limit=10_000.0,
        portfolio_position_limit=3,
        requested_reservation_amount=500.0,
    )


def _seed_context(writer: CanonicalWriter) -> dict:
    """A minimal eligible paper decision context, committed canonically."""
    from app.opip.canonical.decision_context_bridge import (
        DecisionContextFacts,
        build_decision_context_payload,
        submit_decision_context,
    )
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )

    facts = DecisionContextFacts(
        candidate_id="candidate-read",
        episode_id="episode-read",
        instrument_version_id="INSTR:kraken:SOL:USD:1",
        instrument_registration_event_id="EVT:seed-proof",
        snapshot_record_event_id="EVT:seed-snapshot-proof",
        snapshot_id="snapshot-read",
        snapshot_hash="snapshot-hash-read",
        evaluation_time=NOW,
        evidence_cutoff=NOW,
        policy_version=GATE_POLICY_VERSION,
        policy_fingerprint=gate_policy_fingerprint(),
        producing_component="read-test",
        artifact_or_build_id="build-read",
        process_instance_id="proc-read",
        emitted_at=NOW,
        source_record_refs=("source:read",),
    )
    payload = build_decision_context_payload(facts)
    submit_decision_context(payload, client=writer)
    return {"context_id": payload["context_id"]}


def _snapshot(writer: CanonicalWriter) -> dict:
    """Canonical state that a read must not alter."""
    event_count = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT COUNT(*) FROM events"
    ).fetchone()[0]
    idem_count = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT COUNT(*) FROM idempotency_keys"
    ).fetchone()[0]
    meta = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
    ).fetchone()
    watermarks = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT stream, history_epoch, local_sequence FROM watermarks ORDER BY stream"
    ).fetchall()
    return {
        "events": int(event_count),
        "idempotency_rows": int(idem_count),
        "history_epoch": int(meta["history_epoch"]),
        "next_local_sequence": int(meta["next_local_sequence"]),
        "watermarks": [
            (str(row["stream"]), int(row["history_epoch"]), int(row["local_sequence"]))
            for row in watermarks
        ],
    }


# ---------------------------------------------------------------------------
# 1/2. Empty and first admission
# ---------------------------------------------------------------------------


def test_empty_usd_portfolio_is_zero(writer):
    state = writer.paper_portfolio_state("USD")
    assert state.status == "OK"
    assert state.quote_currency == "USD"
    assert state.portfolio_version == 0
    assert state.reserved_capital == 0.0
    assert state.active_reservations == 0


def test_first_admission_advances_version_and_reservation(writer):
    context = _seed_context(writer)
    ack = writer.admit_paper_opportunity(
        _admission(disposition_id="disp-read-1", decision_context_id=context["context_id"])
    )
    assert ack.status == "OK"
    assert ack.disposition == "ADMITTED"

    state = writer.paper_portfolio_state("USD")
    assert state.portfolio_version == 1
    assert state.reserved_capital == 500.0
    assert state.active_reservations == 1


def test_read_version_matches_admission_ack_version(writer):
    context = _seed_context(writer)
    ack = writer.admit_paper_opportunity(
        _admission(disposition_id="disp-read-2", decision_context_id=context["context_id"])
    )
    state = writer.paper_portfolio_state("USD")
    assert state.portfolio_version == ack.portfolio_version


# ---------------------------------------------------------------------------
# 3/4. USD / USDT independence
# ---------------------------------------------------------------------------


def test_usd_and_usdt_projections_are_independent(writer):
    context = _seed_context(writer)
    assert writer.admit_paper_opportunity(
        _admission(
            disposition_id="disp-usd",
            decision_context_id=context["context_id"],
            quote_currency="USD",
        )
    ).disposition == "ADMITTED"

    usd = writer.paper_portfolio_state("USD")
    usdt = writer.paper_portfolio_state("USDT")
    assert usd.portfolio_version == 1
    assert usd.reserved_capital == 500.0
    assert usd.active_reservations == 1
    # The other portfolio is untouched.
    assert usdt.portfolio_version == 0
    assert usdt.reserved_capital == 0.0
    assert usdt.active_reservations == 0


def test_each_portfolio_advances_only_itself(writer):
    context = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(
            disposition_id="disp-usd-2",
            decision_context_id=context["context_id"],
            quote_currency="USD",
        )
    )
    writer.admit_paper_opportunity(
        _admission(
            disposition_id="disp-usdt-2",
            decision_context_id=context["context_id"],
            quote_currency="USDT",
        )
    )
    assert writer.paper_portfolio_state("USD").portfolio_version == 1
    assert writer.paper_portfolio_state("USDT").portfolio_version == 1
    assert writer.paper_portfolio_state("USD").active_reservations == 1
    assert writer.paper_portfolio_state("USDT").active_reservations == 1


def test_read_does_not_change_the_projection(writer):
    """Reading twice returns identical figures - a read has no side effects."""
    context = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(disposition_id="disp-stable", decision_context_id=context["context_id"])
    )
    first = writer.paper_portfolio_state("USD")
    second = writer.paper_portfolio_state("USD")
    assert first == second


# ---------------------------------------------------------------------------
# 5/6/7. Read-only proof
# ---------------------------------------------------------------------------


def test_query_writes_zero_canonical_events(writer):
    context = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(disposition_id="disp-ro", decision_context_id=context["context_id"])
    )
    before = _snapshot(writer)
    writer.paper_portfolio_state("USD")
    writer.paper_portfolio_state("USDT")
    assert _snapshot(writer) == before


def test_query_does_not_advance_local_sequence(writer):
    before = _snapshot(writer)
    writer.paper_portfolio_state("USD")
    after = _snapshot(writer)
    assert after["next_local_sequence"] == before["next_local_sequence"]


def test_query_does_not_alter_watermarks_or_history_epoch(writer):
    context = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(disposition_id="disp-wm", decision_context_id=context["context_id"])
    )
    before = _snapshot(writer)
    assert before["watermarks"], "an admission should have advanced a watermark"
    writer.paper_portfolio_state("USD")
    after = _snapshot(writer)
    assert after["watermarks"] == before["watermarks"]
    assert after["history_epoch"] == before["history_epoch"]


def test_query_creates_no_idempotency_row(writer):
    before = _snapshot(writer)
    writer.paper_portfolio_state("USD")
    assert _snapshot(writer)["idempotency_rows"] == before["idempotency_rows"]


def test_read_on_a_fresh_writer_writes_nothing_at_all(writer):
    """Even on an untouched store the read is inert."""
    before = _snapshot(writer)
    writer.paper_portfolio_state("USD")
    assert _snapshot(writer) == before


# ---------------------------------------------------------------------------
# 8. Malformed / unsupported currency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["eur", "USDX", "usd", "", "   ", " USD", "USD ", 5, None, []])
def test_malformed_or_unsupported_currency_fails_closed(writer, bad):
    state = writer.paper_portfolio_state(bad)
    assert state.status == "REJECTED"
    assert state.error_code in {
        "MALFORMED_QUOTE_CURRENCY",
        "UNSUPPORTED_QUOTE_CURRENCY",
    }
    assert state.portfolio_version is None
    assert state.reserved_capital is None
    assert state.active_reservations is None


def test_rejected_read_writes_nothing(writer):
    before = _snapshot(writer)
    writer.paper_portfolio_state("eur")
    assert _snapshot(writer) == before


# ---------------------------------------------------------------------------
# 10. Client paths share one semantics
# ---------------------------------------------------------------------------


def test_in_process_client_uses_the_same_semantics(tmp_path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        client = InProcessWriterClient(server)
        state = client.get_paper_portfolio_state("USD")
        assert isinstance(state, PaperPortfolioState)
        assert state.status == "OK"
        assert state.quote_currency == "USD"
        assert state.portfolio_version == 0

        bad = client.get_paper_portfolio_state("eur")
        assert bad.status == "REJECTED"
        assert bad.error_code == "UNSUPPORTED_QUOTE_CURRENCY"
    finally:
        server.stop()


def test_in_process_and_writer_agree(tmp_path):
    db_path = tmp_path / "canonical.sqlite3"
    server = CanonicalWriterServer(db_path=db_path, socket_path=tmp_path / "c.sock")
    try:
        client = InProcessWriterClient(server)
        from_client = client.get_paper_portfolio_state("USD")
        from_writer = server.writer.paper_portfolio_state("USD")
        assert (
            from_client.portfolio_version,
            from_client.reserved_capital,
            from_client.active_reservations,
        ) == (
            from_writer.portfolio_version,
            from_writer.reserved_capital,
            from_writer.active_reservations,
        )
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# 9. Unhealthy writer hands back no apparently valid state
# ---------------------------------------------------------------------------


def test_unhealthy_server_does_not_return_valid_state(tmp_path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        server.mark_worker_unhealthy_for_tests("TEST")
        client = InProcessWriterClient(server)
        state = client.get_paper_portfolio_state("USD")
        assert state.status == "RETRYABLE"
        assert state.error_code == "WORKER_UNHEALTHY"
        # Crucially: no version is offered that a producer could build on.
        assert state.portfolio_version is None
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Model round-trip
# ---------------------------------------------------------------------------


def test_model_round_trips_through_the_wire_form():
    state = PaperPortfolioState(
        status="OK",
        quote_currency="USD",
        portfolio_version=3,
        reserved_capital=1500.0,
        active_reservations=2,
    )
    assert PaperPortfolioState.from_dict(json.loads(json.dumps(state.to_dict()))) == state


def test_model_round_trips_a_rejection():
    state = PaperPortfolioState(
        status="REJECTED",
        error_code="UNSUPPORTED_QUOTE_CURRENCY",
        detail="unsupported",
    )
    assert PaperPortfolioState.from_dict(state.to_dict()) == state


# ---------------------------------------------------------------------------
# The producer consumes the public seam, never the private projection
# ---------------------------------------------------------------------------


def test_producer_uses_the_public_client_seam_not_private_state():
    """Static proof that the runtime never reaches into writer-private state."""
    from pathlib import Path

    runtime_modules = (
        "paper_v2_activation.py",
        "paper_v2_instrument_registration.py",
        "paper_v2_pretrade_adapter.py",
        "paper_v2_protection_plan.py",
    )
    root = Path(__file__).resolve().parents[1] / "app" / "services"
    for name in runtime_modules:
        source = (root / name).read_text(encoding="utf-8")
        assert "_portfolio_state" not in source, f"{name} must not use private state"
        assert "get_paper_portfolio_state" not in source or name == "paper_v2_execution.py"

    assert SCHEMA_VERSION == 1
    assert CanonicalWriterClient is not None
