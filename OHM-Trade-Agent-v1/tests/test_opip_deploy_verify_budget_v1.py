"""Release-pipeline verification budgets (ATDD-RELEASE-PIPELINE-v1 / AC-013).

Proves the deploy separates the unified-cycle wait budget from the runtime
verifier's independent budget, derives the cycle wait from the authorized
scheduler hard runtime bound, and keeps the success semantics strict (fresh
SUCCESS after candidate readiness only; DEGRADED fails immediately; a stale
pre-readiness SUCCESS never passes; the verifier always receives its own full
budget).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = APP_ROOT / "deploy" / "remote" / "ohm-deploy"
CRON = APP_ROOT / "deploy" / "cron.d" / "ohm-unified-cycle"


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _function_block(name: str, next_name: str) -> str:
    text = _deploy()
    start = text.index(f"{name}() {{")
    end = text.index(f"{next_name}() {{", start)
    return text[start:end].rstrip()


def _bash() -> str | None:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _is_fork_failure(proc: subprocess.CompletedProcess) -> bool:
    stderr = proc.stderr or ""
    return (
        proc.returncode in (254, 3221225794)
        or "fork:" in stderr
        or "dofork" in stderr
        or "Resource temporarily unavailable" in stderr
    )


# ---------------------------------------------------------------------------
# Structural: budgets are separated and derived, not shared.
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_013_cycle_wait_and_verifier_budgets_are_separated() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the cycle wait and the runtime verifier have separate, independently named budgets."""
    deploy = _deploy()
    # The shared leftover-based deadline is gone.
    assert "DEPLOY_VERIFY_DEADLINE" not in deploy
    assert "REMAINING_VERIFY_SECONDS" not in deploy
    # The cycle wait is derived from the authorized scheduler hard bound plus a
    # bounded grace, not an arbitrary shared constant.
    assert "scheduler_hard_bound_seconds" in deploy
    assert "SCHEDULER_HARD_BOUND_SECONDS=" in deploy
    assert (
        "UNIFIED_CYCLE_RELEASE_WAIT_SECONDS=$((SCHEDULER_HARD_BOUND_SECONDS + UNIFIED_CYCLE_RELEASE_GRACE_SECONDS))"
        in deploy
    )
    # The verifier keeps its own independent window.
    assert "RUNTIME_VERIFIER_TIMEOUT_SECONDS=360" in deploy
    assert '--timeout-seconds "$RUNTIME_VERIFIER_TIMEOUT_SECONDS"' in deploy
    # Machine-readable diagnostics.
    for marker in (
        "OPIP_UNIFIED_CYCLE_WAIT_SECONDS=",
        "OPIP_RUNTIME_VERIFY_BUDGET_SECONDS=",
    ):
        assert marker in deploy


@pytest.mark.acceptance
def test_ac_013_verifier_budget_matches_the_app_max() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the deploy's verifier budget equals the app's own MAX_WAIT_SECONDS, so it cannot silently drift."""
    from app.services import release_runtime_verifier

    deploy = _deploy()
    assert f"RUNTIME_VERIFIER_TIMEOUT_SECONDS={release_runtime_verifier.MAX_WAIT_SECONDS}" in deploy


@pytest.mark.acceptance
def test_ac_013_wait_budget_aligns_with_the_scheduler_hard_bound() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the cycle wait is at least the scheduler hard bound, so a healthy cycle up to that bound is not falsely rejected."""
    cron = CRON.read_text(encoding="utf-8")
    # The scheduler authorizes a long hard runtime bound.
    assert "timeout --signal=TERM --kill-after=30s 3600" in cron
    deploy = _deploy()
    # A 3600s bound + 120s grace is far above the old 360s window.
    assert "UNIFIED_CYCLE_RELEASE_GRACE_SECONDS=120" in deploy


@pytest.mark.acceptance
def test_ac_013_no_second_scheduler_or_cycle_is_launched() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the wait observes the existing scheduler; it launches no second cycle/scheduler and mutates no protection semantics."""
    block = _function_block("wait_unified_cycle_success", "validate_candidate_container_identity")
    for forbidden in (
        "run_cycle",
        "docker compose up",
        "crontab",
        "systemctl",
        "flock",
        "OPIP_PAPER",
        "OPIP_FEATURE_BUS",
        "OPIP_CANONICAL_WRITER",
    ):
        assert forbidden not in block, forbidden
    # Protection independence is unchanged: the deploy still raises no protection
    # mutation path (no direct active-trade/protection writer call).
    deploy = _deploy()
    assert "monitor_active_trades" not in deploy
    assert "paper_v2_protection_runtime" not in deploy


# ---------------------------------------------------------------------------
# Functional: the real wait_unified_cycle_success semantics, via the log seam.
# ---------------------------------------------------------------------------


def _run_wait(ready_after: str, deadline_seconds: int, log_body: str) -> subprocess.CompletedProcess:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _function_block("wait_unified_cycle_success", "validate_candidate_container_identity")
    with tempfile.TemporaryDirectory() as d:
        tmp_log = Path(d) / "cycle.log"
        tmp_log.write_text(log_body, encoding="utf-8")
        script = (
            "set -Eeuo pipefail\n"
            "export OPIP_DEPLOY_TEST_SEAMS=1\n"
            f"export OPIP_RELEASE_CYCLE_LOG='{tmp_log.as_posix()}'\n"
            f"{fn}\n"
            f'wait_unified_cycle_success "{ready_after}" "$((SECONDS + {deadline_seconds}))"\n'
        )
        proc = subprocess.run(
            [bash, "-c", script], capture_output=True, text=True, timeout=90
        )
    return proc


def _iso(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@pytest.mark.acceptance
def test_ac_013_functional_accepts_a_fresh_success() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: a fresh SUCCESS completed after candidate readiness passes."""
    log = f"OPIP_UNIFIED_CYCLE_STATUS=SUCCESS\nOPIP_UNIFIED_CYCLE_COMPLETED_AT={_iso(1)}\n"
    proc = _run_wait(_iso(5), 60, log)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_UNIFIED_CYCLE=HEALTHY" in proc.stdout


