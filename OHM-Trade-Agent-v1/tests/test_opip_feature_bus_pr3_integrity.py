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
    DISPOSITION_DEFERRED_DEPENDENT,
    DISPOSITION_DEFERRED_UNCOMMITTED,
    DISPOSITION_DRY_RUN,
    run_cycle,
)
from app.opip.features.publisher import (
    FeatureBusPublisher,
    PublishOutcome,
    SHADOW_CAPTURE_SETTINGS,
)
from app.opip.features.state import (
    advance_state,
    alignment_from_state,
    from_checkpoint,
    initial_state,
    revise_against_retained,
    to_checkpoint,
)
from app.opip.market.aggregates import align_minute_observations
from app.opip.market.instrument_version_store import (
    hydrate_instrument_version_registry,
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
        super().__init__(enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
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
    publisher = FeatureBusPublisher(client, enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
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
    publisher = FeatureBusPublisher(client, enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
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


@pytest.mark.parametrize("status", ["SPOOLED", "REJECTED", "RETRYABLE"])
def test_dependent_snapshot_or_checkpoint_uncommitted_defers_promotion(status):
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF - timedelta(minutes=10))),
    ).state
    observations = _observations(_rows(count=2, end_before=CUTOFF), commit_from=None)

    class _Publisher(FeatureBusPublisher):
        def __init__(self) -> None:
            super().__init__(enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
            self._obs = 0

        def publish(self, intent):  # type: ignore[override]
            if intent.event_type == "market.observation.recorded":
                self._obs += 1
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status="OK",
                    watermark=ConsumedInputWatermark(1, self._obs),
                )
                self.outcomes.append(outcome)
                return outcome
            if intent.event_type in {
                "feature.snapshot.recorded",
                "feature.checkpoint.recorded",
            }:
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status=status,
                )
                self.outcomes.append(outcome)
                return outcome
            return PublishOutcome(
                event_type=str(intent.event_type),
                idempotency_key=intent.idempotency_key,
                status="DISABLED",
            )

    result = run_cycle(
        observations,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        state=previous,
        publisher=_Publisher(),
        source_version="test",
    )
    assert result.disposition == DISPOSITION_DEFERRED_DEPENDENT
    assert result.promoted is False
    assert result.state == previous
    assert result.restart_recorded is False


def test_normalize_r1_correction_is_minted_against_retained_state():
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF)),
    ).state
    last_epoch = int(previous.first_interval_epoch) + 60 * (previous.interval_count - 1)
    corrected = IntervalRow(
        interval_start_epoch=last_epoch,
        open=999.0,
        high=1001.0,
        low=998.0,
        close=1000.0,
        volume=50.0,
        vwap=1000.0,
        trade_count=9,
    )
    raw = _observations([corrected], commit_from=None)
    assert all(item.revision == 1 for item in raw)
    revised = revise_against_retained(raw, previous)
    assert len(revised) == 1
    assert revised[0].revision == 2
    assert revised[0].supersedes is not None
    assert revised[0].values["close"] == 1000.0

    advanced = advance_state(previous, revised).state
    assert advanced.closes[-1] == 1000.0
    assert advanced.revisions[-1] == 2


def test_run_cycle_applies_minted_revision_for_ohlc_correction():
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF)),
    ).state
    last_epoch = int(previous.first_interval_epoch) + 60 * (previous.interval_count - 1)
    corrected = _observations(
        [
            IntervalRow(
                interval_start_epoch=last_epoch,
                open=200.0,
                high=201.0,
                low=199.0,
                close=200.5,
                volume=12.0,
            )
        ],
        commit_from=None,
    )
    result = run_cycle(
        corrected,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        state=previous,
        source_version="test",
    )
    assert result.promoted is True
    assert result.state.closes[-1] == 200.5
    assert result.state.revisions[-1] == 2
    assert result.alignment.observations[0].revision == 2


