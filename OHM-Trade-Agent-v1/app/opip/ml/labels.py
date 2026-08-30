"""Direction-aware supervised-label construction for O'Pip ML Foundation v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable, Mapping

from app.opip.ml.contracts import SupervisedLabelRecord, stable_hash
from app.opip.ml.temporal import require_utc


@dataclass(frozen=True)
class PriceBar:
    observed_at_utc: datetime
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at_utc",
            require_utc(self.observed_at_utc, field_name="observed_at_utc"),
        )
        for value in (self.high, self.low, self.close):
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError("bar prices must be finite and positive")
        if self.low > self.high:
            raise ValueError("bar low cannot exceed high")


def _touches(bar: PriceBar, level: float) -> bool:
    return bar.low <= level <= bar.high


def _signed_return_bps(entry: float, price: float, direction: str) -> float:
    raw = (price - entry) / entry * 10_000.0
    return raw if direction == "LONG" else -raw


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
    fixed_horizon_closes: Mapping[str, float | None],
    label_calc_version: str,
    fee_model_version: str,
    slippage_model_version: str,
    total_cost_bps: float = 0.0,
) -> SupervisedLabelRecord:
    """Resolve labels without inventing intrabar path order.

    If the first relevant bar spans both a target and the stop, that target/stop
    ordering is ambiguous. The record is censored for primary supervised use.
    A conservative SL-first result can be calculated separately for reporting,
    but is deliberately not encoded here as ground truth.
    """
    decision = require_utc(decision_at_utc, field_name="decision_at_utc")
    horizon = require_utc(horizon_end_utc, field_name="horizon_end_utc")
    if horizon <= decision:
        raise ValueError("horizon_end_utc must follow decision_at_utc")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    for price in (entry_price, tp1_price, tp2_price, sl_price):
        if not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError("entry/target/stop prices must be finite and positive")
    if total_cost_bps < 0:
        raise ValueError("total_cost_bps cannot be negative")

    rows = sorted(
        (bar for bar in bars if decision <= bar.observed_at_utc <= horizon),
        key=lambda bar: bar.observed_at_utc,
    )
    data_gap = not rows
    ambiguous = False
    tp1_time = tp2_time = sl_time = None

    for bar in rows:
        hit_tp1 = _touches(bar, tp1_price)
        hit_tp2 = _touches(bar, tp2_price)
        hit_sl = _touches(bar, sl_price)
        if hit_sl and (hit_tp1 or hit_tp2):
            ambiguous = True
            break
        if tp1_time is None and hit_tp1:
            tp1_time = bar.observed_at_utc
        if tp2_time is None and hit_tp2:
            tp2_time = bar.observed_at_utc
        if sl_time is None and hit_sl:
            sl_time = bar.observed_at_utc
        if sl_time is not None or tp2_time is not None:
            break

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

    if rows:
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

    net_returns: dict[str, float | None] = {}
    for name, close in fixed_horizon_closes.items():
        if close is None:
            net_returns[str(name)] = None
            data_gap = True
            continue
        if not math.isfinite(float(close)) or float(close) <= 0:
            raise ValueError("fixed-horizon close must be finite and positive")
        net_returns[str(name)] = _signed_return_bps(
            entry_price, float(close), direction
        ) - total_cost_bps

    censored = censored or data_gap
    available = max([horizon] + [bar.observed_at_utc for bar in rows])
    hash_payload = {
        "snapshot_id": snapshot_id,
        "candidate_id": candidate_id,
        "direction": direction,
        "label_calc_version": label_calc_version,
        "label_available_at_utc": available,
        "horizon_end_utc": horizon,
        "tp1_before_sl": tp1_before_sl,
        "tp2_before_sl": tp2_before_sl,
        "sl_before_tp1": sl_before_tp1,
        "net_returns_bps": net_returns,
        "mfe_bps": mfe_bps,
        "mae_bps": mae_bps,
        "time_to_tp1_seconds": (
            (tp1_time - decision).total_seconds() if tp1_time else None
        ),
        "time_to_tp2_seconds": (
            (tp2_time - decision).total_seconds() if tp2_time else None
        ),
        "time_to_sl_seconds": (
            (sl_time - decision).total_seconds() if sl_time else None
        ),
        "censored": censored,
        "data_gap": data_gap,
        "execution_path_ambiguous": ambiguous,
        "fee_model_version": fee_model_version,
        "slippage_model_version": slippage_model_version,
    }
    return SupervisedLabelRecord(
        label_id=stable_hash("MLLBL", hash_payload),
        snapshot_id=snapshot_id,
        candidate_id=candidate_id,
        direction=direction,
        label_calc_version=label_calc_version,
        label_available_at_utc=available,
        horizon_end_utc=horizon,
        tp1_before_sl=tp1_before_sl,
        tp2_before_sl=tp2_before_sl,
        sl_before_tp1=sl_before_tp1,
        net_returns_bps=net_returns,
        mfe_bps=mfe_bps,
        mae_bps=mae_bps,
        time_to_tp1_seconds=hash_payload["time_to_tp1_seconds"],
        time_to_tp2_seconds=hash_payload["time_to_tp2_seconds"],
        time_to_sl_seconds=hash_payload["time_to_sl_seconds"],
        censored=censored,
        data_gap=data_gap,
        execution_path_ambiguous=ambiguous,
        fee_model_version=fee_model_version,
        slippage_model_version=slippage_model_version,
    )
