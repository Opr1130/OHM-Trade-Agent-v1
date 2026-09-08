"""Learning-side legacy coverage discontinuity epoch (measurement-only).

Historical archive continuity that cannot be proven empty or complete is
recorded as an explicit discontinuity. This module never mutates production
``DATA_ROOT``, never rewrites legacy ``complete=false`` window-index state,
and never mints empty-export attestation. Trading authority is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from app.opip.learning.empty_export_attestation import (
    inspect_canonical_archive_files,
)
from app.opip.storage.bounded_jsonl import (
    ArchiveWindowSelection,
    BoundedJsonlArchive,
    sha256_file,
    write_atomic_lines,
)


COVERAGE_EPOCH_KIND = "legacy_coverage_discontinuity_v1"
COVERAGE_EPOCH_SCHEMA_VERSION = 1
COVERAGE_EPOCH_REASON = "LEGACY_ARCHIVE_CONTINUITY_UNPROVEN"
COVERAGE_DIRNAME = ".learning_coverage"
COVERAGE_EPOCH_FILENAME = "legacy_coverage_discontinuity_v1.json"
ONESHOT_CONSUMED_FILENAME = "oneshot_consumed.env"

DISPOSITION_LEGACY_COVERAGE_DISCONTINUITY = "LEGACY_COVERAGE_DISCONTINUITY"
WARNING_POST_BOUNDARY = "LEGACY_COVERAGE_DISCONTINUITY_POST_BOUNDARY"
WARNING_PRE_BOUNDARY = "LEGACY_COVERAGE_DISCONTINUITY"
# A present-but-unparseable/invalid epoch must fail closed, never silently
# revert to normal complete-history semantics (Finding 1, regression #9).
WARNING_EPOCH_INVALID = "LEGACY_COVERAGE_EPOCH_INVALID"
OUTCOME_DISPOSITION_UNRESOLVED = "UNRESOLVED_COVERAGE_DISCONTINUITY"

STATUS_ABSENT = "ABSENT"
STATUS_VALID = "VALID"
STATUS_INVALID = "INVALID"

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_EPOCH_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "archive_prefix",
        "boundary_at_utc",
        "production_deployed_sha",
        "exported_at_utc",
        "hot_bytes",
        "hot_sha256",
        "legacy_window_index_state_sha256",
        "reason",
        "measurement_only",
        "trade_authority_changed",
        "policy_change_authorized",
    }
)


@dataclass(frozen=True)
class CoverageEpoch:
    schema_version: int
    kind: str
    archive_prefix: str
    boundary_at_utc: datetime
    production_deployed_sha: str
    exported_at_utc: str
    hot_bytes: int
    hot_sha256: str
    legacy_window_index_state_sha256: str
    reason: str
    measurement_only: bool
    trade_authority_changed: bool
    policy_change_authorized: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "archive_prefix": self.archive_prefix,
            "boundary_at_utc": self.boundary_at_utc.isoformat(),
            "production_deployed_sha": self.production_deployed_sha,
            "exported_at_utc": self.exported_at_utc,
            "hot_bytes": self.hot_bytes,
            "hot_sha256": self.hot_sha256,
            "legacy_window_index_state_sha256": self.legacy_window_index_state_sha256,
            "reason": self.reason,
            "measurement_only": self.measurement_only,
            "trade_authority_changed": self.trade_authority_changed,
            "policy_change_authorized": self.policy_change_authorized,
        }


def coverage_dir(data_root: Path | str) -> Path:
    return Path(data_root) / COVERAGE_DIRNAME


def coverage_epoch_path(data_root: Path | str) -> Path:
    return coverage_dir(data_root) / COVERAGE_EPOCH_FILENAME


def oneshot_consumed_path(data_root: Path | str) -> Path:
    return coverage_dir(data_root) / ONESHOT_CONSUMED_FILENAME


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif value:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _read_manifest_env(data_root: Path) -> dict[str, str]:
    path = data_root / "manifest.env"
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip()
    return values


def archive_matches_legacy_ambiguous_hot_condition(
    archive: BoundedJsonlArchive,
) -> bool:
    """Condition C: HOT>0, no segments/manifest/signature, orphan incomplete index."""
    if not hasattr(archive, "data_file") or not hasattr(
        archive, "_window_index_state_is_orphan_incomplete_empty_without_manifest"
    ):
        return False
    try:
        facts = inspect_canonical_archive_files(archive)
    except RuntimeError:
        return False
    if facts.hot_bytes <= 0:
        return False
    if facts.segment_count != 0 or facts.manifest_present or facts.signature_present:
        return False
    return archive._window_index_state_is_orphan_incomplete_empty_without_manifest()


def legacy_window_index_state_sha256(archive: BoundedJsonlArchive) -> str:
    path = archive.window_index_state_file
    if not path.is_file():
        raise RuntimeError("legacy window index state is missing")
    return sha256_file(path)


def hot_file_sha256(archive: BoundedJsonlArchive) -> str:
    if not archive.data_file.is_file():
        raise RuntimeError("canonical hot JSONL is missing")
    return sha256_file(archive.data_file)


def parse_coverage_epoch(payload: Mapping[str, Any]) -> CoverageEpoch:
    if not isinstance(payload, Mapping):
        raise RuntimeError("coverage epoch payload is not an object")
    missing = _REQUIRED_EPOCH_KEYS - set(payload)
    if missing:
        raise RuntimeError(
            "coverage epoch missing keys: " + ",".join(sorted(missing))
        )
    if payload.get("schema_version") != COVERAGE_EPOCH_SCHEMA_VERSION:
        raise RuntimeError("coverage epoch schema_version is unsupported")
    if payload.get("kind") != COVERAGE_EPOCH_KIND:
        raise RuntimeError("coverage epoch kind is unsupported")
    prefix = str(payload.get("archive_prefix") or "").strip()
    if not prefix:
        raise RuntimeError("coverage epoch archive_prefix is empty")
    boundary = _parse_utc(payload.get("boundary_at_utc"))
    if boundary is None:
        raise RuntimeError("coverage epoch boundary_at_utc is invalid")
    production_sha = str(payload.get("production_deployed_sha") or "").strip().lower()
    if not _SHA40_RE.fullmatch(production_sha):
        raise RuntimeError("coverage epoch production_deployed_sha is invalid")
    exported_at = str(payload.get("exported_at_utc") or "").strip()
    if not exported_at or _parse_utc(exported_at) is None:
        raise RuntimeError("coverage epoch exported_at_utc is invalid")
    hot_bytes = payload.get("hot_bytes")
    if type(hot_bytes) is not int or hot_bytes <= 0:
        raise RuntimeError("coverage epoch hot_bytes is invalid")
    hot_sha = str(payload.get("hot_sha256") or "").strip().lower()
    if not _SHA256_RE.fullmatch(hot_sha):
        raise RuntimeError("coverage epoch hot_sha256 is invalid")
    legacy_sha = str(payload.get("legacy_window_index_state_sha256") or "").strip().lower()
    if not _SHA256_RE.fullmatch(legacy_sha):
        raise RuntimeError("coverage epoch legacy_window_index_state_sha256 is invalid")
    reason = str(payload.get("reason") or "").strip()
    if reason != COVERAGE_EPOCH_REASON:
        raise RuntimeError("coverage epoch reason is unsupported")
    if payload.get("measurement_only") is not True:
        raise RuntimeError("coverage epoch measurement_only must be true")
    if payload.get("trade_authority_changed") is not False:
        raise RuntimeError("coverage epoch trade_authority_changed must be false")
    if payload.get("policy_change_authorized") is not False:
        raise RuntimeError("coverage epoch policy_change_authorized must be false")
    return CoverageEpoch(
        schema_version=COVERAGE_EPOCH_SCHEMA_VERSION,
        kind=COVERAGE_EPOCH_KIND,
        archive_prefix=prefix,
        boundary_at_utc=boundary,
        production_deployed_sha=production_sha,
        exported_at_utc=exported_at,
        hot_bytes=hot_bytes,
        hot_sha256=hot_sha,
        legacy_window_index_state_sha256=legacy_sha,
        reason=reason,
        measurement_only=True,
        trade_authority_changed=False,
        policy_change_authorized=False,
    )


def load_coverage_epoch(data_root: Path | str) -> CoverageEpoch | None:
    path = coverage_epoch_path(data_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("coverage epoch is unreadable") from exc
    return parse_coverage_epoch(payload)


def validate_epoch_against_archive(
    epoch: CoverageEpoch,
    archive: BoundedJsonlArchive,
    *,
    require_live_hot_match: bool = False,
) -> None:
    """Fail closed when epoch does not describe this archive's legacy condition."""
    if epoch.archive_prefix != archive.archive_prefix:
        raise RuntimeError("coverage epoch archive_prefix does not match archive")
    if not archive_matches_legacy_ambiguous_hot_condition(archive):
        raise RuntimeError(
            "coverage epoch archive is not legacy ambiguous HOT-present state"
        )
    state_sha = legacy_window_index_state_sha256(archive)
    if state_sha != epoch.legacy_window_index_state_sha256:
        raise RuntimeError("coverage epoch legacy state SHA does not match")
    facts = inspect_canonical_archive_files(archive)
    if facts.hot_bytes <= 0:
        raise RuntimeError("coverage epoch requires HOT evidence")
    if require_live_hot_match:
        if facts.hot_bytes != epoch.hot_bytes:
            raise RuntimeError("coverage epoch hot_bytes does not match live HOT")
        live_hot_sha = hot_file_sha256(archive)
        if live_hot_sha != epoch.hot_sha256:
            raise RuntimeError("coverage epoch hot_sha256 does not match live HOT")


