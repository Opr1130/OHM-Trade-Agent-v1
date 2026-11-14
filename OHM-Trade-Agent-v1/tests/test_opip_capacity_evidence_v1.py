"""ATDD-OPIP-CAPACITY-EVIDENCE-V1: capacity refusals leave auditable numbers, never new verdicts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import actionable_admission as admission
from app.services import telegram_delivery as delivery

pytestmark = pytest.mark.acceptance

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

BASE = {
    "instrument": "SOLUSD",
    "venue": "KRAKEN",
    "venue_identity_valid": True,
    "observed_at": NOW - timedelta(minutes=2),
    "expires_at": NOW + timedelta(hours=8),
    "episode_id": "SETUP:1",
    "direction": "LONG",
    "entry_style": "enter_now",
    "valid_now": True,
    "current_price": 100.0,
    "entry_price": 100.0,
    "entry_low": 99.5,
    "entry_high": 100.5,
    "chase_limit": 101.0,
    "stop_price": 97.0,
    "target_1": 108.0,
    "target_2": 112.0,
    "invalidation": "close below 97.00",
    "market_liquidity_usd": 2_400_000.0,
    "position_capacity_usd": 5_000.0,
    "order_size_usd": 750.0,
    "spread_bps": 8.0,
    "size_slippage_pct": 0.25,
    "depth_covered": True,
    "economics_available": True,
    "deterministic_qualified": True,
    "risk_gate_passed": True,
}


def _ev(**overrides: object) -> admission.ActionableEvidence:
    return admission.ActionableEvidence(**{**BASE, **overrides})


def _verdict(**overrides: object) -> admission.ActionableAdmission:
    return admission.evaluate_actionable_admission(evidence=_ev(**overrides), now=NOW)


def test_ac_001_capacity_record_never_changes_the_verdict():
    """ATDD-OPIP-CAPACITY-EVIDENCE-V1/AC-001: the capacity record never changes an admission verdict or the stored payload keys."""
    plain = _verdict(position_capacity_usd=400.0)
    enriched = _verdict(
        position_capacity_usd=400.0,
        recommended_position_notional_usd=400.0,
        capacity_capped=True,
        weaker_side_depth_usd=4_000.0,
        depth_band_pct=0.5,
    )
    for left, right in ((plain, enriched), (_verdict(), _verdict(capacity_capped=False))):
        assert (left.admitted, left.condition, left.reason, left.reasons) == (
            right.admitted,
            right.condition,
            right.reason,
            right.reasons,
        )
    assert plain.reason == admission.REASON_CAPACITY_INSUFFICIENT
    assert plain.evidence_payload().keys() == _verdict().evidence_payload().keys()
    assert "capacity" not in plain.evidence_payload()
    record = plain.capacity
    assert record["order_size_usd"] == 750.0
    assert record["position_capacity_usd"] == 400.0
    assert record["allowed_notional_usd"] == 400.0
    assert record["order_to_allowed_ratio"] == 1.875
    assert record["order_exceeds_allowed"] is True


def test_ac_001_a_boundary_and_missing_data():
    """ATDD-OPIP-CAPACITY-EVIDENCE-V1/AC-001: boundary, utilization and missing-data cases are recorded without changing the verdict."""
    policy = admission.ActionableNotificationPolicy()
    at_limit = _verdict(position_capacity_usd=750.0)
    assert at_limit.capacity["order_exceeds_allowed"] is False
    assert at_limit.reason != admission.REASON_CAPACITY_INSUFFICIENT
    one_cent_over = _verdict(position_capacity_usd=749.99)
    assert one_cent_over.capacity["order_exceeds_allowed"] is True
    assert one_cent_over.reason == admission.REASON_CAPACITY_INSUFFICIENT

    half = admission.evaluate_actionable_admission(
        evidence=_ev(position_capacity_usd=1_000.0),
        policy=type(policy)(**{**policy.__dict__, "max_capacity_utilization_pct": 50.0}),
        now=NOW,
    )
    assert half.capacity["allowed_notional_usd"] == 500.0
    assert half.reason == admission.REASON_CAPACITY_INSUFFICIENT

    missing = _verdict(position_capacity_usd=None)
    assert missing.reason == admission.REASON_EXECUTION_EVIDENCE_MISSING
    assert missing.capacity["allowed_notional_usd"] is None
    assert missing.capacity["order_exceeds_allowed"] is None
    assert missing.capacity["order_to_allowed_ratio"] is None
    zero = _verdict(position_capacity_usd=0.0)
    assert zero.reason == admission.REASON_CAPACITY_INSUFFICIENT
    assert zero.capacity["order_to_allowed_ratio"] is None
    json.dumps(zero.capacity, allow_nan=False)


def test_ac_001_b_stale_size_signature_is_recorded():
    """ATDD-OPIP-CAPACITY-EVIDENCE-V1/AC-001: an order basis that differs from the recommended notional is visible in the record."""
    stale = _verdict(
        position_capacity_usd=400.0,
        order_size_usd=750.0,
        recommended_position_notional_usd=400.0,
        capacity_capped=True,
    )
    assert stale.capacity["order_basis_matches_recommended"] is False
    assert stale.capacity["capacity_capped_by_action_gate"] is True
    consistent = _verdict(
        position_capacity_usd=400.0,
        order_size_usd=400.0,
        recommended_position_notional_usd=400.0,
        capacity_capped=True,
    )
    assert consistent.capacity["order_basis_matches_recommended"] is True
    unknown = _verdict(position_capacity_usd=400.0)
    assert unknown.capacity["order_basis_matches_recommended"] is None


def test_ac_002_ledger_row_carries_bounded_evidence(tmp_path):
    """ATDD-OPIP-CAPACITY-EVIDENCE-V1/AC-002: the suppression ledger row carries bounded flat evidence and is otherwise unchanged."""
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"
    common = dict(
        identity="QUALIFIED_OPPORTUNITY:T1",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="NEW_TRADE",
        fingerprint="fp",
        reason="ACTIONABLE_GATE:POSITION_CAPACITY_INSUFFICIENT",
        symbol="SOL",
        state_file=state,
        event_file=events,
    )
    delivery.record_telegram_suppression(**common)
    delivery.record_telegram_suppression(
        **common,
        evidence={
            "order_size_usd": 750.0,
            "capacity_capped_by_action_gate": True,
            "note": "x" * 500,
            "nested": {"a": 1},
            "nan": float("nan"),
            "missing": None,
        },
    )
    plain, with_evidence = [json.loads(line) for line in events.read_text().splitlines()]
    assert "evidence" not in plain
    assert with_evidence["evidence"] == {
        "order_size_usd": 750.0,
        "capacity_capped_by_action_gate": True,
        "note": "x" * 120,
        "missing": None,
    }
    assert with_evidence["suppression_reason"] == common["reason"]
