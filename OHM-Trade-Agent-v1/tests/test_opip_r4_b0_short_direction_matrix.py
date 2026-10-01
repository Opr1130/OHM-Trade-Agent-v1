"""R4-B0 explicit direction/side falsification matrix for Paper-v2 LONG+SHORT.

These tests are adversarial: each is written so an implementation that used the
WRONG book side, the WRONG comparator direction, or the WRONG ancestry would
produce a different number or a different disposition and fail. They complement
the existing Paper-v2 suites, which prove the machinery works; this module tries
to break it.

The books in this module are deliberately asymmetric (``bid`` and ``ask`` differ
materially) so a wrong-side read changes both the fill price and the P&L rather
than merely the reported side string.

R4-B0 remains NON-AUTHORITATIVE: this is a contract/simulation proof only. No
test here activates a mode, wires a runtime, or reaches a funded/exchange order
path.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.exchanges.kraken import KrakenClient
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.models import PaperV2ProtectionWorkItem
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.paper_execution import (
    PAPER_PROTECTION_MODEL_VERSION,
    ProtectionState,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_FILL_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_PROTECTION_STATE_RECORDED,
    PAPER_PROTECTION_TRIGGER_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (
    PaperAdmissionRequest,
    expected_paper_side,
    paper_trade_direction_contract,
    validate_admission_request_record_payload,
)
from app.opip.contracts.serialization import iso_z
from app.services.paper_v2_execution import (
    PaperV2Opportunity,
    _entry_executable_price,  # noqa: PLC2701 - the unit under adversarial test
    run_paper_v2_opportunity,
)
from app.services.paper_v2_protection_runtime import (
    _evaluate_trigger,  # noqa: PLC2701 - the unit under adversarial test
    _executable_exit_price,  # noqa: PLC2701
    _executable_exit_quantity,  # noqa: PLC2701
    _exit_side_for,  # noqa: PLC2701
    run_protection_sweep,
)

APP_ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
QUALIFICATION_TIME = NOW + timedelta(seconds=5)

#: Deliberately asymmetric books. The gap is wide enough that reading the wrong
#: side changes the fill price (and therefore the economics) materially.
WIDE_BID = 99.5
WIDE_ASK = 100.5


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _Settings:
    opip_paper_v2_mode = "active"
    paper_v2_quote_max_age_seconds = 15
    paper_trade_fee_rate = 0.004
    paper_trade_slippage_bps = 10.0
    paper_v2_tp1_fraction = 0.5
    paper_v2_max_hold_seconds = 86_400
    account_equity = 10_000.0
    paper_trade_starting_equity = 10_000.0


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value = self.value + timedelta(**kwargs)


class _BookTransport:
    """STUB of the public Level-1 transport only. Grants no order authority."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        bid: float,
        ask: float,
        bid_qty: float = 10.0,
        ask_qty: float = 10.0,
    ) -> None:
        self.requests: list[tuple[str, Any]] = []
        self._clock = clock
        self.bid = float(bid)
        self.ask = float(ask)
        self.bid_qty = float(bid_qty)
        self.ask_qty = float(ask_qty)

    def request(self, endpoint, params, timeout_seconds):
        self.requests.append((endpoint, params.get("symbol")))
        ts = iso_z(self._clock() - timedelta(seconds=1), field_name="publication_ts")
        return {
            "symbol": params.get("symbol"),
            "bids": [{"price": self.bid, "qty": self.bid_qty, "publication_ts": ts}],
            "asks": [{"price": self.ask, "qty": self.ask_qty, "publication_ts": ts}],
        }

    def telemetry_snapshot(self) -> dict:
        return {}


