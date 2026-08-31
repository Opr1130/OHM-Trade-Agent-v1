from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.services import (
    entry_watch_queue,
    entry_watch_recheck,
    notification_policy,
    opportunity_monitor_queue,
    qualified_alert_outbox,
    trade_monitor_notifier,
)
from app.services.active_trade_registry import ActiveTrade
from app.services.kraken_exposure_resolver import KrakenExposureResolver
from app.services.opportunity_monitor_queue import CandidateObservation
from app.services.position_materiality import refine_protection_action
from app.services.qualified_trade_tracking import (
    ReconciliationIdentityMismatch,
    ReconciliationTrackingDisabled,
)
from app.services.trade_monitor import TradeMonitorResult


def _trade():
    return ActiveTrade(
        symbol="SOLUSD",
        entry_price=100.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=120.0,
        risk_level="medium",
        direction="LONG",
        trade_id="T-LONG",
        capital=100.0,
        margin_leverage=2.0,
    )


def test_monitor_queue_preserves_stronger_cross_source_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        opportunity_monitor_queue,
        "QUEUE_FILE",
        tmp_path / "queue.json",
    )
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    opportunity_monitor_queue.upsert_candidate(
        CandidateObservation(
            symbol="SOLUSD",
            direction="LONG",
            source="FULL_MARKET",
            observed_at=now,
            relative_strength_percentile=96.0,
            volume_acceleration_score=70.0,
            liquidity_usd=2_000_000.0,
            priority_score=92.0,
        )
    )
    opportunity_monitor_queue.upsert_candidate(
        CandidateObservation(
            symbol="SOLUSD",
            direction="LONG",
            source="EARLY_MOVER",
            observed_at=now,
            relative_strength_percentile=None,
            volume_acceleration_score=80.0,
            liquidity_usd=None,
            priority_score=75.0,
        )
    )
    row = opportunity_monitor_queue.read_candidates(now=now)[0]
    assert row["relative_strength_percentile"] == 96.0
    assert row["priority_score"] == 92.0
    assert row["liquidity_usd"] == 2_000_000.0
    assert row["volume_acceleration_score"] == 80.0


def test_monitor_queue_missing_rs_observation_preserves_full_history(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        opportunity_monitor_queue,
        "QUEUE_FILE",
        tmp_path / "queue.json",
    )
    start = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    for offset in range(12):
        opportunity_monitor_queue.upsert_candidate(
            CandidateObservation(
                symbol="SOLUSD",
                direction="LONG",
                source="FULL_MARKET",
                observed_at=start + timedelta(minutes=offset),
                relative_strength_percentile=70.0 + offset,
                priority_score=80.0,
            )
        )

    before = opportunity_monitor_queue.read_candidates(
        now=start + timedelta(minutes=11)
    )[0]
    before_history = list(before["relative_strength_history"])
    assert len(before_history) == 12

    opportunity_monitor_queue.upsert_candidate(
        CandidateObservation(
            symbol="SOLUSD",
            direction="LONG",
            source="EARLY_MOVER",
            observed_at=start + timedelta(minutes=12),
            relative_strength_percentile=None,
            priority_score=75.0,
        )
    )

    after = opportunity_monitor_queue.read_candidates(
        now=start + timedelta(minutes=12)
    )[0]
    assert after["relative_strength_history"] == before_history
    assert len(after["relative_strength_history"]) == 12


def test_monitor_queue_recovers_invalid_source_priority_shape(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        opportunity_monitor_queue,
        "QUEUE_FILE",
        tmp_path / "queue.json",
    )
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    opportunity_monitor_queue.upsert_candidate(
        CandidateObservation(
            symbol="SOLUSD",
            direction="LONG",
            source="FULL_MARKET",
            observed_at=now,
            priority_score=80.0,
        )
    )
    rows = json.loads(
        opportunity_monitor_queue.QUEUE_FILE.read_text(encoding="utf-8")
    )
    rows["SOLUSD:LONG"]["source_priority_scores"] = ["corrupt"]
    opportunity_monitor_queue.QUEUE_FILE.write_text(
        json.dumps(rows),
        encoding="utf-8",
    )

    opportunity_monitor_queue.upsert_candidate(
        CandidateObservation(
            symbol="SOLUSD",
            direction="LONG",
            source="EARLY_MOVER",
            observed_at=now + timedelta(minutes=1),
            priority_score=75.0,
        )
    )

    row = opportunity_monitor_queue.read_candidates(
        now=now + timedelta(minutes=1)
    )[0]
    assert row["source_priority_scores"] == {"EARLY_MOVER": 75.0}
    assert row["priority_score"] == 75.0


