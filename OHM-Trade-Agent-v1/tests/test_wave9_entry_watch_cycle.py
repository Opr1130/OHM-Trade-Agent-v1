from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.jobs import run_cycle
from app.services import entry_watch_recheck


def _cycle_decision():
    return SimpleNamespace(
        override_mode="AUTO",
        effective_mode="SEARCH",
        occupied_slots=0,
        active_trades=0,
        live_order_intents=0,
        pending_setups=0,
        quiet_hours=False,
        reason="test",
    )


def _reconciliation():
    return SimpleNamespace(
        status="OK",
        mode="observe",
        active_checked=0,
        order_intents_checked=0,
        open_orders_seen=0,
        fills_seen=0,
        would_close=(),
        closed=(),
        would_fill=(),
        filled=(),
        reason="",
    )


def _external_review():
    return SimpleNamespace(
        status="OK",
        unmatched_orders_seen=0,
        new_reviews=0,
        notifications_sent=0,
        reason="",
    )


def _patch_cycle(monkeypatch, *, entry_ready: bool, search_due: bool):
    settings = SimpleNamespace(
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
        tradingview_v2_enabled=False,
        opip_event_store_enabled=False,
        signal_quality_scan_interval_seconds=600,
    )
    monkeypatch.setattr(run_cycle, "get_settings", lambda: settings)
    monkeypatch.setattr(run_cycle, "reconcile_kraken_account", _reconciliation)
    monkeypatch.setattr(run_cycle, "review_external_open_orders", _external_review)
    monkeypatch.setattr(
        run_cycle,
        "run_learning_cycle",
        lambda: {
            "status": "OK",
            "paid_ai_calls": 0,
            "shadow": {"status": "OK", "observations_added": 0},
            "price_movement": {"status": "OK", "observations_added": 0},
            "profile_refreshed": False,
            "profile_status": "OK",
        },
    )
    monkeypatch.setattr(run_cycle, "get_operator_decision", _cycle_decision)
    monkeypatch.setattr(run_cycle, "monitor_active_main", lambda: None)
    monkeypatch.setattr(run_cycle, "monitor_pending_main", lambda: None)
    monkeypatch.setattr(
        run_cycle,
        "_run_qualified_alert_retry_fail_open",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        run_cycle,
        "_run_entry_watch_recheck_fail_open",
        lambda: entry_ready,
    )
    monkeypatch.setattr(
        run_cycle,
        "_run_early_watch_if_due",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(run_cycle, "_run_paper_monitor_fail_open", lambda: None)
    monkeypatch.setattr(
        run_cycle,
        "_run_event_intelligence_fail_open",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(run_cycle, "search_due", lambda decision: search_due)
    return settings


def test_entry_watch_ready_forces_full_scan_when_normal_cadence_not_due(monkeypatch):
    _patch_cycle(monkeypatch, entry_ready=True, search_due=False)
    calls = []
    monkeypatch.setattr(run_cycle, "mark_search_started", lambda: calls.append("mark"))
    monkeypatch.setattr(
        run_cycle,
        "run_scan_with_telemetry",
        lambda fn: calls.append("scan"),
    )

    run_cycle._run_cycle_once()

    assert calls == ["mark", "scan"]


def test_not_ready_does_not_bypass_normal_scan_cadence(monkeypatch):
    _patch_cycle(monkeypatch, entry_ready=False, search_due=False)
    calls = []
    monkeypatch.setattr(run_cycle, "mark_search_started", lambda: calls.append("mark"))
    monkeypatch.setattr(
        run_cycle,
        "run_scan_with_telemetry",
        lambda fn: calls.append("scan"),
    )

    run_cycle._run_cycle_once()

    assert calls == []


def test_fast_recheck_is_after_active_protection_and_before_pending_monitor(monkeypatch):
    _patch_cycle(monkeypatch, entry_ready=False, search_due=False)
    calls = []
    monkeypatch.setattr(run_cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(
        run_cycle,
        "_run_qualified_alert_retry_fail_open",
        lambda **kwargs: calls.append("retry"),
    )
    monkeypatch.setattr(
        run_cycle,
        "_run_entry_watch_recheck_fail_open",
        lambda: calls.append("recheck") or False,
    )
    monkeypatch.setattr(run_cycle, "monitor_pending_main", lambda: calls.append("pending"))

    run_cycle._run_cycle_once()

    assert calls[:4] == ["active", "retry", "recheck", "pending"]


def test_fast_recheck_has_no_telegram_or_notifier_authority():
    source = inspect.getsource(entry_watch_recheck)
    assert "telegram" not in source.lower()
    assert "notifier" not in source.lower()
    assert "send_" not in source.lower()


def test_ready_fast_recheck_only_requests_full_scan(monkeypatch):
    row = {
        "symbol": "SOLUSD",
        "direction": "LONG",
        "risk_level": "low",
        "continuation_score": 80,
    }
    snapshot = SimpleNamespace(symbol="SOLUSD")
    plan = SimpleNamespace(valid_now=True)
    deferred = []

    monkeypatch.setattr(entry_watch_recheck, "due_entry_watch", lambda **kwargs: [row])
    monkeypatch.setattr(
        entry_watch_recheck,
        "analyze_symbol",
        lambda symbol: ("ok", snapshot, None),
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "build_entry_exit_plan",
        lambda *args, **kwargs: plan,
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "defer_entry_watch",
        lambda *args, **kwargs: deferred.append(args) or True,
    )

    summary = entry_watch_recheck.recheck_due_entry_watch()

    assert summary.full_scan_required is True
    assert summary.ready_symbols == ("SOLUSD",)
    assert deferred == [("SOLUSD", "LONG")]


def test_not_ready_fast_recheck_defers_without_full_scan(monkeypatch):
    row = {
        "symbol": "SOLUSD",
        "direction": "LONG",
        "risk_level": "low",
        "continuation_score": 80,
    }
    snapshot = SimpleNamespace(symbol="SOLUSD")
    plan = SimpleNamespace(valid_now=False)
    deferred = []

    monkeypatch.setattr(entry_watch_recheck, "due_entry_watch", lambda **kwargs: [row])
    monkeypatch.setattr(
        entry_watch_recheck,
        "analyze_symbol",
        lambda symbol: ("ok", snapshot, None),
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "build_entry_exit_plan",
        lambda *args, **kwargs: plan,
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "defer_entry_watch",
        lambda *args, **kwargs: deferred.append(args) or True,
    )

    summary = entry_watch_recheck.recheck_due_entry_watch()

    assert summary.full_scan_required is False
    assert summary.ready_symbols == ()
    assert summary.deferred == 1
    assert deferred