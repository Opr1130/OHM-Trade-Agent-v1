"""ATDD-R5-F-replay-determinism-v1 acceptance tests.

Deterministic replay and repeat-processing behaviour for the frozen R5-F
prospective expectation and canonical-outcome learning surfaces.

Every callable under test is a production function of
``app.opip.learning.evaluation`` or of the canonical outcome contract. This module
adds no alternate implementation: inputs are built with the production capture and
payload builders, and the results of **independently instantiated** evaluation
contexts are compared in full. No wall clock is read anywhere — every instant is
an explicit fixed timestamp, and every figure keeps its frozen unit.

The increment adds no application code, no store, no writer, no scheduler, no
route, no release-profile key and no trading authority.
"""

from __future__ import annotations

import datetime as dt
import itertools
import pathlib
import sys

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts.paper_outcome import (  # noqa: E402
    build_terminal_outcome_payload,
    terminal_outcome_idempotency_key,
)
from app.opip.contracts.serialization import iso_z  # noqa: E402
from app.opip.learning.evaluation import (  # noqa: E402
    EXPECTATION_ID_PREFIX,
    ExpectationError,
    LearningDisposition,
    MeasureKind,
    OutcomeStatus,
    Population,
    ProspectiveExpectation,
    assert_every_artifact_has_one_disposition,
    assess_unattached_expectation,
    attach_canonical_outcome,
    capture_expectation_from_decision,
    compare_prospective_arms,
    disposition_for,
    evaluate_population_economics,
    realized_outcome_measures,
    total_for_kind,
)
from app.services.release_profiles import (  # noqa: E402
    get_release_profiles,
    validate_profile_contract,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-F-replay-determinism-v1"

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 5, 11, 9, 0, tzinfo=UTC)
CUTOFF = T0
HORIZON_SECONDS = 86_400.0
MATURITY = T0 + dt.timedelta(seconds=HORIZON_SECONDS)
ENTER = T0 + dt.timedelta(hours=2)
EXIT = T0 + dt.timedelta(hours=8)
RECORDED = T0 + dt.timedelta(hours=25)
BASELINE_ID = "BASELINE-R5F-REPLAY-1"
HORIZON = "REPLAY"
POLICY = "policy-r5f-replay"
CHALLENGER_POLICY = "policy-r5f-replay-challenger"
GRAIN = "EP-R5F-REPLAY-0001"


# ---------------------------------------------------------------------------
# Inputs, built only with production constructors
# ---------------------------------------------------------------------------


def decision_evidence(**overrides: object) -> dict:
    """One ex-ante decision record exactly as the capture seam requires it."""

    values: dict = {
        "grain_id": GRAIN,
        "entity": "BTCUSD",
        "policy_id": POLICY,
        "horizon": HORIZON,
        "revision": 1,
        "population": Population.TAKEN.value,
        "decision_at_utc": iso_z(T0 - dt.timedelta(minutes=30)),
        "cutoff": iso_z(CUTOFF),
        "maturity_at": iso_z(MATURITY),
        "recorded_at": iso_z(CUTOFF),
        "baseline_id": BASELINE_ID,
        "baseline_description": "the frozen declared-policy baseline this replay grain is measured against",
        "measure_kinds": [MeasureKind.REALIZED_PAPER_OUTCOME.value],
        "correction_of": None,
        "expected_net_return": 20.0,
        "expected_duration_seconds": 1_800.0,
        "expected_cost_dollars": 5.0,
        "expected_horizon_seconds": HORIZON_SECONDS,
    }
    values.update(overrides)
    return values


def frozen_expectation(**overrides: object) -> ProspectiveExpectation:
    """A fresh frozen SC-10 expectation from the production capture seam."""

    return capture_expectation_from_decision(decision_evidence(**overrides))


