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

import httpx
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
from app.opip.features.pipeline import declared_cycle_submit_bound  # noqa: E402
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


def test_ac_020_deadline_stops_further_waves(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020: the internal budget is the graceful stop. Once the remaining budget cannot fit one more bounded wave, no further instruments are fetched and the pass records explicit budget-exhausted evidence. A source that blows the WHOLE pass budget (past Phase B's own deadline) cannot then commit acquired evidence with an unbounded write: every dependent write needs remaining materialization budget, so the pass emits an explicit materialize_incomplete disposition instead of claiming OK."""
    versions = [_instrument(i) for i in range(6)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    # The SOURCE advances the pass clock: the first wave (2 instruments) completes,
    # then the clock is past the acquisition deadline, so the next wave gate cannot
    # fit one more bounded request and no further instrument is fetched.
    clock = {"value": 0.0}

    def _clock():
        return clock["value"]

    class _AdvancingSource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def __init__(self) -> None:
            self.calls = 0

        def fetch_through(self, version, *, watermark, now):
            self.calls += 1
            if self.calls >= 2:
                clock["value"] = 1000.0
            return batches[version.instrument_version_id]

    lines = _record_markers(monkeypatch)
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
        source=_AdvancingSource(),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )
    assert summary.budget_exhausted is True
    assert any("budget exhausted" in err for err in summary.errors)
    # Phase B's deadline is already blown: no write may be STARTED, so nothing is
    # fabricated and the uncommitted evidence is an explicit durable disposition.
    assert summary.materialize_incomplete is True
    assert summary.fetched == 0
    assert client.snapshot_payloads() == []
    incomplete = [line for line in lines if "PHASE=materialize_incomplete" in line]
    assert len(incomplete) == 1
    assert "reason=INSUFFICIENT_MATERIALIZE_BUDGET" in incomplete[0]
    assert lines[-1].startswith("OPIP_FEATURE_BUS_CAPTURE_PHASE=done")
    assert "status=MATERIALIZE_INCOMPLETE" in lines[-1]


# ---------------------------------------------------------------------------
# AC-020 bounded request/retry sequence, pass deadline and durable dispositions
# ---------------------------------------------------------------------------

#: The declared worst-case wall clock of ONE acquisition wave, as the pass-scoped
#: client and the wave gate both derive it (see capture_request_timeout_seconds).
WAVE_BUDGET_SECONDS = 15.0


def _record_markers(monkeypatch):
    """Capture every producer disposition marker and assert each one is FLUSHED."""
    lines: list[str] = []

    def _fake_print(*args, **kwargs):
        assert kwargs.get("flush") is True, (
            "durable capture markers must be flushed; a buffered line is lost when "
            "the outer containment kills the producer"
        )
        lines.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr(capture, "print", _fake_print, raising=False)
    return lines


def _marker_field(line: str, key: str) -> float | None:
    for field in line.split(" "):
        if field.startswith(f"{key}="):
            try:
                return float(field.split("=", 1)[1])
            except ValueError:
                return None
    return None


class _VirtualKrakenClock:
    """A virtual monotonic clock: the ONLY way to prove a bounded retry sequence.

    Wall-clock sleeps would make the test as slow as the failure it proves, so the
    transport's ``time`` module reference is replaced and every sleep advances the
    virtual clock by exactly the amount the transport asked for. A stalled attempt
    consumes its whole httpx timeout, which is what a real connect+read stall does
    (httpx applies the float to connect and read SEPARATELY).
    """

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.attempts: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


def _stalling_transport(monkeypatch, clock, *, max_retries: int = 2, error=None):
    from app.services import kraken_transport as transport_module

    transport = transport_module.KrakenPublicTransport(
        requests_per_second=1000.0, burst=10, max_retries=max_retries
    )
    monkeypatch.setattr(
        transport_module,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep),
    )
    failure = error if error is not None else httpx.ReadTimeout("stalled public read")

    def _stalled_get(url, params=None, timeout=None):
        clock.attempts.append(float(timeout))
        clock.now += float(timeout)  # a stalled connect+read consumes its full timeout
        raise failure

    monkeypatch.setattr(transport._client, "get", _stalled_get)
    return transport, transport_module


def test_ac_020_stalled_request_cannot_consume_the_complete_pass_budget(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: a fully stalled request (every attempt timing out) still finishes inside ONE wave budget -- retries, backoff, jitter and rate-limit waiting included."""
    from app.services import opip_feature_bus_market_source as market_source
    from app.services.kraken_transport import KrakenTransportError

    clock = _VirtualKrakenClock()
    transport, _ = _stalling_transport(monkeypatch, clock, max_retries=2)
    timeout_seconds = market_source.capture_request_timeout_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS, max_retries=2
    )
    started = clock.now
    deadline = started + WAVE_BUDGET_SECONDS
    with pytest.raises(KrakenTransportError):
        transport.request(
            "OHLC",
            {"pair": "SOLUSD", "interval": 1},
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline,
        )
    # EVERY attempt (not just the derived timeout) is bounded by the wave budget.
    assert len(clock.attempts) == 3
    assert clock.now - started <= WAVE_BUDGET_SECONDS
    assert sum(clock.sleeps) + sum(clock.attempts) <= WAVE_BUDGET_SECONDS
    # The naive ``15s / 3 attempts = 5s`` shortcut is NOT what bounds this: the
    # derived per-attempt timeout first subtracts backoff + rate-wait overhead.
    assert timeout_seconds < WAVE_BUDGET_SECONDS / 3
    assert market_source.capture_worst_case_request_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS, max_retries=2
    ) <= WAVE_BUDGET_SECONDS


def test_ac_020_retry_and_backoff_never_start_without_remaining_budget(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: a retry (and its backoff sleep) never begins when it cannot fit the remaining deadline, and an exhausted budget starts no upstream work at all."""
    from app.services import opip_feature_bus_market_source as market_source
    from app.services.kraken_transport import KrakenTransportDeadlineExceeded

    # One attempt can fit but a FULL retry sequence cannot: the transport shrinks
    # the attempt timeout to the remaining budget and then refuses the retry that
    # cannot fit -- it never sleeps past the deadline.
    clock = _VirtualKrakenClock()
    transport, _ = _stalling_transport(monkeypatch, clock, max_retries=2)
    started = clock.now
    deadline = started + 1.9
    with pytest.raises(KrakenTransportDeadlineExceeded):
        transport.request(
            "OHLC", {"pair": "SOLUSD"}, timeout_seconds=1.0, deadline_monotonic=deadline
        )
    assert len(clock.attempts) <= 3
    assert all(attempt <= 1.0 for attempt in clock.attempts)
    assert clock.attempts[-1] < clock.attempts[0]  # derived from the REMAINING budget
    assert clock.now <= deadline
    assert clock.now - started <= 1.9
    assert started + sum(clock.sleeps) + sum(clock.attempts) <= deadline

    # A single attempt that cannot fit the remaining budget starts NOTHING.
    clock = _VirtualKrakenClock()
    transport, _ = _stalling_transport(monkeypatch, clock, max_retries=2)
    started = clock.now
    with pytest.raises(KrakenTransportDeadlineExceeded):
        transport.request(
            "OHLC",
            {"pair": "SOLUSD"},
            timeout_seconds=1.0,
            deadline_monotonic=started + 0.3,
        )
    assert clock.attempts == []
    assert clock.sleeps == []

    # An already-exhausted budget starts NOTHING (no attempt, no sleep).
    clock = _VirtualKrakenClock()
    transport, _ = _stalling_transport(monkeypatch, clock, max_retries=2)
    started = clock.now
    with pytest.raises(KrakenTransportDeadlineExceeded):
        transport.request(
            "OHLC",
            {"pair": "SOLUSD"},
            timeout_seconds=1.0,
            deadline_monotonic=started + 0.05,
        )
    assert clock.attempts == []
    assert clock.sleeps == []
    assert clock.now - started <= 0.05
    assert (
        market_source.classify_capture_error(
            KrakenTransportDeadlineExceeded("deadline exhausted")
        )
        == "DEADLINE_EXHAUSTED"
    )
    # A rate-limiter wait that cannot fit the deadline also starts nothing.
    clock = _VirtualKrakenClock()
    from app.services.kraken_transport import KrakenPublicTransport

    transport = KrakenPublicTransport(requests_per_second=0.25, burst=1, max_retries=2)
    transport._tokens = 0.0
    transport._last_refill = clock.now
    monkeypatch.setattr(
        sys.modules["app.services.kraken_transport"],
        "time",
        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep),
    )
    with pytest.raises(KrakenTransportDeadlineExceeded):
        transport.request(
            "OHLC",
            {"pair": "SOLUSD"},
            timeout_seconds=1.0,
            deadline_monotonic=clock.now + 0.5,
        )
    assert clock.attempts == []
    assert clock.sleeps == []


def test_ac_020_first_attempt_rate_limit_wait_is_inside_the_declared_wave_bound(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: the declared worst case of one request owns a rate-limiter wait for EVERY transport attempt -- the first one included, because the transport takes a rate-budget token before attempt #1 -- so a bucket that is already DEPLETED when the request starts cannot overrun the wave."""
    from app.services import opip_feature_bus_market_source as market_source
    from app.services.kraken_transport import (
        KRAKEN_ATTEMPT_PHASE_BOUND,
        KrakenTransportError,
        retry_backoff_worst_case_seconds,
    )

    retries = 2
    attempts = market_source.capture_transport_attempt_count(retries)
    allowance = market_source.capture_rate_limit_wait_allowance_seconds(retries)
    # attempts = retries + 1: attempt #1 takes a token too, so it owns a wait.
    assert attempts == retries + 1 == 3
    assert allowance == attempts * market_source.CAPTURE_RATE_LIMIT_WAIT_ALLOWANCE_SECONDS
    # The naive ``retries * allowance`` accounting is NOT what is declared: it
    # omits the first attempt's wait entirely.
    assert allowance > retries * market_source.CAPTURE_RATE_LIMIT_WAIT_ALLOWANCE_SECONDS

    # The declared bound is the transport's REAL schedule, decomposed exactly.
    per_attempt = market_source.capture_request_timeout_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS, max_retries=retries
    )
    assert market_source.capture_worst_case_request_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS, max_retries=retries
    ) == pytest.approx(
        attempts * per_attempt * KRAKEN_ATTEMPT_PHASE_BOUND
        + allowance
        + retry_backoff_worst_case_seconds(retries)
    )
    assert market_source.capture_worst_case_request_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS, max_retries=retries
    ) <= WAVE_BUDGET_SECONDS + 1e-9

    # ADVERSARIAL: the shared token bucket is DEPLETED before attempt #1, so the
    # very first attempt must WAIT for a token instead of being admitted for free.
    clock = _VirtualKrakenClock()
    transport, _ = _stalling_transport(monkeypatch, clock, max_retries=retries)
    transport.requests_per_second = 1.0
    transport._tokens = 0.0
    transport._last_refill = clock.now
    started = clock.now
    with pytest.raises(KrakenTransportError):
        transport.request(
            "OHLC",
            {"pair": "SOLUSD"},
            timeout_seconds=per_attempt,
            deadline_monotonic=started + WAVE_BUDGET_SECONDS,
        )
    # The limiter wait happened BEFORE attempt #1 (nothing was attempted yet)...
    assert clock.sleeps[0] == pytest.approx(1.0)
    assert transport._metrics["rate_wait_seconds"] >= 1.0
    assert clock.attempts  # attempt #1 was made AFTER the depleted-bucket wait
    # ...and the declared allowance already covers the observed first-attempt wait.
    assert allowance >= transport._metrics["rate_wait_seconds"]
    assert clock.now - started <= WAVE_BUDGET_SECONDS + 1e-9
    assert sum(clock.sleeps) + sum(clock.attempts) <= WAVE_BUDGET_SECONDS + 1e-9

    # ADVERSARIAL: a bucket so depleted that a single acquire needs several capped
    # rounds still cannot overrun: the transport refuses to WAIT past the deadline.
    clock = _VirtualKrakenClock()
    transport, _ = _stalling_transport(monkeypatch, clock, max_retries=retries)
    transport.requests_per_second = 0.25
    transport.burst = 1
    transport._tokens = 0.0
    transport._last_refill = clock.now
    started = clock.now
    with pytest.raises(KrakenTransportError):
        transport.request(
            "OHLC",
            {"pair": "SOLUSD"},
            timeout_seconds=per_attempt,
            deadline_monotonic=started + WAVE_BUDGET_SECONDS,
        )
    assert any(sleep >= 1.0 for sleep in clock.sleeps)
    assert clock.now - started <= WAVE_BUDGET_SECONDS + 1e-9


