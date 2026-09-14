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


class CheckpointIntegrityError(ValueError):
    """Committed checkpoint payload is corrupt or non-reconstructable."""


def _require_exact_watermark(raw: Any) -> ConsumedInputWatermark:
    """Rebuild the commit watermark, rejecting coerced components.

    A committed payload storing ``history_epoch``/``local_sequence`` as a
    boolean, float, or numeric string would otherwise be silently normalized
    by ``int()`` into a commit position that was never declared as an integer.
    """
    if not isinstance(raw, Mapping):
        raise CheckpointIntegrityError(
            "committed feature checkpoint consumed_input_watermark must be a "
            "JSON object declaring history_epoch and local_sequence"
        )
    for name in ("history_epoch", "local_sequence"):
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise CheckpointIntegrityError(
                "committed feature checkpoint consumed_input_watermark."
                f"{name} must be an exact integer (got {value!r}); refusing "
                "to normalize a coerced commit position"
            )
    return ConsumedInputWatermark(
        history_epoch=raw["history_epoch"],
        local_sequence=raw["local_sequence"],
    )


def checkpoint_from_payload(payload: Mapping[str, Any]) -> FeatureStateCheckpoint:
    """Rebuild a FeatureStateCheckpoint from a committed canonical payload."""
    consumed_input_watermark = _require_exact_watermark(
        payload.get("consumed_input_watermark")
    )
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
        consumed_input_watermark=consumed_input_watermark,
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
    *,
    feature_version: str | None = None,
) -> dict[str, Any] | None:
    """Latest committed checkpoint for one instrument_version_id, or None.

    When ``feature_version`` is provided, older-version checkpoints are ignored
    so a feature-engine bump cold-starts instead of restoring incompatible
    retained-window assumptions under a new snapshot stamp.

    Structurally malformed committed checkpoint payloads fail closed before
    identity/version filtering so corrupt history cannot be hidden behind an
    older apparently valid checkpoint.
    """
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
            raise CheckpointIntegrityError(
                "committed feature checkpoint payload_json did not decode to "
                f"a JSON object (got {type(payload).__name__}); refusing to "
                "silently skip malformed canonical evidence"
            )
        payload_instrument_id = payload.get("instrument_version_id")
        payload_feature_version = payload.get("feature_version")
        if (
            not isinstance(payload_instrument_id, str)
            or not payload_instrument_id.strip()
            or not isinstance(payload_feature_version, str)
            or not payload_feature_version.strip()
        ):
            raise CheckpointIntegrityError(
                "committed feature checkpoint must declare non-empty string "
                "instrument_version_id and feature_version"
            )
        if payload_instrument_id != instrument_version_id:
            continue
        if (
            feature_version is not None
            and payload_feature_version != feature_version
        ):
            continue
        latest = payload
    return latest


def load_rolling_state(
    instrument_version_id: str,
    db_path: Path | None = None,
    *,
    feature_version: str | None = None,
) -> RollingState | None:
    """Resume RollingState from the latest committed checkpoint, if any."""
    from app.opip.features.engine import FEATURE_VERSION

    required_version = FEATURE_VERSION if feature_version is None else feature_version
    payload = load_latest_checkpoint_payload(
        instrument_version_id,
        db_path=db_path,
        feature_version=required_version,
    )
    if payload is None:
        return None
    return from_checkpoint(checkpoint_from_payload(payload))


__all__ = [
    "CheckpointIntegrityError",
    "checkpoint_from_payload",
    "load_latest_checkpoint_payload",
    "load_rolling_state",
]
