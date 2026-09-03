from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.exchanges.kraken_identity import canonicalize_asset, split_canonical_pair
from app.opip.decision import store
from app.opip.decision.models import GATE_ORDER, GateName, ReasonClass, ReasonCode
from app.opip.decision.screening import (
    ScannerType,
    ScreeningEvaluation,
    ScreeningOutcome,
)
from app.opip.identity import (
    IdentityResolutionStatus,
    InstrumentClass,
    resolve_venue_instrument_identity,
)
from app.opip.storage import bounded_jsonl
from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    encode_row,
    parse_json_object_line,
)
from app.scanner.directional_candidates import select_directional_candidates
from app.scanner.models import MarketSnapshot
from app.services.movement_discovery_v2 import discover_coarse_movers


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _identity(raw: str):
    return resolve_venue_instrument_identity(
        raw,
        canonicalize_asset=canonicalize_asset,
        split_canonical_pair=split_canonical_pair,
        resolved_at_utc=NOW,
    )


def _snapshot(**overrides):
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=90.0,
        rsi=60.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=2.0,
        technical_score=90,
        trend="bullish",
        primary_pair="XXBTZUSD",
        underlying_asset="BTC",
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def test_identity_preserves_raw_kraken_alias_and_resolves_canonical_asset():
    identity = _identity("XXBTZUSD")
    assert identity.raw_identifier == "XXBTZUSD"
    assert identity.canonical_asset_id == "BTC"
    assert identity.venue_instrument_symbol == "BTCUSD"
    assert identity.resolution_status is IdentityResolutionStatus.REFERENCE_ALIAS
    assert identity.instrument_class is InstrumentClass.SPOT


@pytest.mark.parametrize(
    ("raw", "classification"),
    [
        ("ETH2USD", InstrumentClass.STAKED),
        ("ETH.SUSD", InstrumentClass.STAKED),
        ("BTC3LUSD", InstrumentClass.LEVERAGED_TOKEN),
        ("BTC.DUSD", InstrumentClass.DERIVATIVE),
    ],
)
def test_non_spot_instruments_fail_closed(raw, classification):
    identity = _identity(raw)
    assert identity.canonical_asset_id is None
    assert identity.instrument_class is classification
    assert identity.resolution_status is IdentityResolutionStatus.CLASSIFIED_UNRESOLVED


def test_screening_round_trip_identity_does_not_depend_on_direction():
    row = ScreeningEvaluation(
        observed_at=NOW,
        scan_id="OPIPS:test",
        scanner_type=ScannerType.BROAD_SEARCH,
        venue_instrument=_identity("XXBTZUSD"),
        outcome=ScreeningOutcome.ADVANCED,
        long_score=91,
        short_score=22,
        advanced_direction="LONG",
        metadata={"universe_count": 200},
    )
    restored = ScreeningEvaluation.from_dict(row.to_dict())
    assert restored == row
    assert restored.identity_tuple == (
        NOW.isoformat(),
        "OPIPS:test",
        "BROAD_SEARCH",
        "BTCUSD",
    )


def test_directional_callback_is_observational_and_fail_soft(caplog):
    snapshot = _snapshot()

    def hostile(captured, *_args):
        captured.technical_score = -1
        raise RuntimeError("telemetry down")

    selected = select_directional_candidates(
        [snapshot],
        on_evaluated=hostile,
        scan_id="OPIPS:test",
    )
    assert len(selected) == 1
    assert selected[0].technical_score == 90
    assert "scan_id=OPIPS:test" in caplog.text


class _AttritionKraken:
    def get_asset_pairs(self):
        return {
            "FASTUSD": {
                "altname": "FASTUSD",
                "wsname": "FAST/USD",
                "status": "online",
            },
            "SLOWUSD": {
                "altname": "SLOWUSD",
                "wsname": "SLOW/USD",
                "status": "online",
            },
            "MISSUSD": {
                "altname": "MISSUSD",
                "wsname": "MISS/USD",
                "status": "online",
            },
        }

    def get_tickers(self, _pair_ids):
        return {
            "FASTUSD": {
                "last": 10,
                "high_24h": 10.1,
                "low_24h": 8,
                "volume_24h": 100_000,
            },
            "SLOWUSD": {
                "last": 10,
                "high_24h": 12,
                "low_24h": 9.9,
                "volume_24h": 100_000,
            },
        }


