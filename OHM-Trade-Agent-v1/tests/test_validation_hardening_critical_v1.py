from __future__ import annotations

from pathlib import Path

import pytest

from app.exchanges.kraken_identity import canonicalize_asset, canonicalize_pair
from app.scanner.models import MarketSnapshot
from app.services.active_trade_registry import ActiveTrade
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.kraken_position_verification import KrakenPositionVerifier
from app.services.kraken_reconciliation import _balance_for_asset, _matching_fills
from app.services.registry_io import RegistryCorruptionError, load_json


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("XXBTZUSD", "BTCUSD"),
        ("XETHZUSD", "ETHUSD"),
        ("XXRPZUSD", "XRPUSD"),
        ("XXLMZUSD", "XLMUSD"),
        ("XLTCZUSD", "LTCUSD"),
        ("XXDGZUSD", "DOGEUSD"),
        ("XZECZUSD", "ZECUSD"),
        ("XXMRZUSD", "XMRUSD"),
        ("XETCZUSD", "ETCUSD"),
        ("DOGE/USD", "DOGEUSD"),
    ],
)
def test_legacy_kraken_pairs_share_one_canonical_identity(raw, expected):
    assert canonicalize_pair(raw) == expected


def test_doge_legacy_balance_code_is_visible_to_position_gate():
    assert canonicalize_asset("XXDG") == "DOGE"
    assert _balance_for_asset({"XXDG": 250.0}, "DOGE") == 250.0

    class Client:
        enabled = True

        def assert_read_only(self):
            return object()

        def get_balance(self):
            return {"XXDG": 250.0}

        def get_open_positions(self):
            return {}

    verifier = KrakenPositionVerifier(Client())
    verifier.refresh()
    result = verifier.verify(
        ActiveTrade(
            symbol="DOGEUSD",
            entry_price=0.20,
            stop_price=0.18,
            target_1=0.24,
            target_2=0.28,
            risk_level="medium",
            direction="LONG",
            capital=20.0,
        )
    )
    assert result.status == "VERIFIED"
    assert result.observed_quantity == 250.0


def test_legacy_pair_fill_matches_canonical_ohm_symbol():
    rows = _matching_fills(
        {
            "T1": {
                "pair": "XXRPZUSD",
                "type": "buy",
                "time": 1000.0,
                "vol": "10",
                "price": "0.50",
                "fee": "0.01",
            }
        },
        symbol="XRPUSD",
        side="buy",
        after_epoch=900,
    )
    assert [row["txid"] for row in rows] == ["T1"]


def test_malformed_registry_json_fails_loud_and_is_quarantined(tmp_path: Path):
    path = tmp_path / "active_trades.json"
    path.write_text('{"BTCUSD": ', encoding="utf-8")

    with pytest.raises(RegistryCorruptionError):
        load_json(path)

    quarantined = list(tmp_path.glob("active_trades.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == '{"BTCUSD": '
    assert not path.exists()


def _micro_snapshot(*, direction: str) -> MarketSnapshot:
    short = direction == "SHORT"
    return MarketSnapshot(
        symbol="MICROUSD",
        last_price=0.9,
        ema20=0.9,
        ema50=0.8 if not short else 1.0,
        ema200=0.7 if not short else 1.1,
        rsi=55.0 if not short else 45.0,
        macd_line=0.01 if not short else -0.01,
        macd_signal=0.0,
        macd_histogram=0.01 if not short else -0.01,
        atr=0.8,
        atr_pct=88.9,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish" if not short else "bearish",
        trade_direction=direction,
        rolling_24h_upside_p75_pct=20.0,
        rolling_24h_downside_p75_pct=20.0,
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_extreme_atr_plan_geometry_never_authorizes_entry(direction):
    plan = build_entry_exit_plan(_micro_snapshot(direction=direction), "low", direction)
    assert plan.valid_now is False
    assert "invalid" in plan.reason.lower() or "geometry" in plan.reason.lower()
