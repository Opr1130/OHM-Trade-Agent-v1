"""Real-Docker fault-injection capacity checks (skipped without a daemon).

These prove the *mechanisms* the dispatcher depends on: that a labelled
container can be found by label, that exit state including the OOM flag is
inspectable after the process dies, and that removal actually reaps it. They are
deliberately tiny so they are safe to run on any Docker host.

``docker inspect`` publishes ``State.OOMKilled`` only once the container has
*exited*, and the daemon attributes the kill from the cgroup OOM event. The OOM
observation therefore waits for the exit instead of polling a pre-exit flag, and
the workload allocates only after the container has been observed running: an
OOM kill that races container start is not attributed reliably. This matters
beyond the test - ``classify_exit`` in ``app/services/scan_supervisor.py`` reads
exactly this flag, so a false negative there reports a cgroup OOM as
``DEADLINE_KILLED`` instead of ``OOM_KILLED``.

The Docker-dependent checks are skipped where no daemon exists. The evidence
formatter they use is covered by ordinary tests, so a daemon-less host still
proves the diagnostics contract rather than skipping it silently.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

DOCKER = shutil.which("docker")

#: The cap the OOM workload runs under: far below what it asks for, so the kill
#: is caused by our own allocation, and small enough to be safe on any host.
OOM_MEMORY_LIMIT_BYTES = 32 * 1024 * 1024

#: Exit code the workload raises if it ever runs to completion. It is reachable
#: only when the daemon did not enforce ``--memory``.
LIMIT_NOT_ENFORCED_EXIT = 66

#: The exit code the kernel leaves behind when it kills the container's PID 1.
SIGKILL_EXIT = 137

#: Seconds the workload waits before allocating, so the daemon has already
#: observed a fully running container when the OOM kill arrives.
OOM_STARTUP_SETTLE_SECONDS = 2

#: Upper bound on waiting for the container to die. The workload needs the
#: settle plus a fraction of a second; this only guards a hung daemon.
OOM_EXIT_TIMEOUT_SECONDS = 90


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


requires_docker = pytest.mark.skipif(
    not _daemon_available(), reason="no Docker daemon available for fault injection"
)


def _inspect(name: str, template: str) -> str | None:
    """Return one ``docker inspect`` field, or None when it is unavailable."""
    completed = _docker("inspect", "-f", template, name, check=False)
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _container_state(name: str) -> dict[str, Any] | None:
    raw = _inspect(name, "{{json .State}}")
    if raw is None:
        return None
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return state if isinstance(state, dict) else None


def _cgroup_oom_kill_count(container_id: str | None) -> int | None:
    """Read the kernel's own OOM counter for the container's cgroup.

    ``State.OOMKilled`` is a *derived* value. The counter the kernel increments
    when it kills a task is the primary evidence and survives the process, so it
    is what separates "the daemon did not report the flag" from "the kernel never
    killed it". It is reported as unknown rather than asserted when the path or
    the permission is unavailable, because the verdict must rest on the state the
    scanner supervisor actually consumes.
    """
    if not container_id:
        return None
    events = (
        Path(f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope/memory.events"),
        Path(f"/sys/fs/cgroup/docker/{container_id}/memory.events"),
    )
    oom_control = (
        Path(f"/sys/fs/cgroup/memory/docker/{container_id}/memory.oom_control"),
        Path(
            "/sys/fs/cgroup/memory/system.slice/"
            f"docker-{container_id}.scope/memory.oom_control"
        ),
    )
    try:
        for path in events:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition(" ")
                if key == "oom_kill":
                    return int(value)
        for control in oom_control:
            if not control.is_file():
                continue
            for line in control.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("oom_kill"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _format_oom_evidence(
    *,
    state: Mapping[str, Any] | None,
    memory_limit: int | None,
    cgroup_oom_kills: int | None,
) -> str:
    """Render everything the OOM observation measured, for the failure message.

    A bare "OOMKilled was not true" cannot be acted on: it does not say whether
    the limit was applied, whether the container ever exited, or whether the
    kernel killed it at all. The rendered values are recorded, never asserted, so
    they cannot mask a genuine failure.
    """
    observed = state or {}
    return (
        "oom_observation_incomplete "
        f"ExitCode={observed.get('ExitCode')} "
        f"OOMKilled={observed.get('OOMKilled')} "
        f"Running={observed.get('Running')} "
        f"Error={str(observed.get('Error') or '')!r} "
        f"MemoryLimitBytes={memory_limit} "
        f"cgroup_oom_kill={cgroup_oom_kills}"
    )


@requires_docker
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


@requires_docker
@pytest.mark.acceptance
def test_oom_killed_flag_is_observable():
    """ATDD-SCANNER-OOM-OBSERVABILITY-M01/AC-001: a real cgroup OOM is observable.

    The container's own state is what proves the mechanism, so this asserts the
    whole chain the scanner supervisor depends on: the memory cap was applied,
    the container really exited, the kernel killed PID 1 (SIGKILL), and the
    daemon published the OOM flag the supervisor reads.
    """
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
            f"{OOM_MEMORY_LIMIT_BYTES // (1024 * 1024)}m",
            "python:3.12-slim",
            "python",
            "-c",
            # Settle before allocating: the daemon must already have observed a
            # running container, or the OOM event is not attributed to it.
            "import time\n"
            f"time.sleep({OOM_STARTUP_SETTLE_SECONDS})\n"
            # Touch each page: a zero-filled allocation can remain lazily mapped.
            "chunks = []\n"
            "for _ in range(32):\n"
            "    chunk = bytearray(16 * 1024 * 1024)\n"
            "    for offset in range(0, len(chunk), 4096):\n"
            "        chunk[offset] = 1\n"
            "    chunks.append(chunk)\n"
            # Bounded at 512 MiB so a daemon that ignored --memory can never pin
            # the host. This sentinel is reachable only in that case.
            f"raise SystemExit({LIMIT_NOT_ENFORCED_EXIT})\n",
        )

        state: dict[str, Any] | None = None
        deadline = time.monotonic() + OOM_EXIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            state = _container_state(name)
            if state is not None and state.get("Running") is False:
                break
            time.sleep(0.5)

        raw_limit = _inspect(name, "{{.HostConfig.Memory}}")
        memory_limit = int(raw_limit) if raw_limit and raw_limit.isdigit() else None
        evidence = _format_oom_evidence(
            state=state,
            memory_limit=memory_limit,
            cgroup_oom_kills=_cgroup_oom_kill_count(_inspect(name, "{{.Id}}")),
        )

        assert memory_limit == OOM_MEMORY_LIMIT_BYTES, evidence
        assert state is not None and state.get("Running") is False, evidence
        assert state.get("ExitCode") == SIGKILL_EXIT, evidence
        assert state.get("OOMKilled") is True, evidence
    finally:
        _docker("rm", "-f", name, check=False)


@pytest.mark.acceptance
def test_oom_evidence_reports_exit_limit_and_cgroup_counter():
    """ATDD-SCANNER-OOM-OBSERVABILITY-M01/AC-002: a revisit records the evidence."""
    evidence = _format_oom_evidence(
        state={
            "ExitCode": SIGKILL_EXIT,
            "OOMKilled": False,
            "Running": False,
            "Error": "",
        },
        memory_limit=OOM_MEMORY_LIMIT_BYTES,
        cgroup_oom_kills=1,
    )

    for fragment in (
        f"ExitCode={SIGKILL_EXIT}",
        "OOMKilled=False",
        "Running=False",
        f"MemoryLimitBytes={OOM_MEMORY_LIMIT_BYTES}",
        "cgroup_oom_kill=1",
    ):
        assert fragment in evidence


@pytest.mark.acceptance
def test_oom_evidence_tolerates_an_unreadable_container():
    """ATDD-SCANNER-OOM-OBSERVABILITY-M01/AC-002: absent evidence is reported."""
    evidence = _format_oom_evidence(
        state=None, memory_limit=None, cgroup_oom_kills=None
    )

    assert "ExitCode=None" in evidence
    assert "MemoryLimitBytes=None" in evidence
    assert "cgroup_oom_kill=None" in evidence


@pytest.mark.acceptance
def test_scope_contract_declares_the_active_increment():
    """ATDD-SCANNER-OOM-OBSERVABILITY-M01/AC-003: the scope is declared."""
    app_root = Path(__file__).resolve().parents[1]
    pointer = (app_root / "docs" / "atdd" / "ACTIVE_INCREMENT").read_text(
        encoding="utf-8"
    ).strip()
    assert pointer == "ATDD-SCANNER-OOM-OBSERVABILITY-M01"
    contract_path = app_root / "docs" / "atdd" / "scope-contracts" / f"{pointer}.md"
    assert contract_path.is_file()
    contract = contract_path.read_text(encoding="utf-8")
    assert contract.startswith(f"INCREMENT:\n{pointer}")
    for section in (
        "OWNER-APPROVED INTENT:",
        "ARCHITECTURE REFERENCES:",
        "APPROVED ACCEPTANCE CRITERIA:",
        "EXPLICITLY OUT OF SCOPE:",
        "FROZEN BOUNDARIES:",
        "ACCEPTANCE TEST TRACEABILITY:",
        "IMPLEMENTATION MAP:",
        "DEFERRED DISCOVERIES:",
        "UNAPPROVED SCOPE CHANGES:",
    ):
        assert section in contract
    assert contract.rstrip().endswith("NONE")
    # The increment is test-only: the scanner supervisor's OOM classification is
    # frozen by the contract, not corrected by it.
    assert "app/services/scan_supervisor.py" in contract


