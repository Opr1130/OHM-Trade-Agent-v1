from app.services.active_trade_registry import ActiveTrade, add_trade
from app.services.pending_setup_registry import (
    get_pending_setups,
    remove_pending_setup,
)


def confirm_entry(
    symbol: str,
    actual_fill_price: float,
) -> ActiveTrade:
    symbol = symbol.upper()

    setup = next(
        (
            item
            for item in get_pending_setups()
            if item.symbol == symbol
        ),
        None,
    )

    if setup is None:
        raise ValueError(f"No pending setup found for {symbol}")

    if actual_fill_price > setup.chase_limit:
        raise ValueError(
            f"Fill {actual_fill_price} is above chase limit "
            f"{setup.chase_limit}"
        )

    if actual_fill_price <= setup.stop_price:
        raise ValueError(
            "Fill price is already at or below the setup stop"
        )

    trade = ActiveTrade(
        symbol=symbol,
        entry_price=actual_fill_price,
        stop_price=setup.stop_price,
        target_1=setup.target_1,
        target_2=setup.target_2,
        risk_level=setup.risk_level,
    )

    add_trade(trade)
    remove_pending_setup(symbol)

    return trade
