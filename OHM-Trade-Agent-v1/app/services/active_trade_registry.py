from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.registry_io import load_json, registry_lock, save_json_atomic


TRADE_FILE = Path("/app/data/active_trades.json")


@dataclass
class ActiveTrade:
    symbol: str
    entry_price: float
    stop_price: float
    target_1: float
    target_2: float
    risk_level: str
    status: str = "active"
    opened_at: str = ""
    trade_id: str = ""
    direction: str = "LONG"
    margin_leverage: float = 1.0
    capital: float | None = None
    actual_entry_fee: float | None = None
    actual_exit_fee: float | None = None
    financing_fee: float = 0.0
    closed_at: str | None = None
    close_price: float | None = None
    close_reason: str | None = None


def _load_raw() -> dict:
    return load_json(TRADE_FILE)


def _save_raw(data: dict) -> None:
    save_json_atomic(TRADE_FILE, data)


def registry_lock_file() -> Path:
    return TRADE_FILE.parent / ".trade_registry.lock"


def _from_item(item: dict) -> ActiveTrade:
    normalized = dict(item)
    normalized.setdefault("direction", "LONG")
    normalized.setdefault("margin_leverage", 1.0)
    normalized.setdefault("capital", None)
    normalized.setdefault("actual_entry_fee", None)
    normalized.setdefault("actual_exit_fee", None)
    normalized.setdefault("financing_fee", 0.0)
    normalized.setdefault("closed_at", None)
    normalized.setdefault("close_price", None)
    normalized.setdefault("close_reason", None)
    return ActiveTrade(**normalized)


def add_trade(trade: ActiveTrade) -> None:
    trade.symbol = trade.symbol.upper()
    trade.direction = (trade.direction or "LONG").upper()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        existing = data.get(trade.symbol)
        if existing and existing.get("status") == "active":
            existing_trade_id = str(existing.get("trade_id") or "")
            incoming_trade_id = str(trade.trade_id or "")
            # Recovery/retry of the exact same lifecycle transition is safe and
            # idempotent. A different active trade on the same symbol must never
            # be silently overwritten because that would orphan real exposure.
            if existing_trade_id and incoming_trade_id and existing_trade_id == incoming_trade_id:
                return
            raise ValueError(f"active trade already exists for {trade.symbol}")
        if not trade.opened_at:
            trade.opened_at = datetime.now(timezone.utc).isoformat()
        data[trade.symbol] = asdict(trade)
        _save_raw(data)


def get_trade(symbol: str) -> ActiveTrade | None:
    with registry_lock(registry_lock_file()):
        item = _load_raw().get(symbol.upper())
    return _from_item(item) if item else None


def get_active_trades() -> list[ActiveTrade]:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
    return [_from_item(item) for item in data.values() if item.get("status") == "active"]


def close_trade(
    symbol: str,
    *,
    close_price: float | None = None,
    actual_exit_fee: float | None = None,
    financing_fee: float | None = None,
    reason: str = "manual_close",
) -> bool:
    symbol = symbol.upper()
    trade_id = ""
    closed = False
    final_price = close_price
    closed_trade: ActiveTrade | None = None
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if symbol not in data:
            return False
        item = data[symbol]
        if item.get("status") == "closed":
            return True
        trade_id = str(item.get("trade_id") or "")
        item["status"] = "closed"
        item["closed_at"] = datetime.now(timezone.utc).isoformat()
        item["close_reason"] = reason
        if close_price is not None:
            if close_price <= 0:
                raise ValueError("close_price must be positive")
            item["close_price"] = close_price
        if actual_exit_fee is not None:
            if actual_exit_fee < 0:
                raise ValueError("actual_exit_fee cannot be negative")
            item["actual_exit_fee"] = actual_exit_fee
        if financing_fee is not None:
            if financing_fee < 0:
                raise ValueError("financing_fee cannot be negative")
            item["financing_fee"] = financing_fee
        _save_raw(data)
        closed_trade = _from_item(item)
        closed = True
    if closed:
        from app.services.trade_outcome_registry import terminalize_active_outcome
        terminalize_active_outcome(
            trade_id=trade_id or None,
            symbol=symbol,
            status="closed",
            reason=reason,
            final_price=final_price,
        )
        if closed_trade is not None:
            from app.services.outcome_financials import finalize_financial_outcome
            finalize_financial_outcome(closed_trade)
    return closed
