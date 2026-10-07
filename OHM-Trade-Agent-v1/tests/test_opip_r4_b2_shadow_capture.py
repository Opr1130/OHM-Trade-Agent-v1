"""R4-B2 bounded Feature Bus SHADOW capture: acceptance guards (AC-016).

Proves the bounded, budgeted, per-instrument-isolated SHADOW evidence producer and
that it is fully off the protected unified cycle: exact SHADOW gating, post-fetch
evaluation time, per-instrument fault isolation, no publication from an errored
batch, an internal wall-clock budget, and a dedicated non-overlapping scheduler
entry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.jobs.capture_feature_bus_shadow as capture
from app.opip.contracts.enums import CoverageState
from app.opip.contracts.observation import SourceWatermark
from app.opip.features.publisher import FeatureBusPublisher
from app.opip.market.source import SourceBatch, SourceMetrics

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _settings(**overrides) -> SimpleNamespace:
    base = {
        "opip_feature_bus_mode": "shadow",
        "opip_canonical_writer_mode": "shadow",
        "opip_feature_bus_capture_limit": 8,
        "opip_feature_bus_capture_budget_seconds": 180,
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

    def submit(self, intent):
        self.intents.append(intent)
        return _FakeAck(seq=len(self.intents))

    def snapshot_payloads(self):
        return [
            intent.payload
            for intent in self.intents
            if intent.payload.get("record_type") == "FeatureSnapshot"
        ]


def _provider(versions):
    class _P:
        def refresh(self, *, observed_at_utc):
            return list(versions)

    return _P()


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


def _batch(version, observations, *, error=None):
    return SourceBatch(
        instrument_version=version,
        observations=tuple(observations),
        watermark=SourceWatermark(
            instrument_version_id=version.instrument_version_id, through_utc=NOW
        ),
        coverage=CoverageState.COMPLETE,
        metrics=SourceMetrics(observations=len(observations)),
        error=error,
    )


def _instruments(n):
    from app.opip.contracts.identity import InstrumentVersion

    versions = []
    for i in range(n):
        # Distinct base asset per instrument so instrument_version_id differs.
        versions.append(
            InstrumentVersion(
                venue="synthetic",
                base_asset=f"SYN{i}",
                quote_currency="USD",
                venue_instrument_id=f"SYNTHETIC-SYN{i}USD",
                version=1,
                reference_data_version="opip-evidence-identity-v1",
                observed_at_utc=NOW,
                price_decimals=2,
                tick_size=0.01,
                min_order_size=0.2,
            )
        )
    return versions


def _observations(version):
    from app.jobs.run_feature_bus_pilot import _synthetic_observations

    return _synthetic_observations(version, cutoff=NOW, intervals=140, now=NOW)


# ---------------------------------------------------------------------------
# AC-016 exact SHADOW gate matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bus", "writer", "expected_inert"),
    [
        ("off", "off", True),
        ("shadow", "off", True),
        ("off", "shadow", True),
        ("active", "shadow", True),  # active must NOT authorize this SHADOW job
        ("shadow", "shadow", False),
        ("active", "active", True),
    ],
)
def test_ac_016_exact_shadow_gate_matrix(bus, writer, expected_inert):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: this producer is authorized only for exactly Feature Bus shadow AND writer shadow; active does not authorize it."""
    settings = _settings(opip_feature_bus_mode=bus, opip_canonical_writer_mode=writer)
    assert capture.shadow_capture_authorized(settings) is (not expected_inert)
    summary = capture.capture_feature_bus_shadow(settings=settings, now=NOW)
    assert summary.inert is expected_inert
    if expected_inert:
        assert summary.enabled is False


# ---------------------------------------------------------------------------
# AC-016 post-fetch evaluation time
# ---------------------------------------------------------------------------


