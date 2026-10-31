"""ATDD-SCANNER-RELIABILITY-M01/AC-007 - promotion must be reachable under cron.

Root cause this module pins:

* in-cycle qualification was removed (``app/jobs/run_cycle.py`` raises), so
  ``app.services.scan_promotion_runner`` is the ONLY path from a completed
  acquisition to a qualified advisory;
* that runner is started only by dispatcher stage 3;
* stage 3 used to require ``OPIP_SCAN_EXECUTION_MODE`` in *its own process
  environment*, while the only installed scheduler is cron, which exports
  nothing from ``.env``.

So an isolated deployment acquired results and promoted none of them, and the
symptom was indistinguishable from "the market produced no qualified signals".

The corrected contract: the mode is resolved from the same derived environment
the core reads, and an unprovable mode is recorded as an explicit reason instead
of being silently skipped. Legacy and suspended modes stay authorised no-ops.

Idempotency and incomplete-evidence rejection are already owned by
``tests/test_opip_scan_remediation_v1.py`` and are re-used, not duplicated.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time

import pytest

APP = pathlib.Path(__file__).resolve().parents[1]
DISPATCH = APP / "deploy" / "bin" / "opip-scan-dispatch"
RECONCILE = APP / "deploy" / "remote" / "reconcile-scheduler.sh"
CRON = APP / "deploy" / "cron.d" / "ohm-unified-cycle"
RUN_CYCLE = APP / "app" / "jobs" / "run_cycle.py"
BASH = shutil.which("bash")

pytestmark = [pytest.mark.acceptance]
requires_bash = pytest.mark.skipif(BASH is None, reason="bash is required")
_BASH_UNAVAILABLE_CODES = {3221225794, -1073741502, 3221225477}

PROMOTION_CMD = "scan_promotion_runner"
KEY = "OPIP_SCAN_EXECUTION_MODE"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _skip_if_bash_cannot_fork(result) -> None:
    output = f"{result.stderr}{result.stdout}"
    if result.returncode in _BASH_UNAVAILABLE_CODES or (
        result.returncode != 0 and "dofork" in output
    ):
        pytest.skip("bash cannot fork reliably in this environment")


def _dispatch_function(name: str) -> str:
    """Extract one shipped shell function verbatim."""
    lines = _text(DISPATCH).splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"{name}() {{")), None
    )
    assert start is not None, f"{name} is missing from {DISPATCH.name}"
    end = start
    while lines[end] != "}":
        end += 1
    return "\n".join(lines[start : end + 1])


# --- Static contracts (run everywhere) --------------------------------------


def test_the_dispatcher_no_longer_reads_only_its_process_environment():
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: the gate must consult the derived
    core env, not just cron's environment."""
    text = _text(DISPATCH)
    assert "OPIP_CORE_ENV_FILE" in text
    assert "/.env.core" in text
    assert '[ "${OPIP_SCAN_EXECUTION_MODE:-}" = "isolated" ]' not in text
    assert 'MODE="$(resolve_exec_mode)"' in text
    resolver = _dispatch_function("resolve_exec_mode")
    assert ".env.core" in resolver and ".env" in resolver
    assert 'if [ -n "${OPIP_SCAN_EXECUTION_MODE:-}" ]' in resolver


def test_unresolvable_modes_are_recorded_with_distinct_reasons():
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: unset, ambiguous and unknown modes
    are distinguishable so an operator can never mistake one for another."""
    text = _text(DISPATCH)
    for reason in (
        "EXECUTION_MODE_UNSET",
        "EXECUTION_MODE_AMBIGUOUS",
        "EXECUTION_MODE_UNRESOLVED",
    ):
        assert reason in text, reason
    assert "record_promotion_state SKIPPED EXECUTION_MODE_UNSET" in text
    assert "promotion_state.json" in text


def test_the_release_controller_installs_the_dispatcher():
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: nothing else creates the path the
    canonical cron entry invokes, so the controller must install it."""
    text = _text(RECONCILE)
    assert 'DISPATCH_DST="/usr/local/bin/opip-scan-dispatch"' in text
    assert 'bash -n "$DISPATCH_SRC"' in text
    assert 'install -o root -g root -m 0755 "$DISPATCH_SRC"' in text
    assert "OPIP_SCAN_DISPATCH_INSTALLED" in text
    assert "/usr/local/bin/opip-scan-dispatch" in _text(CRON)


