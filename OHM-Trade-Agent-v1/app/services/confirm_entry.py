from dataclasses import asdict
from datetime import datetime, timezone

from app.services import active_trade_registry, pending_setup_registry
from app.services.active_trade_registry import ActiveTrade
from app.services.pending_setup_registry import PendingSetup
from app.services.registry_io import registry_lock
from app.services.trade_outcome_registry import mark_trade_entered


def _validate_fill(setup: PendingSetup, actual_fill_price: float) -> None:
    if actual_fill_price > setup.chase_limit:
        raise ValueError(
            f"Fill {actual_fill_price} is above chase limit {setup.chase_limit}"
        )
    if actual_fill_price <= setup.stop_price:
        raise ValueError("Fill price is already at or below the setup stop")


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
            existing_trade = next(
                (
                    ActiveTrade(**item)
                    for item in active.values()
                    if item.get("trade_id") == trade_id
                ),
                None,
            )

        if existing_trade is None:
            setup_item = next(
                (
                    item
                    for item in pending.values()
                    if item.get("status") == "waiting"
                    and (
                        (trade_id and item.get("trade_id") == trade_id)
                        or (symbol and item.get("symbol") == symbol)
                    )
                ),
                None,
            )
            if setup_item is None:
                identifier = trade_id or symbol
                raise ValueError(f"No confirmable pending setup found for {identifier}")

            setup = PendingSetup(**setup_item)
            fill_price = actual_fill_price
            if fill_price is None:
                fill_price = setup.confirmation_price
            if fill_price is None:
                raise ValueError(f"Trade ID {setup.trade_id} has no confirmation price")
            _validate_fill(setup, fill_price)

            created_trade = ActiveTrade(
                symbol=setup.symbol,
                entry_price=fill_price,
                stop_price=setup.stop_price,
                target_1=setup.target_1,
                target_2=setup.target_2,
                risk_level=setup.risk_level,
                opened_at=datetime.now(timezone.utc).isoformat(),
                trade_id=setup.trade_id,
            )
            active[created_trade.symbol] = asdict(created_trade)
            active_trade_registry._save_raw(active)

            pending.pop(setup.symbol, None)
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
