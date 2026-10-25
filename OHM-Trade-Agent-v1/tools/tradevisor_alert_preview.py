"""Render synthetic Tradevisor alert previews for review.

SYNTHETIC ONLY. Every price, spread and balance here is invented. This tool
performs no market read, no network call, no persistence and no delivery; it
prints what the notifier *would* render so a human can review the format.

Usage:
    python -m tools.tradevisor_alert_preview
"""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.models import MarketSnapshot
from app.services import tradevisor_evaluation as evaluation
from app.services.tradevisor_ingress import TradevisorSignal
from app.services.tradevisor_notifier import format_tradevisor_alert

BANNER = "SYNTHETIC PREVIEW — invented prices, no market read, nothing delivered"


def synthetic_snapshot(**over) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=67000.0,
        ema20=66400.0,
        ema50=64100.0,
        ema200=60250.0,
        rsi=58.0,
        macd_line=180.0,
        macd_signal=120.0,
        macd_histogram=60.0,
        atr=1400.0,
        atr_pct=2.09,
        volume_ratio=1.5,
        technical_score=90,
        trend="bullish",
        recent_24h_high=68400.0,
        recent_24h_low=64300.0,
        recent_72h_high=69900.0,
        recent_72h_low=61200.0,
        momentum_6h_pct=0.9,
        momentum_24h_pct=3.1,
        momentum_72h_pct=6.4,
        rolling_24h_range_median_pct=4.8,
        rolling_24h_range_p75_pct=6.2,
        rolling_24h_range_p90_pct=7.9,
        rolling_72h_range_median_pct=7.1,
        rolling_72h_range_p75_pct=9.4,
        rolling_72h_range_p90_pct=11.8,
        rolling_24h_upside_median_pct=4.4,
        rolling_24h_upside_p75_pct=5.9,
        rolling_24h_upside_p90_pct=7.4,
        rolling_72h_upside_median_pct=6.8,
        rolling_72h_upside_p75_pct=8.9,
        rolling_72h_upside_p90_pct=11.2,
        rolling_24h_downside_median_pct=4.1,
        rolling_24h_downside_p75_pct=5.5,
        rolling_24h_downside_p90_pct=7.0,
        rolling_72h_downside_median_pct=6.5,
        rolling_72h_downside_p75_pct=8.6,
        rolling_72h_downside_p90_pct=10.9,
        ticker_last=67000.0,
        execution_validation=ExecutionValidation(
            status="VALID",
            book_coverage_status="COMPLETE",
            warnings=[],
            best_bid=66985.0,
            best_ask=67015.0,
            mid_price=67000.0,
            spread_pct=0.045,
            spread_bps=4.5,
            validation_notional_usd=250.0,
            buy_vwap=67008.0,
            sell_vwap=66992.0,
            buy_fully_covered=True,
            sell_fully_covered=True,
            estimated_visible_round_trip_market_drag_pct=0.048,
            recent_trade_status="FRESH",
            latest_trade_price=67000.0,
            latest_trade_age_seconds=12.0,
            recent_trade_count=20,
        ),
    )
    values.update(over)
    return MarketSnapshot(**values)


def synthetic_signal(direction: str = "BUY", price: float = 67000.0) -> TradevisorSignal:
    now = datetime.now(timezone.utc).replace(minute=0, second=5, microsecond=0)
    return TradevisorSignal(
        dedup_id=f"preview-{direction.lower()}",
        signal=direction,
        instrument="BTC/USD",
        market="BTCUSD",
        source_exchange="COINBASE",
        source_symbol="BTCUSD",
        timeframe_minutes=60,
        bar_opened_at=now - timedelta(hours=1),
        bar_closed_at=now,
        triggered_at=now,
        received_at=now,
        signal_price=price,
        alert_config_version=f"tradevisor-1h-{direction.lower()}",
    )


