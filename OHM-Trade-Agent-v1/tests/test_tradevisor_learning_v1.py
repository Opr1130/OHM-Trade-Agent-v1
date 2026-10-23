"""ATDD: Tradevisor prospective learning (no look-ahead, no synthetic fills).

Covers ATDD-10 (shadow outcome evidence without live authority), ATDD-44
(release-drift fail-closed), and the evidence-boundary rules from WP-05.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services import tradevisor_learning as learning

DECIDED = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
SHA_A = "a" * 40
SHA_B = "b" * 40


@dataclass
class Candle:
    open_time: datetime
    high: float
    low: float


def record(*, decision: str = "ENTER_NOW", dedup_id: str = "dedup-1") -> dict:
    return {
        "dedup_id": dedup_id,
        "decision": decision,
        "signal": {"market": "BTCUSD", "bar_closed_at": DECIDED.isoformat()},
        "decision_record": {
            "schema_version": "tradevisor-decision-v1",
            "decision": decision,
            "rejection_code": None if decision == "ENTER_NOW" else "ECONOMIC_REJECT",
            "dedup_id": dedup_id,
            "market": "BTCUSD",
            "instrument": "BTC/USD",
            "horizon": "SWING",
            "market_regime": "BULLISH",
            "evaluated_at": DECIDED.isoformat(),
            "plan": {
                "entry_low": 99.0,
                "entry_high": 100.0,
                "stop_price": 96.5,
                "target_1": 105.5,
                "target_2": 108.5,
            },
            "allocation": {"recommended_capital": 1_000.0, "risk_dollars": 35.0},
            "economics": {
                "recommended_capital": 2_000.0,
                "target_1_net_profit": 100.0,
                "target_2_net_profit": 160.0,
            },
        },
    }


def candles(*bars):
    return list(bars)


def test_release_compatibility_mirrors_the_learning_job_contract():
    assert learning.classify_release_compatibility(SHA_A, SHA_A) == learning.RELEASE_CURRENT
    assert learning.classify_release_compatibility(SHA_A, SHA_B) == learning.RELEASE_DRIFT
    assert learning.classify_release_compatibility("", SHA_A) == learning.RELEASE_UNVERIFIED
    assert learning.classify_release_compatibility("nope", "nope") == learning.RELEASE_UNVERIFIED


def test_release_classification_is_the_learning_plane_owner_not_a_copy():
    from app.opip.learning import job_disposition

    # Identity, not equality: a copy could drift from the shell/Python gate.
    assert (
        learning.classify_release_compatibility
        is job_disposition.classify_release_compatibility
    )
    assert learning.RELEASE_CURRENT == job_disposition.RELEASE_CURRENT
    assert learning.RELEASE_DRIFT == job_disposition.RELEASE_DRIFT


def test_atdd44_release_drift_fails_closed_and_publishes_no_metrics():
    report = learning.build_learning_report(
        [record()], deployed_sha=SHA_A, expected_sha=SHA_B
    )
    assert report.status == learning.RELEASE_DRIFT
    assert report.cohorts == {}
    assert report.incremental_value["status"] == "WITHHELD_RELEASE_DRIFT"
    assert report.as_dict()["policy_promotion"] == "NONE"


def test_unverified_release_also_fails_closed():
    report = learning.build_learning_report([record()], deployed_sha="", expected_sha=SHA_A)
    assert report.status == learning.RELEASE_UNVERIFIED


def test_atdd10_shadow_observation_is_measurable_without_live_authority():
    report = learning.build_learning_report(
        [record(decision="WATCH", dedup_id="shadow-1")],
        deployed_sha=SHA_A,
        expected_sha=SHA_A,
    )
    cohort = report.cohorts[learning.COHORT_SHADOW]
    assert cohort["observed"] == 1
    assert cohort["actionable"] == 0
    assert cohort["rejected"] == 1
    assert cohort["rejection_reasons"] == {"ECONOMIC_REJECT": 1}
    payload = report.as_dict()
    assert payload["trade_authority_changed"] is False
    assert payload["policy_promotion"] == "NONE"


def test_cohorts_separate_corroborated_from_shadow():
    report = learning.build_learning_report(
        [record(), record(decision="WATCH", dedup_id="shadow-2")],
        deployed_sha=SHA_A,
        expected_sha=SHA_A,
    )
    assert report.cohorts[learning.COHORT_CORROBORATED]["actionable"] == 1
    assert report.cohorts[learning.COHORT_SHADOW]["actionable"] == 0


def test_outcome_window_must_elapse_before_measurement():
    outcome = learning.evaluate_outcome(
        record(),
        now=DECIDED + timedelta(hours=1),
        candles_provider=lambda *_: candles(Candle(DECIDED, 110.0, 99.0)),
    )
    assert outcome["status"] == learning.OUTCOME_WINDOW_INCOMPLETE


def test_outcome_without_a_provider_is_pending_not_a_guess():
    outcome = learning.evaluate_outcome(record(), now=DECIDED + timedelta(days=3))
    assert outcome["status"] == learning.OUTCOME_PENDING
    assert "provider" in outcome["reason"]


def test_non_actionable_records_are_not_measured_as_trades():
    outcome = learning.evaluate_outcome(
        record(decision="WATCH"),
        now=DECIDED + timedelta(days=3),
        candles_provider=lambda *_: candles(),
    )


def test_outcome_measures_forward_path_only():
    # The bar before the decision reached Target 2; it must not count.
    before = Candle(DECIDED - timedelta(hours=1), 200.0, 150.0)
    entry = Candle(DECIDED + timedelta(hours=1), 101.0, 99.5)
    to_target = Candle(DECIDED + timedelta(hours=2), 106.0, 100.5)
    outcome = learning.evaluate_outcome(
        record(),
        now=DECIDED + timedelta(days=2),
        candles_provider=lambda *_: candles(before, entry, to_target),
    )
    assert outcome["status"] == learning.OUTCOME_MEASURED
    assert outcome["target_1_touched"] is True
    assert outcome["target_2_touched"] is False
    assert outcome["mfe_pct"] < 10.0  # 200 must have been excluded
    assert outcome["time_to_target_1_seconds"] == 7200
    assert outcome["false_positive"] is False
    assert outcome["fill_attribution"] == learning.FILL_ATTRIBUTION_NONE
    assert outcome["basis"] == learning.EXPECTANCY_BASIS


def test_outcome_marks_invalidation_before_target_as_false_positive():
    drop = Candle(DECIDED + timedelta(hours=1), 100.5, 96.0)
    outcome = learning.evaluate_outcome(
        record(),
        now=DECIDED + timedelta(days=2),
        candles_provider=lambda *_: candles(drop),
    )
    assert outcome["status"] == learning.OUTCOME_MEASURED
    assert outcome["stop_touched"] is True
    assert outcome["false_positive"] is True
    assert "stop zone traded before Target 1" in outcome["false_positive_definition"]


def test_outcome_without_an_entry_touch_is_not_counted_as_a_trade():
    never = Candle(DECIDED + timedelta(hours=1), 120.0, 101.0)
    outcome = learning.evaluate_outcome(
        record(),
        now=DECIDED + timedelta(days=2),
        candles_provider=lambda *_: candles(never),
    )
    assert outcome["status"] == learning.OUTCOME_NO_ENTRY_TOUCH


def test_outcome_failure_to_read_candles_never_fabricates_a_result():
    def boom(*_):
        raise RuntimeError("candles unavailable")

    outcome = learning.evaluate_outcome(record(), now=DECIDED + timedelta(days=2), candles_provider=boom)
    assert outcome["status"] == learning.OUTCOME_PENDING
    assert "RuntimeError" in outcome["reason"]


def test_cohort_summary_reports_rates_and_breakdowns():
    measured = {
        "dedup-1": {
            "status": learning.OUTCOME_MEASURED,
            "mfe_pct": 6.0,
            "mae_pct": -1.0,
            "time_to_target_1_seconds": 7200,
            "time_to_invalidation_seconds": None,
            "capital_occupancy_usd_seconds": 86_400_000.0,
            "false_positive": True,
            "target_1_touched": False,
        }
    }
    cohorts = learning.summarize_cohorts([record()], outcomes=measured)
    cohort = cohorts[learning.COHORT_CORROBORATED]
    assert cohort["false_positive_rate"] == 1.0
    assert cohort["mean_mfe_pct"] == 6.0
    assert cohort["mean_time_to_target_1_seconds"] == 7200
    assert cohort["estimated_net_basis"] == learning.EXPECTANCY_BASIS
    # A false positive books the planned loss, not a fabricated win.
    assert cohort["estimated_net_usd"] == -35.0
    assert cohort["by_instrument"]["BTC/USD"]["observed"] == 1
    assert cohort["by_regime"]["BULLISH"]["false_positives"] == 1


def test_incremental_value_is_withheld_without_native_metrics():
    report = learning.build_learning_report(
        [record()], deployed_sha=SHA_A, expected_sha=SHA_A
    )
    assert report.incremental_value["status"] == "NATIVE_COHORT_NOT_AVAILABLE"


def test_incremental_value_compares_when_both_sides_exist():
    measured = {
        "dedup-1": {
            "status": learning.OUTCOME_MEASURED,
            "mfe_pct": 6.0,
            "mae_pct": -1.0,
            "time_to_target_1_seconds": 7200,
            "time_to_invalidation_seconds": None,
            "capital_occupancy_usd_seconds": 0.0,
            "false_positive": False,
            "target_1_touched": True,
        }
    }
    report = learning.build_learning_report(
        [record()],
        deployed_sha=SHA_A,
        expected_sha=SHA_A,
        outcomes=measured,
        native_metrics={"estimated_net_usd": 50.0, "false_positive_rate": 0.5},
    )
    assert report.incremental_value["status"] == "COMPARABLE"
    assert report.incremental_value["tradevisor_corroborated_net_usd"] == 50.0
    assert "never promotes policy" in report.incremental_value["note"]
