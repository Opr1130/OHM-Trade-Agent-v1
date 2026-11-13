"""Hermetic regression tests for the PostgreSQL backup repair, retention and isolated restore drill."""

import datetime as dt
import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "deploy/analytics/opip-postgres-backup.sh"
DRILL = ROOT / "deploy/analytics/opip-postgres-restore-drill.sh"
GIB = 1024**3
MIB = 1024**2

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
  *"pg_restore --exit-on-error"*)
    cat > /dev/null
    exit "${STUB_RESTORE_RC:-0}"
    ;;
  *psql*) echo 12 ;;
esac
"""
DF_STUB = """#!/usr/bin/env bash
echo "Filesystem 1-blocks Used Available Capacity Mounted"
echo "stub 0 0 ${STUB_FREE_BYTES} 0% /"
"""
CHOWN_STUB = "#!/usr/bin/env bash\nexit 0\n"


def _setup(tmp_path: Path, free_bytes: int, **extra: str):
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("df", DF_STUB), ("chown", CHOWN_STUB)):
        (stub / name).write_text(body, encoding="utf-8")
        (stub / name).chmod(0o755)
    env_file = tmp_path / "platform.env"
    env_file.write_text("OPIP_POSTGRES_ADMIN_PASSWORD=not-a-real-secret\n", encoding="utf-8")
    root = tmp_path / "backups"
    state = tmp_path / "state"
    state.mkdir()
    live = tmp_path / "livedata"
    live.mkdir()
    (live / "base").write_bytes(b"\0" * MIB)
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
        OPIP_DRILL_LIVE_DATA_DIR=str(live),
        OPIP_DRILL_WORK_ROOT=str(tmp_path / "work"),
        **extra,
    )
    return env, stub, root


def _run(script: Path, env: dict):
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, check=False, timeout=30
    )


def _calls(stub: Path) -> str:
    log = stub / "docker.calls"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def _verified(root: Path, day: dt.date, hour: str = "030000") -> str:
    name = f"opip-postgres-{day:%Y%m%d}T{hour}Z.dump"
    (root / name).write_bytes(b"d")
    (root / f"{name}.sha256").write_text("x\n", encoding="utf-8")
    (root / f"{name}.verified").write_text("toc_verified_at_utc=x\n", encoding="utf-8")
    return name


def _dump_with_checksum(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    dump = root / "opip-postgres-20260101T000000Z.dump"
    dump.write_bytes(b"x" * 1024)
    Path(f"{dump}.sha256").write_text(
        f"{hashlib.sha256(dump.read_bytes()).hexdigest()}  {dump}\n", encoding="utf-8"
    )
    return dump


@pytest.mark.acceptance
def test_backup_uses_docker_exec_and_writes_verified_dump(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-001: a checksummed, TOC-verified dump is written via docker exec."""
    env, stub, root = _setup(tmp_path, 10 * GIB)
    result = _run(BACKUP, env)
    assert result.returncode == 0, result.stderr
    dumps = list(root.glob("opip-postgres-*.dump"))
    assert len(dumps) == 1
    assert Path(f"{dumps[0]}.verified").is_file()
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
def test_expected_size_uses_twice_the_previous_dump(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-003: the preflight reserves a 2x growth margin over the last dump."""
    env, stub, root = _setup(tmp_path, 2 * GIB + 150 * MIB, OPIP_BACKUP_DEFAULT_DUMP_BYTES=str(MIB))
    root.mkdir()
    (root / "opip-postgres-20260101T000000Z.dump").write_bytes(b"y" * (100 * MIB))
    result = _run(BACKUP, env)
    assert result.returncode == 75, "100 MiB previous dump needs 200 MiB headroom, only 150 MiB free"
    assert _calls(stub) == ""


@pytest.mark.acceptance
def test_restore_drill_refuses_without_measured_space_before_creating_anything(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-004: free space must cover 3 GiB plus 1.25x the live data size, in bytes."""
    env, stub, root = _setup(tmp_path, 3 * GIB)
    _dump_with_checksum(root)
    result = _run(DRILL, env)
    assert result.returncode == 75
    assert "CRITICAL" in result.stderr
    assert _calls(stub) == ""
    assert not (tmp_path / "work").exists()


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
def test_backup_never_deletes_unverified_dumps(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-006: existing unmarked dumps, even very old, are never deleted."""
    env, _, root = _setup(tmp_path, 10 * GIB)
    root.mkdir()
    legacy = []
    for i in range(15):
        f = root / f"opip-postgres-2025{(i % 12) + 1:02d}01T000000Z.dump"
        f.write_bytes(b"legacy")
        Path(f"{f}.sha256").write_text("x\n", encoding="utf-8")
        old = time.time() - 400 * 86400
        os.utime(f, (old, old))
        legacy.append(f)
    assert _run(BACKUP, env).returncode == 0
    assert all(f.exists() and Path(f"{f}.sha256").exists() for f in legacy)
    code = [l for l in BACKUP.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]
    assert not any("-delete" in l for l in code)


@pytest.mark.acceptance
def test_oversized_dump_is_killed_at_the_free_space_floor(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-007: a dump larger than the headroom above 2 GiB cannot be written."""
    env, _, root = _setup(
        tmp_path, 2 * GIB + 2 * MIB,
        STUB_DUMP_BYTES=str(8 * MIB), OPIP_BACKUP_DEFAULT_DUMP_BYTES=str(MIB),
    )
    result = _run(BACKUP, env)
    assert result.returncode != 0
    assert list(root.iterdir()) == []


def _expected_keep(names: list[str], drilled: str | None) -> set[str]:
    ordered = sorted(names, reverse=True)
    keep = set(ordered[:3])
    weeks: list[tuple[int, int]] = []
    for name in ordered:
        day = dt.datetime.strptime(name[len("opip-postgres-"):][:8], "%Y%m%d").date()
        week = day.isocalendar()[:2]
        if week not in weeks and len(weeks) < 4:
            weeks.append(week)
            keep.add(name)
    if drilled:
        keep.add(drilled)
    return keep


@pytest.mark.acceptance
def test_retention_keeps_three_daily_four_weekly_and_the_drilled_dump(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-008: newest 3 plus 4 weekly verified dumps survive, with the restore-proven dump."""
    env, _, root = _setup(tmp_path, 10 * GIB)
    root.mkdir()
    start = dt.date(2026, 1, 1)
    names = [_verified(root, start + dt.timedelta(days=3 * i)) for i in range(12)]
    # Same-day dumps older than the new one are not weekly picks, so only the
    # newest-3 rule decides whether the second of them survives.
    today = dt.datetime.now(dt.timezone.utc).date()
    names += [_verified(root, today, hour) for hour in ("000001", "000002", "000003")]
    legacy = root / "opip-postgres-20250101T000000Z.dump"
    legacy.write_bytes(b"legacy")
    drilled = names[0]
    state = Path(env["OPIP_BACKUP_STATE_ROOT"])
    (state / "last-restore-drill.env").write_text(f"backup_file={drilled}\n", encoding="utf-8")
    assert _run(BACKUP, env).returncode == 0
    new = [p.name for p in root.glob("opip-postgres-*.dump.verified") if p.name[:-9] not in names]
    assert len(new) == 1
    all_names = names + [new[0][:-9]]
    expected = _expected_keep(all_names, drilled)
    survivors = {p.name[:-9] for p in root.glob("opip-postgres-*.dump.verified")}
    assert survivors == expected
    for name in all_names:
        assert (root / name).exists() == (name in expected)
        assert (root / f"{name}.sha256").exists() == (name in expected)
    assert legacy.exists()
    assert (root / drilled).exists()


@pytest.mark.acceptance
def test_retention_refuses_to_prune_more_than_ten_dumps(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-008: a large prune needs a reviewed manifest, so nothing is pruned."""
    env, _, root = _setup(tmp_path, 10 * GIB)
    root.mkdir()
    start = dt.date(2025, 1, 1)
    names = [_verified(root, start + dt.timedelta(days=7 * i)) for i in range(25)]
    result = _run(BACKUP, env)
    assert result.returncode == 0
    assert "review required" in result.stderr
    assert all((root / n).exists() for n in names)


@pytest.mark.acceptance
def test_failed_backup_never_prunes_anything(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-010: retention runs only after a new verified dump exists."""
    env, _, root = _setup(tmp_path, 10 * GIB, STUB_DUMP_FAIL="1")
    root.mkdir()
    start = dt.date(2026, 1, 1)
    names = [_verified(root, start + dt.timedelta(days=3 * i)) for i in range(8)]
    assert _run(BACKUP, env).returncode != 0
    assert all((root / n).exists() for n in names)


@pytest.mark.acceptance
def test_restore_drill_is_isolated_and_records_measured_bytes(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-009: the drill uses a throwaway no-network container and never the live database."""
    env, stub, root = _setup(tmp_path, 10 * GIB)
    _dump_with_checksum(root)
    result = _run(DRILL, env)
    assert result.returncode == 0, result.stderr
    calls = _calls(stub)
    assert "--network none" in calls
    assert "opip-postgres" not in calls, "the live container must never be referenced"
    assert "PGPASSWORD" not in calls and "compose" not in calls
    assert "rm -f opip-restore-drill-" in calls
    evidence = dict(
        line.split("=", 1)
        for line in (Path(env["OPIP_BACKUP_STATE_ROOT"]) / "last-restore-drill.env").read_text().splitlines()
    )
    assert evidence["schema_version"] == "12" and evidence["isolated"] == "true"
    assert evidence["backup_file"] == "opip-postgres-20260101T000000Z.dump"
    assert int(evidence["live_data_bytes"]) >= MIB
    assert int(evidence["required_bytes_estimate"]) > int(evidence["live_data_bytes"])
    assert {"restored_data_bytes", "min_free_bytes_observed", "free_bytes_after_restore"} <= set(evidence)
    assert list((tmp_path / "work").iterdir()) == []


@pytest.mark.acceptance
def test_failed_restore_cleans_up_and_records_no_success(tmp_path):
    """ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1/AC-009: a failed restore removes the container and data and writes no evidence."""
    env, stub, root = _setup(tmp_path, 10 * GIB, STUB_RESTORE_RC="1")
    _dump_with_checksum(root)
    result = _run(DRILL, env)
    assert result.returncode != 0
    assert "rm -f opip-restore-drill-" in _calls(stub)
    assert list((tmp_path / "work").iterdir()) == []
    assert not (Path(env["OPIP_BACKUP_STATE_ROOT"]) / "last-restore-drill.env").exists()
