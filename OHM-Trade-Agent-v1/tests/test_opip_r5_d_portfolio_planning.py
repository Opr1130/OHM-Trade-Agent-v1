"""R5-D Portfolio Planning and Rotation acceptance tests.

This module proves the `ATDD-R5-D-portfolio-planning` increment: the
`PortfolioPlan` record and common-window comparison the frozen R5-0 contract
assigns to `SC-08` (`app/opip/contracts/portfolio.py`) and the linked-intent
`RotationGroup` record assigned to `SC-09`
(`app/opip/contracts/paper_execution.py`), with dependency `R5-C`, open owner
inputs `OQ-05`/`OQ-06` and gates `G3`/`G5`:

* one durable, non-authoritative plan revision carrying exactly the frozen
  `SC-08` keys, with content-hash identity and a manifest bound to its own
  evaluation window;
* the declared disposition vocabulary of `PP-02` (`HOLD_CASH`, `WAIT`,
  `HOLD_POSITION`, `NEW_ENTRY`, `REDUCE`, `EXIT`, `ROTATE`; `ADD` disabled) with
  a per-disposition capital sign and a `ROTATE` action that is only declarable
  when the plan's own recorded evidence already admits the switch: the action must
  name the best observed eligible alternative, its comparison must be built from
  the plan's own compared legs, and every gate branch the plan itself settles
  (policy binding, comparison availability, turnover, uncertainty, advantage) must
  already pass;
* deterministic selection over the *observed* alternatives with recorded
  exclusions, so "best" is never a market-wide optimum and an exact tie prefers
  holding over churning (`PP-04`);
* the matched common-window comparison of `HOLD`, `EXIT_TO_CASH` and
  `EXIT_THEN_ENTER` where the exit leg's own fee, spread, latency and attributable
  operating cost are all counted once and none may be understated, the past
  acquisition cost is sunk, the conservative advantage reads the unfavourable side
  of the declared band and only the plan's own recorded comparison is evaluated
  (`RO-01`);
* the closed abstention precedence of the discretionary switch gate, where an
  unratified owner policy abstains with `POLICY_NOT_BOUND` before any numeric
  input and a proven advantage must record the policy that licensed it (`RO-02`);
* the dormant two-leg rotation-group record with its frozen keys, partial-fill
  proceeds split, residual-exposure reconciliation and terminal-state laws
  (`RO-03`), including refusal of replayed or forged payloads; and
* one cross-component proof, using the *existing* `SC-06`/`SC-07` capital owner,
  that a plan's declared capital consumption is bounded by what is actually
  deployable at the commit boundary, and that the composition fails closed while
  `OQ-06` leaves the caps unratified.

The tests are deliberately adversarial about the boundaries the adopted clauses
set: a forged identity, a dropped or reordered key, a profit target, a costed
hold, a duplicated leg, a sunk cost offered as an advantage, a stale or expired
plan, a claim of an entry the record does not contain, a double-charged exit fee
and an over-committed entry are all refused rather than interpreted.

Every record is research-only evidence: `authoritative` is hard-coded `False`,
`ROTATION_GROUP_RECORDING_ENABLED` is `False`, no order, execution, store,
scheduler, Telegram or environment surface is added, and `OQ-05`/`OQ-06` stay
owner decisions whose unratified numbers keep the shipped policy abstaining
instead of guessing.

The tests are deterministic and hermetic: no clock, no network, no database and
no environment read. Every instant and every amount is explicit.
"""

from __future__ import annotations

import ast
from dataclasses import replace
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts.paper_execution import (  # noqa: E402
    ROTATION_GROUP_ID_PREFIX,
    ROTATION_GROUP_PAYLOAD_KEYS,
    ROTATION_GROUP_RECORDING_ENABLED,
    ROTATION_GROUP_RECORD_KEYS,
    ROTATION_GROUP_SCHEMA_VERSION,
    ROTATION_RECONCILIATION_TOLERANCE,
    TERMINAL_STATE_REQUALIFICATION_DECISIONS,
    RotationGroup,
    RotationGroupContractError,
    RotationGroupReason,
    RotationCommittedProceeds,
    RotationLeg,
    RotationLegFill,
    RotationLegReference,
    RotationOldPosition,
    RotationReplacementThesis,
    RotationRequalification,
    RotationRequalificationDecision,
    RotationResidualExposure,
    RotationTerminalState,
    assert_rotation_group_record_keys,
    commit_rotation_proceeds,
    protection_trigger_implies_exit,
    rotation_requalification_is_admitted,
)
from app.opip.contracts.portfolio import (  # noqa: E402
    OPEN_OWNER_INPUTS,
    OWNER_INPUT_STATUS,
    PORTFOLIO_ALTERNATIVE_KINDS,
    PORTFOLIO_ALTERNATIVE_PREFERENCE,
    PORTFOLIO_ALTERNATIVE_TIE_BREAK_RULE,
    PORTFOLIO_COMPARISON_ID_PREFIX,
    PORTFOLIO_PLAN_ABSTENTION_PRECEDENCE,
    PORTFOLIO_PLAN_DISABLED_DISPOSITIONS,
    PORTFOLIO_PLAN_DISPOSITIONS,
    PORTFOLIO_PLAN_ID_PREFIX,
    PORTFOLIO_PLAN_RECORD_KEYS,
    PORTFOLIO_PLAN_REJECTION_REASONS,
    PORTFOLIO_PLAN_SCHEMA_VERSION,
    PORTFOLIO_SWITCH_ASSESSMENT_ID_PREFIX,
    LegacyComparatorObservables,
    PortfolioAlternative,
    PortfolioAlternativeKind,
    PortfolioCandidate,
    PortfolioContractError,
    PortfolioDirection,
    PortfolioEvaluationWindow,
    PortfolioEvidenceStatus,
    PortfolioPanelManifest,
    PortfolioPlan,
    PortfolioPlanAbstentionReason,
    PortfolioPlanAction,
    PortfolioPlanConstraints,
    PortfolioPlanDisposition,
    PortfolioPlanEconomics,
    PortfolioPlanPolicyBinding,
    PortfolioPlanRejection,
    PortfolioReservation,
    PortfolioReservationPlan,
    RotationComparison,
    RotationSwitchAssessment,
    RotationSwitchStatus,
    UncertaintyTreatment,
    assert_plan_record_keys,
    assert_plan_selects_best_observed,
    assert_reservation_plan_current,
    assess_rotation_switch,
    compose_rotation_replacement,
    portfolio_panel_fingerprint,
    rank_plan_alternatives,
    rotation_comparison,
    select_plan_alternative,
    unratified_plan_constraints,
)
from app.services import capital_rotation_intelligence as capital_module  # noqa: E402
from app.services.capital_rotation_intelligence import (  # noqa: E402
    CapitalAccountingState,
    CapitalBound,
    CapitalCommitRejection,
    CapitalCommitStatus,
    CapitalDeployableAbstentionReason,
    CapitalDeployableStatus,
    CapitalEnvironment,
    CapitalFeeState,
    CapitalFreshness,
    CapitalLimitKind,
    CapitalLimitUsage,
    CapitalMarkState,
    CapitalPartition,
    CapitalPosition,
    CapitalReconciliation,
    CapitalReconciliationStatus,
    CapitalSnapshot,
    CapitalSourceWatermark,
    CashPartitionLedger,
    SleeveUsage,
    capital_deployable_engineering_scenario_policy,
    commit_reservation,
    evaluate_deployable_capital,
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
from tests.test_atdd_scope_control import (  # noqa: E402
    global_pointer_pin_violations,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-D-portfolio-planning"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
PORTFOLIO_MODULE = APP_ROOT / "app" / "opip" / "contracts" / "portfolio.py"
EXECUTION_MODULE = APP_ROOT / "app" / "opip" / "contracts" / "paper_execution.py"
R5D_BANNER = "# R5-D portfolio plan and common-window rotation comparison"
SC09_BANNER = "# SC-09"

#: Runtime roots that own trading, discovery, decision and risk paths. None may
#: import or consume the R5-D plan or rotation-group records.
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
SUBJECT = "POPT:R5D:1"
SYMBOL = "BTCUSDT"
WINDOW = PortfolioEvaluationWindow(start=T0, end=T0 + timedelta(days=5))
CAP_ACCOUNT = "PAPER-LEDGER-R5D-01"
CAP_ENGINE = "paper-ledger-engine-v1"
CAP_CURRENCY = "USD"
CAP_VERSION = "pv-r5d-0001"

#: A labelled engineering-scenario constraint set. These numbers are test
#: fixtures, not an accepted policy: `OQ-05` still owns the real values.
RATIFIED_SCENARIO_ADVANTAGE = 5.0
#: The capital the scenario's own rotation declares it turns over. The turnover
#: budget is charged in capital moved, not in the round-trip cost alone, so the
#: scenario budget must exceed the switch the scenario means to admit. A switch that
#: declares no capital of its own is not priced as a round trip: it fails closed as
#: unestablished, and the tests below prove both branches.
RATIFIED_SCENARIO_SWITCH_CAPITAL = 121.0
RATIFIED_SCENARIO_TURNOVER_BUDGET = 250.0

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


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


def r5d_section(path: Path, banner: str) -> str:
    """Return only the R5-D section of an owning module."""

    source = module_source(path)
    assert banner in source, banner
    section = source[source.index(banner) :]
    if "__all__ = [" in section:
        section = section[: section.index("__all__ = [")]
    return section


def alternative(
    kind: PortfolioAlternativeKind,
    *,
    gross: float,
    exit_fee: float = 0.0,
    entry_fee: float = 0.0,
    spread: float = 0.0,
    latency: float = 0.0,
    operating: float | None = 0.0,
    band: tuple[float, float] | None = None,
    subject: str = SUBJECT,
) -> PortfolioAlternative:
    lower, upper = band if band is not None else (None, None)
    return PortfolioAlternative(
        kind=kind,
        subject_ref=subject,
        expected_gross_net=gross,
        exit_fee=exit_fee,
        entry_fee=entry_fee,
        spread_cost=spread,
        latency_cost=latency,
        attributable_operating_cost=operating,
        uncertainty_lower_bound=lower,
        uncertainty_upper_bound=upper,
    )


def hold_leg(**overrides) -> PortfolioAlternative:
    base: dict[str, object] = {"gross": 100.0, "operating": 0.0}
    base.update(overrides)
    return alternative(PortfolioAlternativeKind.HOLD, **base)  # type: ignore[arg-type]


def cash_leg(**overrides) -> PortfolioAlternative:
    base: dict[str, object] = {
        "gross": 100.0,
        "exit_fee": 5.0,
        "spread": 1.0,
        "operating": 0.0,
    }
    base.update(overrides)
    return alternative(PortfolioAlternativeKind.EXIT_TO_CASH, **base)  # type: ignore[arg-type]


def replacement_leg(
    *,
    cash: PortfolioAlternative | None = None,
    entry_gross: float = 30.0,
    entry_fee: float = 2.0,
    entry_spread: float = 0.0,
    entry_latency: float = 0.0,
    entry_operating: float | None = 1.0,
    entry_band: tuple[float, float] | None = None,
    subject: str = SUBJECT,
) -> PortfolioAlternative:
    exit_leg = cash if cash is not None else cash_leg()
    lower, upper = entry_band if entry_band is not None else (None, None)
    return compose_rotation_replacement(
        subject_ref=subject,
        cash=exit_leg,
        entry_gross_net=entry_gross,
        entry_fee=entry_fee,
        entry_spread_cost=entry_spread,
        entry_latency_cost=entry_latency,
        entry_operating_cost=entry_operating,
        entry_uncertainty_lower_bound=lower,
        entry_uncertainty_upper_bound=upper,
    )


def comparison(
    *,
    hold: PortfolioAlternative | None = None,
    cash: PortfolioAlternative | None = None,
    replacement: PortfolioAlternative | None = None,
    window: PortfolioEvaluationWindow = WINDOW,
    subject: str = SUBJECT,
    acquisition: float | None = None,
) -> RotationComparison:
    cash_record = cash if cash is not None else cash_leg()
    return rotation_comparison(
        window=window,
        subject_ref=subject,
        hold=hold if hold is not None else hold_leg(),
        cash=cash_record,
        replacement=replacement
        if replacement is not None
        else replacement_leg(cash=cash_record),
        acquisition_cost_for_accounting=acquisition,
    )


def candidate(
    *,
    symbol: str = SYMBOL,
    direction: PortfolioDirection = PortfolioDirection.LONG,
) -> PortfolioCandidate:
    return PortfolioCandidate(
        episode_id="PEPS:R5D:1",
        feasibility_decision_id="PFD:R5D:1",
        symbol=symbol,
        direction=direction,
        common_shock_group="CSG:R5D:1",
        data_quality_status=PortfolioEvidenceStatus.VALID,
        execution_evidence_status=PortfolioEvidenceStatus.VALID,
        liquidity_capacity_notional=25_000.0,
        requested_capital_fraction=0.1,
        stop_loss_fraction=0.02,
        legacy_observables=LegacyComparatorObservables(
            technical_score=70,
            attainability_score=0.8,
            target_2_move_pct=1.2,
            book_coverage_status="COVERED",
            recent_trade_status="TRADED",
            cross_pair_confirmation_status="CONFIRMED",
        ),
    )


def manifest(
    *,
    window: PortfolioEvaluationWindow = WINDOW,
    candidates: tuple[PortfolioCandidate, ...] | None = None,
    sleeves: dict[str, float] | None = None,
) -> PortfolioPanelManifest:
    panel = candidates if candidates is not None else (candidate(),)
    return PortfolioPanelManifest(
        window_identity=window.window_identity,
        panel_fingerprint=portfolio_panel_fingerprint(window, panel),
        candidate_ids=tuple(sorted(item.candidate_id for item in panel)),
        horizon_sleeves=sleeves
        if sleeves is not None
        else {"TACTICAL": 0.5, "POSITION": 0.5},
    )

def scenario_constraints(**overrides) -> PortfolioPlanConstraints:
    """A labelled engineering-scenario constraint set (`OQ-05` stays open)."""

    base: dict[str, object] = {
        "min_switch_advantage_dollars": RATIFIED_SCENARIO_ADVANTAGE,
        "uncertainty_treatment": UncertaintyTreatment.EXPECTED,
        "turnover_budget_dollars": RATIFIED_SCENARIO_TURNOVER_BUDGET,
        "cooldown_hours": 0.0,
    }
    base.update(overrides)
    return PortfolioPlanConstraints(**base)  # type: ignore[arg-type]


def band_constraints(**overrides) -> PortfolioPlanConstraints:
    """The scenario set read conservatively (the lower bound decides)."""

    base: dict[str, object] = {"uncertainty_treatment": UncertaintyTreatment.LOWER_BOUND}
    base.update(overrides)
    return scenario_constraints(**base)


def hold_action(**overrides) -> PortfolioPlanAction:
    base: dict[str, object] = {
        "sequence": 1,
        "disposition": PortfolioPlanDisposition.HOLD_POSITION,
        "subject_ref": SUBJECT,
        "capital_delta": 0.0,
    }
    base.update(overrides)
    return PortfolioPlanAction(**base)  # type: ignore[arg-type]


def rotate_action(alternative_id: str, *, delta: float = 121.0) -> PortfolioPlanAction:
    return PortfolioPlanAction(
        sequence=1,
        disposition=PortfolioPlanDisposition.ROTATE,
        subject_ref=SUBJECT,
        capital_delta=delta,
        alternative_id=alternative_id,
    )


def economics_for(net: float) -> PortfolioPlanEconomics:
    return PortfolioPlanEconomics(
        expected_trading_net=net,
        expected_operating_cost=0.0,
        uncertainty_lower_bound=net - 50.0,
        uncertainty_upper_bound=net + 50.0,
    )


def rejections_for(
    legs: tuple[PortfolioAlternative, ...],
    *,
    reason_code: str | None = None,
) -> tuple[PortfolioPlanRejection, ...]:
    """Record every passed-over alternative with the reason that fits.

    A passed-over alternative with the same expected net as the selection lost on
    the declared preference; any other lost on its lower expected net.
    """

    selected = select_plan_alternative(legs)
    rejections: list[PortfolioPlanRejection] = []
    for leg in legs:
        if leg.alternative_id == selected.alternative_id:
            continue
        if reason_code is not None:
            code = reason_code
        elif leg.expected_net_dollars == selected.expected_net_dollars:
            code = "PREFERENCE_TIE_BREAK"
        else:
            code = "LOWER_EXPECTED_NET"
        rejections.append(
            PortfolioPlanRejection(alternative_id=leg.alternative_id, reason_code=code)
        )
    return tuple(rejections)


def plan(
    *,
    alternatives: tuple[PortfolioAlternative, ...] | None = None,
    action: PortfolioPlanAction | None = None,
    constraints: PortfolioPlanConstraints | None = None,
    comparisons: tuple[RotationComparison, ...] = (),
    rejected: tuple[PortfolioPlanRejection, ...] | None = None,
    expiry: datetime | None = None,
    economics: PortfolioPlanEconomics | None = None,
    window: PortfolioEvaluationWindow = WINDOW,
) -> PortfolioPlan:
    legs = alternatives if alternatives is not None else (hold_leg(), cash_leg())
    chosen = select_plan_alternative(legs)
    return PortfolioPlan(
        window_identity=window.window_identity,
        candidate_panel_manifest=manifest(window=window),
        portfolio_version=PORTFOLIO_PLAN_SCHEMA_VERSION,
        plan_expiry=expiry if expiry is not None else T0 + timedelta(hours=48),
        policy_forecast_ids=PortfolioPlanPolicyBinding(),
        alternatives_including_cash=legs,
        action_sequence=(action if action is not None else hold_action(),),
        expected_economics=economics
        if economics is not None
        else economics_for(chosen.expected_net_dollars),
        constraints=constraints if constraints is not None else unratified_plan_constraints(),
        rejected_alternatives=rejected
        if rejected is not None
        else rejections_for(legs),
        comparisons=comparisons,
    )


def rotation_plan(
    *,
    constraints: PortfolioPlanConstraints | None = None,
    action: PortfolioPlanAction | None = None,
    window: PortfolioEvaluationWindow = WINDOW,
    expiry: datetime | None = None,
) -> tuple[PortfolioPlan, RotationComparison]:
    """A well-formed plan whose declared step is a rotation."""

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)
    record = comparison(
        hold=hold_record, cash=cash_record, replacement=replacement, window=window
    )
    chosen = action if action is not None else rotate_action(replacement.alternative_id)
    return (
        plan(
            alternatives=legs,
            action=chosen,
            constraints=constraints if constraints is not None else scenario_constraints(),
            comparisons=(record,),
            window=window,
            expiry=expiry,
        ),
        record,
    )


def capital_cash(
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


def capital_snapshot(
    *,
    version: str = CAP_VERSION,
    free: str = "10000.00",
    risk_used: str = "900.00",
    sleeve_used: str = "1000.00",
    account: str = CAP_ACCOUNT,
) -> CapitalSnapshot:
    """The existing `SC-06` capital book, built only from declared evidence."""

    return CapitalSnapshot(
        portfolio_version=version,
        account=account,
        environment=CapitalEnvironment.PAPER_LEDGER,
        engine=CAP_ENGINE,
        currency=CAP_CURRENCY,
        accounting_state=CapitalAccountingState.SUPPORTED,
        cash=capital_cash(free=free),
        positions=(
            CapitalPosition(
                symbol=SYMBOL,
                quantity=Decimal("0.1"),
                basis_capital="3000.00",
                marked_value="3120.00",
                marked_at=T0,
                mark_reference=f"MARK:{SYMBOL}:1",
            ),
        ),
        marks=CapitalMarkState(
            marked_at=T0,
            mark_source="paper-ledger-mark-v1",
            marked_position_value="3120.00",
        ),
        sleeve_usage=(SleeveUsage(sleeve="TACTICAL", used_capital=sleeve_used, as_of=T0),),
        limit_usage=(
            CapitalLimitUsage(kind=CapitalLimitKind.RISK, used_capital=risk_used, as_of=T0),
            CapitalLimitUsage(
                kind=CapitalLimitKind.CONCENTRATION, used_capital="900.00", as_of=T0
            ),
            CapitalLimitUsage(
                kind=CapitalLimitKind.LIQUIDITY, used_capital="900.00", as_of=T0
            ),
        ),
        fee_state=CapitalFeeState(
            fee_model_version="fee-model-v1",
            fees_settled="0.00",
            fee_watermark=CapitalSourceWatermark(
                ledger_sequence=7, observed_at=T0, state_is_stale=False
            ),
        ),
        source_watermark=CapitalSourceWatermark(
            ledger_sequence=7, observed_at=T0, state_is_stale=False
        ),
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=T0,
            reference="RECON:R5D:1",
            variance="0.00",
        ),
        freshness=CapitalFreshness(
            as_of=T0,
            validity_deadline=T0 + timedelta(seconds=900),
            evidence_complete=True,
        ),
    )


def scenario_deployable_policy(record: CapitalSnapshot, **overrides):
    """The labelled engineering-scenario deployable policy bound to a book."""

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


def deployable(record: CapitalSnapshot, *, policy=None, at_time: datetime | None = None):
    return evaluate_deployable_capital(
        record,
        policy=policy if policy is not None else scenario_deployable_policy(record),
        at_time=at_time if at_time is not None else T0 + timedelta(seconds=60),
    )


def reservation_plan(
    record: CapitalSnapshot,
    *,
    amount: float = 100.0,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    portfolio_version: str | None = None,
) -> PortfolioReservationPlan:
    created = created_at if created_at is not None else T0
    expires = expires_at if expires_at is not None else created + timedelta(hours=1)
    version = portfolio_version if portfolio_version is not None else record.portfolio_version
    return PortfolioReservationPlan(
        portfolio_version=version,
        window_identity=WINDOW.window_identity,
        created_at=created,
        reservations=(
            PortfolioReservation(
                candidate_id="PCAND:R5D:1",
                symbol="ETHUSDT",
                direction=PortfolioDirection.LONG,
                reserved_capital=amount,
                portfolio_version=version,
                created_at=created,
                expires_at=expires,
            ),
        ),
    )


def exit_leg(
    *,
    quantity: float = 6.0,
    price: float = 10.0,
    fee: float = 1.0,
    intent: str = "PINT:R5D:EXIT:1",
    fills: tuple[RotationLegFill, ...] | None = None,
) -> RotationLegReference:
    record = fills
    if record is None:
        record = (
            RotationLegFill(
                fill_id="PFILL:R5D:EXIT:1", quantity=quantity, price=price, fee=fee
            ),
        )
    return RotationLegReference(leg=RotationLeg.EXIT, intent_id=intent, fills=record)


def entry_leg(
    *,
    quantity: float = 4.0,
    price: float = 10.0,
    fee: float = 0.5,
    intent: str = "PINT:R5D:ENTRY:1",
    policy: str = "PFCST:ENTRY:1",
    fills: tuple[RotationLegFill, ...] | None = None,
) -> RotationLegReference:
    record = fills
    if record is None:
        record = (
            RotationLegFill(
                fill_id="PFILL:R5D:ENTRY:1", quantity=quantity, price=price, fee=fee
            ),
        )
    return RotationLegReference(
        leg=RotationLeg.ENTRY,
        intent_id=intent,
        policy_forecast_id=policy,
        fills=record,
    )


def thesis(
    *,
    entry_policy: str = "PFCST:ENTRY:1",
    allowed: tuple[str, ...] = ("PFCST:ENTRY:1", "PFCST:ENTRY:2"),
) -> RotationReplacementThesis:
    return RotationReplacementThesis(
        subject_ref=SUBJECT,
        thesis_ref="PTHS:R5D:1",
        direction="LONG",
        entry_policy_forecast_id=entry_policy,
        policy_forecast_ids=allowed,
    )


def thesis_record_with_subject(subject: str) -> RotationReplacementThesis:
    return RotationReplacementThesis(
        subject_ref=subject,
        thesis_ref="PTHS:R5D:1",
        direction="LONG",
        entry_policy_forecast_id="PFCST:ENTRY:1",
        policy_forecast_ids=("PFCST:ENTRY:1",),
    )


def old_position(*, quantity: float = 10.0) -> RotationOldPosition:
    return RotationOldPosition(
        subject_ref=SUBJECT,
        direction="LONG",
        quantity=quantity,
        position_ref="PPOS:R5D:1",
    )


def abandonment_reason_is_retained(record: RotationGroup) -> bool:
    """Whether a failed second leg still records why the replacement did not enter."""

    return (
        record.terminal_state is RotationTerminalState.ABANDONED_TO_CASH
        and RotationGroupReason.REPLACEMENT_NOT_REQUALIFIED in record.reasons
        and record.committed_proceeds.exit_proceeds > 0.0
        and record.entry_leg is None
    )


def _resolve_binding(
    plan_record: PortfolioPlan | None,
    comparison_record: RotationComparison | None,
) -> tuple[PortfolioPlan, RotationComparison]:
    if plan_record is not None and comparison_record is not None:
        return plan_record, comparison_record
    built_plan, built_comparison = rotation_plan()
    return (
        plan_record if plan_record is not None else built_plan,
        comparison_record if comparison_record is not None else built_comparison,
    )


def third_leg_plan(
    *,
    constraints: PortfolioPlanConstraints | None = None,
    expiry: datetime | None = None,
) -> tuple[PortfolioPlan, RotationComparison]:
    """A plan that records the common-window comparison but declares no switch.

    The plan carries the same three legs and the same preregistered comparison a
    rotation would need while its declared step stays a hold. A `ROTATE` step may
    only be declared when the plan's own recorded evidence already admits the
    switch, so the *decision-time* gate branches are proved on a plan that
    declares no switch at all.
    """

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)
    return (
        plan(
            alternatives=(hold_record, cash_record, replacement),
            action=hold_action(),
            comparisons=(record,),
            constraints=constraints if constraints is not None else scenario_constraints(),
            expiry=expiry,
        ),
        record,
    )


