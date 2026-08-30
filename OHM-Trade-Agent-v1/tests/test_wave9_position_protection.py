from types import SimpleNamespace

import pytest

from app.services.kraken_exposure_resolver import (
    ExposureResolution,
    KrakenExposureResolver,
    ResolvedExposure,
)
from app.opip.protection.position_materiality import refine_protection_action
from app.services import active_trade_monitor_runner, trade_monitor, trade_monitor_notifier
from app.services.active_trade_registry import ActiveTrade
from app.services.trade_monitor import TradeMonitorResult


class FakePrivate:
    def __init__(self, *, balances=None, positions=None, enabled=True, fail=False):
        self.enabled = enabled
        self._balances = balances or {}
        self._positions = positions or {}
        self._fail = fail

    def assert_read_only(self):
        if self._fail:
            raise RuntimeError("private unavailable")
        return SimpleNamespace(is_read_only=True)

    def get_balance(self):
        if self._fail:
            raise RuntimeError("private unavailable")
        return dict(self._balances)

    def get_open_positions(self):
        if self._fail:
            raise RuntimeError("private unavailable")
        return dict(self._positions)


class FakePublic:
    def __init__(self, *, pairs=None, prices=None):
        self._pairs = pairs or {}
        self._prices = prices or {}

    def get_asset_pairs(self):
        return dict(self._pairs)

    def get_tickers(self, pairs):
        result = {}
        for pair in pairs:
            if pair in self._prices:
                result[pair] = {"last": self._prices[pair]}
        return result


def trade(symbol="SOLUSD", *, direction="LONG"):
    if direction == "SHORT":
        return ActiveTrade(
            symbol=symbol,
            entry_price=100.0,
            stop_price=105.0,
            target_1=95.0,
            target_2=90.0,
            risk_level="medium",
            direction="SHORT",
            trade_id="T-SHORT",
        )
    return ActiveTrade(
        symbol=symbol,
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_level="medium",
        direction="LONG",
        trade_id="T-LONG",
    )


def result(*, action="HOLD", price=100.0, pnl=0.0, reasons=None):
    return TradeMonitorResult(
        symbol="SOLUSD",
        action=action,
        current_price=price,
        unrealized_pct=pnl,
        reasons=list(reasons or ["Trade structure remains healthy"]),
    )


def test_kraken_first_discovers_spot_holding_without_registry(monkeypatch):
    monkeypatch.setenv("OPIP_PROTECTION_MIN_UNMANAGED_NOTIONAL_USD", "25")
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(balances={"SOL": 2.0}),
        public_client=FakePublic(
            pairs={
                "SOLUSD": {
                    "status": "online",
                    "altname": "SOLUSD",
                    "base": "SOL",
                    "quote": "ZUSD",
                }
            },
            prices={"SOLUSD": 100.0},
        ),
        trade_loader=lambda: [],
    )

    resolved = resolver.resolve()

    assert resolved.coverage_complete is True
    assert len(resolved.exposures) == 1
    exposure = resolved.exposures[0]
    assert exposure.status == "VERIFIED_UNMANAGED"
    assert exposure.symbol == "SOLUSD"
    assert exposure.observed_quantity == pytest.approx(2.0)
    assert exposure.notional_usd == pytest.approx(200.0)


def test_registry_trade_absent_on_kraken_is_not_verified():
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(balances={}),
        public_client=FakePublic(),
        trade_loader=lambda: [trade()],
    )

    resolved = resolver.resolve()

    assert len(resolved.exposures) == 1
    assert resolved.exposures[0].status == "ABSENT"


def test_kraken_unavailable_never_becomes_absent():
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(enabled=False),
        public_client=FakePublic(),
        trade_loader=lambda: [trade()],
    )

    resolved = resolver.resolve()

    assert resolved.coverage_complete is False
    assert resolved.exposures[0].status == "UNKNOWN"


def test_kraken_managed_trade_is_verified():
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(balances={"SOL": 1.0}),
        public_client=FakePublic(),
        trade_loader=lambda: [trade()],
    )

    resolved = resolver.resolve()

    assert resolved.exposures[0].status == "VERIFIED_MANAGED"
    assert resolved.exposures[0].trade is not None


