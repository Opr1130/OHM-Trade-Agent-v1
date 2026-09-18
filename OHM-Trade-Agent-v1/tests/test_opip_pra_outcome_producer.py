"""PR-A producer: terminal paper outcome delivery, disposition and reconciliation.

Three deliberate layers:

* Layer 1 - real dispatch. An ``InProcessWriterClient`` shares the server's real
  dispatch and queue path without needing a UDS socket, so ack semantics are
  genuine rather than mocked.
* Layer 2 - transport injection. A minimal ``WriterClient`` stub raises, which
  is how timeout / refused / AF_UNIX-absent failures actually surface. The
  producer must never be coupled to server-internal exception types.
* Layer 3 - canonical reconstruction. A real isolated store, seeded through the
  real writer, read back independently of the producer.

The acknowledgement-loss test is the distributed-systems correctness case: the
writer commits but the producer believes delivery failed, spools, retries, and
must end with exactly one economic outcome.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.gap_spool import evidence_window_incomplete, load_gap_spool
from app.opip.canonical.models import WriterAck, WriterIntent
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.paper_outcome import PAPER_OUTCOME_TERMINAL_RECORDED
from app.services import paper_outcome_outbox as outbox
from app.services import paper_trade_registry as registry
from app.services.paper_trade_models import PaperTradeLifecycle

ENTER = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)
PAPER_ID = "PAPER:" + "a" * 20


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_env(tmp_path, monkeypatch):
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(root))
    return {"root": root, "db": root / "opip_canonical_v1.sqlite3"}


@pytest.fixture
def servers(canonical_env):
    created: list[CanonicalWriterServer] = []

    def _factory(**kwargs):
        params = {
            "db_path": canonical_env["db"],
            "socket_path": canonical_env["root"] / "writer.sock",
        }
        params.update(kwargs)
        server = CanonicalWriterServer(**params)
        created.append(server)
        return server

    yield _factory
    for server in created:
        try:
            server.stop()
        except Exception:
            pass


@pytest.fixture
def paper_env(tmp_path, monkeypatch):
    state = tmp_path / "paper_state.json"
    events = tmp_path / "paper_events.jsonl"
    spool = tmp_path / "evidence_gap_spool.json"
    monkeypatch.setattr(registry, "STATE_FILE", state)
    monkeypatch.setattr(registry, "EVENT_FILE", events)
    monkeypatch.setattr(registry, "EVIDENCE_GAP_SPOOL_FILE", spool)
    # Canonical capture on by default for these tests; the gate itself has its
    # own test that flips it off.
    monkeypatch.setattr(outbox, "shadow_capture_enabled", lambda *a, **k: True)
    outbox.set_writer_client_for_tests(None)
    yield {"state": state, "events": events, "spool": spool}
    outbox.set_writer_client_for_tests(None)


def _lifecycle(**overrides) -> PaperTradeLifecycle:
    values = {
        "paper_trade_id": PAPER_ID,
        "episode_id": "EP:test-1",
        "cohort_id": "COH:test-1",
        "symbol": "BTCUSD",
        "base_asset": "BTC",
        "direction": "LONG",
        "status": "OPEN",
        "entry_action": "MARKET_DECISION_TIME",
        "signal_at": ENTER.isoformat(),
        "created_at": ENTER.isoformat(),
        "updated_at": ENTER.isoformat(),
        "entry_low": 100.0,
        "entry_high": 101.0,
        "entry_limit": 100.5,
        "chase_limit": 102.0,
        "stop_price": 98.0,
        "target_1": 104.0,
        "target_2": 108.0,
        "risk_level": "medium",
        "confidence": 70,
        "profit_rank": 1,
        "profit_rank_score": 80.0,
        "capital": 1000.0,
        "fee_rate": 0.004,
        "slippage_bps": 10.0,
        "tp1_fraction": 0.5,
        "pending_ttl_hours": 24,
        "max_hold_hours": 24,
        "reference_price": 100.5,
        "quote_currency": "USD",
        "strategy_version": "OPIP-STRATEGY-V1",
        "entry_price": 100.6,
        "quantity_initial": 9.9404,
        "quantity_remaining": 9.9404,
        "opened_at": ENTER.isoformat(),
        "fees_paid": 4.0,
    }
    values.update(overrides)
    return PaperTradeLifecycle(**values)


def _seed_open(paper_env, **overrides) -> PaperTradeLifecycle:
    trade = _lifecycle(**overrides)
    registry.create_lifecycle(
        trade, state_file=paper_env["state"], event_file=paper_env["events"]
    )
    return trade


def _seed_terminal(paper_env, status="CLOSED", event_type="CLOSED_STOP", **overrides):
    trade = _seed_open(paper_env, **overrides)
    trade.status = status
    if status == "CLOSED":
        trade.exit_reason = "STOP"
        trade.exit_price = 98.0
        trade.closed_at = EXIT.isoformat()
        trade.gross_pnl = -25.0
        trade.fees_paid = 4.0
        trade.net_pnl = -29.0
        trade.net_pnl_pct = -2.9
        trade.outcome = "LOSS"
    elif status == "CANCELLED":
        trade.exit_reason = "PENDING_TTL_EXPIRED"
        trade.closed_at = EXIT.isoformat()
        trade.gross_pnl = 0.0
        trade.fees_paid = 0.0
        trade.net_pnl = 0.0
        trade.net_pnl_pct = 0.0
        trade.outcome = "NO_TRADE"
        trade.entry_price = None
        trade.quantity_initial = 0.0
        trade.quantity_remaining = 0.0
    else:
        trade.exit_reason = "OHLC_GAP:60->120"
        trade.closed_at = EXIT.isoformat()
        trade.gross_pnl = None
        trade.net_pnl = None
        trade.net_pnl_pct = None
        trade.outcome = "UNRESOLVED"
    registry.save_lifecycle(
        trade,
        event_type=event_type,
        state_file=paper_env["state"],
        event_file=paper_env["events"],
    )
    return trade


def _envelope(paper_env, paper_trade_id=PAPER_ID) -> dict:
    rows = json.loads(paper_env["state"].read_text(encoding="utf-8"))["lifecycles"]
    return rows[paper_trade_id]["outcome_outbox"]


def _canonical_rows(canonical_env) -> list[tuple[str, str]]:
    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        return list(
            connection.execute(
                "SELECT event_type, payload_json FROM events ORDER BY local_sequence"
            )
        )
    finally:
        connection.close()


class _FailingClient:
    """Minimal WriterClient whose transport always fails."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def submit(self, intent: WriterIntent) -> WriterAck:
        self.calls += 1
        raise self.error

    def confirm_ops_applied(self, event_id: str) -> WriterAck:
        raise self.error

    def mark_handoff_superseded(self, event_id: str) -> WriterAck:
        raise self.error

    def list_pending_handoffs(self):
        raise self.error

    def health(self):
        raise self.error


