"""B/C-3 production protection/EXIT/reconciliation runtime tests.

``app/services/paper_v2_protection_runtime.py`` is the production path that closes a
filled Paper-v2 position. These tests drive that module end-to-end:

* entry exposure is created by the **real producer** (``run_paper_v2_opportunity``),
  not by hand-built events;
* protection, EXIT execution and reconciliation go through the **real runtime**,
  the **real canonical writer** and its atomic protection-action RPC;
* only the public Kraken Level-1 bytes are faked, because that is the one boundary
  the runtime is allowed to read and a test cannot reach a venue.

Everything else is real: quote-evidence freshness/lineage binding, the frozen
economics, the atomic action transaction, SELL fill conservation, writer-side
threshold and target-fraction validation, and capacity release.

The point of the suite is that a Paper-v2 obligation is only as durable as its
exit. Each scenario below interrupts the lifecycle at a different durable stage and
proves the runtime resumes from canonical evidence alone.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.exchanges.kraken import KrakenClient
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.paper_execution import ProtectionState
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_PROTECTION_STATE_RECORDED,
    PAPER_PROTECTION_TRIGGER_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_QUOTE_EVIDENCE_RECORDED,
)
from app.opip.contracts.serialization import iso_z
from app.services.paper_v2_execution import (
    PaperV2Opportunity,
    build_disposition_id,
    run_paper_v2_opportunity,
)
from app.services.paper_v2_protection_runtime import (
    TRIGGER_PRECEDENCE,
    run_protection_sweep,
)

#: The simulation's fixed qualification instant. Every reading below is relative to
#: it so the frozen freshness window is exercised honestly rather than widened.
NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
QUALIFICATION_TIME = NOW + timedelta(seconds=5)


class _Settings:
    """A settings double whose Paper-v2 values are the canonical ones."""

    opip_paper_v2_mode = "active"
    paper_v2_quote_max_age_seconds = 15
    paper_trade_fee_rate = 0.004
    paper_trade_slippage_bps = 10.0
    paper_v2_tp1_fraction = 0.5
    paper_v2_max_hold_seconds = 86_400
    account_equity = 10_000.0
    paper_trade_starting_equity = 10_000.0


class _Clock:
    """A mutable clock, so a scenario can move time without moving the process."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value = self.value + timedelta(**kwargs)


