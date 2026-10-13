"""ATDD-R5-F prospective outcome integration for the learning plane.

Increment: `ATDD-R5-F-prospective-outcome-integration`. Every test cites its own
criterion in its docstring, and the contract
`docs/atdd/scope-contracts/ATDD-R5-F-prospective-outcome-integration.md` is the
authority for what each one means.

This increment joins the frozen prospective expectation R5-E already captures
(`ATDD-R5-E-expectation-freeze`, `SC-10`) to the committed canonical terminal
paper outcome the canonical writer already owns (`SC-12`), and makes that join
economic, governed and non-authoritative: ex-ante capture without hindsight, a
verified canonical attachment, a population economic truth that is a ratio of
sums, capital-time and horizon evaluation, root-cause classification with repair
lineage, one named consumption disposition per artifact, and a paired
prospective champion/challenger comparison against one frozen baseline.

Nothing new is built here: no engine, no ledger, no scheduler, no writer, no
store, no event type, no release profile, no mode key, no order path and no
trading authority. The surface is dormant evidence code inside the `SC-10`
owning module with no runtime consumer, and `AC-009` is this increment's own
bookkeeping guard. Everything the tests feed in is real: an expectation frozen
by the production capture seam, a terminal outcome committed by the production
canonical writer and read back through the production read-only reader.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
import pathlib
import sys

import pytest
import yaml

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.canonical.models import WriterIntent  # noqa: E402
from app.opip.canonical.paths import SCHEMA_VERSION  # noqa: E402
from app.opip.canonical.writer import CanonicalWriter  # noqa: E402
from app.opip.contracts.paper_outcome import (  # noqa: E402
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    build_terminal_outcome_payload,
    terminal_outcome_idempotency_key,
)
from app.opip.contracts.serialization import iso_z  # noqa: E402
from app.opip.learning.evaluation import (  # noqa: E402
    DISPOSITION_PRECEDENCE,
    EXPECTATION_ID_PREFIX,
    LEARNING_DISPOSITIONS,
    MEASURE_KINDS,
    OUTCOME_STATUS_REASONS,
    ROOT_CAUSE_PRECEDENCE,
    AttachedOutcome,
    EvaluationSupport,
    ExpectationError,
    LearningConsumptionDisposition,
    LearningDisposition,
    LearningRootCause,
    MeasureKind,
    MeasureRecord,
    OutcomeStatus,
    OutcomeStatusReason,
    Population,
    ProspectiveExpectation,
    assert_every_artifact_has_one_disposition,
    assert_expectation_populations_are_separate,
    assess_unattached_expectation,
    attach_canonical_outcome,
    capture_expectation_from_decision,
    classify_root_cause,
    compare_prospective_arms,
    disposition_for,
    evaluate_capital_time,
    evaluate_population_economics,
    expected_payload_hash,
    mark_unusable_expectation,
    outcome_facts_from_canonical_outcome,
    realized_outcome_measures,
    resolve_outcome_status,
    resolve_repair_lineage,
    total_for_kind,
)
from app.opip.learning.paper_outcome_reader import (  # noqa: E402
    read_canonical_paper_outcomes,
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
from tests.test_atdd_scope_control import global_pointer_pin_violations  # noqa: E402

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-F-prospective-outcome-integration"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
EVALUATION_MODULE = APP_ROOT / "app" / "opip" / "learning" / "evaluation.py"
R5F_BANNER = "# R5-F prospective outcome integration"

#: Only the `SC-10` owning module may carry the integrated learning surface.
R5F_ONLY_TOKENS = (
    "LearningDisposition",
    "LearningRootCause",
    "AttachedOutcome",
    "attach_canonical_outcome",
    "capture_expectation_from_decision",
    "evaluate_population_economics",
    "evaluate_capital_time",
    "compare_prospective_arms",
)

FORBIDDEN_RUNTIME_TOKENS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "create_order",
    "api_key",
    "api_secret",
    "credential",
    "writer_client",
    "submit_decision_context",
    "submit_decision_snapshot",
    "submit_canonical_event",
    "parquet",
    "redis",
    "telegram",
    "environ",
    "utcnow",
    "datetime.now",
    "time.time",
    "uuid",
)

T0 = datetime(2026, 4, 6, 8, 0, tzinfo=timezone.utc)
CUTOFF = T0
HORIZON_SECONDS = 86400.0
MATURITY = T0 + timedelta(seconds=HORIZON_SECONDS)
ENTER = T0 + timedelta(hours=1)
EXIT = T0 + timedelta(hours=7)
BASELINE_ID = "BASELINE-R5F-FROZEN-1"
HORIZON = "SWING"
CHAMPION_POLICY = "policy-r5f-champion"
CHALLENGER_POLICY = "policy-r5f-challenger"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def module_source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def r5f_section() -> str:
    source = module_source(EVALUATION_MODULE)
    assert R5F_BANNER in source, R5F_BANNER
    return source[source.index(R5F_BANNER) :]


def decision_evidence(**overrides) -> dict:
    """One ex-ante decision evidence record, as the capture seam requires it."""

    values: dict = {
        "grain_id": "EP-R5F-0001",
        "entity": "BTCUSD",
        "policy_id": CHAMPION_POLICY,
        "horizon": HORIZON,
        "revision": 1,
        "population": Population.TAKEN.value,
        "decision_at_utc": iso_z(T0 - timedelta(minutes=30)),
        "cutoff": iso_z(CUTOFF),
        "maturity_at": iso_z(MATURITY),
        "recorded_at": iso_z(CUTOFF),
        "baseline_id": BASELINE_ID,
        "baseline_description": "the frozen declared-policy baseline this grain is measured against",
        "measure_kinds": [MeasureKind.REALIZED_PAPER_OUTCOME.value],
        "correction_of": None,
        "expected_net_return": 20.0,
        "expected_duration_seconds": HORIZON_SECONDS / 2.0,
        "expected_cost_dollars": 5.0,
        "expected_horizon_seconds": HORIZON_SECONDS,
    }
    values.update(overrides)
    return values


def frozen_expectation(**overrides) -> ProspectiveExpectation:
    return capture_expectation_from_decision(decision_evidence(**overrides))


def canonical_payload(
    *,
    paper_trade_id: str = "PAPER:" + "a" * 20,
    episode_id: str = "EP-R5F-0001",
    gross: float = 25.0,
    fees: float = 4.0,
    capital: float = 1000.0,
    entry: datetime = ENTER,
    exit_at: datetime = EXIT,
    terminal_status: str = "CLOSED",
    exit_reason: str | None = None,
) -> dict:
    """A terminal outcome payload built by the production contract builder."""

    if exit_reason is None:
        exit_reason = "UNRESOLVED" if terminal_status == "UNRESOLVED" else "TARGET_2"

    if terminal_status == "UNRESOLVED":
        gross_value: float | None = None
        fees_value: float | None = None
        net_value: float | None = None
        pct_value: float | None = None
        capital_value: float | None = None
    elif terminal_status == "CANCELLED":
        gross_value = fees_value = net_value = pct_value = 0.0
        capital_value = capital
    else:
        gross_value = gross
        fees_value = fees
        net_value = gross - fees
        pct_value = ((gross - fees) / capital) * 100.0
        capital_value = capital
    return build_terminal_outcome_payload(
        engine="OHM_PAPER_SIM_V1",
        paper_trade_id=paper_trade_id,
        episode_id=episode_id,
        cohort_id="COH-R5F-1",
        strategy_version="OPIP-STRATEGY-V1",
        exchange="KRAKEN",
        native_symbol="BTCUSD",
        base_asset="BTC",
        direction="LONG",
        quote_currency="USD",
        terminal_status=terminal_status,
        exit_reason=exit_reason,
        exit_price=101.0,
        entry_timestamp=entry,
        exit_timestamp=exit_at,
        capital_committed=capital_value,
        gross_pnl=gross_value,
        fees_paid=fees_value,
        net_pnl=net_value,
        net_pnl_pct=pct_value,
        final_revision=3,
        terminal_event_id="PTE:" + "b" * 24,
        candidate_id="CAND-R5F-1",
        decision_context_id="DI-CONTEXT:" + "c" * 32,
    )



def attached_outcome(
    *,
    grain_id: str = "EP-R5F-0001",
    population: Population = Population.TAKEN,
    policy_id: str = CHAMPION_POLICY,
    revision: int = 1,
    baseline_id: str = BASELINE_ID,
    horizon: str = HORIZON,
    gross: float = 25.0,
    fees: float = 4.0,
    capital: float = 1000.0,
    entry: datetime = ENTER,
    exit_at: datetime = EXIT,
    coverage: float | None = 1.0,
    committed_fill_count: int = 1,
    expected_net_return: float = 20.0,
    expected_cost_dollars: float = 5.0,
    expected_duration_seconds: float = HORIZON_SECONDS / 2.0,
    expected_horizon_seconds: float = HORIZON_SECONDS,
    moment: datetime | None = None,
) -> AttachedOutcome:
    """One frozen expectation joined to one committed canonical outcome."""

    expectation = frozen_expectation(
        grain_id=grain_id,
        population=population.value,
        policy_id=policy_id,
        revision=revision,
        baseline_id=baseline_id,
        horizon=horizon,
        expected_net_return=expected_net_return,
        expected_cost_dollars=expected_cost_dollars,
        expected_duration_seconds=expected_duration_seconds,
        expected_horizon_seconds=expected_horizon_seconds,
    )
    payload = canonical_payload(
        paper_trade_id="PAPER:" + grain_id.replace("-", "").lower().ljust(20, "z")[:20],
        episode_id=grain_id,
        gross=gross,
        fees=fees,
        capital=capital,
        entry=entry,
        exit_at=exit_at,
    )
    recorded_at = (
        moment if moment is not None else max(MATURITY, exit_at) + timedelta(minutes=1)
    )
    return attach_canonical_outcome(
        expectation,
        payload,
        committed_fill_count=committed_fill_count,
        recorded_at=recorded_at,
        coverage=coverage,
    )


def unattached_outcome(
    *,
    grain_id: str = "EP-R5F-0002",
    population: Population = Population.REJECTED,
    policy_id: str = CHALLENGER_POLICY,
    baseline_id: str = BASELINE_ID,
    horizon: str = HORIZON,
    evaluated_at: datetime | None = None,
    coverage: float | None = None,
    censored: OutcomeStatusReason | None = None,
) -> AttachedOutcome:
    """One frozen expectation with no committed outcome attached to it."""

    expectation = frozen_expectation(
        grain_id=grain_id,
        population=population.value,
        policy_id=policy_id,
        baseline_id=baseline_id,
        horizon=horizon,
    )
    moment = evaluated_at if evaluated_at is not None else MATURITY + timedelta(minutes=1)
    return assess_unattached_expectation(
        expectation, evaluated_at=moment, coverage=coverage, censored=censored
    )


def seed_canonical(db_path: pathlib.Path, payloads: list[dict]) -> None:
    """Commit canonical terminal outcomes through the real production writer."""

    writer = CanonicalWriter(db_path)
    try:
        for payload in payloads:
            ack = writer.submit(
                WriterIntent(
                    schema_version=SCHEMA_VERSION,
                    priority=PAPER_OUTCOME_PRIORITY,  # type: ignore[arg-type]
                    idempotency_key=terminal_outcome_idempotency_key(
                        payload["outcome_id"]
                    ),
                    event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
                    payload=payload,
                )
            )
            assert ack.status == "OK", ack.detail
    finally:
        writer.close()


@pytest.fixture
def canonical_store(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "canonical"
    root.mkdir()
    return root / "opip_canonical_v1.sqlite3"


# ---------------------------------------------------------------------------
# AC-001 Ex-ante capture and verified canonical attachment
# ---------------------------------------------------------------------------


def test_ac_001_an_ex_ante_decision_capture_is_frozen_at_or_before_its_cutoff() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-001: captured before its cutoff.

    GIVEN one decision evidence record with its own clock, cutoff, maturity horizon
    and expected payload
    WHEN the capture runs at or before the cutoff
    THEN one immutable expectation is frozen with a payload hash derived from that
    payload, an identity derived from its own content, no authority and exactly the
    frozen key set, while a missing key, an extra key and a recording after the
    cutoff are each refused
    """

    record = frozen_expectation()
    assert record.expectation_id.startswith(f"{EXPECTATION_ID_PREFIX}:")
    assert record.expected_hash == expected_payload_hash(record.expected_payload)
    assert record.grain.grain_id == "EP-R5F-0001"
    assert record.population is Population.TAKEN
    assert record.measure_kinds == (MeasureKind.REALIZED_PAPER_OUTCOME,)
    assert record.cutoff == CUTOFF
    assert record.maturity_at == MATURITY
    assert record.recorded_at == CUTOFF
    assert record.authoritative is False
    assert record.can_promote is False
    assert record.correction_of is None

    payload = record.as_dict()
    assert payload["expected_payload"] == record.expected_payload.as_dict()
    assert payload["expected_hash"] == record.expected_hash
    assert payload["authoritative"] is False
    assert payload["can_promote"] is False
    assert ProspectiveExpectation.from_dict(payload).as_dict() == payload

    evidence = decision_evidence()
    without_cutoff = {key: value for key, value in evidence.items() if key != "cutoff"}
    with pytest.raises(ExpectationError):
        capture_expectation_from_decision(without_cutoff)
    with_extra = dict(evidence)
    with_extra["owner_policy_override"] = "edit-the-policy"
    with pytest.raises(ExpectationError):
        capture_expectation_from_decision(with_extra)

    late = decision_evidence(recorded_at=iso_z(CUTOFF + timedelta(seconds=1)))
    with pytest.raises(ExpectationError):
        capture_expectation_from_decision(late)


