"""Read-only canonical learning replica contract and verification.

The production canonical SQLite store is the authority for terminal paper
outcomes. The learning plane needs that evidence to run readiness and learning,
but it must never become a second authority and must never reach back into
production to read it.

This module defines the contract for the **copy-only replica** that bridges the
two planes, and verifies an installed replica before any learning code is
allowed to treat it as usable authority.

Boundary rules:

* The replica is authoritative for **nothing**. Production remains the only
  canonical writer.
* Verification is provenance-first: a replica is unusable unless it can be tied
  to one exact production release SHA, hashes to its recorded bytes, is
  sidecar-free and self-contained, and is not staler than the documented
  freshness bound.
* Every failure mode resolves to *unavailable* or *incomplete*, never to
  *complete*. That direction matters: it is always safe to distrust a replica,
  and never safe to trust one that cannot be proven.
* A legitimately empty outcome stream is not a failure and is not decided here.
  Provenance and emptiness are separate questions; the canonical outcome reader
  owns the latter.

The companion artifacts exist because completeness cannot be decided from the
database alone. ``paper_trading/state.json`` carries delivery state (COMMITTED /
PENDING / PERMANENT_FAILURE) and ``paper_trading/evidence_gap_spool.json``
carries unresolved evidence gaps. A replica missing either one cannot support a
complete-population claim, so both are bound into the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from app.opip.canonical.backup import (
    BackupFormatError,
    BackupProvenanceError,
    assert_rollback_journal_backup,
    assert_sidecar_free,
    hash_file_sha256,
    require_release_sha,
)
from app.opip.canonical.paths import canonical_dir

REPLICA_CONTRACT_VERSION = 1

#: Marker key carried additively in the schema-4 export manifest. Older workers
#: ignore unknown keys, so its presence does not break the established
#: ``production first, learning second`` release order.
REPLICA_MARKER_KEY = "canonical_learning_replica_version"

MANIFEST_FILENAME = "learning_replica_manifest.json"
CANONICAL_DB_FILENAME = "opip_canonical_v1.sqlite3"

#: The production export runs every 2 minutes and the learning sync timer runs
#: every 2 minutes offset from it, so a healthy bridge refreshes the replica at
#: least twice every 4 minutes. 30 minutes is roughly 15 export cycles: far
#: enough above the cadence to tolerate transient sync failures without
#: flapping, and far enough below a daily window to notice a bridge that has
#: genuinely stopped. Chosen explicitly rather than inherited, because no
#: existing threshold applies to this artifact (the dashboard freshness policy
#: in ``data_platform/freshness.py`` governs a different plane and a
#: 120s/300s scale that would be unusable here).
DEFAULT_REPLICA_FRESHNESS_SECONDS = 1800

REASON_MANIFEST_MISSING = "CANONICAL_REPLICA_MANIFEST_MISSING"
REASON_MANIFEST_MALFORMED = "CANONICAL_REPLICA_MANIFEST_MALFORMED"
REASON_CONTRACT_UNSUPPORTED = "CANONICAL_REPLICA_CONTRACT_UNSUPPORTED"
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

    contract_version: int
    source_release_sha: str
    snapshot_created_at_utc: datetime
    canonical: ReplicaArtifact
    paper_state: ReplicaArtifact
    paper_gap_spool: ReplicaArtifact
    history_epoch: int | None
    next_local_sequence: int | None
    max_local_sequence: int | None
    event_count: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "source_release_sha": self.source_release_sha,
            "snapshot_created_at_utc": iso_z(self.snapshot_created_at_utc),
            "canonical": self.canonical.as_dict(),
            "paper_state": self.paper_state.as_dict(),
            "paper_gap_spool": self.paper_gap_spool.as_dict(),
            "history_epoch": self.history_epoch,
            "next_local_sequence": self.next_local_sequence,
            "max_local_sequence": self.max_local_sequence,
            "event_count": self.event_count,
        }


def iso_z(value: datetime) -> str:
    """Render a UTC timestamp in the ``Z`` form used across evidence ids."""
    moment = value.astimezone(timezone.utc)
    if moment.microsecond == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}Z"


def replica_manifest_path(directory: Path | None = None) -> Path:
    return (Path(directory) if directory is not None else canonical_dir()) / MANIFEST_FILENAME


def replica_db_path(directory: Path | None = None) -> Path:
    return (Path(directory) if directory is not None else canonical_dir()) / CANONICAL_DB_FILENAME


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
    snapshot_created_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build the replica manifest from the exported artifacts.

    ``source_release_sha`` must be the exact production release SHA; an empty or
    malformed value raises rather than producing an unattributable manifest.
    The caller is responsible for having produced ``canonical_db`` via the
    canonical online-backup generation path, not a raw file copy.
    """
    release_sha = require_release_sha(source_release_sha, field_name="source_release_sha")
    created = snapshot_created_at_utc or datetime.now(timezone.utc)

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
        raise ReplicaProvenanceError(
            REASON_DB_NOT_SELF_CONTAINED, str(exc)
        ) from exc

    manifest: dict[str, Any] = {
        "schema_version": REPLICA_CONTRACT_VERSION,
        "contract_version": REPLICA_CONTRACT_VERSION,
        "source_release_sha": release_sha,
        "snapshot_created_at_utc": iso_z(created),
        "canonical": _artifact_facts(canonical_db),
        "paper_state": _artifact_facts(paper_state)
        if paper_state is not None
        else {"present": False, "sha256": None, "size_bytes": None},
        "paper_gap_spool": _artifact_facts(paper_gap_spool)
        if paper_gap_spool is not None
        else {"present": False, "sha256": None, "size_bytes": None},
        "journal_mode": "delete",
        "self_contained": True,
    }
    return manifest


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


