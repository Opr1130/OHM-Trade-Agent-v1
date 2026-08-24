"""Print the Signal Quality Phase 2 replay report as JSON.

Read-only and offline. OHLC cross-validation is opt-in via --ohlc because it
performs public network reads; without it the report is explicitly labelled
PROVISIONAL_EVENT_SAMPLED_REPLAY.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.signal_quality_phase2 import (
    DEFAULT_OBSERVATION_FILE,
    KrakenPublicOhlcProvider,
    Phase2Config,
    run_phase2_replay,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal Quality Phase 2 historical replay")
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
        "--calibration-fraction",
        type=float,
        default=Phase2Config().calibration_fraction,
        help="Chronological share of the timeline used for calibration",
    )
    parser.add_argument(
        "--ohlc",
        action="store_true",
        help="Cross-validate episode peaks against public Kraken OHLC (network reads)",
    )
    args = parser.parse_args()

    config = Phase2Config(
        horizon_hours=args.horizon_hours,
        calibration_fraction=args.calibration_fraction,
    )
    report = run_phase2_replay(
        observation_file=args.observations,
        config=config,
        ohlc_provider=KrakenPublicOhlcProvider() if args.ohlc else None,
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
