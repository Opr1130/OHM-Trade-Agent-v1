"""R5-C Capital Intelligence acceptance tests.

This module proves the `ATDD-R5-C-capital-intelligence` increment: the canonical
capital record (`CapitalSnapshot`) owned by `app/services/capital_rotation_intelligence.py`
and the capabilities the frozen R5-0 contract assigns to `SC-06`/`SC-07`
(dependency `R5-B`, open input `OQ-06`, gate `G3`):

* one partitioned capital snapshot that carries every frozen record key, where the
  cash partitions reconstruct the cash total exactly and no second cash book exists;
* exact accounting laws: equity is cash plus declared marked position value, the
  marked aggregate reconciles to the positions, deployed basis is reported
  separately from cash, and liabilities are refused because the frozen engine
  contract declares no liability surface;
* monotonic portfolio-version lineage with a declared recompute reason, so a
  re-derived snapshot proves *why* it is newer and which cash movement it may
  describe (an external deposit or withdrawal is refused);
* atomic compare-and-commit reservation: a plan is applied against the version it
  was built for, exactly once, all-or-nothing, with the consumed capital recorded
  as a partition move and the risk usage declared by the caller, never invented;
* concurrency honesty: a stale expected version is rejected without a partial
  write, a replay of the same plan is idempotent and consumes nothing twice, and a
  losing racer never leaves the capital record double-spent;
* capital-hours and time efficiency per cohort as deterministic arithmetic over
  preserved instants and an explicitly declared endpoint policy, never a clock read.

The tests are deliberately adversarial about the boundaries the adopted clauses
set: an unbound or mismatched identity, a stale account state, an unsupported
accounting state, a failed reconciliation, an unresolved negative balance, missing
fees, an outlived validity deadline and incomplete evidence each abstain visibly
with no deployable number; a partially declared fee state, a silently rounded
float, a naive instant, a negative amount, a duplicate symbol and an unsupported
recompute trigger are refused rather than interpreted.

Every record is research-only evidence: `authoritative` is hard-coded `False`, no
order, execution, store, scheduler, Telegram or environment surface is added, and
`OQ-06` (sleeve caps and hard/soft limit semantics) stays an owner decision whose
unratified numbers keep the shipped policy abstaining instead of guessing.

The tests are deterministic and hermetic: no clock, no network, no database and no
environment read. Every instant and every amount is explicit.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
import threading

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts import portfolio as portfolio_module  # noqa: E402
from app.opip.contracts.portfolio import (  # noqa: E402
    PortfolioContractError,
    PortfolioDirection,
    PortfolioReservation,
    PortfolioReservationPlan,
    assert_reservation_plan_current,
)
from app.services import capital_rotation_intelligence as capital_module  # noqa: E402
from app.services.release_profiles import (  # noqa: E402
    get_release_profiles,
    validate_profile_contract,
)
from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    parse_scope_contract,
)
from tests.test_atdd_scope_control import (  # noqa: E402
    global_pointer_pin_violations,
)

pytestmark = pytest.mark.acceptance

from app.services.capital_rotation_intelligence import (  # noqa: E402
    CAPITAL_BOUND_PRECEDENCE,
    CAPITAL_COMMIT_ID_PREFIX,
    CAPITAL_COMMIT_SCHEMA_VERSION,
    CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE,
    CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION,
    CAPITAL_HOURS_BASIS_TOKEN,
    CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION,
    CAPITAL_HOURS_ID_PREFIX,
    CAPITAL_HOURS_QUANTUM,
    CAPITAL_HOURS_SCHEMA_VERSION,
    CAPITAL_LIABILITIES_SUPPORTED,
    CAPITAL_RECEIVABLES_SUPPORTED,
    CAPITAL_RECOMPUTE_CASH_LAWS,
    CAPITAL_RETURN_PER_HOUR_QUANTUM,
    CAPITAL_SECONDS_PER_HOUR,
    CAPITAL_SNAPSHOT_ID_PREFIX,
    CAPITAL_SNAPSHOT_RECORD_KEYS,
    CAPITAL_SNAPSHOT_SCHEMA_VERSION,
    CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS,
    DECLARED_CAPITAL_POLICY_VERSIONS,
    MONEY_QUANTUM,
    OPEN_OWNER_INPUTS,
    OWNER_INPUT_STATUS,
    CapitalAccountingState,
    CapitalBound,
    CapitalCashLaw,
    CapitalCommitRejection,
    CapitalCommitResult,
    CapitalCommitStatus,
    CapitalDeployableAbstentionReason,
    CapitalDeployablePolicy,
    CapitalDeployableStatus,
    CapitalDeployableView,
    CapitalEnvironment,
    CapitalFeeState,
    CapitalFreshness,
    CapitalHoursEndpointPolicy,
    CapitalHoursMeasure,
    CapitalHoursPolicy,
    CapitalHoursStatus,
    CapitalHoursUnavailableReason,
    CapitalIntelligenceError,
    CapitalLimitKind,
    CapitalLimitUsage,
    CapitalMarkState,
    CapitalOccupancyPoint,
    CapitalPartition,
    CapitalPosition,
    CapitalReconciliation,
    CapitalReconciliationStatus,
    CapitalRecomputeTrigger,
    CapitalSnapshot,
    CapitalSourceWatermark,
    CapitalTimeEfficiencyAggregate,
    CapitalTimeEfficiencyCohort,
    CapitalTimeEfficiencyStatus,
    CashPartitionLedger,
    SleeveUsage,
    aggregate_capital_time_efficiency,
    assert_portfolio_version_lineage,
    assert_snapshot_recomputes_from,
    capital_deployable_engineering_scenario_policy,
    capital_hours_engineering_scenario_policy,
    capital_occupancy_point,
    capital_snapshot_identity,
    capital_time_efficiency,
    commit_reservation,
    evaluate_deployable_capital,
    measure_capital_hours,
    next_portfolio_version,
    to_portfolio_capital_state,
)

INCREMENT = "ATDD-R5-C-capital-intelligence"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
MODULE = APP_ROOT / "app" / "services" / "capital_rotation_intelligence.py"
R5C_BANNER = "# R5-C Capital Intelligence"

#: Runtime roots that own trading, discovery, decision and risk paths. None may
#: import the R5-C capital record.
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

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
ACCOUNT = "PAPER-LEDGER-01"
ENGINE = "paper-ledger-engine-v1"
CURRENCY = "USD"
VERSION = "pv-r5c-0001"

#: Sentinel that distinguishes "use the fixture default" from an explicitly
#: absent optional declaration.
class _Unset:
    """Declarative absence marker for optional fixture arguments."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<unset>"


_UNSET = _Unset()


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


def r5c_source() -> str:
    """Return only the R5-C section of the owning module."""

    source = module_source(MODULE)
    assert R5C_BANNER in source
    return source[source.index(R5C_BANNER) :]


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def cash(
    *,
    free: str = "10000.00",
    reserves: str = "0.00",
    safety: str = "500.00",
    blocked: str = "0.00",
    total: str | None = None,
) -> CashPartitionLedger:
    partitions = {
        CapitalPartition.FREE_CASH: free,
        CapitalPartition.ORDER_RESERVES: reserves,
        CapitalPartition.SAFETY_RESERVE: safety,
        CapitalPartition.OTHER_BLOCKED_CASH: blocked,
    }
    if total is None:
        total = f"{sum((Decimal(value) for value in partitions.values()), Decimal('0.00')):.2f}"
    return CashPartitionLedger(partitions=partitions, cash_total=total)


def position(
    *,
    symbol: str = "BTCUSDT",
    quantity: str = "0.1",
    basis: str = "3000.00",
    marked: str = "3120.00",
    marked_at: datetime | None = None,
) -> CapitalPosition:
    return CapitalPosition(
        symbol=symbol,
        quantity=Decimal(quantity),
        basis_capital=basis,
        marked_value=marked,
        marked_at=marked_at or T0,
        mark_reference=f"MARK:{symbol}:1",
    )


def snapshot(
    *,
    version: str = VERSION,
    cash_ledger: CashPartitionLedger | None = None,
    positions: tuple[CapitalPosition, ...] | None = None,
    marks: CapitalMarkState | None = None,
    sleeve_usage: tuple[SleeveUsage, ...] | None = None,
    limit_usage: tuple[CapitalLimitUsage, ...] | None = None,
    source_watermark: CapitalSourceWatermark | None = None,
    reconciliation: CapitalReconciliation | None = None,
    freshness: CapitalFreshness | None = None,
    fee_state: CapitalFeeState | None | _Unset = _UNSET,
    derived_from: str | None = None,
    trigger: CapitalRecomputeTrigger | None = None,
    accounting_state: CapitalAccountingState = CapitalAccountingState.SUPPORTED,
    environment: CapitalEnvironment = CapitalEnvironment.PAPER_LEDGER,
    account: str = ACCOUNT,
    engine: str = ENGINE,
    currency: str = CURRENCY,
    applied: tuple[str, ...] = (),
) -> CapitalSnapshot:
    ledger = cash_ledger or cash()
    position_set = positions if positions is not None else (position(),)
    mark_state = marks or CapitalMarkState(
        marked_at=T0,
        mark_source="paper-ledger-mark-v1",
        marked_position_value="3120.00",
    )
    return CapitalSnapshot(
        portfolio_version=version,
        account=account,
        environment=environment,
        engine=engine,
        currency=currency,
        accounting_state=accounting_state,
        cash=ledger,
        positions=position_set,
        marks=mark_state,
        sleeve_usage=sleeve_usage
        if sleeve_usage is not None
        else (SleeveUsage(sleeve="TACTICAL", used_capital="3000.00", as_of=T0),),
        limit_usage=limit_usage
        if limit_usage is not None
        else (CapitalLimitUsage(kind=CapitalLimitKind.RISK, used_capital="900.00", as_of=T0),),
        fee_state=fee_state
        if fee_state is not _UNSET
        else CapitalFeeState(
            fee_model_version="fee-model-v1",
            fees_settled="0.00",
            fee_watermark=CapitalSourceWatermark(
                ledger_sequence=7, observed_at=T0, state_is_stale=False
            ),
        ),
        source_watermark=source_watermark
        if source_watermark is not None
        else CapitalSourceWatermark(ledger_sequence=7, observed_at=T0, state_is_stale=False),
        reconciliation=reconciliation
        if reconciliation is not None
        else CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=T0,
            reference="RECON:1",
            variance="0.00",
        ),
        freshness=freshness
        if freshness is not None
        else CapitalFreshness(as_of=T0, validity_deadline=at(900), evidence_complete=True),
        derived_from=derived_from,
        recompute_trigger=trigger,
        applied_reservation_references=applied,
    )


def watermark(sequence: int = 7, observed_at: datetime | None = None, stale: bool | None = False):
    return CapitalSourceWatermark(
        ledger_sequence=sequence, observed_at=observed_at or T0, state_is_stale=stale
    )


def fresh(as_of: datetime | None = None, deadline_seconds: int = 900):
    observed = as_of or T0
    return CapitalFreshness(
        as_of=observed,
        validity_deadline=observed + timedelta(seconds=deadline_seconds),
        evidence_complete=True,
    )


def fee_state(fees: str = "0.00", sequence: int = 7):
    return CapitalFeeState(
        fee_model_version="fee-model-v1",
        fees_settled=fees,
        fee_watermark=CapitalSourceWatermark(
            ledger_sequence=sequence, observed_at=T0, state_is_stale=False
        ),
    )


def successor(
    previous: CapitalSnapshot,
    *,
    trigger: CapitalRecomputeTrigger,
    seconds: int = 60,
    **overrides,
) -> CapitalSnapshot:
    """Derive the next snapshot of a lineage, carrying the previous state forward."""

    parameters: dict[str, object] = {
        "version": next_portfolio_version(previous.portfolio_version, trigger=trigger),
        "freshness": CapitalFreshness(
            as_of=previous.freshness.as_of + timedelta(seconds=seconds),
            validity_deadline=previous.freshness.validity_deadline + timedelta(seconds=seconds),
            evidence_complete=True,
        ),
        "derived_from": previous.snapshot_id,
        "trigger": trigger,
        "cash_ledger": previous.cash,
        "positions": previous.positions,
        "marks": previous.marks,
        "sleeve_usage": previous.sleeve_usage,
        "limit_usage": previous.limit_usage,
        "fee_state": previous.fee_state,
        "source_watermark": previous.source_watermark,
        "reconciliation": previous.reconciliation,
        "account": previous.account,
        "environment": previous.environment,
        "engine": previous.engine,
        "currency": previous.currency,
    }
    parameters.update(overrides)
    return snapshot(**parameters)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-003 Monotonic portfolio version and declared recompute provenance
