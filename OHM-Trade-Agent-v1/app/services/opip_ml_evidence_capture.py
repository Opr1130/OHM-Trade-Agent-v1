"""Asynchronous production evidence capture for O'Pip ML Foundation v1.

This worker consumes the existing canonical P1 evidence ledger after the live
scanner has completed. It never calls an exchange, changes ranking, sends
notifications, mutates trading state, or participates in deterministic risk
protection. Every failure is evidence-only and fail-open to production.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from app.opip.ml.snapshot import seal_feature_snapshot
from app.opip.ml.temporal import AvailabilityStamp, TemporalIntegrityError, require_utc
from app.services.p1_shadow_outbox import (
    DEFAULT_EVIDENCE_LEDGER,
    DrainResult,
    drain_outbox_to_evidence_ledger,
    p1_shadow_outbox_enabled,
)
from app.services.registry_io import registry_lock, save_json_atomic


DEFAULT_ML_SNAPSHOT_DIR = Path("/app/data/opip_ml_feature_snapshots_v1")
DEFAULT_ML_CHECKPOINT_FILE = Path("/app/data/opip_ml_capture_checkpoint.json")
DEFAULT_ML_DEAD_LETTER_FILE = Path("/app/data/opip_ml_capture_dead_letter.jsonl")
DEFAULT_ML_HEALTH_FILE = Path("/app/data/opip_ml_capture_health.json")
DEFAULT_ML_CAPTURE_LOCK_FILE = Path("/var/run/opip-ml-capture.lock")
ML_CAPTURE_SCHEMA_VERSION = 1
ML_FEATURE_SCHEMA_VERSION = "canonical-market-v1"
ML_FEATURE_CALC_VERSION = "canonical-episode-ml-v1"


@dataclass(frozen=True)
class EvidenceLine:
    line_number: int
    start_offset: int
    end_offset: int
    raw: bytes


@dataclass(frozen=True)
class MLCaptureSummary:
    enabled: bool
    ledger_rows_seen: int = 0
    ledger_bytes_seen: int = 0
    batch_rows: int = 0
    processed: int = 0
    legacy_without_seed: int = 0
    malformed: int = 0
    temporal_violations: int = 0
    duplicate_snapshots_skipped: int = 0
    missing_feature_values: int = 0
    feature_values: int = 0
    next_line: int = 0
    byte_offset: int = 0
    p1_drained: int = 0
    p1_duplicates: int = 0
    p1_malformed: int = 0
    p1_stopped_on_error: bool = False
    error_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "record_type": "OPIP_ML_CAPTURE_HEALTH",
                "schema_version": ML_CAPTURE_SCHEMA_VERSION,
                "measurement_only": True,
                "affects_live_decisions": False,
                "trade_authority_changed": False,
            }
        )
        return payload


def _parse_utc(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return require_utc(value, field_name=field_name)
    if not value:
        raise ValueError(f"{field_name} is required")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return require_utc(parsed, field_name=field_name)


def _feature_dag_hash(feature_names: list[str]) -> str:
    encoded = json.dumps(
        sorted(feature_names),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "MLDAG:" + hashlib.sha256(encoded).hexdigest()[:32]


def _availability_from_seed(
    seed: Mapping[str, Any],
    *,
    decision_at_utc: datetime,
) -> AvailabilityStamp:
    raw = seed.get("availability")
    if not isinstance(raw, Mapping):
        raise ValueError("ml_feature_seed.availability is required")
    source_raw = raw.get("source_at_utc")
    source = (
        _parse_utc(source_raw, field_name="source_at_utc")
        if source_raw
        else None
    )
    stamp = AvailabilityStamp(
        source_at_utc=source,
        ingested_at_utc=_parse_utc(
            raw.get("ingested_at_utc"),
            field_name="ingested_at_utc",
        ),
        visible_at_utc=_parse_utc(
            raw.get("visible_at_utc"),
            field_name="visible_at_utc",
        ),
        source_version=str(raw.get("source_version") or "").strip(),
    )
    if not stamp.eligible_at(decision_at_utc):
        raise TemporalIntegrityError(
            "canonical ML feature seed is not visible by decision_at"
        )
    return stamp


def build_ml_snapshot_from_canonical(row: Mapping[str, Any]):
    """Seal one canonical episode row into the Foundation v1 FeatureSnapshot."""

    if str(row.get("record_type") or "") != "CANONICAL_EPISODE_SNAPSHOT":
        raise ValueError("row is not a canonical episode snapshot")
    seed = row.get("ml_feature_seed")
    if not isinstance(seed, Mapping):
        raise ValueError("canonical episode has no ML feature seed")
    if int(seed.get("schema_version", 0) or 0) != 1:
        raise ValueError("unsupported ML feature seed schema")
    if not bool(seed.get("deterministic_outputs_excluded", False)):
        raise ValueError("ML feature seed must exclude deterministic outputs")

    feature_values = seed.get("feature_values")
    if not isinstance(feature_values, Mapping) or not feature_values:
        raise ValueError("ML feature seed has no feature values")

    decision = _parse_utc(row.get("decision_at_utc"), field_name="decision_at_utc")
    stamp = _availability_from_seed(seed, decision_at_utc=decision)
    availability = {str(name): stamp for name in feature_values}
    symbol = str(row.get("symbol") or "").strip().upper()
    episode_id = str(row.get("episode_id") or "").strip()
    if not symbol or not episode_id:
        raise ValueError("canonical episode identity is incomplete")

    base_asset = str(row.get("base_asset") or "").strip().lower()
    if not base_asset:
        base_asset = symbol.lower()

    return seal_feature_snapshot(
        episode_id=episode_id,
        candidate_id=None,
        decision_at_utc=decision,
        canonical_asset_id=base_asset,
        venue="KRAKEN",
        pair=symbol,
        direction="NONE",
        lane="PRODUCTION_SHADOW",
        regime=None,
        feature_values={str(key): value for key, value in feature_values.items()},
        availability=availability,
        feature_schema_version=ML_FEATURE_SCHEMA_VERSION,
        feature_calc_version=ML_FEATURE_CALC_VERSION,
        feature_dag_hash=_feature_dag_hash([str(key) for key in feature_values]),
        source_versions={
            "canonical_episode": f"v{int(row.get('schema_version', 1) or 1)}",
            "scan_source": str(row.get("scan_source") or "UNKNOWN"),
        },
    )


def _load_checkpoint(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    next_line = int(payload.get("next_line", 0))
    byte_offset = int(payload.get("byte_offset", 0))
    if next_line < 0 or byte_offset < 0:
        raise ValueError("ML capture checkpoint cannot be negative")
    return next_line, byte_offset


def _read_complete_batch(
    path: Path,
    *,
    next_line: int,
    byte_offset: int,
    batch_limit: int,
) -> tuple[list[EvidenceLine], int]:
    """Read only complete new JSONL records after the durable byte checkpoint."""

    if not path.exists():
        if byte_offset != 0:
            raise ValueError("checkpoint byte_offset is ahead of missing evidence ledger")
        return [], 0

    lock = path.parent / f".{path.name}.lock"
    rows: list[EvidenceLine] = []
    with registry_lock(lock):
        size = path.stat().st_size
        if byte_offset > size:
            raise ValueError("checkpoint byte_offset is ahead of evidence ledger")
        with path.open("rb") as handle:
            handle.seek(byte_offset)
            line_number = next_line
            while len(rows) < batch_limit:
                start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                end = handle.tell()
                if not raw.endswith(b"\n"):
                    # Writer has not completed the final JSONL record yet.
                    break
                rows.append(
                    EvidenceLine(
                        line_number=line_number,
                        start_offset=start,
                        end_offset=end,
                        raw=raw,
                    )
                )
                line_number += 1
    return rows, size


def _append_dead_letter(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / f".{path.name}.lock"
    with registry_lock(lock):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(payload), sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def _chunk_bytes(wrappers: list[Mapping[str, Any]]) -> bytes:
    raw = b"".join(
        (
            json.dumps(
                dict(wrapper),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for wrapper in wrappers
    )
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _chunk_name(
    *,
    start_line: int,
    end_line: int,
    start_offset: int,
    end_offset: int,
    compressed_payload: bytes,
) -> str:
    digest = hashlib.sha256(compressed_payload).hexdigest()[:20]
    return (
        f"chunk-{start_line:012d}-{end_line:012d}-"
        f"{start_offset:020d}-{end_offset:020d}-{digest}.jsonl.gz"
    )


def _fsync_directory(path: Path) -> None:
    """Require the directory entry namespace to be durable before checkpointing."""

    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_snapshot_chunk_atomic(
    snapshot_dir: Path,
    *,
    wrappers: list[Mapping[str, Any]],
    batch: list[EvidenceLine],
) -> tuple[bool, Path | None]:
    """Atomically publish one compressed immutable chunk for a ledger batch."""

    if not wrappers or not batch:
        return False, None

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    compressed = _chunk_bytes(wrappers)
    name = _chunk_name(
        start_line=batch[0].line_number,
        end_line=batch[-1].line_number + 1,
        start_offset=batch[0].start_offset,
        end_offset=batch[-1].end_offset,
        compressed_payload=compressed,
    )
    destination = snapshot_dir / name
    if destination.exists():
        # A crash retry may find the deterministic chunk already published.
        # Prove the directory entry is durable before permitting checkpoint
        # progression on that retry.
        _fsync_directory(snapshot_dir)
        return False, destination

    temp = snapshot_dir / f".{name}.{os.getpid()}.tmp"
    try:
        with temp.open("wb") as handle:
            handle.write(compressed)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic rename means a killed process leaves either the old namespace
        # or one complete gzip member, never a truncated published chunk.
        os.replace(temp, destination)
        # Directory synchronization is part of publication success. Never let
        # the caller advance its evidence checkpoint if this fails.
        _fsync_directory(snapshot_dir)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return True, destination


def _capture_ml_production_evidence_locked(
    *,
    evidence_path: Path = DEFAULT_EVIDENCE_LEDGER,
    snapshot_dir: Path = DEFAULT_ML_SNAPSHOT_DIR,
    checkpoint_path: Path = DEFAULT_ML_CHECKPOINT_FILE,
    dead_letter_path: Path = DEFAULT_ML_DEAD_LETTER_FILE,
    health_path: Path = DEFAULT_ML_HEALTH_FILE,
    batch_limit: int = 250,
    enabled: bool | None = None,
) -> MLCaptureSummary:
    """Drain canonical evidence into atomic compressed FeatureSnapshot chunks.

    The P1 durable outbox is drained first only for the production-default
    ledger. The consumer then reads complete ledger bytes from its durable
    byte-offset checkpoint. Old rows without the v1.1 seed are intentionally
    skipped; they are never backfilled with fabricated availability timestamps.
    """

    active = p1_shadow_outbox_enabled() if enabled is None else bool(enabled)
    if not active:
        summary = MLCaptureSummary(enabled=False)
        save_json_atomic(health_path, summary.as_dict())
        return summary
    if batch_limit < 1:
        raise ValueError("batch_limit must be >= 1")

    if evidence_path == DEFAULT_EVIDENCE_LEDGER:
        p1 = drain_outbox_to_evidence_ledger(
            evidence_path=evidence_path,
            batch_limit=batch_limit,
        )
    else:
        # Custom/replay ledgers must never consume the production-default P1
        # outbox or advance its checkpoint implicitly.
        p1 = DrainResult(0, 0, 0, 0, False)

    try:
        start_line, start_offset = _load_checkpoint(checkpoint_path)
        batch, ledger_size = _read_complete_batch(
            evidence_path,
            next_line=start_line,
            byte_offset=start_offset,
            batch_limit=batch_limit,
        )
    except Exception as exc:
        summary = MLCaptureSummary(
            enabled=True,
            next_line=0,
            byte_offset=0,
            p1_drained=p1.processed,
            p1_duplicates=p1.duplicates,
            p1_malformed=p1.malformed,
            p1_stopped_on_error=p1.stopped_on_error,
            error_type=f"ML_CAPTURE_CURSOR_UNREADABLE:{type(exc).__name__}",
        )
        save_json_atomic(health_path, summary.as_dict())
        return summary

    processed = legacy = malformed = temporal = missing = feature_count = 0
    wrappers: list[dict[str, Any]] = []

    for item in batch:
        try:
            row = json.loads(item.raw.decode("utf-8"))
            if not isinstance(row, dict):
                raise ValueError("evidence row must be an object")
        except Exception as exc:
            malformed += 1
            _append_dead_letter(
                dead_letter_path,
                {
                    "line_number": item.line_number,
                    "byte_offset": item.start_offset,
                    "error_type": type(exc).__name__,
                    "reason": "MALFORMED_EVIDENCE_ROW",
                    "measurement_only": True,
                },
            )
            continue

        if str(row.get("record_type") or "") != "CANONICAL_EPISODE_SNAPSHOT":
            continue
        if not isinstance(row.get("ml_feature_seed"), Mapping):
            legacy += 1
            continue

        try:
            snapshot = build_ml_snapshot_from_canonical(row)
            values = snapshot.ml_feature_mapping()
            feature_count += len(values)
            missing += sum(value is None for value in values.values())
            wrappers.append(
                {
                    "record_type": "OPIP_ML_FEATURE_SNAPSHOT",
                    "capture_schema_version": ML_CAPTURE_SCHEMA_VERSION,
                    "canonical_snapshot_id": str(row.get("snapshot_id") or ""),
                    "episode_id": snapshot.episode_id,
                    "ml_snapshot_id": snapshot.snapshot_id,
                    "decision_at_utc": snapshot.decision_at_utc.isoformat(),
                    "symbol": snapshot.pair,
                    "feature_snapshot": snapshot.to_dict(),
                    "audit_context": {
                        "decision_status": row.get("decision_status"),
                        "candidate_rank": row.get("candidate_rank"),
                        "opportunity_score": row.get("opportunity_score"),
                        "suppressed": row.get("suppressed"),
                    },
                    "measurement_only": True,
                    "advisory_only": True,
                    "affects_live_decisions": False,
                    "trade_authority_changed": False,
                }
            )
        except TemporalIntegrityError as exc:
            temporal += 1
            _append_dead_letter(
                dead_letter_path,
                {
                    "line_number": item.line_number,
                    "byte_offset": item.start_offset,
                    "canonical_snapshot_id": str(row.get("snapshot_id") or ""),
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                    "measurement_only": True,
                },
            )
        except Exception as exc:
            malformed += 1
            _append_dead_letter(
                dead_letter_path,
                {
                    "line_number": item.line_number,
                    "byte_offset": item.start_offset,
                    "canonical_snapshot_id": str(row.get("snapshot_id") or ""),
                    "error_type": type(exc).__name__,
                    "reason": "ML_SNAPSHOT_BUILD_FAILED",
                    "measurement_only": True,
                },
            )

    duplicate_snapshots = 0
    if batch:
        try:
            created, _ = _write_snapshot_chunk_atomic(
                snapshot_dir,
                wrappers=wrappers,
                batch=batch,
            )
        except Exception as exc:
            summary = MLCaptureSummary(
                enabled=True,
                ledger_rows_seen=start_line + len(batch),
                ledger_bytes_seen=ledger_size,
                batch_rows=len(batch),
                legacy_without_seed=legacy,
                malformed=malformed,
                temporal_violations=temporal,
                missing_feature_values=missing,
                feature_values=feature_count,
                next_line=start_line,
                byte_offset=start_offset,
                p1_drained=p1.processed,
                p1_duplicates=p1.duplicates,
                p1_malformed=p1.malformed,
                p1_stopped_on_error=p1.stopped_on_error,
                error_type=f"ML_SNAPSHOT_CHUNK_WRITE_FAILED:{type(exc).__name__}",
            )
            save_json_atomic(health_path, summary.as_dict())
            return summary

        if wrappers:
            if created:
                processed = len(wrappers)
            else:
                # The deterministic chunk already exists: this is a crash retry
                # after durable chunk publication but before checkpoint commit.
                duplicate_snapshots = len(wrappers)

        next_line = batch[-1].line_number + 1
        next_offset = batch[-1].end_offset
        save_json_atomic(
            checkpoint_path,
            {
                "schema_version": 1,
                "next_line": next_line,
                "byte_offset": next_offset,
            },
        )
    else:
        next_line = start_line
        next_offset = start_offset

    summary = MLCaptureSummary(
        enabled=True,
        ledger_rows_seen=next_line,
        ledger_bytes_seen=ledger_size,
        batch_rows=len(batch),
        processed=processed,
        legacy_without_seed=legacy,
        malformed=malformed,
        temporal_violations=temporal,
        duplicate_snapshots_skipped=duplicate_snapshots,
        missing_feature_values=missing,
        feature_values=feature_count,
        next_line=next_line,
        byte_offset=next_offset,
        p1_drained=p1.processed,
        p1_duplicates=p1.duplicates,
        p1_malformed=p1.malformed,
        p1_stopped_on_error=p1.stopped_on_error,
        error_type=p1.error_type if p1.stopped_on_error else None,
    )
    save_json_atomic(health_path, summary.as_dict())
    return summary

def capture_ml_production_evidence(
    *,
    evidence_path: Path = DEFAULT_EVIDENCE_LEDGER,
    snapshot_dir: Path = DEFAULT_ML_SNAPSHOT_DIR,
    checkpoint_path: Path = DEFAULT_ML_CHECKPOINT_FILE,
    dead_letter_path: Path = DEFAULT_ML_DEAD_LETTER_FILE,
    health_path: Path = DEFAULT_ML_HEALTH_FILE,
    batch_limit: int = 250,
    enabled: bool | None = None,
    capture_lock_path: Path | None = None,
) -> MLCaptureSummary:
    """Serialize one complete ML evidence capture pass.

    Production serializes capture state with its internal pass lock. Host
    cron/deploy invocations use a distinct nonblocking trigger lock so they can
    skip overlapping launches without recursively acquiring this same flock.
    Tests and isolated replay paths use a sibling lock next to their custom
    checkpoint so they never contend with production state.
    """
    lock_path = capture_lock_path
    if lock_path is None:
        lock_path = (
            DEFAULT_ML_CAPTURE_LOCK_FILE
            if checkpoint_path == DEFAULT_ML_CHECKPOINT_FILE
            else checkpoint_path.parent / ".opip-ml-capture.lock"
        )
    with registry_lock(lock_path):
        return _capture_ml_production_evidence_locked(
            evidence_path=evidence_path,
            snapshot_dir=snapshot_dir,
            checkpoint_path=checkpoint_path,
            dead_letter_path=dead_letter_path,
            health_path=health_path,
            batch_limit=batch_limit,
            enabled=enabled,
        )

