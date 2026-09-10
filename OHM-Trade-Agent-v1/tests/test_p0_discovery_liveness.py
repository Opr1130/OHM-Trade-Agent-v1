from datetime import datetime, timezone


def test_interrupted_search_is_terminalized_on_next_canonical_cycle(tmp_path, monkeypatch):
    import app.services.operator_control as control

    monkeypatch.setattr(control, "STATE_FILE", tmp_path / "operator.json")
    monkeypatch.setattr(control, "LOCK_FILE", tmp_path / ".operator.lock")

    started = datetime(2026, 9, 6, 2, 22, 34, tzinfo=timezone.utc)
    recovered = datetime(2026, 9, 6, 2, 30, 0, tzinfo=timezone.utc)
    control.mark_search_started(started)

    assert control.recover_interrupted_search(recovered) is True
    state = control._load_state()
    assert state["last_search_status"] == "FAILED"
    assert state["last_search_failure_reason"] == "INTERRUPTED_PREVIOUS_CYCLE"
    assert state["last_search_started_at"] == started.isoformat()
    assert state["last_search_finished_at"] == recovered.isoformat()

    # Recovery is idempotent once the prior lifecycle has been terminalized.
    assert control.recover_interrupted_search(recovered) is False


def test_new_search_clears_prior_interruption_reason(tmp_path, monkeypatch):
    import app.services.operator_control as control

    monkeypatch.setattr(control, "STATE_FILE", tmp_path / "operator.json")
    monkeypatch.setattr(control, "LOCK_FILE", tmp_path / ".operator.lock")

    first = datetime(2026, 9, 6, 2, 22, 34, tzinfo=timezone.utc)
    recovery = datetime(2026, 9, 6, 2, 30, 0, tzinfo=timezone.utc)
    second = datetime(2026, 9, 6, 2, 35, 0, tzinfo=timezone.utc)
    finish = datetime(2026, 9, 6, 2, 36, 0, tzinfo=timezone.utc)

    control.mark_search_started(first)
    control.recover_interrupted_search(recovery)
    control.mark_search_started(second)
    state = control._load_state()
    assert state["last_search_status"] == "STARTED"
    assert "last_search_failure_reason" not in state

    control.mark_search_finished("COMPLETED", finish)
    state = control._load_state()
    assert state["last_search_status"] == "COMPLETED"
    assert state["last_search_finished_at"] == finish.isoformat()
    assert "last_search_failure_reason" not in state
