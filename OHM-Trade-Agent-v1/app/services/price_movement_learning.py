from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenAPIError, KrakenClient
from app.services.price_movement_radar import EXPIRED
from app.services.registry_io import load_json, registry_lock, save_json_atomic


MOVEMENT_FILE = Path("/app/data/price_movement_learning.json")
LOCK_FILE = MOVEMENT_FILE.parent / ".price_movement_learning.lock"
DEDUP_SECONDS = 4 * 60 * 60
HORIZONS_SECONDS = {
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "12h": 12 * 60 * 60,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load() -> dict[str, Any]:
    state = load_json(MOVEMENT_FILE)
    if not isinstance(state.get("records"), dict):
        state["records"] = {}
    if not isinstance(state.get("latest"), dict):
        state["latest"] = {}
    return state


def _record_key(signal: dict[str, Any]) -> str:
    return ":".join(
        (
            str(signal.get("symbol") or "UNKNOWN").upper(),
            str(signal.get("stage") or "UNKNOWN").upper(),
            str(signal.get("direction") or "UNCONFIRMED").upper(),
            str(signal.get("observed_at") or _now().isoformat()),
        )
    )


def _deduplicated_record(
    records: dict[str, dict[str, Any]],
    signal: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        observed_at = _parse(signal["observed_at"])
    except (KeyError, TypeError, ValueError):
        return None
    identity = (
        str(signal.get("symbol") or "").upper(),
        str(signal.get("stage") or "").upper(),
        str(signal.get("direction") or "").upper(),
        str(signal.get("source") or ""),
    )
    newest: tuple[datetime, dict[str, Any]] | None = None
    for row in records.values():
        row_identity = (
            str(row.get("symbol") or "").upper(),
            str(row.get("stage") or "").upper(),
            str(row.get("direction") or "").upper(),
            str(row.get("source") or ""),
        )
        if row_identity != identity:
            continue
        try:
            row_time = _parse(row.get("observed_at"))
        except (TypeError, ValueError):
            continue
        age = (observed_at - row_time).total_seconds()
        if 0 <= age < DEDUP_SECONDS and (newest is None or row_time > newest[0]):
            newest = (row_time, row)
    return dict(newest[1]) if newest else None


def record_price_movement(signal: dict[str, Any]) -> dict[str, Any]:
    """Persist a shadow movement event; storage never authorizes an entry."""
    if float(signal.get("reference_price") or 0.0) <= 0:
        raise ValueError("movement reference_price must be positive")
    if float(signal.get("reference_atr") or 0.0) <= 0:
        raise ValueError("movement reference_atr must be positive")

    with registry_lock(LOCK_FILE):
        state = _load()
        records: dict[str, dict[str, Any]] = state["records"]
        duplicate = _deduplicated_record(records, signal)
        if duplicate is not None:
            duplicate["deduplicated"] = True
            return duplicate

        key = _record_key(signal)
        row = dict(signal)
        row["record_key"] = key
        row["observations"] = {}
        row["complete"] = str(signal.get("stage") or "").upper() == EXPIRED
        row["updated_at"] = str(signal.get("observed_at") or _now().isoformat())
        records[key] = row
        state["latest"][str(signal.get("symbol") or "").upper()] = key
        save_json_atomic(MOVEMENT_FILE, state)
        return dict(row)


def get_latest_price_movement(symbol: str) -> dict[str, Any] | None:
    try:
        with registry_lock(LOCK_FILE):
            state = _load()
            key = state["latest"].get(symbol.upper())
            row = state["records"].get(key) if key else None
            return dict(row) if isinstance(row, dict) else None
    except OSError:
        return None


def get_price_movement_records() -> list[dict[str, Any]]:
    try:
        with registry_lock(LOCK_FILE):
            return [dict(row) for row in _load()["records"].values()]
    except OSError:
        return []


def observe_due_price_movements(
    *,
    client: KrakenClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Measure magnitude accuracy at 1h/4h/12h using free Kraken prices."""
    now = now or _now()
    client = client or KrakenClient()
    with registry_lock(LOCK_FILE):
        state = _load()
        records: dict[str, dict[str, Any]] = state["records"]
        due_by_symbol: dict[str, list[tuple[str, str]]] = {}
        for key, row in records.items():
            if row.get("complete"):
                continue
            try:
                age = (now - _parse(row["observed_at"])).total_seconds()
            except (KeyError, TypeError, ValueError):
                continue
            observations = row.get("observations") or {}
            for horizon, seconds in HORIZONS_SECONDS.items():
                if horizon not in observations and age >= seconds:
                    due_by_symbol.setdefault(str(row.get("symbol") or ""), []).append((key, horizon))

        if not due_by_symbol:
            return {
                "status": "NOT_DUE",
                "records_checked": len(records),
                "prices_requested": 0,
                "observations_added": 0,
            }
        try:
            tickers = client.get_tickers(sorted(due_by_symbol))
        except KrakenAPIError as exc:
            return {
                "status": "ERROR",
                "reason": str(exc),
                "records_checked": len(records),
                "prices_requested": len(due_by_symbol),
                "observations_added": 0,
            }

        added = 0
        for symbol, due in due_by_symbol.items():
            ticker = tickers.get(symbol)
            if ticker is None:
                try:
                    ticker = client.get_ticker(symbol)
                except KrakenAPIError:
                    continue
            price = float(ticker["last"])
            for key, horizon in due:
                row = records[key]
                reference = float(row["reference_price"])
                reference_atr = float(row["reference_atr"])
                raw_move_pct = (price / reference - 1.0) * 100.0
                absolute_move_pct = abs(raw_move_pct)
                move_atr = abs(price - reference) / reference_atr
                direction = str(row.get("direction") or "UNCONFIRMED").upper()
                directional_move_pct: float | None = None
                if direction == "LONG":
                    directional_move_pct = raw_move_pct
                elif direction == "SHORT":
                    directional_move_pct = -raw_move_pct
                row.setdefault("observations", {})[horizon] = {
                    "observed_at": now.isoformat(),
                    "price": price,
                    "absolute_move_pct": round(absolute_move_pct, 6),
                    "move_atr": round(move_atr, 6),
                    "directional_move_pct": (
                        round(directional_move_pct, 6)
                        if directional_move_pct is not None
                        else None
                    ),
                    "met_expected_low_atr": move_atr >= float(row.get("expected_move_low_atr") or 0.0),
                }
                row["updated_at"] = now.isoformat()
                added += 1
                if all(item in row["observations"] for item in HORIZONS_SECONDS):
                    row["complete"] = True
        save_json_atomic(MOVEMENT_FILE, state)
        return {
            "status": "OK",
            "records_checked": len(records),
            "prices_requested": len(due_by_symbol),
            "observations_added": added,
        }
