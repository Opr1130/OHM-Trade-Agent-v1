from types import SimpleNamespace

import pytest


def _decision(**overrides):
    base = dict(
        override_mode="AUTO",
        effective_mode="SEARCH",
        occupied_slots=0,
        active_trades=0,
        pending_setups=0,
        live_order_intents=0,
        quiet_hours=False,
        search_allowed=True,
        search_interval_seconds=300,
        reason="capacity available",
        cooldown_until=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _use_temp_cycle_lock(cycle, monkeypatch, tmp_path):
    monkeypatch.setattr(cycle, "CYCLE_LOCK_FILE", tmp_path / ".unified_cycle.lock")


def _stub_cycle_dependencies(cycle, monkeypatch):
    monkeypatch.setattr(
        cycle,
        "get_settings",
        lambda: SimpleNamespace(tradingview_v2_enabled=False),
    )
    monkeypatch.setattr(cycle, "_run_qualified_alert_retry_fail_open", lambda **kwargs: None)
    monkeypatch.setattr(cycle, "_run_entry_watch_recheck_fail_open", lambda: False)
    monkeypatch.setattr(cycle, "_run_paper_monitor_fail_open", lambda: None)
    monkeypatch.setattr(cycle, "_run_event_intelligence_fail_open", lambda **kwargs: None)


def test_maintenance_cycle_runs_only_active_protection(monkeypatch, tmp_path):
    import app.jobs.run_cycle as cycle

    _use_temp_cycle_lock(cycle, monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(cycle, "get_operator_decision", lambda: _decision(effective_mode="MAINTENANCE", search_allowed=False))
    monkeypatch.setattr(cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(cycle, "scan_main", lambda: calls.append("scan"))
    cycle.main()
    assert calls == ["active"]


def test_quiet_hours_keep_risk_and_selective_early_watch_active(monkeypatch, tmp_path):
    import app.jobs.run_cycle as cycle

    _use_temp_cycle_lock(cycle, monkeypatch, tmp_path)
    _stub_cycle_dependencies(cycle, monkeypatch)
    calls = []
    monkeypatch.setattr(cycle, "get_operator_decision", lambda: _decision(effective_mode="MONITOR", quiet_hours=True, search_allowed=False))
    monkeypatch.setattr(cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(
        cycle,
        "_run_early_watch_if_due",
        lambda **kwargs: calls.append(("early", kwargs["quiet_hours"])),
    )
    monkeypatch.setattr(cycle, "scan_main", lambda: calls.append("scan"))
    cycle.main()
    assert calls == ["active", ("early", True)]


def test_search_cycle_runs_monitors_then_scan_when_due(monkeypatch, tmp_path):
    import app.jobs.run_cycle as cycle

    _use_temp_cycle_lock(cycle, monkeypatch, tmp_path)
    _stub_cycle_dependencies(cycle, monkeypatch)
    calls = []
    decision = _decision()
    monkeypatch.setattr(cycle, "get_operator_decision", lambda: decision)
    monkeypatch.setattr(cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(cycle, "_run_early_watch_if_due", lambda **kwargs: None)
    monkeypatch.setattr(cycle, "search_due", lambda d: True)
    monkeypatch.setattr(cycle, "mark_search_started", lambda: calls.append("mark"))
    monkeypatch.setattr(
        cycle,
        "mark_search_finished",
        lambda status="COMPLETED": calls.append(("finish", status)),
    )
    monkeypatch.setattr(cycle, "scan_main", lambda: calls.append("scan"))
    cycle.main()
    assert calls == ["active", "pending", "mark", "scan", ("finish", "COMPLETED")]


def test_search_cycle_marks_finished_failed_when_scan_raises(monkeypatch, tmp_path):
    import app.jobs.run_cycle as cycle

    _use_temp_cycle_lock(cycle, monkeypatch, tmp_path)
    _stub_cycle_dependencies(cycle, monkeypatch)
    calls = []
    monkeypatch.setattr(cycle, "get_operator_decision", lambda: _decision())
    monkeypatch.setattr(cycle, "monitor_active_main", lambda: None)
    monkeypatch.setattr(cycle, "monitor_pending_main", lambda: None)
    monkeypatch.setattr(cycle, "_run_early_watch_if_due", lambda **kwargs: None)
    monkeypatch.setattr(cycle, "_run_paper_monitor_fail_open", lambda: None)
    monkeypatch.setattr(cycle, "_run_event_intelligence_fail_open", lambda **kwargs: None)
    monkeypatch.setattr(cycle, "_run_qualified_alert_retry_fail_open", lambda **kwargs: None)
    monkeypatch.setattr(cycle, "_run_entry_watch_recheck_fail_open", lambda: False)
    monkeypatch.setattr(
        cycle,
        "get_settings",
        lambda: SimpleNamespace(tradingview_v2_enabled=False),
    )
    monkeypatch.setattr(cycle, "search_due", lambda d: True)
    monkeypatch.setattr(cycle, "mark_search_started", lambda: calls.append("mark"))
    monkeypatch.setattr(
        cycle,
        "mark_search_finished",
        lambda status="COMPLETED": calls.append(("finish", status)),
    )
    monkeypatch.setattr(
        cycle,
        "run_scan_with_telemetry",
        lambda fn: (_ for _ in ()).throw(RuntimeError("scan hung")),
    )
    with pytest.raises(RuntimeError, match="scan hung"):
        cycle._run_cycle_once()
    assert calls == ["mark", ("finish", "FAILED")]