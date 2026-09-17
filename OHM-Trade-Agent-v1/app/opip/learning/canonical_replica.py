"""Read-only canonical learning replica contract, export, and installation.

The production canonical SQLite store is the authority for terminal paper
outcomes. The learning plane needs that evidence to run readiness and learning,
but it must never become a second authority and must never reach back into
production to read it.

This module owns the whole **copy-only replica bridge**:

* the one-root bundle layout and path derivation;
* the cross-artifact manifest that binds all three authority inputs;
* export (production side), reusing the PR-A0 online-backup machinery;
* verification and atomic generation installation (learning side);
* a CLI seam so the shell scripts never reimplement SQLite validation.

Boundary rules:

* The replica is authoritative for **nothing**. Production remains the only
  canonical writer.
* Verification is provenance-first: a replica is unusable unless it is tied to
  one exact production release SHA, hashes to its recorded bytes, is
  sidecar-free and self-contained, and is not staler than the documented bound.
* Every failure resolves to *unavailable* or *incomplete*, never to *complete*.
  It is always safe to distrust a replica and never safe to trust one that
  cannot be proven.
* A legitimately empty outcome stream is not a failure and is not decided here.
  Provenance and emptiness are separate questions; the canonical outcome reader
  owns the latter.

Three authority inputs are bundled, because completeness cannot be decided from
the database alone: the canonical store, the paper lifecycle/outbox state
(COMMITTED / PENDING / PERMANENT_FAILURE), and the paper evidence-gap spool.
Learning must never combine two of them from one generation and the third from
another.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any, Mapping
import uuid

from app.opip.canonical.backup import (
    BackupFormatError,
    BackupProvenanceError,
    assert_rollback_journal_backup,
    assert_sidecar_free,
    hash_file_sha256,
    publish_backup_generation,
    require_release_sha,
)
from app.opip.canonical.schema import fsync_directory_required, fsync_file_required
from app.opip.canonical.paths import SCHEMA_VERSION

REPLICA_SCHEMA_VERSION = 1

#: Marker key carried additively in the schema-4 export manifest. Older workers
#: ignore unknown keys, so its presence does not break the established
#: ``production first, learning second`` release order.
REPLICA_MARKER_KEY = "canonical_learning_replica_version"

MANIFEST_FILENAME = "replica_manifest.json"
CANONICAL_RELATIVE = Path("opip") / "canonical" / "opip_canonical_v1.sqlite3"
PAPER_STATE_RELATIVE = Path("paper_trading") / "state.json"
PAPER_GAP_RELATIVE = Path("paper_trading") / "evidence_gap_spool.json"

#: Container-side single configuration root. Everything is derived from this so
#: a generation cannot be assembled from mismatched path settings.
DEFAULT_REPLICA_ROOT = Path("/app/canonical-replica")

#: Host-side repository of immutable generations plus the ``current`` pointer.
HOST_GENERATIONS_DIRNAME = "generations"
HOST_CURRENT_POINTER = "current"

#: The production export runs every 2 minutes and the learning sync timer every
#: 2 minutes offset from it, so a healthy bridge refreshes the replica at least
#: twice every 4 minutes. 30 minutes is roughly 15 export cycles: far enough
#: above the cadence to tolerate transient sync failures without flapping, and
#: far enough below a daily window to notice a bridge that has genuinely
#: stopped. Chosen explicitly rather than inherited, because no existing
#: threshold applies to this artifact (the dashboard freshness policy in
#: ``data_platform/freshness.py`` governs a different plane at a 120s/300s
#: scale that would be unusable here).
REPLICA_FRESHNESS_SECONDS = 1800

#: Generations retained on the learning host: the active one plus one
#: known-good fallback. Bounded so the replica cannot grow without limit.
RETAINED_GENERATIONS = 2

REASON_MANIFEST_MISSING = "CANONICAL_REPLICA_MANIFEST_MISSING"
REASON_MANIFEST_MALFORMED = "CANONICAL_REPLICA_MANIFEST_MALFORMED"
REASON_SCHEMA_UNSUPPORTED = "CANONICAL_REPLICA_SCHEMA_UNSUPPORTED"
REASON_SOURCE_SHA_MISMATCH = "CANONICAL_REPLICA_SOURCE_SHA_MISMATCH"
REASON_DB_MISSING = "CANONICAL_REPLICA_DB_MISSING"
REASON_DB_HASH_MISMATCH = "CANONICAL_REPLICA_DB_HASH_MISMATCH"
REASON_DB_SIZE_MISMATCH = "CANONICAL_REPLICA_DB_SIZE_MISMATCH"
REASON_DB_NOT_SELF_CONTAINED = "CANONICAL_REPLICA_DB_NOT_SELF_CONTAINED"
REASON_PAPER_STATE_MISSING = "CANONICAL_REPLICA_PAPER_STATE_MISSING"
REASON_PAPER_STATE_MISMATCH = "CANONICAL_REPLICA_PAPER_STATE_MISMATCH"
REASON_PAPER_GAP_MISMATCH = "CANONICAL_REPLICA_PAPER_GAP_MISMATCH"
REASON_STALE = "CANONICAL_REPLICA_STALE"
REASON_TIMESTAMP_INVALID = "CANONICAL_REPLICA_TIMESTAMP_INVALID"
REASON_GENERATION_ID_INVALID = "CANONICAL_REPLICA_GENERATION_ID_INVALID"
REASON_GENERATION_ID_COLLISION = "CANONICAL_REPLICA_GENERATION_ID_COLLISION"

#: Completeness reason recorded when the bundled lifecycle state is absent.
#: Absence is certified in the manifest rather than silent, but it still cannot
#: prove outbox completeness - which is exactly the distinction between
#: "verified" and "sufficient for supervised truth".
REASON_PAPER_STATE_ABSENT = "CANONICAL_REPLICA_PAPER_STATE_ABSENT"


class ReplicaVerificationError(RuntimeError):
    """The installed replica cannot be proven usable.

    ``reason`` is a stable code so readiness can record *why* the canonical
    source is unavailable rather than only that it is.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}{': ' + detail if detail else ''}")


