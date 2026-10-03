"""R3 F6 Forecast Engine: executable acceptance and unit tests.

These tests satisfy the increment ``ATDD-R3-F6-forecast-engine``. Every
acceptance test cites ``ATDD-R3-F6-forecast-engine/AC-NNN`` so the increment's
traceability holds. No acceptance test is skipped.

Every fixture here is a deterministic literal built from real repository types
(``MarketSnapshot``, ``MarketDataValidation``, ``ExecutionValidation``,
``DetectorClaim``, the F4 ``OpportunityEpisode``, the F5 ``FeasibilityDecision``).
The tests read no network, no wall clock and no randomness, and they assert that
the engine under test does the same.

The synthetic ``SYNTHETIC_TEST_ONLY`` model is an unmistakable test fixture: it
exists only to exercise the mechanics of probability validation, scoring, horizon
handling and serialization. It is never evidence of calibration, and the tests
prove the production registry refuses it.
"""

from __future__ import annotations

import ast
import builtins
import math
import socket
import sys
import time
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip import feasibility as fseam  # noqa: E402
from app.opip import forecast as engine  # noqa: E402
from app.opip import forecast_evaluation as analysis  # noqa: E402
from app.opip import opportunity_lifecycle as lifecycle  # noqa: E402
from app.opip.contracts import (  # noqa: E402
    ENTRY_EXECUTION_OUTCOMES,
    FORECAST_DECISION_ID_PREFIX,
    FORECAST_DECISION_SCHEMA_VERSION,
    FORECAST_ENGINE_VERSION,
    FORECAST_EVALUATION_VERSION,
    FORECAST_MODEL_ARTIFACT_ID_PREFIX,
    FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION,
    FORECAST_POLICY_VERSION,
    POST_FILL_PATH_OUTCOMES,
    DistributionKind,
    EntryExecutionOutcome,
    FeasibilityPolicy,
    ForecastAbstentionReason,
    ForecastContractError,
    ForecastDecision,
    ForecastFeatureValue,
    ForecastHorizon,
    ForecastHorizonAnchor,
    ForecastInputVector,
    ForecastModelArtifact,
    ForecastModelKind,
    ForecastModelOutput,
    ForecastModelStatus,
    ForecastPolicy,
    ForecastRequest,
    ForecastStatus,
    ForecastUncertainty,
    OpportunityLifecyclePolicy,
    PostFillPathOutcome,
    ProbabilityDistribution,
    forecast_horizon_identity,
)
from app.opip.contracts.detector import (  # noqa: E402
    IGNITION_DETECTOR_VERSION,
    IGNITION_POLICY_VERSION,
    DetectorClaim,
)
from app.scanner.execution_validation import (  # noqa: E402
    COMPLETE,
    FRESH,
    VALID as EXECUTION_VALID,
    ExecutionValidation,
)
from app.scanner.market_data_validation import (  # noqa: E402
    PASS as MARKET_PASS,
    REJECT as MARKET_REJECT,
    MarketDataValidation,
)
from app.scanner.models import MarketSnapshot  # noqa: E402
from tests.test_atdd_scope_control import (  # noqa: E402
    global_pointer_pin_violations,
)

INCREMENT = "ATDD-R3-F6-forecast-engine"
FROZEN_F5_INCREMENT = "ATDD-R3-F5-feasibility-safety"

UTC = timezone.utc
CUTOFF = datetime(2026, 9, 11, 15, 1, tzinfo=UTC)
EVAL = datetime(2026, 9, 11, 15, 5, tzinfo=UTC)
TRAIN = datetime(2026, 8, 1, tzinfo=UTC)
RELEASED = datetime(2026, 9, 1, tzinfo=UTC)
CALIBRATION_REPORT_ID = "FEVAL:r3f6-fixture-report"

HORIZON = ForecastHorizon(
    entry_deadline_seconds=300,
    path_horizon_seconds=3600,
    validity_seconds=600,
)
FORECAST_POLICY = ForecastPolicy()
FEASIBILITY_POLICY = FeasibilityPolicy()
F4_POLICY = OpportunityLifecyclePolicy()

FORECAST_PATH = APP_ROOT / "app" / "opip" / "forecast.py"
EVALUATION_PATH = APP_ROOT / "app" / "opip" / "forecast_evaluation.py"
VOCABULARY_PATH = APP_ROOT / "app" / "opip" / "contracts" / "forecast.py"
CONTRACTS_INIT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "__init__.py"
ACTIVE_INCREMENT_PATH = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
COMPOSE_PATH = APP_ROOT / "docker-compose.yml"
RUN_CYCLE_PATH = APP_ROOT / "app" / "jobs" / "run_cycle.py"
SCAN_PATH = APP_ROOT / "app" / "jobs" / "scan_opportunities.py"

FORECAST_SOURCE = FORECAST_PATH.read_text(encoding="utf-8")
EVALUATION_SOURCE = EVALUATION_PATH.read_text(encoding="utf-8")
VOCABULARY_SOURCE = VOCABULARY_PATH.read_text(encoding="utf-8")
F6_SOURCES = (FORECAST_SOURCE, EVALUATION_SOURCE, VOCABULARY_SOURCE)

ALLOWED_IMPORT_PREFIXES = (
    "__future__",
    "app.opip.contracts",
    "app.opip.ml",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
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
    }
)

#: Field names whose value must never be promoted into a probability. The audit
#: below inspects identifiers/attributes (not docstrings) and asserts none occur.
SCORE_PROMOTION_TOKENS = frozenset(
    {
        "confidence",
        "score",
        "opportunity_score",
        "technical_score",
        "tradeability_score",
        "explosion_potential_score",
        "committee_confidence",
        "ai_confidence",
        "rank",
    }
)

#: Sentinel distinguishing "no uncertainty supplied" from an explicit ``None``.
_UNSET = object()


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


def scales_by_hundred(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.BinOp) or not isinstance(
            node.op, (ast.Mult, ast.Div)
        ):
            continue
        for side in (node.left, node.right):
            if isinstance(side, ast.Constant) and side.value == 100:
                return True
    return False


