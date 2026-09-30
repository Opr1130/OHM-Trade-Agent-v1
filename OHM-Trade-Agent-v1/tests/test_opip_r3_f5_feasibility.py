"""R3 F5 Feasibility & Safety: executable acceptance and unit tests.

These tests satisfy the increment ``ATDD-R3-F5-feasibility-safety``. Every
acceptance test cites ``ATDD-R3-F5-feasibility-safety/AC-NNN`` so the increment's
traceability holds. No acceptance test is skipped.

Every fixture here is a deterministic literal built from real repository types
(``MarketSnapshot``, ``MarketDataValidation``, ``ExecutionValidation``,
``DetectorClaim``, the F4 ``OpportunityEpisode``). The tests read no network, no
wall clock and no randomness, and they assert that the seam under test does the
same.
"""

from __future__ import annotations

import ast
import builtins
import socket
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

from app.opip import feasibility as seam  # noqa: E402
from app.opip import opportunity_lifecycle as lifecycle  # noqa: E402
from app.opip.contracts.detector import (  # noqa: E402
    IGNITION_DETECTOR_VERSION,
    IGNITION_POLICY_VERSION,
    DetectorClaim,
)
from app.opip.contracts.feasibility import (  # noqa: E402
    FEASIBILITY_CHECK_ORDER,
    FEASIBILITY_DECISION_SCHEMA_VERSION,
    FEASIBILITY_POLICY_VERSION,
    FEASIBILITY_VERSION,
    FeasibilityCheck,
    FeasibilityCheckName,
    FeasibilityCheckStatus,
    FeasibilityContractError,
    FeasibilityDecision,
    FeasibilityDisposition,
    FeasibilityPolicy,
    feasibility_decision_identity,
)
from app.opip.contracts.opportunity import (  # noqa: E402
    OpportunityDeferral,
    OpportunityLifecyclePolicy,
    OpportunityLifecycleState,
)
from app.scanner.execution_validation import (  # noqa: E402
    COMPLETE,
    FRESH,
    INVALID as EXECUTION_INVALID,
    UNAVAILABLE as EXECUTION_UNAVAILABLE,
    VALID as EXECUTION_VALID,
    ExecutionValidation,
)
from app.scanner.market_data_validation import (  # noqa: E402
    MarketDataValidation,
    PASS as MARKET_PASS,
    REJECT as MARKET_REJECT,
    WARN as MARKET_WARN,
)
from app.scanner.models import MarketSnapshot  # noqa: E402

INCREMENT = "ATDD-R3-F5-feasibility-safety"
FROZEN_F4_INCREMENT = "ATDD-R3-F4-opportunity-lifecycle-implementation"

CUTOFF = datetime(2026, 9, 11, 15, 1, tzinfo=timezone.utc)
DEFER_DEADLINE = datetime(2026, 9, 11, 15, 31, tzinfo=timezone.utc)
VALIDITY_DEADLINE = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)

POLICY = FeasibilityPolicy()
F4_POLICY = OpportunityLifecyclePolicy()

FEASIBILITY_PATH = APP_ROOT / "app" / "opip" / "feasibility.py"
VOCABULARY_PATH = APP_ROOT / "app" / "opip" / "contracts" / "feasibility.py"
F4_TEST_PATH = APP_ROOT / "tests" / "test_opip_r3_f4_opportunity_lifecycle.py"
ACTIVE_INCREMENT_PATH = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
COMPOSE_PATH = APP_ROOT / "docker-compose.yml"
RUN_CYCLE_PATH = APP_ROOT / "app" / "jobs" / "run_cycle.py"
SCAN_PATH = APP_ROOT / "app" / "jobs" / "scan_opportunities.py"

FEASIBILITY_SOURCE = FEASIBILITY_PATH.read_text(encoding="utf-8")
VOCABULARY_SOURCE = VOCABULARY_PATH.read_text(encoding="utf-8")

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "os",
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
        "shutil",
        "tempfile",
        "ccxt",
        "krakenex",
        "telegram",
        "psycopg2",
        "sqlalchemy",
        "openai",
        "anthropic",
    }
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
        "random",
        "randint",
    }
)

EXCLUDED_LEGACY_GATES = (
    "evaluate_recommendation_gate_item",
    "evaluate_deterministic_quality_gate",
    "target_quality_gate_from_result",
    "evaluate_target_quality_gate",
    "economic_quality_gate_from_result",
    "evaluate_economic_quality_gate",
    "evaluate_cross_market_gate",
    "evaluate_reference_gate",
    "evaluate_market_intelligence_gate",
)


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def called_attributes(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def called_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def numeric_constants(source: str) -> list[Any]:
    values: list[Any] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, bool):
                continue
            values.append(node.value)
    return values