class _Env:
    """One canonical store plus a restart seam sharing no in-memory state."""

    def __init__(self, *, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.db_path = tmp_path / "canonical.sqlite3"
        self._sequence = 0
        self.server = self._new_server()
        self.client = InProcessWriterClient(self.server)

    def _new_server(self) -> CanonicalWriterServer:
        self._sequence += 1
        return CanonicalWriterServer(
            db_path=self.db_path,
            socket_path=self.tmp_path / f"canonical-{self._sequence}.sock",
        )

    def restart(self) -> None:
        self.server.stop()
        self.server = self._new_server()
        self.client = InProcessWriterClient(self.server)


@pytest.fixture()
def env(tmp_path: Path) -> _Env:
    holder = _Env(tmp_path=tmp_path)
    try:
        yield holder
    finally:
        holder.server.stop()


def _native_symbol(production_symbol: str) -> str:
    return f"{production_symbol.removesuffix('USD')}/USD"


def _snapshot_payload(*, symbol: str) -> dict:
    from app.services.canonical_episode_capture import build_canonical_episode_snapshots

    observation = SimpleNamespace(
        symbol=symbol,
        base_asset=symbol.removesuffix("USD"),
        kraken_public_symbol=_native_symbol(symbol),
        last_price=100.0,
        ticker_last=100.0,
        volume_24h=100_000.0,
        notional_24h_usd_approx=1_000_000.0,
        high_24h=105.0,
        low_24h=95.0,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=4.0,
    )
    candidate = SimpleNamespace(
        symbol=symbol,
        universe_size=3,
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        opportunity_score=78.0,
        explosion_potential_score=74.0,
        tradeability_score=72.0,
        pattern_strength_score=80.0,
        volume_acceleration_score=70.0,
        relative_strength_score=88.0,
        persistence_scans=3,
        exhaustion_penalty=10.0,
        exhaustion_band="LOW",
        relative_strength_percentile=95.0,
        suppressed=False,
        reasons=(),
        components={"near_high": 75.0},
    )
    return build_canonical_episode_snapshots(
        [observation],
        candidates=[candidate],
        decision_at=NOW,
        signal_quality_enabled=True,
        scan_source="LIVE_FULL_MARKET",
    )[0]


def _instrument_version(symbol: str) -> InstrumentVersion:
    return InstrumentVersion(
        venue="kraken",
        base_asset=symbol.removesuffix("USD"),
        quote_currency="USD",
        venue_instrument_id=_native_symbol(symbol),
        version=1,
        reference_data_version=f"ref-{symbol.lower()}",
        observed_at_utc=NOW - timedelta(minutes=5),
    )


def _opportunity(
    production_symbol: str = "SOLUSD",
    *,
    direction: str = "LONG",
    requested_quantity: float = 4.0,
) -> PaperV2Opportunity:
    """Already-qualified geometry, mirrored correctly for the direction.

    LONG:  entry band [99, 101], chase above (102), stop below (90), targets up.
    SHORT: the same band, chase below (98), stop above (110), targets down.

    Both are consistent with the single geometry kernel's invariant
    (``stop < entry_reference < target_1 < target_2`` for LONG, and the exact
    mirror for SHORT), so a SHORT that reached this point is a *valid* trade and
    any failure is the behaviour under test rather than a malformed fixture.
    """
    from app.opip.decision.versioning import GATE_POLICY_VERSION, gate_policy_fingerprint

    snapshot = _snapshot_payload(symbol=production_symbol)
    version = _instrument_version(production_symbol)
    if direction == "SHORT":
        entry_low, entry_high, chase = 99.0, 101.0, 98.0
        stop, targets = 110.0, (90.0, 80.0)
    else:
        entry_low, entry_high, chase = 99.0, 101.0, 102.0
        stop, targets = 90.0, (110.0, 120.0)
    return PaperV2Opportunity(
        candidate_id="OPIPC:" + "a" * 20,
        episode_id=snapshot["episode_id"],
        cohort_id=snapshot["cohort_id"],
        direction=direction,
        instrument_version_id=version.instrument_version_id,
        snapshot_payload=snapshot,
        evaluation_time=QUALIFICATION_TIME,
        evidence_cutoff=NOW,
        source_record_refs=(),
        qualification_policy_version=GATE_POLICY_VERSION,
        qualification_policy_fingerprint=gate_policy_fingerprint(),
        instrument_version=version,
        quote_currency="USD",
        requested_capital=500.0,
        requested_reservation_amount=500.0,
        decision_time=QUALIFICATION_TIME,
        native_symbol=_native_symbol(production_symbol),
        requested_quantity=requested_quantity,
        requested_notional=requested_quantity * 100.0,        entry_low=entry_low,
        entry_high=entry_high,
        chase_limit=chase,
        stop_price=stop,
        target_prices=targets,
    )


def _open_entry(
    env: _Env,
    *,
    clock: _Clock,
    symbol: str = "SOLUSD",
    direction: str = "LONG",
    bid: float = WIDE_BID,
    ask: float = WIDE_ASK,
    quantity: float = 4.0,
    settings: Any | None = None,
) -> dict:
    opportunity = _opportunity(symbol, direction=direction, requested_quantity=quantity)
    transport = _BookTransport(clock=clock, bid=bid, ask=ask, bid_qty=10.0, ask_qty=10.0)
    result = run_paper_v2_opportunity(
        opportunity,
        client=env.client,
        kraken_client=KrakenClient(transport=transport),
        settings=settings or _Settings(),
        now=QUALIFICATION_TIME,
        clock=clock,
    )
    state = env.client.get_paper_v2_execution_state(result.disposition_id)
    assert state.status == "OK", state.error_code or state.status
    return {
        "symbol": symbol,
        "direction": direction,
        "disposition_id": result.disposition_id,
        "paper_trade_id": result.paper_trade_id,
        "reservation_id": result.reservation_id,
        "protection_plan_id": result.protection_plan_id,
        "entry_order_intent_id": result.entry_order_intent_id,
        "filled_quantity": float(state.filled_quantity),
    }


def _sweep(env: _Env, *, clock: _Clock, transport: _BookTransport | None = None):
    return run_protection_sweep(
        env.client,
        kraken_client=KrakenClient(transport=transport or _BookTransport(clock=clock, bid=WIDE_BID, ask=WIDE_ASK)),
        settings=_Settings(),
        execution_clock=clock,
    )


def _rows(env: _Env, event_type: str, paper_trade_id: str | None = None) -> list[dict]:
    sql = "SELECT payload_json FROM events WHERE event_type = ?"
    params: list[Any] = [event_type]
    if paper_trade_id is not None:
        sql += " AND json_extract(payload_json, '$.paper_trade_id') = ?"
        params.append(paper_trade_id)
    sql += " ORDER BY history_epoch, local_sequence"
    rows = env.server.writer._conn.execute(sql, params).fetchall()  # noqa: SLF001
    return [json.loads(str(row["payload_json"])) for row in rows]


def _item(env: _Env, paper_trade_id: str) -> PaperV2ProtectionWorkItem:
    projection = env.client.get_paper_v2_protection_work()
    assert projection.status == "OK", projection.error_code or projection.status
    for item in projection.items:
        if item.paper_trade_id == paper_trade_id:
            return item
    raise AssertionError(f"no protection work for {paper_trade_id}")


# ---------------------------------------------------------------------------
# A. Asymmetric bid/ask orientation (numbers, not side strings)
# ---------------------------------------------------------------------------


def test_a_long_entry_executes_on_the_ask_and_short_entry_on_the_bid(env):
    """An ENTRY reads the side its direction must consume, proven numerically."""
    assert _entry_executable_price({"best_bid": WIDE_BID, "best_ask": WIDE_ASK}, entry_side="BUY") == WIDE_ASK
    assert _entry_executable_price({"best_bid": WIDE_BID, "best_ask": WIDE_ASK}, entry_side="SELL") == WIDE_BID
    assert WIDE_ASK != WIDE_BID

    clock = _Clock(QUALIFICATION_TIME)
    long_trade = _open_entry(env, clock=clock, symbol="SOLUSD", direction="LONG")
    long_fill = _rows(env, PAPER_FILL_RECORDED, long_trade["paper_trade_id"])[0]
    assert long_fill["side"] == "BUY"
    assert float(long_fill["price"]) == WIDE_ASK, "a LONG ENTRY must pay the ask"

    short_trade = _open_entry(env, clock=clock, symbol="XRPUSD", direction="SHORT")
    short_fill = _rows(env, PAPER_FILL_RECORDED, short_trade["paper_trade_id"])[0]
    assert short_fill["side"] == "SELL"
    assert float(short_fill["price"]) == WIDE_BID, "a SHORT ENTRY must receive the bid"
    assert float(short_fill["price"]) != float(long_fill["price"])


def test_a_exit_side_reads_the_correct_book_side_and_depth():
    """EXIT price and displayed depth follow the exit side, not a fixed bid."""
    assert _exit_side_for("LONG") == "SELL"
    assert _exit_side_for("SHORT") == "BUY"
    quote = {"best_bid": WIDE_BID, "best_ask": WIDE_ASK, "bid_quantity": 3.0, "ask_quantity": 7.0}
    assert _executable_exit_price(quote, exit_side="SELL") == WIDE_BID
    assert _executable_exit_price(quote, exit_side="BUY") == WIDE_ASK
    assert _executable_exit_quantity(quote, exit_side="SELL") == 3.0
    assert _executable_exit_quantity(quote, exit_side="BUY") == 7.0


def test_a_short_pnl_is_positive_when_price_falls_and_negative_when_it_rises(env):
    """Realized SHORT gross P&L follows quantity * (entry - exit), both signs."""
    # Falling price: the short covers below its entry, so it profits.
    clock = _Clock(QUALIFICATION_TIME)
    winner = _open_entry(env, clock=clock, symbol="GRTUSD", direction="SHORT", quantity=4.0)
    winner_id = winner["paper_trade_id"]
    assert _sweep(env, clock=clock).activated == 1
    clock.advance(seconds=60)
    assert _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=89.0, ask=90.0)).triggered == 1
    clock.advance(seconds=60)
    _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=89.0, ask=90.0))
    clock.advance(seconds=60)
    _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=79.0, ask=80.0))
    for _ in range(5):
        clock.advance(seconds=60)
        _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=79.0, ask=80.0))
    final = _rows(env, PAPER_RECONCILIATION_RECORDED, winner_id)[-1]
    # entry SELL 4 @ 99.5 = 398 received; covers BUY 2 @ 90 + 2 @ 80 = 340 paid.
    assert float(final["realized_gross_pnl"]) == pytest.approx(58.0), final
    assert float(final["realized_net_pnl"]) < float(final["realized_gross_pnl"])

    # Rising price: the stop covers above the entry, so the short loses.
    loser = _open_entry(env, clock=clock, symbol="CRVUSD", direction="SHORT", quantity=4.0)
    loser_id = loser["paper_trade_id"]
    assert _sweep(env, clock=clock).activated == 1
    clock.advance(seconds=60)
    assert _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=110.0, ask=111.0)).triggered == 1
    clock.advance(seconds=60)
    _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=110.0, ask=111.0))
    for _ in range(5):
        clock.advance(seconds=60)
        _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=110.0, ask=111.0))
    stopped = _rows(env, PAPER_RECONCILIATION_RECORDED, loser_id)[-1]
    # entry SELL 4 @ 99.5 = 398 received; cover BUY 4 @ 111 = 444 paid.
    assert float(stopped["realized_gross_pnl"]) == pytest.approx(-46.0), stopped



