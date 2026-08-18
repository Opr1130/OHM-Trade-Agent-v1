from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.exchanges import kraken_private
from app.exchanges.kraken_private import KrakenPermissionError, KrakenPrivateClient
from app.services.active_trade_registry import ActiveTrade
from app.services import (
    active_trade_registry,
    execution_learning_registry as learning,
    order_intent_registry,
    pending_setup_registry,
    trade_outcome_registry,
)
from app.services import kraken_reconciliation as reconciliation
from app.services.order_intent_registry import OrderIntent
from app.services.pending_setup_registry import PendingSetup


def test_private_client_rejects_dangerous_permissions(monkeypatch):
    client = KrakenPrivateClient(api_key="key", api_secret="c2VjcmV0")
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, params=None: {
            "apiKeyName": "ohm-test",
            "permissions": ["query-funds", "query-open-trades", "modify-trades"],
        },
    )
    with pytest.raises(KrakenPermissionError):
        client.assert_read_only()


def test_private_client_accepts_read_only_permissions(monkeypatch):
    client = KrakenPrivateClient(api_key="key", api_secret="c2VjcmV0")
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, params=None: {
            "apiKeyName": "ohm-read-only",
            "permissions": ["query-funds", "query-open-trades", "query-closed-trades"],
        },
    )
    info = client.assert_read_only()
    assert info.is_read_only is True
    assert info.name == "ohm-read-only"


def test_private_client_signs_exact_post_body(monkeypatch):
    client = KrakenPrivateClient(api_key="key", api_secret="c2VjcmV0")
    monkeypatch.setattr(client, "_nonce", lambda: 123456789)
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"error": [], "result": {"open": {}}}

    def fake_post(url, *, content, headers, timeout):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return Response()

    monkeypatch.setattr(kraken_private.httpx, "post", fake_post)
    client.get_open_orders()

    assert captured["content"] == b"trades=true&nonce=123456789"
    expected = client._signature(
        "/0/private/OpenOrders",
        "trades=true&nonce=123456789",
        123456789,
    )
    assert captured["headers"]["API-Sign"] == expected


def _fake_client():
    class FakeClient:
        enabled = True

        def assert_read_only(self):
            return SimpleNamespace(name="ohm-read-only")

        def get_balance(self):
            return {"CSPR": 0.0, "ZUSD": 100.0}

        def get_open_orders(self):
            return {}

        def get_trades_history(self, *, start=None):
            return {
                "T-CSPR-CLOSE": {
                    "pair": "CSPRUSD",
                    "type": "sell",
                    "price": "0.0022",
                    "vol": "11000",
                    "fee": "0.09",
                    "time": 1786377600.0,
                }
            }

        def get_open_positions(self):
            return {}

    return FakeClient()


def test_reconciliation_observe_detects_closed_spot_trade_without_mutating(monkeypatch):
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "observe")
    trade = ActiveTrade(
        symbol="CSPRUSD",
        entry_price=0.0019712,
        stop_price=0.0019,
        target_1=0.00205,
        target_2=0.00212,
        risk_level="medium",
        trade_id="OHM-CSPR",
        capital=21.0,
        opened_at="2026-08-10T11:55:35+00:00",
    )
    monkeypatch.setattr(reconciliation, "get_active_trades", lambda: [trade])
    monkeypatch.setattr(reconciliation, "get_live_order_intents", lambda: [])
    closed = []
    monkeypatch.setattr(reconciliation, "close_trade", lambda *a, **k: closed.append((a, k)))
    monkeypatch.setattr(reconciliation, "record_execution_event", lambda *a, **k: {})

    result = reconciliation.reconcile_kraken_account(_fake_client())

    assert result.status == "OK"
    assert result.would_close == ("CSPRUSD",)
    assert result.closed == ()
    assert closed == []


def test_reconciliation_apply_terminalizes_confirmed_closed_spot_trade(monkeypatch):
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "apply")
    trade = ActiveTrade(
        symbol="CSPRUSD",
        entry_price=0.0019712,
        stop_price=0.0019,
        target_1=0.00205,
        target_2=0.00212,
        risk_level="medium",
        trade_id="OHM-CSPR",
        capital=21.0,
        opened_at="2026-08-10T11:55:35+00:00",
    )
    monkeypatch.setattr(reconciliation, "get_active_trades", lambda: [trade])
    monkeypatch.setattr(reconciliation, "get_live_order_intents", lambda: [])
    calls = []
    monkeypatch.setattr(reconciliation, "close_trade", lambda *a, **k: calls.append((a, k)) or True)
    monkeypatch.setattr(reconciliation, "record_execution_event", lambda *a, **k: {})

    result = reconciliation.reconcile_kraken_account(_fake_client())

    assert result.closed == ("CSPRUSD",)
    assert calls[0][0] == ("CSPRUSD",)
    assert calls[0][1]["reason"] == "kraken_reconciled_close"
    assert calls[0][1]["actual_exit_fee"] == pytest.approx(0.09)
    assert calls[0][1]["close_price"] == pytest.approx(0.0022)