def canonical_payload(
    *,
    grain_id: str = GRAIN,
    gross: float = 25.0,
    fees: float = 4.0,
    capital: float = 1_000.0,
    status: str = "CLOSED",
) -> dict:
    """A terminal outcome payload built by the production contract builder."""

    if status == "UNRESOLVED":
        gross_value = fees_value = net_value = pct_value = capital_value = None
    else:
        gross_value, fees_value = gross, fees
        net_value = gross - fees
        pct_value = ((gross - fees) / capital) * 100.0
        capital_value = capital
    return build_terminal_outcome_payload(
        engine="OHM_PAPER_SIM_V1",
        paper_trade_id="PAPER:" + grain_id.replace("-", "").lower()[:20].ljust(20, "r"),
        episode_id=grain_id,
        cohort_id="COH-R5F-REPLAY",
        strategy_version="OPIP-STRATEGY-V1",
        exchange="KRAKEN",
        native_symbol="BTCUSD",
        base_asset="BTC",
        direction="LONG",
        quote_currency="USD",
        terminal_status=status,
        exit_reason="UNRESOLVED" if status == "UNRESOLVED" else "TARGET_2",
        exit_price=101.0,
        entry_timestamp=ENTER,
        exit_timestamp=EXIT,
        capital_committed=capital_value,
        gross_pnl=gross_value,
        fees_paid=fees_value,
        net_pnl=net_value,
        net_pnl_pct=pct_value,
        final_revision=3,
        terminal_event_id="PTE:" + "r" * 24,
        candidate_id="CAND-R5F-REPLAY",
        decision_context_id="DI-CONTEXT:" + "r" * 32,
    )


def attached(
    *,
    grain_id: str = GRAIN,
    gross: float = 25.0,
    fees: float = 4.0,
    policy_id: str = POLICY,
    moment: dt.datetime = RECORDED,
    committed_fill_count: int = 1,
    coverage: float | None = 1.0,
):
    """One frozen expectation joined to one committed canonical outcome."""

    expectation = frozen_expectation(grain_id=grain_id, policy_id=policy_id)
    body = canonical_payload(grain_id=grain_id, gross=gross, fees=fees)
    return attach_canonical_outcome(
        expectation,
        body,
        committed_fill_count=committed_fill_count,
        recorded_at=moment,
        coverage=coverage,
    )


def unattached(
    *,
    grain_id: str,
    population: Population,
    policy_id: str = POLICY,
):
    """One frozen expectation of a population that never took the episode."""

    expectation = frozen_expectation(
        grain_id=grain_id, population=population.value, policy_id=policy_id
    )
    return assess_unattached_expectation(expectation, evaluated_at=RECORDED)


def _normalised_dispositions(artifacts):
    """Dispositions in a grain-defined order, so delivery order cannot matter."""

    return sorted(
        (row.artifact_id, row.grain_id, row.disposition.value, row.reason)
        for row in (disposition_for(item) for item in artifacts)
    )


# ---------------------------------------------------------------------------
# AC-001 frozen expectation replay
# ---------------------------------------------------------------------------


def test_ac_001_independent_capture_reconstructs_one_identical_expectation():
    """ATDD-R5-F-replay-determinism-v1/AC-001: same evidence, same frozen record.

    GIVEN one decision evidence record and an independently rebuilt copy of it
    WHEN the production capture seam freezes a prospective expectation from each
    THEN the two identities, expected-payload hashes and canonical serializations
    are identical, and neither record claims authority
    """

    first = frozen_expectation()
    second = frozen_expectation()

    # Non-vacuous positive control: a real identity, not an empty placeholder.
    assert first.expectation_id.startswith(EXPECTATION_ID_PREFIX)
    assert first.expected_hash

    assert first.expectation_id == second.expectation_id
    assert first.expected_hash == second.expected_hash
    assert first.to_dict() == second.to_dict()
    assert first.expected_payload == second.expected_payload
    assert first.as_dict()["expected_payload"] == second.as_dict()["expected_payload"]

    assert first.authoritative is False
    assert first.can_promote is False


