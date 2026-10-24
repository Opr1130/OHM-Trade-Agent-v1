
"""ATDD: Tradevisor V2 ingress contract (adapter only, no execution authority).

Every case is synthetic. No real webhook, exchange, Telegram or AI call occurs.
Scenarios map to the mission's ATDD-01..ATDD-26 set for the ingress layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.tradevisor_ingress import (
    DISPOSITION_ACCEPTED,
    TradevisorConfig,
    TradevisorIngressError,
    admit,
)

BAR_OPEN = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)
BAR_CLOSE = BAR_OPEN + timedelta(hours=1)
RECEIVED = BAR_CLOSE + timedelta(seconds=5)

CONFIG = TradevisorConfig(
    interval_minutes=60,
    allowed_exchanges=frozenset({"COINBASE", "BINANCE"}),
    # Cross-venue identity is explicitly operator-approved, never inferred:
    # a Binance USDT quote is not assumed to equal a Kraken USD market.
    instruments={
        ("COINBASE", "BTCUSD"): ("BTC/USD", "XBTUSD"),
        ("BINANCE", "NEARUSDT"): ("NEAR/USD", "NEARUSD"),
    },
    alert_configs={
        "tradevisor-1h-buy": "BUY",
        "tradevisor-1h-sell": "SELL",
    },
)


def payload(**overrides):
    body = {
        "schema_version": "tradevisor-ingress-v1",
        "source": "TRADEVISOR",
        "indicator": "TRADEVISOR_V2",
        "signal": "BUY",
        "symbol": "BTCUSD",
        "exchange": "COINBASE",
        "timeframe": "60",
        "signal_price": 67000.5,
        "bar_opened_at": BAR_OPEN.isoformat(),
        "triggered_at": (BAR_CLOSE + timedelta(seconds=1)).isoformat(),
        "alert_config_version": "tradevisor-1h-buy",
    }
    body.update(overrides)
    return body


def test_atdd01_buy_accepted():
    signal = admit(payload(), config=CONFIG, received_at=RECEIVED)
    assert signal.signal == "BUY"
    assert signal.instrument == "BTC/USD"
    assert signal.market == "XBTUSD"
    assert signal.disposition == DISPOSITION_ACCEPTED
    assert signal.bar_closed_at == BAR_CLOSE
    assert signal.timeframe_minutes == 60


def test_atdd02_sell_accepted():
    signal = admit(
        payload(signal="SELL", alert_config_version="tradevisor-1h-sell"),
        config=CONFIG,
        received_at=RECEIVED,
    )
    assert signal.signal == "SELL"


def test_atdd03_trigger_before_bar_close_refused():
    with pytest.raises(TradevisorIngressError, match="TRIGGER_BEFORE_BAR_CLOSE"):
        admit(
            payload(triggered_at=(BAR_OPEN + timedelta(minutes=30)).isoformat()),
            config=CONFIG,
            received_at=RECEIVED,
        )


def test_atdd03_trigger_just_after_close_accepted():
    signal = admit(
        payload(triggered_at=(BAR_CLOSE + timedelta(seconds=30)).isoformat()),
        config=CONFIG,
        received_at=BAR_CLOSE + timedelta(seconds=60),
    )
    assert signal.bar_closed_at == BAR_CLOSE


def test_atdd04_coinbase_source_accepted():
    assert admit(payload(), config=CONFIG, received_at=RECEIVED).source_exchange == "COINBASE"


def test_atdd05_binance_source_accepted():
    signal = admit(
        payload(
            symbol="NEARUSDT",
            exchange="BINANCE",
            alert_config_version="tradevisor-1h-buy",
        ),
        config=CONFIG,
        received_at=RECEIVED,
    )
    assert signal.source_exchange == "BINANCE"
    assert signal.instrument == "NEAR/USD"


def test_atdd06_source_instrument_mapped_to_market():
    signal = admit(payload(), config=CONFIG, received_at=RECEIVED)
    assert (signal.instrument, signal.market) == ("BTC/USD", "XBTUSD")


def test_atdd25_unknown_instrument_fails_closed():
    with pytest.raises(TradevisorIngressError, match="UNSUPPORTED_INSTRUMENT"):
        admit(payload(symbol="DOGEUSD"), config=CONFIG, received_at=RECEIVED)


def test_atdd12_unknown_exchange_refused():
    with pytest.raises(TradevisorIngressError, match="UNSUPPORTED_EXCHANGE"):
        admit(payload(exchange="BYBIT"), config=CONFIG, received_at=RECEIVED)


def test_atdd13_unsupported_indicator_refused():
    with pytest.raises(TradevisorIngressError, match="SCHEMA_INVALID:indicator"):
        admit(payload(indicator="TRADEVISOR_V1"), config=CONFIG, received_at=RECEIVED)


def test_atdd13_unsupported_timeframe_refused():
    with pytest.raises(TradevisorIngressError, match="UNSUPPORTED_TIMEFRAME"):
        admit(payload(timeframe="15"), config=CONFIG, received_at=RECEIVED)


def test_atdd14_non_object_payload_refused():
    with pytest.raises(TradevisorIngressError, match="PAYLOAD_NOT_OBJECT"):
        admit(["not", "an", "object"], config=CONFIG, received_at=RECEIVED)


def test_atdd15_missing_direction_refused():
    body = payload()
    del body["signal"]
    with pytest.raises(TradevisorIngressError, match="SCHEMA_INVALID:signal"):
        admit(body, config=CONFIG, received_at=RECEIVED)


def test_atdd16_config_signal_conflict_refused():
    with pytest.raises(TradevisorIngressError, match="CONFIG_SIGNAL_CONFLICT"):
        admit(
            payload(signal="SELL", alert_config_version="tradevisor-1h-buy"),
            config=CONFIG,
            received_at=RECEIVED,
        )


def test_atdd17_unresolved_placeholder_refused():
    with pytest.raises(TradevisorIngressError, match="SCHEMA_INVALID:symbol"):
        admit(payload(symbol="{{ticker}}"), config=CONFIG, received_at=RECEIVED)


def test_atdd19_nan_price_refused():
    with pytest.raises(TradevisorIngressError, match="SCHEMA_INVALID:signal_price"):
        admit(payload(signal_price=float("nan")), config=CONFIG, received_at=RECEIVED)


def test_atdd19_negative_price_refused():
    with pytest.raises(TradevisorIngressError, match="SCHEMA_INVALID:signal_price"):
        admit(payload(signal_price=-1.0), config=CONFIG, received_at=RECEIVED)


def test_atdd20_stale_event_refused():
    with pytest.raises(TradevisorIngressError, match="STALE_EVENT"):
        admit(
            payload(),
            config=CONFIG,
            received_at=BAR_CLOSE + timedelta(hours=2),
        )


def test_atdd21_future_dated_event_refused():
    with pytest.raises(TradevisorIngressError, match="FUTURE_DATED_EVENT"):
        admit(
            payload(triggered_at=(RECEIVED + timedelta(minutes=5)).isoformat()),
            config=CONFIG,
            received_at=RECEIVED,
        )


def test_atdd22_naive_bar_time_refused():
    with pytest.raises(TradevisorIngressError, match="bar_opened_at_NOT_UTC"):
        admit(
            payload(bar_opened_at="2026-10-23T00:00:00"),
            config=CONFIG,
            received_at=RECEIVED,
        )


def test_atdd22_misaligned_bar_time_refused():
    with pytest.raises(TradevisorIngressError, match="BAR_TIME_MISALIGNED"):
        admit(
            payload(bar_opened_at=(BAR_OPEN + timedelta(minutes=17)).isoformat()),
            config=CONFIG,
            received_at=RECEIVED,
        )


def test_atdd23_dedup_identity_is_stable():
    first = admit(payload(), config=CONFIG, received_at=RECEIVED)
    second = admit(payload(), config=CONFIG, received_at=RECEIVED)
    assert first.dedup_id == second.dedup_id


def test_atdd24_replay_with_new_receipt_time_same_identity():
    first = admit(payload(), config=CONFIG, received_at=RECEIVED)
    replay = admit(
        payload(),
        config=CONFIG,
        received_at=RECEIVED + timedelta(seconds=90),
    )
    assert replay.dedup_id == first.dedup_id
    assert replay.received_at != first.received_at


def test_atdd24_distinct_bar_is_distinct_identity():
    first = admit(payload(), config=CONFIG, received_at=RECEIVED)
    next_bar = admit(
        payload(
            bar_opened_at=(BAR_OPEN + timedelta(hours=1)).isoformat(),
            triggered_at=(BAR_CLOSE + timedelta(hours=1, seconds=1)).isoformat(),
        ),
        config=CONFIG,
        received_at=RECEIVED + timedelta(hours=1),
    )
    assert next_bar.dedup_id != first.dedup_id


def test_atdd34_privilege_escalation_fields_refused():
    with pytest.raises(TradevisorIngressError, match="SCHEMA_INVALID"):
        admit(
            payload(risk_approved=True, order_permission="GRANTED"),
            config=CONFIG,
            received_at=RECEIVED,
        )


def test_atdd35_evidence_declares_no_authority():
    evidence = admit(payload(), config=CONFIG, received_at=RECEIVED).as_evidence()
    assert evidence["measurement_only"] is True
    assert evidence["trade_authority_changed"] is False
    assert evidence["strategy_family"] == "TRADEVISOR_SWING_V1"
    assert "order" not in evidence
    assert "approved" not in evidence


def test_owner_alert_template_is_accepted_verbatim():
    """The documented TradingView template substitutes strings, not numbers."""
    template = {
        "schema_version": "tradevisor-ingress-v1",
        "source": "TRADEVISOR",
        "indicator": "TRADEVISOR_V2",
        "signal": "BUY",
        "symbol": "BTCUSD",
        "exchange": "COINBASE",
        "timeframe": "60",
        "signal_price": "67000.5",  # {{close}} arrives as a quoted string
        "bar_opened_at": "2026-10-23T00:00:00Z",  # {{time}}: bar open, UTC
        "triggered_at": "2026-10-23T01:00:02Z",  # {{timenow}}: fire time
        "alert_config_version": "tradevisor-1h-buy",
    }
    signal = admit(template, config=CONFIG, received_at=BAR_CLOSE + timedelta(seconds=5))
    assert signal.signal_price == 67000.5
    assert signal.bar_opened_at == BAR_OPEN
    assert signal.bar_closed_at == BAR_CLOSE


def test_unresolved_placeholders_are_refused():
    with pytest.raises(TradevisorIngressError):
        admit(
            payload(signal_price="{{close}}"),
            config=CONFIG,
            received_at=RECEIVED,
        )
    with pytest.raises(TradevisorIngressError):
        admit(
            payload(bar_opened_at="{{time}}"),
            config=CONFIG,
            received_at=RECEIVED,
        )


def test_atdd06_usdt_and_usd_are_not_implied_equal():
    # NEARUSDT -> NEAR/USD is an explicit operator mapping in CONFIG; the
    # adapter must never derive it. An unmapped USDT symbol is refused.
    with pytest.raises(TradevisorIngressError, match="UNSUPPORTED_INSTRUMENT"):
        admit(
            payload(symbol="SOLUSDT", exchange="BINANCE"),
            config=CONFIG,
            received_at=RECEIVED,
        )

