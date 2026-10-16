"""R5-C deployable-bound closure acceptance tests.

This module proves the `ATDD-R5-C-deployable-bound-closure` increment: the
deployable-capital bound of `CI-02` and the atomic capital-and-risk-usage commit
of `CI-04` are actually *enforced* at the reservation seam of the existing capital
owner, and the capacity a commit consumes is carried forward instead of being
silently reusable.

The tests are deliberately adversarial about the boundary between *structural*
capital safety and *funding authority*:

* the deployable bound is established from the snapshot's own declared usage and a
  policy that is bound to the book's identity, inside the existing witness lock;
* an amount equal to an established bound is admissible while one cent above it is
  refused with `EXCEEDS_DEPLOYABLE_CAPITAL`, and every amount above free cash is
  refused earlier with `INSUFFICIENT_FREE_CASH`, so a missing bound is never
  reported as a numeric breach;
* the successor usage is exactly the snapshot's declared usage plus validated,
  additive, non-reducible deltas, and complete delta evidence is required before a
  successor can be built at all;
* no engineering-scenario policy grants funding authority. Only engineering
  fixtures exist (`OQ-06` is `OWNER_DECISION_REQUIRED`), so the commit seam fails
  closed with `DEPLOYABLE_BOUND_UNAVAILABLE` and the module declares that absence
  instead of inventing an approval marker;
* a configured sleeve whose usage row was never declared abstains with
  `SLEEVE_USAGE_NOT_DECLARED` rather than reading the whole cap as available, and
  the `HORIZON` dimension is a declared named-sleeve mapping with no independent
  numeric bound.

Every fixture is explicitly labelled an engineering scenario. Nothing here is
owner-ratified policy, no numeric threshold is ratified or invented, no durable
record, store, writer or ledger is added, and every record stays
`authoritative = False`. The tests are deterministic and hermetic: no clock, no
network, no database and no environment read.

`_money_from_float` is *preserved* by this increment and is tested, never changed;
the money-grid cases below pin both rounding directions inside the declared
tolerance so a later "simplification" of the crossing fails here.
"""

from __future__ import annotations

import ast
import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts.portfolio import (  # noqa: E402
    PortfolioDirection,
    PortfolioReservation,
    PortfolioReservationPlan,
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
    CAPITAL_COMMIT_AUTHORIZATION_DEPENDENCY,
    CAPITAL_COMMIT_AUTHORIZATION_STATUS,
    CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE,
    CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION,
    CAPITAL_HORIZON_BOUND_STATUS,
    CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION,
    CAPITAL_RESERVATION_DURABILITY_DEPENDENCY,
    CAPITAL_RESERVATION_DURABILITY_STATUS,
    MONEY_QUANTUM,
    OPEN_OWNER_INPUTS,
    OWNER_INPUT_STATUS,
    OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS,
    CapitalAccountingState,
    CapitalBound,
    CapitalCommitRejection,
    CapitalCommitStatus,
    CapitalDeployableAbstentionReason,
    CapitalDeployablePolicy,
    CapitalDeployableStatus,
    CapitalEnvironment,
    CapitalFeeState,
    CapitalFreshness,
    CapitalIntelligenceError,
    CapitalLimitKind,
    CapitalLimitUsage,
    CapitalMarkState,
    CapitalPartition,
    CapitalPosition,
    CapitalReconciliation,
    CapitalReconciliationStatus,
    CapitalRecomputeTrigger,
    CapitalSnapshot,
    CapitalSourceWatermark,
    CashPartitionLedger,
    ReservationUsageDelta,
    SleeveUsage,
    capital_deployable_engineering_scenario_policy,
    capital_policy_grants_funding_authority,
    commit_reservation,
    derive_successor_usage,
    evaluate_deployable_capital,
    missing_usage_delta_identities,
    reservation_consumption_rejection,
)

INCREMENT = "ATDD-R5-C-deployable-bound-closure"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
MODULE = APP_ROOT / "app" / "services" / "capital_rotation_intelligence.py"
R5C_BANNER = "# R5-C Capital Intelligence"

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
ACCOUNT = "PAPER-LEDGER-CLOSURE-01"
ENGINE = "paper-ledger-engine-v1"
CURRENCY = "USD"
VERSION = "pv-r5c-closure-0001"

RUNTIME_ROOTS = (
    APP_ROOT / "app" / "jobs",
    APP_ROOT / "app" / "api",
    APP_ROOT / "app" / "opip" / "decision",
    APP_ROOT / "app" / "opip" / "discovery",
    APP_ROOT / "app" / "opip" / "risk",
)

FORBIDDEN_AUTHORITY_TOKENS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "create_order",
    "withdraw(",
    "transfer(",
    "api_key",
    "api_secret",
    "credential",
)

FORBIDDEN_IMPORT_ROOTS = (
    "requests",
    "httpx",
    "socket",
    "sqlite3",
    "random",
    "ccxt",
    "krakenex",
    "telegram",
)

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
) -> CashPartitionLedger:
    partitions = {
        CapitalPartition.FREE_CASH: free,
        CapitalPartition.ORDER_RESERVES: reserves,
        CapitalPartition.SAFETY_RESERVE: safety,
        CapitalPartition.OTHER_BLOCKED_CASH: blocked,
    }
    total = f"{sum((Decimal(value) for value in partitions.values()), Decimal('0.00')):.2f}"
    return CashPartitionLedger(partitions=partitions, cash_total=total)


def position(
    *,
    symbol: str = "BTCUSDT",
    quantity: str = "0.1",
    basis: str = "3000.00",
    marked: str = "3120.00",
) -> CapitalPosition:
    return CapitalPosition(
        symbol=symbol,
        quantity=Decimal(quantity),
        basis_capital=basis,
        marked_value=marked,
        marked_at=T0,
        mark_reference=f"MARK:{symbol}:1",
    )


def snapshot(
    *,
    version: str = VERSION,
    account: str = ACCOUNT,
    free: str = "10000.00",
    risk: str = "0.00",
    concentration: str = "0.00",
    liquidity: str = "0.00",
    limits: tuple[CapitalLimitKind, ...] = tuple(CapitalLimitKind),
    sleeves: tuple[tuple[str, str], ...] = (("TACTICAL", "0.00"),),
    stale: bool | None = False,
    as_of: datetime | None = None,
    validity: datetime | None = None,
    environment: CapitalEnvironment = CapitalEnvironment.PAPER_LEDGER,
) -> CapitalSnapshot:
    """A declared paper-ledger book whose usage rows are explicit evidence.

    Only the limit families named in ``limits`` and the sleeves named in
    ``sleeves`` carry a usage row, so "not declared" is a real, constructible
    state rather than a fixture accident. Row order is preserved as given.
    """

    amounts = {
        CapitalLimitKind.RISK: risk,
        CapitalLimitKind.CONCENTRATION: concentration,
        CapitalLimitKind.LIQUIDITY: liquidity,
    }
    return CapitalSnapshot(
        portfolio_version=version,
        account=account,
        environment=environment,
        engine=ENGINE,
        currency=CURRENCY,
        accounting_state=CapitalAccountingState.SUPPORTED,
        cash=cash(free=free),
        positions=(position(),),
        marks=CapitalMarkState(
            marked_at=T0,
            mark_source="paper-ledger-mark-v1",
            marked_position_value="3120.00",
        ),
        sleeve_usage=tuple(
            SleeveUsage(sleeve=name, used_capital=used, as_of=T0)
            for name, used in sleeves
        ),
        limit_usage=tuple(
            CapitalLimitUsage(kind=kind, used_capital=amounts[kind], as_of=T0)
            for kind in limits
        ),
        fee_state=CapitalFeeState(
            fee_model_version="fee-model-v1",
            fees_settled="0.00",
            fee_watermark=CapitalSourceWatermark(
                ledger_sequence=7, observed_at=T0, state_is_stale=False
            ),
        ),
        source_watermark=CapitalSourceWatermark(
            ledger_sequence=7, observed_at=T0, state_is_stale=stale
        ),
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=T0,
            reference="RECON:CLOSURE:1",
            variance="0.00",
        ),
        freshness=CapitalFreshness(
            as_of=as_of if as_of is not None else T0,
            validity_deadline=validity if validity is not None else at(900),
            evidence_complete=True,
        ),
    )


