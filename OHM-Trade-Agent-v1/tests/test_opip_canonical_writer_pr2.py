"""PR 2 canonical writer foundation tests."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.canonical.backup import backup_database, build_backup_manifest
from app.opip.canonical.bridge import (
    durable_record_opportunity_alert,
    durable_release_opportunity_alert_reservation,
    idempotency_key_for_record,
    reconcile_capture_gap_spool,
    reconcile_pending_ops_handoffs,
    set_writer_client_for_tests,
)
from app.opip.canonical.client import CanonicalWriterClient, InProcessWriterClient
from app.opip.canonical.gap_spool import (
    GapSpoolError,
    append_capture_gap,
    evidence_window_incomplete,
    load_gap_spool,
)
from app.opip.canonical.models import WriterAck, WriterIntent
from app.opip.canonical.paths import (
    N8_CI_SANITY_TXN_P99_MS,
    N8_WRITER_TXN_P99_MS,
    SCHEMA_VERSION,
    STATE_FAMILY_EARLY_WATCH,
)
from app.opip.canonical.rebuild import rebuild_identity_projection
from app.opip.canonical.recovery import run_backup_restore_drill
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.canonical.writer import CanonicalWriter
from app.services.alert_governor import evaluate_opportunity_alert
from app.services.registry_io import load_json, save_json_atomic


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


@pytest.fixture
def make_writer_server(canonical_env):
    """Create CanonicalWriterServer instances that always stop on teardown."""
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
        except Exception:  # noqa: BLE001 — test teardown
            pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _record_intent(
    *,
    key: str,
    identity: str,
    priority: str = "NORMAL",
    transition_key: str = "READY:x",
    message_id: int = 42,
    created_new: bool = True,
    pre_state: dict | None = None,
) -> WriterIntent:
    if pre_state is None:
        pre_state = {
            "snapshot_ok": True,
            "identity_present": False,
            "transition_key": None,
            "message_id": None,
        }
    return WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority=priority,  # type: ignore[arg-type]
        idempotency_key=key,
        event_type="alert_governor.transition.recorded",
        payload={
            "identity": identity,
            "transition_key": transition_key,
            "message_id": message_id,
            "created_new": created_new,
            "scan_id": "scan-1",
            "pre_state": pre_state,
        },
        ops_handoff={
            "operation": "RECORD",
            "identity": identity,
            "transition_key": transition_key,
            "message_id": message_id,
            "created_new": created_new,
            "reservation_token": "tok-1",
            "state_family": STATE_FAMILY_EARLY_WATCH,
            "pre_state": pre_state,
        },
    )


def test_n8_activation_gate_constant_unchanged():
    assert N8_WRITER_TXN_P99_MS == 50.0
    assert N8_CI_SANITY_TXN_P99_MS >= N8_WRITER_TXN_P99_MS


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
                "state_family": STATE_FAMILY_EARLY_WATCH,
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
        family = writer._conn.execute(
            "SELECT state_file FROM alert_ops_handoffs WHERE event_id = ?",
            (ack1.event_id,),
        ).fetchone()
        assert family["state_file"] == STATE_FAMILY_EARLY_WATCH
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


def test_priority_queue_reserves_high_capacity(canonical_env, make_writer_server):
    server = make_writer_server()
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)

    def _submit(key: str, identity: str, priority: str) -> None:
        client.submit(_record_intent(key=key, identity=identity, priority=priority))

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


def test_bridge_ack_before_json_and_confirm(canonical_env, make_writer_server):
    server = make_writer_server()
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


def test_writer_unavailable_after_create_still_records_json_and_gap(canonical_env):
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    class _Down:
        def submit(self, intent):  # noqa: ANN001
            raise ConnectionError("writer down")

    set_writer_client_for_tests(_Down())  # type: ignore[arg-type]
    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:DOWN",
        transition_key="READY:down",
        state_file=canonical_env["state"],
    )
    ack = durable_record_opportunity_alert(
        identity="EARLY_MOVER:DOWN",
        transition_key="READY:down",
        message_id=55,
        created_new=True,
        reservation_token=decision.reservation_token,
        scan_id="scan-down",
        state_file=canonical_env["state"],
        settings=settings,
    )
    assert ack is not None
    assert ack.status == "RETRYABLE"
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:DOWN"]["message_id"] == 55
    assert evidence_window_incomplete()
    assert len(load_gap_spool()["unresolved"]) == 1


def test_writer_unavailable_on_release_still_releases_json_and_gap(canonical_env):
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")
    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:REL",
        transition_key="READY:rel",
        state_file=canonical_env["state"],
    )
    assert decision.reservation_token
    token = decision.reservation_token

    class _Down:
        def submit(self, intent):  # noqa: ANN001
            return WriterAck(status="RETRYABLE", error_code="QUEUE_FULL")

    set_writer_client_for_tests(_Down())  # type: ignore[arg-type]
    ack = durable_release_opportunity_alert_reservation(
        token,
        scan_id="scan-rel",
        identity="EARLY_MOVER:REL",
        transition_key="READY:rel",
        state_file=canonical_env["state"],
        settings=settings,
    )
    assert ack is not None
    assert ack.status == "RETRYABLE"
    state = load_json(canonical_env["state"])
    assert token not in (state.get("new_card_reservations") or {})
    assert evidence_window_incomplete()


def test_json_save_failure_leaves_handoff_pending(canonical_env, monkeypatch, make_writer_server):
    server = make_writer_server()
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    monkeypatch.setattr(
        "app.opip.canonical.bridge.record_opportunity_alert",
        lambda **_kwargs: None,
    )
    ack = durable_record_opportunity_alert(
        identity="EARLY_MOVER:JSONFAIL",
        transition_key="READY:jf",
        message_id=11,
        created_new=True,
        reservation_token="tok-jf",
        scan_id="scan-jf",
        state_file=canonical_env["state"],
        settings=settings,
    )
    assert ack is not None
    assert ack.status == "OK"
    assert len(client.list_pending_handoffs()) == 1
    server.stop()


def test_recovery_replay_failure_leaves_pending(canonical_env, monkeypatch, make_writer_server):
    server = make_writer_server()
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    intent = _record_intent(key="crash:1", identity="EARLY_MOVER:CRASH")
    ack = client.submit(intent)
    assert ack.status == "OK"
    assert len(client.list_pending_handoffs()) == 1

    monkeypatch.setattr(
        "app.opip.canonical.bridge.record_opportunity_alert",
        lambda **_kwargs: None,
    )
    stats = reconcile_pending_ops_handoffs(
        settings=settings,
        state_file=canonical_env["state"],
    )
    assert stats["pending"] == 1
    assert len(client.list_pending_handoffs()) == 1
    server.stop()


def test_handoff_reconcile_after_ack_before_json_crash(canonical_env, make_writer_server):
    server = make_writer_server()
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    intent = _record_intent(key="crash:2", identity="EARLY_MOVER:CRASH2")
    ack = client.submit(intent)
    assert ack.status == "OK"
    assert len(client.list_pending_handoffs()) == 1

    stats = reconcile_pending_ops_handoffs(
        settings=settings,
        state_file=canonical_env["state"],
    )
    assert stats["replayed"] == 1
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:CRASH2"]["message_id"] == 42
    assert client.list_pending_handoffs() == []
    server.stop()


def test_gap_spool_never_silently_evicts_and_marks_incomplete(canonical_env, make_writer_server):
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

    server = make_writer_server()
    set_writer_client_for_tests(InProcessWriterClient(server))
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")
    stats = reconcile_capture_gap_spool(settings=settings)
    assert stats["resolved"] == 5
    assert not evidence_window_incomplete()
    server.stop()


def test_corrupt_gap_spool_fail_closed_preserves_file(canonical_env):
    spool = canonical_env["root"] / "capture_gap_spool.json"
    spool.write_text("{not-json", encoding="utf-8")
    before = spool.read_bytes()
    with pytest.raises(GapSpoolError):
        load_gap_spool()
    with pytest.raises(GapSpoolError):
        append_capture_gap(
            idempotency_key="k",
            scan_id="s",
            identity="EARLY_MOVER:X",
            intended_event_type="alert_governor.transition.recorded",
            error_code="E",
        )
    assert spool.read_bytes() == before


def test_writer_and_spool_failure_still_records_json(canonical_env, monkeypatch):
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    class _Down:
        def submit(self, intent):  # noqa: ANN001
            raise ConnectionError("writer down")

    set_writer_client_for_tests(_Down())  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.opip.canonical.bridge.append_capture_gap",
        lambda **_kwargs: (_ for _ in ()).throw(GapSpoolError("spool dead")),
    )
    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:BOTH",
        transition_key="READY:both",
        state_file=canonical_env["state"],
    )
    ack = durable_record_opportunity_alert(
        identity="EARLY_MOVER:BOTH",
        transition_key="READY:both",
        message_id=77,
        created_new=True,
        reservation_token=decision.reservation_token,
        scan_id="scan-both",
        state_file=canonical_env["state"],
        settings=settings,
    )
    assert ack is not None
    assert ack.status == "RETRYABLE"
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:BOTH"]["message_id"] == 77


def test_writer_and_spool_failure_still_releases_json(canonical_env, monkeypatch):
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")
    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:BOTHREL",
        transition_key="READY:br",
        state_file=canonical_env["state"],
    )
    token = decision.reservation_token

    class _Down:
        def submit(self, intent):  # noqa: ANN001
            return WriterAck(status="RETRYABLE", error_code="QUEUE_FULL")

    set_writer_client_for_tests(_Down())  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.opip.canonical.bridge.append_capture_gap",
        lambda **_kwargs: (_ for _ in ()).throw(GapSpoolError("spool dead")),
    )
    ack = durable_release_opportunity_alert_reservation(
        token,
        scan_id="scan-br",
        identity="EARLY_MOVER:BOTHREL",
        transition_key="READY:br",
        state_file=canonical_env["state"],
        settings=settings,
    )
    assert ack is not None
    assert ack.status == "RETRYABLE"
    state = load_json(canonical_env["state"])
    assert token not in (state.get("new_card_reservations") or {})


def test_recovery_replays_when_json_still_at_precondition(canonical_env, make_writer_server):
    """Canonical new committed + JSON write failed → replay, not SUPERSEDED."""
    server = make_writer_server()
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    save_json_atomic(
        canonical_env["state"],
        {
            "identities": {
                "EARLY_MOVER:OLD": {
                    "transition_key": "READY:old",
                    "message_id": 10,
                }
            },
            "new_card_reservations": {},
            "new_card_history": [],
        },
    )
    pre = {
        "snapshot_ok": True,
        "identity_present": True,
        "transition_key": "READY:old",
        "message_id": 10,
    }
    intent = _record_intent(
        key="sup:replay",
        identity="EARLY_MOVER:OLD",
        transition_key="READY:new",
        message_id=20,
        created_new=False,
        pre_state=pre,
    )
    ack = client.submit(intent)
    assert ack.status == "OK"
    assert len(client.list_pending_handoffs()) == 1
    # Simulate JSON write never landing: state remains at precondition.
    assert (
        load_json(canonical_env["state"])["identities"]["EARLY_MOVER:OLD"]["transition_key"]
        == "READY:old"
    )

    stats = reconcile_pending_ops_handoffs(
        settings=settings,
        state_file=canonical_env["state"],
    )
    assert stats["replayed"] == 1
    assert stats.get("superseded", 0) == 0
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:OLD"]["transition_key"] == "READY:new"
    assert state["identities"]["EARLY_MOVER:OLD"]["message_id"] == 20
    assert client.list_pending_handoffs() == []
    server.stop()


def test_recovery_supersedes_only_when_json_proves_newer(canonical_env, make_writer_server):
    server = make_writer_server()
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")

    pre = {
        "snapshot_ok": True,
        "identity_present": True,
        "transition_key": "READY:old",
        "message_id": 10,
    }
    intent = _record_intent(
        key="sup:newer",
        identity="EARLY_MOVER:NS",
        transition_key="READY:new",
        message_id=20,
        created_new=False,
        pre_state=pre,
    )
    ack = client.submit(intent)
    assert ack.status == "OK"

    # Later operational JSON advanced past both pre and the pending target.
    save_json_atomic(
        canonical_env["state"],
        {
            "identities": {
                "EARLY_MOVER:NS": {
                    "transition_key": "READY:newer",
                    "message_id": 30,
                }
            },
            "new_card_reservations": {},
            "new_card_history": [],
        },
    )
    stats = reconcile_pending_ops_handoffs(
        settings=settings,
        state_file=canonical_env["state"],
    )
    assert stats["superseded"] == 1
    state = load_json(canonical_env["state"])
    assert state["identities"]["EARLY_MOVER:NS"]["transition_key"] == "READY:newer"
    assert client.list_pending_handoffs() == []
    server.stop()


def test_capture_gap_reconcile_never_submits_high(canonical_env):
    append_capture_gap(
        idempotency_key="gap-prio",
        scan_id="s",
        identity="EARLY_MOVER:GP",
        intended_event_type="alert_governor.transition.recorded",
        error_code="DOWN",
    )
    seen: list[str] = []

    class _Capture:
        def submit(self, intent):  # noqa: ANN001
            seen.append(str(intent.priority))
            return WriterAck(
                status="OK",
                event_id="evt-gap",
                history_epoch=1,
                local_sequence=1,
            )

    set_writer_client_for_tests(_Capture())  # type: ignore[arg-type]
    settings = SimpleNamespace(opip_canonical_writer_mode="shadow")
    stats = reconcile_capture_gap_spool(settings=settings)
    assert stats["resolved"] == 1
    assert seen == ["LOW"]
    assert "HIGH" not in seen


def test_backup_manifest_matches_snapshot_after_live_write(canonical_env, tmp_path):
    live = canonical_env["db"]
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="snap:1", identity="EARLY_MOVER:S1"))
    finally:
        writer.close()
    backup = tmp_path / "snap.sqlite3"
    backup_database(live, backup)
    manifest = build_backup_manifest(
        backup_path=backup,
        source_release_sha="808a308cd274d30b55fef47b382c229b761e07df",
    )
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="snap:2", identity="EARLY_MOVER:S2"))
    finally:
        writer.close()
    assert manifest["event_count"] == 1
    assert manifest["max_local_sequence"] == 1
    assert manifest["history_epoch"] == 1
    assert manifest["source_release_sha"].startswith("808a308")
    later = build_backup_manifest(backup_path=backup, source_release_sha="x")
    assert later["event_count"] == manifest["event_count"]
    assert later["sha256"] == manifest["sha256"]


def test_backup_restore_advances_history_epoch(canonical_env, tmp_path):
    drill = run_backup_restore_drill(tmp_path / "drill", seed_events=2)
    assert drill["backup_within_rpo"]
    assert drill["restore_within_rto"]
    assert drill["restore"]["history_epoch"] == 2
    assert drill["manifest"]["event_count"] == 2
    assert drill["manifest"]["sha256"]
    assert "source_release_sha" in drill["manifest"]


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


def test_writer_rejects_arbitrary_state_path(canonical_env):
    writer = CanonicalWriter(canonical_env["db"])
    try:
        intent = _record_intent(key="badpath", identity="EARLY_MOVER:BAD")
        intent.ops_handoff["state_family"] = "/etc/passwd"
        intent.ops_handoff.pop("state_file", None)
        ack = writer.submit(intent)
        assert ack.status == "REJECTED"
        assert ack.error_code == "INVALID_INTENT"
    finally:
        writer.close()


def test_writer_txn_latency_budget(canonical_env):
    writer = CanonicalWriter(canonical_env["db"])
    samples: list[float] = []
    try:
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
    # Keep N8 activation constant intact. Shared CI runners are not the
    # production-like measurement host; use a sanity ceiling there.
    if os.environ.get("CI"):
        assert p99 <= N8_CI_SANITY_TXN_P99_MS, f"CI sanity txn p99={p99}"
        assert N8_WRITER_TXN_P99_MS == 50.0
    else:
        assert p99 <= N8_WRITER_TXN_P99_MS, f"txn p99={p99}"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX required")
def test_real_uds_roundtrip(canonical_env, make_writer_server):
    sock_path = canonical_env["root"] / "writer.sock"
    sock_path.write_text("stale", encoding="utf-8")
    server = make_writer_server(socket_path=sock_path)
    server.start()
    try:
        listener = threading.Thread(target=server.serve_forever, daemon=True)
        listener.start()
        time.sleep(0.05)
        mode = sock_path.stat().st_mode & 0o777
        assert mode == 0o600
        client = CanonicalWriterClient(sock_path, timeout=5.0)
        health = client.health()
        assert health["status"] == "OK"
        ack = client.submit(_record_intent(key="uds:1", identity="EARLY_MOVER:UDS"))
        assert ack.status == "OK"
        dup = client.submit(_record_intent(key="uds:1", identity="EARLY_MOVER:UDS"))
        assert dup.status == "DUPLICATE_OK"
        pending = client.list_pending_handoffs()
        assert len(pending) == 1
        confirm = client.confirm_ops_applied(ack.event_id or "")
        assert confirm.status in {"OK", "DUPLICATE_OK"}
        assert client.list_pending_handoffs() == []
    finally:
        server.stop()


def test_ohm_deploy_rollback_handles_missing_writer_service():
    deploy = (_repo_root() / "deploy" / "remote" / "ohm-deploy").read_text(encoding="utf-8")
    assert "grep -qx 'opip-canonical-writer'" in deploy
    rollback = deploy.split("rollback() {", 1)[1].split("trap rollback ERR", 1)[0]
    assert "if docker compose config --services" in rollback
    assert "opip-canonical-writer" in rollback
    assert "--remove-orphans ohm-trade-agent" in rollback


# --- CodeRabbit adjudication hardening (findings 1-9) ---


def test_settings_writer_mode_accepts_off_and_shadow():
    from app.core.config import Settings
    from pydantic import ValidationError

    off = Settings(webhook_secret="test-webhook-secret", opip_canonical_writer_mode="off")
    assert off.opip_canonical_writer_mode == "off"
    shadow = Settings(
        webhook_secret="test-webhook-secret",
        opip_canonical_writer_mode="shadow",
    )
    assert shadow.opip_canonical_writer_mode == "shadow"
    with pytest.raises(ValidationError):
        Settings(webhook_secret="test-webhook-secret", opip_canonical_writer_mode="shadwo")
    with pytest.raises(ValidationError):
        Settings(webhook_secret="test-webhook-secret", opip_canonical_writer_mode="alert")


def test_backup_preserves_previous_on_backup_api_failure(canonical_env, tmp_path, monkeypatch):
    live = canonical_env["db"]
    dest = tmp_path / "good.sqlite3"
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="bk:1", identity="EARLY_MOVER:BK1"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    backup_database(live, dest)
    before = dest.read_bytes()

    real_connect = __import__(
        "app.opip.canonical.backup", fromlist=["connect"]
    ).connect

    class _BoomSource:
        def __init__(self, real):
            self._real = real

        def backup(self, _dest):  # noqa: ANN001
            raise RuntimeError("backup injected failure")

        def close(self):
            return self._real.close()

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _connect(path, *, read_only=False):  # noqa: ANN001
        conn = real_connect(path, read_only=read_only)
        if read_only:
            return _BoomSource(conn)
        return conn

    monkeypatch.setattr("app.opip.canonical.backup.connect", _connect)
    with pytest.raises(RuntimeError, match="backup injected failure"):
        backup_database(live, dest)
    assert dest.read_bytes() == before
    from app.opip.canonical.schema import validate_canonical_sqlite

    validate_canonical_sqlite(dest)


def test_backup_preserves_previous_on_validation_failure(canonical_env, tmp_path, monkeypatch):
    live = canonical_env["db"]
    dest = tmp_path / "good2.sqlite3"
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="bk:2", identity="EARLY_MOVER:BK2"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    backup_database(live, dest)
    before = dest.read_bytes()

    def _bad(_path):  # noqa: ANN001
        raise RuntimeError("validation injected failure")

    monkeypatch.setattr(
        "app.opip.canonical.backup.validate_canonical_sqlite",
        _bad,
    )
    with pytest.raises(RuntimeError, match="validation injected failure"):
        backup_database(live, dest)
    assert dest.read_bytes() == before


def test_backup_success_replaces_and_passes_integrity(canonical_env, tmp_path):
    from app.opip.canonical.schema import validate_canonical_sqlite

    live = canonical_env["db"]
    dest = tmp_path / "snap.sqlite3"
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="bk:a", identity="EARLY_MOVER:A"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    backup_database(live, dest)
    first_sha = dest.read_bytes()
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="bk:b", identity="EARLY_MOVER:B"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    backup_database(live, dest)
    assert dest.read_bytes() != first_sha
    validate_canonical_sqlite(dest)
    manifest = build_backup_manifest(backup_path=dest, source_release_sha="x")
    assert manifest["event_count"] == 2


def test_restore_copy_failure_preserves_live(canonical_env, tmp_path, monkeypatch):
    from app.opip.canonical.recovery import restore_from_backup

    live = canonical_env["db"]
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="rs:1", identity="EARLY_MOVER:RS1"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    before = live.read_bytes()
    backup = tmp_path / "bak.sqlite3"
    backup_database(live, backup)

    def _boom(*_a, **_k):  # noqa: ANN001
        raise OSError("copy injected failure")

    monkeypatch.setattr("app.opip.canonical.recovery.shutil.copy2", _boom)
    with pytest.raises(OSError, match="copy injected failure"):
        restore_from_backup(backup_db=backup, live_db=live, advance_epoch=True)
    assert live.read_bytes() == before


def test_restore_invalid_backup_preserves_live(canonical_env, tmp_path):
    from app.opip.canonical.recovery import restore_from_backup
    from app.opip.canonical.schema import CanonicalDbValidationError

    live = canonical_env["db"]
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="rs:2", identity="EARLY_MOVER:RS2"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    before = live.read_bytes()
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"not-a-sqlite-db")
    with pytest.raises((CanonicalDbValidationError, Exception)):
        restore_from_backup(backup_db=bad, live_db=live, advance_epoch=True)
    assert live.read_bytes() == before


def test_restore_validation_failure_preserves_live(canonical_env, tmp_path, monkeypatch):
    from app.opip.canonical.recovery import restore_from_backup

    live = canonical_env["db"]
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="rs:3", identity="EARLY_MOVER:RS3"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    before = live.read_bytes()
    backup = tmp_path / "bak3.sqlite3"
    backup_database(live, backup)
    calls = {"n": 0}
    real = __import__(
        "app.opip.canonical.recovery", fromlist=["validate_canonical_sqlite"]
    ).validate_canonical_sqlite

    def _wrap(path):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("validation injected failure")
        return real(path)

    monkeypatch.setattr("app.opip.canonical.recovery.validate_canonical_sqlite", _wrap)
    with pytest.raises(RuntimeError, match="validation injected failure"):
        restore_from_backup(backup_db=backup, live_db=live, advance_epoch=True)
    assert live.read_bytes() == before


def test_restore_epoch_advance_failure_preserves_live(canonical_env, tmp_path, monkeypatch):
    from app.opip.canonical.recovery import restore_from_backup

    live = canonical_env["db"]
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="rs:4", identity="EARLY_MOVER:RS4"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    before = live.read_bytes()
    backup = tmp_path / "bak4.sqlite3"
    backup_database(live, backup)

    def _boom(self):  # noqa: ANN001
        raise RuntimeError("epoch injected failure")

    monkeypatch.setattr(
        CanonicalWriter,
        "advance_history_epoch_for_restore",
        _boom,
    )
    with pytest.raises(RuntimeError, match="epoch injected failure"):
        restore_from_backup(backup_db=backup, live_db=live, advance_epoch=True)
    assert live.read_bytes() == before


def test_restore_success_advances_epoch_and_handles_sidecars(canonical_env, tmp_path):
    from app.opip.canonical.recovery import restore_from_backup
    from app.opip.canonical.schema import connect

    live = canonical_env["db"]
    writer = CanonicalWriter(live)
    try:
        writer.submit(_record_intent(key="rs:5", identity="EARLY_MOVER:RS5"))
        writer.checkpoint_wal()
    finally:
        writer.close()
    # Simulate exclusive-restore stale sidecars beside live.
    Path(str(live) + "-wal").write_bytes(b"stale-wal")
    Path(str(live) + "-shm").write_bytes(b"stale-shm")
    backup = tmp_path / "bak5.sqlite3"
    backup_database(live, backup)
    result = restore_from_backup(backup_db=backup, live_db=live, advance_epoch=True)
    assert result["history_epoch"] == 2
    assert not Path(str(live) + "-wal").exists()
    assert not Path(str(live) + "-shm").exists()
    conn = connect(live, read_only=True)
    try:
        meta = conn.execute(
            "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
        ).fetchone()
        assert int(meta["history_epoch"]) == 2
        assert int(meta["next_local_sequence"]) == 1
    finally:
        conn.close()


def test_schema_version_mismatch_fail_closed(canonical_env):
    from app.opip.canonical.schema import SchemaVersionError, connect, initialize_schema

    db = canonical_env["db"]
    writer = CanonicalWriter(db)
    writer.close()
    conn = connect(db, read_only=False)
    try:
        conn.execute("UPDATE meta SET schema_version = 99 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SchemaVersionError):
        CanonicalWriter(db)
    # Re-open raw connection and prove no new events can be committed via writer.
    with pytest.raises(SchemaVersionError):
        CanonicalWriter(db)


def test_schema_new_db_initializes_v1(canonical_env):
    from app.opip.canonical.paths import SCHEMA_VERSION
    from app.opip.canonical.schema import connect

    writer = CanonicalWriter(canonical_env["db"])
    try:
        conn = connect(canonical_env["db"], read_only=True)
        try:
            row = conn.execute("SELECT schema_version FROM meta WHERE id = 1").fetchone()
            assert int(row["schema_version"]) == SCHEMA_VERSION
        finally:
            conn.close()
    finally:
        writer.close()


def test_worker_internal_error_is_contained_and_health_degrades(
    canonical_env, make_writer_server, monkeypatch
):
    server = make_writer_server()
    client = InProcessWriterClient(server)
    set_writer_client_for_tests(client)

    def _boom(self, intent):  # noqa: ANN001
        raise RuntimeError("submit injected failure")

    monkeypatch.setattr(CanonicalWriter, "submit", _boom)
    ack = client.submit(_record_intent(key="wfail:1", identity="EARLY_MOVER:WF"))
    assert ack.status == "RETRYABLE"
    assert ack.error_code == "WORKER_INTERNAL_ERROR"
    health = server.dispatch_for_tests({"method": "HEALTH"})
    assert health["status"] == "UNHEALTHY"
    later = client.submit(_record_intent(key="wfail:2", identity="EARLY_MOVER:WF2"))
    assert later.status == "RETRYABLE"
    assert later.error_code == "WORKER_UNHEALTHY"


def test_health_detects_dead_worker(canonical_env, make_writer_server):
    server = make_writer_server()
    server.ensure_worker_started()
    time.sleep(0.02)
    # Force-join by stopping the loop without full stop(), then kill thread view.
    server.stop_event.set()
    server._worker.join(timeout=2.0)
    health = server.dispatch_for_tests({"method": "HEALTH"})
    assert health["status"] == "UNHEALTHY"
    assert health["metrics"]["worker_alive"] is False


def test_advance_epoch_missing_meta_and_rollback(canonical_env):
    import sqlite3

    writer = CanonicalWriter(canonical_env["db"])
    try:
        writer._conn.execute("DELETE FROM meta WHERE id = 1")
        writer._conn.commit()
        with pytest.raises(RuntimeError, match="meta row missing"):
            writer.advance_history_epoch_for_restore()
    finally:
        writer.close()

    writer = CanonicalWriter(canonical_env["db"])
    try:

        class _ConnProxy:
            def __init__(self, real):
                self._real = real
                self.fail_update = True

            def execute(self, sql, *args):  # noqa: ANN001
                sql_s = str(sql)
                if (
                    self.fail_update
                    and "UPDATE meta" in sql_s
                    and "history_epoch" in sql_s
                ):
                    raise sqlite3.OperationalError("update injected failure")
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

        proxy = _ConnProxy(writer._conn)
        writer._conn = proxy  # type: ignore[assignment]
        with pytest.raises(sqlite3.OperationalError, match="update injected failure"):
            writer.advance_history_epoch_for_restore()
        proxy.fail_update = False
        # Must not be wedged inside an abandoned transaction.
        ack = writer.submit(_record_intent(key="epoch:ok", identity="EARLY_MOVER:EP"))
        assert ack.status == "OK"
        epoch = writer.advance_history_epoch_for_restore()
        assert epoch == 2
        assert (
            writer._conn.execute(
                "SELECT next_local_sequence FROM meta WHERE id = 1"
            ).fetchone()[0]
            == 1
        )
    finally:
        writer.close()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX required")
def test_uds_partial_frame_times_out_and_writer_remains_healthy(
    canonical_env, make_writer_server
):
    sock_path = canonical_env["root"] / "to.sock"
    server = make_writer_server(socket_path=sock_path, client_timeout_sec=0.5)
    server.start()
    listener = threading.Thread(target=server.serve_forever, daemon=True)
    listener.start()
    time.sleep(0.05)
    try:
        # Partial header (2 of 4 bytes) then stall.
        stalled = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stalled.settimeout(3.0)
        stalled.connect(str(sock_path))
        stalled.sendall(b"\x00\x01")
        t0 = time.perf_counter()
        try:
            stalled.recv(1)
        except Exception:
            pass
        elapsed = time.perf_counter() - t0
        stalled.close()
        assert elapsed < 2.5

        # Partial payload: full header claiming 100 bytes, send 1 byte.
        stalled2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stalled2.settimeout(3.0)
        stalled2.connect(str(sock_path))
        import struct

        stalled2.sendall(struct.pack("!I", 100) + b"{")
        t1 = time.perf_counter()
        try:
            stalled2.recv(1)
        except Exception:
            pass
        assert (time.perf_counter() - t1) < 2.5
        stalled2.close()

        client = CanonicalWriterClient(sock_path, timeout=5.0)
        health = client.health()
        assert health["status"] == "OK"
        ack = client.submit(_record_intent(key="to:1", identity="EARLY_MOVER:TO"))
        assert ack.status == "OK"
    finally:
        server.stop()


def test_deploy_path_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    deploy = (_repo_root() / "deploy" / "remote" / "ohm-deploy").read_text(encoding="utf-8")
    assert "opip-canonical-writer" in deploy
