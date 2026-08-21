from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


VERSION = "capital-rotation-shadow-v1"
DEFAULT_FULL_UTILIZATION_PCT = 95.0
DEFAULT_EXCEPTIONAL_CONFIDENCE = 92.0
DEFAULT_EXCEPTIONAL_UPSIDE_PCT = 12.0
DEFAULT_MIN_ROTATION_EDGE_PCT = 4.0


@dataclass(frozen=True)
class CapitalUtilization:
    account_capital: float
    deployed_capital: float
    utilization_pct: float
    fully_utilized: bool


@dataclass(frozen=True)
class IncumbentOpportunity:
    symbol: str
    capital: float
    remaining_upside_pct: float | None
    downside_to_stop_pct: float | None
    momentum_state: str | None = None

    @property
    def quality_proxy(self) -> float | None:
        if self.remaining_upside_pct is None:
            return None
        downside = max(0.0, float(self.downside_to_stop_pct or 0.0))
        return float(self.remaining_upside_pct) - downside


@dataclass(frozen=True)
class RotationDecision:
    version: str
    status: str
    candidate_symbol: str
    capital: CapitalUtilization
    candidate_confidence_score: float
    confidence_is_probability: bool
    candidate_remaining_upside_pct: float | None
    candidate_downside_pct: float | None
    weakest_incumbent_symbol: str | None
    weakest_incumbent_quality_proxy: float | None
    candidate_quality_proxy: float | None
    estimated_rotation_edge_pct: float | None
    exceptional_gate_passed: bool
    recommendation: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    shadow_only: bool = True
    production_decision_changed: bool = False
    trade_authority_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def capital_utilization(
    *,
    account_capital: float,
    active_trades: Iterable[Any],
    full_utilization_pct: float = DEFAULT_FULL_UTILIZATION_PCT,
) -> CapitalUtilization:
    account = max(0.0, float(account_capital))
    deployed = 0.0
    for trade in active_trades:
        capital = getattr(trade, "capital", None)
        if isinstance(capital, (int, float)) and float(capital) > 0:
            deployed += float(capital)
    pct = 0.0 if account <= 0 else deployed / account * 100.0
    return CapitalUtilization(
        account_capital=round(account, 2),
        deployed_capital=round(deployed, 2),
        utilization_pct=round(pct, 2),
        fully_utilized=bool(account > 0 and pct >= float(full_utilization_pct)),
    )


def build_incumbent_opportunity(
    trade: Any,
    *,
    current_price: float | None,
) -> IncumbentOpportunity:
    direction = str(getattr(trade, "direction", "LONG") or "LONG").upper()
    target = float(getattr(trade, "target_2", 0.0) or 0.0)
    stop = float(getattr(trade, "stop_price", 0.0) or 0.0)
    price = float(current_price or 0.0)
    remaining: float | None = None
    downside: float | None = None
    if price > 0 and target > 0:
        remaining = ((price - target) / price * 100.0) if direction == "SHORT" else ((target - price) / price * 100.0)
    if price > 0 and stop > 0:
        downside = ((stop - price) / price * 100.0) if direction == "SHORT" else ((price - stop) / price * 100.0)
    return IncumbentOpportunity(
        symbol=str(getattr(trade, "symbol", "")).upper(),
        capital=float(getattr(trade, "capital", 0.0) or 0.0),
        remaining_upside_pct=(round(remaining, 4) if remaining is not None else None),
        downside_to_stop_pct=(round(max(0.0, downside), 4) if downside is not None else None),
    )