def book_policy(record: CapitalSnapshot, **overrides) -> CapitalDeployablePolicy:
    """The *labelled engineering scenario* bound policy for one book.

    Its numbers are test fixtures. They are not ratified policy, they are never
    funding authority, and `capital_policy_grants_funding_authority` refuses them.
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


def plan(
    *,
    version: str = VERSION,
    amount: float = 100.0,
    candidate: str = "PCAND:CLOSURE:1",
    expires_at: datetime | None = None,
) -> PortfolioReservationPlan:
    created = T0
    expires = expires_at if expires_at is not None else created + timedelta(hours=1)
    return PortfolioReservationPlan(
        portfolio_version=version,
        window_identity="PWIN:CLOSURE:1",
        created_at=created,
        reservations=(
            PortfolioReservation(
                candidate_id=candidate,
                symbol="ETHUSDT",
                direction=PortfolioDirection.LONG,
                reserved_capital=amount,
                portfolio_version=version,
                created_at=created,
                expires_at=expires,
            ),
        ),
    )


def sparse_book(account: str, **overrides) -> CapitalSnapshot:
    """A book that declares only the RISK row and no sleeve row.

    It exists so a delta that names any other usage row is genuinely *undeclared* and can
    be used as a structurally malformed submission.
    """

    return snapshot(
        account=account, limits=(CapitalLimitKind.RISK,), sleeves=(), **overrides
    )


def delta(
    *,
    plan_id: str,
    amount: str = "0.00",
    limit_kind: CapitalLimitKind | None = None,
    sleeve: str | None = None,
    base: str | None = None,
    at_time: datetime | None = None,
) -> ReservationUsageDelta:
    return ReservationUsageDelta(
        plan_id=plan_id,
        base_portfolio_version=VERSION if base is None else base,
        as_of=at(60) if at_time is None else at_time,
        used_capital_delta=amount,
        limit_kind=limit_kind,
        sleeve=sleeve,
    )


def full_deltas(
    plan_record: PortfolioReservationPlan,
    *,
    risk: str = "0.00",
    concentration: str = "0.00",
    liquidity: str = "0.00",
    sleeve: str = "0.00",
    base: str | None = None,
    at_time: datetime | None = None,
    sleeves: tuple[str, ...] = ("TACTICAL",),
    limit_kinds: tuple[CapitalLimitKind, ...] | None = None,
) -> tuple[ReservationUsageDelta, ...]:
    """Declared consumption for every usage row a closure fixture declares."""

    wanted = tuple(CapitalLimitKind) if limit_kinds is None else limit_kinds
    rows = [
        delta(
            plan_id=plan_record.plan_id,
            amount=amount,
            limit_kind=kind,
            base=base,
            at_time=at_time,
        )
        for kind, amount in (
            (CapitalLimitKind.RISK, risk),
            (CapitalLimitKind.CONCENTRATION, concentration),
            (CapitalLimitKind.LIQUIDITY, liquidity),
        )
        if kind in wanted
    ]
    rows.extend(
        delta(
            plan_id=plan_record.plan_id,
            amount=sleeve,
            sleeve=name,
            base=base,
            at_time=at_time,
        )
        for name in sleeves
    )
    return tuple(rows)


def deployable(
    record: CapitalSnapshot,
    *,
    policy_record: CapitalDeployablePolicy | None = None,
    at_time: datetime | None = None,
):
    return evaluate_deployable_capital(
        record,
        policy=policy_record if policy_record is not None else book_policy(record),
        at_time=at_time if at_time is not None else at(60),
    )


def successor_snapshot(
    record: CapitalSnapshot,
    *,
    limit_rows: tuple[CapitalLimitUsage, ...],
    sleeve_rows: tuple[SleeveUsage, ...],
    free_after: str,
    seconds: int = 120,
) -> CapitalSnapshot:
    """Build the successor a lawful commit *would* leave behind: usage advanced, cash moved.

    This is a simulation aid for the derivation laws, not a committed successor: no
    `commit_reservation` call can currently produce one, because the owner-ratified policy
    registry is empty and every commit fails closed at position twelve. The cash movement
    is a partition move, so the declared cash total is preserved, and the portfolio version
    moves forward by exactly one declared step.
    """

    moved = record.cash.amount(CapitalPartition.FREE_CASH) - Decimal(free_after)
    assert moved >= 0
    partitions = {
        CapitalPartition.FREE_CASH: Decimal(free_after),
        CapitalPartition.ORDER_RESERVES: (
            record.cash.amount(CapitalPartition.ORDER_RESERVES) + moved
        ),
        CapitalPartition.SAFETY_RESERVE: record.cash.amount(
            CapitalPartition.SAFETY_RESERVE
        ),
        CapitalPartition.OTHER_BLOCKED_CASH: record.cash.amount(
            CapitalPartition.OTHER_BLOCKED_CASH
        ),
    }
    return CapitalSnapshot(
        portfolio_version=capital_module.next_portfolio_version(
            record.portfolio_version, trigger=CapitalRecomputeTrigger.RESERVATION
        ),
        account=record.account,
        environment=record.environment,
        engine=record.engine,
        currency=record.currency,
        accounting_state=record.accounting_state,
        cash=CashPartitionLedger(
            partitions=partitions, cash_total=record.cash.cash_total
        ),
        positions=record.positions,
        marks=record.marks,
        sleeve_usage=sleeve_rows,
        limit_usage=limit_rows,
        fee_state=record.fee_state,
        source_watermark=record.source_watermark,
        reconciliation=record.reconciliation,
        freshness=CapitalFreshness(
            as_of=at(seconds),
            validity_deadline=record.freshness.validity_deadline,
            evidence_complete=True,
        ),
        derived_from=record.snapshot_id,
        recompute_trigger=CapitalRecomputeTrigger.RESERVATION,
        applied_reservation_references=record.applied_reservation_references,
    )


# ---------------------------------------------------------------------------
# Checkpoint 24 reconciliation of the legacy R5-C criteria
# ---------------------------------------------------------------------------

#: The marker every reconciled legacy test carries when the shadow-only posture
#: supersedes its historical expectation. It is plain prose, not an acceptance-test
#: citation, so the frozen R5-C contract's declared traceability stays exactly what it
#: always was.
PENDING_PROOF_MARKER = "PENDING INTEGRATION PROOF"

LEGACY_MODULE = APP_ROOT / "tests" / "test_opip_r5_c_capital_intelligence.py"
R5C_INCREMENT = "ATDD-R5-C-capital-intelligence"
R5C_CONTRACT = (
    APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{R5C_INCREMENT}.md"
)

#: The thirteen legacy acceptance tests this increment reconciled. Every name here
#: still exists in the legacy module and still cites its original criterion.
RECONCILED_LEGACY_TESTS: tuple[str, ...] = (
    "test_ac_005_one_trust_table_gates_the_deployable_and_commit_seams",
    "test_ac_006_a_plan_commits_atomically_against_the_version_it_was_built_for",
    "test_ac_006_a_rejected_attempt_changes_nothing_at_all",
    "test_ac_006_a_live_book_or_an_undescribable_commit_is_refused",
    "test_ac_006_a_plan_outside_its_declared_window_is_refused",
    "test_ac_006_a_reservation_never_extends_the_snapshot_validity",
    "test_ac_007_a_replay_of_a_committed_plan_consumes_nothing_twice",
    "test_ac_007_a_plan_consumed_at_another_version_is_not_replayed",
    "test_ac_007_a_losing_racer_never_double_spends",
    "test_ac_007_a_lawful_recompute_moves_the_tracked_current_version",
    "test_ac_007_the_plan_current_check_reuses_the_frozen_owner",
    "test_ac_011_every_record_declares_itself_non_authoritative",
    "test_ac_011_the_increment_claims_no_durable_or_restart_safe_allocation",
)


#: What every pending integration proof waits on before it can be proved. It is named
#: once, here, and every registry entry below stores it with its own statement, so no
#: pending proof can be recorded without saying what has to exist first.
PENDING_PROOF_DEPENDENCY = (
    "an owner-ratified funding policy (OQ-06), trusted upstream consumption evidence, "
    "and R5-E durability for anything claimed durable"
)


@dataclasses.dataclass(frozen=True)
class LegacyReconciliation:
    """One reconciled legacy criterion: what changed, and what remains unproven.

    ``criterion`` is the historical R5-C criterion the test cites, ``reconciliation``
    records what this increment changed and why, and ``pending_proof`` names the
    positive obligation the shadow-only posture makes unreachable today - or is ``None``
    when the reconciliation is explicit argument supply only and no expectation was
    superseded. A recorded pending proof always carries its declared dependency, so the
    obligation and the precondition it waits on are read together.
    """

    criterion: str
    reconciliation: str
    pending_proof: str | None = None

    def __post_init__(self) -> None:
        if self.pending_proof is not None:
            object.__setattr__(
                self,
                "pending_proof",
                f"{self.pending_proof} (waits on {PENDING_PROOF_DEPENDENCY})",
            )


#: What each reconciled legacy test changed, and which positive obligation the
#: shadow-only posture leaves unproven. Nothing here is implemented by this increment:
#: every pending proof depends on an owner-ratified funding policy (`OQ-06`), trusted
#: upstream consumption evidence, or `R5-E` durability.
LEGACY_RECONCILIATIONS: dict[str, LegacyReconciliation] = {
    "test_ac_005_one_trust_table_gates_the_deployable_and_commit_seams": (
        LegacyReconciliation(
            criterion="AC-005",
            reconciliation="the closing 'trusted evidence still commits' step became a "
            "DEPLOYABLE_BOUND_UNAVAILABLE refusal with non-mutation",
            pending_proof="trusted account evidence commits once an owner-ratified "
            "funding policy and trusted upstream consumption evidence exist",
        )
    ),
    "test_ac_006_a_plan_commits_atomically_against_the_version_it_was_built_for": (
        LegacyReconciliation(
            criterion="AC-006",
            reconciliation="the committed partition move became a refusal; the same "
            "declaration is proved on a lawful RESERVATION recompute",
            pending_proof="a reservation moves free cash into order reserves, records "
            "the plan reference and advances the portfolio version by one step",
        )
    ),
    "test_ac_006_a_rejected_attempt_changes_nothing_at_all": (
        LegacyReconciliation(
            criterion="AC-006",
            reconciliation="the closing 'a later valid plan still commits' step became a "
            "refusal; inertness is proved by the book re-deriving to its own identity",
            pending_proof="a valid plan on an untouched version commits after earlier "
            "rejections",
        )
    ),
    "test_ac_006_a_live_book_or_an_undescribable_commit_is_refused": (
        LegacyReconciliation(
            criterion="AC-006",
            reconciliation="the required deployable policy is supplied explicitly; every "
            "refusal and structural-misuse assertion is unchanged",
        )
    ),
    "test_ac_006_a_plan_outside_its_declared_window_is_refused": (
        LegacyReconciliation(
            criterion="AC-006",
            reconciliation="the boundary-window and whole-plan commits became refusals "
            "that are not window refusals; the expired retry is refused as expired",
            pending_proof="a plan inside its window commits and a retry of that "
            "committed plan replays",
        )
    ),
    "test_ac_006_a_reservation_never_extends_the_snapshot_validity": (
        LegacyReconciliation(
            criterion="AC-006",
            reconciliation="the deadline-carrying successor is built as a lawful "
            "RESERVATION recompute; deadline and later-read refusals are unchanged",
            pending_proof="a committed reservation cannot extend the snapshot validity "
            "deadline",
        )
    ),
    "test_ac_007_a_replay_of_a_committed_plan_consumes_nothing_twice": (
        LegacyReconciliation(
            criterion="AC-007",
            reconciliation="refusal determinism replaces the recorded replay, and the "
            "tracked-version boundary is proved with a lawful recompute",
            pending_proof="a replay of a committed plan returns the recorded successor "
            "and consumes nothing twice",
        )
    ),
    "test_ac_007_a_plan_consumed_at_another_version_is_not_replayed": (
        LegacyReconciliation(
            criterion="AC-007",
            reconciliation="PLAN_ALREADY_CONSUMED is unreachable, so the same scenario "
            "proves the stale version boundary instead",
            pending_proof="a plan consumed at one version is refused as already consumed "
            "rather than replayed onto another version",
        )
    ),
    "test_ac_007_a_losing_racer_never_double_spends": (
        LegacyReconciliation(
            criterion="AC-007",
            reconciliation="neither racer consumes anything; the contested partition "
            "moving zero times is proved under real concurrency",
            pending_proof="exactly one of two concurrent racers consumes the contested "
            "capacity and the loser can replan against the committed version",
        )
    ),
    "test_ac_007_a_lawful_recompute_moves_the_tracked_current_version": (
        LegacyReconciliation(
            criterion="AC-007",
            reconciliation="the lawful recomputes replace the committed ones, and the "
            "tracked-version law is proved by which version each attempt is judged against",
            pending_proof="consecutive reservations consume the version that is actually "
            "current for the book",
        )
    ),
    "test_ac_007_the_plan_current_check_reuses_the_frozen_owner": (
        LegacyReconciliation(
            criterion="AC-007",
            reconciliation="the required deployable policy is supplied explicitly; the "
            "delegation and staleness assertions are unchanged",
        )
    ),
    "test_ac_011_every_record_declares_itself_non_authoritative": (
        LegacyReconciliation(
            criterion="AC-011",
            reconciliation="the inspected record is now a refused commit attempt; every "
            "non-authority assertion is unchanged",
        )
    ),
    "test_ac_011_the_increment_claims_no_durable_or_restart_safe_allocation": (
        LegacyReconciliation(
            criterion="AC-011",
            reconciliation="the inspected commit record is a refusal rather than a "
            "committed successor; the durability declaration is unchanged",
            pending_proof="a committed reservation record stays non-authoritative and "
            "non-durable",
        )
    ),
}


# ---------------------------------------------------------------------------
# AC-013 The deployable bound is established and enforced at the commit boundary
# ---------------------------------------------------------------------------


def test_ac_013_the_commit_requires_an_explicit_bound_policy() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: the bound is never optional.

    GIVEN a current snapshot and a reservation plan bound to its version
    WHEN a commit omits the policy, passes an explicit `None`, or passes something
    that is not a policy at all
    THEN the omission is a Python signature error, an explicit absence fails closed
    with `DEPLOYABLE_BOUND_UNAVAILABLE` and no mutation, and a forged policy object
    is refused as structural misuse instead of being coerced
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-EXPLICIT")
    plan_record = plan(version=record.portfolio_version)

    with pytest.raises(TypeError):
        commit_reservation(record, plan=plan_record, at_time=at(60))  # type: ignore[call-arg]

    absent = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=None,
    )
    assert absent.status is CapitalCommitStatus.REJECTED
    assert absent.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert absent.snapshot is None
    assert absent.committed_capital is None

    forged = {
        "policy_version": CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        "limit_caps": {CapitalLimitKind.RISK: Decimal("1000.00")},
    }
    with pytest.raises(CapitalIntelligenceError, match="policy"):
        commit_reservation(
            record,
            plan=plan_record,
            at_time=at(60),
            deployable_policy=forged,  # type: ignore[arg-type]
        )
    assert record.portfolio_version == VERSION
    assert record.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")


def test_ac_013_an_unratified_policy_cannot_establish_a_numeric_bound() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: arithmetic is not authority.

    GIVEN an identity-bound engineering-scenario policy whose caps produce a real
    deployable number, and a plan complete with declared consumption
    WHEN the commit is attempted
    THEN the isolated arithmetic is available, but the commit still fails closed
    with `DEPLOYABLE_BOUND_UNAVAILABLE`, because no owner-ratified funding policy
    exists - the engineering token is not a ratification and is never admitted
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-UNRATIFIED")
    policy_record = book_policy(record)
    assert policy_record.policy_version == (
        CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION
    )

    view = evaluate_deployable_capital(record, policy=policy_record, at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.deployable_capital == Decimal("1000.00")
    assert view.binding_bound is CapitalBound.RISK

    plan_record = plan(version=record.portfolio_version, amount=100.0)
    result = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=full_deltas(plan_record, risk="100.00"),
    )
    assert result.status is CapitalCommitStatus.REJECTED
    assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert result.snapshot is None
    assert result.successor_version is None
    assert result.committed_capital is None

    assert OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS == ()
    assert capital_policy_grants_funding_authority(policy_record.policy_version) is False
    assert (
        capital_policy_grants_funding_authority(
            CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION
        )
        is False
    )


def test_ac_013_the_numeric_ladder_preserves_positions_ten_and_eleven() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: the frozen order is preserved.

    GIVEN the pure affordability decision the commit seam uses
    WHEN a non-positive amount, an underfunded amount, an unestablished bound and
    an over-bound amount are evaluated together
    THEN `NON_POSITIVE_AMOUNT` precedes every later reason, `INSUFFICIENT_FREE_CASH`
    precedes both new reasons even when a lower bound also exists, a missing bound
    is `DEPLOYABLE_BOUND_UNAVAILABLE` and never a numeric breach, an on-grid bound
    string is accepted as declared evidence, and a malformed amount or a non-money
    bound is structural misuse rather than a rejection
    """

    ladder = reservation_consumption_rejection
    rejection = CapitalCommitRejection

    assert (
        ladder(
            committed=Decimal("0.00"),
            deployable_bound=None,
            free_cash=Decimal("10000.00"),
        )
        is rejection.NON_POSITIVE_AMOUNT
    )
    assert (
        ladder(
            committed=Decimal("0.01"),
            deployable_bound=None,
            free_cash=Decimal("0.00"),
        )
        is rejection.INSUFFICIENT_FREE_CASH
    )
    # An underfunded amount stays an underfunded amount, whether the bound could be
    # established or not: the new reasons sit strictly below position eleven.
    assert (
        ladder(
            committed=Decimal("20000.00"),
            deployable_bound=None,
            free_cash=Decimal("500.00"),
        )
        is rejection.INSUFFICIENT_FREE_CASH
    )
    assert (
        ladder(
            committed=Decimal("20000.00"),
            deployable_bound=Decimal("100.00"),
            free_cash=Decimal("500.00"),
        )
        is rejection.INSUFFICIENT_FREE_CASH
    )
    assert (
        ladder(
            committed=Decimal("100.00"),
            deployable_bound=None,
            free_cash=Decimal("10000.00"),
        )
        is rejection.DEPLOYABLE_BOUND_UNAVAILABLE
    )
    with pytest.raises(CapitalIntelligenceError, match="reservation amount"):
        ladder(
            committed=100.0,  # type: ignore[arg-type]
            deployable_bound=None,
            free_cash=Decimal("10000.00"),
        )
    assert (
        ladder(
            committed=Decimal("100.00"),
            deployable_bound="100.00",
            free_cash=Decimal("10000.00"),
        )
        is None
    )
    with pytest.raises(CapitalIntelligenceError, match="deployable bound"):
        ladder(
            committed=Decimal("100.00"),
            deployable_bound=100.0,  # type: ignore[arg-type]
            free_cash=Decimal("10000.00"),
        )


