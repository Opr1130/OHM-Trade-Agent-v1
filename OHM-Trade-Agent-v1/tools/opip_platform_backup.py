#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone


SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
SKIP_SUFFIXES = {".lock", ".tmp", ".part"}
SQLITE_TRANSIENT_SUFFIXES = ("-wal", "-shm", "-journal")
SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def should_skip(relative: Path) -> bool:
    name = relative.name
    if name == ".env":
        return True
    if relative.suffix.lower() in SKIP_SUFFIXES:
        return True
    if name.lower().endswith(SQLITE_TRANSIENT_SUFFIXES):
        return True
    if name.startswith(".") and name.endswith(".swp"):
        return True
    return False


def sqlite_online_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30.0) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"SQLite integrity check failed for {source}")


def copy_data_tree(source: Path, stage_data: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if should_skip(relative):
            continue
        target = stage_data / relative
        if path.is_symlink():
            continue
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            continue

        if path.suffix.lower() in SQLITE_SUFFIXES:
            sqlite_online_copy(path, target)
            kind = "sqlite"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            kind = "file"

        records.append(
            {
                "relative_path": relative.as_posix(),
                "size": target.stat().st_size,
                "sha256": sha256_file(target),
                "kind": kind,
            }
        )
    return records


def prune_archives(destination: Path, retention: int) -> None:
    archives = sorted(
        destination.glob("opip-data-*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for archive in archives[retention:]:
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)


def build_backup(source: Path, destination: Path, retention: int) -> Path:
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"backup source does not exist: {source}")
    if retention < 1:
        raise ValueError("retention must be at least 1")

    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = destination / f"opip-data-{timestamp}-{os.getpid()}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="opip-backup-", dir=destination) as tmp:
        stage = Path(tmp) / "payload"
        stage_data = stage / "data"
        stage_data.mkdir(parents=True, exist_ok=True)

        records = copy_data_tree(source, stage_data)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source.resolve()),
            "file_count": len(records),
            "files": records,
            "exclusions": [
                ".env",
                "symbolic links",
                "*.lock",
                "*.tmp",
                "*.part",
                "editor swap files",
                "SQLite transient sidecars (*-wal, *-shm, *-journal)",
            ],
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(stage, arcname=".")

    checksum = sha256_file(archive)
    checksum_path = archive.with_suffix(archive.suffix + ".sha256")
    checksum_path.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    prune_archives(destination, retention)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local O'Pip data backup.")
    parser.add_argument("--source", type=Path, default=Path("data"))
    parser.add_argument("--destination", type=Path, default=Path("/var/backups/opip"))
    parser.add_argument("--retention", type=int, default=14)
    args = parser.parse_args()

    archive = build_backup(args.source, args.destination, args.retention)
    print(f"O'Pip local backup created: {archive}")
    print(f"checksum: {archive.with_suffix(archive.suffix + '.sha256')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
