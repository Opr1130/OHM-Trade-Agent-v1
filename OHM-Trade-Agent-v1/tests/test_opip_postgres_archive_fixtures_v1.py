"""ATDD-OPIP-ARCHIVE-DURABILITY-V1: archive integrity checks proven on real and disposable fixtures."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "deploy/analytics/opip-postgres-backup.sh"
GIB = 1024**3
DSN = os.environ.get("OPIP_TEST_POSTGRES_DSN", "")
needs_pg = pytest.mark.skipif(
    not (DSN and shutil.which("pg_dump") and shutil.which("pg_restore") and shutil.which("psql")),
    reason="needs OPIP_TEST_POSTGRES_DSN (a disposable server) plus pg_dump, pg_restore and psql",
)


def _dsn_for(db: str) -> str:
    parts = urlsplit(DSN)
    return urlunsplit(parts._replace(path=f"/{db}"))


def _psql(dsn: str, sql: str) -> str:
    done = subprocess.run(
        ["psql", dsn, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _rc(args: list[str], source: Path) -> int:
    with source.open("rb") as handle:
        return subprocess.run(
            args, stdin=handle, capture_output=True, check=False, timeout=60
        ).returncode


@pytest.fixture()
def scratch_db():
    name = f"opip_archive_{uuid.uuid4().hex[:10]}"
    admin = _dsn_for("postgres")
    _psql(admin, f"create database {name}")
    created = [name]
    try:
        yield name, created
    finally:
        for db in created:
            _psql(admin, f"drop database if exists {db}")


def _populate(db: str) -> str:
    dsn = _dsn_for(db)
    _psql(dsn, "create schema signal")
    _psql(dsn, "create table signal.t(id bigint primary key, payload text)")
    _psql(dsn, "insert into signal.t select g, repeat(md5(g::text), 40) from generate_series(1, 40000) g")
    return dsn


def _fingerprint(dsn: str) -> str:
    return _psql(dsn, "select count(*) || ':' || md5(string_agg(id::text || payload, ',' order by id)) from signal.t")


def _dump(dsn: str, target: Path) -> None:
    with target.open("wb") as handle:
        done = subprocess.run(
            ["pg_dump", "--format=custom", "--compress=9", "--dbname", dsn],
            stdout=handle, stderr=subprocess.PIPE, check=False, timeout=120,
        )
    assert done.returncode == 0, done.stderr


@needs_pg
def test_ac_001_real_archive_passes_both_reads_and_restores_identically(tmp_path, scratch_db):
    """ATDD-OPIP-ARCHIVE-DURABILITY-V1/AC-001"""
    db, created = scratch_db
    dsn = _populate(db)
    dump = tmp_path / "opip-postgres-20260101T000000Z.dump"
    _dump(dsn, dump)
    assert _rc(["pg_restore", "--list"], dump) == 0
    assert _rc(["pg_restore", "-f", "-"], dump) == 0
    manifest = tmp_path / "dump.sha256"
    manifest.write_text(f"{hashlib.sha256(dump.read_bytes()).hexdigest()}  {dump.name}\n", encoding="utf-8")
    check = subprocess.run(["sha256sum", "--check", "--status", manifest.name], cwd=tmp_path, check=False)
    assert check.returncode == 0
    target = f"{db}_restored"
    _psql(_dsn_for("postgres"), f"create database {target}")
    created.append(target)
    restored = subprocess.run(
        ["pg_restore", "--exit-on-error", "--no-owner", "--dbname", _dsn_for(target), str(dump)],
        capture_output=True, text=True, check=False, timeout=120,
    )
    assert restored.returncode == 0, restored.stderr
    assert _fingerprint(_dsn_for(target)) == _fingerprint(dsn)


@needs_pg
def test_ac_002_table_of_contents_alone_accepts_damaged_archives_full_read_does_not(tmp_path, scratch_db):
    """ATDD-OPIP-ARCHIVE-DURABILITY-V1/AC-002"""
    db, _ = scratch_db
    dsn = _populate(db)
    good = tmp_path / "good.dump"
    _dump(dsn, good)
    raw = good.read_bytes()
    half = tmp_path / "half.dump"
    half.write_bytes(raw[: len(raw) // 2])
    flipped = tmp_path / "flipped.dump"
    damaged = bytearray(raw)
    middle = len(damaged) // 2
    for offset in range(64):
        damaged[middle + offset] ^= 0xFF
    flipped.write_bytes(bytes(damaged))
    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    for damaged_file in (half, flipped):
        assert _rc(["pg_restore", "--list"], damaged_file) == 0, "the TOC check alone does not see this damage"
        assert _rc(["pg_restore", "-f", "-"], damaged_file) != 0, "the full read must reject it"
        assert hashlib.sha256(damaged_file.read_bytes()).hexdigest() != hashlib.sha256(raw).hexdigest()
    assert _rc(["pg_restore", "--list"], empty) != 0
    assert _rc(["pg_restore", "-f", "-"], empty) != 0


DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_DIR/docker.calls"
case "$*" in
  *pg_dump*) printf 'PGDMP-stub-archive' ;;
  *"pg_restore --list"*) cat > /dev/null; exit 0 ;;
  *"pg_restore -f -"*) cat > /dev/null; exit "${STUB_FULLREAD_RC:-0}" ;;
esac
"""
DF_STUB = '#!/usr/bin/env bash\necho "Filesystem 1-blocks Used Available Capacity Mounted"\necho "stub 0 0 ${STUB_FREE_BYTES} 0% /"\n'