def _short_item(*, remaining: float = 5.0, entry: float = 5.0, triggers: list | None = None, targets=None):
    plan = {
        "protection_plan_id": "PPLAN:" + "b" * 20,
        "plan_seq": 1,
        "stop_price": 110.0,
        "targets": targets
        if targets is not None
        else [
            {"target_id": "TP1", "price": 90.0, "fraction": 0.5},
            {"target_id": "TP2", "price": 80.0, "fraction": 0.5},
        ],
        "max_hold_seconds": 86_400,
        "plan_time": {"precision": "EXACT", "basis": "SOURCE_REPORTED", "occurred_at": iso_z(NOW, field_name="plan_time")},
        "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
    }
    return PaperV2ProtectionWorkItem(
        paper_trade_id="PTV2:" + "c" * 20,
        direction="SHORT",
        entry_quantity=entry,
        remaining_quantity=remaining,
        protection_plan=plan,
        protection_state=ProtectionState.ACTIVE.value,
        triggers=list(triggers or []),
    )


def test_a_short_trigger_uses_the_ask_not_the_bid():
    """A SHORT covers into the ask; a bid-side price must not satisfy the STOP."""
    item = _short_item()
    plan = item.protection_plan
    # Ask below the stop and bid above it: only the ask reading is "not stopped".
    crossed_book = {"best_bid": 111.0, "best_ask": 109.0}
    decision = _evaluate_trigger(item, plan=plan, quote=crossed_book, now=NOW, direction="SHORT")
    assert decision is None, "a bid-side read would have falsely triggered the SHORT stop"

    # Ask at/above the stop: the SHORT stop fires, priced from the ask.
    stopped = {"best_bid": 105.0, "best_ask": 111.0}
    decision = _evaluate_trigger(item, plan=plan, quote=stopped, now=NOW, direction="SHORT")
    assert decision is not None
    trigger_type, quantity, reference = decision
    assert trigger_type == "STOP"
    assert quantity == 5.0
    assert reference == 111.0, "the SHORT stop reference price must be the ask"


