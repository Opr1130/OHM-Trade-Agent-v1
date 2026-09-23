"""Durable scheduling from committed evidence (IC-016).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the scheduler's guarantees: it plans only from committed
evidence, it is idempotent under redelivery, it survives a restart, every item
reaches a recorded disposition, and a scheduler fault never becomes a trading-path
fault.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping

import pytest

from app.opip.committee.scheduler import (
    MODE_DISABLED,
    CommitteeScheduleDisposition,
    CommitteeScheduler,
    CommittedEvidenceItem,
    PopulationTally,
    ScheduleDispositionRecord,
    ScheduleKey,
    SchedulerBudget,
    SchedulerCursor,
)
from app.opip.committee.settings import (
    COMMITTEE_MODE_OFF,
    COMMITTEE_MODE_SHADOW,
    CommitteeShadowSettings,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=10)

SHADOW = CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW)
OFF = CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_OFF)


@dataclass
class InMemoryCheckpoint:
    """A checkpoint that behaves like the durable one, including persistence."""

    decided: dict[str, ScheduleDispositionRecord] = field(default_factory=dict)
    saves: int = 0

    def load_decided(self) -> Mapping[str, ScheduleDispositionRecord]:
        return dict(self.decided)

    def save_disposition(self, record: ScheduleDispositionRecord) -> None:
        self.saves += 1
        self.decided.setdefault(record.schedule_key_id, record)


def _item(
    evidence_id: str = "ev-1",
    *,
    case_id: str = "case-1",
    committed: bool = True,
    sealed: bool = True,
    available_at: datetime = CUTOFF,
    evidence_cutoff_at: datetime = CUTOFF,
    expires_at: datetime | None = None,
    cost: int | None = 100,
    snapshot_hash: str = "COMMITTEE-EVIDENCE:aaaa",
) -> CommittedEvidenceItem:
    return CommittedEvidenceItem(
        evidence_id=evidence_id,
        case_id=case_id,
        evidence_snapshot_hash=snapshot_hash,
        committee_policy_version="policy-v1",
        committed=committed,
        sealed=sealed,
        available_at=available_at,
        evidence_cutoff_at=evidence_cutoff_at,
        expires_at=expires_at,
        estimated_cost_microunits=cost,
    )


def _scheduler(
    checkpoint: InMemoryCheckpoint | None = None,
    *,
    settings=SHADOW,
    cases: int = 10,
    cost: int = 1_000,
) -> tuple[CommitteeScheduler, InMemoryCheckpoint]:
    checkpoint = checkpoint or InMemoryCheckpoint()
    scheduler = CommitteeScheduler(
        checkpoint=checkpoint,
        now=lambda: NOW,
        budget=SchedulerBudget(max_committee_cases=cases, max_cost_microunits=cost),
        settings=settings,
    )
    return scheduler, checkpoint


class Recorder:
    """An executor that records what it was handed."""

    def __init__(self, *, complete: bool = True, raises: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._complete = complete
        self._raises = raises

    def __call__(self, item: CommittedEvidenceItem) -> bool:
        self.calls.append(item.evidence_id)
        if self._raises is not None:
            raise self._raises
        return self._complete


# ------------------------------------------------------- mode and authority


def test_mode_off_produces_no_work_and_no_execution():
    """Committee OFF must not select or execute anything."""
    scheduler, checkpoint = _scheduler(settings=OFF)
    executor = Recorder()
    run = scheduler.run_cycle(items=[_item()], executor=executor)
    assert run.ran is False
    assert run.reason == MODE_DISABLED
    assert executor.calls == []
    assert checkpoint.saves == 0
    assert run.tally.total_accounted == 0
    assert run.selected_count == 0


def test_an_unknown_mode_is_treated_as_off():
    """An unreadable configuration resolves to dark, never to enabled."""
    scheduler, _ = _scheduler(settings=CommitteeShadowSettings(opip_committee_mode="nonsense"))
    executor = Recorder()
    run = scheduler.run_cycle(items=[_item()], executor=executor)
    assert run.ran is False
    assert executor.calls == []


# --------------------------------------------------------- eligibility gates


def test_uncommitted_evidence_is_invalid_and_never_scheduled():
    scheduler, _ = _scheduler()
    executor = Recorder()
    run = scheduler.run_cycle(items=[_item(committed=False)], executor=executor)
    assert executor.calls == []
    assert run.tally.count(CommitteeScheduleDisposition.INVALID) == 1


def test_unsealed_evidence_is_invalid_and_never_scheduled():
    scheduler, _ = _scheduler()
    executor = Recorder()
    run = scheduler.run_cycle(items=[_item(sealed=False)], executor=executor)
    assert executor.calls == []
    assert run.tally.count(CommitteeScheduleDisposition.INVALID) == 1


def test_future_dated_evidence_is_invalid():
    scheduler, _ = _scheduler()
    executor = Recorder()
    run = scheduler.run_cycle(
        items=[_item(evidence_cutoff_at=NOW + timedelta(hours=1))], executor=executor
    )
    assert executor.calls == []
    assert run.tally.count(CommitteeScheduleDisposition.INVALID) == 1


def test_an_expired_item_is_expired_not_silently_dropped():
    scheduler, _ = _scheduler()
    executor = Recorder()
    run = scheduler.run_cycle(
        items=[_item(expires_at=NOW - timedelta(seconds=1))], executor=executor
    )
    assert executor.calls == []
    assert run.tally.count(CommitteeScheduleDisposition.EXPIRED) == 1


def test_late_arriving_evidence_is_recorded_but_not_treated_as_timely():
    scheduler, _ = _scheduler()
    executor = Recorder()
    run = scheduler.run_cycle(
        items=[_item(available_at=NOW + timedelta(minutes=5))], executor=executor
    )
    assert executor.calls == []
    assert run.tally.count(CommitteeScheduleDisposition.LATE) == 1


def test_an_unknown_cost_is_refused_rather_than_assumed_to_fit():
    """An unverifiable budget is not a satisfied budget."""
    scheduler, _ = _scheduler()
    executor = Recorder()
    run = scheduler.run_cycle(items=[_item(cost=None)], executor=executor)
    assert executor.calls == []
    assert run.tally.count(CommitteeScheduleDisposition.UNAVAILABLE) == 1


# ------------------------------------------------------- budget and capacity


def test_capacity_exhaustion_is_skipped_capacity_not_dropped():
    scheduler, _ = _scheduler(cases=1)
    executor = Recorder()
    run = scheduler.run_cycle(
        items=[_item("ev-1", case_id="c1"), _item("ev-2", case_id="c2")],
        executor=executor,
    )
    assert run.tally.count(CommitteeScheduleDisposition.SKIPPED_CAPACITY) == 1
    assert run.tally.count(CommitteeScheduleDisposition.COMPLETED) == 1
    assert len(executor.calls) == 1


def test_cost_budget_exhaustion_is_skipped_budget_not_dropped():
    scheduler, _ = _scheduler(cost=150)
    executor = Recorder()
    run = scheduler.run_cycle(
        items=[_item("ev-1", case_id="c1", cost=100), _item("ev-2", case_id="c2", cost=100)],
        executor=executor,
    )
    assert run.tally.count(CommitteeScheduleDisposition.SKIPPED_BUDGET) == 1
    assert run.tally.count(CommitteeScheduleDisposition.COMPLETED) == 1


def test_skipped_budget_and_skipped_capacity_stay_distinguishable():
    """One is a spending decision, the other a concurrency decision."""
    assert (
        CommitteeScheduleDisposition.SKIPPED_BUDGET
        is not CommitteeScheduleDisposition.SKIPPED_CAPACITY
    )


def test_a_zero_budget_refuses_all_work():
    scheduler, _ = _scheduler(cases=0, cost=0)
    executor = Recorder()
    run = scheduler.run_cycle(items=[_item()], executor=executor)
    assert executor.calls == []
    assert run.tally.count(CommitteeScheduleDisposition.SKIPPED_CAPACITY) == 1


# --------------------------------------------------- idempotency / restart


def test_the_schedule_key_is_deterministic_and_content_derived():
    first = ScheduleKey.for_item(_item())
    second = ScheduleKey.for_item(_item(evidence_id="ev-redelivered"))
    assert first.schedule_key_id == second.schedule_key_id
    other = ScheduleKey.for_item(_item(snapshot_hash="COMMITTEE-EVIDENCE:bbbb"))
    assert other.schedule_key_id != first.schedule_key_id


def test_redelivered_evidence_creates_no_second_logical_case():
    """At-least-once delivery must not multiply committee work."""
    scheduler, checkpoint = _scheduler()
    executor = Recorder()
    first = scheduler.run_cycle(items=[_item()], executor=executor)
    assert first.tally.count(CommitteeScheduleDisposition.COMPLETED) == 1

    second = scheduler.run_cycle(
        items=[_item(evidence_id="ev-1-again")], executor=executor
    )
    assert executor.calls == ["ev-1"]  # executed once, not twice
    assert second.tally.redelivered == 1
    assert len(checkpoint.decided) == 1


def test_a_replay_returns_the_original_disposition_unchanged():
    scheduler, checkpoint = _scheduler()
    first = scheduler.run_cycle(items=[_item()], executor=Recorder())
    original = first.dispositions[0]
    replay = scheduler.run_cycle(items=[_item(evidence_id="ev-again")], executor=Recorder())
    assert replay.dispositions[0].disposition is original.disposition
    assert replay.dispositions[0].disposition_id == original.disposition_id
    assert checkpoint.saves == 1  # nothing new was persisted


def test_a_restart_resumes_from_the_durable_checkpoint():
    """A fresh scheduler over the same checkpoint must not reprocess."""
    checkpoint = InMemoryCheckpoint()
    scheduler, _ = _scheduler(checkpoint)
    scheduler.run_cycle(items=[_item()], executor=Recorder())

    # A new scheduler instance, as a restarted process would build.
    restarted = CommitteeScheduler(
        checkpoint=checkpoint,
        now=lambda: NOW,
        budget=SchedulerBudget(max_committee_cases=10, max_cost_microunits=1_000),
        settings=SHADOW,
    )
    executor = Recorder()
    run = restarted.run_cycle(items=[_item()], executor=executor)
    assert executor.calls == []
    assert run.tally.redelivered == 1


def test_the_cursor_advances_with_processed_items():
    scheduler, _ = _scheduler()
    run = scheduler.run_cycle(items=[_item("ev-1"), _item("ev-2", case_id="c2")])
    assert run.cursor.processed == 2
    assert run.cursor.last_evidence_id == "ev-2"
    assert run.cursor.cursor_id.startswith("COMMITTEE-CURSOR:")


def test_the_cursor_requires_a_real_position():
    with pytest.raises(ValueError):
        SchedulerCursor(last_evidence_id=None, processed=-1, updated_at=NOW)


# ------------------------------------------------------- accounting honesty


def test_every_item_reaches_exactly_one_disposition():
    """A consideration that vanishes from the tally is an unaccounted loss."""
    scheduler, _ = _scheduler(cases=2, cost=150)
    items = [
        _item("ev-1", case_id="c1", cost=100),
        _item("ev-2", case_id="c2", cost=100),
        _item("ev-3", case_id="c3", committed=False),
        _item("ev-4", case_id="c4", sealed=False),
        _item("ev-5", case_id="c5", expires_at=NOW - timedelta(seconds=1)),
        _item("ev-6", case_id="c6", cost=None),
        _item("ev-7", case_id="c7", available_at=NOW + timedelta(minutes=1)),
    ]
    run = scheduler.run_cycle(items=items, executor=Recorder())
    assert run.tally.considered == len(items)
    assert run.tally.total_accounted == len(items)


def test_the_tally_reports_every_state_even_when_zero():
    tally = PopulationTally(
        counts={}, considered=0, redelivered=0
    )
    mapped = tally.as_dict()
    for disposition in CommitteeScheduleDisposition:
        assert disposition.value in mapped
        assert mapped[disposition.value] == 0


def test_the_ten_required_dispositions_exist():
    """The population vocabulary is fixed and complete."""
    assert {d.value for d in CommitteeScheduleDisposition} == {
        "ELIGIBLE",
        "SELECTED",
        "SKIPPED_BUDGET",
        "SKIPPED_CAPACITY",
        "EXPIRED",
        "INVALID",
        "FAILED",
        "UNAVAILABLE",
        "LATE",
        "COMPLETED",
    }


# --------------------------------------------------------- failure containment


def test_an_executor_exception_becomes_a_recorded_failure():
    """A scheduler fault must never propagate into a caller's trading path."""
    scheduler, _ = _scheduler()
    executor = Recorder(raises=RuntimeError("provider exploded"))
    run = scheduler.run_cycle(items=[_item()], executor=executor)
    assert run.tally.count(CommitteeScheduleDisposition.FAILED) == 1
    record = run.dispositions[0]
    assert "RuntimeError" in (record.detail or "")


