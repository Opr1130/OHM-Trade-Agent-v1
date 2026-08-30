"""One-time production activation gate for the Sequence 4 shadow worker."""
from __future__ import annotations

from datetime import datetime, timezone
import sys

from app.opip.events.contract import parse_utc
from app.opip.streaming.store import HEALTH_FILE, LATEST_FEATURES_FILE
from app.services.registry_io import RegistryIOError, load_json


MAX_HEALTH_AGE_SECONDS = 20.0
MAX_ACTIVATION_DROP_PCT = 1.0
_REQUIRED_PROVIDERS = {"BINANCE", "BYBIT"}


def main() -> int:
    try:
        health = load_json(HEALTH_FILE)
        latest = load_json(LATEST_FEATURES_FILE)
        updated = parse_utc(
            health.get("updated_at_utc"),
            field_name="updated_at_utc",
        )
    except (OSError, TimeoutError, RegistryIOError, ValueError):
        return 1

    if updated is None:
        return 1
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    if age < 0 or age > MAX_HEALTH_AGE_SECONDS:
        return 1
    if bool(health.get("runtime_failed")):
        return 1

    providers = health.get("provider_states")
    if not isinstance(providers, dict):
        return 1
    if set(providers) != _REQUIRED_PROVIDERS:
        return 1
    if any(str(state) != "CONNECTED" for state in providers.values()):
        return 1

    try:
        received = int(health.get("raw_frames_received") or 0)
        drop_pct = float(health.get("raw_drop_pct") or 0.0)
        store_errors = int(health.get("store_errors") or 0)
        observation_sink_errors = int(
            health.get("observation_sink_errors") or 0
        )
        window_sink_errors = int(health.get("window_sink_errors") or 0)
        bucket_drops = int(health.get("feature_buckets_dropped") or 0)
        snapshot_drops = int(health.get("feature_snapshots_dropped") or 0)
        features_persisted = int(health.get("features_persisted") or 0)
    except (TypeError, ValueError):
        return 1

    if received <= 0 or not 0.0 <= drop_pct <= MAX_ACTIVATION_DROP_PCT:
        return 1
    if any(
        value != 0
        for value in (
            store_errors,
            observation_sink_errors,
            window_sink_errors,
            bucket_drops,
            snapshot_drops,
        )
    ):
        return 1
    if features_persisted <= 0:
        return 1

    assets = latest.get("assets")
    if not isinstance(assets, dict) or not assets:
        return 1
    for row in assets.values():
        if not isinstance(row, dict):
            return 1
        if bool(row.get("liquidation_confirmable")):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
