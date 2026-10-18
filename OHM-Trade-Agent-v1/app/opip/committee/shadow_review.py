"""Committee shadow review readiness assessment.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

`R6` asks one question: is the Committee shadow package that already exists ready
to be put in front of an owner influence review? This module answers it from
measured evidence and grants nothing. It adds no committee feature, no role, no
transport, no store, no scheduler and no timer. It composes seams the package
already owns - ``prospective.assert_release_matches`` for release identity,
``trust.GateResult``/``GateStatus``/``CommitteeInvestment`` for the gate and cost
vocabulary, ``weakness.WeaknessRegistry`` for findings - into one verdict per
required prerequisite, each of which is ``PASSED``, ``BLOCKED`` or
``INSUFFICIENT_EVIDENCE``.

It refuses a pass that would rest on a drifted release, an undecided or
undeclared timer policy, unmatured or stand-in cases, findings held only in
process, an unknown cost, or an unmeasured import boundary. A ``PASSED`` verdict
still means "ready to be reviewed", never "authorized": nothing here can enable
the Committee timer, widen Committee influence, or change a risk or execution
gate.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from app.opip.committee.prospective import (
    ProspectiveIneligibility,
    ProspectiveReleaseDriftError,
    SealedPrediction,
    assert_release_matches,
)
from app.opip.committee.trust import (
    MINIMUM_MATURED_CASES,
    CommitteeInvestment,
    GateName,
    GateResult,
    GateStatus,
)
from app.opip.committee.weakness import WeaknessRegistry
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

SHADOW_REVIEW_SCHEMA_VERSION = 1
SHADOW_REVIEW_IDENTITY_DOMAIN = "COMMITTEE-SHADOW-REVIEW-READINESS"

#: Import roots the roadmap forbids in the Committee review surface. The
#: Committee is measurement-only evidence; a job or API import would make a
#: review depend on an authority plane.
FORBIDDEN_REVIEW_IMPORTS: tuple[str, ...] = ("app.jobs", "app.api")

#: A durable evidence stream is one bounded JSONL segment name, not a path.
DURABLE_STREAM_SUFFIX = ".jsonl"


class ShadowReviewError(ValueError):
    """A readiness assessment was requested with unusable input."""


class ReviewPrerequisite(str, Enum):
    """The prerequisites the roadmap names before any influence review."""

    RELEASE_IDENTITY = "RELEASE_IDENTITY"
    TIMER_POLICY = "TIMER_POLICY"
    PROSPECTIVE_CASES = "PROSPECTIVE_CASES"
    DURABLE_WEAKNESS_FINDINGS = "DURABLE_WEAKNESS_FINDINGS"
    TRUST_EVIDENCE = "TRUST_EVIDENCE"
    IMPORT_BOUNDARY = "IMPORT_BOUNDARY"


class ReviewReason(str, Enum):
    """Closed vocabulary for why a prerequisite is not satisfied.

    A satisfied prerequisite carries no reason. An unsatisfied one must name one
    of these, so an unmet prerequisite is always auditable rather than a bare
    boolean.
    """

    RELEASE_DRIFT = "RELEASE_DRIFT"
    RELEASE_IDENTITY_UNMEASURED = "RELEASE_IDENTITY_UNMEASURED"
    TIMER_POLICY_UNDECIDED = "TIMER_POLICY_UNDECIDED"
    NO_MATURED_PROSPECTIVE_CASES = "NO_MATURED_PROSPECTIVE_CASES"
    MATURED_CASES_BELOW_SUPPORT = "MATURED_CASES_BELOW_SUPPORT"
    WEAKNESS_FINDINGS_NOT_DURABLE = "WEAKNESS_FINDINGS_NOT_DURABLE"
    TRUST_EVIDENCE_UNMEASURED = "TRUST_EVIDENCE_UNMEASURED"
    TRUST_GATE_BLOCKED = "TRUST_GATE_BLOCKED"
    TRUST_GATE_INSUFFICIENT_EVIDENCE = "TRUST_GATE_INSUFFICIENT_EVIDENCE"
    COST_UNKNOWN = "COST_UNKNOWN"
    IMPORT_BOUNDARY_UNMEASURED = "IMPORT_BOUNDARY_UNMEASURED"
    FORBIDDEN_IMPORT_PRESENT = "FORBIDDEN_IMPORT_PRESENT"


class TimerPolicy(str, Enum):
    """The schedule policies an owner may declare for the shadow cycle."""

    DISABLED = "DISABLED"
    ONESHOT = "ONESHOT"
    TIMER = "TIMER"


@dataclass(frozen=True)
class PrerequisiteFinding:
    """One prerequisite's outcome, with the reason it is not satisfied."""

    prerequisite: ReviewPrerequisite
    status: GateStatus
    reason: ReviewReason | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.prerequisite, ReviewPrerequisite):
            raise ShadowReviewError("invalid prerequisite")
        if not isinstance(self.status, GateStatus):
            raise ShadowReviewError("invalid prerequisite status")
        if self.status is GateStatus.PASSED:
            if self.reason is not None:
                raise ShadowReviewError(
                    "a satisfied prerequisite carries no reason"
                )
        elif not isinstance(self.reason, ReviewReason):
            raise ShadowReviewError(
                f"{self.prerequisite.value} must state why it is not satisfied"
            )
        if self.detail is not None and not isinstance(self.detail, str):
            raise ShadowReviewError("detail must be a string or null")

    @property
    def satisfied(self) -> bool:
        return self.status is GateStatus.PASSED


