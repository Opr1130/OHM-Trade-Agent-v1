import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


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


def _load_raw() -> dict:
    if not TRADE_FILE.exists():
        return {}

    try:
        return json.loads(TRADE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_raw(data: dict) -> None:
    TRADE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRADE_FILE.write_text(json.dumps(data, indent=2))


def add_trade(trade: ActiveTrade) -> None:
    data = _load_raw()

    if not trade.opened_at:
        trade.opened_at = datetime.now(timezone.utc).isoformat()

    data[trade.symbol] = asdict(trade)
    _save_raw(data)


def get_trade(symbol: str) -> ActiveTrade | None:
    data = _load_raw()
    item = data.get(symbol)

    if not item:
        return None

    return ActiveTrade(**item)


def get_active_trades() -> list[ActiveTrade]:
    data = _load_raw()

    return [
        ActiveTrade(**item)
        for item in data.values()
        if item.get("status") == "active"
    ]


def close_trade(symbol: str) -> bool:
    data = _load_raw()

    if symbol not in data:
        return False

    data[symbol]["status"] = "closed"
    data[symbol]["closed_at"] = datetime.now(timezone.utc).isoformat()

    _save_raw(data)
    return True