def test_ac_016_evaluated_at_is_post_fetch(monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: decision availability uses the completed fetch time, not the pre-fetch capture start, and point-in-time validation stays strict."""
    versions = _instruments(1)
    version = versions[0]
    observations = _observations(version)
    # The observation is received one second after capture start.
    receipt_after_start = NOW + timedelta(seconds=1)
    for observation in observations:
        object.__setattr__(observation, "receipt_time", receipt_after_start)

    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    fetch_done = NOW + timedelta(seconds=5)
    monkeypatch.setattr(capture, "grid_floor", lambda value: NOW)

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source({version.instrument_version_id: _batch(version, observations)}),
        restore_continuity=lambda versions: ({}, {}, {}),
        wall_clock=lambda: fetch_done,
    )
    assert summary.cycles == 1
    payloads = client.snapshot_payloads()
    assert payloads, "a valid snapshot must be produced"
    committed_at = datetime.fromisoformat(
        str(payloads[0]["evaluated_at_utc"]).replace("Z", "+00:00")
    )
    # Decision availability is the post-fetch completed time, not the capture start.
    assert committed_at == fetch_done
    assert committed_at > NOW


# ---------------------------------------------------------------------------
# AC-016 per-instrument fault isolation + errored batch
# ---------------------------------------------------------------------------


def test_ac_016_per_instrument_fault_isolation():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: one instrument's unexpected fetch failure does not discard successful evidence from other instruments."""
    versions = _instruments(3)
    v0, v1, v2 = versions
    batches = {
        v0.instrument_version_id: _batch(v0, _observations(v0)),
        v2.instrument_version_id: _batch(v2, _observations(v2)),
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches, raising_ids={v1.instrument_version_id}),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    # The failed middle instrument is recorded; the other two still produced snapshots.
    assert summary.source_errors == 1
    assert summary.cycles == 2
    assert any("fetch failed" in err for err in summary.errors)
    assert len(client.snapshot_payloads()) == 2


def test_ac_016_errored_batch_publishes_no_fresh_snapshot():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: a SourceBatch carrying batch.error publishes no fresh snapshot, the error stays observable, and unaffected instruments continue."""
    versions = _instruments(2)
    v0, v1 = versions
    batches = {
        v0.instrument_version_id: _batch(v0, (), error="kraken returned no candles"),
        v1.instrument_version_id: _batch(v1, _observations(v1)),
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    # Only the healthy instrument produced a snapshot; the errored one produced none.
    assert summary.source_errors == 1
    assert summary.cycles == 1
    assert any("source error" in err for err in summary.errors)
    assert len(client.snapshot_payloads()) == 1


# ---------------------------------------------------------------------------
# AC-016 internal wall-clock budget
# ---------------------------------------------------------------------------


def test_ac_016_internal_budget_stops_further_requests():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: once the remaining budget cannot fit one bounded request the loop stops requesting more instruments, records budget exhaustion, and fabricates nothing."""
    versions = _instruments(4)
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    # First tick = start; the next tick is already past the deadline.
    ticks = iter([0.0, 1000.0])

    def _clock():
        try:
            return next(ticks)
        except StopIteration:
            return 1000.0

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
        budget_seconds=180.0,
        clock=_clock,
    )
    assert summary.budget_exhausted is True
    # Nothing was fetched once the budget was exceeded.
    assert summary.fetched == 0
    assert client.snapshot_payloads() == []
    assert any("budget exhausted" in err for err in summary.errors)


def test_ac_016_configured_limit_and_budget_reach_capture():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the configured capture limit and budget are read from Settings (no explicit argument) and actually bound the pass."""
    versions = _instruments(5)
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    # Only the settings carry the values; no explicit limit/budget argument.
    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_limit=2),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert summary.instruments == 2
    assert summary.cycles == 2


def test_ac_016_pre_acquisition_phases_are_independently_attributable(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: each pre-acquisition phase's wall-clock cost is attributable on its own completion marker, so a production DEADLINE_EXHAUSTED pass can name which phase consumed the setup allowance."""
    versions = _instruments(1)
    version = versions[0]
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    # Deterministic monotonic clock. ``tick()`` only READS the current value; the
    # phase stubs ADVANCE it explicitly for the phase they own. This keeps the
    # repeated telemetry reads (phase_seconds, elapsed_seconds, budget_remaining)
    # observational and deterministic: refresh=2s, publish=3s, continuity=4s.
    clock = {"value": 0.0}

    def _clock():
        return clock["value"]

    class _AdvancingProvider:
        def refresh(self, *, observed_at_utc):
            clock["value"] += 2.0
            return list(versions)

    class _AdvancingPublisher:
        def __init__(self, inner):
            self._inner = inner

        def publish_instrument_version(self, version):
            clock["value"] += 3.0
            return self._inner.publish_instrument_version(version)

        def summary(self):
            return self._inner.summary()

        def resolved_writer_client(self):
            return self._inner.resolved_writer_client()

        def __getattr__(self, name):
            # Transparent delegation for every other publisher operation (e.g.
            # the Phase-B ``run_cycle`` publish path), so the wrapper is a
            # faithful FeatureBusPublisher everywhere except the timed
            # instrument-version publication above.
            return getattr(self._inner, name)

    def _advancing_restore(versions):
        clock["value"] += 4.0
        return ({}, {}, {})

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(),
        now=NOW,
        publisher=_AdvancingPublisher(publisher),
        instrument_provider=_AdvancingProvider(),
        source=_source(batches),
        restore_continuity=_advancing_restore,
        clock=_clock,
    )
    assert summary.budget_exhausted is False
    out = capsys.readouterr().out
    # Each phase's completion marker carries its own phase_seconds.
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=universe_ready" in out
    assert "phase_seconds=2.0" in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=instrument_versions_committed" in out
    assert "phase_seconds=3.0" in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=continuity_restored" in out
    assert "phase_seconds=4.0" in out
    # The acquire marker exposes the total pre-acquisition cost.
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=acquire" in out
    assert "pre_acquisition_seconds=9.0" in out


