from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_analytics_workflow_is_owner_gated_and_non_collapsible():
    workflow = (ROOT.parent / ".github/workflows/deploy-analytics.yml").read_text()
    assert "github.event.comment.user.login == github.repository_owner" in workflow
    assert "github.event.comment.author_association == 'OWNER'" in workflow
    assert "Require target to equal current main" in workflow
    assert "Require successful exact-SHA CI" in workflow
    assert "prepare|empty|backup|restore-drill|backfill|shipper|reads-ready" in workflow
    assert "does not merge, trade, or enable production reads" in workflow


def test_remote_runner_keeps_existing_platform_evidence_gates():
    runner = (ROOT / "deploy/analytics/run-gated-stage.sh").read_text()
    bootstrap = (ROOT / "deploy/analytics/bootstrap-opip-data-platform.sh").read_text()
    assert "OPIP_EMPTY_ROLLBACK_VERIFIED_AT_UTC" not in runner
    assert "OPIP_OFFHOST_BACKUP_VERIFIED_AT_UTC" not in runner
    assert "OPIP_RESTORE_DRILL_VERIFIED_AT_UTC" not in runner
    assert 'backfill|shipper|reads-ready)' in runner
    assert 'bootstrap-opip-data-platform.sh\" \"$TARGET_SHA\" \"$STAGE\"' in runner
    assert "7 * 86400" in bootstrap
    assert "health --require-ready" in bootstrap
