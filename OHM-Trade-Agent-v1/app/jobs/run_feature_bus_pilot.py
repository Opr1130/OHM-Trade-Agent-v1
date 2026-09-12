"""Manual-only PR3 feature-bus pilot. Never scheduled.

Ruling D4: PR3 measures what a one-minute market source would cost before any
activation decision. This entrypoint exists to produce that measurement and
nothing else.

Deliberate safety properties:

* It is not registered with the scheduler and no other module imports it.
* It performs no network request unless ``--live`` is passed explicitly.
* It writes canonical evidence only when both ``OPIP_FEATURE_BUS_MODE`` and
  ``OPIP_CANONICAL_WRITER_MODE`` are ``shadow``; otherwise every write is a
  recorded ``DISABLED`` no-op and the report says so.
* It never places orders, sends alerts, ranks candidates, or changes policy.

Usage::

    python -m app.jobs.run_feature_bus_pilot                 # synthetic dry run
    python -m app.jobs.run_feature_bus_pilot --live --limit 5 # measured pilot
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import random
from typing import Any

from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.observation import SourceWatermark
from app.opip.features.checkpoint_store import load_rolling_state
from app.opip.features.parity import compare_against_production_indicators
from app.opip.features.pipeline import CycleIdentityMismatch, run_cycle
from app.opip.features.publisher import (
    FeatureBusPublisher,
    feature_bus_capture_enabled,
    resolve_feature_bus_mode,
)
from app.opip.features.revision_ledger import load_revision_ledger
from app.opip.market.aggregates import DEFAULT_INTERVAL_SECONDS, grid_floor
from app.opip.market.instrument_version_store import hydrate_instrument_version_registry
from app.opip.market.observations import IntervalRow, normalize_interval_rows
from app.opip.market.source import run_pilot_cycle
from app.services.opip_feature_bus_market_source import (
    KRAKEN_OHLC_SOURCE_LABEL,
    KrakenInstrumentProvider,
    kraken_minute_source,
)

SYNTHETIC_SOURCE_LABEL = "synthetic_dry_run"


def restore_pilot_continuity(
    versions: list[InstrumentVersion],
    *,
    load_state=load_rolling_state,
    load_ledger=load_revision_ledger,
    default_interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Restore state, revision ledgers, and source watermarks for a live pilot.

    Revision ledgers load even when no checkpoint exists so observation
    revisions committed before a dependent-write failure survive process restart.
    """
    restored_states: dict[str, Any] = {}
    restored_ledgers: dict[str, Any] = {}
    source_watermarks: dict[str, Any] = {}
    for version in versions:
        state = load_state(version.instrument_version_id)
        since_epoch = None
        interval_seconds = int(default_interval_seconds)
        if state is not None:
            restored_states[version.instrument_version_id] = state
            since_epoch = state.first_interval_epoch
            interval_seconds = int(state.interval_seconds)
            if state.last_interval_epoch is not None:
                tip_end = datetime.fromtimestamp(
                    state.last_interval_epoch + state.interval_seconds,
                    tz=timezone.utc,
                )
                source_watermarks[version.instrument_version_id] = SourceWatermark(
                    instrument_version_id=version.instrument_version_id,
                    through_utc=tip_end,
                )
        restored_ledgers[version.instrument_version_id] = load_ledger(
            version.instrument_version_id,
            interval_seconds=interval_seconds,
            since_interval_epoch=since_epoch,
        )
    return restored_states, restored_ledgers, source_watermarks


def _synthetic_instrument(now: datetime) -> InstrumentVersion:
    # Distinct venue/identity so a mis-enabled publisher cannot collide with
    # real Kraken SOL/USD observation identity.
    return InstrumentVersion(
        venue="synthetic",
        base_asset="SOL",
        quote_currency="USD",
        venue_instrument_id="SYNTHETIC-SOLUSD",
        version=1,
        reference_data_version="opip-evidence-identity-v1",
        observed_at_utc=now,
        price_decimals=2,
        tick_size=0.01,
        min_order_size=0.2,
    )


def _synthetic_observations(
    instrument_version: InstrumentVersion,
    *,
    cutoff: datetime,
    intervals: int,
    now: datetime,
) -> Any:
    random.seed(11)
    first = cutoff - timedelta(minutes=intervals)
    epoch = int(first.timestamp())
    price = 147.0
    rows: list[IntervalRow] = []
    for index in range(intervals):
        price *= 1.0 + random.uniform(-0.0015, 0.0018)
        rows.append(
            IntervalRow(
                interval_start_epoch=epoch + 60 * index,
                open=price,
                high=price * 1.001,
                low=price * 0.999,
                close=price,
                volume=100.0 + random.random() * 50.0,
                vwap=price,
                trade_count=20 + index % 7,
            )
        )
    return normalize_interval_rows(
        rows,
        instrument_version=instrument_version,
        interval_seconds=60,
        receipt_time=now,
        now=now,
        source_label=SYNTHETIC_SOURCE_LABEL,
        source_sequence_prefix="synthetic-1m",
    ).observations


