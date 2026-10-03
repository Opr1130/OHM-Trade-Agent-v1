"""R4-B2 shadow cadence bridge: acceptance guards (AC-020).

Proves the Feature Bus SHADOW capture runs on the F3 60-second evaluation grid so
consecutive passes commit FeatureSnapshots at consecutive cutoffs, that the real
frozen F3 IGNITION detector consumes that produced sequence at each snapshot's own
cutoff, that persistence advances only across a CONSECUTIVE qualifying pair, and
that a 15-minute gap, a replay, or a source delay fails closed. Also proves the
acquisition is bounded-concurrent and the pass respects its sub-60s deadline.

Every fixture is a deterministic literal. No network, wall clock, random value or
production data is read.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import app.jobs.capture_feature_bus_shadow as capture  # noqa: E402
from app.opip.contracts.enums import (  # noqa: E402
    CoverageState,
    Missingness,
    RestartState,
    TrendState,
)
from app.opip.contracts.features import FeatureSnapshot  # noqa: E402
from app.opip.contracts.identity import (  # noqa: E402
    ConsumedInputWatermark,
    InstrumentVersion,
)
from app.opip.contracts.detector import DetectorState  # noqa: E402
from app.opip.contracts.observation import SourceWatermark  # noqa: E402
from app.opip.detectors import ignition  # noqa: E402
from app.opip.features.publisher import FeatureBusPublisher  # noqa: E402
from app.opip.market.source import (  # noqa: E402
    PolledMinuteBarSource,
    SourceBatch,
    SourceMetrics,
)
from app.opip.ml.temporal import AvailabilityStamp  # noqa: E402

pytestmark = pytest.mark.acceptance

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
T60 = T0 + timedelta(seconds=60)
T120 = T0 + timedelta(seconds=120)
T15MIN = T0 + timedelta(seconds=900)

INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
VENUE_INSTRUMENT_ID = "SOLUSD"

#: Satisfies the directional core and all three entry confirmations.
QUALIFYING_VALUES: dict[str, Any] = {
    "trend_state": TrendState.UP.value,
    "return_5m": 0.4,
    "acceleration_5m_vs_15m": 0.05,
    "volume_expansion_5m_vs_20m": 1.30,
    "compression_release_score": 0.40,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(**overrides) -> SimpleNamespace:
    base = {
        "opip_feature_bus_mode": "shadow",
        "opip_canonical_writer_mode": "shadow",
        "opip_feature_bus_capture_limit": 8,
        "opip_feature_bus_capture_budget_seconds": 45,
        "opip_feature_bus_capture_concurrency": 4,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeAck:
    def __init__(self, status="OK", seq=1):
        self.status = status
        self.history_epoch = 0
        self.local_sequence = seq
        self.event_id = f"EV:{seq}"
        self.error_code = None


class _RecordingClient:
    def __init__(self):
        self.intents = []
        self._lock = threading.Lock()

    def submit(self, intent):
        with self._lock:
            self.intents.append(intent)
            return _FakeAck(seq=len(self.intents))

    def snapshot_payloads(self):
        return [
            intent.payload
            for intent in self.intents
            if intent.payload.get("record_type") == "FeatureSnapshot"
        ]


def _instrument(n: int = 1) -> InstrumentVersion:
    return InstrumentVersion(
        venue="synthetic",
        base_asset=f"SYN{n}",
        quote_currency="USD",
        venue_instrument_id=f"SYNTHETIC-SYN{n}USD",
        version=1,
        reference_data_version="opip-evidence-identity-v1",
        observed_at_utc=T0,
        price_decimals=2,
        tick_size=0.01,
        min_order_size=0.2,
    )


def _provider(versions):
    class _P:
        def refresh(self, *, observed_at_utc):
            return list(versions)

    return _P()


def _batch(version, observations, *, error=None) -> SourceBatch:
    return SourceBatch(
        instrument_version=version,
        observations=tuple(observations),
        watermark=SourceWatermark(
            instrument_version_id=version.instrument_version_id, through_utc=T0
        ),
        coverage=CoverageState.COMPLETE,
        metrics=SourceMetrics(observations=len(observations)),
        error=error,
    )


def _observations(version, cutoff: datetime = T0):
    from app.jobs.run_feature_bus_pilot import _synthetic_observations

    return _synthetic_observations(version, cutoff=cutoff, intervals=140, now=cutoff)


def _source(batches_by_id, *, raising_ids=()):
    class _S:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            vid = version.instrument_version_id
            if vid in raising_ids:
                raise RuntimeError("unexpected transport failure")
            return batches_by_id[vid]

    return _S()


def _build_qualifying_snapshot(*, cutoff: datetime, values=None) -> FeatureSnapshot:
    payload = dict(QUALIFYING_VALUES if values is None else values)
    stamps = {name: Missingness.PRESENT for name in payload}
    decision = cutoff + timedelta(seconds=2)
    availability = AvailabilityStamp(
        source_at_utc=cutoff - timedelta(seconds=60),
        ingested_at_utc=cutoff + timedelta(seconds=1),
        visible_at_utc=cutoff + timedelta(seconds=1),
        source_version="r4b2-cadence-fixture-v1",
    )
    return FeatureSnapshot(
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id=VENUE_INSTRUMENT_ID,
        feature_version="features-v1",
        evaluation_cutoff=cutoff,
        evaluated_at_utc=decision,
        consumed_input_watermark=ConsumedInputWatermark(history_epoch=1, local_sequence=7),
        values=payload,
        availability=availability,
        missingness=stamps,
        coverage=CoverageState.COMPLETE,
        restart_state=RestartState.WARM,
        evaluation_grid_seconds=60,
        feature_dag_hash="FDAG:r4b2-cadence-fixture",
    )


def _dormant_state() -> DetectorState:
    return DetectorState(
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id=VENUE_INSTRUMENT_ID,
    )


def _run_pass(*, now: datetime, versions, batches, budget=45, concurrency=4):
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(
            opip_feature_bus_capture_budget_seconds=budget,
            opip_feature_bus_capture_concurrency=concurrency,
        ),
        now=now,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    return summary, client


# ---------------------------------------------------------------------------
# AC-020 cadence
# ---------------------------------------------------------------------------


def test_ac_020_consecutive_passes_commit_snapshots_60s_apart():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: two consecutive minute passes commit one FeatureSnapshot per instrument at cutoffs exactly 60 seconds apart, each with COMPLETE coverage."""
    version = _instrument(1)
    batches_a = {
        version.instrument_version_id: _batch(version, _observations(version, T0))
    }
    batches_b = {
        version.instrument_version_id: _batch(version, _observations(version, T60))
    }

    summary_a, client_a = _run_pass(now=T0, versions=[version], batches=batches_a)
    summary_b, client_b = _run_pass(now=T60, versions=[version], batches=batches_b)

    assert summary_a.cycles == 1 and summary_b.cycles == 1
    payloads_a = client_a.snapshot_payloads()
    payloads_b = client_b.snapshot_payloads()
    assert len(payloads_a) == 1 and len(payloads_b) == 1

    def _cutoff(p):
        return datetime.fromisoformat(str(p["evaluation_cutoff"]).replace("Z", "+00:00"))

    cutoff_a = _cutoff(payloads_a[0])
    cutoff_b = _cutoff(payloads_b[0])
    assert cutoff_a == T0
    assert cutoff_b == T60
    assert (cutoff_b - cutoff_a) == timedelta(seconds=60)
    # Both passes materialize genuinely complete contiguous evidence.
    assert payloads_a[0]["coverage"] == CoverageState.COMPLETE.value
    assert payloads_b[0]["coverage"] == CoverageState.COMPLETE.value


