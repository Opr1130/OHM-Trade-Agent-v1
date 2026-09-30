"""R3 F4 Opportunity Lifecycle: executable acceptance and unit tests.

These tests complete, in place, the contract-stage skeletons frozen by
``ATDD-R3-F4-opportunity-lifecycle`` and satisfy the implementation increment
``ATDD-R3-F4-opportunity-lifecycle-implementation``. Every acceptance test cites
**both** increments, so the frozen contract's traceability and the implementation
increment's traceability each hold. No F4 acceptance test is skipped.

Every fixture here is a deterministic literal. The tests read no network, no wall
clock, no random values and no filesystem state, and they assert that the
lifecycle under test does the same.
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
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.contracts.detector import (  # noqa: E402
    IGNITION_DETECTOR_VERSION,
    IGNITION_POLICY_VERSION,
    DetectorClaim,
    DetectorContractError,
    DetectorFamily,
    DetectorPhase,
    DetectorTransition,
)
from app.opip.contracts import opportunity as opportunity_contract  # noqa: E402
from app.opip.contracts.opportunity import (  # noqa: E402
    OPPORTUNITY_EPISODE_SCHEMA_VERSION,
    OPPORTUNITY_LIFECYCLE_VERSION,
    OPPORTUNITY_POLICY_VERSION,
    OpportunityContractError,
    OpportunityDeferral,
    OpportunityEpisode,
    OpportunityLifecycleEvent,
    OpportunityLifecycleEventType,
    OpportunityLifecyclePolicy,
    OpportunityLifecycleState,
    OpportunityTerminalReason,
    opportunity_episode_identity,
    opportunity_event_identity,
)
from app.opip import opportunity_lifecycle as lifecycle  # noqa: E402

FROZEN_INCREMENT = "ATDD-R3-F4-opportunity-lifecycle"
IMPLEMENTATION_INCREMENT = "ATDD-R3-F4-opportunity-lifecycle-implementation"
EXPECTED_AC_COUNT = 17

# ---------------------------------------------------------------------------
# Deterministic fixtures
# ---------------------------------------------------------------------------

CUTOFF = datetime(2026, 9, 11, 15, 1, tzinfo=timezone.utc)
DEFER_DEADLINE = datetime(2026, 9, 11, 15, 31, tzinfo=timezone.utc)
VALIDITY_DEADLINE = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)
AFTER_DEADLINE = DEFER_DEADLINE + timedelta(minutes=1)
BEFORE_DEADLINE = DEFER_DEADLINE - timedelta(minutes=1)

INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
VENUE_INSTRUMENT_ID = "SOLUSD"
OTHER_SNAPSHOT_ID = "SNAP:r3f4-fixture-other"
OUR_SNAPSHOT_ID = "SNAP:r3f4-fixture"

POLICY = OpportunityLifecyclePolicy()


def build_claim(
    *,
    cutoff: datetime = CUTOFF,
    snapshot_id: str = OUR_SNAPSHOT_ID,
    detector_input_fingerprint: str = "DETIN:r3f4-fixture",
    instrument_version_id: str = INSTRUMENT_VERSION_ID,
    venue_instrument_id: str = VENUE_INSTRUMENT_ID,
) -> DetectorClaim:
    """Build a genuine, internally-valid F3 IGNITION claim from literals."""
    return DetectorClaim.create(
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=instrument_version_id,
        venue_instrument_id=venue_instrument_id,
        snapshot_id=snapshot_id,
        detector_input_fingerprint=detector_input_fingerprint,
        evaluation_cutoff=cutoff,
    )


def deferral(
    *,
    defer: datetime = DEFER_DEADLINE,
    validity: datetime = VALIDITY_DEADLINE,
) -> OpportunityDeferral:
    return OpportunityDeferral(defer_deadline=defer, validity_deadline=validity)


def create_active(claim: DetectorClaim | None = None) -> OpportunityEpisode:
    chosen = claim if claim is not None else build_claim()
    return lifecycle.apply_claim(
        chosen, None, chosen.evaluation_cutoff, POLICY
    ).episode


def create_deferred(claim: DetectorClaim | None = None) -> OpportunityEpisode:
    chosen = claim if claim is not None else build_claim()
    return lifecycle.apply_claim(
        chosen,
        None,
        chosen.evaluation_cutoff,
        POLICY,
        deferral_request=deferral(),
    ).episode


# ---------------------------------------------------------------------------
# Source-level probes
# ---------------------------------------------------------------------------

LIFECYCLE_PATH = APP_ROOT / "app" / "opip" / "opportunity_lifecycle.py"
CONTRACT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "opportunity.py"
CONTRACTS_INIT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "__init__.py"
F3_DETECTOR_PATH = APP_ROOT / "app" / "opip" / "detectors" / "ignition.py"
F3_CONTRACT_PATH = APP_ROOT / "app" / "opip" / "contracts" / "detector.py"
FROZEN_CONTRACT_PATH = (
    APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{FROZEN_INCREMENT}.md"
)
IMPLEMENTATION_CONTRACT_PATH = (
    APP_ROOT
    / "docs"
    / "atdd"
    / "scope-contracts"
    / f"{IMPLEMENTATION_INCREMENT}.md"
)
COMPOSE_PATH = APP_ROOT / "docker-compose.yml"
RUN_CYCLE_PATH = APP_ROOT / "app" / "jobs" / "run_cycle.py"
TEST_PATH = Path(__file__).resolve()

LIFECYCLE_SOURCE = LIFECYCLE_PATH.read_text(encoding="utf-8")
CONTRACT_SOURCE = CONTRACT_PATH.read_text(encoding="utf-8")

AUTHORIZED_PATHS = frozenset(
    str(path)
    for path in (
        LIFECYCLE_PATH,
        CONTRACT_PATH,
        CONTRACTS_INIT_PATH,
        # The separately OWNER-authorized R3 F4 persistence increment
        # (ATDD-R3-F4-opportunity-lifecycle-persistence) authorizes the F4
        # persistence vocabulary module and the canonical writer's IPC event-type
        # literal to name the durable F4 event type/stream. This widens the
        # allow-list only; no assertion below is removed or relaxed, and no other
        # runtime path may import the F4 lifecycle.
        APP_ROOT / "app" / "opip" / "contracts" / "opportunity_persistence.py",
        APP_ROOT / "app" / "opip" / "canonical" / "models.py",
    )
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
    "app.opip.data_platform",
    "app.opip.profit_intelligence",
    "app.opip.detectors",
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
    "protect",
    "route",
)


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
            raise AssertionError("lifecycle performed filesystem access")

        def _no_network(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("lifecycle performed network access")

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
# AC-001 .. AC-017
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_episode_identity_is_deterministic() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-001: episode identity is deterministic and never wall-clock/UUID/process/order/retry. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-001: identity binds schema version and claim_id through the existing canonical helpers."""
    claim = build_claim()

    first = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    second = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    assert first.episode.episode_id == second.episode.episode_id
    assert first.episode.episode_id.startswith("OPEP:")

    # The identity is exactly the pure function of schema version and claim id.
    expected = opportunity_episode_identity(
        episode_schema_version=OPPORTUNITY_EPISODE_SCHEMA_VERSION,
        claim_id=claim.claim_id,
    )
    assert first.episode.episode_id == expected

    # A different claim produces a different episode identity.
    other = build_claim(snapshot_id=OTHER_SNAPSHOT_ID)
    assert (
        lifecycle.apply_claim(other, None, other.evaluation_cutoff, POLICY).episode.episode_id
        != expected
    )

    # No wall clock participates: the identity is identical at any clock value.
    real_time = time.time
    time.time = lambda: 1.0
    try:
        early = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    finally:
        time.time = real_time
    time.time = lambda: 4102444800.0
    try:
        late = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    finally:
        time.time = real_time
    assert early.episode.episode_id == late.episode.episode_id == expected

    # A forged episode identifier cannot be constructed directly.
    valid = first.episode
    with pytest.raises(OpportunityContractError):
        replace(valid, episode_id="OPEP:forged")
    with pytest.raises(OpportunityContractError):
        replace(valid, episode_id="OPEP:" + "0" * 32)

    # No clock, random or process vocabulary is imported by either core module.
    for source in (LIFECYCLE_SOURCE, CONTRACT_SOURCE):
        assert imported_modules(source).isdisjoint({"uuid", "random", "time", "os"})
        assert called_attributes(source).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)