class _BookTransport:
    """STUB: the public Kraken Level-1 transport, and nothing else.

    It grants no order authority. The runtime still commits and validates real
    quote evidence - freshness, future-dating, crossed-book, instrument lineage -
    so only the venue bytes are faked.

    ``publication_ts`` is derived from the same clock the runtime reads, so a
    scenario that advances time by a day still receives a *fresh* book; otherwise
    the freshness rule, not the behaviour under test, would decide the outcome.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        bid: float = 99.9,
        ask: float = 100.0,
        bid_qty: float = 10.0,
        ask_qty: float = 12.0,
    ) -> None:
        self.requests: list[tuple[str, Any]] = []
        self._clock = clock
        self.bid = float(bid)
        self.ask = float(ask)
        self.bid_qty = float(bid_qty)
        self.ask_qty = float(ask_qty)

    def _published(self) -> str:
        return iso_z(
            self._clock() - timedelta(seconds=1), field_name="publication_ts"
        )

    def request(self, endpoint, params, timeout_seconds):
        self.requests.append((endpoint, params.get("symbol")))
        ts = self._published()
        return {
            "symbol": params.get("symbol"),
            "bids": [{"price": self.bid, "qty": self.bid_qty, "publication_ts": ts}],
            "asks": [{"price": self.ask, "qty": self.ask_qty, "publication_ts": ts}],
        }

    def telemetry_snapshot(self) -> dict:
        return {}


class _Env:
    """One canonical database plus a restart seam.

    ``restart()`` opens a fresh server over the same durable store, so the new
    process shares no in-memory state with the old one - the exact restart model the
    runtime must survive.
    """

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


# ---------------------------------------------------------------------------
# Real production-shaped inputs
# ---------------------------------------------------------------------------


def _symbol_base(production_symbol: str) -> str:
    return production_symbol.removesuffix("USD")


def _native_symbol(production_symbol: str) -> str:
    return f"{_symbol_base(production_symbol)}/USD"


def _candidate_id(production_symbol: str) -> str:
    base = _symbol_base(production_symbol).lower()
    return "OPIPC:" + (base + "0" * 20)[:20]


def _instrument_version(production_symbol: str) -> InstrumentVersion:
    base = _symbol_base(production_symbol)
    return InstrumentVersion(
        venue="kraken",
        base_asset=base,
        quote_currency="USD",
        venue_instrument_id=_native_symbol(production_symbol),
        version=1,
        reference_data_version=f"ref-{base.lower()}",
        observed_at_utc=NOW - timedelta(minutes=5),
    )


def _snapshot_payload(*, symbol: str) -> dict:
    """A real canonical episode snapshot, built by the real production builder."""
    from app.services.canonical_episode_capture import (
        build_canonical_episode_snapshots,
    )

    observation = SimpleNamespace(
        symbol=symbol,
        base_asset=_symbol_base(symbol),
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


def _opportunity(production_symbol: str) -> PaperV2Opportunity:
    """An already-qualified LONG opportunity with approved geometry."""
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )

    snapshot = _snapshot_payload(symbol=production_symbol)
    version = _instrument_version(production_symbol)
    return PaperV2Opportunity(
        candidate_id=_candidate_id(production_symbol),
        episode_id=snapshot["episode_id"],
        cohort_id=snapshot["cohort_id"],
        direction="LONG",
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
        requested_quantity=5.0,
        requested_notional=500.0,
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=90.0,
        target_prices=(110.0, 120.0),
    )


# ---------------------------------------------------------------------------
# Driving the real producer and the real runtime
# ---------------------------------------------------------------------------


def _open_entry(
    env: _Env,
    *,
    clock: _Clock,
    symbol: str = "BTCUSD",
    bid: float = 99.9,
    ask: float = 100.0,
    bid_qty: float = 10.0,
) -> dict:
    """Create a real Paper-v2 entry exposure through the production producer."""
    opportunity = _opportunity(symbol)
    transport = _BookTransport(
        clock=clock, bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=bid_qty
    )
    result = run_paper_v2_opportunity(
        opportunity,
        client=env.client,
        kraken_client=KrakenClient(transport=transport),
        settings=_Settings(),
        now=QUALIFICATION_TIME,
        clock=clock,
    )
    state = env.client.get_paper_v2_execution_state(result.disposition_id)
    assert state.status == "OK", state.error_code or state.status
    return {
        "symbol": symbol,
        "disposition_id": result.disposition_id,
        "paper_trade_id": result.paper_trade_id,
        "reservation_id": result.reservation_id,
        "decision_context_id": state.decision_context_id,
        "protection_plan_id": result.protection_plan_id,
        "entry_order_intent_id": result.entry_order_intent_id,
        "filled_quantity": float(state.filled_quantity),
    }


def _transport(clock: _Clock, **kwargs: Any) -> _BookTransport:
    return _BookTransport(clock=clock, **kwargs)


def _sweep(
    env: _Env,
    *,
    clock: _Clock,
    transport: _BookTransport | None = None,
    client: Any | None = None,
):
    """Run the real protection sweep against a real canonical client."""
    return run_protection_sweep(
        client if client is not None else env.client,
        kraken_client=KrakenClient(transport=transport or _transport(clock)),
        settings=_Settings(),
        execution_clock=clock,
    )


def _items(env: _Env) -> list:
    projection = env.client.get_paper_v2_protection_work()
    assert projection.status == "OK", projection.error_code or projection.status
    return projection.items


def _item(env: _Env, paper_trade_id: str):
    for item in _items(env):
        if item.paper_trade_id == paper_trade_id:
            return item
    raise AssertionError(f"no protection work item for {paper_trade_id}")


def _rows(env: _Env, event_type: str, paper_trade_id: str | None = None) -> list[dict]:
    """Raw canonical rows, for counting durable evidence directly."""
    sql = "SELECT payload_json FROM events WHERE event_type = ?"
    params: list[Any] = [event_type]
    if paper_trade_id is not None:
        sql += " AND json_extract(payload_json, '$.paper_trade_id') = ?"
        params.append(paper_trade_id)
    sql += " ORDER BY history_epoch, local_sequence"
    rows = env.server.writer._conn.execute(sql, params).fetchall()  # noqa: SLF001
    return [json.loads(str(row["payload_json"])) for row in rows]


def _parse(value: str) -> datetime:
    """Parse a canonical EXACT timestamp back to an aware UTC instant."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _count(env: _Env, event_type: str, paper_trade_id: str) -> int:
    return len(_rows(env, event_type, paper_trade_id))


def _state_of(env: _Env, paper_trade_id: str) -> str | None:
    return _item(env, paper_trade_id).protection_state


def _next_scan(clock: _Clock, *, seconds: int = 60) -> None:
    """Advance to the next scheduled scan.

    Real scans are minutes apart, and every scan that reads a public book commits
    quote evidence under a deterministic identity derived from the observed instant.
    Advancing the clock is what makes two successive reads two distinct readings
    rather than a same-instant payload conflict - which the writer is right to
    refuse.
    """
    clock.advance(seconds=seconds)


# ---------------------------------------------------------------------------
# PRT1 / PRT2 - arming a committed plan
# ---------------------------------------------------------------------------


def test_prt1_fresh_fill_activates_the_committed_plan(env):
    """A real entry fill leaves the plan PLANNED; the next sweep arms it."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock)
    trade_id = trade["paper_trade_id"]

    # The producer commits the plan before exposure and never arms it, which is
    # what makes arming a genuine production gap rather than a restatement.
    assert _state_of(env, trade_id) == ProtectionState.PLANNED.value
    assert float(_item(env, trade_id).entry_quantity) > 0

    result = _sweep(env, clock=clock)

    assert result.considered == 1
    assert result.activated == 1
    assert result.new_admissions_allowed is True
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value
    states = _rows(env, PAPER_PROTECTION_STATE_RECORDED, trade_id)
    assert [(row["from_state"], row["to_state"]) for row in states] == [
        ("PLANNED", "ACTIVE")
    ]


def test_prt2_crash_after_fill_before_activation_activates_the_exact_plan(env):
    """A restart in the fill->activation window arms the same plan, not a new one."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock)
    trade_id = trade["paper_trade_id"]
    plan_id = trade["protection_plan_id"]
    assert _state_of(env, trade_id) == ProtectionState.PLANNED.value

    env.restart()

    result = _sweep(env, clock=clock)

    assert result.activated == 1
    # The exact committed plan is armed: no second plan was invented for the gap.
    assert len(_rows(env, PAPER_PROTECTION_PLAN_RECORDED, trade_id)) == 1
    item = _item(env, trade_id)
    assert str(item.protection_plan["protection_plan_id"]) == plan_id
    assert item.protection_state == ProtectionState.ACTIVE.value


