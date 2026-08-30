from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.opip.ml.labels import HorizonClose, PriceBar, resolve_barrier_labels


NOW = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)


def _bar(start_minutes: int, end_minutes: int, *, high: float, low: float, close: float) -> PriceBar:
    end = NOW + timedelta(minutes=end_minutes)
    return PriceBar(
        interval_start_utc=NOW + timedelta(minutes=start_minutes),
        interval_end_utc=end,
        visible_at_utc=end,
        high=high,
        low=low,
        close=close,
    )


def _close(horizon_minutes: int, price: float) -> HorizonClose:
    horizon = NOW + timedelta(minutes=horizon_minutes)
    return HorizonClose(
        horizon_at_utc=horizon,
        visible_at_utc=horizon,
        price=price,
    )


def _resolve(*, decision, horizon, bars, computed=None):
    return resolve_barrier_labels(
        snapshot_id="MLSNAP:test",
        candidate_id="OPIPC:test",
        decision_at_utc=decision,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=bars,
        horizon_end_utc=horizon,
        fixed_horizon_closes={
            "horizon": HorizonClose(
                horizon_at_utc=horizon,
                visible_at_utc=horizon,
                price=100.0,
            )
        },
        computed_at_utc=computed or horizon,
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )


def test_bar_straddling_decision_boundary_is_censored_not_consumed():
    decision = NOW + timedelta(minutes=5)
    horizon = NOW + timedelta(minutes=60)
    label = _resolve(
        decision=decision,
        horizon=horizon,
        bars=[
            _bar(0, 10, high=106.0, low=99.0, close=105.0),
            _bar(10, 60, high=101.0, low=99.0, close=100.0),
        ],
    )

    assert label.censored is True
    assert label.data_gap is True
    assert label.tp1_before_sl is None
    assert label.time_to_tp1_seconds is None
    assert label.mfe_bps is None
    assert label.mae_bps is None


def test_bar_straddling_horizon_boundary_is_censored_not_consumed():
    decision = NOW
    horizon = NOW + timedelta(minutes=55)
    label = _resolve(
        decision=decision,
        horizon=horizon,
        bars=[
            _bar(0, 50, high=101.0, low=99.0, close=100.0),
            _bar(50, 60, high=106.0, low=99.0, close=105.0),
        ],
        computed=NOW + timedelta(minutes=60),
    )

    assert label.censored is True
    assert label.data_gap is True
    assert label.tp1_before_sl is None
    assert label.time_to_tp1_seconds is None
    assert label.mfe_bps is None
    assert label.mae_bps is None
    assert label.label_available_at_utc == NOW + timedelta(minutes=60)


def test_overlapping_bar_intervals_are_ambiguous_and_input_order_independent():
    decision = NOW
    horizon = NOW + timedelta(minutes=60)
    first = _bar(0, 40, high=106.0, low=99.0, close=105.0)
    second = _bar(30, 60, high=101.0, low=94.0, close=95.0)

    forward = _resolve(
        decision=decision,
        horizon=horizon,
        bars=[first, second],
    )
    reverse = _resolve(
        decision=decision,
        horizon=horizon,
        bars=[second, first],
    )

    for label in (forward, reverse):
        assert label.censored is True
        assert label.execution_path_ambiguous is True
        assert label.tp1_before_sl is None
        assert label.sl_before_tp1 is None
        assert label.time_to_tp1_seconds is None
        assert label.time_to_sl_seconds is None
        assert label.mfe_bps is None
        assert label.mae_bps is None

    assert forward.label_id == reverse.label_id