def test_an_executor_reporting_no_completion_is_a_failure_not_a_success():
    scheduler, _ = _scheduler()
    executor = Recorder(complete=False)
    run = scheduler.run_cycle(items=[_item()], executor=executor)
    assert run.tally.count(CommitteeScheduleDisposition.FAILED) == 1
    assert run.tally.count(CommitteeScheduleDisposition.COMPLETED) == 0


def test_a_missing_executor_is_unavailable_rather_than_silently_skipped():
    scheduler, _ = _scheduler()
    run = scheduler.run_cycle(items=[_item()])
    assert run.tally.count(CommitteeScheduleDisposition.UNAVAILABLE) == 1


def test_a_failed_item_is_persisted_so_it_is_not_lost():
    scheduler, checkpoint = _scheduler()
    scheduler.run_cycle(
        items=[_item()], executor=Recorder(raises=ValueError("boom"))
    )
    assert checkpoint.saves == 1
    assert len(checkpoint.decided) == 1


def test_each_disposition_is_persisted_before_the_cycle_continues():
    """A crash mid-cycle must not lose a decision already made."""
    scheduler, checkpoint = _scheduler()
    scheduler.run_cycle(items=[_item("ev-1"), _item("ev-2", case_id="c2")])
    assert checkpoint.saves == 2
    assert len(checkpoint.decided) == 2


# ----------------------------------------------------------- budget / contracts


def test_a_budget_rejects_negative_limits():
    for field_name in ("max_committee_cases", "max_cost_microunits"):
        values = {"max_committee_cases": 1, "max_cost_microunits": 1}
        values[field_name] = -1
        with pytest.raises(ValueError, match=field_name):
            SchedulerBudget(**values)


def test_evidence_requires_money_to_be_known_or_explicitly_absent():
    with pytest.raises(ValueError, match="estimated_cost_microunits"):
        _item(cost=-5)


def test_expiry_must_follow_the_cutoff():
    with pytest.raises(ValueError, match="expires_at"):
        _item(expires_at=CUTOFF - timedelta(seconds=1))


def test_the_scheduler_performs_no_model_call_of_its_own():
    """The scheduler is a planner; it holds no provider surface at all."""
    import inspect

    from app.opip.committee import scheduler as scheduler_module

    source = inspect.getsource(scheduler_module)
    for forbidden in (
        "ProviderWireRequest",
        "CommitteeProvider",
        "screen_model_bound_view",
        "os.environ",
        "requests.",
        "httpx",
    ):
        assert forbidden not in source, forbidden
