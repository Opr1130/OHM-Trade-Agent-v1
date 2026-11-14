"""Release-gate evidence for the merged resource-remediation work (#515/#516/#517).

Each test cites its acceptance criterion in ATDD-OPIP-RELEASE-GATES-V1. The tests
use real operating-system processes and real signals wherever the claim is about
interruption, because a simulated interruption proves nothing about SIGKILL.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from app.opip.canonical import writer as writer_module
from app.opip.canonical.writer import CanonicalWriter, _BoundedProjection
from app.opip.contracts import opportunity_persistence as persistence
from tests.test_opip_r3_f4_opportunity_persistence import (
    active_result,
    build_claim,
    canonical_intent,
    deferred_result,
    expired_result,
    payload_for,
)

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
RECORDED = persistence.OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED
POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="real SIGKILL/SIGTERM evidence needs a POSIX host"
)


@pytest.fixture
def canonical_db(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(tmp_path / "canonical"))
    db = tmp_path / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


# ---------------------------------------------------------------------------
# A writer child process that commits in a loop and reports every acknowledgement
# ---------------------------------------------------------------------------

_WRITER_CHILD = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, {root!r})
    os.environ["OPIP_CANONICAL_DIR"] = {canon!r}
    from pathlib import Path
    from app.opip.canonical.writer import CanonicalWriter
    from tests.test_opip_r3_f4_opportunity_persistence import (
        build_claim, canonical_intent, deferred_result, expired_result, payload_for,
    )
    STOP = ()
    rc = None
    if {handlers!r}:
        from app.jobs import run_cycle as rc
        rc._install_shutdown_handlers()
        STOP = (rc._CycleTerminated,)
    writer = CanonicalWriter(Path({db!r}))
    print("READY", flush=True)
    try:
        index = {start}
        while True:
            claim = build_claim(snapshot_id="SNAP:gate-%d" % index)
            for result in (deferred_result(claim), expired_result(claim)):
                intent = canonical_intent(payload_for(result))
                ack = writer.submit(intent)
                if ack.status == "OK":
                    print("ACK " + intent.idempotency_key, flush=True)
            index += 1
    except STOP as stopped:
        rc._record_termination(stopped.signal_name)
    finally:
        writer.close()
    """
)


def _spawn_writer(db: Path, *, start: int, handlers: bool = False) -> subprocess.Popen:
    code = _WRITER_CHILD.format(
        root=str(APP_ROOT),
        canon=str(db.parent),
        db=str(db),
        start=start,
        handlers=handlers,
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(APP_ROOT),
        env={**os.environ, "APP_ENV": "test"},
    )


def _read_acks(proc: subprocess.Popen, *, want: int, deadline: float = 60.0) -> list[str]:
    acked: list[str] = []
    stop = time.monotonic() + deadline
    assert proc.stdout is not None
    while time.monotonic() < stop:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if line.startswith("ACK "):
            acked.append(line[4:])
            if len(acked) >= want:
                return acked
    raise AssertionError(f"writer child produced only {len(acked)} acks")


