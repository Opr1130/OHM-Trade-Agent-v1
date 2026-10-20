"""L-02 release provenance: certify a Git-free release extraction by equivalence.

The V2 release is installed from the pinned commit as a plain tree. It has no
``.git`` and no installer manifest, so "is this the certified commit?" cannot be
answered by Git metadata on the host. This tool answers the question offline:

  build      expected inventory from a trusted checkout or archive of the pinned
             commit (never from the candidate host files)
  inventory  actual inventory of a tree (never trusted by itself)
  compare    full path-by-path equivalence, classifying permitted runtime files

It is read-only: it never writes to the tree it inventories. The installer
policy is mirrored exactly from ``bootstrap-v2.sh manifest_stream`` so an
expected inventory describes the same bytes the installer would accept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
SCHEMA = 1
POLICY_ID = "m07-release-inventory-v1"
#: Directories pruned by the installer's ``manifest_stream``.
EXCLUDED_DIRS = (".git", "data", "__pycache__")
#: Files pruned by the installer's ``manifest_stream``.
EXCLUDED_NAMES = (".env",)
TRUSTED_PROVENANCE = ("git-checkout", "git-archive")
MAX_RUNTIME_DETAIL = 200

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INSUFFICIENT = 2
EXIT_REFUSED = 3


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _runtime_reason(relative: str) -> str | None:
    parts = relative.split("/")
    if parts and parts[0] in EXCLUDED_DIRS:
        return parts[0]
    if parts and parts[-1] in EXCLUDED_NAMES:
        return "env"
    return None


def _manifest_digest(files: list[dict]) -> str:
    """Digest the canonical ``sha256  path`` stream, installer-compatible."""
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda item: item["path"]):
        digest.update(f"{entry['sha256']}  {entry['path']}\n".encode("utf-8"))
    return digest.hexdigest()


def _runtime_inventory(root: Path, detail_limit: int) -> tuple[list[dict], int, int]:
    """Count permitted runtime-only files without hashing them.

    Excluded directories are walked separately so the certified inventory stays
    fast and its manifest never includes runtime data, while an actual host
    observation still reports the runtime footprint and a bounded detail list.
    """
    detail: list[dict] = []
    total = 0
    total_bytes = 0
    for excluded in EXCLUDED_DIRS:
        base = root / excluded
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base, followlinks=False):
            for name in sorted(filenames):
                path = Path(dirpath) / name
                try:
                    size = path.lstat().st_size
                except OSError:
                    size = -1
                total += 1
                total_bytes += max(size, 0)
                if len(detail) < detail_limit:
                    detail.append({
                        "path": path.relative_to(root).as_posix(),
                        "bytes": size,
                        "reason": excluded,
                    })
    for name in EXCLUDED_NAMES:
        path = root / name
        if path.is_file():
            size = path.lstat().st_size
            total += 1
            total_bytes += max(size, 0)
            if len(detail) < detail_limit:
                detail.append({"path": name, "bytes": size, "reason": "env"})
    return detail, total, total_bytes


def scan_tree(root: Path, *, runtime_detail_limit: int = MAX_RUNTIME_DETAIL) -> dict:
    """Inventory one tree without following symlinks and without writing."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"release root is not a directory: {root}")
    files: list[dict] = []
    symlinks: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if _runtime_reason(name) is None
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            relative = path.relative_to(root).as_posix()
            if _runtime_reason(relative) is not None:
                continue
            try:
                size = path.lstat().st_size
            except OSError:
                size = -1
            if path.is_symlink():
                symlinks.append({"path": relative, "target": str(os.readlink(path))})
                continue
            if not path.is_file():
                continue
            files.append({"path": relative, "sha256": _sha256_file(path), "bytes": size})
    files.sort(key=lambda item: item["path"])
    symlinks.sort(key=lambda item: item["path"])
    runtime, runtime_total, runtime_bytes = _runtime_inventory(root, runtime_detail_limit)
    return {
        "source_files": files,
        "source_symlinks": symlinks,
        "runtime_files": runtime,
        "counts": {
            "source_files": len(files),
            "source_symlinks": len(symlinks),
            "source_bytes": sum(item["bytes"] for item in files),
            "runtime_files": runtime_total,
            "runtime_bytes": runtime_bytes,
            "runtime_detail_truncated": runtime_total > len(runtime),
        },
        "manifest_digest": _manifest_digest(files),
    }


