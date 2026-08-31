from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.jobs import run_cycle, scan_opportunities
from app.services import (
    chief_alert_notifier,
    notification_policy,
    pending_setup_notifier,
    pending_setup_registry,
    qualified_alert_outbox,
)
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.pending_setup_registry import PendingSetup
from app.services.trade_action_gate import ActionGateDecision
from app.services.qualified_trade_tracking import (
    ReconciliationIdentityMismatch,
    ReconciliationTrackingDisabled,
)


def plan(symbol="SOLUSD"):
    return EntryExitPlan(
        symbol=symbol,
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="qualified",
    )


def _patch_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pending_setup_registry,
        "PENDING_FILE",
        tmp_path / "pending.json",
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "OUTBOX_FILE",
        tmp_path / "qualified_outbox.json",
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "STATE_FILE",
        tmp_path / "alert_state.json",
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "STATE_LOCK_FILE",
        tmp_path / ".alert_state.lock",
    )


def test_atomic_notification_reservation_allows_one_concurrent_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    monkeypatch.setattr(notification_policy, "allow_new_noncritical", lambda **kwargs: True)
    monkeypatch.setattr(notification_policy, "record_new_noncritical", lambda **kwargs: None)

    def reserve(_):
        return notification_policy.reserve_emit(
            identity="LONG:SOLUSD",
            event_type="OPPORTUNITY",
            fingerprint="same",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = list(executor.map(reserve, range(2)))

    assert sum(token is not None for token in tokens) == 1


def test_release_reservation_allows_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    monkeypatch.setattr(notification_policy, "allow_new_noncritical", lambda **kwargs: True)

    token = notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="OPPORTUNITY",
        fingerprint="same",
    )
    assert token
    assert notification_policy.release_emit(
        identity="LONG:SOLUSD",
        event_type="OPPORTUNITY",
        reservation_token=token,
    )
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="OPPORTUNITY",
        fingerprint="same",
    )


def test_tracking_failure_keeps_qualified_setup_live_and_queues_retry(tmp_path, monkeypatch):
    _patch_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(chief_alert_notifier, "record_recommendation", lambda **kwargs: None)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("tracking unavailable")),
    )

    candidate = {
        "confidence": 88,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 200.0,
    }

    assert not chief_alert_notifier.send_trade_plan(
        candidate,
        plan(),
        "summary",
        "token",
        "chat",
    )

    waiting = pending_setup_registry.get_pending_setups()
    assert len(waiting) == 1
    assert waiting[0].status == "waiting"
    with qualified_alert_outbox.registry_lock(qualified_alert_outbox._lock_file()):
        rows = qualified_alert_outbox.load_json(qualified_alert_outbox.OUTBOX_FILE)
    assert waiting[0].trade_id in rows
    assert rows[waiting[0].trade_id]["reason"].startswith("TRACKING_PENDING:")


def test_reconciliation_identity_mismatch_is_terminal_not_retryable(
    tmp_path,
    monkeypatch,
):
    _patch_pending(tmp_path, monkeypatch)
    queued = []
    terminalized = []
    suppressions = []
    monkeypatch.setattr(
        chief_alert_notifier,
        "should_send_trade_plan",
        lambda *args: True,
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "record_recommendation",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(
            ReconciliationIdentityMismatch("conflict")
        ),
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "terminalize_pending_setup",
        lambda trade_id, status: terminalized.append(
            (trade_id, status)
        ) or True,
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "get_pending_setup_record",
        lambda trade_id: {"status": "tracking_failed"},
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "queue_qualified_alert",
        lambda **kwargs: queued.append(kwargs),
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "record_telegram_suppression",
        lambda **kwargs: suppressions.append(kwargs),
    )

    candidate = {
        "confidence": 88,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 200.0,
    }

    assert not chief_alert_notifier.send_trade_plan(
        candidate,
        plan(),
        "summary",
        "token",
        "chat",
    )
    assert terminalized == [(candidate["trade_id"], "tracking_failed")]
    assert queued == []
    assert suppressions[-1]["reason"] == "TRACKING_IDENTITY_MISMATCH_TERMINAL"


