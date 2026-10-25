"""ATDD: F6 model qualification and offline evaluation (research only).

Increment: ATDD-F6-QUALIFICATION-v1

These tests prove the qualification *gate* and the scoring rules. They do not and
cannot prove predictive performance: no dataset is supplied, and the module
reports DATASET_BLOCKED. The production registry is asserted to stay empty.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip import forecast as engine
from app.opip import forecast_qualification as q

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-F6-QUALIFICATION-v1"
UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def record(**over) -> dict:
    body = {
        "model_kind": "CALIBRATED_SHADOW",
        "model_version": "1.0.0",
        "artifact_fingerprint": "sha256:" + "a" * 64,
        "training_dataset_fingerprint": "sha256:" + "b" * 64,
        "validation_dataset_fingerprint": "sha256:" + "c" * 64,
        "test_dataset_fingerprint": "sha256:" + "d" * 64,
        "feature_definitions_fingerprint": "sha256:" + "e" * 64,
        "horizon_minutes": 60,
        "venue_instrument_id": "KRAKEN:XBTUSD",
        "training_cutoff": T0,
        "validation_start": T0 + timedelta(days=30),
        "test_start": T0 + timedelta(days=60),
        "calibration_report_id": "FEVAL:candidate-1",
        "reproducibility_manifest": {"code_sha": "f" * 40, "dataset_sha": "b" * 64},
        "drift_monitor": {"defined": True, "metric": "PSI"},
        "cost_model": {"spread_included": True, "fees_included": True},
        "embargo_seconds": 3600,
        "purging_applied": True,
        "provenance": {
            "provider": "kraken-public-ohlc",
            "kind": "NATIVE",
            "point_in_time_verified": True,
        },
    }
    body.update(over)
    return body


# AC-001 ----------------------------------------------------------------------
def test_ac_001_a_complete_record_is_only_a_candidate():
    """ATDD-F6-QUALIFICATION-v1/AC-001: a complete record is a candidate, never trusted."""
    result = q.qualify_model_record(record())
    assert result.status == q.QUALIFICATION_CANDIDATE
    evidence = result.as_evidence()
    assert evidence["trusted"] is False
    assert evidence["model_registered"] is False
    assert evidence["trade_authority_changed"] is False
    assert evidence["thresholds"]["status"] == "UNAPPROVED"


# AC-002 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "over,reason",
    [
        ({"model_version": ""}, "MISSING_MODEL_VERSION"),
        ({"test_dataset_fingerprint": None}, "MISSING_TEST_DATASET_FINGERPRINT"),
        ({"provenance": {}}, "PROVENANCE_MISSING"),
        ({"provenance": {"provider": "x", "kind": "SYNTHETIC",
                         "point_in_time_verified": True}},
         "FORBIDDEN_PROVENANCE_SYNTHETIC"),
        ({"provenance": {"provider": "x", "kind": "NATIVE"}},
         "POINT_IN_TIME_UNVERIFIED"),
    ],
)
def test_ac_002_incomplete_or_forbidden_provenance_is_refused(over, reason):
    """ATDD-F6-QUALIFICATION-v1/AC-002: missing fields and forbidden provenance refuse."""
    result = q.qualify_model_record(record(**over))
    assert result.status == q.QUALIFICATION_INCOMPLETE
    assert reason in result.reasons
    assert result.as_evidence()["trusted"] is False


# AC-003 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "over,reason",
    [
        ({"validation_start": T0}, "PERIODS_NOT_ORDERED"),
        ({"test_start": T0 + timedelta(days=1)}, "PERIODS_NOT_ORDERED"),
        ({"embargo_seconds": 60}, "EMBARGO_SHORTER_THAN_LABEL_HORIZON"),
        ({"embargo_seconds": None}, "EMBARGO_MISSING"),
        ({"purging_applied": False}, "PURGING_NOT_PROVEN"),
        ({"reproducibility_manifest": {"code_sha": ""}},
         "REPRODUCIBILITY_MANIFEST_INCOMPLETE"),
        ({"drift_monitor": {"defined": False}}, "DRIFT_MONITOR_UNDEFINED"),
        ({"cost_model": {"spread_included": False}}, "COST_MODEL_INCOMPLETE"),
        ({"training_cutoff": datetime(2026, 1, 1)}, "TIMESTAMP_NOT_UTC"),
    ],
)
def test_ac_003_leakage_and_reproducibility_gaps_are_rejected(over, reason):
    """ATDD-F6-QUALIFICATION-v1/AC-003: overlapping periods, missing purge/embargo and unproven provenance are rejected."""
    result = q.qualify_model_record(record(**over))
    assert result.status == q.QUALIFICATION_REJECTED
    assert reason in result.reasons


# AC-004 ----------------------------------------------------------------------
def test_ac_004_scoring_is_exact_and_refuses_unscoreable_input():
    """ATDD-F6-QUALIFICATION-v1/AC-004: scoring is exact and never guesses."""
    assert q.brier_score([(1.0, 1), (0.0, 0)]) == 0.0
    assert q.brier_score([(0.5, 1), (0.5, 0)]) == 0.25
    assert q.brier_score([]) is None
    assert q.brier_score([(float("nan"), 1)]) is None
    assert q.brier_score([(1.5, 1)]) is None
    assert q.brier_score([(True, 1)]) is None
    assert q.expected_calibration_error([(0.5, 1), (0.5, 0)]) == 0.0
    assert q.expected_calibration_error([(0.9, 0)]) == pytest.approx(0.9)
    assert q.expected_calibration_error([]) is None


# AC-005 ----------------------------------------------------------------------
def test_ac_005_walk_forward_splits_never_leak_and_reject_bad_input():
    """ATDD-F6-QUALIFICATION-v1/AC-005: temporal splits are ordered, embargoed and leak-free."""
    stamps = [T0 + timedelta(hours=index) for index in range(12)]
    splits = q.walk_forward_splits(stamps, folds=3, embargo_seconds=3600)
    assert splits
    for train, test in splits:
        assert max(train) < min(test)
        gap = (stamps[min(test)] - stamps[max(train)]).total_seconds()
        assert gap >= 3600
    with pytest.raises(ValueError):
        q.walk_forward_splits([T0.replace(tzinfo=None)], folds=1)
    with pytest.raises(ValueError):
        q.walk_forward_splits(list(reversed(stamps)), folds=2)
    with pytest.raises(ValueError):
        q.walk_forward_splits(stamps[:2], folds=3)


def test_ac_005_evaluation_reports_dataset_blocked_and_never_promotes():
    """ATDD-F6-QUALIFICATION-v1/AC-005: no dataset means DATASET_BLOCKED, never a PASS."""
    samples = [
        {
            "observed_at": T0 + timedelta(hours=index),
            "probability": 0.5,
            "outcome": index % 2,
        }
        for index in range(12)
    ]
    report = q.evaluate_walk_forward(samples, folds=3, embargo_seconds=3600)
    assert report["promotable"] is False
    assert report["trust_verdict"] == "DATASET_BLOCKED"
    assert report["dataset"] == "NONE_PROVIDED"
    assert report["split_leak_detected"] is False
    assert report["out_of_sample_predictions"] > 0
    assert report["thresholds"]["status"] == "UNAPPROVED"


# AC-006 ----------------------------------------------------------------------
def test_ac_006_the_production_registry_stays_empty_and_nothing_is_wired():
    """ATDD-F6-QUALIFICATION-v1/AC-006: qualification never registers or trusts a model."""
    assert engine.PRODUCTION_MODEL_REGISTRY.registered_adapter_count == 0
    assert engine.PRODUCTION_MODEL_REGISTRY.known_evaluation_report_ids == frozenset()
    source = (
        __import__("pathlib").Path(q.__file__).read_text(encoding="utf-8")
    )
    assert "PRODUCTION_MODEL_REGISTRY" not in source
    assert "register(" not in source
    assert "evaluate_forecast" not in source



# AC-007 ----------------------------------------------------------------------
def _artifact(**over):
    """A real engine artifact, built through the public contract."""
    import sys
    from pathlib import Path

    tests_dir = str(Path(__file__).resolve().parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    import test_opip_r3_f6_forecast_engine as fx

    return fx.artifact(**over)


def test_ac_007_a_record_must_describe_the_artifact_that_would_execute():
    """ATDD-F6-QUALIFICATION-v1/AC-007: qualification reconciles with the real artifact."""
    fx_artifact = _artifact()
    matching = record(
        model_kind=fx_artifact.model_kind.value,
        model_version=fx_artifact.model_version,
        artifact_fingerprint=fx_artifact.artifact_id,
        horizon_minutes=fx_artifact.horizon_contract.path_horizon_seconds // 60,
    )
    ok, reasons = q.reconcile_qualification_with_artifact(matching, fx_artifact)
    assert (ok, reasons) == (True, ())


@pytest.mark.parametrize(
    "over,reason",
    [
        ({"model_kind": "OTHER_KIND"}, "MODEL_KIND_MISMATCH"),
        ({"model_version": "9.9.9"}, "MODEL_VERSION_MISMATCH"),
        ({"artifact_fingerprint": "sha256:" + "0" * 64}, "ARTIFACT_IDENTITY_MISMATCH"),
        ({"horizon_minutes": 240}, "HORIZON_MISMATCH"),
        ({"model_kind": ""}, "MODEL_KIND_MISMATCH"),
    ],
)
def test_ac_007_a_mismatched_record_never_qualifies_the_artifact(over, reason):
    """ATDD-F6-QUALIFICATION-v1/AC-007: a record for a different model is refused."""
    fx_artifact = _artifact()
    base = dict(
        model_kind=fx_artifact.model_kind.value,
        model_version=fx_artifact.model_version,
        artifact_fingerprint=fx_artifact.artifact_id,
        horizon_minutes=fx_artifact.horizon_contract.path_horizon_seconds // 60,
    )
    base.update(over)
    ok, reasons = q.reconcile_qualification_with_artifact(record(**base), fx_artifact)
    assert ok is False
    assert reason in reasons
    # Reconciling never registers or trusts anything.
    assert engine.PRODUCTION_MODEL_REGISTRY.registered_adapter_count == 0


def test_ac_007_a_missing_artifact_is_refused():
    """ATDD-F6-QUALIFICATION-v1/AC-007: no artifact, no reconciliation."""
    ok, reasons = q.reconcile_qualification_with_artifact(record(), None)
    assert ok is False
    assert reasons == ("ARTIFACT_MISSING",)

