"""R1 bundle engine: inventory, export, verify, stage, promote, reconcile.

Fixes R0 defects:
  * no per-dataset file cap (R0 truncated a >2000-file dataset to 50 entries),
  * the archive carries the manifest and its digest, so a transferred archive
    alone can be verified,
  * staging verifies EVERY declared file before copying,
  * promotion refuses unless canonical recovery has actually been recorded,
  * a file copy is never reported as database recovery.

Pure stdlib; Windows and Linux. Certified modules are imported only by
opip_recovery.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import opip_archive as archive_mod
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import opip_archive as archive_mod

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
BUNDLE_SCHEMA = 2
MANIFEST_NAME = "BUNDLE-MANIFEST.json"
MANIFEST_DIGEST_NAME = "MANIFEST-SHA256"
PAYLOAD_HASH_LIST = "payload-sha256.txt"
ARCHIVE_DIGEST_NAME = "bundle.sha256"
EPHEMERAL_SUFFIXES = (".lock", ".sock", ".pid", ".tmp", "-wal", "-shm")
EPHEMERAL_NAME_PARTS = ("__pycache__", ".DS_Store")
PORTABLE = {
    "REQUIRED_CANONICAL",
    "REQUIRED_RUNTIME_STATE",
    "REQUIRED_HISTORICAL_EVIDENCE",
    "DERIVED_REBUILDABLE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def is_ephemeral(relative: str) -> bool:
    name = relative.replace("\\", "/").split("/")[-1]
    if name.endswith(EPHEMERAL_SUFFIXES):
        return True
    return any(part in relative for part in EPHEMERAL_NAME_PARTS)


def load_spec(path: Path) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("certified_sha") != CERTIFIED_SHA:
        raise SystemExit(f"spec is pinned to {spec.get('certified_sha')}, not the certified SHA")
    return spec


def portable_datasets(spec: dict) -> list[dict]:
    rows = [d for d in spec["datasets"] if d["classification"] in PORTABLE and d.get("dest_rel")]
    return sorted(rows, key=lambda d: int(d["ordering"]))


def dataset_files(source_root: Path, dataset: dict) -> list[Path]:
    """Every file for a dataset. Uncapped, and never following escaping symlinks."""
    root = Path(source_root)
    target = root / dataset["source_rel"]
    if dataset["kind"] == "dir":
        if not target.is_dir():
            return []
        resolved_root = root.resolve()
        files: list[Path] = []
        for current, _dirs, names in os.walk(target, followlinks=False):
            for name in names:
                candidate = Path(current) / name
                resolved = candidate.resolve()
                if resolved_root not in resolved.parents and resolved != resolved_root:
                    raise SystemExit(f"source file escapes the authorised root: {candidate}")
                files.append(candidate)
        return sorted(files)
    if target.is_file():
        return [target]
    return []


def rel_inside(source_root: Path, dataset: dict, path: Path) -> str:
    if dataset["kind"] == "dir":
        return path.relative_to(Path(source_root) / dataset["source_rel"]).as_posix()
    return path.name


def dataset_digest(manifest: dict) -> str:
    """Digest over the security-relevant manifest content (tamper detection)."""
    material = {
        "certified_sha": manifest["certified_sha"],
        "source": manifest["source"],
        "canonical": manifest.get("canonical"),
        "datasets": [
            {"id": d["id"], "dest_rel": d["dest_rel"], "status": d["status"], "files": d["files"]}
            for d in manifest["datasets"]
        ],
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def count_lines(path: Path) -> int | None:
    if path.suffix != ".jsonl":
        return None
    total = 0
    with path.open("rb") as handle:
        for _ in handle:
            total += 1
    return total


@dataclass
class ExportResult:
    bundle_dir: Path
    manifest: dict
    manifest_path: Path
    archive: Path | None
    archive_sha256: str | None
    chunks: list[dict] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
def _export_canonical(canonical_dir, payload: Path, dataset: dict, allow_synthetic: bool):
    if not canonical_dir:
        return None
    canonical_dir = Path(canonical_dir)
    manifests = sorted(canonical_dir.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not manifests:
        return None
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    for key in ("backup_file", "sha256", "source_release_sha", "history_epoch", "next_local_sequence", "event_count"):
        if key not in manifest:
            raise SystemExit(f"canonical manifest missing {key}")
    if manifest["source_release_sha"] != CERTIFIED_SHA:
        raise SystemExit("canonical generation is not pinned to the certified SHA")
    if manifest.get("synthetic") and not allow_synthetic:
        raise SystemExit("canonical generation is synthetic; a certified publish_backup_generation is required")
    db = canonical_dir / manifest["backup_file"]
    if not db.is_file():
        raise SystemExit(f"canonical backup database missing: {db}")
    if sha256_file(db) != str(manifest["sha256"]).lower():
        raise SystemExit("canonical backup hash mismatch")
    base = dataset["dest_rel"].rstrip("/")
    if base.endswith(".sqlite3"):
        base = base.rsplit("/", 1)[0]
    target = payload / base / "backups"
    target.mkdir(parents=True, exist_ok=True)
    files = []
    for src in (db, manifests[0]):
        dest = target / src.name
        shutil.copy2(src, dest)
        files.append({"rel": f"{base}/backups/{src.name}", "size": dest.stat().st_size,
                      "sha256": sha256_file(dest), "action": "COPIED"})
    return {
        "generation_manifest": manifests[0].name,
        "backup_file": db.name,
        "sha256": manifest["sha256"],
        "history_epoch": int(manifest["history_epoch"]),
        "next_local_sequence": int(manifest["next_local_sequence"]),
        "event_count": int(manifest["event_count"]),
        "source_release_sha": manifest["source_release_sha"],
        "synthetic": bool(manifest.get("synthetic")),
        "files": files,
        "size_bytes": sum(f["size"] for f in files),
    }
def load_identity_evidence(
    path: str | Path | None,
    *,
    expected: dict,
    max_age_hours: float = 24.0,
) -> dict:
    """Load and verify independently produced host-identity evidence.

    The evidence document must name the expected droplet and IP, an explicit
    source generation, and a fresh capture time. Its digest is returned so it can
    be bound into the export manifest. A bare ``--identity-proof`` string is not
    evidence and is never accepted on its own.
    """
    if not path:
        raise SystemExit("refusing export: --identity-evidence is required for host-evidence")
    evidence_path = Path(path)
    if not evidence_path.is_file():
        raise SystemExit(f"refusing export: identity evidence not found: {evidence_path}")
    raw = evidence_path.read_bytes()
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"refusing export: identity evidence is not valid JSON: {exc}") from exc
    required = ("droplet_id", "ip", "captured_at_utc", "source_generation")
    missing = [key for key in required if not str(document.get(key) or "").strip()]
    if missing:
        raise SystemExit(f"refusing export: identity evidence is missing {', '.join(missing)}")
    if str(document["droplet_id"]) != str(expected.get("droplet_id")):
        raise SystemExit("refusing export: identity evidence droplet does not match the source")
    if str(document["ip"]) != str(expected.get("ip")):
        raise SystemExit("refusing export: identity evidence IP does not match the source")
    try:
        captured = datetime.fromisoformat(str(document["captured_at_utc"]).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SystemExit(f"refusing export: identity evidence has no usable capture time: {exc}") from exc
    age_hours = (datetime.now(timezone.utc) - captured).total_seconds() / 3600.0
    if age_hours < 0:
        raise SystemExit("refusing export: identity evidence is dated in the future")
    if age_hours > max_age_hours:
        raise SystemExit(
            f"refusing export: identity evidence is stale ({age_hours:.1f}h > {max_age_hours}h)"
        )
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "droplet_id": str(document["droplet_id"]),
        "ip": str(document["ip"]),
        "captured_at_utc": str(document["captured_at_utc"]),
        "source_generation": str(document["source_generation"]),
    }


def export(
    *,
    source_root: Path,
    spec: dict,
    out_dir: Path,
    bundle_name: str,
    canonical_dir: Path | None,
    cutoff_utc: str,
    source_identity: dict,
    identity_evidence: dict | None = None,
    chunk_size_mb: int = 0,
    allow_synthetic_canonical: bool = False,
    force: bool = False,
) -> ExportResult:
    source_root = Path(source_root)
    archive_mod.assert_source_tree(source_root)
    proof = str(source_identity.get("identity_proof", "")).strip().lower()
    if proof == "host-evidence":
        if not identity_evidence:
            raise SystemExit(
                "refusing export: host-evidence requires a verified --identity-evidence document"
            )
        if str(identity_evidence.get("droplet_id")) != str(source_identity.get("droplet_id")) \
                or str(identity_evidence.get("ip")) != str(source_identity.get("ip")):
            raise SystemExit(
                "refusing export: verified evidence does not match the declared source identity"
            )
    elif proof == "fixture" and allow_synthetic_canonical:
        pass  # rehearsal only; provenance verification still requires --allow-fixture
    else:
        raise SystemExit(
            "refusing export: source identity must come from trusted host evidence "
            f"(identity_proof={proof or 'unset'}); default CLI values are not provenance"
        )

    bundle_dir = Path(out_dir) / bundle_name
    payload = bundle_dir / "payload"
    payload.mkdir(parents=True, exist_ok=True)

    datasets: list[dict] = []
    exclusions: list[dict] = []
    deferred: list[str] = []
    canonical: dict | None = None

    for dataset in portable_datasets(spec):
        entry = {
            "id": dataset["id"], "classification": dataset["classification"],
            "ordering": int(dataset["ordering"]), "required": bool(dataset["required"]),
            "dest_rel": dataset["dest_rel"], "status": "UNMEASURED",
            "files": [], "file_count": 0, "records": None, "size_bytes": 0,
        }
        if dataset["kind"] == "sqlite_native_backup":
            canonical = _export_canonical(canonical_dir, payload, dataset, allow_synthetic_canonical)
            if canonical:
                entry.update(status="EXPORTED", files=canonical["files"],
                             file_count=len(canonical["files"]), records=canonical["event_count"],
                             size_bytes=canonical["size_bytes"])
            else:
                entry["status"] = "DEFERRED"
                deferred.append(dataset["id"])
            datasets.append(entry)
            continue

        paths = dataset_files(source_root, dataset)
        if not paths:
            entry["status"] = "ABSENT"
            if dataset["required"]:
                deferred.append(dataset["id"])
            datasets.append(entry)
            continue

        files: list[dict] = []
        records = 0
        for path in paths:
            inner = rel_inside(source_root, dataset, path)
            if is_ephemeral(inner):
                exclusions.append({"dataset": dataset["id"], "path": inner, "reason": "ephemeral"})
                continue
            rel = f"{dataset['dest_rel'].rstrip('/')}/{inner}" if dataset["kind"] == "dir" else dataset["dest_rel"]
            digest = sha256_file(path)
            target = payload / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            action = "COPIED"
            if target.exists() and not force:
                if sha256_file(target) == digest:
                    action = "SKIPPED_IDENTICAL"
                else:
                    raise SystemExit(f"payload file differs and force is false: {target}")
            if action == "COPIED":
                shutil.copy2(path, target)
            files.append({"rel": rel, "size": path.stat().st_size, "sha256": digest, "action": action})
            lines = count_lines(path)
            if lines:
                records += lines
        entry.update(status="EXPORTED", files=files, file_count=len(files),
                     records=records or None, size_bytes=sum(i["size"] for i in files))
        datasets.append(entry)

    manifest = {
        "bundle_schema": BUNDLE_SCHEMA, "mission": "MISSION-07-R1",
        "certified_sha": CERTIFIED_SHA, "bundle_name": bundle_name,
        "exported_at_utc": utc_now(), "consistency_cutoff_utc": cutoff_utc,
        "source": source_identity, "canonical": canonical,
        "source_identity_evidence": identity_evidence,
        "identity_evidence_sha256": (identity_evidence or {}).get("sha256"),
        "datasets": sorted(datasets, key=lambda d: int(d["ordering"])),
        "exclusions": exclusions,
        "totals": {"datasets": len(datasets), "files": sum(d["file_count"] for d in datasets),
                   "bytes": sum(d["size_bytes"] for d in datasets)},
        "deferred_required": sorted(set(deferred)),
    }
    manifest["dataset_digest"] = dataset_digest(manifest)

    manifest_path = bundle_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (bundle_dir / MANIFEST_DIGEST_NAME).write_text(sha256_file(manifest_path) + "\n", encoding="utf-8")
    lines = [f"{i['sha256']}  {i['rel']}" for d in manifest["datasets"] for i in d["files"]]
    (bundle_dir / PAYLOAD_HASH_LIST).write_text("\n".join(lines) + "\n", encoding="utf-8")

    archive = _create_archive(bundle_dir, f"{bundle_name}.tar")
    archive_sha = sha256_file(archive)
    (bundle_dir / ARCHIVE_DIGEST_NAME).write_text(
        json.dumps({"archive": archive.name, "sha256": archive_sha, "bytes": archive.stat().st_size}) + "\n",
        encoding="utf-8",
    )
    chunks = _split(archive, bundle_dir / "chunks", chunk_size_mb) if chunk_size_mb > 0 else []
    return ExportResult(bundle_dir=bundle_dir, manifest=manifest, manifest_path=manifest_path,
                        archive=archive, archive_sha256=archive_sha, chunks=chunks,
                        deferred=sorted(set(deferred)))
def _create_archive(bundle_dir: Path, name: str) -> Path:
    """Archive includes the manifest and its digest, so it is self-verifiable."""
    archive = bundle_dir / name
    members = [MANIFEST_NAME, MANIFEST_DIGEST_NAME, PAYLOAD_HASH_LIST, "payload"]
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w") as tar:
        for member in members:
            source = bundle_dir / member
            if source.exists():
                tar.add(source, arcname=member)
    return archive


def _split(path: Path, chunk_dir: Path, chunk_mb: int) -> list[dict]:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    size = int(chunk_mb) * 1024 * 1024
    chunks: list[dict] = []
    with path.open("rb") as handle:
        index = 0
        while True:
            block = handle.read(size)
            if not block:
                break
            part = chunk_dir / f"{path.name}.part{index:05d}"
            part.write_bytes(block)
            chunks.append({"name": part.name, "size": len(block), "sha256": sha256_file(part)})
            index += 1
    return chunks


def reassemble(chunk_dir: Path, chunks: list[dict], out_path: Path) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as out:
        for chunk in chunks:
            part = chunk_dir / chunk["name"]
            if not part.is_file():
                raise SystemExit(f"missing chunk: {part.name}")
            if sha256_file(part) != str(chunk["sha256"]).lower():
                raise SystemExit(f"chunk hash mismatch: {part.name}")
            out.write(part.read_bytes())
    return sha256_file(out_path)


def _check(report: dict, ident: str, ok: bool, detail: str) -> None:
    report["checks"].append({"id": ident, "status": "PASS" if ok else "FAIL", "detail": detail})
    if not ok:
        report["failed"] = True
def verify_bundle(
    *,
    bundle_dir: Path | None = None,
    archive: Path | None = None,
    workdir: Path | None = None,
    expected_droplet_id: str = "589794133",
    expected_ip: str = "161.35.106.207",
) -> dict:
    """Verify a bundle directory or a transferred archive. Fails closed."""
    report: dict = {"verified_at_utc": utc_now(), "checks": [], "failed": False,
                    "source": "archive" if archive else "directory"}
    if archive:
        archive = Path(archive)
        try:
            digest = archive_mod.verify_external_digest(archive)
            _check(report, "ARCHIVE_DIGEST", True, digest["sha256"][:12])
        except archive_mod.ArchiveError as exc:
            _check(report, "ARCHIVE_DIGEST", False, str(exc))
            report["verdict"] = "FAIL"
            report["extraction_skipped"] = "integrity evidence failed before extraction"
            return report
        workdir = Path(workdir) if workdir else archive.parent / f"verify-{digest['sha256'][:8]}"
        try:
            extraction = archive_mod.safe_extract(archive, workdir, expected_sha=digest["sha256"])
            _check(report, "ARCHIVE_MEMBERS_SAFE", True, f"members={extraction['extracted']}")
        except archive_mod.ArchiveError as exc:
            _check(report, "ARCHIVE_MEMBERS_SAFE", False, str(exc))
            report["verdict"] = "FAIL"
            return report
        bundle_dir = workdir
        report["extracted_to"] = str(workdir)

    bundle_dir = Path(bundle_dir)  # type: ignore[arg-type]
    manifest_path = bundle_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        _check(report, "MANIFEST_PRESENT", False, f"missing {manifest_path}")
        report["verdict"] = "FAIL"
        return report
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _check(report, "MANIFEST_PRESENT", True, manifest_path.name)
    _check(report, "BUNDLE_SCHEMA", manifest.get("bundle_schema") == BUNDLE_SCHEMA, str(manifest.get("bundle_schema")))
    _check(report, "CERTIFIED_SHA", manifest.get("certified_sha") == CERTIFIED_SHA, str(manifest.get("certified_sha")))

    digest_file = bundle_dir / MANIFEST_DIGEST_NAME
    manifest_sha = sha256_file(manifest_path)
    declared_manifest_sha = digest_file.read_text(encoding="utf-8").strip() if digest_file.is_file() else ""
    _check(report, "MANIFEST_SELF_HASH", manifest_sha == declared_manifest_sha, manifest_sha[:12])
    recomputed = dataset_digest(manifest)
    _check(report, "MANIFEST_CONTENT_DIGEST", recomputed == manifest.get("dataset_digest"),
           "content digest matches" if recomputed == manifest.get("dataset_digest") else "manifest content was altered")

    source = manifest.get("source") or {}
    identity_ok = str(source.get("droplet_id")) == expected_droplet_id and str(source.get("ip")) == expected_ip
    _check(report, "SOURCE_IDENTITY", identity_ok,
           f"{source.get('droplet_id')}/{source.get('ip')} proof={source.get('identity_proof')}")
    ids = [d["id"] for d in manifest["datasets"]]
    dests = [d["dest_rel"] for d in manifest["datasets"] if d.get("dest_rel")]
    rels = [f["rel"] for d in manifest["datasets"] for f in d["files"]]
    _check(report, "NO_DUPLICATE_IDS", len(ids) == len(set(ids)), f"{len(ids)} datasets")
    _check(report, "NO_DUPLICATE_DESTS", len(dests) == len(set(dests)), f"{len(dests)} destinations")
    _check(report, "NO_DUPLICATE_FILES", len(rels) == len(set(rels)), f"{len(rels)} files")
    unsafe_rels = [
        rel for rel in rels
        if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":")
        or ".." in rel.replace("\\", "/").split("/") or "\x00" in rel
    ]
    _check(report, "MANIFEST_PATHS_SAFE", not unsafe_rels,
           "all declared paths are relative and contained" if not unsafe_rels else "; ".join(unsafe_rels[:5]))
    _check(report, "FILE_LIST_COMPLETE", len(rels) == int(manifest.get("totals", {}).get("files", -1)),
           f"listed {len(rels)} vs declared {manifest.get('totals', {}).get('files')}")

    payload = bundle_dir / "payload"
    missing: list[str] = []
    corrupt: list[str] = []
    verified = 0
    for dataset in manifest["datasets"]:
        if dataset["status"] != "EXPORTED":
            if dataset["required"]:
                _check(report, f"DATASET_REQUIRED_{dataset['id']}", False, f"status={dataset['status']}")
            continue
        for item in dataset["files"]:
            full = payload / item["rel"]
            if not full.is_file():
                missing.append(item["rel"])
            elif full.stat().st_size != int(item["size"]):
                corrupt.append(f"{item['rel']}:size")
            elif sha256_file(full) != str(item["sha256"]).lower():
                corrupt.append(f"{item['rel']}:sha")
            else:
                verified += 1
    _check(report, "PAYLOAD_FILES_PRESENT", not missing, f"verified={verified}" if not missing else "; ".join(missing[:5]))
    _check(report, "PAYLOAD_INTEGRITY", not corrupt, "all hashes match" if not corrupt else "; ".join(corrupt[:5]))

    sidecar = bundle_dir / PAYLOAD_HASH_LIST
    lines = [l for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()] if sidecar.is_file() else []
    sidecar_ok = len(lines) == len(rels)
    for line in lines:
        if not sidecar_ok:
            break
        digest, _, rel = line.partition("  ")
        full = payload / rel
        if not full.is_file() or sha256_file(full) != digest.lower():
            sidecar_ok = False
    _check(report, "HASH_SIDECAR_COMPLETE", sidecar_ok, f"lines={len(lines)} files={len(rels)}")

    if manifest.get("canonical"):
        backups = list(payload.glob("**/canonical/backups/*.manifest.json"))
        _check(report, "CANONICAL_GENERATION_PRESENT", len(backups) == 1, f"found {len(backups)}")
        if len(backups) == 1:
            inner = json.loads(backups[0].read_text(encoding="utf-8"))
            _check(report, "CANONICAL_HASH_MATCH", inner.get("sha256") == manifest["canonical"]["sha256"], "generation hash")
            _check(report, "CANONICAL_RELEASE_SHA", inner.get("source_release_sha") == CERTIFIED_SHA, "pinned SHA")

    report["manifest"] = manifest
    report["verdict"] = "FAIL" if report["failed"] else "PASS"
    return report
RECOVERY_RECEIPT_SCHEMA = 1


def _db_facts(db: Path) -> dict | None:
    import sqlite3

    if not Path(db).is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1").fetchone()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return {"history_epoch": int(row[0]), "next_local_sequence": int(row[1]),
            "event_count": int(events), "integrity_check": integrity,
            "foreign_key_violations": len(fk)}


def validate_recovery_receipt(
    receipt_path: Path,
    *,
    destination_db: Path,
    staged_generation: Path | None = None,
    bundle_manifest_sha256: str | None = None,
    staging_receipt: dict | None = None,
) -> dict:
    """A receipt is evidence only if it validates against CURRENT destination state."""
    checks: list[dict] = []

    def add(ident: str, ok: bool, detail: str) -> None:
        checks.append({"id": ident, "status": "PASS" if ok else "FAIL", "detail": detail})

    if staging_receipt is not None:
        # A staging receipt is a FILE-COPY record. It must never be read as
        # recovery evidence: it always says canonical_recovered=False, and that
        # claim is preserved here so a forged "true" cannot appear later.
        add("STAGING_RECEIPT_IS_COPY_ONLY",
            staging_receipt.get("canonical_recovered") is not True,
            f"staging canonical_recovered={staging_receipt.get('canonical_recovered')} (never evidence)")

    receipt_path = Path(receipt_path)
    if not receipt_path.is_file():
        add("RECEIPT_PRESENT", False, f"missing {receipt_path}")
        return {"verdict": "FAIL", "checks": checks}
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        add("RECEIPT_PRESENT", False, f"unreadable: {exc}")
        return {"verdict": "FAIL", "checks": checks}
    add("RECEIPT_PRESENT", True, receipt_path.name)
    add("RECEIPT_SCHEMA", receipt.get("receipt_schema") == RECOVERY_RECEIPT_SCHEMA, str(receipt.get("receipt_schema")))
    add("RECEIPT_COMPLETED", receipt.get("completed") is True, f"completed={receipt.get('completed')}")
    add("RECEIPT_SOURCE_SHA", receipt.get("source_release_sha") == CERTIFIED_SHA, str(receipt.get("source_release_sha")))

    expected_dest = Path(destination_db).resolve()
    recorded_dest = receipt.get("destination_db")
    dest_ok = bool(recorded_dest) and Path(str(recorded_dest)).resolve() == expected_dest
    add("RECEIPT_DESTINATION", dest_ok, f"recorded={recorded_dest} expected={expected_dest}")

    if bundle_manifest_sha256:
        add("RECEIPT_BUNDLE_IDENTITY", receipt.get("bundle_manifest_sha256") == bundle_manifest_sha256,
            f"recorded={receipt.get('bundle_manifest_sha256')} staged={bundle_manifest_sha256}")
    if staged_generation is not None:
        add("RECEIPT_GENERATION_SHA",
            str(receipt.get("generation_sha256", "")).lower() == sha256_file(staged_generation),
            f"recorded={receipt.get('generation_sha256')} staged={sha256_file(staged_generation)}")

    recorded = receipt.get("restored") or {}
    add("RECEIPT_INTEGRITY", receipt.get("integrity_check") == "ok" and recorded.get("integrity_check", "ok") == "ok",
        f"integrity={receipt.get('integrity_check')}")
    add("RECEIPT_FOREIGN_KEYS", int(receipt.get("foreign_key_violations", -1)) == 0,
        f"violations={receipt.get('foreign_key_violations')}")

    live = _db_facts(expected_dest)
    if live is None:
        add("DESTINATION_READABLE", False, f"cannot read {expected_dest}")
    else:
        add("DESTINATION_READABLE", True, str(expected_dest))
        add("DESTINATION_INTEGRITY", live["integrity_check"] == "ok", live["integrity_check"])
        add("DESTINATION_FOREIGN_KEYS", live["foreign_key_violations"] == 0, f"violations={live['foreign_key_violations']}")
        add("DESTINATION_EPOCH_MATCHES_RECEIPT",
            int(recorded.get("history_epoch", -1)) == live["history_epoch"],
            f"receipt={recorded.get('history_epoch')} live={live['history_epoch']}")
        add("DESTINATION_SEQUENCE_MATCHES_RECEIPT",
            int(recorded.get("next_local_sequence", -1)) == live["next_local_sequence"],
            f"receipt={recorded.get('next_local_sequence')} live={live['next_local_sequence']}")
        add("DESTINATION_EVENT_COUNT",
            int(recorded.get("event_count", -1)) == live["event_count"],
            f"receipt={recorded.get('event_count')} live={live['event_count']}")

    failed = [c for c in checks if c["status"] == "FAIL"]
    return {"verdict": "PASS" if not failed else "FAIL", "checks": checks, "receipt": receipt,
            "live": live, "reason": (failed[0]["id"] + ": " + failed[0]["detail"]) if failed else "receipt validated"}


def _safe_destination(root: Path, relative: str) -> Path:
    """Containment + symlink-ancestor guard for staged/promoted paths (D05)."""
    root = Path(root)
    if relative.startswith(("/", "\\")) or (len(relative) > 1 and relative[1] == ":"):
        raise SystemExit(f"refusing path: absolute destination not allowed: {relative}")
    parts = relative.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SystemExit(f"refusing path: unsafe component in {relative}")
    candidate = root.joinpath(*parts)
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise SystemExit(f"refusing path: destination escapes the target root: {relative}")
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise SystemExit(f"refusing path: symlinked ancestor in destination: {relative}")
    return candidate


def stage_bundle(bundle_dir: Path, staging: Path, *, verify_first: bool = True) -> dict:
    if verify_first:
        report = verify_bundle(bundle_dir=Path(bundle_dir))
        if report["verdict"] != "PASS":
            raise SystemExit("refusing to stage: bundle verification failed")
    manifest = json.loads((Path(bundle_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    payload = Path(bundle_dir) / "payload"
    copied = skipped = 0
    hashes: dict[str, str] = {}
    for dataset in manifest["datasets"]:
        for item in dataset["files"]:
            source = payload / item["rel"]
            dest = _safe_destination(staging, item["rel"])
            if dest.exists():
                if dest.is_symlink():
                    raise SystemExit(f"refusing to stage onto a symlink: {dest}")
                if sha256_file(dest) == str(item["sha256"]).lower():
                    hashes[item["rel"]] = str(item["sha256"]).lower()
                    skipped += 1
                    continue
                raise SystemExit(f"staged file differs; refusing to overwrite: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            if sha256_file(dest) != str(item["sha256"]).lower():
                raise SystemExit(f"staged copy hash mismatch: {dest}")
            hashes[item["rel"]] = str(item["sha256"]).lower()
            copied += 1
    receipt = {
        "staged_at_utc": utc_now(),
        "bundle": str(bundle_dir),
        "bundle_manifest_sha256": sha256_file(Path(bundle_dir) / MANIFEST_NAME),
        "staging": str(staging),
        "copied": copied,
        "skipped_identical": skipped,
        "files": hashes,
        "canonical_staged": bool(manifest.get("canonical")),
        "canonical_recovered": False,
        "note": "A staged file copy is NOT database recovery; run canonical restore explicitly.",
    }
    (staging / "staging-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def promote(
    staging: Path,
    target: Path,
    *,
    recovery_receipt: Path | None = None,
    canonical_recovered: bool = False,
    authorize: bool = False,
    force: bool = False,
) -> dict:
    """Promote staged files. A caller flag is never sufficient evidence of recovery."""
    staging, target = Path(staging), Path(target)
    if not authorize:
        raise SystemExit("refusing promotion: -Authorize is required for an operation that modifies live state")
    staging_receipt = json.loads((staging / "staging-receipt.json").read_text(encoding="utf-8"))
    live_canonical = sorted(staging.glob("data/opip/canonical/backups/*.sqlite3"))
    gate = None
    if live_canonical:
        if not recovery_receipt:
            raise SystemExit(
                "refusing promotion: a persisted canonical recovery receipt is required "
                "(a caller-supplied flag is not evidence)"
            )
        gate = validate_recovery_receipt(
            Path(recovery_receipt),
            destination_db=target / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3",
            staged_generation=live_canonical[0],
            bundle_manifest_sha256=staging_receipt.get("bundle_manifest_sha256"),
            staging_receipt=staging_receipt,
        )
        if gate["verdict"] != "PASS":
            raise SystemExit(f"refusing promotion: recovery receipt failed: {gate.get('reason') or 'receipt invalid'}")
    elif canonical_recovered:
        gate = {"verdict": "PASS", "reason": "no canonical generation staged; flag irrelevant",
                "checks": [{"id": "NO_CANONICAL_STAGED", "status": "PASS", "detail": "nothing to recover"}]}
    hashes = staging_receipt.get("files")
    if not isinstance(hashes, dict) or not hashes:
        raise SystemExit("refusing promotion: staging receipt has no file index; re-stage the bundle")
    plan: list[dict] = []
    preflight_problems: list[str] = []
    for path in sorted(p for p in staging.rglob("*") if p.is_file()):
        rel = path.relative_to(staging).as_posix()
        if rel == "staging-receipt.json":
            continue
        expected = str(hashes.get(rel, "")).lower()
        if not expected:
            preflight_problems.append(f"untracked staged file: {rel}")
            continue
        if sha256_file(path) != expected:
            preflight_problems.append(f"staged file changed after staging: {rel}")
            continue
        try:
            dest = _safe_destination(target, rel)
        except SystemExit as exc:
            preflight_problems.append(str(exc))
            continue
        if dest.is_symlink():
            preflight_problems.append(f"destination is a symlink: {rel}")
            continue
        state = "copy"
        if dest.exists():
            state = "skip" if sha256_file(dest) == expected else "conflict"
        plan.append({"rel": rel, "source": path, "dest": dest, "state": state, "sha256": expected})
    conflicts = [item["rel"] for item in plan if item["state"] == "conflict"]
    if conflicts and not force:
        preflight_problems.extend(f"existing different file: {rel}" for rel in conflicts)
    receipt = {
        "promoted_at_utc": utc_now(),
        "staging": str(staging),
        "target": str(target),
        "promoted": 0,
        "skipped_identical": 0,
        "refused_existing_different": conflicts,
        "preflight_problems": preflight_problems,
        "canonical_recovered": bool(gate and gate.get("verdict") == "PASS" and live_canonical),
        "recovery_gate": gate,
        "bundle_staged_at": staging_receipt.get("staged_at_utc"),
        "bundle_manifest_sha256": staging_receipt.get("bundle_manifest_sha256"),
    }
    if preflight_problems:
        receipt["verdict"] = "REFUSED"
        receipt["writes_performed"] = 0
        return receipt
    promoted = skipped = 0
    for item in plan:
        if item["state"] == "skip":
            skipped += 1
            continue
        item["dest"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["source"], item["dest"])
        if sha256_file(item["dest"]) != item["sha256"]:
            raise SystemExit(f"promoted copy hash mismatch: {item['dest']}")
        promoted += 1
    receipt.update({"promoted": promoted, "skipped_identical": skipped, "verdict": "PASS",
                    "writes_performed": promoted})
    return receipt


def reconcile(bundle_dir: Path, target: Path) -> dict:
    manifest = json.loads((Path(bundle_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    target = Path(target)
    rows: list[dict] = []
    for dataset in manifest["datasets"]:
        if dataset["status"] != "EXPORTED":
            rows.append({"dataset": dataset["id"], "file": "", "status": "UNVERIFIED",
                         "reason": f"export status={dataset['status']}", "required": dataset["required"]})
            continue
        for item in dataset["files"]:
            full = target / item["rel"]
            if not full.is_file():
                status, reason = "MISSING", "file absent"
            elif full.stat().st_size != int(item["size"]):
                status, reason = "CORRUPT", "size mismatch"
            elif sha256_file(full) != str(item["sha256"]).lower():
                status, reason = "CORRUPT", "sha mismatch"
            else:
                status, reason = "MATCH", ""
            if status != "MATCH" and dataset["classification"] == "DERIVED_REBUILDABLE":
                status, reason = "ACCEPTED_TRANSFORMATION", "derived dataset may be regenerated"
            rows.append({"dataset": dataset["id"], "file": item["rel"], "status": status,
                         "reason": reason, "required": dataset["required"]})
    bad = [r for r in rows if r["required"] and r["status"] in {"MISSING", "CORRUPT"}]
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    return {"reconciled_at_utc": utc_now(), "rows": rows, "summary": summary,
            "verdict": "PASS" if not bad else "FAIL"}


def inventory(source_root: Path, spec: dict) -> dict:
    rows = []
    for dataset in portable_datasets(spec):
        paths = dataset_files(source_root, dataset)
        rows.append({
            "id": dataset["id"],
            "classification": dataset["classification"],
            "ordering": int(dataset["ordering"]),
            "required": bool(dataset["required"]),
            "source_rel": dataset["source_rel"],
            "kind": dataset["kind"],
            "status": "PRESENT" if paths else ("ABSENT" if dataset["required"] else "ABSENT_OPTIONAL"),
            "file_count": len(paths),
            "bytes": sum(p.stat().st_size for p in paths),
        })
    missing = [r["id"] for r in rows if r["status"] == "ABSENT"]
    return {"generated_at_utc": utc_now(), "source_root": str(source_root), "rows": rows,
            "missing_required": missing, "verdict": "FAIL" if missing else "PASS"}
def _emit(payload: dict, out: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MISSION-07-R1 bundle engine")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser("inventory")
    p_inv.add_argument("--source-root", required=True)
    p_inv.add_argument("--spec", required=True)
    p_inv.add_argument("--out")

    p_exp = sub.add_parser("export")
    p_exp.add_argument("--source-root", required=True)
    p_exp.add_argument("--spec", required=True)
    p_exp.add_argument("--out-dir", required=True)
    p_exp.add_argument("--bundle-name", required=True)
    p_exp.add_argument("--canonical-dir")
    p_exp.add_argument("--cutoff")
    p_exp.add_argument("--droplet-id", default="589794133")
    p_exp.add_argument("--ip", default="161.35.106.207")
    p_exp.add_argument("--identity-proof", default="UNPROVEN")
    p_exp.add_argument("--identity-evidence")
    p_exp.add_argument("--identity-max-age-hours", type=float, default=24.0)
    p_exp.add_argument("--chunk-mb", type=int, default=0)
    p_exp.add_argument("--allow-synthetic-canonical", action="store_true")
    p_exp.add_argument("--force", action="store_true")
    p_exp.add_argument("--authorize", action="store_true")
    p_exp.add_argument("--out")

    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--bundle")
    p_ver.add_argument("--archive")
    p_ver.add_argument("--workdir")
    p_ver.add_argument("--out")

    p_stage = sub.add_parser("stage")
    p_stage.add_argument("--bundle", required=True)
    p_stage.add_argument("--staging", required=True)
    p_stage.add_argument("--out")

    p_prom = sub.add_parser("promote")
    p_prom.add_argument("--staging", required=True)
    p_prom.add_argument("--target", required=True)
    p_prom.add_argument("--canonical-recovered", action="store_true")
    p_prom.add_argument("--recovery-receipt")
    p_prom.add_argument("--authorize", action="store_true")
    p_prom.add_argument("--force", action="store_true")
    p_prom.add_argument("--out")

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--bundle", required=True)
    p_rec.add_argument("--target", required=True)
    p_rec.add_argument("--out")

    args = parser.parse_args(argv)
    out = Path(args.out) if getattr(args, "out", None) else None

    if args.command == "inventory":
        result = inventory(Path(args.source_root), load_spec(Path(args.spec)))
        _emit(result, out)
        return 0 if result["verdict"] == "PASS" else 1

    if args.command == "export":
        if not args.authorize:
            print(json.dumps({"dry_run": True, "bundle_name": args.bundle_name,
                              "message": "re-run with --authorize to write the bundle"}, indent=2))
            return 77
        spec = load_spec(Path(args.spec))
        evidence = None
        if str(args.identity_proof).strip().lower() == "host-evidence":
            evidence = load_identity_evidence(
                args.identity_evidence,
                expected={"droplet_id": args.droplet_id, "ip": args.ip},
                max_age_hours=args.identity_max_age_hours,
            )
        result = export(
            source_root=Path(args.source_root), spec=spec, out_dir=Path(args.out_dir),
            bundle_name=args.bundle_name,
            canonical_dir=Path(args.canonical_dir) if args.canonical_dir else None,
            cutoff_utc=args.cutoff or utc_now(),
            source_identity={"droplet_id": args.droplet_id, "ip": args.ip,
                             "identity_proof": args.identity_proof},
            identity_evidence=evidence,
            chunk_size_mb=args.chunk_mb,
            allow_synthetic_canonical=args.allow_synthetic_canonical,
            force=args.force,
        )
        payload = {
            "bundle_dir": str(result.bundle_dir),
            "archive": str(result.archive) if result.archive else None,
            "archive_sha256": result.archive_sha256,
            "chunks": len(result.chunks),
            "totals": result.manifest["totals"],
            "deferred_required": result.deferred,
            "canonical": bool(result.manifest.get("canonical")),
        }
        _emit(payload, out)
        if result.deferred:
            return 2 if set(result.deferred) <= {"canonical_store"} else 1
        return 0

    if args.command == "verify":
        if not args.bundle and not args.archive:
            raise SystemExit("verify requires --bundle or --archive")
        result = verify_bundle(
            bundle_dir=Path(args.bundle) if args.bundle else None,
            archive=Path(args.archive) if args.archive else None,
            workdir=Path(args.workdir) if args.workdir else None,
        )
        slim = {k: v for k, v in result.items() if k != "manifest"}
        _emit(slim, out)
        return 0 if result["verdict"] == "PASS" else 1

    if args.command == "stage":
        result = stage_bundle(Path(args.bundle), Path(args.staging))
        _emit(result, out)
        return 0

    if args.command == "promote":
        try:
            result = promote(
                Path(args.staging), Path(args.target),
                recovery_receipt=Path(args.recovery_receipt) if args.recovery_receipt else None,
                canonical_recovered=args.canonical_recovered,
                authorize=args.authorize, force=args.force,
            )
        except SystemExit as exc:
            payload = {"verdict": "REFUSED", "reason": str(exc)}
            _emit(payload, out)
            return 3
        _emit(result, out)
        return 0 if result["verdict"] == "PASS" else 3

    if args.command == "reconcile":
        result = reconcile(Path(args.bundle), Path(args.target))
        _emit(result, out)
        return 0 if result["verdict"] == "PASS" else 1

    return 64


if __name__ == "__main__":
    raise SystemExit(main())