def test_failopen_notification_confirmation_persists_after_storage_recovers(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        notification_policy,
        "STATE_FILE",
        tmp_path / "notification.json",
    )
    monkeypatch.setattr(
        notification_policy,
        "LOCK_FILE",
        tmp_path / ".notification.lock",
    )
    assert notification_policy.confirm_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp",
        reservation_token="FAILOPEN-test",
    )
    assert not notification_policy.should_emit(
        identity="LONG:SOLUSD",
        event_type="ACTIONABLE_TRADE",
        fingerprint="fp",
    )


def test_managed_long_margin_position_is_not_duplicated_as_unmanaged():
    class Private:
        enabled = True

        def assert_read_only(self):
            return SimpleNamespace(is_read_only=True)

        def get_balance(self):
            return {}

        def get_open_positions(self):
            return {
                "P1": {
                    "pair": "SOLUSD",
                    "type": "buy",
                    "vol": "1.0",
                    "vol_closed": "0",
                }
            }

    class Public:
        def get_asset_pairs(self):
            return {}

        def get_tickers(self, pairs):
            return {}

    resolved = KrakenExposureResolver(
        private_client=Private(),
        public_client=Public(),
        trade_loader=lambda: [_trade()],
    ).resolve()

    managed = [row for row in resolved.exposures if row.status == "VERIFIED_MANAGED"]
    unmanaged = [row for row in resolved.exposures if row.status == "VERIFIED_UNMANAGED"]
    assert len(managed) == 1
    assert unmanaged == []


def test_mfe_giveback_uses_price_move_not_leverage_scaled_net_pnl():
    trade = _trade()
    result = TradeMonitorResult(
        symbol="SOLUSD",
        action="HOLD",
        current_price=104.0,
        unrealized_pct=4.0,
        net_pnl_pct=8.0,
        reasons=["healthy"],
    )
    refined = refine_protection_action(
        trade,
        result,
        {"mfe_pct": 8.0},
    )
    assert refined.action == "WARNING"
    assert "Profit protection" in refined.reasons[0]


def test_entry_watch_rejects_invalid_direction(tmp_path, monkeypatch):
    monkeypatch.setattr(
        entry_watch_queue,
        "ENTRY_WATCH_FILE",
        tmp_path / "entry_watch.json",
    )
    with pytest.raises(ValueError, match="LONG or SHORT"):
        entry_watch_queue.enqueue_entry_watch(
            symbol="SOLUSD",
            direction="SIDEWAYS",
            candidate_id="C1",
            continuation_score=80,
            now=datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc),
        )


def test_malformed_due_entry_watch_is_removed(monkeypatch):
    removed = []
    monkeypatch.setattr(
        entry_watch_recheck,
        "due_entry_watch",
        lambda **kwargs: [
            {
                "symbol": "SOLUSD",
                "direction": "SIDEWAYS",
                "risk_level": "low",
            }
        ],
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "remove_entry_watch",
        lambda symbol, direction: removed.append((symbol, direction)) or True,
    )
    summary = entry_watch_recheck.recheck_due_entry_watch()
    assert summary.checked == 0
    assert removed == [("SOLUSD", "SIDEWAYS")]


def test_malformed_qualified_outbox_row_is_removed(monkeypatch):
    row = {
        "trade_id": "Q-BAD",
        "plan": {"not": "an entry plan"},
    }
    removed = []
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_claim",
        lambda trade_id: ("lease", dict(row)),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: None,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )
    assert (
        qualified_alert_outbox._retry_one(
            "Q-BAD",
            bot_token="token",
            chat_id="chat",
        )
        == "MALFORMED"
    )
    assert removed == [("Q-BAD", "lease")]

