"""Phase-B retrospective corpus and Phase-C prospective registration (IC-019, IC-020).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the separation that makes phase evidence trustworthy: a
retrospective corpus cannot be edited invisibly or built from easy cases only, it
cannot leak the answer to the model, and a retrospective result can never be read
as portfolio profit evidence. A sealed prospective experiment cannot be changed
after sealing, cannot score a drifted release, and cannot count a pending or
ineligible record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.contracts import CaseType, EvaluationPhase
from app.opip.committee.experiment import (
    ExperimentError,
    PopulationRecord,
    PopulationScope,
    ProspectiveRecordState,
    ProspectiveTally,
    ResearchMapping,
    RouteSeal,
    build_registration,
)
from app.opip.committee.retrospective import (
    MINIMUM_CORPUS_CASES,
    RETROSPECTIVE_EVIDENCE_DISPOSITION,
    CaseClass,
    CorpusError,
    RetrospectiveCorpus,
    RetrospectiveCorpusCase,
    RoleComparison,
    build_diagnostic_corpus,
    build_role_comparison,
)
from app.opip.committee.roles import CommitteeRole

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(hours=1)
RELEASE = "a" * 40
OTHER_RELEASE = "b" * 40
HORIZON = 4 * 3600


def _case(
    index: int,
    case_class: CaseClass,
    *,
    positive: bool = True,
    payload: dict | None = None,
) -> RetrospectiveCorpusCase:
    return RetrospectiveCorpusCase(
        case_id=f"case-{index}",
        case_class=case_class,
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CUTOFF,
        evidence_refs=(f"ev-{index}",),
        model_bound_payload=payload if payload is not None else {"evidence": f"ev-{index}"},
        resolved_positive=positive,
    )


def _full_corpus(count: int = MINIMUM_CORPUS_CASES) -> RetrospectiveCorpus:
    classes = list(CaseClass)
    cases = [
        _case(i, classes[i % len(classes)]) for i in range(count)
    ]
    return build_diagnostic_corpus(corpus_version="corpus-v1", cases=cases)


# ---------------------------------------------------------------- Phase B


def test_a_complete_corpus_spans_every_class_and_validates():
    corpus = _full_corpus()
    corpus.validate_usable()
    assert corpus.missing_classes() == ()
    assert sum(corpus.class_counts().values()) == MINIMUM_CORPUS_CASES


def test_a_corpus_below_the_minimum_is_refused():
    """A small corpus cannot support a comparison."""
    corpus = build_diagnostic_corpus(
        corpus_version="small",
        cases=[_case(i, list(CaseClass)[i % len(CaseClass)]) for i in range(5)],
    )
    with pytest.raises(CorpusError, match="at least"):
        corpus.validate_usable()


def test_a_corpus_missing_a_diagnostic_class_is_refused():
    """A corpus of easy cases produces a flattering comparison, so it is refused."""
    classes = [c for c in CaseClass if c is not CaseClass.LOSS]
    cases = [
        _case(i, classes[i % len(classes)]) for i in range(MINIMUM_CORPUS_CASES)
    ]
    corpus = build_diagnostic_corpus(corpus_version="no-losses", cases=cases)
    assert CaseClass.LOSS in corpus.missing_classes()
    with pytest.raises(CorpusError, match="every diagnostic class"):
        corpus.validate_usable()


def test_a_corpus_that_leaks_the_answer_is_refused():
    """A fixture that shows its own outcome measures recall, not analysis."""
    with pytest.raises(CorpusError, match="exposes its resolved outcome"):
        _case(1, CaseClass.WIN, payload={"evidence": "ev-1", "resolved_positive": True})
    with pytest.raises(CorpusError, match="exposes its resolved outcome"):
        _case(1, CaseClass.WIN, payload={"nested": {"label": "WIN"}})


def test_the_corpus_identity_is_content_derived_and_frozen():
    first = _full_corpus()
    second = _full_corpus()
    assert first.corpus_hash == second.corpus_hash
    # A changed case changes the corpus identity, so an edit cannot be hidden.
    mutated = list(first.cases)
    mutated[0] = _case(999, CaseClass.WIN, positive=False)
    changed = RetrospectiveCorpus(corpus_version="corpus-v1", cases=tuple(mutated))
    assert changed.corpus_hash != first.corpus_hash


def test_duplicate_case_ids_are_refused():
    duplicate_cases = (_case(1, CaseClass.WIN), _case(1, CaseClass.LOSS))
    with pytest.raises(CorpusError, match="duplicate corpus case id"):
        RetrospectiveCorpus(corpus_version="dup", cases=duplicate_cases)


def test_a_role_comparison_names_the_corpus_it_ran_on():
    corpus = _full_corpus()
    comparison = build_role_comparison(
        corpus=corpus,
        roles=(CommitteeRole.BULL_ADVOCATE, CommitteeRole.BEAR_ADVOCATE),
        experiment_id="phase-b-1",
        generated_at=NOW,
    )
    assert comparison.corpus_hash == corpus.corpus_hash
    assert comparison.phase is EvaluationPhase.RETROSPECTIVE


def test_a_role_comparison_refuses_an_unusable_corpus():
    small = build_diagnostic_corpus(
        corpus_version="small",
        cases=[_case(i, list(CaseClass)[i % len(CaseClass)]) for i in range(3)],
    )
    with pytest.raises(CorpusError):
        build_role_comparison(
            corpus=small,
            roles=(CommitteeRole.BULL_ADVOCATE,),
            experiment_id="x",
            generated_at=NOW,
        )


def test_a_retrospective_comparison_states_it_is_not_portfolio_evidence():
    """The record itself tells a reader what it may not be used for."""
    corpus = _full_corpus()
    comparison = build_role_comparison(
        corpus=corpus,
        roles=(CommitteeRole.BULL_ADVOCATE,),
        experiment_id="phase-b-1",
        generated_at=NOW,
    )
    assert comparison.evidence_disposition == RETROSPECTIVE_EVIDENCE_DISPOSITION
    assert comparison.evidence_disposition == "RESEARCH_ONLY_NOT_PORTFOLIO_EVIDENCE"
    assert comparison.measurement_only is True
    assert comparison.automatic_promotion is False
    assert comparison.trade_authority_changed is False


def test_a_retrospective_comparison_cannot_claim_a_prospective_phase():
    corpus = _full_corpus()

    with pytest.raises(CorpusError, match="retrospective evidence"):
        RoleComparison(
            corpus_version=corpus.corpus_version,
            corpus_hash=corpus.corpus_hash,
            roles=(CommitteeRole.BULL_ADVOCATE,),
            experiment_id="x",
            generated_at=NOW,
            phase=EvaluationPhase.PROSPECTIVE,
        )


def test_a_retrospective_comparison_cannot_promote():
    corpus = _full_corpus()
    with pytest.raises(CorpusError, match="cannot promote"):
        RoleComparison(
            corpus_version=corpus.corpus_version,
            corpus_hash=corpus.corpus_hash,
            roles=(CommitteeRole.BULL_ADVOCATE,),
            experiment_id="x",
            generated_at=NOW,
            automatic_promotion=True,
        )


# ---------------------------------------------------------------- Phase C


def _route(role: CommitteeRole = CommitteeRole.BULL_ADVOCATE) -> RouteSeal:
    return RouteSeal(
        role=role,
        registry_version="registry-v1",
        primary_entry_id="e-primary",
        fallback_entry_id="e-fallback",
        prompt_hash="COMMITTEE-PROMPT:aaaa",
        schema_hash="COMMITTEE-SCHEMA:bbbb",
    )


def _mapping() -> ResearchMapping:
    return ResearchMapping(
        mapping_version="mapping-v1",
        supportive_disposition="FAVOURABLE",
        opposing_disposition="UNFAVOURABLE",
    )


def _registration(**overrides):
    values = {
        "experiment_id": "phase-c-1",
        "corpus_version": "corpus-v1",
        "corpus_hash": "COMMITTEE-CORPUS:cccc",
        "release_sha": RELEASE,
        "horizon_seconds": HORIZON,
        "stopping_rule": "evaluate at maturity, no early stopping",
        "research_mapping": _mapping(),
        "routes": (_route(),),
        "registered_at": NOW,
    }
    values.update(overrides)
    return build_registration(**values)


def test_a_registration_seals_everything_an_evaluator_could_otherwise_choose():
    registration = _registration()
    payload = registration.identity_payload()
    for key in (
        "corpus_version",
        "corpus_hash",
        "release_sha",
        "horizon_seconds",
        "stopping_rule",
        "research_mapping",
        "routes",
        "registered_at",
    ):
        assert key in payload
    assert registration.registration_id.startswith("COMMITTEE-EXPERIMENT-REGISTRATION:")


def test_a_registration_is_always_prospective_evidence():
    assert _registration().experiment_scope is PopulationScope.PROSPECTIVE


def test_editing_a_registration_after_sealing_fails_closed():
    original = _registration()
    edited = original.resealed(release_sha=OTHER_RELEASE)
    with pytest.raises(ExperimentError, match="changed after sealing"):
        original.verify_unchanged(edited)
    # Re-sealing the same content is not a change.
    original.verify_unchanged(_registration())


def test_a_registration_cannot_seal_two_routes_for_one_role():
    """Two routes for one role would make automatic selection possible."""
    ambiguous_routes = (_route(), _route())
    with pytest.raises(ExperimentError, match="automatic selection would be ambiguous"):
        _registration(routes=ambiguous_routes)


def test_a_registration_requires_a_bounded_horizon_and_stopping_rule():
    with pytest.raises(ExperimentError, match="horizon_seconds"):
        _registration(horizon_seconds=0)
    with pytest.raises(ValueError, match="stopping_rule"):
        _registration(stopping_rule="  ")


def test_a_drifted_release_is_ineligible_and_never_scored():
    registration = _registration()
    state = registration.state_for(
        sealed_at=NOW,
        observed_release_sha=OTHER_RELEASE,
        evaluated_at=NOW + timedelta(seconds=HORIZON * 2),
        matured=True,
    )
    assert state is ProspectiveRecordState.INELIGIBLE


def test_an_immature_record_is_pending_not_scored():
    registration = _registration()
    state = registration.state_for(
        sealed_at=NOW,
        observed_release_sha=RELEASE,
        evaluated_at=NOW + timedelta(seconds=HORIZON - 1),
        matured=True,
    )
    assert state is ProspectiveRecordState.PENDING_MATURITY


def test_a_matured_record_on_the_sealed_release_is_scorable():
    registration = _registration()
    state = registration.state_for(
        sealed_at=NOW,
        observed_release_sha=RELEASE,
        evaluated_at=NOW + timedelta(seconds=HORIZON),
        matured=True,
    )
    assert state is ProspectiveRecordState.MATURED


def test_maturity_is_derived_from_the_sealed_horizon():
    registration = _registration()
    expected = NOW + timedelta(seconds=HORIZON)
    assert registration.maturity_at(sealed_at=NOW) == expected


# ------------------------------------------------- population separation


def test_a_retrospective_record_cannot_carry_a_prospective_phase():
    with pytest.raises(ExperimentError, match="must not be mixed"):
        PopulationRecord(
            record_id="r1",
            scope=PopulationScope.RETROSPECTIVE,
            phase=EvaluationPhase.PROSPECTIVE,
        )


def test_a_prospective_record_cannot_carry_a_retrospective_phase():
    with pytest.raises(ExperimentError, match="cannot carry a retrospective phase"):
        PopulationRecord(
            record_id="r1",
            scope=PopulationScope.PROSPECTIVE,
            phase=EvaluationPhase.RETROSPECTIVE,
            prospective_state=ProspectiveRecordState.SEALED,
        )


def test_a_prospective_record_must_declare_its_maturity_state():
    """Otherwise a pending record could be counted as matured by omission."""
    with pytest.raises(ExperimentError, match="must declare its maturity state"):
        PopulationRecord(
            record_id="r1",
            scope=PopulationScope.PROSPECTIVE,
            phase=EvaluationPhase.PROSPECTIVE,
        )


def test_a_retrospective_record_has_no_prospective_maturity_state():
    with pytest.raises(ExperimentError, match="no prospective maturity state"):
        PopulationRecord(
            record_id="r1",
            scope=PopulationScope.RETROSPECTIVE,
            phase=EvaluationPhase.RETROSPECTIVE,
            prospective_state=ProspectiveRecordState.MATURED,
        )


def test_the_tally_excludes_retrospective_and_ineligible_records():
    """Only matured prospective records may contribute to a prospective claim."""
    records = [
        PopulationRecord(
            record_id="retro-1",
            scope=PopulationScope.RETROSPECTIVE,
            phase=EvaluationPhase.RETROSPECTIVE,
        ),
        PopulationRecord(
            record_id="retro-2",
            scope=PopulationScope.RETROSPECTIVE,
            phase=EvaluationPhase.RETROSPECTIVE,
        ),
        PopulationRecord(
            record_id="pro-1",
            scope=PopulationScope.PROSPECTIVE,
            phase=EvaluationPhase.PROSPECTIVE,
            prospective_state=ProspectiveRecordState.SEALED,
        ),
        PopulationRecord(
            record_id="pro-2",
            scope=PopulationScope.PROSPECTIVE,
            phase=EvaluationPhase.PROSPECTIVE,
            prospective_state=ProspectiveRecordState.PENDING_MATURITY,
        ),
        PopulationRecord(
            record_id="pro-3",
            scope=PopulationScope.PROSPECTIVE,
            phase=EvaluationPhase.PROSPECTIVE,
            prospective_state=ProspectiveRecordState.MATURED,
        ),
        PopulationRecord(
            record_id="pro-4",
            scope=PopulationScope.PROSPECTIVE,
            phase=EvaluationPhase.PROSPECTIVE,
            prospective_state=ProspectiveRecordState.INELIGIBLE,
        ),
    ]
    tally = ProspectiveTally.from_records(records)
    assert tally.sealed == 1
    assert tally.pending_maturity == 1
    assert tally.matured == 1
    assert tally.ineligible == 1
    assert tally.total == 4  # the two retrospective records are not counted
    assert tally.scorable == 1


def test_an_empty_prospective_population_is_zero_not_favourable():
    tally = ProspectiveTally.from_records([])
    assert tally.total == 0
    assert tally.scorable == 0
    assert tally.ineligible == 0
