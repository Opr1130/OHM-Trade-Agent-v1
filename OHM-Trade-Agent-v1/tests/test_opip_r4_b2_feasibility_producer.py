"""R4-B2 Slice 3A: bounded PROSPECTIVE SHADOW feasibility-evidence capture (AC-021).

Proves the producer is a prospective evidence collector: it advances a persisted
read cursor across terminal batches, cold-starts at the committed head (never
replaying history), refuses to stamp current market data onto a non-contemporaneous
snapshot, preserves negative (REJECT/INVALID) records as PRESENT evidence, holds the
cursor across retryable records, uses its OWN process-lock identity, and its
scheduled module entrypoint actually runs. Deterministic fixtures only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import app.jobs.capture_feasibility_evidence_shadow as producer  # noqa: E402
import app.jobs.capture_feature_bus_shadow as fb_capture  # noqa: E402
import app.opip.feasibility as feasibility  # noqa: E402
from app.exchanges.kraken import BookLevel, Candle, PreTradeBook  # noqa: E402
from app.opip import opportunity_lifecycle as lifecycle  # noqa: E402
from app.opip.contracts.detector import (  # noqa: E402
    IGNITION_DETECTOR_VERSION,
    IGNITION_POLICY_VERSION,
    DetectorClaim,
)
from app.opip.contracts.feasibility import (  # noqa: E402
    FeasibilityCheckStatus,
    FeasibilityDisposition,
    FeasibilityPolicy,
)
from app.opip.contracts.feasibility_evidence import FeasibilityEvidence  # noqa: E402
from app.opip.contracts.opportunity import OpportunityLifecyclePolicy  # noqa: E402
from app.opip.fev_evidence_event import (  # noqa: E402
    build_feasibility_evidence_recorded_payload,
)
from app.opip.features.committed_snapshot_reader import (  # noqa: E402
    CommittedSnapshotRecord,
)
from app.scanner.execution_validation import ExecutionValidation  # noqa: E402
from app.scanner.market_data_validation import MarketDataValidation  # noqa: E402

pytestmark = pytest.mark.acceptance

T = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
CUTOFF = T - timedelta(seconds=60)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
VENUE_INSTRUMENT_ID = "SOLUSD"


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
    """Per-record fake whose cursor is each row's own canonical position."""

    def __init__(self, snapshots, *, head: int | None = None):
        self._snapshots = list(snapshots)
        self._head = len(self._snapshots) if head is None else head

    def head_cursor(self):
        return None if self._head == 0 else (0, self._head)

    def read_records(self, *, after=None, limit=200):
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
def cursor_path(tmp_path):
    # Simulate an activation whose cold start already ran: the cursor exists at
    # the head boundary, so this pass processes records rather than cold-starting.
    path = tmp_path / "fev_cursor.json"
    producer._save_cursor(path, (0, 0))
    return path


@pytest.fixture
def unprimed_cursor_path(tmp_path):
    return tmp_path / "fev_cursor.json"


def _market(status="WARN", qualified=True) -> MarketDataValidation:
    return MarketDataValidation(
        status=status, qualified=qualified, warnings=[], rejection_reasons=[],
        candle_count=200, latest_candle_timestamp=1759400000,
        latest_candle_age_seconds=120.0, duplicate_timestamp_count=0, gap_count=0,
        largest_gap_seconds=0.0, invalid_ohlc_count=0, non_finite_value_count=0,
        ticker_last=100.0, latest_ohlc_close=100.0,
        ticker_vs_ohlc_difference_pct=0.0, suspicious_spike_detected=False,
    )


def _execution(status="VALID") -> ExecutionValidation:
    coverage = "COMPLETE" if status == "VALID" else "UNAVAILABLE"
    return ExecutionValidation(status=status, book_coverage_status=coverage, warnings=[])


def _evidence(snapshot, *, direction="LONG", market=None, execution=None) -> FeasibilityEvidence:
    return FeasibilityEvidence(
        instrument_version_id=snapshot.instrument_version_id,
        venue_instrument_id=snapshot.venue_instrument_id,
        direction=direction,
        evaluation_time=snapshot.evaluation_cutoff,
        source_cutoff=snapshot.evaluation_cutoff,
        source_snapshot_id=snapshot.snapshot_id,
        source_evidence_refs=(snapshot.snapshot_id,),
        market_data_validation=_market() if market is None else market,
        margin_validation_status=None, margin_eligible=None,
        margin_venue_symbol=None, margin_max_leverage=None,
        execution_validation=_execution() if execution is None else execution,
        availability="AVAILABLE", missingness=(),
        kraken_public_symbol=snapshot.venue_instrument_id,
        primary_pair=snapshot.venue_instrument_id,
    )


def _status_of(payload) -> str:
    return payload["evidence"]["evidence"]["source_snapshot_id"]