class ReplicaUnavailableError(ReplicaVerificationError):
    """No usable replica is installed."""


class ReplicaStaleError(ReplicaVerificationError):
    """The replica is installed and valid but too old to certify a population."""


class ReplicaProvenanceError(ReplicaVerificationError):
    """The replica does not match its recorded provenance."""


def iso_z(value: datetime) -> str:
    """Render a UTC timestamp in the ``Z`` form used across evidence ids."""
    moment = value.astimezone(timezone.utc)
    if moment.microsecond == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}Z"


# ---------------------------------------------------------------------------
# Layout: one root, everything derived
# ---------------------------------------------------------------------------


def replica_root(root: Path | None = None) -> Path:
    """Resolve the single replica root.

    ``OPIP_CANONICAL_REPLICA_ROOT`` is the one configuration knob. Every bundle
    path derives from it so a generation cannot be assembled from mismatched
    settings.
    """
    if root is not None:
        return Path(root)
    override = os.environ.get("OPIP_CANONICAL_REPLICA_ROOT", "").strip()
    return Path(override) if override else DEFAULT_REPLICA_ROOT


def replica_manifest_path(root: Path | None = None) -> Path:
    return replica_root(root) / MANIFEST_FILENAME


def replica_db_path(root: Path | None = None) -> Path:
    return replica_root(root) / CANONICAL_RELATIVE


def replica_paper_state_path(root: Path | None = None) -> Path:
    return replica_root(root) / PAPER_STATE_RELATIVE


def replica_paper_gap_path(root: Path | None = None) -> Path:
    return replica_root(root) / PAPER_GAP_RELATIVE


def host_generations_dir(host_root: Path) -> Path:
    return Path(host_root) / HOST_GENERATIONS_DIRNAME


def host_current_pointer(host_root: Path) -> Path:
    return Path(host_root) / HOST_CURRENT_POINTER


# ---------------------------------------------------------------------------
# Snapshot facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotFacts:
    """Structural facts read from a canonical snapshot."""

    schema_version: int
    history_epoch: int
    next_local_sequence: int
    max_local_sequence: int | None
    event_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "history_epoch": self.history_epoch,
            "next_local_sequence": self.next_local_sequence,
            "max_local_sequence": self.max_local_sequence,
            "event_count": self.event_count,
        }


