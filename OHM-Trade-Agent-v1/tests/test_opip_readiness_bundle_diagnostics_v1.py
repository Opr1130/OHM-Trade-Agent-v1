"""Sections 7-9 of the readiness bundle must not mislead (ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1).

The bundle is run against a stub ``docker`` on PATH; nothing touches a real host.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = APP_ROOT / "tools" / "opip_readiness_bundle.sh"
requires_linux = pytest.mark.skipif(
    shutil.which("bash") is None or not Path("/proc/self/cmdline").exists(), reason="bash and /proc required")

STUB = """#!/bin/sh
case "$1" in
  ps) [ "${STUB_NO_CORE:-0}" = 1 ] && exit 0; echo core123 ;;
  exec) [ "${STUB_EXEC_FAIL:-0}" = 1 ] && exit 1; shift; shift; exec "$@" ;;
  inspect) [ -n "$STUB_ENV_FILE" ] && cat "$STUB_ENV_FILE" ;;
  *) exit 1 ;;
esac
"""


def _run(tmp_path: Path, *, env_lines: str = "", installed: Path | None = None, extra: dict | None = None) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "docker"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    envfile = tmp_path / "core.env"
    envfile.write_text(env_lines, encoding="utf-8")
    app = tmp_path / "app"
    (app / "deploy" / "remote").mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path), "OPIP_APP_ROOT": str(app),
        "OPIP_CANONICAL_DB": str(tmp_path / "none.sqlite3"), "OPIP_CYCLE_LOCK": str(tmp_path / "no.lock"),
        "STUB_ENV_FILE": str(envfile),
        "OPIP_INSTALLED_CONTROLLER": str(installed or tmp_path / "absent-controller"),
    }
    env.update(extra or {})
    done = subprocess.run(["bash", str(BUNDLE)], env=env, capture_output=True, text=True, timeout=120, check=False)
    return done.stdout + done.stderr


def _section(out: str, number: str) -> str:
    start = out.index(f"===== {number} ")
    end = out.find("\n===== ", start + 1)
    return out[start:] if end == -1 else out[start:end]


def _count(section: str) -> int | None:
    match = re.search(r"in-container run_cycle count: (\d+) \(from /proc\)", section)
    return int(match.group(1)) if match else None


@requires_linux
def test_ac_001_a_real_run_cycle_process_is_counted_from_proc(tmp_path):
    """ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1/AC-001: a live run_cycle process raises the count by exactly one, without ps.

    The stub executes the counting script on the test host, whose own process table may
    already hold a run_cycle-named command line, so the assertion is on the difference.
    """
    base = _section(_run(tmp_path), "7")
    baseline = _count(base)
    assert baseline is not None and "ps: not found" not in base
    proc = subprocess.Popen(["bash", "-c", "exec -a run_cycle sleep 30"])
    counted = None
    try:
        for _ in range(20):  # the child needs a moment to exec under its new name
            counted = _count(_section(_run(tmp_path), "7"))
            if counted == baseline + 1:
                break
            time.sleep(0.25)
    finally:
        proc.kill()
        proc.wait()
    assert counted == baseline + 1


@requires_linux
def test_ac_001_no_process_is_zero_and_a_failed_exec_is_unavailable_not_zero(tmp_path):
    """ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1/AC-001: a count is only printed when the scan ran; a failure is never a number."""
    failed = _section(_run(tmp_path, extra={"STUB_EXEC_FAIL": "1"}), "7")
    assert "in-container run_cycle count: UNAVAILABLE (exec failed)" in failed and _count(failed) is None
    missing = _section(_run(tmp_path, extra={"STUB_NO_CORE": "1"}), "7")
    assert "in-container run_cycle count: UNAVAILABLE (core container not found)" in missing and _count(missing) is None


@requires_linux
def test_ac_002_new_legacy_and_unreadable_controllers_are_told_apart(tmp_path):
    """ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1/AC-002: the installed controller is judged NEW, LEGACY or UNAVAILABLE."""
    new = tmp_path / "new"
    new.write_text("WRITER_HEALTH_TIMEOUT_SECONDS=180\nWRITER_HEALTH_POLL_SECONDS=2\nwait_writer_health() {\n  :\n}\n")
    old = tmp_path / "old"
    old.write_text("wait_writer_health() {\n  local cid status\n  for _ in $(seq 1 30); do\n    sleep 2\n  done\n}\n")
    out_new = _section(_run(tmp_path, installed=new), "8")
    assert f"{new}: WRITER_HEALTH_TIMEOUT_SECONDS=180 WRITER_HEALTH_POLL_SECONDS=2 verdict=NEW" in out_new
    out_old = _section(_run(tmp_path, installed=old), "8")
    assert "verdict=LEGACY" in out_old and "seq 1 30" in out_old
    out_missing = _section(_run(tmp_path), "8")
    assert "absent-controller: UNAVAILABLE (not readable)" in out_missing


@requires_linux
def test_ac_003_unset_is_distinguished_from_disabled_and_secrets_are_redacted(tmp_path):
    """ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1/AC-003: UNSET is never presented as OFF; off values and redaction hold."""
    env_lines = "OPIP_COMMITTEE_MODE=off\nOPIP_PAPER_V2_MODE=off\nOPIP_LEARNING_ENABLED=false\nOPIP_AUTO_TOKEN=abc123\n"
    out = _section(_run(tmp_path, env_lines=env_lines), "9")
    assert "OPIP_COMMITTEE_MODE=off" in out and "OPIP_PAPER_V2_MODE=off" in out
    assert "OPIP_RELEASE_PROFILE=<UNSET> (code default applies; not evidence of OFF)" in out
    assert "OPIP_UNIFIED_CYCLE_BUDGET_SECONDS=<UNSET>" in out
    assert "OPIP_LEARNING_ENABLED=false" in out
    assert "abc123" not in out and "TOKEN=REDACTED" in out
    assert "UNAVAILABLE: core container not found" in _section(_run(tmp_path, extra={"STUB_NO_CORE": "1"}), "9")
