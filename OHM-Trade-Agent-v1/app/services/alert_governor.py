from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.registry_io import load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/alert_governor_state.json")
LOCK_FILE = STATE_FILE.parent / ".alert_governor_state.lock"
DEFAULT_REPEAT_COOLDOWN_SECONDS = 6 * 60 * 60
DEFAULT_TRANSITION_COOLDOWN_SECONDS = 60 * 60
DEFAULT_MAX_IMMEDIATE_ALERTS_24H = 8


@dataclass(frozen=True)
class AlertGovernorDecision:
    allow_immediate: bool
    reason: str
    suppressed_to_digest: bool = False


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


def evaluate_opportunity_alert(
    *,
    identity: str,
    transition_key: str,
    now: datetime | None = None,
    repeat_cooldown_seconds: int = DEFAULT_REPEAT_COOLDOWN_SECONDS,
    transition_cooldown_seconds: int = DEFAULT_TRANSITION_COOLDOWN_SECONDS,
    max_immediate_alerts_24h: int = DEFAULT_MAX_IMMEDIATE_ALERTS_24H,
    state_file: Path | None = None,
) -> AlertGovernorDecision:
    """Decide whether an opportunity alert deserves immediate delivery.

    This governor is for opportunity/noise reduction only. Critical held-position
    risk events never call this function and remain governed by their existing
    unrestricted critical-event path.
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
            identities = state.get("identities") or {}
            previous = identities.get(identity) or {}
            previous_at = _parse(previous.get("sent_at"))
            previous_transition = str(previous.get("transition_key") or "")

            if previous_at is not None:
                age = (now - previous_at).total_seconds()
                if previous_transition == transition_key and age < repeat_cooldown_seconds:
                    return AlertGovernorDecision(False, "REPEAT_STATE_COOLDOWN", True)
                if previous_transition != transition_key and age < transition_cooldown_seconds:
                    return AlertGovernorDecision(False, "TRANSITION_COOLDOWN", True)

            cutoff = now - timedelta(hours=24)
            history = []
            for raw in state.get("immediate_history") or []:
                parsed = _parse(raw)
                if parsed is not None and parsed >= cutoff:
                    history.append(parsed)
            if len(history) >= max(1, int(max_immediate_alerts_24h)):
                return AlertGovernorDecision(False, "DAILY_IMMEDIATE_BUDGET", True)
    except OSError:
        # Fail open for advisory opportunity delivery if local state is unavailable.
        return AlertGovernorDecision(True, "STATE_UNAVAILABLE_FAIL_OPEN", False)

    return AlertGovernorDecision(True, "IMMEDIATE_ALLOWED", False)


def record_opportunity_alert(
    *,
    identity: str,
    transition_key: str,
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
            identities = state.get("identities") or {}
            identities[identity] = {
                "transition_key": transition_key,
                "sent_at": now.isoformat(),
            }
            cutoff = now - timedelta(hours=24)
            history = []
            for raw in state.get("immediate_history") or []:
                parsed = _parse(raw)
                if parsed is not None and parsed >= cutoff:
                    history.append(parsed.isoformat())
            history.append(now.isoformat())
            state["identities"] = identities
            state["immediate_history"] = history[-200:]
            save_json_atomic(target, state)
    except OSError:
        return
