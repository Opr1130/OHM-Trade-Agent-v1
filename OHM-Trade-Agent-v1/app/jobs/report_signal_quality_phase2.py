"""Print the Signal Quality Phase 2 replay report as JSON.

Read-only and offline by default. OHLC peak comparison is opt-in via --ohlc
because it performs public network reads; even with OHLC, timing/class metrics
remain event-sampled and the report stays PROVISIONAL_EVENT_SAMPLED_REPLAY.
Normal replay never writes a file; only explicit --write-ohlc-cache does so.
External review orchestration does not alter replay or trading behaviour, and
external model feedback remains advisory until independently validated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.signal_quality_phase2 import (
    DEFAULT_OBSERVATION_FILE,
    CachedOhlcProvider,
    KrakenPublicOhlcProvider,
    Phase2Config,
    build_all_episodes,
    build_timelines,
    read_observations,
    run_phase2_replay,
    write_ohlc_cache,
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
        help="Compare episode peak magnitude against public Kraken OHLC (network reads)",
    )
    parser.add_argument(
        "--ohlc-cache",
        type=Path,
        default=None,
        help=(
            "Compare peak magnitude against a local OHLC cache instead of the network. "
            "Timing/classes remain event-sampled. Build one with --write-ohlc-cache."
        ),
    )
    parser.add_argument(
        "--write-ohlc-cache",
        type=Path,
        default=None,
        help=(
            "Fetch public OHLC for each detected episode once and write it to this "
            "path, then exit. The only mode that writes a file."
        ),
    )
    args = parser.parse_args()

    config = Phase2Config(
        horizon_hours=args.horizon_hours,
        calibration_fraction=args.calibration_fraction,
    )

    if args.write_ohlc_cache is not None:
        ingestion = read_observations(args.observations)
        episodes = build_all_episodes(
            build_timelines(ingestion.observations), config=config.episodes
        )
        written = write_ohlc_cache(episodes, KrakenPublicOhlcProvider(), args.write_ohlc_cache)
        print(json.dumps({
            "wrote_candles": written,
            "episodes": len(episodes),
            "cache_path": str(args.write_ohlc_cache),
        }, indent=2))
        return

    provider = None
    if args.ohlc_cache is not None:
        provider = CachedOhlcProvider(args.ohlc_cache)
    elif args.ohlc:
        provider = KrakenPublicOhlcProvider()

    report = run_phase2_replay(
        observation_file=args.observations,
        config=config,
        ohlc_provider=provider,
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