def test_ac_013_a_valid_bound_admits_equality_and_refuses_one_cent_over() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: the bound is exact to the cent.

    GIVEN an established numeric bound
    WHEN an amount exactly equal to it, one cent above it, one cent below it, and
    an already exhausted bound are evaluated
    THEN equality is admissible, one cent over is `EXCEEDS_DEPLOYABLE_CAPITAL`, and
    a fully consumed bound refuses any positive amount
    """

    ladder = reservation_consumption_rejection
    bound = Decimal("100.00")
    free_cash = Decimal("10000.00")

    assert ladder(committed=bound, deployable_bound=bound, free_cash=free_cash) is None
    assert (
        ladder(
            committed=Decimal("99.99"), deployable_bound=bound, free_cash=free_cash
        )
        is None
    )
    assert (
        ladder(
            committed=Decimal("100.01"), deployable_bound=bound, free_cash=free_cash
        )
        is CapitalCommitRejection.EXCEEDS_DEPLOYABLE_CAPITAL
    )
    assert (
        ladder(
            committed=Decimal("0.01"),
            deployable_bound=Decimal("0.00"),
            free_cash=free_cash,
        )
        is CapitalCommitRejection.EXCEEDS_DEPLOYABLE_CAPITAL
    )


def test_ac_013_an_identity_mismatched_policy_establishes_no_bound() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: an unbound policy proves nothing.

    GIVEN a policy bound to another book and the shipped policy that declares no
    identity and no cap at all
    WHEN deployable capital is evaluated and a commit is attempted
    THEN neither establishes a number, and the commit reports a missing bound
    instead of a numeric limit breach
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-IDENTITY")
    plan_record = plan(version=record.portfolio_version)
    deltas = full_deltas(plan_record)

    other_book = book_policy(record, account="PAPER-LEDGER-SOMEWHERE-ELSE")
    other_view = evaluate_deployable_capital(record, policy=other_book, at_time=at(60))
    assert other_view.status is CapitalDeployableStatus.ABSTAINED
    assert (
        other_view.abstention_reason
        is CapitalDeployableAbstentionReason.IDENTITY_MISMATCH
    )
    assert other_view.deployable_capital is None
    other_commit = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=other_book,
        usage_deltas=deltas,
    )
    assert other_commit.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE

    shipped = capital_deployable_engineering_scenario_policy()
    shipped_view = evaluate_deployable_capital(record, policy=shipped, at_time=at(60))
    assert (
        shipped_view.abstention_reason
        is CapitalDeployableAbstentionReason.IDENTITY_NOT_BOUND
    )
    shipped_commit = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=shipped,
        usage_deltas=deltas,
    )
    assert shipped_commit.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE


def test_ac_013_a_refusal_leaves_the_book_and_witness_unchanged() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: a refusal is inert, and repeatable.

    GIVEN a paper book and a plan whose bound cannot be established
    WHEN the commit is refused and then offered again
    THEN no successor, no committed amount and no version move are produced, the
    book reconstructs to the same identity, the refusal identity is deterministic,
    and the plan is still unconsumed rather than reported as already consumed
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-INERT")
    plan_record = plan(version=record.portfolio_version)
    policy_record = book_policy(record)

    first = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=full_deltas(plan_record),
    )
    assert first.status is CapitalCommitStatus.REJECTED
    assert first.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert first.snapshot is None
    assert first.snapshot_id is None
    assert first.successor_version is None
    assert first.committed_capital is None
    assert first.applied_portfolio_version == record.portfolio_version
    assert first.authoritative is False
    assert first.applied_reservation_references == record.applied_reservation_references

    assert record.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")
    assert CapitalSnapshot.from_dict(record.to_dict()).snapshot_id == record.snapshot_id

    again = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=full_deltas(plan_record),
    )
    assert again.status is CapitalCommitStatus.REJECTED
    assert again.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert again.commit_id == first.commit_id


def test_ac_013_malformed_usage_evidence_never_preempts_an_earlier_refusal() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: the frozen order is not bypassed.

    GIVEN eight compound-invalid submissions, each pairing an input the seam refuses on
    its own declared position with usage evidence that is structurally malformed
    WHEN each is committed
    THEN the earlier declared refusal is published, with no exception, no substitution and
    no mutation, because malformed usage evidence may never preempt a refusal the seam
    would have reached first
    """

    rejection = CapitalCommitRejection

    def malformed(plan_record: PortfolioReservationPlan):
        """A structurally malformed delta: a usage row the book never declares."""

        return (
            delta(
                plan_id=plan_record.plan_id,
                limit_kind=CapitalLimitKind.CONCENTRATION,
                amount="1.00",
            ),
        )

    books: list[CapitalSnapshot] = []

    def attempt(book: CapitalSnapshot, plan_record: PortfolioReservationPlan, **overrides):
        books.append(book)
        return commit_reservation(
            book,
            plan=plan_record,
            at_time=at(60),
            deployable_policy=book_policy(book),
            usage_deltas=malformed(plan_record),
            **overrides,
        )

    # 1. the declared reason is not a reservation
    trigger_book = sparse_book("PAPER-LEDGER-CLOSURE-PRECEDENCE-TRIGGER")
    trigger_plan = plan(version=trigger_book.portfolio_version)
    assert (
        attempt(trigger_book, trigger_plan, trigger=CapitalRecomputeTrigger.FEE).rejection
        is rejection.UNSUPPORTED_RECOMPUTE_TRIGGER
    )

    # 2. no book but the paper ledger may be funded
    live_book = sparse_book(
        "PAPER-LEDGER-CLOSURE-PRECEDENCE-LIVE",
        environment=CapitalEnvironment.LIVE_OBSERVATION,
    )
    live_plan = plan(version=live_book.portfolio_version)
    assert attempt(live_book, live_plan).rejection is rejection.ENVIRONMENT_NOT_FUNDABLE

    # 3. the declared expected version is not this record's
    stale_book = sparse_book("PAPER-LEDGER-CLOSURE-PRECEDENCE-STALE")
    stale_plan = plan(version=stale_book.portfolio_version)
    assert (
        attempt(
            stale_book, stale_plan, expected_portfolio_version="pv-r5c-closure-9999"
        ).rejection
        is rejection.STALE_EXPECTED_VERSION
    )

    # 4. the observation is not strictly inside the snapshot's validity window
    outlived_book = sparse_book(
        "PAPER-LEDGER-CLOSURE-PRECEDENCE-OUTLIVED", validity=at(30)
    )
    outlived_plan = plan(version=outlived_book.portfolio_version)
    assert (
        attempt(outlived_book, outlived_plan).rejection
        is rejection.SNAPSHOT_OUTLIVED_VALIDITY
    )

    # 5. the plan's own declared window has closed
    expired_book = sparse_book("PAPER-LEDGER-CLOSURE-PRECEDENCE-EXPIRED")
    expired_plan = plan(version=expired_book.portfolio_version, expires_at=at(30))
    assert attempt(expired_book, expired_plan).rejection is rejection.PLAN_EXPIRED

    # 6. the account evidence is one the deployable seam would refuse to size
    untrusted_book = sparse_book(
        "PAPER-LEDGER-CLOSURE-PRECEDENCE-UNTRUSTED", stale=True
    )
    untrusted_plan = plan(version=untrusted_book.portfolio_version)
    assert (
        attempt(untrusted_book, untrusted_plan).rejection
        is rejection.ACCOUNT_STATE_UNTRUSTED
    )

    # 7. the plan declares no committable capital (position ten)
    empty_book = sparse_book("PAPER-LEDGER-CLOSURE-PRECEDENCE-EMPTY")
    empty_plan = plan(version=empty_book.portfolio_version, amount=0.0)
    assert attempt(empty_book, empty_plan).rejection is rejection.NON_POSITIVE_AMOUNT

    # 8. free cash does not cover the reservation (position eleven)
    over_book = sparse_book("PAPER-LEDGER-CLOSURE-PRECEDENCE-OVER")
    over_plan = plan(version=over_book.portfolio_version, amount=20_000.0)
    assert attempt(over_book, over_plan).rejection is rejection.INSUFFICIENT_FREE_CASH

    # An earlier refusal stays an inert refusal: no bound verdict is published as a
    # numeric breach, and nothing was written to any of the eight books.
    assert len(books) == 8
    for book in books:
        assert book.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
        assert book.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")
        assert book.cash.cash_total == Decimal("10500.00")
        assert book.applied_reservation_references == ()
        assert CapitalSnapshot.from_dict(book.to_dict()).snapshot_id == book.snapshot_id


