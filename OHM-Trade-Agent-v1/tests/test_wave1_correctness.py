import logging
from types import SimpleNamespace

import pytest

from app.exchanges.kraken import BookLevel, PreTradeBook, PublicTrade
from app.scanner.market_scanner import deep_validate_candidates
from app.scanner.models import MarketSnapshot
from app.services import outcome_financials, trade_outcome_registry
from app.services.active_trade_registry import ActiveTrade
from app.services.capital_allocation import recommend_capital
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.kraken_reconciliation import _matching_fills
from app.services.outcome_financials import finalize_financial_outcome
from app.services.profitability_learning import _trade_bucket_weights
from app.services.registry_io import load_json
from app.services.secret_auth import secret_matches
from app.services.self_calibration import calibration_model


def test_calibration_cannot_exceed_hard_risk_budget():
    result = recommend_capital(
        available_capital=10_000,
        stop_distance_pct=10.0,
        confidence_score=100,
        quality_score=100,
        net_edge_pct=10.0,
        calibration_multiplier=1.25,
        max_position_pct=100.0,
        risk_per_trade_pct=1.0,
        leverage=1.0,
    )
    assert result.recommended_capital == 1_000.0
    assert result.risk_dollars == 100.0


def test_long_economic_gate_enforces_same_account_stop_risk_cap():
    plan = SimpleNamespace(
        direction="LONG",
        entry_low=100.0,
        entry_high=100.0,
        stop_price=90.0,
        target_1=115.0,
        target_2=130.0,
        reward_to_risk_2=3.0,
    )
    result = evaluate_economic_quality(plan, 10_000, min_net_profit=1.0)
    assert not result.qualified
    assert result.account_risk_at_stop_pct == 10.0
    assert "Stop exposure" in result.rejection_reason


def _financial_rows():
    rows = []
    for direction, win_value in (("LONG", 1.0), ("SHORT", 10.0)):
        rows.extend(
            {
                "entered_trade": True,
                "terminal_status": "closed",
                "direction": direction,
                "market_regime": "NEUTRAL",
                "net_pnl": win_value,
            }
            for _ in range(10)
        )
        rows.extend(
            {
                "entered_trade": True,
                "terminal_status": "closed",
                "direction": direction,
                "market_regime": "NEUTRAL",
                "net_pnl": -1.0,
            }
            for _ in range(5)
        )
    return rows


def test_self_calibration_uses_expectancy_not_only_win_rate():
    model = calibration_model(_financial_rows(), min_samples=30)
    assert model["status"] == "CALIBRATED"
    assert model["evidence"]["direction:LONG"]["success_rate"] == model["evidence"]["direction:SHORT"]["success_rate"]
    assert model["weights"]["direction:SHORT"] > 1.0
    assert model["weights"]["direction:LONG"] < 1.0
    assert model["objective"] == "realized_net_pnl_expectancy_after_costs"


def test_profitability_profile_weights_use_expectancy_magnitude():
    weights, evidence = _trade_bucket_weights(_financial_rows())
    assert evidence["status"] == "CALIBRATED"
    assert weights["direction:SHORT"] > weights["direction:LONG"]
    assert evidence["evidence"]["direction:SHORT"]["net_win_rate"] == evidence["evidence"]["direction:LONG"]["net_win_rate"]


def _snapshot():
    return MarketSnapshot(
        symbol="SOLUSD", last_price=100, ema20=99, ema50=98, ema200=90,
        rsi=55, macd_line=1, macd_signal=.5, macd_histogram=.5,
        atr=2, atr_pct=2, volume_ratio=1.2, technical_score=90,
        trend="bullish", primary_pair="SOLUSD", primary_quote_currency="USD",
        kraken_public_symbol="SOL/USD", ticker_last=100,
    )


def test_execution_validation_uses_realistic_position_notional():
    class Client:
        def get_pre_trade(self, symbol):
            return PreTradeBook(
                symbol,
                [BookLevel(99.9, 100), BookLevel(99.5, 100)],
                [BookLevel(100.1, 100), BookLevel(100.5, 100)],
            )

        def get_post_trade(self, symbol, count):
            return []

    item = _snapshot()
    result = deep_validate_candidates([item], 2_000, 1.0, Client())
    assert result == [item]
    assert item.execution_validation.validation_notional_usd == pytest.approx(400.0)


def test_exact_order_txid_prevents_similar_manual_fill_attribution():
    trades = {
        "trade-a": {"ordertxid": "OHM-ORDER", "pair": "SOLUSD", "type": "buy", "time": 100, "vol": "1", "price": "100"},
        "trade-b": {"ordertxid": "MANUAL-ORDER", "pair": "SOLUSD", "type": "buy", "time": 101, "vol": "5", "price": "100"},
    }
    exact = _matching_fills(trades, symbol="SOLUSD", side="buy", after_epoch=0, order_txid="OHM-ORDER")
    broad = _matching_fills(trades, symbol="SOLUSD", side="buy", after_epoch=0)
    assert [row["txid"] for row in exact] == ["trade-a"]
    assert len(broad) == 2


def test_unknown_short_financing_is_not_recorded_as_zero_profitability(tmp_path, monkeypatch):
    outcome_path = tmp_path / "trade_outcomes.json"
    outcome_path.write_text("{}")
    monkeypatch.setattr(outcome_financials, "OUTCOME_FILE", outcome_path)
    monkeypatch.setattr(trade_outcome_registry, "OUTCOME_FILE", outcome_path)
    trade = ActiveTrade(
        symbol="SOLUSD",
        entry_price=100,
        stop_price=105,
        target_1=95,
        target_2=90,
        risk_level="manual",
        trade_id="T1",
        direction="SHORT",
        margin_leverage=2.0,
        capital=100.0,
        close_price=90.0,
        actual_entry_fee=0.1,
        actual_exit_fee=0.1,
        financing_fee=0.0,
        financing_fee_known=False,
    )
    result = finalize_financial_outcome(trade)
    assert result["status"] == "FINANCING_FEE_REQUIRED"
    assert result["net_pnl"] is None


def test_malformed_registry_json_is_logged(tmp_path, caplog):
    path = tmp_path / "broken.json"
    path.write_text("{broken")
    with caplog.at_level(logging.ERROR):
        assert load_json(path) == {}
    assert "Malformed registry JSON" in caplog.text


def test_secret_comparison_rejects_missing_and_accepts_exact():
    assert secret_matches("abc", "abc")
    assert not secret_matches("abc", "abcd")
    assert not secret_matches(None, "abc")
    assert not secret_matches("abc", None)
