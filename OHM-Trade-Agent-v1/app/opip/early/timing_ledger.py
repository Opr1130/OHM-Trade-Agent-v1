"""Point-in-time milestone ledger (Issue #223 sections 14 and 15).

Lead time cannot be claimed from a card-creation timestamp. The alert governor
may return ``EDIT`` and silently update an existing card, or ``SUPPRESS`` it
entirely, so "a card exists" and "the operator was notified" are different
events. This ledger keeps them separate:

``card_created_at``
    a new operator card was created.
``card_edited_at``
    an existing card was updated in place, which may be silent.
``notification_delivered_at``
    a notification actually reached the operator.

Milestones are monotonic per episode: the first time a milestone is observed
wins, so a later scan cannot rewrite history. Forward outcomes (peak, MFE,
MAE, final result) are structurally excluded — attempting to record one raises
:class:`app.opip.early.point_in_time.LookaheadError`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.opip.early.point_in_time import (
    assert_point_in_time_safe,
    parse_timestamp,
)
from app.opip.early.taxonomy import (
    EvidenceGrade,
    MarketPhase,
    coerce_evidence_grade,
    coerce_market_phase,
    is_early_phase,
)

TIMING_LEDGER_SCHEMA_VERSION = 1
TIMING_LEDGER_VERSION = "opip-early-timing-ledger-v1"

#: Quiet gap after which a new episode is opened for the same symbol.
#: Matches the intelligence_journey active-window convention so two separate
#: RAY moves cannot share first_observed / first_qualified / delivery stamps.
EPISODE_RESET_GAP_SECONDS = 48 * 60 * 60

MILESTONE_FIRST_OBSERVED = "first_observed_at"
MILESTONE_FIRST_IGNITION = "first_ignition_at"
MILESTONE_FIRST_CORROBORATED = "first_corroborated_at"
MILESTONE_FIRST_QUALIFIED = "first_qualified_at"
MILESTONE_FIRST_OPERATOR_ALERT = "first_operator_alert_at"
MILESTONE_FIRST_DELIVERED = "first_delivered_notification_at"
MILESTONE_FIRST_ACTIONABLE = "first_actionable_at"
MILESTONE_LATE_EXTENSION = "late_extension_at"
MILESTONE_EXHAUSTION = "exhaustion_at"

#: Every milestone, in causal order. Anchor prices mirror these names.
MILESTONE_ORDER = (
    MILESTONE_FIRST_OBSERVED,
    MILESTONE_FIRST_IGNITION,
    MILESTONE_FIRST_CORROBORATED,
    MILESTONE_FIRST_QUALIFIED,
    MILESTONE_FIRST_OPERATOR_ALERT,
    MILESTONE_FIRST_DELIVERED,
    MILESTONE_FIRST_ACTIONABLE,
    MILESTONE_LATE_EXTENSION,
    MILESTONE_EXHAUSTION,
)

#: Card lifecycle events, tracked separately from notification delivery.
CARD_CREATED = "card_created_at"
CARD_EDITED = "card_edited_at"
NOTIFICATION_DELIVERED = "notification_delivered_at"


def _iso(value: datetime | str | None) -> str | None:
    parsed = parse_timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def _price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass(frozen=True)
class EpisodeTimingLedger:
    """Monotonic milestone timestamps and anchor prices for one episode."""

    symbol: str
    episode_id: str
    schema_version: int = TIMING_LEDGER_SCHEMA_VERSION
    version: str = TIMING_LEDGER_VERSION
    milestones: Mapping[str, str] = None  # type: ignore[assignment]
    anchor_prices: Mapping[str, float] = None  # type: ignore[assignment]
    card_created_at: str | None = None
    card_edited_at: str | None = None
    notification_delivered_at: str | None = None
    card_edit_count: int = 0
    delivered_notification_count: int = 0

    def __post_init__(self) -> None:
        if self.milestones is None:
            object.__setattr__(self, "milestones", {})
        if self.anchor_prices is None:
            object.__setattr__(self, "anchor_prices", {})

    def milestone(self, name: str) -> str | None:
        return dict(self.milestones).get(name)

    def anchor_price(self, name: str) -> float | None:
        return dict(self.anchor_prices).get(name)

    @property
    def operator_was_notified(self) -> bool:
        """Whether a notification actually reached the operator.

        A created or edited card alone is not notification.
        """
        return self.notification_delivered_at is not None

    def lead_time_seconds(self, *, start: str, end: str) -> float | None:
        """Seconds between two recorded milestones, or ``None`` if unrecorded."""
        first = parse_timestamp(self.milestone(start))
        second = parse_timestamp(self.milestone(end))
        if first is None or second is None:
            return None
        return (second - first).total_seconds()

    def move_consumed_pct(self, *, start: str, end: str) -> float | None:
        """Percentage move already consumed between two milestones."""
        first = self.anchor_price(start)
        second = self.anchor_price(end)
        if first is None or second is None or first <= 0:
            return None
        return (second / first - 1.0) * 100.0

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": int(self.schema_version),
            "version": self.version,
            "symbol": self.symbol,
            "episode_id": self.episode_id,
            "milestones": {
                name: dict(self.milestones)[name]
                for name in MILESTONE_ORDER
                if name in dict(self.milestones)
            },
            "anchor_prices": {
                name: dict(self.anchor_prices)[name]
                for name in MILESTONE_ORDER
                if name in dict(self.anchor_prices)
            },
            CARD_CREATED: self.card_created_at,
            CARD_EDITED: self.card_edited_at,
            NOTIFICATION_DELIVERED: self.notification_delivered_at,
            "card_edit_count": int(self.card_edit_count),
            "delivered_notification_count": int(self.delivered_notification_count),
            "operator_was_notified": self.operator_was_notified,
            "measurement_only": True,
            "trade_authority_changed": False,
        }
        assert_point_in_time_safe(payload["milestones"])
        assert_point_in_time_safe(payload["anchor_prices"])
        return payload


def new_ledger(*, symbol: str, episode_id: str) -> EpisodeTimingLedger:
    return EpisodeTimingLedger(symbol=str(symbol).upper(), episode_id=str(episode_id))


def early_episode_id(*, symbol: str, episode_started_at: datetime | str) -> str:
    """Deterministic episode identity: symbol + episode start, never symbol alone.

    Two separate moves for the same symbol therefore cannot share
    ``first_observed_at``, ``first_qualified_at``, delivery stamps or anchors.
    """
    stamp = parse_timestamp(episode_started_at)
    if stamp is None:
        raise ValueError("episode_started_at must be a timezone-aware datetime")
    started = stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"EP:{str(symbol).strip().upper()}:{started}"


def should_reset_episode(
    existing: EpisodeTimingLedger | None,
    *,
    decision_at: datetime | str,
    phase: MarketPhase | str | None = None,
) -> bool:
    """Whether a new episode must open for this symbol.

    Reset when:
    - no prior episode exists; or
    - quiet gap since the last recorded milestone exceeds
      :data:`EPISODE_RESET_GAP_SECONDS`; or
    - the prior episode already reached LATE_EXTENSION / EXHAUSTION and the
      current observation is again early (IGNITION / EARLY_EXPANSION).
    """
    if existing is None:
        return True
    moment = parse_timestamp(decision_at)
    if moment is None:
        return False
    last_stamps = [
        parse_timestamp(value)
        for value in dict(existing.milestones).values()
    ]
    last_stamps = [item for item in last_stamps if item is not None]
    if not last_stamps:
        return True
    newest = max(last_stamps)
    if (moment - newest).total_seconds() > EPISODE_RESET_GAP_SECONDS:
        return True
    prior_extended = bool(
        existing.milestone(MILESTONE_LATE_EXTENSION)
        or existing.milestone(MILESTONE_EXHAUSTION)
    )
    if prior_extended and phase is not None and is_early_phase(phase):
        return True
    return False


def resolve_episode_ledger(
    *,
    symbol: str,
    decision_at: datetime | str,
    phase: MarketPhase | str | None = None,
    existing: EpisodeTimingLedger | None = None,
) -> EpisodeTimingLedger:
    """Return the ledger for the current episode, opening a new one when needed."""
    key = str(symbol).strip().upper()
    if existing is not None and not should_reset_episode(
        existing, decision_at=decision_at, phase=phase
    ):
        return existing
    return new_ledger(
        symbol=key,
        episode_id=early_episode_id(symbol=key, episode_started_at=decision_at),
    )


def record_milestone(
    ledger: EpisodeTimingLedger,
    *,
    milestone: str,
    observed_at: datetime | str,
    anchor_price: Any = None,
) -> EpisodeTimingLedger:
    """Record a milestone the first time it is observed.

    Monotonic by construction: a repeat observation is ignored rather than
    overwriting the original timestamp, so a later scan cannot manufacture a
    better-looking lead time.
    """
    if milestone not in MILESTONE_ORDER:
        raise ValueError(f"unknown milestone: {milestone}")
    assert_point_in_time_safe((milestone,))
    stamp = _iso(observed_at)
    if stamp is None:
        return ledger

    milestones = dict(ledger.milestones)
    anchors = dict(ledger.anchor_prices)
    if milestone in milestones:
        return ledger
    milestones[milestone] = stamp
    price = _price(anchor_price)
    if price is not None:
        anchors[milestone] = price
    return replace(ledger, milestones=milestones, anchor_prices=anchors)


def record_card_created(
    ledger: EpisodeTimingLedger,
    *,
    created_at: datetime | str,
) -> EpisodeTimingLedger:
    """Record card creation. This is not operator notification."""
    stamp = _iso(created_at)
    if stamp is None or ledger.card_created_at is not None:
        return ledger
    return replace(ledger, card_created_at=stamp)


def record_card_edited(
    ledger: EpisodeTimingLedger,
    *,
    edited_at: datetime | str,
) -> EpisodeTimingLedger:
    """Record a silent in-place card edit, counting churn."""
    stamp = _iso(edited_at)
    if stamp is None:
        return ledger
    return replace(
        ledger,
        card_edited_at=ledger.card_edited_at or stamp,
        card_edit_count=int(ledger.card_edit_count) + 1,
    )


def record_notification_delivered(
    ledger: EpisodeTimingLedger,
    *,
    delivered_at: datetime | str,
    anchor_price: Any = None,
) -> EpisodeTimingLedger:
    """Record that a notification actually reached the operator.

    This is the only event that may support a delivered-lead-time claim, so
    it also fills :data:`MILESTONE_FIRST_DELIVERED`.
    """
    stamp = _iso(delivered_at)
    if stamp is None:
        return ledger
    updated = replace(
        ledger,
        notification_delivered_at=ledger.notification_delivered_at or stamp,
        delivered_notification_count=int(ledger.delivered_notification_count) + 1,
    )
    return record_milestone(
        updated,
        milestone=MILESTONE_FIRST_DELIVERED,
        observed_at=stamp,
        anchor_price=anchor_price,
    )


def observe_phase(
    ledger: EpisodeTimingLedger,
    *,
    phase: MarketPhase | str,
    grade: EvidenceGrade | str | None = None,
    observed_at: datetime | str,
    reference_price: Any = None,
    actionable: bool = False,
) -> EpisodeTimingLedger:
    """Advance every milestone implied by one point-in-time observation."""
    updated = record_milestone(
        ledger,
        milestone=MILESTONE_FIRST_OBSERVED,
        observed_at=observed_at,
        anchor_price=reference_price,
    )

    resolved_phase = coerce_market_phase(phase)
    phase_milestones = {
        MarketPhase.IGNITION: MILESTONE_FIRST_IGNITION,
        MarketPhase.LATE_EXTENSION: MILESTONE_LATE_EXTENSION,
        MarketPhase.EXHAUSTION_RISK: MILESTONE_EXHAUSTION,
    }
    milestone = phase_milestones.get(resolved_phase)
    if milestone is not None:
        updated = record_milestone(
            updated,
            milestone=milestone,
            observed_at=observed_at,
            anchor_price=reference_price,
        )

    if grade is not None:
        resolved_grade = coerce_evidence_grade(grade)
        if resolved_grade in {EvidenceGrade.CORROBORATED, EvidenceGrade.QUALIFIED}:
            updated = record_milestone(
                updated,
                milestone=MILESTONE_FIRST_CORROBORATED,
                observed_at=observed_at,
                anchor_price=reference_price,
            )
        if resolved_grade is EvidenceGrade.QUALIFIED:
            updated = record_milestone(
                updated,
                milestone=MILESTONE_FIRST_QUALIFIED,
                observed_at=observed_at,
                anchor_price=reference_price,
            )

    if actionable:
        updated = record_milestone(
            updated,
            milestone=MILESTONE_FIRST_ACTIONABLE,
            observed_at=observed_at,
            anchor_price=reference_price,
        )
    return updated


def ledger_from_dict(payload: Mapping[str, Any]) -> EpisodeTimingLedger:
    """Rebuild a ledger from a persisted row."""
    milestones = payload.get("milestones")
    anchors = payload.get("anchor_prices")
    return EpisodeTimingLedger(
        symbol=str(payload.get("symbol") or "").upper(),
        episode_id=str(payload.get("episode_id") or ""),
        schema_version=int(payload.get("schema_version") or TIMING_LEDGER_SCHEMA_VERSION),
        version=str(payload.get("version") or TIMING_LEDGER_VERSION),
        milestones={
            str(name): str(value)
            for name, value in dict(milestones if isinstance(milestones, Mapping) else {}).items()
            if name in MILESTONE_ORDER
        },
        anchor_prices={
            str(name): float(value)
            for name, value in dict(anchors if isinstance(anchors, Mapping) else {}).items()
            if name in MILESTONE_ORDER
        },
        card_created_at=payload.get(CARD_CREATED),
        card_edited_at=payload.get(CARD_EDITED),
        notification_delivered_at=payload.get(NOTIFICATION_DELIVERED),
        card_edit_count=int(payload.get("card_edit_count") or 0),
        delivered_notification_count=int(payload.get("delivered_notification_count") or 0),
    )


def summarize_delivery_timing(ledgers: Iterable[EpisodeTimingLedger]) -> dict[str, Any]:
    """Aggregate observation-to-delivery timing, never card-created time.

    The reported interval runs from first observation to delivered
    notification, so a *smaller* value is better. It is named as a delay
    rather than a lead time because the two read in opposite directions.

    ``episodes_with_card_but_no_delivery`` is the count that makes a
    card-creation-based timing claim unsafe; it must be reported alongside
    any timing figure.
    """
    rows = list(ledgers)
    delivered = [item for item in rows if item.operator_was_notified]
    delays = [
        seconds
        for seconds in (
            item.lead_time_seconds(
                start=MILESTONE_FIRST_OBSERVED,
                end=MILESTONE_FIRST_DELIVERED,
            )
            for item in delivered
        )
        if seconds is not None
    ]
    consumed = [
        value
        for value in (
            item.move_consumed_pct(
                start=MILESTONE_FIRST_OBSERVED,
                end=MILESTONE_FIRST_DELIVERED,
            )
            for item in delivered
        )
        if value is not None
    ]
    return {
        "version": TIMING_LEDGER_VERSION,
        "episodes": len(rows),
        "episodes_delivered": len(delivered),
        "episodes_with_card_but_no_delivery": sum(
            1 for item in rows if item.card_created_at is not None and not item.operator_was_notified
        ),
        "total_card_edits": sum(int(item.card_edit_count) for item in rows),
        "median_observation_to_delivery_seconds": _median(delays),
        "median_move_consumed_before_delivery_pct": _median(consumed),
        "measurement_only": True,
        "trade_authority_changed": False,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return round(ordered[middle], 6)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 6)
