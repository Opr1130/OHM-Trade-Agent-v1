"""Asynchronously mature Phase 3C forward outcomes from persisted evidence.

The output is an append-only outcome-maturation ledger. Re-running the job is
idempotent for unchanged labels; partial rows may receive later immutable
revisions until the 24h window is complete. The job performs no market scan,
no Telegram action, and no trading-state mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.services.phase3c_outcomes import build_forward_outcome_labels
from app.services.registry_io import registry_lock
from app.services.signal_quality_phase2 import DEFAULT_OBSERVATION_FILE, read_observations
from app.services.signal_quality_phase3c import read_jsonl


DEFAULT_SNAPSHOT_LEDGER = Path("/app/data/p1_evidence_ledger.jsonl")
DEFAULT_OUTPUT = Path("/app/data/phase3c_forward_outcomes.jsonl")


def _canonical_label_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"outcome_record_id", "outcome_revision"}
    }


def _outcome_record_id(row: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_label_payload(row),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "OUT:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _latest_by_snapshot(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        snapshot_id = str(row.get("snapshot_id", "") or "")
        if snapshot_id:
            latest[snapshot_id] = row
    return latest


def build_outcomes(
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_LEDGER,
    observation_path: Path = DEFAULT_OBSERVATION_FILE,
    output_path: Path = DEFAULT_OUTPUT,
) -> list[dict]:
    """Compute current labels and append only new immutable maturation states."""
    snapshots = read_jsonl(snapshot_path)
    ingestion = read_observations(observation_path)
    labels = build_forward_outcome_labels(snapshots, ingestion.observations)

    existing = read_jsonl(output_path)
    latest = _latest_by_snapshot(existing)
    revisions: dict[str, int] = {}
    for row in existing:
        snapshot_id = str(row.get("snapshot_id", "") or "")
        if not snapshot_id:
            continue
        revisions[snapshot_id] = max(
            revisions.get(snapshot_id, 0),
            int(row.get("outcome_revision", 0) or 0),
        )

    pending: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for label in labels:
        snapshot_id = str(label.get("snapshot_id", "") or "")
        if not snapshot_id:
            continue
        record_id = _outcome_record_id(label)
        prior = latest.get(snapshot_id)
        if prior and str(prior.get("outcome_record_id", "") or "") == record_id:
            current.append(prior)
            continue

        revision = revisions.get(snapshot_id, 0) + 1
        row = {
            **label,
            "outcome_record_type": "FORWARD_OUTCOME_MATURATION",
            "outcome_record_id": record_id,
            "outcome_revision": revision,
            "append_only": True,
        }
        pending.append(row)
        current.append(row)

    if pending:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lock = output_path.parent / f".{output_path.name}.lock"
        with registry_lock(lock):
            with output_path.open("a", encoding="utf-8") as handle:
                for row in pending:
                    handle.write(
                        json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
                    )
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass

    return sorted(
        current,
        key=lambda row: (
            str(row.get("reference_at", "")),
            str(row.get("symbol", "")),
            str(row.get("snapshot_id", "")),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mature OHM Phase 3C forward outcome labels"
    )
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
    canonical_episodes = {
        row.get("canonical_episode_id")
        for row in labels
        if row.get("canonical_episode_id")
    }
    print(
        json.dumps(
            {
                "labels": len(labels),
                "complete_24h_windows": complete,
                "canonical_episodes": len(canonical_episodes),
                "source": "PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS",
                "output": str(args.output),
                "append_only": True,
                "trade_authority_changed": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