def test_hydrate_registry_from_canonical_db(canonical_env, writer_server):
    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
    v1 = _instrument(version=1, min_order_size=0.2)
    assert publisher.publish_instrument_version(v1).committed
    restored = hydrate_instrument_version_registry(canonical_env["db"])
    again = restored.observe(
        VenueInstrumentDescriptor(
            venue="kraken",
            base_asset="SOL",
            quote_currency="USD",
            venue_instrument_id="SOLUSD",
            price_decimals=2,
            tick_size=0.01,
            min_order_size=0.2,
        ),
        observed_at_utc=NOW + timedelta(minutes=1),
    )
    assert again.version == 1
    changed = restored.observe(
        VenueInstrumentDescriptor(
            venue="kraken",
            base_asset="SOL",
            quote_currency="USD",
            venue_instrument_id="SOLUSD",
            price_decimals=2,
            tick_size=0.01,
            min_order_size=0.5,
        ),
        observed_at_utc=NOW + timedelta(minutes=2),
    )
    assert changed.version == 2


def test_not_retained_input_missingness_keys_remain_distinct_from_values():
    """Phase 14 REJECT: raw input missingness is intentional, not a defect."""
    state = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=140, end_before=CUTOFF)),
    ).state
    computation = compute_features(
        alignment_from_state(state),
        instrument_version=_instrument(),
        evaluated_at_utc=NOW,
    )
    for name in NOT_RETAINED_INPUTS:
        assert name in computation.missingness
        assert computation.missingness[name] is Missingness.NOT_RETAINED
        assert name not in computation.values


def test_open_only_correction_mints_revision():
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF)),
    ).state
    last_epoch = int(previous.first_interval_epoch) + 60 * (previous.interval_count - 1)
    tip_open = previous.opens[-1]
    tip_close = previous.closes[-1]
    tip_high = previous.highs[-1]
    tip_low = previous.lows[-1]
    tip_volume = previous.volumes[-1]
    new_open = tip_open + (tip_high - tip_open) * 0.5
    assert new_open != tip_open
    corrected = _observations(
        [
            IntervalRow(
                interval_start_epoch=last_epoch,
                open=new_open,
                high=tip_high,
                low=tip_low,
                close=tip_close,
                volume=tip_volume,
            )
        ],
        commit_from=None,
    )
    revised = revise_against_retained(corrected, previous)
    assert len(revised) == 1
    assert revised[0].revision == 2
    advanced_open = advance_state(previous, revised).state
    assert advanced_open.opens[-1] == new_open
    assert advanced_open.revisions[-1] == 2


def test_unchanged_tip_repoll_is_dropped_before_publish():
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF)),
    ).state
    last_epoch = int(previous.first_interval_epoch) + 60 * (previous.interval_count - 1)
    # Must include the same optional aggregate fields the source originally persisted.
    same = _observations(
        [
            IntervalRow(
                interval_start_epoch=last_epoch,
                open=previous.opens[-1],
                high=previous.highs[-1],
                low=previous.lows[-1],
                close=previous.closes[-1],
                volume=previous.volumes[-1],
                vwap=previous.closes[-1],
                trade_count=14,
            )
        ],
        commit_from=None,
    )
    assert revise_against_retained(same, previous) == ()


def test_coverage_gap_uncommitted_defers_promotion():
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF - timedelta(minutes=10))),
    ).state
    # Skip an interval inside the window so alignment reports a coverage gap.
    observations = _observations(
        _rows(count=4, end_before=CUTOFF, skip={1}),
        commit_from=None,
    )

    class _Publisher(FeatureBusPublisher):
        def __init__(self) -> None:
            super().__init__(enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
            self._obs = 0

        def publish(self, intent):  # type: ignore[override]
            if intent.event_type == "market.observation.recorded":
                self._obs += 1
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status="OK",
                    watermark=ConsumedInputWatermark(1, self._obs),
                )
                self.outcomes.append(outcome)
                return outcome
            if intent.event_type == "coverage.gap.recorded":
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status="SPOOLED",
                )
                self.outcomes.append(outcome)
                return outcome
            if intent.event_type in {
                "feature.snapshot.recorded",
                "feature.checkpoint.recorded",
            }:
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status="OK",
                    watermark=ConsumedInputWatermark(1, 100 + self._obs),
                )
                self.outcomes.append(outcome)
                return outcome
            return PublishOutcome(
                event_type=str(intent.event_type),
                idempotency_key=intent.idempotency_key,
                status="DISABLED",
            )

    result = run_cycle(
        observations,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        state=previous,
        publisher=_Publisher(),
        source_version="test",
        window_start=CUTOFF - timedelta(minutes=4),
    )
    assert result.alignment.gaps
    assert result.disposition == DISPOSITION_DEFERRED_DEPENDENT
    assert result.promoted is False


