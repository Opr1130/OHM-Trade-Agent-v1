"""R3 F7 constrained economic / portfolio selector: acceptance and unit tests.

These tests satisfy the increment
``ATDD-R3-F7-economic-portfolio-selector``. Every acceptance test cites
``ATDD-R3-F7-economic-portfolio-selector/AC-NNN`` so the increment traceability
holds. No acceptance test is skipped.

Every fixture is a deterministic literal built from real repository types (the F3
detector claim, the F4 ``OpportunityEpisode``, the F5 ``FeasibilityDecision`` and
the F6 ``ForecastDecision``). The tests read no network, no wall clock and no
randomness, and they assert the pure selector does the same.

The synthetic ``SYNTHETIC_TEST_ONLY`` model is an unmistakable test fixture: it
exists only to exercise the selection mechanics. It is never evidence of
calibration, the production registry stays empty, and a zero-model environment is
asserted to abstain.
"""

from __future__ import annotations

import ast
import builtins
import socket
import sys
import types
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip import feasibility as fseam  # noqa: E402
from app.opip import forecast as fengine  # noqa: E402
from app.opip import opportunity_lifecycle as lifecycle  # noqa: E402
from app.opip.contracts import (  # noqa: E402
    DistributionKind,
    FeasibilityPolicy,
    ForecastContractError,
    ForecastFeatureValue,
    ForecastHorizon,
    ForecastInputVector,
    ForecastModelArtifact,
    ForecastModelKind,
    ForecastModelOutput,
    ForecastModelStatus,
    ForecastPolicy,
    ForecastUncertainty,
    OpportunityLifecyclePolicy,
    ProbabilityDistribution,
)
from app.opip.contracts.detector import (  # noqa: E402
    IGNITION_DETECTOR_VERSION,
    IGNITION_POLICY_VERSION,
    DetectorClaim,
)
from app.opip.contracts.portfolio import (  # noqa: E402
    PORTFOLIO_DECISION_ID_PREFIX,
    PORTFOLIO_DECISION_SCHEMA_VERSION,
    PORTFOLIO_EVALUATION_VERSION,
    PORTFOLIO_POLICY_VERSION,
    PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION,
    PORTFOLIO_SELECTOR_VERSION,
    LegacyComparatorObservables,
    PortfolioAbstentionReason,
    PortfolioAllocation,
    PortfolioCandidate,
    PortfolioCapitalState,
    PortfolioCashReason,
    PortfolioContractError,
    PortfolioDecision,
    PortfolioDirection,
    PortfolioEvaluationWindow,
    PortfolioEvidenceStatus,
    PortfolioExposureSnapshot,
    PortfolioPolicy,
    PortfolioReservation,
    PortfolioReservationPlan,
    PortfolioStatus,
    ReservationReleaseReason,
    ReservationStatus,
    adjust_reservation_for_fill,
    assert_reservation_plan_current,
    expire_reservations,
    expected_fill_probability,
    portfolio_panel_fingerprint,
    release_reservation,
)
from app.opip.portfolio_comparator import (  # noqa: E402
    LEGACY_MAX_CANDIDATES,
    LEGACY_MIN_TECHNICAL_SCORE,
    LEGACY_PRODUCTION_CAPITAL_FRACTION,
    FrozenLegacyComparatorResult,
    LegacyActivePosition,
    build_frozen_legacy_comparison,
    build_portfolio_comparison,
    legacy_unrounded_total,
    observed_legacy_policy,
)
from app.opip.portfolio_selector import (  # noqa: E402
    MAX_SELECTOR_PANEL_SIZE,
    select_portfolio,
)
from app.scanner.execution_validation import (  # noqa: E402
    COMPLETE,
    FRESH,
    VALID as EXECUTION_VALID,
    ExecutionValidation,
)
from app.scanner.market_data_validation import (  # noqa: E402
    PASS as MARKET_PASS,
    MarketDataValidation,
)
from app.scanner.models import MarketSnapshot  # noqa: E402
from tests.test_atdd_scope_control import global_pointer_pin_violations  # noqa: E402

INCREMENT = "ATDD-R3-F7-economic-portfolio-selector"

UTC = timezone.utc
CUTOFF = datetime(2026, 9, 11, 15, 1, tzinfo=UTC)
EVAL = datetime(2026, 9, 11, 15, 5, tzinfo=UTC)
TRAIN = datetime(2026, 8, 1, tzinfo=UTC)
RELEASED = datetime(2026, 9, 1, tzinfo=UTC)
CALIBRATION_REPORT_ID = "FEVAL:r3f7-fixture-report"

HORIZON = ForecastHorizon(
    entry_deadline_seconds=300, path_horizon_seconds=3600, validity_seconds=3600
)


def horizon(validity_seconds: int = 3600) -> ForecastHorizon:
    return ForecastHorizon(
        entry_deadline_seconds=300,
        path_horizon_seconds=3600,
        validity_seconds=validity_seconds,
    )


WINDOW = PortfolioEvaluationWindow(
    start=EVAL, end=EVAL + timedelta(days=2)
)
FORECAST_POLICY = ForecastPolicy()
FEASIBILITY_POLICY = FeasibilityPolicy()
F4_POLICY = OpportunityLifecyclePolicy()

INCREMENT_PATH = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
ACTIVE_INCREMENT_PATH = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"

CONTRACT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "portfolio.py"
SELECTOR_PATH = APP_ROOT / "app" / "opip" / "portfolio_selector.py"
COMPARATOR_PATH = APP_ROOT / "app" / "opip" / "portfolio_comparator.py"
CONTRACTS_INIT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "__init__.py"
RUN_CYCLE_PATH = APP_ROOT / "app" / "jobs" / "run_cycle.py"
SCAN_PATH = APP_ROOT / "app" / "jobs" / "scan_opportunities.py"
COMPOSE_PATH = APP_ROOT / "docker-compose.yml"
PROFIT_RANKING_PATH = APP_ROOT / "app" / "services" / "profit_ranking.py"
PORTFOLIO_RISK_PATH = APP_ROOT / "app" / "services" / "portfolio_risk.py"
CANDIDATES_PATH = APP_ROOT / "app" / "scanner" / "candidates.py"
F6_ENGINE_PATH = APP_ROOT / "app" / "opip" / "forecast.py"

CONTRACT_SOURCE = CONTRACT_PATH.read_text(encoding="utf-8")
SELECTOR_SOURCE = SELECTOR_PATH.read_text(encoding="utf-8")
COMPARATOR_SOURCE = COMPARATOR_PATH.read_text(encoding="utf-8")
F7_SOURCES = (CONTRACT_SOURCE, SELECTOR_SOURCE, COMPARATOR_SOURCE)

SELECTOR_ALLOWED_IMPORT_PREFIXES = (
    "__future__",
    "app.opip.contracts",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "itertools",
    "math",
    "types",
    "typing",
)

FORBIDDEN_CALL_ATTRIBUTES = frozenset(
    {
        "now",
        "utcnow",
        "today",
        "time",
        "monotonic",
        "perf_counter",
        "uuid1",
        "uuid4",
        "getenv",
        "environ",
        "urandom",
        "randint",
        "open",
        "connect",
        "urlopen",
        "system",
        "popen",
    }
)

SCORE_PROMOTION_TOKENS = frozenset(
    {
        "confidence",
        "opportunity_score",
        "technical_quality_score",
        "tradeability_score",
        "explosion_potential_score",
        "committee_confidence",
        "ai_confidence",
    }
)

_UNSET = object()


# ---------------------------------------------------------------------------
# Deterministic fixtures built from real repository types
# ---------------------------------------------------------------------------


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def identifier_tokens(source: str) -> set[str]:
    tokens: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
    return tokens