class _RejectingClient:
    """WriterClient that returns a chosen acknowledgement without a store."""

    def __init__(self, ack: WriterAck) -> None:
        self.ack = ack
        self.calls = 0

    def submit(self, intent: WriterIntent) -> WriterAck:
        self.calls += 1
        return self.ack

    def confirm_ops_applied(self, event_id: str) -> WriterAck:
        return self.ack

    def mark_handoff_superseded(self, event_id: str) -> WriterAck:
        return self.ack

    def list_pending_handoffs(self):
        return []

    def health(self):
        return {"status": "OK"}


# ---------------------------------------------------------------------------
# Layer 1 - real dispatch semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "event_type"),
    [
        ("CLOSED", "CLOSED_STOP"),
        ("CANCELLED", "CANCELLED_PENDING_TTL_EXPIRED"),
        ("UNRESOLVED", "UNRESOLVED_OHLC_GAP"),
    ],
)
def test_each_terminal_path_commits_exactly_one_outcome(
    canonical_env, paper_env, servers, status, event_type
):
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    _seed_terminal(paper_env, status=status, event_type=event_type)

    envelope = _envelope(paper_env)
    assert envelope["delivery"] == outbox.DELIVERY_COMMITTED
    rows = _canonical_rows(canonical_env)
    assert len(rows) == 1
    assert rows[0][0] == PAPER_OUTCOME_TERMINAL_RECORDED
    assert json.loads(rows[0][1])["terminal_status"] == status


