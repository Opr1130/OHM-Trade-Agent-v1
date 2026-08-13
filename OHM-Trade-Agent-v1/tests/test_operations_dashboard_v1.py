from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.services import operations_analytics as analytics


def test_parse_scan_output_captures_funnel_and_rejections():
    text = """
Requested: 200
Analyzed: 199
Skipped: 1
Failed: 0
Technical shortlist: 8
Directional shortlist: LONG=3 SHORT=5
Regime: RISK_OFF
MARGIN AAAUSD: Status=INELIGIBLE Venue=N/A MaxLeverage=N/Ax
MARGIN BBBUSD: Status=INELIGIBLE Venue=N/A MaxLeverage=N/Ax
Execution structural/short-quality rejects: 1
AI top candidates: 3
Qualified alerts before deterministic quality gates: 1
Target quality rejects: 1
Economic rejects: 0
Qualified survivors: 0
Pending setups saved: 0
Telegram notifications sent: 0
"""
    result = analytics.parse_scan_output(text)
    assert result["requested"] == 200
    assert result["analyzed"] == 199
    assert result["technical_shortlist"] == 8
    assert result["long_shortlist"] == 3
    assert result["short_shortlist"] == 5
    assert result["market_regime"] == "RISK_OFF"
    assert result["margin_rejects"] == 2
    assert result["execution_rejects"] == 1
    assert result["qualified_alerts"] == 1
    assert result["target_rejects"] == 1


def test_summary_separates_today_and_all_time(monkeypatch, tmp_path):
    scan_file = tmp_path / "scan.jsonl"
    usage_file = tmp_path / "usage.jsonl"
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
    old = "2025-01-01T12:00:00+00:00"
    scan_rows = [
        {"timestamp_utc": today, "completed_at_utc": today, "analyzed": 100, "requested": 100, "technical_shortlist": 5, "market_regime": "RISK_OFF", "margin_rejects": 2},
        {"timestamp_utc": old, "completed_at_utc": old, "analyzed": 50, "requested": 50, "technical_shortlist": 2, "market_regime": "RISK_ON", "margin_rejects": 1},
    ]
    scan_file.write_text("\n".join(json.dumps(row) for row in scan_rows) + "\n", encoding="utf-8")
    usage_rows = [
        {"timestamp_utc": today, "candidate_count": 3, "total_tokens": 1000},
        {"timestamp_utc": old, "candidate_count": 2, "total_tokens": 500},
    ]
    usage_file.write_text("\n".join(json.dumps(row) for row in usage_rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(analytics, "SCAN_ACTIVITY_FILE", scan_file)
    monkeypatch.setattr(analytics, "USAGE_FILE", usage_file)
    monkeypatch.setattr(analytics, "get_shadow_records", lambda: [
        {"observed_at": today, "decision": "AI_REJECT", "direction": "LONG", "observations": {"5m": {"directional_move_pct": -1.0}}},
        {"observed_at": old, "decision": "ENTER_NOW", "direction": "LONG", "observations": {"5m": {"directional_move_pct": 1.0}}},
    ])
    monkeypatch.setattr(analytics, "get_outcomes", lambda: [
        {"terminal_timestamp": today, "net_pnl": 4.0, "gross_pnl": 5.0, "total_costs": 1.0},
        {"terminal_timestamp": old, "net_pnl": -2.0, "gross_pnl": -1.5, "total_costs": 0.5},
    ])
    monkeypatch.setattr(analytics, "get_active_trades", lambda: [])
    monkeypatch.setattr(analytics, "get_pending_setups", lambda: [])
    monkeypatch.setattr(analytics, "list_order_intents", lambda: [])

    daily = analytics.build_operations_summary("today")
    all_time = analytics.build_operations_summary("all")

    assert daily["market"]["pairs_analyzed"] == 100
    assert all_time["market"]["pairs_analyzed"] == 150
    assert daily["ai"]["chief_calls"] == 1
    assert all_time["ai"]["chief_calls"] == 2
    assert daily["decisions"]["rejected"] == 1
    assert all_time["decisions"]["accepted"] == 1
    assert daily["trading"]["profit_count"] == 1
    assert daily["trading"]["loss_count"] == 0
    assert all_time["trading"]["loss_count"] == 1
    assert daily["trading"]["net_pnl"] == 4.0
    assert all_time["trading"]["net_pnl"] == 2.0


def test_dashboard_html_is_public_but_analytics_api_requires_secret(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "dashboard-test-secret")
    client = TestClient(app)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "OHM AI — Operations & Learning" in page.text

    response = client.get("/api/analytics/summary?scope=today")
    assert response.status_code == 401