def _assert_store_consistent(db: Path, acked: list[str]) -> int:
    """The durable state after an interruption: whole events, nothing partial."""
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        keys = conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0]
        # An event and its idempotency key commit together or not at all.
        assert events == keys
        sequences = [
            row[0]
            for row in conn.execute(
                "SELECT local_sequence FROM events ORDER BY history_epoch, local_sequence"
            )
        ]
        assert sequences == list(range(1, events + 1)), "sequence has a gap or duplicate"
        meta = conn.execute("SELECT next_local_sequence FROM meta WHERE id = 1").fetchone()[0]
        assert meta == events + 1, "meta sequence disagrees with the committed events"
        present = {
            row[0] for row in conn.execute("SELECT idempotency_key FROM idempotency_keys")
        }
        missing = [key for key in acked if key not in present]
        assert not missing, f"acknowledged events were lost: {missing[:3]}"
        return events
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-001 / AC-002 - real interruption of an in-progress canonical write (#515)
# ---------------------------------------------------------------------------


@POSIX_ONLY
def test_ac_001_sigkill_mid_write_never_loses_an_acknowledged_event(canonical_db):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-001: SIGKILL during a write loop leaves a consistent store and every acknowledged event durable."""
    total_acked: list[str] = []
    next_start = 0
    for round_index in range(3):
        child = _spawn_writer(canonical_db, start=next_start)
        try:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "READY"
            total_acked += _read_acks(child, want=15 + 7 * round_index)
            child.send_signal(signal.SIGKILL)
        finally:
            child.wait(timeout=30)
        assert child.returncode == -signal.SIGKILL
        next_start += 10_000
        events = _assert_store_consistent(canonical_db, total_acked)
        assert events >= len(total_acked)

    # A new writer can take the store after the kill (no stale ownership) and the
    # history continues without a gap.
    writer = CanonicalWriter(canonical_db)
    try:
        claim = build_claim(snapshot_id="SNAP:after-kill")
        ack = writer.submit(canonical_intent(payload_for(deferred_result(claim))))
        assert ack.status == "OK", ack
    finally:
        writer.close()
    _assert_store_consistent(canonical_db, total_acked)


@POSIX_ONLY
def test_ac_002_sigterm_through_the_real_handler_stops_in_order(canonical_db):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-002: SIGTERM raises the orderly-stop exception, leaves a consistent store, and the terminal evidence is truthful."""
    child = _spawn_writer(canonical_db, start=0, handlers=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        acked = _read_acks(child, want=20)
        child.send_signal(signal.SIGTERM)
        out, err = child.communicate(timeout=60)
    except BaseException:
        child.kill()
        child.wait(timeout=30)
        raise
    acked += [line[4:] for line in out.splitlines() if line.startswith("ACK ")]

    assert child.returncode == 0, err
    assert "OPIP_UNIFIED_CYCLE_STATUS=TERMINATED" in out
    assert "OPIP_UNIFIED_CYCLE_TERMINATED_REASON=SIGTERM" in out
    # Terminal evidence must never read as a completion.
    assert "OPIP_UNIFIED_CYCLE_COMPLETED_AT=" not in out
    assert "OPIP_UNIFIED_CYCLE_STATUS=SUCCESS" not in out
    _assert_store_consistent(canonical_db, acked)

    # The store is immediately re-openable: ownership was released in order.
    CanonicalWriter(canonical_db).close()


@POSIX_ONLY
def test_ac_002_a_real_signal_mid_cycle_keeps_prior_log_evidence(tmp_path):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-002: the cycle log is append-only evidence; a terminated cycle adds to it and never rewrites it."""
    log = tmp_path / "cycle.log"
    log.write_text("OPIP_UNIFIED_CYCLE_STATUS=SUCCESS\nOPIP_UNIFIED_CYCLE_COMPLETED_AT=PRIOR\n")
    code = textwrap.dedent(
        f"""
        import os, sys, time
        sys.path.insert(0, {str(APP_ROOT)!r})
        os.environ["OHM_DATA_DIR"] = {str(tmp_path)!r}
        from app.jobs import run_cycle as rc
        rc._install_shutdown_handlers()
        print("READY", flush=True)
        try:
            time.sleep(60)
        except rc._CycleTerminated as stopped:
            rc._record_termination(stopped.signal_name)
        """
    )
    with log.open("ab") as sink:
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", code],
            stdout=sink,
            stderr=subprocess.STDOUT,
            cwd=str(APP_ROOT),
            env={**os.environ, "APP_ENV": "test"},
        )
        try:
            for _ in range(200):
                if "READY" in log.read_text():
                    break
                time.sleep(0.05)
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=30)
        finally:
            if child.poll() is None:
                child.kill()
    text = log.read_text()
    assert text.startswith(
        "OPIP_UNIFIED_CYCLE_STATUS=SUCCESS\nOPIP_UNIFIED_CYCLE_COMPLETED_AT=PRIOR\n"
    )
    tail = text.split("COMPLETED_AT=PRIOR\n", 1)[1]
    assert "OPIP_UNIFIED_CYCLE_STATUS=TERMINATED" in tail
    assert "COMPLETED_AT" not in tail


def test_ac_003_an_interruption_mid_transaction_is_rolled_back_not_acknowledged(
    canonical_db, monkeypatch
):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-003: a BaseException between BEGIN and COMMIT must not leave a half-written event that the writer then acknowledges."""

    class Interrupted(BaseException):
        pass

    writer = CanonicalWriter(canonical_db)
    try:
        claim = build_claim(snapshot_id="SNAP:interrupt")
        intent = canonical_intent(payload_for(deferred_result(claim)))
        real = writer._upsert_alert_identity_projection

        def interrupt_after_insert(*args, **kwargs):
            raise Interrupted()

        monkeypatch.setattr(writer, "_upsert_alert_identity_projection", interrupt_after_insert)
        with pytest.raises(Interrupted):
            writer.submit(intent)
        monkeypatch.setattr(writer, "_upsert_alert_identity_projection", real)

        assert writer._conn.in_transaction is False
        count = writer._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 0, "half-written event is visible to the writer's own connection"

        # Resubmission is a real first commit, not a duplicate of a phantom.
        ack = writer.submit(intent)
        assert ack.status == "OK", ack
        assert writer._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        writer.close()
    conn = sqlite3.connect(str(canonical_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        conn.close()


def test_ac_003_the_cycle_never_constructs_a_writing_canonical_writer():
    """ATDD-OPIP-RELEASE-GATES-V1/AC-003: the cycle's signal handler cannot interrupt a canonical commit because the cycle owns no writing connection."""
    cycle_sources = [
        APP_ROOT / "app" / "jobs" / "run_cycle.py",
    ]
    for path in cycle_sources:
        text = path.read_text(encoding="utf-8")
        assert "CanonicalWriter(" not in text, path
    constructors = []
    for path in (APP_ROOT / "app").rglob("*.py"):
        if "CanonicalWriter(" in path.read_text(encoding="utf-8", errors="ignore"):
            constructors.append(path.relative_to(APP_ROOT).as_posix())
    # Only the writer service owns a writing connection; recovery is an offline
    # operator tool. Anything new here needs its own interruption evidence.
    assert sorted(constructors) == sorted(
        [
            "app/opip/canonical/recovery.py",
            "app/opip/canonical/server.py",
        ]
    ), constructors


# ---------------------------------------------------------------------------
# AC-004 / AC-005 - #516 scale: startup is not quadratic, policy is pinned
# ---------------------------------------------------------------------------


def _seed_interleaved(writer: CanonicalWriter, episodes: int) -> None:
    """Every episode's second transition lands after all the first transitions, so
    a small retention bound must release each episode before it is revisited."""
    claims = [build_claim(snapshot_id=f"SNAP:interleave-{i}") for i in range(episodes)]
    for claim in claims:
        ack = writer.submit(canonical_intent(payload_for(deferred_result(claim))))
        assert ack.status == "OK", ack
    for claim in claims:
        ack = writer.submit(canonical_intent(payload_for(expired_result(claim))))
        assert ack.status == "OK", ack


def test_ac_004_hydration_does_not_rescan_the_event_type_per_released_episode(
    canonical_db, monkeypatch
):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-004: with retention far below history, hydrating reads the type once; it never re-reads it per released episode."""
    seeder = CanonicalWriter(canonical_db)
    try:
        _seed_interleaved(seeder, 40)
    finally:
        seeder.close()

    monkeypatch.setattr(writer_module, "PROJECTION_RETENTION_ENTRIES", 4)
    calls: list[str] = []
    original = CanonicalWriter._iter_event_rows

    def counting(self, event_type, columns, *, after=(0, -1)):
        calls.append(event_type)
        return original(self, event_type, columns, after=after)

    monkeypatch.setattr(CanonicalWriter, "_iter_event_rows", counting)
    reopened = CanonicalWriter(canonical_db)
    try:
        opportunity_reads = [c for c in calls if c == RECORDED]
        # One hydration pass. Before the identity index this was 1 + one full
        # re-read for every released episode (>= 40 here), which made startup
        # quadratic in history.
        assert len(opportunity_reads) == 1, len(opportunity_reads)
        assert reopened._opportunity_transition_projection.released > 0
    finally:
        reopened.close()


def test_ac_004_the_episode_lookup_is_planned_as_an_index_seek(canonical_db):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-004: the per-episode read is a range seek on idx_events_episode_identity, not a scan."""
    writer = CanonicalWriter(canonical_db)
    try:
        plan = " ".join(
            str(row[3])
            for row in writer._conn.execute(
                "EXPLAIN QUERY PLAN " + writer_module._EPISODE_ROWS_SQL,
                ("OPEP:x", 0, 0, -1, 10),
            )
        )
        assert "idx_events_episode_identity" in plan, plan
        assert "SCAN" not in plan, plan
    finally:
        writer.close()


def test_ac_004_the_index_is_additive_partial_and_outside_the_generic_schema(
    canonical_db,
):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-004: IF NOT EXISTS, partial on the one event type, created by the writer, and the generic schema stays free of the F4 token it is pinned against."""
    schema_source = (APP_ROOT / "app" / "opip" / "canonical" / "schema.py").read_text(
        encoding="utf-8"
    )
    assert "opportunity" not in schema_source.lower()
    sql = writer_module._EPISODE_IDENTITY_INDEX_SQL
    assert sql.startswith("CREATE INDEX IF NOT EXISTS idx_events_episode_identity")
    assert f"WHERE event_type = '{RECORDED}'" in sql
    writer = CanonicalWriter(canonical_db)
    try:
        names = {
            row[0]
            for row in writer._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "idx_events_episode_identity" in names
    finally:
        writer.close()


def test_ac_005_retention_is_first_in_first_out_and_a_refresh_does_not_reorder():
    """ATDD-OPIP-RELEASE-GATES-V1/AC-005: the intended policy is FIFO by first insertion; touching an entry does not extend its life."""
    projection = _BoundedProjection(limit=3)
    for key in ("a", "b", "c"):
        projection[key] = 1
    projection["a"] = 2  # a refresh, not a new identity
    projection["d"] = 1
    assert "a" not in projection, "a refresh must not move an entry to the back"
    assert list(projection) == ["b", "c", "d"]
    assert projection.released == 1


def test_ac_005_a_release_changes_latency_never_the_answer(canonical_db, monkeypatch):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-005: FIFO vs LRU is a latency choice only, because a released episode re-derives to the identical state."""
    monkeypatch.setattr(writer_module, "PROJECTION_RETENTION_ENTRIES", 2)
    writer = CanonicalWriter(canonical_db)
    try:
        _seed_interleaved(writer, 12)
        claim = build_claim(snapshot_id="SNAP:interleave-0")
        episode_id = persistence.opportunity_transition_episode_id(
            payload_for(active_result(claim))
        )
        writer._ensure_opportunity_transition_projection()
        retained = writer._opportunity_episode_state(episode_id)
        for name in (
            "_opportunity_transition_projection",
            "_opportunity_transition_count",
            "_opportunity_transition_last",
        ):
            getattr(writer, name).clear()
        rederived = writer._opportunity_episode_state(episode_id)
        assert rederived == retained
        assert rederived is not None and rederived.lifecycle_state == "TERMINAL"
        assert writer._opportunity_transition_count[episode_id] == 2
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# AC-006 - #517 default behaviour
# ---------------------------------------------------------------------------


def test_ac_006_telemetry_is_off_by_default_and_unwired(monkeypatch):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-006: with nothing set, per-phase telemetry is disabled; and no production module imports it, so setting the flag alone changes nothing."""
    from app.services import phase_resources

    monkeypatch.delenv(phase_resources.TELEMETRY_ENV, raising=False)
    assert phase_resources.telemetry_enabled() is False
    importers = [
        path.relative_to(APP_ROOT).as_posix()
        for path in (APP_ROOT / "app").rglob("*.py")
        if path.name != "phase_resources.py"
        and "phase_resources" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert importers == [], (
        "phase_resources is now wired into runtime code; its default-off and "
        f"cost evidence must be re-verified first: {importers}"
    )


def test_ac_006_early_watch_defaults_to_the_unchanged_active_behaviour(monkeypatch):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-006: an unset, empty or unrecognised mode keeps today's alerting; only the explicit value withholds, and only EARLY_MOVER."""
    from app.services import early_watch_mode as mode

    monkeypatch.delenv(mode.MODE_ENV, raising=False)
    assert mode.resolve_early_watch_mode() == mode.MODE_ACTIVE
    assert mode.automatic_notifications_enabled() is True
    for value in ("", " ", "ACTIVE", "evidence-only", "off", "true", "1", "none"):
        assert mode.resolve_early_watch_mode({mode.MODE_ENV: value}) == mode.MODE_ACTIVE, value
    assert (
        mode.resolve_early_watch_mode({mode.MODE_ENV: "EVIDENCE_ONLY"})
        == mode.MODE_EVIDENCE_ONLY
    )
    source = (APP_ROOT / "app" / "services" / "movement_discovery_v2.py").read_text(
        encoding="utf-8"
    )
    # The switch is consulted in exactly one place, and that place is the EARLY_MOVER
    # automatic delivery.
    assert source.count("automatic_notifications_enabled()") == 1
    block = source.split("if not automatic_notifications_enabled():", 1)[1][:600]
    assert 'alert_family="EARLY_MOVER"' in block


def test_ac_006_no_deploy_asset_enables_either_new_flag():
    """ATDD-OPIP-RELEASE-GATES-V1/AC-006: nothing in the deploy tree, cron or compose turns on telemetry or evidence-only mode."""
    repo = APP_ROOT.parent
    flagged = []
    for root in (APP_ROOT / "deploy", repo / "docker-compose.yml", repo / "docker-compose.prod.yml"):
        paths = [root] if root.is_file() else list(root.rglob("*")) if root.exists() else []
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for flag in ("OPIP_PHASE_RESOURCE_TELEMETRY", "OPIP_EARLY_WATCH_MODE"):
                if flag in text:
                    flagged.append((path.name, flag))
    assert flagged == [], flagged


# ---------------------------------------------------------------------------
# AC-007 - #517 bundled cleanup is isolated from Linux runtime behaviour
# ---------------------------------------------------------------------------


@POSIX_ONLY
def test_ac_007_the_posix_lock_invariants_are_still_enforced(tmp_path):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-007: the lock-portability cleanup relaxes nothing on POSIX - group-writable and hard-linked lock files are still refused."""
    from app.jobs import capture_feature_bus_shadow as capture

    writable = tmp_path / "group-writable.lock"
    writable.write_text("")
    os.chmod(writable, 0o664)
    with pytest.raises(PermissionError):
        capture.CaptureProcessLock(str(writable)).acquire()

    linked = tmp_path / "linked.lock"
    linked.write_text("")
    os.chmod(linked, 0o600)
    os.link(linked, tmp_path / "second-name.lock")
    with pytest.raises(PermissionError):
        capture.CaptureProcessLock(str(linked)).acquire()

    sound = tmp_path / "sound.lock"
    lock = capture.CaptureProcessLock(str(sound))
    assert lock.acquire() is True
    lock.release()


# ---------------------------------------------------------------------------
# AC-008 - the scale probe is measurement tooling only
# ---------------------------------------------------------------------------


def test_ac_008_the_scale_probe_refuses_live_looking_paths_and_copies_for_measuring(
    tmp_path,
):
    """ATDD-OPIP-RELEASE-GATES-V1/AC-008: the probe never opens a live store and measures a verified snapshot, leaving the original byte-identical."""
    sys.path.insert(0, str(APP_ROOT / "tools"))
    try:
        import opip_canonical_scale_probe as probe
    finally:
        sys.path.pop(0)

    with pytest.raises(SystemExit):
        probe._refuse_live(Path("/app/data/canonical/opip_canonical_v1.sqlite3"))

    db = tmp_path / "store" / "c.sqlite3"
    db.parent.mkdir()
    seed = CanonicalWriter(db)
    seed.close()
    before = db.read_bytes()
    target = tmp_path / "snap" / "snapshot.sqlite3"
    info = probe.snapshot_database(db, target)
    assert target.is_file() and info["integrity"] == "ok"
    assert db.read_bytes() == before
