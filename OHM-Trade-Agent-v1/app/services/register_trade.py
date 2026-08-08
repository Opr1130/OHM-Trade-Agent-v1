from app.services.active_trade_registry import ActiveTrade, add_trade


def register_trade(
    symbol: str,
    entry_price: float,
    stop_price: float,
    target_1: float,
    target_2: float,
    risk_level: str,
) -> ActiveTrade:
    risk_level = risk_level.lower()

    if risk_level not in {"low", "medium"}:
        raise ValueError("risk_level must be low or medium")

    if not (stop_price < entry_price < target_1 < target_2):
        raise ValueError(
            "Expected stop < entry < target_1 < target_2"
        )

    trade = ActiveTrade(
        symbol=symbol.upper(),
        entry_price=entry_price,
        stop_price=stop_price,
        target_1=target_1,
        target_2=target_2,
        risk_level=risk_level,
    )

    add_trade(trade)
    return trade
