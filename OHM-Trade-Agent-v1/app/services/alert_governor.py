from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.registry_io import load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/alert_governor_state.json")
DEFAULT_REPEAT_COOLDOWN_SECONDS = 6 * 60 * 60
DEFAULT_MAX_NEW_CARDS_24H = 8


@dataclass(frozen=True)
class AlertGovernorDecision:
    action: str
    reason: str
    message_id: int | None = None

    @property
    def allow_immediate(self) -> bool:
        return self.action in {"CREATE", "EDIT"}

    @property
    def suppressed_to_digest(self) -> bool:
        return self.action == "SUPPRESS"


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
    max_new_cards_24h: int = DEFAULT_MAX_NEW_CARDS_24H,
    state_file: Path | None = None,
) -> AlertGovernorDecision:
    """Choose CREATE, EDIT, or SUPPRESS for an opportunity card.

    The budget applies only to creation of new Telegram cards. Once a symbol has
    a card, a meaningful state transition edits that card and does not consume a
    new-card budget slot. Critical held-position risk events do not use this path.
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
            previous_transition = str(previous.get("transition_key") or "")
            previous_at = _parse(previous.get("updated_at") or previous.get("sent_at"))
            raw_message_id = previous.get("message_id")
            try:
                message_id = int(raw_message_id) if raw_message_id is not None else None
            except (TypeError, ValueError):
                message_id = None

            if message_id is not None:
                if previous_transition == transition_key:
                    if previous_at is None:
                        return AlertGovernorDecision("SUPPRESS", "SAME_STATE", message_id)
                    age = (now - previous_at).total_seconds()
                    if age < repeat_cooldown_seconds:
                        return AlertGovernorDecision("SUPPRESS", "SAME_STATE_COOLDOWN", message_id)
                    return AlertGovernorDecision("EDIT", "PERIODIC_REFRESH", message_id)
                return AlertGovernorDecision("EDIT", "MEANINGFUL_TRANSITION", message_id)

            cutoff = now - timedelta(hours=24)
            history: list[datetime] = []
            for raw in state.get("new_card_history") or state.get("immediate_history") or []:
                parsed = _parse(raw)
                if parsed is not None and parsed >= cutoff:
                    history.append(parsed)
            if len(history) >= max(1, int(max_new_cards_24h)):
                return AlertGovernorDecision("SUPPRESS", "NEW_CARD_DAILY_BUDGET")
    except OSError:
        return AlertGovernorDecision("CREATE", "STATE_UNAVAILABLE_FAIL_OPEN")

    return AlertGovernorDecision("CREATE", "NEW_SYMBOL")


def record_opportunity_alert(
    *,
    identity: str,
    transition_key: str,
    message_id: int,
    created_new: bool,
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
            previous = identities.get(identity) or {}
            first_sent_at = previous.get("first_sent_at") or previous.get("sent_at") or now.isoformat()
            identities[identity] = {
                "transition_key": transition_key,
                "message_id": int(message_id),
                "first_sent_at": first_sent_at,
                "updated_at": now.isoformat(),
            }
            state["identities"] = identities

            cutoff = now - timedelta(hours=24)
            history: list[str] = []
            for raw in state.get("new_card_history") or state.get("immediate_history") or []:
                parsed = _parse(raw)
                if parsed is not None and parsed >= cutoff:
                    history.append(parsed.isoformat())
            if created_new:
                history.append(now.isoformat())
            state["new_card_history"] = history[-200:]
            state.pop("immediate_history", None)
            save_json_atomic(target, state)
    except OSError:
        return