# ---------------------------------------------------------------------------
# AC-021 gate + basic behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bus", "writer", "inert"),
    [
        ("off", "off", True),
        ("shadow", "off", True),
        ("off", "shadow", True),
        ("active", "shadow", True),
        ("shadow", "shadow", False),
    ],
)
def test_ac_021_exact_shadow_gate_matrix(bus, writer, inert, cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: authorized only for exactly Feature Bus shadow AND writer shadow; active does not authorize it."""
    settings = _settings(opip_feature_bus_mode=bus, opip_canonical_writer_mode=writer)
    assert producer.feasibility_capture_authorized(settings) is (not inert)
    summary = producer.capture_feasibility_evidence_shadow(
        settings=settings, reader=_FakeReader([_Snapshot(1)]), cursor_path=cursor_path
    )
    assert summary.inert is inert
    if inert:
        assert summary.enabled is False


def test_ac_021_records_one_genuine_record_per_contemporaneous_snapshot(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a contemporaneous snapshot yields exactly one genuine feasibility.evidence.recorded payload."""
    snapshots = [_Snapshot(1), _Snapshot(2)]
    submitted = []
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: submitted.append(payload) or "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.records_built == 2
    assert summary.recorded == 2
    assert [_status_of(p) for p in submitted] == ["SNAP:1", "SNAP:2"]
    for payload, snapshot in zip(submitted, snapshots):
        assert payload == build_feasibility_evidence_recorded_payload(_evidence(snapshot))


# ---------------------------------------------------------------------------
# AC-021 prospective integrity: cold start, staleness, honest timestamps
# ---------------------------------------------------------------------------


def test_ac_021_cold_start_initializes_at_head_and_does_not_refetch_history(unprimed_cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a missing cursor cold-starts at the committed head and collects NOTHING, so historical snapshots are never market-refetched."""
    fetched = {"n": 0}

    def _builder(snapshot, direction):
        fetched["n"] += 1
        return _evidence(snapshot)

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1), _Snapshot(2), _Snapshot(3)]),
        evidence_builder=_builder,
        submit_payload=lambda payload: "OK",
        cursor_path=unprimed_cursor_path,
        now=T,
    )
    assert summary.cold_start is True
    assert summary.snapshots_seen == 0
    assert summary.records_built == 0
    assert fetched["n"] == 0
    # The cold-start boundary is deterministic AND persisted.
    assert producer._load_cursor(unprimed_cursor_path) == (0, 3)


def test_ac_021_delayed_snapshot_is_not_stamped_with_current_market(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a snapshot older than the frozen contemporaneous window is not fetched against; it is recorded stale and advanced."""
    old = _Snapshot(1, cutoff=T - timedelta(seconds=900), visible_at=T - timedelta(seconds=900))
    fetched = {"n": 0}

    def _builder(snapshot, direction):
        fetched["n"] += 1
        return _evidence(snapshot)

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([old]),
        evidence_builder=_builder,
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.stale == 1
    assert summary.records_built == 0
    assert fetched["n"] == 0  # no live fetch for a stale snapshot
    assert any("stale" in err for err in summary.errors)


def test_ac_021_out_of_epoch_source_cutoff_fails_closed(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: if the source cutoff would fall after the evaluation epoch the builder fails closed rather than backdating; the record is stale and advanced."""
    class _Builder:
        def __call__(self, snapshot, direction):
            raise producer.FeasibilityEvidenceStaleError("source after epoch")

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=_Builder(),
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.stale == 1
    assert summary.records_built == 0


def test_ac_021_builder_rejects_source_cutoff_after_epoch():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the real builder refuses to stamp a source candle that closes after the snapshot's evaluation epoch."""
    snapshot = _Snapshot(1, cutoff=T)
    # Candles whose latest COMPLETED candle closes at T+60 (one interval past the epoch).
    candles = _minute_candles(end_epoch=T + timedelta(seconds=60), count=6)
    client = _FakeClient(candles=candles)
    with pytest.raises(producer.FeasibilityEvidenceStaleError):
        producer.build_long_feasibility_evidence(
            snapshot, client=client, notional_usd=500.0, acquisition_instant=T,
            interval_minutes=1, interval_seconds=60,
        )


def test_ac_021_future_visible_snapshot_fails_closed_without_advancing(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a snapshot not yet observable at the acquisition instant produces no evidence and does not advance the cursor (retried later)."""
    future = _Snapshot(1, cutoff=T, visible_at=T + timedelta(seconds=30))
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([future]),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.records_built == 0
    assert summary.retryable == 1
    assert producer._load_cursor(cursor_path) is None or producer._load_cursor(cursor_path) == (0, 0)


def test_ac_021_real_builder_builds_genuine_contemporaneous_evidence():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the real builder produces genuine evidence with the snapshot's evaluation epoch and a truthful source cutoff at or before it."""
    snapshot = _Snapshot(1, cutoff=T)
    candles = _minute_candles(end_epoch=T, count=6)  # last completed candle closes at T
    client = _FakeClient(candles=candles)
    evidence = producer.build_long_feasibility_evidence(
        snapshot, client=client, notional_usd=500.0, acquisition_instant=T,
        interval_minutes=1, interval_seconds=60,
    )
    assert evidence.evaluation_time == T
    assert evidence.source_cutoff <= evidence.evaluation_time
    assert evidence.source_cutoff == T
    assert evidence.availability == "AVAILABLE"
    assert evidence.missingness == ()


# ---------------------------------------------------------------------------
# AC-021 analytical horizon vs freshness anchor (two-plane provenance)
# ---------------------------------------------------------------------------

#: The exact production F5 shape that failed: snapshot cutoff 12:51:00Z, F5
#: commit instant ~12:52:12Z, and an hourly-only analytical cutoff of 12:00:00Z
#: (source age 3132s) which the 120-second runtime source-age contract rejects.
LATE = datetime(2026, 10, 2, 12, 51, tzinfo=timezone.utc)
LATE_COMMIT = datetime(2026, 10, 2, 12, 52, 12, tzinfo=timezone.utc)
HOURLY_CUTOFF = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _two_plane_client(
    *, epoch: datetime, hourly_count: int = 8, minute_count: int = 6
) -> _FakeClient:
    """Distinct 60-minute analytical history AND a fresh closed 1-minute anchor."""
    return _FakeClient(
        candles=_hourly_candles(
            end_epoch=epoch.replace(minute=0, second=0, microsecond=0),
            count=hourly_count,
        ),
        candles_1m=_minute_candles(end_epoch=epoch, count=minute_count),
    )


def test_ac_021_analytical_horizon_and_fresh_anchor_are_separately_acquired():
    """ATDD-R4-B2-controlled-paper-activation/AC-021 and ATDD-RELEASE-PIPELINE-v1/AC-017: the 60-minute analytical series keeps its existing semantics while a SEPARATE fresh closed 1-minute observation of the same instrument anchors source_cutoff, and both planes are recorded in provenance."""
    snapshot = _Snapshot(1, cutoff=LATE)
    client = _two_plane_client(epoch=LATE)
    evidence = producer.build_long_feasibility_evidence(
        snapshot, client=client, notional_usd=500.0, acquisition_instant=LATE_COMMIT,
    )

    # Two SEPARATE reads: the analytical horizon first, the freshness anchor second.
    assert client.intervals == [60, 1]
    refs = evidence.source_evidence_refs
    assert (
        "analytical:kraken_public_ohlc:interval_seconds=3600:bars=7"
        ":latest_close=2026-10-02T12:00:00Z"
    ) in refs
    assert (
        "freshness:kraken_public_ohlc:interval_seconds=60"
        ":bar_open=2026-10-02T12:50:00Z:bar_close=2026-10-02T12:51:00Z"
    ) in refs
    assert producer.anchor_source_age_seconds(refs) == 72.0

    # source_cutoff is the freshest datum that ACTUALLY supports this epoch: not
    # the hourly close, and never the acquisition instant (no fabricated freshness).
    assert evidence.source_cutoff == LATE
    assert evidence.source_cutoff != HOURLY_CUTOFF
    assert evidence.source_cutoff != LATE_COMMIT
    assert evidence.source_cutoff == evidence.evaluation_time == snapshot.evaluation_cutoff


@pytest.mark.parametrize("minute", [1, 17, 43, 58, 59])
def test_ac_021_freshness_anchor_holds_anywhere_within_the_hour(minute):
    """ATDD-R4-B2-controlled-paper-activation/AC-021 and ATDD-RELEASE-PIPELINE-v1/AC-017: the freshness anchor satisfies the runtime source-age window regardless of where the minute falls inside the hour, while the analytical cutoff stays at the top of the hour."""
    epoch = datetime(2026, 10, 2, 12, minute, tzinfo=timezone.utc)
    commit = epoch + timedelta(seconds=12)
    evidence = producer.build_long_feasibility_evidence(
        _Snapshot(1, cutoff=epoch),
        client=_two_plane_client(epoch=epoch),
        notional_usd=500.0,
        acquisition_instant=commit,
    )
    assert evidence.source_cutoff == epoch
    assert (commit - evidence.source_cutoff).total_seconds() <= 120
    assert producer.anchor_source_age_seconds(evidence.source_evidence_refs) == 12.0
    assert any(
        "latest_close=2026-10-02T12:00:00Z" in ref for ref in evidence.source_evidence_refs
    )


def test_ac_021_hourly_cutoff_cannot_satisfy_the_verifier_but_the_fresh_anchor_does():
    """ATDD-R4-B2-controlled-paper-activation/AC-021 and ATDD-RELEASE-PIPELINE-v1/AC-017: the OLD hourly-anchored source_cutoff fails the UNCHANGED runtime verifier at the production timings, and only the corrected freshness-anchor semantics pass it."""
    from dataclasses import replace

    from app.services.release_runtime_verifier import (
        MAX_FEV_SOURCE_AGE,
        _new_evidence_is_valid,
    )

    # The fixed reference is untouched by this increment.
    assert MAX_FEV_SOURCE_AGE == timedelta(seconds=120)
    assert producer.MAX_FRESH_ANCHOR_AGE_SECONDS == MAX_FEV_SOURCE_AGE.total_seconds()

    older = _real_feature_snapshot(1, cutoff=LATE - timedelta(seconds=60))
    newer = _real_feature_snapshot(1, cutoff=LATE)
    ready_after = older.evaluation_cutoff - timedelta(seconds=1)
    fresh = producer.build_long_feasibility_evidence(
        newer,
        client=_two_plane_client(epoch=LATE),
        notional_usd=500.0,
        acquisition_instant=LATE_COMMIT,
    )

    # The production failure, exactly: consecutive fresh snapshots + matching
    # lineage, but an HH:00 hourly source_cutoff read 72s after the commit instant.
    hourly_anchored = replace(
        fresh, source_cutoff=HOURLY_CUTOFF, evidence_fingerprint=""
    )
    assert (LATE_COMMIT - HOURLY_CUTOFF).total_seconds() == 3132.0
    passed_old, report_old = _new_evidence_is_valid(
        [older, newer], [hourly_anchored], ready_after=ready_after, now=LATE_COMMIT,
    )
    assert passed_old is False
    assert report_old["consecutive_60s_snapshots"] is True
    assert report_old["feasibility_matches_fresh_snapshot"] is False

    passed_new, report_new = _new_evidence_is_valid(
        [older, newer], [fresh], ready_after=ready_after, now=LATE_COMMIT,
    )
    assert passed_new is True
    assert report_new["consecutive_60s_snapshots"] is True
    assert report_new["feasibility_matches_fresh_snapshot"] is True


def test_ac_021_stale_freshness_anchor_fails_closed_without_synthetic_freshness():
    """ATDD-R4-B2-controlled-paper-activation/AC-021 and ATDD-RELEASE-PIPELINE-v1/AC-017: a window with no candle closing at this epoch fails closed (never backdated, never stamped with the acquisition instant), and an empty anchor read is retryable rather than stale."""
    snapshot = _Snapshot(1, cutoff=LATE)
    hourly = _hourly_candles(end_epoch=HOURLY_CUTOFF, count=8)

    # No closed 1-minute candle closes at the evaluation epoch: non-retryable.
    stale = _FakeClient(
        candles=hourly,
        candles_1m=_minute_candles(end_epoch=LATE - timedelta(seconds=60), count=6),
    )
    with pytest.raises(producer.FeasibilityEvidenceStaleError) as stale_exc:
        producer.build_long_feasibility_evidence(
            snapshot, client=stale, notional_usd=500.0, acquisition_instant=LATE_COMMIT,
        )
    assert "no closed 1m candle" in str(stale_exc.value)

    # An empty anchor read is a transient failure: retryable, and still no
    # manufactured timestamp.
    with pytest.raises(producer.FeasibilityCaptureError) as empty_exc:
        producer.build_long_feasibility_evidence(
            snapshot,
            client=_FakeClient(candles=hourly, candles_1m=[]),
            notional_usd=500.0,
            acquisition_instant=LATE_COMMIT,
        )
    assert type(empty_exc.value) is producer.FeasibilityCaptureError


def test_ac_021_anchor_beyond_the_max_source_age_fails_closed():
    """ATDD-R4-B2-controlled-paper-activation/AC-021 and ATDD-RELEASE-PIPELINE-v1/AC-017: an anchor that would already exceed the runtime source-age window at acquisition is refused rather than reported as fresh."""
    snapshot = _Snapshot(1, cutoff=LATE)
    client = _two_plane_client(epoch=LATE)
    with pytest.raises(producer.FeasibilityEvidenceStaleError) as excinfo:
        producer.build_long_feasibility_evidence(
            snapshot,
            client=client,
            notional_usd=500.0,
            acquisition_instant=LATE + timedelta(seconds=121),
        )
    assert "source-age contract" in str(excinfo.value)


def test_ac_021_evidence_lineage_points_to_the_exact_source_snapshot():
    """ATDD-R4-B2-controlled-paper-activation/AC-021 and ATDD-RELEASE-PIPELINE-v1/AC-017: the freshness anchor changes freshness ONLY -- lineage still names exactly the originating FeatureSnapshot and its own evaluation epoch."""
    snapshot = _Snapshot(3, cutoff=LATE)
    evidence = producer.build_long_feasibility_evidence(
        snapshot,
        client=_two_plane_client(epoch=LATE),
        notional_usd=500.0,
        acquisition_instant=LATE_COMMIT,
    )
    assert evidence.source_snapshot_id == snapshot.snapshot_id
    assert evidence.source_evidence_refs[0] == snapshot.snapshot_id
    assert evidence.evaluation_time == snapshot.evaluation_cutoff
    assert evidence.instrument_version_id == snapshot.instrument_version_id
    assert evidence.source_cutoff <= evidence.evaluation_time


def test_ac_021_freshness_dispositions_are_flushed_durable_markers(
    monkeypatch, cursor_path
):
    """ATDD-R4-B2-controlled-paper-activation/AC-021 and ATDD-RELEASE-PIPELINE-v1/AC-017: a stale freshness anchor and a timed-out market read each leave a FLUSHED, machine-readable disposition naming the reason, so a bound-killed pass is never a silent evidence drop."""
    import httpx

    lines: list[str] = []

    def _fake_print(*args, **kwargs):
        assert kwargs.get("flush") is True, (
            "durable feasibility markers must be flushed; a buffered line is lost "
            "when the outer containment kills the producer"
        )
        lines.append(" ".join(str(arg) for arg in args))

    # The markers are emitted by the SHARED emitter (its ``print`` is resolved in
    # the feature-bus module), so that is the reference to intercept.
    monkeypatch.setattr(fb_capture, "print", _fake_print, raising=False)

    def _stale_builder(snapshot, direction):
        raise producer.FeasibilityEvidenceStaleError(
            "no closed 1m candle closes at the evaluation epoch"
        )

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        cursor_path=cursor_path,
        now=T,
        evidence_builder=_stale_builder,
        submit_payload=lambda payload: "OK",
    )
    assert summary.stale == 1
    assert lines[0].startswith("OPIP_FEASIBILITY_CAPTURE_PHASE=start")
    stale = [line for line in lines if "PHASE=stale" in line]
    assert len(stale) == 1
    assert "snapshot=SNAP:1" in stale[0]
    assert "reason=FRESHNESS_ANCHOR" in stale[0]
    done = [line for line in lines if line.startswith("OPIP_FEASIBILITY_CAPTURE_PHASE=done")]
    assert len(done) == 1
    assert "stale=1" in done[0]

    # A timed-out market read is classified identically to the Feature Bus
    # producer's failure disposition, and the cursor does NOT advance.
    lines.clear()
    producer._save_cursor(cursor_path, (0, 0))

    def _timeout_builder(snapshot, direction):
        raise httpx.ReadTimeout("stalled public read")

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        cursor_path=cursor_path,
        now=T,
        evidence_builder=_timeout_builder,
        submit_payload=lambda payload: "OK",
    )
    assert summary.retryable == 1
    retryable = [line for line in lines if "PHASE=retryable" in line]
    assert len(retryable) == 1
    assert "reason=REQUEST_TIMEOUT" in retryable[0]
    assert producer._load_cursor(cursor_path) == (0, 0)


# ---------------------------------------------------------------------------
# AC-021 negative evidence stays PRESENT
# ---------------------------------------------------------------------------


def test_ac_021_market_reject_stays_present_and_f5_vetoes():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a present REJECT market record is durable present evidence and F5 returns its existing hard VETO (never missingness)."""
    snapshot = _Snapshot(1)
    evidence = _evidence(snapshot, market=_market(status="REJECT", qualified=False))
    payload = build_feasibility_evidence_recorded_payload(evidence)
    body = payload["evidence"]["evidence"]
    assert body["market_data_validation"] is not None
    assert body["market_data_validation"]["status"] == "REJECT"
    assert body["missingness"] == []
    assert body["availability"] == "AVAILABLE"
    assert feasibility._market_check(evidence).status is FeasibilityCheckStatus.VETO


def test_ac_021_execution_invalid_stays_present_and_f5_vetoes():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a present INVALID execution record is durable present evidence and F5 returns its existing hard VETO."""
    snapshot = _Snapshot(1)
    evidence = _evidence(snapshot, execution=_execution(status="INVALID"))
    payload = build_feasibility_evidence_recorded_payload(evidence)
    body = payload["evidence"]["evidence"]
    assert body["execution_validation"] is not None
    assert body["execution_validation"]["status"] == "INVALID"
    assert body["missingness"] == []
    assert feasibility._execution_check(evidence, T).status is FeasibilityCheckStatus.VETO


def test_ac_021_unavailable_evidence_is_insufficient_not_veto():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: genuinely absent evidence is INSUFFICIENT_EVIDENCE (abstention), not VETO."""
    assert (
        feasibility._market_check(SimpleNamespace(market_data_validation=None)).status
        is FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE
    )
    assert (
        feasibility._execution_check(
            SimpleNamespace(execution_validation=None), T
        ).status
        is FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE
    )


def test_ac_021_present_veto_reaches_full_f5_decision():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the full F5 seam turns a present REJECT market record into a VETO disposition."""
    evidence = _evidence(_Snapshot(1), market=_market(status="REJECT", qualified=False))
    claim = DetectorClaim.create(
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id=VENUE_INSTRUMENT_ID,
        snapshot_id="SNAP:r4b2-fev",
        detector_input_fingerprint="DETIN:r4b2-fev",
        evaluation_cutoff=T,
    )
    episode = lifecycle.apply_claim(
        claim, None, claim.evaluation_cutoff, OpportunityLifecyclePolicy()
    ).episode
    decision = feasibility.evaluate_feasibility(episode, evidence, T, FeasibilityPolicy())
    assert decision.disposition is FeasibilityDisposition.VETO


# ---------------------------------------------------------------------------
# AC-021 cursor: terminal vs retryable
# ---------------------------------------------------------------------------


def test_ac_021_cursor_advances_across_passes_and_does_not_reread(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the persisted per-row cursor advances, so a later pass reads NEW snapshots rather than the oldest batch."""
    snapshots = [_Snapshot(i) for i in range(1, 5)]
    # ticks: start(0), read-gate(0), row gates(0,0) process both, next read-gate(1000) expires.
    ticks = iter([0.0, 0.0, 0.0, 0.0, 1000.0])

    def _clock():
        try:
            return next(ticks)
        except StopIteration:
            return 1000.0

    submitted_1 = []
    summary_1 = producer.capture_feasibility_evidence_shadow(
        settings=_settings(opip_feasibility_capture_limit=2),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: submitted_1.append(payload) or "OK",
        cursor_path=cursor_path,
        now=T,
        clock=_clock,
    )
    assert summary_1.budget_exhausted is True
    assert [_status_of(p) for p in submitted_1] == ["SNAP:1", "SNAP:2"]
    assert producer._load_cursor(cursor_path) == (0, 2)
    submitted_2 = []
    producer.capture_feasibility_evidence_shadow(
        settings=_settings(opip_feasibility_capture_limit=2),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: submitted_2.append(payload) or "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert [_status_of(p) for p in submitted_2] == ["SNAP:3", "SNAP:4"]


def test_ac_021_retryable_record_halts_cursor_and_is_retried_next_pass(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a RETRYABLE row halts the pass; the preceding terminal row's cursor is persisted (so it is not rebuilt), and the retryable row and later rows are available next pass."""
    snapshots = [_Snapshot(1), _Snapshot(2), _Snapshot(3)]
    attempts = {"count": 0}

    def _submit(payload):
        sid = _status_of(payload)
        if sid == "SNAP:2":
            attempts["count"] += 1
            return "OK" if attempts["count"] > 1 else "RETRYABLE"
        return "OK"

    pass_1 = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=_submit,
        cursor_path=cursor_path,
        now=T,
    )
    assert pass_1.recorded == 1  # SNAP:1 only
    assert pass_1.retryable == 1  # SNAP:2 halted the pass
    # The cursor stops exactly AT the retryable row's predecessor, so SNAP:1 is not
    # rebuilt next pass and SNAP:2 (retryable) is re-read.
    assert producer._load_cursor(cursor_path) == (0, 1)

    seen_2 = []
    pass_2 = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: seen_2.append(_status_of(payload)) or "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert "SNAP:2" in seen_2 and "SNAP:3" in seen_2
    assert "SNAP:1" not in seen_2  # first row was NOT rebuilt
    assert pass_2.recorded == 2


def test_ac_021_transient_builder_exception_does_not_advance_cursor(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a transient evidence-assembly exception halts advancement so the record is retried, never skipped."""
    def _builder(snapshot, direction):
        raise RuntimeError("transient kraken failure")

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1), _Snapshot(2)]),
        evidence_builder=_builder,
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.retryable == 1
    assert summary.recorded == 0
    assert producer._load_cursor(cursor_path) == (0, 0)


def test_ac_021_all_rejected_batch_advances_and_persists_cursor(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a fully-rejected batch (all terminal) advances AND persists the cursor, so it is not re-read every pass."""
    def _builder(snapshot, direction):
        return object()  # never evidence -> deterministic terminal rejection

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1), _Snapshot(2)]),
        evidence_builder=_builder,
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.rejected == 2
    assert summary.records_built == 0
    assert producer._load_cursor(cursor_path) == (0, 2)


