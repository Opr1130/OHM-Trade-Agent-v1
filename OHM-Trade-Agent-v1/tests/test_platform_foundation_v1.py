from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "tools" / "opip_platform_backup.py"
VERIFY = ROOT / "tools" / "opip_platform_restore_verify.py"
CHECK = ROOT / "deploy" / "platform" / "opip-platform-check.sh"
COMPOSE = ROOT / "docker-compose.yml"
PAPER_COMPOSE = ROOT / "docker-compose.paper.yml"


def test_platform_check_script_has_valid_bash_syntax():
    subprocess.run(
        ["bash", "-n", str(CHECK)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_platform_backup_and_restore_verification(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    (source / "state.json").write_text('{"status":"ok"}\n', encoding="utf-8")
    (source / "runtime.lock").write_text("transient", encoding="utf-8")
    (source / ".env").write_text("SECRET=must-not-copy\n", encoding="utf-8")

    db_path = source / "trades.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("create table trades(id integer primary key, symbol text)")
        db.execute("insert into trades(symbol) values ('BTCUSD')")
        db.commit()

    destination = tmp_path / "backups"
    result = subprocess.run(
        [
            sys.executable,
            str(BACKUP),
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--retention",
            "2",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "O'Pip local backup created:" in result.stdout

    archives = list(destination.glob("opip-data-*.tar.gz"))
    assert len(archives) == 1
    archive = archives[0]
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    assert checksum.exists()

    verified = subprocess.run(
        [sys.executable, str(VERIFY), str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "VERIFIED" in verified.stdout
    assert "sqlite_files=1" in verified.stdout


def test_restore_verifier_rejects_archive_checksum_tamper(tmp_path):
    source = tmp_path / "data"
    source.mkdir()
    (source / "state.json").write_text("{}\n", encoding="utf-8")
    destination = tmp_path / "backups"

    subprocess.run(
        [
            sys.executable,
            str(BACKUP),
            "--source",
            str(source),
            "--destination",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    archive = next(destination.glob("opip-data-*.tar.gz"))
    checksum_path = archive.with_suffix(archive.suffix + ".sha256")
    checksum_path.write_text(
        f"{'0' * 64}  {archive.name}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(VERIFY), str(archive)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "archive checksum mismatch" in (result.stderr + result.stdout)


def test_core_compose_has_bounded_logs_process_limits_and_healthcheck():
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count('driver: "json-file"') >= 2
    assert text.count('max-size: "10m"') >= 2
    assert text.count('max-file: "5"') >= 2
    assert "pids_limit: 256" in text
    assert "healthcheck:" in text
    assert "127.0.0.1:8000/health" in text
    assert "no-new-privileges:true" in text


def test_production_memory_budget_is_bounded_for_2gb_host():
    core = COMPOSE.read_text(encoding="utf-8")
    paper = PAPER_COMPOSE.read_text(encoding="utf-8")

    assert "mem_limit: 384m" in core
    assert "memswap_limit: 384m" in core
    assert "mem_limit: 192m" in core
    assert "memswap_limit: 192m" in core
    assert "mem_limit: 64m" in core
    assert "memswap_limit: 64m" in core

    assert "ohm-freqtrade-paper-usdt:" in paper
    assert paper.count("mem_limit: 384m") == 2
    assert paper.count("memswap_limit: 384m") == 2
    assert paper.count('cpus: "0.20"') == 2
    assert paper.count("oom_score_adj: 500") == 2
    assert "./data/freqtrade/user_data_usdt:/freqtrade/user_data" in paper


def test_platform_backup_excludes_sqlite_transient_sidecars(tmp_path):
    source = tmp_path / "data"
    source.mkdir()

    db_path = source / "live.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("create table sample(id integer primary key, value text)")
        db.execute("insert into sample(value) values ('ok')")
        db.commit()

        # SQLite WAL-mode sidecars are live runtime artifacts. They must not be
        # archived independently from the consistent online backup of live.sqlite.
        (source / "live.sqlite-wal").touch(exist_ok=True)
        (source / "live.sqlite-shm").touch(exist_ok=True)
        (source / "live.sqlite-journal").touch(exist_ok=True)

        destination = tmp_path / "backups"
        subprocess.run(
            [
                sys.executable,
                str(BACKUP),
                "--source",
                str(source),
                "--destination",
                str(destination),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    archive = next(destination.glob("opip-data-*.tar.gz"))
    verified = subprocess.run(
        [sys.executable, str(VERIFY), str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "VERIFIED" in verified.stdout

    import tarfile
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert not any(name.endswith("-wal") for name in names)
    assert not any(name.endswith("-shm") for name in names)
    assert not any(name.endswith("-journal") for name in names)
