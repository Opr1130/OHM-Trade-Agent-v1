"""Independent read-only O'Pip ML data-readiness report job."""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from app.opip.learning.paper_readiness import assess_paper_learning_readiness
from app.opip.learning.readiness import build_ml_data_readiness_report
from app.services.registry_io import save_json_atomic


CANONICAL_EVIDENCE = Path("/app/data/p1_evidence_ledger.jsonl")
ML_SNAPSHOT_DIR = Path("/app/data/opip_ml_feature_snapshots_v1")
PHASE3C_OUTCOMES = Path("/app/data/phase3c_forward_outcomes.jsonl")
PAPER_STATE = Path("/app/data/paper_trading/state.json")
CAPTURE_HEALTH = Path("/app/data/opip_ml_capture_health.json")
CAPTURE_DEAD_LETTER = Path("/app/data/opip_ml_capture_dead_letter.jsonl")
READINESS_REPORT = Path("/app/data/opip_ml_data_readiness_v1.json")


def _jsonl_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read JSONL evidence and count malformed records instead of crashing."""
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    malformed = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    malformed += 1
                    continue
                if isinstance(value, dict):
                    rows.append(value)
                else:
                    malformed += 1
    except OSError:
        return [], 1
    return rows, malformed


def _ml_rows(snapshot_dir: Path) -> tuple[list[dict[str, Any]], int]:
    """Read immutable gzip chunks and count corrupt rows/chunks fail-closed."""
    if not snapshot_dir.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    malformed = 0
    for path in sorted(snapshot_dir.glob("*.jsonl.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for raw in handle:
                    if not raw.strip():
                        continue
                    try:
                        value = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        malformed += 1
                        continue
                    if isinstance(value, dict):
                        rows.append(value)
                    else:
                        malformed += 1
        except (OSError, EOFError):
            malformed += 1
    return rows, malformed


def _json_object(path: Path) -> tuple[dict[str, Any], int]:
    """Read one JSON object without quarantine, rename, or source mutation."""
    if not path.exists():
        return {}, 0
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(
            raw,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return {}, 1
    if not isinstance(payload, dict):
        return {}, 1
    return dict(payload), 0


def _paper_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Load paper lifecycle state without mutating paper control/state."""
    payload, malformed = _json_object(path)
    if malformed:
        return [], malformed
    if "lifecycles" in payload:
        rows = payload.get("lifecycles")
        if not isinstance(rows, dict):
            return [], 1
    else:
        # Historical direct-map state is accepted only when the top-level object
        # is actually a lifecycle map. A metadata wrapper without lifecycles is
        # malformed rather than being miscounted as several bad trade rows.
        if any(key in payload for key in ("schema_version", "paper_only")):
            return [], 1
        rows = payload
    malformed_rows = sum(1 for row in rows.values() if not isinstance(row, dict))
    return (
        [dict(row) for row in rows.values() if isinstance(row, dict)],
        malformed_rows,
    )


def _capture_health(path: Path) -> tuple[dict[str, Any], int]:
    """Load evidence-capture health without mutating its source file."""
    return _json_object(path)


def _verified_replica_inputs(
    *,
    root: Path | None = None,
    expected_release_sha: str = "",
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, tuple[str, ...]]:
    """Verify the canonical replica and read all three of its authority inputs.

    Returns ``(paper_outcome_rows, paper_lifecycle_rows, source_error,
    incomplete_reasons)``.

    Every input comes from **one** verified generation. That is the whole point
    of resolving the bundle here rather than probing for a SQLite file: it makes
    it structurally impossible to read a canonical store from one generation
    while judging completeness from another generation's lifecycle state or gap
    spool.

    A source that cannot be proven is reported as ``source_error`` with no rows
    at all. It is never reported as zero outcomes, and lifecycle rows from the
    writable data root are deliberately not substituted - that substitution is
    exactly what would let mutable local state stand in for an unavailable
    authority plane.
    """
    import sqlite3

    from app.opip.learning.canonical_replica import (
        ReplicaVerificationError,
        resolve_verified_replica_bundle,
    )
    from app.opip.learning.paper_outcome_reader import (
        PaperOutcomeIntegrityError,
        read_canonical_paper_outcomes,
    )

    reasons: list[str] = []
    try:
        bundle = resolve_verified_replica_bundle(
            root=root,
            expected_source_release_sha=expected_release_sha,
            now=now,
        )
    except ReplicaVerificationError as exc:
        return [], [], f"{type(exc).__name__}: {exc}", (exc.reason,)
    except (sqlite3.Error, OSError) as exc:
        return [], [], f"{type(exc).__name__}: {exc}", (
            "CANONICAL_OUTCOME_SOURCE_UNAVAILABLE",
        )

    if not bundle.completeness_supported:
        reasons.extend(bundle.completeness_reasons)

    try:
        read = read_canonical_paper_outcomes(bundle.canonical_db_path)
        outcomes = [outcome.as_dict() for outcome in read.outcomes]
    except (PaperOutcomeIntegrityError, sqlite3.Error, OSError) as exc:
        return [], [], f"{type(exc).__name__}: {exc}", (
            "CANONICAL_OUTCOME_SOURCE_UNAVAILABLE",
        )

    # Lifecycle rows come from this same generation, never from /app/data.
    lifecycle_rows, _malformed = _paper_rows(bundle.paper_state_path)

    try:
        spool = json.loads(bundle.paper_gap_spool_path.read_text(encoding="utf-8"))
        unresolved = spool.get("unresolved") if isinstance(spool, dict) else None
        if not isinstance(unresolved, list):
            raise ValueError("gap spool has no unresolved list")
        if unresolved:
            reasons.append("PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED")
    except (OSError, ValueError, TypeError):
        # Fail closed: a corrupt spool makes completeness unprovable.
        reasons.append("PAPER_OUTCOME_EVIDENCE_GAP_SPOOL_CORRUPT")

    return outcomes, lifecycle_rows, None, tuple(sorted(set(reasons)))


