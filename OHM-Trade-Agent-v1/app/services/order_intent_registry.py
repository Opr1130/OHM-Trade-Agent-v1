from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.services.active_trade_registry import ActiveTrade, add_trade, get_trade
from app.services.registry_io import load_json, registry_lock, save_json_atomic


ORDER_FILE = Path("/app/data/order_intents.json")
LOCK_FILE = ORDER_FILE.parent / ".order_intents.lock"
LIVE_STATUSES = {"LIMIT_PLACED"}
TERMINAL_STATUSES = {"FILLED", "CANCELLED", "CLOSED"}


@dataclass
class OrderIntent:
    symbol: str
    direction: str
    limit_price: float
    capital: float
    stop_price: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    margin_leverage: float = 1.0
    risk_level: str = "manual"
    entry_action: str = "PLACE_LIMIT"
    source: str = "operator_api"
    trade_id: str = ""
    status: str = "LIMIT_PLACED"
    created_at: str = ""
    updated_at: str = ""
    filled_at: str | None = None
    cancelled_at: str | None = None
    actual_entry_fee: float | None = None
    fill_quantity: float | None = None
    exchange_order_txid: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, dict]:
    return load_json(ORDER_FILE)


def _save(data: dict[str, dict]) -> None:
    save_json_atomic(ORDER_FILE, data)


def _normalize(item: dict) -> OrderIntent:
    normalized = dict(item)
    normalized.setdefault("direction", "LONG")
    normalized.setdefault("margin_leverage", 1.0)
    normalized.setdefault("risk_level", "manual")
    normalized.setdefault("entry_action", "PLACE_LIMIT")
    normalized.setdefault("source", "operator_api")
    normalized.setdefault("status", "LIMIT_PLACED")
    normalized.setdefault("created_at", "")
    normalized.setdefault("updated_at", "")
    normalized.setdefault("filled_at", None)
    normalized.setdefault("cancelled_at", None)
    normalized.setdefault("actual_entry_fee", None)
    normalized.setdefault("fill_quantity", None)
    normalized.setdefault("exchange_order_txid", None)
    normalized.pop("fill_price", None)
    return OrderIntent(**normalized)


def register_order_intent(intent: OrderIntent) -> OrderIntent:
    intent.symbol = intent.symbol.upper()
    intent.direction = intent.direction.upper()
    if intent.direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    intent.entry_action = str(intent.entry_action or "PLACE_LIMIT").upper()
    if intent.entry_action not in {"ENTER_NOW", "PLACE_LIMIT"}:
        raise ValueError("entry_action must be ENTER_NOW or PLACE_LIMIT")
    intent.source = str(intent.source or "operator_api")
    if intent.limit_price <= 0 or intent.capital <= 0:
        raise ValueError("limit_price and capital must be positive")
    if intent.margin_leverage <= 0 or intent.margin_leverage > 3:
        raise ValueError("margin_leverage must be > 0 and <= 3")
    now = _now()
    intent.trade_id = intent.trade_id or f"OHM-{intent.symbol}-{uuid4().hex[:12]}"
    intent.created_at = intent.created_at or now
    intent.updated_at = now
    intent.status = "LIMIT_PLACED"
    with registry_lock(LOCK_FILE):
        data = _load()
        for row in data.values():
            if row.get("status") in LIVE_STATUSES and row.get("symbol") == intent.symbol and row.get("direction", "LONG").upper() == intent.direction:
                raise ValueError(f"live order intent already exists for {intent.direction} {intent.symbol}")
        data[intent.trade_id] = asdict(intent)
        _save(data)
    return intent


def get_order_intent(trade_id: str) -> OrderIntent | None:
    with registry_lock(LOCK_FILE):
        row = _load().get(trade_id)
    return _normalize(row) if row else None


def get_live_order_intents() -> list[OrderIntent]:
    with registry_lock(LOCK_FILE):
        data = _load()
    return [_normalize(row) for row in data.values() if row.get("status") in LIVE_STATUSES]


def list_order_intents() -> list[OrderIntent]:
    with registry_lock(LOCK_FILE):
        data = _load()
    return [_normalize(row) for row in data.values()]


def bind_exchange_order_txid(trade_id: str, order_txid: str) -> OrderIntent:
    """Persist an exact exchange-order identity once reconciliation discovers it."""
    order_txid = str(order_txid or "").strip()
    if not order_txid:
        raise ValueError("order_txid is required")
    with registry_lock(LOCK_FILE):
        data = _load()
        if trade_id not in data:
            raise KeyError(trade_id)
        row = data[trade_id]
        existing = str(row.get("exchange_order_txid") or "")
        if existing and existing != order_txid:
            raise ValueError(f"intent {trade_id} is already bound to a different exchange order")
        row["exchange_order_txid"] = order_txid
        row["updated_at"] = _now()
        _save(data)
        return _normalize(row)


def update_limit_order(
    trade_id: str,
    *,
    limit_price: float | None = None,
    capital: float | None = None,
    stop_price: float | None = None,
    target_1: float | None = None,
    target_2: float | None = None,
) -> OrderIntent:
    with registry_lock(LOCK_FILE):
        data = _load()
        if trade_id not in data:
            raise KeyError(trade_id)
        row = data[trade_id]
        if row.get("status") not in LIVE_STATUSES:
            raise ValueError("only live limit intents can be updated")
        if limit_price is not None:
            if limit_price <= 0:
                raise ValueError("limit_price must be positive")
            row["limit_price"] = limit_price
        if capital is not None:
            if capital <= 0:
                raise ValueError("capital must be positive")
            row["capital"] = capital
        if stop_price is not None:
            row["stop_price"] = stop_price
        if target_1 is not None:
            row["target_1"] = target_1
        if target_2 is not None:
            row["target_2"] = target_2
        row["updated_at"] = _now()
        _save(data)
        return _normalize(row)


