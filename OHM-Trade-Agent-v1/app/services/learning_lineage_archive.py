"""Copy-only, verified archival of a canonical replica lineage generation.

Archival is the gate that permits a new lineage to be synchronized without
destroying the previous one. It never moves, deletes or rewrites the source
generation. A generation is accepted as archived only when:

* every source file was copied byte-for-byte with matching size and SHA-256;
* the archive manifest is durable and its own SHA-256 is recorded in a durable
  append-only receipt;
* the source generation still exists with an unchanged ``replica_manifest.json``;
* the archive contains no missing or extra files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.services.fs_durability import DurabilityError, fsync_dir, fsync_file

SCHEMA = "opip-learning-lineage-archive/v1"
RECEIPT = "LEARNING_LINEAGE_ARCHIVE"
RESERVE_BYTES = 256 * 1024 * 1024
COPY_HEADROOM = 1.05


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict]:
    files: list[dict] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return files


def create_archive(
    *,
    source_generation: Path,
    archive_root: Path,
    state_root: Path,
    lineage_id: str,
    source_release_sha: str,
    disk_usage=shutil.disk_usage,
    copy_fn=shutil.copy2,
) -> dict:
    if not source_generation.is_dir():
        raise ValueError(f"source generation missing: {source_generation}")
    if archive_root.exists():
        raise ValueError(f"archive already exists, refusing to overwrite: {archive_root}")
    source_files = _inventory(source_generation)
    if not source_files:
        raise ValueError("source generation has no files")
    total_bytes = sum(item["size"] for item in source_files)
    archive_root.parent.mkdir(parents=True, exist_ok=True)
    free = disk_usage(archive_root.parent).free
    required = int(total_bytes * COPY_HEADROOM) + RESERVE_BYTES
    if free < required:
        raise ValueError(
            f"insufficient disk for archive: free={free} required={required}"
        )

    staging = archive_root.parent / f".{archive_root.name}.tmp.{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, mode=0o700)
    copied: list[dict] = []
    try:
        for item in source_files:
            source = source_generation / item["path"]
            target = staging / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_fn(source, target)
            fsync_file(target)
            copied.append(
                {
                    "path": item["path"],
                    "size": target.stat().st_size,
                    "sha256": _sha256(target),
                }
            )
        if copied != source_files:
            raise ValueError("copied archive differs from source inventory")
        for directory in [staging] + [p for p in staging.rglob("*") if p.is_dir()]:
            os.chmod(directory, 0o750)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o640)

        source_manifest = source_generation / "replica_manifest.json"
        manifest = {
            "schema": SCHEMA,
            "lineage_id": lineage_id,
            "generation_id": source_generation.name,
            "source_release_sha": source_release_sha,
            "created_at_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "file_count": len(copied),
            "total_bytes": total_bytes,
            "source_manifest_sha256": _sha256(source_manifest),
            "files": copied,
        }
        manifest_path = staging / "archive_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        os.chmod(manifest_path, 0o600)
        fsync_file(manifest_path)
        fsync_dir(staging)
        os.replace(staging, archive_root)
        fsync_dir(archive_root.parent)
    except (DurabilityError, OSError, ValueError):
        # An unverified copy is discarded; the source generation is untouched.
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(archive_root, ignore_errors=True)
        raise
    manifest_sha = _sha256(archive_root / "archive_manifest.json")

    state_root.mkdir(parents=True, exist_ok=True)
    os.chmod(state_root, 0o700)
    receipt_path = state_root / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{lineage_id}-{source_generation.name}.json"
    )
    receipt = {
        "receipt": RECEIPT,
        "status": "VERIFIED",
        "lineage_id": lineage_id,
        "generation_id": source_generation.name,
        "source_release_sha": source_release_sha,
        "archive_dir": str(archive_root),
        "archive_manifest_sha256": manifest_sha,
        "file_count": len(copied),
        "total_bytes": total_bytes,
        "source_generation_preserved": True,
        "historical_evidence_modified": False,
        "recorded_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }
    fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.flush()
        fsync_file(receipt_path)
        fsync_dir(state_root)
    except (DurabilityError, OSError):
        # No durable receipt means the archive is not VERIFIED. Discard the
        # unverified copy; the source generation is untouched.
        try:
            receipt_path.unlink()
        except OSError:
            pass
        shutil.rmtree(archive_root, ignore_errors=True)
        raise
    return {"receipt_path": str(receipt_path), "receipt": receipt, "manifest": manifest}


def verify_archive(
    *,
    source_generation: Path,
    archive_dir: Path,
    state_root: Path,
    lineage_id: str,
    source_release_sha: str,
) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    manifest_path = archive_dir / "archive_manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    add("archive_present", archive_dir.is_dir(), str(archive_dir))
    add("archive_manifest_present", bool(manifest), str(manifest_path))
    add(
        "archive_lineage",
        manifest.get("lineage_id") == lineage_id,
        f"archive={manifest.get('lineage_id')} expected={lineage_id}",
    )
    add(
        "archive_source_release",
        manifest.get("source_release_sha") == source_release_sha,
        f"archive={manifest.get('source_release_sha')} expected={source_release_sha}",
    )

    receipts: list[Path] = []
    if state_root.is_dir():
        receipts = sorted(state_root.glob(f"*-{lineage_id}-{archive_dir.name}.json"))
    receipt = {}
    if receipts:
        try:
            receipt = json.loads(receipts[-1].read_text(encoding="utf-8"))
        except Exception:
            receipt = {}
    add("archive_receipt_present", bool(receipt), str(receipts[-1]) if receipts else "MISSING")
    add(
        "archive_receipt_verified",
        receipt.get("status") == "VERIFIED"
        and receipt.get("receipt") == RECEIPT
        and receipt.get("source_generation_preserved") is True
        and receipt.get("historical_evidence_modified") is False,
        f"status={receipt.get('status')}",
    )
    if manifest_path.is_file() and receipt:
        add(
            "archive_manifest_digest",
            _sha256(manifest_path) == receipt.get("archive_manifest_sha256"),
            "manifest sha256 matches receipt",
        )
    else:
        add("archive_manifest_digest", False, "manifest or receipt missing")

    listed = {
        entry["path"]: entry for entry in manifest.get("files") or [] if "path" in entry
    }
    actual = {
        str(path.relative_to(archive_dir)): path
        for path in archive_dir.rglob("*")
        if path.is_file() and path.name != "archive_manifest.json"
    }
    missing = sorted(set(listed) - set(actual))
    extra = sorted(set(actual) - set(listed))
    add("archive_no_missing_files", not missing, f"missing={missing}")
    add("archive_no_extra_files", not extra, f"extra={extra}")
    corrupted = []
    for name, entry in listed.items():
        path = actual.get(name)
        if path is None:
            continue
        if path.stat().st_size != entry["size"] or _sha256(path) != entry["sha256"]:
            corrupted.append(name)
    add("archive_content_verified", not corrupted, f"corrupted={corrupted}")

    source_manifest = source_generation / "replica_manifest.json"
    add(
        "source_generation_preserved",
        source_manifest.is_file()
        and _sha256(source_manifest) == manifest.get("source_manifest_sha256"),
        str(source_manifest),
    )
    return checks


def failed(checks: list[dict]) -> list[dict]:
    return [check for check in checks if not check["passed"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lineage archive")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--source-generation", required=True)
    create.add_argument("--archive-root", required=True)
    create.add_argument("--state-root", required=True)
    create.add_argument("--lineage-id", required=True)
    create.add_argument("--source-sha", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--source-generation", required=True)
    verify.add_argument("--archive-dir", required=True)
    verify.add_argument("--state-root", required=True)
    verify.add_argument("--lineage-id", required=True)
    verify.add_argument("--source-sha", required=True)
    verify.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "create":
        result = create_archive(
            source_generation=Path(args.source_generation),
            archive_root=Path(args.archive_root),
            state_root=Path(args.state_root),
            lineage_id=args.lineage_id,
            source_release_sha=args.source_sha,
        )
        print(json.dumps({"receipt_path": result["receipt_path"]}))
        return 0
    checks = verify_archive(
        source_generation=Path(args.source_generation),
        archive_dir=Path(args.archive_dir),
        state_root=Path(args.state_root),
        lineage_id=args.lineage_id,
        source_release_sha=args.source_sha,
    )
    failures = failed(checks)
    if args.json:
        print(json.dumps({"checks": checks, "failures": failures}, indent=2))
    else:
        for check in checks:
            print(
                f"{'PASS' if check['passed'] else 'FAIL'} "
                f"{check['name']} {check['detail']}"
            )
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
