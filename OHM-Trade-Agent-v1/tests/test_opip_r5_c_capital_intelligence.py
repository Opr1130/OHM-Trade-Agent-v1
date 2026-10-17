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

from app.opip.contracts.portfolio import (  # noqa: E402
    PortfolioContractError,
    PortfolioDirection,
    PortfolioReservation,
    PortfolioReservationPlan,
    ReservationReleaseReason,
    assert_reservation_plan_current,
    release_reservation,
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
    CAPITAL_RESERVATION_DURABILITY_DEPENDENCY,
    CAPITAL_RESERVATION_DURABILITY_STATUS,
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


def declared_usage(
    risk: str = "900.00",
    concentration: str = "900.00",
    liquidity: str = "900.00",
) -> tuple[CapitalLimitUsage, ...]:
    """Declare every limit family a scenario configures a cap for.

    A configured cap whose own usage row is missing abstains rather than being
    charged with another family's usage, so every scenario that means to exercise
    the bound arithmetic declares the usage it is measured against - including an
    explicit ``"0.00"``, which is declared evidence rather than missing evidence.
    """

    return (
        CapitalLimitUsage(kind=CapitalLimitKind.RISK, used_capital=risk, as_of=T0),
        CapitalLimitUsage(
            kind=CapitalLimitKind.CONCENTRATION, used_capital=concentration, as_of=T0
        ),
        CapitalLimitUsage(
            kind=CapitalLimitKind.LIQUIDITY, used_capital=liquidity, as_of=T0
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

    Each declared law is exercised on its own book: one book has exactly one
    lawful successor of a version, so a scenario that asserts a lawful recompute
    declares the book it belongs to.
    """

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

    reservation_base = snapshot(account="PAPER-LEDGER-R5C-LAW-RESERVATION")
    moved = successor(
        reservation_base,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
    )
    assert_snapshot_recomputes_from(
        reservation_base, moved, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    assert moved.cash.cash_total == reservation_base.cash.cash_total
    inflated = successor(
        reservation_base,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="600.00"),
    )
    with pytest.raises(CapitalIntelligenceError, match="cash total"):
        assert_snapshot_recomputes_from(
            reservation_base, inflated, trigger=CapitalRecomputeTrigger.RESERVATION
        )

    fee_base = snapshot(account="PAPER-LEDGER-R5C-LAW-FEE")
    charged = successor(
        fee_base,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9990.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=8),
        source_watermark=watermark(8),
    )
    assert_snapshot_recomputes_from(fee_base, charged, trigger=CapitalRecomputeTrigger.FEE)
    assert charged.cash.cash_total == Decimal("10490.00")
    undeclared_fee = successor(
        fee_base,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9990.00", safety="500.00"),
        fee_state=fee_state(fees="0.00", sequence=8),
        source_watermark=watermark(8),
    )
    with pytest.raises(CapitalIntelligenceError, match="fee"):
        assert_snapshot_recomputes_from(
            fee_base, undeclared_fee, trigger=CapitalRecomputeTrigger.FEE
        )
    credited = successor(
        fee_base,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="10000.00", blocked="10.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=8),
        source_watermark=watermark(8),
    )
    with pytest.raises(CapitalIntelligenceError, match="cash total"):
        assert_snapshot_recomputes_from(fee_base, credited, trigger=CapitalRecomputeTrigger.FEE)
    rewound = successor(
        fee_base,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9990.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=6),
        source_watermark=watermark(6),
    )
    with pytest.raises(CapitalIntelligenceError, match="watermark"):
        assert_snapshot_recomputes_from(fee_base, rewound, trigger=CapitalRecomputeTrigger.FEE)
    mismatched = successor(
        fee_base,
        trigger=CapitalRecomputeTrigger.FEE,
        cash_ledger=cash(free="9995.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=8),
        source_watermark=watermark(8),
    )
    with pytest.raises(CapitalIntelligenceError, match="fee"):
        assert_snapshot_recomputes_from(fee_base, mismatched, trigger=CapitalRecomputeTrigger.FEE)

    mark_base = snapshot(account="PAPER-LEDGER-R5C-LAW-MARK")
    remarked = successor(
        mark_base,
        trigger=CapitalRecomputeTrigger.MARK,
        positions=(position(marked="3600.00", marked_at=at(60)),),
        marks=CapitalMarkState(
            marked_at=at(60), mark_source="paper-ledger-mark-v1", marked_position_value="3600.00"
        ),
        source_watermark=watermark(8),
    )
    assert_snapshot_recomputes_from(mark_base, remarked, trigger=CapitalRecomputeTrigger.MARK)
    assert remarked.cash.to_dict() == mark_base.cash.to_dict()
    with pytest.raises(CapitalIntelligenceError, match="mark"):
        assert_snapshot_recomputes_from(
            mark_base,
            successor(mark_base, trigger=CapitalRecomputeTrigger.MARK),
            trigger=CapitalRecomputeTrigger.MARK,
        )
    with pytest.raises(CapitalIntelligenceError, match="partition"):
        assert_snapshot_recomputes_from(
            mark_base,
            successor(
                mark_base,
                trigger=CapitalRecomputeTrigger.MARK,
                cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
            ),
            trigger=CapitalRecomputeTrigger.MARK,
        )

    reconciliation_base = snapshot(account="PAPER-LEDGER-R5C-LAW-RECONCILIATION")
    reconciled = successor(
        reconciliation_base,
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
        reconciliation_base, reconciled, trigger=CapitalRecomputeTrigger.RECONCILIATION
    )
    unreconciled = successor(
        reconciliation_base,
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
            reconciliation_base, unreconciled, trigger=CapitalRecomputeTrigger.RECONCILIATION
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
    usage_declared = snapshot(limit_usage=declared_usage())
    view = evaluate_deployable_capital(usage_declared, policy=policy_record, at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.abstention_reason is None
    assert view.deployable_capital == Decimal("100.00")
    assert view.binding_bound is CapitalBound.RISK
    assert view.binding_sleeve is None
    assert view.free_cash == Decimal("10000.00")
    assert view.currency == CURRENCY
    assert view.account == ACCOUNT
    assert view.policy_version == policy_record.policy_version
    assert view.snapshot_id == usage_declared.snapshot_id
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

    poor = snapshot(
        cash_ledger=cash(free="50.00", safety="500.00"), limit_usage=declared_usage()
    )
    view = evaluate_deployable_capital(poor, policy=policy(), at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.deployable_capital == Decimal("50.00")
    assert view.binding_bound is CapitalBound.FREE_CASH
    tied = snapshot(
        cash_ledger=cash(free="100.00", safety="500.00"),
        limit_usage=declared_usage(),
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
        sleeve_usage=(SleeveUsage(sleeve="TACTICAL", used_capital="5000.00", as_of=T0),),
        limit_usage=declared_usage(),
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
        ),
        limit_usage=declared_usage(),
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
        sleeve_usage=(SleeveUsage(sleeve="CORE", used_capital="100.00", as_of=T0),),
        limit_usage=declared_usage(),
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
    sleeve_view = evaluate_deployable_capital(
        snapshot(limit_usage=declared_usage()), policy=no_sleeve_limit, at_time=at(60)
    )
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
    view = evaluate_deployable_capital(
        snapshot(limit_usage=declared_usage()), policy=over, at_time=at(60)
    )
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.bounds[CapitalBound.RISK] == Decimal("0.00")
    assert view.over_limit_bounds == (CapitalBound.RISK,)
    assert view.deployable_capital == Decimal("0.00")
    assert view.binding_bound is CapitalBound.RISK
    near = snapshot(
        limit_usage=declared_usage(risk="999.99"),
        sleeve_usage=(SleeveUsage(sleeve="TACTICAL", used_capital="2999.99", as_of=T0),),
    )
    near_view = evaluate_deployable_capital(near, policy=policy(), at_time=at(60))
    assert near_view.bounds[CapitalBound.RISK] == Decimal("0.01")
    assert str(near_view.bounds[CapitalBound.RISK]) == "0.01"
    assert near_view.bounds[CapitalBound.SLEEVE] == Decimal("2000.01")
    assert near_view.deployable_capital == Decimal("0.01")
    assert near_view.binding_bound is CapitalBound.RISK
    assert near_view.over_limit_bounds == ()


def test_ac_004_a_capped_limit_without_declared_usage_abstains() -> None:
    """ATDD-R5-C-capital-intelligence/AC-004: a cap is not its own usage.

    GIVEN a policy that configures a cap for a limit family, and snapshots that
    declare no usage for it, no usage at all, another family's usage or an
    explicit zero
    WHEN deployable capital is evaluated
    THEN the configured cap without its own usage row abstains and names the
    missing row rather than being charged with another family's usage or read as
    the whole cap, while an explicitly declared zero is evidence and not a gap
    """

    missing_one = snapshot(limit_usage=declared_usage(liquidity="0.00")[:2])
    one_view = evaluate_deployable_capital(missing_one, policy=policy(), at_time=at(60))
    assert one_view.status is CapitalDeployableStatus.ABSTAINED
    assert one_view.abstention_reason is CapitalDeployableAbstentionReason.LIMIT_USAGE_NOT_DECLARED
    assert one_view.missing_policy_inputs == ("LIQUIDITY",)
    assert one_view.deployable_capital is None
    assert one_view.bounds == {}
    assert one_view.binding_bound is None

    no_usage = snapshot(limit_usage=())
    none_view = evaluate_deployable_capital(no_usage, policy=policy(), at_time=at(60))
    assert none_view.abstention_reason is CapitalDeployableAbstentionReason.LIMIT_USAGE_NOT_DECLARED
    assert none_view.missing_policy_inputs == ("RISK", "CONCENTRATION", "LIQUIDITY")
    assert none_view.deployable_capital is None

    # Risk usage may never be charged to the concentration or liquidity bound: with
    # only RISK declared, the other two named rows abstain instead of inheriting it.
    cross_family = snapshot(
        limit_usage=(CapitalLimitUsage(kind=CapitalLimitKind.RISK, used_capital="900.00", as_of=T0),)
    )
    cross_view = evaluate_deployable_capital(cross_family, policy=policy(), at_time=at(60))
    assert cross_view.abstention_reason is CapitalDeployableAbstentionReason.LIMIT_USAGE_NOT_DECLARED
    assert cross_view.missing_policy_inputs == ("CONCENTRATION", "LIQUIDITY")
    assert cross_view.deployable_capital is None

    # Declared zero usage is declared evidence: every capped family is measured and
    # the caps themselves bind.
    declared_zero = snapshot(
        limit_usage=declared_usage(risk="0.00", concentration="0.00", liquidity="0.00")
    )
    zero_view = evaluate_deployable_capital(declared_zero, policy=policy(), at_time=at(60))
    assert zero_view.status is CapitalDeployableStatus.AVAILABLE
    assert zero_view.bounds[CapitalBound.RISK] == Decimal("1000.00")
    assert zero_view.bounds[CapitalBound.CONCENTRATION] == Decimal("2500.00")
    assert zero_view.bounds[CapitalBound.LIQUIDITY] == Decimal("4000.00")
    assert zero_view.deployable_capital == Decimal("1000.00")
    assert zero_view.binding_bound is CapitalBound.RISK
    assert zero_view.over_limit_bounds == ()

    # A partial policy declaration is evaluated exactly as declared: the one capped
    # family that declares usage participates, and no uncapped family is invented.
    partial = evaluate_deployable_capital(
        snapshot(limit_usage=declared_usage()[:1]),
        policy=policy(
            limit_caps={CapitalLimitKind.RISK: "1000.00"},
            sleeve_limits={"TACTICAL": "5000.00"},
        ),
        at_time=at(60),
    )
    assert partial.status is CapitalDeployableStatus.AVAILABLE
    assert partial.bounds == {
        CapitalBound.FREE_CASH: Decimal("10000.00"),
        CapitalBound.RISK: Decimal("100.00"),
        CapitalBound.SLEEVE: Decimal("2000.00"),
    }
    assert partial.deployable_capital == Decimal("100.00")

    with pytest.raises(CapitalIntelligenceError, match="negative"):
        CapitalLimitUsage(kind=CapitalLimitKind.RISK, used_capital="-1.00", as_of=T0)


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
    expires_at: datetime | None = None,
    second_candidate: str | None = None,
) -> PortfolioReservationPlan:
    created = created_at or T0
    expires = expires_at if expires_at is not None else created + timedelta(hours=1)
    reservations = [
        PortfolioReservation(
            candidate_id=candidate,
            symbol=symbol,
            direction=PortfolioDirection.LONG,
            reserved_capital=amount,
            portfolio_version=version,
            created_at=created,
            expires_at=expires,
        )
    ]
    if second_candidate is not None:
        reservations.append(
            PortfolioReservation(
                candidate_id=second_candidate,
                symbol="BTCUSDT",
                direction=PortfolioDirection.LONG,
                reserved_capital=amount,
                portfolio_version=version,
                created_at=created,
                expires_at=expires,
            )
        )
    return PortfolioReservationPlan(
        portfolio_version=version,
        window_identity=window,
        created_at=created,
        reservations=tuple(reservations),
    )


def book_policy(record: CapitalSnapshot, **overrides) -> CapitalDeployablePolicy:
    """The engineering-scenario deployable policy bound to a snapshot's own book.

    A commit scenario runs on its own account so witnesses cannot cross tests, so
    the policy that sizes it is bound to that same account, engine and currency.
    """

    base: dict[str, object] = {
        "account": record.account,
        "environment": CapitalEnvironment.PAPER_LEDGER,
        "engine": record.engine,
        "currency": record.currency,
        "limit_caps": {
            CapitalLimitKind.RISK: "1000.00",
            CapitalLimitKind.CONCENTRATION: "2500.00",
            CapitalLimitKind.LIQUIDITY: "4000.00",
        },
        "sleeve_limits": {"TACTICAL": "5000.00"},
    }
    base.update(overrides)
    return capital_deployable_engineering_scenario_policy(**base)  # type: ignore[arg-type]


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


#: Modules owner-ratified as *readers* of the R5-C capital snapshot surface without
#: owning a book. The R5-E capital-admission seam
#: (`ATDD-R5-E-capital-admission-integration`) takes exactly one snapshot as its
#: declared input, so it may name the record; it must still declare neither
#: `CapitalSnapshot` nor `CashPartitionLedger`, and every other carrier remains a
#: violation.
DECLARED_SNAPSHOT_READERS = frozenset({"app/services/capital_admission_integration.py"})


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
        if relative in DECLARED_SNAPSHOT_READERS:
            assert "class CapitalSnapshot" not in source, relative
            assert "class CashPartitionLedger" not in source, relative
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
    assert CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE[-1] is CapitalDeployableAbstentionReason.SLEEVE_USAGE_NOT_DECLARED
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
        freshness=CapitalFreshness(as_of=T0, validity_deadline=at(60), evidence_complete=True),
        limit_usage=declared_usage(),
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


def test_ac_005_one_trust_table_gates_the_deployable_and_commit_seams() -> None:
    """ATDD-R5-C-capital-intelligence/AC-005: one blocking table, both seams.

    GIVEN the account-evidence conditions `CI-02` blocks on - an unsupported
    accounting state, a stale watermark, a failed or skipped reconciliation, a
    negative partition, missing fees, an outlived validity window and incomplete
    evidence
    WHEN deployable capital is evaluated and a reservation plan is committed
    against the same record
    THEN the deployable seam publishes its specific declared reason while the commit
    seam refuses with its one declared untrusted disposition, so the commit path can
    never consume capital the deployable seam would refuse to size

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical closing step of this
    criterion expected trusted evidence to still commit (`COMMITTED`). The
    owner-approved shadow-only capital-safety contract makes that unreachable,
    because no owner-ratified funding policy exists (`OQ-06`), so a trusted record
    is refused with `DEPLOYABLE_BOUND_UNAVAILABLE` without mutating anything.
    PENDING INTEGRATION PROOF: "trusted account evidence commits" once a ratified
    funding policy and trusted upstream consumption evidence exist (owned by the
    `ATDD-R5-C-deployable-bound-closure` increment contract).
    """

    order = CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE
    assert order.index(CapitalDeployableAbstentionReason.LIMIT_NOT_CONFIGURED) < order.index(
        CapitalDeployableAbstentionReason.LIMIT_USAGE_NOT_DECLARED
    )
    assert order.index(CapitalDeployableAbstentionReason.LIMIT_USAGE_NOT_DECLARED) < order.index(
        CapitalDeployableAbstentionReason.SLEEVE_LIMIT_NOT_CONFIGURED
    )

    cases = (
        (
            {"accounting_state": CapitalAccountingState.UNSUPPORTED},
            CapitalDeployableAbstentionReason.UNSUPPORTED_ACCOUNTING_STATE,
        ),
        (
            {"source_watermark": watermark(7, T0, True)},
            CapitalDeployableAbstentionReason.ACCOUNT_STATE_STALE,
        ),
        (
            {
                "reconciliation": CapitalReconciliation(
                    status=CapitalReconciliationStatus.FAILED,
                    observed_at=T0,
                    reference="RECON:1",
                    variance="12.00",
                )
            },
            CapitalDeployableAbstentionReason.FAILED_RECONCILIATION,
        ),
        (
            {
                "reconciliation": CapitalReconciliation(
                    status=CapitalReconciliationStatus.NOT_PERFORMED,
                    observed_at=T0,
                    reference="RECON:1",
                    variance=None,
                )
            },
            CapitalDeployableAbstentionReason.RECONCILIATION_NOT_PERFORMED,
        ),
        (
            {"cash_ledger": cash(free="-1.00", safety="500.00")},
            CapitalDeployableAbstentionReason.UNRESOLVED_NEGATIVE_BALANCE,
        ),
        ({"fee_state": None}, CapitalDeployableAbstentionReason.MISSING_FEES),
        (
            {
                "freshness": CapitalFreshness(
                    as_of=T0, validity_deadline=at(900), evidence_complete=False
                )
            },
            CapitalDeployableAbstentionReason.EVIDENCE_INCOMPLETE,
        ),
    )
    for index, (overrides, expected) in enumerate(cases):
        record = commit_fixture(f"PAPER-LEDGER-R5C-TRUST-{index}", **overrides)
        view = evaluate_deployable_capital(record, policy=book_policy(record), at_time=at(60))
        assert view.abstention_reason is expected, overrides
        assert view.deployable_capital is None
        refusal = commit_reservation(
            record,
            plan=plan(version=record.portfolio_version),
            at_time=at(60),
            deployable_policy=book_policy(record),
        )
        assert refusal.status is CapitalCommitStatus.REJECTED, overrides
        assert refusal.rejection is CapitalCommitRejection.ACCOUNT_STATE_UNTRUSTED, overrides
        assert refusal.snapshot is None
        assert refusal.committed_capital is None
        assert refusal.commit_id.startswith(f"{CAPITAL_COMMIT_ID_PREFIX}:")

    # The outlived window is the one condition the commit seam reports under its own
    # declared token, because the observation instant is checked before the plan is
    # read at all.
    outlived = commit_fixture(
        "PAPER-LEDGER-R5C-TRUST-OUTLIVED",
        freshness=CapitalFreshness(as_of=T0, validity_deadline=at(30), evidence_complete=True),
    )
    assert (
        evaluate_deployable_capital(outlived, policy=book_policy(outlived), at_time=at(60)).abstention_reason
        is CapitalDeployableAbstentionReason.SNAPSHOT_OUTLIVED_VALIDITY
    )
    assert (
        commit_reservation(
            outlived,
            plan=plan(version=VERSION),
            at_time=at(60),
            deployable_policy=book_policy(outlived),
        ).rejection
        is CapitalCommitRejection.SNAPSHOT_OUTLIVED_VALIDITY
    )

    trusted = commit_fixture(
        "PAPER-LEDGER-R5C-TRUST-CONTROL", limit_usage=declared_usage()
    )
    assert (
        evaluate_deployable_capital(trusted, policy=book_policy(trusted), at_time=at(60)).status
        is CapitalDeployableStatus.AVAILABLE
    )
    # Trust is not authorisation: the evidence is good enough to size capital, and
    # the commit still fails closed because no owner-ratified funding policy exists.
    control = commit_reservation(
        trusted,
        plan=plan(version=trusted.portfolio_version),
        at_time=at(60),
        deployable_policy=book_policy(trusted),
    )
    assert control.status is CapitalCommitStatus.REJECTED
    assert control.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert control.snapshot is None
    assert control.committed_capital is None
    assert control.successor_version is None
    assert trusted.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
    assert trusted.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")


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
    THEN the commit is refused with `DEPLOYABLE_BOUND_UNAVAILABLE` and publishes no
    snapshot, no successor version and no consumed capital, the book and its own
    derived capital state are unchanged, and the declaration the historical criterion
    proved for a committed plan - the partition move, the preserved cash total and
    the declared next version of the same lineage - stays provable as a lawful
    recompute of the same book

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: this criterion historically expected the
    plan's capital to move from free cash into order reserves with the committed
    amount recorded. With no owner-ratified funding policy (`OQ-06`) the seam refuses
    instead of committing.
    PENDING INTEGRATION PROOF: "a reservation moves free cash into order reserves,
    records the plan reference and advances the portfolio version by one step" once a
    ratified funding policy and trusted upstream consumption evidence exist (owned by
    the `ATDD-R5-C-deployable-bound-closure` increment contract).
    """

    record = commit_fixture("PAPER-LEDGER-R5C-COMMIT-1")
    reservation_plan = plan(version=record.portfolio_version)
    result = commit_reservation(
        record,
        plan=reservation_plan,
        at_time=at(60),
        deployable_policy=book_policy(record),
    )

    assert result.status is CapitalCommitStatus.REJECTED
    assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert result.commit_id.startswith(f"{CAPITAL_COMMIT_ID_PREFIX}:")
    assert result.applied_portfolio_version == record.portfolio_version
    assert result.successor_version is None
    assert result.committed_capital is None
    assert result.snapshot is None
    assert result.snapshot_id is None
    payload = result.to_dict()
    assert payload["commit_schema_version"] == CAPITAL_COMMIT_SCHEMA_VERSION
    assert payload["status"] == "REJECTED"
    assert payload["committed_capital"] is None
    assert payload["snapshot_id"] is None
    assert payload["authoritative"] is False

    # The refusal is inert: the book did not move and re-derives to its own identity.
    assert record.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")
    assert CapitalSnapshot.from_dict(record.to_dict()).snapshot_id == record.snapshot_id

    # The historical commitment declaration is still proved where it is independently
    # testable: the declared RESERVATION recompute law is the same all-or-nothing
    # partition move that preserves the declared cash total and advances the version.
    lawfully_moved = successor(
        record,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
        limit_usage=declared_usage(),
    )
    assert_snapshot_recomputes_from(
        record, lawfully_moved, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    assert lawfully_moved.portfolio_version == "pv-r5c-0001.0001"
    assert lawfully_moved.derived_from == record.snapshot_id
    assert lawfully_moved.recompute_trigger is CapitalRecomputeTrigger.RESERVATION
    assert lawfully_moved.cash.amount(CapitalPartition.FREE_CASH) == Decimal("9000.00")
    assert lawfully_moved.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("1000.00")
    assert lawfully_moved.cash.amount(CapitalPartition.SAFETY_RESERVE) == Decimal("500.00")
    assert lawfully_moved.cash.cash_total == record.cash.cash_total

    state = to_portfolio_capital_state(record)
    assert state.portfolio_version == record.portfolio_version
    # The frozen F7 owner types its capital base as a float, so this declared
    # conversion is asserted against the fixture's declared literal
    # (10 000.00 free cash) rather than by repeating the conversion.
    assert state.available_capital == 10000.0
    assert state.currency == record.currency
    assert state.as_of == record.freshness.as_of
    assert state.capital_state_fingerprint.startswith("PCAP:")
    moved_state = to_portfolio_capital_state(lawfully_moved)
    assert moved_state.portfolio_version == lawfully_moved.portfolio_version
    assert moved_state.available_capital == 9000.0


def test_ac_006_a_rejected_attempt_changes_nothing_at_all() -> None:
    """ATDD-R5-C-capital-intelligence/AC-006: a rejection is not a partial write.

    GIVEN a current snapshot and plans that declare a stale expected version, an
    underfunded amount or a non-positive amount
    WHEN each is committed
    THEN each is rejected with its declared reason in the frozen order, publishes no
    snapshot, no successor version and no consumed capital, and a later valid plan on
    the same version is refused for the missing authorisation instead of consuming
    anything, so nothing was written by any of the refusals

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical closing step expected a
    later valid plan to still commit (`COMMITTED`), which proved nothing had been
    written. With no owner-ratified funding policy the closing step is a
    `DEPLOYABLE_BOUND_UNAVAILABLE` refusal, and inertness is proved by the book
    re-deriving to its own identity instead.
    PENDING INTEGRATION PROOF: "a valid plan on an untouched version commits after
    earlier rejections" once a ratified funding policy and trusted upstream
    consumption evidence exist (owned by the
    `ATDD-R5-C-deployable-bound-closure` increment contract).
    """

    record = commit_fixture("PAPER-LEDGER-R5C-COMMIT-2")
    stale = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version),
        at_time=at(60),
        expected_portfolio_version="pv-r5c-0002",
        deployable_policy=book_policy(record),
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
        deployable_policy=book_policy(record),
    )
    assert another_version.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert another_version.snapshot is None

    underfunded = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, amount=20000.0, candidate="PCAND:R5C:3"),
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert underfunded.rejection is CapitalCommitRejection.INSUFFICIENT_FREE_CASH
    assert underfunded.snapshot is None
    assert underfunded.committed_capital is None

    empty = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, amount=0.0, candidate="PCAND:R5C:4"),
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert empty.rejection is CapitalCommitRejection.NON_POSITIVE_AMOUNT
    assert empty.snapshot is None

    accepted = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, candidate="PCAND:R5C:5"),
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert accepted.status is CapitalCommitStatus.REJECTED
    assert accepted.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert accepted.snapshot is None
    assert accepted.successor_version is None
    assert accepted.committed_capital is None
    # Nothing any of the five attempts was offered wrote anything to the book.
    assert CapitalSnapshot.from_dict(record.to_dict()).snapshot_id == record.snapshot_id


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
    live_result = commit_reservation(
        live, plan=plan(version=VERSION), at_time=at(60), deployable_policy=book_policy(live)
    )
    assert live_result.rejection is CapitalCommitRejection.ENVIRONMENT_NOT_FUNDABLE
    assert live_result.snapshot is None
    shadow = commit_fixture(
        "PAPER-LEDGER-R5C-COMMIT-4", environment=CapitalEnvironment.SHADOW_EVIDENCE
    )
    shadow_result = commit_reservation(
        shadow, plan=plan(version=VERSION), at_time=at(60), deployable_policy=book_policy(shadow)
    )
    assert shadow_result.rejection is CapitalCommitRejection.ENVIRONMENT_NOT_FUNDABLE

    funded = commit_fixture("PAPER-LEDGER-R5C-COMMIT-5")
    for trigger in (
        CapitalRecomputeTrigger.FEE,
        CapitalRecomputeTrigger.DEPOSIT,
        CapitalRecomputeTrigger.APPROVAL,
    ):
        refused = commit_reservation(
            funded,
            plan=plan(version=VERSION),
            at_time=at(60),
            trigger=trigger,
            deployable_policy=book_policy(funded),
        )
        assert refused.rejection is CapitalCommitRejection.UNSUPPORTED_RECOMPUTE_TRIGGER
        assert refused.snapshot is None
    with pytest.raises(CapitalIntelligenceError, match="trigger"):
        commit_reservation(
            funded,
            plan=plan(version=VERSION),
            at_time=at(60),
            trigger="RESERVATION",
            deployable_policy=book_policy(funded),
        )

    outlived = commit_reservation(
        funded,
        plan=plan(version=VERSION),
        at_time=at(901),
        deployable_policy=book_policy(funded),
    )
    assert outlived.rejection is CapitalCommitRejection.SNAPSHOT_OUTLIVED_VALIDITY
    with pytest.raises(CapitalIntelligenceError, match="as_of"):
        commit_reservation(
            funded,
            plan=plan(version=VERSION),
            at_time=T0,
            deployable_policy=book_policy(funded),
        )
    with pytest.raises(CapitalIntelligenceError):
        commit_reservation(
            funded,
            plan="PRSV:pasted",  # type: ignore[arg-type]
            at_time=at(60),
            deployable_policy=book_policy(funded),
        )
    with pytest.raises(CapitalIntelligenceError):
        commit_reservation(
            "not-a-snapshot",  # type: ignore[arg-type]
            plan=plan(version=VERSION),
            at_time=at(60),
            deployable_policy=book_policy(funded),
        )


