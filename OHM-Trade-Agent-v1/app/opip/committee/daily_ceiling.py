"""UTC daily committee spending ceiling.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

A per-case ceiling bounds one candidate; it does not bound a day. This module adds
the second bound: a declared maximum spend per UTC calendar day, tracked durably so
a restart cannot reset the day's spend and a busy day cannot quietly exceed the
ceiling.

Three rules:

* **The day boundary is UTC**, not local time, so the ceiling cannot be stretched by
  a timezone or by daylight saving.

* **An unknown reservation is not free.** A request whose cost cannot be bounded is
  refused rather than admitted under an assumption, because a ceiling that cannot be
  evaluated cannot be enforced.

* **Spend is recorded before it is relied on.** The reservation is written durably
  before the work it authorises, so a crash cannot lose spend that already happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Protocol

from app.opip.decision_intelligence.serialization import require_utc, stable_hash

DAILY_SPEND_SCHEMA_VERSION = 1
DAILY_SPEND_IDENTITY_DOMAIN = "COMMITTEE-DAILY-SPEND"

#: Microunits are 1e-6 of a currency unit, so one unit is 1_000_000 microunits.
MICROUNITS_PER_UNIT = 1_000_000


class DailyCeilingExceededError(ValueError):
    """The UTC daily ceiling would be exceeded by the requested reservation."""


class UnboundedReservationError(ValueError):
    """A reservation whose cost cannot be bounded was offered to the ceiling."""


def utc_day_of(moment: datetime) -> date:
    """The UTC calendar day containing ``moment``."""
    return require_utc(moment, field_name="moment").date()


@dataclass(frozen=True)
class DailySpendRecord:
    """Durable spend for one UTC day."""

    day: date
    spent_microunits: int
    reservations: int = 0
    schema_version: int = DAILY_SPEND_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DAILY_SPEND_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported DailySpendRecord schema_version")
        if not isinstance(self.day, date):
            raise ValueError("day must be a date")
        if type(self.spent_microunits) is not int or self.spent_microunits < 0:
            raise ValueError("spent_microunits must be a non-negative integer")
        if type(self.reservations) is not int or self.reservations < 0:
            raise ValueError("reservations must be a non-negative integer")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "day": self.day.isoformat(),
            "spent_microunits": self.spent_microunits,
            "reservations": self.reservations,
        }

    @property
    def record_id(self) -> str:
        return stable_hash(DAILY_SPEND_IDENTITY_DOMAIN, self.identity_payload())


class DailySpendStore(Protocol):
    """Durable home of the per-day spend. Injected so it can be tested directly."""

    def load_day(self, day: date) -> DailySpendRecord | None:
        """Return the record for a UTC day, or ``None`` when nothing is recorded."""

    def save_day(self, record: DailySpendRecord) -> None:
        """Persist the day's record durably before the work it authorises."""


@dataclass
class InMemoryDailySpendStore:
    """A store for tests and offline runs. Not durable across processes."""

    days: dict[date, DailySpendRecord] = field(default_factory=dict)
    saves: int = 0

    def load_day(self, day: date) -> DailySpendRecord | None:
        return self.days.get(day)

    def save_day(self, record: DailySpendRecord) -> None:
        self.saves += 1
        self.days[record.day] = record


class DailyCeiling:
    """Enforces a declared maximum spend per UTC calendar day."""

    def __init__(
        self,
        *,
        max_daily_microunits: int,
        store: DailySpendStore,
        now: Callable[[], datetime],
    ) -> None:
        if type(max_daily_microunits) is not int or max_daily_microunits < 0:
            raise ValueError("max_daily_microunits must be a non-negative integer")
        self._max = max_daily_microunits
        self._store = store
        self._now = now

    @property
    def max_daily_microunits(self) -> int:
        return self._max

    def record_for_today(self) -> DailySpendRecord:
        """The current UTC day's record, created empty when absent."""
        return self._store.load_day(utc_day_of(self._now())) or DailySpendRecord(
            day=utc_day_of(self._now()), spent_microunits=0
        )

    def remaining_today(self) -> int:
        """What is left of today's ceiling. Never negative."""
        return max(0, self._max - self.record_for_today().spent_microunits)

    def admit(self, *, estimated_cost_microunits: int | None, at: datetime) -> DailySpendRecord:
        """Reserve a bounded cost against today's ceiling, or fail closed.

        The reservation is persisted before returning, so the work it authorises
        cannot outlive the record of its cost.
        """
        if estimated_cost_microunits is None:
            # An unbounded cost could exceed any ceiling, and admitting it would
            # make the ceiling decorative.
            raise UnboundedReservationError(
                "a reservation whose cost cannot be bounded is refused rather than "
                "admitted under an assumption"
            )
        if type(estimated_cost_microunits) is not int or estimated_cost_microunits < 0:
            raise UnboundedReservationError(
                "estimated_cost_microunits must be a non-negative integer"
            )
        day = utc_day_of(at)
        current = self._store.load_day(day) or DailySpendRecord(
            day=day, spent_microunits=0
        )
        projected = current.spent_microunits + estimated_cost_microunits
        if projected > self._max:
            raise DailyCeilingExceededError(
                f"the UTC daily ceiling would be exceeded: {projected} > {self._max} "
                f"microunits for {day.isoformat()}"
            )
        updated = DailySpendRecord(
            day=day,
            spent_microunits=projected,
            reservations=current.reservations + 1,
        )
        self._store.save_day(updated)
        return updated

    def settle(
        self, *, reserved_microunits: int, reported_microunits: int | None, at: datetime
    ) -> DailySpendRecord:
        """Replace a reservation with what was actually spent.

        Charges the greater of the reservation and the reported cost, so an
        under-estimate cannot escape the ceiling. A failure with unknown cost keeps
        its reservation, because nothing better is known.
        """
        day = utc_day_of(at)
        current = self._store.load_day(day) or DailySpendRecord(
            day=day, spent_microunits=0
        )
        charged = reserved_microunits
        if reported_microunits is not None:
            charged = max(reserved_microunits, reported_microunits)
        corrected = max(0, current.spent_microunits - reserved_microunits) + charged
        updated = DailySpendRecord(
            day=day,
            spent_microunits=corrected,
            reservations=current.reservations,
        )
        self._store.save_day(updated)
        return updated


__all__ = [
    "DAILY_SPEND_IDENTITY_DOMAIN",
    "DAILY_SPEND_SCHEMA_VERSION",
    "MICROUNITS_PER_UNIT",
    "DailyCeiling",
    "DailyCeilingExceededError",
    "DailySpendRecord",
    "DailySpendStore",
    "InMemoryDailySpendStore",
    "UnboundedReservationError",
    "utc_day_of",
]