def test_ac_016_pre_acquisition_delay_rejects_first_wave(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: a pre-acquisition delay that consumes the setup allowance makes the EXISTING first-wave gate reject acquisition -- no fetch, no snapshot, explicit budget-exhausted evidence, and telemetry names the consuming phase.

    This regression EXPLICITLY configures the 45-second production budget (it is
    NOT the implicit test default) so the arithmetic models the target production
    case exactly:

        budget = 45
        materialize reserve = 10
        acquisition_deadline = started + 35
        pre-acquisition elapsed = 25
        remaining = 35 - 25 = 10
        wave_bound = 15
        10 < 15  =>  the existing gate rejects the first wave

    The gate is strict ``<``, so ``remaining == wave_bound`` would be admitted;
    this test deliberately lands strictly below it.
    """
    versions = _instruments(2)
    v0, v1 = versions
    batches = {
        v0.instrument_version_id: _batch(v0, _observations(v0)),
        v1.instrument_version_id: _batch(v1, _observations(v1)),
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    fetch_calls = []

    class _SpySource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            fetch_calls.append(version.instrument_version_id)
            return batches[version.instrument_version_id]

    # Explicitly configure the 45-second production budget. ``tick()`` only READS
    # the current value; the stubs ADVANCE it, so the delay is attributable to a
    # named phase.
    clock = {"value": 0.0}

    def _clock():
        return clock["value"]

    class _SlowProvider:
        def refresh(self, *, observed_at_utc):
            clock["value"] += 25.0
            return list(versions)

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        instrument_provider=_SlowProvider(),
        source=_SpySource(),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )
    # The existing first-wave gate rejected acquisition: nothing was fetched.
    assert fetch_calls == []
    assert summary.fetched == 0
    assert summary.cycles == 0
    assert summary.source_errors == 0
    assert summary.budget_exhausted is True
    # No FeatureSnapshot was fabricated.
    assert client.snapshot_payloads() == []
    out = capsys.readouterr().out
    # Telemetry attributes the delay to the refresh phase and names the rejected wave.
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=universe_ready" in out
    assert "phase_seconds=25.0" in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=acquire" in out
    assert "pre_acquisition_seconds=25.0" in out
    assert "budget_remaining=10.0" in out
    assert "wave_bound_seconds=15.0" in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=deadline_exhausted" in out
    assert "reason=INSUFFICIENT_ACQUISITION_BUDGET" in out


def test_ac_016_healthy_setup_within_envelope_reaches_acquisition(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: setup that stays inside its envelope (acquisition_deadline - wave_bound) still reaches acquisition and materializes normally."""
    versions = _instruments(1)
    version = versions[0]
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    clock = {"value": 0.0}

    def _clock():
        return clock["value"]

    class _AdvancingProvider:
        def refresh(self, *, observed_at_utc):
            clock["value"] += 2.0
            return list(versions)

    class _AdvancingPublisher:
        def __init__(self, inner):
            self._inner = inner

        def publish_instrument_version(self, version):
            clock["value"] += 3.0
            return self._inner.publish_instrument_version(version)

        def summary(self):
            return self._inner.summary()

        def resolved_writer_client(self):
            return self._inner.resolved_writer_client()

        def __getattr__(self, name):
            # Transparent delegation for every other publisher operation (e.g.
            # the Phase-B ``run_cycle`` publish path), so the wrapper is a
            # faithful FeatureBusPublisher everywhere except the timed
            # instrument-version publication above.
            return getattr(self._inner, name)

    def _advancing_restore(versions):
        clock["value"] += 4.0
        return ({}, {}, {})

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=_AdvancingPublisher(publisher),
        instrument_provider=_AdvancingProvider(),
        source=_source(batches),
        restore_continuity=_advancing_restore,
        clock=_clock,
    )
    # 9s setup < 20s envelope: acquisition proceeds and materializes.
    assert summary.budget_exhausted is False
    assert summary.cycles == 1
    assert len(client.snapshot_payloads()) == 1
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" not in out


