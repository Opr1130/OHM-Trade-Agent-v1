"""R4-B2 continuity batch restore: acceptance guards (AC-018).

Proves the production/default continuity restoration scans each canonical event
family ONCE for the whole requested instrument batch (instead of once per
instrument), reconstructs per-instrument state/ledger/watermark, preserves the
existing validation/filter/ordering semantics, keeps the historical
single-instrument injected-callback seam intact, and emits bounded telemetry.

Every fixture is a deterministic literal. No network, wall clock, random value or
production data is read.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.canonical import schema as schema_module  # noqa: E402
from app.opip.contracts.events import (  # noqa: E402
    FEATURE_CHECKPOINT_RECORDED,
    MARKET_OBSERVATION_RECORDED,
)
from app.opip.contracts.identity import InstrumentVersion  # noqa: E402
from app.opip.features import checkpoint_store as checkpoint_module  # noqa: E402
from app.opip.features import revision_ledger as ledger_module  # noqa: E402
from app.jobs.run_feature_bus_pilot import (  # noqa: E402
    restore_pilot_continuity,
    restore_pilot_continuity_batch,
)

pytestmark = pytest.mark.acceptance

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
FEATURE_VERSION = "opip-features-v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _instrument(n: int) -> InstrumentVersion:
    return InstrumentVersion(
        venue="synthetic",
        base_asset=f"SYN{n}",
        quote_currency="USD",
        venue_instrument_id=f"SYNTHETIC-SYN{n}USD",
        version=1,
        reference_data_version="opip-evidence-identity-v1",
        observed_at_utc=NOW,
        price_decimals=2,
        tick_size=0.01,
        min_order_size=0.2,
    )


def _checkpoint_payload(
    instrument_version_id: str,
    *,
    feature_version: str = FEATURE_VERSION,
    first_interval_epoch: int = 1_700_000_000,
    last_interval_epoch: int = 1_700_000_060,
    interval_seconds: int = 60,
    history_epoch: int = 1,
    local_sequence: int = 1,
) -> dict:
    # Real RollingState derives ``last_interval_epoch`` from the RETAINED series
    # (``first_interval_epoch + interval_seconds * (interval_count - 1)``), so a
    # payload that merely stores ``last_interval_epoch`` restores a state with no
    # last interval. Build internally consistent, equal-length retained arrays so
    # the restored state genuinely carries the requested endpoints.
    count = ((last_interval_epoch - first_interval_epoch) // interval_seconds) + 1
    if count < 1:
        count = 1
    opens = [100.0 + float(index) for index in range(count)]
    closes = [100.5 + float(index) for index in range(count)]
    highs = [101.0 + float(index) for index in range(count)]
    lows = [99.5 + float(index) for index in range(count)]
    volumes = [10.0 + float(index) for index in range(count)]
    revisions = [1 for _ in range(count)]
    opens_known = [True for _ in range(count)]
    content_fingerprints = [f"fp-{index:04d}" for index in range(count)]
    return {
        "instrument_version_id": instrument_version_id,
        "venue_instrument_id": "SYNTHETIC-SYN0USD",
        "feature_version": feature_version,
        "consumed_input_watermark": {
            "history_epoch": history_epoch,
            "local_sequence": local_sequence,
        },
        "rolling_state": {
            "venue": "synthetic",
            "first_interval_epoch": first_interval_epoch,
            "interval_seconds": interval_seconds,
            "opens": opens,
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
            "revisions": revisions,
            "opens_known": opens_known,
            "content_fingerprints": content_fingerprints,
        },
        "restart_state": "NEW_LISTING_COLD_START",
        "reconstruction_dependencies": ["fixed_interval_aggregate:60s"],
        "created_at_utc": "2026-01-01T00:00:00Z",
        "schema_version": 1,
    }


def _observation_payload(
    instrument_version_id: str,
    *,
    epoch: int,
    revision: int = 1,
    interval_seconds: int = 60,
    values: dict | None = None,
) -> dict:
    moment = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return {
        "instrument_version_id": instrument_version_id,
        "aggregate_interval_seconds": interval_seconds,
        "source_event_time": moment.isoformat().replace("+00:00", "Z"),
        "values": values if values is not None else {"close": 1.0},
        "revision": revision,
        "observation_id": (
            f"OBS:{instrument_version_id}:{interval_seconds}s:{epoch}:{revision}"
        ),
        "receipt_time": "2026-01-01T00:00:00Z",
    }


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "canonical.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema_module.DDL)
    conn.execute(
        "INSERT INTO meta (id, schema_version, history_epoch, next_local_sequence, "
        "created_at, updated_at) VALUES (1, ?, 1, 1, "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (schema_module.SCHEMA_VERSION,),
    )
    conn.commit()
    conn.close()
    return db


def _insert_event(
    db: Path,
    *,
    event_id: str,
    event_type: str,
    payload: dict,
    history_epoch: int = 1,
    local_sequence: int = 1,
) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
        "local_sequence, recorded_at, idempotency_key, payload_json) "
        "VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', ?, ?)",
        (
            event_id,
            schema_module.SCHEMA_VERSION,
            event_type,
            history_epoch,
            local_sequence,
            f"K:{event_id}",
            json.dumps(payload),
        ),
    )
    conn.commit()
    conn.close()


class _CountingConnection:
    """Wraps a real connection and counts event-family scans by event_type."""

    def __init__(self, inner, counts: dict[str, int]) -> None:
        self._inner = inner
        self._counts = counts

    def execute(self, sql, params=()):
        if "FROM events" in sql and "WHERE event_type" in sql:
            event_type = params[0] if params else None
            if event_type is not None:
                self._counts[str(event_type)] = self._counts.get(str(event_type), 0) + 1
        return self._inner.execute(sql, params)

    def set_progress_handler(self, *args, **kwargs):
        return self._inner.set_progress_handler(*args, **kwargs)

    def close(self):
        return self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _count_scans(monkeypatch, counts: dict[str, int]) -> None:
    real_connect = schema_module.connect

    def _connect(*args, **kwargs):
        return _CountingConnection(real_connect(*args, **kwargs), counts)

    monkeypatch.setattr(schema_module, "connect", _connect)


# ---------------------------------------------------------------------------
# AC-018 checkpoint batch
# ---------------------------------------------------------------------------


def test_ac_018_checkpoint_batch_restores_multiple_instruments_in_one_scan(
    tmp_path, monkeypatch
):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: multiple requested instruments restore correctly from ONE FEATURE_CHECKPOINT_RECORDED scan, even with significant unrelated checkpoint history."""
    db = _make_db(tmp_path)
    v0, v1, v2 = _instrument(0), _instrument(1), _instrument(2)
    # Significant unrelated checkpoint history (other instruments).
    for i in range(50):
        _insert_event(
            db,
            event_id=f"EV:other:{i}",
            event_type=FEATURE_CHECKPOINT_RECORDED,
            payload=_checkpoint_payload(f"INSTR:other:{i}"),
            local_sequence=i + 1,
        )
    _insert_event(
        db,
        event_id="EV:v0",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(v0.instrument_version_id, local_sequence=100),
        local_sequence=100,
    )
    _insert_event(
        db,
        event_id="EV:v1",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(v1.instrument_version_id, local_sequence=101),
        local_sequence=101,
    )
    _insert_event(
        db,
        event_id="EV:v2",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(v2.instrument_version_id, local_sequence=102),
        local_sequence=102,
    )

    counts: dict[str, int] = {}
    _count_scans(monkeypatch, counts)

    batch = checkpoint_module.load_latest_checkpoint_payloads_batch(
        [v0.instrument_version_id, v1.instrument_version_id, v2.instrument_version_id],
        db_path=db,
        feature_version=FEATURE_VERSION,
    )
    assert set(batch) == {
        v0.instrument_version_id,
        v1.instrument_version_id,
        v2.instrument_version_id,
    }
    # Exactly ONE canonical checkpoint event-family scan for the whole batch.
    assert counts.get(FEATURE_CHECKPOINT_RECORDED) == 1

    # Observational match against the single-instrument API.
    for version in (v0, v1, v2):
        single = checkpoint_module.load_latest_checkpoint_payload(
            version.instrument_version_id,
            db_path=db,
            feature_version=FEATURE_VERSION,
        )
        assert batch[version.instrument_version_id] == single


