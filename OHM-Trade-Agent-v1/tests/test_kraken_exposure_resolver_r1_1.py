from types import SimpleNamespace

import pytest

from app.services.active_trade_registry import ActiveTrade
from app.services.kraken_exposure_resolver import KrakenExposureResolver


class FakePrivate:
    enabled = True

    def __init__(self, *, balances):
        self._balances = balances

    def assert_read_only(self):
        return SimpleNamespace(is_read_only=True)

    def get_balance(self):
        return dict(self._balances)

    def get_open_positions(self):
        return {}


class FakePublic:
    def get_asset_pairs(self):
        return {
            "SOLUSD": {
                "altname": "SOLUSD",
                "base": "SOL",
                "quote": "ZUSD",
            }
        }

    def get_tickers(self, pairs):
        return {"SOLUSD": {"last": 100.0}}


def managed_trade(*, capital):
    return ActiveTrade(
        symbol="SOLUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_level="medium",
        direction="LONG",
        trade_id=f"T-{capital}",
        margin_leverage=1.0,
        capital=capital,
    )


def test_managed_spot_suppresses_only_lifecycle_quantity(monkeypatch):
    monkeypatch.setenv("OPIP_PROTECTION_MIN_UNMANAGED_NOTIONAL_USD", "25")
    resolved = KrakenExposureResolver(
        private_client=FakePrivate(balances={"SOL": 2.0}),
        public_client=FakePublic(),
        trade_loader=lambda: [managed_trade(capital=50.0)],
    ).resolve()

    managed = [row for row in resolved.exposures if row.status == "VERIFIED_MANAGED"]
    unmanaged = [row for row in resolved.exposures if row.status == "VERIFIED_UNMANAGED"]

    assert len(managed) == 1
    assert len(unmanaged) == 1
    assert unmanaged[0].symbol == "SOLUSD"
    assert unmanaged[0].observed_quantity == pytest.approx(1.5)
    assert unmanaged[0].notional_usd == pytest.approx(150.0)


def test_multiple_managed_spot_lifecycles_subtract_additively(monkeypatch):
    monkeypatch.setenv("OPIP_PROTECTION_MIN_UNMANAGED_NOTIONAL_USD", "25")
    resolved = KrakenExposureResolver(
        private_client=FakePrivate(balances={"SOL": 3.0}),
        public_client=FakePublic(),
        trade_loader=lambda: [
            managed_trade(capital=50.0),
            managed_trade(capital=75.0),
        ],
    ).resolve()

    unmanaged = [row for row in resolved.exposures if row.status == "VERIFIED_UNMANAGED"]

    assert len(unmanaged) == 1
    assert unmanaged[0].observed_quantity == pytest.approx(1.75)
    assert unmanaged[0].notional_usd == pytest.approx(175.0)


def test_legacy_unsized_lifecycle_retains_verified_full_balance_behavior():
    trade = managed_trade(capital=None)
    resolved = KrakenExposureResolver(
        private_client=FakePrivate(balances={"SOL": 2.0}),
        public_client=FakePublic(),
        trade_loader=lambda: [trade],
    ).resolve()

    assert len([row for row in resolved.exposures if row.status == "VERIFIED_MANAGED"]) == 1
    assert [row for row in resolved.exposures if row.status == "VERIFIED_UNMANAGED"] == []