# ---------------------------------------------------------------------------
# PRT3 / PRT4 / PRT5 - STOP
# ---------------------------------------------------------------------------


def test_prt3_stop_uncrossed_takes_no_action(env):
    """Bid above the stop leaves the armed position untouched."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock)
    trade_id = trade["paper_trade_id"]
    _sweep(env, clock=clock)

    # 95.0 is above the committed 90.0 stop but below the 110.0 first target.
    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=95.0, ask=95.5))

    assert result.triggered == 0
    assert result.exit_attempted == 0
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 0
    assert _count(env, PAPER_ORDER_INTENT_RECORDED, trade_id) == 1  # ENTRY only
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value
    assert float(_item(env, trade_id).remaining_quantity) == 5.0


def test_prt4_stop_crossed_commits_atomic_trigger_and_exact_exit_intent(env):
    """A crossed stop produces one trigger, one EXIT order and a TRIGGERED state."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock)
    trade_id = trade["paper_trade_id"]
    _sweep(env, clock=clock)

    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert result.triggered == 1
    triggers = _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
    assert len(triggers) == 1
    assert triggers[0]["trigger_type"] == "STOP"
    # The trigger cites the exact executable price it was proven against.
    assert float(triggers[0]["reference_price"]) == 90.0
    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    assert len(exit_orders) == 1
    assert exit_orders[0]["side"] == "SELL"
    # STOP claims the full canonical remaining exposure, never a fraction.
    assert float(exit_orders[0]["requested_quantity"]) == 5.0
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value


def test_prt5_stop_full_exit_reconciles_and_releases_reservation(env):
    """SELL fill -> FLAT -> FINAL_VERIFIED -> reservation released exactly once."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _open_entry(env, clock=clock)
    trade_id = trade["paper_trade_id"]
    _sweep(env, clock=clock)
    _next_scan(clock)
    _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    # Execute the committed EXIT order against a later, still-fresh public book.
    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))
    assert result.exit_attempted == 1
    fills = _rows(env, PAPER_FILL_RECORDED, trade_id)
    exits = [fill for fill in fills if fill["side"] == "SELL"]
    assert len(exits) == 1
    assert float(exits[0]["quantity"]) == 5.0
    # A long exit sells into the bid.
    assert float(exits[0]["price"]) == 90.0

    # Now canonically flat but not yet verified: still reserved, still found.
    item = _item(env, trade_id)
    assert float(item.remaining_quantity) == 0.0
    assert item.final_verified is False

    flat = _sweep(env, clock=clock)
    assert flat.terminalized == 1
    assert _item(env, trade_id).protection_state is not None  # still readable
    recons = _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    assert [row["terminal_reconciliation_state"] for row in recons] == [
        "FLAT_AWAITING_RECONCILIATION"
    ]

    final = _sweep(env, clock=clock)
    assert final.terminalized == 1
    recons = _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    assert [row["terminal_reconciliation_state"] for row in recons] == [
        "FLAT_AWAITING_RECONCILIATION",
        "FINAL_VERIFIED",
    ]
    assert _item(env, trade_id).final_verified is True

    # Exactly once: a further sweep is a no-op and never re-verifies or re-releases.
    again = _sweep(env, clock=clock)
    assert _count(env, PAPER_RECONCILIATION_RECORDED, trade_id) == 2
    assert again.triggered == 0
    assert again.exit_attempted == 0


# ---------------------------------------------------------------------------
# PRT6 - PRT11: TARGET, fractions and residual plan revisions
# ---------------------------------------------------------------------------


def _arm(env: _Env, clock: _Clock) -> dict:
    """Open an entry and arm its plan, the precondition every trigger test needs."""
    trade = _open_entry(env, clock=clock)
    result = _sweep(env, clock=clock)
    assert result.activated == 1, result.details
    assert _state_of(env, trade["paper_trade_id"]) == ProtectionState.ACTIVE.value
    return trade


def _take_tp1(env: _Env, clock: _Clock, trade: dict) -> None:
    """Trigger and fill the first target's configured fraction."""
    _next_scan(clock)
    triggered = _sweep(
        env, clock=clock, transport=_transport(clock, bid=110.0, ask=110.5)
    )
    assert triggered.triggered == 1, triggered.details
    _next_scan(clock)
    executed = _sweep(
        env, clock=clock, transport=_transport(clock, bid=110.0, ask=110.5)
    )
    assert executed.exit_attempted == 1, executed.details
    exits = [
        fill
        for fill in _rows(env, PAPER_FILL_RECORDED, trade["paper_trade_id"])
        if fill["side"] == "SELL"
    ]
    assert len(exits) == 1


def test_prt6_target_uncrossed_takes_no_action(env):
    """A bid below the first target leaves the position armed and untouched."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=109.0, ask=109.5))

    assert result.triggered == 0
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 0
    assert float(_item(env, trade_id).remaining_quantity) == 5.0


def test_prt7_tp1_crossed_exits_the_configured_fraction(env):
    """TP1 exits 0.5 x 5.0 = 2.5, leaving 2.5 exposed."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=110.0, ask=110.5))

    assert result.triggered == 1
    triggers = _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
    assert [row["trigger_type"] for row in triggers] == ["TARGET"]
    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    assert float(exit_orders[0]["requested_quantity"]) == 2.5


