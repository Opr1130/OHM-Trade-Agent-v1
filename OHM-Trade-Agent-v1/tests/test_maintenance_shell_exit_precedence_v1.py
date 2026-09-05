"""Executes opip-data-platform-maintenance.sh against a stubbed `docker`
binary to prove the reconcile -> refresh-freshness -> health exit-status
precedence, without needing a real docker/compose environment or the
script's hardcoded production paths.

The script under test is copied verbatim except for its path *constants*
(APP_ROOT/ENV_FILE/STATE_FILE/LOCK_FILE/COMPOSE), which are rewritten to
point at a tmp_path sandbox -- the actual control flow (every command,
every `|| STATUS=$?` capture, the final precedence `if` chain) is executed
unmodified.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/analytics/opip-data-platform-maintenance.sh"


def _prepare_script(tmp_path: Path) -> Path:
    text = SCRIPT.read_text(encoding="utf-8")
    compose_file = tmp_path / "compose.yml"
    env_file = tmp_path / "opip-data-platform.env"
    state_file = tmp_path / "rollout.env"
    lock_file = tmp_path / "opip-learning-plane.lock"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("OPIP_ANALYTICS_ADMIN_DATABASE_URL=postgresql://x/y\n", encoding="utf-8")
    state_file.write_text(f"DEPLOYED_SHA={'a' * 40}\n", encoding="utf-8")

    replacements = {
        'ENV_FILE="/etc/opip-data-platform.env"': f'ENV_FILE="{env_file}"',
        'STATE_FILE="/var/lib/opip-data-platform/rollout.env"': f'STATE_FILE="{state_file}"',
        'LOCK_FILE="/var/lock/opip-learning-plane.lock"': f'LOCK_FILE="{lock_file}"',
        'COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.yml"': f'COMPOSE="{compose_file}"',
    }
    for old, new in replacements.items():
        assert old in text, f"maintenance.sh no longer contains expected constant: {old}"
        text = text.replace(old, new)

    script_path = tmp_path / "maintenance.sh"
    script_path.write_text(text, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return script_path


def _fake_docker(tmp_path: Path) -> Path:
    """A `docker` stand-in that logs the module it was asked to run (for
    ordering assertions) and exits according to RECONCILE_EXIT /
    REFRESH_EXIT / HEALTH_EXIT / MAINTENANCE_EXIT env vars."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
args="$*"
echo "$args" >> "$CALL_LOG"
case "$args" in
  *app.opip.data_platform.reconcile*) exit "${RECONCILE_EXIT:-0}" ;;
  *refresh-freshness*) exit "${REFRESH_EXIT:-0}" ;;
  *app.opip.data_platform.health*) exit "${HEALTH_EXIT:-0}" ;;
  *app.opip.data_platform.maintenance*) exit "${MAINTENANCE_EXIT:-0}" ;;
  *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run(tmp_path: Path, **exit_codes: int) -> subprocess.CompletedProcess:
    script_path = _prepare_script(tmp_path)
    bin_dir = _fake_docker(tmp_path)
    call_log = tmp_path / "calls.log"
    call_log.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CALL_LOG"] = str(call_log)
    for name, value in exit_codes.items():
        env[name] = str(value)
    result = subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    result.calls = call_log.read_text(encoding="utf-8").splitlines()
    return result


def _stage(call: str) -> str:
    if "app.opip.data_platform.reconcile" in call:
        return "reconcile"
    if "refresh-freshness" in call:
        return "refresh"
    if "app.opip.data_platform.health" in call:
        return "health"
    if "app.opip.data_platform.maintenance" in call:
        return "maintenance"
    return "unknown"


def test_all_stages_succeed_exits_zero(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0
    assert [_stage(c) for c in result.calls] == ["maintenance", "reconcile", "refresh", "health"]


def test_reconciliation_nonzero_still_runs_refresh_and_health(tmp_path):
    result = _run(tmp_path, RECONCILE_EXIT=2)
    stages = [_stage(c) for c in result.calls]
    assert "refresh" in stages
    assert "health" in stages
    assert result.returncode == 2


def test_refresh_nonzero_still_runs_health(tmp_path):
    result = _run(tmp_path, REFRESH_EXIT=1)
    stages = [_stage(c) for c in result.calls]
    assert "health" in stages
    assert result.returncode == 1


def test_health_nonzero_is_preserved_when_earlier_stages_succeed(tmp_path):
    result = _run(tmp_path, HEALTH_EXIT=2)
    assert result.returncode == 2


def test_reconciliation_status_takes_precedence_over_other_failures(tmp_path):
    result = _run(tmp_path, RECONCILE_EXIT=2, REFRESH_EXIT=1, HEALTH_EXIT=2)
    stages = [_stage(c) for c in result.calls]
    # Every stage still ran even though reconciliation already failed.
    assert stages == ["maintenance", "reconcile", "refresh", "health"]
    # Reconciliation's exit code wins over the later refresh/health failures.
    assert result.returncode == 2


def test_refresh_failure_takes_precedence_over_health_failure_when_reconcile_is_clean(tmp_path):
    result = _run(tmp_path, REFRESH_EXIT=3, HEALTH_EXIT=2)
    assert result.returncode == 3


def test_command_ordering_is_reconcile_then_refresh_then_health(tmp_path):
    result = _run(tmp_path)
    stages = [_stage(c) for c in result.calls]
    assert stages.index("reconcile") < stages.index("refresh") < stages.index("health")


def test_maintenance_nonzero_still_runs_reconcile_refresh_and_health(tmp_path):
    result = _run(tmp_path, MAINTENANCE_EXIT=7)
    stages = [_stage(c) for c in result.calls]
    assert stages == ["maintenance", "reconcile", "refresh", "health"]
    # All later stages succeeded, so maintenance's own exit code wins.
    assert result.returncode == 7


def test_maintenance_and_reconciliation_failures_together_prefer_reconciliation(tmp_path):
    result = _run(tmp_path, MAINTENANCE_EXIT=7, RECONCILE_EXIT=2)
    stages = [_stage(c) for c in result.calls]
    assert stages == ["maintenance", "reconcile", "refresh", "health"]
    assert result.returncode == 2


def test_maintenance_failure_wins_over_refresh_and_health_when_reconcile_is_clean(tmp_path):
    result = _run(tmp_path, MAINTENANCE_EXIT=7, REFRESH_EXIT=3, HEALTH_EXIT=2)
    stages = [_stage(c) for c in result.calls]
    assert stages == ["maintenance", "reconcile", "refresh", "health"]
    assert result.returncode == 7