@dataclass(frozen=True)
class TimerPolicyDecision:
    """A recorded owner decision about the shadow cycle's schedule.

    The roadmap leaves the timer an explicit owner decision. This record states
    that the decision exists; it is not a way to take it, and holding one can
    never enable a scheduler.
    """

    policy: TimerPolicy
    decided_by: str
    decided_at: datetime
    note: str | None = None
    schema_version: int = SHADOW_REVIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or (
            self.schema_version != SHADOW_REVIEW_SCHEMA_VERSION
        ):
            raise ShadowReviewError("unsupported TimerPolicyDecision schema_version")
        if not isinstance(self.policy, TimerPolicy):
            raise ShadowReviewError("an owner decision must declare a known policy")
        owner = str(self.decided_by or "").strip()
        if not owner:
            raise ShadowReviewError("an owner decision must name the deciding owner")
        object.__setattr__(self, "decided_by", owner)
        object.__setattr__(
            self,
            "decided_at",
            require_utc(self.decided_at, field_name="decided_at"),
        )


@dataclass(frozen=True)
class CasePopulationEvidence:
    """The prospective case population measured at assessment time.

    ``sealed_cases`` are the T0 records the Committee actually sealed, ``matured``
    counts those whose own committed horizon has elapsed, and ``excluded`` names
    the cases that cannot be scored with the reason each was excluded. An excluded
    case is reported, never dropped: the roadmap requires real prospective cases,
    and a silently vanished case would hide a missing population.
    """

    sealed_cases: int
    matured: int
    excluded: tuple[tuple[str, str], ...] = ()
    ineligible: int = 0

    def __post_init__(self) -> None:
        for field_name in ("sealed_cases", "matured", "ineligible"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ShadowReviewError(f"{field_name} must be a non-negative integer")
        if self.matured > self.sealed_cases:
            raise ShadowReviewError("matured cases cannot exceed sealed cases")
        for entry in self.excluded:
            if len(entry) != 2 or not all(str(part).strip() for part in entry):
                raise ShadowReviewError("each excluded case names its case and reason")


@dataclass(frozen=True)
class ShadowReviewReadiness:
    """The per-prerequisite verdict, and nothing that could influence a decision."""

    assessed_at: datetime
    core_release_sha: str
    committee_release_sha: str
    findings: tuple[PrerequisiteFinding, ...]
    case_population: CasePopulationEvidence | None = None
    timer_policy: TimerPolicy | None = None
    measurement_only: bool = True
    can_influence: bool = False
    authority_granted: bool = False
    trade_authority_changed: bool = False
    timer_enabled: bool = False
    automatic_promotion: bool = False
    schema_version: int = SHADOW_REVIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or (
            self.schema_version != SHADOW_REVIEW_SCHEMA_VERSION
        ):
            raise ShadowReviewError("unsupported ShadowReviewReadiness schema_version")
        if self.measurement_only is not True:
            raise ShadowReviewError("a readiness assessment is measurement only")
        for flag in (
            "can_influence",
            "authority_granted",
            "trade_authority_changed",
            "timer_enabled",
            "automatic_promotion",
        ):
            if getattr(self, flag) is not False:
                raise ShadowReviewError(
                    f"a readiness assessment cannot set {flag}: it grants nothing"
                )
        object.__setattr__(
            self,
            "assessed_at",
            require_utc(self.assessed_at, field_name="assessed_at"),
        )
        for field_name in ("core_release_sha", "committee_release_sha"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ShadowReviewError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)
        seen: set[ReviewPrerequisite] = set()
        for entry in self.findings:
            if not isinstance(entry, PrerequisiteFinding):
                raise ShadowReviewError("findings must be PrerequisiteFinding values")
            if entry.prerequisite in seen:
                raise ShadowReviewError(
                    f"prerequisite {entry.prerequisite.value} is reported twice"
                )
            seen.add(entry.prerequisite)
        if seen != set(ReviewPrerequisite):
            missing = sorted(
                entry.value for entry in set(ReviewPrerequisite) - seen
            )
            raise ShadowReviewError(
                f"every prerequisite must be reported exactly once; missing {missing}"
            )
        if self.case_population is not None and not isinstance(
            self.case_population, CasePopulationEvidence
        ):
            raise ShadowReviewError("case_population must be CasePopulationEvidence")

    # ------------------------------------------------------------ derived views

    @property
    def verdict(self) -> GateStatus:
        """The overall status: one blocked prerequisite blocks the review."""

        statuses = [entry.status for entry in self.findings]
        if not statuses:
            return GateStatus.INSUFFICIENT_EVIDENCE
        if any(status is GateStatus.BLOCKED for status in statuses):
            return GateStatus.BLOCKED
        if any(status is GateStatus.INSUFFICIENT_EVIDENCE for status in statuses):
            return GateStatus.INSUFFICIENT_EVIDENCE
        return GateStatus.PASSED

    @property
    def ready_for_influence_review(self) -> bool:
        """Whether the package may be reviewed for influence. Never authority."""

        return self.verdict is GateStatus.PASSED

    def finding(self, prerequisite: ReviewPrerequisite) -> PrerequisiteFinding:
        for entry in self.findings:
            if entry.prerequisite is prerequisite:
                return entry
        raise ShadowReviewError(f"prerequisite {prerequisite.value} was not assessed")

    @property
    def blocking_reasons(self) -> tuple[ReviewReason, ...]:
        return tuple(
            entry.reason for entry in self.findings if entry.reason is not None
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "assessed_at": self.assessed_at,
            "core_release_sha": self.core_release_sha,
            "committee_release_sha": self.committee_release_sha,
            "timer_policy": self.timer_policy,
            "findings": tuple(
                (
                    entry.prerequisite.value,
                    entry.status.value,
                    entry.reason.value if entry.reason is not None else None,
                )
                for entry in self.findings
            ),
            "sealed_cases": (
                None
                if self.case_population is None
                else self.case_population.sealed_cases
            ),
            "matured_cases": (
                None if self.case_population is None else self.case_population.matured
            ),
        }

    @property
    def readiness_id(self) -> str:
        return stable_hash(SHADOW_REVIEW_IDENTITY_DOMAIN, self.identity_payload())


# ---------------------------------------------------------------------------
# Measurements the assessment consumes
# ---------------------------------------------------------------------------


def is_durable_stream_name(name: object) -> bool:
    """Whether a value is one bounded durable JSONL stream name.

    A path, an absolute location, a traversal segment or a directory is refused:
    a durable evidence stream is a single name inside the evidence root, and
    anything else would be an unspecified second location for the same facts.
    """

    if not isinstance(name, str):
        return False
    candidate = name.strip()
    if not candidate or not candidate.endswith(DURABLE_STREAM_SUFFIX):
        return False
    if candidate != name or candidate.startswith(("/", "\\", "~")):
        return False
    if "/" in candidate or "\\" in candidate or ":" in candidate:
        return False
    if candidate in {".", ".."} or candidate.startswith("."):
        return False
    return True


def measure_import_boundary(module_paths: Iterable[Path]) -> tuple[str, ...]:
    """Return the forbidden imports found in the given modules.

    The Committee review surface must not reach a job or API authority plane, so
    the boundary is measured from the modules themselves rather than asserted by
    the caller. Unreadable input is refused instead of silently measuring nothing.
    """

    violations: list[str] = []
    for path in module_paths:
        module_path = Path(path)
        try:
            source = module_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(module_path))
        except (OSError, SyntaxError) as error:  # pragma: no cover - fail closed
            raise ShadowReviewError(
                f"cannot measure the import boundary of {module_path}: {error}"
            ) from error
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name in FORBIDDEN_REVIEW_IMPORTS or name.startswith(
                    tuple(f"{root}." for root in FORBIDDEN_REVIEW_IMPORTS)
                ):
                    violations.append(
                        f"{module_path.name}: {name} at line {getattr(node, 'lineno', 0)}"
                    )
    return tuple(sorted(violations))