def test_ac_001_a_decision_clock_after_the_cutoff_is_refused_as_hindsight() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-001: no hindsight contamination.

    GIVEN a decision whose own clock falls after the expectation's cutoff
    WHEN the capture runs
    THEN the capture is refused as hindsight rather than recorded later, while a
    clock exactly at the cutoff is accepted and the expected payload is unchanged
    """

    late = decision_evidence(decision_at_utc=iso_z(CUTOFF + timedelta(seconds=1)))
    with pytest.raises(ExpectationError) as refused:
        capture_expectation_from_decision(late)
    assert "hindsight" in str(refused.value)

    boundary = capture_expectation_from_decision(
        decision_evidence(decision_at_utc=iso_z(CUTOFF))
    )
    assert boundary.cutoff == CUTOFF
    assert boundary.expected_payload.expected_horizon_seconds == HORIZON_SECONDS
    assert boundary.expected_hash == expected_payload_hash(boundary.expected_payload)



def test_ac_001_a_canonical_outcome_attaches_only_with_committed_fill_evidence() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-001: the attachment seam.

    GIVEN a committed canonical terminal outcome and a frozen expectation
    WHEN committed fill evidence is supplied or withheld, and an attachment moment
    is offered before the horizon or before the episode had exited
    THEN the attachment is accepted only with a positive integral committed fill
    count at or after the maturity horizon and never before the episode it
    describes had exited, the committed economics are carried exactly as the
    canonical payload asserts them, and the frozen expectation is left identical
    """

    expectation = frozen_expectation()
    payload = canonical_payload()

    facts = outcome_facts_from_canonical_outcome(payload, committed_fill_count=3)
    assert facts.fill_count == 3
    assert facts.net_return == pytest.approx(21.0)
    assert facts.cost_dollars == pytest.approx(4.0)
    assert facts.duration_seconds == pytest.approx((EXIT - ENTER).total_seconds())

    for withheld in (0, -1, 1.5, True):
        with pytest.raises(ExpectationError):
            outcome_facts_from_canonical_outcome(payload, committed_fill_count=withheld)

    with pytest.raises(ExpectationError):
        attach_canonical_outcome(
            expectation,
            payload,
            committed_fill_count=1,
            recorded_at=MATURITY - timedelta(seconds=1),
            coverage=1.0,
        )

    long_exit = MATURITY + timedelta(hours=2)
    with pytest.raises(ExpectationError):
        attach_canonical_outcome(
            expectation,
            canonical_payload(exit_at=long_exit),
            committed_fill_count=1,
            recorded_at=MATURITY + timedelta(minutes=1),
            coverage=1.0,
        )

    attached = attach_canonical_outcome(
        expectation,
        payload,
        committed_fill_count=1,
        recorded_at=max(MATURITY, EXIT) + timedelta(minutes=1),
        coverage=1.0,
    )
    assert attached.integrity_reason is None
    assert attached.attachment is not None
    assert attached.attachment.facts.fill_count == 1
    assert attached.attachment.expectation_id == expectation.expectation_id
    assert attached.resolution.status is OutcomeStatus.MATURED
    assert attached.expectation.as_dict() == expectation.as_dict()


