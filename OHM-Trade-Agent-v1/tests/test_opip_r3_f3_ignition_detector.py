"""R3 F3 IGNITION detector runtime: executable acceptance and unit tests.

These tests complete, in place, the contract-stage skeletons frozen by
``ATDD-R3-F3-ignition-detector`` and satisfy the implementation increment
``ATDD-R3-F3-ignition-implementation``. Every acceptance test cites **both**
increments, so the frozen contract's traceability and the implementation
increment's traceability each hold. No F3 acceptance test is skipped.

Every fixture here is a deterministic literal. The tests read no network, no
wall clock, no random values and no filesystem state, and they assert that the
detector under test does the same.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import json
import socket
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts.detector import (  # noqa: E402
    IGNITION_DETECTOR_VERSION,
    IGNITION_FAMILY,
    IGNITION_POLICY_VERSION,
    PERSISTENCE_INTERVAL_SECONDS,
    DetectorClaim,
    DetectorContractError,
    DetectorFamily,
    DetectorPhase,
    DetectorResetReason,
    DetectorState,
    DetectorTransition,
    detector_claim_identity,
)
from app.opip.contracts.enums import (  # noqa: E402
    CoverageState,
    Missingness,
    RestartState,
    TrendState,
)
from app.opip.contracts.features import FeatureSnapshot  # noqa: E402
from app.opip.contracts.identity import ConsumedInputWatermark  # noqa: E402
from app.opip.contracts.temporal import AvailabilityStamp  # noqa: E402
from app.opip.detectors import ignition  # noqa: E402
from app.opip.features.replay import (  # noqa: E402
    detector_input_fingerprint,
    detector_replay_input,
)

IMPLEMENTATION_INCREMENT = "ATDD-R3-F3-ignition-implementation"
FROZEN_INCREMENT = "ATDD-R3-F3-ignition-detector"

# ---------------------------------------------------------------------------
# Deterministic fixtures
# ---------------------------------------------------------------------------

CUTOFF = datetime(2026, 9, 11, 15, 1, tzinfo=timezone.utc)
SECOND_CUTOFF = CUTOFF + timedelta(seconds=60)
THIRD_CUTOFF = CUTOFF + timedelta(seconds=120)

INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
VENUE_INSTRUMENT_ID = "SOLUSD"
OTHER_INSTRUMENT_VERSION_ID = "INSTR:kraken:BTC:USD:1"
OTHER_VENUE_INSTRUMENT_ID = "BTCUSD"
FEATURE_VERSION = "features-v1"
FEATURE_DAG_HASH = "FDAG:r3f3-deterministic-fixture"

REQUIRED_FEATURES = (
    "trend_state",
    "return_5m",
    "acceleration_5m_vs_15m",
    "volume_expansion_5m_vs_20m",
    "compression_release_score",
)

#: Satisfies the directional core and all three entry confirmations.
QUALIFYING_VALUES: dict[str, Any] = {
    "trend_state": TrendState.UP.value,
    "return_5m": 0.4,
    "acceleration_5m_vs_15m": 0.05,
    "volume_expansion_5m_vs_20m": 1.30,
    "compression_release_score": 0.40,
}

#: A longer-cadence feature, consumed as a value only.
LONGER_CADENCE_VALUE = {"range_15m": 3.25}


def _present(values: Mapping[str, Any]) -> dict[str, Missingness]:
    return {name: Missingness.PRESENT for name in values}


def build_snapshot(
    *,
    values: Mapping[str, Any] | None = None,
    missingness: Mapping[str, Any] | None = None,
    cutoff: datetime = CUTOFF,
    coverage: CoverageState = CoverageState.COMPLETE,
    restart_state: RestartState = RestartState.WARM,
    grid_seconds: int = 60,
    evaluated_at_utc: datetime | None = None,
    notes: str | None = None,
    instrument_version_id: str = INSTRUMENT_VERSION_ID,
    venue_instrument_id: str = VENUE_INSTRUMENT_ID,
) -> FeatureSnapshot:
    """Build a sealed snapshot from deterministic literals.

    Availability is derived from the cutoff so every honest snapshot stays
    point-in-time consistent: source event before the cutoff, receipt and
    visibility after it, decision time after visibility.
    """
    payload = dict(QUALIFYING_VALUES if values is None else values)
    stamps = dict(_present(payload) if missingness is None else missingness)
    decision = evaluated_at_utc or (cutoff + timedelta(seconds=2))
    availability = AvailabilityStamp(
        source_at_utc=cutoff - timedelta(seconds=60),
        ingested_at_utc=cutoff + timedelta(seconds=1),
        visible_at_utc=cutoff + timedelta(seconds=1),
        source_version="r3f3-fixture-v1",
    )
    return FeatureSnapshot(
        instrument_version_id=instrument_version_id,
        venue_instrument_id=venue_instrument_id,
        feature_version=FEATURE_VERSION,
        evaluation_cutoff=cutoff,
        evaluated_at_utc=decision,
        consumed_input_watermark=ConsumedInputWatermark(history_epoch=1, local_sequence=7),
        values=payload,
        availability=availability,
        missingness=stamps,
        coverage=coverage,
        restart_state=restart_state,
        evaluation_grid_seconds=grid_seconds,
        feature_dag_hash=FEATURE_DAG_HASH,
        notes=notes,
    )


ENTRY_VALUES: dict[str, Any] = dict(QUALIFYING_VALUES)

#: Only one entry confirmation: `acceleration_5m_vs_15m > 0.0`.
ONE_CONFIRMATION_VALUES: dict[str, Any] = {
    "trend_state": TrendState.UP.value,
    "return_5m": 0.4,
    "acceleration_5m_vs_15m": 0.05,
    "volume_expansion_5m_vs_20m": 1.10,
    "compression_release_score": 0.20,
}

#: Exactly two entry confirmations, at the inclusive boundaries.
BOUNDARY_TWO_CONFIRMATIONS: dict[str, Any] = {
    "trend_state": TrendState.UP.value,
    "return_5m": 0.4,
    "acceleration_5m_vs_15m": 0.0,
    "volume_expansion_5m_vs_20m": 1.25,
    "compression_release_score": 0.35,
}

#: Hold satisfied at the inclusive boundaries: volume and compression only.
HOLD_BOUNDARY_VALUES: dict[str, Any] = {
    "trend_state": TrendState.UP.value,
    "return_5m": 0.4,
    "acceleration_5m_vs_15m": 0.0,
    "volume_expansion_5m_vs_20m": 1.00,
    "compression_release_score": 0.15,
}

#: Hold fails: no confirmation, though the directional core still holds.
HOLD_FAILED_VALUES: dict[str, Any] = {
    "trend_state": TrendState.UP.value,
    "return_5m": 0.4,
    "acceleration_5m_vs_15m": 0.0,
    "volume_expansion_5m_vs_20m": 0.99,
    "compression_release_score": 0.14,
}

#: Directional core fails on direction.
DOWN_TREND_VALUES: dict[str, Any] = {**QUALIFYING_VALUES, "trend_state": TrendState.DOWN.value}
#: Directional core fails on return.
FLAT_RETURN_VALUES: dict[str, Any] = {**QUALIFYING_VALUES, "return_5m": 0.0}
#: Directional core fails on a negative return.
NEGATIVE_RETURN_VALUES: dict[str, Any] = {**QUALIFYING_VALUES, "return_5m": -0.2}
#: A usable direction that is neither UP nor an unusable UNKNOWN.
FLAT_TREND_VALUES: dict[str, Any] = {**QUALIFYING_VALUES, "trend_state": TrendState.FLAT.value}
#: A declared vocabulary token that carries no usable direction.
UNKNOWN_TREND_VALUES: dict[str, Any] = {
    **QUALIFYING_VALUES,
    "trend_state": TrendState.UNKNOWN.value,
}


def build_state(**overrides: Any) -> DetectorState:
    params: dict[str, Any] = {
        "instrument_version_id": INSTRUMENT_VERSION_ID,
        "venue_instrument_id": VENUE_INSTRUMENT_ID,
    }
    params.update(overrides)
    return DetectorState(**params)


def complete_entry(*, values: Mapping[str, Any] | None = None) -> DetectorState:
    """Drive the two-interval entry persistence and return the resulting state."""
    snapshot_1 = build_snapshot(values=values, cutoff=CUTOFF)
    claims_1, state_1 = ignition.evaluate(snapshot_1, build_state(), CUTOFF)
    assert claims_1 == []
    assert state_1.phase is DetectorPhase.DORMANT
    assert state_1.persistence_seconds == PERSISTENCE_INTERVAL_SECONDS
    snapshot_2 = build_snapshot(values=values, cutoff=SECOND_CUTOFF)
    claims_2, state_2 = ignition.evaluate(snapshot_2, state_1, SECOND_CUTOFF)
    assert len(claims_2) == 1
    assert state_2.phase is DetectorPhase.IGNITION
    return state_2


def evaluate_entry_sequence() -> tuple[list[DetectorClaim], DetectorState]:
    """The full entry sequence from a fresh state, for replay comparison."""
    snapshot_1 = build_snapshot(cutoff=CUTOFF)
    claims_1, state_1 = ignition.evaluate(snapshot_1, build_state(), CUTOFF)
    snapshot_2 = build_snapshot(cutoff=SECOND_CUTOFF)
    claims_2, state_2 = ignition.evaluate(snapshot_2, state_1, SECOND_CUTOFF)
    return [*claims_1, *claims_2], state_2


def assert_reset(
    state: DetectorState,
    reason: DetectorResetReason,
    *,
    transition: DetectorTransition = DetectorTransition.NONE,
) -> None:
    """A reset returns a safe DORMANT state with zeroed persistence."""
    assert state.phase is DetectorPhase.DORMANT
    assert state.persistence_seconds == 0
    assert state.reset_reason is reason
    assert state.transition is transition


# ---------------------------------------------------------------------------
# Source-level probes
# ---------------------------------------------------------------------------

IGNITION_PATH = APP_ROOT / "app" / "opip" / "detectors" / "ignition.py"
CONTRACT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "detector.py"
DETECTORS_INIT_PATH = APP_ROOT / "app" / "opip" / "detectors" / "__init__.py"
CONTRACTS_INIT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "__init__.py"
TEST_PATH = Path(__file__).resolve()
COMPOSE_PATH = APP_ROOT / "docker-compose.yml"
ACTIVE_INCREMENT_PATH = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"

IGNITION_SOURCE = IGNITION_PATH.read_text(encoding="utf-8")
CONTRACT_SOURCE = CONTRACT_PATH.read_text(encoding="utf-8")

NEW_MODULE_PATHS = frozenset(
    str(path) for path in (IGNITION_PATH, CONTRACT_PATH, DETECTORS_INIT_PATH, CONTRACTS_INIT_PATH)
)

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "os",
        "sys",
        "socket",
        "subprocess",
        "sqlite3",
        "requests",
        "urllib",
        "http",
        "time",
        "random",
        "uuid",
        "secrets",
        "pathlib",
        "shutil",
        "tempfile",
        "logging",
        "ccxt",
        "krakenex",
        "telegram",
        "psycopg2",
        "sqlalchemy",
        "yaml",
    }
)

FORBIDDEN_MODULE_PREFIXES = (
    "app.services",
    "app.jobs",
    "app.opip.storage",
    "app.opip.canonical",
    "app.opip.committee",
    "app.opip.risk",
    "app.opip.opportunity",
    "app.opip.lifecycle",
    "app.opip.data_platform",
    "app.opip.profit_intelligence",
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
        "putenv",
        "environ",
        "urandom",
        "random",
        "randint",
        "choice",
        "connect",
        "urlopen",
        "open",
        "read_text",
        "write_text",
        "execute",
        "execute_many",
    }
)

AUTHORITY_TOKENS = (
    "authorize",
    "admit",
    "approve",
    "order",
    "execute",
    "trade",
    "allocat",
    "risk",
    "permission",
    "signal",
)

LEAKAGE_TOKENS = ("deadline", "expiry", "expires", "opportunity", "episode", "deferral")


def imported_modules(source: str) -> set[str]:
    """Every imported module name (not just the root) in one source string."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def called_attributes(source: str) -> set[str]:
    """Every ``x.attr(...)`` attribute name called in one source string."""
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
            raise AssertionError("evaluate() performed filesystem access")

        def _no_network(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("evaluate() performed network access")

        builtins.open = _no_filesystem
        socket.socket = _no_network
        socket.create_connection = _no_network
        return self

    def __exit__(self, *_exc: Any) -> bool:
        builtins.open = self._open
        socket.socket = self._socket
        socket.create_connection = self._create_connection
        return False


NEW_MODULE_SOURCE = {
    "ignition": IGNITION_SOURCE,
    "detector": CONTRACT_SOURCE,
    "detectors_init": DETECTORS_INIT_PATH.read_text(encoding="utf-8"),
    "contracts_init": CONTRACTS_INIT_PATH.read_text(encoding="utf-8"),
}


# ---------------------------------------------------------------------------
# AC-001 .. AC-016
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_evaluation_is_pure() -> None:
    """ATDD-R3-F3-ignition-detector/AC-001: evaluate() performs no I/O and reads no hidden state. ATDD-R3-F3-ignition-implementation/AC-001: purity is proved over the authorized implementation module."""
    signature = inspect.signature(ignition.evaluate)
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "snapshot",
        "prior_state",
        "evaluation_time",
    ]
    for parameter in parameters:
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )

    modules = imported_modules(IGNITION_SOURCE)
    assert modules.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
    for module in modules:
        assert not module.startswith(FORBIDDEN_MODULE_PREFIXES), module
    assert called_attributes(IGNITION_SOURCE).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)

    # No global mutable state: every module-level binding is immutable.
    for name, value in vars(ignition).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (dict, list, set, bytearray)), name

    snapshot = build_snapshot()
    prior_state = build_state()
    with ForbiddenIO():
        claims, next_state = ignition.evaluate(snapshot, prior_state, CUTOFF)
    assert isinstance(claims, list)
    assert isinstance(next_state, DetectorState)

    # Inputs are never mutated, and the next state is a new object.
    assert snapshot.to_dict() == build_snapshot().to_dict()
    assert prior_state.to_dict() == build_state().to_dict()
    assert next_state is not prior_state