class ForbiddenIO:
    """Make filesystem and network access explode inside the with-block."""

    def __enter__(self) -> "ForbiddenIO":
        self._open = builtins.open
        self._socket = socket.socket
        self._create_connection = socket.create_connection

        def _no_filesystem(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("forecast performed filesystem access")

        def _no_network(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("forecast performed network access")

        builtins.open = _no_filesystem
        socket.socket = _no_network
        socket.create_connection = _no_network
        return self

    def __exit__(self, *_exc: Any) -> bool:
        builtins.open = self._open
        socket.socket = self._socket
        socket.create_connection = self._create_connection
        return False


# ---------------------------------------------------------------------------
# Deterministic fixtures built from real repository types
# ---------------------------------------------------------------------------


def build_claim(
    *,
    venue_instrument_id: str = "SOLUSD",
    instrument_version_id: str = "INSTR:kraken:SOL:USD:1",
) -> DetectorClaim:
    return DetectorClaim.create(
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=instrument_version_id,
        venue_instrument_id=venue_instrument_id,
        snapshot_id="SNAP:r3f6-fixture",
        detector_input_fingerprint="DETIN:r3f6-fixture",
        evaluation_cutoff=CUTOFF,
    )


def active_episode(claim: DetectorClaim | None = None):
    chosen = claim if claim is not None else build_claim()
    return lifecycle.apply_claim(chosen, None, chosen.evaluation_cutoff, F4_POLICY).episode


def market_validation(status: str = MARKET_PASS, qualified: bool = True) -> MarketDataValidation:
    return MarketDataValidation(
        status=status,
        qualified=qualified,
        warnings=[],
        rejection_reasons=[] if qualified else ["market data rejected"],
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


def snapshot(
    *,
    market: MarketDataValidation | None = None,
    execution: ExecutionValidation | None = None,
) -> MarketSnapshot:
    candidate = MarketSnapshot(
        symbol="SOLUSD",
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
        technical_score=72,
        trend="bullish",
    )
    candidate.trade_direction = "LONG"
    candidate.market_data_validation = market
    candidate.execution_validation = execution
    candidate.margin_validation_status = "NOT_REQUIRED"
    candidate.margin_eligible = False
    return candidate


def feasible_decision(episode=None):
    chosen = episode if episode is not None else active_episode()
    return fseam.evaluate_feasibility(
        chosen,
        snapshot(market=market_validation(), execution=execution_validation()),
        CUTOFF,
        FEASIBILITY_POLICY,
    )


def veto_decision():
    return fseam.evaluate_feasibility(
        active_episode(),
        snapshot(
            market=market_validation(status=MARKET_REJECT, qualified=False),
            execution=execution_validation(),
        ),
        CUTOFF,
        FEASIBILITY_POLICY,
    )


def insufficient_decision():
    return fseam.evaluate_feasibility(
        active_episode(),
        snapshot(market=None, execution=execution_validation()),
        CUTOFF,
        FEASIBILITY_POLICY,
    )


def input_vector(**overrides) -> ForecastInputVector:
    payload: dict[str, Any] = {
        "input_schema_id": "forecast-input-schema",
        "input_schema_version": "1",
        "source_snapshot_id": "SNAP:r3f6-fixture",
        "source_cutoff": CUTOFF,
        "horizon": HORIZON,
        "features": (
            ForecastFeatureValue("rsi", 55.0, CUTOFF),
            ForecastFeatureValue("atr_pct", 2.0, CUTOFF),
        ),
    }
    payload.update(overrides)
    return ForecastInputVector(**payload)


def artifact(**overrides) -> ForecastModelArtifact:
    inputs = input_vector()
    payload: dict[str, Any] = {
        "model_kind": ForecastModelKind.SYNTHETIC_TEST_ONLY,
        "model_family": "synthetic-deterministic",
        "model_version": "1",
        "model_status": ForecastModelStatus.CALIBRATED_SHADOW,
        "training_cutoff": TRAIN,
        "input_schema_id": inputs.input_schema_id,
        "input_schema_version": inputs.input_schema_version,
        "input_feature_names": inputs.feature_names,
        "horizon_contract": inputs.horizon,
        "training_population_id": "POP:train",
        "evaluation_population_id": "POP:eval",
        "calibration_report_id": CALIBRATION_REPORT_ID,
        "model_payload_ref": "sha256:" + "0" * 64,
        "parameters": {"coefficients": [0.1, 0.2], "intercept": -0.5},
        "created_at": RELEASED,
        "released_at": RELEASED,
    }
    payload.update(overrides)
    return ForecastModelArtifact.build(**payload)


class SyntheticTestModel:
    """A deterministic ``SYNTHETIC_TEST_ONLY`` adapter for mechanics only."""

    kind = ForecastModelKind.SYNTHETIC_TEST_ONLY

    def __init__(
        self,
        *,
        entry: dict[str, float] | None = None,
        path: dict[str, float] | None = None,
        expected_return: float = 0.012,
        uncertainty: ForecastUncertainty | None | object = _UNSET,
    ) -> None:
        self._entry = entry or {"NO_FILL": 0.2, "PARTIAL_FILL": 0.3, "FULL_FILL": 0.5}
        self._path = path or {
            "TARGET": 0.4,
            "STOP": 0.3,
            "TIMEOUT": 0.2,
            "RISK_EXIT": 0.1,
        }
        self._expected_return = expected_return
        if uncertainty is _UNSET:
            self._uncertainty: ForecastUncertainty | None = ForecastUncertainty(
                expected_return_lower_bound=-0.02,
                expected_return_upper_bound=0.05,
                coverage_level=0.9,
                interval_semantics="central predictive interval of realized net return",
                evidence_reference=CALIBRATION_REPORT_ID,
                sample_count=500,
                support_count=420,
            )
        else:
            self._uncertainty = uncertainty  # type: ignore[assignment]

    def predict(self, request: ForecastRequest) -> ForecastModelOutput:
        return ForecastModelOutput(
            entry_distribution=ProbabilityDistribution(
                kind=DistributionKind.ENTRY_EXECUTION, probabilities=dict(self._entry)
            ),
            post_fill_distribution=ProbabilityDistribution(
                kind=DistributionKind.POST_FILL_PATH, probabilities=dict(self._path)
            ),
            expected_return_unconditional=self._expected_return,
            uncertainty=self._uncertainty,
        )


def make_registry(adapter: Any = None) -> engine.TrustedForecastModelRegistry:
    chosen = adapter if adapter is not None else SyntheticTestModel()
    base = engine.TrustedForecastModelRegistry(adapters={}, allow_test_adapters=True)
    return base.register(chosen).vouch_for_report(CALIBRATION_REPORT_ID)


def forecast_decision(**overrides) -> ForecastDecision:
    payload: dict[str, Any] = {
        "episode": active_episode(),
        "feasibility": None,
        "inputs": input_vector(),
        "artifact": artifact(),
        "registry": None,
    }
    payload.update(overrides)
    episode = payload["episode"]
    feasibility = payload["feasibility"] or feasible_decision(episode)
    registry = payload["registry"]
    if registry is None:
        registry = make_registry()
    return engine.evaluate_forecast(
        episode,
        feasibility,
        payload["inputs"],
        EVAL,
        FORECAST_POLICY,
        model_artifact=payload["artifact"],
        registry=registry,
    )


def example_from(
    decision: ForecastDecision,
    *,
    entry_label: EntryExecutionOutcome | None = "FULL_FILL",
    entry_state: str = "RESOLVED",
    path_label: PostFillPathOutcome | None = "TARGET",
    path_state: str = "RESOLVED",
    realized_return: float | None = 0.02,
    return_state: str = "RESOLVED",
    label_available_at: datetime | None = None,
    fidelity: str = "A",
    example_id: str = "EX1",
) -> analysis.ForecastEvaluationExample:
    return analysis.ForecastEvaluationExample(
        example_id=example_id,
        forecast_decision_id=decision.decision_id,
        model_artifact_id=decision.model_artifact_id,
        prediction_cutoff=EVAL,
        input_fingerprint=decision.input_fingerprint,
        status=decision.status,
        entry_distribution=decision.entry_distribution,
        post_fill_distribution=decision.post_fill_distribution,
        expected_return_unconditional=decision.expected_return_unconditional,
        expected_return_conditional_on_fill=decision.expected_return_conditional_on_fill,
        uncertainty=decision.uncertainty,
        horizon=decision.horizon,
        entry_outcome_label=entry_label,
        entry_label_state=entry_state,
        post_fill_outcome_label=path_label,
        post_fill_label_state=path_state,
        realized_net_return=realized_return,
        realized_return_label_state=return_state,
        label_available_at=label_available_at or (EVAL + timedelta(hours=2)),
        label_cutoff=(EVAL + timedelta(hours=3)) if label_available_at is None else None,
        fidelity=fidelity,
    )


# ---------------------------------------------------------------------------
# AC-001 .. AC-036
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_vocabulary_is_exact() -> None:
    """ATDD-R3-F6-forecast-engine/AC-001: the F6 status, entry, path, model-status and version vocabularies are exactly the ratified tokens."""
    assert [token.value for token in ForecastStatus] == [
        "FORECAST",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert [token.value for token in EntryExecutionOutcome] == [
        "NO_FILL",
        "PARTIAL_FILL",
        "FULL_FILL",
    ]
    assert [token.value for token in PostFillPathOutcome] == [
        "TARGET",
        "STOP",
        "TIMEOUT",
        "RISK_EXIT",
    ]
    assert [token.value for token in ForecastModelStatus] == [
        "RESEARCH_ONLY",
        "CALIBRATED_SHADOW",
    ]
    assert [token.value for token in ForecastHorizonAnchor] == ["FIRST_FILL"]
    assert FORECAST_DECISION_SCHEMA_VERSION == "forecast-decision-v1"
    assert FORECAST_ENGINE_VERSION == "forecast-engine-v1"
    assert FORECAST_POLICY_VERSION == "forecast-shadow-policy-v1"
    assert FORECAST_MODEL_ARTIFACT_SCHEMA_VERSION == "forecast-model-artifact-v1"
    assert FORECAST_EVALUATION_VERSION == "forecast-evaluation-v1"
    assert FORECAST_DECISION_ID_PREFIX == "FCST"
    assert FORECAST_MODEL_ARTIFACT_ID_PREFIX == "FMOD"
    assert list(ENTRY_EXECUTION_OUTCOMES) == ["NO_FILL", "PARTIAL_FILL", "FULL_FILL"]
    assert list(POST_FILL_PATH_OUTCOMES) == ["TARGET", "STOP", "TIMEOUT", "RISK_EXIT"]
    for token in ForecastStatus:
        assert token.value not in {
            "PASS",
            "FAIL",
            "VETO",
            "WATCH",
            "APPROVE",
            "REJECT",
        }
    with pytest.raises(ForecastContractError):
        ProbabilityDistribution(
            kind="BOGUS", probabilities={"NO_FILL": 1.0}
        )


@pytest.mark.acceptance
def test_ac_002_requires_feasible_f5() -> None:
    """ATDD-R3-F6-forecast-engine/AC-002: F6 consumes only an F5 decision whose disposition is FEASIBLE; VETO and INSUFFICIENT_EVIDENCE fail closed with no lost attribution."""
    episode = active_episode()
    inputs = input_vector()

    feasible = feasible_decision(episode)
    decision = engine.evaluate_forecast(
        episode, feasible, inputs, EVAL, FORECAST_POLICY
    )
    assert decision.status is ForecastStatus.INSUFFICIENT_EVIDENCE

    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode, veto_decision(), inputs, EVAL, FORECAST_POLICY
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode, insufficient_decision(), inputs, EVAL, FORECAST_POLICY
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(episode, object(), inputs, EVAL, FORECAST_POLICY)

    # An upstream veto is never converted into an F6 abstention.
    assert veto_decision().disposition.value == "VETO"


@pytest.mark.acceptance
def test_ac_003_lineage_retained_and_immutable() -> None:
    """ATDD-R3-F6-forecast-engine/AC-003: the F4 episode and F5 decision lineage is retained immutably and a mismatched lineage fails closed."""
    episode = active_episode()
    feasibility = feasible_decision(episode)
    inputs = input_vector()
    decision = engine.evaluate_forecast(episode, feasibility, inputs, EVAL, FORECAST_POLICY)

    assert decision.episode_id == episode.episode_id
    assert decision.feasibility_decision_id == feasibility.decision_id
    assert decision.input_fingerprint == inputs.input_fingerprint
    assert decision.evaluation_time == EVAL

    # F6 mints no episode and mutates no F4/F5 state.
    assert episode.episode_id.startswith("OPEP:")
    assert episode.defer_deadline is None
    assert decision.episode_id != decision.feasibility_decision_id

    with pytest.raises(FrozenInstanceError):
        decision.episode_id = "OPEP:forged"  # type: ignore[misc]

    # A foreign episode with a different venue instrument cannot reuse the
    # decision's lineage.
    foreign = active_episode(build_claim(venue_instrument_id="XBTUSD"))
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(foreign, feasibility, inputs, EVAL, FORECAST_POLICY)


@pytest.mark.acceptance
def test_ac_004_evaluation_time_explicit_utc() -> None:
    """ATDD-R3-F6-forecast-engine/AC-004: evaluation_time must be explicit aware UTC; naive or non-datetime input fails closed and no clock is read."""
    episode = active_episode()
    feasibility = feasible_decision(episode)
    inputs = input_vector()

    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode, feasibility, inputs, datetime(2026, 9, 11, 15, 5), FORECAST_POLICY
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode, feasibility, inputs, "2026-09-11T15:05:00Z", FORECAST_POLICY
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(episode, feasibility, inputs, 1757600000.0, FORECAST_POLICY)

    offset = timezone(timedelta(hours=-4))
    decision = engine.evaluate_forecast(
        episode, feasibility, inputs, EVAL.astimezone(offset), FORECAST_POLICY
    )
    assert decision.evaluation_time == EVAL
    assert decision.evaluation_time.utcoffset() == timedelta(0)

    for source in F6_SOURCES:
        assert called_attributes(source).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)
        assert imported_modules(source).isdisjoint({"time", "random", "uuid", "os"})


@pytest.mark.acceptance
def test_ac_005_input_fingerprint_deterministic() -> None:
    """ATDD-R3-F6-forecast-engine/AC-005: the input fingerprint is a deterministic FIN: function of the schema, cutoff, horizon and normalized feature values, and a forged fingerprint fails closed."""
    first = input_vector()
    second = input_vector()
    assert first.input_fingerprint == second.input_fingerprint
    assert first.input_fingerprint.startswith("FIN:")
    assert first.input_schema_fingerprint.startswith("FISCH:")

    changed_value = input_vector(
        features=(
            ForecastFeatureValue("rsi", 61.0, CUTOFF),
            ForecastFeatureValue("atr_pct", 2.0, CUTOFF),
        )
    )
    assert changed_value.input_fingerprint != first.input_fingerprint

    changed_horizon = input_vector(
        horizon=ForecastHorizon(
            entry_deadline_seconds=300, path_horizon_seconds=7200, validity_seconds=600
        )
    )
    assert changed_horizon.input_fingerprint != first.input_fingerprint

    with pytest.raises(ForecastContractError):
        input_vector(input_fingerprint="FIN:forged")
    # Round-trips without a hidden value.
    assert ForecastInputVector.from_dict(first.to_dict()) == first


@pytest.mark.acceptance
def test_ac_006_model_artifact_identity_deterministic() -> None:
    """ATDD-R3-F6-forecast-engine/AC-006: the model artifact identity is a deterministic FMOD: function of its provenance, a forged identity fails closed, and created/released metadata does not influence it."""
    first = artifact()
    second = artifact()
    assert first.artifact_id == second.artifact_id
    assert first.artifact_id.startswith("FMOD:")

    assert artifact(parameters={"intercept": 9.0}).artifact_id != first.artifact_id
    assert (
        artifact(model_version="2").artifact_id != first.artifact_id
    )

    # created/released metadata never influences forecast semantics (or identity).
    restamped = artifact(
        created_at=RELEASED + timedelta(days=1),
        released_at=RELEASED + timedelta(days=1),
    )
    assert restamped.artifact_id == first.artifact_id

    # The declared expiry is bound into the identity because it changes the
    # artifact's eligibility, unlike the informational created/released metadata.
    assert (
        artifact(expires_at=RELEASED + timedelta(days=30)).artifact_id
        != first.artifact_id
    )

    with pytest.raises(ForecastContractError):
        replace(first, artifact_id="FMOD:forged")
    with pytest.raises(ForecastContractError):
        ForecastModelArtifact.build(
            model_kind=ForecastModelKind.SYNTHETIC_TEST_ONLY,
            model_family="synthetic-deterministic",
            model_version="1",
            model_status=ForecastModelStatus.CALIBRATED_SHADOW,
            training_cutoff=TRAIN,
            input_schema_id="forecast-input-schema",
            input_schema_version="1",
            input_feature_names=("rsi", "atr_pct"),
            horizon_contract=HORIZON,
            training_population_id="POP:train",
            evaluation_population_id="POP:eval",
            calibration_report_id=CALIBRATION_REPORT_ID,
            model_payload_ref="",
            parameters={},
            created_at=RELEASED,
            released_at=RELEASED,
        )
    assert ForecastModelArtifact.from_dict(first.to_dict()) == first


@pytest.mark.acceptance
def test_ac_007_decision_identity_deterministic() -> None:
    """ATDD-R3-F6-forecast-engine/AC-007: the decision identity is a deterministic FCST: function binding schema, lineage, input, time, model, horizon, engine and policy, and a forged identity fails closed."""
    decision = forecast_decision()
    repeat = forecast_decision()
    assert decision.decision_id == repeat.decision_id
    assert decision.decision_id.startswith("FCST:")
    assert decision.horizon_identity == forecast_horizon_identity(HORIZON)

    tampered = decision.to_dict()
    tampered["expected_return_unconditional"] = 0.5
    with pytest.raises(ForecastContractError):
        ForecastDecision.from_dict(tampered)
    tampered_entry = decision.to_dict()
    tampered_entry["entry_distribution"]["probabilities"]["FULL_FILL"] = 0.9
    with pytest.raises(ForecastContractError):
        ForecastDecision.from_dict(tampered_entry)
    forged = decision.to_dict()
    forged["episode_id"] = "OPEP:forged"
    with pytest.raises(ForecastContractError):
        ForecastDecision.from_dict(forged)

    with pytest.raises(ForecastContractError):
        replace(decision, decision_id="FCST:forged")
    with pytest.raises(ForecastContractError):
        replace(decision, input_fingerprint="FIN:forged")

    # No clock participates.
    real_time = time.time
    time.time = lambda: 1.0
    try:
        early = forecast_decision()
    finally:
        time.time = real_time
    assert early.decision_id == decision.decision_id

    # Abstentions are deterministic and auditable: a different reason changes id.
    episode = active_episode()
    feasibility = feasible_decision(episode)
    inputs = input_vector()
    abstain_a = engine.evaluate_forecast(episode, feasibility, inputs, EVAL, FORECAST_POLICY)
    abstain_b = engine.evaluate_forecast(episode, feasibility, inputs, EVAL, FORECAST_POLICY)
    assert abstain_a.decision_id == abstain_b.decision_id
    assert abstain_a.abstention_reason is ForecastAbstentionReason.NO_CALIBRATED_MODEL
    assert ForecastDecision.from_dict(abstain_a.to_dict()) == abstain_a


@pytest.mark.acceptance
def test_ac_008_no_model_abstains() -> None:
    """ATDD-R3-F6-forecast-engine/AC-008: with no registered calibrated model the engine returns a deterministic INSUFFICIENT_EVIDENCE decision with reason NO_CALIBRATED_MODEL."""
    episode = active_episode()
    feasibility = feasible_decision(episode)
    decision = engine.evaluate_forecast(
        episode, feasibility, input_vector(), EVAL, FORECAST_POLICY
    )
    assert decision.status is ForecastStatus.INSUFFICIENT_EVIDENCE
    assert decision.abstention_reason is ForecastAbstentionReason.NO_CALIBRATED_MODEL
    assert decision.model_artifact_id is None
    assert decision.entry_distribution is None
    assert decision.horizon is None
    assert decision.valid_until is None


@pytest.mark.acceptance
def test_ac_009_research_only_abstains() -> None:
    """ATDD-R3-F6-forecast-engine/AC-009: a RESEARCH_ONLY artifact is structurally valid but unsuitable, so the engine abstains with MODEL_NOT_CALIBRATED_FOR_SHADOW_AUTHORITY."""
    research = artifact(model_status=ForecastModelStatus.RESEARCH_ONLY)
    assert research.model_status is ForecastModelStatus.RESEARCH_ONLY
    episode = active_episode()
    feasibility = feasible_decision(episode)
    decision = engine.evaluate_forecast(
        episode,
        feasibility,
        input_vector(),
        EVAL,
        FORECAST_POLICY,
        model_artifact=research,
        registry=make_registry(),
    )
    assert decision.status is ForecastStatus.INSUFFICIENT_EVIDENCE
    assert (
        decision.abstention_reason
        is ForecastAbstentionReason.MODEL_NOT_CALIBRATED_FOR_SHADOW_AUTHORITY
    )
    assert decision.model_artifact_id == research.artifact_id
    assert decision.entry_distribution is None


@pytest.mark.acceptance
def test_ac_010_synthetic_model_excluded_from_production() -> None:
    """ATDD-R3-F6-forecast-engine/AC-010: the synthetic test model cannot enter the production registry, which stays empty."""
    production = engine.PRODUCTION_MODEL_REGISTRY
    assert production.registered_adapter_count == 0
    assert production.adapters == {}
    assert production.known_evaluation_report_ids == frozenset()
    assert production.allow_test_adapters is False

    with pytest.raises(ForecastContractError):
        production.register(SyntheticTestModel())
    with pytest.raises(ForecastContractError):
        engine.TrustedForecastModelRegistry(
            adapters={ForecastModelKind.SYNTHETIC_TEST_ONLY: SyntheticTestModel()}
        )

    # A CALIBRATED_SHADOW artifact cannot produce a FORECAST through production.
    episode = active_episode()
    feasibility = feasible_decision(episode)
    decision = engine.evaluate_forecast(
        episode,
        feasibility,
        input_vector(),
        EVAL,
        FORECAST_POLICY,
        model_artifact=artifact(),
        registry=production,
    )
    assert decision.status is ForecastStatus.INSUFFICIENT_EVIDENCE
    assert (
        decision.abstention_reason
        is ForecastAbstentionReason.CALIBRATION_REPORT_UNTRUSTED
    )
    assert engine.PRODUCTION_MODEL_REGISTRY.registered_adapter_count == 0


@pytest.mark.acceptance
def test_ac_011_calibrated_shadow_test_artifact_forecasts() -> None:
    """ATDD-R3-F6-forecast-engine/AC-011: a structurally valid, compatible CALIBRATED_SHADOW test artifact with a registered trusted adapter produces a FORECAST in tests only."""
    episode = active_episode()
    feasibility = feasible_decision(episode)
    inputs = input_vector()
    with ForbiddenIO():
        decision = engine.evaluate_forecast(
            episode,
            feasibility,
            inputs,
            EVAL,
            FORECAST_POLICY,
            model_artifact=artifact(),
            registry=make_registry(),
        )
    assert decision.status is ForecastStatus.FORECAST
    assert decision.entry_distribution is not None
    assert decision.post_fill_distribution is not None
    assert decision.expected_return_unconditional == 0.012
    assert decision.uncertainty is not None
    assert decision.horizon == HORIZON
    assert decision.valid_until == EVAL + timedelta(seconds=600)
    assert decision.evidence_references == (CALIBRATION_REPORT_ID,)

    # The production registry remains empty: the FORECAST path is tests-only.
    assert engine.PRODUCTION_MODEL_REGISTRY.registered_adapter_count == 0


@pytest.mark.acceptance
def test_ac_012_entry_outcomes_separate() -> None:
    """ATDD-R3-F6-forecast-engine/AC-012: entry-execution outcomes are modeled as a separate NO_FILL/PARTIAL_FILL/FULL_FILL distribution and never mixed with the post-fill family."""
    decision = forecast_decision()
    entry = decision.entry_distribution
    assert entry is not None
    assert entry.kind is DistributionKind.ENTRY_EXECUTION
    assert set(entry.probabilities) == {"NO_FILL", "PARTIAL_FILL", "FULL_FILL"}
    assert "TARGET" not in entry.probabilities

    with pytest.raises(ForecastContractError):
        ProbabilityDistribution(
            kind=DistributionKind.ENTRY_EXECUTION,
            probabilities={"NO_FILL": 0.2, "PARTIAL_FILL": 0.3, "TARGET": 0.5},
        )
    # A fill probability is never collapsed into a single win probability.
    assert abs(sum(entry.probabilities.values()) - 1.0) <= 1e-9
    assert decision.post_fill_distribution is not entry


@pytest.mark.acceptance
def test_ac_013_post_fill_path_separate() -> None:
    """ATDD-R3-F6-forecast-engine/AC-013: the conditional post-fill path is a separate TARGET/STOP/TIMEOUT/RISK_EXIT distribution and is never merged with the entry family."""
    decision = forecast_decision()
    path = decision.post_fill_distribution
    assert path is not None
    assert path.kind is DistributionKind.POST_FILL_PATH
    assert set(path.probabilities) == {"TARGET", "STOP", "TIMEOUT", "RISK_EXIT"}
    assert "FULL_FILL" not in path.probabilities

    with pytest.raises(ForecastContractError):
        ProbabilityDistribution(
            kind=DistributionKind.POST_FILL_PATH,
            probabilities={"TARGET": 0.4, "STOP": 0.3, "NO_FILL": 0.3, "RISK_EXIT": 0.0},
        )
    # P(TARGET) is not P(positive return): they are distinct fields.
    assert "positive_return" not in path.probabilities
    assert decision.expected_return_unconditional is not None


@pytest.mark.acceptance
def test_ac_014_probability_range_and_sum() -> None:
    """ATDD-R3-F6-forecast-engine/AC-014: a probability is finite, not a bool, within [0, 1], the mass must sum to one, and invalid mass is refused rather than normalized."""
    good = {"NO_FILL": 0.2, "PARTIAL_FILL": 0.3, "FULL_FILL": 0.5}
    assert ProbabilityDistribution(
        kind=DistributionKind.ENTRY_EXECUTION, probabilities=good
    )
    for bad in (-0.01, 1.01, float("nan"), float("inf")):
        with pytest.raises(ForecastContractError):
            ProbabilityDistribution(
                kind=DistributionKind.ENTRY_EXECUTION,
                probabilities={"NO_FILL": bad, "PARTIAL_FILL": 0.5, "FULL_FILL": 0.5},
            )
    with pytest.raises(ForecastContractError):
        ProbabilityDistribution(
            kind=DistributionKind.ENTRY_EXECUTION,
            probabilities={"NO_FILL": True, "PARTIAL_FILL": 0.3, "FULL_FILL": 0.7},
        )
    with pytest.raises(ForecastContractError):
        ProbabilityDistribution(
            kind=DistributionKind.ENTRY_EXECUTION,
            probabilities={"NO_FILL": 0.2, "PARTIAL_FILL": 0.3, "FULL_FILL": 0.4},
        )
    with pytest.raises(ForecastContractError):
        ProbabilityDistribution(
            kind=DistributionKind.POST_FILL_PATH,
            probabilities={"TARGET": 0.4, "STOP": 0.3, "TIMEOUT": 0.2, "RISK_EXIT": 0.2},
        )
    with pytest.raises(ForecastContractError):
        ProbabilityDistribution(
            kind=DistributionKind.ENTRY_EXECUTION,
            probabilities={"NO_FILL": 0.2, "PARTIAL_FILL": 0.8},
        )


@pytest.mark.acceptance
def test_ac_015_expected_return_finite_and_units() -> None:
    """ATDD-R3-F6-forecast-engine/AC-015: the expected net return is a finite dimensionless decimal (0.012 = +1.2 percent), non-finite values are refused, and there is no percent conversion."""
    decision = forecast_decision()
    assert decision.expected_return_unconditional == 0.012
    assert decision.to_dict()["expected_return_unconditional"] == 0.012

    for bad in (float("nan"), float("inf"), float("-inf"), True):
        with pytest.raises(ForecastContractError):
            ForecastModelOutput(
                entry_distribution=decision.entry_distribution,
                post_fill_distribution=decision.post_fill_distribution,
                expected_return_unconditional=bad,
            )
    with pytest.raises(ForecastContractError):
        ForecastModelOutput(
            entry_distribution=decision.entry_distribution,
            post_fill_distribution=decision.post_fill_distribution,
            expected_return_unconditional=0.01,
            expected_return_conditional_on_fill=float("nan"),
        )
    for source in F6_SOURCES:
        assert scales_by_hundred(source) is False

    # A conditional-on-fill return is validated exactly like the unconditional one.
    for bad_conditional in (float("inf"), float("nan"), True, "0.01"):
        with pytest.raises(ForecastContractError):
            replace(decision, expected_return_conditional_on_fill=bad_conditional)


@pytest.mark.acceptance
def test_ac_016_uncertainty_required_and_valid() -> None:
    """ATDD-R3-F6-forecast-engine/AC-016: a FORECAST requires a valid explicit uncertainty; a missing or invalid interval is refused and never defaulted to zero width."""
    episode = active_episode()
    feasibility = feasible_decision(episode)
    inputs = input_vector()

    no_uncertainty = SyntheticTestModel(uncertainty=None)
    decision = engine.evaluate_forecast(
        episode,
        feasibility,
        inputs,
        EVAL,
        FORECAST_POLICY,
        model_artifact=artifact(),
        registry=engine.TrustedForecastModelRegistry(
            adapters={}, allow_test_adapters=True
        )
        .register(no_uncertainty)
        .vouch_for_report(CALIBRATION_REPORT_ID),
    )
    assert decision.status is ForecastStatus.INSUFFICIENT_EVIDENCE
    assert decision.abstention_reason is ForecastAbstentionReason.UNCERTAINTY_UNAVAILABLE
    assert decision.uncertainty is None

    with pytest.raises(TypeError):
        ForecastUncertainty()  # type: ignore[call-arg]
    with pytest.raises(ForecastContractError):
        ForecastUncertainty(
            expected_return_lower_bound=0.05,
            expected_return_upper_bound=-0.02,
            coverage_level=0.9,
            interval_semantics="central interval",
            evidence_reference=CALIBRATION_REPORT_ID,
        )
    with pytest.raises(ForecastContractError):
        ForecastUncertainty(
            expected_return_lower_bound=-0.02,
            expected_return_upper_bound=0.05,
            coverage_level=1.5,
            interval_semantics="central interval",
            evidence_reference=CALIBRATION_REPORT_ID,
        )

    # A decision whose expected return lies outside the interval fails closed.
    good = forecast_decision()
    outside = ForecastUncertainty(
        expected_return_lower_bound=0.2,
        expected_return_upper_bound=0.4,
        coverage_level=0.9,
        interval_semantics="central interval",
        evidence_reference=CALIBRATION_REPORT_ID,
    )
    with pytest.raises(ForecastContractError):
        replace(good, uncertainty=outside)


@pytest.mark.acceptance
def test_ac_017_horizon_explicit_no_default() -> None:
    """ATDD-R3-F6-forecast-engine/AC-017: the horizon contract is explicit with no numeric default; zero or negative durations are refused and an abstention invents no horizon."""
    with pytest.raises(TypeError):
        ForecastHorizon()  # type: ignore[call-arg]
    for bad in (0, -1):
        with pytest.raises(ForecastContractError):
            ForecastHorizon(
                entry_deadline_seconds=bad,
                path_horizon_seconds=3600,
                validity_seconds=600,
            )
        with pytest.raises(ForecastContractError):
            ForecastHorizon(
                entry_deadline_seconds=300,
                path_horizon_seconds=bad,
                validity_seconds=600,
            )
        with pytest.raises(ForecastContractError):
            ForecastHorizon(
                entry_deadline_seconds=300,
                path_horizon_seconds=3600,
                validity_seconds=bad,
            )
    decision = forecast_decision()
    assert decision.horizon == HORIZON
    assert decision.horizon.to_dict()["validity_seconds"] == 600
    abstain = engine.evaluate_forecast(
        active_episode(), feasible_decision(), input_vector(), EVAL, FORECAST_POLICY
    )
    assert abstain.horizon is None
    assert abstain.valid_until is None


@pytest.mark.acceptance
def test_ac_018_path_horizon_anchored_first_fill() -> None:
    """ATDD-R3-F6-forecast-engine/AC-018: the path horizon is anchored to FIRST_FILL, additional fills do not restart it, and an unknown anchor fails closed."""
    assert HORIZON.anchor is ForecastHorizonAnchor.FIRST_FILL
    assert HORIZON.to_dict()["anchor"] == "FIRST_FILL"
    with pytest.raises(ForecastContractError):
        ForecastHorizon(
            entry_deadline_seconds=300,
            path_horizon_seconds=3600,
            validity_seconds=600,
            anchor="LAST_FILL",
        )
    # The anchor token is FIRST_FILL only; there is no restart-on-later-fill state.
    assert [token.value for token in ForecastHorizonAnchor] == ["FIRST_FILL"]
    assert "RESTART" not in VOCABULARY_SOURCE


@pytest.mark.acceptance
def test_ac_019_validity_expiry_explicit() -> None:
    """ATDD-R3-F6-forecast-engine/AC-019: valid_until is derived deterministically from evaluation_time plus the declared validity, and a non-positive validity or an expired valid_until fails closed."""
    decision = forecast_decision()
    assert decision.valid_until == EVAL + timedelta(seconds=HORIZON.validity_seconds)
    assert decision.valid_until > decision.evaluation_time
    with pytest.raises(ForecastContractError):
        replace(decision, valid_until=EVAL)
    with pytest.raises(ForecastContractError):
        replace(decision, valid_until=EVAL - timedelta(seconds=1))
    # valid_until must equal the horizon-derived instant exactly, not merely be
    # later than the evaluation time.
    with pytest.raises(ForecastContractError):
        replace(
            decision,
            valid_until=EVAL + timedelta(seconds=HORIZON.validity_seconds + 1),
        )
    # No hidden default: an abstention carries no valid_until.
    abstain = engine.evaluate_forecast(
        active_episode(), feasible_decision(), input_vector(), EVAL, FORECAST_POLICY
    )
    assert abstain.valid_until is None


@pytest.mark.acceptance
def test_ac_020_schema_compatibility_enforced() -> None:
    """ATDD-R3-F6-forecast-engine/AC-020: input/model schema, horizon, expiry and version compatibility is enforced as a structural invariant, while an unknown model kind fails closed."""
    episode = active_episode()
    feasibility = feasible_decision(episode)
    inputs = input_vector()
    registry = make_registry()

    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode,
            feasibility,
            inputs,
            EVAL,
            FORECAST_POLICY,
            model_artifact=artifact(input_schema_id="other-schema"),
            registry=registry,
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode,
            feasibility,
            inputs,
            EVAL,
            FORECAST_POLICY,
            model_artifact=artifact(input_feature_names=("rsi",)),
            registry=registry,
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode,
            feasibility,
            inputs,
            EVAL,
            FORECAST_POLICY,
            model_artifact=artifact(
                horizon_contract=ForecastHorizon(
                    entry_deadline_seconds=300,
                    path_horizon_seconds=7200,
                    validity_seconds=600,
                )
            ),
            registry=registry,
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode,
            feasibility,
            inputs,
            EVAL,
            FORECAST_POLICY,
            model_artifact=artifact(expires_at=EVAL - timedelta(seconds=1)),
            registry=registry,
        )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode,
            feasibility,
            inputs,
            EVAL,
            FORECAST_POLICY,
            model_artifact=artifact(training_cutoff=EVAL + timedelta(days=1)),
            registry=registry,
        )
    with pytest.raises(ForecastContractError):
        artifact(model_kind="BOGUS")
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode, feasibility, inputs, EVAL, FORECAST_POLICY, model_artifact=object()
        )


