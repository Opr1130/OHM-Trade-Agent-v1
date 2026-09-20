"""B/C-4C portfolio / contribution / attention analytics tests.

The tests concentrate on the rules that decide whether an owner can trust what the
overview shows:

* marked-equity drawdown is ``UNKNOWN`` (never silently ``0``, never silently the
  realized series wearing the marked name);
* nothing is summed across quote currencies;
* unverified economics never move the realized equity curve;
* contribution carries no statistical authority it has not earned;
* attention items are decision-first (what happened, what to do, evidence).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.cockpit import (
    PaperLedger,
    ValuationPosture,
    build_currency_portfolio,
    build_drawdown_summary,
    build_overview,
    build_realized_equity_series,
    build_strategy_contribution,
)
from app.opip.cockpit.ledger import (
    EconomicResult,
    LifecycleStatus,
    build_trade_row,
)
from app.opip.cockpit.portfolio import NO_MARK_EVIDENCE
from app.opip.cockpit.trust import Completeness, Freshness, Uncertainty
from app.opip.canonical.models import PaperV2LedgerEntry

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _exact(offset_seconds: int) -> dict[str, object]:
    moment = NOW + timedelta(seconds=offset_seconds)
    return {
        "precision": "EXACT",
        "basis": "SOURCE_REPORTED",
        "occurred_at": moment.isoformat().replace("+00:00", "Z"),
    }


def _entry(**overrides):
    base = dict(
        paper_trade_id="PTV2:" + "a" * 64,
        quote_currency="USD",
        native_symbol="BTC/USD",
        policy_version="gate-v1",
        policy_fingerprint="fp1",
        entry_quantity=5.0,
        exited_quantity=5.0,
        remaining_quantity=0.0,
        latest_reconciliation={
            "reconciliation_seq": 1,
            "terminal_reconciliation_state": "FINAL_VERIFIED",
            "position_state": "FLAT",
        },
        final_verified=True,
        entry_fills=(
            {
                "side": "BUY",
                "quantity": 5.0,
                "price": 100.0,
                "fee_cost": 0.1,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "other_supported_cost": 0.0,
                "fill_time": _exact(0),
            },
        ),
        exit_fills=(
            {
                "side": "SELL",
                "quantity": 5.0,
                "price": 110.0,
                "fee_cost": 0.1,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "other_supported_cost": 0.0,
                "fill_time": _exact(600),
            },
        ),
        first_entry_fill_time=_exact(0),
        last_exit_fill_time=_exact(600),
        entry_order_intent={"requested_quantity": 5.0, "intent_role": "ENTRY"},
        protection_state="TRIGGERED",
        event_ids=("e1", "e2"),
    )
    base.update(overrides)
    return PaperV2LedgerEntry(**base)


def _settled(
    *,
    trade_id: str,
    net: float,
    exit_offset: int,
    currency: str = "USD",
    policy_version: str = "gate-v1",
):
    reconciliation = {
        "reconciliation_seq": 1,
        "terminal_reconciliation_state": "FINAL_VERIFIED",
        "position_state": "FLAT",
        "realized_gross_pnl": net + 0.2,
        "recorded_execution_costs": 0.2,
        "realized_net_pnl": net,
    }
    return build_trade_row(
        _entry(
            paper_trade_id=trade_id,
            quote_currency=currency,
            native_symbol=f"BTC/{currency}",
            policy_version=policy_version,
            latest_reconciliation=reconciliation,
            final_verified=True,
            last_exit_fill_time=_exact(exit_offset),
        )
    )


# ---------------------------------------------------------------------------
# Marked vs realized drawdown - the central rule
# ---------------------------------------------------------------------------


def test_marked_equity_drawdown_is_unknown_not_zero():
    """A marked figure that cannot be computed must never render as 0."""
    rows = [_settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600)]
    portfolio = build_currency_portfolio(
        "USD", rows, now=NOW, starting_equity=10_000.0
    )

    assert portfolio.drawdown.marked_current_drawdown is None
    assert portfolio.drawdown.marked_max_drawdown is None
    assert portfolio.drawdown.valuation.status == "UNKNOWN"
    assert portfolio.drawdown.valuation.reason == NO_MARK_EVIDENCE
    assert portfolio.drawdown.valuation.is_known is False
    # Critically: not zero, and not a copy of the realized series.
    assert portfolio.drawdown.marked_max_drawdown != 0


def test_realized_drawdown_is_reported_under_its_own_name():
    """The realized series is authoritative here and is named explicitly."""
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600),
        _settled(trade_id="PTV2:" + "2" * 64, net=-25.0, exit_offset=1200),
    ]
    portfolio = build_currency_portfolio("USD", rows, now=NOW)

    # Cumulative: 10 then -15; peak 10, trough -15 -> drawdown 25.
    assert portfolio.drawdown.realized_max_drawdown_quote_currency == pytest.approx(
        25.0
    )
    assert portfolio.drawdown.realized_current_drawdown_quote_currency == (
        pytest.approx(25.0)
    )
    assert portfolio.drawdown.realized_closed_trades == 2


def test_overview_surfaces_valuation_posture_globally():
    overview = build_overview(PaperLedger(), now=NOW)
    assert overview.valuation.status == "UNKNOWN"
    assert overview.valuation.reason == NO_MARK_EVIDENCE


def test_drawdown_percentage_requires_an_explicit_base():
    """Without a stated starting equity there is no defensible percentage."""
    rows = [_settled(trade_id="PTV2:" + "1" * 64, net=-10.0, exit_offset=600)]
    without_base = build_currency_portfolio("USD", rows, now=NOW)
    assert without_base.drawdown.realized_max_drawdown_pct is None

    with_base = build_currency_portfolio(
        "USD", rows, now=NOW, starting_equity=1_000.0
    )
    assert with_base.drawdown.realized_max_drawdown_pct == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# The equity series only contains verified, time-placeable results
# ---------------------------------------------------------------------------


def test_unverified_rows_never_move_the_realized_equity_curve():
    """Indicative economics must not appear as realized profit."""
    verified = _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600)
    unverified = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "2" * 64,
            latest_reconciliation=None,
            final_verified=False,
            last_exit_fill_time=_exact(1200),
        )
    )
    points = build_realized_equity_series([verified, unverified])
    assert len(points) == 1
    assert points[0].cumulative_net_pnl == pytest.approx(10.0)


def test_row_without_a_close_instant_is_excluded_from_the_series():
    """A settled row that cannot be placed in time is excluded, not timestamped."""
    settled = _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600)
    untimed = build_trade_row(
        _entry(paper_trade_id="PTV2:" + "2" * 64, last_exit_fill_time=None)
    )
    assert untimed.is_settled is True
    points = build_realized_equity_series([settled, untimed])
    assert len(points) == 1


def test_equity_series_is_ordered_by_close_instant_not_input_order():
    later = _settled(trade_id="PTV2:" + "2" * 64, net=5.0, exit_offset=2000)
    earlier = _settled(trade_id="PTV2:" + "1" * 64, net=7.0, exit_offset=600)
    points = build_realized_equity_series([later, earlier])
    assert [point.cumulative_net_pnl for point in points] == [7.0, 12.0]


def test_time_underwater_and_recovery_are_derived_from_the_realized_series():
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=20.0, exit_offset=600),
        _settled(trade_id="PTV2:" + "2" * 64, net=-10.0, exit_offset=1200),
        _settled(trade_id="PTV2:" + "3" * 64, net=15.0, exit_offset=1800),
    ]
    summary = build_drawdown_summary(build_realized_equity_series(rows))
    assert summary.realized_time_underwater_seconds == pytest.approx(600.0)
    assert summary.realized_recovery_duration_seconds == pytest.approx(600.0)


def test_empty_series_is_zero_drawdown_with_no_trades():
    """Zero drawdown is legitimate *when there are no trades* - unlike a missing mark."""
    summary = build_drawdown_summary(())
    assert summary.realized_max_drawdown_quote_currency == 0.0
    assert summary.realized_closed_trades == 0
    # Marked remains unknown even here.
    assert summary.marked_max_drawdown is None


# ---------------------------------------------------------------------------
# Currency separation
# ---------------------------------------------------------------------------


def test_currencies_are_never_summed_together():
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=100.0, exit_offset=600, currency="USD"),
        _settled(
            trade_id="PTV2:" + "2" * 64, net=50.0, exit_offset=900, currency="USDT"
        ),
    ]
    ledger = PaperLedger(entries=tuple(rows))
    overview = build_overview(ledger, now=NOW)

    portfolios = overview.by_quote_currency()
    assert set(portfolios) == {"USD", "USDT"}
    assert portfolios["USD"].realized_net_pnl == pytest.approx(100.0)
    assert portfolios["USDT"].realized_net_pnl == pytest.approx(50.0)
    # No cross-currency total exists anywhere in the payload.
    assert "realized_net_pnl" not in overview.to_dict()


def test_each_currency_keeps_its_own_drawdown():
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=-30.0, exit_offset=600, currency="USD"),
        _settled(trade_id="PTV2:" + "2" * 64, net=10.0, exit_offset=900, currency="USDT"),
    ]
    overview = build_overview(PaperLedger(entries=tuple(rows)), now=NOW)
    portfolios = overview.by_quote_currency()
    assert portfolios["USD"].drawdown.realized_max_drawdown_quote_currency == (
        pytest.approx(30.0)
    )
    assert portfolios["USDT"].drawdown.realized_max_drawdown_quote_currency == (
        pytest.approx(0.0)
    )


# ---------------------------------------------------------------------------
# Strategy / version contribution
# ---------------------------------------------------------------------------


def test_contribution_groups_by_policy_version_and_exposes_expectancy():
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600, policy_version="gate-v1"),
        _settled(trade_id="PTV2:" + "2" * 64, net=30.0, exit_offset=1200, policy_version="gate-v1"),
        _settled(trade_id="PTV2:" + "3" * 64, net=-5.0, exit_offset=1800, policy_version="gate-v2"),
    ]
    contribution = {item.policy_version: item for item in build_strategy_contribution(rows)}

    assert contribution["gate-v1"].settled_trades == 2
    assert contribution["gate-v1"].realized_net_pnl == pytest.approx(40.0)
    assert contribution["gate-v1"].expectancy_quote_currency == pytest.approx(20.0)
    assert contribution["gate-v1"].winning_trades == 2
    assert contribution["gate-v2"].losing_trades == 1


def test_contribution_carries_no_statistical_authority_it_has_not_earned():
    """No registered interval estimator exists, so expectancy is not supported."""
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600),
    ]
    item = build_strategy_contribution(rows)[0]
    assert item.uncertainty is Uncertainty.INSUFFICIENT_EVIDENCE
    assert "NO_REGISTERED_INTERVAL_ESTIMATOR" in item.uncertainty_reasons


def test_unverified_trades_are_excluded_from_contribution():
    verified = _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600)
    unverified = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "2" * 64,
            latest_reconciliation=None,
            final_verified=False,
            policy_version="gate-v9",
        )
    )
    contributions = build_strategy_contribution([verified, unverified])
    assert [item.policy_version for item in contributions] == ["gate-v1"]


def test_contribution_ordering_is_presentational_not_a_verdict():
    """Ordering is stated as presentational: every entry is INSUFFICIENT_EVIDENCE."""
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=1.0, exit_offset=600, policy_version="a"),
        _settled(trade_id="PTV2:" + "2" * 64, net=99.0, exit_offset=1200, policy_version="b"),
    ]
    contributions = build_strategy_contribution(rows)
    assert [item.policy_version for item in contributions] == ["b", "a"]
    assert all(
        item.uncertainty is Uncertainty.INSUFFICIENT_EVIDENCE
        for item in contributions
    )


# ---------------------------------------------------------------------------
# Attention, accountability, current watch
# ---------------------------------------------------------------------------


def test_flat_but_unverified_trade_raises_attention_with_evidence():
    flat_unverified = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "9" * 64,
            latest_reconciliation=None,
            final_verified=False,
            exit_fills=(
                {
                    "side": "SELL",
                    "quantity": 5.0,
                    "price": 110.0,
                    "fee_cost": 0.0,
                    "spread_cost": 0.0,
                    "slippage_cost": 0.0,
                    "other_supported_cost": 0.0,
                    "fill_time": _exact(600),
                },
            ),
            last_exit_fill_time=_exact(600),
        )
    )
    portfolio = build_currency_portfolio(
        "USD", [flat_unverified], now=NOW + timedelta(seconds=1200)
    )
    codes = [item.code for item in portfolio.attention]
    assert "FLAT_BUT_UNVERIFIED" in codes

    item = next(i for i in portfolio.attention if i.code == "FLAT_BUT_UNVERIFIED")
    assert item.action_required
    assert item.authority_affected is False
    assert item.evidence_refs == ("e1", "e2")
    # Closed 600s after NOW, read at NOW+1200s.
    assert item.age_seconds == pytest.approx(600.0)


def test_attention_age_is_withheld_rather_than_negative_when_clock_skews():
    """A close instant after the reading clock yields no age, not a negative one."""
    flat_unverified = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "5" * 64,
            latest_reconciliation=None,
            final_verified=False,
            exit_fills=(
                {
                    "side": "SELL",
                    "quantity": 5.0,
                    "price": 110.0,
                    "fee_cost": 0.0,
                    "spread_cost": 0.0,
                    "slippage_cost": 0.0,
                    "other_supported_cost": 0.0,
                    "fill_time": _exact(600),
                },
            ),
            last_exit_fill_time=_exact(600),
        )
    )
    # Read *before* the recorded close: age is unprovable, so it is withheld.
    portfolio = build_currency_portfolio("USD", [flat_unverified], now=NOW)
    item = next(i for i in portfolio.attention if i.code == "FLAT_BUT_UNVERIFIED")
    assert item.age_seconds is None


def test_open_position_without_protection_raises_attention():
    naked = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "8" * 64,
            latest_reconciliation=None,
            final_verified=False,
            protection_state=None,
            exit_fills=(),
            exited_quantity=0.0,
            remaining_quantity=5.0,
            last_exit_fill_time=None,
        )
    )
    portfolio = build_currency_portfolio("USD", [naked], now=NOW)
    assert "OPEN_WITHOUT_PROTECTION" in {i.code for i in portfolio.attention}


def test_unresolved_evidence_reconciliation_raises_attention():
    unresolved = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "7" * 64,
            latest_reconciliation={
                "reconciliation_seq": 1,
                "terminal_reconciliation_state": "UNRESOLVED_EVIDENCE",
                "position_state": "FLAT",
            },
            final_verified=False,
        )
    )
    portfolio = build_currency_portfolio("USD", [unresolved], now=NOW)
    assert "UNRESOLVED_EVIDENCE" in {i.code for i in portfolio.attention}


def test_attention_never_claims_trading_authority():
    """Attention is advisory: nothing it reports may affect authority."""
    rows = [
        build_trade_row(
            _entry(
                paper_trade_id="PTV2:" + "6" * 64,
                latest_reconciliation=None,
                final_verified=False,
                protection_state=None,
                exit_fills=(),
                exited_quantity=0.0,
                remaining_quantity=5.0,
                last_exit_fill_time=None,
            )
        )
    ]
    portfolio = build_currency_portfolio("USD", rows, now=NOW)
    assert portfolio.attention
    assert all(item.authority_affected is False for item in portfolio.attention)


def test_accountability_reports_admitted_counts_and_states_the_rejection_gap():
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600),
        build_trade_row(
            _entry(
                paper_trade_id="PTV2:" + "2" * 64,
                latest_reconciliation=None,
                final_verified=False,
                exit_fills=(),
                exited_quantity=0.0,
                remaining_quantity=5.0,
                last_exit_fill_time=None,
            )
        ),
    ]
    portfolio = build_currency_portfolio("USD", rows, now=NOW)
    accountability = portfolio.accountability
    assert accountability.admitted == 2
    assert accountability.closed_settled == 1
    assert accountability.open_positions == 1
    # Rejected counts cannot be derived here, and are declared unavailable rather
    # than silently reported as zero.
    assert accountability.rejected_counts_available is False
    assert accountability.rejected_unavailable_reason


def test_current_watch_lists_open_positions_only():
    open_row = build_trade_row(
        _entry(
            paper_trade_id="PTV2:" + "3" * 64,
            latest_reconciliation=None,
            final_verified=False,
            exit_fills=(),
            exited_quantity=0.0,
            remaining_quantity=5.0,
            last_exit_fill_time=None,
        )
    )
    settled = _settled(trade_id="PTV2:" + "4" * 64, net=1.0, exit_offset=600)
    portfolio = build_currency_portfolio("USD", [open_row, settled], now=NOW)
    assert [row.paper_trade_id for row in portfolio.current_watch] == [
        open_row.paper_trade_id
    ]
    assert portfolio.open_positions == 1


def test_recent_settled_trades_are_most_recent_first_and_bounded():
    rows = [
        _settled(trade_id="PTV2:" + f"{index}" * 64, net=1.0, exit_offset=600 * index)
        for index in range(1, 6)
    ]
    portfolio = build_currency_portfolio("USD", rows, now=NOW, recent_limit=2)
    recent = portfolio.recent_settled_trades
    assert len(recent) == 2
    # The last-closed trades are the most recent offsets.
    assert recent[0].last_exit_fill_at > recent[1].last_exit_fill_at


# ---------------------------------------------------------------------------
# Trust propagation and determinism
# ---------------------------------------------------------------------------


def test_unavailable_evidence_raises_a_global_attention_item():
    from app.opip.cockpit.trust import unavailable

    ledger = PaperLedger(entries=(), trust=unavailable("CANONICAL_READ_RETRYABLE"))
    overview = build_overview(ledger, now=NOW)
    assert overview.trust.is_healthy is False
    assert "EVIDENCE_UNAVAILABLE" in {item.code for item in overview.attention}
    assert overview.portfolios == ()


def test_overview_is_deterministic_for_the_same_ledger():
    rows = [
        _settled(trade_id="PTV2:" + "1" * 64, net=10.0, exit_offset=600),
        _settled(trade_id="PTV2:" + "2" * 64, net=-4.0, exit_offset=1200),
    ]
    ledger = PaperLedger(entries=tuple(rows))
    first = json.dumps(build_overview(ledger, now=NOW).to_dict(), sort_keys=True)
    second = json.dumps(build_overview(ledger, now=NOW).to_dict(), sort_keys=True)
    assert first == second


def test_overview_carries_both_projection_versions():
    overview = build_overview(PaperLedger(), now=NOW)
    payload = overview.to_dict()
    assert payload["projection_version"] == "cockpit-portfolio-v1"
    assert payload["ledger_projection_version"] == "cockpit-ledger-v1"


def test_portfolio_trust_inherits_ledger_freshness():
    """A stale/incomplete ledger must make the overview visibly unhealthy."""
    from app.opip.cockpit.trust import TrustEnvelope

    ledger = PaperLedger(
        entries=(),
        trust=TrustEnvelope(
            freshness=Freshness.STALE, completeness=Completeness.INCOMPLETE
        ),
    )
    overview = build_overview(ledger, now=NOW)
    assert overview.trust.freshness is Freshness.STALE
    assert overview.trust.completeness is Completeness.INCOMPLETE
    assert overview.trust.is_healthy is False


def test_zero_verified_trades_reports_no_expectancy_rather_than_zero():
    """With nothing settled there is no expectancy - not an expectancy of zero."""
    portfolio = build_currency_portfolio("USD", (), now=NOW)
    assert portfolio.expectancy_quote_currency is None
    assert portfolio.realized_net_pnl == 0.0
    assert portfolio.strategy_contribution == ()
