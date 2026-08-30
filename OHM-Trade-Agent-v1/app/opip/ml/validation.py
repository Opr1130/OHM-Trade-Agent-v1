"""Chronological purge/embargo primitives for financial time-series validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from app.opip.ml.temporal import require_utc


@dataclass(frozen=True)
class TemporalSample:
    sample_id: str
    decision_at_utc: datetime
    label_interval_end_utc: datetime

    def __post_init__(self) -> None:
        decision = require_utc(self.decision_at_utc, field_name="decision_at_utc")
        label_end = require_utc(
            self.label_interval_end_utc, field_name="label_interval_end_utc"
        )
        if label_end < decision:
            raise ValueError("label interval cannot end before decision")
        object.__setattr__(self, "decision_at_utc", decision)
        object.__setattr__(self, "label_interval_end_utc", label_end)


@dataclass(frozen=True)
class PurgedSplit:
    train_ids: tuple[str, ...]
    validation_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    purged_ids: tuple[str, ...]


def purged_chronological_split(
    samples: Iterable[TemporalSample],
    *,
    train_end_utc: datetime,
    validation_start_utc: datetime,
    validation_end_utc: datetime,
    test_start_utc: datetime,
    test_end_utc: datetime,
    embargo: timedelta,
) -> PurgedSplit:
    train_end = require_utc(train_end_utc, field_name="train_end_utc")
    val_start = require_utc(validation_start_utc, field_name="validation_start_utc")
    val_end = require_utc(validation_end_utc, field_name="validation_end_utc")
    test_start = require_utc(test_start_utc, field_name="test_start_utc")
    test_end = require_utc(test_end_utc, field_name="test_end_utc")
    if embargo < timedelta(0):
        raise ValueError("embargo cannot be negative")
    if not (train_end < val_start <= val_end < test_start <= test_end):
        raise ValueError("split boundaries must be chronological and non-overlapping")
    if val_start - train_end < embargo or test_start - val_end < embargo:
        raise ValueError("configured boundaries do not satisfy embargo")

    train: list[str] = []
    validation: list[str] = []
    test: list[str] = []
    purged: list[str] = []
    for sample in sorted(samples, key=lambda row: row.decision_at_utc):
        if sample.decision_at_utc <= train_end:
            if sample.label_interval_end_utc >= val_start:
                purged.append(sample.sample_id)
            else:
                train.append(sample.sample_id)
        elif val_start <= sample.decision_at_utc <= val_end:
            if sample.label_interval_end_utc >= test_start:
                purged.append(sample.sample_id)
            else:
                validation.append(sample.sample_id)
        elif test_start <= sample.decision_at_utc <= test_end:
            test.append(sample.sample_id)
        else:
            # Embargo-window and post-test samples remain explicitly accounted
            # for rather than silently disappearing from validation lineage.
            purged.append(sample.sample_id)

    accounted = train + validation + test + purged
    if len(accounted) != len(set(accounted)):
        raise ValueError("sample IDs must be unique across split accounting")
    return PurgedSplit(
        train_ids=tuple(train),
        validation_ids=tuple(validation),
        test_ids=tuple(test),
        purged_ids=tuple(purged),
    )
