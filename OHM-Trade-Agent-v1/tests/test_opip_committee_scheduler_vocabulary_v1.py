"""Integration checkpoint: scheduler vocabulary vs frozen DI request lifecycle.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The committee schedules committee work, so it keeps its own population-disposition
vocabulary. That vocabulary shares eight names with the frozen Decision
Intelligence ``RequestState``, which makes a cross-vocabulary collision the main
integration risk in this area. These tests hold the separation:

* the two vocabularies are distinct types and the committee plane never imports
  the DI contract;
* a durable row must carry an explicit record kind, because a bare ``COMPLETED``
  is ambiguous across the two vocabularies;
* a row of the wrong kind is refused rather than reinterpreted, so no implicit
  conversion exists in either direction;
* disposition identity survives a durable reload, and the states that carry
  different meaning stay distinct.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.scheduler import (
    CommitteeScheduleDisposition,
    PopulationTally,
    ScheduleDispositionRecord,
)
from app.opip.committee.serialization import (
    CommitteeSerializationError,
    population_tally_from_dict,
    population_tally_to_dict,
    schedule_disposition_from_dict,
    schedule_disposition_to_dict,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _record(
    disposition: CommitteeScheduleDisposition = CommitteeScheduleDisposition.COMPLETED,
    **overrides,
) -> ScheduleDispositionRecord:
    values = {
        "schedule_key_id": "COMMITTEE-SCHEDULE-KEY:aaaa",
        "evidence_id": "ev-1",
        "disposition": disposition,
        "decided_at": NOW,
        "reason": None,
        "detail": None,
    }
    values.update(overrides)
    return ScheduleDispositionRecord(**values)


# ------------------------------------------- vocabulary separation


def test_the_committee_plane_never_imports_the_frozen_di_request_state():
    """A cross-plane import would couple the two lifecycles.

    Checked by parsing imports rather than grepping text, so an explanatory
    comment naming the DI vocabulary is not mistaken for a dependency while a real
    import cannot hide behind formatting.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "opip" / "committee"
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "decision_intelligence.contracts" in module:
                    offenders.append(f"{path.name}: from {module}")
                for alias in node.names:
                    if alias.name == "RequestState":
                        offenders.append(f"{path.name}: {alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "decision_intelligence.contracts" in alias.name:
                        offenders.append(f"{path.name}: import {alias.name}")
    assert offenders == [], offenders


def test_the_two_vocabularies_are_different_types_with_shared_names():
    """The collision is real, which is why the record kind is required."""
    from app.opip.decision_intelligence.contracts import RequestState

    committee = {disposition.value for disposition in CommitteeScheduleDisposition}
    di = {state.value for state in RequestState}
    shared = committee & di
    # Pin the overlap: if it changes, the kind guard's justification changes too.
    assert shared == {
        "ELIGIBLE",
        "SELECTED",
        "SKIPPED_BUDGET",
        "SKIPPED_CAPACITY",
        "EXPIRED",
        "FAILED",
        "INVALID",
        "COMPLETED",
    }
    assert CommitteeScheduleDisposition is not RequestState
    # The committee vocabulary is a superset: it distinguishes two states DI does
    # not carry, and neither is a rename of an existing one.
    assert committee - di == {"LATE", "UNAVAILABLE"}


def test_the_committee_vocabulary_is_not_a_second_authority():
    """It describes committee population; it does not restate DI request lifecycle."""
    from app.opip.decision_intelligence.contracts import RequestState

    committee = {disposition.value for disposition in CommitteeScheduleDisposition}
    di = {state.value for state in RequestState}
    # The committee does not carry DI-only lifecycle states it has no use for,
    # which is what would indicate it was trying to replace RequestState.
    assert di - committee == set()


def test_unavailable_and_late_stay_distinct_from_the_other_states():
    """Each of these means something different and must not be conflated."""
    distinct = {
        CommitteeScheduleDisposition.UNAVAILABLE,
        CommitteeScheduleDisposition.LATE,
        CommitteeScheduleDisposition.FAILED,
        CommitteeScheduleDisposition.INVALID,
        CommitteeScheduleDisposition.EXPIRED,
        CommitteeScheduleDisposition.SKIPPED_BUDGET,
        CommitteeScheduleDisposition.SKIPPED_CAPACITY,
    }
    assert len(distinct) == 7
    values = {disposition.value for disposition in distinct}
    assert len(values) == 7


def test_skipped_budget_and_skipped_capacity_are_not_interchangeable():
    assert (
        CommitteeScheduleDisposition.SKIPPED_BUDGET.value
        != CommitteeScheduleDisposition.SKIPPED_CAPACITY.value
    )


# ------------------------------------------- durable persistence


def test_a_disposition_round_trips_through_the_durable_codec():
    record = _record(reason="policy mismatch", detail="extra")
    row = schedule_disposition_to_dict(record)
    assert row["kind"] == "COMMITTEE_SCHEDULE_DISPOSITION"
    assert row["disposition"] == "COMPLETED"
    restored = schedule_disposition_from_dict(row)
    assert restored.disposition is record.disposition
    assert restored.decided_at == record.decided_at
    assert restored.schedule_key_id == record.schedule_key_id
    assert restored.evidence_id == record.evidence_id
    assert restored.reason == record.reason
    assert restored.detail == record.detail


def test_every_disposition_survives_a_reload():
    for disposition in CommitteeScheduleDisposition:
        row = schedule_disposition_to_dict(_record(disposition))
        assert schedule_disposition_from_dict(row).disposition is disposition


def test_a_row_without_a_record_kind_is_refused():
    """An unlabelled COMPLETED is exactly the ambiguous case."""
    row = schedule_disposition_to_dict(_record())
    row.pop("kind")
    with pytest.raises(CommitteeSerializationError, match="must declare its record kind"):
        schedule_disposition_from_dict(row)


def test_a_row_of_another_kind_is_refused_rather_than_reinterpreted():
    row = schedule_disposition_to_dict(_record())
    row["kind"] = "DI_REQUEST_STATE"
    with pytest.raises(CommitteeSerializationError, match="expected a"):
        schedule_disposition_from_dict(row)


def test_an_undeclared_disposition_name_is_refused():
    row = schedule_disposition_to_dict(_record())
    row["disposition"] = "NOT_A_DISPOSITION"
    with pytest.raises(CommitteeSerializationError, match="not a declared"):
        schedule_disposition_from_dict(row)


def test_a_non_string_disposition_is_refused():
    row = schedule_disposition_to_dict(_record())
    row["disposition"] = 1
    with pytest.raises(CommitteeSerializationError, match="must be a string"):
        schedule_disposition_from_dict(row)


def test_unknown_fields_are_refused():
    row = schedule_disposition_to_dict(_record())
    row["unexpected"] = "x"
    with pytest.raises(CommitteeSerializationError, match="undeclared"):
        schedule_disposition_from_dict(row)


def test_the_codec_is_the_only_place_the_disposition_is_stringified():
    """A durable row stores the declared value, not a derived label."""
    for disposition in CommitteeScheduleDisposition:
        row = schedule_disposition_to_dict(_record(disposition))
        assert row["disposition"] == disposition.value


# ------------------------------------------- tally persistence


def test_a_tally_round_trips_and_states_every_disposition():
    counts = {disposition: 0 for disposition in CommitteeScheduleDisposition}
    counts[CommitteeScheduleDisposition.COMPLETED] = 3
    counts[CommitteeScheduleDisposition.SKIPPED_BUDGET] = 1
    tally = PopulationTally(counts=counts, considered=4, redelivered=0)
    row = population_tally_to_dict(tally)
    assert row["kind"] == "COMMITTEE_POPULATION_TALLY"
    assert len(row["counts"]) == len(CommitteeScheduleDisposition)
    restored = population_tally_from_dict(row)
    assert restored.count(CommitteeScheduleDisposition.COMPLETED) == 3
    assert restored.count(CommitteeScheduleDisposition.SKIPPED_BUDGET) == 1
    assert restored.considered == 4


def test_a_reloaded_tally_cannot_lose_a_state():
    """A state missing from the row would read as one that does not exist."""
    tally = PopulationTally(
        counts={disposition: 0 for disposition in CommitteeScheduleDisposition},
        considered=0,
        redelivered=0,
    )
    row = population_tally_to_dict(tally)
    row["counts"].pop("UNAVAILABLE")
    with pytest.raises(CommitteeSerializationError, match="must state every disposition"):
        population_tally_from_dict(row)


def test_a_tally_of_another_kind_is_refused():
    tally = PopulationTally(
        counts={disposition: 0 for disposition in CommitteeScheduleDisposition},
        considered=0,
        redelivered=0,
    )
    row = population_tally_to_dict(tally)
    row["kind"] = "DI_POPULATION_TALLY"
    with pytest.raises(CommitteeSerializationError, match="expected a"):
        population_tally_from_dict(row)


def test_a_negative_tally_count_is_refused():
    tally = PopulationTally(
        counts={disposition: 0 for disposition in CommitteeScheduleDisposition},
        considered=0,
        redelivered=0,
    )
    row = population_tally_to_dict(tally)
    row["counts"]["FAILED"] = -1
    with pytest.raises(CommitteeSerializationError, match="non-negative integer"):
        population_tally_from_dict(row)


# ------------------------------------------- determinism


def test_a_reloaded_disposition_is_byte_identical_on_re_encoding():
    """Reload must be lossless, so a restart cannot drift the record."""
    for disposition in CommitteeScheduleDisposition:
        record = _record(disposition, reason="r", detail="d")
        first = schedule_disposition_to_dict(record)
        restored = schedule_disposition_from_dict(first)
        second = schedule_disposition_to_dict(restored)
        assert first == second


def test_redelivery_semantics_are_unaffected_by_the_codec():
    """Persistence must not invent a second identity for the same decision."""
    record = _record()
    restored = schedule_disposition_from_dict(schedule_disposition_to_dict(record))
    assert restored.disposition_id == record.disposition_id


def test_the_disposition_record_is_immutable():
    record = _record()
    with pytest.raises(Exception):
        record.disposition = CommitteeScheduleDisposition.FAILED  # type: ignore[misc]


def test_decided_at_is_normalised_to_utc_on_reload():
    naive = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    row = schedule_disposition_to_dict(_record(decided_at=naive))
    assert schedule_disposition_from_dict(row).decided_at.tzinfo is not None
    # A stored timestamp with an offset decodes to the same instant.
    shifted = naive.astimezone(timezone(timedelta(hours=-5)))
    row2 = schedule_disposition_to_dict(_record(decided_at=shifted))
    assert schedule_disposition_from_dict(row2).decided_at == naive
