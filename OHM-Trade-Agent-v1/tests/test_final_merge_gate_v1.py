from __future__ import annotations

from datetime import datetime, timezone

from app.core.runtime_environment import conservative_runtime
import app.services.price_movement_learning as price_movement_learning


class _TickerOnlyClient:
    def get_ohlc(self, *args, **kwargs):
        raise RuntimeError("historical OHLC unavailable")

    def get_ticker(self, symbol):
        return {"last": 150.0}


def test_missing_app_env_defaults_to_conservative_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_ENV", raising=False)
    assert conservative_runtime() is True


def test_exact_horizon_never_falls_back_to_future_ticker_price():
    observed = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)
    due = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    labelled = datetime(2026, 8, 22, 12, 4, tzinfo=timezone.utc)

    price = price_movement_learning._price_at_horizon(
        _TickerOnlyClient(),
        "BTCUSD",
        observed,
        due,
        labelled_at=labelled,
    )

    assert price is None