def test_ac_001_a_defective_canonical_payload_is_refused_and_quarantined() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-001: no fabricated numbers.

    GIVEN a canonical payload that asserts nothing realised, a payload whose result
    is unknowable and a payload whose own net disagrees with its gross minus fees
    WHEN facts are derived from each of them
    THEN each is refused instead of yielding zeros or a guessed figure, and the
    refused artifact is quarantined with its reason recorded rather than dropped
    or reported as a break-even outcome
    """

    expectation = frozen_expectation()
    for status in ("CANCELLED", "UNRESOLVED"):
        with pytest.raises(ExpectationError) as refused:
            outcome_facts_from_canonical_outcome(
                canonical_payload(terminal_status=status), committed_fill_count=1
            )
        assert "realised economics" in str(refused.value)

    tampered = dict(canonical_payload())
    tampered["net_pnl"] = tampered["gross_pnl"] - tampered["fees_paid"] + 50.0
    with pytest.raises(ExpectationError) as mismatched:
        outcome_facts_from_canonical_outcome(tampered, committed_fill_count=1)
    assert "disagrees" in str(mismatched.value)

    quarantined = mark_unusable_expectation(
        expectation,
        evaluated_at=MATURITY + timedelta(minutes=1),
        reason="the canonical payload asserts no evaluable economics",
    )
    assert quarantined.attachment is None
    assert quarantined.integrity_reason == "the canonical payload asserts no evaluable economics"
    assert quarantined.resolution.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert quarantined.resolution.reasons == (OutcomeStatusReason.NO_OUTCOME_FACTS,)
    assert (
        disposition_for(quarantined).disposition is LearningDisposition.QUARANTINED
    )


# ---------------------------------------------------------------------------
# AC-002 Economic truth over committed canonical outcomes
# ---------------------------------------------------------------------------


def test_ac_002_population_economics_is_a_ratio_of_sums_not_a_mean_of_ratios() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-002: economic truth of a population.

    GIVEN one retained population holding a wide-margin episode and a thin-margin
    episode whose per-episode cost shares are very different
    WHEN the population economic truth is evaluated
    THEN the reported cost share is the ratio of the summed costs to the summed
    gross and not the mean of the per-episode ratios, the realized net is the
    gross minus the costs deducted once, the return on committed capital is
    capital-weighted, and support, coverage and uncertainty are reported with
    every promotion and authority flag false
    """

    wide = attached_outcome(grain_id="EP-R5F-1001", gross=100.0, fees=10.0)
    thin = attached_outcome(grain_id="EP-R5F-1002", gross=10.0, fees=9.0)

    economics = evaluate_population_economics(
        [wide, thin], population=Population.TAKEN, minimum_support=2
    )
    assert economics.support == 2
    assert economics.population is Population.TAKEN
    assert economics.kind is MeasureKind.REALIZED_PAPER_OUTCOME
    assert economics.gross_dollars == pytest.approx(110.0)
    assert economics.cost_dollars == pytest.approx(19.0)
    assert economics.net_dollars == pytest.approx(91.0)
    assert economics.net_dollars == pytest.approx(
        economics.gross_dollars - economics.cost_dollars
    )
    assert economics.cost_share == pytest.approx(19.0 / 110.0)
    assert economics.mean_episode_cost_share_diagnostic.mean == pytest.approx(0.5)
    assert (
        abs(economics.cost_share - economics.mean_episode_cost_share_diagnostic.mean)
        > 0.2
    )
    assert economics.net_return_on_capital == pytest.approx(91.0 / 2000.0)
    assert economics.committed_capital == pytest.approx(2000.0)
    assert economics.support_sufficiency is EvaluationSupport.SUFFICIENT
    assert economics.net_expectancy.n == 2
    assert economics.net_expectancy.mean == pytest.approx(45.5)
    assert economics.measurement_only is True
    assert economics.authoritative is False
    assert economics.can_promote is False
    assert economics.affects_live_decisions is False

    declared = evaluate_population_economics([wide, thin], population=Population.TAKEN)
    assert declared.support_sufficiency is EvaluationSupport.INSUFFICIENT
    assert declared.minimum_support == 30


def test_ac_002_a_payload_whose_net_disagrees_with_gross_minus_fees_is_refused() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-002: costs deducted exactly once.

    GIVEN a committed payload whose own net disagrees with its gross minus its fees
    and, separately, the same episode committed with a higher fee
    WHEN facts are derived and the population economics are compared
    THEN the disagreeing payload is refused instead of being trusted, and the extra
    fee reduces the realized net by exactly that fee, so a cost is deducted once
    and never twice
    """

    payload = canonical_payload()
    facts = outcome_facts_from_canonical_outcome(payload, committed_fill_count=1)
    assert facts.net_return == pytest.approx(
        float(payload["gross_pnl"]) - float(payload["fees_paid"])
    )

    disagreeing = dict(payload)
    disagreeing["net_pnl"] = float(payload["gross_pnl"]) - float(payload["fees_paid"]) - 25.0
    with pytest.raises(ExpectationError) as refused:
        outcome_facts_from_canonical_outcome(disagreeing, committed_fill_count=1)
    assert "disagrees" in str(refused.value)

    cheaper = attached_outcome(grain_id="EP-R5F-1101", gross=25.0, fees=4.0)
    dearer = attached_outcome(grain_id="EP-R5F-1102", gross=25.0, fees=8.0)
    cheap = evaluate_population_economics(
        [cheaper], population=Population.TAKEN, minimum_support=2
    )
    dear = evaluate_population_economics(
        [dearer], population=Population.TAKEN, minimum_support=2
    )
    assert cheap.gross_dollars == pytest.approx(dear.gross_dollars)
    assert cheap.cost_dollars == pytest.approx(4.0)
    assert dear.cost_dollars == pytest.approx(8.0)
    assert cheap.net_dollars - dear.net_dollars == pytest.approx(4.0)



def test_ac_002_canonical_outcomes_reach_economics_end_to_end(
    canonical_store: pathlib.Path,
) -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-002: the real committed chain.

    GIVEN terminal paper outcomes committed to the canonical store by the real
    writer and read back through the real read-only reader
    WHEN each committed outcome is joined to its frozen expectation and the
    retained population is evaluated
    THEN every committed outcome reaches the economic truth with the payload's own
    figures, each artifact receives its disposition, and the canonical store is
    left byte-identical because the read is read-only
    """

    payloads = [
        canonical_payload(
            paper_trade_id="PAPER:" + "1" * 20,
            episode_id="EP-R5F-2001",
            gross=30.0,
            fees=5.0,
        ),
        canonical_payload(
            paper_trade_id="PAPER:" + "2" * 20,
            episode_id="EP-R5F-2002",
            gross=-12.0,
            fees=4.0,
        ),
        canonical_payload(
            paper_trade_id="PAPER:" + "3" * 20,
            episode_id="EP-R5F-2003",
            gross=8.0,
            fees=3.0,
        ),
    ]
    seed_canonical(canonical_store, payloads)
    before = canonical_store.read_bytes()

    read = read_canonical_paper_outcomes(canonical_store)
    assert read.stream_present is True
    assert len(read.outcomes) == 3

    attached = [
        attach_canonical_outcome(
            frozen_expectation(grain_id=outcome.episode_id),
            outcome.payload,
            committed_fill_count=1,
            recorded_at=max(MATURITY, EXIT) + timedelta(minutes=1),
            coverage=1.0,
        )
        for outcome in read.outcomes
    ]
    economics = evaluate_population_economics(
        attached, population=Population.TAKEN, minimum_support=2
    )
    assert economics.support == 3
    assert economics.gross_dollars == pytest.approx(
        sum(float(payload["gross_pnl"]) for payload in payloads)
    )
    assert economics.cost_dollars == pytest.approx(
        sum(float(payload["fees_paid"]) for payload in payloads)
    )
    assert economics.net_dollars == pytest.approx(
        sum(float(payload["net_pnl"]) for payload in payloads)
    )

    measures = realized_outcome_measures(attached)
    assert len(measures) == 3
    assert total_for_kind(
        measures, kind=MeasureKind.REALIZED_PAPER_OUTCOME
    ) == pytest.approx(economics.net_dollars)

    dispositions = [disposition_for(row) for row in attached]
    assert {item.artifact_id for item in dispositions} == {
        row.expectation.expectation_id for row in attached
    }
    assert {item.disposition for item in dispositions} == {LearningDisposition.LIVE}
    assert canonical_store.read_bytes() == before


