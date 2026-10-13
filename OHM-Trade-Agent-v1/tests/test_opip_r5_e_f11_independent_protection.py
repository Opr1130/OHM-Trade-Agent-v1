"""ATDD-R5-E independent protection over the research-paper portfolio.

Increment: `ATDD-R5-E-F11-independent-protection-validation`. Every test cites its
own criterion in its docstring, and the contract
`docs/atdd/scope-contracts/ATDD-R5-E-F11-independent-protection-validation.md` is
the authority for what each one means.

The research-paper portfolio now has a real lifecycle
(`ATDD-R5-E-portfolio-lifecycle-closure`). This increment proves that the protective
authority which already exists - the F11 scheduled protection sweep, the canonical
writer's atomic protection action and the live-domain F11 health decision - still
holds over that portfolio: it is operational, independent of the
discovery/intelligence/notification surfaces, deterministic and prioritized,
fail-closed on unusable market data and on an unproven writer verdict, and
recoverable across a restart without inventing evidence or releasing capacity it
did not prove.

Nothing new is built here: no engine, no ledger, no scheduler, no writer, no event
type, no release profile and no trading authority. `AC-008` is this increment's own
bookkeeping guard.

Everything except the venue's public Level-1 bytes is real: the production producer
creates the exposure, the R5-C/R5-D authority commits the admission the portfolio
rests on, the production protection runtime advances it, and the canonical writer
commits every transition.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.exchanges.kraken import KrakenClient, KrakenTransportError  # noqa: E402
from app.jobs import scan_opportunities  # noqa: E402
from app.opip.contracts.paper_execution import (  # noqa: E402
    ProtectionState,
    QualifiedOpportunityDisposition,
)
from app.opip.contracts.paper_execution_events import (  # noqa: E402
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_TRIGGER_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (  # noqa: E402
    PaperProtectionActionAck,
)
from app.opip.contracts.serialization import iso_z  # noqa: E402
from app.services import paper_v2_protection_runtime as protection_runtime  # noqa: E402
from app.services import paper_v2_scan_router  # noqa: E402
from app.services import protection_health as ph  # noqa: E402
from app.services.paper_v2_capital_admission_execution import (  # noqa: E402
    OUTCOME_EXECUTED,
)
from app.services.paper_v2_execution import run_paper_v2_opportunity  # noqa: E402
from tests.test_opip_paper_v2_protection_runtime_bc3 import (  # noqa: E402
    QUALIFICATION_TIME,
    _AckLossClient,
    _BookTransport,
    _Clock,
    _Env,
    _Settings,
    _count,
    _expiry,
    _item,
    _next_scan,
    _open_entry,
    _opportunity as _prt_opportunity,
    _rows,
    _state_of,
    _sweep,
    _transport,
)
from tests.test_opip_r5_e_capital_admission_integration import (  # noqa: E402
    _book as _capital_book,
    _store_fingerprint,
)
from tests.test_opip_r5_e_portfolio_lifecycle_closure import (  # noqa: E402
    _commit_planned_admission,
    _dispatch_once,
)

# Imported at module scope, deliberately, BEFORE any double is installed. The F11
# report binds `evaluate_protection_health` and the non-mutating live-registry
# loader by name at import time, so a double installed before that first import
# would be captured by the report for the rest of the session (monkeypatch restores
# the module attribute, not another module's copy of it). Importing the consumers
# here binds the real callables first, so every double below is an ordinary module
# attribute that monkeypatch restores cleanly.
import app.jobs.preflight_protection_health  # noqa: E402,F401
import app.jobs.report_protection_health  # noqa: E402,F401
import app.services.active_trade_registry  # noqa: E402,F401

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-E-F11-independent-protection-validation"
PROTECTION_RUNTIME_PATH = pathlib.Path(protection_runtime.__file__).resolve()
SCAN_JOB_PATH = APP_ROOT / "app" / "jobs" / "scan_opportunities.py"

#: Surfaces that live entirely outside the protective authority. Each one is the
#: module's real entry point, so making it fail is the honest model of "this whole
#: subsystem is down" rather than a test-only shim.
BROKEN_SURFACES: tuple[tuple[str, str], ...] = (
    ("app.jobs.scan_movers.main", "market discovery"),
    ("app.scanner.candidates.select_candidates", "candidate selection"),
    ("app.opip.detectors.ignition.evaluate", "the ignition detector"),
    ("app.opip.forecast.evaluate_forecast", "the forecast engine"),
    ("app.opip.feasibility.evaluate_feasibility", "the feasibility gate"),
    ("app.opip.portfolio_selector.select_portfolio", "the portfolio selector"),
    ("app.api.dashboard.analytics_summary", "the dashboard"),
    ("app.services.telegram_notifier.send_telegram_message_with_id", "notification"),
)

#: Live (Kraken/operator) domain observers and the F11 read-only report. None of
#: them may be a dependency of the target protective authority, and none of them
#: may be able to stop it.
LIVE_DOMAIN_OBSERVERS: tuple[tuple[str, str], ...] = (
    ("app.services.active_trade_registry.read_active_trades_without_mutation", "the live registry"),
    ("app.services.protection_health.evaluate_protection_health", "the F11 evaluator"),
    ("app.jobs.report_protection_health.build_report", "the F11 report"),
    ("app.jobs.report_protection_health.read_incident_projection", "the incident store"),
)

#: Application modules the protective authority is forbidden to reach: discovery,
#: instrument selection, intelligence, notification, dashboard, or the live
#: exposure domain.
FORBIDDEN_APP_IMPORT_FRAGMENTS: tuple[str, ...] = (
    "scan_movers",
    "scan_opportunities",
    "candidates",
    "ignition",
    "forecast",
    "feasibility",
    "portfolio_selector",
    "dashboard",
    "telegram",
    "active_trade_registry",
    "kraken_exposure_resolver",
    "kraken_reconciliation",
    "protection_health",
    "report_protection_health",
    "trade_cli",
    "app.api",
)


def _native_symbol(production_symbol: str) -> str:
    return production_symbol.removesuffix("USD") + "/USD"


@pytest.fixture()
def env(tmp_path):
    """One real canonical store, over real SQLite, with a real restart seam."""

    holder = _Env(tmp_path=tmp_path)
    try:
        yield holder
    finally:
        holder.server.stop()


@pytest.fixture(autouse=True)
def _reset_scan_seams():
    """The scan seams are global; never let one test's client leak into another."""

    yield
    paper_v2_scan_router.set_writer_client_for_tests(None)
    paper_v2_scan_router.set_kraken_client_for_tests(None)