def test_ac_013_malformed_or_incomplete_usage_evidence_is_refused_at_its_own_position() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-013: misuse raises, incompleteness abstains.

    GIVEN submissions whose every earlier gate is valid, first with malformed usage
    evidence (an undeclared row, a forged plan identity, a duplicate row) and then a
    well-formed but incomplete submission
    WHEN each is committed
    THEN the malformed shapes are refused structurally without mutating cash, snapshot
    identity, tracked version or witness, the incomplete shape is refused at position
    twelve as an unestablished bound, and neither is ever published as a numeric breach
    """

    rejection = CapitalCommitRejection
    book = sparse_book("PAPER-LEDGER-CLOSURE-OWN-POSITION")
    policy_record = book_policy(book)

    def attempt(plan_record: PortfolioReservationPlan, deltas):
        return commit_reservation(
            book,
            plan=plan_record,
            at_time=at(60),
            deployable_policy=policy_record,
            usage_deltas=deltas,
        )

    def declared_delta(plan_record: PortfolioReservationPlan) -> ReservationUsageDelta:
        return delta(
            plan_id=plan_record.plan_id, limit_kind=CapitalLimitKind.RISK, amount="1.00"
        )

    # The book declares exactly one usage row, so "incomplete" is a constructible state.
    assert missing_usage_delta_identities(book, deltas=()) == ("RISK",)

    undeclared_plan = plan(
        version=book.portfolio_version, candidate="PCAND:CLOSURE:UNDECLARED"
    )
    with pytest.raises(CapitalIntelligenceError, match="declared"):
        attempt(
            undeclared_plan,
            (
                declared_delta(undeclared_plan),
                delta(
                    plan_id=undeclared_plan.plan_id,
                    limit_kind=CapitalLimitKind.CONCENTRATION,
                    amount="1.00",
                ),
            ),
        )

    forged_plan = plan(version=book.portfolio_version, candidate="PCAND:CLOSURE:FORGED")
    with pytest.raises(CapitalIntelligenceError, match="plan"):
        attempt(
            forged_plan,
            (
                delta(
                    plan_id=forged_plan.plan_id + "-FORGED",
                    limit_kind=CapitalLimitKind.RISK,
                    amount="1.00",
                ),
            ),
        )

    duplicate_plan = plan(
        version=book.portfolio_version, candidate="PCAND:CLOSURE:DUPLICATE"
    )
    with pytest.raises(CapitalIntelligenceError, match="duplicate"):
        attempt(
            duplicate_plan,
            (declared_delta(duplicate_plan), declared_delta(duplicate_plan)),
        )

    # Five deltas were offered across three submissions and none of them moved anything.
    assert book.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
    assert book.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")
    assert book.cash.cash_total == Decimal("10500.00")
    assert book.applied_reservation_references == ()
    assert CapitalSnapshot.from_dict(book.to_dict()).snapshot_id == book.snapshot_id

    # A well-formed but incomplete submission is missing evidence, not misuse: it is
    # refused at position twelve, and never as a numeric breach.
    incomplete_plan = plan(
        version=book.portfolio_version, candidate="PCAND:CLOSURE:INCOMPLETE"
    )
    incomplete = attempt(incomplete_plan, ())
    assert incomplete.status is CapitalCommitStatus.REJECTED
    assert incomplete.rejection is rejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert incomplete.rejection is not rejection.EXCEEDS_DEPLOYABLE_CAPITAL
    assert incomplete.snapshot is None
    assert incomplete.committed_capital is None
    assert incomplete.successor_version is None

    # Nothing was recorded by any of the four refusals: the same plan is still judged on
    # its merits rather than reported as a replay or as an already-consumed plan.
    retry = attempt(incomplete_plan, (declared_delta(incomplete_plan),))
    assert retry.status is CapitalCommitStatus.REJECTED
    assert retry.rejection is rejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert retry.rejection is not rejection.PLAN_ALREADY_CONSUMED
    assert retry.snapshot is None
    assert (
        missing_usage_delta_identities(
            book, deltas=(declared_delta(incomplete_plan),)
        )
        == ()
    )


# ---------------------------------------------------------------------------
# AC-014 Successor usage is additive, evidenced and non-reducible
# ---------------------------------------------------------------------------


def test_ac_014_the_delta_contract_refuses_forged_and_duplicate_rows() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: provenance is not decorative.

    GIVEN the declared usage-delta contract and a book with declared usage rows
    WHEN a delta has no identity, two identities, a negative amount, a float, an
    over-precise amount or a naive instant, when two deltas name the same row, and
    when a delta names another plan, another base version or another instant
    THEN every one of them is refused structurally, before any successor exists
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-DELTA")
    plan_record = plan(version=record.portfolio_version)
    plan_id = plan_record.plan_id

    def build(**overrides):
        base = {
            "plan_id": plan_id,
            "base_portfolio_version": VERSION,
            "as_of": at(60),
            "used_capital_delta": "1.00",
            "limit_kind": CapitalLimitKind.RISK,
        }
        base.update(overrides)
        return ReservationUsageDelta(**base)  # type: ignore[arg-type]

    with pytest.raises(CapitalIntelligenceError, match="exactly one"):
        build(limit_kind=None)
    with pytest.raises(CapitalIntelligenceError, match="exactly one"):
        build(sleeve="TACTICAL")
    with pytest.raises(CapitalIntelligenceError, match="negative"):
        build(used_capital_delta="-0.01")
    with pytest.raises(CapitalIntelligenceError, match="exact decimal"):
        build(used_capital_delta=1.0)
    with pytest.raises(CapitalIntelligenceError, match="two places"):
        build(used_capital_delta="1.001")
    with pytest.raises(CapitalIntelligenceError, match="precision"):
        build(used_capital_delta=Decimal("1.001"))
    with pytest.raises(CapitalIntelligenceError):
        build(as_of=datetime(2026, 1, 5, 9, 0))
    with pytest.raises(CapitalIntelligenceError):
        build(sleeve=" ")

    with pytest.raises(CapitalIntelligenceError, match="duplicate"):
        derive_successor_usage(
            record,
            plan_id=plan_id,
            base_portfolio_version=record.portfolio_version,
            at_time=at(60),
            deltas=(build(), build(used_capital_delta="2.00")),
        )
    with pytest.raises(CapitalIntelligenceError, match="plan"):
        derive_successor_usage(
            record,
            plan_id=plan_id,
            base_portfolio_version=record.portfolio_version,
            at_time=at(60),
            deltas=(build(plan_id="PCAND:SOMEWHERE-ELSE"),),
        )
    with pytest.raises(CapitalIntelligenceError, match="version"):
        derive_successor_usage(
            record,
            plan_id=plan_id,
            base_portfolio_version="pv-r5c-closure-9999",
            at_time=at(60),
            deltas=(build(),),
        )
    with pytest.raises(CapitalIntelligenceError, match="instant"):
        derive_successor_usage(
            record,
            plan_id=plan_id,
            base_portfolio_version=record.portfolio_version,
            at_time=at(60),
            deltas=(build(as_of=at(120)),),
        )
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")


def test_ac_014_the_successor_usage_equals_declared_usage_plus_validated_deltas() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: usage is additive, never inferred.

    GIVEN a book whose declared usage rows are authoritative and a plan that
    consumes capital
    WHEN the successor usage is derived with declared deltas for every row
    THEN each row equals its declared value plus exactly its delta, a zero delta
    leaves its row untouched, and the committed dollar amount is never used to
    invent usage in any category
    """

    record = snapshot(
        account="PAPER-LEDGER-CLOSURE-ADDITIVE",
        risk="100.00",
        concentration="10.00",
        liquidity="20.00",
        sleeves=(("TACTICAL", "30.00"),),
    )
    plan_record = plan(version=record.portfolio_version, amount=400.0)

    limit_rows, sleeve_rows = derive_successor_usage(
        record,
        plan_id=plan_record.plan_id,
        base_portfolio_version=record.portfolio_version,
        at_time=at(60),
        deltas=full_deltas(
            plan_record,
            risk="400.00",
            concentration="5.00",
            liquidity="0.00",
            sleeve="15.00",
        ),
    )
    assert tuple((row.kind, row.used_capital) for row in limit_rows) == (
        (CapitalLimitKind.RISK, Decimal("500.00")),
        (CapitalLimitKind.CONCENTRATION, Decimal("15.00")),
        (CapitalLimitKind.LIQUIDITY, Decimal("20.00")),
    )
    assert tuple((row.sleeve, row.used_capital) for row in sleeve_rows) == (
        ("TACTICAL", Decimal("45.00")),
    )
    assert all(row.as_of == at(60) for row in (*limit_rows, *sleeve_rows))
    for before, after in zip(record.limit_usage, limit_rows):
        assert after.kind is before.kind
        assert after.used_capital >= before.used_capital
    for before, after in zip(record.sleeve_usage, sleeve_rows):
        assert after.sleeve == before.sleeve
        assert after.used_capital >= before.used_capital

    # The plan's own dollar amount is NOT a usage claim: with declared zero deltas
    # every row is carried unchanged, so committed capital is never read as
    # incremental risk, concentration, liquidity or sleeve consumption.
    untouched_limits, untouched_sleeves = derive_successor_usage(
        record,
        plan_id=plan_record.plan_id,
        base_portfolio_version=record.portfolio_version,
        at_time=at(60),
        deltas=full_deltas(plan_record),
    )
    assert tuple(row.used_capital for row in untouched_limits) == tuple(
        row.used_capital for row in record.limit_usage
    )
    assert tuple(row.used_capital for row in untouched_sleeves) == tuple(
        row.used_capital for row in record.sleeve_usage
    )

    # A reduction is not expressible at all: the only shape is a non-negative add.
    with pytest.raises(CapitalIntelligenceError, match="negative"):
        ReservationUsageDelta(
            plan_id=plan_record.plan_id,
            base_portfolio_version=record.portfolio_version,
            as_of=at(60),
            used_capital_delta="-400.00",
            limit_kind=CapitalLimitKind.RISK,
        )


def test_ac_014_an_undeclared_usage_row_cannot_be_created_by_a_delta() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: a delta may never make a row.

    GIVEN a book that declares exactly one limit row and no sleeve row
    WHEN a delta names a limit family, or a sleeve, that the snapshot never declared
    THEN the derivation refuses it, and the commit seam refuses it before any
    successor or witness entry exists, because a missing row is missing evidence
    and never a zero
    """

    record = snapshot(
        account="PAPER-LEDGER-CLOSURE-UNDECLARED",
        limits=(CapitalLimitKind.RISK,),
        sleeves=(),
    )
    plan_record = plan(version=record.portfolio_version)

    with pytest.raises(CapitalIntelligenceError, match="declared"):
        derive_successor_usage(
            record,
            plan_id=plan_record.plan_id,
            base_portfolio_version=record.portfolio_version,
            at_time=at(60),
            deltas=(
                delta(
                    plan_id=plan_record.plan_id,
                    limit_kind=CapitalLimitKind.RISK,
                    amount="1.00",
                ),
                delta(
                    plan_id=plan_record.plan_id,
                    limit_kind=CapitalLimitKind.CONCENTRATION,
                    amount="1.00",
                ),
            ),
        )
    with pytest.raises(CapitalIntelligenceError, match="declared"):
        derive_successor_usage(
            record,
            plan_id=plan_record.plan_id,
            base_portfolio_version=record.portfolio_version,
            at_time=at(60),
            deltas=(
                delta(
                    plan_id=plan_record.plan_id,
                    limit_kind=CapitalLimitKind.RISK,
                    amount="1.00",
                ),
                delta(
                    plan_id=plan_record.plan_id,
                    sleeve="TACTICAL",
                    amount="1.00",
                ),
            ),
        )
    with pytest.raises(CapitalIntelligenceError, match="declared"):
        commit_reservation(
            record,
            plan=plan_record,
            at_time=at(60),
            deployable_policy=book_policy(record),
            usage_deltas=(
                delta(
                    plan_id=plan_record.plan_id,
                    limit_kind=CapitalLimitKind.CONCENTRATION,
                    amount="1.00",
                ),
            ),
        )
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")


def test_ac_014_a_declared_zero_delta_is_evidence_not_a_missing_row() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: zero is declared, absence is not.

    GIVEN a book with declared usage rows and two candidate submissions - one that
    declares an explicit zero delta for every row, and one that declares nothing
    WHEN the successor usage is derived and the commits are attempted
    THEN the explicit zeros are accepted as declared evidence and leave every row
    untouched, while the empty submission is missing evidence and is refused at
    position twelve as an unestablished bound - never as a numeric breach
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-ZERO")
    plan_record = plan(version=record.portfolio_version)
    policy_record = book_policy(record)
    zeros = full_deltas(plan_record)

    assert missing_usage_delta_identities(record, deltas=zeros) == ()
    assert missing_usage_delta_identities(record, deltas=()) == (
        "RISK",
        "CONCENTRATION",
        "LIQUIDITY",
        "TACTICAL",
    )

    limit_rows, sleeve_rows = derive_successor_usage(
        record,
        plan_id=plan_record.plan_id,
        base_portfolio_version=record.portfolio_version,
        at_time=at(60),
        deltas=zeros,
    )
    assert all(row.used_capital == Decimal("0.00") for row in limit_rows)
    assert all(row.used_capital == Decimal("0.00") for row in sleeve_rows)
    assert tuple(row.used_capital for row in limit_rows) == tuple(
        row.used_capital for row in record.limit_usage
    )

    declared = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=zeros,
    )
    assert declared.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert declared.snapshot is None

    absent = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=(),
    )
    assert absent.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert absent.rejection is not CapitalCommitRejection.EXCEEDS_DEPLOYABLE_CAPITAL


def test_ac_014_a_missing_required_delta_fails_closed() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: incomplete evidence cannot derive.

    GIVEN a submission that evidences only one of the book's declared usage rows
    WHEN the missing evidence is identified, the derivation is attempted, and the
    commit is attempted
    THEN the missing identities are named in the declared deterministic order, the
    derivation refuses to build a successor from incomplete evidence, and the commit
    fails closed with `DEPLOYABLE_BOUND_UNAVAILABLE` and no successor at all
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-INCOMPLETE")
    plan_record = plan(version=record.portfolio_version)
    partial = full_deltas(plan_record)[:1]

    assert missing_usage_delta_identities(record, deltas=partial) == (
        "CONCENTRATION",
        "LIQUIDITY",
        "TACTICAL",
    )
    with pytest.raises(CapitalIntelligenceError, match="declared"):
        derive_successor_usage(
            record,
            plan_id=plan_record.plan_id,
            base_portfolio_version=record.portfolio_version,
            at_time=at(60),
            deltas=partial,
        )

    result = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=book_policy(record),
        usage_deltas=partial,
    )
    assert result.status is CapitalCommitStatus.REJECTED
    assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert result.snapshot is None
    assert result.successor_version is None
    assert record.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")


def test_ac_014_the_derivation_is_deterministic_and_preserves_row_order() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: the derivation is a pure function.

    GIVEN a book whose declared usage rows are deliberately out of enumeration order
    WHEN the successor usage is derived twice, and once more from a different amount
    THEN the two derivations are identical, the declared row order survives, and the
    successor snapshot built from the derived rows has a stable identity that
    changes when, and only when, the evidenced usage changes
    """

    record = snapshot(
        account="PAPER-LEDGER-CLOSURE-ORDER",
        limits=(
            CapitalLimitKind.CONCENTRATION,
            CapitalLimitKind.RISK,
            CapitalLimitKind.LIQUIDITY,
        ),
        sleeves=(("CORE", "0.00"), ("TACTICAL", "0.00")),
    )
    plan_record = plan(version=record.portfolio_version)
    deltas = full_deltas(
        plan_record,
        risk="1.00",
        concentration="2.00",
        liquidity="3.00",
        sleeve="4.00",
        sleeves=("CORE", "TACTICAL"),
    )
    kwargs = {
        "plan_id": plan_record.plan_id,
        "base_portfolio_version": record.portfolio_version,
        "at_time": at(60),
    }
    # Two plans with the same identity are needed for the repeat, because a delta is
    # bound to the plan that declared it; the derivation itself takes no plan object.
    first = derive_successor_usage(record, deltas=deltas, **kwargs)
    second = derive_successor_usage(record, deltas=tuple(deltas), **kwargs)
    assert first == second
    assert tuple(row.kind for row in first[0]) == (
        CapitalLimitKind.CONCENTRATION,
        CapitalLimitKind.RISK,
        CapitalLimitKind.LIQUIDITY,
    )
    assert tuple(row.sleeve for row in first[1]) == ("CORE", "TACTICAL")

    successor = successor_snapshot(
        record, limit_rows=first[0], sleeve_rows=first[1], free_after="9996.00"
    )
    rebuilt = successor_snapshot(
        record, limit_rows=second[0], sleeve_rows=second[1], free_after="9996.00"
    )
    assert successor.snapshot_id == rebuilt.snapshot_id

    drifted_deltas = full_deltas(
        plan_record,
        risk="9.00",
        concentration="2.00",
        liquidity="3.00",
        sleeve="4.00",
        sleeves=("CORE", "TACTICAL"),
    )
    drifted_usage = derive_successor_usage(record, deltas=drifted_deltas, **kwargs)
    drifted = successor_snapshot(
        record,
        limit_rows=drifted_usage[0],
        sleeve_rows=drifted_usage[1],
        free_after="9996.00",
    )
    assert drifted.snapshot_id != successor.snapshot_id