def test_telegram_failure_keeps_qualified_setup_live_and_queues_retry(tmp_path, monkeypatch):
    _patch_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(chief_alert_notifier, "record_recommendation", lambda **kwargs: None)
    monkeypatch.setattr(chief_alert_notifier, "_register_reconciliation_intent", lambda **kwargs: None)
    monkeypatch.setattr(chief_alert_notifier, "reserve_emit", lambda **kwargs: "lease")
    monkeypatch.setattr(chief_alert_notifier, "release_emit", lambda **kwargs: True)
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=False, message_id=None),
    )

    candidate = {
        "confidence": 88,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 200.0,
    }

    assert not chief_alert_notifier.send_trade_plan(
        candidate,
        plan(),
        "summary",
        "token",
        "chat",
    )
    waiting = pending_setup_registry.get_pending_setups()
    assert len(waiting) == 1
    with qualified_alert_outbox.registry_lock(qualified_alert_outbox._lock_file()):
        rows = qualified_alert_outbox.load_json(qualified_alert_outbox.OUTBOX_FILE)
    assert rows[waiting[0].trade_id]["reason"] == "DELIVERY_PENDING"


def test_qualified_outbox_retry_delivers_without_rescan(tmp_path, monkeypatch):
    _patch_pending(tmp_path, monkeypatch)
    setup = pending_setup_registry.add_pending_setup(
        PendingSetup(
            symbol="SOLUSD",
            entry_low=99.0,
            entry_high=100.0,
            chase_limit=101.0,
            stop_price=95.0,
            target_1=110.0,
            target_2=115.0,
            risk_level="low",
            confidence=88,
            trade_id="Q-1",
        )
    )
    qualified_alert_outbox.queue_qualified_alert(
        trade_id=setup.trade_id,
        message="trade",
        candidate={"economic_qualified": False},
        plan=plan(),
        action="ENTER_NOW",
        direction="LONG",
        identity=f"QUALIFIED_OPPORTUNITY:{setup.trade_id}",
        fingerprint="fp",
        reason="DELIVERY_PENDING",
    )
    monkeypatch.setattr(qualified_alert_outbox, "accepted_delivery_message_id", lambda **kwargs: None)
    monkeypatch.setattr(qualified_alert_outbox, "reserve_emit", lambda **kwargs: "reserve")
    monkeypatch.setattr(qualified_alert_outbox, "confirm_emit", lambda **kwargs: True)
    monkeypatch.setattr(
        qualified_alert_outbox,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=10),
    )

    delivered, pending = qualified_alert_outbox.retry_qualified_alerts(
        bot_token="token",
        chat_id="chat",
    )

    assert delivered == 1
    assert pending == 0
    with qualified_alert_outbox.registry_lock(qualified_alert_outbox._lock_file()):
        assert qualified_alert_outbox.load_json(qualified_alert_outbox.OUTBOX_FILE) == {}


