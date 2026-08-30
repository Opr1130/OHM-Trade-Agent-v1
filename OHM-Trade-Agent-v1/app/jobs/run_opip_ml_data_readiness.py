"""Independent read-only O'Pip ML data-readiness report job."""
from __future__ import annotations

import gzip
import json
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
    rows = payload.get("lifecycles", payload)
    if not isinstance(rows, dict):
        return [], 1
    malformed_rows = sum(1 for row in rows.values() if not isinstance(row, dict))
    return (
        [dict(row) for row in rows.values() if isinstance(row, dict)],
        malformed_rows,
    )


def _capture_health(path: Path) -> tuple[dict[str, Any], int]:
    """Load evidence-capture health without mutating its source file."""
    return _json_object(path)


def build_production_readiness_report(
    *,
    canonical_path: Path = CANONICAL_EVIDENCE,
    snapshot_dir: Path = ML_SNAPSHOT_DIR,
    phase3c_path: Path = PHASE3C_OUTCOMES,
    paper_state_path: Path = PAPER_STATE,
    capture_health_path: Path = CAPTURE_HEALTH,
    long_paper_production_verified: bool = False,
) -> dict[str, Any]:
    """Build one bounded production-evidence readiness snapshot."""
    canonical_rows, canonical_malformed = _jsonl_rows(canonical_path)
    ml_rows, ml_malformed = _ml_rows(snapshot_dir)
    phase3c_rows, phase3c_malformed = _jsonl_rows(phase3c_path)
    paper_rows, paper_malformed = _paper_rows(paper_state_path)
    health, health_malformed = _capture_health(capture_health_path)
    health["malformed"] = int(health.get("malformed", 0) or 0) + (
        canonical_malformed
        + ml_malformed
        + phase3c_malformed
        + paper_malformed
        + health_malformed
    )
    report = build_ml_data_readiness_report(
        canonical_rows=canonical_rows,
        ml_snapshot_rows=ml_rows,
        phase3c_outcome_rows=phase3c_rows,
        paper_trade_rows=paper_rows,
        capture_health=health,
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
    payload = build_production_readiness_report()
    save_json_atomic(READINESS_REPORT, payload)
    print(json.dumps(payload, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
