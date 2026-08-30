"""Provider-specific sequencing abstraction.

Different venues encode "no gap happened" differently:

* Some streams (e.g. a strict per-symbol update counter) are meant to
  increment by exactly one; any larger jump is a genuine gap.
* Some streams intentionally emit non-contiguous, non-decreasing IDs (gaps
  between values are normal and carry no meaning); only a decrease or an
  exact repeat is informative.
* Some streams carry no usable sequence value at all.

A single global rule ("current != previous + 1 => gap") is unsafe across
these. Each policy is implemented as its own small SequenceTracker so a
provider-specific quirk can never leak into the generic envelope/window/
quality logic.

Reconnect epochs are handled uniformly by the base class: an epoch bump never
produces GAP or OUT_OF_ORDER by itself (a reconnect is not, by itself,
evidence a message was lost) — it produces RESET_NEW_EPOCH and clears
per-epoch memory, so history never silently crosses an invalid connection
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.opip.streaming.contract import SequenceStatus


@dataclass(frozen=True)
class SequenceObservation:
    status: SequenceStatus
    sequence_value: str | None
    previous_sequence_value: str | None
    reconnect_epoch: int
    epoch_changed: bool
    gap_size: int | None = None

    def __post_init__(self) -> None:
        if int(self.reconnect_epoch) < 0:
            raise ValueError("reconnect_epoch cannot be negative")
        if self.gap_size is not None and self.gap_size <= 0:
            raise ValueError("gap_size must be positive when present")


class SequenceTracker(Protocol):
    """One tracker instance owns the sequence memory for one (venue, stream,
    symbol) key. Bounded state: a handful of scalars, never an unbounded
    history."""

    def observe(
        self, sequence_value: str | None, *, reconnect_epoch: int
    ) -> SequenceObservation:
        ...

    def reset(self) -> None:
        """Explicitly discard tracker memory (e.g. on a caller-detected
        discontinuity that isn't a reconnect)."""
        ...


class _EpochAwareTracker:
    """Shared reconnect-epoch bookkeeping for the concrete trackers below."""

    def __init__(self) -> None:
        self._known_epoch: int | None = None
        self._has_seen: bool = False

    def reset(self) -> None:
        self._has_seen = False

    def _epoch_transition(self, reconnect_epoch: int) -> bool:
        if int(reconnect_epoch) < 0:
            raise ValueError("reconnect_epoch cannot be negative")
        changed = self._known_epoch is not None and reconnect_epoch != self._known_epoch
        if self._known_epoch is None or changed:
            self._known_epoch = reconnect_epoch
        if changed:
            self._has_seen = False
        return changed


def _try_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class StrictIncrementingSequenceTracker(_EpochAwareTracker):
    """For streams whose sequence values are meant to increment by exactly 1.

    A jump of more than one is a GAP of that size. A repeat of the last value
    is a DUPLICATE. Any smaller/non-numeric/malformed value is OUT_OF_ORDER
    (informative but not classified as a specific gap size) rather than
    silently ignored.
    """

    def __init__(self) -> None:
        super().__init__()
        self._last: int | None = None

    def reset(self) -> None:
        super().reset()
        self._last = None

    def observe(
        self, sequence_value: str | None, *, reconnect_epoch: int
    ) -> SequenceObservation:
        epoch_changed = self._epoch_transition(reconnect_epoch)
        if epoch_changed:
            self._last = None

        parsed = _try_int(sequence_value)
        if parsed is None:
            return SequenceObservation(
                status=SequenceStatus.UNSUPPORTED,
                sequence_value=sequence_value,
                previous_sequence_value=(
                    str(self._last) if self._last is not None else None
                ),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=epoch_changed,
            )

        previous = self._last
        self._last = parsed
        self._has_seen = True

        if epoch_changed or previous is None:
            return SequenceObservation(
                status=(
                    SequenceStatus.RESET_NEW_EPOCH if epoch_changed
                    else SequenceStatus.FIRST
                ),
                sequence_value=str(parsed),
                previous_sequence_value=(
                    str(previous) if previous is not None else None
                ),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=epoch_changed,
            )

        if parsed == previous:
            return SequenceObservation(
                status=SequenceStatus.DUPLICATE,
                sequence_value=str(parsed),
                previous_sequence_value=str(previous),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=False,
            )
        if parsed == previous + 1:
            return SequenceObservation(
                status=SequenceStatus.CONTIGUOUS,
                sequence_value=str(parsed),
                previous_sequence_value=str(previous),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=False,
            )
        if parsed > previous + 1:
            return SequenceObservation(
                status=SequenceStatus.GAP,
                sequence_value=str(parsed),
                previous_sequence_value=str(previous),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=False,
                gap_size=parsed - previous - 1,
            )
        return SequenceObservation(
            status=SequenceStatus.OUT_OF_ORDER,
            sequence_value=str(parsed),
            previous_sequence_value=str(previous),
            reconnect_epoch=reconnect_epoch,
            epoch_changed=False,
        )


class NonDecreasingSequenceTracker(_EpochAwareTracker):
    """For streams where non-contiguous IDs are normal and carry no meaning.

    Only a repeat (DUPLICATE) or a decrease (OUT_OF_ORDER) is informative;
    any increase, regardless of size, is CONTIGUOUS. This tracker never
    reports GAP, because "gap size" has no meaning for this policy.
    """

    def __init__(self) -> None:
        super().__init__()
        self._last: int | None = None

    def reset(self) -> None:
        super().reset()
        self._last = None

    def observe(
        self, sequence_value: str | None, *, reconnect_epoch: int
    ) -> SequenceObservation:
        epoch_changed = self._epoch_transition(reconnect_epoch)
        if epoch_changed:
            self._last = None

        parsed = _try_int(sequence_value)
        if parsed is None:
            return SequenceObservation(
                status=SequenceStatus.UNSUPPORTED,
                sequence_value=sequence_value,
                previous_sequence_value=(
                    str(self._last) if self._last is not None else None
                ),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=epoch_changed,
            )

        previous = self._last
        self._last = parsed
        self._has_seen = True

        if epoch_changed or previous is None:
            return SequenceObservation(
                status=(
                    SequenceStatus.RESET_NEW_EPOCH if epoch_changed
                    else SequenceStatus.FIRST
                ),
                sequence_value=str(parsed),
                previous_sequence_value=(
                    str(previous) if previous is not None else None
                ),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=epoch_changed,
            )

        if parsed == previous:
            return SequenceObservation(
                status=SequenceStatus.DUPLICATE,
                sequence_value=str(parsed),
                previous_sequence_value=str(previous),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=False,
            )
        if parsed > previous:
            return SequenceObservation(
                status=SequenceStatus.CONTIGUOUS,
                sequence_value=str(parsed),
                previous_sequence_value=str(previous),
                reconnect_epoch=reconnect_epoch,
                epoch_changed=False,
            )
        return SequenceObservation(
            status=SequenceStatus.OUT_OF_ORDER,
            sequence_value=str(parsed),
            previous_sequence_value=str(previous),
            reconnect_epoch=reconnect_epoch,
            epoch_changed=False,
        )


class NoSequenceTracker(_EpochAwareTracker):
    """For streams that carry no usable sequence value.

    Always reports UNSUPPORTED. Downstream quality/window logic must treat
    UNSUPPORTED as "sequence continuity cannot be verified", never as
    "contiguous" and never as a gap.
    """

    def observe(
        self, sequence_value: str | None, *, reconnect_epoch: int
    ) -> SequenceObservation:
        epoch_changed = self._epoch_transition(reconnect_epoch)
        return SequenceObservation(
            status=SequenceStatus.UNSUPPORTED,
            sequence_value=sequence_value,
            previous_sequence_value=None,
            reconnect_epoch=reconnect_epoch,
            epoch_changed=epoch_changed,
        )