def test_source_readmits_tip_interval_for_correction():
    instrument = _instrument()
    rows = _rows(count=3, end_before=CUTOFF)
    calls: list[int | None] = []

    def fetcher(venue_id, *, interval_minutes, since_epoch):
        calls.append(since_epoch)
        return rows

    source = PolledMinuteBarSource(
        fetcher,
        venue="kraken",
        source_label="test",
        sequence_prefix="test",
        interval_seconds=60,
        clock=lambda: NOW,
    )
    first = source.fetch_through(instrument, watermark=None, now=NOW)
    assert first.ok
    assert first.coverage is CoverageState.COMPLETE
    assert first.watermark.through_utc is not None
    second = source.fetch_through(
        instrument, watermark=first.watermark, now=NOW
    )
    tip_start = first.watermark.through_utc - timedelta(seconds=60)
    assert any(item.source_event_time == tip_start for item in second.observations)
    # Same evaluation clock: only the tip closed bar is expected → COMPLETE.
    assert second.coverage is CoverageState.COMPLETE


def test_source_coverage_incomplete_when_tip_window_has_gaps():
    instrument = _instrument()
    gapped = _rows(count=4, end_before=CUTOFF, skip={1})

    def fetcher(venue_id, *, interval_minutes, since_epoch):
        return gapped

    source = PolledMinuteBarSource(
        fetcher,
        venue="kraken",
        source_label="test",
        sequence_prefix="test",
        interval_seconds=60,
        clock=lambda: NOW,
    )
    batch = source.fetch_through(instrument, watermark=None, now=NOW)
    assert batch.observations
    assert batch.coverage is CoverageState.INCOMPLETE_COVERAGE


def test_source_coverage_incomplete_when_tip_lags_evaluation_clock():
    instrument = _instrument()
    rows = _rows(count=3, end_before=CUTOFF)

    def fetcher(venue_id, *, interval_minutes, since_epoch):
        return rows

    source = PolledMinuteBarSource(
        fetcher,
        venue="kraken",
        source_label="test",
        sequence_prefix="test",
        interval_seconds=60,
        clock=lambda: NOW,
    )
    first = source.fetch_through(instrument, watermark=None, now=NOW)
    later = NOW + timedelta(minutes=3)
    tip_only = source.fetch_through(
        instrument, watermark=first.watermark, now=later
    )
    assert tip_only.observations
    assert tip_only.coverage is CoverageState.INCOMPLETE_COVERAGE


@pytest.mark.parametrize("status", ["SPOOLED", "REJECTED", "RETRYABLE"])
def test_required_restart_uncommitted_defers_promotion(status):
    observations = _observations(_rows(count=5, end_before=CUTOFF), commit_from=None)

    class _Publisher(FeatureBusPublisher):
        def __init__(self) -> None:
            super().__init__(enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
            self._obs = 0

        def publish(self, intent):  # type: ignore[override]
            if intent.event_type == "market.observation.recorded":
                self._obs += 1
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status="OK",
                    watermark=ConsumedInputWatermark(1, self._obs),
                )
                self.outcomes.append(outcome)
                return outcome
            if intent.event_type in {
                "feature.snapshot.recorded",
                "feature.checkpoint.recorded",
            }:
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status="OK",
                    watermark=ConsumedInputWatermark(1, 100 + self._obs),
                )
                self.outcomes.append(outcome)
                return outcome
            if intent.event_type == "feature.restart.recorded":
                outcome = PublishOutcome(
                    event_type=str(intent.event_type),
                    idempotency_key=intent.idempotency_key,
                    status=status,
                )
                self.outcomes.append(outcome)
                return outcome
            return PublishOutcome(
                event_type=str(intent.event_type),
                idempotency_key=intent.idempotency_key,
                status="DISABLED",
            )

    result = run_cycle(
        observations,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        publisher=_Publisher(),
        source_version="test",
    )
    assert result.disposition == DISPOSITION_DEFERRED_DEPENDENT
    assert result.promoted is False
    assert result.restart_recorded is False
    assert result.state.interval_count == 0


