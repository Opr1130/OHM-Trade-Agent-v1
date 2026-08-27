from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
from typing import Any

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.paper_trade_control import CONTROL_FILE, paper_trade_enabled
from app.services.paper_trade_models import PaperTradeLifecycle
from app.services.paper_trade_registry import (
    EVENT_FILE,
    STATE_FILE,
    account_summary,
    create_lifecycle,
    has_nonterminal_symbol,
)


@dataclass(frozen=True)
class PaperTradeConfig:
    starting_equity: float
    capital_per_trade: float
    max_positions: int
    fee_rate: float
    slippage_bps: float
    tp1_fraction: float
    pending_ttl_hours: int
    max_hold_hours: int
    candle_interval_minutes: int

    @classmethod
    def from_settings(cls, settings: Any) -> "PaperTradeConfig":
        return cls(
            starting_equity=float(settings.paper_trade_starting_equity),
            capital_per_trade=float(settings.paper_trade_capital_per_trade),
            max_positions=int(settings.paper_trade_max_positions),
            fee_rate=float(settings.paper_trade_fee_rate),
            slippage_bps=float(settings.paper_trade_slippage_bps),
            tp1_fraction=float(settings.paper_trade_tp1_fraction),
            pending_ttl_hours=int(settings.paper_trade_pending_ttl_hours),
            max_hold_hours=int(settings.paper_trade_max_hold_hours),
            candle_interval_minutes=int(settings.paper_trade_candle_interval_minutes),
        )


@dataclass(frozen=True)
class PaperEnrollmentResult:
    status: str
    reason: str
    paper_trade_id: str | None = None


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _paper_id(episode_id: str, symbol: str) -> str:
    raw = f"{episode_id}|{symbol.upper()}|LONG"
    return "PAPER:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _decision_prices(snapshot: Any) -> tuple[float | None, float | None]:
    reference = (
        _positive(getattr(snapshot, "ticker_last", None))
        or _positive(getattr(snapshot, "last_price", None))
    )
    ask = _positive(getattr(snapshot, "ticker_ask", None))
    return reference, ask


def _valid_long_geometry(
    plan: EntryExitPlan,
    *,
    actual_entry: float | None = None,
) -> bool:
    values = (
        plan.entry_low,
        plan.entry_high,
        plan.chase_limit,
        plan.stop_price,
        plan.target_1,
        plan.target_2,
    )
    if any(_positive(value) is None for value in values):
        return False
    if not (
        float(plan.entry_low)
        <= float(plan.entry_high)
        <= float(plan.chase_limit)
    ):
        return False
    entry = float(actual_entry) if actual_entry is not None else float(plan.entry_low)
    return (
        float(plan.stop_price)
        < entry
        < float(plan.target_1)
        < float(plan.target_2)
    )


