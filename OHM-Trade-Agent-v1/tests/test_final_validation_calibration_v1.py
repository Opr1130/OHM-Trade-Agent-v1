from __future__ import annotations

import math

import pytest

from app.services.self_calibration import _live_multiplier, calibration_model


def _row(i: int, pnl: float) -> dict:
    return {
        "trade_id": f"T-{i}",
        "entered_trade": True,
        "terminal_status": "closed",
        "net_pnl": pnl,
        "direction": "LONG",
        "market_regime": "BULLISH",
    }


def test_self_calibration_excludes_nonfinite_financial_samples():
    records = [_row(i, 2.0) for i in range(30)]
    records.append(_row(31, float("nan")))
    model = calibration_model(records)
    assert model["samples"] == 30
    assert math.isfinite(model["baseline_avg_net_pnl"])
    assert all(math.isfinite(float(value)) for value in model["weights"].values())


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_self_calibration_live_multiplier_fails_neutral_on_nonfinite_weight(bad):
    model = {
        "status": "CALIBRATED",
        "weights": {"direction:LONG": bad},
    }
    assert _live_multiplier(model, direction="LONG", regime=None) == 1.0
