from __future__ import annotations

import json
import math
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import Candle, KrakenClient
from app.services.jsonl_retention import compact_jsonl_recent
from app.services.movement_discovery_learning_capture import DETECTION_FILE
from app.services.registry_io import load_json, registry_lock, save_json_atomic


OUTCOME_FILE = Path("/app/data/movement_discovery_v2_1_outcomes.jsonl")
STATE_FILE = Path("/app/data/movement_discovery_v2_1_outcome_state.json")
LOCK_FILE = OUTCOME_FILE.parent / ".movement_discovery_v2_1_outcome.lock"
CANDLE_INTERVAL_SECONDS = 15 * 60
HORIZONS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
}
MAX_SOURCE_DETECTIONS = 10_000
MAX_COMPLETED_KEYS = 50_000
COMPLETION_INDEX_VERSION = 2
OUTCOME_LEDGER_MAX_BYTES = 512 * 1024 * 1024
OUTCOME_LEDGER_KEEP_LINES = 100_000


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


def _read_detections(path: Path, *, limit: int = MAX_SOURCE_DETECTIONS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, int(limit)))
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
    return list(rows)


def _read_completed_outcome_keys(path: Path) -> list[str]:
    """Recover recent completion tombstones from the durable outcome ledger."""
    if not path.exists():
        return []
    ordered: OrderedDict[str, None] = OrderedDict()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("record_type") != "OUTCOME":
                    continue
                detection_id = str(row.get("detection_id") or "")
                horizon = str(row.get("horizon") or "")
                if not detection_id or horizon not in HORIZONS:
                    continue
                key = f"{detection_id}:{horizon}"
                ordered.pop(key, None)
                ordered[key] = None
    except OSError:
        return []
    return list(ordered.keys())[-MAX_COMPLETED_KEYS:]


def _merge_completed_keys(*groups: list[str]) -> list[str]:
    ordered: OrderedDict[str, None] = OrderedDict()
    for group in groups:
        for raw in group:
            key = str(raw or "")
            if not key:
                continue
            ordered.pop(key, None)
            ordered[key] = None
    return list(ordered.keys())[-MAX_COMPLETED_KEYS:]


def _window_metrics(
    candles: list[Candle],
    *,
    start: datetime,
    end: datetime,
    reference_price: float,
    direction: str,
) -> dict[str, Any] | None:
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    selected = [
        candle
        for candle in candles
        if start_ts <= candle.timestamp and candle.timestamp + CANDLE_INTERVAL_SECONDS <= end_ts
    ]
    if not selected or not math.isfinite(float(reference_price)) or reference_price <= 0:
        return None
    values = [float(selected[-1].close)] + [float(candle.high) for candle in selected] + [float(candle.low) for candle in selected]
    if not all(math.isfinite(value) and value > 0 for value in values):
        return None
    close_price = values[0]
    highest = max(float(candle.high) for candle in selected)
    lowest = min(float(candle.low) for candle in selected)
    direction = direction.upper()
    if direction == "SHORT":
        close_return = (reference_price - close_price) / reference_price * 100.0
        mfe = (reference_price - lowest) / reference_price * 100.0
        mae = -((highest - reference_price) / reference_price * 100.0)
    elif direction == "LONG":
        close_return = (close_price - reference_price) / reference_price * 100.0
        mfe = (highest - reference_price) / reference_price * 100.0
        mae = (lowest - reference_price) / reference_price * 100.0
    else:
        return None
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


def _append_completed_key(completed_order: list[str], completed: set[str], key: str) -> None:
    if key in completed:
        return
    completed.add(key)
    completed_order.append(key)