# ---------------------------------------------------------------------------
# One prerequisite at a time
# ---------------------------------------------------------------------------


def assess_release_identity(
    *, predictions: Sequence[SealedPrediction], core_release_sha: str
) -> PrerequisiteFinding:
    """The Committee release under review must be the core release under test."""

    core = str(core_release_sha or "").strip()
    sealed = tuple(prediction for prediction in predictions)
    if not core or not sealed:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.RELEASE_IDENTITY,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.RELEASE_IDENTITY_UNMEASURED,
            detail=(
                "no sealed prospective case is available to compare a release "
                "identity against"
                if not sealed
                else "no core release identity was supplied"
            ),
        )
    for prediction in sealed:
        if not isinstance(prediction, SealedPrediction):
            raise ShadowReviewError(
                "release identity is measured from sealed predictions"
            )
        try:
            assert_release_matches(
                prediction=prediction, observed_release_sha=core
            )
        except ProspectiveReleaseDriftError as drift:
            return PrerequisiteFinding(
                prerequisite=ReviewPrerequisite.RELEASE_IDENTITY,
                status=GateStatus.BLOCKED,
                reason=ReviewReason.RELEASE_DRIFT,
                detail=str(drift),
            )
    return PrerequisiteFinding(
        prerequisite=ReviewPrerequisite.RELEASE_IDENTITY,
        status=GateStatus.PASSED,
        detail=f"{len(sealed)} sealed case(s) match release {core}",
    )