def group(
    *,
    plan_record: PortfolioPlan | None = None,
    comparison_record: RotationComparison | None = None,
    legs: tuple[RotationLegReference, ...] | None = None,
    committed_to_entry: float = 40.5,
    residual: float = 4.0,
    terminal_state: RotationTerminalState = RotationTerminalState.ENTRY_COMMITTED,
    requalification: RotationRequalification | None = None,
    reasons: tuple[RotationGroupReason, ...] | None = None,
    old_quantity: float = 10.0,
    thesis_record: RotationReplacementThesis | None = None,
    exit_record: RotationLegReference | None = None,
    entry_record: RotationLegReference | None = None,
    with_entry: bool = True,
) -> RotationGroup:
    """A committed partial rotation: 6 of 10 units exit, 40.50 of 59.00 re-enters.

    The entry leg's committed fills cost 40.00 of notional plus 0.50 of fee, so the
    entry commitment is 40.50 and 18.50 is released to cash: the fixture is the
    cash-accurate boundary of the entry it funds, not a rounded 40.00 commitment
    that the entry could never have been afforded from.
    """

    bound_plan, bound_comparison = _resolve_binding(plan_record, comparison_record)
    exit_fills = exit_record if exit_record is not None else exit_leg()
    entry_fills = entry_record if entry_record is not None else entry_leg()
    if legs is not None:
        legs_record = legs
    elif with_entry:
        legs_record = (exit_fills, entry_fills)
    else:
        legs_record = (exit_fills,)
    proceeds = commit_rotation_proceeds(
        exit_fills, committed_to_entry=committed_to_entry
    )
    return RotationGroup(
        old_position=old_position(quantity=old_quantity),
        replacement_thesis=thesis_record if thesis_record is not None else thesis(),
        legs=legs_record,
        committed_proceeds=proceeds,
        requalification=requalification
        if requalification is not None
        else RotationRequalification(decision=RotationRequalificationDecision.PENDING),
        residual_exposure=RotationResidualExposure(subject_ref=SUBJECT, quantity=residual),
        terminal_state=terminal_state,
        reasons=reasons
        if reasons is not None
        else (
            RotationGroupReason.EXIT_FILLS_COMMITTED,
            RotationGroupReason.PARTIAL_EXIT_RESIDUAL_RETAINED,
            RotationGroupReason.ENTRY_FUNDED_BY_EXIT_PROCEEDS,
            RotationGroupReason.ENTRY_FILLS_COMMITTED,
            RotationGroupReason.PROCEEDS_RELEASED_TO_CASH,
        ),
        plan_id=bound_plan.plan_id,
        window_identity=bound_plan.window_identity,
        comparison_id=bound_comparison.comparison_id,
    )


# ---------------------------------------------------------------------------
# AC-001 The plan record and its frozen keys (PP-01, SC-08)
# ---------------------------------------------------------------------------


def test_ac_001_the_plan_carries_every_frozen_record_key() -> None:
    """ATDD-R5-D-portfolio-planning/AC-001: the frozen record is complete.

    GIVEN the frozen `SC-08` portfolio-plan record
    WHEN a plan revision is built and exported to its durable form
    THEN it declares exactly the frozen record keys in order, its version is the
    ratified plan schema version, its identity is the declared content hash and
    it is never authoritative
    """

    record = plan()
    payload = record.to_dict()
    assert set(PORTFOLIO_PLAN_RECORD_KEYS) == {
        "candidate_panel_manifest",
        "portfolio_version",
        "plan_expiry",
        "policy_forecast_ids",
        "alternatives_including_cash",
        "action_sequence",
        "expected_economics",
        "constraints",
    }
    assert assert_plan_record_keys(record) == tuple(payload)
    for key in PORTFOLIO_PLAN_RECORD_KEYS:
        assert key in payload, key
    assert record.portfolio_version == PORTFOLIO_PLAN_SCHEMA_VERSION
    assert record.schema_version == PORTFOLIO_PLAN_SCHEMA_VERSION
    assert record.plan_id.startswith(f"{PORTFOLIO_PLAN_ID_PREFIX}:")
    assert record.authoritative is False
    assert not hasattr(record, "reservations")
    assert not hasattr(record, "order_id")
    assert payload["candidate_panel_manifest"]["window_identity"] == record.window_identity
    assert record.has_cash_comparison is True
    assert record.compared_kinds == ("HOLD", "EXIT_TO_CASH")


