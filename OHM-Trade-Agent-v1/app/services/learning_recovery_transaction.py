"""Transactional configuration recovery for the learning worker.

The recovery deploy replaces the learning environment, installed runner scripts
and systemd unit definitions before running evidence-generating one-shots. This
module makes those replacements reversible **only** while they remain pure
configuration.

The commit boundary is the canonical replica ``current`` pointer: before a new
generation is activated, a failure restores environment, scripts and units from
verified byte-for-byte backups; once a new generation is active, configuration
is never rolled back blindly. The transaction records ``KEEP_CONFIG_BLOCKED``
and timers remain disabled.

Snapshots and receipts are fsynced. Restores verify backup hashes first and are
idempotent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROLLBACK_CONFIG = "ROLLBACK_CONFIG"
KEEP_CONFIG_BLOCKED = "KEEP_CONFIG_BLOCKED"
REFUSE_TIMERS_ENABLED = "REFUSE_TIMERS_ENABLED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        # Windows rejects fsync on read-only descriptors; production is Linux.
        pass


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def snapshot(paths: Iterable[Path], transaction_dir: Path) -> dict:
    """Copy every existing configuration file into a verified transaction."""
    backups = transaction_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    os.chmod(transaction_dir, 0o700)
    os.chmod(backups, 0o700)
    files: list[dict] = []
    for index, target in enumerate(sorted({str(path) for path in paths})):
        path = Path(target)
        if path.is_file() and not path.is_symlink():
            backup = backups / f"{index:02d}-{path.name}"
            backup.write_bytes(path.read_bytes())
            os.chmod(backup, 0o600)
            _fsync_file(backup)
            files.append(
                {
                    "target": str(path),
                    "existed": True,
                    "backup": str(backup),
                    "mode": oct(path.stat().st_mode & 0o777),
                    "sha256": _sha256(backup),
                }
            )
        else:
            files.append(
                {
                    "target": str(path),
                    "existed": False,
                    "backup": None,
                    "mode": None,
                    "sha256": None,
                }
            )
    _fsync_dir(backups)
    _fsync_dir(transaction_dir)
    manifest = {
        "schema": "opip-learning-recovery-transaction/v1",
        "created_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "files": files,
    }
    write_receipt(transaction_dir, manifest, name="snapshot.json")
    return manifest


def verify_snapshot(manifest: dict) -> list[str]:
    problems: list[str] = []
    for entry in manifest.get("files") or []:
        if not entry.get("existed"):
            continue
        backup = Path(entry["backup"])
        if not backup.is_file():
            problems.append(f"missing backup: {backup}")
            continue
        if _sha256(backup) != entry["sha256"]:
            problems.append(f"backup hash mismatch: {backup}")
    return problems


def restore(manifest: dict, *, dry_run: bool = False) -> list[dict]:
    """Restore verified backups. Newly created files are removed."""
    problems = verify_snapshot(manifest)
    if problems:
        raise ValueError(
            "transaction backup verification failed: " + "; ".join(problems)
        )
    actions: list[dict] = []
    for entry in manifest.get("files") or []:
        target = Path(entry["target"])
        if entry.get("existed"):
            backup = Path(entry["backup"])
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(backup.read_bytes())
                if entry.get("mode"):
                    os.chmod(target, int(entry["mode"], 8))
                _fsync_file(target)
            actions.append({"target": str(target), "action": "restored"})
        else:
            if target.exists() or target.is_symlink():
                if not dry_run:
                    target.unlink()
                actions.append({"target": str(target), "action": "removed_new_file"})
            else:
                actions.append({"target": str(target), "action": "already_absent"})
    if not dry_run:
        for directory in {
            str(Path(entry["target"]).parent) for entry in manifest.get("files") or []
        }:
            _fsync_dir(Path(directory))
    return actions


def generation_committed(before_current: str, after_current: str) -> bool:
    """A new replica generation was activated; it must never be rolled back."""
    return bool(after_current) and after_current != before_current


def failure_action(
    *, stage: str, evidence_committed: bool, timers_enabled: bool
) -> str:
    if timers_enabled:
        return REFUSE_TIMERS_ENABLED
    if evidence_committed:
        return KEEP_CONFIG_BLOCKED
    return ROLLBACK_CONFIG


def write_receipt(transaction_dir: Path, payload: dict, *, name: str) -> Path:
    transaction_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(transaction_dir, 0o700)
    path = transaction_dir / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_dir(transaction_dir)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Learning recovery transaction")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot")
    snap.add_argument("--transaction-dir", required=True)
    snap.add_argument("--path", action="append", default=[])

    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--transaction-dir", required=True)
    restore_parser.add_argument("--dry-run", action="store_true")

    decide = sub.add_parser("decide")
    decide.add_argument("--stage", required=True)
    decide.add_argument("--evidence-committed", required=True)
    decide.add_argument("--timers-enabled", required=True)

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        manifest = snapshot([Path(p) for p in args.path], Path(args.transaction_dir))
        print(json.dumps({"files": len(manifest["files"])}))
        return 0
    if args.command == "restore":
        tx = Path(args.transaction_dir)
        manifest = json.loads((tx / "snapshot.json").read_text())
        actions = restore(manifest, dry_run=args.dry_run)
        print(json.dumps({"actions": actions}))
        return 0
    action = failure_action(
        stage=args.stage,
        evidence_committed=args.evidence_committed.strip().lower() == "true",
        timers_enabled=args.timers_enabled.strip().lower() == "true",
    )
    print(action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
