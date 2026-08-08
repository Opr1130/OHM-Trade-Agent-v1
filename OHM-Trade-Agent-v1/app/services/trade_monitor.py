from dataclasses import dataclass

from app.exchanges.kraken import KrakenClient
from app.indicators.technical import ema, macd, rsi, volume_ratio
from app.services.active_trade_registry import ActiveTrade


@dataclass
class TradeMonitorResult:
    symbol: str
    action: str
    current_price: float
    unrealized_pct: float
    reasons: list[str]


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

    unrealized_pct = (
        (current_price - trade.entry_price) / trade.entry_price
    ) * 100

    reasons: list[str] = []
    action = "HOLD"

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

    if not reasons:
        reasons.append("Trade structure remains healthy")

    return TradeMonitorResult(
        symbol=trade.symbol,
        action=action,
        current_price=round(current_price, 8),
        unrealized_pct=round(unrealized_pct, 2),
        reasons=reasons,
    )