def test_ac_020_f3_consumes_produced_snapshots_at_their_own_cutoff():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: the frozen F3 detector consumes each produced snapshot at its own exact cutoff (evaluation_time == snapshot.evaluation_cutoff) with no structural rejection."""
    version = _instrument(1)
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    _, client = _run_pass(now=T0, versions=[version], batches=batches)

    from app.opip.features.committed_snapshot_reader import (  # noqa: E402
        feature_snapshot_from_payload,
    )

    produced = feature_snapshot_from_payload(client.snapshot_payloads()[0])
    assert produced.evaluation_grid_seconds == 60
    assert produced.coverage is CoverageState.COMPLETE
    assert produced.restart_state is RestartState.WARM

    # Every REQUIRED F3 feature must be PRESENT and the snapshot must be
    # epistemically usable at its own cutoff; a pipeline regression dropping a
    # required feature would make _project non-usable (no raise) and must fail here.
    for name in ignition.REQUIRED_FEATURES:
        assert produced.values.get(name) is not None, name
        assert produced.missingness.get(name) is Missingness.PRESENT, name
    projection = ignition._project(produced, produced.evaluation_cutoff)
    assert projection.usable is True

    state = DetectorState(
        instrument_version_id=produced.instrument_version_id,
        venue_instrument_id=produced.venue_instrument_id,
    )
    claims, next_state = ignition.evaluate(produced, state, produced.evaluation_cutoff)
    # Structurally consumable at its own cutoff: a well-formed state comes back.
    assert next_state.last_evaluation_cutoff == produced.evaluation_cutoff
    assert next_state.last_snapshot_id == produced.snapshot_id
    assert isinstance(claims, list)


def test_ac_020_two_consecutive_qualifying_evaluations_produce_claim():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: persistence advances from the first qualifying 60-second evaluation to the second, and the second produces a genuine F3 IGNITION claim."""
    snap_t0 = _build_qualifying_snapshot(cutoff=T0)
    snap_t60 = _build_qualifying_snapshot(cutoff=T60)

    claims_1, state_1 = ignition.evaluate(snap_t0, _dormant_state(), T0)
    assert claims_1 == []
    assert state_1.persistence_seconds == 60
    assert state_1.phase.value == "DORMANT"

    claims_2, state_2 = ignition.evaluate(snap_t60, state_1, T60)
    assert len(claims_2) == 1
    assert claims_2[0].transition.value == "DORMANT_TO_IGNITION"
    assert claims_2[0].evaluation_cutoff == T60
    assert state_2.phase.value == "IGNITION"