def cancel_order_intent(trade_id: str) -> OrderIntent:
    with registry_lock(LOCK_FILE):
        data = _load()
        if trade_id not in data:
            raise KeyError(trade_id)
        row = data[trade_id]
        if row.get("status") == "CANCELLED":
            return _normalize(row)
        if row.get("status") != "LIMIT_PLACED":
            raise ValueError("only LIMIT_PLACED intents can be cancelled")
        row["status"] = "CANCELLED"
        row["cancelled_at"] = _now()
        row["updated_at"] = row["cancelled_at"]
        _save(data)
        return _normalize(row)


def _trade_from_filled_row(row: dict) -> ActiveTrade:
    if row.get("stop_price") is None or row.get("target_1") is None or row.get("target_2") is None:
        raise ValueError("filled intent is missing stop/target data")
    fill_price = float(row.get("fill_price") or 0.0)
    if fill_price <= 0:
        raise ValueError("filled intent is missing a valid fill_price")
    capital = float(row.get("capital") or 0.0)
    if capital <= 0:
        raise ValueError("filled intent is missing valid capital")
    raw_fill_quantity = row.get("fill_quantity")
    fill_quantity = (
        float(raw_fill_quantity)
        if raw_fill_quantity is not None
        else None
    )
    if fill_quantity is not None and fill_quantity <= 0:
        raise ValueError("filled intent has invalid fill_quantity")
    return ActiveTrade(
        symbol=str(row.get("symbol") or "").upper(),
        entry_price=fill_price,
        stop_price=float(row["stop_price"]),
        target_1=float(row["target_1"]),
        target_2=float(row["target_2"]),
        risk_level=str(row.get("risk_level") or "manual"),
        trade_id=str(row.get("trade_id") or ""),
        direction=str(row.get("direction") or "LONG").upper(),
        margin_leverage=float(row.get("margin_leverage") or 1.0),
        capital=capital,
        entry_quantity=fill_quantity,
        remaining_quantity=fill_quantity,
        actual_entry_fee=row.get("actual_entry_fee"),
        opened_at=str(row.get("filled_at") or row.get("updated_at") or ""),
    )


def _retire_linked_pending_setup(trade_id: str) -> None:
    try:
        from app.services.pending_setup_registry import mark_pending_setup_entered

        mark_pending_setup_entered(trade_id)
    except Exception:
        # A proved Kraken fill and active-trade record remain authoritative.
        # The next recovery cycle will retry retiring stale pending state.
        pass


def recover_orphaned_filled_intents() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Repair the crash window between FILLED intent persistence and active-trade creation.

    FILLED intents are exchange/lifecycle truth. If one has no corresponding
    active trade, reconstruct the active record from fields already persisted
    with the fill. A conflicting different active trade is never overwritten.
    """
    with registry_lock(LOCK_FILE):
        rows = [dict(row) for row in _load().values() if row.get("status") == "FILLED"]

    recovered: list[str] = []
    failures: list[str] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        trade_id = str(row.get("trade_id") or "")
        if not symbol or not trade_id:
            failures.append(f"{trade_id or symbol or 'UNKNOWN'}: filled intent missing identity")
            continue
        existing = get_trade(symbol)
        if existing is not None and existing.status == "active":
            if existing.trade_id == trade_id:
                _retire_linked_pending_setup(trade_id)
                continue
            failures.append(f"{trade_id}: conflicting active trade already exists for {symbol}")
            continue
        try:
            trade = _trade_from_filled_row(row)
            add_trade(trade)
            from app.services.trade_outcome_registry import mark_trade_entered

            mark_trade_entered(trade, entry_price_source="recovered_limit_fill")
            _retire_linked_pending_setup(trade_id)
            recovered.append(trade_id)
        except Exception as exc:
            failures.append(f"{trade_id}: {exc}")
    return tuple(recovered), tuple(failures)


def mark_order_filled(
    trade_id: str,
    *,
    fill_price: float,
    actual_entry_fee: float | None = None,
    fill_quantity: float | None = None,
    entry_price_source: str = "manual_limit_fill",
) -> ActiveTrade:
    if fill_price <= 0:
        raise ValueError("fill_price must be positive")
    if actual_entry_fee is not None and actual_entry_fee < 0:
        raise ValueError("actual_entry_fee cannot be negative")
    if fill_quantity is not None and fill_quantity <= 0:
        raise ValueError("fill_quantity must be positive")
    with registry_lock(LOCK_FILE):
        data = _load()
        if trade_id not in data:
            raise KeyError(trade_id)
        row = data[trade_id]
        if row.get("status") != "LIMIT_PLACED":
            raise ValueError("only LIMIT_PLACED intents can be filled")
        if row.get("stop_price") is None or row.get("target_1") is None or row.get("target_2") is None:
            raise ValueError("stop_price, target_1, and target_2 are required before marking filled")
        row["status"] = "FILLED"
        row["filled_at"] = _now()
        row["updated_at"] = row["filled_at"]
        row["fill_price"] = fill_price
        row["fill_quantity"] = fill_quantity
        row["actual_entry_fee"] = actual_entry_fee
        _save(data)
        persisted = dict(row)

    trade = _trade_from_filled_row(persisted)
    add_trade(trade)
    from app.services.trade_outcome_registry import mark_trade_entered

    mark_trade_entered(trade, entry_price_source=entry_price_source)
    _retire_linked_pending_setup(trade_id)
    return trade