def test_coarse_attrition_is_machine_readable_and_counts_actual_universe():
    rows = []
    movers = discover_coarse_movers(
        _AttritionKraken(),
        on_evaluated=rows.append,
        scan_id="OPIPS:early",
    )
    assert [item.base_asset for item in movers] == ["FAST"]
    by_raw = {row["raw_identifier"]: row for row in rows}
    assert by_raw["MISSUSD"]["outcome"] == "DATA_UNAVAILABLE"
    assert by_raw["SLOWUSD"]["outcome"] == "BELOW_COARSE_THRESHOLD"
    assert "maximum_distance_from_24h_high_pct" in (
        by_raw["SLOWUSD"]["metadata"]["failed_predicates"]
    )
    assert by_raw["SLOWUSD"]["metadata"]["universe_count"] == 3


def test_archive_retry_reuses_published_content_before_compacting_hot(
    tmp_path, monkeypatch
):
    hot = tmp_path / "evidence.jsonl"
    archive = BoundedJsonlArchive(
        data_file=hot,
        archive_dir=tmp_path / "archive",
        max_bytes=10_000,
        keep_lines=3,
        archive_prefix="evidence",
        parse_line=parse_json_object_line,
    )
    archive.append_encoded_many_locked(encode_row({"row": i}) for i in range(5))

    real_write = bounded_jsonl.write_atomic_lines

    def fail_hot_replace(path, lines):
        if path == hot:
            raise OSError("simulated crash before HOT replace")
        return real_write(path, lines)

    monkeypatch.setattr(bounded_jsonl, "write_atomic_lines", fail_hot_replace)
    with pytest.raises(OSError, match="simulated crash"):
        archive.compact_locked()
    assert len(list((tmp_path / "archive").glob("evidence-*.jsonl.gz"))) == 1
    assert len(list(archive.iter_hot_rows())) == 5

    # Repeated retries while HOT replacement is still unavailable must not
    # create an additional immutable segment for identical content.
    with pytest.raises(OSError, match="simulated crash"):
        archive.compact_locked()
    assert len(list((tmp_path / "archive").glob("evidence-*.jsonl.gz"))) == 1

    monkeypatch.setattr(bounded_jsonl, "write_atomic_lines", real_write)
    archive.compact_locked()
    assert len(list((tmp_path / "archive").glob("evidence-*.jsonl.gz"))) == 1
    assert len(list(archive.iter_archive_rows())) == 3
    assert len(list(archive.iter_hot_rows())) == 2


def test_archive_window_selection_uses_day_shards_without_history_scan(
    tmp_path,
    monkeypatch,
):
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = BoundedJsonlArchive(
        data_file=hot,
        archive_dir=tmp_path / "screening_evaluations_archive",
        max_bytes=100_000,
        keep_lines=2,
        archive_prefix="screening_evaluations",
        parse_line=parse_json_object_line,
        visible_at=lambda row: datetime.fromisoformat(row["observed_at"]),
    )
    archive.append_encoded_many_locked(
        encode_row(
            {
                "observed_at": (
                    NOW + timedelta(seconds=index)
                ).isoformat(),
                "row": index,
            }
        )
        for index in range(5)
    )
    archived = archive.compact_locked()
    assert archived is not None
    assert archive.window_index_state_file.exists()

    path_type = type(archive.manifest_file)
    real_read_text = path_type.read_text

    def guarded_read_text(self, *args, **kwargs):
        if self == archive.manifest_file:
            raise AssertionError(
                "steady-state window selection read the full master manifest"
            )
        return real_read_text(self, *args, **kwargs)

    def forbidden_rglob(self, *_args, **_kwargs):
        raise AssertionError(
            "steady-state window selection enumerated lifetime archives"
        )

    monkeypatch.setattr(path_type, "read_text", guarded_read_text)
    monkeypatch.setattr(path_type, "rglob", forbidden_rglob)

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert selection.truncated is False
    assert selection.paths == (archived,)


