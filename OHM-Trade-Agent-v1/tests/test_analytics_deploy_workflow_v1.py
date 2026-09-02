from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_analytics_workflow_is_owner_gated_and_non_collapsible():
    """The workflow must accept only owner-approved, exact-main rollout stages."""
    workflow = (ROOT.parent / ".github/workflows/deploy-analytics.yml").read_text()
    assert "github.event.comment.user.login == github.repository_owner" in workflow
    assert "github.event.comment.author_association == 'OWNER'" in workflow
    assert "Require target to equal current main" in workflow
    assert "Require successful exact-SHA CI" in workflow
    assert (
        "prepare|activate|empty|backup|restore-drill|offhost-verified|"
        "rollback-verified|backfill|shipper|reads-ready"
    ) in workflow
    assert "does not merge, trade, or enable production reads" in workflow
    assert "Clean remote release and sealed environment" in workflow
    assert "if: always() && steps.target.outputs.sha != ''" in workflow


def test_remote_runner_records_real_local_evidence_without_mutable_secret_timestamps():
    """Promotion evidence must be durable, auditable, and independent of sealed credentials."""
    runner = (ROOT / "deploy/analytics/run-gated-stage.sh").read_text()
    bootstrap = (ROOT / "deploy/analytics/bootstrap-opip-data-platform.sh").read_text()

    assert "offhost-verified" in runner
    assert "rollback-verified" in runner
    assert "offhost-backup.env" in runner
    assert "empty-rollback.env" in runner
    assert "EMPTY_LAST_COMPLETED_AT_UTC" in bootstrap
    assert "last-restore-drill.env" in bootstrap
    assert "offhost-backup.env" in bootstrap
    assert "empty-rollback.env" in bootstrap

    assert "OPIP_OFFHOST_BACKUP_VERIFIED_AT_UTC" not in bootstrap
    assert "OPIP_RESTORE_DRILL_VERIFIED_AT_UTC" not in bootstrap
    assert "OPIP_EMPTY_ROLLBACK_VERIFIED_AT_UTC" not in bootstrap

    assert 'backfill|shipper|reads-ready)' in runner
    assert 'bash "$APP_ROOT/deploy/analytics/opip-postgres-backup.sh"' in runner
    assert 'bash "$APP_ROOT/deploy/analytics/opip-postgres-restore-drill.sh"' in runner
    assert 'print "OPIP_DEPLOYED_SHA=" sha' in runner
    assert 'mv -f -- "$normalized" "$ENV_FILE"' in runner

    assert runner.index("prepare)") < runner.index("activate)")
    assert 'systemctl disable --now "$unit"' in runner
    assert '! systemctl is-active --quiet "$unit"' in runner
    assert '! systemctl is-enabled --quiet "$unit"' in runner

    assert "7 * 86400" in bootstrap
    assert "health --require-ready" in bootstrap
    assert "restore drill must validate the attested PostgreSQL dump" in bootstrap