def test_ac_002_realized_measures_never_carry_a_counterfactual_or_hindsight_kind() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-002: separate measure kinds.

    GIVEN two matured realized episodes, a hindsight measure offered at the same
    grain and an unresolved artifact
    WHEN the realized measure series is built and totalled
    THEN the series carries the realized kind alone, a contaminated total is
    refused instead of being summed, a duplicate grain is refused, and an
    artifact without verified facts yields no realized measure at all
    """

    first = attached_outcome(grain_id="EP-R5F-3001", gross=30.0, fees=5.0)
    second = attached_outcome(grain_id="EP-R5F-3002", gross=30.0, fees=5.0)

    measures = realized_outcome_measures([first, second])
    assert {row.kind for row in measures} == {MeasureKind.REALIZED_PAPER_OUTCOME}
    assert len({row.grain_id for row in measures}) == 2
    assert total_for_kind(
        measures, kind=MeasureKind.REALIZED_PAPER_OUTCOME
    ) == pytest.approx(50.0)

    assert set(MEASURE_KINDS) == {
        "REALIZED_PAPER_OUTCOME",
        "DECLARED_POLICY_COUNTERFACTUAL",
        "HINDSIGHT_EXCURSION",
    }

    contaminated = measures + (
        MeasureRecord(
            kind=MeasureKind.HINDSIGHT_EXCURSION,
            grain_id="EP-R5F-3001",
            value=999.0,
        ),
    )
    with pytest.raises(ExpectationError):
        total_for_kind(contaminated, kind=MeasureKind.REALIZED_PAPER_OUTCOME)

    with pytest.raises(ExpectationError):
        realized_outcome_measures([first, first])

    with pytest.raises(ExpectationError):
        realized_outcome_measures([unattached_outcome(grain_id="EP-R5F-3003")])


# ---------------------------------------------------------------------------
# AC-003 Capital time and declared horizon
# ---------------------------------------------------------------------------


def test_ac_003_capital_time_is_occupied_capital_times_realized_duration() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-003: capital time is measured.

    GIVEN two matured attachments of equal committed capital held for one and two
    hours against the horizons their own frozen expectations declared
    WHEN capital time is evaluated for that population
    THEN occupied capital time is the committed capital multiplied by the realized
    duration, the capital time the expectations themselves declared is reported
    beside it, the mean holding time and its uncertainty are reported with support
    and no numeric holding threshold is invented
    """

    short = attached_outcome(
        grain_id="EP-R5F-4001", entry=ENTER, exit_at=ENTER + timedelta(hours=1)
    )
    longer = attached_outcome(
        grain_id="EP-R5F-4002", entry=ENTER, exit_at=ENTER + timedelta(hours=2)
    )

    evaluation = evaluate_capital_time(
        [short, longer], population=Population.TAKEN, minimum_support=2
    )
    assert evaluation.support == 2
    assert evaluation.committed_capital == pytest.approx(2000.0)
    assert evaluation.capital_time_seconds == pytest.approx(
        1000.0 * 3600.0 + 1000.0 * 7200.0
    )
    assert evaluation.expected_capital_time_seconds == pytest.approx(
        1000.0 * HORIZON_SECONDS * 2
    )
    assert evaluation.mean_holding_seconds.n == 2
    assert evaluation.mean_holding_seconds.mean == pytest.approx(5400.0)
    assert evaluation.capital_time_utilisation == pytest.approx(
        (1000.0 * 3600.0 + 1000.0 * 7200.0) / (1000.0 * HORIZON_SECONDS * 2)
    )
    assert evaluation.horizon_breaches == 0
    assert evaluation.horizon_breach_share == pytest.approx(0.0)
    assert evaluation.measurement_only is True
    assert evaluation.affects_live_decisions is False


def test_ac_003_a_position_beyond_its_declared_horizon_is_a_named_breach() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-003: a breach is named.

    GIVEN one attachment held inside the horizon its expectation declared and one
    held well beyond the horizon that same expectation declared
    WHEN capital time is evaluated and each attachment's root cause is classified
    THEN the over-held episode is counted and shared as a horizon breach and is
    classified as a horizon mismatch with its own reason, instead of being
    smoothed away or attributed to another cause
    """

    within = attached_outcome(
        grain_id="EP-R5F-4101", entry=ENTER, exit_at=ENTER + timedelta(hours=2)
    )
    beyond = attached_outcome(
        grain_id="EP-R5F-4102",
        entry=ENTER,
        exit_at=ENTER + timedelta(hours=3),
        expected_duration_seconds=1800.0,
        expected_horizon_seconds=3600.0,
    )

    evaluation = evaluate_capital_time(
        [within, beyond], population=Population.TAKEN, minimum_support=2
    )
    assert evaluation.horizon_breaches == 1
    assert evaluation.horizon_breach_share == pytest.approx(0.5)

    breached = classify_root_cause(beyond)
    assert breached.root_cause is LearningRootCause.HORIZON_MISMATCH
    assert "REALISED_DURATION_BEYOND_DECLARED_HORIZON" in breached.reasons
    assert breached.measurement_only is True
    assert breached.affects_live_decisions is False
    assert (
        classify_root_cause(within).root_cause is not LearningRootCause.HORIZON_MISMATCH
    )


# ---------------------------------------------------------------------------
# AC-004 Populations stay separate
# ---------------------------------------------------------------------------


def test_ac_004_a_mixed_population_economic_total_is_refused() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-004: never one combined number.

    GIVEN a matured taken attachment and a matured attachment of another retained
    population
    WHEN one economic total is requested over both of them, and when a total is
    requested for a population that is not present at all
    THEN the mixed request and the absent population are each refused instead of
    producing one combined figure
    """

    taken = attached_outcome(grain_id="EP-R5F-5001")
    rejected = attached_outcome(grain_id="EP-R5F-5002", population=Population.REJECTED)

    with pytest.raises(ExpectationError) as refused:
        evaluate_population_economics(
            [taken, rejected], population=Population.TAKEN, minimum_support=2
        )
    assert "population" in str(refused.value)

    with pytest.raises(ExpectationError):
        evaluate_population_economics([], population=Population.TAKEN, minimum_support=2)

    with pytest.raises(ExpectationError):
        evaluate_population_economics(
            [taken], population=Population.EXPIRED, minimum_support=2
        )

    with pytest.raises(ExpectationError):
        evaluate_capital_time(
            [taken, rejected], population=Population.TAKEN, minimum_support=2
        )


