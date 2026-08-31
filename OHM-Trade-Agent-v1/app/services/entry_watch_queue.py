from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.registry_io import load_json, registry_lock, save_json_atomic


ENTRY_WATCH_FILE = Path("/app/data/opip_entry_watch.json")
MAX_ENTRY_WATCH = 25
DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_RECHECK_SECONDS = 90


def _lock_file() -> Path:
    return ENTRY_WATCH_FILE.parent / f".{ENTRY_WATCH_FILE.name}.lock"


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("entry-watch timestamp must be timezone-aware")
    return result.astimezone(timezone.utc)


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def enqueue_entry_watch(
    *,
    symbol: str,
    direction: str,
    candidate_id: str,
    continuation_score: int,
    risk_level: str = "low",
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    recheck_seconds: int = DEFAULT_RECHECK_SECONDS,
) -> None:
    current = _utc(now)
    normalized_direction = str(direction).upper()
    if normalized_direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    key = f"{symbol.upper()}:{normalized_direction}"
    with registry_lock(_lock_file()):
        rows = load_json(ENTRY_WATCH_FILE)
        for existing_key, existing_row in list(rows.items()):
            expires = _parse((existing_row or {}).get("expires_at")) if isinstance(existing_row, dict) else None
            if expires is None or expires <= current:
                rows.pop(existing_key, None)
        row = rows.get(key)
        is_new = not isinstance(row, dict)
        if is_new:
            row = {
                "schema_version": 1,
                "symbol": symbol.upper(),
                "direction": normalized_direction,
                "candidate_id": candidate_id,
                "first_seen_at": current.isoformat(),
                "expires_at": (
                    current + timedelta(seconds=max(300, int(ttl_seconds)))
                ).isoformat(),
            }
        row["candidate_id"] = candidate_id
        row["continuation_score"] = int(continuation_score)
        row["risk_level"] = (
            str(risk_level).lower()
            if str(risk_level).lower() in {"low", "medium"}
            else "low"
        )
        row["updated_at"] = current.isoformat()
        row["next_due_at"] = (
            current + timedelta(seconds=max(60, min(120, int(recheck_seconds))))
        ).isoformat()
        rows[key] = row

        if len(rows) > MAX_ENTRY_WATCH:
            ordered = sorted(
                rows.items(),
                key=lambda item: (
                    int((item[1] or {}).get("continuation_score") or 0),
                    str((item[1] or {}).get("updated_at") or ""),
                    item[0],
                ),
            )
            for remove_key, _ in ordered[: len(rows) - MAX_ENTRY_WATCH]:
                rows.pop(remove_key, None)
        save_json_atomic(ENTRY_WATCH_FILE, rows)


def due_entry_watch(*, now: datetime | None = None) -> list[dict]:
    current = _utc(now)
    with registry_lock(_lock_file()):
        rows = load_json(ENTRY_WATCH_FILE)
        changed = False
        due: list[dict] = []
        for key, row in list(rows.items()):
            if not isinstance(row, dict):
                rows.pop(key, None)
                changed = True
                continue
            symbol = str(row.get("symbol") or "").upper()
            direction = str(row.get("direction") or "").upper()
            if not symbol or direction not in {"LONG", "SHORT"}:
                rows.pop(key, None)
                changed = True
                continue
            expires = _parse(row.get("expires_at"))
            if expires is None or expires <= current:
                rows.pop(key, None)
                changed = True
                continue
            next_due = _parse(row.get("next_due_at"))
            if next_due is None or next_due <= current:
                due.append(dict(row))
        if changed:
            save_json_atomic(ENTRY_WATCH_FILE, rows)

    due.sort(
        key=lambda row: (
            -int(row.get("continuation_score") or 0),
            str(row.get("first_seen_at") or ""),
        )
    )
    return due


def defer_entry_watch(
    symbol: str,
    direction: str,
    *,
    now: datetime | None = None,
    recheck_seconds: int = DEFAULT_RECHECK_SECONDS,
    accelerated_scan: bool = False,
) -> bool:
    current = _utc(now)
    key = f"{symbol.upper()}:{direction.upper()}"
    with registry_lock(_lock_file()):
        rows = load_json(ENTRY_WATCH_FILE)
        row = rows.get(key)
        if not isinstance(row, dict):
            return False
        updated = dict(row)
        updated["next_due_at"] = (
            current + timedelta(seconds=max(60, min(900, int(recheck_seconds))))
        ).isoformat()
        updated["updated_at"] = current.isoformat()
        if accelerated_scan:
            updated["last_accelerated_scan_at"] = current.isoformat()
        rows[key] = updated
        save_json_atomic(ENTRY_WATCH_FILE, rows)
        return True


def remove_entry_watch(symbol: str, direction: str) -> bool:
    key = f"{symbol.upper()}:{direction.upper()}"
    with registry_lock(_lock_file()):
        rows = load_json(ENTRY_WATCH_FILE)
        if key not in rows:
            return False
        rows.pop(key, None)
        save_json_atomic(ENTRY_WATCH_FILE, rows)
        return True