# ---------------------------------------------------------------------------
# B. STOP / TARGET boundaries (below / exactly at / above)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "side_key", "price", "expected"),
    [
        # LONG protection price = bid; stop 90, target 110.
        ("LONG", "best_bid", 89.99, "STOP"),
        ("LONG", "best_bid", 90.0, "STOP"),
        ("LONG", "best_bid", 90.01, None),
        ("LONG", "best_bid", 109.99, None),
        ("LONG", "best_bid", 110.0, "TARGET"),
        ("LONG", "best_bid", 110.01, "TARGET"),
        # SHORT protection price = ask; stop 110, nearest target 90.
        ("SHORT", "best_ask", 109.99, None),
        ("SHORT", "best_ask", 110.0, "STOP"),
        ("SHORT", "best_ask", 110.01, "STOP"),
        ("SHORT", "best_ask", 90.01, None),
        ("SHORT", "best_ask", 90.0, "TARGET"),
        ("SHORT", "best_ask", 89.99, "TARGET"),
    ],
)
def test_b_stop_and_target_boundaries_are_exact(direction, side_key, price, expected):
    """Below / exactly-at / above each threshold, on the executable side only."""
    if direction == "SHORT":
        item = _short_item()
    else:
        plan = {
            "protection_plan_id": "PPLAN:" + "b" * 20,
            "plan_seq": 1,
            "stop_price": 90.0,
            "targets": [
                {"target_id": "TP1", "price": 110.0, "fraction": 0.5},
                {"target_id": "TP2", "price": 120.0, "fraction": 0.5},
            ],
            "max_hold_seconds": 86_400,
            "plan_time": {"precision": "EXACT", "basis": "SOURCE_REPORTED", "occurred_at": iso_z(NOW, field_name="plan_time")},
            "protection_model_version": PAPER_PROTECTION_MODEL_VERSION,
        }
        item = PaperV2ProtectionWorkItem(
            paper_trade_id="PTV2:" + "d" * 20,
            direction="LONG",
            entry_quantity=5.0,
            remaining_quantity=5.0,
            protection_plan=plan,
            protection_state=ProtectionState.ACTIVE.value,
            triggers=[],
        )
    quote = {"best_bid": 100.0, "best_ask": 100.0}
    quote[side_key] = price
    # Keep the untested side deliberately far away so only the tested side can act.
    other = "best_ask" if side_key == "best_bid" else "best_bid"
    quote[other] = 100.0 if side_key == "best_bid" else 100.0
    decision = _evaluate_trigger(
        item, plan=item.protection_plan, quote=quote, now=NOW, direction=direction
    )
    assert (decision[0] if decision else None) == expected, (direction, side_key, price, decision)


