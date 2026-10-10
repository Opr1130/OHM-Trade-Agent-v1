"""SCHED: one deduplicated evaluation coordinator for the R5-A slice.

Adopted authority: `docs/architecture/v1.5.0/ARCHITECTURE.md` section 3
(`ME-04`-`ME-07`), section 2's "three operating speeds" and the R5-0 structural
contract `SC-03`. The clauses require that the stream path and the existing
slower scheduler converge on **identical semantic evaluation identities** for the
same cut-off and policy, that work stays bounded and fair, and that capacity is
reserved for protection, reconciliation and open positions before discovery.

`SC-03` is `OPEN`: every numeric budget below is `OQ-04` owner input, so
`CoordinatorLimits` has no defaults and refuses incomplete numbers rather than
choosing them. A limit object built by this module is unratified engineering
evidence, and the class records that the numbers are not owner-ratified.

Nothing here adds a worker, a second scheduler, a database or an async runtime.
This is a deterministic, in-process request ledger: every instant is supplied by
the caller, duplicates converge, and a repeat completion cannot produce a second
detector transition.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.features import DEFAULT_EVALUATION_GRID_SECONDS
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc

#: Schema version of the coordinator records.
COORDINATOR_SCHEMA_VERSION = "evaluation-coordinator-v1"

IDENTITY_PREFIX = "EEVL"
REQUEST_ID_PREFIX = "EREQ"

#: `OQ-04` remains open; this module never claims otherwise.
OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"


class CoordinatorContractError(ValueError):
    """A coordinator contract violation. Always fails closed."""


class EvaluationPath(str, Enum):
    """Which path requested an evaluation. Both converge on one identity."""

    STREAM_EVENT = "STREAM_EVENT"
    SCHEDULER_RECONCILIATION = "SCHEDULER_RECONCILIATION"
    RECOVERY_BACKFILL = "RECOVERY_BACKFILL"
    UNIVERSE_REFRESH = "UNIVERSE_REFRESH"


class WorkPriority(str, Enum):
    """Declared work classes. Protection outranks every discovery class."""

    PROTECTION = "PROTECTION"
    OPEN_POSITION = "OPEN_POSITION"
    RECONCILIATION = "RECONCILIATION"
    DISCOVERY = "DISCOVERY"
    OPTIONAL_AI = "OPTIONAL_AI"


#: Strict priority order. A smaller rank is served first, always.
PRIORITY_RANK: Mapping[WorkPriority, int] = MappingProxyType(
    {
        WorkPriority.PROTECTION: 0,
        WorkPriority.OPEN_POSITION: 1,
        WorkPriority.RECONCILIATION: 2,
        WorkPriority.DISCOVERY: 3,
        WorkPriority.OPTIONAL_AI: 4,
    }
)

#: Classes whose capacity is reserved ahead of discovery (`ME-05`).
RESERVED_PRIORITIES: tuple[WorkPriority, ...] = (
    WorkPriority.PROTECTION,
    WorkPriority.RECONCILIATION,
)

#: Classes that may be shed visibly when capacity is saturated.
SHEDDABLE_PRIORITIES: tuple[WorkPriority, ...] = (
    WorkPriority.DISCOVERY,
    WorkPriority.OPTIONAL_AI,
)


class RequestDisposition(str, Enum):
    """The lifecycle of one evaluation request."""

    ADMITTED = "ADMITTED"
    COALESCED = "COALESCED"
    DROPPED_BACKPRESSURE = "DROPPED_BACKPRESSURE"
    REJECTED_STALE = "REJECTED_STALE"
    SERVED = "SERVED"
    EXPIRED = "EXPIRED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


def evaluation_identity(
    *,
    instrument_version_id: str,
    evaluation_cutoff: datetime,
    policy_version: str,
) -> str:
    """The one semantic evaluation identity for a cut-off and policy.

    A stream trigger and a scheduler request for the same instrument, cut-off and
    policy produce the identical identity, so they cannot become two detector
    evaluations of the same facts.
    """

    for name, value in (
        ("instrument_version_id", instrument_version_id),
        ("policy_version", policy_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise CoordinatorContractError(f"{name} must be a non-empty string")
    cutoff = require_utc(evaluation_cutoff, field_name="evaluation_cutoff")
    return stable_hash(
        IDENTITY_PREFIX,
        {
            "instrument_version_id": instrument_version_id,
            "evaluation_cutoff": iso_z(cutoff, field_name="evaluation_cutoff"),
            "policy_version": policy_version,
        },
    )


@dataclass(frozen=True)
class CoordinatorLimits:
    """Owner-gated capacity and latency budgets. No value has a default."""

    queue_capacity: int
    queue_age_limit_intervals: int
    retry_ceiling: int
    per_instrument_budget_per_cycle: int
    protection_reserved_slots: int
    reconciliation_reserved_slots: int
    optional_ai_admitted: bool = False

    def __post_init__(self) -> None:
        for name in (
            "queue_capacity",
            "queue_age_limit_intervals",
            "retry_ceiling",
            "per_instrument_budget_per_cycle",
            "protection_reserved_slots",
            "reconciliation_reserved_slots",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise CoordinatorContractError(f"{name} must be an integer")
            if value <= 0:
                raise CoordinatorContractError(f"{name} must be positive")
            object.__setattr__(self, name, int(value))
        if not isinstance(self.optional_ai_admitted, bool):
            raise CoordinatorContractError("optional_ai_admitted must be a bool")
        if self.protection_reserved_slots + self.reconciliation_reserved_slots > (
            self.queue_capacity
        ):
            raise CoordinatorContractError(
                "reserved slots cannot exceed the declared queue capacity"
            )

    @property
    def ratified(self) -> bool:
        """Capacity and latency budgets are owner policy, never agent defaults."""

        return False

    @property
    def owner_input_status(self) -> str:
        return OWNER_INPUT_STATUS

    def age_limit(self) -> timedelta:
        return timedelta(
            seconds=self.queue_age_limit_intervals * DEFAULT_EVALUATION_GRID_SECONDS
        )


@dataclass(frozen=True)
class EvaluationRequest:
    """One deduplicated evaluation request."""

    request_id: str
    schema_version: str
    evaluation_identity: str
    path: EvaluationPath
    priority: WorkPriority
    instrument_version_id: str
    venue_instrument_id: str
    evaluation_cutoff: datetime
    requested_at_utc: datetime
    expires_at_utc: datetime
    policy_version: str
    attention_trigger_id: str | None = None
    attempts: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != COORDINATOR_SCHEMA_VERSION:
            raise CoordinatorContractError(
                f"schema_version is not {COORDINATOR_SCHEMA_VERSION}"
            )
        for name in (
            "request_id",
            "evaluation_identity",
            "instrument_version_id",
            "venue_instrument_id",
            "policy_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CoordinatorContractError(f"{name} must be a non-empty string")
        if not isinstance(self.path, EvaluationPath):
            raise CoordinatorContractError("path must be an EvaluationPath")
        if not isinstance(self.priority, WorkPriority):
            raise CoordinatorContractError("priority must be a WorkPriority")
        cutoff = require_utc(self.evaluation_cutoff, field_name="evaluation_cutoff")
        requested = require_utc(self.requested_at_utc, field_name="requested_at_utc")
        expires = require_utc(self.expires_at_utc, field_name="expires_at_utc")
        if cutoff > requested:
            raise CoordinatorContractError(
                "an evaluation cutoff cannot follow the request instant"
            )
        if expires <= requested:
            raise CoordinatorContractError(
                "a request must expire after the instant it was created"
            )
        object.__setattr__(self, "evaluation_cutoff", cutoff)
        object.__setattr__(self, "requested_at_utc", requested)
        object.__setattr__(self, "expires_at_utc", expires)
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise CoordinatorContractError("attempts must be an integer")
        if self.attempts < 0:
            raise CoordinatorContractError("attempts must be non-negative")
        if self.attention_trigger_id is not None:
            token = str(self.attention_trigger_id).strip()
            if not token:
                raise CoordinatorContractError(
                    "attention_trigger_id must be non-empty or None"
                )
            object.__setattr__(self, "attention_trigger_id", token)
        expected_identity = evaluation_identity(
            instrument_version_id=self.instrument_version_id,
            evaluation_cutoff=cutoff,
            policy_version=self.policy_version,
        )
        if self.evaluation_identity != expected_identity:
            raise CoordinatorContractError(
                "evaluation_identity does not match its instrument, cutoff and "
                "policy; build requests with EvaluationRequest.create()"
            )


    @classmethod
    def create(
        cls,
        *,
        path: EvaluationPath,
        priority: WorkPriority,
        instrument_version_id: str,
        venue_instrument_id: str,
        evaluation_cutoff: datetime,
        requested_at_utc: datetime,
        expires_at_utc: datetime,
        policy_version: str,
        attention_trigger_id: str | None = None,
        intentional_dedupe_key: str | None = None,
    ) -> "EvaluationRequest":
        """Build a request whose identity is a pure function of its semantics.

        `path`, `priority`, the originating attention trigger and arrival order
        are deliberately *not* part of the evaluation identity: that is what
        makes a stream request and a scheduler request for the same cut-off
        converge on one evaluation.
        """

        identity = evaluation_identity(
            instrument_version_id=instrument_version_id,
            evaluation_cutoff=evaluation_cutoff,
            policy_version=policy_version,
        )
        request_id = stable_hash(
            REQUEST_ID_PREFIX,
            {
                "evaluation_identity": identity,
                "path": EvaluationPath(path).value,
                "priority": WorkPriority(priority).value,
                "requested_at_utc": iso_z(
                    requested_at_utc, field_name="requested_at_utc"
                ),
                "intentional_dedupe_key": (
                    None
                    if intentional_dedupe_key is None
                    else str(intentional_dedupe_key)
                ),
            },
        )
        return cls(
            request_id=request_id,
            schema_version=COORDINATOR_SCHEMA_VERSION,
            evaluation_identity=identity,
            path=path,
            priority=priority,
            instrument_version_id=instrument_version_id,
            venue_instrument_id=venue_instrument_id,
            evaluation_cutoff=evaluation_cutoff,
            requested_at_utc=requested_at_utc,
            expires_at_utc=expires_at_utc,
            policy_version=policy_version,
            attention_trigger_id=attention_trigger_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "EvaluationRequest",
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "evaluation_identity": self.evaluation_identity,
            "path": self.path.value,
            "priority": self.priority.value,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "evaluation_cutoff": iso_z(
                self.evaluation_cutoff, field_name="evaluation_cutoff"
            ),
            "requested_at_utc": iso_z(
                self.requested_at_utc, field_name="requested_at_utc"
            ),
            "expires_at_utc": iso_z(self.expires_at_utc, field_name="expires_at_utc"),
            "policy_version": self.policy_version,
            "attention_trigger_id": self.attention_trigger_id,
            "attempts": self.attempts,
        }


@dataclass(frozen=True)
class SubmissionOutcome:
    """What happened to one submitted request."""

    disposition: RequestDisposition
    evaluation_identity: str
    request_id: str | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, RequestDisposition):
            raise CoordinatorContractError(
                "disposition must be a RequestDisposition"
            )
        if not str(self.reason or "").strip():
            raise CoordinatorContractError("a submission reason is required")


@dataclass(frozen=True)
class CoordinatorTelemetry:
    """Bounded-work telemetry for one coordinator."""

    submitted: int = 0
    admitted: int = 0
    coalesced: int = 0
    dropped_backpressure: int = 0
    preempted: int = 0
    rejected_stale: int = 0
    expired: int = 0
    served: int = 0
    retried: int = 0
    terminal_failures: int = 0
    max_backlog: int = 0
    backlog: int = 0
    by_priority: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    protected_slots_used: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.by_priority, Mapping):
            raise CoordinatorContractError("by_priority must be a mapping")

    def to_dict(self) -> dict[str, Any]:
        return {
            "submitted": self.submitted,
            "admitted": self.admitted,
            "coalesced": self.coalesced,
            "dropped_backpressure": self.dropped_backpressure,
            "preempted": self.preempted,
            "rejected_stale": self.rejected_stale,
            "expired": self.expired,
            "served": self.served,
            "retried": self.retried,
            "terminal_failures": self.terminal_failures,
            "max_backlog": self.max_backlog,
            "backlog": self.backlog,
            "by_priority": dict(self.by_priority),
            "protected_slots_used": self.protected_slots_used,
        }



class UnifiedEvaluationCoordinator:
    """One logical evaluation coordinator for stream and scheduler requests.

    Deterministic and in-process. It holds no clock: every method takes the
    instant it should reason at. It reserves capacity for protection and
    reconciliation, sheds discovery visibly under saturation, expires stale
    requests, bounds retries into terminal failure evidence, and refuses to
    produce a second served transition for one identity.
    """

    def __init__(self, *, limits: CoordinatorLimits) -> None:
        if not isinstance(limits, CoordinatorLimits):
            raise CoordinatorContractError("limits must be CoordinatorLimits")
        self._limits = limits
        self._queued: dict[str, EvaluationRequest] = {}
        self._order: list[str] = []
        self._admitted_at: dict[str, datetime] = {}
        self._served: set[str] = set()
        self._terminal: dict[str, str] = {}
        self._counts: Counter[str] = Counter()
        self._by_priority: Counter[str] = Counter()
        self._served_by_instrument: Counter[str] = Counter()
        self._max_backlog = 0
        self._protected_slots_used = 0

    @property
    def limits(self) -> CoordinatorLimits:
        return self._limits

    def backlog(self) -> int:
        return len(self._queued)

    def _expire(self, *, evaluation_time: datetime) -> int:
        expired = 0
        for request_id in list(self._order):
            request = self._queued.get(request_id)
            if request is None:
                continue
            limit = self._admitted_at.get(request_id, request.requested_at_utc)
            if request.expires_at_utc <= evaluation_time or (
                evaluation_time - limit > self._limits.age_limit()
            ):
                self._queued.pop(request_id, None)
                self._admitted_at.pop(request_id, None)
                self._order.remove(request_id)
                self._counts[RequestDisposition.EXPIRED.value] += 1
                expired += 1
        return expired

    def _shed_discovery(self) -> bool:
        """Shed the newest queued discovery request. Visible, never silent."""

        for request_id in reversed(self._order):
            request = self._queued.get(request_id)
            if request is None:
                continue
            if request.priority in SHEDDABLE_PRIORITIES:
                self._queued.pop(request_id, None)
                self._admitted_at.pop(request_id, None)
                self._order.remove(request_id)
                self._counts[RequestDisposition.DROPPED_BACKPRESSURE.value] += 1
                return True
        return False


    def submit(
        self, request: EvaluationRequest, *, evaluation_time: datetime
    ) -> SubmissionOutcome:
        """Submit one request, deduplicating and applying admission control."""

        if not isinstance(request, EvaluationRequest):
            raise CoordinatorContractError("request must be an EvaluationRequest")
        instant = require_utc(evaluation_time, field_name="evaluation_time")
        if instant < request.requested_at_utc:
            raise CoordinatorContractError(
                "a request cannot be submitted before it was created"
            )
        self._counts["SUBMITTED"] += 1
        identity = request.evaluation_identity
        if identity in self._served:
            self._counts[RequestDisposition.COALESCED.value] += 1
            return SubmissionOutcome(
                disposition=RequestDisposition.COALESCED,
                evaluation_identity=identity,
                request_id=request.request_id,
                reason="this identity was already evaluated",
            )
        if identity in self._terminal:
            self._counts[RequestDisposition.COALESCED.value] += 1
            return SubmissionOutcome(
                disposition=RequestDisposition.COALESCED,
                evaluation_identity=identity,
                request_id=request.request_id,
                reason=f"identity reached a terminal state: {self._terminal[identity]}",
            )
        for request_id in self._order:
            queued = self._queued.get(request_id)
            if queued is not None and queued.evaluation_identity == identity:
                self._counts[RequestDisposition.COALESCED.value] += 1
                if PRIORITY_RANK[request.priority] < PRIORITY_RANK[queued.priority]:
                    self._queued[request_id] = replace(
                        queued, priority=request.priority, path=request.path
                    )
                return SubmissionOutcome(
                    disposition=RequestDisposition.COALESCED,
                    evaluation_identity=identity,
                    request_id=request_id,
                    reason="duplicate delivery converged on one queued evaluation",
                )
        if request.expires_at_utc <= instant:
            self._counts[RequestDisposition.REJECTED_STALE.value] += 1
            return SubmissionOutcome(
                disposition=RequestDisposition.REJECTED_STALE,
                evaluation_identity=identity,
                request_id=request.request_id,
                reason="the request expired before it could be queued",
            )
        if request.priority is WorkPriority.OPTIONAL_AI and (
            not self._limits.optional_ai_admitted
        ):
            self._counts[RequestDisposition.DROPPED_BACKPRESSURE.value] += 1
            return SubmissionOutcome(
                disposition=RequestDisposition.DROPPED_BACKPRESSURE,
                evaluation_identity=identity,
                request_id=request.request_id,
                reason="optional AI work is the last priority and is not admitted",
            )
        if len(self._queued) >= self._limits.queue_capacity:
            if request.priority in SHEDDABLE_PRIORITIES:
                self._counts[RequestDisposition.DROPPED_BACKPRESSURE.value] += 1
                return SubmissionOutcome(
                    disposition=RequestDisposition.DROPPED_BACKPRESSURE,
                    evaluation_identity=identity,
                    request_id=request.request_id,
                    reason="queue capacity is saturated; discovery is shed visibly",
                )
            if not self._shed_discovery():
                self._counts[RequestDisposition.DROPPED_BACKPRESSURE.value] += 1
                return SubmissionOutcome(
                    disposition=RequestDisposition.DROPPED_BACKPRESSURE,
                    evaluation_identity=identity,
                    request_id=request.request_id,
                    reason=(
                        "queue capacity is saturated and no shedable work exists; "
                        "the request fails closed rather than evicting protection"
                    ),
                )
            self._counts["PREEMPTED"] += 1
        self._queued[request.request_id] = request
        self._order.append(request.request_id)
        self._admitted_at[request.request_id] = instant
        self._counts[RequestDisposition.ADMITTED.value] += 1
        self._by_priority[request.priority.value] += 1
        self._max_backlog = max(self._max_backlog, len(self._queued))
        return SubmissionOutcome(
            disposition=RequestDisposition.ADMITTED,
            evaluation_identity=identity,
            request_id=request.request_id,
            reason="admitted",
        )


    def next_batch(
        self, *, evaluation_time: datetime, size: int | None = None
    ) -> tuple[EvaluationRequest, ...]:
        """Return a bounded, priority-ordered, instrument-fair batch.

        Reserved protection and reconciliation slots are served first and are
        never starved by discovery. Stale requests expire here. Discovery work
        left behind stays queued.
        """

        instant = require_utc(evaluation_time, field_name="evaluation_time")
        self._expire(evaluation_time=instant)
        if size is None:
            size = self._limits.queue_capacity
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise CoordinatorContractError("size must be a positive integer")
        candidates = [
            self._queued[request_id]
            for request_id in self._order
            if request_id in self._queued
        ]
        ordered = sorted(
            candidates,
            key=lambda item: (
                PRIORITY_RANK[item.priority],
                item.requested_at_utc,
                item.instrument_version_id,
                item.request_id,
            ),
        )
        reserved_total = (
            self._limits.protection_reserved_slots
            + self._limits.reconciliation_reserved_slots
        )
        selected: list[EvaluationRequest] = []
        per_instrument: Counter[str] = Counter()
        reserved_used = 0
        for request in ordered:
            if len(selected) >= size:
                break
            if request in selected:
                continue
            if request.priority in RESERVED_PRIORITIES and reserved_used >= reserved_total:
                continue
            if per_instrument[request.instrument_version_id] >= (
                self._limits.per_instrument_budget_per_cycle
            ):
                continue
            selected.append(request)
            per_instrument[request.instrument_version_id] += 1
            if request.priority in RESERVED_PRIORITIES:
                reserved_used += 1
        self._protected_slots_used = max(self._protected_slots_used, reserved_used)
        return tuple(selected)

    def complete(
        self, evaluation_identity_value: str, *, evaluation_time: datetime
    ) -> bool:
        """Mark one identity served. A repeat returns False and changes nothing.

        This is the structural guarantee that one identity cannot produce a
        second detector transition through a retry, a duplicate delivery or a
        restart.
        """

        require_utc(evaluation_time, field_name="evaluation_time")
        token = str(evaluation_identity_value or "").strip()
        if not token:
            raise CoordinatorContractError("evaluation_identity is required")
        if token in self._served:
            return False
        request_id = next(
            (
                candidate
                for candidate, request in self._queued.items()
                if request.evaluation_identity == token
            ),
            None,
        )
        if request_id is None:
            # A committed transition may be acknowledged on restart before the
            # queue is rebuilt; that is still exactly one transition.
            self._served.add(token)
            self._counts[RequestDisposition.SERVED.value] += 1
            return True
        request = self._queued.pop(request_id)
        self._admitted_at.pop(request_id, None)
        if request_id in self._order:
            self._order.remove(request_id)
        self._served.add(token)
        self._counts[RequestDisposition.SERVED.value] += 1
        self._served_by_instrument[request.instrument_version_id] += 1
        return True


    def fail(
        self, evaluation_identity_value: str, *, reason: str, evaluation_time: datetime
    ) -> RequestDisposition:
        """Record a failed attempt: bounded retry, then terminal evidence."""

        instant = require_utc(evaluation_time, field_name="evaluation_time")
        token = str(evaluation_identity_value or "").strip()
        if not token:
            raise CoordinatorContractError("evaluation_identity is required")
        detail = str(reason or "").strip()
        if not detail:
            raise CoordinatorContractError("a failure reason is required")
        request_id = next(
            (
                candidate
                for candidate, request in self._queued.items()
                if request.evaluation_identity == token
            ),
            None,
        )
        if request_id is None:
            self._terminal[token] = detail
            self._counts[RequestDisposition.TERMINAL_FAILURE.value] += 1
            return RequestDisposition.TERMINAL_FAILURE
        request = self._queued[request_id]
        attempts = request.attempts + 1
        if attempts >= self._limits.retry_ceiling:
            self._queued.pop(request_id, None)
            self._admitted_at.pop(request_id, None)
            if request_id in self._order:
                self._order.remove(request_id)
            self._terminal[token] = detail
            self._counts[RequestDisposition.TERMINAL_FAILURE.value] += 1
            return RequestDisposition.TERMINAL_FAILURE
        self._queued[request_id] = replace(
            request,
            attempts=attempts,
            requested_at_utc=instant,
            expires_at_utc=max(
                request.expires_at_utc, instant + self._limits.age_limit()
            ),
        )
        self._admitted_at[request_id] = instant
        self._counts[RequestDisposition.RETRY_SCHEDULED.value] += 1
        return RequestDisposition.RETRY_SCHEDULED

    def export_state(self) -> dict[str, Any]:
        """Deterministic export used by restart-idempotency proofs."""

        return {
            "schema_version": COORDINATOR_SCHEMA_VERSION,
            "queued": [
                self._queued[request_id].to_dict()
                for request_id in self._order
                if request_id in self._queued
            ],
            "served": sorted(self._served),
            "terminal": dict(sorted(self._terminal.items())),
        }

    def telemetry(self) -> CoordinatorTelemetry:
        counts = self._counts
        return CoordinatorTelemetry(
            submitted=counts["SUBMITTED"],
            admitted=counts[RequestDisposition.ADMITTED.value],
            coalesced=counts[RequestDisposition.COALESCED.value],
            dropped_backpressure=counts[RequestDisposition.DROPPED_BACKPRESSURE.value],
            preempted=counts["PREEMPTED"],
            rejected_stale=counts[RequestDisposition.REJECTED_STALE.value],
            expired=counts[RequestDisposition.EXPIRED.value],
            served=counts[RequestDisposition.SERVED.value],
            retried=counts[RequestDisposition.RETRY_SCHEDULED.value],
            terminal_failures=counts[RequestDisposition.TERMINAL_FAILURE.value],
            max_backlog=self._max_backlog,
            backlog=len(self._queued),
            by_priority=MappingProxyType(dict(self._by_priority)),
            protected_slots_used=self._protected_slots_used,
        )

    def served_identities(self) -> tuple[str, ...]:
        return tuple(sorted(self._served))


__all__ = [
    "COORDINATOR_SCHEMA_VERSION",
    "CoordinatorContractError",
    "CoordinatorLimits",
    "CoordinatorTelemetry",
    "EvaluationPath",
    "EvaluationRequest",
    "IDENTITY_PREFIX",
    "OWNER_INPUT_STATUS",
    "PRIORITY_RANK",
    "REQUEST_ID_PREFIX",
    "RESERVED_PRIORITIES",
    "RequestDisposition",
    "SHEDDABLE_PRIORITIES",
    "SubmissionOutcome",
    "UnifiedEvaluationCoordinator",
    "WorkPriority",
    "evaluation_identity",
]

