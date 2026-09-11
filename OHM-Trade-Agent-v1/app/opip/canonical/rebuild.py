"""Projection rebuild helpers (read-model from named watermark)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.opip.canonical.schema import connect


def rebuild_identity_projection(
    source_db: Path,
    *,
    through_history_epoch: int | None = None,
    through_local_sequence: int | None = None,
) -> dict[str, Any]:
    """Rebuild early-watch identity projection into a dict (fresh read model)."""
    conn = connect(source_db, read_only=True)
    try:
        sql = """
            SELECT event_id, event_type, history_epoch, local_sequence, payload_json
            FROM events
            ORDER BY history_epoch ASC, local_sequence ASC
        """
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()

    projection: dict[str, dict[str, Any]] = {}
    applied = 0
    for row in rows:
        epoch = int(row["history_epoch"])
        seq = int(row["local_sequence"])
        if through_history_epoch is not None and epoch > through_history_epoch:
            break
        if (
            through_history_epoch is not None
            and through_local_sequence is not None
            and epoch == through_history_epoch
            and seq > through_local_sequence
        ):
            break
        if str(row["event_type"]) != "alert_governor.transition.recorded":
            continue
        payload = json.loads(str(row["payload_json"]))
        identity = str(payload.get("identity") or "")
        if not identity:
            continue
        projection[identity] = {
            "transition_key": str(payload.get("transition_key") or ""),
            "message_id": payload.get("message_id"),
            "last_event_id": str(row["event_id"]),
            "last_history_epoch": epoch,
            "last_local_sequence": seq,
            "last_action": "CREATE" if payload.get("created_new") else "EDIT",
        }
        applied += 1
    return {
        "state_family": "early_watch",
        "identities": projection,
        "events_applied": applied,
        "through_history_epoch": through_history_epoch,
        "through_local_sequence": through_local_sequence,
    }


def write_projection_snapshot(projection: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