def build_production_readiness_report(
    *,
    canonical_path: Path = CANONICAL_EVIDENCE,
    snapshot_dir: Path = ML_SNAPSHOT_DIR,
    phase3c_path: Path = PHASE3C_OUTCOMES,
    capture_health_path: Path = CAPTURE_HEALTH,
    capture_dead_letter_path: Path = CAPTURE_DEAD_LETTER,
    canonical_replica_root: Path | None = None,
    expected_release_sha: str = "",
    readiness_now: datetime | None = None,
    long_paper_production_verified: bool = False,
) -> dict[str, Any]:
    """Build one bounded production-evidence readiness snapshot.

    Canonical paper-outcome authority is read exclusively through the verified
    replica bundle. ``canonical_path`` and friends remain the legacy JSON/JSONL
    evidence inputs, which are unrelated to canonical outcome authority.
    """
    canonical_rows, canonical_malformed = _jsonl_rows(canonical_path)
    ml_rows, ml_malformed = _ml_rows(snapshot_dir)
    phase3c_rows, phase3c_malformed = _jsonl_rows(phase3c_path)
    dead_letter_rows, dead_letter_malformed = _jsonl_rows(capture_dead_letter_path)
    (
        paper_outcome_rows,
        paper_rows,
        paper_outcome_source_error,
        paper_outcome_incomplete_reasons,
    ) = _verified_replica_inputs(
        root=canonical_replica_root,
        expected_release_sha=expected_release_sha,
        now=readiness_now,
    )
    # Malformed lifecycle rows inside a verified generation are an integrity
    # signal, not a reason to fall back to another source.
    paper_malformed = sum(
        1
        for row in paper_rows
        if not isinstance(row, dict)
        or not str(row.get("paper_trade_id") or "").strip()
    )
    health, health_malformed = _capture_health(capture_health_path)
    health["malformed"] = int(health.get("malformed", 0) or 0) + (
        canonical_malformed
        + ml_malformed
        + phase3c_malformed
        + paper_malformed
        + dead_letter_malformed
        + health_malformed
    )
    report = build_ml_data_readiness_report(
        canonical_rows=canonical_rows,
        ml_snapshot_rows=ml_rows,
        phase3c_outcome_rows=phase3c_rows,
        paper_trade_rows=paper_rows,
        paper_outcome_rows=paper_outcome_rows,
        paper_outcome_source_error=paper_outcome_source_error,
        paper_outcome_incomplete_reasons=paper_outcome_incomplete_reasons,
        capture_health=health,
        capture_dead_letter_rows=dead_letter_rows,
    )
    return {
        "record_type": "OPIP_ML_DATA_READINESS_V1",
        "schema_version": 1,
        "ml_data_readiness": report.as_dict(),
        "paper_learning_readiness": assess_paper_learning_readiness(
            long_production_verified=long_paper_production_verified
        ).as_dict(),
        "measurement_only": True,
        "affects_live_decisions": False,
        "automatic_training_allowed": False,
        "automatic_promotion": False,
        "trade_authority_changed": False,
    }


def main() -> None:
    """Persist and print one readiness report; never schedule/train/promote."""
    payload = build_production_readiness_report(
        expected_release_sha=os.environ.get("OPIP_PRODUCTION_DEPLOYED_SHA", "").strip()
    )
    save_json_atomic(READINESS_REPORT, payload)
    print(json.dumps(payload, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
