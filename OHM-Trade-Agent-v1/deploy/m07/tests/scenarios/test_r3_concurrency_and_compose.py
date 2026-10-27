"""R3: Compose-contract and SQLite concurrency qualification.

Everything here is offline and disposable: no daemon, no host, no production
database. The Compose file under test is the **certified** one from the app tree.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

import opip_rollback_guard as guard
from conftest import CERT_SRC

COMPOSE = CERT_SRC / "docker-compose.yml"
CORE = "ohm-trade-agent"
WRITER = "opip-canonical-writer"
EDGE = "ohm-dashboard-edge"
STREAM = "opip-stream-worker"


@pytest.fixture(scope="module")
def compose() -> dict:
    if not COMPOSE.is_file():
        pytest.skip(f"certified compose not present at {COMPOSE}")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_certified_compose_declares_every_service_the_scripts_drive(compose):
    services = compose["services"]
    for name in (CORE, WRITER, EDGE, STREAM):
        assert name in services, f"{name} missing from the certified compose project"
        assert services[name].get("container_name") == name


def test_environment_file_contract_is_the_app_root_dotenv(compose):
    # The core consumes the controller-generated FILTERED dotenv (`.env.core`),
    # which is derived from the canonical `.env` in the same app root and never
    # carries a funded exchange credential. The writer and stream worker use
    # explicit `environment:` blocks so a stale dotenv can never activate
    # capture or execution.
    assert compose["services"][CORE].get("env_file") == [".env.core"]
    for name in (WRITER, STREAM):
        assert ".env" not in (compose["services"][name].get("env_file") or []), name
        assert ".env.core" not in (compose["services"][name].get("env_file") or []), name
        assert compose["services"][name].get("environment"), name


def test_tracked_nginx_config_is_mounted_read_only(compose):
    volumes = compose["services"][EDGE].get("volumes") or []
    assert any(str(v).startswith("./deploy/nginx/dashboard-sidecar.conf:") for v in volumes)
    assert all(":ro" in str(v) for v in volumes if "nginx" in str(v)), "nginx config must be read-only"


def test_core_does_not_depend_on_the_writer(compose):
    depends = compose["services"][CORE].get("depends_on") or {}
    names = depends if isinstance(depends, list) else list(depends)
    assert WRITER not in names, "a core->writer dependency would start a writer before recovery"


def test_writer_declares_no_published_port(compose):
    assert not compose["services"][WRITER].get("ports"), "the writer must not be reachable"


def test_scripts_only_reference_paths_that_exist_in_the_release():
    for rel in ("docker-compose.yml", "app", "deploy/nginx/dashboard-sidecar.conf",
                "deploy/remote/reconcile-scheduler.sh", "deploy/m07/scripts/opip_rollback_guard.py"):
        assert (CERT_SRC / rel).exists(), f"bootstrap/rollback depends on missing {rel}"


def test_bootstrap_service_targets_exist_in_the_certified_project(compose):
    import re
    text = (CERT_SRC / "deploy/m07/scripts/v2/bootstrap-v2.sh").read_text(encoding="utf-8")
    compose_lines = [line for line in text.splitlines() if "compose " in line]
    targets: set[str] = set()
    for line in compose_lines:
        targets.update(re.findall(r"(?<![\w./-])(ohm-[a-z0-9-]+|opip-[a-z0-9-]+)(?![\w./:-])", line))
    assert WRITER in targets and CORE in targets
    declared = set(compose["services"])
    unknown = targets - declared
    assert not unknown, f"scripts reference undeclared services: {sorted(unknown)}"



# --- SQLite behaviour under real concurrency ----------------------------
def _wal_db(path: Path, rows: int = 3) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meta (id INTEGER PRIMARY KEY, history_epoch INTEGER, next_local_sequence INTEGER)")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO meta VALUES (1, 2, 3)")
    for _ in range(rows):
        conn.execute("INSERT INTO events DEFAULT VALUES")
    conn.commit()
    return conn


def test_snapshot_preserves_uncheckpointed_wal_rows(tmp_path):
    live = tmp_path / "live.sqlite3"
    writer = _wal_db(live, rows=5)
    try:
        assert (tmp_path / "live.sqlite3-wal").exists(), "scenario needs an uncheckpointed WAL"
        result = guard.snapshot_database(live, tmp_path / "snap.sqlite3")
        assert result["event_count"] == 5
        assert result["integrity_check"] == "ok"
    finally:
        writer.close()


def test_snapshot_under_concurrent_writes_is_consistent_and_leaves_source_intact(tmp_path):
    live = tmp_path / "live.sqlite3"
    seed = _wal_db(live, rows=0)
    seed.close()
    total = 200
    errors: list[BaseException] = []

    def write_rows() -> None:
        # SQLite connections are thread-affine: open the writer inside the thread.
        conn = sqlite3.connect(live)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            for _ in range(total):
                conn.execute("INSERT INTO events DEFAULT VALUES")
                time.sleep(0.001)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=write_rows, daemon=True)
    thread.start()
    time.sleep(0.02)
    result = guard.snapshot_database(live, tmp_path / "snap.sqlite3")
    thread.join(timeout=30)

    assert not errors, errors
    assert result["integrity_check"] == "ok"
    assert result["foreign_key_violations"] == 0
    assert 0 <= result["event_count"] <= total
    check = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        assert check.execute("SELECT COUNT(*) FROM events").fetchone()[0] == total, \
            "the snapshot must not disturb the live writer"
    finally:
        check.close()


def test_held_writer_lock_is_detected_as_not_quiescent(tmp_path):
    import opip_recovery as recovery

    app_root = tmp_path / "app"
    canonical = app_root / "data" / "opip" / "canonical"
    canonical.mkdir(parents=True)
    lock = canonical / "opip_canonical_v1.sqlite3.writer.lock"
    lock.write_text("", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
         "from app.services.registry_io import registry_lock;"
         "ctx=registry_lock(Path(sys.argv[2]), timeout=0.0); ctx.__enter__();"
         "print('held', flush=True); time.sleep(6)",
         str(CERT_SRC), str(lock)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = holder.stdout.readline()
        if "held" not in line:
            holder.kill()
            pytest.skip(f"certified registry_lock unavailable for this probe: {holder.stderr.read()[:200]}")
        quiescent, reason = recovery.writer_is_quiescent(app_root)
        assert quiescent is False, reason
        assert "held" in reason
    finally:
        holder.kill()
        holder.wait(timeout=10)
    quiescent, _ = recovery.writer_is_quiescent(app_root)
    assert quiescent is True