def test_closed_outcome_carries_economics_and_quote_currency(canonical_env, paper_env, servers):
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    _seed_terminal(paper_env)

    payload = json.loads(_canonical_rows(canonical_env)[0][1])
    assert payload["quote_currency"] == "USD"
    assert payload["gross_pnl"] == pytest.approx(-25.0)
    assert payload["fees_paid"] == pytest.approx(4.0)
    assert payload["net_pnl"] == pytest.approx(-29.0)
    assert payload["capital_committed"] == pytest.approx(1000.0)
    assert payload["strategy_version"] == "OPIP-STRATEGY-V1"
    assert payload["engine"] == "OHM_PAPER_SIM_V1"
    assert payload["exit_reason"] == "STOP"
    assert payload["entry_timestamp"] < payload["exit_timestamp"]


def test_cancelled_outcome_has_zero_realised_economics(canonical_env, paper_env, servers):
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    _seed_terminal(paper_env, status="CANCELLED", event_type="CANCELLED_PENDING_TTL_EXPIRED")

    payload = json.loads(_canonical_rows(canonical_env)[0][1])
    assert payload["net_pnl"] == 0.0
    assert payload["gross_pnl"] == 0.0
    assert payload["simulated_entry_price"] is None


def test_cancelled_outcome_preserves_planned_committed_capital(canonical_env, paper_env, servers):
    """Planned capital survives a cancellation; realised economics stay zero.

    ``capital_committed`` records what the lifecycle committed, which is true
    regardless of whether a position was ever realised. Zeroing it would lose
    the only record of what the setup would have risked.
    """
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    _seed_terminal(
        paper_env,
        status="CANCELLED",
        event_type="CANCELLED_PENDING_TTL_EXPIRED",
        capital=1234.5,
    )

    payload = json.loads(_canonical_rows(canonical_env)[0][1])
    assert payload["capital_committed"] == pytest.approx(1234.5)
    assert payload["gross_pnl"] == 0.0
    assert payload["fees_paid"] == 0.0
    assert payload["net_pnl"] == 0.0
    assert payload["net_pnl_pct"] == 0.0


def test_unresolved_outcome_asserts_no_economics(canonical_env, paper_env, servers):
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    _seed_terminal(paper_env, status="UNRESOLVED", event_type="UNRESOLVED_OHLC_GAP")

    payload = json.loads(_canonical_rows(canonical_env)[0][1])
    assert payload["net_pnl"] is None
    assert payload["terminal_status"] == "UNRESOLVED"
    # The parameterised simulator reason must collapse to its family.
    assert payload["exit_reason"] == "OHLC_GAP"


def test_acknowledgement_loss_retry_yields_one_outcome(canonical_env, paper_env, servers):
    """Commit, lose the ack, spool, retry, end with exactly one outcome."""
    server = servers()
    calls = {"n": 0}
    real = InProcessWriterClient(server)

    class _LoseFirstAck:
        def submit(self, intent):
            calls["n"] += 1
            real.submit(intent)  # the writer really commits
            if calls["n"] == 1:
                raise OSError("connection reset after commit")
            return real.submit(intent)

        def confirm_ops_applied(self, event_id):
            return real.confirm_ops_applied(event_id)

        def mark_handoff_superseded(self, event_id):
            return real.mark_handoff_superseded(event_id)

        def list_pending_handoffs(self):
            return real.list_pending_handoffs()

        def health(self):
            return real.health()

    outbox.set_writer_client_for_tests(_LoseFirstAck())
    _seed_terminal(paper_env)

    # First attempt: the writer committed but the producer saw a transport error.
    assert _envelope(paper_env)["delivery"] == outbox.DELIVERY_PENDING
    assert len(_canonical_rows(canonical_env)) == 1

    stats = registry.reconcile_pending_outcomes(state_file=paper_env["state"])
    assert stats["committed"] == 1
    assert stats["pending"] == 0

    # Exactly one economic outcome, and the gap is resolved.
    assert len(_canonical_rows(canonical_env)) == 1
    assert evidence_window_incomplete(paper_env["spool"]) is False
    assert _envelope(paper_env)["delivery"] == outbox.DELIVERY_COMMITTED