@pytest.mark.acceptance
def test_ac_002_repeated_evaluation_is_deterministic() -> None:
    """ATDD-R3-F3-ignition-detector/AC-002: identical inputs replay to equivalent claims and state. ATDD-R3-F3-ignition-implementation/AC-002: replay determinism holds for the implementation increment."""
    claims_first, state_first = evaluate_entry_sequence()
    claims_second, state_second = evaluate_entry_sequence()
    assert [claim.to_dict() for claim in claims_first] == [
        claim.to_dict() for claim in claims_second
    ]
    assert state_first.to_dict() == state_second.to_dict()
    assert len(claims_first) == 1

    # A fresh state in a fresh process produces the identical result.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json;"
            "from tests.test_opip_r3_f3_ignition_detector import evaluate_entry_sequence;"
            "claims, state = evaluate_entry_sequence();"
            "print(json.dumps({'claims': [c.to_dict() for c in claims],"
            " 'state': state.to_dict()}, sort_keys=True))",
        ],
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["claims"] == [claim.to_dict() for claim in claims_first]
    assert payload["state"] == state_first.to_dict()


@pytest.mark.acceptance
def test_ac_003_explicit_evaluation_time_uses_declared_grid() -> None:
    """ATDD-R3-F3-ignition-detector/AC-003: grid cadence governs and evaluation_time equals the snapshot cutoff. ATDD-R3-F3-ignition-implementation/AC-003: the 60-second grid is declared once and never redefined by a longer-cadence feature."""
    assert ignition.EVALUATION_GRID_SECONDS == 60
    assert PERSISTENCE_INTERVAL_SECONDS == 60
    assert ignition.ENTRY_PERSISTENCE_SECONDS == 120
    assert ignition.RELEASE_PERSISTENCE_SECONDS == 120

    # A 15-minute feature is consumed as a value only.
    with_longer_cadence = build_snapshot(values={**QUALIFYING_VALUES, **LONGER_CADENCE_VALUE})
    assert with_longer_cadence.evaluation_grid_seconds == 60
    _, state = ignition.evaluate(with_longer_cadence, build_state(), CUTOFF)
    assert state.persistence_seconds == PERSISTENCE_INTERVAL_SECONDS
    assert detector_replay_input(with_longer_cadence)["evaluation_grid_seconds"] == 60

    # A longer cadence cannot become the detector cadence.
    with pytest.raises(DetectorContractError):
        ignition.evaluate(build_snapshot(grid_seconds=30), build_state(), CUTOFF)
    # A 15-minute-aligned instant that is not the snapshot cutoff fails closed.
    with pytest.raises(DetectorContractError):
        ignition.evaluate(
            build_snapshot(), build_state(), CUTOFF + timedelta(seconds=840)
        )


