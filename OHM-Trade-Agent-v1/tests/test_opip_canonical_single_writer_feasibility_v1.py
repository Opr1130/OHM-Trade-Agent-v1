"""Canonical single-writer authority and the bounded release-runtime verifier (AC-015).

Production Release Pipeline run ``37174893501`` failed with a healthy unified
cycle, no runtime-verifier PASS/FAIL receipt, a deploy that ended in the outer
watchdog's exit ``124``, and a rollback after which ``opip-canonical-writer`` was
unhealthy (``SAFE_BASELINE_ROLLBACK=UNPROVEN``). Three coupled defects produced
that outcome, and this module proves each fix against the real composition
wherever practical:

1. ``app/jobs/capture_feasibility_evidence_shadow.py`` opened a SECOND writable
   ``CanonicalWriter`` against the live canonical store. ``opip-canonical-writer``
   already owns that store through ``CanonicalStoreLock`` for its whole process
   lifetime, so the producer's ``CanonicalWriter(db_path())`` raised
   ``CanonicalStoreBusyError`` before it could publish a single
   ``feasibility.evidence.recorded`` event -- which is exactly what
   ``release_runtime_verifier`` then waited for until it gave up.
2. ``app/services/release_runtime_verifier.py`` tested its own deadline only
   AFTER a read, so an ordinary "matching F5 evidence never arrived" outcome
   always began one more full read and slept up to a whole poll interval past its
   360-second window, landing on the deploy's ~370-second outer watchdog as a bare
   exit ``124`` instead of a structured FAIL receipt.
3. ``deploy/remote/ohm-deploy`` rolled back without first quiescing the
   candidate's evidence producers, which run inside the still-running candidate
   core container and could win the canonical store lock from the
   about-to-restore writer inside the writer-rebuild window.

The single-writer tests drive a real ``CanonicalWriterServer`` (the sole writable
store owner) plus the real ``CanonicalWriterClient``, the real production
submitter and the real evidence builder, so the production path cannot silently
regress back to a second writable store handle.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import app.jobs.capture_feasibility_evidence_shadow as producer  # noqa: E402
from app.exchanges.kraken import BookLevel, Candle, PreTradeBook  # noqa: E402
from app.opip.canonical.client import CanonicalWriterClient  # noqa: E402
from app.opip.canonical.schema import CanonicalStoreBusyError  # noqa: E402
from app.opip.canonical.server import CanonicalWriterServer  # noqa: E402
from app.opip.canonical.writer import CanonicalWriter  # noqa: E402
from app.opip.fev_evidence_event import (  # noqa: E402
    build_feasibility_evidence_recorded_payload,
    reconstruct_feasibility_evidence_recorded_payload,
)
from app.services import release_runtime_verifier as verifier  # noqa: E402

pytestmark = pytest.mark.acceptance

DEPLOY = APP_ROOT / "deploy" / "remote" / "ohm-deploy"
PRODUCER_SOURCE = APP_ROOT / "app" / "jobs" / "capture_feasibility_evidence_shadow.py"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / "ATDD-RELEASE-PIPELINE-v1.md"

T = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
VENUE_INSTRUMENT_ID = "SOLUSD"

AF_UNIX = hasattr(socket, "AF_UNIX")


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_env(tmp_path, monkeypatch):
    """One isolated canonical directory (store path + writer socket)."""
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(root))
    return {"root": root, "db": root / "opip_canonical_v1.sqlite3", "sock": root / "writer.sock"}


@pytest.fixture
def make_writer_server(canonical_env):
    """CanonicalWriterServer instances that are always stopped on teardown."""
    created: list[CanonicalWriterServer] = []

    def _factory(**kwargs):
        params = {"db_path": canonical_env["db"], "socket_path": canonical_env["sock"]}
        params.update(kwargs)
        server = CanonicalWriterServer(**params)
        created.append(server)
        return server

    yield _factory
    for server in created:
        try:
            server.stop()
        except Exception:  # noqa: BLE001 - test teardown
            pass


def _start_serving(server: CanonicalWriterServer) -> threading.Thread:
    """Start the real writer server, serve the UDS, and wait for the socket file."""
    server.start()
    listener = threading.Thread(target=server.serve_forever, daemon=True)
    listener.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if server.socket_path.exists():
            return listener
        time.sleep(0.01)
    raise AssertionError("canonical writer socket did not appear")


#: The two genuine canonical-writer transports. Both share the server's real
#: dispatch -> queue -> commit path; only the UDS framing differs, and the
#: production transport is exercised wherever the platform has AF_UNIX.
TRANSPORTS = [pytest.param("in_process", id="in_process")]
if AF_UNIX:
    TRANSPORTS.append(pytest.param("uds", id="uds"))


def _start_transport(server: CanonicalWriterServer, canonical_env, transport: str):
    """Return a real canonical-writer client over the requested transport."""
    if transport == "uds":
        _start_serving(server)
        return CanonicalWriterClient(canonical_env["sock"], timeout=5.0)
    from app.opip.canonical.client import InProcessWriterClient

    return InProcessWriterClient(server)


def _settings(**overrides) -> SimpleNamespace:
    base = {
        "opip_feature_bus_mode": "shadow",
        "opip_canonical_writer_mode": "shadow",
        "opip_feasibility_capture_limit": 8,
        "opip_feasibility_capture_budget_seconds": 45,
        "opip_feasibility_capture_notional_usd": 500.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Snapshot:
    def __init__(self, n: int, *, cutoff: datetime = T, visible_at: datetime | None = None):
        self.snapshot_id = f"SNAP:{n}"
        self.instrument_version_id = INSTRUMENT_VERSION_ID
        self.venue_instrument_id = VENUE_INSTRUMENT_ID
        self.evaluation_cutoff = cutoff
        self.evaluated_at_utc = cutoff
        self.availability = SimpleNamespace(
            source_at_utc=cutoff - timedelta(seconds=60),
            visible_at_utc=visible_at if visible_at is not None else cutoff,
        )


class _FakeReader:
    """Per-record reader whose cursor is each committed row's own position."""

    def __init__(self, snapshots, *, head: int | None = None):
        self._snapshots = list(snapshots)
        self._head = len(self._snapshots) if head is None else head

    def head_cursor(self):
        return None if self._head == 0 else (0, self._head)

    def read_records(self, *, after=None, limit=200):
        from app.opip.features.committed_snapshot_reader import CommittedSnapshotRecord

        start = 0 if after is None else int(after[1])
        window = self._snapshots[start : start + limit]
        records = tuple(
            CommittedSnapshotRecord(
                event_id=f"EV:{start + i + 1}",
                history_epoch=0,
                local_sequence=start + i + 1,
                snapshot=snapshot,
                rejected=False,
            )
            for i, snapshot in enumerate(window)
        )
        tail = (0, start + len(window)) if window else after
        return records, tail

    def close(self):
        pass


@pytest.fixture
def primed_cursor(tmp_path):
    """A cursor already at the committed head, so a pass processes records."""
    path = tmp_path / "fev_cursor.json"
    producer._save_cursor(path, (0, 0))
    return path


def _minute_candles(*, end_epoch: datetime, count: int) -> list[Candle]:
    candles = []
    for i in range(count):
        ts = int(end_epoch.timestamp()) - (count - 1 - i) * 60
        candles.append(
            Candle(
                timestamp=ts,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                vwap=100.0,
                volume=10.0,
                trade_count=1,
            )
        )
    return candles