def test_ac_014_the_commit_leaves_no_trace_when_usage_cannot_be_validated() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: an invalid delta mutates nothing.

    GIVEN a book and a plan, and a delta that claims to belong to another plan
    WHEN the commit is attempted with that forged evidence and then with complete,
    valid evidence for the same plan
    THEN the forged attempt raises before anything is written, the book and its
    identity are unchanged, and the same plan is still treated as a plan this
    process has never consumed
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-NO-TRACE")
    plan_record = plan(version=record.portfolio_version)
    policy_record = book_policy(record)

    forged = (
        delta(
            plan_id=plan_record.plan_id + "-FORGED",
            limit_kind=CapitalLimitKind.RISK,
            amount="1.00",
        ),
    )
    with pytest.raises(CapitalIntelligenceError, match="plan"):
        commit_reservation(
            record,
            plan=plan_record,
            at_time=at(60),
            deployable_policy=policy_record,
            usage_deltas=forged,
        )

    assert record.cash.amount(CapitalPartition.FREE_CASH) == Decimal("10000.00")
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")
    assert CapitalSnapshot.from_dict(record.to_dict()).snapshot_id == record.snapshot_id

    after = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=full_deltas(plan_record),
    )
    assert after.status is CapitalCommitStatus.REJECTED
    assert after.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert after.rejection is not CapitalCommitRejection.PLAN_ALREADY_CONSUMED
    assert after.snapshot is None
    assert after.committed_capital is None


def test_ac_014_sequential_reservations_deplete_declared_capacity() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-014: capacity is consumed, not reused.

    GIVEN a book whose declared usage is authoritative under a bound policy
    WHEN two successive generations of usage are derived from it - a pure simulation in
    which each generation is derived from the previous generation's declared usage plus
    its deltas, and in which no `commit_reservation` call succeeds anywhere - and a third
    generation asks for the next cent
    THEN each derived successor carries the consumption forward, the remaining bound falls
    by exactly what was consumed, the exhausting amount is inside the bound, and the next
    cent is refused as `EXCEEDS_DEPLOYABLE_CAPITAL` although free cash covers it; at the
    commit seam that same attempt is refused one position earlier with
    `DEPLOYABLE_BOUND_UNAVAILABLE`, because no owner-ratified policy exists to establish a
    bound in the first place, so this derivation is a proof of the accounting law and is
    never evidence of a successful atomic commitment
    """

    record = snapshot(account="PAPER-LEDGER-CLOSURE-SEQUENCE")
    policy_record = book_policy(record)

    opening = evaluate_deployable_capital(record, policy=policy_record, at_time=at(60))
    assert opening.status is CapitalDeployableStatus.AVAILABLE
    assert opening.deployable_capital == Decimal("1000.00")
    assert opening.binding_bound is CapitalBound.RISK

    def ladder(committed: str, bound: Decimal | None, book: CapitalSnapshot):
        return reservation_consumption_rejection(
            committed=Decimal(committed),
            deployable_bound=bound,
            free_cash=book.cash.amount(CapitalPartition.FREE_CASH),
        )

    step_one = plan(
        version=record.portfolio_version, amount=400.0, candidate="PCAND:CLOSURE:STEP1"
    )
    deltas_one = full_deltas(step_one, risk="400.00")
    assert missing_usage_delta_identities(record, deltas=deltas_one) == ()
    assert ladder("400.00", opening.deployable_capital, record) is None
    limits_one, sleeves_one = derive_successor_usage(
        record,
        plan_id=step_one.plan_id,
        base_portfolio_version=record.portfolio_version,
        at_time=at(60),
        deltas=deltas_one,
    )
    assert dict((row.kind, row.used_capital) for row in limits_one)[
        CapitalLimitKind.RISK
    ] == Decimal("400.00")
    first = successor_snapshot(
        record, limit_rows=limits_one, sleeve_rows=sleeves_one, free_after="9600.00"
    )
    first_view = evaluate_deployable_capital(first, policy=policy_record, at_time=at(120))
    assert first_view.deployable_capital == Decimal("600.00")
    assert first_view.binding_bound is CapitalBound.RISK

    step_two = plan(
        version=first.portfolio_version, amount=600.0, candidate="PCAND:CLOSURE:STEP2"
    )
    deltas_two = full_deltas(
        step_two, risk="600.00", base=first.portfolio_version, at_time=at(120)
    )
    assert missing_usage_delta_identities(first, deltas=deltas_two) == ()
    # The exhausting amount is exactly the remaining bound: equality is admissible.
    assert ladder("600.00", first_view.deployable_capital, first) is None
    limits_two, sleeves_two = derive_successor_usage(
        first,
        plan_id=step_two.plan_id,
        base_portfolio_version=first.portfolio_version,
        at_time=at(120),
        deltas=deltas_two,
    )
    assert dict((row.kind, row.used_capital) for row in limits_two)[
        CapitalLimitKind.RISK
    ] == Decimal("1000.00")
    second = successor_snapshot(
        first,
        limit_rows=limits_two,
        sleeve_rows=sleeves_two,
        free_after="9000.00",
        seconds=180,
    )
    second_view = evaluate_deployable_capital(
        second, policy=policy_record, at_time=at(180)
    )
    assert second_view.status is CapitalDeployableStatus.AVAILABLE
    assert second_view.deployable_capital == Decimal("0.00")
    assert second_view.binding_bound is CapitalBound.RISK
    assert second_view.over_limit_bounds == ()
    assert second.cash.amount(CapitalPartition.FREE_CASH) == Decimal("9000.00")
    assert second.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("1000.00")
    assert second.cash.cash_total == record.cash.cash_total

    step_three = plan(
        version=second.portfolio_version, amount=0.01, candidate="PCAND:CLOSURE:STEP3"
    )
    deltas_three = full_deltas(
        step_three, risk="0.01", base=second.portfolio_version, at_time=at(240)
    )
    assert missing_usage_delta_identities(second, deltas=deltas_three) == ()
    assert (
        ladder("0.01", second_view.deployable_capital, second)
        is CapitalCommitRejection.EXCEEDS_DEPLOYABLE_CAPITAL
    )
    # At the seam the same attempt is refused one position earlier, because no
    # owner-ratified policy exists to establish the bound in the first place. The
    # numeric breach becomes reachable only once such a policy is ratified.
    seam = commit_reservation(
        second,
        plan=step_three,
        at_time=at(240),
        deployable_policy=policy_record,
        usage_deltas=deltas_three,
    )
    assert seam.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert seam.snapshot is None

    # A sleeve-bound book depletes its sleeve capacity by the same law.
    sleeve_book = snapshot(
        account="PAPER-LEDGER-CLOSURE-SLEEVE-SEQUENCE",
        limits=(),
        sleeves=(("TACTICAL", "4500.00"),),
    )
    sleeve_policy = book_policy(
        sleeve_book, limit_caps={}, sleeve_limits={"TACTICAL": "5000.00"}
    )
    sleeve_view = evaluate_deployable_capital(
        sleeve_book, policy=sleeve_policy, at_time=at(60)
    )
    assert sleeve_view.deployable_capital == Decimal("500.00")
    assert sleeve_view.binding_bound is CapitalBound.SLEEVE
    assert sleeve_view.binding_sleeve == "TACTICAL"

    sleeve_step = plan(
        version=sleeve_book.portfolio_version,
        amount=500.0,
        candidate="PCAND:CLOSURE:SLEEVE1",
    )
    sleeve_deltas = full_deltas(sleeve_step, sleeve="500.00", limit_kinds=())
    assert missing_usage_delta_identities(sleeve_book, deltas=sleeve_deltas) == ()
    sleeve_limits, sleeve_rows = derive_successor_usage(
        sleeve_book,
        plan_id=sleeve_step.plan_id,
        base_portfolio_version=sleeve_book.portfolio_version,
        at_time=at(60),
        deltas=sleeve_deltas,
    )
    assert sleeve_limits == ()
    assert sleeve_rows[0].used_capital == Decimal("5000.00")
    exhausted = successor_snapshot(
        sleeve_book,
        limit_rows=sleeve_limits,
        sleeve_rows=sleeve_rows,
        free_after="9500.00",
    )
    exhausted_view = evaluate_deployable_capital(
        exhausted, policy=sleeve_policy, at_time=at(120)
    )
    assert exhausted_view.deployable_capital == Decimal("0.00")
    assert exhausted_view.binding_bound is CapitalBound.SLEEVE


# ---------------------------------------------------------------------------
# AC-015 Missing sleeve usage abstains; the horizon dimension is declared
# ---------------------------------------------------------------------------


def test_ac_015_a_configured_sleeve_without_declared_usage_abstains() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-015: a missing row is never a zero.

    GIVEN a policy that caps two sleeves and a book that declares usage for one
    WHEN deployable capital is evaluated and a commit is attempted
    THEN the evaluation abstains with `SLEEVE_USAGE_NOT_DECLARED` and names the
    undeclared sleeve instead of reading its whole cap as available, the commit
    reports an unestablished bound, and declaring the row makes the same policy
    available again
    """

    record = snapshot(
        account="PAPER-LEDGER-CLOSURE-SLEEVE-MISSING",
        sleeves=(("TACTICAL", "0.00"),),
    )
    policy_record = book_policy(
        record, sleeve_limits={"TACTICAL": "5000.00", "CORE": "200.00"}
    )

    view = evaluate_deployable_capital(record, policy=policy_record, at_time=at(60))
    assert view.status is CapitalDeployableStatus.ABSTAINED
    assert (
        view.abstention_reason
        is CapitalDeployableAbstentionReason.SLEEVE_USAGE_NOT_DECLARED
    )
    assert view.missing_policy_inputs == ("CORE",)
    assert view.deployable_capital is None

    plan_record = plan(version=record.portfolio_version)
    result = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=full_deltas(plan_record),
    )
    assert result.status is CapitalCommitStatus.REJECTED
    assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert result.snapshot is None

    # The very same policy is available once the row it caps is declared: the
    # abstention was caused by the missing evidence, not by the policy itself.
    declared = snapshot(
        account="PAPER-LEDGER-CLOSURE-SLEEVE-DECLARED",
        sleeves=(("TACTICAL", "0.00"), ("CORE", "100.00")),
    )
    declared_policy = book_policy(
        declared, sleeve_limits={"TACTICAL": "5000.00", "CORE": "200.00"}
    )
    declared_view = evaluate_deployable_capital(
        declared, policy=declared_policy, at_time=at(60)
    )
    assert declared_view.status is CapitalDeployableStatus.AVAILABLE
    assert declared_view.deployable_capital == Decimal("100.00")
    assert declared_view.binding_bound is CapitalBound.SLEEVE
    assert declared_view.binding_sleeve == "CORE"


