from __future__ import annotations

from app.services.active_trade_registry import ActiveTrade
from app.services.fee_pnl import calculate_fee_aware_pnl
from app.services.registry_io import load_json, registry_lock, save_json_atomic
from app.services.trade_outcome_registry import OUTCOME_FILE, registry_lock_file


def _find_key(data: dict, trade_id: str, symbol: str) -> str | None:
    if trade_id and trade_id in data:
        return trade_id
    for key, item in data.items():
        if trade_id and item.get("trade_id") == trade_id:
            return key
        if not trade_id and item.get("symbol") == symbol.upper() and not item.get("terminal_status"):
            return key
    return None


def capture_entry_financials(trade: ActiveTrade) -> None:
    with registry_lock(registry_lock_file()):
        data = load_json(OUTCOME_FILE)
        key = _find_key(data, trade.trade_id, trade.symbol)
        if key is None:
            return
        item = data[key]
        item["capital"] = trade.capital
        item["margin_leverage"] = trade.margin_leverage
        item["actual_entry_fee"] = trade.actual_entry_fee
        item["fee_accounting_status"] = "ENTRY_CAPTURED" if trade.actual_entry_fee is not None else "ENTRY_FEE_ESTIMATE_REQUIRED"
        save_json_atomic(OUTCOME_FILE, data)


def _mark_financing_unknown(trade: ActiveTrade) -> dict:
    result = {
        "status": "FINANCING_FEE_REQUIRED",
        "symbol": trade.symbol,
        "direction": trade.direction,
        "net_pnl": None,
    }
    with registry_lock(registry_lock_file()):
        data = load_json(OUTCOME_FILE)
        key = _find_key(data, trade.trade_id, trade.symbol)
        if key is None:
            return result
        item = data[key]
        item.update({
            "capital": trade.capital,
            "actual_entry_fee": trade.actual_entry_fee,
            "actual_exit_fee": trade.actual_exit_fee,
            "financing_fee": None,
            "net_pnl": None,
            "net_pnl_pct_on_capital": None,
            "net_profitable": None,
            "fee_accounting_status": "FINANCING_FEE_REQUIRED",
        })
        save_json_atomic(OUTCOME_FILE, data)
    return result


def finalize_financial_outcome(trade: ActiveTrade) -> dict | None:
    if trade.close_price is None or trade.capital is None:
        return None
    if trade.direction.upper() == "SHORT" and not trade.financing_fee_known:
        # Zero is not silently assumed to be the actual margin financing cost.
        # Keep the lifecycle closed but exclude the outcome from realized-P/L
        # learning until an explicit financing amount is captured.
        return _mark_financing_unknown(trade)

    pnl = calculate_fee_aware_pnl(
        direction=trade.direction,
        entry_price=trade.entry_price,
        current_or_exit_price=trade.close_price,
        capital=trade.capital,
        leverage=trade.margin_leverage,
        actual_entry_fee=trade.actual_entry_fee,
        actual_exit_fee=trade.actual_exit_fee,
        financing_fee=trade.financing_fee,
    )
    with registry_lock(registry_lock_file()):
        data = load_json(OUTCOME_FILE)
        key = _find_key(data, trade.trade_id, trade.symbol)
        if key is None:
            return pnl.as_dict()
        item = data[key]
        item.update({
            "capital": trade.capital,
            "gross_pnl": pnl.gross_pnl,
            "gross_pnl_pct_on_capital": pnl.gross_pnl_pct_on_capital,
            "actual_entry_fee": trade.actual_entry_fee,
            "actual_exit_fee": trade.actual_exit_fee,
            "financing_fee": trade.financing_fee,
            "total_trading_costs": pnl.total_costs,
            "net_pnl": pnl.net_pnl,
            "net_pnl_pct_on_capital": pnl.net_pnl_pct_on_capital,
            "break_even_move_pct": pnl.break_even_move_pct,
            "fee_accounting_status": pnl.fee_source,
            "net_profitable": pnl.net_pnl > 0,
        })
        save_json_atomic(OUTCOME_FILE, data)
    return pnl.as_dict()
