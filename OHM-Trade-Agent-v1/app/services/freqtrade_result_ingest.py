from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from app.services.intelligence_journey import (
    EVENT_FILE as JOURNEY_EVENT_FILE,
    STATE_FILE as JOURNEY_STATE_FILE,
    record_paper_outcome,
)
from app.services.registry_io import load_json, registry_lock, save_json_atomic


DB_USD_FILE = Path("/app/freqtrade_paper/tradesv3.ohm_dry_run_usd.sqlite")
DB_USDT_FILE = Path("/app/freqtrade_paper/tradesv3.ohm_dry_run_usdt.sqlite")
DB_FILES = (DB_USD_FILE, DB_USDT_FILE)
# Backward-compatible primary path for tests/importers.
DB_FILE = DB_USD_FILE
STATE_FILE = Path("/app/data/intelligence_learning/freqtrade_ingest_state.json")
DEDUP_DB_FILE = Path("/app/data/intelligence_learning/freqtrade_ingest_dedup.sqlite")
WORKER_HEARTBEAT_MAX_AGE_SECONDS = 60


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _value(
    row: sqlite3.Row,
    columns: set[str],
    name: str,
    default: Any = None,
) -> Any:
    return row[name] if name in columns else default


def _quote_for_db(path: Path) -> str:
    return "USDT" if "usdt" in path.name.casefold() else "USD"


def _selected_db_files(db_file: Path | None) -> tuple[Path, ...]:
    return (db_file,) if db_file is not None else DB_FILES


def _read_trade_rows(
    db_file: Path,
    *,
    closed_only: bool,
) -> tuple[str, set[str], list[sqlite3.Row], str | None]:
    if not db_file.exists():
        return "NOT_READY", set(), [], "database not created yet"

    connection = sqlite3.connect(
        f"file:{db_file}?mode=ro",
        uri=True,
        timeout=2.0,
    )
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "trades" not in tables:
            return "NOT_READY", set(), [], "trades table not created yet"

        columns = _columns(connection, "trades")
        required = {"id", "pair", "is_open", "enter_tag"}
        if closed_only:
            required |= {"close_date", "close_profit", "close_profit_abs"}
        if not required.issubset(columns):
            return (
                "SCHEMA_UNAVAILABLE",
                columns,
                [],
                f"missing columns: {sorted(required - columns)}",
            )

        where = (
            "is_open = 0 AND enter_tag LIKE 'OHM:%'"
            if closed_only
            else "enter_tag LIKE 'OHM:%'"
        )
        rows = connection.execute(
            f"SELECT * FROM trades WHERE {where} ORDER BY id"
        ).fetchall()
        return "OK", columns, rows, None
    finally:
        connection.close()


def freqtrade_dry_run_status(
    *,
    db_file: Path | None = None,
    require_heartbeat: bool = True,
) -> dict[str, Any]:
    open_count = 0
    closed_count = 0
    open_pairs: set[str] = set()
    pnl_by_currency: dict[str, float] = {}
    active_stake_by_currency: dict[str, float] = {}
    active_signal_ids: set[str] = set()
    per_worker: dict[str, dict[str, Any]] = {}

    now = datetime.now(timezone.utc)
    for path in _selected_db_files(db_file):
        quote = _quote_for_db(path)
        heartbeat = path.parent / f"heartbeat_{quote}"
        heartbeat_age_seconds: float | None = None
        heartbeat_fresh = not require_heartbeat
        if require_heartbeat and heartbeat.exists():
            try:
                modified = datetime.fromtimestamp(
                    heartbeat.stat().st_mtime,
                    tz=timezone.utc,
                )
                heartbeat_age_seconds = max(
                    0.0,
                    (now - modified).total_seconds(),
                )
                heartbeat_fresh = (
                    heartbeat_age_seconds <= WORKER_HEARTBEAT_MAX_AGE_SECONDS
                )
            except OSError:
                heartbeat_fresh = False

        status, columns, rows, reason = _read_trade_rows(
            path,
            closed_only=False,
        )
        if status != "OK":
            per_worker[quote] = {
                "status": status,
                "reason": reason,
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "open_trades": 0,
                "closed_trades": 0,
                "realized_net_pnl": 0.0,
            }
            continue
        if not heartbeat_fresh:
            status = "STALE"
            reason = "worker heartbeat missing or stale"

        open_rows = [row for row in rows if int(row["is_open"] or 0) == 1]
        closed_rows = [row for row in rows if int(row["is_open"] or 0) == 0]
        pnl = 0.0
        if "close_profit_abs" in columns:
            for row in closed_rows:
                try:
                    pnl += float(row["close_profit_abs"] or 0.0)
                except (TypeError, ValueError):
                    continue

        worker_pairs = {
            str(row["pair"])
            for row in open_rows
            if "pair" in columns and row["pair"]
        }
        worker_signal_ids = {
            str(row["enter_tag"])
            for row in open_rows
            if "enter_tag" in columns and row["enter_tag"]
        }
        worker_active_stake = 0.0
        if "stake_amount" in columns:
            for row in open_rows:
                try:
                    worker_active_stake += max(
                        0.0,
                        float(row["stake_amount"] or 0.0),
                    )
                except (TypeError, ValueError):
                    continue
        open_pairs.update(worker_pairs)
        active_signal_ids.update(worker_signal_ids)
        active_stake_by_currency[quote] = round(worker_active_stake, 8)
        open_count += len(open_rows)
        closed_count += len(closed_rows)
        pnl_by_currency[quote] = round(pnl, 8)
        per_worker[quote] = {
            "status": status,
            "reason": reason,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "open_trades": len(open_rows),
            "closed_trades": len(closed_rows),
            "realized_net_pnl": round(pnl, 8),
            "active_stake": round(worker_active_stake, 8),
            "active_signal_ids": sorted(worker_signal_ids),
            "open_pairs": sorted(worker_pairs),
        }

    statuses = {row["status"] for row in per_worker.values()}
    overall = (
        "OK"
        if statuses and statuses == {"OK"}
        else "PARTIAL"
        if "OK" in statuses
        else "NOT_READY"
    )
    total_numeric = round(sum(pnl_by_currency.values()), 8)
    return {
        "status": overall,
        "engine": "FREQTRADE",
        "mode": "DRY_RUN",
        "open_trades": open_count,
        "closed_trades": closed_count,
        # Retained for single-worker tests/backward compatibility. Cross-quote
        # reporting should use realized_pnl_by_currency instead of assuming
        # exact USD/USDT parity.
        "realized_net_pnl": total_numeric,
        "realized_pnl_by_currency": pnl_by_currency,
        "active_stake_by_currency": active_stake_by_currency,
        "active_signal_ids": sorted(active_signal_ids),
        "open_pairs": sorted(open_pairs),
        "workers": per_worker,
        "exchange_write_authority": False,
    }