def test_ac_016_setup_bound_refresh_deadline_is_setup_budget_exhaustion(
    monkeypatch, capsys
):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: a typed Kraken deadline during a SETUP-BOUND refresh (locally created client armed with setup_deadline) is classified as setup-budget exhaustion -- publication and acquisition are never entered, no snapshot is fabricated."""
    from app.services.kraken_transport import KrakenTransportDeadlineExceeded

    versions = _instruments(1)
    version = versions[0]
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    fetch_calls = []

    class _SpySource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            fetch_calls.append(version.instrument_version_id)
            return batches[version.instrument_version_id]

    class _DeadlineProvider:
        def refresh(self, *, observed_at_utc):
            # Mirrors KrakenClient._get: the typed deadline is re-raised as a
            # generic error with the typed exception as __cause__.
            try:
                raise KrakenTransportDeadlineExceeded("deadline exhausted before attempt")
            except KrakenTransportDeadlineExceeded as exc:
                raise RuntimeError("kraken api error") from exc

    class _StubKrakenClient:
        timeout_seconds = 1.0
        deadline_monotonic = None

    # Force the LOCAL-client path so refresh is setup-bound.
    monkeypatch.setattr(
        capture, "capture_kraken_client", lambda **kwargs: _StubKrakenClient()
    )
    monkeypatch.setattr(
        capture,
        "KrakenInstrumentProvider",
        lambda registry=None, client=None: _DeadlineProvider(),
    )
    monkeypatch.setattr(
        capture, "kraken_minute_source", lambda client=None: _SpySource()
    )
    monkeypatch.setattr(capture, "hydrate_instrument_version_registry", lambda: None)

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert fetch_calls == []
    assert summary.fetched == 0
    assert summary.cycles == 0
    assert summary.budget_exhausted is True
    assert client.snapshot_payloads() == []
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" in out
    assert "where=refresh_universe" in out
    assert "reason=INSUFFICIENT_SETUP_BUDGET" in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=publish_instrument_versions" not in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=acquire" not in out


def test_ac_016_injected_refresh_deadline_is_ordinary_failure(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: an INJECTED provider's chained Kraken deadline is NOT our setup deadline -- it keeps ordinary provider-failure semantics and is NOT misclassified as setup-budget exhaustion."""
    from app.services.kraken_transport import KrakenTransportDeadlineExceeded

    versions = _instruments(1)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    class _InjectedDeadlineProvider:
        def refresh(self, *, observed_at_utc):
            try:
                raise KrakenTransportDeadlineExceeded("deadline exhausted before attempt")
            except KrakenTransportDeadlineExceeded as exc:
                raise RuntimeError("kraken api error") from exc

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        instrument_provider=_InjectedDeadlineProvider(),
        source=_source({}),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert summary.budget_exhausted is False
    assert summary.cycles == 0
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" not in out
    assert "status=FAILED" in out