def epoch_allows_post_boundary_hot_only(
    epoch: CoverageEpoch,
    archive: BoundedJsonlArchive,
    *,
    start: datetime,
    through: datetime,
) -> ArchiveWindowSelection | None:
    """Return a synthetic complete empty-archive selection for post-boundary windows.

    Returns None when the epoch does not apply to this archive. Raises when the
    epoch applies but the requested window is not entirely post-boundary.
    """
    try:
        validate_epoch_against_archive(epoch, archive, require_live_hot_match=False)
    except RuntimeError:
        return None
    if start.tzinfo is None or through.tzinfo is None:
        raise ValueError("window bounds must be timezone-aware")
    start_utc = start.astimezone(timezone.utc)
    through_utc = through.astimezone(timezone.utc)
    boundary = epoch.boundary_at_utc
    if start_utc < boundary:
        return ArchiveWindowSelection(
            paths=(),
            complete=False,
            truncated=False,
            warnings=(WARNING_PRE_BOUNDARY,),
        )
    if through_utc < start_utc:
        raise ValueError("start cannot be after through")
    return ArchiveWindowSelection(
        paths=(),
        complete=True,
        truncated=False,
        warnings=(WARNING_POST_BOUNDARY,),
    )


def row_visibility_utc(row: Mapping[str, Any], *, kind: str) -> datetime | None:
    if kind == "screening":
        return _parse_utc(row.get("observed_at"))
    if kind == "funnel":
        return _parse_utc(
            row.get("decision_at_utc")
            or row.get("decided_at")
            or row.get("observed_at")
        )
    return _parse_utc(row.get("observed_at") or row.get("decision_at_utc"))


