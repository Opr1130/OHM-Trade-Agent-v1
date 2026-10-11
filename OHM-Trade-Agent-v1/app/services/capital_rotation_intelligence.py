from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable


VERSION = "capital-rotation-shadow-v1"
DEFAULT_FULL_UTILIZATION_PCT = 95.0
DEFAULT_EXCEPTIONAL_CONFIDENCE = 92.0
DEFAULT_EXCEPTIONAL_UPSIDE_PCT = 12.0
DEFAULT_MIN_ROTATION_EDGE_PCT = 4.0


@dataclass(frozen=True)
class CapitalUtilization:
    account_capital: float
    deployed_capital: float
    utilization_pct: float
    fully_utilized: bool


@dataclass(frozen=True)
class IncumbentOpportunity:
    symbol: str
    capital: float
    remaining_upside_pct: float | None
    downside_to_stop_pct: float | None
    momentum_state: str | None = None

    @property
    def quality_proxy(self) -> float | None:
        if self.remaining_upside_pct is None:
            return None
        values = (self.remaining_upside_pct, self.downside_to_stop_pct or 0.0)
        if not all(math.isfinite(float(value)) for value in values):
            return None
        downside = max(0.0, float(self.downside_to_stop_pct or 0.0))
        return float(self.remaining_upside_pct) - downside


@dataclass(frozen=True)
class RotationDecision:
    version: str
    status: str
    candidate_symbol: str
    capital: CapitalUtilization
    candidate_confidence_score: float
    confidence_is_probability: bool
    candidate_remaining_upside_pct: float | None
    candidate_downside_pct: float | None
    weakest_incumbent_symbol: str | None
    weakest_incumbent_quality_proxy: float | None
    candidate_quality_proxy: float | None
    estimated_rotation_edge_pct: float | None
    exceptional_gate_passed: bool
    recommendation: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    shadow_only: bool = True
    production_decision_changed: bool = False
    trade_authority_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def capital_utilization(
    *,
    account_capital: float,
    active_trades: Iterable[Any],
    full_utilization_pct: float = DEFAULT_FULL_UTILIZATION_PCT,
) -> CapitalUtilization:
    account_raw = float(account_capital)
    threshold = float(full_utilization_pct)
    if not math.isfinite(account_raw) or not math.isfinite(threshold) or account_raw <= 0 or threshold <= 0:
        return CapitalUtilization(0.0, 0.0, 0.0, True)
    deployed = 0.0
    unknown_exposure = False
    for trade in active_trades:
        capital = getattr(trade, "capital", None)
        if capital is None:
            unknown_exposure = True
            continue
        try:
            numeric = float(capital)
        except (TypeError, ValueError):
            unknown_exposure = True
            continue
        if not math.isfinite(numeric):
            unknown_exposure = True
            continue
        if numeric > 0:
            deployed += numeric
    pct = deployed / account_raw * 100.0
    # Unknown live exposure must never be interpreted as free capital. Rotation
    # is shadow-only, so conservatively treat utilization as full until an
    # authoritative capital value is available.
    fully_utilized = unknown_exposure or pct >= threshold
    return CapitalUtilization(
        account_capital=round(account_raw, 2),
        deployed_capital=round(deployed, 2),
        utilization_pct=round(pct, 2),
        fully_utilized=fully_utilized,
    )


def build_incumbent_opportunity(trade: Any, *, current_price: float | None) -> IncumbentOpportunity:
    direction = str(getattr(trade, "direction", "LONG") or "LONG").upper()
    target = _finite_or_none(getattr(trade, "target_2", None))
    stop = _finite_or_none(getattr(trade, "stop_price", None))
    price = _finite_or_none(current_price)
    remaining: float | None = None
    downside: float | None = None
    if price is not None and price > 0 and target is not None and target > 0:
        remaining = ((price - target) / price * 100.0) if direction == "SHORT" else ((target - price) / price * 100.0)
    if price is not None and price > 0 and stop is not None and stop > 0:
        downside = ((stop - price) / price * 100.0) if direction == "SHORT" else ((price - stop) / price * 100.0)
    capital = _finite_or_none(getattr(trade, "capital", None))
    return IncumbentOpportunity(
        symbol=str(getattr(trade, "symbol", "")).upper(),
        capital=max(0.0, capital) if capital is not None else 0.0,
        remaining_upside_pct=(round(remaining, 4) if remaining is not None and math.isfinite(remaining) else None),
        downside_to_stop_pct=(round(max(0.0, downside), 4) if downside is not None and math.isfinite(downside) else None),
    )


def evaluate_rotation(
    *,
    candidate_symbol: str,
    candidate_confidence_score: float,
    candidate_remaining_upside_pct: float | None,
    candidate_downside_pct: float | None,
    account_capital: float,
    active_trades: Iterable[Any],
    incumbents: Iterable[IncumbentOpportunity],
    full_utilization_pct: float = DEFAULT_FULL_UTILIZATION_PCT,
    exceptional_confidence: float = DEFAULT_EXCEPTIONAL_CONFIDENCE,
    exceptional_upside_pct: float = DEFAULT_EXCEPTIONAL_UPSIDE_PCT,
    min_rotation_edge_pct: float = DEFAULT_MIN_ROTATION_EDGE_PCT,
) -> RotationDecision:
    """Compare a new opportunity with currently deployed capital in shadow only."""
    active = list(active_trades)
    usage = capital_utilization(
        account_capital=account_capital,
        active_trades=active,
        full_utilization_pct=full_utilization_pct,
    )
    reasons: list[str] = []
    warnings: list[str] = [
        "confidence is a heuristic/model score, not a calibrated win probability",
        "remaining-upside inputs are comparative proxies until prospectively calibrated",
    ]

    confidence = _finite_or_none(candidate_confidence_score)
    upside = _finite_or_none(candidate_remaining_upside_pct)
    downside = _finite_or_none(candidate_downside_pct)
    thresholds = (
        _finite_or_none(exceptional_confidence),
        _finite_or_none(exceptional_upside_pct),
        _finite_or_none(min_rotation_edge_pct),
    )
    invalid_candidate = confidence is None or (candidate_remaining_upside_pct is not None and upside is None) or (candidate_downside_pct is not None and downside is None) or any(value is None for value in thresholds)
    if invalid_candidate:
        warnings.append("non-finite candidate or threshold evidence was rejected")

    incumbent_rows = [item for item in incumbents if item.quality_proxy is not None]
    weakest = min(incumbent_rows, key=lambda item: item.quality_proxy) if incumbent_rows else None

    candidate_quality: float | None = None
    if not invalid_candidate and upside is not None:
        candidate_quality = upside - max(0.0, downside or 0.0)

    rotation_edge: float | None = None
    if candidate_quality is not None and weakest is not None and weakest.quality_proxy is not None:
        rotation_edge = candidate_quality - float(weakest.quality_proxy)

    exceptional = bool(
        not invalid_candidate
        and confidence is not None
        and upside is not None
        and confidence >= float(thresholds[0])
        and upside >= float(thresholds[1])
    )

    if invalid_candidate:
        status = "INVALID_CANDIDATE_EVIDENCE"
        recommendation = "SUPPRESS_ROTATION_ALERT"
        reasons.append("candidate rotation evidence is incomplete or non-finite")
    elif not usage.fully_utilized:
        status = "CAPITAL_AVAILABLE"
        recommendation = "NORMAL_ALERT_POLICY"
        reasons.append(f"capital utilization is {usage.utilization_pct:.1f}%; rotation is not required")
    elif not exceptional:
        status = "FULL_CAPITAL_EXCEPTIONAL_GATE_NOT_MET"
        recommendation = "SUPPRESS_ROTATION_ALERT"
        reasons.append(
            f"full-capital exceptional gate requires score >= {float(thresholds[0]):.0f} and upside proxy >= {float(thresholds[1]):.1f}%"
        )
    elif weakest is None or rotation_edge is None:
        status = "INSUFFICIENT_INCUMBENT_EVIDENCE"
        recommendation = "WATCH_ROTATION"
        reasons.append("candidate is exceptional but incumbent remaining-upside evidence is incomplete")
    elif rotation_edge < float(thresholds[2]):
        status = "ROTATION_ADVANTAGE_TOO_SMALL"
        recommendation = "HOLD_CURRENT_CAPITAL"
        reasons.append(f"estimated comparative advantage is only {rotation_edge:+.2f}%")
    else:
        status = "HIGH_CONVICTION_ROTATION_CANDIDATE"
        recommendation = "REVIEW_ROTATION"
        reasons.append("candidate passes exceptional score/upside gate")
        reasons.append(
            f"candidate quality proxy exceeds {weakest.symbol} by approximately {rotation_edge:+.2f}%"
        )

    return RotationDecision(
        version=VERSION,
        status=status,
        candidate_symbol=candidate_symbol.upper(),
        capital=usage,
        candidate_confidence_score=round(confidence, 2) if confidence is not None else 0.0,
        confidence_is_probability=False,
        candidate_remaining_upside_pct=(round(upside, 4) if upside is not None else None),
        candidate_downside_pct=(round(max(0.0, downside), 4) if downside is not None else None),
        weakest_incumbent_symbol=(weakest.symbol if weakest is not None else None),
        weakest_incumbent_quality_proxy=(round(float(weakest.quality_proxy), 4) if weakest is not None and weakest.quality_proxy is not None else None),
        candidate_quality_proxy=(round(candidate_quality, 4) if candidate_quality is not None else None),
        estimated_rotation_edge_pct=(round(rotation_edge, 4) if rotation_edge is not None else None),
        exceptional_gate_passed=exceptional,
        recommendation=recommendation,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


# R5-C Capital Intelligence
# ===========================================================================
# Append-only R5-C section. Everything above this banner is the frozen R5-B
# rotation vocabulary and is untouched by this increment, which only *extends*
# the existing owner (`EXTEND_EXISTING_MODULE`) as the frozen R5-0 contract
# freeze requires.
#
# What this section owns, and nothing else:
#
# * `CapitalSnapshot`, the one canonical capital record of `SC-06`, carrying
#   every frozen record key (account/environment/engine, currency, cash
#   partitions, positions, liabilities if supported, marks, sleeve/risk usage,
#   fee state, source watermark, reconciliation, freshness).
# * the exact accounting laws of adopted clauses `CI-01`-`CI-03`: the cash
#   partitions are disjoint and reconstruct the declared cash total, deployed
#   basis and declared marked value are reported separately from cash, equity is
#   cash plus declared marked position value, liabilities and receivables are
#   declared unsupported rather than invented, a recompute declares *why* it
#   happened, and a snapshot cannot outlive its declared validity limit.
# * the atomic compare-and-commit reservation path of `CI-04`: a plan is applied
#   against the portfolio version it was built for, exactly once, all-or-nothing,
#   with the committed capital recorded as a partition move and the risk and
#   sleeve usage declared by the caller, never invented. Concurrent change fails
#   closed instead of over-committing, and a replay consumes nothing twice.
# * the capital-time diagnostics of v1.5.0 section 12: capital occupancy is the
#   time integral of the declared allocated basis over preserved instants and an
#   explicitly declared endpoint policy, capital-time efficiency is realized
#   trading net divided by its capital-hours, and zero or unknown occupancy is
#   `UNAVAILABLE` instead of a fabricated number.
#
# Deliberate boundaries, enforced here and proved by the acceptance suite:
#
# * cash is partitioned, never scalar, and there is no second cash book: the
#   module reuses the frozen portfolio owner's `PortfolioCapitalState` and
#   `assert_reservation_plan_current` instead of forking its own capital family.
# * every money field is an exact `Decimal` on the declared `MONEY_QUANTUM`
#   grid. A float, a boolean, an integer, a non-finite value or a value carrying
#   more precision than the quantum is refused instead of rounded, defaulted or
#   coerced.
# * an external deposit, a withdrawal or a liability movement is not a
#   describable recompute trigger, because `CI-03` lists deposits/withdrawals
#   only "if supported" and this engine declares no such support.
# * the deployable-capital policy ships no unratified number: `OQ-06` (sleeve
#   caps and hard versus soft constraint behaviour) is an owner input, so a
#   missing cap abstains visibly instead of being read as "unlimited".
# * research-only evidence: `authoritative` is hard-coded `False`, nothing here
#   places, modifies or cancels any paper or funded order, no exchange secret is
#   handled, no store or writer is invoked, no environment variable is read, no
#   wall clock is consulted and nothing is wired into a runtime root.

import re  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from collections.abc import Mapping  # noqa: E402
from dataclasses import field, replace  # noqa: E402
from datetime import datetime  # noqa: E402
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation  # noqa: E402
from enum import Enum  # noqa: E402
from types import MappingProxyType  # noqa: E402

from app.opip.contracts.portfolio import (  # noqa: E402
    PortfolioCapitalState,
    PortfolioContractError,
    PortfolioReservationPlan,
    assert_reservation_plan_current,
)
from app.opip.contracts.serialization import iso_z, stable_hash  # noqa: E402
from app.opip.contracts.temporal import require_utc  # noqa: E402

#: Schema version of the durable `CapitalSnapshot` record (`SC-06`).
CAPITAL_SNAPSHOT_SCHEMA_VERSION = "capital-snapshot-v1"

#: Schema version of the reservation-commit record (`SC-07`).
CAPITAL_COMMIT_SCHEMA_VERSION = "capital-commit-v1"

#: Schema version of the capital-hours measurement record (v1.5.0 section 12).
CAPITAL_HOURS_SCHEMA_VERSION = "capital-hours-v1"

#: Semantic prefixes of the deterministic identities.
CAPITAL_SNAPSHOT_ID_PREFIX = "CPSN"
CAPITAL_COMMIT_ID_PREFIX = "CCMT"
CAPITAL_HOURS_ID_PREFIX = "CHRS"

#: Applied engineering-scenario policy versions. Each is a deterministic code
#: artifact tagged by this token; none of them ratifies a numeric policy.
CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION = (
    "capital-deployable-engineering-scenario-v1"
)
CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION = (
    "capital-hours-engineering-scenario-v1"
)

#: Every policy version this increment ships. A caller-supplied policy carrying
#: any other token is refused rather than reinterpreted.
DECLARED_CAPITAL_POLICY_VERSIONS: tuple[str, ...] = (
    CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION,
    CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION,
)

#: Owner inputs this increment cannot decide. `OQ-06` (sleeve caps and hard
#: versus soft constraint behaviour) is a register row, so it is never defaulted
#: here, and its numeric policy stays `OWNER_DECISION_REQUIRED`.
OPEN_OWNER_INPUTS: tuple[str, ...] = ("OQ-06",)
OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"

#: The exact money grid. No money field here may carry more precision than this
#: quantum, and none is rounded onto it silently.
MONEY_QUANTUM = Decimal("0.01")

#: The exact capital-hours grid, in currency-hours.
CAPITAL_HOURS_QUANTUM = Decimal("0.0001")

#: The exact `return per hour` grid, in declared currency per currency-hour.
CAPITAL_RETURN_PER_HOUR_QUANTUM = Decimal("0.000001")

#: The frozen record keys of `SC-06`, mapped to the durable payload keys that
#: carry them. A payload missing one of these fails closed.
CAPITAL_SNAPSHOT_RECORD_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "account/environment/engine": ("account", "environment", "engine"),
        "currency": ("currency",),
        "cash partitions": ("cash",),
        "positions": ("positions",),
        "liabilities if supported": (
            "liabilities",
            "liabilities_supported",
            "receivables_supported",
        ),
        "marks": ("marks",),
        "sleeve/risk usage": ("sleeve_usage", "limit_usage"),
        "fee state": ("fee_state",),
        "source watermark": ("source_watermark",),
        "reconciliation": ("reconciliation",),
        "freshness": ("freshness",),
    }
)

#: The declared accounting surfaces. The frozen `SC-06` key is qualified "if
#: supported", and the adopted engine contract supports neither, so both are
#: declared `False` and a supplied value is refused instead of stored.
CAPITAL_LIABILITIES_SUPPORTED = False
CAPITAL_RECEIVABLES_SUPPORTED = False


class CapitalIntelligenceError(ValueError):
    """A structural capital-contract violation. Always fails closed."""


class CapitalEnvironment(str, Enum):
    """The declared environment of a capital record.

    A live observation may inform research, but it can never fund a paper
    account: `CI-02` blocks new allocations whenever the account state cannot be
    trusted, and mixing two books is exactly that.
    """

    PAPER_LEDGER = "PAPER_LEDGER"
    SHADOW_EVIDENCE = "SHADOW_EVIDENCE"
    LIVE_OBSERVATION = "LIVE_OBSERVATION"


class CapitalAccountingState(str, Enum):
    """Whether the engine contract supports this accounting state (`CI-01`).

    An unsupported instrument or accounting state blocks planning, so the state
    is declared explicitly instead of being assumed supported.
    """

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


class CapitalPartition(str, Enum):
    """The four disjoint cash partitions of `CI-01`. There is no fifth."""

    FREE_CASH = "FREE_CASH"
    ORDER_RESERVES = "ORDER_RESERVES"
    SAFETY_RESERVE = "SAFETY_RESERVE"
    OTHER_BLOCKED_CASH = "OTHER_BLOCKED_CASH"


class CapitalRecomputeTrigger(str, Enum):
    """Why a snapshot was recomputed. Only describable reasons exist (`CI-03`)."""

    RESERVATION = "RESERVATION"
    FEE = "FEE"
    MARK = "MARK"
    RECONCILIATION = "RECONCILIATION"
    APPROVAL = "APPROVAL"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    LIABILITY_CHANGE = "LIABILITY_CHANGE"


class CapitalCashLaw(str, Enum):
    """What a declared recompute reason may do to the cash book (`CI-03`).

    ``PRESERVE_PARTITIONS``   - not one cent and not one partition may move.
    ``PRESERVE_TOTAL``        - partitions may be re-partitioned, the total may not.
    ``MAY_ONLY_DECREASE``     - a cost may only reduce the total, by its own
                                declared amount and against a newer watermark.
    """

    PRESERVE_PARTITIONS = "PRESERVE_PARTITIONS"
    PRESERVE_TOTAL = "PRESERVE_TOTAL"
    MAY_ONLY_DECREASE = "MAY_ONLY_DECREASE"