class ForbiddenIO:
    """Make filesystem and network access explode inside the with-block."""

    def __enter__(self) -> "ForbiddenIO":
        self._open = builtins.open
        self._socket = socket.socket
        self._create_connection = socket.create_connection

        def _no_filesystem(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("feasibility performed filesystem access")

        def _no_network(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("feasibility performed network access")

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
    *, venue_instrument_id: str = "SOLUSD", instrument_version_id: str = "INSTR:kraken:SOL:USD:1"
) -> DetectorClaim:
    return DetectorClaim.create(
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=instrument_version_id,
        venue_instrument_id=venue_instrument_id,
        snapshot_id="SNAP:r3f5-fixture",
        detector_input_fingerprint="DETIN:r3f5-fixture",
        evaluation_cutoff=CUTOFF,
    )


def active_episode(claim: DetectorClaim | None = None):
    chosen = claim if claim is not None else build_claim()
    return lifecycle.apply_claim(
        chosen, None, chosen.evaluation_cutoff, F4_POLICY
    ).episode


def deferred_episode():
    claim = build_claim()
    return lifecycle.apply_claim(
        claim,
        None,
        claim.evaluation_cutoff,
        F4_POLICY,
        deferral_request=OpportunityDeferral(
            defer_deadline=DEFER_DEADLINE, validity_deadline=VALIDITY_DEADLINE
        ),
    ).episode


def terminal_episode():
    deferred = deferred_episode()
    return lifecycle.evaluate_time(deferred, DEFER_DEADLINE, F4_POLICY).episode


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


def execution_validation(
    *,
    status: str = EXECUTION_VALID,
    tradeable: bool = True,
    spread_bps: float = 5.0,
) -> ExecutionValidation:
    return ExecutionValidation(
        status=status,
        book_coverage_status=COMPLETE,
        warnings=[],
        buy_fully_covered=tradeable,
        sell_fully_covered=tradeable,
        buy_visible_coverage_pct=100.0 if tradeable else 50.0,
        sell_visible_coverage_pct=100.0 if tradeable else 50.0,
        spread_bps=spread_bps,
        estimated_visible_short_round_trip_market_drag_pct=0.1 if tradeable else 2.0,
        recent_trade_status=FRESH,
    )


def unavailable_execution() -> ExecutionValidation:
    return ExecutionValidation(
        status=EXECUTION_UNAVAILABLE,
        book_coverage_status=EXECUTION_UNAVAILABLE,
        warnings=["book unavailable"],
    )


def invalid_execution() -> ExecutionValidation:
    return ExecutionValidation(
        status=EXECUTION_INVALID,
        book_coverage_status=EXECUTION_UNAVAILABLE,
        warnings=["crossed book"],
    )


def snapshot(
    *,
    direction: str = "LONG",
    market: MarketDataValidation | None = None,
    execution: ExecutionValidation | None = None,
    margin_status: str = "NOT_REQUIRED",
    margin_eligible: bool = False,
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
    candidate.trade_direction = direction
    candidate.market_data_validation = market
    candidate.execution_validation = execution
    candidate.margin_validation_status = margin_status
    candidate.margin_eligible = margin_eligible
    return candidate


def status_of(decision: FeasibilityDecision, name: FeasibilityCheckName) -> str:
    for check in decision.checks:
        if check.name is name:
            return check.status.value
    return "NOT_EVALUATED"


# ---------------------------------------------------------------------------
# AC-001 .. AC-028
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_vocabulary_is_exact() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-001: the F5 dispositions, component statuses, check names and version tokens are exactly the ratified vocabularies."""
    assert [token.value for token in FeasibilityDisposition] == [
        "FEASIBLE",
        "VETO",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert [token.value for token in FeasibilityCheckStatus] == [
        "PASS",
        "VETO",
        "INSUFFICIENT_EVIDENCE",
        "NOT_APPLICABLE",
    ]
    assert [token.value for token in FeasibilityCheckName] == [
        "MARKET_DATA",
        "MARGIN_ELIGIBILITY",
        "EXECUTION_LIQUIDITY",
    ]
    assert FEASIBILITY_DECISION_SCHEMA_VERSION == "feasibility-decision-v1"
    assert FEASIBILITY_VERSION == "feasibility-seam-v1"
    assert FEASIBILITY_POLICY_VERSION == "feasibility-shadow-policy-v1"
    assert FEASIBILITY_CHECK_ORDER == (
        FeasibilityCheckName.MARKET_DATA,
        FeasibilityCheckName.MARGIN_ELIGIBILITY,
        FeasibilityCheckName.EXECUTION_LIQUIDITY,
    )

    # No forbidden overall token exists anywhere in the disposition vocabulary.
    for token in FeasibilityDisposition:
        assert token.value not in {"PASS", "FAIL", "ERROR", "WATCH", "REJECT", "NO_TRADE"}

    # An unknown component or check token is refused rather than coerced.
    with pytest.raises(FeasibilityContractError):
        FeasibilityCheck(name="BOGUS", status=FeasibilityCheckStatus.PASS)
    with pytest.raises(FeasibilityContractError):
        FeasibilityCheck(
            name=FeasibilityCheckName.MARKET_DATA, status="MAYBE"
        )


@pytest.mark.acceptance
def test_ac_002_active_episode_lineage_preserved() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-002: an ACTIVE F4 episode is accepted and its immutable lineage and explicit evaluation time are preserved."""
    episode = active_episode()
    candidate = snapshot(
        market=market_validation(), execution=execution_validation()
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)

    assert episode.lifecycle_state is OpportunityLifecycleState.ACTIVE
    assert decision.episode_id == episode.episode_id
    assert decision.source_claim_id == episode.source_claim_id
    assert decision.instrument_version_id == episode.instrument_version_id
    assert decision.venue_instrument_id == episode.venue_instrument_id
    assert decision.detector_snapshot_id == episode.snapshot_id
    assert decision.evaluation_time == CUTOFF

    # F5 mints no episode and mutates no lifecycle state, deadline or terminal.
    assert decision.episode_id.startswith("OPEP:")
    assert decision.episode_id != "FEAS:" + decision.decision_id
    assert episode.defer_deadline is None
    assert episode.terminal_reason is None
    assert episode.lifecycle_state is OpportunityLifecycleState.ACTIVE

    # The repository's Kraken base aliases (BASE_ALIASES) are reused, so a
    # legitimate XBT/USD venue episode matches a canonical BTC/USD snapshot.
    alias_episode = active_episode(build_claim(venue_instrument_id="XBTUSD"))
    alias_candidate = snapshot(
        market=market_validation(), execution=execution_validation()
    )
    alias_candidate.symbol = "BTC/USD"
    alias_decision = seam.evaluate_feasibility(
        alias_episode, alias_candidate, CUTOFF, POLICY
    )
    assert alias_decision.disposition is FeasibilityDisposition.FEASIBLE


@pytest.mark.acceptance
def test_ac_003_non_active_episode_rejected() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-003: a DEFERRED or TERMINAL episode fails closed and is never mapped into an F5 disposition."""
    candidate = snapshot(
        market=market_validation(), execution=execution_validation()
    )
    for episode in (deferred_episode(), terminal_episode()):
        assert episode.lifecycle_state is not OpportunityLifecycleState.ACTIVE
        with pytest.raises(FeasibilityContractError):
            seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)

    # A wrong object type is likewise refused.
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(object(), candidate, CUTOFF, POLICY)


@pytest.mark.acceptance
def test_ac_004_decision_identity_is_deterministic() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-004: the FEAS decision identity is deterministic, binds the schema/episode/evidence/time/version inputs, and a forged identity fails closed."""
    episode = active_episode()
    candidate = snapshot(
        market=market_validation(), execution=execution_validation()
    )
    first = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    second = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert first.decision_id == second.decision_id
    assert first.decision_id.startswith("FEAS:")

    expected = feasibility_decision_identity(
        decision_schema_version=FEASIBILITY_DECISION_SCHEMA_VERSION,
        episode_id=episode.episode_id,
        source_claim_id=episode.source_claim_id,
        instrument_version_id=episode.instrument_version_id,
        venue_instrument_id=episode.venue_instrument_id,
        detector_snapshot_id=episode.snapshot_id,
        evidence_fingerprint=first.evidence_fingerprint,
        evaluation_time=CUTOFF,
        disposition=first.disposition,
        checks=first.checks,
        feasibility_version=FEASIBILITY_VERSION,
        policy_version=FEASIBILITY_POLICY_VERSION,
    )
    assert first.decision_id == expected

    # The identity binds the outcome and the copied lineage, so tampering with a
    # durable record (disposition, checks, or lineage) fails closed.
    tampered_disposition = first.to_dict()
    tampered_disposition["disposition"] = "VETO"
    with pytest.raises(FeasibilityContractError):
        FeasibilityDecision.from_dict(tampered_disposition)
    dropped_check = first.to_dict()
    dropped_check["checks"] = list(dropped_check["checks"][:-1])
    with pytest.raises(FeasibilityContractError):
        FeasibilityDecision.from_dict(dropped_check)
    emptied = first.to_dict()
    emptied["checks"] = []
    with pytest.raises(FeasibilityContractError):
        FeasibilityDecision.from_dict(emptied)
    forged_lineage = first.to_dict()
    forged_lineage["source_claim_id"] = "DCLM:forged"
    with pytest.raises(FeasibilityContractError):
        FeasibilityDecision.from_dict(forged_lineage)
    inapplicable = first.to_dict()
    inapplicable["checks"][0]["status"] = "NOT_APPLICABLE"
    with pytest.raises(FeasibilityContractError):
        FeasibilityDecision.from_dict(inapplicable)

    # The identity is identical at any wall-clock value (no clock participates).
    real_time = time.time
    time.time = lambda: 1.0
    try:
        early = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    finally:
        time.time = real_time
    assert early.decision_id == first.decision_id

    # A different episode or a different evidence fingerprint changes the id.
    other_candidate = snapshot(
        market=market_validation(status=MARKET_WARN), execution=execution_validation()
    )
    assert (
        seam.evaluate_feasibility(episode, other_candidate, CUTOFF, POLICY).decision_id
        != first.decision_id
    )

    # A changed validated market measurement changes the fingerprint and the id.
    gap_changed = seam.evaluate_feasibility(
        episode,
        snapshot(
            market=replace(market_validation(), largest_gap_seconds=3600.0),
            execution=execution_validation(),
        ),
        CUTOFF,
        POLICY,
    )
    assert gap_changed.evidence_fingerprint != first.evidence_fingerprint
    assert gap_changed.decision_id != first.decision_id

    # An accepted non-finite ticker_last is preserved in the fingerprint, so it
    # is distinct from an absent ticker.
    reject_nan = snapshot(
        market=replace(
            market_validation(status=MARKET_REJECT, qualified=False),
            ticker_last=float("nan"),
        ),
        execution=execution_validation(),
    )
    reject_none = snapshot(
        market=market_validation(status=MARKET_REJECT, qualified=False),
        execution=execution_validation(),
    )
    assert (
        seam.evaluate_feasibility(
            episode, reject_nan, CUTOFF, POLICY
        ).evidence_fingerprint
        != seam.evaluate_feasibility(
            episode, reject_none, CUTOFF, POLICY
        ).evidence_fingerprint
    )

    # A forged decision identity fails closed.
    with pytest.raises(FeasibilityContractError):
        replace(first, decision_id="FEAS:forged")
    with pytest.raises(FeasibilityContractError):
        replace(first, evidence_fingerprint="FEASEV:forged")

    assert "uuid" not in imported_modules(FEASIBILITY_SOURCE)
    assert called_attributes(FEASIBILITY_SOURCE).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)


@pytest.mark.acceptance
def test_ac_005_evaluation_time_is_explicit_aware_utc() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-005: evaluation_time must be explicit aware UTC; naive or non-datetime input fails closed and no clock is read."""
    episode = active_episode()
    candidate = snapshot(
        market=market_validation(), execution=execution_validation()
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(
            episode, candidate, datetime(2026, 9, 11, 15, 1), POLICY
        )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, candidate, "2026-09-11T15:01:00Z", POLICY)
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, candidate, 1757600000.0, POLICY)

    # A non-UTC offset is normalized to UTC and accepted, not silently local.
    offset = timezone(timedelta(hours=-4))
    decision = seam.evaluate_feasibility(
        episode, candidate, CUTOFF.astimezone(offset), POLICY
    )
    assert decision.evaluation_time == CUTOFF
    assert decision.evaluation_time.utcoffset() == timedelta(0)

    # No clock attribute is called in either F5 module.
    for source in (FEASIBILITY_SOURCE, VOCABULARY_SOURCE):
        assert called_attributes(source).isdisjoint(FORBIDDEN_CALL_ATTRIBUTES)
        assert imported_modules(source).isdisjoint({"time", "datetime.now"})


@pytest.mark.acceptance
def test_ac_006_market_valid_is_pass() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-006: market-data evidence the live scanner accepts is a PASS component."""
    episode = active_episode()
    for status in (MARKET_PASS, MARKET_WARN):
        candidate = snapshot(
            market=market_validation(status=status),
            execution=execution_validation(),
        )
        decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
        assert status_of(decision, FeasibilityCheckName.MARKET_DATA) == "PASS"
        assert decision.disposition is FeasibilityDisposition.FEASIBLE


@pytest.mark.acceptance
def test_ac_007_market_explicit_invalid_is_veto() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-007: market-data evidence the live scanner rejects as invalid is a VETO component."""
    episode = active_episode()
    candidate = snapshot(
        market=market_validation(status=MARKET_REJECT, qualified=False),
        execution=execution_validation(),
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert status_of(decision, FeasibilityCheckName.MARKET_DATA) == "VETO"
    assert decision.disposition is FeasibilityDisposition.VETO
    # A proven veto short-circuits the remaining checks.
    assert len(decision.checks) == 1

    # A real live REJECT can carry a raw non-finite ticker_last (the validator
    # records the rejection but stores the raw value); it must still be a VETO,
    # not a structural error.
    nonfinite_ticker = snapshot(
        market=replace(
            market_validation(status=MARKET_REJECT, qualified=False),
            ticker_last=float("nan"),
        ),
        execution=execution_validation(),
    )
    veto = seam.evaluate_feasibility(episode, nonfinite_ticker, CUTOFF, POLICY)
    assert status_of(veto, FeasibilityCheckName.MARKET_DATA) == "VETO"
    assert veto.disposition is FeasibilityDisposition.VETO


@pytest.mark.acceptance
def test_ac_008_market_missing_or_unavailable_is_insufficient() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-008: absent or explicitly unavailable market-data evidence is INSUFFICIENT_EVIDENCE, never PASS or FEASIBLE."""
    episode = active_episode()

    missing = snapshot(market=None, execution=execution_validation())
    decision = seam.evaluate_feasibility(episode, missing, CUTOFF, POLICY)
    assert status_of(decision, FeasibilityCheckName.MARKET_DATA) == "INSUFFICIENT_EVIDENCE"
    assert decision.disposition is FeasibilityDisposition.INSUFFICIENT_EVIDENCE

    unavailable = snapshot(
        market=replace(market_validation(), status="UNAVAILABLE", qualified=False),
        execution=execution_validation(),
    )
    decision = seam.evaluate_feasibility(episode, unavailable, CUTOFF, POLICY)
    assert status_of(decision, FeasibilityCheckName.MARKET_DATA) == "INSUFFICIENT_EVIDENCE"
    assert decision.disposition is FeasibilityDisposition.INSUFFICIENT_EVIDENCE


@pytest.mark.acceptance
def test_ac_009_long_margin_is_not_applicable() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-009: a LONG candidate reports MARGIN_ELIGIBILITY as NOT_APPLICABLE, never PASS."""
    episode = active_episode()
    candidate = snapshot(
        direction="LONG",
        market=market_validation(),
        execution=execution_validation(),
        margin_status="NOT_REQUIRED",
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert (
        status_of(decision, FeasibilityCheckName.MARGIN_ELIGIBILITY)
        == "NOT_APPLICABLE"
    )
    assert decision.disposition is FeasibilityDisposition.FEASIBLE


@pytest.mark.acceptance
def test_ac_010_short_margin_eligible_is_pass() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-010: an explicitly margin-eligible SHORT candidate reports MARGIN_ELIGIBILITY as PASS."""
    episode = active_episode()
    candidate = snapshot(
        direction="SHORT",
        market=market_validation(),
        execution=execution_validation(),
        margin_status="ELIGIBLE",
        margin_eligible=True,
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert status_of(decision, FeasibilityCheckName.MARGIN_ELIGIBILITY) == "PASS"
    assert decision.disposition is FeasibilityDisposition.FEASIBLE


@pytest.mark.acceptance
def test_ac_011_short_margin_ineligible_is_veto() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-011: an explicitly margin-ineligible SHORT candidate reports MARGIN_ELIGIBILITY as VETO."""
    episode = active_episode()
    candidate = snapshot(
        direction="SHORT",
        market=market_validation(),
        execution=execution_validation(),
        margin_status="INELIGIBLE",
        margin_eligible=False,
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert status_of(decision, FeasibilityCheckName.MARGIN_ELIGIBILITY) == "VETO"
    assert decision.disposition is FeasibilityDisposition.VETO


@pytest.mark.acceptance
def test_ac_012_short_margin_unavailable_is_insufficient() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-012: unavailable SHORT margin discovery is INSUFFICIENT_EVIDENCE, not VETO, and adds no new leverage threshold."""
    episode = active_episode()
    candidate = snapshot(
        direction="SHORT",
        market=market_validation(),
        execution=execution_validation(),
        margin_status="UNAVAILABLE",
        margin_eligible=False,
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert (
        status_of(decision, FeasibilityCheckName.MARGIN_ELIGIBILITY)
        == "INSUFFICIENT_EVIDENCE"
    )
    assert decision.disposition is FeasibilityDisposition.INSUFFICIENT_EVIDENCE

    # The seam introduces no leverage/MIN_SHORT_LEVERAGE policy of its own.
    assert FEASIBILITY_SOURCE.count("LEVERAGE") == 0
    assert "MIN_SHORT_LEVERAGE" not in FEASIBILITY_SOURCE


@pytest.mark.acceptance
def test_ac_013_execution_valid_is_pass() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-013: usable validated execution evidence is a PASS component."""
    episode = active_episode()
    candidate = snapshot(
        market=market_validation(), execution=execution_validation()
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert status_of(decision, FeasibilityCheckName.EXECUTION_LIQUIDITY) == "PASS"
    assert decision.disposition is FeasibilityDisposition.FEASIBLE


@pytest.mark.acceptance
def test_ac_014_execution_explicit_invalid_is_veto() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-014: execution evidence the live structural validator marks INVALID is a VETO component."""
    episode = active_episode()
    candidate = snapshot(
        market=market_validation(), execution=invalid_execution()
    )
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert (
        status_of(decision, FeasibilityCheckName.EXECUTION_LIQUIDITY) == "VETO"
    )
    assert decision.disposition is FeasibilityDisposition.VETO


@pytest.mark.acceptance
def test_ac_015_execution_missing_or_unavailable_is_insufficient() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-015: missing or explicitly unavailable required execution evidence is INSUFFICIENT_EVIDENCE, never PASS or FEASIBLE."""
    episode = active_episode()

    missing = snapshot(market=market_validation(), execution=None)
    decision = seam.evaluate_feasibility(episode, missing, CUTOFF, POLICY)
    assert (
        status_of(decision, FeasibilityCheckName.EXECUTION_LIQUIDITY)
        == "INSUFFICIENT_EVIDENCE"
    )
    assert decision.disposition is FeasibilityDisposition.INSUFFICIENT_EVIDENCE

    unavailable = snapshot(
        market=market_validation(), execution=unavailable_execution()
    )
    decision = seam.evaluate_feasibility(episode, unavailable, CUTOFF, POLICY)
    assert (
        status_of(decision, FeasibilityCheckName.EXECUTION_LIQUIDITY)
        == "INSUFFICIENT_EVIDENCE"
    )
    assert decision.disposition is FeasibilityDisposition.INSUFFICIENT_EVIDENCE


@pytest.mark.acceptance
def test_ac_016_short_execution_quality_reject_is_reproduced() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-016: a SHORT candidate the offline execution-quality evaluator rejects is a VETO reproduced without network refresh."""
    episode = active_episode()
    candidate = snapshot(
        direction="SHORT",
        market=market_validation(),
        execution=execution_validation(status=EXECUTION_VALID, tradeable=True, spread_bps=50.0),
        margin_status="ELIGIBLE",
        margin_eligible=True,
    )
    with ForbiddenIO():
        decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    check = next(
        c for c in decision.checks if c.name is FeasibilityCheckName.EXECUTION_LIQUIDITY
    )
    assert check.status is FeasibilityCheckStatus.VETO
    assert "SHORT execution-quality" in check.reason
    assert decision.disposition is FeasibilityDisposition.VETO
    # The offline route is used: no exchange refresh happens inside the seam.
    assert "refresh_margin_book=False" not in FEASIBILITY_SOURCE
    assert "KrakenClient(" not in FEASIBILITY_SOURCE


@pytest.mark.acceptance
def test_ac_017_aggregation_is_deterministic() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-017: aggregation is deterministic sequential in the recorded order; veto dominates, abstention is next, and NOT_APPLICABLE never blocks."""
    episode = active_episode()

    # All applicable PASS -> FEASIBLE.
    feasible = seam.evaluate_feasibility(
        episode,
        snapshot(market=market_validation(), execution=execution_validation()),
        CUTOFF,
        POLICY,
    )
    assert feasible.disposition is FeasibilityDisposition.FEASIBLE
    assert tuple(c.name for c in feasible.checks) == FEASIBILITY_CHECK_ORDER

    # A later veto still yields VETO even when an earlier check abstained.
    abstain_then_veto = seam.evaluate_feasibility(
        episode,
        snapshot(
            direction="SHORT",
            market=None,
            execution=execution_validation(),
            margin_status="INELIGIBLE",
        ),
        CUTOFF,
        POLICY,
    )
    assert abstain_then_veto.disposition is FeasibilityDisposition.VETO

    # A proven veto is never downgraded to abstention by an absent later check.
    veto_first = seam.evaluate_feasibility(
        episode,
        snapshot(
            market=market_validation(status=MARKET_REJECT, qualified=False),
            execution=None,
        ),
        CUTOFF,
        POLICY,
    )
    assert veto_first.disposition is FeasibilityDisposition.VETO

    # Deterministic replay: identical inputs produce an identical decision.
    assert (
        seam.evaluate_feasibility(
            episode,
            snapshot(market=market_validation(), execution=execution_validation()),
            CUTOFF,
            POLICY,
        ).to_dict()
        == feasible.to_dict()
    )


@pytest.mark.acceptance
def test_ac_018_malformed_required_evidence_fails_structurally() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-018: present-but-malformed required evidence raises FeasibilityContractError and is never coerced into zero, default or pass."""
    episode = active_episode()

    malformed_market = snapshot(
        market=replace(market_validation(), status=123),
        execution=execution_validation(),
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, malformed_market, CUTOFF, POLICY)

    contradictory_market = snapshot(
        market=replace(market_validation(), status=MARKET_REJECT, qualified=True),
        execution=execution_validation(),
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, contradictory_market, CUTOFF, POLICY)

    malformed_margin = snapshot(
        direction="SHORT",
        market=market_validation(),
        execution=execution_validation(),
        margin_status="WEIRD",
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, malformed_margin, CUTOFF, POLICY)

    malformed_execution = snapshot(
        market=market_validation(),
        execution=replace(execution_validation(), spread_bps=float("nan")),
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, malformed_execution, CUTOFF, POLICY)

    bad_bool = snapshot(
        market=market_validation(),
        execution=replace(execution_validation(), buy_fully_covered="yes"),
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, bad_bool, CUTOFF, POLICY)

    # A SHORT whose status and eligibility flag contradict is refused, so an
    # ELIGIBLE status can never override the live margin safeguard.
    contradictory_margin = snapshot(
        direction="SHORT",
        market=market_validation(),
        execution=execution_validation(),
        margin_status="ELIGIBLE",
        margin_eligible=False,
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, contradictory_margin, CUTOFF, POLICY)

    # Evidence for a different venue instrument is refused rather than stamped
    # with the episode's lineage.
    foreign = snapshot(market=market_validation(), execution=execution_validation())
    foreign.symbol = "BTCUSD"
    foreign.kraken_public_symbol = "XBTUSD"
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, foreign, CUTOFF, POLICY)

    # If the symbol matches but another populated identifier conflicts, the
    # snapshot is refused rather than accepted on one matching field.
    conflicting = snapshot(market=market_validation(), execution=execution_validation())
    conflicting.kraken_public_symbol = "BTCUSD"
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, conflicting, CUTOFF, POLICY)

    # A populated but unnormalizable identifier is refused, not discarded.
    unusable_id = snapshot(market=market_validation(), execution=execution_validation())
    unusable_id.kraken_public_symbol = "///"
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, unusable_id, CUTOFF, POLICY)
    nonstring_id = snapshot(market=market_validation(), execution=execution_validation())
    nonstring_id.primary_pair = 123
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, nonstring_id, CUTOFF, POLICY)

    # A REJECT record with a non-finite non-ticker measurement is still refused.
    reject_nonfinite_gap = snapshot(
        market=replace(
            market_validation(status=MARKET_REJECT, qualified=False),
            largest_gap_seconds=float("inf"),
        ),
        execution=execution_validation(),
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, reject_nonfinite_gap, CUTOFF, POLICY)

    # A proven hard veto short-circuits: malformed later evidence does not
    # pre-empt the veto (the fingerprint is lenient by design).
    vetoed_with_malformed_later = snapshot(
        market=market_validation(status=MARKET_REJECT, qualified=False),
        execution=replace(execution_validation(), spread_bps=float("nan")),
    )
    short_circuit = seam.evaluate_feasibility(
        episode, vetoed_with_malformed_later, CUTOFF, POLICY
    )
    assert short_circuit.disposition is FeasibilityDisposition.VETO
    assert len(short_circuit.checks) == 1

    # Malformed market-evidence fields are refused even when status/qualified
    # look valid, so a PASS record with broken evidence cannot become FEASIBLE.
    for field_name, bad_value in (
        ("candle_count", True),
        ("latest_candle_age_seconds", float("nan")),
        ("gap_count", -1),
        ("rejection_reasons", [1, 2]),
    ):
        bad_market = snapshot(
            market=replace(market_validation(), **{field_name: bad_value}),
            execution=execution_validation(),
        )
        with pytest.raises(FeasibilityContractError):
            seam.evaluate_feasibility(episode, bad_market, CUTOFF, POLICY)

    # A duck-typed market object and a wrong execution object are refused.
    class _Duck:
        status = "PASS"
        qualified = True

    duck = snapshot(market=market_validation(), execution=execution_validation())
    duck.market_data_validation = _Duck()
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, duck, CUTOFF, POLICY)

    unknown_coverage = snapshot(
        market=market_validation(),
        execution=replace(execution_validation(), book_coverage_status="BOGUS"),
    )
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, unknown_coverage, CUTOFF, POLICY)

    wrong_execution_type = snapshot(
        market=market_validation(), execution=execution_validation()
    )
    wrong_execution_type.execution_validation = object()
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, wrong_execution_type, CUTOFF, POLICY)

    # Wrong evidence type, unsupported version and naive time all fail closed.
    with pytest.raises(FeasibilityContractError):
        seam.evaluate_feasibility(episode, {"market": "PASS"}, CUTOFF, POLICY)
    with pytest.raises(FeasibilityContractError):
        FeasibilityPolicy(policy_version="feasibility-other-v1")
    with pytest.raises(FeasibilityContractError):
        replace(
            seam.evaluate_feasibility(
                episode,
                snapshot(market=market_validation(), execution=execution_validation()),
                CUTOFF,
                POLICY,
            ),
            policy_version="feasibility-other-v1",
        )


