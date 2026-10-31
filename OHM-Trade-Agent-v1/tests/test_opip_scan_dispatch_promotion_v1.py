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

import os
import pathlib
import shutil
import subprocess

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


# --- Behaviour: the shipped resolver, extracted verbatim --------------------
# The resolver is exercised on its own rather than by launching the whole
# dispatcher: the dispatcher's promotion branch backgrounds a detached job, and
# a test harness must never be able to hang the suite on an inherited file
# descriptor. The wiring from resolver -> promotion is pinned statically above.


def _run_resolver(tmp_path: pathlib.Path, *, mode, owner_mode=None, process_mode=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    if mode is not None:
        (repo / ".env.core").write_text(f"{KEY}={mode}\nAPP_ENV=production\n", encoding="utf-8")
    if owner_mode is not None:
        (repo / ".env").write_text(f"{KEY}={owner_mode}\n", encoding="utf-8")

    env = {key: value for key, value in os.environ.items() if key != KEY}
    if process_mode is not None:
        env[KEY] = process_mode
    script = (
        "set -Eeuo pipefail\n"
        f'REPO_DIR="{repo.as_posix()}"\n'
        f"{_dispatch_function('resolve_exec_mode')}\n"
        'resolve_exec_mode\n'
    )
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    _skip_if_bash_cannot_fork(result)
    return result


@requires_bash
def test_isolated_is_resolved_from_the_derived_core_env(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: cron carries no mode, the derived
    file does - the resolver must return isolated."""
    result = _run_resolver(tmp_path, mode="isolated")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"


@requires_bash
@pytest.mark.parametrize("mode", ["legacy_inprocess", "suspended"])
def test_legacy_and_suspended_resolve_verbatim(tmp_path, mode):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: authorised modes pass through, so
    the dispatcher can treat them as explicit no-ops."""
    result = _run_resolver(tmp_path, mode=mode)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == mode


@requires_bash
def test_missing_mode_resolves_empty(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: no source carries the key, so the
    dispatcher records EXECUTION_MODE_UNSET instead of guessing."""
    result = _run_resolver(tmp_path, mode=None)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@requires_bash
def test_ambiguous_mode_is_never_guessed(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: two definitions are ambiguous."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.core").write_text(
        f"{KEY}=isolated\n{KEY}=suspended\n", encoding="utf-8"
    )
    script = (
        "set -Eeuo pipefail\n"
        f'REPO_DIR="{repo.as_posix()}"\n'
        f"{_dispatch_function('resolve_exec_mode')}\n"
        'resolve_exec_mode\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    _skip_if_bash_cannot_fork(result)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AMBIGUOUS"


@requires_bash
def test_process_env_overrides_the_derived_env(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: an explicit fleet override wins."""
    result = _run_resolver(tmp_path, mode="legacy_inprocess", process_mode="isolated")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"


@requires_bash
def test_owner_env_is_the_last_resort_source(tmp_path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-007: a legacy layout whose core reads
    `.env` still resolves a mode."""
    result = _run_resolver(tmp_path, mode=None, owner_mode="isolated")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"



# --- Deployment rollback: installed dispatcher belongs to the transaction ----

DEPLOY = APP / "deploy" / "remote" / "ohm-deploy"


def _deploy_function(name: str) -> str:
    lines = _text(DEPLOY).splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"{name}() {{")), None
    )
    assert start is not None, f"{name} is missing from {DEPLOY.name}"
    end = start
    while lines[end] != "}":
        end += 1
    return "\n".join(lines[start : end + 1])


def test_dispatcher_is_in_both_deploy_snapshot_and_restore_contracts():
    """AC-007: the host dispatcher must roll back with scheduler state."""
    text = _text(DEPLOY)
    assert 'SCAN_DISPATCH_DST="/usr/local/bin/opip-scan-dispatch"' in text
    mapping = "$SCAN_DISPATCH_DST|remote-op-scan-dispatch"
    assert _deploy_function("snapshot_scheduler_state").count(mapping) == 1
    assert _deploy_function("restore_scheduler_state").count(mapping) == 1


@requires_bash
@pytest.mark.parametrize("previous_exists", [True, False])
def test_deployment_rollback_restores_dispatcher_presence(tmp_path, previous_exists):
    """AC-007: rollback restores prior dispatcher bytes or removes a new install.

    Exercise the real deploy functions, substituting only sandbox destinations
    and a harmless crontab stub. Never touch real cron, installed scripts or
    production state.
    """
    state = tmp_path / "state"
    cron = tmp_path / "cron"
    state.mkdir()
    cron.mkdir()
    dest = tmp_path / "scan-dispatch"
    # Replace only the real cron directory with a sandbox; every other
    # install target is defined as a separate file under tmp_path.
    functions = "\n".join(
        _deploy_function(name).replace("/etc/cron.d/", f"{cron.as_posix()}/")
        for name in (
            "replace_file_atomically",
            "snapshot_scheduler_state",
            "restore_scheduler_state",
        )
    )
    setup = "\n".join(
        f'{name}="{(tmp_path / name.lower()).as_posix()}"'
        for name in (
            "DEPLOY_SCRIPT_DST",
            "SSH_GATEWAY_DST",
            "LEARNING_READER_DST",
            "LEARNING_DIAGNOSTICS_DST",
        )
    )
    script = (
        "set -Eeuo pipefail\n"
        f'STATE_DIR="{state.as_posix()}"\n'
        f'SCAN_DISPATCH_DST="{dest.as_posix()}"\n'
        'SCHEDULER_SNAPSHOT=""\n'
        f"{setup}\n"
        "crontab() { return 1; }\n"
        f"{functions}\n"
        + ('printf "previous\\n" > "$SCAN_DISPATCH_DST"\n' if previous_exists else "")
        + 'snapshot_scheduler_state\n'
        + 'printf "candidate\\n" > "$SCAN_DISPATCH_DST"\n'
        + 'restore_scheduler_state\n'
        + ('grep -qx previous "$SCAN_DISPATCH_DST"\n' if previous_exists else 'test ! -e "$SCAN_DISPATCH_DST"\n')
    )
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    _skip_if_bash_cannot_fork(result)
    assert result.returncode == 0, result.stderr