def test_legacy_opens_retained_stays_false_until_series_rebuild():
    early = advance_state(
        initial_state(_instrument()),
        _observations(
            _rows(count=5, end_before=CUTOFF - timedelta(minutes=40)),
            commit_from=None,
        ),
    ).state
    checkpoint = to_checkpoint(early, created_at_utc=NOW)
    rolling = dict(checkpoint.rolling_state)
    rolling.pop("opens", None)
    rolling.pop("opens_retained", None)
    restored = from_checkpoint(replace(checkpoint, rolling_state=rolling))
    assert restored.opens_retained is False

    tip_epoch = int(restored.first_interval_epoch) + 60 * (restored.interval_count - 1)
    corrected = replace(
        _observations(
            [
                IntervalRow(
                    interval_start_epoch=tip_epoch,
                    open=111.0,
                    high=112.0,
                    low=110.0,
                    close=111.5,
                    volume=9.0,
                    vwap=111.2,
                    trade_count=3,
                )
            ],
            commit_from=None,
        )[0],
        revision=2,
        supersedes="OBS:legacy",
    )
    advanced = advance_state(restored, (corrected,)).state
    assert advanced.opens_retained is False
    assert advanced.closes[-1] == 111.5

    rebuilt = advance_state(
        advanced,
        _observations(_rows(count=5, end_before=CUTOFF), commit_from=None),
    )
    assert rebuilt.gap_detected
    assert rebuilt.state.opens_retained is True
    assert rebuilt.state.interval_count == 5


def test_pilot_dry_run_forces_disabled_capture_and_synthetic_identity(monkeypatch):
    monkeypatch.setenv("OPIP_FEATURE_BUS_MODE", "shadow")
    monkeypatch.setenv("OPIP_CANONICAL_WRITER_MODE", "shadow")
    from app.jobs import run_feature_bus_pilot as pilot

    captured: dict[str, object] = {}

    class _SpyPublisher(FeatureBusPublisher):
        def __init__(self, *args, **kwargs):
            captured["enabled"] = kwargs.get("enabled", True)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(pilot, "FeatureBusPublisher", _SpyPublisher)
    report = pilot._dry_run(4)
    assert captured.get("enabled") is False
    assert report["cycle"]["instrument_version_id"].startswith("INSTR:synthetic:")
    assert report["cycle"]["persisted"] is False
    assert report["cycle"]["disposition"] == DISPOSITION_DRY_RUN


def test_checkpoint_store_restores_rolling_state(canonical_env, writer_server):
    from app.opip.features.checkpoint_store import load_rolling_state

    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
    instrument = _instrument()
    observations = _observations(
        _rows(count=MINIMUM_WARMUP_INTERVALS, end_before=CUTOFF), commit_from=None
    )
    result = run_cycle(
        observations,
        instrument_version=instrument,
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        publisher=publisher,
        source_version="test",
    )
    assert result.promoted is True
    restored = load_rolling_state(
        instrument.instrument_version_id, db_path=canonical_env["db"]
    )
    assert restored is not None
    assert restored.interval_count == result.state.interval_count
    assert restored.closes == result.state.closes
    assert restored.resumed_from_checkpoint is True


def test_enabled_true_cannot_bypass_dual_shadow_gates():
    off = FeatureBusPublisher(enabled=True)
    assert off.enabled is False
    on = FeatureBusPublisher(enabled=True, settings=SHADOW_CAPTURE_SETTINGS)
    assert on.enabled is True
    forced_off = FeatureBusPublisher(enabled=False, settings=SHADOW_CAPTURE_SETTINGS)
    assert forced_off.enabled is False


def test_reference_data_version_participates_in_fingerprint():
    a = _instrument(reference_data_version="opip-evidence-identity-v1")
    b = _instrument(reference_data_version="opip-evidence-identity-v2")
    assert a.reference_fingerprint() != b.reference_fingerprint()


def test_instrument_version_same_version_fingerprint_conflict_fails_closed():
    v1 = _instrument(version=1)
    payload = instrument_version_record_payload(v1)
    conflict = dict(payload)
    conflict["tick_size"] = 0.02
    with pytest.raises(ValueError, match="instrument version conflict"):
        reconstruct_instrument_version_registry([payload, conflict])


def test_window_start_must_be_grid_aligned():
    with pytest.raises(ValueError, match="window_start"):
        align_minute_observations(
            (),
            cutoff=CUTOFF,
            window_start=CUTOFF + timedelta(seconds=1),
        )