@pytest.mark.acceptance
def test_ac_004_transition_owned_by_detector_not_storage() -> None:
    """ATDD-R3-F3-ignition-detector/AC-004: the detector owns transitions and performs no persistence. ATDD-R3-F3-ignition-implementation/AC-004: transition ownership and the absence of persistence work are asserted on the implementation."""
    modules = imported_modules(IGNITION_SOURCE) | imported_modules(CONTRACT_SOURCE)
    for module in modules:
        assert not module.startswith(FORBIDDEN_MODULE_PREFIXES), module
    assert "sqlite3" not in modules

    # Transition semantics live in the detector: only the state handoff is needed.
    prior_state = build_state()
    snapshot_1 = build_snapshot(cutoff=CUTOFF)
    claims_1, state_1 = ignition.evaluate(snapshot_1, prior_state, CUTOFF)
    assert claims_1 == []
    assert state_1.transition is DetectorTransition.NONE
    assert state_1.persistence_seconds == 60

    snapshot_2 = build_snapshot(cutoff=SECOND_CUTOFF)
    claims_2, state_2 = ignition.evaluate(snapshot_2, state_1, SECOND_CUTOFF)
    assert len(claims_2) == 1
    assert state_2.transition is DetectorTransition.DORMANT_TO_IGNITION
    assert state_2.persistence_seconds == 0
    assert state_2.reset_reason is DetectorResetReason.NONE

    # Only one claim is emitted, and it is for the completion transition.
    claim = claims_2[0]
    assert claim.transition is DetectorTransition.DORMANT_TO_IGNITION
    assert claim.phase is DetectorPhase.IGNITION
    assert claim.episode_id is None

    # The detector does no persistence work: the prior state is untouched.
    assert prior_state.phase is DetectorPhase.DORMANT
    assert prior_state.persistence_seconds == 0
    assert prior_state.to_dict() == build_state().to_dict()


@pytest.mark.acceptance
def test_ac_005_material_gap_resets_persistence() -> None:
    """ATDD-R3-F3-ignition-detector/AC-005: persistence does not accrue across a material gap. ATDD-R3-F3-ignition-implementation/AC-005: MATERIAL_GAP is the OWNER-ratified reason and resets evidence to zero."""
    stated = build_state(persistence_seconds=60)
    gap = build_snapshot(coverage=CoverageState.INCOMPLETE_COVERAGE)
    claims, state = ignition.evaluate(gap, stated, CUTOFF)
    assert claims == []
    assert_reset(state, DetectorResetReason.MATERIAL_GAP)
    # The favourable 60 seconds of prior evidence is not preserved.
    assert state.persistence_seconds != stated.persistence_seconds

    # A gap during IGNITION also releases to DORMANT with no claim.
    igniting = build_state(phase=DetectorPhase.IGNITION)
    claims_igniting, state_igniting = ignition.evaluate(gap, igniting, CUTOFF)
    assert claims_igniting == []
    assert_reset(
        state_igniting,
        DetectorResetReason.MATERIAL_GAP,
        transition=DetectorTransition.IGNITION_TO_DORMANT,
    )

    # A gap cannot advance an entry sequence even when every value qualifies.
    first_claims, first_state = ignition.evaluate(build_snapshot(), build_state(), CUTOFF)
    assert first_claims == []
    gap_second = build_snapshot(
        cutoff=SECOND_CUTOFF, coverage=CoverageState.INCOMPLETE_COVERAGE
    )
    gap_claims, gap_state = ignition.evaluate(gap_second, first_state, SECOND_CUTOFF)
    assert gap_claims == []
    assert_reset(gap_state, DetectorResetReason.MATERIAL_GAP)

    # A complete window keeps the entry sequence intact.
    complete_second = build_snapshot(cutoff=SECOND_CUTOFF)
    complete_claims, complete_state = ignition.evaluate(
        complete_second, first_state, SECOND_CUTOFF
    )
    assert len(complete_claims) == 1
    assert complete_state.phase is DetectorPhase.IGNITION


