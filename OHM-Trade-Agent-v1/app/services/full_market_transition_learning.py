from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from app.exchanges.kraken import Candle, KrakenClient
from app.services.registry_io import load_json, registry_lock, save_json_atomic


DETECTION_FILE = Path("/app/data/full_market_transition_detections.jsonl")
OUTCOME_FILE = Path("/app/data/full_market_transition_outcomes.jsonl")
STATE_FILE = Path("/app/data/full_market_transition_outcome_state.json")
CAPTURE_STATE_FILE = Path("/app/data/full_market_transition_capture_state.json")
CANDLE_INTERVAL_SECONDS = 15 * 60
CAPTURE_COOLDOWN_SECONDS = 60 * 60
MIN_EVIDENCE_SAMPLES = 30
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


def _read_jsonl(path: Path, record_type: str | None = None) -> list[dict[str, Any]]:
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
                if not isinstance(row, dict):
                    continue
                if record_type is None or row.get("record_type") == record_type:
                    rows.append(row)
    except OSError:
        return []
    return rows


def _transition_signature(transition: Any) -> str:
    score = int(getattr(transition, "score", 0) or 0)
    return ":".join(
        (
            str(getattr(transition, "pattern", "UNKNOWN")).upper(),
            str(getattr(transition, "alert_tier", "UNKNOWN")).upper(),
            str(score // 10 * 10),
        )
    )


def _detection_id(*, symbol: str, signature: str, observed_at: datetime, reference_price: float) -> str:
    payload = f"{symbol.upper()}|{signature}|{observed_at.isoformat()}|{reference_price:.12g}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def capture_full_market_transitions(
    transitions: Iterable[Any],
    *,
    now: datetime | None = None,
    detection_file: Path | None = None,
    state_file: Path | None = None,
) -> int:
    """Persist prospective transition detections without granting trade authority."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    target = detection_file or DETECTION_FILE
    state_target = state_file or CAPTURE_STATE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"

    captured: list[dict[str, Any]] = []
    with registry_lock(lock):
        state = load_json(state_target)
        latest = state.get("latest_by_symbol") or {}
        if not isinstance(latest, dict):
            latest = {}

        for transition in transitions:
            symbol = str(getattr(transition, "symbol", "") or "").upper()
            pattern = str(getattr(transition, "pattern", "") or "").upper()
            tier = str(getattr(transition, "alert_tier", "") or "").upper()
            signature = _transition_signature(transition)
            try:
                score = int(getattr(transition, "score", 0) or 0)
                reference_price = float(getattr(transition, "reference_price", 0.0) or 0.0)
                liquidity = float(getattr(transition, "liquidity_24h_usd_approx", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if (
                not symbol
                or not pattern
                or not math.isfinite(reference_price)
                or reference_price <= 0
                or not math.isfinite(liquidity)
                or liquidity < 0
            ):
                continue

            previous = latest.get(symbol) if isinstance(latest.get(symbol), dict) else None
            previous_at = _parse_time(previous.get("captured_at")) if previous else None
            if (
                previous
                and previous.get("signature") == signature
                and previous_at is not None
                and (now - previous_at).total_seconds() < CAPTURE_COOLDOWN_SECONDS
            ):
                continue

            detection_id = _detection_id(
                symbol=symbol,
                signature=signature,
                observed_at=now,
                reference_price=reference_price,
            )
            row = {
                "record_type": "FULL_MARKET_TRANSITION_DETECTION",
                "detection_id": detection_id,
                "observed_at": now.isoformat(),
                "symbol": symbol,
                "reference_price": reference_price,
                "pattern": pattern,
                "score": score,
                "score_bucket": score // 10 * 10,
                "alert_tier": tier,
                "liquidity_24h_usd_approx": liquidity,
                "price_change_since_prior_pct": getattr(transition, "price_change_since_prior_pct", None),
                "lift_change_since_prior_pct": getattr(transition, "lift_change_since_prior_pct", None),
                "lift_from_24h_low_pct": getattr(transition, "lift_from_24h_low_pct", None),
                "distance_from_24h_high_pct": getattr(transition, "distance_from_24h_high_pct", None),
                "learning_only": True,
                "shadow_only": True,
                "trade_authority_changed": False,
                "production_execution_gate_changed": False,
            }
            captured.append(row)
            latest[symbol] = {"signature": signature, "captured_at": now.isoformat(), "detection_id": detection_id}

        if captured:
            with target.open("a", encoding="utf-8") as handle:
                for row in captured:
                    handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
            state["latest_by_symbol"] = latest
            state["last_capture_at"] = now.isoformat()
            save_json_atomic(state_target, state)
    return len(captured)


def _window_metrics(
    candles: list[Candle],
    *,
    start: datetime,
    end: datetime,
    reference_price: float,
) -> dict[str, Any] | None:
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    selected = [
        candle
        for candle in candles
        if start_ts <= float(candle.timestamp)
        and float(candle.timestamp) + CANDLE_INTERVAL_SECONDS <= end_ts
    ]
    if not selected or not math.isfinite(reference_price) or reference_price <= 0:
        return None
    try:
        close_price = float(selected[-1].close)
        highs = [float(candle.high) for candle in selected]
        lows = [float(candle.low) for candle in selected]
    except (TypeError, ValueError):
        return None
    values = [close_price, *highs, *lows]
    if not all(math.isfinite(value) and value > 0 for value in values):
        return None

    highest = max(highs)
    lowest = min(lows)
    max_candle = max(selected, key=lambda candle: float(candle.high))
    close_return = (close_price - reference_price) / reference_price * 100.0
    mfe = (highest - reference_price) / reference_price * 100.0
    mae = (lowest - reference_price) / reference_price * 100.0
    time_to_mfe = max(0.0, (float(max_candle.timestamp) - start_ts) / 60.0)
    return {
        "close_price": round(close_price, 12),
        "close_return_pct": round(close_return, 6),
        "mfe_pct": round(mfe, 6),
        "mae_pct": round(mae, 6),
        "time_to_mfe_minutes": round(time_to_mfe, 2),
        "hit_2pct": mfe >= 2.0,
        "hit_5pct": mfe >= 5.0,
        "hit_10pct": mfe >= 10.0,
        "hit_20pct": mfe >= 20.0,
        "sample_candles": len(selected),
    }


def observe_due_full_market_transition_outcomes(
    *,
    now: datetime | None = None,
    client: KrakenClient | None = None,
    detection_file: Path | None = None,
    outcome_file: Path | None = None,
    state_file: Path | None = None,
) -> dict[str, Any]:
    """Label transition outcomes only after each exact horizon is fully due."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    detections_path = detection_file or DETECTION_FILE
    outcomes_path = outcome_file or OUTCOME_FILE
    state_target = state_file or STATE_FILE
    detections = _read_jsonl(detections_path, "FULL_MARKET_TRANSITION_DETECTION")
    if not detections:
        return {"status": "OK", "detections": 0, "outcomes_added": 0, "pending_horizons": 0}

    state = load_json(state_target)
    completed_order = list(dict.fromkeys(str(item) for item in (state.get("completed") or [])))
    completed = set(completed_order)
    due_by_symbol: dict[str, list[tuple[dict[str, Any], str, datetime, datetime]]] = defaultdict(list)
    pending = invalid = 0

    for row in detections:
        detection_id = str(row.get("detection_id") or "")
        observed_at = _parse_time(row.get("observed_at"))
        symbol = str(row.get("symbol") or "").upper()
        try:
            reference_price = float(row.get("reference_price") or 0.0)
        except (TypeError, ValueError):
            reference_price = 0.0
        if not detection_id or observed_at is None or not symbol or not math.isfinite(reference_price) or reference_price <= 0:
            invalid += 1
            continue
        for horizon, delta in HORIZONS.items():
            key = f"{detection_id}:{horizon}"
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
            metrics = _window_metrics(
                candles,
                start=start,
                end=end,
                reference_price=float(row["reference_price"]),
            )
            if metrics is None:
                unavailable += 1
                continue
            key = f"{row['detection_id']}:{horizon}"
            outcomes.append({
                "record_type": "FULL_MARKET_TRANSITION_OUTCOME",
                "detection_id": row["detection_id"],
                "symbol": symbol,
                "pattern": row.get("pattern"),
                "score": row.get("score"),
                "score_bucket": row.get("score_bucket"),
                "alert_tier": row.get("alert_tier"),
                "liquidity_24h_usd_approx": row.get("liquidity_24h_usd_approx"),
                "detected_at": start.isoformat(),
                "outcome_as_of": end.isoformat(),
                "labelled_at": now.isoformat(),
                "horizon": horizon,
                "reference_price": float(row["reference_price"]),
                **metrics,
                "shadow_only": True,
                "production_decision_changed": False,
            })
            completed.add(key)
            completed_order.append(key)

    if outcomes:
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        lock = outcomes_path.parent / f".{outcomes_path.name}.lock"
        with registry_lock(lock):
            with outcomes_path.open("a", encoding="utf-8") as handle:
                for row in outcomes:
                    handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
            state["completed"] = completed_order[-50000:]
            state["last_observed_at"] = now.isoformat()
            save_json_atomic(state_target, state)

    return {
        "status": "OK",
        "detections": len(detections),
        "outcomes_added": len(outcomes),
        "pending_horizons": pending,
        "invalid_detections": invalid,
        "unavailable_horizons": unavailable,
    }


