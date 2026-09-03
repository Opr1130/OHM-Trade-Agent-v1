import ast
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LEARNING = ROOT / "deploy" / "learning"


def _module_path(module: str) -> Path | None:
    candidate = ROOT / Path(*module.split("."))
    file_path = candidate.with_suffix(".py")
    if file_path.is_file():
        return file_path
    package_path = candidate / "__init__.py"
    if package_path.is_file():
        return package_path
    return None


def _first_party_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                alias.name for alias in node.names if alias.name.startswith("app.")
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "app" or node.module.startswith("app."):
                names.add(node.module)
    return names


def test_unified_cycle_does_not_import_or_run_ml_capture():
    seen: set[str] = set()
    pending = ["app.jobs.run_cycle"]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_path(module)
        if path is None:
            continue
        pending.extend(_first_party_imports(path) - seen)

    assert "app.services.opip_ml_evidence_capture" not in seen
    assert "app.jobs.run_opip_ml_capture" not in seen


def test_production_ml_cron_is_decommissioned():
    cron = (ROOT / "deploy" / "cron.d" / "opip-ml-evidence").read_text(
        encoding="utf-8"
    )
    executable = [
        line
        for line in cron.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and "=" not in line.split(maxsplit=1)[0]
    ]
    assert executable == []
    assert "DECOMMISSIONED ON PRODUCTION" in cron


def test_production_scheduler_removes_local_learning_compute():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    assert 'ML_EVIDENCE_DST="/etc/cron.d/opip-ml-evidence"' in source
    assert 'LEARNING_EXPORT_DST="/etc/cron.d/opip-learning-export"' in source
    assert 'rm -f "$ML_EVIDENCE_DST"' in source
    assert 'install -o root -g root -m 0644 "$LEARNING_EXPORT_SRC" "$LEARNING_EXPORT_DST"' in source
    assert "run_opip_ml_capture" not in (
        ROOT / "deploy" / "cron.d" / "opip-learning-export"
    ).read_text(encoding="utf-8")
    assert "build_phase3c_forward_outcomes" not in (
        ROOT / "deploy" / "cron.d" / "opip-learning-export"
    ).read_text(encoding="utf-8")
    assert "learning_compute=REMOTE_ONLY" in source
    assert "local_ml_evidence_cron=ABSENT" in source


def test_production_export_is_copy_only_and_locked():
    runner = ROOT / "deploy" / "remote" / "export-opip-learning-evidence.sh"
    source = runner.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(runner)], check=True)
    assert "p1_shadow_outbox.jsonl" in source
    assert "full_market_observations.jsonl" in source
    assert "flock -x 8" in source
    assert "sha256sum" in source
    assert "schema_version=3" in source
    assert 'archive_temp="$EXPORT_ROOT/.$archive_name.tmp.$$"' in source
    assert source.count('install -d -o root -g "$READER_GROUP" -m 0750') >= 2
    assert source.count("install -d -o root -g root -m 0700") >= 2
    assert not re.search(
        r"install\\s+-d\\s+-o\\s+root\\s+-g\\s+root\\s+-m\\s+0750\\b",
        source,
    )
    assert "mv -f" in source
    assert "python" not in source
    assert "docker" not in source
    assert "kraken" not in source.lower()


def test_production_deploy_has_no_learning_compute_probe():
    source = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    assert "run-opip-background-job.sh" not in source
    assert "app.jobs.run_opip_ml_capture" not in source
    assert "build_phase3c_forward_outcomes" not in source
    assert "Learning computation is remote-only" in source


def test_learning_job_runner_has_clean_entry_and_clean_exit():
    runner = LEARNING / "opip-learning-job.sh"
    source = runner.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(runner)], check=True)
    assert 'LOCK_FILE="/var/lock/opip-learning-plane.lock"' in source
    assert 'docker ps -aq --filter "label=$LABEL"' in source
    assert 'docker rm -f "${stale_ids[@]}"' in source
    assert "timeout --signal=TERM --kill-after=20s" in source
    assert "docker run --rm" in source
    assert "--network none" in source
    assert "--memory \"$MEMORY_LIMIT\"" in source
    assert "--memory-swap \"$MEMORY_LIMIT\"" in source
    assert "--pids-limit 128" in source
    assert "--cap-drop ALL" in source
    assert "trap cleanup EXIT INT TERM" in source
    assert 'docker rm -f "${remaining_ids[@]}"' in source
    assert source.count('MEMORY_LIMIT="384m"') == 2
    assert 'MODULE="app.jobs.run_opportunity_intelligence_cycle"' in source
    assert "app.jobs.build_phase3c_forward_outcomes" not in source
    assert 'MEMORY_LIMIT="512m"' not in source
    assert source.count('MIN_AVAILABLE_KB=$((512 * 1024))') == 2


