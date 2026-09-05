from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterable

import zstandard as zstd


@dataclass(frozen=True)
class SegmentLifecycleAssessment:
    path: str
    tier: str
    age_days: int
    compression: str
    finalized: bool
    checksum_verified: bool
    manifest_recorded: bool
    archive_verified: bool
    offhost_verified: bool
    cleanup_eligible: bool
    blockers: list[str]


def _age_days(path: Path, *, now: datetime) -> int:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    delta = now - modified
    return max(0, int(delta.total_seconds() // 86400))


def _tier_for_age(age_days: int) -> str:
    if age_days < 7:
        return "HOT"
    if age_days < 90:
        return "WARM"
    return "COLD"


def _drain_reader(reader: object) -> None:
    while True:
        chunk = reader.read(1024 * 1024)  # type: ignore[attr-defined]
        if not chunk:
            return


def _compression(path: Path) -> str:
    """Return a compression type only after the complete stream validates."""
    name = path.name.lower()
    compressed_suffix = name.endswith(".gz") or name.endswith(".zst")
    if compressed_suffix:
        try:
            if path.stat().st_size == 0:
                return "invalid"
        except OSError:
            return "invalid"
    if name.endswith(".gz"):
        try:
            with gzip.open(path, "rb") as handle:
                _drain_reader(handle)
        except (EOFError, OSError):
            return "invalid"
        return "gzip"
    if name.endswith(".zst"):
        try:
            with path.open("rb") as source:
                with zstd.ZstdDecompressor().stream_reader(source, read_across_frames=True) as reader:
                    _drain_reader(reader)
        except (EOFError, OSError, ValueError, zstd.ZstdError):
            return "invalid"
        return "zstd"
    return "none"


def _checksum_sha(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _has_manifest_entry(manifest: Path, segment: Path) -> bool:
    try:
        if not manifest.is_file():
            return False
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    token = segment.name
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() not in {"segment", "segment_name", "file", "name"}:
            continue
        if value.strip() == token:
            return True
    return False


def _verify_checksum_sidecar(segment: Path) -> bool:
    checksum_path = segment.with_suffix(segment.suffix + ".sha256")
    try:
        if not checksum_path.is_file():
            return False
        fields = checksum_path.read_text(encoding="utf-8").strip().split(maxsplit=1)
    except (OSError, UnicodeDecodeError):
        return False
    if not fields or len(fields[0]) != 64:
        return False
    actual = _checksum_sha(segment)
    return actual is not None and fields[0].lower() == actual


def assess_segment(
    segment: Path,
    *,
    now: datetime | None = None,
) -> SegmentLifecycleAssessment:
    """Assess lifecycle state; COLD cleanup always requires off-host verification."""
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_days = _age_days(segment, now=clock)
    tier = _tier_for_age(age_days)
    compression = _compression(segment)

    finalized = segment.with_suffix(segment.suffix + ".finalized").is_file()
    checksum_verified = _verify_checksum_sidecar(segment)
    manifest_recorded = _has_manifest_entry(segment.parent / "manifest.env", segment)
    archive_verified = segment.with_suffix(segment.suffix + ".archive.verified").is_file()
    offhost_verified = segment.with_suffix(segment.suffix + ".offhost.verified").is_file()

    blockers: list[str] = []
    if tier in {"WARM", "COLD"}:
        if not finalized:
            blockers.append("segment_not_finalized")
        if not checksum_verified:
            blockers.append("checksum_missing_or_mismatch")
        if not manifest_recorded:
            blockers.append("manifest_not_updated")
        if not archive_verified:
            blockers.append("archive_not_verified")
    if tier == "COLD" and not offhost_verified:
        blockers.append("offhost_backup_not_verified")
    if tier in {"WARM", "COLD"} and compression not in {"gzip", "zstd"}:
        blockers.append("warm_cold_segment_must_be_compressed")

    cleanup_eligible = tier == "COLD" and not blockers

    return SegmentLifecycleAssessment(
        path=str(segment),
        tier=tier,
        age_days=age_days,
        compression=compression,
        finalized=finalized,
        checksum_verified=checksum_verified,
        manifest_recorded=manifest_recorded,
        archive_verified=archive_verified,
        offhost_verified=offhost_verified,
        cleanup_eligible=cleanup_eligible,
        blockers=blockers,
    )


def discover_segments(root: Path) -> list[Path]:
    sidecar_suffixes = (
        ".sha256",
        ".finalized",
        ".archive.verified",
        ".offhost.verified",
    )
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(path.name.endswith(suffix) for suffix in sidecar_suffixes)
        and (
            path.suffixes[-2:] == [".jsonl", ".gz"]
            or path.suffixes[-2:] == [".jsonl", ".zst"]
            or path.suffixes[-1:] == [".jsonl"]
        )
        and ".tmp" not in path.name
    )


def assess_root(
    root: Path,
    *,
    now: datetime | None = None,
) -> list[SegmentLifecycleAssessment]:
    return [assess_segment(path, now=now) for path in discover_segments(root)]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assess O'Pip archive lifecycle eligibility")
    parser.add_argument("--root", required=True, help="Path containing finalized archive segments")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--fail-if-cold-unverified",
        action="store_true",
        help="Exit non-zero when any COLD segment is not cleanup-eligible",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"archive root must be an existing directory: {root}", file=sys.stderr)
        return 64
    assessments = assess_root(root)

    if args.json:
        print(json.dumps([asdict(item) for item in assessments], indent=2))
    else:
        for item in assessments:
            print(
                f"{item.tier:>4} age_days={item.age_days:>4} "
                f"cleanup_eligible={str(item.cleanup_eligible).lower():<5} "
                f"compression={item.compression:<5} {item.path}"
            )
            if item.blockers:
                print(f"  blockers={','.join(item.blockers)}")

    if args.fail_if_cold_unverified:
        blocked_cold = [
            item for item in assessments
            if item.tier == "COLD" and not item.cleanup_eligible
        ]
        if blocked_cold:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