def test_malformed_outbox_records_audit_and_terminalizes(monkeypatch):
    row = {
        "trade_id": "Q-AUDIT-BAD",
        "plan": {"not": "an entry plan"},
        "identity": "QUALIFIED_OPPORTUNITY:Q-AUDIT-BAD",
        "action": "ENTER_NOW",
        "fingerprint": "fp",
    }
    audits = []
    terminalized = []
    removed = []
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_claim",
        lambda trade_id: ("lease", dict(row)),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "record_telegram_not_eligible",
        lambda **kwargs: audits.append(kwargs),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "terminalize_pending_setup",
        lambda trade_id, status: terminalized.append((trade_id, status)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )

    status = qualified_alert_outbox._retry_one(
        "Q-AUDIT-BAD",
        bot_token="token",
        chat_id="chat",
    )

    assert status == "MALFORMED"
    assert terminalized == [("Q-AUDIT-BAD", "delivery_malformed")]
    assert removed == [("Q-AUDIT-BAD", "lease")]
    assert audits[0]["reason"].startswith("OUTBOX_MALFORMED:")


def test_malformed_outbox_retains_row_when_lifecycle_retirement_fails(
    monkeypatch,
):
    row = {
        "trade_id": "Q-BAD-PENDING",
        "plan": {"not": "an entry plan"},
    }
    removed = []
    released = []
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_claim",
        lambda trade_id: ("lease", dict(row)),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "record_telegram_not_eligible",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "terminalize_pending_setup",
        lambda trade_id, status: False,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_release",
        lambda trade_id, token: released.append((trade_id, token)) or True,
    )

    status = qualified_alert_outbox._retry_one(
        "Q-BAD-PENDING",
        bot_token="token",
        chat_id="chat",
    )

    assert status == "MALFORMED_PENDING"
    assert removed == []
    assert released == [("Q-BAD-PENDING", "lease")]


def test_reconciliation_identity_mismatch_terminalizes_outbox(monkeypatch):
    row = {
        "trade_id": "Q-MISMATCH",
        "plan": _queued_plan(),
        "direction": "LONG",
        "action": "ENTER_NOW",
        "tracking_candidate": {
            "economic_qualified": True,
            "recommended_capital": 100.0,
        },
        "identity": "QUALIFIED_OPPORTUNITY:Q-MISMATCH",
        "fingerprint": "fp",
        "message": "trade",
        "leverage": 1.0,
    }
    terminalized = []
    removed = []
    suppressions = []
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_claim",
        lambda trade_id: ("lease", dict(row)),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(
            ReconciliationIdentityMismatch("conflict")
        ),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "terminalize_pending_setup",
        lambda trade_id, status: terminalized.append(
            (trade_id, status)
        ) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "record_telegram_suppression",
        lambda **kwargs: suppressions.append(kwargs),
    )

    status = qualified_alert_outbox._retry_one(
        "Q-MISMATCH",
        bot_token="token",
        chat_id="chat",
    )

    assert status == "SUPPRESSED"
    assert terminalized == [("Q-MISMATCH", "tracking_failed")]
    assert removed == [("Q-MISMATCH", "lease")]
    assert suppressions[-1]["reason"] == "TRACKING_IDENTITY_MISMATCH_TERMINAL"


def test_non_numeric_outbox_leverage_is_malformed_and_removed(monkeypatch):
    row = {
        "trade_id": "Q-BAD-LEV",
        "plan": _queued_plan(),
        "direction": "LONG",
        "action": "ENTER_NOW",
        "tracking_candidate": {"economic_qualified": True},
        "leverage": "not-a-number",
    }
    removed = []
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_claim",
        lambda trade_id: ("lease", dict(row)),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: None,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )

    status = qualified_alert_outbox._retry_one(
        "Q-BAD-LEV",
        bot_token="token",
        chat_id="chat",
    )

    assert status == "MALFORMED"
    assert removed == [("Q-BAD-LEV", "lease")]


def test_entry_watch_expired_rows_do_not_evict_fresh_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(entry_watch_queue, "ENTRY_WATCH_FILE", tmp_path / "entry_watch.json")
    monkeypatch.setattr(entry_watch_queue, "MAX_ENTRY_WATCH", 1)
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    expired = {
        "OLDUSD:LONG": {
            "schema_version": 1,
            "symbol": "OLDUSD",
            "direction": "LONG",
            "candidate_id": "OLD",
            "continuation_score": 99,
            "updated_at": (now - timedelta(hours=2)).isoformat(),
            "next_due_at": (now - timedelta(hours=2)).isoformat(),
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
        }
    }
    entry_watch_queue.ENTRY_WATCH_FILE.write_text(json.dumps(expired), encoding="utf-8")

    entry_watch_queue.enqueue_entry_watch(
        symbol="SOLUSD",
        direction="LONG",
        candidate_id="NEW",
        continuation_score=10,
        now=now,
    )

    rows = entry_watch_queue.due_entry_watch(now=now + timedelta(seconds=120))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SOLUSD"


