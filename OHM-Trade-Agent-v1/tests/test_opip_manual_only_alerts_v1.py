"""Manual-only alert transport remains advisory, quality-gated and untracked."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from app.services import (
    chief_alert_notifier,
    pending_setup_registry,
    qualified_alert_outbox,
    qualified_trade_tracking,
)
from app.services.pending_setup_registry import PendingSetup
from tests.test_wave9_action_notification_reliability import (
    _admitted_verdict,
    _patch_pending,
    plan,
    scanner_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.acceptance
def test_manual_only_tracking_never_creates_kraken_intent(monkeypatch):
    """ATDD-OPIP-MANUAL-ALERT-v1/AC-001: explicit untracked advisory does not register an exchange intent."""
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "false")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "observe")
    entered = []
    monkeypatch.setattr(
        qualified_trade_tracking, "get_order_intent",
        lambda trade_id: entered.append(trade_id),
    )
    candidate = {"economic_qualified": True, "manual_only_advisory": True}
    assert qualified_trade_tracking.manual_advisory_without_reconciliation(candidate)
    qualified_trade_tracking.register_reconciliation_intent(
        candidate=candidate, plan=plan(), action="ENTER_NOW",
        direction="LONG", leverage=1.0, trade_id="MANUAL-1",
    )
    assert entered == []
    with pytest.raises(qualified_trade_tracking.ReconciliationTrackingDisabled):
        qualified_trade_tracking.register_reconciliation_intent(
            candidate={"economic_qualified": True}, plan=plan(),
            action="ENTER_NOW", direction="LONG", leverage=1.0,
            trade_id="TRACKED-REQUIRED",
        )
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "apply")
    assert not qualified_trade_tracking.manual_advisory_without_reconciliation(candidate)
    with pytest.raises(ValueError, match="recommended capital"):
        qualified_trade_tracking.register_reconciliation_intent(
            candidate=candidate, plan=plan(), action="ENTER_NOW",
            direction="LONG", leverage=1.0, trade_id="ON-NOT-SKIPPED",
        )


@pytest.mark.acceptance
def test_manual_only_signal_uses_existing_action_and_market_admission(tmp_path, monkeypatch):
    """ATDD-OPIP-MANUAL-ALERT-v1/AC-002: a qualified manual signal is labelled and sent with zero order intent writes."""
    _patch_pending(tmp_path, monkeypatch)
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "false")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "observe")
    monkeypatch.setattr(chief_alert_notifier, "should_send_trade_plan", lambda *args: True)
    monkeypatch.setattr(chief_alert_notifier, "record_recommendation", lambda **kwargs: None)
    monkeypatch.setattr(chief_alert_notifier, "accepted_delivery_message_id", lambda **kwargs: None)
    monkeypatch.setattr(chief_alert_notifier, "reserve_emit", lambda **kwargs: "lease")
    monkeypatch.setattr(chief_alert_notifier, "_settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(qualified_trade_tracking, "get_order_intent",
                        lambda *_: pytest.fail("manual alert tried to read a Kraken order intent"))
    monkeypatch.setattr(qualified_trade_tracking, "register_order_intent",
                        lambda *_: pytest.fail("manual alert tried to write a Kraken order intent"))
    delivered = []
    monkeypatch.setattr(
        chief_alert_notifier, "send_tracked_telegram",
        lambda **kwargs: (delivered.append(kwargs["message"]) or
                          SimpleNamespace(delivered=True, message_id=42)),
    )
    candidate = {
        "confidence": 88, "decision": "alert", "economic_qualified": True,
        "action_gate_evaluated": True, "action_gate_allowed": True,
        "recommended_capital": 200.0, "manual_only_advisory": True,
        **scanner_evidence(),
    }
    assert chief_alert_notifier.send_trade_plan(candidate, plan(), "summary", "token", "chat")
    assert len(delivered) == 1
    assert delivered[0].startswith(qualified_trade_tracking.MANUAL_ONLY_ALERT_BANNER)
    assert "No automatic order, fill tracking, or protective stop" in delivered[0]
    assert "Entry zone:" in delivered[0]
    assert "Stop" in delivered[0]
    assert "Target" in delivered[0]
    assert "Fill Tracking: DISABLED" in candidate["alert_detail"]


@pytest.mark.acceptance
def test_replay_requires_explicit_manual_stamp_and_still_requires_admission(tmp_path, monkeypatch):
    """ATDD-OPIP-MANUAL-ALERT-v1/AC-003: only a correctly stamped admitted manual card retries without recon."""
    _patch_pending(tmp_path, monkeypatch)
    monkeypatch.setenv("KRAKEN_RECONCILIATION_ENABLED", "false")
    monkeypatch.setenv("KRAKEN_RECONCILIATION_MODE", "observe")
    setup = pending_setup_registry.add_pending_setup(
        PendingSetup(
            symbol="SOLUSD", entry_low=99.0, entry_high=100.0, chase_limit=101.0,
            stop_price=95.0, target_1=110.0, target_2=115.0,
            risk_level="low", confidence=88, trade_id="MANUAL-Q-1",
        )
    )
    candidate = {
        "economic_qualified": True,
        "recommended_capital": 200.0,
        "manual_only_advisory": True,
    }
    qualified_alert_outbox.queue_qualified_alert(
        trade_id=setup.trade_id,
        message=qualified_trade_tracking.MANUAL_ONLY_ALERT_BANNER + "\nEntry zone...",
        candidate=candidate, plan=plan(), action="ENTER_NOW",
        direction="LONG", identity=f"QUALIFIED_OPPORTUNITY:{setup.trade_id}",
        fingerprint="fingerprint-manual", reason="DELIVERY_PENDING",
        admission=_admitted_verdict(),
    )
    monkeypatch.setattr(qualified_alert_outbox, "accepted_delivery_message_id", lambda **kwargs: None)
    monkeypatch.setattr(qualified_alert_outbox, "reserve_emit", lambda **kwargs: "lease")
    monkeypatch.setattr(qualified_alert_outbox, "_settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(qualified_trade_tracking, "get_order_intent",
                        lambda *_: pytest.fail("retry tried to read a Kraken order intent"))
    monkeypatch.setattr(qualified_trade_tracking, "register_order_intent",
                        lambda *_: pytest.fail("retry tried to write a Kraken order intent"))
    messages = []
    monkeypatch.setattr(
        qualified_alert_outbox, "send_tracked_telegram",
        lambda **kwargs: (messages.append(kwargs["message"]) or
                          SimpleNamespace(delivered=True, message_id=43)),
    )
    delivered, pending = qualified_alert_outbox.retry_qualified_alerts(
        bot_token="token", chat_id="chat",
    )
    assert (delivered, pending) == (1, 0)
    assert len(messages) == 1
    assert messages[0].startswith(qualified_trade_tracking.MANUAL_ONLY_ALERT_BANNER)


@pytest.mark.acceptance
def test_unstamped_legacy_alert_remains_tracking_required():
    """ATDD-OPIP-MANUAL-ALERT-v1/AC-003: mode OFF cannot silently reclassify an old tracking-required alert."""
    track = (ROOT / "app/services/qualified_trade_tracking.py").read_text(encoding="utf-8")
    outbox = (ROOT / "app/services/qualified_alert_outbox.py").read_text(encoding="utf-8")
    notifier = (ROOT / "app/services/chief_alert_notifier.py").read_text(encoding="utf-8")
    scan = (ROOT / "app/jobs/scan_opportunities.py").read_text(encoding="utf-8")
    assert 'candidate.get("manual_only_advisory") is True' in track
    assert 'not enabled' in track and 'mode == "observe"' in track
    assert '"manual_only_advisory": candidate.get("manual_only_advisory") is True' in outbox
    assert 'not manual_advisory_without_reconciliation(candidate)' in outbox
    assert "MANUAL_ADVISORY_RETRY_UNVERIFIED" in outbox
    assert "MANUAL_ADVISORY_MODE_UNVERIFIED" in notifier
    assert 'alert["manual_only_advisory"]' in scan
    assert 'not reconciliation_enabled() and reconciliation_mode() == "observe"' in scan
