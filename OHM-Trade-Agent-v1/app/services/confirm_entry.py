from dataclasses import asdict
from datetime import datetime, timezone

from app.services import active_trade_registry, pending_setup_registry
from app.services.active_trade_registry import ActiveTrade
from app.services.pending_setup_registry import PendingSetup
from app.services.registry_io import registry_lock
from app.services.trade_outcome_registry import mark_trade_entered


def _validate_fill(setup: PendingSetup, actual_fill_price: float) -> None:
    direction = (setup.direction or "LONG").upper()
    if direction == "SHORT":
        if actual_fill_price < setup.chase_limit:
            raise ValueError(
                f"Fill {actual_fill_price} is below short chase limit {setup.chase_limit}"
            )
        if actual_fill_price >= setup.stop_price:
            raise ValueError("Short fill price is already at or above the setup stop")
    else:
        if actual_fill_price > setup.chase_limit:
            raise ValueError(
                f"Fill {actual_fill_price} is above chase limit {setup.chase_limit}"
            )
        if actual_fill_price <= setup.stop_price:
            raise ValueError("Fill price is already at or below the setup stop")


def _active_by_trade_id(active: dict, trade_id: str) -> ActiveTrade | None:
    return next(
        (
            active_trade_registry._from_item(item)
            for item in active.values()
            if item.get("trade_id") == trade_id
        ),
        None,
    )


def _transition_to_active(
    *,
    trade_id: str | None = None,
    symbol: str | None = None,
    actual_fill_price: float | None = None,
    entry_price_source: str,
) -> ActiveTrade:
    lock_file = pending_setup_registry.registry_lock_file()
    if lock_file != active_trade_registry.registry_lock_file():
        raise RuntimeError("Pending and active registries must share one data directory")

    existing_trade: ActiveTrade | None = None
    created_trade: ActiveTrade | None = None

    with registry_lock(lock_file):
        pending = pending_setup_registry._load_raw()
        active = active_trade_registry._load_raw()

        if trade_id:
            existing_trade = _active_by_trade_id(active, trade_id)

        if existing_trade is None:
            matches: list[tuple[str, dict]] = []
            for key, item in pending.items():
                if item.get("status") != "waiting":
                    continue
                if trade_id and item.get("trade_id") == trade_id:
                    matches.append((key, item))
                elif symbol and str(item.get("symbol") or "").upper() == symbol.upper():
                    matches.append((key, item))

            if symbol and not trade_id and len(matches) > 1:
                raise ValueError(
                    f"Multiple waiting setups exist for {symbol.upper()}; confirm by trade_id"
                )
            if not matches:
                identifier = trade_id or symbol
                raise ValueError(f"No confirmable pending setup found for {identifier}")

            pending_key, setup_item = matches[0]
            normalized_setup = dict(setup_item)
            normalized_setup.setdefault("direction", "LONG")
            normalized_setup.setdefault("margin_leverage", 1.0)
            setup = PendingSetup(**normalized_setup)
            fill_price = actual_fill_price
            if fill_price is None:
                fill_price = setup.confirmation_price
            if fill_price is None:
                raise ValueError(f"Trade ID {setup.trade_id} has no confirmation price")
            _validate_fill(setup, fill_price)

            symbol_key = setup.symbol.upper()
            existing_symbol = active.get(symbol_key)
            if existing_symbol and existing_symbol.get("status") == "active":
                existing_id = str(existing_symbol.get("trade_id") or "")
                if existing_id and existing_id == str(setup.trade_id or ""):
                    existing_trade = active_trade_registry._from_item(existing_symbol)
                else:
                    raise ValueError(f"active trade already exists for {symbol_key}")

            if existing_trade is None:
                created_trade = ActiveTrade(
                    symbol=symbol_key,
                    entry_price=fill_price,
                    stop_price=setup.stop_price,
                    target_1=setup.target_1,
                    target_2=setup.target_2,
                    risk_level=setup.risk_level,
                    opened_at=datetime.now(timezone.utc).isoformat(),
                    trade_id=setup.trade_id,
                    direction=setup.direction,
                    margin_leverage=setup.margin_leverage,
                )
                active[symbol_key] = asdict(created_trade)
                active_trade_registry._save_raw(active)
                pending.pop(pending_key, None)
                pending_setup_registry._save_raw(pending)

    trade = existing_trade or created_trade
    if trade is None:
        raise RuntimeError("Active trade transition produced no trade")
    mark_trade_entered(trade, entry_price_source=entry_price_source)
    return trade


def confirm_entry(symbol: str, actual_fill_price: float) -> ActiveTrade:
    return _transition_to_active(
        symbol=symbol.upper(),
        actual_fill_price=actual_fill_price,
        entry_price_source="manual_actual_fill",
    )


def confirm_trade_id(trade_id: str) -> ActiveTrade:
    return _transition_to_active(
        trade_id=trade_id,
        entry_price_source="confirmation_reference",
    )
