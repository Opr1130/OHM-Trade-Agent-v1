"""Hermetic regression tests for the PostgreSQL backup repair and restore-drill guard."""

import hashlib
import os
import time
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "deploy/analytics/opip-postgres-backup.sh"
DRILL = ROOT / "deploy/analytics/opip-postgres-restore-drill.sh"
GIB = 1024**3

DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_DIR/docker.calls"
case "$*" in
  *pg_dump*)
    [[ -n "${STUB_DUMP_FAIL:-}" ]] && { echo "pg_dump: boom" >&2; exit 3; }
    [[ -n "${STUB_DUMP_EMPTY:-}" ]] && exit 0
    if [[ -n "${STUB_DUMP_BYTES:-}" ]]; then head -c "$STUB_DUMP_BYTES" /dev/zero; else printf 'PGDMP-stub-archive'; fi
    ;;
  *"pg_restore --list"*)
    cat > /dev/null
    exit "${STUB_TOC_RC:-0}"
    ;;
esac
"""
DF_STUB = """#!/usr/bin/env bash
echo "Filesystem 1-blocks Used Available Capacity Mounted"
echo "stub 0 0 ${STUB_FREE_BYTES} 0% /"
"""


def _setup(tmp_path: Path, free_bytes: int, **extra: str):
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("df", DF_STUB)):
        (stub / name).write_text(body, encoding="utf-8")
        (stub / name).chmod(0o755)
    env_file = tmp_path / "platform.env"
    env_file.write_text("OPIP_POSTGRES_ADMIN_PASSWORD=not-a-real-secret\n", encoding="utf-8")
    root = tmp_path / "backups"
    state = tmp_path / "state"
    state.mkdir()
    env = os.environ.copy()
    env.update(
        PATH=f"{stub}:{env['PATH']}",
        STUB_DIR=str(stub),
        STUB_FREE_BYTES=str(free_bytes),
        OPIP_BACKUP_ENV_FILE=str(env_file),
        OPIP_BACKUP_ROOT=str(root),
        OPIP_BACKUP_STATE_ROOT=str(state),
        OPIP_BACKUP_LOCK_FILE=str(tmp_path / "backup.lock"),
        OPIP_DRILL_LOCK_FILE=str(tmp_path / "drill.lock"),
        OPIP_PLANE_LOCK_FILE=str(tmp_path / "plane.lock"),
        **extra,
    )
    return env, stub, root


def _run(script: Path, env: dict):
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, check=False, timeout=20
    )


def _calls(stub: Path) -> str:
    log = stub / "docker.calls"
    return log.read_text(encoding="utf-8") if log.exists() else ""


@pytest.mark.acceptance
def test_backup_uses_docker_exec_and_writes_verified_dump(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-001: a checksummed, TOC-verified dump is written via docker exec."""
    env, stub, root = _setup(tmp_path, 10 * GIB)
    result = _run(BACKUP, env)
    assert result.returncode == 0, result.stderr
    dumps = list(root.glob("opip-postgres-*.dump"))
    assert len(dumps) == 1
    sidecar = Path(f"{dumps[0]}.sha256")
    assert hashlib.sha256(dumps[0].read_bytes()).hexdigest() in sidecar.read_text()
    calls = _calls(stub)
    assert "compose" not in calls
    assert "pg_dump" in calls and "pg_restore --list" in calls
    assert "not-a-real-secret" not in calls, "the password must not appear in docker argv"
    assert not list(root.glob(".*.tmp"))


@pytest.mark.acceptance
def test_backup_failure_leaves_no_partial_or_final_file(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-002: failed, empty or unreadable dumps are discarded and exit non-zero."""
    for extra in ({"STUB_DUMP_FAIL": "1"}, {"STUB_DUMP_EMPTY": "1"}, {"STUB_TOC_RC": "1"}):
        case = tmp_path / next(iter(extra))
        case.mkdir()
        env, _, root = _setup(case, 10 * GIB, **extra)
        result = _run(BACKUP, env)
        assert result.returncode != 0, extra
        assert list(root.iterdir()) == [], extra


@pytest.mark.acceptance
def test_backup_refuses_below_free_space_floor_before_any_docker_call(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-003: below the 2 GiB floor plus expected size nothing is written or deleted."""
    env, stub, root = _setup(tmp_path, 2 * GIB)
    result = _run(BACKUP, env)
    assert result.returncode == 75
    assert "CRITICAL" in result.stderr
    assert _calls(stub) == ""
    assert list(root.iterdir()) == []


@pytest.mark.acceptance
def test_restore_drill_refuses_low_space_before_touching_the_database(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-004: the drill refuses without a free-space margin and never calls docker."""
    env, stub, root = _setup(tmp_path, 2 * GIB)
    root.mkdir()
    dump = root / "opip-postgres-20260101T000000Z.dump"
    dump.write_bytes(b"x" * 1024)
    Path(f"{dump}.sha256").write_text(
        f"{hashlib.sha256(dump.read_bytes()).hexdigest()}  {dump}\n", encoding="utf-8"
    )
    result = _run(DRILL, env)
    assert result.returncode == 75
    assert "CRITICAL" in result.stderr
    assert _calls(stub) == ""


@pytest.mark.acceptance
def test_scripts_never_invoke_docker_compose():
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-005: compose interpolation is the root cause and must not return."""
    for script in (BACKUP, DRILL):
        code = [
            line for line in script.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        assert not any("docker compose" in line or "COMPOSE" in line for line in code), script.name


@pytest.mark.acceptance
def test_backup_never_deletes_existing_dumps(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-006: even dumps older than 30 days are preserved by a backup run."""
    env, _, root = _setup(tmp_path, 10 * GIB)
    root.mkdir()
    old = root / "opip-postgres-20250101T000000Z.dump"
    old.write_bytes(b"old")
    sidecar = Path(f"{old}.sha256")
    sidecar.write_text("x  old\n", encoding="utf-8")
    ancient = time.time() - 400 * 86400
    os.utime(old, (ancient, ancient))
    os.utime(sidecar, (ancient, ancient))
    assert _run(BACKUP, env).returncode == 0
    assert old.exists() and sidecar.exists()
    code = [l for l in BACKUP.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]
    assert not any("-delete" in l or "rm -f" in l and "tmp" not in l for l in code)


@pytest.mark.acceptance
def test_oversized_dump_is_killed_at_the_free_space_floor(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-007: a dump larger than the headroom above 2 GiB cannot be written."""
    mib = 1024 * 1024
    env, _, root = _setup(
        tmp_path, 2 * GIB + 2 * mib,
        STUB_DUMP_BYTES=str(8 * mib), OPIP_BACKUP_DEFAULT_DUMP_BYTES=str(mib),
    )
    result = _run(BACKUP, env)
    assert result.returncode != 0
    assert list(root.iterdir()) == []


@pytest.mark.acceptance
def test_expected_size_uses_twice_the_previous_dump(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-003: the preflight reserves a 2x growth margin over the last dump."""
    mib = 1024 * 1024
    env, stub, root = _setup(tmp_path, 2 * GIB + 150 * mib, OPIP_BACKUP_DEFAULT_DUMP_BYTES=str(mib))
    root.mkdir()
    (root / "opip-postgres-20260101T000000Z.dump").write_bytes(b"y" * (100 * mib))
    result = _run(BACKUP, env)
    assert result.returncode == 75, "100 MiB previous dump needs 200 MiB headroom, only 150 MiB free"
    assert _calls(stub) == ""