class _PerSymbolTransport:
    """STUB: the public Kraken Level-1 transport, one book per instrument.

    Only the venue bytes are faked. Every reading still goes through the runtime's
    real quote-evidence commit and its real freshness, future-dating, crossed-book
    and lineage rules, so a book this double serves is refused by the runtime's own
    rules rather than by the double.
    """

    def __init__(
        self,
        *,
        clock,
        books: dict[str, tuple[float, float]],
        bid_qty: float = 10.0,
        ask_qty: float = 12.0,
    ) -> None:
        self.requests: list[str] = []
        self._clock = clock
        self._books = {str(symbol): book for symbol, book in books.items()}
        self._bid_qty = float(bid_qty)
        self._ask_qty = float(ask_qty)

    def _published(self) -> str:
        return iso_z(
            self._clock() - timedelta(seconds=1), field_name="publication_ts"
        )

    def request(self, endpoint, params, timeout_seconds=30.0):
        symbol = str(params.get("symbol"))
        self.requests.append(symbol)
        bid, ask = self._books[symbol]
        ts = self._published()
        return {
            "symbol": symbol,
            "bids": [{"price": bid, "qty": self._bid_qty, "publication_ts": ts}],
            "asks": [{"price": ask, "qty": self._ask_qty, "publication_ts": ts}],
        }

    def telemetry_snapshot(self) -> dict:
        return {}


class _StaleBookTransport(_BookTransport):
    """A real, well-formed book that is simply too old to be trusted."""

    def __init__(self, *, clock, age_seconds: int, **kwargs: Any) -> None:
        super().__init__(clock=clock, **kwargs)
        self._age_seconds = int(age_seconds)

    def _published(self) -> str:
        return iso_z(
            self._clock() - timedelta(seconds=self._age_seconds),
            field_name="publication_ts",
        )


class _DownTransport:
    """A venue that cannot be reached at all."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.requests: list[dict] = []

    def request(self, endpoint, params, timeout_seconds=30.0):
        self.requests.append(dict(params))
        raise self.error

    def telemetry_snapshot(self) -> dict:
        return {}


class _IndeterminateActionClient:
    """A writer whose atomic-action answer never arrives as a commit.

    The atomic action RPC is the one call that commits a trigger, its EXIT order
    intent and the lifecycle state together. Here it answers without committing -
    the exact shape of a client whose response was lost, timed out or could not be
    proven - so the runtime must treat the verdict as indeterminate. The rest of
    the runtime stays real.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def trigger_paper_protection_action(self, request):
        self.calls += 1
        return PaperProtectionActionAck(
            status="UNAVAILABLE",
            error_code="WRITER_ANSWER_UNPROVEN",
            detail="the answer to the atomic protection action was lost",
        )

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - delegation only
        return getattr(self._inner, name)


def _arm_trades(env: _Env, clock: _Clock, symbols: tuple[str, ...]) -> list[dict]:
    """Open and arm one real position per symbol, in one canonical store."""

    trades = [_open_entry(env, clock=clock, symbol=symbol) for symbol in symbols]
    armed = _sweep(env, clock=clock)
    assert armed.activated == len(symbols), armed.details
    for trade in trades:
        assert _state_of(env, trade["paper_trade_id"]) == ProtectionState.ACTIVE.value
    return trades


def _produce_one_more_entry(env: _Env, clock: _Clock, symbol: str):
    """Run the real F8 producer for one further opportunity, unmodified.

    This is the same call the entry path itself makes. It is used to prove that the
    producer's own capacity bound refuses a further concurrent position *before* it
    can become unprotected exposure - never to fabricate an exposure.
    """

    book = _BookTransport(clock=clock, bid=99.9, ask=100.0, bid_qty=10.0, ask_qty=10.0)
    return run_paper_v2_opportunity(
        _prt_opportunity(symbol),
        client=env.client,
        kraken_client=KrakenClient(transport=book),
        settings=_Settings(),
        now=QUALIFICATION_TIME,
        clock=clock,
    )