def observe_due_movement_discovery_outcomes(
    *,
    now: datetime | None = None,
    client: KrakenClient | None = None,
    detection_file: Path | None = None,
    outcome_file: Path | None = None,
    state_file: Path | None = None,
) -> dict[str, Any]:
    """Label recent v2.1 detections only after fixed horizons are due.

    The source window and completion index are intentionally bounded together:
    every horizon for every retained source detection fits inside the completion
    index. This prevents old completed detections from falling out of state and
    being appended repeatedly forever.
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
        return {"status": "OK", "detections": 0, "observations_added": 0, "pending_horizons": 0, "legacy_unobservable": 0}

    state = load_json(state_target)
    state_completed = [str(item) for item in (state.get("completed") or [])]
    migrated_completion_index = int(state.get("completion_index_version") or 0) < COMPLETION_INDEX_VERSION
    ledger_completed: list[str] = []
    if migrated_completion_index:
        lock_target = outcomes_path.parent / f".{outcomes_path.name}.lock"
        with registry_lock(lock_target):
            ledger_completed = _read_completed_outcome_keys(outcomes_path)
    completed_order = _merge_completed_keys(state_completed, ledger_completed)
    completed = set(completed_order)
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
        if not detection_id or detected_at is None or not math.isfinite(reference_price) or reference_price <= 0:
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
            metrics = _window_metrics(candles, start=start, end=end, reference_price=float(row["reference_price"]), direction=str(row.get("direction") or "LONG"))
            if metrics is None:
                unavailable += 1
                continue
            key = f"{row['detection_id']}:{horizon}"
            observation = {
                "record_type": "OUTCOME", "detection_id": row["detection_id"], "symbol": symbol,
                "direction": str(row.get("direction") or "LONG").upper(), "detected_at": start.isoformat(),
                "outcome_as_of": end.isoformat(), "observed_at": now.isoformat(), "horizon": horizon,
                "reference_price": float(row["reference_price"]), **metrics, "stage": row.get("stage"),
                "entry_recommendation": row.get("entry_recommendation"), "momentum_state": row.get("momentum_state"),
                "continuation_confidence": row.get("continuation_confidence"), "entry_quality": row.get("entry_quality"),
                "alert_eligible": row.get("alert_eligible"),
                "relative_volume": row.get("relative_volume"), "liquidity_24h_usd_approx": row.get("liquidity_24h_usd_approx"),
                "version": row.get("version"), "shadow_only": True, "production_decision_changed": False,
            }
            observations.append(observation)
            _append_completed_key(completed_order, completed, key)

    ledger_compacted = False
    if observations or migrated_completion_index:
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        lock_target = outcomes_path.parent / f".{outcomes_path.name}.lock"
        with registry_lock(lock_target):
            latest_state = load_json(state_target)
            latest_completed = [str(item) for item in (latest_state.get("completed") or [])]
            completed_order = _merge_completed_keys(latest_completed, completed_order)
            latest_completed_set = set(latest_completed)
            if observations:
                observations = [
                    row for row in observations
                    if f"{row['detection_id']}:{row['horizon']}" not in latest_completed_set
                ]
                if observations:
                    with outcomes_path.open("a", encoding="utf-8") as handle:
                        for row in observations:
                            handle.write(json.dumps(row, sort_keys=True, default=str, allow_nan=False) + "\n")
                        handle.flush()
                    ledger_compacted = compact_jsonl_recent(
                        outcomes_path,
                        max_bytes=OUTCOME_LEDGER_MAX_BYTES,
                        keep_lines=OUTCOME_LEDGER_KEEP_LINES,
                    )
            latest_state["completed"] = completed_order[-MAX_COMPLETED_KEYS:]
            latest_state["completion_index_version"] = COMPLETION_INDEX_VERSION
            if observations:
                latest_state["last_observed_at"] = now.isoformat()
            save_json_atomic(state_target, latest_state)

    return {
        "status": "OK", "detections": len(rows), "observations_added": len(observations),
        "pending_horizons": pending, "legacy_unobservable": legacy_unobservable,
        "unavailable_horizons": unavailable, "completion_index_migrated": migrated_completion_index,
        "outcome_ledger_compacted": ledger_compacted,
    }
