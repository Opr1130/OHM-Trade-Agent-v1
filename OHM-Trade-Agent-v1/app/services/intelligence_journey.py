from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.services.registry_io import load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/intelligence_learning/journeys.json")
EVENT_FILE = Path("/app/data/intelligence_learning/events.jsonl")
ACTIVE_WINDOW = timedelta(hours=48)


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("intelligence journey timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash(prefix: str, raw: str, length: int = 24) -> str:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}:{digest}"


def _append_event(row: dict[str, Any], path: Path = EVENT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / f".{path.name}.lock"
    with registry_lock(lock):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False, default=str) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def _new_journey_id(symbol: str, observed_at: datetime) -> str:
    return _hash("JOURNEY", f"{symbol}|{observed_at.isoformat()}")


def _ensure_journey(
    *,
    state: dict[str, Any],
    symbol: str,
    observed_at: datetime,
) -> dict[str, Any]:
    symbols = state.setdefault("symbols", {})
    current = symbols.get(symbol)
    current_time = _parse(current.get("last_seen_at")) if isinstance(current, dict) else None
    if (
        not isinstance(current, dict)
        or current_time is None
        or observed_at - current_time > ACTIVE_WINDOW
        or observed_at < current_time - timedelta(minutes=5)
    ):
        current = {
            "journey_id": _new_journey_id(symbol, observed_at),
            "symbol": symbol,
            "started_at": observed_at.isoformat(),
            "last_seen_at": observed_at.isoformat(),
            "early_watch_count": 0,
            "movement_watch_count": 0,
            "qualified_signal_count": 0,
            "paper_outcome_count": 0,
            "latest_stage": None,
            "latest_signal_id": None,
            "latest_paper_outcome": None,
        }
        symbols[symbol] = current
    else:
        current["last_seen_at"] = max(current_time, observed_at).isoformat()
    return current


def record_watch_observation(
    *,
    symbol: str,
    observed_at: datetime,
    watch_type: str,
    payload: dict[str, Any],
    delivery_action: str,
    delivered: bool,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> str:
    timestamp = _utc(observed_at)
    normalized = str(symbol or "").strip().upper()
    kind = str(watch_type or "").strip().upper()
    if not normalized or kind not in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}:
        raise ValueError("unsupported watch observation")

    lock = state_file.parent / f".{state_file.name}.lock"
    with registry_lock(lock):
        state = load_json(state_file)
        state.setdefault("version", 1)
        state.setdefault("signals", {})
        journey = _ensure_journey(state=state, symbol=normalized, observed_at=timestamp)
        if kind == "EARLY_WATCH":
            journey["early_watch_count"] = int(journey.get("early_watch_count") or 0) + 1
        else:
            journey["movement_watch_count"] = int(journey.get("movement_watch_count") or 0) + 1
        journey["latest_stage"] = payload.get("stage")
        journey["last_seen_at"] = timestamp.isoformat()
        save_json_atomic(state_file, state)
        journey_id = str(journey["journey_id"])

    _append_event(
        {
            "record_type": "INTELLIGENCE_JOURNEY_EVENT",
            "population": "INTELLIGENCE_JOURNEY_V1",
            "event_type": kind,
            "journey_id": journey_id,
            "symbol": normalized,
            "observed_at": timestamp.isoformat(),
            "delivery_action": str(delivery_action or "UNKNOWN"),
            "delivered": bool(delivered),
            "payload": dict(payload),
            "measurement_only": True,
            "affects_ranking": False,
            "affects_trade_authority": False,
        },
        event_file,
    )
    return journey_id


def link_qualified_signal(
    *,
    symbol: str,
    signal_id: str,
    observed_at: datetime,
    payload: dict[str, Any],
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> str:
    timestamp = _utc(observed_at)
    normalized = str(symbol or "").strip().upper()
    signal = str(signal_id or "").strip()
    if not normalized or not signal:
        raise ValueError("symbol and signal_id are required")

    lock = state_file.parent / f".{state_file.name}.lock"
    with registry_lock(lock):
        state = load_json(state_file)
        state.setdefault("version", 1)
        signals = state.setdefault("signals", {})
        existing = signals.get(signal)
        if existing:
            return str(existing)
        journey = _ensure_journey(state=state, symbol=normalized, observed_at=timestamp)
        journey["qualified_signal_count"] = int(journey.get("qualified_signal_count") or 0) + 1
        journey["latest_signal_id"] = signal
        journey["last_seen_at"] = timestamp.isoformat()
        signals[signal] = journey["journey_id"]
        save_json_atomic(state_file, state)
        journey_id = str(journey["journey_id"])

    _append_event(
        {
            "record_type": "INTELLIGENCE_JOURNEY_EVENT",
            "population": "INTELLIGENCE_JOURNEY_V1",
            "event_type": "QUALIFIED_SIGNAL",
            "journey_id": journey_id,
            "signal_id": signal,
            "symbol": normalized,
            "observed_at": timestamp.isoformat(),
            "payload": dict(payload),
            "measurement_only": True,
            "affects_ranking": False,
            "affects_trade_authority": False,
        },
        event_file,
    )
    return journey_id


def journey_for_signal(
    signal_id: str,
    *,
    state_file: Path = STATE_FILE,
) -> str | None:
    lock = state_file.parent / f".{state_file.name}.lock"
    with registry_lock(lock):
        state = load_json(state_file)
    value = (state.get("signals") or {}).get(str(signal_id or ""))
    return str(value) if value else None


def record_paper_outcome(
    *,
    signal_id: str,
    symbol: str,
    observed_at: datetime,
    payload: dict[str, Any],
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> str | None:
    timestamp = _utc(observed_at)
    signal = str(signal_id or "").strip()
    normalized = str(symbol or "").strip().upper()
    if not signal or not normalized:
        return None

    lock = state_file.parent / f".{state_file.name}.lock"
    with registry_lock(lock):
        state = load_json(state_file)
        journey_id = (state.get("signals") or {}).get(signal)
        if not journey_id:
            return None
        symbols = state.setdefault("symbols", {})
        journey = symbols.get(normalized)
        if isinstance(journey, dict) and journey.get("journey_id") == journey_id:
            journey["paper_outcome_count"] = int(journey.get("paper_outcome_count") or 0) + 1
            journey["latest_paper_outcome"] = dict(payload)
            journey["last_seen_at"] = timestamp.isoformat()
            save_json_atomic(state_file, state)

    _append_event(
        {
            "record_type": "INTELLIGENCE_JOURNEY_EVENT",
            "population": "FREQTRADE_DRY_RUN_V1",
            "event_type": "PAPER_OUTCOME",
            "journey_id": str(journey_id),
            "signal_id": signal,
            "symbol": normalized,
            "observed_at": timestamp.isoformat(),
            "payload": dict(payload),
            "measurement_only": True,
            "affects_ranking": False,
            "affects_trade_authority": False,
        },
        event_file,
    )
    return str(journey_id)