def _run_scheduled_sweep(monkeypatch, env: _Env, clock: _Clock, *, transport: Any):
    """Drive the PRODUCTION scheduled protection seam, not a test-only helper.

    ``scan_opportunities._run_paper_v2_protection_sweep`` is the single scheduled
    caller of the protection runtime: it runs on the existing scan, on the
    existing scheduler. Only the canonical writer client and the public Kraken
    client are supplied through the router's own test seams, and only the runtime's
    default clock is pinned so the run is deterministic.
    """

    monkeypatch.setattr(protection_runtime, "system_utc_clock", clock)
    paper_v2_scan_router.set_writer_client_for_tests(env.client)
    paper_v2_scan_router.set_kraken_client_for_tests(KrakenClient(transport=transport))
    return scan_opportunities._run_paper_v2_protection_sweep(
        _Settings(), requested=True
    )


def _exit_orders(env: _Env, trade_id: str) -> list[dict]:
    return [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]


def _sell_fills(env: _Env, trade_id: str) -> list[dict]:
    return [
        row
        for row in _rows(env, PAPER_FILL_RECORDED, trade_id)
        if row["side"] == "SELL"
    ]


def _book_state(env: _Env) -> tuple[int, float, dict[str, str]]:
    """The canonical capital book: active reservations, reserved capital, states."""

    status, _version, reserved, active, reservations = _capital_book(env.server.writer)
    assert status == "OK", status
    return active, float(reserved), {str(row[1]): str(row[3]) for row in reservations}


def _strict_exposure(status: str, symbol: str = "SOLUSD"):
    """One live-domain exposure, shaped exactly as the F11 evaluator reads it."""

    if status == "VERIFIED_MANAGED":
        trade = SimpleNamespace(entry_price=100.0, stop_price=90.0, direction="LONG")
    else:
        trade = None
    return SimpleNamespace(
        status=status, symbol=symbol, trade=trade, observed_quantity=1.0
    )


def _app_imports(path: pathlib.Path) -> set[str]:
    """Every application module named by one source file's import statements."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return {module for module in modules if module.startswith("app.")}


def _called_names(path: pathlib.Path) -> set[str]:
    """Every simple function name this source file actually calls."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


# ---------------------------------------------------------------------------
# AC-001: the protective exit does not depend on discovery or notification
# ---------------------------------------------------------------------------


def test_ac_001_a_broken_discovery_and_notification_surface_cannot_stop_the_exit(
    env, monkeypatch
):
    """ATDD-R5-E-F11-independent-protection-validation/AC-001: with every other surface down, the scheduled sweep still takes the approved stop exit."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    called: list[str] = []

    def _break(what: str):
        def _fail(*args: Any, **kwargs: Any) -> Any:
            called.append(what)
            raise RuntimeError(f"{what} is unavailable")

        return _fail

    for target, what in BROKEN_SURFACES:
        monkeypatch.setattr(target, _break(what))

    book = _transport(clock, bid=90.0, ask=90.5)
    result = _run_scheduled_sweep(monkeypatch, env, clock, transport=book)

    # The production scheduled seam really read the venue and really triggered.
    assert book.requests
    assert result.triggered == 1, result.details
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    # STOP claims the whole remaining exposure even though every other subsystem
    # was exploding at the same instant.
    assert [
        float(row["requested_quantity"]) for row in _exit_orders(env, trade_id)
    ] == [5.0]
    # None of those surfaces was consulted: protection does not reach them at all.
    assert called == []


def test_ac_001_the_protective_authority_imports_no_other_subsystem():
    """ATDD-R5-E-F11-independent-protection-validation/AC-001: the authority's own imports reach none of those subsystems."""

    reachable = _app_imports(PROTECTION_RUNTIME_PATH)
    # Non-vacuous: the module does import application code, so the census is real.
    assert reachable, "the import census must be able to see the module's imports"
    offenders = sorted(
        module
        for module in reachable
        if any(fragment in module for fragment in FORBIDDEN_APP_IMPORT_FRAGMENTS)
    )
    assert offenders == [], offenders
    # The one scheduled caller is the scan, which owns the admission workload; the
    # authority itself never reaches it, and it reaches no event, board or message
    # store other than the canonical writer's own client.
    assert "app.jobs.scan_opportunities" not in reachable


# ---------------------------------------------------------------------------
# AC-002: deterministic precedence, and suspension outranking new exposure
# ---------------------------------------------------------------------------