def test_ac_001_the_plan_round_trips_and_a_forgery_fails_closed() -> None:
    """ATDD-R5-D-portfolio-planning/AC-001: identity is content, not trust.

    GIVEN a durable plan payload
    WHEN it is reloaded, then mutated, dropped, renamed or extended
    THEN the untouched payload round trips and every forgery is refused
    """

    record = plan()
    payload = record.to_dict()
    assert PortfolioPlan.from_dict(payload) == record

    mutated = dict(payload)
    mutated["expected_economics"] = dict(payload["expected_economics"])
    mutated["expected_economics"]["expected_trading_net"] = 999.0
    with pytest.raises(PortfolioContractError, match="expected net|band|identity"):
        PortfolioPlan.from_dict(mutated)

    forged_identity = dict(payload)
    forged_identity["plan_id"] = f"{PORTFOLIO_PLAN_ID_PREFIX}:{'0' * 32}"
    with pytest.raises(PortfolioContractError, match="plan_id"):
        PortfolioPlan.from_dict(forged_identity)

    dropped = dict(payload)
    dropped.pop("constraints")
    with pytest.raises(PortfolioContractError, match="missing"):
        PortfolioPlan.from_dict(dropped)

    extended = dict(payload)
    extended["mode_override"] = "active"
    with pytest.raises(PortfolioContractError, match="unknown key"):
        PortfolioPlan.from_dict(extended)

    renamed = dict(payload)
    renamed["portfolio_version"] = "portfolio-plan-v2"
    with pytest.raises(PortfolioContractError, match="portfolio_version|plan_id"):
        PortfolioPlan.from_dict(renamed)


def test_ac_001_an_unbound_panel_window_or_version_is_refused() -> None:
    """ATDD-R5-D-portfolio-planning/AC-001: an unbound record fails closed.

    GIVEN a plan revision and the records it must be bound to
    WHEN the manifest, the version or the compared legs do not match the plan
    THEN the plan is refused instead of being stored with unbound evidence
    """

    other_window = PortfolioEvaluationWindow(
        start=T0 + timedelta(days=10), end=T0 + timedelta(days=15)
    )
    with pytest.raises(PortfolioContractError, match="own evaluation window"):
        PortfolioPlan(
            window_identity=WINDOW.window_identity,
            candidate_panel_manifest=manifest(window=other_window),
            portfolio_version=PORTFOLIO_PLAN_SCHEMA_VERSION,
            plan_expiry=T0 + timedelta(hours=1),
            policy_forecast_ids=PortfolioPlanPolicyBinding(),
            alternatives_including_cash=(hold_leg(), cash_leg()),
            action_sequence=(hold_action(),),
            expected_economics=economics_for(100.0),
            constraints=unratified_plan_constraints(),
        )

    with pytest.raises(PortfolioContractError, match="portfolio_version"):
        PortfolioPlan(
            window_identity=WINDOW.window_identity,
            candidate_panel_manifest=manifest(),
            portfolio_version="portfolio-plan-v2",
            plan_expiry=T0 + timedelta(hours=1),
            policy_forecast_ids=PortfolioPlanPolicyBinding(),
            alternatives_including_cash=(hold_leg(), cash_leg()),
            action_sequence=(hold_action(),),
            expected_economics=economics_for(100.0),
            constraints=unratified_plan_constraints(),
        )

    with pytest.raises(PortfolioContractError, match="EXIT_TO_CASH"):
        plan(alternatives=(hold_leg(),), rejected=())

    with pytest.raises(PortfolioContractError, match="one subject"):
        plan(
            alternatives=(
                hold_leg(),
                cash_leg(subject="POPT:R5D:9"),
            )
        )


# ---------------------------------------------------------------------------
# AC-002 Disposition vocabulary and per-disposition capital laws (PP-02)
# ---------------------------------------------------------------------------


def test_ac_002_only_the_declared_dispositions_are_eligible() -> None:
    """ATDD-R5-D-portfolio-planning/AC-002: the closed disposition vocabulary.

    GIVEN the declared eligible dispositions of `PP-02`
    WHEN the vocabulary is inspected and an undeclared disposition is used
    THEN the seven declared tokens are exactly eligible, `ADD` is disabled and an
    unknown or disabled token is refused
    """

    assert PORTFOLIO_PLAN_DISPOSITIONS == (
        "HOLD_CASH",
        "WAIT",
        "HOLD_POSITION",
        "NEW_ENTRY",
        "REDUCE",
        "EXIT",
        "ROTATE",
    )
    assert PORTFOLIO_PLAN_DISABLED_DISPOSITIONS == ("ADD",)
    assert "ADD" not in {member.value for member in PortfolioPlanDisposition}

    # `ADD` is not merely disabled — it has no token at all, so an `ADD` step
    # cannot even be represented, and the declared disabled list refuses it as
    # well should it ever become representable.
    with pytest.raises(PortfolioContractError, match="unsupported token"):
        PortfolioPlanAction(
            sequence=1,
            disposition="ADD",
            subject_ref=SUBJECT,
            capital_delta=10.0,
        )
    with pytest.raises(PortfolioContractError, match="unsupported token"):
        PortfolioPlanAction(
            sequence=1,
            disposition="TARGET_MODE",
            subject_ref=SUBJECT,
            capital_delta=10.0,
        )

    plan_record = plan()
    assert [action.disposition.value for action in plan_record.action_sequence] == [
        "HOLD_POSITION"
    ]
    assert plan_record.is_rotation_plan is False
    assert plan_record.rotation_actions == ()


def test_ac_002_every_disposition_obeys_its_declared_capital_sign() -> None:
    """ATDD-R5-D-portfolio-planning/AC-002: a disposition cannot lie about capital.

    GIVEN the declared capital sign of each eligible disposition
    WHEN an action declares a capital delta that contradicts its disposition
    THEN the action is refused, so a `WAIT` cannot move capital and an `EXIT`
    cannot be a hold
    """

    for disposition in ("HOLD_CASH", "WAIT", "HOLD_POSITION"):
        PortfolioPlanAction(
            sequence=1, disposition=disposition, subject_ref=SUBJECT, capital_delta=0.0
        )
        with pytest.raises(PortfolioContractError, match="moves no capital"):
            PortfolioPlanAction(
                sequence=1,
                disposition=disposition,
                subject_ref=SUBJECT,
                capital_delta=1.0,
            )

    with pytest.raises(PortfolioContractError, match="commits capital"):
        PortfolioPlanAction(
            sequence=1,
            disposition="NEW_ENTRY",
            subject_ref=SUBJECT,
            capital_delta=0.0,
        )
    PortfolioPlanAction(
        sequence=1, disposition="NEW_ENTRY", subject_ref=SUBJECT, capital_delta=25.0
    )

    for disposition in ("REDUCE", "EXIT"):
        PortfolioPlanAction(
            sequence=1,
            disposition=disposition,
            subject_ref=SUBJECT,
            capital_delta=-25.0,
        )
        with pytest.raises(PortfolioContractError, match="releases capital"):
            PortfolioPlanAction(
                sequence=1,
                disposition=disposition,
                subject_ref=SUBJECT,
                capital_delta=25.0,
            )

    with pytest.raises(PortfolioContractError, match="EXIT_THEN_ENTER"):
        PortfolioPlanAction(
            sequence=1, disposition="ROTATE", subject_ref=SUBJECT, capital_delta=1.0
        )


def test_ac_002_a_rotate_action_requires_a_proven_comparison_and_a_ratified_policy() -> None:
    """ATDD-R5-D-portfolio-planning/AC-002: a rotation is proven, never assumed.

    GIVEN a plan that declares a `ROTATE` step
    WHEN the switch policy is unratified, the comparison is missing or the step
    names an alternative the plan never compared
    THEN the plan is refused and the governed outcome stays an abstention
    """

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)
    action = rotate_action(replacement.alternative_id)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)

    with pytest.raises(PortfolioContractError, match="ratified switch policy"):
        plan(
            alternatives=legs,
            action=action,
            comparisons=(record,),
            constraints=unratified_plan_constraints(),
        )

    with pytest.raises(PortfolioContractError, match="comparison"):
        plan(
            alternatives=legs,
            action=action,
            comparisons=(),
            constraints=scenario_constraints(),
        )

    with pytest.raises(PortfolioContractError, match="compared"):
        plan(
            alternatives=legs,
            action=rotate_action("PALT:R5D:missing"),
            comparisons=(record,),
            constraints=scenario_constraints(),
        )

    # A comparison whose legs are not the plan's own compared alternatives cannot be
    # attached to the plan at all; the substituted-comparison refusal is proved in
    # `test_ac_002_a_substituted_comparison_cannot_license_a_rotate_step`.
    with pytest.raises(PortfolioContractError, match="substituted or forged comparison"):
        plan(
            alternatives=(hold_record, cash_record),
            action=rotate_action(replacement.alternative_id),
            comparisons=(record,),
            constraints=scenario_constraints(),
        )

    proven_plan, _ = rotation_plan()
    assert proven_plan.is_rotation_plan is True
    assert proven_plan.rotation_actions[0].is_proven_rotation is True


def test_ac_002_a_rotate_step_must_be_admitted_by_the_plans_own_evidence() -> None:
    """ATDD-R5-D-portfolio-planning/AC-002: a declared switch is a proven advantage.

    GIVEN a plan whose own recorded comparison leaves the switch gate abstaining
    WHEN the plan declares a `ROTATE` step
    THEN the plan revision is refused instead of being recorded as a rotation,
    because a declared switch must already satisfy every gate branch the plan's own
    evidence settles
    """

    cash_record = cash_leg()
    hold_record = hold_leg(gross=118.0)
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)

    # The replacement *is* the observed best alternative (121.00 against 118.00 and
    # 94.00), yet its 3.00 advantage is below the ratified scenario minimum of 5.00.
    assert select_plan_alternative(legs) is replacement
    assert record.expected_advantage_over_best_alternative == 3.0

    with pytest.raises(PortfolioContractError, match="ADVANTAGE_BELOW_MINIMUM"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id, delta=121.0),
            comparisons=(record,),
            constraints=scenario_constraints(),
        )

    # The treatment declares the lower bound but no leg carries a band: the switch
    # cannot be read, so it cannot be declared.
    with pytest.raises(PortfolioContractError, match="UNCERTAINTY_BAND_UNCLEAR"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id, delta=121.0),
            comparisons=(record,),
            constraints=band_constraints(),
        )

    # The plan's own round trip alone busts the whole budget, and prior turnover is
    # never negative, so no history could license this switch.
    with pytest.raises(PortfolioContractError, match="TURNOVER_BUDGET_EXHAUSTED"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id, delta=121.0),
            comparisons=(record,),
            constraints=scenario_constraints(turnover_budget_dollars=1.0),
        )

    # The same evidence, recorded as a hold, is a legitimate plan: the refusal is
    # about the declared action, not about the record.
    holding = plan(
        alternatives=legs,
        action=hold_action(),
        comparisons=(record,),
        constraints=scenario_constraints(),
    )
    assert holding.is_rotation_plan is False
    assert (
        holding.assess_rotation_switch(
            now=T0 + timedelta(hours=1),
            current_plan_id=holding.plan_id,
            switched_capital_dollars=RATIFIED_SCENARIO_SWITCH_CAPITAL,
        ).abstention_reason
        is PortfolioPlanAbstentionReason.ADVANTAGE_BELOW_MINIMUM
    )

    admitted, _ = rotation_plan()
    assert admitted.is_rotation_plan is True
    assert (
        admitted.assess_rotation_switch(
            now=T0 + timedelta(hours=1), current_plan_id=admitted.plan_id
        ).status
        is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
    )


def test_ac_002_a_rotate_step_must_name_the_plans_best_observed_alternative() -> None:
    """ATDD-R5-D-portfolio-planning/AC-002: the recorded ranking decides the switch.

    GIVEN a plan whose own alternatives rank holding above the replacement
    WHEN the declared step is a `ROTATE` into that replacement
    THEN the plan is refused, so a plan can never act on an alternative its own
    eligible-alternative ranking did not select
    """

    cash_record = cash_leg()
    hold_record = hold_leg(gross=130.0)
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)

    assert hold_record.expected_net_dollars == 130.0
    assert replacement.expected_net_dollars == 121.0
    assert select_plan_alternative(legs) is hold_record

    with pytest.raises(PortfolioContractError, match="best observed eligible alternative"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id, delta=121.0),
            comparisons=(record,),
            constraints=scenario_constraints(),
        )

    holding = plan(
        alternatives=legs,
        action=hold_action(),
        comparisons=(record,),
        constraints=scenario_constraints(),
    )
    assert holding.best_observed_alternative_id == hold_record.alternative_id
    assert holding.is_rotation_plan is False


def test_ac_002_a_substituted_comparison_cannot_license_a_rotate_step() -> None:
    """ATDD-R5-D-portfolio-planning/AC-002: only the plan's own legs can prove it.

    GIVEN a plan that compared its own `HOLD`, `EXIT_TO_CASH` and `EXIT_THEN_ENTER`
    alternatives
    WHEN a step is attached to a comparison carrying the same subject and window but
    different, more favourable legs
    THEN the substituted comparison is refused, whether or not the action names the
    plan's own alternative, because those economics are not the plan's evidence
    """

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)

    generous_cash = cash_leg()
    generous_replacement = replacement_leg(cash=generous_cash, entry_gross=5_000.0)
    substituted = comparison(
        hold=hold_leg(gross=10.0),
        cash=generous_cash,
        replacement=generous_replacement,
    )
    assert substituted.window_identity == WINDOW.window_identity

    with pytest.raises(PortfolioContractError, match="substituted or forged comparison"):
        plan(
            alternatives=legs,
            action=rotate_action(substituted.replacement.alternative_id),
            comparisons=(substituted,),
            constraints=scenario_constraints(),
        )

    with pytest.raises(PortfolioContractError, match="substituted or forged comparison"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id),
            comparisons=(substituted,),
            constraints=scenario_constraints(),
        )

    owned = comparison(hold=hold_record, cash=cash_record, replacement=replacement)
    admitted = plan(
        alternatives=legs,
        action=rotate_action(replacement.alternative_id),
        comparisons=(owned,),
        constraints=scenario_constraints(),
    )
    assert admitted.is_rotation_plan is True
    assert admitted.comparisons[0].comparison_id == owned.comparison_id


