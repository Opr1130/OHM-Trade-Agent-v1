"""Runtime bridge from sealed cases into the isolated Committee cycle.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.committee import cycle_runner
from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    ProviderFamily,
)
from app.opip.committee.evidence import build_evidence_item, build_evidence_snapshot
from app.opip.committee.scheduler import CommitteeScheduleDisposition, SchedulerBudget
from app.opip.committee.settings import COMMITTEE_MODE_SHADOW, CommitteeShadowSettings
from app.opip.committee.shadow_case_bridge import ShadowCaseEnvelopeError
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
SHA = "b" * 40


def _case() -> CommitteeCase:
    policy = CommitteePolicy(
        policy_version="committee-shadow-policy-v1",
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id="committee-system",
        prompt_version="v1",
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=500_000,
    )
    item = build_evidence_item(
        evidence_id="ev-runtime-1",
        source_id="canonical-learning-replica",
        available_at=NOW,
        payload={"symbol": "BTC/USD", "signal": "neutral"},
        evidence_cutoff_at=NOW,
    )
    snapshot = build_evidence_snapshot(
        case_id="case-runtime-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=NOW,
        assembled_at=NOW,
        items=(item,),
        source_refs=("canonical:runtime-1",),
        committee_policy_version=policy.policy_version,
        prompt_template_id=policy.prompt_template_id,
        prompt_version=policy.prompt_version,
        instrument_id="BTC/USD",
    )
    return CommitteeCase(
        case_id="case-runtime-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=NOW,
        provenance=Provenance(
            producing_component="tests.shadow-cycle-runtime",
            artifact_or_build_id="build-1",
            process_instance_id="test-1",
            emitted_at=NOW,
            source_record_refs=("canonical:runtime-1",),
        ),
        instrument_id="BTC/USD",
    )


def _settings() -> CommitteeShadowSettings:
    return CommitteeShadowSettings(
        opip_committee_mode=COMMITTEE_MODE_SHADOW,
        opip_committee_max_estimated_cost_microunits=500_000,
    )


def test_sealed_case_path_executes_identity_matched_case_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    calls: list[str] = []
    monkeypatch.setattr(
        cycle_runner,
        "load_case_envelopes",
        lambda path: (case,),
    )

    def execute(case_arg, **kwargs):
        calls.append(case_arg.case_hash)
        return object()

    monkeypatch.setattr(cycle_runner, "execute_shadow_case", execute)
    case_path = tmp_path / "read-only" / cycle_runner.CASE_INPUTS_FILE
    outcome = cycle_runner.run_once(
        release_sha=SHA,
        committee_home=tmp_path / "committee",
        evidence_path=tmp_path / "legacy.jsonl",
        execution=cycle_runner.ShadowCycleExecution(case_input_path=case_path),
        settings=_settings(),
        budget=SchedulerBudget(max_committee_cases=1, max_cost_microunits=500_000),
        now=NOW,
    )
    assert calls == [case.case_hash]
    assert outcome.dispositions[CommitteeScheduleDisposition.COMPLETED.value] == 1


def test_restart_checkpoint_prevents_second_external_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    calls = 0
    monkeypatch.setattr(cycle_runner, "load_case_envelopes", lambda path: (case,))

    def execute(case_arg, **kwargs):
        nonlocal calls
        calls += 1
        return object()

    monkeypatch.setattr(cycle_runner, "execute_shadow_case", execute)
    arguments = {
        "release_sha": SHA,
        "committee_home": tmp_path / "committee",
        "evidence_path": tmp_path / "legacy.jsonl",
        "execution": cycle_runner.ShadowCycleExecution(
            case_input_path=tmp_path / "read-only" / cycle_runner.CASE_INPUTS_FILE
        ),
        "settings": _settings(),
        "budget": SchedulerBudget(
            max_committee_cases=1,
            max_cost_microunits=500_000,
        ),
        "now": NOW,
    }
    first = cycle_runner.run_once(**arguments)
    second = cycle_runner.run_once(**arguments)
    assert calls == 1
    assert first.dispositions[CommitteeScheduleDisposition.COMPLETED.value] == 1
    assert second.dispositions[CommitteeScheduleDisposition.COMPLETED.value] == 1


def test_off_mode_never_invokes_the_case_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    monkeypatch.setattr(cycle_runner, "load_case_envelopes", lambda path: (case,))
    monkeypatch.setattr(
        cycle_runner,
        "execute_shadow_case",
        lambda *args, **kwargs: pytest.fail("OFF mode must not execute a case"),
    )
    outcome = cycle_runner.run_once(
        release_sha=SHA,
        committee_home=tmp_path / "committee",
        evidence_path=tmp_path / "legacy.jsonl",
        execution=cycle_runner.ShadowCycleExecution(
            case_input_path=tmp_path / "sealed.jsonl"
        ),
        settings=CommitteeShadowSettings(),
        now=NOW,
    )
    assert outcome.ran is False
    assert outcome.reason == "MODE_DISABLED"


def test_invalid_sealed_input_is_a_configuration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cycle_runner,
        "load_case_envelopes",
        lambda path: (_ for _ in ()).throw(ShadowCaseEnvelopeError("identity mismatch")),
    )
    with pytest.raises(
        cycle_runner.CycleConfigurationError,
        match="sealed case input is invalid",
    ):
        cycle_runner.run_once(
            release_sha=SHA,
            committee_home=tmp_path / "committee",
            evidence_path=tmp_path / "legacy.jsonl",
            execution=cycle_runner.ShadowCycleExecution(
            case_input_path=tmp_path / "sealed.jsonl"
        ),
            settings=_settings(),
            now=NOW,
        )


def test_environment_settings_propagate_shadow_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPIP_COMMITTEE_MODE", "shadow")
    monkeypatch.setenv("OPIP_COMMITTEE_MAX_ESTIMATED_COST_MICROUNITS", "500000")
    settings = cycle_runner._runtime_settings_from_environment()
    assert settings.opip_committee_mode == "shadow"
    assert settings.opip_committee_max_estimated_cost_microunits == 500_000


@pytest.mark.parametrize("value", ["not-an-int", "-1"])
def test_invalid_environment_cost_fails_closed(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPIP_COMMITTEE_MAX_ESTIMATED_COST_MICROUNITS", value)
    with pytest.raises(cycle_runner.CycleConfigurationError):
        cycle_runner._runtime_settings_from_environment()


def test_deployed_shell_uses_read_only_case_input_and_rejects_unknown_mode() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "committee"
        / "run-committee-shadow-cycle.sh"
    ).read_text(encoding="utf-8")
    assert 'EVIDENCE_ROOT="${OPIP_COMMITTEE_EVIDENCE_ROOT:-/var/lib/opip-learning}"' in script
    assert 'CASE_INPUTS="${OPIP_COMMITTEE_CASE_INPUTS:-${EVIDENCE_ROOT}/committee_case_inputs.jsonl}"' in script
    assert '--case-inputs "${CASE_INPUTS}"' in script
    assert 'if [[ "${MODE}" != "shadow" ]]' in script