@pytest.mark.acceptance
def test_ac_002_deduplication_is_at_least_once_safe() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-002: at-least-once delivery creates no duplicate episodes or extra work. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-002: a duplicate is idempotent, never extends a deadline and never expires."""
    claim = build_claim()
    episode = create_deferred(claim)

    duplicate = lifecycle.apply_claim(
        claim, episode, AFTER_DEADLINE, POLICY, deferral_request=deferral()
    )
    assert duplicate.changed is False
    assert duplicate.events == ()
    assert duplicate.episode is episode
    assert duplicate.episode.defer_deadline == DEFER_DEADLINE
    assert duplicate.episode.lifecycle_state is OpportunityLifecycleState.DEFERRED

    # A duplicate delivered at or after the deadline does NOT act as a timer.
    at_deadline = lifecycle.apply_claim(claim, episode, DEFER_DEADLINE, POLICY)
    assert at_deadline.episode.lifecycle_state is OpportunityLifecycleState.DEFERRED
    assert at_deadline.events == ()

    # No second episode is produced by any number of duplicate deliveries.
    seen: set[str] = set()
    for offset in range(0, 5):
        result = lifecycle.apply_claim(
            claim, episode, CUTOFF + timedelta(hours=offset), POLICY
        )
        seen.add(result.episode.episode_id)
        assert result.changed is False
    assert seen == {episode.episode_id}

    # An altered deferral on a duplicate never extends the recorded deadline.
    extended = lifecycle.apply_claim(
        claim,
        episode,
        AFTER_DEADLINE,
        POLICY,
        deferral_request=deferral(
            defer=DEFER_DEADLINE + timedelta(hours=2),
            validity=VALIDITY_DEADLINE + timedelta(hours=2),
        ),
    )
    assert extended.episode.defer_deadline == DEFER_DEADLINE
    assert extended.episode.validity_deadline == VALIDITY_DEADLINE


@pytest.mark.acceptance
def test_ac_003_claim_episode_lineage_reuses_identities() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-003: claim→episode lineage reuses identities and does not reinterpret F3. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-003: the episode carries claim-derived lineage only."""
    claim = build_claim()
    episode = create_active(claim)

    assert episode.source_claim_id == claim.claim_id
    assert episode.source_claim_idempotency_key == claim.idempotency_key
    assert episode.instrument_version_id == claim.instrument_version_id
    assert episode.venue_instrument_id == claim.venue_instrument_id
    assert episode.detector_family is DetectorFamily.IGNITION
    assert episode.detector_version == claim.detector_version
    assert episode.detector_policy_version == claim.policy_version
    assert episode.claim_transition is DetectorTransition.DORMANT_TO_IGNITION
    assert episode.snapshot_id == claim.snapshot_id
    assert episode.detector_input_fingerprint == claim.detector_input_fingerprint
    assert episode.claim_evaluation_cutoff == claim.evaluation_cutoff

    # The episode copies no FeatureSnapshot payload and reinterprets no F3 evidence.
    payload = episode.to_dict()
    for forbidden in (
        "accuracy",
        "threshold",
        "hysteresis",
        "debounce",
        "persistence_seconds",
        "return_5m",
        "volume_expansion",
        "compression_release",
        "reasons",
    ):
        assert forbidden not in payload


