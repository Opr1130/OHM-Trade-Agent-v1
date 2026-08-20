from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import Candle, KrakenClient
from app.services.movement_discovery_learning_capture import DETECTION_FILE
from app.services.registry_io import load_json, registry_lock, save_json_atomic


OUTCOME_FILE = Path("/app/data/movement_discovery_v2_1_outcomes.jsonl")
STATE_FILE = Path("/app/data/movement_discovery_v2_1_outcome_state.json")
LOCK_FILE = OUTCOME_FILE.parent / ".movement_discovery_v2_1_outcome.lock"
HORIZONS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_detections(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("record_type") == "DETECTION":
                    rows.append(row)
    except OSError:
        return []
    return rows


def _window_metrics(
    candles: list[Candle],
    *,
    start: datetime,
    end: datetime,
    reference_price: float,
    direction: str,
) -> dict[str, Any] | None:
    selected = [
        candle
        for candle in candles
        if start.timestamp() <= candle.timestamp <= end.timestamp()
    ]
    if not selected or reference_price <= 0:
        return None
    close_price = float(selected[-1].close)
    highest = max(float(candle.high) for candle in selected)
    lowest = min(float(candle.low) for candle in selected)
    direction = direction.upper()
    if direction == "SHORT":
        close_return = (reference_price / close_price - 1.0) * 100.0 if close_price > 0 else 0.0
        mfe = (reference_price / lowest - 1.0) * 100.0 if lowest > 0 else 0.0
        mae = -((highest / reference_price - 1.0) * 100.0)
    else:
        close_return = (close_price / reference_price - 1.0) * 100.0
        mfe = (highest / reference_price - 1.0) * 100.0
        mae = (lowest / reference_price - 1.0) * 100.0
    return {
        "close_price": round(close_price, 12),
        "close_return_pct": round(close_return, 6),
        "mfe_pct": round(mfe, 6),
        "mae_pct": round(mae, 6),
        "hit_5pct": mfe >= 5.0,
        "hit_10pct": mfe >= 10.0,
        "hit_20pct": mfe >= 20.0,
        "sample_candles": len(selected),
    }


def observe_due_movement_discovery_outcomes(
    *,
    now: datetime | None = None,
    client: KrakenClient | None = None,
    detection_file: Path | None = None,
    outcome_file: Path | None = None,
    state_file: Path | None = None,
) -> dict[str, Any]:
    """Label v2.1 detections only after fixed horizons are due.

    Uses historical 15-minute OHLC so downtime does not smear a 1h label into
    a later ticker observation. MFE/MAE are computed from candle highs/lows.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    detections_path = detection_file or DETECTION_FILE
    outcomes_path = outcome_file or OUTCOME_FILE
    state_target = state_file or STATE_FILE
    rows = _read_detections(detections_path)
    if not rows:
        return {
            "status": "OK",
            "detections": 0,
            "observations_added": 0,
            "pending_horizons": 0,
            "legacy_unobservable": 0,
        }

    state = load_json(state_target)
    completed = set(str(item) for item in (state.get("completed") or []))
    legacy_unobservable = 0
    pending = 0
    due_by_symbol: dict[str, list[tuple[dict[str, Any], str, datetime, datetime]]] = defaultdict(list)

    for row in rows:
        detection_id = str(row.get("detection_id") or "")
        detected_at = _parse_time(row.get("observed_at"))
        try:
            reference_price = float(row.get("reference_price") or 0.0)
        except (TypeError, ValueError):
            reference_price = 0.0
        if not detection_id or detected_at is None or reference_price <= 0:
            legacy_unobservable += 1
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            legacy_unobservable += 1
            continue
        for horizon, delta in HORIZONS.items():
            key = f"{detection_id}:{horizon}"
            if key in completed:
                continue
            end = detected_at + delta
            if end > now:
                pending += 1
                continue
            due_by_symbol[symbol].append((row, horizon, detected_at, end))

    client = client or KrakenClient()
    observations: list[dict[str, Any]] = []
    unavailable = 0
    for symbol, due in due_by_symbol.items():
        earliest = min(item[2] for item in due) - timedelta(minutes=15)
        try:
            candles = client.get_ohlc(symbol, interval=15, since=int(earliest.timestamp()))
        except Exception:
            unavailable += len(due)
            continue
        for row, horizon, start, end in due:
            metrics = _window_metrics(
                candles,
                start=start,
                end=end,
                reference_price=float(row["reference_price"]),
                direction=str(row.get("direction") or "LONG"),
            )
            if metrics is None:
                unavailable += 1
                continue
            key = f"{row['detection_id']}:{horizon}"
            observation = {
                "record_type": "OUTCOME",
                "detection_id": row["detection_id"],
                "symbol": symbol,
                "direction": str(row.get("direction") or "LONG").upper(),
                "detected_at": start.isoformat(),
                "outcome_as_of": end.isoformat(),
                "observed_at": now.isoformat(),
                "horizon": horizon,
                "reference_price": float(row["reference_price"]),
                **metrics,
                "stage": row.get("stage"),
                "entry_recommendation": row.get("entry_recommendation"),
                "momentum_state": row.get("momentum_state"),
                "continuation_confidence": row.get("continuation_confidence"),
                "entry_quality": row.get("entry_quality"),
                "relative_volume": row.get("relative_volume"),
                "liquidity_24h_usd_approx": row.get("liquidity_24h_usd_approx"),
                "version": row.get("version"),
                "shadow_only": True,
                "production_decision_changed": False,
            }
            observations.append(observation)
            completed.add(key)

    if observations:
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        lock_target = outcomes_path.parent / f".{outcomes_path.name}.lock"
        with registry_lock(lock_target):
            with outcomes_path.open("a", encoding="utf-8") as handle:
                for row in observations:
                    handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                handle.flush()
            state["completed"] = sorted(completed)[-20000:]
            state["last_observed_at"] = now.isoformat()
            save_json_atomic(state_target, state)

    return {
        "status": "OK",
        "detections": len(rows),
        "observations_added": len(observations),
        "pending_horizons": pending,
        "legacy_unobservable": legacy_unobservable,
        "unavailable_horizons": unavailable,
    }
