from app.services import trade_decision_intelligence as intel


def test_profitability_profile_preferred_when_calibrated(monkeypatch):
    monkeypatch.setattr(intel, "learned_multiplier", lambda **kwargs: 1.12)
    legacy = {"status": "CALIBRATED", "weights": {"direction:LONG": 0.9}}

    multiplier, status = intel._effective_calibration_multiplier(
        legacy_model=legacy,
        direction="LONG",
        regime="RISK_ON",
    )

    assert multiplier == 1.12
    assert status == "PROFITABILITY_PROFILE"


def test_profitability_profile_failure_falls_back(monkeypatch):
    monkeypatch.setattr(intel, "learned_multiplier", lambda **kwargs: (_ for _ in ()).throw(OSError("profile unavailable")))
    monkeypatch.setattr(intel, "calibrated_multiplier", lambda *args, **kwargs: 0.93)
    legacy = {"status": "CALIBRATED"}

    multiplier, status = intel._effective_calibration_multiplier(
        legacy_model=legacy,
        direction="LONG",
        regime="RISK_OFF",
    )

    assert multiplier == 0.93
    assert status == "CALIBRATED"