def read_snapshot_facts(db_path: Path) -> SnapshotFacts:
    """Read meta and event facts from a snapshot, read-only.

    Opened ``mode=ro`` so this can never write to the artifact it inspects. The
    tip is computed lexicographically for the same reason the outcome reader
    does: a canonical restore resets ``local_sequence``, so independent column
    maxima would fabricate a coordinate that need not exist.
    """
    if not db_path.exists():
        raise ReplicaUnavailableError(REASON_DB_MISSING, str(db_path))
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        meta = connection.execute(
            "SELECT schema_version, history_epoch, next_local_sequence FROM meta WHERE id = 1"
        ).fetchone()
        if meta is None:
            raise ReplicaProvenanceError(REASON_DB_NOT_SELF_CONTAINED, "meta row is missing")
        schema_version = meta["schema_version"]
        if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
            raise ReplicaProvenanceError(
                REASON_SCHEMA_UNSUPPORTED,
                f"snapshot schema_version={schema_version!r} incompatible with "
                f"code SCHEMA_VERSION={SCHEMA_VERSION}",
            )
        count_row = connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        event_count = int(count_row["n"]) if count_row is not None else 0
        max_seq: int | None = None
        if event_count:
            tip = connection.execute(
                """
                SELECT local_sequence FROM events
                WHERE history_epoch = ?
                ORDER BY local_sequence DESC LIMIT 1
                """,
                (int(meta["history_epoch"]),),
            ).fetchone()
            if tip is not None:
                max_seq = int(tip["local_sequence"])
        return SnapshotFacts(
            schema_version=int(schema_version),
            history_epoch=int(meta["history_epoch"]),
            next_local_sequence=int(meta["next_local_sequence"]),
            max_local_sequence=max_seq,
            event_count=event_count,
        )
    except sqlite3.Error as exc:
        raise ReplicaProvenanceError(
            REASON_DB_NOT_SELF_CONTAINED, f"snapshot is unreadable: {exc}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplicaArtifact:
    """One artifact bound into the replica manifest."""

    present: bool
    sha256: str | None
    size_bytes: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class CanonicalLearningReplicaManifest:
    """A verified replica manifest."""

    replica_schema_version: int
    source_release_sha: str
    snapshot_created_at_utc: datetime
    generation_id: str
    canonical: ReplicaArtifact
    paper_state: ReplicaArtifact
    paper_gap_spool: ReplicaArtifact
    facts: SnapshotFacts

    def as_dict(self) -> dict[str, Any]:
        return {
            "replica_schema_version": self.replica_schema_version,
            "source_release_sha": self.source_release_sha,
            "snapshot_created_at_utc": iso_z(self.snapshot_created_at_utc),
            "generation_id": self.generation_id,
            "canonical_db_present": self.canonical.present,
            "canonical_db_sha256": self.canonical.sha256,
            "canonical_db_bytes": self.canonical.size_bytes,
            "paper_state_present": self.paper_state.present,
            "paper_state_sha256": self.paper_state.sha256,
            "paper_state_bytes": self.paper_state.size_bytes,
            "paper_gap_present": self.paper_gap_spool.present,
            "paper_gap_sha256": self.paper_gap_spool.sha256,
            "paper_gap_bytes": self.paper_gap_spool.size_bytes,
            **self.facts.as_dict(),
        }


def _artifact_facts(path: Path) -> dict[str, Any]:
    """Hash and size one artifact, or record it as explicitly absent.

    Absence is recorded, never inferred. A manifest that says ``present: false``
    is a positive statement that the exporter found no such artifact, which is
    what makes "legitimately absent" distinguishable from "lost mid-export".
    """
    if not path.exists():
        return {"present": False, "sha256": None, "size_bytes": None}
    return {
        "present": True,
        "sha256": hash_file_sha256(path),
        "size_bytes": int(path.stat().st_size),
    }


def build_replica_manifest(
    *,
    source_release_sha: str,
    canonical_db: Path,
    paper_state: Path | None = None,
    paper_gap_spool: Path | None = None,
    generation_id: str | None = None,
    snapshot_created_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build the cross-artifact manifest for one replica generation.

    ``source_release_sha`` must be the exact production release SHA; an empty or
    malformed value raises rather than producing an unattributable manifest, and
    there is no ``UNVERIFIED`` fallback.

    The caller is responsible for having produced ``canonical_db`` through the
    canonical generation path, not a raw file copy; that is asserted here.
    """
    release_sha = require_release_sha(source_release_sha, field_name="source_release_sha")
    created = snapshot_created_at_utc or datetime.now(timezone.utc)
    identifier = generation_id or f"gen-{uuid.uuid4().hex[:16]}"
    require_generation_id(identifier)

    if not canonical_db.exists():
        raise ReplicaUnavailableError(
            REASON_DB_MISSING, f"canonical snapshot not found at {canonical_db}"
        )

    # Reuse the PR-A0 canonical format assertions rather than re-deriving them.
    # A raw copy of the live WAL store is not an acceptable snapshot: its
    # completeness would depend on a sidecar.
    try:
        assert_rollback_journal_backup(canonical_db)
        assert_sidecar_free(canonical_db, field_name="canonical_db", error_type=BackupFormatError)
    except BackupProvenanceError as exc:
        raise ReplicaProvenanceError(REASON_DB_NOT_SELF_CONTAINED, str(exc)) from exc

    facts = read_snapshot_facts(canonical_db)

    return {
        "replica_schema_version": REPLICA_SCHEMA_VERSION,
        "source_release_sha": release_sha,
        "snapshot_created_at_utc": iso_z(created),
        "generation_id": identifier,
        "canonical_db": _artifact_facts(canonical_db),
        "paper_state": _artifact_facts(paper_state)
        if paper_state is not None
        else {"present": False, "sha256": None, "size_bytes": None},
        "paper_gap_spool": _artifact_facts(paper_gap_spool)
        if paper_gap_spool is not None
        else {"present": False, "sha256": None, "size_bytes": None},
        "journal_mode": "delete",
        "self_contained": True,
        **facts.as_dict(),
    }


def require_generation_id(value: Any) -> str:
    """Validate a generation identifier: path-safe and non-empty.

    Rejects path separators and traversal so a manifest cannot name a
    generation outside the generations directory.
    """
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or text in {".", ".."}:
        raise ReplicaProvenanceError(
            REASON_GENERATION_ID_INVALID, f"unsafe generation id {value!r}"
        )
    return text


def read_replica_manifest(path: Path) -> dict[str, Any]:
    """Read a replica manifest, failing closed on any unreadable content."""
    if not path.exists():
        raise ReplicaUnavailableError(REASON_MANIFEST_MISSING, str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReplicaProvenanceError(REASON_MANIFEST_MALFORMED, str(exc)) from exc
    if not isinstance(payload, dict):
        raise ReplicaProvenanceError(
            REASON_MANIFEST_MALFORMED, "manifest must be a JSON object"
        )
    return payload


def write_replica_manifest(manifest: Mapping[str, Any], path: Path) -> Path:
    """Write a manifest atomically, then fsync the directory.

    The file is flushed before the rename, and the containing directory entry is
    then made durable with the same PR-A0 primitive authoritative publication
    uses. Without the directory sync the rename itself could be lost on crash,
    leaving a generation directory whose manifest is absent - which callers would
    see as unverifiable provenance rather than as the durability shortfall it is.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{target.name}.tmp.{os.getpid()}"
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n")
            handle.flush()
            # Same primitive and ordering as the PR-A0 canonical manifest write.
            fsync_file_required(temp)
        os.replace(temp, target)
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    # Before this returns, publication is reported as durable, so the namespace
    # entry must actually be durable.
    fsync_directory_required(target.parent)
    return target


def _parse_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReplicaProvenanceError(REASON_TIMESTAMP_INVALID, f"{field_name} is missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplicaProvenanceError(
            REASON_TIMESTAMP_INVALID, f"{field_name} is not ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReplicaProvenanceError(
            REASON_TIMESTAMP_INVALID, f"{field_name} must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _verify_artifact(
    recorded: object,
    path: Path,
    *,
    mismatch_reason: str,
    missing_reason: str | None,
) -> ReplicaArtifact:
    """Verify one bound artifact against the file actually installed.

    Two distinct conditions are deliberately separated:

    * **Recorded absent** (``present: false``) is a positive exporter statement.
      It is returned as ``present=False`` rather than raised, so a legitimately
      absent companion is distinguishable from a lost one. Whether absence is
      *acceptable* is a completeness question for the caller.
    * **Recorded present but not installed** is unavailability, and raises. This
      is the case that must never be read as "no lifecycles".
    """
    if not isinstance(recorded, Mapping):
        raise ReplicaProvenanceError(mismatch_reason, "manifest artifact block is invalid")

    if recorded.get("present") is not True:
        return ReplicaArtifact(present=False, sha256=None, size_bytes=None)

    if not path.exists():
        raise ReplicaUnavailableError(
            missing_reason or mismatch_reason, f"recorded present but missing at {path}"
        )
    expected_sha = recorded.get("sha256")
    expected_size = recorded.get("size_bytes")
    if not isinstance(expected_sha, str) or not expected_sha:
        raise ReplicaProvenanceError(mismatch_reason, "recorded sha256 is missing")
    if not isinstance(expected_size, int) or expected_size < 0:
        raise ReplicaProvenanceError(mismatch_reason, "recorded size is missing")

    actual_size = int(path.stat().st_size)
    if actual_size != expected_size:
        raise ReplicaProvenanceError(
            mismatch_reason, f"size {actual_size} != recorded {expected_size}"
        )
    actual_sha = hash_file_sha256(path)
    if actual_sha != expected_sha:
        raise ReplicaProvenanceError(mismatch_reason, "sha256 does not match recorded value")
    return ReplicaArtifact(present=True, sha256=actual_sha, size_bytes=actual_size)


def verify_replica_manifest(
    manifest: Mapping[str, Any],
    *,
    root: Path | None = None,
    expected_source_release_sha: str,
    now: datetime | None = None,
    max_age_seconds: int = REPLICA_FRESHNESS_SECONDS,
) -> CanonicalLearningReplicaManifest:
    """Verify a replica bundle at ``root``, raising on any unprovable condition.

    ``expected_source_release_sha`` is the production release the learning
    worker believes is deployed. Making it a required argument is deliberate: a
    replica can never be accepted merely because it is internally consistent
    with itself.
    """
    if not isinstance(manifest, Mapping):
        raise ReplicaProvenanceError(REASON_MANIFEST_MALFORMED, "manifest must be a mapping")

    schema = manifest.get("replica_schema_version")
    if type(schema) is not int or schema != REPLICA_SCHEMA_VERSION:
        raise ReplicaProvenanceError(
            REASON_SCHEMA_UNSUPPORTED,
            f"replica_schema_version={schema!r} unsupported by this build",
        )

    expected_sha = require_release_sha(
        expected_source_release_sha, field_name="expected_source_release_sha"
    )
    recorded_sha = manifest.get("source_release_sha")
    if str(recorded_sha or "") != expected_sha:
        raise ReplicaProvenanceError(
            REASON_SOURCE_SHA_MISMATCH,
            f"replica source {recorded_sha!r} != expected production {expected_sha}",
        )

    created = _parse_utc(
        manifest.get("snapshot_created_at_utc"), field_name="snapshot_created_at_utc"
    )
    generation_id = require_generation_id(manifest.get("generation_id"))

    base = replica_root(root)
    db_path = base / CANONICAL_RELATIVE

    canonical = _verify_artifact(
        manifest.get("canonical_db"),
        db_path,
        mismatch_reason=REASON_DB_HASH_MISMATCH,
        missing_reason=REASON_DB_MISSING,
    )
    if not canonical.present:
        raise ReplicaUnavailableError(REASON_DB_MISSING, str(db_path))

    # The snapshot must be a self-contained artifact. A WAL-format or
    # sidecar-dependent file is not a valid replica even if its bytes match.
    try:
        assert_rollback_journal_backup(db_path)
        assert_sidecar_free(db_path, field_name="canonical_db", error_type=BackupFormatError)
    except BackupProvenanceError as exc:
        raise ReplicaProvenanceError(REASON_DB_NOT_SELF_CONTAINED, str(exc)) from exc

    # Structural facts are re-derived from the artifact rather than trusted, so
    # a manifest cannot assert a schema or epoch the snapshot does not have.
    facts = read_snapshot_facts(db_path)
    recorded_facts = {
        "history_epoch": manifest.get("history_epoch"),
        "next_local_sequence": manifest.get("next_local_sequence"),
        "max_local_sequence": manifest.get("max_local_sequence"),
        "event_count": manifest.get("event_count"),
    }
    actual_facts = facts.as_dict()
    for key, recorded in recorded_facts.items():
        if type(recorded) is not type(actual_facts[key]) and not (
            recorded is None and actual_facts[key] is None
        ):
            raise ReplicaProvenanceError(
                REASON_DB_NOT_SELF_CONTAINED,
                f"manifest {key}={recorded!r} does not match snapshot {actual_facts[key]!r}",
            )
        if recorded != actual_facts[key]:
            raise ReplicaProvenanceError(
                REASON_DB_NOT_SELF_CONTAINED,
                f"manifest {key}={recorded!r} does not match snapshot {actual_facts[key]!r}",
            )

    paper_state = _verify_artifact(
        manifest.get("paper_state"),
        base / PAPER_STATE_RELATIVE,
        mismatch_reason=REASON_PAPER_STATE_MISMATCH,
        missing_reason=REASON_PAPER_STATE_MISSING,
    )
    gap_spool = _verify_artifact(
        manifest.get("paper_gap_spool"),
        base / PAPER_GAP_RELATIVE,
        mismatch_reason=REASON_PAPER_GAP_MISMATCH,
        missing_reason=None,
    )

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    age = (moment - created).total_seconds()
    if age > max_age_seconds:
        raise ReplicaStaleError(
            REASON_STALE, f"replica is {int(age)}s old, limit {max_age_seconds}s"
        )
    if age < -max_age_seconds:
        # A replica stamped well into the future is a provenance defect, not
        # freshness: it cannot have been produced by this production release.
        raise ReplicaProvenanceError(
            REASON_TIMESTAMP_INVALID, "snapshot timestamp is implausibly in the future"
        )

    return CanonicalLearningReplicaManifest(
        replica_schema_version=schema,
        source_release_sha=expected_sha,
        snapshot_created_at_utc=created,
        generation_id=generation_id,
        canonical=canonical,
        paper_state=paper_state,
        paper_gap_spool=gap_spool,
        facts=facts,
    )


def verify_installed_replica(
    *,
    expected_source_release_sha: str,
    root: Path | None = None,
    now: datetime | None = None,
    max_age_seconds: int = REPLICA_FRESHNESS_SECONDS,
) -> CanonicalLearningReplicaManifest:
    """Read then verify the replica bundle installed at ``root``."""
    base = replica_root(root)
    manifest = read_replica_manifest(replica_manifest_path(base))
    return verify_replica_manifest(
        manifest,
        root=base,
        expected_source_release_sha=expected_source_release_sha,
        now=now,
        max_age_seconds=max_age_seconds,
    )


def replica_supports_completeness(
    verified: CanonicalLearningReplicaManifest,
) -> tuple[bool, tuple[str, ...]]:
    """Whether a verified replica can support a complete-population claim.

    Verification establishes *provenance*. This establishes *sufficiency*, and
    it is deliberately a separate step so neither concern can be satisfied by
    the other.

    A bundle whose lifecycle state is absent cannot prove outbox completeness.
    Absence is certified rather than silent, but it is still insufficient for
    supervised truth.

    The gap spool is asymmetric on purpose: its legitimate absence means zero
    unresolved gaps, which production semantics already accept, so it does not
    block completeness. A *recorded present yet missing* spool is already
    unavailability and never reaches here.
    """
    reasons: list[str] = []
    if not verified.paper_state.present:
        reasons.append(REASON_PAPER_STATE_ABSENT)
    return (not reasons, tuple(reasons))


@dataclass(frozen=True)
class VerifiedReplicaBundle:
    """A verified generation plus the three authority paths it owns.

    Consumers must take all three paths from one instance of this object. That
    is the type-level guard against the split-generation error, where a
    canonical store from one generation is combined with lifecycle state or a
    gap spool from another.
    """

    root: Path
    manifest: CanonicalLearningReplicaManifest
    canonical_db_path: Path
    paper_state_path: Path
    paper_gap_spool_path: Path
    completeness_supported: bool
    completeness_reasons: tuple[str, ...]


def resolve_verified_replica_bundle(
    *,
    root: Path | None = None,
    expected_source_release_sha: str,
    now: datetime | None = None,
    max_age_seconds: int = REPLICA_FRESHNESS_SECONDS,
) -> VerifiedReplicaBundle:
    """Verify the installed replica and return its three authority paths.

    Readiness must begin here rather than by probing for a SQLite file, so that
    provenance is established before any evidence is read.
    """
    base = replica_root(root)
    verified = verify_installed_replica(
        expected_source_release_sha=expected_source_release_sha,
        root=base,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    supports, reasons = replica_supports_completeness(verified)
    return VerifiedReplicaBundle(
        root=base,
        manifest=verified,
        canonical_db_path=base / CANONICAL_RELATIVE,
        paper_state_path=base / PAPER_STATE_RELATIVE,
        paper_gap_spool_path=base / PAPER_GAP_RELATIVE,
        completeness_supported=supports,
        completeness_reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Export (production side)
# ---------------------------------------------------------------------------


def export_replica_bundle(
    *,
    source_db: Path,
    staging_dir: Path,
    source_release_sha: str,
    paper_state_source: Path | None = None,
    paper_gap_source: Path | None = None,
    generation_id: str | None = None,
    now: datetime | None = None,
    backup_work_dir: Path | None = None,
    require_paper_state: bool = True,
) -> dict[str, Any]:
    """Stage one complete replica bundle into ``staging_dir``.

    The canonical snapshot is produced by ``publish_backup_generation``, which
    performs a SQLite online backup and normalizes the artifact to
    rollback-journal. A raw copy of the live WAL store is never used.

    The bundle is staged as a whole; the caller publishes the export manifest
    last as the generation commit marker, so a reader can never observe a
    partially written generation.

    ``require_paper_state`` defaults to True: missing lifecycle state cannot
    prove outbox completeness, so by default its absence fails the export
    rather than producing a bundle that can never support supervised truth.
    """
    release_sha = require_release_sha(source_release_sha, field_name="source_release_sha")
    staging = Path(staging_dir)
    (staging / CANONICAL_RELATIVE).parent.mkdir(parents=True, exist_ok=True)
    (staging / PAPER_STATE_RELATIVE).parent.mkdir(parents=True, exist_ok=True)

    work = Path(backup_work_dir) if backup_work_dir is not None else staging / ".backup-work"
    work.mkdir(parents=True, exist_ok=True)

    generation = publish_backup_generation(
        Path(source_db),
        work,
        source_release_sha=release_sha,
        generation_id=(generation_id or f"gen-{uuid.uuid4().hex[:16]}"),
    )

    db_target = staging / CANONICAL_RELATIVE
    shutil.copyfile(generation.backup_path, db_target)
    # Normalization already happened inside the generation; assert it survived
    # the copy so the staged artifact is provably self-contained.
    assert_rollback_journal_backup(db_target)
    assert_sidecar_free(db_target, field_name="canonical_db", error_type=BackupFormatError)

    state_target = staging / PAPER_STATE_RELATIVE
    gap_target = staging / PAPER_GAP_RELATIVE
    state_source = Path(paper_state_source) if paper_state_source is not None else None
    gap_source = Path(paper_gap_source) if paper_gap_source is not None else None

    state_present = False
    if state_source is not None and state_source.exists():
        shutil.copyfile(state_source, state_target)
        state_present = True
    elif require_paper_state:
        raise ReplicaUnavailableError(
            REASON_PAPER_STATE_MISSING,
            f"paper lifecycle state not found at {state_source}",
        )

    if gap_source is not None and gap_source.exists():
        shutil.copyfile(gap_source, gap_target)
        gap_source_present = True
    else:
        # Legitimately absent: publish a certified empty spool so the artifact
        # is always a validated, readable contract, AND record in the manifest
        # that the production source was absent. Together those keep "absent"
        # distinguishable from "lost during export".
        gap_target.write_text(
            json.dumps({"unresolved": [], "updated_at": None}, sort_keys=True),
            encoding="utf-8",
        )
        gap_source_present = False

    manifest = build_replica_manifest(
        source_release_sha=release_sha,
        canonical_db=db_target,
        paper_state=state_target if state_present else None,
        paper_gap_spool=gap_target if gap_target.exists() else None,
        generation_id=generation_id,
        snapshot_created_at_utc=now,
    )
    manifest["paper_gap_source_present"] = gap_source_present
    write_replica_manifest(manifest, staging / MANIFEST_FILENAME)

    # The staging work directory is owned by this attempt; remove it so a
    # failed export cannot accumulate partial snapshots.
    shutil.rmtree(work, ignore_errors=True)
    return manifest


def install_replica_generation(
    *,
    staging_dir: Path,
    host_root: Path,
    expected_source_release_sha: str,
    now: datetime | None = None,
    max_age_seconds: int = REPLICA_FRESHNESS_SECONDS,
    retain: int = RETAINED_GENERATIONS,
) -> dict[str, Any]:
    """Validate a staged bundle and publish it as the new current generation.

    Order is deliberate: validate, install under a private directory, re-verify,
    atomically publish a previously absent generation name, then flip the
    pointer. A failure at any point leaves the existing ``current`` generation
    untouched, and a reader can never observe a partial generation.

    A previously installed generation id is immutable. Reinstalling the exact
    same manifest is idempotent and reuses the verified directory; attempting to
    reuse the id for different content is a provenance collision and fails
    closed. In particular, an active generation is never deleted in place.

    Returns a summary dict. Raises on any unprovable condition.
    """
    staged = Path(staging_dir)
    root = Path(host_root)
    generations = host_generations_dir(root)
    pointer = host_current_pointer(root)

    manifest = read_replica_manifest(staged / MANIFEST_FILENAME)
    verified = verify_replica_manifest(
        manifest,
        root=staged,
        expected_source_release_sha=expected_source_release_sha,
        now=now,
        max_age_seconds=max_age_seconds,
    )

    generation_id = verified.generation_id
    final = generations / generation_id
    generations.mkdir(parents=True, exist_ok=True)

    if final.exists():
        # Generation names are immutable namespace keys. An exact retry may
        # reuse the already verified directory, but a different manifest under
        # the same id is a collision and must never overwrite known-good data.
        existing_manifest = read_replica_manifest(final / MANIFEST_FILENAME)
        if existing_manifest != manifest:
            raise ReplicaProvenanceError(
                REASON_GENERATION_ID_COLLISION,
                f"generation id {generation_id!r} already exists with different content",
            )
        verify_replica_manifest(
            existing_manifest,
            root=final,
            expected_source_release_sha=expected_source_release_sha,
            now=now,
            max_age_seconds=max_age_seconds,
        )
    else:
        private = generations / f".{generation_id}.install.{os.getpid()}.{uuid.uuid4().hex}"
        try:
            shutil.copytree(staged, private)

            # Re-verify the private copy before its generation name becomes
            # visible. If copy or verification fails, current and all published
            # generation directories are unchanged.
            verify_replica_manifest(
                read_replica_manifest(private / MANIFEST_FILENAME),
                root=private,
                expected_source_release_sha=expected_source_release_sha,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            os.replace(private, final)
        except Exception:
            shutil.rmtree(private, ignore_errors=True)
            raise
        # The generation directory entry must be durable before it is named as
        # the current generation.
        fsync_directory_required(generations)

    # The pointer is a small atomically-replaced file naming the generation,
    # not a symlink: ``os.replace`` on a symlink is not portable, and a plain
    # file is auditable and needs no symlink privilege. Readers resolve it to a
    # concrete immutable generation directory.
    temp_pointer = root / f".{HOST_CURRENT_POINTER}.tmp.{os.getpid()}"
    try:
        if temp_pointer.exists():
            temp_pointer.unlink()
        temp_pointer.write_text(generation_id + "\n", encoding="utf-8")
        # Required durability before activation. ``os.replace`` is atomic, but a
        # rename re-points a directory entry; it does not make the file's
        # *contents* durable. Without this flush a crash could leave ``current``
        # present but empty or partial, so install would have reported a
        # generation as current while the pointer naming it is unreadable. This
        # mirrors the PR-A0 publication sequence for the canonical manifest.
        fsync_file_required(temp_pointer)
        os.replace(temp_pointer, pointer)
    except Exception:
        try:
            temp_pointer.unlink()
        except OSError:
            pass
        raise
    # The pointer rename is what makes a generation current, so its directory
    # entry must be durable before install reports success.
    fsync_directory_required(root)

    pruned = _prune_generations(generations, keep=retain, active=generation_id)

    return {
        "installed": generation_id,
        "source_release_sha": verified.source_release_sha,
        "snapshot_created_at_utc": iso_z(verified.snapshot_created_at_utc),
        "pruned": pruned,
    }


def resolve_current_generation(host_root: Path) -> Path:
    """Resolve the committed ``current`` generation directory.

    Raises when the pointer is missing, malformed, or names a generation that is
    not installed - so a job never binds a staging directory or the writable
    parent repository.
    """
    root = Path(host_root)
    pointer = host_current_pointer(root)
    if not pointer.exists():
        raise ReplicaUnavailableError(
            REASON_MANIFEST_MISSING, f"no current replica generation at {pointer}"
        )
    try:
        generation_id = require_generation_id(pointer.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReplicaUnavailableError(REASON_MANIFEST_MISSING, str(exc)) from exc

    resolved = host_generations_dir(root) / generation_id
    if not resolved.is_dir():
        raise ReplicaUnavailableError(
            REASON_MANIFEST_MISSING,
            f"current pointer names an uninstalled generation: {generation_id}",
        )
    return resolved


def _prune_generations(generations: Path, *, keep: int, active: str) -> list[str]:
    """Remove old generations, never the active one. Bounded retention."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if not generations.is_dir():
        return []
    entries = sorted(
        (p for p in generations.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    keep_names = {active}
    for entry in entries:
        if len(keep_names) >= keep:
            break
        keep_names.add(entry.name)
    removed: list[str] = []
    for entry in entries:
        if entry.name in keep_names:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed


# ---------------------------------------------------------------------------
# CLI seam
#
# The shell scripts call these instead of reimplementing SQLite or hash
# validation. Keeping the logic in Python means one implementation is proven
# once and reused by both planes.
# ---------------------------------------------------------------------------


def _cmd_export(args: argparse.Namespace) -> int:
    manifest = export_replica_bundle(
        source_db=Path(args.source_db),
        staging_dir=Path(args.staging),
        source_release_sha=args.release_sha,
        paper_state_source=Path(args.paper_state) if args.paper_state else None,
        paper_gap_source=Path(args.paper_gap) if args.paper_gap else None,
        backup_work_dir=Path(args.backup_work_dir) if args.backup_work_dir else None,
    )
    print(json.dumps(manifest, sort_keys=True))
    print("O'Pip canonical replica export: OK")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    verified = verify_installed_replica(
        expected_source_release_sha=args.release_sha,
        root=Path(args.root),
        max_age_seconds=int(args.max_age_seconds),
    )
    supports, reasons = replica_supports_completeness(verified)
    payload = {
        "generation_id": verified.generation_id,
        "source_release_sha": verified.source_release_sha,
        "snapshot_created_at_utc": iso_z(verified.snapshot_created_at_utc),
        "completeness_supported": supports,
        "completeness_reasons": list(reasons),
        "freshness_bound_seconds": int(args.max_age_seconds),
    }
    print(json.dumps(payload, sort_keys=True))
    print("O'Pip canonical replica verify: OK")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    summary = install_replica_generation(
        staging_dir=Path(args.staging),
        host_root=Path(args.host_root),
        expected_source_release_sha=args.release_sha,
        max_age_seconds=int(args.max_age_seconds),
    )
    print(json.dumps(summary, sort_keys=True))
    print("O'Pip canonical replica install: OK")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    print(resolve_current_generation(Path(args.host_root)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="canonical_replica",
        description="Canonical learning replica bridge (copy-only, read-only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="stage a replica bundle on production")
    export.add_argument("--source-db", required=True)
    export.add_argument("--staging", required=True)
    export.add_argument("--release-sha", required=True)
    export.add_argument("--paper-state", default="")
    export.add_argument("--paper-gap", default="")
    export.add_argument("--backup-work-dir", default="")
    export.set_defaults(func=_cmd_export)

    verify = sub.add_parser("verify", help="verify an installed replica bundle")
    verify.add_argument("--root", required=True)
    verify.add_argument("--release-sha", required=True)
    verify.add_argument("--max-age-seconds", default=str(REPLICA_FRESHNESS_SECONDS))
    verify.set_defaults(func=_cmd_verify)

    install = sub.add_parser("install", help="validate and publish a generation")
    install.add_argument("--staging", required=True)
    install.add_argument("--host-root", required=True)
    install.add_argument("--release-sha", required=True)
    install.add_argument("--max-age-seconds", default=str(REPLICA_FRESHNESS_SECONDS))
    install.set_defaults(func=_cmd_install)

    resolve = sub.add_parser("resolve", help="print the committed current generation")
    resolve.add_argument("--host-root", required=True)
    resolve.set_defaults(func=_cmd_resolve)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ReplicaVerificationError as exc:
        print(f"canonical replica refused: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANONICAL_RELATIVE",
    "CanonicalLearningReplicaManifest",
    "DEFAULT_REPLICA_ROOT",
    "HOST_CURRENT_POINTER",
    "HOST_GENERATIONS_DIRNAME",
    "MANIFEST_FILENAME",
    "PAPER_GAP_RELATIVE",
    "PAPER_STATE_RELATIVE",
    "REASON_DB_HASH_MISMATCH",
    "REASON_DB_MISSING",
    "REASON_DB_NOT_SELF_CONTAINED",
    "REASON_DB_SIZE_MISMATCH",
    "REASON_GENERATION_ID_COLLISION",
    "REASON_GENERATION_ID_INVALID",
    "REASON_MANIFEST_MALFORMED",
    "REASON_MANIFEST_MISSING",
    "REASON_PAPER_GAP_MISMATCH",
    "REASON_PAPER_STATE_ABSENT",
    "REASON_PAPER_STATE_MISMATCH",
    "REASON_PAPER_STATE_MISSING",
    "REASON_SCHEMA_UNSUPPORTED",
    "REASON_SOURCE_SHA_MISMATCH",
    "REASON_STALE",
    "REASON_TIMESTAMP_INVALID",
    "REPLICA_FRESHNESS_SECONDS",
    "REPLICA_MARKER_KEY",
    "REPLICA_SCHEMA_VERSION",
    "RETAINED_GENERATIONS",
    "ReplicaArtifact",
    "ReplicaProvenanceError",
    "ReplicaStaleError",
    "ReplicaUnavailableError",
    "ReplicaVerificationError",
    "SnapshotFacts",
    "VerifiedReplicaBundle",
    "build_replica_manifest",
    "export_replica_bundle",
    "host_current_pointer",
    "host_generations_dir",
    "install_replica_generation",
    "iso_z",
    "read_replica_manifest",
    "read_snapshot_facts",
    "replica_db_path",
    "replica_manifest_path",
    "replica_paper_gap_path",
    "replica_paper_state_path",
    "replica_root",
    "replica_supports_completeness",
    "require_generation_id",
    "resolve_current_generation",
    "resolve_verified_replica_bundle",
    "verify_installed_replica",
    "verify_replica_manifest",
    "write_replica_manifest",
]