def test_ac_015_no_deployable_number_is_published_when_usage_is_missing() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-015: an abstention publishes nothing.

    GIVEN an abstaining evaluation caused by one undeclared sleeve usage row
    WHEN the view and its durable payload are inspected
    THEN no number, no bound, no binding bound, no binding sleeve and no over-limit
    list are published, the reason is named, and the record stays non-authoritative
    """

    record = snapshot(
        account="PAPER-LEDGER-CLOSURE-SLEEVE-SILENT",
        sleeves=(("TACTICAL", "0.00"),),
    )
    view = evaluate_deployable_capital(
        record,
        policy=book_policy(record, sleeve_limits={"TACTICAL": "5000.00", "CORE": "1.00"}),
        at_time=at(60),
    )
    assert view.deployable_capital is None
    assert view.bounds == {}
    assert view.binding_bound is None
    assert view.binding_sleeve is None
    assert view.over_limit_bounds == ()
    payload = view.to_dict()
    assert payload["deployable_capital"] is None
    assert payload["abstention_reason"] == "SLEEVE_USAGE_NOT_DECLARED"
    assert payload["binding_bound"] is None
    assert payload["binding_sleeve"] is None
    assert payload["over_limit_bounds"] == []
    assert payload["authoritative"] is False


def test_ac_015_the_abstention_vocabulary_stays_closed_and_ordered() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-015: the vocabulary stays closed.

    GIVEN the declared abstention vocabulary and its declared precedence
    WHEN both are compared with the reason enumeration
    THEN the new reason is the last member, the declared set and length are exactly
    the enumeration, the frozen relative order of the earlier reasons is unchanged,
    and the first reason is still `IDENTITY_NOT_BOUND`
    """

    reason = CapitalDeployableAbstentionReason
    order = CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE

    assert reason.SLEEVE_USAGE_NOT_DECLARED.value == "SLEEVE_USAGE_NOT_DECLARED"
    assert order[0] is reason.IDENTITY_NOT_BOUND
    assert order[-1] is reason.SLEEVE_USAGE_NOT_DECLARED
    assert set(order) == set(reason)
    assert len(order) == len(reason)
    assert len(set(order)) == len(order)
    assert order.index(reason.LIMIT_NOT_CONFIGURED) < order.index(
        reason.LIMIT_USAGE_NOT_DECLARED
    )
    assert order.index(reason.LIMIT_USAGE_NOT_DECLARED) < order.index(
        reason.SLEEVE_LIMIT_NOT_CONFIGURED
    )
    assert order.index(reason.SLEEVE_LIMIT_NOT_CONFIGURED) < order.index(
        reason.SLEEVE_USAGE_NOT_DECLARED
    )


def test_ac_015_the_horizon_dimension_is_a_declared_named_sleeve_mapping() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-015: no seventh bound is invented.

    GIVEN the declared bound vocabulary, which names `HORIZON`, and a book whose
    sleeve cap participates in the minimum
    WHEN an available view is inspected and the module source is read
    THEN `HORIZON` is documented as represented only by named policy sleeves, no
    `HORIZON` key is ever constructed, the vocabulary keeps exactly its six declared
    members, and no independent horizon enforcement is claimed
    """

    assert CAPITAL_HORIZON_BOUND_STATUS == "NAMED_SLEEVE_REPRESENTATION_ONLY"
    assert len(CapitalBound) == 6
    assert CAPITAL_BOUND_PRECEDENCE == (
        CapitalBound.FREE_CASH,
        CapitalBound.RISK,
        CapitalBound.CONCENTRATION,
        CapitalBound.LIQUIDITY,
        CapitalBound.HORIZON,
        CapitalBound.SLEEVE,
    )
    assert tuple(CapitalBound) == CAPITAL_BOUND_PRECEDENCE

    record = snapshot(account="PAPER-LEDGER-CLOSURE-HORIZON")
    view = evaluate_deployable_capital(record, policy=book_policy(record), at_time=at(60))
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert CapitalBound.SLEEVE in view.bounds
    assert CapitalBound.HORIZON not in view.bounds
    assert view.binding_bound is not CapitalBound.HORIZON

    source = r5c_source()
    assert CAPITAL_HORIZON_BOUND_STATUS in source
    assert "no independent numeric bound" in source


def test_ac_015_the_limit_and_identity_refusals_are_preserved() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-015: the earlier reasons are untouched.

    GIVEN a configured cap whose usage row is missing, declared usage with no
    configured cap, an unbound policy, a stale account state and an outlived
    snapshot
    WHEN deployable capital is evaluated
    THEN each keeps its own declared reason and named inputs, and the binding-limit
    selection and published bounds are unchanged
    """

    reason = CapitalDeployableAbstentionReason

    limit_missing = snapshot(
        account="PAPER-LEDGER-CLOSURE-LIMIT-MISSING",
        limits=(CapitalLimitKind.RISK, CapitalLimitKind.CONCENTRATION),
    )
    limit_view = evaluate_deployable_capital(
        limit_missing, policy=book_policy(limit_missing), at_time=at(60)
    )
    assert limit_view.abstention_reason is reason.LIMIT_USAGE_NOT_DECLARED
    assert limit_view.missing_policy_inputs == ("LIQUIDITY",)

    cap_missing = snapshot(
        account="PAPER-LEDGER-CLOSURE-CAP-MISSING",
        limits=(
            CapitalLimitKind.RISK,
            CapitalLimitKind.CONCENTRATION,
            CapitalLimitKind.LIQUIDITY,
        ),
        sleeves=(),
    )
    cap_view = evaluate_deployable_capital(
        cap_missing,
        policy=book_policy(
            cap_missing,
            limit_caps={CapitalLimitKind.RISK: "1000.00"},
            sleeve_limits={},
        ),
        at_time=at(60),
    )
    assert cap_view.abstention_reason is reason.LIMIT_NOT_CONFIGURED
    assert cap_view.missing_policy_inputs == ("CONCENTRATION", "LIQUIDITY")

    sleeve_cap_missing = snapshot(
        account="PAPER-LEDGER-CLOSURE-SLEEVE-CAP-MISSING",
        sleeves=(("CORE", "0.00"),),
    )
    sleeve_cap_view = evaluate_deployable_capital(
        sleeve_cap_missing,
        policy=book_policy(sleeve_cap_missing, sleeve_limits={}),
        at_time=at(60),
    )
    assert sleeve_cap_view.abstention_reason is reason.SLEEVE_LIMIT_NOT_CONFIGURED
    assert sleeve_cap_view.missing_policy_inputs == ("CORE",)

    unbound = evaluate_deployable_capital(
        snapshot(account="PAPER-LEDGER-CLOSURE-UNBOUND"),
        policy=capital_deployable_engineering_scenario_policy(),
        at_time=at(60),
    )
    assert unbound.abstention_reason is reason.IDENTITY_NOT_BOUND
    assert unbound.missing_policy_inputs == (
        "account",
        "environment",
        "engine",
        "currency",
    )

    stale = snapshot(account="PAPER-LEDGER-CLOSURE-STALE", stale=True)
    assert (
        evaluate_deployable_capital(
            stale, policy=book_policy(stale), at_time=at(60)
        ).abstention_reason
        is reason.ACCOUNT_STATE_STALE
    )

    outlived = snapshot(account="PAPER-LEDGER-CLOSURE-OUTLIVED")
    assert (
        evaluate_deployable_capital(
            outlived, policy=book_policy(outlived), at_time=at(901)
        ).abstention_reason
        is reason.SNAPSHOT_OUTLIVED_VALIDITY
    )

    binding_book = snapshot(account="PAPER-LEDGER-CLOSURE-BINDING", risk="900.00")
    binding_view = evaluate_deployable_capital(
        binding_book, policy=book_policy(binding_book), at_time=at(60)
    )
    assert binding_view.status is CapitalDeployableStatus.AVAILABLE
    assert binding_view.deployable_capital == Decimal("100.00")
    assert binding_view.binding_bound is CapitalBound.RISK
    assert binding_view.bounds[CapitalBound.FREE_CASH] == Decimal("10000.00")
    assert binding_view.bounds[CapitalBound.SLEEVE] == Decimal("5000.00")
    assert binding_view.over_limit_bounds == ()


# ---------------------------------------------------------------------------
# AC-016 Monetary precision and the closed rejection vocabulary
# ---------------------------------------------------------------------------


def test_ac_016_the_money_crossing_rounds_up_and_down_within_tolerance() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-016: both adjustment directions hold.

    GIVEN the frozen float-to-money crossing used to read a plan's declared amount
    WHEN an exact cent value, a value just above the grid and a value just below the
    grid are read, all inside the declared tolerance
    THEN each is read onto the exact cent grid, `ROUND_HALF_UP` adjusting downward in
    one direction and upward in the other, and the seam accepts the crossing and
    continues to the authority gate instead of raising
    """

    money = capital_module._money_from_float

    assert money(1000.0, field_name="plan.planned_capital") == Decimal("1000.00")
    assert money(0.0, field_name="plan.planned_capital") == Decimal("0.00")

    below = Decimal(str(999.9999996))
    above = Decimal(str(1000.0000004))
    assert below < Decimal("1000.00") < above
    assert money(999.9999996, field_name="plan.planned_capital") == Decimal("1000.00")
    assert money(1000.0000004, field_name="plan.planned_capital") == Decimal("1000.00")

    record = snapshot(account="PAPER-LEDGER-CLOSURE-GRID")
    plan_record = plan(version=record.portfolio_version, amount=999.9999996)
    result = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=book_policy(record),
        usage_deltas=full_deltas(plan_record),
    )
    assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert result.snapshot is None


def test_ac_016_an_amount_beyond_the_declared_tolerance_is_refused() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-016: the crossing never rounds a plan.

    GIVEN the frozen float-to-money crossing and a commit seam
    WHEN a value beyond the declared tolerance, a non-finite value, a boolean, a
    string and an off-grid plan amount are offered
    THEN each is refused structurally rather than silently rounded or coerced, and
    an exact cent amount still reaches the authority gate
    """

    money = capital_module._money_from_float
    record = snapshot(account="PAPER-LEDGER-CLOSURE-OFFGRID")
    policy_record = book_policy(record)

    with pytest.raises(CapitalIntelligenceError, match="quantum"):
        money(1000.004, field_name="plan.planned_capital")
    with pytest.raises(CapitalIntelligenceError, match="quantum"):
        money(999.9959, field_name="plan.planned_capital")
    with pytest.raises(CapitalIntelligenceError, match="finite"):
        money(float("nan"), field_name="plan.planned_capital")
    with pytest.raises(CapitalIntelligenceError, match="plan amount"):
        money(True, field_name="plan.planned_capital")
    with pytest.raises(CapitalIntelligenceError, match="plan amount"):
        money("1000.00", field_name="plan.planned_capital")

    off_grid = plan(version=record.portfolio_version, amount=1000.004)
    with pytest.raises(CapitalIntelligenceError, match="quantum"):
        commit_reservation(
            record,
            plan=off_grid,
            at_time=at(60),
            deployable_policy=policy_record,
            usage_deltas=full_deltas(off_grid),
        )

    exact = plan(version=record.portfolio_version, amount=1000.0, candidate="PCAND:CLOSURE:EXACT")
    accepted = commit_reservation(
        record,
        plan=exact,
        at_time=at(60),
        deployable_policy=policy_record,
        usage_deltas=full_deltas(exact),
    )
    assert accepted.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert record.cash.amount(CapitalPartition.ORDER_RESERVES) == Decimal("0.00")


