"""Run bounded forward-outcome maturation plus opportunity accountability.

This job executes only on the isolated learning worker. Both stages are
measurement-only and have no network access or trading authority.
"""

from __future__ import annotations

import json

from app.jobs.build_phase3c_forward_outcomes import build_outcomes_bounded
from app.services.opportunity_accountability import build_incremental_from_outcomes


def main() -> None:
    outcomes = build_outcomes_bounded()
    summary = build_incremental_from_outcomes(outcomes)
    print(
        json.dumps(
            {
                "status": "OK",
                "outcomes_evaluated": len(outcomes),
                "population": summary.get("population", {}),
                "opportunity_capture_rate_pct": summary.get(
                    "opportunity_capture_rate_pct"
                ),
                "measurement_only": True,
                "trade_authority_changed": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
