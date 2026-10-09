"""R4 alert / notification quality gate acceptance tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import routes
from app.jobs import scan_opportunities
from app.services import (
    chief_alert_notifier,
    notification_policy,
    pending_setup_registry,
    qualified_alert_outbox,
    trade_outcome_registry,
)
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.pending_setup_registry import PendingSetup
from app.services.registry_io import load_json
from app.services.telegram_delivery import record_telegram_suppression


pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R4-alert-notification-quality-gate"
APP = Path(__file__).resolve().parents[1]


def _plan(**overrides) -> EntryExitPlan:
    payload = dict(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.5,
        entry_high=100.5,
        chase_limit=101.25,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.5,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="qualified structure\nsecond reason\nthird reason\nfourth reason\nfifth reason",
        direction="LONG",
    )
    payload.update(overrides)
    return EntryExitPlan(**payload)


def _rows():
    return [
        {"rank": 1, "quality_score": 90.0, "symbol": "AAA"},
        {"rank": 2, "quality_score": 80.0, "symbol": "BBB"},
        {"rank": 3, "quality_score": 70.0, "symbol": "CCC"},
        {"rank": 4, "quality_score": None, "symbol": "DDD"},
    ]


def test_score_only_path_cannot_send_new_trade():
    """ATDD-R4-alert-notification-quality-gate/AC-001: a local score cannot send NEW TRADE Telegram."""
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert "send_tracked_telegram" not in source
    assert "SCORE_ONLY_NOT_AUTHORITATIVE_SELECTION" in source
    scan = Path(scan_opportunities.__file__).read_text(encoding="utf-8")
    assert "select_new_trade_window" in scan
    assert "send_trade_plan" in scan


def test_new_trade_window_is_ranked_and_bounded():
    """ATDD-R4-alert-notification-quality-gate/AC-002: delivery follows authoritative order and a bound."""
    chosen, suppressed = notification_policy.select_new_trade_window(
        _rows(),
        max_per_window=2,
        min_quality_score=0,
    )
    assert [row["symbol"] for row in chosen] == ["AAA", "BBB", "CCC"]
    assert [row["rank"] for row in chosen] == [1, 2, 3]
    assert [row["symbol"] for row in suppressed] == ["DDD"]
    assert "NOTIFICATION_QUALITY_UNAVAILABLE" in suppressed[0]["notification_suppression_reason"]
    assert notification_policy.new_trade_delivery_slot_open(1, 2)
    assert not notification_policy.new_trade_delivery_slot_open(2, 2)
    emergency = Path(APP / "app" / "services" / "emergency_alert_notifier.py").read_text(encoding="utf-8")
    assert "select_new_trade_window" not in emergency


def test_notification_threshold_does_not_change_rank_or_score():
    """ATDD-R4-alert-notification-quality-gate/AC-003: the threshold changes notification eligibility only."""
    original = _rows()
    snapshot = [dict(row) for row in original]
    chosen, suppressed = notification_policy.select_new_trade_window(
        original,
        max_per_window=3,
        min_quality_score=85,
    )
    assert original == snapshot
    assert [row["symbol"] for row in chosen] == ["AAA"]
    assert any("NOTIFICATION_QUALITY_THRESHOLD" in row["notification_suppression_reason"] for row in suppressed)
    assert "min_alert_score" not in Path(notification_policy.__file__).read_text(encoding="utf-8")


def test_fingerprint_dedupe_cooldown_and_material_change(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-004: identity, dedupe, cooldown and material change."""
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    first = notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        now=now,
    )
    second = notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        now=now,
    )
    assert first
    assert second is None
    assert notification_policy.confirm_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        reservation_token=first,
        now=now,
    )
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:A",
        now=now + timedelta(hours=7),
    ) is None
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="r4-geometry-v1:B",
        now=now + timedelta(minutes=1),
    )
    plan = _plan()
    quiet = {"direction": "LONG", "observed_at": "2026-10-09T00:00:00Z", "profit_rank": 1}
    noisy = {"direction": "LONG", "observed_at": "2026-10-09T00:00:01Z", "profit_rank": 9}
    assert chief_alert_notifier._alert_state_key(quiet, plan) == chief_alert_notifier._alert_state_key(noisy, plan)
    moved = _plan(stop_price=90.0)
    assert chief_alert_notifier._alert_state_key(quiet, plan) != chief_alert_notifier._alert_state_key(quiet, moved)


