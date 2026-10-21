"""Append-only core release provenance reconciliation.

Problem this fixes
------------------
The learning exporter publishes ``production_deployed_sha`` from
``/var/lib/ohm-deploy/last-good-sha``. A qualified core-only activation can
leave a healthy, correctly-labelled production core running while that
controller-owned receipt file is absent. The exporter then correctly fails
closed and the learning worker can never admit a generation.

This module reconciles that one missing fact from evidence that already exists
on the host. It never deploys, restarts, rebuilds, or reconfigures anything. It
is deliberately explicit about what it trusts:

* the running immutable core image ID and its revision label,
* the checksummed deployed release tree manifest,
* the LIVE_ACCEPTANCE and CORE-UPGRADE qualification receipts,
* the unchanged canonical writer image/container identity,
* canonical database continuity and integrity, read in a read-only transaction.

Only when every check passes does it append a reconciliation receipt and
atomically publish ``last-good-sha``. Existing receipts are hashed, never
rewritten. An existing conflicting ``last-good-sha`` is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

try:  # POSIX-only production lock; Windows test hosts inject a fake lock.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SCHEMA = "opip-core-release-provenance/v1"
DEPLOY_LOCK = Path("/var/lock/ohm-deploy.lock")
SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
EXPECTED_RUNTIME_ENV = {
    "OPIP_RELEASE_PROFILE": "EVIDENCE_SHADOW",
    "OPIP_FEATURE_BUS_MODE": "shadow",
    "OPIP_CANONICAL_WRITER_MODE": "shadow",
    "OPIP_TARGET_SPINE_MODE": "shadow",
    "OPIP_PAPER_V2_MODE": "off",
    "OPIP_COMMITTEE_MODE": "off",
}
FUNDED_AUTHORITY_ENV = re.compile(
    r"^(OPIP_)?(FUNDED|LIVE_EXECUTION|ORDER_PLACEMENT|ORDER_MODIFICATION|"
    r"ORDER_CANCEL|EXCHANGE_EXECUTION)[A-Z0-9_]*$"
)
TRUTHY = {"1", "true", "yes", "on", "enabled", "active", "live"}
EXPECTED_AUTHORITY = {
    "canonical_writer_mode": "shadow",
    "committee_mode": "off",
    "feature_bus": "shadow",
    "funded_authority": "ABSENT",
    "new_entry_authority": "LEGACY_ONLY",
    "paper_v2": "OFF",
    "release_profile": "EVIDENCE_SHADOW",
    "target_spine": "shadow",
}
MIN_RELEASE_MANIFEST_ENTRIES = 1000


class DeployLockBusy(RuntimeError):
    """The production deploy controller holds the authoritative lock."""


class DeployLockUnavailable(RuntimeError):
    """The host cannot provide the authoritative deploy lock."""


class ProvenanceConflict(RuntimeError):
    """An existing release marker disagrees with the reconciled release."""


@contextmanager
def deploy_lock(path: Path = DEPLOY_LOCK):
    """Hold the exact lock used by the production deploy controller.

    Non-blocking by design: waiting behind a live deploy would validate a stale
    snapshot by the time the lock is released. A busy lock is a refusal.
    """
    if fcntl is None:
        raise DeployLockUnavailable("fcntl is unavailable; refusing without lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeployLockBusy(f"deploy lock busy: {path}") from exc
        except OSError as exc:
            raise DeployLockUnavailable(
                f"deploy lock unavailable: {path}: {exc}"
            ) from exc
        yield path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)



@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ContainerFacts:
    name: str
    container_id: str
    image_id: str
    revision: str
    status: str
    health: str
    env: Mapping[str, str]


@dataclass(frozen=True)
class DatabaseFacts:
    journal_mode: str
    quick_check: str
    foreign_key_violations: int
    event_count: int
    max_local_sequence: int
    history_epoch: int
    schema_version: int


@dataclass(frozen=True)
class Evidence:
    expected_sha: str
    core: ContainerFacts
    writer: ContainerFacts
    live_acceptance: Mapping
    core_upgrade: Mapping
    receipt_hashes: Mapping[str, str]
    manifest_entries: int | None
    manifest_mismatches: int | None
    database: DatabaseFacts
    last_good_before: str


def _get(value: object, *path: str, default=None):
    current = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate(evidence: Evidence) -> tuple[Check, ...]:
    """Pure evaluation of already-collected evidence. No I/O, no mutation."""

    checks: list[Check] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(Check(name=name, passed=bool(passed), detail=detail))

    live = evidence.live_acceptance
    upgrade = evidence.core_upgrade
    db = evidence.database
    acceptance_db = _get(live, "database", default={}) or {}

    add(
        "expected_sha_well_formed",
        bool(SHA_RE.match(evidence.expected_sha or "")),
        f"sha={evidence.expected_sha}",
    )
    add(
        "core_container_healthy",
        evidence.core.status == "running" and evidence.core.health == "healthy",
        f"status={evidence.core.status} health={evidence.core.health}",
    )
    add(
        "core_image_revision_matches_expected",
        evidence.core.revision == evidence.expected_sha,
        f"revision={evidence.core.revision}",
    )
    live_core_image = _get(live, "release", "core_image")
    add(
        "core_image_matches_live_acceptance",
        bool(live_core_image) and evidence.core.image_id == live_core_image,
        f"core_image={evidence.core.image_id} live_acceptance={live_core_image}",
    )
    candidate_image = _get(upgrade, "candidate_image", "image_id")
    add(
        "core_image_matches_core_upgrade_candidate",
        bool(candidate_image) and evidence.core.image_id == candidate_image,
        f"core_image={evidence.core.image_id} candidate={candidate_image}",
    )
    add(
        "live_acceptance_receipt_identity",
        live.get("receipt") == "LIVE_ACCEPTANCE",
        f"receipt={live.get('receipt')}",
    )
    add(
        "live_acceptance_release_sha",
        _get(live, "release", "app_sha") == evidence.expected_sha,
        f"app_sha={_get(live, 'release', 'app_sha')}",
    )
    add(
        "live_acceptance_architecture_gate",
        _get(live, "release", "architecture_gate") == "PASS",
        f"architecture_gate={_get(live, 'release', 'architecture_gate')}",
    )
    add(
        "live_acceptance_target_state",
        live.get("target_state") == "LIVE_EVIDENCE_SHADOW",
        f"target_state={live.get('target_state')}",
    )
    add(
        "core_upgrade_receipt_identity",
        upgrade.get("receipt") == "CORE_SECURITY_UPGRADE_QUALIFICATION",
        f"receipt={upgrade.get('receipt')}",
    )
    add(
        "core_upgrade_is_append_only",
        upgrade.get("append_only") is True,
        f"append_only={upgrade.get('append_only')}",
    )
    add(
        "core_upgrade_provenance_verified",
        _get(upgrade, "provenance", "verdict") == "VERIFIED",
        f"verdict={_get(upgrade, 'provenance', 'verdict')}",
    )
    add(
        "core_upgrade_merged_main_sha",
        upgrade.get("merged_main_sha") == evidence.expected_sha,
        f"merged_main_sha={upgrade.get('merged_main_sha')}",
    )
    add(
        "core_upgrade_candidate_revision_label",
        _get(upgrade, "candidate_image", "revision_label") == evidence.expected_sha,
        f"revision_label={_get(upgrade, 'candidate_image', 'revision_label')}",
    )
    add(
        "core_upgrade_candidate_built_from_sha",
        _get(upgrade, "candidate_image", "built_from_sha") == evidence.expected_sha,
        f"built_from_sha={_get(upgrade, 'candidate_image', 'built_from_sha')}",
    )

    writer_expected = _get(live, "release", "certified_writer_image")
    writer_preserved = _get(upgrade, "preserved_state", "writer_image")
    add(
        "writer_image_unchanged",
        bool(writer_expected)
        and evidence.writer.image_id == writer_expected == writer_preserved,
        f"writer={evidence.writer.image_id} live={writer_expected} "
        f"preserved={writer_preserved}",
    )
    add(
        "writer_container_identity_unchanged",
        _get(upgrade, "preserved_state", "writer_container_id")
        == evidence.writer.container_id,
        f"container_id={evidence.writer.container_id}",
    )
    add(
        "writer_healthy",
        evidence.writer.status == "running" and evidence.writer.health == "healthy",
        f"status={evidence.writer.status} health={evidence.writer.health}",
    )
    live_env = evidence.core.env or {}
    runtime_mismatches = {
        key: live_env.get(key)
        for key, expected in EXPECTED_RUNTIME_ENV.items()
        if live_env.get(key) != expected
    }
    add(
        "runtime_authority_modes",
        not runtime_mismatches,
        (
            f"mismatched={runtime_mismatches}"
            if runtime_mismatches
            else "EVIDENCE_SHADOW/shadow/off"
        ),
    )
    funded_keys = sorted(
        key
        for key, value in live_env.items()
        if FUNDED_AUTHORITY_ENV.match(key)
        and str(value).strip().lower() in TRUTHY
    )
    add(
        "runtime_funded_authority_absent",
        not funded_keys,
        f"truthy_authority_keys={funded_keys}" if funded_keys else "ABSENT",
    )
    writer_env = evidence.writer.env or {}
    add(
        "writer_container_not_authority_widened",
        writer_env.get("OPIP_FEATURE_BUS_MODE") not in {"active", "live"},
        f"writer_mode={writer_env.get('OPIP_CANONICAL_WRITER_MODE')} "
        f"writer_bus={writer_env.get('OPIP_FEATURE_BUS_MODE')}",
    )
    authority = _get(live, "authority", default={}) or {}
    mismatched = {
        key: authority.get(key)
        for key, expected in EXPECTED_AUTHORITY.items()
        if authority.get(key) != expected
    }
    add(
        "authority_unchanged",
        not mismatched,
        f"mismatched={mismatched}" if mismatched else "shadow/off/funded_absent",
    )
    add(
        "db_journal_wal",
        str(db.journal_mode).lower() == "wal",
        f"journal_mode={db.journal_mode}",
    )
    add(
        "db_integrity_ok",
        db.quick_check == "ok",
        f"quick_check={db.quick_check}",
    )
    add(
        "db_foreign_keys_clean",
        db.foreign_key_violations == 0,
        f"violations={db.foreign_key_violations}",
    )
    add(
        "db_history_epoch_matches_acceptance",
        db.history_epoch == acceptance_db.get("history_epoch"),
        f"db={db.history_epoch} acceptance={acceptance_db.get('history_epoch')}",
    )
    acceptance_next = acceptance_db.get("next_local_sequence")
    add(
        "db_sequence_monotonic",
        isinstance(acceptance_next, int)
        and db.max_local_sequence >= acceptance_next
        and db.event_count >= int(acceptance_db.get("event_count") or 0),
        f"max_local_sequence={db.max_local_sequence} event_count={db.event_count} "
        f"acceptance_next={acceptance_next}",
    )
    entries = evidence.manifest_entries
    add(
        "release_manifest_verified",
        entries is not None
        and entries >= MIN_RELEASE_MANIFEST_ENTRIES
        and evidence.manifest_mismatches == 0,
        f"entries={entries} mismatches={evidence.manifest_mismatches}",
    )
    add(
        "last_good_not_conflicting",
        evidence.last_good_before in {"", evidence.expected_sha},
        f"existing={evidence.last_good_before or '<absent>'}",
    )
    return tuple(checks)


def failed_checks(checks: tuple[Check, ...]) -> list[Check]:
    return [check for check in checks if not check.passed]



def container_facts(name: str) -> ContainerFacts:
    """Read-only docker inspect. Never starts, stops or restarts anything."""
    result = subprocess.run(
        ["docker", "inspect", name, "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {name}")
    payload = json.loads(result.stdout)
    state = payload.get("State") or {}
    health = (state.get("Health") or {}).get("Status") or "none"
    labels = (payload.get("Config") or {}).get("Labels") or {}
    env: dict[str, str] = {}
    for entry in (payload.get("Config") or {}).get("Env") or []:
        if not isinstance(entry, str) or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        if any(hint in key.upper() for hint in SECRET_ENV_HINTS):
            env[key] = "<redacted>"
        else:
            env[key] = value
    return ContainerFacts(
        name=name,
        container_id=str(payload.get("Id") or ""),
        image_id=str(payload.get("Image") or ""),
        revision=str(labels.get("org.opencontainers.image.revision") or ""),
        status=str(state.get("Status") or ""),
        health=str(health),
        env=env,
    )


def database_facts(path: Path) -> DatabaseFacts:
    """Read-only canonical database facts. Never writes or migrates."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        event_count = int(
            conn.execute('SELECT COUNT(*) FROM "events"').fetchone()[0]
        )
        max_sequence = conn.execute(
            'SELECT MAX(local_sequence) FROM "events"'
        ).fetchone()[0]
        meta = conn.execute(
            "SELECT schema_version, history_epoch, next_local_sequence "
            "FROM meta WHERE id = 1"
        ).fetchone()
        if meta is None:
            raise RuntimeError("canonical meta row missing")
        return DatabaseFacts(
            journal_mode=journal_mode,
            quick_check=quick_check,
            foreign_key_violations=violations,
            event_count=event_count,
            max_local_sequence=int(max_sequence or 0),
            history_epoch=int(meta[1]),
            schema_version=int(meta[0]),
        )
    finally:
        conn.close()