def test_ac_002_no_profit_target_or_trade_count_can_force_a_trade() -> None:
    """ATDD-R5-D-portfolio-planning/AC-002: profit targets cannot force a trade.

    GIVEN a plan whose observed alternatives are all expected to lose after costs
    WHEN the plan and the switch gate are evaluated
    THEN no target, minimum trade count or unratified number can manufacture a
    rotation: holding is selected and the switch abstains
    """

    section = r5d_section(PORTFOLIO_MODULE, R5D_BANNER)
    for token in ("profit_target", "min_trades", "minimum_trade_count", "target_mode"):
        assert token not in section, token

    hold_record = hold_leg(gross=-10.0)
    cash_record = cash_leg(gross=-10.0)
    replacement = replacement_leg(
        cash=cash_record, entry_gross=0.0, entry_fee=0.0, entry_operating=0.0
    )
    legs = (hold_record, cash_record, replacement)
    assert hold_record.expected_net_dollars == -10.0
    assert replacement.expected_net_dollars == -16.0

    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)
    assert record.expected_advantage_over_best_alternative == -6.0

    plan_record = plan(
        alternatives=legs,
        action=hold_action(),
        comparisons=(record,),
        constraints=scenario_constraints(),
    )
    assert plan_record.is_rotation_plan is False

    assessment = assess_rotation_switch(
        plan_record,
        now=T0 + timedelta(hours=1),
        current_plan_id=plan_record.plan_id,
        switched_capital_dollars=RATIFIED_SCENARIO_SWITCH_CAPITAL,
    )
    assert assessment.status is RotationSwitchStatus.ABSTAINED
    assert (
        assessment.abstention_reason
        is PortfolioPlanAbstentionReason.ADVANTAGE_BELOW_MINIMUM
    )
    assert assessment.advantage_dollars is None


# ---------------------------------------------------------------------------
# AC-003 Deterministic selection over the observed alternatives (PP-04)
# ---------------------------------------------------------------------------


def test_ac_003_best_means_best_among_the_observed_alternatives() -> None:
    """ATDD-R5-D-portfolio-planning/AC-003: "best" is observed, not hypothetical.

    GIVEN the observed alternatives of a plan revision
    WHEN the selection is inspected
    THEN it is the head of a deterministic ranking of the alternatives the plan
    actually observed, and no unobserved or market-wide optimum can be claimed
    """

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)

    ranking = rank_plan_alternatives(legs)
    assert select_plan_alternative(legs) is replacement
    assert [item.kind.value for item in ranking] == [
        "EXIT_THEN_ENTER",
        "HOLD",
        "EXIT_TO_CASH",
    ]
    assert ranking[0].expected_net_dollars == 121.0
    assert rank_plan_alternatives(legs) == ranking
    assert (
        PORTFOLIO_ALTERNATIVE_TIE_BREAK_RULE
        == "net-descending-then-declared-leg-preference-then-alternative-id"
    )

    assert_plan_selects_best_observed(replacement, legs)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)
    plan_record = plan(
        alternatives=legs,
        action=rotate_action(replacement.alternative_id),
        comparisons=(record,),
        constraints=scenario_constraints(),
    )
    assert plan_record.best_observed_alternative_id == replacement.alternative_id

    unobserved = hold_leg(gross=5000.0)
    with pytest.raises(PortfolioContractError, match="best observed"):
        assert_plan_selects_best_observed(unobserved, legs)


def test_ac_003_an_exact_tie_prefers_holding_over_churning() -> None:
    """ATDD-R5-D-portfolio-planning/AC-003: a tie never churns the book.

    GIVEN a hold, a cash and a composed replacement with the same expected net
    WHEN the alternatives are ranked
    THEN the declared preference selects holding first and the switch last
    """

    assert PORTFOLIO_ALTERNATIVE_PREFERENCE == (
        "HOLD",
        "EXIT_TO_CASH",
        "EXIT_THEN_ENTER",
    )

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record, entry_gross=9.0)
    assert hold_record.expected_net_dollars == 100.0
    assert replacement.expected_net_dollars == 100.0

    legs = (hold_record, cash_record, replacement)
    ranking = rank_plan_alternatives(legs)
    assert ranking[0] is hold_record
    assert ranking[1] is replacement
    assert ranking[2] is cash_record

    plan_record = plan(alternatives=legs)
    assert plan_record.best_observed_alternative_id == hold_record.alternative_id
    plan_record.assert_rejections_are_complete()
    assert plan_record.is_rotation_plan is False
    assert {item.reason_code for item in plan_record.rejected_alternatives} == {
        "LOWER_EXPECTED_NET",
        "PREFERENCE_TIE_BREAK",
    }

    # The tied switch is recorded as a preference tie-break, not as an advantage.
    tied = [item for item in plan_record.rejected_alternatives]
    assert any(item.reason_code == "PREFERENCE_TIE_BREAK" for item in tied)
    assert not any(
        item.alternative_id == hold_record.alternative_id for item in tied
    )


def test_ac_003_a_plan_cannot_select_an_alternative_it_never_compared() -> None:
    """ATDD-R5-D-portfolio-planning/AC-003: no claim without a comparison.

    GIVEN a plan that compared a fixed set of alternatives
    WHEN a step names an alternative outside that set
    THEN the plan is refused instead of acting on an uncompared alternative
    """

    legs = (hold_leg(), cash_leg())
    with pytest.raises(PortfolioContractError, match="compared"):
        plan(
            alternatives=legs,
            action=PortfolioPlanAction(
                sequence=1,
                disposition="NEW_ENTRY",
                subject_ref=SUBJECT,
                capital_delta=10.0,
                alternative_id="PALT:R5D:uncompared",
            ),
        )

    record = plan(alternatives=legs)
    assert record.comparison_for_alternative("PALT:R5D:absent") is None
    assert record._default_comparison(None) is None
    assert record.has_replacement_comparison is False


def test_ac_003_every_passed_over_alternative_is_recorded_exactly_once() -> None:
    """ATDD-R5-D-portfolio-planning/AC-003: the exclusions are recorded evidence.

    GIVEN a plan that examined several alternatives
    WHEN its rejections are inspected
    THEN every compared alternative but the selected one appears exactly once with
    a reason code from the declared vocabulary, and a rejection for an uncompared
    or unnaming alternative is refused
    """

    assert set(PORTFOLIO_PLAN_REJECTION_REASONS) == {
        "ADVANTAGE_BELOW_MINIMUM",
        "EXCLUDED_BY_CONSTRAINT",
        "EVIDENCE_INCOMPLETE",
        "LOWER_EXPECTED_NET",
        "PREFERENCE_TIE_BREAK",
    }

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)
    plan_record = plan(
        alternatives=legs,
        action=rotate_action(replacement.alternative_id),
        comparisons=(record,),
        constraints=scenario_constraints(),
    )
    plan_record.assert_rejections_are_complete()
    recorded = {item.alternative_id for item in plan_record.rejected_alternatives}
    assert recorded == {hold_record.alternative_id, cash_record.alternative_id}

    incomplete = plan(
        alternatives=legs,
        action=rotate_action(replacement.alternative_id),
        comparisons=(record,),
        constraints=scenario_constraints(),
        rejected=(
            PortfolioPlanRejection(
                alternative_id=hold_record.alternative_id, reason_code="LOWER_EXPECTED_NET"
            ),
        ),
    )
    with pytest.raises(PortfolioContractError, match="exactly once"):
        incomplete.assert_rejections_are_complete()

    with pytest.raises(PortfolioContractError, match="unnamed token"):
        PortfolioPlanRejection(
            alternative_id=hold_record.alternative_id, reason_code="BECAUSE_I_SAID_SO"
        )

    with pytest.raises(PortfolioContractError, match="actually compared"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id),
            comparisons=(record,),
            constraints=scenario_constraints(),
            rejected=(
                PortfolioPlanRejection(
                    alternative_id="PALT:R5D:uncompared", reason_code="LOWER_EXPECTED_NET"
                ),
            ),
        )

    with pytest.raises(PortfolioContractError, match="exactly once"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id),
            comparisons=(record,),
            constraints=scenario_constraints(),
            rejected=(
                PortfolioPlanRejection(
                    alternative_id=hold_record.alternative_id,
                    reason_code="LOWER_EXPECTED_NET",
                ),
                PortfolioPlanRejection(
                    alternative_id=hold_record.alternative_id,
                    reason_code="EVIDENCE_INCOMPLETE",
                ),
            ),
        )


# ---------------------------------------------------------------------------
# AC-004 Matched common-window economics (RO-01)
# ---------------------------------------------------------------------------


def test_ac_004_exit_and_entry_costs_are_counted_once() -> None:
    """ATDD-R5-D-portfolio-planning/AC-004: the round trip is charged once.

    GIVEN a composed `EXIT_THEN_ENTER` leg built from the exit leg
    WHEN the round trip is inspected, and a composed leg charges the exit twice
    THEN the exit fee, spread and latency appear exactly once, the composed leg
    carries the exit leg's own fee, and a double-charged comparison fails closed
    """

    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)

    assert replacement.exit_fee == cash_record.exit_fee
    assert replacement.spread_cost == cash_record.spread_cost
    assert replacement.round_trip_cost == cash_record.round_trip_cost + 2.0
    assert record.exit_costs_are_counted_once is True
    assert record.window_count == 1
    assert replacement.expected_net_dollars == 121.0
    assert record.expected_advantage_over_hold == 21.0
    assert record.expected_advantage_over_cash == 27.0
    assert record.expected_advantage_over_best_alternative == 21.0

    double_charged = PortfolioAlternative(
        kind=PortfolioAlternativeKind.EXIT_THEN_ENTER,
        subject_ref=SUBJECT,
        expected_gross_net=130.0,
        exit_fee=10.0,
        entry_fee=2.0,
        spread_cost=1.0,
        attributable_operating_cost=1.0,
    )
    with pytest.raises(PortfolioContractError, match="counted once"):
        RotationComparison(
            window_identity=WINDOW.window_identity,
            subject_ref=SUBJECT,
            hold=hold_record,
            cash=cash_record,
            replacement=double_charged,
        )

    with pytest.raises(PortfolioContractError, match="round-trip cost"):
        alternative(PortfolioAlternativeKind.HOLD, gross=100.0, exit_fee=1.0)
    with pytest.raises(PortfolioContractError, match="entry fee"):
        alternative(PortfolioAlternativeKind.EXIT_TO_CASH, gross=100.0, entry_fee=1.0)


def test_ac_004_the_exit_spread_latency_and_operating_cost_cannot_be_dropped() -> None:
    """ATDD-R5-D-portfolio-planning/AC-004: the whole exit cost stack is counted once.

    GIVEN an exit leg that pays a fee, a spread, a latency and an attributable
    operating cost
    WHEN a composed replacement understates any one of those exit costs
    THEN the comparison is refused at the record boundary and on reload, so the
    switch can never be compared on after-cost economics it never paid
    """

    cash_record = cash_leg(spread=1.0, latency=0.25, operating=0.5)
    composed = replacement_leg(
        cash=cash_record,
        entry_spread=0.5,
        entry_latency=0.25,
        entry_operating=0.5,
    )
    assert composed.spread_cost == 1.5
    assert composed.latency_cost == 0.5
    assert composed.attributable_operating_cost == 1.0

    counted_once = comparison(hold=hold_leg(), cash=cash_record, replacement=composed)
    assert counted_once.exit_costs_are_counted_once is True
    assert RotationComparison.from_dict(counted_once.to_dict()) == counted_once

    def entry_leg_understating(**overrides: float) -> PortfolioAlternative:
        fields: dict[str, float] = {
            "spread_cost": composed.spread_cost,
            "latency_cost": composed.latency_cost,
            "attributable_operating_cost": float(composed.attributable_operating_cost),
        }
        fields.update(overrides)
        return PortfolioAlternative(
            kind=PortfolioAlternativeKind.EXIT_THEN_ENTER,
            subject_ref=SUBJECT,
            expected_gross_net=composed.expected_gross_net,
            exit_fee=cash_record.exit_fee,
            entry_fee=composed.entry_fee,
            spread_cost=fields["spread_cost"],
            latency_cost=fields["latency_cost"],
            attributable_operating_cost=fields["attributable_operating_cost"],
        )

    dropped_spread = entry_leg_understating(spread_cost=0.0)
    dropped_latency = entry_leg_understating(latency_cost=0.0)
    dropped_operating = entry_leg_understating(attributable_operating_cost=0.0)
    for understated in (dropped_spread, dropped_latency, dropped_operating):
        with pytest.raises(PortfolioContractError, match="counted once"):
            comparison(hold=hold_leg(), cash=cash_record, replacement=understated)

    payload = counted_once.to_dict()
    payload["replacement"] = dropped_spread.to_dict()
    with pytest.raises(PortfolioContractError, match="counted once"):
        RotationComparison.from_dict(payload)


def test_ac_004_a_sunk_acquisition_cost_never_enters_the_comparison() -> None:
    """ATDD-R5-D-portfolio-planning/AC-004: a past cost is sunk, not an advantage.

    GIVEN a comparison that retains the old position's acquisition cost
    WHEN the economics are inspected
    THEN the acquisition cost stays accounting evidence and changes no leg's
    expected net or advantage, and the composition has no parameter for it
    """

    assert "acquisition" not in inspect.signature(
        compose_rotation_replacement
    ).parameters

    base = comparison()
    with_cost = comparison(acquisition=2500.0)
    assert with_cost.acquisition_cost_for_accounting == 2500.0
    assert with_cost.expected_advantage_over_best_alternative == (
        base.expected_advantage_over_best_alternative
    )
    assert with_cost.expected_advantage_over_hold == base.expected_advantage_over_hold
    assert with_cost.replacement.expected_net_dollars == base.replacement.expected_net_dollars
    assert with_cost.replacement.alternative_id == base.replacement.alternative_id
    assert with_cost.comparison_id != base.comparison_id

    with pytest.raises(PortfolioContractError, match="must not be negative"):
        comparison(acquisition=-1.0)


def test_ac_004_missing_evidence_refuses_instead_of_looking_favourable() -> None:
    """ATDD-R5-D-portfolio-planning/AC-004: missing evidence is not a free option.

    GIVEN an alternative or a comparison with a missing operating cost or a
    partially declared uncertainty band
    WHEN the economics are requested
    THEN the request is refused instead of returning a flattering number
    """

    missing_operating = hold_leg(operating=None)
    assert missing_operating.has_operating_cost is False
    with pytest.raises(PortfolioContractError, match="operating cost"):
        _ = missing_operating.expected_net_dollars
    with pytest.raises(PortfolioContractError, match="operating cost"):
        comparison(hold=missing_operating)
    with pytest.raises(PortfolioContractError, match="operating cost"):
        rank_plan_alternatives((missing_operating, cash_leg()))
    with pytest.raises(PortfolioContractError, match="operating cost"):
        replacement_leg(entry_operating=None)

    with pytest.raises(PortfolioContractError, match="both bounds or on neither"):
        PortfolioAlternative(
            kind=PortfolioAlternativeKind.HOLD,
            subject_ref=SUBJECT,
            expected_gross_net=100.0,
            attributable_operating_cost=0.0,
            uncertainty_lower_bound=90.0,
        )
    with pytest.raises(PortfolioContractError, match="must contain"):
        alternative(PortfolioAlternativeKind.HOLD, gross=100.0, band=(200.0, 300.0))
    with pytest.raises(PortfolioContractError, match="must not exceed"):
        alternative(PortfolioAlternativeKind.HOLD, gross=100.0, band=(120.0, 110.0))


