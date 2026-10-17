"""ATDD-R6 Committee shadow review readiness.

Increment: `ATDD-R6-committee-shadow-review-readiness`. Every test cites its own
criterion in its docstring, and the contract
`docs/atdd/scope-contracts/ATDD-R6-committee-shadow-review-readiness.md` is the
authority for what each one means.

This increment answers the roadmap's `R6` question over the Committee package
that already exists - is it ready to be put in front of an owner influence
review, and what exactly is missing - without adding a Committee feature, role,
transport, provider, timer, scheduler, store or any trading authority. Every test
feeds the assessment real records from the package's own seams: a sealed T0
prediction built the way the sealing seam validates it, an ineligibility record,
the trust gate vocabulary, the weakness registry, the durable stream names the
evidence store declares, and the module sources that define the import boundary.
`AC-008` is this increment's own bookkeeping guard.
"""

from __future__ import annotations

import ast
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
import pathlib
import re
import sys
from unittest.mock import Mock

import pytest
import yaml

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.committee.contracts import CaseType  # noqa: E402
from app.opip.committee import shadow_review  # noqa: E402
from app.opip.committee.prospective import (  # noqa: E402
    LEGACY_UNSEALED_RELEASE,
    ProspectiveIneligibility,
    SealedPrediction,
)
from app.opip.committee.settings import COMMITTEE_MODES  # noqa: E402
from app.opip.committee.shadow_review import (  # noqa: E402
    FORBIDDEN_REVIEW_IMPORTS,
    SHADOW_REVIEW_SCHEMA_VERSION,
    CasePopulationEvidence,
    ReviewPrerequisite,
    ReviewReason,
    ShadowReviewError,
    ShadowReviewReadiness,
    TimerPolicy,
    TimerPolicyDecision,
    assess_import_boundary,
    assess_prospective_cases,
    assess_release_identity,
    assess_shadow_review_readiness,
    assess_timer_policy,
    assess_trust_evidence,
    assess_weakness_finding_durability,
    is_durable_stream_name,
    measure_case_population,
    measure_import_boundary,
)
from app.opip.committee.trust import (  # noqa: E402
    MINIMUM_MATURED_CASES,
    CommitteeInvestment,
    GateName,
    GateResult,
    GateStatus,
)
from app.opip.committee.weakness import WeaknessRegistry  # noqa: E402
from app.opip.decision_intelligence.identity import Provenance  # noqa: E402
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

INCREMENT = "ATDD-R6-committee-shadow-review-readiness"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
COMMITTEE_DIR = APP_ROOT / "app" / "opip" / "committee"
MODULE = COMMITTEE_DIR / "shadow_review.py"
STORE_MODULE = COMMITTEE_DIR / "store.py"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"

CORE_SHA = "a" * 40
DRIFTED_SHA = "b" * 40
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ASSESSED_AT = T0 + timedelta(days=30)
HORIZON_SECONDS = 86_400
DURABLE_STREAM = "weakness_findings.jsonl"

#: Names this increment exports. The list is the surface the contract describes.
EXPECTED_EXPORTS = {
    "DURABLE_STREAM_SUFFIX",
    "FORBIDDEN_REVIEW_IMPORTS",
    "SHADOW_REVIEW_IDENTITY_DOMAIN",
    "SHADOW_REVIEW_SCHEMA_VERSION",
    "CasePopulationEvidence",
    "PrerequisiteFinding",
    "ReviewPrerequisite",
    "ReviewReason",
    "ShadowReviewError",
    "ShadowReviewReadiness",
    "TimerPolicy",
    "TimerPolicyDecision",
    "assess_import_boundary",
    "assess_prospective_cases",
    "assess_release_identity",
    "assess_shadow_review_readiness",
    "assess_timer_policy",
    "assess_trust_evidence",
    "assess_weakness_finding_durability",
    "is_durable_stream_name",
    "measure_case_population",
    "measure_import_boundary",
}

#: Tokens that would mean the readiness surface reached a runtime authority.
FORBIDDEN_RUNTIME_TOKENS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "create_order",
    "api_key",
    "api_secret",
    "credential",
    "telegram",
    "writer_client",
    "environ",
    "utcnow",
    "datetime.now",
    "time.time",
    "uuid",
    "random",
    "sqlite3",
    "subprocess",
    "socket",
    "requests",
    "httpx",
    "ccxt",
    "krakenex",
    "psycopg",
    "sqlalchemy",
)

#: Modules the readiness surface may import. Anything else is a new dependency.
ALLOWED_IMPORTS = {
    "__future__",
    "app.opip.committee.prospective",
    "app.opip.committee.trust",
    "app.opip.committee.weakness",
    "app.opip.decision_intelligence.serialization",
    "ast",
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "pathlib",
    "typing",
}


