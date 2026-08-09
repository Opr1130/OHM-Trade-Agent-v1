import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


PENDING_FILE = Path("/app/data/pending_setups.json")


@dataclass
class PendingSetup:
    symbol: str
    entry_low: float
    entry_high: float
    chase_limit: float
    stop_price: float
    target_1: float
    target_2: float
    risk_level: str
    confidence: int
    status: str = "waiting"
    created_at: str = ""


def _load_raw() -> dict:
    if not PENDING_FILE.exists():
        return {}

    try:
        return json.loads(PENDING_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_raw(data: dict) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(data, indent=2))


def add_pending_setup(setup: PendingSetup) -> None:
    data = _load_raw()

    if not setup.created_at:
        setup.created_at = datetime.now(timezone.utc).isoformat()

    data[setup.symbol] = asdict(setup)
    _save_raw(data)


def get_pending_setups() -> list[PendingSetup]:
    data = _load_raw()

    return [
        PendingSetup(**item)
        for item in data.values()
        if item.get("status") == "waiting"
    ]


def remove_pending_setup(symbol: str) -> bool:
    data = _load_raw()

    if symbol not in data:
        return False

    del data[symbol]
    _save_raw(data)
    return True
