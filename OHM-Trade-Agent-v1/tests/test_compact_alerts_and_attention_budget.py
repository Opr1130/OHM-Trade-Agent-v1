from datetime import datetime, timedelta, timezone

from app.services.attention_budget import allow_new_noncritical, record_new_noncritical
from app.services.compact_alerts import (
    downside_scenario_pct,
    explosion_band,
    format_watch_alert,
    heuristic_risk_score,
)


def test_compact_watch_alert_is_short_and_explicit():
    message = format_watch_alert(
        symbol="ENAUSD",
        potential_low_pct=20,
        potential_high_pct=50,
        confidence_pct=78,
        risk_pct=32,
        downside_pct=9,
        reason="Volume expansion + re-acceleration",
        action="REVIEW ENTRY",
    )
    assert "Potential: +20% to +50%" in message
    assert "Confidence score: 78/100" in message
    assert "Risk score: 32/100" in message
    assert "Downside if wrong: up to -9%" in message
    assert "Action: REVIEW ENTRY" in message
    assert "*Heuristic scenario scores, not probabilities." not in message\n    assert len(message.splitlines()) <= 7


def test_scenario_helpers_do_not_claim_probability():
    assert explosion_band(95) == (20, 50)
    assert explosion_band(95, extended=True) == (10, 25)
    risk = heuristic_risk_score(80, liquidity_usd=40_000)
    assert risk >= 40
    assert 5 <= downside_scenario_pct(risk) <= 15


def test_shared_attention_budget_caps_new_noncritical(tmp_path):
    state = tmp_path / "attention.json"
    now = datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc)
    for index in range(3):
        assert allow_new_noncritical(now=now, max_new_24h=3, state_file=state)
        record_new_noncritical(kind=f"WATCH_{index}", now=now, state_file=state)
    assert not allow_new_noncritical(now=now, max_new_24h=3, state_file=state)
    assert allow_new_noncritical(
        now=now + timedelta(hours=25),
        max_new_24h=3,
        state_file=state,
    )
