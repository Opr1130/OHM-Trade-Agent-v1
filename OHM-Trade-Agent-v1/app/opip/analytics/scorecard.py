"""Pure hit-rate Scorecard core (v6 slice E).

The Scorecard answers one question: for each strategy version, how often did a
scored outcome hit? This module is the pure core of that answer. It reads the
records it is handed, groups them by the version stamp already written on them
(v6 slices A and D) and returns the hit rate and count per strategy version.

A *scored outcome record* is any mapping that carries:

- ``strategy_version``: the version stamp written when the outcome was produced.
  An absent, ``None`` or blank stamp is grouped under :data:`UNVERSIONED`, so
  evidence recorded before versioning existed is still counted and never
  silently dropped or attributed to the current version.
- ``hit``: the explicit boolean outcome of that record. A record without a
  boolean outcome is refused instead of being guessed at, because a guessed hit
  would manufacture a rate the evidence does not support.

The function is pure: it opens no file, reads no clock, imports no runtime
service, never mutates its input and returns a new mapping. It grants no
trading, exchange, ranking, alert or admission authority. Mapping a concrete
evidence stream (screening evaluations, discovery labels, paper outcomes) into
this record shape is a separate adapter concern, not this slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: Grouping key for records that carry no strategy version stamp.
UNVERSIONED = "UNVERSIONED"

HIT_FIELD = "hit"
VERSION_FIELD = "strategy_version"


@dataclass(frozen=True)
class VersionScore:
    """Hit rate and count for one strategy version."""

    strategy_version: str
    hits: int
    count: int

    @property
    def misses(self) -> int:
        return self.count - self.hits

    @property
    def hit_rate(self) -> float:
        """Share of scored records that hit, from ``0.0`` to ``1.0``."""
        return self.hits / self.count

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_version": self.strategy_version,
            "hits": self.hits,
            "misses": self.misses,
            "count": self.count,
            "hit_rate": self.hit_rate,
        }


def version_of(record: Mapping[str, Any]) -> str:
    """Return the record's strategy version, or :data:`UNVERSIONED`.

    A stamp that was never written, or was written empty, is unversioned
    evidence. It is not back-filled with the live strategy version, because
    that would attribute an old outcome to a strategy that did not produce it.
    """
    value = record.get(VERSION_FIELD)
    if value is None:
        return UNVERSIONED
    if not isinstance(value, str):
        raise TypeError(
            f"{VERSION_FIELD} must be a string or None, got {type(value).__name__}"
        )
    return value.strip() or UNVERSIONED


def hit_of(record: Mapping[str, Any], *, index: int | None = None) -> bool:
    """Return the record's explicit boolean hit outcome.

    A missing or non-boolean outcome is refused rather than counted as a miss,
    so an unscorable record can never quietly move a hit rate.
    """
    where = f"record {index}" if index is not None else "record"
    if HIT_FIELD not in record:
        raise ValueError(f"{where} has no {HIT_FIELD!r} outcome")
    value = record[HIT_FIELD]
    if not isinstance(value, bool):
        raise ValueError(
            f"{where} has a non-boolean {HIT_FIELD!r} outcome: {value!r}"
        )
    return value


def score_outcomes(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, VersionScore]:
    """Return ``{strategy_version: VersionScore}`` for the given outcome records.

    Records without a stamp are grouped under :data:`UNVERSIONED` (AC-002).
    Every other record is grouped by the exact stamp it carries, so two
    strategies never share a bucket and no record is attributed to a version it
    did not record (AC-001). The returned mapping is ordered by version key for
    deterministic output.
    """
    if isinstance(records, (str, bytes, bytearray, Mapping)):
        raise TypeError("outcome records must be an iterable of mappings")

    tally: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise TypeError(
                f"record {index} must be a mapping, got {type(record).__name__}"
            )
        version = version_of(record)
        hit = hit_of(record, index=index)
        counts = tally.setdefault(version, [0, 0])
        counts[0] += 1
        if hit:
            counts[1] += 1

    return {
        version: VersionScore(
            strategy_version=version,
            hits=hits,
            count=count,
        )
        for version, (count, hits) in sorted(tally.items())
    }
