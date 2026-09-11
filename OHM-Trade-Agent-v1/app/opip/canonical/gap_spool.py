"""Non-authoritative capture-gap recovery spool (O10).

Not an evidence journal. Unresolved entries make the evidence window INCOMPLETE
and must never be silently evicted by a size limit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.opip.canonical.paths import gap_spool_path
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic

_ALLOWED_FIELDS = (
    "gap_id",
    "idempotency_key",
    "scan_id",
    "identity",
    "intended_event_type",
    "observed_at",
    "error_code",
    "retry_count",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _lock_for(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def load_gap_spool(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or gap_spool_path())
    try:
        payload = load_json(target)
    except (OSError, TimeoutError, RegistryIOError):
        return {"unresolved": [], "updated_at": None}
    unresolved = payload.get("unresolved")
    if not isinstance(unresolved, list):
        unresolved = []
    return {
        "unresolved": [row for row in unresolved if isinstance(row, dict)],
        "updated_at": payload.get("updated_at"),
    }


def evidence_window_incomplete(path: Path | None = None) -> bool:
    return bool(load_gap_spool(path)["unresolved"])


def append_capture_gap(
    *,
    idempotency_key: str,
    scan_id: str,
    identity: str,
    intended_event_type: str,
    error_code: str,
    path: Path | None = None,
) -> str:
    """Append an unresolved gap descriptor. Never silently drops older entries."""
    target = Path(path or gap_spool_path())
    gap_id = f"GAP:{uuid.uuid4().hex}"
    entry = {
        "gap_id": gap_id,
        "idempotency_key": str(idempotency_key),
        "scan_id": str(scan_id),
        "identity": str(identity),
        "intended_event_type": str(intended_event_type),
        "observed_at": _now(),
        "error_code": str(error_code),
        "retry_count": 0,
    }
    # Strip anything that could accidentally carry secrets/payloads.
    entry = {key: entry[key] for key in _ALLOWED_FIELDS}
    with registry_lock(_lock_for(target)):
        payload = load_gap_spool(target)
        unresolved = list(payload["unresolved"])
        unresolved.append(entry)
        save_json_atomic(
            target,
            {"unresolved": unresolved, "updated_at": _now()},
        )
    return gap_id


def bump_gap_retry(gap_id: str, *, path: Path | None = None) -> None:
    target = Path(path or gap_spool_path())
    with registry_lock(_lock_for(target)):
        payload = load_gap_spool(target)
        unresolved = list(payload["unresolved"])
        for row in unresolved:
            if str(row.get("gap_id")) == gap_id:
                row["retry_count"] = int(row.get("retry_count") or 0) + 1
                break
        save_json_atomic(
            target,
            {"unresolved": unresolved, "updated_at": _now()},
        )


def resolve_gap(gap_id: str, *, path: Path | None = None) -> None:
    """Remove a resolved spool entry (pruning resolved state is allowed)."""
    target = Path(path or gap_spool_path())
    with registry_lock(_lock_for(target)):
        payload = load_gap_spool(target)
        unresolved = [
            row
            for row in payload["unresolved"]
            if str(row.get("gap_id")) != gap_id
        ]
        save_json_atomic(
            target,
            {"unresolved": unresolved, "updated_at": _now()},
        )