def _persist_entry_watch_rows(rows: dict, tmp_path, monkeypatch):
    monkeypatch.setattr(entry_watch_queue, "ENTRY_WATCH_FILE", tmp_path / "entry_watch.json")
    entry_watch_queue.ENTRY_WATCH_FILE.write_text(json.dumps(rows), encoding="utf-8")


def _watch_row(*, symbol, direction="LONG", now, continuation_score=50):
    return {
        "schema_version": 1,
        "symbol": symbol,
        "direction": direction,
        "candidate_id": "C1",
        "continuation_score": continuation_score,
        "updated_at": now.isoformat(),
        "next_due_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
    }


def test_persisted_empty_symbol_row_is_terminalized_and_never_reappears(tmp_path, monkeypatch):
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    _persist_entry_watch_rows(
        {":LONG": _watch_row(symbol="", now=now)},
        tmp_path,
        monkeypatch,
    )

    due = entry_watch_queue.due_entry_watch(now=now)
    assert due == []
    remaining = json.loads(entry_watch_queue.ENTRY_WATCH_FILE.read_text(encoding="utf-8"))
    assert remaining == {}

    # Never reappears on a later scan either -- the record is gone, not
    # merely skipped for one cycle.
    assert entry_watch_queue.due_entry_watch(now=now + timedelta(minutes=5)) == []


def test_persisted_whitespace_only_symbol_row_is_terminalized(tmp_path, monkeypatch):
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    _persist_entry_watch_rows(
        {"   :LONG": _watch_row(symbol="   ", now=now)},
        tmp_path,
        monkeypatch,
    )

    due = entry_watch_queue.due_entry_watch(now=now)
    assert due == []
    remaining = json.loads(entry_watch_queue.ENTRY_WATCH_FILE.read_text(encoding="utf-8"))
    assert remaining == {}
    assert entry_watch_queue.due_entry_watch(now=now + timedelta(minutes=5)) == []


def test_malformed_invalid_persisted_row_is_removed(tmp_path, monkeypatch):
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    _persist_entry_watch_rows(
        {
            "GARBAGE:LONG": "not-a-dict",
            "NOENTRY:LONG": {"schema_version": 1, "direction": "LONG"},
        },
        tmp_path,
        monkeypatch,
    )

    due = entry_watch_queue.due_entry_watch(now=now)
    assert due == []
    remaining = json.loads(entry_watch_queue.ENTRY_WATCH_FILE.read_text(encoding="utf-8"))
    assert remaining == {}


def test_valid_rows_continue_processing_alongside_malformed_rows(tmp_path, monkeypatch):
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    _persist_entry_watch_rows(
        {
            ":LONG": _watch_row(symbol="", now=now),
            "SOLUSD:LONG": _watch_row(symbol="SOLUSD", now=now, continuation_score=80),
        },
        tmp_path,
        monkeypatch,
    )

    due = entry_watch_queue.due_entry_watch(now=now)
    assert [row["symbol"] for row in due] == ["SOLUSD"]
    remaining = json.loads(entry_watch_queue.ENTRY_WATCH_FILE.read_text(encoding="utf-8"))
    assert list(remaining.keys()) == ["SOLUSD:LONG"]


def test_recheck_quarantines_malformed_row_without_usable_symbol(monkeypatch):
    removed_keys = []
    monkeypatch.setattr(
        entry_watch_recheck,
        "due_entry_watch",
        lambda **kwargs: [
            {
                "symbol": "   ",
                "direction": "LONG",
                "risk_level": "low",
                "_watch_key": "   :LONG",
            }
        ],
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "remove_entry_watch_key",
        lambda key: removed_keys.append(key) or True,
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "remove_entry_watch",
        lambda symbol, direction: (_ for _ in ()).throw(
            AssertionError("should not reconstruct a key when the queue key is known")
        ),
    )

    summary = entry_watch_recheck.recheck_due_entry_watch()

    assert summary.checked == 0
    assert removed_keys == ["   :LONG"]


