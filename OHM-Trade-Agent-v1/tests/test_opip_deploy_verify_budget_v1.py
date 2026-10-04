"""Release-pipeline verification budgets (ATDD-RELEASE-PIPELINE-v1 / AC-013).

Proves the deploy separates the unified-cycle wait budget from the runtime
verifier's independent budget, derives the cycle wait from the authorized
scheduler hard runtime bound, and keeps the success semantics strict (fresh
SUCCESS after candidate readiness only; DEGRADED fails immediately; a stale
pre-readiness SUCCESS never passes; the verifier always receives its own full
budget).

It also proves the wait's FAILURE path is diagnosable: a failed wait emits
bounded, structured, read-only evidence (log state, marker counts, last
completion age, installed cron schedule, host wrapper lock ownership) into the
deploy log BEFORE rollback restores the previous release and discards the
candidate's own diagnostics. The reporter runs only after the failure is already
decided, so it can neither change the wait's success semantics nor mask the
original failure reason.
"""

from __future__ import annotations

import re
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


def _diagnostic_constants() -> str:
    """The reporter's own module-level bounds/filter, which precede the functions."""
    text = _deploy()
    start = text.index("UNIFIED_CYCLE_DIAGNOSTIC_TAIL_LINES=")
    end = text.index("unified_cycle_lock_probe() {", start)
    return text[start:end].rstrip()


def _marker(stdout: str, name: str) -> str | None:
    match = re.search(rf"^{name}=(.*)$", stdout, re.MULTILINE)
    return match.group(1).strip() if match else None


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