def test_archive_window_index_rebuilds_legacy_master_manifest(tmp_path):
    hot = tmp_path / "funnel_events.jsonl"
    archive = BoundedJsonlArchive(
        data_file=hot,
        archive_dir=tmp_path / "funnel_events_archive",
        max_bytes=100_000,
        keep_lines=2,
        archive_prefix="funnel_events",
        parse_line=parse_json_object_line,
        visible_at=lambda row: datetime.fromisoformat(row["observed_at"]),
    )
    archive.append_encoded_many_locked(
        encode_row(
            {
                "observed_at": (
                    NOW + timedelta(seconds=index)
                ).isoformat(),
                "row": index,
            }
        )
        for index in range(5)
    )
    archived = archive.compact_locked()
    assert archived is not None

    # Simulate a pre-window-index production archive copied to a new worker.
    import shutil
    shutil.rmtree(archive.window_index_dir)
    assert not archive.window_index_state_file.exists()

    assert archive.ensure_window_index_locked() is True
    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert selection.paths == (archived,)


def test_capacity_health_uses_measured_universe_and_preserves_reserve(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        store.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            total=50_884_108_288,
            used=12_720_566_272,
            free=38_163_542_016,
        ),
    )
    health = store.retention_capacity_health(
        qualification_dir=tmp_path,
        observed_early_watch_universe=1_000,
    )
    assert health.observed_early_watch_universe == 1_000
    assert health.capacity_status == "HEALTHY"
    assert health.recovery_window_proven is True
    assert health.recoverable_days_estimate >= 14
    assert health.projected_14d_bytes is not None
    assert health.current_free_bytes > health.minimum_free_reserve_bytes


def test_capacity_is_unknown_without_an_observed_universe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        store.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000_000, used=100, free=999_900),
    )
    health = store.retention_capacity_health(qualification_dir=tmp_path)
    assert health.capacity_status == "UNKNOWN"
    assert health.recovery_window_proven is False
    assert health.projected_14d_bytes is None


def test_capital_gate_is_ordered_and_policy_attributed():
    assert GATE_ORDER.index(GateName.CAPITAL_PORTFOLIO_GATE) == (
        GATE_ORDER.index(GateName.FINAL_QUALIFICATION) - 1
    )
    assert ReasonCode.NO_CAPITAL.value == "NO_CAPITAL"
    from app.opip.decision.models import reason_class

    assert reason_class(ReasonCode.NO_CAPITAL) is ReasonClass.POLICY



def test_capacity_projection_includes_recovered_long_funnel_states(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        store.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            total=100_000_000_000,
            used=10_000_000_000,
            free=90_000_000_000,
        ),
    )
    universe = 100
    health = store.retention_capacity_health(
        qualification_dir=tmp_path,
        observed_early_watch_universe=universe,
    )

    assert store.MAX_FUNNEL_CANDIDATES_PER_SCAN == 13
    screening_rows_per_day = (
        store.BROAD_SEARCH_MAX_INSTRUMENTS * store.BROAD_SEARCH_SCANS_PER_DAY
        + store.EARLY_WATCH_DEEP_MAX_INSTRUMENTS
        * store.EARLY_WATCH_SCANS_PER_DAY
        + universe * store.EARLY_WATCH_SCANS_PER_DAY
    )
    daily_bytes = store.STAGE0_CAPACITY_SAFETY_FACTOR * (
        screening_rows_per_day * store.SCREENING_P95_ROW_BYTES
        + 13
        * store.BROAD_SEARCH_SCANS_PER_DAY
        * store.FUNNEL_P95_ROW_BYTES
        + store.BROAD_SEARCH_SCANS_PER_DAY * store.SUMMARY_P95_ROW_BYTES
    )
    assert health.projected_14d_bytes == int(
        daily_bytes * store.STAGE0_REQUIRED_RECOVERY_DAYS
    )
