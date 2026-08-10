from dataclasses import dataclass

from app.exchanges.kraken import KrakenClient
from app.services.active_trade_registry import ActiveTrade


SEVERITY_RANK = {"normal": 0, "warning": 1, "critical": 2}


@dataclass
class EmergencyMoveResult:
    symbol: str
    emergency: bool
    severity: str
    current_price: float
    change_5m_pct: float
    change_15m_pct: float
    stop_distance_pct: float
    reasons: list[str]


def _raise_severity(current: str, new: str) -> str:
    return new if SEVERITY_RANK[new] > SEVERITY_RANK[current] else current


def detect_emergency_move(trade: ActiveTrade) -> EmergencyMoveResult:
    client = KrakenClient()
    candles_5m = client.get_ohlc(trade.symbol, interval=5)
    if len(candles_5m) < 4:
        raise ValueError("Not enough 5-minute candles for emergency monitoring")

    closes = [c.close for c in candles_5m]
    current = closes[-1]
    five_minutes_ago = closes[-2]
    fifteen_minutes_ago = closes[-4]
    change_5m_pct = (current - five_minutes_ago) / five_minutes_ago * 100
    change_15m_pct = (current - fifteen_minutes_ago) / fifteen_minutes_ago * 100
    direction = (trade.direction or "LONG").upper()

    if direction == "SHORT":
        stop_distance_pct = (trade.stop_price - current) / current * 100
    else:
        stop_distance_pct = (current - trade.stop_price) / current * 100

    reasons: list[str] = []
    severity = "normal"

    if direction == "SHORT":
        if current >= trade.stop_price:
            severity = _raise_severity(severity, "critical")
            reasons.append("Short stop price breached")
        if change_5m_pct >= 2.0:
            severity = _raise_severity(severity, "critical")
            reasons.append(f"Rapid 5-minute rise against short ({change_5m_pct:.2f}%)")
        elif change_5m_pct >= 1.0:
            severity = _raise_severity(severity, "warning")
            reasons.append(f"Sharp 5-minute rise against short ({change_5m_pct:.2f}%)")
        if change_15m_pct >= 3.0:
            severity = _raise_severity(severity, "critical")
            reasons.append(f"Rapid 15-minute rise against short ({change_15m_pct:.2f}%)")
        elif change_15m_pct >= 1.5:
            severity = _raise_severity(severity, "warning")
            reasons.append(f"15-minute strength against short ({change_15m_pct:.2f}%)")
    else:
        if current <= trade.stop_price:
            severity = _raise_severity(severity, "critical")
            reasons.append("Stop price breached")
        if change_5m_pct <= -2.0:
            severity = _raise_severity(severity, "critical")
            reasons.append(f"Rapid 5-minute drop detected ({change_5m_pct:.2f}%)")
        elif change_5m_pct <= -1.0:
            severity = _raise_severity(severity, "warning")
            reasons.append(f"Sharp 5-minute decline detected ({change_5m_pct:.2f}%)")
        if change_15m_pct <= -3.0:
            severity = _raise_severity(severity, "critical")
            reasons.append(f"Rapid 15-minute decline detected ({change_15m_pct:.2f}%)")
        elif change_15m_pct <= -1.5:
            severity = _raise_severity(severity, "warning")
            reasons.append(f"15-minute weakness detected ({change_15m_pct:.2f}%)")

    if 0 < stop_distance_pct <= 1.0:
        severity = _raise_severity(severity, "warning")
        reasons.append(f"Price is only {stop_distance_pct:.2f}% from the stop")
    if not reasons:
        reasons.append("No sudden adverse movement detected")

    return EmergencyMoveResult(
        symbol=trade.symbol,
        emergency=severity == "critical",
        severity=severity,
        current_price=round(current, 8),
        change_5m_pct=round(change_5m_pct, 2),
        change_15m_pct=round(change_15m_pct, 2),
        stop_distance_pct=round(stop_distance_pct, 2),
        reasons=reasons,
    )
