"""Real-Docker fault-injection capacity checks (skipped without a daemon).

These prove the *mechanisms* the dispatcher depends on: that a labelled
container can be found by label, that exit state including the OOM flag is
inspectable after the process dies, and that removal actually reaps it. They are
deliberately tiny so they are safe to run on any Docker host.

They are skipped where no Docker daemon exists; the environment used for this
branch had none, so they have not been executed here.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid

import pytest

DOCKER = shutil.which("docker")


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [DOCKER, *args], capture_output=True, text=True, timeout=120, check=check
    )


def _daemon_available() -> bool:
    if DOCKER is None:
        return False
    try:
        return _docker("info", "--format", "{{.ServerVersion}}", check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(
    not _daemon_available(), reason="no Docker daemon available for fault injection"
)


def test_label_discovery_and_cleanup_of_a_dead_container():
    attempt = f"SCAN-it-{uuid.uuid4().hex[:8]}"
    name = f"opip-it-{attempt}"
    try:
        _docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"opip.attempt_id={attempt}",
            "--memory",
            "64m",
            "alpine:3.20",
            "sh",
            "-c",
            "exit 7",
        )
        for _ in range(30):
            ids = _docker("ps", "-aq", "--filter", f"label=opip.attempt_id={attempt}").stdout.split()
            if ids:
                break
            time.sleep(0.2)
        assert ids, "a labelled container must be discoverable by label"

        for _ in range(50):
            running = _docker("inspect", "-f", "{{.State.Running}}", ids[0]).stdout.strip()
            if running == "false":
                break
            time.sleep(0.2)

        state = _docker("inspect", "-f", "{{json .State}}", ids[0]).stdout
        assert '"ExitCode":7' in state.replace(" ", "")
        assert "OOMKilled" in state, "exit evidence must expose the OOM flag"

        _docker("rm", "-f", ids[0])
        remaining = _docker("ps", "-aq", "--filter", f"label=opip.attempt_id={attempt}").stdout
        assert remaining.strip() == "", "removal must reap the container"
    finally:
        _docker("rm", "-f", name, check=False)


def test_oom_killed_flag_is_observable():
    attempt = f"SCAN-oom-{uuid.uuid4().hex[:8]}"
    name = f"opip-oom-{attempt}"
    try:
        _docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"opip.attempt_id={attempt}",
            "--memory",
            "32m",
            "python:3.12-slim",
            "python",
            "-c",
            # Allocate real memory; /dev/zero writes to /dev/null would not.
            "import time; b = bytearray(200 * 1024 * 1024); time.sleep(60)",
        )
        flags: set[str] = set()
        for _ in range(60):
            flags.add(_docker("inspect", "-f", "{{.State.OOMKilled}}", name).stdout.strip())
            if "true" in flags:
                break
            time.sleep(0.5)
        assert "true" in flags, "a memory-bounded workload must report OOMKilled"
    finally:
        _docker("rm", "-f", name, check=False)
