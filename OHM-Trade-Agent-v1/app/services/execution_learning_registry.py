from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.registry_io import load_json, registry_lock, save_json_atomic


EXECUTION_FILE = Path("/app/data/execution_learning.json")
LOCK_FILE = EXECUTION_FILE.parent / ".execution_learning.lock"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, dict[str, Any]]:
    return load_json(EXECUTION_FILE)


def _save(data: dict[str, dict[str, Any]]) -> None:
    save_json_atomic(EXECUTION_FILE, data)


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((b - a).total_seconds(), 3)


def record_execution_event(
    trade_id: str,
    *,
    symbol: str,
    direction: str,
    signal_at: str | None = None,
    order_placed_at: str | None = None,
    first_fill_at: str | None = None,
    full_fill_at: str | None = None,
    closed_at: str | None = None,
    limit_price: float | None = None,
    fill_price: float | None = None,
    close_price: float | None = None,
    quantity: float | None = None,
    entry_fee: float | None = None,
    exit_fee: float | None = None,
    source: str = "kraken_reconciliation",
) -> dict[str, Any]:
    if not trade_id:
        raise ValueError("trade_id is required")
    with registry_lock(LOCK_FILE):
        data = _load()
        row = dict(data.get(trade_id) or {})
        row.setdefault("trade_id", trade_id)
        row["symbol"] = symbol.upper()
        row["direction"] = direction.upper()
        row["source"] = source
        row.setdefault("created_at", _now())
        for key, value in {
            "signal_at": signal_at,
            "order_placed_at": order_placed_at,
            "first_fill_at": first_fill_at,
            "full_fill_at": full_fill_at,
            "closed_at": closed_at,
            "limit_price": limit_price,
            "fill_price": fill_price,
            "close_price": close_price,
            "quantity": quantity,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
        }.items():
            if value is not None:
                row[key] = value
        row["idea_to_order_seconds"] = _seconds_between(row.get("signal_at"), row.get("order_placed_at"))
        row["order_to_first_fill_seconds"] = _seconds_between(row.get("order_placed_at"), row.get("first_fill_at"))
        row["idea_to_first_fill_seconds"] = _seconds_between(row.get("signal_at"), row.get("first_fill_at"))
        row["idea_to_full_fill_seconds"] = _seconds_between(row.get("signal_at"), row.get("full_fill_at"))
        row["holding_seconds"] = _seconds_between(row.get("full_fill_at") or row.get("first_fill_at"), row.get("closed_at"))
        if row.get("limit_price") and row.get("fill_price"):
            lp = float(row["limit_price"])
            fp = float(row["fill_price"])
            if lp > 0:
                if row["direction"] == "SHORT":
                    row["fill_vs_limit_pct"] = round((lp / fp - 1.0) * 100.0, 6)
                else:
                    row["fill_vs_limit_pct"] = round((fp / lp - 1.0) * 100.0, 6)
        row["updated_at"] = _now()
        data[trade_id] = row
        _save(data)
        return row


def get_execution_records() -> list[dict[str, Any]]:
    with registry_lock(LOCK_FILE):
        data = _load()
    return list(data.values())


def get_execution_record(trade_id: str) -> dict[str, Any] | None:
    with registry_lock(LOCK_FILE):
        row = _load().get(trade_id)
    return dict(row) if row else None