def verify_release_manifest(app_root: Path, manifest: Path) -> tuple[int | None, int | None]:
    """Verify the checksummed release tree. Returns (entries, mismatches)."""
    if not manifest.is_file():
        return (None, None)
    entries = sum(
        1
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    )
    result = subprocess.run(
        ["sha256sum", "-c", "--quiet", str(manifest)],
        cwd=str(app_root),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode == 0:
        return (entries, 0)
    mismatches = sum(
        1
        for line in (result.stderr or result.stdout or "").splitlines()
        if line.strip().endswith("FAILED")
    )
    return (entries, mismatches or None)


def _load_json(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def load_receipts(
    bootstrap_dir: Path, expected_sha: str
) -> tuple[dict, dict, dict[str, str]]:
    live_path = bootstrap_dir / "LIVE-ACCEPTANCE.json"
    live = _load_json(live_path)

    upgrade = {}
    upgrade_path = None
    for candidate in sorted(bootstrap_dir.glob("CORE-UPGRADE-*.json")):
        payload = _load_json(candidate)
        if payload.get("merged_main_sha") == expected_sha:
            if upgrade_path is not None:
                raise RuntimeError("multiple matching CORE-UPGRADE receipts")
            upgrade = payload
            upgrade_path = candidate

    hashes: dict[str, str] = {}
    for path in (live_path, upgrade_path, bootstrap_dir / "SCHEDULER_ACTIVATED.json"):
        if path is not None and path.is_file():
            hashes[str(path)] = _sha256_file(path)
    return (live, upgrade, hashes)


def last_good_value(state_dir: Path) -> str:
    path = state_dir / "last-good-sha"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()



def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_receipt(
    directory: Path, status: str, stamp: str, sha: str, payload: dict
) -> Path:
    """Append-only receipt, durable before the caller may publish a marker."""
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    base = f"{stamp}-{sha}-{status.lower()}"
    for suffix in range(0, 100):
        name = f"{base}.json" if suffix == 0 else f"{base}.{suffix}.json"
        path = directory / name
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(directory)
        return path
    raise RuntimeError("could not allocate append-only receipt name")


def _publish_last_good(state_dir: Path, sha: str) -> bool:
    """Publish last-good-sha atomically. Returns True when the value changed.

    A conflicting existing marker is never replaced, even if the process that
    validated it lost a race with a newer deployment.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    target = state_dir / "last-good-sha"
    current = last_good_value(state_dir)
    if current and current != sha:
        raise ProvenanceConflict(
            f"existing last-good-sha={current} conflicts with reconciled={sha}"
        )
    if current == sha:
        return False
    tmp = state_dir / f".last-good-sha.tmp.{os.getpid()}"
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{sha}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    _fsync_dir(state_dir)
    return True


def _lock_refusal(
    *,
    expected_sha: str,
    apply: bool,
    state_dir: Path,
    moment: datetime,
    reason: str,
    detail: str,
    receipt_writer,
) -> dict:
    receipt = {
        "schema": SCHEMA,
        "receipt": "CORE_RELEASE_PROVENANCE_RECONCILIATION",
        "recorded_at_utc": moment.isoformat(),
        "host": socket.gethostname(),
        "expected_sha": expected_sha,
        "apply": bool(apply),
        "status": "REFUSED",
        "failures": [reason],
        "detail": detail,
        "checks": [],
        "evidence": {"last_good_before": last_good_value(state_dir)},
        "historical_evidence_modified": False,
        "trading_authority_changed": False,
        "deploy_invoked": False,
        "services_restarted": False,
    }
    if apply:
        try:
            path = receipt_writer(
                state_dir / "provenance" / "rejected",
                "lock-refused",
                moment.strftime("%Y%m%dT%H%M%SZ"),
                expected_sha,
                receipt,
            )
            receipt["receipt_path"] = str(path)
        except OSError as exc:
            receipt["receipt_write_error"] = type(exc).__name__
    return receipt


def reconcile(
    *,
    expected_sha: str,
    app_root: Path,
    state_dir: Path,
    bootstrap_dir: Path,
    manifest: Path,
    apply: bool,
    lock=deploy_lock,
    receipt_writer=_append_receipt,
    publisher=_publish_last_good,
    container_reader=container_facts,
    db_reader=database_facts,
    manifest_verifier=verify_release_manifest,
    receipts_loader=load_receipts,
    now: datetime | None = None,
) -> tuple[dict, int]:
    """Hold the controller lock across collection, validation and publication."""
    moment = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    try:
        with lock():
            return _reconcile_locked(
                expected_sha=expected_sha,
                app_root=app_root,
                state_dir=state_dir,
                bootstrap_dir=bootstrap_dir,
                manifest=manifest,
                apply=apply,
                receipt_writer=receipt_writer,
                publisher=publisher,
                container_reader=container_reader,
                db_reader=db_reader,
                manifest_verifier=manifest_verifier,
                receipts_loader=receipts_loader,
                moment=moment,
            )
    except DeployLockBusy as exc:
        return (
            _lock_refusal(
                expected_sha=expected_sha,
                apply=apply,
                state_dir=state_dir,
                moment=moment,
                reason="deploy_lock_busy",
                detail=str(exc),
                receipt_writer=receipt_writer,
            ),
            2,
        )
    except DeployLockUnavailable as exc:
        return (
            _lock_refusal(
                expected_sha=expected_sha,
                apply=apply,
                state_dir=state_dir,
                moment=moment,
                reason="deploy_lock_unavailable",
                detail=str(exc),
                receipt_writer=receipt_writer,
            ),
            2,
        )


def _reconcile_locked(
    *,
    expected_sha: str,
    app_root: Path,
    state_dir: Path,
    bootstrap_dir: Path,
    manifest: Path,
    apply: bool,
    receipt_writer,
    publisher,
    container_reader,
    db_reader,
    manifest_verifier,
    receipts_loader,
    moment: datetime,
) -> tuple[dict, int]:

    live, upgrade, hashes = receipts_loader(bootstrap_dir, expected_sha)
    core = container_reader("ohm-trade-agent")
    writer = container_reader("opip-canonical-writer")
    db_path = app_root / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    database = db_reader(db_path)
    entries, mismatches = manifest_verifier(app_root, manifest)
    evidence = Evidence(
        expected_sha=expected_sha,
        core=core,
        writer=writer,
        live_acceptance=live,
        core_upgrade=upgrade,
        receipt_hashes=hashes,
        manifest_entries=entries,
        manifest_mismatches=mismatches,
        database=database,
        last_good_before=last_good_value(state_dir),
    )
    checks = evaluate(evidence)
    failures = failed_checks(checks)

    receipt = {
        "schema": SCHEMA,
        "receipt": "CORE_RELEASE_PROVENANCE_RECONCILIATION",
        "recorded_at_utc": moment.isoformat(),
        "host": socket.gethostname(),
        "expected_sha": expected_sha,
        "apply": bool(apply),
        "checks": [
            {"name": check.name, "passed": check.passed, "detail": check.detail}
            for check in checks
        ],
        "failures": [check.name for check in failures],
        "evidence": {
            "core": {
                "container_id": core.container_id,
                "image_id": core.image_id,
                "revision": core.revision,
                "status": core.status,
                "health": core.health,
            },
            "writer": {
                "container_id": writer.container_id,
                "image_id": writer.image_id,
                "status": writer.status,
                "health": writer.health,
            },
            "database": {
                "journal_mode": database.journal_mode,
                "quick_check": database.quick_check,
                "foreign_key_violations": database.foreign_key_violations,
                "event_count": database.event_count,
                "max_local_sequence": database.max_local_sequence,
                "history_epoch": database.history_epoch,
                "schema_version": database.schema_version,
            },
            "release_manifest_entries": entries,
            "release_manifest_mismatches": mismatches,
            "receipt_sha256": dict(hashes),
            "last_good_before": evidence.last_good_before,
        },
        "historical_evidence_modified": False,
        "trading_authority_changed": False,
        "deploy_invoked": False,
        "services_restarted": False,
    }

    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    if failures:
        receipt["status"] = "REFUSED"
        if apply:
            path = receipt_writer(
                state_dir / "provenance" / "rejected",
                "refused",
                stamp,
                expected_sha,
                receipt,
            )
            receipt["receipt_path"] = str(path)
        return receipt, (2 if apply else 1)

    if not apply:
        receipt["status"] = "DRY_RUN_PASS"
        receipt["last_good_sha_published"] = False
        return receipt, 0

    # Write-ahead: provenance must be durable on disk before the authoritative
    # marker becomes publishable. A crash after this point leaves a marker-less
    # but fully documented refusal-safe state, never an unexplained marker.
    receipt["status"] = "RECONCILED"
    receipt["publication_state"] = "PENDING"
    receipt["last_good_sha_target"] = expected_sha
    try:
        pending = receipt_writer(
            state_dir / "provenance" / "reconciled",
            "pending",
            stamp,
            expected_sha,
            receipt,
        )
    except OSError as exc:
        receipt["status"] = "PROVENANCE_WRITE_FAILED"
        receipt["publication_state"] = "BLOCKED"
        receipt["detail"] = f"{type(exc).__name__}: {exc}"
        return receipt, 4
    receipt["provenance_receipt_path"] = str(pending)

    try:
        changed = publisher(state_dir, expected_sha)
    except ProvenanceConflict as exc:
        receipt["status"] = "REFUSED"
        receipt["publication_state"] = "CONFLICT"
        receipt["detail"] = str(exc)
        receipt["failures"] = ["conflicting_release_marker"]
        path = receipt_writer(
            state_dir / "provenance" / "rejected",
            "conflict",
            stamp,
            expected_sha,
            receipt,
        )
        receipt["receipt_path"] = str(path)
        return receipt, 2
    except OSError as exc:
        receipt["status"] = "PUBLICATION_FAILED"
        receipt["publication_state"] = "FAILED"
        receipt["detail"] = f"{type(exc).__name__}: {exc}"
        path = receipt_writer(
            state_dir / "provenance" / "rejected",
            "publication-failed",
            stamp,
            expected_sha,
            receipt,
        )
        receipt["receipt_path"] = str(path)
        return receipt, 3

    receipt["publication_state"] = "PUBLISHED"
    receipt["last_good_sha_published"] = True
    receipt["last_good_sha_changed"] = changed
    receipt["last_good_sha_after"] = last_good_value(state_dir)
    confirmation = {
        "schema": SCHEMA,
        "receipt": "CORE_RELEASE_PROVENANCE_PUBLICATION",
        "status": "PUBLISHED",
        "recorded_at_utc": moment.isoformat(),
        "expected_sha": expected_sha,
        "provenance_receipt": str(pending),
        "last_good_sha": receipt["last_good_sha_after"],
        "last_good_sha_changed": changed,
        "marker_sha256": hashlib.sha256(
            f"{expected_sha}\n".encode("utf-8")
        ).hexdigest(),
        "historical_evidence_modified": False,
        "trading_authority_changed": False,
    }
    path = receipt_writer(
        state_dir / "provenance" / "reconciled",
        "published",
        stamp,
        expected_sha,
        confirmation,
    )
    receipt["publication_receipt_path"] = str(path)
    receipt["receipt_path"] = str(pending)
    return receipt, 0



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile the missing core release provenance consumed by the "
            "learning exporter. Dry-run by default; --apply requires root."
        )
    )
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument(
        "--app-root", default="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
    )
    parser.add_argument("--state-dir", default="/var/lib/ohm-deploy")
    parser.add_argument("--bootstrap-dir", default="/var/lib/opip-bootstrap")
    parser.add_argument(
        "--manifest",
        default="/var/lib/opip-bootstrap/release-manifest.sha256",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publish last-good-sha after every check passes. Default is dry-run.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.apply and os.geteuid() != 0:
        print("REFUSED reason=root_required_for_apply")
        return 2

    receipt, code = reconcile(
        expected_sha=args.expected_sha.strip(),
        app_root=Path(args.app_root),
        state_dir=Path(args.state_dir),
        bootstrap_dir=Path(args.bootstrap_dir),
        manifest=Path(args.manifest),
        apply=bool(args.apply),
    )
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(f"status={receipt['status']} expected_sha={receipt['expected_sha']}")
        for check in receipt["checks"]:
            print(
                f"  {'PASS' if check['passed'] else 'FAIL'} {check['name']} "
                f"({check['detail']})"
            )
        if receipt.get("receipt_path"):
            print(f"receipt={receipt['receipt_path']}")
        if receipt["status"] == "RECONCILED":
            print(f"last_good_sha={receipt.get('last_good_sha_after')}")
    return code


if __name__ == "__main__":
    sys.exit(main())