class CapitalBound(str, Enum):
    """The declared bounds deployable capital is the minimum of (`CI-02`)."""

    FREE_CASH = "FREE_CASH"
    RISK = "RISK"
    CONCENTRATION = "CONCENTRATION"
    LIQUIDITY = "LIQUIDITY"
    HORIZON = "HORIZON"
    SLEEVE = "SLEEVE"


class CapitalLimitKind(str, Enum):
    """The declared usage families a bound may be measured against."""

    RISK = "RISK"
    CONCENTRATION = "CONCENTRATION"
    LIQUIDITY = "LIQUIDITY"


class CapitalReconciliationStatus(str, Enum):
    """The declared reconciliation disposition of a snapshot (`CI-02`)."""

    RECONCILED = "RECONCILED"
    FAILED = "FAILED"
    NOT_PERFORMED = "NOT_PERFORMED"


class CapitalDeployableStatus(str, Enum):
    """A deployable evaluation either publishes a number or abstains."""

    AVAILABLE = "AVAILABLE"
    ABSTAINED = "ABSTAINED"


class CapitalDeployableAbstentionReason(str, Enum):
    """The closed abstention vocabulary of a deployable-capital evaluation.

    Missing evidence is never favourable evidence (`CI-02`), so every reason
    here publishes no number instead of a conservative guess.
    """

    IDENTITY_NOT_BOUND = "IDENTITY_NOT_BOUND"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    LIVE_OBSERVATION_CANNOT_FUND_PAPER_ACCOUNT = (
        "LIVE_OBSERVATION_CANNOT_FUND_PAPER_ACCOUNT"
    )
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    UNSUPPORTED_ACCOUNTING_STATE = "UNSUPPORTED_ACCOUNTING_STATE"
    ACCOUNT_STATE_STALE = "ACCOUNT_STATE_STALE"
    FAILED_RECONCILIATION = "FAILED_RECONCILIATION"
    RECONCILIATION_NOT_PERFORMED = "RECONCILIATION_NOT_PERFORMED"
    UNRESOLVED_NEGATIVE_BALANCE = "UNRESOLVED_NEGATIVE_BALANCE"
    MISSING_FEES = "MISSING_FEES"
    SNAPSHOT_OUTLIVED_VALIDITY = "SNAPSHOT_OUTLIVED_VALIDITY"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    LIMIT_NOT_CONFIGURED = "LIMIT_NOT_CONFIGURED"
    SLEEVE_LIMIT_NOT_CONFIGURED = "SLEEVE_LIMIT_NOT_CONFIGURED"


class CapitalCommitStatus(str, Enum):
    """The outcome of one compare-and-commit reservation attempt (`CI-04`)."""

    COMMITTED = "COMMITTED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    REJECTED = "REJECTED"


class CapitalCommitRejection(str, Enum):
    """Why a commit attempt was refused without changing the capital record."""

    STALE_EXPECTED_VERSION = "STALE_EXPECTED_VERSION"
    PLAN_ALREADY_CONSUMED = "PLAN_ALREADY_CONSUMED"
    NON_POSITIVE_AMOUNT = "NON_POSITIVE_AMOUNT"
    ENVIRONMENT_NOT_FUNDABLE = "ENVIRONMENT_NOT_FUNDABLE"
    INSUFFICIENT_FREE_CASH = "INSUFFICIENT_FREE_CASH"
    UNSUPPORTED_RECOMPUTE_TRIGGER = "UNSUPPORTED_RECOMPUTE_TRIGGER"
    SNAPSHOT_OUTLIVED_VALIDITY = "SNAPSHOT_OUTLIVED_VALIDITY"


class CapitalHoursStatus(str, Enum):
    """A capital-hours measurement is either measured or unavailable."""

    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"


class CapitalHoursUnavailableReason(str, Enum):
    """Why a capital-hours measurement abstains (v1.5.0 section 12).

    "Zero/unknown occupancy yields unavailable", so the null policy is a named
    reason and never a zero.
    """

    NO_OCCUPANCY_POINTS = "NO_OCCUPANCY_POINTS"
    ENDPOINT_POLICY_NOT_DECLARED = "ENDPOINT_POLICY_NOT_DECLARED"
    POINTS_NOT_MONOTONE = "POINTS_NOT_MONOTONE"
    POINT_BEFORE_START = "POINT_BEFORE_START"
    DECLARED_COSTS_MISSING = "DECLARED_COSTS_MISSING"
    ZERO_OCCUPANCY = "ZERO_OCCUPANCY"


class CapitalHoursEndpointPolicy(str, Enum):
    """Which declared basis resolves an occupancy interval between two points."""

    STEP_START = "STEP_START"
    STEP_END = "STEP_END"


class CapitalTimeEfficiencyStatus(str, Enum):
    """A cohort's capital-time efficiency is either measured or unavailable."""

    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"


#: Recompute triggers the frozen clause cannot describe. `CI-03` recomputes on
#: committed fills, cancellations, expiries, deposits/withdrawals *if
#: supported*, fees, marks and approved policy changes; this engine declares no
#: external cash-movement or liability support, so those reasons are refused
#: rather than recorded as if a snapshot could explain them.
CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS: tuple[CapitalRecomputeTrigger, ...] = (
    CapitalRecomputeTrigger.DEPOSIT,
    CapitalRecomputeTrigger.WITHDRAWAL,
    CapitalRecomputeTrigger.LIABILITY_CHANGE,
)

#: The declared cash law of every *supported* recompute reason. A reason with no
#: declared law cannot be recomputed at all.
CAPITAL_RECOMPUTE_CASH_LAWS: Mapping[
    CapitalRecomputeTrigger, CapitalCashLaw
] = MappingProxyType(
    {
        CapitalRecomputeTrigger.RESERVATION: CapitalCashLaw.PRESERVE_TOTAL,
        CapitalRecomputeTrigger.FEE: CapitalCashLaw.MAY_ONLY_DECREASE,
        CapitalRecomputeTrigger.MARK: CapitalCashLaw.PRESERVE_PARTITIONS,
        CapitalRecomputeTrigger.RECONCILIATION: CapitalCashLaw.PRESERVE_TOTAL,
        CapitalRecomputeTrigger.APPROVAL: CapitalCashLaw.PRESERVE_TOTAL,
    }
)

#: The bound vocabulary in the order a tie is resolved (`CI-02`). Free cash is
#: first because cash may never be exceeded, whatever the limits say.
CAPITAL_BOUND_PRECEDENCE: tuple[CapitalBound, ...] = (
    CapitalBound.FREE_CASH,
    CapitalBound.RISK,
    CapitalBound.CONCENTRATION,
    CapitalBound.LIQUIDITY,
    CapitalBound.HORIZON,
    CapitalBound.SLEEVE,
)

#: The abstention vocabulary in the order a decision is taken. Exactly one
#: reason is published: the first applicable one, so an evaluation never has to
#: arbitrate between two explanations of the same missing evidence.
CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE: tuple[
    CapitalDeployableAbstentionReason, ...
] = (
    CapitalDeployableAbstentionReason.IDENTITY_NOT_BOUND,
    CapitalDeployableAbstentionReason.CURRENCY_MISMATCH,
    CapitalDeployableAbstentionReason.LIVE_OBSERVATION_CANNOT_FUND_PAPER_ACCOUNT,
    CapitalDeployableAbstentionReason.IDENTITY_MISMATCH,
    CapitalDeployableAbstentionReason.UNSUPPORTED_ACCOUNTING_STATE,
    CapitalDeployableAbstentionReason.ACCOUNT_STATE_STALE,
    CapitalDeployableAbstentionReason.FAILED_RECONCILIATION,
    CapitalDeployableAbstentionReason.RECONCILIATION_NOT_PERFORMED,
    CapitalDeployableAbstentionReason.UNRESOLVED_NEGATIVE_BALANCE,
    CapitalDeployableAbstentionReason.MISSING_FEES,
    CapitalDeployableAbstentionReason.SNAPSHOT_OUTLIVED_VALIDITY,
    CapitalDeployableAbstentionReason.EVIDENCE_INCOMPLETE,
    CapitalDeployableAbstentionReason.LIMIT_NOT_CONFIGURED,
    CapitalDeployableAbstentionReason.SLEEVE_LIMIT_NOT_CONFIGURED,
)

#: One hour in seconds. The occupancy integral is expressed in currency-hours,
#: so a span is divided by this declared constant and never by a locale value.
CAPITAL_SECONDS_PER_HOUR = Decimal("3600")

#: The labelled occupancy-basis definition this engineering scenario measures
#: against. The basis is *declared per occupancy point* by the caller - per the
#: adopted section 12, committed capital is allocated position capital plus
#: outstanding reserved cash with no overlap after fills - and the token is
#: recorded so the definition is versioned rather than implicit. ``# nosec B105``
#: - the declared basis label is a version string, not a protected value.
CAPITAL_HOURS_BASIS_TOKEN = (  # nosec B105
    "declared-allocated-basis-engineering-scenario-v1"
)

_MONEY_TEXT = re.compile(r"\A-?\d+\.\d{2}\Z")

#: The widest difference a float from the frozen portfolio owner may carry and
#: still be read as the exact money value it stands for. It is a nanocent, far
#: below the declared quantum, so nothing is silently rounded onto the grid.
_FLOAT_TOLERANCE = Decimal("0.000001")


def _money(value: Any, *, field_name: str) -> Decimal:
    """Return ``value`` as an exact ``Decimal`` on the declared money grid.

    Only a two-place decimal string or an already-exact ``Decimal`` on the grid
    is accepted. A float, a boolean, an integer, a non-finite value or a value
    carrying more precision than ``MONEY_QUANTUM`` is refused instead of being
    rounded, defaulted or coerced.
    """

    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if _MONEY_TEXT.fullmatch(text) is None:
            raise CapitalIntelligenceError(
                f"{field_name} must be an exact decimal with two places, got {value!r}"
            )
        parsed = Decimal(text)
    else:
        raise CapitalIntelligenceError(
            f"{field_name} must be an exact decimal string or Decimal value, "
            f"not {type(value).__name__}"
        )
    if not parsed.is_finite():
        raise CapitalIntelligenceError(f"{field_name} must be a finite amount")
    if -parsed.as_tuple().exponent > 2:
        raise CapitalIntelligenceError(
            f"{field_name} carries more precision than the declared money quantum "
            f"{MONEY_QUANTUM}"
        )
    return parsed.quantize(MONEY_QUANTUM)