@pytest.mark.acceptance
def test_ac_019_no_new_threshold_ownership() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-019: the F5 modules add no independent numeric trading threshold and only re-map existing evaluator outcomes."""
    numeric = numeric_constants(FEASIBILITY_SOURCE) + numeric_constants(VOCABULARY_SOURCE)
    # Only ordinal/index literals (0 and 1) appear: the seam introduces no
    # numeric trading policy literal and duplicates no threshold.
    assert set(numeric) <= {0, 1}, numeric

    # No service/threshold module is imported; only the recorded evaluators.
    modules = imported_modules(FEASIBILITY_SOURCE)
    assert not any(module.startswith("app.services") for module in modules)
    assert not any("threshold" in module for module in modules)

    # The seam calls only the two recorded adapters, never a threshold owner.
    names = called_names(FEASIBILITY_SOURCE) | {
        node.func.attr
        for node in ast.walk(ast.parse(FEASIBILITY_SOURCE))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"evaluate_margin_gate", "evaluate_execution_gate"} <= names
    for excluded in EXCLUDED_LEGACY_GATES:
        assert excluded not in names


@pytest.mark.acceptance
def test_ac_020_frozen_population_parity() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-020: the frozen reproduction population reproduces live hard vetoes as VETO, unavailable evidence as INSUFFICIENT_EVIDENCE and valid evidence as FEASIBLE with no network refresh."""
    episode = active_episode()

    def long_valid() -> MarketSnapshot:
        return snapshot(market=market_validation(), execution=execution_validation())

    def short_valid() -> MarketSnapshot:
        return snapshot(
            direction="SHORT",
            market=market_validation(),
            execution=execution_validation(),
            margin_status="ELIGIBLE",
            margin_eligible=True,
        )

    parity: dict[str, tuple[MarketSnapshot | None, str]] = {
        "LONG_VALID": (long_valid(), "FEASIBLE"),
        "SHORT_VALID": (short_valid(), "FEASIBLE"),
        "MARKET_INVALID": (
            snapshot(
                market=market_validation(status=MARKET_REJECT, qualified=False),
                execution=execution_validation(),
            ),
            "VETO",
        ),
        "MARKET_UNAVAILABLE": (
            snapshot(
                market=replace(
                    market_validation(), status="UNAVAILABLE", qualified=False
                ),
                execution=execution_validation(),
            ),
            "INSUFFICIENT_EVIDENCE",
        ),
        "MARKET_MISSING": (
            snapshot(market=None, execution=execution_validation()),
            "INSUFFICIENT_EVIDENCE",
        ),
        "SHORT_MARGIN_INELIGIBLE": (
            snapshot(
                direction="SHORT",
                market=market_validation(),
                execution=execution_validation(),
                margin_status="INELIGIBLE",
            ),
            "VETO",
        ),
        "SHORT_MARGIN_UNAVAILABLE": (
            snapshot(
                direction="SHORT",
                market=market_validation(),
                execution=execution_validation(),
                margin_status="UNAVAILABLE",
            ),
            "INSUFFICIENT_EVIDENCE",
        ),
        "EXECUTION_INVALID": (
            snapshot(market=market_validation(), execution=invalid_execution()),
            "VETO",
        ),
        "EXECUTION_MISSING": (
            snapshot(market=market_validation(), execution=None),
            "INSUFFICIENT_EVIDENCE",
        ),
        "SHORT_EXECUTION_REJECTED": (
            snapshot(
                direction="SHORT",
                market=market_validation(),
                execution=execution_validation(spread_bps=50.0),
                margin_status="ELIGIBLE",
                margin_eligible=True,
            ),
            "VETO",
        ),
    }

    with ForbiddenIO():
        for name, (candidate, expected) in parity.items():
            assert candidate is not None
            decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
            assert decision.disposition.value == expected, (name, decision.disposition)

    # Malformed fixtures raise structurally rather than becoming FEASIBLE.
    malformed = {
        "MALFORMED_MARKET": snapshot(
            market=replace(market_validation(), status=123),
            execution=execution_validation(),
        ),
        "MALFORMED_MARGIN": snapshot(
            direction="SHORT",
            market=market_validation(),
            execution=execution_validation(),
            margin_status="WEIRD",
        ),
        "MALFORMED_EXECUTION": snapshot(
            market=market_validation(),
            execution=replace(execution_validation(), spread_bps=float("inf")),
        ),
    }
    for name, candidate in malformed.items():
        with pytest.raises(FeasibilityContractError):
            seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)

    # No known live hard veto is reproduced as FEASIBLE, and the veto fixtures
    # report the vetoing component status.
    assert status_of(
        seam.evaluate_feasibility(episode, parity["MARKET_INVALID"][0], CUTOFF, POLICY),
        FeasibilityCheckName.MARKET_DATA,
    ) == "VETO"
    assert status_of(
        seam.evaluate_feasibility(
            episode, parity["SHORT_EXECUTION_REJECTED"][0], CUTOFF, POLICY
        ),
        FeasibilityCheckName.EXECUTION_LIQUIDITY,
    ) == "VETO"