def test_ac_020_capture_client_declares_a_bounded_public_only_request_budget(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: the pass-scoped capture client declares a derived per-attempt timeout AND the pass deadline, exposes only public read endpoints, and its worst case fits the declared wave for every retry policy."""
    from app.services import opip_feature_bus_market_source as market_source

    # The SHIPPED retry policy fits ONE wave exactly; a policy that cannot fit is
    # reported honestly (never understated), so the wave gate fail-closes instead
    # of starting work that would overrun the pass.
    assert market_source.capture_worst_case_request_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS
    ) <= WAVE_BUDGET_SECONDS + 1e-9
    assert market_source.capture_worst_case_request_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS, max_retries=5
    ) > WAVE_BUDGET_SECONDS

    deadline = 1234.5
    client = market_source.capture_kraken_client(
        wave_budget_seconds=WAVE_BUDGET_SECONDS, deadline_monotonic=deadline
    )
    assert client.timeout_seconds == market_source.capture_request_timeout_seconds(
        wave_budget_seconds=WAVE_BUDGET_SECONDS
    )
    assert client.deadline_monotonic == deadline
    # Public reads only: the capture client has no order authority of any kind.
    for forbidden in (
        "place_order",
        "add_order",
        "create_order",
        "cancel_order",
        "amend_order",
        "withdraw",
    ):
        assert not hasattr(client, forbidden), forbidden


def test_ac_020_materialization_reserve_is_retained_for_phase_b(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: Phase A can never consume the time reserved for Phase B, so every acquired snapshot is still committed (with a durable marker) after a deadline-exhausted acquisition."""
    versions = [_instrument(i) for i in range(6)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    tick = {"value": 0.0}

    def _clock():
        return tick["value"]

    class _SlowSource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            tick["value"] += 14.0  # one bounded request consumes its whole wave
            return batches[version.instrument_version_id]

    lines = _record_markers(monkeypatch)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    budget = 45.0
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(
            opip_feature_bus_capture_budget_seconds=budget,
            opip_feature_bus_capture_concurrency=2,
        ),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_SlowSource(),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )

    reserve = min(
        capture.CAPTURE_MATERIALIZE_RESERVE_SECONDS,
        max(0.0, budget - capture.PER_REQUEST_BUDGET_SECONDS),
    )
    assert reserve == capture.CAPTURE_MATERIALIZE_RESERVE_SECONDS == 10.0
    assert capture.CAPTURE_MATERIALIZE_RESERVE_SECONDS < capture.MAX_BUDGET_SECONDS

    # Acquisition stopped at its own (earlier) deadline...
    assert summary.budget_exhausted is True
    assert summary.fetched == 2
    acquire_complete = [
        line for line in lines if "PHASE=acquire_complete" in line
    ]
    assert len(acquire_complete) == 1
    acquired_elapsed = _marker_field(acquire_complete[0], "elapsed_seconds")
    assert acquired_elapsed == 28.0
    assert acquired_elapsed <= budget - reserve
    # The REFUSED wave would have been admitted without the reserve (45 - 28 = 17
    # >= 15) and would then have run the pass to 56s -- past both the pass budget
    # and the 50-second containment. The reserve is what refuses it at 7s.
    assert budget - reserve - acquired_elapsed < WAVE_BUDGET_SECONDS
    assert budget - acquired_elapsed >= WAVE_BUDGET_SECONDS
    assert acquired_elapsed + 2 * 14.0 > budget
    # ...and Phase B still committed EVERY acquired snapshot (nothing dropped).
    assert any("PHASE=materialize" in line for line in lines)
    assert any("PHASE=deadline_exhausted" in line for line in lines)
    assert summary.cycles == 2
    assert len(client.snapshot_payloads()) == 2
    assert lines[-1].startswith("OPIP_FEATURE_BUS_CAPTURE_PHASE=done")
    assert _marker_field(lines[-1], "elapsed_seconds") <= budget


class _SlowWriterClient(_RecordingClient):
    """A PERSISTENT canonical writer whose OWN submissions consume the pass budget.

    Proves a whole cycle of canonical submits is bounded by the materialization
    deadline rather than by the outer cron containment: the client records the
    timeout in force at every PHASE-B submission and advances the pass clock by its
    own (slow) write cost. Recording every phase-B submit -- not just the snapshot --
    is what makes the per-cycle bound visible: one cycle performs many dependent
    submits. Instrument-version publishes happen before Phase B and are not part of
    the materialization bound, so they are excluded.
    """

    #: Pre-Phase-B institutional submit: published before any materialization work.
    _NON_MATERIALIZE_EVENT = "market.instrument_version.recorded"

    def __init__(self, *, write_seconds: float, timeout: float = 30.0):
        super().__init__()
        self.write_seconds = write_seconds
        self.timeout = timeout
        self.write_timeouts: list[float] = []
        self.on_submit = None

    def submit(self, intent):
        if str(getattr(intent, "event_type", "")) != self._NON_MATERIALIZE_EVENT:
            self.write_timeouts.append(self.timeout)
            if self.on_submit is not None:
                self.on_submit()
        return super().submit(intent)


def test_ac_020_production_publisher_resolves_one_client_and_keeps_the_timeout_clamp(
    monkeypatch,
):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: a PRODUCTION-style publisher (no injected client) constructs the canonical writer ONCE and reuses that SAME object for every submit, so the per-operation timeout a producer narrows is the one the writes that actually run are bounded by."""
    import app.opip.features.publisher as publisher_module

    constructed: list[Any] = []

    class _CountingClient:
        def __init__(self):
            self.timeout = 5.0
            self.submit_timeouts: list[float] = []
            self.intents: list[Any] = []
            constructed.append(self)

        def submit(self, intent):
            self.submit_timeouts.append(self.timeout)
            self.intents.append(intent)
            return _FakeAck(seq=len(self.intents))

    monkeypatch.setattr(publisher_module, "CanonicalWriterClient", _CountingClient)
    version = _instrument(1)
    observation = _observations(version)[-1]

    publisher = FeatureBusPublisher(settings=_settings())
    assert publisher.enabled is True

    resolved = publisher.resolved_writer_client()
    assert len(constructed) == 1
    assert constructed[0] is resolved
    # Every later resolution -- and every produce path -- reuses the SAME object.
    assert publisher.resolved_writer_client() is resolved
    assert publisher._resolve_client() is resolved

    # The producer narrows the resolved client's per-operation timeout for Phase B;
    # the narrowing must PERSIST into subsequent publish() calls instead of being
    # spent on a throwaway client that the next publish() replaces.
    assert capture._bound_writer_operation_timeout(publisher, 0.25) == pytest.approx(0.25)
    assert resolved.timeout == pytest.approx(0.25)

    publisher.publish_observations([observation])
    publisher.publish_observations([observation])

    assert len(constructed) == 1
    assert resolved.submit_timeouts == [pytest.approx(0.25)] * 2
    # Never widened back to the client's declared default.
    assert resolved.timeout == pytest.approx(0.25)


def test_ac_020_materialization_admission_bounds_every_submit_of_a_cycle(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: Phase B admits a cycle against the pipeline's declared submit bound for the ACTUAL batch -- observations, coverage gaps, snapshot, restart and checkpoint -- so the enforced per-submit timeout is the remaining materialization budget shared across ALL of them, never a single 5-second write."""
    version = _instrument(1)
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    tick = {"value": 0.0}

    client = _SlowWriterClient(write_seconds=10_000.0)
    client.on_submit = None
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    budget = 45.0
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(
            opip_feature_bus_capture_budget_seconds=budget,
            opip_feature_bus_capture_concurrency=1,
        ),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider([version]),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=lambda: tick["value"],
    )
    assert summary.cycles == 1

    submit_bound = declared_cycle_submit_bound(len(_observations(version)))
    # The bound covers EVERY dependent submit of one cycle, so it is strictly more
    # than the snapshot + checkpoint a single-write assumption would count.
    assert submit_bound > 2
    # One cycle really does perform MANY submits.
    assert len(client.write_timeouts) > 1
    assert len(client.write_timeouts) <= submit_bound

    # Admission split the whole Phase-B budget across those submits, so the timeout
    # in force is the time-share -- far below both the client default and the
    # single-write bound the pass used to admit against.
    time_share = budget / submit_bound
    assert min(client.write_timeouts) == pytest.approx(time_share)
    assert max(client.write_timeouts) == pytest.approx(time_share)
    assert client.write_timeouts[0] < capture.CAPTURE_MATERIALIZE_WRITE_BOUND_SECONDS