def test_ac_020_fifteen_minute_gap_does_not_count_as_persistence():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: a 15-minute gap is not a consecutive 60-second step, so persistence resets and no claim is emitted."""
    snap_t0 = _build_qualifying_snapshot(cutoff=T0)
    snap_gap = _build_qualifying_snapshot(cutoff=T15MIN)

    _, state_1 = ignition.evaluate(snap_t0, _dormant_state(), T0)
    assert state_1.persistence_seconds == 60

    claims, state_2 = ignition.evaluate(snap_gap, state_1, T15MIN)
    assert claims == []
    # The non-adjacent evaluation broke the run: it starts a new one at 60s.
    assert state_2.persistence_seconds == 60
    assert state_2.phase.value == "DORMANT"


def test_ac_020_replayed_cutoff_does_not_advance_persistence():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: replaying the same cutoff (same sealed evaluation) does not advance persistence and cannot fabricate a claim."""
    snap_t0 = _build_qualifying_snapshot(cutoff=T0)

    _, state_1 = ignition.evaluate(snap_t0, _dormant_state(), T0)
    assert state_1.persistence_seconds == 60

    claims, state_2 = ignition.evaluate(snap_t0, state_1, T0)
    assert claims == []
    assert state_2.persistence_seconds == 60  # reset then re-accrued once, never 120


