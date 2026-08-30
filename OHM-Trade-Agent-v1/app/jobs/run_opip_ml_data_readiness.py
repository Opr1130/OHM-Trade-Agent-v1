"""Independent read-only O'Pip ML data-readiness report job."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.opip.learning.linkage import read_jsonl, read_ml_snapshot_chunks
from app.opip.learning.paper_readiness import assess_paper_learning_readiness
from app.opip.learning.readiness import build_ml_data_readiness_report
from app.services.registry_io import load_json, save_json_atomic


CANONICAL_EVIDENCE = Path("/app/data/p1_evidence_ledger.jsonl")
ML_SNAPSHOT_DIR = Path("/app/data/opip_ml_feature_snapshots_v1")
PHASE3C_OUTCOMES = Path("/app/data/phase3c_forward_outcomes.jsonl")
PAPER_STATE = Path("/app/data/paper_trading/state.json")
CAPTURE_HEALTH = Path("/app/data/opip_ml_capture_health.json")
READINESS_REPORT = Path("/app/data/opip_ml_data_readiness_v1.json")


def _paper_rows(path: Path) -> list[dict[str, Any]]:
    """Load current paper lifecycle state without mutating paper control."""
    if not path.exists():
        return []
    payload = load_json(path)
    rows = payload.get("lifecycles", payload)
    if not isinstance(rows, dict):
        raise ValueError("paper state must contain lifecycle objects")
    return [dict(row) for row in rows.values() if isinstance(row, dict)]


def _capture_health(path: Path) -> dict[str, Any]:
    """Load evidence-capture health when available."""
    if not path.exists():
        return {}
    payload = load_json(path)
    return dict(payload) if isinstance(payload, dict) else {}


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
    report = build_ml_data_readiness_report(
        canonical_rows=read_jsonl(canonical_path),
        ml_snapshot_rows=read_ml_snapshot_chunks(snapshot_dir),
        phase3c_outcome_rows=read_jsonl(phase3c_path),
        paper_trade_rows=_paper_rows(paper_state_path),
        capture_health=_capture_health(capture_health_path),
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
