from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.active_trade_registry import get_active_trades
from app.services.pending_setup_registry import get_pending_setups
from app.services.registry_io import load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/operator_control.json")
LOCK_FILE = STATE_FILE.parent / ".operator_control.lock"
VALID_OVERRIDES = {"AUTO", "SEARCH", "MONITOR", "MAINTENANCE"}
DEFAULT_TIMEZONE = "America/New_York"
QUIET_START_HOUR = 23
QUIET_END_HOUR = 5
MAX_OCCUPIED_SLOTS = 3
THROTTLE_AT_SLOTS = 2
NORMAL_SEARCH_INTERVAL_SECONDS = 300
THROTTLED_SEARCH_INTERVAL_SECONDS = 900
RESUME_COOLDOWN_SECONDS = 900


@dataclass(frozen=True)
class OperatorDecision:
    override_mode: str
    effective_mode: str
    occupied_slots: int
    active_trades: int
    pending_setups: int
    live_order_intents: int
    quiet_hours: bool
    search_allowed: bool
    search_interval_seconds: int
    reason: str
    cooldown_until: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state() -> dict:
    return load_json(STATE_FILE)


def _save_state(state: dict) -> None:
    save_json_atomic(STATE_FILE, state)


def set_override_mode(mode: str) -> dict:
    normalized = mode.upper()
    if normalized not in VALID_OVERRIDES:
        raise ValueError(f"Unsupported operator mode: {mode}")
    with registry_lock(LOCK_FILE):
        state = _load_state()
        state["override_mode"] = normalized
        state["updated_at"] = _now().isoformat()
        _save_state(state)
        return state


def get_override_mode() -> str:
    with registry_lock(LOCK_FILE):
        mode = str(_load_state().get("override_mode") or "AUTO").upper()
    return mode if mode in VALID_OVERRIDES else "AUTO"


def _live_order_intents() -> int:
    try:
        from app.services.order_intent_registry import get_live_order_intents
        return len(get_live_order_intents())
    except (ImportError, OSError, ValueError):
        return 0


def _is_quiet_hours(now: datetime, tz_name: str = DEFAULT_TIMEZONE) -> bool:
    local = now.astimezone(ZoneInfo(tz_name))
    hour = local.hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_operator_decision(now: datetime | None = None) -> OperatorDecision:
    now = now or _now()
    active_count = len(get_active_trades())
    pending_count = len(get_pending_setups())
    order_count = _live_order_intents()
    occupied = active_count + order_count
    quiet = _is_quiet_hours(now)

    with registry_lock(LOCK_FILE):
        state = _load_state()
        override = str(state.get("override_mode") or "AUTO").upper()
        if override not in VALID_OVERRIDES:
            override = "AUTO"

        previous_occupied = int(state.get("last_occupied_slots") or 0)
        cooldown_until = _parse_dt(state.get("cooldown_until"))
        if previous_occupied >= MAX_OCCUPIED_SLOTS and occupied < MAX_OCCUPIED_SLOTS:
            cooldown_until = now + timedelta(seconds=RESUME_COOLDOWN_SECONDS)
            state["cooldown_until"] = cooldown_until.isoformat()
        state["last_occupied_slots"] = occupied
        state["last_evaluated_at"] = now.isoformat()
        _save_state(state)

    if override == "MAINTENANCE":
        return OperatorDecision(override, "MAINTENANCE", occupied, active_count, pending_count, order_count, quiet, False, 0, "operator override", state.get("cooldown_until"))
    if override == "MONITOR":
        return OperatorDecision(override, "MONITOR", occupied, active_count, pending_count, order_count, quiet, False, 0, "operator override", state.get("cooldown_until"))
    if override == "SEARCH":
        interval = THROTTLED_SEARCH_INTERVAL_SECONDS if occupied >= THROTTLE_AT_SLOTS else NORMAL_SEARCH_INTERVAL_SECONDS
        return OperatorDecision(override, "SEARCH", occupied, active_count, pending_count, order_count, quiet, True, interval, "operator override", state.get("cooldown_until"))

    if quiet:
        return OperatorDecision(override, "MONITOR", occupied, active_count, pending_count, order_count, quiet, False, 0, "quiet hours 23:00-05:00 America/New_York", state.get("cooldown_until"))
    if occupied >= MAX_OCCUPIED_SLOTS:
        return OperatorDecision(override, "MONITOR", occupied, active_count, pending_count, order_count, quiet, False, 0, "portfolio capacity reached", state.get("cooldown_until"))
    if cooldown_until and now < cooldown_until:
        return OperatorDecision(override, "MONITOR", occupied, active_count, pending_count, order_count, quiet, False, 0, "capacity-release cooldown", cooldown_until.isoformat())

    interval = THROTTLED_SEARCH_INTERVAL_SECONDS if occupied >= THROTTLE_AT_SLOTS else NORMAL_SEARCH_INTERVAL_SECONDS
    reason = "throttled search: two occupied slots" if occupied >= THROTTLE_AT_SLOTS else "capacity available"
    return OperatorDecision(override, "SEARCH", occupied, active_count, pending_count, order_count, quiet, True, interval, reason, state.get("cooldown_until"))


def search_due(decision: OperatorDecision, now: datetime | None = None) -> bool:
    if not decision.search_allowed:
        return False
    now = now or _now()
    with registry_lock(LOCK_FILE):
        last = _parse_dt(_load_state().get("last_search_started_at"))
    return last is None or (now - last).total_seconds() >= decision.search_interval_seconds


def mark_search_started(now: datetime | None = None) -> None:
    now = now or _now()
    with registry_lock(LOCK_FILE):
        state = _load_state()
        state["last_search_started_at"] = now.isoformat()
        _save_state(state)


def status_payload(now: datetime | None = None) -> dict:
    decision = get_operator_decision(now)
    payload = asdict(decision)
    payload["search_due"] = search_due(decision, now)
    return payload
