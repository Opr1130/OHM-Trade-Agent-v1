from datetime import datetime, timedelta, timezone

from app.services.alert_governor import evaluate_opportunity_alert, record_opportunity_alert


def test_first_opportunity_creates_card(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key="READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED",
        now=now,
        state_file=state,
    )

    assert decision.action == "CREATE"
    assert decision.allow_immediate is True
    assert decision.reason == "NEW_SYMBOL"
    assert decision.message_id is None


def test_repeat_state_is_suppressed_for_six_hours(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    transition = "READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED"
    record_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key=transition,
        message_id=101,
        created_new=True,
        now=now,
        state_file=state,
    )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key=transition,
        now=now + timedelta(hours=2),
        state_file=state,
    )

    assert decision.action == "SUPPRESS"
    assert decision.reason == "SAME_STATE_COOLDOWN"
    assert decision.message_id == 101


def test_meaningful_transition_edits_existing_card_without_new_alert(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    record_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key="READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED",
        message_id=202,
        created_new=True,
        now=now,
        state_file=state,
    )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(minutes=10),
        state_file=state,
    )

    assert decision.action == "EDIT"
    assert decision.reason == "MEANINGFUL_TRANSITION"
    assert decision.message_id == 202


def test_same_state_gets_periodic_refresh_after_six_hours(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    transition = "READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED"
    record_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key=transition,
        message_id=303,
        created_new=True,
        now=now,
        state_file=state,
    )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TESTUSD",
        transition_key=transition,
        now=now + timedelta(hours=6, seconds=1),
        state_file=state,
    )

    assert decision.action == "EDIT"
    assert decision.reason == "PERIODIC_REFRESH"
    assert decision.message_id == 303


def test_new_card_budget_suppresses_ninth_distinct_coin(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for index in range(8):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
            message_id=1000 + index,
            created_new=True,
            now=now + timedelta(minutes=index),
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:NINTHUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(minutes=10),
        state_file=state,
    )

    assert decision.action == "SUPPRESS"
    assert decision.reason == "NEW_CARD_DAILY_BUDGET"


def test_edit_does_not_consume_new_card_budget(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for index in range(8):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED",
            message_id=2000 + index,
            created_new=True,
            now=now,
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:C0USD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(minutes=5),
        state_file=state,
    )

    assert decision.action == "EDIT"
    assert decision.message_id == 2000


def test_budget_rolls_forward_after_twenty_four_hours(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for index in range(8):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
            message_id=3000 + index,
            created_new=True,
            now=now,
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:NEWUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(hours=24, seconds=1),
        state_file=state,
    )

    assert decision.action == "CREATE"