def test_ac_006_a_plan_outside_its_declared_window_is_refused() -> None:
    """ATDD-R5-C-capital-intelligence/AC-006: an approval window is enforced.

    GIVEN a plan created in the future, a plan whose reservation window has closed
    while the snapshot is still valid, a plan a release has partially consumed and a
    plan carrying a version this increment never declared
    WHEN each is committed
    THEN the future plan is not yet valid, a plan created exactly at the commit
    instant is inside its window, a plan whose window is still open one second before
    expiry is not refused for its window, the closed and partially consumed plans are
    refused without consuming capital, the undeclared plan version fails closed in the
    frozen owner, and an expired plan is refused as expired rather than replayed

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical criterion expected a
    boundary-window plan and the whole two-leg plan to commit (`COMMITTED`) and the
    expired plan to replay (`IDEMPOTENT_REPLAY`). With no owner-ratified funding
    policy no plan commits at all, so the window and expiry behaviour is proved by
    which refusal each plan receives instead.
    PENDING INTEGRATION PROOF: "a plan inside its window commits and a retry of that
    committed plan replays" once a ratified funding policy and trusted upstream
    consumption evidence exist (owned by the
    `ATDD-R5-C-deployable-bound-closure` increment contract).
    """

    future_book = commit_fixture("PAPER-LEDGER-R5C-WINDOW-1")
    early = commit_reservation(
        future_book,
        plan=plan(version=future_book.portfolio_version, created_at=at(120)),
        at_time=at(60),
        deployable_policy=book_policy(future_book),
    )
    assert early.status is CapitalCommitStatus.REJECTED
    assert early.rejection is CapitalCommitRejection.PLAN_NOT_YET_VALID
    assert early.snapshot is None

    # A plan created exactly at the commit instant is inside its window: the boundary
    # itself passes the window check and is refused for the missing authorisation.
    boundary = commit_reservation(
        future_book,
        plan=plan(
            version=future_book.portfolio_version,
            created_at=at(60),
            candidate="PCAND:R5C:BOUNDARY",
        ),
        at_time=at(60),
        deployable_policy=book_policy(future_book),
    )
    assert boundary.status is CapitalCommitStatus.REJECTED
    assert boundary.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert boundary.rejection is not CapitalCommitRejection.PLAN_NOT_YET_VALID
    assert boundary.applied_portfolio_version == future_book.portfolio_version
    assert boundary.snapshot is None

    closed_book = commit_fixture("PAPER-LEDGER-R5C-WINDOW-2")
    closing_plan = plan(version=closed_book.portfolio_version, expires_at=at(600))
    still_open = commit_reservation(
        closed_book,
        plan=closing_plan,
        at_time=at(599),
        deployable_policy=book_policy(closed_book),
    )
    assert still_open.status is CapitalCommitStatus.REJECTED
    assert still_open.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert still_open.rejection is not CapitalCommitRejection.PLAN_EXPIRED
    expiring = commit_reservation(
        closed_book,
        plan=plan(
            version=closed_book.portfolio_version,
            expires_at=at(600),
            candidate="PCAND:R5C:EXPIRED",
        ),
        at_time=at(600),
        deployable_policy=book_policy(closed_book),
    )
    assert expiring.rejection is CapitalCommitRejection.PLAN_EXPIRED
    assert expiring.snapshot is None
    assert expiring.committed_capital is None

    # The plan was never committed, so a retry after its window closed is refused as
    # expired rather than replayed: expiry is still enforced ahead of authorisation.
    replay = commit_reservation(
        closed_book,
        plan=closing_plan,
        at_time=at(700),
        deployable_policy=book_policy(closed_book),
    )
    assert replay.status is CapitalCommitStatus.REJECTED
    assert replay.rejection is CapitalCommitRejection.PLAN_EXPIRED
    assert replay.snapshot is None

    partial_book = commit_fixture("PAPER-LEDGER-R5C-WINDOW-3")
    two_legs = plan(
        version=partial_book.portfolio_version, second_candidate="PCAND:R5C:PARTIAL-B"
    )
    released = release_reservation(
        two_legs,
        candidate_id="PCAND:R5C:1",
        reason=ReservationReleaseReason.CANCELLED,
        at_time=at(60),
    )
    assert released.planned_capital == 1000.0
    partial = commit_reservation(
        partial_book,
        plan=released,
        at_time=at(60),
        deployable_policy=book_policy(partial_book),
    )
    assert partial.rejection is CapitalCommitRejection.PLAN_EXPIRED
    assert partial.snapshot is None
    whole = commit_reservation(
        partial_book,
        plan=two_legs,
        at_time=at(60),
        deployable_policy=book_policy(partial_book),
    )
    assert whole.status is CapitalCommitStatus.REJECTED
    assert whole.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert whole.committed_capital is None
    assert partial_book.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")

    undeclared_book = commit_fixture("PAPER-LEDGER-R5C-WINDOW-4")
    legs = plan(version=undeclared_book.portfolio_version).reservations
    with pytest.raises(PortfolioContractError, match="plan_schema_version"):
        PortfolioReservationPlan(
            portfolio_version=undeclared_book.portfolio_version,
            window_identity="PWIN:R5C:WINDOW-4",
            created_at=T0,
            reservations=legs,
            plan_schema_version="portfolio-reservation-plan-v0",
        )
    with pytest.raises(PortfolioContractError, match="plan_id"):
        PortfolioReservationPlan(
            portfolio_version=undeclared_book.portfolio_version,
            window_identity="PWIN:R5C:WINDOW-4",
            created_at=T0,
            reservations=legs,
            plan_id="PRSV:forged",
        )


