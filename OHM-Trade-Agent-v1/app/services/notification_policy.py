from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.services.attention_budget import allow_new_noncritical, record_new_noncritical
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/notification_state.json")
LOCK_FILE = STATE_FILE.parent / ".notification_state.lock"
DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60
DEFAULT_RESERVATION_LEASE_SECONDS = 120
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
    "POSITION_WARNING",
    "ACTIONABLE_TRADE",
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


def reserve_emit(
    *,
    identity: str,
    event_type: str,
    fingerprint: str,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    lease_seconds: int = DEFAULT_RESERVATION_LEASE_SECONDS,
    now: datetime | None = None,
) -> str | None:
    """Atomically reserve one notification attempt.

    A caller must confirm or release the returned token. This closes the
    check-then-send race in should_emit()/record_emitted() for migrated paths.
    Noncritical attention budget remains a precondition, but duplicate send
    exclusion is enforced under the notification-state lock.
    """
    now = now or _now()
    event_type = event_type.upper()
    key = f"{identity}:{event_type}"

    if event_type not in CRITICAL_EVENTS and not allow_new_noncritical(
        now=now,
        state_file=_shared_budget_file(),
    ):
        return None

    try:
        with registry_lock(LOCK_FILE):
            state = load_json(STATE_FILE)
            previous = state.get(key, {})
            if not isinstance(previous, dict):
                previous = {}

            if previous.get("fingerprint") == fingerprint and previous.get("sent_at"):
                return None

            last_at = _parse(previous.get("sent_at"))
            if event_type not in CRITICAL_EVENTS and last_at is not None:
                if (now - last_at).total_seconds() < cooldown_seconds:
                    return None

            lease_until = _parse(previous.get("reservation_expires_at"))
            if (
                previous.get("reservation_token")
                and lease_until is not None
                and lease_until > now
            ):
                return None

            token = uuid4().hex
            previous["reservation_token"] = token
            previous["reservation_fingerprint"] = fingerprint
            previous["reservation_started_at"] = now.isoformat()
            previous["reservation_expires_at"] = (
                now.timestamp() + max(1, int(lease_seconds))
            )
            # Store ISO text for the existing parser.
            previous["reservation_expires_at"] = datetime.fromtimestamp(
                float(previous["reservation_expires_at"]),
                tz=timezone.utc,
            ).isoformat()
            state[key] = previous
            save_json_atomic(STATE_FILE, state)
            return token
    except (OSError, TimeoutError, RegistryIOError):
        # Critical protection paths keep their historical fail-open behavior.
        # A synthetic token lets the caller attempt delivery; confirm/release
        # will safely no-op if storage is still unavailable.
        return f"FAILOPEN-{uuid4().hex}" if event_type in CRITICAL_EVENTS else None


def confirm_emit(
    *,
    identity: str,
    event_type: str,
    fingerprint: str,
    reservation_token: str,
    now: datetime | None = None,
) -> bool:
    now = now or _now()
    event_type = event_type.upper()
    key = f"{identity}:{event_type}"
    confirmed = False
    try:
        with registry_lock(LOCK_FILE):
            state = load_json(STATE_FILE)
            previous = state.get(key, {})
            if not isinstance(previous, dict):
                previous = {}
            token = str(previous.get("reservation_token") or "")
            if reservation_token.startswith("FAILOPEN-"):
                return False
            if token != reservation_token:
                return False
            state[key] = {
                "fingerprint": fingerprint,
                "sent_at": now.isoformat(),
            }
            save_json_atomic(STATE_FILE, state)
            confirmed = True
    except (OSError, TimeoutError, RegistryIOError):
        return False

    if confirmed and event_type not in CRITICAL_EVENTS:
        record_new_noncritical(
            kind=event_type,
            now=now,
            state_file=_shared_budget_file(),
        )
    return confirmed


def release_emit(
    *,
    identity: str,
    event_type: str,
    reservation_token: str,
) -> bool:
    if reservation_token.startswith("FAILOPEN-"):
        return True
    event_type = event_type.upper()
    key = f"{identity}:{event_type}"
    try:
        with registry_lock(LOCK_FILE):
            state = load_json(STATE_FILE)
            previous = state.get(key)
            if not isinstance(previous, dict):
                return False
            if str(previous.get("reservation_token") or "") != reservation_token:
                return False
            updated = dict(previous)
            for field in (
                "reservation_token",
                "reservation_fingerprint",
                "reservation_started_at",
                "reservation_expires_at",
            ):
                updated.pop(field, None)
            if updated:
                state[key] = updated
            else:
                state.pop(key, None)
            save_json_atomic(STATE_FILE, state)
            return True
    except (OSError, TimeoutError, RegistryIOError):
        return False