def _money_from_float(value: Any, *, field_name: str) -> Decimal:
    """Read a float from the frozen portfolio owner onto the money grid.

    The R3 F7 owner stores capital as a float, so the commit seam has to cross
    that boundary explicitly. A value that is not already an exact money amount
    (beyond ``_FLOAT_TOLERANCE``) is refused, so the crossing can never round a
    reservation into a different amount than the plan declared.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapitalIntelligenceError(
            f"{field_name} must be a plan amount, not {type(value).__name__}"
        )
    try:
        raw = Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise CapitalIntelligenceError(f"{field_name} is not a finite amount") from exc
    if not raw.is_finite():
        raise CapitalIntelligenceError(f"{field_name} must be a finite amount")
    rounded = raw.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if abs(raw - rounded) > _FLOAT_TOLERANCE:
        raise CapitalIntelligenceError(
            f"{field_name} does not lie on the declared money quantum {MONEY_QUANTUM}"
        )
    return rounded


def _money_text(value: Decimal) -> str:
    """Render a money amount without an exponent, so it round-trips exactly."""

    return format(value, "f")


def _text(value: Any, *, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CapitalIntelligenceError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if text == "":
        raise CapitalIntelligenceError(f"{field_name} must be a non-empty string")
    return text


def _utc(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise CapitalIntelligenceError(
            f"{field_name} must be a timezone-aware UTC instant"
        )
    try:
        return require_utc(value, field_name=field_name)
    except ValueError as exc:
        raise CapitalIntelligenceError(str(exc)) from exc


def _enum_of(enum_type: Any, value: Any, *, field_name: str) -> Any:
    """Return ``value`` only when it is already a declared member of ``enum_type``.

    A plain string is refused: a token that only *looks* like a member would
    otherwise be coerced, and a coerced reason is a fabricated one.
    """

    if isinstance(value, enum_type):
        return value
    declared = tuple(member.value for member in enum_type)
    raise CapitalIntelligenceError(f"{field_name} must be one of {declared}")


def _optional_enum_of(enum_type: Any, value: Any, *, field_name: str) -> Any:
    return None if value is None else _enum_of(enum_type, value, field_name=field_name)


def _require_policy_version(
    value: Any, *, field_name: str, declared: tuple[str, ...]
) -> str:
    token = _text(value, field_name=field_name)
    if token not in declared:
        raise CapitalIntelligenceError(
            f"{field_name} {token!r} is not a declared capital policy version "
            f"{declared}"
        )
    return token


def _require_supported_trigger(
    value: Any, *, field_name: str = "trigger"
) -> CapitalRecomputeTrigger:
    trigger = _enum_of(CapitalRecomputeTrigger, value, field_name=field_name)
    if trigger in CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS:
        raise CapitalIntelligenceError(
            f"{field_name} {trigger.value} is not a describable recompute reason: "
            "this engine declares no external cash-movement or liability support"
        )
    if trigger not in CAPITAL_RECOMPUTE_CASH_LAWS:
        raise CapitalIntelligenceError(
            f"{field_name} {trigger.value} has no declared cash law"
        )
    return trigger


def _records(values: Any, *, field_name: str, kind: Any) -> tuple[Any, ...]:
    """Freeze a declared sequence of records, refusing anything else."""

    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise CapitalIntelligenceError(f"{field_name} must be a sequence of {kind}")
    rows = tuple(values)
    for row in rows:
        if not isinstance(row, kind):
            raise CapitalIntelligenceError(f"{field_name} must be a sequence of {kind}")
    return rows


def _unique_records(
    values: Any, *, field_name: str, kind: Any, key: Any, key_name: str = "record"
) -> tuple[Any, ...]:
    """Freeze a declared sequence whose records are unique by ``key``."""

    rows = _records(values, field_name=field_name, kind=kind)
    seen: set[Any] = set()
    for row in rows:
        marker = key(row)
        if marker in seen:
            raise CapitalIntelligenceError(
                f"{field_name} must not repeat the same {key_name}: {marker}"
            )
        seen.add(marker)
    return rows


_WATERMARK_DURABLE_KEYS: tuple[str, ...] = (
    "ledger_sequence",
    "observed_at",
    "state_is_stale",
)
_FRESHNESS_DURABLE_KEYS: tuple[str, ...] = (
    "as_of",
    "validity_deadline",
    "evidence_complete",
)
_RECONCILIATION_DURABLE_KEYS: tuple[str, ...] = (
    "status",
    "observed_at",
    "reference",
    "variance",
)


def _durable_body(
    raw: Any, *, field_name: str, expected_keys: tuple[str, ...]
) -> Mapping[str, Any]:
    """Refuse a persisted payload whose key set is not exactly the declared one."""

    if not isinstance(raw, Mapping):
        raise CapitalIntelligenceError(f"{field_name} payload must be a mapping")
    missing = [key for key in expected_keys if key not in raw]
    extra = [key for key in raw if key not in expected_keys]
    if missing or extra:
        raise CapitalIntelligenceError(
            f"{field_name} payload keys drifted: missing {missing}, "
            f"unexpected {extra}"
        )
    return raw


def _persisted_utc(value: Any, *, field_name: str) -> datetime:
    """Read back the canonical ``Z`` instant form written by ``iso_z``."""

    text = _text(value, field_name=field_name)
    if not text.endswith("Z"):
        raise CapitalIntelligenceError(
            f"{field_name} must be persisted in the canonical Z form"
        )
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise CapitalIntelligenceError(
            f"{field_name} is not a canonical UTC instant"
        ) from exc
    return _utc(moment, field_name=field_name)


@dataclass(frozen=True)
class CapitalSourceWatermark:
    """Where an account state came from, when it was observed and whether it is stale.

    ``state_is_stale`` has three states on purpose: ``False`` is fresh, ``True``
    is stale, and ``None`` is *undetermined*. An undetermined account state is
    never read as fresh (`CI-02` blocks new allocations on a stale account state,
    and an unknown one may not be treated as better than a stale one).
    """

    ledger_sequence: int
    observed_at: datetime
    state_is_stale: bool | None

    def __post_init__(self) -> None:
        sequence = self.ledger_sequence
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise CapitalIntelligenceError(
                "ledger_sequence must be a non-negative integer"
            )
        stale = self.state_is_stale
        if stale is not None and not isinstance(stale, bool):
            raise CapitalIntelligenceError(
                "state_is_stale must be True, False or an explicit undetermined"
            )
        object.__setattr__(self, "ledger_sequence", sequence)
        object.__setattr__(
            self, "observed_at", _utc(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(self, "state_is_stale", stale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_sequence": self.ledger_sequence,
            "observed_at": iso_z(self.observed_at, field_name="observed_at"),
            "state_is_stale": self.state_is_stale,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalSourceWatermark":
        body = _durable_body(
            raw,
            field_name="source_watermark",
            expected_keys=_WATERMARK_DURABLE_KEYS,
        )
        return cls(
            ledger_sequence=body["ledger_sequence"],
            observed_at=_persisted_utc(body["observed_at"], field_name="observed_at"),
            state_is_stale=body["state_is_stale"],
        )


@dataclass(frozen=True)
class CapitalFreshness:
    """The declared observation instant, validity limit and evidence completeness.

    A snapshot "has a validity limit and cannot outlive its required evidence"
    (`CI-03`), so the limit is declared with the observation and an observation
    that is not evidence-complete says so instead of being assumed complete.
    """

    as_of: datetime
    validity_deadline: datetime
    evidence_complete: bool

    def __post_init__(self) -> None:
        as_of = _utc(self.as_of, field_name="as_of")
        deadline = _utc(self.validity_deadline, field_name="validity_deadline")
        if deadline <= as_of:
            raise CapitalIntelligenceError(
                "validity_deadline must be strictly after as_of: a capital snapshot "
                "may not outlive its declared validity limit"
            )
        complete = self.evidence_complete
        if not isinstance(complete, bool):
            raise CapitalIntelligenceError("evidence_complete must be True or False")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "validity_deadline", deadline)
        object.__setattr__(self, "evidence_complete", complete)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": iso_z(self.as_of, field_name="as_of"),
            "validity_deadline": iso_z(
                self.validity_deadline, field_name="validity_deadline"
            ),
            "evidence_complete": self.evidence_complete,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalFreshness":
        body = _durable_body(
            raw, field_name="freshness", expected_keys=_FRESHNESS_DURABLE_KEYS
        )
        return cls(
            as_of=_persisted_utc(body["as_of"], field_name="as_of"),
            validity_deadline=_persisted_utc(
                body["validity_deadline"], field_name="validity_deadline"
            ),
            evidence_complete=body["evidence_complete"],
        )


@dataclass(frozen=True)
class CapitalReconciliation:
    """The declared reconciliation of one snapshot against the source ledger.

    ``FAILED`` and ``NOT_PERFORMED`` are recorded states, not errors: a snapshot
    that could not be reconciled says so, and the evaluator abstains on it.
    ``NOT_PERFORMED`` may carry no variance, because a reconciliation that did
    not run observed nothing to report.
    """

    status: CapitalReconciliationStatus
    observed_at: datetime
    reference: str
    variance: Decimal | None = None

    def __post_init__(self) -> None:
        status = _enum_of(
            CapitalReconciliationStatus, self.status, field_name="status"
        )
        observed = _utc(self.observed_at, field_name="observed_at")
        reference = _text(self.reference, field_name="reference")
        variance = (
            None
            if self.variance is None
            else _money(self.variance, field_name="variance")
        )
        if status is CapitalReconciliationStatus.RECONCILED:
            if variance is None or variance != Decimal("0.00"):
                raise CapitalIntelligenceError(
                    "a reconciled capital state declares a zero variance"
                )
        elif status is CapitalReconciliationStatus.FAILED:
            if variance is None:
                raise CapitalIntelligenceError(
                    "a failed reconciliation must declare the observed variance"
                )
        elif variance is not None:
            raise CapitalIntelligenceError(
                "a reconciliation that was not performed declares no variance"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "variance", variance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "observed_at": iso_z(self.observed_at, field_name="observed_at"),
            "reference": self.reference,
            "variance": None if self.variance is None else _money_text(self.variance),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalReconciliation":
        body = _durable_body(
            raw,
            field_name="reconciliation",
            expected_keys=_RECONCILIATION_DURABLE_KEYS,
        )
        return cls(
            status=CapitalReconciliationStatus(body["status"]),
            observed_at=_persisted_utc(body["observed_at"], field_name="observed_at"),
            reference=body["reference"],
            variance=body["variance"],
        )


@dataclass(frozen=True)
class CapitalFeeState:
    """The declared fee model, settled fees and the fee watermark.

    Fees are declared, never estimated: a snapshot with no declared fee state is
    a snapshot whose fees are unknown, and unknown costs abstain (`CI-02` lists
    missing fees among the conditions that block new allocations).
    """

    fee_model_version: str
    fees_settled: Decimal
    fee_watermark: CapitalSourceWatermark

    def __post_init__(self) -> None:
        version = _text(self.fee_model_version, field_name="fee_model_version")
        settled = _money(self.fees_settled, field_name="fees_settled")
        if settled < 0:
            raise CapitalIntelligenceError("fees_settled must not be negative")
        watermark = self.fee_watermark
        if not isinstance(watermark, CapitalSourceWatermark):
            raise CapitalIntelligenceError(
                "fee_watermark must be a CapitalSourceWatermark"
            )
        object.__setattr__(self, "fee_model_version", version)
        object.__setattr__(self, "fees_settled", settled)
        object.__setattr__(self, "fee_watermark", watermark)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fee_model_version": self.fee_model_version,
            "fees_settled": _money_text(self.fees_settled),
            "fee_watermark": self.fee_watermark.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalFeeState":
        body = _durable_body(
            raw,
            field_name="fee_state",
            expected_keys=_FEE_STATE_DURABLE_KEYS,
        )
        return cls(
            fee_model_version=body["fee_model_version"],
            fees_settled=body["fees_settled"],
            fee_watermark=CapitalSourceWatermark.from_dict(body["fee_watermark"]),
        )


_FEE_STATE_DURABLE_KEYS: tuple[str, ...] = (
    "fee_model_version",
    "fees_settled",
    "fee_watermark",
)
_POSITION_DURABLE_KEYS: tuple[str, ...] = (
    "symbol",
    "quantity",
    "basis_capital",
    "marked_value",
    "marked_at",
    "mark_reference",
)
_MARK_STATE_DURABLE_KEYS: tuple[str, ...] = (
    "marked_at",
    "mark_source",
    "marked_position_value",
)
_SLEEVE_USAGE_DURABLE_KEYS: tuple[str, ...] = ("sleeve", "used_capital", "as_of")
_LIMIT_USAGE_DURABLE_KEYS: tuple[str, ...] = ("kind", "used_capital", "as_of")


def _quantity(value: Any, *, field_name: str) -> Decimal:
    """Return an exact, strictly positive quantity.

    A quantity is not money, so it is not forced onto the cent grid, but it is
    still refused as a float or a non-positive value instead of being coerced.
    """

    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise CapitalIntelligenceError(
                f"{field_name} must be an exact decimal, got {value!r}"
            ) from exc
    else:
        raise CapitalIntelligenceError(
            f"{field_name} must be an exact decimal string or Decimal value, "
            f"not {type(value).__name__}"
        )
    if not parsed.is_finite() or parsed <= 0:
        raise CapitalIntelligenceError(f"{field_name} must be a positive amount")
    return parsed


@dataclass(frozen=True)
class CapitalPosition:
    """One declared position: its basis and its declared marked value.

    Deployed basis and marked value are reported *separately from cash*
    (`CI-01`): the marked gain of a position is not free cash and may not fund a
    reservation, so it never enters a cash partition.
    """

    symbol: str
    quantity: Decimal
    basis_capital: Decimal
    marked_value: Decimal
    marked_at: datetime
    mark_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "symbol", _text(self.symbol, field_name="symbol")
        )
        object.__setattr__(
            self, "quantity", _quantity(self.quantity, field_name="quantity")
        )
        basis = _money(self.basis_capital, field_name="basis_capital")
        marked = _money(self.marked_value, field_name="marked_value")
        if basis < 0:
            raise CapitalIntelligenceError("basis_capital must not be negative")
        if marked < 0:
            raise CapitalIntelligenceError("marked_value must not be negative")
        if not isinstance(self.marked_at, datetime):
            raise CapitalIntelligenceError(
                "marked_at must be a timezone-aware UTC instant"
            )
        object.__setattr__(self, "basis_capital", basis)
        object.__setattr__(self, "marked_value", marked)
        object.__setattr__(
            self, "marked_at", _utc(self.marked_at, field_name="marked_at")
        )
        object.__setattr__(
            self, "mark_reference", _text(self.mark_reference, field_name="mark_reference")
        )

    @property
    def unrealized_delta(self) -> Decimal:
        """The declared mark movement. A diagnostic, never spendable cash."""

        return self.marked_value - self.basis_capital

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": format(self.quantity, "f"),
            "basis_capital": _money_text(self.basis_capital),
            "marked_value": _money_text(self.marked_value),
            "marked_at": iso_z(self.marked_at, field_name="marked_at"),
            "mark_reference": self.mark_reference,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalPosition":
        body = _durable_body(
            raw, field_name="position", expected_keys=_POSITION_DURABLE_KEYS
        )
        return cls(
            symbol=body["symbol"],
            quantity=body["quantity"],
            basis_capital=body["basis_capital"],
            marked_value=body["marked_value"],
            marked_at=_persisted_utc(body["marked_at"], field_name="marked_at"),
            mark_reference=body["mark_reference"],
        )


@dataclass(frozen=True)
class CapitalMarkState:
    """The declared marked value of the whole declared position set.

    The aggregate is declared *and* reconciled against the positions: a marked
    aggregate that no position explains is an orphan number, and an orphan number
    would let a snapshot claim equity it cannot account for.
    """

    marked_at: datetime
    mark_source: str
    marked_position_value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.marked_at, datetime):
            raise CapitalIntelligenceError(
                "marked_at must be a timezone-aware UTC instant"
            )
        source = _text(self.mark_source, field_name="mark_source")
        marked = _money(self.marked_position_value, field_name="marked_position_value")
        if marked < 0:
            raise CapitalIntelligenceError("marked_position_value must not be negative")
        object.__setattr__(
            self, "marked_at", _utc(self.marked_at, field_name="marked_at")
        )
        object.__setattr__(self, "mark_source", source)
        object.__setattr__(self, "marked_position_value", marked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "marked_at": iso_z(self.marked_at, field_name="marked_at"),
            "mark_source": self.mark_source,
            "marked_position_value": _money_text(self.marked_position_value),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalMarkState":
        body = _durable_body(
            raw, field_name="marks", expected_keys=_MARK_STATE_DURABLE_KEYS
        )
        return cls(
            marked_at=_persisted_utc(body["marked_at"], field_name="marked_at"),
            mark_source=body["mark_source"],
            marked_position_value=body["marked_position_value"],
        )


@dataclass(frozen=True)
class SleeveUsage:
    """Declared capital already used inside one policy sleeve.

    "Policy sleeves constrain one shared ledger; they are not extra balances that
    can be spent twice" (`CI-02`), so sleeve usage is declared usage against the
    same book, and never a second balance.
    """

    sleeve: str
    used_capital: Decimal
    as_of: datetime

    def __post_init__(self) -> None:
        sleeve = _text(self.sleeve, field_name="sleeve")
        used = _money(self.used_capital, field_name="used_capital")
        if used < 0:
            raise CapitalIntelligenceError("used_capital must not be negative")
        object.__setattr__(self, "sleeve", sleeve)
        object.__setattr__(self, "used_capital", used)
        object.__setattr__(self, "as_of", _utc(self.as_of, field_name="as_of"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sleeve": self.sleeve,
            "used_capital": _money_text(self.used_capital),
            "as_of": iso_z(self.as_of, field_name="as_of"),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SleeveUsage":
        body = _durable_body(
            raw, field_name="sleeve_usage", expected_keys=_SLEEVE_USAGE_DURABLE_KEYS
        )
        return cls(
            sleeve=body["sleeve"],
            used_capital=body["used_capital"],
            as_of=_persisted_utc(body["as_of"], field_name="as_of"),
        )


@dataclass(frozen=True)
class CapitalLimitUsage:
    """Declared capital already used against one declared limit family.

    Usage is always declared by the caller. Nothing in this section infers usage
    from a position list, because an inferred usage would silently become a
    policy input.
    """

    kind: CapitalLimitKind
    used_capital: Decimal
    as_of: datetime

    def __post_init__(self) -> None:
        kind = _enum_of(CapitalLimitKind, self.kind, field_name="kind")
        used = _money(self.used_capital, field_name="used_capital")
        if used < 0:
            raise CapitalIntelligenceError("used_capital must not be negative")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "used_capital", used)
        object.__setattr__(self, "as_of", _utc(self.as_of, field_name="as_of"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "used_capital": _money_text(self.used_capital),
            "as_of": iso_z(self.as_of, field_name="as_of"),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalLimitUsage":
        body = _durable_body(
            raw, field_name="limit_usage", expected_keys=_LIMIT_USAGE_DURABLE_KEYS
        )
        return cls(
            kind=CapitalLimitKind(body["kind"]),
            used_capital=body["used_capital"],
            as_of=_persisted_utc(body["as_of"], field_name="as_of"),
        )


_CASH_LEDGER_DURABLE_KEYS: tuple[str, ...] = ("partitions", "cash_total")


@dataclass(frozen=True)
class CashPartitionLedger:
    """The disjoint cash partitions of `CI-01`, and nothing else.

    "Reconcile cash_total into free_cash, order_reserves, safety_reserve and
    other_blocked_cash. All partitions are disjoint." The ledger therefore
    refuses an incomplete set, an unknown key or a set that does not reconstruct
    the declared total: there is exactly one cash book, and it must add up.
    """

    partitions: Mapping[CapitalPartition, Decimal]
    cash_total: Decimal

    def __post_init__(self) -> None:
        raw = self.partitions
        if not isinstance(raw, Mapping):
            raise CapitalIntelligenceError(
                "cash partitions must be a mapping of the declared cash partitions "
                f"{tuple(member.value for member in CapitalPartition)}"
            )
        declared: dict[CapitalPartition, Decimal] = {}
        for key, value in raw.items():
            if not isinstance(key, CapitalPartition):
                raise CapitalIntelligenceError(
                    "cash partitions must be exactly the declared cash partitions "
                    f"{tuple(member.value for member in CapitalPartition)}, "
                    f"got {key!r}"
                )
            if key in declared:
                raise CapitalIntelligenceError(
                    f"cash partitions must not repeat {key.value}"
                )
            declared[key] = _money(value, field_name=f"cash partitions[{key.value}]")
        missing = [
            member.value for member in CapitalPartition if member not in declared
        ]
        if missing:
            raise CapitalIntelligenceError(
                "cash partitions must be exactly the declared cash partitions; "
                "missing " + ", ".join(missing)
            )
        total = _money(self.cash_total, field_name="cash_total")
        reconstructed = sum(
            (declared[member] for member in CapitalPartition), Decimal("0.00")
        )
        if reconstructed != total:
            raise CapitalIntelligenceError(
                "cash partitions do not reconstruct the declared cash total "
                f"{_money_text(total)}: they sum to {_money_text(reconstructed)}"
            )
        object.__setattr__(
            self,
            "partitions",
            MappingProxyType(
                {member: declared[member] for member in CapitalPartition}
            ),
        )
        object.__setattr__(self, "cash_total", total)

    @property
    def free_cash(self) -> Decimal:
        return self.partitions[CapitalPartition.FREE_CASH]

    @property
    def has_negative_amount(self) -> bool:
        """True when any partition is negative, so the book is unresolved.

        A negative partition is an unresolved negative balance (`CI-02`), not a
        rounding artifact, so it is reported rather than netted away.
        """

        return any(amount < 0 for amount in self.partitions.values())

    def amount(self, partition: CapitalPartition) -> Decimal:
        member = _enum_of(CapitalPartition, partition, field_name="partition")
        return self.partitions[member]

    def to_dict(self) -> dict[str, Any]:
        return {
            "partitions": {
                member.value: _money_text(self.partitions[member])
                for member in CapitalPartition
            },
            "cash_total": _money_text(self.cash_total),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CashPartitionLedger":
        body = _durable_body(
            raw, field_name="cash", expected_keys=_CASH_LEDGER_DURABLE_KEYS
        )
        stored = body["partitions"]
        if not isinstance(stored, Mapping):
            raise CapitalIntelligenceError(
                "cash partitions must be a mapping of the declared cash partitions"
            )
        partitions: dict[CapitalPartition, Any] = {}
        for key, value in stored.items():
            if key not in {member.value for member in CapitalPartition}:
                raise CapitalIntelligenceError(
                    "cash partitions must be exactly the declared cash partitions "
                    f"{tuple(member.value for member in CapitalPartition)}, "
                    f"got {key!r}"
                )
            partitions[CapitalPartition(key)] = value
        return cls(partitions=partitions, cash_total=body["cash_total"])


#: The process-scoped witness of the capital records this engine has already
#: seen. It is deliberately *not* a store: it is in-memory only, it is never
#: written anywhere, it holds no authority, and it exists so a compare-and-commit
#: can prove that a claim about a predecessor is a claim about a record this
#: process actually observed (`CI-04`). Reservation state lives with the capital
#: services, not in a new store, exactly as the frozen `SC-07` contract requires.
_CAPITAL_WITNESS_ATTRIBUTE = "_opip_r5_c_capital_witness_v1"

#: The most records the in-process witness retains before it evicts the oldest.
#: A bounded witness cannot grow into an unbounded store by accident.
_CAPITAL_WITNESS_LIMIT = 4096


def _capital_witness() -> dict[str, Any]:
    """Return the process-scoped capital witness, creating it once if needed."""

    holder = getattr(sys, _CAPITAL_WITNESS_ATTRIBUTE, None)
    if isinstance(holder, dict):
        return holder
    holder: dict[str, Any] = {
        "lock": threading.RLock(),
        "snapshots": {},
        "versions": {},
        "current": {},
        "plans": {},
    }
    setattr(sys, _CAPITAL_WITNESS_ATTRIBUTE, holder)
    return holder


def _remember(bucket: dict[str, Any], key: Any, value: Any) -> None:
    """Record one witness entry, evicting the oldest when the cap is reached."""

    if key not in bucket and len(bucket) >= _CAPITAL_WITNESS_LIMIT:
        del bucket[next(iter(bucket))]
    bucket[key] = value


_LINEAGE_VERSION = re.compile(r"\A(?P<base>.*\.)(?P<index>\d{4})\Z")


def _lineage_parts(version: str) -> tuple[str, int] | None:
    """Split a decremented portfolio version into its base and its index."""

    match = _LINEAGE_VERSION.match(version)
    if match is None:
        return None
    return match.group("base"), int(match.group("index"))


def _predecessor_version(version: str) -> str | None:
    """Return the portfolio version a record's version claims to follow."""

    parts = _lineage_parts(version)
    if parts is None:
        return None
    base, index = parts
    if index <= 1:
        return base.rstrip(".")
    return f"{base}{index - 1:04d}"


