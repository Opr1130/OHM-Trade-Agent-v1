"""Build the read-only O'Pip opportunity accountability ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.opportunity_accountability import (
    DEFAULT_FUNNEL_FILE,
    DEFAULT_INTELLIGENCE_EVENT_FILE,
    DEFAULT_LEDGER_FILE,
    DEFAULT_OUTCOME_FILE,
    DEFAULT_SCREENING_FILE,
    DEFAULT_SNAPSHOT_FILE,
    DEFAULT_SUMMARY_FILE,
    build_from_files,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build O'Pip missed-winner and opportunity accountability evidence"
    )
    parser.add_argument("--screening", type=Path, default=DEFAULT_SCREENING_FILE)
    parser.add_argument("--funnel", type=Path, default=DEFAULT_FUNNEL_FILE)
    parser.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOT_FILE)
    parser.add_argument("--outcomes", type=Path, default=DEFAULT_OUTCOME_FILE)
    parser.add_argument(
        "--intelligence-events",
        type=Path,
        default=DEFAULT_INTELLIGENCE_EVENT_FILE,
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_FILE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY_FILE)
    args = parser.parse_args()

    summary = build_from_files(
        screening_path=args.screening,
        funnel_path=args.funnel,
        snapshot_path=args.snapshots,
        outcome_path=args.outcomes,
        intelligence_event_path=args.intelligence_events,
        ledger_path=args.ledger,
        summary_path=args.summary,
    )
    print(
        json.dumps(
            {
                "status": "OK",
                "population": summary.get("population", {}),
                "opportunity_capture_rate_pct": summary.get(
                    "opportunity_capture_rate_pct"
                ),
                "ledger": str(args.ledger),
                "summary": str(args.summary),
                "measurement_only": True,
                "trade_authority_changed": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
