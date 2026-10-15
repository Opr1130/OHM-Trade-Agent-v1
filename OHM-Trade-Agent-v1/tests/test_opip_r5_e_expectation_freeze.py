"""ATDD acceptance tests for the R5-E expectation-freeze slice (`SC-10` capture).

Increment: `ATDD-R5-E-expectation-freeze`. Every test cites its criterion in the
docstring as `INCREMENT/AC-NNN`, and the module is marked
`pytest.mark.acceptance`, so both the scope checker and `pytest -m acceptance`
read the same citations.

The increment freezes a *prospective* expectation before any outcome can be
recorded for it. Nothing here writes durably, places an order or changes policy:
the records are evidence, and the tests below prove that they carry no authority,
no storage and no runtime consumer.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.learning.evaluation import (  # noqa: E402
    EXPECTATION_ID_PREFIX,
    EXPECTATION_RECORD_KEYS,
    EXPECTATION_SCHEMA_VERSION,
    EXPECTED_PAYLOAD_HASH_PREFIX,
    MEASURE_KINDS,
    OUTCOME_STATUS_REASONS,
    POPULATIONS,
    ExpectationBaseline,
    ExpectationError,
    ExpectationGrain,
    ExpectedOutcome,
    MeasureKind,
    MeasureRecord,
    OutcomeFacts,
    OutcomeStatus,
    OutcomeStatusReason,
    Population,
    ProspectiveExpectation,
    assert_expectation_populations_are_separate,
    assert_expectation_record_keys,
    assert_measures_stay_separate,
    attach_outcome,
    expected_payload_hash,
    freeze_expectation,
    resolve_outcome_status,
    total_for_kind,
)
from app.services.release_profiles import (  # noqa: E402
    get_release_profiles,
    validate_profile_contract,
)
from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)
from tests.test_atdd_scope_control import (  # noqa: E402
    global_pointer_pin_violations,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-E-expectation-freeze"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
EVALUATION_MODULE = APP_ROOT / "app" / "opip" / "learning" / "evaluation.py"
R5E_BANNER = "# R5-E prospective expectation capture (SC-10)"

#: Runtime roots that own trading, discovery, decision and risk paths. None may
#: import or consume the expectation records this increment adds.
RUNTIME_ROOTS = (
    APP_ROOT / "app" / "jobs",
    APP_ROOT / "app" / "api",
    APP_ROOT / "app" / "opip" / "decision",
    APP_ROOT / "app" / "opip" / "discovery",
    APP_ROOT / "app" / "opip" / "risk",
)

FORBIDDEN_PRIMITIVES = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "sqlite3",
    "psycopg",
    "psycopg2",
    "sqlalchemy",
    "subprocess",
    "random",
    "secrets",
    "ccxt",
    "krakenex",
    "telegram",
}

FORBIDDEN_AUTHORITY_TOKENS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "create_order",
    "withdraw(",
    "transfer(",
    "deposit(",
    "api_key",
    "api_secret",
    "credential",
)

FORBIDDEN_PERSISTENCE_TOKENS = (
    "writer_client",
    "submit_decision_context",
    "submit_decision_snapshot",
    "submit_canonical_event",
    "INSERT INTO",
    "parquet",
    "redis",
    "telegram",
)

FORBIDDEN_CROSS_KIND_TOTALS = (
    "total_measures",
    "sum_measures",
    "aggregate_measures",
    "combined_measures",
)

T0 = datetime(2026, 2, 2, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0
MATURITY = T0 + timedelta(days=3)
AFTER_MATURITY = MATURITY + timedelta(minutes=1)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def r5e_section(path: Path) -> str:
    """Return only the R5-E section of the owning module."""

    source = module_source(path)
    assert R5E_BANNER in source, R5E_BANNER
    section = source[source.index(R5E_BANNER) :]
    if "__all__ = [" in section:
        section = section[: section.index("__all__ = [")]
    return section


def function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def grain(
    grain_id: str = "EP-R5E-0001",
    *,
    entity: str = "BTCUSDT",
    policy_id: str = "policy-r5e-baseline",
    horizon: str = "SWING",
    revision: int = 1,
) -> ExpectationGrain:
    return ExpectationGrain(
        grain_id=grain_id,
        entity=entity,
        policy_id=policy_id,
        horizon=horizon,
        revision=revision,
    )


def baseline() -> ExpectationBaseline:
    return ExpectationBaseline(
        baseline_id="BASELINE-R5E-FROZEN-1",
        description="the frozen declared-policy baseline this expectation is measured against",
    )


def payoff(*, net: float = 12.5, horizon: float = 259200.0) -> ExpectedOutcome:
    return ExpectedOutcome(
        expected_net_return=net,
        expected_duration_seconds=86400.0,
        expected_cost_dollars=4.25,
        expected_horizon_seconds=horizon,
    )


def facts(*, net: float = 9.75, fills: int = 2) -> OutcomeFacts:
    return OutcomeFacts(
        fill_count=fills,
        net_return=net,
        duration_seconds=82800.0,
        cost_dollars=4.5,
    )


def expectation(**overrides) -> ProspectiveExpectation:
    base: dict[str, object] = {
        "grain": grain(),
        "expected_payload": payoff(),
        "cutoff": CUTOFF,
        "maturity_at": MATURITY,
        "recorded_at": T0,
        "population": Population.TAKEN,
        "baseline": baseline(),
        "measure_kinds": (MeasureKind.REALIZED_PAPER_OUTCOME,),
    }
    base.update(overrides)
    return freeze_expectation(**base)


def expectation_for(population: Population, index: int) -> ProspectiveExpectation:
    return expectation(grain=grain(f"EP-R5E-{index:04d}"), population=population)


# ---------------------------------------------------------------------------
# AC-001 The frozen prospective expectation
# ---------------------------------------------------------------------------


def test_ac_001_the_frozen_expectation_is_complete_and_immutable() -> None:
    """ATDD-R5-E-expectation-freeze/AC-001: expected payload and hash are frozen.

    GIVEN a paper-research decision whose prospective expectation is captured
    WHEN the record is frozen
    THEN it carries exactly the frozen SC-10 expectation keys, its expected
    payload hash is the content hash of that payload, its cutoff is preserved,
    and every field refuses mutation
    """

    record = expectation()

    assert assert_expectation_record_keys(record) == EXPECTATION_RECORD_KEYS
    assert record.expectation_id.startswith(f"{EXPECTATION_ID_PREFIX}:")
    assert record.expected_hash == expected_payload_hash(record.expected_payload)
    assert record.expected_hash.startswith(f"{EXPECTED_PAYLOAD_HASH_PREFIX}:")
    assert record.cutoff == CUTOFF
    assert record.maturity_at == MATURITY
    assert record.recorded_at == T0
    assert record.schema_version == EXPECTATION_SCHEMA_VERSION
    assert record.authoritative is False
    assert record.can_promote is False
    assert record.correction_of is None

    with pytest.raises(FrozenInstanceError):
        record.expected_hash = "PEXPP:forged"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.cutoff = MATURITY  # type: ignore[misc]

    # Re-freezing the identical decision reproduces the identical frozen record.
    assert expectation() == record


def test_ac_001_a_mutated_payload_or_forged_hash_fails_closed() -> None:
    """ATDD-R5-E-expectation-freeze/AC-001: a mutated payload cannot keep its id.

    GIVEN a frozen expectation
    WHEN its expected payload is substituted or its stored hash is forged
    THEN the substitution and the forgery are both refused
    """

    record = expectation()

    with pytest.raises(ExpectationError):
        replace(record, expected_payload=payoff(net=99.0))
    with pytest.raises(ExpectationError):
        replace(record, expected_hash="PEXPP:00000000000000000000000000000000")
    with pytest.raises(ExpectationError):
        replace(record, cutoff=MATURITY, maturity_at=MATURITY)
    with pytest.raises(ExpectationError):
        replace(record, maturity_at=CUTOFF)

    # A different decision is a different record, not a mutation of this one.
    other = expectation(grain=grain("EP-R5E-0002"))
    assert other.expectation_id != record.expectation_id


def test_ac_001_a_replayed_payload_with_a_dropped_or_extra_key_fails_closed() -> None:
    """ATDD-R5-E-expectation-freeze/AC-001: replay is content-checked.

    GIVEN a durable expectation payload
    WHEN it is reloaded, then reloaded with a dropped key, an extra key or a
    forged identity
    THEN the round trip is exact and each forgery fails closed
    """

    record = expectation()
    payload = record.as_dict()
    assert ProspectiveExpectation.from_dict(payload) == record

    dropped = dict(payload)
    dropped.pop("population")
    with pytest.raises(ExpectationError):
        ProspectiveExpectation.from_dict(dropped)

    extra = dict(payload)
    extra["authority"] = "granted"
    with pytest.raises(ExpectationError):
        ProspectiveExpectation.from_dict(extra)

    forged = dict(payload)
    forged["expectation_id"] = "PEXP:00000000000000000000000000000000"
    with pytest.raises(ExpectationError):
        ProspectiveExpectation.from_dict(forged)

    swapped = dict(payload)
    swapped["expected_hash"] = expected_payload_hash(payoff(net=1.0))
    with pytest.raises(ExpectationError):
        ProspectiveExpectation.from_dict(swapped)


# ---------------------------------------------------------------------------
# AC-002 Cutoff and maturity precede every outcome
# ---------------------------------------------------------------------------


def test_ac_002_an_outcome_before_the_cutoff_or_maturity_is_refused() -> None:
    """ATDD-R5-E-expectation-freeze/AC-002: no outcome precedes its expectation.

    GIVEN a frozen expectation with a cutoff and a maturity horizon
    WHEN it is frozen after its cutoff, or an outcome is attached at or before
    the cutoff, before maturity, or with no usable facts
    THEN each attempt is refused
    """

    with pytest.raises(ExpectationError):
        expectation(recorded_at=CUTOFF + timedelta(seconds=1))

    record = expectation()

    with pytest.raises(ExpectationError):
        attach_outcome(record, facts(), recorded_at=CUTOFF)
    with pytest.raises(ExpectationError):
        attach_outcome(record, facts(), recorded_at=CUTOFF - timedelta(seconds=1))
    with pytest.raises(ExpectationError):
        attach_outcome(record, facts(), recorded_at=MATURITY - timedelta(seconds=1))
    with pytest.raises(ExpectationError):
        attach_outcome(record, None, recorded_at=AFTER_MATURITY)
    with pytest.raises(ExpectationError):
        attach_outcome(record, facts(fills=0), recorded_at=AFTER_MATURITY)
    with pytest.raises(ExpectationError):
        attach_outcome(record, facts(net=float("nan")), recorded_at=AFTER_MATURITY)

    attachment = attach_outcome(record, facts(), recorded_at=AFTER_MATURITY)
    assert attachment.recorded_at == AFTER_MATURITY
    assert attachment.expectation_id == record.expectation_id
    assert attachment.facts.fill_count == 2


def test_ac_002_a_refused_attachment_leaves_the_expectation_byte_identical() -> None:
    """ATDD-R5-E-expectation-freeze/AC-002: a refusal changes nothing.

    GIVEN a frozen expectation
    WHEN several illegal attachments are attempted against it
    THEN the frozen record is unchanged, and a legal attachment is a separate
    record that never rewrites the expectation it measures
    """

    record = expectation()
    before = record.as_dict()

    for attempt in (
        lambda: attach_outcome(record, facts(), recorded_at=CUTOFF),
        lambda: attach_outcome(record, None, recorded_at=AFTER_MATURITY),
        lambda: attach_outcome(record, facts(fills=0), recorded_at=AFTER_MATURITY),
    ):
        with pytest.raises(ExpectationError):
            attempt()

    assert record.as_dict() == before
    assert record == expectation()

    attachment = attach_outcome(record, facts(), recorded_at=AFTER_MATURITY)
    assert attachment.as_dict()["expectation_id"] == record.expectation_id
    assert "facts" not in record.as_dict()
    assert record.as_dict() == before


# ---------------------------------------------------------------------------
# AC-003 Populations at a deduplicated grain
# ---------------------------------------------------------------------------


def test_ac_003_each_population_is_retained_at_a_deduplicated_grain() -> None:
    """ATDD-R5-E-expectation-freeze/AC-003: every population is retained.

    GIVEN the retained populations of one evaluated decision span
    WHEN expectations are captured for taken, rejected, missed, expired, no-fill
    and no-trade episodes
    THEN all six populations are present, each expectation carries exactly one of
    them, and two populations of one grain stay two records rather than merging
    """

    assert POPULATIONS == (
        "TAKEN",
        "REJECTED",
        "MISSED",
        "EXPIRED",
        "NO_FILL",
        "NO_TRADE",
    )

    rows = [
        expectation_for(population, index)
        for index, population in enumerate(Population, start=1)
    ]
    assert assert_expectation_populations_are_separate(rows) == POPULATIONS

    shared = grain("EP-R5E-SHARED")
    taken = expectation(grain=shared, population=Population.TAKEN)
    missed = expectation(grain=shared, population=Population.MISSED)
    assert taken.expectation_id != missed.expectation_id
    assert set(
        assert_expectation_populations_are_separate(
            [taken, missed], required=("TAKEN", "MISSED")
        )
    ) == {"TAKEN", "MISSED"}


def test_ac_003_a_duplicate_grain_is_refused_and_no_population_is_dropped() -> None:
    """ATDD-R5-E-expectation-freeze/AC-003: deduplication fails closed.

    GIVEN captured expectations for one span
    WHEN the same grain and population is recorded twice, or one retained
    population is missing, or an unknown population token is used
    THEN the duplicate and the omission are refused and the unknown token cannot
    be expressed at all
    """

    taken = expectation(grain=grain("EP-R5E-0100"), population=Population.TAKEN)
    duplicate = expectation(grain=grain("EP-R5E-0100"), population=Population.TAKEN)
    assert duplicate.expectation_id == taken.expectation_id
    with pytest.raises(ExpectationError):
        assert_expectation_populations_are_separate([taken, duplicate])

    complete = [
        expectation_for(population, index)
        for index, population in enumerate(Population, start=1)
    ]
    incomplete = complete[:-1]
    with pytest.raises(ExpectationError):
        assert_expectation_populations_are_separate(incomplete)

    assert assert_expectation_populations_are_separate(
        list(reversed(incomplete)),
        required=tuple(population.value for population in Population)[:-1],
    ) == POPULATIONS[:-1]

    with pytest.raises(ExpectationError):
        expectation(population="TAKEN_AND_MISSED")


# ---------------------------------------------------------------------------
# AC-004 The outcome status vocabulary
# ---------------------------------------------------------------------------


def test_ac_004_the_outcome_status_vocabulary_is_closed_and_reasoned() -> None:
    """ATDD-R5-E-expectation-freeze/AC-004: statuses are closed and reasoned.

    GIVEN a frozen expectation with and without a matured outcome
    WHEN its status is resolved
    THEN each status comes from the closed vocabulary with at least one explicit
    reason, and a reason that belongs to another status is refused
    """

    assert {status.value for status in OutcomeStatus} == {
        "PENDING",
        "MATURED",
        "CENSORED",
        "INSUFFICIENT_EVIDENCE",
    }
    assert set(OUTCOME_STATUS_REASONS) == {
        "PENDING",
        "MATURED",
        "CENSORED",
        "INSUFFICIENT_EVIDENCE",
    }

    record = expectation()

    pending = resolve_outcome_status(
        record, evaluated_at=MATURITY - timedelta(seconds=1)
    )
    assert pending.status is OutcomeStatus.PENDING
    assert pending.reasons == (OutcomeStatusReason.HORIZON_NOT_REACHED,)

    unfounded = resolve_outcome_status(record, evaluated_at=AFTER_MATURITY)
    assert unfounded.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert OutcomeStatusReason.NO_OUTCOME_FACTS in unfounded.reasons

    attachment = attach_outcome(record, facts(), recorded_at=AFTER_MATURITY)

    uncovered = resolve_outcome_status(
        record, attachment=attachment, evaluated_at=AFTER_MATURITY
    )
    assert uncovered.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert OutcomeStatusReason.COVERAGE_MISSING in uncovered.reasons

    matured = resolve_outcome_status(
        record, attachment=attachment, evaluated_at=AFTER_MATURITY, coverage=0.75
    )
    assert matured.status is OutcomeStatus.MATURED
    assert matured.reasons == (OutcomeStatusReason.MATURED_WITH_FACTS,)

    censored = resolve_outcome_status(
        record,
        attachment=attachment,
        evaluated_at=AFTER_MATURITY,
        coverage=0.75,
        censored=OutcomeStatusReason.CENSORED_BY_WINDOW_END,
    )
    assert censored.status is OutcomeStatus.CENSORED
    assert censored.reasons == (OutcomeStatusReason.CENSORED_BY_WINDOW_END,)

    with pytest.raises(ExpectationError):
        resolve_outcome_status(
            record,
            attachment=attachment,
            evaluated_at=AFTER_MATURITY,
            coverage=0.75,
            censored=OutcomeStatusReason.MATURED_WITH_FACTS,
        )
    with pytest.raises(ExpectationError):
        resolve_outcome_status(
            record,
            evaluated_at=AFTER_MATURITY,
            reasons=(OutcomeStatusReason.CENSORED_BY_WINDOW_END,),
        )
    with pytest.raises(ExpectationError):
        resolve_outcome_status(record, attachment=attachment, coverage=0.5)


def test_ac_004_an_immature_window_can_never_mature() -> None:
    """ATDD-R5-E-expectation-freeze/AC-004: an immature window never succeeds.

    GIVEN an expectation whose horizon has not been reached
    WHEN its status is resolved with a full attachment
    THEN it is censored rather than matured, maturity begins only at the declared
    horizon, and zero coverage is not evidence of maturity
    """

    record = expectation()
    attachment = attach_outcome(record, facts(), recorded_at=AFTER_MATURITY)

    immature = resolve_outcome_status(
        record,
        attachment=attachment,
        evaluated_at=MATURITY - timedelta(seconds=1),
        coverage=1.0,
    )
    assert immature.status is OutcomeStatus.CENSORED
    assert immature.reasons == (OutcomeStatusReason.CENSORED_BY_IMMATURE_WINDOW,)

    at_horizon = resolve_outcome_status(
        record, attachment=attachment, evaluated_at=MATURITY, coverage=1.0
    )
    assert at_horizon.status is OutcomeStatus.MATURED

    no_coverage = resolve_outcome_status(
        record, attachment=attachment, evaluated_at=AFTER_MATURITY, coverage=0.0
    )
    assert no_coverage.status is OutcomeStatus.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# AC-005 Separate measures
# ---------------------------------------------------------------------------


def test_ac_005_the_three_measure_kinds_stay_separate() -> None:
    """ATDD-R5-E-expectation-freeze/AC-005: measure kinds never mix in a slot.

    GIVEN realized paper outcomes, declared-policy counterfactuals and hindsight
    excursion measures
    WHEN they are recorded against a frozen expectation
    THEN each measure carries exactly one kind, the three kinds stay in their own
    slots, and a duplicate kind at one grain is refused
    """

    assert MEASURE_KINDS == (
        "REALIZED_PAPER_OUTCOME",
        "DECLARED_POLICY_COUNTERFACTUAL",
        "HINDSIGHT_EXCURSION",
    )

    measures = tuple(
        MeasureRecord(kind=kind, grain_id="EP-R5E-0001", value=float(index))
        for index, kind in enumerate(MeasureKind, start=1)
    )
    assert assert_measures_stay_separate(measures) == MEASURE_KINDS

    duplicate = measures + (
        MeasureRecord(
            kind=MeasureKind.REALIZED_PAPER_OUTCOME,
            grain_id="EP-R5E-0001",
            value=1.0,
        ),
    )
    with pytest.raises(ExpectationError):
        assert_measures_stay_separate(duplicate)

    with pytest.raises(ExpectationError):
        MeasureRecord(
            kind=("REALIZED_PAPER_OUTCOME", "HINDSIGHT_EXCURSION"),
            grain_id="EP-R5E-0001",
            value=1.0,
        )
    with pytest.raises(ExpectationError):
        MeasureRecord(
            kind="REALIZED_AND_COUNTERFACTUAL", grain_id="EP-R5E-0001", value=1.0
        )

    # One slot per kind per grain keeps the populations apart, so a second grain
    # is a second slot rather than a second kind.
    separate_grain = measures + (
        MeasureRecord(
            kind=MeasureKind.REALIZED_PAPER_OUTCOME, grain_id="EP-R5E-0002", value=5.0
        ),
    )
    assert assert_measures_stay_separate(separate_grain) == MEASURE_KINDS


def test_ac_005_a_missed_winner_is_never_summed_into_a_realized_total() -> None:
    """ATDD-R5-E-expectation-freeze/AC-005: incompatible measures are not summed.

    GIVEN a realized paper outcome, a counterfactual measure and a hindsight
    excursion measure for one grain
    WHEN a total is requested
    THEN only one kind can be totalled, a mixed request is refused, and the module
    exposes no cross-kind total
    """

    measures = (
        MeasureRecord(
            kind=MeasureKind.REALIZED_PAPER_OUTCOME, grain_id="EP-R5E-0001", value=10.0
        ),
        MeasureRecord(
            kind=MeasureKind.DECLARED_POLICY_COUNTERFACTUAL,
            grain_id="EP-R5E-0001",
            value=25.0,
        ),
        MeasureRecord(
            kind=MeasureKind.HINDSIGHT_EXCURSION,
            grain_id="EP-R5E-0001",
            value=40.0,
        ),
    )

    assert total_for_kind(
        tuple(
            row for row in measures if row.kind is MeasureKind.REALIZED_PAPER_OUTCOME
        ),
        kind=MeasureKind.REALIZED_PAPER_OUTCOME,
    ) == pytest.approx(10.0)
    assert total_for_kind(
        tuple(row for row in measures if row.kind is MeasureKind.HINDSIGHT_EXCURSION),
        kind=MeasureKind.HINDSIGHT_EXCURSION,
    ) == pytest.approx(40.0)

    with pytest.raises(ExpectationError):
        total_for_kind(measures, kind="REALIZED_AND_HINDSIGHT")
    with pytest.raises(ExpectationError):
        total_for_kind(measures, kind=MeasureKind.REALIZED_PAPER_OUTCOME)
    with pytest.raises(ExpectationError):
        total_for_kind(
            (measures[0], measures[2]), kind=MeasureKind.REALIZED_PAPER_OUTCOME
        )

    names = function_names(EVALUATION_MODULE)
    assert names & set(FORBIDDEN_CROSS_KIND_TOTALS) == set()
    assert not any(
        "measures" in name and name.startswith("total_") and name != "total_for_kind"
        for name in names
    )


# ---------------------------------------------------------------------------
# AC-006 No authority, no storage, no consumer
# ---------------------------------------------------------------------------


def test_ac_006_the_expectation_surface_grants_no_authority_or_storage() -> None:
    """ATDD-R5-E-expectation-freeze/AC-006: evidence, not authority.

    GIVEN the R5-E section of the SC-10 owning module
    WHEN its tokens, imports and the neighbouring module names are inspected
    THEN it carries no order, execution, storage, scheduler or venue surface, it
    reads no clock, no randomness and no local file, and it created no parallel
    expectation or outcome module
    """

    section = r5e_section(EVALUATION_MODULE)
    assert section

    for token in FORBIDDEN_AUTHORITY_TOKENS:
        assert token not in section, token
    for token in FORBIDDEN_PERSISTENCE_TOKENS:
        assert token not in section, token
    for token in (
        "environ",
        "utcnow",
        "datetime.now",
        "time.time",
        "open(",
        "uuid",
        "random",
    ):
        assert token not in section, token

    assert module_imports(EVALUATION_MODULE) & FORBIDDEN_PRIMITIVES == set()

    record = expectation()
    assert record.authoritative is False
    assert record.can_promote is False
    payload = record.as_dict()
    assert payload["authoritative"] is False
    assert payload["can_promote"] is False

    learning_dir = EVALUATION_MODULE.parent
    for parallel in (
        "expectations.py",
        "expectation_store.py",
        "expectation_engine.py",
        "outcome_engine.py",
        "expectation_registry.py",
    ):
        assert not (learning_dir / parallel).exists(), parallel

    contracts_dir = APP_ROOT / "app" / "opip" / "contracts"
    for parallel in (
        "expectation.py",
        "prospective_expectation.py",
        "expectation_store.py",
    ):
        assert not (contracts_dir / parallel).exists(), parallel


def test_ac_006_no_runtime_root_consumes_the_expectation_records() -> None:
    """ATDD-R5-E-expectation-freeze/AC-006: dormancy is proved, not assumed.

    GIVEN the trading, discovery, decision and risk runtime roots and the rest of
    the application package
    WHEN references to the expectation surface are searched for
    THEN nothing outside the owning module consumes it, so this increment has no
    runtime consumer and no admission path
    """

    consumer_tokens = (
        "ProspectiveExpectation",
        "freeze_expectation",
        "ExpectationGrain",
        "expected_payload_hash",
        "EXPECTATION_ID_PREFIX",
    )

    for root in RUNTIME_ROOTS:
        for path in sorted(root.rglob("*.py")):
            source = module_source(path)
            for token in consumer_tokens:
                assert token not in source, (path, token)

    consumers = []
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if path == EVALUATION_MODULE:
            continue
        source = module_source(path)
        if any(token in source for token in consumer_tokens):
            consumers.append(path.relative_to(APP_ROOT).as_posix())
    assert consumers == []


# ---------------------------------------------------------------------------
# AC-007 The increment boundary: governance and posture
# ---------------------------------------------------------------------------


def test_ac_007_the_increment_scope_contract_is_self_consistent() -> None:
    """ATDD-R5-E-expectation-freeze/AC-007: the contract traces to its tests.

    GIVEN this increment, its scope contract and the active pointer
    WHEN the contract is parsed and checked against the acceptance index
    THEN the pointer resolves to an existing scope contract (the CI scope job
    owns exact current-increment enforcement), every criterion is traced to a
    matching acceptance test and mapped to a changed file, and this module pins
    no global pointer value
    """

    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    ours = [contract for contract in contracts if contract.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert CONTRACT.name == f"{INCREMENT}.md"
    assert contract.sections["INCREMENT"] == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"

    pointer_name = read_increment_pointer(POINTER)
    assert pointer_name in {item.increment for item in contracts}

    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id

    assert set(contract.sections) == {
        "INCREMENT",
        "OWNER-APPROVED INTENT",
        "ARCHITECTURE REFERENCES",
        "APPROVED ACCEPTANCE CRITERIA",
        "EXPLICITLY OUT OF SCOPE",
        "FROZEN BOUNDARIES",
        "ACCEPTANCE TEST TRACEABILITY",
        "IMPLEMENTATION MAP",
        "DEFERRED DISCOVERIES",
        "UNAPPROVED SCOPE CHANGES",
    }

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons
    assert global_pointer_pin_violations(module_source(Path(__file__))) == []


def test_ac_007_release_profile_and_non_authority_posture_are_unchanged() -> None:
    """ATDD-R5-E-expectation-freeze/AC-007: the posture is preserved.

    GIVEN the repository release-profile contract and the Compose literals
    WHEN they are inspected
    THEN `EVIDENCE_SHADOW` remains the active shadow profile with `TARGET_PAPER`
    blocked, paper-v2 stays off, this increment adds no mode key, and the frozen
    expectation still declares itself non-authoritative and non-promotable
    """

    profiles = get_release_profiles()
    assert set(profiles) == {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}
    assert validate_profile_contract("EVIDENCE_SHADOW") == (True, [])
    valid, reasons = validate_profile_contract("TARGET_PAPER")
    assert valid is False
    assert reasons and "blocked" in reasons[0]

    allowed = profiles["EVIDENCE_SHADOW"]["allowed_modes"]
    assert allowed["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert allowed["OPIP_CANONICAL_WRITER_MODE"] == "shadow"
    assert allowed["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert allowed["OPIP_PAPER_V2_MODE"] == "off"
    assert not any(key.startswith("OPIP_R5") for key in allowed)

    compose = yaml.safe_load((APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["ohm-trade-agent"]["environment"]
    assert environment["OPIP_PAPER_V2_MODE"] == "off"
    assert environment["OPIP_FEATURE_BUS_MODE"] == "shadow"

    record = expectation()
    assert record.authoritative is False
    assert record.can_promote is False
    assert MEASURE_KINDS == (
        "REALIZED_PAPER_OUTCOME",
        "DECLARED_POLICY_COUNTERFACTUAL",
        "HINDSIGHT_EXCURSION",
    )