def build(signal=None, snapshot=None, equity: float = 10_000.0, active_trades=()):
    resolved = snapshot or synthetic_snapshot()
    return evaluation.evaluate_signal(
        signal or synthetic_signal(),
        settings=Settings(_env_file=None, webhook_secret="preview-only-secret"),
        snapshot_provider=lambda _market: ("ok", resolved, None),
        account_equity=equity,
        active_trades=list(active_trades),
    )


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _synthetic_actionable(decision, *, limit_entry: bool = False):
    """Test/preview-only: a deliberately synthetic actionable decision.

    Production evaluation returns WATCH while F6 and trusted 4H are missing.
    This helper exists ONLY so the preview can render the approved layout and is
    labelled synthetic everywhere it is used.
    """
    return replace(
        decision,
        decision=(
            evaluation.DECISION_SET_LIMIT_ENTRY
            if limit_entry
            else evaluation.DECISION_ENTER_NOW
        ),
        rejection_code=None,
        reason="SYNTHETIC PREVIEW — formatting only; no qualification is implied.",
        expires_at=decision.evaluated_at + timedelta(minutes=90),
    )


def main() -> int:
    print(BANNER)

    from app.services.tradevisor_observation import (
        format_observation,
        observation_candidate,
    )

    _section("ENTER NOW — approved layout (SYNTHETIC: F6/4H withheld)")
    # A larger account so the final-allocation economic gate can genuinely pass;
    # the authority gates still return WATCH, which is what makes this synthetic.
    enter_now = build(equity=250_000.0)
    candidate = observation_candidate(enter_now)
    if candidate is None:
        print(f"decision={enter_now.decision} rejection_code={enter_now.rejection_code}")
        print(f"reason={enter_now.reason}")
        print("No actionable layout: this fixture does not clear every deterministic gate.")
    else:
        print(format_tradevisor_alert(_synthetic_actionable(enter_now)))

    _section("SET LIMIT ENTRY — approved layout (SYNTHETIC: F6/4H withheld)")
    extended = synthetic_snapshot(
        last_price=71000.0, ticker_last=71000.0, ema20=66400.0
    )
    limit = build(
        signal=synthetic_signal(price=71000.0), snapshot=extended, equity=250_000.0
    )
    if (
        observation_candidate(limit) is not None
        and limit.evidence.get("shadow_proposed_action")
        == evaluation.DECISION_SET_LIMIT_ENTRY
    ):
        print(format_tradevisor_alert(_synthetic_actionable(limit, limit_entry=True)))
    else:
        print(f"decision={limit.decision} reason={limit.reason}")

    _section("WATCH — recorded, NOT delivered as a trade instruction")
    thin = build(equity=1_000.0)
    print(f"decision={thin.decision}")
    print(f"rejection_code={thin.rejection_code}")
    print(f"reason={thin.reason}")
    print("Telegram: no actionable alert (non-actionable decisions are never sent)")

    _section("OBSERVATION — substantive candidate withheld by an evidence gate")
    candidate = candidate if candidate is not None else observation_candidate(enter_now)
    if candidate is None:
        print("no observation cohort for this fixture")
    else:
        print(format_observation(candidate))
        print()
        print("Telegram: only if OPIP_TRADEVISOR_OBSERVATION_ENABLED is true.")

    _section("SELL — evidence only, never a short")
    sell = build(signal=synthetic_signal("SELL"))
    print(f"decision={sell.decision}")
    print(f"rejection_code={sell.rejection_code}")
    print(f"sell_semantics={sell.evidence['sell_semantics']}")
    print("Telegram: suppressed (non-actionable)")

    _section("EXPIRED — actionable plan past its lifetime")
    expired = replace(
        _synthetic_actionable(enter_now),
        expires_at=enter_now.evaluated_at - timedelta(minutes=1),
    )
    from app.services.tradevisor_notifier import notify_tradevisor_decision

    from types import SimpleNamespace

    outcome = notify_tradevisor_decision(
        expired,
        settings=SimpleNamespace(
            telegram_enabled=True,
            telegram_bot_token="synthetic",
            telegram_chat_id="synthetic",
        ),
        now=enter_now.evaluated_at,
        sender=lambda **_: (_ for _ in ()).throw(
            AssertionError("expired plans must never reach the sender")
        ),
    )
    print(f"notification_status={outcome.status}")
    print(f"reason={outcome.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
