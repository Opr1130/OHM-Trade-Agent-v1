"""Idempotency ledger for committee observations.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Committee execution has a deterministic logical identity per seat. The ledger
answers three questions for the runtime:

* has this exact logical observation already been committed? If it has, a retry
  must not call a provider again and must not create a second, independent
  opinion;
* how much has this case already spent? So a redelivery cannot spend the whole
  declared ceiling again;
* which canonical decision was this case attributed to? So a reused case cannot
  be reattributed to a different decision.

Only a validated opinion is a committed observation. A recorded failure is not a
vote and never suppresses a later legitimate attempt.
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

    def committed_opinion(
        self, logical_observation_id: str
    ) -> ProviderCallOutcome | None:
        """Return the committed observation for this logical seat, if any."""

    def attempt_count(self, logical_observation_id: str) -> int:
        """Return how many attempts this logical seat has already recorded."""

    def case_spend_microunits(self, case_id: str) -> int:
        """Known spend already recorded for a case.

        Each attempt contributes its explicitly recorded charge, falling back to
        its reported cost for evidence written before charges were recorded, so
        legacy and current evidence are combined rather than choosing between
        them.
        """

    def recorded_case_binding(self, case_id: str) -> tuple[bool, object | None]:
        """Return ``(was_recorded, binding)`` for a case.

        The flag distinguishes a case that was never run from one that was run
        without a binding, so a caller cannot mistake "no binding yet" for "no
        case outcome yet".
        """

    def note_case_binding(self, case_id: str, binding: object | None) -> None:
        """Remember the canonical binding this case was run with."""

    def record(
        self,
        outcome: ProviderCallOutcome,
        *,
        charge_microunits: int | None = None,
    ) -> None:
        """Record one attempt outcome, with its charge where known."""


class InMemoryObservationLedger:
    """A ledger for tests and offline runs. Not durable across processes."""

    def __init__(self, *, prior: Iterator[ProviderCallOutcome] | None = None) -> None:
        self._committed: dict[str, ProviderCallOutcome] = {}
        self._attempts: dict[str, int] = {}
        self._charges: dict[str, int] = {}
        self._by_case: dict[str, list[ProviderCallOutcome]] = {}
        self._case_bindings: dict[str, object | None] = {}
        if prior is not None:
            for outcome in prior:
                self.record(outcome)

    def committed_opinion(
        self, logical_observation_id: str
    ) -> ProviderCallOutcome | None:
        return self._committed.get(logical_observation_id)

    def attempt_count(self, logical_observation_id: str) -> int:
        return self._attempts.get(logical_observation_id, 0)

    def case_spend_microunits(self, case_id: str) -> int:
        return sum(
            self._charges.get(row.outcome_id, row.estimated_microunits_reported())
            for row in self._by_case.get(case_id, [])
        )

    def recorded_case_binding(self, case_id: str) -> tuple[bool, object | None]:
        if case_id in self._case_bindings:
            return True, self._case_bindings[case_id]
        return bool(self._by_case.get(case_id)), None

    def note_case_binding(self, case_id: str, binding: object | None) -> None:
        self._case_bindings.setdefault(case_id, binding)

    def record(
        self,
        outcome: ProviderCallOutcome,
        *,
        charge_microunits: int | None = None,
    ) -> None:
        if charge_microunits is not None and charge_microunits < 0:
            raise ValueError("a case charge cannot be negative")
        key = outcome.logical_observation_id
        self._attempts[key] = self._attempts.get(key, 0) + 1
        if charge_microunits is not None:
            self._charges[outcome.outcome_id] = charge_microunits
        self._by_case.setdefault(outcome.case_id, []).append(outcome)
        if outcome.status in COMMITTED_STATUSES:
            # First committed observation wins; history is never rewritten.
            self._committed.setdefault(key, outcome)


__all__ = [
    "COMMITTED_STATUSES",
    "InMemoryObservationLedger",
    "ObservationLedger",
]