# ---------------------------------------------------------------------------


def test_ac_003_a_recompute_declares_a_supported_trigger() -> None:
    """ATDD-R5-C-capital-intelligence/AC-003: only describable triggers exist.

    GIVEN the frozen recompute reasons of `CI-03`
    WHEN an unsupported, unknown or missing trigger is used and a supported one is
    declared
    THEN the engine refuses every trigger it cannot describe, including an external
    deposit or withdrawal, and the declared law table covers exactly the supported
    vocabulary
    """

    assert CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS == (
        CapitalRecomputeTrigger.DEPOSIT,
        CapitalRecomputeTrigger.WITHDRAWAL,
        CapitalRecomputeTrigger.LIABILITY_CHANGE,
    )
    assert set(CAPITAL_RECOMPUTE_CASH_LAWS) == set(CapitalRecomputeTrigger) - set(
        CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS
    )
    for trigger in CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS:
        with pytest.raises(CapitalIntelligenceError, match="trigger"):
            next_portfolio_version(VERSION, trigger=trigger)
        with pytest.raises(CapitalIntelligenceError, match="trigger"):
            snapshot(trigger=trigger)
    with pytest.raises(CapitalIntelligenceError):
        snapshot(trigger="FEE")
    declared = snapshot(derived_from="CPSN:anything", trigger=CapitalRecomputeTrigger.FEE)
    assert declared.recompute_trigger is CapitalRecomputeTrigger.FEE
    assert declared.to_dict()["recompute_trigger"] == "FEE"
    assert declared.to_dict()["derived_from"] == "CPSN:anything"


def test_ac_003_version_lineage_is_monotone_and_unforked() -> None:
    """ATDD-R5-C-capital-intelligence/AC-003: versions move forward by one.

    GIVEN a committed portfolio version
    WHEN the declared successor is derived and when a fork, a repeat, a skip or a
    backwards version is asserted as lineage
    THEN only the exact next version is accepted as lineage
    """

    assert next_portfolio_version(VERSION, trigger=CapitalRecomputeTrigger.FEE) == "pv-r5c-0001.0001"
    assert (
        next_portfolio_version("pv-r5c-0001.0001", trigger=CapitalRecomputeTrigger.FEE)
        == "pv-r5c-0001.0002"
    )
    assert_portfolio_version_lineage(VERSION, "pv-r5c-0001.0001")
    for invalid in (VERSION, "pv-r5c-0002.0001", "pv-r5c-0001.0002", "pv-r5c-0001.0000"):
        with pytest.raises(CapitalIntelligenceError):
            assert_portfolio_version_lineage(VERSION, invalid)
    with pytest.raises(CapitalIntelligenceError):
        assert_portfolio_version_lineage("pv-r5c-0001.0001", VERSION)
    previous = snapshot()
    renewed = successor(previous, trigger=CapitalRecomputeTrigger.APPROVAL)
    assert renewed.portfolio_version == "pv-r5c-0001.0001"
    assert renewed.derived_from == previous.snapshot_id
    assert renewed.recompute_trigger is CapitalRecomputeTrigger.APPROVAL
    assert renewed.snapshot_id != previous.snapshot_id
    assert_snapshot_recomputes_from(previous, renewed, trigger=CapitalRecomputeTrigger.APPROVAL)
    with pytest.raises(CapitalIntelligenceError, match="trigger"):
        assert_snapshot_recomputes_from(previous, renewed, trigger=CapitalRecomputeTrigger.FEE)


def test_ac_003_a_recompute_may_not_precede_or_re_identify_the_record() -> None:
    """ATDD-R5-C-capital-intelligence/AC-003: provenance is not decorative.

    GIVEN a committed snapshot
    WHEN a successor is asserted from a non-newer observation, a rewound ledger
    watermark, a foreign predecessor or a drifted identity
    THEN each is refused
    """

    previous = snapshot()
    with pytest.raises(CapitalIntelligenceError, match="as_of"):
        successor(previous, trigger=CapitalRecomputeTrigger.MARK, freshness=fresh(as_of=T0))
    with pytest.raises(CapitalIntelligenceError, match="as_of"):
        successor(
            previous,
            trigger=CapitalRecomputeTrigger.MARK,
            freshness=fresh(as_of=T0 - timedelta(seconds=60)),
        )
    with pytest.raises(CapitalIntelligenceError, match="derived_from"):
        successor(previous, trigger=CapitalRecomputeTrigger.MARK, derived_from="CPSN:other")
    with pytest.raises(CapitalIntelligenceError, match="drift"):
        successor(previous, trigger=CapitalRecomputeTrigger.MARK, currency="EUR")
    with pytest.raises(CapitalIntelligenceError, match="drift"):
        successor(previous, trigger=CapitalRecomputeTrigger.MARK, account="PAPER-LEDGER-02")
    with pytest.raises(CapitalIntelligenceError, match="drift"):
        successor(previous, trigger=CapitalRecomputeTrigger.MARK, engine="other-engine-v1")


def test_ac_003_each_trigger_obeys_its_declared_cash_law() -> None:
    """ATDD-R5-C-capital-intelligence/AC-003: cash moves only as declared.

    GIVEN the declared cash law of each recompute trigger
    WHEN a reservation, a fee, a mark and a reconciliation recompute are asserted
    THEN a partition move preserves the total, a fee may only decrease it with a
    declared fee movement and a newer watermark, a mark preserves the partitions,
    and only a reconciled successor may declare reconciliation
    """

    previous = snapshot()
    assert (
        CAPITAL_RECOMPUTE_CASH_LAWS[CapitalRecomputeTrigger.RESERVATION]
        is CapitalCashLaw.PRESERVE_TOTAL
    )
    assert (
        CAPITAL_RECOMPUTE_CASH_LAWS[CapitalRecomputeTrigger.FEE]
        is CapitalCashLaw.MAY_ONLY_DECREASE
    )
    assert (
        CAPITAL_RECOMPUTE_CASH_LAWS[CapitalRecomputeTrigger.MARK]
        is CapitalCashLaw.PRESERVE_PARTITIONS
    )

    moved = successor(
        previous,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
    )
    assert_snapshot_recomputes_from(previous, moved, trigger=CapitalRecomputeTrigger.RESERVATION)
    assert moved.cash.cash_total == previous.cash.cash_total
    inflated = successor(
        previous,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="600.00"),
    )
    with pytest.raises(CapitalIntelligenceError, match="cash total"):
        assert_snapshot_recomputes_from(
            previous, inflated, trigger=CapitalRecomputeTrigger.RESERVATION
        )

    charged = successor(
        previous,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9990.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=8),
        source_watermark=watermark(8),
    )
    assert_snapshot_recomputes_from(previous, charged, trigger=CapitalRecomputeTrigger.FEE)
    assert charged.cash.cash_total == Decimal("10490.00")
    undeclared_fee = successor(
        previous,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9990.00", safety="500.00"),
        fee_state=fee_state(fees="0.00", sequence=8),
        source_watermark=watermark(8),
    )
    with pytest.raises(CapitalIntelligenceError, match="fee"):
        assert_snapshot_recomputes_from(
            previous, undeclared_fee, trigger=CapitalRecomputeTrigger.FEE
        )
    credited = successor(
        previous,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="10000.00", blocked="10.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=8),
        source_watermark=watermark(8),
    )
    with pytest.raises(CapitalIntelligenceError, match="cash total"):
        assert_snapshot_recomputes_from(previous, credited, trigger=CapitalRecomputeTrigger.FEE)
    rewound = successor(
        previous,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9990.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=6),
        source_watermark=watermark(6),
    )
    with pytest.raises(CapitalIntelligenceError, match="watermark"):
        assert_snapshot_recomputes_from(previous, rewound, trigger=CapitalRecomputeTrigger.FEE)
    mismatched = successor(
        previous,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9995.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=8),
        source_watermark=watermark(8),
    )
    with pytest.raises(CapitalIntelligenceError, match="fee"):
        assert_snapshot_recomputes_from(previous, mismatched, trigger=CapitalRecomputeTrigger.FEE)

    remarked = successor(
        previous,
        trigger=CapitalRecomputeTrigger.MARK,
        positions=(position(marked="3600.00", marked_at=at(60)),),
        marks=CapitalMarkState(
            marked_at=at(60), mark_source="paper-ledger-mark-v1", marked_position_value="3600.00"
        ),
        source_watermark=watermark(8),
    )
    assert_snapshot_recomputes_from(previous, remarked, trigger=CapitalRecomputeTrigger.MARK)
    assert remarked.cash.to_dict() == previous.cash.to_dict()
    with pytest.raises(CapitalIntelligenceError, match="mark"):
        assert_snapshot_recomputes_from(
            previous,
            successor(previous, trigger=CapitalRecomputeTrigger.MARK),
            trigger=CapitalRecomputeTrigger.MARK,
        )
    with pytest.raises(CapitalIntelligenceError, match="partition"):
        assert_snapshot_recomputes_from(
            previous,
            successor(
                previous,
                trigger=CapitalRecomputeTrigger.MARK,
                cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
            ),
            trigger=CapitalRecomputeTrigger.MARK,
        )

    reconciled = successor(
        previous,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=at(60),
            reference="RECON:2",
            variance="0.00",
        ),
        source_watermark=watermark(8),
    )
    assert_snapshot_recomputes_from(
        previous, reconciled, trigger=CapitalRecomputeTrigger.RECONCILIATION
    )
    unreconciled = successor(
        previous,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.NOT_PERFORMED,
            observed_at=at(60),
            reference="RECON:2",
            variance=None,
        ),
        source_watermark=watermark(8),
    )
    with pytest.raises(CapitalIntelligenceError, match="reconcil"):
        assert_snapshot_recomputes_from(
            previous, unreconciled, trigger=CapitalRecomputeTrigger.RECONCILIATION
        )


# ---------------------------------------------------------------------------
# AC-004 Deployable capital is the tightest declared bound
# ---------------------------------------------------------------------------


