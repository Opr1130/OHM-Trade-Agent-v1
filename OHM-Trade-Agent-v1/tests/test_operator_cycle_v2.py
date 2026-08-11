from types import SimpleNamespace


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


def test_maintenance_cycle_runs_nothing(monkeypatch):
    import app.jobs.run_cycle as cycle

    calls = []
    monkeypatch.setattr(cycle, "get_operator_decision", lambda: _decision(effective_mode="MAINTENANCE", search_allowed=False))
    monkeypatch.setattr(cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(cycle, "scan_main", lambda: calls.append("scan"))
    cycle.main()
    assert calls == []


def test_quiet_hours_monitor_active_only(monkeypatch):
    import app.jobs.run_cycle as cycle

    calls = []
    monkeypatch.setattr(cycle, "get_operator_decision", lambda: _decision(effective_mode="MONITOR", quiet_hours=True, search_allowed=False))
    monkeypatch.setattr(cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(cycle, "scan_main", lambda: calls.append("scan"))
    cycle.main()
    assert calls == ["active"]


def test_search_cycle_runs_monitors_then_scan_when_due(monkeypatch):
    import app.jobs.run_cycle as cycle

    calls = []
    decision = _decision()
    monkeypatch.setattr(cycle, "get_operator_decision", lambda: decision)
    monkeypatch.setattr(cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(cycle, "search_due", lambda d: True)
    monkeypatch.setattr(cycle, "mark_search_started", lambda: calls.append("mark"))
    monkeypatch.setattr(cycle, "scan_main", lambda: calls.append("scan"))
    cycle.main()
    assert calls == ["active", "pending", "mark", "scan"]
