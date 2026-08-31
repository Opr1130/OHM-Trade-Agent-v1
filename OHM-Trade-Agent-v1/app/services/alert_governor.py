from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.attention_budget import allow_new_noncritical, record_new_noncritical
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/alert_governor_state.json")
DEFAULT_REPEAT_COOLDOWN_SECONDS = 6 * 60 * 60
DEFAULT_MAX_NEW_CARDS_24H = 8
DEFAULT_PRIORITY_HARD_CAP_MULTIPLIER = 3
PRIORITY_TRANSITION_STAGES = frozenset(
    {
        "READY",
        "BREAKOUT_CANDIDATE",
        "ACTIONABLE_REVIEW",
    }
)


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


def _shared_budget_file(target: Path) -> Path:
    return target.parent / "attention_budget_state.json"


def _transition_stage(transition_key: str) -> str:
    return str(transition_key or "").split(":", 1)[0].strip().upper()


def _is_priority_transition(transition_key: str) -> bool:
    """Return whether a transition is important enough to bypass ordinary budgets.

    The transition key already starts with the canonical stage for both the
    movement-discovery and Signal Quality alert families. READY,
    BREAKOUT_CANDIDATE and ACTIONABLE_REVIEW are therefore allowed through an
    exhausted ordinary budget, but they still remain subject to the hard
    emergency cap and all same-symbol cooldown/edit rules.
    """
    return _transition_stage(transition_key) in PRIORITY_TRANSITION_STAGES


def evaluate_opportunity_alert(
    *,
    identity: str,
    transition_key: str,
    now: datetime | None = None,
    repeat_cooldown_seconds: int = DEFAULT_REPEAT_COOLDOWN_SECONDS,
    max_new_cards_24h: int = DEFAULT_MAX_NEW_CARDS_24H,
    hard_max_new_cards_24h: int | None = None,
    priority: bool | None = None,
    state_file: Path | None = None,
) -> AlertGovernorDecision:
    """Choose CREATE, EDIT, or SUPPRESS for an opportunity card.

    Ordinary new cards are constrained by the local family budget and the
    shared non-critical attention budget. High-quality READY,
    BREAKOUT_CANDIDATE and ACTIONABLE_REVIEW transitions may bypass those
    ordinary limits so a busy morning cannot hide a later qualified
    opportunity. They still cannot bypass the hard emergency cap.

    Existing-card edits keep their historical behavior: same-state cooldowns
    remain enforced and meaningful transitions edit rather than create a new
    card. Critical held-position risk events do not use this path.
    """
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"

    ordinary_limit = max(1, int(max_new_cards_24h))
    hard_limit = (
        max(ordinary_limit, int(hard_max_new_cards_24h))
        if hard_max_new_cards_24h is not None
        else ordinary_limit * DEFAULT_PRIORITY_HARD_CAP_MULTIPLIER
    )
    is_priority = _is_priority_transition(transition_key) if priority is None else bool(priority)
    ordinary_budget_exhausted = False

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

            if len(history) >= hard_limit:
                return AlertGovernorDecision("SUPPRESS", "NEW_CARD_EMERGENCY_CAP")

            ordinary_budget_exhausted = len(history) >= ordinary_limit
            if ordinary_budget_exhausted and not is_priority:
                return AlertGovernorDecision("SUPPRESS", "NEW_CARD_DAILY_BUDGET")
    except (OSError, TimeoutError, RegistryIOError):
        return AlertGovernorDecision("SUPPRESS", "STATE_UNAVAILABLE_FAIL_CLOSED")

    if not is_priority and not allow_new_noncritical(
        now=now,
        state_file=_shared_budget_file(target),
    ):
        return AlertGovernorDecision("SUPPRESS", "GLOBAL_ATTENTION_BUDGET")

    if is_priority and ordinary_budget_exhausted:
        return AlertGovernorDecision("CREATE", "PRIORITY_BYPASS_DAILY_BUDGET")
    if is_priority:
        return AlertGovernorDecision("CREATE", "PRIORITY_NEW_SYMBOL")
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
    except (OSError, TimeoutError, RegistryIOError):
        return

    if created_new:
        record_new_noncritical(
            kind="OPPORTUNITY_CARD",
            now=now,
            state_file=_shared_budget_file(target),
        )