def test_ac_020_missing_minute_is_incomplete_coverage():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: a missing minute inside the fetched window is INCOMPLETE coverage, so a delayed/absent minute fails closed rather than fabricating contiguous evidence."""
    version = _instrument(1)

    class _Fetcher:
        def __init__(self, rows):
            self._rows = rows

        def __call__(self, venue_instrument_id, *, interval_minutes, since_epoch):
            return list(self._rows)

    from app.opip.market.observations import IntervalRow

    # Two rows whose starts are 120s apart: the middle 60s minute is absent.
    rows = [
        IntervalRow(
            interval_start_epoch=int((T0 - timedelta(minutes=2)).timestamp()),
            open=1.0, high=1.1, low=0.9, close=1.0, volume=10.0, vwap=1.0, trade_count=1,
        ),
        IntervalRow(
            interval_start_epoch=int(T0.timestamp()),
            open=1.0, high=1.1, low=0.9, close=1.0, volume=10.0, vwap=1.0, trade_count=1,
        ),
    ]
    source = PolledMinuteBarSource(
        _Fetcher(rows),
        venue="kraken",
        source_label="kraken_ohlc",
        sequence_prefix="kraken-ohlc",
        interval_seconds=60,
    )
    batch = source.fetch_through(version, watermark=None, now=T0)
    assert batch.coverage is CoverageState.INCOMPLETE_COVERAGE


def test_ac_020_no_historical_catch_up_is_materialized():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: one pass materializes exactly ONE snapshot per instrument at the current cutoff, never a catch-up series, even when the fetch returns many prior minutes."""
    version = _instrument(1)
    # A source that returns a full window of 140 prior minutes in one fetch.
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    summary, client = _run_pass(now=T0, versions=[version], batches=batches)
    assert summary.cycles == 1
    # Exactly one snapshot: no backdated catch-up series is fabricated.
    assert len(client.snapshot_payloads()) == 1
    cutoff = datetime.fromisoformat(
        str(client.snapshot_payloads()[0]["evaluation_cutoff"]).replace("Z", "+00:00")
    )
    assert cutoff == T0


def test_ac_020_acquisition_concurrency_is_bounded():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: acquisition uses a finite worker pool bounded by the configured concurrency, never unbounded."""
    versions = [_instrument(i) for i in range(6)]

    in_flight = {"current": 0, "max": 0}
    lock = threading.Lock()

    class _SlowSource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            with lock:
                in_flight["current"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["current"])
            time.sleep(0.02)
            with lock:
                in_flight["current"] -= 1
            return _batch(version, _observations(version))

    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_concurrency=2),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_SlowSource(),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    # The pool is bounded by the configured concurrency, never unbounded.
    assert in_flight["max"] <= 2
    assert summary.cycles == 6


def test_ac_020_deadline_stops_further_waves():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: the internal budget is the graceful stop. Once the remaining budget cannot fit one more bounded wave, no further instruments are fetched and the pass records explicit budget-exhausted evidence."""
    versions = [_instrument(i) for i in range(6)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    # start=0; the first wave gate sees 0 (budget intact); the second wave gate
    # jumps past the deadline, so only the first wave (2 instruments) is fetched.
    ticks = iter([0.0, 0.0, 1000.0])

    def _clock():
        try:
            return next(ticks)
        except StopIteration:
            return 1000.0

    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(
            opip_feature_bus_capture_budget_seconds=45,
            opip_feature_bus_capture_concurrency=2,
        ),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )
    assert summary.budget_exhausted is True
    assert summary.fetched == 2
    assert len(client.snapshot_payloads()) == 2
    assert any("budget exhausted" in err for err in summary.errors)