def enroll_paper_opportunity(
    *,
    candidate: dict[str, Any],
    snapshot: Any,
    plan: EntryExitPlan,
    episode_id: str,
    cohort_id: str,
    decision_at: datetime,
    config: PaperTradeConfig,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
    control_file: Path = CONTROL_FILE,
    enabled: bool | None = None,
) -> PaperEnrollmentResult:
    """Create an isolated paper lifecycle from a final qualified opportunity."""
    active = paper_trade_enabled(control_file) if enabled is None else bool(enabled)
    if not active:
        return PaperEnrollmentResult("DISABLED", "paper enrollment is off")

    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        return PaperEnrollmentResult("REJECTED", "decision_at must be timezone-aware")
    decision_at = decision_at.astimezone(timezone.utc)

    direction = str(
        getattr(snapshot, "trade_direction", "")
        or candidate.get("direction")
        or plan.direction
        or "LONG"
    ).upper()
    if direction != "LONG":
        return PaperEnrollmentResult(
            "UNSUPPORTED_DIRECTION",
            "Paper Trade v1 is spot-long only",
        )
    if candidate.get("economic_qualified") is not True:
        return PaperEnrollmentResult(
            "NOT_QUALIFIED",
            "economic quality gate did not qualify this opportunity",
        )

    symbol = str(plan.symbol or getattr(snapshot, "symbol", "") or "").upper()
    base_asset = str(
        getattr(snapshot, "underlying_asset", "")
        or candidate.get("underlying_asset")
        or symbol
    ).upper()
    if not symbol or not episode_id or not cohort_id:
        return PaperEnrollmentResult("REJECTED", "canonical signal identity is incomplete")

    if has_nonterminal_symbol(symbol, state_file=state_file):
        return PaperEnrollmentResult(
            "ALREADY_TRACKED",
            f"paper lifecycle already active for {symbol}",
        )

    summary = account_summary(config.starting_equity, state_file=state_file)
    if summary.pending_entries + summary.open_positions >= config.max_positions:
        return PaperEnrollmentResult("CAPACITY", "paper position capacity reached")
    required_cash = config.capital_per_trade * (1.0 + config.fee_rate)
    if summary.available_capital + 1e-9 < required_cash:
        return PaperEnrollmentResult(
            "CAPITAL",
            "paper cash reserve is insufficient for capital plus entry fee",
        )

    reference, ask = _decision_prices(snapshot)
    if reference is None:
        return PaperEnrollmentResult("REJECTED", "decision-time market price unavailable")

    if not _valid_long_geometry(plan):
        return PaperEnrollmentResult(
            "REJECTED",
            "paper entry/stop/target geometry is invalid",
        )

    market_now = bool(plan.valid_now)
    limit_setup = str(plan.entry_style or "").lower() == "wait_for_pullback"
    if not market_now and not limit_setup:
        return PaperEnrollmentResult(
            "NOT_ACTIONABLE",
            "entry plan is neither enter-now nor approved long pullback",
        )

    paper_trade_id = _paper_id(episode_id, symbol)
    status = "OPEN" if market_now else "PENDING_ENTRY"
    entry_action = "MARKET_DECISION_TIME" if market_now else "LIMIT_PULLBACK"
    entry_limit = float(plan.entry_low)

    entry_price: float | None = None
    entry_fee = 0.0
    quantity = 0.0
    opened_at: str | None = None

    if market_now:
        market_reference = ask or reference
        entry_price = market_reference * (1.0 + config.slippage_bps / 10_000.0)
        if entry_price > float(plan.chase_limit):
            return PaperEnrollmentResult(
                "DO_NOT_CHASE",
                "decision-time simulated fill exceeded the approved chase boundary",
            )
        if not _valid_long_geometry(plan, actual_entry=entry_price):
            return PaperEnrollmentResult(
                "REJECTED",
                "simulated market fill invalidated stop/target geometry",
            )
        quantity = config.capital_per_trade / entry_price
        entry_fee = config.capital_per_trade * config.fee_rate
        opened_at = decision_at.isoformat()

    now_iso = decision_at.isoformat()
    lifecycle = PaperTradeLifecycle(
        paper_trade_id=paper_trade_id,
        episode_id=episode_id,
        cohort_id=cohort_id,
        symbol=symbol,
        base_asset=base_asset,
        direction="LONG",
        status=status,
        entry_action=entry_action,
        signal_at=now_iso,
        created_at=now_iso,
        updated_at=now_iso,
        entry_low=float(plan.entry_low),
        entry_high=float(plan.entry_high),
        entry_limit=entry_limit,
        chase_limit=float(plan.chase_limit),
        stop_price=float(plan.stop_price),
        target_1=float(plan.target_1),
        target_2=float(plan.target_2),
        risk_level=str(plan.risk_level),
        confidence=int(candidate.get("confidence") or 0),
        profit_rank=(
            int(candidate["profit_rank"])
            if candidate.get("profit_rank") is not None
            else None
        ),
        profit_rank_score=(
            float(candidate["profit_rank_score"])
            if candidate.get("profit_rank_score") is not None
            else None
        ),
        capital=float(config.capital_per_trade),
        fee_rate=float(config.fee_rate),
        slippage_bps=float(config.slippage_bps),
        tp1_fraction=float(config.tp1_fraction),
        pending_ttl_hours=int(config.pending_ttl_hours),
        max_hold_hours=int(config.max_hold_hours),
        reference_price=float(reference),
        candle_interval_minutes=int(config.candle_interval_minutes),
        reference_ask=float(ask) if ask is not None else None,
        entry_price=entry_price,
        entry_fee=entry_fee,
        quantity_initial=quantity,
        quantity_remaining=quantity,
        opened_at=opened_at,
        fees_paid=entry_fee,
        last_observed_price=float(reference),
        paper_only=True,
        exchange_write_authority=False,
    )
    try:
        stored = create_lifecycle(
            lifecycle,
            state_file=state_file,
            event_file=event_file,
        )
    except ValueError as exc:
        return PaperEnrollmentResult("ALREADY_TRACKED", str(exc))

    return PaperEnrollmentResult(
        "OPENED" if stored.status == "OPEN" else "PENDING",
        f"paper lifecycle {stored.status.lower()}",
        stored.paper_trade_id,
    )
