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
import re
import shutil
import sqlite3
import sys
import time
from typing import Any, Callable, Iterable, Mapping
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
from app.opip.contracts.temporal import TemporalIntegrityError, require_utc
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


def _require_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    """Require a timezone-aware datetime, normalized to UTC.

    Replica evidence timestamps and freshness arithmetic are defined in UTC, so a
    naive datetime must be rejected rather than silently interpreted. Python
    resolves a naive ``datetime`` against the host local timezone, which would
    quietly shift a recorded release/freshness timestamp by the host offset - and
    could even make a stale replica look fresh.

    The awareness invariant is delegated to the shared ``require_utc`` primitive
    rather than restated here; only the error translation is local, so callers
    keep the stable replica provenance reason code.
    """
    try:
        return require_utc(value, field_name=field_name)
    except TemporalIntegrityError as exc:
        raise ReplicaProvenanceError(REASON_TIMESTAMP_INVALID, str(exc)) from exc


def iso_z(value: datetime, *, field_name: str = "snapshot_created_at_utc") -> str:
    """Render a UTC timestamp in the ``Z`` form used across evidence ids.

    A naive datetime raises rather than being localized to the host timezone.
    """
    moment = _require_utc_datetime(value, field_name=field_name)
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
    # An externally supplied timestamp must be timezone-aware; only the
    # internally created clock below is allowed to be naive-by-construction.
    created = (
        _require_utc_datetime(
            snapshot_created_at_utc, field_name="snapshot_created_at_utc"
        )
        if snapshot_created_at_utc is not None
        else datetime.now(timezone.utc)
    )
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
    # One awareness invariant for the whole module: parse, then delegate.
    return _require_utc_datetime(parsed, field_name=field_name)


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

    # The verification clock is externally supplied, so it must be aware. A naive
    # value would be resolved against the host timezone and could turn a stale
    # replica into a freshness pass.
    moment = (
        _require_utc_datetime(now, field_name="now")
        if now is not None
        else datetime.now(timezone.utc)
    )
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
        # The generation is already published under its immutable name, and the
        # normal post-success prune will not run. Reconcile against the committed
        # pointer so repeated pre-rename failures cannot accumulate generations.
        _reconcile_retention_quietly(root, keep=retain)
        raise
    # The pointer rename is what makes a generation current, so its directory
    # entry must be durable before install reports success.
    try:
        fsync_directory_required(root)
    except Exception:
        # The rename already happened, so ``current`` may already name this
        # generation even though durability failed. Reconcile reads the pointer
        # from disk and protects whatever it names, so this cannot delete a
        # referenced generation; it only bounds orphans.
        _reconcile_retention_quietly(root, keep=retain)
        raise

    pruned = _prune_generations(generations, keep=retain, protected={generation_id})

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


