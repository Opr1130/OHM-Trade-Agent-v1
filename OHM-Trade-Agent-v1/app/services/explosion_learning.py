from __future__ import annotations

import json
import math
import uuid
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import Candle, KrakenClient
from app.services.explosion_state import ExplosionStateVector
from app.services.jsonl_retention import compact_jsonl_recent
from app.services.registry_io import load_json, registry_lock, save_json_atomic


SNAPSHOT_FILE = Path("/app/data/explosion_state_snapshots.jsonl")
OUTCOME_FILE = Path("/app/data/explosion_state_outcomes.jsonl")
STATE_FILE = Path("/app/data/explosion_state_outcome_state.json")
CANDLE_INTERVAL_SECONDS = 15 * 60
HORIZONS = {
    "1h": timedelta(hours=1), "4h": timedelta(hours=4),
    "12h": timedelta(hours=12), "24h": timedelta(hours=24),
}
MAX_SOURCE_SNAPSHOTS = 20_000
MAX_COMPLETED_KEYS = 100_000
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


def append_explosion_state(vector: ExplosionStateVector, *, path: Path | None = None, observed_at: datetime | None = None, source_cohort: str | None = None) -> str:
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    target = path or SNAPSHOT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    observation_id = uuid.uuid4().hex
    payload = {"record_type": "EXPLOSION_STATE", "observation_id": observation_id, "observed_at": observed_at.astimezone(timezone.utc).isoformat(), "source_cohort": source_cohort, **vector.as_dict()}
    lock = target.parent / f".{target.name}.lock"
    with registry_lock(lock):
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str, allow_nan=False) + "\n")
            handle.flush()
    return observation_id


def read_recent_explosion_states(*, path: Path | None = None, limit: int = 5000) -> list[dict[str, Any]]:
    target = path or SNAPSHOT_FILE
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("record_type") == "EXPLOSION_STATE":
                    rows.append(row)
    except OSError:
        return []
    return rows[-max(1, limit):]


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
                if row.get("record_type") != "EXPLOSION_OUTCOME":
                    continue
                observation_id = str(row.get("observation_id") or "")
                horizon = str(row.get("horizon") or "")
                if not observation_id or horizon not in HORIZONS:
                    continue
                key = f"{observation_id}:{horizon}"
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


