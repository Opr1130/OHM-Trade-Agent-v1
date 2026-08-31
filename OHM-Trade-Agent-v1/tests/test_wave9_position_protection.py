from types import SimpleNamespace

import pytest

from app.services.kraken_exposure_resolver import (
    ExposureResolution,
    KrakenExposureResolver,
    ResolvedExposure,
)
from app.services.position_materiality import refine_protection_action
from app.services.kraken_position_verification import verify_trade_against_snapshot
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
        private_client=FakePrivate(
            balances={},
            positions={
                "P-LONG": {
                    "pair": "SOLUSD",
                    "type": "buy",
                    "vol": "1.0",
                    "vol_closed": "0",
                }
            },
        ),
        public_client=FakePublic(),
        trade_loader=lambda: [
            ActiveTrade(
                symbol="SOLUSD",
                entry_price=100.0,
                stop_price=95.0,
                target_1=110.0,
                target_2=120.0,
                risk_level="medium",
                direction="LONG",
                trade_id="T-LONG-MARGIN",
                capital=100.0,
                margin_leverage=2.0,
            )
        ],
    )

    resolved = resolver.resolve()

    assert resolved.exposures[0].status == "VERIFIED_MANAGED"
    assert resolved.exposures[0].trade is not None


def test_short_positions_resolve_managed_and_unmanaged():
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(
            balances={},
            positions={
                "P-SOL": {
                    "pair": "SOLUSD",
                    "type": "sell",
                    "vol": "1.0",
                    "vol_closed": "0",
                },
                "P-ETH": {
                    "pair": "ETHUSD",
                    "type": "sell",
                    "vol": "2.0",
                    "vol_closed": "0",
                },
            },
        ),
        public_client=FakePublic(),
        trade_loader=lambda: [trade(direction="SHORT")],
    )

    resolved = resolver.resolve()

    sol = [e for e in resolved.exposures if e.symbol == "SOLUSD"][0]
    eth = [e for e in resolved.exposures if e.symbol == "ETHUSD"][0]
    assert sol.status == "VERIFIED_MANAGED"
    assert sol.observed_quantity == pytest.approx(1.0)
    assert eth.status == "VERIFIED_UNMANAGED"
    assert eth.direction == "SHORT"
    assert eth.observed_quantity == pytest.approx(2.0)


def test_unpriced_non_cash_holding_is_never_silently_dropped():
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(balances={"ABC": 3.0}),
        public_client=FakePublic(pairs={}),
        trade_loader=lambda: [],
    )

    resolved = resolver.resolve()

    assert resolved.coverage_complete is False
    assert len(resolved.exposures) == 1
    exposure = resolved.exposures[0]
    assert exposure.status == "VERIFIED_UNMANAGED"
    assert exposure.symbol == "ABC"
    assert exposure.observed_quantity == pytest.approx(3.0)
    assert "no USD/stable-quote pair" in exposure.reason


def test_managed_usdc_pair_does_not_duplicate_as_unmanaged():
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(balances={"SOL": 2.0}),
        public_client=FakePublic(),
        trade_loader=lambda: [trade(symbol="SOLUSDC")],
    )

    resolved = resolver.resolve()

    managed = [e for e in resolved.exposures if e.status == "VERIFIED_MANAGED"]
    unmanaged = [e for e in resolved.exposures if e.status == "VERIFIED_UNMANAGED"]
    assert len(managed) == 1
    assert unmanaged == []


def test_unmanaged_notification_failure_does_not_stop_later_managed_protection(monkeypatch):
    settings = SimpleNamespace(
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    managed_trade = trade()
    resolution = ExposureResolution(
        exposures=(
            ResolvedExposure(
                status="VERIFIED_UNMANAGED",
                symbol="ABC",
                direction="LONG",
                observed_quantity=1.0,
                reason="unmanaged",
            ),
            ResolvedExposure(
                status="VERIFIED_MANAGED",
                symbol="SOLUSD",
                direction="LONG",
                observed_quantity=1.0,
                reason="managed",
                trade=managed_trade,
            ),
        ),
        coverage_complete=True,
    )
    monkeypatch.setattr(active_trade_monitor_runner, "get_settings", lambda: settings)
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "_notify_unmanaged_holding",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("telegram broke")),
    )
    checked = []
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "monitor_trade",
        lambda trade: checked.append(trade.symbol) or result(action="HOLD"),
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "update_active_observation",
        lambda trade, current_price: {"mfe_pct": 0.0},
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "refine_protection_action",
        lambda trade, monitor_result, observation: monitor_result,
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "send_monitor_update",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "detect_emergency_move",
        lambda trade: SimpleNamespace(triggered=False),
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "send_emergency_alert",
        lambda **kwargs: False,
    )

    summary = active_trade_monitor_runner.run_active_trade_monitor()

    assert checked == ["SOLUSD"]
    assert summary.checked == 1
    assert any("unmanaged-holding notification failed" in item for item in summary.failures)


def test_pair_without_ticker_price_marks_exposure_coverage_incomplete():
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
            prices={},
        ),
        trade_loader=lambda: [],
    )

    resolved = resolver.resolve()

    assert resolved.coverage_complete is False
    assert "pricing unavailable" in resolved.reason.lower()
    exposure = resolved.exposures[0]
    assert exposure.status == "VERIFIED_UNMANAGED"
    assert exposure.notional_usd is None


def test_malformed_open_position_degrades_coverage():
    resolver = KrakenExposureResolver(
        private_client=FakePrivate(
            balances={},
            positions={"BROKEN": "not-a-position-row"},
        ),
        public_client=FakePublic(),
        trade_loader=lambda: [],
    )

    resolved = resolver.resolve()

    assert resolved.coverage_complete is False
    assert "malformed open position row" in resolved.reason


def test_leveraged_long_does_not_fallback_to_unrelated_spot_balance():
    margin_trade = ActiveTrade(
        symbol="SOLUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_level="medium",
        direction="LONG",
        trade_id="T-MARGIN-LONG",
        capital=100.0,
        margin_leverage=2.0,
    )

    verification = verify_trade_against_snapshot(
        margin_trade,
        balances={"SOL": 5.0},
        positions={},
    )

    assert verification.status == "ABSENT"
    assert "leveraged long" in verification.reason


def test_degraded_alert_failure_does_not_stop_managed_protection(monkeypatch):
    settings = SimpleNamespace(
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    managed_trade = trade()
    resolution = ExposureResolution(
        exposures=(
            ResolvedExposure(
                status="VERIFIED_MANAGED",
                symbol="SOLUSD",
                direction="LONG",
                observed_quantity=1.0,
                reason="managed",
                trade=managed_trade,
            ),
        ),
        coverage_complete=False,
        reason="pricing coverage incomplete",
    )
    monkeypatch.setattr(active_trade_monitor_runner, "get_settings", lambda: settings)
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "_notify_monitor_degraded",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("telegram broke")),
    )
    checked = []
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "monitor_trade",
        lambda trade: checked.append(trade.symbol) or result(action="HOLD"),
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "update_active_observation",
        lambda trade, current_price: {"mfe_pct": 0.0},
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "refine_protection_action",
        lambda trade, monitor_result, observation: monitor_result,
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "send_monitor_update",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "detect_emergency_move",
        lambda trade: SimpleNamespace(triggered=False),
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "send_emergency_alert",
        lambda **kwargs: False,
    )

    summary = active_trade_monitor_runner.run_active_trade_monitor()

    assert checked == ["SOLUSD"]
    assert summary.checked == 1
    assert any(
        "degraded-monitor notification failed" in failure
        for failure in summary.failures
    )


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