def test_ac_004_every_retained_population_is_measured_in_its_own_slot() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-004: every population is kept.

    GIVEN one frozen expectation per LE-01 population and matured attachments in
    two different populations
    WHEN the retained set is checked and each population is evaluated
    THEN all six populations are retained at their own grain, each population is
    evaluated only against attachments of that same population, and no population
    is silently dropped
    """

    expectations = [
        frozen_expectation(
            grain_id=f"EP-R5F-6{index:03d}", population=population.value
        )
        for index, population in enumerate(Population, start=1)
    ]
    retained = assert_expectation_populations_are_separate(expectations)
    assert set(retained) == {population.value for population in Population}
    assert len(retained) == 6

    taken = attached_outcome(grain_id="EP-R5F-7001", population=Population.TAKEN)
    missed = attached_outcome(
        grain_id="EP-R5F-7002", population=Population.MISSED, gross=0.0, fees=0.0
    )
    taken_economics = evaluate_population_economics(
        [taken], population=Population.TAKEN, minimum_support=2
    )
    missed_economics = evaluate_population_economics(
        [missed], population=Population.MISSED, minimum_support=2
    )
    assert taken_economics.population is Population.TAKEN
    assert missed_economics.population is Population.MISSED
    assert taken_economics.net_dollars == pytest.approx(21.0)
    assert missed_economics.net_dollars == pytest.approx(0.0)
    assert taken_economics.net_dollars != missed_economics.net_dollars


# ---------------------------------------------------------------------------
# AC-005 Maturity vocabulary stays closed
# ---------------------------------------------------------------------------


def test_ac_005_an_unresolved_or_censored_outcome_is_never_matured() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-005: an immature window stays immature.

    GIVEN one frozen expectation offered before its horizon, offered after its
    horizon with no facts, and offered with an explicit censoring reading
    WHEN the maturity vocabulary is resolved for each of them
    THEN an immature window is pending rather than matured, a missing outcome is
    insufficient evidence rather than a result, a censored reading carries only
    its censoring reason, and a censoring reason is refused for a matured outcome
    """

    expectation = frozen_expectation()

    pending = assess_unattached_expectation(
        expectation, evaluated_at=MATURITY - timedelta(minutes=1)
    )
    assert pending.resolution.status is OutcomeStatus.PENDING
    assert pending.resolution.reasons == (OutcomeStatusReason.HORIZON_NOT_REACHED,)

    without_facts = assess_unattached_expectation(
        expectation, evaluated_at=MATURITY + timedelta(minutes=1)
    )
    assert without_facts.resolution.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert without_facts.resolution.reasons == (OutcomeStatusReason.NO_OUTCOME_FACTS,)

    censored = assess_unattached_expectation(
        expectation,
        evaluated_at=MATURITY + timedelta(minutes=1),
        censored=OutcomeStatusReason.CENSORED_BY_DATA_GAP,
    )
    assert censored.resolution.status is OutcomeStatus.CENSORED
    assert censored.resolution.reasons == (OutcomeStatusReason.CENSORED_BY_DATA_GAP,)

    assert {status.value for status in OutcomeStatus} == set(OUTCOME_STATUS_REASONS)
    known_reasons = {reason.value for reason in OutcomeStatusReason}
    for reasons in OUTCOME_STATUS_REASONS.values():
        assert set(reasons) <= known_reasons

    matured = attached_outcome(grain_id="EP-R5F-8001")
    assert matured.resolution.status is OutcomeStatus.MATURED
    with pytest.raises(ExpectationError):
        resolve_outcome_status(
            matured.expectation,
            evaluated_at=MATURITY + timedelta(minutes=1),
            attachment=matured.attachment,
            coverage=1.0,
            reasons=(OutcomeStatusReason.CENSORED_BY_DATA_GAP,),
        )


def test_ac_005_missing_or_zero_coverage_is_insufficient_evidence() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-005: empty coverage is not a result.

    GIVEN a matured attachment whose coverage was never measured and one whose
    measured coverage is zero
    WHEN the outcome status is resolved for each of them
    THEN both are insufficient evidence with the coverage reason rather than
    matured, neither can produce a realized measure, and a fully covered
    attachment is matured with its own reason
    """

    unmeasured = attached_outcome(grain_id="EP-R5F-8101", coverage=None)
    assert unmeasured.resolution.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert unmeasured.resolution.reasons == (OutcomeStatusReason.COVERAGE_MISSING,)

    empty = attached_outcome(grain_id="EP-R5F-8102", coverage=0.0)
    assert empty.resolution.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert empty.resolution.reasons == (OutcomeStatusReason.COVERAGE_MISSING,)

    for row in (unmeasured, empty):
        with pytest.raises(ExpectationError):
            realized_outcome_measures([row])

    covered = attached_outcome(grain_id="EP-R5F-8103", coverage=1.0)
    assert covered.resolution.status is OutcomeStatus.MATURED
    assert covered.resolution.reasons == (OutcomeStatusReason.MATURED_WITH_FACTS,)


def test_ac_005_coverage_beyond_the_declared_window_is_refused() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-005: coverage is a share of a window.

    GIVEN an attachment moment at or after the horizon and coverage figures above,
    exactly at and below the declared window
    WHEN the canonical outcome is attached
    THEN a coverage above the declared window and a negative coverage are refused,
    while a full coverage is accepted and resolved as matured
    """

    expectation = frozen_expectation()
    payload = canonical_payload()
    moment = max(MATURITY, EXIT) + timedelta(minutes=1)

    for coverage in (1.0001, 2.0, -0.5):
        with pytest.raises(ExpectationError):
            attach_canonical_outcome(
                expectation,
                payload,
                committed_fill_count=1,
                recorded_at=moment,
                coverage=coverage,
            )

    boundary = attach_canonical_outcome(
        expectation,
        payload,
        committed_fill_count=1,
        recorded_at=moment,
        coverage=1.0,
    )
    assert boundary.resolution.status is OutcomeStatus.MATURED
    assert boundary.attachment is not None
    assert boundary.attachment.facts.fill_count == 1


# ---------------------------------------------------------------------------
# AC-006 Root cause and repair lineage
# ---------------------------------------------------------------------------


