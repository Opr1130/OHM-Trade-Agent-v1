"""Committee Trust Report and investment observability (IC-043, IC-044).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The Trust Report is the durable, authoritative summary of what the committee has
actually demonstrated. It exists so trust is *read from evidence* rather than
asserted, and so a UI displays authoritative facts instead of computing trust
itself. Three rules make it honest:

* **Every number is derived from records, never restated.** Population counts come
  from the scheduler's tally, economics from matched arms, weakness counts from the
  registry. The report cannot disagree with its sources because it does not
  duplicate them.

* **Unknown stays unknown.** A metric that has no evidence is ``None`` with a
  reason, never zero. An empty report must not read as a clean one.

* **Trust stage follows gates, not outcomes.** The stage is computed from
  integrity, coverage, and evidence sufficiency. A high gross number with unknown
  operating cost cannot advance a stage, because a stage that could be reached by
  an unmeasured result would be meaningless.

The report also records what the committee cost, so its value can be judged
against its investment rather than in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from app.opip.committee.economics import (
    FourWayComparison,
    IncrementalEconomics,
)
from app.opip.committee.scheduler import (
    CommitteeScheduleDisposition,
    PopulationTally,
)
from app.opip.committee.weakness import (
    WeaknessRegistry,
    WeaknessValidationState,
)
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

TRUST_REPORT_SCHEMA_VERSION = 1
TRUST_REPORT_IDENTITY_DOMAIN = "COMMITTEE-TRUST-REPORT"

#: Reported when a metric has no supporting evidence. Never zero.
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

#: Reported when a metric could not be measured for a stated reason.
NOT_MEASURED = "NOT_MEASURED"


class TrustStage(str, Enum):
    """How far the committee's evidence has taken it.

    Each stage is an evidence claim, not an authority grant. Even the highest stage
    here confers no trading influence: that requires a separate human decision
    outside this module.
    """

    T0_UNTRUSTED_SHADOW = "T0_UNTRUSTED_SHADOW"
    T1_MEASURED = "T1_MEASURED"
    T2_SIGNAL_ELIGIBLE_REVIEW = "T2_SIGNAL_ELIGIBLE_REVIEW"
    T3_PAPER_INFLUENCE_ELIGIBLE_REVIEW = "T3_PAPER_INFLUENCE_ELIGIBLE_REVIEW"
    T4_LIVE_ELIGIBILITY_REVIEW = "T4_LIVE_ELIGIBILITY_REVIEW"


class GateName(str, Enum):
    """The gates a stage advance depends on."""

    EVIDENCE_INTEGRITY = "EVIDENCE_INTEGRITY"
    COVERAGE = "COVERAGE"
    BASELINE_COMPARISON = "BASELINE_COMPARISON"
    ECONOMICS = "ECONOMICS"
    OPERATING_COST_KNOWN = "OPERATING_COST_KNOWN"
    WEAKNESS_PRECISION = "WEAKNESS_PRECISION"


class GateStatus(str, Enum):
    """Whether a gate is satisfied, blocked, or unmeasurable."""

    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class GateResult:
    """One gate's outcome, with the reason it did not pass."""

    gate: GateName
    status: GateStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.gate, GateName):
            raise ValueError("invalid gate")
        if not isinstance(self.status, GateStatus):
            raise ValueError("invalid gate status")
        if self.status is not GateStatus.PASSED and not (
            self.reason or ""
        ).strip():
            # A blocked gate with no reason is unauditable.
            raise ValueError(f"gate {self.gate.value} must state why it did not pass")