def test_ac_016_ordinary_refresh_failure_is_not_budget_exhaustion(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: an ordinary provider failure keeps its existing FAILED semantics and is NOT misclassified as setup-budget exhaustion."""
    versions = _instruments(1)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    class _BrokenProvider:
        def refresh(self, *, observed_at_utc):
            raise RuntimeError("kraken api error")

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        instrument_provider=_BrokenProvider(),
        source=_source({}),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert summary.budget_exhausted is False
    assert summary.cycles == 0
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" not in out
    assert "status=FAILED" in out


def test_ac_016_setup_bound_writer_deadline_is_setup_budget_exhaustion(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: a WriterDeadlineExceeded publish outcome while the writer is SETUP-BOUND is classified as setup-budget exhaustion -- continuity and acquisition are never entered, no snapshot is fabricated."""
    versions = _instruments(1)
    version = versions[0]
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    fetch_calls = []

    class _SpySource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            fetch_calls.append(version.instrument_version_id)
            return batches[version.instrument_version_id]

    class _DeadlinePublisher:
        def __init__(self, inner):
            self._inner = inner

        def publish_instrument_version(self, version):
            from app.opip.features.publisher import PublishOutcome

            return PublishOutcome(
                event_type="market.instrument_version.recorded",
                idempotency_key="k",
                status="SPOOLED",
                error_code="WriterDeadlineExceeded",
            )

        def summary(self):
            return self._inner.summary()

        def resolved_writer_client(self):
            # A bindable client so ``_bind_writer_deadline`` succeeds and the
            # writer is provably setup-bound.
            return _BindableClient()

    class _BindableClient:
        def __init__(self):
            self.deadline_monotonic = None

        def bind_deadline(self, deadline_monotonic):
            self.deadline_monotonic = deadline_monotonic
            return self.deadline_monotonic

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=_DeadlinePublisher(publisher),
        instrument_provider=_provider(versions),
        source=_SpySource(),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert fetch_calls == []
    assert summary.fetched == 0
    assert summary.cycles == 0
    assert summary.budget_exhausted is True
    assert client.snapshot_payloads() == []
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" in out
    assert "where=publish_instrument_versions" in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=restore_continuity" not in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=acquire" not in out


def test_ac_016_unbound_writer_deadline_is_ordinary_failure(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: an UNBOUND/injected publisher that merely returns WriterDeadlineExceeded keeps ordinary publication-failure semantics -- we cannot prove the cut belongs to our setup deadline."""
    versions = _instruments(1)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    class _UnboundDeadlinePublisher:
        def __init__(self, inner):
            self._inner = inner

        def publish_instrument_version(self, version):
            from app.opip.features.publisher import PublishOutcome

            return PublishOutcome(
                event_type="market.instrument_version.recorded",
                idempotency_key="k",
                status="SPOOLED",
                error_code="WriterDeadlineExceeded",
            )

        def summary(self):
            return self._inner.summary()

        def resolved_writer_client(self):
            # No bind_deadline and no deadline_monotonic: the bind cannot take
            # effect, so the writer is NOT setup-bound.
            return object()

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=_UnboundDeadlinePublisher(publisher),
        instrument_provider=_provider(versions),
        source=_source({}),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert summary.budget_exhausted is False
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" not in out
    assert "status=NOTHING_CAPTURED" in out


def test_ac_016_ordinary_publication_failure_preserves_behavior(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: an ordinary publication failure keeps its existing semantics and is NOT misclassified as setup-budget exhaustion."""
    versions = _instruments(1)
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    class _BrokenPublisher:
        def __init__(self, inner):
            self._inner = inner

        def publish_instrument_version(self, version):
            from app.opip.features.publisher import PublishOutcome

            return PublishOutcome(
                event_type="market.instrument_version.recorded",
                idempotency_key="k",
                status="REJECTED",
                error_code="SOME_OTHER_ERROR",
            )

        def summary(self):
            return self._inner.summary()

        def resolved_writer_client(self):
            return self._inner.resolved_writer_client()

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=_BrokenPublisher(publisher),
        instrument_provider=_provider(versions),
        source=_source({}),
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert summary.budget_exhausted is False
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" not in out
    assert "status=NOTHING_CAPTURED" in out


def test_ac_016_continuity_deadline_is_setup_budget_exhaustion(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: a typed continuity deadline is classified as setup-budget exhaustion -- acquisition is never entered, no partial continuity is used, no snapshot is fabricated."""
    from app.opip.features.checkpoint_store import CheckpointDeadlineExceeded

    versions = _instruments(1)
    version = versions[0]
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    fetch_calls = []

    class _SpySource:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            fetch_calls.append(version.instrument_version_id)
            return batches[version.instrument_version_id]

    def _deadline_restore(versions):
        raise CheckpointDeadlineExceeded("deadline exceeded during query")

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_SpySource(),
        restore_continuity=_deadline_restore,
    )
    assert fetch_calls == []
    assert summary.fetched == 0
    assert summary.cycles == 0
    assert summary.budget_exhausted is True
    assert client.snapshot_payloads() == []
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=setup_deadline_exhausted" in out
    assert "where=restore_continuity" in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=acquire" not in out



def test_ac_016_setup_deadline_uses_actual_wave_bound(monkeypatch, capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the setup envelope is derived from the ACTUAL wave bound the acquisition gate uses, so the two can never drift.

    This test REALLY exercises the calculated wave-bound path: it forces the
    local-client path (no injected provider/source), stubs the local Kraken
    client/provider construction so no network request is made, and makes
    ``capture_worst_case_request_seconds`` return a value DIFFERENT from
    ``PER_REQUEST_BUDGET_SECONDS``. It fails if production goes back to
    ``setup_deadline = acquisition_deadline - PER_REQUEST_BUDGET_SECONDS``.
    """
    versions = _instruments(1)
    version = versions[0]
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    # Force the local-client path without a network request: the local Kraken
    # client is a stub, and the provider/source are built from it.
    class _StubKrakenClient:
        timeout_seconds = 1.0
        deadline_monotonic = None

    monkeypatch.setattr(
        capture, "capture_kraken_client", lambda **kwargs: _StubKrakenClient()
    )
    monkeypatch.setattr(
        capture,
        "KrakenInstrumentProvider",
        lambda registry=None, client=None: _provider(versions),
    )
    monkeypatch.setattr(
        capture, "kraken_minute_source", lambda client=None: _source(batches)
    )
    monkeypatch.setattr(
        capture, "hydrate_instrument_version_registry", lambda: None
    )
    # The ACTUAL wave bound differs from PER_REQUEST_BUDGET_SECONDS (15.0).
    monkeypatch.setattr(
        capture, "capture_worst_case_request_seconds", lambda **kwargs: 17.0
    )

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        restore_continuity=lambda versions: ({}, {}, {}),
    )
    assert summary.budget_exhausted is False
    out = capsys.readouterr().out
    # budget=45 -> acquisition_deadline=35, actual wave_bound=17, setup_deadline=18.
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=budget_declared" in out
    assert "wave_bound_seconds=17.0" in out
    assert "setup_budget_seconds=18.0" in out
    assert "acquisition_budget_seconds=35.0" in out
    # The materialization reserve is unchanged by the setup-budget invariant:
    # the same production-path budget declaration still reports the 10s reserve.
    assert "materialize_reserve_seconds=10.0" in out


def test_ac_016_first_wave_boundary_equality_is_admissible(capsys):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the first-wave gate is strict ``<``, so remaining == wave_bound is ADMITTED -- the boundary semantics are unchanged by the setup envelope."""
    versions = _instruments(1)
    version = versions[0]
    batches = {version.instrument_version_id: _batch(version, _observations(version))}
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    # budget=45 -> acquisition_deadline=35, wave_bound=15. Land EXACTLY on the
    # boundary: remaining == 15 == wave_bound, which the strict gate admits.
    clock = {"value": 0.0}

    def _clock():
        return clock["value"]

    class _BoundaryProvider:
        def refresh(self, *, observed_at_utc):
            clock["value"] += 20.0
            return list(versions)

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        instrument_provider=_BoundaryProvider(),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )
    # remaining == wave_bound is admitted: acquisition proceeds.
    assert summary.budget_exhausted is False
    assert summary.cycles == 1
    out = capsys.readouterr().out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=acquire" in out
    assert "budget_remaining=15.0" in out
    assert "wave_bound_seconds=15.0" in out


def test_ac_016_configured_budget_is_read_from_settings():
    """ATDD-R4-B2-controlled-paper-activation/AC-016 and ATDD-RELEASE-PIPELINE-v1/AC-017: the configured budget is read from Settings. A clock jump that would exhaust the default 45s budget but not the configured larger budget proves the configured value was used (both stay within the 60-second cadence slot)."""
    versions = _instruments(3)
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    # start tick = 0. Acquisition may proceed only while
    # (acquisition_deadline - now) >= 15, where
    # acquisition_deadline = budget - materialize_reserve and the reserve is
    # min(10, budget - 15) = 10s for BOTH a default 45s and a configured 50s budget.
    # So the gate is (45-10-now) >= 15 for the default and (50-10-now) >= 15 for the
    # configured budget. A 23s jump exhausts the default 45s budget
    # (35-23=12 < 15) but not a configured 50s budget (40-23=17 >= 15), so only a
    # real settings read can avoid exhaustion -- and the reserved Phase-B window is
    # honoured at exactly the same margins as the pre-reserve contract.
    ticks = iter([0.0, 23.0])

    def _clock():
        try:
            return next(ticks)
        except StopIteration:
            return 23.0

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=50),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
        restore_continuity=lambda versions: ({}, {}, {}),
        clock=_clock,
    )
    assert summary.budget_exhausted is False
    assert summary.fetched == 3


# ---------------------------------------------------------------------------
# AC-016 scheduler safety
# ---------------------------------------------------------------------------


def test_ac_016_sqlite_progress_handler_interrupts_at_deadline(tmp_path, monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: a durable checkpoint read is SQLite-VM interruptible at its deadline -- the progress handler aborts the real query and the typed deadline exception is raised, with no partial state returned."""
    import sqlite3

    from app.opip.canonical import schema as schema_module
    from app.opip.features import checkpoint_store as store_module

    # Real SQLite DB with a real events table and enough rows that the query runs
    # many VM instructions. No sleeps: the injected clock moves past the deadline
    # between the pre-query check and the progress callback.
    db = tmp_path / "canonical.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema_module.DDL)
    conn.execute(
        "INSERT INTO meta (id, schema_version, history_epoch, next_local_sequence, created_at, updated_at) VALUES (1, ?, 1, 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (schema_module.SCHEMA_VERSION,),
    )
    for i in range(500):
        conn.execute(
            "INSERT INTO events (event_id, schema_version, event_type, history_epoch, local_sequence, recorded_at, idempotency_key, payload_json) VALUES (?, ?, ?, 1, ?, '2026-01-01T00:00:00Z', ?, '{}')",
            (f"EV:{i}", schema_module.SCHEMA_VERSION, "feature.checkpoint.recorded", i + 1, f"K:{i}"),
        )
    conn.commit()
    conn.close()

    # Progress interval 1 guarantees the callback runs as the VM executes.
    monkeypatch.setattr(store_module, "_SQLITE_DEADLINE_PROGRESS_OPS", 1)

    calls = {"count": 0}

    def fake_clock():
        calls["count"] += 1
        return 0.0 if calls["count"] == 1 else 2.0

    with pytest.raises(store_module.CheckpointDeadlineExceeded):
        store_module.load_latest_checkpoint_payload(
            "IV:any",
            db_path=db,
            deadline_monotonic=1.0,
            clock=fake_clock,
        )


def test_ac_016_unrelated_sqlite_error_is_not_translated(tmp_path, monkeypatch):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: an unrelated sqlite3.DatabaseError with deadline_triggered=False propagates unchanged, never misclassified as a deadline."""
    import sqlite3

    from app.opip.features import checkpoint_store as store_module

    db = tmp_path / "canonical.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("CREATE TABLE meta (id INTEGER PRIMARY KEY, schema_version INTEGER);")
    conn.commit()
    conn.close()

    # The events table is absent, so the query raises sqlite3.OperationalError
    # (a DatabaseError) with deadline_triggered=False.
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        store_module.load_latest_checkpoint_payload(
            "IV:any",
            db_path=db,
            deadline_monotonic=1.0,
            clock=lambda: 0.0,
        )
    assert not isinstance(excinfo.value, store_module.CheckpointDeadlineExceeded)


def test_ac_016_restore_pilot_continuity_legacy_loaders_without_deadline():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: restore_pilot_continuity with no deadline sends NO new kwargs, so a legacy injected loader that accepts only the historical arguments keeps working."""
    from app.jobs.run_feature_bus_pilot import restore_pilot_continuity

    versions = _instruments(1)
    seen = []

    def _no_state(instrument_version_id: str):
        seen.append(("state", instrument_version_id))
        return None

    def _no_ledger(instrument_version_id: str, *, interval_seconds, since_interval_epoch):
        seen.append(("ledger", instrument_version_id))
        return None

    states, ledgers, watermarks = restore_pilot_continuity(
        versions,
        load_state=_no_state,
        load_ledger=_no_ledger,
    )
    assert states == {}
    assert watermarks == {}
    assert len(seen) == 2


def test_ac_016_restore_pilot_continuity_forwards_deadline_when_supplied():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: when a deadline IS supplied, the canonical deadline-aware loaders receive both deadline_monotonic and clock."""
    from app.jobs.run_feature_bus_pilot import restore_pilot_continuity

    versions = _instruments(1)
    seen = []

    def _state(instrument_version_id: str, *, deadline_monotonic, clock):
        seen.append(("state", deadline_monotonic, clock))
        return None

    def _ledger(
        instrument_version_id: str,
        *,
        interval_seconds,
        since_interval_epoch,
        deadline_monotonic,
        clock,
    ):
        seen.append(("ledger", deadline_monotonic, clock))
        return None

    def _clock():
        return 0.0

    restore_pilot_continuity(
        versions,
        load_state=_state,
        load_ledger=_ledger,
        deadline_monotonic=20.0,
        clock=_clock,
    )
    assert len(seen) == 2
    assert all(entry[1] == 20.0 for entry in seen)
    assert all(entry[2] is _clock for entry in seen)


class _FakeCursor:
    """Deterministic cursor whose fetchall returns controlled rows immediately.

    Its connection's ``set_progress_handler`` is a no-op, so the SQLite VM can
    never fire the deadline callback: any deadline exception raised by the code
    under test MUST come from the post-fetch Python row-processing check.
    """

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _FakeCursor(self._rows)

    def set_progress_handler(self, *_args, **_kwargs):
        # Deliberately inert: the VM never interrupts, so only the Python
        # row-processing deadline check can raise.
        return None

    def close(self):
        return None


def _valid_checkpoint_row(instrument_version_id: str):
    import json as _json

    payload = {
        "instrument_version_id": instrument_version_id,
        "venue_instrument_id": "SYNTHETIC-SYN0USD",
        "feature_version": "opip-features-v1",
        "consumed_input_watermark": {"history_epoch": 1, "local_sequence": 1},
        "rolling_state": {},
        "restart_state": "COLD_START",
        "reconstruction_dependencies": ["fixed_interval_aggregate:60s"],
        "created_at_utc": "2026-01-01T00:00:00Z",
        "schema_version": 1,
    }
    return {"payload_json": _json.dumps(payload)}


def test_ac_016_checkpoint_row_processing_observes_deadline(monkeypatch, tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: checkpoint Python row reconstruction observes the SAME deadline as the SQLite query.

    The connection seam is a deterministic fake whose ``set_progress_handler`` is
    inert, so the SQLite VM cannot fire the deadline callback. The only way this
    test can raise is the post-fetch Python row-processing check, which is exactly
    what it must prove.
    """
    from app.opip.canonical import schema as schema_module
    from app.opip.features import checkpoint_store as store_module

    rows = [_valid_checkpoint_row("IV:any")]
    monkeypatch.setattr(
        schema_module, "connect", lambda *_a, **_k: _FakeConnection(rows)
    )
    # The db file must exist for the read to proceed.
    db = tmp_path / "canonical.db"
    db.write_bytes(b"")

    # First read is the pre-query check (below deadline); the next read is the
    # first Python row-processing check (at/above deadline).
    calls = {"count": 0}

    def fake_clock():
        calls["count"] += 1
        return 0.0 if calls["count"] == 1 else 2.0

    with pytest.raises(store_module.CheckpointDeadlineExceeded):
        store_module.load_latest_checkpoint_payload(
            "IV:any", db_path=db, deadline_monotonic=1.0, clock=fake_clock
        )


def test_ac_016_revision_ledger_row_processing_observes_deadline(monkeypatch, tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-016: revision-ledger Python row reconstruction observes the SAME deadline as the SQLite query.

    The connection seam is a deterministic fake whose ``set_progress_handler`` is
    inert, so the SQLite VM cannot fire the deadline callback. The only way this
    test can raise is the post-fetch Python row-processing check.
    """
    from app.opip.canonical import schema as schema_module
    from app.opip.features import revision_ledger as ledger_module

    rows = [{"payload_json": "{}", "history_epoch": 1, "local_sequence": 1}]
    monkeypatch.setattr(
        schema_module, "connect", lambda *_a, **_k: _FakeConnection(rows)
    )
    db = tmp_path / "canonical.db"
    db.write_bytes(b"")

    calls = {"count": 0}

    def fake_clock():
        calls["count"] += 1
        return 0.0 if calls["count"] == 1 else 2.0

    with pytest.raises(ledger_module.RevisionLedgerDeadlineExceeded):
        ledger_module.load_revision_ledger(
            "IV:any", db_path=db, deadline_monotonic=1.0, clock=fake_clock
        )


def test_ac_016_unified_cycle_does_not_run_capture():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the protected unified cycle neither imports nor invokes the Feature Bus capture."""
    cycle = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "capture_feature_bus_shadow" not in cycle
    assert "feature_bus" not in cycle.lower()


def test_ac_016_capture_has_a_bounded_non_overlapping_cron_entry():
    """ATDD-R4-B2-controlled-paper-activation/AC-016 and AC-020: the capture runs from its own cron entry on the F3 60-second grid, with flock non-overlap and a timeout that is strictly below the minute so two passes can never overlap."""
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
    assert "app.jobs.capture_feature_bus_shadow" in line
    assert "flock -n /var/run/opip-feature-bus-capture.lock" in line
    # The R4-B2 cadence bridge requires the 60-second F3 evaluation grid, and the
    # containment timeout must sit strictly below that slot so passes cannot overlap.
    assert line.startswith("* * * * *")
    assert "timeout --signal=TERM --kill-after=5s 50" in line


def test_ac_016_scheduler_reconciliation_installs_capture_once():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the existing scheduler reconciliation installs the capture entry exactly once and can roll it back."""
    script = (
        APP_ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    assert script.count('install -o root -g root -m 0644 "$CAPTURE_SRC" "$CAPTURE_DST"') == 1
    assert "opip-feature-bus-capture" in script
    assert 'had_capture' in script

def test_ac_018_default_capture_composes_batch_restore_observer_without_marker_collision(monkeypatch, capsys):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: the production/default Feature Bus
    capture composition drives the real batch continuity restore observer into
    durable capture markers without colliding with emit_capture_marker's stage
    parameter.
    """
    import app.jobs.run_feature_bus_pilot as pilot

    versions = _instruments(1)
    version = versions[0]
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())

    def _states_batch(ids, **kwargs):
        assert ids == [version.instrument_version_id]
        return {}

    def _ledgers_batch(ids, **kwargs):
        assert ids == [version.instrument_version_id]
        return {}

    kwdefaults = pilot.restore_pilot_continuity_batch.__kwdefaults__
    assert kwdefaults is not None
    monkeypatch.setitem(kwdefaults, "load_states_batch", _states_batch)
    monkeypatch.setitem(kwdefaults, "load_ledgers_batch", _ledgers_batch)

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        now=NOW,
        publisher=publisher,
        instrument_provider=_provider(versions),
        source=_source(batches),
    )

    assert summary.budget_exhausted is False
    assert summary.cycles == 1
    assert len(client.snapshot_payloads()) == 1

    out = capsys.readouterr().out
    assert (
        "OPIP_FEATURE_BUS_CAPTURE_PHASE=continuity_phase "
        "continuity_stage=checkpoint_restore"
    ) in out
    assert (
        "OPIP_FEATURE_BUS_CAPTURE_PHASE=continuity_phase "
        "continuity_stage=revision_ledger_restore"
    ) in out
    assert (
        "OPIP_FEATURE_BUS_CAPTURE_PHASE=continuity_phase "
        "continuity_stage=continuity_restore_total"
    ) in out
    assert "OPIP_FEATURE_BUS_CAPTURE_PHASE=continuity_restored" in out