@pytest.mark.acceptance
def test_ac_021_future_feature_rejected() -> None:
    """ATDD-R3-F6-forecast-engine/AC-021: a feature or source cutoff that was not available at the evaluation time is future leakage and fails closed."""
    episode = active_episode()
    feasibility = feasible_decision(episode)

    # A feature that became available after the vector's declared source cutoff is
    # leakage with respect to that snapshot and is refused at construction, whether
    # or not it also post-dates the evaluation time.
    with pytest.raises(ForecastContractError):
        input_vector(
            features=(
                ForecastFeatureValue("rsi", 55.0, CUTOFF),
                ForecastFeatureValue("atr_pct", 2.0, CUTOFF + timedelta(seconds=1)),
            )
        )
    with pytest.raises(ForecastContractError):
        input_vector(
            features=(
                ForecastFeatureValue("rsi", 55.0, CUTOFF),
                ForecastFeatureValue("atr_pct", 2.0, EVAL + timedelta(seconds=1)),
            )
        )

    # A source cutoff after the evaluation time is future leakage and fails closed.
    future_cutoff = input_vector(source_cutoff=EVAL + timedelta(seconds=1))
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode, feasibility, future_cutoff, EVAL, FORECAST_POLICY
        )

    # An upstream F5 decision that post-dates the forecast instant is refused.
    late_feasibility = fseam.evaluate_feasibility(
        episode,
        snapshot(market=market_validation(), execution=execution_validation()),
        EVAL + timedelta(hours=1),
        FEASIBILITY_POLICY,
    )
    with pytest.raises(ForecastContractError):
        engine.evaluate_forecast(
            episode, late_feasibility, input_vector(), EVAL, FORECAST_POLICY
        )

    # A point-in-time input is accepted.
    ok = engine.evaluate_forecast(
        episode, feasibility, input_vector(), EVAL, FORECAST_POLICY
    )
    assert ok.status is ForecastStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.acceptance