def test_b_short_target_precedence_and_tp1_depth():
    """The SHORT stop outranks the target, and TP1 claims its configured fraction."""
    item = _short_item()
    # Both conditions hold (ask at/above stop and at/below target is impossible
    # for one price, so prove precedence with a stop-crossing price only).
    decision = _evaluate_trigger(
        item, plan=item.protection_plan, quote={"best_bid": 100.0, "best_ask": 111.0}, now=NOW, direction="SHORT"
    )
    assert decision[0] == "STOP"

    decision = _evaluate_trigger(
        item, plan=item.protection_plan, quote={"best_bid": 100.0, "best_ask": 90.0}, now=NOW, direction="SHORT"
    )
    assert decision[0] == "TARGET"
    assert decision[1] == pytest.approx(2.5), "TP1 must cover only its configured half"


def test_b_short_target_consumes_the_nearest_target_first():
    """A SHORT profits as price falls, so TP1 is the HIGHER (nearest) target."""
    item = _short_item(triggers=[{"protection_plan_id": "PPLAN:" + "b" * 20, "trigger_type": "TARGET"}])
    decision = _evaluate_trigger(
        item, plan=item.protection_plan, quote={"best_bid": 100.0, "best_ask": 81.0}, now=NOW, direction="SHORT"
    )
    # TP1 (90) was consumed, so the eligible target is TP2 (80); at an 81 ask it
    # is not yet reached. A descending-then-ascending bug would fire here.
    assert decision is None
    reached = _evaluate_trigger(
        item, plan=item.protection_plan, quote={"best_bid": 100.0, "best_ask": 80.0}, now=NOW, direction="SHORT"
    )
    assert reached is not None and reached[0] == "TARGET"


# ---------------------------------------------------------------------------
# C. TIMEOUT / RISK_EXIT side
# ---------------------------------------------------------------------------