def test_ac_004_the_conservative_advantage_reads_the_unfavourable_side() -> None:
    """ATDD-R5-D-portfolio-planning/AC-004: the conservative read is not the gross read.

    GIVEN a comparison whose replacement has a wide declared uncertainty band
    WHEN both the expected and the conservative advantages are read
    THEN the expected advantage shows the gross upside while the conservative one
    reads the replacement's lower bound against the competitor's upper bound, and
    a missing band refuses the conservative read instead of assuming it
    """

    cash_record = cash_leg(band=(84.0, 104.0))
    hold_record = hold_leg(band=(90.0, 110.0))
    replacement = replacement_leg(
        cash=cash_record, entry_band=(-10.0, 30.0)
    )
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)

    assert replacement.uncertainty_lower_bound == 74.0
    assert replacement.uncertainty_upper_bound == 134.0
    assert replacement.expected_net_dollars == 121.0
    assert record.expected_advantage_over_best_alternative == 21.0
    assert record.conservative_advantage_over_best_alternative == -36.0

    without_bands = comparison(
        hold=hold_leg(), cash=cash_leg(), replacement=replacement_leg(cash=cash_leg())
    )
    with pytest.raises(PortfolioContractError, match="uncertainty band"):
        _ = without_bands.conservative_advantage_over_best_alternative


# ---------------------------------------------------------------------------
# AC-005 The discretionary switch gate and its closed precedence (RO-02)
# ---------------------------------------------------------------------------


def test_ac_005_an_unratified_policy_abstains_before_anything_numeric() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: an open owner policy abstains visibly.

    GIVEN `OQ-05`/`OQ-06` still open and the shipped constraint record
    WHEN the switch gate is evaluated
    THEN it abstains with `POLICY_NOT_BOUND` and records no advantage at all, and
    the same gap is reported even when every other gap is present
    """

    constraints = unratified_plan_constraints()
    assert constraints.is_ratified is False
    assert constraints.unratified_fields == (
        "min_switch_advantage_dollars",
        "uncertainty_treatment",
        "turnover_budget_dollars",
        "cooldown_hours",
    )
    assert OPEN_OWNER_INPUTS == ("OQ-05", "OQ-06")
    assert constraints.open_owner_inputs == OPEN_OWNER_INPUTS
    assert OWNER_INPUT_STATUS == "OWNER_DECISION_REQUIRED"
    assert constraints.owner_input_status == OWNER_INPUT_STATUS
    assert constraints.to_dict()["min_switch_advantage_dollars"] is None

    shipped_plan = plan()
    assessment = assess_rotation_switch(shipped_plan, now=T0 + timedelta(hours=1))
    assert assessment.status is RotationSwitchStatus.ABSTAINED
    assert assessment.is_abstained is True
    assert assessment.abstention_reason is PortfolioPlanAbstentionReason.POLICY_NOT_BOUND
    assert assessment.advantage_dollars is None
    assert assessment.minimum_advantage_dollars is None
    assert assessment.uncertainty_treatment is None

    expired_and_unratified = plan(expiry=T0 - timedelta(hours=1))
    also_unbound = assess_rotation_switch(expired_and_unratified, now=T0)
    assert (
        also_unbound.abstention_reason is PortfolioPlanAbstentionReason.POLICY_NOT_BOUND
    )


def test_ac_005_a_stale_or_expired_plan_abstains() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: a superseded or expired plan cannot switch.

    GIVEN a plan revision that is superseded, unestablished or past its expiry
    WHEN the switch gate is evaluated
    THEN it abstains with `PLAN_NOT_CURRENT` or `PLAN_EXPIRED` instead of the plan
    being assumed live, and the current, unexpired plan is still evaluated on its
    economics
    """

    record, _ = rotation_plan()

    stale = assess_rotation_switch(
        record,
        now=T0 + timedelta(hours=1),
        current_plan_id=f"{PORTFOLIO_PLAN_ID_PREFIX}:{'0' * 32}",
    )
    assert stale.status is RotationSwitchStatus.ABSTAINED
    assert stale.abstention_reason is PortfolioPlanAbstentionReason.PLAN_NOT_CURRENT
    assert stale.advantage_dollars is None

    expired, _ = rotation_plan(expiry=T0 + timedelta(hours=1))
    late = assess_rotation_switch(
        expired, now=T0 + timedelta(hours=2), current_plan_id=expired.plan_id
    )
    assert late.abstention_reason is PortfolioPlanAbstentionReason.PLAN_EXPIRED

    # An unestablished current revision is not the same as a current one: a caller
    # that cannot name the plan it believes is current gets the same fail-closed
    # abstention a superseded revision gets, instead of the plan being assumed live.
    unnamed = assess_rotation_switch(record, now=T0 + timedelta(hours=1))
    assert unnamed.status is RotationSwitchStatus.ABSTAINED
    assert unnamed.abstention_reason is PortfolioPlanAbstentionReason.PLAN_NOT_CURRENT
    assert unnamed.advantage_dollars is None

    current = assess_rotation_switch(
        record, now=T0 + timedelta(hours=1), current_plan_id=record.plan_id
    )
    assert current.status is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN


def test_ac_005_the_abstention_precedence_is_closed_and_ordered() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: the gap is never ambiguous.

    GIVEN a plan that fails several gates at once
    WHEN the gate reports its outcome
    THEN the frozen precedence decides which single reason is reported
    """

    assert PORTFOLIO_PLAN_ABSTENTION_PRECEDENCE == (
        "POLICY_NOT_BOUND",
        "PLAN_NOT_CURRENT",
        "PLAN_EXPIRED",
        "CASH_COMPARISON_UNAVAILABLE",
        "COOLDOWN_ACTIVE",
        "TURNOVER_BUDGET_EXHAUSTED",
        "UNCERTAINTY_BAND_UNCLEAR",
        "ADVANTAGE_BELOW_MINIMUM",
    )

    long_cooldown, _ = rotation_plan(
        constraints=scenario_constraints(cooldown_hours=24.0),
        expiry=T0 + timedelta(hours=3),
    )
    expired_wins = assess_rotation_switch(
        long_cooldown,
        now=T0 + timedelta(hours=4),
        current_plan_id=long_cooldown.plan_id,
        seconds_since_last_switch=0.0,
        turn_over_committed_dollars=10_000.0,
    )
    assert expired_wins.abstention_reason is PortfolioPlanAbstentionReason.PLAN_EXPIRED

    no_comparison = plan(constraints=scenario_constraints(cooldown_hours=24.0))
    comparison_wins = assess_rotation_switch(
        no_comparison,
        now=T0 + timedelta(hours=1),
        current_plan_id=no_comparison.plan_id,
        seconds_since_last_switch=0.0,
        turn_over_committed_dollars=10_000.0,
    )
    assert (
        comparison_wins.abstention_reason
        is PortfolioPlanAbstentionReason.CASH_COMPARISON_UNAVAILABLE
    )

    cooldown_wins = assess_rotation_switch(
        long_cooldown,
        now=T0 + timedelta(hours=1),
        current_plan_id=long_cooldown.plan_id,
        seconds_since_last_switch=0.0,
        turn_over_committed_dollars=10_000.0,
    )
    assert cooldown_wins.abstention_reason is PortfolioPlanAbstentionReason.COOLDOWN_ACTIVE

    # A ROTATE step may only be declared when the plan's own recorded evidence
    # already admits the switch, so the decision-time branches are proved on a plan
    # that records the comparison and declares no switch. A hypothetical switch
    # carries no capital of its own, so the caller declares what it would turn over.
    turnover, _ = third_leg_plan(
        constraints=band_constraints(turnover_budget_dollars=1.0)
    )
    turnover_wins = assess_rotation_switch(
        turnover,
        now=T0 + timedelta(hours=1),
        current_plan_id=turnover.plan_id,
        turn_over_committed_dollars=10_000.0,
        switched_capital_dollars=RATIFIED_SCENARIO_SWITCH_CAPITAL,
    )
    assert (
        turnover_wins.abstention_reason
        is PortfolioPlanAbstentionReason.TURNOVER_BUDGET_EXHAUSTED
    )

    band_plan, _ = third_leg_plan(constraints=band_constraints())
    band_wins = assess_rotation_switch(
        band_plan,
        now=T0 + timedelta(hours=1),
        current_plan_id=band_plan.plan_id,
        switched_capital_dollars=RATIFIED_SCENARIO_SWITCH_CAPITAL,
    )
    assert (
        band_wins.abstention_reason
        is PortfolioPlanAbstentionReason.UNCERTAINTY_BAND_UNCLEAR
    )


def test_ac_005_a_proven_advantage_records_the_policy_that_licensed_it() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: no advantage without its policy.

    GIVEN a plan whose comparison is evaluated under a ratified scenario policy
    WHEN the outcome is recorded
    THEN a proven advantage carries the advantage together with the ratified
    minimum and uncertainty treatment, while the conservative treatment refuses
    the same gross upside
    """

    record, _ = rotation_plan()
    assessment = assess_rotation_switch(
        record, now=T0 + timedelta(hours=1), current_plan_id=record.plan_id
    )
    assert assessment.status is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
    assert assessment.abstention_reason is None
    assert assessment.advantage_dollars == 21.0
    assert assessment.minimum_advantage_dollars == RATIFIED_SCENARIO_ADVANTAGE
    assert assessment.uncertainty_treatment is UncertaintyTreatment.EXPECTED
    assert assessment.assessment_id.startswith(
        f"{PORTFOLIO_SWITCH_ASSESSMENT_ID_PREFIX}:"
    )
    assert RotationSwitchAssessment.from_dict(assessment.to_dict()) == assessment

    with pytest.raises(PortfolioContractError, match="proven advantage"):
        RotationSwitchAssessment(
            plan_id=record.plan_id,
            window_identity=record.window_identity,
            status=RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN,
            decided_at=T0,
            advantage_dollars=21.0,
        )
    with pytest.raises(PortfolioContractError, match="abstention reason"):
        RotationSwitchAssessment(
            plan_id=record.plan_id,
            window_identity=record.window_identity,
            status=RotationSwitchStatus.ABSTAINED,
            decided_at=T0,
        )

    banded_cash = cash_leg(band=(84.0, 104.0))
    banded_hold = hold_leg(band=(90.0, 110.0))
    banded_replacement = replacement_leg(cash=banded_cash, entry_band=(-10.0, 30.0))
    banded_legs = (banded_hold, banded_cash, banded_replacement)
    banded_comparison = comparison(
        hold=banded_hold, cash=banded_cash, replacement=banded_replacement
    )

    conservative = plan(
        alternatives=banded_legs,
        action=hold_action(),
        comparisons=(banded_comparison,),
        constraints=band_constraints(),
    )
    conservative_assessment = assess_rotation_switch(
        conservative,
        now=T0 + timedelta(hours=1),
        current_plan_id=conservative.plan_id,
        switched_capital_dollars=RATIFIED_SCENARIO_SWITCH_CAPITAL,
    )
    assert conservative_assessment.status is RotationSwitchStatus.ABSTAINED
    assert (
        conservative_assessment.abstention_reason
        is PortfolioPlanAbstentionReason.ADVANTAGE_BELOW_MINIMUM
    )
    assert conservative_assessment.advantage_dollars is None

    expected = plan(
        alternatives=banded_legs,
        action=rotate_action(banded_replacement.alternative_id),
        comparisons=(banded_comparison,),
        constraints=scenario_constraints(),
    )
    expected_assessment = assess_rotation_switch(
        expected, now=T0 + timedelta(hours=1), current_plan_id=expected.plan_id
    )
    assert expected_assessment.status is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
    assert expected_assessment.advantage_dollars == 21.0
    assert expected_assessment.uncertainty_treatment is UncertaintyTreatment.EXPECTED


def test_ac_005_a_caller_cannot_substitute_a_more_favourable_comparison() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: the plan's own comparison decides.

    GIVEN a plan whose recorded comparison proves a modest, admitted switch
    WHEN a caller supplies a comparison the plan never recorded that carries much
    more favourable replacement economics
    THEN the plan's own recorded comparison is the one evaluated: the unrecorded
    comparison abstains with `CASH_COMPARISON_UNAVAILABLE` instead of buying an
    advantage the plan never preregistered, while the recorded comparison still
    proves exactly the recorded advantage
    """

    record, recorded = rotation_plan()
    assert record.comparisons == (recorded,)

    generous_cash = cash_leg()
    generous_replacement = replacement_leg(cash=generous_cash, entry_gross=5_000.0)
    substituted = comparison(cash=generous_cash, replacement=generous_replacement)
    assert substituted.comparison_id != recorded.comparison_id
    assert (
        substituted.expected_advantage_over_best_alternative
        > recorded.expected_advantage_over_best_alternative
    )

    smuggled = record.assess_rotation_switch(
        now=T0 + timedelta(hours=1),
        current_plan_id=record.plan_id,
        comparison=substituted,
    )
    assert smuggled.status is RotationSwitchStatus.ABSTAINED
    assert (
        smuggled.abstention_reason
        is PortfolioPlanAbstentionReason.CASH_COMPARISON_UNAVAILABLE
    )
    assert smuggled.advantage_dollars is None

    owned = record.assess_rotation_switch(
        now=T0 + timedelta(hours=1),
        current_plan_id=record.plan_id,
        comparison=recorded,
    )
    assert owned.status is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
    assert owned.advantage_dollars == 21.0


def test_ac_005_the_turnover_budget_is_charged_in_the_capital_a_switch_moves() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: the budget bounds capital, not fees.

    GIVEN an admitted rotation whose declared capital and round-trip cost are both
    known
    WHEN the committed turnover and the switch's own capital are charged together
    THEN the exhaustion boundary is the capital the switch moves: a caller cannot
    understate it and buy the switch back, while a hypothetical switch that declares
    less capital than the round trip it already commits cannot look cheaper than
    those costs
    """

    record, recorded = rotation_plan()
    capital = record.rotation_actions[0].capital_delta
    round_trip = recorded.replacement.round_trip_cost
    budget = float(record.constraints.turnover_budget_dollars)
    assert capital == RATIFIED_SCENARIO_SWITCH_CAPITAL
    assert 0.0 < round_trip < capital < budget

    # The committed turnover that would still fit if only the round trip were
    # charged exhausts the budget once the capital the switch moves is charged too.
    committed = budget - capital + round_trip
    exhausted = record.assess_rotation_switch(
        now=T0 + timedelta(hours=1),
        current_plan_id=record.plan_id,
        turn_over_committed_dollars=committed,
    )
    assert exhausted.status is RotationSwitchStatus.ABSTAINED
    assert (
        exhausted.abstention_reason
        is PortfolioPlanAbstentionReason.TURNOVER_BUDGET_EXHAUSTED
    )
    assert exhausted.advantage_dollars is None

    # Declaring less capital than the plan's own rotation record states cannot buy
    # the switch back: the plan's own declaration is the floor.
    understated = record.assess_rotation_switch(
        now=T0 + timedelta(hours=1),
        current_plan_id=record.plan_id,
        turn_over_committed_dollars=committed,
        switched_capital_dollars=1.0,
    )
    assert (
        understated.abstention_reason
        is PortfolioPlanAbstentionReason.TURNOVER_BUDGET_EXHAUSTED
    )

    # The boundary is inclusive: turnover plus the declared capital up to the budget
    # still admits the switch.
    exact = record.assess_rotation_switch(
        now=T0 + timedelta(hours=1),
        current_plan_id=record.plan_id,
        turn_over_committed_dollars=budget - capital,
    )
    assert exact.status is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
    assert exact.advantage_dollars == 21.0

    # A hypothetical switch the plan does not declare carries no capital of its own,
    # so the caller declares it, and the round trip the switch already commits is the
    # floor under that declaration.
    hypothetical, hypothetical_comparison = third_leg_plan(
        constraints=scenario_constraints(turnover_budget_dollars=round_trip / 2.0)
    )
    assert hypothetical_comparison.replacement.round_trip_cost == round_trip
    assert hypothetical_comparison.expected_advantage_over_best_alternative == 21.0
    floored = assess_rotation_switch(
        hypothetical,
        now=T0 + timedelta(hours=1),
        current_plan_id=hypothetical.plan_id,
        switched_capital_dollars=1.0,
    )
    assert (
        floored.abstention_reason
        is PortfolioPlanAbstentionReason.TURNOVER_BUDGET_EXHAUSTED
    )

    admitted, _ = third_leg_plan(
        constraints=scenario_constraints(turnover_budget_dollars=round_trip)
    )
    admitted_assessment = assess_rotation_switch(
        admitted,
        now=T0 + timedelta(hours=1),
        current_plan_id=admitted.plan_id,
        switched_capital_dollars=1.0,
    )
    assert admitted_assessment.status is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
    assert admitted_assessment.advantage_dollars == 21.0