def test_ac_016_the_rejection_vocabulary_is_closed_and_ordered() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-016: the ladder is closed and ordered.

    GIVEN the declared commit rejection vocabulary and the numbered refusal order
    documented on the seam
    WHEN both are inspected
    THEN the vocabulary is exactly the declared closed set with the two new members
    appended after `INSUFFICIENT_FREE_CASH`, positions ten and eleven are unchanged,
    and the two new reasons are rejections rather than abstention reasons
    """

    rejection = CapitalCommitRejection
    assert len(rejection) == 12
    assert {member.value for member in rejection} == {
        "STALE_EXPECTED_VERSION",
        "PLAN_ALREADY_CONSUMED",
        "PLAN_NOT_YET_VALID",
        "PLAN_EXPIRED",
        "ACCOUNT_STATE_UNTRUSTED",
        "NON_POSITIVE_AMOUNT",
        "ENVIRONMENT_NOT_FUNDABLE",
        "INSUFFICIENT_FREE_CASH",
        "UNSUPPORTED_RECOMPUTE_TRIGGER",
        "SNAPSHOT_OUTLIVED_VALIDITY",
        "DEPLOYABLE_BOUND_UNAVAILABLE",
        "EXCEEDS_DEPLOYABLE_CAPITAL",
    }
    assert tuple(rejection)[-2:] == (
        rejection.DEPLOYABLE_BOUND_UNAVAILABLE,
        rejection.EXCEEDS_DEPLOYABLE_CAPITAL,
    )

    source = r5c_source()
    tenth = "10. ``NON_POSITIVE_AMOUNT``"
    eleventh = "11. ``INSUFFICIENT_FREE_CASH``"
    twelfth = "12. ``DEPLOYABLE_BOUND_UNAVAILABLE``"
    thirteenth = "13. ``EXCEEDS_DEPLOYABLE_CAPITAL``"
    for token in (tenth, eleventh, twelfth, thirteenth):
        assert token in source, token
    assert (
        source.index(tenth)
        < source.index(eleventh)
        < source.index(twelfth)
        < source.index(thirteenth)
    )

    abstention_values = {
        member.value for member in CapitalDeployableAbstentionReason
    }
    assert "DEPLOYABLE_BOUND_UNAVAILABLE" not in abstention_values
    assert "EXCEEDS_DEPLOYABLE_CAPITAL" not in abstention_values


def test_ac_016_the_money_grid_constants_are_the_declared_ones() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-016: one rounding rule, unchanged.

    GIVEN the declared money grid and tolerance and the frozen crossing seam
    WHEN the module source and constants are inspected
    THEN `MONEY_QUANTUM` is `0.01`, `_FLOAT_TOLERANCE` is `0.000001`, the crossing
    still reads `Decimal(str(value))` and applies exactly one `ROUND_HALF_UP`, and
    no second rounding rule is introduced anywhere in the section
    """

    assert MONEY_QUANTUM == Decimal("0.01")
    assert capital_module._FLOAT_TOLERANCE == Decimal("0.000001")
    source = r5c_source()
    assert source.count("_FLOAT_TOLERANCE =") == 1
    assert "def _money_from_float(value: Any, *, field_name: str) -> Decimal:" in source
    crossing = source[source.index("def _money_from_float(") :]
    crossing = crossing[: crossing.index("\ndef ", 1)]
    assert crossing.count("ROUND_HALF_UP") == 1
    assert "Decimal(str(value))" in crossing
    assert "quantize(" in crossing
    # One rounding rule for the whole section: no other rounding mode is ever used,
    # so the float crossing keeps the only rule the contract declares.
    for mode in (
        "ROUND_DOWN",
        "ROUND_UP",
        "ROUND_HALF_EVEN",
        "ROUND_CEILING",
        "ROUND_FLOOR",
    ):
        assert mode not in source, mode


# ---------------------------------------------------------------------------
# AC-017 Shadow-only authority, boundaries and scope
# ---------------------------------------------------------------------------


def test_ac_017_no_engineering_policy_grants_funding_authority() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-017: no fabricated approval exists.

    GIVEN the declared ratification set, the funding-authority predicate, the policy
    record's own field set and the open owner inputs
    WHEN each is inspected
    THEN the ratified set is empty, every engineering or unknown token is refused,
    the policy record gained no approval field, and `OQ-06` is still an owner
    decision rather than an implied ratification
    """

    assert OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS == ()
    for token in (
        CAPITAL_DEPLOYABLE_ENGINEERING_SCENARIO_POLICY_VERSION,
        CAPITAL_HOURS_ENGINEERING_SCENARIO_POLICY_VERSION,
        "capital-approved-v1",
        "",
        None,
    ):
        assert capital_policy_grants_funding_authority(token) is False, token

    fields = {field.name for field in dataclasses.fields(CapitalDeployablePolicy)}
    assert fields == {
        "policy_version",
        "account",
        "environment",
        "engine",
        "currency",
        "limit_caps",
        "sleeve_limits",
    }
    assert not any("approv" in name or "ratif" in name for name in fields)
    assert OPEN_OWNER_INPUTS == ("OQ-06",)
    assert OWNER_INPUT_STATUS == "OWNER_DECISION_REQUIRED"
    assert CAPITAL_COMMIT_AUTHORIZATION_DEPENDENCY == "OQ-06"


def test_ac_017_the_shadow_only_posture_is_declared_in_the_module() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-017: the posture is declared, not inferred.

    GIVEN the amended module, its records and its commit seam
    WHEN the declared status constants, the section source and a refused commit are
    inspected
    THEN the shadow-only posture and its owner dependency are declared constants, the
    engineering token is explicitly not a ratification, the witness stays
    process-scoped with `R5-E` as the declared durable dependency, and every record
    that leaves the seam stays non-authoritative
    """

    assert (
        CAPITAL_COMMIT_AUTHORIZATION_STATUS
        == "FAIL_CLOSED_NO_OWNER_RATIFIED_CAPITAL_POLICY"
    )
    assert CAPITAL_COMMIT_AUTHORIZATION_DEPENDENCY == "OQ-06"
    assert CAPITAL_RESERVATION_DURABILITY_STATUS == "PROCESS_SCOPED_IN_MEMORY_WITNESS"
    assert CAPITAL_RESERVATION_DURABILITY_DEPENDENCY == "R5-E"

    source = r5c_source()
    for token in (
        CAPITAL_COMMIT_AUTHORIZATION_STATUS,
        "OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS",
        "not an owner ratification",
        "engineering-scenario",
        "Restart, multi-worker and durable-ledger safety are explicitly NOT claimed",
        "R5-E",
    ):
        assert token in source, token

    record = snapshot(account="PAPER-LEDGER-CLOSURE-POSTURE")
    plan_record = plan(version=record.portfolio_version)
    result = commit_reservation(
        record,
        plan=plan_record,
        at_time=at(60),
        deployable_policy=book_policy(record),
        usage_deltas=full_deltas(plan_record),
    )
    assert result.rejection is CapitalCommitRejection.DEPLOYABLE_BOUND_UNAVAILABLE
    assert result.authoritative is False
    assert result.to_dict()["authoritative"] is False
    assert result.snapshot is None

    view = evaluate_deployable_capital(record, policy=book_policy(record), at_time=at(60))
    assert view.authoritative is False
    assert view.to_dict()["authoritative"] is False

    usage_delta = delta(
        plan_id=plan_record.plan_id,
        limit_kind=CapitalLimitKind.RISK,
        amount="1.00",
    )
    payload = usage_delta.to_dict()
    assert payload["used_capital_delta"] == "1.00"
    assert payload["limit_kind"] == "RISK"
    assert payload["sleeve"] is None
    assert "authoritative" not in payload
    for key in payload:
        assert "secret" not in key and "api_key" not in key and "credential" not in key


RUNTIME_WIRING_TARGETS: frozenset[str] = frozenset({
    "capital_rotation_intelligence",
    "app.services.capital_rotation_intelligence",
    "capital_admission_integration",
    "app.services.capital_admission_integration",
})


def module_references(path: Path) -> set[str]:
    """Modules referenced by a file, including aliased ``from`` imports.

    ``module_imports`` records only ``node.module`` for ``from`` imports, so an
    aliased ``from app.services import x as y`` form is invisible to it. This
    helper also composes ``module.name`` for every imported alias.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
            for alias in node.names:
                found.add((node.module + "." + alias.name) if node.module else alias.name)
    return found


def runtime_root_wiring_offenders(roots) -> list[str]:
    """Runtime-root modules that reference the seam or the permitted integration.

    Limit: this is a direct-import scanner. It cannot see dynamic or computed
    imports, attribute-level indirection or runtime module mutation.
    """
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            hits = sorted(n for n in module_references(path) if n in RUNTIME_WIRING_TARGETS)
            if hits:
                offenders.append(str(path.name) + ": " + str(hits))
    return offenders

DECLARED_APPLICATION_CONSUMERS: frozenset[str] = frozenset({
    "app/services/capital_admission_integration.py"
})

SEAM_TARGETS: frozenset[str] = frozenset({
    "capital_rotation_intelligence",
    "app.services.capital_rotation_intelligence",
})


def _app_relative(path: Path) -> str:
    try:
        return path.relative_to(APP_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def runtime_consumers(paths) -> list[str]:
    """App-relative modules whose source references the capital-owner seam."""
    found = []
    for path in sorted(paths):
        if _app_relative(path) == _app_relative(MODULE):
            continue
        if "capital_rotation_intelligence" in module_source(path):
            found.append(_app_relative(path))
    return sorted(found)


def undeclared_consumers(paths) -> list[str]:
    """Consumers outside the bounded declared exception."""
    return sorted(n for n in runtime_consumers(paths) if n not in DECLARED_APPLICATION_CONSUMERS)


def runtime_root_offenders(roots) -> list[str]:
    """Runtime-root modules that import the capital owner (prohibited wiring)."""
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            hits = sorted(n for n in module_imports(path) if n in SEAM_TARGETS)
            if hits:
                offenders.append(str(path.name) + ": " + str(hits))
    return offenders

def test_ac_017_the_seam_gains_no_runtime_consumer_or_authority_surface() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-017: nothing new consumes the seam.

    GIVEN the amended module, the runtime roots that own trading, discovery, decision
    and risk paths, and the whole application tree
    WHEN imports, consumers, the executable call sites and the section source are
    inspected
    THEN no runtime root imports the capital owner, no application module outside the
    owner references it or calls the commit seam, the module imports no network,
    database, randomness or exchange facility, and no order, secret or store surface
    exists in the section
    """

    assert runtime_root_offenders(RUNTIME_ROOTS) == []
    assert runtime_root_wiring_offenders(RUNTIME_ROOTS) == []

    app_files = sorted((APP_ROOT / "app").rglob("*.py"))
    assert DECLARED_APPLICATION_CONSUMERS == {
        "app/services/capital_admission_integration.py"
    }, "the supersession is bounded to exactly one declared application consumer"
    assert undeclared_consumers(app_files) == []
    assert set(runtime_consumers(app_files)) <= DECLARED_APPLICATION_CONSUMERS
    callers = [
        path.relative_to(APP_ROOT).as_posix()
        for path in app_files
        if path != MODULE and "commit_reservation" in module_source(path)
    ]
    assert callers == []

    imports = module_imports(MODULE)
    for root in FORBIDDEN_IMPORT_ROOTS:
        assert not any(
            name == root or name.startswith(f"{root}.") for name in imports
        ), root

    source = r5c_source()
    for token in FORBIDDEN_AUTHORITY_TOKENS:
        assert token not in source, token
    for token in ("os.environ", "getenv(", "datetime.now", "utcnow", "time.time", "random."):
        assert token not in source, token


def test_ac_017_the_release_profile_posture_is_unchanged() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-017: profile posture preserved.

    GIVEN the repository release-profile contract and the Compose literals
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


