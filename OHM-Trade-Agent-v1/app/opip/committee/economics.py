"""Matched portfolio economics and incremental value (IC-021 to IC-028).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The committee's contribution is the *difference* between what a committee-informed
research policy would have produced and what the frozen deterministic baseline
actually produces, measured on the same opportunities under the same conditions.
Two disciplines make that difference meaningful rather than flattering:

**Matching is a precondition, not a caveat.** Two arms may only be subtracted when
they ran on the same eligible population, capital, window, execution model, fees,
slippage, liquidity assumption, coverage, and capital-occupancy semantics. A
comparison across mismatched conditions is refused, because the difference would
then measure the conditions rather than the committee.

**Nothing may be quietly excluded.** No-fills, baseline rejects, failures, late
opinions, missing committee calls, and expensive retries are all part of the
population. An arm that reports only its successful calls is refused, and the
absent population must be declared with counts. Survivorship is not a rounding
detail; it is usually the whole result.

**Unknown is never zero.** If any attributable operating cost is unknown, the
incremental operating net is unknown rather than a partial total presented as
complete. A counterfactual is labelled simulated and never presented as realised
cash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from app.opip.decision_intelligence.serialization import require_utc, stable_hash

MATCHED_CONDITIONS_SCHEMA_VERSION = 1
ARM_OUTCOME_SCHEMA_VERSION = 1
INCREMENTAL_ECONOMICS_SCHEMA_VERSION = 1
COUNTERFACTUAL_SCHEMA_VERSION = 1

MATCHED_CONDITIONS_IDENTITY_DOMAIN = "COMMITTEE-MATCHED-CONDITIONS"
ARM_OUTCOME_IDENTITY_DOMAIN = "COMMITTEE-ARM-OUTCOME"
INCREMENTAL_ECONOMICS_IDENTITY_DOMAIN = "COMMITTEE-INCREMENTAL-ECONOMICS"
COUNTERFACTUAL_IDENTITY_DOMAIN = "COMMITTEE-COUNTERFACTUAL"

#: The disposition every counterfactual carries. A simulated counterfactual is
#: research evidence about a policy, never realised cash P&L.
SIMULATED_COUNTERFACTUAL = "SIMULATED_NOT_REALISED_CASH"


class EconomicsError(ValueError):
    """An economic comparison contract was violated."""


class PortfolioArm(str, Enum):
    """The arms a matched comparison must contain."""

    DETERMINISTIC_BASELINE = "DETERMINISTIC_BASELINE"
    CASH_NO_TRADE = "CASH_NO_TRADE"
    COMMITTEE_RESEARCH_POLICY = "COMMITTEE_RESEARCH_POLICY"


class PopulationKind(str, Enum):
    """Populations that must be accounted for rather than omitted.

    An arm is required to state the count for every kind, including the ones it
    would rather not mention, so an exclusion cannot be silent.
    """

    FILLED = "FILLED"
    NO_FILL = "NO_FILL"
    BASELINE_REJECT = "BASELINE_REJECT"
    FAILED = "FAILED"
    LATE = "LATE"
    MISSING_COMMITTEE_CALL = "MISSING_COMMITTEE_CALL"
    RETRY = "RETRY"


@dataclass(frozen=True)
class MatchedConditions:
    """The conditions two arms must share before their nets may be subtracted."""

    population_id: str
    capital_microunits: int
    window_start: datetime
    window_end: datetime
    execution_model: str
    fee_model: str
    slippage_model: str
    liquidity_assumption: str
    coverage: str
    capital_occupancy_semantics: str
    schema_version: int = MATCHED_CONDITIONS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MATCHED_CONDITIONS_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported MatchedConditions schema_version")
        for field_name in (
            "population_id",
            "execution_model",
            "fee_model",
            "slippage_model",
            "liquidity_assumption",
            "coverage",
            "capital_occupancy_semantics",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise EconomicsError(f"{field_name} is required")
        if type(self.capital_microunits) is not int or self.capital_microunits < 0:
            raise EconomicsError("capital_microunits must be a non-negative integer")
        object.__setattr__(
            self,
            "window_start",
            require_utc(self.window_start, field_name="window_start"),
        )
        object.__setattr__(
            self,
            "window_end",
            require_utc(self.window_end, field_name="window_end"),
        )
        if self.window_end <= self.window_start:
            raise EconomicsError("window_end must be after window_start")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "population_id": self.population_id,
            "capital_microunits": self.capital_microunits,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "execution_model": self.execution_model,
            "fee_model": self.fee_model,
            "slippage_model": self.slippage_model,
            "liquidity_assumption": self.liquidity_assumption,
            "coverage": self.coverage,
            "capital_occupancy_semantics": self.capital_occupancy_semantics,
        }

    @property
    def conditions_hash(self) -> str:
        return stable_hash(MATCHED_CONDITIONS_IDENTITY_DOMAIN, self.identity_payload())

    def require_match(self, other: "MatchedConditions") -> None:
        """Fail closed unless two arms ran under identical conditions."""
        if other.conditions_hash != self.conditions_hash:
            differing = sorted(
                key
                for key, value in self.identity_payload().items()
                if other.identity_payload().get(key) != value
            )
            raise EconomicsError(
                "arms may only be compared under identical conditions; differing "
                f"fields: {differing}"
            )


@dataclass(frozen=True)
class ArmOutcome:
    """One arm's measured result, with its full population accounting.

    ``net_microunits`` is ``None`` when the arm's net could not be fully measured.
    It is never zero-by-default: an unmeasured net must not look like break-even.
    """

    arm: PortfolioArm
    conditions_hash: str
    net_microunits: int | None
    population: Mapping[PopulationKind, int]
    net_completeness: str
    schema_version: int = ARM_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ARM_OUTCOME_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported ArmOutcome schema_version")
        if not isinstance(self.arm, PortfolioArm):
            raise EconomicsError("invalid arm")
        if not isinstance(self.conditions_hash, str) or not self.conditions_hash.strip():
            raise EconomicsError("conditions_hash is required")
        if self.net_microunits is not None and type(self.net_microunits) is not int:
            raise EconomicsError("net_microunits must be an integer or null")
        if not isinstance(self.net_completeness, str) or not self.net_completeness.strip():
            raise EconomicsError(
                "net_completeness is required so an incomplete net is visible"
            )
        missing = [
            kind.value
            for kind in PopulationKind
            if kind not in self.population
        ]
        if missing:
            # An omitted population is an unreported exclusion, which is how
            # survivorship bias enters an evaluation.
            raise EconomicsError(
                f"arm {self.arm.value} does not account for every population; "
                f"missing {missing}"
            )
        for kind, count in self.population.items():
            if type(count) is not int or count < 0:
                raise EconomicsError(
                    f"population count for {kind.value} must be a non-negative integer"
                )

    def count(self, kind: PopulationKind) -> int:
        return self.population[kind]

    @property
    def total_population(self) -> int:
        return sum(self.population.values())

    @property
    def filled(self) -> int:
        return self.population[PopulationKind.FILLED]

    @property
    def is_complete(self) -> bool:
        return self.net_microunits is not None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "arm": self.arm,
            "conditions_hash": self.conditions_hash,
            "net_microunits": self.net_microunits,
            "population": tuple(
                sorted((kind.value, count) for kind, count in self.population.items())
            ),
            "net_completeness": self.net_completeness,
        }

    @property
    def arm_outcome_id(self) -> str:
        return stable_hash(ARM_OUTCOME_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class FourWayComparison:
    """Committee versus deterministic baseline, judged from evidence only.

    ``false_intervention`` counts the cases where the baseline was right and the
    committee-derived policy would have hurt. It is tracked separately because a
    net-positive total can still hide a harmful intervention pattern.
    """

    committee_right_baseline_wrong: int
    baseline_right_committee_wrong: int
    both_right: int
    both_wrong: int

    def __post_init__(self) -> None:
        for field_name in (
            "committee_right_baseline_wrong",
            "baseline_right_committee_wrong",
            "both_right",
            "both_wrong",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise EconomicsError(f"{field_name} must be a non-negative integer")

    @property
    def total_scored(self) -> int:
        return (
            self.committee_right_baseline_wrong
            + self.baseline_right_committee_wrong
            + self.both_right
            + self.both_wrong
        )

    @property
    def false_intervention(self) -> int:
        return self.baseline_right_committee_wrong

    @property
    def false_intervention_rate(self) -> float | None:
        """Proportion of scored cases where the committee would have hurt.

        ``None`` when nothing was scored: an undefined rate is not zero.
        """
        if self.total_scored == 0:
            return None
        return self.false_intervention / self.total_scored


@dataclass(frozen=True)
class IncrementalEconomics:
    """The two headline metrics, with their completeness stated explicitly."""

    matched_conditions: MatchedConditions
    baseline_net_microunits: int | None
    committee_net_microunits: int | None
    operating_cost_microunits: int | None
    four_way: FourWayComparison
    cash_no_trade_net_microunits: int | None = None
    schema_version: int = INCREMENTAL_ECONOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != INCREMENTAL_ECONOMICS_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported IncrementalEconomics schema_version")
        if not isinstance(self.matched_conditions, MatchedConditions):
            raise EconomicsError("matched_conditions is required")
        if not isinstance(self.four_way, FourWayComparison):
            raise EconomicsError("four_way comparison is required")

    @property
    def incremental_trading_net(self) -> int | None:
        """Committee research-policy net minus the frozen baseline net.

        ``None`` unless both nets are known: comparing against an unknown baseline
        would fabricate the comparison.
        """
        if self.baseline_net_microunits is None or self.committee_net_microunits is None:
            return None
        return self.committee_net_microunits - self.baseline_net_microunits

    @property
    def incremental_operating_net(self) -> int | None:
        """Incremental trading net minus attributable operating cost.

        ``None`` when any input is unknown. An unknown cost is never treated as
        free, because doing so would make the committee look better the less was
        measured about it.
        """
        trading = self.incremental_trading_net
        if trading is None or self.operating_cost_microunits is None:
            return None
        return trading - self.operating_cost_microunits

    @property
    def has_negative_operating_value(self) -> bool | None:
        """Whether gross value is positive but operating value is not.

        ``None`` when either metric is unknown, so an unmeasured case is not
        reported as healthy.
        """
        trading = self.incremental_trading_net
        operating = self.incremental_operating_net
        if trading is None or operating is None:
            return None
        return trading > 0 and operating <= 0

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "conditions": self.matched_conditions.conditions_hash,
            "baseline_net_microunits": self.baseline_net_microunits,
            "committee_net_microunits": self.committee_net_microunits,
            "operating_cost_microunits": self.operating_cost_microunits,
            "cash_no_trade_net_microunits": self.cash_no_trade_net_microunits,
            "incremental_trading_net": self.incremental_trading_net,
            "incremental_operating_net": self.incremental_operating_net,
            "four_way": {
                "committee_right_baseline_wrong": (
                    self.four_way.committee_right_baseline_wrong
                ),
                "baseline_right_committee_wrong": (
                    self.four_way.baseline_right_committee_wrong
                ),
                "both_right": self.four_way.both_right,
                "both_wrong": self.four_way.both_wrong,
            },
        }

    @property
    def incremental_id(self) -> str:
        return stable_hash(
            INCREMENTAL_ECONOMICS_IDENTITY_DOMAIN, self.identity_payload()
        )


@dataclass(frozen=True)
class CounterfactualClaim:
    """An avoided-loss or missed-gain claim.

    Permitted only under a preregistered policy and feasible timing, and always
    labelled simulated. A counterfactual is a statement about a policy, not about
    cash that was received.
    """

    claim_kind: str
    policy_ref: str
    amount_microunits: int
    feasible_timing: bool
    disposition: str = SIMULATED_COUNTERFACTUAL
    schema_version: int = COUNTERFACTUAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != COUNTERFACTUAL_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported CounterfactualClaim schema_version")
        for field_name in ("claim_kind", "policy_ref", "disposition"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise EconomicsError(f"{field_name} is required")
        if type(self.amount_microunits) is not int:
            raise EconomicsError("amount_microunits must be an integer")
        if type(self.feasible_timing) is not bool:
            raise EconomicsError("feasible_timing must be a boolean")
        if not self.feasible_timing:
            raise EconomicsError(
                "an avoided-loss or missed-gain claim requires feasible timing; "
                "an opinion that arrived after the opportunity expired cannot be "
                "credited with avoiding it"
            )
        if self.disposition != SIMULATED_COUNTERFACTUAL:
            raise EconomicsError(
                "a counterfactual must be labelled simulated; it is not realised cash"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_kind": self.claim_kind,
            "policy_ref": self.policy_ref,
            "amount_microunits": self.amount_microunits,
            "feasible_timing": self.feasible_timing,
            "disposition": self.disposition,
        }

    @property
    def claim_id(self) -> str:
        return stable_hash(COUNTERFACTUAL_IDENTITY_DOMAIN, self.identity_payload())


def build_incremental_economics(
    *,
    matched_conditions: MatchedConditions,
    baseline: ArmOutcome,
    committee: ArmOutcome,
    operating_cost_microunits: int | None,
    four_way: FourWayComparison,
    cash_no_trade: ArmOutcome | None = None,
) -> IncrementalEconomics:
    """Compute incremental value from matched arms.

    Every arm must declare the same conditions, and each must account for the full
    population. An unfinished arm reports an unknown net rather than a partial one.
    """
    for arm in (baseline, committee):
        if not isinstance(arm, ArmOutcome):
            raise EconomicsError("arms must be ArmOutcome values")
        if arm.conditions_hash != matched_conditions.conditions_hash:
            raise EconomicsError(
                f"arm {arm.arm.value} did not run under the stated conditions"
            )
    if baseline.arm is not PortfolioArm.DETERMINISTIC_BASELINE:
        raise EconomicsError("the baseline arm must be DETERMINISTIC_BASELINE")
    if committee.arm is not PortfolioArm.COMMITTEE_RESEARCH_POLICY:
        raise EconomicsError(
            "the committee arm must be COMMITTEE_RESEARCH_POLICY"
        )
    if cash_no_trade is not None:
        if cash_no_trade.arm is not PortfolioArm.CASH_NO_TRADE:
            raise EconomicsError("the comparator arm must be CASH_NO_TRADE")
        if cash_no_trade.conditions_hash != matched_conditions.conditions_hash:
            raise EconomicsError("the cash comparator did not run under the conditions")
    if operating_cost_microunits is not None and (
        type(operating_cost_microunits) is not int
    ):
        raise EconomicsError("operating_cost_microunits must be an integer or null")
    return IncrementalEconomics(
        matched_conditions=matched_conditions,
        baseline_net_microunits=baseline.net_microunits,
        committee_net_microunits=committee.net_microunits,
        operating_cost_microunits=operating_cost_microunits,
        four_way=four_way,
        cash_no_trade_net_microunits=(
            None if cash_no_trade is None else cash_no_trade.net_microunits
        ),
    )


def assert_no_survivorship(*, arm: ArmOutcome, excluded_kinds: Sequence[PopulationKind]) -> None:
    """Require every excluded population to be declared with a count.

    An evaluation that drops its failures is not an evaluation. This does not
    forbid exclusion; it forbids *unreported* exclusion.
    """
    if not excluded_kinds:
        return
    undeclared = [
        kind.value for kind in excluded_kinds if kind not in arm.population
    ]
    if undeclared:
        raise EconomicsError(
            f"arm {arm.arm.value} excludes {undeclared} without accounting for them"
        )


__all__ = [
    "ARM_OUTCOME_IDENTITY_DOMAIN",
    "ARM_OUTCOME_SCHEMA_VERSION",
    "COUNTERFACTUAL_IDENTITY_DOMAIN",
    "COUNTERFACTUAL_SCHEMA_VERSION",
    "INCREMENTAL_ECONOMICS_IDENTITY_DOMAIN",
    "INCREMENTAL_ECONOMICS_SCHEMA_VERSION",
    "MATCHED_CONDITIONS_IDENTITY_DOMAIN",
    "MATCHED_CONDITIONS_SCHEMA_VERSION",
    "SIMULATED_COUNTERFACTUAL",
    "ArmOutcome",
    "CounterfactualClaim",
    "EconomicsError",
    "FourWayComparison",
    "IncrementalEconomics",
    "MatchedConditions",
    "PopulationKind",
    "PortfolioArm",
    "assert_no_survivorship",
    "build_incremental_economics",
]
