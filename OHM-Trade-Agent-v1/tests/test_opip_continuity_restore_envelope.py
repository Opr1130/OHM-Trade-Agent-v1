"""Continuity restore stays inside the existing setup envelope.

Deterministic row counts prove an 8-instrument batch does not grow with
unrelated canonical history. No wall clock, network, or production data.
"""

from __future__ import annotations

import sqlite3
import sys
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
    checkpoint_module.load_rolling_states_batch(ids, db_path=db)
    ledger_module.load_revision_ledgers_batch(
        ids,
        interval_seconds_by_instrument={item: 60 for item in ids},
        since_interval_epoch_by_instrument={item: HORIZON for item in ids},
        db_path=db,
    )
    assert counts[FEATURE_CHECKPOINT_RECORDED] == 1
    assert counts[MARKET_OBSERVATION_RECORDED] == 1


def test_batch_queries_use_the_instrument_index_and_budgets_stay_fixed(tmp_path):
    """ATDD-EVIDENCE-continuity-restore-envelope/AC-006: the bounded reads seek the instrument index, and the 45/50/15/10 budgets are unchanged."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:one",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=HORIZON),
    )
    conn = sqlite3.connect(str(db))
    plan = conn.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT payload_json
        FROM events
        WHERE event_type = ?
          AND json_extract(payload_json, '$.instrument_version_id') = ?
        """,
        (MARKET_OBSERVATION_RECORDED, version.instrument_version_id),
    ).fetchall()
    conn.close()
    detail = " ".join(str(row) for row in plan)
    assert "idx_events_type_instrument_order" in detail
    assert DEFAULT_BUDGET_SECONDS == 45.0
    assert MAX_BUDGET_SECONDS == 50.0
    assert PER_REQUEST_BUDGET_SECONDS == 15.0
    assert CAPTURE_MATERIALIZE_RESERVE_SECONDS == 10.0