def test_rank_one_veto_does_not_block_rank_two(monkeypatch):
    first = SimpleNamespace(
        rank=1,
        opportunity=SimpleNamespace(
            alert={},
            plan=plan("AAAUSD"),
            snapshot=SimpleNamespace(trade_direction="LONG"),
        ),
        profit_ranking=SimpleNamespace(total_score=95.0),
    )
    second = SimpleNamespace(
        rank=2,
        opportunity=SimpleNamespace(
            alert={},
            plan=plan("BBBUSD"),
            snapshot=SimpleNamespace(trade_direction="LONG"),
        ),
        profit_ranking=SimpleNamespace(total_score=90.0),
    )
    monkeypatch.setattr(scan_opportunities, "get_active_trades", lambda: [])
    entry_watch_cleanup_calls = []
    # Finding 5: this test must never touch the real/default entry-watch
    # registry file. Isolate remove_entry_watch entirely (no tmp_path
    # indirection needed since the call itself must not reach shared state)
    # while still proving the gate-approved path invokes cleanup.
    monkeypatch.setattr(
        scan_opportunities,
        "remove_entry_watch",
        lambda *args: entry_watch_cleanup_calls.append(args),
    )

    def gate(*, candidate, plan, account_capital, active_trades):
        if plan.symbol == "AAAUSD":
            candidate["action_gate_evaluated"] = True
            candidate["action_gate_allowed"] = False
            return ActionGateDecision(False, "veto")
        candidate["action_gate_evaluated"] = True
        candidate["action_gate_allowed"] = True
        candidate["recommended_capital"] = 100.0
        return ActionGateDecision(True, "pass")

    monkeypatch.setattr(scan_opportunities, "apply_action_gate", gate)
    eligible = scan_opportunities._apply_ranked_action_gates(
        [first, second],
        settings=SimpleNamespace(account_equity=1000.0),
    )

    assert [item.rank for item in eligible] == [2]
    assert second.opportunity.alert["profit_rank"] == 2
    assert entry_watch_cleanup_calls == [("BBBUSD", "LONG")]


def test_notifier_consumes_pre_evaluated_action_gate(tmp_path, monkeypatch):
    _patch_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(chief_alert_notifier, "record_recommendation", lambda **kwargs: None)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(chief_alert_notifier, "reserve_emit", lambda **kwargs: "reserve")
    monkeypatch.setattr(chief_alert_notifier, "confirm_emit", lambda **kwargs: True)
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=11),
    )
    candidate = {
        "confidence": 80,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 200.0,
    }

    assert chief_alert_notifier.send_trade_plan(
        candidate,
        plan(),
        "summary",
        "token",
        "chat",
    )


def test_notifier_rejects_missing_action_gate_decision(tmp_path, monkeypatch):
    _patch_pending(tmp_path, monkeypatch)
    audits = []
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(
        chief_alert_notifier,
        "record_telegram_not_eligible",
        lambda **kwargs: audits.append(kwargs),
    )
    candidate = {
        "confidence": 80,
        "decision": "alert",
        "economic_qualified": True,
    }

    assert not chief_alert_notifier.send_trade_plan(
        candidate,
        plan(),
        "summary",
        "token",
        "chat",
    )
    assert audits[-1]["reason"] == "ACTION_GATE_NOT_EVALUATED"


def test_alerts_do_not_render_uncalibrated_confidence_percent():
    message = chief_alert_notifier.format_trade_plan(
        {"confidence": 84, "decision": "alert"},
        plan(),
        "summary",
    )
    assert "AI Confidence" not in message
    assert "Setup Score: 84/100 (not probability)" in message

    setup = PendingSetup(
        symbol="SOLUSD",
        entry_low=99,
        entry_high=100,
        chase_limit=101,
        stop_price=95,
        target_1=110,
        target_2=115,
        risk_level="low",
        confidence=84,
        trade_id="Q-2",
    )
    pending_message = pending_setup_notifier.format_pending_setup_message(
        setup,
        SimpleNamespace(
            status="ENTRY_ZONE_REACHED",
            current_price=100.0,
            reason="entry reached",
        ),
    )
    assert "Confidence*" not in pending_message
    assert "Setup Score: 84/100 (not probability)" in pending_message



