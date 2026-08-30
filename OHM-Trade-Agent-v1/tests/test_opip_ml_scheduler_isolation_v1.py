import ast
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_ml_evidence_cron_is_independent_and_serialized():
    cron = (ROOT / "deploy" / "cron.d" / "opip-ml-evidence").read_text(
        encoding="utf-8"
    )
    assert "app.jobs.run_opip_ml_capture" in cron
    assert "app.jobs.build_phase3c_forward_outcomes" in cron
    assert cron.count("/var/run/opip-background.lock") == 2
    assert "run-opip-background-job.sh" in cron
    assert "* * * * * root sleep 25;" in cron
    assert "2,12,22,32,42,52 * * * * root sleep 20;" in cron

    executable = "\n".join(
        line
        for line in cron.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert "/var/run/ohm-unified-cycle.lock" not in executable
    assert "/var/run/opip-ml-capture-trigger.lock" not in executable
    assert "/var/run/opip-ml-outcomes.lock" not in executable


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


def test_scheduler_reconcile_installs_bounded_background_lane():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    assert 'ML_EVIDENCE_DST="/etc/cron.d/opip-ml-evidence"' in source
    assert 'BACKGROUND_RUNNER="$APP_ROOT/deploy/remote/run-opip-background-job.sh"' in source
    assert 'install -o root -g root -m 0644 "$ML_EVIDENCE_SRC" "$ML_EVIDENCE_DST"' in source
    assert "/var/run/opip-background.lock" in source
    assert "run-opip-background-job.sh" in source
    assert "ml_background_memory_limits=capture:192m,outcomes:256m" in source


def test_deploy_probes_ml_capture_through_bounded_background_lane():
    source = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    assert 'BACKGROUND_RUNNER="$APP_ROOT/deploy/remote/run-opip-background-job.sh"' in source
    assert "flock -w 120 8" in source
    assert "app.jobs.run_opip_ml_capture" in source
    assert "completed or safely skipped by memory guard" in source
    assert "production unaffected" in source


def test_scheduler_validator_ignores_comment_only_unified_lock_mentions():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    cron = (ROOT / "deploy" / "cron.d" / "opip-ml-evidence").read_text(
        encoding="utf-8"
    )

    assert "/var/run/ohm-unified-cycle.lock" in cron
    executable = "\n".join(
        line
        for line in cron.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert "/var/run/ohm-unified-cycle.lock" not in executable
    assert "for cmd in install crontab grep awk mktemp cp rm" in source
    assert "!/^[[:space:]]*#/" in source
    assert "/\\/var\\/run\\/ohm-unified-cycle\\.lock/" in source
    assert "grep -v -E '^[[:space:]]*#' \"$ML_EVIDENCE_DST\" | grep -q" not in source


def test_background_runner_is_resource_bounded_and_networkless():
    runner = ROOT / "deploy" / "remote" / "run-opip-background-job.sh"
    source = runner.read_text(encoding="utf-8")
    subprocess.run(
        ["bash", "-n", str(runner)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--network none" in source
    assert 'MEMORY_LIMIT="192m"' in source
    assert 'MEMORY_LIMIT="256m"' in source
    assert "--memory \"$MEMORY_LIMIT\"" in source
    assert "--memory-swap \"$MEMORY_LIMIT\"" in source
    assert "--cpus 0.20" in source
    assert "MIN_AVAILABLE_KB" in source
    assert "/proc/meminfo" in source
    assert "/usr/bin/timeout" in source
    assert "--pids-limit 128" in source
    assert "--oom-score-adj 800" in source
    assert "--read-only" in source
    assert "--cap-drop ALL" in source
    assert "app.jobs.run_opip_ml_capture|app.jobs.build_phase3c_forward_outcomes" in source
    assert "docker inspect --format '{{.Image}}'" in source
    assert 'P1_SHADOW_OUTBOX_ENABLED="$P1_SHADOW_OUTBOX_ENABLED"' in source
    assert '--env-file "$APP_ROOT/.env"' not in source
    assert "python -m \"$MODULE\"" in source


def test_background_launch_lock_is_distinct_from_capture_state_lock():
    cron = (ROOT / "deploy" / "cron.d" / "opip-ml-evidence").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    service = (
        ROOT / "app" / "services" / "opip_ml_evidence_capture.py"
    ).read_text(encoding="utf-8")

    launch = "/var/run/opip-background.lock"
    state = "/var/run/opip-ml-capture.lock"

    assert launch != state
    assert f"flock -n {launch}" in cron
    assert launch in deploy
    assert "flock -w 120 8" in deploy
    assert f'DEFAULT_ML_CAPTURE_LOCK_FILE = Path("{state}")' in service
    assert f"flock -n {state}" not in cron
    assert f"flock -n {state}" not in deploy


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
    assert section.index("stop_paper_stack") < section.index("docker compose build ohm-trade-agent")