def test_c_timeout_exit_uses_the_direction_side():
    """TIME must exit with the direction's side, not always SELL."""
    expired = NOW + timedelta(seconds=86_401)
    item = _short_item()
    decision = _evaluate_trigger(
        item,
        plan=item.protection_plan,
        quote={"best_bid": 100.0, "best_ask": 100.0},
        now=expired,
        direction="SHORT",
    )
    assert decision is not None and decision[0] == "TIME"
    assert _exit_side_for("SHORT") == "BUY"
    assert _exit_side_for("LONG") == "SELL"


def test_c_short_timeout_and_stop_close_with_buy_side_end_to_end(env):
    """A real SHORT closes BUY-side through the production sweep."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="ADAUSD", direction="SHORT")
    trade_id = trade["paper_trade_id"]
    assert _sweep(env, clock=clock).activated == 1

    clock.advance(seconds=86_500)
    sweep = _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=WIDE_BID, ask=WIDE_ASK))
    assert sweep.triggered == 1, sweep.details

    exits = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if str(row["intent_role"]) == "EXIT"
    ]
    assert exits, "the SHORT must have committed an EXIT intent"
    assert all(str(row["side"]) == "BUY" for row in exits), "a SHORT exits by BUY-to-cover"


# ---------------------------------------------------------------------------
# D. Partial fill / partial cover
# ---------------------------------------------------------------------------


def test_d_short_partial_cover_then_terminal_cover(env):
    """TP1 covers half; the remainder cover terminalizes at exactly zero."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="DOTUSD", direction="SHORT", quantity=4.0)
    trade_id = trade["paper_trade_id"]
    assert _sweep(env, clock=clock).activated == 1

    # TP1 = 90: ask at/below 90 releases the first half only.
    clock.advance(seconds=60)
    first = _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=89.0, ask=90.0))
    assert first.triggered == 1, first.details
    clock.advance(seconds=60)
    # The EXIT attempt and its fill commit on the sweep after the trigger.
    _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=89.0, ask=90.0))
    covers = [row for row in _rows(env, PAPER_FILL_RECORDED, trade_id) if str(row["side"]) == "BUY"]
    assert len(covers) == 1, covers
    assert float(covers[0]["quantity"]) == pytest.approx(2.0), "only TP1's half may be covered"
    partial = _item(env, trade_id)
    assert partial.remaining_quantity == pytest.approx(2.0)
    assert partial.entry_quantity == pytest.approx(4.0)

    # The partial exit commits a residual plan revision and arms it in the same
    # step, so the remaining target can act on the next sweep.
    clock.advance(seconds=60)
    assert _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=89.0, ask=90.0)).partially_exited == 1

    # TP2 = 80 covers the residual and the trade reconciles terminal.
    clock.advance(seconds=60)
    assert _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=79.0, ask=80.0)).triggered == 1
    clock.advance(seconds=60)
    _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=79.0, ask=80.0))
    for _ in range(5):
        clock.advance(seconds=60)
        _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=79.0, ask=80.0))

    reconciliations = [
        row["terminal_reconciliation_state"]
        for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    ]
    assert reconciliations == ["FLAT_AWAITING_RECONCILIATION", "FINAL_VERIFIED"], reconciliations
    assert _item(env, trade_id).final_verified is True

    covers = [row for row in _rows(env, PAPER_FILL_RECORDED, trade_id) if str(row["side"]) == "BUY"]
    covered = sum(float(row["quantity"]) for row in covers)
    assert covered == pytest.approx(4.0), "total cover must equal the filled entry quantity"
    assert all(float(row["quantity"]) > 0 for row in covers), "no negative cover quantity"
    # Exactly two cover fills: no duplicate economic mutation on retry.
    assert len(covers) == 2, covers


def test_d_short_cover_longer_than_exposure_is_refused(env):
    """An EXIT that would over-cover is refused by the canonical writer."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="LINKUSD", direction="SHORT", quantity=4.0)
    trade_id = trade["paper_trade_id"]
    assert _sweep(env, clock=clock).activated == 1

    capacity = env.server.writer._exit_capacity(trade_id)  # noqa: SLF001
    over = capacity["available_exit_quantity"] + 1.0
    with pytest.raises(ValueError):
        env.server.writer._validate_action_exit_intent(  # noqa: SLF001
            {
                "intent_role": "EXIT",
                "side": "BUY",
                "requested_quantity": over,
            },
            capacity,
            direction="SHORT",
        )


def test_d_short_exit_side_must_match_direction(env):
    """A SHORT EXIT claiming SELL is refused: the side is bound to the direction."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="MATICUSD", direction="SHORT")
    trade_id = trade["paper_trade_id"]
    capacity = env.server.writer._exit_capacity(trade_id)  # noqa: SLF001
    with pytest.raises(ValueError, match="must use side BUY"):
        env.server.writer._validate_action_exit_intent(  # noqa: SLF001
            {"intent_role": "EXIT", "side": "SELL", "requested_quantity": 1.0},
            capacity,
            direction="SHORT",
        )


