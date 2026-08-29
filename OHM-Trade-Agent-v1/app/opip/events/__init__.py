"""O'Pip Event Intelligence Foundation.

Sequence 2 is evidence infrastructure only. Nothing in this package owns
qualification, ranking, notification, order placement, or exchange authority.
"""

from app.opip.events.contract import (
    EventClass,
    EventIdentity,
    IngestOutcome,
    MappingStatus,
    OPipEvent,
)
from app.opip.events.storage import EventStore

__all__ = [
    "EventClass",
    "EventIdentity",
    "EventStore",
    "IngestOutcome",
    "MappingStatus",
    "OPipEvent",
]