def test_ac_004_deployable_capital_is_the_tightest_declared_bound() -> None:
    """ATDD-R5-C-capital-intelligence/AC-004: deployable is a minimum, not a sum.

    GIVEN a bound policy and a snapshot with declared usage
    WHEN deployable capital is evaluated
    THEN it is the tightest remaining bound, the binding bound is named, every
    contributing bound is published, and a limit that has neither a declared cap
    nor declared usage is not evaluated at all
    """

    policy_record = policy()
    view = evaluate_deployable_capital(snapshot(), policy=policy_record, at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.abstention_reason is None
    assert view.deployable_capital == Decimal("100.00")
    assert view.binding_bound is CapitalBound.RISK
    assert view.binding_sleeve is None
    assert view.free_cash == Decimal("10000.00")
    assert view.currency == CURRENCY
    assert view.account == ACCOUNT
    assert view.policy_version == policy_record.policy_version
    assert view.snapshot_id == snapshot().snapshot_id
    assert view.evaluated_at == at(60)
    assert view.authoritative is False
    assert view.to_dict()["deployable_capital"] == "100.00"
    assert view.bounds == {
        CapitalBound.FREE_CASH: Decimal("10000.00"),
        CapitalBound.RISK: Decimal("100.00"),
        CapitalBound.CONCENTRATION: Decimal("1600.00"),
        CapitalBound.LIQUIDITY: Decimal("3100.00"),
        CapitalBound.SLEEVE: Decimal("2000.00"),
    }
    assert CapitalBound.HORIZON not in view.bounds
    assert view.over_limit_bounds == ()
    assert view.deployable_capital <= view.free_cash
    assert CAPITAL_BOUND_PRECEDENCE == (
        CapitalBound.FREE_CASH,
        CapitalBound.RISK,
        CapitalBound.CONCENTRATION,
        CapitalBound.LIQUIDITY,
        CapitalBound.HORIZON,
        CapitalBound.SLEEVE,
    )


def test_ac_004_free_cash_is_a_hard_ceiling() -> None:
    """ATDD-R5-C-capital-intelligence/AC-004: cash may never be exceeded.

    GIVEN a snapshot whose free cash is tighter than every configured limit
    WHEN deployable capital is evaluated, including a tie between bounds
    THEN free cash binds, and a tie is resolved by the declared bound precedence
    """

    poor = snapshot(cash_ledger=cash(free="50.00", safety="500.00"))
    view = evaluate_deployable_capital(poor, policy=policy(), at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.deployable_capital == Decimal("50.00")
    assert view.binding_bound is CapitalBound.FREE_CASH
    tied = snapshot(
        cash_ledger=cash(free="100.00", safety="500.00"),
        limit_usage=(CapitalLimitUsage(kind=CapitalLimitKind.RISK, used_capital="900.00", as_of=T0),),
    )
    tied_view = evaluate_deployable_capital(tied, policy=policy(), at_time=at(60))
    assert tied_view.bounds[CapitalBound.FREE_CASH] == Decimal("100.00")
    assert tied_view.bounds[CapitalBound.RISK] == Decimal("100.00")
    assert tied_view.deployable_capital == Decimal("100.00")
    assert tied_view.binding_bound is CapitalBound.FREE_CASH


def test_ac_004_a_sleeve_limit_can_bind_independently() -> None:
    """ATDD-R5-C-capital-intelligence/AC-004: sleeves bound on their own.

    GIVEN declared sleeve usage
    WHEN one or more sleeves approach their declared caps
    THEN the tightest sleeve bound participates in the minimum, the binding sleeve
    is named, and a sleeve with usage but no declared cap abstains
    """

    full = snapshot(
        sleeve_usage=(SleeveUsage(sleeve="TACTICAL", used_capital="5000.00", as_of=T0),)
    )
    view = evaluate_deployable_capital(full, policy=policy(), at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.bounds[CapitalBound.SLEEVE] == Decimal("0.00")
    assert view.deployable_capital == Decimal("0.00")
    assert view.binding_bound is CapitalBound.SLEEVE
    assert view.binding_sleeve == "TACTICAL"
    two_sleeves = snapshot(
        sleeve_usage=(
            SleeveUsage(sleeve="TACTICAL", used_capital="3000.00", as_of=T0),
            SleeveUsage(sleeve="CORE", used_capital="150.00", as_of=T0),
        )
    )
    two_view = evaluate_deployable_capital(
        two_sleeves,
        policy=policy(sleeve_limits={"TACTICAL": "5000.00", "CORE": "200.00"}),
        at_time=at(60),
    )
    assert two_view.bounds[CapitalBound.SLEEVE] == Decimal("50.00")
    assert two_view.binding_bound is CapitalBound.SLEEVE
    assert two_view.binding_sleeve == "CORE"
    assert two_view.deployable_capital == Decimal("50.00")
    undeclared_sleeve = snapshot(
        sleeve_usage=(SleeveUsage(sleeve="CORE", used_capital="100.00", as_of=T0),)
    )
    undeclared_view = evaluate_deployable_capital(
        undeclared_sleeve, policy=policy(), at_time=at(60)
    )
    assert undeclared_view.abstention_reason is CapitalDeployableAbstentionReason.SLEEVE_LIMIT_NOT_CONFIGURED
    assert undeclared_view.missing_policy_inputs == ("CORE",)


def test_ac_004_a_missing_cap_abstains_instead_of_being_unlimited() -> None:
    """ATDD-R5-C-capital-intelligence/AC-004: absent policy is not unlimited.

    GIVEN a limit or sleeve with declared usage but no configured cap
    WHEN deployable capital is evaluated
    THEN it abstains, names the missing input and publishes no number, and the
    shipped policy ships no unratified numbers at all
    """

    undeclared_limit = policy(
        limit_caps={CapitalLimitKind.CONCENTRATION: "2500.00", CapitalLimitKind.LIQUIDITY: "4000.00"}
    )
    view = evaluate_deployable_capital(snapshot(), policy=undeclared_limit, at_time=at(60))
    assert view.status is CapitalDeployableStatus.ABSTAINED
    assert view.abstention_reason is CapitalDeployableAbstentionReason.LIMIT_NOT_CONFIGURED
    assert view.missing_policy_inputs == ("RISK",)
    assert view.deployable_capital is None
    assert view.binding_bound is None
    assert view.bounds == {}
    no_sleeve_limit = policy(sleeve_limits={})
    sleeve_view = evaluate_deployable_capital(snapshot(), policy=no_sleeve_limit, at_time=at(60))
    assert sleeve_view.status is CapitalDeployableStatus.ABSTAINED
    assert sleeve_view.abstention_reason is CapitalDeployableAbstentionReason.SLEEVE_LIMIT_NOT_CONFIGURED
    assert sleeve_view.missing_policy_inputs == ("TACTICAL",)
    assert sleeve_view.deployable_capital is None
    shipped = capital_deployable_engineering_scenario_policy()
    assert shipped.limit_caps == {}
    assert shipped.sleeve_limits == {}
    assert shipped.account is None
    assert shipped.currency is None
    shipped_view = evaluate_deployable_capital(snapshot(), policy=shipped, at_time=at(60))
    assert shipped_view.status is CapitalDeployableStatus.ABSTAINED
    assert shipped_view.abstention_reason is CapitalDeployableAbstentionReason.IDENTITY_NOT_BOUND
    assert shipped_view.deployable_capital is None


def test_ac_004_bounds_are_exact_and_never_negative() -> None:
    """ATDD-R5-C-capital-intelligence/AC-004: the arithmetic is exact.

    GIVEN usage that exceeds a configured cap and usage that is one cent below it
    WHEN deployable capital is evaluated
    THEN an exceeded cap is reported as zero with the over-limit bound named rather
    than a negative number, and the remaining bound is exact to the cent
    """

    over = policy(
        limit_caps={
            CapitalLimitKind.RISK: "500.00",
            CapitalLimitKind.CONCENTRATION: "2500.00",
            CapitalLimitKind.LIQUIDITY: "4000.00",
        }
    )
    view = evaluate_deployable_capital(snapshot(), policy=over, at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.bounds[CapitalBound.RISK] == Decimal("0.00")
    assert view.over_limit_bounds == (CapitalBound.RISK,)
    assert view.deployable_capital == Decimal("0.00")
    assert view.binding_bound is CapitalBound.RISK
    near = snapshot(
        limit_usage=(
            CapitalLimitUsage(
                kind=CapitalLimitKind.RISK, used_capital="999.99", as_of=T0
            ),
        ),
        sleeve_usage=(SleeveUsage(sleeve="TACTICAL", used_capital="2999.99", as_of=T0),),
    )
    near_view = evaluate_deployable_capital(near, policy=policy(), at_time=at(60))
    assert near_view.bounds[CapitalBound.RISK] == Decimal("0.01")
    assert str(near_view.bounds[CapitalBound.RISK]) == "0.01"
    assert near_view.bounds[CapitalBound.SLEEVE] == Decimal("2000.01")
    assert near_view.deployable_capital == Decimal("0.01")
    assert near_view.binding_bound is CapitalBound.RISK
    assert near_view.over_limit_bounds == ()


def policy(**overrides):
    base = {
        "account": ACCOUNT,
        "environment": CapitalEnvironment.PAPER_LEDGER,
        "engine": ENGINE,
        "currency": CURRENCY,
        "limit_caps": {
            CapitalLimitKind.RISK: "1000.00",
            CapitalLimitKind.CONCENTRATION: "2500.00",
            CapitalLimitKind.LIQUIDITY: "4000.00",
        },
        "sleeve_limits": {"TACTICAL": "5000.00"},
    }
    base.update(overrides)
    return capital_deployable_engineering_scenario_policy(**base)


def plan(
    *,
    version: str = VERSION,
    amount: float = 1000.0,
    candidate: str = "PCAND:R5C:1",
    symbol: str = "ETHUSDT",
    created_at: datetime | None = None,
    window: str = "PWIN:R5C:1",
) -> PortfolioReservationPlan:
    created = created_at or T0
    return PortfolioReservationPlan(
        portfolio_version=version,
        window_identity=window,
        created_at=created,
        reservations=(
            PortfolioReservation(
                candidate_id=candidate,
                symbol=symbol,
                direction=PortfolioDirection.LONG,
                reserved_capital=amount,
                portfolio_version=version,
                created_at=created,
                expires_at=created + timedelta(hours=1),
            ),
        ),
    )


def check(
    record: CapitalSnapshot,
    *,
    policy_record: CapitalDeployablePolicy | None = None,
    at_time: datetime | None = None,
) -> CapitalDeployableView:
    """Evaluate deployable capital for a snapshot at a declared instant."""

    return evaluate_deployable_capital(
        record,
        policy=policy_record if policy_record is not None else policy(),
        at_time=at_time if at_time is not None else record.freshness.as_of + timedelta(seconds=60),
    )


def hours_policy(
    endpoint: CapitalHoursEndpointPolicy | None = CapitalHoursEndpointPolicy.STEP_START,
):
    return capital_hours_engineering_scenario_policy(endpoint_policy=endpoint)


def points(*spec: tuple[int, str]) -> tuple[CapitalOccupancyPoint, ...]:
    return tuple(
        CapitalOccupancyPoint(
            at=at(seconds),
            allocated_basis=Decimal(basis),
            cumulative_fees=Decimal("0.00"),
            cumulative_carry=Decimal("0.00"),
        )
        for seconds, basis in spec
    )


# ---------------------------------------------------------------------------
# AC-001 One partitioned capital record carrying every frozen record key
# ---------------------------------------------------------------------------


def test_ac_001_the_snapshot_carries_every_frozen_record_key() -> None:
    """ATDD-R5-C-capital-intelligence/AC-001: the frozen record is complete.

    GIVEN the frozen `SC-06` capital snapshot record
    WHEN a snapshot is built and exported to its durable form
    THEN it declares every frozen record key, its identity is the declared
    deterministic prefix, it is one record for one portfolio version, and it is
    never authoritative
    """

    record = snapshot()
    payload = record.to_dict()
    assert set(CAPITAL_SNAPSHOT_RECORD_KEYS) == {
        "account/environment/engine",
        "currency",
        "cash partitions",
        "positions",
        "liabilities if supported",
        "marks",
        "sleeve/risk usage",
        "fee state",
        "source watermark",
        "reconciliation",
        "freshness",
    }
    for frozen_key, durable_keys in CAPITAL_SNAPSHOT_RECORD_KEYS.items():
        assert durable_keys, frozen_key
        for durable_key in durable_keys:
            assert durable_key in payload, (frozen_key, durable_key)
    assert record.portfolio_version == VERSION
    assert record.account == ACCOUNT
    assert record.environment is CapitalEnvironment.PAPER_LEDGER
    assert record.engine == ENGINE
    assert record.currency == CURRENCY
    assert record.snapshot_id == capital_snapshot_identity(record)
    assert record.snapshot_id.startswith(f"{CAPITAL_SNAPSHOT_ID_PREFIX}:")
    assert record.authoritative is False
    assert payload["authoritative"] is False


def test_ac_001_the_cash_partitions_reconstruct_the_cash_total() -> None:
    """ATDD-R5-C-capital-intelligence/AC-001: cash is partitioned, not scalar.

    GIVEN the four declared cash partitions
    WHEN a ledger is built and when a partition is missing, unknown or does not
    reconstruct the declared total
    THEN the complete declaration is accepted and every incomplete or
    contradictory one fails closed
    """

    ledger = cash(free="1000.00", reserves="250.00", safety="100.00", blocked="50.00")
    assert ledger.cash_total == Decimal("1400.00")
    assert set(ledger.partitions) == {
        CapitalPartition.FREE_CASH,
        CapitalPartition.ORDER_RESERVES,
        CapitalPartition.SAFETY_RESERVE,
        CapitalPartition.OTHER_BLOCKED_CASH,
    }
    assert ledger.amount(CapitalPartition.ORDER_RESERVES) == Decimal("250.00")
    assert ledger.free_cash == Decimal("1000.00")
    assert ledger.has_negative_amount is False
    with pytest.raises(CapitalIntelligenceError, match="declared cash partitions"):
        CashPartitionLedger(partitions={CapitalPartition.FREE_CASH: "1.00"}, cash_total="1.00")
    with pytest.raises(CapitalIntelligenceError):
        CashPartitionLedger(
            partitions={
                CapitalPartition.FREE_CASH: "1.00",
                CapitalPartition.ORDER_RESERVES: "0.00",
                CapitalPartition.SAFETY_RESERVE: "0.00",
                "CASH": "0.00",
            },
            cash_total="1.00",
        )
    with pytest.raises(CapitalIntelligenceError, match="reconstruct"):
        CashPartitionLedger(
            partitions={
                CapitalPartition.FREE_CASH: "1.00",
                CapitalPartition.ORDER_RESERVES: "0.00",
                CapitalPartition.SAFETY_RESERVE: "0.00",
                CapitalPartition.OTHER_BLOCKED_CASH: "0.00",
            },
            cash_total="2.00",
        )


def test_ac_001_the_snapshot_round_trips_and_a_forgery_fails_closed() -> None:
    """ATDD-R5-C-capital-intelligence/AC-001: durable fidelity, no forgery.

    GIVEN a durable capital snapshot payload
    WHEN it is reconstructed and when its content, its key set or its identity is
    tampered with
    THEN an identical record is rebuilt and every tampered payload is refused
    """

    record = snapshot()
    payload = record.to_dict()
    rebuilt = CapitalSnapshot.from_dict(payload)
    assert rebuilt.to_dict() == payload
    assert rebuilt.snapshot_id == record.snapshot_id
    extra = dict(payload)
    extra["free_cash"] = "999999.00"
    with pytest.raises(CapitalIntelligenceError):
        CapitalSnapshot.from_dict(extra)
    forged = dict(payload)
    forged["snapshot_id"] = "CPSN:forged"
    with pytest.raises(CapitalIntelligenceError, match="snapshot_id"):
        CapitalSnapshot.from_dict(forged)
    missing = {key: value for key, value in payload.items() if key != "marks"}
    with pytest.raises(CapitalIntelligenceError):
        CapitalSnapshot.from_dict(missing)


def test_ac_001_there_is_exactly_one_cash_book_in_the_application() -> None:
    """ATDD-R5-C-capital-intelligence/AC-001: extend the owner, add no second book.

    GIVEN every module under `app/`
    WHEN the capital-record surface is scanned
    THEN only the frozen owning module declares it, it reuses the frozen portfolio
    owner instead of forking a second capital book, and no second partition or
    snapshot family is introduced
    """

    owner = "app/services/capital_rotation_intelligence.py"
    carriers: list[str] = []
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(APP_ROOT).as_posix()
        source = module_source(path)
        if relative == owner:
            assert "class CapitalSnapshot" in source
            assert "class CashPartitionLedger" in source
            continue
        if "CapitalSnapshot" in source or "CashPartitionLedger" in source:
            carriers.append(relative)
    assert carriers == []
    source = r5c_source()
    assert "from app.opip.contracts.portfolio import" in source
    assert "PortfolioCapitalState" in source
    assert "assert_reservation_plan_current" in source
    assert "class PortfolioCapitalState" not in source


# ---------------------------------------------------------------------------
# AC-002 Exact accounting laws
# ---------------------------------------------------------------------------


def test_ac_002_equity_is_cash_plus_declared_marked_value() -> None:
    """ATDD-R5-C-capital-intelligence/AC-002: the equity law is exact.

    GIVEN a supported paper account with no liability surface
    WHEN equity is derived
    THEN it equals cash plus declared marked position value, liabilities are
    declared unsupported and cannot be smuggled in, and the record reports that
    unsupported surface explicitly
    """

    record = snapshot()
    assert CAPITAL_LIABILITIES_SUPPORTED is False
    assert CAPITAL_RECEIVABLES_SUPPORTED is False
    assert record.liabilities_total == Decimal("0.00")
    assert record.cash.cash_total == Decimal("10500.00")
    assert record.marks.marked_position_value == Decimal("3120.00")
    assert record.equity == Decimal("13620.00")
    payload = record.to_dict()
    assert payload["liabilities"] == []
    assert payload["liabilities_supported"] is False
    empty = snapshot(
        positions=(),
        marks=CapitalMarkState(
            marked_at=T0, mark_source="paper-ledger-mark-v1", marked_position_value="0.00"
        ),
    )
    assert empty.deployed_basis_capital == Decimal("0.00")
    assert empty.equity == empty.cash.cash_total


def test_ac_002_the_marked_aggregate_reconciles_to_the_positions() -> None:
    """ATDD-R5-C-capital-intelligence/AC-002: no orphan marked value.

    GIVEN declared positions and one marked aggregate
    WHEN the aggregate, a position mark instant or the position set disagrees
    THEN the disagreement is refused instead of reconciled silently
    """

    with pytest.raises(CapitalIntelligenceError, match="reconcile"):
        snapshot(
            marks=CapitalMarkState(
                marked_at=T0,
                mark_source="paper-ledger-mark-v1",
                marked_position_value="3100.00",
            )
        )
    with pytest.raises(CapitalIntelligenceError, match="mark"):
        snapshot(positions=(position(marked_at=at(60)),))
    with pytest.raises(CapitalIntelligenceError, match="symbol"):
        snapshot(positions=(position(), position()))
    two = snapshot(
        positions=(position(), position(symbol="ETHUSDT", basis="1000.00", marked="1010.00")),
        marks=CapitalMarkState(
            marked_at=T0, mark_source="paper-ledger-mark-v1", marked_position_value="4130.00"
        ),
    )
    assert two.deployed_basis_capital == Decimal("4000.00")
    assert two.marks.marked_position_value == Decimal("4130.00")


def test_ac_002_deployed_basis_and_marked_value_stay_outside_cash() -> None:
    """ATDD-R5-C-capital-intelligence/AC-002: cash is never derived from marks.

    GIVEN a snapshot with a deployed position
    WHEN the cash book and the deployed basis are compared
    THEN deployed basis and marked value are reported separately from cash and the
    cash partitions stay independent of position value
    """

    record = snapshot()
    assert record.deployed_basis_capital == Decimal("3000.00")
    assert record.deployed_basis_capital != record.cash.cash_total
    assert (
        sum((record.cash.amount(partition) for partition in CapitalPartition), Decimal("0.00"))
        == record.cash.cash_total
    )
    revalued = snapshot(
        positions=(position(marked="3600.00"),),
        marks=CapitalMarkState(
            marked_at=T0, mark_source="paper-ledger-mark-v1", marked_position_value="3600.00"
        ),
    )
    assert revalued.cash.to_dict() == record.cash.to_dict()
    assert revalued.deployed_basis_capital == record.deployed_basis_capital
    assert revalued.equity != record.equity
    assert revalued.cash.cash_total == record.cash.cash_total


def test_ac_002_float_and_over_precise_money_is_refused() -> None:
    """ATDD-R5-C-capital-intelligence/AC-002: exact decimals only.

    GIVEN money fields
    WHEN a float, a bool, a non-finite value, a scientific-notation string or a
    value with more precision than the declared money quantum is offered
    THEN each is refused rather than silently rounded, defaulted or coerced
    """

    assert MONEY_QUANTUM == Decimal("0.01")
    for bad in (10000.0, 10000, True, float("nan"), float("inf"), "1e3", "10000.000"):
        with pytest.raises(CapitalIntelligenceError):
            _ledger_with_free(bad)
    assert cash(free="10000.00").free_cash == Decimal("10000.00")
    assert cash(free="-1.00", safety="500.00").has_negative_amount is True
    assert cash(free="10000.00").has_negative_amount is False
    with pytest.raises(CapitalIntelligenceError):
        snapshot(
            positions=(position(basis="3000.000"),),
            marks=CapitalMarkState(
                marked_at=T0, mark_source="paper-ledger-mark-v1", marked_position_value="3120.00"
            ),
        )
    with pytest.raises(CapitalIntelligenceError):
        CapitalPosition(
            symbol="BTCUSDT",
            quantity=0.1,
            basis_capital="3000.00",
            marked_value="3120.00",
            marked_at=T0,
            mark_reference="MARK:BTCUSDT:1",
        )
    with pytest.raises(CapitalIntelligenceError):
        CapitalPosition(
            symbol="BTCUSDT",
            quantity=Decimal("-0.1"),
            basis_capital="3000.00",
            marked_value="3120.00",
            marked_at=T0,
            mark_reference="MARK:BTCUSDT:1",
        )
    with pytest.raises(CapitalIntelligenceError):
        CapitalPosition(
            symbol="",
            quantity=Decimal("0.1"),
            basis_capital="3000.00",
            marked_value="3120.00",
            marked_at=T0,
            mark_reference="MARK:BTCUSDT:1",
        )


def _ledger_with_free(value: object) -> CashPartitionLedger:
    return CashPartitionLedger(
        partitions={
            CapitalPartition.FREE_CASH: value,
            CapitalPartition.ORDER_RESERVES: "0.00",
            CapitalPartition.SAFETY_RESERVE: "0.00",
            CapitalPartition.OTHER_BLOCKED_CASH: "0.00",
        },
        cash_total="0.00",
    )


# ---------------------------------------------------------------------------
# AC-005 Abstention precedence, identity and no invented number
# ---------------------------------------------------------------------------


def test_ac_005_a_stale_or_unsupported_account_state_abstains() -> None:
    """ATDD-R5-C-capital-intelligence/AC-005: unknown account state abstains.

    GIVEN an account state that is stale, undetermined or an accounting state the
    engine does not support
    WHEN deployable capital is evaluated
    THEN each abstains with the declared reason and publishes no number
    """

    assert CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE[0] is CapitalDeployableAbstentionReason.IDENTITY_NOT_BOUND
    assert CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE[-1] is CapitalDeployableAbstentionReason.SLEEVE_LIMIT_NOT_CONFIGURED
    assert len(set(CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE)) == len(CapitalDeployableAbstentionReason)
    stale = check(snapshot(source_watermark=watermark(7, T0, True)))
    assert stale.status is CapitalDeployableStatus.ABSTAINED
    assert stale.abstention_reason is CapitalDeployableAbstentionReason.ACCOUNT_STATE_STALE
    assert stale.deployable_capital is None
    undetermined = check(snapshot(source_watermark=watermark(7, T0, None)))
    assert undetermined.abstention_reason is CapitalDeployableAbstentionReason.ACCOUNT_STATE_STALE
    unsupported = check(snapshot(accounting_state=CapitalAccountingState.UNSUPPORTED))
    assert unsupported.abstention_reason is CapitalDeployableAbstentionReason.UNSUPPORTED_ACCOUNTING_STATE
    assert set(CapitalDeployableStatus) == {
        CapitalDeployableStatus.AVAILABLE,
        CapitalDeployableStatus.ABSTAINED,
    }


def test_ac_005_reconciliation_and_balance_failures_abstain() -> None:
    """ATDD-R5-C-capital-intelligence/AC-005: broken evidence abstains.

    GIVEN a failed reconciliation, a reconciliation that was never performed, a
    negative partition balance and an undeclared fee state
    WHEN deployable capital is evaluated
    THEN each abstains with its declared reason instead of being netted out
    """

    failed = snapshot(
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.FAILED,
            observed_at=T0,
            reference="RECON:1",
            variance="12.00",
        )
    )
    failed_view = check(failed)
    assert failed_view.abstention_reason is CapitalDeployableAbstentionReason.FAILED_RECONCILIATION
    assert failed_view.deployable_capital is None
    skipped = snapshot(
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.NOT_PERFORMED,
            observed_at=T0,
            reference="RECON:1",
            variance=None,
        )
    )
    assert (
        check(skipped).abstention_reason is CapitalDeployableAbstentionReason.RECONCILIATION_NOT_PERFORMED
    )
    negative = snapshot(cash_ledger=cash(free="-1.00", safety="500.00"), fee_state=None)
    negative_view = check(negative)
    assert negative_view.abstention_reason is CapitalDeployableAbstentionReason.UNRESOLVED_NEGATIVE_BALANCE
    assert negative_view.deployable_capital is None
    assert CapitalSnapshot.from_dict(negative.to_dict()).snapshot_id == negative.snapshot_id
    unfunded = snapshot(fee_state=None)
    unfunded_view = check(unfunded)
    assert unfunded_view.abstention_reason is CapitalDeployableAbstentionReason.MISSING_FEES
    assert unfunded_view.to_dict()["deployable_capital"] is None
    with pytest.raises(CapitalIntelligenceError, match="variance"):
        CapitalReconciliation(
            status=CapitalReconciliationStatus.FAILED,
            observed_at=T0,
            reference="RECON:1",
            variance=None,
        )


def test_ac_005_an_outlived_or_incomplete_snapshot_abstains() -> None:
    """ATDD-R5-C-capital-intelligence/AC-005: freshness gates the number.

    GIVEN a snapshot whose validity deadline has passed or whose evidence is
    explicitly incomplete, and a snapshot observed exactly at its deadline
    WHEN deployable capital is evaluated
    THEN the outlived and incomplete snapshots abstain while the deadline instant
    itself is still valid
    """

    expiring = snapshot(
        freshness=CapitalFreshness(as_of=T0, validity_deadline=at(60), evidence_complete=True)
    )
    at_deadline = check(expiring, at_time=at(60))
    assert at_deadline.status is CapitalDeployableStatus.AVAILABLE
    assert at_deadline.deployable_capital == Decimal("100.00")
    outlived = check(expiring, at_time=at(61))
    assert outlived.status is CapitalDeployableStatus.ABSTAINED
    assert outlived.abstention_reason is CapitalDeployableAbstentionReason.SNAPSHOT_OUTLIVED_VALIDITY
    assert outlived.deployable_capital is None
    incomplete = snapshot(
        freshness=CapitalFreshness(as_of=T0, validity_deadline=at(900), evidence_complete=False)
    )
    incomplete_view = check(incomplete)
    assert incomplete_view.abstention_reason is CapitalDeployableAbstentionReason.EVIDENCE_INCOMPLETE
    assert incomplete_view.deployable_capital is None
    with pytest.raises(CapitalIntelligenceError, match="deadline"):
        CapitalFreshness(as_of=T0, validity_deadline=T0, evidence_complete=True)


def test_ac_005_identity_must_be_bound_and_match() -> None:
    """ATDD-R5-C-capital-intelligence/AC-005: identity is bound, not assumed.

    GIVEN an unbound policy, a currency mismatch, an engine, account or environment
    drift and a live-observation snapshot
    WHEN deployable capital is evaluated against a paper-ledger policy
    THEN each abstains, the unbound inputs are named, and a live observation can
    never fund a paper account
    """

    unbound = capital_deployable_engineering_scenario_policy()
    unbound_view = check(snapshot(), policy_record=unbound)
    assert unbound_view.status is CapitalDeployableStatus.ABSTAINED
    assert unbound_view.abstention_reason is CapitalDeployableAbstentionReason.IDENTITY_NOT_BOUND
    assert unbound_view.missing_policy_inputs == ("account", "environment", "engine", "currency")
    cross_currency = check(snapshot(), policy_record=policy(currency="EUR"))
    assert cross_currency.abstention_reason is CapitalDeployableAbstentionReason.CURRENCY_MISMATCH
    drifted_engine = check(snapshot(), policy_record=policy(engine="other-engine-v1"))
    assert drifted_engine.abstention_reason is CapitalDeployableAbstentionReason.IDENTITY_MISMATCH
    drifted_account = check(snapshot(), policy_record=policy(account="PAPER-LEDGER-02"))
    assert drifted_account.abstention_reason is CapitalDeployableAbstentionReason.IDENTITY_MISMATCH
    shadow = snapshot(environment=CapitalEnvironment.SHADOW_EVIDENCE)
    shadow_view = check(shadow)
    assert shadow_view.abstention_reason is CapitalDeployableAbstentionReason.IDENTITY_MISMATCH
    live = snapshot(environment=CapitalEnvironment.LIVE_OBSERVATION)
    live_view = check(live)
    assert (
        live_view.abstention_reason
        is CapitalDeployableAbstentionReason.LIVE_OBSERVATION_CANNOT_FUND_PAPER_ACCOUNT
    )
    for view in (
        unbound_view,
        cross_currency,
        drifted_engine,
        drifted_account,
        shadow_view,
        live_view,
    ):
        assert view.deployable_capital is None
        assert view.bounds == {}
        assert view.binding_bound is None
        assert view.binding_sleeve is None
        assert view.over_limit_bounds == ()
        assert view.to_dict()["status"] == "ABSTAINED"


def test_ac_005_the_declared_precedence_decides_and_publishes_nothing() -> None:
    """ATDD-R5-C-capital-intelligence/AC-005: one ordered abstention law.

    GIVEN a snapshot that violates several abstention conditions at once
    WHEN deployable capital is evaluated
    THEN the first applicable declared reason wins, the ordering is exactly the
    declared precedence, and the abstention publishes no bound, no binding and no
    number
    """

    order = CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE
    assert order.index(CapitalDeployableAbstentionReason.CURRENCY_MISMATCH) < order.index(
        CapitalDeployableAbstentionReason.LIVE_OBSERVATION_CANNOT_FUND_PAPER_ACCOUNT
    )
    assert order.index(CapitalDeployableAbstentionReason.LIVE_OBSERVATION_CANNOT_FUND_PAPER_ACCOUNT) < order.index(
        CapitalDeployableAbstentionReason.IDENTITY_MISMATCH
    )
    for earlier, later in (
        (CapitalDeployableAbstentionReason.IDENTITY_MISMATCH, CapitalDeployableAbstentionReason.UNSUPPORTED_ACCOUNTING_STATE),
        (CapitalDeployableAbstentionReason.UNSUPPORTED_ACCOUNTING_STATE, CapitalDeployableAbstentionReason.ACCOUNT_STATE_STALE),
        (CapitalDeployableAbstentionReason.ACCOUNT_STATE_STALE, CapitalDeployableAbstentionReason.FAILED_RECONCILIATION),
        (CapitalDeployableAbstentionReason.FAILED_RECONCILIATION, CapitalDeployableAbstentionReason.RECONCILIATION_NOT_PERFORMED),
        (CapitalDeployableAbstentionReason.RECONCILIATION_NOT_PERFORMED, CapitalDeployableAbstentionReason.UNRESOLVED_NEGATIVE_BALANCE),
        (CapitalDeployableAbstentionReason.UNRESOLVED_NEGATIVE_BALANCE, CapitalDeployableAbstentionReason.MISSING_FEES),
        (CapitalDeployableAbstentionReason.MISSING_FEES, CapitalDeployableAbstentionReason.SNAPSHOT_OUTLIVED_VALIDITY),
        (CapitalDeployableAbstentionReason.SNAPSHOT_OUTLIVED_VALIDITY, CapitalDeployableAbstentionReason.EVIDENCE_INCOMPLETE),
        (CapitalDeployableAbstentionReason.EVIDENCE_INCOMPLETE, CapitalDeployableAbstentionReason.LIMIT_NOT_CONFIGURED),
        (CapitalDeployableAbstentionReason.LIMIT_NOT_CONFIGURED, CapitalDeployableAbstentionReason.SLEEVE_LIMIT_NOT_CONFIGURED),
    ):
        assert order.index(earlier) < order.index(later), (earlier, later)
    both = snapshot(
        source_watermark=watermark(7, T0, True),
        accounting_state=CapitalAccountingState.UNSUPPORTED,
    )
    assert (
        check(both).abstention_reason is CapitalDeployableAbstentionReason.UNSUPPORTED_ACCOUNTING_STATE
    )
    worst = snapshot(
        cash_ledger=cash(free="-5.00", safety="500.00"),
        fee_state=None,
        source_watermark=watermark(7, T0, True),
        freshness=CapitalFreshness(as_of=T0, validity_deadline=at(30), evidence_complete=False),
    )
    view = check(worst, at_time=at(60))
    assert view.abstention_reason is CapitalDeployableAbstentionReason.ACCOUNT_STATE_STALE
    payload = view.to_dict()
    assert payload["deployable_capital"] is None
    assert payload["abstention_reason"] == "ACCOUNT_STATE_STALE"
    assert payload["bounds"] == {}
    assert payload["binding_bound"] is None
    assert payload["binding_sleeve"] is None
    assert payload["authoritative"] is False


# ---------------------------------------------------------------------------
# AC-006 Atomic compare-and-commit reservation (`CI-04`)
# ---------------------------------------------------------------------------


def commit_fixture(account: str, **overrides) -> CapitalSnapshot:
    """A snapshot on its own account, so one commit witness cannot cross tests."""

    payload: dict[str, object] = {"account": account}
    payload.update(overrides)
    return snapshot(**payload)  # type: ignore[arg-type]


def test_ac_006_a_plan_commits_atomically_against_the_version_it_was_built_for() -> None:
    """ATDD-R5-C-capital-intelligence/AC-006: one all-or-nothing consumption.

    GIVEN a current capital snapshot and a reservation plan bound to its version
    WHEN the plan is committed
    THEN the plan's capital moves from free cash into order reserves, the declared
    cash total is preserved, the committed amount is recorded, the successor is the
    declared next version of the same lineage, and the frozen portfolio owner's own
    capital-state seam follows the committed record
    """

    record = commit_fixture("PAPER-LEDGER-R5C-COMMIT-1")
    reservation_plan = plan(version=record.portfolio_version)
    result = commit_reservation(record, plan=reservation_plan, at_time=at(60))

    assert result.status is CapitalCommitStatus.COMMITTED
    assert result.rejection is None
    assert result.commit_id.startswith(f"{CAPITAL_COMMIT_ID_PREFIX}:")
    assert result.applied_portfolio_version == record.portfolio_version
    assert result.successor_version == "pv-r5c-0001.0001"
    assert result.committed_capital == Decimal("1000.00")
    successor = result.snapshot
    assert successor is not None
    assert result.snapshot_id == successor.snapshot_id
    assert successor.cash.amount(CapitalPartition.FREE_CASH) == Decimal("9000.00")
    assert successor.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("1000.00")
    assert successor.cash.amount(CapitalPartition.SAFETY_RESERVE) == Decimal("500.00")
    assert successor.cash.cash_total == record.cash.cash_total
    assert successor.derived_from == record.snapshot_id
    assert successor.recompute_trigger is CapitalRecomputeTrigger.RESERVATION
    assert reservation_plan.plan_id in successor.applied_reservation_references
    assert_snapshot_recomputes_from(
        record, successor, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    payload = result.to_dict()
    assert payload["commit_schema_version"] == CAPITAL_COMMIT_SCHEMA_VERSION
    assert payload["status"] == "COMMITTED"
    assert payload["committed_capital"] == "1000.00"
    assert payload["authoritative"] is False

    state = to_portfolio_capital_state(record)
    assert state.portfolio_version == record.portfolio_version
    assert state.available_capital == float(record.cash.free_cash)
    assert state.currency == record.currency
    assert state.as_of == record.freshness.as_of
    assert state.capital_state_fingerprint.startswith("PCAP:")
    committed_state = to_portfolio_capital_state(successor)
    assert committed_state.portfolio_version == successor.portfolio_version
    assert committed_state.available_capital == float(successor.cash.free_cash)


def test_ac_006_a_rejected_attempt_changes_nothing_at_all() -> None:
    """ATDD-R5-C-capital-intelligence/AC-006: a rejection is not a partial write.

    GIVEN a current snapshot and plans that declare a stale expected version, an
    underfunded amount or a non-positive amount
    WHEN each is committed
    THEN each is rejected with its declared reason, publishes no snapshot, no
    successor version and no consumed capital, and a later valid plan on the same
    version still commits, so nothing was written by the rejections
    """

    record = commit_fixture("PAPER-LEDGER-R5C-COMMIT-2")
    stale = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version),
        at_time=at(60),
        expected_portfolio_version="pv-r5c-0002",
    )
    assert stale.status is CapitalCommitStatus.REJECTED
    assert stale.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert stale.snapshot is None
    assert stale.successor_version is None
    assert stale.committed_capital is None
    assert stale.to_dict()["snapshot_id"] is None
    assert stale.applied_reservation_references == record.applied_reservation_references

    another_version = commit_reservation(
        record,
        plan=plan(version="pv-r5c-0002", candidate="PCAND:R5C:2"),
        at_time=at(60),
    )
    assert another_version.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert another_version.snapshot is None

    underfunded = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, amount=20000.0, candidate="PCAND:R5C:3"),
        at_time=at(60),
    )
    assert underfunded.rejection is CapitalCommitRejection.INSUFFICIENT_FREE_CASH
    assert underfunded.snapshot is None
    assert underfunded.committed_capital is None

    empty = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, amount=0.0, candidate="PCAND:R5C:4"),
        at_time=at(60),
    )
    assert empty.rejection is CapitalCommitRejection.NON_POSITIVE_AMOUNT
    assert empty.snapshot is None

    accepted = commit_reservation(
        record, plan=plan(version=record.portfolio_version, candidate="PCAND:R5C:5"), at_time=at(60)
    )
    assert accepted.status is CapitalCommitStatus.COMMITTED
    assert accepted.snapshot is not None
    assert accepted.snapshot.cash.amount(CapitalPartition.FREE_CASH) == Decimal("9000.00")


