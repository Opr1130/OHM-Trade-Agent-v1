from __future__ import annotations


def clamp_pct(value: float) -> int:
    return int(round(max(0.0, min(100.0, float(value)))))


def explosion_band(score: float, *, extended: bool = False) -> tuple[int, int]:
    """Map a heuristic continuation/transition score to a scenario band.

    The returned range is an upside scenario for alert readability, not a
    calibrated probability or promised return.
    """
    score = clamp_pct(score)
    if extended:
        if score >= 90:
            return 10, 25
        if score >= 75:
            return 8, 20
        return 5, 15
    if score >= 90:
        return 20, 50
    if score >= 80:
        return 15, 35
    if score >= 70:
        return 10, 25
    return 5, 15


def heuristic_risk_score(
    confidence_score: float,
    *,
    liquidity_usd: float | None = None,
    extended: bool = False,
) -> int:
    """Presentation-only risk score; never used as a trade gate."""
    risk = 100.0 - clamp_pct(confidence_score)
    if liquidity_usd is not None:
        if liquidity_usd < 50_000:
            risk += 25
        elif liquidity_usd < 250_000:
            risk += 12
    if extended:
        risk += 12
    return max(10, min(90, clamp_pct(risk)))


def downside_scenario_pct(risk_score: float) -> int:
    """Conservative watch-only downside scenario, capped at 15%."""
    risk = clamp_pct(risk_score)
    return max(5, min(15, int(round(5 + risk * 0.12))))


def one_line_reason(*reasons: str) -> str:
    for reason in reasons:
        text = " ".join(str(reason or "").split()).strip(" .")
        if text:
            return text[:140]
    return "Multi-factor market transition"


def format_watch_alert(
    *,
    symbol: str,
    potential_low_pct: float,
    potential_high_pct: float,
    confidence_pct: float,
    risk_pct: float,
    downside_pct: float,
    reason: str,
    action: str,
    title: str = "OHM MARKET WATCH",
) -> str:
    return (
        f"🚀 {title} — {symbol}\n"
        f"Potential: +{potential_low_pct:.0f}% to +{potential_high_pct:.0f}%\n"
        f"Confidence*: {confidence_pct:.0f}%\n"
        f"Risk*: {risk_pct:.0f}%\n"
        f"Downside if wrong*: up to -{downside_pct:.0f}%\n"
        f"Reason: {one_line_reason(reason)}\n"
        f"Action: {action}\n"
        "*Heuristic scenario scores, not probabilities."
    )


def pct_from_prices(target: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return (float(target) / float(reference) - 1.0) * 100.0


def downside_to_stop_pct(entry: float, stop: float, direction: str = "LONG") -> float:
    if entry <= 0:
        return 0.0
    direction = str(direction).upper()
    if direction == "SHORT":
        return max(0.0, (float(stop) / float(entry) - 1.0) * 100.0)
    return max(0.0, (1.0 - float(stop) / float(entry)) * 100.0)
