from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from app.scanner.models import MarketSnapshot
from app.services.entry_exit_advisor import EntryExitPlan


SIGNAL_CLASS = "PRICE_MOVEMENT"
SIGNAL_SUBTYPE = "VOLATILITY_EXPANSION"

WATCH = "WATCH"
READY = "READY"
CONFIRMED = "CONFIRMED"
ACTIVE = "ACTIVE"
EXPIRED = "EXPIRED"
UNCONFIRMED = "UNCONFIRMED"


@dataclass(frozen=True)
class MovementComponents:
    compression: int
    leverage: int
    participation: int
    liquidation_fuel: int
    order_book_fragility: int

    @property
    def total(self) -> int:
        return min(
            100,
            self.compression
            + self.leverage
            + self.participation
            + self.liquidation_fuel
            + self.order_book_fragility,
        )

    @property
    def independent_families(self) -> int:
        return sum(
            score > 0
            for score in (
                self.compression,
                self.leverage,
                self.participation,
                self.liquidation_fuel,
                self.order_book_fragility,
            )
        )


@dataclass(frozen=True)
class PriceMovementSignal:
    symbol: str
    signal_class: str
    subtype: str
    stage: str
    direction: str
    readiness_score: int
    score_is_probability: bool
    components: MovementComponents
    observed_at: datetime
    expires_at: datetime
    detection_timeframe: str
    confirmation_timeframe: str
    regime_timeframe: str
    expected_window_primary: str
    expected_window_secondary: str
    expected_move_low_atr: float
    expected_move_high_atr: float
    expected_move_low_pct: float
    expected_move_high_pct: float
    reference_price: float
    reference_atr: float
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    source: str

    @property
    def fingerprint(self) -> str:
        score_bucket = int(self.readiness_score / 5) * 5
        return ":".join(
            (
                self.stage,
                self.direction,
                str(score_bucket),
                self.detection_timeframe,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_at"] = self.observed_at.isoformat()
        payload["expires_at"] = self.expires_at.isoformat()
        payload["fingerprint"] = self.fingerprint
        payload["actionable"] = False
        return payload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _derivative_rows(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(evidence, dict):
        return []
    bundle = evidence.get("bundle")
    if not isinstance(bundle, dict):
        return []
    rows = bundle.get("derivatives")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("status") == "AVAILABLE"]


def _average_field(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [
        float(row[field])
        for row in rows
        if isinstance(row.get(field), (int, float))
    ]
    return mean(values) if values else None


def _compression_score(snapshot: MarketSnapshot, reasons: list[str]) -> int:
    if snapshot.movement_data_status != "AVAILABLE":
        return 0
    bandwidth_rank = float(snapshot.bollinger_bandwidth_percentile)
    atr_rank = float(snapshot.atr_percentile)
    score = 0
    if bandwidth_rank <= 10:
        score += 20
        reasons.append(f"BBW is compressed at the {bandwidth_rank:.1f} percentile")
    elif bandwidth_rank <= 20:
        score += 16
        reasons.append(f"BBW is unusually narrow at the {bandwidth_rank:.1f} percentile")
    elif bandwidth_rank <= 35:
        score += 10
        reasons.append(f"BBW compression is developing at the {bandwidth_rank:.1f} percentile")

    if atr_rank <= 20:
        score += 10
        reasons.append(f"ATR is compressed at the {atr_rank:.1f} percentile")
    elif atr_rank <= 35:
        score += 6
        reasons.append(f"ATR compression is developing at the {atr_rank:.1f} percentile")
    elif atr_rank <= 50:
        score += 3
    return min(30, score)


def _leverage_score(
    snapshot: MarketSnapshot,
    rows: list[dict[str, Any]],
    reasons: list[str],
) -> int:
    oi_15m = _average_field(rows, "open_interest_change_15m_pct")
    oi_1h = _average_field(rows, "open_interest_change_1h_pct")
    oi_24h = _average_field(rows, "open_interest_change_24h_pct")
    acceleration = _average_field(rows, "open_interest_acceleration_zscore")

    base = 0
    if (acceleration is not None and acceleration >= 2.0) or (
        oi_15m is not None and oi_15m >= 1.5
    ):
        base = 15
    elif (acceleration is not None and acceleration >= 1.0) or (
        oi_15m is not None and oi_15m >= 0.75
    ) or (oi_1h is not None and oi_1h >= 2.0):
        base = 10
    elif (oi_15m is not None and oi_15m >= 0.25) or (
        oi_1h is not None and oi_1h >= 0.75
    ) or (oi_24h is not None and oi_24h >= 5.0):
        base = 6

    if base:
        description = []
        if oi_15m is not None:
            description.append(f"15m {oi_15m:+.2f}%")
        if oi_1h is not None:
            description.append(f"1h {oi_1h:+.2f}%")
        if acceleration is not None:
            description.append(f"z={acceleration:.2f}")
        reasons.append("open interest is accelerating" + (f" ({', '.join(description)})" if description else ""))

    quiet_threshold = max(0.15, float(snapshot.atr_pct) * 0.5)
    quiet = abs(float(snapshot.confirmed_price_change_1h_pct)) <= quiet_threshold
    if base and quiet:
        reasons.append("leverage is building while the confirmed price remains compressed")
        base += 10
    return min(25, base)


def _participation_score(snapshot: MarketSnapshot, reasons: list[str]) -> int:
    ratio = float(snapshot.movement_volume_ratio or snapshot.volume_ratio or 0.0)
    score = 0
    if ratio >= 2.0:
        score = 15
    elif ratio >= 1.5:
        score = 12
    elif ratio >= 1.1:
        score = 7
    elif ratio >= 0.9:
        score = 3
    if score >= 7:
        reasons.append(f"{snapshot.movement_timeframe} relative volume is {ratio:.2f}x")

    secondary = snapshot.secondary_volume_ratio
    if isinstance(secondary, (int, float)) and secondary >= 1.5:
        score += 5
        reasons.append(f"secondary Kraken market volume confirms at {secondary:.2f}x")
    return min(20, score)


def _liquidation_score(
    rows: list[dict[str, Any]],
    reasons: list[str],
) -> int:
    funding = _average_field(rows, "funding_rate_pct")
    liquidations_1h = _average_field(rows, "liquidations_1h_usd")
    liquidations_24h = _average_field(rows, "liquidations_24h_usd")
    long_short = _average_field(rows, "long_short_ratio")
    score = 0

    if liquidations_1h is not None and liquidations_24h is not None and liquidations_24h > 0:
        normal_hour = liquidations_24h / 24.0
        burst = liquidations_1h / normal_hour if normal_hour > 0 else 0.0
        if burst >= 3.0:
            score += 8
            reasons.append(f"1h liquidations are {burst:.1f}x the 24h hourly baseline")
        elif burst >= 1.5:
            score += 5
            reasons.append(f"liquidation activity is elevated at {burst:.1f}x baseline")

    if funding is not None:
        if abs(funding) >= 0.03:
            score += 4
            reasons.append(f"funding is crowded at {funding:+.4f}%")
        elif abs(funding) >= 0.015:
            score += 2

    if long_short is not None and (long_short >= 1.5 or long_short <= 0.67):
        score += 3
        reasons.append(f"long/short positioning is imbalanced at {long_short:.2f}")
    return min(15, score)


def _order_book_score(snapshot: MarketSnapshot, reasons: list[str]) -> int:
    execution = snapshot.execution_validation
    if execution is None or execution.status != "VALID":
        return 0
    bid = execution.bid_depth_050_usd
    ask = execution.ask_depth_050_usd
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return 0
    total = float(bid) + float(ask)
    if total <= 0:
        return 0
    imbalance = abs(float(bid) - float(ask)) / total
    if imbalance >= 0.50:
        reasons.append(f"0.5% order-book depth imbalance is {imbalance * 100:.1f}%")
        return 10
    if imbalance >= 0.35:
        reasons.append(f"0.5% order-book depth imbalance is {imbalance * 100:.1f}%")
        return 7
    if imbalance >= 0.20:
        return 4
    return 0


def _confirmed_direction(snapshot: MarketSnapshot) -> tuple[str, str]:
    tradingview = getattr(snapshot, "_tradingview_evidence", None)
    candidate_direction = str(snapshot.trade_direction or "").upper()
    classification = (
        str(tradingview.get("source_classification") or "")
        if isinstance(tradingview, dict)
        else ""
    )
    if (
        classification in {
            "TRADINGVIEW_CONFIRMED_NATIVE",
            "TRADINGVIEW_ONLY_CANDIDATE",
        }
        and candidate_direction in {"LONG", "SHORT"}
    ):
        return candidate_direction, "TRADINGVIEW_4H_1H_15M_CONFIRMED"

    close = float(snapshot.confirmed_close or 0.0)
    volume = float(snapshot.movement_volume_ratio or 0.0)
    if snapshot.movement_timeframe == "15M" and volume >= 1.5:
        if snapshot.prior_24h_range_high > 0 and close > snapshot.prior_24h_range_high:
            return "LONG", "KRAKEN_15M_RANGE_BREAK"
        if snapshot.prior_24h_range_low > 0 and close < snapshot.prior_24h_range_low:
            return "SHORT", "KRAKEN_15M_RANGE_BREAK"
    return UNCONFIRMED, "OHM_COMPRESSION_RADAR"


def _active_from_previous(
    snapshot: MarketSnapshot,
    previous: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not isinstance(previous, dict) or previous.get("stage") not in {CONFIRMED, ACTIVE}:
        return False, UNCONFIRMED
    direction = str(previous.get("direction") or UNCONFIRMED).upper()
    if direction not in {"LONG", "SHORT"}:
        return False, UNCONFIRMED
    try:
        reference = float(previous["reference_price"])
        reference_atr = float(previous["reference_atr"])
    except (KeyError, TypeError, ValueError):
        return False, UNCONFIRMED
    if reference <= 0 or reference_atr <= 0:
        return False, UNCONFIRMED
    move_atr = (
        (snapshot.last_price - reference) / reference_atr
        if direction == "LONG"
        else (reference - snapshot.last_price) / reference_atr
    )
    return move_atr >= 1.0, direction


def evaluate_price_movement(
    snapshot: MarketSnapshot,
    *,
    market_intelligence: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    now: datetime | None = None,
    watch_score: int = 35,
    ready_score: int = 70,
    expiry_hours: int = 12,
) -> PriceMovementSignal | None:
    """Classify volatility-expansion readiness without authorizing a trade."""
    if not 0 <= watch_score <= ready_score <= 100:
        raise ValueError("movement score thresholds must satisfy 0 <= watch <= ready <= 100")
    if expiry_hours <= 0:
        raise ValueError("expiry_hours must be positive")

    now = now or _now()
    reasons: list[str] = []
    warnings = list(snapshot.movement_warnings or [])
    rows = _derivative_rows(market_intelligence)
    components = MovementComponents(
        compression=_compression_score(snapshot, reasons),
        leverage=_leverage_score(snapshot, rows, reasons),
        participation=_participation_score(snapshot, reasons),
        liquidation_fuel=_liquidation_score(rows, reasons),
        order_book_fragility=_order_book_score(snapshot, reasons),
    )
    score = components.total
    direction, source = _confirmed_direction(snapshot)

    stage: str | None = None
    if (
        score >= watch_score
        and components.compression >= 16
        and components.independent_families >= 2
    ):
        stage = WATCH
    if (
        score >= ready_score
        and components.compression >= 20
        and components.leverage >= 6
        and components.independent_families >= 3
    ):
        stage = READY
    if (
        direction in {"LONG", "SHORT"}
        and score >= max(watch_score, ready_score - 15)
        and components.participation >= 7
        and components.independent_families >= 3
    ):
        stage = CONFIRMED

    active, active_direction = _active_from_previous(snapshot, previous)
    if active:
        previous_components = (
            previous.get("components")
            if isinstance(previous, dict)
            else None
        )
        if isinstance(previous_components, dict):
            components = MovementComponents(
                compression=max(
                    components.compression,
                    int(previous_components.get("compression") or 0),
                ),
                leverage=max(
                    components.leverage,
                    int(previous_components.get("leverage") or 0),
                ),
                participation=max(
                    components.participation,
                    int(previous_components.get("participation") or 0),
                ),
                liquidation_fuel=max(
                    components.liquidation_fuel,
                    int(previous_components.get("liquidation_fuel") or 0),
                ),
                order_book_fragility=max(
                    components.order_book_fragility,
                    int(previous_components.get("order_book_fragility") or 0),
                ),
            )
            score = components.total
        stage = ACTIVE
        direction = active_direction
        source = "OHM_CONFIRMED_MOVEMENT_TRACKING"

    previous_expiry = _parse(previous.get("expires_at")) if isinstance(previous, dict) else None
    if (
        stage is None
        and isinstance(previous, dict)
        and previous.get("stage") in {WATCH, READY}
        and previous_expiry is not None
        and now >= previous_expiry
    ):
        stage = EXPIRED
        direction = str(previous.get("direction") or UNCONFIRMED).upper()
        source = "OHM_MOVEMENT_EXPIRY"
        reasons.append("movement setup expired without confirmed expansion")

    if stage is None:
        return None

    if not rows:
        warnings.append("short-window derivatives evidence is unavailable")
    if direction == UNCONFIRMED and stage in {WATCH, READY}:
        warnings.append("direction is unconfirmed; no entry is authorized")
    warnings.append("readiness score is deterministic context, not probability")

    if stage == WATCH:
        primary_window, secondary_window = "4-12h", "12-24h"
        low_atr, high_atr = 1.5, 2.5
        expires_at = now + timedelta(hours=expiry_hours)
    elif stage == READY:
        primary_window, secondary_window = "1-4h", "4-12h"
        low_atr, high_atr = 2.0, 3.0
        expires_at = now + timedelta(hours=expiry_hours)
    elif stage == CONFIRMED:
        primary_window, secondary_window = "1-4h", "4-12h"
        low_atr, high_atr = (2.5, 3.5) if score >= 85 else (2.0, 3.0)
        expires_at = now + timedelta(hours=4)
    elif stage == ACTIVE:
        primary_window, secondary_window = "NOW-4h", "4-12h"
        low_atr, high_atr = (2.5, 3.5) if score >= 85 else (2.0, 3.0)
        expires_at = now + timedelta(hours=12)
    else:
        primary_window, secondary_window = "EXPIRED", "NONE"
        low_atr, high_atr = 0.0, 0.0
        expires_at = now

    atr_pct = max(0.0, float(snapshot.atr_pct))
    return PriceMovementSignal(
        symbol=snapshot.symbol.upper(),
        signal_class=SIGNAL_CLASS,
        subtype=SIGNAL_SUBTYPE,
        stage=stage,
        direction=direction,
        readiness_score=score,
        score_is_probability=False,
        components=components,
        observed_at=now,
        expires_at=expires_at,
        detection_timeframe=snapshot.movement_timeframe,
        confirmation_timeframe="15M" if "TRADINGVIEW" in source else snapshot.movement_timeframe,
        regime_timeframe="4H",
        expected_window_primary=primary_window,
        expected_window_secondary=secondary_window,
        expected_move_low_atr=low_atr,
        expected_move_high_atr=high_atr,
        expected_move_low_pct=round(low_atr * atr_pct, 3),
        expected_move_high_pct=round(high_atr * atr_pct, 3),
        reference_price=float(snapshot.last_price),
        reference_atr=float(snapshot.atr),
        reasons=tuple(reasons),
        warnings=tuple(dict.fromkeys(warnings)),
        source=source,
    )


def attach_actionable_plan(
    movement: dict[str, Any] | None,
    *,
    plan: EntryExitPlan,
    action: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Attach the already-approved OHM plan; never calculate a second plan."""
    if not isinstance(movement, dict):
        return None
    if movement.get("stage") not in {CONFIRMED, ACTIVE}:
        return movement
    direction = str(movement.get("direction") or "").upper()
    if direction != str(plan.direction).upper():
        return movement

    now = now or _now()
    payload = dict(movement)
    payload["actionable"] = True
    payload["entry_status"] = action
    payload["entry"] = {
        "style": plan.entry_style,
        "zone_low": plan.entry_low,
        "zone_high": plan.entry_high,
        "do_not_chase": plan.chase_limit,
    }
    payload["exits"] = {
        "stop": plan.stop_price,
        "target_1": plan.target_1,
        "target_2": plan.target_2,
        "reward_to_risk_1": plan.reward_to_risk_1,
        "reward_to_risk_2": plan.reward_to_risk_2,
    }
    payload["setup_expires_at"] = (now + timedelta(hours=4)).isoformat()
    return payload