def test_ac_006_a_root_cause_is_classified_by_frozen_precedence() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-006: one named cause by precedence.

    GIVEN a met expectation, a cost overrun, an early exit, a full-horizon
    shortfall, an episode held past its declared horizon that also overspent and an
    artifact with no verified outcome
    WHEN each root cause is classified
    THEN each attachment receives exactly one named cause chosen by the frozen
    precedence with its own reasons, so a horizon breach outranks a cost overrun
    that outranks an early exit that outranks an adverse move that outranks a met
    expectation, and nothing is attributed beyond the evidence
    """

    met = attached_outcome(grain_id="EP-R5F-9001", gross=25.0, fees=4.0)
    cost_drag = attached_outcome(grain_id="EP-R5F-9002", gross=22.0, fees=8.0)
    early = attached_outcome(
        grain_id="EP-R5F-9003",
        gross=18.0,
        fees=4.0,
        entry=ENTER,
        exit_at=ENTER + timedelta(hours=1),
    )
    adverse = attached_outcome(
        grain_id="EP-R5F-9004",
        gross=18.0,
        fees=4.0,
        entry=ENTER,
        exit_at=ENTER + timedelta(hours=13),
    )
    over_held = attached_outcome(
        grain_id="EP-R5F-9005",
        gross=22.0,
        fees=8.0,
        entry=ENTER,
        exit_at=ENTER + timedelta(hours=2),
        expected_duration_seconds=1800.0,
        expected_horizon_seconds=3600.0,
    )
    unknown = unattached_outcome(grain_id="EP-R5F-9006")

    assert classify_root_cause(met).root_cause is LearningRootCause.OUTCOME_MET_EXPECTATION
    assert classify_root_cause(cost_drag).root_cause is LearningRootCause.COST_DRAG
    assert classify_root_cause(early).root_cause is LearningRootCause.EXECUTION_TIMING
    assert classify_root_cause(adverse).root_cause is LearningRootCause.MARKET_ADVERSE_MOVE
    assert classify_root_cause(over_held).root_cause is LearningRootCause.HORIZON_MISMATCH
    assert classify_root_cause(unknown).root_cause is LearningRootCause.NO_VERIFIED_OUTCOME

    assert ROOT_CAUSE_PRECEDENCE[0] == "NO_VERIFIED_OUTCOME"
    assert set(ROOT_CAUSE_PRECEDENCE) == {cause.value for cause in LearningRootCause}
    ordered = [
        ROOT_CAUSE_PRECEDENCE.index(name)
        for name in (
            "HORIZON_MISMATCH",
            "COST_DRAG",
            "EXECUTION_TIMING",
            "MARKET_ADVERSE_MOVE",
            "OUTCOME_MET_EXPECTATION",
        )
    ]
    assert ordered == sorted(ordered)

    classification = classify_root_cause(over_held)
    assert classification.reasons
    assert classification.expectation_id == over_held.expectation.expectation_id
    assert classification.grain_id == over_held.expectation.grain.grain_id
    assert classification.population is over_held.expectation.population
    assert classification.measurement_only is True
    assert classification.affects_live_decisions is False


def test_ac_006_repair_lineage_resolves_to_its_root_and_refuses_a_dangling_parent() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-006: lineage resolves to its root.

    GIVEN one frozen expectation and two corrections that name their parent, and
    separately a correction naming an unknown parent, one recorded before its own
    parent and two expectations claiming one revision of one grain
    WHEN the repair lineage is resolved
    THEN each correction resolves to its root with its own depth and chain, while a
    dangling parent, a correction recorded before the thing it corrects and an
    ambiguous revision each fail closed
    """

    base = frozen_expectation(grain_id="EP-R5F-9100", revision=1)
    repair = frozen_expectation(
        grain_id="EP-R5F-9100", revision=2, correction_of=base.expectation_id
    )
    repair_of_repair = frozen_expectation(
        grain_id="EP-R5F-9100", revision=3, correction_of=repair.expectation_id
    )

    lineage = resolve_repair_lineage([repair_of_repair, base, repair])
    assert len(lineage) == 3
    by_id = {row.expectation_id: row for row in lineage}
    assert by_id[base.expectation_id].root_expectation_id == base.expectation_id
    assert by_id[base.expectation_id].depth == 0
    assert by_id[base.expectation_id].chain == (base.expectation_id,)
    assert by_id[repair.expectation_id].depth == 1
    assert by_id[repair.expectation_id].chain == (
        base.expectation_id,
        repair.expectation_id,
    )
    assert by_id[repair_of_repair.expectation_id].depth == 2
    assert by_id[repair_of_repair.expectation_id].chain == (
        base.expectation_id,
        repair.expectation_id,
        repair_of_repair.expectation_id,
    )
    assert by_id[repair_of_repair.expectation_id].root_expectation_id == (
        base.expectation_id
    )

    dangling = frozen_expectation(
        grain_id="EP-R5F-9101",
        revision=2,
        correction_of=f"{EXPECTATION_ID_PREFIX}:{'0' * 64}",
    )
    with pytest.raises(ExpectationError) as refused:
        resolve_repair_lineage([dangling])
    assert "unknown" in str(refused.value)

    premature = frozen_expectation(
        grain_id="EP-R5F-9100",
        revision=4,
        correction_of=base.expectation_id,
        recorded_at=iso_z(CUTOFF - timedelta(minutes=1)),
    )
    with pytest.raises(ExpectationError):
        resolve_repair_lineage([base, premature])

    ambiguous = frozen_expectation(
        grain_id="EP-R5F-9100",
        revision=2,
        correction_of=base.expectation_id,
        expected_net_return=99.0,
    )
    with pytest.raises(ExpectationError):
        resolve_repair_lineage([base, repair, ambiguous])


def test_ac_006_a_cyclic_or_self_repair_lineage_fails_closed() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-006: a cycle cannot be built.

    GIVEN a frozen expectation payload that names its own identity as its
    correction, two payloads that name each other as their correction, and a frozen
    expectation that is rewritten in place
    WHEN each is rebuilt or replaced
    THEN every one of them fails closed on identity mismatch, so a self-reference
    or a cycle can never be constructed and a frozen expectation is never edited,
    only superseded through the recorded lineage
    """

    record = frozen_expectation(grain_id="EP-R5F-9200")
    other = frozen_expectation(grain_id="EP-R5F-9201")

    self_reference = record.as_dict()
    self_reference["correction_of"] = record.expectation_id
    with pytest.raises(ExpectationError) as refused:
        ProspectiveExpectation.from_dict(self_reference)
    assert "does not match its content" in str(refused.value)

    cycle_head = record.as_dict()
    cycle_head["correction_of"] = other.expectation_id
    cycle_tail = other.as_dict()
    cycle_tail["correction_of"] = record.expectation_id
    with pytest.raises(ExpectationError):
        ProspectiveExpectation.from_dict(cycle_head)
    with pytest.raises(ExpectationError):
        ProspectiveExpectation.from_dict(cycle_tail)

    with pytest.raises(ExpectationError):
        dataclass_replace(
            record, correction_of=f"{EXPECTATION_ID_PREFIX}:{'1' * 64}"
        )


# ---------------------------------------------------------------------------
# AC-007 Named consumption dispositions
# ---------------------------------------------------------------------------


def test_ac_007_every_artifact_receives_exactly_one_named_disposition() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-007: one disposition per artifact.

    GIVEN a retained set holding a matured taken attachment, a matured attachment
    from another population, an unresolved artifact, an artifact that could never
    carry realized truth, a quarantined artifact and a superseded revision
    WHEN consumption dispositions are assigned and the retained set is checked
    THEN every artifact receives exactly one disposition from the closed
    vocabulary with a reason, every branch is reachable, and a duplicate or an
    empty retained set is refused rather than silently accepted
    """

    taken = attached_outcome(grain_id="EP-R5F-9501")
    advisory = attached_outcome(
        grain_id="EP-R5F-9502", population=Population.MISSED, gross=0.0, fees=0.0
    )
    unresolved = unattached_outcome(grain_id="EP-R5F-9503")
    never_realized = assess_unattached_expectation(
        frozen_expectation(
            grain_id="EP-R5F-9504",
            measure_kinds=[MeasureKind.DECLARED_POLICY_COUNTERFACTUAL.value],
        ),
        evaluated_at=MATURITY + timedelta(minutes=1),
    )
    quarantined = mark_unusable_expectation(
        frozen_expectation(grain_id="EP-R5F-9505"),
        evaluated_at=MATURITY + timedelta(minutes=1),
        reason="the canonical payload asserts no evaluable economics",
    )
    superseded = attached_outcome(grain_id="EP-R5F-9506")

    artifacts = [taken, advisory, unresolved, never_realized, quarantined]
    dispositions = [disposition_for(row) for row in artifacts]
    dispositions.append(
        disposition_for(superseded, superseded_by=advisory.expectation.expectation_id)
    )

    ordered = assert_every_artifact_has_one_disposition(dispositions)
    assert len(ordered) == len(dispositions) == 6
    assert set(ordered) <= set(LEARNING_DISPOSITIONS)
    assert set(ordered) == {
        "LIVE",
        "ADVISORY",
        "SHADOW",
        "REJECTED",
        "QUARANTINED",
        "SUPERSEDED",
    }
    assert {item.artifact_id for item in dispositions} == {
        row.expectation.expectation_id for row in artifacts
    } | {superseded.expectation.expectation_id}
    assert all(item.reason for item in dispositions)

    assert DISPOSITION_PRECEDENCE[0] == "QUARANTINED"
    assert set(DISPOSITION_PRECEDENCE) == set(LEARNING_DISPOSITIONS)

    by_artifact = {item.artifact_id: item.disposition for item in dispositions}
    assert by_artifact[taken.expectation.expectation_id] is LearningDisposition.LIVE
    assert (
        by_artifact[advisory.expectation.expectation_id]
        is LearningDisposition.ADVISORY
    )
    assert (
        by_artifact[unresolved.expectation.expectation_id]
        is LearningDisposition.SHADOW
    )
    assert (
        by_artifact[never_realized.expectation.expectation_id]
        is LearningDisposition.REJECTED
    )
    assert (
        by_artifact[quarantined.expectation.expectation_id]
        is LearningDisposition.QUARANTINED
    )
    assert (
        by_artifact[superseded.expectation.expectation_id]
        is LearningDisposition.SUPERSEDED
    )

    with pytest.raises(ExpectationError):
        assert_every_artifact_has_one_disposition(dispositions + [dispositions[0]])
    with pytest.raises(ExpectationError):
        assert_every_artifact_has_one_disposition([])


