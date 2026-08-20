from datetime import datetime, timedelta, timezone

import pytest

from app.services import pending_setup_registry
from app.services.legacy_tradingview_guard import evaluate_legacy_tradingview_request
from app.services.signal_evidence_contracts import (
    EvidenceEnvelope,
    MovementSignalEnvelope,
    transition_allowed,
)


def test_ambiguous_external_evidence_cannot_be_directional():
    with pytest.raises(ValueError):
        EvidenceEnvelope(
            source="COINGECKO",
            evidence_type="REFERENCE",
            symbol="ABCUSD",
            bias="BULLISH",
            strength=5,
            provider_status="AVAILABLE",
            identity_status="AMBIGUOUS",
        )


def test_unavailable_evidence_cannot_be_directional():
    with pytest.raises(ValueError):
        EvidenceEnvelope(
            source="SOCIAL",
            evidence_type="MOMENTUM",
            symbol="ABCUSD",
            bias="BEARISH",
            strength=5,
            provider_status="UNAVAILABLE",
        )


def test_movement_envelope_has_stable_signal_id_and_non_actionable_default():
    envelope = MovementSignalEnvelope(
        detector="MOVEMENT_DISCOVERY",
        detector_version="2.1",
        symbol="BTCUSD",
        direction="LONG",
        stage="WATCH",
        observed_at="2026-08-20T12:00:00+00:00",
        reference_price=100.0,
        source_fingerprint="WATCH:70:55",
    )
    assert envelope.signal_id == envelope.signal_id
    assert len(envelope.signal_id) == 24
    assert envelope.actionable is False


def test_signal_lifecycle_legal_transitions_are_explicit():
    assert transition_allowed("DISCOVERED", "WATCH")
    assert transition_allowed("READY", "USER_ACCEPTED")
    assert transition_allowed("ACTIVE", "CLOSED")
    assert not transition_allowed("DISCOVERED", "ACTIVE")
    assert not transition_allowed("EXPIRED", "READY")


def test_legacy_tradingview_is_disabled_by_default_in_production(monkeypatch):
    monkeypatch.delenv("TRADINGVIEW_V1_ENABLED", raising=False)
    monkeypatch.delenv("TRADINGVIEW_V1_WEBHOOK_SECRET", raising=False)
    decision = evaluate_legacy_tradingview_request(
        app_env="production",
        supplied_legacy_secret=None,
    )
    assert decision.allowed is False
    assert decision.status_code == 404


def test_legacy_tradingview_uses_separate_secret_in_production(monkeypatch):
    secret = "x" * 48
    monkeypatch.setenv("TRADINGVIEW_V1_ENABLED", "true")
    monkeypatch.setenv("TRADINGVIEW_V1_WEBHOOK_SECRET", secret)
    denied = evaluate_legacy_tradingview_request(
        app_env="production",
        supplied_legacy_secret="wrong",
    )
    allowed = evaluate_legacy_tradingview_request(
        app_env="production",
        supplied_legacy_secret=secret,
    )
    assert denied.allowed is False and denied.status_code == 401
    assert allowed.allowed is True
    assert allowed.inject_operator_secret is True


def test_pending_setup_gets_expiry_without_changing_trade_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(pending_setup_registry, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setenv("PENDING_SETUP_TTL_HOURS", "24")
    setup = pending_setup_registry.PendingSetup(
        symbol="BTCUSD",
        entry_low=100,
        entry_high=101,
        chase_limit=102,
        stop_price=95,
        target_1=105,
        target_2=110,
        risk_level="low",
        confidence=90,
        trade_id="OHM-BTCUSD-test",
        created_at="2026-08-20T10:00:00+00:00",
    )
    stored = pending_setup_registry.add_pending_setup(setup)
    assert stored.trade_id == "OHM-BTCUSD-test"
    assert stored.expires_at == "2026-08-21T10:00:00+00:00"


def test_expiry_selector_uses_trade_id(monkeypatch):
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    setups = [
        pending_setup_registry.PendingSetup(
            symbol="BTCUSD", entry_low=1, entry_high=2, chase_limit=3,
            stop_price=.5, target_1=4, target_2=5, risk_level="low", confidence=80,
            trade_id="old", created_at=(now - timedelta(days=2)).isoformat(), expires_at=(now - timedelta(hours=1)).isoformat(),
        ),
        pending_setup_registry.PendingSetup(
            symbol="ETHUSD", entry_low=1, entry_high=2, chase_limit=3,
            stop_price=.5, target_1=4, target_2=5, risk_level="low", confidence=80,
            trade_id="fresh", created_at=now.isoformat(), expires_at=(now + timedelta(hours=2)).isoformat(),
        ),
    ]
    calls = []
    monkeypatch.setattr(pending_setup_registry, "get_pending_setups", lambda: setups)
    monkeypatch.setattr(
        pending_setup_registry,
        "terminalize_pending_setup",
        lambda trade_id, status: calls.append((trade_id, status)) or True,
    )
    assert pending_setup_registry.expire_due_pending_setups(now=now) == 1
    assert calls == [("old", "expired")]