def test_ac_022_future_label_leakage_rejected() -> None:
    """ATDD-R3-F6-forecast-engine/AC-022: sealed evaluation rejects a label available at or before the prediction cutoff, and a clean population seals."""
    decision = forecast_decision()
    clean = example_from(decision)
    assert clean.has_leakage is False
    assert analysis.count_leakage_violations([clean]) == 0
    assert analysis.seal_forecast_population([clean]) == (clean,)

    leaked = example_from(decision, label_available_at=EVAL - timedelta(seconds=1))
    assert leaked.has_leakage is True
    assert analysis.count_leakage_violations([leaked]) == 1
    with pytest.raises(ForecastContractError):
        analysis.seal_forecast_population([leaked])
    with pytest.raises(ForecastContractError):
        analysis.build_evaluation_report(
            [leaked],
            model_artifact_id=decision.model_artifact_id or "FMOD:x",
            population_id="POP:leak",
            evaluation_start=EVAL,
            evaluation_end=EVAL + timedelta(hours=3),
            bin_edges=(0.0, 0.5, 1.0),
            primary_fidelity={"A", "B"},
        )


@pytest.mark.acceptance
def test_ac_023_unresolved_incomplete_not_negatives() -> None:
    """ATDD-R3-F6-forecast-engine/AC-023: UNRESOLVED and INCOMPLETE_COVERAGE labels are never turned into negatives, and a TIMEOUT is never equated with a negative return."""
    decision = forecast_decision()
    resolved = example_from(decision, example_id="EX-RESOLVED")
    unresolved = example_from(
        decision,
        example_id="EX-UNRESOLVED",
        entry_label=None,
        entry_state="UNRESOLVED",
        path_label=None,
        path_state="UNRESOLVED",
        realized_return=None,
        return_state="UNRESOLVED",
    )
    incomplete = example_from(
        decision,
        example_id="EX-INCOMPLETE",
        path_label=None,
        path_state="INCOMPLETE_COVERAGE",
    )
    report = analysis.build_evaluation_report(
        [resolved, unresolved, incomplete],
        model_artifact_id=decision.model_artifact_id or "FMOD:x",
        population_id="POP:mixed",
        evaluation_start=EVAL,
        evaluation_end=EVAL + timedelta(hours=3),
        bin_edges=(0.0, 0.5, 1.0),
        primary_fidelity={"A", "B"},
    )
    # Only the resolved entry is scored; the others are not fabricated negatives.
    assert report.entry_brier == pytest.approx(
        analysis.brier_score(decision.entry_distribution, "FULL_FILL")
    )
    assert report.resolved_label_count == 2
    assert report.unresolved_count == 1
    assert report.incomplete_coverage_count == 1
    assert report.missingness["prediction_count"] == 3

    # A TIMEOUT with a positive realized return is scored as a positive return.
    timeout_positive = example_from(
        decision,
        example_id="EX-TIMEOUT",
        path_label="TIMEOUT",
        realized_return=0.03,
    )
    diagnostics = analysis.expected_return_diagnostics([timeout_positive])
    assert diagnostics.count == 1
    assert diagnostics.mean_squared_error == pytest.approx((0.012 - 0.03) ** 2)


