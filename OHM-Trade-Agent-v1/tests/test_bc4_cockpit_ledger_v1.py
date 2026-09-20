"""B/C-4B reconciled Paper-v2 economic ledger tests.

Two layers of coverage:

* **Derivation** tests drive :func:`app.opip.cockpit.ledger.build_trade_row` with
  committed-looking ledger facts. These are the rules that decide what the cockpit
  is allowed to claim - definitive vs indicative economics, separate outcome
  dimensions, refusal to invent point precision from bounded evidence.
* **Integration** tests drive a real Paper-v2 entry through the real producer and
  the real canonical writer, then read the ledger through the real RPC, proving the
  wiring works on genuine canonical evidence rather than only on constructed
  inputs.

The recurring theme is that the ledger must never overstate what it knows: an
unverified trade is not closed, a bounded timestamp is not an instant, and an
unreadable store is not an empty ledger.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.models import (
    PaperV2Ledger,
    PaperV2LedgerEntry,
)
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.cockpit import (
    COCKPIT_LEDGER_PROJECTION_VERSION,
    EconomicResult,
    EconomicsSource,
    ExecutionResult,
    ExitMechanism,
    LifecycleStatus,
    PaperLedger,
    build_ledger,
    build_trade_row,
    read_paper_ledger,
)
from app.opip.cockpit.ledger import latency_interval, temporal_interval, temporal_point
from app.opip.cockpit.trust import Completeness, Freshness, Uncertainty

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _exact(offset_seconds: int = 0) -> dict[str, Any]:
    moment = NOW + timedelta(seconds=offset_seconds)
    return {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": moment.isoformat().replace("+00:00", "Z"),
    }


def _bounded(start: int, end: int) -> dict[str, Any]:
    return {
        "precision": "BOUNDED",
        "basis": "LOCALLY_OBSERVED",
        "window_start": (NOW + timedelta(seconds=start))
        .isoformat()
        .replace("+00:00", "Z"),
        "window_end": (NOW + timedelta(seconds=end))
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _fill(
    *,
    side: str,
    quantity: float,
    price: float,
    fee: float = 0.4,
    spread: float = 0.2,
    slippage: float = 0.1,
    other: float = 0.0,
    offset: int = 0,
) -> dict[str, Any]:
    return {
        "side": side,
        "quantity": quantity,
        "price": price,
        "fee_cost": fee,
        "spread_cost": spread,
        "slippage_cost": slippage,
        "other_supported_cost": other,
        "fill_time": _exact(offset),
        "economic_model_version": "opip-paper-economics-v2",
    }


def _entry(**overrides: Any) -> PaperV2LedgerEntry:
    """A closed, fully verified long trade: 5 in at 100, 5 out at 110."""
    entry_fill = _fill(side="BUY", quantity=5.0, price=100.0, offset=0)
    exit_fill = _fill(side="SELL", quantity=5.0, price=110.0, offset=600)
    reconciliation = {
        "reconciliation_seq": 1,
        "terminal_reconciliation_state": "FINAL_VERIFIED",
        "position_state": "FLAT",
        "realized_gross_pnl": 50.0,
        "recorded_execution_costs": 1.4,
        "realized_net_pnl": 48.6,
        "filled_entry_quantity": 5.0,
        "filled_exit_quantity": 5.0,
        "remaining_quantity": 0.0,
    }
    base = dict(
        paper_trade_id="PTV2:" + "a" * 64,
        disposition_id="DISP:1",
        decision_context_id="CTX:1",
        reservation_id="RES:1",
        native_symbol="BTC/USD",
        quote_currency="USD",
        policy_version="opip-gate-v1",
        policy_fingerprint="fp-1",
        entry_quantity=5.0,
        exited_quantity=5.0,
        remaining_quantity=0.0,
        gross_pnl=50.0,
        fee_cost=0.8,
        spread_cost=0.4,
        slippage_cost=0.2,
        other_cost=0.0,
        execution_costs=1.4,
        reserved_capital=500.0,
        protection_plan={
            "protection_plan_id": "PPLAN:1",
            "plan_seq": 0,
            "stop_price": 90.0,
            "max_hold_seconds": 3600,
            "targets": [{"target_id": "TP1", "price": 110.0, "fraction": 0.5}],
        },
        protection_state="TRIGGERED",
        plan_seq=0,
        trigger_types=("TARGET",),
        entry_order_intent={"requested_quantity": 5.0, "intent_role": "ENTRY"},
        entry_fills=(entry_fill,),
        exit_fills=(exit_fill,),
        first_entry_fill_time=_exact(0),
        last_exit_fill_time=_exact(600),
        entry_intent_time=_exact(-30),
        disposition_time=_exact(-60),
        latest_reconciliation=reconciliation,
        final_verified=True,
        event_ids=("e1", "e2", "e3"),
    )
    base.update(overrides)
    return PaperV2LedgerEntry(**base)


# ---------------------------------------------------------------------------
# Definitive vs indicative economics
# ---------------------------------------------------------------------------


def test_final_verified_trade_is_settled_and_definitive():
    """Only canonical FINAL_VERIFIED makes a row a settled result."""
    row = build_trade_row(_entry())

    assert row.lifecycle_status is LifecycleStatus.CLOSED
    assert row.net_pnl_definitive is True
    assert row.is_settled is True
    assert row.economics_source is EconomicsSource.CANONICAL_RECONCILIATION
    assert row.net_pnl == pytest.approx(48.6)
    assert row.gross_pnl == pytest.approx(50.0)
    assert row.trust.completeness is Completeness.COMPLETE
    assert row.trust.is_healthy is True


def test_flat_but_unverified_trade_is_not_reported_as_closed():
    """A filled-in-then-filled-out trade with no verification is NOT closed.

    This is the central correctness rule: exposure is gone, but no canonical
    evidence proves the economics, so calling it CLOSED would claim a settled
    result the store cannot support.
    """
    row = build_trade_row(
        _entry(latest_reconciliation=None, final_verified=False)
    )

    assert row.lifecycle_status is LifecycleStatus.UNRESOLVED
    assert row.net_pnl_definitive is False
    assert row.economic_result is EconomicResult.UNRESOLVED
    assert row.trust.completeness is Completeness.INCOMPLETE
    assert row.trust.uncertainty is Uncertainty.INSUFFICIENT_EVIDENCE
    assert "ECONOMICS_NOT_FINAL_VERIFIED" in row.trust.reasons
    # The numbers are still shown, but as derived rather than authoritative.
    assert row.economics_source is EconomicsSource.DERIVED_FROM_FILLS


def test_flat_awaiting_reconciliation_is_incomplete_not_verified():
    """FLAT_AWAITING_RECONCILIATION carries totals but is not yet verified."""
    pending = dict(_entry().latest_reconciliation or {})
    pending["terminal_reconciliation_state"] = "FLAT_AWAITING_RECONCILIATION"

    row = build_trade_row(
        _entry(latest_reconciliation=pending, final_verified=False)
    )

    assert row.terminal_reconciliation_state == "FLAT_AWAITING_RECONCILIATION"
    assert row.net_pnl_definitive is False
    assert row.economic_result is EconomicResult.UNRESOLVED


def test_unresolved_evidence_reconciliation_never_releases_as_verified():
    """`UNRESOLVED_EVIDENCE` must not be presented as a settled result."""
    unresolved = dict(_entry().latest_reconciliation or {})
    unresolved["terminal_reconciliation_state"] = "UNRESOLVED_EVIDENCE"

    row = build_trade_row(
        _entry(latest_reconciliation=unresolved, final_verified=False)
    )
    assert row.net_pnl_definitive is False
    assert row.economic_result is EconomicResult.UNRESOLVED


def test_derived_economics_reproduce_the_canonical_relationship():
    """Without a reconciliation, net is derived as gross - recorded costs."""
    entries = (
        _fill(side="BUY", quantity=5.0, price=100.0, offset=0),
    )
    exits = (_fill(side="SELL", quantity=5.0, price=90.0, offset=600),)
    row = build_trade_row(
        _entry(
            latest_reconciliation=None,
            final_verified=False,
            entry_fills=entries,
            exit_fills=exits,
        )
    )
    # 5*(90) - 5*(100) = -50 gross; costs 2 x 0.7 = 1.4
    assert row.gross_pnl == pytest.approx(-50.0)
    assert row.execution_costs == pytest.approx(1.4)
    assert row.net_pnl == pytest.approx(-51.4)


def test_economic_result_uses_only_verified_net_and_respects_tolerance():
    """Win/loss/breakeven derive from the canonical net, only once verified."""
    win = build_trade_row(_entry())
    assert win.economic_result is EconomicResult.WIN

    breakeven_recon = dict(_entry().latest_reconciliation or {})
    breakeven_recon["realized_net_pnl"] = 0.0
    breakeven = build_trade_row(_entry(latest_reconciliation=breakeven_recon))
    assert breakeven.economic_result is EconomicResult.BREAKEVEN

    loss_recon = dict(_entry().latest_reconciliation or {})
    loss_recon["realized_net_pnl"] = -12.0
    loss = build_trade_row(_entry(latest_reconciliation=loss_recon))
    assert loss.economic_result is EconomicResult.LOSS


def test_zero_fill_terminal_trade_is_closed_with_no_position():
    """A canonically terminal zero-fill trade is a settled no-fill outcome."""
    recon = {
        "reconciliation_seq": 0,
        "terminal_reconciliation_state": "FINAL_VERIFIED",
        "position_state": "NO_POSITION",
        "realized_gross_pnl": 0.0,
        "recorded_execution_costs": 0.0,
        "realized_net_pnl": 0.0,
        "remaining_quantity": 0.0,
    }
    row = build_trade_row(
        _entry(
            entry_quantity=0.0,
            exited_quantity=0.0,
            remaining_quantity=0.0,
            gross_pnl=0.0,
            execution_costs=0.0,
            entry_fills=(),
            exit_fills=(),
            first_entry_fill_time=None,
            last_exit_fill_time=None,
            latest_reconciliation=recon,
            final_verified=True,
        )
    )
    assert row.lifecycle_status is LifecycleStatus.CLOSED
    assert row.execution_result is ExecutionResult.NO_FILL
    assert row.net_pnl_definitive is True


# ---------------------------------------------------------------------------
# Outcome dimensions stay independent
# ---------------------------------------------------------------------------


def test_partial_fill_is_reported_independently_of_economics():
    """A partial fill is an execution fact, not an economic outcome."""
    row = build_trade_row(
        _entry(
            entry_quantity=2.0,
            exited_quantity=0.0,
            remaining_quantity=2.0,
            final_verified=False,
            latest_reconciliation=None,
            exit_fills=(),
            entry_fills=(_fill(side="BUY", quantity=2.0, price=100.0),),
        )
    )
    assert row.execution_result is ExecutionResult.PARTIAL_FILL
    assert row.economic_result is EconomicResult.UNRESOLVED
    assert row.lifecycle_status is LifecycleStatus.OPEN


def test_no_fill_trade_is_pending_with_no_exposure():
    """An admitted trade that never filled is PENDING, not a loss."""
    row = build_trade_row(
        _entry(
            entry_quantity=0.0,
            exited_quantity=0.0,
            remaining_quantity=0.0,
            final_verified=False,
            latest_reconciliation=None,
            entry_fills=(),
            exit_fills=(),
            first_entry_fill_time=None,
            last_exit_fill_time=None,
        )
    )
    assert row.execution_result is ExecutionResult.NO_FILL
    assert row.lifecycle_status is LifecycleStatus.PENDING
    assert row.economic_result is EconomicResult.UNRESOLVED


def test_exit_mechanism_uses_last_trigger_and_retains_all_mechanisms():
    """The closing mechanism is the last trigger; history is not discarded."""
    row = build_trade_row(_entry(trigger_types=("TARGET", "STOP")))
    assert row.exit_mechanism is ExitMechanism.STOP
    assert row.exit_mechanisms == (ExitMechanism.TARGET, ExitMechanism.STOP)


def test_no_trigger_and_no_exit_reports_no_mechanism():
    row = build_trade_row(
        _entry(trigger_types=(), exit_fills=(), remaining_quantity=5.0)
    )
    assert row.exit_mechanism is ExitMechanism.NONE
    assert row.exit_mechanisms == ()


def test_early_close_is_a_relationship_to_plan_not_an_outcome():
    """Closing before the horizon at a profit is early AND a win, not a category."""
    row = build_trade_row(_entry(trigger_types=("TARGET",)))
    assert row.planned_max_hold_seconds == 3600
    assert row.holding_seconds == pytest.approx(600.0)
    assert row.early_close is True
    # Independent dimensions, both true at once.
    assert row.economic_result is EconomicResult.WIN


def test_early_close_unknown_when_holding_cannot_be_proven():
    """No provable holding duration means no early-close claim."""
    row = build_trade_row(
        _entry(
            first_entry_fill_time=_bounded(0, 60),
            last_exit_fill_time=_bounded(600, 660),
        )
    )
    assert row.early_close is None


# ---------------------------------------------------------------------------
# Temporal evidence: never invent point precision
# ---------------------------------------------------------------------------


def test_bounded_evidence_yields_an_interval_and_no_point():
    assert temporal_point(_bounded(0, 60)) is None
    interval = temporal_interval(_bounded(0, 60))
    assert interval is not None
    assert (interval[1] - interval[0]).total_seconds() == pytest.approx(60.0)


def test_unknown_temporal_evidence_yields_nothing():
    unknown = {"precision": "UNKNOWN", "basis": "MODEL_ASSIGNED", "reason": "no clock"}
    assert temporal_point(unknown) is None
    assert temporal_interval(unknown) is None


def test_latency_interval_is_always_an_interval_never_a_bare_point():
    low, high = latency_interval(_bounded(0, 10), _bounded(100, 120))
    assert (low, high) == pytest.approx((90.0, 120.0))


def test_impossible_chronology_is_refused_rather_than_clamped():
    """Evidence that would make time run backwards produces no latency."""
    assert latency_interval(_bounded(100, 200), _bounded(0, 50)) is None


def test_bounded_fills_do_not_produce_a_holding_or_early_close_claim():
    row = build_trade_row(
        _entry(
            first_entry_fill_time=_bounded(0, 30),
            last_exit_fill_time=_bounded(600, 630),
        )
    )
    assert row.holding_seconds is None
    assert row.holding_seconds_interval is not None
    assert row.early_close is None


# ---------------------------------------------------------------------------
# Portfolio separation, trust propagation, determinism, lineage
# ---------------------------------------------------------------------------


def _row_for(currency: str, trade_id: str) -> Any:
    return build_trade_row(
        _entry(
            paper_trade_id=trade_id,
            quote_currency=currency,
            native_symbol=f"BTC/{currency}",
        )
    )


def test_usd_and_usdt_are_grouped_separately_and_never_summed():
    """The two quote currencies are distinct portfolios with distinct capacity."""
    ledger = PaperLedger(
        entries=(
            _row_for("USD", "PTV2:" + "1" * 64),
            _row_for("USDT", "PTV2:" + "2" * 64),
        )
    )
    grouped = ledger.by_quote_currency()
    assert set(grouped) == {"USD", "USDT"}
    assert len(grouped["USD"]) == 1
    assert len(grouped["USDT"]) == 1
    assert grouped["USD"][0].quote_currency == "USD"
    assert grouped["USDT"][0].quote_currency == "USDT"


def test_settled_excludes_unverified_rows():
    verified = _row_for("USD", "PTV2:" + "a" * 64)
    unverified = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "b" * 64,
            latest_reconciliation=None,
            final_verified=False,
        )
    )
    ledger = PaperLedger(entries=(verified, unverified))
    assert [row.paper_trade_id for row in ledger.settled()] == [
        verified.paper_trade_id
    ]


def test_unreadable_store_is_not_an_empty_healthy_ledger():
    """The distinction that matters most: unreadable != "no trades"."""
    ledger = build_ledger(
        PaperV2Ledger(status="RETRYABLE", error_code="WORKER_UNHEALTHY")
    )
    assert ledger.entries == ()
    assert ledger.trust.is_healthy is False
    assert ledger.trust.completeness is Completeness.UNKNOWN
    assert ledger.trust.freshness is not Freshness.LIVE
    assert ledger.details


def test_rejected_ledger_read_is_also_unhealthy():
    ledger = build_ledger(PaperV2Ledger(status="REJECTED", error_code="BAD_STATE"))
    assert ledger.entries == ()
    assert ledger.trust.is_healthy is False


def test_read_failure_is_typed_rather_than_raised():
    class _Exploding:
        def get_paper_v2_ledger(self):
            raise RuntimeError("socket closed")

    ledger = read_paper_ledger(_Exploding())
    assert ledger.entries == ()
    assert ledger.trust.is_healthy is False
    assert any("LEDGER_READ_FAILED" in reason for reason in ledger.trust.reasons)


def test_ledger_carries_its_projection_version():
    ledger = build_ledger(PaperV2Ledger(status="OK"))
    assert ledger.projection_version == COCKPIT_LEDGER_PROJECTION_VERSION
    assert ledger.to_dict()["projection_version"] == "cockpit-ledger-v1"


def test_rebuilding_the_same_row_is_byte_identical():
    """Replay determinism: the derivation is a pure function of committed facts."""
    entry = _entry()
    first = json.dumps(build_trade_row(entry).to_dict(), sort_keys=True)
    second = json.dumps(build_trade_row(entry).to_dict(), sort_keys=True)
    assert first == second


def test_canonical_lineage_is_preserved_for_audit():
    row = build_trade_row(_entry())
    assert row.event_ids == ("e1", "e2", "e3")


def test_cost_breakdown_matches_the_canonical_components():
    """Fee/spread/slippage/other stay separable - never a single opaque cost."""
    row = build_trade_row(_entry())
    assert row.fee_cost == pytest.approx(0.8)
    assert row.spread_cost == pytest.approx(0.4)
    assert row.slippage_cost == pytest.approx(0.2)
    assert row.other_cost == pytest.approx(0.0)
    assert row.fee_cost + row.spread_cost + row.slippage_cost + row.other_cost == (
        pytest.approx(row.execution_costs)
    )


def test_cost_model_version_is_surfaced_for_historical_correctness():
    row = build_trade_row(_entry())
    assert row.economic_model_version == "opip-paper-economics-v2"


def test_planned_geometry_is_exposed_for_planned_versus_observed():
    row = build_trade_row(_entry())
    assert row.planned_stop_price == pytest.approx(90.0)
    assert row.planned_max_hold_seconds == 3600
    assert row.plan_seq == 0
    assert [t["target_id"] for t in row.planned_targets] == ["TP1"]


def test_observed_vwaps_are_quantity_weighted():
    row = build_trade_row(
        _entry(
            entry_fills=(
                _fill(side="BUY", quantity=1.0, price=100.0, offset=0),
                _fill(side="BUY", quantity=3.0, price=110.0, offset=10),
            ),
            entry_quantity=4.0,
        )
    )
    # (1*100 + 3*110) / 4 = 107.5
    assert row.entry_price_vwap == pytest.approx(107.5)


def test_missing_instrument_facts_do_not_materialise_as_empty_strings():
    row = build_trade_row(_entry(native_symbol=None, quote_currency=None))
    assert row.native_symbol is None
    assert row.quote_currency is None


def test_policy_version_axis_is_surfaced_even_without_a_strategy_name():
    """The B/C-4A claim: strategy identity is policy version + fingerprint."""
    row = build_trade_row(_entry())
    assert row.policy_version == "opip-gate-v1"
    assert row.policy_fingerprint == "fp-1"
    assert not hasattr(row, "strategy_name")


# ---------------------------------------------------------------------------
# Integration: real producer -> real writer -> real ledger RPC
# ---------------------------------------------------------------------------

_QUALIFICATION = NOW + timedelta(seconds=5)


class _Settings:
    opip_paper_v2_mode = "active"
    paper_v2_quote_max_age_seconds = 15
    paper_trade_fee_rate = 0.004
    paper_trade_slippage_bps = 10.0
    paper_v2_tp1_fraction = 0.5
    paper_v2_max_hold_seconds = 86_400
    account_equity = 10_000.0
    paper_trade_starting_equity = 10_000.0


class _BookTransport:
    def __init__(self, *, publication_ts: str, bid: float = 99.9, ask: float = 100.0):
        self._ts = publication_ts
        self._bid = bid
        self._ask = ask

    def request(self, endpoint, params, timeout_seconds):
        return {
            "symbol": params.get("symbol"),
            "bids": [{"price": self._bid, "qty": 10.0, "publication_ts": self._ts}],
            "asks": [{"price": self._ask, "qty": 12.0, "publication_ts": self._ts}],
        }

    def telemetry_snapshot(self) -> dict:
        return {}


def _instrument_version():
    from app.opip.contracts.identity import InstrumentVersion

    return InstrumentVersion(
        venue="kraken",
        base_asset="BTC",
        quote_currency="USD",
        venue_instrument_id="BTC/USD",
        version=1,
        reference_data_version="ref-btc",
        observed_at_utc=NOW - timedelta(minutes=5),
    )


def _snapshot_payload() -> dict:
    from app.services.canonical_episode_capture import (
        build_canonical_episode_snapshots,
    )

    observation = SimpleNamespace(
        symbol="BTCUSD",
        base_asset="BTC",
        kraken_public_symbol="BTC/USD",
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
        symbol="BTCUSD",
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


def _opportunity():
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )
    from app.services.paper_v2_execution import PaperV2Opportunity

    snapshot = _snapshot_payload()
    version = _instrument_version()
    return PaperV2Opportunity(
        candidate_id="OPIPC:" + "b" * 20,
        episode_id=snapshot["episode_id"],
        cohort_id=snapshot["cohort_id"],
        direction="LONG",
        instrument_version_id=version.instrument_version_id,
        snapshot_payload=snapshot,
        evaluation_time=_QUALIFICATION,
        evidence_cutoff=NOW,
        source_record_refs=(),
        qualification_policy_version=GATE_POLICY_VERSION,
        qualification_policy_fingerprint=gate_policy_fingerprint(),
        instrument_version=version,
        quote_currency="USD",
        requested_capital=500.0,
        requested_reservation_amount=500.0,
        decision_time=_QUALIFICATION,
        native_symbol="BTC/USD",
        requested_quantity=5.0,
        requested_notional=500.0,
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=90.0,
        target_prices=(110.0, 120.0),
    )


@pytest.fixture
def writer_env(tmp_path: Path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        yield server, InProcessWriterClient(server)
    finally:
        server.stop()


def test_integration_real_entry_appears_in_the_ledger(writer_env):
    """A real Paper-v2 entry reaches the cockpit ledger through the real RPC."""
    from app.exchanges.kraken import KrakenClient
    from app.services.paper_v2_execution import run_paper_v2_opportunity

    server, client = writer_env
    result = run_paper_v2_opportunity(
        _opportunity(),
        client=client,
        kraken_client=KrakenClient(
            transport=_BookTransport(publication_ts="2026-09-20T11:59:59Z")
        ),
        settings=_Settings(),
        now=_QUALIFICATION,
        clock=lambda: _QUALIFICATION,
    )

    ledger = read_paper_ledger(client)

    assert ledger.trust.is_healthy is True
    rows = [row for row in ledger.entries if row.paper_trade_id == result.paper_trade_id]
    assert len(rows) == 1
    row = rows[0]

    # Identity: the strategy axis is the policy version, not a strategy name.
    assert row.quote_currency == "USD"
    assert row.native_symbol == "BTC/USD"
    assert row.policy_version
    assert row.policy_fingerprint

    # A freshly opened position has real exposure and is NOT a settled result.
    assert row.lifecycle_status is LifecycleStatus.OPEN
    assert row.entry_quantity == pytest.approx(5.0)
    assert row.remaining_quantity == pytest.approx(5.0)
    assert row.net_pnl_definitive is False
    assert row.economic_result is EconomicResult.UNRESOLVED
    assert row.trust.completeness is Completeness.INCOMPLETE

    # The plan is bound and the cost/economic models are named.
    assert row.planned_stop_price == pytest.approx(90.0)
    assert row.economic_model_version == "opip-paper-economics-v2"

    # Lineage is present so Trade Detail can trace every number.
    assert len(row.event_ids) >= 5
    assert len(set(row.event_ids)) == len(row.event_ids)


def test_integration_ledger_is_deterministic_across_reads(writer_env):
    """Two reads of the same committed evidence produce identical output."""
    from app.exchanges.kraken import KrakenClient
    from app.services.paper_v2_execution import run_paper_v2_opportunity

    _server, client = writer_env
    run_paper_v2_opportunity(
        _opportunity(),
        client=client,
        kraken_client=KrakenClient(
            transport=_BookTransport(publication_ts="2026-09-20T11:59:59Z")
        ),
        settings=_Settings(),
        now=_QUALIFICATION,
        clock=lambda: _QUALIFICATION,
    )

    first = json.dumps(read_paper_ledger(client).to_dict(), sort_keys=True)
    second = json.dumps(read_paper_ledger(client).to_dict(), sort_keys=True)
    assert first == second


def test_integration_unhealthy_store_never_reports_an_empty_healthy_ledger(writer_env):
    """An unhealthy canonical writer withholds the ledger rather than zeroing it."""
    server, client = writer_env
    server.mark_worker_unhealthy_for_tests("MAINTENANCE")

    ledger = read_paper_ledger(client)

    assert ledger.entries == ()
    assert ledger.trust.is_healthy is False
    assert ledger.trust.completeness is Completeness.UNKNOWN
    assert ledger.details


def test_integration_bounded_read_uses_the_per_trade_index(writer_env):
    """The ledger read must stay structurally bounded as history grows."""
    server, _client = writer_env
    plan = server.writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "EXPLAIN QUERY PLAN SELECT event_id FROM events "
        "WHERE json_extract(payload_json, '$.paper_trade_id') = ?",
        ("PTV2:none",),
    ).fetchall()
    detail = " ".join(str(row["detail"]) for row in plan)
    assert "idx_events_paper_trade" in detail, detail


def test_replace_is_not_used_to_mutate_committed_rows():
    """Guard: a derived row must never be able to rewrite canonical evidence."""
    row = build_trade_row(_entry())
    mutated = replace(row, net_pnl=999.0)
    # Rebuilding from the same committed facts is unaffected by the copy.
    assert build_trade_row(_entry()).net_pnl == pytest.approx(48.6)
    assert mutated.net_pnl == pytest.approx(999.0)