@pytest.mark.acceptance
def test_ac_006_missing_or_invalid_evidence_is_not_favourable() -> None:
    """ATDD-R3-F3-ignition-detector/AC-006: missing or invalid evidence fails closed. ATDD-R3-F3-ignition-implementation/AC-006: INSUFFICIENT_EVIDENCE covers absence while malformed values raise."""
    previous = build_state(persistence_seconds=60)

    # Absent keys.
    absent = build_snapshot(values={"trend_state": TrendState.UP.value}, missingness={})
    claims, state = ignition.evaluate(absent, previous, CUTOFF)
    assert claims == []
    assert_reset(state, DetectorResetReason.INSUFFICIENT_EVIDENCE)
    assert state.persistence_seconds != previous.persistence_seconds

    # Declared but missing.
    missing = build_snapshot(
        values=QUALIFYING_VALUES,
        missingness={**{name: Missingness.PRESENT for name in REQUIRED_FEATURES}, "return_5m": Missingness.MISSING},
    )
    claims, state = ignition.evaluate(missing, previous, CUTOFF)
    assert claims == []
    assert_reset(state, DetectorResetReason.INSUFFICIENT_EVIDENCE)

    # Declared but deliberately not retained.
    not_retained = build_snapshot(
        values=QUALIFYING_VALUES,
        missingness={
            **{name: Missingness.PRESENT for name in REQUIRED_FEATURES},
            "compression_release_score": Missingness.NOT_RETAINED,
        },
    )
    claims, state = ignition.evaluate(not_retained, previous, CUTOFF)
    assert claims == []
    assert_reset(state, DetectorResetReason.INSUFFICIENT_EVIDENCE)

    # Present but explicitly null.
    nulled = build_snapshot(values={**QUALIFYING_VALUES, "volume_expansion_5m_vs_20m": None})
    claims, state = ignition.evaluate(nulled, previous, CUTOFF)
    assert claims == []
    assert_reset(state, DetectorResetReason.INSUFFICIENT_EVIDENCE)

    # Not warm yet, in every declared warm-up state.
    for restart_state in (
        RestartState.RESTART_WARMUP,
        RestartState.INSUFFICIENT_HISTORY,
        RestartState.NEW_LISTING_COLD_START,
    ):
        cold = build_snapshot(restart_state=restart_state)
        claims, state = ignition.evaluate(cold, previous, CUTOFF)
        assert claims == []
        assert_reset(state, DetectorResetReason.INSUFFICIENT_EVIDENCE)

    # A declared but unusable vocabulary token.
    unknown = build_snapshot(values=UNKNOWN_TREND_VALUES)
    claims, state = ignition.evaluate(unknown, previous, CUTOFF)
    assert claims == []
    assert_reset(state, DetectorResetReason.INSUFFICIENT_EVIDENCE)

    # Malformed values are structural violations, not reset reasons.
    for malformed in (
        {**QUALIFYING_VALUES, "return_5m": "0.4"},
        {**QUALIFYING_VALUES, "return_5m": True},
        {**QUALIFYING_VALUES, "volume_expansion_5m_vs_20m": "high"},
        {**QUALIFYING_VALUES, "compression_release_score": True},
        {**QUALIFYING_VALUES, "trend_state": "SIDEWAYS"},
        {**QUALIFYING_VALUES, "trend_state": 1},
    ):
        with pytest.raises(DetectorContractError):
            ignition.evaluate(build_snapshot(values=malformed), previous, CUTOFF)

    # Non-finite numbers never even reach the detector.
    with pytest.raises(ValueError):
        build_snapshot(values={**QUALIFYING_VALUES, "return_5m": float("inf")})

    # A malformed missingness or coverage token is structural.
    bypassed = build_snapshot()
    object.__setattr__(
        bypassed,
        "missingness",
        {
            **{name: Missingness.PRESENT for name in REQUIRED_FEATURES},
            "trend_state": "PROBABLY_PRESENT",
        },
    )
    with pytest.raises(DetectorContractError):
        ignition.evaluate(bypassed, previous, CUTOFF)
    bypassed_coverage = build_snapshot()
    object.__setattr__(bypassed_coverage, "coverage", "COMPLETE_ISH")
    with pytest.raises(DetectorContractError):
        ignition.evaluate(bypassed_coverage, previous, CUTOFF)


@pytest.mark.acceptance
def test_ac_007_transition_identity_is_not_wall_clock() -> None:
    """ATDD-R3-F3-ignition-detector/AC-007: claim identity derives from the transition, not the clock. ATDD-R3-F3-ignition-implementation/AC-007: deterministic identity binds the sealed evidence."""
    assert called_attributes(CONTRACT_SOURCE).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)
    assert imported_modules(CONTRACT_SOURCE).isdisjoint({"uuid", "random", "time"})

    claims_a, _ = evaluate_entry_sequence()
    claims_b, _ = evaluate_entry_sequence()
    assert len(claims_a) == 1
    assert claims_a[0].claim_id == claims_b[0].claim_id
    assert claims_a[0].idempotency_key == claims_b[0].idempotency_key
    assert claims_a[0].claim_id.startswith("DCLM:")

    # The same transition at a wildly different wall-clock instant is identical.
    real_time = time.time
    time.time = lambda: 1.0
    try:
        claims_early, _ = evaluate_entry_sequence()
    finally:
        time.time = real_time
    time.time = lambda: 4102444800.0
    try:
        claims_late, _ = evaluate_entry_sequence()
    finally:
        time.time = real_time
    assert claims_early[0].claim_id == claims_late[0].claim_id
    assert claims_early[0].idempotency_key == claims_late[0].idempotency_key

    # The identity is a pure function of the declared evidence.
    second_snapshot = build_snapshot(cutoff=SECOND_CUTOFF)
    expected_id, expected_key = detector_claim_identity(
        detector_family=DetectorFamily.IGNITION,
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id=VENUE_INSTRUMENT_ID,
        transition=DetectorTransition.DORMANT_TO_IGNITION,
        snapshot_id=second_snapshot.snapshot_id,
        detector_input_fingerprint=detector_input_fingerprint(second_snapshot),
    )
    assert claims_a[0].claim_id == expected_id
    assert claims_a[0].idempotency_key == expected_key

    # Different sealed evidence yields a different identity.
    assert claims_a[0].claim_id != detector_claim_identity(
        detector_family=DetectorFamily.IGNITION,
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id=VENUE_INSTRUMENT_ID,
        transition=DetectorTransition.DORMANT_TO_IGNITION,
        snapshot_id=build_snapshot(cutoff=THIRD_CUTOFF).snapshot_id,
        detector_input_fingerprint=detector_input_fingerprint(
            build_snapshot(cutoff=THIRD_CUTOFF)
        ),
    )[0]


@pytest.mark.acceptance
def test_ac_008_claims_do_not_create_opportunity_lifecycles() -> None:
    """ATDD-R3-F3-ignition-detector/AC-008: claims create no episode, deadline or terminal reason. ATDD-R3-F3-ignition-implementation/AC-008: no F4 lifecycle field or module is introduced."""
    lower_ignition = IGNITION_SOURCE.lower()
    for token in LEAKAGE_TOKENS:
        assert token not in lower_ignition, token

    claims, state = evaluate_entry_sequence()
    claim = claims[0]
    assert claim.episode_id is None

    claim_payload = claim.to_dict()
    for forbidden in (
        "deadline",
        "expires_at",
        "expiry",
        "terminal_reason",
        "opportunity_id",
        "deferral",
        "episode_phase",
    ):
        assert forbidden not in claim_payload
    assert claim_payload["episode_id"] is None

    for forbidden in ("episode", "deadline", "expiry", "opportunity", "deferral"):
        assert forbidden not in state.to_dict()

    # A claim that tries to mint an episode is refused.
    with pytest.raises(DetectorContractError):
        DetectorClaim(
            claim_id="DCLM:x",
            idempotency_key="DCLMKEY:x",
            detector_family=DetectorFamily.IGNITION,
            detector_version=IGNITION_DETECTOR_VERSION,
            policy_version=IGNITION_POLICY_VERSION,
            instrument_version_id=INSTRUMENT_VERSION_ID,
            venue_instrument_id=VENUE_INSTRUMENT_ID,
            phase=DetectorPhase.IGNITION,
            transition=DetectorTransition.DORMANT_TO_IGNITION,
            snapshot_id=build_snapshot().snapshot_id,
            detector_input_fingerprint=detector_input_fingerprint(build_snapshot()),
            evaluation_cutoff=CUTOFF,
            episode_id="episode-1",
        )

    for module in imported_modules(IGNITION_SOURCE) | imported_modules(CONTRACT_SOURCE):
        assert "opportunit" not in module
        assert "lifecycle" not in module


