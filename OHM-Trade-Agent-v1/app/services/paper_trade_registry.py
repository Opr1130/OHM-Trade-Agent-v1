from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.services.paper_trade_models import (
    NONTERMINAL_STATUSES,
    TERMINAL_STATUSES,
    PaperAccountSummary,
    PaperTradeLifecycle,
)
from app.services.registry_io import load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/paper_trading/state.json")
EVENT_FILE = Path("/app/data/paper_trading/events.jsonl")


def _state_lock(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def _event_lock(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def _normalize_item(item: dict[str, Any]) -> PaperTradeLifecycle:
    allowed = {field.name for field in fields(PaperTradeLifecycle)}
    normalized = {key: value for key, value in item.items() if key in allowed}
    defaults = {
        "candle_interval_minutes": 15,
        "reference_ask": None,
        "entry_price": None,
        "entry_fee": 0.0,
        "quantity_initial": 0.0,
        "quantity_remaining": 0.0,
        "opened_at": None,
        "tp1_hit": False,
        "tp1_at": None,
        "tp1_price": None,
        "tp1_quantity": 0.0,
        "realized_gross_pnl": 0.0,
        "fees_paid": 0.0,
        "closed_at": None,
        "exit_price": None,
        "exit_reason": None,
        "gross_pnl": None,
        "net_pnl": None,
        "net_pnl_pct": None,
        "outcome": None,
        "last_processed_candle_ts": None,
        "last_observed_price": None,
        "revision": 1,
        "paper_only": True,
        "exchange_write_authority": False,
    }
    for key, value in defaults.items():
        normalized.setdefault(key, value)
    return PaperTradeLifecycle(**normalized)


def _load_rows(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    rows = payload.get("lifecycles", payload)
    if not isinstance(rows, dict):
        raise ValueError("paper state must contain lifecycle objects")
    return rows


def _save_rows(rows: dict[str, Any], path: Path) -> None:
    save_json_atomic(
        path,
        {
            "schema_version": 1,
            "paper_only": True,
            "lifecycles": rows,
        },
    )


def _event_id(trade: PaperTradeLifecycle, event_type: str) -> str:
    raw = f"{trade.paper_trade_id}|{trade.revision}|{event_type}"
    return "PTE:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _append_event(
    trade: PaperTradeLifecycle,
    event_type: str,
    *,
    event_file: Path,
    details: dict[str, Any] | None = None,
) -> None:
    event = {
        "event_id": _event_id(trade, event_type),
        "event_type": str(event_type).upper(),
        "paper_trade_id": trade.paper_trade_id,
        "episode_id": trade.episode_id,
        "cohort_id": trade.cohort_id,
        "symbol": trade.symbol,
        "status": trade.status,
        "revision": trade.revision,
        "updated_at": trade.updated_at,
        "paper_only": True,
        "population": "PAPER_TRADE_V1",
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason,
        "tp1_hit": trade.tp1_hit,
        "gross_pnl": trade.gross_pnl,
        "net_pnl": trade.net_pnl,
        "net_pnl_pct": trade.net_pnl_pct,
        "outcome": trade.outcome,
        "details": details or {},
    }
    event_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with registry_lock(_event_lock(event_file)):
            with event_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
    except (OSError, TimeoutError):
        return


def create_lifecycle(
    trade: PaperTradeLifecycle,
    *,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> PaperTradeLifecycle:
    trade.symbol = trade.symbol.upper()
    trade.direction = trade.direction.upper()
    if trade.direction != "LONG":
        raise ValueError("Paper Trade v1 supports LONG lifecycles only")
    if trade.status not in NONTERMINAL_STATUSES:
        raise ValueError("paper lifecycle must start pending or open")
    if not trade.paper_only or trade.exchange_write_authority:
        raise ValueError("paper lifecycle authority invariant violated")

    with registry_lock(_state_lock(state_file)):
        rows = _load_rows(state_file)
        existing = rows.get(trade.paper_trade_id)
        if isinstance(existing, dict):
            return _normalize_item(existing)
        for row in rows.values():
            if not isinstance(row, dict):
                continue
            if (
                str(row.get("symbol") or "").upper() == trade.symbol
                and str(row.get("status") or "").upper() in NONTERMINAL_STATUSES
            ):
                raise ValueError(f"paper lifecycle already active for {trade.symbol}")
        rows[trade.paper_trade_id] = asdict(trade)
        _save_rows(rows, state_file)

    _append_event(trade, "CREATED", event_file=event_file)
    return trade


def save_lifecycle(
    trade: PaperTradeLifecycle,
    *,
    event_type: str,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> PaperTradeLifecycle:
    if not trade.paper_only or trade.exchange_write_authority:
        raise ValueError("paper lifecycle authority invariant violated")
    if trade.status not in NONTERMINAL_STATUSES | TERMINAL_STATUSES:
        raise ValueError(f"unsupported paper status: {trade.status}")

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with registry_lock(_state_lock(state_file)):
        rows = _load_rows(state_file)
        current = rows.get(trade.paper_trade_id)
        if not isinstance(current, dict):
            raise KeyError(f"paper lifecycle not found: {trade.paper_trade_id}")
        trade.revision = int(current.get("revision") or 1) + 1
        trade.updated_at = stamp.isoformat()
        rows[trade.paper_trade_id] = asdict(trade)
        _save_rows(rows, state_file)

    _append_event(trade, event_type, event_file=event_file, details=details)
    return trade


def get_lifecycles(*, state_file: Path = STATE_FILE) -> list[PaperTradeLifecycle]:
    with registry_lock(_state_lock(state_file)):
        rows = _load_rows(state_file)
    return [
        _normalize_item(row)
        for row in rows.values()
        if isinstance(row, dict)
    ]


def get_nonterminal_lifecycles(
    *,
    state_file: Path = STATE_FILE,
) -> list[PaperTradeLifecycle]:
    return [
        trade
        for trade in get_lifecycles(state_file=state_file)
        if trade.status in NONTERMINAL_STATUSES
    ]


def has_nonterminal_symbol(
    symbol: str,
    *,
    state_file: Path = STATE_FILE,
) -> bool:
    wanted = str(symbol or "").upper()
    return any(
        trade.symbol == wanted
        for trade in get_nonterminal_lifecycles(state_file=state_file)
    )


def account_summary(
    starting_equity: float,
    *,
    state_file: Path = STATE_FILE,
) -> PaperAccountSummary:
    if float(starting_equity) <= 0:
        raise ValueError("starting_equity must be positive")
    rows = get_lifecycles(state_file=state_file)
    realized = sum(
        float(trade.net_pnl or 0.0)
        for trade in rows
        if trade.status == "CLOSED"
    )
    reserved = sum(
        float(trade.capital)
        + (
            float(trade.fees_paid)
            if trade.status == "OPEN"
            else float(trade.capital) * float(trade.fee_rate)
        )
        for trade in rows
        if trade.status in NONTERMINAL_STATUSES
    )
    closed_equity = float(starting_equity) + realized
    return PaperAccountSummary(
        starting_equity=round(float(starting_equity), 2),
        realized_net_pnl=round(realized, 8),
        closed_equity=round(closed_equity, 8),
        reserved_capital=round(reserved, 8),
        available_capital=round(max(0.0, closed_equity - reserved), 8),
        pending_entries=sum(trade.status == "PENDING_ENTRY" for trade in rows),
        open_positions=sum(trade.status == "OPEN" for trade in rows),
        closed_trades=sum(trade.status == "CLOSED" for trade in rows),
        cancelled_setups=sum(trade.status == "CANCELLED" for trade in rows),
    )
