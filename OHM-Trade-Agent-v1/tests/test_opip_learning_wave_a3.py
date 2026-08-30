"""Sequence 5 Wave A3 paper and ML data-readiness tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.opip.learning.paper_readiness import (
    PaperReadinessState,
    assess_paper_learning_readiness,
)
from app.jobs.run_opip_ml_data_readiness import _paper_rows
from app.opip.learning.readiness import (
    MLReadinessPolicy,
    MLReadinessState,
    build_ml_data_readiness_report,
)


NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _canonical(snapshot_id: str, episode_id: str, status: str = "SCORED_ELIGIBLE"):
    """Return one production-shaped canonical evidence row."""
    return {
        "record_type": "CANONICAL_EPISODE_SNAPSHOT",
        "snapshot_id": snapshot_id,
        "episode_id": episode_id,
        "decision_status": status,
        "suppressed": status == "SCORED_SUPPRESSED",
    }


def _ml(
    snapshot_id: str,
    episode_id: str,
    *,
    ml_id: str | None = None,
    direction: str = "LONG",
    feature_value=1.0,
    visible_at=None,
):
    """Return one production-shaped ML FeatureSnapshot wrapper."""
    ml_id = ml_id or f"ML:{snapshot_id}"
    visible = visible_at or NOW
    return {
        "record_type": "OPIP_ML_FEATURE_SNAPSHOT",
        "canonical_snapshot_id": snapshot_id,
        "episode_id": episode_id,
        "ml_snapshot_id": ml_id,
        "feature_snapshot": {
            "snapshot_id": ml_id,
            "episode_id": episode_id,
            "decision_at_utc": NOW.isoformat(),
            "max_visible_at_utc": visible.isoformat(),
            "direction": direction,
            "lane": "PRODUCTION_SHADOW",
            "regime": "TREND",
            "canonical_asset_id": "bitcoin",
            "features": [
                {
                    "name": "momentum",
                    "value": feature_value,
                    "missing": feature_value is None,
                    "visible_at_utc": visible.isoformat(),
                }
            ],
        },
    }


def _phase(snapshot_id: str, episode_id: str):
    """Return one provisional Phase 3C outcome."""
    return {
        "snapshot_id": snapshot_id,
        "canonical_episode_id": episode_id,
        "outcome_record_id": f"OUT:{snapshot_id}",
        "outcome_revision": 1,
        "outcome_source": "PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS",
        "horizon_returns_pct": {"1h": 1.0, "4h": 2.0},
        "mfe_pct": 3.0,
        "mae_pct": -1.0,
        "window_complete": True,
    }


def _paper(trade_id: str, episode_id: str, *, direction: str = "LONG"):
    """Return one complete paper-only final lifecycle row."""
    return {
        "paper_trade_id": trade_id,
        "episode_id": episode_id,
        "revision": 2,
        "status": "CLOSED",
        "direction": direction,
        "paper_only": True,
        "exchange_write_authority": False,
        "closed_at": (NOW + timedelta(hours=1)).isoformat(),
        "exit_price": 101.0,
        "net_pnl": 1.0,
        "net_pnl_pct": 1.0,
        "outcome": "WIN",
        "mfe_pct": 2.0,
        "mae_pct": -0.5,
    }


def test_current_short_futures_paper_readiness_fails_closed():
    """Verify current LONG-only paper architecture never claims SHORT readiness."""
    report = assess_paper_learning_readiness(long_production_verified=False)
    assert report.long.state is PaperReadinessState.NOT_READY
    assert "LONG_PAPER_PRODUCTION_HEALTH_NOT_VERIFIED" in report.long.reasons
    assert report.short.state is PaperReadinessState.NOT_READY
    assert "PAPER_ENGINE_V1_LONG_ONLY" in report.short.reasons
    assert "FUNDING_MARGIN_LIQUIDATION_ACCOUNTING_UNVERIFIED" in report.short.reasons
    assert report.extended_short_learning_ready is False
    assert report.funded_execution_allowed is False


def test_verified_long_does_not_unlock_short_or_futures():
    """Verify a LONG health proof cannot imply SHORT/futures capability."""
    report = assess_paper_learning_readiness(long_production_verified=True)
    assert report.long.state is PaperReadinessState.READY
    assert report.short.state is PaperReadinessState.NOT_READY
    assert report.extended_short_learning_ready is False


def test_provisional_only_outcomes_are_not_ready_for_training():
    """Verify provisional labels never become final supervised truth."""
    report = build_ml_data_readiness_report(
        canonical_rows=[_canonical("S:1", "E:1")],
        ml_snapshot_rows=[_ml("S:1", "E:1")],
        phase3c_outcome_rows=[_phase("S:1", "E:1")],
        policy=MLReadinessPolicy(minimum_primary_supervised_rows=2),
    )
    assert report.readiness_state is MLReadinessState.NOT_READY
    assert report.feature_bearing_snapshots == 1
    assert report.provisional_only_linkage_count == 1
    assert report.final_supervised_truth_count == 0
    assert report.primary_supervised_usable_rows == 0
    assert "NO_FINAL_SUPERVISED_TRUTH" in report.blockers
    assert "PROVISIONAL_OUTCOMES_ONLY" in report.blockers


def test_zero_feature_bearing_snapshots_fail_closed():
    """Verify empty feature vectors are structurally NOT_READY."""
    row = _ml("S:1", "E:1")
    row["feature_snapshot"]["features"] = []
    report = build_ml_data_readiness_report(
        canonical_rows=[_canonical("S:1", "E:1")],
        ml_snapshot_rows=[row],
        phase3c_outcome_rows=[_phase("S:1", "E:1")],
        policy=MLReadinessPolicy(minimum_primary_supervised_rows=2),
    )
    assert report.readiness_state is MLReadinessState.NOT_READY
    assert report.feature_bearing_snapshots == 0
    assert "NO_FEATURE_BEARING_SNAPSHOTS" in report.blockers


def test_direction_none_binds_to_exact_final_paper_but_stays_support_gated():
    """Verify production direction NONE can bind only through exact final paper evidence."""
    report = build_ml_data_readiness_report(
        canonical_rows=[_canonical("S:1", "E:1")],
        ml_snapshot_rows=[_ml("S:1", "E:1", direction="NONE")],
        paper_trade_rows=[_paper("P:1", "E:1", direction="LONG")],
        policy=MLReadinessPolicy(minimum_primary_supervised_rows=2),
    )
    assert report.readiness_state is MLReadinessState.COLLECT_MORE_DATA
    assert "INSUFFICIENT_PRIMARY_SUPERVISED_SUPPORT" in report.blockers
    assert report.primary_supervised_usable_rows == 1
    assert report.direction_coverage.get("NONE") == 1


def test_feature_visibility_after_decision_is_pit_violation():
    """Verify late features are excluded and block readiness."""
    late = NOW + timedelta(seconds=1)
    report = build_ml_data_readiness_report(
        canonical_rows=[_canonical("S:1", "E:1")],
        ml_snapshot_rows=[_ml("S:1", "E:1", visible_at=late)],
        phase3c_outcome_rows=[_phase("S:1", "E:1")],
        policy=MLReadinessPolicy(minimum_primary_supervised_rows=2),
    )
    assert report.pit_violations == 1
    assert report.readiness_state is MLReadinessState.NOT_READY
    assert "PIT_VIOLATIONS_PRESENT" in report.blockers


def test_duplicate_identities_block_readiness():
    """Verify duplicate canonical/ML identities fail closed."""
    canonical = _canonical("S:1", "E:1")
    ml = _ml("S:1", "E:1")
    report = build_ml_data_readiness_report(
        canonical_rows=[canonical, dict(canonical)],
        ml_snapshot_rows=[ml, dict(ml)],
        phase3c_outcome_rows=[_phase("S:1", "E:1")],
        policy=MLReadinessPolicy(minimum_primary_supervised_rows=2),
    )
    assert report.duplicate_identities > 0
    assert report.readiness_state is MLReadinessState.NOT_READY
    assert "DUPLICATE_IDENTITIES_PRESENT" in report.blockers


def test_missingness_above_policy_blocks_readiness():
    """Verify excessive feature missingness is structural NOT_READY."""
    report = build_ml_data_readiness_report(
        canonical_rows=[_canonical("S:1", "E:1")],
        ml_snapshot_rows=[_ml("S:1", "E:1", feature_value=None)],
        phase3c_outcome_rows=[_phase("S:1", "E:1")],
        policy=MLReadinessPolicy(
            minimum_primary_supervised_rows=2,
            maximum_missing_feature_rate=0.0,
        ),
    )
    assert report.missing_feature_values == 1
    assert report.overall_missing_feature_rate == 1.0
    assert report.readiness_state is MLReadinessState.NOT_READY
    assert "FEATURE_MISSINGNESS_ABOVE_POLICY" in report.blockers


def test_clean_final_paper_rows_can_reach_ready_with_declared_support():
    """Verify clean final paper evidence can pass an intentionally small test policy."""
    canonical = [
        _canonical("S:1", "E:1"),
        _canonical("S:2", "E:2"),
    ]
    ml = [
        _ml("S:1", "E:1"),
        _ml("S:2", "E:2", ml_id="ML:S:2"),
    ]
    paper = [
        _paper("P:1", "E:1"),
        _paper("P:2", "E:2"),
    ]
    report = build_ml_data_readiness_report(
        canonical_rows=canonical,
        ml_snapshot_rows=ml,
        paper_trade_rows=paper,
        policy=MLReadinessPolicy(
            minimum_primary_supervised_rows=2,
            minimum_exact_linkage_rate=1.0,
            maximum_missing_feature_rate=0.0,
        ),
    )
    assert report.readiness_state is MLReadinessState.READY_FOR_OFFLINE_TRAINING
    assert report.final_supervised_truth_count == 2
    assert report.primary_supervised_usable_rows == 2
    assert report.exact_outcome_linkage_rate == 1.0
    assert report.blockers == ()


def test_support_shortfall_collects_more_data_not_structural_failure():
    """Verify a clean but undersized population returns COLLECT_MORE_DATA."""
    report = build_ml_data_readiness_report(
        canonical_rows=[_canonical("S:1", "E:1")],
        ml_snapshot_rows=[_ml("S:1", "E:1")],
        paper_trade_rows=[_paper("P:1", "E:1")],
        policy=MLReadinessPolicy(
            minimum_primary_supervised_rows=2,
            minimum_exact_linkage_rate=1.0,
        ),
    )
    assert report.readiness_state is MLReadinessState.COLLECT_MORE_DATA
    assert "INSUFFICIENT_PRIMARY_SUPERVISED_SUPPORT" in report.blockers


def test_readiness_paper_source_corruption_is_counted_without_mutation(tmp_path):
    """Verify the readiness reader never quarantines or rewrites paper state."""
    path = tmp_path / "state.json"
    original = '{"lifecycles": {"broken": NaN}}'
    path.write_text(original, encoding="utf-8")
    rows, malformed = _paper_rows(path)
    assert rows == []
    assert malformed == 1
    assert path.exists()
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [path]