def assess_timer_policy(
    decision: TimerPolicyDecision | None,
) -> PrerequisiteFinding:
    """The schedule policy must be an explicit owner decision, never a default."""

    if decision is None:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.TIMER_POLICY,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.TIMER_POLICY_UNDECIDED,
            detail=(
                "no owner decision is recorded for the shadow cycle policy; the "
                "2026-09-25 proof left the timer disabled"
            ),
        )
    if not isinstance(decision, TimerPolicyDecision):
        raise ShadowReviewError("the timer policy must be a recorded owner decision")
    return PrerequisiteFinding(
        prerequisite=ReviewPrerequisite.TIMER_POLICY,
        status=GateStatus.PASSED,
        detail=f"owner decision recorded: {decision.policy.value}",
    )


def measure_case_population(
    *,
    predictions: Iterable[SealedPrediction],
    assessed_at: datetime,
    ineligible: Iterable[ProspectiveIneligibility] = (),
) -> CasePopulationEvidence:
    """Measure the prospective case population from the records themselves.

    Only a genuinely sealed prediction is a prospective case: an object that
    merely resembles one - a mapping, a stand-in or a mock that satisfies
    ``isinstance`` - is refused rather than counted, because mock cases are
    exactly what an influence review must not rest on. A case whose own committed
    horizon has not elapsed is reported as unmatured with its reason instead of
    being matured early.
    """

    moment = require_utc(assessed_at, field_name="assessed_at")
    sealed = 0
    matured = 0
    excluded: list[tuple[str, str]] = []
    for prediction in predictions:
        if type(prediction) is not SealedPrediction:
            raise ShadowReviewError(
                "a prospective case must be a sealed prediction produced by the "
                f"committee's own sealing seam; got {type(prediction).__name__}"
            )
        sealed += 1
        if moment >= prediction.outcome_eligible_at():
            matured += 1
        else:
            excluded.append((prediction.case_id, "HORIZON_NOT_ELAPSED"))
    ineligible_count = 0
    for item in ineligible:
        if type(item) is not ProspectiveIneligibility:
            raise ShadowReviewError(
                "an excluded case must carry the committee's own ineligibility "
                f"record; got {type(item).__name__}"
            )
        ineligible_count += 1
        excluded.append((item.case_id, item.reason))
    return CasePopulationEvidence(
        sealed_cases=sealed,
        matured=matured,
        excluded=tuple(excluded),
        ineligible=ineligible_count,
    )