def test_opportunity_cycle_acks_only_after_accountability_build():
    source = (
        ROOT / "app" / "jobs" / "run_opportunity_intelligence_cycle.py"
    ).read_text(encoding="utf-8")
    pending = source.index("pending_accountability_outcomes()")
    build = source.index("build_incremental_from_outcomes(outcomes)")
    ack = source.index("acknowledge_accountability_outcomes(outcomes)")
    assert pending < build < ack
    assert "if not outcomes:" in source


def test_learning_systemd_services_enforce_post_run_cleanup():
    capture = (LEARNING / "opip-learning-capture.service").read_text(
        encoding="utf-8"
    )
    outcomes = (LEARNING / "opip-learning-outcomes.service").read_text(
        encoding="utf-8"
    )
    for source, job in ((capture, "capture"), (outcomes, "outcomes")):
        assert "Type=oneshot" in source
        assert "KillMode=control-group" in source
        assert "TimeoutStartSec=" in source
        assert "RuntimeMaxSec=" not in source
        assert f"ExecStart=/usr/local/sbin/opip-learning-job {job}" in source
        assert f"ExecStopPost=/usr/local/sbin/opip-learning-cleanup {job}" in source


def test_learning_timers_are_staggered():
    sync = (LEARNING / "opip-learning-sync.timer").read_text(encoding="utf-8")
    capture = (LEARNING / "opip-learning-capture.timer").read_text(
        encoding="utf-8"
    )
    outcomes = (LEARNING / "opip-learning-outcomes.timer").read_text(
        encoding="utf-8"
    )
    assert "OnCalendar=*-*-* *:0/2:20" in sync
    assert "OnCalendar=*-*-* *:0/5:50" in capture
    assert "OnCalendar=*-*-* *:3/10:30" in outcomes
    for source in (sync, capture, outcomes):
        assert "Persistent=true" in source
        assert "RandomizedDelaySec=5s" in source


def test_learning_sync_and_cleanup_shell_validate():
    for script in (
        LEARNING / "opip-learning-sync.sh",
        LEARNING / "opip-learning-cleanup.sh",
        LEARNING / "bootstrap-opip-learning-worker.sh",
        ROOT / "deploy" / "remote" / "configure-opip-learning-reader.sh",
        ROOT / "deploy" / "remote" / "opip-learning-read-export.sh",
    ):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_learning_worker_deploy_is_exact_sha_and_no_trading_credentials():
    bootstrap = (LEARNING / "bootstrap-opip-learning-worker.sh").read_text(
        encoding="utf-8"
    )
    runner = (LEARNING / "opip-learning-job.sh").read_text(encoding="utf-8")
    assert 'REMOTE_MAIN="$(git -C "$REPO_ROOT" rev-parse origin/main)"' in bootstrap
    assert 'if [[ "$REMOTE_MAIN" != "$TARGET_SHA" ]]' in bootstrap
    assert 'OPIP_LEARNING_IMAGE=$IMAGE' in bootstrap
    assert "KRAKEN" not in runner
    assert '--env-file' not in runner
    assert '$APP_ROOT/.env' not in runner
    assert "P1_SHADOW_OUTBOX_ENABLED=true" in runner


def test_deploy_reconciles_paper_topology_before_marking_last_good():
    source = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    assert 'PAPER_COMPOSE="$APP_ROOT/docker-compose.paper.yml"' in source
    assert "start_paper_stack" in source
    assert "wait_paper_health" in source
    assert "freqtrade_dry_run_status" in source
    assert "exchange_write_authority" in source
    assert "snapshot_scheduler_state" in source
    assert "restore_scheduler_state" in source
    assert source.rfind("wait_paper_health") < source.rfind('> "$LAST_GOOD_FILE"')