def test_ack_loss_then_terminal_resave_preserves_exact_pending_intent(
    canonical_env, paper_env, servers
):
    """ACK loss + re-save must retry the exact durable intent, never rebuild it."""
    server = servers()
    real = InProcessWriterClient(server)
    calls = {"n": 0}

    class _LoseFirstAck:
        def submit(self, intent):
            calls["n"] += 1
            if calls["n"] == 1:
                real.submit(intent)  # commit succeeds; producer loses the ACK
                raise OSError("connection reset after commit")
            return real.submit(intent)

        def confirm_ops_applied(self, event_id):
            return real.confirm_ops_applied(event_id)

        def mark_handoff_superseded(self, event_id):
            return real.mark_handoff_superseded(event_id)

        def list_pending_handoffs(self):
            return real.list_pending_handoffs()

        def health(self):
            return real.health()

    outbox.set_writer_client_for_tests(_LoseFirstAck())
    trade = _seed_terminal(paper_env)

    pending = _envelope(paper_env)
    assert pending["delivery"] == outbox.DELIVERY_PENDING
    durable_intent = json.loads(json.dumps(pending["intent"], sort_keys=True))
    durable_outcome_id = pending["outcome_id"]
    durable_key = pending["idempotency_key"]
    durable_attempts = pending["attempts"]
    assert len(_canonical_rows(canonical_env)) == 1

    # A later terminal save increments lifecycle revision. It must not rebuild
    # the already-durable PENDING intent from that newer mutable lifecycle.
    registry.save_lifecycle(
        trade,
        event_type="CLOSED_STOP",
        state_file=paper_env["state"],
        event_file=paper_env["events"],
    )

    after_resave = _envelope(paper_env)
    assert after_resave["delivery"] == outbox.DELIVERY_PENDING
    assert after_resave["intent"] == durable_intent
    assert after_resave["outcome_id"] == durable_outcome_id
    assert after_resave["idempotency_key"] == durable_key
    assert after_resave["attempts"] == durable_attempts
    assert len(_canonical_rows(canonical_env)) == 1

    # Reconciliation resubmits the exact original intent. Since the writer
    # already committed it before the ACK was lost, DUPLICATE_OK resolves the
    # delivery without creating a second economic outcome.
    stats = registry.reconcile_pending_outcomes(state_file=paper_env["state"])
    assert stats["committed"] == 1
    assert stats["pending"] == 0
    assert _envelope(paper_env)["delivery"] == outbox.DELIVERY_COMMITTED
    assert _envelope(paper_env)["intent"] == durable_intent
    assert len(_canonical_rows(canonical_env)) == 1


def test_existing_malformed_terminal_envelope_is_not_rebuilt_from_later_state(
    canonical_env, paper_env
):
    """Corrupt recovery evidence must stay visible instead of being rewritten."""
    client = _FailingClient(OSError("must not submit"))
    outbox.set_writer_client_for_tests(client)
    trade = _seed_open(paper_env)

    # Simulate a durable but malformed recovery envelope left by corruption or
    # an older defect. Reconstructing it from a later terminal lifecycle would
    # create a new evidence claim and hide the integrity problem.
    state = json.loads(paper_env["state"].read_text(encoding="utf-8"))
    malformed = {
        "schema_version": 1,
        "delivery": outbox.DELIVERY_PENDING,
        "intent": None,
        "outcome_id": None,
        "idempotency_key": None,
        "attempts": 2,
        "gap_id": None,
        "last_error": outbox.ERROR_UNBUILDABLE_PAYLOAD,
        "last_detail": "corrupt durable envelope",
        "last_attempt_at": None,
    }
    state["lifecycles"][PAPER_ID]["outcome_outbox"] = malformed
    paper_env["state"].write_text(json.dumps(state), encoding="utf-8")

    trade.status = "CLOSED"
    trade.exit_reason = "STOP"
    trade.exit_price = 98.0
    trade.closed_at = EXIT.isoformat()
    trade.gross_pnl = -25.0
    trade.net_pnl = -29.0
    trade.net_pnl_pct = -2.9
    trade.outcome = "LOSS"

    registry.save_lifecycle(
        trade,
        event_type="CLOSED_STOP",
        state_file=paper_env["state"],
        event_file=paper_env["events"],
    )

    assert _envelope(paper_env) == malformed
    assert client.calls == 0
    assert not canonical_env["db"].exists()