def _policy() -> dict:
    return {
        "id": POLICY_ID,
        "excluded_dirs": list(EXCLUDED_DIRS),
        "excluded_names": list(EXCLUDED_NAMES),
        "follow_symlinks": False,
    }


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_checkout_provenance(root: Path, certified_sha: str) -> dict:
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise ValueError(f"expected source is not a git checkout: {root}")
    observed = head.stdout.strip()
    if observed != certified_sha:
        raise ValueError(f"git HEAD {observed} is not the certified SHA {certified_sha}")
    status = _git(root, "status", "--porcelain", "--", ".")
    if status.returncode != 0:
        raise ValueError("cannot read git status for the expected source")
    if status.stdout.strip():
        raise ValueError("expected source checkout has uncommitted changes")
    tracked = _git(root, "ls-tree", "-r", "--name-only", "HEAD")
    if tracked.returncode != 0:
        raise ValueError("cannot read the tracked file list from the expected source")
    # `ls-tree` reports paths relative to the current directory when run inside
    # a subdirectory, which is exactly the release-root namespace we compare in.
    tracked_paths = {
        line.strip()
        for line in tracked.stdout.splitlines()
        if line.strip() and _runtime_reason(line.strip()) is None
    }
    return {
        "kind": "git-checkout",
        "head": observed,
        "clean": True,
        "tracked_paths": tracked_paths,
    }


def _archive_provenance(archive: Path, expected_digest: str) -> dict:
    archive = Path(archive)
    if not archive.is_file():
        raise ValueError(f"certified archive is missing: {archive}")
    observed = _sha256_file(archive)
    if not expected_digest or observed != expected_digest.strip().lower():
        raise ValueError(
            "certified archive digest does not match the approved evidence value"
        )
    return {
        "kind": "git-archive",
        "archive_sha256": observed,
        "archive_name": archive.name,
    }


def build_expected(
    *,
    root: Path,
    certified_sha: str,
    provenance: str,
    archive: Path | None,
    archive_sha256: str | None,
    allow_test_sha: bool,
) -> dict:
    if not allow_test_sha and certified_sha != CERTIFIED_SHA:
        raise PermissionError(
            f"certified SHA {certified_sha} is not the pinned {CERTIFIED_SHA}"
        )
    if provenance == "candidate":
        raise PermissionError(
            "a candidate-generated inventory cannot establish its own authenticity"
        )
    if provenance not in TRUSTED_PROVENANCE:
        raise ValueError(f"unsupported provenance: {provenance}")
    scanned = scan_tree(Path(root))
    if provenance == "git-checkout":
        record = _git_checkout_provenance(Path(root), certified_sha)
        observed_paths = {entry["path"] for entry in scanned["source_files"]}
        observed_paths.update(entry["path"] for entry in scanned["source_symlinks"])
        expected_paths = record.pop("tracked_paths")
        if observed_paths != expected_paths:
            missing = sorted(expected_paths - observed_paths)
            extra = sorted(observed_paths - expected_paths)
            raise ValueError(
                "checkout content does not match its own HEAD: "
                f"missing={missing[:5]} unexpected={extra[:5]}"
            )
    else:
        if archive is None or not archive_sha256:
            raise ValueError("--archive and --archive-sha256 are required for git-archive")
        record = _archive_provenance(Path(archive), archive_sha256)
    return {
        "schema": SCHEMA,
        "kind": "expected",
        "certified_sha": certified_sha,
        "generated_at_utc": _utc(),
        "policy": _policy(),
        "provenance": record,
        "source_authenticity": "trusted",
        **scanned,
    }


