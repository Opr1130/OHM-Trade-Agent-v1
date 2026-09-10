from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone

from app.services import learning_scheduler


def test_learning_cycle_includes_wave5_outcomes_without_paid_ai(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
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
    monkeypatch.delenv("APP_ENV", raising=False)
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

    def fail(*, now):
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


def test_production_learning_is_remote_only_and_does_not_touch_local_observers(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("production core must not execute local learning compute")

    monkeypatch.setattr(learning_scheduler, "observe_due_shadows", forbidden)
    monkeypatch.setattr(learning_scheduler, "observe_due_price_movements", forbidden)
    monkeypatch.setattr(learning_scheduler, "observe_due_movement_discovery_outcomes", forbidden)
    monkeypatch.setattr(learning_scheduler, "observe_due_explosion_outcomes", forbidden)
    monkeypatch.setattr(learning_scheduler, "ingest_freqtrade_dry_run", forbidden)
    monkeypatch.setattr(learning_scheduler, "build_intelligence_learning_profile", forbidden)
    monkeypatch.setattr(learning_scheduler, "build_profitability_profile", forbidden)

    result = learning_scheduler.run_learning_cycle()

    assert result["status"] == "REMOTE_ONLY"
    assert result["paid_ai_calls"] == 0
    assert result["profile_refreshed"] is False
    assert result["reason"] == "production learning compute is isolated to the remote learning worker"