def test_ac_007_an_unusable_or_never_realized_artifact_is_quarantined_or_rejected() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-007: nothing is silently dropped.

    GIVEN an artifact whose evidence is unusable and an artifact whose declared
    measure kinds could never produce realized truth
    WHEN their dispositions are assigned
    THEN the unusable artifact is quarantined with the refusal reason and the never
    realized artifact is rejected, both keep their identity in the retained set,
    and neither is dropped or measured as realized truth
    """

    unusable_expectation = frozen_expectation(grain_id="EP-R5F-9601")
    reason = "the canonical payload asserts no evaluable economics"
    quarantined = mark_unusable_expectation(
        unusable_expectation,
        evaluated_at=MATURITY + timedelta(minutes=1),
        reason=reason,
    )
    quarantined_disposition = disposition_for(quarantined)
    assert quarantined_disposition.disposition is LearningDisposition.QUARANTINED
    assert quarantined_disposition.reason == reason
    assert quarantined_disposition.artifact_id == unusable_expectation.expectation_id
    assert quarantined_disposition.grain_id == unusable_expectation.grain.grain_id
    with pytest.raises(ExpectationError):
        realized_outcome_measures([quarantined])

    never_realized = assess_unattached_expectation(
        frozen_expectation(
            grain_id="EP-R5F-9602",
            measure_kinds=[MeasureKind.HINDSIGHT_EXCURSION.value],
        ),
        evaluated_at=MATURITY + timedelta(minutes=1),
    )
    rejected_disposition = disposition_for(never_realized)
    assert rejected_disposition.disposition is LearningDisposition.REJECTED
    assert rejected_disposition.reason
    assert rejected_disposition.artifact_id == never_realized.expectation.expectation_id

    retained = [quarantined_disposition, rejected_disposition]
    ordered = assert_every_artifact_has_one_disposition(retained)
    assert len(ordered) == 2
    assert {item.artifact_id for item in retained} == {
        unusable_expectation.expectation_id,
        never_realized.expectation.expectation_id,
    }


def test_ac_007_no_disposition_grants_authority_or_changes_a_policy() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-007: evidence, never authority.

    GIVEN a disposition record for a matured taken attachment
    WHEN its payload and its declared fields are inspected
    THEN it declares itself evidence only, grants no authority, changes no trade
    decision and carries no field that could edit a policy, an order or a size
    """

    taken = attached_outcome(grain_id="EP-R5F-9701")
    disposition = disposition_for(taken)

    assert isinstance(disposition, LearningConsumptionDisposition)
    assert disposition.consumed_as_evidence_only is True
    assert disposition.authority_granted is False
    assert disposition.trade_authority_changed is False

    payload = disposition.as_dict()
    assert payload["consumed_as_evidence_only"] is True
    assert payload["authority_granted"] is False
    assert payload["trade_authority_changed"] is False
    assert payload["disposition"] == disposition.disposition.value
    assert payload["reason"] == disposition.reason
    assert payload["grain_id"] == taken.expectation.grain.grain_id
    assert payload["population"] == taken.expectation.population.value

    names = {field.name for field in dataclass_fields(LearningConsumptionDisposition)}
    assert names == {
        "artifact_id",
        "grain_id",
        "population",
        "disposition",
        "reason",
        "consumed_as_evidence_only",
        "authority_granted",
        "trade_authority_changed",
    }
    for name in names:
        for token in ("order", "execute", "promote", "sizing", "permission"):
            assert token not in name, (name, token)
    assert {
        name for name in names if "authority" in name
    } == {"authority_granted", "trade_authority_changed"}


# ---------------------------------------------------------------------------
# AC-008 Prospective champion/challenger
# ---------------------------------------------------------------------------


def test_ac_008_a_prospective_comparison_is_paired_on_one_frozen_baseline() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-008: paired prospective change.

    GIVEN a champion arm and a challenger arm of frozen expectations sharing one
    frozen baseline and one horizon over four grains, where the champion took two
    episodes and the challenger took the other two
    WHEN the prospective comparison runs
    THEN the comparison is paired at the same grains, reports the cohort, its
    support, each arm's coverage, admitted count and net expectancy with its
    uncertainty, derives the expectancy delta from those means, and keeps every
    promotion and authority flag false
    """

    champion = [
        attached_outcome(
            grain_id="EP-R5F-A101", policy_id=CHAMPION_POLICY, gross=30.0, fees=5.0
        ),
        attached_outcome(
            grain_id="EP-R5F-A102", policy_id=CHAMPION_POLICY, gross=40.0, fees=5.0
        ),
        unattached_outcome(
            grain_id="EP-R5F-A103",
            population=Population.REJECTED,
            policy_id=CHAMPION_POLICY,
        ),
        unattached_outcome(
            grain_id="EP-R5F-A104",
            population=Population.REJECTED,
            policy_id=CHAMPION_POLICY,
        ),
    ]
    challenger = [
        unattached_outcome(
            grain_id="EP-R5F-A101",
            population=Population.REJECTED,
            policy_id=CHALLENGER_POLICY,
        ),
        unattached_outcome(
            grain_id="EP-R5F-A102",
            population=Population.REJECTED,
            policy_id=CHALLENGER_POLICY,
        ),
        attached_outcome(
            grain_id="EP-R5F-A103",
            policy_id=CHALLENGER_POLICY,
            gross=35.0,
            fees=5.0,
        ),
        attached_outcome(
            grain_id="EP-R5F-A104",
            policy_id=CHALLENGER_POLICY,
            gross=25.0,
            fees=5.0,
        ),
    ]

    comparison = compare_prospective_arms(champion, challenger, minimum_support=2)
    assert comparison.paired_samples == 4
    assert comparison.minimum_support == 2
    assert comparison.support is EvaluationSupport.SUFFICIENT
    assert comparison.cohort == f"{BASELINE_ID}:{HORIZON}"
    assert comparison.champion.admitted == 2
    assert comparison.challenger.admitted == 2
    assert comparison.champion.coverage == pytest.approx(0.5)
    assert comparison.challenger.coverage == pytest.approx(0.5)
    assert comparison.champion.net_expectancy.n == 2
    assert comparison.challenger.net_expectancy.n == 2
    assert comparison.champion.net_expectancy.mean == pytest.approx(30.0)
    assert comparison.challenger.net_expectancy.mean == pytest.approx(25.0)
    assert comparison.expectancy_delta == pytest.approx(
        comparison.challenger.net_expectancy.mean
        - comparison.champion.net_expectancy.mean
    )
    assert comparison.expectancy_delta == pytest.approx(-5.0)
    assert comparison.measurement_only is True
    assert comparison.can_promote is False
    assert comparison.automatic_promotion is False
    assert comparison.trade_authority_changed is False
    assert comparison.as_dict()["can_promote"] is False
    assert comparison.as_dict()["automatic_promotion"] is False
    assert comparison.as_dict()["trade_authority_changed"] is False


def test_ac_008_an_unpaired_or_double_taken_grain_is_refused() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-008: no imputation.

    GIVEN a grain present in only one arm, a grain taken by both arms, two arms
    sharing one policy, two arms with different baselines or horizons, and a grain
    neither arm took
    WHEN the prospective comparison runs
    THEN every unpaired, double-taken or mismatched comparison is refused, and a
    grain neither arm took contributes no realized return at all rather than an
    imputed one
    """

    with pytest.raises(ExpectationError) as refused:
        compare_prospective_arms(
            [attached_outcome(grain_id="EP-R5F-B101", policy_id=CHAMPION_POLICY)],
            [
                unattached_outcome(
                    grain_id="EP-R5F-B102",
                    population=Population.REJECTED,
                    policy_id=CHALLENGER_POLICY,
                )
            ],
            minimum_support=2,
        )
    assert "paired" in str(refused.value)

    with pytest.raises(ExpectationError):
        compare_prospective_arms(
            [attached_outcome(grain_id="EP-R5F-B103", policy_id=CHAMPION_POLICY)],
            [attached_outcome(grain_id="EP-R5F-B103", policy_id=CHALLENGER_POLICY)],
            minimum_support=2,
        )

    with pytest.raises(ExpectationError):
        compare_prospective_arms(
            [attached_outcome(grain_id="EP-R5F-B104", policy_id=CHAMPION_POLICY)],
            [
                unattached_outcome(
                    grain_id="EP-R5F-B104",
                    population=Population.REJECTED,
                    policy_id=CHAMPION_POLICY,
                )
            ],
            minimum_support=2,
        )

    with pytest.raises(ExpectationError):
        compare_prospective_arms(
            [attached_outcome(grain_id="EP-R5F-B105", policy_id=CHAMPION_POLICY)],
            [
                unattached_outcome(
                    grain_id="EP-R5F-B105",
                    population=Population.REJECTED,
                    policy_id=CHALLENGER_POLICY,
                    baseline_id="BASELINE-R5F-OTHER",
                )
            ],
            minimum_support=2,
        )

    with pytest.raises(ExpectationError):
        compare_prospective_arms(
            [attached_outcome(grain_id="EP-R5F-B106", policy_id=CHAMPION_POLICY)],
            [
                unattached_outcome(
                    grain_id="EP-R5F-B106",
                    population=Population.REJECTED,
                    policy_id=CHALLENGER_POLICY,
                    horizon="POSITION",
                )
            ],
            minimum_support=2,
        )

    never_taken = compare_prospective_arms(
        [
            unattached_outcome(
                grain_id="EP-R5F-B107",
                population=Population.REJECTED,
                policy_id=CHAMPION_POLICY,
            )
        ],
        [
            unattached_outcome(
                grain_id="EP-R5F-B107",
                population=Population.REJECTED,
                policy_id=CHALLENGER_POLICY,
            )
        ],
        minimum_support=2,
    )
    assert never_taken.champion.admitted == 0
    assert never_taken.challenger.admitted == 0
    assert never_taken.champion.net_expectancy.n == 0
    assert never_taken.champion.net_expectancy.mean is None
    assert never_taken.challenger.net_expectancy.mean is None
    assert never_taken.expectancy_delta is None