def test_ac_001_the_frozen_record_round_trips_through_its_durable_payload():
    """ATDD-R5-F-replay-determinism-v1/AC-001: the SC-10 payload is lossless.

    GIVEN a frozen expectation serialized by the production record path
    WHEN it is rebuilt by the production loader
    THEN identity, expected hash, canonical payload and the frozen temporal fields
    survive unchanged, so replay from durable bytes is the same expectation
    """

    captured = frozen_expectation()
    rebuilt = ProspectiveExpectation.from_dict(captured.to_dict())

    assert rebuilt.expectation_id == captured.expectation_id
    assert rebuilt.expected_hash == captured.expected_hash
    assert rebuilt.to_dict() == captured.to_dict()
    assert rebuilt.to_dict() == frozen_expectation().to_dict()

    assert rebuilt.cutoff == captured.cutoff == CUTOFF
    assert rebuilt.maturity_at == captured.maturity_at == MATURITY
    assert rebuilt.recorded_at == captured.recorded_at
    assert rebuilt.measure_kinds == captured.measure_kinds

    assert rebuilt.authoritative is False
    assert rebuilt.can_promote is False


def test_ac_001_a_mutated_payload_fails_closed_instead_of_loading():
    """ATDD-R5-F-replay-determinism-v1/AC-001: a forged record is refused.

    GIVEN a durable expectation payload whose expected net has been rewritten
    WHEN the production loader rebuilds it
    THEN the loader fails closed rather than accepting a record whose content no
    longer matches its own identity and hash
    """

    body = frozen_expectation().to_dict()
    assert body["expected_payload"]["expected_net_return"] == 20.0
    body["expected_payload"]["expected_net_return"] = 999.0

    with pytest.raises(ExpectationError):
        ProspectiveExpectation.from_dict(body)


# ---------------------------------------------------------------------------
# AC-002 committed outcome replay
# ---------------------------------------------------------------------------


def test_ac_002_independent_evaluation_produces_one_identical_attachment():
    """ATDD-R5-F-replay-determinism-v1/AC-002: same evidence, same join.

    GIVEN one committed canonical terminal outcome and two independently captured
    expectations of the same grain
    WHEN each expectation is joined to that outcome by the production seam
    THEN the attachment identity, economic facts, committed capital and resolution
    are identical, and the economics of the single-episode population agree
    """

    first = attached()
    second = attached()

    assert first.attachment is not None and second.attachment is not None
    assert first.expectation.expectation_id == second.expectation.expectation_id
    assert first.attachment.expectation_id == second.attachment.expectation_id
    assert first.attachment.facts == second.attachment.facts
    assert first.attachment.recorded_at == second.attachment.recorded_at == RECORDED
    assert first.committed_capital == second.committed_capital == 1_000.0

    # Economic facts keep their frozen unit: net return and cost are quote currency,
    # duration is seconds, and the committed fill count is one.
    facts = first.attachment.facts
    committed = canonical_payload()
    assert facts.fill_count == 1
    assert facts.net_return == 21.0
    assert facts.cost_dollars == 4.0
    assert facts.duration_seconds == 6 * 3_600.0
    assert facts.net_return == committed["gross_pnl"] - committed["fees_paid"]

    assert first.resolution.status is OutcomeStatus.MATURED
    assert first.resolution == second.resolution

    economics_a = evaluate_population_economics(
        [first], population=Population.TAKEN, minimum_support=2
    )
    economics_b = evaluate_population_economics(
        [second], population=Population.TAKEN, minimum_support=2
    )
    assert economics_a.support == economics_b.support == 1
    assert economics_a.net_dollars == pytest.approx(21.0)
    assert economics_b.net_dollars == pytest.approx(economics_a.net_dollars)