def next_portfolio_version(
    version: str, *, trigger: CapitalRecomputeTrigger
) -> str:
    """Return the one admissible successor of a portfolio version.

    Version lineage is monotone and unforked: a version moves forward by exactly
    one declared step, so two recomputes of the same base can never both claim to
    be "the" successor. The trigger is validated here as well, because a version
    that cannot say why it moved is not a describable recompute either.
    """

    current = _text(version, field_name="portfolio_version")
    _require_supported_trigger(trigger)
    parts = _lineage_parts(current)
    if parts is None:
        return f"{current}.0001"
    base, index = parts
    if index >= 9999:
        raise CapitalIntelligenceError(
            f"portfolio version lineage is exhausted at {current}"
        )
    return f"{base}{index + 1:04d}"


def assert_portfolio_version_lineage(
    previous: str,
    successor: str,
    *,
    trigger: CapitalRecomputeTrigger = CapitalRecomputeTrigger.APPROVAL,
) -> None:
    """Fail closed unless ``successor`` is exactly the next declared version."""

    declared = _text(previous, field_name="previous_portfolio_version")
    observed = _text(successor, field_name="successor_portfolio_version")
    expected = next_portfolio_version(declared, trigger=trigger)
    if observed != expected:
        raise CapitalIntelligenceError(
            "portfolio version lineage must move forward by exactly one step: "
            f"{declared} -> {expected}, not {observed}"
        )


def _record_witness(record: "CapitalSnapshot") -> None:
    """Witness one snapshot's lineage, identity and account facts."""

    holder = _capital_witness()
    facts = {
        "portfolio_version": record.portfolio_version,
        "account": record.account,
        "environment": record.environment,
        "engine": record.engine,
        "currency": record.currency,
        "as_of": record.freshness.as_of,
        "cash": record.cash.to_dict(),
        "marks": (record.marks.marked_at, record.marks.marked_position_value),
    }
    with holder["lock"]:
        _remember(holder["snapshots"], record.snapshot_id, facts)
        ids = holder["versions"].get(record.portfolio_version, ())
        if record.snapshot_id not in ids:
            holder["versions"][record.portfolio_version] = (*ids, record.snapshot_id)


def _predecessor_witness(record: "CapitalSnapshot") -> Mapping[str, Any] | None:
    """Return the witnessed facts of the declared predecessor, or refuse the claim.

    ``derived_from`` is a claim about another record, not decoration. When this
    process has witnessed snapshots of the predecessor portfolio version but not
    the id the record names, the claim is refused instead of being trusted.
    """

    declared = record.derived_from
    if declared is None:
        return None
    holder = _capital_witness()
    with holder["lock"]:
        witness = holder["snapshots"].get(declared)
        if witness is not None:
            return witness
        claimed_version = _predecessor_version(record.portfolio_version)
        witnessed_ids = (
            () if claimed_version is None else holder["versions"].get(claimed_version, ())
        )
    if witnessed_ids:
        raise CapitalIntelligenceError(
            "derived_from must reference the witnessed snapshot of the predecessor "
            f"portfolio version {claimed_version}, not {declared!r}"
        )
    return None


def _enum_from_token(enum_type: Any, value: Any, *, field_name: str) -> Any:
    """Read a persisted token back into its declared member."""

    try:
        return enum_type(value)
    except ValueError as exc:
        raise CapitalIntelligenceError(
            f"{field_name} {value!r} is not a declared token of {enum_type.__name__}"
        ) from exc


def _freeze_capital_snapshot(record: "CapitalSnapshot") -> str:
    """Validate and normalize one snapshot; return its expected identity.

    Every refusal here is a refusal to *interpret*: a mismatched mark instant, an
    orphan marked aggregate, a duplicated symbol, an undeclared recompute reason,
    a liability surface this engine does not support, or a value off the declared
    money grid is refused instead of being reconciled silently.
    """

    object.__setattr__(
        record,
        "portfolio_version",
        _text(record.portfolio_version, field_name="portfolio_version"),
    )
    object.__setattr__(record, "account", _text(record.account, field_name="account"))
    object.__setattr__(record, "engine", _text(record.engine, field_name="engine"))
    object.__setattr__(
        record, "currency", _text(record.currency, field_name="currency")
    )
    object.__setattr__(
        record,
        "environment",
        _enum_of(CapitalEnvironment, record.environment, field_name="environment"),
    )
    object.__setattr__(
        record,
        "accounting_state",
        _enum_of(
            CapitalAccountingState, record.accounting_state, field_name="accounting_state"
        ),
    )
    cash = record.cash
    if not isinstance(cash, CashPartitionLedger):
        raise CapitalIntelligenceError("cash must be a CashPartitionLedger")
    marks = record.marks
    if not isinstance(marks, CapitalMarkState):
        raise CapitalIntelligenceError("marks must be a CapitalMarkState")
    positions = _unique_records(
        record.positions,
        field_name="positions",
        kind=CapitalPosition,
        key=lambda row: row.symbol,
        key_name="symbol",
    )
    for row in positions:
        if row.marked_at != marks.marked_at:
            raise CapitalIntelligenceError(
                "every position mark instant must be the declared mark instant: a "
                "position marked at another instant describes another observation"
            )
    declared_marks = sum((row.marked_value for row in positions), Decimal("0.00"))
    if declared_marks != marks.marked_position_value:
        raise CapitalIntelligenceError(
            "the declared marks do not reconcile to the declared positions: "
            f"{_money_text(marks.marked_position_value)} declared, "
            f"{_money_text(declared_marks)} declared by the positions"
        )
    object.__setattr__(record, "positions", positions)
    object.__setattr__(
        record,
        "sleeve_usage",
        _unique_records(
            record.sleeve_usage,
            field_name="sleeve_usage",
            kind=SleeveUsage,
            key=lambda row: row.sleeve,
        ),
    )
    object.__setattr__(
        record,
        "limit_usage",
        _unique_records(
            record.limit_usage,
            field_name="limit_usage",
            kind=CapitalLimitUsage,
            key=lambda row: row.kind,
        ),
    )
    fee_state = record.fee_state
    if fee_state is not None and not isinstance(fee_state, CapitalFeeState):
        raise CapitalIntelligenceError(
            "fee_state must be a CapitalFeeState or an explicit absence"
        )
    if not isinstance(record.source_watermark, CapitalSourceWatermark):
        raise CapitalIntelligenceError(
            "source_watermark must be a CapitalSourceWatermark"
        )
    if not isinstance(record.reconciliation, CapitalReconciliation):
        raise CapitalIntelligenceError("reconciliation must be a CapitalReconciliation")
    if not isinstance(record.freshness, CapitalFreshness):
        raise CapitalIntelligenceError("freshness must be a CapitalFreshness")
    references: list[str] = []
    applied = record.applied_reservation_references
    if isinstance(applied, (str, bytes)) or not isinstance(applied, Iterable):
        raise CapitalIntelligenceError(
            "applied_reservation_references must be a sequence of plan references"
        )
    for reference in applied:
        token = _text(reference, field_name="applied_reservation_references")
        if token in references:
            raise CapitalIntelligenceError(
                f"applied_reservation_references must not repeat {token}"
            )
        references.append(token)
    object.__setattr__(record, "applied_reservation_references", tuple(references))
    object.__setattr__(
        record,
        "recompute_trigger",
        None
        if record.recompute_trigger is None
        else _require_supported_trigger(record.recompute_trigger),
    )
    object.__setattr__(
        record,
        "derived_from",
        None
        if record.derived_from is None
        else _text(record.derived_from, field_name="derived_from"),
    )
    schema = _text(record.snapshot_schema_version, field_name="snapshot_schema_version")
    if schema != CAPITAL_SNAPSHOT_SCHEMA_VERSION:
        raise CapitalIntelligenceError(
            f"snapshot_schema_version must be the declared {CAPITAL_SNAPSHOT_SCHEMA_VERSION}"
        )
    if record.authoritative is not False:
        raise CapitalIntelligenceError(
            "a capital snapshot is research evidence and is never authoritative"
        )
    payload = {
        key: value
        for key, value in record.to_dict().items()
        if key != "snapshot_id"
    }
    return stable_hash(CAPITAL_SNAPSHOT_ID_PREFIX, payload)


def _bind_snapshot_provenance(record: "CapitalSnapshot") -> None:
    """Check a declared predecessor claim against the witnessed lineage.

    A recompute must say which record it re-derived, that record must be the
    *immediate* predecessor, it must describe the same account, environment,
    engine and currency, and the new observation must be strictly newer. A MARK
    recompute may not move cash at all, and must actually re-mark.
    """

    witness = _predecessor_witness(record)
    if witness is None:
        return
    trigger = record.recompute_trigger or CapitalRecomputeTrigger.APPROVAL
    expected_version = next_portfolio_version(
        witness["portfolio_version"], trigger=trigger
    )
    if record.portfolio_version != expected_version:
        raise CapitalIntelligenceError(
            "derived_from must reference the immediate predecessor: the portfolio "
            "version lineage must be exactly "
            f"{expected_version}, not {record.portfolio_version}"
        )
    for identity_field in ("account", "environment", "engine", "currency"):
        if getattr(record, identity_field) != witness[identity_field]:
            raise CapitalIntelligenceError(
                f"capital identity drift: {identity_field} changed across a recompute"
            )
    if record.freshness.as_of <= witness["as_of"]:
        raise CapitalIntelligenceError(
            "a recompute must observe the account at an as_of strictly newer than its "
            "predecessor's as_of; a rewound observation describes the predecessor"
        )
    if trigger is CapitalRecomputeTrigger.MARK:
        if record.cash.to_dict() != witness["cash"]:
            raise CapitalIntelligenceError(
                "a MARK recompute preserves the cash partitions: a mark may not move cash"
            )
        if (record.marks.marked_at, record.marks.marked_position_value) == witness["marks"]:
            raise CapitalIntelligenceError(
                "a MARK recompute requires a newer declared mark observation"
            )