def test_tracking_failure_does_not_terminalize_trade():
    """ATDD-R4-alert-notification-quality-gate/AC-005: tracking failure stays retryable."""
    source = Path(chief_alert_notifier.__file__).read_text(encoding="utf-8")
    assert "terminalize_pending_setup" not in source
    assert "TRACKING_FAILURE_RETRYABLE" in source
    assert "release_emit" in source


def test_primary_alert_is_decision_first_without_fake_confidence():
    """ATDD-R4-alert-notification-quality-gate/AC-006: primary body is decision-first and evidence-backed."""
    message = chief_alert_notifier.format_primary_new_trade_alert(
        {
            "direction": "LONG",
            "underlying_asset": "SOL",
            "primary_pair": "SOLUSD",
            "reference_price": 100.25,
            "confidence": 84,
            "reason": "whale activity spotted\nreal structure",
            "trade_id": "T-1",
            "journey_id": "J-1",
            "opportunity_rank": 1,
        },
        _plan(),
    )
    assert message.splitlines()[0].startswith("ENTER NOW — ")
    assert "SOL" in message
    assert "SOLUSD" in message
    assert "Reference price: 100.25" in message
    assert "Entry zone: 99.5 - 100.5" in message
    assert "Target: 120.5" in message
    assert "Invalidation: 95" in message
    assert message.count("\n- ") <= 4
    assert "whale" not in message.lower()
    assert "Confidence" not in message
    assert "%" not in message.split("Trace:")[0]
    again = chief_alert_notifier.format_primary_new_trade_alert(
        {
            "direction": "LONG",
            "underlying_asset": "SOL",
            "primary_pair": "SOLUSD",
            "reference_price": 100.25,
            "confidence": 84,
            "reason": "whale activity spotted\nreal structure",
            "trade_id": "T-1",
            "journey_id": "J-1",
            "opportunity_rank": 1,
        },
        _plan(),
    )
    assert message == again


def test_notification_policy_does_not_rebuild_qualification():
    """ATDD-R4-alert-notification-quality-gate/AC-007: paper still sees the same selected rows."""
    rows = _rows()
    chosen, suppressed = notification_policy.select_new_trade_window(
        rows,
        max_per_window=1,
        min_quality_score=0,
    )
    assert len(rows) == 4
    assert len(chosen) + len(suppressed) == 4
    scan = Path(scan_opportunities.__file__).read_text(encoding="utf-8")
    assert "_route_paper_v2_opportunities(\n                ranked_opportunities," in scan
    assert "_publish_freqtrade_paper_opportunities(\n            ranked_opportunities," in scan
    assert "chosen_notification_ids" not in scan
    assert "authorize_new_trade_notification" in scan


def test_suppressed_candidate_remains_auditable(tmp_path):
    """ATDD-R4-alert-notification-quality-gate/AC-008: suppression is recorded and the row remains."""
    rows = _rows()
    _chosen, suppressed = notification_policy.select_new_trade_window(
        rows,
        max_per_window=1,
        min_quality_score=85,
    )
    assert any(row["symbol"] == "BBB" for row in suppressed)
    event_file = tmp_path / "events.jsonl"
    state_file = tmp_path / "state.json"
    record_telegram_suppression(
        identity="QUALIFIED_OPPORTUNITY:BBB",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="NEW_TRADE",
        fingerprint="r4-geometry-v1:BBB",
        reason=suppressed[0]["notification_suppression_reason"],
        symbol="BBB",
        trade_id="BBB",
        state_file=state_file,
        event_file=event_file,
    )
    text = event_file.read_text(encoding="utf-8")
    assert "SUPPRESSED" in text
    assert "r4-alert-quality-v1" in text
    assert len(rows) == 4


