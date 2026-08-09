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


def _load_raw() -> dict:
    return load_json(TRADE_FILE)


def _save_raw(data: dict) -> None:
    save_json_atomic(TRADE_FILE, data)


def registry_lock_file() -> Path:
    return TRADE_FILE.parent / ".trade_registry.lock"


def add_trade(trade: ActiveTrade) -> None:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if not trade.opened_at:
            trade.opened_at = datetime.now(timezone.utc).isoformat()
        data[trade.symbol] = asdict(trade)
        _save_raw(data)


def get_trade(symbol: str) -> ActiveTrade | None:
    with registry_lock(registry_lock_file()):
        item = _load_raw().get(symbol)

    if not item:
        return None

    return ActiveTrade(**item)


def get_active_trades() -> list[ActiveTrade]:
    with registry_lock(registry_lock_file()):
        data = _load_raw()

    return [
        ActiveTrade(**item)
        for item in data.values()
        if item.get("status") == "active"
    ]


def close_trade(symbol: str) -> bool:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if symbol not in data:
            return False
        data[symbol]["status"] = "closed"
        data[symbol]["closed_at"] = datetime.now(timezone.utc).isoformat()
        _save_raw(data)
        return True