def test_prt8_tp1_cannot_full_close_a_smaller_configured_fraction(env):
    """The size of a target exit is bounded by its own fraction, not the position."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _take_tp1(env, clock, trade)

    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    # 2.5, not the full 5.0 exposure: a TP1 crossing must never close the trade.
    assert [float(row["requested_quantity"]) for row in exit_orders] == [2.5]
    assert float(_item(env, trade_id).remaining_quantity) == 2.5


def test_prt9_partial_target_exit_rearms_an_immutable_residual_plan(env):
    """The residual gets a new plan revision; the original plan is never altered."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]
    original_plan = dict(_item(env, trade_id).protection_plan)
    original_expiry = (
        _parse(original_plan["plan_time"]["occurred_at"])
        + timedelta(seconds=int(original_plan["max_hold_seconds"]))
    )

    _take_tp1(env, clock, trade)
    _next_scan(clock)
    result = _sweep(env, clock=clock)

    assert result.partially_exited == 1, result.details
    plans = _rows(env, PAPER_PROTECTION_PLAN_RECORDED, trade_id)
    # The original plan is retained verbatim as immutable history.
    assert plans[0] == original_plan
    assert [int(plan["plan_seq"]) for plan in plans] == [0, 1]
    residual = plans[1]
    # Stop, target identity/price and fraction are carried over, so the residual
    # fraction keeps its meaning relative to the ORIGINAL entry quantity.
    assert float(residual["stop_price"]) == float(original_plan["stop_price"])
    assert [(t["target_id"], t["price"], t["fraction"]) for t in residual["targets"]] == [
        (t["target_id"], t["price"], t["fraction"])
        for t in original_plan["targets"]
    ][1:]
    # The max-hold deadline is preserved, never restarted: the residual can never
    # be held longer than the position it replaced.
    residual_expiry = _parse(residual["plan_time"]["occurred_at"]) + timedelta(
        seconds=int(residual["max_hold_seconds"])
    )
    assert residual_expiry <= original_expiry
    assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value

    # The consumed target cannot fire again: the same TRIGGERED plan is retired.
    assert not any(
        row["trigger_type"] == "TARGET" and row["protection_plan_id"] == original_plan["protection_plan_id"]
        for row in _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)[1:]
    )


def test_prt10_stop_closes_the_residual_after_a_partial_target(env):
    """A later stop closes exactly the remaining exposure and verifies it."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]
    _take_tp1(env, clock, trade)
    _next_scan(clock)
    assert _sweep(env, clock=clock).partially_exited == 1

    _next_scan(clock)
    triggered = _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    )
    assert triggered.triggered == 1, triggered.details
    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    # STOP claims the full remaining exposure: 5.0 - 2.5.
    assert float(exit_orders[-1]["requested_quantity"]) == 2.5

    _next_scan(clock)
    executed = _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    )
    assert executed.exit_attempted == 1
    assert float(_item(env, trade_id).remaining_quantity) == 0.0

    _sweep(env, clock=clock)
    _sweep(env, clock=clock)
    assert [
        row["terminal_reconciliation_state"]
        for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    ] == ["FLAT_AWAITING_RECONCILIATION", "FINAL_VERIFIED"]
    assert _item(env, trade_id).final_verified is True


def test_prt11_tp2_closes_the_residual_after_a_partial_target(env):
    """The residual revision's own target closes the remainder."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]
    _take_tp1(env, clock, trade)
    _next_scan(clock)
    assert _sweep(env, clock=clock).partially_exited == 1

    _next_scan(clock)
    triggered = _sweep(
        env, clock=clock, transport=_transport(clock, bid=120.0, ask=120.5)
    )
    assert triggered.triggered == 1, triggered.details
    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    # TP2's fraction (0.5) is still measured against the ORIGINAL entry, so it
    # closes exactly the residual rather than half of it.
    assert float(exit_orders[-1]["requested_quantity"]) == 2.5

    _next_scan(clock)
    executed = _sweep(
        env, clock=clock, transport=_transport(clock, bid=120.0, ask=120.5)
    )
    assert executed.exit_attempted == 1
    _sweep(env, clock=clock)
    _sweep(env, clock=clock)
    assert _item(env, trade_id).final_verified is True


# ---------------------------------------------------------------------------
# PRT12 - PRT14: TIME
# ---------------------------------------------------------------------------


def _expiry(env: _Env, trade_id: str) -> datetime:
    plan = _item(env, trade_id).protection_plan
    return _parse(plan["plan_time"]["occurred_at"]) + timedelta(
        seconds=int(plan["max_hold_seconds"])
    )


def test_prt12_time_before_expiry_takes_no_action(env):
    """A long-held position is not closed a second before its committed expiry."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]
    expiry = _expiry(env, trade_id)

    # Flat price: neither stop nor target is crossed, so only TIME could fire.
    clock.value = expiry - timedelta(seconds=1)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=100.0, ask=100.5))

    assert result.triggered == 0
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 0
    assert float(_item(env, trade_id).remaining_quantity) == 5.0


def test_prt13_time_at_expiry_closes_the_full_remaining_exposure(env):
    """Firing exactly at expiry is allowed and closes everything still exposed."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    clock.value = _expiry(env, trade_id)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=100.0, ask=100.5))

    assert result.triggered == 1, result.details
    triggers = _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
    assert [row["trigger_type"] for row in triggers] == ["TIME"]
    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    assert float(exit_orders[0]["requested_quantity"]) == 5.0

    _next_scan(clock)
    executed = _sweep(env, clock=clock, transport=_transport(clock, bid=100.0, ask=100.5))
    assert executed.exit_attempted == 1
    assert float(_item(env, trade_id).remaining_quantity) == 0.0