def test_ac_005_an_unestablished_switch_capital_abstains_instead_of_being_assumed() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: unknown capital is not free capital.

    GIVEN a plan that records no switch of its own
    WHEN the gate is asked whether a hypothetical switch into the replacement would
    be licensed, with no capital declared for it
    THEN it abstains as `TURNOVER_BUDGET_EXHAUSTED` rather than pricing the
    hypothetical switch at its round trip, while a declared capital is evaluated on
    the declared figure
    """

    hypothetical, _ = third_leg_plan()

    unknown = assess_rotation_switch(
        hypothetical, now=T0 + timedelta(hours=1), current_plan_id=hypothetical.plan_id
    )
    assert unknown.status is RotationSwitchStatus.ABSTAINED
    assert (
        unknown.abstention_reason
        is PortfolioPlanAbstentionReason.TURNOVER_BUDGET_EXHAUSTED
    )
    assert unknown.advantage_dollars is None

    declared = assess_rotation_switch(
        hypothetical,
        now=T0 + timedelta(hours=1),
        current_plan_id=hypothetical.plan_id,
        switched_capital_dollars=RATIFIED_SCENARIO_SWITCH_CAPITAL,
    )
    assert declared.status is RotationSwitchStatus.ROTATION_ADVANTAGE_PROVEN
    assert declared.advantage_dollars == 21.0


def test_ac_005_the_discretionary_gate_never_delays_a_protective_exit() -> None:
    """ATDD-R5-D-portfolio-planning/AC-005: protection is not gated by rotation.

    GIVEN the independent protection invariant and the discretionary switch gate
    WHEN the gate's surface is inspected
    THEN a protection trigger is still never itself an exit, the gate carries no
    veto, override or delay over protection, and an abstention authorizes nothing
    """

    assert protection_trigger_implies_exit() is False

    rotation_section = r5d_section(EXECUTION_MODULE, SC09_BANNER)
    plan_section = r5d_section(PORTFOLIO_MODULE, R5D_BANNER)
    for section in (rotation_section, plan_section):
        assert "protect" not in section.lower()
        for token in ("block_exit", "delay_exit", "override_protection", "suppress_exit"):
            assert token not in section, token
    for path in (PORTFOLIO_MODULE, EXECUTION_MODULE):
        assert not any("risk" in name for name in module_imports(path))

    abstained = assess_rotation_switch(plan(), now=T0 + timedelta(hours=1))
    assert abstained.is_abstained is True
    assert abstained.abstention_reason is PortfolioPlanAbstentionReason.POLICY_NOT_BOUND
    assert "order" not in abstained.to_dict()
    assert abstained.assessment_id.startswith(f"{PORTFOLIO_SWITCH_ASSESSMENT_ID_PREFIX}:")


# ---------------------------------------------------------------------------
# AC-006 The linked-intent rotation-group record (RO-03, SC-09)
# ---------------------------------------------------------------------------


def test_ac_006_the_group_payload_carries_the_frozen_keys_in_order() -> None:
    """ATDD-R5-D-portfolio-planning/AC-006: the frozen `SC-09` record is complete.

    GIVEN the frozen linked-intent rotation-group record
    WHEN a group is recorded and exported to its durable form
    THEN its payload carries the eight frozen keys in order, its identity is the
    declared content hash, it binds to the `SC-08` plan and comparison, and the
    untouched payload round trips
    """

    assert ROTATION_GROUP_RECORD_KEYS == {
        "old position": ("old_position",),
        "replacement thesis": ("replacement_thesis",),
        "both-leg intent/fill IDs": ("legs",),
        "committed proceeds": ("committed_proceeds",),
        "requalification decision": ("requalification",),
        "residual exposure": ("residual_exposure",),
        "terminal state": ("terminal_state",),
        "reasons": ("reasons",),
    }

    record = group()
    payload = record.as_dict()
    assert tuple(payload) == ROTATION_GROUP_PAYLOAD_KEYS
    assert assert_rotation_group_record_keys(record) == ROTATION_GROUP_PAYLOAD_KEYS
    assert record.to_dict() == payload
    for frozen_key, durable_keys in ROTATION_GROUP_RECORD_KEYS.items():
        assert durable_keys, frozen_key
        for durable_key in durable_keys:
            assert durable_key in payload, (frozen_key, durable_key)

    assert record.group_id.startswith(f"{ROTATION_GROUP_ID_PREFIX}:")
    assert record.schema_version == ROTATION_GROUP_SCHEMA_VERSION
    assert record.plan_id.startswith(f"{PORTFOLIO_PLAN_ID_PREFIX}:")
    assert record.comparison_id.startswith(f"{PORTFOLIO_COMPARISON_ID_PREFIX}:")
    assert RotationGroup.from_dict(payload) == record


def test_ac_006_the_linked_legs_obey_the_two_leg_laws() -> None:
    """ATDD-R5-D-portfolio-planning/AC-006: two legs, one group, no third leg.

    GIVEN a rotation group and the leg records it links
    WHEN a leg breaks the declared linkage laws
    THEN the group is refused instead of recording an ambiguous move
    """

    record = group()
    assert record.exit_leg.leg is RotationLeg.EXIT
    assert record.entry_leg is not None
    assert record.entry_leg.leg is RotationLeg.ENTRY

    with pytest.raises(RotationGroupContractError, match="distinct intent ids"):
        group(legs=(exit_leg(), entry_leg(intent="PINT:R5D:EXIT:1")))
    with pytest.raises(RotationGroupContractError, match="no third leg"):
        group(legs=(exit_leg(), entry_leg(), exit_leg(intent="PINT:R5D:EXIT:2")))
    with pytest.raises(RotationGroupContractError, match="first leg"):
        group(legs=(entry_leg(), exit_leg()))
    with pytest.raises(RotationGroupContractError, match="policy-allowed entry"):
        RotationLegReference(
            leg=RotationLeg.EXIT,
            intent_id="PINT:R5D:EXIT:1",
            policy_forecast_id="PFCST:ENTRY:1",
        )
    with pytest.raises(RotationGroupContractError, match="policy_forecast_id"):
        RotationLegReference(leg=RotationLeg.ENTRY, intent_id="PINT:R5D:ENTRY:1")
    with pytest.raises(RotationGroupContractError, match="policy-allowed entries"):
        group(legs=(exit_leg(), entry_leg(policy="PFCST:ENTRY:2")))
    with pytest.raises(RotationGroupContractError, match="unsupported direction"):
        RotationOldPosition(
            subject_ref=SUBJECT, direction="SIDEWAYS", quantity=1.0, position_ref="PPOS:R5D:1"
        )
    with pytest.raises(RotationGroupContractError, match="one of the declared"):
        thesis(entry_policy="PFCST:ENTRY:9")
    with pytest.raises(RotationGroupContractError, match="rotation group's subject"):
        group(thesis_record=thesis_record_with_subject("POPT:R5D:OTHER"))


def test_ac_006_a_committed_fill_is_committed_by_one_leg_only() -> None:
    """ATDD-R5-D-portfolio-planning/AC-006: one fill, one leg, one move of capital.

    GIVEN a rotation group whose two legs each reference the fills they committed
    WHEN the exit's own committed fill is claimed by the entry leg as well, whether
    the group is built directly or replayed from its durable payload
    THEN the group is refused, because a fill is committed once and a payload that
    books it twice counts one move of capital as two
    """

    record = group()
    committed = [fill.fill_id for leg in record.legs for fill in leg.fills]
    assert committed == ["PFILL:R5D:EXIT:1", "PFILL:R5D:ENTRY:1"]
    assert len(set(committed)) == len(committed)

    shared = "PFILL:R5D:SHARED:1"
    ambiguous_exit = exit_leg(
        fills=(RotationLegFill(fill_id=shared, quantity=6.0, price=10.0, fee=1.0),)
    )
    ambiguous_entry = entry_leg(
        fills=(RotationLegFill(fill_id=shared, quantity=4.0, price=10.0, fee=0.5),)
    )
    with pytest.raises(RotationGroupContractError, match="both legs"):
        group(exit_record=ambiguous_exit, entry_record=ambiguous_entry)

    # The same forgery replayed from the durable payload is refused while the payload
    # is rebuilt, before the re-linked fill can be reasoned about as evidence.
    payload = dict(record.as_dict())
    legs = [dict(leg) for leg in payload["legs"]]
    legs[1]["fills"] = [
        dict(legs[1]["fills"][0], fill_id=legs[0]["fills"][0]["fill_id"])
    ]
    payload["legs"] = legs
    with pytest.raises(RotationGroupContractError, match="both legs"):
        RotationGroup.from_dict(payload)


def test_ac_006_a_partial_exit_funds_only_policy_allowed_entries() -> None:
    """ATDD-R5-D-portfolio-planning/AC-006: a partial exit funds only what it released.

    GIVEN an exit leg that committed a partial fill
    WHEN the proceeds are split into the entry commitment and released cash
    THEN the split equals the committed fill evidence exactly, an entry commitment
    beyond what the exit released is refused, and the residual exposure is counted
    """

    exit_record = exit_leg()
    assert exit_record.proceeds == 59.0

    proceeds = commit_rotation_proceeds(exit_record, committed_to_entry=40.5)
    assert proceeds.exit_proceeds == 59.0
    assert proceeds.committed_to_entry == 40.5
    assert proceeds.released_to_cash == 18.5

    with pytest.raises(RotationGroupContractError, match="proceeds the exit actually committed"):
        commit_rotation_proceeds(exit_record, committed_to_entry=59.01)
    with pytest.raises(RotationGroupContractError, match="exit leg's committed proceeds"):
        commit_rotation_proceeds(entry_leg(), committed_to_entry=1.0)

    record = group()
    assert record.committed_proceeds.exit_proceeds == 59.0
    assert record.committed_proceeds.committed_to_entry == 40.5
    assert record.committed_proceeds.released_to_cash == 18.5
    assert record.reuses_the_old_position is True
    assert record.residual_exposure.quantity == 4.0


def test_ac_006_the_group_is_dormant_and_non_authoritative() -> None:
    """ATDD-R5-D-portfolio-planning/AC-006: the record is evidence, not authority.

    GIVEN the rotation-group record and its declared dormancy
    WHEN the record is built and an authority claim is attempted
    THEN recording stays disabled, every record declares itself non-authoritative
    and an authoritative group is refused
    """

    assert ROTATION_GROUP_RECORDING_ENABLED is False

    record = group()
    assert record.authoritative is False
    assert record.as_dict()["authoritative"] is False
    assert record.as_dict()["schema_version"] == ROTATION_GROUP_SCHEMA_VERSION

    with pytest.raises(RotationGroupContractError, match="never authoritative"):
        replace(record, authoritative=True)
    with pytest.raises(RotationGroupContractError, match="schema version"):
        replace(record, schema_version="rotation-group-v2")
    with pytest.raises(RotationGroupContractError, match="SC-08 portfolio plan"):
        replace(record, plan_id="PINT:R5D:NOT-A-PLAN")
    with pytest.raises(RotationGroupContractError, match="preregistered SC-08 comparison"):
        replace(record, comparison_id="PINT:R5D:NOT-A-COMPARISON")


# ---------------------------------------------------------------------------
# AC-007 Partial fills, a failed second leg, replay and recovery (RO-03)
# ---------------------------------------------------------------------------


def test_ac_007_partial_fills_reconcile_proceeds_and_residual_exposure() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: the record reconciles to the fills.

    GIVEN an exit that only partially filled, in one fill and in several
    WHEN the committed proceeds and the residual exposure are inspected
    THEN they reconcile exactly to the committed fill evidence, and a residual or
    a proceeds figure that does not reconcile fails closed
    """

    record = group()
    assert record.exit_leg.filled_quantity == 6.0
    assert record.exit_leg.is_filled is True
    assert record.residual_exposure.quantity == 4.0
    assert record.residual_exposure.is_flat is False
    assert record.terminal_state is RotationTerminalState.ENTRY_COMMITTED
    assert record.entry_leg is not None and record.entry_leg.is_filled is True
    assert record.requalification.decision is RotationRequalificationDecision.PENDING

    multi_fill = exit_leg(
        fills=(
            RotationLegFill(
                fill_id="PFILL:R5D:EXIT:1", quantity=4.0, price=10.0, fee=0.5
            ),
            RotationLegFill(
                fill_id="PFILL:R5D:EXIT:2", quantity=2.0, price=10.0, fee=0.5
            ),
        )
    )
    multi_record = group(exit_record=multi_fill)
    assert multi_record.exit_leg.filled_quantity == 6.0
    assert multi_record.committed_proceeds.exit_proceeds == 59.0
    assert multi_record.residual_exposure.quantity == 4.0

    with pytest.raises(RotationGroupContractError, match="residual exposure must equal"):
        group(residual=1.0)
    with pytest.raises(RotationGroupContractError, match="must equal what the exit leg"):
        replace(
            record,
            committed_proceeds=RotationCommittedProceeds(
                quote_currency="USD",
                exit_proceeds=99.0,
                committed_to_entry=40.0,
                released_to_cash=59.0,
            ),
        )
    with pytest.raises(RotationGroupContractError, match="do not reconcile"):
        RotationCommittedProceeds(
            quote_currency="USD",
            exit_proceeds=59.0,
            committed_to_entry=40.0,
            released_to_cash=50.0,
        )
    with pytest.raises(RotationGroupContractError, match="PARTIAL_EXIT_RESIDUAL_RETAINED"):
        replace(
            record,
            reasons=(
                RotationGroupReason.EXIT_FILLS_COMMITTED,
                RotationGroupReason.ENTRY_FUNDED_BY_EXIT_PROCEEDS,
                RotationGroupReason.ENTRY_FILLS_COMMITTED,
                RotationGroupReason.PROCEEDS_RELEASED_TO_CASH,
            ),
        )