@pytest.mark.acceptance
def test_ac_009_supplied_prior_state_continues_deterministically() -> None:
    """ATDD-R3-F3-ignition-detector/AC-009: a supplied prior DetectorState continues without defaulting. ATDD-R3-F3-ignition-implementation/AC-009: the supplied counter is used exactly as given."""
    # The supplied entry counter is honoured: one qualifying interval completes it.
    stated = build_state(persistence_seconds=60)
    claims, state = ignition.evaluate(build_snapshot(), stated, CUTOFF)
    assert len(claims) == 1
    assert state.phase is DetectorPhase.IGNITION

    # The supplied hold-failure counter is honoured: one failed hold releases.
    igniting = build_state(phase=DetectorPhase.IGNITION, persistence_seconds=60)
    claims, state = ignition.evaluate(
        build_snapshot(values=HOLD_FAILED_VALUES), igniting, CUTOFF
    )
    assert claims == []
    assert state.phase is DetectorPhase.DORMANT
    assert state.transition is DetectorTransition.IGNITION_TO_DORMANT
    assert state.reset_reason is DetectorResetReason.NONE

    # Deterministic continuation: the same prior state replays identically.
    prior = build_state(phase=DetectorPhase.IGNITION)
    hold_snapshot = build_snapshot(values=HOLD_BOUNDARY_VALUES)
    first = ignition.evaluate(hold_snapshot, prior, CUTOFF)
    second = ignition.evaluate(hold_snapshot, prior, CUTOFF)
    assert [claim.to_dict() for claim in first[0]] == [
        claim.to_dict() for claim in second[0]
    ]
    assert first[1].to_dict() == second[1].to_dict()
    assert first[1].phase is DetectorPhase.IGNITION
    assert first[1].persistence_seconds == 0
    assert prior.phase is DetectorPhase.IGNITION

    # An absent or invalid prior state fails closed instead of being invented.
    for invalid in (None, {}, "DORMANT", 0):
        with pytest.raises(DetectorContractError):
            ignition.evaluate(build_snapshot(), invalid, CUTOFF)  # type: ignore[arg-type]


@pytest.mark.acceptance
def test_ac_010_shadow_isolation_grants_no_authority() -> None:
    """ATDD-R3-F3-ignition-detector/AC-010: no run_cycle wiring, Feature Bus activation or authority change. ATDD-R3-F3-ignition-implementation/AC-010: the implementation increment activates nothing."""
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose

    for name, source in NEW_MODULE_SOURCE.items():
        for module in imported_modules(source):
            assert not module.startswith(FORBIDDEN_MODULE_PREFIXES), (name, module)
        assert imported_modules(source).isdisjoint(
            {"ccxt", "krakenex", "telegram", "requests"}
        )

    # No runtime path references the new detector.
    for runtime_module in (
        APP_ROOT / "app" / "jobs" / "run_cycle.py",
        APP_ROOT / "app" / "jobs" / "run_feature_bus_pilot.py",
        APP_ROOT / "app" / "opip" / "features" / "pipeline.py",
        APP_ROOT / "app" / "opip" / "features" / "replay.py",
    ):
        text = runtime_module.read_text(encoding="utf-8")
        assert "opip.detectors" not in text, runtime_module
        assert "detectors.ignition" not in text, runtime_module
        assert "IGNITION detector" not in text, runtime_module

    # Nothing outside the authorized paths imports the new detector vocabulary.
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if str(path) in NEW_MODULE_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        assert "opip.detectors" not in text, path
        assert "detectors.ignition" not in text, path
        assert "contracts.detector" not in text, path

    # The detector exposes no authority-shaped capability.
    for name in ignition.__all__:
        lowered = name.lower()
        for token in AUTHORITY_TOKENS:
            assert token not in lowered, (name, token)

    # The active increment is the implementation increment: no activation happened.
    assert ACTIVE_INCREMENT_PATH.read_text(encoding="utf-8").strip() == (
        IMPLEMENTATION_INCREMENT
    )


@pytest.mark.acceptance
def test_ac_011_evaluation_is_bound_to_the_sealed_snapshot_identity() -> None:
    """ATDD-R3-F3-ignition-detector/AC-011: decision input is detector_replay_input; lineage ids are carried, not decision inputs. ATDD-R3-F3-ignition-implementation/AC-011: excluded metadata is not favourable evidence."""
    snapshot = build_snapshot()
    replay_input = detector_replay_input(snapshot)
    assert replay_input["snapshot_id"] == snapshot.snapshot_id
    assert replay_input["instrument_version_id"] == INSTRUMENT_VERSION_ID
    assert replay_input["values"] == dict(QUALIFYING_VALUES)

    # Excluded metadata never enters the decision input.
    for excluded in ("evaluated_at_utc", "availability", "notes", "content_hash"):
        assert excluded not in replay_input

    # Lineage is carried onto the claim and the next state.
    claims, state = evaluate_entry_sequence()
    second_snapshot = build_snapshot(cutoff=SECOND_CUTOFF)
    claim = claims[0]
    assert claim.snapshot_id == second_snapshot.snapshot_id
    assert claim.detector_input_fingerprint == detector_input_fingerprint(second_snapshot)
    assert state.last_snapshot_id == second_snapshot.snapshot_id
    assert state.last_detector_input_fingerprint == detector_input_fingerprint(
        second_snapshot
    )
    assert state.last_evaluation_cutoff == SECOND_CUTOFF

    # Changing only excluded metadata changes the content hash, not the decision.
    later_receipt = build_snapshot(cutoff=SECOND_CUTOFF, evaluated_at_utc=SECOND_CUTOFF + timedelta(seconds=9))
    annotated = build_snapshot(cutoff=SECOND_CUTOFF, notes="operator annotation")
    assert later_receipt.content_hash() != annotated.content_hash()
    assert detector_input_fingerprint(later_receipt) == detector_input_fingerprint(
        annotated
    )
    first_claims, _ = ignition.evaluate(
        later_receipt, build_state(persistence_seconds=60), SECOND_CUTOFF
    )
    second_claims, _ = ignition.evaluate(
        annotated, build_state(persistence_seconds=60), SECOND_CUTOFF
    )
    assert first_claims[0].claim_id == second_claims[0].claim_id

    # A snapshot identity that is absent fails closed.
    with pytest.raises(DetectorContractError):
        DetectorClaim.create(
            detector_version=IGNITION_DETECTOR_VERSION,
            policy_version=IGNITION_POLICY_VERSION,
            instrument_version_id=INSTRUMENT_VERSION_ID,
            venue_instrument_id=VENUE_INSTRUMENT_ID,
            snapshot_id="",
            detector_input_fingerprint=detector_input_fingerprint(second_snapshot),
            evaluation_cutoff=SECOND_CUTOFF,
        )


