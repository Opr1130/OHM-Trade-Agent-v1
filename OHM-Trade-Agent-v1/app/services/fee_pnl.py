from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FeeAwarePnL:
    direction: str
    entry_price: float
    current_or_exit_price: float
    capital: float
    leverage: float
    notional: float
    gross_pnl: float
    gross_pnl_pct_on_capital: float
    entry_fee: float
    exit_fee: float
    financing_fee: float
    other_costs: float
    total_costs: float
    net_pnl: float
    net_pnl_pct_on_capital: float
    break_even_move_pct: float
    fee_source: str

    def as_dict(self) -> dict:
        return asdict(self)


def calculate_fee_aware_pnl(
    *,
    direction: str,
    entry_price: float,
    current_or_exit_price: float,
    capital: float,
    leverage: float = 1.0,
    estimated_entry_fee_rate: float = 0.004,
    estimated_exit_fee_rate: float = 0.004,
    actual_entry_fee: float | None = None,
    actual_exit_fee: float | None = None,
    financing_fee: float = 0.0,
    other_costs: float = 0.0,
) -> FeeAwarePnL:
    direction = direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    for name, value in {
        "entry_price": entry_price,
        "current_or_exit_price": current_or_exit_price,
        "capital": capital,
        "leverage": leverage,
    }.items():
        if value <= 0:
            raise ValueError(f"{name} must be positive")
    if estimated_entry_fee_rate < 0 or estimated_exit_fee_rate < 0:
        raise ValueError("fee rates cannot be negative")
    if financing_fee < 0 or other_costs < 0:
        raise ValueError("costs cannot be negative")

    notional = capital * leverage
    quantity = notional / entry_price
    if direction == "SHORT":
        gross = (entry_price - current_or_exit_price) * quantity
    else:
        gross = (current_or_exit_price - entry_price) * quantity

    entry_fee = actual_entry_fee if actual_entry_fee is not None else notional * estimated_entry_fee_rate
    exit_notional = quantity * current_or_exit_price
    exit_fee = actual_exit_fee if actual_exit_fee is not None else exit_notional * estimated_exit_fee_rate
    total_costs = entry_fee + exit_fee + financing_fee + other_costs
    net = gross - total_costs
    gross_pct = gross / capital * 100
    net_pct = net / capital * 100
    break_even_move_pct = total_costs / notional * 100 if notional else 0.0
    source = "ACTUAL" if actual_entry_fee is not None and actual_exit_fee is not None else "ESTIMATED_OR_MIXED"

    return FeeAwarePnL(
        direction=direction,
        entry_price=entry_price,
        current_or_exit_price=current_or_exit_price,
        capital=capital,
        leverage=leverage,
        notional=notional,
        gross_pnl=round(gross, 8),
        gross_pnl_pct_on_capital=round(gross_pct, 6),
        entry_fee=round(entry_fee, 8),
        exit_fee=round(exit_fee, 8),
        financing_fee=round(financing_fee, 8),
        other_costs=round(other_costs, 8),
        total_costs=round(total_costs, 8),
        net_pnl=round(net, 8),
        net_pnl_pct_on_capital=round(net_pct, 6),
        break_even_move_pct=round(break_even_move_pct, 6),
        fee_source=source,
    )


def projected_net_edge_pct(
    *,
    gross_target_move_pct: float,
    round_trip_fee_pct: float,
    financing_cost_pct: float = 0.0,
    execution_drag_pct: float = 0.0,
) -> float:
    return gross_target_move_pct - round_trip_fee_pct - financing_cost_pct - execution_drag_pct


def net_edge_is_sufficient(
    *,
    gross_target_move_pct: float,
    round_trip_fee_pct: float,
    financing_cost_pct: float = 0.0,
    execution_drag_pct: float = 0.0,
    minimum_net_edge_pct: float = 0.5,
) -> bool:
    return projected_net_edge_pct(
        gross_target_move_pct=gross_target_move_pct,
        round_trip_fee_pct=round_trip_fee_pct,
        financing_cost_pct=financing_cost_pct,
        execution_drag_pct=execution_drag_pct,
    ) >= minimum_net_edge_pct