def test_ac_002_maturity_and_disposition_are_identical_across_replay():
    """ATDD-R5-F-replay-determinism-v1/AC-002: maturity and consumption are stable.

    GIVEN the same frozen expectation and the same verified outcome evaluated twice
    WHEN maturity is resolved and a consumption disposition is assigned each time
    THEN the maturity status, its reasons, the measure series and the disposition
    record are identical, and nothing is consumed twice
    """

    first = attached()
    second = attached()

    assert first.resolution.status is OutcomeStatus.MATURED
    assert second.resolution.status is OutcomeStatus.MATURED
    assert first.resolution.reasons == second.resolution.reasons

    measures_a = realized_outcome_measures([first])
    measures_b = realized_outcome_measures([second])
    assert measures_a == measures_b
    assert {row.kind for row in measures_a} == {MeasureKind.REALIZED_PAPER_OUTCOME}
    assert {row.grain_id for row in measures_a} == {GRAIN}

    total_a = total_for_kind(measures_a, kind=MeasureKind.REALIZED_PAPER_OUTCOME)
    total_b = total_for_kind(measures_b, kind=MeasureKind.REALIZED_PAPER_OUTCOME)
    assert total_a == pytest.approx(21.0)
    assert total_b == pytest.approx(total_a)

    disposition_a = disposition_for(first)
    disposition_b = disposition_for(second)
    assert disposition_a == disposition_b
    assert disposition_a.disposition is LearningDisposition.LIVE
    assert disposition_a.reason
    assert disposition_a.consumed_as_evidence_only is True

    assert assert_every_artifact_has_one_disposition([disposition_a]) == (
        disposition_a.disposition.value,
    )


# ---------------------------------------------------------------------------
# AC-003 duplicate processing
# ---------------------------------------------------------------------------


def test_ac_003_a_repeated_outcome_identity_adds_no_second_contribution():
    """ATDD-R5-F-replay-determinism-v1/AC-003: re-delivery adds nothing twice.

    GIVEN one committed outcome identity evaluated twice
    WHEN the measure series and the population economics are derived each time
    THEN the second derivation reproduces the first exactly, the same artifact
    delivered twice inside one collection is refused rather than summed, and the
    population contribution stays one episode
    """

    artifact = attached()

    measures_once = realized_outcome_measures([artifact])
    measures_again = realized_outcome_measures([artifact])
    assert measures_again == measures_once

    total_once = total_for_kind(measures_once, kind=MeasureKind.REALIZED_PAPER_OUTCOME)
    total_again = total_for_kind(measures_again, kind=MeasureKind.REALIZED_PAPER_OUTCOME)
    assert total_again == pytest.approx(total_once) == pytest.approx(21.0)

    # Re-delivery of the same grain inside one collection is refused, not summed.
    with pytest.raises(ExpectationError):
        realized_outcome_measures([artifact, artifact])

    economics = evaluate_population_economics(
        [artifact], population=Population.TAKEN, minimum_support=2
    )
    assert economics.support == 1
    assert economics.net_dollars == pytest.approx(21.0)


def test_ac_003_a_duplicate_disposition_is_refused_not_registered_twice():
    """ATDD-R5-F-replay-determinism-v1/AC-003: one artifact, one disposition.

    GIVEN the disposition of one artifact
    WHEN a retained set is checked that holds it once, twice, or not at all
    THEN the single set yields exactly one coverage entry and both the duplicated
    set and the empty set are refused
    """

    artifact = attached()
    disposition = disposition_for(artifact)

    assert assert_every_artifact_has_one_disposition([disposition]) == (
        disposition.disposition.value,
    )

    with pytest.raises(ExpectationError):
        assert_every_artifact_has_one_disposition([disposition, disposition])
    with pytest.raises(ExpectationError):
        assert_every_artifact_has_one_disposition([])