@pytest.mark.acceptance
def test_ac_004_lifecycle_state_vocabulary() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-004: lifecycle state covers active/open, deferred and terminal concepts. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-004: states are exactly ACTIVE/DEFERRED/TERMINAL and detector phases are never states."""
    assert {state.value for state in OpportunityLifecycleState} == {
        "ACTIVE",
        "DEFERRED",
        "TERMINAL",
    }

    assert create_active().lifecycle_state is OpportunityLifecycleState.ACTIVE
    assert create_deferred().lifecycle_state is OpportunityLifecycleState.DEFERRED
    expired = lifecycle.evaluate_time(
        create_deferred(), DEFER_DEADLINE, POLICY
    ).episode
    assert expired.lifecycle_state is OpportunityLifecycleState.TERMINAL

    # Detector phases are never episode states.
    for detector_token in ("DORMANT", "IGNITION", "NONE"):
        with pytest.raises(OpportunityContractError):
            opportunity_contract.require_opportunity_enum(
                OpportunityLifecycleState, detector_token, field_name="state"
            )

    # Exact tokens parse; malformed and coercible values fail closed.
    assert (
        opportunity_contract.require_opportunity_enum(
            OpportunityLifecycleState, "DEFERRED", field_name="state"
        )
        is OpportunityLifecycleState.DEFERRED
    )
    for malformed in (0, 1, True, False, 1.0, None, "active"):
        with pytest.raises(OpportunityContractError):
            opportunity_contract.require_opportunity_enum(
                OpportunityLifecycleState, malformed, field_name="state"
            )


@pytest.mark.acceptance
def test_ac_005_deferral_is_explicit_and_not_detector_dormant() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-005: deferral is explicit F4 disposition, not detector DORMANT. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-005: a valid deferral creates a DEFERRED episode with explicit deadlines."""
    claim = build_claim()
    result = lifecycle.apply_claim(
        claim, None, claim.evaluation_cutoff, POLICY, deferral_request=deferral()
    )
    episode = result.episode
    assert episode.lifecycle_state is OpportunityLifecycleState.DEFERRED
    assert episode.defer_deadline == DEFER_DEADLINE
    assert episode.validity_deadline == VALIDITY_DEADLINE
    assert episode.terminal_reason is None
    assert result.changed is True

    # The deferral is recorded as an F4 disposition, not a detector phase.
    assert episode.lifecycle_state is OpportunityLifecycleState.DEFERRED
    assert "DORMANT" not in episode.lifecycle_state.value
    assert "IGNITION" not in episode.lifecycle_state.value
    assert {state.value for state in OpportunityLifecycleState} == {
        "ACTIVE",
        "DEFERRED",
        "TERMINAL",
    }

    # A malformed deferral request fails closed rather than being invented.
    for bad in (object(), "defer", {}, 42):
        with pytest.raises(OpportunityContractError):
            lifecycle.apply_claim(
                claim, None, claim.evaluation_cutoff, POLICY, deferral_request=bad  # type: ignore[arg-type]
            )


@pytest.mark.acceptance
def test_ac_006_deferred_deadlines_are_bounded() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-006: deferred episodes carry deadlines bounded by validity horizon. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-006: deadlines are explicit caller inputs, well-ordered, and no numeric duration is invented."""
    episode = create_deferred()
    assert episode.defer_deadline is not None
    assert episode.validity_deadline is not None
    assert episode.defer_deadline <= episode.validity_deadline

    # No numeric validity duration is invented by this increment.
    for invented in ("DEFAULT_TTL", "VALIDITY_SECONDS", "DEADLINE_MINUTES", "TTL_SECONDS"):
        assert not hasattr(lifecycle, invented)
        assert not hasattr(opportunity_contract, invented)

    # Deadlines are explicit: a missing one fails closed.
    with pytest.raises(OpportunityContractError):
        OpportunityDeferral(
            defer_deadline=DEFER_DEADLINE, validity_deadline=None  # type: ignore[arg-type]
        )
    with pytest.raises(OpportunityContractError):
        OpportunityDeferral(defer_deadline=None, validity_deadline=VALIDITY_DEADLINE)  # type: ignore[arg-type]

    # A deadline beyond validity fails closed.
    with pytest.raises(OpportunityContractError):
        deferral(defer=VALIDITY_DEADLINE + timedelta(seconds=1))

    # A deferral deadline before the creation evaluation time fails closed.
    with pytest.raises(OpportunityContractError):
        lifecycle.apply_claim(
            build_claim(),
            None,
            CUTOFF,
            POLICY,
            deferral_request=deferral(defer=CUTOFF - timedelta(seconds=1)),
        )

    # Non-datetime and naive deadlines fail closed rather than being coerced.
    for bad in ("2026-09-11T15:31:00Z", 123, True):
        with pytest.raises(OpportunityContractError):
            OpportunityDeferral(  # type: ignore[arg-type]
                defer_deadline=bad, validity_deadline=VALIDITY_DEADLINE
            )
    naive = datetime(2026, 9, 11, 15, 31)
    with pytest.raises(OpportunityContractError):
        OpportunityDeferral(defer_deadline=naive, validity_deadline=VALIDITY_DEADLINE)


@pytest.mark.acceptance
def test_ac_007_expiry_is_explicit_terminal() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-007: TIME-DRIVEN expiry at evaluation_time >= deadline needs no new DetectorClaim; duplicates are not a timer. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-007: only evaluate_time expires, exactly once, at the deadline boundary."""
    # evaluate_time takes no claim at all: expiry never requires one.
    parameters = list(inspect.signature(lifecycle.evaluate_time).parameters)
    assert parameters == ["prior_episode", "evaluation_time", "policy"]

    episode = create_deferred()

    # Before the deadline: unchanged, no event.
    early = lifecycle.evaluate_time(episode, BEFORE_DEADLINE, POLICY)
    assert early.changed is False
    assert early.events == ()
    assert early.episode.lifecycle_state is OpportunityLifecycleState.DEFERRED

    # Exactly at the deadline: TERMINAL/EXPIRED with exactly one event.
    at_deadline = lifecycle.evaluate_time(episode, DEFER_DEADLINE, POLICY)
    assert at_deadline.changed is True
    assert at_deadline.episode.lifecycle_state is OpportunityLifecycleState.TERMINAL
    assert at_deadline.episode.terminal_reason is OpportunityTerminalReason.EXPIRED
    assert at_deadline.episode.terminal_evaluation_time == DEFER_DEADLINE
    assert [event.event_type for event in at_deadline.events] == [
        OpportunityLifecycleEventType.EXPIRED
    ]

    # After the deadline (even beyond validity): still a single boundary-crossing expiry.
    after = lifecycle.evaluate_time(episode, AFTER_DEADLINE, POLICY)
    assert after.episode.lifecycle_state is OpportunityLifecycleState.TERMINAL
    assert len(after.events) == 1

    # A duplicate claim is not a timer and cannot expire or extend.
    duplicated = lifecycle.apply_claim(claim=build_claim(), prior_episode=episode, evaluation_time=AFTER_DEADLINE, policy=POLICY)
    assert duplicated.changed is False
    assert duplicated.episode.lifecycle_state is OpportunityLifecycleState.DEFERRED