def test_deploy_stops_paper_before_target_build():
    source = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    marker = "# Stop paper workers during the build/recreate window."
    section = source[source.index(marker):]
    assert section.index("stop_paper_stack") < section.index(
        "docker compose build ohm-trade-agent"
    )



def test_learning_reader_key_is_forced_read_only_and_source_bound():
    configure = (
        ROOT / "deploy" / "remote" / "configure-opip-learning-reader.sh"
    ).read_text(encoding="utf-8")
    reader = (
        ROOT / "deploy" / "remote" / "opip-learning-read-export.sh"
    ).read_text(encoding="utf-8")
    sync = (LEARNING / "opip-learning-sync.sh").read_text(encoding="utf-8")

    assert 'from="%s",restrict,command="%s" %s' in configure
    assert 'SOURCE_CIDR="${2:-}"' in configure
    assert 'READER_STATE_ROOT="/var/lib/opip-learning-reader"' in configure
    assert 'install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$READER_STATE_ROOT"' in configure
    assert 'ORIGINAL="${SSH_ORIGINAL_COMMAND:-}"' in reader
    assert 'if [[ "$ORIGINAL" == "opip-export-v1" ]]' in reader
    assert "opip-export-v2" in reader
    assert "command rejected" in reader
    assert "tar -C" in reader
    assert "flock -s 8" in reader
    assert "last_sync_request.env" in reader
    assert "last_successful_sync_at_utc" in reader
    assert "sync_success_at=" in reader
    assert "opip-export-v2 sha=" in sync
    assert "sync_success_at=" in sync
    assert "last_sync_at_utc" in sync
    assert "capture_at=" in sync
    assert "outcomes_at=" in sync
    assert "rsync" not in sync


def test_learning_sync_validates_manifest_before_promotion():
    sync = (LEARNING / "opip-learning-sync.sh").read_text(encoding="utf-8")
    assert 'schema="$(manifest_value schema_version)"' in sync
    assert '[[ "$schema" == "3" ]]' in sync
    assert "sha256sum" in sync
    assert "size mismatch" in sync
    assert "checksum mismatch" in sync
    validation = sync.index('validate_artifact "p1_shadow_outbox.jsonl"')
    promotion = sync.index("for name in \\")
    assert validation < promotion


def test_learning_bootstrap_keeps_timers_disabled_until_validation():
    bootstrap = (LEARNING / "bootstrap-opip-learning-worker.sh").read_text(
        encoding="utf-8"
    )
    assert "systemctl enable opip-learning-sync.timer" not in bootstrap
    assert "systemctl enable opip-learning-capture.timer" not in bootstrap
    assert "systemctl enable opip-learning-outcomes.timer" not in bootstrap
    assert "systemctl enable --now opip-learning-sync.timer" in bootstrap


def test_learning_cleanup_checks_all_container_states():
    cleanup = (LEARNING / "opip-learning-cleanup.sh").read_text(encoding="utf-8")
    assert 'docker ps -aq --filter "label=$LABEL"' in cleanup


def test_learning_capture_uses_bounded_outbox_cursor_and_disk_dedup():
    source = (
        ROOT / "app" / "services" / "p1_shadow_outbox.py"
    ).read_text(encoding="utf-8")
    assert "OUTBOX_CHECKPOINT_SCHEMA_VERSION = 2" in source
    assert "byte_offset" in source
    assert "anchor_sha256" in source
    assert "source_tail_sha256" in source
    assert "source_size" in source
    assert "CHECKPOINT_SOURCE_DIVERGED" in source
    assert "sqlite3.connect" in source
    assert "snapshot_ids" in source
    assert "LEDGER_INDEX_RECONCILE_MAX_ROWS" in source
    assert "LEDGER_INDEX_RECONCILE_MAX_BYTES" in source
    assert "index_catchup_in_progress" in source
    assert "_read_complete_outbox_lines" not in source
    assert "_ledger_snapshot_ids" not in source


def test_learning_outcomes_use_bounded_queue_and_filtered_observations():
    source = (
        ROOT / "app" / "jobs" / "build_phase3c_forward_outcomes.py"
    ).read_text(encoding="utf-8")
    assert "build_outcomes_bounded" in source
    assert "snapshot_queue" in source
    assert "latest_outcomes" in source
    assert "max_snapshots" in source
    assert "symbols=symbols" in source
    assert "start_at=min(decision_times)" in source
    assert "end_at=max(decision_times)" in source
