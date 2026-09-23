"""Immutable Weakness Finding Registry (IC-029 to IC-034).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the registry's guarantees: the original finding is immutable,
the committee cannot certify its own finding, the taxonomy is versioned so history
cannot be re-labelled, recurrence is scope-bound, and an unmeasured economic
effect stays unknown rather than becoming zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.roles import CommitteeRole
from app.opip.committee.weakness import (
    PERMITTED_VALIDATORS,
    WEAKNESS_TAXONOMY_VERSION,
    RecurrenceKey,
    ValidatorKind,
    WeaknessCategory,
    WeaknessFinding,
    WeaknessFollowUp,
    WeaknessRegistry,
    WeaknessRegistryError,
    WeaknessValidation,
    WeaknessValidationState,
    build_finding,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(hours=2)


def _finding(
    finding_id: str = "w-1",
    *,
    category: WeaknessCategory = WeaknessCategory.SLIPPAGE,
    scope: str = "instrument:BTC-USD",
    subject: str = "spread-at-entry",
    statement: str = "entry filled materially worse than the decision price",
    refs: tuple[str, ...] = ("ev-1",),
    detected_at: datetime = NOW,
    evidence_cutoff: datetime = CUTOFF,
    **overrides,
) -> WeaknessFinding:
    return build_finding(
        finding_id=finding_id,
        decision_context_id="ctx-1",
        weakness_category=category,
        finding_statement=statement,
        detected_at=detected_at,
        evidence_cutoff=evidence_cutoff,
        evidence_refs=refs,
        recurrence_scope=scope,
        recurrence_subject=subject,
        **overrides,
    )


def _validation(
    *,
    validation_id: str = "v-1",
    finding_id: str = "w-1",
    state: WeaknessValidationState = WeaknessValidationState.VALIDATED,
    validator: ValidatorKind = ValidatorKind.OUTCOME_EVIDENCE,
    outcome_refs: tuple[str, ...] = ("outcome-1",),
    validated_at: datetime = NOW,
    economic: int | None = None,
    rationale: str = "the realised outcome confirms the finding",
) -> WeaknessValidation:
    return WeaknessValidation(
        validation_id=validation_id,
        finding_id=finding_id,
        state=state,
        validator=validator,
        validated_at=validated_at,
        rationale=rationale,
        outcome_refs=outcome_refs,
        economic_effect_microunits=economic,
    )


# ------------------------------------------------- IC-030 taxonomy


def test_the_taxonomy_is_the_declared_controlled_set():
    assert {category.value for category in WeaknessCategory} == {
        "MISSING_EVIDENCE",
        "STALE_EVIDENCE",
        "OBSERVABILITY_GAP",
        "DECISION_OR_QUALIFICATION_WEAKNESS",
        "FALSE_POSITIVE",
        "MISSED_OPPORTUNITY",
        "REGIME_MISCLASSIFICATION",
        "MODEL_ASSUMPTION_CONFLICT",
        "ENTRY_TIMING",
        "LIQUIDITY_EXECUTION",
        "SLIPPAGE",
        "RISK_POLICY",
        "PROTECTION_WEAKNESS",
        "EXIT_POLICY",
        "POLICY_VERSION_WEAKNESS",
        "RECURRING_SYSTEM_WEAKNESS",
        "OTHER",
    }


def test_other_requires_a_specific_statement_and_evidence():
    """OTHER is an escape hatch, not a way to avoid categorising."""
    with pytest.raises(WeaknessRegistryError, match="specific structured statement"):
        _finding(category=WeaknessCategory.OTHER, statement="unclear")
    # Evidence is required of every finding, so an OTHER finding is not exempt.
    with pytest.raises(WeaknessRegistryError, match="at least one piece of evidence"):
        _finding(
            category=WeaknessCategory.OTHER,
            statement="a genuinely new weakness with a specific description",
            refs=(),
        )
    # A specific, evidenced OTHER is accepted.
    finding = _finding(
        category=WeaknessCategory.OTHER,
        statement="a genuinely new weakness with a specific description",
    )
    assert finding.weakness_category is WeaknessCategory.OTHER


def test_a_finding_records_the_taxonomy_version_in_force():
    """A later taxonomy change must not retroactively re-label history."""
    finding = _finding()
    assert finding.taxonomy_version == WEAKNESS_TAXONOMY_VERSION
    payload = finding.identity_payload()
    assert payload["taxonomy_version"] == WEAKNESS_TAXONOMY_VERSION


def test_the_recurrence_key_carries_its_own_taxonomy_version():
    key = RecurrenceKey(
        category=WeaknessCategory.SLIPPAGE, scope="s", subject="x"
    )
    assert key.identity_payload()["taxonomy_version"] == WEAKNESS_TAXONOMY_VERSION


# ------------------------------------------------- IC-029 registry


def _finding_params(**overrides) -> dict:
    """Construction arguments for a finding, so a test can vary one field.

    Exposed so a test can build the arguments outside a ``pytest.raises`` block and
    leave exactly one possibly-throwing invocation inside it.
    """
    params: dict = {
        "finding_id": "w-1",
        "decision_context_id": "ctx-1",
        "weakness_category": WeaknessCategory.SLIPPAGE,
        "finding_statement": "entry filled materially worse than the decision price",
        "detected_at": NOW,
        "evidence_cutoff": CUTOFF,
        "evidence_refs": ("ev-1",),
        "recurrence_key": RecurrenceKey(
            category=WeaknessCategory.SLIPPAGE,
            scope="instrument:BTC-USD",
            subject="spread-at-entry",
        ),
    }
    params.update(overrides)
    return params


def test_a_finding_requires_evidence_and_a_coherent_key():
    no_evidence = _finding_params(evidence_refs=())
    mismatched_key = _finding_params(
        recurrence_key=RecurrenceKey(
            category=WeaknessCategory.EXIT_POLICY, scope="s", subject="x"
        )
    )
    with pytest.raises(WeaknessRegistryError, match="at least one piece of evidence"):
        WeaknessFinding(**no_evidence)
    with pytest.raises(WeaknessRegistryError, match="must match the finding category"):
        WeaknessFinding(**mismatched_key)


def test_a_finding_may_not_cite_evidence_from_its_own_future():
    with pytest.raises(WeaknessRegistryError, match="cannot be after detected_at"):
        _finding(detected_at=CUTOFF, evidence_cutoff=NOW)


def test_registering_a_duplicate_finding_id_is_refused():
    """Findings are immutable, so an id cannot be quietly replaced."""
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    duplicate = _finding(statement="a different statement")
    with pytest.raises(WeaknessRegistryError, match="immutable"):
        registry.append_finding(duplicate)


def test_the_original_finding_is_never_mutated_by_validation():
    registry = WeaknessRegistry()
    finding = registry.append_finding(_finding())
    before = finding.finding_hash
    registry.append_validation(_validation())
    after = registry.findings()[0]
    assert after.finding_hash == before
    assert after.finding_statement == finding.finding_statement


def test_a_finding_without_a_validation_is_pending():
    """A finding never becomes valid because nobody looked at it."""
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    assert registry.state_of("w-1") is WeaknessValidationState.PENDING


def test_validation_and_follow_up_reference_known_findings():
    registry = WeaknessRegistry()
    orphan_validation = _validation(finding_id="nope")
    orphan_follow_up = WeaknessFollowUp(
        follow_up_id="f-1",
        finding_id="nope",
        recorded_at=NOW,
        remediation_ref="pr-1",
    )
    with pytest.raises(WeaknessRegistryError, match="unknown finding"):
        registry.append_validation(orphan_validation)
    with pytest.raises(WeaknessRegistryError, match="unknown finding"):
        registry.append_follow_up(orphan_follow_up)


def test_a_rejected_append_can_be_recorded_rather_than_lost():
    registry = WeaknessRegistry()
    registry.note_rejection("duplicate finding id")
    assert registry.rejections == ("duplicate finding id",)


# --------------------------------------- IC-031 validation lifecycle


def test_the_committee_cannot_validate_its_own_finding():
    """A model asserting its own finding is not evidence."""
    assert ValidatorKind.COMMITTEE_MODEL not in PERMITTED_VALIDATORS
    with pytest.raises(WeaknessRegistryError, match="may not validate its own finding"):
        _validation(validator=ValidatorKind.COMMITTEE_MODEL)


def test_a_resolved_validation_must_reference_an_outcome():
    with pytest.raises(WeaknessRegistryError, match="must reference the realised outcome"):
        _validation(outcome_refs=())


def test_a_pending_validation_record_is_refused():
    """PENDING is the absence of a validation, not a kind of one."""
    with pytest.raises(WeaknessRegistryError, match="must resolve a finding"):
        _validation(state=WeaknessValidationState.PENDING)


def test_the_current_state_is_derived_from_appended_history():
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    registry.append_validation(
        _validation(
            validation_id="v-inconclusive",
            state=WeaknessValidationState.INCONCLUSIVE,
            outcome_refs=(),
            validated_at=NOW,
        )
    )
    assert registry.state_of("w-1") is WeaknessValidationState.INCONCLUSIVE
    # A later validation supersedes it without erasing the earlier record.
    registry.append_validation(
        _validation(
            validation_id="v-validated",
            state=WeaknessValidationState.VALIDATED,
            validated_at=NOW + timedelta(hours=1),
        )
    )
    assert registry.state_of("w-1") is WeaknessValidationState.VALIDATED
    assert len(registry.validations_for("w-1")) == 2


def test_a_rejected_finding_remains_in_history():
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    registry.append_validation(
        _validation(
            validation_id="v-rejected",
            state=WeaknessValidationState.REJECTED,
            rationale="the outcome shows the entry was within tolerance",
        )
    )
    assert registry.state_of("w-1") is WeaknessValidationState.REJECTED
    assert len(registry.findings()) == 1


def test_inconclusive_is_a_first_class_outcome():
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    registry.append_validation(
        _validation(
            validation_id="v-inc",
            state=WeaknessValidationState.INCONCLUSIVE,
            outcome_refs=(),
        )
    )
    assert registry.summary()["INCONCLUSIVE"] == 1


# ------------------------------------------- IC-032 recurrence


def test_recurrence_groups_only_same_scope_and_subject():
    """Unrelated incidents sharing a category must never be merged."""
    registry = WeaknessRegistry()
    registry.append_finding(_finding("w-1", scope="instrument:BTC-USD"))
    registry.append_finding(_finding("w-2", scope="instrument:BTC-USD"))
    registry.append_finding(_finding("w-3", scope="instrument:ETH-USD"))
    groups = registry.recurrence_groups()
    sizes = sorted(len(items) for items in groups.values())
    assert sizes == [1, 2]
    recurring = registry.recurring_findings()
    assert len(recurring) == 1


def test_a_different_category_is_a_different_recurrence_key():
    registry = WeaknessRegistry()
    registry.append_finding(_finding("w-1"))
    registry.append_finding(_finding("w-2", category=WeaknessCategory.EXIT_POLICY))
    assert len(registry.recurrence_groups()) == 2


def test_recurrence_requires_at_least_two_occurrences():
    registry = WeaknessRegistry()
    registry.append_finding(_finding("w-1"))
    assert registry.recurring_findings() == ()
    with pytest.raises(WeaknessRegistryError, match=">= 2"):
        registry.recurring_findings(minimum_occurrences=1)


# ------------------------------------------- IC-033 economic effect


def test_an_unmeasured_economic_effect_stays_unknown_not_zero():
    """An unmeasured weakness must not look like a costless one."""
    registry = WeaknessRegistry()
    finding = registry.append_finding(_finding())
    assert finding.economic_effect_microunits is None
    assert finding.is_economically_measured is False
    assert registry.validated_economic_effect("w-1") is None


def test_an_unvalidated_finding_reports_no_economic_effect():
    registry = WeaknessRegistry()
    registry.append_finding(_finding(economic_effect_microunits=-5_000))
    # Not validated, so no economic conclusion is drawn from it yet.
    assert registry.validated_economic_effect("w-1") is None


def test_a_measured_effect_on_a_validated_finding_is_reported():
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    registry.append_validation(_validation(economic=-12_500))
    assert registry.validated_economic_effect("w-1") == -12_500


def test_the_finding_effect_is_used_when_the_validation_omits_one():
    registry = WeaknessRegistry()
    registry.append_finding(_finding(economic_effect_microunits=-7_000))
    registry.append_validation(_validation(economic=None))
    assert registry.validated_economic_effect("w-1") == -7_000


# ------------------------------------------- IC-034 attribution


def test_a_finding_records_the_role_model_and_policy_that_raised_it():
    """Attribution to a discovering role is what makes a model's contribution measurable."""
    finding = _finding(
        committee_role=CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST,
        provider="openai",
        model="model-a",
        prompt_policy_version="policy-v1",
        committee_case_id="case-1",
        paper_trade_id="paper-1",
        baseline_decision_ref="decision-1",
    )
    payload = finding.identity_payload()
    assert payload["committee_role"] is CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST
    assert payload["provider"] == "openai"
    assert payload["model"] == "model-a"
    assert payload["prompt_policy_version"] == "policy-v1"
    assert payload["paper_trade_id"] == "paper-1"
    assert payload["baseline_decision_ref"] == "decision-1"