@dataclass(frozen=True)
class CapitalSnapshot:
    """The one canonical capital record of `SC-06` at one committed version.

    One record per portfolio version (`CI-01`): the cash partitions reconcile to
    the declared cash total, deployed basis and declared marked value sit outside
    cash, equity is cash plus that declared marked value, and every recompute
    declares why it happened and which record it re-derived (`CI-03`).

    SHADOW / NON-AUTHORITATIVE research evidence: `authoritative` is hard-coded
    `False`, and holding this record grants no sizing, allocation, order or
    execution authority to any reader.
    """

    portfolio_version: str
    account: str
    environment: CapitalEnvironment
    engine: str
    currency: str
    accounting_state: CapitalAccountingState
    cash: CashPartitionLedger
    positions: tuple[CapitalPosition, ...]
    marks: CapitalMarkState
    sleeve_usage: tuple[SleeveUsage, ...]
    limit_usage: tuple[CapitalLimitUsage, ...]
    fee_state: CapitalFeeState | None
    source_watermark: CapitalSourceWatermark
    reconciliation: CapitalReconciliation
    freshness: CapitalFreshness
    derived_from: str | None = None
    recompute_trigger: CapitalRecomputeTrigger | None = None
    applied_reservation_references: tuple[str, ...] = ()
    snapshot_schema_version: str = CAPITAL_SNAPSHOT_SCHEMA_VERSION
    snapshot_id: str = ""
    authoritative: bool = False

    def __post_init__(self) -> None:
        expected_id = _freeze_capital_snapshot(self)
        _bind_snapshot_provenance(self)
        if self.snapshot_id == "":
            object.__setattr__(self, "snapshot_id", expected_id)
        elif self.snapshot_id != expected_id:
            raise CapitalIntelligenceError(
                "snapshot_id does not match its content; a forged capital snapshot "
                "fails closed"
            )
        _record_witness(self)

    @property
    def free_cash(self) -> Decimal:
        return self.cash.free_cash

    @property
    def cash_total(self) -> Decimal:
        return self.cash.cash_total

    @property
    def liabilities_total(self) -> Decimal:
        """Always zero: this engine declares no liability or receivable surface."""

        return Decimal("0.00")

    @property
    def deployed_basis_capital(self) -> Decimal:
        """Historic basis of the declared positions, never available cash."""

        return sum((row.basis_capital for row in self.positions), Decimal("0.00"))

    @property
    def marked_position_value(self) -> Decimal:
        return self.marks.marked_position_value

    @property
    def equity(self) -> Decimal:
        """Cash plus the declared marked position value (`CI-01`).

        The declared liability/receivable surfaces are both unsupported here, so
        they contribute exactly zero rather than an estimated adjustment.
        """

        return self.cash.cash_total + self.marks.marked_position_value + self.liabilities_total

    @property
    def has_negative_amount(self) -> bool:
        return self.cash.has_negative_amount

    @property
    def declared_limit_usage(self) -> Mapping[CapitalLimitKind, Decimal]:
        return MappingProxyType(
            {row.kind: row.used_capital for row in self.limit_usage}
        )

    @property
    def declared_sleeve_usage(self) -> Mapping[str, Decimal]:
        return MappingProxyType(
            {row.sleeve: row.used_capital for row in self.sleeve_usage}
        )

    def to_dict(self) -> dict[str, Any]:
        """The durable payload: every frozen `SC-06` record key, and no authority."""

        return {
            "snapshot_schema_version": self.snapshot_schema_version,
            "portfolio_version": self.portfolio_version,
            "account": self.account,
            "environment": self.environment.value,
            "engine": self.engine,
            "currency": self.currency,
            "accounting_state": self.accounting_state.value,
            "cash": self.cash.to_dict(),
            "positions": [row.to_dict() for row in self.positions],
            "liabilities": [],
            "liabilities_supported": CAPITAL_LIABILITIES_SUPPORTED,
            "receivables_supported": CAPITAL_RECEIVABLES_SUPPORTED,
            "marks": self.marks.to_dict(),
            "sleeve_usage": [row.to_dict() for row in self.sleeve_usage],
            "limit_usage": [row.to_dict() for row in self.limit_usage],
            "fee_state": None if self.fee_state is None else self.fee_state.to_dict(),
            "source_watermark": self.source_watermark.to_dict(),
            "reconciliation": self.reconciliation.to_dict(),
            "freshness": self.freshness.to_dict(),
            "derived_from": self.derived_from,
            "recompute_trigger": (
                None
                if self.recompute_trigger is None
                else self.recompute_trigger.value
            ),
            "applied_reservation_references": list(
                self.applied_reservation_references
            ),
            "snapshot_id": self.snapshot_id,
            "authoritative": self.authoritative,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalSnapshot":
        """Rebuild a snapshot from its durable payload, refusing any tampering.

        The key set, the declared unsupported surfaces, the record's identity and
        every nested record are all re-validated, so a payload that was edited by
        hand fails closed instead of being loaded as evidence.
        """

        body = _durable_body(
            raw, field_name="capital snapshot", expected_keys=_SNAPSHOT_DURABLE_KEYS
        )
        if list(body["liabilities"]) != []:
            raise CapitalIntelligenceError(
                "this engine declares no liability surface: a capital snapshot "
                "carries no liabilities"
            )
        if body["liabilities_supported"] is not CAPITAL_LIABILITIES_SUPPORTED:
            raise CapitalIntelligenceError(
                "liabilities_supported must be the declared False: this engine "
                "declares no liability surface"
            )
        if body["receivables_supported"] is not CAPITAL_RECEIVABLES_SUPPORTED:
            raise CapitalIntelligenceError(
                "receivables_supported must be the declared False: this engine "
                "declares no receivable surface"
            )
        positions = _records(
            body["positions"], field_name="positions", kind=Mapping
        )
        sleeves = _records(
            body["sleeve_usage"], field_name="sleeve_usage", kind=Mapping
        )
        limits = _records(
            body["limit_usage"], field_name="limit_usage", kind=Mapping
        )
        applied = _records(
            body["applied_reservation_references"],
            field_name="applied_reservation_references",
            kind=str,
        )
        stored_trigger = body["recompute_trigger"]
        trigger = (
            None
            if stored_trigger is None
            else _require_supported_trigger(
                _enum_from_token(
                    CapitalRecomputeTrigger,
                    stored_trigger,
                    field_name="recompute_trigger",
                )
            )
        )
        stored_fee_state = body["fee_state"]
        return cls(
            snapshot_schema_version=body["snapshot_schema_version"],
            portfolio_version=body["portfolio_version"],
            account=body["account"],
            environment=_enum_from_token(
                CapitalEnvironment, body["environment"], field_name="environment"
            ),
            engine=body["engine"],
            currency=body["currency"],
            accounting_state=_enum_from_token(
                CapitalAccountingState,
                body["accounting_state"],
                field_name="accounting_state",
            ),
            cash=CashPartitionLedger.from_dict(body["cash"]),
            positions=tuple(CapitalPosition.from_dict(row) for row in positions),
            marks=CapitalMarkState.from_dict(body["marks"]),
            sleeve_usage=tuple(SleeveUsage.from_dict(row) for row in sleeves),
            limit_usage=tuple(CapitalLimitUsage.from_dict(row) for row in limits),
            fee_state=(
                None
                if stored_fee_state is None
                else CapitalFeeState.from_dict(stored_fee_state)
            ),
            source_watermark=CapitalSourceWatermark.from_dict(body["source_watermark"]),
            reconciliation=CapitalReconciliation.from_dict(body["reconciliation"]),
            freshness=CapitalFreshness.from_dict(body["freshness"]),
            derived_from=body["derived_from"],
            recompute_trigger=trigger,
            applied_reservation_references=applied,
            snapshot_id=body["snapshot_id"],
            authoritative=body["authoritative"],
        )


_SNAPSHOT_DURABLE_KEYS: tuple[str, ...] = (
    "snapshot_schema_version",
    "portfolio_version",
    "account",
    "environment",
    "engine",
    "currency",
    "accounting_state",
    "cash",
    "positions",
    "liabilities",
    "liabilities_supported",
    "receivables_supported",
    "marks",
    "sleeve_usage",
    "limit_usage",
    "fee_state",
    "source_watermark",
    "reconciliation",
    "freshness",
    "derived_from",
    "recompute_trigger",
    "applied_reservation_references",
    "snapshot_id",
    "authoritative",
)


def capital_snapshot_identity(record: "CapitalSnapshot") -> str:
    """Return the deterministic identity of one capital snapshot.

    The identity is a pure function of the record's declared content: no clock,
    retry count, process identity or database sequence takes part, so re-deriving
    the same evidence reproduces the same ``CPSN:`` id and an edited or forged
    record produces a different one.
    """

    if not isinstance(record, CapitalSnapshot):
        raise CapitalIntelligenceError(
            "a capital snapshot identity can only be derived from a CapitalSnapshot"
        )
    payload = {
        key: value for key, value in record.to_dict().items() if key != "snapshot_id"
    }
    return stable_hash(CAPITAL_SNAPSHOT_ID_PREFIX, payload)


def assert_snapshot_recomputes_from(
    previous: "CapitalSnapshot",
    renewed: "CapitalSnapshot",
    *,
    trigger: CapitalRecomputeTrigger,
) -> None:
    """Fail closed unless ``renewed`` is a lawful recompute of ``previous``.

    The declared reason decides what may have moved: the version must be exactly
    the next one, the identity may not drift, the observation must be strictly
    newer, and the cash book must obey the declared cash law of that reason. A
    recompute whose declared reason is reconciliation is additionally refused
    unless it is reconciled, because a recompute that says it reconciled and then
    reports an unreconciled state has not done what it claims.
    """

    if not isinstance(previous, CapitalSnapshot) or not isinstance(
        renewed, CapitalSnapshot
    ):
        raise CapitalIntelligenceError("a recompute compares two capital snapshots")
    expected = _require_supported_trigger(trigger)
    if renewed.recompute_trigger is not expected:
        raise CapitalIntelligenceError(
            "a recompute must declare the trigger it is asserted with: expected "
            f"{expected.value}, record declares "
            f"{None if renewed.recompute_trigger is None else renewed.recompute_trigger.value}"
        )
    if renewed.derived_from != previous.snapshot_id:
        raise CapitalIntelligenceError(
            "derived_from must reference the exact predecessor snapshot id"
        )
    assert_portfolio_version_lineage(
        previous.portfolio_version, renewed.portfolio_version, trigger=expected
    )
    for identity_field in ("account", "environment", "engine", "currency"):
        if getattr(renewed, identity_field) != getattr(previous, identity_field):
            raise CapitalIntelligenceError(
                f"capital identity drift: {identity_field} changed across a recompute"
            )
    if renewed.freshness.as_of <= previous.freshness.as_of:
        raise CapitalIntelligenceError(
            "a recompute must observe the account at an as_of strictly newer than "
            "its predecessor's as_of"
        )
    law = CAPITAL_RECOMPUTE_CASH_LAWS[expected]
    if law is CapitalCashLaw.PRESERVE_PARTITIONS:
        if renewed.cash.to_dict() != previous.cash.to_dict():
            raise CapitalIntelligenceError(
                "a recompute under a PRESERVE_PARTITIONS cash law may not move the "
                "cash partitions"
            )
        if (
            renewed.marks.marked_at,
            renewed.marks.marked_position_value,
        ) == (previous.marks.marked_at, previous.marks.marked_position_value):
            raise CapitalIntelligenceError(
                "a recompute under a PRESERVE_PARTITIONS cash law must declare a "
                "newer mark observation"
            )
    elif law is CapitalCashLaw.PRESERVE_TOTAL:
        if renewed.cash.cash_total != previous.cash.cash_total:
            raise CapitalIntelligenceError(
                "a recompute under a PRESERVE_TOTAL cash law may not move the cash "
                f"total: {previous.cash.cash_total} -> {renewed.cash.cash_total}"
            )
    else:
        if renewed.cash.cash_total > previous.cash.cash_total:
            raise CapitalIntelligenceError(
                "a recompute under a MAY_ONLY_DECREASE cash law may not increase the "
                "cash total"
            )
        if renewed.fee_state is None or previous.fee_state is None:
            raise CapitalIntelligenceError(
                "a recompute that charges fees must declare the fee state on both "
                "the predecessor and the successor"
            )
        movement = renewed.fee_state.fees_settled - previous.fee_state.fees_settled
        decrease = previous.cash.cash_total - renewed.cash.cash_total
        if movement <= 0 or decrease != movement:
            raise CapitalIntelligenceError(
                "a recompute that charges fees must decrease the cash total by "
                "exactly its declared fee movement: declared "
                f"{_money_text(movement)}, observed {_money_text(decrease)}"
            )
        if (
            renewed.source_watermark.ledger_sequence
            <= previous.source_watermark.ledger_sequence
        ):
            raise CapitalIntelligenceError(
                "a fee recompute must advance the source watermark"
            )
        if (
            renewed.fee_state.fee_watermark.ledger_sequence
            <= previous.fee_state.fee_watermark.ledger_sequence
        ):
            raise CapitalIntelligenceError(
                "a fee recompute must advance the fee watermark"
            )
    if (
        expected is CapitalRecomputeTrigger.RECONCILIATION
        and renewed.reconciliation.status is not CapitalReconciliationStatus.RECONCILED
    ):
        raise CapitalIntelligenceError(
            "a reconciliation recompute must report a reconciled capital state"
        )


@dataclass(frozen=True)
class CapitalDeployablePolicy:
    """The declared identity and caps one deployable evaluation runs against.

    It ships no number: `OQ-06` (sleeve caps and hard versus soft constraint
    behaviour) is an owner input, so an absent cap stays absent and the evaluator
    abstains on it instead of reading it as unlimited. The account, environment,
    engine and currency are *bound* explicitly, because an unbound policy could
    otherwise be applied to whatever account happened to be at hand.
    """

    policy_version: str
    account: str | None = None
    environment: CapitalEnvironment | None = None
    engine: str | None = None
    currency: str | None = None
    limit_caps: Mapping[CapitalLimitKind, Decimal] = field(default_factory=dict)
    sleeve_limits: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_version",
            _require_policy_version(
                self.policy_version,
                field_name="policy_version",
                declared=DECLARED_CAPITAL_POLICY_VERSIONS,
            ),
        )
        for bound_field in ("account", "engine", "currency"):
            value = getattr(self, bound_field)
            if value is not None:
                object.__setattr__(
                    self, bound_field, _text(value, field_name=bound_field)
                )
        if self.environment is not None:
            object.__setattr__(
                self,
                "environment",
                _enum_of(CapitalEnvironment, self.environment, field_name="environment"),
            )
        object.__setattr__(self, "limit_caps", MappingProxyType(self._freeze_caps()))
        object.__setattr__(
            self, "sleeve_limits", MappingProxyType(self._freeze_sleeve_limits())
        )

    def _freeze_caps(self) -> dict[CapitalLimitKind, Decimal]:
        raw = self.limit_caps
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise CapitalIntelligenceError(
                "limit_caps must be a mapping of declared limit kinds to caps"
            )
        caps: dict[CapitalLimitKind, Decimal] = {}
        for key, value in raw.items():
            member = _enum_of(CapitalLimitKind, key, field_name="limit_caps key")
            amount = _money(value, field_name=f"limit_caps[{member.value}]")
            if amount < 0:
                raise CapitalIntelligenceError("limit_caps must not be negative")
            caps[member] = amount
        return caps

    def _freeze_sleeve_limits(self) -> dict[str, Decimal]:
        raw = self.sleeve_limits
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise CapitalIntelligenceError(
                "sleeve_limits must be a mapping of sleeve names to caps"
            )
        limits: dict[str, Decimal] = {}
        for key, value in raw.items():
            name = _text(key, field_name="sleeve_limits key")
            amount = _money(value, field_name=f"sleeve_limits[{name}]")
            if amount < 0:
                raise CapitalIntelligenceError("sleeve_limits must not be negative")
            limits[name] = amount
        return limits

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "account": self.account,
            "environment": (
                None if self.environment is None else self.environment.value
            ),
            "engine": self.engine,
            "currency": self.currency,
            "limit_caps": {
                member.value: _money_text(amount)
                for member, amount in self.limit_caps.items()
            },
            "sleeve_limits": {
                name: _money_text(amount) for name, amount in self.sleeve_limits.items()
            },
        }


def capital_deployable_engineering_scenario_policy(
    *,
    account: str | None = None,
    environment: CapitalEnvironment | None = None,
    engine: str | None = None,
    currency: str | None = None,
    limit_caps: Mapping[CapitalLimitKind, Any] | None = None,
    sleeve_limits: Mapping[str, Any] | None = None,
) -> CapitalDeployablePolicy:
    """The R5-C engineering-scenario deployable-capital policy.

    Every cap is caller-declared and defaults to *nothing at all*. The shipped
    policy is therefore an abstaining policy: `OQ-06` is an owner decision, and
    inventing a sleeve cap or a limit number here would ratify a number the owner
    has not ratified.
    """

    return CapitalDeployablePolicy(
        policy_version=CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        account=account,
        environment=environment,
        engine=engine,
        currency=currency,
        limit_caps=limit_caps or {},
        sleeve_limits=sleeve_limits or {},
    )


@dataclass(frozen=True)
class CapitalDeployableView:
    """The outcome of one deployable-capital evaluation.

    An abstention publishes *nothing*: no number, no bound, no binding bound and
    no over-limit list. That is the whole point - a reader cannot mistake an
    abstention for a small number, and cannot quote one as if it were evidence.
    """

    status: CapitalDeployableStatus
    abstention_reason: CapitalDeployableAbstentionReason | None
    missing_policy_inputs: tuple[str, ...]
    deployable_capital: Decimal | None
    bounds: Mapping[CapitalBound, Decimal]
    binding_bound: CapitalBound | None
    binding_sleeve: str | None
    over_limit_bounds: tuple[CapitalBound, ...]
    free_cash: Decimal | None
    currency: str | None
    account: str | None
    policy_version: str
    snapshot_id: str | None
    evaluated_at: datetime
    authoritative: bool = False

    def __post_init__(self) -> None:
        status = _enum_of(CapitalDeployableStatus, self.status, field_name="status")
        reason = _optional_enum_of(
            CapitalDeployableAbstentionReason,
            self.abstention_reason,
            field_name="abstention_reason",
        )
        missing = _records(
            self.missing_policy_inputs,
            field_name="missing_policy_inputs",
            kind=str,
        )
        published = (
            None
            if self.deployable_capital is None
            else _money(self.deployable_capital, field_name="deployable_capital")
        )
        raw_bounds = self.bounds
        if not isinstance(raw_bounds, Mapping):
            raise CapitalIntelligenceError("bounds must be a mapping of declared bounds")
        bounds: dict[CapitalBound, Decimal] = {}
        for key, value in raw_bounds.items():
            member = _enum_of(CapitalBound, key, field_name="bounds key")
            amount = _money(value, field_name=f"bounds[{member.value}]")
            if amount < 0:
                raise CapitalIntelligenceError("a published bound must not be negative")
            bounds[member] = amount
        binding = _optional_enum_of(
            CapitalBound, self.binding_bound, field_name="binding_bound"
        )
        over = _records(
            self.over_limit_bounds, field_name="over_limit_bounds", kind=CapitalBound
        )
        evaluated = _utc(self.evaluated_at, field_name="evaluated_at")
        if self.authoritative is not False:
            raise CapitalIntelligenceError(
                "a deployable-capital view is research evidence and is never authoritative"
            )
        if status is CapitalDeployableStatus.AVAILABLE:
            if reason is not None:
                raise CapitalIntelligenceError(
                    "an available evaluation publishes no abstention reason"
                )
            if published is None or not bounds or binding is None:
                raise CapitalIntelligenceError(
                    "an available evaluation must publish its number, its bounds and "
                    "its binding bound"
                )
        else:
            if reason is None:
                raise CapitalIntelligenceError(
                    "an abstention must publish the reason it abstained"
                )
            if published is not None or bounds or binding is not None:
                raise CapitalIntelligenceError(
                    "an abstention publishes no number, no bound and no binding bound"
                )
            if self.binding_sleeve is not None or over:
                raise CapitalIntelligenceError(
                    "an abstention publishes no binding sleeve and no over-limit bound"
                )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "abstention_reason", reason)
        object.__setattr__(self, "missing_policy_inputs", missing)
        object.__setattr__(self, "deployable_capital", published)
        object.__setattr__(
            self,
            "bounds",
            MappingProxyType({member: bounds[member] for member in CapitalBound if member in bounds}),
        )
        object.__setattr__(self, "binding_bound", binding)
        object.__setattr__(self, "over_limit_bounds", over)
        object.__setattr__(
            self,
            "free_cash",
            None if self.free_cash is None else _money(self.free_cash, field_name="free_cash"),
        )
        object.__setattr__(self, "evaluated_at", evaluated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "abstention_reason": (
                None if self.abstention_reason is None else self.abstention_reason.value
            ),
            "missing_policy_inputs": list(self.missing_policy_inputs),
            "deployable_capital": (
                None
                if self.deployable_capital is None
                else _money_text(self.deployable_capital)
            ),
            "bounds": {
                member.value: _money_text(amount)
                for member, amount in self.bounds.items()
            },
            "binding_bound": (
                None if self.binding_bound is None else self.binding_bound.value
            ),
            "binding_sleeve": self.binding_sleeve,
            "over_limit_bounds": [member.value for member in self.over_limit_bounds],
            "free_cash": None if self.free_cash is None else _money_text(self.free_cash),
            "currency": self.currency,
            "account": self.account,
            "policy_version": self.policy_version,
            "snapshot_id": self.snapshot_id,
            "evaluated_at": iso_z(self.evaluated_at, field_name="evaluated_at"),
            "authoritative": self.authoritative,
        }


#: The bound each declared limit family is measured against. A limit family is
#: never silently mapped onto another bound.
_BOUND_FOR_LIMIT: Mapping[CapitalLimitKind, CapitalBound] = MappingProxyType(
    {
        CapitalLimitKind.RISK: CapitalBound.RISK,
        CapitalLimitKind.CONCENTRATION: CapitalBound.CONCENTRATION,
        CapitalLimitKind.LIQUIDITY: CapitalBound.LIQUIDITY,
    }
)


def evaluate_deployable_capital(
    record: "CapitalSnapshot",
    *,
    policy: CapitalDeployablePolicy,
    at_time: datetime,
) -> CapitalDeployableView:
    """Return deployable capital as the tightest declared remaining bound (`CI-02`).

    "Deployable capital is bounded by free_cash and the remaining approved risk,
    concentration, liquidity and horizon-sleeve limits." The result is therefore
    a *minimum*, never a sum, and it is never larger than free cash. Every
    condition the clause lists as blocking - an unbound or mismatched identity, a
    stale account state, an unsupported accounting state, a failed or skipped
    reconciliation, an unresolved negative balance, missing fees, a currency
    mismatch, an outlived validity limit, incomplete evidence, or a limit that has
    usage but no configured cap - abstains in the declared precedence order and
    publishes no number.
    """

    if not isinstance(record, CapitalSnapshot):
        raise CapitalIntelligenceError(
            "deployable capital is evaluated on a CapitalSnapshot"
        )
    if not isinstance(policy, CapitalDeployablePolicy):
        raise CapitalIntelligenceError(
            "a deployable evaluation requires a CapitalDeployablePolicy"
        )
    evaluated_at = _utc(at_time, field_name="at_time")
    reason = CapitalDeployableAbstentionReason

    def abstain(
        abstention_reason: CapitalDeployableAbstentionReason,
        missing: tuple[str, ...] = (),
    ) -> CapitalDeployableView:
        return CapitalDeployableView(
            status=CapitalDeployableStatus.ABSTAINED,
            abstention_reason=abstention_reason,
            missing_policy_inputs=missing,
            deployable_capital=None,
            bounds={},
            binding_bound=None,
            binding_sleeve=None,
            over_limit_bounds=(),
            free_cash=record.cash.free_cash,
            currency=record.currency,
            account=record.account,
            policy_version=policy.policy_version,
            snapshot_id=record.snapshot_id,
            evaluated_at=evaluated_at,
        )

    unbound = tuple(
        name
        for name in ("account", "environment", "engine", "currency")
        if getattr(policy, name) is None
    )
    if unbound:
        return abstain(reason.IDENTITY_NOT_BOUND, unbound)
    if policy.currency != record.currency:
        return abstain(reason.CURRENCY_MISMATCH)
    if record.environment is CapitalEnvironment.LIVE_OBSERVATION:
        return abstain(reason.LIVE_OBSERVATION_CANNOT_FUND_PAPER_ACCOUNT)
    if (
        policy.account != record.account
        or policy.engine != record.engine
        or policy.environment is not record.environment
    ):
        return abstain(reason.IDENTITY_MISMATCH)
    if record.accounting_state is CapitalAccountingState.UNSUPPORTED:
        return abstain(reason.UNSUPPORTED_ACCOUNTING_STATE)
    if record.source_watermark.state_is_stale is not False:
        return abstain(reason.ACCOUNT_STATE_STALE)
    if record.reconciliation.status is CapitalReconciliationStatus.FAILED:
        return abstain(reason.FAILED_RECONCILIATION)
    if record.reconciliation.status is CapitalReconciliationStatus.NOT_PERFORMED:
        return abstain(reason.RECONCILIATION_NOT_PERFORMED)
    if record.cash.has_negative_amount:
        return abstain(reason.UNRESOLVED_NEGATIVE_BALANCE)
    if record.fee_state is None:
        return abstain(reason.MISSING_FEES)
    if evaluated_at > record.freshness.validity_deadline:
        return abstain(reason.SNAPSHOT_OUTLIVED_VALIDITY)
    if not record.freshness.evidence_complete:
        return abstain(reason.EVIDENCE_INCOMPLETE)

    usage = dict(record.declared_limit_usage)
    missing_limits = tuple(
        member.value
        for member in CapitalLimitKind
        if member in usage and member not in policy.limit_caps
    )
    if missing_limits:
        return abstain(reason.LIMIT_NOT_CONFIGURED, missing_limits)
    sleeve_usage = dict(record.declared_sleeve_usage)
    missing_sleeves = tuple(
        name for name in sleeve_usage if name not in policy.sleeve_limits
    )
    if missing_sleeves:
        return abstain(reason.SLEEVE_LIMIT_NOT_CONFIGURED, missing_sleeves)

    aggregate_usage = sum(usage.values(), Decimal("0.00"))
    bounds: dict[CapitalBound, Decimal] = {
        CapitalBound.FREE_CASH: record.cash.free_cash
    }
    over: list[CapitalBound] = []
    for member in CapitalLimitKind:
        cap = policy.limit_caps.get(member)
        if cap is None:
            # Neither a declared cap nor declared usage: the bound is simply not
            # evaluated rather than assumed unlimited and published as a limit.
            continue
        used = usage.get(member, aggregate_usage)
        bound = cap - used
        if bound < 0:
            over.append(_BOUND_FOR_LIMIT[member])
            bound = Decimal("0.00")
        bounds[_BOUND_FOR_LIMIT[member]] = bound
    tightest_sleeve: str | None = None
    if policy.sleeve_limits:
        tightest_value: Decimal | None = None
        for name in policy.sleeve_limits:
            bound = policy.sleeve_limits[name] - sleeve_usage.get(
                name, Decimal("0.00")
            )
            if bound < 0:
                if CapitalBound.SLEEVE not in over:
                    over.append(CapitalBound.SLEEVE)
                bound = Decimal("0.00")
            if tightest_value is None or bound < tightest_value:
                tightest_value, tightest_sleeve = bound, name
        bounds[CapitalBound.SLEEVE] = tightest_value

    deployable = min(bounds.values())
    binding = next(
        member
        for member in CAPITAL_BOUND_PRECEDENCE
        if bounds.get(member) == deployable
    )
    return CapitalDeployableView(
        status=CapitalDeployableStatus.AVAILABLE,
        abstention_reason=None,
        missing_policy_inputs=(),
        deployable_capital=deployable,
        bounds=bounds,
        binding_bound=binding,
        binding_sleeve=tightest_sleeve if binding is CapitalBound.SLEEVE else None,
        over_limit_bounds=tuple(
            member for member in CAPITAL_BOUND_PRECEDENCE if member in over
        ),
        free_cash=record.cash.free_cash,
        currency=record.currency,
        account=record.account,
        policy_version=policy.policy_version,
        snapshot_id=record.snapshot_id,
        evaluated_at=evaluated_at,
    )