def test_ac_020_cycle_whose_declared_maximum_cannot_fit_is_not_started(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: a cycle whose declared per-cycle submit cost cannot fit the remaining materialization budget is NOT STARTED -- no canonical submit runs, an explicit durable materialize_incomplete disposition is emitted, and done=OK is never claimed."""
    version = _instrument(1)
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    tick = {"value": 0.0}

    def _clock():
        return tick["value"]

    class _Source:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            tick["value"] += 14.0  # acquisition eats the pass budget down to 6s
            return batches[version.instrument_version_id]

    lines = _record_markers(monkeypatch)
    budget = 20.0
    client = _SlowWriterClient(write_seconds=10_000.0)
    original_timeout = client.timeout
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(
            opip_feature_bus_capture_budget_seconds=budget,
            opip_feature_bus_capture_concurrency=1,
        ),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider([version]),
        source=_Source(),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )

    submit_bound = declared_cycle_submit_bound(len(_observations(version)))
    remaining = budget - 14.0
    # 6s left cannot give each of the cycle's possible submits a meaningful bound.
    assert remaining / submit_bound < capture.CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS

    # NOTHING was started: no submit, no fabricated snapshot, no narrowed timeout.
    assert client.write_timeouts == []
    assert client.snapshot_payloads() == []
    assert client.timeout == original_timeout
    assert summary.fetched == 0
    assert summary.cycles == 0

    assert summary.materialize_incomplete is True
    incomplete = [line for line in lines if "PHASE=materialize_incomplete" in line]
    assert len(incomplete) == 1
    assert "reason=INSUFFICIENT_MATERIALIZE_BUDGET" in incomplete[0]
    assert _marker_field(incomplete[0], "submit_bound") == float(submit_bound)
    assert _marker_field(incomplete[0], "remaining_seconds") == pytest.approx(remaining)
    assert _marker_field(incomplete[0], "required_seconds") == pytest.approx(
        submit_bound * capture.CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS
    )

    assert lines[-1].startswith("OPIP_FEATURE_BUS_CAPTURE_PHASE=done")
    assert "status=MATERIALIZE_INCOMPLETE" in lines[-1]
    assert "materialize_incomplete=True" in lines[-1]
    assert "status=OK" not in lines[-1]


def test_ac_020_slow_persistent_writer_cannot_exceed_the_materialize_deadline(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: Phase B enforces its OWN absolute deadline -- a persistent writer that consumes its whole declared per-submit timeout on EVERY submit of a cycle cannot cross the deadline into cron containment; a cycle that no longer fits is never started, an explicit durable disposition is emitted, and done=OK is never claimed."""
    versions = [_instrument(i) for i in range(3)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    tick = {"value": 0.0}

    def _clock():
        return tick["value"]

    class _Source:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            return batches[version.instrument_version_id]

    lines = _record_markers(monkeypatch)
    budget = 45.0
    # The raw submission would take 10000s, but the producer clamps the writer's OWN
    # per-operation timeout, so the client returns (here: advances the pass clock)
    # inside the bound it was admitted under. Every submit pays it.
    client = _SlowWriterClient(write_seconds=10_000.0)
    original_timeout = client.timeout
    client.on_submit = lambda: tick.__setitem__(
        "value", tick["value"] + min(client.write_seconds, client.timeout)
    )
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(
            opip_feature_bus_capture_budget_seconds=budget,
            opip_feature_bus_capture_concurrency=1,
        ),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_Source(),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )

    submit_bound = declared_cycle_submit_bound(len(_observations(versions[0])))
    # Acquisitions are instant here, so Phase B starts with the whole pass budget.
    materialize = [line for line in lines if "PHASE=materialize " in line]
    assert len(materialize) == 1
    assert _marker_field(materialize[0], "count") == 3.0
    assert _marker_field(materialize[0], "deadline_remaining") == pytest.approx(budget)
    assert summary.budget_exhausted is False

    # The FIRST cycle was admitted against the time-share: the remaining budget
    # divided across every submit that cycle may make, and the SAME persistent
    # client carried that narrowed timeout into every submit it performed.
    assert client.write_timeouts
    assert client.write_timeouts[0] == pytest.approx(budget / submit_bound)
    assert client.write_timeouts[0] < original_timeout
    assert client.timeout <= client.write_timeouts[0]

    # The canonical submits of a cycle can NEVER consume more than the declared
    # Phase-B budget, and the pass stops ITSELF inside it: cron containment is final
    # containment only, never the mechanism that bounds Phase B.
    assert sum(client.write_timeouts) <= budget + 1e-9
    assert summary.elapsed_seconds <= budget + 1e-9
    assert _marker_field(lines[-1], "elapsed_seconds") <= budget + 1e-9

    # At least one acquired instrument could no longer fit a bounded cycle, so it was
    # NEVER STARTED: no fabricated commit, an explicit durable disposition instead.
    assert summary.fetched < len(versions)
    assert len(client.snapshot_payloads()) == summary.fetched
    assert summary.cycles == summary.fetched
    assert summary.materialize_incomplete is True
    incomplete = [line for line in lines if "PHASE=materialize_incomplete" in line]
    assert len(incomplete) == 1
    assert "reason=INSUFFICIENT_MATERIALIZE_BUDGET" in incomplete[0]
    assert _marker_field(incomplete[0], "submit_bound") == float(submit_bound)

    # done=OK is NEVER claimed when acquired evidence could not be committed.
    assert lines[-1].startswith("OPIP_FEATURE_BUS_CAPTURE_PHASE=done")
    assert "status=MATERIALIZE_INCOMPLETE" in lines[-1]
    assert "materialize_incomplete=True" in lines[-1]


def test_ac_020_failed_acquisition_emits_a_durable_disposition_marker(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: a timed-out instrument emits a FLUSHED failure disposition naming the reason, while the remaining instruments still commit and nothing is fabricated."""
    versions = [_instrument(1), _instrument(2)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    failing = versions[1].instrument_version_id

    class _StallingSource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            if version.instrument_version_id == failing:
                raise httpx.ReadTimeout("stalled public read")
            return batches[version.instrument_version_id]

    lines = _record_markers(monkeypatch)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_StallingSource(),
        restore_continuity=lambda versions: ({}, {}, {}),
    )

    failures = [line for line in lines if "PHASE=acquire_failure" in line]
    assert len(failures) == 1
    assert f"instrument={failing}" in failures[0]
    assert "reason=REQUEST_TIMEOUT" in failures[0]
    assert summary.source_errors == 1
    # The healthy instrument is still materialized; the failed one is NOT.
    assert len(client.snapshot_payloads()) == 1
    assert summary.cycles == 1
    done = [line for line in lines if line.startswith("OPIP_FEATURE_BUS_CAPTURE_PHASE=done")]
    assert len(done) == 1
    assert "status=OK" in done[0]
    # The full phase sequence is observable, not only the failure.
    stages = [line.split(" ")[0].split("=", 1)[1] for line in lines]
    for stage in (
        "start",
        "universe_ready",
        "acquire",
        "acquire_complete",
        "materialize",
        "done",
    ):
        assert stage in stages, stage


def test_ac_020_zero_materialization_emits_a_durable_disposition_marker(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: a pass that materializes zero snapshots is never indistinguishable from a silent evidence drop -- it emits an explicit flushed zero-snapshot disposition."""
    versions = [_instrument(1), _instrument(2)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }

    class _AllFailingSource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            raise RuntimeError("unexpected transport failure")

    lines = _record_markers(monkeypatch)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_AllFailingSource(),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert summary.cycles == 0
    assert client.snapshot_payloads() == []
    zero = [line for line in lines if "PHASE=zero_snapshots" in line]
    assert len(zero) == 1
    assert "fetched=0" in zero[0] and "source_errors=2" in zero[0]
    done = [line for line in lines if line.startswith("OPIP_FEATURE_BUS_CAPTURE_PHASE=done")]
    assert "status=NO_SNAPSHOTS" in done[0]


def test_ac_020_deadline_exhaustion_emits_a_durable_disposition_marker(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: an exhausted acquisition deadline is recorded as an explicit durable disposition naming the reason and the remaining/required budget."""
    versions = [_instrument(i) for i in range(6)]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    clock = {"value": 0.0}

    def _clock():
        return clock["value"]

    class _AdvancingSource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def __init__(self) -> None:
            self.calls = 0

        def fetch_through(self, version, *, watermark, now):
            self.calls += 1
            if self.calls >= 2:
                clock["value"] = 1000.0
            return batches[version.instrument_version_id]

    lines = _record_markers(monkeypatch)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_concurrency=2),
        now=T0,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_AdvancingSource(),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )
    assert summary.budget_exhausted is True
    deadline = [line for line in lines if "PHASE=deadline_exhausted" in line]
    assert len(deadline) == 1
    assert "where=acquire" in deadline[0]
    assert "reason=INSUFFICIENT_ACQUISITION_BUDGET" in deadline[0]
    assert "instruments_acquired=2" in deadline[0]
    assert "instruments_pending=4" in deadline[0]
    assert _marker_field(deadline[0], "required_seconds") == WAVE_BUDGET_SECONDS
    done = [line for line in lines if line.startswith("OPIP_FEATURE_BUS_CAPTURE_PHASE=done")]
    assert "budget_exhausted=True" in done[0]


def test_ac_020_two_minute_passes_satisfy_the_runtime_verifier():
    """ATDD-R4-B2-controlled-paper-activation/AC-020 and ATDD-RELEASE-PIPELINE-v1/AC-017: the two consecutive minute passes satisfy the UNCHANGED runtime verifier (exact 60-second cadence plus a matching fresh F5), and the same pair fails it when F5 is anchored only to the top of the hour."""
    from app.opip.contracts.feasibility_evidence import FeasibilityEvidence
    from app.opip.features.committed_snapshot_reader import feature_snapshot_from_payload
    from app.services.release_runtime_verifier import (
        MAX_FEV_SOURCE_AGE,
        _new_evidence_is_valid,
    )

    assert MAX_FEV_SOURCE_AGE == timedelta(seconds=120)
    version = _instrument(1)
    batches_a = {
        version.instrument_version_id: _batch(version, _observations(version, T0))
    }
    batches_b = {
        version.instrument_version_id: _batch(version, _observations(version, T60))
    }
    _, client_a = _run_pass(now=T0, versions=[version], batches=batches_a)
    _, client_b = _run_pass(now=T60, versions=[version], batches=batches_b)
    snap_a = feature_snapshot_from_payload(client_a.snapshot_payloads()[0])
    snap_b = feature_snapshot_from_payload(client_b.snapshot_payloads()[0])
    assert snap_a.evaluation_grid_seconds == 60
    assert snap_b.evaluation_grid_seconds == 60
    assert snap_b.evaluation_cutoff - snap_a.evaluation_cutoff == timedelta(seconds=60)

    def _evidence(source_cutoff):
        return FeasibilityEvidence(
            instrument_version_id=snap_b.instrument_version_id,
            venue_instrument_id=snap_b.venue_instrument_id,
            direction="LONG",
            evaluation_time=snap_b.evaluation_cutoff,
            source_cutoff=source_cutoff,
            source_snapshot_id=snap_b.snapshot_id,
            source_evidence_refs=(snap_b.snapshot_id,),
            market_data_validation=None,
            margin_validation_status=None,
            margin_eligible=None,
            margin_venue_symbol=None,
            margin_max_leverage=None,
            execution_validation=None,
            availability="AVAILABLE",
            missingness=(),
            kraken_public_symbol=snap_b.venue_instrument_id,
            primary_pair=snap_b.venue_instrument_id,
        )

    now = T60 + timedelta(seconds=72)  # the production commit-lag shape
    ready_after = T0 - timedelta(seconds=1)
    passed, report = _new_evidence_is_valid(
        [snap_a, snap_b], [_evidence(T60)], ready_after=ready_after, now=now
    )
    assert passed is True
    assert report["consecutive_60s_snapshots"] is True
    assert report["feasibility_matches_fresh_snapshot"] is True

    # Same lineage, same cadence -- only the source anchor is stale (HH:00).
    hourly_only = _evidence(T0)
    assert now - hourly_only.source_cutoff > MAX_FEV_SOURCE_AGE
    passed_hourly, report_hourly = _new_evidence_is_valid(
        [snap_a, snap_b], [hourly_only], ready_after=ready_after, now=now
    )
    assert passed_hourly is False
    assert report_hourly["consecutive_60s_snapshots"] is True
    assert report_hourly["feasibility_matches_fresh_snapshot"] is False


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