def test_recheck_falls_back_to_reconstructed_key_without_watch_key(monkeypatch):
    removed = []
    monkeypatch.setattr(
        entry_watch_recheck,
        "due_entry_watch",
        lambda **kwargs: [{"symbol": "", "direction": "LONG", "risk_level": "low"}],
    )
    monkeypatch.setattr(
        entry_watch_recheck,
        "remove_entry_watch",
        lambda symbol, direction: removed.append((symbol, direction)) or True,
    )

    summary = entry_watch_recheck.recheck_due_entry_watch()

    assert summary.checked == 0
    assert removed == [("", "LONG")]


def _queued_plan():
    return {
        "symbol": "SOLUSD",
        "valid_now": True,
        "entry_style": "pullback_or_retest",
        "entry_low": 99.0,
        "entry_high": 100.0,
        "chase_limit": 101.0,
        "stop_price": 95.0,
        "target_1": 110.0,
        "target_2": 115.0,
        "reward_to_risk_1": 2.0,
        "reward_to_risk_2": 3.0,
        "risk_level": "low",
        "reason": "qualified",
        "direction": "LONG",
    }


def test_outbox_removes_already_confirmed_policy_emission(monkeypatch):
    row = {
        "trade_id": "Q-CONFIRMED",
        "plan": _queued_plan(),
        "direction": "LONG",
        "action": "ENTER_NOW",
        "tracking_candidate": {"economic_qualified": False},
        "identity": "QUALIFIED_OPPORTUNITY:Q-CONFIRMED",
        "policy_identity": "LONG:SOLUSD",
        "fingerprint": "fp",
        "message": "trade",
    }
    removed = []
    monkeypatch.setattr(qualified_alert_outbox, "_claim", lambda trade_id: ("lease", dict(row)))
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "accepted_delivery_message_id",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(qualified_alert_outbox, "reserve_emit", lambda **kwargs: None)
    monkeypatch.setattr(
        qualified_alert_outbox,
        "is_confirmed_emission",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )

    status = qualified_alert_outbox._retry_one(
        "Q-CONFIRMED",
        bot_token="token",
        chat_id="chat",
    )

    assert status == "DELIVERED"
    assert removed == [("Q-CONFIRMED", "lease")]


def test_outbox_terminally_suppresses_disabled_reconciliation(monkeypatch):
    row = {
        "trade_id": "Q-DISABLED",
        "plan": _queued_plan(),
        "direction": "LONG",
        "action": "ENTER_NOW",
        "tracking_candidate": {
            "economic_qualified": True,
            "recommended_capital": 100.0,
        },
        "identity": "QUALIFIED_OPPORTUNITY:Q-DISABLED",
        "policy_identity": "LONG:SOLUSD",
        "fingerprint": "fp",
        "message": "trade",
        "leverage": 1.0,
    }
    removed = []
    suppressions = []
    monkeypatch.setattr(qualified_alert_outbox, "_claim", lambda trade_id: ("lease", dict(row)))
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(
            ReconciliationTrackingDisabled("disabled")
        ),
    )
    terminalized = []
    monkeypatch.setattr(
        qualified_alert_outbox,
        "terminalize_pending_setup",
        lambda trade_id, status: terminalized.append((trade_id, status)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "record_telegram_suppression",
        lambda **kwargs: suppressions.append(kwargs),
    )

    status = qualified_alert_outbox._retry_one(
        "Q-DISABLED",
        bot_token="token",
        chat_id="chat",
    )

    assert status == "SUPPRESSED"
    assert terminalized == [("Q-DISABLED", "tracking_disabled")]
    assert removed == [("Q-DISABLED", "lease")]
    assert suppressions[0]["reason"] == "RECONCILIATION_NOT_APPLY_TERMINAL"


# --- Finding 3: outbox recovery record durability -----------------------


def _tracking_disabled_row(trade_id="Q-DUR"):
    return {
        "trade_id": trade_id,
        "plan": _queued_plan(),
        "direction": "LONG",
        "action": "ENTER_NOW",
        "tracking_candidate": {
            "economic_qualified": True,
            "recommended_capital": 100.0,
        },
        "identity": f"QUALIFIED_OPPORTUNITY:{trade_id}",
        "policy_identity": "LONG:SOLUSD",
        "fingerprint": "fp",
        "message": "trade",
        "leverage": 1.0,
    }


def test_outbox_retains_row_when_terminalization_fails(monkeypatch):
    row = _tracking_disabled_row()
    removed = []
    released = []
    monkeypatch.setattr(qualified_alert_outbox, "_claim", lambda trade_id: ("lease", dict(row)))
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(ReconciliationTrackingDisabled("disabled")),
    )
    # Terminalization fails outright, and a refreshed lookup still shows
    # "waiting" -- the recovery record must not be destroyed.
    monkeypatch.setattr(
        qualified_alert_outbox,
        "terminalize_pending_setup",
        lambda trade_id, status: False,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_release",
        lambda trade_id, token: released.append((trade_id, token)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox, "record_telegram_suppression", lambda **kwargs: None
    )

    status = qualified_alert_outbox._retry_one("Q-DUR", bot_token="token", chat_id="chat")

    assert status == "TRACKING_PENDING"
    assert removed == []
    assert released == [("Q-DUR", "lease")]


def test_outbox_removes_row_once_retry_confirms_terminalization(monkeypatch):
    row = _tracking_disabled_row()
    removed = []
    calls = {"n": 0}

    def _terminalize(trade_id, status):
        # First attempt fails, second (retry) attempt succeeds.
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(qualified_alert_outbox, "_claim", lambda trade_id: ("lease", dict(row)))
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(ReconciliationTrackingDisabled("disabled")),
    )
    monkeypatch.setattr(qualified_alert_outbox, "terminalize_pending_setup", _terminalize)
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )
    monkeypatch.setattr(qualified_alert_outbox, "_release", lambda trade_id, token: True)
    monkeypatch.setattr(
        qualified_alert_outbox, "record_telegram_suppression", lambda **kwargs: None
    )

    first = qualified_alert_outbox._retry_one("Q-DUR", bot_token="token", chat_id="chat")
    assert first == "TRACKING_PENDING"
    assert removed == []

    second = qualified_alert_outbox._retry_one("Q-DUR", bot_token="token", chat_id="chat")
    assert second == "SUPPRESSED"
    assert removed == [("Q-DUR", "lease")]