def _owned_generation_dirs(generations: Path) -> list[Path]:
    """Owned generation directories, newest first.

    A directory is treated as an owned generation only when it holds a manifest:
    the manifest is what makes it a generation rather than a stray directory that
    happens to sit under the replica root. Retention therefore never removes
    something it cannot positively identify, and it never considers the
    dot-prefixed private staging directories an install publishes through.
    """
    if not generations.is_dir():
        return []
    owned: list[Path] = []
    for entry in generations.iterdir():
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if not (entry / MANIFEST_FILENAME).is_file():
            continue
        try:
            owned.append(entry)
        except OSError:
            continue
    try:
        return sorted(owned, key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return owned


def _committed_generation_id(root: Path) -> tuple[str | None, bool]:
    """Best-effort read of the committed ``current`` pointer.

    Returns ``(generation_id, provable)``. Three states must stay distinct,
    because retention must never confuse "nothing is referenced" with "the
    reference could not be read":

    * ``("<id>", True)``  - the pointer names a generation;
    * ``(None, True)``    - the pointer is provably absent, so nothing is
      referenced and pruning cannot create a dangling pointer;
    * ``(None, False)``   - the pointer exists but is unreadable or empty, so the
      reference is unknown and retention must be conservative.
    """
    pointer = host_current_pointer(root)
    try:
        if not pointer.is_file():
            return None, True
        value = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None, False
    if not value:
        # An empty pointer names nothing, but it is not evidence of absence.
        return None, False
    return value, True


def _reconcile_generation_retention(root: Path, *, keep: int) -> list[str]:
    """Enforce bounded retention using the *committed* pointer, not a guess.

    This is the failure-path reconciliation. An install publishes a generation
    under its immutable final name before it touches the pointer, so any failure
    at pointer-fsync, pointer-rename or post-rename directory-fsync returns
    without the normal post-success prune ever running. Repeated failures would
    otherwise accumulate orphaned generations until the disk filled, breaking the
    bounded "active + previous known-good" contract.

    Safety comes from reading the reference *from disk* rather than trusting the
    in-flight ``generation_id``. After ``os.replace(temp_pointer, current)`` the
    live pointer can already name the incoming generation even though directory
    durability failed, so deleting it because "the install failed" would leave a
    dangling pointer. Protecting whatever the pointer actually names is what makes
    this safe to run on every failure, and running it on every retry is what keeps
    storage bounded.
    """
    generations = host_generations_dir(root)
    active, provable = _committed_generation_id(root)
    protected: set[str] = {active} if active else set()
    # An unprovable reference state is never treated as "unreferenced", so retain
    # one extra generation as margin before removing anything.
    floor = keep if provable else keep + 1
    return _prune_generations(generations, keep=floor, protected=protected)


def _reconcile_retention_quietly(root: Path, *, keep: int) -> None:
    """Best-effort retention reconciliation that never masks the real failure."""
    try:
        _reconcile_generation_retention(root, keep=keep)
    except OSError:
        pass


def _prune_generations(
    generations: Path, *, keep: int, protected: Iterable[str] = ()
) -> list[str]:
    """Remove old owned generations, never a protected one. Bounded retention.

    ``protected`` names generations that must survive regardless of the bound -
    normally the one the committed pointer references. The remaining slots are
    filled with the most recent generations up to ``keep``. Only directories
    positively identified as generations are candidates, so an unrecognized
    directory is left untouched.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    owned = _owned_generation_dirs(generations)
    keep_names = {name for name in protected if name}
    for entry in owned:
        if len(keep_names) >= keep:
            break
        keep_names.add(entry.name)
    removed: list[str] = []
    for entry in owned:
        if entry.name in keep_names:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
    return removed


# ---------------------------------------------------------------------------
# Fresh committed-export proof (deploy-side gate)
#
# A cron/export lock skip is *valid* exporter behaviour and returns success, but
# it is **not** evidence that the deployed release is exported. The deploy must
# therefore prove a committed export for the exact target SHA from durable
# artifacts, never from an exit code.
# ---------------------------------------------------------------------------

#: Filename of the production export manifest that acts as the generation commit
#: marker for the learning export root.
MANIFEST_ENV_FILENAME = "manifest.env"

#: Content-addressed replica directory name produced by the exporter:
#: ``canonical_learning_replica.<tree sha256>``.
EXPORT_REPLICA_DIR_PATTERN = re.compile(r"^canonical_learning_replica\.[0-9a-f]{64}$")

#: Deploy-time freshness bound for the committed export. Derived from the export
#: schedule (2 minutes + 40s offset, so ~160s per cycle) with margin: two full
#: cycles plus slack, so one missed tick does not flap the gate while a genuinely
#: stale manifest still fails. Deliberately tighter than the 1800s replica
#: contract, which governs learning consumption rather than deploy proof.
EXPORT_PROOF_FRESHNESS_SECONDS = 600

#: Bounded wait for a committed target-SHA export. Also derived from the cadence
#: (two cycles plus slack) so a skipped-due-to-lock run has a chance to be
#: superseded by the next scheduled run, and so the wait stays well inside the
#: deploy's SSH keepalive budget. Never unbounded.
EXPORT_PROOF_WAIT_SECONDS = 360
EXPORT_PROOF_POLL_INTERVAL_SECONDS = 10.0

REASON_EXPORT_MANIFEST_MISSING = "LEARNING_EXPORT_MANIFEST_MISSING"
REASON_EXPORT_MANIFEST_MALFORMED = "LEARNING_EXPORT_MANIFEST_MALFORMED"
REASON_EXPORT_RELEASE_SHA_MISMATCH = "LEARNING_EXPORT_RELEASE_SHA_MISMATCH"
REASON_EXPORT_TIMESTAMP_INVALID = "LEARNING_EXPORT_TIMESTAMP_INVALID"
REASON_EXPORT_STALE = "LEARNING_EXPORT_STALE"
REASON_EXPORT_REPLICA_MARKER_MISSING = "LEARNING_EXPORT_REPLICA_MARKER_MISSING"
REASON_EXPORT_REPLICA_DIR_INVALID = "LEARNING_EXPORT_REPLICA_DIR_INVALID"
REASON_EXPORT_REPLICA_MISMATCH = "LEARNING_EXPORT_REPLICA_PROVENANCE_MISMATCH"
REASON_EXPORT_READY = "LEARNING_EXPORT_READY"


@dataclass(frozen=True)
class ExportReadiness:
    """Whether a committed export proves the target release is exported."""

    ready: bool
    reason: str
    detail: str
    facts: dict[str, Any]
    attempts: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "detail": self.detail,
            "attempts": self.attempts,
            **self.facts,
        }


def read_export_manifest(path: Path) -> dict[str, str]:
    """Parse the production export manifest (``KEY=VALUE`` lines)."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReplicaUnavailableError(
            REASON_EXPORT_MANIFEST_MISSING,
            f"export manifest unavailable at {target}: {exc}",
        ) from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _evaluate_committed_export(
    *,
    export_root: Path,
    expected_source_release_sha: str,
    moment: datetime,
    max_age_seconds: int,
) -> tuple[bool, str, str, dict[str, Any]]:
    """One evaluation of the committed export. Never raises for a bad state."""
    facts: dict[str, Any] = {"export_root": str(export_root)}
    manifest_path = export_root / MANIFEST_ENV_FILENAME
    try:
        manifest = read_export_manifest(manifest_path)
    except ReplicaUnavailableError as exc:
        return False, exc.reason, str(exc), facts

    facts["manifest_path"] = str(manifest_path)
    facts["manifest_production_deployed_sha"] = manifest.get("production_deployed_sha", "")
    facts["manifest_exported_at_utc"] = manifest.get("exported_at_utc", "")
    facts["manifest_replica_marker"] = manifest.get("canonical_learning_replica_version", "")
    facts["manifest_replica_dir"] = manifest.get("canonical_learning_replica_dir", "")

    # 1. The manifest must name the exact release being deployed. This is the
    #    check a lock-skip cannot satisfy on its own.
    recorded_sha = manifest.get("production_deployed_sha", "")
    if recorded_sha != expected_source_release_sha:
        return (
            False,
            REASON_EXPORT_RELEASE_SHA_MISMATCH,
            f"committed export is for {recorded_sha or 'no recorded SHA'}, "
            f"expected {expected_source_release_sha}",
            facts,
        )

    # 2. The commit timestamp must be present, timezone-aware and fresh.
    raw_stamp = manifest.get("exported_at_utc", "")
    if not raw_stamp:
        return (
            False,
            REASON_EXPORT_MANIFEST_MALFORMED,
            "committed export has no exported_at_utc",
            facts,
        )
    try:
        exported_at = _parse_utc(raw_stamp, field_name="exported_at_utc")
    except ReplicaProvenanceError as exc:
        return False, REASON_EXPORT_TIMESTAMP_INVALID, str(exc), facts
    age = (moment - exported_at).total_seconds()
    facts["export_age_seconds"] = int(age)
    facts["export_freshness_bound_seconds"] = int(max_age_seconds)
    if age > max_age_seconds or age < -max_age_seconds:
        return (
            False,
            REASON_EXPORT_STALE,
            f"committed export age {int(age)}s exceeds bound {max_age_seconds}s",
            facts,
        )

    # 3. The additive replica marker must be present and versioned.
    marker = manifest.get("canonical_learning_replica_version", "")
    if marker != "1":
        return (
            False,
            REASON_EXPORT_REPLICA_MARKER_MISSING,
            f"committed export has no canonical_learning_replica_version=1 (got {marker!r})",
            facts,
        )

    # 4. The referenced directory must be a real, contained, content-addressed
    #    directory. A valid-looking *name* is not sufficient: `is_dir()` follows
    #    symlinks, so the resolved path is re-verified beneath the export root.
    dir_name = manifest.get("canonical_learning_replica_dir", "")
    if not EXPORT_REPLICA_DIR_PATTERN.match(dir_name or ""):
        return (
            False,
            REASON_EXPORT_REPLICA_DIR_INVALID,
            f"canonical_learning_replica_dir is not content-addressed: {dir_name!r}",
            facts,
        )
    replica_dir = export_root / dir_name
    if replica_dir.is_symlink():
        return (
            False,
            REASON_EXPORT_REPLICA_DIR_INVALID,
            f"referenced replica directory is a symlink: {replica_dir}",
            facts,
        )
    if not replica_dir.is_dir():
        return (
            False,
            REASON_EXPORT_REPLICA_DIR_INVALID,
            f"referenced replica directory is missing: {replica_dir}",
            facts,
        )
    resolved_dir = Path(os.path.realpath(replica_dir))
    resolved_root = Path(os.path.realpath(export_root))
    if resolved_dir != resolved_root and resolved_root not in resolved_dir.parents:
        return (
            False,
            REASON_EXPORT_REPLICA_DIR_INVALID,
            f"referenced replica directory escapes the export root: {resolved_dir}",
            facts,
        )

    # 5. The inner replica manifest is the real provenance contract: it proves
    #    hashes, release binding and snapshot freshness for the immutable
    #    generation the manifest points at.
    try:
        inner = read_replica_manifest(replica_dir / MANIFEST_FILENAME)
        verified = verify_replica_manifest(
            inner,
            root=replica_dir,
            expected_source_release_sha=expected_source_release_sha,
            now=moment,
            max_age_seconds=REPLICA_FRESHNESS_SECONDS,
        )
    except ReplicaVerificationError as exc:
        return (
            False,
            REASON_EXPORT_REPLICA_MISMATCH,
            f"inner replica manifest did not verify: {exc}",
            facts,
        )
    facts["replica_generation_id"] = verified.generation_id
    facts["replica_source_release_sha"] = verified.source_release_sha
    facts["replica_snapshot_created_at_utc"] = iso_z(verified.snapshot_created_at_utc)
    facts["replica_completeness_supported"] = replica_supports_completeness(verified)[0]

    return True, REASON_EXPORT_READY, "committed export proves the target release", facts