@pytest.mark.acceptance
def test_ac_021_optional_enrichment_is_not_a_new_veto() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-021: absent optional informational enrichment neither favors a candidate nor becomes a new veto; informational shadow adapters stay informational."""
    episode = active_episode()
    candidate = snapshot(market=market_validation(), execution=execution_validation())
    # No cross-market, reference or market-intelligence enrichment is attached.
    assert getattr(candidate, "cross_pair_confirmation_status", "SINGLE_MARKET") in {
        "SINGLE_MARKET",
        "UNAVAILABLE",
    }
    decision = seam.evaluate_feasibility(episode, candidate, CUTOFF, POLICY)
    assert decision.disposition is FeasibilityDisposition.FEASIBLE

    # The informational-only adapters are not called and cannot veto.
    for adapter in (
        "evaluate_cross_market_gate",
        "evaluate_reference_gate",
        "evaluate_market_intelligence_gate",
    ):
        assert adapter not in FEASIBILITY_SOURCE
    assert "chase" not in FEASIBILITY_SOURCE.lower()


@pytest.mark.acceptance
def test_ac_022_no_ai_or_committee_authority() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-022: the F5 seam makes no AI or Committee call, imports no AI authority, and no AI confidence can become a veto."""
    modules = imported_modules(FEASIBILITY_SOURCE)
    for root in ("openai", "anthropic", "google.generativeai", "deepseek"):
        assert root not in modules
    assert not any("committee" in module for module in modules)
    assert "recommendation" not in FEASIBILITY_SOURCE.lower()
    assert "confidence" not in FEASIBILITY_SOURCE.lower()
    assert "evaluate_recommendation_gate_item" not in FEASIBILITY_SOURCE