class _FakeKrakenClient:
    """Minimal deterministic market seam for the real evidence builder."""

    def __init__(self, *, candles):
        self._candles = list(candles)

    def get_ohlc(self, pair, interval=60, since=None):
        return list(self._candles)

    def get_ticker(self, pair):
        return {"last": 100.0}

    def get_pre_trade(self, symbol):
        return PreTradeBook(
            symbol=symbol,
            bids=[BookLevel(price=99.9, quantity=10.0)],
            asks=[BookLevel(price=100.1, quantity=10.0)],
        )

    def get_post_trade(self, symbol, count=100):
        return []


def _build_long(snapshot, direction):
    """The real production LONG evidence builder, on a deterministic market seam."""
    return producer.build_long_feasibility_evidence(
        snapshot,
        client=_FakeKrakenClient(candles=_minute_candles(end_epoch=T, count=6)),
        notional_usd=500.0,
        acquisition_instant=T,
        interval_minutes=1,
        interval_seconds=60,
    )


# ---------------------------------------------------------------------------
# AC-015(a) Sole canonical writer authority
# ---------------------------------------------------------------------------


def test_ac_015_sole_writer_owns_the_store_and_refuses_a_second_writer(
    canonical_env, make_writer_server
):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: the live writer owns the store exclusively, so the pre-fix production capture path (`CanonicalWriter(db_path())`) must fail closed."""
    # Constructing the server is enough: it opens the SOLE writable CanonicalWriter
    # and therefore holds CanonicalStoreLock for its whole lifetime.
    server = make_writer_server()
    # This is exactly what the pre-fix producer did inside the capture process,
    # and exactly why it could never publish feasibility evidence.
    with pytest.raises(CanonicalStoreBusyError):
        CanonicalWriter(canonical_env["db"])
    # Ownership is an OS advisory lock: the refusal is real while the owner runs,
    # and the store becomes writable again only once the owner has released it.
    server.stop()
    released = CanonicalWriter(canonical_env["db"])
    released.close()


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_ac_015_feasibility_capture_publishes_through_the_writer_client_into_canonical_history(
    transport, canonical_env, make_writer_server, primed_cursor
):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: the feasibility producer publishes a genuine `feasibility.evidence.recorded` through the writer client into canonical history while the writer server owns the store."""
    server = make_writer_server()
    submit = producer.resolve_canonical_submitter(
        client=_start_transport(server, canonical_env, transport)
    )

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=_build_long,
        submit_payload=submit,
        cursor_path=primed_cursor,
        now=T,
    )

    assert summary.recorded == 1
    assert summary.retryable == 0
    assert producer._load_cursor(primed_cursor) == (0, 1)

    # The event is genuine, readable canonical history -- not a fabricated local
    # projection -- and it carries the snapshot's own epoch/source cutoff.
    reader = CanonicalWriter.for_reads(canonical_env["db"])
    try:
        rows, _ = reader.read_feasibility_evidence_records()
    finally:
        reader.close()
    assert len(rows) == 1
    payload = json.loads(rows[0])
    _, record = reconstruct_feasibility_evidence_recorded_payload(payload)
    assert record.instrument_version_id == INSTRUMENT_VERSION_ID
    assert record.evaluation_time == T
    assert record.source_cutoff == T

    # The producer's submission did not disturb single-writer exclusivity.
    with pytest.raises(CanonicalStoreBusyError):
        CanonicalWriter(canonical_env["db"])


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_ac_015_writer_client_outage_is_fail_closed_and_does_not_advance_or_drop_evidence(
    transport, canonical_env, make_writer_server, primed_cursor
):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: an unreachable canonical writer yields a retryable disposition, publishes nothing and does not advance the evidence cursor, so the record is re-attempted rather than silently dropped."""
    server = make_writer_server()
    submit = producer.resolve_canonical_submitter(
        client=_start_transport(server, canonical_env, transport)
    )
    first = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=_build_long,
        submit_payload=submit,
        cursor_path=primed_cursor,
        now=T,
    )
    assert first.recorded == 1

    # The production write path now goes away (the writer process died).
    server.stop()

    retry_cursor = primed_cursor.parent / "retry_cursor.json"
    producer._save_cursor(retry_cursor, (0, 0))
    outage = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(2)]),
        evidence_builder=_build_long,
        submit_payload=submit,
        cursor_path=retry_cursor,
        now=T,
    )

    assert outage.retryable == 1
    assert outage.recorded == 0
    assert outage.rejected == 0
    # Fail-closed: nothing was published and the cursor did NOT advance, so the
    # record is retried next pass instead of being silently skipped.
    assert producer._load_cursor(retry_cursor) == (0, 0)
    reader = CanonicalWriter.for_reads(canonical_env["db"])
    try:
        rows, _ = reader.read_feasibility_evidence_records()
    finally:
        reader.close()
    assert len(rows) == 1  # still exactly the one committed before the outage


def test_ac_015_production_submitter_is_a_client_and_the_module_opens_no_writable_store(
    canonical_env,
):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: the production capture source imports no writable store and constructs no writable store handle anywhere in its executable code (AST, not text, so docstrings cannot mask a regression)."""
    import ast

    source = PRODUCER_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # No writable-store import of any spelling.
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    assert not any(
        name.endswith(("canonical.writer", "canonical.writer.CanonicalWriter"))
        or name == "CanonicalWriter"
        or name == "CanonicalStoreLock"
        for name in imported
    ), sorted(imported)

    # Executable code constructs no writable store handle. Docstring mentions of
    # the classes are fine; an actual `CanonicalWriter(...)`/`CanonicalStoreLock(...)`
    # call is the production defect this test freezes.
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    constructed |= {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "CanonicalWriter" not in constructed
    assert "CanonicalStoreLock" not in constructed

    # The submitter is a client, by construction.
    assert "resolve_canonical_submitter" in source
    assert "from app.opip.canonical.client import CanonicalWriterClient" in source

    # Behavioural proof: the production default submitter fails at the SOCKET
    # (no writer is listening), never at the store lock, so it holds no writable
    # canonical connection of its own.
    submit = producer.resolve_canonical_submitter()
    payload = build_feasibility_evidence_recorded_payload(_build_long(_Snapshot(1), "LONG"))
    with pytest.raises((OSError, RuntimeError)) as excinfo:
        submit(payload)
    assert not isinstance(excinfo.value, CanonicalStoreBusyError)


# ---------------------------------------------------------------------------
# AC-015(c) Bounded runtime-verifier window and structured FAIL receipt
# ---------------------------------------------------------------------------


class _FakeClock:
    """Module-local fake for ``release_runtime_verifier.time`` (no global patching)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:  # pragma: no cover - never reached
        self.sleeps.append(seconds)
        self.now += seconds


def _verifier_evidence_pair(now: datetime):
    """Two fresh consecutive 60s snapshots plus matching prospective F5 evidence."""
    newer = (now - timedelta(seconds=30)).replace(microsecond=0)
    older = newer - timedelta(seconds=60)
    snapshots = [
        SimpleNamespace(
            snapshot_id="CUT:older",
            instrument_version_id=INSTRUMENT_VERSION_ID,
            evaluation_cutoff=older,
            evaluation_grid_seconds=60,
        ),
        SimpleNamespace(
            snapshot_id="CUT:newer",
            instrument_version_id=INSTRUMENT_VERSION_ID,
            evaluation_cutoff=newer,
            evaluation_grid_seconds=60,
        ),
    ]
    evidence = [
        SimpleNamespace(
            instrument_version_id=INSTRUMENT_VERSION_ID,
            evaluation_time=newer,
            source_cutoff=newer,
        )
    ]
    return snapshots, evidence


def _enable_evidence_shadow(monkeypatch):
    monkeypatch.setenv("OPIP_RELEASE_PROFILE", "EVIDENCE_SHADOW")


def test_ac_015_valid_evidence_returns_pass_within_the_verifier_budget(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: fresh consecutive snapshots with matching prospect F5 evidence return PASS inside the verifier's own budget."""
    _enable_evidence_shadow(monkeypatch)
    now = datetime.now(timezone.utc)
    snapshots, evidence = _verifier_evidence_pair(now)
    monkeypatch.setattr(verifier, "_read_new_evidence", lambda baseline: (snapshots, evidence))
    monkeypatch.setattr(
        verifier,
        "_verify_live_posture",
        lambda name: {
            "profile": name,
            "modes": {},
            "protection": "HEALTHY",
            "target_authority": "ABSENT",
        },
    )

    result = verifier.verify_release_runtime(
        expected_sha="a" * 40,
        baseline={},
        ready_after=now - timedelta(seconds=200),
        timeout_seconds=30,
    )

    assert result["status"] == "PASS"
    assert result["evidence"]["consecutive_60s_snapshots"] is True
    assert result["evidence"]["feasibility_matches_fresh_snapshot"] is True


def test_ac_015_missing_matching_f5_evidence_returns_structured_fail_within_the_budget(
    monkeypatch,
):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: no matching F5 evidence terminates under the verifier's own control and carries the last observed evidence counters."""
    _enable_evidence_shadow(monkeypatch)
    monkeypatch.setattr(verifier, "_read_new_evidence", lambda baseline: ([], []))
    monkeypatch.setattr(
        verifier, "_verify_live_posture", lambda name: pytest.fail("posture must not run")
    )

    started = time.monotonic()
    with pytest.raises(verifier.ReleaseRuntimeVerificationTimeout) as excinfo:
        verifier.verify_release_runtime(
            expected_sha="a" * 40,
            baseline={},
            ready_after=datetime.now(timezone.utc) - timedelta(seconds=200),
            timeout_seconds=1,
        )
    elapsed = time.monotonic() - started

    exc = excinfo.value
    assert exc.stage == "EVIDENCE_TIMEOUT"
    assert exc.attempts >= 1
    assert elapsed < verifier.MAX_WAIT_SECONDS + verifier.MAX_SINGLE_READ_SECONDS
    for key in verifier.FAILURE_EVIDENCE_KEYS:
        assert key in exc.evidence, key
    assert exc.evidence["feature_snapshot_count"] == 0
    assert exc.evidence["feasibility_evidence_count"] == 0
    assert exc.evidence["consecutive_60s_snapshots"] is False
    assert exc.evidence["feasibility_matches_fresh_snapshot"] is False


def test_ac_015_receipt_cli_reports_structured_fail_instead_of_the_outer_watchdog(
    canonical_env,
):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: the verifier CLI emits a structured FAIL receipt (never a bare watchdog timeout) when the canonical store holds no matching evidence."""
    # A real (empty, schema-initialized) canonical store, then a real process run.
    writer = CanonicalWriter(canonical_env["db"])
    writer.close()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(APP_ROOT)
    env["OPIP_CANONICAL_DIR"] = str(canonical_env["root"])
    env["OPIP_RELEASE_PROFILE"] = "EVIDENCE_SHADOW"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.services.release_runtime_verifier",
            "--expected-sha",
            "a" * 40,
            "--baseline-json",
            "{}",
            "--ready-after",
            (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat(),
            "--timeout-seconds",
            "1",
        ],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 1, completed.stderr
    assert "OPIP_RELEASE_RUNTIME_VERIFICATION=FAIL" in completed.stdout
    assert (
        "OPIP_RELEASE_RUNTIME_FAILURE=ReleaseRuntimeVerificationTimeout"
        in completed.stdout
    )
    assert "OPIP_RELEASE_RUNTIME_FAILURE_STAGE=EVIDENCE_TIMEOUT" in completed.stdout
    for marker in (
        "OPIP_RELEASE_FEATURE_SNAPSHOT_COUNT=0",
        "OPIP_RELEASE_FRESH_INSTRUMENT_COUNT=0",
        "OPIP_RELEASE_CONSECUTIVE_60S_SNAPSHOTS=false",
        "OPIP_RELEASE_FEASIBILITY_EVIDENCE_COUNT=0",
        "OPIP_RELEASE_FEASIBILITY_MATCHES_FRESH_SNAPSHOT=false",
    ):
        assert marker in completed.stdout, marker


def test_ac_015_slow_read_stops_at_the_declared_read_bound_without_overrunning(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: one read past the declared single-read bound stops the loop with an explicit READ_OVERRUN reason instead of stacking unbounded work."""
    _enable_evidence_shadow(monkeypatch)
    clock = _FakeClock()
    monkeypatch.setattr(verifier, "time", clock)
    calls = {"n": 0}

    def _slow_read(_baseline):
        calls["n"] += 1
        clock.now += verifier.MAX_SINGLE_READ_SECONDS + 1.0
        return [], []

    monkeypatch.setattr(verifier, "_read_new_evidence", _slow_read)
    monkeypatch.setattr(
        verifier, "_verify_live_posture", lambda name: pytest.fail("posture must not run")
    )

    with pytest.raises(verifier.ReleaseRuntimeVerificationTimeout) as excinfo:
        verifier.verify_release_runtime(
            expected_sha="a" * 40,
            baseline={},
            ready_after=datetime.now(timezone.utc) - timedelta(seconds=200),
            timeout_seconds=verifier.MAX_WAIT_SECONDS,
        )

    # Exactly one bounded read was started, the loop did not sleep again, and the
    # reason is explicit rather than an opaque outer timeout.
    assert calls["n"] == 1
    assert clock.sleeps == []
    assert excinfo.value.stage == "READ_OVERRUN"
    assert excinfo.value.observed_seconds <= (
        verifier.MAX_WAIT_SECONDS + verifier.MAX_SINGLE_READ_SECONDS
    )


def test_ac_015_slow_but_successful_read_is_not_falsely_failed(monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: a read slower than the declared bound that still proves valid evidence returns PASS, so containment never becomes a false failure."""
    _enable_evidence_shadow(monkeypatch)
    clock = _FakeClock()
    monkeypatch.setattr(verifier, "time", clock)
    now = datetime.now(timezone.utc)
    snapshots, evidence = _verifier_evidence_pair(now)

    def _slow_but_valid(_baseline):
        clock.now += verifier.MAX_SINGLE_READ_SECONDS + 1.0
        return snapshots, evidence

    monkeypatch.setattr(verifier, "_read_new_evidence", _slow_but_valid)
    monkeypatch.setattr(
        verifier,
        "_verify_live_posture",
        lambda name: {
            "profile": name,
            "modes": {},
            "protection": "HEALTHY",
            "target_authority": "ABSENT",
        },
    )

    result = verifier.verify_release_runtime(
        expected_sha="a" * 40,
        baseline={},
        ready_after=now - timedelta(seconds=200),
        timeout_seconds=verifier.MAX_WAIT_SECONDS,
    )
    assert result["status"] == "PASS"


def test_ac_015_deploy_containment_exceeds_the_verifier_window_and_is_not_the_normal_timeout():
    """ATDD-RELEASE-PIPELINE-v1/AC-015: the app window is unchanged at 360s, the deploy's outer containment exceeds it by more than the declared single-read bound, and the outer `timeout` wraps the verifier invocation."""
    deploy = DEPLOY.read_text(encoding="utf-8")

    # The internal verifier window is deliberately NOT raised.
    assert verifier.MAX_WAIT_SECONDS == 360
    assert "RUNTIME_VERIFIER_TIMEOUT_SECONDS=360" in deploy
    assert (
        f"RUNTIME_VERIFIER_TIMEOUT_SECONDS={verifier.MAX_WAIT_SECONDS}" in deploy
    )

    # The outer watchdog is emergency containment only: it must strictly exceed
    # the verifier's window plus the worst-case in-flight read.
    assert "RUNTIME_VERIFIER_CONTAINMENT_MARGIN_SECONDS=60" in deploy
    assert (
        "VERIFIER_CONTAINMENT_SECONDS=$((RUNTIME_VERIFIER_TIMEOUT_SECONDS + "
        "RUNTIME_VERIFIER_CONTAINMENT_MARGIN_SECONDS))" in deploy
    )
    assert 'timeout --signal=TERM --kill-after=5s "$VERIFIER_CONTAINMENT_SECONDS" \\' in deploy
    assert '--timeout-seconds "$RUNTIME_VERIFIER_TIMEOUT_SECONDS"' in deploy
    assert "OPIP_RUNTIME_VERIFY_CONTAINMENT_SECONDS=" in deploy

    # Derived numerically: margin covers the app's declared read bound, so the
    # verifier's own structured FAIL always wins the race.
    margin = 60
    assert margin > verifier.MAX_SINGLE_READ_SECONDS
    containment = verifier.MAX_WAIT_SECONDS + margin
    assert containment > verifier.MAX_WAIT_SECONDS + verifier.MAX_SINGLE_READ_SECONDS


# ---------------------------------------------------------------------------
# AC-015(d) Rollback producer quiescence and writer recovery
# ---------------------------------------------------------------------------


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _function_block(name: str, next_name: str) -> str:
    text = _deploy()
    start = text.index(f"{name}() {{")
    end = text.index(f"{next_name}() {{", start)
    return text[start:end].rstrip()


def _rollback_block() -> str:
    text = _deploy()
    start = text.index("rollback() {")
    end = text.index("\ntrap rollback ERR", start)
    return text[start:end]


def _quiescence_helpers() -> str:
    """The complete AC-015 rollback producer-quiescence implementation.

    Release, launch-lock acquisition, the in-container process proof, and the
    composing barrier are extracted together so the adversarial tests exercise the
    real descriptor/lock mechanics rather than a paraphrase.
    """
    text = _deploy()
    start = text.index("release_evidence_producer_locks() {")
    end = text.index("writer_health_diagnostics() {", start)
    return text[start:end].rstrip()


def _quiescence_header(workdir: str, wait_seconds: int = 5) -> str:
    """Deterministic globals for driving the quiescence functions in isolation.

    Lock identities are relative to ``workdir`` so the tests never touch the real
    ``/var/run`` producer locks.
    """
    return (
        "set -Eeuo pipefail\n"
        f"cd {shlex.quote(Path(workdir).as_posix())}\n"
        "EVIDENCE_PRODUCER_CRON_ENTRIES=(opip-feature-bus-capture opip-feasibility-evidence-capture)\n"
        "EVIDENCE_PRODUCER_QUIESCE_SECONDS=60\n"
        "EVIDENCE_PRODUCER_HOST_LOCKS=(opip-feature-bus-capture.lock opip-feasibility-capture.lock)\n"
        f"EVIDENCE_PRODUCER_LOCK_WAIT_SECONDS={wait_seconds}\n"
        'EVIDENCE_PRODUCER_QUIESCENCE_REASON=""\n'
        'EVIDENCE_PRODUCER_PROCESS_STATE=""\n'
        "EVIDENCE_PRODUCER_LOCK_FDS=()\n"
    )


#: A zero-producer, container-present `docker`/`timeout` seam: `ps` returns a
#: container id and the bounded exec reports no matching producer processes.
_ZERO_PRODUCER_SEAM = (
    'docker() { if [[ "${1:-}" == "compose" && "${2:-}" == "ps" ]]; then echo "cid-1"; return 0; fi; return 0; }\n'
    "timeout() {\n"
    '  echo "OPIP_EVIDENCE_PRODUCER_PROCESSES_SIGNALLED=0"\n'
    '  echo "OPIP_EVIDENCE_PRODUCER_PROCESSES_REMAINING=0"\n'
    '  echo "OPIP_EVIDENCE_PRODUCER_PROCESS_QUIESCENCE=QUIESCED"\n'
    "  return 0\n"
    "}\n"
)


def _rollback_harness(tmp: Path, quiescence_rc: int) -> subprocess.CompletedProcess:
    """Run the REAL rollback control flow with every external effect stubbed.

    Every side effect is recorded in ``<tmp>/calls.log`` so a test can prove what
    did and did not execute: a fail-closed quiescence must never reach the SHA
    reset, the writer rebuild/start, or the SAFE_BASELINE success receipt.
    """
    log = tmp / "calls.log"
    block = _rollback_block()
    script = (
        "set -uo pipefail\n"
        f"LOG={shlex.quote(log.as_posix())}\n"
        ': >"$LOG"\n'
        "PREVIOUS_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        f"APP_ROOT={shlex.quote(tmp.as_posix())}\n"
        f"SAFE_BASELINE_OVERRIDE={shlex.quote((tmp / 'override.yml').as_posix())}\n"
        f"LAST_GOOD_FILE={shlex.quote((tmp / 'last-good-sha').as_posix())}\n"
        "GIT=(git)\n"
        'git() { echo "git $*" >>"$LOG"; return 0; }\n'
        'stop_paper_stack() { echo "stop_paper_stack" >>"$LOG"; }\n'
        'cleanup_snapshot() { echo "cleanup_snapshot" >>"$LOG"; }\n'
        'preserve_scheduler_snapshot_on_abort() { echo "preserve_scheduler_snapshot_on_abort" >>"$LOG"; }\n'
        'restore_scheduler_state() { echo "restore_scheduler_state" >>"$LOG"; }\n'
        'release_evidence_producer_locks() { echo "release_evidence_producer_locks" >>"$LOG"; }\n'
        'write_safe_baseline_override() { echo "write_safe_baseline_override" >>"$LOG"; }\n'
        'wait_core_health() { echo "wait_core_health" >>"$LOG"; return 0; }\n'
        'wait_writer_health() { echo "wait_writer_health" >>"$LOG"; return 0; }\n'
        'writer_health_diagnostics() { echo "writer_health_diagnostics" >>"$LOG"; return 0; }\n'
        'validate_safe_baseline_modes() { echo "validate_safe_baseline_modes" >>"$LOG"; return 0; }\n'
        'start_paper_stack() { echo "start_paper_stack" >>"$LOG"; return 0; }\n'
        'wait_paper_health() { echo "wait_paper_health" >>"$LOG"; return 0; }\n'
        'docker() { echo "docker $*" >>"$LOG"; '
        'if [[ " $* " == *" config "* && " $* " == *" --services "* ]]; then echo "opip-canonical-writer"; fi; '
        "return 0; }\n"
        f'quiesce_evidence_producers() {{ echo "quiesce_evidence_producers" >>"$LOG"; return {int(quiescence_rc)}; }}\n'
        f"{block}\n"
        "false\n"
        "rollback\n"
    )
    return _run_bash_script(script, str(tmp), name="rollback.sh", timeout=60)


def _writer_restoration_precedes_barrier(block: str) -> bool:
    """True when a canonical-writer restoration step appears before the quiescence guard.

    The AC-015 invariant is positional, not merely ordered: NO restoration step may
    precede the barrier, so a mutation that hoists one above it is detected even
    though every token is still present.
    """
    guard = block.index("if ! quiesce_evidence_producers")
    for token in (
        "checkout -f main",
        "reset --hard",
        "write_safe_baseline_override",
        "opip-canonical-writer",
    ):
        found = block.find(token)
        if found != -1 and found < guard:
            return True
    return False


def _bash() -> str | None:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _is_fork_failure(proc: subprocess.CompletedProcess) -> bool:
    stderr = proc.stderr or ""
    return (
        proc.returncode in (254, 3221225794)
        or "fork:" in stderr
        or "dofork" in stderr
        or "Resource temporarily unavailable" in stderr
    )


def _run_bash_script(
    script: str, tmpdir: str, *, name: str = "script.sh", timeout: int = 90
) -> subprocess.CompletedProcess:
    """Run a multi-line script from a file (not ``-c``).

    A file is used because a long multi-line ``-c`` argument can be silently
    dropped by some bash builds, and because heredocs are parsed reliably there.
    """
    path = Path(tmpdir) / name
    path.write_text(script, encoding="utf-8")
    return subprocess.run(
        [_bash(), str(path)], capture_output=True, text=True, timeout=timeout
    )


def test_ac_015_rollback_quiesces_producers_before_the_writer_restore():
    """ATDD-RELEASE-PIPELINE-v1/AC-015: rollback quiesces candidate evidence producers before the SHA reset/rebuild, and only emits SAFE_BASELINE_ROLLBACK=SUCCESS after health, modes, scheduler and paper are proven."""
    block = _rollback_block()

    # Quiescence is invoked exactly once, as a fail-closed precondition, after the
    # paper stack is stopped and BEFORE any rebuild/reset that could let a
    # producer win the store lock.
    assert block.count("if ! quiesce_evidence_producers; then") == 1
    order = (
        "stop_paper_stack",
        "if ! quiesce_evidence_producers; then",
        "checkout -f main",
        "reset --hard",
        "write_safe_baseline_override",
        "wait_core_health",
        "wait_writer_health",
        "validate_safe_baseline_modes",
        "restore_scheduler_state",
        "release_evidence_producer_locks",
        "wait_paper_health",
        "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS",
    )
    # Sequential forward search, so the error-branch occurrences of
    # `restore_scheduler_state` (which precede `wait_writer_health` in the source)
    # cannot satisfy the ordering: only the happy path matters. Finding every token
    # ahead of the previous one proves the intended sequence exists in order.
    cursor = 0
    for token in order:
        found = block.find(token, cursor)
        assert found != -1, f"rollback is missing or misorders {token!r}"
        cursor = found + len(token)

    # The barrier is a precondition, not just an ordering: nothing that restores
    # the canonical writer may appear before the quiescence guard, and the held
    # launch locks are released only after the LAST scheduler restore.
    assert not _writer_restoration_precedes_barrier(block)
    assert block.rfind("release_evidence_producer_locks") > block.rfind(
        "restore_scheduler_state"
    )
    assert "OPIP_ROLLBACK_ABORTED=PRODUCER_QUIESCENCE_UNPROVEN" in block
    assert "OPIP_SAFE_BASELINE_ROLLBACK=UNPROVEN" in block

    # A writer-health failure surfaces classified diagnostics before the exit.
    writer_failure = block.index("rollback writer health check failed")
    assert "writer_health_diagnostics" in block[writer_failure:]

    # Both bounded evidence producers are snapshotted AND restored, so the exact
    # pre-deploy scheduler state survives the rollback.
    snapshot = _function_block("snapshot_scheduler_state", "restore_scheduler_state")
    restore = _function_block("restore_scheduler_state", "cleanup_snapshot")
    for entry in ("opip-feature-bus-capture", "opip-feasibility-evidence-capture"):
        assert entry in snapshot, entry
        assert entry in restore, entry


def test_ac_015_rollback_never_claims_unproven_producer_quiescence():
    """ATDD-RELEASE-PIPELINE-v1/AC-015: quiescence is fail-closed. A failed exec returns nonzero and reports `FAILED` with a reason (never a claimed `QUIESCED`), the launch locks are taken before any process proof, and ownership is never released by unlinking a lock file."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _quiescence_helpers()

    with tempfile.TemporaryDirectory() as d:
        # (1) No container: honest NO_CONTAINER, zero counters, launch locks taken.
        no_container = _run_bash_script(
            _quiescence_header(d)
            + 'docker() { return 0; }\n'
            + f"{fn}\n"
            + "quiesce_evidence_producers\n",
            d,
            name="no_container.sh",
            timeout=60,
        )
        if _is_fork_failure(no_container):
            pytest.skip("bash cannot fork reliably in this environment")
        assert no_container.returncode == 0, no_container.stderr
        assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=NO_CONTAINER" in no_container.stdout
        assert "OPIP_EVIDENCE_PRODUCER_PROCESSES_REMAINING=0" in no_container.stdout
        assert "OPIP_EVIDENCE_PRODUCER_LAUNCH_LOCKS=ACQUIRED" in no_container.stdout
        assert "OPIP_EVIDENCE_PRODUCER_LAUNCH_LOCK_COUNT=2" in no_container.stdout

        # (2) The exec itself fails: FAILED + reason, nonzero, never `QUIESCED`.
        failed_exec = _run_bash_script(
            _quiescence_header(d)
            + 'docker() { if [[ "${1:-}" == "compose" && "${2:-}" == "ps" ]]; then echo "cid-1"; return 0; fi; return 0; }\n'
            + "timeout() { return 1; }\n"
            + f"{fn}\n"
            + "if quiesce_evidence_producers; then echo QUIESCE_RC=0; else echo QUIESCE_RC=$?; fi\n",
            d,
            name="failed_exec.sh",
            timeout=60,
        )
        if _is_fork_failure(failed_exec):
            pytest.skip("bash cannot fork reliably in this environment")
        assert failed_exec.returncode == 0, failed_exec.stderr
        assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=FAILED" in failed_exec.stderr
        assert (
            "OPIP_EVIDENCE_PRODUCER_QUIESCENCE_REASON=PRODUCER_PROCESS_STATE_UNKNOWN"
            in failed_exec.stderr
        )
        assert "QUIESCE_RC=1" in failed_exec.stdout
        assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=QUIESCED" not in failed_exec.stdout

        # Structural: the escalation re-proves absence and reports survivors, the
        # launch barrier is a bounded `flock`, and no `.lock` file is ever removed.
        assert "SIGKILL" in fn
        assert "NOT_QUIESCED" in fn
        assert "flock -x -w" in fn
        assert "writer.lock" not in fn
        assert "store_lock" not in fn
        for line in fn.splitlines():
            if ".lock" in line:
                assert "rm " not in line, line


def test_ac_015_rollback_launch_lock_barrier_waits_for_an_in_flight_wrapper():
    """ATDD-RELEASE-PIPELINE-v1/AC-015: an already-running cron wrapper owns a producer launch lock, so quiescence bounds its wait against that owner, fails closed on timeout, and proceeds only once the lock is free."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _quiescence_helpers()

    with tempfile.TemporaryDirectory() as d:
        script = (
            _quiescence_header(d, wait_seconds=2)
            + 'docker() { return 0; }\n'
            + f"{fn}\n"
            # An in-flight host cron wrapper: a SEPARATE process owns the launch
            # lock and has not yet exited.
            + "( exec 200>opip-feature-bus-capture.lock; flock -x 200; exec sleep 30 ) &\n"
            + "HOLDER=$!\n"
            + "sleep 1\n"
            + "start=$(date +%s)\n"
            + "if quiesce_evidence_producers; then echo QUIESCE_RC=0; else echo QUIESCE_RC=$?; fi\n"
            + 'echo "WAITED_SECONDS=$(( $(date +%s) - start ))"\n'
            + "kill \"$HOLDER\" 2>/dev/null || true\n"
            + "wait \"$HOLDER\" 2>/dev/null || true\n"
            + "sleep 1\n"
            + "if quiesce_evidence_producers; then echo RETRY_RC=0; else echo RETRY_RC=$?; fi\n"
        )
        proc = _run_bash_script(script, d, timeout=90)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")

    assert proc.returncode == 0, proc.stderr
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=FAILED" in proc.stderr
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE_REASON=LAUNCH_LOCK_TIMEOUT" in proc.stderr
    assert "QUIESCE_RC=1" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=QUIESCED" not in proc.stdout
    # It bounded and actually WAITED for the owner rather than declaring quiescence.
    waited = int(re.search(r"WAITED_SECONDS=(\d+)", proc.stdout).group(1))
    assert waited >= 1, proc.stdout
    # Once the wrapper finished and released, the barrier is acquired and detail
    # quiescence proceeds.
    assert "RETRY_RC=0" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_LAUNCH_LOCKS=ACQUIRED" in proc.stdout


def test_ac_015_rollback_launch_lock_barrier_blocks_a_delayed_producer():
    """ATDD-RELEASE-PIPELINE-v1/AC-015: cron removal alone is insufficient. A wrapper can launch a producer before the barrier, and a producer that appears only after the first clean process scan is blocked by the held launch locks until release."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _quiescence_helpers()

    with tempfile.TemporaryDirectory() as d:
        script = (
            _quiescence_header(d, wait_seconds=5)
            + _ZERO_PRODUCER_SEAM
            + f"{fn}\n"
            # (1) BEFORE rollback takes the barrier, a wrapper can launch a producer.
            + "if flock -n opip-feature-bus-capture.lock -c 'echo EARLY_PRODUCER_LAUNCHED'; then echo EARLY_WRAPPER=ACQUIRED; else echo EARLY_WRAPPER=BLOCKED; fi\n"
            # (2) Quiescence proves zero in-container processes and RETAINS locks.
            + "if quiesce_evidence_producers; then echo QUIESCE_RC=0; else echo QUIESCE_RC=$?; fi\n"
            # (3) The dangerous gap: the producer appears only now, after the empty
            # scan. Its wrapper must be blocked by the barrier rollback holds.
            + "if flock -n opip-feature-bus-capture.lock -c 'echo LATE_PRODUCER_LAUNCHED'; then echo LATE_WRAPPER=ACQUIRED; else echo LATE_WRAPPER=BLOCKED; fi\n"
            + "if flock -n opip-feasibility-capture.lock -c 'echo LATE_FEASIBILITY_PRODUCER_LAUNCHED'; then echo LATE_FEASIBILITY_WRAPPER=ACQUIRED; else echo LATE_FEASIBILITY_WRAPPER=BLOCKED; fi\n"
            # (4) Release, then prove the locks are genuinely freed again.
            + "release_evidence_producer_locks\n"
            + "if flock -n opip-feature-bus-capture.lock -c 'echo POST_RELEASE_PRODUCER_LAUNCHED'; then echo POST_RELEASE_WRAPPER=ACQUIRED; else echo POST_RELEASE_WRAPPER=BLOCKED; fi\n"
        )
        proc = _run_bash_script(script, d, timeout=60)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")

    assert proc.returncode == 0, proc.stderr
    # The race is real: without the barrier a wrapper starts a producer.
    assert "EARLY_PRODUCER_LAUNCHED" in proc.stdout
    assert "EARLY_WRAPPER=ACQUIRED" in proc.stdout
    # Quiescence proved zero processes and kept the barrier.
    assert "QUIESCE_RC=0" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=QUIESCED" in proc.stdout
    # The delayed launch is blocked while the barrier is held.
    assert "LATE_PRODUCER_LAUNCHED" not in proc.stdout
    assert "LATE_WRAPPER=BLOCKED" in proc.stdout
    assert "LATE_FEASIBILITY_PRODUCER_LAUNCHED" not in proc.stdout
    assert "LATE_FEASIBILITY_WRAPPER=BLOCKED" in proc.stdout
    # Release frees them only when rollback is done with the barrier.
    assert "OPIP_EVIDENCE_PRODUCER_LAUNCH_LOCKS=RELEASED" in proc.stdout
    assert "POST_RELEASE_PRODUCER_LAUNCHED" in proc.stdout
    assert "POST_RELEASE_WRAPPER=ACQUIRED" in proc.stdout


def test_ac_015_quiescence_survivor_after_sigkill_is_fail_closed(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: a producer that survives TERM/KILL makes quiescence return nonzero, and rollback never resets the SHA, restores the writer, or emits a success receipt."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _quiescence_helpers()

    with tempfile.TemporaryDirectory() as d:
        script = (
            _quiescence_header(d, wait_seconds=5)
            + 'docker() { if [[ "${1:-}" == "compose" && "${2:-}" == "ps" ]]; then echo "cid-1"; return 0; fi; return 0; }\n'
            + "timeout() {\n"
            + '  echo "OPIP_EVIDENCE_PRODUCER_PROCESSES_SIGNALLED=1"\n'
            + '  echo "OPIP_EVIDENCE_PRODUCER_PROCESSES_REMAINING=1"\n'
            + '  echo "OPIP_EVIDENCE_PRODUCER_PROCESS_QUIESCENCE=NOT_QUIESCED"\n'
            + "  return 1\n"
            + "}\n"
            + f"{fn}\n"
            + "if quiesce_evidence_producers; then echo QUIESCE_RC=0; else echo QUIESCE_RC=$?; fi\n"
        )
        proc = _run_bash_script(script, d, timeout=60)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")

    assert proc.returncode == 0, proc.stderr
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=FAILED" in proc.stderr
    assert (
        "OPIP_EVIDENCE_PRODUCER_QUIESCENCE_REASON=PRODUCER_SURVIVED_TERM_AND_KILL"
        in proc.stderr
    )
    assert "OPIP_EVIDENCE_PRODUCER_PROCESS_STATE=NOT_QUIESCED" in proc.stderr
    assert "QUIESCE_RC=1" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=QUIESCED" not in proc.stdout

    harness = _rollback_harness(tmp_path, quiescence_rc=1)
    if _is_fork_failure(harness):
        pytest.skip("bash cannot fork reliably in this environment")
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "git checkout" not in calls
    assert "git reset" not in calls
    assert "write_safe_baseline_override" not in calls
    assert "wait_core_health" not in calls
    assert "wait_writer_health" not in calls
    assert "restore_scheduler_state" not in calls
    assert "release_evidence_producer_locks" not in calls
    # The abort must not destroy the pre-deploy scheduler transaction; it is
    # preserved as durable operator recovery evidence instead.
    assert "cleanup_snapshot" not in calls
    assert "preserve_scheduler_snapshot_on_abort" in calls
    assert "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS" not in (harness.stdout + harness.stderr)
    assert "OPIP_SAFE_BASELINE_ROLLBACK=UNPROVEN" in harness.stderr
    assert "OPIP_ROLLBACK_ABORTED=PRODUCER_QUIESCENCE_UNPROVEN" in harness.stderr


def test_ac_015_quiescence_exec_failure_is_fail_closed(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: an unprovable process state (`UNKNOWN`) returns nonzero, and rollback stops before any canonical-writer restoration instead of proceeding on a guess."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _quiescence_helpers()

    with tempfile.TemporaryDirectory() as d:
        script = (
            _quiescence_header(d, wait_seconds=5)
            + 'docker() { if [[ "${1:-}" == "compose" && "${2:-}" == "ps" ]]; then echo "cid-1"; return 0; fi; return 0; }\n'
            + "timeout() { return 1; }\n"
            + f"{fn}\n"
            + "if quiesce_evidence_producers; then echo QUIESCE_RC=0; else echo QUIESCE_RC=$?; fi\n"
        )
        proc = _run_bash_script(script, d, timeout=60)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")

    assert proc.returncode == 0, proc.stderr
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=FAILED" in proc.stderr
    assert (
        "OPIP_EVIDENCE_PRODUCER_QUIESCENCE_REASON=PRODUCER_PROCESS_STATE_UNKNOWN"
        in proc.stderr
    )
    assert "OPIP_EVIDENCE_PRODUCER_PROCESS_STATE=UNKNOWN" in proc.stderr
    assert "QUIESCE_RC=1" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=QUIESCED" not in proc.stdout

    harness = _rollback_harness(tmp_path, quiescence_rc=1)
    if _is_fork_failure(harness):
        pytest.skip("bash cannot fork reliably in this environment")
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "git reset" not in calls
    assert "opip-canonical-writer" not in calls
    assert "release_evidence_producer_locks" not in calls
    assert "cleanup_snapshot" not in calls
    assert "preserve_scheduler_snapshot_on_abort" in calls
    assert "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS" not in (harness.stdout + harness.stderr)
    assert "OPIP_ROLLBACK_ABORTED=PRODUCER_QUIESCENCE_UNPROVEN" in harness.stderr


def test_ac_015_producer_barrier_precedes_writer_restoration():
    """ATDD-RELEASE-PIPELINE-v1/AC-015: the quiescence barrier is a positional precondition — remove schedule, take both launch locks, prove zero in-container producers, and only then touch the writer — and hoisting a restoration step above it is detected."""
    block = _rollback_block()

    # No canonical-writer restoration step may appear before the barrier guard.
    assert not _writer_restoration_precedes_barrier(block)
    assert block.index("if ! quiesce_evidence_producers") < block.index("reset --hard")
    # The held launch locks are released only after the LAST scheduler restore.
    assert block.rfind("release_evidence_producer_locks") > block.rfind(
        "restore_scheduler_state"
    )

    # Inside the barrier the order is exactly: schedule removal -> launch locks ->
    # process proof.
    qfn = _function_block("quiesce_evidence_producers", "writer_health_diagnostics")
    schedule = qfn.index("OPIP_EVIDENCE_PRODUCER_ENTRIES_REMOVED")
    locks = qfn.index("acquire_evidence_producer_locks")
    procs = qfn.index("producer_process_quiescence")
    assert schedule < locks < procs

    # Sentinel mutation: hoisting the SHA reset above the barrier must fail the
    # positional check even though every token is still present.
    mutated = block.replace(
        "  if ! quiesce_evidence_producers; then",
        '  "${GIT[@]}" reset --hard "$PREVIOUS_SHA"\n  if ! quiesce_evidence_producers; then',
        1,
    )
    assert mutated != block
    assert _writer_restoration_precedes_barrier(mutated)


def test_ac_015_clean_quiescence_holds_locks_until_scheduler_restore_then_succeeds(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: a clean quiescence acquires BOTH launch locks, proves zero producers, keeps the barrier through writer/core/mode validation, and the rollback control flow restores the scheduler before releasing them and still emits `OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS`."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _quiescence_helpers()

    with tempfile.TemporaryDirectory() as d:
        script = (
            _quiescence_header(d, wait_seconds=5)
            + _ZERO_PRODUCER_SEAM
            + f"{fn}\n"
            + "if quiesce_evidence_producers; then echo QUIESCE_RC=0; else echo QUIESCE_RC=$?; fi\n"
            # While rollback holds the barrier a late wrapper cannot launch.
            + "if flock -n opip-feature-bus-capture.lock -c 'echo UNEXPECTED_PRODUCER'; then echo HELD=NO; else echo HELD=YES; fi\n"
            + "release_evidence_producer_locks\n"
            + "if flock -n opip-feature-bus-capture.lock -c 'echo EXPECTED_PRODUCER'; then echo FREED=YES; else echo FREED=NO; fi\n"
        )
        proc = _run_bash_script(script, d, timeout=60)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")

    assert proc.returncode == 0, proc.stderr
    assert "QUIESCE_RC=0" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_LAUNCH_LOCKS=ACQUIRED" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_LAUNCH_LOCK_COUNT=2" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_PROCESSES_REMAINING=0" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=QUIESCED" in proc.stdout
    assert "HELD=YES" in proc.stdout
    assert "OPIP_EVIDENCE_PRODUCER_LAUNCH_LOCKS=RELEASED" in proc.stdout
    assert "FREED=YES" in proc.stdout
    assert "UNEXPECTED_PRODUCER" not in proc.stdout

    harness = _rollback_harness(tmp_path, quiescence_rc=0)
    if _is_fork_failure(harness):
        pytest.skip("bash cannot fork reliably in this environment")
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    for expected in (
        "git checkout -f main",
        "git reset --hard",
        "write_safe_baseline_override",
        "wait_core_health",
        "wait_writer_health",
        "validate_safe_baseline_modes",
        "start_paper_stack",
    ):
        assert expected in calls, expected
    assert calls.index("quiesce_evidence_producers") < calls.index("git reset --hard")
    assert calls.index("restore_scheduler_state") < calls.index(
        "release_evidence_producer_locks"
    )
    assert "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS" in harness.stdout


def test_ac_015_quiescence_abort_preserves_the_scheduler_snapshot(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: a fail-closed quiescence abort must not delete the pre-deploy scheduler transaction. The snapshot is the only durable copy of the pre-deploy cron.d entries, root crontab and installed remote-op scripts, so it is preserved under a name that cannot break the deploy-controller bootstrap's exactly-one-transaction proof."""
    block = _rollback_block()

    # The abort branch never calls the destructive cleanup; it preserves.
    abort = block[
        block.index("if ! quiesce_evidence_producers") : block.index(
            '  "${GIT[@]}" checkout -f main'
        )
    ]
    assert "cleanup_snapshot" not in abort
    assert "preserve_scheduler_snapshot_on_abort" in abort

    fn = _function_block(
        "preserve_scheduler_snapshot_on_abort", "write_safe_baseline_override"
    )
    assert "OPIP_ROLLBACK_SCHEDULER_SNAPSHOT_PRESERVED" in fn
    # A preserved copy lives outside the transaction namespace that the
    # deploy-controller bootstrap counts, and nothing is deleted.
    assert "scheduler-recovery." in fn
    assert "rm -rf" not in fn
    # The override is dropped: no rollback was applied.
    assert 'rm -f "$SAFE_BASELINE_OVERRIDE"' in fn

    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")

    state = tmp_path / "state"
    state.mkdir()
    snapshot = state / "scheduler-before.A1B2C3"
    snapshot.mkdir()
    (snapshot / "ohm-unified-cycle").write_text("MAILTO=opip\n", encoding="utf-8")
    (snapshot / "root.crontab.present").write_text("", encoding="utf-8")
    override = tmp_path / "safe-baseline-rollback.override.yml"
    override.write_text("services: {}\n", encoding="utf-8")

    script = (
        "set -uo pipefail\n"
        f"STATE_DIR={shlex.quote(state.as_posix())}\n"
        f"SCHEDULER_SNAPSHOT={shlex.quote(snapshot.as_posix())}\n"
        f"SAFE_BASELINE_OVERRIDE={shlex.quote(override.as_posix())}\n"
        f"{fn}\n"
        "preserve_scheduler_snapshot_on_abort\n"
    )
    proc = _run_bash_script(script, str(tmp_path), name="preserve.sh", timeout=60)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")

    assert proc.returncode == 0, proc.stderr
    # The transaction is gone from the namespace the bootstrap proof counts,
    # but its content survives intact elsewhere.
    assert not snapshot.exists()
    assert list(state.glob("scheduler-before.*")) == []
    preserved = list(state.glob("scheduler-recovery.*"))
    assert len(preserved) == 1, proc.stdout
    assert (preserved[0] / "ohm-unified-cycle").read_text(
        encoding="utf-8"
    ) == "MAILTO=opip\n"
    assert (preserved[0] / "root.crontab.present").is_file()
    markers = [
        line
        for line in proc.stdout.splitlines()
        if line.startswith("OPIP_ROLLBACK_SCHEDULER_SNAPSHOT_PRESERVED=")
    ]
    assert len(markers) == 1, proc.stdout
    assert markers[0].split("=", 1)[1].endswith(
        f"scheduler-recovery.{snapshot.name}"
    )
    assert not override.exists()


@pytest.mark.parametrize(
    ("log_text", "expected"),
    [
        (
            "canonical writer failed: CanonicalStoreBusyError: canonical store is "
            "already owned exclusively by another writer or restore",
            "STORE_LOCK_CONTENTION",
        ),
        (
            "sqlite3.OperationalError: database disk image is malformed",
            "SQLITE_SCHEMA_OR_INTEGRITY",
        ),
        (
            "OSError: [Errno 98] Address already in use",
            "STALE_UNIX_SOCKET",
        ),
        (
            "Traceback (most recent call last):\n  RuntimeError: boom",
            "WRITER_PROCESS_CRASH",
        ),
        (
            "container health: starting",
            "CONTAINER_HEALTHCHECK_OR_STARTUP",
        ),
    ],
)
def test_ac_015_writer_health_failure_is_classified_for_the_operator(log_text, expected):
    """ATDD-RELEASE-PIPELINE-v1/AC-015: a writer health failure emits bounded diagnostics classified across lock contention, SQLite/schema integrity, stale socket, process crash and healthcheck/startup."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _function_block("writer_health_diagnostics", "rollback")

    with tempfile.TemporaryDirectory() as d:
        log_file = Path(d) / "writer.log"
        log_file.write_text(log_text + "\n", encoding="utf-8")
        script = (
            "set -Eeuo pipefail\n"
            "docker() {\n"
            '  if [[ "${1:-}" == "compose" && "${2:-}" == "ps" ]]; then echo "cid-1"; return 0; fi\n'
            '  if [[ "${1:-}" == "inspect" ]]; then\n'
            '    case "${2:-}" in\n'
            '      *State.Health*) echo "unhealthy" ;;\n'
            '      *State.ExitCode*) echo "1" ;;\n'
            '      *RestartCount*) echo "7" ;;\n'
            '      *) echo "unknown" ;;\n'
            "    esac\n"
            "    return 0\n"
            "  fi\n"
            '  if [[ "${1:-}" == "compose" && "${2:-}" == "logs" ]]; then '
            f"cat {shlex.quote(log_file.as_posix())}; return 0; fi\n"
            "  return 0\n"
            "}\n"
            f"{fn}\n"
            "writer_health_diagnostics\n"
        )
        proc = subprocess.run(
            [bash, "-c", script], capture_output=True, text=True, timeout=60
        )
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode == 0, proc.stderr
    assert f"OPIP_WRITER_DIAG_CLASSIFICATION={expected}" in proc.stderr
    assert "OPIP_WRITER_DIAG_HEALTH=unhealthy" in proc.stderr
    assert "OPIP_WRITER_DIAG_RESTART_COUNT=7" in proc.stderr


# ---------------------------------------------------------------------------
# AC-015(e) No authority widening
# ---------------------------------------------------------------------------


def test_ac_015_no_authority_is_widened_by_this_increment():
    """ATDD-RELEASE-PIPELINE-v1/AC-015: Paper-v2 stays off, the Committee stays absent, funded/live authority stays absent, legacy remains the sole new-entry authority and TARGET_PAPER stays blocked."""
    from app.services.release_profiles import resolve_release_profile

    evidence_shadow = resolve_release_profile("EVIDENCE_SHADOW")["allowed_modes"]
    assert evidence_shadow["OPIP_PAPER_V2_MODE"] == "off"
    assert evidence_shadow["OPIP_COMMITTEE_MODE"] == "off"
    assert resolve_release_profile("TARGET_PAPER")["status"] != "ACTIVE"

    # The capture producer is still strictly dual-gated on Feature Bus AND writer
    # exactly `shadow`, and is still a non-authoritative consumer.
    assert producer.feasibility_capture_authorized(_settings()) is True
    assert (
        producer.feasibility_capture_authorized(
            _settings(opip_feature_bus_mode="active", opip_canonical_writer_mode="shadow")
        )
        is False
    )
    assert (
        producer.feasibility_capture_authorized(
            _settings(opip_feature_bus_mode="shadow", opip_canonical_writer_mode="active")
        )
        is False
    )

    source = PRODUCER_SOURCE.read_text(encoding="utf-8")
    for forbidden in (
        "place_order",
        "cancel_order",
        "paper_v2_protection_runtime",
        "telegram",
    ):
        assert forbidden not in source, forbidden

    deploy = _deploy()
    assert "OPIP_PAPER_V2_MODE=off" in deploy