# ---------------------------------------------------------------------------
# Helpers: real records, built the way the owning seams validate them
# ---------------------------------------------------------------------------


def module_source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def module_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(module_source(path), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def provenance(case_id: str) -> Provenance:
    return Provenance(
        producing_component="committee-shadow-cycle",
        artifact_or_build_id=CORE_SHA,
        process_instance_id=f"cycle-{case_id}",
        emitted_at=T0,
        source_record_refs=(f"case:{case_id}",),
    )


def sealed_prediction(
    case_id: str,
    *,
    release_sha: str = CORE_SHA,
    sealed_at: datetime = T0,
    horizon_seconds: int = HORIZON_SECONDS,
) -> SealedPrediction:
    """One T0 record as the sealing seam validates it."""

    return SealedPrediction(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        experiment_id="exp-r6-shadow-review",
        evidence_cutoff_at=sealed_at,
        sealed_at=sealed_at,
        case_outcome_id=f"outcome-{case_id}",
        evidence_snapshot_hash=f"snapshot-{case_id}",
        committee_policy_version="committee-policy-r6",
        sealed_opinion_hashes=(f"opinion-{case_id}",),
        sealed_seat_count=1,
        horizon_seconds=horizon_seconds,
        release_sha=release_sha,
        provenance=provenance(case_id),
    )


def sealed_population(
    count: int,
    *,
    release_sha: str = CORE_SHA,
    sealed_at: datetime = T0,
    horizon_seconds: int = HORIZON_SECONDS,
) -> tuple[SealedPrediction, ...]:
    return tuple(
        sealed_prediction(
            f"case-r6-{index:03d}",
            release_sha=release_sha,
            sealed_at=sealed_at,
            horizon_seconds=horizon_seconds,
        )
        for index in range(count)
    )


def ineligibility(case_id: str, *, reason: str = "RELEASE_DRIFT") -> ProspectiveIneligibility:
    return ProspectiveIneligibility(
        prediction_id=f"prediction-{case_id}",
        case_id=case_id,
        experiment_id="exp-r6-shadow-review",
        reason=reason,
        expected_release_sha=CORE_SHA,
        observed_release_sha=DRIFTED_SHA,
        detected_at=T0 + timedelta(days=1),
    )


def owner_decision(
    *,
    policy: TimerPolicy = TimerPolicy.DISABLED,
    decided_by: str = "owner-opip",
) -> TimerPolicyDecision:
    return TimerPolicyDecision(
        policy=policy,
        decided_by=decided_by,
        decided_at=T0 - timedelta(days=1),
    )


def passing_gates() -> tuple[GateResult, ...]:
    return tuple(
        GateResult(gate=name, status=GateStatus.PASSED) for name in GateName
    )


def investment(*, cost: int | None, provider_calls: int = 4) -> CommitteeInvestment:
    return CommitteeInvestment(
        attributable_cost_microunits=cost, provider_calls=provider_calls
    )


def all_pass_readiness(**overrides) -> ShadowReviewReadiness:
    """The composed assessment over evidence that satisfies every prerequisite."""

    kwargs: dict = {
        "assessed_at": ASSESSED_AT,
        "core_release_sha": CORE_SHA,
        "committee_release_sha": CORE_SHA,
        "predictions": sealed_population(MINIMUM_MATURED_CASES),
        "investment": investment(cost=1_500),
        "timer_decision": owner_decision(),
        "weakness_registry": WeaknessRegistry(),
        "durable_weakness_stream": DURABLE_STREAM,
        "gates": passing_gates(),
        "import_violations": (),
    }
    kwargs.update(overrides)
    return assess_shadow_review_readiness(**kwargs)


# ---------------------------------------------------------------------------
# AC-001 release identity
# ---------------------------------------------------------------------------


def test_ac_001_a_matching_committee_release_is_the_only_release_identity() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-001: the release under test.

    GIVEN the release identity of the Committee under review, the core release
    under test, and cases the Committee sealed
    WHEN the release identity prerequisite is assessed
    THEN a sealed population matching the core release and a Committee release
    equal to it are satisfied, a population with no sealed case is insufficient
    evidence instead of a pass, and a prediction sealed before the release
    identity existed can never stand in for the release under test
    """

    readiness = all_pass_readiness()
    assert readiness.committee_release_sha == CORE_SHA
    assert readiness.core_release_sha == CORE_SHA
    finding = readiness.finding(ReviewPrerequisite.RELEASE_IDENTITY)
    assert finding.status is GateStatus.PASSED
    assert finding.reason is None
    assert CORE_SHA in (finding.detail or "")
    assert readiness.verdict is GateStatus.PASSED

    unmeasured = assess_release_identity(predictions=(), core_release_sha=CORE_SHA)
    assert unmeasured.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert unmeasured.reason is ReviewReason.RELEASE_IDENTITY_UNMEASURED

    blank = assess_release_identity(
        predictions=sealed_population(1), core_release_sha="   "
    )
    assert blank.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert blank.reason is ReviewReason.RELEASE_IDENTITY_UNMEASURED

    legacy = assess_release_identity(
        predictions=(
            sealed_prediction("case-legacy", release_sha=LEGACY_UNSEALED_RELEASE),
        ),
        core_release_sha=CORE_SHA,
    )
    assert legacy.status is GateStatus.BLOCKED
    assert legacy.reason is ReviewReason.RELEASE_DRIFT


def test_ac_001_a_drifted_release_blocks_and_is_named_as_drift() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-001: drift is named, not bucketed.

    GIVEN a population sealed with a release other than the core release under
    test, and separately a Committee release identity that differs from it
    WHEN the release identity prerequisite is assessed
    THEN the prerequisite is blocked with `RELEASE_DRIFT` naming both identities,
    the composed verdict is blocked rather than a generic failure or a pass, and
    no promotion or influence follows from the blocked record
    """

    drifted_population = sealed_population(3, release_sha=DRIFTED_SHA)
    finding = assess_release_identity(
        predictions=drifted_population, core_release_sha=CORE_SHA
    )
    assert finding.status is GateStatus.BLOCKED
    assert finding.reason is ReviewReason.RELEASE_DRIFT
    assert CORE_SHA in (finding.detail or "")
    assert DRIFTED_SHA in (finding.detail or "")

    mixed = sealed_population(MINIMUM_MATURED_CASES - 1) + (
        sealed_prediction("case-drifted", release_sha=DRIFTED_SHA),
    )
    readiness = all_pass_readiness(predictions=mixed)
    assert readiness.verdict is GateStatus.BLOCKED
    assert readiness.ready_for_influence_review is False
    assert ReviewReason.RELEASE_DRIFT in readiness.blocking_reasons
    assert readiness.can_influence is False

    identity = all_pass_readiness(committee_release_sha=DRIFTED_SHA)
    assert identity.verdict is GateStatus.BLOCKED
    assert (
        identity.finding(ReviewPrerequisite.RELEASE_IDENTITY).reason
        is ReviewReason.RELEASE_DRIFT
    )
    assert DRIFTED_SHA in (
        identity.finding(ReviewPrerequisite.RELEASE_IDENTITY).detail or ""
    )


# ---------------------------------------------------------------------------
# AC-002 timer policy
# ---------------------------------------------------------------------------


def test_ac_002_an_undecided_timer_policy_is_insufficient_evidence() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-002: no decision, no pass.

    GIVEN the shadow cycle schedule policy with no owner decision recorded
    WHEN the timer policy prerequisite is assessed
    THEN it is insufficient evidence with `TIMER_POLICY_UNDECIDED`, it states
    that the proof left the timer disabled, the composed verdict is insufficient
    evidence, and the readiness still reports the timer as not enabled
    """

    finding = assess_timer_policy(None)
    assert finding.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert finding.reason is ReviewReason.TIMER_POLICY_UNDECIDED
    assert "disabled" in (finding.detail or "")

    readiness = all_pass_readiness(timer_decision=None)
    assert readiness.timer_policy is None
    assert readiness.verdict is GateStatus.INSUFFICIENT_EVIDENCE
    assert readiness.ready_for_influence_review is False
    assert readiness.timer_enabled is False
    assert readiness.authority_granted is False


def test_ac_002_a_recorded_owner_decision_never_enables_the_timer() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-002: a decision is not an enable.

    GIVEN a recorded owner decision for each declared schedule policy, including
    oneshot and timer
    WHEN the timer policy prerequisite is assessed and the record is inspected
    THEN each recognised decision is satisfied, every one of them still reports
    the timer as not enabled, no authority is granted and nothing is scheduled
    """

    for policy in TimerPolicy:
        decision = owner_decision(policy=policy)
        finding = assess_timer_policy(decision)
        assert finding.status is GateStatus.PASSED
        assert finding.reason is None
        assert policy.value in (finding.detail or "")

        readiness = all_pass_readiness(timer_decision=decision)
        assert readiness.verdict is GateStatus.PASSED
        assert readiness.timer_policy is policy
        assert readiness.timer_enabled is False
        assert readiness.can_influence is False
        assert readiness.authority_granted is False
        assert readiness.trade_authority_changed is False
        assert readiness.automatic_promotion is False


def test_ac_002_an_owner_decision_without_a_named_owner_is_refused() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-002: malformed decisions fail closed.

    GIVEN owner decisions with no deciding owner, with an unknown policy, with a
    naive timestamp, with an unsupported schema version, and a value that is not
    a decision at all
    WHEN each is offered to the timer policy prerequisite
    THEN each is refused rather than accepted as a decision
    """

    with pytest.raises(ShadowReviewError):
        TimerPolicyDecision(
            policy=TimerPolicy.DISABLED, decided_by="   ", decided_at=T0
        )
    with pytest.raises(ShadowReviewError):
        TimerPolicyDecision(policy="sometimes", decided_by="owner-opip", decided_at=T0)
    with pytest.raises(ValueError):
        TimerPolicyDecision(
            policy=TimerPolicy.DISABLED,
            decided_by="owner-opip",
            decided_at=datetime(2026, 10, 1),
        )
    with pytest.raises(ShadowReviewError):
        TimerPolicyDecision(
            policy=TimerPolicy.DISABLED,
            decided_by="owner-opip",
            decided_at=T0,
            schema_version=99,
        )
    with pytest.raises(ShadowReviewError):
        assess_timer_policy("disabled")


# ---------------------------------------------------------------------------
# AC-003 prospective cases
# ---------------------------------------------------------------------------


def test_ac_003_only_matured_sealed_cases_count_as_prospective_evidence() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-003: real cases, matured.

    GIVEN the cases the Committee sealed, each with its own committed horizon
    WHEN the case population is measured and the prerequisite assessed
    THEN every sealed case whose own horizon has elapsed is counted, the package's
    existing support convention decides the verdict, and a matured count below it
    is insufficient evidence rather than a pass
    """

    population = measure_case_population(
        predictions=sealed_population(MINIMUM_MATURED_CASES),
        assessed_at=ASSESSED_AT,
    )
    assert population.sealed_cases == MINIMUM_MATURED_CASES
    assert population.matured == MINIMUM_MATURED_CASES
    assert population.excluded == ()
    assert population.ineligible == 0

    finding = assess_prospective_cases(population=population)
    assert finding.status is GateStatus.PASSED
    assert finding.reason is None
    assert str(MINIMUM_MATURED_CASES) in (finding.detail or "")

    below_support = measure_case_population(
        predictions=sealed_population(MINIMUM_MATURED_CASES - 1),
        assessed_at=ASSESSED_AT,
    )
    below = assess_prospective_cases(population=below_support)
    assert below.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert below.reason is ReviewReason.MATURED_CASES_BELOW_SUPPORT

    empty = assess_prospective_cases(
        population=measure_case_population(predictions=(), assessed_at=ASSESSED_AT)
    )
    assert empty.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert empty.reason is ReviewReason.NO_MATURED_PROSPECTIVE_CASES

    with pytest.raises(ShadowReviewError):
        assess_prospective_cases(population=below_support, minimum_support=0)


def test_ac_003_an_immature_or_ineligible_case_is_reported_not_dropped() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-003: nothing vanishes.

    GIVEN a sealed case whose own horizon has not yet elapsed and an
    ineligibility record the Committee itself wrote
    WHEN the case population is measured
    THEN the immature case is reported as unmatured with its reason instead of
    being matured early, the ineligible case is reported with the reason the
    Committee recorded instead of being dropped, and a population whose matured
    count is below the support convention is insufficient evidence
    """

    immature = sealed_prediction(
        "case-immature",
        sealed_at=ASSESSED_AT - timedelta(hours=1),
        horizon_seconds=HORIZON_SECONDS,
    )
    population = measure_case_population(
        predictions=(*sealed_population(2), immature),
        assessed_at=ASSESSED_AT,
        ineligible=(ineligibility("case-ineligible"),),
    )
    assert population.sealed_cases == 3
    assert population.matured == 2
    assert population.ineligible == 1
    reported = dict(population.excluded)
    assert reported["case-immature"] == "HORIZON_NOT_ELAPSED"
    assert reported["case-ineligible"] == "RELEASE_DRIFT"
    assert len(population.excluded) == 2

    finding = assess_prospective_cases(population=population)
    assert finding.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert finding.reason is ReviewReason.MATURED_CASES_BELOW_SUPPORT

    with pytest.raises(ShadowReviewError):
        CasePopulationEvidence(sealed_cases=1, matured=2)
    with pytest.raises(ShadowReviewError):
        CasePopulationEvidence(sealed_cases=1, matured=1, excluded=(("case", ""),))


def test_ac_003_a_stand_in_case_is_refused_rather_than_counted() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-003: no mocks in the population.

    GIVEN a stand-in object that resembles a sealed case, a mapping that carries
    case-shaped keys, a mock that satisfies the class check, and an ineligibility
    stand-in
    WHEN the case population is measured
    THEN each is refused rather than counted as prospective evidence
    """

    class StandIn:
        case_id = "case-stand-in"

        def outcome_eligible_at(self) -> datetime:
            return T0

    with pytest.raises(ShadowReviewError):
        measure_case_population(
            predictions=(StandIn(),), assessed_at=ASSESSED_AT
        )
    with pytest.raises(ShadowReviewError):
        measure_case_population(
            predictions=({"case_id": "case-mapping"},), assessed_at=ASSESSED_AT
        )
    with pytest.raises(ShadowReviewError):
        measure_case_population(
            predictions=(Mock(spec=SealedPrediction),), assessed_at=ASSESSED_AT
        )
    with pytest.raises(ShadowReviewError):
        measure_case_population(
            predictions=sealed_population(1),
            assessed_at=ASSESSED_AT,
            ineligible=({"reason": "RELEASE_DRIFT"},),
        )


# ---------------------------------------------------------------------------
# AC-004 durable weakness findings
# ---------------------------------------------------------------------------


def test_ac_004_findings_held_only_in_process_are_insufficient_evidence() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-004: not durable, not evidence.

    GIVEN the in-process weakness finding registry, in turn no stream behind it
    and a stream name that is a path, an absolute location or a traversal
    WHEN the durability prerequisite is assessed
    THEN each is insufficient evidence with `WEAKNESS_FINDINGS_NOT_DURABLE`, a
    properly declared bounded JSONL stream is the only thing that satisfies it,
    and the composed verdict for evidence in the state the roadmap describes is
    insufficient evidence rather than a pass
    """

    registry = WeaknessRegistry()
    assert registry.findings() == ()

    for offered in (
        None,
        "",
        "   ",
        "/var/data/opip/weakness_findings.jsonl",
        "archive/weakness_findings.jsonl",
        "..\\weakness_findings.jsonl",
        ".weakness_findings.jsonl",
        "  weakness_findings.jsonl",
        "weakness_findings.json",
        "weakness_findings.jsonl.bak",
    ):
        assert is_durable_stream_name(offered) is False, offered
        finding = assess_weakness_finding_durability(
            registry=registry, durable_stream=offered
        )
        assert finding.status is GateStatus.INSUFFICIENT_EVIDENCE
        assert finding.reason is ReviewReason.WEAKNESS_FINDINGS_NOT_DURABLE

    assert is_durable_stream_name(DURABLE_STREAM) is True
    declared = assess_weakness_finding_durability(
        registry=registry, durable_stream=DURABLE_STREAM
    )
    assert declared.status is GateStatus.PASSED
    assert declared.reason is None

    absent = assess_weakness_finding_durability(
        registry=None, durable_stream=DURABLE_STREAM
    )
    assert absent.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert absent.reason is ReviewReason.WEAKNESS_FINDINGS_NOT_DURABLE

    readiness = all_pass_readiness(durable_weakness_stream=None)
    assert readiness.verdict is GateStatus.INSUFFICIENT_EVIDENCE
    assert (
        readiness.finding(ReviewPrerequisite.DURABLE_WEAKNESS_FINDINGS).reason
        is ReviewReason.WEAKNESS_FINDINGS_NOT_DURABLE
    )

    with pytest.raises(ShadowReviewError):
        assess_weakness_finding_durability(registry={}, durable_stream=DURABLE_STREAM)


def test_ac_004_no_durable_weakness_stream_is_declared_by_the_evidence_store() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-004: the gap is measured.

    GIVEN the Committee evidence store the repository actually ships, with the
    durable JSONL streams it declares
    WHEN those declared streams are compared with the facts a weakness finding
    would need
    THEN the store declares no weakness-findings stream, so the honest verdict
    for today's evidence is insufficient evidence rather than a pass
    """

    store_source = module_source(STORE_MODULE)
    declared = sorted(
        re.findall(r'data_file=self\.root / "([^"]+)"', store_source)
    )
    assert declared
    assert all(name.endswith(".jsonl") for name in declared)
    assert all(is_durable_stream_name(name) for name in declared)
    assert "weakness" not in store_source.lower()
    assert not any("weakness" in name or "finding" in name for name in declared)

    resolved = next(
        (name for name in declared if "weakness" in name or "finding" in name), None
    )
    assert resolved is None

    registry = WeaknessRegistry()
    finding = assess_weakness_finding_durability(
        registry=registry, durable_stream=resolved
    )
    assert finding.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert finding.reason is ReviewReason.WEAKNESS_FINDINGS_NOT_DURABLE
    assert "in process" in (finding.detail or "")


# ---------------------------------------------------------------------------
# AC-005 trust evidence and cost
# ---------------------------------------------------------------------------


def test_ac_005_a_measured_cost_with_clear_gates_satisfies_trust_evidence() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-005: measured evidence only.

    GIVEN the gates the package's own gate seam declares, with a measured
    attributable cost, and in turn a blocked gate and an unmeasurable gate
    WHEN the trust evidence prerequisite is assessed
    THEN a measured cost with clear gates is satisfied, a blocked gate blocks
    with `TRUST_GATE_BLOCKED`, an unmeasurable gate is insufficient evidence with
    `TRUST_GATE_INSUFFICIENT_EVIDENCE`, and no gate result at all is insufficient
    evidence with `TRUST_EVIDENCE_UNMEASURED`
    """

    assert GateName.OPERATING_COST_KNOWN in set(GateName)
    clear = assess_trust_evidence(gates=passing_gates(), investment=investment(cost=2_500))
    assert clear.status is GateStatus.PASSED
    assert clear.reason is None
    assert str(len(passing_gates())) in (clear.detail or "")

    blocked_gates = tuple(
        GateResult(
            gate=name,
            status=GateStatus.BLOCKED,
            reason="3 evidence integrity violations recorded",
        )
        if name is GateName.EVIDENCE_INTEGRITY
        else GateResult(gate=name, status=GateStatus.PASSED)
        for name in GateName
    )
    blocked = assess_trust_evidence(
        gates=blocked_gates, investment=investment(cost=2_500)
    )
    assert blocked.status is GateStatus.BLOCKED
    assert blocked.reason is ReviewReason.TRUST_GATE_BLOCKED
    assert "EVIDENCE_INTEGRITY" in (blocked.detail or "")

    thin_gates = tuple(
        GateResult(
            gate=name,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=f"4 completed cases, minimum {MINIMUM_MATURED_CASES}",
        )
        if name is GateName.COVERAGE
        else GateResult(gate=name, status=GateStatus.PASSED)
        for name in GateName
    )
    thin = assess_trust_evidence(gates=thin_gates, investment=investment(cost=2_500))
    assert thin.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert thin.reason is ReviewReason.TRUST_GATE_INSUFFICIENT_EVIDENCE
    assert "COVERAGE" in (thin.detail or "")

    unmeasured = assess_trust_evidence(gates=(), investment=investment(cost=2_500))
    assert unmeasured.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert unmeasured.reason is ReviewReason.TRUST_EVIDENCE_UNMEASURED

    readiness = all_pass_readiness()
    assert readiness.verdict is GateStatus.PASSED
    assert readiness.finding(ReviewPrerequisite.TRUST_EVIDENCE).status is (
        GateStatus.PASSED
    )

    with pytest.raises(ShadowReviewError):
        assess_trust_evidence(gates=passing_gates(), investment={})
    with pytest.raises(ShadowReviewError):
        assess_trust_evidence(
            gates=(("EVIDENCE_INTEGRITY", "PASSED"),), investment=investment(cost=1)
        )


def test_ac_005_an_unknown_cost_can_never_yield_a_pass() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-005: unknown stays unknown.

    GIVEN an investment whose attributable cost was never measured, in turn while
    its own operating cost gate claims a pass and with no cost gate supplied
    WHEN the trust evidence prerequisite is assessed and the record inspected
    THEN the unknown cost blocks with `COST_UNKNOWN` instead of reading as zero or
    as free, a cost gate that passed with nothing measured is refused as a
    contradiction, and the composed verdict cannot pass
    """

    unknown = investment(cost=None)
    assert unknown.attributable_cost_microunits is None

    contradiction = assess_trust_evidence(gates=passing_gates(), investment=unknown)
    assert contradiction.status is GateStatus.BLOCKED
    assert contradiction.reason is ReviewReason.COST_UNKNOWN
    assert "cannot be reported as known" in (contradiction.detail or "")

    without_cost_gate = tuple(
        entry for entry in passing_gates() if entry.gate is not GateName.OPERATING_COST_KNOWN
    )
    assert len(without_cost_gate) == len(passing_gates()) - 1
    plain = assess_trust_evidence(gates=without_cost_gate, investment=unknown)
    assert plain.status is GateStatus.BLOCKED
    assert plain.reason is ReviewReason.COST_UNKNOWN
    assert "cannot yield a pass" in (plain.detail or "")

    readiness = all_pass_readiness(investment=unknown)
    assert readiness.verdict is GateStatus.BLOCKED
    assert readiness.ready_for_influence_review is False
    assert ReviewReason.COST_UNKNOWN in readiness.blocking_reasons


# ---------------------------------------------------------------------------
# AC-006 import boundary
# ---------------------------------------------------------------------------


def test_ac_006_the_real_review_surface_imports_no_job_or_api_plane() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-006: measured, not asserted.

    GIVEN the modules that make up the Committee review surface, including the
    module this increment adds
    WHEN the import boundary is measured from their sources and the prerequisite
    is assessed
    THEN the boundary reports no violation, an independent per-module check of
    the same sources agrees, and the composed verdict can pass on measured
    evidence
    """

    modules = sorted(COMMITTEE_DIR.glob("*.py"))
    assert len(modules) > 10
    assert MODULE in modules

    measured = measure_import_boundary(modules)
    assert measured == ()
    finding = assess_import_boundary(violations=measured)
    assert finding.status is GateStatus.PASSED
    assert finding.reason is None

    offenders = sorted(
        path.name
        for path in modules
        if any(
            name == root or name.startswith(f"{root}.")
            for name in module_imports(path)
            for root in FORBIDDEN_REVIEW_IMPORTS
        )
    )
    assert offenders == []
    assert FORBIDDEN_REVIEW_IMPORTS == ("app.jobs", "app.api")

    readiness = all_pass_readiness(import_violations=measured)
    assert readiness.verdict is GateStatus.PASSED


def test_ac_006_a_forbidden_import_blocks_the_boundary(tmp_path) -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-006: a job or API reach blocks.

    GIVEN a module that imports a job plane and an API plane, an unreadable
    module, and a clean module
    WHEN the boundary is measured and its prerequisite assessed
    THEN the offending imports block with `FORBIDDEN_IMPORT_PRESENT` naming the
    module and the import, an unmeasured boundary is insufficient evidence, an
    unreadable module fails closed, and the clean module passes
    """

    offender = tmp_path / "review_helper.py"
    offender.write_text(
        "from app.jobs import scheduler\nimport app.api.routes\n",
        encoding="utf-8",
    )
    violations = measure_import_boundary((offender,))
    assert len(violations) == 2
    assert all("review_helper.py" in entry for entry in violations)
    assert any("app.jobs" in entry for entry in violations)
    assert any("app.api.routes" in entry for entry in violations)

    finding = assess_import_boundary(violations=violations)
    assert finding.status is GateStatus.BLOCKED
    assert finding.reason is ReviewReason.FORBIDDEN_IMPORT_PRESENT
    assert "review_helper.py" in (finding.detail or "")

    unmeasured = assess_import_boundary(violations=None)
    assert unmeasured.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert unmeasured.reason is ReviewReason.IMPORT_BOUNDARY_UNMEASURED

    with pytest.raises(ShadowReviewError):
        measure_import_boundary((tmp_path / "missing.py",))
    broken = tmp_path / "broken.py"
    broken.write_text("def (:\n", encoding="utf-8")
    with pytest.raises(ShadowReviewError):
        measure_import_boundary((broken,))

    clean = tmp_path / "clean_helper.py"
    clean.write_text("import ast\nfrom pathlib import Path\n", encoding="utf-8")
    assert measure_import_boundary((clean,)) == ()
    assert assess_import_boundary(violations=()).status is GateStatus.PASSED

    blocked_readiness = all_pass_readiness(import_violations=violations)
    assert blocked_readiness.verdict is GateStatus.BLOCKED
    assert (
        blocked_readiness.finding(ReviewPrerequisite.IMPORT_BOUNDARY).reason
        is ReviewReason.FORBIDDEN_IMPORT_PRESENT
    )


# ---------------------------------------------------------------------------
# AC-007 dormant and non-authoritative
# ---------------------------------------------------------------------------


def test_ac_007_a_passed_readiness_still_grants_nothing() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-007: readiness is not authority.

    GIVEN a readiness record whose every prerequisite passed
    WHEN the record is inspected and a caller tries to make it grant something
    THEN it measures only and reports no authority, no trading authority change,
    no automatic promotion and no timer enablement, it cannot be constructed or
    altered with any of those set, and a passing verdict means ready to be
    reviewed rather than authorized to act
    """

    readiness = all_pass_readiness()
    assert readiness.verdict is GateStatus.PASSED
    assert readiness.ready_for_influence_review is True
    assert readiness.measurement_only is True
    assert readiness.can_influence is False
    assert readiness.authority_granted is False
    assert readiness.trade_authority_changed is False
    assert readiness.timer_enabled is False
    assert readiness.automatic_promotion is False

    for field_name in (
        "can_influence",
        "authority_granted",
        "trade_authority_changed",
        "timer_enabled",
        "automatic_promotion",
    ):
        with pytest.raises(ShadowReviewError):
            dataclass_replace(readiness, **{field_name: True})
    with pytest.raises(ShadowReviewError):
        dataclass_replace(readiness, measurement_only=False)
    with pytest.raises(ShadowReviewError):
        dataclass_replace(readiness, schema_version=99)

    assert readiness.readiness_id == all_pass_readiness().readiness_id
    assert readiness.readiness_id != all_pass_readiness(
        timer_decision=owner_decision(policy=TimerPolicy.TIMER)
    ).readiness_id


def test_ac_007_the_readiness_surface_is_dormant_and_the_posture_is_unchanged() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-007: dormant evidence only.

    GIVEN the readiness module, the whole application package, the Committee
    enablement vocabulary and the repository release posture
    WHEN each is inspected
    THEN the module reaches no order, network, storage, clock, randomness or
    secret surface, imports nothing outside its declared dependencies, has no
    runtime consumer anywhere in the application package, the Committee mode
    vocabulary is unchanged, and the profiles, Compose literals and paper-v2 mode
    are unchanged
    """

    source = module_source(MODULE)
    for token in FORBIDDEN_RUNTIME_TOKENS:
        assert token not in source, token
    imported = module_imports(MODULE)
    assert imported <= ALLOWED_IMPORTS, sorted(imported - ALLOWED_IMPORTS)
    assert FORBIDDEN_REVIEW_IMPORTS[0] not in imported
    assert FORBIDDEN_REVIEW_IMPORTS[1] not in imported

    consumer_tokens = (
        "assess_shadow_review_readiness",
        "ShadowReviewReadiness",
        "ReviewPrerequisite",
        "TimerPolicyDecision",
    )
    consumers = sorted(
        path.relative_to(APP_ROOT).as_posix()
        for path in (APP_ROOT / "app").rglob("*.py")
        if path != MODULE
        and any(token in module_source(path) for token in consumer_tokens)
    )
    assert consumers == []

    assert COMMITTEE_MODES == frozenset({"off", "shadow"})

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
    committee_keys = sorted(key for key in allowed if key.startswith("OPIP_COMMITTEE"))
    assert committee_keys == ["OPIP_COMMITTEE_MODE"]
    assert allowed["OPIP_COMMITTEE_MODE"] == "off"
    assert not any(
        marker in key
        for key in allowed
        for marker in ("SHADOW_REVIEW", "READINESS", "COMMITTEE_TIMER")
    )

    compose = yaml.safe_load(
        (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["ohm-trade-agent"]["environment"]
    assert environment["OPIP_PAPER_V2_MODE"] == "off"
    assert environment["OPIP_FEATURE_BUS_MODE"] == "shadow"


# ---------------------------------------------------------------------------
# AC-008 bookkeeping
# ---------------------------------------------------------------------------


def test_ac_008_the_increment_contract_pointer_and_build_report_are_self_consistent() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-008: contract, pointer, report.

    GIVEN this increment, its scope contract, the movable pointer, the
    acceptance-test index and the autonomous build report
    WHEN the contract is parsed and checked against that index and the report
    THEN every criterion is traced to a matching acceptance-marked test and mapped
    to a changed path, the pointer resolves to a declared contract instead of
    being pinned here, `UNAPPROVED SCOPE CHANGES` is `NONE`, and the report
    records this increment as delivered while the increments still outstanding
    stay outstanding
    """

    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    ours = [contract for contract in contracts if contract.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert CONTRACT.name == f"{INCREMENT}.md"
    assert contract.sections["INCREMENT"] == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert "ATDD-R5-F-prospective-outcome-integration" in {
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

    assert BUILD_REPORT.exists()
    report = BUILD_REPORT.read_text(encoding="utf-8")
    assert INCREMENT in report
    assert "ATDD-R5-F-prospective-outcome-integration" in report
    assert "pull/370" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "| R7 governed retirement | OUTSTANDING |" in report


def test_ac_008_the_module_declares_its_whole_public_surface() -> None:
    """ATDD-R6-committee-shadow-review-readiness/AC-008: a declared surface.

    GIVEN the readiness module's declared export list and a readiness record
    WHEN the exports are compared with the intended surface and the record is
    inspected
    THEN the exported set is exactly the intended one, every exported name exists,
    every prerequisite is reported exactly once, and a record that omits or
    repeats a prerequisite cannot be built
    """

    assert set(shadow_review.__all__) == EXPECTED_EXPORTS
    for name in shadow_review.__all__:
        assert hasattr(shadow_review, name), name
    assert SHADOW_REVIEW_SCHEMA_VERSION == 1

    readiness = all_pass_readiness()
    assert {entry.prerequisite for entry in readiness.findings} == set(
        ReviewPrerequisite
    )
    assert len(readiness.findings) == len(ReviewPrerequisite)

    with pytest.raises(ShadowReviewError):
        dataclass_replace(
            readiness, findings=readiness.findings + (readiness.findings[0],)
        )
    with pytest.raises(ShadowReviewError):
        dataclass_replace(readiness, findings=readiness.findings[:-1])
    with pytest.raises(ShadowReviewError):
        dataclass_replace(readiness, findings=())
    with pytest.raises(ShadowReviewError):
        dataclass_replace(readiness, core_release_sha="   ")
