import math
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


def _require_finite_trade_geometry(trade: ActiveTrade) -> None:
    values = (
        trade.entry_price,
        trade.stop_price,
        trade.target_1,
        trade.target_2,
        trade.margin_leverage,
    )
    try:
        numeric = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{trade.symbol}: active trade geometry contains malformed values") from exc
    if not all(math.isfinite(value) for value in numeric):
        raise ValueError(f"{trade.symbol}: active trade geometry contains non-finite values")

    entry, stop, target_1, target_2, leverage = numeric
    if min(entry, stop, target_1, target_2) <= 0:
        raise ValueError(f"{trade.symbol}: active trade geometry contains non-positive prices")
    if leverage <= 0:
        raise ValueError(f"{trade.symbol}: active trade leverage must be positive")

    direction = (trade.direction or "").upper()
    if direction == "LONG":
        valid = 0 < stop < entry < target_1 < target_2
    elif direction == "SHORT":
        valid = 0 < target_2 < target_1 < entry < stop
    else:
        raise ValueError(f"{trade.symbol}: active trade direction must be LONG or SHORT")
    if not valid:
        raise ValueError(f"{trade.symbol}: active trade stop/target geometry is invalid for {direction}")


def monitor_trade(trade: ActiveTrade) -> TradeMonitorResult:
    _require_finite_trade_geometry(trade)
    client = KrakenClient()
    candles = client.get_ohlc(trade.symbol, interval=60)
    if len(candles) < 36:
        raise ValueError(f"{trade.symbol}: insufficient OHLC history for active-trade monitoring")

    closes = [float(c.close) for c in candles]
    volumes = [float(c.volume) for c in candles]
    if not all(math.isfinite(value) and value > 0 for value in closes):
        raise ValueError(f"{trade.symbol}: OHLC close data must be finite and positive")
    if not all(math.isfinite(value) and value >= 0 for value in volumes):
        raise ValueError(f"{trade.symbol}: OHLC volume data must be finite and non-negative")

    current_price = closes[-1]
    # Current price is live monitoring evidence; technical corroboration is
    # intentionally based on completed bars so a partial 1H candle cannot
    # repaint EMA/MACD/RSI state while the monitor is running.
    completed_closes = closes[:-1]
    completed_volumes = volumes[:-1]
    ema20_value = ema(completed_closes, 20)
    rsi_value = rsi(completed_closes, 14)
    macd_line, macd_signal, _ = macd(completed_closes)
    vol_ratio = volume_ratio(completed_volumes, 20)
    direction = trade.direction.upper()

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

        weak_signals: list[str] = []
        if current_price > ema20_value:
            weak_signals.append("Price reclaimed EMA20 against short")
        if macd_line > macd_signal:
            weak_signals.append("MACD turned bullish against short")
        if rsi_value > 60:
            weak_signals.append("RSI momentum strengthened against short")
        if len(weak_signals) >= 2:
            reasons.extend(weak_signals)
            if action == "HOLD":
                action = "WARNING"
        elif len(weak_signals) == 1:
            reasons.append(f"{weak_signals[0]} (single signal — monitoring, not a warning yet)")

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

        weak_signals = []
        if current_price < ema20_value:
            weak_signals.append("Price lost EMA20")
        if macd_line < macd_signal:
            weak_signals.append("MACD turned bearish")
        if rsi_value < 40:
            weak_signals.append("RSI momentum weakened")
        if len(weak_signals) >= 2:
            reasons.extend(weak_signals)
            if action == "HOLD":
                action = "WARNING"
        elif len(weak_signals) == 1:
            reasons.append(f"{weak_signals[0]} (single signal — monitoring, not a warning yet)")

        if vol_ratio >= 1.8 and current_price < trade.entry_price:
            reasons.append("Heavy selling volume detected")
            if action in {"HOLD", "WARNING"}:
                action = "EXIT_NOW"

    pnl = None
    if trade.capital is not None and trade.capital > 0:
        if not math.isfinite(float(trade.capital)):
            raise ValueError(f"{trade.symbol}: capital must be finite")
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