@pytest.mark.acceptance
def test_ac_013_functional_accepts_a_cycle_that_exceeded_the_old_window() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: a healthy cycle that completed more than 360s after readiness is NOT falsely rejected."""
    # ready_after was 400s ago; the cycle completed 1s ago (i.e. ~399s after
    # readiness) -- beyond the old 360s shared window.
    log = f"OPIP_UNIFIED_CYCLE_STATUS=SUCCESS\nOPIP_UNIFIED_CYCLE_COMPLETED_AT={_iso(1)}\n"
    proc = _run_wait(_iso(400), 60, log)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_UNIFIED_CYCLE=HEALTHY" in proc.stdout


@pytest.mark.acceptance
def test_ac_013_functional_rejects_degraded_immediately() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: a fresh DEGRADED cycle fails immediately."""
    log = f"OPIP_UNIFIED_CYCLE_STATUS=DEGRADED\nOPIP_UNIFIED_CYCLE_COMPLETED_AT={_iso(1)}\n"
    proc = _run_wait(_iso(30), 30, log)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "DEGRADED" in proc.stderr


@pytest.mark.acceptance
def test_ac_013_functional_rejects_a_stale_pre_readiness_success() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: a stale SUCCESS that completed before candidate readiness never passes."""
    # ready_after 60s ago; completion 300s ago (before readiness) -> not fresh.
    log = f"OPIP_UNIFIED_CYCLE_STATUS=SUCCESS\nOPIP_UNIFIED_CYCLE_COMPLETED_AT={_iso(300)}\n"
    proc = _run_wait(_iso(60), 1, log)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "no successful unified cycle" in proc.stderr


@pytest.mark.acceptance
def test_ac_013_functional_rejects_no_completion_within_the_window() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: no completion within the bounded window fails closed."""
    proc = _run_wait(_iso(30), 1, "OPIP_UNIFIED_CYCLE_STATUS=STARTED\n")
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "no successful unified cycle" in proc.stderr


@pytest.mark.acceptance
def test_ac_013_test_seams_are_gated_by_an_explicit_marker() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the log/entry test seams are inert in production (honored only under an explicit test marker), so a forged log cannot fake the success signal."""
    deploy = _deploy()
    # Both overrides are guarded by the marker, so production cannot be pointed
    # at a forged cycle log or an arbitrary scheduler entry.
    assert deploy.count('"${OPIP_DEPLOY_TEST_SEAMS:-0}" == "1"') >= 2
    assert 'cycle_log="$OPIP_RELEASE_CYCLE_LOG"' in deploy
    assert 'entry="$OPIP_RELEASE_SCHEDULER_ENTRY"' in deploy
    # The production defaults remain the real paths.
    assert 'local cycle_log="/var/log/ohm-unified-cycle.log"' in deploy
    assert 'local entry="/etc/cron.d/ohm-unified-cycle"' in deploy


@pytest.mark.acceptance
def test_ac_013_scheduler_bound_is_derived_and_fails_closed() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: scheduler_hard_bound_seconds derives the bound from the entry and fails closed when it cannot."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _function_block("scheduler_hard_bound_seconds", "wait_unified_cycle_success")
    with tempfile.TemporaryDirectory() as d:
        good = Path(d) / "good.cron"
        good.write_text(
            "* * * * * root flock -n /var/run/ohm-unified-cycle.lock -c "
            "'... timeout --signal=TERM --kill-after=30s 3600 docker compose exec ...'\n",
            encoding="utf-8",
        )
        bad = Path(d) / "bad.cron"
        bad.write_text("no bound here\n", encoding="utf-8")
        script = (
            "set -Eeuo pipefail\n"
            "export OPIP_DEPLOY_TEST_SEAMS=1\n"
            f"{fn}\n"
            f"echo \"$(OPIP_RELEASE_SCHEDULER_ENTRY='{good.as_posix()}' scheduler_hard_bound_seconds)\"\n"
            f"if OPIP_RELEASE_SCHEDULER_ENTRY='{bad.as_posix()}' scheduler_hard_bound_seconds; then echo UNEXPECTED; exit 9; else echo CLOSED; fi\n"
        )
        proc = subprocess.run([bash, "-c", script], capture_output=True, text=True, timeout=60)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode == 0, proc.stderr
    assert "3600" in proc.stdout
    assert "CLOSED" in proc.stdout
