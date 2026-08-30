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
    drain_outbox_to_evidence_ledger,
    p1_shadow_outbox_enabled,
)
from app.services.registry_io import registry_lock, save_json_atomic


DEFAULT_ML_SNAPSHOT_FILE = Path("/app/data/opip_ml_feature_snapshots_v1.jsonl.gz")
DEFAULT_ML_CHECKPOINT_FILE = Path("/app/data/opip_ml_capture_checkpoint.json")
DEFAULT_ML_DEAD_LETTER_FILE = Path("/app/data/opip_ml_capture_dead_letter.jsonl")
DEFAULT_ML_HEALTH_FILE = Path("/app/data/opip_ml_capture_health.json")
ML_CAPTURE_SCHEMA_VERSION = 1
ML_FEATURE_SCHEMA_VERSION = "canonical-market-v1"
ML_FEATURE_CALC_VERSION = "canonical-episode-ml-v1"


@dataclass(frozen=True)
class MLCaptureSummary:
    enabled: bool
    ledger_rows_seen: int = 0
    processed: int = 0
    legacy_without_seed: int = 0
    malformed: int = 0
    temporal_violations: int = 0
    duplicate_snapshots_skipped: int = 0
    missing_feature_values: int = 0
    feature_values: int = 0
    next_line: int = 0
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


def _read_complete_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    lock = path.parent / f".{path.name}.lock"
    with registry_lock(lock):
        text = path.read_text(encoding="utf-8")
    if not text:
        return []
    lines = text.splitlines()
    if not text.endswith("\n") and lines:
        lines = lines[:-1]
    return lines


def _load_next_line(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("next_line", 0)))
    except Exception:
        return 0


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


def _append_gzip_snapshot(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / f".{path.name}.lock"
    with registry_lock(lock):
        with gzip.open(path, "at", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(payload), sort_keys=True, allow_nan=False) + "\n")


def _existing_ml_snapshot_ids(path: Path) -> set[str]:
    """Read deterministic snapshot identities for crash-safe retry deduplication."""
    if not path.exists():
        return set()
    lock = path.parent / f".{path.name}.lock"
    ids: set[str] = set()
    with registry_lock(lock):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError("ML snapshot store contains a non-object row")
                snapshot_id = str(row.get("ml_snapshot_id") or "")
                if not snapshot_id:
                    raise ValueError("ML snapshot store row has no ml_snapshot_id")
                ids.add(snapshot_id)
    return ids