def called_attributes(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


class ForbiddenIO:
    """Make filesystem and network access explode inside the with-block."""

    def __enter__(self) -> "ForbiddenIO":
        self._open = builtins.open
        self._socket = socket.socket
        self._create_connection = socket.create_connection

        def _no_filesystem(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("the pure selector performed filesystem access")

        def _no_network(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("the pure selector performed network access")

        builtins.open = _no_filesystem
        socket.socket = _no_network
        socket.create_connection = _no_network
        return self

    def __exit__(self, *_exc: Any) -> bool:
        builtins.open = self._open
        socket.socket = self._socket
        socket.create_connection = self._create_connection
        return False


def build_claim(symbol: str = "SOLUSD") -> DetectorClaim:
    return DetectorClaim.create(
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=f"INSTR:kraken:{symbol}:1",
        venue_instrument_id=symbol,
        snapshot_id=f"SNAP:{symbol}",
        detector_input_fingerprint=f"DETIN:{symbol}",
        evaluation_cutoff=CUTOFF,
    )


def active_episode(symbol: str = "SOLUSD"):
    chosen = build_claim(symbol)
    return lifecycle.apply_claim(chosen, None, chosen.evaluation_cutoff, F4_POLICY).episode


def market_validation() -> MarketDataValidation:
    return MarketDataValidation(
        status=MARKET_PASS,
        qualified=True,
        warnings=[],
        rejection_reasons=[],
        candle_count=200,
        latest_candle_timestamp=1757600000,
        latest_candle_age_seconds=120.0,
        duplicate_timestamp_count=0,
        gap_count=0,
        largest_gap_seconds=0.0,
        invalid_ohlc_count=0,
        non_finite_value_count=0,
        ticker_last=100.0,
        latest_ohlc_close=100.0,
        ticker_vs_ohlc_difference_pct=0.0,
        suspicious_spike_detected=False,
    )


def execution_validation() -> ExecutionValidation:
    return ExecutionValidation(
        status=EXECUTION_VALID,
        book_coverage_status=COMPLETE,
        warnings=[],
        buy_fully_covered=True,
        sell_fully_covered=True,
        buy_visible_coverage_pct=100.0,
        sell_visible_coverage_pct=100.0,
        spread_bps=5.0,
        estimated_visible_short_round_trip_market_drag_pct=0.1,
        recent_trade_status=FRESH,
    )


def snapshot(symbol: str = "SOLUSD") -> MarketSnapshot:
    candidate = MarketSnapshot(
        symbol=symbol,
        last_price=100.0,
        ema20=101.0,
        ema50=100.0,
        ema200=99.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.1,
        technical_score=90,
        trend="bullish",
    )
    candidate.trade_direction = "LONG"
    candidate.market_data_validation = market_validation()
    candidate.execution_validation = execution_validation()
    candidate.margin_validation_status = "NOT_REQUIRED"
    candidate.margin_eligible = False
    return candidate


def feasible_decision(symbol: str = "SOLUSD"):
    episode = active_episode(symbol)
    return fseam.evaluate_feasibility(
        episode, snapshot(symbol), CUTOFF, FEASIBILITY_POLICY
    )


def input_vector(symbol: str = "SOLUSD", *, validity_seconds: int = 3600) -> ForecastInputVector:
    return ForecastInputVector(
        input_schema_id="forecast-input-schema",
        input_schema_version="1",
        source_snapshot_id=f"SNAP:{symbol}",
        source_cutoff=CUTOFF,
        horizon=horizon(validity_seconds),
        features=(ForecastFeatureValue("rsi", 55.0, CUTOFF),),
    )


def artifact(symbol: str = "SOLUSD", *, validity_seconds: int = 3600) -> ForecastModelArtifact:
    inputs = input_vector(symbol, validity_seconds=validity_seconds)
    return ForecastModelArtifact.build(
        model_kind=ForecastModelKind.SYNTHETIC_TEST_ONLY,
        model_family="synthetic-deterministic",
        model_version="1",
        model_status=ForecastModelStatus.CALIBRATED_SHADOW,
        training_cutoff=TRAIN,
        input_schema_id=inputs.input_schema_id,
        input_schema_version=inputs.input_schema_version,
        input_feature_names=inputs.feature_names,
        horizon_contract=inputs.horizon,
        training_population_id="POP:train",
        evaluation_population_id="POP:eval",
        calibration_report_id=CALIBRATION_REPORT_ID,
        model_payload_ref="sha256:" + "0" * 64,
        parameters={},
        created_at=RELEASED,
        released_at=RELEASED,
    )


class SyntheticTestModel:
    """A deterministic ``SYNTHETIC_TEST_ONLY`` adapter for mechanics only."""

    kind = ForecastModelKind.SYNTHETIC_TEST_ONLY

    def __init__(self, expected_return: float = 0.05) -> None:
        self._expected_return = expected_return

    def predict(self, request: ForecastRequestLike) -> ForecastModelOutput:  # noqa: ARG002
        return ForecastModelOutput(
            entry_distribution=ProbabilityDistribution(
                kind=DistributionKind.ENTRY_EXECUTION,
                probabilities={"NO_FILL": 0.2, "PARTIAL_FILL": 0.3, "FULL_FILL": 0.5},
            ),
            post_fill_distribution=ProbabilityDistribution(
                kind=DistributionKind.POST_FILL_PATH,
                probabilities={"TARGET": 0.4, "STOP": 0.3, "TIMEOUT": 0.2, "RISK_EXIT": 0.1},
            ),
            expected_return_unconditional=self._expected_return,
            uncertainty=ForecastUncertainty(
                expected_return_lower_bound=self._expected_return - 0.02,
                expected_return_upper_bound=self._expected_return + 0.02,
                coverage_level=0.9,
                interval_semantics="central predictive interval of realized net return",
                evidence_reference=CALIBRATION_REPORT_ID,
            ),
        )


ForecastRequestLike = Any


def make_registry(expected_return: float) -> Any:
    base = fengine.TrustedForecastModelRegistry(adapters={}, allow_test_adapters=True)
    return base.register(SyntheticTestModel(expected_return)).vouch_for_report(
        CALIBRATION_REPORT_ID
    )


def forecast_for(symbol: str, expected_return: float, *, validity_seconds: int = 3600):
    episode = active_episode(symbol)
    feasibility = feasible_decision(symbol)
    return fengine.evaluate_forecast(
        episode,
        feasibility,
        input_vector(symbol, validity_seconds=validity_seconds),
        EVAL,
        FORECAST_POLICY,
        model_artifact=artifact(symbol, validity_seconds=validity_seconds),
        registry=make_registry(expected_return),
    )


def observables(
    *,
    technical_score: int = 90,
    attainability_score: float = 70.0,
    target_2_move_pct: float = 6.0,
    execution_drag_pct: float | None = 0.1,
    book_coverage_status: str = COMPLETE,
    recent_trade_status: str = FRESH,
    cross_pair_confirmation_status: str = "CONFIRMED",
    independent_reference_status: str | None = "CONFIRMED",
) -> LegacyComparatorObservables:
    return LegacyComparatorObservables(
        technical_score=technical_score,
        attainability_score=attainability_score,
        target_2_move_pct=target_2_move_pct,
        book_coverage_status=book_coverage_status,
        recent_trade_status=recent_trade_status,
        cross_pair_confirmation_status=cross_pair_confirmation_status,
        execution_drag_pct=execution_drag_pct,
        independent_reference_status=independent_reference_status,
    )


def candidate(
    symbol: str,
    expected_return: float = 0.05,
    *,
    direction: PortfolioDirection = PortfolioDirection.LONG,
    stop_loss_fraction: float = 0.01,
    requested_capital_fraction: float = 0.1,
    capacity_notional: float = 10_000.0,
    data_quality_status: PortfolioEvidenceStatus = PortfolioEvidenceStatus.VALID,
    execution_evidence_status: PortfolioEvidenceStatus = PortfolioEvidenceStatus.VALID,
    common_shock_group: str | None = None,
    technical_score: int = 90,
    validity_seconds: int = 3600,
    forecast: Any = _UNSET,
) -> PortfolioCandidate:
    resolved_forecast = (
        forecast_for(symbol, expected_return, validity_seconds=validity_seconds)
        if forecast is _UNSET
        else forecast
    )
    return PortfolioCandidate(
        episode_id=resolved_forecast.episode_id,
        feasibility_decision_id=resolved_forecast.feasibility_decision_id,
        symbol=symbol,
        direction=direction,
        common_shock_group=common_shock_group or f"G-{symbol}",
        data_quality_status=data_quality_status,
        execution_evidence_status=execution_evidence_status,
        liquidity_capacity_notional=capacity_notional,
        requested_capital_fraction=requested_capital_fraction,
        stop_loss_fraction=stop_loss_fraction,
        legacy_observables=observables(technical_score=technical_score),
        forecast=resolved_forecast,
    )


def capital_state(
    available: float = 1000.0,
    *,
    portfolio_version: str = "PV:r3f7",
    as_of: datetime = EVAL,
) -> PortfolioCapitalState:
    return PortfolioCapitalState(
        portfolio_version=portfolio_version,
        available_capital=available,
        currency="USD",
        as_of=as_of,
    )


def exposure(
    *,
    portfolio_version: str = "PV:r3f7",
    gross: float = 0.0,
    open_positions: int = 0,
    loss_at_stop: float = 0.0,
    same_direction: dict[str, int] | None = None,
    symbol_exposure: dict[str, float] | None = None,
    group_counts: dict[str, int] | None = None,
    as_of: datetime = EVAL,
) -> PortfolioExposureSnapshot:
    return PortfolioExposureSnapshot(
        portfolio_version=portfolio_version,
        gross_exposure=gross,
        open_positions=open_positions,
        loss_at_stop=loss_at_stop,
        same_direction_counts=same_direction or {},
        symbol_exposure=symbol_exposure or {},
        common_shock_group_counts=group_counts or {},
        as_of=as_of,
    )


def policy(**overrides: Any) -> PortfolioPolicy:
    payload: dict[str, Any] = {
        "max_gross_exposure_fraction": 1.0,
        "max_positions": 5,
        "max_same_direction": 5,
        "max_capital_fraction_per_candidate": 0.1,
        "max_symbol_exposure_fraction": 1.0,
        "max_common_shock_group_positions": 5,
        "max_portfolio_loss_fraction": 0.05,
    }
    payload.update(overrides)
    return PortfolioPolicy(**payload)


def select(
    panel: Any,
    *,
    capital: Any = _UNSET,
    portfolio_exposure: Any = _UNSET,
    window: PortfolioEvaluationWindow | None = WINDOW,
    evaluation_time: datetime = EVAL,
    configuration: PortfolioPolicy | None = None,
) -> PortfolioDecision:
    return select_portfolio(
        panel,
        capital_state=capital_state() if capital is _UNSET else capital,
        exposure=exposure() if portfolio_exposure is _UNSET else portfolio_exposure,
        window=window,
        evaluation_time=evaluation_time,
        policy=configuration if configuration is not None else policy(),
    )


def permitted_capital(
    item: PortfolioCandidate, configuration: PortfolioPolicy, capital: PortfolioCapitalState
) -> float:
    fraction = min(
        item.requested_capital_fraction, configuration.max_capital_fraction_per_candidate
    )
    return min(fraction * capital.available_capital, item.liquidity_capacity_notional)


# ---------------------------------------------------------------------------
# AC-001 .. AC-028
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_vocabulary_is_exact() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-001: the F7 status, cash, abstention, evidence, reservation and version vocabularies are exactly the ratified tokens."""
    assert [token.value for token in PortfolioStatus] == [
        "SELECTED",
        "CASH_NO_TRADE",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert [token.value for token in PortfolioCashReason] == [
        "NO_POSITIVE_EXPECTED_NET_DOLLARS",
        "CONSTRAINTS_EXCLUDE_ALL_CANDIDATES",
    ]
    assert [token.value for token in PortfolioEvidenceStatus] == [
        "VALID",
        "UNAVAILABLE",
        "FAILED",
    ]
    assert [token.value for token in PortfolioDirection] == ["LONG", "SHORT"]
    assert [token.value for token in ReservationStatus] == [
        "PLANNED",
        "RELEASED",
        "EXPIRED",
        "FILL_ADJUSTED",
    ]
    assert [token.value for token in ReservationReleaseReason] == [
        "CANCELLED",
        "EXPIRED",
        "FILL_ADJUSTED",
    ]
    assert PORTFOLIO_DECISION_SCHEMA_VERSION == "portfolio-decision-v1"
    assert PORTFOLIO_SELECTOR_VERSION == "portfolio-selector-v1"
    assert PORTFOLIO_POLICY_VERSION == "portfolio-shadow-policy-v1"
    assert PORTFOLIO_RESERVATION_PLAN_SCHEMA_VERSION == "portfolio-reservation-plan-v1"
    assert PORTFOLIO_EVALUATION_VERSION == "portfolio-evaluation-v1"
    assert PORTFOLIO_DECISION_ID_PREFIX == "PSEL"
    for token in PortfolioStatus:
        assert token.value not in {"PASS", "FAIL", "VETO", "WATCH", "APPROVE", "REJECT"}
    with pytest.raises(PortfolioContractError):
        PortfolioPolicy(
            max_gross_exposure_fraction=1.0,
            max_positions=1,
            max_same_direction=1,
            max_capital_fraction_per_candidate=0.1,
            max_symbol_exposure_fraction=1.0,
            max_common_shock_group_positions=1,
            max_portfolio_loss_fraction=0.1,
            policy_version="portfolio-other-v1",
        )


@pytest.mark.acceptance
def test_ac_002_contract_first_and_movable_pointer() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-002: the F7 contract exists and declares its own increment, the movable pointer resolves to an existing contract, and F7 pins nothing."""
    assert INCREMENT_PATH.is_file()
    lines = INCREMENT_PATH.read_text(encoding="utf-8").splitlines()
    assert lines[0].strip() == "INCREMENT:"
    assert lines[1].strip() == INCREMENT

    # The pointer is deliberately movable: it only has to resolve to a contract.
    pointer = ACTIVE_INCREMENT_PATH.read_text(encoding="utf-8").strip()
    assert (CONTRACT_DIR / f"{pointer}.md").is_file(), pointer

    # This module never pins the global pointer to its own identity.
    assert global_pointer_pin_violations(Path(__file__).read_text(encoding="utf-8")) == []
    assert "assert active == INCREMENT" not in SELECTOR_SOURCE


@pytest.mark.acceptance
def test_ac_003_candidate_panel_identity_and_collision() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-003: candidate identity is deterministic, a forged identity fails closed, and a duplicate or colliding panel fails closed."""
    first = candidate("AAAUSD")
    second = candidate("AAAUSD")
    assert first.candidate_id == second.candidate_id
    assert first.candidate_id.startswith("PCAND:")

    with pytest.raises(PortfolioContractError):
        replace(first, candidate_id="PCAND:forged")

    with pytest.raises(PortfolioContractError):
        select([first, first])

    duplicate_symbol = candidate("AAAUSD")
    colliding = PortfolioCandidate(
        episode_id=second.episode_id,
        feasibility_decision_id=second.feasibility_decision_id,
        symbol="AAAUSD",
        direction=PortfolioDirection.LONG,
        common_shock_group="G-other",
        data_quality_status=PortfolioEvidenceStatus.VALID,
        execution_evidence_status=PortfolioEvidenceStatus.VALID,
        liquidity_capacity_notional=500.0,
        requested_capital_fraction=0.05,
        stop_loss_fraction=0.02,
        legacy_observables=observables(technical_score=85),
        forecast=duplicate_symbol.forecast,
    )
    with pytest.raises(PortfolioContractError):
        select([first, colliding])

    # A different forecast produces a different candidate identity.
    assert candidate("AAAUSD", 0.06).candidate_id != first.candidate_id


@pytest.mark.acceptance
def test_ac_004_common_evaluation_window_is_explicit_and_consistent() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-004: the evaluation window is explicit UTC and common, a naive or inverted window fails closed, and an inconsistent window fails closed."""
    with pytest.raises(PortfolioContractError):
        PortfolioEvaluationWindow(
            start=datetime(2026, 9, 11, 15, 0), end=EVAL
        )
    with pytest.raises(PortfolioContractError):
        PortfolioEvaluationWindow(start=EVAL, end=EVAL)

    assert WINDOW.window_identity.startswith("PWIN:")

    # An inverted window fails closed.
    with pytest.raises(PortfolioContractError):
        PortfolioEvaluationWindow(start=EVAL, end=EVAL - timedelta(hours=1))

    # An evaluation instant outside the declared window fails closed.
    with pytest.raises(PortfolioContractError):
        select([candidate("AAAUSD")], evaluation_time=WINDOW.end)

    # A forecast that outlives the declared window fails closed.
    narrow = PortfolioEvaluationWindow(start=EVAL, end=EVAL + timedelta(minutes=5))
    with pytest.raises(PortfolioContractError):
        select([candidate("AAAUSD")], window=narrow)

    # Only one window exists for the whole panel: there is no per-candidate window.
    assert not hasattr(candidate("AAAUSD"), "window")


@pytest.mark.acceptance
def test_ac_005_capital_and_exposure_snapshots_are_explicit() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-005: the capital and exposure snapshots are explicit, fingerprint-deterministic, forged-id rejecting and version-aligned."""
    state = capital_state()
    assert state.capital_state_fingerprint.startswith("PCAP:")
    assert capital_state().capital_state_fingerprint == state.capital_state_fingerprint
    assert capital_state(2000.0).capital_state_fingerprint != state.capital_state_fingerprint
    with pytest.raises(PortfolioContractError):
        replace(state, capital_state_fingerprint="PCAP:forged")

    book = exposure(gross=100.0, open_positions=1, same_direction={"LONG": 1})
    assert book.exposure_fingerprint.startswith("PEXP:")
    assert book.exposure_fingerprint == exposure(
        gross=100.0, open_positions=1, same_direction={"LONG": 1}
    ).exposure_fingerprint
    with pytest.raises(PortfolioContractError):
        replace(book, exposure_fingerprint="PEXP:forged")

    # A stale / mismatched portfolio version fails closed.
    stale = exposure(portfolio_version="PV:other")
    with pytest.raises(PortfolioContractError):
        select([candidate("AAAUSD")], portfolio_exposure=stale)


@pytest.mark.acceptance
def test_ac_006_policy_has_no_invented_defaults() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-006: every policy limit is explicit, validated and identity-bound, and an unratified or out-of-range value fails closed."""
    with pytest.raises(TypeError):
        PortfolioPolicy()  # type: ignore[call-arg]
    with pytest.raises(PortfolioContractError):
        policy(max_gross_exposure_fraction=0.0)
    with pytest.raises(PortfolioContractError):
        policy(max_gross_exposure_fraction=1.5)
    with pytest.raises(PortfolioContractError):
        policy(max_positions=0)
    with pytest.raises(PortfolioContractError):
        policy(max_portfolio_loss_fraction=float("nan"))

    configured = policy()
    assert configured.policy_identity.startswith("PPOL:")
    assert policy().policy_identity == configured.policy_identity
    assert policy(max_positions=2).policy_identity != configured.policy_identity
    with pytest.raises(PortfolioContractError):
        replace(configured, policy_identity="PPOL:forged")


@pytest.mark.acceptance
def test_ac_007_forecast_evidence_boundary() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-007: a trusted FORECAST is required, missing or stale evidence is never favorable, and a lineage mismatch fails closed."""
    trust = candidate("AAAUSD")

    # No forecast at all: the candidate is never favorable.
    without = replace(trust, forecast=None, candidate_id="")
    decision = select([without])
    assert decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert decision.abstention_reason is PortfolioAbstentionReason.NO_QUALIFIED_FORECAST

    # A stale forecast never improves a rank: evaluating after valid_until drops it.
    stale = select([trust], evaluation_time=EVAL + timedelta(hours=2))
    assert stale.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert stale.abstention_reason is PortfolioAbstentionReason.NO_QUALIFIED_FORECAST

    # A non-VALID data-quality or execution-evidence gate is never favorable.
    degraded = replace(
        trust, data_quality_status=PortfolioEvidenceStatus.UNAVAILABLE, candidate_id=""
    )
    assert select([degraded]).status is PortfolioStatus.INSUFFICIENT_EVIDENCE

    # A mismatched lineage fails closed.
    mismatched = replace(trust, feasibility_decision_id="FEAS:forged", candidate_id="")
    with pytest.raises(PortfolioContractError):
        select([mismatched])


@pytest.mark.acceptance
def test_ac_008_zero_calibrated_models_abstain() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-008: with the empty production registry the selector abstains deterministically and never synthesizes economics."""
    production = fengine.PRODUCTION_MODEL_REGISTRY
    assert production.registered_adapter_count == 0

    episode = active_episode("AAAUSD")
    feasibility = feasible_decision("AAAUSD")
    abstained = fengine.evaluate_forecast(
        episode, feasibility, input_vector("AAAUSD"), EVAL, FORECAST_POLICY
    )
    assert abstained.status.value == "INSUFFICIENT_EVIDENCE"

    producer = candidate("AAAUSD", forecast=abstained)
    decision = select([producer])
    assert decision.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert decision.abstention_reason is PortfolioAbstentionReason.NO_QUALIFIED_FORECAST
    assert decision.allocations == ()
    assert decision.expected_net_dollars is None
    assert fengine.PRODUCTION_MODEL_REGISTRY.registered_adapter_count == 0


@pytest.mark.acceptance
def test_ac_009_cash_is_a_real_competing_decision() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-009: cash / no-trade is selected whenever no admissible portfolio beats it, and no quota is ever forced."""
    negative = [candidate("AAAUSD", -0.01), candidate("BBBUSD", -0.02)]
    decision = select(negative)
    assert decision.status is PortfolioStatus.CASH_NO_TRADE
    assert decision.cash_reason is PortfolioCashReason.NO_POSITIVE_EXPECTED_NET_DOLLARS
    assert decision.allocations == ()
    assert decision.reservation_plan is None
    assert decision.unallocated_capital == 1000.0

    # A constraint set that excludes every candidate is still cash, not an error.
    blocked = select(
        [candidate("AAAUSD")],
        portfolio_exposure=exposure(gross=10_000.0, open_positions=0),
    )
    assert blocked.status is PortfolioStatus.CASH_NO_TRADE
    assert blocked.cash_reason is PortfolioCashReason.CONSTRAINTS_EXCLUDE_ALL_CANDIDATES

    # Cash is strictly better than a portfolio whose only option is negative.
    assert select([candidate("AAAUSD", 0.0)]).status is PortfolioStatus.CASH_NO_TRADE


@pytest.mark.acceptance
def test_ac_010_missing_state_abstains() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-010: an unavailable capital or exposure snapshot yields a governed abstention, never a default state."""
    assert (
        select([candidate("AAAUSD")], capital=None).abstention_reason
        is PortfolioAbstentionReason.CAPITAL_STATE_UNAVAILABLE
    )
    assert (
        select([candidate("AAAUSD")], portfolio_exposure=None).abstention_reason
        is PortfolioAbstentionReason.EXPOSURE_STATE_UNAVAILABLE
    )
    empty = select_portfolio(
        [],
        capital_state=capital_state(),
        exposure=exposure(),
        window=WINDOW,
        evaluation_time=EVAL,
        policy=policy(),
    )
    assert empty.abstention_reason is PortfolioAbstentionReason.NO_ELIGIBLE_CANDIDATES


@pytest.mark.acceptance
def test_ac_011_economics_use_f6_returns_once_and_keep_families_separate() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-011: expected net dollars are capital times the F6 unconditional expected return, fees are never charged twice, and NO_FILL is included without being a loss."""
    item = candidate("AAAUSD", 0.05)
    decision = select([item], configuration=policy(max_portfolio_loss_fraction=1.0))
    allocation = decision.allocations[0]
    assert allocation.allocated_capital == pytest.approx(100.0)
    assert allocation.expected_net_dollars == pytest.approx(100.0 * 0.05)
    assert allocation.expected_fill_probability == pytest.approx(0.8)
    assert allocation.expected_no_fill_probability == pytest.approx(0.2)
    assert allocation.expected_fill_probability + allocation.expected_no_fill_probability == pytest.approx(1.0)

    # NO_FILL is included in the intent population and contributes no loss: the
    # entry family is read separately and never collapsed into a win probability.
    forecast = item.forecast
    probability_of_fill, probability_of_no_fill = expected_fill_probability(forecast)
    assert probability_of_fill == pytest.approx(0.8)
    assert probability_of_no_fill == pytest.approx(0.2)
    assert allocation.expected_net_dollars == pytest.approx(
        allocation.allocated_capital * forecast.expected_return_unconditional
    )
    # No second fee/profit model exists: there is no explicit charge input.
    assert not hasattr(policy(), "explicit_transaction_charge")
    assert not hasattr(policy(), "estimated_round_trip_cost_pct")
    for source in F7_SOURCES:
        assert "estimated_round_trip_cost" not in source


@pytest.mark.acceptance
def test_ac_012_uncertainty_is_retained() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-012: the decision retains an explicit aggregate uncertainty interval and never optimizes a point estimate alone."""
    decision = select([candidate("AAAUSD", 0.05)], configuration=policy(max_portfolio_loss_fraction=1.0))
    assert decision.expected_net_dollars == pytest.approx(5.0)
    assert decision.expected_net_dollars_lower_bound == pytest.approx(100.0 * 0.03)
    assert decision.expected_net_dollars_upper_bound == pytest.approx(100.0 * 0.07)
    assert (
        decision.expected_net_dollars_lower_bound
        <= decision.expected_net_dollars
        <= decision.expected_net_dollars_upper_bound
    )

    # A FORECAST without uncertainty is refused by F6 and therefore unusable here.
    with pytest.raises(ForecastContractError):
        ForecastUncertainty(
            expected_return_lower_bound=0.05,
            expected_return_upper_bound=0.01,
            coverage_level=0.9,
            interval_semantics="central interval",
            evidence_reference=CALIBRATION_REPORT_ID,
        )


@pytest.mark.acceptance
def test_ac_013_operating_cost_unknown_is_represented() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-013: an unavailable operating cost is reported as unknown rather than silently assumed zero, and a supplied cost is reported."""
    unknown = select([candidate("AAAUSD", 0.05)])
    assert unknown.operating_cost_available is False
    assert unknown.attributable_operating_cost is None

    known = select(
        [candidate("AAAUSD", 0.05)],
        configuration=policy(attributable_operating_cost=1.25),
    )
    assert known.operating_cost_available is True
    assert known.attributable_operating_cost == pytest.approx(1.25)
    # The primary objective stays trading economics; operating economics is explicit.
    assert known.expected_net_dollars == pytest.approx(5.0)


@pytest.mark.acceptance
def test_ac_014_selection_and_reservation_never_over_commit() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-014: a selected portfolio never exceeds the explicit capital or caps, cash may remain, and a reservation never over-commits."""
    panel = [candidate("AAAUSD", 0.08), candidate("BBBUSD", 0.06)]
    decision = select(
        panel,
        configuration=policy(
            max_gross_exposure_fraction=0.2, max_portfolio_loss_fraction=1.0
        ),
    )
    assert decision.status is PortfolioStatus.SELECTED
    assert len(decision.allocations) == 2
    gross = sum(a.allocated_capital for a in decision.allocations)
    assert gross <= 0.2 * 1000.0 + 1e-9
    assert decision.unallocated_capital == pytest.approx(1000.0 - gross)
    assert decision.reservation_plan is not None
    assert decision.reservation_plan.planned_capital == pytest.approx(gross)
    assert decision.reservation_plan.planned_capital <= 1000.0
    assert set(decision.selected_candidate_ids) == {
        a.candidate_id for a in decision.allocations
    }

    # A single candidate and an empty available capital base are both handled.
    assert len(select([candidate("AAAUSD", 0.05)]).allocations) == 1
    assert select([], capital=capital_state()) .status is PortfolioStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.acceptance
def test_ac_015_constraints_are_enforced() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-015: capital, concentration, common-shock, capacity, evidence and drawdown limits all bind and never improve a rank."""
    panel = [candidate("AAAUSD", 0.05), candidate("BBBUSD", 0.05)]

    # Concentration: a per-symbol cap blocks a second position in the same symbol.
    concentrated = select(
        [candidate("AAAUSD", 0.05)],
        portfolio_exposure=exposure(symbol_exposure={"AAAUSD": 900.0}),
        configuration=policy(max_symbol_exposure_fraction=0.5, max_portfolio_loss_fraction=1.0),
    )
    assert concentrated.status is PortfolioStatus.CASH_NO_TRADE

    # Same-direction limit.
    directional = select(
        panel,
        portfolio_exposure=exposure(same_direction={"LONG": 1}, open_positions=1),
        configuration=policy(max_same_direction=1, max_portfolio_loss_fraction=1.0),
    )
    assert directional.status is PortfolioStatus.CASH_NO_TRADE

    # Common-shock group limit.
    grouped = select(
        [
            candidate("AAAUSD", 0.05, common_shock_group="G-1"),
            candidate("BBBUSD", 0.05, common_shock_group="G-1"),
        ],
        configuration=policy(max_common_shock_group_positions=1, max_portfolio_loss_fraction=1.0),
    )
    assert len(grouped.allocations) == 1

    # Position-count limit.
    capped = select(
        panel, configuration=policy(max_positions=1, max_portfolio_loss_fraction=1.0)
    )
    assert len(capped.allocations) == 1

    # Liquidity / capacity: a capacity below the permitted allocation shrinks it.
    thin = select(
        [candidate("AAAUSD", 0.05, capacity_notional=25.0)],
        configuration=policy(max_portfolio_loss_fraction=1.0),
    )
    assert thin.allocations[0].allocated_capital == pytest.approx(25.0)

    # Drawdown / loss limit binds.
    lossy = select(
        [candidate("AAAUSD", 0.05, stop_loss_fraction=0.5)],
        configuration=policy(max_portfolio_loss_fraction=0.02),
    )
    assert lossy.status is PortfolioStatus.CASH_NO_TRADE


@pytest.mark.acceptance
def test_ac_016_permutation_invariance_and_byte_identity() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-016: input ordering never changes the selected portfolio and identical semantic inputs are byte-identical."""
    panel = [
        candidate("AAAUSD", 0.08),
        candidate("BBBUSD", 0.06),
        candidate("CCCUSD", 0.04),
    ]
    configuration = policy(max_portfolio_loss_fraction=1.0)
    first = select(panel, configuration=configuration)
    second = select(list(reversed(panel)), configuration=configuration)
    assert first.decision_id == second.decision_id
    assert first.to_dict() == second.to_dict()
    assert first.panel_fingerprint == portfolio_panel_fingerprint(WINDOW, panel)
    assert PortfolioDecision.from_dict(first.to_dict()) == first


@pytest.mark.acceptance
def test_ac_017_deterministic_tie_break() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-017: equally valued portfolios are separated by an explicit, stable tie-break."""
    left = candidate("AAAUSD", 0.05, stop_loss_fraction=0.05)
    right = candidate("BBBUSD", 0.05, stop_loss_fraction=0.05)
    configuration = policy(max_portfolio_loss_fraction=0.005)  # only one fits
    decision = select([right, left], configuration=configuration)
    assert len(decision.allocations) == 1
    expected = min(left.candidate_id, right.candidate_id)
    assert decision.allocations[0].candidate_id == expected
    # Deterministic across a reordering too.
    assert select([left, right], configuration=configuration).decision_id == decision.decision_id


@pytest.mark.acceptance
def test_ac_018_objective_beats_greedy_per_row() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-018: the portfolio objective can beat a greedy per-row selection on the same synthetic panel."""
    panel = [candidate("AAAUSD", 0.12, stop_loss_fraction=0.25)]
    for index, letter in enumerate("BCDEFGHI"):
        panel.append(
            candidate(f"{letter}{letter}{letter}USD", 0.05, stop_loss_fraction=0.02)
        )
    configuration = policy(
        max_gross_exposure_fraction=1.0,
        max_portfolio_loss_fraction=0.03,
        max_positions=12,
        max_same_direction=12,
        max_common_shock_group_positions=12,
    )
    state = capital_state()
    decision = select(panel, configuration=configuration)
    assert decision.status is PortfolioStatus.SELECTED

    # Greedy per-row: take the highest expected return first, then the next, while
    # the constraints still allow it.
    ordered = sorted(
        panel, key=lambda item: -item.forecast.expected_return_unconditional
    )
    loss_cap = configuration.max_portfolio_loss_fraction * state.available_capital
    greedy_net = 0.0
    greedy_loss = 0.0
    greedy_count = 0
    for item in ordered:
        capital = permitted_capital(item, configuration, state)
        loss = capital * item.stop_loss_fraction
        if greedy_loss + loss > loss_cap + 1e-9:
            continue
        greedy_loss += loss
        greedy_net += capital * item.forecast.expected_return_unconditional
        greedy_count += 1
    assert decision.expected_net_dollars > greedy_net
    assert len(decision.allocations) != greedy_count


@pytest.mark.acceptance
def test_ac_019_reservation_plan_is_deterministic_and_version_bound() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-019: the reservation plan is deterministic and version-bound, a stale version fails closed, and release/expire/fill-adjust are pure and deterministic."""
    item = candidate("AAAUSD", 0.05)
    decision = select([item], configuration=policy(max_portfolio_loss_fraction=1.0))
    plan = decision.reservation_plan
    assert plan is not None
    assert plan.plan_id.startswith("PRSV:")
    assert plan.portfolio_version == "PV:r3f7"
    assert PortfolioReservationPlan.from_dict(plan.to_dict()) == plan

    assert_reservation_plan_current(plan, current_portfolio_version="PV:r3f7")
    with pytest.raises(PortfolioContractError):
        assert_reservation_plan_current(plan, current_portfolio_version="PV:stale")

    reservation = plan.reservations[0]
    assert reservation.reservation_id.startswith("PRES:")
    assert reservation.status is ReservationStatus.PLANNED
    assert reservation.expires_at == item.forecast.valid_until

    released = release_reservation(
        plan,
        candidate_id=item.candidate_id,
        reason=ReservationReleaseReason.CANCELLED,
        at_time=EVAL,
    )
    assert released.plan_id != plan.plan_id
    assert released.reservations[0].status is ReservationStatus.RELEASED
    assert released.planned_capital == 0.0
    assert (
        release_reservation(
            plan,
            candidate_id=item.candidate_id,
            reason=ReservationReleaseReason.CANCELLED,
            at_time=EVAL,
        ).plan_id
        == released.plan_id
    )
    with pytest.raises(PortfolioContractError):
        release_reservation(
            plan,
            candidate_id="PCAND:missing",
            reason=ReservationReleaseReason.CANCELLED,
            at_time=EVAL,
        )

    late = expire_reservations(plan, at_time=EVAL + timedelta(days=1))
    assert late.reservations[0].status is ReservationStatus.EXPIRED
    assert expire_reservations(plan, at_time=EVAL).plan_id == plan.plan_id

    adjusted = adjust_reservation_for_fill(
        plan, candidate_id=item.candidate_id, filled_capital=40.0, at_time=EVAL
    )
    assert adjusted.reservations[0].status is ReservationStatus.FILL_ADJUSTED
    assert adjusted.reservations[0].reserved_capital == pytest.approx(60.0)
    with pytest.raises(PortfolioContractError):
        adjust_reservation_for_fill(
            plan, candidate_id=item.candidate_id, filled_capital=500.0, at_time=EVAL
        )
    # No production reservation writer is imported or invoked.
    for module in imported_modules(SELECTOR_SOURCE):
        assert not any(
            part in module for part in ("paper_v2", "reservation_writer", "registry_io")
        )


@pytest.mark.acceptance
def test_ac_020_frozen_legacy_comparator_preserves_semantics() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-020: the comparator reuses the live legacy constants and behaviour on the same panel without modifying them."""
    import app.scanner.candidates as legacy_candidates
    import app.services.economic_quality_gate as legacy_gate
    import app.services.profit_ranking as legacy_ranking

    # The comparator reuses the live constants by identity, never by restating them.
    assert LEGACY_MIN_TECHNICAL_SCORE is legacy_candidates.MIN_TECHNICAL_SCORE
    assert LEGACY_MAX_CANDIDATES is legacy_candidates.MAX_CANDIDATES
    assert (
        LEGACY_PRODUCTION_CAPITAL_FRACTION
        is legacy_gate.PRODUCTION_MAX_CAPITAL_FRACTION
    )
    for name in (
        "ECONOMIC_WEIGHT",
        "TARGET_QUALITY_WEIGHT",
        "EXECUTION_QUALITY_WEIGHT",
        "TECHNICAL_QUALITY_WEIGHT",
        "EVIDENCE_QUALITY_WEIGHT",
        "ECONOMIC_FULL_CREDIT_MOVE_PCT",
        "EXECUTION_DRAG_FULL_PENALTY_PCT",
    ):
        assert name in legacy_ranking.__dict__, name

    # The observed policy derives its constraints from the live risk defaults.
    observed = observed_legacy_policy(
        max_symbol_exposure_fraction=0.5,
        max_common_shock_group_positions=2,
        max_portfolio_loss_fraction=0.03,
    )
    assert observed.max_gross_exposure_fraction == pytest.approx(0.5)
    assert observed.max_positions == 3
    assert observed.max_same_direction == 2
    assert observed.max_capital_fraction_per_candidate == pytest.approx(0.2)

    # The comparator reproduces the live profit-ranking arithmetic exactly: the
    # real live function is driven with equivalent observations and must agree.
    import app.services.profit_ranking as legacy_ranking

    def _duck_opportunity(symbol: str, item_obs: LegacyComparatorObservables):
        snapshot_like = types.SimpleNamespace(
            symbol=symbol,
            technical_score=item_obs.technical_score,
            execution_validation=types.SimpleNamespace(
                estimated_visible_round_trip_market_drag_pct=item_obs.execution_drag_pct,
                book_coverage_status=item_obs.book_coverage_status,
                recent_trade_status=item_obs.recent_trade_status,
            ),
            independent_market_reference=(
                None
                if item_obs.independent_reference_status is None
                else types.SimpleNamespace(
                    mapping_status="UNIQUE",
                    status=item_obs.independent_reference_status,
                )
            ),
            cross_pair_confirmation_status=item_obs.cross_pair_confirmation_status,
        )
        target_like = types.SimpleNamespace(
            attainability_score=item_obs.attainability_score,
            qualified=True,
        )
        economic_like = types.SimpleNamespace(
            target_2_move_pct=item_obs.target_2_move_pct,
            target_2_net_profit=100.0,
            qualified=True,
        )
        return legacy_ranking.QualifiedOpportunity(
            alert={},
            snapshot=snapshot_like,
            plan=None,  # type: ignore[arg-type]
            target_quality=target_like,  # type: ignore[arg-type]
            economic_quality=economic_like,  # type: ignore[arg-type]
        )

    sample = observables()
    real_single = legacy_ranking.evaluate_profit_ranking(
        _duck_opportunity("AAAUSD", sample).snapshot,
        _duck_opportunity("AAAUSD", sample).target_quality,
        _duck_opportunity("AAAUSD", sample).economic_quality,
    )
    assert legacy_unrounded_total(sample) == pytest.approx(
        real_single._unrounded_total_score
    )
    assert legacy_unrounded_total(sample) > 0.0

    panel = [candidate("AAAUSD", 0.06), candidate("BBBUSD", 0.05)]
    result = build_frozen_legacy_comparison(panel, capital_state=capital_state(), window=WINDOW)
    assert result.panel_fingerprint == portfolio_panel_fingerprint(WINDOW, panel)
    assert len(result.population_candidate_ids) == 2
    assert result.result_id.startswith("PCMP:")
    assert FrozenLegacyComparatorResult.from_dict(result.to_dict()) == result
    assert (
        set(result.risk_admitted_candidate_ids)
        | set(result.risk_vetoed_candidate_ids)
        == set(result.ranked_candidate_ids)
    )

    # The comparator's ordering matches the live profit-ranking ordering exactly.
    real_order = [
        ranked.opportunity.snapshot.symbol
        for ranked in legacy_ranking.rank_profit_opportunities(
            [_duck_opportunity(item.symbol, item.legacy_observables) for item in panel]
        )
    ]
    symbol_by_id = {item.candidate_id: item.symbol for item in panel}
    assert [symbol_by_id[cid] for cid in result.ranked_candidate_ids] == real_order

    # Top-8 population semantics: the technical threshold and the limit are respected.
    below = candidate("ZZZUSD", 0.05, technical_score=LEGACY_MIN_TECHNICAL_SCORE - 1)
    result2 = build_frozen_legacy_comparison(
        [*panel, below], capital_state=capital_state(), window=WINDOW
    )
    assert below.candidate_id not in result2.population_candidate_ids

    # The live legacy sources are unmodified: no F7 token leaked into them.
    for path in (PROFIT_RANKING_PATH, PORTFOLIO_RISK_PATH, CANDIDATES_PATH):
        text = path.read_text(encoding="utf-8")
        for token in ("portfolio_selector", "select_portfolio", "portfolio_comparator", "F7"):
            assert token not in text, (path, token)


@pytest.mark.acceptance
def test_ac_021_comparison_declares_no_winner() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-021: the comparison record reports F7, cash and legacy side by side, enforces the shared panel and declares no winner."""
    panel = [candidate("AAAUSD", 0.06), candidate("BBBUSD", -0.01)]
    decision = select(panel, configuration=policy(max_portfolio_loss_fraction=1.0))
    legacy = build_frozen_legacy_comparison(
        panel, capital_state=capital_state(), window=WINDOW
    )
    comparison = build_portfolio_comparison(
        decision, legacy, panel_fingerprint=legacy.panel_fingerprint
    )
    assert comparison.f7_decision_id == decision.decision_id
    assert comparison.legacy_result_id == legacy.result_id
    assert comparison.cash_expected_net_dollars == 0.0
    assert comparison.declared_winner is None
    assert "declared_winner" in comparison.to_dict()
    assert comparison.to_dict()["declared_winner"] is None
    for forbidden in ("winner", "promotion", "threshold", "promoted"):
        assert not hasattr(comparison, forbidden)
    assert "threshold" not in identifier_tokens(COMPARATOR_SOURCE)

    # A comparison across two different panels fails closed.
    other = [candidate("CCCUSD", 0.06)]
    other_legacy = build_frozen_legacy_comparison(
        other, capital_state=capital_state(), window=WINDOW
    )
    with pytest.raises(PortfolioContractError):
        build_portfolio_comparison(
            decision, other_legacy, panel_fingerprint=legacy.panel_fingerprint
        )
    with pytest.raises(PortfolioContractError):
        build_portfolio_comparison(
            decision, legacy, panel_fingerprint=other_legacy.panel_fingerprint
        )


@pytest.mark.acceptance
def test_ac_022_runtime_isolation_and_purity() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-022: no runtime path imports or invokes F7, no authority is activated, and the pure selector performs no IO or hidden reads."""
    for path in (RUN_CYCLE_PATH, SCAN_PATH):
        text = path.read_text(encoding="utf-8")
        for token in ("select_portfolio", "portfolio_selector", "opip.portfolio", "portfolio_comparator"):
            assert token not in text, (path, token)

    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if path in (SELECTOR_PATH, COMPARATOR_PATH, CONTRACT_PATH, CONTRACTS_INIT_PATH):
            continue
        text = path.read_text(encoding="utf-8")
        assert "select_portfolio" not in text, path
        assert "portfolio_selector" not in text, path

    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "shadow"' in compose
    assert 'OPIP_PAPER_V2_MODE: "on"' not in compose
    assert "OPIP_COMMITTEE_MODE: \"on\"" not in compose

    # The selector is pure over its declared inputs.
    for source in (SELECTOR_SOURCE, CONTRACT_SOURCE):
        for module in imported_modules(source):
            assert module.startswith(SELECTOR_ALLOWED_IMPORT_PREFIXES), module
        assert called_attributes(source).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)
        assert imported_modules(source).isdisjoint(
            {"time", "random", "uuid", "os", "socket", "subprocess"}
        )

    with ForbiddenIO():
        decision = select([candidate("AAAUSD", 0.05)], configuration=policy(max_portfolio_loss_fraction=1.0))
    assert decision.status is PortfolioStatus.SELECTED

    # The selector carries no funded-order or Committee authority.
    for module in imported_modules(SELECTOR_SOURCE):
        assert not any(
            part in module
            for part in ("committee", "kraken", "telegram", "openai", "anthropic")
        ), module
    assert identifier_tokens(SELECTOR_SOURCE).isdisjoint(
        {"committee", "kraken", "telegram"}
    )


@pytest.mark.acceptance
def test_ac_023_f3_f4_f5_f6_and_legacy_semantics_unchanged() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-023: F3/F4/F5/F6 stay frozen, the legacy comparator stays live, and Top-8 and the ranking weights are unchanged."""
    detector = (APP_ROOT / "app" / "opip" / "contracts" / "detector.py").read_text(encoding="utf-8")
    assert "episode_id is not None" in detector
    lifecycle_source = (APP_ROOT / "app" / "opip" / "opportunity_lifecycle.py").read_text(
        encoding="utf-8"
    )
    assert "def apply_claim(" in lifecycle_source
    feasibility_source = (APP_ROOT / "app" / "opip" / "feasibility.py").read_text(
        encoding="utf-8"
    )
    assert "def evaluate_feasibility(" in feasibility_source
    f6_source = F6_ENGINE_PATH.read_text(encoding="utf-8")
    assert "def evaluate_forecast(" in f6_source
    assert "portfolio_selector" not in f6_source
    assert "select_portfolio" not in f6_source

    ranking = PROFIT_RANKING_PATH.read_text(encoding="utf-8")
    assert "ECONOMIC_WEIGHT = 35.0" in ranking
    assert "def rank_profit_opportunities(" in ranking
    candidates = CANDIDATES_PATH.read_text(encoding="utf-8")
    assert "MIN_TECHNICAL_SCORE = 80" in candidates
    assert "MAX_CANDIDATES = 8" in candidates
    scan = SCAN_PATH.read_text(encoding="utf-8")
    assert "select_candidates = select_directional_candidates" in scan
    assert "rank_profit_opportunities(qualified_opportunities)" in scan

    # F5/F6 decisions remain identical after F7 evaluates them.
    feasibility = feasible_decision("AAAUSD")
    before = feasibility.to_dict()
    forecast = forecast_for("AAAUSD", 0.05)
    forecast_before = forecast.to_dict()
    select([candidate("AAAUSD", 0.05)])
    assert feasibility.to_dict() == before
    assert forecast.to_dict() == forecast_before


@pytest.mark.acceptance
def test_ac_024_allocation_policy_is_explicit() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-024: the per-candidate allocation is the lesser of the requested fraction, the policy cap and the liquidity capacity."""
    item = candidate("AAAUSD", 0.05, requested_capital_fraction=0.4, capacity_notional=10_000.0)
    capped = select(
        [item],
        configuration=policy(max_capital_fraction_per_candidate=0.15, max_portfolio_loss_fraction=1.0),
    )
    assert capped.allocations[0].allocated_capital == pytest.approx(150.0)

    requested = select(
        [item],
        configuration=policy(max_capital_fraction_per_candidate=0.5, max_portfolio_loss_fraction=1.0),
    )
    assert requested.allocations[0].allocated_capital == pytest.approx(400.0)

    assert not hasattr(policy(), "leverage")
    for source in F7_SOURCES:
        assert identifier_tokens(source).isdisjoint(
            {"kelly", "covariance", "risk_parity", "leverage"}
        )


@pytest.mark.acceptance
def test_ac_025_malformed_economics_fail_closed() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-025: malformed or non-finite economics are refused rather than silently repaired or ranked."""
    valid = candidate("AAAUSD", 0.05)
    with pytest.raises(PortfolioContractError):
        replace(valid, liquidity_capacity_notional=float("nan"))
    with pytest.raises(PortfolioContractError):
        replace(valid, liquidity_capacity_notional=0.0)
    with pytest.raises(PortfolioContractError):
        replace(valid, stop_loss_fraction=0.0)
    with pytest.raises(PortfolioContractError):
        replace(valid, requested_capital_fraction=1.5)
    with pytest.raises(PortfolioContractError):
        replace(valid, candidate_id="PCAND:forged")
    with pytest.raises(PortfolioContractError):
        PortfolioAllocation(
            candidate_id="PCAND:x",
            episode_id="OPEP:x",
            symbol="AAAUSD",
            direction=PortfolioDirection.LONG,
            allocated_capital=100.0,
            expected_net_dollars=float("inf"),
            expected_net_dollars_lower_bound=0.0,
            expected_net_dollars_upper_bound=1.0,
            expected_fill_probability=1.0,
            expected_no_fill_probability=0.0,
            loss_at_stop=1.0,
        )
    with pytest.raises(PortfolioContractError):
        PortfolioAllocation(
            candidate_id="PCAND:x",
            episode_id="OPEP:x",
            symbol="AAAUSD",
            direction=PortfolioDirection.LONG,
            allocated_capital=100.0,
            expected_net_dollars=1.0,
            expected_net_dollars_lower_bound=0.0,
            expected_net_dollars_upper_bound=1.0,
            expected_fill_probability=1.5,
            expected_no_fill_probability=0.0,
            loss_at_stop=1.0,
        )
    # A non-finite economic input is also refused at the F6 boundary.
    with pytest.raises(ForecastContractError):
        forecast_for("AAAUSD", float("nan"))


@pytest.mark.acceptance
def test_ac_026_panel_bounds_and_input_types() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-026: a non-panel input or a panel beyond the deterministic enumeration bound fails closed."""
    with pytest.raises(PortfolioContractError):
        select("not-a-panel")  # type: ignore[arg-type]
    with pytest.raises(PortfolioContractError):
        select([object()])  # type: ignore[list-item]

    too_many = [
        candidate(f"ASSET{index:02d}USD", 0.01) for index in range(MAX_SELECTOR_PANEL_SIZE + 1)
    ]
    with pytest.raises(PortfolioContractError):
        select(too_many)


@pytest.mark.acceptance
def test_ac_027_every_record_round_trips() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-027: every durable record round-trips and a tampered durable record fails closed."""
    decision = select(
        [candidate("AAAUSD", 0.06), candidate("BBBUSD", 0.04)],
        configuration=policy(max_portfolio_loss_fraction=1.0),
    )
    payload = decision.to_dict()
    assert PortfolioDecision.from_dict(payload) == decision
    assert PortfolioCandidate.from_dict(candidate("AAAUSD", 0.06).to_dict()) == candidate(
        "AAAUSD", 0.06
    )
    assert PortfolioCapitalState.from_dict(capital_state().to_dict()) == capital_state()
    assert PortfolioExposureSnapshot.from_dict(exposure(gross=5.0).to_dict()) == exposure(
        gross=5.0
    )
    assert PortfolioPolicy.from_dict(policy().to_dict()) == policy()
    assert PortfolioEvaluationWindow.from_dict(WINDOW.to_dict()) == WINDOW
    assert PortfolioAllocation.from_dict(
        decision.allocations[0].to_dict()
    ) == decision.allocations[0]

    tampered = dict(payload)
    tampered["expected_net_dollars"] = 999.0
    with pytest.raises(PortfolioContractError):
        PortfolioDecision.from_dict(tampered)
    tampered_status = dict(payload)
    tampered_status["status"] = "CASH_NO_TRADE"
    with pytest.raises(PortfolioContractError):
        PortfolioDecision.from_dict(tampered_status)


@pytest.mark.acceptance
def test_ac_028_no_score_to_probability_or_allocation() -> None:
    """ATDD-R3-F7-economic-portfolio-selector/AC-028: no ordinal score or confidence becomes a probability or an economic input, and no prior allocation module is duplicated."""
    for source in F7_SOURCES:
        tokens = identifier_tokens(source)
        assert tokens.isdisjoint(SCORE_PROMOTION_TOKENS), tokens & SCORE_PROMOTION_TOKENS
    assert "confidence" not in SELECTOR_SOURCE
    assert "committee" not in COMPARATOR_SOURCE.lower()

    # No second allocation tree or selector package is created.
    assert not (APP_ROOT / "app" / "opip" / "opportunity").exists()
    assert not (APP_ROOT / "app" / "opip" / "portfolio").exists()
    assert not (APP_ROOT / "app" / "opip" / "allocation").exists()

    decision = select([candidate("AAAUSD", 0.05)], configuration=policy(max_portfolio_loss_fraction=1.0))
    for forbidden in ("probability", "confidence", "score", "top_n", "quota"):
        assert not hasattr(decision, forbidden)
    assert decision.status is PortfolioStatus.SELECTED


# ---------------------------------------------------------------------------
# Additional unit and adversarial coverage (not increment acceptance criteria)
# ---------------------------------------------------------------------------


def test_selector_is_deterministic_across_repeated_calls() -> None:
    panel = [candidate("AAAUSD", 0.07), candidate("BBBUSD", 0.03)]
    first = select(panel)
    second = select(panel)
    assert first.to_dict() == second.to_dict()
    assert first.decision_id == second.decision_id


def test_decision_identity_binds_the_zero_clock() -> None:
    import time as clock

    panel = [candidate("AAAUSD", 0.05)]
    first = select(panel)
    real_time = clock.time
    clock.time = lambda: 1.0
    try:
        second = select(panel)
    finally:
        clock.time = real_time
    assert first.decision_id == second.decision_id


def test_missing_evidence_never_improves_rank() -> None:
    """A candidate without a usable forecast cannot outrank a usable one."""
    usable = candidate("AAAUSD", 0.01)
    unusable = replace(candidate("BBBUSD", 0.99), forecast=None, candidate_id="")
    decision = select([usable, unusable], configuration=policy(max_portfolio_loss_fraction=1.0))
    assert decision.status is PortfolioStatus.SELECTED
    assert decision.selected_candidate_ids == (usable.candidate_id,)
    assert decision.expected_net_dollars == pytest.approx(100.0 * 0.01)


def test_insufficient_available_capital_holds_cash() -> None:
    tiny = capital_state(0.5)
    decision = select(
        [candidate("AAAUSD", 0.05, capacity_notional=10_000.0)],
        capital=tiny,
        configuration=policy(max_portfolio_loss_fraction=1.0),
    )
    # The permitted allocation is 0.05 of half a dollar, which remains positive.
    assert decision.status is PortfolioStatus.SELECTED
    assert decision.allocations[0].allocated_capital == pytest.approx(0.05)


def test_existing_gross_exposure_blocks_new_capital() -> None:
    decision = select(
        [candidate("AAAUSD", 0.05)],
        portfolio_exposure=exposure(gross=1000.0, open_positions=1),
    )
    assert decision.status is PortfolioStatus.CASH_NO_TRADE
    assert decision.cash_reason is PortfolioCashReason.CONSTRAINTS_EXCLUDE_ALL_CANDIDATES


def test_reservation_plan_requires_matching_reservation_versions() -> None:
    plan = PortfolioReservationPlan(
        portfolio_version="PV:r3f7",
        window_identity=WINDOW.window_identity,
        created_at=EVAL,
        reservations=(),
    )
    assert plan.planned_capital == 0.0
    with pytest.raises(PortfolioContractError):
        PortfolioReservationPlan(
            portfolio_version="PV:r3f7",
            window_identity=WINDOW.window_identity,
            created_at=EVAL,
            reservations=(
                PortfolioReservation(
                    candidate_id="PCAND:x",
                    symbol="AAAUSD",
                    direction=PortfolioDirection.LONG,
                    reserved_capital=10.0,
                    portfolio_version="PV:other",
                    created_at=EVAL,
                    expires_at=EVAL + timedelta(hours=1),
                ),
            ),
        )


def test_diagnostics_are_diagnostics_only() -> None:
    decision = select([candidate("AAAUSD", 0.05)], configuration=policy(max_portfolio_loss_fraction=1.0))
    assert decision.diagnostics["selected_position_count"] == 1.0
    assert 0.0 < decision.diagnostics["gross_allocated_fraction"] <= 1.0
    with pytest.raises(TypeError):
        decision.diagnostics["tampered"] = 1.0


def test_comparison_never_mutates_the_panel() -> None:
    panel = [candidate("AAAUSD", 0.06)]
    before = [item.to_dict() for item in panel]
    build_frozen_legacy_comparison(panel, capital_state=capital_state(), window=WINDOW)
    assert [item.to_dict() for item in panel] == before


def test_legacy_active_positions_are_deterministic() -> None:
    panel = [candidate("AAAUSD", 0.06), candidate("BBBUSD", 0.05)]
    first = build_frozen_legacy_comparison(panel, capital_state=capital_state(), window=WINDOW)
    second = build_frozen_legacy_comparison(panel, capital_state=capital_state(), window=WINDOW)
    assert first.to_dict() == second.to_dict()

    blocked = build_frozen_legacy_comparison(
        panel,
        capital_state=capital_state(),
        window=WINDOW,
        active_positions=(
            LegacyActivePosition(
                symbol="AAAUSD", direction=PortfolioDirection.LONG, capital=100.0
            ),
        ),
    )
    assert blocked.risk_veto_reasons


# ---------------------------------------------------------------------------
# Review-finding regressions
# ---------------------------------------------------------------------------


def test_regression_comparator_same_direction_counts_existing_positions() -> None:
    """The live veto must see a plain direction token for existing positions."""
    panel = [
        candidate("AAAUSD", 0.06),
        candidate("BBBUSD", 0.05),
        candidate("CCCUSD", 0.04),
    ]
    result = build_frozen_legacy_comparison(
        panel,
        capital_state=capital_state(),
        window=WINDOW,
        active_positions=(
            LegacyActivePosition(
                symbol="ZZZUSD", direction=PortfolioDirection.LONG, capital=50.0
            ),
        ),
    )
    # One existing LONG plus the live default max_same_direction of 2 admits one more.
    assert len(result.risk_admitted_candidate_ids) == 1
    assert any(
        "same-direction" in reason for reason in result.risk_veto_reasons.values()
    )


def test_regression_each_reservation_uses_its_own_forecast_expiry() -> None:
    short = candidate("AAAUSD", 0.06, validity_seconds=3600)
    long = candidate("BBBUSD", 0.05, validity_seconds=7200)
    decision = select([short, long], configuration=policy(max_portfolio_loss_fraction=1.0))
    assert decision.reservation_plan is not None
    by_candidate = {
        reservation.candidate_id: reservation
        for reservation in decision.reservation_plan.reservations
    }
    assert by_candidate[short.candidate_id].expires_at == short.forecast.valid_until
    assert by_candidate[long.candidate_id].expires_at == long.forecast.valid_until
    assert (
        by_candidate[short.candidate_id].expires_at
        != by_candidate[long.candidate_id].expires_at
    )

    late = expire_reservations(
        decision.reservation_plan, at_time=EVAL + timedelta(hours=1, minutes=1)
    )
    states = {reservation.candidate_id: reservation.status for reservation in late.reservations}
    assert states[short.candidate_id] is ReservationStatus.EXPIRED
    assert states[long.candidate_id] is ReservationStatus.PLANNED


def test_regression_symbol_cap_sums_same_symbol_allocations() -> None:
    long = candidate(
        "AAAUSD", 0.06, direction=PortfolioDirection.LONG, requested_capital_fraction=0.3
    )
    short = candidate(
        "AAAUSD", 0.06, direction=PortfolioDirection.SHORT, requested_capital_fraction=0.3
    )
    decision = select(
        [long, short],
        configuration=policy(
            max_capital_fraction_per_candidate=0.3,
            max_symbol_exposure_fraction=0.5,
            max_portfolio_loss_fraction=1.0,
        ),
    )
    # Each allocation is 300; the 50% symbol cap (500) refuses the 600 pair.
    assert len(decision.allocations) == 1


def test_regression_negative_symbol_exposure_fails_closed() -> None:
    with pytest.raises(PortfolioContractError):
        exposure(symbol_exposure={"AAAUSD": -50.0})


def test_regression_future_snapshots_fail_closed() -> None:
    future = EVAL + timedelta(hours=1)
    with pytest.raises(PortfolioContractError):
        select([candidate("AAAUSD", 0.05)], capital=capital_state(as_of=future))
    with pytest.raises(PortfolioContractError):
        select([candidate("AAAUSD", 0.05)], portfolio_exposure=exposure(as_of=future))
    assert select([candidate("AAAUSD", 0.05)]).status is PortfolioStatus.SELECTED


def test_regression_partial_fill_residual_stays_reserved_and_adjustable() -> None:
    item = candidate("AAAUSD", 0.05)
    plan = select(
        [item], configuration=policy(max_portfolio_loss_fraction=1.0)
    ).reservation_plan
    assert plan is not None

    partial = adjust_reservation_for_fill(
        plan, candidate_id=item.candidate_id, filled_capital=40.0, at_time=EVAL
    )
    reservation = partial.reservations[0]
    assert reservation.status is ReservationStatus.FILL_ADJUSTED
    assert reservation.is_active is True
    assert partial.planned_capital == pytest.approx(60.0)

    again = adjust_reservation_for_fill(
        partial, candidate_id=item.candidate_id, filled_capital=10.0, at_time=EVAL
    )
    assert again.reservations[0].reserved_capital == pytest.approx(50.0)

    released = release_reservation(
        again,
        candidate_id=item.candidate_id,
        reason=ReservationReleaseReason.CANCELLED,
        at_time=EVAL,
    )
    assert released.planned_capital == 0.0

    expired = expire_reservations(partial, at_time=EVAL + timedelta(days=1))
    assert expired.reservations[0].status is ReservationStatus.EXPIRED

    fully_filled = adjust_reservation_for_fill(
        plan, candidate_id=item.candidate_id, filled_capital=100.0, at_time=EVAL
    )
    assert fully_filled.reservations[0].status is ReservationStatus.RELEASED
    assert fully_filled.planned_capital == 0.0


def test_regression_comparison_requires_a_panel_evaluated_decision() -> None:
    panel = [candidate("AAAUSD", 0.06)]
    legacy = build_frozen_legacy_comparison(
        panel, capital_state=capital_state(), window=WINDOW
    )
    abstention = select([])
    assert abstention.status is PortfolioStatus.INSUFFICIENT_EVIDENCE
    assert abstention.panel_fingerprint is None
    with pytest.raises(PortfolioContractError):
        build_portfolio_comparison(
            abstention, legacy, panel_fingerprint=legacy.panel_fingerprint
        )


def test_regression_decision_identity_binds_aggregate_fields() -> None:
    decision = select(
        [candidate("AAAUSD", 0.05)], configuration=policy(max_portfolio_loss_fraction=1.0)
    )
    for field, tampered in (
        ("expected_net_dollars_lower_bound", -999.0),
        ("expected_net_dollars_upper_bound", 999.0),
        ("loss_at_stop", 999.0),
        ("unallocated_capital", 0.0),
    ):
        payload = decision.to_dict()
        payload[field] = tampered
        with pytest.raises(PortfolioContractError):
            PortfolioDecision.from_dict(payload)


def test_regression_candidate_round_trip_rejects_forecast_id_drift() -> None:
    payload = candidate("AAAUSD", 0.05).to_dict()
    payload["forecast_decision_id"] = "FCST:forged"
    with pytest.raises(PortfolioContractError):
        PortfolioCandidate.from_dict(payload)


def test_regression_comparator_is_permutation_invariant() -> None:
    panel = [
        candidate("AAAUSD", 0.06),
        candidate("BBBUSD", 0.05),
        candidate("CCCUSD", 0.04),
    ]
    first = build_frozen_legacy_comparison(
        panel, capital_state=capital_state(), window=WINDOW
    )
    second = build_frozen_legacy_comparison(
        list(reversed(panel)), capital_state=capital_state(), window=WINDOW
    )
    assert first.to_dict() == second.to_dict()


def test_regression_existing_loss_counts_toward_the_loss_cap() -> None:
    blocked = select(
        [candidate("AAAUSD", 0.05, stop_loss_fraction=0.01)],
        portfolio_exposure=exposure(loss_at_stop=9.5, open_positions=1),
        configuration=policy(max_portfolio_loss_fraction=0.01),
    )
    assert blocked.status is PortfolioStatus.CASH_NO_TRADE
    allowed = select(
        [candidate("AAAUSD", 0.05, stop_loss_fraction=0.01)],
        portfolio_exposure=exposure(loss_at_stop=8.0, open_positions=1),
        configuration=policy(max_portfolio_loss_fraction=0.01),
    )
    assert allowed.status is PortfolioStatus.SELECTED


def test_regression_comparator_rejects_duplicate_panels() -> None:
    item = candidate("AAAUSD", 0.05)
    with pytest.raises(PortfolioContractError):
        build_frozen_legacy_comparison(
            [item, item], capital_state=capital_state(), window=WINDOW
        )
