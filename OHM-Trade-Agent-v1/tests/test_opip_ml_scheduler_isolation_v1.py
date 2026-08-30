import ast
from pathlib import Path
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
    assert "flock -s" in source
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
    assert 'MEMORY_LIMIT="384m"' in source
    assert 'MEMORY_LIMIT="512m"' in source
    assert "MIN_AVAILABLE_KB" in source


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
        assert "RuntimeMaxSec=" in source
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
