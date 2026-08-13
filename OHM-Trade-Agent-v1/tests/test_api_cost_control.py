import json
from types import SimpleNamespace

import pytest

from app.api import routes
from app.models.signal import RiskPlan, TradingSignal
from app.scanner.models import MarketSnapshot
from app.services import chief_analyst
from app.services.entry_exit_advisor import EntryExitPlan


def _candidate(symbol="BTCUSD"):
    return MarketSnapshot(
        symbol=symbol,
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
    )


def _plan(symbol="BTCUSD"):
    return EntryExitPlan(
        symbol=symbol,
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="ok",
    )


def test_no_candidate_payload_skips_openai(monkeypatch):
    monkeypatch.setattr(chief_analyst, "_quality_by_risk_level", lambda *args, **kwargs: ({}, False))

    def should_not_run(*args, **kwargs):
        raise AssertionError("OpenAI should not be called")

    monkeypatch.setattr(chief_analyst, "_call_responses_api", should_not_run)
    review = chief_analyst.review_candidates([_candidate()], account_equity=2000)
    assert review["top_candidates"] == []


def test_tradingview_signal_rejected_by_deterministic_risk_still_returns_reject(monkeypatch):
    settings = SimpleNamespace(
        webhook_secret="secret-secret",
        account_equity=2000,
        risk_per_trade_pct=0.35,
        ai_enabled=False,
        min_alert_score=80,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
    )
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "score_signal", lambda signal: (90, ["qualified"]))
    monkeypatch.setattr(
        routes,
        "build_risk_plan",
        lambda *args, **kwargs: RiskPlan(
            risk_dollars=7,
            position_size=1,
            reward_to_risk=1.0,
            allowed=False,
        ),
    )
    monkeypatch.setattr(routes, "append_signal", lambda *args: None)
    signal = TradingSignal(
        symbol="BTC/USD",
        asset_class="crypto",
        price=100.0,
        stop_price=95.0,
        target_price=103.0,
        rsi=50.0,
        volume_ratio=1.5,
        ema_fast=100.0,
        ema_slow=95.0,
        breakout=True,
    )
    decision = routes.tradingview_webhook(signal, "secret-secret")
    assert decision.action == "reject"
