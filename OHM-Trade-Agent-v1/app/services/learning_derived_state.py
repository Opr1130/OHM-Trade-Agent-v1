"""Lineage-aware isolation of learning derived state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.services.fs_durability import fsync_dir, fsync_file

SCHEMA = "opip-learning-derived-state/v1"
LINEAGE_SCHEMA = "opip-learning-lineage/v1"
RECEIPT_KIND = "OPIP_LEARNING_DERIVED_STATE_ARCHIVE"
LINEAGE_FILE = ".lineage.json"
DEFAULT_DATA_ROOT = Path("/app/data")

ARTIFACT_SPECS = (
    {"path": ".p1_evidence_ledger.jsonl.dedup.sqlite3", "category": "derived", "kind": "sqlite", "reason": "P1 dedup offset"},
    {"path": ".phase3c_forward_outcomes.jsonl.state.sqlite3", "category": "derived", "kind": "sqlite", "reason": "Phase3C state"},
    {"path": "opip/discovery/.forward_outcomes.jsonl.state.sqlite3", "category": "derived", "kind": "sqlite", "reason": "Discovery state"},
    {"path": "opip/.opportunity_accountability.jsonl.state.sqlite3", "category": "derived", "kind": "sqlite", "reason": "Accountability state"},
    {"path": "phase3c_forward_outcomes.jsonl", "category": "derived", "kind": "file", "reason": "Phase3C output"},
    {"path": "opip/discovery/forward_outcomes.jsonl", "category": "derived", "kind": "file", "reason": "Discovery output"},
    {"path": "opip/discovery/attributions.jsonl", "category": "derived", "kind": "file", "reason": "Discovery attribution"},
    {"path": "opip/discovery/ef01_reconciliation_report.json", "category": "derived", "kind": "file", "reason": "Discovery report"},
    {"path": "opip/discovery/discovery_dead_letter.jsonl", "category": "derived", "kind": "file", "reason": "Discovery dead letter"},
    {"path": "opip/opportunity_accountability.jsonl", "category": "derived", "kind": "file", "reason": "Accountability output"},
    {"path": "opip/opportunity_accountability_summary.json", "category": "derived", "kind": "file", "reason": "Accountability summary"},
    {"path": ".learning_consumption", "category": "derived", "kind": "dir", "reason": "Consumption summaries"},
    {"path": ".learning_coverage", "category": "derived", "kind": "dir", "reason": "Coverage evidence"},
    {"path": "opip_ml_capture_checkpoint.json", "category": "derived", "kind": "file", "reason": "Capture checkpoint"},
    {"path": "opip_ml_capture_health.json", "category": "derived", "kind": "file", "reason": "Capture health"},
    {"path": "paper_trading/evidence_gap_spool.json", "category": "derived", "kind": "file", "reason": "Paper gap spool"},
    {"path": "p1_shadow_outbox.jsonl", "category": "derived", "kind": "file", "reason": "Retired P1 shadow outbox evidence"},
    {"path": "p1_shadow_outbox_checkpoint.json", "category": "derived", "kind": "file", "reason": "Retired P1 checkpoint"},
    {"path": "p1_shadow_outbox_dead_letter.jsonl", "category": "derived", "kind": "file", "reason": "Retired P1 dead letter"},
    {"path": "p1_evidence_ledger.jsonl", "category": "input", "kind": "file", "reason": "P1 ledger input"},
    {"path": "opip/qualification/screening_evaluations.jsonl", "category": "input", "kind": "file", "reason": "Screening input"},
    {"path": "opip/qualification/funnel_events.jsonl", "category": "input", "kind": "file", "reason": "Funnel input"},
    {"path": "paper_trading/events.jsonl", "category": "input", "kind": "file", "reason": "Paper event input"},
    {"path": "full_market_observations.jsonl", "category": "input", "kind": "file", "reason": "Observation input"},
    {"path": "intelligence_learning/events.jsonl", "category": "input", "kind": "file", "reason": "Intelligence input"},
)

SQLITE_SUFFIXES = ("", "-wal", "-shm")

class DerivedStateError(RuntimeError):
    """Base class for fail-closed derived-state failures."""


class InsufficientSpaceError(DerivedStateError):
    """Insufficient headroom for a durable archive."""


class IncompleteArchiveError(DerivedStateError):
    """An archive destination exists but is not VERIFIED."""


class StateLineageError(DerivedStateError):
    """A sidecar is unbound to, or bound to another, lineage."""


@dataclass(frozen=True)
class LineageIdentity:
    lineage_id: str
    source_release_sha: str
    generation_id: str

    def validate(self) -> None:
        if not self.lineage_id or not all(
            ch.isalnum() or ch in "._-" for ch in self.lineage_id
        ):
            raise DerivedStateError(f"invalid lineage_id: {self.lineage_id!r}")
        if len(self.source_release_sha) != 40 or any(
            ch not in "0123456789abcdef" for ch in self.source_release_sha
        ):
            raise DerivedStateError(
                f"invalid source_release_sha: {self.source_release_sha!r}"
            )
        if not self.generation_id.startswith("gen-") or any(
            ch not in "0123456789abcdef" for ch in self.generation_id[4:]
        ):
            raise DerivedStateError(
                f"invalid generation_id: {self.generation_id!r}"
            )

    def as_dict(self) -> dict[str, str]:
        return {
            "lineage_id": self.lineage_id,
            "source_release_sha": self.source_release_sha,
            "generation_id": self.generation_id,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _write_json_durable(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        fsync_file(path)
        fsync_dir(path.parent)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _write_exclusive_durable(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        fsync_file(path)
        fsync_dir(path.parent)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DerivedStateError(f"unreadable JSON evidence: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DerivedStateError(f"JSON evidence is not an object: {path}")
    return payload


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(
        microsecond=0
    ).isoformat()


def lineage_for_sha(source_release_sha: str) -> str:
    known = {
        "8b3cc2712432ca21007be4db0667301d48b89d97": "LINEAGE-A-LEGACY",
        "a26d664972075b2a220a466215cf71e389decb86": "LINEAGE-B-V2-GENESIS",
    }
    return known.get(source_release_sha, f"LINEAGE-{source_release_sha[:12]}")

def read_lineage(data_root: Path) -> LineageIdentity | None:
    marker = Path(data_root) / LINEAGE_FILE
    if not marker.is_file():
        return None
    payload = _read_json(marker)
    if payload.get("schema") != LINEAGE_SCHEMA:
        raise StateLineageError(f"unsupported lineage marker: {marker}")
    identity = LineageIdentity(
        lineage_id=str(payload.get("lineage_id") or ""),
        source_release_sha=str(payload.get("source_release_sha") or ""),
        generation_id=str(payload.get("generation_id") or ""),
    )
    identity.validate()
    return identity


def write_lineage(
    data_root: Path, identity: LineageIdentity, *, replace: bool = False
) -> Path:
    identity.validate()
    marker = Path(data_root) / LINEAGE_FILE
    existing = read_lineage(data_root)
    if existing is not None and existing != identity and not replace:
        raise StateLineageError(
            "refusing to overwrite a different lineage marker: "
            f"{existing.lineage_id} -> {identity.lineage_id}"
        )
    _write_json_durable(
        marker,
        {
            "schema": LINEAGE_SCHEMA,
            "recorded_at_utc": _utc_now(),
            **identity.as_dict(),
        },
    )
    return marker


def _safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    target = (root / relative).resolve(strict=False)
    if target != root and root not in target.parents:
        raise DerivedStateError(f"artifact escapes data root: {relative}")
    return target


def _sqlite_metadata(path: Path) -> dict[str, str]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise DerivedStateError(f"cannot open sqlite evidence {path}: {exc}") from exc
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        result: dict[str, str] = {}
        if "metadata" in tables:
            for key, value in connection.execute(
                "SELECT key, value FROM metadata ORDER BY key"
            ):
                result[str(key)] = str(value)
        return result
    finally:
        connection.close()

def _iter_spec_files(root: Path, relative: str, kind: str) -> list[Path]:
    target = _safe_path(root, relative)
    if kind == "dir":
        if not target.is_dir():
            return []
        files: list[Path] = []
        for child in sorted(target.rglob("*")):
            if child.is_symlink():
                raise DerivedStateError(f"symlink is not archiveable: {child}")
            if child.is_file():
                files.append(child)
        return files
    if target.is_symlink():
        raise DerivedStateError(f"symlink is not archiveable: {target}")
    return [target] if target.is_file() else []


def _sidecar_facts(path: Path) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        facts[suffix] = (
            {"present": True, "size": sidecar.stat().st_size, "sha256": _sha256(sidecar)}
            if sidecar.is_file()
            else {"present": False, "size": 0, "sha256": None}
        )
    return facts


def inventory(
    data_root: Path, *, known_gaps: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    root = Path(data_root).resolve()
    if not root.is_dir():
        raise DerivedStateError(f"data root is missing: {root}")
    artifacts: list[dict[str, Any]] = []
    missing: list[str] = []
    for spec in ARTIFACT_SPECS:
        relative = str(spec["path"])
        kind = str(spec["kind"])
        files = _iter_spec_files(root, relative, kind)
        if not files:
            missing.append(relative)
            continue
        for path in files:
            entry: dict[str, Any] = {
                "path": path.relative_to(root).as_posix(),
                "spec_path": relative,
                "spec_kind": kind,
                "category": str(spec["category"]),
                "kind": "file" if kind == "dir" else kind,
                "reason": str(spec["reason"]),
                "status": "PRESENT",
                "size": path.stat().st_size,
                "sha256": _sha256(path),
                "mtime_utc": _mtime(path),
                "mode": oct(path.stat().st_mode & 0o777),
            }
            if kind == "sqlite":
                entry["sqlite_metadata"] = _sqlite_metadata(path)
                entry["sidecars"] = _sidecar_facts(path)
            artifacts.append(entry)
    return {
        "schema": SCHEMA,
        "generated_at_utc": _utc_now(),
        "data_root": str(root),
        "artifacts": artifacts,
        "missing": sorted(missing),
        "known_gaps": [dict(gap) for gap in known_gaps],
    }

def _snapshot_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
        result = target_connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise DerivedStateError(f"sqlite snapshot failed quick_check: {source}")
    finally:
        target_connection.close()
        source_connection.close()
    os.chmod(destination, 0o600)
    fsync_file(destination)


def _copy_plain(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.chmod(destination, 0o600)
    fsync_file(destination)


def _archive_one(
    source: Path,
    destination: Path,
    kind: str,
    *,
    copy_hook: Callable[[Path, Path], None] | None = None,
) -> None:
    if copy_hook is not None:
        copy_hook(source, destination)
    elif kind == "sqlite":
        _snapshot_sqlite(source, destination)
    else:
        _copy_plain(source, destination)
    fsync_file(destination)


def _receipt_path(archive_root: Path) -> Path:
    return archive_root / "receipt.json"


def _manifest_path(archive_root: Path) -> Path:
    return archive_root / "archive_manifest.json"


def _remove_derived(data_root: Path, entries: Iterable[Mapping[str, Any]]) -> list[str]:
    removed: list[str] = []
    root = Path(data_root)
    for entry in entries:
        if entry.get("category") != "derived":
            continue
        relative = str(entry.get("spec_path") or entry.get("path"))
        target = _safe_path(root, relative)
        kind = str(entry.get("spec_kind") or entry.get("kind"))
        if kind == "sqlite":
            for suffix in SQLITE_SUFFIXES:
                candidate = Path(str(target) + suffix)
                if candidate.exists():
                    candidate.unlink()
                    removed.append(str(candidate.relative_to(root)))
        elif kind == "dir":
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(str(target.relative_to(root)))
        elif target.exists():
            target.unlink()
            removed.append(str(target.relative_to(root)))
    return removed

def archive_derived_state(
    *,
    data_root: Path,
    archive_root: Path,
    lineage: LineageIdentity,
    active_lineage: str,
    reason: str,
    retire: bool = False,
    preserve_inputs: bool = True,
    known_gaps: Sequence[Mapping[str, Any]] = (),
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    copy_hook: Callable[[Path, Path], None] | None = None,
) -> dict[str, Any]:
    """Copy evidence, verify it, then optionally retire derived active state."""
    lineage.validate()
    data_root = Path(data_root).resolve()
    archive_root = Path(archive_root).resolve()
    if archive_root == data_root or data_root in archive_root.parents:
        raise DerivedStateError('archive root must be outside the writable data root')
    if archive_root.exists():
        receipt = _receipt_path(archive_root)
        if receipt.is_file():
            existing = _read_json(receipt)
            if (
                existing.get("status") == "VERIFIED"
                and existing.get("lineage_id") == lineage.lineage_id
                and existing.get("source_release_sha") == lineage.source_release_sha
            ):
                removed = (
                    _remove_derived(data_root, existing.get("artifacts") or [])
                    if retire
                    else []
                )
                return {
                    "status": "ALREADY_VERIFIED",
                    "archive_root": str(archive_root),
                    "receipt_path": str(receipt),
                    "removed": removed,
                }
        raise IncompleteArchiveError(
            f"archive exists without matching VERIFIED receipt: {archive_root}"
        )

    current = inventory(data_root, known_gaps=known_gaps)
    selected = [
        entry
        for entry in current["artifacts"]
        if entry["category"] == "derived"
        or (preserve_inputs and entry["category"] == "input")
    ]
    required = sum(int(entry["size"]) for entry in selected)
    required += max(64 * 1024 * 1024, required // 10)
    archive_root.parent.mkdir(parents=True, exist_ok=True)
    free = int(disk_usage(archive_root.parent).free)
    if free < required:
        raise InsufficientSpaceError(
            f"insufficient disk: free={free} required={required}"
        )
    staging = archive_root.parent / (
        f".{archive_root.name}.staging-{os.getpid()}-{int(time.time() * 1000)}"
    )
    if staging.exists():
        raise IncompleteArchiveError(f"staging already exists: {staging}")
    staging.mkdir(mode=0o700)
    try:
        archived: list[dict[str, Any]] = []
        for entry in selected:
            source = _safe_path(data_root, entry["path"])
            destination = staging / "payload" / entry["path"]
            kind = "sqlite" if entry["kind"] == "sqlite" else "file"
            _archive_one(source, destination, kind, copy_hook=copy_hook)
            item = dict(entry)
            item["archived_size"] = destination.stat().st_size
            item["archived_sha256"] = _sha256(destination)
            item["snapshot"] = "sqlite_backup" if kind == "sqlite" else "copy"
            archived.append(item)
        payload = {
            "schema": SCHEMA,
            "status": "PREPARED",
            "created_at_utc": _utc_now(),
            "reason": reason,
            "lineage_id": lineage.lineage_id,
            "source_release_sha": lineage.source_release_sha,
            "generation_id": lineage.generation_id,
            "active_lineage": active_lineage,
            "artifacts": archived,
            "known_gaps": [dict(gap) for gap in known_gaps],
            "missing": current["missing"],
            "historical_evidence_modified": False,
        }
        _write_json_durable(_manifest_path(staging), payload)
        checks = _verify_payload(staging, payload)
        if checks["failures"]:
            raise DerivedStateError(f"archive verification failed: {checks['failures']}")
        directories = {
            path.parent for path in (staging / "payload").rglob("*") if path.is_dir()
        }
        for directory in sorted(
            directories, key=lambda item: len(item.parts), reverse=True
        ):
            fsync_dir(directory)
        fsync_dir(staging)
        os.replace(staging, archive_root)
        fsync_dir(archive_root.parent)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    manifest_sha = _sha256(_manifest_path(archive_root))
    receipt = {
        "receipt": RECEIPT_KIND,
        "schema": SCHEMA,
        "status": "VERIFIED",
        "recorded_at_utc": _utc_now(),
        **lineage.as_dict(),
        "active_lineage": active_lineage,
        "reason": reason,
        "archive_manifest_sha256": manifest_sha,
        "artifact_count": len(archived),
        "artifacts": archived,
        "known_gaps": [dict(gap) for gap in known_gaps],
        "historical_evidence_modified": False,
        "source_artifact_inventory": current,
    }
    _write_exclusive_durable(_receipt_path(archive_root), receipt)

    removed: list[str] = []
    if retire:
        removed = _remove_derived(data_root, archived)
        _write_exclusive_durable(
            archive_root / "retirement.json",
            {
                "schema": SCHEMA,
                "status": "RETIRED",
                "recorded_at_utc": _utc_now(),
                "archive_receipt_sha256": _sha256(_receipt_path(archive_root)),
                "removed": removed,
            },
        )
    return {
        "status": "VERIFIED",
        "archive_root": str(archive_root),
        "receipt_path": str(_receipt_path(archive_root)),
        "receipt_sha256": _sha256(_receipt_path(archive_root)),
        "manifest_sha256": manifest_sha,
        "archived": len(archived),
        "removed": removed,
    }

def _verify_payload(archive_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    corrupted: list[str] = []
    missing: list[str] = []
    for entry in manifest.get("artifacts") or []:
        relative = str(entry.get("path") or "")
        path = archive_root / "payload" / relative
        if not path.is_file():
            missing.append(relative)
            continue
        if path.stat().st_size != int(entry.get("archived_size", -1)):
            corrupted.append(relative)
            continue
        if _sha256(path) != entry.get("archived_sha256"):
            corrupted.append(relative)
            continue
        if entry.get("snapshot") == "sqlite_backup":
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    result = connection.execute("PRAGMA quick_check").fetchone()
                finally:
                    connection.close()
                if not result or result[0] != "ok":
                    corrupted.append(relative)
            except sqlite3.Error:
                corrupted.append(relative)
    checks.append({"name": "payload_no_missing_files", "passed": not missing, "detail": f"missing={missing}"})
    checks.append({"name": "payload_content_verified", "passed": not corrupted, "detail": f"corrupted={corrupted}"})
    return {"checks": checks, "failures": [c["name"] for c in checks if not c["passed"]]}


def verify_archive(archive_root: Path) -> dict[str, Any]:
    root = Path(archive_root)
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    manifest_path = _manifest_path(root)
    receipt_path = _receipt_path(root)
    add("archive_present", root.is_dir(), str(root))
    add("archive_manifest_present", manifest_path.is_file(), str(manifest_path))
    add("archive_receipt_present", receipt_path.is_file(), str(receipt_path))
    if not (manifest_path.is_file() and receipt_path.is_file()):
        return {"checks": checks, "failures": [c["name"] for c in checks if not c["passed"]]}
    manifest = _read_json(manifest_path)
    receipt = _read_json(receipt_path)
    add(
        "archive_receipt_verified",
        receipt.get("status") == "VERIFIED"
        and receipt.get("receipt") == RECEIPT_KIND
        and receipt.get("historical_evidence_modified") is False,
        f"status={receipt.get('status')}",
    )
    add(
        "archive_manifest_digest",
        _sha256(manifest_path) == receipt.get("archive_manifest_sha256"),
        "manifest sha256 matches receipt",
    )
    add(
        "archive_identity",
        manifest.get("lineage_id") == receipt.get("lineage_id")
        and manifest.get("source_release_sha") == receipt.get("source_release_sha")
        and manifest.get("generation_id") == receipt.get("generation_id"),
        f"lineage={manifest.get('lineage_id')}",
    )
    payload = _verify_payload(root, manifest)
    checks.extend(payload["checks"])
    return {"checks": checks, "failures": [c["name"] for c in checks if not c["passed"]]}

def bind_state_connection(
    connection: sqlite3.Connection, data_root: Path, consumer: str
) -> LineageIdentity | None:
    """Bind a new sidecar to the active lineage, or fail closed."""
    identity = read_lineage(data_root)
    if identity is None:
        return None
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = 'lineage_id'"
    ).fetchone()
    if row is None:
        tables = [
            str(item[0])
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        metadata_keys = {str(item[0]) for item in connection.execute('SELECT key FROM metadata')}
        populated = bool(metadata_keys - {'lineage_id', 'lineage_source_release_sha', 'lineage_generation_id'})
        for table in tables:
            if table == "metadata":
                continue
            count = connection.execute(
                f'SELECT count(*) FROM "{table}"'
            ).fetchone()[0]
            if count:
                populated = True
                break
        if populated:
            raise StateLineageError(
                f"STATE_LINEAGE_UNBOUND:{consumer}: legacy sidecar has state "
                "but no lineage identity"
            )
        for key, value in (
            ("lineage_id", identity.lineage_id),
            ("lineage_source_release_sha", identity.source_release_sha),
            ("lineage_generation_id", identity.generation_id),
        ):
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", (key, value)
            )
        connection.commit()
        return identity
    if str(row[0]) != identity.lineage_id:
        raise StateLineageError(
            f"STATE_LINEAGE_MISMATCH:{consumer}: "
            f"state={row[0]} active={identity.lineage_id}"
        )
    return identity

def _known_gaps(values: Sequence[str]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for value in values:
        try:
            gap = json.loads(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid --known-gap JSON: {exc}") from exc
        if not isinstance(gap, dict):
            raise argparse.ArgumentTypeError("--known-gap must be a JSON object")
        gaps.append(gap)
    return gaps


def _identity(args: argparse.Namespace) -> LineageIdentity:
    identity = LineageIdentity(
        lineage_id=args.lineage_id,
        source_release_sha=args.source_sha,
        generation_id=args.generation_id,
    )
    identity.validate()
    return identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lineage-aware learning state")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    inspect.add_argument("--known-gap", action="append", default=[])

    for name in ("archive", "retire"):
        command = sub.add_parser(name)
        command.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
        command.add_argument("--archive-root", required=True)
        command.add_argument("--lineage-id", required=True)
        command.add_argument("--source-sha", required=True)
        command.add_argument("--generation-id", required=True)
        command.add_argument("--active-lineage", required=True)
        command.add_argument("--reason", default=name)
        command.add_argument("--preserve-inputs", action="store_true", default=True)
        command.add_argument("--no-preserve-inputs", dest="preserve_inputs", action="store_false")
        command.add_argument("--known-gap", action="append", default=[])

    verify = sub.add_parser("verify")
    verify.add_argument("--archive-root", required=True)

    mark = sub.add_parser("mark-lineage")
    mark.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    mark.add_argument("--lineage-id", required=True)
    mark.add_argument("--source-sha", required=True)
    mark.add_argument("--generation-id", required=True)
    mark.add_argument("--replace", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "inspect":
        print(json.dumps(inventory(Path(args.data_root), known_gaps=_known_gaps(args.known_gap)), indent=2, sort_keys=True))
        return 0
    if args.command in ("archive", "retire"):
        result = archive_derived_state(
            data_root=Path(args.data_root),
            archive_root=Path(args.archive_root),
            lineage=_identity(args),
            active_lineage=args.active_lineage,
            reason=args.reason,
            retire=args.command == "retire",
            preserve_inputs=args.preserve_inputs,
            known_gaps=_known_gaps(args.known_gap),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "verify":
        result = verify_archive(Path(args.archive_root))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if not result["failures"] else 2
    marker = write_lineage(Path(args.data_root), _identity(args), replace=args.replace)
    print(json.dumps({"marker": str(marker)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