@pytest.mark.acceptance
def test_ac_008_terminal_reasons_are_f4_vocabulary() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-008: terminal reasons use F4 vocabulary, not funnel/scan codes. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-008: EXPIRED is the only v1 terminal reason."""
    assert {reason.value for reason in OpportunityTerminalReason} == {"EXPIRED"}

    expired = lifecycle.evaluate_time(create_deferred(), DEFER_DEADLINE, POLICY).episode
    assert expired.terminal_reason is OpportunityTerminalReason.EXPIRED

    # Funnel/scan/order/learning vocabularies are not F4 terminal reasons.
    for foreign in (
        "TIMEOUT",
        "NO_FILL",
        "PARTIAL_FILL",
        "FULL_FILL",
        "TARGET",
        "STOP",
        "RISK_EXIT",
        "CANCELLED",
        "PROTECTED",
        "SCAN_REJECTED",
        "LABEL_WIN",
        "normalized",
    ):
        with pytest.raises(OpportunityContractError):
            opportunity_contract.require_opportunity_enum(
                OpportunityTerminalReason, foreign, field_name="terminal_reason"
            )

    # A terminal episode cannot carry a non-EXPIRED reason.
    with pytest.raises(OpportunityContractError):
        replace(expired, terminal_reason="TIMEOUT")
    with pytest.raises(OpportunityContractError):
        replace(expired, terminal_reason=None)


@pytest.mark.acceptance
def test_ac_009_terminality_requires_new_lifecycle() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-009: terminal is terminal; a new lifecycle needs an eligible new claim. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-009: a different claim fails closed while unresolved and opens a new episode after terminal."""
    claim = build_claim()
    active = create_active(claim)
    deferred = create_deferred(claim)

    # A different claim while the prior episode is unresolved fails closed.
    other = build_claim(snapshot_id=OTHER_SNAPSHOT_ID)
    with pytest.raises(OpportunityContractError):
        lifecycle.apply_claim(other, active, other.evaluation_cutoff, POLICY)
    with pytest.raises(OpportunityContractError):
        lifecycle.apply_claim(other, deferred, other.evaluation_cutoff, POLICY)

    terminal = lifecycle.evaluate_time(deferred, DEFER_DEADLINE, POLICY).episode

    # Terminal replay (same claim) is unchanged, including a restart redelivery
    # at the claim's own cutoff, which precedes the terminal instant.
    cutoff_replay = lifecycle.apply_claim(claim, terminal, claim.evaluation_cutoff, POLICY)
    assert cutoff_replay.changed is False
    assert cutoff_replay.events == ()
    assert cutoff_replay.episode is terminal
    assert cutoff_replay.episode.lifecycle_state is OpportunityLifecycleState.TERMINAL

    replay = lifecycle.apply_claim(
        claim, terminal, AFTER_DEADLINE, POLICY, deferral_request=deferral()
    )
    assert replay.changed is False
    assert replay.events == ()
    assert replay.episode is terminal
    assert replay.episode.lifecycle_state is OpportunityLifecycleState.TERMINAL

    # Different claim after terminal opens a NEW episode and never mutates the old.
    newer = build_claim(
        cutoff=CUTOFF + timedelta(hours=1),
        snapshot_id=OTHER_SNAPSHOT_ID,
    )
    fresh = lifecycle.apply_claim(newer, terminal, newer.evaluation_cutoff, POLICY)
    assert fresh.changed is True
    assert fresh.episode.episode_id != terminal.episode_id
    assert fresh.episode.lifecycle_state is OpportunityLifecycleState.ACTIVE
    assert terminal.lifecycle_state is OpportunityLifecycleState.TERMINAL
    assert terminal.terminal_reason is OpportunityTerminalReason.EXPIRED