# ---------------------------------------------------------------------------
# E. Restart / recovery preserves SHORT from committed ancestry
# ---------------------------------------------------------------------------


def test_e_restart_preserves_short_direction_and_buy_to_cover(env):
    """Direction survives a real process restart and drives BUY-to-cover."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="AVAXUSD", direction="SHORT")
    trade_id = trade["paper_trade_id"]

    # A real restart: a fresh server over the same durable store, no shared memory.
    env.restart()

    recovered = _item(env, trade_id)
    assert recovered.direction == "SHORT", "direction must come from committed ancestry"
    assert _exit_side_for(recovered.direction) == "BUY"

    assert _sweep(env, clock=clock).activated == 1
    clock.advance(seconds=60)
    # Ask below the nearest SHORT target covers; the recovered runtime must price
    # the cover from the ask, not the bid.
    sweep = _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=89.0, ask=89.9))
    assert sweep.triggered == 1, sweep.details
    trigger = _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)[0]
    assert float(trigger["reference_price"]) == pytest.approx(89.9), "cover reference must be the ask"

    clock.advance(seconds=60)
    _sweep(env, clock=clock, transport=_BookTransport(clock=clock, bid=89.0, ask=89.9))
    cover = [row for row in _rows(env, PAPER_FILL_RECORDED, trade_id) if str(row["side"]) == "BUY"]
    assert cover and float(cover[0]["price"]) == pytest.approx(89.9)


def test_e_recovered_low_level_projection_carries_direction(env):
    """The protection-work projection itself exposes the committed direction."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="ATOMUSD", direction="SHORT")
    long_trade = _open_entry(env, clock=clock, symbol="NEARUSD", direction="LONG")
    env.restart()
    assert _item(env, trade["paper_trade_id"]).direction == "SHORT"
    assert _item(env, long_trade["paper_trade_id"]).direction == "LONG"


# ---------------------------------------------------------------------------
# F. Identity / idempotency
# ---------------------------------------------------------------------------


def test_f_repeated_sweeps_do_not_duplicate_economic_events(env):
    """Replaying protection over committed SHORT evidence is idempotent."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="UNIUSD", direction="SHORT")
    trade_id = trade["paper_trade_id"]

    _sweep(env, clock=clock)
    _sweep(env, clock=clock)
    _sweep(env, clock=clock)

    assert len(_rows(env, PAPER_PROTECTION_STATE_RECORDED, trade_id)) == 1
    assert len(_rows(env, PAPER_PROTECTION_PLAN_RECORDED, trade_id)) == 1
    assert len(_rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)) == 1
    assert len(_rows(env, PAPER_FILL_RECORDED, trade_id)) == 1


def test_f_replayed_short_entry_is_byte_identical(env):
    """Re-running the SHORT producer reproduces the same evidence, no duplicates."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock, symbol="FILUSD", direction="SHORT")
    trade_id = trade["paper_trade_id"]
    before = _rows(env, PAPER_FILL_RECORDED, trade_id)
    _open_entry(env, clock=clock, symbol="FILUSD", direction="SHORT")
    after = _rows(env, PAPER_FILL_RECORDED, trade_id)
    assert before == after, "a replay must not duplicate or alter the committed fill"


# ---------------------------------------------------------------------------
# G. Historical LONG compatibility vs new-format fail-closed
# ---------------------------------------------------------------------------