def test_telegram_callback_has_no_exchange_order_authority():
    """ATDD-R4-alert-notification-quality-gate/AC-009: Telegram cannot place an exchange order."""
    source = (APP / "app" / "services" / "telegram_callback_listener.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "addorder" not in lowered
    assert "place_order" not in lowered
    assert "Kraken execution is NOT enabled." in source


def test_protection_alerts_are_outside_the_new_trade_budget(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-010: protection alerts ignore the NEW TRADE bound."""
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    monkeypatch.setattr(notification_policy, "allow_new_noncritical", lambda **kwargs: False)
    assert "STOP" in notification_policy.CRITICAL_EVENTS
    assert notification_policy.should_emit(
        identity="ACTIVE_TRADE:STOP",
        event_type="STOP",
        fingerprint="stop:1",
    )
    assert "select_new_trade_window" not in Path(
        APP / "app" / "services" / "trade_monitor_notifier.py"
    ).read_text(encoding="utf-8")


def _isolate_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_setup_registry, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(trade_outcome_registry, "OUTCOME_FILE", tmp_path / "outcomes.json")
    monkeypatch.setattr(qualified_alert_outbox, "OUTBOX_FILE", tmp_path / "outbox.json")
    monkeypatch.setattr(chief_alert_notifier, "STATE_FILE", tmp_path / "alert_state.json")
    monkeypatch.setattr(chief_alert_notifier, "STATE_LOCK_FILE", tmp_path / ".alert_state.lock")
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    tracked = []
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: tracked.append(kwargs["trade_id"]),
    )
    return tracked


def _candidate(symbol: str, score: float) -> dict:
    return {
        "symbol": symbol,
        "confidence": 88,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "profit_rank_score": score,
        "direction": "LONG",
        "underlying_asset": symbol,
        "primary_pair": symbol,
        "reference_price": 100.0,
    }


def _send_ranked(candidates, *, cap: float, floor: float, monkeypatch):
    sent_messages = []

    def _send(**kwargs):
        sent_messages.append(kwargs["message"])
        return SimpleNamespace(delivered=True, message_id="m-1")

    monkeypatch.setattr(chief_alert_notifier, "send_tracked_telegram", _send)
    attempts = 0
    for candidate in candidates:
        quality_reason = notification_policy.notification_quality_suppression_reason(
            candidate["profit_rank_score"],
            min_quality_score=floor,
        )
        notify, _reason = notification_policy.authorize_new_trade_notification(
            quality_reason=quality_reason,
            delivery_attempts=attempts,
            max_per_window=int(cap),
        )
        chief_alert_notifier.send_trade_plan(
            candidate,
            _plan(symbol=candidate["symbol"]),
            "summary",
            "token",
            "chat",
            notify=notify,
        )
        if candidate.get("notification_attempted"):
            attempts += 1
    return sent_messages, attempts


def test_threshold_suppression_keeps_lifecycle(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-007: a quality-floor skip still records the trade."""
    tracked = _isolate_lifecycle(tmp_path, monkeypatch)
    candidate = _candidate("LOW", 10)
    sent, attempts = _send_ranked([candidate], cap=3, floor=80, monkeypatch=monkeypatch)
    waiting = pending_setup_registry.get_pending_setups()
    assert len(waiting) == 1
    assert waiting[0].symbol == "LOW"
    outcomes = load_json(trade_outcome_registry.OUTCOME_FILE)
    assert any(row.get("trade_id") == waiting[0].trade_id for row in outcomes.values())
    assert tracked == [waiting[0].trade_id]
    assert sent == []
    assert attempts == 0


def test_volume_cap_suppression_keeps_lifecycle(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-007: a window-cap skip still records the trade."""
    tracked = _isolate_lifecycle(tmp_path, monkeypatch)
    candidates = [_candidate("AAA", 90), _candidate("BBB", 80)]
    sent, attempts = _send_ranked(candidates, cap=1, floor=0, monkeypatch=monkeypatch)
    waiting = pending_setup_registry.get_pending_setups()
    assert {setup.symbol for setup in waiting} == {"AAA", "BBB"}
    outcomes = load_json(trade_outcome_registry.OUTCOME_FILE)
    recorded = {row.get("trade_id") for row in outcomes.values()}
    assert recorded == {setup.trade_id for setup in waiting}
    assert set(tracked) == recorded
    assert len(sent) == 1
    assert attempts == 1


def test_duplicate_ranks_do_not_consume_new_trade_slots(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: cooldown duplicates leave the slot for the next rank."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    fresh = {"FRESH"}

    def _should_send(candidate, plan):
        return plan.symbol in fresh

    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", _should_send)
    candidates = [
        _candidate("OLD1", 99),
        _candidate("OLD2", 98),
        _candidate("OLD3", 97),
        _candidate("FRESH", 70),
    ]
    sent, attempts = _send_ranked(candidates, cap=1, floor=0, monkeypatch=monkeypatch)
    assert attempts == 1
    assert len(sent) == 1
    assert "trade_id=" in sent[0]
    fresh_setup = next(
        setup for setup in pending_setup_registry.get_pending_setups() if setup.symbol == "FRESH"
    )
    assert f"trade_id={fresh_setup.trade_id}" in sent[0]
    assert {setup.symbol for setup in pending_setup_registry.get_pending_setups()} == {
        "OLD1",
        "OLD2",
        "OLD3",
        "FRESH",
    }


def test_tracking_failure_queue_counts_toward_the_cap(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: a queued tracking retry spends one NEW TRADE slot."""
    calls = {"n": 0}

    def _tracking(**kwargs):
        calls["n"] += 1
        if kwargs["candidate"]["symbol"] == "AAA":
            raise RuntimeError("tracking unavailable")

    monkeypatch.setattr(chief_alert_notifier, "_register_reconciliation_intent", _tracking)
    _isolate_lifecycle(tmp_path, monkeypatch)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        _tracking,
    )
    sent, attempts = _send_ranked(
        [_candidate("AAA", 90), _candidate("BBB", 80)],
        cap=1,
        floor=0,
        monkeypatch=monkeypatch,
    )
    with qualified_alert_outbox.registry_lock(qualified_alert_outbox._lock_file()):
        rows = load_json(qualified_alert_outbox.OUTBOX_FILE)
    assert attempts == 1
    assert sent == []
    assert len(rows) == 1
    assert {setup.symbol for setup in pending_setup_registry.get_pending_setups()} == {"AAA", "BBB"}

    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: False)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("tracking unavailable")),
    )
    duplicate = _candidate("OLD", 99)
    chief_alert_notifier.send_trade_plan(
        duplicate,
        _plan(symbol="OLD"),
        "summary",
        "token",
        "chat",
    )
    with qualified_alert_outbox.registry_lock(qualified_alert_outbox._lock_file()):
        rows = load_json(qualified_alert_outbox.OUTBOX_FILE)
    assert len(rows) == 1
    assert duplicate.get("notification_attempted") is False