@pytest.mark.acceptance
def test_ac_010_transition_core_is_pure() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-010: purity covers CLAIM-DRIVEN and TIME-DRIVEN; deterministic/replayable; no I/O or hidden clock. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-010: both surfaces are pure over their declared inputs."""
    for func, expected in (
        (
            lifecycle.apply_claim,
            ["claim", "prior_episode", "evaluation_time", "policy", "deferral_request"],
        ),
        (lifecycle.evaluate_time, ["prior_episode", "evaluation_time", "policy"]),
    ):
        parameters = list(inspect.signature(func).parameters.values())
        assert [parameter.name for parameter in parameters] == expected
        assert not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
        assert not any(
            parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters
        )
    deferral_parameter = inspect.signature(lifecycle.apply_claim).parameters[
        "deferral_request"
    ]
    assert deferral_parameter.default is None

    for source in (LIFECYCLE_SOURCE, CONTRACT_SOURCE):
        modules = imported_modules(source)
        assert modules.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
        for module in modules:
            assert not module.startswith(FORBIDDEN_MODULE_PREFIXES), module
        assert called_attributes(source).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)

    # No module-global mutable lifecycle state.
    for module in (lifecycle, opportunity_contract):
        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            assert not isinstance(value, (dict, list, set, bytearray)), name

    claim = build_claim()
    episode = create_deferred(claim)
    claim_payload = claim.to_dict()
    episode_payload = episode.to_dict()
    with ForbiddenIO():
        created = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
        expired = lifecycle.evaluate_time(episode, DEFER_DEADLINE, POLICY)
    assert created.episode.lifecycle_state is OpportunityLifecycleState.ACTIVE
    assert expired.episode.lifecycle_state is OpportunityLifecycleState.TERMINAL

    # Inputs are never mutated, and a change returns a new episode object.
    assert claim.to_dict() == claim_payload
    assert episode.to_dict() == episode_payload
    assert created.episode is not episode
    assert expired.episode is not episode


@pytest.mark.acceptance
def test_ac_011_replay_is_deterministic() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-011: identical inputs replay to equivalent episode state and events. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-011: replay determinism holds for the implementation increment."""

    def run() -> dict[str, Any]:
        claim = build_claim()
        created = lifecycle.apply_claim(
            claim, None, claim.evaluation_cutoff, POLICY, deferral_request=deferral()
        )
        expired = lifecycle.evaluate_time(created.episode, DEFER_DEADLINE, POLICY)
        return {"created": created.to_dict(), "expired": expired.to_dict()}

    first = run()
    second = run()
    assert first == second

    # A fresh process produces the identical result.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json;"
            "from tests.test_opip_r3_f4_opportunity_lifecycle import run_sequence;"
            "print(json.dumps(run_sequence(), sort_keys=True))",
        ],
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == first


@pytest.mark.acceptance
def test_ac_012_f3_boundary_unchanged() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-012: F3 detector contracts and semantics remain unmodified. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-012: F4 consumes claim vocabulary only and changes no F3 rule."""
    detector_source = F3_CONTRACT_PATH.read_text(encoding="utf-8")
    f3_source = F3_DETECTOR_PATH.read_text(encoding="utf-8")

    # F4 does not import or touch the F3 detector implementation.
    assert "detectors" not in imported_modules(LIFECYCLE_SOURCE)
    assert "ignition" not in imported_modules(LIFECYCLE_SOURCE)
    assert "OpportunityEpisode" not in f3_source
    assert "opportunity_lifecycle" not in f3_source

    # The F3 claim still forces a null episode identity and rejects an F3 claim
    # that tries to carry one.
    assert "episode_id is not None" in detector_source
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
            snapshot_id=OUR_SNAPSHOT_ID,
            detector_input_fingerprint="DETIN:r3f4-fixture",
            evaluation_cutoff=CUTOFF,
            episode_id="episode-1",
        )

    # F4 refuses a claim that is not a genuine IGNITION positive claim.
    with pytest.raises(OpportunityContractError):
        lifecycle.apply_claim(None, None, CUTOFF, POLICY)  # type: ignore[arg-type]


@pytest.mark.acceptance
def test_ac_013_f5_plus_isolation() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-013: F5+/Paper/Committee/Feature Bus authorities are unchanged. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-013: the implementation activates nothing and wires no consumer."""
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose

    for source in (LIFECYCLE_SOURCE, CONTRACT_SOURCE):
        modules = imported_modules(source)
        for module in modules:
            assert not module.startswith(FORBIDDEN_MODULE_PREFIXES), module
        assert modules.isdisjoint(
            {"ccxt", "krakenex", "telegram", "requests", "psycopg2"}
        )

    # No runtime path imports the F4 lifecycle or its contract module.
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if str(path) in AUTHORIZED_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        assert "opip.opportunity_lifecycle" not in text, path
        assert "opportunity_lifecycle" not in text, path
        assert "contracts.opportunity" not in text, path
        assert "apply_claim" not in text, path

    run_cycle = RUN_CYCLE_PATH.read_text(encoding="utf-8")
    assert "opportunity_lifecycle" not in run_cycle
    assert "OpportunityEpisode" not in run_cycle

    # No authority-shaped export.
    for name in lifecycle.__all__:
        lowered = name.lower()
        for token in AUTHORITY_TOKENS:
            assert token not in lowered, (name, token)