def test_ac_006_a_live_book_or_an_undescribable_commit_is_refused() -> None:
    """ATDD-R5-C-capital-intelligence/AC-006: authority and time fail closed.

    GIVEN a live observation, a shadow book, a commit that declares a reason which
    is not a reservation, an outlived snapshot and an observation at the snapshot's
    own instant
    WHEN each is committed
    THEN no environment but the paper ledger may be funded, only the reservation
    reason commits, an outlived snapshot and a rewound instant are refused, and a
    structural misuse raises instead of returning a result
    """

    live = commit_fixture("PAPER-LEDGER-R5C-COMMIT-3", environment=CapitalEnvironment.LIVE_OBSERVATION)
    live_result = commit_reservation(live, plan=plan(version=VERSION), at_time=at(60))
    assert live_result.rejection is CapitalCommitRejection.ENVIRONMENT_NOT_FUNDABLE
    assert live_result.snapshot is None
    shadow = commit_fixture(
        "PAPER-LEDGER-R5C-COMMIT-4", environment=CapitalEnvironment.SHADOW_EVIDENCE
    )
    shadow_result = commit_reservation(shadow, plan=plan(version=VERSION), at_time=at(60))
    assert shadow_result.rejection is CapitalCommitRejection.ENVIRONMENT_NOT_FUNDABLE

    funded = commit_fixture("PAPER-LEDGER-R5C-COMMIT-5")
    for trigger in (
        CapitalRecomputeTrigger.FEE,
        CapitalRecomputeTrigger.DEPOSIT,
        CapitalRecomputeTrigger.APPROVAL,
    ):
        refused = commit_reservation(
            funded, plan=plan(version=VERSION), at_time=at(60), trigger=trigger
        )
        assert refused.rejection is CapitalCommitRejection.UNSUPPORTED_RECOMPUTE_TRIGGER
        assert refused.snapshot is None
    with pytest.raises(CapitalIntelligenceError, match="trigger"):
        commit_reservation(funded, plan=plan(version=VERSION), at_time=at(60), trigger="RESERVATION")

    outlived = commit_reservation(
        funded, plan=plan(version=VERSION), at_time=at(901)
    )
    assert outlived.rejection is CapitalCommitRejection.SNAPSHOT_OUTLIVED_VALIDITY
    with pytest.raises(CapitalIntelligenceError, match="as_of"):
        commit_reservation(funded, plan=plan(version=VERSION), at_time=T0)
    with pytest.raises(CapitalIntelligenceError):
        commit_reservation(funded, plan="PRSV:pasted", at_time=at(60))  # type: ignore[arg-type]
    with pytest.raises(CapitalIntelligenceError):
        commit_reservation("not-a-snapshot", plan=plan(version=VERSION), at_time=at(60))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-007 Concurrency honesty: one winner, no double spend, honest replay
