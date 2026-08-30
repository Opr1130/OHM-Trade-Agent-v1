"""Production shadow entrypoint for O'Pip Sequence 4 BUILD 4.5."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import logging
import signal
import sys
import time

from app.opip.streaming.binance import BinancePublicAdapter
from app.opip.streaming.bybit import BybitPublicAdapter
from app.opip.streaming.config import StreamingWorkerSettings
from app.opip.streaming.contract import StreamProvider
from app.opip.streaming.feature_accumulator import CrossVenueFeatureAccumulator
from app.opip.streaming.instruments import initial_symbols
from app.opip.streaming.runtime import StreamingRuntime, StreamingRuntimeConfig
from app.opip.streaming.store import StreamingShadowStore


logger = logging.getLogger("opip.streaming.worker")
_MAX_CONSECUTIVE_STORE_ERRORS = 5
_PRUNE_INTERVAL_SECONDS = 300.0


def _parse_symbols(raw: str) -> tuple[str, ...]:
    symbols = tuple(
        token.strip().upper()
        for token in str(raw or "").split(",")
        if token.strip()
    )
    if not symbols or len(symbols) > 5:
        raise ValueError("OPIP streaming symbols must contain 1..5 values")
    allowed = set(initial_symbols(StreamProvider.BINANCE)) & set(
        initial_symbols(StreamProvider.BYBIT)
    )
    unknown = sorted(set(symbols) - allowed)
    if unknown:
        raise ValueError(
            "production shadow symbols require explicit reviewed identity bindings: "
            + ",".join(unknown)
        )
    return symbols


def _health_payload(
    *,
    runtime_snapshot,
    accumulator: CrossVenueFeatureAccumulator,
    features_persisted: int,
    store_errors: int,
    last_feature_snapshot_utc: str | None,
) -> dict:
    received = runtime_snapshot.raw_frames_received
    dropped = runtime_snapshot.raw_frames_dropped_newest
    drop_pct = round(100.0 * dropped / received, 6) if received else 0.0
    provider_states = {
        item.provider: item.transport_state
        for item in runtime_snapshot.providers
    }
    degraded = (
        runtime_snapshot.resource_degraded
        or runtime_snapshot.runtime_failed
        or dropped > 0
        or runtime_snapshot.observation_sink_errors > 0
        or runtime_snapshot.window_sink_errors > 0
        or accumulator.dropped_buckets > 0
        or accumulator.dropped_ready_snapshots > 0
        or any(state != "CONNECTED" for state in provider_states.values())
    )
    return {
        "schema_version": 1,
        "status": "DEGRADED" if degraded else "RUNNING",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_failed": runtime_snapshot.runtime_failed,
        "fatal_error_type": runtime_snapshot.fatal_error_type,
        "provider_states": provider_states,
        "queue": asdict(runtime_snapshot.queue),
        "raw_frames_received": received,
        "raw_frames_dropped_newest": dropped,
        "raw_drop_pct": drop_pct,
        "sequence_gaps": runtime_snapshot.sequence_gaps,
        "sequence_out_of_order": runtime_snapshot.sequence_out_of_order,
        "late_frames": runtime_snapshot.late_frames,
        "degraded_windows": runtime_snapshot.degraded_windows,
        "incomplete_windows": runtime_snapshot.incomplete_windows,
        "observation_sink_errors": runtime_snapshot.observation_sink_errors,
        "window_sink_errors": runtime_snapshot.window_sink_errors,
        "resource_degraded": runtime_snapshot.resource_degraded,
        "resource_reasons": list(runtime_snapshot.resource_reasons),
        "rss_bytes": runtime_snapshot.rss_bytes,
        "cpu_fraction": runtime_snapshot.cpu_fraction,
        "event_loop_lag_seconds": runtime_snapshot.event_loop_lag_seconds,
        "feature_buckets_dropped": accumulator.dropped_buckets,
        "feature_snapshots_dropped": accumulator.dropped_ready_snapshots,
        "invalid_identity_observations": accumulator.invalid_identity_observations,
        "features_persisted": int(features_persisted),
        "last_feature_snapshot_utc": last_feature_snapshot_utc,
        "store_errors": int(store_errors),
        "authoritative": False,
        "can_trade": False,
        "can_change_policy": False,
    }


async def run_worker() -> int:
    settings = StreamingWorkerSettings()
    store = StreamingShadowStore(
        retention_hours=settings.retention_hours
    )
    if not settings.enabled:
        store.write_health(
            {
                "schema_version": 1,
                "status": "DISABLED",
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "runtime_failed": False,
                "authoritative": False,
                "can_trade": False,
            }
        )
        return 0

    symbols = _parse_symbols(settings.symbols)
    accumulator = CrossVenueFeatureAccumulator()
    runtime = StreamingRuntime(
        {
            StreamProvider.BINANCE: BinancePublicAdapter(
                url=settings.binance_url,
                symbols=symbols,
            ),
            StreamProvider.BYBIT: BybitPublicAdapter(
                url=settings.bybit_url,
                symbols=symbols,
            ),
        },
        config=StreamingRuntimeConfig(
            queue_maxsize=settings.queue_maxsize,
            max_symbols=len(symbols),
        ),
        observation_sink=accumulator.record,
        sealed_window_sink=accumulator.seal,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    features_persisted = 0
    store_errors = 0
    consecutive_store_errors = 0
    last_feature_snapshot_utc: str | None = None
    last_prune = time.monotonic()

    await runtime.start()
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.health_interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                pass

            if not runtime.running:
                snapshot = runtime.snapshot()
                store.write_health(
                    {
                        **_health_payload(
                            runtime_snapshot=snapshot,
                            accumulator=accumulator,
                            features_persisted=features_persisted,
                            store_errors=store_errors,
                            last_feature_snapshot_utc=last_feature_snapshot_utc,
                        ),
                        "status": "FAILED",
                    }
                )
                return 1

            snapshot = runtime.snapshot()
            rows = accumulator.drain_ready()
            try:
                if rows:
                    features_persisted += store.append_features(rows)
                    last_feature_snapshot_utc = max(
                        row.window_end_utc for row in rows
                    ).isoformat()
                store.write_telemetry(snapshot)
                now_monotonic = time.monotonic()
                if now_monotonic - last_prune >= _PRUNE_INTERVAL_SECONDS:
                    store.prune(now_utc=datetime.now(timezone.utc))
                    last_prune = now_monotonic
                store.write_health(
                    _health_payload(
                        runtime_snapshot=snapshot,
                        accumulator=accumulator,
                        features_persisted=features_persisted,
                        store_errors=store_errors,
                        last_feature_snapshot_utc=last_feature_snapshot_utc,
                    )
                )
                consecutive_store_errors = 0
            except Exception:
                store_errors += 1
                consecutive_store_errors += 1
                logger.exception("O'Pip streaming shadow persistence failed")
                if consecutive_store_errors >= _MAX_CONSECUTIVE_STORE_ERRORS:
                    raise RuntimeError(
                        "streaming persistence failed repeatedly"
                    )
    finally:
        await runtime.stop()

    final_snapshot = runtime.snapshot()
    try:
        remaining = accumulator.drain_ready()
        if remaining:
            features_persisted += store.append_features(remaining)
        store.write_telemetry(final_snapshot)
        store.write_health(
            {
                **_health_payload(
                    runtime_snapshot=final_snapshot,
                    accumulator=accumulator,
                    features_persisted=features_persisted,
                    store_errors=store_errors,
                    last_feature_snapshot_utc=last_feature_snapshot_utc,
                ),
                "status": "STOPPED",
            }
        )
    except Exception:
        logger.exception("final streaming shadow persistence failed")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(run_worker())
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("O'Pip streaming worker terminated")
        return 1


if __name__ == "__main__":
    sys.exit(main())
