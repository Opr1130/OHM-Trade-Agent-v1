"""Read-only Kraken balance-identity pricing for exposure coverage.

Kraken balance extensions (.S/.B/.M/.F/.P/.T) and the ETH2 staking receipt are
balance identities, not trading symbols. These tests prove the exposure
resolver prices only the documented underlying spot asset and still fails
closed on an unrecognized decoration.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exchanges.kraken_identity import (
    balance_underlying_asset,
    canonicalize_asset,
    canonicalize_pair,
)
from app.services.kraken_exposure_resolver import KrakenExposureResolver
from app.services.protection_health import (
    REASON_COVERAGE_INCOMPLETE,
    REASON_UNMANAGED_EXPOSURE,
    evaluate_protection_health,
)


class _Private:
    def __init__(self, balances):
        self.enabled = True
        self._balances = balances

    def assert_read_only(self):
        return SimpleNamespace(is_read_only=True)

    def get_balance(self):
        return dict(self._balances)

    def get_open_positions(self):
        return {}


class _Public:
    def __init__(self, pairs, prices):
        self._pairs = pairs
        self._prices = prices

    def get_asset_pairs(self):
        return dict(self._pairs)

    def get_tickers(self, pairs):
        return {
            pair: {"last": self._prices[pair]}
            for pair in pairs
            if pair in self._prices
        }


def _usd_pair(base: str, price: float) -> tuple[dict, dict]:
    pair = f"{base}USD"
    return (
        {
            pair: {
                "status": "online",
                "altname": pair,
                "base": base,
                "quote": "ZUSD",
            }
        },
        {pair: price},
    )


@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("raw", "underlying"),
    [
        ("ADA.S", "ADA"),
        ("ETH2.S", "ETH"),
        ("ETH2", "ETH"),
        ("SEI.B", "SEI"),
        ("SUI.B", "SUI"),
        ("TAO.B", "TAO"),
        ("XTZ.S", "XTZ"),
        ("BTC.M", "BTC"),
        ("DOT.P", "DOT"),
        ("USDT.F", "USDT"),
    ],
)
def test_ac_023_documented_balance_identities_map_to_underlying(raw, underlying):
    """ATDD-RELEASE-PIPELINE-v1/AC-023: a documented Kraken balance extension or
    the ETH2 staking receipt normalizes to its underlying spot asset.
    """
    assert balance_underlying_asset(raw) == underlying
    # Generic pair parsing is unchanged by the balance helper.
    assert canonicalize_asset(raw) == raw
    assert canonicalize_pair("XXBTZUSD") == "BTCUSD"


@pytest.mark.acceptance
def test_ac_023_decorated_balance_prices_on_the_underlying_usd_pair():
    """ATDD-RELEASE-PIPELINE-v1/AC-023: each proven decorated identity prices
    through the underlying preferred USD pair and computes notional as
    quantity times that price.
    """
    pairs: dict = {}
    prices: dict = {}
    for base, price in (("ADA", 0.40), ("ETH", 2500.0), ("SEI", 0.20), ("SUI", 2.0), ("TAO", 300.0)):
        pair_row, price_row = _usd_pair(base, price)
        pairs.update(pair_row)
        prices.update(price_row)
    balances = {
        "ADA.S": "2",
        "ETH2.S": "0.1",
        "SEI.B": "10",
        "SUI.B": "4",
        "TAO.B": "0.5",
    }
    resolver = KrakenExposureResolver(
        private_client=_Private(balances),
        public_client=_Public(pairs, prices),
        trade_loader=lambda: [],
        minimum_unmanaged_notional_usd=0.0,
    )
    resolved = resolver.resolve()
    assert resolved.coverage_complete is True
    by_symbol = {row.symbol: row for row in resolved.exposures}
    expected = {
        "ADAUSD": (2.0, 0.80),
        "ETHUSD": (0.1, 250.0),
        "SEIUSD": (10.0, 2.0),
        "SUIUSD": (4.0, 8.0),
        "TAOUSD": (0.5, 150.0),
    }
    assert set(by_symbol) == set(expected)
    for symbol, (quantity, notional) in expected.items():
        row = by_symbol[symbol]
        assert row.status == "VERIFIED_UNMANAGED"
        assert row.observed_quantity == pytest.approx(quantity)
        assert row.notional_usd == pytest.approx(notional)
    decision = evaluate_protection_health(
        resolved.exposures,
        coverage_complete=resolved.coverage_complete,
        incidents_healthy=True,
    )
    assert REASON_UNMANAGED_EXPOSURE in decision.reason_codes
    assert decision.admissions_suspended is True
    assert decision.state != "HEALTHY"


@pytest.mark.acceptance
def test_ac_023_unknown_decoration_stays_unpriced_and_coverage_incomplete():
    """ATDD-RELEASE-PIPELINE-v1/AC-023: an unrecognized decorated balance form
    is not stripped and remains an unpriced holding, so coverage stays
    incomplete.
    """
    assert balance_underlying_asset("ADA.Z") == "ADA.Z"
    assert balance_underlying_asset("SEI.B.X") == "SEI.B.X"
    pairs, prices = _usd_pair("ADA", 0.40)
    resolver = KrakenExposureResolver(
        private_client=_Private({"ADA.Z": "3", "ADA.S": "1"}),
        public_client=_Public(pairs, prices),
        trade_loader=lambda: [],
        minimum_unmanaged_notional_usd=0.0,
    )
    resolved = resolver.resolve()
    assert resolved.coverage_complete is False
    assert "ADA.Z" in resolved.reason
    unpriced = [row for row in resolved.exposures if row.symbol == "ADA.Z"]
    assert len(unpriced) == 1
    assert unpriced[0].status == "VERIFIED_UNMANAGED"
    assert unpriced[0].notional_usd is None
    priced = [row for row in resolved.exposures if row.symbol == "ADAUSD"]
    assert len(priced) == 1
    assert priced[0].notional_usd == pytest.approx(0.40)
    decision = evaluate_protection_health(
        resolved.exposures,
        coverage_complete=resolved.coverage_complete,
        incidents_healthy=True,
    )
    assert REASON_COVERAGE_INCOMPLETE in decision.reason_codes
    assert decision.state == "UNAVAILABLE"
    assert decision.admissions_suspended is True
