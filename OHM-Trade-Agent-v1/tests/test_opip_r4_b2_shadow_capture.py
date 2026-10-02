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


def test_ac_016_configured_budget_is_read_from_settings():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the configured budget is read from Settings. A clock jump that would exhaust the default 180s budget but not the configured larger budget proves the configured value was used."""
    versions = _instruments(3)
    batches = {
        version.instrument_version_id: _batch(version, _observations(version))
        for version in versions
    }
    client = _RecordingClient()
    publisher = FeatureBusPublisher(client, enabled=True, settings=_settings())
    # start tick = 0; the next tick (200s) is past the 180s default but within a
    # configured 220s budget, so only a real settings read can avoid exhaustion.
    ticks = iter([0.0, 200.0])

    def _clock():
        try:
            return next(ticks)
        except StopIteration:
            return 200.0

    summary = capture.capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=220),
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


def test_ac_016_unified_cycle_does_not_run_capture():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the protected unified cycle neither imports nor invokes the Feature Bus capture."""
    cycle = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "capture_feature_bus_shadow" not in cycle
    assert "feature_bus" not in cycle.lower()


def test_ac_016_capture_has_a_bounded_non_overlapping_cron_entry():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the capture runs from its own cron entry with flock non-overlap and a bounded timeout, on the one scheduler."""
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
    assert "timeout --signal=TERM --kill-after=20s 240" in line
    # Cadence must not be every minute: it is a separate, bounded pass.
    assert not line.startswith("* * * * *")


def test_ac_016_scheduler_reconciliation_installs_capture_once():
    """ATDD-R4-B2-controlled-paper-activation/AC-016: the existing scheduler reconciliation installs the capture entry exactly once and can roll it back."""
    script = (
        APP_ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    assert script.count('install -o root -g root -m 0644 "$CAPTURE_SRC" "$CAPTURE_DST"') == 1
    assert "opip-feature-bus-capture" in script
    assert 'had_capture' in script