def test_ac_020_materialization_is_sequential(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020: materialization runs strictly sequentially on the single canonical writer connection, even though acquisition is concurrent."""
    versions = [_instrument(i) for i in range(4)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    active = {"current": 0, "max": 0}
    lock = threading.Lock()
    real_run_cycle = capture.run_cycle

    def _tracked_run_cycle(*args, **kwargs):
        with lock:
            active["current"] += 1
            active["max"] = max(active["max"], active["current"])
        try:
            time.sleep(0.02)
            return real_run_cycle(*args, **kwargs)
        finally:
            with lock:
                active["current"] -= 1

    monkeypatch.setattr(capture, "run_cycle", _tracked_run_cycle)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_concurrency=4),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert active["max"] == 1
    assert summary.cycles == 4


def test_ac_020_config_bounds_are_within_the_minute_slot():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: the configured budget cap and the cron containment timeout both sit strictly below the 60-second cadence slot."""
    from app.core.config import Settings

    fields = Settings.model_fields
    budget = fields["opip_feature_bus_capture_budget_seconds"]
    concurrency = fields["opip_feature_bus_capture_concurrency"]
    assert budget.default <= 50 < 60
    assert concurrency.default <= 8

    entry = (APP_ROOT / "deploy" / "cron.d" / "opip-feature-bus-capture").read_text(
        encoding="utf-8"
    )
    assert "* * * * *" in entry
    assert "timeout --signal=TERM --kill-after=5s 50" in entry


def test_ac_020_unified_cycle_runs_none_of_the_capture_path():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: the protected unified cycle neither imports nor invokes the capture path."""
    cycle = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "capture_feature_bus_shadow" not in cycle
    assert "feature_bus" not in cycle.lower()


# ---------------------------------------------------------------------------
# AC-020 in-container non-overlap containment
# ---------------------------------------------------------------------------


_HOLDER_SCRIPT = r"""
import sys, time
from app.jobs import capture_feature_bus_shadow as cap

lock_path, log_path, tag, sleep_s = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])


def body():
    with open(log_path, "a") as handle:
        handle.write(tag + "-IN\n")
        handle.flush()
    time.sleep(sleep_s)
    with open(log_path, "a") as handle:
        handle.write(tag + "-OUT\n")
        handle.flush()

    class _Summary:
        def to_dict(self):
            return {"tag": tag}

    return _Summary()


result = cap.run_capture_locked(lock_path=lock_path, capture_fn=body)
if result.get("status") != "RAN":
    with open(log_path, "a") as handle:
        handle.write(tag + "-SKIP\n")
"""


def _spawn_holder(lock_path: Path, log_path: Path, tag: str, sleep_s: float):
    import subprocess

    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = str(APP_ROOT)
    return subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(lock_path), str(log_path), tag, str(sleep_s)],
        cwd=str(APP_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_marker(log_path: Path, marker: str, *, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists() and marker in log_path.read_text():
            return True
        time.sleep(0.05)
    return False


def _wait_for_lock_release(lock_path: Path, *, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        probe = capture.CaptureProcessLock(str(lock_path))
        if probe.acquire():
            probe.release()
            return True
        time.sleep(0.05)
    return False


def test_ac_020_run_capture_locked_skips_when_lock_held(monkeypatch, tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-020: while the process lock is held a second invocation does NOT run capture; it records an explicit SKIPPED_LOCK_HELD disposition and never executes a second capture body."""
    lock_path = str(tmp_path / "cap.lock")
    monkeypatch.setenv("OPIP_FEATURE_BUS_CAPTURE_LOCK", lock_path)
    holder = capture.CaptureProcessLock(lock_path)
    assert holder.acquire() is True
    try:
        ran = {"count": 0}

        def _must_not_run():
            ran["count"] += 1
            raise AssertionError("capture body must not run while the lock is held")

        result = capture.run_capture_locked(capture_fn=_must_not_run)
        assert result["status"] == "SKIPPED_LOCK_HELD"
        assert ran["count"] == 0
    finally:
        holder.release()

    # Once released, a later invocation proceeds.
    def _ok():
        class _S:
            def to_dict(self):
                return {"ok": True}

        return _S()

    assert capture.run_capture_locked(capture_fn=_ok)["status"] == "RAN"