def test_qualified_retry_is_after_active_protection(monkeypatch):
    calls = []
    reconciliation = SimpleNamespace(
        status="OK",
        mode="observe",
        active_checked=0,
        order_intents_checked=0,
        open_orders_seen=0,
        fills_seen=0,
        would_close=(),
        closed=(),
        would_fill=(),
        filled=(),
        reason="",
    )
    external_review = SimpleNamespace(
        status="OK",
        unmatched_orders_seen=0,
        new_reviews=0,
        notifications_sent=0,
        reason="",
    )
    operator = SimpleNamespace(
        override_mode="AUTO",
        effective_mode="MONITOR",
        occupied_slots=0,
        active_trades=0,
        live_order_intents=0,
        pending_setups=0,
        quiet_hours=False,
        reason="",
    )
    monkeypatch.setattr(run_cycle, "reconcile_kraken_account", lambda: reconciliation)
    monkeypatch.setattr(run_cycle, "review_external_open_orders", lambda: external_review)
    monkeypatch.setattr(
        run_cycle,
        "run_learning_cycle",
        lambda: {
            "status": "OK",
            "paid_ai_calls": 0,
            "shadow": {},
            "price_movement": {},
            "profile_refreshed": False,
            "profile_status": "OK",
        },
    )
    monkeypatch.setattr(run_cycle, "get_operator_decision", lambda: operator)
    monkeypatch.setattr(run_cycle, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(run_cycle, "monitor_active_main", lambda: calls.append("active"))
    monkeypatch.setattr(
        run_cycle,
        "_run_qualified_alert_retry_fail_open",
        lambda **kwargs: calls.append("retry"),
    )
    monkeypatch.setattr(run_cycle, "_run_entry_watch_recheck_fail_open", lambda: False)
    monkeypatch.setattr(run_cycle, "monitor_pending_main", lambda: calls.append("pending"))
    monkeypatch.setattr(run_cycle, "_run_early_watch_if_due", lambda **kwargs: None)
    monkeypatch.setattr(run_cycle, "_run_paper_monitor_fail_open", lambda: None)
    monkeypatch.setattr(run_cycle, "_run_event_intelligence_fail_open", lambda **kwargs: None)

    run_cycle._run_cycle_once()

    assert calls[:3] == ["active", "retry", "pending"]


# --- Finding 1: notification reservation must never remain stranded ---------


def _reservation_test_setup(tmp_path, monkeypatch):
    """Wire send_trade_plan up to a real, tmp-isolated notification_policy.

    economic_qualified is False so the flow reaches the atomic reservation
    directly, and only the network-facing pieces are mocked.
    """
    _patch_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(notification_policy, "STATE_FILE", tmp_path / "notification.json")
    monkeypatch.setattr(notification_policy, "LOCK_FILE", tmp_path / ".notification.lock")
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(chief_alert_notifier, "record_recommendation", lambda **kwargs: None)
    monkeypatch.setattr(chief_alert_notifier, "accepted_delivery_message_id", lambda **kwargs: None)


def _reservation_candidate():
    return {
        "confidence": 88,
        "decision": "alert",
        "economic_qualified": False,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 200.0,
    }


def test_reservation_confirmed_and_no_double_send_on_successful_delivery(tmp_path, monkeypatch):
    _reservation_test_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=1),
    )

    assert chief_alert_notifier.send_trade_plan(
        _reservation_candidate(), plan(), "summary", "token", "chat"
    )

    # Confirmed emission means a same-fingerprint retry must not resend.
    assert notification_policy.is_confirmed_emission(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint=chief_alert_notifier._alert_state_key(_reservation_candidate(), plan()),
    )
    assert (
        notification_policy.reserve_emit(
            identity="LONG:SOLUSD",
            event_type="ACTIONABLE_TRADE",
            fingerprint=chief_alert_notifier._alert_state_key(_reservation_candidate(), plan()),
        )
        is None
    )


def test_reservation_released_on_suppressed_delivery(tmp_path, monkeypatch):
    _reservation_test_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=False, message_id=None),
    )

    assert not chief_alert_notifier.send_trade_plan(
        _reservation_candidate(), plan(), "summary", "token", "chat"
    )

    # A suppressed (not delivered, no exception) send must release immediately
    # -- a fresh reservation attempt must not wait for the lease TTL.
    fingerprint = chief_alert_notifier._alert_state_key(_reservation_candidate(), plan())
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint=fingerprint,
    )