def test_ac_003_the_canonical_idempotency_key_is_bound_to_outcome_identity():
    """ATDD-R5-F-replay-determinism-v1/AC-003: the delivery key is identity-bound.

    GIVEN two payloads carrying the same canonical outcome identity but different
    economics, and a third carrying a different identity
    WHEN the canonical idempotency key is derived for each
    THEN the key is stable across calls and equal for the shared identity even
    though the economics differ, and distinct for the other identity — so repeated
    delivery collapses onto one identity while a different episode does not
    """

    same_identity_a = canonical_payload(grain_id="EP-R5F-REPLAY-0301", gross=25.0)
    same_identity_b = canonical_payload(grain_id="EP-R5F-REPLAY-0301", gross=999.0)
    other_identity = canonical_payload(grain_id="EP-R5F-REPLAY-0302")

    # Non-vacuous positive control: identities and economics differ where claimed.
    assert same_identity_a["outcome_id"] == same_identity_b["outcome_id"]
    assert same_identity_a["net_pnl"] != same_identity_b["net_pnl"]
    assert same_identity_a["outcome_id"] != other_identity["outcome_id"]

    key = terminal_outcome_idempotency_key(same_identity_a["outcome_id"])
    assert key == terminal_outcome_idempotency_key(same_identity_a["outcome_id"])
    assert key == terminal_outcome_idempotency_key(same_identity_b["outcome_id"])
    assert key != terminal_outcome_idempotency_key(other_identity["outcome_id"])


# ---------------------------------------------------------------------------
# AC-004 valid-order equivalence
# ---------------------------------------------------------------------------


def test_ac_004_economics_are_invariant_under_valid_delivery_order():
    """ATDD-R5-F-replay-determinism-v1/AC-004: order does not change the truth.

    GIVEN three matured attachments of one population whose figures are exact in
    the frozen unit
    WHEN the population is evaluated under every delivery permutation
    THEN every permutation yields the identical economics record, and the measure
    series and dispositions are identical once normalised by grain
    """

    artifacts = [
        attached(grain_id="EP-R5F-REPLAY-0401", gross=30.0),
        attached(grain_id="EP-R5F-REPLAY-0402", gross=20.0),
        attached(grain_id="EP-R5F-REPLAY-0403", gross=40.0),
    ]

    baseline_economics = evaluate_population_economics(
        artifacts, population=Population.TAKEN, minimum_support=2
    )
    baseline_measures = sorted(
        realized_outcome_measures(artifacts), key=lambda row: (row.grain_id, row.kind.value)
    )
    baseline_dispositions = _normalised_dispositions(artifacts)

    # Non-vacuous positive control: three grains really contributed.
    assert baseline_economics.support == 3
    assert len(baseline_measures) == 3
    assert len(baseline_dispositions) == 3

    for order in itertools.permutations(range(len(artifacts))):
        subset = [artifacts[index] for index in order]
        economics = evaluate_population_economics(
            subset, population=Population.TAKEN, minimum_support=2
        )
        measures = sorted(
            realized_outcome_measures(subset), key=lambda row: (row.grain_id, row.kind.value)
        )
        assert economics == baseline_economics, order
        assert measures == baseline_measures, order
        assert _normalised_dispositions(subset) == baseline_dispositions, order


def test_ac_004_an_invalid_temporal_sequence_is_refused_whatever_the_order():
    """ATDD-R5-F-replay-determinism-v1/AC-004: invalid time is never order-excused.

    GIVEN a committed outcome and an evaluation moment that precedes the episode's
    own exit, and separately an evaluation at the cutoff of a window that is still
    immature
    WHEN each is evaluated and then re-evaluated
    THEN the temporal violation is refused with the same classification both times,
    and the immature window is never matured
    """

    expectation = frozen_expectation()
    body = canonical_payload()

    refusals = []
    for _ in range(2):
        with pytest.raises(ExpectationError) as failure:
            attach_canonical_outcome(
                expectation, body, committed_fill_count=1, recorded_at=ENTER
            )
        refusals.append((type(failure.value).__name__, str(failure.value)))
    assert refusals[0] == refusals[1]

    immature = assess_unattached_expectation(expectation, evaluated_at=CUTOFF)
    assert immature.resolution.status is OutcomeStatus.PENDING
    assert immature.resolution.status is not OutcomeStatus.MATURED
    repeated = assess_unattached_expectation(expectation, evaluated_at=CUTOFF)
    assert repeated.resolution == immature.resolution


