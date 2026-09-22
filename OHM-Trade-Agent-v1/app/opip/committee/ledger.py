"""Idempotency ledger for committee observations.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Committee execution has a deterministic logical identity per seat. The ledger
answers one question: has this exact logical observation already been committed?
If it has, a retry must not call a provider again and must not create a second,
independent opinion. If it has not, the runtime may attempt the seat.

Only a validated opinion is a committed observation. A recorded failure is not
a vote and never suppresses a later legitimate attempt.
"""

from __future__ import annotations

from typing import Iterator, Protocol

from app.opip.committee.contracts import (
    ObservationStatus,
    ProviderCallOutcome,
)

#: Statuses that represent a committed, immutable observation of a seat.
COMMITTED_STATUSES = frozenset(
    {ObservationStatus.COMPLETED, ObservationStatus.DUPLICATE_OK}
)


class ObservationLedger(Protocol):
    """Structural contract the committee runtime depends on."""

    def committed_opinion(self, logical_observation_id: str) -> ProviderCallOutcome | None:
        """Return the committed observation for this logical seat, if any."""

    def attempt_count(self, logical_observation_id: str) -> int:
        """Return how many attempts this logical seat has already recorded."""

    def case_spend_microunits(self, case_id: str) -> int:
        """Return the known spend already recorded for a case."""

    def add_case_charge(self, case_id: str, microunits: int) -> None:
        """Persist the charge for one seat's invocations against its case.

        The charge is the runtime's own accounting (per-attempt max of estimate
        and reported cost), which cannot be reconstructed from reported cost
        alone - an attempt whose cost is unknown still consumes its reservation.
        Recording it explicitly is what keeps a redelivery within the ceiling.
        """

    def recorded_case_binding(self, case_id: str) -> object | None:
        """Return the canonical binding already recorded for a case, if any."""

    def note_case_binding(self, case_id: str, binding: object | None) -> None:
        """Remember the canonical binding this case was run with."""
    def record(self, outcome: ProviderCallOutcome) -> None:
        """Record one attempt outcome. Never overwrites a committed opinion."""


class InMemoryObservationLedger:
    """A ledger for tests and offline runs. Not durable across processes."""

    def __init__(self, *, prior: Iterator[ProviderCallOutcome] | None = None) -> None:
        self._committed: dict[str, ProviderCallOutcome] = {}
        self._attempts: dict[str, int] = {}
        self._case_spend: dict[str, int] = {}
        self._case_bindings: dict[str, object | None] = {}
        if prior is not None:
            for outcome in prior:
                self.record(outcome)

    def committed_opinion(self, logical_observation_id: str) -> ProviderCallOutcome | None:
        return self._committed.get(logical_observation_id)

    def attempt_count(self, logical_observation_id: str) -> int:
        return self._attempts.get(logical_observation_id, 0)

    def case_spend_microunits(self, case_id: str) -> int:
        return self._case_spend.get(case_id, 0)

    def add_case_charge(self, case_id: str, microunits: int) -> None:
        if microunits < 0:
            raise ValueError("a case charge cannot be negative")
        self._case_spend[case_id] = self._case_spend.get(case_id, 0) + microunits

    def recorded_case_binding(self, case_id: str) -> object | None:
        return self._case_bindings.get(case_id)

    def note_case_binding(self, case_id: str, binding: object | None) -> None:
        self._case_bindings.setdefault(case_id, binding)

    def record(self, outcome: ProviderCallOutcome) -> None:
        key = outcome.logical_observation_id
        self._attempts[key] = self._attempts.get(key, 0) + 1
        if outcome.status in COMMITTED_STATUSES:
            # First committed observation wins; history is never rewritten.
            self._committed.setdefault(key, outcome)


__all__ = [
    "COMMITTED_STATUSES",
    "InMemoryObservationLedger",
    "ObservationLedger",
]