def test_ac_008_insufficient_support_is_reported_and_can_never_promote() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-008: insufficient support is not a result.

    GIVEN a paired cohort of four grains compared against a minimum support of ten
    WHEN the prospective comparison runs
    THEN the comparison reports its cohort size, coverage and uncertainty, marks
    itself insufficient, and every promotion and authority flag stays false, so an
    under-supported cohort can never promote a policy
    """

    champion = [
        attached_outcome(
            grain_id="EP-R5F-C101", policy_id=CHAMPION_POLICY, gross=30.0, fees=5.0
        ),
        attached_outcome(
            grain_id="EP-R5F-C102", policy_id=CHAMPION_POLICY, gross=40.0, fees=5.0
        ),
        unattached_outcome(
            grain_id="EP-R5F-C103",
            population=Population.REJECTED,
            policy_id=CHAMPION_POLICY,
        ),
        unattached_outcome(
            grain_id="EP-R5F-C104",
            population=Population.REJECTED,
            policy_id=CHAMPION_POLICY,
        ),
    ]
    challenger = [
        unattached_outcome(
            grain_id="EP-R5F-C101",
            population=Population.REJECTED,
            policy_id=CHALLENGER_POLICY,
        ),
        unattached_outcome(
            grain_id="EP-R5F-C102",
            population=Population.REJECTED,
            policy_id=CHALLENGER_POLICY,
        ),
        attached_outcome(
            grain_id="EP-R5F-C103", policy_id=CHALLENGER_POLICY, gross=35.0, fees=5.0
        ),
        attached_outcome(
            grain_id="EP-R5F-C104", policy_id=CHALLENGER_POLICY, gross=25.0, fees=5.0
        ),
    ]

    comparison = compare_prospective_arms(champion, challenger, minimum_support=10)
    assert comparison.paired_samples == 4
    assert comparison.minimum_support == 10
    assert comparison.cohort.endswith(HORIZON)
    assert comparison.support is EvaluationSupport.INSUFFICIENT
    assert comparison.champion.support == 4
    assert comparison.champion.coverage == pytest.approx(0.5)
    assert comparison.champion.admitted == 2
    assert comparison.challenger.admitted == 2
    assert comparison.champion.net_expectancy.n == 2
    assert comparison.champion.net_expectancy.mean == pytest.approx(30.0)
    # An under-supported cohort reports the point it measured and no interval: the
    # uncertainty is withheld rather than asserted from too few episodes.
    assert comparison.champion.net_expectancy.ci_low is None
    assert comparison.champion.net_expectancy.ci_high is None
    assert comparison.champion.net_expectancy.support is EvaluationSupport.INSUFFICIENT
    assert comparison.challenger.net_expectancy.mean == pytest.approx(25.0)
    # The measured delta is a measurement of an insufficient cohort, and the only
    # thing standing between it and a policy change is the support gate below.
    assert comparison.expectancy_delta == pytest.approx(-5.0)
    assert comparison.measurement_only is True
    assert comparison.can_promote is False
    assert comparison.automatic_promotion is False
    assert comparison.trade_authority_changed is False



# ---------------------------------------------------------------------------
# AC-009 Bookkeeping, dormancy and posture
# ---------------------------------------------------------------------------


def test_ac_009_the_increment_contract_and_pointer_are_self_consistent() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-009: the contract traces to tests.

    GIVEN this increment, its scope contract, the movable pointer and the
    acceptance-test index
    WHEN the contract is parsed and checked against that index
    THEN every criterion is traced to a matching acceptance-marked test and mapped
    to a changed path, the pointer resolves to a declared contract instead of being
    pinned here, the increment this one follows stays declared, and
    `UNAPPROVED SCOPE CHANGES` is `NONE`
    """

    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    ours = [contract for contract in contracts if contract.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert CONTRACT.name == f"{INCREMENT}.md"
    assert contract.sections["INCREMENT"] == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert "ATDD-R5-E-F11-independent-protection-validation" in {
        item.increment for item in contracts
    }

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

    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id

    pointer = read_increment_pointer(POINTER)
    assert pointer in {item.increment for item in contracts}

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons
    assert global_pointer_pin_violations(module_source(pathlib.Path(__file__))) == []


def test_ac_009_no_new_consumer_profile_or_trading_authority_is_introduced() -> None:
    """ATDD-R5-F-prospective-outcome-integration/AC-009: dormant evidence only.

    GIVEN the release-profile surface, the Compose literals and the whole
    application package
    WHEN they are inspected for a new consumer, a new profile or a new authority
    THEN the profiles and the paper-v2 mode are unchanged, no application module
    outside the `SC-10` owning module reaches the surface this increment adds, and
    that surface carries no venue, network, storage or scheduler token, no import
    and no new mode key
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

    consumers = []
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if path == EVALUATION_MODULE:
            continue
        source = module_source(path)
        if any(token in source for token in R5F_ONLY_TOKENS):
            consumers.append(path.relative_to(APP_ROOT).as_posix())
    assert consumers == []

    section = r5f_section()
    for token in FORBIDDEN_RUNTIME_TOKENS:
        assert token not in section, token
    assert not any(
        line.strip().startswith(("import ", "from ")) for line in section.splitlines()
    )
    for token in ("sqlite3", "subprocess", "socket", "ccxt", "requests"):
        assert token not in section, token
    assert "measurement_only: bool = True" in section