def test_new_trade_delivery_never_exceeds_configured_cap(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: only real delivery attempts spend the cap."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    candidates = [_candidate(symbol, 90 - index) for index, symbol in enumerate(["A", "B", "C", "D", "E"])]
    sent, attempts = _send_ranked(candidates, cap=2, floor=0, monkeypatch=monkeypatch)
    assert attempts == 2
    assert len(sent) == 2
    assert len(pending_setup_registry.get_pending_setups()) == 5


def test_delivered_primary_message_contains_generated_trade_id(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-006: the sent body uses the assigned trade id."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    candidate = _candidate("SOL", 90)
    sent, _attempts = _send_ranked([candidate], cap=1, floor=0, monkeypatch=monkeypatch)
    trade_id = pending_setup_registry.get_pending_setups()[0].trade_id
    assert sent[0].count(f"trade_id={trade_id}") == 1
    assert candidate["trade_id"] == trade_id


def test_queued_outbox_message_contains_same_trade_id(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-006: the retry body uses that same trade id."""
    _isolate_lifecycle(tmp_path, monkeypatch)

    def _fail(**kwargs):
        return SimpleNamespace(delivered=False, message_id=None)

    monkeypatch.setattr(chief_alert_notifier, "send_tracked_telegram", _fail)
    candidate = _candidate("SOL", 90)
    chief_alert_notifier.send_trade_plan(
        candidate,
        _plan(symbol="SOL"),
        "summary",
        "token",
        "chat",
    )
    trade_id = candidate["trade_id"]
    with qualified_alert_outbox.registry_lock(qualified_alert_outbox._lock_file()):
        rows = load_json(qualified_alert_outbox.OUTBOX_FILE)
    assert trade_id in rows
    assert f"trade_id={trade_id}" in rows[trade_id]["message"]


def test_notification_settings_do_not_change_paper_routing_input():
    """ATDD-R4-alert-notification-quality-gate/AC-007: threshold and cap do not narrow paper input."""
    scan = Path(scan_opportunities.__file__).read_text(encoding="utf-8")
    paper_at = scan.index("_route_paper_v2_opportunities(\n                ranked_opportunities,")
    alert_at = scan.index("notify, reason = authorize_new_trade_notification(")
    assert alert_at < paper_at
    paper_block = scan[paper_at:]
    assert "notification_quality_min_score" not in paper_block
    assert "new_trade_alert_max_per_window" not in paper_block
    rows = _rows()
    low_cap, _low_suppressed = notification_policy.select_new_trade_window(
        rows,
        max_per_window=1,
        min_quality_score=0,
    )
    high_cap, _high_suppressed = notification_policy.select_new_trade_window(
        rows,
        max_per_window=20,
        min_quality_score=90,
    )
    assert [row["symbol"] for row in low_cap] == ["AAA", "BBB", "CCC"]
    assert [row["symbol"] for row in high_cap] == ["AAA"]
    assert rows[0]["rank"] == 1


def _outbox_rows():
    with qualified_alert_outbox.registry_lock(qualified_alert_outbox._lock_file()):
        return load_json(qualified_alert_outbox.OUTBOX_FILE)


def test_same_queued_fingerprint_does_not_consume_another_slot(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: a durable retry is not queued again."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("tracking unavailable")),
    )
    first = _candidate("AAA", 90)
    chief_alert_notifier.send_trade_plan(
        first,
        _plan(symbol="AAA"),
        "summary",
        "token",
        "chat",
    )
    assert first.get("notification_attempted") is True
    saved = _outbox_rows()
    assert len(saved) == 1
    trade_id = first["trade_id"]
    original = dict(saved[trade_id])

    again = _candidate("AAA", 90)
    again["trade_id"] = trade_id
    chief_alert_notifier.send_trade_plan(
        again,
        _plan(symbol="AAA"),
        "summary",
        "token",
        "chat",
    )
    assert again.get("notification_attempted") is False
    replayed = _outbox_rows()
    assert list(replayed) == [trade_id]
    assert replayed[trade_id] == original


def test_queued_unchanged_rank_leaves_the_slot_for_the_next_rank(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: a queued rank-1 leaves cap=1 for rank-2."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("tracking unavailable")),
    )
    queued = _candidate("OLD", 99)
    chief_alert_notifier.send_trade_plan(
        queued,
        _plan(symbol="OLD"),
        "summary",
        "token",
        "chat",
    )
    assert queued.get("notification_attempted") is True
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: None,
    )
    rank1 = _candidate("OLD", 99)
    rank1["trade_id"] = queued["trade_id"]
    rank2 = _candidate("NEW", 70)
    sent, attempts = _send_ranked(
        [rank1, rank2],
        cap=1,
        floor=0,
        monkeypatch=monkeypatch,
    )
    assert rank1.get("notification_attempted") is False
    assert attempts == 1
    assert len(sent) == 1
    fresh = next(
        setup for setup in pending_setup_registry.get_pending_setups() if setup.symbol == "NEW"
    )
    assert f"trade_id={fresh.trade_id}" in sent[0]
    assert queued["trade_id"] in _outbox_rows()


def _seed_retry_row(trade_id: str, symbol: str) -> None:
    pending_setup_registry.add_pending_setup(
        PendingSetup(
            symbol=symbol,
            entry_low=99.0,
            entry_high=100.0,
            chase_limit=101.0,
            stop_price=95.0,
            target_1=110.0,
            target_2=115.0,
            risk_level="low",
            confidence=88,
            trade_id=trade_id,
        )
    )
    qualified_alert_outbox.queue_qualified_alert(
        trade_id=trade_id,
        message=f"trade_id={trade_id}",
        candidate={"economic_qualified": False},
        plan=_plan(symbol=symbol),
        action="ENTER_NOW",
        direction="LONG",
        identity=f"QUALIFIED_OPPORTUNITY:{trade_id}",
        fingerprint=f"fp-{trade_id}",
        reason="DELIVERY_PENDING",
    )


def _patch_retry_delivery(monkeypatch):
    delivered = []

    def _send(**kwargs):
        delivered.append(kwargs["trade_id"])
        return SimpleNamespace(delivered=True, message_id=len(delivered))

    monkeypatch.setattr(qualified_alert_outbox, "accepted_delivery_message_id", lambda **kwargs: None)
    monkeypatch.setattr(qualified_alert_outbox, "reserve_emit", lambda **kwargs: "reserve")
    monkeypatch.setattr(qualified_alert_outbox, "confirm_emit", lambda **kwargs: True)
    monkeypatch.setattr(qualified_alert_outbox, "send_tracked_telegram", _send)
    return delivered


def test_recovery_delivers_no_more_than_the_configured_bound(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: one recovery run is bounded."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    for trade_id, symbol in (("Q-1", "AAA"), ("Q-2", "BBB"), ("Q-3", "CCC"), ("Q-4", "DDD")):
        _seed_retry_row(trade_id, symbol)
    delivered_ids = _patch_retry_delivery(monkeypatch)
    delivered, pending = qualified_alert_outbox.retry_qualified_alerts(
        bot_token="token",
        chat_id="chat",
        max_new_trade_retries=2,
    )
    assert delivered == 2
    assert delivered_ids == ["Q-1", "Q-2"]
    assert pending == 2
    remaining = _outbox_rows()
    assert list(remaining) == ["Q-3", "Q-4"]
    assert remaining["Q-3"]["reason"] == "DELIVERY_PENDING"
    assert remaining["Q-4"]["reason"] == "DELIVERY_PENDING"


def test_recovery_bound_leaves_later_rows_pending_for_the_next_run(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: unattempted retries stay eligible."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    for trade_id, symbol in (("Q-1", "AAA"), ("Q-2", "BBB"), ("Q-3", "CCC")):
        _seed_retry_row(trade_id, symbol)
    delivered_ids = _patch_retry_delivery(monkeypatch)
    first_delivered, first_pending = qualified_alert_outbox.retry_qualified_alerts(
        bot_token="token",
        chat_id="chat",
        max_new_trade_retries=1,
    )
    assert first_delivered == 1
    assert first_pending == 2
    assert list(_outbox_rows()) == ["Q-2", "Q-3"]
    second_delivered, second_pending = qualified_alert_outbox.retry_qualified_alerts(
        bot_token="token",
        chat_id="chat",
        max_new_trade_retries=1,
    )
    assert second_delivered == 1
    assert second_pending == 1
    assert delivered_ids == ["Q-1", "Q-2"]
    assert list(_outbox_rows()) == ["Q-3"]
    assert pending_setup_registry.get_pending_setup_by_trade_id("Q-3") is not None


def test_stuck_tracking_row_does_not_block_a_later_recovery(tmp_path, monkeypatch):
    """ATDD-R4-alert-notification-quality-gate/AC-002: a no-send retry does not spend the bound."""
    _isolate_lifecycle(tmp_path, monkeypatch)
    _seed_retry_row("Q-1", "AAA")
    qualified_alert_outbox.queue_qualified_alert(
        trade_id="Q-1",
        message="trade_id=Q-1",
        candidate={"economic_qualified": True},
        plan=_plan(symbol="AAA"),
        action="ENTER_NOW",
        direction="LONG",
        identity="QUALIFIED_OPPORTUNITY:Q-1",
        fingerprint="fp-Q-1",
        reason="TRACKING_PENDING:RuntimeError",
    )
    _seed_retry_row("Q-2", "BBB")
    _seed_retry_row("Q-3", "CCC")

    def _tracking(**kwargs):
        if kwargs["trade_id"] == "Q-1":
            raise RuntimeError("tracking stuck")

    monkeypatch.setattr(qualified_alert_outbox, "register_reconciliation_intent", _tracking)
    delivered_ids = _patch_retry_delivery(monkeypatch)
    delivered, pending = qualified_alert_outbox.retry_qualified_alerts(
        bot_token="token",
        chat_id="chat",
        max_new_trade_retries=1,
    )
    assert delivered == 1
    assert delivered_ids == ["Q-2"]
    assert "Q-1" in _outbox_rows()
    assert "Q-3" in _outbox_rows()
    assert "Q-2" not in _outbox_rows()
    assert pending == 2