@pytest.mark.acceptance
def test_ac_023_no_f6_or_f7_behavior() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-023: the F5 seam produces no F6 forecast behavior and no F7 selector behavior."""
    lowered = FEASIBILITY_SOURCE.lower()
    for token in (
        "probability",
        "expected_return",
        "calibration",
        "uncertainty",
        "validity_horizon",
        "confidence_interval",
    ):
        assert token not in lowered, token
    for token in (
        "net_dollars",
        "ranking",
        "reservation",
        "sizing",
        "concentration",
        "cash_no_trade",
        "top_n",
    ):
        assert token not in lowered, token
    # The vocabulary tokens themselves carry no forecast/selector semantics, and
    # neither F5 module imports a forecast/selector module.
    tokens = (
        {token.value for token in FeasibilityDisposition}
        | {token.value for token in FeasibilityCheckStatus}
        | {token.value for token in FeasibilityCheckName}
    )
    for token in tokens:
        lowered_token = token.lower()
        for forbidden in ("forecast", "probability", "return", "selector", "portfolio"):
            assert forbidden not in lowered_token, token
    for source in (FEASIBILITY_SOURCE, VOCABULARY_SOURCE):
        modules = imported_modules(source)
        assert not any(
            part in module
            for module in modules
            for part in ("forecast", "selector", "portfolio", "optimiz")
        ), modules


@pytest.mark.acceptance
def test_ac_024_no_runtime_consumer() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-024: no runtime path imports or invokes F5; legacy scan and service gates remain the live authority."""
    for path in (RUN_CYCLE_PATH, SCAN_PATH):
        text = path.read_text(encoding="utf-8")
        assert "evaluate_feasibility" not in text, path
        assert "opip.feasibility" not in text, path

    # No application module outside the seam itself imports the seam.
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if path == FEASIBILITY_PATH:
            continue
        text = path.read_text(encoding="utf-8")
        assert "opip.feasibility" not in text, path
        assert "import feasibility" not in text, path

    # Legacy scanner/service gate functions remain present and unchanged.
    assert "keep_margin_tradeable_candidates" in (
        APP_ROOT / "app" / "scanner" / "margin_eligibility.py"
    ).read_text(encoding="utf-8")
    assert "deep_validate_candidates" in (
        APP_ROOT / "app" / "scanner" / "market_scanner.py"
    ).read_text(encoding="utf-8")