def row_allowed_for_post_boundary_learning(
    row: Mapping[str, Any],
    *,
    kind: str,
    start: datetime,
    through: datetime,
    boundary: datetime,
) -> bool:
    """Governed learning may consume only in-window, post-boundary rows."""
    visible = row_visibility_utc(row, kind=kind)
    if visible is None:
        return False
    if visible > through.astimezone(timezone.utc):
        return False
    if visible < start.astimezone(timezone.utc):
        return False
    if visible < boundary.astimezone(timezone.utc):
        return False
    return True


def outcome_window_crosses_discontinuity(
    reference_at: datetime,
    boundary: datetime,
    *,
    pad,
) -> bool:
    """True when the required evidence window starts before the boundary."""
    start = reference_at.astimezone(timezone.utc) - pad
    return start < boundary.astimezone(timezone.utc)


def diagnostic_epoch_status(data_root: Path | str) -> dict[str, str]:
    """Read-only diagnostic fields; never mutates evidence."""
    path = coverage_epoch_path(data_root)
    if not path.is_file():
        return {
            "learning_coverage_epoch_status": STATUS_ABSENT,
            "learning_coverage_epoch_boundary_utc": "ABSENT",
            "learning_coverage_epoch_archive": "ABSENT",
            "learning_coverage_epoch_reason": "ABSENT",
        }
    try:
        epoch = load_coverage_epoch(data_root)
    except RuntimeError:
        return {
            "learning_coverage_epoch_status": STATUS_INVALID,
            "learning_coverage_epoch_boundary_utc": "INVALID",
            "learning_coverage_epoch_archive": "INVALID",
            "learning_coverage_epoch_reason": "INVALID",
        }
    if epoch is None:
        return {
            "learning_coverage_epoch_status": STATUS_ABSENT,
            "learning_coverage_epoch_boundary_utc": "ABSENT",
            "learning_coverage_epoch_archive": "ABSENT",
            "learning_coverage_epoch_reason": "ABSENT",
        }
    return {
        "learning_coverage_epoch_status": STATUS_VALID,
        "learning_coverage_epoch_boundary_utc": epoch.boundary_at_utc.isoformat(),
        "learning_coverage_epoch_archive": epoch.archive_prefix,
        "learning_coverage_epoch_reason": epoch.reason,
    }


