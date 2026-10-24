"""ATDD: Tradevisor actionable Telegram alerts (content, policy, dedup, expiry).

Covers ATDD-08 (exact actionable content), ATDD-09 (limit entry plan),
ATDD-41 (delivery failure/retry), ATDD-42 (expired plan), ATDD-48 (no duplicate
material alerts across sources).

No Telegram call is made: a synthetic sender captures the rendered message.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import notification_policy, tradevisor_evaluation as te
from app.services import tradevisor_notifier as notifier
from app.services.tradevisor_ingress import TradevisorSignal

NOW = datetime(2026, 10, 23, 13, 0, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification_state.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification_state.lock")
    monkeypatch.setattr(
        notifier, "load_json", notification_policy.load_json, raising=False
    )
    return tmp_path


def market(**over) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=58.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
        recent_24h_high=112.0,
        recent_24h_low=94.0,
        recent_72h_high=120.0,
        recent_72h_low=88.0,
        momentum_6h_pct=1.0,
        momentum_24h_pct=3.0,
        momentum_72h_pct=7.0,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
        rolling_24h_downside_median_pct=8.0,
        rolling_24h_downside_p75_pct=10.0,
        rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=12.0,
        rolling_72h_downside_p75_pct=16.0,
        rolling_72h_downside_p90_pct=20.0,
        ticker_last=100.0,
        execution_validation=ExecutionValidation(
            status="VALID",
            book_coverage_status="COMPLETE",
            warnings=[],
            best_bid=99.95,
            best_ask=100.05,
            mid_price=100.0,
            spread_pct=0.1,
            spread_bps=5.0,
            validation_notional_usd=250.0,
            buy_vwap=100.02,
            sell_vwap=99.98,
            buy_fully_covered=True,
            sell_fully_covered=True,
            estimated_visible_round_trip_market_drag_pct=0.10,
            recent_trade_status="FRESH",
            latest_trade_price=100.0,
            latest_trade_age_seconds=10.0,
            recent_trade_count=20,
        ),
    )
    values.update(over)
    return MarketSnapshot(**values)


def decision(**over):
    signal = TradevisorSignal(
        dedup_id="dedup-BUY-BTCUSD",
        signal="BUY",
        instrument="BTC/USD",
        market="BTCUSD",
        source_exchange="COINBASE",
        source_symbol="BTCUSD",
        timeframe_minutes=60,
        bar_opened_at=NOW - timedelta(minutes=65),
        bar_closed_at=NOW - timedelta(minutes=5),
        triggered_at=NOW - timedelta(seconds=5),
        received_at=NOW,
        signal_price=100.0,
        alert_config_version="tradevisor-1h-buy",
    )
    resolved = te.evaluate_signal(
        signal,
        now=NOW,
        settings=Settings(_env_file=None, webhook_secret="x" * 12),
        snapshot_provider=lambda _m: ("ok", market(), None),
        account_equity=10_000.0,
        active_trades=[],
    )
    # Isolate formatter/transport mechanics from the qualification policy.
    # Production evaluation correctly returns WATCH while F6 and trusted 4H
    # are missing. This synthetic, future-eligible object is used ONLY to
    # exercise the notifier's formatting, expiry, ledger and retry contracts.
    assert resolved.decision == te.DECISION_WATCH
    assert resolved.evidence["advisory_qualification"]["status"] == "SHADOW_ONLY"
    return replace(
        resolved,
        decision=te.DECISION_ENTER_NOW,
        rejection_code=None,
        reason="Synthetic notifier contract fixture; no production authorization.",
        expires_at=NOW + timedelta(minutes=90),
        evidence={
            **resolved.evidence,
            "f6_forecast_status": "SYNTHETIC_TRUSTED_FOR_NOTIFIER_TEST",
            "regime_4h": "SYNTHETIC_COMPLETED_4H_FOR_NOTIFIER_TEST",
            "advisory_qualification": {"status": "SYNTHETIC_TEST_ONLY"},
        },
    )


def telegram_settings(**over) -> SimpleNamespace:
    values = dict(
        telegram_enabled=True,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
    )
    values.update(over)
    return SimpleNamespace(**values)


class Sender:
    def __init__(self, *, delivered: bool = True) -> None:
        self.delivered = delivered
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(delivered=self.delivered, message_id=1)


# ---------------------------------------------------------------------------
# Content (ATDD-08, ATDD-09)
# ---------------------------------------------------------------------------


def test_atdd08_actionable_message_contains_every_required_field():
    message = notifier.format_tradevisor_alert(decision())
    lines = message.splitlines()
    assert lines[0] == "TRADEVISOR ALERTS"
    for required in (
        "Action: ENTER NOW",
        "Signal: Tradevisor V2 BUY",
        "O'Pip: QUALIFIED",
        "Source: COINBASE 1H",
        "Execution venue: Kraken (BTCUSD)",
        "Current price:",
        "Preferred entry:",
        "Stop:",
        "Target 1:",
        "Target 2:",
        "Capital:",
        "Planned loss:",
        "Estimated net profit T1:",
        "Estimated net profit T2:",
        "Reward/Risk:",
        "Do not chase above:",
        "Expires:",
        "Why now:",
        "Market regime: BULLISH",
        "Execution: Human approval required",
    ):
        assert required in message, required
    # Never promise a profit.
    assert "not guaranteed profits" in message


def test_atdd09_limit_entry_message_carries_the_limit_price():
    limit = replace(decision(), decision=te.DECISION_SET_LIMIT_ENTRY)
    message = notifier.format_tradevisor_alert(limit)
    assert "Action: SET LIMIT ENTRY" in message
    assert "Limit buy:" in message
    # The limit sits at the retest level (99), not at the current price (100).
    assert "Limit buy: 99" in message
    assert f"Current price: {notifier._price(limit.current_price)}" in message
    assert notifier._price(limit.plan.entry_low) != notifier._price(limit.current_price)
    assert "Do not chase above:" in message
    assert "Expires: 2026-10-23 14:30 UTC" in message


def test_non_actionable_decisions_cannot_be_formatted():
    watch = replace(decision(), decision=te.DECISION_WATCH)
    with pytest.raises(ValueError):
        notifier.format_tradevisor_alert(watch)


def test_prices_are_rendered_without_scientific_notation():
    message = notifier.format_tradevisor_alert(decision())
    assert "e-" not in message.lower().replace("expires", "")
    assert "e+" not in message.lower()


# ---------------------------------------------------------------------------
# Delivery policy (ATDD-41, 42, 48)
# ---------------------------------------------------------------------------


def test_non_actionable_decision_is_never_sent():
    sender = Sender()
    outcome = notifier.notify_tradevisor_decision(
        replace(decision(), decision=te.DECISION_WATCH),
        settings=telegram_settings(),
        now=NOW,
        sender=sender,
    )
    assert outcome.status == notifier.STATUS_NOT_ACTIONABLE
    assert sender.calls == []


def test_disabled_telegram_is_never_sent():
    sender = Sender()
    outcome = notifier.notify_tradevisor_decision(
        decision(),
        settings=telegram_settings(telegram_enabled=False),
        now=NOW,
        sender=sender,
    )
    assert outcome.status == notifier.STATUS_TELEGRAM_DISABLED
    assert sender.calls == []


def test_atdd42_expired_plan_is_never_delivered():
    sender = Sender()
    outcome = notifier.notify_tradevisor_decision(
        decision(),
        settings=telegram_settings(),
        now=NOW + timedelta(hours=3),
        sender=sender,
    )
    assert outcome.status == notifier.STATUS_EXPIRED
    assert sender.calls == []


def test_delivery_records_the_policy_reservation_and_alert_family():
    sender = Sender()
    outcome = notifier.notify_tradevisor_decision(
        decision(), settings=telegram_settings(), now=NOW, sender=sender
    )
    assert outcome.status == notifier.STATUS_DELIVERED
    assert outcome.delivered is True
    assert len(sender.calls) == 1
    call = sender.calls[0]
    assert call["alert_family"] == notifier.TRADEVISOR_ALERT_FAMILY
    assert call["event_type"] == notifier.TRADEVISOR_EVENT_TYPE
    assert call["identity"] == "TRADEVISOR:LONG:BTCUSD"
    assert call["message"].splitlines()[0] == "TRADEVISOR ALERTS"
    assert notification_policy.is_confirmed_emission(
        identity="TRADEVISOR:LONG:BTCUSD",
        event_type=notifier.TRADEVISOR_EVENT_TYPE,
        fingerprint=call["fingerprint"],
    )


def test_second_identical_recommendation_is_suppressed_by_policy():
    sender = Sender()
    first = notifier.notify_tradevisor_decision(
        decision(), settings=telegram_settings(), now=NOW, sender=sender
    )
    second = notifier.notify_tradevisor_decision(
        decision(), settings=telegram_settings(), now=NOW, sender=sender
    )
    assert first.status == notifier.STATUS_DELIVERED
    assert second.status == notifier.STATUS_SUPPRESSED_POLICY
    assert len(sender.calls) == 1


def test_atdd48_native_material_alert_suppresses_the_tradevisor_duplicate():
    notification_policy.record_emitted(
        identity="LONG:BTCUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="native-fp",
        now=NOW - timedelta(minutes=10),
    )
    sender = Sender()
    outcome = notifier.notify_tradevisor_decision(
        decision(), settings=telegram_settings(), now=NOW, sender=sender
    )
    assert outcome.status == notifier.STATUS_SUPPRESSED_NATIVE_DUPLICATE
    assert sender.calls == []


def test_atdd41_failed_delivery_releases_the_reservation_for_retry():
    failing = Sender(delivered=False)
    first = notifier.notify_tradevisor_decision(
        decision(), settings=telegram_settings(), now=NOW, sender=failing
    )
    assert first.status == notifier.STATUS_SEND_FAILED
    assert first.delivered is False

    working = Sender()
    retry = notifier.notify_tradevisor_decision(
        decision(), settings=telegram_settings(), now=NOW, sender=working
    )
    assert retry.status == notifier.STATUS_DELIVERED
    assert len(working.calls) == 1


def test_message_names_the_evidence_it_does_not_have():
    # Missing F6/4H no longer produce an actionable message. We still test
    # the evidence helper without fabricating a real qualified recommendation.
    degraded = replace(
        decision(),
        evidence={
            "regime_4h": "UNAVAILABLE",
            "f6_forecast_status": "UNAVAILABLE_NO_TRUSTED_MODEL",
        },
        decision=te.DECISION_WATCH,
    )
    line = notifier._evidence_line(degraded)
    assert "Evidence: native 1H structure, Kraken execution book" in line
    assert "unavailable: 4H regime, trusted forecast" in line
    with pytest.raises(ValueError):
        notifier.format_tradevisor_alert(degraded)