@pytest.mark.acceptance
def test_ac_014_current_vs_target_authority() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-014: contract stage transfers no authority from legacy clocks. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-014: the active increment is the F4 implementation and remains shadow."""
    assert IMPLEMENTATION_CONTRACT_PATH.is_file()
    # The globally active increment is intentionally movable: this increment is
    # complete, so a later OWNER-authorized increment (bridge, persistence, or a
    # later F4 slice) legitimately owns the pointer. Freezing it here would block
    # every subsequent approval, so this test no longer pins it. The completed
    # increment stays historically identifiable through its own contract below,
    # and the shadow / no-authority assertions are unchanged.

    lines = IMPLEMENTATION_CONTRACT_PATH.read_text(encoding="utf-8").splitlines()
    assert "INCREMENT:" in lines
    assert lines[lines.index("INCREMENT:") + 1].strip() == IMPLEMENTATION_INCREMENT

    # Both core modules declare themselves shadow / non-authoritative.
    for source in (LIFECYCLE_SOURCE, CONTRACT_SOURCE):
        lowered = source.lower()
        assert "shadow" in lowered
        assert "non-authoritative" in lowered


@pytest.mark.acceptance
def test_ac_015_consumer_census_recorded() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-015: consumer census is recorded for future cutover planning. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-015: the census owners are still recorded in the frozen contract."""
    assert FROZEN_CONTRACT_PATH.is_file()
    frozen = FROZEN_CONTRACT_PATH.read_text(encoding="utf-8")
    for consumer in (
        "scan_opportunities",
        "entry_watch_queue",
        "price_movement_radar",
        "monitor_pending_setups",
        "signal_quality_phase2",
        "timing_ledger",
    ):
        assert consumer in frozen, consumer


@pytest.mark.acceptance
def test_ac_016_cutover_retirement_boundary() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-016: no retirement or deletion; only cutover prerequisites are frozen. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-016: legacy clocks remain present and unmodified."""
    for legacy in (
        APP_ROOT / "app" / "jobs" / "scan_opportunities.py",
        APP_ROOT / "app" / "services" / "entry_watch_queue.py",
        APP_ROOT / "app" / "services" / "price_movement_radar.py",
        APP_ROOT / "app" / "jobs" / "monitor_pending_setups.py",
    ):
        assert legacy.is_file(), legacy

    frozen = FROZEN_CONTRACT_PATH.read_text(encoding="utf-8")
    assert "no legacy clock is retired or deleted" in frozen
    assert "app/opip/opportunity/" in frozen


@pytest.mark.acceptance
def test_ac_017_no_fake_implementation() -> None:
    """ATDD-R3-F4-opportunity-lifecycle/AC-017: no skeleton fabricates lifecycle results or smuggles F4 application runtime. ATDD-R3-F4-opportunity-lifecycle-implementation/AC-017: every acceptance test executes and the runtime stays isolated."""
    source = TEST_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # No acceptance skeleton skips, xfails or fabricates a result.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"skip", "skipif", "xfail"}, ast.dump(node)

    counted: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_ac_"):
            continue
        marked = any(
            isinstance(decorator, ast.Attribute) and decorator.attr == "acceptance"
            for decorator in node.decorator_list
        )
        assert marked, node.name
        docstring = ast.get_docstring(node) or ""
        assert f"{FROZEN_INCREMENT}/AC-" in docstring, node.name
        assert f"{IMPLEMENTATION_INCREMENT}/AC-" in docstring, node.name
        counted.add(node.name)
    assert len(counted) == EXPECTED_AC_COUNT

    # The forbidden second spine is never created.
    assert not (APP_ROOT / "app" / "opip" / "opportunity").exists()

    # This test module imports no forbidden runtime path.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.opip.opportunity")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("app.opip.opportunity")
            assert module != "app.services"


# ---------------------------------------------------------------------------
# Focused unit cases for the same policy (not acceptance-marked by design)
# ---------------------------------------------------------------------------


def run_sequence() -> dict[str, Any]:
    """Deterministic claim-driven-then-time-driven sequence for replay checks."""
    claim = build_claim()
    created = lifecycle.apply_claim(
        claim, None, claim.evaluation_cutoff, POLICY, deferral_request=deferral()
    )
    expired = lifecycle.evaluate_time(created.episode, DEFER_DEADLINE, POLICY)
    return {"created": created.to_dict(), "expired": expired.to_dict()}


def test_creation_without_deferral_is_active_with_one_opened_event() -> None:
    claim = build_claim()
    result = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    assert result.episode.lifecycle_state is OpportunityLifecycleState.ACTIVE
    assert result.episode.defer_deadline is None
    assert result.episode.validity_deadline is None
    assert [event.event_type for event in result.events] == [
        OpportunityLifecycleEventType.OPENED
    ]
    assert result.changed is True


def test_creation_into_deferred_emits_one_deferred_event_only() -> None:
    """The minimal unambiguous model: creation directly into DEFERRED, no double count."""
    claim = build_claim()
    result = lifecycle.apply_claim(
        claim, None, claim.evaluation_cutoff, POLICY, deferral_request=deferral()
    )
    assert result.episode.lifecycle_state is OpportunityLifecycleState.DEFERRED
    assert [event.event_type for event in result.events] == [
        OpportunityLifecycleEventType.DEFERRED
    ]
    assert len(result.events) == 1


def test_creation_requires_evaluation_time_to_equal_claim_cutoff() -> None:
    claim = build_claim()
    for wrong in (CUTOFF - timedelta(seconds=1), CUTOFF + timedelta(seconds=1)):
        with pytest.raises(OpportunityContractError):
            lifecycle.apply_claim(claim, None, wrong, POLICY)


def test_event_identity_is_deterministic_and_matches_payload() -> None:
    claim = build_claim()
    first = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    second = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    assert first.events[0].event_id == second.events[0].event_id
    assert first.events[0].event_id.startswith("OPEV:")

    event = first.events[0]
    expected = opportunity_event_identity(
        episode_id=event.episode_id,
        event_type=event.event_type,
        evaluation_time=event.evaluation_time,
        lifecycle_version=event.lifecycle_version,
        policy_version=event.policy_version,
        source_claim_id=event.source_claim_id,
    )
    assert event.event_id == expected
    with pytest.raises(OpportunityContractError):
        replace(event, event_id="OPEV:forged")


def test_expired_event_identity_is_deterministic_across_episodes_of_same_claim() -> None:
    episode = create_deferred()
    first = lifecycle.evaluate_time(episode, DEFER_DEADLINE, POLICY)
    second = lifecycle.evaluate_time(episode, DEFER_DEADLINE, POLICY)
    assert first.events[0].event_id == second.events[0].event_id
    assert first.events[0].source_claim_id is None


def test_terminal_replay_emits_zero_events() -> None:
    terminal = lifecycle.evaluate_time(create_deferred(), DEFER_DEADLINE, POLICY).episode
    replay = lifecycle.evaluate_time(terminal, AFTER_DEADLINE, POLICY)
    assert replay.changed is False
    assert replay.events == ()
    assert replay.episode is terminal


def test_evaluate_time_returns_the_same_object_when_unchanged() -> None:
    active = create_active()
    assert lifecycle.evaluate_time(active, AFTER_DEADLINE, POLICY).episode is active
    deferred = create_deferred()
    assert (
        lifecycle.evaluate_time(deferred, BEFORE_DEADLINE, POLICY).episode is deferred
    )


def test_time_regression_fails_closed_on_both_paths() -> None:
    claim = build_claim()
    created = lifecycle.apply_claim(
        claim, None, claim.evaluation_cutoff, POLICY, deferral_request=deferral()
    ).episode
    regressed = claim.evaluation_cutoff - timedelta(seconds=1)
    with pytest.raises(OpportunityContractError):
        lifecycle.evaluate_time(created, regressed, POLICY)

    # A duplicate redelivery is idempotent and never rewrites history, even at an
    # earlier delivery instant: it returns the recorded episode unchanged.
    duplicate = lifecycle.apply_claim(claim, created, regressed, POLICY)
    assert duplicate.changed is False
    assert duplicate.events == ()
    assert duplicate.episode is created

    terminal = lifecycle.evaluate_time(created, DEFER_DEADLINE, POLICY).episode
    newer = build_claim(cutoff=CUTOFF + timedelta(hours=2), snapshot_id=OTHER_SNAPSHOT_ID)
    with pytest.raises(OpportunityContractError):
        lifecycle.apply_claim(
            newer, terminal, terminal.last_evaluation_time - timedelta(seconds=1), POLICY
        )


def test_duplicate_at_claim_cutoff_against_terminal_stays_terminal() -> None:
    """AC-002/AC-009: a restart redelivery at the claim's own cutoff is idempotent."""
    claim = build_claim()
    deferred = create_deferred(claim)
    terminal = lifecycle.evaluate_time(deferred, DEFER_DEADLINE, POLICY).episode
    assert terminal.last_evaluation_time == DEFER_DEADLINE

    # The claim cutoff is EARLIER than the terminal instant; a restart redelivery
    # at that cutoff must still return the recorded terminal outcome unchanged.
    replay = lifecycle.apply_claim(claim, terminal, claim.evaluation_cutoff, POLICY)
    assert replay.changed is False
    assert replay.events == ()
    assert replay.episode is terminal
    assert replay.episode.lifecycle_state is OpportunityLifecycleState.TERMINAL
    assert replay.episode.terminal_reason is OpportunityTerminalReason.EXPIRED


