"""Load latest FeatureStateCheckpoint payloads from canonical evidence (PR3).

Additive reads from the generic events table — no physical schema bump.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.opip.contracts.enums import RestartState
from app.opip.contracts.events import FEATURE_CHECKPOINT_RECORDED
from app.opip.contracts.features import FeatureStateCheckpoint
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.contracts.temporal import require_utc
from app.opip.features.state import RollingState, from_checkpoint


def checkpoint_from_payload(payload: Mapping[str, Any]) -> FeatureStateCheckpoint:
    """Rebuild a FeatureStateCheckpoint from a committed canonical payload."""
    created = payload.get("created_at_utc")
    created_at = None
    if created is not None:
        created_at = require_utc(
            datetime.fromisoformat(str(created).replace("Z", "+00:00")),
            field_name="created_at_utc",
        )
    return FeatureStateCheckpoint(
        instrument_version_id=str(payload["instrument_version_id"]),
        venue_instrument_id=str(payload["venue_instrument_id"]),
        feature_version=str(payload["feature_version"]),
        consumed_input_watermark=ConsumedInputWatermark.from_dict(
            dict(payload.get("consumed_input_watermark") or {})
        ),
        rolling_state=dict(payload.get("rolling_state") or {}),
        restart_state=RestartState(str(payload["restart_state"])),
        reconstruction_dependencies=tuple(
            str(item) for item in (payload.get("reconstruction_dependencies") or ())
        )
        or ("fixed_interval_aggregate:60s",),
        created_at_utc=created_at,
        schema_version=int(payload.get("schema_version") or 1),
    )


def load_latest_checkpoint_payload(
    instrument_version_id: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Latest committed checkpoint for one instrument_version_id, or None."""
    from app.opip.canonical.schema import connect

    if db_path is None:
        from app.opip.canonical.paths import db_path as default_db_path

        db_path = default_db_path()
    target = Path(db_path)
    if not target.exists():
        return None
    conn = connect(target, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (FEATURE_CHECKPOINT_RECORDED,),
        ).fetchall()
    finally:
        conn.close()
    latest: dict[str, Any] | None = None
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            continue
        if str(payload.get("instrument_version_id") or "") != instrument_version_id:
            continue
        latest = payload
    return latest


def load_rolling_state(
    instrument_version_id: str,
    db_path: Path | None = None,
) -> RollingState | None:
    """Resume RollingState from the latest committed checkpoint, if any."""
    payload = load_latest_checkpoint_payload(instrument_version_id, db_path=db_path)
    if payload is None:
        return None
    return from_checkpoint(checkpoint_from_payload(payload))


__all__ = [
    "checkpoint_from_payload",
    "load_latest_checkpoint_payload",
    "load_rolling_state",
]