def test_an_invalid_role_is_refused():
    with pytest.raises(WeaknessRegistryError, match="invalid committee_role"):
        _finding(committee_role="LIQUIDITY_STRUCTURE_ANALYST")


# ------------------------------------- append-only follow-up linkage


def test_remediation_and_post_change_evidence_are_appended_not_folded_in():
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    registry.append_follow_up(
        WeaknessFollowUp(
            follow_up_id="f-1",
            finding_id="w-1",
            recorded_at=NOW + timedelta(days=1),
            remediation_ref="pr-42",
            hypothesis_id="h-1",
            experiment_id="e-1",
        )
    )
    registry.append_follow_up(
        WeaknessFollowUp(
            follow_up_id="f-2",
            finding_id="w-1",
            recorded_at=NOW + timedelta(days=30),
            resolution_release_sha="a" * 40,
            post_change_result="recurrence dropped to zero over the next window",
        )
    )
    follow_ups = registry.follow_ups_for("w-1")
    assert len(follow_ups) == 2
    # The original statement is still exactly as raised.
    assert registry.findings()[0].finding_statement == (
        "entry filled materially worse than the decision price"
    )


def test_an_empty_follow_up_is_refused():
    with pytest.raises(WeaknessRegistryError, match="must carry remediation"):
        WeaknessFollowUp(follow_up_id="f", finding_id="w-1", recorded_at=NOW)


def test_the_summary_reports_every_state_even_at_zero():
    registry = WeaknessRegistry()
    registry.append_finding(_finding())
    summary = registry.summary()
    for state in WeaknessValidationState:
        assert state.value in summary
    assert summary[WeaknessValidationState.PENDING.value] == 1
    assert summary[WeaknessValidationState.VALIDATED.value] == 0
