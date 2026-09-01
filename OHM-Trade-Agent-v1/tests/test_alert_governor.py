from datetime import datetime, timedelta, timezone
import json
import threading

from app.services.alert_governor import (
    evaluate_opportunity_alert,
    record_opportunity_alert,
    release_opportunity_alert_reservation,
)
from app.services.attention_budget import record_new_noncritical


def test_first_priority_opportunity_creates_card(tmp_path):
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
    assert decision.reason == "PRIORITY_NEW_SYMBOL"
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


def test_ordinary_new_card_budget_suppresses_ninth_distinct_coin(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for index in range(8):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="WATCH:WATCH_ONLY:STEADY:NOT_EXTENDED",
            message_id=1000 + index,
            created_new=True,
            now=now + timedelta(minutes=index),
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:NINTHUSD",
        transition_key="WATCH:WATCH_ONLY:STEADY:NOT_EXTENDED",
        now=now + timedelta(minutes=10),
        state_file=state,
    )

    assert decision.action == "SUPPRESS"
    assert decision.reason == "NEW_CARD_DAILY_BUDGET"


def test_ready_priority_bypasses_exhausted_ordinary_daily_budget(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for index in range(8):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="WATCH:WATCH_ONLY:STEADY:NOT_EXTENDED",
            message_id=1100 + index,
            created_new=True,
            now=now + timedelta(minutes=index),
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:READYUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(minutes=10),
        state_file=state,
    )

    assert decision.action == "CREATE"
    assert decision.reason == "PRIORITY_BYPASS_DAILY_BUDGET"


def test_signal_quality_breakout_and_actionable_are_priority(tmp_path):
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    for stage in ("BREAKOUT_CANDIDATE", "ACTIONABLE_REVIEW"):
        state = tmp_path / f"{stage}.json"
        for index in range(8):
            record_opportunity_alert(
                identity=f"FULL_MARKET_WATCH:C{index}USD",
                transition_key="EARLY_BUILDING:NONE:50:NORMAL",
                message_id=1200 + index,
                created_new=True,
                now=now,
                state_file=state,
            )

        decision = evaluate_opportunity_alert(
            identity=f"FULL_MARKET_WATCH:{stage}USD",
            transition_key=f"{stage}:COMPRESSION_RELEASE:80:NORMAL",
            now=now + timedelta(minutes=10),
            max_new_cards_24h=8,
            state_file=state,
        )

        assert decision.action == "CREATE"
        assert decision.reason == "PRIORITY_BYPASS_DAILY_BUDGET"


def test_priority_bypasses_shared_global_attention_budget(tmp_path):
    state = tmp_path / "governor.json"
    attention_state = tmp_path / "attention_budget_state.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    for index in range(12):
        record_new_noncritical(
            kind="TEST_CARD",
            now=now + timedelta(seconds=index),
            state_file=attention_state,
        )

    ordinary = evaluate_opportunity_alert(
        identity="EARLY_MOVER:ORDINARYUSD",
        transition_key="WATCH:WATCH_ONLY:STEADY:NOT_EXTENDED",
        now=now + timedelta(minutes=5),
        state_file=state,
    )
    priority = evaluate_opportunity_alert(
        identity="EARLY_MOVER:PRIORITYUSD",
        transition_key="READY:WAIT_FOR_PULLBACK:STEADY:NOT_EXTENDED",
        now=now + timedelta(minutes=5),
        state_file=state,
    )

    assert ordinary.action == "SUPPRESS"
    assert ordinary.reason == "GLOBAL_ATTENTION_BUDGET"
    assert priority.action == "CREATE"
    assert priority.reason == "PRIORITY_NEW_SYMBOL"


def test_priority_cannot_bypass_emergency_cap(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    # Default emergency cap is three times the ordinary eight-card budget.
    for index in range(24):
        record_opportunity_alert(
            identity=f"EARLY_MOVER:C{index}USD",
            transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
            message_id=1300 + index,
            created_new=True,
            now=now + timedelta(minutes=index),
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:TWENTYFIFTHUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now + timedelta(minutes=30),
        state_file=state,
    )

    assert decision.action == "SUPPRESS"
    assert decision.reason == "NEW_CARD_EMERGENCY_CAP"


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
            transition_key="WATCH:WATCH_ONLY:STEADY:NOT_EXTENDED",
            message_id=3000 + index,
            created_new=True,
            now=now,
            state_file=state,
        )

    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:NEWUSD",
        transition_key="WATCH:WATCH_ONLY:STEADY:NOT_EXTENDED",
        now=now + timedelta(hours=24, seconds=1),
        state_file=state,
    )

    assert decision.action == "CREATE"