def evaluate_rotation(
    *,
    candidate_symbol: str,
    candidate_confidence_score: float,
    candidate_remaining_upside_pct: float | None,
    candidate_downside_pct: float | None,
    account_capital: float,
    active_trades: Iterable[Any],
    incumbents: Iterable[IncumbentOpportunity],
    full_utilization_pct: float = DEFAULT_FULL_UTILIZATION_PCT,
    exceptional_confidence: float = DEFAULT_EXCEPTIONAL_CONFIDENCE,
    exceptional_upside_pct: float = DEFAULT_EXCEPTIONAL_UPSIDE_PCT,
    min_rotation_edge_pct: float = DEFAULT_MIN_ROTATION_EDGE_PCT,
) -> RotationDecision:
    """Compare a new opportunity with currently deployed capital in shadow only.

    Confidence is deliberately treated as a score, not a probability. Remaining
    upside may be a target/upside proxy until prospective calibration is strong
    enough to replace it with an expected-return estimate.
    """
    active = list(active_trades)
    usage = capital_utilization(
        account_capital=account_capital,
        active_trades=active,
        full_utilization_pct=full_utilization_pct,
    )
    reasons: list[str] = []
    warnings: list[str] = [
        "confidence is a heuristic/model score, not a calibrated win probability",
        "remaining-upside inputs are comparative proxies until prospectively calibrated",
    ]

    incumbent_rows = [item for item in incumbents if item.quality_proxy is not None]
    weakest = min(incumbent_rows, key=lambda item: item.quality_proxy) if incumbent_rows else None

    candidate_quality: float | None = None
    if candidate_remaining_upside_pct is not None:
        candidate_quality = float(candidate_remaining_upside_pct) - max(0.0, float(candidate_downside_pct or 0.0))

    rotation_edge: float | None = None
    if candidate_quality is not None and weakest is not None and weakest.quality_proxy is not None:
        rotation_edge = candidate_quality - float(weakest.quality_proxy)

    exceptional = (
        float(candidate_confidence_score) >= float(exceptional_confidence)
        and candidate_remaining_upside_pct is not None
        and float(candidate_remaining_upside_pct) >= float(exceptional_upside_pct)
    )

    if not usage.fully_utilized:
        status = "CAPITAL_AVAILABLE"
        recommendation = "NORMAL_ALERT_POLICY"
        reasons.append(f"capital utilization is {usage.utilization_pct:.1f}%; rotation is not required")
    elif not exceptional:
        status = "FULL_CAPITAL_EXCEPTIONAL_GATE_NOT_MET"
        recommendation = "SUPPRESS_ROTATION_ALERT"
        reasons.append(
            f"full-capital exceptional gate requires score >= {exceptional_confidence:.0f} and upside proxy >= {exceptional_upside_pct:.1f}%"
        )
    elif weakest is None or rotation_edge is None:
        status = "INSUFFICIENT_INCUMBENT_EVIDENCE"
        recommendation = "WATCH_ROTATION"
        reasons.append("candidate is exceptional but incumbent remaining-upside evidence is incomplete")
    elif rotation_edge < float(min_rotation_edge_pct):
        status = "ROTATION_ADVANTAGE_TOO_SMALL"
        recommendation = "HOLD_CURRENT_CAPITAL"
        reasons.append(f"estimated comparative advantage is only {rotation_edge:+.2f}%")
    else:
        status = "HIGH_CONVICTION_ROTATION_CANDIDATE"
        recommendation = "REVIEW_ROTATION"
        reasons.append(f"candidate passes exceptional score/upside gate")
        reasons.append(
            f"candidate quality proxy exceeds {weakest.symbol} by approximately {rotation_edge:+.2f}%"
        )

    return RotationDecision(
        version=VERSION,
        status=status,
        candidate_symbol=candidate_symbol.upper(),
        capital=usage,
        candidate_confidence_score=round(float(candidate_confidence_score), 2),
        confidence_is_probability=False,
        candidate_remaining_upside_pct=(round(float(candidate_remaining_upside_pct), 4) if candidate_remaining_upside_pct is not None else None),
        candidate_downside_pct=(round(max(0.0, float(candidate_downside_pct or 0.0)), 4) if candidate_downside_pct is not None else None),
        weakest_incumbent_symbol=(weakest.symbol if weakest is not None else None),
        weakest_incumbent_quality_proxy=(round(float(weakest.quality_proxy), 4) if weakest is not None and weakest.quality_proxy is not None else None),
        candidate_quality_proxy=(round(candidate_quality, 4) if candidate_quality is not None else None),
        estimated_rotation_edge_pct=(round(rotation_edge, 4) if rotation_edge is not None else None),
        exceptional_gate_passed=exceptional,
        recommendation=recommendation,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )
