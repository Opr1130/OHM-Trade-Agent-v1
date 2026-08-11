from dataclasses import dataclass

from app.exchanges.kraken import KrakenClient
from app.indicators.technical import ema, macd, rsi, volume_ratio
from app.services.active_trade_registry import ActiveTrade
from app.services.fee_pnl import calculate_fee_aware_pnl


@dataclass
class TradeMonitorResult:
    symbol: str
    action: str
    current_price: float
    unrealized_pct: float
    reasons: list[str]
    gross_pnl: float | None = None
    estimated_total_costs: float | None = None
    net_pnl: float | None = None
    net_pnl_pct: float | None = None
    break_even_move_pct: float | None = None
    fee_source: str | None = None


def monitor_trade(trade: ActiveTrade) -> TradeMonitorResult:
    client = KrakenClient()
    candles = client.get_ohlc(trade.symbol, interval=60)
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]
    current_price = closes[-1]
    ema20_value = ema(closes, 20)
    rsi_value = rsi(closes, 14)
    macd_line, macd_signal, _ = macd(closes)
    vol_ratio = volume_ratio(volumes, 20)
    direction = (trade.direction or "LONG").upper()

    raw_move = (current_price - trade.entry_price) / trade.entry_price * 100
    unrealized_pct = -raw_move if direction == "SHORT" else raw_move
    reasons: list[str] = []
    action = "HOLD"

    if direction == "SHORT":
        if current_price >= trade.stop_price:
            action = "EXIT_NOW"
            reasons.append("Short stop price breached")
        elif current_price <= trade.target_2:
            action = "TAKE_PROFIT"
            reasons.append("Short Target 2 reached")
        elif current_price <= trade.target_1:
            action = "TAKE_PROFIT"
            reasons.append("Short Target 1 reached")

        if current_price > ema20_value:
            reasons.append("Price reclaimed EMA20 against short")
            if action == "HOLD":
                action = "WARNING"
        if macd_line > macd_signal:
            reasons.append("MACD turned bullish against short")
            if action == "HOLD":
                action = "WARNING"
        if rsi_value > 60:
            reasons.append("RSI momentum strengthened against short")
            if action == "HOLD":
                action = "WARNING"
        if vol_ratio >= 1.8 and current_price > trade.entry_price:
            reasons.append("Heavy buying volume detected against short")
            if action in {"HOLD", "WARNING"}:
                action = "EXIT_NOW"
    else:
        if current_price <= trade.stop_price:
            action = "EXIT_NOW"
            reasons.append("Stop price breached")
        elif current_price >= trade.target_2:
            action = "TAKE_PROFIT"
            reasons.append("Target 2 reached")
        elif current_price >= trade.target_1:
            action = "TAKE_PROFIT"
            reasons.append("Target 1 reached")

        if current_price < ema20_value:
            reasons.append("Price lost EMA20")
            if action == "HOLD":
                action = "WARNING"
        if macd_line < macd_signal:
            reasons.append("MACD turned bearish")
            if action == "HOLD":
                action = "WARNING"
        if rsi_value < 40:
            reasons.append("RSI momentum weakened")
            if action == "HOLD":
                action = "WARNING"
        if vol_ratio >= 1.8 and current_price < trade.entry_price:
            reasons.append("Heavy selling volume detected")
            if action in {"HOLD", "WARNING"}:
                action = "EXIT_NOW"

    pnl = None
    if trade.capital is not None and trade.capital > 0:
        pnl = calculate_fee_aware_pnl(
            direction=direction,
            entry_price=trade.entry_price,
            current_or_exit_price=current_price,
            capital=trade.capital,
            leverage=trade.margin_leverage,
            actual_entry_fee=trade.actual_entry_fee,
            financing_fee=trade.financing_fee,
        )
        if pnl.gross_pnl > 0 and pnl.net_pnl <= 0:
            reasons.append(
                "Gross price move is positive but estimated net P/L remains negative after trading costs"
            )

    if not reasons:
        reasons.append("Trade structure remains healthy")

    return TradeMonitorResult(
        symbol=trade.symbol,
        action=action,
        current_price=round(current_price, 8),
        unrealized_pct=round(unrealized_pct, 2),
        reasons=reasons,
        gross_pnl=(pnl.gross_pnl if pnl else None),
        estimated_total_costs=(pnl.total_costs if pnl else None),
        net_pnl=(pnl.net_pnl if pnl else None),
        net_pnl_pct=(pnl.net_pnl_pct_on_capital if pnl else None),
        break_even_move_pct=(pnl.break_even_move_pct if pnl else None),
        fee_source=(pnl.fee_source if pnl else None),
    )