def latest_state_by_symbol(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in read_recent_explosion_states(path=path):
        symbol = str(row.get("symbol") or "").upper()
        observed = _parse_time(row.get("observed_at"))
        if not symbol or observed is None:
            continue
        current = latest.get(symbol)
        current_time = _parse_time(current.get("observed_at")) if current else None
        if current_time is None or observed > current_time:
            latest[symbol] = row
    return latest


def _window_metrics(candles: list[Candle], *, start: datetime, end: datetime, reference_price: float) -> dict[str, Any] | None:
    start_ts, end_ts = start.timestamp(), end.timestamp()
    selected = [candle for candle in candles if start_ts <= candle.timestamp and candle.timestamp + CANDLE_INTERVAL_SECONDS <= end_ts]
    if not selected or not math.isfinite(float(reference_price)) or reference_price <= 0:
        return None
    numeric = [float(selected[-1].close)] + [float(c.high) for c in selected] + [float(c.low) for c in selected]
    if not all(math.isfinite(value) and value > 0 for value in numeric):
        return None
    highest = max(float(c.high) for c in selected)
    lowest = min(float(c.low) for c in selected)
    close_price = float(selected[-1].close)
    mfe = (highest - reference_price) / reference_price * 100.0
    mae = (lowest - reference_price) / reference_price * 100.0
    close_return = (close_price - reference_price) / reference_price * 100.0
    max_candle = max(selected, key=lambda candle: float(candle.high))
    time_to_mfe_minutes = max(0.0, (max_candle.timestamp - start.timestamp()) / 60.0)
    return {
        "close_price": round(close_price, 12), "close_return_pct": round(close_return, 6),
        "mfe_pct": round(mfe, 6), "mae_pct": round(mae, 6),
        "time_to_mfe_minutes": round(time_to_mfe_minutes, 2),
        "hit_2pct": mfe >= 2.0, "hit_5pct": mfe >= 5.0,
        "hit_10pct": mfe >= 10.0, "hit_20pct": mfe >= 20.0,
        "sample_candles": len(selected),
    }


def observe_due_explosion_outcomes(*, now: datetime | None = None, client: KrakenClient | None = None, snapshot_file: Path | None = None, outcome_file: Path | None = None, state_file: Path | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    snapshots = read_recent_explosion_states(path=snapshot_file, limit=MAX_SOURCE_SNAPSHOTS)
    target = outcome_file or OUTCOME_FILE
    state_target = state_file or STATE_FILE
    state = load_json(state_target)
    state_completed = [str(item) for item in (state.get("completed") or [])]
    migrated_completion_index = int(state.get("completion_index_version") or 0) < COMPLETION_INDEX_VERSION
    ledger_completed: list[str] = []
    if migrated_completion_index:
        lock_target = target.parent / f".{target.name}.lock"
        with registry_lock(lock_target):
            ledger_completed = _read_completed_outcome_keys(target)
    completed_order = _merge_completed_keys(state_completed, ledger_completed)
    completed = set(completed_order)

    due_by_symbol: dict[str, list[tuple[dict[str, Any], str, datetime, datetime]]] = defaultdict(list)
    pending = invalid = 0
    for row in snapshots:
        observation_id = str(row.get("observation_id") or "")
        observed_at = _parse_time(row.get("observed_at"))
        symbol = str(row.get("symbol") or "").upper()
        try:
            reference_price = float(row.get("reference_price") or 0.0)
        except (TypeError, ValueError):
            reference_price = 0.0
        if not observation_id or observed_at is None or not symbol or not math.isfinite(reference_price) or reference_price <= 0:
            invalid += 1
            continue
        for horizon, delta in HORIZONS.items():
            key = f"{observation_id}:{horizon}"
            if key in completed:
                continue
            end = observed_at + delta
            if end > now:
                pending += 1
                continue
            due_by_symbol[symbol].append((row, horizon, observed_at, end))

    client = client or KrakenClient()
    outcomes: list[dict[str, Any]] = []
    unavailable = 0
    for symbol, due in due_by_symbol.items():
        earliest = min(item[2] for item in due) - timedelta(minutes=15)
        try:
            candles = client.get_ohlc(symbol, interval=15, since=int(earliest.timestamp()))
        except Exception:
            unavailable += len(due)
            continue
        for row, horizon, start, end in due:
            metrics = _window_metrics(candles, start=start, end=end, reference_price=float(row["reference_price"]))
            if metrics is None:
                unavailable += 1
                continue
            key = f"{row['observation_id']}:{horizon}"
            outcomes.append({
                "record_type": "EXPLOSION_OUTCOME", "observation_id": row["observation_id"], "symbol": symbol,
                "phase": row.get("phase"), "phase_score": row.get("phase_score"), "source_cohort": row.get("source_cohort"),
                "observed_at": start.isoformat(), "outcome_as_of": end.isoformat(), "horizon": horizon,
                "reference_price": float(row["reference_price"]), "momentum_1h_pct": row.get("momentum_1h_pct"),
                "momentum_6h_pct": row.get("momentum_6h_pct"), "momentum_24h_pct": row.get("momentum_24h_pct"),
                "momentum_acceleration": row.get("momentum_acceleration"), "relative_volume": row.get("relative_volume"),
                "relative_volume_change": row.get("relative_volume_change"), "atr_percentile": row.get("atr_percentile"),
                "atr_percentile_change": row.get("atr_percentile_change"), "bandwidth_percentile": row.get("bandwidth_percentile"),
                "bandwidth_percentile_change": row.get("bandwidth_percentile_change"), "compression_release_score": row.get("compression_release_score"),
                "distance_to_high_velocity_pct": row.get("distance_to_high_velocity_pct"), "base_displacement_velocity_pct": row.get("base_displacement_velocity_pct"),
                "trade_count_acceleration": row.get("trade_count_acceleration"), "aggressor_imbalance": row.get("aggressor_imbalance"),
                "large_print_concentration": row.get("large_print_concentration"), "book_notional_imbalance": row.get("book_notional_imbalance"),
                "persistence_score": row.get("persistence_score"), **metrics, "shadow_only": True, "production_decision_changed": False,
            })
            if key not in completed:
                completed.add(key)
                completed_order.append(key)

    ledger_compacted = False
    if outcomes or migrated_completion_index:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = target.parent / f".{target.name}.lock"
        with registry_lock(lock):
            latest_state = load_json(state_target)
            latest_completed = [str(item) for item in (latest_state.get("completed") or [])]
            completed_order = _merge_completed_keys(latest_completed, completed_order)
            latest_completed_set = set(latest_completed)
            if outcomes:
                outcomes = [
                    row for row in outcomes
                    if f"{row['observation_id']}:{row['horizon']}" not in latest_completed_set
                ]
                if outcomes:
                    with target.open("a", encoding="utf-8") as handle:
                        for row in outcomes:
                            handle.write(json.dumps(row, sort_keys=True, default=str, allow_nan=False) + "\n")
                        handle.flush()
                    ledger_compacted = compact_jsonl_recent(
                        target,
                        max_bytes=OUTCOME_LEDGER_MAX_BYTES,
                        keep_lines=OUTCOME_LEDGER_KEEP_LINES,
                    )
            latest_state["completed"] = completed_order[-MAX_COMPLETED_KEYS:]
            latest_state["completion_index_version"] = COMPLETION_INDEX_VERSION
            if outcomes:
                latest_state["last_observed_at"] = now.isoformat()
            save_json_atomic(state_target, latest_state)

    return {
        "status": "OK", "snapshots": len(snapshots), "outcomes_added": len(outcomes),
        "pending_horizons": pending, "invalid_snapshots": invalid, "unavailable_horizons": unavailable,
        "completion_index_migrated": migrated_completion_index, "outcome_ledger_compacted": ledger_compacted,
    }