def test_reservation_released_when_delivery_raises(tmp_path, monkeypatch):
    _reservation_test_setup(tmp_path, monkeypatch)

    def _raise(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(chief_alert_notifier, "send_tracked_telegram", _raise)

    assert not chief_alert_notifier.send_trade_plan(
        _reservation_candidate(), plan(), "summary", "token", "chat"
    )

    # A provider exception must not strand the reservation for the 120s
    # lease TTL -- retry must be immediately possible.
    fingerprint = chief_alert_notifier._alert_state_key(_reservation_candidate(), plan())
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint=fingerprint,
    )


def test_reservation_released_on_unexpected_exception_in_downstream_audit(tmp_path, monkeypatch):
    _reservation_test_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        chief_alert_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=False, message_id=None),
    )

    def _raise_queue(**kwargs):
        raise MemoryError("unexpected runtime failure")

    monkeypatch.setattr(chief_alert_notifier, "queue_qualified_alert", _raise_queue)

    candidate = _reservation_candidate()
    candidate["trade_id"] = "Q-UNEXPECTED"
    # An unrelated, unexpected exception raised deep in the audit/queueing
    # path must not propagate out of send_trade_plan and must not prevent
    # the reservation from having already been released.
    assert not chief_alert_notifier.send_trade_plan(
        candidate, plan(), "summary", "token", "chat"
    )

    fingerprint = chief_alert_notifier._alert_state_key(candidate, plan())
    assert notification_policy.reserve_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint=fingerprint,
    )


def test_settle_reservation_never_raises_when_policy_calls_fail(monkeypatch):
    monkeypatch.setattr(
        chief_alert_notifier,
        "confirm_emit",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "release_emit",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )

    # Settlement must never raise, whether confirming a delivered message or
    # releasing a non-delivered one -- an unexpected exception here must not
    # crash the caller or leave the reservation lifecycle undefined.
    chief_alert_notifier._settle_reservation(
        policy_identity="LONG:SOLUSD",
        fingerprint="fp",
        reservation_token="token",
        delivered=True,
    )
    chief_alert_notifier._settle_reservation(
        policy_identity="LONG:SOLUSD",
        fingerprint="fp",
        reservation_token="token",
        delivered=False,
    )


def test_send_trade_plan_retains_recovery_record_when_terminalization_unconfirmed(
    tmp_path, monkeypatch
):
    """Finding 3 (notifier side): never destroy the recovery path before the
    intended terminal lifecycle transition is confirmed successful."""
    _patch_pending(tmp_path, monkeypatch)
    queued = []
    suppressions = []
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(chief_alert_notifier, "record_recommendation", lambda **kwargs: None)
    monkeypatch.setattr(
        chief_alert_notifier,
        "_register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(ReconciliationTrackingDisabled("disabled")),
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "terminalize_pending_setup",
        lambda trade_id, status: False,
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "queue_qualified_alert",
        lambda **kwargs: queued.append(kwargs),
    )
    monkeypatch.setattr(
        chief_alert_notifier,
        "record_telegram_suppression",
        lambda **kwargs: suppressions.append(kwargs),
    )

    candidate = {
        "confidence": 88,
        "decision": "alert",
        "economic_qualified": True,
        "action_gate_evaluated": True,
        "action_gate_allowed": True,
        "recommended_capital": 200.0,
    }

    assert not chief_alert_notifier.send_trade_plan(
        candidate, plan(), "summary", "token", "chat"
    )
    # Terminalization was never confirmed, so the durable recovery record
    # must be queued rather than silently dropped.
    assert len(queued) == 1
    assert suppressions[-1]["reason"] == "RECONCILIATION_NOT_APPLY_TERMINALIZATION_PENDING"