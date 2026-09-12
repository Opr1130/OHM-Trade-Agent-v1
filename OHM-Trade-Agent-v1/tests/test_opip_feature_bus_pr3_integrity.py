"""Adversarial integrity regressions for PR3 feature-bus review findings."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.enums import CoverageState, Missingness, RestartState
from app.opip.contracts.events import snapshot_idempotency_key
from app.opip.contracts.features import FeatureSnapshot
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.temporal import AvailabilityStamp
from app.opip.features.engine import (
    FEATURE_WINDOW_INTERVALS,
    MINIMUM_WARMUP_INTERVALS,
    NOT_RETAINED_INPUTS,
    build_feature_snapshot,
    compute_features,
    feature_dag_hash,
)
from app.opip.features.pipeline import (
    DISPOSITION_DEFERRED_UNCOMMITTED,
    DISPOSITION_DRY_RUN,
    run_cycle,
)
from app.opip.features.publisher import FeatureBusPublisher, PublishOutcome
from app.opip.features.state import (
    advance_state,
    from_checkpoint,
    initial_state,
    to_checkpoint,
)
from app.opip.market.aggregates import align_minute_observations
from app.opip.market.instrument_version_store import (
    instrument_version_record_payload,
    reconstruct_instrument_version_registry,
)
from app.opip.market.instruments import InstrumentVersionRegistry, VenueInstrumentDescriptor
from app.opip.market.observations import IntervalRow, normalize_interval_rows
from app.opip.market.source import PolledMinuteBarSource

NOW = datetime(2026, 9, 11, 15, 1, 0, 220000, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 11, 15, 1, 0, tzinfo=timezone.utc)


def _instrument(**overrides) -> InstrumentVersion:
    params = {
        "venue": "kraken",
        "base_asset": "SOL",
        "quote_currency": "USD",
        "venue_instrument_id": "SOLUSD",
        "version": 1,
        "reference_data_version": "opip-evidence-identity-v1",
        "observed_at_utc": NOW,
        "price_decimals": 2,
        "tick_size": 0.01,
        "min_order_size": 0.2,
    }
    params.update(overrides)
    return InstrumentVersion(**params)


def _rows(*, count: int, end_before: datetime, skip: set[int] | None = None):
    skipped = skip or set()
    first = end_before - timedelta(minutes=count)
    epoch = int(first.timestamp())
    rows = []
    for index in range(count):
        if index in skipped:
            continue
        price = 100.0 + 0.05 * index
        rows.append(
            IntervalRow(
                interval_start_epoch=epoch + 60 * index,
                open=price,
                high=price * 1.002,
                low=price * 0.998,
                close=price,
                volume=100.0 + index,
                vwap=price,
                trade_count=10 + index,
            )
        )
    return rows


def _observations(rows, *, commit_from: int | None = 1000):
    result = normalize_interval_rows(
        rows,
        instrument_version=_instrument(),
        interval_seconds=60,
        receipt_time=NOW,
        now=NOW,
        source_label="test_source",
        source_sequence_prefix="test-1m",
    )
    if commit_from is None:
        return result.observations
    return tuple(
        replace(
            item,
            commit_order=ConsumedInputWatermark(
                history_epoch=1, local_sequence=commit_from + index
            ),
        )
        for index, item in enumerate(result.observations)
    )


@pytest.fixture
def canonical_env(tmp_path, monkeypatch):
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(root))
    return {"root": root, "db": root / "opip_canonical_v1.sqlite3"}


@pytest.fixture
def writer_server(canonical_env):
    server = CanonicalWriterServer(
        db_path=canonical_env["db"],
        socket_path=canonical_env["root"] / "writer.sock",
    )
    yield server
    try:
        server.stop()
    except Exception:  # noqa: BLE001
        pass


class _ScriptedPublisher(FeatureBusPublisher):
    """Forces observation outcomes for canonical-first adversarial cases."""

    def __init__(self, script: list[PublishOutcome]):
        super().__init__(enabled=True)
        self._script = list(script)
        self._index = 0

    def publish(self, intent):  # type: ignore[override]
        if intent.event_type == "market.observation.recorded" and self._index < len(
            self._script
        ):
            outcome = self._script[self._index]
            self._index += 1
            self.outcomes.append(outcome)
            return outcome
        return PublishOutcome(
            event_type=str(intent.event_type),
            idempotency_key=intent.idempotency_key,
            status="DISABLED",
            payload_bytes=0,
        )


def test_minimum_warmup_matches_declared_feature_window():
    assert MINIMUM_WARMUP_INTERVALS == FEATURE_WINDOW_INTERVALS == 140


@pytest.mark.parametrize("count,expect_warm", [(0, False), (1, False), (21, False), (139, False), (140, True)])
def test_warm_boundary_from_declared_feature_requirement(count, expect_warm):
    state = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=count, end_before=CUTOFF)) if count else (),
    ).state
    assert state.warm is expect_warm
    if count == 0:
        assert state.restart_state is RestartState.NEW_LISTING_COLD_START
    elif expect_warm:
        assert state.restart_state is RestartState.WARM
    else:
        assert state.restart_state is RestartState.NEW_LISTING_COLD_START


def test_known_empty_window_is_incomplete_not_complete():
    result = align_minute_observations(
        (),
        cutoff=CUTOFF,
        window_start=CUTOFF - timedelta(minutes=10),
    )
    assert result.expected_intervals == 10
    assert result.present_intervals == 0
    assert result.missing_intervals == 10
    assert result.coverage is CoverageState.INCOMPLETE_COVERAGE


def test_cold_start_empty_without_window_remains_complete():
    result = align_minute_observations((), cutoff=CUTOFF)
    assert result.expected_intervals == 0
    assert result.coverage is CoverageState.COMPLETE


def test_all_misaligned_inputs_with_window_are_incomplete():
    observations = list(_observations(_rows(count=3, end_before=CUTOFF), commit_from=None))
    broken = tuple(
        replace(item, aggregate_interval_seconds=900) for item in observations
    )
    result = align_minute_observations(
        broken,
        cutoff=CUTOFF,
        window_start=CUTOFF - timedelta(minutes=3),
    )
    assert result.present_intervals == 0
    assert result.excluded_misaligned == 3
    assert result.coverage is CoverageState.INCOMPLETE_COVERAGE


def test_superseding_revision_updates_retained_state_and_survives_checkpoint():
    base = _observations(_rows(count=5, end_before=CUTOFF), commit_from=None)
    state = advance_state(initial_state(_instrument()), base).state
    corrected = replace(
        base[2],
        revision=2,
        supersedes=base[2].observation_id,
        values={**dict(base[2].values), "close": 999.0},
        ingestion_order=99,
    )
    advanced = advance_state(state, (corrected,)).state
    assert advanced.closes[2] == 999.0
    assert advanced.revisions[2] == 2
    restored = from_checkpoint(to_checkpoint(advanced, created_at_utc=NOW))
    assert restored.closes[2] == 999.0
    assert restored.revisions[2] == 2
    assert restored.venue == "kraken"


def test_stale_revision_cannot_rewind_retained_state():
    base = _observations(_rows(count=3, end_before=CUTOFF), commit_from=None)
    first = advance_state(initial_state(_instrument()), base).state
    upgraded = replace(
        base[1],
        revision=2,
        supersedes=base[1].observation_id,
        values={**dict(base[1].values), "close": 555.0},
        ingestion_order=50,
    )
    warm = advance_state(first, (upgraded,)).state
    stale = replace(
        base[1],
        revision=1,
        values={**dict(base[1].values), "close": 1.0},
        ingestion_order=51,
    )
    after = advance_state(warm, (stale,)).state
    assert after.closes[1] == 555.0
    assert after.revisions[1] == 2


def test_material_gap_always_clears_retained_series():
    before = _observations(
        _rows(count=30, end_before=CUTOFF - timedelta(minutes=40)), commit_from=None
    )
    after = _observations(_rows(count=5, end_before=CUTOFF), commit_from=None)
    state = advance_state(initial_state(_instrument()), before).state
    advanced = advance_state(state, after)
    assert advanced.gap_detected
    assert advanced.state.interval_count == 5
    assert advanced.state.persistence_intervals == 5
    assert advanced.state.gap_resets == 1


def test_checkpoint_id_distinguishes_history_epochs():
    instrument = _instrument()
    a = to_checkpoint(
        replace(
            initial_state(instrument),
            consumed_input_watermark=ConsumedInputWatermark(1, 42),
        ),
        created_at_utc=NOW,
    )
    b = to_checkpoint(
        replace(
            initial_state(instrument),
            consumed_input_watermark=ConsumedInputWatermark(2, 42),
        ),
        created_at_utc=NOW,
    )
    assert a.checkpoint_id == "FSC:1:solusd:features-v1:1-42"
    assert b.checkpoint_id == "FSC:1:solusd:features-v1:2-42"
    assert a.checkpoint_id != b.checkpoint_id


def test_snapshot_idempotency_includes_instrument_version_id():
    shared = dict(
        venue_instrument_id="SOLUSD",
        feature_version="features-v1",
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=ConsumedInputWatermark(1, 7),
        values={"return_1m": 0.1},
        availability=AvailabilityStamp(
            source_at_utc=CUTOFF,
            ingested_at_utc=NOW,
            visible_at_utc=NOW,
            source_version="test",
        ),
        feature_dag_hash=feature_dag_hash(),
    )
    a = FeatureSnapshot(instrument_version_id="INSTR:kraken:SOL:USD:1", **shared)
    b = FeatureSnapshot(instrument_version_id="INSTR:kraken:SOL:USD:2", **shared)
    assert a.snapshot_id == b.snapshot_id
    assert snapshot_idempotency_key(a) != snapshot_idempotency_key(b)
    assert "INSTR:kraken:SOL:USD:1" in snapshot_idempotency_key(a)


def test_content_hash_binds_temporal_provenance():
    base = FeatureSnapshot(
        instrument_version_id="INSTR:kraken:SOL:USD:1",
        venue_instrument_id="SOLUSD",
        feature_version="features-v1",
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=ConsumedInputWatermark(1, 7),
        values={"return_1m": 0.1},
        availability=AvailabilityStamp(
            source_at_utc=CUTOFF,
            ingested_at_utc=NOW,
            visible_at_utc=NOW,
            source_version="test",
        ),
        feature_dag_hash=feature_dag_hash(),
    )
    mutated = FeatureSnapshot(
        instrument_version_id=base.instrument_version_id,
        venue_instrument_id=base.venue_instrument_id,
        feature_version=base.feature_version,
        evaluation_cutoff=base.evaluation_cutoff,
        evaluated_at_utc=NOW + timedelta(milliseconds=1),
        consumed_input_watermark=base.consumed_input_watermark,
        values=dict(base.values),
        availability=AvailabilityStamp(
            source_at_utc=CUTOFF,
            ingested_at_utc=NOW + timedelta(milliseconds=1),
            visible_at_utc=NOW + timedelta(milliseconds=1),
            source_version="test",
        ),
        feature_dag_hash=base.feature_dag_hash,
    )
    assert base.content_hash() != mutated.content_hash()
    assert "availability" in base.to_dict()
    assert base.to_dict()["availability"]["ingested_at_utc"] is not None


def test_evaluation_grid_uses_full_unix_timestamp():
    # 15:01:30 is second=30; old buggy check (second % 60) would accept it.
    bad = CUTOFF + timedelta(seconds=30)
    with pytest.raises(ValueError, match="evaluation grid"):
        FeatureSnapshot(
            instrument_version_id="INSTR:kraken:SOL:USD:1",
            venue_instrument_id="SOLUSD",
            feature_version="features-v1",
            evaluation_cutoff=bad,
            evaluated_at_utc=bad + timedelta(seconds=1),
            consumed_input_watermark=ConsumedInputWatermark.zero(),
            values={},
            availability=AvailabilityStamp(
                source_at_utc=None,
                ingested_at_utc=bad + timedelta(seconds=1),
                visible_at_utc=bad + timedelta(seconds=1),
                source_version="test",
            ),
            feature_dag_hash=feature_dag_hash(),
        )


def test_input_missingness_may_exist_without_matching_values_keys():
    computed = compute_features(
        align_minute_observations(
            _observations(_rows(count=MINIMUM_WARMUP_INTERVALS, end_before=CUTOFF)),
            cutoff=CUTOFF,
        ),
        instrument_version=_instrument(),
        evaluated_at_utc=NOW,
    )
    for name in NOT_RETAINED_INPUTS:
        assert name in computed.missingness
        assert name not in computed.values
        assert computed.missingness[name] is Missingness.NOT_RETAINED


def test_receipt_time_is_stamped_after_fetch_response():
    receipt = CUTOFF + timedelta(seconds=5)

    def clock():
        return receipt

    rows = _rows(count=2, end_before=CUTOFF)

    def fetcher(venue_instrument_id, *, interval_minutes, since_epoch):
        return rows

    source = PolledMinuteBarSource(
        fetcher,
        venue="kraken",
        source_label="test",
        sequence_prefix="test",
        clock=clock,
    )
    # Evaluation `now` is earlier than receipt to prove receipt is not the
    # pre-request evaluation clock.
    batch = source.fetch_through(
        _instrument(), watermark=None, now=CUTOFF + timedelta(seconds=1)
    )
    assert batch.ok
    assert all(item.receipt_time == receipt for item in batch.observations)
    assert all(item.arrival_lag_seconds >= 4 for item in batch.observations)


def test_instrument_version_registry_survives_process_restart_reconstruction():
    registry = InstrumentVersionRegistry()
    descriptor = VenueInstrumentDescriptor(
        venue="kraken",
        base_asset="SOL",
        quote_currency="USD",
        venue_instrument_id="SOLUSD",
        price_decimals=2,
        tick_size=0.01,
        min_order_size=0.2,
    )
    v1 = registry.observe(descriptor, observed_at_utc=NOW)
    payloads = [instrument_version_record_payload(v1)]
    restored = reconstruct_instrument_version_registry(payloads)
    again = restored.observe(descriptor, observed_at_utc=NOW + timedelta(minutes=1))
    assert again.version == 1
    assert again.instrument_version_id == v1.instrument_version_id

    changed = replace(descriptor, min_order_size=0.5)
    v2 = restored.observe(changed, observed_at_utc=NOW + timedelta(minutes=2))
    assert v2.version == 2
    payloads.append(instrument_version_record_payload(v2))
    restored2 = reconstruct_instrument_version_registry(payloads)
    same = restored2.observe(changed, observed_at_utc=NOW + timedelta(minutes=3))
    assert same.version == 2


@pytest.mark.parametrize(
    "status,watermark",
    [
        ("RETRYABLE", ConsumedInputWatermark(1, 1)),
        ("REJECTED", ConsumedInputWatermark(1, 1)),
        ("SPOOLED", ConsumedInputWatermark(1, 1)),
        ("OK", None),
    ],
)
def test_canonical_first_defers_promotion_when_observation_uncommitted(status, watermark):
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF - timedelta(minutes=10))),
    ).state
    observations = _observations(
        _rows(count=2, end_before=CUTOFF), commit_from=None
    )
    script = [
        PublishOutcome(
            event_type="market.observation.recorded",
            idempotency_key=f"obs-{index}",
            status=status,
            watermark=watermark,
        )
        for index in range(len(observations))
    ]
    publisher = _ScriptedPublisher(script)
    result = run_cycle(
        observations,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        state=previous,
        publisher=publisher,
        source_version="test",
    )
    assert result.disposition == DISPOSITION_DEFERRED_UNCOMMITTED
    assert result.promoted is False
    assert result.state is previous or result.state.interval_count == previous.interval_count
    assert result.state.consumed_input_watermark == previous.consumed_input_watermark
    assert result.restart_recorded is False
    assert not any(
        item.event_type.startswith("feature.") and item.status == "OK"
        for item in result.outcomes
    )


def test_canonical_first_promotes_when_all_observations_commit(canonical_env, writer_server):
    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True)
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF - timedelta(minutes=3))),
    ).state
    result = run_cycle(
        _observations(_rows(count=3, end_before=CUTOFF), commit_from=None),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        state=previous,
        publisher=publisher,
        source_version="test",
    )
    assert result.disposition == "OK"
    assert result.promoted is True
    assert result.state.interval_count == previous.interval_count + 3
    assert result.state.consumed_input_watermark != ConsumedInputWatermark.zero()
    assert result.persisted


def test_dry_run_without_publisher_does_not_claim_persistence():
    result = run_cycle(
        _observations(_rows(count=5, end_before=CUTOFF)),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        source_version="test",
    )
    assert result.disposition == DISPOSITION_DRY_RUN
    assert result.promoted is True
    assert result.persisted is False
    assert result.outcomes == ()


def test_restart_recorded_only_when_restart_event_commits(canonical_env, writer_server):
    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True)
    result = run_cycle(
        _observations(_rows(count=5, end_before=CUTOFF), commit_from=None),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        publisher=publisher,
        source_version="test",
    )
    # Short history => restart disposition published; must be committed.
    assert result.state.restart_state is not RestartState.WARM
    assert result.restart_recorded is True