def test_ac_002_a_crossed_stop_outranks_the_target_and_the_expired_plan(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-002: STOP has fixed precedence when the plan has also expired and the target is also crossed."""

    assert protection_runtime.TRIGGER_PRECEDENCE == ("STOP", "TARGET", "TIME")
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    expiry = _expiry(env, trade_id)
    # Every trigger is due at this instant: the plan has expired, the bid is below
    # the stop, and the same book is far below the first target.
    clock.value = expiry + timedelta(seconds=120)
    assert clock.value > expiry

    result = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert result.triggered == 1, result.details
    triggers = _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
    assert [row["trigger_type"] for row in triggers] == ["STOP"]
    # The trigger cites the exact executable price it was proven against.
    assert float(triggers[0]["reference_price"]) == 90.0
    # The protective exit claims the FULL remaining exposure: never the 0.5 profit
    # fraction, and never a partial close of the position.
    assert [
        float(row["requested_quantity"]) for row in _exit_orders(env, trade_id)
    ] == [5.0]


def test_ac_002_an_expired_plan_takes_its_approved_target_before_time(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-002: an expired plan takes the approved profit fraction where the target is crossed, and only TIME closes what crossed nothing."""

    clock = _Clock(QUALIFICATION_TIME)
    first, second = _arm_trades(env, clock, ("BTCUSD", "ETHUSD"))
    first_id = first["paper_trade_id"]
    second_id = second["paper_trade_id"]
    expiry = _expiry(env, first_id)
    assert _expiry(env, second_id) == expiry
    clock.value = expiry + timedelta(seconds=120)

    book = _PerSymbolTransport(
        clock=clock,
        books={
            _native_symbol("BTCUSD"): (110.0, 110.5),
            _native_symbol("ETHUSD"): (100.0, 100.5),
        },
    )
    result = _sweep(env, clock=clock, transport=book)

    assert result.considered == 2
    assert result.triggered == 2, result.details
    # The expired plan whose first target is crossed takes the approved 0.5 of 5.0:
    # profit is taken before the clock is allowed to close the whole position.
    target = _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, first_id)
    assert [row["trigger_type"] for row in target] == ["TARGET"]
    assert float(target[0]["reference_price"]) == 110.0
    assert [
        float(row["requested_quantity"]) for row in _exit_orders(env, first_id)
    ] == [2.5]
    # Non-vacuity: the same instant does close a position whose book crossed
    # nothing, so TIME really was due and was genuinely outranked above.
    time_exit = _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, second_id)
    assert [row["trigger_type"] for row in time_exit] == ["TIME"]
    assert [
        float(row["requested_quantity"]) for row in _exit_orders(env, second_id)
    ] == [5.0]


