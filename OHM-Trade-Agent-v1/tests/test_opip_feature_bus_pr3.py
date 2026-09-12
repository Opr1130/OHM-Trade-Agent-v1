"""PR 3 O'Pip feature bus tests (slices A-G).

These lock the properties the adjudication asked PR3 to prove:

* v1.2 fixture identities reproduce exactly.
* Point-in-time integrity is enforced, not documented.
* Malformed and forming market rows are rejected, never repaired.
* Missing minutes are gaps; rolling features never span them.
* Resume from checkpoint equals uninterrupted processing, exactly.
* Feature-bus canonical writes are additive, LOW priority, on their own
  watermark stream, and do not change alert-governor behaviour.
* Capture stays off unless both gates are shadow.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION, STREAM_EARLY_WATCH
from app.opip.canonical.rebuild import rebuild_identity_projection
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.enums import (
    CompressionState,
    CoverageState,
    Missingness,
    PayloadKind,
    RestartState,
)
from app.opip.contracts.events import (
    FEATURE_BUS_EVENT_TYPES,
    FEATURE_BUS_PRIORITY,
    FEATURE_BUS_STREAM,
    FEATURE_SNAPSHOT_RECORDED,
    MARKET_OBSERVATION_RECORDED,
    snapshot_idempotency_key,
)
from app.opip.contracts.features import FeatureSnapshot
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.observation import Observation, SourceWatermark
from app.opip.contracts.serialization import iso_z
from app.opip.contracts.temporal import AvailabilityStamp, TemporalIntegrityError
from app.opip.features import parity as parity_module
from app.opip.features.engine import (
    FEATURE_NAMES,
    FEATURE_WINDOW_INTERVALS,
    MINIMUM_WARMUP_INTERVALS,
    ROLLING_FEATURE_NAMES,
    build_feature_snapshot,
    compute_features,
    feature_dag_hash,
)
from app.opip.features.ml_bridge import to_shared_snapshot, versioning_is_lossless
from app.opip.features.parity import (
    ABSOLUTE_TOLERANCE,
    compare_against_production_indicators,
    percentile_definition_divergence,
)
from app.opip.features.pipeline import run_cycle
from app.opip.features.publisher import (
    FeatureBusPublisher,
    build_intent,
    feature_bus_capture_enabled,
    observation_intent,
    resolve_feature_bus_mode,
)
from app.opip.features.replay import (
    assert_snapshot_determinism,
    compare_resumed_state,
    detector_input_fingerprint,
    detector_replay_input,
    reconstruct_state,
)
from app.opip.features.state import (
    advance_state,
    alignment_from_state,
    checkpoint_payload_bytes,
    from_checkpoint,
    initial_state,
    to_checkpoint,
)
from app.opip.market.aggregates import (
    aggregate_trades_to_minutes,
    align_minute_observations,
    contiguous_tail,
    grid_floor,
    is_grid_aligned,
)
from app.opip.market.instruments import (
    InstrumentVersionRegistry,
    is_eligible_pair,
    kraken_descriptors,
)
from app.opip.market.observations import (
    DERIVED_SEQUENCE_ORIGIN,
    IntervalRow,
    completed_observations,
    normalize_interval_rows,
)
from app.opip.market.source import run_pilot_cycle
from app.services.opip_feature_bus_market_source import kraken_minute_source

NOW = datetime(2026, 9, 11, 15, 1, 0, 220000, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 11, 15, 1, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


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


def _rows(
    *,
    count: int,
    end_before: datetime,
    start_price: float = 100.0,
    step: float = 0.05,
    volume: float = 100.0,
    skip: set[int] | None = None,
) -> list[IntervalRow]:
    """Deterministic ascending minute bars ending just before ``end_before``."""
    skipped = skip or set()
    first = end_before - timedelta(minutes=count)
    epoch = int(first.timestamp())
    rows: list[IntervalRow] = []
    for index in range(count):
        if index in skipped:
            continue
        price = start_price + step * index
        rows.append(
            IntervalRow(
                interval_start_epoch=epoch + 60 * index,
                open=price,
                high=price * 1.002,
                low=price * 0.998,
                close=price,
                volume=volume + index,
                vwap=price,
                trade_count=10 + index,
            )
        )
    return rows


def _observations(
    rows: list[IntervalRow],
    *,
    instrument_version: InstrumentVersion | None = None,
    receipt_time: datetime = NOW,
    now: datetime = NOW,
    commit_from: int | None = 1000,
) -> tuple[Observation, ...]:
    instrument = instrument_version or _instrument()
    result = normalize_interval_rows(
        rows,
        instrument_version=instrument,
        interval_seconds=60,
        receipt_time=receipt_time,
        now=now,
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


def _aligned(observations, *, cutoff: datetime = CUTOFF):
    return align_minute_observations(observations, cutoff=cutoff)


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
    except Exception:  # noqa: BLE001 - teardown only
        pass


# --------------------------------------------------------------------------- #
# Slice A - contracts
# --------------------------------------------------------------------------- #


def test_fixture_identities_reproduce_exactly():
    instrument = _instrument()
    assert instrument.instrument_version_id == "INSTR:kraken:SOL:USD:1"
    assert instrument.instrument_key == "kraken:SOL:USD"

    snapshot = FeatureSnapshot(
        instrument_version_id=instrument.instrument_version_id,
        venue_instrument_id="SOLUSD",
        feature_version="features-v1",
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=ConsumedInputWatermark(1, 1042),
        values={"return_1m": 0.5},
        availability=AvailabilityStamp(
            source_at_utc=CUTOFF,
            ingested_at_utc=NOW,
            visible_at_utc=NOW,
            source_version="test",
        ),
        feature_dag_hash="deadbeef",
    )
    assert snapshot.snapshot_id == "FS:1:solusd:2026-09-11T15:01:00Z:features-v1"
    assert iso_z(NOW) == "2026-09-11T15:01:00.220Z"

    state = advance_state(
        initial_state(instrument),
        _observations(_rows(count=30, end_before=CUTOFF), commit_from=1013),
    ).state
    checkpoint = to_checkpoint(state, created_at_utc=NOW)
    assert checkpoint.consumed_input_watermark.local_sequence == 1042
    assert checkpoint.checkpoint_id == "FSC:1:solusd:features-v1:1-1042"


def test_snapshot_rejects_input_visible_after_evaluation():
    with pytest.raises(TemporalIntegrityError):
        FeatureSnapshot(
            instrument_version_id="INSTR:kraken:SOL:USD:1",
            venue_instrument_id="SOLUSD",
            feature_version="features-v1",
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=ConsumedInputWatermark.zero(),
            values={},
            availability=AvailabilityStamp(
                source_at_utc=CUTOFF,
                ingested_at_utc=NOW,
                visible_at_utc=NOW + timedelta(seconds=1),
                source_version="test",
            ),
            feature_dag_hash="deadbeef",
        )


def test_snapshot_rejects_off_grid_cutoff_and_backwards_evaluation():
    stamp = AvailabilityStamp(
        source_at_utc=CUTOFF,
        ingested_at_utc=CUTOFF,
        visible_at_utc=CUTOFF,
        source_version="test",
    )
    common = {
        "instrument_version_id": "INSTR:kraken:SOL:USD:1",
        "venue_instrument_id": "SOLUSD",
        "feature_version": "features-v1",
        "consumed_input_watermark": ConsumedInputWatermark.zero(),
        "values": {},
        "availability": stamp,
        "feature_dag_hash": "deadbeef",
    }
    with pytest.raises(ValueError):
        FeatureSnapshot(
            evaluation_cutoff=CUTOFF + timedelta(seconds=13),
            evaluated_at_utc=NOW,
            **common,
        )
    with pytest.raises(ValueError):
        FeatureSnapshot(
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=CUTOFF - timedelta(seconds=1),
            **common,
        )


def test_watermark_never_regresses():
    start = ConsumedInputWatermark(1, 10)
    assert start.advanced_to(history_epoch=1, local_sequence=5) == start
    assert start.advanced_to(history_epoch=1, local_sequence=11).local_sequence == 11
    assert start.advanced_to(history_epoch=2, local_sequence=1).history_epoch == 2


def test_instrument_fingerprint_ignores_observation_time_but_not_metadata():
    registry = InstrumentVersionRegistry()
    first = registry.observe(_reference_descriptor(), observed_at_utc=NOW)
    again = registry.observe(
        _reference_descriptor(), observed_at_utc=NOW + timedelta(days=1)
    )
    assert again.version == first.version == 1

    changed = registry.observe(
        _reference_descriptor(price_decimals=3), observed_at_utc=NOW
    )
    assert changed.version == 2


def _reference_descriptor(**overrides):
    from app.opip.market.instruments import VenueInstrumentDescriptor

    params = {
        "venue": "kraken",
        "base_asset": "SOL",
        "quote_currency": "USD",
        "venue_instrument_id": "SOLUSD",
        "price_decimals": 2,
        "tick_size": 0.01,
        "min_order_size": 0.2,
    }
    params.update(overrides)
    return VenueInstrumentDescriptor(**params)


def test_contracts_layer_has_no_downstream_dependencies():
    root = Path(__file__).resolve().parents[1] / "app" / "opip" / "contracts"
    forbidden = (
        "app.services",
        "app.exchanges",
        "app.opip.canonical",
        "app.opip.storage",
        "app.scanner",
        "app.notifications",
        "app.jobs",
    )
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for module in forbidden:
            assert f"import {module}" not in text, f"{path.name} imports {module}"
            assert f"from {module}" not in text, f"{path.name} imports {module}"


# --------------------------------------------------------------------------- #
# Slice B - market inputs
# --------------------------------------------------------------------------- #


def test_eligibility_matches_production_universe_rules():
    assert is_eligible_pair({"wsname": "SOL/USD", "altname": "SOLUSD"})
    assert is_eligible_pair({"wsname": "SOL/USDT", "altname": "SOLUSDT"})
    assert not is_eligible_pair({"wsname": "SOL/EUR", "altname": "SOLEUR"})
    assert not is_eligible_pair({"wsname": "USDT/USD", "altname": "USDTUSD"})


def test_kraken_descriptors_alias_and_sort_deterministically():
    details = {
        "XXBTZUSD": {
            "wsname": "XBT/USD",
            "altname": "XBTUSD",
            "pair_decimals": 1,
            "ordermin": "0.0001",
        },
        "SOLUSD": {
            "wsname": "SOL/USD",
            "altname": "SOLUSD",
            "pair_decimals": 2,
            "ordermin": "0.2",
        },
        "SOLEUR": {"wsname": "SOL/EUR", "altname": "SOLEUR", "pair_decimals": 2},
    }
    descriptors = kraken_descriptors(details)
    keys = [item.instrument_key for item in descriptors]
    reordered = kraken_descriptors(dict(reversed(list(details.items()))))
    assert keys == [item.instrument_key for item in reordered]
    assert "kraken:BTC:USD" in keys
    assert all("EUR" not in key for key in keys)
    btc = next(item for item in descriptors if item.base_asset == "BTC")
    assert btc.tick_size == pytest.approx(0.1)
    assert btc.min_order_size == pytest.approx(0.0001)


def test_malformed_rows_are_rejected_with_reasons_not_repaired():
    base = int(CUTOFF.timestamp()) - 600
    rows = [
        IntervalRow(base, 10.0, 9.0, 8.0, 8.5, 1.0),  # high below open
        IntervalRow(base + 30, 10.0, 11.0, 9.0, 10.0, 1.0),  # off grid
        IntervalRow(base + 60, 10.0, 11.0, 9.0, 10.0, -1.0),  # negative volume
        IntervalRow(base + 120, 0.0, 1.0, 0.0, 0.5, 1.0),  # non-positive price
        IntervalRow(base + 180, 10.0, 11.0, 9.0, 10.5, 5.0),  # valid
    ]
    result = normalize_interval_rows(
        rows,
        instrument_version=_instrument(),
        interval_seconds=60,
        receipt_time=NOW,
        now=NOW,
        source_label="test_source",
        source_sequence_prefix="test-1m",
    )
    reasons = {item.reason for item in result.rejected}
    assert reasons == {
        "high_below_body",
        "interval_start_not_grid_aligned",
        "negative_volume",
        "non_positive_price",
    }
    assert len(result.observations) == 1
    assert result.observations[0].values["close"] == 10.5


def test_derived_source_sequence_is_labelled_as_derived():
    observations = _observations(_rows(count=2, end_before=CUTOFF), commit_from=None)
    assert observations[0].provenance["source_sequence_origin"] == (
        DERIVED_SEQUENCE_ORIGIN
    )
    assert observations[0].source_sequence.startswith("test-1m-")


def test_forming_interval_is_marked_and_excluded():
    now = CUTOFF + timedelta(seconds=30)
    rows = _rows(count=3, end_before=CUTOFF) + [
        IntervalRow(int(CUTOFF.timestamp()), 10.0, 11.0, 9.0, 10.0, 1.0)
    ]
    observations = _observations(rows, now=now, commit_from=None)
    forming = [item for item in observations if item.interval_forming]
    assert len(forming) == 1
    assert forming[0].coverage is CoverageState.INCOMPLETE_COVERAGE
    assert len(completed_observations(observations)) == 3


class _FakeKrakenClient:
    """Minimal stand-in. Records how it was called; performs no network I/O."""

    def __init__(self, candles):
        self._candles = candles
        self.calls: list[dict] = []

    def get_ohlc(self, pair, interval, since=None):
        self.calls.append({"pair": pair, "interval": interval, "since": since})
        return self._candles


def _candle(epoch: int, price: float):
    from app.exchanges.kraken import Candle

    return Candle(
        timestamp=epoch,
        open=price,
        high=price * 1.001,
        low=price * 0.999,
        close=price,
        vwap=price,
        volume=10.0,
        trade_count=5,
    )


def test_kraken_pilot_source_requests_one_minute_and_excludes_forming():
    first = int(CUTOFF.timestamp()) - 180
    candles = [
        _candle(first, 100.0),
        _candle(first + 60, 100.5),
        _candle(first + 120, 101.0),
        _candle(int(CUTOFF.timestamp()), 101.5),  # forming at CUTOFF+30s
    ]
    client = _FakeKrakenClient(candles)
    source = kraken_minute_source(client)
    batch = source.fetch_through(
        _instrument(), watermark=None, now=CUTOFF + timedelta(seconds=30)
    )
    assert client.calls[0]["interval"] == 1
    assert client.calls[0]["since"] is None
    assert len(batch.observations) == 3
    assert batch.metrics.requests == 1
    assert batch.metrics.forming_excluded == 1
    assert batch.watermark.through_utc == CUTOFF
    assert batch.coverage is CoverageState.COMPLETE

    resumed = source.fetch_through(
        _instrument(), watermark=batch.watermark, now=CUTOFF + timedelta(seconds=30)
    )
    assert client.calls[1]["since"] == int(CUTOFF.timestamp()) - 1
    assert resumed.observations == ()


class _FailingKrakenClient:
    def get_ohlc(self, pair, interval, since=None):
        from app.exchanges.kraken import KrakenAPIError

        raise KrakenAPIError("Kraken rate limit exceeded")


def test_kraken_pilot_source_fails_closed_without_advancing_watermark():
    source = kraken_minute_source(_FailingKrakenClient())
    watermark = SourceWatermark(
        instrument_version_id="INSTR:kraken:SOL:USD:1", through_utc=CUTOFF
    )
    batch = source.fetch_through(_instrument(), watermark=watermark, now=NOW)
    assert batch.observations == ()
    assert batch.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert batch.metrics.failures == 1
    assert batch.metrics.rate_limited == 1
    assert batch.watermark.through_utc == CUTOFF


def test_polled_source_propagates_unexpected_errors_instead_of_hiding_them():
    """Only a named transport failure becomes a coverage gap."""
    from app.opip.market.source import PolledMinuteBarSource

    def _broken(venue_instrument_id, *, interval_minutes, since_epoch):
        raise KeyError("programming error")

    source = PolledMinuteBarSource(
        _broken,
        venue="test",
        source_label="test",
        sequence_prefix="test",
    )
    with pytest.raises(KeyError):
        source.fetch_through(_instrument(), watermark=None, now=NOW)


def test_pilot_cycle_report_measures_the_d4_question():
    first = int(CUTOFF.timestamp()) - 120
    source = kraken_minute_source(
        _FakeKrakenClient([_candle(first, 100.0), _candle(first + 60, 100.5)])
    )
    ticks = iter([CUTOFF, CUTOFF + timedelta(seconds=30)])
    _, report, watermarks = run_pilot_cycle(
        source,
        [_instrument()],
        now=CUTOFF,
        eligible_instruments=420,
        clock=lambda: next(ticks),
    )
    assert report.metrics.requests == 1
    assert report.requests_per_minute == pytest.approx(2.0)
    assert report.projected_requests_per_minute_full_universe == 420.0
    assert report.coverage_pct == 100.0
    assert "INSTR:kraken:SOL:USD:1" in watermarks


# --------------------------------------------------------------------------- #
# Slice C - one-minute aggregation
# --------------------------------------------------------------------------- #


def test_grid_helpers():
    assert grid_floor(NOW) == CUTOFF
    assert is_grid_aligned(CUTOFF)
    assert not is_grid_aligned(NOW)
    with pytest.raises(ValueError):
        align_minute_observations((), cutoff=NOW)


def test_missing_minute_is_a_gap_and_reduces_coverage():
    observations = _observations(
        _rows(count=10, end_before=CUTOFF, skip={7}), commit_from=None
    )
    result = _aligned(observations)
    assert result.expected_intervals == 10
    assert result.present_intervals == 9
    assert result.missing_intervals == 1
    assert result.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert result.coverage_ratio == pytest.approx(0.9)
    gap = result.gaps[0]
    assert gap.first_missing_utc == CUTOFF - timedelta(minutes=3)
    assert gap.resumes_at_utc == CUTOFF - timedelta(minutes=2)
    assert len(contiguous_tail(result)) == 2


def test_trailing_coverage_holes_do_not_wipe_contiguous_tail():
    """Incomplete latest minutes must not empty the rolling series.

    When present bars stop short of cutoff, alignment correctly reports trailing
    gaps. Those holes are coverage evidence, not a mid-window split: wiping the
    contiguous tail to empty would discard warm history and publish cold
    features while retained state stayed warm.
    """
    observations = _observations(
        _rows(count=20, end_before=CUTOFF - timedelta(minutes=5)),
        commit_from=None,
    )
    result = _aligned(observations)
    assert result.gaps
    latest = max(item.source_event_time for item in result.observations)
    assert all(gap.resumes_at_utc > latest for gap in result.gaps)
    assert len(contiguous_tail(result)) == 20


def test_alignment_is_order_independent():
    observations = list(
        _observations(_rows(count=12, end_before=CUTOFF), commit_from=None)
    )
    forward = _aligned(observations)
    shuffled = _aligned(list(reversed(observations)))
    assert [item.observation_id for item in forward.observations] == [
        item.observation_id for item in shuffled.observations
    ]


def test_unclosed_and_misaligned_intervals_are_excluded():
    rows = _rows(count=3, end_before=CUTOFF)
    observations = list(_observations(rows, commit_from=None))
    future = replace(
        observations[-1],
        source_event_time=CUTOFF,
        ingestion_order=99,
    )
    wrong_interval = replace(observations[0], aggregate_interval_seconds=900)
    result = _aligned(observations + [future, wrong_interval])
    assert result.excluded_unclosed == 1
    assert result.excluded_misaligned == 1
    assert result.coverage is CoverageState.INCOMPLETE_COVERAGE


def test_superseding_revision_wins_without_deleting_the_earlier_one():
    observations = list(
        _observations(_rows(count=3, end_before=CUTOFF), commit_from=None)
    )
    corrected = replace(
        observations[1],
        revision=2,
        supersedes=observations[1].observation_id,
        values={**dict(observations[1].values), "close": 999.0},
        ingestion_order=50,
    )
    result = _aligned(observations + [corrected])
    assert result.observations[1].values["close"] == 999.0
    assert len(result.superseded) == 1


def test_late_arrival_is_visible_as_late():
    rows = _rows(count=3, end_before=CUTOFF)
    late = _observations(rows, receipt_time=CUTOFF + timedelta(seconds=45))
    result = _aligned(late)
    assert len(result.late_arrivals) == 3
    assert result.late_arrivals[0].arrival_lag_seconds > 5.0


def test_trade_folding_is_deterministic():
    instrument = _instrument()
    start = CUTOFF - timedelta(minutes=2)
    trades = [
        Observation(
            instrument_version_id=instrument.instrument_version_id,
            venue="kraken",
            venue_instrument_id="SOLUSD",
            source_event_time=start + timedelta(seconds=offset),
            receipt_time=NOW,
            ingestion_order=index + 1,
            payload_kind=PayloadKind.TRADE,
            values={"price": price, "quantity": 1.0},
        )
        for index, (offset, price) in enumerate(
            [(1, 100.0), (10, 101.0), (59, 99.5), (61, 100.25)]
        )
    ]
    forward = aggregate_trades_to_minutes(
        trades,
        instrument_version=instrument,
        receipt_time=NOW,
        now=NOW,
        source_label="trades",
        source_sequence_prefix="trade-1m",
    )
    reverse = aggregate_trades_to_minutes(
        list(reversed(trades)),
        instrument_version=instrument,
        receipt_time=NOW,
        now=NOW,
        source_label="trades",
        source_sequence_prefix="trade-1m",
    )
    assert [item.to_dict() for item in forward.observations] == [
        item.to_dict() for item in reverse.observations
    ]
    first_bar = forward.observations[0]
    assert first_bar.values["high"] == 101.0
    assert first_bar.values["low"] == 99.5
    assert first_bar.values["close"] == 99.5
    assert first_bar.values["trade_count"] == 3


# --------------------------------------------------------------------------- #
# Slice D - feature engine
# --------------------------------------------------------------------------- #


def test_feature_set_is_complete_and_explicit_about_absence():
    warm = compute_features(
        _aligned(_observations(_rows(count=FEATURE_WINDOW_INTERVALS, end_before=CUTOFF))),
        instrument_version=_instrument(),
        evaluated_at_utc=NOW,
    )
    assert set(warm.values) == set(FEATURE_NAMES)
    assert warm.warm
    assert warm.missingness["book_depth_imbalance"] is Missingness.NOT_RETAINED
    assert warm.missingness["return_1m"] is Missingness.PRESENT

    cold = compute_features(
        _aligned(_observations(_rows(count=3, end_before=CUTOFF))),
        instrument_version=_instrument(),
        evaluated_at_utc=NOW,
    )
    assert not cold.warm
    assert cold.values["return_60m"] is None
    assert cold.missingness["return_60m"] is Missingness.MISSING
    assert cold.values["ema_slow_21"] is None


def _coiling_rows(count: int = 120, base: float = 200.0) -> list[IntervalRow]:
    """Oscillation with shrinking amplitude: bandwidth narrows monotonically."""
    first = CUTOFF - timedelta(minutes=count)
    epoch = int(first.timestamp())
    rows: list[IntervalRow] = []
    for index in range(count):
        amplitude = 4.0 * (1.0 - index / count)
        price = base + (amplitude if index % 2 else -amplitude)
        rows.append(
            IntervalRow(
                interval_start_epoch=epoch + 60 * index,
                open=price,
                high=price + amplitude * 0.1 + 0.01,
                low=price - amplitude * 0.1 - 0.01,
                close=price,
                volume=50.0 + index,
                vwap=price,
                trade_count=5,
            )
        )
    return rows


def test_compression_is_a_feature_not_a_lifecycle_state():
    computed = compute_features(
        _aligned(_observations(_coiling_rows())),
        instrument_version=_instrument(),
        evaluated_at_utc=NOW,
    )
    assert computed.values["compression_state"] == CompressionState.COILED.value
    assert computed.values["compression_depth"] > 0.5
    assert computed.values["compression_duration"] >= 1
    assert computed.values["compression_release_score"] == pytest.approx(0.0)
    # D3: compression is never a detector lifecycle state.
    assert "COILED" not in {state.value for state in RestartState}


def test_tied_bandwidth_inflates_the_reused_percentile_definition():
    """Known limitation, recorded rather than hidden.

    ``app.indicators.technical.percentile_rank`` counts values at or below the
    target, so a long run of identical bandwidth reads as mid-distribution
    instead of compressed. Real minute bars rarely tie exactly; a synthetic
    perfectly flat series does, and this test states that consequence
    explicitly so the behaviour is evidence rather than a surprise.
    """
    flat = _rows(count=120, end_before=CUTOFF, step=0.0)
    computed = compute_features(
        _aligned(_observations(flat)),
        instrument_version=_instrument(),
        evaluated_at_utc=NOW,
    )
    assert computed.values["bandwidth_20"] == pytest.approx(0.0)
    assert computed.values["compression_state"] != CompressionState.COILED.value


def test_rolling_features_never_span_a_gap():
    observations = _observations(
        _rows(count=80, end_before=CUTOFF, skip=set(range(40, 60)))
    )
    alignment = _aligned(observations)
    computed = compute_features(
        alignment, instrument_version=_instrument(), evaluated_at_utc=NOW
    )
    assert computed.contiguous_intervals == 20
    assert computed.values["return_60m"] is None
    assert computed.values["missing_intervals"] == 20


def test_features_package_does_not_import_scoring_decisions():
    """Features must not depend on ranking or admission decisions."""
    root = Path(__file__).resolve().parents[1] / "app" / "opip" / "features"
    banned = (
        "signal_quality",
        "opportunity",
        "alert_governor",
        "profit",
        "app.scanner",
    )
    for path in sorted(root.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            if path.name == "parity.py" and "signal_features" in stripped:
                # Parity deliberately reads the production percentile
                # definition in order to report its divergence.
                continue
            for token in banned:
                assert token not in stripped, f"{path.name}: {stripped}"


def test_feature_dag_hash_is_stable_and_identity_bearing():
    assert feature_dag_hash() == feature_dag_hash()
    assert len(feature_dag_hash()) >= 16


# --------------------------------------------------------------------------- #
# Slice E - rolling state, checkpoints, restart semantics
# --------------------------------------------------------------------------- #


def test_restart_semantics_distinguish_cold_start_short_history_and_resume():
    instrument = _instrument()
    cold = initial_state(instrument)
    assert cold.restart_state is RestartState.NEW_LISTING_COLD_START

    short = advance_state(
        cold, _observations(_rows(count=5, end_before=CUTOFF))
    ).state
    assert not short.warm
    assert short.restart_state is RestartState.NEW_LISTING_COLD_START

    warm = advance_state(
        cold,
        _observations(_rows(count=MINIMUM_WARMUP_INTERVALS, end_before=CUTOFF)),
    ).state
    assert warm.warm
    assert warm.restart_state is RestartState.WARM

    resumed = from_checkpoint(to_checkpoint(short, created_at_utc=NOW))
    assert resumed.resumed_from_checkpoint
    assert resumed.restart_state is RestartState.RESTART_WARMUP


def test_material_gap_resets_persistence_evidence():
    instrument = _instrument()
    before = _observations(
        _rows(count=30, end_before=CUTOFF - timedelta(minutes=40)), commit_from=1000
    )
    after = _observations(
        _rows(count=10, end_before=CUTOFF), commit_from=2000
    )
    state = advance_state(initial_state(instrument), before).state
    assert state.persistence_intervals == 30

    advanced = advance_state(state, after)
    assert advanced.gap_detected
    assert advanced.state.interval_count == 10
    assert advanced.state.persistence_intervals == 10
    assert advanced.state.gap_resets == 1


def test_checkpoint_round_trip_and_payload_stays_under_writer_bound():
    from app.opip.canonical.writer import MAX_PAYLOAD_BYTES

    state = advance_state(
        initial_state(_instrument()),
        _observations(
            _rows(
                count=FEATURE_WINDOW_INTERVALS,
                end_before=CUTOFF,
                start_price=12345.678901234,
                step=1.0000000001,
            )
        ),
    ).state
    assert state.interval_count == FEATURE_WINDOW_INTERVALS
    checkpoint = to_checkpoint(state, created_at_utc=NOW)
    size = checkpoint_payload_bytes(checkpoint)
    assert size < MAX_PAYLOAD_BYTES, f"checkpoint payload {size} bytes"

    restored = from_checkpoint(checkpoint)
    assert restored.closes == state.closes
    assert restored.highs == state.highs
    assert restored.lows == state.lows
    assert restored.volumes == state.volumes
    assert restored.first_interval_epoch == state.first_interval_epoch
    assert restored.consumed_input_watermark == state.consumed_input_watermark


def test_retained_window_is_bounded_and_drops_oldest_first():
    state = advance_state(
        initial_state(_instrument()),
        _observations(_rows(count=FEATURE_WINDOW_INTERVALS + 25, end_before=CUTOFF)),
    ).state
    assert state.interval_count == FEATURE_WINDOW_INTERVALS
    assert state.last_interval_epoch == int(CUTOFF.timestamp()) - 60


def test_already_folded_intervals_are_ignored_not_reapplied():
    observations = _observations(_rows(count=10, end_before=CUTOFF))
    state = advance_state(initial_state(_instrument()), observations).state
    again = advance_state(state, observations)
    assert again.applied == 0
    assert again.ignored_stale == 10
    assert again.state.closes == state.closes


# --------------------------------------------------------------------------- #
# Slice F - canonical persistence
# --------------------------------------------------------------------------- #


def test_capture_requires_both_gates(monkeypatch):
    from types import SimpleNamespace

    off = SimpleNamespace(
        opip_feature_bus_mode="off", opip_canonical_writer_mode="shadow"
    )
    half = SimpleNamespace(
        opip_feature_bus_mode="shadow", opip_canonical_writer_mode="off"
    )
    both = SimpleNamespace(
        opip_feature_bus_mode="shadow", opip_canonical_writer_mode="shadow"
    )
    assert resolve_feature_bus_mode(off) == "off"
    assert not feature_bus_capture_enabled(off)
    assert not feature_bus_capture_enabled(half)
    assert feature_bus_capture_enabled(both)


def test_disabled_publisher_records_explicit_no_ops():
    publisher = FeatureBusPublisher(enabled=False)
    observations = _observations(_rows(count=2, end_before=CUTOFF))
    outcomes = publisher.publish_observations(observations)
    assert [item.status for item in outcomes] == ["DISABLED", "DISABLED"]
    assert publisher.summary() == {"DISABLED": 2}
    assert not any(item.committed for item in outcomes)


def test_feature_bus_intents_are_low_priority_without_handoff():
    intent = observation_intent(_observations(_rows(count=1, end_before=CUTOFF))[0])
    assert intent.priority == FEATURE_BUS_PRIORITY == "LOW"
    assert intent.ops_handoff is None
    assert intent.event_type == MARKET_OBSERVATION_RECORDED


def test_oversized_feature_bus_payload_is_refused_before_submission():
    with pytest.raises(ValueError, match="canonical writer bound"):
        build_intent(
            event_type=FEATURE_SNAPSHOT_RECORDED,
            idempotency_key="too-big",
            payload={"blob": "x" * 20_000},
        )


def test_writer_accepts_feature_bus_events_on_a_separate_stream(
    canonical_env, writer_server
):
    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True)
    result = run_cycle(
        _observations(_rows(count=40, end_before=CUTOFF)),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        publisher=publisher,
        source_version="test_source",
    )
    assert result.persisted
    assert {item.status for item in result.outcomes} <= {"OK", "DUPLICATE_OK"}

    with sqlite3.connect(canonical_env["db"]) as conn:
        types = {
            row[0]
            for row in conn.execute("SELECT DISTINCT event_type FROM events")
        }
        streams = dict(
            conn.execute("SELECT stream, local_sequence FROM watermarks")
        )
        handoffs = conn.execute(
            "SELECT COUNT(*) FROM alert_ops_handoffs"
        ).fetchone()[0]
        projections = conn.execute(
            "SELECT COUNT(*) FROM alert_identity_projection"
        ).fetchone()[0]
        schema_version = conn.execute(
            "SELECT schema_version FROM meta WHERE id = 1"
        ).fetchone()[0]

    assert types <= FEATURE_BUS_EVENT_TYPES
    assert FEATURE_BUS_STREAM in streams
    assert STREAM_EARLY_WATCH not in streams
    assert handoffs == 0
    assert projections == 0
    assert schema_version == SCHEMA_VERSION == 1


def test_feature_bus_writes_are_idempotent(canonical_env, writer_server):
    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True)
    snapshot = build_feature_snapshot(
        _aligned(_observations(_rows(count=30, end_before=CUTOFF))),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=ConsumedInputWatermark(1, 1029),
        source_version="test_source",
    )
    first = publisher.publish_snapshot(snapshot)
    second = publisher.publish_snapshot(snapshot)
    assert first.status == "OK"
    assert second.status == "DUPLICATE_OK"
    assert first.event_id == second.event_id
    assert snapshot_idempotency_key(snapshot) == first.idempotency_key


def test_writer_rejects_feature_bus_traffic_that_breaks_its_contract(
    canonical_env, writer_server
):
    client = InProcessWriterClient(writer_server)
    payload = _observations(_rows(count=1, end_before=CUTOFF))[0].to_dict()

    wrong_priority = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="HIGH",
        idempotency_key="fb:priority",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=payload,
    )
    ack = client.submit(wrong_priority)
    assert ack.status == "REJECTED"

    with_handoff = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority=FEATURE_BUS_PRIORITY,
        idempotency_key="fb:handoff",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=payload,
        ops_handoff={"operation": "RECORD"},
    )
    assert client.submit(with_handoff).status == "REJECTED"


def test_projection_rebuild_ignores_feature_bus_events(canonical_env, writer_server):
    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True)
    publisher.publish_observations(_observations(_rows(count=5, end_before=CUTOFF)))
    summary = rebuild_identity_projection(canonical_env["db"])
    assert summary["events_applied"] == 0
    assert summary["identities"] == {}


def test_failed_submission_spools_diagnostically(tmp_path):
    class _Boom:
        def submit(self, intent):
            raise RuntimeError("writer down")

    publisher = FeatureBusPublisher(_Boom(), enabled=True, spool_dir=tmp_path)
    outcome = publisher.publish_observations(
        _observations(_rows(count=1, end_before=CUTOFF))
    )[0]
    assert outcome.status == "SPOOLED"
    assert not outcome.committed
    assert (tmp_path / "feature_bus_rejected.jsonl").exists()


# --------------------------------------------------------------------------- #
# Slice G - replay, parity, ML consumption
# --------------------------------------------------------------------------- #


def test_resume_from_checkpoint_equals_uninterrupted_processing_exactly():
    instrument = _instrument()
    rows = _rows(count=100, end_before=CUTOFF)
    observations = _observations(rows)
    uninterrupted = _aligned(observations)

    first_half = observations[:70]
    deltas = observations[70:]
    checkpoint = to_checkpoint(
        advance_state(initial_state(instrument), first_half).state,
        created_at_utc=NOW,
    )
    resumed = reconstruct_state(checkpoint, deltas)

    equivalence = compare_resumed_state(
        uninterrupted=uninterrupted,
        resumed_state=resumed,
        instrument_version=instrument,
        evaluated_at_utc=NOW,
    )
    assert equivalence.equivalent, equivalence.to_dict()
    assert set(equivalence.compared) == set(ROLLING_FEATURE_NAMES)


def test_snapshot_replay_is_byte_identical_and_detector_ready():
    alignment = _aligned(_observations(_rows(count=60, end_before=CUTOFF)))
    snapshots = [
        build_feature_snapshot(
            alignment,
            instrument_version=_instrument(),
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=ConsumedInputWatermark(1, 1059),
            source_version="test_source",
        )
        for _ in range(3)
    ]
    assert_snapshot_determinism(snapshots)
    replay_input = detector_replay_input(snapshots[0])
    assert replay_input["evaluation_cutoff"] == iso_z(CUTOFF)
    assert set(replay_input["values"]) == set(FEATURE_NAMES)
    assert detector_input_fingerprint(snapshots[0]) == detector_input_fingerprint(
        snapshots[-1]
    )


def test_feature_values_match_production_indicator_math_exactly():
    alignment = _aligned(
        _observations(_rows(count=FEATURE_WINDOW_INTERVALS, end_before=CUTOFF))
    )
    report = compare_against_production_indicators(
        alignment, instrument_version=_instrument(), evaluated_at_utc=NOW
    )
    assert ABSOLUTE_TOLERANCE == 0.0
    assert report.matched, report.to_dict()
    assert len(report.checks) == 5


def test_parity_tolerance_cannot_be_widened_by_configuration():
    assert not hasattr(parity_module, "set_tolerance")
    assert parity_module.ABSOLUTE_TOLERANCE == 0.0


def test_percentile_definition_divergence_is_reported_not_resolved():
    divergence = percentile_definition_divergence([1.0] * 10)
    assert divergence.diverges
    assert divergence.indicator_definition == 100.0
    assert divergence.scan_definition == 50.0
    assert "outside PR3 scope" in divergence.to_dict()["disposition"]


def test_ml_snapshot_projects_onto_the_shared_contract_losslessly():
    from app.opip.ml.contracts import FeatureSnapshot as MlSnapshot, FeatureValue

    stamp = AvailabilityStamp(
        source_at_utc=CUTOFF,
        ingested_at_utc=NOW,
        visible_at_utc=NOW,
        source_version="ml-test",
    )
    ml = MlSnapshot.build(
        episode_id="episode-1",
        candidate_id=None,
        decision_at_utc=NOW,
        canonical_asset_id="CRYPTO:SOL",
        venue="kraken",
        pair="SOLUSD",
        direction="NONE",
        lane="SHADOW",
        regime=None,
        feature_schema_version="features-schema-v1",
        feature_calc_version="features-calc-v1",
        feature_dag_hash="mldag",
        serialization_version=1,
        features=(
            FeatureValue(name="return_1m", value=0.42, availability=stamp),
            FeatureValue(
                name="atr_pct_14", value=None, availability=stamp, missing=True
            ),
        ),
    )
    shared = to_shared_snapshot(
        ml,
        instrument_version=_instrument(),
        consumed_input_watermark=ConsumedInputWatermark(1, 7),
    )
    assert versioning_is_lossless(ml, shared)
    assert shared.values["return_1m"] == 0.42
    assert shared.missingness["atr_pct_14"] is Missingness.MISSING
    assert shared.evaluation_cutoff == CUTOFF
    assert shared.evaluated_at_utc == NOW


def test_pipeline_cycle_produces_snapshot_checkpoint_and_gap_evidence():
    result = run_cycle(
        _observations(_rows(count=40, end_before=CUTOFF, skip={30})),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        source_version="test_source",
    )
    assert result.gap_detected
    assert result.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert result.snapshot.snapshot_id.startswith("FS:1:solusd:")
    assert result.checkpoint.instrument_version_id == result.snapshot.instrument_version_id
    assert result.outcomes == ()  # no publisher supplied, nothing persisted
    replayed = alignment_from_state(result.state)
    assert len(replayed.observations) == result.state.interval_count


def test_incremental_cycle_uses_retained_state_for_rolling_features():
    """A short fetch must not publish cold features when retained state is warm."""
    instrument = _instrument()
    history_end = CUTOFF - timedelta(minutes=5)
    warm = advance_state(
        initial_state(instrument),
        _observations(
            _rows(count=MINIMUM_WARMUP_INTERVALS, end_before=history_end)
        ),
    ).state
    assert warm.restart_state is RestartState.WARM

    # Contiguous continuation that alone is far below warmup, with trailing
    # holes between the batch end and cutoff.
    batch = _observations(
        _rows(count=2, end_before=CUTOFF - timedelta(minutes=3)),
        commit_from=None,
    )
    result = run_cycle(
        batch,
        instrument_version=instrument,
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        state=warm,
        source_version="test_source",
    )
    assert result.alignment.gaps
    assert result.state.restart_state is RestartState.WARM
    assert result.snapshot.restart_state is RestartState.WARM
    assert (
        result.snapshot.values["contiguous_intervals"] >= MINIMUM_WARMUP_INTERVALS
    )
    assert result.snapshot.values["return_1m"] is not None


def test_published_observations_advance_watermark_without_pre_stamped_commit_order(
    canonical_env, writer_server
):
    """Live path: commit_order is absent until the writer ack supplies it."""
    client = InProcessWriterClient(writer_server)
    publisher = FeatureBusPublisher(client, enabled=True)
    result = run_cycle(
        _observations(_rows(count=40, end_before=CUTOFF), commit_from=None),
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        publisher=publisher,
        source_version="test_source",
    )
    assert result.persisted
    assert result.state.consumed_input_watermark != ConsumedInputWatermark.zero()
    assert (
        result.snapshot.consumed_input_watermark
        == result.state.consumed_input_watermark
    )
    assert (
        result.checkpoint.consumed_input_watermark
        == result.state.consumed_input_watermark
    )
    assert ":0-0" not in snapshot_idempotency_key(result.snapshot)
