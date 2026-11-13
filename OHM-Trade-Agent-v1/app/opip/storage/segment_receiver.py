"""S2 gzip-segment receiver: verified, durable, files only.

Stdlib plus the existing bounded_jsonl helpers. No database, no network listener, no
subprocess, no deletion. The receiver stores each sealed gzip segment once, identified by
the sha256 recorded in its manifest, and acknowledges it only after the bytes are durable.

Gates, in order, for every segment:
1. Disk reserve, in bytes, BEFORE any write: projected free below 3 GiB pauses ingestion
   (PAUSED_RESERVE); below 2 GiB fails closed (CRITICAL). Nothing is ever auto-deleted.
2. The manifest is well-formed and the segment bytes match its sha256 and size.
3. Streaming content check: every row is a JSON object, the row count matches, nothing
   looks like a credential (fail closed), and the uncompressed size is bounded.
4. Temp write, fsync, atomic rename, directory fsync, then the acknowledgement.

Delivery is at-least-once: a repeated segment is a no-op that returns the same ack. A crash
leaves only temp files (discarded by ``recover``) or a stored segment without an ack (which
``recover`` verifies and acknowledges). Source files are never modified or removed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.opip.storage.bounded_jsonl import (
    fsync_dir,
    parse_json_object_line,
    sha256_file,
)

GIB = 1024**3
RESERVE_BYTES = 3 * GIB
FLOOR_BYTES = 2 * GIB
CHUNK = 1024 * 1024
MAX_ROW_BYTES = 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 16 * GIB
FLOOR_RECHECK_BYTES = 64 * 1024 * 1024

OK = "OK"
PAUSED_RESERVE = "PAUSED_RESERVE"
CRITICAL = "CRITICAL"

ACCEPTED = "ACCEPTED"
DUPLICATE = "DUPLICATE"
RECOVERED = "RECOVERED"
REFUSED_RESERVE = "REFUSED_RESERVE"
REJECTED_MANIFEST = "REJECTED_MANIFEST"
REJECTED_CHECKSUM = "REJECTED_CHECKSUM"
REJECTED_CONTENT = "REJECTED_CONTENT"
REJECTED_CREDENTIAL = "REJECTED_CREDENTIAL"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STREAM = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Same shapes the S2-0 credential count used: bot token, key/value secret, private key.
_CREDENTIAL = re.compile(
    rb"(?P<bot>\d{6,}:[A-Za-z0-9_-]{30,})"
    rb"|(?i:\"(?P<key>api[_-]?key|secret|password|authorization|bearer|ingest_token)\""
    rb"\s*:\s*\"[A-Za-z0-9._~+/=-]{16,}\")"
    rb"|(?P<pem>-----BEGIN [A-Z ]*PRIVATE KEY)"
)


@dataclass(frozen=True)
class Outcome:
    result: str
    reason: str = ""
    segment_id: str = ""


class FloorBreached(Exception):
    """Free space fell below the hard floor while a segment was being written."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".tmp-{path.name}-{os.getpid()}")
    with tmp.open("wb") as handle:
        handle.write(json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


class SegmentReceiver:
    def __init__(
        self,
        store_root: Path,
        *,
        free_bytes: Callable[[Path], int] = _free_bytes,
        max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
    ) -> None:
        self.store_root = Path(store_root)
        self._free_bytes = free_bytes
        self._max_uncompressed = max_uncompressed_bytes
        self.segments_dir = self.store_root / "segments"
        self.acks_dir = self.store_root / "acks"
        for directory in (self.segments_dir, self.acks_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.status_file = self.store_root / "receiver-status.json"

    # ---- paths -------------------------------------------------------------
    def _segment_path(self, stream: str, segment_id: str) -> Path:
        return self.segments_dir / stream / segment_id[:2] / f"{segment_id}.jsonl.gz"

    def _ack_path(self, segment_id: str) -> Path:
        return self.acks_dir / f"{segment_id}.ack"

    # ---- status ------------------------------------------------------------
    def _status(self, state: str, reason: str = "") -> None:
        _write_json_atomic(
            self.status_file,
            {
                "state": state,
                "reason": reason,
                "free_bytes": self._free_bytes(self.store_root),
                "at_utc": _utc_now(),
            },
        )

    # ---- recovery ----------------------------------------------------------
    def recover(self) -> list[Outcome]:
        """Discard temp files; acknowledge stored segments that lack an ack."""
        for temp in self.store_root.rglob(".tmp-*"):
            if temp.is_file():
                temp.unlink()
        outcomes: list[Outcome] = []
        for manifest_path in sorted(self.segments_dir.rglob("*.manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                segment_id = manifest["sha256"]
                segment = self._segment_path(manifest["stream"], segment_id)
            except (OSError, ValueError, KeyError):
                outcomes.append(Outcome(REJECTED_MANIFEST, f"unreadable stored manifest {manifest_path.name}"))
                continue
            if self._ack_path(segment_id).exists():
                continue
            if segment.is_file() and sha256_file(segment) == segment_id:
                self._write_ack(segment_id, manifest)
                outcomes.append(Outcome(RECOVERED, "stored segment acknowledged", segment_id))
            else:
                outcomes.append(Outcome(REJECTED_CHECKSUM, "stored segment failed verification; left in place", segment_id))
        return outcomes

    # ---- accept ------------------------------------------------------------
    def accept(self, segment_path: Path, manifest_path: Path) -> Outcome:
        segment_path, manifest_path = Path(segment_path), Path(manifest_path)
        manifest, problem = self._read_manifest(manifest_path)
        if manifest is None:
            return Outcome(REJECTED_MANIFEST, problem)
        segment_id = manifest["sha256"]

        if self._ack_path(segment_id).exists():
            return Outcome(DUPLICATE, "already acknowledged", segment_id)

        projected = self._free_bytes(self.store_root) - manifest["size_bytes"]
        if projected < FLOOR_BYTES:
            self._status(CRITICAL, "projected free space below the 2 GiB floor")
            return Outcome(REFUSED_RESERVE, CRITICAL, segment_id)
        if projected < RESERVE_BYTES:
            self._status(PAUSED_RESERVE, "projected free space below the 3 GiB reserve")
            return Outcome(REFUSED_RESERVE, PAUSED_RESERVE, segment_id)

        try:
            size = segment_path.stat().st_size
        except OSError:
            return Outcome(REJECTED_CHECKSUM, "segment file unreadable", segment_id)
        if size != manifest["size_bytes"] or sha256_file(segment_path) != segment_id:
            return Outcome(REJECTED_CHECKSUM, "size or sha256 does not match the manifest", segment_id)

        verdict = self._verify_content(segment_path, manifest["row_count"])
        if verdict is not None:
            return Outcome(verdict[0], verdict[1], segment_id)

        final = self._segment_path(manifest["stream"], segment_id)
        final.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._durable_copy(segment_path, final, segment_id)
        except FloorBreached:
            self._status(CRITICAL, "free space fell below the 2 GiB floor while writing")
            return Outcome(REFUSED_RESERVE, CRITICAL, segment_id)
        _write_json_atomic(final.with_name(f"{segment_id}.manifest.json"), manifest)
        self._write_ack(segment_id, manifest)
        self._status(OK)
        return Outcome(ACCEPTED, "", segment_id)

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def _read_manifest(path: Path) -> tuple[dict | None, str]:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "manifest unreadable or not JSON"
        if not isinstance(manifest, dict):
            return None, "manifest must be an object"
        checks = (
            isinstance(manifest.get("stream"), str) and _STREAM.match(manifest["stream"]),
            isinstance(manifest.get("sha256"), str) and _HEX64.match(manifest["sha256"]),
            type(manifest.get("size_bytes")) is int and manifest["size_bytes"] >= 0,
            type(manifest.get("row_count")) is int and manifest["row_count"] >= 0,
        )
        if not all(checks):
            return None, "manifest requires stream, sha256, size_bytes and row_count"
        return manifest, ""

    def _verify_content(self, path: Path, expected_rows: int) -> tuple[str, str] | None:
        rows = 0
        total = 0
        try:
            with gzip.open(path, "rb") as handle:
                while True:
                    line = handle.readline(MAX_ROW_BYTES + 1)
                    if not line:
                        break
                    total += len(line)
                    if len(line) > MAX_ROW_BYTES and not line.endswith(b"\n"):
                        return REJECTED_CONTENT, "row exceeds the maximum row size"
                    if total > self._max_uncompressed:
                        return REJECTED_CONTENT, "uncompressed size exceeds the bound"
                    if not line.strip():
                        continue
                    if _CREDENTIAL.search(line):
                        return REJECTED_CREDENTIAL, "credential-like content; failing closed"
                    try:
                        parse_json_object_line(line)
                    except ValueError:
                        return REJECTED_CONTENT, f"row {rows + 1} is not a JSON object"
                    rows += 1
        except (OSError, EOFError, gzip.BadGzipFile):
            return REJECTED_CONTENT, "segment is not a readable gzip stream"
        if rows != expected_rows:
            return REJECTED_CONTENT, f"row_count {expected_rows} does not match {rows} rows"
        return None

    def _durable_copy(self, source: Path, final: Path, segment_id: str) -> None:
        tmp = final.with_name(f".tmp-{final.name}-{os.getpid()}")
        written = 0
        next_check = FLOOR_RECHECK_BYTES
        try:
            with source.open("rb") as reader, tmp.open("wb") as writer:
                for chunk in iter(lambda: reader.read(CHUNK), b""):
                    writer.write(chunk)
                    written += len(chunk)
                    if written >= next_check:
                        next_check += FLOOR_RECHECK_BYTES
                        if self._free_bytes(self.store_root) < FLOOR_BYTES:
                            raise FloorBreached
                writer.flush()
                os.fsync(writer.fileno())
            if sha256_file(tmp) != segment_id:
                raise OSError("stored copy failed re-verification")
            os.replace(tmp, final)
            fsync_dir(final.parent)
        except BaseException:
            if tmp.exists():
                tmp.unlink()
            raise

    def _write_ack(self, segment_id: str, manifest: dict) -> None:
        _write_json_atomic(
            self._ack_path(segment_id),
            {
                "sha256": segment_id,
                "stream": manifest["stream"],
                "row_count": manifest["row_count"],
                "size_bytes": manifest["size_bytes"],
                "accepted_at_utc": _utc_now(),
            },
        )


def main(argv: list[str] | None = None) -> int:
    """One-shot pass over an incoming directory. Never scheduled or activated by this repo."""
    parser = argparse.ArgumentParser(description="O'Pip S2 gzip-segment receiver (one pass)")
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--incoming", required=True, type=Path)
    args = parser.parse_args(argv)
    receiver = SegmentReceiver(args.store)
    receiver.recover()
    worst = 0
    for manifest_path in sorted(args.incoming.glob("*.manifest.json")):
        segment = manifest_path.with_name(manifest_path.name[: -len(".manifest.json")] + ".jsonl.gz")
        outcome = receiver.accept(segment, manifest_path)
        print(f"{manifest_path.name} {outcome.result} {outcome.reason}".rstrip())
        if outcome.result.startswith("REFUSED"):
            return 75
        if outcome.result.startswith("REJECTED"):
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
