from __future__ import annotations

import json
from collections import defaultdict

from app.services.explosion_learning import OUTCOME_FILE, SNAPSHOT_FILE, observe_due_explosion_outcomes


def _read(path, record_type: str):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("record_type") == record_type:
            rows.append(row)
    return rows


def _pct(count: int, total: int) -> str:
    return "n/a" if total <= 0 else f"{count / total * 100.0:.1f}%"


def main() -> None:
    observation = observe_due_explosion_outcomes()
    snapshots = _read(SNAPSHOT_FILE, "EXPLOSION_STATE")
    outcomes = _read(OUTCOME_FILE, "EXPLOSION_OUTCOME")

    print("===== OHM WAVE 5 EXPLOSION LEARNING =====")
    print("Snapshots:", len(snapshots))
    print("Outcomes:", len(outcomes))
    print("New outcomes this run:", observation.get("outcomes_added", 0))
    print("Pending horizons:", observation.get("pending_horizons", 0))

    phase_counts = defaultdict(int)
    for row in snapshots:
        phase_counts[str(row.get("phase") or "UNKNOWN")] += 1
    print("Phase observations:", dict(sorted(phase_counts.items())))

    by_phase_horizon = defaultdict(list)
    for row in outcomes:
        by_phase_horizon[(str(row.get("phase") or "UNKNOWN"), str(row.get("horizon") or "unknown"))].append(row)

    for phase in (
        "IGNITION",
        "EARLY_EXPANSION",
        "CONFIRMED_EXPANSION",
        "LATE_EXTENSION",
        "EXHAUSTION_RISK",
    ):
        for horizon in ("1h", "4h", "12h", "24h"):
            rows = by_phase_horizon.get((phase, horizon), [])
            if not rows:
                continue
            avg_mfe = sum(float(row.get("mfe_pct") or 0.0) for row in rows) / len(rows)
            avg_mae = sum(float(row.get("mae_pct") or 0.0) for row in rows) / len(rows)
            hit2 = sum(1 for row in rows if row.get("hit_2pct"))
            hit5 = sum(1 for row in rows if row.get("hit_5pct"))
            print(
                f"{phase} {horizon}: samples={len(rows)} avg_MFE={avg_mfe:.2f}% "
                f"avg_MAE={avg_mae:.2f}% +2={_pct(hit2, len(rows))} +5={_pct(hit5, len(rows))}"
            )

    print("Learning status: SHADOW ONLY")
    print("Production thresholds changed: False")
    print("Scores are heuristic state descriptors, not calibrated probabilities.")


if __name__ == "__main__":
    main()