def test_prt14_time_after_partial_target_uses_the_original_expiry(env):
    """A partial target exit must not extend how long the residual is held."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]
    original_expiry = _expiry(env, trade_id)

    _take_tp1(env, clock, trade)
    _next_scan(clock)
    assert _sweep(env, clock=clock).partially_exited == 1

    # Well before the ORIGINAL expiry the residual must stay open...
    clock.value = original_expiry - timedelta(hours=1)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=100.0, ask=100.5))
    assert result.triggered == 0
    assert [
        row["trigger_type"]
        for row in _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
    ] == ["TARGET"]

    # ...and at the original expiry it must fire, on the residual revision.
    clock.value = original_expiry
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=100.0, ask=100.5))
    assert result.triggered == 1, result.details
    residual_plan_id = _item(env, trade_id).protection_plan["protection_plan_id"]
    time_triggers = [
        row
        for row in _rows(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)
        if row["trigger_type"] == "TIME"
    ]
    assert len(time_triggers) == 1
    assert time_triggers[0]["protection_plan_id"] == residual_plan_id


# ---------------------------------------------------------------------------
# PRT15 - PRT16: displayed depth
# ---------------------------------------------------------------------------


def test_prt15_insufficient_bid_depth_after_trigger_fabricates_nothing(env):
    """There is no depth model, so a thin book yields no fill and no resize."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    assert _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    ).triggered == 1

    # 5.0 is requested but only 2.0 is displayed: the order stays open.
    _next_scan(clock)
    result = _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5, bid_qty=2.0)
    )

    assert result.exit_attempted == 0
    assert result.retryable == 1
    assert result.new_admissions_allowed is False
    assert _count(env, PAPER_EXECUTION_ATTEMPT_RECORDED, trade_id) == 1  # ENTRY only
    assert [
        fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"
    ] == []
    # The trigger stays historically true and the EXIT order stays pending.
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    assert float(_item(env, trade_id).remaining_quantity) == 5.0


def test_prt16_later_sufficient_depth_fills_the_existing_exit_order(env):
    """The pending EXIT order is completed later, not replaced."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))
    _next_scan(clock)
    _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5, bid_qty=2.0)
    )
    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    assert len(exit_orders) == 1

    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert result.exit_attempted == 1
    # Still exactly one EXIT order and one SELL fill: the same order completed.
    exit_orders = [
        row
        for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "EXIT"
    ]
    assert len(exit_orders) == 1
    assert len(
        [fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"]
    ) == 1


# ---------------------------------------------------------------------------
# PRT17 - PRT19: lost ACKs
# ---------------------------------------------------------------------------


class _AckLossClient:
    """Commit through the real client, then lose the answer.

    This is the exact ACK-loss model: the durable write succeeded, the caller never
    learned it. Delegating everything else keeps the rest of the runtime real.
    """

    def __init__(self, inner: Any, *, event_type: str | None = None, action: bool = False):
        self._inner = inner
        self._event_type = event_type
        self._action = action

    def submit(self, intent):
        ack = self._inner.submit(intent)
        if self._event_type is not None and intent.event_type == self._event_type:
            raise RuntimeError("commit acknowledged but the answer was lost")
        return ack

    def trigger_paper_protection_action(self, request):
        ack = self._inner.trigger_paper_protection_action(request)
        if self._action:
            raise RuntimeError("action committed but the answer was lost")
        return ack

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _CrashBeforeClient:
    """Die immediately before one event is written.

    Where ``_AckLossClient`` models "written but unacknowledged", this models the
    other durable interruption: the process stops between two stages, so the later
    stage never reached the store. Everything else is delegated unchanged.
    """

    def __init__(self, inner: Any, *, event_type: str) -> None:
        self._inner = inner
        self._event_type = event_type

    def submit(self, intent):
        if intent.event_type == self._event_type:
            raise RuntimeError("process died before the stage was committed")
        return self._inner.submit(intent)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_prt17_lost_action_ack_creates_no_duplicate_trigger_or_order(env):
    """An atomic action whose answer was lost is discovered, not repeated."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    lost = _sweep(
        env,
        clock=clock,
        transport=_transport(clock, bid=90.0, ask=90.5),
        client=_AckLossClient(env.client, action=True),
    )
    assert lost.retryable == 1 and lost.triggered == 0

    # The action did commit, so the next scan resumes the committed EXIT order.
    _next_scan(clock)
    recovered = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 1
    assert len(
        [
            row
            for row in _rows(env, PAPER_ORDER_INTENT_RECORDED, trade_id)
            if row["intent_role"] == "EXIT"
        ]
    ) == 1
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    assert recovered.exit_attempted == 1


