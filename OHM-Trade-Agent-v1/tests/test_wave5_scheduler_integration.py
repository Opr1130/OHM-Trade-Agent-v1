from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone

from app.services import learning_scheduler


def test_learning_cycle_includes_wave5_outcomes_without_paid_ai(monkeypatch):
    now = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        learning_scheduler,
        "observe_due_shadows",
        lambda now: {"status": "OK", "observations_added": 1},
    )
    monkeypatch.setattr(
        learning_scheduler,
        "observe_due_price_movements",
        lambda now: {"status": "OK", "observations_added": 2},
    )
    monkeypatch.setattr(
        learning_scheduler,
        "observe_due_movement_discovery_outcomes",
        lambda now: {"status": "OK", "observations_added": 3},
    )
    monkeypatch.setattr(
        learning_scheduler,
        "observe_due_explosion_outcomes",
        lambda now: {
            "status": "OK",
            "snapshots": 8,
            "outcomes_added": 4,
            "pending_horizons": 6,
        },
    )
    monkeypatch.setattr(learning_scheduler, "registry_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        learning_scheduler,
        "load_json",
        lambda *_args, **_kwargs: {
            "last_profile_refresh_at": "2026-08-20T19:30:00+00:00",
            "last_profile_status": "PROVISIONAL",
        },
    )

    result = learning_scheduler.run_learning_cycle(now=now)

    assert result["status"] == "OK"
    assert result["paid_ai_calls"] == 0
    assert result["wave5_explosion_learning"]["outcomes_added"] == 4
    assert result["wave5_explosion_learning"]["pending_horizons"] == 6
    assert result["profile_refreshed"] is False


def test_wave5_outcome_failure_is_fail_open(monkeypatch):
    now = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        learning_scheduler,
        "observe_due_shadows",
        lambda now: {"status": "OK", "observations_added": 0},
    )
    monkeypatch.setattr(
        learning_scheduler,
        "observe_due_price_movements",
        lambda now: {"status": "OK", "observations_added": 0},
    )
    monkeypatch.setattr(
        learning_scheduler,
        "observe_due_movement_discovery_outcomes",
        lambda now: {"status": "OK", "observations_added": 0},
    )

    def fail(_now):
        raise RuntimeError("test failure")

    monkeypatch.setattr(learning_scheduler, "observe_due_explosion_outcomes", fail)
    monkeypatch.setattr(learning_scheduler, "registry_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        learning_scheduler,
        "load_json",
        lambda *_args, **_kwargs: {
            "last_profile_refresh_at": "2026-08-20T19:30:00+00:00",
        },
    )

    result = learning_scheduler.run_learning_cycle(now=now)

    assert result["status"] == "OK"
    assert result["paid_ai_calls"] == 0
    assert result["wave5_explosion_learning"]["status"] == "UNAVAILABLE"
    assert result["wave5_explosion_learning"]["outcomes_added"] == 0
    assert "RuntimeError" in result["wave5_explosion_learning"]["reason"]
