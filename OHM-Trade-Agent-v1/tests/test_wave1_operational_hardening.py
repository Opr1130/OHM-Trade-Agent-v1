from types import SimpleNamespace

from app.jobs import run_cycle
from app.services import active_trade_monitor_runner
from app.services.external_order_review import ExternalOrderReviewSummary


def _learning_ok():
    return {
        "status": "OK",
        "paid_ai_calls": 0,
        "shadow": {"status": "NOT_DUE", "observations_added": 0},
        "profile_refreshed": False,
        "profile_status": "NOT_DUE",
    }


def _decision_search():
    return SimpleNamespace(
        override_mode="AUTO",
        effective_mode="SEARCH",
        occupied_slots=0,
        active_trades=0,
        live_order_intents=0,
        pending_setups=0,
        quiet_hours=False,
        reason="capacity available",
    )


def test_reconciliation_failure_does_not_block_active_monitor(monkeypatch):
    calls = []
    monkeypatch.setattr(run_cycle, "reconcile_kraken_account", lambda: (_ for _ in ()).throw(TimeoutError("busy")))
    monkeypatch.setattr(
        run_cycle,
        "review_external_open_orders",
        lambda: ExternalOrderReviewSummary(status="OK"),
    )
    monkeypatch.setattr(run_cycle, "run_learning_cycle", _learning_ok)
    monkeypatch.setattr(run_cycle, "get_operator_decision", _decision_search)
    monkeypatch.setattr(run_cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(run_cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(run_cycle, "search_due", lambda decision: False)

    run_cycle._run_cycle_once()

    assert calls == ["active", "pending"]


def test_external_order_review_failure_does_not_block_active_monitor(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_cycle,
        "reconcile_kraken_account",
        lambda: SimpleNamespace(
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
        ),
    )
    monkeypatch.setattr(run_cycle, "review_external_open_orders", lambda: (_ for _ in ()).throw(TimeoutError("busy")))
    monkeypatch.setattr(run_cycle, "run_learning_cycle", _learning_ok)
    monkeypatch.setattr(run_cycle, "get_operator_decision", _decision_search)
    monkeypatch.setattr(run_cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(run_cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(run_cycle, "search_due", lambda decision: False)

    run_cycle._run_cycle_once()

    assert calls == ["active", "pending"]


def test_cycle_lock_contention_skips_without_running_cycle(monkeypatch, capsys):
    calls = []

    class BusyLock:
        def __enter__(self):
            raise TimeoutError("held")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(run_cycle, "registry_lock", lambda *args, **kwargs: BusyLock())
    monkeypatch.setattr(run_cycle, "_run_cycle_once", lambda: calls.append("ran"))

    run_cycle.main()

    assert calls == []
    assert "previous cycle still running" in capsys.readouterr().out


def test_active_monitor_registry_failure_returns_failure_summary(monkeypatch):
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="", telegram_chat_id=""),
    )
    monkeypatch.setattr(
        active_trade_monitor_runner,
        "get_active_trades",
        lambda: (_ for _ in ()).throw(TimeoutError("registry busy")),
    )

    summary = active_trade_monitor_runner.run_active_trade_monitor()

    assert summary.active_trades == 0
    assert summary.checked == 0
    assert summary.monitor_notifications_sent == 0
    assert summary.emergency_notifications_sent == 0
    assert summary.failures == ["active trade registry unavailable: registry busy"]
