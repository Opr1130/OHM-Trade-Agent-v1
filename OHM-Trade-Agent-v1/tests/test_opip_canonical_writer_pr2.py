"""PR 2 canonical writer foundation tests."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.canonical.bridge import (
    durable_record_opportunity_alert,
    idempotency_key_for_record,
    reconcile_capture_gap_spool,
    reconcile_pending_ops_handoffs,
    set_writer_client_for_tests,
)
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.gap_spool import (
    append_capture_gap,
    evidence_window_incomplete,
    load_gap_spool,
)
from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.rebuild import rebuild_identity_projection
from app.opip.canonical.recovery import run_backup_restore_drill
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.canonical.writer import CanonicalWriter
from app.services.alert_governor import STATE_FILE, evaluate_opportunity_alert
from app.services.registry_io import load_json


@pytest.fixture
def canonical_env(tmp_path, monkeypatch):
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(root))
    state = tmp_path / "alert_governor_state.json"
    monkeypatch.setattr("app.services.alert_governor.STATE_FILE", state)
    monkeypatch.setattr("app.opip.canonical.bridge.STATE_FILE", state)
    yield {"root": root, "state": state, "db": root / "opip_canonical_v1.sqlite3"}
    set_writer_client_for_tests(None)


def _record_intent(*, key: str, identity: str, priority: str = "NORMAL") -> WriterIntent:
    return WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority=priority,  # type: ignore[arg-type]
        idempotency_key=key,
        event_type="alert_governor.transition.recorded",
        payload={
            "identity": identity,
            "transition_key": "READY:x",
            "message_id": 42,
            "created_new": True,
            "scan_id": "scan-1",
        },
        ops_handoff={
            "operation": "RECORD",
            "identity": identity,
            "transition_key": "READY:x",
            "message_id": 42,
            "created_new": True,
            "reservation_token": "tok-1",
            "state_file": str(STATE_FILE),
        },
    )


def test_writer_assigns_local_sequence_and_skips_projection_on_release(canonical_env):
    writer = CanonicalWriter(canonical_env["db"])
    try:
        ack1 = writer.submit(_record_intent(key="k1", identity="EARLY_MOVER:AAA"))
        assert ack1.status == "OK"
        assert ack1.local_sequence == 1
        dup = writer.submit(_record_intent(key="k1", identity="EARLY_MOVER:AAA"))
        assert dup.status == "DUPLICATE_OK"
        assert dup.event_id == ack1.event_id

        release = WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="NORMAL",
            idempotency_key="ag:v1:early_watch:RELEASE:tok-1",
            event_type="alert_governor.reservation.released",
            payload={"identity": "EARLY_MOVER:AAA", "reservation_token": "tok-1"},
            ops_handoff={
                "operation": "RELEASE",
                "identity": "EARLY_MOVER:AAA",
                "transition_key": "READY:x",
                "message_id": None,
                "created_new": False,
                "reservation_token": "tok-1",
                "state_file": str(canonical_env["state"]),
            },
        )
        ack2 = writer.submit(release)
        assert ack2.status == "OK"
        assert ack2.local_sequence == 2
        row = writer._conn.execute(
            "SELECT transition_key FROM alert_identity_projection WHERE identity = ?",
            ("EARLY_MOVER:AAA",),
        ).fetchone()
        assert row is not None
        assert row["transition_key"] == "READY:x"
    finally:
        writer.close()


def test_edit_idempotency_uses_scan_id():
    key = idempotency_key_for_record(
        created_new=False,
        reservation_token=None,
        scan_id="SCAN123",
        identity="EARLY_MOVER:BTC",
        transition_key="READY:1",
    )
    assert key == "ag:v1:early_watch:EDIT:SCAN123:EARLY_MOVER:BTC:READY:1"


def test_priority_queue_reserves_high_capacity(canonical_env):
    server = CanonicalWriterServer(
        db_path=canonical_env["db"],
        socket_path=canonical_env["root"] / "writer.sock",
    )
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)

    # Hold the worker busy with a slow HIGH-first contention pattern:
    # enqueue many NORMAL then one HIGH; HIGH must still complete under 1s
    # once the worker drains (reserved capacity prevents NORMAL starvation).
    acks: list[str] = []

    def _submit(key: str, identity: str, priority: str) -> None:
        ack = client.submit(
            _record_intent(key=key, identity=identity, priority=priority)
        )
        acks.append(f"{priority}:{ack.status}")

    threads = [
        threading.Thread(target=_submit, args=(f"n:{idx}", f"EARLY_MOVER:N{idx}", "NORMAL"))
        for idx in range(20)
    ]
    for thread in threads:
        thread.start()
    t0 = time.perf_counter()
    high_ack = client.submit(
        _record_intent(key="h:1", identity="EARLY_MOVER:HIGH", priority="HIGH")
    )
    high_ms = (time.perf_counter() - t0) * 1000.0
    for thread in threads:
        thread.join(timeout=10)
    assert high_ack.status in {"OK", "DUPLICATE_OK"}
    assert high_ms < 1000.0
    metrics = server.metrics_snapshot()
    assert metrics["commits"] + metrics["duplicates"] >= 1
    server.stop()


def test_bridge_ack_before_json_and_confirm(canonical_env, monkeypatch):
    server = CanonicalWriterServer(
        db_path=canonical_env["db"],
        socket_path=canonical_env["root"] / "writer.sock",
    )
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:SOL",
        transition_key="READY:sol",
        state_file=canonical_env["state"],
    )
    assert decision.action == "CREATE"
    assert decision.reservation_token

    ack = durable_record_opportunity_alert(
        identity="EARLY_MOVER:SOL",
        transition_key="READY:sol",
        message_id=99,
        created_new=True,
        reservation_token=decision.reservation_token,
        scan_id="scan-sol",
        state_file=canonical_env["state"],
        settings=settings,
    )
    assert ack is not None
    assert ack.status == "OK"
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:SOL"]["message_id"] == 99
    pending = client.list_pending_handoffs()
    assert pending == []
    server.stop()


def test_handoff_reconcile_after_ack_before_json_crash(canonical_env):
    server = CanonicalWriterServer(
        db_path=canonical_env["db"],
        socket_path=canonical_env["root"] / "writer.sock",
    )
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    intent = _record_intent(key="crash:1", identity="EARLY_MOVER:CRASH")
    intent.ops_handoff["state_file"] = str(canonical_env["state"])
    ack = client.submit(intent)
    assert ack.status == "OK"
    assert len(client.list_pending_handoffs()) == 1

    # Simulate process crash before JSON mutation, then recover.
    stats = reconcile_pending_ops_handoffs(
        settings=settings,
        state_file=canonical_env["state"],
    )
    assert stats["replayed"] == 1
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:CRASH"]["message_id"] == 42
    assert client.list_pending_handoffs() == []
    server.stop()


def test_gap_spool_never_silently_evicts_and_marks_incomplete(canonical_env):
    for idx in range(5):
        append_capture_gap(
            idempotency_key=f"k{idx}",
            scan_id="s",
            identity=f"EARLY_MOVER:G{idx}",
            intended_event_type="alert_governor.transition.recorded",
            error_code="RETRYABLE",
        )
    assert evidence_window_incomplete()
    assert len(load_gap_spool()["unresolved"]) == 5

    server = CanonicalWriterServer(
        db_path=canonical_env["db"],
        socket_path=canonical_env["root"] / "writer.sock",
    )
    set_writer_client_for_tests(InProcessWriterClient(server))
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")
    stats = reconcile_capture_gap_spool(settings=settings)
    assert stats["resolved"] == 5
    assert not evidence_window_incomplete()
    server.stop()


def test_backup_restore_advances_history_epoch(canonical_env, tmp_path):
    drill = run_backup_restore_drill(tmp_path / "drill", seed_events=2)
    assert drill["backup_within_rpo"]
    assert drill["restore_within_rto"]
    assert drill["restore"]["history_epoch"] == 2
    assert drill["manifest"]["event_count"] == 2
    assert drill["manifest"]["sha256"]


def test_projection_rebuild_from_watermark(canonical_env):
    writer = CanonicalWriter(canonical_env["db"])
    try:
        writer.submit(_record_intent(key="rb1", identity="EARLY_MOVER:RB1"))
        writer.submit(_record_intent(key="rb2", identity="EARLY_MOVER:RB2"))
    finally:
        writer.close()
    projection = rebuild_identity_projection(
        canonical_env["db"],
        through_history_epoch=1,
        through_local_sequence=1,
    )
    assert "EARLY_MOVER:RB1" in projection["identities"]
    assert "EARLY_MOVER:RB2" not in projection["identities"]


def test_mode_off_skips_writer(canonical_env):
    settings = SimpleNamespace(opip_canonical_writer_mode="off")
    durable_record_opportunity_alert(
        identity="EARLY_MOVER:OFF",
        transition_key="READY:off",
        message_id=7,
        created_new=True,
        reservation_token="tok-off",
        scan_id="scan-off",
        state_file=canonical_env["state"],
        settings=settings,
    )
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:OFF"]["message_id"] == 7
    assert not canonical_env["db"].exists()


def test_writer_txn_latency_budget(canonical_env):
    import sys

    writer = CanonicalWriter(canonical_env["db"])
    samples: list[float] = []
    try:
        # Warm the page cache / WAL before measuring.
        for idx in range(10):
            ack = writer.submit(
                _record_intent(key=f"warm:{idx}", identity=f"EARLY_MOVER:W{idx}")
            )
            assert ack.status == "OK"
        for idx in range(100):
            t0 = time.perf_counter()
            ack = writer.submit(
                _record_intent(key=f"lat:{idx}", identity=f"EARLY_MOVER:L{idx}")
            )
            samples.append((time.perf_counter() - t0) * 1000.0)
            assert ack.status == "OK"
    finally:
        writer.close()
    samples.sort()
    p99 = samples[int(round(0.99 * (len(samples) - 1)))]
    # N8 provisional target is ≤50ms on the production Linux host. Windows
    # developer filesystems are noisier; keep a higher local ceiling there.
    budget_ms = 50.0 if sys.platform.startswith("linux") else 150.0
    assert p99 <= budget_ms, f"txn p99={p99} budget={budget_ms}"