def test_ac_021_canonical_rejected_is_retryable_not_terminal(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a canonical REJECTED/ambiguous status is retryable (halts advancement), never a silent terminal skip."""
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: "REJECTED",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.retryable == 1
    assert summary.rejected == 0
    assert producer._load_cursor(cursor_path) == (0, 0)


class _RejectingReader:
    """Reader whose first read yields malformed rows then valid snapshots; then empty.

    Each malformed row carries its OWN cursor so the producer must advance to that
    row's position, not merely the batch tail.
    """

    def __init__(self, *, rejected, snapshots=()):
        self._rejected = rejected
        self._snapshots = list(snapshots)
        self._calls = 0

    def head_cursor(self):
        return (0, 0)

    def read_records(self, *, after=None, limit=200):
        self._calls += 1
        if self._calls > 1:
            return (), after
        records = []
        seq = 0
        for i in range(self._rejected):
            seq += 1
            records.append(
                CommittedSnapshotRecord(
                    event_id=f"BAD:{i}",
                    history_epoch=0,
                    local_sequence=seq,
                    snapshot=None,
                    rejected=True,
                    reject_reason=f"bad row {i}",
                )
            )
        for j, snapshot in enumerate(self._snapshots):
            seq += 1
            records.append(
                CommittedSnapshotRecord(
                    event_id=f"EV:{j + 1}",
                    history_epoch=0,
                    local_sequence=seq,
                    snapshot=snapshot,
                    rejected=False,
                )
            )
        tail = (0, seq) if records else None
        return tuple(records), tail

    def close(self):
        pass


def test_ac_021_all_reader_rejected_batch_advances_and_surfaces(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a batch of malformed committed rows is terminal (deterministically invalid), counted and surfaced, and the cursor advances through all of them — no livelock and no silent skip."""
    reader = _RejectingReader(rejected=7)
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=reader,
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.rejected_by_reader == 7
    assert summary.snapshots_seen == 0
    assert any("reader rejected" in err for err in summary.errors)
    # No livelock: the cursor advanced past every malformed row.
    assert producer._load_cursor(cursor_path) == (0, 7)


def test_ac_021_malformed_then_valid_row_processes_valid_and_advances(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a malformed row is terminal, advancing to its OWN cursor, and a following valid row still processes."""
    reader = _RejectingReader(rejected=1, snapshots=[_Snapshot(1)])
    submitted = []
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=reader,
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: submitted.append(_status_of(payload)) or "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.rejected_by_reader == 1
    assert summary.recorded == 1
    assert submitted == ["SNAP:1"]
    assert producer._load_cursor(cursor_path) == (0, 2)


def test_ac_021_scheduler_comment_names_the_feasibility_lock():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the scheduler entry documents the feasibility producer's OWN lock env, not the Feature Bus lock."""
    entry = (
        APP_ROOT / "deploy" / "cron.d" / "opip-feasibility-evidence-capture"
    ).read_text(encoding="utf-8")
    assert "OPIP_FEASIBILITY_CAPTURE_LOCK" in entry
    assert "OPIP_FEATURE_BUS_CAPTURE_LOCK" not in entry


def test_ac_021_deterministic_rejection_is_terminal_and_advances(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a deterministic producer rejection (non-evidence) is terminal and advances the cursor."""
    def _builder(snapshot, direction):
        return object()  # never a FeasibilityEvidence; deterministic

    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=_builder,
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.rejected == 1
    assert producer._load_cursor(cursor_path) == (0, 1)


def test_ac_021_exact_replay_after_restart_produces_no_duplicate_and_advances(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: after a completed pass the cursor prevents re-processing; a restart with no new records produces no duplicate evidence."""
    snapshots = [_Snapshot(1)]
    first = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert first.recorded == 1
    # "Restart": fresh reader, same persisted cursor -> nothing new to process.
    second = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert second.records_built == 0
    assert second.snapshots_seen == 0


def test_ac_021_duplicate_replay_is_counted_not_rejected(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a DUPLICATE_OK replay is counted as a duplicate (terminal), not a rejection."""
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: "DUPLICATE_OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.duplicate == 1
    assert summary.recorded == 0
    assert summary.rejected == 0


def test_ac_021_unsupported_direction_rejected(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: a direction outside {LONG, SHORT} is rejected, never recorded."""
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        direction_for=lambda snapshot: "SIDEWAYS",
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.rejected == 1
    assert summary.recorded == 0


def test_ac_021_direction_mismatch_rejected(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: evidence whose direction does not match the request is rejected (SHORT can never be satisfied by LONG/spot)."""
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),  # always LONG
        direction_for=lambda snapshot: "SHORT",
        submit_payload=lambda payload: "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.rejected == 1
    assert summary.recorded == 0


def test_ac_021_missing_builder_records_unavailable_and_does_not_advance(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: with no genuine builder the pass records unavailable and does NOT advance (nothing is fabricated or dropped)."""
    submitted = []
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(),
        reader=_FakeReader([_Snapshot(1)]),
        submit_payload=lambda payload: submitted.append(payload) or "OK",
        cursor_path=cursor_path,
        now=T,
    )
    assert summary.unavailable == 1
    assert summary.records_built == 0
    assert submitted == []
    assert producer._load_cursor(cursor_path) == (0, 0)


def test_ac_021_budget_expires_after_first_row_persists_first_only(cursor_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: when the budget expires after the first terminal row, only that row's cursor is persisted and the rest are left for the next pass."""
    snapshots = [_Snapshot(i) for i in range(1, 5)]
    # ticks: start(0), read-gate(0), row-1 gate(0) -> process; row-2 gate(1000) -> stop.
    ticks = iter([0.0, 0.0, 0.0, 1000.0])

    def _clock():
        try:
            return next(ticks)
        except StopIteration:
            return 1000.0

    submitted = []
    summary = producer.capture_feasibility_evidence_shadow(
        settings=_settings(opip_feasibility_capture_limit=2),
        reader=_FakeReader(snapshots),
        evidence_builder=lambda snapshot, direction: _evidence(snapshot),
        submit_payload=lambda payload: submitted.append(payload) or "OK",
        cursor_path=cursor_path,
        now=T,
        clock=_clock,
    )
    assert summary.budget_exhausted is True
    assert summary.records_built == 1
    assert producer._load_cursor(cursor_path) == (0, 1)
    assert len(submitted) == 1


# ---------------------------------------------------------------------------
# AC-021 lock identity independence
# ---------------------------------------------------------------------------


def test_ac_021_locks_are_structurally_distinct():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the feasibility producer has its own process-lock identity, distinct from the Feature Bus capture."""
    assert producer.FEASIBILITY_CAPTURE_LOCK_PATH != fb_capture.DEFAULT_PROCESS_LOCK_PATH
    assert producer.FEASIBILITY_CAPTURE_LOCK_ENV != "OPIP_FEATURE_BUS_CAPTURE_LOCK"


def test_ac_021_feature_bus_lock_does_not_block_feasibility(tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: while the Feature Bus process lock is held, the feasibility producer still acquires its own; and a second feasibility invocation is refused."""
    fb_lock = tmp_path / "fb.lock"
    fev_lock = tmp_path / "fev.lock"
    fb_holder = fb_capture.CaptureProcessLock(str(fb_lock))
    assert fb_holder.acquire() is True
    try:
        # Feature Bus lock held -> feasibility can still acquire its OWN lock.
        fev_holder = fb_capture.CaptureProcessLock(str(fev_lock))
        assert fev_holder.acquire() is True
        # Feasibility lock held -> a second feasibility invocation is refused.
        assert fb_capture.CaptureProcessLock(str(fev_lock)).acquire() is False
        fev_holder.release()
    finally:
        fb_holder.release()


def test_ac_021_run_capture_locked_uses_own_lock_identity(monkeypatch, tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: run_capture_locked with the feasibility lock identity runs even while the Feature Bus lock is held; its own lock suppresses a second feasibility run."""
    fb_lock = str(tmp_path / "fb.lock")
    fev_lock = str(tmp_path / "fev.lock")
    monkeypatch.setenv(producer.FEASIBILITY_CAPTURE_LOCK_ENV, fev_lock)
    monkeypatch.setenv("OPIP_FEATURE_BUS_CAPTURE_LOCK", fb_lock)
    fb_holder = fb_capture.CaptureProcessLock(fb_lock)
    assert fb_holder.acquire() is True
    try:
        ran = {"n": 0}

        def _cap():
            ran["n"] += 1
            return SimpleNamespace(to_dict=lambda: {"ok": True})

        result = fb_capture.run_capture_locked(
            lock_env=producer.FEASIBILITY_CAPTURE_LOCK_ENV,
            lock_default=producer.FEASIBILITY_CAPTURE_LOCK_PATH,
            capture_fn=_cap,
        )
        assert result["status"] == "RAN"
        assert ran["n"] == 1

        # With the feasibility lock held, a second feasibility run is refused.
        fev_holder = fb_capture.CaptureProcessLock(fev_lock)
        assert fev_holder.acquire() is True
        refused = fb_capture.run_capture_locked(
            lock_env=producer.FEASIBILITY_CAPTURE_LOCK_ENV,
            lock_default=producer.FEASIBILITY_CAPTURE_LOCK_PATH,
            capture_fn=_cap,
        )
        assert refused["status"] == "SKIPPED_LOCK_HELD"
        fev_holder.release()
    finally:
        fb_holder.release()


# ---------------------------------------------------------------------------
# AC-021 module entrypoint + scheduler
# ---------------------------------------------------------------------------


def test_ac_021_reader_seam_exposes_per_record_provenance(tmp_path):
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the committed-snapshot read seam returns one ordered record per committed row, each carrying its own event_id and canonical (history_epoch, local_sequence) cursor, and a malformed row becomes a rejected record at its own cursor."""
    import sqlite3

    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.features.committed_snapshot_reader import CommittedSnapshotReader

    db = tmp_path / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    writer = CanonicalWriter(db)
    # Submit two real FeatureSnapshot intents through the single canonical writer.
    snapshots = []
    for n in (1, 2):
        snapshot = _real_feature_snapshot(n)
        writer.submit(_feature_snapshot_intent(snapshot))
        snapshots.append(snapshot)
    writer.close()

    reader = CommittedSnapshotReader(db_path=db)
    try:
        records, tail = reader.read_records()
    finally:
        reader.close()
    assert len(records) == 2
    assert all(not r.rejected and r.snapshot is not None for r in records)
    assert [r.snapshot.snapshot_id for r in records] == [
        s.snapshot_id for s in snapshots
    ]
    # Each record carries its OWN canonical provenance (distinct event_id + cursor).
    assert len({r.event_id for r in records}) == 2
    assert all(r.event_id for r in records)
    assert [r.cursor for r in records] == sorted(r.cursor for r in records)
    assert tail == records[-1].cursor

    # Corrupt the first row's payload: it becomes a rejected record at ITS OWN cursor.
    connection = sqlite3.connect(str(db))
    try:
        first_event = records[0].event_id
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            (json.dumps({"record_type": "not_a_snapshot"}), first_event),
        )
        connection.commit()
    finally:
        connection.close()
    reader = CommittedSnapshotReader(db_path=db)
    try:
        corrupted, _ = reader.read_records()
    finally:
        reader.close()
    assert corrupted[0].rejected is True
    assert corrupted[0].event_id == records[0].event_id
    assert corrupted[0].cursor == records[0].cursor
    assert corrupted[0].snapshot is None
    assert corrupted[0].reject_reason


def test_ac_021_module_entrypoint_runs_and_reports_inert():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the scheduled module entrypoint actually runs (`python -m ...`) and emits a machine-readable inert result in an unauthorized configuration."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(APP_ROOT)
    env["OPIP_FEATURE_BUS_MODE"] = "off"
    env["OPIP_CANONICAL_WRITER_MODE"] = "off"
    env.setdefault("WEBHOOK_SECRET", "r4b2-entrypoint-secret")
    completed = subprocess.run(
        [sys.executable, "-m", "app.jobs.capture_feasibility_evidence_shadow"],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["status"] == "INERT"


def test_ac_021_no_protected_cycle_dependency():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the protected unified cycle neither imports nor invokes the feasibility capture."""
    cycle = (APP_ROOT / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "capture_feasibility_evidence_shadow" not in cycle
    assert "feasibility.evidence" not in cycle


def test_ac_021_configured_notional_is_required_and_bounded():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the shadow validation notional is a configured field (never live equity); limit/budget are bounded."""
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["opip_feasibility_capture_notional_usd"].default == 0.0
    assert fields["opip_feasibility_capture_limit"].default <= 32
    assert fields["opip_feasibility_capture_budget_seconds"].default <= 50


def test_ac_021_has_a_bounded_non_overlapping_scheduler_entry():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the producer runs from its own bounded scheduler entry with an in-container timeout below the minute and flock non-overlap."""
    entry = (
        APP_ROOT / "deploy" / "cron.d" / "opip-feasibility-evidence-capture"
    ).read_text(encoding="utf-8")
    import re as _re

    env_assignment = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    command = [
        line.strip()
        for line in entry.splitlines()
        if line.strip() and not line.strip().startswith("#") and not env_assignment.match(line.strip())
    ]
    assert len(command) == 1, command
    line = command[0]
    assert line.startswith("* * * * *")
    assert "flock -n /var/run/opip-feasibility-capture.lock" in line
    assert (
        "docker compose exec -T ohm-trade-agent "
        "timeout --signal=TERM --kill-after=5s 50 "
        "python -m app.jobs.capture_feasibility_evidence_shadow"
    ) in line
    assert "timeout " not in line.split("docker compose exec")[0]


def test_ac_021_scheduler_reconciliation_installs_capture_once_and_can_roll_back():
    """ATDD-R4-B2-controlled-paper-activation/AC-021: the scheduler reconciliation installs the feasibility-capture entry exactly once and can roll it back."""
    script = (
        APP_ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    assert (
        script.count(
            'install -o root -g root -m 0644 "$FEV_CAPTURE_SRC" "$FEV_CAPTURE_DST"'
        )
        == 1
    )
    assert "opip-feasibility-evidence-capture" in script
    assert "had_fev_capture" in script


# ---------------------------------------------------------------------------
# Helpers for the real builder
# ---------------------------------------------------------------------------


def _real_feature_snapshot(n: int, *, cutoff: datetime = T):
    from app.jobs.run_feature_bus_pilot import _synthetic_observations
    from app.opip.contracts.identity import InstrumentVersion
    from app.opip.features.pipeline import run_cycle
    from app.opip.features.state import initial_state

    version = InstrumentVersion(
        venue="synthetic",
        base_asset=f"SYN{n}",
        quote_currency="USD",
        venue_instrument_id=f"SYNTHETIC-SYN{n}USD",
        version=1,
        reference_data_version="opip-evidence-identity-v1",
        observed_at_utc=cutoff,
        price_decimals=2,
        tick_size=0.01,
        min_order_size=0.2,
    )
    observations = _synthetic_observations(
        version, cutoff=cutoff, intervals=140, now=cutoff
    )
    return run_cycle(
        observations,
        instrument_version=version,
        evaluation_cutoff=cutoff,
        evaluated_at_utc=cutoff,
        state=initial_state(version),
        source_version="r4b2-provenance-fixture",
    ).snapshot


def _feature_snapshot_intent(snapshot):
    from app.opip.features.publisher import snapshot_intent

    return snapshot_intent(snapshot)


def _minute_candles(*, end_epoch: datetime, count: int) -> list[Candle]:
    candles = []
    for i in range(count):
        ts = int(end_epoch.timestamp()) - (count - 1 - i) * 60
        candles.append(
            Candle(
                timestamp=ts, open=100.0, high=101.0, low=99.0, close=100.0,
                vwap=100.0, volume=10.0, trade_count=1,
            )
        )
    return candles


def _hourly_candles(*, end_epoch: datetime, count: int) -> list[Candle]:
    """A 60-minute analytical series whose last row is still forming at ``end_epoch``.

    Kraken returns the forming interval last, and the producer drops it, so the
    latest COMPLETED hourly close is ``end_epoch`` itself.
    """
    candles = []
    for i in range(count):
        ts = int(end_epoch.timestamp()) - (count - 1 - i) * 3600
        candles.append(
            Candle(
                timestamp=ts, open=100.0, high=101.0, low=99.0, close=100.0,
                vwap=100.0, volume=10.0, trade_count=1,
            )
        )
    return candles


class _FakeClient:
    def __init__(self, *, candles, candles_1m=None, intervals=None):
        self._candles = list(candles)
        self._candles_1m = (
            list(candles) if candles_1m is None else list(candles_1m)
        )
        #: Records every requested interval so a test can prove the analytical
        #: horizon and the freshness anchor are SEPARATELY acquired reads.
        self.intervals = intervals if intervals is not None else []

    def get_ohlc(self, pair, interval=60, since=None):
        self.intervals.append(interval)
        if int(interval) == 1:
            return list(self._candles_1m)
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