@pytest.mark.acceptance
def test_ac_012_detector_policy_version_is_carried_and_checked() -> None:
    """ATDD-R3-F3-ignition-detector/AC-012: the detector/policy version is carried and mismatches fail closed. ATDD-R3-F3-ignition-implementation/AC-012: the applied policy is a code artifact, never a caller argument."""
    assert ignition.POLICY_VERSION == "ignition-shadow-policy-v1"
    assert ignition.DETECTOR_VERSION == "ignition-detector-v1"
    assert IGNITION_POLICY_VERSION == ignition.POLICY_VERSION
    assert IGNITION_DETECTOR_VERSION == ignition.DETECTOR_VERSION
    assert IGNITION_FAMILY == "IGNITION"

    # The applied policy cannot be selected by a caller.
    assert len(inspect.signature(ignition.evaluate).parameters) == 3

    claims, state = evaluate_entry_sequence()
    claim = claims[0]
    assert claim.policy_version == "ignition-shadow-policy-v1"
    assert claim.detector_version == "ignition-detector-v1"
    assert state.policy_version == "ignition-shadow-policy-v1"
    assert state.detector_version == "ignition-detector-v1"
    assert state.detector_family is DetectorFamily.IGNITION

    # A version mismatch on the prior state fails closed rather than continuing.
    with pytest.raises(DetectorContractError):
        ignition.evaluate(
            build_snapshot(),
            build_state(policy_version="ignition-shadow-policy-v0"),
            CUTOFF,
        )
    with pytest.raises(DetectorContractError):
        ignition.evaluate(
            build_snapshot(),
            build_state(detector_version="ignition-detector-v0"),
            CUTOFF,
        )
    # A prior state that bypassed construction with the wrong family fails closed.
    bypassed = build_state()
    object.__setattr__(bypassed, "detector_family", "NOT_IGNITION")
    with pytest.raises(DetectorContractError):
        ignition.evaluate(build_snapshot(), bypassed, CUTOFF)

    # The version is part of claim identity, so the identity binds the policy.
    other_version = detector_claim_identity(
        detector_family=DetectorFamily.IGNITION,
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version="ignition-shadow-policy-v2",
        instrument_version_id=claim.instrument_version_id,
        venue_instrument_id=claim.venue_instrument_id,
        transition=DetectorTransition.DORMANT_TO_IGNITION,
        snapshot_id=claim.snapshot_id,
        detector_input_fingerprint=claim.detector_input_fingerprint,
    )
    assert other_version[0] != claim.claim_id


@pytest.mark.acceptance
def test_ac_013_instrument_identity_must_match_prior_state() -> None:
    """ATDD-R3-F3-ignition-detector/AC-013: an instrument identity mismatch fails closed. ATDD-R3-F3-ignition-implementation/AC-013: no cross-instrument phase or persistence bleed."""
    snapshot = build_snapshot()

    foreign_version = build_state(instrument_version_id=OTHER_INSTRUMENT_VERSION_ID)
    with pytest.raises(DetectorContractError):
        ignition.evaluate(snapshot, foreign_version, CUTOFF)

    foreign_venue = build_state(venue_instrument_id=OTHER_VENUE_INSTRUMENT_ID)
    with pytest.raises(DetectorContractError):
        ignition.evaluate(snapshot, foreign_venue, CUTOFF)

    # The reverse direction fails too: a foreign snapshot against a known state.
    foreign_snapshot = build_snapshot(
        instrument_version_id=OTHER_INSTRUMENT_VERSION_ID,
        venue_instrument_id=OTHER_VENUE_INSTRUMENT_ID,
    )
    with pytest.raises(DetectorContractError):
        ignition.evaluate(foreign_snapshot, build_state(), CUTOFF)

    # No bleed: the legitimate prior state is unchanged after the refusals.
    legitimate = build_state()
    assert legitimate.to_dict() == build_state().to_dict()

    # Matching identity continues normally.
    claims, state = ignition.evaluate(snapshot, legitimate, CUTOFF)
    assert claims == []
    assert state.instrument_version_id == INSTRUMENT_VERSION_ID
    assert state.venue_instrument_id == VENUE_INSTRUMENT_ID


@pytest.mark.acceptance
def test_ac_014_evaluation_time_is_explicit_and_valid() -> None:
    """ATDD-R3-F3-ignition-detector/AC-014: evaluation_time is explicit, grid-valid and exactly equals the snapshot cutoff. ATDD-R3-F3-ignition-implementation/AC-014: evaluated_at_utc is never the evaluation instant."""
    snapshot = build_snapshot()
    parameter = inspect.signature(ignition.evaluate).parameters["evaluation_time"]
    assert parameter.default is inspect.Parameter.empty

    # The valid instant is accepted.
    claims, state = ignition.evaluate(snapshot, build_state(), CUTOFF)
    assert claims == []
    assert state.last_evaluation_cutoff == CUTOFF

    # A naive instant fails closed.
    with pytest.raises(DetectorContractError):
        ignition.evaluate(
            snapshot, build_state(), datetime(2026, 9, 11, 15, 1)
        )
    # An off-grid instant fails closed.
    with pytest.raises(DetectorContractError):
        ignition.evaluate(snapshot, build_state(), CUTOFF + timedelta(seconds=30))
    # A sub-second instant fails closed.
    with pytest.raises(DetectorContractError):
        ignition.evaluate(
            snapshot, build_state(), CUTOFF + timedelta(milliseconds=500)
        )
    # A non-datetime instant fails closed.
    with pytest.raises(DetectorContractError):
        ignition.evaluate(snapshot, build_state(), "2026-09-11T15:01:00Z")

    # The receipt boundary is never equated with the evaluation instant.
    assert snapshot.evaluated_at_utc != snapshot.evaluation_cutoff
    with pytest.raises(DetectorContractError):
        ignition.evaluate(snapshot, build_state(), snapshot.evaluated_at_utc)
    with pytest.raises(DetectorContractError):
        ignition.evaluate(snapshot, build_state(), CUTOFF + timedelta(seconds=60))

    # A non-UTC aware instant is normalized but must still match the cutoff.
    other_zone = CUTOFF.astimezone(timezone(timedelta(hours=2)))
    claims, state = ignition.evaluate(snapshot, build_state(), other_zone)
    assert state.last_evaluation_cutoff == CUTOFF