def test_g_historical_long_record_is_read_as_long_but_new_omission_fails():
    """The narrow legacy rule applies only to records that predate the contract."""
    base = dict(
        disposition_id="PDISP:" + "a" * 32,
        decision_context_id="DCTX:" + "b" * 32,
        disposition_seq=0,
        quote_currency="USD",
        requested_capital=500.0,
        disposition_time={
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": "2026-10-01T00:00:00Z",
        },
        expected_portfolio_version=1,
        capital_policy_version="paper-capital-v1",
        portfolio_equity_limit=10000.0,
        portfolio_position_limit=3,
        requested_reservation_amount=500.0,
    )
    modern = PaperAdmissionRequest(**base, direction="SHORT").as_dict()

    historical = {
        key: value
        for key, value in modern.items()
        if key not in ("direction", "direction_contract_version")
    }
    historical["guard_result"] = "ELIGIBLE"
    historical["observed_portfolio_version"] = 1
    assert validate_admission_request_record_payload(historical)["direction"] == "LONG"

    modern["guard_result"] = "ELIGIBLE"
    modern["observed_portfolio_version"] = 1
    assert validate_admission_request_record_payload(modern)["direction"] == "SHORT"

    omitted = {key: value for key, value in modern.items() if key != "direction"}
    with pytest.raises(ValueError):
        validate_admission_request_record_payload(omitted)

    # The same narrow rule is what the writer applies to a committed admission.
    assert paper_trade_direction_contract(historical) == "LONG"
    with pytest.raises(ValueError):
        paper_trade_direction_contract(
            {**modern, "direction_contract_version": 2, "direction": None}
        )


def test_g_historical_legacy_shot_never_falls_back_for_a_modern_record():
    """A modern record with no direction is never silently treated as LONG."""
    with pytest.raises(ValueError):
        paper_trade_direction_contract(
            {"schema_version": 1, "direction_contract_version": 2}
        )


# ---------------------------------------------------------------------------
# H. Isolation
# ---------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_h_short_path_imports_no_order_or_margin_surface():
    """The SHORT path cannot import an order-placement or margin API."""
    for relative in (
        "app/services/paper_v2_execution.py",
        "app/services/paper_v2_protection_runtime.py",
        "app/opip/canonical/writer.py",
    ):
        modules = _imported_modules(APP_ROOT / relative)
        for name in modules:
            lowered = name.lower()
            assert "kraken_private" not in lowered, (relative, name)
            assert "kraken_futures" not in lowered, (relative, name)
            assert not lowered.endswith("exchange_orders"), (relative, name)
            assert "margin_endpoint" not in lowered, (relative, name)


def test_h_short_implementation_calls_no_order_or_borrow_api():
    """No funded order, borrow or leverage *call* appears on the SHORT path.

    Matched as call syntax (``verb(``) so prose in a comment or docstring - such as
    the explicit statement that the path grants no borrow - is not mistaken for an
    invocation.
    """
    import re

    forbidden = (
        "create_order",
        "cancel_order",
        "place_order",
        "modify_order",
        "amend_order",
        "add_order",
        "borrow",
        "set_leverage",
        "enable_margin",
        "private_balance",
    )
    for relative in (
        "app/services/paper_v2_execution.py",
        "app/services/paper_v2_protection_runtime.py",
        "app/services/paper_v2_protection_plan.py",
        "app/opip/canonical/writer.py",
    ):
        source = (APP_ROOT / relative).read_text(encoding="utf-8")
        for verb in forbidden:
            assert not re.search(rf"\b{verb}\s*\(", source), (relative, verb)


def test_h_no_private_kraken_client_is_constructed_on_the_short_path():
    """Only the public client is constructible from the SHORT path."""
    for relative in (
        "app/services/paper_v2_execution.py",
        "app/services/paper_v2_protection_runtime.py",
    ):
        source = (APP_ROOT / relative).read_text(encoding="utf-8")
        assert "KrakenPrivateClient(" not in source
        assert "KrakenPrivateAPIError(" not in source


def test_h_paper_v2_execution_targets_the_registered_instrument(env):
    """The SHORT path reads only public market evidence for its own instrument."""
    clock = _Clock(QUALIFICATION_TIME)
    transport = _BookTransport(clock=clock, bid=WIDE_BID, ask=WIDE_ASK)
    run_paper_v2_opportunity(
        _opportunity("SOLUSD", direction="SHORT"),
        client=env.client,
        kraken_client=KrakenClient(transport=transport),
        settings=_Settings(),
        now=QUALIFICATION_TIME,
        clock=clock,
    )
    assert transport.requests, "public book evidence must have been read"
    assert {symbol for _endpoint, symbol in transport.requests} == {"SOL/USD"}


def test_h_direction_contract_rejects_an_unknown_direction():
    """An unknown direction is refused rather than mapped onto a side."""
    with pytest.raises(ValueError):
        expected_paper_side("FLAT", "ENTRY")
    with pytest.raises(ValueError):
        expected_paper_side("LONG", "SIDEWAYS")
    with pytest.raises(ValueError):
        paper_trade_direction_contract(
            {"schema_version": 1, "direction": "sideways", "direction_contract_version": 2}
        )
