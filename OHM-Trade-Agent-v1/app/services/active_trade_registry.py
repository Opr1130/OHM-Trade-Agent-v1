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
    return ActiveTrade(**normalized)


def add_trade(trade: ActiveTrade) -> None:
    trade.direction = (trade.direction or "LONG").upper()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if not trade.opened_at:
            trade.opened_at = datetime.now(timezone.utc).isoformat()
        data[trade.symbol] = asdict(trade)
        _save_raw(data)


def get_trade(symbol: str) -> ActiveTrade | None:
    with registry_lock(registry_lock_file()):
        item = _load_raw().get(symbol)
    return _from_item(item) if item else None


def get_active_trades() -> list[ActiveTrade]:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
    return [
        _from_item(item)
        for item in data.values()
        if item.get("status") == "active"
    ]


def close_trade(symbol: str) -> bool:
    trade_id = ""
    closed = False
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
        _save_raw(data)
        closed = True
    if closed:
        from app.services.trade_outcome_registry import terminalize_active_outcome
        terminalize_active_outcome(
            trade_id=trade_id or None,
            symbol=symbol,
            status="closed",
            reason="active_registry_closed",
        )
    return closed