def _run_backup(tmp_path: Path, **extra: str) -> tuple[subprocess.CompletedProcess, Path, Path]:
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("df", DF_STUB)):
        (stub / name).write_text(body, encoding="utf-8")
        (stub / name).chmod(0o755)
    env_file = tmp_path / "platform.env"
    env_file.write_text("OPIP_POSTGRES_ADMIN_PASSWORD=not-a-real-secret\n", encoding="utf-8")
    root = tmp_path / "backups"
    env = os.environ.copy()
    env.update(
        PATH=f"{stub}:{env['PATH']}", STUB_DIR=str(stub), STUB_FREE_BYTES=str(10 * GIB),
        OPIP_BACKUP_ENV_FILE=str(env_file), OPIP_BACKUP_ROOT=str(root),
        OPIP_BACKUP_STATE_ROOT=str(tmp_path / "state"), OPIP_BACKUP_LOCK_FILE=str(tmp_path / "backup.lock"),
        **extra,
    )
    done = subprocess.run(["bash", str(BACKUP)], capture_output=True, text=True, env=env, check=False, timeout=30)
    return done, root, stub


def test_ac_003_backup_discards_a_dump_whose_data_blocks_cannot_be_read(tmp_path):
    """ATDD-OPIP-ARCHIVE-DURABILITY-V1/AC-003"""
    (tmp_path / "bad").mkdir()
    bad, bad_root, stub = _run_backup(tmp_path / "bad", STUB_FULLREAD_RC="1")
    assert bad.returncode != 0 and "data blocks are unreadable" in bad.stderr
    assert list(bad_root.iterdir()) == []
    assert "pg_restore -f -" in (stub / "docker.calls").read_text(encoding="utf-8")
    (tmp_path / "ok").mkdir()
    ok, ok_root, _ = _run_backup(tmp_path / "ok")
    assert ok.returncode == 0, ok.stderr
    dump = next(ok_root.glob("opip-postgres-*.dump"))
    assert Path(f"{dump}.verified").is_file() and Path(f"{dump}.sha256").is_file()


def _load_backup_tool():
    spec = importlib.util.spec_from_file_location("opip_platform_backup", ROOT / "tools/opip_platform_backup.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ac_004_manifest_flags_a_line_ledger_copied_mid_row_and_leaves_the_source_alone(tmp_path):
    """ATDD-OPIP-ARCHIVE-DURABILITY-V1/AC-004"""
    tool = _load_backup_tool()
    source = tmp_path / "data"
    source.mkdir()
    complete = b'{"a":1}\n{"a":2}\n'
    torn = b'{"a":1}\n{"a":'
    (source / "complete.jsonl").write_bytes(complete)
    (source / "torn.jsonl").write_bytes(torn)
    (source / "empty.jsonl").write_bytes(b"")
    (source / "plain.txt").write_bytes(b"no newline")
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    stage = tmp_path / "stage"
    stage.mkdir()
    records = {r["relative_path"]: r for r in tool.copy_data_tree(source, stage)}
    assert records["complete.jsonl"]["line_ledger_tail_complete"] is True
    assert records["empty.jsonl"]["line_ledger_tail_complete"] is True
    assert records["torn.jsonl"]["line_ledger_tail_complete"] is False
    assert "line_ledger_tail_complete" not in records["plain.txt"]
    assert records["torn.jsonl"]["sha256"] == hashlib.sha256(torn).hexdigest()
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    json.dumps(list(records.values()))