def test_reprocessing_a_closed_lifecycle_does_not_duplicate(canonical_env, paper_env, servers):
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    trade = _seed_terminal(paper_env)
    first = json.loads(_canonical_rows(canonical_env)[0][1])["outcome_id"]

    # Re-save the same terminal facts: same identity, no second outcome.
    registry.save_lifecycle(
        trade,
        event_type="CLOSED_STOP",
        state_file=paper_env["state"],
        event_file=paper_env["events"],
    )
    rows = _canonical_rows(canonical_env)
    assert len(rows) == 1
    assert json.loads(rows[0][1])["outcome_id"] == first


def test_resaving_a_settled_terminal_lifecycle_keeps_its_delivery(canonical_env, paper_env, servers):
    """A re-save must not rebuild the envelope into a permanent conflict.

    Rebuilding would embed the new revision, and recorded provenance takes part
    in conflict detection, so the retry would be rejected as a conflict and a
    settled COMMITTED delivery would degrade into a permanent failure.
    """
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    trade = _seed_terminal(paper_env)
    assert _envelope(paper_env)["delivery"] == outbox.DELIVERY_COMMITTED
    committed_intent = _envelope(paper_env)["intent"]

    registry.save_lifecycle(
        trade,
        event_type="CLOSED_STOP",
        state_file=paper_env["state"],
        event_file=paper_env["events"],
    )

    envelope = _envelope(paper_env)
    assert envelope["delivery"] == outbox.DELIVERY_COMMITTED
    # The persisted intent is unchanged, so it remains byte-identical on retry.
    assert envelope["intent"] == committed_intent
    assert len(_canonical_rows(canonical_env)) == 1


