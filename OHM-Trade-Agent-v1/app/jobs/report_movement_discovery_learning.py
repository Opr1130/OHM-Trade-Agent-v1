from __future__ import annotations

import json
from collections import defaultdict

from app.services.movement_discovery_outcomes import OUTCOME_FILE


def _pct(count: int, total: int) -> str:
    return "n/a" if total <= 0 else f"{count / total * 100.0:.1f}%"


def main() -> None:
    rows = []
    if OUTCOME_FILE.exists():
        for line in OUTCOME_FILE.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("record_type") == "OUTCOME":
                rows.append(row)

    print("===== MOVEMENT DISCOVERY V2.1 LEARNING =====")
    print("Outcome samples:", len(rows))
    by_horizon = defaultdict(list)
    for row in rows:
        by_horizon[str(row.get("horizon") or "unknown")].append(row)
    for horizon in ("1h", "4h", "12h", "24h"):
        bucket = by_horizon.get(horizon, [])
        if not bucket:
            print(f"{horizon}: samples=0")
            continue
        avg_mfe = sum(float(row.get("mfe_pct") or 0.0) for row in bucket) / len(bucket)
        avg_mae = sum(float(row.get("mae_pct") or 0.0) for row in bucket) / len(bucket)
        hit5 = sum(1 for row in bucket if row.get("hit_5pct"))
        hit10 = sum(1 for row in bucket if row.get("hit_10pct"))
        hit20 = sum(1 for row in bucket if row.get("hit_20pct"))
        print(
            f"{horizon}: samples={len(bucket)} avg_MFE={avg_mfe:.2f}% avg_MAE={avg_mae:.2f}% "
            f"+5={_pct(hit5, len(bucket))} +10={_pct(hit10, len(bucket))} +20={_pct(hit20, len(bucket))}"
        )

    confidence_buckets = defaultdict(list)
    for row in rows:
        try:
            confidence = int(row.get("continuation_confidence"))
        except (TypeError, ValueError):
            continue
        confidence_buckets[(confidence // 5) * 5].append(row)
    print("Continuation buckets (all horizons):")
    for bucket in sorted(confidence_buckets):
        group = confidence_buckets[bucket]
        hit5 = sum(1 for row in group if row.get("hit_5pct"))
        hit10 = sum(1 for row in group if row.get("hit_10pct"))
        print(
            f"  {bucket:02d}-{bucket + 4:02d}: samples={len(group)} "
            f"+5={_pct(hit5, len(group))} +10={_pct(hit10, len(group))}"
        )
    print("Scores remain heuristic ranks, not calibrated probabilities.")
    print("Production decisions changed: False")


if __name__ == "__main__":
    main()