@pytest.mark.acceptance
def test_ac_024_no_fill_has_no_path_label() -> None:
    """ATDD-R3-F6-forecast-engine/AC-024: a NO_FILL entry has no post-fill path label and the report never fabricates or scores one."""
    decision = forecast_decision()
    no_fill = example_from(
        decision,
        entry_label="NO_FILL",
        path_label=None,
        path_state="INSUFFICIENT_EVIDENCE",
        example_id="EX-NOFILL",
    )
    assert no_fill.entry_outcome_label is EntryExecutionOutcome.NO_FILL
    assert no_fill.post_fill_outcome_label is None

    with pytest.raises(ForecastContractError):
        example_from(
            decision,
            entry_label="NO_FILL",
            path_label="TIMEOUT",
            path_state="RESOLVED",
        )
    with pytest.raises(ForecastContractError):
        example_from(
            decision,
            entry_label="NO_FILL",
            path_label=None,
            path_state="RESOLVED",
        )
    # Path labels require actual fill exposure.
    with pytest.raises(ForecastContractError):
        example_from(
            decision,
            entry_label=None,
            entry_state="UNRESOLVED",
            path_label="TARGET",
            path_state="RESOLVED",
        )

    report = analysis.build_evaluation_report(
        [no_fill],
        model_artifact_id=decision.model_artifact_id or "FMOD:x",
        population_id="POP:nofill",
        evaluation_start=EVAL,
        evaluation_end=EVAL + timedelta(hours=3),
        bin_edges=(0.0, 0.5, 1.0),
        primary_fidelity={"A", "B"},
    )
    assert report.path_brier is None
    assert report.entry_brier is not None