@dataclass(frozen=True)
class CommitteeInvestment:
    """What the committee has cost and consumed, for value-against-investment.

    Unknown values stay ``None``: an unmeasured cost must not read as free, and an
    unmeasured token total must not read as zero.
    """

    attributable_cost_microunits: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_calls: int = 0
    failed_calls: int = 0
    measured_latencies_micros: tuple[int, ...] = ()
    unmeasured_attempts: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "attributable_cost_microunits",
            "input_tokens",
            "output_tokens",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer or null")
        for field_name in ("provider_calls", "failed_calls", "unmeasured_attempts"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.failed_calls > self.provider_calls:
            raise ValueError("failed_calls cannot exceed provider_calls")
        for latency in self.measured_latencies_micros:
            if type(latency) is not int or latency < 0:
                raise ValueError(
                    "measured latencies must be non-negative integers; an "
                    "unmeasured attempt must not be represented as a zero latency"
                )

    @property
    def total_latency_micros(self) -> int | None:
        """Summed measured latency, or ``None`` when nothing was measured.

        Aggregated only from measured observations, never zero-by-default.
        """
        if not self.measured_latencies_micros:
            return None
        return sum(self.measured_latencies_micros)

    @property
    def mean_latency_micros(self) -> float | None:
        """Mean over measured attempts only, or ``None`` when none were measured."""
        if not self.measured_latencies_micros:
            return None
        return sum(self.measured_latencies_micros) / len(self.measured_latencies_micros)

    @property
    def latency_sample_complete(self) -> bool:
        """Whether every attempt was measured.

        A partial latency sample is reported as partial rather than presented as
        the whole picture.
        """
        return self.unmeasured_attempts == 0

    @property
    def failure_rate(self) -> float | None:
        """Proportion of calls that failed, or ``None`` when nothing was called."""
        if self.provider_calls == 0:
            return None
        return self.failed_calls / self.provider_calls

    @property
    def cost_is_known(self) -> bool:
        return self.attributable_cost_microunits is not None


@dataclass(frozen=True)
class CommitteeTrustReport:
    """The durable authoritative trust summary for one evaluation cycle."""

    report_version: str
    release_sha: str
    registry_version: str
    generated_at: datetime
    population: PopulationTally
    economics: IncrementalEconomics | None
    four_way: FourWayComparison | None
    investment: CommitteeInvestment
    gates: tuple[GateResult, ...]
    stage: TrustStage
    weakness_counts: Mapping[str, int] = field(default_factory=dict)
    recurring_weakness_count: int = 0
    resolved_weakness_count: int = 0
    insufficiency_reasons: tuple[str, ...] = ()
    schema_version: int = TRUST_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TRUST_REPORT_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported CommitteeTrustReport schema_version")
        for field_name in ("report_version", "release_sha", "registry_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.population, PopulationTally):
            raise ValueError("population must be a PopulationTally")
        if not isinstance(self.investment, CommitteeInvestment):
            raise ValueError("investment must be a CommitteeInvestment")
        if not isinstance(self.stage, TrustStage):
            raise ValueError("invalid trust stage")
        for gate in self.gates:
            if not isinstance(gate, GateResult):
                raise ValueError("gates must be GateResult values")
        if self.economics is not None and not isinstance(
            self.economics, IncrementalEconomics
        ):
            raise ValueError("economics must be an IncrementalEconomics or null")
        if type(self.recurring_weakness_count) is not int or (
            self.recurring_weakness_count < 0
        ):
            raise ValueError("recurring_weakness_count must be a non-negative integer")
        if type(self.resolved_weakness_count) is not int or (
            self.resolved_weakness_count < 0
        ):
            raise ValueError("resolved_weakness_count must be a non-negative integer")
        object.__setattr__(
            self,
            "generated_at",
            require_utc(self.generated_at, field_name="generated_at"),
        )

    # ---------------------------------------------------------- derived views

    @property
    def incremental_trading_net(self) -> int | None:
        """Never zero by default: unknown when there is no matched comparison."""
        if self.economics is None:
            return None
        return self.economics.incremental_trading_net

    @property
    def incremental_operating_net(self) -> int | None:
        if self.economics is None:
            return None
        return self.economics.incremental_operating_net

    @property
    def false_intervention_rate(self) -> float | None:
        if self.four_way is None:
            return None
        return self.four_way.false_intervention_rate

    @property
    def maturity_progress(self) -> Mapping[str, int]:
        """Prospective counts, with only matured cases scorable."""
        return {
            "sealed": self.population.count(CommitteeScheduleDisposition.SELECTED),
            "completed": self.population.count(
                CommitteeScheduleDisposition.COMPLETED
            ),
            "late": self.population.count(CommitteeScheduleDisposition.LATE),
            "unavailable": self.population.count(
                CommitteeScheduleDisposition.UNAVAILABLE
            ),
            "failed": self.population.count(CommitteeScheduleDisposition.FAILED),
            "latency_measured": len(self.investment.measured_latencies_micros),
            "latency_unmeasured": self.investment.unmeasured_attempts,
        }

    @property
    def mean_latency_micros(self) -> float | None:
        """Role/model observability latency, or ``None`` when nothing was measured."""
        return self.investment.mean_latency_micros

    @property
    def blocked_gates(self) -> tuple[GateName, ...]:
        return tuple(
            gate.gate for gate in self.gates if gate.status is not GateStatus.PASSED
        )

    @property
    def has_unknown_key_metric(self) -> bool:
        """Whether a headline metric is unknown.

        Used by the stage computation: a stage must not advance on unknown inputs.
        """
        return (
            self.incremental_trading_net is None
            or self.incremental_operating_net is None
            or self.four_way is None
        )

    def gate(self, name: GateName) -> GateResult | None:
        for entry in self.gates:
            if entry.gate is name:
                return entry
        return None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_version": self.report_version,
            "release_sha": self.release_sha,
            "registry_version": self.registry_version,
            "generated_at": self.generated_at,
            "stage": self.stage,
            "population": self.population.as_dict(),
            "incremental_trading_net": self.incremental_trading_net,
            "incremental_operating_net": self.incremental_operating_net,
            "gates": tuple(
                (gate.gate.value, gate.status.value, gate.reason)
                for gate in self.gates
            ),
        }

    @property
    def report_id(self) -> str:
        return stable_hash(TRUST_REPORT_IDENTITY_DOMAIN, self.identity_payload())


#: The minimum prospective evidence a stage above measurement requires. A
#: convention, not an inferential guarantee: it prevents anecdotal claims.
MINIMUM_MATURED_CASES = 30


def evaluate_gates(
    *,
    population: PopulationTally,
    economics: IncrementalEconomics | None,
    investment: CommitteeInvestment,
    weakness_counts: Mapping[str, int],
    integrity_violations: int,
) -> tuple[GateResult, ...]:
    """Compute each gate from evidence, stating why a gate did not pass."""
    gates: list[GateResult] = []

    gates.append(
        GateResult(
            gate=GateName.EVIDENCE_INTEGRITY,
            status=(
                GateStatus.PASSED
                if integrity_violations == 0
                else GateStatus.BLOCKED
            ),
            reason=(
                None
                if integrity_violations == 0
                else f"{integrity_violations} evidence integrity violations recorded"
            ),
        )
    )

    matured = population.count(CommitteeScheduleDisposition.COMPLETED)
    gates.append(
        GateResult(
            gate=GateName.COVERAGE,
            status=(
                GateStatus.PASSED
                if matured >= MINIMUM_MATURED_CASES
                else GateStatus.INSUFFICIENT_EVIDENCE
            ),
            reason=(
                None
                if matured >= MINIMUM_MATURED_CASES
                else f"{matured} completed cases, minimum {MINIMUM_MATURED_CASES}"
            ),
        )
    )

    if economics is None:
        gates.append(
            GateResult(
                gate=GateName.BASELINE_COMPARISON,
                status=GateStatus.INSUFFICIENT_EVIDENCE,
                reason="no matched baseline comparison has been produced",
            )
        )
        gates.append(
            GateResult(
                gate=GateName.ECONOMICS,
                status=GateStatus.INSUFFICIENT_EVIDENCE,
                reason="no matched baseline comparison has been produced",
            )
        )
    else:
        four_way = economics.four_way
        gates.append(
            GateResult(
                gate=GateName.BASELINE_COMPARISON,
                status=(
                    GateStatus.PASSED
                    if four_way.total_scored > 0
                    else GateStatus.INSUFFICIENT_EVIDENCE
                ),
                reason=(
                    None
                    if four_way.total_scored > 0
                    else "the comparison scored no cases"
                ),
            )
        )
        trading = economics.incremental_trading_net
        gates.append(
            GateResult(
                gate=GateName.ECONOMICS,
                status=(
                    GateStatus.INSUFFICIENT_EVIDENCE
                    if trading is None
                    else GateStatus.PASSED
                ),
                reason=(
                    "the incremental trading net is unknown, so net value is unproven"
                    if trading is None
                    else None
                ),
            )
        )

    gates.append(
        GateResult(
            gate=GateName.OPERATING_COST_KNOWN,
            status=(
                GateStatus.PASSED
                if investment.cost_is_known
                else GateStatus.INSUFFICIENT_EVIDENCE
            ),
            reason=(
                None
                if investment.cost_is_known
                else "the attributable operating cost is unknown"
            ),
        )
    )

    validated = weakness_counts.get(WeaknessValidationState.VALIDATED.value, 0)
    rejected = weakness_counts.get(WeaknessValidationState.REJECTED.value, 0)
    gates.append(
        GateResult(
            gate=GateName.WEAKNESS_PRECISION,
            status=(
                GateStatus.PASSED
                if validated > 0
                else GateStatus.INSUFFICIENT_EVIDENCE
            ),
            reason=(
                None
                if validated > 0
                else (
                    "no finding has been validated against an outcome"
                    + (f" ({rejected} were rejected)" if rejected else "")
                )
            ),
        )
    )
    return tuple(gates)


def compute_trust_stage(
    *,
    gates: tuple[GateResult, ...],
    population: PopulationTally,
    economics: IncrementalEconomics | None,
) -> TrustStage:
    """Derive the stage from gates and evidence only.

    Deliberately conservative: a stage requires the gates beneath it to pass, so a
    stage cannot be reached by an unmeasured or unintegrated result.
    """
    by_name = {gate.gate: gate for gate in gates}

    def passed(name: GateName) -> bool:
        gate = by_name.get(name)
        return gate is not None and gate.status is GateStatus.PASSED

    matured = population.count(CommitteeScheduleDisposition.COMPLETED)
    if matured == 0 or not passed(GateName.EVIDENCE_INTEGRITY):
        return TrustStage.T0_UNTRUSTED_SHADOW
    if not passed(GateName.COVERAGE):
        return TrustStage.T1_MEASURED
    ready = (
        passed(GateName.BASELINE_COMPARISON)
        and passed(GateName.ECONOMICS)
        and passed(GateName.OPERATING_COST_KNOWN)
        and passed(GateName.WEAKNESS_PRECISION)
    )
    if not ready:
        return TrustStage.T1_MEASURED
    operating = None if economics is None else economics.incremental_operating_net
    if operating is None or operating <= 0:
        # Positive net value is required before a review stage is even proposed.
        return TrustStage.T2_SIGNAL_ELIGIBLE_REVIEW
    return TrustStage.T3_PAPER_INFLUENCE_ELIGIBLE_REVIEW


def build_trust_report(
    *,
    report_version: str,
    release_sha: str,
    registry_version: str,
    generated_at: datetime,
    population: PopulationTally,
    investment: CommitteeInvestment,
    weakness_registry: WeaknessRegistry | None = None,
    economics: IncrementalEconomics | None = None,
    integrity_violations: int = 0,
) -> CommitteeTrustReport:
    """Assemble the trust report from the records that prove it.

    Counts come from the sources rather than being restated, so the report cannot
    disagree with them.
    """
    counts: Mapping[str, int] = (
        {} if weakness_registry is None else weakness_registry.summary()
    )
    recurring = (
        0 if weakness_registry is None else len(weakness_registry.recurring_findings())
    )
    resolved = 0 if weakness_registry is None else sum(
        1
        for finding in weakness_registry.findings()
        if weakness_registry.follow_ups_for(finding.finding_id)
    )
    gates = evaluate_gates(
        population=population,
        economics=economics,
        investment=investment,
        weakness_counts=counts,
        integrity_violations=integrity_violations,
    )
    reasons = tuple(
        f"{gate.gate.value}: {gate.reason}"
        for gate in gates
        if gate.status is not GateStatus.PASSED and gate.reason
    )
    return CommitteeTrustReport(
        report_version=report_version,
        release_sha=release_sha,
        registry_version=registry_version,
        generated_at=generated_at,
        population=population,
        economics=economics,
        four_way=None if economics is None else economics.four_way,
        investment=investment,
        gates=gates,
        stage=compute_trust_stage(
            gates=gates, population=population, economics=economics
        ),
        weakness_counts=dict(counts),
        recurring_weakness_count=recurring,
        resolved_weakness_count=resolved,
        insufficiency_reasons=reasons,
    )


__all__ = [
    "INSUFFICIENT_EVIDENCE",
    "MINIMUM_MATURED_CASES",
    "NOT_MEASURED",
    "TRUST_REPORT_IDENTITY_DOMAIN",
    "TRUST_REPORT_SCHEMA_VERSION",
    "CommitteeInvestment",
    "CommitteeTrustReport",
    "GateName",
    "GateResult",
    "GateStatus",
    "TrustStage",
    "build_trust_report",
    "compute_trust_stage",
    "evaluate_gates",
]
