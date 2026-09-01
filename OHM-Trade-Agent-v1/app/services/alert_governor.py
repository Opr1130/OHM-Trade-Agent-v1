from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.services.attention_budget import allow_new_noncritical, record_new_noncritical
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/alert_governor_state.json")
DEFAULT_REPEAT_COOLDOWN_SECONDS = 6 * 60 * 60
DEFAULT_MAX_NEW_CARDS_24H = 8
DEFAULT_PRIORITY_HARD_CAP_MULTIPLIER = 3
RESERVATION_TTL_SECONDS = 5 * 60
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
    reservation_token: str | None = None

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


def _pruned_history(state: dict, *, now: datetime) -> list[str]:
    cutoff = now - timedelta(hours=24)
    history: list[str] = []
    for raw in state.get("new_card_history") or state.get("immediate_history") or []:
        parsed = _parse(raw)
        if parsed is not None and parsed >= cutoff:
            history.append(parsed.isoformat())
    return history


def _active_reservations(state: dict, *, now: datetime) -> dict[str, dict]:
    cutoff = now - timedelta(seconds=RESERVATION_TTL_SECONDS)
    active: dict[str, dict] = {}
    raw_reservations = state.get("new_card_reservations") or {}
    if not isinstance(raw_reservations, dict):
        return active
    for token, raw in raw_reservations.items():
        if not isinstance(raw, dict):
            continue
        reserved_at = _parse(raw.get("reserved_at"))
        if reserved_at is None or reserved_at < cutoff:
            continue
        active[str(token)] = dict(raw)
    return active


