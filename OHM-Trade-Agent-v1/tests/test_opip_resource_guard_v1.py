"""Hermetic guard, export default-off and cron timing regression tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "deploy/remote/opip-run-guarded"
EXPORT = ROOT / "deploy/remote/export-opip-learning-evidence.sh"
CRON = ROOT / "deploy/cron.d"


def _env(tmp_path: Path, available_kb: str | None):
    meminfo = tmp_path / "meminfo"
    if available_kb is not None:
        meminfo.write_text(f"MemAvailable: {available_kb} kB\n", encoding="utf-8")
    env = os.environ.copy()
    env["OPIP_GUARD_MEMINFO_PATH"] = str(meminfo)
    env["OPIP_GUARD_LOG_PATH"] = str(tmp_path / "guard.log")
    return env


@pytest.mark.acceptance
def test_guard_skips_under_low_memory_without_running_child(tmp_path):
    """ATDD-OPIP-RESOURCE-GUARD-v1/AC-002: low host memory skips an optional process and records one reason."""
    marker = tmp_path / "ran"
    env = _env(tmp_path, "300000")
    call = subprocess.run(
        ["bash", str(GUARD), "512", "--", sys.executable, "-c",
         f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')"],
        capture_output=True, text=True, env=env, check=False, timeout=10,
    )
    assert call.returncode == 0
    assert call.stdout == "" and call.stderr == ""
    assert not marker.exists()
    log = (tmp_path / "guard.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and "SKIPPED reason=LOW_MEMORY" in log[0]


@pytest.mark.acceptance
def test_guard_execs_command_when_memory_is_available(tmp_path):
    """ATDD-OPIP-RESOURCE-GUARD-v1/AC-002: sufficient MemAvailable executes the child unchanged."""
    env = _env(tmp_path, "1048576")
    result = subprocess.run(
        ["bash", str(GUARD), "--", sys.executable, "-c", "print('RUN_OK')"],
        capture_output=True, text=True, env=env, check=False, timeout=10,
    )
    assert result.returncode == 0 and result.stdout == "RUN_OK\n"
    assert not (tmp_path / "guard.log").exists()


@pytest.mark.acceptance
def test_guard_fails_closed_when_meminfo_is_unavailable(tmp_path):
    """ATDD-OPIP-RESOURCE-GUARD-v1/AC-002: unavailable memory evidence must not launch a child."""
    env = _env(tmp_path, None)
    result = subprocess.run(
        ["bash", str(GUARD), "512", "--", sys.executable, "-c", "raise SystemExit(19)"],
        capture_output=True, text=True, env=env, check=False, timeout=10,
    )
    assert result.returncode == 0
    assert "reason=MEMINFO_UNAVAILABLE" in (tmp_path / "guard.log").read_text(encoding="utf-8")


@pytest.mark.acceptance
def test_export_exits_silently_without_explicit_enable_file(tmp_path):
    """ATDD-OPIP-RESOURCE-GUARD-v1/AC-001: reinstalled cron cannot launch canonical export without owner flag."""
    source = EXPORT.read_text(encoding="utf-8")
    clause = "[[ -f /etc/opip/learning-export.enabled && ! -L /etc/opip/learning-export.enabled ]] || exit 0"
    assert source.count(clause) == 1
    missing = tmp_path / "no-export-flag"
    substitute = f"[[ -f {str(missing)!r} && ! -L {str(missing)!r} ]] || exit 0"
    copy = tmp_path / "test-export.sh"
    copy.write_text(source.replace(clause, substitute), encoding="utf-8")
    result = subprocess.run(["bash", str(copy)], capture_output=True, text=True,
                            check=False, timeout=10)
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""


@pytest.mark.acceptance
def test_crons_keep_flock_and_stagger_only_bounded_optional_jobs():
    """ATDD-OPIP-RESOURCE-GUARD-v1/AC-003: guard optional cron jobs, protect unified cycle and fit offset plus timeout within 59s."""
    for name, offset in (("opip-feature-bus-capture", 2),
                         ("opip-feasibility-evidence-capture", 3),
                         ("ohm-tradingview-inbox", 4)):
        entry = (CRON / name).read_text(encoding="utf-8")
        line = next(l for l in entry.splitlines() if l.startswith("* * * * * root "))
        assert f"sleep {offset}; /bin/bash " in line
        assert "opip-run-guarded 512 -- flock -n " in line
        assert "timeout --signal=TERM --kill-after=5s 50" in line
        assert offset + 50 + 5 <= 59
    irr = (CRON / "opip-irr-shadow").read_text(encoding="utf-8")
    assert "opip-run-guarded 512 -- flock -n /var/run/opip-irr-shadow.lock" in irr
    assert "sleep " not in next(l for l in irr.splitlines() if l.startswith("* * * * * root "))
    cycle = (CRON / "ohm-unified-cycle").read_text(encoding="utf-8")
    assert "opip-run-guarded" not in cycle
    assert "timeout --signal=TERM --kill-after=30s 3600" in cycle
    scheduler = (ROOT / "deploy/remote/reconcile-scheduler.sh").read_text(encoding="utf-8")
    assert 'install -o root -g root -m 0644 "$LEARNING_EXPORT_SRC" "$LEARNING_EXPORT_DST"' in scheduler
    assert "ohm-tradingview-inbox" not in scheduler
    assert "ohm-wave5-explosion-learning" not in scheduler