@pytest.mark.acceptance
def test_ac_025_brier_correct() -> None:
    """ATDD-R3-F6-forecast-engine/AC-025: the multi-class Brier score is analytically correct for perfect, uniform and known examples, and the two families are not merged."""
    entry = ProbabilityDistribution(
        kind=DistributionKind.ENTRY_EXECUTION,
        probabilities={"NO_FILL": 0.2, "PARTIAL_FILL": 0.3, "FULL_FILL": 0.5},
    )
    assert analysis.brier_score(entry, "FULL_FILL") == pytest.approx(0.38)
    assert analysis.brier_score(entry, "NO_FILL") == pytest.approx(
        (0.2 - 1) ** 2 + 0.3**2 + 0.5**2
    )

    perfect = ProbabilityDistribution(
        kind=DistributionKind.POST_FILL_PATH,
        probabilities={"TARGET": 1.0, "STOP": 0.0, "TIMEOUT": 0.0, "RISK_EXIT": 0.0},
    )
    assert analysis.brier_score(perfect, "TARGET") == pytest.approx(0.0)

    uniform_entry = ProbabilityDistribution(
        kind=DistributionKind.ENTRY_EXECUTION,
        probabilities={"NO_FILL": 1 / 3, "PARTIAL_FILL": 1 / 3, "FULL_FILL": 1 / 3},
    )
    assert analysis.brier_score(uniform_entry, "NO_FILL") == pytest.approx(2 / 3)
    uniform_path = ProbabilityDistribution(
        kind=DistributionKind.POST_FILL_PATH,
        probabilities={
            "TARGET": 0.25,
            "STOP": 0.25,
            "TIMEOUT": 0.25,
            "RISK_EXIT": 0.25,
        },
    )
    assert analysis.brier_score(uniform_path, "STOP") == pytest.approx(3 / 4)

    # A cross-family observation is refused rather than silently merged.
    with pytest.raises(ForecastContractError):
        analysis.brier_score(entry, "TARGET")
    with pytest.raises(ForecastContractError):
        analysis.brier_score(perfect, "FULL_FILL")