def assess_prospective_cases(
    *,
    population: CasePopulationEvidence,
    minimum_support: int = MINIMUM_MATURED_CASES,
) -> PrerequisiteFinding:
    """Real prospective cases must exist, and enough of them must have matured.

    The support figure is the convention the trust report already uses
    (``trust.MINIMUM_MATURED_CASES``); no threshold is invented here, and a
    caller-supplied override is refused unless it is a positive integer.
    """

    if not isinstance(population, CasePopulationEvidence):
        raise ShadowReviewError("case evidence must be CasePopulationEvidence")
    if type(minimum_support) is not int or minimum_support < 1:
        raise ShadowReviewError("minimum_support must be a positive integer")
    if population.matured == 0:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.PROSPECTIVE_CASES,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.NO_MATURED_PROSPECTIVE_CASES,
            detail=(
                f"{population.sealed_cases} sealed case(s), "
                f"{population.ineligible} ineligible, none matured"
            ),
        )
    if population.matured < minimum_support:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.PROSPECTIVE_CASES,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.MATURED_CASES_BELOW_SUPPORT,
            detail=(
                f"{population.matured} matured case(s), minimum {minimum_support}"
            ),
        )
    return PrerequisiteFinding(
        prerequisite=ReviewPrerequisite.PROSPECTIVE_CASES,
        status=GateStatus.PASSED,
        detail=(
            f"{population.matured} matured of {population.sealed_cases} sealed "
            f"case(s), {len(population.excluded)} reported as excluded"
        ),
    )


def assess_weakness_finding_durability(
    *,
    registry: WeaknessRegistry | None,
    durable_stream: str | None,
) -> PrerequisiteFinding:
    """Findings must be durable, not held only for the life of one process."""

    if registry is None:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.DURABLE_WEAKNESS_FINDINGS,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.WEAKNESS_FINDINGS_NOT_DURABLE,
            detail="no weakness finding registry was supplied",
        )
    if not isinstance(registry, WeaknessRegistry):
        raise ShadowReviewError("weakness findings must be a WeaknessRegistry")
    if not is_durable_stream_name(durable_stream):
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.DURABLE_WEAKNESS_FINDINGS,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.WEAKNESS_FINDINGS_NOT_DURABLE,
            detail=(
                "the registry is held in process; no durable evidence stream "
                "declares weakness findings"
            ),
        )
    return PrerequisiteFinding(
        prerequisite=ReviewPrerequisite.DURABLE_WEAKNESS_FINDINGS,
        status=GateStatus.PASSED,
        detail=(
            f"{len(registry.findings())} finding(s) in "
            f"{str(durable_stream).strip()}"
        ),
    )


def assess_trust_evidence(
    *,
    gates: Sequence[GateResult],
    investment: CommitteeInvestment,
) -> PrerequisiteFinding:
    """A trust report must exist, and an unmeasured cost can never pass.

    Section 18 is explicit that a missing population or an unknown cost cannot
    yield PASS. An unknown cost stays reported as unknown rather than being read
    as free, and a report whose own cost gate claims a measured cost while the
    investment records none is refused as a contradiction.
    """

    if not isinstance(investment, CommitteeInvestment):
        raise ShadowReviewError("trust evidence requires a CommitteeInvestment")
    measured = tuple(gates)
    if not measured:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.TRUST_EVIDENCE,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.TRUST_EVIDENCE_UNMEASURED,
            detail="no trust gate result was supplied",
        )
    for entry in measured:
        if not isinstance(entry, GateResult):
            raise ShadowReviewError("trust evidence is a sequence of GateResult")
    blocked = [
        entry.gate.value
        for entry in measured
        if entry.status is GateStatus.BLOCKED
    ]
    if blocked:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.TRUST_EVIDENCE,
            status=GateStatus.BLOCKED,
            reason=ReviewReason.TRUST_GATE_BLOCKED,
            detail=f"blocked gate(s): {', '.join(sorted(blocked))}",
        )
    insufficient = [
        entry.gate.value
        for entry in measured
        if entry.status is GateStatus.INSUFFICIENT_EVIDENCE
    ]
    if insufficient:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.TRUST_EVIDENCE,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.TRUST_GATE_INSUFFICIENT_EVIDENCE,
            detail=f"unmeasurable gate(s): {', '.join(sorted(insufficient))}",
        )
    if investment.attributable_cost_microunits is None:
        cost_gate = [
            entry for entry in measured if entry.gate is GateName.OPERATING_COST_KNOWN
        ]
        if cost_gate and cost_gate[0].status is GateStatus.PASSED:
            return PrerequisiteFinding(
                prerequisite=ReviewPrerequisite.TRUST_EVIDENCE,
                status=GateStatus.BLOCKED,
                reason=ReviewReason.COST_UNKNOWN,
                detail=(
                    "the operating cost gate passed while no attributable cost was "
                    "measured; an unknown cost cannot be reported as known"
                ),
            )
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.TRUST_EVIDENCE,
            status=GateStatus.BLOCKED,
            reason=ReviewReason.COST_UNKNOWN,
            detail=(
                "attributable cost was not measured; an unknown cost cannot yield a "
                "pass"
            ),
        )
    return PrerequisiteFinding(
        prerequisite=ReviewPrerequisite.TRUST_EVIDENCE,
        status=GateStatus.PASSED,
        detail=f"{len(measured)} gate(s) measured with a known cost",
    )