def build_full_market_transition_summary(
    *,
    outcome_file: Path | None = None,
    minimum_samples: int = MIN_EVIDENCE_SAMPLES,
) -> dict[str, Any]:
    """Report prospective pattern evidence; never auto-promote a transition."""
    rows = _read_jsonl(outcome_file or OUTCOME_FILE, "FULL_MARKET_TRANSITION_OUTCOME")
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pattern = str(row.get("pattern") or "UNKNOWN").upper()
        horizon = str(row.get("horizon") or "UNKNOWN")
        tier = str(row.get("alert_tier") or "UNKNOWN").upper()
        buckets[f"{pattern}|{tier}|{horizon}"].append(row)

    result: dict[str, Any] = {}
    for key, bucket in sorted(buckets.items()):
        mfe = [float(row["mfe_pct"]) for row in bucket if isinstance(row.get("mfe_pct"), (int, float))]
        mae = [float(row["mae_pct"]) for row in bucket if isinstance(row.get("mae_pct"), (int, float))]
        if not mfe or not mae:
            continue
        samples = min(len(mfe), len(mae))
        result[key] = {
            "samples": samples,
            "sufficient_samples": samples >= minimum_samples,
            "avg_mfe_pct": round(mean(mfe), 4),
            "avg_mae_pct": round(mean(mae), 4),
            "hit_2pct_rate": round(sum(bool(row.get("hit_2pct")) for row in bucket) / len(bucket), 4),
            "hit_5pct_rate": round(sum(bool(row.get("hit_5pct")) for row in bucket) / len(bucket), 4),
            "hit_10pct_rate": round(sum(bool(row.get("hit_10pct")) for row in bucket) / len(bucket), 4),
            "hit_20pct_rate": round(sum(bool(row.get("hit_20pct")) for row in bucket) / len(bucket), 4),
        }
    return {
        "status": "EVIDENCE_READY" if result and all(item["sufficient_samples"] for item in result.values()) else "BUILDING_EVIDENCE",
        "minimum_samples_per_bucket": minimum_samples,
        "outcome_rows": len(rows),
        "buckets": result,
        "auto_promotion_allowed": False,
        "human_review_required": True,
    }
