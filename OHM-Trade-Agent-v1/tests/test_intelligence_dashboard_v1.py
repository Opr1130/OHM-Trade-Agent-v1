from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services import dashboard_read_model as read_model


def test_daily_intelligence_series_links_early_signal_and_paper_outcome():
    events = [
        {
            "journey_id": "J1",
            "event_type": "EARLY_WATCH",
            "observed_at": "2026-08-20T10:00:00+00:00",
        },
        {
            "journey_id": "J1",
            "event_type": "QUALIFIED_SIGNAL",
            "signal_id": "S1",
            "observed_at": "2026-08-20T10:10:00+00:00",
        },
        {
            "journey_id": "J1",
            "event_type": "PAPER_OUTCOME",
            "signal_id": "S1",
            "observed_at": "2026-08-20T12:00:00+00:00",
            "payload": {"net_pnl": 10.0, "close_profit_ratio": 0.02},
        },
    ]
    rows = read_model._daily_intelligence_series(events)
    assert rows == [
        {
            "date": "2026-08-20",
            "early_watch_journeys": 1,
            "qualified_signals": 1,
            "paper_outcomes": 1,
            "early_to_signal_conversion_pct": 100.0,
            "paper_win_rate_pct": 100.0,
            "paper_avg_return_pct": 2.0,
            "early_watch_paper_win_rate_pct": 100.0,
        }
    ]


def test_failure_snapshot_reports_partial_recurrence_without_claiming_eradication():
    outcomes = [
        {
            "terminal_timestamp": "2026-08-20T12:00:00+00:00",
            "net_pnl": -5.0,
            "stop_observed": True,
            "mfe_pct": 0.1,
            "mae_pct": 2.0,
        },
        {
            "terminal_timestamp": "2026-08-21T12:00:00+00:00",
            "net_pnl": -7.0,
            "stop_observed": True,
            "mfe_pct": 0.2,
            "mae_pct": 2.5,
        },
    ]
    profile = {
        "loss_learning": {
            "potentially_avoidable_losses": 2,
            "potentially_avoidable_loss_dollars": 12.0,
        },
        "shadow_learning": {"missed_profitable": 3},
    }
    result = read_model._failure_snapshot(outcomes, profile)
    assert result["classified_losing_trades"] == 2
    assert result["by_reason"]["NO_FAVORABLE_EXCURSION"] == 2
    assert result["heuristic_recurrence_pct"] == 50.0
    assert result["coverage"] == "HEURISTIC_PARTIAL"
    assert result["missed_profitable_opportunities"] == 3


def test_dashboard_read_model_is_explicitly_read_only(monkeypatch):
    monkeypatch.setattr(read_model, "build_operations_summary", lambda scope: {"scope": scope})
    monkeypatch.setattr(
        read_model,
        "build_intelligence_learning_profile",
        lambda **kwargs: {
            "events_considered": 4,
            "journeys": 1,
            "early_watch_journeys": 1,
            "qualified_signals": 1,
            "paper_requested_signals": 1,
            "paper_outcome_signals": 1,
            "early_watch_to_signal_conversion_pct": 100.0,
            "signal_to_paper_outcome_conversion_pct": 100.0,
            "early_watch_to_signal_latency_minutes": {"average": 10.0},
            "paper_performance": {"count": 1, "wins": 1, "losses": 0, "win_rate_pct": 100.0, "avg_return_pct": 2.0},
            "paper_performance_with_early_watch": {"count": 1, "wins": 1, "losses": 0, "win_rate_pct": 100.0},
            "early_stage_pattern_performance": {},
        },
    )
    monkeypatch.setattr(
        read_model,
        "build_profitability_profile",
        lambda **kwargs: {
            "shadow_learning": {"samples": 10, "decision_accuracy_pct": 70.0, "missed_profitable": 1},
            "trade_calibration": {"status": "INSUFFICIENT_DATA", "samples": 1},
            "loss_learning": {"potentially_avoidable_losses": 0, "potentially_avoidable_loss_dollars": 0.0},
        },
    )
    monkeypatch.setattr(read_model, "get_outcomes", lambda: [])
    monkeypatch.setattr(read_model, "get_shadow_records", lambda: [])
    monkeypatch.setattr(read_model, "get_execution_records", lambda: [])
    monkeypatch.setattr(read_model, "_read_events", lambda: [])
    monkeypatch.setattr(
        read_model,
        "_paper_status",
        lambda: {"enabled": True, "engine": "FREQTRADE_DRY_RUN", "kraken_execution_authority": False, "status": {"status": "OK"}},
    )

    result = read_model.build_dashboard_read_model("all")
    assert result["read_only"] is True
    assert result["guardrails"] == {
        "dashboard_can_change_rankings": False,
        "dashboard_can_change_alerts": False,
        "dashboard_can_admit_paper_trades": False,
        "dashboard_can_write_exchange_orders": False,
    }
    assert result["paper_engine"]["kraken_execution_authority"] is False
    assert result["intelligence"]["evidence_state"] == "INSUFFICIENT_DATA"


def test_intelligence_dashboard_route_requires_secret_and_serves_graphs(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "dashboard-test-secret")
    get_settings.cache_clear()
    client = TestClient(app)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "O’Pip Intelligence Cockpit" in page.text
    assert 'id="intelligenceTrend"' in page.text
    assert "Failure Eradication" in page.text

    denied = client.get("/api/analytics/intelligence?scope=all")
    assert denied.status_code == 401
    get_settings.cache_clear()
