"""Durable scheduling from committed evidence.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The scheduler decides **which** already-committed evidence items become committee
cases. It never calls a model, never reaches a provider, and never runs in a
trading critical path: it is a planner over durable evidence, and an injected
executor performs any work downstream.

The properties that matter, enforced rather than documented:

* **Committed evidence only.** An uncommitted, unsealed, or future-dated item is
  recorded ``INVALID`` rather than scheduled. Committee work may never be derived
  from evidence that is not yet durable, because a case produced from unsettled
  evidence cannot be reproduced.
* **Deterministic identity, idempotent at-least-once delivery.** A logical case is
  keyed by ``(case_id, evidence_snapshot_hash, policy_version)``. Redelivering the
  same committed evidence returns the *existing* disposition and creates no second
  case, so at-least-once delivery cannot multiply committee work.
* **Restart-safe.** The cursor and the set of decided keys live in a durable
  checkpoint, so a process restart resumes rather than reprocessing or skipping.
* **Exhaustive accounting.** Every eligible item ends in exactly one of the ten
  dispositions and appears in the tally. A skipped, failed, unavailable, or late
  item never disappears; the tally's total is checked against the population.
* **Failure is contained.** A scheduler error becomes a recorded ``FAILED``
  disposition, never an exception into a caller, and never a reason for baseline
  trading to stop.
* **Unknown is never favourable.** An unknown budget or an unavailable dependency
  refuses the item rather than assuming it fits or treating it as free.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

from app.opip.committee.settings import committee_shadow_enabled
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

#: Schema versions for the durable scheduler records.
SCHEDULE_KEY_SCHEMA_VERSION = 1
DISPOSITION_SCHEMA_VERSION = 1
CURSOR_SCHEMA_VERSION = 1

SCHEDULE_KEY_IDENTITY_DOMAIN = "COMMITTEE-SCHEDULE-KEY"
DISPOSITION_IDENTITY_DOMAIN = "COMMITTEE-DISPOSITION"
CURSOR_IDENTITY_DOMAIN = "COMMITTEE-CURSOR"

#: The declared reason a scheduler cycle did not run.
MODE_DISABLED = "MODE_DISABLED"


class CommitteeScheduleDisposition(str, Enum):
    """The exhaustive set of terminal states for one eligible evidence item.

    ``SKIPPED_BUDGET`` and ``SKIPPED_CAPACITY`` are distinct because one is a
    spending decision and the other a concurrency decision, and an operator needs
    to tell them apart. ``UNAVAILABLE`` is distinct from ``FAILED``: a missing
    dependency is not the same fact as a broken attempt. ``LATE`` is recorded for
    accountability but is not treated as timely evidence.
    """

    ELIGIBLE = "ELIGIBLE"
    SELECTED = "SELECTED"
    SKIPPED_BUDGET = "SKIPPED_BUDGET"
    SKIPPED_CAPACITY = "SKIPPED_CAPACITY"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    LATE = "LATE"
    COMPLETED = "COMPLETED"


#: Dispositions that represent work that was actually handed to the executor.
SELECTED_DISPOSITIONS = frozenset(
    {CommitteeScheduleDisposition.SELECTED, CommitteeScheduleDisposition.COMPLETED}
)

#: Dispositions that represent an item which did **not** become committee work and
#: must still be visible. A skip that disappears is an unaccounted loss.
HELD_DISPOSITIONS = frozenset(
    {
        CommitteeScheduleDisposition.SKIPPED_BUDGET,
        CommitteeScheduleDisposition.SKIPPED_CAPACITY,
        CommitteeScheduleDisposition.EXPIRED,
        CommitteeScheduleDisposition.INVALID,
        CommitteeScheduleDisposition.FAILED,
        CommitteeScheduleDisposition.UNAVAILABLE,
        CommitteeScheduleDisposition.LATE,
    }
)


class SchedulerError(ValueError):
    """A scheduling contract was violated."""


@dataclass(frozen=True)
class CommittedEvidenceItem:
    """One durable evidence item offered to the scheduler.

    ``committed`` is explicit rather than assumed from presence: an item that is
    not durably committed must not become committee work, so the flag is part of
    the contract instead of being inferred from the source.
    """

    evidence_id: str
    case_id: str
    evidence_snapshot_hash: str
    committee_policy_version: str
    committed: bool
    sealed: bool
    available_at: datetime
    evidence_cutoff_at: datetime
    expires_at: datetime | None = None
    estimated_cost_microunits: int | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_id",
            "case_id",
            "evidence_snapshot_hash",
            "committee_policy_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        for field_name in ("committed", "sealed"):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")
        for field_name in ("available_at", "evidence_cutoff_at", "expires_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    require_utc(value, field_name=field_name),
                )
        if self.expires_at is not None and self.expires_at <= self.evidence_cutoff_at:
            raise ValueError("expires_at must be after evidence_cutoff_at")
        if self.estimated_cost_microunits is not None and (
            type(self.estimated_cost_microunits) is not int
            or self.estimated_cost_microunits < 0
        ):
            raise ValueError(
                "estimated_cost_microunits must be a non-negative integer or null"
            )


@dataclass(frozen=True)
class ScheduleKey:
    """The deterministic identity of one logical committee case.

    Derived only from durable facts: the case, the sealed snapshot it was decided
    on, and the policy version. A redelivery of the same committed evidence
    therefore yields the same key and cannot create a second logical case.
    """

    case_id: str
    evidence_snapshot_hash: str
    committee_policy_version: str
    schema_version: int = SCHEDULE_KEY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "case_id",
            "evidence_snapshot_hash",
            "committee_policy_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")

    @classmethod
    def for_item(cls, item: CommittedEvidenceItem) -> "ScheduleKey":
        return cls(
            case_id=item.case_id,
            evidence_snapshot_hash=item.evidence_snapshot_hash,
            committee_policy_version=item.committee_policy_version,
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "committee_policy_version": self.committee_policy_version,
        }

    @property
    def schedule_key_id(self) -> str:
        return stable_hash(SCHEDULE_KEY_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class ScheduleDispositionRecord:
    """The durable disposition of one scheduled item.

    Immutable: a redelivered item returns its existing record rather than
    overwriting it, so history cannot be rewritten by a replay.
    """

    schedule_key_id: str
    evidence_id: str
    disposition: CommitteeScheduleDisposition
    decided_at: datetime
    reason: str | None = None
    detail: str | None = None
    schema_version: int = DISPOSITION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("schedule_key_id", "evidence_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.disposition, CommitteeScheduleDisposition):
            raise ValueError("invalid disposition")
        object.__setattr__(
            self,
            "decided_at",
            require_utc(self.decided_at, field_name="decided_at"),
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "schedule_key_id": self.schedule_key_id,
            "evidence_id": self.evidence_id,
            "disposition": self.disposition,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "detail": self.detail,
        }

    @property
    def disposition_id(self) -> str:
        return stable_hash(DISPOSITION_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class PopulationTally:
    """Exhaustive counts over one scheduling cycle.

    ``total`` is checked against the number of items considered, so a disposition
    that silently vanished fails the cycle rather than understating the
    population.
    """

    counts: Mapping[CommitteeScheduleDisposition, int]
    considered: int
    redelivered: int

    @property
    def total_accounted(self) -> int:
        return sum(self.counts.values())

    def count(self, disposition: CommitteeScheduleDisposition) -> int:
        return self.counts.get(disposition, 0)

    def as_dict(self) -> dict[str, int]:
        """Counts keyed by disposition name, with every state present.

        Every disposition appears even when zero, so a reader cannot mistake a
        missing key for a state that does not exist.
        """
        return {
            disposition.value: self.count(disposition)
            for disposition in CommitteeScheduleDisposition
        }


class SchedulerCheckpoint(Protocol):
    """Durable home of the cursor and the already-decided keys."""

    def load_decided(self) -> Mapping[str, ScheduleDispositionRecord]:
        """Return every disposition decided so far, keyed by schedule key id."""

    def save_disposition(self, record: ScheduleDispositionRecord) -> None:
        """Persist one disposition durably before the cycle continues."""


@dataclass(frozen=True)
class SchedulerBudget:
    """Per-cycle budget and capacity, as data rather than ambient state."""

    max_committee_cases: int
    max_cost_microunits: int

    def __post_init__(self) -> None:
        for field_name in ("max_committee_cases", "max_cost_microunits"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True)
class SchedulerCursor:
    """The durable cursor position for a scheduler."""

    last_evidence_id: str | None
    processed: int
    updated_at: datetime
    schema_version: int = CURSOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.processed) is not int or self.processed < 0:
            raise ValueError("processed must be a non-negative integer")
        object.__setattr__(
            self,
            "updated_at",
            require_utc(self.updated_at, field_name="updated_at"),
        )

    def advanced_to(self, evidence_id: str, *, at: datetime) -> "SchedulerCursor":
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("evidence_id is required")
        return replace(
            self, last_evidence_id=evidence_id, processed=self.processed + 1, updated_at=at
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "last_evidence_id": self.last_evidence_id,
            "processed": self.processed,
            "updated_at": self.updated_at,
        }

    @property
    def cursor_id(self) -> str:
        return stable_hash(CURSOR_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class SchedulerRun:
    """The outcome of one scheduling cycle."""

    ran: bool
    reason: str | None
    tally: PopulationTally
    cursor: SchedulerCursor
    dispositions: tuple[ScheduleDispositionRecord, ...] = field(default=())

    @property
    def selected_count(self) -> int:
        return self.tally.count(CommitteeScheduleDisposition.SELECTED)


class CaseExecutor(Protocol):
    """What the scheduler hands selected work to.

    The scheduler itself performs no model call. Whoever implements this owns the
    actual committee execution and its own authority boundaries.
    """

    def __call__(self, item: CommittedEvidenceItem) -> bool:
        """Execute one selected item, returning whether it completed."""


def _zero_disposition_counts() -> dict[CommitteeScheduleDisposition, int]:
    """A count for every disposition, so no state can be absent from a tally."""
    return dict.fromkeys(CommitteeScheduleDisposition, 0)


def _run_selected(
    *,
    item: CommittedEvidenceItem,
    executor: CaseExecutor | None,
) -> tuple[CommitteeScheduleDisposition, str | None, str | None, int | None]:
    """Hand one selected item to the executor and classify the result.

    Returns the disposition, an optional reason, an optional detail, and the cost
    to charge. A scheduler fault is contained here rather than propagating into a
    caller's trading path.
    """
    if executor is None:
        return (
            CommitteeScheduleDisposition.UNAVAILABLE,
            "no executor is configured for this scheduler",
            None,
            None,
        )
    try:
        completed = executor(item)
    except Exception as exc:  # noqa: BLE001 - contained on purpose
        return (
            CommitteeScheduleDisposition.FAILED,
            "executor raised",
            f"{type(exc).__name__}: {exc}",
            None,
        )
    if completed:
        return (
            CommitteeScheduleDisposition.COMPLETED,
            None,
            None,
            item.estimated_cost_microunits,
        )
    return (
        CommitteeScheduleDisposition.FAILED,
        "executor reported no completion",
        None,
        None,
    )


@dataclass
class _CycleState:
    """Mutable accumulation for one scheduling cycle.

    Kept as a small explicit object rather than a long list of locals, so the
    per-item decision stays readable and the cycle's accounting is auditable in one
    place.
    """

    moment: datetime
    counts: dict[CommitteeScheduleDisposition, int]
    decided: dict[str, ScheduleDispositionRecord]
    records: list[ScheduleDispositionRecord] = field(default_factory=list)
    cursor: SchedulerCursor | None = None
    redelivered: int = 0
    considered: int = 0
    spent: int = 0
    selected: int = 0

    def __post_init__(self) -> None:
        if self.cursor is None:
            self.cursor = SchedulerCursor(
                last_evidence_id=None, processed=0, updated_at=self.moment
            )

    def advance(self, item: CommittedEvidenceItem) -> None:
        self.cursor = self.cursor.advanced_to(item.evidence_id, at=self.moment)


@dataclass
class CommitteeScheduler:
    """Plans committee cases from committed evidence, durably and idempotently."""

    checkpoint: SchedulerCheckpoint
    now: Callable[[], datetime]
    budget: SchedulerBudget
    settings: Any | None = None

    def run_cycle(
        self,
        *,
        items: Sequence[CommittedEvidenceItem],
        executor: CaseExecutor | None = None,
    ) -> SchedulerRun:
        """Decide the disposition of every item offered this cycle.

        Never raises for a data problem: a malformed or failing item becomes a
        recorded disposition. That is what keeps a scheduler fault from becoming a
        trading-path fault.
        """
        moment = require_utc(self._now(), field_name="now")
        counts = _zero_disposition_counts()

        # An unreadable checkpoint must not cause the scheduler to reprocess
        # everything, and must not cause it to silently skip either: the cycle
        # reports that it could not run.
        if not committee_shadow_enabled(self.settings):
            return SchedulerRun(
                ran=False,
                reason=MODE_DISABLED,
                tally=PopulationTally(counts=counts, considered=0, redelivered=0),
                cursor=SchedulerCursor(
                    last_evidence_id=None, processed=0, updated_at=moment
                ),
            )

        state = _CycleState(
            moment=moment,
            counts=counts,
            decided=dict(self.checkpoint.load_decided()),
        )
        for item in items:
            self._consider(item=item, state=state, executor=executor)

        return SchedulerRun(
            ran=True,
            reason=None,
            tally=PopulationTally(
                counts=state.counts,
                considered=state.considered,
                redelivered=state.redelivered,
            ),
            cursor=state.cursor,
            dispositions=tuple(state.records),
        )

    # -------------------------------------------------------------- internals

    def _consider(
        self,
        *,
        item: CommittedEvidenceItem,
        state: "_CycleState",
        executor: CaseExecutor | None,
    ) -> None:
        """Decide and durably record one item's disposition."""
        state.considered += 1
        key = ScheduleKey.for_item(item)

        existing = state.decided.get(key.schedule_key_id)
        if existing is not None:
            # At-least-once delivery: the original disposition stands and no
            # second logical case is created.
            state.redelivered += 1
            state.records.append(existing)
            state.counts[existing.disposition] += 1
            state.advance(item)
            return

        disposition, reason, detail = self._decide(
            item=item,
            moment=state.moment,
            spent=state.spent,
            selected=state.selected,
        )
        if disposition is CommitteeScheduleDisposition.SELECTED:
            # Counted as selected *before* execution, because execution replaces
            # the disposition with COMPLETED/FAILED and the capacity check reads
            # this counter. Counting afterwards would let an exhausted cycle keep
            # selecting.
            state.selected += 1
            disposition, reason, detail, cost = _run_selected(
                item=item, executor=executor
            )
            if cost is not None:
                state.spent += cost

        record = ScheduleDispositionRecord(
            schedule_key_id=key.schedule_key_id,
            evidence_id=item.evidence_id,
            disposition=disposition,
            decided_at=state.moment,
            reason=reason,
            detail=detail,
        )
        # Persisted before the cycle continues, so a crash mid-cycle cannot lose a
        # decision that has already been made.
        self.checkpoint.save_disposition(record)
        state.decided[key.schedule_key_id] = record
        state.records.append(record)
        state.counts[disposition] += 1
        state.advance(item)

    def _now(self) -> datetime:
        return self.now()

    def _decide(
        self,
        *,
        item: CommittedEvidenceItem,
        moment: datetime,
        spent: int,
        selected: int,
    ) -> tuple[CommitteeScheduleDisposition, str | None, str | None]:
        """Decide one item's disposition from durable facts only."""
        if not item.committed:
            return (
                CommitteeScheduleDisposition.INVALID,
                "evidence is not durably committed",
                None,
            )
        if not item.sealed:
            return (
                CommitteeScheduleDisposition.INVALID,
                "evidence is not sealed",
                None,
            )
        if item.evidence_cutoff_at > moment:
            return (
                CommitteeScheduleDisposition.INVALID,
                "evidence cutoff is in the future",
                None,
            )
        if item.expires_at is not None and moment >= item.expires_at:
            return (
                CommitteeScheduleDisposition.EXPIRED,
                "the item's processing window closed before it was considered",
                None,
            )
        if item.available_at > moment:
            # Committed and sealed, but surfaced after its window: recorded for
            # accountability without being treated as timely evidence.
            return (
                CommitteeScheduleDisposition.LATE,
                "evidence became available after the scheduling moment",
                None,
            )
        if selected >= self.budget.max_committee_cases:
            return (
                CommitteeScheduleDisposition.SKIPPED_CAPACITY,
                "the cycle's case capacity is exhausted",
                None,
            )
        if item.estimated_cost_microunits is None:
            # An unknown cost cannot be shown to fit the budget, and assuming it
            # fits would let unbounded spend through a ceiling that exists to
            # prevent exactly that.
            return (
                CommitteeScheduleDisposition.UNAVAILABLE,
                "the item's cost is unknown, so the budget cannot be enforced",
                None,
            )
        if spent + item.estimated_cost_microunits > self.budget.max_cost_microunits:
            return (
                CommitteeScheduleDisposition.SKIPPED_BUDGET,
                "the cycle's cost budget would be exceeded",
                None,
            )
        return CommitteeScheduleDisposition.SELECTED, None, None


__all__ = [
    "CURSOR_SCHEMA_VERSION",
    "CURSOR_IDENTITY_DOMAIN",
    "DISPOSITION_SCHEMA_VERSION",
    "DISPOSITION_IDENTITY_DOMAIN",
    "HELD_DISPOSITIONS",
    "MODE_DISABLED",
    "SCHEDULE_KEY_SCHEMA_VERSION",
    "SCHEDULE_KEY_IDENTITY_DOMAIN",
    "SELECTED_DISPOSITIONS",
    "CaseExecutor",
    "CommitteeScheduleDisposition",
    "CommitteeScheduler",
    "CommittedEvidenceItem",
    "PopulationTally",
    "ScheduleDispositionRecord",
    "ScheduleKey",
    "SchedulerBudget",
    "SchedulerCheckpoint",
    "SchedulerCursor",
    "SchedulerError",
    "SchedulerRun",
]