def _dry_run(intervals: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = grid_floor(now)
    instrument_version = _synthetic_instrument(now)
    observations = _synthetic_observations(
        instrument_version, cutoff=cutoff, intervals=intervals, now=now
    )
    # Capture must stay disabled on the synthetic path even if shadow gates are
    # enabled — fabricated candles must never enter the canonical WAL.
    publisher = FeatureBusPublisher(enabled=False)
    result = run_cycle(
        observations,
        instrument_version=instrument_version,
        evaluation_cutoff=cutoff,
        evaluated_at_utc=now,
        publisher=publisher,
        source_version=SYNTHETIC_SOURCE_LABEL,
    )
    parity = compare_against_production_indicators(
        result.alignment,
        instrument_version=instrument_version,
        evaluated_at_utc=now,
    )
    return {
        "mode": "dry_run",
        "cycle": result.to_dict(),
        "publisher": publisher.summary(),
        "parity": parity.to_dict(),
    }


def _live_pilot(limit: int) -> dict[str, Any]:
    publisher = FeatureBusPublisher()
    registry = hydrate_instrument_version_registry()
    provider = KrakenInstrumentProvider(registry=registry)
    refresh_at = datetime.now(timezone.utc)
    universe = provider.refresh(observed_at_utc=refresh_at)
    selected = universe[:limit]

    instrument_version_outcomes: list[dict[str, Any]] = []
    committed_versions: list[InstrumentVersion] = []
    for version in selected:
        outcome = publisher.publish_instrument_version(version)
        instrument_version_outcomes.append(
            {
                "instrument_version_id": version.instrument_version_id,
                "status": outcome.status,
                "committed": outcome.committed,
            }
        )
        # Capture-off (DISABLED) is a deliberate dry path; only fail closed when
        # capture is enabled and the newly required version did not commit.
        if publisher.enabled and not outcome.committed:
            continue
        committed_versions.append(version)

    source = kraken_minute_source()
    # Fixed cycle cutoff at pilot start — never advances mid-cycle merely because
    # sequential fetches crossed a minute boundary.
    cycle_cutoff = grid_floor(refresh_at)
    restored_states, restored_ledgers, source_watermarks = restore_pilot_continuity(
        committed_versions
    )

    batches, report, _ = run_pilot_cycle(
        source,
        committed_versions,
        now=cycle_cutoff,
        eligible_instruments=len(universe),
        watermarks=source_watermarks or None,
    )
    # Evaluation time is after network receipt so availability cannot outrun it.
    evaluated_at = report.finished_at_utc
    if evaluated_at < cycle_cutoff:
        evaluated_at = cycle_cutoff
    cycles: list[dict[str, Any]] = []
    for batch in batches:
        prior = restored_states.get(batch.instrument_version.instrument_version_id)
        ledger = restored_ledgers.get(batch.instrument_version.instrument_version_id)
        # Resumed coverage bounds THIS cycle's tip re-poll window only.
        # Widening to the oldest retained bar invents false gaps for intervals
        # that were never supposed to be re-fetched this cycle.
        window_start = None
        if prior is not None and prior.last_interval_epoch is not None:
            window_start = datetime.fromtimestamp(
                prior.last_interval_epoch, tz=timezone.utc
            )
        # Always evaluate the expected window — empty/transport failures still
        # produce incomplete coverage evidence rather than silent skips.
        try:
            result = run_cycle(
                batch.observations,
                instrument_version=batch.instrument_version,
                evaluation_cutoff=cycle_cutoff,
                evaluated_at_utc=evaluated_at,
                state=prior,
                revision_ledger=ledger,
                publisher=publisher,
                source_version=KRAKEN_OHLC_SOURCE_LABEL,
                window_start=window_start,
                source_coverage=batch.coverage,
            )
        except CycleIdentityMismatch as exc:
            cycles.append(
                {
                    "instrument_version_id": (
                        batch.instrument_version.instrument_version_id
                    ),
                    "disposition": "REJECTED_IDENTITY_MISMATCH",
                    "promoted": False,
                    "error": str(exc),
                }
            )
            continue
        payload = result.to_dict()
        if batch.error:
            payload["source_error"] = batch.error
        cycles.append(payload)
    return {
        "mode": "live_pilot",
        "measurement": report.to_dict(),
        "instrument_versions": instrument_version_outcomes,
        "cycles": cycles,
        "publisher": publisher.summary(),
        "restored_checkpoints": len(restored_states),
        "evaluation_cutoff": cycle_cutoff.isoformat(),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="contact Kraken for a measured pilot cycle (off by default)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="maximum instruments to request in a live pilot",
    )
    parser.add_argument(
        "--intervals",
        type=int,
        default=140,
        help="synthetic intervals to generate in a dry run",
    )
    args = parser.parse_args(argv)

    print("O'Pip PR3 Feature Bus Pilot - MANUAL, EVIDENCE ONLY")
    print("feature bus mode:", resolve_feature_bus_mode())
    print("canonical capture enabled:", feature_bus_capture_enabled())
    print("trading authority: NONE")

    payload = _live_pilot(max(1, args.limit)) if args.live else _dry_run(
        max(2, args.intervals)
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
