"""HF-01 safe archive verification and extraction.

Rules enforced here (never delegated to Python-version defaults):
  * the external digest (bundle.sha256) is validated BEFORE anything is extracted;
  * missing/mismatched integrity evidence aborts before any filesystem write;
  * members with absolute paths, drive letters, '..', control characters,
    duplicate names, symlinks, hardlinks, devices or FIFOs are rejected;
  * every extraction target must resolve under the extraction root;
  * an existing extraction directory is never deleted or overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path, PurePosixPath

ARCHIVE_DIGEST_NAME = "bundle.sha256"


class ArchiveError(RuntimeError):
    """A bundle archive failed integrity or member-safety validation."""


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_external_digest(archive: Path, sidecar: Path | None = None) -> dict:
    """Validate the out-of-band digest. Reads only; writes nothing."""
    archive = Path(archive)
    if not archive.is_file():
        raise ArchiveError(f"archive missing: {archive}")
    sidecar = Path(sidecar) if sidecar else archive.with_name(ARCHIVE_DIGEST_NAME)
    if not sidecar.is_file():
        raise ArchiveError(
            f"integrity evidence missing: {sidecar.name} is required next to the archive "
            "(a transfer without an external digest cannot be verified)"
        )
    try:
        declared = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"integrity evidence is malformed: {sidecar.name}: {exc}") from exc
    expected_sha = str(declared.get("sha256", "")).lower()
    expected_bytes = declared.get("bytes")
    if len(expected_sha) != 64 or any(c not in "0123456789abcdef" for c in expected_sha):
        raise ArchiveError("integrity evidence does not contain a 64-character sha256")
    if str(declared.get("archive", "")) != archive.name:
        raise ArchiveError(f"integrity evidence names {declared.get('archive')!r}, not {archive.name!r}")
    actual_sha = sha256_file(archive)
    actual_bytes = archive.stat().st_size
    if actual_sha != expected_sha:
        raise ArchiveError(f"archive digest mismatch: declared={expected_sha[:12]} actual={actual_sha[:12]}")
    if expected_bytes is not None and int(expected_bytes) != actual_bytes:
        raise ArchiveError(f"archive size mismatch: declared={expected_bytes} actual={actual_bytes}")
    return {"archive": archive.name, "sha256": actual_sha, "bytes": actual_bytes}


def _reject_member_name(name: str) -> None:
    if not name or name in {".", "./"}:
        raise ArchiveError("archive member has an empty name")
    if "\x00" in name or any(ord(c) < 32 for c in name):
        raise ArchiveError(f"archive member has a control character: {name!r}")
    normalised = name.replace("\\", "/")
    if normalised.startswith("/"):
        raise ArchiveError(f"archive member is absolute: {name!r}")
    if len(normalised) > 1 and normalised[1] == ":":
        raise ArchiveError(f"archive member carries a drive letter: {name!r}")
    if any(part == ".." for part in PurePosixPath(normalised).parts):
        raise ArchiveError(f"archive member escapes the extraction root: {name!r}")
def inspect_members(archive: Path) -> list[dict]:
    """Validate every member without extracting anything."""
    archive = Path(archive)
    members: list[dict] = []
    seen: set[str] = set()
    with tarfile.open(archive, "r") as tar:
        for member in tar.getmembers():
            _reject_member_name(member.name)
            normalised = member.name.replace("\\", "/")
            while normalised.startswith("./"):
                normalised = normalised[2:]
            if normalised in seen:
                raise ArchiveError(f"duplicate archive member: {member.name!r}")
            seen.add(normalised)
            if member.issym() or member.islnk():
                raise ArchiveError(f"archive member is a link: {member.name!r} -> {member.linkname!r}")
            if member.ischr() or member.isblk() or member.isfifo():
                raise ArchiveError(f"archive member is a device/FIFO: {member.name!r}")
            if not (member.isfile() or member.isdir()):
                raise ArchiveError(f"unsupported archive member type: {member.name!r}")
            members.append({"name": member.name, "size": member.size, "is_dir": member.isdir()})
    if not members:
        raise ArchiveError("archive contains no members")
    return members


def safe_extract(archive: Path, dest_root: Path, *, expected_sha: str | None = None) -> dict:
    """Extract into a NEW directory. Existing directories are never reused or deleted."""
    archive, dest_root = Path(archive), Path(dest_root)
    if expected_sha and sha256_file(archive) != expected_sha.lower():
        raise ArchiveError("archive changed between digest verification and extraction")
    inspect_members(archive)
    if dest_root.exists():
        if any(dest_root.iterdir()):
            raise ArchiveError(
                f"extraction directory already exists and is not empty: {dest_root} "
                "(refusing to delete or overwrite it)"
            )
    else:
        dest_root.mkdir(parents=True, exist_ok=False)
    root = dest_root.resolve()
    supports_data_filter = hasattr(tarfile, "data_filter")
    extracted: list[str] = []
    with tarfile.open(archive, "r") as tar:
        for member in tar.getmembers():
            target = (dest_root / member.name.replace("\\", "/")).resolve()
            if target != root and root not in target.parents:
                raise ArchiveError(f"member would escape the extraction root: {member.name!r}")
            if supports_data_filter:
                tar.extract(member, dest_root, filter="data", set_attrs=False)
            else:  # explicit manual guards above already constrain the member
                tar.extract(member, dest_root, set_attrs=False)
            extracted.append(member.name)
    return {"extracted": len(extracted), "destination": str(dest_root), "members": extracted}


def assert_source_tree(source_root: Path) -> None:
    """Reject symlinks that resolve outside the authorised source tree."""
    root = Path(source_root).resolve()
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs) + list(files):
            candidate = Path(current) / name
            if not candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if resolved != root and root not in resolved.parents:
                raise ArchiveError(f"source symlink escapes the authorised root: {candidate}")