# ---------------------------------------------------------------------------
# AC-005 deterministic failure and authority boundaries
# ---------------------------------------------------------------------------


def test_ac_005_repeated_refusals_are_stable_across_classifications():
    """ATDD-R5-F-replay-determinism-v1/AC-005: a refusal is the same refusal.

    GIVEN four distinct inadmissible inputs — hindsight contamination, an
    unverifiable fill count, a duplicated grain and a forged expectation payload
    WHEN each is processed twice
    THEN each produces the same refusal type and the same refusal message both
    times, so a failure classification is reproducible and not incidental
    """

    def hindsight_contamination():
        capture_expectation_from_decision(
            decision_evidence(recorded_at=iso_z(CUTOFF + dt.timedelta(hours=1)))
        )

    def unverifiable_fill_count():
        attach_canonical_outcome(
            frozen_expectation(),
            canonical_payload(),
            committed_fill_count=0,
            recorded_at=RECORDED,
        )

    def duplicated_grain():
        artifact = attached()
        realized_outcome_measures([artifact, artifact])

    def forged_expectation_payload():
        body = frozen_expectation().to_dict()
        body["expected_hash"] = "PEXPP:" + "0" * 64
        ProspectiveExpectation.from_dict(body)

    cases = (
        hindsight_contamination,
        unverifiable_fill_count,
        duplicated_grain,
        forged_expectation_payload,
    )
    for case in cases:
        seen = []
        for _ in range(2):
            with pytest.raises(ExpectationError) as failure:
                case()
            seen.append((type(failure.value).__name__, str(failure.value)))
        assert seen[0] == seen[1], case.__name__
        assert seen[0][1], case.__name__


def test_ac_005_the_learning_path_grants_no_authority_and_activates_no_mode():
    """ATDD-R5-F-replay-determinism-v1/AC-005: evidence only, still off.

    GIVEN the release-profile contract, a frozen expectation, a consumption
    disposition and the learning module's own imports
    WHEN they are inspected after a full evaluate-and-dispose cycle
    THEN the profiles and the paper-v2 mode are unchanged, the records declare no
    authority, and the module imports no writer, exchange, storage, job or
    Telegram surface
    """

    profiles = get_release_profiles()
    assert set(profiles) == {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}
    assert validate_profile_contract("EVIDENCE_SHADOW") == (True, [])

    valid, reasons = validate_profile_contract("TARGET_PAPER")
    assert valid is False
    assert reasons

    allowed = profiles["EVIDENCE_SHADOW"]["allowed_modes"]
    assert allowed["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert allowed["OPIP_CANONICAL_WRITER_MODE"] == "shadow"
    assert allowed["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert allowed["OPIP_PAPER_V2_MODE"] == "off"

    expectation = frozen_expectation()
    assert expectation.authoritative is False
    assert expectation.can_promote is False

    artifact = attached()
    disposition = disposition_for(artifact)
    assert disposition.consumed_as_evidence_only is True

    source = (
        APP_ROOT / "app" / "opip" / "learning" / "evaluation.py"
    ).read_text(encoding="utf-8")
    import_lines = "\n".join(
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    )
    for token in (
        "app.opip.canonical",
        "app.exchanges",
        "app.services.telegram",
        "app.services.registry_io",
        "sqlite3",
        "app.opip.storage",
        "app.jobs",
        "app.core.config",
        "subprocess",
        "socket",
        "ccxt",
        "requests",
    ):
        assert token not in import_lines, token