# ---------------------------------------------------------------------------
# Layer 2 - transport failure injection and the disposition table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TimeoutError("timed out"), id="socket-timeout"),
        pytest.param(ConnectionRefusedError("refused"), id="connection-refused"),
        pytest.param(FileNotFoundError("no socket"), id="socket-missing"),
        pytest.param(RuntimeError("AF_UNIX_UNAVAILABLE"), id="af-unix-unavailable"),
        pytest.param(OSError("generic"), id="oserror"),
    ],
)
def test_transport_failure_leaves_outbox_pending_and_records_gap(canonical_env, paper_env, error):
    outbox.set_writer_client_for_tests(_FailingClient(error))
    _seed_terminal(paper_env)

    envelope = _envelope(paper_env)
    assert envelope["delivery"] == outbox.DELIVERY_PENDING
    assert envelope["last_error"] == outbox.ERROR_TRANSPORT_FAILURE
    assert envelope["attempts"] == 1
    # The exact intent is preserved for a byte-identical retry.
    assert isinstance(envelope["intent"], dict)
    assert envelope["idempotency_key"].startswith(PAPER_OUTCOME_TERMINAL_RECORDED + ":")

    assert evidence_window_incomplete(paper_env["spool"]) is True
    unresolved = load_gap_spool(paper_env["spool"])["unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0]["intended_event_type"] == PAPER_OUTCOME_TERMINAL_RECORDED


def test_retryable_ack_is_pending_then_commits(canonical_env, paper_env, servers):
    server = servers()
    acked = {"n": 0}
    real = InProcessWriterClient(server)

    class _RetryOnce:
        def submit(self, intent):
            acked["n"] += 1
            if acked["n"] == 1:
                return WriterAck(status="RETRYABLE", error_code="INTEGRITY_CONFLICT")
            return real.submit(intent)

        def confirm_ops_applied(self, event_id):
            return real.confirm_ops_applied(event_id)

        def mark_handoff_superseded(self, event_id):
            return real.mark_handoff_superseded(event_id)

        def list_pending_handoffs(self):
            return real.list_pending_handoffs()

        def health(self):
            return real.health()

    outbox.set_writer_client_for_tests(_RetryOnce())
    _seed_terminal(paper_env)
    assert _envelope(paper_env)["delivery"] == outbox.DELIVERY_PENDING
    assert _envelope(paper_env)["last_error"] == outbox.ERROR_RETRYABLE_ACK

    stats = registry.reconcile_pending_outcomes(state_file=paper_env["state"])
    assert stats["committed"] == 1
    assert len(_canonical_rows(canonical_env)) == 1


def test_payload_conflict_is_permanent_and_never_retried(canonical_env, paper_env):
    outbox.set_writer_client_for_tests(
        _RejectingClient(WriterAck(status="REJECTED", error_code="IDEMPOTENCY_PAYLOAD_CONFLICT"))
    )
    _seed_terminal(paper_env)

    envelope = _envelope(paper_env)
    assert envelope["delivery"] == outbox.DELIVERY_PERMANENT_FAILURE
    assert envelope["last_error"] == outbox.ERROR_PAYLOAD_CONFLICT

    before = envelope["attempts"]
    stats = registry.reconcile_pending_outcomes(state_file=paper_env["state"])
    assert stats["permanent_failure"] == 1
    assert stats["pending"] == 0
    assert _envelope(paper_env)["attempts"] == before  # no further attempts


def test_invalid_intent_is_permanent_and_never_retried(canonical_env, paper_env):
    outbox.set_writer_client_for_tests(
        _RejectingClient(WriterAck(status="REJECTED", error_code="INVALID_INTENT"))
    )
    _seed_terminal(paper_env)

    envelope = _envelope(paper_env)
    assert envelope["delivery"] == outbox.DELIVERY_PERMANENT_FAILURE
    assert envelope["last_error"] == "INVALID_INTENT"
    stats = registry.reconcile_pending_outcomes(state_file=paper_env["state"])
    assert stats["permanent_failure"] == 1


def test_unclassified_ack_stays_pending(canonical_env, paper_env):
    outbox.set_writer_client_for_tests(_RejectingClient(WriterAck(status="WAT")))  # type: ignore[arg-type]
    _seed_terminal(paper_env)
    envelope = _envelope(paper_env)
    assert envelope["delivery"] == outbox.DELIVERY_PENDING
    assert envelope["last_error"] == outbox.ERROR_UNCLASSIFIED_ACK


def test_unknown_quote_currency_is_permanent_failure_not_a_submission(
    canonical_env, paper_env
):
    client = _FailingClient(OSError("must not be called"))
    outbox.set_writer_client_for_tests(client)
    _seed_terminal(paper_env, symbol="BTCGBP", base_asset="BTC", quote_currency=None)

    envelope = _envelope(paper_env)
    assert envelope["delivery"] == outbox.DELIVERY_PERMANENT_FAILURE
    assert envelope["last_error"] == outbox.ERROR_UNBUILDABLE_PAYLOAD
    assert envelope["intent"] is None
    assert client.calls == 0


def test_capture_disabled_is_inert_and_records_no_gap(canonical_env, paper_env, monkeypatch):
    outbox.set_writer_client_for_tests(_FailingClient(OSError("must not be called")))
    monkeypatch.setattr(outbox, "shadow_capture_enabled", lambda *a, **k: False)
    _seed_terminal(paper_env)

    assert _envelope(paper_env)["delivery"] == outbox.DELIVERY_PENDING
    assert evidence_window_incomplete(paper_env["spool"]) is False
    stats = registry.reconcile_pending_outcomes(state_file=paper_env["state"])
    assert stats["skipped"] == 1


def test_restart_with_unresolved_gap_reconciles_safely(canonical_env, paper_env, servers):
    """A gap left by a previous process is drained by the next one."""
    outbox.set_writer_client_for_tests(_FailingClient(OSError("writer down")))
    _seed_terminal(paper_env)
    assert evidence_window_incomplete(paper_env["spool"]) is True

    # Simulate restart: a fresh client, with the envelope already on disk.
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    stats = registry.reconcile_pending_outcomes(state_file=paper_env["state"])

    assert stats["committed"] == 1
    assert evidence_window_incomplete(paper_env["spool"]) is False
    assert len(_canonical_rows(canonical_env)) == 1


def test_repeated_failures_never_evict_or_duplicate_gaps(canonical_env, paper_env):
    outbox.set_writer_client_for_tests(_FailingClient(OSError("down")))
    _seed_terminal(paper_env)
    for _ in range(3):
        registry.reconcile_pending_outcomes(state_file=paper_env["state"])

    unresolved = load_gap_spool(paper_env["spool"])["unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0]["retry_count"] >= 3


def test_closed_lifecycle_alone_is_not_evidence_complete(canonical_env, paper_env):
    """State says CLOSED; evidence says incomplete. Both are true."""
    outbox.set_writer_client_for_tests(_FailingClient(OSError("down")))
    trade = _seed_terminal(paper_env)

    rows = json.loads(paper_env["state"].read_text(encoding="utf-8"))["lifecycles"]
    assert rows[trade.paper_trade_id]["status"] == "CLOSED"
    assert evidence_window_incomplete(paper_env["spool"]) is True


# ---------------------------------------------------------------------------
# Layer 3 - canonical reconstruction, independent of the producer
# ---------------------------------------------------------------------------


def test_canonical_record_is_reconstructable_from_the_store(canonical_env, paper_env, servers):
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    _seed_terminal(paper_env)

    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM events WHERE event_type = ?",
            (PAPER_OUTCOME_TERMINAL_RECORDED,),
        ).fetchone()
        watermarks = {
            r["stream"] for r in connection.execute("SELECT stream FROM watermarks")
        }
    finally:
        connection.close()

    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["outcome_id"].startswith("PAPER-OUTCOME:")
    assert row["idempotency_key"] == f"{PAPER_OUTCOME_TERMINAL_RECORDED}:{payload['outcome_id']}"
    assert "paper_outcome.v1" in watermarks
    # Lineage is carried end to end.
    assert payload["episode_id"] == "EP:test-1"
    assert payload["cohort_id"] == "COH:test-1"
    assert payload["paper_trade_id"] == PAPER_ID


def test_usd_and_usdt_outcomes_remain_distinguishable(canonical_env, paper_env, servers):
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))
    _seed_terminal(paper_env)
    _seed_terminal(
        paper_env,
        paper_trade_id="PAPER:" + "b" * 20,
        episode_id="EP:test-2",
        symbol="ETHUSDT",
        base_asset="ETH",
        quote_currency="USDT",
    )

    currencies = sorted(
        json.loads(payload)["quote_currency"] for _, payload in _canonical_rows(canonical_env)
    )
    assert currencies == ["USD", "USDT"]


# ---------------------------------------------------------------------------
# Account summary - per-currency, admission untouched
# ---------------------------------------------------------------------------


def test_account_summary_separates_realised_pnl_by_currency(paper_env):
    _seed_terminal(paper_env)
    _seed_terminal(
        paper_env,
        paper_trade_id="PAPER:" + "c" * 20,
        episode_id="EP:test-3",
        symbol="ETHUSDT",
        base_asset="ETH",
        quote_currency="USDT",
    )

    summary = registry.account_summary(10000.0, state_file=paper_env["state"])
    assert summary.realized_net_pnl_by_currency["USD"] == pytest.approx(-29.0)
    assert summary.realized_net_pnl_by_currency["USDT"] == pytest.approx(-29.0)
    # The pre-existing single total is unchanged in this PR.
    assert summary.realized_net_pnl == pytest.approx(-58.0)


def test_account_summary_reports_unknown_currency_separately(paper_env):
    _seed_terminal(paper_env, symbol="BTCGBP", base_asset="BTC", quote_currency=None)
    summary = registry.account_summary(10000.0, state_file=paper_env["state"])
    assert "UNKNOWN" in summary.realized_net_pnl_by_currency
    assert "USD" not in summary.realized_net_pnl_by_currency