def test_ac_007_an_entry_leg_cannot_spend_more_than_was_committed_to_it() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: a rotation cannot invent capital.

    GIVEN an exit that released 59.00 and an entry whose committed fills cost 40.50
    of notional and fees
    WHEN the proceeds committed to the entry are below, exactly at and above what
    the entry actually committed
    THEN a commitment below the entry's own measured cost is refused, the exact
    boundary is admitted, and a multi-fill or partially filled entry is measured on
    what it really committed
    """

    exit_record = exit_leg()
    assert exit_record.proceeds == 59.0
    assert entry_leg().fills[0].gross_notional + entry_leg().fills[0].fee == 40.5

    with pytest.raises(RotationGroupContractError, match="cannot spend capital it never"):
        group(committed_to_entry=40.0)

    boundary = group(committed_to_entry=40.5)
    assert boundary.committed_proceeds.committed_to_entry == 40.5
    assert boundary.committed_proceeds.released_to_cash == 18.5
    assert boundary.entry_leg is not None
    assert boundary.entry_leg.filled_quantity == 4.0

    generous = group(committed_to_entry=55.0)
    assert generous.committed_proceeds.exit_proceeds == 59.0
    assert generous.committed_proceeds.released_to_cash == 4.0

    # A multi-fill entry is charged on the sum of what it committed, not on one fill:
    # 20.50 + 20.50 = 41.00, so the 40.50 commitment no longer covers it.
    split_entry = entry_leg(
        fills=(
            RotationLegFill(
                fill_id="PFILL:R5D:ENTRY:1", quantity=2.0, price=10.0, fee=0.5
            ),
            RotationLegFill(
                fill_id="PFILL:R5D:ENTRY:2", quantity=2.0, price=10.0, fee=0.5
            ),
        )
    )
    with pytest.raises(RotationGroupContractError, match="cannot spend capital it never"):
        group(entry_record=split_entry, committed_to_entry=40.5)
    multi = group(entry_record=split_entry, committed_to_entry=41.0)
    assert multi.entry_leg is not None
    assert multi.entry_leg.filled_quantity == 4.0

    # A partially filled replacement that spent less than the commitment is not an
    # overspend: the commitment is what was authorized, not a spending target.
    partial = group(
        entry_record=entry_leg(quantity=1.0), committed_to_entry=40.5
    )
    assert partial.committed_proceeds.committed_to_entry == 40.5

    # An oversized replacement, however self-consistent its identities are, cannot be
    # funded by the same committed proceeds.
    with pytest.raises(RotationGroupContractError, match="cannot spend capital it never"):
        group(entry_record=entry_leg(quantity=5.0), committed_to_entry=40.5)
    with pytest.raises(RotationGroupContractError, match="cannot spend capital it never"):
        group(entry_record=entry_leg(quantity=676.0), committed_to_entry=40.5)

    forged = boundary.as_dict()
    legs = [dict(leg) for leg in forged["legs"]]
    entry = dict(legs[1])
    entry["fills"] = [dict(entry["fills"][0], quantity=676.0)]
    legs[1] = entry
    forged["legs"] = legs
    with pytest.raises(RotationGroupContractError, match="cannot spend capital it never"):
        RotationGroup.from_dict(forged)


def test_ac_007_a_failed_or_expired_second_leg_retains_its_costs_and_reason() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: a failed second leg keeps its evidence.

    GIVEN an exit that committed and a second leg that never entered, and an exit
    that has not committed at all
    WHEN the group is recorded
    THEN the realized costs and the reason are retained, nothing is claimed that
    did not happen, and the unsold exposure stays protected
    """

    abandoned = group(
        exit_record=exit_leg(),
        with_entry=False,
        committed_to_entry=0.0,
        residual=4.0,
        terminal_state=RotationTerminalState.ABANDONED_TO_CASH,
        requalification=RotationRequalification(
            decision=RotationRequalificationDecision.NOT_REQUALIFIED,
            decided_at=T0,
            policy_forecast_id="PFCST:ENTRY:1",
        ),
        reasons=(
            RotationGroupReason.EXIT_FILLS_COMMITTED,
            RotationGroupReason.PARTIAL_EXIT_RESIDUAL_RETAINED,
            RotationGroupReason.PROCEEDS_RELEASED_TO_CASH,
            RotationGroupReason.REPLACEMENT_NOT_REQUALIFIED,
        ),
    )
    assert abandoned.exit_leg.proceeds == 59.0
    assert abandoned.committed_proceeds.exit_proceeds == 59.0
    assert abandoned.committed_proceeds.committed_to_entry == 0.0
    assert abandoned.committed_proceeds.released_to_cash == 59.0
    assert abandoned.entry_leg is None
    assert abandonment_reason_is_retained(abandoned) is True
    assert abandoned.requalification.is_decided is True
    assert abandoned.reuses_the_old_position is True

    with pytest.raises(RotationGroupContractError, match="REPLACEMENT_NOT_REQUALIFIED"):
        replace(
            abandoned,
            reasons=(
                RotationGroupReason.EXIT_FILLS_COMMITTED,
                RotationGroupReason.PARTIAL_EXIT_RESIDUAL_RETAINED,
                RotationGroupReason.PROCEEDS_RELEASED_TO_CASH,
            ),
        )
    with pytest.raises(RotationGroupContractError, match="only be funded by proceeds"):
        commit_rotation_proceeds(exit_leg(), committed_to_entry=59.01)

    awaiting = group(
        exit_record=exit_leg(fills=()),
        with_entry=False,
        committed_to_entry=0.0,
        residual=10.0,
        terminal_state=RotationTerminalState.AWAITING_EXIT_FILL,
        requalification=RotationRequalification(
            decision=RotationRequalificationDecision.PENDING
        ),
        reasons=(RotationGroupReason.SWITCH_ABSTAINED,),
    )
    assert awaiting.exit_leg.is_filled is False
    assert awaiting.committed_proceeds.exit_proceeds == 0.0
    assert awaiting.residual_exposure.quantity == 10.0
    assert awaiting.terminal_state is RotationTerminalState.AWAITING_EXIT_FILL
    assert awaiting.entry_leg is None


def test_ac_007_a_state_that_claims_an_unrequalified_entry_is_refused() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: a terminal state must tell one story.

    GIVEN the terminal states and the requalification decisions they admit
    WHEN a state claims an entry the record does not contain, or carries a
    requalification decision that cannot accompany it
    THEN the record is refused
    """

    requalified = RotationRequalification(
        decision=RotationRequalificationDecision.REQUALIFIED,
        decided_at=T0,
        policy_forecast_id="PFCST:ENTRY:1",
    )

    with pytest.raises(
        RotationGroupContractError, match="nothing may be released|COMPLETED"
    ):
        group(
            terminal_state=RotationTerminalState.COMPLETED,
            requalification=requalified,
        )

    completed = group(
        exit_record=exit_leg(quantity=10.0),
        committed_to_entry=99.0,
        residual=0.0,
        terminal_state=RotationTerminalState.COMPLETED,
        requalification=requalified,
        reasons=(
            RotationGroupReason.EXIT_FILLS_COMMITTED,
            RotationGroupReason.ENTRY_FUNDED_BY_EXIT_PROCEEDS,
            RotationGroupReason.ENTRY_FILLS_COMMITTED,
            RotationGroupReason.REPLACEMENT_REQUALIFIED,
        ),
    )
    assert completed.residual_exposure.is_flat is True
    assert completed.committed_proceeds.released_to_cash == 0.0
    assert completed.entry_leg is not None
    assert completed.terminal_state is RotationTerminalState.COMPLETED

    with pytest.raises(RotationGroupContractError, match="cannot accompany"):
        group(
            exit_record=exit_leg(fills=()),
            with_entry=False,
            committed_to_entry=0.0,
            residual=10.0,
            terminal_state=RotationTerminalState.AWAITING_EXIT_FILL,
            requalification=requalified,
            reasons=(RotationGroupReason.SWITCH_ABSTAINED,),
        )

    abstained = group(
        exit_record=exit_leg(fills=()),
        with_entry=False,
        committed_to_entry=0.0,
        residual=10.0,
        terminal_state=RotationTerminalState.ABSTAINED,
        requalification=RotationRequalification(
            decision=RotationRequalificationDecision.ABSTAINED
        ),
        reasons=(RotationGroupReason.SWITCH_ABSTAINED,),
    )
    assert abstained.is_abstained is True
    assert abstained.committed_proceeds.exit_proceeds == 0.0

    with pytest.raises(RotationGroupContractError, match="SWITCH_ABSTAINED"):
        replace(
            abstained,
            reasons=(RotationGroupReason.PROCEEDS_RELEASED_TO_CASH,),
        )

    assert set(TERMINAL_STATE_REQUALIFICATION_DECISIONS) == {
        state.value for state in RotationTerminalState
    }
    assert (
        rotation_requalification_is_admitted(
            RotationTerminalState.COMPLETED, RotationRequalificationDecision.REQUALIFIED
        )
        is True
    )
    assert (
        rotation_requalification_is_admitted(
            RotationTerminalState.COMPLETED, RotationRequalificationDecision.PENDING
        )
        is False
    )
    assert rotation_requalification_is_admitted("NOT_A_STATE", "NOT_A_DECISION") is False


def test_ac_007_a_replayed_or_forged_group_payload_fails_closed() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: recovery cannot repeat or invent a fill.

    GIVEN a durable rotation-group payload
    WHEN it is replayed, then mutated, forged, dropped or extended, and a fill id
    is repeated
    THEN the untouched payload is idempotent and every forgery is refused
    """

    record = group()
    payload = record.as_dict()
    assert RotationGroup.from_dict(payload) == record
    replayed = RotationGroup.from_dict(payload)
    assert RotationGroup.from_dict(replayed.as_dict()) == record

    forged = dict(payload)
    forged["group_id"] = f"{ROTATION_GROUP_ID_PREFIX}:{'0' * 32}"
    with pytest.raises(RotationGroupContractError, match="group_id"):
        RotationGroup.from_dict(forged)

    mutated = dict(payload)
    legs = [dict(leg) for leg in payload["legs"]]
    legs[0] = dict(legs[0])
    legs[0]["fills"] = [
        dict(payload["legs"][0]["fills"][0], quantity=9.0, fill_id="PFILL:R5D:REPLAY:1")
    ]
    mutated["legs"] = legs
    with pytest.raises(RotationGroupContractError, match="residual|proceeds"):
        RotationGroup.from_dict(mutated)

    dropped = dict(payload)
    dropped.pop("requalification")
    with pytest.raises(RotationGroupContractError, match="missing"):
        RotationGroup.from_dict(dropped)

    extended = dict(payload)
    extended["recovery_mode"] = "replay"
    with pytest.raises(RotationGroupContractError, match="unknown key"):
        RotationGroup.from_dict(extended)

    with pytest.raises(RotationGroupContractError, match="missing"):
        RotationGroup.from_dict({"rotation_group": payload})

    with pytest.raises(RotationGroupContractError, match="repeat a fill id"):
        exit_leg(
            fills=(
                RotationLegFill(
                    fill_id="PFILL:R5D:EXIT:1", quantity=3.0, price=10.0, fee=0.5
                ),
                RotationLegFill(
                    fill_id="PFILL:R5D:EXIT:1", quantity=3.0, price=10.0, fee=0.5
                ),
            )
        )


# ---------------------------------------------------------------------------
# AC-008 The cross-component deployable-capital boundary (SC-06/SC-07)
# ---------------------------------------------------------------------------


def test_ac_008_a_plan_cannot_consume_more_than_is_deployable() -> None:
    """ATDD-R5-D-portfolio-planning/AC-008: the deployable bound is the ceiling.

    GIVEN an existing capital book, its deployable bound under approved risk,
    concentration, liquidity and sleeve constraints, and a plan that declares a
    capital-consuming step
    WHEN the declared consumption is compared with the deployable capital
    THEN the tightest declared bound decides, and a consumption above it is not
    affordable while one at or below it is
    """

    record = capital_snapshot()
    view = deployable(record)
    assert view.status is CapitalDeployableStatus.AVAILABLE
    assert view.deployable_capital == Decimal("100.00")
    assert view.binding_bound is CapitalBound.RISK
    assert view.free_cash == Decimal("10000.00")

    plan_record, _ = rotation_plan()
    alternative_id = plan_record.rotation_actions[0].alternative_id
    # A changed action sequence is a different plan: the content identity must be
    # recomputed, so a silently re-pointed plan is refused.
    with pytest.raises(PortfolioContractError, match="plan_id"):
        replace(
            plan_record,
            action_sequence=(rotate_action(alternative_id, delta=150.0),),
        )
    consumes_too_much = replace(
        plan_record,
        action_sequence=(rotate_action(alternative_id, delta=150.0),),
        plan_id="",
    )
    affordable = replace(
        plan_record,
        action_sequence=(rotate_action(alternative_id, delta=100.0),),
        plan_id="",
    )
    assert consumes_too_much.plan_id != plan_record.plan_id
    assert consumes_too_much.rotation_actions[0].capital_delta > float(
        view.deployable_capital
    )
    assert affordable.rotation_actions[0].capital_delta <= float(view.deployable_capital)

    assert deployable(capital_snapshot(risk_used="0.00")).deployable_capital == Decimal(
        "1000.00"
    )
    cash_bound = deployable(capital_snapshot(free="50.00", risk_used="0.00"))
    assert cash_bound.deployable_capital == Decimal("50.00")
    assert cash_bound.binding_bound is CapitalBound.FREE_CASH

    sleeve_book = capital_snapshot(risk_used="0.00", sleeve_used="0.00")
    sleeve_bound = deployable(
        sleeve_book,
        policy=scenario_deployable_policy(
            sleeve_book, sleeve_limits={"TACTICAL": "60.00"}
        ),
    )
    assert sleeve_bound.deployable_capital == Decimal("60.00")
    assert sleeve_bound.binding_bound is CapitalBound.SLEEVE
    assert sleeve_bound.binding_sleeve == "TACTICAL"


