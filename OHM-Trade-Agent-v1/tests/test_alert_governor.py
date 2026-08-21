from datetime import datetime, timedelta, timezone

from app.services.alert_governor import evaluate_opportunity_alert, record_opportunity_alert


def test_first_opportunity_is_immediate(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key="READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED",
        now=now,
        state_file=state,
    )

    assert decision.allow_immediate is True
    assert decision.reason == "IMMEDIATE_ALLOWED"


def test_repeat_state_is_suppressed_for_six_hours(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    transition = "READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED"
    record_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key=transition,
        now=now,
        state_file=state,
    )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key=transition,
        now=now + timedelta(hours=2),
        state_file=state,
    )

    assert decision.allow_immediate is False
    assert decision.reason == "REPEAT_STATE_COOLDOWN"
    assert decision.suppressed_to_digest is True


def test_meaningful_transition_can_surface_after_one_hour(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    record_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key="READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED",
        now=now,
        state_file=state,
    )

    too_soon = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(minutes=30),
        state_file=state,
    )
    allowed = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(hours=1, minutes=1),
        state_file=state,
    )

    assert too_soon.allow_immediate is False
    assert too_soon.reason == "TRANSITION_COOLDOWN"
    assert allowed.allow_immediate is True


def test_daily_immediate_budget_suppresses_ninth_opportunity(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for index in range(8):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
            now=now + timedelta(minutes=index),
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:NINTHUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(minutes=10),
        state_file=state,
    )

    assert decision.allow_immediate is False
    assert decision.reason == "DAILY_IMMEDIATE_BUDGET"
    assert decision.suppressed_to_digest is True


def test_budget_rolls_forward_after_twenty_four_hours(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for index in range(8):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
            now=now,
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:NEWUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(hours=24, seconds=1),
        state_file=state,
    )

    assert decision.allow_immediate is True
