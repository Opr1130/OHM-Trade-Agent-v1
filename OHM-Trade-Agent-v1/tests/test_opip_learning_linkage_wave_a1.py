"""Adversarial tests for O'Pip Sequence 5 Wave A1 evidence linkage."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.opip.learning.linkage import (
    LearningCohort,
    LinkageStatus,
    OutcomeSourceQuality,
    PROVISIONAL_PHASE3C_SOURCE,
    build_learning_linkage_records,
    normalize_paper_outcome,
    select_latest_phase3c_outcomes,
)


def _canonical(
    snapshot_id="SNAP:1",
    *,
    episode_id="EP:1",
    status="QUALIFIED",
    suppressed=False,
    counterfactual=False,
):
    """Return canonical."""
    return {
        "record_type": "CANONICAL_EPISODE_SNAPSHOT",
        "snapshot_id": snapshot_id,
        "episode_id": episode_id,
        "decision_status": status,
        "suppressed": suppressed,
        "counterfactual_eligible": counterfactual,
    }


def _ml(snapshot_id="SNAP:1", *, ml_id="MLSNAP:1", direction="LONG"):
    """Return ml."""
    return {
        "record_type": "OPIP_ML_FEATURE_SNAPSHOT",
        "canonical_snapshot_id": snapshot_id,
        "ml_snapshot_id": ml_id,
        "feature_snapshot": {
            "snapshot_id": ml_id,
            "direction": direction,
            "features": [{"name": "x", "value": 1.0}],
        },
    }


def _phase(snapshot_id="SNAP:1", *, revision=1, record_id=None):
    """Return phase."""
    return {
        "snapshot_id": snapshot_id,
        "canonical_episode_id": "EP:1",
        "outcome_source": PROVISIONAL_PHASE3C_SOURCE,
        "outcome_record_id": record_id or f"OUT:{revision}",
        "outcome_revision": revision,
        "window_complete": True,
        "horizon_returns_pct": {"1h": 2.0},
        "mfe_pct": 3.0,
        "mae_pct": -1.0,
    }


def _paper(
    *,
    trade_id="PT:1",
    episode_id="EP:1",
    revision=1,
    status="CLOSED",
    direction="LONG",
):
    """Return paper."""
    return {
        "paper_trade_id": trade_id,
        "episode_id": episode_id,
        "revision": revision,
        "status": status,
        "direction": direction,
        "paper_only": True,
        "exchange_write_authority": False,
        "closed_at": "2026-08-30T00:00:00+00:00" if status == "CLOSED" else None,
        "exit_price": 101.0 if status == "CLOSED" else None,
        "net_pnl": 5.0 if status == "CLOSED" else None,
        "net_pnl_pct": 1.5 if status == "CLOSED" else None,
        "outcome": "WIN" if status == "CLOSED" else None,
    }


def test_provisional_phase3c_never_becomes_primary_supervised_truth():
    """Verify provisional phase3c never becomes primary supervised truth."""
    row = build_learning_linkage_records(
        canonical_rows=[_canonical()],
        ml_snapshot_rows=[_ml()],
        phase3c_outcome_rows=[_phase()],
    )[0]
    assert row.linkage_status is LinkageStatus.COMPLETE_PROVISIONAL
    assert row.normalized_outcome.source_quality is OutcomeSourceQuality.PROVISIONAL_MARKET
    assert row.primary_supervised_eligible is False
    assert "OUTCOME_PROVISIONAL_NOT_SUPERVISED_TRUTH" in row.exclusion_reasons


def test_production_scored_eligible_maps_to_primary_paper_cohort():
    """Verify production scored eligible maps to primary paper cohort."""
    row = build_learning_linkage_records(
        canonical_rows=[_canonical(status="SCORED_ELIGIBLE")],
        ml_snapshot_rows=[_ml()],
        phase3c_outcome_rows=[_phase()],
        paper_trade_rows=[_paper()],
    )[0]
    assert row.cohort is LearningCohort.QUALIFIED_PAPER
    assert row.linkage_status is LinkageStatus.COMPLETE_FINAL
    assert row.normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert row.paper_trade_id == "PT:1"
    assert row.primary_supervised_eligible is True


def test_feature_direction_none_cannot_enter_primary_supervised_cohort():
    """Verify production direction NONE remains excluded from primary training."""
    row = build_learning_linkage_records(
        canonical_rows=[_canonical(status="SCORED_ELIGIBLE")],
        ml_snapshot_rows=[_ml(direction="NONE")],
        paper_trade_rows=[_paper(direction="LONG")],
    )[0]
    assert row.linkage_status is LinkageStatus.COMPLETE_FINAL
    assert row.primary_supervised_eligible is False
    assert "ML_DIRECTION_NOT_TRAINABLE" in row.exclusion_reasons


def test_feature_paper_direction_mismatch_cannot_train():
    """Verify explicit direction disagreement fails supervised eligibility."""
    row = build_learning_linkage_records(
        canonical_rows=[_canonical(status="SCORED_ELIGIBLE")],
        ml_snapshot_rows=[_ml(direction="SHORT")],
        paper_trade_rows=[_paper(direction="LONG")],
    )[0]
    assert row.primary_supervised_eligible is False
    assert "DIRECTION_LINK_MISMATCH" in row.exclusion_reasons


def test_production_scored_suppressed_is_counterfactual_research_only():
    """Verify production scored suppressed is counterfactual research only."""
    row = build_learning_linkage_records(
        canonical_rows=[_canonical(status="SCORED_SUPPRESSED", suppressed=True)],
        ml_snapshot_rows=[_ml()],
        paper_trade_rows=[_paper()],
    )[0]
    assert row.cohort is LearningCohort.COUNTERFACTUAL_REJECTED
    assert row.primary_supervised_eligible is False


def test_counterfactual_rejected_is_research_only_even_with_final_paper_row():
    """Verify counterfactual rejected is research only even with final paper row."""
    row = build_learning_linkage_records(
        canonical_rows=[
            _canonical(status="REJECTED", counterfactual=True)
        ],
        ml_snapshot_rows=[_ml()],
        paper_trade_rows=[_paper()],
    )[0]
    assert row.cohort is LearningCohort.COUNTERFACTUAL_REJECTED
    assert row.primary_supervised_eligible is False
    assert "COUNTERFACTUAL_RESEARCH_ONLY" in row.exclusion_reasons


def test_missing_feature_snapshot_fails_closed_without_symbol_time_fallback():
    """Verify missing feature snapshot fails closed without symbol time fallback."""
    row = build_learning_linkage_records(
        canonical_rows=[_canonical(snapshot_id="SNAP:canonical")],
        ml_snapshot_rows=[_ml(snapshot_id="SNAP:other")],
        phase3c_outcome_rows=[_phase(snapshot_id="SNAP:canonical")],
    )[0]
    assert row.cohort is LearningCohort.INELIGIBLE_UNLINKED
    assert row.linkage_status is LinkageStatus.MISSING_FEATURE_SNAPSHOT
    assert row.primary_supervised_eligible is False


def test_reused_episode_across_multiple_canonical_snapshots_is_not_exact_paper_link():
    """Verify reused episode across multiple canonical snapshots is not exact paper link."""
    rows = build_learning_linkage_records(
        canonical_rows=[
            _canonical(snapshot_id="SNAP:1", episode_id="EP:shared"),
            _canonical(snapshot_id="SNAP:2", episode_id="EP:shared"),
        ],
        ml_snapshot_rows=[
            _ml(snapshot_id="SNAP:1", ml_id="MLSNAP:1"),
            _ml(snapshot_id="SNAP:2", ml_id="MLSNAP:2"),
        ],
        paper_trade_rows=[_paper(episode_id="EP:shared")],
    )
    assert all(row.linkage_status is LinkageStatus.AMBIGUOUS_PAPER_LINK for row in rows)
    assert all(row.primary_supervised_eligible is False for row in rows)
    assert all(
        "AMBIGUOUS_CANONICAL_SNAPSHOT_FOR_PAPER_EPISODE" in row.exclusion_reasons
        for row in rows
    )


def test_multiple_paper_trades_for_same_episode_are_ambiguous_not_guessed():
    """Verify multiple paper trades for same episode are ambiguous not guessed."""
    row = build_learning_linkage_records(
        canonical_rows=[_canonical()],
        ml_snapshot_rows=[_ml()],
        paper_trade_rows=[
            _paper(trade_id="PT:1"),
            _paper(trade_id="PT:2"),
        ],
    )[0]
    assert row.linkage_status is LinkageStatus.AMBIGUOUS_PAPER_LINK
    assert row.normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert row.primary_supervised_eligible is False


def test_latest_phase3c_revision_is_selected_by_explicit_revision():
    """Verify latest phase3c revision is selected by explicit revision."""
    latest = select_latest_phase3c_outcomes(
        [_phase(revision=1), _phase(revision=3), _phase(revision=2)]
    )
    assert latest["SNAP:1"]["outcome_revision"] == 3
    assert latest["SNAP:1"]["outcome_record_id"] == "OUT:3"


def test_conflicting_same_revision_phase3c_records_fail_closed():
    """Verify conflicting same revision phase3c records fail closed."""
    with pytest.raises(ValueError, match="conflicting immutable revisions"):
        select_latest_phase3c_outcomes(
            [
                _phase(revision=2, record_id="OUT:a"),
                _phase(revision=2, record_id="OUT:b"),
            ]
        )


def test_duplicate_canonical_snapshot_identity_fails_closed():
    """Verify duplicate canonical snapshot identity fails closed."""
    with pytest.raises(ValueError, match="duplicate canonical snapshot identity"):
        build_learning_linkage_records(
            canonical_rows=[_canonical(), _canonical()],
            ml_snapshot_rows=[_ml()],
        )


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_nonfinite_final_paper_values_are_unusable(bad_value):
    """Verify nonfinite final paper values are unusable."""
    row = _paper()
    row["net_pnl"] = bad_value
    row["net_pnl_pct"] = bad_value
    normalized = normalize_paper_outcome(row)
    assert normalized.source_quality is OutcomeSourceQuality.UNUSABLE

    linkage = build_learning_linkage_records(
        canonical_rows=[_canonical(status="SCORED_ELIGIBLE")],
        ml_snapshot_rows=[_ml()],
        paper_trade_rows=[row],
    )[0]
    assert linkage.primary_supervised_eligible is False


@pytest.mark.parametrize("bad_flag", ["false", "true", 0, 1, None])
def test_malformed_paper_authority_flags_are_unusable(bad_flag):
    """Verify malformed paper authority flags are unusable."""
    row = _paper()
    row["paper_only"] = bad_flag
    normalized = normalize_paper_outcome(row)
    assert normalized.source_quality is OutcomeSourceQuality.UNUSABLE

    row = _paper()
    row["exchange_write_authority"] = bad_flag
    normalized = normalize_paper_outcome(row)
    assert normalized.source_quality is OutcomeSourceQuality.UNUSABLE


def test_nonclosed_or_authoritative_paper_record_is_unusable():
    """Verify nonclosed or authoritative paper record is unusable."""
    open_row = normalize_paper_outcome(_paper(status="OPEN"))
    assert open_row.source_quality is OutcomeSourceQuality.UNUSABLE

    row = _paper()
    row["exchange_write_authority"] = True
    unsafe = normalize_paper_outcome(row)
    assert unsafe.source_quality is OutcomeSourceQuality.UNUSABLE


def test_learning_package_has_no_execution_authority_imports():
    """Verify learning package has no execution authority imports."""
    root = Path(__file__).resolve().parents[1] / "app" / "opip" / "learning"
    forbidden = (
        "kraken_private",
        "order",
        "telegram",
        "execution",
        "position",
    )
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.lower() for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [str(node.module or "").lower()]
            else:
                continue
            for name in names:
                assert not any(fragment in name for fragment in forbidden), (
                    path,
                    name,
                )
