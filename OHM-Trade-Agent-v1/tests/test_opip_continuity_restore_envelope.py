"""Continuity restore stays inside the existing setup envelope.

Deterministic row counts prove an 8-instrument batch does not grow with
unrelated canonical history. No wall clock, network, or production data.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.jobs.capture_feature_bus_shadow import (  # noqa: E402
    CAPTURE_MATERIALIZE_RESERVE_SECONDS,
    DEFAULT_BUDGET_SECONDS,
    MAX_BUDGET_SECONDS,
    PER_REQUEST_BUDGET_SECONDS,
    capture_feature_bus_shadow,
)
from app.jobs.run_feature_bus_pilot import restore_pilot_continuity_batch  # noqa: E402
from app.opip.canonical import schema as schema_module  # noqa: E402
from app.opip.contracts.events import (  # noqa: E402
    FEATURE_CHECKPOINT_RECORDED,
    MARKET_OBSERVATION_RECORDED,
)
from app.opip.features import checkpoint_store as checkpoint_module  # noqa: E402
from app.opip.features import revision_ledger as ledger_module  # noqa: E402
from test_opip_feature_bus_continuity_batch import (  # noqa: E402
    FEATURE_VERSION,
    _checkpoint_payload,
    _count_scans,
    _insert_event,
    _instrument,
    _make_db,
    _observation_payload,
)
from test_opip_r4_b2_shadow_capture import (  # noqa: E402
    _RecordingClient,
    _provider,
    _settings,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-EVIDENCE-continuity-restore-envelope"
HORIZON = 1_700_000_000
REQUESTED = 8


def _fill(db: Path, *, unrelated: int) -> list:
    versions = [_instrument(index) for index in range(REQUESTED)]
    sequence = 1
    for index in range(unrelated):
        _insert_event(
            db,
            event_id=f"EV:ckpt:other:{index}",
            event_type=FEATURE_CHECKPOINT_RECORDED,
            payload=_checkpoint_payload(f"INSTR:other:{index}"),
            local_sequence=sequence,
        )
        sequence += 1
        _insert_event(
            db,
            event_id=f"EV:obs:other:{index}",
            event_type=MARKET_OBSERVATION_RECORDED,
            payload=_observation_payload(
                f"INSTR:other:{index}", epoch=HORIZON - 86_400 - index
            ),
            local_sequence=sequence,
        )
        sequence += 1
    for version in versions:
        for older in range(4):
            _insert_event(
                db,
                event_id=f"EV:ckpt:{version.base_asset}:{older}",
                event_type=FEATURE_CHECKPOINT_RECORDED,
                payload=_checkpoint_payload(
                    version.instrument_version_id,
                    first_interval_epoch=HORIZON - 240,
                    last_interval_epoch=HORIZON - 180,
                    local_sequence=sequence,
                ),
                local_sequence=sequence,
            )
            sequence += 1
            _insert_event(
                db,
                event_id=f"EV:obs:old:{version.base_asset}:{older}",
                event_type=MARKET_OBSERVATION_RECORDED,
                payload=_observation_payload(
                    version.instrument_version_id,
                    epoch=HORIZON - 240 + (60 * older),
                ),
                local_sequence=sequence,
            )
            sequence += 1
        _insert_event(
            db,
            event_id=f"EV:ckpt:{version.base_asset}:tip",
            event_type=FEATURE_CHECKPOINT_RECORDED,
            payload=_checkpoint_payload(
                version.instrument_version_id,
                first_interval_epoch=HORIZON,
                last_interval_epoch=HORIZON + 120,
                local_sequence=sequence,
            ),
            local_sequence=sequence,
        )
        sequence += 1
        for step in range(3):
            _insert_event(
                db,
                event_id=f"EV:obs:{version.base_asset}:{step}",
                event_type=MARKET_OBSERVATION_RECORDED,
                payload=_observation_payload(
                    version.instrument_version_id, epoch=HORIZON + (60 * step)
                ),
                local_sequence=sequence,
            )
            sequence += 1
    return versions


def test_unrelated_history_does_not_increase_batch_rows(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-001: an 8-instrument restore returns the same rows when unrelated history grows."""
    small_dir = tmp_path / "small"
    large_dir = tmp_path / "large"
    small_dir.mkdir()
    large_dir.mkdir()
    small_db = _make_db(small_dir)
    large_db = _make_db(large_dir)
    small_versions = _fill(small_db, unrelated=20)
    large_versions = _fill(large_db, unrelated=400)
    ids = [version.instrument_version_id for version in small_versions]
    assert [version.instrument_version_id for version in large_versions] == ids

    def _load(db: Path) -> tuple[int, int]:
        checkpoint_stats: dict[str, int] = {}
        ledger_stats: dict[str, int] = {}
        checkpoint_module.load_latest_checkpoint_payloads_batch(
            ids,
            db_path=db,
            feature_version=FEATURE_VERSION,
            stats=checkpoint_stats,
        )
        ledger_module.load_revision_ledgers_batch(
            ids,
            interval_seconds_by_instrument={item: 60 for item in ids},
            since_interval_epoch_by_instrument={item: HORIZON for item in ids},
            db_path=db,
            stats=ledger_stats,
        )
        return checkpoint_stats["rows_returned"], ledger_stats["rows_returned"]

    assert _load(small_db) == (REQUESTED, REQUESTED * 3)
    assert _load(large_db) == (REQUESTED, REQUESTED * 3)