def _parse_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReplicaProvenanceError(
            REASON_TIMESTAMP_INVALID, f"{field_name} is missing"
        )
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

    * **Recorded absent** (``present: false``) is a positive exporter
      statement. It is returned as ``present=False`` rather than raised, so a
      legitimately absent companion is distinguishable from a lost one. Whether
      absence is *acceptable* is a completeness question for the caller, not a
      provenance question for this function.
    * **Recorded present but not installed** is unavailability, and raises.
      This is the case §9 forbids treating as "no lifecycles".
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
    replica_directory: Path | None = None,
    expected_source_release_sha: str,
    paper_state_path: Path | None = None,
    paper_gap_spool_path: Path | None = None,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_REPLICA_FRESHNESS_SECONDS,
) -> CanonicalLearningReplicaManifest:
    """Verify an installed replica, raising on any unprovable condition.

    ``expected_source_release_sha`` is the production release the learning
    worker believes is deployed. Making it a required argument is deliberate:
    a replica can never be accepted merely because it looks internally
    consistent with itself.
    """
    if not isinstance(manifest, Mapping):
        raise ReplicaProvenanceError(REASON_MANIFEST_MALFORMED, "manifest must be a mapping")

    contract = manifest.get("contract_version")
    if type(contract) is not int or contract != REPLICA_CONTRACT_VERSION:
        raise ReplicaProvenanceError(
            REASON_CONTRACT_UNSUPPORTED,
            f"contract_version={contract!r} unsupported by this build",
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

    created = _parse_utc(manifest.get("snapshot_created_at_utc"), field_name="snapshot_created_at_utc")

    directory = Path(replica_directory) if replica_directory is not None else canonical_dir()
    db_path = replica_db_path(directory)

    canonical = _verify_artifact(
        manifest.get("canonical"),
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

    paper_state = _verify_artifact(
        manifest.get("paper_state"),
        Path(paper_state_path) if paper_state_path is not None else Path("/app/data/paper_trading/state.json"),
        mismatch_reason=REASON_PAPER_STATE_MISMATCH,
        missing_reason=REASON_PAPER_STATE_MISSING,
    )
    gap_spool = _verify_artifact(
        manifest.get("paper_gap_spool"),
        Path(paper_gap_spool_path)
        if paper_gap_spool_path is not None
        else Path("/app/data/paper_trading/evidence_gap_spool.json"),
        mismatch_reason=REASON_PAPER_GAP_MISMATCH,
        missing_reason=None,
    )

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    age = (moment - created).total_seconds()
    if age > max_age_seconds:
        raise ReplicaStaleError(
            REASON_STALE,
            f"replica is {int(age)}s old, limit {max_age_seconds}s",
        )
    if age < -max_age_seconds:
        # A replica stamped well into the future is a provenance defect, not
        # freshness: it cannot have been produced by this production release.
        raise ReplicaProvenanceError(
            REASON_TIMESTAMP_INVALID,
            "snapshot timestamp is implausibly in the future",
        )

    def _int_or_none(value: object) -> int | None:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    return CanonicalLearningReplicaManifest(
        contract_version=contract,
        source_release_sha=expected_sha,
        snapshot_created_at_utc=created,
        canonical=canonical,
        paper_state=paper_state,
        paper_gap_spool=gap_spool,
        history_epoch=_int_or_none(manifest.get("history_epoch")),
        next_local_sequence=_int_or_none(manifest.get("next_local_sequence")),
        max_local_sequence=_int_or_none(manifest.get("max_local_sequence")),
        event_count=_int_or_none(manifest.get("event_count")),
    )


def replica_supports_completeness(verified: CanonicalLearningReplicaManifest) -> tuple[bool, tuple[str, ...]]:
    """Whether a verified replica can support a complete-population claim.

    Verification establishes *provenance*. This establishes *sufficiency*, and
    it is deliberately a separate step so neither concern can be satisfied by
    the other.

    A replica whose bundled paper lifecycle state is absent cannot prove outbox
    completeness. That is exactly the condition §9 forbids treating as "all
    outboxes complete": absence is certified in the manifest, so it is not
    silent, but it is still insufficient for supervised truth.

    The gap spool is treated asymmetrically on purpose. Its legitimate absence
    means zero unresolved gaps, which production semantics already accept, so it
    does not block completeness - but a *recorded present yet missing* spool is
    already unavailability and never reaches here.
    """
    reasons: list[str] = []
    if not verified.paper_state.present:
        reasons.append("CANONICAL_REPLICA_PAPER_STATE_ABSENT")
    return (not reasons, tuple(reasons))


def verify_installed_replica(
    *,
    expected_source_release_sha: str,
    replica_directory: Path | None = None,
    paper_state_path: Path | None = None,
    paper_gap_spool_path: Path | None = None,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_REPLICA_FRESHNESS_SECONDS,
) -> CanonicalLearningReplicaManifest:
    """Convenience entry point: read then verify the installed replica."""
    directory = Path(replica_directory) if replica_directory is not None else canonical_dir()
    manifest = read_replica_manifest(replica_manifest_path(directory))
    return verify_replica_manifest(
        manifest,
        replica_directory=directory,
        expected_source_release_sha=expected_source_release_sha,
        paper_state_path=paper_state_path,
        paper_gap_spool_path=paper_gap_spool_path,
        now=now,
        max_age_seconds=max_age_seconds,
    )


__all__ = [
    "CANONICAL_DB_FILENAME",
    "CanonicalLearningReplicaManifest",
    "DEFAULT_REPLICA_FRESHNESS_SECONDS",
    "MANIFEST_FILENAME",
    "REASON_CONTRACT_UNSUPPORTED",
    "REASON_DB_HASH_MISMATCH",
    "REASON_DB_MISSING",
    "REASON_DB_NOT_SELF_CONTAINED",
    "REASON_DB_SIZE_MISMATCH",
    "REASON_MANIFEST_MALFORMED",
    "REASON_MANIFEST_MISSING",
    "REASON_PAPER_GAP_MISMATCH",
    "REASON_PAPER_STATE_MISMATCH",
    "REASON_PAPER_STATE_MISSING",
    "REASON_SOURCE_SHA_MISMATCH",
    "REASON_STALE",
    "REASON_TIMESTAMP_INVALID",
    "REPLICA_CONTRACT_VERSION",
    "REPLICA_MARKER_KEY",
    "ReplicaArtifact",
    "ReplicaProvenanceError",
    "ReplicaStaleError",
    "ReplicaUnavailableError",
    "ReplicaVerificationError",
    "build_replica_manifest",
    "read_replica_manifest",
    "replica_db_path",
    "replica_manifest_path",
    "replica_supports_completeness",
    "verify_installed_replica",
    "verify_replica_manifest",
]