def classify_replica_archive_coverage_status(
    archive: BoundedJsonlArchive,
    *,
    repair_disposition: str | None = None,
) -> str:
    """Human-readable coverage class for diagnostics (not a health claim)."""
    if repair_disposition == DISPOSITION_LEGACY_COVERAGE_DISCONTINUITY:
        return DISPOSITION_LEGACY_COVERAGE_DISCONTINUITY
    if repair_disposition in {
        "EMPTY_CERTIFIED",
        "EMPTY_CERTIFIED_FROM_EXPORT_ATTESTATION",
    }:
        return "EMPTY_CERTIFIED"
    if archive.window_index_state_file.is_file():
        try:
            state = json.loads(
                archive.window_index_state_file.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return "ARCHIVE_INCOMPLETE"
        if isinstance(state, dict) and state.get("complete") is True:
            return "ARCHIVE_COMPLETE"
        return "ARCHIVE_INCOMPLETE"
    return "ARCHIVE_INCOMPLETE"


def _oneshot_requested() -> bool:
    return _env_truthy("OPIP_LEARNING_ESTABLISH_COVERAGE_DISCONTINUITY")


def _expected_oneshot_inputs() -> tuple[str, str]:
    prefix = os.getenv(
        "OPIP_LEARNING_COVERAGE_DISCONTINUITY_ARCHIVE_PREFIX", ""
    ).strip()
    expected_sha = os.getenv(
        "OPIP_LEARNING_COVERAGE_DISCONTINUITY_EXPECTED_STATE_SHA", ""
    ).strip().lower()
    if not prefix:
        raise RuntimeError(
            "oneshot coverage discontinuity requires "
            "OPIP_LEARNING_COVERAGE_DISCONTINUITY_ARCHIVE_PREFIX"
        )
    if not _SHA256_RE.fullmatch(expected_sha):
        raise RuntimeError(
            "oneshot coverage discontinuity requires "
            "OPIP_LEARNING_COVERAGE_DISCONTINUITY_EXPECTED_STATE_SHA"
        )
    return prefix, expected_sha


def _write_oneshot_consumed(data_root: Path, *, archive_prefix: str) -> None:
    coverage_dir(data_root).mkdir(parents=True, exist_ok=True)
    recorded = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_atomic_lines(
        oneshot_consumed_path(data_root),
        [
            (
                f"consumed_at_utc={recorded}\n"
                f"archive_prefix={archive_prefix}\n"
                f"kind={COVERAGE_EPOCH_KIND}\n"
            ).encode("utf-8")
        ],
    )


def establish_coverage_discontinuity_epoch(
    data_root: Path | str,
    archive: BoundedJsonlArchive,
    *,
    expected_legacy_state_sha256: str,
    boundary_at_utc: datetime | None = None,
    production_deployed_sha: str | None = None,
    exported_at_utc: str | None = None,
) -> CoverageEpoch:
    """One-shot create the durable epoch after verifying legacy condition C.

    Idempotency contract (Finding 2): a matching epoch that already exists is
    returned unchanged **before** any live condition-C / HOT check. This lets a
    recurring job (or a restart) that still carries the one-shot authorization
    env re-observe the completed migration without advancing the boundary, and
    without failing merely because the archive has since rotated out of
    condition C. Establishing a *new* epoch still requires live condition C and
    an exact legacy-state SHA match.
    """
    root = Path(data_root)
    expected = expected_legacy_state_sha256.strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise RuntimeError("expected legacy state SHA is invalid")

    # Present-but-invalid epoch is a fail-closed defect, never a fresh mint.
    try:
        existing = load_coverage_epoch(root)
    except RuntimeError as exc:
        raise RuntimeError(
            "refusing coverage discontinuity; existing epoch invalid"
        ) from exc
    if existing is not None:
        # Immutable provenance: never advance boundary / rewrite identity.
        if existing.archive_prefix != archive.archive_prefix:
            raise RuntimeError(
                "refusing coverage discontinuity; existing epoch archive differs"
            )
        if existing.legacy_window_index_state_sha256 != expected:
            raise RuntimeError(
                "refusing coverage discontinuity; existing epoch SHA differs"
            )
        return existing

    # No epoch yet: establishing a new one requires the live legacy condition C
    # and an exact legacy-state SHA match.
    if not archive_matches_legacy_ambiguous_hot_condition(archive):
        raise RuntimeError(
            "refusing coverage discontinuity; archive is not HOT-present "
            "legacy ambiguous state"
        )
    state_sha = legacy_window_index_state_sha256(archive)
    if state_sha != expected:
        raise RuntimeError(
            "refusing coverage discontinuity; legacy state SHA mismatch"
        )

    if oneshot_consumed_path(root).is_file():
        raise RuntimeError(
            "refusing coverage discontinuity; oneshot already consumed without epoch"
        )

    facts = inspect_canonical_archive_files(archive)
    hot_sha = hot_file_sha256(archive)
    manifest = _read_manifest_env(root)
    production_sha = (
        production_deployed_sha
        or manifest.get("production_deployed_sha")
        or ""
    ).strip().lower()
    if not _SHA40_RE.fullmatch(production_sha):
        raise RuntimeError(
            "refusing coverage discontinuity; production_deployed_sha unavailable"
        )
    exported = (
        exported_at_utc or manifest.get("exported_at_utc") or ""
    ).strip()
    if not exported or _parse_utc(exported) is None:
        raise RuntimeError(
            "refusing coverage discontinuity; exported_at_utc unavailable"
        )
    boundary = boundary_at_utc or _parse_utc(exported)
    if boundary is None:
        raise RuntimeError("refusing coverage discontinuity; boundary_at_utc invalid")
    # Boundary is the recovery generation timestamp (export identity), never a
    # moving "now" that advances on later syncs.
    epoch = CoverageEpoch(
        schema_version=COVERAGE_EPOCH_SCHEMA_VERSION,
        kind=COVERAGE_EPOCH_KIND,
        archive_prefix=archive.archive_prefix,
        boundary_at_utc=boundary.astimezone(timezone.utc),
        production_deployed_sha=production_sha,
        exported_at_utc=exported,
        hot_bytes=facts.hot_bytes,
        hot_sha256=hot_sha,
        legacy_window_index_state_sha256=state_sha,
        reason=COVERAGE_EPOCH_REASON,
        measurement_only=True,
        trade_authority_changed=False,
        policy_change_authorized=False,
    )
    coverage_dir(root).mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        epoch.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    write_atomic_lines(coverage_epoch_path(root), [payload])
    _write_oneshot_consumed(root, archive_prefix=archive.archive_prefix)
    return epoch


def maybe_establish_oneshot_coverage_discontinuity(
    data_root: Path | str,
    archives: Mapping[str, BoundedJsonlArchive],
) -> CoverageEpoch | None:
    """Consume default-false oneshot env exactly once when conditions match."""
    if not _oneshot_requested():
        return None
    root = Path(data_root)
    prefix, expected_sha = _expected_oneshot_inputs()
    target: BoundedJsonlArchive | None = None
    for archive in archives.values():
        if archive.archive_prefix == prefix:
            target = archive
            break
    if target is None:
        raise RuntimeError(
            "oneshot coverage discontinuity archive_prefix not in qualification set"
        )
    return establish_coverage_discontinuity_epoch(
        root,
        target,
        expected_legacy_state_sha256=expected_sha,
    )


def load_applicable_coverage_epoch(
    data_root: Path | str,
    archive: BoundedJsonlArchive,
) -> CoverageEpoch | None:
    """Return epoch when present and bound to this archive prefix.

    Unlike ``resolve_discontinuity_for_archive``, this does **not** require
    live condition C. After post-boundary archive rotation, the epoch remains
    provenance and still governs pre-boundary / straddling windows.
    """
    epoch = load_coverage_epoch(data_root)
    if epoch is None:
        return None
    if epoch.archive_prefix != archive.archive_prefix:
        return None
    if epoch.reason != COVERAGE_EPOCH_REASON:
        raise RuntimeError("coverage epoch reason is unsupported")
    if epoch.measurement_only is not True:
        raise RuntimeError("coverage epoch measurement_only must be true")
    if epoch.trade_authority_changed is not False:
        raise RuntimeError("coverage epoch trade_authority_changed must be false")
    if epoch.policy_change_authorized is not False:
        raise RuntimeError("coverage epoch policy_change_authorized must be false")
    return epoch


def resolve_discontinuity_for_archive(
    data_root: Path | str,
    archive: BoundedJsonlArchive,
) -> CoverageEpoch | None:
    """Return a validated epoch for condition C, or None if epoch absent/inapplicable.

    Raises when an epoch exists for this archive prefix but fails validation
    (wrong legacy state SHA, malformed live condition, etc.). A durable epoch
    for a *different* archive prefix does not apply and returns None so other
    qualification archives can still reconcile.
    """
    if not archive_matches_legacy_ambiguous_hot_condition(archive):
        return None
    epoch = load_coverage_epoch(data_root)
    if epoch is None:
        return None
    if epoch.archive_prefix != archive.archive_prefix:
        return None
    validate_epoch_against_archive(epoch, archive, require_live_hot_match=False)
    return epoch
