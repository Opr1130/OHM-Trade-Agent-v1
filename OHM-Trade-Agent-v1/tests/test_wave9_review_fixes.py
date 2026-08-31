from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import (
    entry_watch_queue,
    entry_watch_recheck,
    notification_policy,
    opportunity_monitor_queue,
    qualified_alert_outbox,
)
from app.services.active_trade_registry import ActiveTrade
from app.services.kraken_exposure_resolver import KrakenExposureResolver
from app.services.opportunity_monitor_queue import CandidateObservation
from app.services.position_materiality import refine_protection_action
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
            return {"SOL": 1.0}

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
