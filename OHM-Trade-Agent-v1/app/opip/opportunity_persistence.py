"""F4 opportunity-lifecycle persistence producer seam (dormant).

This module is the only application seam that turns an already-computed F4
lifecycle result into a canonical writer intent. It is deliberately thin:

* It never instantiates ``CanonicalWriter`` or opens a database. It submits only
  through an injected canonical writer client (the existing ``SUBMIT`` path).
* It reads no clock. ``event_time`` is the validated lifecycle event's own
  evaluation instant; the canonical writer keeps ``recorded_at`` as its receipt
  time and never derives F4 semantics from it.
* It grants no runtime authority. It is not called by ``run_cycle``, any scan,
  the Feature Bus, the watch queue, the radar, pending setups, alert routing,
  Telegram, paper, execution, risk, the Committee or any dashboard. It is
  deployed but dormant until a separate, OWNER-authorized integration increment.

ONE record is ONE real state change: a changed result with exactly one event
yields exactly one writer intent, an unchanged result with zero events yields no
write at all, and anything ambiguous fails closed.
"""

from __future__ import annotations

from typing import Protocol

from app.opip.canonical.models import WriterAck, WriterIntent
from app.opip.canonical.paths import EVENT_SCHEMA_VERSION
from app.opip.contracts import (
    OpportunityEpisode,
    OpportunityLifecycleEvent,
    OpportunityLifecycleResult,
)
from app.opip.contracts import opportunity_persistence as persistence

#: Re-exported under a local name so the producer seam and its callers share the
#: single fail-closed persistence error without a second definition.
OpportunityPersistenceError = persistence.OpportunityPersistenceError


class OpportunityWriterClient(Protocol):
    """The minimal canonical writer client surface this seam depends on."""

    def submit(self, intent: WriterIntent) -> WriterAck: ...


def build_opportunity_transition_intent(
    episode: OpportunityEpisode,
    event: OpportunityLifecycleEvent,
) -> WriterIntent:
    """Build exactly one validated canonical writer intent for one transition.

    The intent carries LOW priority, no ops handoff, the deterministic ``OPLT:``
    idempotency key derived only from the lifecycle event identity, the
    correlation id set to the episode id, and the causation id set to the source
    claim id when one exists (never fabricated for an expiry).
    """
    payload = persistence.build_opportunity_transition_payload(episode, event)
    normalized = persistence.validate_opportunity_transition_record(
        persistence.OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED, payload
    )
    return WriterIntent(
        schema_version=EVENT_SCHEMA_VERSION,
        priority=persistence.OPPORTUNITY_LIFECYCLE_PRIORITY,
        idempotency_key=persistence.opportunity_transition_idempotency_key(normalized),
        event_type=persistence.OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED,
        payload=normalized,
        event_time=persistence.opportunity_transition_event_time(normalized),
        correlation_id=persistence.opportunity_transition_correlation_id(normalized),
        causation_id=persistence.opportunity_transition_causation_id(normalized),
        ops_handoff=None,
    )


def persist_opportunity_result(
    client: OpportunityWriterClient,
    result: OpportunityLifecycleResult,
) -> WriterAck | None:
    """Persist one lifecycle result, or write nothing for an unchanged result.

    * ``changed=True`` with exactly one event -> exactly one writer intent.
    * ``changed=False`` with zero events -> no write (returns ``None``).
    * Anything else (more than one event, an unchanged result that somehow emits
      events, or a changed result with no event) fails closed.
    """
    if not isinstance(result, OpportunityLifecycleResult):
        raise OpportunityPersistenceError(
            "result must be an OpportunityLifecycleResult"
        )
    events = result.events
    if not result.changed:
        if events:
            raise OpportunityPersistenceError(
                "an unchanged lifecycle result must not emit events"
            )
        return None
    if len(events) != 1:
        raise OpportunityPersistenceError(
            "v1 persistence requires exactly one event for a changed result"
        )
    intent = build_opportunity_transition_intent(result.episode, events[0])
    return client.submit(intent)


__all__ = [
    "OpportunityWriterClient",
    "build_opportunity_transition_intent",
    "persist_opportunity_result",
]
