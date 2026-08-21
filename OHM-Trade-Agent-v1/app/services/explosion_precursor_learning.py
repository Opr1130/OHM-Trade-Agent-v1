from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.explosion_precursor import ExplosionPrecursor
from app.services.registry_io import registry_lock


PRECURSOR_FILE = Path("/app/data/explosion_precursor_snapshots.jsonl")


def append_precursor_observation(
    precursor: ExplosionPrecursor,
    *,
    observation_id: str,
    observed_at: str,
    phase: str,
    phase_score: int,
    reference_price: float,
    source_cohort: str | None,
    path: Path | None = None,
) -> None:
    """Persist the challenger decision at the same decision time as Wave 5.

    The observation id is shared with the base explosion snapshot so later
    outcomes can be joined without re-querying or reconstructing history.
    """
    target = path or PRECURSOR_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "record_type": "EXPLOSION_PRECURSOR",
        "observation_id": observation_id,
        "observed_at": observed_at,
        "phase": phase,
        "phase_score": int(phase_score),
        "reference_price": float(reference_price),
        "source_cohort": source_cohort,
        **precursor.as_dict(),
    }
    lock = target.parent / f".{target.name}.lock"
    with registry_lock(lock):
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            handle.flush()


def read_precursor_observations(*, path: Path | None = None, limit: int = 50000) -> list[dict[str, Any]]:
    target = path or PRECURSOR_FILE
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("record_type") == "EXPLOSION_PRECURSOR":
                    rows.append(row)
    except OSError:
        return []
    return rows[-max(1, int(limit)):]


def precursor_outcome_rows(
    *,
    outcome_file: Path,
    precursor_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Join already-prospective Wave 5 outcomes to precursor decisions."""
    precursors = read_precursor_observations(path=precursor_file)
    by_id = {str(row.get("observation_id") or ""): row for row in precursors if row.get("observation_id")}
    if not outcome_file.exists() or not by_id:
        return []
    joined: list[dict[str, Any]] = []
    try:
        with outcome_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    outcome = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if outcome.get("record_type") != "EXPLOSION_OUTCOME":
                    continue
                precursor = by_id.get(str(outcome.get("observation_id") or ""))
                if precursor is None:
                    continue
                joined.append({**outcome, "precursor_score": precursor.get("score"), "precursor_watch": precursor.get("watch"), "precursor_evidence_count": precursor.get("evidence_count")})
    except OSError:
        return []
    return joined