def test_ac_020_process_lock_serializes_capture_bodies(tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-020: a second invocation cannot enter capture while the first holds the lock; the first is terminated; the lock releases; a later invocation proceeds; no two bodies execute concurrently."""
    lock_path = tmp_path / "cap.lock"
    log_path = tmp_path / "log.txt"

    first = _spawn_holder(lock_path, log_path, "A", 2.0)
    try:
        assert _wait_for_marker(log_path, "A-IN"), "first capture body never entered"

        # A second attempted invocation, while the first is mid-body, is refused.
        second = _spawn_holder(lock_path, log_path, "B", 0.0)
        second.wait(timeout=15)

        # Deliberately terminate the first in-container workload (as the in-container
        # timeout would), then confirm the lock is released and a later run proceeds.
        first.terminate()
        first.wait(timeout=15)
        assert _wait_for_lock_release(lock_path), "process lock was not released on termination"

        text = log_path.read_text()
        assert "A-IN" in text
        assert "B-SKIP" in text, text
        assert "B-IN" not in text, "a second capture body entered concurrently"

        # Lock released after termination: a fresh invocation can enter.
        third = _spawn_holder(lock_path, log_path, "C", 0.0)
        third.wait(timeout=15)
        text = log_path.read_text()
        assert "C-IN" in text
    finally:
        if first.poll() is None:
            first.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="coreutils 'timeout' is POSIX; the in-container timeout is asserted statically and via the process lock on Windows")
def test_ac_020_inner_timeout_terminates_workload_and_releases_lock(tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-020: the in-container `timeout` genuinely terminates the capture workload, and the process lock is released on termination so the next invocation can proceed."""
    import subprocess

    lock_path = tmp_path / "cap.lock"
    log_path = tmp_path / "log.txt"
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = str(APP_ROOT)
    # A holder that would sleep far past the enforceable bound.
    completed = subprocess.run(
        [
            "timeout", "--signal=TERM", "--kill-after=5s", "1",
            sys.executable, "-c", _HOLDER_SCRIPT, str(lock_path), str(log_path), "T", "30",
        ],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    # timeout terminated it (124) rather than letting it run its 30s body.
    assert completed.returncode == 124, completed.returncode
    assert _wait_for_marker(log_path, "T-IN")
    assert not _wait_for_marker(log_path, "T-OUT", timeout=1.0)
    # The OS released the dead process's lock.
    probe = capture.CaptureProcessLock(str(lock_path))
    assert probe.acquire() is True
    probe.release()


def test_ac_020_inner_timeout_is_container_side_and_no_outer_lock_release():
    """ATDD-R4-B2-controlled-paper-activation/AC-020: the enforceable timeout runs INSIDE the container (after `docker compose exec`), and no host-side timeout wraps the call so the only lock cannot be released while the in-container process may continue."""
    entry = (APP_ROOT / "deploy" / "cron.d" / "opip-feature-bus-capture").read_text(
        encoding="utf-8"
    )
    import re as _re

    env_assignment = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    command = [
        line.strip()
        for line in entry.splitlines()
        if line.strip() and not line.strip().startswith("#") and not env_assignment.match(line.strip())
    ]
    assert len(command) == 1, command
    line = command[0]
    # The timeout is container-side, immediately before the Python module.
    assert (
        "docker compose exec -T ohm-trade-agent "
        "timeout --signal=TERM --kill-after=5s 50 "
        "python -m app.jobs.capture_feature_bus_shadow"
    ) in line
    # No host-side timeout wraps `docker compose exec` (it would release the only
    # host lock while the in-container process could continue).
    assert "timeout " not in line.split("docker compose exec")[0]
    assert line.startswith("* * * * *")
    assert "flock -n /var/run/opip-feature-bus-capture.lock" in line