def test_promotion_remains_the_only_qualification_path():
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: a second promotion path would let
    one result be qualified twice."""
    assert "in-cycle qualification was removed" in _text(RUN_CYCLE)
    reachable = sorted(
        path.name
        for path in (APP / "app").rglob("*.py")
        if "scan_promotion_runner" in path.read_text(encoding="utf-8")
    )
    assert reachable == ["run_cycle.py"], reachable
    assert PROMOTION_CMD in _text(DISPATCH)


# --- Behaviour: the shipped dispatcher, in a sandbox ------------------------


def _sandbox(tmp_path: pathlib.Path, *, mode, owner_mode=None):
    repo = tmp_path / "repo"
    (repo / "data" / "scan" / "staging").mkdir(parents=True)
    (repo / "data" / "scan" / "evidence").mkdir(parents=True)
    if mode is not None:
        (repo / ".env.core").write_text(
            f"{KEY}={mode}\nAPP_ENV=production\n", encoding="utf-8"
        )
    if owner_mode is not None:
        (repo / ".env").write_text(f"{KEY}={owner_mode}\n", encoding="utf-8")

    stub = tmp_path / "bin"
    stub.mkdir()
    calls = tmp_path / "docker_calls.txt"
    (stub / "docker").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n'
        'case "$1" in ps) exit 0 ;; esac\nexit 0\n',
        encoding="utf-8",
    )
    (stub / "flock").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub / "setsid").write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
    for name in ("docker", "flock", "setsid"):
        (stub / name).chmod(0o755)

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    env = {key: value for key, value in os.environ.items() if key != KEY}
    env.update(
        {
            "PATH": f"{stub}{os.pathsep}{env.get('PATH', '')}",
            "OPIP_REPO_DIR": str(repo),
            "OPIP_SCAN_LOCK": str(tmp_path / "scan.lock"),
            "OPIP_QUALIFY_LOCK": str(tmp_path / "qualify.lock"),
            "OPIP_SCAN_LOG_DIR": str(log_dir),
        }
    )
    return repo, calls, env


def _collect(repo: pathlib.Path, calls: pathlib.Path):
    deadline = time.monotonic() + 10
    while not calls.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    state_file = repo / "data" / "scan" / "promotion_state.json"
    state = (
        json.loads(state_file.read_text(encoding="utf-8"))
        if state_file.exists()
        else None
    )
    return recorded, state


@requires_bash
def test_isolated_promotion_runs_from_the_derived_core_env(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: cron carries no mode, the derived
    file does - promotion must run."""
    repo, calls, env = _sandbox(tmp_path, mode="isolated")
    result = subprocess.run(
        [BASH, str(DISPATCH)], capture_output=True, text=True, env=env, timeout=90
    )
    _skip_if_bash_cannot_fork(result)
    recorded, state = _collect(repo, calls)
    assert result.returncode == 0, result.stderr
    assert PROMOTION_CMD in recorded
    assert state["state"] == "DISPATCHED"
    assert state["reason_code"] == "MODE_ISOLATED"
    assert "qualification stage dispatched" in result.stdout


@requires_bash
@pytest.mark.parametrize("mode", ["legacy_inprocess", "suspended"])
def test_legacy_and_suspended_never_promote(tmp_path, mode):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: authorized no-ops - no promotion is
    launched, and nothing is recorded."""
    repo, calls, env = _sandbox(tmp_path, mode=mode)
    result = subprocess.run(
        [BASH, str(DISPATCH)], capture_output=True, text=True, env=env, timeout=90
    )
    _skip_if_bash_cannot_fork(result)
    recorded, state = _collect(repo, calls)
    assert result.returncode == 0, result.stderr
    assert PROMOTION_CMD not in recorded
    assert f"mode={mode} (authorised)" in result.stdout
    assert state is None


@requires_bash
def test_missing_mode_is_recorded_not_silently_skipped(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: no mode anywhere cannot be allowed
    to look like 'nothing qualified'."""
    repo, calls, env = _sandbox(tmp_path, mode=None)
    result = subprocess.run(
        [BASH, str(DISPATCH)], capture_output=True, text=True, env=env, timeout=90
    )
    _skip_if_bash_cannot_fork(result)
    recorded, state = _collect(repo, calls)
    assert result.returncode == 0, result.stderr
    assert PROMOTION_CMD not in recorded
    assert state["state"] == "SKIPPED"
    assert state["reason_code"] == "EXECUTION_MODE_UNSET"
    assert "WARN qualification stage SKIPPED" in result.stdout


@requires_bash
def test_ambiguous_mode_is_recorded_not_guessed(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: a duplicate definition is ambiguous,
    never 'last one wins'."""
    repo, calls, env = _sandbox(tmp_path, mode="isolated")
    (repo / ".env.core").write_text(
        f"{KEY}=isolated\n{KEY}=suspended\n", encoding="utf-8"
    )
    result = subprocess.run(
        [BASH, str(DISPATCH)], capture_output=True, text=True, env=env, timeout=90
    )
    _skip_if_bash_cannot_fork(result)
    recorded, state = _collect(repo, calls)
    assert result.returncode == 0, result.stderr
    assert PROMOTION_CMD not in recorded
    assert state["reason_code"] == "EXECUTION_MODE_AMBIGUOUS"


@requires_bash
def test_owner_env_is_the_last_resort_source(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: a legacy layout whose core reads
    `.env` still resolves a mode."""
    repo, calls, env = _sandbox(tmp_path, mode=None, owner_mode="isolated")
    result = subprocess.run(
        [BASH, str(DISPATCH)], capture_output=True, text=True, env=env, timeout=90
    )
    _skip_if_bash_cannot_fork(result)
    recorded, state = _collect(repo, calls)
    assert result.returncode == 0, result.stderr
    assert PROMOTION_CMD in recorded
    assert state["state"] == "DISPATCHED"