def test_deferral_request_on_a_duplicate_never_defers_an_active_episode() -> None:
    """The ratified v1 policy defers only at creation; a duplicate stays unchanged."""
    claim = build_claim()
    active = create_active(claim)
    result = lifecycle.apply_claim(
        claim, active, claim.evaluation_cutoff, POLICY, deferral_request=deferral()
    )
    assert result.changed is False
    assert result.events == ()
    assert result.episode is active
    assert result.episode.lifecycle_state is OpportunityLifecycleState.ACTIVE
    assert result.episode.defer_deadline is None


def test_episode_version_fields_are_bound_to_the_ratified_policy() -> None:
    episode = create_active()
    for override in (
        {"policy_version": "opportunity-shadow-policy-v2"},
        {"lifecycle_version": "opportunity-lifecycle-v2"},
        {"episode_schema_version": "opportunity-episode-v2"},
    ):
        with pytest.raises(OpportunityContractError):
            replace(episode, **override)


def test_prior_episode_with_mismatched_versions_fails_closed_on_both_surfaces() -> None:
    episode = create_active()
    mutated = replace(episode)
    object.__setattr__(mutated, "policy_version", "opportunity-shadow-policy-v2")
    claim = build_claim()
    with pytest.raises(OpportunityContractError):
        lifecycle.apply_claim(claim, mutated, claim.evaluation_cutoff, POLICY)
    with pytest.raises(OpportunityContractError):
        lifecycle.evaluate_time(mutated, AFTER_DEADLINE, POLICY)


def test_episode_lineage_fields_must_reproduce_the_claim_identity() -> None:
    episode = create_active()
    for override in (
        {"snapshot_id": "SNAP:tampered"},
        {"instrument_version_id": "INSTR:kraken:BTC:USD:1"},
        {"venue_instrument_id": "BTCUSD"},
        {"detector_input_fingerprint": "DETIN:tampered"},
        {"source_claim_idempotency_key": "DCLMKEY:tampered"},
        {"detector_version": "ignition-detector-v2"},
        {"detector_policy_version": "ignition-shadow-policy-v2"},
    ):
        with pytest.raises(OpportunityContractError):
            replace(episode, **override)


