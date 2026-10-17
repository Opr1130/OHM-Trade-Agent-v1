"""ATDD-R5-E research-paper execution: committed admission -> F8 execution.

The increment under test composes two authorities that already exist and must not
be duplicated: the single canonical capital admission
(``WriterClient.admit_paper_opportunity``) and the single paper execution producer
(``app.services.paper_v2_execution.run_paper_v2_opportunity``). The seam adds no
third one: it consumes committed admission evidence, refuses before dispatch when
that evidence is unusable, absent, uncommitted or divergent, and otherwise reports
the producer's own canonical outcome verbatim.

Every test here is adversarial: it asks what happens when the committed admission
is missing, unusable, uncommitted, divergent or already finished, and it asserts
that the seam never invents capital, never writes canonical evidence of its own,
never reads a market or a clock of its own making, and never grants authority.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.exchanges.kraken import KrakenClient  # noqa: E402
from app.exchanges.kraken import KrakenTransportError  # noqa: E402
from app.opip.canonical.client import InProcessWriterClient  # noqa: E402
from app.opip.canonical.models import PaperV2ExecutionState  # noqa: E402
from app.opip.canonical.server import CanonicalWriterServer  # noqa: E402
from app.opip.contracts.paper_execution_events import (  # noqa: E402
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (  # noqa: E402
    PAPER_ADMISSION_REQUEST_RECORDED,
    PAPER_QUOTE_EVIDENCE_RECORDED,
)
from app.opip.decision_intelligence.events import (  # noqa: E402
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
)
from app.services import paper_v2_capital_admission_execution as seam  # noqa: E402
from app.services.paper_v2_capital_admission_execution import (  # noqa: E402
    OUTCOME_BLOCKED,
    OUTCOME_EXECUTED,
    OUTCOME_FAILED,
    REASON_ADMISSION_INTENT_DIVERGENCE,
    REASON_NO_COMMITTED_ADMISSION,
    REASON_PRODUCER_FAILED,
    REASON_UNCOMMITTED_RESERVATION,
    REASON_UNUSABLE_CANONICAL_STATE,
    execute_admitted_research_paper_opportunity,
)
from app.services.paper_v2_execution import (  # noqa: E402
    PaperV2ExecutionError,
    build_disposition_id,
    run_paper_v2_opportunity,
)
from tests.test_opip_paper_v2_execution_bc3 import (  # noqa: E402
    NOW,
    _Settings,
    _kraken,
    _opportunity,
    _rows,
    _run,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-E-research-paper-execution"
SEAM_PATH = pathlib.Path(seam.__file__).resolve()
SEAM_RELATIVE = "app/services/paper_v2_capital_admission_execution.py"


# ---------------------------------------------------------------------------
# Harness: the real canonical writer, plus transports and clients that refuse
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _frozen_producer_clock(monkeypatch):
    """Freeze the producer's execution clock to this module's NOW (BC3 harness)."""
    monkeypatch.setattr(
        "app.services.paper_v2_execution.system_utc_clock", lambda: NOW
    )


@pytest.fixture
def env(tmp_path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        yield server, InProcessWriterClient(server)
    finally:
        server.stop()


class _RefusingTransport:
    """A market-data transport that records reads and never serves a book."""

    def __init__(self, error=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error = error

    def request(self, endpoint, params, timeout_seconds=30.0):
        self.calls.append((str(endpoint), dict(params)))
        if self.error is not None:
            raise self.error
        raise AssertionError("no market data may be read on this path")

    def telemetry_snapshot(self) -> dict:
        return {}


class _StubClient:
    """A writer client whose committed projection the test dictates.

    Admission is refused loudly here: the seam must never be able to admit even
    when a caller hands it a client that could.
    """

    def __init__(self, projection_factory) -> None:
        self._projection_factory = projection_factory
        self.reads: list[str] = []

    def get_paper_v2_execution_state(self, disposition_id):
        self.reads.append(disposition_id)
        return self._projection_factory(disposition_id)

    def admit_paper_opportunity(self, request):  # pragma: no cover - must not run
        raise AssertionError("the seam must never admit capital")

    def admit_planned_reservation(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("the seam must never admit capital")


def _projection(disposition_id: str, **overrides) -> PaperV2ExecutionState:
    fields = {
        "status": "OK",
        "disposition_id": disposition_id,
        "admitted": True,
        "paper_trade_id": "paper-trade-committed-1",
        "reservation_id": "reservation-committed-1",
        "quote_currency": "USD",
        "requested_reservation_amount": _opportunity().requested_reservation_amount,
        "disposition": "ADMITTED",
        "entry_attempt_fill_capable": False,
    }
    fields.update(overrides)
    return PaperV2ExecutionState(**fields)


def _dispatch(env, *, kraken=None, opportunity=None, client=None, now=NOW, settings=None):
    _server, live_client = env
    return execute_admitted_research_paper_opportunity(
        opportunity or _opportunity(),
        client=client if client is not None else live_client,
        kraken_client=kraken or _kraken(),
        settings=settings or _Settings(),
        now=now,
    )


def _seed_committed_admission(env, *, kraken=None, opportunity=None):
    """Seed the *upstream* committed admission through the canonical producer path.

    In production the committed admission is produced by the capital authority
    before this seam is reached; the seam itself deliberately cannot admit, which
    is asserted separately. Seeding through the real producer is what makes the
    committed state canonically authentic rather than hand-built.
    """
    return _run(env, opportunity, kraken=kraken)


def _counts(writer) -> dict[str, int]:
    return {
        "admission_request": len(_rows(writer, PAPER_ADMISSION_REQUEST_RECORDED)),
        "disposition": len(_rows(writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)),
        "order_intent": len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)),
        "quote_evidence": len(_rows(writer, PAPER_QUOTE_EVIDENCE_RECORDED)),
        "execution_attempt": len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)),
        "fill": len(_rows(writer, PAPER_FILL_RECORDED)),
        "protection_plan": len(_rows(writer, PAPER_PROTECTION_PLAN_RECORDED)),
        "reconciliation": len(_rows(writer, PAPER_RECONCILIATION_RECORDED)),
    }


def _portfolio(writer, currency: str = "USD") -> dict:
    state = writer.paper_portfolio_state(currency)
    return {
        "portfolio_version": state.portfolio_version,
        "active_reservations": state.active_reservations,
        "reserved_capital": state.reserved_capital,
    }


# ---------------------------------------------------------------------------
# AC-001: committed admission -> one canonical reservation -> one execution
# ---------------------------------------------------------------------------


def test_ac_001_a_committed_admission_is_dispatched_into_one_execution(env):
    """ATDD-R5-E-research-paper-execution/AC-001: a committed admission is dispatched into one execution."""

    server, _client = env
    seeded = _seed_committed_admission(env)
    assert seeded.status == "EXECUTED"

    outcome = _dispatch(env)

    assert outcome.status == OUTCOME_EXECUTED
    assert outcome.admission_consumed is True
    assert outcome.reason_code == outcome.execution.status == seeded.status
    assert outcome.execution.paper_trade_id == seeded.paper_trade_id
    assert outcome.execution.reservation_id == seeded.reservation_id
    assert outcome.execution.entry_order_intent_id == seeded.entry_order_intent_id
    assert outcome.execution.fill_id == seeded.fill_id

    counts = _counts(server.writer)
    assert counts["admission_request"] == 1
    assert counts["execution_attempt"] == 1
    assert counts["fill"] == 1
    assert _portfolio(server.writer)["active_reservations"] == 1


def test_ac_001_the_seam_adds_no_identity_of_its_own(env):
    """ATDD-R5-E-research-paper-execution/AC-001: the seam adds no identity of its own."""

    server, _client = env
    opportunity = _opportunity()
    seeded = _seed_committed_admission(env, opportunity=opportunity)
    outcome = _dispatch(env, opportunity=opportunity)

    expected = build_disposition_id(
        episode_id=opportunity.episode_id,
        native_symbol=opportunity.native_symbol,
        direction=str(opportunity.direction),
    )
    assert outcome.disposition_id == expected == outcome.admission.disposition_id
    assert outcome.admission.reservation_id == seeded.reservation_id
    assert outcome.admission.paper_trade_id == seeded.paper_trade_id
    assert outcome.admission.quote_currency == opportunity.quote_currency
    assert outcome.admission.requested_reservation_amount == (
        opportunity.requested_reservation_amount
    )
    assert len(_rows(server.writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)) == 1


# ---------------------------------------------------------------------------
# AC-002: the seam owns no capital authority and writes no canonical record
# ---------------------------------------------------------------------------


def test_ac_002_a_dispatch_writes_no_admission_reservation_or_stage(env):
    """ATDD-R5-E-research-paper-execution/AC-002: a dispatch writes no admission reservation or stage."""

    server, _client = env
    _seed_committed_admission(env)
    before_counts = _counts(server.writer)
    before_portfolio = _portfolio(server.writer)

    outcome = _dispatch(env)

    assert outcome.status == OUTCOME_EXECUTED
    assert _counts(server.writer) == before_counts
    assert _portfolio(server.writer) == before_portfolio


def test_ac_002_the_seam_has_no_admission_or_write_surface():
    """ATDD-R5-E-research-paper-execution/AC-002: the seam has no admission or write surface."""

    source = SEAM_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else getattr(node.func, "id", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    for forbidden in (
        "admit_paper_opportunity",
        "admit_planned_reservation",
        "record_paper_order_intent",
        "reserve_paper_capital",
        "release_paper_reservation",
    ):
        assert forbidden not in called, forbidden

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {
        "__future__",
        "dataclasses",
        "datetime",
        "typing",
        "app.services.paper_v2_execution",
    }, sorted(imported)

    # The only canonical surface it touches is the read-only progress RPC, and it
    # travels with the producer rather than around it.
    assert "get_paper_v2_execution_state" in called
    assert "run_paper_v2_opportunity" in called
    assert "grants_execution_authority" in source


# ---------------------------------------------------------------------------
# AC-003: no committed admission -> no execution, and no canonical record
# ---------------------------------------------------------------------------


def test_ac_003_a_store_without_a_committed_admission_blocks_execution(env):
    """ATDD-R5-E-research-paper-execution/AC-003: a store without a committed admission blocks execution."""

    server, _client = env
    transport = _RefusingTransport()

    outcome = _dispatch(env, kraken=KrakenClient(transport=transport))

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_NO_COMMITTED_ADMISSION
    assert outcome.admission_consumed is False
    assert outcome.execution is None
    assert outcome.admission is not None and outcome.admission.admitted is False
    assert transport.calls == []
    assert all(value == 0 for value in _counts(server.writer).values())


def test_ac_003_a_refused_canonical_disposition_blocks_execution(env):
    """ATDD-R5-E-research-paper-execution/AC-003: a refused canonical disposition blocks execution."""

    stub = _StubClient(
        lambda disposition_id: _projection(
            disposition_id,
            admitted=False,
            reservation_id=None,
            paper_trade_id=None,
            disposition="CAPITAL_REJECTED",
        )
    )
    transport = _RefusingTransport()

    outcome = _dispatch(env, client=stub, kraken=KrakenClient(transport=transport))

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_NO_COMMITTED_ADMISSION
    assert outcome.admission.disposition == "CAPITAL_REJECTED"
    assert outcome.admission.reservation_id is None
    assert outcome.admission_consumed is False
    assert transport.calls == []


def test_ac_003_an_unusable_canonical_state_blocks_execution(env):
    """ATDD-R5-E-research-paper-execution/AC-003: an unusable canonical state blocks execution."""

    stub = _StubClient(
        lambda disposition_id: _projection(
            disposition_id,
            status="RETRYABLE",
            error_code="SQLITE_ERROR",
            detail="canonical store busy",
        )
    )
    transport = _RefusingTransport()

    outcome = _dispatch(env, client=stub, kraken=KrakenClient(transport=transport))

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_UNUSABLE_CANONICAL_STATE
    assert outcome.execution is None
    assert "SQLITE_ERROR" in outcome.detail
    assert transport.calls == []
    assert stub.reads == [outcome.disposition_id]
    assert stub.reads[0] == build_disposition_id(
        episode_id=_opportunity().episode_id,
        native_symbol=_opportunity().native_symbol,
        direction="LONG",
    )


def test_ac_003_an_admitted_trade_without_a_committed_reservation_blocks_execution(env):
    """ATDD-R5-E-research-paper-execution/AC-003: an admitted trade without a committed reservation blocks execution."""

    stub = _StubClient(lambda disposition_id: _projection(disposition_id, reservation_id=None))
    transport = _RefusingTransport()

    outcome = _dispatch(env, client=stub, kraken=KrakenClient(transport=transport))

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_UNCOMMITTED_RESERVATION
    assert outcome.admission.admitted is True
    assert outcome.admission.reservation_id is None
    assert outcome.execution is None
    assert transport.calls == []


# ---------------------------------------------------------------------------
# AC-004: a committed admission that does not describe this intent is refused
# ---------------------------------------------------------------------------


def test_ac_004_a_divergent_reservation_amount_is_refused_before_dispatch(env):
    """ATDD-R5-E-research-paper-execution/AC-004: a divergent reservation amount is refused before dispatch."""

    opportunity = _opportunity()
    divergent = float(opportunity.requested_reservation_amount) - 1.0
    stub = _StubClient(
        lambda disposition_id: _projection(
            disposition_id, requested_reservation_amount=divergent
        )
    )
    transport = _RefusingTransport()

    outcome = _dispatch(
        env,
        client=stub,
        kraken=KrakenClient(transport=transport),
        opportunity=opportunity,
    )

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_ADMISSION_INTENT_DIVERGENCE
    assert outcome.execution is None
    assert outcome.admission_consumed is False
    assert repr(divergent) in outcome.detail
    assert repr(float(opportunity.requested_reservation_amount)) in outcome.detail
    assert transport.calls == []


def test_ac_004_a_divergent_quote_currency_is_refused_before_dispatch(env):
    """ATDD-R5-E-research-paper-execution/AC-004: a divergent quote currency is refused before dispatch."""

    stub = _StubClient(lambda disposition_id: _projection(disposition_id, quote_currency="USDT"))
    transport = _RefusingTransport()

    outcome = _dispatch(env, client=stub, kraken=KrakenClient(transport=transport))

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_ADMISSION_INTENT_DIVERGENCE
    assert outcome.admission.quote_currency == "USDT"
    assert outcome.execution is None
    assert transport.calls == []


# ---------------------------------------------------------------------------
# AC-005: the producer's own canonical outcome is reported verbatim
# ---------------------------------------------------------------------------


def test_ac_005_a_committed_fill_is_reported_from_committed_evidence(env):
    """ATDD-R5-E-research-paper-execution/AC-005: a committed fill is reported from committed evidence."""

    server, _client = env
    seeded = _seed_committed_admission(env)
    writer = server.writer
    fill = _rows(writer, PAPER_FILL_RECORDED)[0]
    economic_keys = sorted(
        key
        for key in fill
        if any(token in key for token in ("fee", "cost", "slippage", "spread"))
    )
    assert economic_keys, sorted(fill)

    outcome = _dispatch(env)

    assert outcome.status == OUTCOME_EXECUTED
    assert outcome.reason_code == outcome.execution.status
    assert outcome.execution.fill_id == seeded.fill_id
    assert outcome.execution.filled_quantity == seeded.filled_quantity == 5.0
    # The committed fill is the only place the trade's economics live, and the
    # outcome has no surface of its own for them, so nothing can be invented,
    # adjusted or carried in transit by this seam.
    for absent in ("fee", "fees", "slippage", "spread", "latency", "economics"):
        assert not hasattr(outcome.execution, absent), absent


def test_ac_005_a_committed_zero_fill_terminal_is_reported_verbatim(env):
    """ATDD-R5-E-research-paper-execution/AC-005: a committed zero fill terminal is reported verbatim."""

    server, _client = env
    writer = server.writer
    with pytest.raises(PaperV2ExecutionError):
        _seed_committed_admission(
            env, kraken=_kraken(error=KrakenTransportError("down"))
        )
    reconciliation = _rows(writer, PAPER_RECONCILIATION_RECORDED)
    assert len(reconciliation) == 1
    assert reconciliation[0]["terminal_reconciliation_state"] == "FINAL_VERIFIED"
    assert _rows(writer, PAPER_FILL_RECORDED) == []
    assert _portfolio(writer)["active_reservations"] == 0
    assert _portfolio(writer)["reserved_capital"] == 0.0

    outcome = _dispatch(env)

    assert outcome.status == OUTCOME_EXECUTED
    assert outcome.reason_code == outcome.execution.status == "NO_FILL_TERMINAL"
    assert outcome.execution.fill_id is None
    assert _rows(writer, PAPER_FILL_RECORDED) == []
    assert _counts(writer)["admission_request"] == 1
    assert _portfolio(writer)["active_reservations"] == 0


# ---------------------------------------------------------------------------
# AC-006: a failure fails closed and is never reported as a success
# ---------------------------------------------------------------------------


def test_ac_006_a_producer_failure_fails_closed_and_claims_no_execution(env, monkeypatch):
    """ATDD-R5-E-research-paper-execution/AC-006: a producer failure fails closed and claims no execution."""

    server, _client = env
    _seed_committed_admission(env)
    before_counts = _counts(server.writer)
    before_portfolio = _portfolio(server.writer)

    def _boom(*args, **kwargs):
        raise PaperV2ExecutionError("market data unavailable during dispatch")

    monkeypatch.setattr(seam, "run_paper_v2_opportunity", _boom)

    outcome = _dispatch(env)

    assert outcome.status == OUTCOME_FAILED
    assert outcome.reason_code == REASON_PRODUCER_FAILED
    assert outcome.execution is None
    assert outcome.admission_consumed is True
    assert "market data unavailable during dispatch" in outcome.detail
    assert _counts(server.writer) == before_counts
    assert _portfolio(server.writer) == before_portfolio


def test_ac_006_a_blocked_dispatch_reads_no_market_and_writes_nothing(env):
    """ATDD-R5-E-research-paper-execution/AC-006: a blocked dispatch reads no market and writes nothing."""

    server, _client = env
    transport = _RefusingTransport(error=KrakenTransportError("down"))

    outcome = _dispatch(env, kraken=KrakenClient(transport=transport))

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_NO_COMMITTED_ADMISSION
    assert transport.calls == []
    assert all(value == 0 for value in _counts(server.writer).values())


# ---------------------------------------------------------------------------
# AC-007: replay and restart consume the one committed admission exactly once
# ---------------------------------------------------------------------------


def test_ac_007_a_replay_of_the_committed_admission_consumes_it_once(env):
    """ATDD-R5-E-research-paper-execution/AC-007: a replay of the committed admission consumes it once."""

    server, _client = env
    _seed_committed_admission(env)

    first = _dispatch(env)
    counts_after_first = _counts(server.writer)
    portfolio_after_first = _portfolio(server.writer)
    second = _dispatch(env)

    assert first.status == second.status == OUTCOME_EXECUTED
    assert second.reason_code == second.execution.status
    assert second.execution.paper_trade_id == first.execution.paper_trade_id
    assert second.execution.reservation_id == first.execution.reservation_id
    assert second.execution.fill_id == first.execution.fill_id
    assert _counts(server.writer) == counts_after_first
    assert _portfolio(server.writer) == portfolio_after_first
    assert portfolio_after_first["active_reservations"] == 1


def test_ac_007_a_restart_after_execution_resumes_without_new_evidence(tmp_path):
    """ATDD-R5-E-research-paper-execution/AC-007: a restart after execution resumes without new evidence."""

    db_path = tmp_path / "canonical.sqlite3"
    sock = tmp_path / "canonical.sock"

    first_server = CanonicalWriterServer(db_path=db_path, socket_path=sock)
    try:
        first_env = (first_server, InProcessWriterClient(first_server))
        _seed_committed_admission(first_env)
        first = _dispatch(first_env)
        counts = _counts(first_server.writer)
    finally:
        first_server.stop()

    reopened = CanonicalWriterServer(db_path=db_path, socket_path=sock)
    try:
        second_env = (reopened, InProcessWriterClient(reopened))
        second = _dispatch(second_env)

        assert second.status == OUTCOME_EXECUTED
        assert second.admission_consumed is True
        assert second.reason_code == second.execution.status
        assert second.execution.paper_trade_id == first.execution.paper_trade_id
        assert second.execution.fill_id == first.execution.fill_id
        assert _counts(reopened.writer) == counts
        assert _portfolio(reopened.writer)["active_reservations"] == 1
    finally:
        reopened.stop()


# ---------------------------------------------------------------------------
# AC-008: no policy, clock, secret, environment or economic knob of its own
# ---------------------------------------------------------------------------


def test_ac_008_the_seam_owns_no_policy_clock_secret_or_environment():
    """ATDD-R5-E-research-paper-execution/AC-008: the seam owns no policy clock secret or environment."""

    source = SEAM_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "os.environ",
        "getenv",
        "import time",
        "datetime.now",
        "utcnow",
        "monotonic",
        "settings.",
        "api_key",
        "external_ai",
    ):
        assert forbidden not in source, forbidden
    floats = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == []


def test_ac_008_the_dispatch_accepts_no_economic_knob():
    """ATDD-R5-E-research-paper-execution/AC-008: the dispatch accepts no economic knob."""

    signature = inspect.signature(execute_admitted_research_paper_opportunity)
    assert set(signature.parameters) == {
        "opportunity",
        "client",
        "kraken_client",
        "settings",
        "now",
        "clock",
    }
    for banned in ("fee", "slippage", "latency", "spread", "risk", "size", "ratio"):
        assert not any(banned in name for name in signature.parameters), banned
    assert (
        signature.parameters["opportunity"].kind
        is inspect.Parameter.POSITIONAL_OR_KEYWORD
    )
    for name in ("client", "kraken_client", "settings", "now", "clock"):
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name


# ---------------------------------------------------------------------------
# AC-009: zero authority, dormant, and outside every frozen guard's census
# ---------------------------------------------------------------------------


def test_ac_009_the_seam_grants_no_authority_and_refuses_to():
    """ATDD-R5-E-research-paper-execution/AC-009: the seam grants no authority and refuses to."""

    outcome = seam.ResearchPaperExecutionOutcome(
        status=OUTCOME_BLOCKED,
        reason_code=REASON_NO_COMMITTED_ADMISSION,
        disposition_id="disposition-x",
    )
    assert outcome.grants_execution_authority is False
    with pytest.raises(ValueError):
        seam.ResearchPaperExecutionOutcome(
            status=OUTCOME_EXECUTED,
            reason_code="EXECUTED",
            disposition_id="disposition-x",
            grants_execution_authority=True,
        )


def test_ac_009_no_application_module_imports_the_seam():
    """ATDD-R5-E-research-paper-execution/AC-009: no application module imports the seam."""

    offenders = [
        path.relative_to(APP_ROOT).as_posix()
        for path in sorted((APP_ROOT / "app").rglob("*.py"))
        if "__pycache__" not in path.parts
        and path.resolve() != SEAM_PATH
        and "paper_v2_capital_admission_execution" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_ac_009_the_frozen_guards_do_not_declare_this_seam():
    """ATDD-R5-E-research-paper-execution/AC-009: the frozen guards do not declare this seam."""

    from tests.test_opip_f11_precutover_protection import (
        DECLARED_TARGET_MUTATOR_CALLERS,
        TARGET_MUTATORS,
    )

    assert SEAM_RELATIVE not in DECLARED_TARGET_MUTATOR_CALLERS
    source = SEAM_PATH.read_text(encoding="utf-8")
    called = {
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else getattr(node.func, "id", None)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
    }
    assert not (set(TARGET_MUTATORS) & called)


# ---------------------------------------------------------------------------
# AC-010: the increment's own contract, pointer and posture stay self-consistent
# ---------------------------------------------------------------------------


def test_ac_010_the_increment_contract_and_pointer_are_self_consistent():
    """ATDD-R5-E-research-paper-execution/AC-010: the increment contract and pointer are self-consistent."""

    from tests.atdd_scope import (  # noqa: PLC0415 - scoped to this proof
        check_scope,
        load_acceptance_index,
        load_scope_contracts,
        parse_scope_contract,
        read_increment_pointer,
    )

    contract_dir = APP_ROOT / "docs" / "atdd" / "scope-contracts"
    contract_path = contract_dir / f"{INCREMENT}.md"
    assert contract_path.is_file(), contract_path

    contract = parse_scope_contract(contract_path.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert set(contract.traceability) == set(contract.acceptance_criteria)
    assert set(contract.implementation_map) == set(contract.acceptance_criteria)

    # The pointer is movable by contract, so it is not pinned; it must resolve to
    # exactly one existing contract, and this increment must be among them.
    contracts = load_scope_contracts(contract_dir)
    pointer = read_increment_pointer()
    assert len([c for c in contracts if c.increment == pointer]) == 1, pointer
    assert any(c.increment == INCREMENT for c in contracts)

    index = load_acceptance_index(APP_ROOT / "tests")
    assert check_scope(contract, index).passed, check_scope(contract, index).reasons
    for ac_id, nodes in contract.traceability.items():
        known = index.by_ac.get((INCREMENT, ac_id), frozenset())
        assert set(nodes) == set(known), ac_id
    assert not [
        node
        for node in index.unscoped_tests
        if node.startswith("tests/test_opip_r5_e_research_paper_execution.py")
    ]
    for paths in contract.implementation_map.values():
        for path in paths:
            assert (APP_ROOT.parent / path).is_file(), path


def test_ac_010_the_release_profile_and_posture_are_unchanged():
    """ATDD-R5-E-research-paper-execution/AC-010: the release profile and posture are unchanged."""

    from app.services.release_profiles import RELEASE_PROFILES  # noqa: PLC0415

    assert {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"} <= set(RELEASE_PROFILES)
    shadow = RELEASE_PROFILES["EVIDENCE_SHADOW"]
    assert shadow["status"] == "ACTIVE"
    assert shadow["allowed_modes"] == {
        "OPIP_FEATURE_BUS_MODE": "shadow",
        "OPIP_CANONICAL_WRITER_MODE": "shadow",
        "OPIP_TARGET_SPINE_MODE": "shadow",
        "OPIP_PAPER_V2_MODE": "off",
        "OPIP_COMMITTEE_MODE": "off",
        "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "1000.0",
    }
    assert RELEASE_PROFILES["TARGET_PAPER"]["status"] == "BLOCKED"
    for name, profile in RELEASE_PROFILES.items():
        for key in profile["allowed_modes"]:
            assert not key.startswith("OPIP_R5"), (name, key)

    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_RELEASE_PROFILE: "EVIDENCE_SHADOW"' in compose
    assert 'OPIP_PAPER_V2_MODE: "off"' in compose


def test_ac_010_no_release_or_runtime_surface_names_the_seam():
    """ATDD-R5-E-research-paper-execution/AC-010: no release or runtime surface names the seam."""

    candidates = [
        APP_ROOT / "app",
        APP_ROOT / "deploy",
        APP_ROOT.parent / ".github",
    ]
    suffixes = {".py", ".yml", ".yaml", ".sh", ".json", ".toml", ".cfg", ".env"}
    files = [
        path
        for root in candidates
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in suffixes
    ]
    files.extend(sorted(APP_ROOT.glob("docker-compose*.yml")))

    offenders = [
        path.relative_to(APP_ROOT.parent).as_posix()
        for path in files
        if "paper_v2_capital_admission_execution"
        in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# AC-011: committed-context continuity and zero duplicate economic effect
# ---------------------------------------------------------------------------


def _decision_contexts(writer):
    return _rows(writer, DECISION_INTELLIGENCE_CONTEXT_RECORDED)


def test_ac_011_an_admitted_dispatch_reuses_the_committed_decision_context(env):
    """ATDD-R5-E-research-paper-execution/AC-011: a dispatch reuses the committed decision context."""

    server, client = env
    seeded = _seed_committed_admission(env)
    assert seeded.status == "EXECUTED"

    committed = client.get_paper_v2_execution_state(seeded.disposition_id)
    assert committed.decision_context_id
    contexts_before = _decision_contexts(server.writer)
    assert len(contexts_before) == 1

    outcome = _dispatch(env)

    assert outcome.status == OUTCOME_EXECUTED
    assert _decision_contexts(server.writer) == contexts_before
    after = client.get_paper_v2_execution_state(seeded.disposition_id)
    assert after.decision_context_id == committed.decision_context_id


def test_ac_011_a_divergent_committed_context_is_refused_without_writing(env):
    """ATDD-R5-E-research-paper-execution/AC-011: a divergent committed context is refused before writing."""

    server, client = env
    seeded = _seed_committed_admission(env)
    assert seeded.status == "EXECUTED"

    counts_before = _counts(server.writer)
    contexts_before = _decision_contexts(server.writer)

    with pytest.raises(PaperV2ExecutionError) as raised:
        run_paper_v2_opportunity(
            _opportunity(),
            client=client,
            kraken_client=_kraken(),
            settings=_Settings(),
            now=NOW,
            committed_decision_context_id="decision-context-not-committed",
        )

    assert "does not match canonical state" in str(raised.value)
    assert _counts(server.writer) == counts_before
    assert _decision_contexts(server.writer) == contexts_before


def test_ac_011_a_dispatch_commits_no_second_admission_or_reservation(env):
    """ATDD-R5-E-research-paper-execution/AC-011: a dispatch commits no second admission or reservation."""

    server, _client = env
    seeded = _seed_committed_admission(env)
    counts_before = _counts(server.writer)
    portfolio_before = _portfolio(server.writer)

    outcome = _dispatch(env)

    assert outcome.status == OUTCOME_EXECUTED
    assert _counts(server.writer) == counts_before
    assert _portfolio(server.writer) == portfolio_before
    assert outcome.admission.reservation_id == seeded.reservation_id
    assert outcome.execution.reservation_id == seeded.reservation_id
    assert len(_rows(server.writer, PAPER_ADMISSION_REQUEST_RECORDED)) == 1
    assert len(_rows(server.writer, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)) == 1