@pytest.mark.acceptance
def test_ac_026_log_loss_correct() -> None:
    """ATDD-R3-F6-forecast-engine/AC-026: the multi-class log loss is analytically correct, never evaluates log(0), and uses an explicit evaluation-only epsilon."""
    entry = ProbabilityDistribution(
        kind=DistributionKind.ENTRY_EXECUTION,
        probabilities={"NO_FILL": 0.2, "PARTIAL_FILL": 0.3, "FULL_FILL": 0.5},
    )
    assert analysis.log_loss(entry, "FULL_FILL") == pytest.approx(math.log(2.0))
    perfect = ProbabilityDistribution(
        kind=DistributionKind.POST_FILL_PATH,
        probabilities={"TARGET": 1.0, "STOP": 0.0, "TIMEOUT": 0.0, "RISK_EXIT": 0.0},
    )
    assert analysis.log_loss(perfect, "TARGET") == pytest.approx(0.0)
    uniform = ProbabilityDistribution(
        kind=DistributionKind.POST_FILL_PATH,
        probabilities={
            "TARGET": 0.25,
            "STOP": 0.25,
            "TIMEOUT": 0.25,
            "RISK_EXIT": 0.25,
        },
    )
    assert analysis.log_loss(uniform, "STOP") == pytest.approx(math.log(4.0))
    # An observed outcome with probability zero stays finite.
    zero_prob = ProbabilityDistribution(
        kind=DistributionKind.ENTRY_EXECUTION,
        probabilities={"NO_FILL": 0.0, "PARTIAL_FILL": 0.0, "FULL_FILL": 1.0},
    )
    loss = analysis.log_loss(zero_prob, "NO_FILL")
    assert math.isfinite(loss)
    assert loss == pytest.approx(-math.log(analysis.EVALUATION_LOG_EPSILON))
    assert 0.0 < analysis.EVALUATION_LOG_EPSILON < 1.0
    with pytest.raises(ForecastContractError):
        analysis.log_loss(entry, "FULL_FILL", epsilon=0.0)
    with pytest.raises(ForecastContractError):
        analysis.log_loss(entry, "FULL_FILL", epsilon=2.0)


@pytest.mark.acceptance
def test_ac_027_families_scored_separately() -> None:
    """ATDD-R3-F6-forecast-engine/AC-027: the entry-execution and conditional post-fill families are scored separately and never merged into one win probability."""
    decision = forecast_decision()
    example = example_from(decision)
    report = analysis.build_evaluation_report(
        [example],
        model_artifact_id=decision.model_artifact_id or "FMOD:x",
        population_id="POP:separate",
        evaluation_start=EVAL,
        evaluation_end=EVAL + timedelta(hours=3),
        bin_edges=(0.0, 0.5, 1.0),
        primary_fidelity={"A", "B"},
    )
    assert report.entry_brier == pytest.approx(
        analysis.brier_score(decision.entry_distribution, "FULL_FILL")
    )
    assert report.path_brier == pytest.approx(
        analysis.brier_score(decision.post_fill_distribution, "TARGET")
    )
    assert report.entry_brier != report.path_brier
    assert report.entry_log_loss != report.path_log_loss

    # Changing the entry distribution leaves the path score unchanged.
    other_entry = SyntheticTestModel(entry={"NO_FILL": 0.5, "PARTIAL_FILL": 0.25, "FULL_FILL": 0.25})
    changed = forecast_decision(registry=make_registry(other_entry))
    changed_report = analysis.build_evaluation_report(
        [example_from(changed)],
        model_artifact_id=changed.model_artifact_id or "FMOD:x",
        population_id="POP:separate",
        evaluation_start=EVAL,
        evaluation_end=EVAL + timedelta(hours=3),
        bin_edges=(0.0, 0.5, 1.0),
        primary_fidelity={"A", "B"},
    )
    assert changed_report.path_brier == pytest.approx(report.path_brier)
    assert changed_report.entry_brier != report.entry_brier

    # A report refuses a population that names another model, or a prediction
    # outside the declared window, rather than crediting a model it did not score.
    other_model = replace(example, model_artifact_id="FMOD:other")
    with pytest.raises(ForecastContractError):
        analysis.build_evaluation_report(
            [other_model],
            model_artifact_id=decision.model_artifact_id or "FMOD:x",
            population_id="POP:mismatch",
            evaluation_start=EVAL,
            evaluation_end=EVAL + timedelta(hours=3),
            bin_edges=(0.0, 0.5, 1.0),
            primary_fidelity={"A", "B"},
        )
    outside_window = replace(example, prediction_cutoff=EVAL - timedelta(hours=1))
    with pytest.raises(ForecastContractError):
        analysis.build_evaluation_report(
            [outside_window],
            model_artifact_id=decision.model_artifact_id or "FMOD:x",
            population_id="POP:outside",
            evaluation_start=EVAL,
            evaluation_end=EVAL + timedelta(hours=3),
            bin_edges=(0.0, 0.5, 1.0),
            primary_fidelity={"A", "B"},
        )


@pytest.mark.acceptance
def test_ac_028_reliability_report_deterministic() -> None:
    """ATDD-R3-F6-forecast-engine/AC-028: reliability diagnostics require an explicit bin configuration, are deterministic, report empty bins as unavailable, and never emit a calibration-pass verdict."""
    decision = forecast_decision()
    example = example_from(decision)
    first = analysis.reliability_diagnostics([example], bin_edges=(0.0, 0.25, 0.5, 0.75, 1.0))
    second = analysis.reliability_diagnostics([example], bin_edges=(0.0, 0.25, 0.5, 0.75, 1.0))
    assert first == second
    assert all(bin_.count >= 0 for bin_ in first)
    empty = [bin_ for bin_ in first if bin_.count == 0]
    assert empty
    assert all(bin_.predicted_mean is None for bin_ in empty)
    assert all(bin_.observed_frequency is None for bin_ in empty)
    with pytest.raises(TypeError):
        analysis.reliability_diagnostics([example])  # type: ignore[call-arg]
    with pytest.raises(ForecastContractError):
        analysis.reliability_diagnostics([example], bin_edges=(0.0, 900.0))
    with pytest.raises(ForecastContractError):
        analysis.reliability_diagnostics([example], bin_edges=(0.0, 0.5))

    assert "threshold" not in identifier_tokens(EVALUATION_SOURCE)
    assert "calibration_pass" not in EVALUATION_SOURCE
    report = analysis.build_evaluation_report(
        [example],
        model_artifact_id=decision.model_artifact_id or "FMOD:x",
        population_id="POP:reliability",
        evaluation_start=EVAL,
        evaluation_end=EVAL + timedelta(hours=3),
        bin_edges=(0.0, 0.5, 1.0),
        primary_fidelity={"A", "B"},
    )
    assert not hasattr(report, "calibration_passed")
    assert report.sealed_evaluation is True
    assert report.primary_fidelity == ("A", "B")
    assert analysis.ForecastEvaluationReport.from_dict(report.to_dict()) == report
    # The report's mappings are frozen so evaluation evidence cannot change after
    # construction and silently disagree with its identity.
    with pytest.raises(TypeError):
        report.missingness["tampered"] = 1
    with pytest.raises(TypeError):
        report.fidelity_breakdown["A"] = 99


@pytest.mark.acceptance
def test_ac_029_no_score_to_probability() -> None:
    """ATDD-R3-F6-forecast-engine/AC-029: no ordinal score or confidence is promoted into a probability, and no score is divided by 100."""
    for source in F6_SOURCES:
        tokens = identifier_tokens(source)
        assert tokens.isdisjoint(SCORE_PROMOTION_TOKENS), tokens & SCORE_PROMOTION_TOKENS
        assert scales_by_hundred(source) is False
    # A probability is only produced by a validated distribution or an adapter
    # output, never by a score field.
    decision = forecast_decision()
    assert decision.entry_distribution is not None
    assert decision.to_dict()["entry_distribution"]["probabilities"]["FULL_FILL"] == 0.5
    # No prohibited negative fixture is present in the production code.
    assert "score / 100" not in VOCABULARY_SOURCE


@pytest.mark.acceptance
def test_ac_030_no_ai_or_committee_authority() -> None:
    """ATDD-R3-F6-forecast-engine/AC-030: the F6 modules import no AI or Committee authority and no AI/Committee output can become a probability."""
    for source in F6_SOURCES:
        modules = imported_modules(source)
        for module in modules:
            assert not any(
                part in module
                for part in ("openai", "anthropic", "deepseek", "gemini", "committee")
            ), module
        tokens = identifier_tokens(source)
        assert not any(
            token in tokens
            for token in ("committee", "ai_confidence", "committee_confidence")
        )
        assert "recommendation" not in tokens


@pytest.mark.acceptance
def test_ac_031_no_allocation_or_f7() -> None:
    """ATDD-R3-F6-forecast-engine/AC-031: the F6 modules perform no F7 allocation, ranking, reservation, sizing or cash competition and import no selector/portfolio module."""
    for source in F6_SOURCES:
        for module in imported_modules(source):
            assert not any(
                part in module
                for part in (
                    "portfolio",
                    "selector",
                    "optimiz",
                    "allocat",
                    "ranking",
                    "services",
                    "scanner",
                )
            ), module
    assert not (APP_ROOT / "app" / "opip" / "forecast").exists()
    assert not (APP_ROOT / "app" / "opip" / "calibration").exists()
    # No allocation-like attribute is exposed by a decision.
    decision = forecast_decision()
    for forbidden in ("allocation", "reservation", "position_size", "top_n"):
        assert not hasattr(decision, forbidden)