# ---------------------------------------------------------------------------


def test_ac_007_a_replay_of_a_committed_plan_consumes_nothing_twice() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: retries create nothing twice.

    GIVEN a committed reservation of one plan
    WHEN the very same plan is presented again
    THEN the attempt is an idempotent replay of the recorded successor rather than
    a second consumption, and a competing plan written against the consumed version
    is refused, so a retry can never double spend
    """

    record = commit_fixture("PAPER-LEDGER-R5C-CONCURRENCY-1")
    reservation_plan = plan(version=record.portfolio_version)
    first = commit_reservation(record, plan=reservation_plan, at_time=at(60))
    assert first.status is CapitalCommitStatus.COMMITTED

    replay = commit_reservation(record, plan=reservation_plan, at_time=at(60))
    assert replay.status is CapitalCommitStatus.IDEMPOTENT_REPLAY
    assert replay.rejection is None
    assert replay.commit_id == first.commit_id
    assert replay.snapshot_id == first.snapshot_id
    assert replay.committed_capital == first.committed_capital == Decimal("1000.00")
    assert replay.snapshot is not None
    assert replay.snapshot.cash.amount(CapitalPartition.FREE_CASH) == Decimal("9000.00")
    assert replay.snapshot.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("1000.00")
    assert replay.snapshot.cash.cash_total == record.cash.cash_total
    assert len(replay.snapshot.applied_reservation_references) == 1

    competing = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, candidate="PCAND:R5C:COMPETING"),
        at_time=at(60),
    )
    assert competing.status is CapitalCommitStatus.REJECTED
    assert competing.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert competing.snapshot is None


def test_ac_007_a_plan_consumed_at_another_version_is_not_replayed() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: a plan is consumed once per book.

    GIVEN a plan already committed against one version
    WHEN the same plan is offered against the committed successor version
    THEN it is refused as an already-consumed plan instead of being replayed onto
    another version of the ledger
    """

    record = commit_fixture("PAPER-LEDGER-R5C-CONCURRENCY-2")
    reservation_plan = plan(version=record.portfolio_version)
    first = commit_reservation(record, plan=reservation_plan, at_time=at(60))
    successor = first.snapshot
    assert successor is not None
    again = commit_reservation(successor, plan=reservation_plan, at_time=at(120))
    assert again.status is CapitalCommitStatus.REJECTED
    assert again.rejection is CapitalCommitRejection.PLAN_ALREADY_CONSUMED
    assert again.snapshot is None
    assert again.committed_capital is None