@pytest.mark.acceptance
def test_ac_015_persistence_and_reset_state_is_typed() -> None:
    """ATDD-R3-F3-ignition-detector/AC-015: phase, persistence-timer and reset values are typed exactly. ATDD-R3-F3-ignition-implementation/AC-015: fractional, boolean and off-grid values are refused, never coerced."""
    for invalid in (0.5, 60.0, True, False, -60, 30, "60", None):
        with pytest.raises(DetectorContractError):
            build_state(persistence_seconds=invalid)

    with pytest.raises(DetectorContractError):
        build_state(phase="NOT_A_PHASE")
    with pytest.raises(DetectorContractError):
        build_state(reset_reason="WHATEVER")
    with pytest.raises(DetectorContractError):
        build_state(transition=123)
    with pytest.raises(DetectorContractError):
        build_state(detector_family="NOT_IGNITION")
    with pytest.raises(DetectorContractError):
        build_state(instrument_version_id="   ")

    # Exact tokens are accepted.
    assert build_state(detector_family="IGNITION").detector_family is DetectorFamily.IGNITION
    assert build_state(phase="IGNITION").phase is DetectorPhase.IGNITION
    assert (
        build_state(reset_reason="MATERIAL_GAP").reset_reason
        is DetectorResetReason.MATERIAL_GAP
    )

    # A state that bypassed construction cannot smuggle a fractional counter in.
    bypassed = build_state()
    object.__setattr__(bypassed, "persistence_seconds", 0.5)
    with pytest.raises(DetectorContractError):
        ignition.evaluate(build_snapshot(), bypassed, CUTOFF)

    # Every returned state satisfies the canonical types and serializes cleanly.
    _, state = evaluate_entry_sequence()
    assert isinstance(state.persistence_seconds, int)
    assert not isinstance(state.persistence_seconds, bool)
    assert isinstance(state.phase, DetectorPhase)
    assert isinstance(state.reset_reason, DetectorResetReason)
    assert isinstance(state.transition, DetectorTransition)
    payload = state.to_dict()
    assert isinstance(payload["last_evaluation_cutoff"], str)
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload
    assert state.persistence_seconds % PERSISTENCE_INTERVAL_SECONDS == 0


@pytest.mark.acceptance
def test_ac_016_no_claim_evaluation_is_valid_and_deterministic() -> None:
    """ATDD-R3-F3-ignition-detector/AC-016: a no-claim evaluation is valid, empty and deterministic. ATDD-R3-F3-ignition-implementation/AC-016: no transition is fabricated by a non-qualifying evaluation."""
    non_qualifying = (
        DOWN_TREND_VALUES,
        FLAT_TREND_VALUES,
        FLAT_RETURN_VALUES,
        NEGATIVE_RETURN_VALUES,
        ONE_CONFIRMATION_VALUES,
    )
    for values in non_qualifying:
        snapshot = build_snapshot(values=values)
        claims, state = ignition.evaluate(snapshot, build_state(), CUTOFF)
        assert claims == []
        assert state.phase is DetectorPhase.DORMANT
        assert state.persistence_seconds == 0
        # A non-qualifying market evaluation is not an epistemic reset.
        assert state.reset_reason is DetectorResetReason.NONE
        assert state.transition is DetectorTransition.NONE
        assert state.last_snapshot_id == snapshot.snapshot_id

    # The no-claim outcome is deterministic and replayable.
    snapshot = build_snapshot(values=ONE_CONFIRMATION_VALUES)
    first = ignition.evaluate(snapshot, build_state(), CUTOFF)
    second = ignition.evaluate(snapshot, build_state(), CUTOFF)
    assert first[0] == second[0] == []
    assert first[1].to_dict() == second[1].to_dict()

    # A non-qualifying interval clears prior persistence evidence.
    stated = build_state(persistence_seconds=60)
    claims, state = ignition.evaluate(snapshot, stated, CUTOFF)
    assert claims == []
    assert state.persistence_seconds == 0

    # Two of three confirmations is the inclusive entry boundary.
    boundary = build_snapshot(values=BOUNDARY_TWO_CONFIRMATIONS)
    claims, state = ignition.evaluate(boundary, build_state(), CUTOFF)
    assert claims == []
    assert state.persistence_seconds == PERSISTENCE_INTERVAL_SECONDS
    completing = build_snapshot(values=BOUNDARY_TWO_CONFIRMATIONS, cutoff=SECOND_CUTOFF)
    claims, state = ignition.evaluate(completing, state, SECOND_CUTOFF)
    assert len(claims) == 1
    assert state.phase is DetectorPhase.IGNITION


# ---------------------------------------------------------------------------
# Focused unit cases for the same policy (not acceptance-marked by design)
# ---------------------------------------------------------------------------


def test_entry_requires_two_consecutive_evaluations() -> None:
    first_claims, first_state = ignition.evaluate(
        build_snapshot(cutoff=CUTOFF), build_state(), CUTOFF
    )
    assert first_claims == []
    assert first_state.persistence_seconds == 60
    second_claims, second_state = ignition.evaluate(
        build_snapshot(cutoff=SECOND_CUTOFF), first_state, SECOND_CUTOFF
    )
    assert len(second_claims) == 1
    assert second_state.phase is DetectorPhase.IGNITION
    assert second_state.persistence_seconds == 0


def test_hold_boundary_keeps_ignition_and_failed_hold_releases() -> None:
    ignition_state = complete_entry()

    hold_claims, held_state = ignition.evaluate(
        build_snapshot(values=HOLD_BOUNDARY_VALUES, cutoff=THIRD_CUTOFF),
        ignition_state,
        THIRD_CUTOFF,
    )
    assert hold_claims == []
    assert held_state.phase is DetectorPhase.IGNITION
    assert held_state.persistence_seconds == 0

    first_failure = replace(
        held_state, last_evaluation_cutoff=THIRD_CUTOFF
    )
    fourth = THIRD_CUTOFF + timedelta(seconds=60)
    failed_claims, failed_state = ignition.evaluate(
        build_snapshot(values=HOLD_FAILED_VALUES, cutoff=fourth),
        first_failure,
        fourth,
    )
    assert failed_claims == []
    assert failed_state.phase is DetectorPhase.IGNITION
    assert failed_state.persistence_seconds == 60

    fifth = fourth + timedelta(seconds=60)
    released_claims, released_state = ignition.evaluate(
        build_snapshot(values=HOLD_FAILED_VALUES, cutoff=fifth),
        failed_state,
        fifth,
    )
    assert released_claims == []
    assert released_state.phase is DetectorPhase.DORMANT
    assert released_state.persistence_seconds == 0
    assert released_state.transition is DetectorTransition.IGNITION_TO_DORMANT
    assert released_state.reset_reason is DetectorResetReason.NONE


def test_release_requires_a_full_re_entry_sequence() -> None:
    igniting = build_state(phase=DetectorPhase.IGNITION, persistence_seconds=60)
    released_claims, released_state = ignition.evaluate(
        build_snapshot(values=HOLD_FAILED_VALUES), igniting, CUTOFF
    )
    assert released_claims == []
    assert released_state.phase is DetectorPhase.DORMANT

    re_entry_one, re_entry_state = ignition.evaluate(
        build_snapshot(cutoff=SECOND_CUTOFF), released_state, SECOND_CUTOFF
    )
    assert re_entry_one == []
    assert re_entry_state.phase is DetectorPhase.DORMANT
    assert re_entry_state.persistence_seconds == 60

    re_entry_two, re_entry_done = ignition.evaluate(
        build_snapshot(cutoff=THIRD_CUTOFF), re_entry_state, THIRD_CUTOFF
    )
    assert len(re_entry_two) == 1
    assert re_entry_done.phase is DetectorPhase.IGNITION