def test_ac_018_checkpoint_batch_preserves_feature_version_semantics(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a checkpoint for a different feature version is ignored, exactly as the single-instrument loader ignores it."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:old",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(
            version.instrument_version_id, feature_version="opip-features-v0"
        ),
        local_sequence=1,
    )
    batch = checkpoint_module.load_latest_checkpoint_payloads_batch(
        [version.instrument_version_id],
        db_path=db,
        feature_version=FEATURE_VERSION,
    )
    assert batch == {}


def test_ac_018_checkpoint_batch_malformed_evidence_matches_single_loader(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a malformed committed checkpoint fails closed in the batch loader exactly as it does in the single-instrument loader."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    # A payload that is not a JSON object.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
        "local_sequence, recorded_at, idempotency_key, payload_json) "
        "VALUES (?, ?, ?, 1, 1, '2026-01-01T00:00:00Z', 'K:bad', ?)",
        ("EV:bad", schema_module.SCHEMA_VERSION, FEATURE_CHECKPOINT_RECORDED, "[1,2,3]"),
    )
    conn.commit()
    conn.close()

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


def test_ac_018_checkpoint_preserves_canonical_validation_precedence(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: canonical per-row validation order is preserved.

    The FIRST canonical row is a JSON object whose checkpoint identity/version
    metadata is invalid (historically raising
    ``CheckpointIntegrityError("...must declare non-empty string...")``); a LATER
    row is malformed in a DIFFERENT way (a non-object JSON payload). Both the
    single-instrument and batch APIs must fail on the EARLIER canonical
    identity/version violation, not on the later row. This is an
    exception-precedence test, not merely "both eventually fail".
    """
    db = _make_db(tmp_path)
    version = _instrument(0)
    # Earlier canonical row: JSON object with empty identity/version metadata.
    _insert_event(
        db,
        event_id="EV:early",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(""),
        local_sequence=1,
    )
    # Later canonical row: a non-object JSON payload (a different malformation).
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
        "local_sequence, recorded_at, idempotency_key, payload_json) "
        "VALUES (?, ?, ?, 1, 2, '2026-01-01T00:00:00Z', 'K:late', ?)",
        ("EV:late", schema_module.SCHEMA_VERSION, FEATURE_CHECKPOINT_RECORDED, "[1,2,3]"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(checkpoint_module.CheckpointIntegrityError) as single_exc:
        checkpoint_module.load_latest_checkpoint_payload(
            version.instrument_version_id, db_path=db, feature_version=FEATURE_VERSION
        )
    assert "must declare non-empty string" in str(single_exc.value)

    with pytest.raises(checkpoint_module.CheckpointIntegrityError) as batch_exc:
        checkpoint_module.load_latest_checkpoint_payloads_batch(
            [version.instrument_version_id],
            db_path=db,
            feature_version=FEATURE_VERSION,
        )
    assert "must declare non-empty string" in str(batch_exc.value)


def test_ac_018_checkpoint_batch_deadline_returns_no_partial_result(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a deadline failure raises and returns no partial mapping."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:v0",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(version.instrument_version_id),
    )
    calls = {"count": 0}

    def fake_clock():
        calls["count"] += 1
        return 0.0 if calls["count"] == 1 else 2.0

    with pytest.raises(checkpoint_module.CheckpointDeadlineExceeded):
        checkpoint_module.load_latest_checkpoint_payloads_batch(
            [version.instrument_version_id],
            db_path=db,
            feature_version=FEATURE_VERSION,
            deadline_monotonic=1.0,
            clock=fake_clock,
        )


# ---------------------------------------------------------------------------
# AC-018 revision-ledger batch
# ---------------------------------------------------------------------------


def test_ac_018_revision_ledger_batch_restores_multiple_instruments_in_one_scan(
    tmp_path, monkeypatch
):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: multiple requested instruments restore correctly from ONE MARKET_OBSERVATION_RECORDED scan, even with significant unrelated observation history."""
    db = _make_db(tmp_path)
    v0, v1 = _instrument(0), _instrument(1)
    base_epoch = 1_700_000_000
    # Significant unrelated observation history.
    for i in range(50):
        _insert_event(
            db,
            event_id=f"EV:other:{i}",
            event_type=MARKET_OBSERVATION_RECORDED,
            payload=_observation_payload(f"INSTR:other:{i}", epoch=base_epoch + i),
            local_sequence=i + 1,
        )
    _insert_event(
        db,
        event_id="EV:obs:v0",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(v0.instrument_version_id, epoch=base_epoch),
        local_sequence=100,
    )
    _insert_event(
        db,
        event_id="EV:obs:v1",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(v1.instrument_version_id, epoch=base_epoch + 60),
        local_sequence=101,
    )

    counts: dict[str, int] = {}
    _count_scans(monkeypatch, counts)

    batch = ledger_module.load_revision_ledgers_batch(
        [v0.instrument_version_id, v1.instrument_version_id],
        interval_seconds_by_instrument={
            v0.instrument_version_id: 60,
            v1.instrument_version_id: 60,
        },
        since_interval_epoch_by_instrument={
            v0.instrument_version_id: None,
            v1.instrument_version_id: None,
        },
        db_path=db,
    )
    assert set(batch) == {v0.instrument_version_id, v1.instrument_version_id}
    # Exactly ONE canonical market-observation event-family scan for the batch.
    assert counts.get(MARKET_OBSERVATION_RECORDED) == 1

    # Observational match against the single-instrument API.
    for version in (v0, v1):
        single = ledger_module.load_revision_ledger(
            version.instrument_version_id,
            interval_seconds=60,
            db_path=db,
        )
        assert batch[version.instrument_version_id].entries == single.entries


def test_ac_018_revision_ledger_batch_decodes_each_row_once(tmp_path, monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: the batch loader JSON-decodes each canonical MARKET_OBSERVATION_RECORDED row exactly ONCE for the whole batch, regardless of how many instruments are requested.

    This is the structural proof that the batch path is O(canonical rows), not
    O(requested_instruments * canonical rows): the historical per-instrument
    reconstruction looped the full row set once per requested instrument, so the
    decode count would have been requested_instruments * canonical_rows.
    """
    db = _make_db(tmp_path)
    versions = [_instrument(i) for i in range(4)]
    base_epoch = 1_700_000_000
    # Substantial unrelated history plus one row per requested instrument.
    unrelated_rows = 40
    for i in range(unrelated_rows):
        _insert_event(
            db,
            event_id=f"EV:other:{i}",
            event_type=MARKET_OBSERVATION_RECORDED,
            payload=_observation_payload(f"INSTR:other:{i}", epoch=base_epoch + i),
            local_sequence=i + 1,
        )
    for index, version in enumerate(versions):
        _insert_event(
            db,
            event_id=f"EV:obs:{index}",
            event_type=MARKET_OBSERVATION_RECORDED,
            payload=_observation_payload(
                version.instrument_version_id, epoch=base_epoch + 60 * index
            ),
            local_sequence=100 + index,
        )
    canonical_rows = unrelated_rows + len(versions)

    decode_calls = {"count": 0}
    real_loads = ledger_module.json.loads

    def _counting_loads(*args, **kwargs):
        decode_calls["count"] += 1
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(ledger_module.json, "loads", _counting_loads)

    batch = ledger_module.load_revision_ledgers_batch(
        [version.instrument_version_id for version in versions],
        interval_seconds_by_instrument={
            version.instrument_version_id: 60 for version in versions
        },
        since_interval_epoch_by_instrument={
            version.instrument_version_id: None for version in versions
        },
        db_path=db,
    )
    assert set(batch) == {version.instrument_version_id for version in versions}
    # Every canonical row decoded exactly once for the whole batch -- NOT once
    # per requested instrument.
    assert decode_calls["count"] == canonical_rows
    assert decode_calls["count"] < len(versions) * canonical_rows


def test_ac_018_revision_ledger_batch_requires_interval_mapping(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a missing interval_seconds_by_instrument key fails deterministically instead of silently defaulting."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:obs",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=1_700_000_000),
    )
    with pytest.raises(ValueError):
        ledger_module.load_revision_ledgers_batch(
            [version.instrument_version_id],
            interval_seconds_by_instrument={},
            since_interval_epoch_by_instrument={version.instrument_version_id: None},
            db_path=db,
        )


def test_ac_018_revision_ledger_batch_requires_since_mapping(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a missing since_interval_epoch_by_instrument key fails deterministically instead of silently defaulting."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:obs",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=1_700_000_000),
    )
    with pytest.raises(ValueError):
        ledger_module.load_revision_ledgers_batch(
            [version.instrument_version_id],
            interval_seconds_by_instrument={version.instrument_version_id: 60},
            since_interval_epoch_by_instrument={},
            db_path=db,
        )


def test_ac_018_checkpoint_batch_deadline_during_post_scan_selection(
    tmp_path, monkeypatch
):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a deadline that expires during POST-SCAN checkpoint selection fails with CheckpointDeadlineExceeded and returns no partial mapping.

    This isolates the post-scan selection boundary deterministically: the scan is
    stubbed to return two already-decoded valid payloads, so the test does not
    depend on SQLite VM instruction counts or progress-handler callback counts.
    The separate durable-read/query deadline tests cover the scan itself.
    """
    v0, v1 = _instrument(0), _instrument(1)
    payloads = [
        _checkpoint_payload(v0.instrument_version_id),
        _checkpoint_payload(v1.instrument_version_id),
    ]

    def _stub_scan(*, db_path, deadline_monotonic, clock):
        return list(payloads)

    monkeypatch.setattr(
        checkpoint_module, "_scan_checkpoint_payloads", _stub_scan
    )

    # Allow the first selection iteration, then expire on the next one.
    calls = {"count": 0}

    def fake_clock():
        calls["count"] += 1
        return 0.0 if calls["count"] == 1 else 2.0

    with pytest.raises(checkpoint_module.CheckpointDeadlineExceeded):
        checkpoint_module.load_latest_checkpoint_payloads_batch(
            [v0.instrument_version_id, v1.instrument_version_id],
            db_path=tmp_path / "unused.db",
            feature_version=FEATURE_VERSION,
            deadline_monotonic=1.0,
            clock=fake_clock,
        )


def test_ac_018_revision_ledger_batch_preserves_per_instrument_semantics(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: per-instrument interval and since_interval_epoch are honored independently."""
    db = _make_db(tmp_path)
    v0, v1 = _instrument(0), _instrument(1)
    base_epoch = 1_700_000_000
    _insert_event(
        db,
        event_id="EV:obs:v0",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(v0.instrument_version_id, epoch=base_epoch),
        local_sequence=1,
    )
    _insert_event(
        db,
        event_id="EV:obs:v1",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(v1.instrument_version_id, epoch=base_epoch + 60),
        local_sequence=2,
    )

    batch = ledger_module.load_revision_ledgers_batch(
        [v0.instrument_version_id, v1.instrument_version_id],
        interval_seconds_by_instrument={
            v0.instrument_version_id: 60,
            v1.instrument_version_id: 60,
        },
        since_interval_epoch_by_instrument={
            v0.instrument_version_id: None,
            v1.instrument_version_id: base_epoch + 120,  # excludes v1's only row
        },
        db_path=db,
    )
    assert batch[v0.instrument_version_id].entries
    assert batch[v1.instrument_version_id].entries == {}


def test_ac_018_revision_ledger_batch_preserves_revision_ordering(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: the highest revision per epoch wins, exactly as the single-instrument loader selects it."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    epoch = 1_700_000_000
    _insert_event(
        db,
        event_id="EV:r1",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=epoch, revision=1),
        local_sequence=1,
    )
    _insert_event(
        db,
        event_id="EV:r2",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=epoch, revision=2),
        local_sequence=2,
    )
    batch = ledger_module.load_revision_ledgers_batch(
        [version.instrument_version_id],
        interval_seconds_by_instrument={version.instrument_version_id: 60},
        since_interval_epoch_by_instrument={version.instrument_version_id: None},
        db_path=db,
    )
    entry = batch[version.instrument_version_id].entry_for(epoch)
    assert entry is not None
    assert entry.revision == 2


def test_ac_018_revision_ledger_batch_conflicting_duplicate_fails_closed(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: conflicting content fingerprints for the same (epoch, revision) fail closed in the batch loader exactly as in the single-instrument loader."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    epoch = 1_700_000_000
    _insert_event(
        db,
        event_id="EV:c1",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(
            version.instrument_version_id, epoch=epoch, revision=1, values={"close": 1.0}
        ),
        local_sequence=1,
    )
    _insert_event(
        db,
        event_id="EV:c2",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(
            version.instrument_version_id, epoch=epoch, revision=1, values={"close": 2.0}
        ),
        local_sequence=2,
    )
    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledger(
            version.instrument_version_id, interval_seconds=60, db_path=db
        )
    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledgers_batch(
            [version.instrument_version_id],
            interval_seconds_by_instrument={version.instrument_version_id: 60},
            since_interval_epoch_by_instrument={version.instrument_version_id: None},
            db_path=db,
        )


def test_ac_018_revision_ledger_batch_malformed_evidence_matches_single_loader(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a malformed committed observation fails closed in the batch loader exactly as in the single-instrument loader."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
        "local_sequence, recorded_at, idempotency_key, payload_json) "
        "VALUES (?, ?, ?, 1, 1, '2026-01-01T00:00:00Z', 'K:bad', ?)",
        ("EV:bad", schema_module.SCHEMA_VERSION, MARKET_OBSERVATION_RECORDED, "[1,2,3]"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledger(
            version.instrument_version_id, interval_seconds=60, db_path=db
        )
    with pytest.raises(ledger_module.RevisionLedgerIntegrityError):
        ledger_module.load_revision_ledgers_batch(
            [version.instrument_version_id],
            interval_seconds_by_instrument={version.instrument_version_id: 60},
            since_interval_epoch_by_instrument={version.instrument_version_id: None},
            db_path=db,
        )


def test_ac_018_revision_ledger_batch_deadline_returns_no_partial_map(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: a deadline failure raises and returns no partial mapping."""
    db = _make_db(tmp_path)
    version = _instrument(0)
    _insert_event(
        db,
        event_id="EV:obs",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(version.instrument_version_id, epoch=1_700_000_000),
    )
    calls = {"count": 0}

    def fake_clock():
        calls["count"] += 1
        return 0.0 if calls["count"] == 1 else 2.0

    with pytest.raises(ledger_module.RevisionLedgerDeadlineExceeded):
        ledger_module.load_revision_ledgers_batch(
            [version.instrument_version_id],
            interval_seconds_by_instrument={version.instrument_version_id: 60},
            since_interval_epoch_by_instrument={version.instrument_version_id: None},
            db_path=db,
            deadline_monotonic=1.0,
            clock=fake_clock,
        )


# ---------------------------------------------------------------------------
# AC-018 production batch restore
# ---------------------------------------------------------------------------


def test_ac_018_production_batch_restore_calls_each_loader_once(tmp_path, monkeypatch):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: production/default restore calls each batch loader once regardless of instrument count, and threads one absolute deadline/clock through both phases."""
    versions = [_instrument(i) for i in range(5)]
    calls = {"states": 0, "ledgers": 0}
    seen_deadlines: list[float | None] = []

    def _states_batch(ids, **kwargs):
        calls["states"] += 1
        seen_deadlines.append(kwargs.get("deadline_monotonic"))
        return {}

    def _ledgers_batch(ids, **kwargs):
        calls["ledgers"] += 1
        seen_deadlines.append(kwargs.get("deadline_monotonic"))
        return {}

    def _clock():
        return 0.0

    states, ledgers, watermarks = restore_pilot_continuity_batch(
        versions,
        load_states_batch=_states_batch,
        load_ledgers_batch=_ledgers_batch,
        deadline_monotonic=20.0,
        clock=_clock,
    )
    assert calls == {"states": 1, "ledgers": 1}
    assert seen_deadlines == [20.0, 20.0]
    assert states == {}
    assert ledgers == {}
    assert watermarks == {}


def test_ac_018_production_batch_restore_outputs_are_correct(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-018: the batch restore reconstructs correct state/ledger/watermark outputs from real canonical evidence."""
    db = _make_db(tmp_path)
    v0, v1 = _instrument(0), _instrument(1)
    base_epoch = 1_700_000_000
    _insert_event(
        db,
        event_id="EV:cp:v0",
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=_checkpoint_payload(
            v0.instrument_version_id,
            first_interval_epoch=base_epoch,
            last_interval_epoch=base_epoch + 60,
        ),
        local_sequence=1,
    )
    _insert_event(
        db,
        event_id="EV:obs:v0",
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=_observation_payload(v0.instrument_version_id, epoch=base_epoch),
        local_sequence=2,
    )

    def _states_batch(ids, **kwargs):
        return checkpoint_module.load_rolling_states_batch(
            ids, db_path=db, feature_version=FEATURE_VERSION, **kwargs
        )

    def _ledgers_batch(ids, **kwargs):
        return ledger_module.load_revision_ledgers_batch(ids, db_path=db, **kwargs)

    states, ledgers, watermarks = restore_pilot_continuity_batch(
        [v0, v1],
        load_states_batch=_states_batch,
        load_ledgers_batch=_ledgers_batch,
    )
    assert v0.instrument_version_id in states
    assert v1.instrument_version_id not in states
    assert v0.instrument_version_id in ledgers
    assert v1.instrument_version_id in ledgers
    assert v0.instrument_version_id in watermarks
    assert v1.instrument_version_id not in watermarks


# ---------------------------------------------------------------------------
# AC-018 historical injection compatibility
# ---------------------------------------------------------------------------


def test_ac_018_historical_single_instrument_callbacks_are_preserved():
    """ATDD-RELEASE-PIPELINE-v1/AC-018: the historical restore_pilot_continuity seam still invokes single-instrument callbacks once per instrument with the historical signatures -- no batch list is ever passed."""
    versions = [_instrument(0), _instrument(1), _instrument(2)]
    state_calls: list[str] = []
    ledger_calls: list[tuple[str, int, int | None]] = []

    def _load_state(instrument_version_id: str):
        state_calls.append(instrument_version_id)
        return None

    def _load_ledger(
        instrument_version_id: str,
        *,
        interval_seconds,
        since_interval_epoch,
    ):
        ledger_calls.append(
            (instrument_version_id, interval_seconds, since_interval_epoch)
        )
        return None

    states, ledgers, watermarks = restore_pilot_continuity(
        versions,
        load_state=_load_state,
        load_ledger=_load_ledger,
    )
    # ONE call per instrument, with a single instrument_version_id each time.
    assert state_calls == [v.instrument_version_id for v in versions]
    assert [call[0] for call in ledger_calls] == [
        v.instrument_version_id for v in versions
    ]
    # The historical ledger kwargs are still supplied.
    assert all(call[1] == 60 for call in ledger_calls)
    assert all(call[2] is None for call in ledger_calls)
    assert states == {}
    assert watermarks == {}


def test_ac_018_historical_callbacks_receive_deadline_kwargs_when_supplied():
    """ATDD-RELEASE-PIPELINE-v1/AC-018: when a deadline IS supplied, the historical single-instrument callbacks still receive deadline_monotonic and clock."""
    versions = [_instrument(0)]
    seen: list[tuple] = []

    def _load_state(instrument_version_id: str, *, deadline_monotonic, clock):
        seen.append(("state", instrument_version_id, deadline_monotonic, clock))
        return None

    def _load_ledger(
        instrument_version_id: str,
        *,
        interval_seconds,
        since_interval_epoch,
        deadline_monotonic,
        clock,
    ):
        seen.append(("ledger", instrument_version_id, deadline_monotonic, clock))
        return None

    def _clock():
        return 0.0

    restore_pilot_continuity(
        versions,
        load_state=_load_state,
        load_ledger=_load_ledger,
        deadline_monotonic=20.0,
        clock=_clock,
    )
    assert len(seen) == 2
    assert all(entry[2] == 20.0 for entry in seen)
    assert all(entry[3] is _clock for entry in seen)


# ---------------------------------------------------------------------------
# AC-018 telemetry
# ---------------------------------------------------------------------------


def test_ac_018_observer_attributes_failed_restore_phase():
    """ATDD-RELEASE-PIPELINE-v1/AC-018: the observer attributes a FAILED restore phase.

    A checkpoint loader failure emits ``checkpoint_restore`` and
    ``continuity_restore_total`` (no ``revision_ledger_restore``, since ledger
    restoration was never entered) and the ORIGINAL typed exception propagates.
    A ledger loader failure emits ``checkpoint_restore``,
    ``revision_ledger_restore`` and ``continuity_restore_total`` and the ORIGINAL
    typed exception propagates. No sleeps or wall-clock thresholds.
    """
    versions = [_instrument(0), _instrument(1)]

    class _CheckpointFailure(RuntimeError):
        pass

    class _LedgerFailure(RuntimeError):
        pass

    # Checkpoint loader failure: ledger restoration is never entered.
    checkpoint_observed: list[str] = []

    def _states_batch_fail(ids, **kwargs):
        raise _CheckpointFailure("checkpoint loader failed")

    def _ledgers_batch_unused(ids, **kwargs):  # pragma: no cover - never entered
        raise AssertionError("ledger loader must not be entered")

    with pytest.raises(_CheckpointFailure):
        restore_pilot_continuity_batch(
            versions,
            load_states_batch=_states_batch_fail,
            load_ledgers_batch=_ledgers_batch_unused,
            observer=lambda stage, seconds: checkpoint_observed.append(stage),
        )
    assert checkpoint_observed == ["checkpoint_restore", "continuity_restore_total"]

    # Ledger loader failure: checkpoint restore succeeded, ledger restore entered.
    ledger_observed: list[str] = []

    def _states_batch_ok(ids, **kwargs):
        return {}

    def _ledgers_batch_fail(ids, **kwargs):
        raise _LedgerFailure("ledger loader failed")

    with pytest.raises(_LedgerFailure):
        restore_pilot_continuity_batch(
            versions,
            load_states_batch=_states_batch_ok,
            load_ledgers_batch=_ledgers_batch_fail,
            observer=lambda stage, seconds: ledger_observed.append(stage),
        )
    assert ledger_observed == [
        "checkpoint_restore",
        "revision_ledger_restore",
        "continuity_restore_total",
    ]


def test_ac_018_observer_receives_bounded_timing_attribution():
    """ATDD-RELEASE-PIPELINE-v1/AC-018: the optional observer receives bounded checkpoint/ledger/total timing attribution."""
    versions = [_instrument(0), _instrument(1)]
    observed: list[tuple[str, float]] = []

    def _states_batch(ids, **kwargs):
        return {}

    def _ledgers_batch(ids, **kwargs):
        return {}

    restore_pilot_continuity_batch(
        versions,
        load_states_batch=_states_batch,
        load_ledgers_batch=_ledgers_batch,
        observer=lambda stage, seconds: observed.append((stage, seconds)),
    )
    stages = [stage for stage, _ in observed]
    assert stages == [
        "checkpoint_restore",
        "revision_ledger_restore",
        "continuity_restore_total",
    ]
    assert all(isinstance(seconds, float) and seconds >= 0.0 for _, seconds in observed)