def test_ac_007_a_losing_racer_never_double_spends() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: one winner, many honest losers.

    GIVEN two decisions that compete for the same contested cash at the same
    version, started together
    WHEN both are committed concurrently
    THEN exactly one consumes the capacity, the loser is refused without a partial
    write, the contested partition moved exactly once, and the loser can reread the
    committed version and replan successfully
    """

    record = commit_fixture("PAPER-LEDGER-R5C-CONCURRENCY-3")
    racers = (
        plan(version=record.portfolio_version, candidate="PCAND:R5C:RACER-A", amount=1000.0),
        plan(version=record.portfolio_version, candidate="PCAND:R5C:RACER-B", amount=1000.0),
    )
    results: list[CapitalCommitResult] = []
    gate = threading.Barrier(len(racers))

    def race(reservation_plan) -> None:
        gate.wait(timeout=10)
        results.append(commit_reservation(record, plan=reservation_plan, at_time=at(60)))

    threads = [threading.Thread(target=race, args=(row,)) for row in racers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert len(results) == len(racers)
    assert sorted(result.status.value for result in results) == ["COMMITTED", "REJECTED"]
    winner = next(
        result for result in results if result.status is CapitalCommitStatus.COMMITTED
    )
    loser = next(
        result for result in results if result.status is CapitalCommitStatus.REJECTED
    )
    assert loser.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert loser.snapshot is None
    assert loser.committed_capital is None
    assert winner.snapshot is not None
    assert winner.snapshot.cash.amount(CapitalPartition.FREE_CASH) == Decimal("9000.00")
    assert winner.snapshot.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("1000.00")
    assert len(winner.snapshot.applied_reservation_references) == 1

    replanned = commit_reservation(
        winner.snapshot,
        plan=plan(
            version=winner.snapshot.portfolio_version,
            candidate="PCAND:R5C:RACER-C",
            amount=250.0,
        ),
        at_time=at(120),
    )
    assert replanned.status is CapitalCommitStatus.COMMITTED
    assert replanned.successor_version == "pv-r5c-0001.0002"
    assert replanned.snapshot is not None
    assert replanned.snapshot.cash.amount(CapitalPartition.FREE_CASH) == Decimal("8750.00")
    assert replanned.snapshot.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("1250.00")




# ---------------------------------------------------------------------------
# AC-008 Capital-hours is an integral over preserved instants (v1.5.0 section 12)
# ---------------------------------------------------------------------------


def test_ac_008_capital_hours_is_the_declared_basis_integral() -> None:
    """ATDD-R5-C-capital-intelligence/AC-008: capital-hours is an integral.

    GIVEN a declared occupancy series of committed capital on preserved instants
    WHEN the series is measured under a policy that declares its endpoint policy
    THEN capital-hours is the time integral of the declared basis in currency-hours
    - the adopted illustrative example included - the identity is deterministic, and
    the measurement publishes the policy and basis tokens it was taken under
    """

    series = points((0, "2000.00"), (43_200, "2000.00"))
    measured = measure_capital_hours(series, policy=hours_policy())
    assert measured.status is CapitalHoursStatus.MEASURED
    assert measured.capital_hours == Decimal("24000.0000")
    assert measured.cost_capital_hours == Decimal("0.0000")
    assert measured.net_capital_hours == Decimal("24000.0000")
    assert measured.occupancy_points == 2
    assert measured.observed_span_seconds == 43_200
    assert measured.endpoint_policy is CapitalHoursEndpointPolicy.STEP_START
    assert measured.policy_version == CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION
    assert measured.basis_token == CAPITAL_HOURS_BASIS_TOKEN
    assert measured.hours_id.startswith(f"{CAPITAL_HOURS_ID_PREFIX}:")
    assert measured.authoritative is False
    assert measured.to_dict()["capital_hours"] == "24000.0000"

    # The same declared evidence and the same policy reproduce the same identity.
    assert measure_capital_hours(series, policy=hours_policy()).hours_id == measured.hours_id

    accumulating = measure_capital_hours(
        points((0, "2000.00"), (3600, "2000.00"), (7200, "1000.00")),
        policy=hours_policy(),
    )
    assert accumulating.capital_hours == Decimal("4000.0000")
    assert accumulating.occupancy_points == 3
    assert accumulating.observed_span_seconds == 7200


def test_ac_008_the_endpoint_policy_is_declared_never_assumed() -> None:
    """ATDD-R5-C-capital-intelligence/AC-008: the basis rule is owner input.

    GIVEN the declared endpoint policy of an occupancy interval
    WHEN the same series is measured with the start basis, with the end basis and
    with no declared endpoint policy at all
    THEN the two declared policies resolve the interval differently and the shipped
    policy abstains instead of choosing one
    """

    series = points((0, "1000.00"), (3600, "2000.00"))
    from_start = measure_capital_hours(
        series, policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_START)
    )
    from_end = measure_capital_hours(
        series, policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_END)
    )
    assert from_start.capital_hours == Decimal("1000.0000")
    assert from_end.capital_hours == Decimal("2000.0000")
    assert from_start.hours_id != from_end.hours_id

    undeclared = measure_capital_hours(series, policy=hours_policy(endpoint=None))
    assert undeclared.status is CapitalHoursStatus.UNAVAILABLE
    assert (
        undeclared.unavailable_reason
        is CapitalHoursUnavailableReason.ENDPOINT_POLICY_NOT_DECLARED
    )
    assert undeclared.capital_hours is None
    assert undeclared.net_capital_hours is None
    assert undeclared.occupancy_points == 2
    assert undeclared.to_dict()["capital_hours"] is None

    shipped = capital_hours_engineering_scenario_policy()
    assert shipped.endpoint_policy is None
    assert shipped.policy_version == CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION
    assert measure_capital_hours(series, policy=shipped).status is CapitalHoursStatus.UNAVAILABLE



def test_ac_008_an_unresolvable_series_abstains_rather_than_guessing() -> None:
    """ATDD-R5-C-capital-intelligence/AC-008: unavailable, never a zero.

    GIVEN an empty series, a series that starts before its declared start, a
    non-monotone series, a rewound cumulative cost series and a zero occupancy
    WHEN each is measured
    THEN each abstains with its declared reason and publishes no number, and a
    structural misuse of the seam raises instead of returning a result
    """

    assert (
        measure_capital_hours((), policy=hours_policy()).unavailable_reason
        is CapitalHoursUnavailableReason.NO_OCCUPANCY_POINTS
    )
    assert (
        measure_capital_hours(
            points((0, "1000.00"), (3600, "1000.00")),
            policy=hours_policy(),
            started_at=at(60),
        ).unavailable_reason
        is CapitalHoursUnavailableReason.POINT_BEFORE_START
    )
    for reversed_series in (
        points((3600, "1000.00"), (0, "1000.00")),
        points((0, "1000.00"), (0, "1000.00")),
    ):
        assert (
            measure_capital_hours(reversed_series, policy=hours_policy()).unavailable_reason
            is CapitalHoursUnavailableReason.POINTS_NOT_MONOTONE
        )
    rewound = (
        capital_occupancy_point(at=T0, allocated_basis="1000.00", cumulative_fees="5.00"),
        capital_occupancy_point(at=at(3600), allocated_basis="1000.00", cumulative_fees="1.00"),
    )
    assert (
        measure_capital_hours(rewound, policy=hours_policy()).unavailable_reason
        is CapitalHoursUnavailableReason.DECLARED_COSTS_MISSING
    )
    for zero in (points((0, "0.00"), (3600, "0.00")), points((0, "1000.00"))):
        vacuous = measure_capital_hours(zero, policy=hours_policy())
        assert vacuous.unavailable_reason is CapitalHoursUnavailableReason.ZERO_OCCUPANCY
        assert vacuous.capital_hours is None
        assert vacuous.to_dict()["capital_hours"] is None
        assert vacuous.status is CapitalHoursStatus.UNAVAILABLE

    with pytest.raises(CapitalIntelligenceError):
        measure_capital_hours(("not-a-point",), policy=hours_policy())
    with pytest.raises(CapitalIntelligenceError):
        measure_capital_hours(points((0, "1000.00"), (3600, "1000.00")), policy="policy")


def test_ac_008_the_cost_channel_is_measured_not_estimated() -> None:
    """ATDD-R5-C-capital-intelligence/AC-008: declared costs occupy capital too.

    GIVEN a declared occupancy series whose cumulative fees and carry rise
    WHEN the series is measured
    THEN the cost channel is integrated over the same preserved instants and
    subtracted once, so the net occupancy is exactly the declared arithmetic
    """

    series = (
        capital_occupancy_point(at=T0, allocated_basis="2000.00"),
        capital_occupancy_point(
            at=at(7200), allocated_basis="2000.00", cumulative_fees="5.00", cumulative_carry="3.00"
        ),
    )
    measured = measure_capital_hours(series, policy=hours_policy())
    assert measured.capital_hours == Decimal("4000.0000")
    assert measured.cost_capital_hours == Decimal("16.0000")
    assert measured.net_capital_hours == Decimal("3984.0000")
    assert measured.net_capital_hours == measured.capital_hours - measured.cost_capital_hours


def test_ac_008_off_grid_or_unusable_occupancy_values_are_refused() -> None:
    """ATDD-R5-C-capital-intelligence/AC-008: exact declared instants and amounts.

    GIVEN an occupancy point declared with a float, an over-precise amount, a
    negative amount or a naive instant
    WHEN it is declared
    THEN each is refused rather than rounded, coerced or read as local time
    """

    for bad_basis in (1000.0, 1000, True, "1000.000", "-1.00"):
        with pytest.raises(CapitalIntelligenceError):
            capital_occupancy_point(at=T0, allocated_basis=bad_basis)
    with pytest.raises(CapitalIntelligenceError):
        capital_occupancy_point(at=T0, allocated_basis="1000.00", cumulative_fees="-1.00")
    with pytest.raises(CapitalIntelligenceError):
        capital_occupancy_point(at=T0, allocated_basis="1000.00", cumulative_carry="1.0")
    with pytest.raises(CapitalIntelligenceError):
        capital_occupancy_point(
            at=datetime(2026, 1, 5, 9, 0), allocated_basis="1000.00"
        )
    assert capital_occupancy_point(at=T0, allocated_basis="1000.00").cumulative_cost == Decimal("0.00")

def test_ac_007_the_plan_current_check_reuses_the_frozen_owner() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: no second reservation family.

    GIVEN a plan bound to another portfolio version
    WHEN the frozen owner's own current-plan assertion and this commit seam are
    compared
    THEN the seam delegates to the frozen owner's assertion, it imports the frozen
    capital state instead of declaring one, and it never re-declares the frozen
    record
    """

    record = commit_fixture("PAPER-LEDGER-R5C-CONCURRENCY-4")
    stale_plan = plan(version="pv-r5c-0002")
    with pytest.raises(PortfolioContractError):
        assert_reservation_plan_current(
            stale_plan, current_portfolio_version=record.portfolio_version
        )
    refusal = commit_reservation(record, plan=stale_plan, at_time=at(60))
    assert refusal.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION

    source = r5c_source()
    assert "from app.opip.contracts.portfolio import" in source
    assert "assert_reservation_plan_current" in source
    assert "PortfolioCapitalState" in source
    assert "class PortfolioCapitalState" not in source
    assert "class PortfolioReservationPlan" not in source