def test_requested_continuity_matches_single_instrument_loader(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-002: the bounded batch restores the same state and in-horizon ledger as the single-instrument loader."""
    db = _make_db(tmp_path)
    versions = _fill(db, unrelated=80)
    ids = [version.instrument_version_id for version in versions]
    states = checkpoint_module.load_rolling_states_batch(
        ids, db_path=db, feature_version=FEATURE_VERSION
    )
    ledgers = ledger_module.load_revision_ledgers_batch(
        ids,
        interval_seconds_by_instrument={item: 60 for item in ids},
        since_interval_epoch_by_instrument={item: HORIZON for item in ids},
        db_path=db,
    )
    for version in versions:
        single_state = checkpoint_module.load_rolling_state(
            version.instrument_version_id,
            db_path=db,
            feature_version=FEATURE_VERSION,
        )
        single_ledger = ledger_module.load_revision_ledger(
            version.instrument_version_id,
            interval_seconds=60,
            since_interval_epoch=HORIZON,
            db_path=db,
        )
        assert single_state is not None
        restored = states[version.instrument_version_id]
        assert restored.first_interval_epoch == single_state.first_interval_epoch
        assert restored.last_interval_epoch == single_state.last_interval_epoch
        assert restored.closes == single_state.closes
        assert (
            ledgers[version.instrument_version_id].entries == single_ledger.entries
        )
        assert set(single_ledger.entries) == {
            HORIZON,
            HORIZON + 60,
            HORIZON + 120,
        }


def test_restored_watermark_stays_at_the_checkpoint_tip(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-003: a restored watermark is the checkpoint tip plus one interval, including when that tip is stale."""
    db = _make_db(tmp_path)
    versions = _fill(db, unrelated=10)
    ids = [version.instrument_version_id for version in versions]

    def _states(instrument_ids, **kwargs):
        kwargs.setdefault("feature_version", FEATURE_VERSION)
        return checkpoint_module.load_rolling_states_batch(
            instrument_ids, db_path=db, **kwargs
        )

    def _ledgers(instrument_ids, **kwargs):
        return ledger_module.load_revision_ledgers_batch(
            instrument_ids, db_path=db, **kwargs
        )

    _states_out, _ledgers_out, watermarks = restore_pilot_continuity_batch(
        versions,
        load_states_batch=_states,
        load_ledgers_batch=_ledgers,
    )
    expected = datetime.fromtimestamp(HORIZON + 180, tz=timezone.utc)
    assert expected < datetime(2026, 10, 2, tzinfo=timezone.utc)
    for instrument_id in ids:
        assert watermarks[instrument_id].through_utc == expected


def test_deadline_expiry_fails_closed_before_acquisition(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-004: an exhausted setup deadline raises and does not acquire."""
    db = _make_db(tmp_path)
    versions = _fill(db, unrelated=5)

    def _states(instrument_ids, **kwargs):
        return checkpoint_module.load_rolling_states_batch(
            instrument_ids, db_path=db, **kwargs
        )

    def _ledgers(instrument_ids, **kwargs):
        raise AssertionError("ledger restore must not start after a checkpoint deadline")

    with pytest.raises(checkpoint_module.CheckpointDeadlineExceeded):
        restore_pilot_continuity_batch(
            versions,
            load_states_batch=_states,
            load_ledgers_batch=_ledgers,
            deadline_monotonic=1.0,
            clock=lambda: 5.0,
        )

    def _exhausted_restore(_versions):
        raise checkpoint_module.CheckpointDeadlineExceeded("setup exhausted")

    acquired = {"called": False}

    class _Source:
        venue = "kraken"
        source_label = "kraken_ohlc"
        interval_seconds = 60

        def fetch_through(self, version, *, watermark, now):
            acquired["called"] = True
            raise AssertionError("acquisition must not run")

    from app.opip.features.publisher import FeatureBusPublisher

    summary = capture_feature_bus_shadow(
        settings=_settings(opip_feature_bus_capture_budget_seconds=45),
        publisher=FeatureBusPublisher(_RecordingClient(), enabled=True, settings=_settings()),
        instrument_provider=_provider(versions[:1]),
        source=_Source(),
        restore_continuity=_exhausted_restore,
    )
    assert acquired["called"] is False
    assert summary.cycles == 0


def test_batch_restore_issues_one_query_per_family(tmp_path, monkeypatch):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-005: eight instruments share one checkpoint query and one observation query."""
    db = _make_db(tmp_path)
    versions = _fill(db, unrelated=30)
    ids = [version.instrument_version_id for version in versions]
    counts: dict[str, int] = {}
    _count_scans(monkeypatch, counts)
    checkpoint_module.load_rolling_states_batch(
        ids, db_path=db, feature_version=FEATURE_VERSION
    )
    ledger_module.load_revision_ledgers_batch(
        ids,
        interval_seconds_by_instrument={item: 60 for item in ids},
        since_interval_epoch_by_instrument={item: HORIZON for item in ids},
        db_path=db,
    )
    assert counts[FEATURE_CHECKPOINT_RECORDED] == 1
    assert counts[MARKET_OBSERVATION_RECORDED] == 1


def test_batch_queries_use_the_instrument_index_and_budgets_stay_fixed(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-006: the actual batch SQL seeks the continuity indexes, and the 45/50/15/10 budgets are unchanged."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    conn = sqlite3.connect(str(db))
    checkpoint_plan = conn.execute(
        "EXPLAIN QUERY PLAN " + checkpoint_module.latest_checkpoint_batch_sql(1),
        (version.instrument_version_id, FEATURE_CHECKPOINT_RECORDED, version.instrument_version_id),
    ).fetchall()
    ledger_plan = conn.execute(
        "EXPLAIN QUERY PLAN " + ledger_module.observation_batch_sql(1, horizon=True),
        (
            MARKET_OBSERVATION_RECORDED,
            version.instrument_version_id,
            HORIZON,
            MARKET_OBSERVATION_RECORDED,
            version.instrument_version_id,
        ),
    ).fetchall()
    conn.close()
    checkpoint_detail = " ".join(str(row) for row in checkpoint_plan)
    ledger_detail = " ".join(str(row) for row in ledger_plan)
    assert "idx_events_type_instrument_order" in checkpoint_detail
    assert "idx_events_observation_utc_epoch" in ledger_detail
    assert "idx_events_observation_noncanonical_time" in ledger_detail
    assert DEFAULT_BUDGET_SECONDS == 45.0
    assert MAX_BUDGET_SECONDS == 50.0
    assert PER_REQUEST_BUDGET_SECONDS == 15.0
    assert CAPTURE_MATERIALIZE_RESERVE_SECONDS == 10.0


def test_malformed_newest_feature_version_does_not_restore_older_checkpoint(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-007: a non-string newest feature_version fails closed instead of restoring the older match."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:older",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(version.instrument_version_id),
        local_sequence=1,
    )
    newer = _checkpoint_payload(version.instrument_version_id, local_sequence=2)
    newer["feature_version"] = 123
    _insert_event(
        db,
        event_id="EV:newer",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=newer,
        local_sequence=2,
    )
    with pytest.raises(checkpoint_module.CheckpointIntegrityError):
        checkpoint_module.load_latest_checkpoint_payload(
            version.instrument_version_id, db_path=db, feature_version=FEATURE_VERSION
        )
    with pytest.raises(checkpoint_module.CheckpointIntegrityError):
        checkpoint_module.load_latest_checkpoint_payloads_batch(
            [version.instrument_version_id],
            db_path=db,
            feature_version=FEATURE_VERSION,
        )


def test_whitespace_feature_version_under_a_different_tip_fails_closed(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-011: whitespace-only feature_version fails closed instead of restoring an older match."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:older",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(version.instrument_version_id),
        local_sequence=1,
    )
    blank = _checkpoint_payload(version.instrument_version_id, local_sequence=2)
    blank["feature_version"] = "   "
    _insert_event(
        db,
        event_id="EV:blank",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=blank,
        local_sequence=2,
    )
    other = _checkpoint_payload(version.instrument_version_id, local_sequence=3)
    other["feature_version"] = "opip-features-other"
    _insert_event(
        db,
        event_id="EV:other",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=other,
        local_sequence=3,
    )
    with pytest.raises(checkpoint_module.CheckpointIntegrityError):
        checkpoint_module.load_latest_checkpoint_payload(
            version.instrument_version_id, db_path=db, feature_version=FEATURE_VERSION
        )
    with pytest.raises(checkpoint_module.CheckpointIntegrityError):
        checkpoint_module.load_latest_checkpoint_payloads_batch(
            [version.instrument_version_id],
            db_path=db,
            feature_version=FEATURE_VERSION,
        )


@pytest.mark.parametrize(
    "source_event_time",
    ["2021-02-30T00:00:00Z", "2021-02-29T00:00:00Z"],
)
def test_impossible_canonical_shaped_utc_date_fails_closed(tmp_path, source_event_time):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-012: an impossible UTC date is not horizon-pruned."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    bad = _observation_payload(version.instrument_version_id, epoch=HORIZON - 86_400)
    bad["source_event_time"] = source_event_time
    _insert_event(
        db,
        event_id="EV:bad",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=bad,
        local_sequence=1,
    )
    _insert_event(
        db,
        event_id="EV:good",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=HORIZON),
        local_sequence=2,
    )
    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledger(
            version.instrument_version_id,
            interval_seconds=60,
            since_interval_epoch=HORIZON,
            db_path=db,
        )
    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledgers_batch(
            [version.instrument_version_id],
            interval_seconds_by_instrument={version.instrument_version_id: 60},
            since_interval_epoch_by_instrument={version.instrument_version_id: HORIZON},
            db_path=db,
        )


def test_valid_leap_day_stays_on_the_horizon_seek(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-012: a real leap-day timestamp keeps normal horizon semantics."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    leap = _observation_payload(version.instrument_version_id, epoch=HORIZON - 86_400)
    leap["source_event_time"] = "2020-02-29T00:00:00Z"
    _insert_event(
        db,
        event_id="EV:leap",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=leap,
        local_sequence=1,
    )
    _insert_event(
        db,
        event_id="EV:good",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=HORIZON),
        local_sequence=2,
    )
    single = ledger_module.load_revision_ledger(
        version.instrument_version_id,
        interval_seconds=60,
        since_interval_epoch=HORIZON,
        db_path=db,
    )
    stats: dict[str, int] = {}
    batch = ledger_module.load_revision_ledgers_batch(
        [version.instrument_version_id],
        interval_seconds_by_instrument={version.instrument_version_id: 60},
        since_interval_epoch_by_instrument={version.instrument_version_id: HORIZON},
        db_path=db,
        stats=stats,
    )
    assert set(single.entries) == {HORIZON}
    assert set(batch[version.instrument_version_id].entries) == {HORIZON}
    assert stats["rows_returned"] == 1


@pytest.mark.parametrize(
    "source_event_time",
    [123, "2020-01-01T00:00:00", "2020-01-01T00:00:00+05:00", "not-a-timestamp"],
)
def test_noncanonical_pre_horizon_source_time_fails_closed(tmp_path, source_event_time):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-008: numeric, naive, non-UTC, and malformed source times fail closed before horizon pruning."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    bad = _observation_payload(version.instrument_version_id, epoch=HORIZON - 86_400)
    bad["source_event_time"] = source_event_time
    _insert_event(
        db,
        event_id="EV:bad",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=bad,
        local_sequence=1,
    )
    _insert_event(
        db,
        event_id="EV:good",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=HORIZON),
        local_sequence=2,
    )
    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledger(
            version.instrument_version_id,
            interval_seconds=60,
            since_interval_epoch=HORIZON,
            db_path=db,
        )
    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledgers_batch(
            [version.instrument_version_id],
            interval_seconds_by_instrument={version.instrument_version_id: 60},
            since_interval_epoch_by_instrument={version.instrument_version_id: HORIZON},
            db_path=db,
        )


def _history(db: Path, depth: int) -> list:
    versions = [_instrument(index) for index in range(REQUESTED)]
    sequence = 1
    for version in versions:
        for older in range(depth):
            _insert_event(
                db,
                event_id=f"EV:ckpt:{version.base_asset}:{older}",
                event_type=FEATURE_CHECKPOINT_RECORDED,
                payload=_checkpoint_payload(
                    version.instrument_version_id,
                    first_interval_epoch=HORIZON - 240,
                    last_interval_epoch=HORIZON - 180,
                    local_sequence=sequence,
                ),
                local_sequence=sequence,
            )
            sequence += 1
            _insert_event(
                db,
                event_id=f"EV:old:{version.base_asset}:{older}",
                event_type=MARKET_OBSERVATION_RECORDED,
                payload=_observation_payload(
                    version.instrument_version_id,
                    epoch=HORIZON - 60 * (older + 1),
                ),
                local_sequence=sequence,
            )
            sequence += 1
        _insert_event(
            db,
            event_id=f"EV:tip:{version.base_asset}",
            event_type=FEATURE_CHECKPOINT_RECORDED,
            payload=_checkpoint_payload(
                version.instrument_version_id,
                first_interval_epoch=HORIZON,
                last_interval_epoch=HORIZON + 120,
                local_sequence=sequence,
            ),
            local_sequence=sequence,
        )
        sequence += 1
        for step in range(3):
            _insert_event(
                db,
                event_id=f"EV:now:{version.base_asset}:{step}",
                event_type=MARKET_OBSERVATION_RECORDED,
                payload=_observation_payload(
                    version.instrument_version_id, epoch=HORIZON + 60 * step
                ),
                local_sequence=sequence,
            )
            sequence += 1
    return versions


def _vm_steps(db: Path, versions: list) -> int:
    ids = [version.instrument_version_id for version in versions]
    checkpoint_stats = {"measure_vm_steps": 1}
    ledger_stats = {"measure_vm_steps": 1}
    checkpoint_module.load_latest_checkpoint_payloads_batch(
        ids, db_path=db, feature_version=FEATURE_VERSION, stats=checkpoint_stats
    )
    ledger_module.load_revision_ledgers_batch(
        ids,
        interval_seconds_by_instrument={item: 60 for item in ids},
        since_interval_epoch_by_instrument={item: HORIZON for item in ids},
        db_path=db,
        stats=ledger_stats,
    )
    assert checkpoint_stats["rows_returned"] == REQUESTED
    assert ledger_stats["rows_returned"] == REQUESTED * 3
    return int(checkpoint_stats["vm_steps"]) + int(ledger_stats["vm_steps"])


def test_requested_instrument_history_depth_does_not_scale_vm_steps(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-009: the same eight instruments do not make the production queries walk their full history."""
    shallow_dir = tmp_path / "shallow"
    deep_dir = tmp_path / "deep"
    shallow_dir.mkdir()
    deep_dir.mkdir()
    shallow = _history(_make_db(shallow_dir), 4)
    deep = _history(_make_db(deep_dir), 400)
    shallow_steps = _vm_steps(shallow_dir / "canonical.db", shallow)
    deep_steps = _vm_steps(deep_dir / "canonical.db", deep)
    assert deep_steps <= shallow_steps * 3


def test_index_build_uses_the_ddl_and_stays_inside_writer_start(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-010: index creation is the DDL statement, a scaled build finishes inside the writer start period, and malformed JSON fails the build."""
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    marker = compose.index("app.opip.canonical.healthcheck")
    section = compose[marker:marker + 400]
    assert "timeout: 5s" in section
    assert "retries: 3" in section
    assert "start_period: 20s" in section
    for statement in (
        schema_module.INDEX_EVENTS_TYPE_INSTRUMENT_ORDER,
        schema_module.INDEX_EVENTS_OBSERVATION_UTC_EPOCH,
        schema_module.INDEX_EVENTS_OBSERVATION_NONCANONICAL_TIME,
    ):
        assert statement in schema_module.DDL

    db = tmp_path / "scaled.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema_module.DDL)
    for index_name in (
        "idx_events_type_instrument_order",
        "idx_events_observation_utc_epoch",
        "idx_events_observation_noncanonical_time",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {index_name}")
    rows = []
    for index in range(40_000):
        rows.append(
            (
                f"E{index}",
                schema_module.SCHEMA_VERSION,
                MARKET_OBSERVATION_RECORDED,
                index + 1,
                f"K{index}",
                json.dumps(
                    {
                        "instrument_version_id": f"INST{index % 8}",
                        "source_event_time": "2024-01-01T00:00:00Z",
                        "feature_version": FEATURE_VERSION,
                    }
                ),
            )
        )
    conn.executemany(
        "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
        "local_sequence, recorded_at, idempotency_key, payload_json) "
        "VALUES (?, ?, ?, 1, ?, '2026-01-01T00:00:00Z', ?, ?)",
        rows,
    )
    started = time.perf_counter()
    for statement in (
        schema_module.INDEX_EVENTS_TYPE_INSTRUMENT_ORDER,
        schema_module.INDEX_EVENTS_OBSERVATION_UTC_EPOCH,
        schema_module.INDEX_EVENTS_OBSERVATION_NONCANONICAL_TIME,
    ):
        conn.execute(statement)
    conn.commit()
    elapsed = time.perf_counter() - started
    conn.close()
    assert elapsed < 20.0

    poisoned = tmp_path / "poison.db"
    poison = sqlite3.connect(str(poisoned))
    poison.executescript(schema_module.DDL)
    for index_name in (
        "idx_events_paper_trade",
        "idx_events_type_instrument_order",
        "idx_events_observation_utc_epoch",
        "idx_events_observation_noncanonical_time",
    ):
        poison.execute(f"DROP INDEX IF EXISTS {index_name}")
    poison.execute(
        "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
        "local_sequence, recorded_at, idempotency_key, payload_json) "
        "VALUES ('Ebad', ?, ?, 1, 1, '2026-01-01T00:00:00Z', 'Kbad', ?)",
        (schema_module.SCHEMA_VERSION, FEATURE_CHECKPOINT_RECORDED, "this is not json"),
    )
    with pytest.raises(sqlite3.OperationalError):
        poison.execute(schema_module.INDEX_EVENTS_TYPE_INSTRUMENT_ORDER)
    poison.close()
