from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/attention_budget_state.json")
DEFAULT_MAX_NEW_NONCRITICAL_24H = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _recent_history(state: dict, now: datetime) -> list[dict]:
    cutoff = now - timedelta(hours=24)
    rows: list[dict] = []
    for item in state.get("history") or []:
        if not isinstance(item, dict):
            continue
        at = _parse(item.get("at"))
        if at is not None and at >= cutoff:
            rows.append({"at": at.isoformat(), "kind": str(item.get("kind") or "UNKNOWN")})
    return rows


def allow_new_noncritical(
    *,
    now: datetime | None = None,
    max_new_24h: int = DEFAULT_MAX_NEW_NONCRITICAL_24H,
    state_file: Path | None = None,
) -> bool:
    """Fail closed for ordinary cards if budget state cannot be trusted.

    Lifecycle-critical notifications bypass this helper in notification_policy,
    so suppressing here protects the user from an alert flood without hiding
    STOP/TARGET/EMERGENCY/MONITOR_DEGRADED events.
    """
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    try:
        with registry_lock(lock):
            state = load_json(target)
            return len(_recent_history(state, now)) < max(1, int(max_new_24h))
    except (OSError, TimeoutError, RegistryIOError):
        return False


def record_new_noncritical(
    *,
    kind: str,
    now: datetime | None = None,
    state_file: Path | None = None,
) -> None:
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    try:
        with registry_lock(lock):
            state = load_json(target)
            history = _recent_history(state, now)
            history.append({"at": now.isoformat(), "kind": str(kind).upper()})
            state["history"] = history[-200:]
            save_json_atomic(target, state)
    except (OSError, TimeoutError, RegistryIOError):
        return