def assess_import_boundary(
    *, violations: Sequence[str] | None
) -> PrerequisiteFinding:
    """The Committee review surface must not reach a job or API authority plane."""

    if violations is None:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.IMPORT_BOUNDARY,
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            reason=ReviewReason.IMPORT_BOUNDARY_UNMEASURED,
            detail="the import boundary was not measured",
        )
    found = tuple(sorted(str(entry) for entry in violations))
    if found:
        return PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.IMPORT_BOUNDARY,
            status=GateStatus.BLOCKED,
            reason=ReviewReason.FORBIDDEN_IMPORT_PRESENT,
            detail=f"forbidden import(s): {', '.join(found)}",
        )
    return PrerequisiteFinding(
        prerequisite=ReviewPrerequisite.IMPORT_BOUNDARY,
        status=GateStatus.PASSED,
        detail="no forbidden import reached the review surface",
    )


# ---------------------------------------------------------------------------
# The composed readiness assessment
# ---------------------------------------------------------------------------


def assess_shadow_review_readiness(
    *,
    assessed_at: datetime,
    core_release_sha: str,
    committee_release_sha: str,
    predictions: Iterable[SealedPrediction],
    investment: CommitteeInvestment,
    ineligible: Iterable[ProspectiveIneligibility] = (),
    timer_decision: TimerPolicyDecision | None = None,
    weakness_registry: WeaknessRegistry | None = None,
    durable_weakness_stream: str | None = None,
    gates: Sequence[GateResult] = (),
    import_violations: Sequence[str] | None = None,
    minimum_support: int = MINIMUM_MATURED_CASES,
) -> ShadowReviewReadiness:
    """Assess every prerequisite the roadmap names before an influence review.

    Each prerequisite is measured from the package's own records: release
    identity through ``prospective.assert_release_matches``, case population
    through the sealed predictions and their own committed horizons, trust
    evidence through the gate results and the investment the report carries,
    findings through the registry and the stream that declares them, and the
    import boundary from the modules themselves.

    The result can only report. It cannot enable the timer, grant influence or
    change a risk or execution gate, and a ``PASSED`` verdict means the package
    is ready to be reviewed rather than authorized to act.
    """

    core = str(core_release_sha or "").strip()
    committee = str(committee_release_sha or "").strip()
    if not core:
        raise ShadowReviewError("core_release_sha is required")
    if not committee:
        raise ShadowReviewError("committee_release_sha is required")
    sealed = tuple(predictions)
    population = measure_case_population(
        predictions=sealed,
        assessed_at=assessed_at,
        ineligible=ineligible,
    )
    release = assess_release_identity(
        predictions=sealed, core_release_sha=core
    )
    if release.satisfied and committee != core:
        # The roadmap's first requirement is the plain equality of the two
        # identities: the Committee under review must be the release under test.
        release = PrerequisiteFinding(
            prerequisite=ReviewPrerequisite.RELEASE_IDENTITY,
            status=GateStatus.BLOCKED,
            reason=ReviewReason.RELEASE_DRIFT,
            detail=(
                f"the Committee release under review ({committee}) is not the core "
                f"release under test ({core})"
            ),
        )
    findings = (
        release,
        assess_timer_policy(timer_decision),
        assess_prospective_cases(
            population=population, minimum_support=minimum_support
        ),
        assess_weakness_finding_durability(
            registry=weakness_registry, durable_stream=durable_weakness_stream
        ),
        assess_trust_evidence(gates=gates, investment=investment),
        assess_import_boundary(violations=import_violations),
    )
    return ShadowReviewReadiness(
        assessed_at=assessed_at,
        core_release_sha=core,
        committee_release_sha=committee,
        findings=findings,
        case_population=population,
        timer_policy=None if timer_decision is None else timer_decision.policy,
    )


__all__ = [
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
]
