from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

from app.jobs import run_cycle, scan_opportunities
from app.services import (
    paper_trade_control,
    paper_trade_engine,
    paper_trade_models,
    paper_trade_monitor,
    paper_trade_registry,
    paper_trade_simulation,
)
from app.services.canonical_episode_capture import (
    build_canonical_episode_snapshots,
    canonical_episode_id,
)
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.paper_trade_control import set_paper_trade_enabled
from app.services.paper_trade_engine import PaperTradeConfig, enroll_paper_opportunity
from app.services.paper_trade_monitor import run_paper_trade_monitor
from app.services.paper_trade_registry import get_lifecycles


SIGNAL_AT = datetime(2026, 8, 27, 5, 7, tzinfo=timezone.utc)


def config() -> PaperTradeConfig:
    return PaperTradeConfig(
        starting_equity=10_000.0,
        capital_per_trade=1_000.0,
        max_positions=4,
        fee_rate=0.004,
        slippage_bps=10.0,
        tp1_fraction=0.5,
        pending_ttl_hours=24,
        max_hold_hours=24,
        candle_interval_minutes=15,
    )


def plan(symbol: str, *, pending: bool = False) -> EntryExitPlan:
    return EntryExitPlan(
        symbol=symbol,
        valid_now=not pending,
        entry_style="wait_for_pullback" if pending else "pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=102.0,
        stop_price=95.0,
        target_1=105.0,
        target_2=110.0,
        reward_to_risk_1=1.5,
        reward_to_risk_2=2.5,
        risk_level="low",
        reason="test",
        direction="LONG",
    )


def snap(symbol: str):
    return SimpleNamespace(
        symbol=symbol,
        underlying_asset=symbol.removesuffix("USD"),
        trade_direction="LONG",
        ticker_last=100.0,
        ticker_ask=100.0,
        last_price=99.0,
    )


def enroll(symbol, episode, *, pending, state, events):
    return enroll_paper_opportunity(
        candidate={
            "direction": "LONG",
            "economic_qualified": True,
            "underlying_asset": symbol.removesuffix("USD"),
            "confidence": 90,
            "profit_rank": 1,
            "profit_rank_score": 80.0,
        },
        snapshot=snap(symbol),
        plan=plan(symbol, pending=pending),
        episode_id=episode,
        cohort_id="COHORT:test",
        decision_at=SIGNAL_AT,
        config=config(),
        state_file=state,
        event_file=events,
        enabled=True,
    )


class FakeClient:
    def __init__(self, candles=None, failures=None, tickers=None):
        self.candles = candles or {}
        self.failures = set(failures or [])
        self.tickers = tickers or {}

    def get_ohlc(self, symbol, interval=15, since=None):
        if symbol in self.failures:
            raise RuntimeError("public market unavailable")
        return list(self.candles.get(symbol, []))

    def get_ticker(self, symbol):
        if symbol in self.failures:
            raise RuntimeError("public ticker unavailable")
        return dict(self.tickers.get(symbol, {"bid": 100.0, "last": 100.0}))


def candle(symbol_close=100.0):
    return SimpleNamespace(
        timestamp=int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()),
        open=100.0,
        high=101.0,
        low=99.5,
        close=symbol_close,
    )


def test_operator_off_cancels_pending_but_keeps_open_position_managed(tmp_path):
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"
    control = tmp_path / "control.json"

    assert enroll("SOLUSD", "EP:sol", pending=True, state=state, events=events).status == "PENDING"
    assert enroll("ETHUSD", "EP:eth", pending=False, state=state, events=events).status == "OPENED"
    set_paper_trade_enabled(False, path=control, now=SIGNAL_AT)

    summary = run_paper_trade_monitor(
        config(),
        client=FakeClient(candles={"ETHUSD": []}),
        now=datetime(2026, 8, 27, 5, 30, tzinfo=timezone.utc),
        state_file=state,
        event_file=events,
        control_file=control,
    )

    rows = {row.symbol: row for row in get_lifecycles(state_file=state)}
    assert summary.control_enabled is False
    assert summary.cancelled == 1
    assert rows["SOLUSD"].status == "CANCELLED"
    assert rows["SOLUSD"].exit_reason == "OPERATOR_OFF"
    assert rows["ETHUSD"].status == "OPEN"


def test_public_data_failure_isolated_per_paper_lifecycle(tmp_path):
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"
    control = tmp_path / "control.json"

    enroll("SOLUSD", "EP:sol", pending=False, state=state, events=events)
    enroll("ETHUSD", "EP:eth", pending=False, state=state, events=events)
    set_paper_trade_enabled(True, path=control, now=SIGNAL_AT)

    summary = run_paper_trade_monitor(
        config(),
        client=FakeClient(
            candles={"ETHUSD": [candle(100.5)]},
            failures={"SOLUSD"},
        ),
        now=datetime(2026, 8, 27, 5, 45, tzinfo=timezone.utc),
        state_file=state,
        event_file=events,
        control_file=control,
    )

    rows = {row.symbol: row for row in get_lifecycles(state_file=state)}
    assert len(summary.failures) == 1
    assert "SOLUSD" in summary.failures[0]
    assert rows["SOLUSD"].last_processed_candle_ts is None
    assert rows["ETHUSD"].last_processed_candle_ts == candle().timestamp


def test_canonical_episode_helper_matches_persisted_episode_identity():
    rows = [
        SimpleNamespace(
            symbol="SOLUSD",
            underlying_asset="SOL",
            ticker_last=100.0,
            last_price=99.0,
        ),
        SimpleNamespace(
            symbol="ETHUSD",
            underlying_asset="ETH",
            ticker_last=2000.0,
            last_price=1990.0,
        ),
    ]
    snapshots = build_canonical_episode_snapshots(
        rows,
        candidates=(),
        decision_at=SIGNAL_AT,
        signal_quality_enabled=False,
        scan_source="LIVE_OPPORTUNITY_SCAN",
    )
    by_symbol = {row["symbol"]: row for row in snapshots}
    assert canonical_episode_id(
        rows,
        decision_at=SIGNAL_AT,
        symbol="SOLUSD",
    ) == by_symbol["SOLUSD"]["episode_id"]


def test_paper_modules_have_no_private_exchange_or_live_lifecycle_dependencies():
    modules = (
        paper_trade_control,
        paper_trade_engine,
        paper_trade_models,
        paper_trade_monitor,
        paper_trade_registry,
        paper_trade_simulation,
    )
    source = "\n".join(inspect.getsource(module) for module in modules)
    forbidden = (
        "kraken_private",
        "order_intent_registry",
        "active_trade_registry",
        "pending_setup_registry",
        "register_trade",
        "confirm_entry",
        "telegram_notifier",
    )
    for name in forbidden:
        assert name not in source


def test_paper_enrollment_is_post_telegram_and_monitor_is_below_live_protection():
    scan_source = inspect.getsource(scan_opportunities.main)
    assert scan_source.rfind("send_trade_plan(") < scan_source.rfind(
        "_maybe_enroll_paper_opportunities("
    )

    cycle_source = inspect.getsource(run_cycle._run_cycle_once)
    paper_index = cycle_source.index("_run_paper_monitor_fail_open()")
    assert cycle_source.rfind("monitor_active_main()", 0, paper_index) >= 0
    assert cycle_source.rfind("monitor_pending_main()", 0, paper_index) >= 0
