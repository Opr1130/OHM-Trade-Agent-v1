"""Profile influence is authorized only by an approved promotion.

Contract change (PR-A): this module previously asserted that the persisted
profitability profile - and, on failure, an in-memory fallback recomputed from
outcome records - could move runtime calibration directly from evidence. That
was an approval bypass: enough samples alone would change live sizing.

Runtime influence is now neutral unless a durable, human-approved, versioned
and currently effective promotion exists for the exact learned profile
content. These tests pin the refusal side of that boundary. The full approval
matrix lives in test_opip_pra_learning_safety_gate.py.
"""
from __future__ import annotations

from datetime import datetime

from app.services import trade_decision_intelligence as intel
from app.services.learning_governance import (
    CalibrationPromotionResolution,
    PROMOTION_REASON_APPROVED_AND_EFFECTIVE,
    PROMOTION_REASON_NO_APPROVED_PROMOTION,
)


def _approved_resolution() -> CalibrationPromotionResolution:
    return CalibrationPromotionResolution(
        True,
        "CALPROF:" + "a" * 32,
        PROMOTION_REASON_APPROVED_AND_EFFECTIVE,
        "PROMO:test",
    )


def test_calibrated_evidence_without_approval_does_not_influence_runtime(monkeypatch):
    """Sufficient samples are evidence, not authorization."""
    monkeypatch.setattr(intel, "learned_multiplier", lambda **kwargs: 1.12)
    monkeypatch.setattr(intel, "load_approved_calibration_promotion", lambda *a, **k: None)

    multiplier, status = intel._effective_calibration_multiplier(
        direction="LONG",
        regime="RISK_ON",
    )

    assert multiplier == 1.0
    assert status == PROMOTION_REASON_NO_APPROVED_PROMOTION


def test_profile_read_failure_never_falls_back_to_in_memory_model(monkeypatch):
    """The decision-time fallback was a bypass and must not be resurrected.

    Even with an approved promotion active, a failed profile read is neutral -
    never the previously-derived in-memory multiplier.
    """
    monkeypatch.setattr(intel, "load_approved_calibration_promotion", lambda *a, **k: object())
    monkeypatch.setattr(intel, "active_profile_id", lambda *a, **k: "CALPROF:" + "a" * 32)
    monkeypatch.setattr(intel, "resolve_calibration_promotion", lambda **kwargs: _approved_resolution())

    def _boom(**kwargs):
        raise OSError("profile unavailable")

    monkeypatch.setattr(intel, "learned_multiplier", _boom)

    multiplier, status = intel._effective_calibration_multiplier(
        direction="LONG",
        regime="RISK_OFF",
    )

    assert multiplier == 1.0
    assert status == PROMOTION_REASON_APPROVED_AND_EFFECTIVE


def test_decision_time_model_is_not_reachable_from_runtime_path():
    """Structural regression: the in-memory model must not be wired to sizing."""
    assert not hasattr(intel, "calibrated_multiplier")
    assert not hasattr(intel, "calibration_model")
    assert not hasattr(intel, "get_outcomes")


def test_approved_promotion_authorizes_profile_multiplier(monkeypatch):
    """The approved path still works - the gate authorizes, it does not block."""
    monkeypatch.setattr(intel, "load_approved_calibration_promotion", lambda *a, **k: object())
    monkeypatch.setattr(intel, "active_profile_id", lambda *a, **k: "CALPROF:" + "a" * 32)
    monkeypatch.setattr(intel, "resolve_calibration_promotion", lambda **kwargs: _approved_resolution())
    monkeypatch.setattr(intel, "learned_multiplier", lambda **kwargs: 1.12)

    multiplier, status = intel._effective_calibration_multiplier(
        direction="LONG",
        regime="RISK_ON",
    )

    assert multiplier == 1.12
    assert status.startswith(PROMOTION_REASON_APPROVED_AND_EFFECTIVE)


def test_gate_uses_timezone_aware_clock(monkeypatch):
    """Guard against a naive-clock regression in the approval resolution."""
    seen: dict[str, object] = {}

    def _capture(*, promotion, active_profile_id, now):
        seen["now"] = now
        return CalibrationPromotionResolution(False, None, PROMOTION_REASON_NO_APPROVED_PROMOTION)

    monkeypatch.setattr(intel, "load_approved_calibration_promotion", lambda *a, **k: object())
    monkeypatch.setattr(intel, "active_profile_id", lambda *a, **k: "CALPROF:" + "a" * 32)
    monkeypatch.setattr(intel, "resolve_calibration_promotion", _capture)

    intel._effective_calibration_multiplier(direction="LONG", regime=None)

    assert isinstance(seen["now"], datetime)
    assert seen["now"].tzinfo is not None
    assert seen["now"].utcoffset() is not None