def _dedup_path(
    state_file: Path,
    dedup_db_file: Path | None,
) -> Path:
    if dedup_db_file is not None:
        return dedup_db_file
    if state_file == STATE_FILE:
        return DEDUP_DB_FILE
    return state_file.with_name(f"{state_file.stem}_dedup.sqlite")


def _open_dedup(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=2.0)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_trade_outcomes (
            trade_key TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def _event_contains_trade_key(event_file: Path, trade_key: str) -> bool:
    if not event_file.exists():
        return False
    try:
        with event_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                if trade_key not in line:
                    continue
                try:
                    import json
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (
                    str(row.get("event_type") or "").upper() == "PAPER_OUTCOME"
                    and str((row.get("payload") or {}).get("trade_key") or "")
                    == trade_key
                ):
                    return True
    except OSError:
        return False
    return False


def _ingest_freqtrade_dry_run_unlocked(
    *,
    db_file: Path | None = None,
    state_file: Path = STATE_FILE,
    journey_state_file: Path = JOURNEY_STATE_FILE,
    journey_event_file: Path = JOURNEY_EVENT_FILE,
    dedup_db_file: Path | None = None,
) -> dict[str, Any]:
    dedup_path = _dedup_path(state_file, dedup_db_file)
    dedup = _open_dedup(dedup_path)

    added = 0
    rows_seen = 0
    unmatched_outcomes = 0
    invalid_outcomes = 0
    new_keys: list[str] = []
    worker_status: dict[str, str] = {}
    ready_workers = 0

    try:
        for path in _selected_db_files(db_file):
            quote = _quote_for_db(path)
            status, columns, rows, _reason = _read_trade_rows(
                path,
                closed_only=True,
            )
            worker_status[quote] = status
            if status != "OK":
                continue
            ready_workers += 1
            rows_seen += len(rows)

            for row in rows:
                trade_id = int(row["id"])
                trade_key = f"{quote}:{trade_id}"
                existing = dedup.execute(
                    "SELECT status FROM processed_trade_outcomes WHERE trade_key = ?",
                    (trade_key,),
                ).fetchone()
                if existing is not None:
                    if str(existing[0]).upper() == "APPLIED":
                        continue
                    # Recover a crash after the append but before the ledger
                    # status update without creating a duplicate learning row.
                    if _event_contains_trade_key(journey_event_file, trade_key):
                        dedup.execute(
                            """
                            UPDATE processed_trade_outcomes
                            SET status = 'APPLIED', updated_at = ?
                            WHERE trade_key = ?
                            """,
                            (datetime.now(timezone.utc).isoformat(), trade_key),
                        )
                        dedup.commit()
                        continue
                    dedup.execute(
                        "DELETE FROM processed_trade_outcomes WHERE trade_key = ?",
                        (trade_key,),
                    )
                    dedup.commit()

                signal_id = str(row["enter_tag"] or "")
                pair = str(row["pair"] or "")
                symbol = pair.replace("/", "").replace(":", "").upper()
                close_time = _parse_time(
                    _value(row, columns, "close_date")
                    or _value(row, columns, "close_date_utc")
                )
                if close_time is None:
                    invalid_outcomes += 1
                    continue
                try:
                    net_pnl = float(_value(row, columns, "close_profit_abs"))
                    close_profit_ratio = float(
                        _value(row, columns, "close_profit")
                    )
                except (TypeError, ValueError):
                    invalid_outcomes += 1
                    continue

                payload = {
                    "engine": "FREQTRADE",
                    "mode": "DRY_RUN",
                    "worker_quote": quote,
                    "trade_id": trade_id,
                    "trade_key": trade_key,
                    "pair": pair,
                    "pnl_currency": quote,
                    "open_date": _value(row, columns, "open_date"),
                    "close_date": _value(row, columns, "close_date"),
                    "open_rate": _value(row, columns, "open_rate"),
                    "open_rate_requested": _value(
                        row,
                        columns,
                        "open_rate_requested",
                    ),
                    "close_rate": _value(row, columns, "close_rate"),
                    "close_rate_requested": _value(
                        row,
                        columns,
                        "close_rate_requested",
                    ),
                    "stake_amount": _value(row, columns, "stake_amount"),
                    "amount": _value(row, columns, "amount"),
                    "fee_open": _value(row, columns, "fee_open"),
                    "fee_close": _value(row, columns, "fee_close"),
                    "close_profit_ratio": close_profit_ratio,
                    "net_pnl": net_pnl,
                    "exit_reason": _value(row, columns, "exit_reason"),
                    "strategy": _value(row, columns, "strategy"),
                    "timeframe": _value(row, columns, "timeframe"),
                    "dry_run": True,
                    "exchange_write_authority": False,
                }

                now_iso = datetime.now(timezone.utc).isoformat()
                dedup.execute(
                    """
                    INSERT INTO processed_trade_outcomes
                        (trade_key, signal_id, status, updated_at)
                    VALUES (?, ?, 'PENDING', ?)
                    """,
                    (trade_key, signal_id, now_iso),
                )
                dedup.commit()

                journey_id = record_paper_outcome(
                    signal_id=signal_id,
                    symbol=symbol,
                    observed_at=close_time,
                    payload=payload,
                    state_file=journey_state_file,
                    event_file=journey_event_file,
                )
                if journey_id is not None:
                    dedup.execute(
                        """
                        UPDATE processed_trade_outcomes
                        SET status = 'APPLIED', updated_at = ?
                        WHERE trade_key = ?
                        """,
                        (datetime.now(timezone.utc).isoformat(), trade_key),
                    )
                    dedup.commit()
                    added += 1
                    new_keys.append(trade_key)
                else:
                    # Keep unmatched outcomes retryable and do not poison the
                    # durable primary-key ledger.
                    dedup.execute(
                        "DELETE FROM processed_trade_outcomes WHERE trade_key = ?",
                        (trade_key,),
                    )
                    dedup.commit()
                    unmatched_outcomes += 1
    finally:
        dedup.close()

    state_lock = state_file.parent / f".{state_file.name}.lock"
    with registry_lock(state_lock):
        latest = load_json(state_file)
        latest["last_ingested_at"] = datetime.now(timezone.utc).isoformat()
        latest["population"] = "FREQTRADE_DRY_RUN_V1"
        latest["dedup_store"] = str(dedup_path)
        save_json_atomic(state_file, latest)

    total_workers = len(_selected_db_files(db_file))
    status = (
        "OK"
        if ready_workers == total_workers
        else "PARTIAL"
        if ready_workers
        else "NOT_READY"
    )
    return {
        "status": status,
        "closed_rows_seen": rows_seen,
        "outcomes_added": added,
        "unmatched_outcomes": unmatched_outcomes,
        "invalid_outcomes": invalid_outcomes,
        "new_trade_ids": len(new_keys),
        "new_trade_keys": len(new_keys),
        "workers": worker_status,
        "dedup_store": str(dedup_path),
    }


def ingest_freqtrade_dry_run(
    *,
    db_file: Path | None = None,
    state_file: Path = STATE_FILE,
    journey_state_file: Path = JOURNEY_STATE_FILE,
    journey_event_file: Path = JOURNEY_EVENT_FILE,
    dedup_db_file: Path | None = None,
) -> dict[str, Any]:
    ingest_lock = state_file.parent / ".freqtrade_ingest.lock"
    with registry_lock(ingest_lock):
        return _ingest_freqtrade_dry_run_unlocked(
            db_file=db_file,
            state_file=state_file,
            journey_state_file=journey_state_file,
            journey_event_file=journey_event_file,
            dedup_db_file=dedup_db_file,
        )