# ---------------------------------------------------------------------------
# AC-009 Capital-time efficiency is a diagnostic, published with its occupancy
# ---------------------------------------------------------------------------


def test_ac_009_return_per_hour_uses_the_measured_occupancy() -> None:
    """ATDD-R5-C-capital-intelligence/AC-009: realized net per capital-hour.

    GIVEN a resolved cohort with a declared realized trading net
    WHEN its capital-time efficiency is measured
    THEN the ratio is realized trading net divided by the measured capital-hours
    - the adopted illustrative example included - the operating-net variant is
    published separately, a loss is published as a negative ratio, and the record
    is a deterministic diagnostic that is never authoritative
    """

    series = points((0, "2000.00"), (43_200, "2000.00"))
    measured = measure_capital_hours(series, policy=hours_policy())
    cohort = capital_time_efficiency(
        "R5C-TACTICAL",
        points=series,
        policy=hours_policy(),
        realized_trading_net="60.00",
        operating_net="50.00",
    )
    assert cohort.status is CapitalTimeEfficiencyStatus.MEASURED
    assert cohort.unavailable_reason is None
    assert cohort.hours_id == measured.hours_id
    assert cohort.cohort_id.startswith(f"{CAPITAL_HOURS_ID_PREFIX}:")
    assert cohort.capital_hours == Decimal("24000.0000")
    assert cohort.realized_trading_net == Decimal("60.00")
    assert cohort.return_per_hour == Decimal("0.002500")
    assert cohort.operating_net == Decimal("50.00")
    assert cohort.operating_return_per_hour == Decimal("0.002083")
    assert cohort.operating_net != cohort.realized_trading_net
    assert cohort.occupancy_points == 2
    assert cohort.authoritative is False
    payload = cohort.to_dict()
    assert payload["status"] == "MEASURED"
    assert payload["return_per_hour"] == "0.002500"
    assert payload["operating_return_per_hour"] == "0.002083"
    assert payload["authoritative"] is False
    assert (
        capital_time_efficiency(
            "R5C-TACTICAL", points=series, policy=hours_policy(), realized_trading_net="60.00"
        ).cohort_id
        != cohort.cohort_id
    )

    losing = capital_time_efficiency(
        "R5C-LOSING", points=series, policy=hours_policy(), realized_trading_net="-12.00"
    )
    assert losing.return_per_hour == Decimal("-0.000500")
    assert losing.operating_net is None
    assert losing.operating_return_per_hour is None


def test_ac_009_zero_or_unknown_occupancy_yields_unavailable() -> None:
    """ATDD-R5-C-capital-intelligence/AC-009: no occupancy, no efficiency.

    GIVEN a cohort whose occupancy is zero or cannot be resolved at all
    WHEN its capital-time efficiency is measured
    THEN every number - the occupancy, the realized net and the ratio - is absent
    and the abstention carries the reason the occupancy could not be measured
    """

    for unresolved, reason in (
        (points((0, "0.00"), (3600, "0.00")), CapitalHoursUnavailableReason.ZERO_OCCUPANCY),
        ((), CapitalHoursUnavailableReason.NO_OCCUPANCY_POINTS),
    ):
        cohort = capital_time_efficiency(
            "R5C-UNRESOLVED",
            points=unresolved,
            policy=hours_policy(),
            realized_trading_net="60.00",
        )
        assert cohort.status is CapitalTimeEfficiencyStatus.UNAVAILABLE
        assert cohort.unavailable_reason is reason
        payload = cohort.to_dict()
        assert payload["capital_hours"] is None
        assert payload["realized_trading_net"] is None
        assert payload["return_per_hour"] is None
        assert payload["operating_return_per_hour"] is None
        assert payload["status"] == "UNAVAILABLE"

    undeclared = capital_time_efficiency(
        "R5C-UNDECLARED",
        points=points((0, "1000.00"), (3600, "1000.00")),
        policy=hours_policy(endpoint=None),
        realized_trading_net="60.00",
    )
    assert (
        undeclared.unavailable_reason
        is CapitalHoursUnavailableReason.ENDPOINT_POLICY_NOT_DECLARED
    )
    assert undeclared.return_per_hour is None




def test_ac_009_the_aggregate_is_a_ratio_of_sums_not_an_average_of_ratios() -> None:
    """ATDD-R5-C-capital-intelligence/AC-009: aggregate honestly, name exclusions.

    GIVEN two measured cohorts of very different size and one unresolvable cohort
    WHEN they are aggregated
    THEN the aggregate is the ratio of the summed realized net to the summed
    capital-hours - not the mean of the two ratios - the measured cohorts are named
    in order, the unresolvable cohort is named as excluded, and an aggregate with no
    measured cohort abstains
    """

    large = capital_time_efficiency(
        "R5C-LARGE",
        points=points((0, "2000.00"), (43_200, "2000.00")),
        policy=hours_policy(),
        realized_trading_net="60.00",
    )
    small = capital_time_efficiency(
        "R5C-SMALL",
        points=points((0, "1000.00"), (3600, "1000.00")),
        policy=hours_policy(),
        realized_trading_net="100.00",
    )
    assert large.return_per_hour == Decimal("0.002500")
    assert small.return_per_hour == Decimal("0.100000")

    aggregate = aggregate_capital_time_efficiency((large, small))
    assert aggregate.status is CapitalTimeEfficiencyStatus.MEASURED
    assert aggregate.unavailable_reason is None
    assert aggregate.aggregate_id.startswith(f"{CAPITAL_HOURS_ID_PREFIX}:")
    assert aggregate.capital_hours == Decimal("25000.0000")
    assert aggregate.realized_trading_net == Decimal("160.00")
    assert aggregate.return_per_hour == Decimal("0.006400")
    naive_mean = (large.return_per_hour + small.return_per_hour) / 2
    assert aggregate.return_per_hour != naive_mean
    assert aggregate.measured_cohorts == ("R5C-LARGE", "R5C-SMALL")
    assert aggregate.unavailable_cohorts == ()
    assert aggregate.authoritative is False
    assert aggregate.to_dict()["return_per_hour"] == "0.006400"

    unresolved = capital_time_efficiency(
        "R5C-EXCLUDED", points=(), policy=hours_policy(), realized_trading_net="5.00"
    )
    mixed = aggregate_capital_time_efficiency((large, unresolved, small))
    assert mixed.status is CapitalTimeEfficiencyStatus.MEASURED
    assert mixed.measured_cohorts == ("R5C-LARGE", "R5C-SMALL")
    assert mixed.unavailable_cohorts == ("R5C-EXCLUDED",)
    assert mixed.capital_hours == aggregate.capital_hours
    assert mixed.realized_trading_net == aggregate.realized_trading_net
    assert mixed.aggregate_id != aggregate.aggregate_id

    empty = aggregate_capital_time_efficiency((unresolved,))
    assert empty.status is CapitalTimeEfficiencyStatus.UNAVAILABLE
    assert empty.unavailable_reason is CapitalHoursUnavailableReason.NO_OCCUPANCY_POINTS
    assert empty.measured_cohorts == ()
    assert empty.unavailable_cohorts == ("R5C-EXCLUDED",)
    assert empty.return_per_hour is None
    assert empty.capital_hours is None

    with pytest.raises(CapitalIntelligenceError):
        aggregate_capital_time_efficiency(("not-a-cohort",))



# ---------------------------------------------------------------------------
# AC-010 The declared policies ship no unratified number (`OQ-06` stays owner)
# ---------------------------------------------------------------------------


