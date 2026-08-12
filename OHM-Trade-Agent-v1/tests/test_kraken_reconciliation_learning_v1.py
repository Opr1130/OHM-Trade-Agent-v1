from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exchanges.kraken_private import KrakenPermissionError, KrakenPrivateClient
from app.services.active_trade_registry import ActiveTrade
from app.services import execution_learning_registry as learning
from app.services import kraken_reconciliation as reconciliation


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
