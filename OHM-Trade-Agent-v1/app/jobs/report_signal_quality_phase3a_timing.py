"""Print the Phase 3A signal timing replay report as JSON.

Read-only and offline. Mirrors report_signal_quality_phase2.py: reads one
observation file, writes nothing, and prints a JSON report. The
current-production stage timing/opportunity-decay blocks and the
persistence_counterfactual block are kept strictly separate in the output -
the counterfactual sweep never overwrites or is merged into the
production-configuration figures. No configuration is selected or applied.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.signal_quality_phase2 import DEFAULT_OBSERVATION_FILE, Phase2Config
from app.services.signal_timing_v2 import run_phase3a_timing_replay


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3A signal timing replay")
    parser.add_argument(
        "--observations",
        type=Path,
        default=DEFAULT_OBSERVATION_FILE,
        help="Path to full_market_observations.jsonl",
    )
    parser.add_argument(
        "--horizon-hours",
        type=float,
        default=Phase2Config().horizon_hours,
        help="Forward outcome horizon measured from each decision",
    )
    parser.add_argument(
        "--no-persistence-sweep",
        action="store_true",
        help="Skip the persistence counterfactual sweep (faster on large files)",
    )
    args = parser.parse_args()

    config = Phase2Config(horizon_hours=args.horizon_hours)
    report = run_phase3a_timing_replay(
        observation_file=args.observations,
        config=config,
        run_persistence_sweep=not args.no_persistence_sweep,
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