def test_mfe_giveback_promotes_hold_to_warning():
    t = trade()
    base = result(action="HOLD", price=104.0, pnl=4.0)
    observation = {"mfe_pct": 8.0}

    refined = refine_protection_action(t, base, observation)

    assert refined.action == "WARNING"
    assert "Profit protection" in refined.reasons[0]


def test_adverse_progress_promotes_hold_to_warning():
    t = trade()
    refined = refine_protection_action(
        t,
        result(action="HOLD", price=96.0, pnl=-4.0),
        {"mfe_pct": 0.0},
    )

    assert refined.action == "WARNING"
    assert "stop-risk budget" in refined.reasons[0]


def test_limited_history_still_enforces_stop(monkeypatch):
    t = trade()

    class FakeKraken:
        def get_ohlc(self, symbol, interval=60):
            candle = SimpleNamespace(close=94.0, volume=1.0)
            return [candle for _ in range(5)]

    monkeypatch.setattr(trade_monitor, "KrakenClient", FakeKraken)

    monitored = trade_monitor.monitor_trade(t)

    assert monitored.action == "EXIT_NOW"
    assert monitored.current_price == pytest.approx(94.0)
    assert any("Limited OHLC history" in reason for reason in monitored.reasons)


def test_hold_is_silent(monkeypatch, tmp_path):
    t = trade()
    monkeypatch.setattr(
        trade_monitor_notifier,
        "STATE_FILE",
        tmp_path / "trade_monitor_state.json",
    )
    sent = []
    monkeypatch.setattr(
        trade_monitor_notifier,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs) or SimpleNamespace(delivered=True, message_id=1),
    )

    assert not trade_monitor_notifier.send_monitor_update(
        t, result(action="HOLD"), "token", "chat"
    )
    assert sent == []


def test_same_warning_realerts_only_after_material_risk_progress(monkeypatch, tmp_path):
    t = trade()
    monkeypatch.setattr(
        trade_monitor_notifier,
        "STATE_FILE",
        tmp_path / "trade_monitor_state.json",
    )
    monkeypatch.setattr(trade_monitor_notifier, "should_emit", lambda **kwargs: True)
    monkeypatch.setattr(trade_monitor_notifier, "record_emitted", lambda **kwargs: None)
    monkeypatch.setattr(
        trade_monitor_notifier,
        "record_telegram_suppression",
        lambda **kwargs: None,
    )
    sent = []
    monkeypatch.setattr(
        trade_monitor_notifier,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs) or SimpleNamespace(delivered=True, message_id=len(sent) + 1),
    )

    warning_1 = result(
        action="WARNING",
        price=98.5,
        pnl=-1.5,
        reasons=["Price lost EMA20", "MACD turned bearish"],
    )
    warning_same = result(
        action="WARNING",
        price=98.2,
        pnl=-1.8,
        reasons=["Price lost EMA20", "MACD turned bearish"],
    )
    warning_worse = result(
        action="WARNING",
        price=97.0,
        pnl=-3.0,
        reasons=["Price lost EMA20", "MACD turned bearish"],
    )

    assert trade_monitor_notifier.send_monitor_update(t, warning_1, "token", "chat")
    assert not trade_monitor_notifier.send_monitor_update(t, warning_same, "token", "chat")
    assert trade_monitor_notifier.send_monitor_update(t, warning_worse, "token", "chat")
    assert len(sent) == 2


def test_runner_surfaces_unmanaged_even_without_local_active_trade(monkeypatch):
    settings = SimpleNamespace(telegram_bot_token="token", telegram_chat_id="chat")
    monkeypatch.setattr(active_trade_monitor_runner, "get_settings", lambda: settings)
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(
            resolve=lambda: ExposureResolution(
                exposures=(
                    ResolvedExposure(
                        status="VERIFIED_UNMANAGED",
                        symbol="SOLUSD",
                        direction="LONG",
                        observed_quantity=2.0,
                        reason="unmanaged",
                        notional_usd=200.0,
                    ),
                ),
                coverage_complete=True,
            )
        ),
    )
    notified = []
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "_notify_unmanaged_holding",
        lambda **kwargs: notified.append(kwargs["exposure"]) or True,
    )

    summary = active_trade_monitor_runner.run_active_trade_monitor()

    assert summary.active_trades == 0
    assert summary.positions_unmanaged == 1
    assert len(notified) == 1