@pytest.mark.acceptance
def test_ac_032_no_runtime_integration() -> None:
    """ATDD-R3-F6-forecast-engine/AC-032: no runtime path imports or invokes F6; the shadow decoupling is preserved."""
    for path in (RUN_CYCLE_PATH, SCAN_PATH):
        text = path.read_text(encoding="utf-8")
        assert "evaluate_forecast" not in text, path
        assert "opip.forecast" not in text, path
        assert "opip import forecast" not in text, path
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if path in (FORECAST_PATH, EVALUATION_PATH, VOCABULARY_PATH, CONTRACTS_INIT_PATH):
            continue
        text = path.read_text(encoding="utf-8")
        assert "opip.forecast" not in text, path
        assert "evaluate_forecast" not in text, path


@pytest.mark.acceptance
def test_ac_033_no_new_writer_db_jsonl() -> None:
    """ATDD-R3-F6-forecast-engine/AC-033: F6 creates no persistence module, writer, DB table or JSONL stream and writes no canonical evidence."""
    for source in F6_SOURCES:
        for token in ("sqlite3", "jsonl", "jsonlines", "CanonicalWriter", "scheduler"):
            assert token not in source, token
    for source in F6_SOURCES:
        assert not any(
            part in module
            for module in imported_modules(source)
            for part in ("sqlite3", "canonical", "writer", "storage", "jsonl")
        )
    decision = forecast_decision()
    assert ForecastDecision.from_dict(decision.to_dict()) == decision


@pytest.mark.acceptance
def test_ac_034_feature_bus_off() -> None:
    """ATDD-R3-F6-forecast-engine/AC-034: the Feature Bus mode remains off and F6 activates no Feature Bus behavior."""
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "shadow"' in compose
    for source in F6_SOURCES:
        assert "feature_bus" not in source.lower()


@pytest.mark.acceptance
def test_ac_035_f3_f4_f5_semantics_unchanged() -> None:
    """ATDD-R3-F6-forecast-engine/AC-035: F3, F4 and F5 semantics are unmodified and F6 reinterprets none of them."""
    detector_source = (
        APP_ROOT / "app" / "opip" / "contracts" / "detector.py"
    ).read_text(encoding="utf-8")
    assert "episode_id is not None" in detector_source

    lifecycle_source = (
        APP_ROOT / "app" / "opip" / "opportunity_lifecycle.py"
    ).read_text(encoding="utf-8")
    assert "def apply_claim(" in lifecycle_source
    assert "forecast" not in lifecycle_source.lower()

    feasibility_source = (APP_ROOT / "app" / "opip" / "feasibility.py").read_text(
        encoding="utf-8"
    )
    assert "def evaluate_feasibility(" in feasibility_source
    assert "opip.forecast" not in feasibility_source
    assert "evaluate_forecast" not in feasibility_source

    # F5 still produces identical, deterministic decisions.
    first = feasible_decision().to_dict()
    second = feasible_decision().to_dict()
    assert first == second
    # F6 never mutates F5/F4: the episode and decision are unchanged after use.
    episode = active_episode()
    feasibility = feasible_decision(episode)
    before = feasibility.to_dict()
    engine.evaluate_forecast(episode, feasibility, input_vector(), EVAL, FORECAST_POLICY)
    assert feasibility.to_dict() == before


@pytest.mark.acceptance
def test_ac_036_f5_pointer_handoff() -> None:
    """ATDD-R3-F6-forecast-engine/AC-036: F6 proves its own identity, the completed F5 and F6 increments do not pin the movable global pointer, and every substantive F6 isolation guarantee is retained."""
    # F6's own identity comes from its own frozen scope contract, not the pointer.
    f6_contract_path = (
        APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
    )
    assert f6_contract_path.is_file()
    f6_contract = f6_contract_path.read_text(encoding="utf-8")
    f6_lines = f6_contract.splitlines()
    assert f6_lines[0].strip() == "INCREMENT:"
    assert f6_lines[1].strip() == INCREMENT
    assert "F5 ACTIVE_INCREMENT HANDOFF" in f6_contract
    assert "test_ac_027_f4_pointer_handoff" in f6_contract

    # The global pointer is deliberately movable orchestration state. F6 and F5 are
    # complete, so neither pins it; the pointer only has to resolve to an existing
    # scope contract, exactly as the R0/R1 increment does.
    pointer = ACTIVE_INCREMENT_PATH.read_text(encoding="utf-8").strip()
    assert (
        APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{pointer}.md"
    ).is_file(), pointer

    # Neither this module nor the completed F5 module reads the global pointer and
    # compares it to its own increment identity; the structural guard proves it.
    f6_source = Path(__file__).read_text(encoding="utf-8")
    assert global_pointer_pin_violations(f6_source) == []
    f5_test = (
        APP_ROOT / "tests" / "test_opip_r3_f5_feasibility.py"
    ).read_text(encoding="utf-8")
    assert global_pointer_pin_violations(f5_test) == []
    assert FROZEN_F5_INCREMENT in f5_test

    # F5/F6 stay shadow and non-authoritative with the Feature Bus off, and every
    # substantive F6 isolation criterion stays asserted in this module.
    assert 'OPIP_FEATURE_BUS_MODE: "shadow"' in COMPOSE_PATH.read_text(encoding="utf-8")
    for marker in (
        "test_ac_030_no_ai_or_committee_authority",
        "test_ac_031_no_allocation_or_f7",
        "test_ac_032_no_runtime_integration",
        "test_ac_033_no_new_writer_db_jsonl",
        "test_ac_034_feature_bus_off",
        "test_ac_035_f3_f4_f5_semantics_unchanged",
    ):
        assert marker in f6_source, marker


# ---------------------------------------------------------------------------
# Adversarial and structural unit tests (not increment acceptance criteria)
# ---------------------------------------------------------------------------


def test_adversarial_executable_or_pickle_artifact_rejected() -> None:
    for bad in (lambda x: x, b"\x80\x04N.", object()):
        with pytest.raises(ForecastContractError):
            artifact(parameters={"payload": bad})
    with pytest.raises(ForecastContractError):
        artifact(parameters={"payload": float("inf")})


def test_adversarial_artifact_parameters_must_be_a_mapping() -> None:
    for bad in (None, [], "not-a-mapping", 5):
        with pytest.raises(ForecastContractError):
            artifact(parameters=bad)


def test_adversarial_missing_feature_does_not_discard_value() -> None:
    with pytest.raises(ForecastContractError):
        ForecastFeatureValue("rsi", 5.0, CUTOFF, missing=True)
    assert ForecastFeatureValue("rsi", None, CUTOFF, missing=True).missing is True


def test_interval_coverage_counts_without_point_forecast() -> None:
    decision = forecast_decision()
    example = replace(example_from(decision), expected_return_unconditional=None)
    diagnostics = analysis.expected_return_diagnostics([example])
    assert diagnostics.count == 0
    assert diagnostics.interval_total == 1
    assert diagnostics.interval_coverage == pytest.approx(1.0)


def test_adversarial_forecast_is_pure_over_declared_inputs() -> None:
    episode = active_episode()
    feasibility = feasible_decision(episode)
    with ForbiddenIO():
        decision = engine.evaluate_forecast(
            episode,
            feasibility,
            input_vector(),
            EVAL,
            FORECAST_POLICY,
            model_artifact=artifact(),
            registry=make_registry(),
        )
    assert decision.status is ForecastStatus.FORECAST
    for source in F6_SOURCES:
        modules = imported_modules(source)
        for module in modules:
            assert module.startswith(ALLOWED_IMPORT_PREFIXES), module
        assert called_attributes(source).isdisjoint(
            {"connect", "urlopen", "system", "popen", "run", "exec", "eval"}
        )


def test_forecast_request_bundles_declared_inputs() -> None:
    episode = active_episode()
    feasibility = feasible_decision(episode)
    inputs = input_vector()
    request = ForecastRequest(
        episode_id=episode.episode_id,
        feasibility_decision_id=feasibility.decision_id,
        input_vector=inputs,
        evaluation_time=EVAL,
        policy=FORECAST_POLICY,
        model_artifact=artifact(),
    )
    assert request.horizon == HORIZON
    assert request.input_fingerprint == inputs.input_fingerprint
    assert request.model_artifact_id is not None
    with pytest.raises(ForecastContractError):
        replace(request, input_vector=object())


def test_decision_round_trip_and_defaults() -> None:
    decision = forecast_decision()
    assert ForecastDecision.from_dict(decision.to_dict()) == decision
    assert decision.expected_return_conditional_on_fill is None
    # A decision cannot be built with an unratified policy version.
    with pytest.raises(ForecastContractError):
        ForecastPolicy(policy_version="forecast-other-v1")
    with pytest.raises(ForecastContractError):
        replace(decision, policy_version="forecast-other-v1")