def build_actual(
    *, root: Path, host: str, droplet_id: str, privilege: str, observed_at: str | None
) -> dict:
    scanned = scan_tree(Path(root))
    return {
        "schema": SCHEMA,
        "kind": "actual",
        "certified_sha": CERTIFIED_SHA,
        "observed_at_utc": observed_at or _utc(),
        "policy": _policy(),
        "provenance": {
            "kind": "host-observation",
            "host": host,
            "droplet_id": droplet_id,
            "privilege": privilege,
        },
        # A host observation never authenticates itself, regardless of who ran it.
        "source_authenticity": "UNPROVEN",
        **scanned,
    }


def _require_trusted_expected(expected: dict, *, allow_test_sha: bool = False) -> None:
    if expected.get("kind") != "expected":
        raise PermissionError("expected inventory must have kind=expected")
    if expected.get("source_authenticity") != "trusted":
        raise PermissionError("expected inventory is not trusted evidence")
    provenance = expected.get("provenance") or {}
    if provenance.get("kind") not in TRUSTED_PROVENANCE:
        raise PermissionError(
            "expected inventory provenance must be a trusted checkout or archive"
        )
    if not allow_test_sha and expected.get("certified_sha") != CERTIFIED_SHA:
        raise PermissionError(
            f"expected inventory certifies {expected.get('certified_sha')}, "
            f"not the pinned {CERTIFIED_SHA}"
        )
    if expected.get("certified_sha") and len(str(expected["certified_sha"])) != 40:
        raise PermissionError("expected inventory certified SHA is not a commit id")
    if (expected.get("policy") or {}).get("id") != POLICY_ID:
        raise PermissionError("expected inventory policy is not the approved policy")
    files = expected.get("source_files") or []
    if "manifest_digest" not in expected:
        raise PermissionError("expected inventory has no manifest digest")
    if _manifest_digest(files) != expected.get("manifest_digest"):
        raise PermissionError("expected inventory digest does not match its contents")


def compare_inventories(
    expected: dict, actual: dict, *, allow_test_sha: bool = False
) -> dict:
    _require_trusted_expected(expected, allow_test_sha=allow_test_sha)
    if actual.get("kind") != "actual":
        raise PermissionError("actual inventory must have kind=actual")
    if actual.get("source_authenticity") != "UNPROVEN":
        raise PermissionError("a host observation may not claim source authenticity")

    expected_files = {entry["path"]: entry for entry in expected.get("source_files") or []}
    actual_files = {entry["path"]: entry for entry in actual.get("source_files") or []}
    expected_links = {
        entry["path"]: entry.get("target") for entry in expected.get("source_symlinks") or []
    }
    actual_links = {
        entry["path"]: entry.get("target") for entry in actual.get("source_symlinks") or []
    }

    missing = sorted(set(expected_files) - set(actual_files))
    added = sorted(set(actual_files) - set(expected_files))
    changed = sorted(
        path
        for path in set(expected_files) & set(actual_files)
        if expected_files[path]["sha256"] != actual_files[path]["sha256"]
    )
    missing_links = sorted(set(expected_links) - set(actual_links))
    added_links = sorted(
        path for path in set(actual_links) - set(expected_links) if path not in added
    )
    changed_links = sorted(
        path
        for path in set(expected_links) & set(actual_links)
        if expected_links[path] != actual_links[path]
    )

    problems: list[str] = []
    if missing:
        problems.append(f"{len(missing)} certified file(s) missing")
    if added:
        problems.append(f"{len(added)} unexpected file(s) added")
    if changed:
        problems.append(f"{len(changed)} certified file(s) changed")
    if missing_links:
        problems.append(f"{len(missing_links)} certified symlink(s) missing")
    if added_links:
        problems.append(f"{len(added_links)} unexpected symlink(s) added")
    if changed_links:
        problems.append(f"{len(changed_links)} symlink target(s) changed")
    if expected["counts"]["source_files"] != actual["counts"]["source_files"]:
        problems.append(
            "source file count differs: "
            f"expected={expected['counts']['source_files']} "
            f"actual={actual['counts']['source_files']}"
        )

    return {
        "verdict": "PASS" if not problems else "FAIL",
        "certified_sha": expected["certified_sha"],
        "expected_manifest_digest": expected["manifest_digest"],
        "expected_file_count": expected["counts"]["source_files"],
        "actual_file_count": actual["counts"]["source_files"],
        "expected_symlink_count": expected["counts"]["source_symlinks"],
        "actual_symlink_count": actual["counts"]["source_symlinks"],
        "missing": missing,
        "added_unexpected": added,
        "added_permitted_runtime": [
            entry["path"] for entry in actual.get("runtime_files") or []
        ],
        "changed": changed,
        "missing_symlinks": missing_links,
        "added_symlinks": added_links,
        "changed_symlinks": changed_links,
        "problems": problems,
        "expected_provenance": expected.get("provenance"),
        "actual_provenance": actual.get("provenance"),
    }