def test_active_episode_rejects_impossible_time_ordering() -> None:
    episode = create_active()
    with pytest.raises(OpportunityContractError):
        replace(episode, last_evaluation_time=episode.claim_evaluation_cutoff - timedelta(seconds=1))


def test_naive_and_malformed_evaluation_times_fail_closed() -> None:
    claim = build_claim()
    for bad in (
        datetime(2026, 9, 11, 15, 1),
        "2026-09-11T15:01:00Z",
        123,
        True,
        None,
    ):
        with pytest.raises(OpportunityContractError):
            lifecycle.apply_claim(claim, None, bad, POLICY)  # type: ignore[arg-type]
    episode = create_active()
    for bad in (datetime(2026, 9, 11, 15, 1), "x", 1):
        with pytest.raises(OpportunityContractError):
            lifecycle.evaluate_time(episode, bad, POLICY)  # type: ignore[arg-type]


def test_non_utc_aware_evaluation_time_is_normalized() -> None:
    claim = build_claim()
    other_zone = claim.evaluation_cutoff.astimezone(timezone(timedelta(hours=2)))
    result = lifecycle.apply_claim(claim, None, other_zone, POLICY)
    assert result.episode.last_evaluation_time == claim.evaluation_cutoff


def test_policy_version_binding_fails_closed() -> None:
    for override in (
        {"policy_version": "opportunity-shadow-policy-v2"},
        {"lifecycle_version": "opportunity-lifecycle-v2"},
        {"episode_schema_version": "opportunity-episode-v2"},
    ):
        with pytest.raises(OpportunityContractError):
            OpportunityLifecyclePolicy(**override)
    with pytest.raises(OpportunityContractError):
        lifecycle.apply_claim(build_claim(), None, CUTOFF, object())  # type: ignore[arg-type]
    assert OPPORTUNITY_POLICY_VERSION == "opportunity-shadow-policy-v1"
    assert OPPORTUNITY_LIFECYCLE_VERSION == "opportunity-lifecycle-v1"
    assert OPPORTUNITY_EPISODE_SCHEMA_VERSION == "opportunity-episode-v1"


def test_forged_or_mismatched_episode_lineage_fails_closed() -> None:
    episode = create_active()
    with pytest.raises(OpportunityContractError):
        replace(episode, source_claim_id="DCLM:mismatch")
    with pytest.raises(OpportunityContractError):
        replace(episode, episode_id="OPEP:forged")
    with pytest.raises(OpportunityContractError):
        replace(episode, lifecycle_state=OpportunityLifecycleState.TERMINAL)
    with pytest.raises(OpportunityContractError):
        replace(
            episode,
            lifecycle_state=OpportunityLifecycleState.DEFERRED,
        )
    # An ACTIVE episode cannot smuggle terminal state.
    with pytest.raises(OpportunityContractError):
        replace(
            episode,
            terminal_reason=OpportunityTerminalReason.EXPIRED,
            terminal_evaluation_time=CUTOFF,
        )


def test_boolean_and_string_coercion_fail_closed_in_episode_construction() -> None:
    episode = create_active()
    for override in (
        {"lifecycle_state": True},
        {"lifecycle_state": 1},
        {"lifecycle_state": "active"},
        {"last_evaluation_time": "2026-09-11T15:01:00Z"},
        {"last_evaluation_time": 1},
        {"episode_id": 123},
    ):
        with pytest.raises(OpportunityContractError):
            replace(episode, **override)


def test_result_event_must_reference_the_result_episode() -> None:
    claim = build_claim()
    created = lifecycle.apply_claim(claim, None, claim.evaluation_cutoff, POLICY)
    foreign_event = OpportunityLifecycleEvent(
        event_id=opportunity_event_identity(
            episode_id="OPEP:" + "a" * 32,
            event_type=OpportunityLifecycleEventType.OPENED,
            evaluation_time=claim.evaluation_cutoff,
            source_claim_id=claim.claim_id,
        ),
        event_type=OpportunityLifecycleEventType.OPENED,
        episode_id="OPEP:" + "a" * 32,
        lifecycle_version=OPPORTUNITY_LIFECYCLE_VERSION,
        policy_version=OPPORTUNITY_POLICY_VERSION,
        evaluation_time=claim.evaluation_cutoff,
        source_claim_id=claim.claim_id,
    )
    with pytest.raises(OpportunityContractError):
        opportunity_contract.OpportunityLifecycleResult(
            episode=created.episode, events=(foreign_event,), changed=True
        )


def test_implementation_guard_ac_count_traceability_and_no_smuggling() -> None:
    """Guard: AC count, both-increment citations, shared structure, no second spine."""
    contract = IMPLEMENTATION_CONTRACT_PATH.read_text(encoding="utf-8")
    assert f"INCREMENT:\n{IMPLEMENTATION_INCREMENT}" in contract.replace("\r\n", "\n")
    import re

    assert sorted(set(re.findall(r"^AC-(\d{3}):\s*$", contract, flags=re.MULTILINE))) == [
        f"{i:03d}" for i in range(1, EXPECTED_AC_COUNT + 1)
    ]

    tree = ast.parse(TEST_PATH.read_text(encoding="utf-8"), filename=str(TEST_PATH))
    acceptance_names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_ac_")
    }
    assert len(acceptance_names) == EXPECTED_AC_COUNT

    assert not (APP_ROOT / "app" / "opip" / "opportunity").exists()