def test_source_incomplete_propagates_into_cycle_coverage():
    previous = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=5, end_before=CUTOFF - timedelta(minutes=5))),
    ).state
    result = run_cycle(
        _observations(_rows(count=1, end_before=CUTOFF), commit_from=None),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        state=previous,
        source_version="test",
        window_start=datetime.fromtimestamp(previous.first_interval_epoch, tz=timezone.utc),
        source_coverage=CoverageState.INCOMPLETE_COVERAGE,
    )
    assert result.alignment.coverage is CoverageState.INCOMPLETE_COVERAGE


def test_publish_observations_false_with_capture_raises():
    with pytest.raises(ValueError, match="publish_observations=False"):
        run_cycle(
            _observations(_rows(count=3, end_before=CUTOFF), commit_from=None),
            instrument_version=_instrument(),
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            publisher=FeatureBusPublisher(
                enabled=True, settings=SHADOW_CAPTURE_SETTINGS
            ),
            source_version="test",
            publish_observations=False,
        )


def test_non_finite_rows_are_rejected():
    from app.opip.market.observations import normalize_interval_rows

    result = normalize_interval_rows(
        [
            IntervalRow(
                interval_start_epoch=int(CUTOFF.timestamp()) - 60,
                open=float("nan"),
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
            )
        ],
        instrument_version=_instrument(),
        interval_seconds=60,
        receipt_time=NOW,
        now=NOW,
        source_label="test",
        source_sequence_prefix="t",
    )
    assert result.observations == ()
    assert result.rejected[0].reason == "non_finite_price"


def test_kraken_invalid_pair_decimals_fail_closed():
    from app.opip.market.instruments import kraken_descriptor

    assert (
        kraken_descriptor(
            "X",
            {
                "wsname": "SOL/USD",
                "base": "SOL",
                "quote": "ZUSD",
                "pair_decimals": True,
                "ordermin": "0.1",
            },
        )
        is None
    )
    assert (
        kraken_descriptor(
            "X",
            {
                "wsname": "SOL/USD",
                "base": "SOL",
                "quote": "ZUSD",
                "pair_decimals": 99,
                "ordermin": "0.1",
            },
        )
        is None
    )


def test_opens_known_recovers_after_full_window_eviction():
    from app.opip.features.engine import FEATURE_WINDOW_INTERVALS

    early = advance_state(
        initial_state(_instrument()),
        _observations(
            _rows(count=5, end_before=CUTOFF - timedelta(minutes=200)),
            commit_from=None,
        ),
    ).state
    checkpoint = to_checkpoint(early, created_at_utc=NOW)
    rolling = dict(checkpoint.rolling_state)
    rolling.pop("opens", None)
    rolling.pop("opens_known", None)
    rolling.pop("opens_retained", None)
    rolling.pop("content_fingerprints", None)
    legacy = from_checkpoint(replace(checkpoint, rolling_state=rolling))
    assert legacy.opens_retained is False
    assert all(not known for known in legacy.opens_known)

    filled = advance_state(
        legacy,
        _observations(
            _rows(count=FEATURE_WINDOW_INTERVALS, end_before=CUTOFF),
            commit_from=None,
        ),
    ).state
    assert filled.interval_count == FEATURE_WINDOW_INTERVALS
    assert filled.opens_retained is True
    assert all(filled.opens_known)


def test_schema_version_rejects_bool_and_zero():
    shared = dict(
        instrument_version_id="INSTR:kraken:SOL:USD:1",
        venue_instrument_id="SOLUSD",
        feature_version="features-v1",
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=ConsumedInputWatermark(1, 1),
        values={"x": 1.0},
        availability=AvailabilityStamp(
            source_at_utc=CUTOFF,
            ingested_at_utc=NOW,
            visible_at_utc=NOW,
            source_version="test",
        ),
        feature_dag_hash=feature_dag_hash(),
    )
    with pytest.raises(ValueError):
        FeatureSnapshot(**shared, schema_version=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        FeatureSnapshot(**shared, schema_version=0)


def test_observation_values_are_immutable_after_construction():
    obs = _observations(_rows(count=1, end_before=CUTOFF), commit_from=None)[0]
    before = dict(obs.values)
    with pytest.raises(TypeError):
        obs.values["close"] = 0.0  # type: ignore[index]
    assert dict(obs.values) == before


def test_empty_expected_window_cycle_is_incomplete():
    result = run_cycle(
        (),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        source_version="test",
        window_start=CUTOFF - timedelta(minutes=5),
    )
    assert result.alignment.expected_intervals == 5
    assert result.alignment.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert result.alignment.gaps
