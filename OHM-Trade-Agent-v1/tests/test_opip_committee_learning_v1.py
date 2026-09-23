"""Learning lineage: hypothesis, experiment, conclusion, release, effectiveness.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the chain's guarantees: a hypothesis must be falsifiable and
grounded, a registered experiment is frozen, inconclusive is a first-class
conclusion, promotion stays human and SHA-bound, effectiveness over incomparable
cohorts is refused, and no link in the chain can change policy by itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.learning import (
    COMPROMISED,
    CohortComparability,
    EffectDirection,
    Hypothesis,
    LearningChain,
    LearningError,
    PostChangeEffectiveness,
    RegisteredExperiment,
    ReleaseRecord,
    ResearchConclusion,
    ResearchConclusionRecord,
    build_learning_chain,
    chain_for,
    require_validated_weakness,
)
from app.opip.committee.weakness import WeaknessValidationState

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=1)
SHA_A = "a" * 40


def _hypothesis(**overrides) -> Hypothesis:
    values = {
        "hypothesis_id": "h-1",
        "suspected_mechanism": "spread widens at the open, so entries fill worse",
        "eligible_cohort": "instrument:BTC-USD",
        "falsifiable_prediction": "entries fill no worse than the decision price",
        "expected_effect_direction": EffectDirection.IMPROVEMENT,
        "expected_effect_microunits": -12_000,
        "source_finding_ids": ("w-1",),
        "raised_at": NOW,
    }
    values.update(overrides)
    return Hypothesis(**values)


def _experiment(**overrides) -> RegisteredExperiment:
    values = {
        "experiment_id": "e-1",
        "hypothesis_id": "h-1",
        "dataset_manifest_ref": "manifest-v1",
        "knowledge_cutoff_at": CUTOFF,
        "policy_version": "policy-v1",
        "route_version": "registry-v1",
        "model_version": "model-a",
        "prompt_hash": "COMMITTEE-PROMPT:aaaa",
        "schema_hash": "COMMITTEE-SCHEMA:bbbb",
        "endpoint": "https://example.invalid/v1",
        "effect_definition": "incremental trading net over the matched panel",
        "experiment_family": "entry-timing",
        "statistical_method": "paired bootstrap over matched cases",
        "stopping_rule": "stop at the sealed horizon, no early stopping",
        "registered_at": NOW,
        "holdout_ref": "holdout-v1",
    }
    values.update(overrides)
    return RegisteredExperiment(**values)


def _conclusion(**overrides) -> ResearchConclusionRecord:
    values = {
        "conclusion_id": "c-1",
        "experiment_id": "e-1",
        "conclusion": ResearchConclusion.ACCEPTED,
        "rationale": "the sealed evaluation supported the prediction",
        "concluded_at": NOW,
        "evaluation_refs": ("COMMITTEE-PROSPECTIVE-EVAL:cccc",),
        "measured_effect_microunits": -9_500,
    }
    values.update(overrides)
    return ResearchConclusionRecord(**values)


def _release(**overrides) -> ReleaseRecord:
    values = {
        "release_id": "r-1",
        "conclusion_id": "c-1",
        "approved_sha": SHA_A,
        "released_at": NOW,
        "approved_by": "owner",
        "artifact_ref": "artifact-v1",
    }
    values.update(overrides)
    return ReleaseRecord(**values)


def _effectiveness(**overrides) -> PostChangeEffectiveness:
    values = {
        "effectiveness_id": "eff-1",
        "release_id": "r-1",
        "comparability": CohortComparability.COMPARABLE,
        "measured_at": NOW + timedelta(days=30),
        "before_window_ref": "window-before",
        "after_window_ref": "window-after",
        "before_metric_microunits": -20_000,
        "after_metric_microunits": -8_000,
        "recurrence_before": 5,
        "recurrence_after": 1,
    }
    values.update(overrides)
    return PostChangeEffectiveness(**values)


# --------------------------------------------------- IC-035 hypothesis


def test_a_hypothesis_records_mechanism_cohort_prediction_and_effect():
    hypothesis = _hypothesis()
    payload = hypothesis.identity_payload()
    for key in (
        "suspected_mechanism",
        "eligible_cohort",
        "falsifiable_prediction",
        "expected_effect_direction",
        "expected_effect_microunits",
        "source_finding_ids",
    ):
        assert key in payload


def test_a_hypothesis_must_be_grounded_in_a_source():
    """An ungrounded hypothesis is a hunch, not a research line."""
    with pytest.raises(LearningError, match="must cite the findings"):
        _hypothesis(source_finding_ids=())


def test_a_hypothesis_requires_a_falsifiable_prediction():
    with pytest.raises(LearningError, match="falsifiable_prediction is required"):
        _hypothesis(falsifiable_prediction="  ")


def test_a_no_change_hypothesis_cannot_expect_a_non_zero_effect():
    with pytest.raises(LearningError, match="NO_CHANGE"):
        _hypothesis(
            expected_effect_direction=EffectDirection.NO_CHANGE,
            expected_effect_microunits=500,
        )
    # A zero or unknown effect is consistent with NO_CHANGE.
    assert _hypothesis(
        expected_effect_direction=EffectDirection.NO_CHANGE,
        expected_effect_microunits=0,
    ).expected_effect_microunits == 0


def test_an_unmeasured_expected_effect_stays_null():
    assert _hypothesis(expected_effect_microunits=None).expected_effect_microunits is None


def test_a_hypothesis_requires_a_validated_weakness_to_be_grounded():
    """A pending or rejected finding does not establish a mechanism."""
    with pytest.raises(LearningError, match="VALIDATED finding"):
        require_validated_weakness([WeaknessValidationState.PENDING])
    with pytest.raises(LearningError, match="VALIDATED finding"):
        require_validated_weakness([WeaknessValidationState.REJECTED])
    with pytest.raises(LearningError, match="VALIDATED finding"):
        require_validated_weakness([WeaknessValidationState.INCONCLUSIVE])
    with pytest.raises(LearningError, match="at least one finding"):
        require_validated_weakness([])
    require_validated_weakness(
        [WeaknessValidationState.REJECTED, WeaknessValidationState.VALIDATED]
    )


# --------------------------------------------------- IC-036 experiment


def test_an_experiment_freezes_every_choice_on_every_axis():
    payload = _experiment().identity_payload()
    for key in (
        "dataset_manifest_ref",
        "knowledge_cutoff_at",
        "policy_version",
        "route_version",
        "model_version",
        "prompt_hash",
        "schema_hash",
        "endpoint",
        "effect_definition",
        "experiment_family",
        "statistical_method",
        "stopping_rule",
        "holdout_ref",
    ):
        assert key in payload


def test_every_frozen_field_is_required():
    for field_name in (
        "dataset_manifest_ref",
        "policy_version",
        "route_version",
        "model_version",
        "prompt_hash",
        "schema_hash",
        "endpoint",
        "effect_definition",
        "experiment_family",
        "statistical_method",
        "stopping_rule",
    ):
        with pytest.raises(LearningError, match=field_name):
            _experiment(**{field_name: ""})


def test_changing_a_sealed_experiment_fails_closed():
    original = _experiment()
    edited = _experiment(stopping_rule="stop early once significant")
    with pytest.raises(LearningError, match="changed after registration"):
        original.verify_unchanged(edited)
    # Re-registering identical content is not a change.
    original.verify_unchanged(_experiment())


def test_changing_the_prompt_hash_after_sealing_is_a_change():
    """A silent prompt change mid-experiment invalidates the comparison."""
    original = _experiment()
    edited = _experiment(prompt_hash="COMMITTEE-PROMPT:zzzz")
    with pytest.raises(LearningError, match="changed after registration"):
        original.verify_unchanged(edited)


def test_compromise_is_explicit_and_requires_a_reason():
    original = _experiment()
    with pytest.raises(LearningError, match="requires a reason"):
        original.mark_compromised("  ")
    compromised = original.mark_compromised("model alias rotated mid-run")
    assert compromised.is_compromised
    assert compromised.compromised_reason == "model alias rotated mid-run"
    # The compromise is semantic state, so it changes the identity: a compromised
    # experiment must not pass as the sealed design it deviated from.
    assert compromised.experiment_hash != original.experiment_hash
    with pytest.raises(LearningError, match="changed after registration"):
        original.verify_unchanged(compromised)
    assert COMPROMISED == "COMPROMISED"


# --------------------------------------------------- IC-037 conclusion


def test_the_three_conclusions_are_exactly_declared():
    assert {value.value for value in ResearchConclusion} == {
        "ACCEPTED",
        "REJECTED",
        "INCONCLUSIVE",
    }


def test_a_resolved_conclusion_must_reference_its_evaluation():
    for conclusion in (ResearchConclusion.ACCEPTED, ResearchConclusion.REJECTED):
        with pytest.raises(LearningError, match="must reference the sealed"):
            _conclusion(conclusion=conclusion, evaluation_refs=())


def test_inconclusive_is_a_first_class_conclusion():
    """Forcing an unresolved experiment into accepted or rejected would invent a finding."""
    record = _conclusion(
        conclusion=ResearchConclusion.INCONCLUSIVE,
        evaluation_refs=(),
        rationale="the sample did not reach the declared minimum",
        measured_effect_microunits=None,
    )
    assert record.conclusion is ResearchConclusion.INCONCLUSIVE
    assert record.measured_effect_microunits is None


def test_a_conclusion_never_authorises_a_policy_change():
    """Promotion is a separate human act, not an inference from evidence."""
    assert _conclusion().authorises_policy_change is False


def test_an_unmeasured_effect_stays_null():
    assert _conclusion(measured_effect_microunits=None).measured_effect_microunits is None


# --------------------------------------------------- IC-038/039 release


def test_a_release_must_name_the_exact_approved_sha():
    """A short or absent SHA cannot identify the released artifact."""
    for bad in ("", "abc123", "a" * 39, "A" * 40, "z" * 40):
        with pytest.raises(LearningError, match="approved_sha"):
            _release(approved_sha=bad)
    assert _release().approved_sha == SHA_A


def test_a_release_records_who_decided():
    assert _release(approved_by="owner").approved_by == "owner"
    with pytest.raises(LearningError, match="approved_by"):
        _release(approved_by="   ")


# ------------------------------------------- IC-040/041 effectiveness


def test_effectiveness_reports_the_observed_delta():
    effectiveness = _effectiveness()
    assert effectiveness.measured_delta_microunits == 12_000
    assert effectiveness.recurrence_delta == -4


def test_an_unmeasured_delta_stays_null_not_zero():
    effectiveness = _effectiveness(
        before_metric_microunits=None, after_metric_microunits=None
    )
    assert effectiveness.measured_delta_microunits is None
    assert effectiveness.recurrence_delta == -4


def test_an_incomparable_cohort_must_state_why_and_report_no_metric():
    """A before/after over unlike populations produces a confident wrong answer."""
    with pytest.raises(LearningError, match="must state why"):
        _effectiveness(
            comparability=CohortComparability.INCOMPARABLE,
            incomparable_reason=None,
            before_metric_microunits=None,
            after_metric_microunits=None,
        )
    with pytest.raises(LearningError, match="must not report a before/after metric"):
        _effectiveness(
            comparability=CohortComparability.INCOMPARABLE,
            incomparable_reason="regime changed",
            before_metric_microunits=-1,
            after_metric_microunits=-2,
        )
    incomparable = _effectiveness(
        comparability=CohortComparability.INCOMPARABLE,
        incomparable_reason="regime changed mid-window",
        before_metric_microunits=None,
        after_metric_microunits=None,
        recurrence_before=None,
        recurrence_after=None,
    )
    assert incomparable.measured_delta_microunits is None
    assert incomparable.recurrence_delta is None


# --------------------------------------------------- chain integrity


def test_a_chain_assembles_the_full_lineage_in_order():
    chain = build_learning_chain(
        hypothesis=_hypothesis(),
        experiment=_experiment(),
        conclusion=_conclusion(),
        release=_release(),
        effectiveness=_effectiveness(),
    )
    assert chain.stage == "EFFECTIVENESS_MEASURED"
    assert chain.chain_hash.startswith("COMMITTEE-EXPERIMENT:")


def test_the_stage_reports_the_furthest_completed_link():
    assert (
        build_learning_chain(hypothesis=_hypothesis(), experiment=_experiment()).stage
        == "REGISTERED"
    )
    assert (
        build_learning_chain(
            hypothesis=_hypothesis(),
            experiment=_experiment(),
            conclusion=_conclusion(),
        ).stage
        == "CONCLUDED"
    )
    assert (
        build_learning_chain(
            hypothesis=_hypothesis(),
            experiment=_experiment(),
            conclusion=_conclusion(),
            release=_release(),
        ).stage
        == "RELEASED"
    )


def test_an_experiment_must_test_its_stated_hypothesis():
    with pytest.raises(LearningError, match="does not test the stated hypothesis"):
        LearningChain(hypothesis=_hypothesis(), experiment=_experiment(hypothesis_id="h-9"))


def test_a_conclusion_must_belong_to_its_experiment():
    with pytest.raises(LearningError, match="does not belong to the experiment"):
        LearningChain(
            hypothesis=_hypothesis(),
            experiment=_experiment(),
            conclusion=_conclusion(experiment_id="e-9"),
        )


def test_a_release_cannot_exist_without_a_conclusion():
    with pytest.raises(LearningError, match="without a conclusion"):
        LearningChain(
            hypothesis=_hypothesis(),
            experiment=_experiment(),
            release=_release(),
        )


def test_a_release_must_reference_its_conclusion():
    with pytest.raises(LearningError, match="does not reference the conclusion"):
        LearningChain(
            hypothesis=_hypothesis(),
            experiment=_experiment(),
            conclusion=_conclusion(),
            release=_release(conclusion_id="c-9"),
        )


def test_effectiveness_requires_a_release_to_measure():
    with pytest.raises(LearningError, match="requires a release"):
        LearningChain(
            hypothesis=_hypothesis(),
            experiment=_experiment(),
            conclusion=_conclusion(),
            effectiveness=_effectiveness(),
        )


def test_effectiveness_must_reference_its_release():
    with pytest.raises(LearningError, match="does not reference the release"):
        LearningChain(
            hypothesis=_hypothesis(),
            experiment=_experiment(),
            conclusion=_conclusion(),
            release=_release(),
            effectiveness=_effectiveness(release_id="r-9"),
        )


def test_the_chain_can_be_looked_up_by_hypothesis():
    chain = build_learning_chain(hypothesis=_hypothesis(), experiment=_experiment())
    assert chain_for([chain], hypothesis_id="h-1") is chain
    assert chain_for([chain], hypothesis_id="h-missing") is None
