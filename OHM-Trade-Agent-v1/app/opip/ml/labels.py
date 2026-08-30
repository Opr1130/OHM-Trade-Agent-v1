"""Direction-aware supervised-label construction for O'Pip ML Foundation v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable, Mapping

from app.opip.ml.contracts import LABEL_SCHEMA_VERSION, SupervisedLabelRecord, stable_hash
from app.opip.ml.temporal import require_utc


@dataclass(frozen=True)
class PriceBar:
    """One complete price interval plus when O'Pip could actually use it."""

    interval_start_utc: datetime
    interval_end_utc: datetime
    visible_at_utc: datetime
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        start = require_utc(self.interval_start_utc, field_name="interval_start_utc")
        end = require_utc(self.interval_end_utc, field_name="interval_end_utc")
        visible = require_utc(self.visible_at_utc, field_name="visible_at_utc")
        if end <= start:
            raise ValueError("bar interval_end_utc must follow interval_start_utc")
        if visible < end:
            raise ValueError("bar cannot be visible before its interval is complete")
        for value in (self.high, self.low, self.close):
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError("bar prices must be finite and positive")
        if self.low > self.high:
            raise ValueError("bar low cannot exceed high")
        object.__setattr__(self, "interval_start_utc", start)
        object.__setattr__(self, "interval_end_utc", end)
        object.__setattr__(self, "visible_at_utc", visible)


@dataclass(frozen=True)
class HorizonClose:
    """A fixed-horizon close together with its real availability timestamp."""

    horizon_at_utc: datetime
    visible_at_utc: datetime
    price: float

    def __post_init__(self) -> None:
        horizon = require_utc(self.horizon_at_utc, field_name="horizon_at_utc")
        visible = require_utc(self.visible_at_utc, field_name="visible_at_utc")
        if visible < horizon:
            raise ValueError("horizon close cannot be visible before its horizon")
        if not math.isfinite(float(self.price)) or float(self.price) <= 0:
            raise ValueError("horizon close price must be finite and positive")
        object.__setattr__(self, "horizon_at_utc", horizon)
        object.__setattr__(self, "visible_at_utc", visible)


def _hits_target(bar: PriceBar, level: float, direction: str) -> bool:
    """Treat a gap beyond a target as a barrier crossing."""
    return bar.high >= level if direction == "LONG" else bar.low <= level


def _hits_stop(bar: PriceBar, level: float, direction: str) -> bool:
    """Treat a gap beyond a stop as a barrier crossing."""
    return bar.low <= level if direction == "LONG" else bar.high >= level


def _signed_return_bps(entry: float, price: float, direction: str) -> float:
    raw = (price - entry) / entry * 10_000.0
    return raw if direction == "LONG" else -raw


def _validate_barrier_geometry(
    *,
    direction: str,
    entry_price: float,
    tp1_price: float,
    tp2_price: float,
    sl_price: float,
) -> None:
    if direction == "LONG":
        if not (sl_price < entry_price < tp1_price <= tp2_price):
            raise ValueError("LONG barriers must satisfy SL < entry < TP1 <= TP2")
    else:
        if not (sl_price > entry_price > tp1_price >= tp2_price):
            raise ValueError("SHORT barriers must satisfy SL > entry > TP1 >= TP2")


def _path_is_complete(
    rows: list[PriceBar], *, decision_at_utc: datetime, horizon_end_utc: datetime
) -> bool:
    """Require uninterrupted interval coverage across the complete label horizon."""
    if not rows:
        return False
    cursor = decision_at_utc
    for bar in rows:
        if bar.interval_end_utc <= decision_at_utc:
            continue
        if bar.interval_start_utc >= horizon_end_utc:
            break
        effective_start = max(bar.interval_start_utc, decision_at_utc)
        if effective_start > cursor:
            return False
        cursor = max(cursor, min(bar.interval_end_utc, horizon_end_utc))
        if cursor >= horizon_end_utc:
            return True
    return cursor >= horizon_end_utc


