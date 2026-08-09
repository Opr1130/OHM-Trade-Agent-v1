from dataclasses import dataclass

from app.scanner.models import MarketSnapshot


STRONG_CONFIRMATION = "STRONG_CONFIRMATION"
CONFIRMED = "CONFIRMED"
MIXED = "MIXED"
DIVERGENCE = "DIVERGENCE"
SINGLE_MARKET = "SINGLE_MARKET"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class CrossPairConfirmation:
    status: str
    strengths: list[str]
    warnings: list[str]


def _direction(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def evaluate_cross_pair_confirmation(
    primary: MarketSnapshot,
    secondary: MarketSnapshot | None,
) -> CrossPairConfirmation:
    if not primary.secondary_pair:
        return CrossPairConfirmation(
            SINGLE_MARKET,
            ["Only one eligible USD/USDT analysis market"],
            [],
        )
    if secondary is None:
        return CrossPairConfirmation(
            UNAVAILABLE,
            [],
            ["Secondary-market OHLC confirmation unavailable"],
        )

    six_hour_agrees = (
        _direction(primary.momentum_6h_pct)
        == _direction(secondary.momentum_6h_pct)
    )
    twenty_four_hour_agrees = (
        _direction(primary.momentum_24h_pct)
        == _direction(secondary.momentum_24h_pct)
    )
    trend_agrees = primary.trend == secondary.trend
    both_positive = (
        primary.momentum_6h_pct > 0
        and secondary.momentum_6h_pct > 0
        and primary.momentum_24h_pct > 0
        and secondary.momentum_24h_pct > 0
    )
    both_bullish = primary.trend == secondary.trend == "bullish"
    both_volume_confirm = (
        primary.volume_ratio >= 1.1
        and secondary.volume_ratio >= 1.1
    )
    opposite_momentum = (
        _direction(primary.momentum_6h_pct)
        * _direction(secondary.momentum_6h_pct) < 0
        and _direction(primary.momentum_24h_pct)
        * _direction(secondary.momentum_24h_pct) < 0
    )
    opposite_trend = {primary.trend, secondary.trend} == {"bullish", "bearish"}

    if both_positive and both_bullish and both_volume_confirm:
        return CrossPairConfirmation(
            STRONG_CONFIRMATION,
            ["Both markets confirm bullish momentum, trend, and volume expansion"],
            [],
        )
    if opposite_momentum or opposite_trend:
        return CrossPairConfirmation(
            DIVERGENCE,
            [],
            ["USD and USDT markets materially diverge"],
        )
    if six_hour_agrees and twenty_four_hour_agrees and trend_agrees:
        return CrossPairConfirmation(
            CONFIRMED,
            ["Both markets agree on 6h/24h direction and broader trend"],
            [],
        )
    return CrossPairConfirmation(
        MIXED,
        [],
        ["USD and USDT markets provide mixed confirmation"],
    )
