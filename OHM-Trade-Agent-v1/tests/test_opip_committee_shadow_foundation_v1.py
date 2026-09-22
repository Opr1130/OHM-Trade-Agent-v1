"""Intelligence Committee shadow foundation (increment 2A).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests prove the committee foundation's core invariants: a sealed
point-in-time evidence snapshot, independent seats that never see each other's
answers, validated structured opinions, idempotent retries, fail-closed
secret screening, and honest partial-committee semantics where a failure is
not a vote and an abstention is not a negative vote.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteeCaseOutcome,
    CommitteePolicy,
    CostCompleteness,
    DirectionalAssessment,
    EvidenceSufficiency,
    EvaluationPhase,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFailureClass,
    ProviderFamily,
    ReproducibilityClass,
    ResearchAction,
    StructuredOpinion,
    logical_observation_id,
)
from app.opip.committee.evidence import (
    EvidencePolicyError,
    build_evidence_item,
    build_evidence_snapshot,
)
from app.opip.committee.fakes import (
    RecordingTransport,
    ScriptedAnswer,
    ScriptedCommitteeProvider,
    opinion_json,
)
from app.opip.committee.ledger import InMemoryObservationLedger
from app.opip.committee.opinion import OpinionParseError, parse_structured_opinion
from app.opip.committee.outbound import OutboundPolicyError, screen_model_bound_view
from app.opip.committee.providers import (
    ProviderAvailability,
    ProviderInvocationError,
    TransportBackedProvider,
    UnavailableProvider,
    resolve_seated_providers,
)
from app.opip.committee.runtime import (
    CommitteePolicyViolation,
    CommitteeReplayDivergenceError,
    CommitteeRunner,
)
from app.opip.committee.serialization import (
    CommitteeSerializationError,
    call_outcome_from_dict,
    call_outcome_to_dict,
    case_outcome_from_dict,
    case_outcome_to_dict,
)
from app.opip.committee.store import (
    REASON_DUPLICATE,
    REASON_DIVERGENCE,
    REASON_STORED,
    CommitteeEvidenceStore,
    DurableObservationLedger,
)
from app.opip.committee.settings import CommitteeShadowSettings
from app.opip.decision_intelligence.identity import Provenance

#: The committee ships dark, so this file states enablement explicitly rather
#: than depending on ambient process settings.
SHADOW_SETTINGS = CommitteeShadowSettings()

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(minutes=5)
POLICY_VERSION = "committee-policy-v1"


def _provenance() -> Provenance:
    return Provenance(
        producing_component="tests.committee",
        artifact_or_build_id="test-build",
        process_instance_id="test-process",
        emitted_at=NOW,
        source_record_refs=("src-1",),
    )


def _item(evidence_id: str, *, minutes_before: int = 10, payload=None):
    return build_evidence_item(
        evidence_id=evidence_id,
        source_id="market-observation",
        available_at=CUTOFF - timedelta(minutes=minutes_before),
        payload=payload or {"metric_name": "close", "metric_value": "100"},
        evidence_cutoff_at=CUTOFF,
    )


def _snapshot(*, evidence_ids=("E1", "E2"), policy_version: str = POLICY_VERSION):
    return build_evidence_snapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CUTOFF,
        assembled_at=NOW,
        items=tuple(_item(eid) for eid in evidence_ids),
        source_refs=("snapshot-ref-1",),
        committee_policy_version=policy_version,
        prompt_template_id="committee.opinion.v1",
        prompt_version="3",
        instrument_id="INSTR:kraken:SOL:USD:1",
    )


def _policy(*, families=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
            max_attempts: int = 1, cost_ceiling: int | None = None) -> CommitteePolicy:
    return CommitteePolicy(
        policy_version=POLICY_VERSION,
        seated_providers=tuple(families),
        prompt_template_id="committee.opinion.v1",
        prompt_version="3",
        max_attempts_per_seat=max_attempts,
        max_estimated_cost_microunits=cost_ceiling,
    )


def _case(snapshot=None, policy=None) -> CommitteeCase:
    return CommitteeCase(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot or _snapshot(),
        policy=policy or _policy(),
        created_at=NOW,
        provenance=_provenance(),
        instrument_id="INSTR:kraken:SOL:USD:1",
    )


def _provider(family: ProviderFamily, *, answers, model: str = "model-a", **kwargs):
    return ScriptedCommitteeProvider(
        family=family, model=model, answers=answers, **kwargs
    )


def _ok(family: ProviderFamily = ProviderFamily.OPENAI, model: str = "model-a", **kwargs):
    return _provider(
        family,
        model=model,
        answers=(ScriptedAnswer(text=opinion_json(**kwargs)),),
    )


# ---------------------------------------------------------------- evidence


def test_evidence_available_after_cutoff_is_rejected():
    available_late = CUTOFF + timedelta(seconds=1)
    with pytest.raises(EvidencePolicyError):
        build_evidence_item(
            evidence_id="E-late",
            source_id="market-observation",
            available_at=available_late,
            payload={"metric_value": "100"},
            evidence_cutoff_at=CUTOFF,
        )


def test_snapshot_rejects_duplicate_evidence_ids():
    duplicate = _item("E1")
    with pytest.raises(EvidencePolicyError):
        build_evidence_snapshot(
            case_id="case-1",
            case_type=CaseType.MARKET_OPPORTUNITY,
            evidence_cutoff_at=CUTOFF,
            assembled_at=NOW,
            items=(duplicate, duplicate),
            source_refs=("ref",),
            committee_policy_version=POLICY_VERSION,
            prompt_template_id="committee.opinion.v1",
            prompt_version="3",
        )


def test_snapshot_identity_is_content_derived_and_tamper_evident():
    first = _snapshot()
    assert first.snapshot_hash == _snapshot().snapshot_hash
    assert first.snapshot_id != first.snapshot_hash
    changed = _snapshot(evidence_ids=("E1",))
    assert changed.snapshot_hash != first.snapshot_hash


def test_binary_floats_are_rejected_from_evidence_identity():
    with pytest.raises(EvidencePolicyError):
        build_evidence_item(
            evidence_id="E-float",
            source_id="market-observation",
            available_at=CUTOFF,
            payload={"metric_value": 1.5},
            evidence_cutoff_at=CUTOFF,
        )


def test_case_requires_snapshot_policy_alignment():
    mismatched = _snapshot(policy_version="other-policy")
    policy = _policy()
    provenance = _provenance()
    with pytest.raises(ValueError):
        CommitteeCase(
            case_id="case-1",
            case_type=CaseType.MARKET_OPPORTUNITY,
            snapshot=mismatched,
            policy=policy,
            created_at=NOW,
            provenance=provenance,
            instrument_id="INSTR:kraken:SOL:USD:1",
        )


# -------------------------------------------------------------- outbound


def test_outbound_screen_rejects_prohibited_keys():
    for key in ("api_key", "bot_token", "db_password", "private_key", "secret"):
        with pytest.raises(OutboundPolicyError):
            screen_model_bound_view({"evidence": [{key: "value"}]})


def test_outbound_screen_rejects_environment_dumps():
    with pytest.raises(OutboundPolicyError):
        screen_model_bound_view({"env": {"PATH": "/usr/bin"}})


def test_outbound_screen_rejects_credential_formed_values():
    # Credential-shaped fixtures are assembled from fragments so that no live
    # credential pattern ever appears as a literal in source. That keeps the
    # repository's secret scanner meaningful instead of teaching it to ignore
    # test files.
    filler = "A" * 32
    credential_formed_values = (
        "sk-" + filler,
        "ghp_" + "0" * 36,
        "Bearer " + filler,
        "-----BEGIN " + "PRIVATE KEY" + "-----",
    )
    for value in credential_formed_values:
        with pytest.raises(OutboundPolicyError):
            screen_model_bound_view({"note": value})


def test_outbound_screen_allows_ordinary_evidence():
    screened = screen_model_bound_view(
        {"evidence": [{"evidence_id": "E1", "metric_name": "close", "metric_value": "100"}]}
    )
    assert screened["evidence"][0]["metric_value"] == "100"


def test_model_bound_view_of_a_secret_bearing_item_fails_closed():
    secret_shaped = "sk-" + "L" * 28
    secret_bearing = build_evidence_snapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CUTOFF,
        assembled_at=NOW,
        items=(
            _item(
                "E1",
                payload={"metric_name": "close", "note": secret_shaped},
            ),
        ),
        source_refs=("ref",),
        committee_policy_version=POLICY_VERSION,
        prompt_template_id="committee.opinion.v1",
        prompt_version="3",
    )
    model_bound = secret_bearing.model_bound_view()
    with pytest.raises(OutboundPolicyError):
        screen_model_bound_view(model_bound)


# ------------------------------------------------------------ opinion parse


def _parse(text: str, *, allowed=("E1", "E2")):
    return parse_structured_opinion(
        raw_text=text,
        case_id="case-1",
        provider="openai",
        model="model-a",
        allowed_evidence_refs=allowed,
    )


def _parse_error(text: str) -> OpinionParseError:
    """Return the specific parse error raised for ``text``.

    A single invocation inside the block plus the concrete error type keeps the
    test from passing for an unrelated reason.
    """
    with pytest.raises(OpinionParseError) as excinfo:
        _parse(text)
    return excinfo.value


def test_valid_opinion_parses():
    opinion = _parse(
        opinion_json(
            lists={"supporting_evidence_refs": ("E1",)}, confidence=70
        )
    )
    assert opinion.assessment is DirectionalAssessment.SUPPORTIVE
    assert opinion.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
    assert opinion.confidence == 70
    assert opinion.abstained is False
    assert opinion.opinion_id != opinion.opinion_hash


def test_non_json_response_is_malformed():
    assert _parse_error("I think this looks bullish, trust me.").failure_class is (
        ProviderFailureClass.MALFORMED_RESPONSE
    )


def test_markdown_fenced_response_is_rejected_not_repaired():
    fenced = f"```json\n{opinion_json()}\n```"
    assert _parse_error(fenced).failure_class is (
        ProviderFailureClass.MALFORMED_RESPONSE
    )


def test_undeclared_opinion_field_is_a_schema_failure():
    undeclared = opinion_json(overrides={"trade_command": "buy"})
    assert _parse_error(undeclared).failure_class is (
        ProviderFailureClass.SCHEMA_VALIDATION_FAILURE
    )


def test_confidence_outside_legal_bounds_is_rejected():
    for value in (-1, 101):
        out_of_range = opinion_json(overrides={"confidence": value})
        assert _parse_error(out_of_range).failure_class is (
            ProviderFailureClass.SCHEMA_VALIDATION_FAILURE
        )


def test_hallucinated_evidence_reference_is_rejected():
    hallucinated = opinion_json(
        lists={"supporting_evidence_refs": ("E-not-in-snapshot",)}
    )
    assert _parse_error(hallucinated).failure_class is (
        ProviderFailureClass.SCHEMA_VALIDATION_FAILURE
    )


def test_abstention_rejects_sufficient_evidence_and_confidence():
    sufficient = opinion_json(
        abstention_reason="evidence too thin",
        evidence_sufficiency="SUFFICIENT",
        confidence=None,
    )
    assert _parse_error(sufficient).failure_class is (
        ProviderFailureClass.SCHEMA_VALIDATION_FAILURE
    )

    confident = opinion_json(
        abstention_reason="evidence too thin",
        evidence_sufficiency="INSUFFICIENT",
        confidence=40,
    )
    assert _parse_error(confident).failure_class is (
        ProviderFailureClass.SCHEMA_VALIDATION_FAILURE
    )


# ----------------------------------------------------------- provider layer


def test_missing_adapter_yields_an_explicitly_unavailable_seat():
    resolved = resolve_seated_providers(
        policy_families=(ProviderFamily.OPENAI, ProviderFamily.GOOGLE_GEMINI),
        providers={ProviderFamily.OPENAI: _ok()},
    )
    assert isinstance(resolved[ProviderFamily.GOOGLE_GEMINI], UnavailableProvider)
    assert resolved[ProviderFamily.GOOGLE_GEMINI].availability() is (
        ProviderAvailability.UNAVAILABLE
    )


def test_adapter_for_a_different_family_is_refused():
    misplaced = _ok(ProviderFamily.OPENAI)
    with pytest.raises(ValueError):
        resolve_seated_providers(
            policy_families=(ProviderFamily.ANTHROPIC,),
            providers={ProviderFamily.ANTHROPIC: misplaced},
        )


def test_transport_backed_provider_classifies_unknown_failure():
    transport = RecordingTransport(
        reported_provider="openai", reported_model="m", error=RuntimeError("boom")
    )
    provider = TransportBackedProvider(
        family=ProviderFamily.OPENAI, model="m", transport=transport
    )
    probe = _wire_probe()
    with pytest.raises(ProviderInvocationError) as excinfo:
        provider.invoke(probe)
    assert excinfo.value.failure_class is ProviderFailureClass.INTERNAL_ERROR


def _wire_probe():
    from app.opip.committee.providers import ProviderWireRequest

    return ProviderWireRequest(
        case_id="case-1",
        logical_observation_id="logical-1",
        model="m",
        system_prompt="prompt",
        user_payload={"evidence": []},
        max_output_tokens=100,
        timeout_seconds=10,
    )


# ------------------------------------------------------------------ runner


def test_seats_receive_identical_evidence_and_never_each_other_answers():
    openai = _ok(ProviderFamily.OPENAI, hypothesis="openai-only-hypothesis-text")
    anthropic = _ok(
        ProviderFamily.ANTHROPIC,
        model="model-b",
        hypothesis="anthropic-only-hypothesis-text",
    )
    runner = CommitteeRunner(
        providers={
            ProviderFamily.OPENAI: openai,
            ProviderFamily.ANTHROPIC: anthropic,
        },
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case())

    assert result.answered_count == 2
    assert len(openai.calls) == 1
    assert len(anthropic.calls) == 1
    assert openai.calls[0].user_payload == anthropic.calls[0].user_payload

    for provider in (openai, anthropic):
        payload_text = json.dumps(provider.calls[0].user_payload, sort_keys=True)
        assert "hypothesis" not in payload_text
        assert "openai-only-hypothesis-text" not in payload_text
        assert "anthropic-only-hypothesis-text" not in payload_text


def test_complete_committee_seals_one_opinion_per_seat():
    runner = CommitteeRunner(
        providers={
            ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI),
            ProviderFamily.ANTHROPIC: _ok(ProviderFamily.ANTHROPIC, model="model-b"),
        },
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case())

    assert result.answered_count == 2
    assert result.failed_count == 0
    assert result.completeness_basis_points == 10_000
    assert len(result.case_outcome.sealed_opinions) == 2
    assert result.phase is EvaluationPhase.PROSPECTIVE


def test_repeating_a_case_does_not_create_a_second_opinion():
    openai = _ok(ProviderFamily.OPENAI)
    ledger = InMemoryObservationLedger()
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: openai},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    policy = _policy(families=(ProviderFamily.OPENAI,))
    first = runner.run_case(_case(policy=policy))
    second = runner.run_case(_case(policy=policy))

    assert len(openai.calls) == 1, "a committed observation must not be re-queried"
    assert first.seats[0].outcome.status is ObservationStatus.COMPLETED
    assert second.seats[0].outcome.status is ObservationStatus.DUPLICATE_OK
    assert (
        second.seats[0].outcome.opinion.opinion_hash
        == first.seats[0].outcome.opinion.opinion_hash
    )


def test_replay_divergence_fails_explicitly_and_preserves_history():
    first = _ok(ProviderFamily.OPENAI, hypothesis="first sealed hypothesis")
    ledger = InMemoryObservationLedger()
    policy = _policy(families=(ProviderFamily.OPENAI,))
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: first},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    runner.run_case(_case(policy=policy))

    diverging = _ok(ProviderFamily.OPENAI, hypothesis="a different hypothesis now")
    replayer = CommitteeRunner(
        providers={ProviderFamily.OPENAI: diverging},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    replay_case = _case(policy=policy)
    with pytest.raises(CommitteeReplayDivergenceError):
        replayer.run_case(replay_case, replay_existing=True)

    preserved = ledger.committed_opinion(
        logical_observation_id(
            case_id="case-1",
            provider_family=ProviderFamily.OPENAI,
            requested_model="model-a",
            prompt_version="3",
            committee_policy_version=POLICY_VERSION,
            evidence_snapshot_hash=_snapshot().snapshot_hash,
        )
    )
    assert preserved is not None
    assert "first sealed hypothesis" in preserved.opinion.hypothesis


def test_retryable_failure_is_retried_within_bounds():
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(
            ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),
            ScriptedAnswer(text=opinion_json()),
        ),
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(
        families=(ProviderFamily.OPENAI,), max_attempts=2
    )))

    assert len(provider.calls) == 2
    assert result.seats[0].outcome.status is ObservationStatus.COMPLETED
    assert result.seats[0].outcome.attempt == 2


def test_non_retryable_failure_is_not_retried():
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(
            ScriptedAnswer(
                failure_class=ProviderFailureClass.AUTH_FAILURE,
                failure_message="bad credential",
            ),
        ),
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(
        families=(ProviderFamily.OPENAI,), max_attempts=3
    )))

    assert len(provider.calls) == 1
    assert result.seats[0].outcome.status is ObservationStatus.FAILED
    assert (
        result.seats[0].outcome.failure_class is ProviderFailureClass.AUTH_FAILURE
    )


def test_retries_never_exceed_the_policy_bound():
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    runner.run_case(_case(policy=_policy(
        families=(ProviderFamily.OPENAI,), max_attempts=2
    )))
    assert len(provider.calls) == 2


def test_response_from_the_wrong_provider_is_rejected_not_attributed():
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(
            ScriptedAnswer(
                text=opinion_json(), reported_provider=ProviderFamily.DEEPSEEK.value
            ),
        ),
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))

    outcome = result.seats[0].outcome
    assert outcome.status is ObservationStatus.INVALID
    assert outcome.failure_class is ProviderFailureClass.PROVIDER_IDENTITY_MISMATCH
    assert outcome.opinion is None


def test_response_from_the_wrong_model_is_rejected_not_attributed():
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(ScriptedAnswer(text=opinion_json(), reported_model="some-other-model"),),
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))
    assert result.seats[0].outcome.status is ObservationStatus.INVALID


def test_spoofed_model_identity_is_recorded_verbatim():
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(ScriptedAnswer(text=opinion_json(), reported_model="gpt-5.6-terra"),),
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(policy=_policy(families=(ProviderFamily.OPENAI,)))
    )
    outcome = result.seats[0].outcome
    assert outcome.requested_model == "model-a"
    assert outcome.reported_model == "gpt-5.6-terra"


def test_partial_committee_preserves_failure_as_a_failure_not_a_vote():
    openai = _ok(ProviderFamily.OPENAI)
    gemini = _provider(
        ProviderFamily.GOOGLE_GEMINI,
        model="model-c",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.RATE_LIMIT),),
    )
    runner = CommitteeRunner(
        providers={
            ProviderFamily.OPENAI: openai,
            ProviderFamily.GOOGLE_GEMINI: gemini,
        },
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(policy=_policy(families=(ProviderFamily.OPENAI, ProviderFamily.GOOGLE_GEMINI)))
    )

    assert result.answered_count == 1
    assert result.failed_count == 1
    assert result.completeness_basis_points == 5_000
    assert len(result.case_outcome.sealed_opinions) == 1
    assert len(result.case_outcome.failed_seats) == 1


def test_seat_with_no_supported_integration_is_unavailable():
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok()},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(
            policy=_policy(
                families=(ProviderFamily.OPENAI, ProviderFamily.INDEPENDENT_REVIEWER)
            )
        )
    )
    independent = [
        seat
        for seat in result.seats
        if seat.provider_family is ProviderFamily.INDEPENDENT_REVIEWER
    ]
    assert len(independent) == 1
    assert independent[0].outcome.status is ObservationStatus.UNAVAILABLE
    assert independent[0].outcome.opinion is None
    assert result.completeness_basis_points == 5_000


def test_budget_ceiling_skips_a_seat_instead_of_spending():
    provider = _ok(ProviderFamily.OPENAI)
    provider._estimated_cost_microunits = 10_000_000  # noqa: SLF001 - test double
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(
            policy=_policy(
                families=(ProviderFamily.OPENAI,), cost_ceiling=1_000
            )
        )
    )
    assert result.seats[0].outcome.status is ObservationStatus.SKIPPED_BUDGET
    assert provider.calls == []


def test_seat_failure_does_not_raise_out_of_the_runner():
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(
            ScriptedAnswer(
                failure_class=ProviderFailureClass.MALFORMED_RESPONSE,
                failure_message="unusable",
            ),
        ),
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))
    outcome = result.seats[0].outcome
    assert outcome.status is ObservationStatus.FAILED
    assert outcome.failure_class is ProviderFailureClass.MALFORMED_RESPONSE
    assert outcome.opinion is None


def test_received_but_unusable_body_is_invalid_not_a_silent_success():
    transport = RecordingTransport(
        reported_provider="openai",
        reported_model="model-a",
        text="this is not JSON at all",
    )
    provider = TransportBackedProvider(
        family=ProviderFamily.OPENAI, model="model-a", transport=transport
    )
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))
    outcome = result.seats[0].outcome
    assert outcome.status is ObservationStatus.INVALID
    assert outcome.failure_class is ProviderFailureClass.MALFORMED_RESPONSE
    assert outcome.opinion is None


def test_reproducibility_is_declared_honestly():
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI)},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))
    assert result.seats[0].outcome.reproducibility is (
        ReproducibilityClass.NONDETERMINISTIC_PROVIDER_OUTPUT
    )
    assert result.seats[0].outcome.input_hash.startswith("COMMITTEE-WIRE:")


def test_logical_observation_identity_is_stable_and_keyed_by_all_axes():
    base = dict(
        case_id="case-1",
        provider_family=ProviderFamily.OPENAI,
        requested_model="model-a",
        prompt_version="3",
        committee_policy_version=POLICY_VERSION,
        evidence_snapshot_hash="COMMITTEE-EVIDENCE:abc",
    )
    identity = logical_observation_id(**base)
    assert identity.startswith("COMMITTEE-LOGICAL:")
    # Stability: identical axes always produce the identical logical identity.
    assert logical_observation_id(**base) == identity
    for axis, value in (
        ("case_id", "case-2"),
        ("provider_family", ProviderFamily.ANTHROPIC),
        ("requested_model", "model-z"),
        ("prompt_version", "4"),
        ("committee_policy_version", "committee-policy-v2"),
        ("evidence_snapshot_hash", "COMMITTEE-EVIDENCE:def"),
    ):
        changed = dict(base)
        changed[axis] = value
        assert logical_observation_id(**changed) != logical_observation_id(**base)


# ------------------------------------------------------------------ store


def test_store_round_trips_case_and_call_evidence(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI)},
        ledger=DurableObservationLedger(store=store),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))
    assert store.append_case_outcome(result.case_outcome).reason == REASON_STORED

    reloaded = list(store.iter_case_outcomes())
    assert len(reloaded) == 1
    assert reloaded[0].case_outcome_id == result.case_outcome.case_outcome_id
    assert reloaded[0].outcomes[0].opinion.opinion_id == (
        result.case_outcome.outcomes[0].opinion.opinion_id
    )


def test_store_acknowledges_an_identical_logical_observation(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    ledger = DurableObservationLedger(store=store)
    provider = _ok(ProviderFamily.OPENAI)
    policy = _policy(families=(ProviderFamily.OPENAI,))
    CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    ).run_case(_case(policy=policy))
    outcome = list(store.iter_call_outcomes())[0]

    assert store.append_call_outcome(outcome).reason == REASON_DUPLICATE
    assert len(list(store.iter_call_outcomes())) == 1


def test_store_refuses_to_overwrite_a_committed_opinion_with_different_content(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    ledger = DurableObservationLedger(store=store)
    policy = _policy(families=(ProviderFamily.OPENAI,))
    CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI, hypothesis="original")},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    ).run_case(_case(policy=policy))
    committed = list(store.iter_call_outcomes())[0]

    # A second, internally consistent outcome for the same logical seat but with
    # different content — the shape a diverging replay or a tampering writer
    # would produce.
    replacement = ProviderCallOutcome(
        logical_observation_id=committed.logical_observation_id,
        case_id=committed.case_id,
        provider_family=committed.provider_family,
        requested_model=committed.requested_model,
        status=ObservationStatus.COMPLETED,
        attempt=committed.attempt,
        reproducibility=committed.reproducibility,
        request_at=committed.request_at,
        input_hash=committed.input_hash,
        reported_provider=committed.reported_provider,
        reported_model=committed.reported_model,
        opinion=StructuredOpinion(
            case_id=committed.case_id,
            provider=committed.reported_provider,
            model=committed.reported_model,
            evidence_sufficiency=committed.opinion.evidence_sufficiency,
            assessment=committed.opinion.assessment,
            hypothesis="rewritten history",
            recommended_research_action=committed.opinion.recommended_research_action,
        ),
        response_at=committed.response_at,
    )
    assert replacement.opinion.opinion_hash != committed.opinion.opinion_hash

    result = store.append_call_outcome(replacement)
    assert result.stored is False
    assert result.reason == REASON_DIVERGENCE
    assert len(list(store.iter_call_outcomes())) == 1
    assert "original" in list(store.iter_call_outcomes())[0].opinion.hypothesis


def test_durable_ledger_survives_a_new_ledger_instance(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    provider = _ok(ProviderFamily.OPENAI)
    policy = _policy(families=(ProviderFamily.OPENAI,))
    CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=DurableObservationLedger(store=store),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    ).run_case(_case(policy=policy))

    # A fresh process would build a fresh ledger over the same durable store.
    second = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=DurableObservationLedger(store=store),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = second.run_case(_case(policy=policy))
    assert len(provider.calls) == 1
    assert result.seats[0].outcome.status is ObservationStatus.DUPLICATE_OK


def test_persisted_identity_mismatch_is_rejected_on_read(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI)},
        ledger=DurableObservationLedger(store=store),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))
    committed = list(store.iter_call_outcomes())[0]

    row = call_outcome_to_dict(committed)
    row["outcome_id"] = "COMMITTEE-CALL:forged"
    with pytest.raises(CommitteeSerializationError):
        call_outcome_from_dict(row)


def test_case_outcome_serialization_round_trip():
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI)},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    outcome = runner.run_case(
        _case(policy=_policy(families=(ProviderFamily.OPENAI,)))
    ).case_outcome
    restored = case_outcome_from_dict(case_outcome_to_dict(outcome))
    assert restored.case_outcome_id == outcome.case_outcome_id


# ------------------------------------------- review findings (fail-closed)


def test_declared_cost_ceiling_skips_a_seat_whose_cost_cannot_be_bounded():
    """A declared ceiling must not be bypassable by an unknown cost estimate."""
    provider = _ok(ProviderFamily.OPENAI)
    provider._estimated_cost_microunits = None  # noqa: SLF001 - test double
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(policy=_policy(families=(ProviderFamily.OPENAI,), cost_ceiling=1_000))
    )
    outcome = result.seats[0].outcome
    assert outcome.status is ObservationStatus.SKIPPED_BUDGET
    assert provider.calls == []
    assert "cannot be bounded" in outcome.detail


def test_no_declared_ceiling_still_allows_an_unknown_cost():
    """Without a ceiling there is nothing to enforce, so the seat runs."""
    provider = _ok(ProviderFamily.OPENAI)
    provider._estimated_cost_microunits = None  # noqa: SLF001 - test double
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(_case(policy=_policy(families=(ProviderFamily.OPENAI,))))
    assert result.seats[0].outcome.status is ObservationStatus.COMPLETED


def test_every_retry_attempt_is_recorded_not_only_the_last():
    """The audit trail keeps each attempt, so attempt_count reflects reality."""
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(
            ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),
            ScriptedAnswer(text=opinion_json()),
        ),
    )
    ledger = InMemoryObservationLedger()
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(policy=_policy(families=(ProviderFamily.OPENAI,), max_attempts=2))
    )
    logical_id = result.seats[0].logical_observation_id
    # The failed first attempt is durably recorded, so two attempts are visible.
    assert ledger.attempt_count(logical_id) == 2
    assert result.seats[0].outcome.attempt == 2


def test_a_lost_index_is_rebuilt_so_a_redelivery_is_still_a_duplicate(tmp_path):
    """Losing the index must not let the same opinion be appended twice."""
    store = CommitteeEvidenceStore(root=tmp_path)
    ledger = DurableObservationLedger(store=store)
    policy = _policy(families=(ProviderFamily.OPENAI,))
    CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI)},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    ).run_case(_case(policy=policy))
    committed = list(store.iter_call_outcomes())[0]
    assert len(list(store.iter_call_outcomes())) == 1

    # Simulate an index lost after a failed update.
    store.calls_index_file.unlink()

    result = store.append_call_outcome(committed)
    assert result.stored is False
    assert result.reason == REASON_DUPLICATE
    assert len(list(store.iter_call_outcomes())) == 1


# ------------------------------- review findings (durable evidence integrity)


def test_a_rejected_divergent_ledger_append_is_propagated_not_swallowed(tmp_path):
    """A refused opinion must not be published as durable evidence."""
    store = CommitteeEvidenceStore(root=tmp_path)
    policy = _policy(families=(ProviderFamily.OPENAI,))
    CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI, hypothesis="sealed")},
        ledger=DurableObservationLedger(store=store),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    ).run_case(_case(policy=policy))
    committed = list(store.iter_call_outcomes())[0]

    # A second runner re-derives the same logical seat but produces a different
    # opinion (a race, or a provider that changed its answer). The append is
    # refused, and the refusal must reach the caller.
    divergent_opinion = StructuredOpinion(
        case_id=committed.case_id,
        provider=committed.reported_provider,
        model=committed.reported_model,
        evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
        assessment=DirectionalAssessment.OPPOSING,
        hypothesis="a different hypothesis",
        recommended_research_action=ResearchAction.NO_ACTION,
    )
    divergent = ProviderCallOutcome(
        logical_observation_id=committed.logical_observation_id,
        case_id=committed.case_id,
        provider_family=committed.provider_family,
        requested_model=committed.requested_model,
        status=ObservationStatus.COMPLETED,
        attempt=1,
        reproducibility=ReproducibilityClass.NONDETERMINISTIC_PROVIDER_OUTPUT,
        request_at=NOW,
        input_hash=committed.input_hash,
        reported_provider=committed.reported_provider,
        reported_model=committed.reported_model,
        opinion=divergent_opinion,
        response_at=NOW,
    )
    ledger = DurableObservationLedger(store=store)
    with pytest.raises(CommitteeReplayDivergenceError):
        ledger.record(divergent)

    # History is unchanged: exactly one committed observation survives.
    survivors = list(store.iter_call_outcomes())
    assert len(survivors) == 1
    assert survivors[0].opinion.opinion_hash == committed.opinion.opinion_hash


def test_a_partially_stale_call_index_is_reconciled_from_the_log(tmp_path):
    """A non-empty but stale sidecar must not let a redelivery append again."""
    store = CommitteeEvidenceStore(root=tmp_path)
    first = _ok(ProviderFamily.OPENAI)
    policy = _policy(families=(ProviderFamily.OPENAI,))
    CommitteeRunner(
        providers={ProviderFamily.OPENAI: first},
        ledger=DurableObservationLedger(store=store),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    ).run_case(_case(policy=policy))
    committed = list(store.iter_call_outcomes())[0]

    # A sidecar that is present and non-empty but missing this logical id.
    store.calls_index_file.write_text(
        '{"schema_version":1,"kind":"CALL_OUTCOME","entries":'
        '{"COMMITTEE-LOGICAL:other":{"outcome_id":"COMMITTEE-CALL:other",'
        '"opinion_hash":"COMMITTEE-OPINION:other"}}}',
        encoding="utf-8",
    )

    result = store.append_call_outcome(committed)
    assert result.stored is False
    assert result.reason == REASON_DUPLICATE
    assert len(list(store.iter_call_outcomes())) == 1

# ==================== review findings: archive + runtime integrity


def _authenticated_snapshot():
    from app.opip.committee.evidence import build_evidence_item, build_evidence_snapshot

    return build_evidence_snapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CUTOFF,
        assembled_at=NOW,
        items=(
            build_evidence_item(
                evidence_id="E1",
                source_id="market-observation",
                available_at=CUTOFF - timedelta(minutes=10),
                payload={"metric_name": "close", "metric_value": "100"},
                evidence_cutoff_at=CUTOFF,
            ),
        ),
        source_refs=("snapshot-ref-1",),
        committee_policy_version=POLICY_VERSION,
        prompt_template_id="committee.opinion.v1",
        prompt_version="3",
        instrument_id="INSTR:kraken:SOL:USD:1",
    )


def _prospective_case_outcome(snapshot):
    return CommitteeCaseOutcome(
        case_id="case-1",
        evidence_snapshot_hash=snapshot.snapshot_hash,
        committee_policy_version=POLICY_VERSION,
        phase=EvaluationPhase.PROSPECTIVE,
        started_at=CUTOFF - timedelta(minutes=5),
        completed_at=NOW,
        outcomes=(_call_outcome(),),
        provenance=_provenance(),
    )


def _call_outcome(
    *,
    family: ProviderFamily = ProviderFamily.OPENAI,
    model: str = "model-a",
) -> ProviderCallOutcome:
    """A committed call outcome, for identity and telemetry assertions."""
    return ProviderCallOutcome(
        logical_observation_id=f"COMMITTEE-LOGICAL:case-1:{family.value}:{model}",
        case_id="case-1",
        provider_family=family,
        requested_model=model,
        status=ObservationStatus.COMPLETED,
        attempt=1,
        reproducibility=ReproducibilityClass.NONDETERMINISTIC_PROVIDER_OUTPUT,
        request_at=NOW,
        input_hash=f"COMMITTEE-WIRE:case-1:{family.value}",
        reported_provider=family.value,
        reported_model=model,
        opinion=StructuredOpinion(
            case_id="case-1",
            provider=family.value,
            model=model,
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
            assessment=DirectionalAssessment.SUPPORTIVE,
            hypothesis="probe hypothesis",
            recommended_research_action=ResearchAction.NO_ACTION,
        ),
        response_at=NOW,
        latency_micros=500_000,
        input_tokens=100,
        output_tokens=40,
        estimated_cost_microunits=1_000,
        cost_completeness=CostCompleteness.COMPLETE,
    )


def _prediction_with_snapshot():
    """A sealed prospective prediction over the authenticated snapshot."""
    from app.opip.committee.prospective import seal_prediction

    snapshot = _authenticated_snapshot()
    return seal_prediction(
        case_outcome=_prospective_case_outcome(snapshot),
        evidence_snapshot=snapshot,
        sealed_at=NOW + timedelta(seconds=30),
        experiment_id="archive-exp-1",
        provenance=_provenance(),
        case_type=CaseType.MARKET_OPPORTUNITY,
    )


def _observation_probe():
    from app.opip.committee.prospective import OutcomeFinality, OutcomeObservation

    return OutcomeObservation(
        case_id="case-1",
        outcome_source="kraken_public_ohlc",
        source_refs=("phase3c_forward_outcomes:row-1",),
        observed_at=NOW + timedelta(hours=4),
        horizon_seconds=4 * 3600,
        finality=OutcomeFinality.FINAL,
        positive=True,
        realised_return_microunits=12_500,
    )


def _evaluation_probe():
    from app.opip.committee.prospective import evaluate_prospective

    snapshot = _authenticated_snapshot()
    return evaluate_prospective(
        prediction=_prediction_with_snapshot(),
        case_outcome=_prospective_case_outcome(snapshot),
        observation=_observation_probe(),
        evaluated_at=NOW + timedelta(hours=4, minutes=5),
        provenance=_provenance(),
    )


def test_prospective_visibility_timestamp_is_dispatched_by_record_type():
    """Each prospective type has its own authoritative temporal field."""
    from app.opip.committee.prospective import (
        OutcomeObservation,
        ProspectiveEvaluation,
        SealedPrediction,
    )
    from app.opip.committee.store import _prospective_visible_at

    sealed = _prediction_with_snapshot()
    observation = _observation_probe()
    evaluation = _evaluation_probe()

    assert _prospective_visible_at(sealed) == sealed.sealed_at
    assert _prospective_visible_at(observation) == observation.observed_at
    assert _prospective_visible_at(evaluation) == evaluation.evaluated_at
    assert isinstance(sealed, SealedPrediction)
    assert isinstance(observation, OutcomeObservation)
    assert isinstance(evaluation, ProspectiveEvaluation)


def test_prospective_visibility_timestamp_fails_closed_for_unknown_type():
    """No getattr fallback: an unknown record type raises instead of guessing."""
    from app.opip.committee.store import _prospective_visible_at

    class NotAProspectiveRecord:
        observed_at = NOW

    with pytest.raises(CommitteeSerializationError):
        _prospective_visible_at(NotAProspectiveRecord())


def test_prospective_compaction_succeeds_across_all_record_types(tmp_path):
    """Rotation must not fail on a SealedPrediction, which has no observed_at."""
    store = CommitteeEvidenceStore(
        root=tmp_path,
        prospective_max_bytes=1,
        prospective_keep_lines=1,
    )
    prediction = _prediction_with_snapshot()
    observation = _observation_probe()
    evaluation = _evaluation_probe()
    assert store.append_sealed_prediction(prediction).stored is True
    assert store.append_outcome_observation(observation).stored is True
    assert store.append_prospective_evaluation(evaluation).stored is True

    # Rotation ran on every append; all three record types remain readable and
    # each was verified with its own temporal field.
    assert len(list(store.iter_sealed_predictions())) == 1
    assert len(list(store.iter_outcome_observations())) == 1
    assert len(list(store.iter_prospective_evaluations())) == 1
    assert list(tmp_path.glob("archive_prospective/*.jsonl.gz")), (
        "expected the archived prefix to exist after rotation"
    )


def test_a_replay_after_the_original_response_stays_contract_valid():
    """T2 > T1 must still yield DUPLICATE_OK without falsifying timings."""
    provider = _ok(ProviderFamily.OPENAI)
    ledger = InMemoryObservationLedger()
    policy = _policy(families=(ProviderFamily.OPENAI,))
    first_clock = [NOW]
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=ledger,
        now=lambda: first_clock[0],
        settings=SHADOW_SETTINGS,
    )
    original = runner.run_case(_case(policy=policy))
    original_outcome = original.seats[0].outcome

    # The replay happens well after the original response. The clock is advanced
    # rather than frozen, because a frozen clock is what hid this defect.
    later = NOW + timedelta(hours=6)
    replay = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=ledger,
        now=lambda: later,
        settings=SHADOW_SETTINGS,
    ).run_case(_case(policy=policy))

    acknowledged = replay.seats[0].outcome
    assert acknowledged.status is ObservationStatus.DUPLICATE_OK
    # The provider was not asked again.
    assert len(provider.calls) == 1
    # Timestamps remain contract-valid: the acknowledgement reuses the original
    # call's timing evidence instead of stamping a later synthetic request.
    assert acknowledged.response_at is not None
    assert acknowledged.response_at >= acknowledged.request_at
    assert acknowledged.request_at == original_outcome.request_at
    assert acknowledged.response_at == original_outcome.response_at
    # The original immutable observation is unchanged.
    preserved = ledger.committed_opinion(original_outcome.logical_observation_id)
    assert preserved is not None
    assert preserved.outcome_id == original_outcome.outcome_id
    assert preserved.opinion.opinion_hash == original_outcome.opinion.opinion_hash


@pytest.mark.parametrize(
    "omitted",
    [
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
        "major_assumptions",
        "risk_factors",
        "missing_evidence",
        "alternative_explanations",
    ],
)
def test_omitting_a_required_list_field_is_a_schema_failure(omitted):
    """A missing declared field must not be defaulted to an empty list."""
    from app.opip.committee.opinion import parse_structured_opinion

    payload = opinion_json(drop=(omitted,))
    with pytest.raises(OpinionParseError) as excinfo:
        parse_structured_opinion(
            raw_text=payload,
            case_id="case-1",
            provider="openai",
            model="model-a",
            allowed_evidence_refs=("E1", "E2"),
        )
    assert excinfo.value.failure_class is (
        ProviderFailureClass.SCHEMA_VALIDATION_FAILURE
    )


def test_an_explicitly_empty_list_field_is_still_valid():
    """The model may assert "none"; that is different from omitting the field."""
    payload = opinion_json(lists={"risk_factors": ()})
    opinion = _parse(payload)
    assert opinion.risk_factors == ()


def test_the_execution_api_refuses_to_run_while_the_committee_is_disabled():
    """The off/shadow switch must govern model egress, not just be advertised."""
    provider = _ok(ProviderFamily.OPENAI)
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings={"opip_committee_mode": "off"},
    )
    policy = _policy(families=(ProviderFamily.OPENAI,))
    with pytest.raises(CommitteePolicyViolation):
        runner.run_case(_case(policy=policy))
    assert provider.calls == []


def test_the_execution_api_runs_when_shadow_is_enabled():
    provider = _ok(ProviderFamily.OPENAI)
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(policy=_policy(families=(ProviderFamily.OPENAI,)))
    )
    assert result.answered_count == 1


def test_a_retry_cannot_exceed_the_declared_case_ceiling():
    """The reservation is rechecked per invocation, not once per seat."""
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    provider._estimated_cost_microunits = 60  # noqa: SLF001 - test double
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=InMemoryObservationLedger(),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    result = runner.run_case(
        _case(
            policy=_policy(
                families=(ProviderFamily.OPENAI,),
                max_attempts=3,
                cost_ceiling=100,
            )
        )
    )
    # 60 fits the ceiling once; a second 60 would be 120 > 100, so only one
    # invocation may happen even though three attempts were permitted.
    assert len(provider.calls) == 1
    assert result.seats[0].outcome.status is ObservationStatus.FAILED
    assert result.seats[0].outcome.failure_class is ProviderFailureClass.TIMEOUT


def test_an_exhausted_attempt_budget_returns_a_governed_disposition():
    """A persistently failing seat must not raise after one more external call."""
    provider = _provider(
        ProviderFamily.OPENAI,
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    ledger = InMemoryObservationLedger()
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: provider},
        ledger=ledger,
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    policy = _policy(families=(ProviderFamily.OPENAI,), max_attempts=5)
    # Five separate runs accumulate five recorded failures for the same seat.
    for _ in range(5):
        runner.run_case(_case(policy=policy))
    calls_before = len(provider.calls)
    assert calls_before == 5

    final = runner.run_case(_case(policy=policy))
    outcome = final.seats[0].outcome
    # No sixth external call, and a typed disposition rather than an exception.
    assert len(provider.calls) == calls_before
    assert outcome.status is ObservationStatus.UNAVAILABLE
    assert outcome.failure_class is ProviderFailureClass.PROVIDER_UNAVAILABLE
    assert outcome.attempt == 5


def test_a_payload_cannot_override_authenticated_evidence_metadata():
    """Reserved metadata comes from the snapshot, never from the payload."""
    snapshot = build_evidence_snapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CUTOFF,
        assembled_at=NOW,
        items=(
            _item(
                "E-real",
                payload={
                    "metric_name": "close",
                    "evidence_id": "E-spoofed",
                    "source_id": "spoofed-source",
                    "available_at": CUTOFF.isoformat(),
                },
            ),
        ),
        source_refs=("ref",),
        committee_policy_version=POLICY_VERSION,
        prompt_template_id="committee.opinion.v1",
        prompt_version="3",
    )
    view = snapshot.model_bound_view()
    item = view["evidence"][0]
    assert item["evidence_id"] == "E-real"
    assert item["source_id"] == "market-observation"
    assert item["available_at"] == (CUTOFF - timedelta(minutes=10)).isoformat()
    assert "E-spoofed" not in json.dumps(view, sort_keys=True)
    assert "spoofed-source" not in json.dumps(view, sort_keys=True)


def test_call_telemetry_participates_in_the_outcome_identity():
    """A row differing only in recorded telemetry must get a different id."""
    from dataclasses import replace

    base = _call_outcome()
    assert replace(base, latency_micros=999).outcome_id != base.outcome_id
    assert replace(base, input_tokens=7).outcome_id != base.outcome_id
    assert replace(base, output_tokens=7).outcome_id != base.outcome_id
    assert replace(base, estimated_cost_microunits=5).outcome_id != base.outcome_id
    assert replace(base, cost_completeness=CostCompleteness.UNKNOWN).outcome_id != (
        base.outcome_id
    )
    assert replace(base, reported_model="other").outcome_id != base.outcome_id
    assert replace(base, detail="changed").outcome_id != base.outcome_id
    # Identical content still collapses to one identity.
    assert replace(base).outcome_id == base.outcome_id


def test_a_stale_case_sidecar_is_reconciled_before_append(tmp_path):
    """Every sidecar, not just the call index, is reconciled to the log."""
    store = CommitteeEvidenceStore(root=tmp_path)
    runner = CommitteeRunner(
        providers={ProviderFamily.OPENAI: _ok(ProviderFamily.OPENAI)},
        ledger=DurableObservationLedger(store=store),
        now=lambda: NOW,
        settings=SHADOW_SETTINGS,
    )
    case_outcome = runner.run_case(
        _case(policy=_policy(families=(ProviderFamily.OPENAI,)))
    ).case_outcome
    assert store.append_case_outcome(case_outcome).reason == REASON_STORED

    # A sidecar that is present and non-empty but stale.
    store.cases_index_file.write_text(
        '{"schema_version":1,"kind":"CASE_OUTCOME","entries":'
        '{"COMMITTEE-OUTCOME:other":"COMMITTEE-OUTCOME:other"}}',
        encoding="utf-8",
    )
    result = store.append_case_outcome(case_outcome)
    assert result.stored is False
    assert result.reason == REASON_DUPLICATE
    assert len(list(store.iter_case_outcomes())) == 1