def test_reconciliation_apply_terminalizes_stale_trade_with_verified_zero_balance(monkeypatch):
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "apply")
    trade = ActiveTrade(
        symbol="WLDUSD",
        entry_price=0.3458,
        stop_price=0.3384,
        target_1=0.36,
        target_2=0.38,
        risk_level="medium",
        trade_id="OHM-WLD",
        capital=100.0,
        opened_at="2026-08-17T10:00:00+00:00",
    )
    monkeypatch.setattr(reconciliation, "get_active_trades", lambda: [trade])
    monkeypatch.setattr(reconciliation, "get_live_order_intents", lambda: [])
    monkeypatch.setattr(reconciliation, "record_execution_event", lambda *a, **k: {})
    calls = []
    monkeypatch.setattr(
        reconciliation,
        "close_trade",
        lambda *a, **k: calls.append((a, k)) or True,
    )

    class Client:
        enabled = True

        def assert_read_only(self):
            return SimpleNamespace(name="ohm-read-only")

        def get_balance(self):
            return {"WLD": 0.0, "ZUSD": 500.0}

        def get_open_orders(self):
            return {}

        def get_trades_history(self, *, start=None):
            return {}

        def get_open_positions(self):
            return {}

    result = reconciliation.reconcile_kraken_account(Client())

    assert result.would_close == ("WLDUSD",)
    assert result.closed == ("WLDUSD",)
    assert calls == [
        (("WLDUSD",), {"reason": "kraken_verified_no_position"})
    ]


def test_execution_learning_derives_idea_order_fill_timing(monkeypatch, tmp_path):
    monkeypatch.setattr(learning, "EXECUTION_FILE", tmp_path / "execution_learning.json")
    monkeypatch.setattr(learning, "LOCK_FILE", tmp_path / ".execution_learning.lock")
    row = learning.record_execution_event(
        "OHM-ABC",
        symbol="ABCUSD",
        direction="LONG",
        signal_at="2026-08-12T10:00:00+00:00",
        order_placed_at="2026-08-12T10:02:00+00:00",
        first_fill_at="2026-08-12T10:03:00+00:00",
        full_fill_at="2026-08-12T10:04:00+00:00",
        limit_price=10.0,
        fill_price=10.02,
    )
    assert row["idea_to_order_seconds"] == 120.0
    assert row["order_to_first_fill_seconds"] == 60.0
    assert row["idea_to_full_fill_seconds"] == 240.0
    assert row["fill_vs_limit_pct"] == pytest.approx(0.2)


def _isolate_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr(active_trade_registry, "TRADE_FILE", tmp_path / "active.json")
    monkeypatch.setattr(order_intent_registry, "ORDER_FILE", tmp_path / "intents.json")
    monkeypatch.setattr(order_intent_registry, "LOCK_FILE", tmp_path / ".intents.lock")
    monkeypatch.setattr(pending_setup_registry, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(trade_outcome_registry, "OUTCOME_FILE", tmp_path / "outcomes.json")


def test_apply_reconciliation_activates_verified_signal_fill_without_buttons(
    monkeypatch,
    tmp_path,
):
    _isolate_lifecycle(monkeypatch, tmp_path)
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "apply")
    monkeypatch.setattr(reconciliation, "record_execution_event", lambda *a, **k: {})
    trade_id = "OHM-ADA-AUTO"
    pending_setup_registry.add_pending_setup(
        PendingSetup(
            symbol="ADAUSD",
            direction="LONG",
            entry_low=0.99,
            entry_high=1.0,
            chase_limit=1.01,
            stop_price=0.95,
            target_1=1.1,
            target_2=1.2,
            risk_level="low",
            confidence=90,
            trade_id=trade_id,
        )
    )
    intent = order_intent_registry.register_order_intent(
        OrderIntent(
            symbol="ADAUSD",
            direction="LONG",
            limit_price=1.0,
            capital=100.0,
            stop_price=0.95,
            target_1=1.1,
            target_2=1.2,
            risk_level="low",
            trade_id=trade_id,
            source="ohm_actionable_signal",
        )
    )
    fill_time = time.time() + 1

    class Client:
        enabled = True

        def assert_read_only(self):
            return SimpleNamespace(name="ohm-read-only")

        def get_balance(self):
            return {"ZUSD": 1000.0}

        def get_open_orders(self):
            return {}

        def get_trades_history(self, *, start=None):
            return {
                "TRADE-ADA": {
                    "ordertxid": "KRAKEN-ADA-ORDER",
                    "pair": "ADAUSD",
                    "type": "buy",
                    "price": "1.0",
                    "vol": "100.0",
                    "fee": "0.25",
                    "time": fill_time,
                }
            }

        def get_open_positions(self):
            return {}

    result = reconciliation.reconcile_kraken_account(Client())

    assert result.filled == (trade_id,)
    stored_intent = order_intent_registry.get_order_intent(trade_id)
    assert stored_intent.status == "FILLED"
    assert stored_intent.exchange_order_txid == "KRAKEN-ADA-ORDER"
    active = active_trade_registry.get_trade("ADAUSD")
    assert active is not None
    assert active.trade_id == trade_id
    assert active.entry_price == pytest.approx(1.0)
    assert pending_setup_registry.get_pending_setups() == []
    outcome = trade_outcome_registry.get_outcomes()[0]
    assert outcome["entry_price_source"] == "kraken_reconciliation_fill"


def test_unbound_reconciliation_rejects_ambiguous_similar_orders():
    now = time.time()
    intent = SimpleNamespace(
        symbol="ADAUSD",
        direction="LONG",
        created_at="1970-01-01T00:00:00+00:00",
        limit_price=1.0,
        capital=100.0,
        margin_leverage=1.0,
    )
    trades = {
        "T1": {
            "ordertxid": "ORDER-1",
            "pair": "ADAUSD",
            "type": "buy",
            "price": "1.0",
            "vol": "100",
            "time": now,
        },
        "T2": {
            "ordertxid": "ORDER-2",
            "pair": "ADAUSD",
            "type": "buy",
            "price": "1.0",
            "vol": "100",
            "time": now + 1,
        },
    }

    rows, order_txid = reconciliation._verified_unbound_entry_fills(
        trades,
        intent=intent,
    )

    assert rows == []
    assert order_txid is None
