"""Build an offline Phase 3C evidence report from immutable P1 evidence.

Expected inputs are append-only research files. Forward outcomes are labels
computed offline (for example from Phase 3A's point-in-time outcome functions);
this job never derives live features from them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.registry_io import save_json_atomic
from app.services.signal_quality_phase3c import (
    build_phase3c_report,
    join_point_in_time_evidence,
    read_jsonl,
)


DEFAULT_SNAPSHOT_LEDGER = Path("/app/data/p1_evidence_ledger.jsonl")
DEFAULT_PHASE3B = Path("/app/data/phase3b_shadow_telemetry.jsonl")
DEFAULT_OUTCOMES = Path("/app/data/phase3c_forward_outcomes.jsonl")
DEFAULT_REPORT = Path("/app/data/phase3c_verified_edge_report.json")


def build_report(
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_LEDGER,
    phase3b_path: Path = DEFAULT_PHASE3B,
    outcomes_path: Path = DEFAULT_OUTCOMES,
    report_path: Path = DEFAULT_REPORT,
) -> dict:
    snapshots = read_jsonl(snapshot_path)
    phase3b = read_jsonl(phase3b_path)
    outcomes = read_jsonl(outcomes_path)
    rows = join_point_in_time_evidence(
        snapshots,
        phase3b_rows=phase3b,
        outcomes=outcomes,
    )
    report = build_phase3c_report(rows)
    save_json_atomic(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OHM Phase 3C offline evidence report")
    parser.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOT_LEDGER)
    parser.add_argument("--phase3b", type=Path, default=DEFAULT_PHASE3B)
    parser.add_argument("--outcomes", type=Path, default=DEFAULT_OUTCOMES)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    report = build_report(
        snapshot_path=args.snapshots,
        phase3b_path=args.phase3b,
        outcomes_path=args.outcomes,
        report_path=args.output,
    )
    print(json.dumps(
        {
            "status": report["status"],
            "episodes": report["episodes"],
            "gate0_ready": report["promotion_gate"]["gate0_ready"],
            "auto_promotion_allowed": report["auto_promotion_allowed"],
            "output": str(args.output),
        },
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