def test_ac_017_the_increment_scope_contract_is_self_consistent() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-017: the contract matches its tests.

    GIVEN this increment, its scope contract, the repository acceptance index and
    the movable active pointer
    WHEN the contract is parsed and scope-checked
    THEN its criteria, its declared acceptance-test traceability and its
    implementation map agree exactly, the allowed changed-file set is exactly the
    files this increment touches, the pointer resolves without being pinned, and
    every increment identifier in the repository stays unique
    """

    contract = parse_scope_contract(CONTRACT.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    assert set(contract.acceptance_criteria) == {
        "AC-013",
        "AC-014",
        "AC-015",
        "AC-016",
        "AC-017",
        "AC-018",
    }
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"

    acceptance = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, acceptance)
    assert result.passed, result.reasons

    allow = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    assert allow == {
        "OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py",
        "OHM-Trade-Agent-v1/tests/test_opip_r5_c_deployable_bound_closure.py",
        "OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py",
        "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-C-deployable-bound-closure.md",
        "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
    }
    for criterion in contract.acceptance_criteria:
        assert contract.traceability.get(criterion), criterion

    atdd_root = APP_ROOT / "docs" / "atdd"
    active = (atdd_root / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()
    assert (atdd_root / "scope-contracts" / f"{active}.md").is_file(), active
    assert global_pointer_pin_violations(module_source(Path(__file__))) == []

    contracts = load_scope_contracts(atdd_root / "scope-contracts")
    increments = [item.increment for item in contracts]
    assert len(increments) == len(set(increments))
    assert INCREMENT in increments


# ---------------------------------------------------------------------------
# AC-018 The legacy R5-C criteria are reconciled, not reinterpreted
# ---------------------------------------------------------------------------


def _flat_names(node: ast.AST) -> list[str]:
    """Every dotted name an assignment or deletion target can denote."""

    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [f"{prefix}.{node.attr}" for prefix in _flat_names(node.value)]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in _flat_names(item)]
    return []


def test_ac_018_the_reconciled_legacy_criteria_are_named_and_traced() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-018: the reconciliation is recorded.

    GIVEN the legacy R5-C module and this increment's reconciliation registry
    WHEN each reconciled test is inspected
    THEN every one still exists under its original name and still cites its original
    R5-C criterion, exactly the tests with a superseded obligation carry the
    `PENDING INTEGRATION PROOF` marker and no other test does, every pending proof names
    the dependency it waits on, and the frozen contract's declared traceability still
    equals the citations actually discovered in the test tree
    """

    tree = ast.parse(module_source(LEGACY_MODULE))
    docstrings = {
        node.name: ast.get_docstring(node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    assert set(RECONCILED_LEGACY_TESTS) == set(LEGACY_RECONCILIATIONS)
    assert len(RECONCILED_LEGACY_TESTS) == 13
    assert len(set(RECONCILED_LEGACY_TESTS)) == 13

    for name in RECONCILED_LEGACY_TESTS:
        assert name in docstrings, name
        entry = LEGACY_RECONCILIATIONS[name]
        doc = docstrings[name]
        assert f"{R5C_INCREMENT}/{entry.criterion}" in doc, name
        assert entry.reconciliation != "", name
        if entry.pending_proof is None:
            assert PENDING_PROOF_MARKER not in doc, name
        else:
            assert PENDING_PROOF_MARKER in doc, name
            assert PENDING_PROOF_DEPENDENCY in entry.pending_proof, name
            assert any(
                token in entry.pending_proof
                for token in ("funding policy", "OQ-06", "R5-E")
            ), name

    marked = {name for name, doc in docstrings.items() if PENDING_PROOF_MARKER in doc}
    assert marked == {
        name
        for name, entry in LEGACY_RECONCILIATIONS.items()
        if entry.pending_proof is not None
    }
    assert len(marked) == 10

    frozen = parse_scope_contract(R5C_CONTRACT.read_text(encoding="utf-8"))
    discovered_index = load_acceptance_index(APP_ROOT / "tests")
    for criterion in frozen.acceptance_criteria:
        declared = set(frozen.traceability.get(criterion, ()))
        discovered = set(
            discovered_index.by_ac.get((R5C_INCREMENT, criterion), frozenset())
        )
        assert declared == discovered, criterion


def test_ac_018_no_skip_xfail_or_authorization_bypass_exists() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-018: no bypass was introduced.

    GIVEN the two test modules and the whole test tree
    WHEN their decorators, fixtures and mutation targets are inspected
    THEN no test or fixture is skipped or marked expected-failure, these modules take no
    monkeypatch fixture, no test assigns or deletes the owner-ratified policy registry
    or the funding-authority predicate, and nothing patches the capital owner, its bound
    arithmetic or its authorization gate
    """

    forbidden_marks = {"skip", "skipif", "xfail"}
    forbidden_attributes = {
        "OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS",
        "capital_policy_grants_funding_authority",
        "commit_reservation",
        "evaluate_deployable_capital",
        "_established_deployable_bound",
        "_money_from_float",
    }
    authority_symbols = {
        "OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS",
        "capital_policy_grants_funding_authority",
    }

    for path in (LEGACY_MODULE, Path(__file__)):
        tree = ast.parse(module_source(path))
        marks = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_marks
        }
        assert marks == set(), (path.name, marks)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"setattr", "delattr"} or len(node.args) < 2:
                continue
            attribute = node.args[1]
            if isinstance(attribute, ast.Constant):
                assert attribute.value not in forbidden_attributes, path.name

    # The reconciled tests are behavioural: none of them may take a patching fixture.
    for name in RECONCILED_LEGACY_TESTS:
        node = next(
            item
            for item in ast.parse(module_source(LEGACY_MODULE)).body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        parameters = {
            argument.arg for argument in node.args.args + node.args.kwonlyargs
        }
        assert "monkeypatch" not in parameters, name
    # This increment's own module uses no patching machinery at all.
    closure_names = {
        node.id
        for node in ast.walk(ast.parse(module_source(Path(__file__))))
        if isinstance(node, ast.Name)
    }
    assert "monkeypatch" not in closure_names

    for path in sorted((APP_ROOT / "tests").rglob("test_*.py")):
        tree = ast.parse(module_source(path))
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            elif isinstance(node, ast.Delete):
                targets = list(node.targets)
            for target in targets:
                for name in _flat_names(target):
                    leaf = name.rsplit(".", 1)[-1]
                    assert leaf not in authority_symbols, (path.name, name)


def test_ac_018_the_supersession_is_versioned_in_this_contract() -> None:
    """ATDD-R5-C-deployable-bound-closure/AC-018: the supersession lives here.

    GIVEN this increment's contract, the frozen R5-C contract and the active pointer
    WHEN both contracts are read and compared
    THEN this contract names the owner-authorised reconciliation, its superseded
    criteria, the pending proofs and their dependencies, the frozen contract still
    carries its original `AC-006`/`AC-007` obligations with an `UNAPPROVED SCOPE
    CHANGES` of `NONE`, and the increment pointer resolves to a contract that exists
    """

    text = CONTRACT.read_text(encoding="utf-8")
    for token in (
        "LEGACY-ACCEPTANCE RECONCILIATION",
        "owner-authorised",
        "PENDING INTEGRATION PROOF",
        "SUPERSESSION RECORD",
        R5C_INCREMENT,
        "owner-ratified funding policy",
        "trusted upstream consumption evidence",
        "R5-E",
        "OQ-06",
        "test is marked skipped or",
    ):
        assert token in text, token

    frozen_text = R5C_CONTRACT.read_text(encoding="utf-8")
    for token in (
        "the commit is all-or-nothing against the version the plan was built for",
        "a replay is an idempotent replay of the recorded successor that consumes nothing twice",
        "exactly one racer consumes the contested capacity",
    ):
        assert token in frozen_text, token
    frozen = parse_scope_contract(frozen_text)
    assert frozen.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert {"AC-006", "AC-007"} <= set(frozen.acceptance_criteria)

    contract = parse_scope_contract(text)
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert set(contract.acceptance_criteria) == {
        "AC-013",
        "AC-014",
        "AC-015",
        "AC-016",
        "AC-017",
        "AC-018",
    }
    assert set(contract.traceability) <= set(contract.acceptance_criteria)
    assert set(contract.implementation_map) <= set(contract.acceptance_criteria)

    atdd_root = APP_ROOT / "docs" / "atdd"
    active = (atdd_root / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()
    assert (atdd_root / "scope-contracts" / f"{active}.md").is_file(), active
    increments = {
        item.increment for item in load_scope_contracts(atdd_root / "scope-contracts")
    }
    assert {INCREMENT, R5C_INCREMENT} <= increments
    assert global_pointer_pin_violations(module_source(Path(__file__))) == []



def test_ac_011_a_declared_consumer_is_admitted_and_is_the_only_one(tmp_path=None) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: the declared consumer is the only application consumer."""
    app_files = sorted((APP_ROOT / "app").rglob("*.py"))
    consumers = runtime_consumers(app_files)
    assert consumers == ["app/services/capital_admission_integration.py"], consumers
    assert undeclared_consumers(app_files) == []


def test_ac_011_an_additional_undeclared_consumer_is_rejected(tmp_path) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: a second consumer is rejected."""
    extra = tmp_path / "extra_seam_consumer.py"
    extra.write_text("from app.services import capital_rotation_intelligence as c" + chr(10), encoding="utf-8")
    offenders = undeclared_consumers([extra])
    assert len(offenders) == 1
    assert offenders[0].endswith("extra_seam_consumer.py")


def test_ac_011_prohibited_runtime_wiring_is_rejected(tmp_path) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: runtime wiring of the seam is rejected."""
    wiring = tmp_path / "wired_job.py"
    wiring.write_text("import app.services.capital_rotation_intelligence" + chr(10), encoding="utf-8")
    detected = runtime_root_offenders([tmp_path])
    assert detected and "wired_job.py" in detected[0]
    assert runtime_root_offenders(RUNTIME_ROOTS) == []


def test_ac_011_the_permitted_module_is_not_prohibited_runtime_wiring(tmp_path=None) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: the exception does not open runtime wiring."""
    offenders = runtime_root_offenders(RUNTIME_ROOTS)
    assert offenders == []
    assert "capital_admission_integration.py" not in "".join(offenders)


def test_ac_011_the_declared_consumer_touches_the_existing_writer_without_new_authority(tmp_path=None) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: the consumer reuses the existing writer and gains no new authority."""
    import importlib
    mod = importlib.import_module("app.services.capital_admission_integration")
    assert callable(getattr(mod, "admit_planned_reservation", None))
    src = module_source(APP_ROOT / "app" / "services" / "capital_admission_integration.py")
    assert "WriterClient" in src
    assert "grants_execution_authority" in src
    forbidden = ("place_order", "submit_order", "cancel_order", "create_order")
    assert [token for token in forbidden if token in src] == []


def test_ac_011_prohibited_runtime_wiring_of_the_permitted_integration_is_rejected(tmp_path) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: a runtime entry point may not wire the permitted integration."""
    wiring = tmp_path / "wired_integration_job.py"
    wiring.write_text("from app.services.capital_admission_integration import admit_planned_reservation" + chr(10), encoding="utf-8")
    detected = runtime_root_wiring_offenders([tmp_path])
    assert detected and "wired_integration_job.py" in detected[0]
    assert runtime_root_wiring_offenders(RUNTIME_ROOTS) == []


def test_ac_011_prohibited_runtime_wiring_is_detected_for_all_import_forms(tmp_path) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: aliased and plain forms are both detected."""
    plain = tmp_path / "plain_seam.py"
    plain.write_text("import app.services.capital_rotation_intelligence" + chr(10), encoding="utf-8")
    direct = tmp_path / "direct_integration.py"
    direct.write_text("import app.services.capital_admission_integration" + chr(10), encoding="utf-8")
    aliased = tmp_path / "aliased_integration.py"
    aliased.write_text("from app.services import capital_admission_integration as seam" + chr(10), encoding="utf-8")
    detected = runtime_root_wiring_offenders([tmp_path])
    joined = " ".join(detected)
    assert "plain_seam.py" in joined
    assert "direct_integration.py" in joined
    assert "aliased_integration.py" in joined, "the aliased from-import form must be detected"


def test_ac_011_indirect_or_dynamic_reachability_is_outside_this_direct_import_scanner(tmp_path) -> None:
    """ATDD-R5-E-capital-admission-integration/AC-011: the detector limit is executable, not implied."""
    dynamic = tmp_path / "dynamic_wiring.py"
    dynamic.write_text("import importlib" + chr(10) + "importlib.import_module('app.services.capital_admission_integration')" + chr(10), encoding="utf-8")
    known = {name for name in module_references(dynamic) if name in RUNTIME_WIRING_TARGETS}
    assert known == set(), "a computed import name is not a direct import reference"