def test_concurrent_create_reservations_cannot_exceed_hard_cap(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    workers = 20
    barrier = threading.Barrier(workers)
    decisions = []
    guard = threading.Lock()

    def run(index):
        barrier.wait()
        decision = evaluate_opportunity_alert(
            identity=f"EARLY_MOVER:RACE{index}USD",
            transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
            now=now,
            max_new_cards_24h=2,
            hard_max_new_cards_24h=3,
            priority=True,
            state_file=state,
        )
        with guard:
            decisions.append(decision)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    created = [decision for decision in decisions if decision.action == "CREATE"]
    rejected = [decision for decision in decisions if decision.action != "CREATE"]
    assert len(created) == 3
    assert all(decision.reservation_token for decision in created)
    assert all(decision.action == "SUPPRESS" for decision in rejected)
    assert any(
        decision.reason == "NEW_CARD_EMERGENCY_CAP"
        for decision in rejected
    )


def test_failed_delivery_release_returns_reserved_capacity(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    first = evaluate_opportunity_alert(
        identity="EARLY_MOVER:FIRSTUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now,
        max_new_cards_24h=1,
        hard_max_new_cards_24h=1,
        priority=True,
        state_file=state,
    )
    blocked = evaluate_opportunity_alert(
        identity="EARLY_MOVER:BLOCKEDUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now,
        max_new_cards_24h=1,
        hard_max_new_cards_24h=1,
        priority=True,
        state_file=state,
    )
    assert first.action == "CREATE"
    assert blocked.reason == "NEW_CARD_EMERGENCY_CAP"

    release_opportunity_alert_reservation(
        first.reservation_token,
        state_file=state,
    )
    second = evaluate_opportunity_alert(
        identity="EARLY_MOVER:SECONDUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now,
        max_new_cards_24h=1,
        hard_max_new_cards_24h=1,
        priority=True,
        state_file=state,
    )
    assert second.action == "CREATE"


def test_commit_consumes_reservation_and_records_one_card(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:COMMITUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        now=now,
        state_file=state,
    )
    assert decision.reservation_token

    record_opportunity_alert(
        identity="EARLY_MOVER:COMMITUSD",
        transition_key="READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED",
        message_id=9001,
        created_new=True,
        reservation_token=decision.reservation_token,
        now=now,
        state_file=state,
    )

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["new_card_reservations"] == {}
    assert len(payload["new_card_history"]) == 1
    assert payload["identities"]["EARLY_MOVER:COMMITUSD"]["message_id"] == 9001


def test_expired_reservation_still_records_delivered_card(tmp_path):
    state = tmp_path / "governor.json"
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    transition = "READY:BREAKOUT_ENTRY_POSSIBLE:ACCELERATING:NOT_EXTENDED"
    decision = evaluate_opportunity_alert(
        identity="EARLY_MOVER:EXPIREDUSD",
        transition_key=transition,
        now=now,
        state_file=state,
    )
    assert decision.reservation_token

    # Delivery can finish after the five-minute reservation TTL. The Telegram
    # message exists, so persistence must not discard its canonical identity.
    record_opportunity_alert(
        identity="EARLY_MOVER:EXPIREDUSD",
        transition_key=transition,
        message_id=9002,
        created_new=True,
        reservation_token=decision.reservation_token,
        now=now + timedelta(minutes=6),
        state_file=state,
    )

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["identities"]["EARLY_MOVER:EXPIREDUSD"]["message_id"] == 9002
    assert len(payload["new_card_history"]) == 1
    follow_up = evaluate_opportunity_alert(
        identity="EARLY_MOVER:EXPIREDUSD",
        transition_key=transition,
        now=now + timedelta(minutes=7),
        state_file=state,
    )
    assert follow_up.action == "SUPPRESS"
    assert follow_up.reason == "SAME_STATE_COOLDOWN"