def test_ac_008_the_commit_boundary_validates_identity_and_snapshot_version() -> None:
    """ATDD-R5-D-portfolio-planning/AC-008: the commit boundary is the gate.

    GIVEN a reservation plan built against a capital snapshot version
    WHEN it is validated and committed, and a stale or over-consuming competitor
    arrives
    THEN the current plan commits, while a superseded or unaffordable one is
    refused without a partial write
    """

    record = capital_snapshot(account="PAPER-LEDGER-R5D-COMMIT")
    plan_record, _ = rotation_plan()
    reservation = reservation_plan(record, amount=100.0)

    assert reservation.portfolio_version == record.portfolio_version
    assert reservation.plan_schema_version  # declared by the existing owner
    assert_reservation_plan_current(
        reservation, current_portfolio_version=record.portfolio_version
    )
    with pytest.raises(PortfolioContractError, match="stale"):
        assert_reservation_plan_current(
            reservation, current_portfolio_version="pv-r5d-0002"
        )

    commit = commit_reservation(
        record,
        plan=reservation,
        at_time=T0 + timedelta(seconds=60),
        expected_portfolio_version=record.portfolio_version,
    )
    assert commit.status is CapitalCommitStatus.COMMITTED
    assert commit.applied_portfolio_version == record.portfolio_version
    assert commit.committed_capital == Decimal("100.00")
    assert commit.plan_id == reservation.plan_id
    assert commit.snapshot.portfolio_version == commit.successor_version
    assert plan_record.authoritative is False

    stale_book = capital_snapshot(account="PAPER-LEDGER-R5D-STALE")
    stale = reservation_plan(stale_book, amount=25.0, portfolio_version="pv-r5d-0000")
    stale_commit = commit_reservation(
        stale_book,
        plan=stale,
        at_time=T0 + timedelta(seconds=60),
        expected_portfolio_version=stale_book.portfolio_version,
    )
    assert stale_commit.status is CapitalCommitStatus.REJECTED
    assert stale_commit.rejection is CapitalCommitRejection.STALE_EXPECTED_VERSION
    assert stale_commit.snapshot is None

    over_book = capital_snapshot(account="PAPER-LEDGER-R5D-OVER", free="500.00")
    over = reservation_plan(over_book, amount=20_000.0)
    over_commit = commit_reservation(
        over_book,
        plan=over,
        at_time=T0 + timedelta(seconds=60),
        expected_portfolio_version=over_book.portfolio_version,
    )
    assert over_commit.status is CapitalCommitStatus.REJECTED
    assert over_commit.rejection is CapitalCommitRejection.INSUFFICIENT_FREE_CASH
    assert over_commit.snapshot is None


def test_ac_008_an_unratified_policy_fails_closed_and_records_the_dependency() -> None:
    """ATDD-R5-D-portfolio-planning/AC-008: no invented limit, no silent default.

    GIVEN the shipped deployable policy, which ratifies no cap, and a policy that
    binds the book but still ratifies no limit
    WHEN the deployable bound is requested
    THEN no number is produced, the missing input is named, and the increment
    records the owner dependency instead of assuming unlimited capital
    """

    record = capital_snapshot(account="PAPER-LEDGER-R5D-UNRATIFIED")
    shipped = evaluate_deployable_capital(
        record,
        policy=capital_deployable_engineering_scenario_policy(),
        at_time=T0 + timedelta(seconds=60),
    )
    assert shipped.status is CapitalDeployableStatus.ABSTAINED
    assert shipped.deployable_capital is None
    assert shipped.abstention_reason is CapitalDeployableAbstentionReason.IDENTITY_NOT_BOUND
    assert shipped.missing_policy_inputs == (
        "account",
        "environment",
        "engine",
        "currency",
    )

    bound_but_capless = evaluate_deployable_capital(
        record,
        policy=capital_deployable_engineering_scenario_policy(
            account=record.account,
            environment=CapitalEnvironment.PAPER_LEDGER,
            engine=record.engine,
            currency=record.currency,
        ),
        at_time=T0 + timedelta(seconds=60),
    )
    assert bound_but_capless.status is CapitalDeployableStatus.ABSTAINED
    assert bound_but_capless.deployable_capital is None
    assert (
        bound_but_capless.abstention_reason
        is CapitalDeployableAbstentionReason.LIMIT_NOT_CONFIGURED
    )

    assert bound_but_capless.over_limit_bounds == ()
    reasons = {member.value for member in CapitalDeployableAbstentionReason}
    assert {
        "LIMIT_NOT_CONFIGURED",
        "SLEEVE_LIMIT_NOT_CONFIGURED",
        "LIMIT_USAGE_NOT_DECLARED",
        "UNRESOLVED_NEGATIVE_BALANCE",
    } <= reasons

    # The plan cannot be proven affordable while the bound is unprovable, so the
    # increment records the dependency rather than defaulting a cap.
    contract_text = CONTRACT.read_text(encoding="utf-8")
    for token in ("OQ-05", "OQ-06", "R5-E", "engineering-scenario"):
        assert token in contract_text, token
    record_text = module_source(PORTFOLIO_MODULE)
    assert "no cap is ratified" in record_text or "OWNER_DECISION_REQUIRED" in record_text


# ---------------------------------------------------------------------------
# AC-009 The increment boundary: governance, isolation and posture
# ---------------------------------------------------------------------------


def test_ac_009_the_increment_scope_contract_is_self_consistent() -> None:
    """ATDD-R5-D-portfolio-planning/AC-009: the contract traces to its tests.

    GIVEN this increment, its scope contract and the active pointer
    WHEN the contract is parsed and checked against the acceptance index
    THEN the pointer resolves to an existing scope contract (the CI scope job
    owns exact current-increment enforcement), every criterion is traced to a
    matching acceptance test, every criterion is mapped to a changed file, and
    this module pins no global pointer value
    """

    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    ours = [contract for contract in contracts if contract.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert CONTRACT.name == f"{INCREMENT}.md"
    assert contract.sections["INCREMENT"] == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"

    # The pointer is mutable orchestration state: it must name an existing
    # contract, and the life-cycle-safe assertion never pins its value.
    pointer_name = read_increment_pointer(POINTER)
    assert pointer_name in {item.increment for item in contracts}

    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id
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

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons
    assert global_pointer_pin_violations(module_source(Path(__file__))) == []


def test_ac_009_the_owning_modules_gain_no_authority_or_storage_surface() -> None:
    """ATDD-R5-D-portfolio-planning/AC-009: no authority and no second source.

    GIVEN the two extended owning modules
    WHEN the R5-D sections and their imports are inspected
    THEN they carry no order, execution, storage, scheduler, Telegram or
    environment surface, read no clock and no randomness, import no network or
    venue primitive, and no parallel plan or rotation store was created
    """

    for path, banner in ((PORTFOLIO_MODULE, R5D_BANNER), (EXECUTION_MODULE, SC09_BANNER)):
        section = r5d_section(path, banner)
        assert section
        for token in FORBIDDEN_AUTHORITY_TOKENS:
            assert token not in section, (path.name, token)
        for token in FORBIDDEN_PERSISTENCE_TOKENS:
            assert token not in section, (path.name, token)
        for token in ("environ", "utcnow", "datetime.now", "time.time", "open(", "uuid"):
            assert token not in section, (path.name, token)

    for path in (PORTFOLIO_MODULE, EXECUTION_MODULE):
        assert module_imports(path) & FORBIDDEN_PRIMITIVES == set()

    contracts_dir = APP_ROOT / "app" / "opip" / "contracts"
    for parallel in (
        "portfolio_plan.py",
        "rotation_group.py",
        "plan_store.py",
        "reservation_store.py",
        "allocator.py",
    ):
        assert not (contracts_dir / parallel).exists(), parallel


def test_ac_009_no_runtime_root_consumes_the_plan_or_rotation_records() -> None:
    """ATDD-R5-D-portfolio-planning/AC-009: dormancy is proved, not assumed.

    GIVEN the trading, discovery, decision and risk runtime roots and the rest of
    the application package
    WHEN references to the plan and rotation-group records are searched for
    THEN nothing outside the two owning modules consumes them, so the increment
    has no runtime consumer and no admission path
    """

    for root in RUNTIME_ROOTS:
        for path in sorted(root.rglob("*.py")):
            source = module_source(path)
            assert "PortfolioPlan" not in source, path
            assert "RotationGroup" not in source, path

    consumers = []
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if path in (PORTFOLIO_MODULE, EXECUTION_MODULE):
            continue
        source = module_source(path)
        if "PortfolioPlan" in source or "RotationGroup" in source:
            consumers.append(path.relative_to(APP_ROOT).as_posix())
    assert consumers == []


def test_ac_009_release_profile_and_non_authority_posture_are_unchanged() -> None:
    """ATDD-R5-D-portfolio-planning/AC-009: the posture is preserved.

    GIVEN the repository release-profile contract and the Compose literals
    WHEN they are inspected
    THEN `EVIDENCE_SHADOW` remains the active shadow profile with `TARGET_PAPER`
    blocked, paper-v2 stays off, this increment adds no mode key, and every R5-D
    record keeps declaring itself non-authoritative
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

    assert ROTATION_GROUP_RECORDING_ENABLED is False
    assert plan().authoritative is False
    assert group().authoritative is False
    assert capital_module.CAPITAL_RESERVATION_DURABILITY_STATUS == (
        "PROCESS_SCOPED_IN_MEMORY_WITNESS"
    )






























# ---------------------------------------------------------------------------
# F-007 remediation (AC-007): the frozen SC-09 payload order is enforced
# ---------------------------------------------------------------------------


def test_f007_the_frozen_payload_order_is_accepted_and_a_reorder_is_refused() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: the durable payload must carry the frozen SC-09 keys in order."""

    record = group()
    payload = record.as_dict()
    assert tuple(payload) == ROTATION_GROUP_PAYLOAD_KEYS
    assert RotationGroup.from_dict(payload) == record

    keys = list(ROTATION_GROUP_PAYLOAD_KEYS)
    swapped = {key: payload[key] for key in ([keys[1], keys[0]] + keys[2:])}
    assert list(swapped) != keys
    with pytest.raises(RotationGroupContractError, match="order"):
        RotationGroup.from_dict(swapped)

    reversed_payload = {key: payload[key] for key in reversed(keys)}
    with pytest.raises(RotationGroupContractError, match="order"):
        RotationGroup.from_dict(reversed_payload)

    rotated = {key: payload[key] for key in (keys[1:] + keys[:1])}
    with pytest.raises(RotationGroupContractError, match="order"):
        RotationGroup.from_dict(rotated)


def test_f007_reordering_cannot_bypass_the_frozen_keys_or_identity() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: a reorder neither hides a missing key nor forges an identity."""

    record = group()
    payload = record.as_dict()
    keys = list(ROTATION_GROUP_PAYLOAD_KEYS)

    missing = {key: payload[key] for key in keys if key != "reasons"}
    with pytest.raises(RotationGroupContractError, match="missing"):
        RotationGroup.from_dict(missing)

    extra = dict(payload)
    extra["extra_key"] = 1
    with pytest.raises(RotationGroupContractError, match="unknown key"):
        RotationGroup.from_dict(extra)

    renamed = {("reasons_" if key == "reasons" else key): payload[key] for key in keys}
    with pytest.raises(RotationGroupContractError, match="missing"):
        RotationGroup.from_dict(renamed)

    forged = dict(payload)
    forged["group_id"] = f"{ROTATION_GROUP_ID_PREFIX}:{'0' * 32}"
    with pytest.raises(RotationGroupContractError, match="content"):
        RotationGroup.from_dict(forged)


def test_f007_reordering_preserves_existing_refusals() -> None:
    """ATDD-R5-D-portfolio-planning/AC-007: the ordering guard does not displace the existing refusals."""

    record = group()
    payload = dict(record.as_dict())
    legs = [dict(leg) for leg in payload["legs"]]
    legs[1]["fills"] = [dict(legs[1]["fills"][0], fill_id=legs[0]["fills"][0]["fill_id"])]
    payload["legs"] = legs
    with pytest.raises(RotationGroupContractError, match="both legs"):
        RotationGroup.from_dict(payload)

    with pytest.raises(RotationGroupContractError, match="cannot spend capital it never"):
        group(entry_record=entry_leg(quantity=676.0), committed_to_entry=40.5)

# ---------------------------------------------------------------------------
# F-003 remediation (AC-003): completeness is a durable-record invariant
# ---------------------------------------------------------------------------


def _ac003_fixture():
    cash_record = cash_leg()
    hold_record = hold_leg()
    replacement = replacement_leg(cash=cash_record)
    legs = (hold_record, cash_record, replacement)
    record = comparison(hold=hold_record, cash=cash_record, replacement=replacement)
    return legs, record, hold_record, cash_record, replacement


def test_f003_a_durable_plan_missing_a_passed_over_rejection_is_refused() -> None:
    """ATDD-R5-D-portfolio-planning/AC-003: completeness is a durable-record invariant, not an optional call."""

    legs, record, hold_record, cash_record, replacement = _ac003_fixture()
    incomplete = plan(
        alternatives=legs,
        action=rotate_action(replacement.alternative_id),
        comparisons=(record,),
        constraints=scenario_constraints(),
        rejected=(
            PortfolioPlanRejection(
                alternative_id=hold_record.alternative_id,
                reason_code="LOWER_EXPECTED_NET",
            ),
        ),
    )
    with pytest.raises(PortfolioContractError, match="exactly once"):
        incomplete.assert_rejections_are_complete()
    with pytest.raises(PortfolioContractError, match="exactly once"):
        PortfolioPlan.from_dict(incomplete.to_dict())


def test_f003_an_invalid_rejection_cannot_be_loaded_as_a_durable_plan() -> None:
    """ATDD-R5-D-portfolio-planning/AC-003: a complete record loads; duplicates, selected and uncompared stay refused."""

    legs, record, hold_record, cash_record, replacement = _ac003_fixture()
    complete = plan(
        alternatives=legs,
        action=rotate_action(replacement.alternative_id),
        comparisons=(record,),
        constraints=scenario_constraints(),
    )
    complete.assert_rejections_are_complete()
    assert PortfolioPlan.from_dict(complete.to_dict()) == complete

    with pytest.raises(PortfolioContractError, match="unnamed token"):
        PortfolioPlanRejection(
            alternative_id=hold_record.alternative_id, reason_code="BECAUSE_I_SAID_SO"
        )

    with pytest.raises(PortfolioContractError, match="exactly once"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id),
            comparisons=(record,),
            constraints=scenario_constraints(),
            rejected=(
                PortfolioPlanRejection(
                    alternative_id=hold_record.alternative_id,
                    reason_code="LOWER_EXPECTED_NET",
                ),
                PortfolioPlanRejection(
                    alternative_id=hold_record.alternative_id,
                    reason_code="EVIDENCE_INCOMPLETE",
                ),
            ),
        )

    with pytest.raises(PortfolioContractError, match="actually compared"):
        plan(
            alternatives=legs,
            action=rotate_action(replacement.alternative_id),
            comparisons=(record,),
            constraints=scenario_constraints(),
            rejected=(
                PortfolioPlanRejection(
                    alternative_id="PALT:R5D:uncompared",
                    reason_code="LOWER_EXPECTED_NET",
                ),
            ),
        )