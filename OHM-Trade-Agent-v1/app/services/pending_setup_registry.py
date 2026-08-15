from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.services.registry_io import load_json, registry_lock, save_json_atomic


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
    trade_id: str = ""
    confirmation_price: float | None = None
    direction: str = "LONG"
    margin_leverage: float = 1.0


def _load_raw() -> dict:
    return load_json(PENDING_FILE)


def _save_raw(data: dict) -> None:
    save_json_atomic(PENDING_FILE, data)


def registry_lock_file() -> Path:
    return PENDING_FILE.parent / ".trade_registry.lock"


def add_pending_setup(setup: PendingSetup) -> PendingSetup:
    setup.direction = (setup.direction or "LONG").upper()
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if not setup.created_at:
            setup.created_at = datetime.now(timezone.utc).isoformat()
        if not setup.trade_id:
            setup.trade_id = f"OHM-{setup.symbol.upper()}-{uuid4().hex[:12]}"
        data[setup.symbol] = asdict(setup)
        _save_raw(data)
    return setup


def _from_item(item: dict) -> PendingSetup:
    normalized = dict(item)
    normalized.setdefault("direction", "LONG")
    normalized.setdefault("margin_leverage", 1.0)
    return PendingSetup(**normalized)


def get_pending_setups() -> list[PendingSetup]:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
    return [
        _from_item(item)
        for item in data.values()
        if item.get("status") == "waiting"
    ]


def get_pending_setup_by_trade_id(trade_id: str) -> PendingSetup | None:
    return next((setup for setup in get_pending_setups() if setup.trade_id == trade_id), None)


def terminalize_pending_setup(trade_id: str, status: str) -> bool:
    if status not in {
        "skipped",
        "invalidated",
        "too_extended",
        "send_failed",
        "portfolio_risk_rejected",
        "tracking_failed",
    }:
        raise ValueError(f"Unsupported terminal status: {status}")
    try:
        from app.services.order_intent_registry import (
            cancel_order_intent,
            get_order_intent,
        )

        intent = get_order_intent(trade_id)
        if intent is not None and intent.status == "FILLED":
            # Exchange truth wins over a late local invalidation. Retire the
            # pending view without terminalizing the now-entered trade.
            mark_pending_setup_entered(trade_id)
            return False
        if intent is not None and intent.status == "LIMIT_PLACED":
            # Cancel reconciliation eligibility before terminalizing the
            # pending setup. If this write cannot complete, leave both records
            # live so a later cycle can retry instead of creating split brain.
            cancel_order_intent(trade_id)
    except Exception:
        return False
    terminalized = False
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        for item in data.values():
            if item.get("trade_id") == trade_id and item.get("status") == "waiting":
                item["status"] = status
                item["terminal_at"] = datetime.now(timezone.utc).isoformat()
                _save_raw(data)
                terminalized = True
                break
    if terminalized:
        from app.services.trade_outcome_registry import terminalize_setup_outcome
        terminalize_setup_outcome(trade_id, status)
    return terminalized


def mark_pending_setup_entered(trade_id: str) -> bool:
    """Retire a pending setup after Kraken proves that its order filled.

    Entering a trade is not a terminal trade outcome, so this deliberately
    does not call terminalize_setup_outcome. The active-trade/outcome registry
    becomes authoritative after the transition.
    """
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        for item in data.values():
            if item.get("trade_id") == trade_id and item.get("status") == "waiting":
                item["status"] = "entered"
                item["terminal_at"] = datetime.now(timezone.utc).isoformat()
                _save_raw(data)
                return True
    return False


def remove_pending_setup(symbol: str) -> bool:
    with registry_lock(registry_lock_file()):
        data = _load_raw()
        if symbol not in data:
            return False
        del data[symbol]
        _save_raw(data)
        return True