def test_entry_confirmation_boundaries_are_exact() -> None:
    # Exactly 1.25 volume expansion counts; just below it does not.
    at_boundary, _ = ignition.evaluate(
        build_snapshot(
            values={**BOUNDARY_TWO_CONFIRMATIONS, "acceleration_5m_vs_15m": 0.0}
        ),
        build_state(),
        CUTOFF,
    )
    assert at_boundary == []

    below_boundary = {
        **BOUNDARY_TWO_CONFIRMATIONS,
        "volume_expansion_5m_vs_20m": 1.2499999,
    }
    _, below_state = ignition.evaluate(
        build_snapshot(values=below_boundary), build_state(), CUTOFF
    )
    assert below_state.persistence_seconds == 0

    # Exactly 0.35 compression release counts; just below it does not.
    compression_only = {
        **BOUNDARY_TWO_CONFIRMATIONS,
        "volume_expansion_5m_vs_20m": 1.0,
    }
    _, compression_state = ignition.evaluate(
        build_snapshot(values=compression_only), build_state(), CUTOFF
    )
    assert compression_state.persistence_seconds == 0

    just_below_compression = {
        **BOUNDARY_TWO_CONFIRMATIONS,
        "volume_expansion_5m_vs_20m": 1.0,
        "compression_release_score": 0.34,
    }
    _, just_below_state = ignition.evaluate(
        build_snapshot(values=just_below_compression), build_state(), CUTOFF
    )
    assert just_below_state.persistence_seconds == 0

    # Acceleration must be strictly positive to confirm.
    zero_acceleration_volume_only = {
        "trend_state": TrendState.UP.value,
        "return_5m": 0.4,
        "acceleration_5m_vs_15m": 0.0,
        "volume_expansion_5m_vs_20m": 1.30,
        "compression_release_score": 0.0,
    }
    _, single_confirmation = ignition.evaluate(
        build_snapshot(values=zero_acceleration_volume_only), build_state(), CUTOFF
    )
    assert single_confirmation.persistence_seconds == 0


def test_claim_reasons_are_deterministic_and_sorted() -> None:
    claims, _ = evaluate_entry_sequence()
    reasons = list(claims[0].reasons)
    assert reasons[0] == ignition.REASON_DIRECTIONAL_CORE
    assert sorted(reasons[1:]) == reasons[1:]
    assert set(reasons[1:]) == {
        ignition.REASON_ENTRY_ACCELERATION,
        ignition.REASON_ENTRY_VOLUME_EXPANSION,
        ignition.REASON_ENTRY_COMPRESSION_RELEASE,
    }
    assert json.dumps(list(claims[0].to_dict()["reasons"])) == json.dumps(reasons)


def test_debounce_is_the_persistence_rule_not_an_extra_cooldown() -> None:
    assert ignition.DEBOUNCE_INTERVALS == 0
    assert ignition.ENTRY_MIN_CONFIRMATIONS == 2
    assert ignition.HOLD_MIN_CONFIRMATIONS == 1
    # Re-entry after a release is possible on the next completed persistence run.
    state = build_state(persistence_seconds=PERSISTENCE_INTERVAL_SECONDS)
    claims, next_state = ignition.evaluate(build_snapshot(), state, CUTOFF)
    assert len(claims) == 1
    assert next_state.phase is DetectorPhase.IGNITION


def test_direct_construction_refuses_identity_that_does_not_match_its_evidence() -> None:
    """A claim identifier is a pure function of its evidence, so it is enforced."""
    snapshot = build_snapshot(cutoff=SECOND_CUTOFF)
    fingerprint = detector_input_fingerprint(snapshot)
    valid_id, valid_key = detector_claim_identity(
        detector_family=DetectorFamily.IGNITION,
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id=VENUE_INSTRUMENT_ID,
        transition=DetectorTransition.DORMANT_TO_IGNITION,
        snapshot_id=snapshot.snapshot_id,
        detector_input_fingerprint=fingerprint,
    )
    evidence = {
        "detector_family": DetectorFamily.IGNITION,
        "detector_version": IGNITION_DETECTOR_VERSION,
        "policy_version": IGNITION_POLICY_VERSION,
        "instrument_version_id": INSTRUMENT_VERSION_ID,
        "venue_instrument_id": VENUE_INSTRUMENT_ID,
        "phase": DetectorPhase.IGNITION,
        "transition": DetectorTransition.DORMANT_TO_IGNITION,
        "snapshot_id": snapshot.snapshot_id,
        "detector_input_fingerprint": fingerprint,
        "evaluation_cutoff": SECOND_CUTOFF,
    }

    # The matching identity is accepted.
    assert DetectorClaim(
        claim_id=valid_id, idempotency_key=valid_key, **evidence
    ).claim_id == valid_id

    # Non-empty but incorrect identifiers are refused.
    for override in (
        {"claim_id": "DCLM:not-the-evidence", "idempotency_key": valid_key},
        {"claim_id": valid_id, "idempotency_key": "DCLMKEY:not-the-evidence"},
        {"claim_id": valid_key, "idempotency_key": valid_id},
    ):
        with pytest.raises(DetectorContractError):
            DetectorClaim(**{**evidence, **override})

    # Identity is bound to the evidence, so different evidence needs a different id.
    other = build_snapshot(cutoff=THIRD_CUTOFF)
    with pytest.raises(DetectorContractError):
        DetectorClaim(
            claim_id=valid_id,
            idempotency_key=valid_key,
            **{
                **evidence,
                "snapshot_id": other.snapshot_id,
                "detector_input_fingerprint": detector_input_fingerprint(other),
                "evaluation_cutoff": THIRD_CUTOFF,
            },
        )


def test_all_sixteen_acceptance_tests_execute_and_cite_both_increments() -> None:
    """No F3 acceptance test may be skipped, and each must cite both increments."""
    source = TEST_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # No test may skip or xfail, by any pytest form.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"skip", "skipif", "xfail"}, ast.dump(node)

    counted: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        marked = False
        for decorator in node.decorator_list:
            func = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(func, ast.Attribute) and func.attr == "acceptance":
                marked = True
        if not marked:
            continue
        docstring = ast.get_docstring(node) or ""
        assert f"{FROZEN_INCREMENT}/AC-" in docstring, node.name
        assert f"{IMPLEMENTATION_INCREMENT}/AC-" in docstring, node.name
        counted[node.name] = docstring

    assert len(counted) == 16
    for index in range(1, 17):
        assert any(f"/AC-{index:03d}" in docstring for docstring in counted.values())


@pytest.mark.parametrize("feature", REQUIRED_FEATURES)
def test_every_required_feature_is_individually_enforced(feature: str) -> None:
    """Each required feature is load-bearing on its own."""
    values = {key: value for key, value in QUALIFYING_VALUES.items() if key != feature}
    snapshot = build_snapshot(
        values=values,
        missingness={key: Missingness.PRESENT for key in values},
    )
    claims, state = ignition.evaluate(
        snapshot, build_state(persistence_seconds=60), CUTOFF
    )
    assert claims == []
    assert_reset(state, DetectorResetReason.INSUFFICIENT_EVIDENCE)