def capture_ml_production_evidence(
    *,
    evidence_path: Path = DEFAULT_EVIDENCE_LEDGER,
    snapshot_path: Path = DEFAULT_ML_SNAPSHOT_FILE,
    checkpoint_path: Path = DEFAULT_ML_CHECKPOINT_FILE,
    dead_letter_path: Path = DEFAULT_ML_DEAD_LETTER_FILE,
    health_path: Path = DEFAULT_ML_HEALTH_FILE,
    batch_limit: int = 250,
    enabled: bool | None = None,
) -> MLCaptureSummary:
    """Drain canonical evidence into compressed FeatureSnapshots, fail-open.

    The P1 durable outbox is drained first using its existing independent
    checkpoint. This worker then consumes only the accepted evidence ledger and
    advances its own cursor. Old rows without the v1.1 seed are intentionally
    skipped; they are not backfilled with fabricated availability timestamps.
    """

    active = p1_shadow_outbox_enabled() if enabled is None else bool(enabled)
    if not active:
        summary = MLCaptureSummary(enabled=False)
        save_json_atomic(health_path, summary.as_dict())
        return summary
    if batch_limit < 1:
        raise ValueError("batch_limit must be >= 1")

    p1 = drain_outbox_to_evidence_ledger(\n        evidence_path=evidence_path,\n        batch_limit=batch_limit,\n    )\n    lines = _read_complete_lines(evidence_path)
    start = _load_next_line(checkpoint_path)
    if start > len(lines):
        summary = MLCaptureSummary(
            enabled=True,
            ledger_rows_seen=len(lines),
            next_line=start,
            p1_drained=p1.processed,
            p1_duplicates=p1.duplicates,
            p1_malformed=p1.malformed,
            p1_stopped_on_error=p1.stopped_on_error,
            error_type="CHECKPOINT_AHEAD_OF_EVIDENCE_LEDGER",
        )
        save_json_atomic(health_path, summary.as_dict())
        return summary

    processed = legacy = malformed = temporal = missing = feature_count = 0
    duplicate_snapshots = 0
    try:
        known_ml_snapshot_ids = _existing_ml_snapshot_ids(snapshot_path)
    except Exception as exc:
        summary = MLCaptureSummary(
            enabled=True,
            ledger_rows_seen=len(lines),
            next_line=start,
            p1_drained=p1.processed,
            p1_duplicates=p1.duplicates,
            p1_malformed=p1.malformed,
            p1_stopped_on_error=p1.stopped_on_error,
            error_type=f"ML_SNAPSHOT_STORE_UNREADABLE:{type(exc).__name__}",
        )
        save_json_atomic(health_path, summary.as_dict())
        return summary
    next_line = start
    upper = min(len(lines), start + batch_limit)
    for index in range(start, upper):
        raw = lines[index]
        try:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("evidence row must be an object")
        except Exception as exc:
            malformed += 1
            _append_dead_letter(
                dead_letter_path,
                {
                    "line_number": index,
                    "error_type": type(exc).__name__,
                    "reason": "MALFORMED_EVIDENCE_ROW",
                    "measurement_only": True,
                },
            )
            next_line = index + 1
            save_json_atomic(checkpoint_path, {"next_line": next_line})
            continue

        if str(row.get("record_type") or "") != "CANONICAL_EPISODE_SNAPSHOT":
            next_line = index + 1
            save_json_atomic(checkpoint_path, {"next_line": next_line})
            continue
        if not isinstance(row.get("ml_feature_seed"), Mapping):
            legacy += 1
            next_line = index + 1
            save_json_atomic(checkpoint_path, {"next_line": next_line})
            continue

        try:
            snapshot = build_ml_snapshot_from_canonical(row)
            values = snapshot.ml_feature_mapping()
            feature_count += len(values)
            missing += sum(value is None for value in values.values())
            wrapper = {
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
            if snapshot.snapshot_id in known_ml_snapshot_ids:
                duplicate_snapshots += 1
            else:
                _append_gzip_snapshot(snapshot_path, wrapper)
                known_ml_snapshot_ids.add(snapshot.snapshot_id)
                processed += 1
        except TemporalIntegrityError as exc:
            temporal += 1
            _append_dead_letter(
                dead_letter_path,
                {
                    "line_number": index,
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
                    "line_number": index,
                    "canonical_snapshot_id": str(row.get("snapshot_id") or ""),
                    "error_type": type(exc).__name__,
                    "reason": "ML_SNAPSHOT_BUILD_FAILED",
                    "measurement_only": True,
                },
            )

        next_line = index + 1
        save_json_atomic(checkpoint_path, {"next_line": next_line})

    summary = MLCaptureSummary(
        enabled=True,
        ledger_rows_seen=len(lines),
        processed=processed,
        legacy_without_seed=legacy,
        malformed=malformed,
        temporal_violations=temporal,
        duplicate_snapshots_skipped=duplicate_snapshots,
        missing_feature_values=missing,
        feature_values=feature_count,
        next_line=next_line,
        p1_drained=p1.processed,
        p1_duplicates=p1.duplicates,
        p1_malformed=p1.malformed,
        p1_stopped_on_error=p1.stopped_on_error,
        error_type=p1.error_type if p1.stopped_on_error else None,
    )
    save_json_atomic(health_path, summary.as_dict())
    return summary