def test_ac_002_an_unreadable_item_withholds_new_admissions_and_protects_others(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-002: one item that cannot be advanced suspends new authority and still does not stop another item's approved exit."""

    clock = _Clock(QUALIFICATION_TIME)
    btc, eth = _arm_trades(env, clock, ("BTCUSD", "ETHUSD"))
    btc_id = btc["paper_trade_id"]
    eth_id = eth["paper_trade_id"]
    _next_scan(clock)

    book = _PerSymbolTransport(
        clock=clock,
        # A contradictory book: the bid is above the ask, so neither side of it can
        # be trusted as a price. The other instrument is simply crossed against its
        # committed stop.
        books={
            _native_symbol("BTCUSD"): (90.0, 90.5),
            _native_symbol("ETHUSD"): (101.0, 99.0),
        },
    )
    result = _sweep(env, clock=clock, transport=book)

    assert result.considered == 2
    # One item could not be advanced, and that alone withholds new authority.
    assert result.retryable + result.unavailable == 1
    assert result.new_admissions_allowed is False
    # The failure does not stop the other item's approved exit.
    assert result.triggered == 1, result.details
    assert _state_of(env, btc_id) == ProtectionState.TRIGGERED.value
    assert _state_of(env, eth_id) == ProtectionState.ACTIVE.value
    assert [
        float(row["requested_quantity"]) for row in _exit_orders(env, btc_id)
    ] == [5.0]
    assert _exit_orders(env, eth_id) == []
    # Withholding new authority is not releasing capital: both reservations stay
    # committed and ACTIVE, and nothing was reconciled or filled.
    active, reserved, states = _book_state(env)
    assert (active, reserved) == (2, 1000.0)
    assert sorted(states.values()) == ["ACTIVE", "ACTIVE"]
    assert _rows(env, PAPER_RECONCILIATION_RECORDED) == []
    assert _sell_fills(env, btc_id) == []


# ---------------------------------------------------------------------------
# AC-003: unusable market data is never a trigger and never a licence
# ---------------------------------------------------------------------------


def test_ac_003_a_stale_book_neither_triggers_nor_grants_new_admissions(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-003: a book older than the frozen freshness window is refused outright."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    stale = _StaleBookTransport(clock=clock, age_seconds=3600, bid=90.0, ask=90.5)
    result = _sweep(env, clock=clock, transport=stale)

    assert stale.requests  # the reading was really attempted
    assert result.triggered == 0
    assert result.exit_attempted == 0
    assert result.retryable + result.unavailable == 1
    assert result.new_admissions_allowed is False
    # Nothing was committed beyond the entry the producer already committed, and
    # the position is still armed and still fully exposed.
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 0
    assert len(_rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)) == 1
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value
    assert float(_item(env, trade_id).remaining_quantity) == 5.0
    active, reserved, states = _book_state(env)
    assert (active, reserved) == (1, 500.0)
    assert sorted(states.values()) == ["ACTIVE"]


def test_ac_003_an_unavailable_venue_neither_triggers_nor_grants_new_admissions(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-003: an unreachable venue leaves the position armed, protected and still reserved."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    down = _DownTransport(KrakenTransportError("the public venue is unavailable"))
    result = _sweep(env, clock=clock, transport=down)

    assert down.requests
    assert result.triggered == 0
    assert result.exit_attempted == 0
    assert result.retryable + result.unavailable == 1
    assert result.new_admissions_allowed is False
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 0
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value
    # The venue failing is not a licence to release the reservation either.
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (1, 500.0)


def test_ac_003_a_contradictory_book_neither_triggers_nor_grants_new_admissions(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-003: a book whose bid is above its ask proves nothing, and does not abandon the position."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    result = _sweep(env, clock=clock, transport=_transport(clock, bid=101.0, ask=99.0))

    assert result.triggered == 0
    assert result.exit_attempted == 0
    assert result.retryable + result.unavailable == 1
    assert result.new_admissions_allowed is False
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 0
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value

    # A later, trustworthy book is still read: the refusal is about that piece of
    # evidence, not about giving up on the position.
    _next_scan(clock)
    recovered = _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    )
    assert recovered.triggered == 1, recovered.details
    assert [
        row["trigger_type"]
        for row in _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
    ] == ["STOP"]


def test_ac_003_a_fresh_crossing_book_still_triggers(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-003: non-vacuity control - a trustworthy crossing book does trigger, and does not withhold new authority."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    result = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert result.triggered == 1, result.details
    assert result.retryable == 0
    assert result.unavailable == 0
    # Protection was proven for this item, so admission authority is NOT withheld.
    assert result.new_admissions_allowed is True
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    assert len(_exit_orders(env, trade_id)) == 1


# ---------------------------------------------------------------------------
# AC-004: a lost or indeterminate writer acknowledgement is never completion
# ---------------------------------------------------------------------------


def test_ac_004_a_lost_action_acknowledgement_is_never_a_completed_exit(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-004: the durable action committed, the answer was lost, and the sweep claims no exit and withholds new authority."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    lost = _sweep(
        env,
        clock=clock,
        transport=_transport(clock, bid=90.0, ask=90.5),
        client=_AckLossClient(env.client, action=True),
    )

    # The sweep reports no completed trigger and no completed exit.
    assert lost.triggered == 0
    assert lost.exit_attempted == 0
    assert lost.retryable == 1
    assert lost.new_admissions_allowed is False
    # The action did commit durably, so the trigger is discoverable evidence - and
    # exactly one of it, never a duplicate invented by the retry.
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 1
    assert len(_exit_orders(env, trade_id)) == 1
    # No fill was fabricated and no capacity was released on an unproven verdict.
    assert _sell_fills(env, trade_id) == []
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (1, 500.0)


def test_ac_004_the_action_behind_a_lost_acknowledgement_is_resumed_once(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-004: the sweep after the lost answer completes that same exit exactly once and releases the reservation exactly once."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)
    lost = _sweep(
        env,
        clock=clock,
        transport=_transport(clock, bid=90.0, ask=90.5),
        client=_AckLossClient(env.client, action=True),
    )
    assert lost.retryable == 1

    _next_scan(clock)
    resumed = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert resumed.exit_attempted == 1, resumed.details
    # Exactly one trigger, one EXIT order and one SELL fill: the committed action
    # was discovered and completed, never replaced or repeated.
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 1
    assert len(_exit_orders(env, trade_id)) == 1
    assert len(_sell_fills(env, trade_id)) == 1

    # Flat, then verified: the reservation is released exactly once, on proof.
    assert _sweep(env, clock=clock).terminalized == 1
    assert _sweep(env, clock=clock).terminalized == 1
    assert [
        row["terminal_reconciliation_state"]
        for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    ] == ["FLAT_AWAITING_RECONCILIATION", "FINAL_VERIFIED"]
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (0, 0.0)

    # And none of it is repeated: a further sweep writes nothing at all.
    before = _store_fingerprint(env.server.writer)
    again = _sweep(env, clock=clock)
    assert again.triggered == 0
    assert again.exit_attempted == 0
    assert _store_fingerprint(env.server.writer) == before


def test_ac_004_an_indeterminate_writer_verdict_is_never_a_cached_success(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-004: an unproven action answer commits nothing, withholds new authority, and never becomes a remembered exit."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    indeterminate = _IndeterminateActionClient(env.client)
    unproven = _sweep(
        env,
        clock=clock,
        transport=_transport(clock, bid=90.0, ask=90.5),
        client=indeterminate,
    )

    assert indeterminate.calls == 1
    assert unproven.triggered == 0
    assert unproven.retryable == 1
    assert unproven.new_admissions_allowed is False
    # Nothing was committed: no trigger, no EXIT order, plan still armed, capital
    # still reserved.
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 0
    assert len(_rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)) == 1
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (1, 500.0)

    # The unproven verdict was never cached as an exit: a healthy writer triggers
    # exactly once from the same committed evidence.
    _next_scan(clock)
    healthy = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert healthy.triggered == 1, healthy.details
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 1
    assert len(_exit_orders(env, trade_id)) == 1


# ---------------------------------------------------------------------------
# AC-005: restart and recovery with open exposure
# ---------------------------------------------------------------------------


def test_ac_005_a_restart_between_the_trigger_and_its_exit_finishes_it_once(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-005: a restart after the trigger resumes the committed exit, releases capacity once, and then writes nothing."""

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)
    assert (
        _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)).triggered
        == 1
    )

    # The process dies with the exit still outstanding and returns on a fresh
    # client over the same durable store.
    env.restart()
    assert _item(env, trade_id).protection_state == ProtectionState.TRIGGERED.value

    _next_scan(clock)
    resumed = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert resumed.exit_attempted == 1, resumed.details
    # The SAME trigger and the SAME EXIT order, filled exactly once.
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 1
    assert len(_exit_orders(env, trade_id)) == 1
    assert len(_sell_fills(env, trade_id)) == 1
    assert float(_item(env, trade_id).remaining_quantity) == 0.0

    # Flat, then verified: the reservation is released exactly once.
    assert _sweep(env, clock=clock).terminalized == 1
    assert _sweep(env, clock=clock).terminalized == 1
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (0, 0.0)

    # A further restart and sweep over committed evidence adds nothing at all.
    env.restart()
    before = _store_fingerprint(env.server.writer)
    quiet = _sweep(env, clock=clock)
    assert quiet.triggered == 0
    assert quiet.exit_attempted == 0
    assert _store_fingerprint(env.server.writer) == before


def test_ac_005_an_admission_backed_entry_is_protected_across_a_restart(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-005: the authority protects and exits the entry the R5-E chain admitted, across a restart."""

    clock = _Clock(QUALIFICATION_TIME)
    opportunity = _prt_opportunity("SOLUSD")
    # The entry comes from the real R5-E chain: the R5-C/R5-D capital authority
    # commits the planned reservation, and the seam dispatches it into exactly one
    # canonical execution. No test invents a disposition or a reservation.
    _context_id, admitted = _commit_planned_admission(
        env, opportunity, moment=QUALIFICATION_TIME
    )
    outcome, _book = _dispatch_once(env, opportunity, clock=clock)
    assert outcome.status == OUTCOME_EXECUTED, outcome.detail
    trade_id = admitted.paper_trade_id

    # The protective authority arms the plan the dispatch committed.
    armed = _sweep(env, clock=clock)
    assert armed.activated == 1, armed.details
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value

    # Restart with the position open and fully exposed.
    env.restart()
    _next_scan(clock)
    triggered = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert triggered.triggered == 1, triggered.details
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    assert [
        float(row["reference_price"])
        for row in _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
    ] == [90.0]

    # The exit completes exactly once, and the admitted reservation is released.
    _next_scan(clock)
    assert (
        _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)).exit_attempted
        == 1
    )
    assert len(_sell_fills(env, trade_id)) == 1
    assert _sweep(env, clock=clock).terminalized == 1
    assert _sweep(env, clock=clock).terminalized == 1
    assert [
        row["terminal_reconciliation_state"]
        for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    ] == ["FLAT_AWAITING_RECONCILIATION", "FINAL_VERIFIED"]
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (0, 0.0)


# ---------------------------------------------------------------------------
# AC-006: capacity - protection is not starved, and releases only proven exits
# ---------------------------------------------------------------------------


def test_ac_006_a_saturated_portfolio_is_fully_protected_in_one_sweep(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-006: every committed position is advanced in one sweep at committed capacity, so the admission workload cannot starve protection."""

    symbols = ("BTCUSD", "ETHUSD", "SOLUSD")
    clock = _Clock(QUALIFICATION_TIME)
    trades = _arm_trades(env, clock, symbols)

    # Capacity is bounded by the producer itself. The commit below is real: the
    # further opportunity is refused by the producer's own capacity rule, and it
    # therefore never becomes unprotected exposure that protection has to find.
    refused = _produce_one_more_entry(env, clock, "ADAUSD")
    assert refused.status == QualifiedOpportunityDisposition.CAPACITY_REJECTED.value
    assert refused.paper_trade_id is None

    _next_scan(clock)
    book = _PerSymbolTransport(
        clock=clock,
        books={_native_symbol(symbol): (90.0, 90.5) for symbol in symbols},
    )
    result = _sweep(env, clock=clock, transport=book)

    # Every committed position is advanced, and the refused opportunity produced no
    # protection item of its own: four dispositions exist, three exposures do.
    assert len(_rows(env, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)) == 4
    assert result.considered == len(symbols)
    assert result.triggered == len(symbols), result.details
    for trade in trades:
        trade_id = trade["paper_trade_id"]
        assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
        assert len(_exit_orders(env, trade_id)) == 1
    # Every instrument was really read, exactly once: no item was skipped for lack
    # of capacity, and none was read twice.
    assert sorted(book.requests) == sorted(
        _native_symbol(symbol) for symbol in symbols
    )
    # Protection was proven throughout, so new authority is not withheld.
    assert result.new_admissions_allowed is True


def test_ac_006_capacity_is_released_only_from_committed_exits(env):
    """ATDD-R5-E-F11-independent-protection-validation/AC-006: capital stays committed until the exit is filled and verified, then returns exactly once."""

    clock = _Clock(QUALIFICATION_TIME)
    btc, eth = _arm_trades(env, clock, ("BTCUSD", "ETHUSD"))
    btc_id = btc["paper_trade_id"]
    eth_id = eth["paper_trade_id"]
    _next_scan(clock)

    def _books(btc_book: tuple[float, float]) -> dict[str, tuple[float, float]]:
        return {
            _native_symbol("BTCUSD"): btc_book,
            _native_symbol("ETHUSD"): (100.0, 100.5),
        }

    triggered = _sweep(
        env,
        clock=clock,
        transport=_PerSymbolTransport(clock=clock, books=_books((90.0, 90.5))),
    )
    assert triggered.triggered == 1, triggered.details
    # The untouched instrument is not dragged into the other's exit.
    assert _state_of(env, eth_id) == ProtectionState.ACTIVE.value

    # The exit cannot be filled: the book shows less than the approved size. Nothing
    # is released while an exit is outstanding, and new authority is withheld.
    _next_scan(clock)
    thin = _sweep(
        env,
        clock=clock,
        transport=_PerSymbolTransport(
            clock=clock, books=_books((90.0, 90.5)), bid_qty=2.0
        ),
    )
    assert thin.exit_attempted == 0
    assert thin.retryable == 1
    assert thin.new_admissions_allowed is False
    assert _sell_fills(env, btc_id) == []
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (2, 1000.0)

    # The same EXIT order fills later, from a book that can support it.
    _next_scan(clock)
    filled = _sweep(
        env,
        clock=clock,
        transport=_PerSymbolTransport(clock=clock, books=_books((90.0, 90.5))),
    )
    assert filled.exit_attempted == 1, filled.details
    assert len(_sell_fills(env, btc_id)) == 1
    # Flat but not yet verified: still reserved, still counted.
    active, reserved, _states = _book_state(env)
    assert (active, reserved) == (2, 1000.0)

    # Verified: the released trade's capital returns to the book exactly once, and
    # the other trade's reservation is untouched.
    assert _sweep(env, clock=clock).terminalized == 1
    assert _sweep(env, clock=clock).terminalized == 1
    active, reserved, states = _book_state(env)
    assert (active, reserved) == (1, 500.0)
    assert states[eth_id] == "ACTIVE"

    # A further sweep changes nothing: capacity is released once, not repeatedly.
    before = _store_fingerprint(env.server.writer)
    quiet = _sweep(env, clock=clock)
    assert quiet.triggered == 0
    assert quiet.terminalized == 0
    assert _store_fingerprint(env.server.writer) == before


# ---------------------------------------------------------------------------
# AC-007: the target authority and the live-domain observer are independent
# ---------------------------------------------------------------------------


def test_ac_007_the_protection_authority_reads_no_live_domain_observer(env, monkeypatch):
    """ATDD-R5-E-F11-independent-protection-validation/AC-007: the live exposure domain and the F11 report are neither a dependency of the target authority nor able to stop it."""

    reachable = _app_imports(PROTECTION_RUNTIME_PATH)
    # Non-vacuous: the census sees the module's real imports.
    assert reachable
    observers = [
        module
        for module in sorted(reachable)
        if any(fragment in module for fragment in FORBIDDEN_APP_IMPORT_FRAGMENTS)
    ]
    assert observers == [], observers

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    called: list[str] = []

    def _break(what: str):
        def _fail(*args: Any, **kwargs: Any) -> Any:
            called.append(what)
            raise RuntimeError(f"{what} is unavailable")

        return _fail

    for target, what in LIVE_DOMAIN_OBSERVERS:
        monkeypatch.setattr(target, _break(what))

    result = _run_scheduled_sweep(
        monkeypatch, env, clock, transport=_transport(clock, bid=90.0, ask=90.5)
    )

    assert result.triggered == 1, result.details
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    assert called == []


def test_ac_007_a_healthy_target_verdict_can_never_lift_a_strict_f11_objection():
    """ATDD-R5-E-F11-independent-protection-validation/AC-007: the portfolio's clean bill of health is never a licence - the strict decision fails closed on its own evidence, and a target objection can only suspend further."""

    healthy_target = {
        "coverage_complete": True,
        "incidents_healthy": True,
        "target_protection_allows_admissions": True,
    }

    unmanaged = ph.evaluate_protection_health(
        (_strict_exposure("VERIFIED_UNMANAGED"),), **healthy_target
    )
    assert unmanaged.state == ph.STATE_UNSAFE
    assert ph.REASON_UNMANAGED_EXPOSURE in unmanaged.reason_codes
    assert unmanaged.admissions_suspended is True

    uncertain = ph.evaluate_protection_health(
        (_strict_exposure("DEGRADED"),), **healthy_target
    )
    assert uncertain.state == ph.STATE_UNAVAILABLE
    assert ph.REASON_EXPOSURE_UNCERTAIN in uncertain.reason_codes
    assert uncertain.admissions_suspended is True

    incomplete = ph.evaluate_protection_health(
        (),
        coverage_complete=False,
        incidents_healthy=True,
        target_protection_allows_admissions=True,
    )
    assert incomplete.state == ph.STATE_UNAVAILABLE
    assert incomplete.admissions_suspended is True

    unproven_incidents = ph.evaluate_protection_health(
        (_strict_exposure("VERIFIED_MANAGED"),),
        coverage_complete=True,
        incidents_healthy=None,
        target_protection_allows_admissions=True,
    )
    assert unproven_incidents.state == ph.STATE_UNAVAILABLE
    assert unproven_incidents.admissions_suspended is True

    # A target-domain objection can only ever suspend further; it is never a way to
    # make an objection disappear.
    objection = ph.evaluate_protection_health(
        (_strict_exposure("VERIFIED_MANAGED"),),
        coverage_complete=True,
        incidents_healthy=True,
        target_protection_allows_admissions=False,
    )
    assert objection.state == ph.STATE_UNSAFE
    assert ph.REASON_TARGET_PROTECTION_UNHEALTHY in objection.reason_codes
    assert objection.admissions_suspended is True

    # Only a fully proven set with no target objection is healthy at all.
    proven = ph.evaluate_protection_health(
        (_strict_exposure("VERIFIED_MANAGED"),),
        coverage_complete=True,
        incidents_healthy=True,
        target_protection_allows_admissions=None,
    )
    assert proven.state == ph.STATE_HEALTHY
    assert proven.admissions_suspended is False


# ---------------------------------------------------------------------------
# AC-008: this increment's own bookkeeping
# ---------------------------------------------------------------------------


def test_ac_008_the_contract_pointer_and_validated_increment_are_consistent():
    """ATDD-R5-E-F11-independent-protection-validation/AC-008: this contract, the pointer and the increment it validates agree."""

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
    for ac_id, body in contract.acceptance_criteria.items():
        for label in ("GIVEN:", "WHEN:", "THEN:"):
            assert label in body, (ac_id, label)

    contracts = load_scope_contracts(contract_dir)
    pointer = read_increment_pointer()
    # The pointer is deliberately movable orchestration state, so it is never pinned
    # to this module's own increment identity: it must resolve to exactly one
    # existing contract instead of being asserted equal to this one.
    assert len([c for c in contracts if c.increment == pointer]) == 1, pointer
    assert any(c.increment == INCREMENT for c in contracts)
    # The increment this one validates is still a declared contract, so its own
    # pointer-based scope control and its tests keep working.
    assert any(c.increment == "ATDD-R5-E-portfolio-lifecycle-closure" for c in contracts)

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.passed, result.reasons
    for ac_id, nodes in contract.traceability.items():
        assert set(nodes) == set(index.by_ac.get((INCREMENT, ac_id), frozenset())), ac_id
    assert not [
        node
        for node in index.unscoped_tests
        if node.startswith("tests/test_opip_r5_e_f11_independent_protection.py")
    ]
    for paths in contract.implementation_map.values():
        for path in paths:
            assert (APP_ROOT.parent / path).is_file(), path


def test_ac_008_the_posture_and_release_surface_are_unchanged():
    """ATDD-R5-E-F11-independent-protection-validation/AC-008: this increment changes no release profile and no deployment posture."""

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


def test_ac_008_the_increment_adds_no_scheduler_and_no_second_authority():
    """ATDD-R5-E-F11-independent-protection-validation/AC-008: one scheduled protection path, one atomic protection authority, no new engine."""

    # The scheduled protection path exists exactly once: the scan calls the runtime,
    # and no other application module calls the sweep at all.
    callers = sorted(
        path.relative_to(APP_ROOT).as_posix()
        for path in (APP_ROOT / "app").rglob("*.py")
        if "__pycache__" not in path.parts and "run_protection_sweep" in _called_names(path)
    )
    assert callers == ["app/jobs/scan_opportunities.py"], callers

    # The atomic protection action is the writer's own authority. It is dispatched
    # by the writer's own transport and composed by exactly one application module:
    # the protection runtime. Both halves are asserted, not assumed.
    action_callers = sorted(
        path.relative_to(APP_ROOT).as_posix()
        for path in (APP_ROOT / "app").rglob("*.py")
        if "__pycache__" not in path.parts
        and "trigger_paper_protection_action" in _called_names(path)
    )
    assert action_callers == [
        "app/opip/canonical/server.py",
        "app/services/paper_v2_protection_runtime.py",
    ], action_callers
    composers = [
        path
        for path in action_callers
        if not path.startswith("app/opip/canonical/")
    ]
    assert composers == ["app/services/paper_v2_protection_runtime.py"], composers

    # No second writer, event store, ledger or scheduler is introduced: the runtime
    # still reaches the producer and the canonical writer client, and nothing that
    # schedules or stores work of its own.
    reachable = _app_imports(PROTECTION_RUNTIME_PATH)
    assert {
        "app.services.paper_v2_execution",
        "app.services.paper_v2_pretrade_adapter",
    } <= reachable
    assert not [module for module in reachable if "scheduler" in module or "cron" in module]
    # And the scan really is the one scheduled seam: it calls the sweep.
    assert "run_protection_sweep" in _called_names(SCAN_JOB_PATH)