@pytest.mark.acceptance
def test_ac_025_feature_bus_off() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-025: the Feature Bus mode remains off and F5 activates no Feature Bus behavior."""
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose
    assert "feature_bus" not in FEASIBILITY_SOURCE.lower()


@pytest.mark.acceptance
def test_ac_026_f3_f4_semantics_unchanged() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-026: F3 detector and F4 lifecycle semantics are unmodified and F5 reinterprets neither."""
    detector_source = (
        APP_ROOT / "app" / "opip" / "contracts" / "detector.py"
    ).read_text(encoding="utf-8")
    assert "episode_id is not None" in detector_source

    lifecycle_source = (
        APP_ROOT / "app" / "opip" / "opportunity_lifecycle.py"
    ).read_text(encoding="utf-8")
    assert "def apply_claim(" in lifecycle_source
    assert "def evaluate_time(" in lifecycle_source
    assert "feasib" not in lifecycle_source.lower()

    # F4 still produces identical, deterministic semantics.
    first = lifecycle.apply_claim(
        build_claim(), None, CUTOFF, F4_POLICY
    ).to_dict()
    second = lifecycle.apply_claim(
        build_claim(), None, CUTOFF, F4_POLICY
    ).to_dict()
    assert first == second


@pytest.mark.acceptance
def test_ac_027_f4_pointer_handoff() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-027: the ATDD pointer names this F5 increment, the F4 test no longer pins the global pointer, and every substantive F4 AC-014 assertion is preserved."""
    active = ACTIVE_INCREMENT_PATH.read_text(encoding="utf-8").strip()
    assert active == INCREMENT

    f4_test = F4_TEST_PATH.read_text(encoding="utf-8")
    # The stale global-pointer ownership assertion is gone.
    assert "ACTIVE_INCREMENT" not in f4_test

    # Every substantive AC-014 assertion is preserved.
    assert FROZEN_F4_INCREMENT in f4_test
    assert "IMPLEMENTATION_CONTRACT_PATH" in f4_test
    assert 'assert "shadow" in lowered' in f4_test
    assert 'assert "non-authoritative" in lowered' in f4_test
    assert "ATDD-R3-F4-opportunity-lifecycle" in f4_test

    # The F4 contract still exists and names the correct increment.
    f4_contract = (
        APP_ROOT
        / "docs"
        / "atdd"
        / "scope-contracts"
        / f"{FROZEN_F4_INCREMENT}.md"
    )
    assert f4_contract.is_file()
    lines = f4_contract.read_text(encoding="utf-8").splitlines()
    assert lines[0].strip() == "INCREMENT:"
    assert lines[1].strip() == FROZEN_F4_INCREMENT

    # This F5 contract records the handoff authorization.
    f5_contract = (
        APP_ROOT
        / "docs"
        / "atdd"
        / "scope-contracts"
        / f"{INCREMENT}.md"
    ).read_text(encoding="utf-8")
    assert "F4 GOVERNANCE HANDOFF" in f5_contract
    assert "test_ac_014_current_vs_target_authority" in f5_contract


@pytest.mark.acceptance
def test_ac_028_no_new_persistence() -> None:
    """ATDD-R3-F5-feasibility-safety/AC-028: F5 creates no persistence module, writer, table, JSONL stream or scheduler and writes no canonical evidence."""
    for token in ("sqlite3", "jsonl", "jsonlines", "CanonicalWriter", "writer", "scheduler"):
        assert token not in FEASIBILITY_SOURCE
        assert token not in VOCABULARY_SOURCE
    assert not (APP_ROOT / "app" / "opip" / "feasibility").exists()
    # Deterministic serialization round-trips without any durable side effect.
    episode = active_episode()
    decision = seam.evaluate_feasibility(
        episode,
        snapshot(market=market_validation(), execution=execution_validation()),
        CUTOFF,
        POLICY,
    )
    assert FeasibilityDecision.from_dict(decision.to_dict()) == decision
