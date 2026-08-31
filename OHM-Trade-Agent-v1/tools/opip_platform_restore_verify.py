#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive_checksum(archive: Path) -> None:
    checksum_path = archive.with_suffix(archive.suffix + ".sha256")
    if not checksum_path.exists():
        raise FileNotFoundError(f"checksum file missing: {checksum_path}")
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0]
    actual = sha256_file(archive)
    if actual != expected:
        raise RuntimeError(
            f"archive checksum mismatch: expected {expected}, got {actual}"
        )


def safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"links are not permitted in backup archive: {member.name}")
        tar.extractall(destination)


def verify_payload(root: Path) -> tuple[int, int]:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("manifest.json missing from backup")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise RuntimeError("unsupported backup manifest schema")

    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("backup manifest files must be a list")

    sqlite_count = 0
    for record in files:
        relative = Path(record["relative_path"])
        path = root / "data" / relative
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"backup file missing: {relative}")
        if path.stat().st_size != int(record["size"]):
            raise RuntimeError(f"backup file size mismatch: {relative}")
        if sha256_file(path) != record["sha256"]:
            raise RuntimeError(f"backup file checksum mismatch: {relative}")

        if record.get("kind") == "sqlite":
            sqlite_count += 1
            with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
                result = db.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RuntimeError(f"SQLite integrity check failed: {relative}")

    if int(manifest.get("file_count", -1)) != len(files):
        raise RuntimeError("manifest file_count does not match records")
    return len(files), sqlite_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an O'Pip local backup without restoring production."
    )
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()

    archive = args.archive.resolve()
    if not archive.exists():
        raise FileNotFoundError(f"archive not found: {archive}")

    verify_archive_checksum(archive)
    with tempfile.TemporaryDirectory(prefix="opip-restore-verify-") as tmp:
        root = Path(tmp)
        safe_extract(archive, root)
        files, sqlite_files = verify_payload(root)

    print(
        f"O'Pip restore verification: VERIFIED files={files} "
        f"sqlite_files={sqlite_files}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
