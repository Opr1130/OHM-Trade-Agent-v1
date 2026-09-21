from pathlib import Path
import subprocess


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
        "rollback-verified|backfill|shipper|reads-ready|cockpit-ready"
    ) in workflow
    assert "does not merge, trade, or enable production reads" in workflow
    assert "Clean remote release and sealed environment" in workflow
    assert "sudo -n install -o root -g root -m 0600 /dev/stdin" in workflow
    assert "scp -i ~/.ssh/opip_analytics" not in workflow
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

    assert 'cockpit-ready)' in runner
    assert 'backfill|shipper|reads-ready)' in runner
    assert 'bash "$APP_ROOT/deploy/analytics/opip-postgres-backup.sh"' in runner
    assert 'bash "$APP_ROOT/deploy/analytics/opip-postgres-restore-drill.sh"' in runner
    assert 'print "OPIP_DEPLOYED_SHA=" sha' in runner
    assert 'mv -f -- "$normalized" "$ENV_FILE"' in runner

    assert runner.index("prepare)") < runner.index("activate)")
    assert 'systemctl disable --now "$unit"' in runner
    assert '! systemctl is-active --quiet "$unit"' in runner
    assert '! systemctl is-enabled --quiet "$unit"' in runner
    assert 'start_learning_unit opip-learning-outcomes.service' in runner
    assert 'systemctl status "$unit" --no-pager --full' in runner
    assert 'journalctl -u "$unit" -n 160 --no-pager' in runner

    assert "7 * 86400" in bootstrap
    assert "health --require-ready" in bootstrap
    assert "restore drill must validate the attested PostgreSQL dump" in bootstrap


def test_cockpit_ready_receives_sealed_env_without_running_empty_stage():
    """Cockpit secret provisioning is narrow and does not reuse the PostgreSQL empty stage."""
    workflow = (ROOT.parent / ".github/workflows/deploy-analytics.yml").read_text()
    runner = (ROOT / "deploy/analytics/run-gated-stage.sh").read_text()

    assert workflow.count('[[ "$STAGE" == "empty" || "$STAGE" == "cockpit-ready" ]]') >= 2
    assert "COCKPIT_SECRET: ${{ secrets.OPIP_COCKPIT_SECRET }}" in workflow
    assert 'if [[ "$STAGE" == "empty" ]]; then' in workflow
    assert 'elif [[ "$STAGE" == "cockpit-ready" ]]; then' in workflow
    assert "OPIP_COCKPIT_BIND_ADDRESS=127.0.0.1" in workflow
    assert "sync_cockpit_settings()" in runner
    assert "sealed analytics environment must contain exactly one canonical $key setting" in runner
    assert "OPIP_COCKPIT_SECRET must be a non-placeholder URL-safe secret of at least 24 characters" in runner
    assert 'mv -f -- "$temporary" "$ENV_FILE"' in runner
    assert "WEBHOOK_SECRET KRAKEN_API_KEY KRAKEN_API_SECRET TELEGRAM_BOT_TOKEN" in runner

    cockpit_case = runner[runner.index("  cockpit-ready)") : runner.index("  backfill|shipper|reads-ready)")]
    assert "sync_cockpit_settings" in cockpit_case
    assert "bootstrap-opip-data-platform.sh" in cockpit_case
    assert 'bootstrap-opip-data-platform.sh" "$TARGET_SHA" empty' not in cockpit_case
    assert "opip-postgres" not in cockpit_case
    assert "opip-grafana" not in cockpit_case


def test_cockpit_secret_sync_allowlist_is_exact():
    """Only Cockpit-owned settings may be merged into the installed analytics env."""
    runner = (ROOT / "deploy/analytics/run-gated-stage.sh").read_text()
    start = runner.index("sync_cockpit_settings()")
    end = runner.index("\n}\n", start) + 3
    sync = runner[start:end]

    for key in (
        "OPIP_COCKPIT_SECRET",
        "OPIP_COCKPIT_BIND_ADDRESS",
        "OPIP_COCKPIT_HOST_PORT",
        "OPIP_COCKPIT_HTTP_PORT",
    ):
        assert key in sync

    for forbidden in (
        "OPIP_POSTGRES_ADMIN_PASSWORD",
        "OPIP_SHIPPER_PASSWORD",
        "OPIP_GRAFANA_ADMIN_PASSWORD",
        "OPIP_ANALYTICS_DATABASE_URL",
    ):
        assert forbidden not in sync

    assert "canonical_count" in sync
    assert "^[A-Za-z0-9._~-]{24,}$" in sync
    assert '[[ "$bind_value" == "127.0.0.1" ]]' in sync
    assert "Cockpit ports must be decimal values from 1 through 65535" in sync
    assert "grep -E \"^${key}=\"" not in sync
    assert r"printf 'OPIP_COCKPIT_SECRET=%s\n'" in sync
    assert r"printf 'OPIP_COCKPIT_SECRET=%s\\n'" not in sync


def test_cockpit_secret_sync_writes_four_physical_env_records(tmp_path):
    """The sync append block must serialize one Cockpit setting per physical line."""
    runner = (ROOT / "deploy/analytics/run-gated-stage.sh").read_text()
    start = runner.index("sync_cockpit_settings()")
    end = runner.index("\n}\n", start) + 3
    sync = runner[start:end]
    printf_lines = [
        line.strip()
        for line in sync.splitlines()
        if line.strip().startswith("printf 'OPIP_COCKPIT_")
    ]
    assert len(printf_lines) == 4

    target = tmp_path / "cockpit.env"
    script = "\n".join(
        [
            "set -euo pipefail",
            "cockpit_secret_value=abcdefghijklmnopqrstuvwxyz012345",
            "bind_value=127.0.0.1",
            "host_port_value=8000",
            "http_port_value=8000",
            "{",
            *[f"  {line}" for line in printf_lines],
            f"}} > {str(target)!r}",
        ]
    )
    subprocess.run(["bash", "-c", script], check=True)
    assert target.read_text().splitlines() == [
        "OPIP_COCKPIT_SECRET=abcdefghijklmnopqrstuvwxyz012345",
        "OPIP_COCKPIT_BIND_ADDRESS=127.0.0.1",
        "OPIP_COCKPIT_HOST_PORT=8000",
        "OPIP_COCKPIT_HTTP_PORT=8000",
    ]
