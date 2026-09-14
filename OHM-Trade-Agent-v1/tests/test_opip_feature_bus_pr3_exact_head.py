"""Focused exact-head regressions for PR3 canonical hydration integrity."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.events import (
    FEATURE_CHECKPOINT_RECORDED,
    MARKET_OBSERVATION_RECORDED,
)
from app.opip.features.checkpoint_store import (
    CheckpointIntegrityError,
    load_latest_checkpoint_payload,
)
from app.opip.features.revision_ledger import (
    RevisionLedgerIntegrityError,
    load_revision_ledger,
)

INSTRUMENT_ID = "INSTR:kraken:SOL:USD:1"


def _insert_raw_event(conn, *, event_type: str, payload: dict, sequence: int = 1) -> None:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO events (
            event_id, schema_version, event_type, history_epoch, local_sequence,
            recorded_at, event_time, causation_id, correlation_id,
            idempotency_key, payload_json
        ) VALUES (?, 1, ?, 1, ?, ?, NULL, NULL, NULL, ?, ?)
        """,
        (
            f"EVT:pr3-exact-{sequence}",
            event_type,
            sequence,
            now,
            f"idem-pr3-exact-{sequence}",
            json.dumps(payload),
        ),
    )
    conn.commit()


@pytest.fixture
def canonical_db(tmp_path):
    db_path = tmp_path / "canonical.db"
    server = CanonicalWriterServer(
        db_path=db_path,
        socket_path=tmp_path / "writer.sock",
    )
    yield server, db_path
    try:
        server.stop()
    except Exception:  # noqa: BLE001
        pass


@pytest.mark.parametrize(
    "instrument_value,feature_value",
    [
        (None, "features-v1"),
        ("", "features-v1"),
        (123, "features-v1"),
        (INSTRUMENT_ID, None),
        (INSTRUMENT_ID, ""),
        (INSTRUMENT_ID, 123),
    ],
)
def test_checkpoint_hydration_requires_nonempty_string_identifiers(
    canonical_db, instrument_value, feature_value
):
    server, db_path = canonical_db
    payload = {
        "instrument_version_id": instrument_value,
        "feature_version": feature_value,
    }
    _insert_raw_event(
        server.writer._conn,
        event_type=FEATURE_CHECKPOINT_RECORDED,
        payload=payload,
    )
    with pytest.raises(CheckpointIntegrityError, match="non-empty string"):
        load_latest_checkpoint_payload(
            INSTRUMENT_ID,
            db_path=db_path,
            feature_version="features-v1",
        )


@pytest.mark.parametrize("raw_revision", [True, 1.0, "1"])
def test_revision_ledger_rejects_coerced_revision_types(canonical_db, raw_revision):
    server, db_path = canonical_db
    epoch = 1789390800
    payload = {
        "instrument_version_id": INSTRUMENT_ID,
        "aggregate_interval_seconds": 60,
        "source_event_time": datetime.fromtimestamp(epoch, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "revision": raw_revision,
        "observation_id": f"OBS:{INSTRUMENT_ID}:60s:{epoch}:1",
        "receipt_time": datetime.fromtimestamp(epoch + 60, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "values": {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
        },
    }
    _insert_raw_event(
        server.writer._conn,
        event_type=MARKET_OBSERVATION_RECORDED,
        payload=payload,
    )
    with pytest.raises(RevisionLedgerIntegrityError, match="non-integer revision"):
        load_revision_ledger(INSTRUMENT_ID, db_path=db_path)
