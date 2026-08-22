from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.services.attention_budget import allow_new_noncritical, record_new_noncritical
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/notification_state.json")
LOCK_FILE = STATE_FILE.parent / ".notification_state.lock"
DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60
CRITICAL_EVENTS = {
    "STOP",
    "T1",
    "T2",
    "CLOSED",
    "EMERGENCY",
    "FILLED",
    "TAKE_PROFIT",
    "EXIT_NOW",
    "INVALIDATED",
    "TOO_EXTENDED",
    "MONITOR_DEGRADED",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _shared_budget_file() -> Path:
    return STATE_FILE.parent / "attention_budget_state.json"


def should_emit(
    *,
    identity: str,
    event_type: str,
    fingerprint: str,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    now: datetime | None = None,
) -> bool:
    now = now or _now()
    event_type = event_type.upper()
    key = f"{identity}:{event_type}"
    try:
        with registry_lock(LOCK_FILE):
            state = load_json(STATE_FILE)
            previous = state.get(key, {})
            if previous.get("fingerprint") == fingerprint:
                return False
            last_at = _parse(previous.get("sent_at"))
            if event_type not in CRITICAL_EVENTS and last_at is not None:
                if (now - last_at).total_seconds() < cooldown_seconds:
                    return False
    except (OSError, TimeoutError, RegistryIOError):
        # Lifecycle-critical alerts remain fail-open; ordinary attention cards
        # fail closed so a broken state registry cannot bypass flood controls.
        return event_type in CRITICAL_EVENTS

    if event_type not in CRITICAL_EVENTS and not allow_new_noncritical(
        now=now,
        state_file=_shared_budget_file(),
    ):
        return False
    return True


def record_emitted(
    *,
    identity: str,
    event_type: str,
    fingerprint: str,
    now: datetime | None = None,
) -> None:
    now = now or _now()
    event_type = event_type.upper()
    key = f"{identity}:{event_type}"
    try:
        with registry_lock(LOCK_FILE):
            state = load_json(STATE_FILE)
            state[key] = {"fingerprint": fingerprint, "sent_at": now.isoformat()}
            save_json_atomic(STATE_FILE, state)
    except (OSError, TimeoutError, RegistryIOError):
        return

    if event_type not in CRITICAL_EVENTS:
        record_new_noncritical(
            kind=event_type,
            now=now,
            state_file=_shared_budget_file(),
        )
