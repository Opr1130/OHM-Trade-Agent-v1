from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from app.services.intelligence_journey import record_paper_outcome
from app.services.registry_io import load_json, registry_lock, save_json_atomic


DB_FILE = Path("/app/freqtrade_paper/tradesv3.ohm_dry_run.sqlite")
STATE_FILE = Path("/app/data/intelligence_learning/freqtrade_ingest_state.json")


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _value(row: sqlite3.Row, columns: set[str], name: str, default: Any = None) -> Any:
    return row[name] if name in columns else default


def ingest_freqtrade_dry_run(
    *,
    db_file: Path = DB_FILE,
    state_file: Path = STATE_FILE,
) -> dict[str, Any]:
    if not db_file.exists():
        return {
            "status": "NOT_READY",
            "closed_rows_seen": 0,
            "outcomes_added": 0,
            "reason": "Freqtrade dry-run database not created yet",
        }

    state_lock = state_file.parent / f".{state_file.name}.lock"
    with registry_lock(state_lock):
        state = load_json(state_file)
        processed = {int(value) for value in (state.get("processed_trade_ids") or [])}

    connection = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "trades" not in tables:
            return {
                "status": "NOT_READY",
                "closed_rows_seen": 0,
                "outcomes_added": 0,
                "reason": "Freqtrade trades table not created yet",
            }
        columns = _columns(connection, "trades")
        required = {"id", "pair", "is_open", "enter_tag"}
        if not required.issubset(columns):
            return {
                "status": "SCHEMA_UNAVAILABLE",
                "closed_rows_seen": 0,
                "outcomes_added": 0,
                "reason": f"missing columns: {sorted(required - columns)}",
            }
        rows = connection.execute(
            "SELECT * FROM trades WHERE is_open = 0 AND enter_tag LIKE 'OHM:%' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    added = 0
    new_ids: list[int] = []
    for row in rows:
        trade_id = int(row["id"])
        if trade_id in processed:
            continue
        signal_id = str(row["enter_tag"] or "")
        pair = str(row["pair"] or "")
        symbol = pair.replace("/", "").replace(":", "").upper()
        close_time = _parse_time(
            _value(row, columns, "close_date")
            or _value(row, columns, "close_date_utc")
        )
        payload = {
            "engine": "FREQTRADE",
            "mode": "DRY_RUN",
            "trade_id": trade_id,
            "pair": pair,
            "open_date": _value(row, columns, "open_date"),
            "close_date": _value(row, columns, "close_date"),
            "open_rate": _value(row, columns, "open_rate"),
            "open_rate_requested": _value(row, columns, "open_rate_requested"),
            "close_rate": _value(row, columns, "close_rate"),
            "close_rate_requested": _value(row, columns, "close_rate_requested"),
            "stake_amount": _value(row, columns, "stake_amount"),
            "amount": _value(row, columns, "amount"),
            "fee_open": _value(row, columns, "fee_open"),
            "fee_close": _value(row, columns, "fee_close"),
            "close_profit_ratio": _value(row, columns, "close_profit"),
            "net_pnl": _value(row, columns, "close_profit_abs"),
            "exit_reason": _value(row, columns, "exit_reason"),
            "strategy": _value(row, columns, "strategy"),
            "timeframe": _value(row, columns, "timeframe"),
            "dry_run": True,
            "exchange_write_authority": False,
        }
        journey_id = record_paper_outcome(
            signal_id=signal_id,
            symbol=symbol,
            observed_at=close_time,
            payload=payload,
        )
        if journey_id is not None:
            added += 1
        new_ids.append(trade_id)

    if new_ids:
        with registry_lock(state_lock):
            latest = load_json(state_file)
            existing = [int(value) for value in (latest.get("processed_trade_ids") or [])]
            merged = list(dict.fromkeys(existing + new_ids))[-5000:]
            latest["processed_trade_ids"] = merged
            latest["last_ingested_at"] = datetime.now(timezone.utc).isoformat()
            latest["population"] = "FREQTRADE_DRY_RUN_V1"
            save_json_atomic(state_file, latest)

    return {
        "status": "OK",
        "closed_rows_seen": len(rows),
        "outcomes_added": added,
        "new_trade_ids": len(new_ids),
    }