def resolve_barrier_labels(
    *,
    snapshot_id: str,
    candidate_id: str | None,
    decision_at_utc: datetime,
    direction: str,
    entry_price: float,
    tp1_price: float,
    tp2_price: float,
    sl_price: float,
    bars: Iterable[PriceBar],
    horizon_end_utc: datetime,
    fixed_horizon_closes: Mapping[str, HorizonClose | None],
    computed_at_utc: datetime,
    label_calc_version: str,
    fee_model_version: str,
    slippage_model_version: str,
    total_cost_bps: float = 0.0,
) -> SupervisedLabelRecord:
    """Resolve labels without lookahead or invented intrabar path order.

    Barrier labels require complete price-path coverage from the decision
    through the declared horizon. A same-bar target/stop collision remains
    ambiguous unless a finer-grained resolver is used upstream. Fixed-horizon
    closes carry their own visibility timestamps, so delayed/backfilled values
    cannot become historically available merely because their horizon is old.
    """
    decision = require_utc(decision_at_utc, field_name="decision_at_utc")
    horizon = require_utc(horizon_end_utc, field_name="horizon_end_utc")
    computed = require_utc(computed_at_utc, field_name="computed_at_utc")
    if horizon <= decision:
        raise ValueError("horizon_end_utc must follow decision_at_utc")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    for price in (entry_price, tp1_price, tp2_price, sl_price):
        if not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError("entry/target/stop prices must be finite and positive")
    _validate_barrier_geometry(
        direction=direction,
        entry_price=entry_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        sl_price=sl_price,
    )
    if total_cost_bps < 0 or not math.isfinite(float(total_cost_bps)):
        raise ValueError("total_cost_bps must be finite and non-negative")

    relevant_rows = sorted(
        (
            bar
            for bar in bars
            if bar.interval_end_utc > decision and bar.interval_start_utc < horizon
        ),
        key=lambda bar: (bar.interval_start_utc, bar.interval_end_utc),
    )
    boundary_straddle = any(
        bar.interval_start_utc < decision or bar.interval_end_utc > horizon
        for bar in relevant_rows
    )
    rows = [
        bar
        for bar in relevant_rows
        if bar.interval_start_utc >= decision and bar.interval_end_utc <= horizon
    ]
    overlapping_intervals = any(
        current.interval_start_utc < previous.interval_end_utc
        for previous, current in zip(rows, rows[1:])
    )
    data_gap = boundary_straddle or not _path_is_complete(
        rows, decision_at_utc=decision, horizon_end_utc=horizon
    )
    ambiguous = overlapping_intervals
    tp1_time = tp2_time = sl_time = None

    for bar in rows:
        hit_tp1 = _hits_target(bar, tp1_price, direction)
        hit_tp2 = _hits_target(bar, tp2_price, direction)
        hit_sl = _hits_stop(bar, sl_price, direction)
        if hit_sl and (hit_tp1 or hit_tp2):
            ambiguous = True
            break
        confirmed_at = min(bar.interval_end_utc, horizon)
        if tp1_time is None and hit_tp1:
            tp1_time = confirmed_at
        if tp2_time is None and hit_tp2:
            tp2_time = confirmed_at
        if sl_time is None and hit_sl:
            sl_time = confirmed_at
        if sl_time is not None or tp2_time is not None:
            break

    net_returns: dict[str, float | None] = {}
    close_visibility: list[datetime] = []
    for name, close in fixed_horizon_closes.items():
        if close is None:
            net_returns[str(name)] = None
            data_gap = True
            continue
        if close.horizon_at_utc < decision or close.horizon_at_utc > horizon:
            raise ValueError("fixed-horizon close lies outside label horizon")
        net_returns[str(name)] = _signed_return_bps(
            entry_price, float(close.price), direction
        ) - total_cost_bps
        close_visibility.append(close.visible_at_utc)

    censored = data_gap or ambiguous
    if censored:
        tp1_before_sl = tp2_before_sl = sl_before_tp1 = None
    else:
        tp1_before_sl = tp1_time is not None and (
            sl_time is None or tp1_time < sl_time
        )
        tp2_before_sl = tp2_time is not None and (
            sl_time is None or tp2_time < sl_time
        )
        sl_before_tp1 = sl_time is not None and (
            tp1_time is None or sl_time < tp1_time
        )

    if rows and not censored:
        favorable = [
            _signed_return_bps(entry_price, bar.high, direction)
            if direction == "LONG"
            else _signed_return_bps(entry_price, bar.low, direction)
            for bar in rows
        ]
        adverse = [
            _signed_return_bps(entry_price, bar.low, direction)
            if direction == "LONG"
            else _signed_return_bps(entry_price, bar.high, direction)
            for bar in rows
        ]
        mfe_bps = max(favorable)
        mae_bps = min(adverse)
    else:
        mfe_bps = mae_bps = None

    input_visibility = [horizon]
    input_visibility.extend(bar.visible_at_utc for bar in relevant_rows)
    input_visibility.extend(close_visibility)
    available = max(input_visibility)
    if computed < available:
        raise ValueError("computed_at_utc cannot precede label input availability")

    time_to_tp1 = (
        (tp1_time - decision).total_seconds() if tp1_time is not None and not censored else None
    )
    time_to_tp2 = (
        (tp2_time - decision).total_seconds() if tp2_time is not None and not censored else None
    )
    time_to_sl = (
        (sl_time - decision).total_seconds() if sl_time is not None and not censored else None
    )

    hash_payload = {
        "snapshot_id": snapshot_id,
        "candidate_id": candidate_id,
        "direction": direction,
        "label_calc_version": label_calc_version,
        "label_available_at_utc": available,
        "label_computed_at_utc": computed,
        "horizon_end_utc": horizon,
        "tp1_before_sl": tp1_before_sl,
        "tp2_before_sl": tp2_before_sl,
        "sl_before_tp1": sl_before_tp1,
        "net_returns_bps": net_returns,
        "mfe_bps": mfe_bps,
        "mae_bps": mae_bps,
        "time_to_tp1_seconds": time_to_tp1,
        "time_to_tp2_seconds": time_to_tp2,
        "time_to_sl_seconds": time_to_sl,
        "censored": censored,
        "data_gap": data_gap,
        "execution_path_ambiguous": ambiguous,
        "fee_model_version": fee_model_version,
        "slippage_model_version": slippage_model_version,
        "schema_version": LABEL_SCHEMA_VERSION,
    }
    return SupervisedLabelRecord(
        label_id=stable_hash("MLLBL", hash_payload),
        snapshot_id=snapshot_id,
        candidate_id=candidate_id,
        direction=direction,
        label_calc_version=label_calc_version,
        label_available_at_utc=available,
        label_computed_at_utc=computed,
        horizon_end_utc=horizon,
        tp1_before_sl=tp1_before_sl,
        tp2_before_sl=tp2_before_sl,
        sl_before_tp1=sl_before_tp1,
        net_returns_bps=net_returns,
        mfe_bps=mfe_bps,
        mae_bps=mae_bps,
        time_to_tp1_seconds=time_to_tp1,
        time_to_tp2_seconds=time_to_tp2,
        time_to_sl_seconds=time_to_sl,
        censored=censored,
        data_gap=data_gap,
        execution_path_ambiguous=ambiguous,
        fee_model_version=fee_model_version,
        slippage_model_version=slippage_model_version,
        schema_version=LABEL_SCHEMA_VERSION,
    )