def test_ac_006_a_reservation_never_extends_the_snapshot_validity() -> None:
    """ATDD-R5-C-capital-intelligence/AC-006: a reservation is not a re-observation.

    GIVEN a snapshot whose account evidence was observed once with a declared
    validity deadline
    WHEN reservations are committed against it, chained, offered at the deadline and
    read after it, and when the account is later genuinely re-observed
    THEN a lawful RESERVATION recompute carries the same source evidence and the same
    deadline, chained reservations cannot extend it, the deadline and any later read
    fail closed, and only a genuine re-observation with its own declared window moves
    the deadline

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical criterion asserted the
    deadline-carrying successor of a *committed* reservation. With no owner-ratified
    funding policy the commit refuses, so the carrying law is proved on a lawful
    RESERVATION recompute of the same book.
    PENDING INTEGRATION PROOF: "a committed reservation cannot extend the snapshot
    validity deadline" once a ratified funding policy and trusted upstream consumption
    evidence exist (owned by the `ATDD-R5-C-deployable-bound-closure` contract).
    """

    book = commit_fixture("PAPER-LEDGER-R5C-VALIDITY-1")
    assert book.freshness.validity_deadline == at(900)

    # A reservation is a movement of cash, not a re-observation of the account, so the
    # declared recompute law accepts a RESERVATION successor that carries the source
    # evidence and the deadline forward unchanged.
    reserved_snapshot = successor(
        book,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        freshness=fresh(as_of=at(60), deadline_seconds=840),
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
        limit_usage=declared_usage(),
    )
    assert_snapshot_recomputes_from(
        book, reserved_snapshot, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    assert reserved_snapshot.freshness.as_of == at(60)
    assert reserved_snapshot.freshness.validity_deadline == book.freshness.validity_deadline
    assert reserved_snapshot.source_watermark == book.source_watermark
    assert reserved_snapshot.reconciliation == book.reconciliation
    assert reserved_snapshot.marks == book.marks
    assert reserved_snapshot.fee_state == book.fee_state

    chained = successor(
        reserved_snapshot,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        freshness=fresh(as_of=at(120), deadline_seconds=780),
        cash_ledger=cash(free="8000.00", reserves="2000.00", safety="500.00"),
        limit_usage=declared_usage(),
    )
    assert_snapshot_recomputes_from(
        reserved_snapshot, chained, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    assert chained.freshness.validity_deadline == book.freshness.validity_deadline
    assert chained.source_watermark == book.source_watermark
    assert (
        evaluate_deployable_capital(
            chained, policy=book_policy(chained), at_time=at(901)
        ).abstention_reason
        is CapitalDeployableAbstentionReason.SNAPSHOT_OUTLIVED_VALIDITY
    )
    at_deadline = commit_reservation(
        chained,
        plan=plan(version=chained.portfolio_version, candidate="PCAND:R5C:DEADLINE"),
        at_time=at(900),
        deployable_policy=book_policy(chained),
    )
    assert at_deadline.rejection is CapitalCommitRejection.SNAPSHOT_OUTLIVED_VALIDITY
    assert at_deadline.snapshot is None
    assert chained.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("2000.00")

    # Only a genuine re-observation moves the deadline: the fee recompute below
    # declares a newer source watermark and its own window, and the refreshed record
    # is deployable long after the original deadline had passed.
    observed = commit_fixture("PAPER-LEDGER-R5C-VALIDITY-2")
    observed_successor = successor(
        observed,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
        limit_usage=declared_usage(),
    )
    assert observed_successor.snapshot_id != observed.snapshot_id
    refreshed = successor(
        observed_successor,
        trigger=CapitalRecomputeTrigger.FEE,
        seconds=540,
        cash_ledger=cash(free="8990.00", reserves="1000.00", safety="500.00"),
        fee_state=fee_state(fees="10.00", sequence=8),
        source_watermark=watermark(8, at(600)),
        freshness=fresh(as_of=at(600), deadline_seconds=900),
        limit_usage=declared_usage(),
    )
    assert_snapshot_recomputes_from(
        observed_successor, refreshed, trigger=CapitalRecomputeTrigger.FEE
    )
    assert refreshed.freshness.validity_deadline == at(1500)
    assert refreshed.source_watermark.ledger_sequence == 8
    assert (
        evaluate_deployable_capital(refreshed, policy=book_policy(refreshed), at_time=at(1000)).status
        is CapitalDeployableStatus.AVAILABLE
    )


# ---------------------------------------------------------------------------
# AC-007 Concurrency honesty: one winner, no double spend, honest replay
# ---------------------------------------------------------------------------


def test_ac_007_a_replay_of_a_committed_plan_consumes_nothing_twice() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: retries create nothing twice.

    GIVEN a refused reservation of one plan
    WHEN the very same plan is presented again, and when a competing plan is offered
    after a lawful recompute has moved the tracked version of the book
    THEN the retry reproduces the identical refusal instead of consuming anything
    twice, nothing was written by either attempt, and the competing plan written
    against the replaced version is refused as stale

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical criterion expected an
    idempotent replay of a recorded committed successor (`IDEMPOTENT_REPLAY`). With no
    owner-ratified funding policy nothing is ever recorded, so determinism of the
    refusal replaces the replay proof and the tracked-version boundary is proved with
    a lawful recompute instead of a commit.
    PENDING INTEGRATION PROOF: "a replay of a committed plan returns the recorded
    successor and consumes nothing twice" once a ratified funding policy and trusted
    upstream consumption evidence exist (owned by the
    `ATDD-R5-C-deployable-bound-closure` increment contract).
    """

    record = commit_fixture("PAPER-LEDGER-R5C-CONCURRENCY-1")
    reservation_plan = plan(version=record.portfolio_version)
    first = commit_reservation(
        record,
        plan=reservation_plan,
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert first.status is CapitalCommitStatus.REJECTED
    assert first.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert first.snapshot is None

    replay = commit_reservation(
        record,
        plan=reservation_plan,
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert replay.status is CapitalCommitStatus.REJECTED
    assert replay.status is not CapitalCommitStatus.IDEMPOTENT_REPLAY
    assert replay.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert replay.commit_id == first.commit_id
    assert replay.snapshot is None
    assert replay.committed_capital is None
    assert replay.successor_version is None
    assert len(record.applied_reservation_references) == 0
    assert record.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")
    assert CapitalSnapshot.from_dict(record.to_dict()).snapshot_id == record.snapshot_id

    # A lawful recompute - not a commit - is what moves the version this process tracks
    # for the book, so a competing plan written against the replaced version is stale.
    advanced = successor(
        record,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=at(120),
            reference="RECON:2",
            variance="0.00",
        ),
        source_watermark=watermark(8, at(120)),
        freshness=fresh(as_of=at(120)),
    )
    assert_snapshot_recomputes_from(
        record, advanced, trigger=CapitalRecomputeTrigger.RECONCILIATION
    )
    competing = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, candidate="PCAND:R5C:COMPETING"),
        at_time=at(180),
        deployable_policy=book_policy(record),
    )
    assert competing.status is CapitalCommitStatus.REJECTED
    assert competing.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert competing.snapshot is None


def test_ac_007_a_plan_consumed_at_another_version_is_not_replayed() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: a plan is consumed once per book.

    GIVEN a plan refused against one version of a book and a lawful recompute of that
    same book
    WHEN the same plan is offered again, and then against the recomputed version
    THEN the repeat reproduces the identical refusal with nothing consumed, and the
    plan is refused against the successor because it does not belong to that version

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical criterion expected the
    already-consumed disposition `PLAN_ALREADY_CONSUMED`, which only a plan recorded by
    a successful commit can produce. With no owner-ratified funding policy no plan is
    ever recorded, so the same scenario proves the version boundary instead.
    PENDING INTEGRATION PROOF: "a plan consumed at one version is refused as already
    consumed rather than replayed onto another version" once a ratified funding policy
    and trusted upstream consumption evidence exist (owned by the
    `ATDD-R5-C-deployable-bound-closure` increment contract).
    """

    record = commit_fixture("PAPER-LEDGER-R5C-CONCURRENCY-2")
    reservation_plan = plan(version=record.portfolio_version)
    first = commit_reservation(
        record,
        plan=reservation_plan,
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert first.status is CapitalCommitStatus.REJECTED
    assert first.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert first.snapshot is None

    again = commit_reservation(
        record,
        plan=reservation_plan,
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert again.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert again.rejection is not CapitalCommitRejection.PLAN_ALREADY_CONSUMED
    assert again.commit_id == first.commit_id
    assert again.snapshot is None
    assert again.committed_capital is None

    advanced = successor(
        record,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=at(120),
            reference="RECON:2",
            variance="0.00",
        ),
        source_watermark=watermark(8, at(120)),
        freshness=fresh(as_of=at(120)),
    )
    assert advanced.portfolio_version == "pv-r5c-0001.0001"
    assert_snapshot_recomputes_from(
        record, advanced, trigger=CapitalRecomputeTrigger.RECONCILIATION
    )
    offered_against_successor = commit_reservation(
        advanced,
        plan=reservation_plan,
        at_time=at(180),
        deployable_policy=book_policy(advanced),
    )
    assert offered_against_successor.status is CapitalCommitStatus.REJECTED
    assert (
        offered_against_successor.rejection
        is CapitalCommitRejection.STALE_EXPECTED_VERSION
    )
    assert offered_against_successor.snapshot is None
    assert offered_against_successor.committed_capital is None



def test_ac_007_a_losing_racer_never_double_spends() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: one winner, many honest losers.

    GIVEN two decisions that compete for the same contested cash at the same version,
    started together
    WHEN both are committed concurrently
    THEN neither consumes anything, both are refused deterministically with the same
    declared reason, the contested partition never moves, and the book stays
    replannable because no attempt was recorded against it

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical criterion expected exactly
    one racer to win (`COMMITTED`) and the other to be refused as stale. With no
    owner-ratified funding policy there is no winner to have, so the same concurrent
    scenario proves that contested capacity is never consumed by accident and that no
    partial write appears under concurrency.
    PENDING INTEGRATION PROOF: "exactly one of two concurrent racers consumes the
    contested capacity and the loser can replan against the committed version" once a
    ratified funding policy and trusted upstream consumption evidence exist (owned by
    the `ATDD-R5-C-deployable-bound-closure` increment contract).
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
        results.append(
            commit_reservation(
                record,
                plan=reservation_plan,
                at_time=at(60),
                deployable_policy=book_policy(record),
            )
        )

    threads = [threading.Thread(target=race, args=(row,)) for row in racers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert len(results) == len(racers)
    assert sorted(result.status.value for result in results) == ["REJECTED", "REJECTED"]
    for result in results:
        assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
        assert result.snapshot is None
        assert result.snapshot_id is None
        assert result.committed_capital is None
        assert result.successor_version is None
    # The contested partition moved exactly zero times and nothing was recorded.
    assert record.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")
    assert record.cash.cash_total == Decimal("10500.00")
    assert record.applied_reservation_references == ()
    assert CapitalSnapshot.from_dict(record.to_dict()).snapshot_id == record.snapshot_id

    # No attempt was recorded against the book, so it stays replannable: a further
    # decision is judged on its own merits rather than being refused as stale.
    replanned = commit_reservation(
        record,
        plan=plan(version=record.portfolio_version, candidate="PCAND:R5C:REPLANNED"),
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
    assert replanned.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert replanned.rejection is not CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert replanned.snapshot is None

    # The same "reread and replan" obligation, proved lawfully: a plan offered against
    # the version a reservation *would* have produced is judged on its own merits, and
    # the contested partition stays exactly as the book declared it.
    advanced = successor(
        record,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
        limit_usage=declared_usage(),
    )
    assert_snapshot_recomputes_from(
        record, advanced, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    assert advanced.portfolio_version == "pv-r5c-0001.0001"
    replanned_after_advance = commit_reservation(
        advanced,
        plan=plan(
            version=advanced.portfolio_version,
            candidate="PCAND:R5C:RACER-C",
            amount=250.0,
        ),
        at_time=at(120),
        deployable_policy=book_policy(advanced),
    )
    assert replanned_after_advance.status is CapitalCommitStatus.REJECTED
    assert (
        replanned_after_advance.rejection
        is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    )
    assert replanned_after_advance.snapshot is None
    assert replanned_after_advance.committed_capital is None
    assert advanced.cash.amount(CapitalPartition.FREE_CASH) == Decimal("9000.00")
    assert advanced.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("1000.00")




def test_ac_007_a_lawful_recompute_moves_the_tracked_current_version() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: the book follows its real lineage.

    GIVEN a lawful RESERVATION recompute of a book, a lawful reconciliation recompute
    of that successor and the replaced version the reconciliation superseded
    WHEN a reservation is offered against the current version and another against the
    replaced version
    THEN the reservation against the current version is judged on its merits instead of
    being refused as stale, and the replaced version never becomes current again

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical criterion expected the first
    and the second reservation to commit (`COMMITTED`). With no owner-ratified funding
    policy neither commits, so the tracked-version law is proved by which version each
    attempt is judged against.
    PENDING INTEGRATION PROOF: "consecutive reservations consume the version that is
    actually current for the book" once a ratified funding policy and trusted upstream
    consumption evidence exist (owned by the `ATDD-R5-C-deployable-bound-closure`
    increment contract).
    """

    book = commit_fixture("PAPER-LEDGER-R5C-VERSION-CHAIN-1")
    reserved = successor(
        book,
        trigger=CapitalRecomputeTrigger.RESERVATION,
        cash_ledger=cash(free="9000.00", reserves="1000.00", safety="500.00"),
        limit_usage=declared_usage(),
    )
    assert_snapshot_recomputes_from(
        book, reserved, trigger=CapitalRecomputeTrigger.RESERVATION
    )
    assert reserved.portfolio_version == "pv-r5c-0001.0001"

    reconciled = successor(
        reserved,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=at(120),
            reference="RECON:2",
            variance="0.00",
        ),
        source_watermark=watermark(8, at(120)),
        freshness=fresh(as_of=at(120)),
        limit_usage=declared_usage(),
    )
    assert reconciled.portfolio_version == "pv-r5c-0001.0002"
    assert_snapshot_recomputes_from(
        reserved, reconciled, trigger=CapitalRecomputeTrigger.RECONCILIATION
    )

    # The reconciliation moved the version this process tracks for the book, so an
    # attempt against the current version is judged on its merits rather than as stale.
    second = commit_reservation(
        reconciled,
        plan=plan(version=reconciled.portfolio_version, candidate="PCAND:R5C:AFTER-RECON"),
        at_time=at(180),
        deployable_policy=book_policy(reconciled),
    )
    assert second.status is CapitalCommitStatus.REJECTED
    assert second.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert second.snapshot is None

    # The replaced version never becomes current again.
    replaced = commit_reservation(
        reserved,
        plan=plan(version=reserved.portfolio_version, candidate="PCAND:R5C:REPLACED"),
        at_time=at(240),
        deployable_policy=book_policy(reserved),
    )
    assert replaced.status is CapitalCommitStatus.REJECTED
    assert replaced.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert replaced.snapshot is None


def test_ac_007_a_competing_successor_is_refused_but_a_replay_is_not() -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: one version, one successor.

    GIVEN a committed snapshot and two different lawful recomputes of it, plus an
    unrelated book that uses the same version strings
    WHEN both are asserted as that version's successor and when the identical
    successor is asserted twice
    THEN the first successor is the only one accepted for that book, the identical
    replay is not treated as a fork, and the unrelated book is never refuted by a
    similar version string
    """

    base = commit_fixture("PAPER-LEDGER-R5C-FORK-1")
    first = successor(
        base,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=at(60),
            reference="RECON:2",
            variance="0.00",
        ),
        source_watermark=watermark(8),
        limit_usage=declared_usage(),
    )
    assert_snapshot_recomputes_from(base, first, trigger=CapitalRecomputeTrigger.RECONCILIATION)
    # The identical successor replayed is the same claim, not a second one.
    assert_snapshot_recomputes_from(base, first, trigger=CapitalRecomputeTrigger.RECONCILIATION)

    competing = successor(
        base,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=at(60),
            reference="RECON:3",
            variance="0.00",
        ),
        source_watermark=watermark(8),
        limit_usage=declared_usage(),
    )
    assert competing.portfolio_version == first.portfolio_version
    assert competing.snapshot_id != first.snapshot_id
    with pytest.raises(CapitalIntelligenceError, match="unforked"):
        assert_snapshot_recomputes_from(
            base, competing, trigger=CapitalRecomputeTrigger.RECONCILIATION
        )

    other = commit_fixture("PAPER-LEDGER-R5C-FORK-2")
    other_first = successor(
        other,
        trigger=CapitalRecomputeTrigger.RECONCILIATION,
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=at(60),
            reference="RECON:2",
            variance="0.00",
        ),
        source_watermark=watermark(8),
        limit_usage=declared_usage(),
    )
    assert other_first.portfolio_version == first.portfolio_version
    assert other_first.snapshot_id != first.snapshot_id
    assert_snapshot_recomputes_from(
        other, other_first, trigger=CapitalRecomputeTrigger.RECONCILIATION
    )


def test_ac_007_the_witness_is_bounded_and_declares_its_retention_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATDD-R5-C-capital-intelligence/AC-007: a bounded witness, honestly labelled.

    GIVEN the process-scoped witness and a deliberately small retention cap
    WHEN many records and many records of one version are witnessed, and when a
    successor is asserted for a predecessor the cap has already evicted
    THEN both witness buckets and the per-version bucket stay inside the cap, the
    eviction removes the proof rather than corrupting anything, the tracked current
    version never moves backwards, and the increment declares that retention window
    instead of claiming durable, restart-safe ownership
    """

    assert CAPITAL_RESERVATION_DURABILITY_STATUS == "PROCESS_SCOPED_IN_MEMORY_WITNESS"
    assert CAPITAL_RESERVATION_DURABILITY_DEPENDENCY == "R5-E"
    assert "Restart, multi-worker and durable-ledger safety are explicitly NOT claimed" in r5c_source()

    bounded_attribute = "_opip_r5_c_capital_witness_bounded_test"
    monkeypatch.setattr(capital_module, "_CAPITAL_WITNESS_LIMIT", 4)
    monkeypatch.setattr(capital_module, "_CAPITAL_WITNESS_ATTRIBUTE", bounded_attribute)
    try:
        holder = capital_module._capital_witness()
        records = [snapshot(account="PAPER-LEDGER-R5C-BOUNDED")]
        for _ in range(7):
            records.append(successor(records[-1], trigger=CapitalRecomputeTrigger.APPROVAL))
        assert len(holder["snapshots"]) <= 4
        assert len(holder["versions"]) <= 4

        assert_snapshot_recomputes_from(
            records[6], records[7], trigger=CapitalRecomputeTrigger.APPROVAL
        )
        identity = (
            records[7].account,
            records[7].environment.value,
            records[7].engine,
            records[7].currency,
        )
        assert holder["current"][identity] == records[7].portfolio_version

        # The base of the chain has been evicted, so its competing successor is
        # accepted here: the proof is retained-window scoped, which is exactly why
        # the increment declares the witness process-scoped and guards the
        # capital-creating path with the tracked version, the trust gate and free
        # cash as well. The evicted, older version never becomes current again.
        late = successor(records[0], trigger=CapitalRecomputeTrigger.APPROVAL)
        assert_snapshot_recomputes_from(
            records[0], late, trigger=CapitalRecomputeTrigger.APPROVAL
        )
        assert late.portfolio_version != records[7].portfolio_version
        assert holder["current"][identity] == records[7].portfolio_version

        same_version = [
            snapshot(
                account="PAPER-LEDGER-R5C-BOUNDED-SAME",
                cash_ledger=cash(free=f"{1000 + index}.00", safety="500.00"),
            )
            for index in range(6)
        ]
        key = (
            same_version[-1].account,
            same_version[-1].environment.value,
            same_version[-1].engine,
            same_version[-1].currency,
            VERSION,
        )
        assert len(holder["versions"][key]) == 4
        assert holder["versions"][key][-1] == same_version[-1].snapshot_id
    finally:
        if hasattr(sys, bounded_attribute):
            delattr(sys, bounded_attribute)


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

    GIVEN a declared occupancy series whose cumulative fees and carry are declared
    WHEN the series is measured
    THEN the cumulative-cost channel is integrated over the same preserved instants
    under the same declared endpoint policy, subtracted once, and measured the same
    way however finely the same economic history was sampled, so a one-off cost is
    never scaled by the interval it happened to be observed across
    """

    carried = (
        capital_occupancy_point(
            at=T0, allocated_basis="2000.00", cumulative_fees="5.00", cumulative_carry="3.00"
        ),
        capital_occupancy_point(
            at=at(7200), allocated_basis="2000.00", cumulative_fees="5.00", cumulative_carry="3.00"
        ),
    )
    measured = measure_capital_hours(carried, policy=hours_policy())
    assert measured.capital_hours == Decimal("4000.0000")
    assert measured.cost_capital_hours == Decimal("16.0000")
    assert measured.net_capital_hours == Decimal("3984.0000")
    assert measured.net_capital_hours == measured.capital_hours - measured.cost_capital_hours

    stepped = (
        capital_occupancy_point(
            at=T0, allocated_basis="2000.00", cumulative_fees="5.00", cumulative_carry="3.00"
        ),
        capital_occupancy_point(
            at=at(7200), allocated_basis="2000.00", cumulative_fees="8.00", cumulative_carry="4.00"
        ),
    )
    from_start = measure_capital_hours(
        stepped, policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_START)
    )
    from_end = measure_capital_hours(
        stepped, policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_END)
    )
    assert from_start.cost_capital_hours == Decimal("16.0000")
    assert from_end.cost_capital_hours == Decimal("24.0000")
    assert from_start.hours_id != from_end.hours_id

    # The same economic history, sampled more finely, measures the same cost: the
    # channel is a step integral of the level carried through each interval, not a
    # movement inside one interval scaled by its length.
    finer = (
        capital_occupancy_point(
            at=T0, allocated_basis="2000.00", cumulative_fees="5.00", cumulative_carry="3.00"
        ),
        capital_occupancy_point(
            at=at(3600), allocated_basis="2000.00", cumulative_fees="5.00", cumulative_carry="3.00"
        ),
        capital_occupancy_point(
            at=at(7200), allocated_basis="2000.00", cumulative_fees="8.00", cumulative_carry="4.00"
        ),
    )
    assert (
        measure_capital_hours(finer, policy=hours_policy()).cost_capital_hours
        == from_start.cost_capital_hours
        == Decimal("16.0000")
    )

    # The endpoint policy is declared, and it decides where a sample lands relative
    # to a step in *both* channels: the basis integral moves by the same declared
    # rule as the cost integral once a point is placed before the step, so the
    # dependence on sampling is a property of the declared policy and not of one
    # channel.
    stepping_basis = (
        capital_occupancy_point(at=T0, allocated_basis="1000.00", cumulative_fees="5.00"),
        capital_occupancy_point(
            at=at(7200), allocated_basis="2000.00", cumulative_fees="12.00"
        ),
    )
    stepping_basis_fine = (
        capital_occupancy_point(at=T0, allocated_basis="1000.00", cumulative_fees="5.00"),
        capital_occupancy_point(
            at=at(3600), allocated_basis="1000.00", cumulative_fees="5.00"
        ),
        capital_occupancy_point(
            at=at(7200), allocated_basis="2000.00", cumulative_fees="12.00"
        ),
    )
    coarse_start = measure_capital_hours(
        stepping_basis, policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_START)
    )
    fine_start = measure_capital_hours(
        stepping_basis_fine,
        policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_START),
    )
    assert coarse_start.capital_hours == fine_start.capital_hours == Decimal("2000.0000")
    assert coarse_start.cost_capital_hours == fine_start.cost_capital_hours == Decimal("10.0000")

    coarse_end = measure_capital_hours(
        stepping_basis, policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_END)
    )
    fine_end = measure_capital_hours(
        stepping_basis_fine,
        policy=hours_policy(endpoint=CapitalHoursEndpointPolicy.STEP_END),
    )
    assert coarse_end.capital_hours == Decimal("4000.0000")
    assert fine_end.capital_hours == Decimal("3000.0000")
    assert coarse_end.cost_capital_hours == Decimal("24.0000")
    assert fine_end.cost_capital_hours == Decimal("17.0000")

    # The same declared rule applies to a cost declared only at the end of the
    # window: STEP_END integrates the end level over the declared span (a longer
    # span means more currency-hours of carried cost), while STEP_START integrates
    # the level carried in, which is zero for a cost that was never declared
    # before it happened. Both are the declared policy, not a sampling artefact.
    def one_off(hours: int, endpoint: CapitalHoursEndpointPolicy) -> CapitalHoursMeasure:
        return measure_capital_hours(
            (
                capital_occupancy_point(at=T0, allocated_basis="2000.00"),
                capital_occupancy_point(
                    at=at(hours * 3600), allocated_basis="2000.00", cumulative_fees="10.00"
                ),
            ),
            policy=hours_policy(endpoint=endpoint),
        )

    short = one_off(1, CapitalHoursEndpointPolicy.STEP_START)
    long = one_off(10, CapitalHoursEndpointPolicy.STEP_START)
    assert short.cost_capital_hours == long.cost_capital_hours == Decimal("0.0000")
    assert short.net_capital_hours == short.capital_hours
    assert long.net_capital_hours == long.capital_hours
    assert one_off(1, CapitalHoursEndpointPolicy.STEP_END).cost_capital_hours == Decimal("10.0000")
    assert one_off(10, CapitalHoursEndpointPolicy.STEP_END).cost_capital_hours == Decimal(
        "100.0000"
    )

    duplicated = points((0, "1000.00"), (0, "1000.00"))
    assert (
        measure_capital_hours(duplicated, policy=hours_policy()).unavailable_reason
        is CapitalHoursUnavailableReason.POINTS_NOT_MONOTONE
    )


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
    refusal = commit_reservation(
        record,
        plan=stale_plan,
        at_time=at(60),
        deployable_policy=book_policy(record),
    )
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


