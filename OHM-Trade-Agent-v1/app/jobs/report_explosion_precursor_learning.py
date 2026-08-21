from __future__ import annotations

from collections import defaultdict

from app.services.explosion_learning import OUTCOME_FILE
from app.services.explosion_precursor_learning import precursor_outcome_rows, read_precursor_observations


def _pct(count: int, total: int) -> str:
    return "n/a" if total <= 0 else f"{count / total * 100.0:.1f}%"


def main() -> None:
    observations = read_precursor_observations()
    outcomes = precursor_outcome_rows(outcome_file=OUTCOME_FILE)

    print("===== OHM WAVE 5.1 LATENT PRECURSOR LEARNING =====")
    print("Precursor observations:", len(observations))
    print("Precursor watches:", sum(1 for row in observations if row.get("watch") is True))
    print("Prospectively joined outcomes:", len(outcomes))

    grouped = defaultdict(list)
    for row in outcomes:
        phase = str(row.get("phase") or "UNKNOWN")
        if phase not in {"DORMANT", "IGNITION"}:
            continue
        label = "WATCH" if row.get("precursor_watch") is True else "NO_WATCH"
        horizon = str(row.get("horizon") or "unknown")
        grouped[(label, horizon)].append(row)

    for label in ("WATCH", "NO_WATCH"):
        for horizon in ("1h", "4h", "12h", "24h"):
            rows = grouped.get((label, horizon), [])
            if not rows:
                continue
            avg_mfe = sum(float(row.get("mfe_pct") or 0.0) for row in rows) / len(rows)
            avg_mae = sum(float(row.get("mae_pct") or 0.0) for row in rows) / len(rows)
            hit2 = sum(1 for row in rows if row.get("hit_2pct"))
            hit5 = sum(1 for row in rows if row.get("hit_5pct"))
            hit10 = sum(1 for row in rows if row.get("hit_10pct"))
            print(
                f"{label} {horizon}: samples={len(rows)} avg_MFE={avg_mfe:.2f}% avg_MAE={avg_mae:.2f}% "
                f"+2={_pct(hit2, len(rows))} +5={_pct(hit5, len(rows))} +10={_pct(hit10, len(rows))}"
            )

    print("Promotion status: NOT ELIGIBLE until prospective separation is demonstrated")
    print("Production phase thresholds changed: False")
    print("Trade authority changed: False")


if __name__ == "__main__":
    main()