def test_prt18_lost_attempt_ack_creates_no_duplicate_attempt(env):
    """An EXIT attempt whose answer was lost is reused, not resubmitted."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    _next_scan(clock)
    lost = _sweep(
        env,
        clock=clock,
        transport=_transport(clock, bid=90.0, ask=90.5),
        client=_AckLossClient(env.client, event_type=PAPER_EXECUTION_ATTEMPT_RECORDED),
    )
    assert lost.retryable == 1

    _next_scan(clock)
    _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    attempts = _rows(env, PAPER_EXECUTION_ATTEMPT_RECORDED, trade_id)
    # One ENTRY attempt and one EXIT attempt: no duplicate was written.
    assert len(attempts) == 2
    assert len(
        [fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"]
    ) == 1


def test_prt19_lost_fill_ack_creates_no_duplicate_fill(env):
    """A SELL fill whose answer was lost is not written twice."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))
    _next_scan(clock)
    lost = _sweep(
        env,
        clock=clock,
        transport=_transport(clock, bid=90.0, ask=90.5),
        client=_AckLossClient(env.client, event_type=PAPER_FILL_RECORDED),
    )
    assert lost.retryable == 1

    _next_scan(clock)
    _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    exits = [
        fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"
    ]
    assert len(exits) == 1
    assert float(_item(env, trade_id).remaining_quantity) == 0.0


# ---------------------------------------------------------------------------
# PRT20 - PRT23: crash recovery and terminal idempotence
# ---------------------------------------------------------------------------


def _drive_to_flat(env: _Env, clock: _Clock) -> dict:
    """Open, arm, stop out and fill, leaving the trade flat but unverified."""
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)
    assert _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)).triggered == 1
    _next_scan(clock)
    assert _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    ).exit_attempted == 1
    assert float(_item(env, trade_id).remaining_quantity) == 0.0
    return trade


def test_prt20_crash_after_the_flat_fill_completes_reconciliation_on_restart(env):
    """A flat trade is absent from active exposure but must still be reconciled."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _drive_to_flat(env, clock)
    trade_id = trade["paper_trade_id"]

    env.restart()

    # The trade is no longer "active", yet the protection projection still finds it.
    active = env.client.get_paper_v2_active_exposures()
    assert all(
        exposure.paper_trade_id != trade_id for exposure in active.exposures
    )
    assert any(item.paper_trade_id == trade_id for item in _items(env))

    _sweep(env, clock=clock)
    _sweep(env, clock=clock)
    assert [
        row["terminal_reconciliation_state"]
        for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    ] == ["FLAT_AWAITING_RECONCILIATION", "FINAL_VERIFIED"]


def test_prt21_crash_after_flat_awaiting_verifies_exactly_once(env):
    """A restart between the two reconciliation states finishes the second only."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _drive_to_flat(env, clock)
    trade_id = trade["paper_trade_id"]

    _sweep(env, clock=clock)
    assert [
        row["terminal_reconciliation_state"]
        for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    ] == ["FLAT_AWAITING_RECONCILIATION"]

    env.restart()
    _sweep(env, clock=clock)
    _sweep(env, clock=clock)

    assert [
        row["terminal_reconciliation_state"]
        for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    ] == ["FLAT_AWAITING_RECONCILIATION", "FINAL_VERIFIED"]


def test_prt22_final_verified_releases_capacity_exactly_once(env):
    """Only FINAL_VERIFIED releases, and a further sweep never releases again."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _drive_to_flat(env, clock)
    trade_id = trade["paper_trade_id"]

    # Still reserved while merely flat.
    assert _item(env, trade_id).final_verified is False
    _sweep(env, clock=clock)
    assert _item(env, trade_id).final_verified is False

    _sweep(env, clock=clock)
    assert _item(env, trade_id).final_verified is True

    before = _count(env, PAPER_RECONCILIATION_RECORDED, trade_id)
    for _ in range(3):
        _sweep(env, clock=clock)
    assert _count(env, PAPER_RECONCILIATION_RECORDED, trade_id) == before


def test_prt23_restart_after_terminal_reads_no_market_and_writes_nothing(env):
    """A fully verified trade is inert across restarts."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _drive_to_flat(env, clock)
    trade_id = trade["paper_trade_id"]
    _sweep(env, clock=clock)
    _sweep(env, clock=clock)
    assert _item(env, trade_id).final_verified is True

    env.restart()
    transport = _transport(clock, bid=90.0, ask=90.5)
    result = _sweep(env, clock=clock, transport=transport)

    assert result.triggered == 0
    assert result.exit_attempted == 0
    assert result.terminalized == 0
    # No market read at all: a terminal trade justifies no evidence request.
    assert transport.requests == []
    assert _count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id) == 1
    assert len(
        [fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"]
    ) == 1


# ---------------------------------------------------------------------------
# PRT24 - PRT25: causal chronology
# ---------------------------------------------------------------------------


def test_prt24_clock_regression_before_the_exit_attempt_fabricates_no_chronology(env):
    """A clock behind the trigger cannot produce a chronologically false attempt."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    assert _sweep(
        env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5)
    ).triggered == 1
    committed_at = clock.value

    # The process clock regresses behind the trigger it must follow.
    clock.value = committed_at - timedelta(minutes=30)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    assert result.exit_attempted == 0
    assert result.retryable == 1
    assert _count(env, PAPER_EXECUTION_ATTEMPT_RECORDED, trade_id) == 1  # ENTRY only
    # Exposure and reservation are retained rather than released.
    assert float(_item(env, trade_id).remaining_quantity) == 5.0
    assert _item(env, trade_id).final_verified is False


def test_prt25_clock_regression_after_an_accepted_attempt_does_not_release(env):
    """An accepted EXIT attempt with a regressed clock retries instead of releasing."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))

    # The process dies between the committed attempt and its fill.
    _next_scan(clock)
    accepted_at = clock.value
    crashed = _sweep(
        env,
        clock=clock,
        transport=_transport(clock, bid=90.0, ask=90.5),
        client=_CrashBeforeClient(env.client, event_type=PAPER_FILL_RECORDED),
    )
    assert crashed.retryable == 1
    assert _count(env, PAPER_EXECUTION_ATTEMPT_RECORDED, trade_id) == 2  # ENTRY + EXIT
    assert [
        fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"
    ] == []

    # A clock behind the accepted attempt must not produce a fill or a release.
    clock.value = accepted_at - timedelta(minutes=1)
    regressed = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))
    assert regressed.filled == 0
    assert regressed.retryable == 1
    assert [
        fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"
    ] == []
    assert _item(env, trade_id).final_verified is False

    # A later, correctly ordered scan completes the same committed attempt.
    clock.value = accepted_at + timedelta(minutes=5)
    completed = _sweep(env, clock=clock, transport=_transport(clock, bid=90.0, ask=90.5))
    assert completed.filled == 1
    exits = [
        fill for fill in _rows(env, PAPER_FILL_RECORDED, trade_id) if fill["side"] == "SELL"
    ]
    assert len(exits) == 1
    assert _count(env, PAPER_EXECUTION_ATTEMPT_RECORDED, trade_id) == 2
    assert float(_item(env, trade_id).remaining_quantity) == 0.0