def _lock_needle(path: Path) -> str:
    """Mirror the probe's own decode of ``stat`` device/inode into the /proc/locks shape."""
    bash = _bash()
    proc = subprocess.run(
        [bash, "-c", f"stat -c '%D %i' '{path.as_posix()}'"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0 or len(proc.stdout.split()) != 2:
        pytest.skip("bash stat is unavailable in this environment")
    dev_hex, ino = proc.stdout.split()
    dev = int(dev_hex, 16)
    return f"{(dev >> 20) & 0xfff:02x}:{dev & 0xfffff:02x}:{ino}"


def _run_wait(
    ready_after: str,
    deadline_seconds: int,
    log_body: str,
    cron_body: str | None = None,
    lock_probe: str | None = None,
) -> subprocess.CompletedProcess:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available in this environment")
    fn = _function_block("wait_unified_cycle_success", "validate_candidate_container_identity")
    # The failure path under test calls the real reporter, so the harness runs the
    # real bounds, lock probe and reporter definitions instead of stubbing them.
    evidence = (
        _diagnostic_constants()
        + "\n\n"
        + _function_block("unified_cycle_lock_probe", "report_unified_cycle_wait_failure")
        + "\n\n"
        + _function_block("report_unified_cycle_wait_failure", "scheduler_hard_bound_seconds")
        + "\n\n"
    )
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        tmp_log = tmp / "cycle.log"
        tmp_log.write_bytes(log_body.encode("utf-8"))
        env_lines = ""
        if lock_probe is not None:
            # A realistic wrapper entry pointing at a real lock file, plus a synthetic
            # /proc/locks entry built from that lock's own device:inode, so the probe's
            # decode is exercised against the kernel's documented lock-table layout.
            lock_file = tmp / "wrapper.lock"
            lock_file.write_bytes(b"")
            tmp_cron = tmp / "ohm-unified-cycle"
            tmp_cron.write_bytes(
                (
                    "SHELL=/bin/bash\n"
                    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
                    "* * * * * root flock -n "
                    f"{lock_file.as_posix()} -c 'cd /opt/OHM-Trade-Agent-v1 && timeout "
                    "--signal=TERM --kill-after=30s 3600 /usr/bin/docker compose exec -T "
                    "ohm-trade-agent python -m app.jobs.run_cycle "
                    ">> /var/log/ohm-unified-cycle.log 2>&1'\n"
                ).encode("utf-8")
            )
            needle = _lock_needle(lock_file)
            # A HELD probe needs the real device:inode; the free case must use a
            # needle that cannot match it, and appending digits to the inode field
            # cannot collide with the real entry.
            needle = needle if lock_probe == "held" else f"{needle}9999"
            tmp_locks = tmp / "locks"
            tmp_locks.write_bytes(f"1: FLOCK  ADVISORY  WRITE 4242 {needle} 0 EOF\n".encode("utf-8"))
            env_lines += f"export OPIP_RELEASE_SCHEDULER_ENTRY='{tmp_cron.as_posix()}'\n"
            env_lines += f"export OPIP_RELEASE_LOCKS_FILE='{tmp_locks.as_posix()}'\n"
        elif cron_body is not None:
            tmp_cron = tmp / "ohm-unified-cycle"
            tmp_cron.write_bytes(cron_body.encode("utf-8"))
            env_lines += f"export OPIP_RELEASE_SCHEDULER_ENTRY='{tmp_cron.as_posix()}'\n"
        log_path = tmp_log.as_posix()
        script = (
            "set -Eeuo pipefail\n"
            "export OPIP_DEPLOY_TEST_SEAMS=1\n"
            f"export OPIP_RELEASE_CYCLE_LOG='{log_path}'\n"
            f"{env_lines}"
            f"{evidence}{fn}\n"
            # The cycle log is fingerprinted immediately before and after the wait,
            # so the reporter's read-only promise is observed, not merely asserted.
            f'echo "PRECHECK_LOG_BYTES=$(wc -c < \'{log_path}\')"\n'
            f'echo "PRECHECK_LOG_SHA=$(sha256sum \'{log_path}\' | cut -d" " -f1)"\n'
            "set +e\n"
            f'wait_unified_cycle_success "{ready_after}" "$((SECONDS + {deadline_seconds}))"\n'
            "rc=$?\n"
            f'echo "POSTCHECK_LOG_BYTES=$(wc -c < \'{log_path}\')"\n'
            f'echo "POSTCHECK_LOG_SHA=$(sha256sum \'{log_path}\' | cut -d" " -f1)"\n'
            "exit $rc\n"
        )
        # The harness script is run from a file, exactly as the release controller is
        # on the host. Passing this much shell through argv re-quotes it on Windows
        # and can corrupt the parse in ways production never sees.
        harness = tmp / "wait_harness.sh"
        harness.write_bytes(script.encode("utf-8"))
        proc = subprocess.run(
            [bash, harness.as_posix()], capture_output=True, text=True, timeout=90
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


# ---------------------------------------------------------------------------
# Failure evidence: a failed wait must be diagnosable from the deploy log alone.
#
# Rollback restores the PREVIOUS release's binaries, so any diagnostics shipped
# by the candidate release are gone by the time an operator runs
# `/diagnose-learning`. The evidence therefore has to be emitted by the deploy
# controller itself, at the moment the wait fails.
# ---------------------------------------------------------------------------

_EVIDENCE_MARKERS = (
    "OPIP_UNIFIED_CYCLE_WAIT_FAILURE=",
    "OPIP_UNIFIED_CYCLE_WAIT_FAILURE_CLASS=",
    "OPIP_UNIFIED_CYCLE_WAIT_READY_AFTER=",
    "OPIP_UNIFIED_CYCLE_DIAGNOSTIC=READ_ONLY",
    "OPIP_UNIFIED_CYCLE_LOG_EXISTS=",
    "OPIP_UNIFIED_CYCLE_LOG_SIZE_BYTES=",
    "OPIP_UNIFIED_CYCLE_LOG_MTIME_UTC=",
    "OPIP_UNIFIED_CYCLE_TAIL_LINES_REQUESTED=",
    "OPIP_UNIFIED_CYCLE_TAIL_BYTES_LIMIT=",
    "OPIP_UNIFIED_CYCLE_TAIL_SCANNED_LINES=",
    "OPIP_UNIFIED_CYCLE_TAIL_SUCCESS_COUNT=",
    "OPIP_UNIFIED_CYCLE_TAIL_DEGRADED_COUNT=",
    "OPIP_UNIFIED_CYCLE_TAIL_COMPLETED_COUNT=",
    "OPIP_UNIFIED_CYCLE_TAIL_SKIPPED_COUNT=",
    "OPIP_UNIFIED_CYCLE_TAIL_TRACEBACK_COUNT=",
    "OPIP_UNIFIED_CYCLE_LAST_STATUS=",
    "OPIP_UNIFIED_CYCLE_LAST_COMPLETED_AT=",
    "OPIP_UNIFIED_CYCLE_LAST_COMPLETION_AGE_SECONDS=",
    "OPIP_UNIFIED_CYCLE_CRON_EXISTS=",
    "OPIP_UNIFIED_CYCLE_CRON_SCHEDULE=",
    "OPIP_UNIFIED_CYCLE_CRON_LOG_TARGET=",
    "OPIP_UNIFIED_CYCLE_HOST_LOCK_PATH=",
    "OPIP_UNIFIED_CYCLE_HOST_LOCK_PRESENT=",
    "OPIP_UNIFIED_CYCLE_HOST_LOCK_HELD=",
    "OPIP_UNIFIED_CYCLE_TAIL",
    "OPIP_UNIFIED_CYCLE_TAIL_END",
)


@pytest.mark.acceptance
def test_ac_013_wait_failure_emits_bounded_structured_evidence() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: a failed wait emits the bounded, structured evidence needed to diagnose it from the deploy log."""
    reporter = _function_block("report_unified_cycle_wait_failure", "scheduler_hard_bound_seconds")
    for marker in _EVIDENCE_MARKERS:
        assert marker in reporter, marker
    consts = _diagnostic_constants()
    # Bounded: a line window to scan, a match window and per-line truncation to
    # surface, and a total byte ceiling -- all from named constants.
    for bound in (
        "UNIFIED_CYCLE_DIAGNOSTIC_TAIL_LINES=",
        "UNIFIED_CYCLE_DIAGNOSTIC_TAIL_MATCHES=",
        "UNIFIED_CYCLE_DIAGNOSTIC_TAIL_BYTES=",
        "UNIFIED_CYCLE_DIAGNOSTIC_LINE_BYTES=",
    ):
        assert bound in consts, bound
    assert 'tail -n "$UNIFIED_CYCLE_DIAGNOSTIC_TAIL_LINES"' in reporter
    assert 'tail -n "$UNIFIED_CYCLE_DIAGNOSTIC_TAIL_MATCHES"' in reporter
    assert 'cut -c "1-$UNIFIED_CYCLE_DIAGNOSTIC_LINE_BYTES"' in reporter
    assert 'head -c "$UNIFIED_CYCLE_DIAGNOSTIC_TAIL_BYTES"' in reporter
    # The surfaced tail is allowlisted to release markers and exception frames,
    # never arbitrary application output.
    assert "UNIFIED_CYCLE_DIAGNOSTIC_LINE_FILTER=" in consts
    assert 'grep -E "$UNIFIED_CYCLE_DIAGNOSTIC_LINE_FILTER"' in reporter
    # It is called on BOTH failure paths -- no fresh completion, and a fresh
    # DEGRADED cycle -- and nowhere else, so it cannot become a success-path side
    # effect or be silently reused on a healthy wait.
    deploy = _deploy()
    assert deploy.count('report_unified_cycle_wait_failure "$cycle_log" "$ready_after"') == 2
    assert '"$ready_after" NO_FRESH_COMPLETION' in deploy
    assert '"$ready_after" DEGRADED_AFTER_READINESS' in deploy


@pytest.mark.acceptance
def test_ac_013_wait_failure_evidence_is_read_only() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the evidence path only observes - it starts nothing, writes nothing and takes no lock."""
    reporter = _function_block("report_unified_cycle_wait_failure", "scheduler_hard_bound_seconds")
    # It starts no second cycle, container, scheduler or cron edit ...
    for forbidden in ("docker", "crontab", "systemctl", "run_cycle", "nohup", "pkill", "kill "):
        assert forbidden not in reporter, forbidden
    # ... and mutates no file or process state.
    for forbidden in ("mkdir", "touch", "tee ", "chmod", "chown", "rm -", "mv -", "truncate", "unlink"):
        assert forbidden not in reporter, forbidden
    # The only `>` in the reporter silences a probe (`2>/dev/null`) or is an
    # arithmetic comparison/shift; nothing redirects into a path. Comments (the
    # extraction carries the next function's doc block) and quoted regions are
    # removed first, so a pattern that merely mentions `>>` cannot mask a write.
    code = "\n".join(
        line for line in reporter.splitlines() if not line.lstrip().startswith("#")
    )
    unquoted = re.sub(r"'[^']*'", "", code, flags=re.S)
    for target in re.findall(r">>?\s*([^\s;&|]+)", unquoted):
        assert target == "/dev/null" or target.isdigit(), target
    # Lock OWNERSHIP is read from the kernel's lock table: the probe never calls
    # flock, so it cannot take, release or delete a lock.
    probe = _function_block("unified_cycle_lock_probe", "report_unified_cycle_wait_failure")
    assert "/proc/locks" in probe
    assert "flock" not in probe
    assert "docker" not in probe


@pytest.mark.acceptance
def test_ac_013_functional_no_completion_reports_structured_evidence() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: a timed-out wait keeps the original failure and reports what the cycle log and schedule actually showed."""
    stale = _iso(600)
    log = (
        "OHM Unified Cycle skipped: previous cycle still running.\n"
        "OHM Unified Cycle skipped: previous cycle still running.\n"
        "OHM Unified Cycle skipped: previous cycle still running.\n"
        f"OPIP_UNIFIED_CYCLE_STATUS=SUCCESS\nOPIP_UNIFIED_CYCLE_COMPLETED_AT={stale}\n"
    )
    cron = (
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        "MAILTO=root\n"
        "* * * * * root flock -n /var/run/ohm-unified-cycle.lock -c 'cd "
        "/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1 && timeout --signal=TERM "
        "--kill-after=30s 3600 /usr/bin/docker compose exec -T ohm-trade-agent "
        "python -m app.jobs.run_cycle >> /var/log/ohm-unified-cycle.log 2>&1'\n"
    )
    proc = _run_wait(_iso(30), 1, log, cron_body=cron)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    # The failure is still the original, unchanged one.
    assert proc.returncode != 0
    assert "no successful unified cycle" in proc.stderr
    # ... and it is now diagnosable from the log alone.
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_WAIT_FAILURE") == "NO_FRESH_COMPLETION"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_LOG_EXISTS") == "YES"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_TAIL_SKIPPED_COUNT") == "3"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_TAIL_SUCCESS_COUNT") == "1"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_LAST_STATUS") == "SUCCESS"
    age = _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_LAST_COMPLETION_AGE_SECONDS")
    assert age is not None and age.isdigit() and int(age) >= 600
    # The schedule read must survive the `SHELL=`/`PATH=`/`MAILTO=` assignments that
    # a real cron.d entry starts with, or the diagnosis would report the
    # environment instead of the schedule.
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_CRON_EXISTS") == "YES"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_CRON_SCHEDULE") == "* * * * *"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_CRON_LOG_TARGET") == "/var/log/ohm-unified-cycle.log"
    # The tail markers bracket real, allowlisted content.
    assert "OPIP_UNIFIED_CYCLE_TAIL_END" in proc.stdout
    assert "OHM Unified Cycle skipped" in proc.stdout
    assert "OPIP_UNIFIED_CYCLE_STATUS=SUCCESS" in proc.stdout
    # The reporter observed the log without touching it.
    assert _marker(proc.stdout, "PRECHECK_LOG_SHA") == _marker(proc.stdout, "POSTCHECK_LOG_SHA")
    assert _marker(proc.stdout, "PRECHECK_LOG_BYTES") == _marker(proc.stdout, "POSTCHECK_LOG_BYTES")


@pytest.mark.acceptance
def test_ac_013_functional_degraded_is_reported_as_evidence() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the immediate DEGRADED failure is attributed, not just reported."""
    log = f"OPIP_UNIFIED_CYCLE_STATUS=DEGRADED\nOPIP_UNIFIED_CYCLE_COMPLETED_AT={_iso(1)}\n"
    proc = _run_wait(_iso(30), 30, log)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "DEGRADED" in proc.stderr
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_WAIT_FAILURE") == "DEGRADED_AFTER_READINESS"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_LAST_STATUS") == "DEGRADED"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_TAIL_DEGRADED_COUNT") == "1"
    assert _marker(proc.stdout, "PRECHECK_LOG_SHA") == _marker(proc.stdout, "POSTCHECK_LOG_SHA")


@pytest.mark.acceptance
@pytest.mark.parametrize("lock_probe,expected", [("held", "YES"), ("free", "NO")])
def test_ac_013_functional_host_lock_probe_distinguishes_held_from_free(lock_probe: str, expected: str) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: the host wrapper lock state is read from the kernel lock table without taking the lock."""
    proc = _run_wait(_iso(30), 1, "OHM Unified Cycle skipped: previous cycle still running.\n", lock_probe=lock_probe)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_HOST_LOCK_PRESENT") == "YES"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_HOST_LOCK_HELD") == expected
    lock_path = _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_HOST_LOCK_PATH")
    assert lock_path is not None and lock_path.endswith("wrapper.lock")
    # A held wrapper lock is the one cause a cycle log cannot show at all, so it is
    # classified explicitly; a free lock leaves the cause to the log-derived classes.
    failure_class = _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_WAIT_FAILURE_CLASS")
    if lock_probe == "held":
        assert failure_class == "HOST_WRAPPER_LOCK_HELD"
    else:
        assert failure_class == "SKIPPED_BEHIND_RUNNING_CYCLE"


@pytest.mark.acceptance
def test_ac_013_functional_evidence_classifies_a_silent_log() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-013: a log with no cycle activity is classified as such instead of being left unexplained."""
    proc = _run_wait(_iso(30), 1, "")
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_TAIL_SKIPPED_COUNT") == "0"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_LAST_STATUS") == "NONE"
    assert _marker(proc.stdout, "OPIP_UNIFIED_CYCLE_WAIT_FAILURE_CLASS") == "NO_CYCLE_LOG_ACTIVITY"