def _emit(payload: dict, out: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M07 L-02 release provenance")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="expected inventory from trusted evidence")
    p_build.add_argument("--root", required=True)
    p_build.add_argument("--out", required=True)
    p_build.add_argument("--certified-sha", default=CERTIFIED_SHA)
    p_build.add_argument(
        "--provenance",
        choices=("git-checkout", "git-archive", "candidate"),
        default="git-checkout",
    )
    p_build.add_argument("--archive")
    p_build.add_argument("--archive-sha256")
    p_build.add_argument("--allow-test-sha", action="store_true")

    p_inv = sub.add_parser("inventory", help="actual inventory of a release tree")
    p_inv.add_argument("--root", required=True)
    p_inv.add_argument("--out")
    p_inv.add_argument("--host", default="UNKNOWN")
    p_inv.add_argument("--droplet-id", default="UNKNOWN")
    p_inv.add_argument("--privilege", default="unknown")
    p_inv.add_argument("--observed-at")

    p_cmp = sub.add_parser("compare", help="prove the actual tree equals the expected tree")
    p_cmp.add_argument("--expected", required=True)
    p_cmp.add_argument("--actual", required=True)
    p_cmp.add_argument("--out")
    p_cmp.add_argument("--allow-test-sha", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            payload = build_expected(
                root=Path(args.root),
                certified_sha=args.certified_sha,
                provenance=args.provenance,
                archive=Path(args.archive) if args.archive else None,
                archive_sha256=args.archive_sha256,
                allow_test_sha=args.allow_test_sha,
            )
            _emit(payload, Path(args.out))
            return EXIT_PASS
        if args.command == "inventory":
            payload = build_actual(
                root=Path(args.root),
                host=args.host,
                droplet_id=args.droplet_id,
                privilege=args.privilege,
                observed_at=args.observed_at,
            )
            _emit(payload, Path(args.out) if args.out else None)
            return EXIT_PASS
        expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
        actual = json.loads(Path(args.actual).read_text(encoding="utf-8"))
        result = compare_inventories(
            expected, actual, allow_test_sha=args.allow_test_sha
        )
        _emit(result, Path(args.out) if args.out else None)
        return EXIT_PASS if result["verdict"] == "PASS" else EXIT_FAIL
    except PermissionError as exc:
        print(json.dumps({"verdict": "REFUSED", "reason": str(exc)}, indent=2))
        return EXIT_REFUSED
    except (ValueError, OSError) as exc:
        print(json.dumps({"verdict": "INSUFFICIENT", "reason": str(exc)}, indent=2))
        return EXIT_INSUFFICIENT


if __name__ == "__main__":
    raise SystemExit(main())



