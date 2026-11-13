"""Pure O'Pip retention policy table (v6 retention policy slice).

The table says, per data dataset, how many days of data stay hot and the
smallest hot window that dataset is ever allowed to use. It is the policy half
of the v6 retention decision ("hot data window is 14 days; older data ships to
the data box and is deleted only after hash verification").

This module is pure: it opens no file or directory, opens no database, reads no
clock, environment or network, and it is not a configuration registry. It
defines the table, validates it and answers which row a dataset uses. It never
prunes, deletes, moves, renames, compresses or writes anything, and it has no
trading, exchange, Telegram or learning authority.

:func:`validate_retention_policy` fails closed. A row that names a blank or
duplicated dataset, a non-positive window or floor, or a hot window below the
dataset's minimum floor is rejected with :class:`RetentionPolicyError` instead
of being silently corrected or clamped. The default table is validated at import
time, so an invalid default cannot ship.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

#: The owner's v6 hot data window, in days.
DEFAULT_HOT_WINDOW_DAYS = 14
#: Smallest floor the policy grants a dataset whose floor is not stated.
DEFAULT_MIN_FLOOR_DAYS = 7
#: Dataset name the dry-run report uses for files that sit directly in the root.
ROOT_DATASET = "(root)"


class RetentionPolicyError(ValueError):
    """A retention policy row is unusable and must not be applied."""


@dataclass(frozen=True)
class RetentionPolicyEntry:
    """One retention rule: a dataset, its hot window and its minimum floor."""

    dataset: str
    hot_window_days: int = DEFAULT_HOT_WINDOW_DAYS
    min_floor_days: int = DEFAULT_MIN_FLOOR_DAYS

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "hot_window_days": self.hot_window_days,
            "min_floor_days": self.min_floor_days,
        }


def _require_days(value: object, *, field: str, dataset: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RetentionPolicyError(
            f"{dataset}: {field} must be a whole number of days, got {value!r}"
        )
    if value < 1:
        raise RetentionPolicyError(
            f"{dataset}: {field} must be at least 1 day, got {value}"
        )
    return value


def validate_retention_policy(
    entries: Iterable[RetentionPolicyEntry],
) -> tuple[RetentionPolicyEntry, ...]:
    """Return the table unchanged, or raise if any row is unusable.

    The check is deterministic and total: every row is inspected before the
    table is accepted. A hot window below its dataset's minimum floor is the
    load-bearing rejection, because applying it would drop evidence earlier
    than the policy allows.
    """

    validated: list[RetentionPolicyEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, RetentionPolicyEntry):
            raise RetentionPolicyError(
                "retention policy rows must be RetentionPolicyEntry values, "
                f"got {type(entry).__name__}"
            )
        dataset = entry.dataset
        if not isinstance(dataset, str) or not dataset:
            raise RetentionPolicyError(
                "retention policy dataset must be a non-empty string"
            )
        if dataset != dataset.strip():
            raise RetentionPolicyError(
                f"retention policy dataset {dataset!r} must not carry surrounding whitespace"
            )
        if dataset in seen:
            raise RetentionPolicyError(
                f"retention policy dataset {dataset!r} is listed more than once"
            )
        seen.add(dataset)
        hot_window_days = _require_days(
            entry.hot_window_days, field="hot_window_days", dataset=dataset
        )
        min_floor_days = _require_days(
            entry.min_floor_days, field="min_floor_days", dataset=dataset
        )
        if hot_window_days < min_floor_days:
            raise RetentionPolicyError(
                f"{dataset}: hot window of {hot_window_days} day(s) is below its "
                f"minimum floor of {min_floor_days} day(s)"
            )
        validated.append(entry)
    return tuple(validated)


#: The default v6 table. Canonical evidence keeps the full 14-day hot window as
#: its floor because it is durable evidence; the other datasets use the 7-day
#: floor the existing analytics, inbox and cold-event settings already use.
DEFAULT_RETENTION_POLICY: tuple[RetentionPolicyEntry, ...] = validate_retention_policy(
    (
        RetentionPolicyEntry(
            dataset="canonical",
            hot_window_days=DEFAULT_HOT_WINDOW_DAYS,
            min_floor_days=DEFAULT_HOT_WINDOW_DAYS,
        ),
        RetentionPolicyEntry(
            dataset="events",
            hot_window_days=DEFAULT_HOT_WINDOW_DAYS,
            min_floor_days=DEFAULT_MIN_FLOOR_DAYS,
        ),
        RetentionPolicyEntry(
            dataset=ROOT_DATASET,
            hot_window_days=DEFAULT_HOT_WINDOW_DAYS,
            min_floor_days=DEFAULT_MIN_FLOOR_DAYS,
        ),
    )
)


def entry_for_dataset(
    dataset: str,
    policy: Iterable[RetentionPolicyEntry] = DEFAULT_RETENTION_POLICY,
) -> RetentionPolicyEntry:
    """Return the row for ``dataset``, or the unlisted-dataset default row.

    An unlisted dataset is never dropped from the report and never inherits a
    laxer window: it gets the 14-day hot window and the 7-day floor.
    """

    for entry in policy:
        if entry.dataset == dataset:
            return entry
    return RetentionPolicyEntry(
        dataset=dataset,
        hot_window_days=DEFAULT_HOT_WINDOW_DAYS,
        min_floor_days=DEFAULT_MIN_FLOOR_DAYS,
    )