def test_outbox_terminalization_retry_is_idempotent(monkeypatch):
    row = _tracking_disabled_row()
    removed = []
    terminalize_calls = []

    monkeypatch.setattr(qualified_alert_outbox, "_claim", lambda trade_id: ("lease", dict(row)))
    monkeypatch.setattr(
        qualified_alert_outbox,
        "get_pending_setup_record",
        lambda trade_id: {"status": "waiting"},
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "register_reconciliation_intent",
        lambda **kwargs: (_ for _ in ()).throw(ReconciliationTrackingDisabled("disabled")),
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "terminalize_pending_setup",
        lambda trade_id, status: terminalize_calls.append((trade_id, status)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox,
        "_remove",
        lambda trade_id, token=None: removed.append((trade_id, token)) or True,
    )
    monkeypatch.setattr(
        qualified_alert_outbox, "record_telegram_suppression", lambda **kwargs: None
    )

    for _ in range(3):
        status = qualified_alert_outbox._retry_one("Q-DUR", bot_token="token", chat_id="chat")
        assert status == "SUPPRESSED"

    # Every repeated drain terminalizes/removes without creating duplicate
    # lifecycle transitions or notifications beyond one per retry.
    assert terminalize_calls == [("Q-DUR", "tracking_disabled")] * 3
    assert removed == [("Q-DUR", "lease")] * 3


def test_monitor_state_load_failure_never_overwrites_other_symbol_state(monkeypatch):
    t = _trade()
    warning = TradeMonitorResult(
        symbol="SOLUSD",
        action="WARNING",
        current_price=97.0,
        unrealized_pct=-3.0,
        reasons=["material deterioration"],
    )
    saved = []
    monkeypatch.setattr(
        trade_monitor_notifier,
        "_load_state",
        lambda: (_ for _ in ()).throw(OSError("state unavailable")),
    )
    monkeypatch.setattr(
        trade_monitor_notifier,
        "_save_state",
        lambda state: saved.append(state),
    )
    monkeypatch.setattr(trade_monitor_notifier, "should_emit", lambda **kwargs: True)
    monkeypatch.setattr(trade_monitor_notifier, "record_emitted", lambda **kwargs: None)
    monkeypatch.setattr(
        trade_monitor_notifier,
        "send_tracked_telegram",
        lambda **kwargs: SimpleNamespace(delivered=True, message_id=1),
    )

    assert trade_monitor_notifier.send_monitor_update(
        t,
        warning,
        "token",
        "chat",
    )
    assert saved == []