#: Modules owner-ratified as consumers of the R5-C capital record. The R5-E
#: capital-admission seam (`ATDD-R5-E-capital-admission-integration`) is that first
#: approved wiring: it reads one snapshot as its declared input, grants no authority,
#: and no runtime root below may import the record's owner.
DECLARED_RECORD_CONSUMERS = frozenset({"app/services/capital_admission_integration.py"})


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
        if path != MODULE
        and "capital_rotation_intelligence" in module_source(path)
        and path.relative_to(APP_ROOT).as_posix() not in DECLARED_RECORD_CONSUMERS
    ]
    assert consumers == []
    for declared in sorted(DECLARED_RECORD_CONSUMERS):
        assert (APP_ROOT / declared).is_file(), declared


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

    GIVEN a snapshot, a deployable view, a commit attempt, a measurement, a cohort
    and an aggregate
    WHEN each record's flag and serialized keys are inspected
    THEN every record declares itself non-authoritative, no serialized record
    carries an authority or secret key, and the component records carry no
    authority flag at all
    """

    record = snapshot()
    view = evaluate_deployable_capital(record, policy=policy(), at_time=at(60))
    posture_book = commit_fixture("PAPER-LEDGER-R5C-POSTURE")
    commit = commit_reservation(
        posture_book,
        plan=plan(version=VERSION),
        at_time=at(60),
        deployable_policy=book_policy(posture_book),
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


def test_ac_011_the_increment_claims_no_durable_or_restart_safe_allocation() -> None:
    """ATDD-R5-C-capital-intelligence/AC-011: the witness is not a store.

    GIVEN every record this increment produces and the process-scoped reservation
    witness
    WHEN the declared durability status, the module source and a refused commit record
    are inspected
    THEN the increment declares its witness process-scoped and in-memory, names the
    `R5-E` delivery that would own a canonical durable reservation record, states that
    restart, multi-worker and durable-ledger safety are NOT claimed, and exposes no
    persistence facility at all

    SUPERSEDED IN THE SHADOW-ONLY POSTURE: the historical criterion inspected the
    record of a *committed* reservation. With no owner-ratified funding policy the
    seam refuses, so the non-authority property is inspected on the refusal record
    instead; the durability declaration itself is unchanged.
    PENDING INTEGRATION PROOF: "a committed reservation record stays non-authoritative
    and non-durable" once a ratified funding policy exists (owned by the
    `ATDD-R5-C-deployable-bound-closure` increment contract).
    """

    assert CAPITAL_RESERVATION_DURABILITY_STATUS == "PROCESS_SCOPED_IN_MEMORY_WITNESS"
    assert CAPITAL_RESERVATION_DURABILITY_DEPENDENCY == "R5-E"
    source = r5c_source()
    assert (
        "Restart, multi-worker and durable-ledger safety are explicitly NOT claimed"
        in source
    )
    assert "_CAPITAL_WITNESS_LIMIT" in source
    for token in FORBIDDEN_PERSISTENCE_TOKENS:
        assert token not in source, token

    durability_book = commit_fixture("PAPER-LEDGER-R5C-DURABILITY")
    result = commit_reservation(
        durability_book,
        plan=plan(version=VERSION),
        at_time=at(60),
        deployable_policy=book_policy(durability_book),
    )
    assert result.status is CapitalCommitStatus.REJECTED
    assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert result.authoritative is False
    assert result.snapshot is None
    assert result.to_dict()["authoritative"] is False


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
