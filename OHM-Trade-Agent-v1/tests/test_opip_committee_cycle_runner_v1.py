"""The isolated shadow worker's cycle entry point (IC-042).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests prove the deployment artifact would behave correctly if installed: it
refuses without an exact release identity, performs no work while the plane is off,
runs exactly one bounded cycle when enabled, enforces the UTC daily ceiling, and
writes an observable report. No provider call is made and nothing is deployed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    EvidenceItem,
    EvidenceSnapshot,
    ProviderFamily,
)
from app.opip.committee.cycle_runner import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    CYCLE_DISPOSITIONS_FILE,
    EVIDENCE_ITEMS_FILE,
    TRUST_REPORT_FILE,
    CycleConfigurationError,
    FileCheckpoint,
    load_evidence_items,
    main,
    run_once,
)
from app.opip.committee.registry import APPROVED_MAX_DAILY_COST_MICROUNITS
from app.opip.decision_intelligence.identity import Provenance
from app.opip.committee.scheduler import (
    CommitteeScheduleDisposition,
    SchedulerBudget,
)
from app.opip.committee.settings import COMMITTEE_MODE_SHADOW, CommitteeShadowSettings

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def _item(evidence_id: str = "ev-1", *, cost: int | None = 400_000) -> dict:
    return {
        "evidence_id": evidence_id,
        "case_id": f"case-{evidence_id}",
        "evidence_snapshot_hash": f"COMMITTEE-EVIDENCE:{evidence_id}",
        "committee_policy_version": "policy-v1",
        "committed": True,
        "sealed": True,
        "available_at": NOW.isoformat(),
        "evidence_cutoff_at": NOW.isoformat(),
        "expires_at": None,
        "estimated_cost_microunits": cost,
    }


def _write_items(root: Path, items: list[dict]) -> Path:
    path = root / EVIDENCE_ITEMS_FILE
    path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")
    return path


def _case_ingress_row() -> dict:
    policy = CommitteePolicy(
        policy_version="policy-v1",
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id="committee-v1",
        prompt_version="prompt-v1",
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=500_000,
    )
    evidence = EvidenceItem(
        evidence_id="fact-1",
        source_id="canonical:1",
        available_at=NOW,
        payload={
            "instrument_id": "BTCUSD",
            "metric_name": "example",
            "metric_value": "1",
        },
    )
    snapshot = EvidenceSnapshot(
        case_id="case-ingress-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=NOW,
        assembled_at=NOW,
        items=(evidence,),
        source_refs=("canonical:1",),
        committee_policy_version=policy.policy_version,
        prompt_template_id=policy.prompt_template_id,
        prompt_version=policy.prompt_version,
        instrument_id="BTCUSD",
    )
    case = CommitteeCase(
        case_id="case-ingress-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=NOW,
        provenance=Provenance(
            producing_component="test.cycle_runner",
            artifact_or_build_id="test",
            process_instance_id="cycle-runner-1",
            emitted_at=NOW,
            source_record_refs=("canonical:1",),
        ),
        instrument_id="BTCUSD",
    )
    return {
        "schema_version": 1,
        "evidence_id": "queue-ingress-1",
        "evidence_snapshot_hash": snapshot.snapshot_hash,
        "committee_policy_version": policy.policy_version,
        "committed": True,
        "sealed": True,
        "available_at": NOW.isoformat(),
        "expires_at": None,
        "estimated_cost_microunits": 400_000,
        "case": {
            "case_id": case.case_id,
            "case_type": case.case_type.value,
            "created_at": case.created_at.isoformat(),
            "instrument_id": case.instrument_id,
            "strategy_context_id": None,
            "canonical_binding": None,
            "policy": {
                "policy_version": policy.policy_version,
                "seated_providers": [
                    family.value for family in policy.seated_providers
                ],
                "prompt_template_id": policy.prompt_template_id,
                "prompt_version": policy.prompt_version,
                "max_attempts_per_seat": policy.max_attempts_per_seat,
                "max_estimated_cost_microunits": (
                    policy.max_estimated_cost_microunits
                ),
            },
            "snapshot": {
                "case_id": snapshot.case_id,
                "evidence_cutoff_at": snapshot.evidence_cutoff_at.isoformat(),
                "assembled_at": snapshot.assembled_at.isoformat(),
                "items": [
                    {
                        "evidence_id": evidence.evidence_id,
                        "source_id": evidence.source_id,
                        "available_at": evidence.available_at.isoformat(),
                        "payload": dict(evidence.payload),
                    }
                ],
                "source_refs": list(snapshot.source_refs),
                "committee_policy_version": snapshot.committee_policy_version,
                "prompt_template_id": snapshot.prompt_template_id,
                "prompt_version": snapshot.prompt_version,
                "instrument_id": snapshot.instrument_id,
                "strategy_context_id": snapshot.strategy_context_id,
            },
            "provenance": {
                "producing_component": case.provenance.producing_component,
                "artifact_or_build_id": case.provenance.artifact_or_build_id,
                "process_instance_id": case.provenance.process_instance_id,
                "emitted_at": case.provenance.emitted_at.isoformat(),
                "source_record_refs": list(case.provenance.source_record_refs),
            },
        },
    }


def _write_case_ingress(root: Path) -> Path:
    path = root / "committed_committee_cases.jsonl"
    path.write_text(json.dumps(_case_ingress_row()) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------- release identity


def test_a_full_sha_is_required_and_a_branch_name_is_refused(tmp_path):
    with pytest.raises(CycleConfigurationError, match="40-character release SHA"):
        run_once(release_sha="main", committee_home=tmp_path, evidence_path=tmp_path / EVIDENCE_ITEMS_FILE, now=NOW)
    with pytest.raises(CycleConfigurationError, match="40-character release SHA"):
        run_once(release_sha="", committee_home=tmp_path, evidence_path=tmp_path / EVIDENCE_ITEMS_FILE, now=NOW)
    with pytest.raises(CycleConfigurationError, match="40-character release SHA"):
        run_once(release_sha="abc1234", committee_home=tmp_path, evidence_path=tmp_path / EVIDENCE_ITEMS_FILE, now=NOW)


def test_main_refuses_without_an_advisory_directory():
    """The worker states its output path; it holds no data-path literal."""
    assert main(["--release-sha", SHA]) == EXIT_CONFIG_ERROR


def test_main_returns_a_config_error_for_a_branch_release(tmp_path):
    code = main(["--release-sha", "main", "--committee-home", str(tmp_path)])
    assert code == EXIT_CONFIG_ERROR


# ------------------------------------------------- dark by default


def test_mode_off_performs_no_work_and_records_nothing_selected(tmp_path):
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        now=NOW,
    )
    assert outcome.ran is False
    assert outcome.reason == "MODE_DISABLED"
    assert outcome.considered == 0
    assert outcome.dispositions[CommitteeScheduleDisposition.COMPLETED.value] == 0


def test_the_default_settings_leave_the_plane_dark(tmp_path):
    """Invoking the worker without explicit settings must not enable it."""
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        now=NOW,
    )
    assert outcome.ran is False


def test_main_succeeds_without_doing_work_when_mode_is_off(tmp_path):
    _write_items(tmp_path, [_item()])
    code = main(["--release-sha", SHA, "--committee-home", str(tmp_path)])
    assert code == EXIT_OK


# ------------------------------------------------- shadow cycle


def test_an_enabled_cycle_runs_once_and_accounts_for_its_population(tmp_path):
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item("ev-1"), _item("ev-2")]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    assert outcome.ran is True
    assert outcome.considered == 2
    # No executor is wired, so a selected item resolves to UNAVAILABLE rather than
    # being executed: scheduling is exercised, spending is not.
    assert outcome.dispositions[CommitteeScheduleDisposition.UNAVAILABLE.value] == 2


def test_no_provider_call_is_made_by_a_cycle(tmp_path):
    """The runner constructs no transport, so a cycle cannot spend."""
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    assert outcome.ran is True
    report = json.loads((tmp_path / TRUST_REPORT_FILE).read_text(encoding="utf-8"))
    # Latency is unmeasured because nothing was invoked.
    assert report["mean_latency_micros"] is None


def test_validated_case_ingress_dispatches_the_exact_reconstructed_case(tmp_path):
    seen: list[CommitteeCase] = []

    def execute(case: CommitteeCase) -> bool:
        seen.append(case)
        return True

    row = _case_ingress_row()
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=tmp_path / EVIDENCE_ITEMS_FILE,
        case_ingress_path=_write_case_ingress(tmp_path),
        case_executor=execute,
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    assert outcome.dispositions[CommitteeScheduleDisposition.COMPLETED.value] == 1
    assert len(seen) == 1
    assert seen[0].case_id == row["case"]["case_id"]
    assert seen[0].snapshot.snapshot_hash == row["evidence_snapshot_hash"]

    daily = json.loads((tmp_path / "daily_spend.json").read_text(encoding="utf-8"))
    today = daily[NOW.date().isoformat()]
    assert today["spent_microunits"] == 400_000
    assert today["reservations"] == 1


def test_an_executor_cannot_run_without_validated_case_ingress(tmp_path):
    with pytest.raises(
        CycleConfigurationError,
        match="case_executor requires a validated case_ingress_path",
    ):
        run_once(
            release_sha=SHA,
            committee_home=tmp_path,
            evidence_path=_write_items(tmp_path, [_item()]),
            case_executor=lambda case: True,
            settings=CommitteeShadowSettings(
                opip_committee_mode=COMMITTEE_MODE_SHADOW
            ),
            now=NOW,
        )


def test_a_second_cycle_does_not_reprocess_the_same_evidence(tmp_path):
    items = _write_items(tmp_path, [_item("ev-1")])
    settings = CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW)
    first = run_once(
        release_sha=SHA, committee_home=tmp_path, evidence_path=items, settings=settings, now=NOW
    )
    second = run_once(
        release_sha=SHA, committee_home=tmp_path, evidence_path=items, settings=settings, now=NOW
    )
    # The durable checkpoint means the redelivery is acknowledged, not reprocessed.
    assert second.considered == 1
    assert first.dispositions[CommitteeScheduleDisposition.UNAVAILABLE.value] == 1
    assert second.dispositions[CommitteeScheduleDisposition.UNAVAILABLE.value] == 1


# ------------------------------------------------- daily ceiling


def test_the_cycle_budget_is_set_from_the_remaining_daily_ceiling(tmp_path):
    """A cycle can never spend more than the UTC day has left."""
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    report = json.loads((tmp_path / TRUST_REPORT_FILE).read_text(encoding="utf-8"))
    assert report["remaining_daily_ceiling_microunits"] == APPROVED_MAX_DAILY_COST_MICROUNITS
    assert outcome.ran is True


def test_an_exhausted_daily_ceiling_refuses_every_item(tmp_path):
    (tmp_path / "daily_spend.json").write_text(
        json.dumps(
            {
                NOW.date().isoformat(): {
                    "spent_microunits": APPROVED_MAX_DAILY_COST_MICROUNITS,
                    "reservations": 20,
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    # Nothing can be selected once the day's allowance is gone.
    assert outcome.dispositions[CommitteeScheduleDisposition.UNAVAILABLE.value] == 0
    assert (
        outcome.dispositions[CommitteeScheduleDisposition.SKIPPED_CAPACITY.value]
        + outcome.dispositions[CommitteeScheduleDisposition.SKIPPED_BUDGET.value]
    ) == 1


def test_an_unreadable_daily_spend_record_fails_closed(tmp_path):
    """A corrupt spend record must not read as zero spend, which would re-open the ceiling."""
    (tmp_path / "daily_spend.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="daily spend record is unreadable"):
        run_once(
            release_sha=SHA,
            committee_home=tmp_path,
            evidence_path=_write_items(tmp_path, [_item()]),
            settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
            now=NOW,
        )


# ------------------------------------------------- evidence input


def test_missing_evidence_is_an_empty_population_not_an_error(tmp_path):
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=tmp_path / EVIDENCE_ITEMS_FILE,
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    assert outcome.ran is True
    assert outcome.considered == 0


def test_malformed_evidence_raises_rather_than_being_silently_skipped(tmp_path):
    """Skipping a bad row would understate the population."""
    path = tmp_path / EVIDENCE_ITEMS_FILE
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(CycleConfigurationError, match="not valid JSON"):
        load_evidence_items(path)


def test_an_evidence_row_missing_a_required_field_raises(tmp_path):
    path = tmp_path / EVIDENCE_ITEMS_FILE
    path.write_text(json.dumps({"evidence_id": "ev-1"}) + "\n", encoding="utf-8")
    with pytest.raises(CycleConfigurationError, match="not a valid evidence item"):
        load_evidence_items(path)


# ------------------------------------------------- observability


def test_the_report_is_written_and_states_what_could_not_be_measured(tmp_path):
    run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    report = json.loads((tmp_path / TRUST_REPORT_FILE).read_text(encoding="utf-8"))
    assert report["report_id"].startswith("COMMITTEE-TRUST-REPORT:")
    assert report["stage"] == "T0_UNTRUSTED_SHADOW"
    assert report["blocked_gates"]
    assert report["insufficiency_reasons"]
    # Every disposition is present, so an absent state cannot be misread.
    for disposition in CommitteeScheduleDisposition:
        assert disposition.value in report["population"]


def test_dispositions_are_persisted_before_the_cycle_completes(tmp_path):
    run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / CYCLE_DISPOSITIONS_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["evidence_id"] == "ev-1"


def test_the_checkpoint_rebuilds_from_its_durable_file(tmp_path):
    run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=NOW,
    )
    # A fresh checkpoint over the same directory, as a restarted worker would build.
    restarted = FileCheckpoint(tmp_path)
    assert len(restarted.load_decided()) == 1


def test_a_corrupt_disposition_row_blocks_reprocessing(tmp_path):
    (tmp_path / CYCLE_DISPOSITIONS_FILE).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(CycleConfigurationError, match="cannot be reconstructed safely"):
        FileCheckpoint(tmp_path).load_decided()


def test_the_cycle_budget_can_be_supplied_by_the_caller(tmp_path):
    outcome = run_once(
        release_sha=SHA,
        committee_home=tmp_path,
        evidence_path=_write_items(tmp_path, [_item()]),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        budget=SchedulerBudget(max_committee_cases=0, max_cost_microunits=0),
        now=NOW,
    )
    assert outcome.dispositions[CommitteeScheduleDisposition.SKIPPED_CAPACITY.value] == 1