def verify_committed_export(
    *,
    export_root: Path,
    expected_source_release_sha: str,
    now: datetime | None = None,
    max_age_seconds: int = EXPORT_PROOF_FRESHNESS_SECONDS,
    wait_seconds: float = 0.0,
    poll_interval_seconds: float = EXPORT_PROOF_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    clock: Callable[[], datetime] | None = None,
) -> ExportReadiness:
    """Prove that ``expected_source_release_sha`` is committed in the export.

    ``wait_seconds`` exists for one specific, legitimate case: the exporter
    returned success because the cron run already held the lock, so the committed
    manifest still names the previous release. The next scheduled run may commit
    the target, so a bounded wait is useful - but it is strictly bounded and
    never spins indefinitely.

    Time is injectable so the wait and the freshness bound are testable without
    sleeping in tests.
    """
    if wait_seconds < 0:
        raise ValueError("wait_seconds must not be negative")
    release_sha = require_release_sha(
        expected_source_release_sha, field_name="expected_source_release_sha"
    )
    root = Path(export_root)
    resolved_now = _require_utc_datetime(now, field_name="now") if now is not None else None

    started = monotonic()
    attempts = 0
    readiness: ExportReadiness | None = None
    while True:
        attempts += 1
        moment = resolved_now if resolved_now is not None else (clock or _utc_now)()
        ready, reason, detail, facts = _evaluate_committed_export(
            export_root=root,
            expected_source_release_sha=release_sha,
            moment=moment,
            max_age_seconds=max_age_seconds,
        )
        readiness = ExportReadiness(
            ready=ready, reason=reason, detail=detail, facts=facts, attempts=attempts
        )
        if ready:
            return readiness
        elapsed = monotonic() - started
        remaining = wait_seconds - elapsed
        if remaining <= 0:
            return readiness
        sleep(min(poll_interval_seconds, remaining))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cmd_verify_export(args: argparse.Namespace) -> int:
    readiness = verify_committed_export(
        export_root=Path(args.root),
        expected_source_release_sha=args.release_sha,
        max_age_seconds=int(args.max_age_seconds),
        wait_seconds=float(args.wait_seconds),
    )
    print(json.dumps(readiness.as_dict(), sort_keys=True))
    print(f"OPIP_LEARNING_READINESS={'READY' if readiness.ready else 'BLOCKED'}")
    print(f"OPIP_LEARNING_READINESS_REASON={readiness.reason}")
    print(f"OPIP_LEARNING_EXPORT_ATTEMPTS={readiness.attempts}")
    if readiness.ready:
        print("O'Pip learning export readiness: READY")
        return 0
    # Non-zero so the deploy cannot mistake this for a proven export; the caller
    # reads it in an `if` so the core deployment is never aborted by it.
    print(
        f"O'Pip learning export readiness: BLOCKED ({readiness.reason})",
        file=sys.stderr,
    )
    print(f"  {readiness.detail}", file=sys.stderr)
    return 3


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

    verify_export = sub.add_parser(
        "verify-export",
        help="prove a committed export names the target release (deploy gate)",
    )
    verify_export.add_argument("--root", required=True)
    verify_export.add_argument("--release-sha", required=True)
    verify_export.add_argument(
        "--max-age-seconds", default=str(EXPORT_PROOF_FRESHNESS_SECONDS)
    )
    verify_export.add_argument(
        "--wait-seconds",
        default="0",
        help=(
            "bounded wait for a committed target-SHA export, for the case where "
            "the exporter returned success because a cron run held the lock"
        ),
    )
    verify_export.set_defaults(func=_cmd_verify_export)

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
    "EXPORT_PROOF_FRESHNESS_SECONDS",
    "EXPORT_PROOF_WAIT_SECONDS",
    "EXPORT_REPLICA_DIR_PATTERN",
    "ExportReadiness",
    "HOST_CURRENT_POINTER",
    "HOST_GENERATIONS_DIRNAME",
    "MANIFEST_FILENAME",
    "PAPER_GAP_RELATIVE",
    "PAPER_STATE_RELATIVE",
    "REASON_DB_HASH_MISMATCH",
    "REASON_DB_MISSING",
    "REASON_DB_NOT_SELF_CONTAINED",
    "REASON_DB_SIZE_MISMATCH",
    "REASON_EXPORT_MANIFEST_MISSING",
    "REASON_EXPORT_READY",
    "REASON_EXPORT_RELEASE_SHA_MISMATCH",
    "REASON_EXPORT_REPLICA_DIR_INVALID",
    "REASON_EXPORT_REPLICA_MARKER_MISSING",
    "REASON_EXPORT_REPLICA_MISMATCH",
    "REASON_EXPORT_STALE",
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
    "read_export_manifest",
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
    "verify_committed_export",
    "verify_installed_replica",
    "verify_replica_manifest",
    "write_replica_manifest",
]