@dataclass(frozen=True)
class CapitalCommitResult:
    """The outcome of one compare-and-commit reservation attempt (`CI-04`).

    The record is an *event* record, not a content record: a rejected attempt
    carries the successor-free shape (no snapshot, no version, no capital), and
    an idempotent replay carries the very record the original commit produced, so
    a retry can never look like a second consumption.
    """

    status: CapitalCommitStatus
    rejection: CapitalCommitRejection | None
    commit_id: str
    plan_id: str
    applied_portfolio_version: str
    successor_version: str | None
    committed_capital: Decimal | None
    snapshot: "CapitalSnapshot | None"
    snapshot_id: str | None
    applied_reservation_references: tuple[str, ...]
    at_time: datetime
    authoritative: bool = False

    def __post_init__(self) -> None:
        status = _enum_of(CapitalCommitStatus, self.status, field_name="status")
        rejection = _optional_enum_of(
            CapitalCommitRejection, self.rejection, field_name="rejection"
        )
        commit_id = _text(self.commit_id, field_name="commit_id")
        if not commit_id.startswith(f"{CAPITAL_COMMIT_ID_PREFIX}:"):
            raise CapitalIntelligenceError(
                f"commit_id must carry the declared {CAPITAL_COMMIT_ID_PREFIX}: prefix"
            )
        plan_id = _text(self.plan_id, field_name="plan_id")
        applied = _text(
            self.applied_portfolio_version, field_name="applied_portfolio_version"
        )
        successor = (
            None
            if self.successor_version is None
            else _text(self.successor_version, field_name="successor_version")
        )
        committed = (
            None
            if self.committed_capital is None
            else _money(self.committed_capital, field_name="committed_capital")
        )
        references = _records(
            self.applied_reservation_references,
            field_name="applied_reservation_references",
            kind=str,
        )
        moment = _utc(self.at_time, field_name="at_time")
        if self.snapshot is not None and not isinstance(self.snapshot, CapitalSnapshot):
            raise CapitalIntelligenceError("snapshot must be a CapitalSnapshot or None")
        if self.authoritative is not False:
            raise CapitalIntelligenceError(
                "a reservation commit is research evidence and is never authoritative"
            )
        if status is CapitalCommitStatus.REJECTED:
            if rejection is None:
                raise CapitalIntelligenceError("a rejected commit names its rejection")
            if (
                self.snapshot is not None
                or successor is not None
                or committed is not None
                or self.snapshot_id is not None
            ):
                raise CapitalIntelligenceError(
                    "a rejected commit changes nothing: it carries no snapshot, no "
                    "successor version and no consumed capital"
                )
        else:
            if rejection is not None:
                raise CapitalIntelligenceError(
                    "a committed or replayed commit is not a rejection"
                )
            if self.snapshot is None or successor is None or committed is None:
                raise CapitalIntelligenceError(
                    "a committed or replayed commit carries its record, its version "
                    "and the capital it consumed"
                )
            if self.snapshot_id != self.snapshot.snapshot_id:
                raise CapitalIntelligenceError(
                    "a commit's snapshot_id must be the identity of the snapshot it "
                    "reports"
                )
            if self.snapshot.portfolio_version != successor:
                raise CapitalIntelligenceError(
                    "a commit's successor version must be the version of the snapshot "
                    "it reports"
                )
            if committed <= 0:
                raise CapitalIntelligenceError(
                    "a committed reservation consumes a positive amount"
                )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "rejection", rejection)
        object.__setattr__(self, "commit_id", commit_id)
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "applied_portfolio_version", applied)
        object.__setattr__(self, "successor_version", successor)
        object.__setattr__(self, "committed_capital", committed)
        object.__setattr__(self, "applied_reservation_references", references)
        object.__setattr__(self, "at_time", moment)

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_schema_version": CAPITAL_COMMIT_SCHEMA_VERSION,
            "status": self.status.value,
            "rejection": None if self.rejection is None else self.rejection.value,
            "commit_id": self.commit_id,
            "plan_id": self.plan_id,
            "applied_portfolio_version": self.applied_portfolio_version,
            "successor_version": self.successor_version,
            "committed_capital": (
                None
                if self.committed_capital is None
                else _money_text(self.committed_capital)
            ),
            "snapshot_id": self.snapshot_id,
            "applied_reservation_references": list(
                self.applied_reservation_references
            ),
            "at_time": iso_z(self.at_time, field_name="at_time"),
            "authoritative": self.authoritative,
        }


def _commit_identity(
    *,
    plan_id: str,
    applied_version: str,
    successor_version: str | None,
    status: CapitalCommitStatus,
    rejection: CapitalCommitRejection | None,
    committed: Decimal | None,
) -> str:
    """Deterministic identity of one commit attempt.

    It is a function of what the attempt declared and produced, so the same
    attempt reproduces the same ``CCMT:`` id and a replay cannot be mistaken for a
    fresh consumption.
    """

    return stable_hash(
        CAPITAL_COMMIT_ID_PREFIX,
        {
            "commit_schema_version": CAPITAL_COMMIT_SCHEMA_VERSION,
            "plan_id": plan_id,
            "applied_portfolio_version": applied_version,
            "successor_version": successor_version,
            "status": status.value,
            "rejection": None if rejection is None else rejection.value,
            "committed_capital": None if committed is None else _money_text(committed),
        },
    )


def _reserved_successor(
    snapshot: "CapitalSnapshot",
    *,
    plan: PortfolioReservationPlan,
    committed: Decimal,
    at_time: datetime,
    limit_usage: tuple[CapitalLimitUsage, ...] | None,
    sleeve_usage: tuple[SleeveUsage, ...] | None,
) -> "CapitalSnapshot":
    """Build the successor snapshot of one reservation commit.

    The committed capital *moves* between partitions: free cash falls by exactly
    the committed amount and order reserves rise by exactly the same amount, so
    the declared cash total is preserved (`PRESERVE_TOTAL`). Positions, marks,
    fees, watermarks and reconciliation are carried, the validity window keeps its
    declared duration from the new observation, and the risk and sleeve usage are
    whatever the caller declared - nothing here invents a usage number.
    """

    partitions = dict(snapshot.cash.partitions)
    partitions[CapitalPartition.FREE_CASH] = (
        partitions[CapitalPartition.FREE_CASH] - committed
    )
    partitions[CapitalPartition.ORDER_RESERVES] = (
        partitions[CapitalPartition.ORDER_RESERVES] + committed
    )
    ledger = CashPartitionLedger(
        partitions=partitions, cash_total=snapshot.cash.cash_total
    )
    declared_validity = (
        snapshot.freshness.validity_deadline - snapshot.freshness.as_of
    )
    successor = CapitalSnapshot(
        portfolio_version=next_portfolio_version(
            snapshot.portfolio_version, trigger=CapitalRecomputeTrigger.RESERVATION
        ),
        account=snapshot.account,
        environment=snapshot.environment,
        engine=snapshot.engine,
        currency=snapshot.currency,
        accounting_state=snapshot.accounting_state,
        cash=ledger,
        positions=snapshot.positions,
        marks=snapshot.marks,
        sleeve_usage=(
            snapshot.sleeve_usage if sleeve_usage is None else tuple(sleeve_usage)
        ),
        limit_usage=(
            snapshot.limit_usage if limit_usage is None else tuple(limit_usage)
        ),
        fee_state=snapshot.fee_state,
        source_watermark=snapshot.source_watermark,
        reconciliation=snapshot.reconciliation,
        freshness=CapitalFreshness(
            as_of=at_time,
            validity_deadline=at_time + declared_validity,
            evidence_complete=snapshot.freshness.evidence_complete,
        ),
        derived_from=snapshot.snapshot_id,
        recompute_trigger=CapitalRecomputeTrigger.RESERVATION,
        applied_reservation_references=(
            *snapshot.applied_reservation_references,
            plan.plan_id,
        ),
    )
    # The commit is asserted against the same laws any other recompute is, so a
    # reservation cannot be a back door around the accounting contract.
    assert_snapshot_recomputes_from(
        snapshot, successor, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    return successor


def commit_reservation(
    snapshot: "CapitalSnapshot",
    *,
    plan: PortfolioReservationPlan,
    at_time: datetime,
    trigger: CapitalRecomputeTrigger = CapitalRecomputeTrigger.RESERVATION,
    expected_portfolio_version: str | None = None,
    limit_usage: tuple[CapitalLimitUsage, ...] | None = None,
    sleeve_usage: tuple[SleeveUsage, ...] | None = None,
) -> CapitalCommitResult:
    """Compare-and-commit one reservation plan against one capital snapshot (`CI-04`).

    "Reservation compares the expected portfolio version and commits capital and
    risk usage atomically. If concurrent decisions compete for the same cash, at
    most one can consume the contested capacity. The loser rereads and replans.
    Duplicate fills, retries and restart must not create or release capital
    twice."

    The whole compare-and-commit therefore runs under one lock and is
    all-or-nothing: the expected version is compared before anything moves, the
    plan must be current for that version (checked by the frozen owner's own
    ``assert_reservation_plan_current``), sufficient free cash must be present,
    and a rejection writes nothing at all. A replay of a plan this process already
    committed returns the recorded successor rather than consuming the contested
    capacity again.
    """

    if not isinstance(snapshot, CapitalSnapshot):
        raise CapitalIntelligenceError(
            "a reservation commits against a CapitalSnapshot"
        )
    if not isinstance(plan, PortfolioReservationPlan):
        raise CapitalIntelligenceError(
            "a reservation commit requires a PortfolioReservationPlan"
        )
    requested_at = _utc(at_time, field_name="at_time")
    requested_trigger = _enum_of(
        CapitalRecomputeTrigger, trigger, field_name="trigger"
    )
    declared_expected = (
        snapshot.portfolio_version
        if expected_portfolio_version is None
        else _text(
            expected_portfolio_version, field_name="expected_portfolio_version"
        )
    )

    def rejected(rejection: CapitalCommitRejection) -> CapitalCommitResult:
        return CapitalCommitResult(
            status=CapitalCommitStatus.REJECTED,
            rejection=rejection,
            commit_id=_commit_identity(
                plan_id=plan.plan_id,
                applied_version=snapshot.portfolio_version,
                successor_version=None,
                status=CapitalCommitStatus.REJECTED,
                rejection=rejection,
                committed=None,
            ),
            plan_id=plan.plan_id,
            applied_portfolio_version=snapshot.portfolio_version,
            successor_version=None,
            committed_capital=None,
            snapshot=None,
            snapshot_id=None,
            applied_reservation_references=snapshot.applied_reservation_references,
            at_time=requested_at,
        )

    if requested_trigger is not CapitalRecomputeTrigger.RESERVATION:
        return rejected(CapitalCommitRejection.UNSUPPORTED_RECOMPUTE_TRIGGER)
    if snapshot.environment is not CapitalEnvironment.PAPER_LEDGER:
        return rejected(CapitalCommitRejection.ENVIRONMENT_NOT_FUNDABLE)
    if declared_expected != snapshot.portfolio_version:
        return rejected(CapitalCommitRejection.STALE_EXPECTED_VERSION)
    committed = _money_from_float(
        plan.planned_capital, field_name="plan.planned_capital"
    )
    if committed <= 0:
        return rejected(CapitalCommitRejection.NON_POSITIVE_AMOUNT)
    if requested_at <= snapshot.freshness.as_of:
        raise CapitalIntelligenceError(
            "a reservation commit must observe the account at an at_time strictly "
            "newer than the snapshot as_of"
        )
    if requested_at > snapshot.freshness.validity_deadline:
        return rejected(CapitalCommitRejection.SNAPSHOT_OUTLIVED_VALIDITY)

    holder = _capital_witness()
    identity_key = (
        snapshot.account,
        snapshot.environment.value,
        snapshot.engine,
        snapshot.currency,
    )
    with holder["lock"]:
        plan_key = (*identity_key, plan.plan_id)
        recorded = holder["plans"].get(plan_key)
        if recorded is not None:
            if (
                recorded["applied_portfolio_version"] == snapshot.portfolio_version
                and recorded["status"] is CapitalCommitStatus.COMMITTED
            ):
                return replace(
                    recorded["result"], status=CapitalCommitStatus.IDEMPOTENT_REPLAY
                )
            return rejected(CapitalCommitRejection.PLAN_ALREADY_CONSUMED)
        try:
            assert_reservation_plan_current(
                plan, current_portfolio_version=snapshot.portfolio_version
            )
        except PortfolioContractError:
            return rejected(CapitalCommitRejection.STALE_EXPECTED_VERSION)
        current = holder["current"].get(identity_key)
        if current is not None and current != snapshot.portfolio_version:
            return rejected(CapitalCommitRejection.STALE_EXPECTED_VERSION)
        if committed > snapshot.cash.free_cash:
            return rejected(CapitalCommitRejection.INSUFFICIENT_FREE_CASH)
        successor = _reserved_successor(
            snapshot,
            plan=plan,
            committed=committed,
            at_time=requested_at,
            limit_usage=limit_usage,
            sleeve_usage=sleeve_usage,
        )
        result = CapitalCommitResult(
            status=CapitalCommitStatus.COMMITTED,
            rejection=None,
            commit_id=_commit_identity(
                plan_id=plan.plan_id,
                applied_version=snapshot.portfolio_version,
                successor_version=successor.portfolio_version,
                status=CapitalCommitStatus.COMMITTED,
                rejection=None,
                committed=committed,
            ),
            plan_id=plan.plan_id,
            applied_portfolio_version=snapshot.portfolio_version,
            successor_version=successor.portfolio_version,
            committed_capital=committed,
            snapshot=successor,
            snapshot_id=successor.snapshot_id,
            applied_reservation_references=successor.applied_reservation_references,
            at_time=requested_at,
        )
        _remember(
            holder["plans"],
            plan_key,
            {
                "applied_portfolio_version": snapshot.portfolio_version,
                "status": CapitalCommitStatus.COMMITTED,
                "result": result,
            },
        )
        holder["current"][identity_key] = successor.portfolio_version
        return result


def to_portfolio_capital_state(
    snapshot: "CapitalSnapshot", *, as_of: datetime | None = None
) -> PortfolioCapitalState:
    """Hand the frozen F7 owner an explicit capital state derived from a snapshot.

    The frozen owner exposes ``PortfolioCapitalState`` with a float capital base
    and its own ``PCAP:`` fingerprint, so this seam is a *declared conversion*
    rather than a second capital family: the base is the snapshot's free cash, the
    version is the snapshot's version, and the instant defaults to the snapshot's
    ``as_of``. A non-positive base is refused here, because the frozen owner
    requires a positive base and a zero base would describe a book with nothing
    left to allocate.
    """

    if not isinstance(snapshot, CapitalSnapshot):
        raise CapitalIntelligenceError(
            "a portfolio capital state is derived from a CapitalSnapshot"
        )
    moment = (
        snapshot.freshness.as_of if as_of is None else _utc(as_of, field_name="as_of")
    )
    available = snapshot.cash.free_cash
    if available <= 0:
        raise CapitalIntelligenceError(
            "free cash must be positive before it can be offered to the frozen "
            "portfolio owner, which allocates fractions of a positive base"
        )
    return PortfolioCapitalState(
        portfolio_version=snapshot.portfolio_version,
        available_capital=float(available),
        currency=snapshot.currency,
        as_of=moment,
    )


_OCCUPANCY_POINT_KEYS: tuple[str, ...] = (
    "at",
    "allocated_basis",
    "cumulative_fees",
    "cumulative_carry",
)


@dataclass(frozen=True)
class CapitalOccupancyPoint:
    """One declared observation of committed capital at one preserved instant.

    Committed capital is "allocated position capital plus outstanding reserved
    cash, with no overlap after fills" (v1.5.0 section 12), and the caller
    declares that basis explicitly here. Nothing in this section infers a basis
    from a position list or a cash partition, because an inferred basis would
    silently become a policy input. Cumulative fees and carry are declared the same
    way, so the cost channel of the occupancy is *measured* rather than estimated.
    """

    at: datetime
    allocated_basis: Decimal
    cumulative_fees: Decimal = Decimal("0.00")
    cumulative_carry: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        moment = _utc(self.at, field_name="at")
        basis = _money(self.allocated_basis, field_name="allocated_basis")
        fees = _money(self.cumulative_fees, field_name="cumulative_fees")
        carry = _money(self.cumulative_carry, field_name="cumulative_carry")
        if basis < 0:
            raise CapitalIntelligenceError("allocated_basis must not be negative")
        if fees < 0 or carry < 0:
            raise CapitalIntelligenceError(
                "cumulative fees and carry must not be negative"
            )
        object.__setattr__(self, "at", moment)
        object.__setattr__(self, "allocated_basis", basis)
        object.__setattr__(self, "cumulative_fees", fees)
        object.__setattr__(self, "cumulative_carry", carry)

    @property
    def cumulative_cost(self) -> Decimal:
        return self.cumulative_fees + self.cumulative_carry

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": iso_z(self.at, field_name="at"),
            "allocated_basis": _money_text(self.allocated_basis),
            "cumulative_fees": _money_text(self.cumulative_fees),
            "cumulative_carry": _money_text(self.cumulative_carry),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapitalOccupancyPoint":
        body = _durable_body(
            raw, field_name="occupancy_point", expected_keys=_OCCUPANCY_POINT_KEYS
        )
        return cls(
            at=_persisted_utc(body["at"], field_name="at"),
            allocated_basis=body["allocated_basis"],
            cumulative_fees=body["cumulative_fees"],
            cumulative_carry=body["cumulative_carry"],
        )


def capital_occupancy_point(
    *,
    at: datetime,
    allocated_basis: Any,
    cumulative_fees: Any = "0.00",
    cumulative_carry: Any = "0.00",
) -> CapitalOccupancyPoint:
    """Declare one occupancy point of committed capital."""

    return CapitalOccupancyPoint(
        at=at,
        allocated_basis=allocated_basis,
        cumulative_fees=cumulative_fees,
        cumulative_carry=cumulative_carry,
    )


@dataclass(frozen=True)
class CapitalHoursPolicy:
    """The declared policy a capital-hours measurement runs under.

    The endpoint policy - which declared basis resolves an interval between two
    points - is *explicitly* declared, because "freeze basis, reserve treatment,
    partial-fill rules and endpoint policy" is an owner input. The shipped policy
    declares no endpoint policy at all, so a measurement under it abstains instead
    of picking one. The occupancy-basis token is recorded with every measurement so
    the definition of the measured quantity is versioned rather than implicit.
    """

    policy_version: str = CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION
    endpoint_policy: CapitalHoursEndpointPolicy | None = None
    basis_token: str = CAPITAL_HOURS_BASIS_TOKEN

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_version",
            _require_policy_version(
                self.policy_version,
                field_name="policy_version",
                declared=DECLARED_CAPITAL_POLICY_VERSIONS,
            ),
        )
        object.__setattr__(
            self,
            "endpoint_policy",
            _optional_enum_of(
                CapitalHoursEndpointPolicy,
                self.endpoint_policy,
                field_name="endpoint_policy",
            ),
        )
        object.__setattr__(
            self, "basis_token", _text(self.basis_token, field_name="basis_token")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "endpoint_policy": (
                None if self.endpoint_policy is None else self.endpoint_policy.value
            ),
            "basis_token": self.basis_token,
        }