def _existing_decision(
    state: dict,
    *,
    identity: str,
    transition_key: str,
    now: datetime,
    repeat_cooldown_seconds: int,
) -> AlertGovernorDecision | None:
    identities = state.get("identities") or {}
    previous = identities.get(identity) or {}
    previous_transition = str(previous.get("transition_key") or "")
    previous_at = _parse(previous.get("updated_at") or previous.get("sent_at"))
    raw_message_id = previous.get("message_id")
    try:
        message_id = int(raw_message_id) if raw_message_id is not None else None
    except (TypeError, ValueError):
        message_id = None

    if message_id is None:
        return None
    if previous_transition == transition_key:
        if previous_at is None:
            return AlertGovernorDecision("SUPPRESS", "SAME_STATE", message_id)
        age = (now - previous_at).total_seconds()
        if age < repeat_cooldown_seconds:
            return AlertGovernorDecision(
                "SUPPRESS",
                "SAME_STATE_COOLDOWN",
                message_id,
            )
        return AlertGovernorDecision("EDIT", "PERIODIC_REFRESH", message_id)
    return AlertGovernorDecision("EDIT", "MEANINGFUL_TRANSITION", message_id)


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
    """Choose CREATE, EDIT, or SUPPRESS and reserve CREATE capacity atomically."""
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
    is_priority = (
        _is_priority_transition(transition_key)
        if priority is None
        else bool(priority)
    )

    # Existing canonical cards never consume a CREATE reservation.
    try:
        with registry_lock(lock):
            state = load_json(target)
            existing = _existing_decision(
                state,
                identity=identity,
                transition_key=transition_key,
                now=now,
                repeat_cooldown_seconds=repeat_cooldown_seconds,
            )
            if existing is not None:
                return existing
    except (OSError, TimeoutError, RegistryIOError):
        return AlertGovernorDecision("SUPPRESS", "STATE_UNAVAILABLE_FAIL_CLOSED")

    # The shared cross-family budget remains a separate subsystem. Do not hold
    # this family's registry lock while consulting it.
    if not is_priority and not allow_new_noncritical(
        now=now,
        state_file=_shared_budget_file(target),
    ):
        return AlertGovernorDecision("SUPPRESS", "GLOBAL_ATTENTION_BUDGET")

    # Re-check and reserve under one lock. The hard/ordinary local cap counts
    # both committed history and live reservations, closing the check/send/
    # commit TOCTOU race without holding a lock across Telegram I/O.
    try:
        with registry_lock(lock):
            state = load_json(target)
            existing = _existing_decision(
                state,
                identity=identity,
                transition_key=transition_key,
                now=now,
                repeat_cooldown_seconds=repeat_cooldown_seconds,
            )
            if existing is not None:
                return existing

            history = _pruned_history(state, now=now)
            reservations = _active_reservations(state, now=now)
            for row in reservations.values():
                if str(row.get("identity") or "") == identity:
                    state["new_card_history"] = history[-200:]
                    state["new_card_reservations"] = reservations
                    state.pop("immediate_history", None)
                    save_json_atomic(target, state)
                    return AlertGovernorDecision(
                        "SUPPRESS",
                        "CREATE_IN_FLIGHT",
                    )

            occupied = len(history) + len(reservations)
            if occupied >= hard_limit:
                state["new_card_history"] = history[-200:]
                state["new_card_reservations"] = reservations
                state.pop("immediate_history", None)
                save_json_atomic(target, state)
                return AlertGovernorDecision(
                    "SUPPRESS",
                    "NEW_CARD_EMERGENCY_CAP",
                )

            ordinary_budget_exhausted = occupied >= ordinary_limit
            if ordinary_budget_exhausted and not is_priority:
                state["new_card_history"] = history[-200:]
                state["new_card_reservations"] = reservations
                state.pop("immediate_history", None)
                save_json_atomic(target, state)
                return AlertGovernorDecision(
                    "SUPPRESS",
                    "NEW_CARD_DAILY_BUDGET",
                )

            reservation_token = uuid4().hex
            reservations[reservation_token] = {
                "identity": identity,
                "transition_key": transition_key,
                "reserved_at": now.isoformat(),
            }
            state["new_card_history"] = history[-200:]
            state["new_card_reservations"] = reservations
            state.pop("immediate_history", None)
            save_json_atomic(target, state)
    except (OSError, TimeoutError, RegistryIOError):
        return AlertGovernorDecision("SUPPRESS", "STATE_UNAVAILABLE_FAIL_CLOSED")

    if is_priority and ordinary_budget_exhausted:
        reason = "PRIORITY_BYPASS_DAILY_BUDGET"
    elif is_priority:
        reason = "PRIORITY_NEW_SYMBOL"
    else:
        reason = "NEW_SYMBOL"
    return AlertGovernorDecision(
        "CREATE",
        reason,
        reservation_token=reservation_token,
    )


def record_opportunity_alert(
    *,
    identity: str,
    transition_key: str,
    message_id: int,
    created_new: bool,
    reservation_token: str | None = None,
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
            reservations = _active_reservations(state, now=now)
            if created_new and reservation_token:
                reservation = reservations.get(reservation_token)
                if isinstance(reservation, dict) and (
                    str(reservation.get("identity") or "") != identity
                    or str(reservation.get("transition_key") or "") != transition_key
                ):
                    return
                # Telegram delivery already succeeded. If the reservation has
                # expired or disappeared, capacity is no longer reserved but the
                # delivered card must still become canonical to prevent duplicates.
                reservations.pop(reservation_token, None)
            state["new_card_reservations"] = reservations

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

            history = _pruned_history(state, now=now)
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


def release_opportunity_alert_reservation(
    reservation_token: str | None,
    *,
    state_file: Path | None = None,
) -> None:
    """Release unused CREATE capacity after a failed delivery attempt."""
    if not reservation_token:
        return
    target = state_file or STATE_FILE
    lock = target.parent / f".{target.name}.lock"
    try:
        with registry_lock(lock):
            state = load_json(target)
            reservations = _active_reservations(state, now=_now())
            reservations.pop(str(reservation_token), None)
            state["new_card_reservations"] = reservations
            save_json_atomic(target, state)
    except (OSError, TimeoutError, RegistryIOError):
        return