# ---------------------------------------------------------------------------
# Threshold evidence (writer validation as defence in depth)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bid", "should_trigger"),
    [
        (90.5, False),  # strictly above the stop: not crossed
        (90.0, True),   # exactly at the stop: crossed
        (89.0, True),   # below the stop: crossed
    ],
)
def test_stop_threshold_evidence(env, bid, should_trigger):
    """STOP fires at or below its committed price, never above it."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=bid, ask=bid + 0.5))

    assert (result.triggered == 1) is should_trigger
    assert bool(_count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)) is should_trigger


@pytest.mark.parametrize(
    ("bid", "should_trigger"),
    [
        (109.0, False),  # strictly below the target: not crossed
        (110.0, True),   # exactly at the target: crossed
        (111.0, True),   # above the target: crossed
    ],
)
def test_target_threshold_evidence(env, bid, should_trigger):
    """TARGET fires at or above its eligible price, never below it."""
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    _next_scan(clock)
    result = _sweep(env, clock=clock, transport=_transport(clock, bid=bid, ask=bid + 0.5))

    assert (result.triggered == 1) is should_trigger
    assert bool(_count(env, PAPER_PROTECTION_TRIGGER_RECORDED, trade_id)) is should_trigger


def test_trigger_precedence_is_explicit_and_conservative():
    """Precedence is a fixed tuple, so it can never be decided by iteration order."""
    assert TRIGGER_PRECEDENCE == ("STOP", "TARGET", "TIME")


# ---------------------------------------------------------------------------
# Existing exposure outlives the authority that created it
# ---------------------------------------------------------------------------


def _real_transport(*, bid: float, ask: float) -> _BookTransport:
    """A book whose publication instant tracks the real clock.

    These tests drive the production scan wiring, which supplies its own execution
    clock, so the faked venue bytes must be published relative to real time or the
    frozen freshness rule - not the behaviour under test - would decide the outcome.
    """
    return _BookTransport(
        clock=lambda: datetime.now(timezone.utc), bid=bid, ask=ask
    )


def test_existing_exposure_stays_protected_after_the_mode_is_turned_off(env):
    """Switching the mode off must not strand a position Paper v2 already owns.

    ``opip_paper_v2_mode`` governs who may open a NEW position. It is not a licence
    to abandon an existing obligation, so the production sweep invoked by the scan
    must still protect and close exposure while the mode reads ``off`` - and must
    still grant no new Paper-v2 entry authority.
    """
    from app.jobs import scan_opportunities
    from app.services.paper_v2_scan_router import (
        set_kraken_client_for_tests,
        set_writer_client_for_tests,
    )

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    off = _Settings()
    off.opip_paper_v2_mode = "off"

    set_writer_client_for_tests(env.client)
    set_kraken_client_for_tests(KrakenClient(transport=_real_transport(bid=90.0, ask=90.5)))
    try:
        # The mode is off, so Paper v2 owns no NEW entry authority at all.
        authority = scan_opportunities._resolve_paper_authority(off)
        assert authority.paper_v2_routing is False

        # Yet the production sweep still protects the committed obligation.
        result = scan_opportunities._run_paper_v2_protection_sweep(off, requested=False)
        assert result is not None, "protection sweep must be reachable while off"
        assert result.triggered == 1, result.details
        assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value

        # And it can complete the closure it started.
        assert scan_opportunities._run_paper_v2_protection_sweep(
            off, requested=False
        ).exit_attempted == 1
        for _ in range(2):
            scan_opportunities._run_paper_v2_protection_sweep(off, requested=False)
        assert [
            row["terminal_reconciliation_state"]
            for row in _rows(env, PAPER_RECONCILIATION_RECORDED, trade_id)
        ] == ["FLAT_AWAITING_RECONCILIATION", "FINAL_VERIFIED"]
        assert _item(env, trade_id).final_verified is True
    finally:
        set_writer_client_for_tests(None)
        set_kraken_client_for_tests(None)


def test_existing_exposure_stays_protected_when_cutover_readiness_is_unavailable(env):
    """An unreadable drain verdict must not disable lifecycle management.

    Cutover readiness gates new admissions. When the legacy drain verdict cannot be
    read the scan grants no new entry in either authority, but the Paper-v2 position
    that already exists is still canonically owned and must keep being protected.
    """
    from app.jobs import scan_opportunities
    from app.services.paper_v2_scan_router import (
        set_kraken_client_for_tests,
        set_writer_client_for_tests,
    )

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]

    requested = _Settings()
    requested.opip_paper_v2_mode = "active"

    set_writer_client_for_tests(env.client)
    set_kraken_client_for_tests(KrakenClient(transport=_real_transport(bid=90.0, ask=90.5)))
    try:
        # An unreadable drain verdict resolves to UNAVAILABLE: no new entries.
        authority = scan_opportunities.PaperAuthority(
            requested=True,
            granted=scan_opportunities.AUTHORITY_PAPER_V2_UNAVAILABLE,
            reason="legacy drain unreadable",
        )
        assert authority.paper_v2_routing is False
        assert authority.legacy_new_entry_allowed is False

        result = scan_opportunities._run_paper_v2_protection_sweep(
            requested, requested=False
        )
        assert result is not None
        assert result.triggered == 1, result.details
        assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    finally:
        set_writer_client_for_tests(None)
        set_kraken_client_for_tests(None)


def test_per_trade_protection_reads_use_the_index_not_a_full_history_scan(env):
    """Canonical reads must be structurally bounded as history grows.

    The projection is only as safe as its access path: a per-trade read that fell
    back to a full scan would degrade with total canonical history rather than with
    the live position count. ``EXPLAIN QUERY PLAN`` proves the access path uses the
    additive ``paper_trade_id`` expression index instead of scanning the events
    table. Asserting the plan (rather than timing it) makes the claim structural and
    independent of machine speed.
    """
    plan = env.server.writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "EXPLAIN QUERY PLAN SELECT payload_json FROM events "
        "WHERE event_type = ? "
        "AND json_extract(payload_json, '$.paper_trade_id') = ?",
        (PAPER_FILL_RECORDED, "PTV2:nonexistent"),
    ).fetchall()
    detail = " ".join(str(row["detail"]) for row in plan)

    assert "idx_events_paper_trade" in detail, detail
    assert "SCAN events" not in detail, detail


def test_protection_work_is_correct_alongside_a_large_unrelated_history(env):
    """Unrelated canonical history must not corrupt or hide protection work.

    Appends a large volume of real canonical evidence (a distinct committed public
    reading per scan, with the clock advanced so each is a genuinely new record),
    then proves the projection still reports exactly the trades it should, with the
    original trade's committed lifecycle intact.
    """
    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm(env, clock)
    trade_id = trade["paper_trade_id"]
    before = _item(env, trade_id)

    # A second, unrelated trade, explicitly *not* armed, so the history below is
    # evidence about a trade whose lifecycle is not the one under assertion.
    other = _open_entry(env, clock=clock, symbol="ETHUSD")

    # A benign book: 100.0 crosses neither the 90.0 stop nor the 110.0 first target
    # for either trade, so this accumulates market evidence without changing any
    # lifecycle state. The clock advances so every reading is a distinct commit.
    quotes_before = _count(env, PAPER_QUOTE_EVIDENCE_RECORDED, trade_id)
    for _ in range(60):
        _next_scan(clock, seconds=30)
        result = _sweep(env, clock=clock, transport=_transport(clock, bid=100.0, ask=100.5))
        assert result.triggered == 0, result.details
        assert result.new_admissions_allowed is True

    # Non-vacuity: the unrelated history really did grow.
    total_quotes = len(_rows(env, PAPER_QUOTE_EVIDENCE_RECORDED))
    assert total_quotes >= 60

    items = _items(env)
    assert sorted(item.paper_trade_id for item in items) == sorted(
        [trade_id, other["paper_trade_id"]]
    )
    after = _item(env, trade_id)
    assert after.paper_trade_id == before.paper_trade_id
    assert after.plan_seq == before.plan_seq
    assert after.entry_quantity == before.entry_quantity
    assert after.remaining_quantity == before.remaining_quantity
    assert after.protection_state == ProtectionState.ACTIVE.value
    assert _count(env, PAPER_QUOTE_EVIDENCE_RECORDED, trade_id) >= quotes_before


def test_an_unavailable_protection_sweep_withholds_new_authority_and_never_falls_back(env):
    """An unreadable store must not be reported as healthy, nor fall back to legacy."""
    from app.jobs import scan_opportunities
    from app.services.paper_v2_scan_router import set_writer_client_for_tests

    class _ExplodingClient:
        def get_paper_v2_protection_work(self):
            raise RuntimeError("canonical store unreadable")

    set_writer_client_for_tests(_ExplodingClient())
    try:
        result = scan_opportunities._run_paper_v2_protection_sweep(
            _Settings(), requested=True
        )
    finally:
        set_writer_client_for_tests(None)

    # The unreadable store is reported as unavailable and explicitly *not* healthy,
    # which is what withholds new Paper-v2 admissions for the scan. It is never
    # silently treated as "nothing to protect", and no legacy authority is reached.
    assert result is not None
    assert result.unavailable == 1
    assert result.new_admissions_allowed is False