def capital_hours_engineering_scenario_policy(
    *,
    endpoint_policy: CapitalHoursEndpointPolicy | None = None,
    basis_token: str = CAPITAL_HOURS_BASIS_TOKEN,
) -> CapitalHoursPolicy:
    """The R5-C engineering-scenario capital-hours policy.

    The endpoint policy defaults to *nothing*, so the shipped policy abstains on
    every measurement. The basis token labels the measured quantity as an
    engineering-scenario definition rather than a ratified production metric.
    """

    return CapitalHoursPolicy(
        policy_version=CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION,
        endpoint_policy=endpoint_policy,
        basis_token=basis_token,
    )


def _hours_span_seconds(
    earlier: CapitalOccupancyPoint, later: CapitalOccupancyPoint
) -> Decimal:
    """Exact span between two preserved instants, in seconds."""

    delta = later.at - earlier.at
    microseconds = (
        delta.days * 86_400 + delta.seconds
    ) * 1_000_000 + delta.microseconds
    return Decimal(microseconds) / Decimal(1_000_000)


def _hours_value(value: Any, *, field_name: str, signed: bool = False) -> Decimal:
    """Validate a capital-hours amount on the declared quantum grid."""

    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise CapitalIntelligenceError(
                f"{field_name} must be an exact decimal, got {value!r}"
            ) from exc
    else:
        raise CapitalIntelligenceError(
            f"{field_name} must be an exact decimal string or Decimal value, "
            f"not {type(value).__name__}"
        )
    if not parsed.is_finite():
        raise CapitalIntelligenceError(f"{field_name} must be a finite amount")
    if not signed and parsed < 0:
        raise CapitalIntelligenceError(f"{field_name} must not be negative")
    return parsed.quantize(CAPITAL_HOURS_QUANTUM, rounding=ROUND_HALF_UP)


def _hours_identity(
    series: tuple[CapitalOccupancyPoint, ...],
    policy: CapitalHoursPolicy,
    reason: CapitalHoursUnavailableReason | None,
) -> str:
    """Deterministic identity of one capital-hours measurement."""

    return stable_hash(
        CAPITAL_HOURS_ID_PREFIX,
        {
            "hours_schema_version": CAPITAL_HOURS_SCHEMA_VERSION,
            "policy_version": policy.policy_version,
            "basis_token": policy.basis_token,
            "endpoint_policy": (
                None if policy.endpoint_policy is None else policy.endpoint_policy.value
            ),
            "occupancy_points": [point.to_dict() for point in series],
            "unavailable_reason": None if reason is None else reason.value,
        },
    )


@dataclass(frozen=True)
class CapitalHoursMeasure:
    """The capital-hours measurement of one declared occupancy series.

    Capital-hours is "the time integral of K(t), expressed in currency-hours", so
    the integral is deterministic arithmetic over preserved instants and an
    explicitly declared endpoint policy, quantized once onto the declared
    ``CAPITAL_HOURS_QUANTUM`` grid. Zero or unknown occupancy yields the declared
    ``UNAVAILABLE`` status rather than a zero that could be averaged into a metric
    and quietly read as a real occupancy.
    """

    hours_id: str
    policy_version: str
    basis_token: str
    endpoint_policy: CapitalHoursEndpointPolicy | None
    status: CapitalHoursStatus
    unavailable_reason: CapitalHoursUnavailableReason | None
    capital_hours: Decimal | None
    cost_capital_hours: Decimal | None
    net_capital_hours: Decimal | None
    occupancy_points: int
    observed_span_seconds: int
    authoritative: bool = False

    def __post_init__(self) -> None:
        hours_id = _text(self.hours_id, field_name="hours_id")
        if not hours_id.startswith(f"{CAPITAL_HOURS_ID_PREFIX}:"):
            raise CapitalIntelligenceError(
                f"hours_id must carry the declared {CAPITAL_HOURS_ID_PREFIX}: prefix"
            )
        object.__setattr__(self, "hours_id", hours_id)
        object.__setattr__(
            self,
            "policy_version",
            _require_policy_version(
                self.policy_version,
                field_name="policy_version",
                declared=DECLARED_CAPITAL_POLICY_VERSIONS,
            ),
        )
        object.__setattr__(
            self, "basis_token", _text(self.basis_token, field_name="basis_token")
        )
        object.__setattr__(
            self,
            "endpoint_policy",
            _optional_enum_of(
                CapitalHoursEndpointPolicy,
                self.endpoint_policy,
                field_name="endpoint_policy",
            ),
        )
        status = _enum_of(CapitalHoursStatus, self.status, field_name="status")
        reason = _optional_enum_of(
            CapitalHoursUnavailableReason,
            self.unavailable_reason,
            field_name="unavailable_reason",
        )
        capital = (
            None
            if self.capital_hours is None
            else _hours_value(self.capital_hours, field_name="capital_hours")
        )
        cost = (
            None
            if self.cost_capital_hours is None
            else _hours_value(self.cost_capital_hours, field_name="cost_capital_hours")
        )
        net = (
            None
            if self.net_capital_hours is None
            else _hours_value(
                self.net_capital_hours, field_name="net_capital_hours", signed=True
            )
        )
        for counted_name, counted in (
            ("occupancy_points", self.occupancy_points),
            ("observed_span_seconds", self.observed_span_seconds),
        ):
            if isinstance(counted, bool) or not isinstance(counted, int) or counted < 0:
                raise CapitalIntelligenceError(
                    f"{counted_name} must be a non-negative integer"
                )
        if self.authoritative is not False:
            raise CapitalIntelligenceError(
                "a capital-hours measurement is research evidence and is never "
                "authoritative"
            )
        if status is CapitalHoursStatus.MEASURED:
            if reason is not None or capital is None or cost is None or net is None:
                raise CapitalIntelligenceError(
                    "a measured capital-hours record publishes its integral, its cost "
                    "channel and its net, and no unavailable reason"
                )
            if capital <= 0:
                raise CapitalIntelligenceError(
                    "a measured capital-hours record has a positive occupancy integral"
                )
        else:
            if reason is None:
                raise CapitalIntelligenceError(
                    "an unavailable capital-hours record names the reason it abstained"
                )
            if capital is not None or cost is not None or net is not None:
                raise CapitalIntelligenceError(
                    "an unavailable capital-hours record publishes no number"
                )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "unavailable_reason", reason)
        object.__setattr__(self, "capital_hours", capital)
        object.__setattr__(self, "cost_capital_hours", cost)
        object.__setattr__(self, "net_capital_hours", net)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hours_schema_version": CAPITAL_HOURS_SCHEMA_VERSION,
            "hours_id": self.hours_id,
            "policy_version": self.policy_version,
            "basis_token": self.basis_token,
            "endpoint_policy": (
                None if self.endpoint_policy is None else self.endpoint_policy.value
            ),
            "status": self.status.value,
            "unavailable_reason": (
                None
                if self.unavailable_reason is None
                else self.unavailable_reason.value
            ),
            "capital_hours": (
                None if self.capital_hours is None else _money_text(self.capital_hours)
            ),
            "cost_capital_hours": (
                None
                if self.cost_capital_hours is None
                else _money_text(self.cost_capital_hours)
            ),
            "net_capital_hours": (
                None
                if self.net_capital_hours is None
                else _money_text(self.net_capital_hours)
            ),
            "occupancy_points": self.occupancy_points,
            "observed_span_seconds": self.observed_span_seconds,
            "authoritative": self.authoritative,
        }


def measure_capital_hours(
    points: Any,
    *,
    policy: CapitalHoursPolicy,
    started_at: datetime | None = None,
) -> CapitalHoursMeasure:
    """Measure the capital-hours of one declared occupancy series.

    The integral walks the declared points in preserved-instant order, resolving
    each interval under the policy's *declared* endpoint policy, and it refuses to
    guess: no declared endpoint policy, a non-monotone series, a point before the
    declared start, a rewound cumulative cost series or a zero occupancy all yield
    a named ``UNAVAILABLE`` measurement instead of a number. Nothing here reads a
    clock, and no instant is invented.
    """

    if not isinstance(policy, CapitalHoursPolicy):
        raise CapitalIntelligenceError(
            "a capital-hours measurement requires a CapitalHoursPolicy"
        )
    series = _records(
        points, field_name="occupancy_points", kind=CapitalOccupancyPoint
    )
    start = None if started_at is None else _utc(started_at, field_name="started_at")
    # An unordered series has no observed span: it reports none rather than a
    # negative or absolute distance between two instants that never followed one
    # another.
    span = (
        int(_hours_span_seconds(series[0], series[-1]))
        if series and series[-1].at > series[0].at
        else 0
    )

    def unavailable(
        reason: CapitalHoursUnavailableReason,
    ) -> CapitalHoursMeasure:
        return CapitalHoursMeasure(
            hours_id=_hours_identity(series, policy, reason),
            policy_version=policy.policy_version,
            basis_token=policy.basis_token,
            endpoint_policy=policy.endpoint_policy,
            status=CapitalHoursStatus.UNAVAILABLE,
            unavailable_reason=reason,
            capital_hours=None,
            cost_capital_hours=None,
            net_capital_hours=None,
            occupancy_points=len(series),
            observed_span_seconds=span,
        )

    if not series:
        return unavailable(CapitalHoursUnavailableReason.NO_OCCUPANCY_POINTS)
    if policy.endpoint_policy is None:
        return unavailable(CapitalHoursUnavailableReason.ENDPOINT_POLICY_NOT_DECLARED)
    if start is not None and series[0].at < start:
        return unavailable(CapitalHoursUnavailableReason.POINT_BEFORE_START)
    for earlier, later in zip(series, series[1:]):
        if later.at <= earlier.at:
            return unavailable(CapitalHoursUnavailableReason.POINTS_NOT_MONOTONE)
        if later.cumulative_cost < earlier.cumulative_cost:
            return unavailable(CapitalHoursUnavailableReason.DECLARED_COSTS_MISSING)

    step_from_start = (
        policy.endpoint_policy is CapitalHoursEndpointPolicy.STEP_START
    )
    capital_total = Decimal("0.00")
    cost_total = Decimal("0.00")
    for earlier, later in zip(series, series[1:]):
        seconds = _hours_span_seconds(earlier, later)
        basis = earlier.allocated_basis if step_from_start else later.allocated_basis
        capital_total += (basis * seconds) / CAPITAL_SECONDS_PER_HOUR
        cost_total += (
            (later.cumulative_cost - earlier.cumulative_cost) * seconds
        ) / CAPITAL_SECONDS_PER_HOUR
    capital_hours = capital_total.quantize(
        CAPITAL_HOURS_QUANTUM, rounding=ROUND_HALF_UP
    )
    cost_hours = cost_total.quantize(CAPITAL_HOURS_QUANTUM, rounding=ROUND_HALF_UP)
    if capital_hours == 0:
        return unavailable(CapitalHoursUnavailableReason.ZERO_OCCUPANCY)
    return CapitalHoursMeasure(
        hours_id=_hours_identity(series, policy, None),
        policy_version=policy.policy_version,
        basis_token=policy.basis_token,
        endpoint_policy=policy.endpoint_policy,
        status=CapitalHoursStatus.MEASURED,
        unavailable_reason=None,
        capital_hours=capital_hours,
        cost_capital_hours=cost_hours,
        net_capital_hours=capital_hours - cost_hours,
        occupancy_points=len(series),
        observed_span_seconds=span,
    )


def _return_per_hour(value: Any, *, field_name: str) -> Decimal:
    """Validate a `return per hour` amount on its declared quantum grid."""

    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise CapitalIntelligenceError(
                f"{field_name} must be an exact decimal, got {value!r}"
            ) from exc
    else:
        raise CapitalIntelligenceError(
            f"{field_name} must be an exact decimal string or Decimal value, "
            f"not {type(value).__name__}"
        )
    if not parsed.is_finite():
        raise CapitalIntelligenceError(f"{field_name} must be a finite amount")
    return parsed.quantize(CAPITAL_RETURN_PER_HOUR_QUANTUM, rounding=ROUND_HALF_UP)


def _cohort_identity(
    cohort: str,
    measure: CapitalHoursMeasure,
    realized: Decimal | None,
    operating: Decimal | None,
) -> str:
    return stable_hash(
        CAPITAL_HOURS_ID_PREFIX,
        {
            "cohort": cohort,
            "hours_id": measure.hours_id,
            "status": measure.status.value,
            "realized_trading_net": (
                None if realized is None else _money_text(realized)
            ),
            "operating_net": None if operating is None else _money_text(operating),
        },
    )