def test_ac_010_the_declared_policy_versions_and_owner_input_are_exact() -> None:
    """ATDD-R5-C-capital-intelligence/AC-010: no invented policy number.

    GIVEN the two policy versions this increment ships and the owner input it
    cannot decide
    WHEN the shipped policies are inspected and a foreign policy token is offered
    THEN both shipped policies declare no number at all and abstain, `OQ-06` stays
    `OWNER_DECISION_REQUIRED`, and a policy version this increment did not declare
    is refused rather than reinterpreted
    """

    assert DECLARED_CAPITAL_POLICY_VERSIONS == (
        CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION,
    )
    assert OPEN_OWNER_INPUTS == ("OQ-06",)
    assert OWNER_INPUT_STATUS == "OWNER_DECISION_REQUIRED"

    shipped = capital_deployable_engineering_scenario_policy()
    assert shipped.policy_version == CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION
    assert shipped.limit_caps == {}
    assert shipped.sleeve_limits == {}
    assert shipped.account is None
    assert shipped.environment is None
    assert shipped.engine is None
    assert shipped.currency is None
    shipped_view = evaluate_deployable_capital(snapshot(), policy=shipped, at_time=at(60))
    assert shipped_view.abstention_reason is CapitalDeployableAbstentionReason.IDENTITY_NOT_BOUND
    assert shipped_view.deployable_capital is None

    shipped_hours = capital_hours_engineering_scenario_policy()
    assert shipped_hours.policy_version == CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION
    assert shipped_hours.endpoint_policy is None
    assert shipped_hours.basis_token == CAPITAL_HOURS_BASIS_TOKEN
    assert (
        measure_capital_hours(
            points((0, "1000.00"), (3600, "1000.00")), policy=shipped_hours
        ).status
        is CapitalHoursStatus.UNAVAILABLE
    )

    with pytest.raises(CapitalIntelligenceError, match="policy_version"):
        CapitalDeployablePolicy(policy_version="capital-deployable-policy-v1")
    with pytest.raises(CapitalIntelligenceError, match="policy_version"):
        CapitalHoursPolicy(policy_version="capital-hours-policy-v1")


def test_ac_010_the_engine_ships_no_unratified_cap_or_band() -> None:
    """ATDD-R5-C-capital-intelligence/AC-010: the defaults are declared absences.

    GIVEN the R5-C section source
    WHEN its declared policy fields are inspected
    THEN each cap, sleeve limit and endpoint policy defaults to a declared absence
    rather than a number, and the owner input is named as an owner decision
    """

    source = r5c_source()
    assert (
        "limit_caps: Mapping[CapitalLimitKind, Decimal] = field(default_factory=dict)"
        in source
    )
    assert "sleeve_limits: Mapping[str, Decimal] = field(default_factory=dict)" in source
    assert "endpoint_policy: CapitalHoursEndpointPolicy | None = None" in source
    assert (
        "policy_version: str = CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION"
        in source
    )
    assert 'OPEN_OWNER_INPUTS: tuple[str, ...] = ("OQ-06",)' in source
    assert 'OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"' in source



# ---------------------------------------------------------------------------
# AC-011 Zero authority, zero storage, zero runtime wiring
# ---------------------------------------------------------------------------


def test_ac_011_the_module_carries_no_authority_storage_or_environment_surface() -> None:
    """ATDD-R5-C-capital-intelligence/AC-011: zero authority by construction.

    GIVEN the R5-C section of the owning module
    WHEN its imports, source and exports are inspected
    THEN it imports no network, database, process, randomness or persistence
    facility, names no order, secret or store surface, reads no environment
    variable and exports no authority-shaped name
    """

    assert MODULE.is_file()
    source = r5c_source()
    lowered = source.lower()
    assert module_imports(MODULE) & FORBIDDEN_PRIMITIVES == set()
    for token in FORBIDDEN_AUTHORITY_TOKENS:
        assert token not in lowered, token
    for token in FORBIDDEN_PERSISTENCE_TOKENS:
        assert token not in source, token
    for pattern in ("OPIP_", "os.environ", "os.getenv", "getenv(", "environ["):
        assert pattern not in source, pattern
    for name in capital_module.__all__:
        asserted = name.lower()
        for token in FORBIDDEN_AUTHORITY_TOKENS:
            assert token not in asserted, (name, token)
        for token in ("store", "writer", "persist", "allocate", "execute", "secret"):
            assert token not in asserted, (name, token)


def test_ac_011_no_runtime_root_or_application_module_consumes_the_record() -> None:
    """ATDD-R5-C-capital-intelligence/AC-011: not wired into runtime.

    GIVEN the runtime roots that own trading, discovery, decision and risk paths
    WHEN every application module is inspected
    THEN no runtime root imports the capital record's owner and no application
    module outside the owner even references it, so it cannot act on live state
    """

    targets = (
        "app.services.capital_rotation_intelligence",
        "services.capital_rotation_intelligence",
        "capital_rotation_intelligence",
    )
    offenders: list[str] = []
    for root in RUNTIME_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            hits = sorted(name for name in module_imports(path) if name in targets)
            if hits:
                offenders.append(f"{path.name}: {hits}")
    assert offenders == []
    consumers = [
        path.relative_to(APP_ROOT).as_posix()
        for path in sorted((APP_ROOT / "app").rglob("*.py"))
        if path != MODULE and "capital_rotation_intelligence" in module_source(path)
    ]
    assert consumers == []


def test_ac_011_release_profile_and_paper_posture_are_unchanged() -> None:
    """ATDD-R5-C-capital-intelligence/AC-011: profile posture preserved.

    GIVEN the repository release-profile contract and Compose literals
    WHEN they are inspected
    THEN `EVIDENCE_SHADOW` remains the active shadow profile with `TARGET_PAPER`
    blocked, paper-v2 stays off, and this increment adds no mode key
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
    compose = yaml.safe_load(
        (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["ohm-trade-agent"]["environment"]
    assert environment["OPIP_PAPER_V2_MODE"] == "off"
    assert environment["OPIP_FEATURE_BUS_MODE"] == "shadow"


def test_ac_011_every_record_declares_itself_non_authoritative() -> None:
    """ATDD-R5-C-capital-intelligence/AC-011: research-only evidence.

    GIVEN a snapshot, a deployable view, a commit, a measurement, a cohort and an
    aggregate
    WHEN each record's flag and serialized keys are inspected
    THEN every record declares itself non-authoritative, no serialized record
    carries an authority or secret key, and the component records carry no
    authority flag at all
    """

    record = snapshot()
    view = evaluate_deployable_capital(record, policy=policy(), at_time=at(60))
    commit = commit_reservation(
        commit_fixture("PAPER-LEDGER-R5C-POSTURE"),
        plan=plan(version=VERSION),
        at_time=at(60),
    )
    series = points((0, "1000.00"), (3600, "1000.00"))
    measured = measure_capital_hours(series, policy=hours_policy())
    cohort = capital_time_efficiency(
        "R5C-POSTURE", points=series, policy=hours_policy(), realized_trading_net="10.00"
    )
    aggregate = aggregate_capital_time_efficiency((cohort,))
    for item in (record, view, commit, measured, cohort, aggregate):
        payload = item.to_dict()
        assert payload["authoritative"] is False, item
        for key in payload:
            assert "secret" not in key and "api_key" not in key
            assert "credential" not in key
    for item in (
        record.cash,
        record.marks,
        record.positions[0],
        record.sleeve_usage[0],
        record.limit_usage[0],
        record.fee_state,
        record.source_watermark,
        record.freshness,
        record.reconciliation,
        series[0],
    ):
        payload = item.to_dict()
        assert "authoritative" not in payload, item
        for key in payload:
            assert "secret" not in key and "api_key" not in key
            assert "credential" not in key


# ---------------------------------------------------------------------------
# AC-012 The increment, its declared vocabulary and its source are self-consistent
# ---------------------------------------------------------------------------


def test_ac_012_the_increment_scope_contract_is_self_consistent() -> None:
    """ATDD-R5-C-capital-intelligence/AC-012: contract matches its tests.

    GIVEN this increment's scope contract and the repository acceptance index
    WHEN the contract is parsed and checked
    THEN its increment matches, its declared traceability equals the acceptance
    tests that cite it, every criterion has an implementation-map entry, the scope
    check reports no reason, and the movable increment pointer resolves without
    being pinned by this module
    """

    assert CONTRACT.is_file()
    contract = parse_scope_contract(module_source(CONTRACT))
    assert contract.increment == INCREMENT
    assert set(contract.acceptance_criteria) == {
        f"AC-{index:03d}" for index in range(1, 13)
    }
    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.reasons == ()
    assert result.status == "PASS"
    for ac_id in contract.acceptance_criteria:
        assert contract.traceability.get(ac_id), ac_id
        assert contract.implementation_map.get(ac_id), ac_id
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    increments = [item.increment for item in contracts]
    assert len(increments) == len(set(increments))
    assert INCREMENT in increments
    # The pointer is movable, so it is not pinned here; it must merely resolve.
    atdd_root = APP_ROOT / "docs" / "atdd"
    active = (atdd_root / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()
    assert (atdd_root / "scope-contracts" / f"{active}.md").is_file(), active
    assert global_pointer_pin_violations(module_source(Path(__file__))) == []


def test_ac_012_the_declared_schema_versions_tokens_and_prefixes_are_exact() -> None:
    """ATDD-R5-C-capital-intelligence/AC-012: the declared vocabulary is exact.

    GIVEN the module's declared schema versions, quanta, prefixes, record keys and
    precedence tables
    WHEN each is compared with its declared value
    THEN every token is the exact declared one, the frozen `SC-06` record keys are
    the adopted ones, the cash-law table covers exactly the supported recompute
    reasons, and both declared precedence tables are closed and ordered
    """

    assert CAPITAL_SNAPSHOT_SCHEMA_VERSION == "capital-snapshot-v1"
    assert CAPITAL_COMMIT_SCHEMA_VERSION == "capital-commit-v1"
    assert CAPITAL_HOURS_SCHEMA_VERSION == "capital-hours-v1"
    assert CAPITAL_SNAPSHOT_ID_PREFIX == "CPSN"
    assert CAPITAL_COMMIT_ID_PREFIX == "CCMT"
    assert CAPITAL_HOURS_ID_PREFIX == "CHRS"
    assert MONEY_QUANTUM == Decimal("0.01")
    assert CAPITAL_HOURS_QUANTUM == Decimal("0.0001")
    assert CAPITAL_RETURN_PER_HOUR_QUANTUM == Decimal("0.000001")
    assert CAPITAL_SECONDS_PER_HOUR == Decimal("3600")
    assert CAPITAL_LIABILITIES_SUPPORTED is False
    assert CAPITAL_RECEIVABLES_SUPPORTED is False
    assert CAPITAL_HOURS_BASIS_TOKEN == (
        "declared-allocated-basis-engineering-scenario-v1"
    )
    assert CAPITAL_SNAPSHOT_RECORD_KEYS == {
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
    assert set(CAPITAL_RECOMPUTE_CASH_LAWS) == set(CapitalRecomputeTrigger) - set(
        CAPITAL_UNSUPPORTED_RECOMPUTE_TRIGGERS
    )
    assert CAPITAL_BOUND_PRECEDENCE == (
        CapitalBound.FREE_CASH,
        CapitalBound.RISK,
        CapitalBound.CONCENTRATION,
        CapitalBound.LIQUIDITY,
        CapitalBound.HORIZON,
        CapitalBound.SLEEVE,
    )
    assert set(CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE) == set(
        CapitalDeployableAbstentionReason
    )
    assert len(CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE) == len(
        CapitalDeployableAbstentionReason
    )
    for name in capital_module.__all__:
        assert hasattr(capital_module, name), name




def test_ac_012_the_module_holds_no_hidden_clock_randomness_or_environment_read() -> None:
    """ATDD-R5-C-capital-intelligence/AC-012: explicit instants only.

    GIVEN the R5-C section of the owning module
    WHEN its source and imports are inspected
    THEN it reads no wall clock, no randomness, no environment, no filesystem and
    no network, and it validates and serializes instants and identities through the
    frozen temporal, serialization and portfolio owners
    """

    source = r5c_source()
    for pattern in (
        "datetime.now",
        "utcnow",
        "time.time",
        "time.monotonic",
        "random.",
        "uuid",
        "os.environ",
        "getenv(",
        "Path(",
        "open(",
        "read_text",
        "requests",
        "socket",
        "subprocess",
        "sqlite3",
        "pickle",
        "asyncio",
    ):
        assert pattern not in source, pattern
    imports = module_imports(MODULE)
    assert imports & FORBIDDEN_PRIMITIVES == set()
    assert "app.opip.contracts.temporal" in source
    assert "app.opip.contracts.serialization" in source
    assert "app.opip.contracts.portfolio" in source
    assert "require_utc" in source
    assert "iso_z" in source
    assert "stable_hash" in source
    assert "authoritative" in source
