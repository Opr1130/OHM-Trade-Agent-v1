"""Build offline Phase 3C forward outcome labels from persisted market evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase3c_outcomes import build_forward_outcome_labels
from app.services.signal_quality_phase2 import DEFAULT_OBSERVATION_FILE, read_observations
from app.services.signal_quality_phase3c import read_jsonl


DEFAULT_SNAPSHOT_LEDGER = Path("/app/data/p1_evidence_ledger.jsonl")
DEFAULT_OUTPUT = Path("/app/data/phase3c_forward_outcomes.jsonl")


def build_outcomes(
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_LEDGER,
    observation_path: Path = DEFAULT_OBSERVATION_FILE,
    output_path: Path = DEFAULT_OUTPUT,
) -> list[dict]:
    snapshots = read_jsonl(snapshot_path)
    ingestion = read_observations(observation_path)
    labels = build_forward_outcome_labels(snapshots, ingestion.observations)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in labels:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OHM Phase 3C forward outcome labels")
    parser.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOT_LEDGER)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATION_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    labels = build_outcomes(
        snapshot_path=args.snapshots,
        observation_path=args.observations,
        output_path=args.output,
    )
    complete = sum(1 for row in labels if row.get("window_complete"))
    signal_episodes = {
        row.get("signal_episode_id") for row in labels if row.get("signal_episode_id")
    }
    print(
        json.dumps(
            {
                "labels": len(labels),
                "complete_24h_windows": complete,
                "signal_episodes": len(signal_episodes),
                "source": "PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS",
                "output": str(args.output),
                "trade_authority_changed": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