@dataclass(frozen=True)
class CapitalTimeEfficiencyCohort:
    """One resolved cohort's capital-time efficiency (return per capital-hour).

    "For a fully resolved cohort: realized trading net divided by its
    capital-hours. Report operating-net variant separately. Units are return per
    hour; zero/unknown occupancy yields unavailable." The metric is a *diagnostic*
    only - never the portfolio objective and never a promotion threshold - so it is
    published with the occupancy it was measured over, and the operating-net
    variant is reported separately rather than blended into the trading net.
    """

    cohort_id: str
    cohort: str
    hours_id: str
    status: CapitalTimeEfficiencyStatus
    unavailable_reason: CapitalHoursUnavailableReason | None
    capital_hours: Decimal | None
    cost_capital_hours: Decimal | None
    net_capital_hours: Decimal | None
    realized_trading_net: Decimal | None
    return_per_hour: Decimal | None
    operating_net: Decimal | None
    operating_return_per_hour: Decimal | None
    occupancy_points: int
    authoritative: bool = False

    def __post_init__(self) -> None:
        for identifier_name, identifier in (
            ("cohort_id", self.cohort_id),
            ("hours_id", self.hours_id),
        ):
            token = _text(identifier, field_name=identifier_name)
            if not token.startswith(f"{CAPITAL_HOURS_ID_PREFIX}:"):
                raise CapitalIntelligenceError(
                    f"{identifier_name} must carry the declared "
                    f"{CAPITAL_HOURS_ID_PREFIX}: prefix"
                )
            object.__setattr__(self, identifier_name, token)
        status = _enum_of(
            CapitalTimeEfficiencyStatus, self.status, field_name="status"
        )
        reason = _optional_enum_of(
            CapitalHoursUnavailableReason,
            self.unavailable_reason,
            field_name="unavailable_reason",
        )
        capital = (
            None
            if self.capital_hours is None
            else _hours_value(self.capital_hours, field_name="capital_hours")
        )
        cost = (
            None
            if self.cost_capital_hours is None
            else _hours_value(self.cost_capital_hours, field_name="cost_capital_hours")
        )
        net_hours = (
            None
            if self.net_capital_hours is None
            else _hours_value(
                self.net_capital_hours, field_name="net_capital_hours", signed=True
            )
        )
        realized = (
            None
            if self.realized_trading_net is None
            else _money(self.realized_trading_net, field_name="realized_trading_net")
        )
        ratio = (
            None
            if self.return_per_hour is None
            else _return_per_hour(self.return_per_hour, field_name="return_per_hour")
        )
        operating = (
            None
            if self.operating_net is None
            else _money(self.operating_net, field_name="operating_net")
        )
        operating_ratio = (
            None
            if self.operating_return_per_hour is None
            else _return_per_hour(
                self.operating_return_per_hour,
                field_name="operating_return_per_hour",
            )
        )
        counted = self.occupancy_points
        if isinstance(counted, bool) or not isinstance(counted, int) or counted < 0:
            raise CapitalIntelligenceError(
                "occupancy_points must be a non-negative integer"
            )
        if self.authoritative is not False:
            raise CapitalIntelligenceError(
                "a capital-time efficiency record is research evidence and is never "
                "authoritative"
            )
        numbers = (capital, cost, net_hours, realized, ratio)
        if status is CapitalTimeEfficiencyStatus.MEASURED:
            if reason is not None or any(value is None for value in numbers):
                raise CapitalIntelligenceError(
                    "a measured cohort publishes its occupancy, its realized net and "
                    "its return per hour, and no unavailable reason"
                )
            if capital <= 0:
                raise CapitalIntelligenceError(
                    "a measured cohort has a positive occupancy integral"
                )
        else:
            if reason is None:
                raise CapitalIntelligenceError(
                    "an unavailable cohort names the reason it abstained"
                )
            if any(value is not None for value in (*numbers, operating, operating_ratio)):
                raise CapitalIntelligenceError(
                    "an unavailable cohort publishes no number at all"
                )
        object.__setattr__(self, "cohort", _text(self.cohort, field_name="cohort"))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "unavailable_reason", reason)
        object.__setattr__(self, "capital_hours", capital)
        object.__setattr__(self, "cost_capital_hours", cost)
        object.__setattr__(self, "net_capital_hours", net_hours)
        object.__setattr__(self, "realized_trading_net", realized)
        object.__setattr__(self, "return_per_hour", ratio)
        object.__setattr__(self, "operating_net", operating)
        object.__setattr__(self, "operating_return_per_hour", operating_ratio)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hours_schema_version": CAPITAL_HOURS_SCHEMA_VERSION,
            "cohort_id": self.cohort_id,
            "cohort": self.cohort,
            "hours_id": self.hours_id,
            "status": self.status.value,
            "unavailable_reason": (
                None
                if self.unavailable_reason is None
                else self.unavailable_reason.value
            ),
            "capital_hours": (
                None if self.capital_hours is None else _money_text(self.capital_hours)
            ),
            "cost_capital_hours": (
                None
                if self.cost_capital_hours is None
                else _money_text(self.cost_capital_hours)
            ),
            "net_capital_hours": (
                None
                if self.net_capital_hours is None
                else _money_text(self.net_capital_hours)
            ),
            "realized_trading_net": (
                None
                if self.realized_trading_net is None
                else _money_text(self.realized_trading_net)
            ),
            "return_per_hour": (
                None
                if self.return_per_hour is None
                else _money_text(self.return_per_hour)
            ),
            "operating_net": (
                None if self.operating_net is None else _money_text(self.operating_net)
            ),
            "operating_return_per_hour": (
                None
                if self.operating_return_per_hour is None
                else _money_text(self.operating_return_per_hour)
            ),
            "occupancy_points": self.occupancy_points,
            "authoritative": self.authoritative,
        }


def capital_time_efficiency(
    cohort: str,
    *,
    points: Any,
    policy: CapitalHoursPolicy,
    realized_trading_net: Any = "0.00",
    operating_net: Any = None,
    started_at: datetime | None = None,
) -> CapitalTimeEfficiencyCohort:
    """Measure one cohort's capital-time efficiency.

    "For a fully resolved cohort: realized trading net divided by its
    capital-hours ... zero/unknown occupancy yields unavailable." The occupancy is
    measured first and *every* reason it abstains for is carried through, so an
    unresolvable cohort is unavailable rather than a fabricated zero. The ratio is
    quantized once onto the declared ``CAPITAL_RETURN_PER_HOUR_QUANTUM`` grid, and
    an operating-net variant is reported as its own ratio instead of being mixed
    into the trading return.
    """

    label = _text(cohort, field_name="cohort")
    if not isinstance(policy, CapitalHoursPolicy):
        raise CapitalIntelligenceError("a cohort requires a CapitalHoursPolicy")
    measure = measure_capital_hours(points, policy=policy, started_at=started_at)
    if measure.status is CapitalHoursStatus.UNAVAILABLE:
        return CapitalTimeEfficiencyCohort(
            cohort_id=_cohort_identity(label, measure, None, None),
            cohort=label,
            hours_id=measure.hours_id,
            status=CapitalTimeEfficiencyStatus.UNAVAILABLE,
            unavailable_reason=measure.unavailable_reason,
            capital_hours=None,
            cost_capital_hours=None,
            net_capital_hours=None,
            realized_trading_net=None,
            return_per_hour=None,
            operating_net=None,
            operating_return_per_hour=None,
            occupancy_points=measure.occupancy_points,
        )
    realized = _money(realized_trading_net, field_name="realized_trading_net")
    operating = (
        None if operating_net is None else _money(operating_net, field_name="operating_net")
    )
    occupancy = measure.capital_hours
    return CapitalTimeEfficiencyCohort(
        cohort_id=_cohort_identity(label, measure, realized, operating),
        cohort=label,
        hours_id=measure.hours_id,
        status=CapitalTimeEfficiencyStatus.MEASURED,
        unavailable_reason=None,
        capital_hours=measure.capital_hours,
        cost_capital_hours=measure.cost_capital_hours,
        net_capital_hours=measure.net_capital_hours,
        realized_trading_net=realized,
        return_per_hour=(realized / occupancy).quantize(
            CAPITAL_RETURN_PER_HOUR_QUANTUM, rounding=ROUND_HALF_UP
        ),
        operating_net=operating,
        operating_return_per_hour=(
            None
            if operating is None
            else (operating / occupancy).quantize(
                CAPITAL_RETURN_PER_HOUR_QUANTUM, rounding=ROUND_HALF_UP
            )
        ),
        occupancy_points=measure.occupancy_points,
    )


@dataclass(frozen=True)
class CapitalTimeEfficiencyAggregate:
    """The capital-time efficiency of several cohorts, aggregated by ratio of sums.

    "Aggregate by ratio of sums, not average of trade ratios." Every contributing
    cohort's occupancy and realized net is summed first and the ratio is taken
    once, so a tiny cohort cannot move the aggregate as much as a large one. The
    cohorts that abstained are *named* rather than dropped silently, because an
    excluded cohort that nobody can see is a fabricated coverage claim.
    """

    aggregate_id: str
    status: CapitalTimeEfficiencyStatus
    unavailable_reason: CapitalHoursUnavailableReason | None
    capital_hours: Decimal | None
    cost_capital_hours: Decimal | None
    net_capital_hours: Decimal | None
    realized_trading_net: Decimal | None
    return_per_hour: Decimal | None
    measured_cohorts: tuple[str, ...]
    unavailable_cohorts: tuple[str, ...]
    authoritative: bool = False

    def __post_init__(self) -> None:
        aggregate_id = _text(self.aggregate_id, field_name="aggregate_id")
        if not aggregate_id.startswith(f"{CAPITAL_HOURS_ID_PREFIX}:"):
            raise CapitalIntelligenceError(
                f"aggregate_id must carry the declared {CAPITAL_HOURS_ID_PREFIX}: "
                "prefix"
            )
        status = _enum_of(
            CapitalTimeEfficiencyStatus, self.status, field_name="status"
        )
        reason = _optional_enum_of(
            CapitalHoursUnavailableReason,
            self.unavailable_reason,
            field_name="unavailable_reason",
        )
        capital = (
            None
            if self.capital_hours is None
            else _hours_value(self.capital_hours, field_name="capital_hours")
        )
        cost = (
            None
            if self.cost_capital_hours is None
            else _hours_value(self.cost_capital_hours, field_name="cost_capital_hours")
        )
        net_hours = (
            None
            if self.net_capital_hours is None
            else _hours_value(
                self.net_capital_hours, field_name="net_capital_hours", signed=True
            )
        )
        realized = (
            None
            if self.realized_trading_net is None
            else _money(self.realized_trading_net, field_name="realized_trading_net")
        )
        ratio = (
            None
            if self.return_per_hour is None
            else _return_per_hour(self.return_per_hour, field_name="return_per_hour")
        )
        measured = _records(
            self.measured_cohorts, field_name="measured_cohorts", kind=str
        )
        unavailable = _records(
            self.unavailable_cohorts, field_name="unavailable_cohorts", kind=str
        )
        if self.authoritative is not False:
            raise CapitalIntelligenceError(
                "a capital-time aggregate is research evidence and is never "
                "authoritative"
            )
        numbers = (capital, cost, net_hours, realized, ratio)
        if status is CapitalTimeEfficiencyStatus.MEASURED:
            if reason is not None or not measured:
                raise CapitalIntelligenceError(
                    "a measured aggregate names its measured cohorts and no reason"
                )
            if any(value is None for value in numbers):
                raise CapitalIntelligenceError(
                    "a measured aggregate publishes its summed occupancy and its ratio"
                )
        else:
            if reason is None or measured:
                raise CapitalIntelligenceError(
                    "an unavailable aggregate names its reason and measures no cohort"
                )
            if any(value is not None for value in numbers):
                raise CapitalIntelligenceError(
                    "an unavailable aggregate publishes no number"
                )
        object.__setattr__(self, "aggregate_id", aggregate_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "unavailable_reason", reason)
        object.__setattr__(self, "capital_hours", capital)
        object.__setattr__(self, "cost_capital_hours", cost)
        object.__setattr__(self, "net_capital_hours", net_hours)
        object.__setattr__(self, "realized_trading_net", realized)
        object.__setattr__(self, "return_per_hour", ratio)
        object.__setattr__(self, "measured_cohorts", measured)
        object.__setattr__(self, "unavailable_cohorts", unavailable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hours_schema_version": CAPITAL_HOURS_SCHEMA_VERSION,
            "aggregate_id": self.aggregate_id,
            "status": self.status.value,
            "unavailable_reason": (
                None
                if self.unavailable_reason is None
                else self.unavailable_reason.value
            ),
            "capital_hours": (
                None if self.capital_hours is None else _money_text(self.capital_hours)
            ),
            "cost_capital_hours": (
                None
                if self.cost_capital_hours is None
                else _money_text(self.cost_capital_hours)
            ),
            "net_capital_hours": (
                None
                if self.net_capital_hours is None
                else _money_text(self.net_capital_hours)
            ),
            "realized_trading_net": (
                None
                if self.realized_trading_net is None
                else _money_text(self.realized_trading_net)
            ),
            "return_per_hour": (
                None
                if self.return_per_hour is None
                else _money_text(self.return_per_hour)
            ),
            "measured_cohorts": list(self.measured_cohorts),
            "unavailable_cohorts": list(self.unavailable_cohorts),
            "authoritative": self.authoritative,
        }


def aggregate_capital_time_efficiency(
    cohorts: Any,
) -> CapitalTimeEfficiencyAggregate:
    """Aggregate several cohorts "by ratio of sums, not average of trade ratios".

    Every measured cohort contributes its declared occupancy and its realized net;
    the ratio is taken once, at the end, so the aggregate is dominated by
    occupancy rather than by the number of cohorts. An unresolvable cohort is named
    in ``unavailable_cohorts`` and contributes nothing, and an aggregate with no
    measured cohort is ``UNAVAILABLE`` rather than a zero.
    """

    rows = _records(
        cohorts, field_name="cohorts", kind=CapitalTimeEfficiencyCohort
    )
    measured = tuple(
        row for row in rows if row.status is CapitalTimeEfficiencyStatus.MEASURED
    )
    unavailable = tuple(
        row.cohort for row in rows if row.status is not CapitalTimeEfficiencyStatus.MEASURED
    )
    aggregate_id = stable_hash(
        CAPITAL_HOURS_ID_PREFIX,
        {
            "aggregate": "capital-time-efficiency",
            "hours_schema_version": CAPITAL_HOURS_SCHEMA_VERSION,
            "cohorts": [row.cohort_id for row in rows],
        },
    )
    if not measured:
        return CapitalTimeEfficiencyAggregate(
            aggregate_id=aggregate_id,
            status=CapitalTimeEfficiencyStatus.UNAVAILABLE,
            unavailable_reason=CapitalHoursUnavailableReason.NO_OCCUPANCY_POINTS,
            capital_hours=None,
            cost_capital_hours=None,
            net_capital_hours=None,
            realized_trading_net=None,
            return_per_hour=None,
            measured_cohorts=(),
            unavailable_cohorts=unavailable,
        )
    capital_hours = sum(
        (row.capital_hours for row in measured), Decimal("0.0000")
    )
    cost_hours = sum(
        (row.cost_capital_hours for row in measured), Decimal("0.0000")
    )
    net_hours = sum(
        (row.net_capital_hours for row in measured), Decimal("0.0000")
    )
    realized = sum(
        (row.realized_trading_net for row in measured), Decimal("0.00")
    )
    return CapitalTimeEfficiencyAggregate(
        aggregate_id=aggregate_id,
        status=CapitalTimeEfficiencyStatus.MEASURED,
        unavailable_reason=None,
        capital_hours=capital_hours,
        cost_capital_hours=cost_hours,
        net_capital_hours=net_hours,
        realized_trading_net=realized,
        return_per_hour=(realized / capital_hours).quantize(
            CAPITAL_RETURN_PER_HOUR_QUANTUM, rounding=ROUND_HALF_UP
        ),
        measured_cohorts=tuple(row.cohort for row in measured),
        unavailable_cohorts=unavailable,
    )


#: The declared public surface of the R5-C section. Every name here is research
#: vocabulary: none of them places, sizes, allocates or executes anything.
__all__ = [
    "CAPITAL_BOUND_PRECEDENCE",
    "CAPITAL_COMMIT_ID_PREFIX",
    "CAPITAL_COMMIT_SCHEMA_VERSION",
    "CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE",
    "CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION",
    "CAPITAL_HOURS_BASIS_TOKEN",
    "CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION",
    "CAPITAL_HOURS_ID_PREFIX",
    "CAPITAL_HOURS_QUANTUM",
    "CAPITAL_HOURS_SCHEMA_VERSION",
    "CAPITAL_LIABILITIES_SUPPORTED",
    "CAPITAL_RECEIVABLES_SUPPORTED",
    "CAPITAL_RECOMPUTE_CASH_LAWS",
    "CAPITAL_RETURN_PER_HOUR_QUANTUM",
    "CAPITAL_SECONDS_PER_HOUR",
    "CAPITAL_SNAPSHOT_ID_PREFIX",
    "CAPITAL_SNAPSHOT_RECORD_KEYS",
    "CAPITAL_SNAPSHOT_SCHEMA_VERSION",
    "CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS",
    "DECLARED_CAPITAL_POLICY_VERSIONS",
    "MONEY_QUANTUM",
    "OPEN_OWNER_INPUTS",
    "OWNER_INPUT_STATUS",
    "CapitalAccountingState",
    "CapitalBound",
    "CapitalCashLaw",
    "CapitalCommitRejection",
    "CapitalCommitResult",
    "CapitalCommitStatus",
    "CapitalDeployableAbstentionReason",
    "CapitalDeployablePolicy",
    "CapitalDeployableStatus",
    "CapitalDeployableView",
    "CapitalEnvironment",
    "CapitalFeeState",
    "CapitalFreshness",
    "CapitalHoursEndpointPolicy",
    "CapitalHoursMeasure",
    "CapitalHoursPolicy",
    "CapitalHoursStatus",
    "CapitalHoursUnavailableReason",
    "CapitalIntelligenceError",
    "CapitalLimitKind",
    "CapitalLimitUsage",
    "CapitalMarkState",
    "CapitalOccupancyPoint",
    "CapitalPartition",
    "CapitalPosition",
    "CapitalReconciliation",
    "CapitalReconciliationStatus",
    "CapitalRecomputeTrigger",
    "CapitalSnapshot",
    "CapitalSourceWatermark",
    "CapitalTimeEfficiencyAggregate",
    "CapitalTimeEfficiencyCohort",
    "CapitalTimeEfficiencyStatus",
    "CashPartitionLedger",
    "SleeveUsage",
    "aggregate_capital_time_efficiency",
    "assert_portfolio_version_lineage",
    "assert_snapshot_recomputes_from",
    "capital_deployable_engineering_scenario_policy",
    "capital_hours_engineering_scenario_policy",
    "capital_occupancy_point",
    "capital_snapshot_identity",
    "capital_time_efficiency",
    "commit_reservation",
    "evaluate_deployable_capital",
    "measure_capital_hours",
    "next_portfolio_version",
    "to_portfolio_capital_state",
]

































